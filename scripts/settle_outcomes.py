#!/usr/bin/env python3
"""
Phase 0 outcome settlement.

For each actionable, not-yet-settled signal in the DB, compute the executable
1/5/10-session forward outcome from the ticker's parquet and upsert into
signal_outcomes. Contract v2 enters at the first session after ``as_of_date``
at its open, then exits at the H-th session close. Idempotent: re-running only
fills missing (signal, horizon) pairs that now have enough forward data.

Usage:
  uv run python scripts/settle_outcomes.py
  uv run python scripts/settle_outcomes.py --as-of 2026-06-08
  uv run python scripts/settle_outcomes.py --refill-benchmark
  uv run python scripts/settle_outcomes.py --restate-execution-contract

The macro panel has TOPIX-proxy closes but no next-session open.  Contract v2
therefore leaves benchmark_ret NULL instead of mixing a close-to-close proxy
with the stock's open-to-close return. Legacy v1 rows can still be refilled.
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
from src.db_records import (  # noqa: E402
    OUTCOME_HORIZONS,
    compute_benchmark_ret,
    compute_outcome,
)
from src.data_loader import load_archived_data, load_data  # noqa: E402
from src.execution import (  # noqa: E402
    BENCHMARK_BASIS,
    ENTRY_PRICE_BASIS,
    EXECUTION_CONTRACT_VERSION,
    EXIT_PRICE_BASIS,
    LEGACY_EXECUTION_CONTRACT_VERSION,
    resolve_execution_window,
)
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
    result = {
        d[:10]: float(v)
        for d, v in zip(
            pd.to_datetime(sub["date"]).dt.strftime("%Y-%m-%d"), sub["topix"]
        )
    }
    if not result:
        print("macro_panel.parquet topix column is all-NaN; benchmark stays NULL")
    return result


def _settle_for_ticker(
    conn, ticker: str, signals: list[dict], _topix_by_date: dict
) -> int:
    df = load_data(ticker)
    if df is None:
        df = load_archived_data(ticker)
    if df is None or df.empty or "date" not in df.columns:
        return 0
    df = df.sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    date_to_idx = {d: i for i, d in enumerate(df["date"].tolist())}

    settled = 0
    for sig in signals:
        as_of = str(sig["as_of_date"])
        market_as_of_idx = date_to_idx.get(as_of)
        if market_as_of_idx is None:
            continue  # as_of date not present in price history (e.g. failed signal)
        for h in sig["missing_horizons"]:
            try:
                window = resolve_execution_window(df, market_as_of_idx, h)
            except (IndexError, TypeError, ValueError) as exc:
                print(
                    f"settlement skipped invalid execution window "
                    f"{ticker} {as_of} H={h}: {exc}"
                )
                continue
            if window is None:
                continue  # not enough forward data yet; settle on a later run
            path = df.iloc[window.entry_index : window.exit_index + 1]
            payload = compute_outcome(
                action=sig["action"],
                entry_close=window.entry_price,
                exit_close=window.exit_price,
                path_highs=path["high"].astype(float).tolist(),
                path_lows=path["low"].astype(float).tolist(),
            )
            # TOPIX proxy data has closes only. A prior-close benchmark would
            # include an overnight move unavailable to the stock strategy, so
            # v2 fails closed rather than publishing a mismatched excess return.
            benchmark_ret = None
            excess_ret = None
            db.upsert_outcome(
                conn,
                sig["signal_id"],
                h,
                {
                    "market_as_of_date": window.market_as_of_date,
                    "entry_date": window.entry_date,
                    "eval_date": window.exit_date,
                    # Legacy aliases remain populated during the schema rollout.
                    "entry_close": window.entry_price,
                    "exit_close": window.exit_price,
                    "entry_price": window.entry_price,
                    "exit_price": window.exit_price,
                    "entry_price_basis": ENTRY_PRICE_BASIS,
                    "exit_price_basis": EXIT_PRICE_BASIS,
                    "contract_version": EXECUTION_CONTRACT_VERSION,
                    "benchmark_basis": BENCHMARK_BASIS,
                    "realized_ret": payload["realized_ret"],
                    "benchmark_ret": benchmark_ret,
                    "excess_ret": excess_ret,
                    "hit": payload["hit"],
                    "mae": payload["mae"],
                    "mfe": payload["mfe"],
                    "exit_reason": payload["exit_reason"],
                },
            )
            settled += 1
    return settled


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--as-of",
        default=today_jst_iso(),
        help="JST date label (informational; settlement scans all unsettled).",
    )
    parser.add_argument(
        "--refill-benchmark",
        action="store_true",
        help="Backfill benchmark_ret/excess_ret for legacy v1 rows only.",
    )
    parser.add_argument(
        "--restate-execution-contract",
        action="store_true",
        help=(
            "Recompute every actionable signal/horizon under "
            f"{EXECUTION_CONTRACT_VERSION}; use once after migration 0004."
        ),
    )
    args = parser.parse_args()

    if not db.db_enabled():
        print("DB disabled or DATABASE_URL unset; skipping settlement.")
        return 0

    topix_by_date = _load_topix_by_date() if args.refill_benchmark else {}

    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001
        print(
            f"Could not connect for settlement (ignored): {type(exc).__name__}: {exc}"
        )
        return 0

    try:
        if args.restate_execution_contract:
            unsettled = db.fetch_signals_for_outcome_restatement(conn)
            for row in unsettled:
                row["missing_horizons"] = list(OUTCOME_HORIZONS)
            print(
                f"Restating {len(unsettled)} actionable signals under "
                f"{EXECUTION_CONTRACT_VERSION}."
            )
        else:
            unsettled = db.fetch_unsettled(conn)
        by_ticker: dict[str, list[dict]] = {}
        for row in unsettled:
            by_ticker.setdefault(row["ticker"], []).append(row)

        total = 0
        for ticker, sigs in by_ticker.items():
            total += _settle_for_ticker(conn, ticker, sigs, topix_by_date)
        print(
            f"Settlement as-of {args.as_of}: filled {total} outcome rows "
            f"across {len(by_ticker)} tickers ({len(unsettled)} unsettled signals scanned)."
        )

        if args.refill_benchmark and not topix_by_date:
            print("Refill benchmark: no TOPIX data available; skipping.")
        elif args.refill_benchmark:
            missing = db.fetch_outcomes_missing_benchmark(conn)
            refilled = 0
            skipped_contract = 0
            for row in missing:
                contract_version = row.get("contract_version")
                if contract_version not in (
                    None,
                    LEGACY_EXECUTION_CONTRACT_VERSION,
                ):
                    skipped_contract += 1
                    continue
                benchmark_ret = compute_benchmark_ret(
                    topix_by_date, row["entry_date"], row["eval_date"]
                )
                if benchmark_ret is None:
                    continue
                excess_ret = row["realized_ret"] - benchmark_ret
                db.update_outcome_benchmark(
                    conn,
                    row["signal_id"],
                    row["horizon_days"],
                    benchmark_ret,
                    excess_ret,
                )
                refilled += 1
            print(
                f"Refill benchmark: updated {refilled}/{len(missing)} rows "
                f"(skipped {skipped_contract} non-legacy rows)."
            )

        try:
            from src import dashboard

            dashboard.export_performance_summary()
            dashboard.export_performance_detail()
            dashboard.export_signal_outcomes_recent()
        except Exception as exc:  # noqa: BLE001
            print(f"settle export (ignored): {type(exc).__name__}: {exc}")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
