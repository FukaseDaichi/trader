#!/usr/bin/env python3
"""
Evaluation-only market outcome settlement (migration 0005).

For every (ticker, market as-of date) that has a prediction in the DB —
Phase 1 or Phase 2, HOLD included — compute the contract-v2 1/5/10-session
forward return from the ticker's parquet and insert it into market_outcomes.
The window, prices and same-basis TOPIX benchmark are identical to
scripts/settle_outcomes.py; only the population differs (all predicted names
instead of actionable signals).

The ledger feeds the shadow report's full-universe comparison and the drift
report's informational all-prediction metrics. The KPI gate, performance
summaries and notifications never read it, and signal_outcomes is untouched.

Idempotent: existing rows are never rewritten; pairs without enough forward
data settle on a later run. Rows whose benchmark was NULL (panel lag) are
refilled on every run.

Usage:
  uv run python scripts/settle_market_outcomes.py
  uv run python scripts/settle_market_outcomes.py --since 2026-08-01

Always exits 0: DB disabled / unreachable, migration 0005 not applied, or any
other error is logged and skipped so the daily run is never affected.
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
from src.db_records import compute_benchmark_ret  # noqa: E402
from src.data_loader import load_archived_data, load_data  # noqa: E402
from src.execution import (  # noqa: E402
    BENCHMARK_BASIS,
    ENTRY_PRICE_BASIS,
    EXECUTION_CONTRACT_VERSION,
    EXIT_PRICE_BASIS,
    SAME_BASIS_BENCHMARK,
    resolve_execution_window,
)
from scripts.settle_outcomes import _load_topix_by_date  # noqa: E402


def _market_rows_for_ticker(
    ticker: str,
    pending: list[dict],
    topix_open_by_date: dict,
    topix_close_by_date: dict,
) -> list[dict]:
    """Ledger rows for one ticker's pending (as_of_date, horizons) pairs."""
    df = load_data(ticker)
    if df is None:
        df = load_archived_data(ticker)
    if df is None or df.empty or "date" not in df.columns:
        return []
    df = df.sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    date_to_idx = {d: i for i, d in enumerate(df["date"].tolist())}

    rows: list[dict] = []
    for item in pending:
        as_of = str(item["as_of_date"])
        market_as_of_idx = date_to_idx.get(as_of)
        if market_as_of_idx is None:
            continue  # as_of date not present in price history
        for h in item["missing_horizons"]:
            try:
                window = resolve_execution_window(df, market_as_of_idx, h)
            except (IndexError, TypeError, ValueError) as exc:
                print(
                    f"market settlement skipped invalid execution window "
                    f"{ticker} {as_of} H={h}: {exc}"
                )
                continue
            if window is None:
                continue  # not enough forward data yet; settle on a later run
            entry = float(window.entry_price)
            if not entry > 0:
                continue
            realized_ret = float(window.exit_price) / entry - 1.0
            benchmark_ret = compute_benchmark_ret(
                topix_open_by_date,
                topix_close_by_date,
                window.entry_date,
                window.exit_date,
            )
            rows.append(
                {
                    "ticker": ticker,
                    "market_as_of_date": window.market_as_of_date,
                    "horizon_days": int(h),
                    "contract_version": EXECUTION_CONTRACT_VERSION,
                    "entry_date": window.entry_date,
                    "eval_date": window.exit_date,
                    "entry_price": entry,
                    "exit_price": float(window.exit_price),
                    "entry_price_basis": ENTRY_PRICE_BASIS,
                    "exit_price_basis": EXIT_PRICE_BASIS,
                    "realized_ret": realized_ret,
                    "benchmark_ret": benchmark_ret,
                    "excess_ret": (
                        realized_ret - benchmark_ret
                        if benchmark_ret is not None
                        else None
                    ),
                    "benchmark_basis": (
                        SAME_BASIS_BENCHMARK
                        if benchmark_ret is not None
                        else BENCHMARK_BASIS
                    ),
                }
            )
    return rows


def _refill_benchmarks(
    conn, topix_open_by_date: dict, topix_close_by_date: dict
) -> tuple[int, int]:
    """Backfill same-basis benchmark for ledger rows settled while data lagged."""
    missing = db.fetch_market_outcomes_missing_benchmark(conn)
    refilled = 0
    for row in missing:
        benchmark_ret = compute_benchmark_ret(
            topix_open_by_date,
            topix_close_by_date,
            row["entry_date"],
            row["eval_date"],
        )
        if benchmark_ret is None:
            continue
        db.update_market_outcome_benchmark(
            conn,
            row["ticker"],
            row["market_as_of_date"],
            row["horizon_days"],
            benchmark_ret,
            row["realized_ret"] - benchmark_ret,
            SAME_BASIS_BENCHMARK,
        )
        refilled += 1
    return refilled, len(missing)


def settle(conn, *, since: str | None) -> tuple[int, int]:
    """Settle every pending pair; returns (rows inserted, tickers scanned)."""
    topix_open_by_date, topix_close_by_date = _load_topix_by_date()
    pending = db.fetch_unsettled_market_outcomes(conn, since=since)
    by_ticker: dict[str, list[dict]] = {}
    for item in pending:
        by_ticker.setdefault(item["ticker"], []).append(item)

    total = 0
    for ticker, items in by_ticker.items():
        try:
            rows = _market_rows_for_ticker(
                ticker, items, topix_open_by_date, topix_close_by_date
            )
            total += db.insert_market_outcomes(conn, rows)
        except Exception as exc:  # noqa: BLE001 — one ticker must not stop the rest
            conn.rollback()
            print(
                f"market settlement skipped {ticker} (ignored): "
                f"{type(exc).__name__}: {exc}"
            )

    if topix_open_by_date and topix_close_by_date:
        refilled, scanned = _refill_benchmarks(
            conn, topix_open_by_date, topix_close_by_date
        )
        print(f"Market outcome benchmark refill: updated {refilled}/{scanned} rows.")
    return total, len(by_ticker)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--since",
        default=None,
        help="Only settle predictions with as_of_date >= this ISO date.",
    )
    args = parser.parse_args()

    if not db.db_enabled():
        print("DB disabled or DATABASE_URL unset; skipping market settlement.")
        return 0

    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001
        print(
            "Could not connect for market settlement (ignored): "
            f"{type(exc).__name__}: {exc}"
        )
        return 0

    try:
        if not db.market_outcomes_available(conn):
            print(
                "market_outcomes table missing (migration 0005 not applied); skipping."
            )
            return 0
        total, tickers = settle(conn, since=args.since)
        print(f"Market settlement: inserted {total} rows across {tickers} tickers.")
    except Exception as exc:  # noqa: BLE001 — evaluation-only; never fail the job
        print(f"Market settlement failed (ignored): {type(exc).__name__}: {exc}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
