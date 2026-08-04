#!/usr/bin/env python3
"""
Unit tests for src/predictor.py exit-plan (ATR take-profit / stop-loss) logic.

PURE — no DB/network/file IO. Runnable as:
  uv run python tests/test_predictor.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import predictor  # noqa: E402


def _frame(close=1000.0, atr=30.0, volatility=0.02, with_atr=True):
    """One-row feature frame sufficient for generate_signal()."""
    row = {
        "date": pd.Timestamp("2026-07-13"),
        "close": close,
        "volatility": volatility,
    }
    if with_atr:
        row["atr"] = atr
    return pd.DataFrame([row])


_TICKER = {"code": "9999.JP", "name": "テスト"}
_LABEL_CFG = {"tb_tp_atr": 1.5, "tb_sl_atr": 1.0, "tb_max_days": 5}


# ---------------------------------------------------------------------------
# build_long_exit_plan
# ---------------------------------------------------------------------------


def test_exit_plan_prices_match_atr_widths():
    plan = predictor.build_long_exit_plan(1000.0, 30.0, _LABEL_CFG)
    assert plan["take_profit_price"] == 1045  # 1000 + 1.5*30
    assert plan["stop_price"] == 970  # 1000 - 1.0*30
    assert plan["time_exit_days"] == 5


def test_exit_plan_pct_signs():
    plan = predictor.build_long_exit_plan(1000.0, 30.0, _LABEL_CFG)
    assert plan["take_profit_pct"] > 0
    assert plan["stop_pct"] < 0


def test_exit_plan_uses_defaults_when_no_config():
    plan = predictor.build_long_exit_plan(1000.0, 30.0, None)
    # defaults 1.5 / 1.0
    assert plan["take_profit_price"] == 1045 and plan["stop_price"] == 970


def test_exit_plan_custom_widths():
    plan = predictor.build_long_exit_plan(
        1000.0, 30.0, {"tb_tp_atr": 2.0, "tb_sl_atr": 0.5, "tb_max_days": 10}
    )
    assert plan["take_profit_price"] == 1060  # +2.0*30
    assert plan["stop_price"] == 985  # -0.5*30
    assert plan["time_exit_days"] == 10


def test_exit_plan_none_on_missing_atr():
    assert predictor.build_long_exit_plan(1000.0, None, _LABEL_CFG) is None
    assert predictor.build_long_exit_plan(1000.0, float("nan"), _LABEL_CFG) is None


def test_exit_plan_none_on_nonfinite_input():
    assert predictor.build_long_exit_plan(float("inf"), 30.0, _LABEL_CFG) is None
    assert predictor.build_long_exit_plan(1000.0, float("inf"), _LABEL_CFG) is None
    assert predictor.build_long_exit_plan(1000.0, float("-inf"), _LABEL_CFG) is None


def test_exit_plan_none_on_nonpositive():
    assert predictor.build_long_exit_plan(0.0, 30.0, _LABEL_CFG) is None
    assert predictor.build_long_exit_plan(1000.0, 0.0, _LABEL_CFG) is None


def test_thresholds_reject_nonfinite_values():
    for key in predictor.DEFAULT_SIGNAL_THRESHOLDS:
        for value in (float("nan"), float("inf"), float("-inf")):
            try:
                predictor.resolve_thresholds({key: value})
            except ValueError:
                continue
            raise AssertionError(f"{key} accepted non-finite value {value}")


def test_exit_plan_returns_native_python_types():
    plan = predictor.build_long_exit_plan(1000.0, 30.0, _LABEL_CFG)
    for key in ("take_profit_price", "stop_price", "take_profit_pct", "stop_pct"):
        assert isinstance(plan[key], (int, float)), key
        # native (not numpy) so json.dump without a custom encoder works
        assert type(plan[key]).__module__ == "builtins", key


# ---------------------------------------------------------------------------
# generate_signal integration
# ---------------------------------------------------------------------------


def test_buy_signal_attaches_exit_plan():
    sig = predictor.generate_signal(
        _frame(), 0.90, _TICKER, thresholds=None, label_config=_LABEL_CFG
    )
    assert sig["action"] == "BUY"
    assert sig["exit_plan"] is not None
    assert sig["take_profit_price"] == 1045
    assert sig["stop_price"] == 970
    # DB stop_loss column now carries the ATR stop (was fixed 2%).
    assert sig["stop_loss"] == 970
    assert sig["time_exit_days"] == 5
    # entry limit still present for BUY
    assert sig["limit_price"] is not None


def test_mild_buy_attaches_exit_plan():
    sig = predictor.generate_signal(
        _frame(), 0.70, _TICKER, thresholds=None, label_config=_LABEL_CFG
    )
    assert sig["action"] == "MILD_BUY"
    assert sig["exit_plan"] is not None
    assert sig["take_profit_price"] == 1045


def test_sell_signal_has_no_exit_plan():
    sig = predictor.generate_signal(
        _frame(), 0.02, _TICKER, thresholds=None, label_config=_LABEL_CFG
    )
    assert sig["action"] == "SELL"
    assert sig["exit_plan"] is None
    assert sig["take_profit_price"] is None
    assert sig["stop_price"] is None


def test_hold_signal_has_no_exit_plan():
    sig = predictor.generate_signal(
        _frame(), 0.50, _TICKER, thresholds=None, label_config=_LABEL_CFG
    )
    assert sig["action"] == "HOLD"
    assert sig["exit_plan"] is None


def test_buy_without_atr_degrades_gracefully():
    # Missing ATR feature must not break signal generation.
    sig = predictor.generate_signal(
        _frame(with_atr=False), 0.90, _TICKER, thresholds=None, label_config=_LABEL_CFG
    )
    assert sig["action"] == "BUY"
    assert sig["exit_plan"] is None
    assert sig["take_profit_price"] is None
    # legacy limit still set; stop_loss falls back to None (no ATR)
    assert sig["limit_price"] is not None


# ---------------------------------------------------------------------------
# main._attach_confidence_fields cleanup of exit fields on forced HOLD
# ---------------------------------------------------------------------------


def test_gate_fail_clears_exit_fields():
    import main

    sig = predictor.generate_signal(
        _frame(), 0.90, _TICKER, thresholds=None, label_config=_LABEL_CFG
    )
    assert sig["exit_plan"] is not None  # precondition: BUY with plan
    out = main._attach_confidence_fields(
        sig, {"passed": False, "failures": ["sharpe"]}, model_ready=True
    )
    assert out["action"] == "HOLD"
    for key in (
        "limit_price",
        "stop_loss",
        "take_profit_price",
        "stop_price",
        "take_profit_pct",
        "stop_pct",
        "time_exit_days",
        "exit_plan",
    ):
        assert out[key] is None, key


def test_model_fail_clears_exit_fields():
    import main

    sig = predictor.generate_signal(
        _frame(), 0.90, _TICKER, thresholds=None, label_config=_LABEL_CFG
    )
    out = main._attach_confidence_fields(
        sig, {"passed": True, "failures": []}, model_ready=False
    )
    assert out["action"] == "HOLD" and out["status"] == "failed"
    assert out["exit_plan"] is None and out["take_profit_price"] is None
    assert out["stop_loss"] is None and out["time_exit_days"] is None


ALL_TESTS = [
    test_exit_plan_prices_match_atr_widths,
    test_exit_plan_pct_signs,
    test_exit_plan_uses_defaults_when_no_config,
    test_exit_plan_custom_widths,
    test_exit_plan_none_on_missing_atr,
    test_exit_plan_none_on_nonfinite_input,
    test_exit_plan_none_on_nonpositive,
    test_thresholds_reject_nonfinite_values,
    test_exit_plan_returns_native_python_types,
    test_buy_signal_attaches_exit_plan,
    test_mild_buy_attaches_exit_plan,
    test_sell_signal_has_no_exit_plan,
    test_hold_signal_has_no_exit_plan,
    test_buy_without_atr_degrades_gracefully,
    test_gate_fail_clears_exit_fields,
    test_model_fail_clears_exit_fields,
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
    print(
        f"\n{'OK' if not failures else 'FAILED'} "
        f"({len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed)"
    )
    return failures


if __name__ == "__main__":
    sys.exit(main())
