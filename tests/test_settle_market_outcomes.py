#!/usr/bin/env python3
"""Evaluation-only market outcome settlement tests (no DB or network)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import settle_market_outcomes  # noqa: E402
from src.execution import (  # noqa: E402
    BENCHMARK_BASIS,
    EXECUTION_CONTRACT_VERSION,
    SAME_BASIS_BENCHMARK,
)


def _prices() -> pd.DataFrame:
    return pd.DataFrame(
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


class _FakeConn:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


def _patch(**overrides):
    originals = {}
    for name, value in overrides.items():
        target, attr = (
            (settle_market_outcomes.db, name[3:])
            if name.startswith("db_")
            else (settle_market_outcomes, name)
        )
        originals[(target, attr)] = getattr(target, attr)
        setattr(target, attr, value)
    return originals


def _restore(originals):
    for (target, attr), value in originals.items():
        setattr(target, attr, value)


def test_rows_use_contract_v2_window_without_action():
    originals = _patch(load_data=lambda ticker: _prices())
    try:
        rows = settle_market_outcomes._market_rows_for_ticker(
            "7011.JP",
            [{"as_of_date": "2026-01-09", "missing_horizons": [1, 2, 10]}],
            {"2026-01-13": 2000.0},
            {"2026-01-13": 2010.0},
        )
    finally:
        _restore(originals)

    # H=10 lacks forward data and is left for a later run.
    assert [r["horizon_days"] for r in rows] == [1, 2]
    one = rows[0]
    assert one["ticker"] == "7011.JP"
    assert one["market_as_of_date"] == "2026-01-09"
    assert one["entry_date"] == "2026-01-13"
    assert one["eval_date"] == "2026-01-13"
    assert one["entry_price"] == 120.0 and one["exit_price"] == 80.0
    assert abs(one["realized_ret"] - (80.0 / 120.0 - 1.0)) < 1e-12
    assert one["contract_version"] == EXECUTION_CONTRACT_VERSION
    assert abs(one["benchmark_ret"] - (2010.0 / 2000.0 - 1.0)) < 1e-12
    assert one["benchmark_basis"] == SAME_BASIS_BENCHMARK
    # No action-dependent fields: the ledger is a market fact.
    assert "hit" not in one and "action" not in one
    # H=2 exits on 01-14, which has no same-basis TOPIX close -> NULL, not 0.
    two = rows[1]
    assert two["benchmark_ret"] is None and two["excess_ret"] is None
    assert two["benchmark_basis"] == BENCHMARK_BASIS


def test_rows_fall_back_to_archived_data_and_skip_unknown_dates():
    originals = _patch(
        load_data=lambda ticker: None,
        load_archived_data=lambda ticker: _prices(),
    )
    try:
        rows = settle_market_outcomes._market_rows_for_ticker(
            "8053.JP",
            [
                {"as_of_date": "2026-01-09", "missing_horizons": [1]},
                {"as_of_date": "2026-01-10", "missing_horizons": [1]},
            ],
            {},
            {},
        )
    finally:
        _restore(originals)
    assert len(rows) == 1
    assert rows[0]["market_as_of_date"] == "2026-01-09"


def test_settle_covers_hold_names_and_isolates_ticker_failures():
    inserted = []

    def fake_insert(conn, rows):
        if rows and rows[0]["ticker"] == "6098.JP":
            raise RuntimeError("boom")
        inserted.extend(rows)
        return len(rows)

    pending = [
        {"ticker": "7011.JP", "as_of_date": "2026-01-09", "missing_horizons": [1]},
        {"ticker": "6098.JP", "as_of_date": "2026-01-09", "missing_horizons": [1]},
        {"ticker": "6501.JP", "as_of_date": "2026-01-09", "missing_horizons": [1]},
    ]
    conn = _FakeConn()
    originals = _patch(
        load_data=lambda ticker: _prices(),
        _load_topix_by_date=lambda: ({}, {}),
        db_fetch_unsettled_market_outcomes=lambda conn, since=None: pending,
        db_insert_market_outcomes=fake_insert,
    )
    try:
        total, tickers = settle_market_outcomes.settle(conn, since=None)
    finally:
        _restore(originals)
    assert tickers == 3
    assert total == 2
    assert sorted(r["ticker"] for r in inserted) == ["6501.JP", "7011.JP"]
    assert conn.rollbacks == 1


def test_refill_updates_only_rows_with_benchmark():
    updates = []
    originals = _patch(
        db_fetch_market_outcomes_missing_benchmark=lambda conn: [
            {
                "ticker": "7011.JP",
                "market_as_of_date": "2026-01-09",
                "horizon_days": 1,
                "entry_date": "2026-01-13",
                "eval_date": "2026-01-13",
                "realized_ret": 0.05,
            },
            {
                "ticker": "7011.JP",
                "market_as_of_date": "2026-01-09",
                "horizon_days": 2,
                "entry_date": "2026-01-13",
                "eval_date": "2026-01-14",
                "realized_ret": 0.02,
            },
        ],
        db_update_market_outcome_benchmark=lambda conn, *args: updates.append(args),
    )
    try:
        refilled, scanned = settle_market_outcomes._refill_benchmarks(
            object(), {"2026-01-13": 2000.0}, {"2026-01-13": 2010.0}
        )
    finally:
        _restore(originals)
    assert (refilled, scanned) == (1, 2)
    ticker, as_of, horizon, bench, excess, basis = updates[0]
    assert (ticker, as_of, horizon) == ("7011.JP", "2026-01-09", 1)
    assert abs(bench - 0.005) < 1e-12
    assert abs(excess - 0.045) < 1e-12
    assert basis == SAME_BASIS_BENCHMARK


def test_main_exits_zero_when_db_disabled_or_table_missing():
    closed = []

    class _Conn:
        def close(self):
            closed.append(True)

    argv = sys.argv
    sys.argv = ["settle_market_outcomes.py"]
    originals = _patch(db_db_enabled=lambda: False)
    try:
        assert settle_market_outcomes.main() == 0
    finally:
        _restore(originals)

    called = []
    originals = _patch(
        db_db_enabled=lambda: True,
        db_connect=lambda: _Conn(),
        db_market_outcomes_available=lambda conn: False,
        settle=lambda conn, since: called.append(True),
    )
    try:
        assert settle_market_outcomes.main() == 0
    finally:
        _restore(originals)
    assert called == []
    assert closed == [True]

    def boom():
        raise OSError("unreachable")

    originals = _patch(db_db_enabled=lambda: True, db_connect=boom)
    try:
        assert settle_market_outcomes.main() == 0
    finally:
        _restore(originals)
        sys.argv = argv


ALL_TESTS = [
    test_rows_use_contract_v2_window_without_action,
    test_rows_fall_back_to_archived_data_and_skip_unknown_dates,
    test_settle_covers_hold_names_and_isolates_ticker_failures,
    test_refill_updates_only_rows_with_benchmark,
    test_main_exits_zero_when_db_disabled_or_table_missing,
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
