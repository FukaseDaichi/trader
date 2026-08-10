#!/usr/bin/env python3
"""Pure settlement contract tests (no DB or network)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import settle_outcomes  # noqa: E402
from src.execution import (  # noqa: E402
    BENCHMARK_BASIS,
    EXECUTION_CONTRACT_VERSION,
    SAME_BASIS_BENCHMARK,
)


def test_settlement_uses_same_next_open_window_as_labels():
    prices = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2026-01-09", "2026-01-13", "2026-01-14", "2026-01-15"]
            ),
            "open": [100.0, 120.0, 90.0, 95.0],
            "high": [101.0, 122.0, 96.0, 99.0],
            "low": [99.0, 79.0, 88.0, 94.0],
            "close": [100.0, 80.0, 95.0, 98.0],
        }
    )
    captured = []
    original_load = settle_outcomes.load_data
    original_upsert = settle_outcomes.db.upsert_outcome
    try:
        settle_outcomes.load_data = lambda ticker: prices.copy()
        settle_outcomes.db.upsert_outcome = lambda conn, signal_id, horizon, payload: (
            captured.append((signal_id, horizon, payload))
        )
        count = settle_outcomes._settle_for_ticker(
            object(),
            "7011.JP",
            [
                {
                    "signal_id": 7,
                    "as_of_date": "2026-01-09",
                    "action": "BUY",
                    "missing_horizons": [1, 2],
                }
            ],
            {"2026-01-13": 2000.0, "2026-01-14": 2030.0},
            {"2026-01-13": 2010.0, "2026-01-14": 2040.0},
        )
    finally:
        settle_outcomes.load_data = original_load
        settle_outcomes.db.upsert_outcome = original_upsert

    assert count == 2
    one_day = captured[0][2]
    assert one_day["market_as_of_date"] == "2026-01-09"
    assert one_day["entry_date"] == "2026-01-13"
    assert one_day["eval_date"] == "2026-01-13"
    assert one_day["entry_price"] == 120.0
    assert one_day["exit_price"] == 80.0
    assert abs(one_day["realized_ret"] - (80.0 / 120.0 - 1.0)) < 1e-12
    assert one_day["contract_version"] == EXECUTION_CONTRACT_VERSION
    # H=1: same-basis TOPIX open(entry 01-13) -> close(eval 01-13).
    assert abs(one_day["benchmark_ret"] - (2010.0 / 2000.0 - 1.0)) < 1e-12
    assert (
        abs(one_day["excess_ret"] - ((80.0 / 120.0 - 1.0) - (2010.0 / 2000.0 - 1.0)))
        < 1e-12
    )
    assert one_day["benchmark_basis"] == SAME_BASIS_BENCHMARK
    assert SAME_BASIS_BENCHMARK == "next_session_open_to_horizon_session_close"
    # H=2: entry open 01-13 -> eval close 01-14.
    two_day = captured[1][2]
    assert abs(two_day["benchmark_ret"] - (2040.0 / 2000.0 - 1.0)) < 1e-12
    assert two_day["benchmark_basis"] == SAME_BASIS_BENCHMARK


def test_settlement_falls_back_to_archived_inactive_ticker_data():
    prices = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-09", "2026-01-13"]),
            "open": [100.0, 120.0],
            "high": [101.0, 122.0],
            "low": [99.0, 119.0],
            "close": [100.0, 121.0],
        }
    )
    captured = []
    original_load = settle_outcomes.load_data
    original_load_archived = settle_outcomes.load_archived_data
    original_upsert = settle_outcomes.db.upsert_outcome
    try:
        settle_outcomes.load_data = lambda ticker: None
        settle_outcomes.load_archived_data = lambda ticker: prices.copy()
        settle_outcomes.db.upsert_outcome = lambda conn, signal_id, horizon, payload: (
            captured.append((signal_id, horizon, payload))
        )
        count = settle_outcomes._settle_for_ticker(
            object(),
            "8053.JP",
            [
                {
                    "signal_id": 8,
                    "as_of_date": "2026-01-09",
                    "action": "BUY",
                    "missing_horizons": [1],
                }
            ],
            {},
            {},
        )
    finally:
        settle_outcomes.load_data = original_load
        settle_outcomes.load_archived_data = original_load_archived
        settle_outcomes.db.upsert_outcome = original_upsert

    assert count == 1
    assert captured[0][2]["entry_date"] == "2026-01-13"
    assert captured[0][2]["entry_price"] == 120.0
    assert captured[0][2]["benchmark_ret"] is None
    assert captured[0][2]["excess_ret"] is None
    assert captured[0][2]["benchmark_basis"] == BENCHMARK_BASIS
    assert BENCHMARK_BASIS == "unavailable_same_basis"


def test_loader_excludes_dates_whose_open_is_missing():
    # 2026-01-14 has a forward-filled close but no genuine open -> excluded
    # from BOTH dicts, so it can never supply an entry or an exit price.
    panel = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-13", "2026-01-14", "2026-01-15"]),
            "topix_open": [2000.0, float("nan"), 2050.0],
            "topix": [2010.0, 2010.0, 2070.0],
        }
    )
    original_read = settle_outcomes.pd.read_parquet
    try:
        settle_outcomes.pd.read_parquet = lambda path: panel.copy()
        opens, closes = settle_outcomes._load_topix_by_date()
    finally:
        settle_outcomes.pd.read_parquet = original_read

    assert sorted(opens) == ["2026-01-13", "2026-01-15"]
    assert sorted(closes) == ["2026-01-13", "2026-01-15"]
    assert opens["2026-01-13"] == 2000.0
    assert closes["2026-01-15"] == 2070.0


def test_loader_degrades_when_panel_unreadable():
    def _boom(path):
        raise ValueError("corrupt parquet")

    original_read = settle_outcomes.pd.read_parquet
    try:
        settle_outcomes.pd.read_parquet = _boom
        opens, closes = settle_outcomes._load_topix_by_date()
    finally:
        settle_outcomes.pd.read_parquet = original_read

    assert opens == {} and closes == {}


def test_refill_targets_v2_null_rows_and_updates_basis():
    updates = []
    original_fetch = settle_outcomes.db.fetch_outcomes_missing_benchmark
    original_update = settle_outcomes.db.update_outcome_benchmark
    try:
        settle_outcomes.db.fetch_outcomes_missing_benchmark = lambda conn: [
            {
                "signal_id": 1,
                "horizon_days": 1,
                "entry_date": "2026-01-13",
                "eval_date": "2026-01-13",
                "realized_ret": 0.02,
            },
            {  # panel has no data for this window -> stays NULL, no update call
                "signal_id": 2,
                "horizon_days": 5,
                "entry_date": "2026-02-02",
                "eval_date": "2026-02-06",
                "realized_ret": -0.01,
            },
        ]
        settle_outcomes.db.update_outcome_benchmark = (
            lambda conn, signal_id, horizon_days, benchmark_ret, excess_ret, benchmark_basis: (
                updates.append(
                    (
                        signal_id,
                        horizon_days,
                        benchmark_ret,
                        excess_ret,
                        benchmark_basis,
                    )
                )
            )
        )
        refilled, scanned = settle_outcomes._refill_v2_benchmarks(
            object(),
            {"2026-01-13": 2000.0},
            {"2026-01-13": 2010.0},
        )
    finally:
        settle_outcomes.db.fetch_outcomes_missing_benchmark = original_fetch
        settle_outcomes.db.update_outcome_benchmark = original_update

    assert (refilled, scanned) == (1, 2)
    assert len(updates) == 1
    signal_id, horizon, benchmark_ret, excess_ret, basis = updates[0]
    assert (signal_id, horizon) == (1, 1)
    assert abs(benchmark_ret - (2010.0 / 2000.0 - 1.0)) < 1e-12
    assert abs(excess_ret - (0.02 - (2010.0 / 2000.0 - 1.0))) < 1e-12
    assert basis == SAME_BASIS_BENCHMARK


ALL_TESTS = [
    test_settlement_uses_same_next_open_window_as_labels,
    test_settlement_falls_back_to_archived_inactive_ticker_data,
    test_loader_excludes_dates_whose_open_is_missing,
    test_loader_degrades_when_panel_unreadable,
    test_refill_targets_v2_null_rows_and_updates_basis,
]


def main() -> int:
    failures = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
