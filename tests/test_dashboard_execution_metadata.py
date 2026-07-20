#!/usr/bin/env python3
"""Contract tests for execution metadata in dashboard JSON exports."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import dashboard  # noqa: E402
from src.execution import EXECUTION_CONTRACT_VERSION  # noqa: E402


class FakeConn:
    def close(self):
        return None


def test_performance_summary_exports_execution_and_accounting_metadata():
    original_path = dashboard.PERFORMANCE_FILE
    original_db_enabled = dashboard.db.db_enabled
    original_connect = dashboard.db.connect
    original_fetch_rows = dashboard.db.fetch_outcome_rows
    original_db_size = dashboard.db.db_size_mb
    original_summarize = dashboard.summarize_performance

    with tempfile.TemporaryDirectory() as tmp:
        try:
            dashboard.PERFORMANCE_FILE = Path(tmp) / "performance_summary.json"
            dashboard.db.db_enabled = lambda: True
            dashboard.db.connect = lambda: FakeConn()
            dashboard.db.fetch_outcome_rows = lambda _conn: [
                {
                    "entry_date": "2026-07-21",
                    "action": "BUY",
                    "horizon_days": 1,
                    "realized_ret": 0.01,
                    "hit": True,
                }
            ]
            dashboard.db.db_size_mb = lambda _conn: 12.5
            dashboard.summarize_performance = lambda *_args, **_kwargs: {
                "n_long_signals": 1,
                "horizons": {"1": {"count": 1}},
                "equity_curve": [
                    {
                        "date": "2026-07-21",
                        "equity": 1.01,
                        "daily_return": 0.01,
                        "n": 1,
                    }
                ],
            }

            dashboard.export_performance_summary()
            payload = json.loads(dashboard.PERFORMANCE_FILE.read_text(encoding="utf-8"))
            assert payload["available"] is True
            assert (
                payload["execution_contract"]["contract_version"]
                == EXECUTION_CONTRACT_VERSION
            )
            assert payload["execution_contract"]["entry_price_basis"] == (
                "next_session_open"
            )
            assert payload["execution_contract"]["cost_treatment"] == (
                "deducted_from_performance_equity"
            )
            assert payload["execution_contract"]["return_basis"] == (
                "net_after_entry_exit_costs"
            )
            cost_metadata = dashboard.performance.equity_cost_metadata(
                dashboard.BACKTEST_GATE_CONFIG["cost_bps"],
                dashboard.BACKTEST_GATE_CONFIG["slippage_bps"],
            )
            assert payload["accounting_method"] == {
                "name": "non_overlapping_cohorts_v1",
                "selection": "daily_horizon_1_cohorts",
                "horizon_days": 1,
                "overlapping_horizon_returns_compounded": False,
                **cost_metadata,
            }
        finally:
            dashboard.PERFORMANCE_FILE = original_path
            dashboard.db.db_enabled = original_db_enabled
            dashboard.db.connect = original_connect
            dashboard.db.fetch_outcome_rows = original_fetch_rows
            dashboard.db.db_size_mb = original_db_size
            dashboard.summarize_performance = original_summarize


def test_recent_outcomes_exports_contract_and_coverage():
    original_path = dashboard.SIGNAL_OUTCOMES_RECENT_FILE
    original_db_enabled = dashboard.db.db_enabled
    original_connect = dashboard.db.connect
    original_fetch_rows = dashboard.db.fetch_outcome_detail_rows
    original_build_recent = dashboard.performance.build_recent_outcomes

    rows = [
        {
            "entry_date": "2026-07-21",
            "contract_version": EXECUTION_CONTRACT_VERSION,
        },
        {
            "entry_date": "2026-07-22",
            "contract_version": EXECUTION_CONTRACT_VERSION,
        },
    ]
    recent = [
        {
            "entry_date": "2026-07-22",
            "ticker": "7011.JP",
            "contract_version": EXECUTION_CONTRACT_VERSION,
        }
    ]

    with tempfile.TemporaryDirectory() as tmp:
        try:
            dashboard.SIGNAL_OUTCOMES_RECENT_FILE = (
                Path(tmp) / "signal_outcomes_recent.json"
            )
            dashboard.db.db_enabled = lambda: True
            dashboard.db.connect = lambda: FakeConn()
            dashboard.db.fetch_outcome_detail_rows = lambda *_args, **_kwargs: rows
            dashboard.performance.build_recent_outcomes = lambda *_args, **_kwargs: (
                recent
            )

            dashboard.export_signal_outcomes_recent()
            payload = json.loads(
                dashboard.SIGNAL_OUTCOMES_RECENT_FILE.read_text(encoding="utf-8")
            )
            assert payload["available"] is True
            assert (
                payload["execution_contract"]["contract_version"]
                == EXECUTION_CONTRACT_VERSION
            )
            assert payload["contract_coverage"] == {
                "required_contract_version": EXECUTION_CONTRACT_VERSION,
                "source_counts": {EXECUTION_CONTRACT_VERSION: 2},
                "included_rows": 2,
                "excluded_rows": 0,
                "fallback_assumption": None,
            }
            assert payload["rows"] == recent
        finally:
            dashboard.SIGNAL_OUTCOMES_RECENT_FILE = original_path
            dashboard.db.db_enabled = original_db_enabled
            dashboard.db.connect = original_connect
            dashboard.db.fetch_outcome_detail_rows = original_fetch_rows
            dashboard.performance.build_recent_outcomes = original_build_recent


def test_contract_coverage_reports_incompatible_rows():
    coverage = dashboard._outcome_contract_coverage(
        [
            {"contract_version": EXECUTION_CONTRACT_VERSION},
            {"contract_version": "close_to_close_v1"},
        ]
    )
    assert coverage["included_rows"] == 1
    assert coverage["excluded_rows"] == 1
    assert coverage["source_counts"] == {
        "close_to_close_v1": 1,
        EXECUTION_CONTRACT_VERSION: 1,
    }


def test_unavailable_performance_detail_always_carries_v2_accounting_metadata():
    original_path = dashboard.PERFORMANCE_DETAIL_FILE
    original_db_enabled = dashboard.db.db_enabled
    original_connect = dashboard.db.connect
    original_fetch_detail = dashboard.db.fetch_outcome_detail_rows
    original_fetch_reliability = dashboard.db.fetch_signal_reliability_rows

    def assert_contract(reason):
        payload = json.loads(
            dashboard.PERFORMANCE_DETAIL_FILE.read_text(encoding="utf-8")
        )
        assert payload["available"] is False
        assert payload["reason"] == reason
        assert (
            payload["execution_contract"]["contract_version"]
            == EXECUTION_CONTRACT_VERSION
        )
        assert payload["execution_contract"]["return_basis"] == (
            "net_after_entry_exit_costs"
        )
        assert payload["accounting_method"]["name"] == ("non_overlapping_cohorts_v1")
        assert payload["accounting_method"]["horizon_days"] == 5
        assert payload["accounting_method"]["round_trip_cost_rate"] > 0
        assert payload["benchmark_coverage"]["reason"] == "no_selected_cohorts"
        assert payload["contract_coverage"]["required_contract_version"] == (
            EXECUTION_CONTRACT_VERSION
        )

    with tempfile.TemporaryDirectory() as tmp:
        try:
            dashboard.PERFORMANCE_DETAIL_FILE = Path(tmp) / "performance_detail.json"

            dashboard.db.db_enabled = lambda: False
            dashboard.export_performance_detail()
            assert_contract("db_disabled")

            dashboard.PERFORMANCE_DETAIL_FILE.unlink()
            dashboard.db.db_enabled = lambda: True

            def unreachable():
                raise RuntimeError("offline")

            dashboard.db.connect = unreachable
            dashboard.export_performance_detail()
            assert_contract("db_unreachable")

            dashboard.PERFORMANCE_DETAIL_FILE.unlink()
            dashboard.db.connect = lambda: FakeConn()
            dashboard.db.fetch_outcome_detail_rows = lambda *_args, **_kwargs: []
            dashboard.db.fetch_signal_reliability_rows = lambda *_args, **_kwargs: {
                "rows": [],
                "provenance": {},
            }
            dashboard.export_performance_detail()
            assert_contract("insufficient_data")
        finally:
            dashboard.PERFORMANCE_DETAIL_FILE = original_path
            dashboard.db.db_enabled = original_db_enabled
            dashboard.db.connect = original_connect
            dashboard.db.fetch_outcome_detail_rows = original_fetch_detail
            dashboard.db.fetch_signal_reliability_rows = original_fetch_reliability


ALL_TESTS = [
    test_performance_summary_exports_execution_and_accounting_metadata,
    test_recent_outcomes_exports_contract_and_coverage,
    test_contract_coverage_reports_incompatible_rows,
    test_unavailable_performance_detail_always_carries_v2_accounting_metadata,
]


def main() -> int:
    failures = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
