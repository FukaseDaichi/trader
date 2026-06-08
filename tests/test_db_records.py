#!/usr/bin/env python3
"""
Unit tests for src/db_records.py (pure logic, no DB / no network).

Runnable two ways:
  uv run python tests/test_db_records.py     # standalone
  uv run pytest tests/test_db_records.py      # if pytest is available
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db_records import (  # noqa: E402
    compute_outcome,
    make_event_id,
    signal_to_prediction_row,
    signal_to_signal_row,
    summarize_performance,
)

OK_SIGNAL = {
    "ticker": "7011.JP",
    "name": "三菱重工業",
    "date": "2026-06-05",
    "close": 4586.0,
    "prob_up": 0.72,
    "action": "MILD_BUY",
    "raw_action": "MILD_BUY",
    "gate_passed": True,
    "status": "ok",
    "thresholds": {"buy": 0.8, "mild_buy": 0.65, "mild_sell": 0.25, "sell": 0.1, "volatility_limit": 0.04},
    "limit_price": None,
    "stop_loss": None,
    "reason": "やや上昇傾向 (上昇確率 72%)",
}

FAILED_SIGNAL = {
    "ticker": "9999.JP",
    "name": "失敗銘柄",
    "date": "2026-06-08",
    "close": None,
    "prob_up": None,
    "action": "HOLD",
    "raw_action": "HOLD",
    "gate_passed": False,
    "status": "failed",
}


def test_event_id_is_stable_and_namespaced():
    assert make_event_id("2026-06-08", "7011.JP", "sig") == "2026-06-08:7011.JP:sig"
    assert make_event_id("2026-06-08", "7011.JP", "pred") != make_event_id("2026-06-08", "7011.JP", "sig")


def test_prediction_row_for_ok_signal():
    row = signal_to_prediction_row(OK_SIGNAL, run_date="2026-06-08")
    assert row is not None
    assert row["run_date"] == "2026-06-08"
    assert row["as_of_date"] == "2026-06-05"      # signal['date'] = latest price date
    assert row["ticker"] == "7011.JP"
    assert row["model_version"] == "legacy-daily-v0"
    assert row["horizon_days"] == 1               # legacy model predicts next-day
    assert abs(row["prob_up"] - 0.72) < 1e-9
    assert abs(row["raw_score"] - 0.72) < 1e-9


def test_prediction_row_is_none_when_prob_missing():
    assert signal_to_prediction_row(FAILED_SIGNAL, run_date="2026-06-08") is None


def test_signal_row_maps_core_fields():
    row = signal_to_signal_row(OK_SIGNAL, run_date="2026-06-08")
    assert row["run_date"] == "2026-06-08"
    assert row["as_of_date"] == "2026-06-05"
    assert row["ticker"] == "7011.JP"
    assert row["action"] == "MILD_BUY"
    assert row["gate_passed"] is True
    assert row["status"] == "ok"
    assert abs(row["conviction"] - 0.72) < 1e-9
    assert row["target_weight"] is None           # Phase 2
    assert row["thresholds"]["buy"] == 0.8


def test_signal_row_for_failed_signal():
    row = signal_to_signal_row(FAILED_SIGNAL, run_date="2026-06-08")
    assert row["ticker"] == "9999.JP"
    assert row["action"] == "HOLD"
    assert row["gate_passed"] is False
    assert row["status"] == "failed"
    assert row["conviction"] is None


def test_outcome_long_profit():
    # entry 100, exit 110, path high 112 / low 99
    o = compute_outcome("BUY", entry_close=100.0, exit_close=110.0,
                        path_highs=[105.0, 112.0], path_lows=[99.0, 108.0])
    assert abs(o["realized_ret"] - 0.10) < 1e-9
    assert o["hit"] is True
    assert abs(o["mfe"] - 0.12) < 1e-9     # 112/100 - 1
    assert abs(o["mae"] - (-0.01)) < 1e-9  # 99/100 - 1
    assert o["exit_reason"] == "time"


def test_outcome_long_loss_is_not_hit():
    o = compute_outcome("MILD_BUY", entry_close=100.0, exit_close=95.0,
                        path_highs=[101.0], path_lows=[94.0])
    assert abs(o["realized_ret"] - (-0.05)) < 1e-9
    assert o["hit"] is False


def test_outcome_avoid_hit_when_price_falls():
    # SELL/avoid: "hit" means avoiding was correct, i.e. price fell.
    o = compute_outcome("SELL", entry_close=100.0, exit_close=90.0,
                        path_highs=[101.0], path_lows=[89.0])
    assert o["hit"] is True
    o2 = compute_outcome("MILD_SELL", entry_close=100.0, exit_close=105.0,
                         path_highs=[106.0], path_lows=[100.0])
    assert o2["hit"] is False


def test_outcome_hold_has_no_hit():
    o = compute_outcome("HOLD", entry_close=100.0, exit_close=101.0,
                        path_highs=[102.0], path_lows=[100.0])
    assert o["hit"] is None
    assert abs(o["realized_ret"] - 0.01) < 1e-9


def test_outcome_rejects_bad_entry():
    raised = False
    try:
        compute_outcome("BUY", entry_close=0.0, exit_close=100.0, path_highs=[], path_lows=[])
    except ValueError:
        raised = True
    assert raised is True


def test_outcome_empty_path_uses_realized():
    o = compute_outcome("BUY", entry_close=100.0, exit_close=103.0, path_highs=[], path_lows=[])
    assert abs(o["mfe"] - 0.03) < 1e-9
    assert abs(o["mae"] - 0.03) < 1e-9


def _row(entry_date, action, horizon, ret, hit):
    return {"entry_date": entry_date, "action": action,
            "horizon_days": horizon, "realized_ret": ret, "hit": hit}


def test_summary_hit_rate_and_curve():
    rows = [
        _row("2026-05-01", "BUY", 1, 0.02, True),
        _row("2026-05-01", "MILD_BUY", 1, -0.01, False),  # same day -> averaged
        _row("2026-05-02", "BUY", 1, 0.03, True),
        _row("2026-05-01", "BUY", 5, 0.05, True),
        _row("2026-05-01", "SELL", 1, -0.04, True),       # avoid hit, excluded from curve
    ]
    s = summarize_performance(rows, curve_horizon=1)

    # 1d long hit-rate: 3 long 1d rows (0.02 T, -0.01 F, 0.03 T) -> 2/3
    assert s["horizons"]["1"]["count"] == 3
    assert abs(s["horizons"]["1"]["hit_rate"] - (2.0 / 3.0)) < 1e-9

    # equity curve: day1 mean(0.02,-0.01)=0.005 ; day2 = 0.03
    curve = s["equity_curve"]
    assert [p["date"] for p in curve] == ["2026-05-01", "2026-05-02"]
    assert abs(curve[0]["equity"] - 1.005) < 1e-9
    assert abs(curve[1]["equity"] - 1.005 * 1.03) < 1e-9
    assert s["n_long_signals"] == 4   # BUY/MILD_BUY rows across all horizons


def test_summary_handles_empty():
    s = summarize_performance([], curve_horizon=1)
    assert s["n_long_signals"] == 0
    assert s["equity_curve"] == []
    assert s["horizons"]["5"]["count"] == 0
    assert s["horizons"]["5"]["hit_rate"] is None


ALL_TESTS = [
    test_event_id_is_stable_and_namespaced,
    test_prediction_row_for_ok_signal,
    test_prediction_row_is_none_when_prob_missing,
    test_signal_row_maps_core_fields,
    test_signal_row_for_failed_signal,
    test_outcome_long_profit,
    test_outcome_long_loss_is_not_hit,
    test_outcome_avoid_hit_when_price_falls,
    test_outcome_hold_has_no_hit,
    test_outcome_rejects_bad_entry,
    test_outcome_empty_path_uses_realized,
    test_summary_hit_rate_and_curve,
    test_summary_handles_empty,
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
