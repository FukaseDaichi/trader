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
    DEFAULT_MARKET_SERIES,
    MACRO_FEATURE_COLS,
    MACRO_LEVEL_COLS,
    add_macro_features,
    build_macro_panel,
    encode_market_bias,
    fetch_market_series,
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


def test_default_series_symbols_are_fetchable_or_disabled():
    """Issue #1 (2026-06-11): Stooq's CSV endpoint 404s for EVERY symbol, so a
    series is only alive through its yfinance fallback. A configured series
    without a yfinance symbol is guaranteed-dead config; series with no working
    source anywhere must be fully disabled (both sources None)."""
    for key, spec in DEFAULT_MARKET_SERIES.items():
        enabled = any(spec.get(src) for src in ("stooq", "yfinance"))
        if enabled:
            assert spec.get("yfinance"), (
                f"{key}: enabled series needs a yfinance fallback "
                "(Stooq alone is dead as of 2026-06-11)"
            )
    # TOPIX itself is unavailable (Yahoo ^TPX is an empty stub); the largest
    # TOPIX ETF (1306) is the documented benchmark proxy on both sources.
    assert DEFAULT_MARKET_SERIES["topix"]["yfinance"] == "1306.T"
    assert DEFAULT_MARKET_SERIES["topix"]["stooq"] == "1306.jp"
    # No working source exists for these (Yahoo: not listed; Stooq: endpoint
    # dead, symbols unverifiable) -> disabled, levels/features stay NaN.
    for key in ("nikkei_vi", "jgb10y"):
        assert DEFAULT_MARKET_SERIES[key]["stooq"] is None, key
        assert DEFAULT_MARKET_SERIES[key]["yfinance"] is None, key


class _FakeYFinance:
    """Stub injected as sys.modules['yfinance']; serves frames per period."""

    def __init__(self, by_period):
        self.by_period = by_period
        self.calls = []

    def download(self, symbol, period=None, **kwargs):
        self.calls.append(period)
        out = self.by_period.get(period)
        if isinstance(out, Exception):
            raise out
        return out


def _with_fake_yf(by_period, spec):
    """Run fetch_market_series with a stubbed yfinance + stooq guard."""
    import src.data_loader as dl

    fake = _FakeYFinance(by_period)
    orig_yf = sys.modules.get("yfinance")
    orig_stooq = dl.download_stooq_data

    def _no_stooq(symbol):
        raise AssertionError(f"stooq must not be called (symbol={symbol})")

    sys.modules["yfinance"] = fake
    dl.download_stooq_data = _no_stooq
    try:
        return fetch_market_series(spec), fake
    finally:
        dl.download_stooq_data = orig_stooq
        if orig_yf is not None:
            sys.modules["yfinance"] = orig_yf
        else:
            sys.modules.pop("yfinance", None)


def test_fetch_market_series_disabled_spec_touches_no_source():
    result, fake = _with_fake_yf({}, {"stooq": None, "yfinance": None})
    assert result is None
    assert fake.calls == []  # yfinance never consulted for a disabled series


def test_fetch_market_series_retries_bounded_period_when_max_empty():
    """yfinance period='max' breaks for some symbols (e.g. 1306.T returns
    empty / TypeError); the fetch must retry with a bounded period."""
    idx = pd.date_range("2026-01-01", periods=5, freq="D", name="Date")
    ok = pd.DataFrame({"Close": [1.0, 2.0, 3.0, 4.0, 5.0]}, index=idx)
    result, fake = _with_fake_yf(
        {"max": pd.DataFrame(), "10y": ok},
        {"stooq": None, "yfinance": "1306.T"},
    )
    assert fake.calls == ["max", "10y"]
    assert result is not None and len(result) == 5
    assert list(result.columns) == ["date", "close"]
    assert float(result["close"].iloc[-1]) == 5.0


def test_fetch_market_series_retries_bounded_period_when_max_raises():
    idx = pd.date_range("2026-01-01", periods=3, freq="D", name="Date")
    ok = pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=idx)
    result, fake = _with_fake_yf(
        {"max": TypeError("'NoneType' object is not subscriptable"), "10y": ok},
        {"stooq": None, "yfinance": "1306.T"},
    )
    assert fake.calls == ["max", "10y"]
    assert result is not None and len(result) == 3


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
    test_default_series_symbols_are_fetchable_or_disabled,
    test_fetch_market_series_disabled_spec_touches_no_source,
    test_fetch_market_series_retries_bounded_period_when_max_empty,
    test_fetch_market_series_retries_bounded_period_when_max_raises,
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
