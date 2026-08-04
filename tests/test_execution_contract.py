#!/usr/bin/env python3
"""Regression tests for executable-price contract DR-002/DR-003."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest import _simulate_strategy  # noqa: E402
from src.execution import (  # noqa: E402
    EXECUTION_CONTRACT_VERSION,
    add_execution_columns,
    resolve_execution_window,
)


def _market_frame():
    # The missing dates model a weekend plus a JPX holiday. Positional market
    # rows, not calendar-day arithmetic, define the next executable session.
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


def test_window_uses_next_observed_session_open_across_holiday():
    window = resolve_execution_window(_market_frame(), 0, horizon_days=1)
    assert window is not None
    assert window.market_as_of_date == "2026-01-09"
    assert window.entry_date == "2026-01-13"
    assert window.exit_date == "2026-01-13"
    assert window.entry_price == 120.0
    assert window.exit_price == 80.0
    assert abs(window.realized_return - (80.0 / 120.0 - 1.0)) < 1e-12


def test_window_waits_until_full_horizon_exists():
    frame = _market_frame()
    assert resolve_execution_window(frame, 2, horizon_days=2) is None


def test_vectorized_return_does_not_use_unexecutable_prior_close():
    out = add_execution_columns(_market_frame(), horizon_days=1)
    assert abs(out.loc[0, "fwd_return"] - (80.0 / 120.0 - 1.0)) < 1e-12
    assert out.loc[0, "fwd_return"] != 80.0 / 100.0 - 1.0
    assert out.loc[0, "execution_contract_version"] == EXECUTION_CONTRACT_VERSION


def test_sleeves_include_overnight_for_existing_positions_and_cap_gross():
    oos = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=3, freq="D"),
            "market_row_number": [0, 1, 2],
            "prob_up": [0.95, 0.95, 0.95],
            "volatility": [0.01, 0.01, 0.01],
            # New sleeve first-session returns.
            "entry_session_return": [0.10, 0.00, 0.00],
            # Already-held sleeve return includes the overnight gap.
            "continuation_session_return": [0.10, 0.20, 0.00],
            "execution_path_returns": [[0.10, 0.20], [0.00, 0.00], [0.00, 0.00]],
            "execution_path_dates": [
                list(pd.to_datetime(["2026-01-02", "2026-01-03"])),
                list(pd.to_datetime(["2026-01-03", "2026-01-04"])),
                list(pd.to_datetime(["2026-01-04", "2026-01-05"])),
            ],
            "execution_path_market_rows": [[1, 2], [2, 3], [3, 4]],
        }
    )
    sim = _simulate_strategy(
        oos,
        {
            "allow_short": False,
            "cost_bps": 0.0,
            "slippage_bps": 0.0,
        },
        horizon=2,
    )

    assert abs(sim.loc[0, "gross_return"] - 0.05) < 1e-12
    # One prior 1/2-capital sleeve earns the full close-to-close 20%.
    assert abs(sim.loc[1, "gross_return"] - 0.10) < 1e-12
    assert sim["gross_exposure"].max() <= 1.0
    # Tail session is retained so the last OOS sleeve is fully marked/exited.
    assert sim["gross_exposure"].tolist() == [0.5, 1.0, 1.0, 0.5]


def test_migration_versions_legacy_rows_before_v2_restatement():
    sql = (ROOT / "migrations" / "0004_execution_contract.sql").read_text(
        encoding="utf-8"
    )
    for column in (
        "market_as_of_date",
        "entry_price",
        "exit_price",
        "entry_price_basis",
        "contract_version",
        "benchmark_basis",
    ):
        assert column in sql
    assert "close_to_close_v1" in sql


def test_frontend_performance_guards_match_current_contract():
    guard = (ROOT / "web" / "src" / "lib" / "executionContract.ts").read_text(
        encoding="utf-8"
    )
    assert f'"{EXECUTION_CONTRACT_VERSION}"' in guard
    assert "hasCurrentExecutionContract" in guard
    for component in (
        "PerformanceCard.tsx",
        "PerformanceHeadline.tsx",
        "PerformanceDetail.tsx",
    ):
        source = (ROOT / "web" / "src" / "components" / component).read_text(
            encoding="utf-8"
        )
        assert "hasCurrentExecutionContract(v)" in source, component


ALL_TESTS = [
    test_window_uses_next_observed_session_open_across_holiday,
    test_window_waits_until_full_horizon_exists,
    test_vectorized_return_does_not_use_unexecutable_prior_close,
    test_sleeves_include_overnight_for_existing_positions_and_cap_gross,
    test_migration_versions_legacy_rows_before_v2_restatement,
    test_frontend_performance_guards_match_current_contract,
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
