#!/usr/bin/env python3
"""
Phase 0 outcome settlement.

For each actionable, not-yet-settled signal in the DB, compute the realized
1/5/10 trading-day forward outcome from the ticker's parquet and upsert into
signal_outcomes. Idempotent: re-running only fills missing (signal, horizon)
pairs that now have enough forward data.

Usage:
  uv run python scripts/settle_outcomes.py
  uv run python scripts/settle_outcomes.py --as-of 2026-06-08
  uv run python scripts/settle_outcomes.py --refill-benchmark

Benchmark (TOPIX) columns are filled from macro_panel TOPIX when available;
rows without a matching TOPIX date keep NULL and are retried on the next run.
Exits 0 (no-op) when DB is disabled / unreachable.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.db_records import compute_benchmark_ret, compute_outcome  # noqa: E402
from src.data_loader import load_data  # noqa: E402
from scripts.curation_common import today_jst_iso  # noqa: E402


def _load_topix_by_date() -> dict[str, float]:
    macro_path = ROOT / "data" / "macro" / "macro_panel.parquet"
    try:
        df = pd.read_parquet(macro_path)
    except FileNotFoundError:
        print("macro_panel.parquet not found; TOPIX benchmark will stay NULL")
        return {}
    except Exception as exc:  # noqa: BLE001
        print(f"macro_panel.parquet read error (benchmark stays NULL): {exc}")
        return {}
    if "topix" not in df.columns:
        print("macro_panel.parquet has no topix column; benchmark stays NULL")
        return {}
    sub = df[["date", "topix"]].dropna(subset=["topix"])
    result = {d[:10]: float(v) for d, v in
              zip(pd.to_datetime(sub["date"]).dt.strftime("%Y-%m-%d"), sub["topix"])}
    if not result:
        print("macro_panel.parquet topix column is all-NaN; benchmark stays NULL")
    return result


def _settle_for_ticker(conn, ticker: str, signals: list[dict],
                       topix_by_date: dict) -> int:
    df = load_data(ticker)
    if df is None or df.empty or "date" not in df.columns:
        return 0
    df = df.sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    date_to_idx = {d: i for i, d in enumerate(df["date"].tolist())}

    settled = 0
    for sig in signals:
        as_of = str(sig["as_of_date"])
        idx = date_to_idx.get(as_of)
        if idx is None:
            continue  # as_of date not present in price history (e.g. failed signal)
        entry_close = float(df["close"].iloc[idx])
        for h in sig["missing_horizons"]:
            exit_idx = idx + h
            if exit_idx >= len(df):
                continue  # not enough forward data yet; settle on a later run
            exit_close = float(df["close"].iloc[exit_idx])
            eval_date = df["date"].iloc[exit_idx]
            path = df.iloc[idx + 1: exit_idx + 1]
            payload = compute_outcome(
                action=sig["action"], entry_close=entry_close, exit_close=exit_close,
                path_highs=path["high"].astype(float).tolist(),
                path_lows=path["low"].astype(float).tolist(),
            )
            benchmark_ret = compute_benchmark_ret(topix_by_date, as_of, eval_date)
            excess_ret = (payload["realized_ret"] - benchmark_ret
                          if benchmark_ret is not None else None)
            db.upsert_outcome(conn, sig["signal_id"], h, {
                "entry_date": as_of,
                "eval_date": eval_date,
                "entry_close": entry_close,
                "exit_close": exit_close,
                "realized_ret": payload["realized_ret"],
                "benchmark_ret": benchmark_ret,
                "excess_ret": excess_ret,
                "hit": payload["hit"],
                "mae": payload["mae"],
                "mfe": payload["mfe"],
                "exit_reason": payload["exit_reason"],
            })
            settled += 1
    return settled


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=today_jst_iso(),
                        help="JST date label (informational; settlement scans all unsettled).")
    parser.add_argument("--refill-benchmark", action="store_true",
                        help="Backfill benchmark_ret/excess_ret for already-settled rows.")
    args = parser.parse_args()

    if not db.db_enabled():
        print("DB disabled or DATABASE_URL unset; skipping settlement.")
        return 0

    topix_by_date = _load_topix_by_date()

    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not connect for settlement (ignored): {type(exc).__name__}: {exc}")
        return 0

    try:
        unsettled = db.fetch_unsettled(conn)
        by_ticker: dict[str, list[dict]] = {}
        for row in unsettled:
            by_ticker.setdefault(row["ticker"], []).append(row)

        total = 0
        for ticker, sigs in by_ticker.items():
            total += _settle_for_ticker(conn, ticker, sigs, topix_by_date)
        print(f"Settlement as-of {args.as_of}: filled {total} outcome rows "
              f"across {len(by_ticker)} tickers ({len(unsettled)} unsettled signals scanned).")

        if args.refill_benchmark and not topix_by_date:
            print("Refill benchmark: no TOPIX data available; skipping.")
        elif args.refill_benchmark:
            missing = db.fetch_outcomes_missing_benchmark(conn)
            refilled = 0
            for row in missing:
                benchmark_ret = compute_benchmark_ret(
                    topix_by_date, row["entry_date"], row["eval_date"]
                )
                if benchmark_ret is None:
                    continue
                excess_ret = row["realized_ret"] - benchmark_ret
                db.update_outcome_benchmark(
                    conn, row["signal_id"], row["horizon_days"],
                    benchmark_ret, excess_ret
                )
                refilled += 1
            print(f"Refill benchmark: updated {refilled}/{len(missing)} rows.")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
