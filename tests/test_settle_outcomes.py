#!/usr/bin/env python3
"""Pure settlement contract tests (no DB or network)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import settle_outcomes  # noqa: E402
from src.execution import EXECUTION_CONTRACT_VERSION  # noqa: E402


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
            {"2026-01-09": 100.0, "2026-01-13": 200.0},
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
    # Even available prior-close TOPIX data must not be mixed into v2.
    assert one_day["benchmark_ret"] is None
    assert one_day["excess_ret"] is None
    assert one_day["benchmark_basis"] == "unavailable_same_basis"


ALL_TESTS = [test_settlement_uses_same_next_open_window_as_labels]


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
