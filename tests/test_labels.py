#!/usr/bin/env python3
"""
Unit tests for src/labels.py (pure logic, no DB / no network).

Runnable two ways:
  uv run python tests/test_labels.py     # standalone
  uv run pytest tests/test_labels.py      # if pytest is available
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.labels import (  # noqa: E402
    add_forward_return_labels,
    add_triple_barrier_labels,
    add_vol_normalized_labels,
    build_labelled_frame,
    target_kind,
)


def _frame(closes, highs=None, lows=None, atr=None, volatility=None):
    n = len(closes)
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    highs = highs if highs is not None else [c * 1.01 for c in closes]
    lows = lows if lows is not None else [c * 0.99 for c in closes]
    df = pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1000] * n,
        }
    )
    if atr is not None:
        df["atr"] = atr
    if volatility is not None:
        df["volatility"] = volatility
    return df


def test_forward_return_and_last_rows_unknown():
    closes = [100, 101, 102, 103, 104, 105, 106]
    df = add_forward_return_labels(_frame(closes), horizon_days=2)
    # fwd_return[0] = 102/100 - 1
    assert abs(df["fwd_return"].iloc[0] - 0.02) < 1e-9
    assert df["target_class"].iloc[0] == 1.0
    # last 2 rows have no 2-day forward -> NaN target
    assert np.isnan(df["fwd_return"].iloc[-1])
    assert np.isnan(df["target_class"].iloc[-1])
    assert np.isnan(df["target_class"].iloc[-2])


def test_forward_return_no_future_leakage():
    # target_class[i] must equal sign(close[i+h] > close[i]) using only forward data.
    closes = [100, 99, 101, 100, 102, 98, 105, 103]
    h = 3
    df = add_forward_return_labels(_frame(closes), horizon_days=h)
    for i in range(len(closes) - h):
        expected = 1.0 if closes[i + h] > closes[i] else 0.0
        assert df["target_class"].iloc[i] == expected, i


def test_build_binary_1d_reproduces_legacy():
    closes = [100, 101, 100.5, 102, 101]
    out = build_labelled_frame(_frame(closes), {"label_mode": "binary_1d"})
    # legacy target = (close.shift(-1) > close); last row dropped
    assert len(out) == len(closes) - 1
    legacy = [1, 0, 1, 0]  # 101>100, 100.5<101, 102>100.5, 101<102
    assert out["target_class"].tolist() == legacy
    assert out["target_class"].dtype.kind in ("i", "u")


def test_build_horizon_5_drops_last_5():
    closes = list(range(100, 120))  # 20 strictly increasing rows
    out = build_labelled_frame(
        _frame(closes, atr=[1.0] * 20),
        {
            "label_mode": "triple_barrier",
            "horizon_days": 5,
            "tb_max_days": 5,
            "tb_tp_atr": 1.5,
            "tb_sl_atr": 1.0,
        },
    )
    # 20 rows, horizon 5 -> at most 15 labelled rows
    assert len(out) == 15


def test_triple_barrier_tp_first():
    # entry=100, atr=1 -> tp=101.5, sl=99. Next bar touches TP only.
    df = _frame(
        closes=[100, 101, 101, 101],
        highs=[100, 101.6, 101, 101],
        lows=[100, 99.6, 100, 100],
        atr=[1.0, 1.0, 1.0, 1.0],
    )
    out = add_triple_barrier_labels(df, horizon_days=3, tp_atr=1.5, sl_atr=1.0)
    assert out["target_class"].iloc[0] == 1.0
    assert out["tb_exit_reason"].iloc[0] == "tp"


def test_triple_barrier_sl_first_priority():
    # entry=100, atr=1 -> tp=101.5, sl=99. i1 touches SL (not TP); i2 spikes high
    # but SL was hit first, so label must be 0.
    df = _frame(
        closes=[100, 100, 100, 100],
        highs=[100, 101.0, 200.0, 100],
        lows=[100, 98.5, 100, 100],
        atr=[1.0, 1.0, 1.0, 1.0],
    )
    out = add_triple_barrier_labels(df, horizon_days=3, tp_atr=1.5, sl_atr=1.0)
    assert out["target_class"].iloc[0] == 0.0
    assert out["tb_exit_reason"].iloc[0] == "sl"


def test_triple_barrier_same_bar_is_conservative_sl():
    # i1 bar touches BOTH tp and sl -> conservative SL (0).
    df = _frame(
        closes=[100, 100, 100],
        highs=[100, 102.0, 100],
        lows=[100, 98.0, 100],
        atr=[1.0, 1.0, 1.0],
    )
    out = add_triple_barrier_labels(df, horizon_days=2, tp_atr=1.5, sl_atr=1.0)
    assert out["target_class"].iloc[0] == 0.0
    assert out["tb_exit_reason"].iloc[0] == "sl"


def test_triple_barrier_time_exit():
    # Wide ATR so no barrier touched; time exit on sign of close[i+H].
    df = _frame(
        closes=[100, 101, 103],
        highs=[100, 101.5, 103.5],
        lows=[100, 100.5, 102.5],
        atr=[5.0, 5.0, 5.0],
    )
    out = add_triple_barrier_labels(df, horizon_days=2, tp_atr=1.5, sl_atr=1.0)
    assert out["target_class"].iloc[0] == 1.0  # 103 > 100
    assert out["tb_exit_reason"].iloc[0] == "time"


def test_triple_barrier_missing_atr_is_nan():
    df = _frame(closes=[100, 101, 102], atr=[np.nan, np.nan, np.nan])
    out = add_triple_barrier_labels(df, horizon_days=2)
    assert np.isnan(out["target_class"].iloc[0])


def test_vol_normalized_target():
    closes = [100, 110, 110]  # fwd_return[0] = 0.10 at h=1 -> but use h=1
    df = _frame(closes, volatility=[0.05, 0.05, 0.05])
    out = add_vol_normalized_labels(df, horizon_days=1, vol_col="volatility")
    # target = fwd_return / vol = 0.10 / 0.05 = 2.0
    assert abs(out["target"].iloc[0] - 2.0) < 1e-9
    assert out["target_class"].iloc[0] == 1.0  # sign of forward return
    assert target_kind("vol_norm") == "regression"
    assert target_kind("triple_barrier") == "binary"


def test_vol_normalized_zero_vol_is_nan():
    df = _frame([100, 110], volatility=[0.0, 0.0])
    out = add_vol_normalized_labels(df, horizon_days=1)
    assert np.isnan(out["target"].iloc[0])


def test_build_unknown_mode_raises():
    raised = False
    try:
        build_labelled_frame(_frame([100, 101, 102]), {"label_mode": "nope"})
    except ValueError:
        raised = True
    assert raised is True


def test_build_returns_int_target_class():
    out = build_labelled_frame(
        _frame(list(range(100, 110)), atr=[1.0] * 10),
        {"label_mode": "triple_barrier", "horizon_days": 3, "tb_max_days": 3},
    )
    assert out["target_class"].dtype.kind in ("i", "u")
    assert set(out["target_class"].unique()) <= {0, 1}


ALL_TESTS = [
    test_forward_return_and_last_rows_unknown,
    test_forward_return_no_future_leakage,
    test_build_binary_1d_reproduces_legacy,
    test_build_horizon_5_drops_last_5,
    test_triple_barrier_tp_first,
    test_triple_barrier_sl_first_priority,
    test_triple_barrier_same_bar_is_conservative_sl,
    test_triple_barrier_time_exit,
    test_triple_barrier_missing_atr_is_nan,
    test_vol_normalized_target,
    test_vol_normalized_zero_vol_is_nan,
    test_build_unknown_mode_raises,
    test_build_returns_int_target_class,
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
