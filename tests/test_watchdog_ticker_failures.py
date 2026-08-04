#!/usr/bin/env python3
"""Regression tests for per-ticker failure detection in the daily watchdog."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.workflow_watchdog import (  # noqa: E402
    _ticker_processing_failures,
    build_parser,
    run_daily_check,
)

TODAY = "2026-07-16"


def _entry(ticker: str, *, status: str = "ok", reason: str = "ok") -> dict:
    return {"ticker": ticker, "status": status, "reason": reason, "passed": False}


def test_failed_status_and_processing_reason_are_listed_concisely():
    report = {
        "entries": [
            _entry("1001.JP"),
            _entry("1002.JP", status="failed", reason="insufficient_data"),
            _entry("1003.JP", status="ok", reason="ticker_processing_failed"),
            _entry("1003.JP", status="ok", reason="ticker_processing_failed"),
        ]
    }
    assert _ticker_processing_failures(report) == [
        "backtest_ticker_failures:1002.JP(insufficient_data),"
        "1003.JP(ticker_processing_failed)"
    ]


def test_clean_or_malformed_reports_are_silent():
    assert _ticker_processing_failures(None) == []
    assert _ticker_processing_failures({"entries": "bad"}) == []
    assert _ticker_processing_failures({"entries": [_entry("1001.JP")]}) == []


def test_daily_check_fails_when_report_contains_ticker_failure():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ticker_dir = root / "tickers"
        ticker_dir.mkdir()

        state = root / "state.json"
        state.write_text(json.dumps({"history": [{"date": TODAY}]}), encoding="utf-8")

        index = root / "dashboard_index.json"
        index.write_text(
            json.dumps(
                {"last_update": f"{TODAY} 06:00:00", "tickers": {"1001.JP": {}}}
            ),
            encoding="utf-8",
        )

        (ticker_dir / "1001.JP.json").write_text(
            json.dumps({"data": []}), encoding="utf-8"
        )
        tickers = root / "tickers.yml"
        tickers.write_text(
            "tickers:\n  - code: 1001.JP\n    name: test\n    enabled: true\n",
            encoding="utf-8",
        )

        report = root / "backtest_report.json"
        report.write_text(
            json.dumps(
                {
                    "entries": [
                        _entry(
                            "1001.JP",
                            status="failed",
                            reason="ticker_processing_failed",
                        )
                    ]
                }
            ),
            encoding="utf-8",
        )

        args = build_parser().parse_args(
            [
                "--today",
                TODAY,
                "--state-file",
                str(state),
                "--index-file",
                str(index),
                "--ticker-dir",
                str(ticker_dir),
                "--report-file",
                str(report),
                "--tickers-file",
                str(tickers),
                "--outbox-dir",
                str(root / "outbox"),
            ]
        )

        assert run_daily_check(args) == 1


ALL_TESTS = [
    test_failed_status_and_processing_reason_are_listed_concisely,
    test_clean_or_malformed_reports_are_silent,
    test_daily_check_fails_when_report_contains_ticker_failure,
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
