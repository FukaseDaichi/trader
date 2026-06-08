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

Benchmark (TOPIX) columns are left NULL in Phase 0 (added in Phase 1).
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
from src.db_records import compute_outcome  # noqa: E402
from src.data_loader import load_data  # noqa: E402
from scripts.curation_common import today_jst_iso  # noqa: E402


def _settle_for_ticker(conn, ticker: str, signals: list[dict]) -> int:
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
            path = df.iloc[idx + 1: exit_idx + 1]
            payload = compute_outcome(
                action=sig["action"], entry_close=entry_close, exit_close=exit_close,
                path_highs=path["high"].astype(float).tolist(),
                path_lows=path["low"].astype(float).tolist(),
            )
            db.upsert_outcome(conn, sig["signal_id"], h, {
                "entry_date": as_of,
                "eval_date": df["date"].iloc[exit_idx],
                "entry_close": entry_close,
                "exit_close": exit_close,
                "realized_ret": payload["realized_ret"],
                "benchmark_ret": None,
                "excess_ret": None,
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
    args = parser.parse_args()

    if not db.db_enabled():
        print("DB disabled or DATABASE_URL unset; skipping settlement.")
        return 0

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
            total += _settle_for_ticker(conn, ticker, sigs)
        print(f"Settlement as-of {args.as_of}: filled {total} outcome rows "
              f"across {len(by_ticker)} tickers ({len(unsettled)} unsettled signals scanned).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
