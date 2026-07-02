#!/usr/bin/env python3
"""
Unit tests for the holdout-usage surfacing in the backtest report and the
watchdog warning (no DB / no network).

A gate pass with holdout_used=False means thresholds were tuned and evaluated
on the same OOS rows (optimistic pass) — see backtest._split_oos_for_thresholding.

Runnable two ways:
  uv run python tests/test_backtest_report.py
  uv run pytest tests/test_backtest_report.py      # if pytest is available
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.workflow_watchdog import _holdout_warnings  # noqa: E402
from src.backtest import summarize_holdout  # noqa: E402


def _entry(ticker: str, passed: bool, holdout_used: bool | None) -> dict:
    optimization = None if holdout_used is None else {"holdout_used": holdout_used}
    return {
        "ticker": ticker,
        "passed": passed,
        "threshold_optimization": optimization,
    }


def test_summary_counts_only_gate_passed_entries():
    entries = [
        _entry("1001", True, True),
        _entry("1002", True, False),
        _entry("1003", False, False),  # gate-failed: never counted
    ]
    summary = summarize_holdout(entries)
    assert summary["gate_passed"] == 2
    assert summary["passed_without_holdout"] == 1
    assert summary["tickers_without_holdout"] == ["1002"]


def test_summary_missing_optimization_counts_as_no_holdout():
    summary = summarize_holdout([_entry("1001", True, None)])
    assert summary["passed_without_holdout"] == 1
    assert summary["tickers_without_holdout"] == ["1001"]


def test_summary_all_holdout_is_clean():
    entries = [_entry("1001", True, True), _entry("1002", True, True)]
    summary = summarize_holdout(entries)
    assert summary["gate_passed"] == 2
    assert summary["passed_without_holdout"] == 0
    assert summary["tickers_without_holdout"] == []


def test_gate_disabled_skipped_entries_are_excluded():
    # gate_disabled → passed=True, skipped=True, holdout_used=False; these
    # never went through threshold optimization and must not pollute the
    # "tuned on the same rows" metric.
    entry = _entry("1001", True, False)
    entry["skipped"] = True
    summary = summarize_holdout([entry])
    assert summary["gate_passed"] == 0
    assert summary["passed_without_holdout"] == 0
    assert _holdout_warnings({"entries": [entry]}) == []


def test_summary_handles_empty_and_malformed_entries():
    summary = summarize_holdout([None, "junk", {}])
    assert summary == {
        "gate_passed": 0,
        "passed_without_holdout": 0,
        "tickers_without_holdout": [],
    }


def test_watchdog_warns_on_pass_without_holdout():
    report = {"entries": [_entry("1001", True, False), _entry("1002", True, True)]}
    assert _holdout_warnings(report) == ["gate_passed_without_holdout:1001"]


def test_watchdog_silent_when_all_passes_have_holdout():
    report = {"entries": [_entry("1001", True, True)]}
    assert _holdout_warnings(report) == []


def test_watchdog_silent_on_missing_or_malformed_report():
    assert _holdout_warnings(None) == []
    assert _holdout_warnings({}) == []
    assert _holdout_warnings({"entries": "junk"}) == []


ALL_TESTS = [
    test_summary_counts_only_gate_passed_entries,
    test_summary_missing_optimization_counts_as_no_holdout,
    test_summary_all_holdout_is_clean,
    test_gate_disabled_skipped_entries_are_excluded,
    test_summary_handles_empty_and_malformed_entries,
    test_watchdog_warns_on_pass_without_holdout,
    test_watchdog_silent_when_all_passes_have_holdout,
    test_watchdog_silent_on_missing_or_malformed_report,
]


def main() -> int:
    failures = 0
    for t in ALL_TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
