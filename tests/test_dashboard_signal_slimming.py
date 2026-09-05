#!/usr/bin/env python3
"""
Unit tests for dashboard signal slimming in src/dashboard.py.

`threshold_optimization` is ~3.3KB of gate-tuning diagnostics per signal. The
dashboard keeps 30 days x 50 tickers of full signal objects, so carrying it
pushed docs/tickers past the watchdog's 10MB guard on 2026-08-17 and kept the
watchdog red for 13 straight trading days.

Nothing downstream of the dashboard reads it: the frontend declares it as an
unused optional type, db_records maps a fixed field list that excludes it, and
the durable audit trail lives in docs/backtest_report.json plus the immutable
data/models/<version>/<ticker>/ticker_metadata.json evidence.

Runnable two ways:
  uv run python tests/test_dashboard_signal_slimming.py
  uv run pytest tests/test_dashboard_signal_slimming.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.dashboard import (  # noqa: E402
    DASHBOARD_SIGNAL_DROP_FIELDS,
    _normalize_history,
    _normalize_signals,
)


def _signal(ticker: str = "7011.JP") -> dict:
    return {
        "ticker": ticker,
        "name": "三菱重工業",
        "date": "2026-09-04",
        "close": 4586.0,
        "prob_up": 0.72,
        "action": "MILD_BUY",
        "raw_action": "MILD_BUY",
        "gate_passed": True,
        "status": "ok",
        "thresholds": {"buy": 0.8, "mild_buy": 0.65},
        "threshold_optimization": {
            "enabled": True,
            "selected_metrics": {"metrics_semantics": {"round_trips": "..."}},
        },
        "model_version": "per-ticker-v1-x",
        "horizon_days": 5,
    }


def test_drop_fields_contains_threshold_optimization() -> None:
    assert "threshold_optimization" in DASHBOARD_SIGNAL_DROP_FIELDS


def test_normalize_signals_drops_threshold_optimization() -> None:
    out = _normalize_signals([_signal()])
    assert len(out) == 1
    assert "threshold_optimization" not in out[0]


def test_normalize_signals_keeps_every_other_field() -> None:
    src = _signal()
    out = _normalize_signals([src])[0]
    for key, value in src.items():
        if key in DASHBOARD_SIGNAL_DROP_FIELDS:
            continue
        assert out[key] == value, f"{key} changed"
    # `thresholds` is what the frontend renders; it must survive.
    assert out["thresholds"] == {"buy": 0.8, "mild_buy": 0.65}


def test_normalize_signals_does_not_mutate_caller_signal() -> None:
    """main.py hands the same dicts to db.record_run before update_dashboard."""
    src = _signal()
    _normalize_signals([src])
    assert "threshold_optimization" in src


def test_normalize_history_strips_already_persisted_days() -> None:
    """Existing state.json days are slimmed retroactively, not only new ones."""
    history = [
        {"date": "2026-09-04", "signals": [_signal("7011.JP")]},
        {"date": "2026-09-03", "signals": [_signal("6501.JP")]},
    ]
    out = _normalize_history(history)
    assert len(out) == 2
    for entry in out:
        for signal in entry["signals"]:
            assert "threshold_optimization" not in signal


def test_normalize_signals_still_filters_and_dedupes() -> None:
    a, b, dup = _signal("7011.JP"), _signal("6501.JP"), _signal("7011.JP")
    out = _normalize_signals([a, dup, b, "junk", {}], allowed_tickers={"7011.JP"})
    assert [s["ticker"] for s in out] == ["7011.JP"]


ALL_TESTS = [
    test_drop_fields_contains_threshold_optimization,
    test_normalize_signals_drops_threshold_optimization,
    test_normalize_signals_keeps_every_other_field,
    test_normalize_signals_does_not_mutate_caller_signal,
    test_normalize_history_strips_already_persisted_days,
    test_normalize_signals_still_filters_and_dedupes,
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
