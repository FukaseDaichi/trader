#!/usr/bin/env python3
"""
Unit tests for cs_model.infer_cross_section (Task 5).

Runnable two ways:
  TRADER_DB_ENABLED=false uv run python tests/test_cs_inference.py
  uv run pytest tests/test_cs_inference.py

Strategy
--------
We build a SYNTHETIC tickers_data list (one (ticker_info, ohlcv_df) per ticker)
that build_cs_panel can process through build_ticker_feature_frame. The OHLCV
frames must be real enough for build_feature_frame to produce a non-empty result:
at least ~60 rows with close/open/high/low/volume columns.

Rather than replicating the full build_cs_panel path in the test, we also test
infer_cross_section in isolation by calling predict_cs_model directly on a
hand-built panel (mirroring test_cs_model.py). Both cases are covered:
1. test_infer_cs_end_to_end: exercises infer_cross_section with real
   synthetic OHLCV so build_cs_panel actually runs.
2. test_infer_cs_predict_path: feeds a pre-built panel to predict_cs_model
   directly (already tested in test_cs_model.py, here acts as a smoke check
   that the return contract is correct).
3. test_infer_cs_empty_tickers_data: guard against empty input.
4. test_infer_cs_none_bundle: guard against missing bundle.
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import cs_model as cm  # noqa: E402
from src import model_store  # noqa: E402
from src.cross_section import cross_sectional_feature_cols  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic data builders
# ---------------------------------------------------------------------------

def _make_ohlcv(n_rows: int = 250, seed: int = 0) -> pd.DataFrame:
    """
    Build a minimal OHLCV DataFrame that build_feature_frame accepts.

    Columns: date (pd.Timestamp), open, high, low, close, volume.
    Prices are a random walk (always positive). Dates are business days.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-01", periods=n_rows)
    log_ret = rng.normal(0.0, 0.01, size=n_rows)
    close = 1000.0 * np.exp(np.cumsum(log_ret))
    noise = rng.uniform(0.98, 1.02, size=n_rows)
    high = close * noise
    low = close / noise
    open_ = close * rng.uniform(0.99, 1.01, size=n_rows)
    volume = rng.integers(100_000, 1_000_000, size=n_rows).astype(float)
    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def _make_ticker_info(code: str, sector: str = "SEC0") -> dict:
    return {"code": code, "name": f"Corp {code}", "enabled": True, "sector": sector}


def _make_tickers_data(n_tickers: int = 35, n_rows: int = 250) -> list:
    """Build a list of (ticker_info, ohlcv_df) tuples for n_tickers."""
    tickers = [f"{2000 + i}.JP" for i in range(n_tickers)]
    sectors = [f"SEC{i % 5}" for i in range(n_tickers)]
    result = []
    for i, (code, sector) in enumerate(zip(tickers, sectors)):
        info = _make_ticker_info(code, sector)
        df = _make_ohlcv(n_rows=n_rows, seed=i)
        result.append((info, df))
    return result


# ---------------------------------------------------------------------------
# Small planted panel for predict_cs_model smoke check
# ---------------------------------------------------------------------------

def _make_planted_panel(
    n_tickers: int = 30,
    n_dates: int = 160,
    signal_feature: str = "cs_z_return_20d",
    noise: float = 0.4,
    macro_enabled: bool = False,
    seed: int = 7,
) -> pd.DataFrame:
    """Hand-build a labelled CS panel (mirrors test_cs_model._make_planted_panel)."""
    rng = np.random.default_rng(seed)
    feature_cols = cross_sectional_feature_cols(macro_enabled=macro_enabled)
    dates = pd.bdate_range("2024-01-01", periods=n_dates)
    tickers = [f"{3000 + i}.JP" for i in range(n_tickers)]
    sectors = [f"SEC{i % 5}" for i in range(n_tickers)]
    sector_map = dict(zip(tickers, sectors))

    rows = []
    for d in dates:
        sig = rng.normal(size=n_tickers)
        fwd = 0.02 * sig + noise * 0.02 * rng.normal(size=n_tickers)
        for i, tk in enumerate(tickers):
            row = {"date": d, "ticker": tk, "sector": sector_map[tk]}
            for c in feature_cols:
                row[c] = float(sig[i]) if c == signal_feature else float(rng.normal())
            row["fwd_return"] = float(fwd[i])
            rows.append(row)

    panel = pd.DataFrame(rows)
    panel["target_up"] = (panel["fwd_return"] > 0).astype("float64")
    panel["target_vol_norm"] = panel["fwd_return"] / 0.02

    bucket = pd.Series(np.nan, index=panel.index, dtype="float64")
    for _d, grp in panel.groupby("date"):
        rp = grp["fwd_return"].rank(pct=True, method="first")
        b = (rp * 5).apply(math.floor).clip(0, 4).astype("float64")
        bucket.loc[b.index] = b
    panel["target_rank_bucket"] = bucket

    return panel.sort_values(["date", "ticker"]).reset_index(drop=True)


def _cfg(**overrides) -> dict:
    base = {
        "objective": "regression",   # regression for stability in small tests
        "top_n": 5,
        "label_horizon_days": 5,
        "min_daily_names": 2,
        "panel_lookback_years": 10,
        "val_size": 25,
        "purge_gap": 5,
        "n_folds": 3,
        "train_min_rows": 200,
        "validation_years": 4,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_infer_cs_predict_path():
    """
    Smoke-check the predict_cs_model return contract (shape / cs_rank permutation).
    This test is fast: it bypasses build_cs_panel and feeds a hand-built panel.
    """
    panel = _make_planted_panel()
    bundle, info = cm.train_cs_model(panel, config=_cfg(), macro_enabled=False, seed=42)
    assert bundle is not None, f"training failed: {info}"

    latest = panel[panel["date"] == panel["date"].max()].drop(
        columns=["fwd_return", "target_up", "target_vol_norm", "target_rank_bucket"],
        errors="ignore",
    )
    pred = cm.predict_cs_model(bundle, latest)

    n = latest["ticker"].nunique()
    assert len(pred) == n, f"expected {n} rows, got {len(pred)}"
    assert list(pred.columns) == ["ticker", "raw_score", "cs_rank", "score_pct", "prob_up", "expected_ret"]
    assert sorted(pred["cs_rank"].tolist()) == list(range(1, n + 1)), "cs_rank must be a permutation of 1..N"
    assert pred["prob_up"].between(0.0, 1.0).all(), "prob_up must be in [0,1]"
    assert pred["cs_rank"].is_monotonic_increasing, "output should be sorted by cs_rank"


def test_infer_cs_end_to_end():
    """
    End-to-end test of infer_cross_section with real synthetic OHLCV data.

    We first train a bundle from a hand-built panel (no network), then call
    infer_cross_section with real synthetic OHLCV tickers_data.  The function
    must return pred_df with one row per ticker and cs_rank a permutation of
    1..N, plus a valid as_of Timestamp.
    """
    # Step 1: train a bundle on a hand-built panel (no build_cs_panel needed).
    panel = _make_planted_panel(n_tickers=30, n_dates=160, macro_enabled=False, seed=11)
    bundle, info = cm.train_cs_model(panel, config=_cfg(), macro_enabled=False, seed=42)
    assert bundle is not None, f"training failed: {info}"

    # Step 2: build synthetic tickers_data (35 tickers, 250 rows each).
    n_tickers = 35
    tickers_data = _make_tickers_data(n_tickers=n_tickers, n_rows=250)

    # Step 3: call infer_cross_section (macro_panel=None).
    pred_df, as_of = cm.infer_cross_section(
        tickers_data,
        macro_panel=None,
        bundle=bundle,
        macro_enabled=False,
        label_horizon_days=5,
    )

    # The panel will have at most n_tickers rows on the latest date; some may
    # fail feature engineering, but at least min_universe tickers should pass.
    # We just assert that pred_df is non-empty and the cs_rank contract holds.
    assert pred_df is not None and not pred_df.empty, "expected non-empty predictions"

    n = len(pred_df)
    assert n >= 1, "expected at least 1 ticker in pred_df"
    # cs_rank must be a permutation of 1..N (contiguous integers starting at 1).
    assert sorted(pred_df["cs_rank"].tolist()) == list(range(1, n + 1)), (
        f"cs_rank is not a permutation of 1..{n}: {pred_df['cs_rank'].tolist()}"
    )
    assert pred_df["prob_up"].between(0.0, 1.0).all(), "prob_up must be in [0,1]"
    assert as_of is not None, "as_of_date must not be None on success"
    assert isinstance(as_of, pd.Timestamp), f"as_of must be pd.Timestamp, got {type(as_of)}"


def test_infer_cs_after_saved_bundle_reload():
    """
    Regression: a saved+loaded CS bundle must retain feature_cols so daily
    inference scores with the same feature schema used in training.
    """
    panel = _make_planted_panel(n_tickers=30, n_dates=160, macro_enabled=False, seed=17)
    bundle, info = cm.train_cs_model(panel, config=_cfg(), macro_enabled=False, seed=42)
    assert bundle is not None, f"training failed: {info}"

    with tempfile.TemporaryDirectory() as tmp:
        version = "cs-v1-test"
        model_store.save_cs_bundle(
            version,
            bundle["booster"],
            feature_schema={
                "feature_cols": bundle["feature_cols"],
                "objective": bundle["objective"],
                "macro_enabled": bundle["macro_enabled"],
            },
            calibration=bundle["calibration"],
            feature_reference=bundle["feature_reference"],
            sector_encoder=bundle["sector_encoder"],
            universe=bundle["universe"],
            oos_predictions=bundle["oos_predictions"],
            model_dir=tmp,
        )
        loaded = model_store.load_cs_bundle(version, model_dir=tmp)

    assert loaded is not None
    assert loaded["feature_cols"] == bundle["feature_cols"]

    pred_df, as_of = cm.infer_cross_section(
        _make_tickers_data(n_tickers=35, n_rows=250),
        macro_panel=None,
        bundle=loaded,
        macro_enabled=False,
        label_horizon_days=5,
    )

    assert pred_df is not None and not pred_df.empty, "expected loaded bundle to score"
    assert sorted(pred_df["cs_rank"].tolist()) == list(range(1, len(pred_df) + 1))
    assert as_of is not None


def test_predict_cs_uses_latest_wide_cross_section():
    """
    If only a few tickers have a newer vendor date, score the latest date with
    enough names instead of ranking a two-name cross-section.
    """
    panel = _make_planted_panel(n_tickers=30, n_dates=160, macro_enabled=False, seed=23)
    bundle, info = cm.train_cs_model(panel, config=_cfg(), macro_enabled=False, seed=42)
    assert bundle is not None, f"training failed: {info}"

    score_panel = panel.drop(
        columns=["fwd_return", "target_up", "target_vol_norm", "target_rank_bucket"],
        errors="ignore",
    )
    latest_date = score_panel["date"].max()
    thin_next_date = latest_date + pd.offsets.BDay(1)
    thin_rows = score_panel[score_panel["date"] == latest_date].head(2).copy()
    thin_rows["date"] = thin_next_date
    mixed = pd.concat([score_panel, thin_rows], ignore_index=True)

    assert cm._latest_scorable_date(mixed) == latest_date

    pred = cm.predict_cs_model(bundle, mixed)

    assert len(pred) == 30, f"expected the latest wide cross-section, got {len(pred)}"
    assert sorted(pred["cs_rank"].tolist()) == list(range(1, 31))


def test_infer_cs_empty_tickers_data():
    """Empty tickers_data returns (empty DataFrame, None)."""
    panel = _make_planted_panel()
    bundle, info = cm.train_cs_model(panel, config=_cfg(), macro_enabled=False, seed=42)
    assert bundle is not None, info

    pred_df, as_of = cm.infer_cross_section([], macro_panel=None, bundle=bundle, macro_enabled=False)

    assert pred_df is not None and pred_df.empty, "expected empty DataFrame"
    assert as_of is None, "expected as_of to be None"


def test_infer_cs_none_bundle():
    """None bundle returns (empty DataFrame, None) without raising."""
    tickers_data = _make_tickers_data(n_tickers=5, n_rows=120)
    pred_df, as_of = cm.infer_cross_section(tickers_data, macro_panel=None, bundle=None,
                                            macro_enabled=False)
    assert pred_df is not None and pred_df.empty, "expected empty DataFrame for None bundle"
    assert as_of is None


ALL_TESTS = [
    test_infer_cs_predict_path,
    test_infer_cs_end_to_end,
    test_infer_cs_after_saved_bundle_reload,
    test_predict_cs_uses_latest_wide_cross_section,
    test_infer_cs_empty_tickers_data,
    test_infer_cs_none_bundle,
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
