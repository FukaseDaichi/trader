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

The macro panel carries a same-basis TOPIX-proxy open (topix_open, same
instrument and adjustment basis as the topix close column). Settlement
computes the contract-v2 same-basis benchmark inline
(topix_open[entry_date] -> topix[eval_date] close, gross), and
--refill-benchmark idempotently backfills v2 rows whose benchmark is still
NULL (e.g. the panel lagged on settle day). Rows settle with NULL benchmark
and benchmark_basis=unavailable_same_basis when either level is missing.
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
    SAME_BASIS_BENCHMARK,
    resolve_execution_window,
)
from scripts.curation_common import today_jst_iso  # noqa: E402


def _load_topix_by_date() -> tuple[dict[str, float], dict[str, float]]:
    """Return ({date: topix_open}, {date: topix_close}) from the macro panel.

    Both dicts are keyed only on dates where topix_open AND topix are present
    and positive.  src/macro.py forward-fills close columns but never opens, so
    a genuine open proves the instrument traded that date and its close from the
    same source row is genuine too; keying both sides off that rule keeps a
    stale forward-filled close from becoming an exit price.  Same rule as
    src/portfolio_backtest.py::_prepare_topix, so settlement and the Phase 2
    backtest measure the identical basis.

    Never raises: any problem degrades to empty dicts (benchmark stays NULL).
    """
    macro_path = ROOT / "data" / "macro" / "macro_panel.parquet"
    try:
        df = pd.read_parquet(macro_path)
        required = {"date", "topix_open", "topix"}
        if not required.issubset(df.columns):
            print(
                "macro_panel.parquet lacks same-basis benchmark columns "
                f"{sorted(required.difference(df.columns))}; benchmark stays NULL"
            )
            return {}, {}
        tp = df[["date", "topix_open", "topix"]].copy()
        tp["date"] = pd.to_datetime(tp["date"], errors="coerce")
        tp["topix_open"] = pd.to_numeric(tp["topix_open"], errors="coerce")
        tp["topix"] = pd.to_numeric(tp["topix"], errors="coerce")
        tp = tp.dropna(subset=["date", "topix_open", "topix"])
        tp = tp[(tp["topix_open"] > 0) & (tp["topix"] > 0)]
        tp = tp.sort_values("date").drop_duplicates(subset="date", keep="last")
        if tp.empty:
            print(
                "macro_panel.parquet has no same-basis TOPIX rows; benchmark stays NULL"
            )
            return {}, {}
        keys = tp["date"].dt.strftime("%Y-%m-%d")
        opens = {d: float(v) for d, v in zip(keys, tp["topix_open"])}
        closes = {d: float(v) for d, v in zip(keys, tp["topix"])}
        return opens, closes
    except FileNotFoundError:
        print("macro_panel.parquet not found; TOPIX benchmark will stay NULL")
        return {}, {}
    except Exception as exc:  # noqa: BLE001
        print(f"macro_panel benchmark load error (benchmark stays NULL): {exc}")
        return {}, {}


def _settle_for_ticker(
    conn,
    ticker: str,
    signals: list[dict],
    topix_open_by_date: dict,
    topix_close_by_date: dict,
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
            benchmark_ret = compute_benchmark_ret(
                topix_open_by_date,
                topix_close_by_date,
                window.entry_date,
                window.exit_date,
            )
            excess_ret = (
                payload["realized_ret"] - benchmark_ret
                if benchmark_ret is not None
                else None
            )
            benchmark_basis = (
                SAME_BASIS_BENCHMARK if benchmark_ret is not None else BENCHMARK_BASIS
            )
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
                    "benchmark_basis": benchmark_basis,
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


def _refill_v2_benchmarks(
    conn, topix_open_by_date: dict, topix_close_by_date: dict
) -> tuple[int, int]:
    """Backfill same-basis benchmark for v2 rows settled while data lagged.

    Idempotent: only rows with benchmark_ret still NULL are scanned, and the
    computation is deterministic.  Returns (refilled, scanned).
    """
    missing = db.fetch_outcomes_missing_benchmark(conn)
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
        db.update_outcome_benchmark(
            conn,
            row["signal_id"],
            row["horizon_days"],
            benchmark_ret,
            row["realized_ret"] - benchmark_ret,
            SAME_BASIS_BENCHMARK,
        )
        refilled += 1
    return refilled, len(missing)


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
        help="Backfill same-basis benchmark_ret/excess_ret for v2 rows still NULL.",
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

    topix_open_by_date, topix_close_by_date = _load_topix_by_date()

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
            total += _settle_for_ticker(
                conn, ticker, sigs, topix_open_by_date, topix_close_by_date
            )
        print(
            f"Settlement as-of {args.as_of}: filled {total} outcome rows "
            f"across {len(by_ticker)} tickers ({len(unsettled)} unsettled signals scanned)."
        )

        if args.refill_benchmark:
            if not topix_open_by_date or not topix_close_by_date:
                print("Refill benchmark: no same-basis TOPIX data; skipping.")
            else:
                refilled, scanned = _refill_v2_benchmarks(
                    conn, topix_open_by_date, topix_close_by_date
                )
                print(f"Refill benchmark: updated {refilled}/{scanned} v2 rows.")

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
