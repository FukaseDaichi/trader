#!/usr/bin/env python3
"""
Unit tests for src/macro.py pure logic (panel build + as-of join, no network).

Runnable two ways:
  uv run python tests/test_macro_features.py     # standalone
  uv run pytest tests/test_macro_features.py      # if pytest is available
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.macro import (  # noqa: E402
    MACRO_FEATURE_COLS,
    MACRO_LEVEL_COLS,
    add_macro_features,
    build_macro_panel,
    encode_market_bias,
    latest_snapshot_row,
)
from src.model import FEATURE_COLS, build_feature_frame, phase1_feature_cols  # noqa: E402


def _series(start, n, step, key):
    dates = pd.date_range(start, periods=n, freq="D")
    return pd.DataFrame({"date": dates, "close": [100.0 + i * step for i in range(n)]}), key


def test_encode_market_bias():
    assert encode_market_bias("risk_on") == 1.0
    assert encode_market_bias("RISK_OFF") == -1.0
    assert encode_market_bias("neutral") == 0.0
    assert encode_market_bias(None) == 0.0
    assert encode_market_bias("???") == 0.0


def test_add_macro_features_none_panel_emits_nan_schema():
    stock = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=3, freq="D"),
        "close": [10.0, 11.0, 12.0],
    })
    out = add_macro_features(stock, None)
    assert len(out) == 3  # stock rows preserved
    for col in MACRO_FEATURE_COLS:
        assert col in out.columns
        assert out[col].isna().all()


def test_add_macro_features_backward_join_no_future_leak():
    macro_panel = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-05", "2026-01-10"]),
        "macro_topix_ret_20": [0.1, 0.2],
    })
    stock = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-03", "2026-01-05", "2026-01-07", "2026-01-12"]),
        "close": [1.0, 2.0, 3.0, 4.0],
    })
    out = add_macro_features(stock, macro_panel).set_index("date")
    # before first macro date -> NaN (no backfill from the future)
    assert np.isnan(out.loc["2026-01-03", "macro_topix_ret_20"])
    # on the macro date -> that value
    assert out.loc["2026-01-05", "macro_topix_ret_20"] == 0.1
    # between macro dates -> the EARLIER value (never the future 0.2)
    assert out.loc["2026-01-07", "macro_topix_ret_20"] == 0.1
    # after the last macro date -> last value
    assert out.loc["2026-01-12", "macro_topix_ret_20"] == 0.2
    # full schema present
    for col in MACRO_FEATURE_COLS:
        assert col in out.columns


def test_build_macro_panel_columns_and_returns():
    usd, _ = _series("2026-01-01", 70, 0.1, "usdjpy")
    top, _ = _series("2026-01-01", 70, 1.0, "topix")
    panel = build_macro_panel(
        {"usdjpy": usd, "topix": top},
        qualitative={"market_bias": "risk_on"},
    )
    for col in ["date"] + MACRO_LEVEL_COLS + MACRO_FEATURE_COLS:
        assert col in panel.columns, col
    # ret_20 at index 30 = close[30]/close[10] - 1 for topix (start 100, step 1.0)
    expected = (100.0 + 30) / (100.0 + 10) - 1.0
    assert abs(panel["macro_topix_ret_20"].iloc[30] - expected) < 1e-9
    # qualitative bias encoded as a constant auxiliary feature
    assert (panel["macro_bias_score"] == 1.0).all()
    # series we did not supply stay NaN (robust to missing series)
    assert panel["macro_nikkei_vi"].isna().all()


def test_build_macro_panel_empty_input():
    panel = build_macro_panel({})
    assert "date" in panel.columns
    assert len(panel) == 0
    for col in MACRO_FEATURE_COLS:
        assert col in panel.columns


def test_latest_snapshot_row():
    usd, _ = _series("2026-01-01", 30, 0.1, "usdjpy")
    panel = build_macro_panel({"usdjpy": usd}, qualitative={"market_bias": "neutral"})
    row = latest_snapshot_row(panel, qualitative={"market_bias": "neutral"})
    assert row["date"] == "2026-01-30"
    assert abs(row["usdjpy"] - (100.0 + 29 * 0.1)) < 1e-9
    assert row["topix"] is None  # not supplied
    assert row["market_bias"] == "neutral"
    assert latest_snapshot_row(pd.DataFrame()) is None


def test_phase1_feature_cols_respects_macro_flag():
    assert phase1_feature_cols(False) == FEATURE_COLS
    enabled = phase1_feature_cols(True)
    assert enabled[:len(FEATURE_COLS)] == FEATURE_COLS
    for col in MACRO_FEATURE_COLS:
        assert col in enabled


def test_build_feature_frame_macro_disabled_omits_macro_columns():
    dates = pd.date_range("2026-01-01", periods=90, freq="D")
    close = pd.Series([100.0 + i * 0.5 for i in range(90)])
    stock = pd.DataFrame({
        "date": dates,
        "open": close - 0.2,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": [1_000_000 + i * 1000 for i in range(90)],
    })
    macro_panel = pd.DataFrame({
        "date": dates,
        "macro_topix_ret_20": [0.1] * 90,
    })

    out = build_feature_frame(stock, macro_panel=macro_panel, macro_enabled=False)
    assert not out.empty
    for col in FEATURE_COLS:
        assert col in out.columns
    for col in MACRO_FEATURE_COLS:
        assert col not in out.columns


ALL_TESTS = [
    test_encode_market_bias,
    test_add_macro_features_none_panel_emits_nan_schema,
    test_add_macro_features_backward_join_no_future_leak,
    test_build_macro_panel_columns_and_returns,
    test_build_macro_panel_empty_input,
    test_latest_snapshot_row,
    test_phase1_feature_cols_respects_macro_flag,
    test_build_feature_frame_macro_disabled_omits_macro_columns,
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
