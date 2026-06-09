#!/usr/bin/env python3
"""
Unit tests for the Phase 2 portfolio dashboard exports in src/dashboard.py
(``export_portfolio_latest`` / ``export_portfolio_backtest``). No DB / no
network: every write is pointed at a temp directory via ``output_path``.

Runnable two ways:
  uv run python tests/test_dashboard_portfolio.py     # standalone
  uv run pytest tests/test_dashboard_portfolio.py      # if pytest is available
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import json  # noqa: E402
import tempfile  # noqa: E402

from src.dashboard import (  # noqa: E402
    export_portfolio_backtest,
    export_portfolio_latest,
)


def _snapshot_ok():
    return {
        "run_date": "2026-06-09",
        "as_of_date": "2026-06-06",
        "mode": "shadow",
        "status": "ok",
        "model_version": "cs-v1-20260609",
        "gross_exposure": 0.85,
        "net_exposure": 0.85,
        "expected_vol": 0.11,
        "expected_ret": 0.012,
        "sector_exposure": {"Industrials": 0.4},
        "diff_summary": {"add": 2, "trim": 1, "exit": 0, "hold": 5},
        "positions": [
            {"ticker": "7011.JP", "name": "三菱重工業", "sector": "Industrials",
             "target_weight": 0.2, "prev_weight": 0.15, "diff_type": "increase"},
        ],
        "constraints": {"target_vol": 0.12, "top_n": 8},
        "warnings": [],
    }


def test_export_portfolio_latest_available():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "portfolio_latest.json"
        ret = export_portfolio_latest(
            _snapshot_ok(), run_date="2026-06-09",
            generated_at="2026-06-09 06:00:00", output_path=out,
        )
        assert ret == str(out)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["available"] is True
        assert data["generated_at"] == "2026-06-09 06:00:00"
        assert data["run_date"] == "2026-06-09"
        assert data["mode"] == "shadow"
        assert data["status"] == "ok"
        assert len(data["positions"]) == 1
        assert data["positions"][0]["ticker"] == "7011.JP"
        assert data["diff_summary"]["add"] == 2


def test_export_portfolio_latest_unavailable_with_reason():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "portfolio_latest.json"
        export_portfolio_latest(
            None, reason="insufficient_universe", run_date="2026-06-09",
            generated_at="2026-06-09 06:00:00", output_path=out,
        )
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["available"] is False
        assert data["reason"] == "insufficient_universe"
        assert data["run_date"] == "2026-06-09"
        assert data["generated_at"] == "2026-06-09 06:00:00"
        assert "positions" not in data


def test_export_portfolio_latest_non_ok_status_is_unavailable():
    # A non-"ok" snapshot status -> unavailable, reason defaults to the status.
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "portfolio_latest.json"
        snap = _snapshot_ok()
        snap["status"] = "failed"
        export_portfolio_latest(snap, run_date="2026-06-09", output_path=out)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["available"] is False
        assert data["reason"] == "failed"


def test_export_portfolio_latest_omits_generated_at_when_absent():
    # No generated_at supplied -> key absent (deterministic; no datetime.now).
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "portfolio_latest.json"
        export_portfolio_latest(_snapshot_ok(), run_date="2026-06-09", output_path=out)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert "generated_at" not in data


def test_export_portfolio_backtest_insufficient_is_unavailable():
    # Delegate to write_portfolio_backtest_report: insufficient -> available:false.
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "portfolio_backtest.json"
        result = {"status": "insufficient", "metrics": {}, "equity": []}
        ret = export_portfolio_backtest(
            result, model_version="cs-v1-20260609", run_date="2026-06-10",
            generated_at="2026-06-10 06:00:00", output_path=out,
        )
        assert ret == str(out)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["available"] is False
        assert data["reason"] == "insufficient"
        assert data["model_version"] == "cs-v1-20260609"
        assert data["run_date"] == "2026-06-10"
        assert data["generated_at"] == "2026-06-10 06:00:00"


def test_export_portfolio_backtest_ok_is_available():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "portfolio_backtest.json"
        result = {
            "status": "ok",
            "start_date": "2024-01-05",
            "end_date": "2024-12-20",
            "n_periods": 48,
            "metrics": {"sharpe": 0.8},
            "equity": [],
            "params": {"top_n": 8},
        }
        export_portfolio_backtest(result, output_path=out)
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["available"] is True
        assert data["metrics"]["sharpe"] == 0.8


ALL_TESTS = [
    test_export_portfolio_latest_available,
    test_export_portfolio_latest_unavailable_with_reason,
    test_export_portfolio_latest_non_ok_status_is_unavailable,
    test_export_portfolio_latest_omits_generated_at_when_absent,
    test_export_portfolio_backtest_insufficient_is_unavailable,
    test_export_portfolio_backtest_ok_is_available,
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
