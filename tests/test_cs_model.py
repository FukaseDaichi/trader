#!/usr/bin/env python3
"""
Unit tests for src/cs_model.py (cross-sectional train / predict / calibration /
metrics). Pure ML logic — NO database or network.

Runnable two ways:
  TRADER_DB_ENABLED=false uv run python tests/test_cs_model.py   # standalone
  uv run pytest tests/test_cs_model.py                           # if pytest is present

These tests BYPASS build_cs_panel and feed a hand-built labelled panel that
matches its schema (date, ticker, sector, the cross-sectional feature cols, and
the labels fwd_return / target_vol_norm / target_up / target_rank_bucket). This
is more controllable for unit tests: we plant a learnable signal by making the
forward return a monotonic function of one feature column plus small noise, so
the model has something real to learn and the ranking metrics are meaningful.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import cs_model as cm  # noqa: E402
from src.cross_section import cross_sectional_feature_cols  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic labelled-panel builder (matches build_cs_panel schema)
# ---------------------------------------------------------------------------


def _make_planted_panel(
    n_tickers: int = 30,
    n_dates: int = 160,
    signal_feature: str = "cs_z_return_20d",
    noise: float = 0.4,
    macro_enabled: bool = False,
    seed: int = 7,
) -> pd.DataFrame:
    """Hand-build a labelled CS panel with a planted signal.

    The forward return on each date is a monotonic (linear) function of one
    cross-sectional feature plus Gaussian noise, so a competent model recovers
    a positive IC. All other feature columns are filled with independent noise.
    Labels are derived consistently with cross_section.build_cs_labels:
      - fwd_return        : signal * f + noise
      - target_up         : 1.0 if fwd_return > 0 else 0.0
      - target_vol_norm   : fwd_return / vol
      - target_rank_bucket: within-date floor(pct_rank * 5) clipped 0..4
    """
    rng = np.random.default_rng(seed)
    feature_cols = cross_sectional_feature_cols(macro_enabled=macro_enabled)
    dates = pd.bdate_range("2024-01-01", periods=n_dates)
    tickers = [f"{1000 + i}.JP" for i in range(n_tickers)]
    sectors = [f"SEC{i % 5}" for i in range(n_tickers)]
    sector_map = dict(zip(tickers, sectors))

    rows = []
    for d in dates:
        # The signal feature is a standardized cross-sectional score.
        sig = rng.normal(size=n_tickers)
        fwd = 0.02 * sig + noise * 0.02 * rng.normal(size=n_tickers)
        for i, tk in enumerate(tickers):
            row = {"date": d, "ticker": tk, "sector": sector_map[tk]}
            for c in feature_cols:
                row[c] = float(sig[i]) if c == signal_feature else float(rng.normal())
            row["fwd_return"] = float(fwd[i])
            rows.append(row)

    panel = pd.DataFrame(rows)

    # Labels derived from fwd_return (per-date for the bucket).
    panel["target_up"] = (panel["fwd_return"] > 0).astype("float64")
    panel["target_vol_norm"] = panel["fwd_return"] / 0.02  # constant synthetic vol

    bucket = pd.Series(np.nan, index=panel.index, dtype="float64")
    for _d, grp in panel.groupby("date"):
        rp = grp["fwd_return"].rank(pct=True, method="first")
        b = (rp * 5).apply(math.floor).clip(0, 4).astype("float64")
        bucket.loc[b.index] = b
    panel["target_rank_bucket"] = bucket

    return panel.sort_values(["date", "ticker"]).reset_index(drop=True)


def _small_group_panel(
    n_dates: int = 160, names_per_date: int = 3, seed: int = 3
) -> pd.DataFrame:
    """A panel with tiny daily cross-sections (forces ranker -> regression)."""
    return _make_planted_panel(
        n_tickers=names_per_date, n_dates=n_dates, macro_enabled=False, seed=seed
    )


# Config override: tiny min_daily_names so the synthetic panel passes the gate,
# and a short val window so 3 folds fit comfortably in ~160 dates.
def _cfg(**overrides) -> dict:
    base = {
        "objective": "ranker",
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


def test_train_ranker_returns_bundle():
    panel = _make_planted_panel()
    bundle, info = cm.train_cs_model(
        panel, config=_cfg(objective="ranker"), macro_enabled=False, seed=42
    )
    assert bundle is not None, f"expected a bundle, info={info}"
    assert info["objective"] == "ranker", info
    assert bundle["booster"] is not None
    assert not bundle["oos_predictions"].empty

    ic = bundle["metrics"]["daily_ic"]
    assert ic is not None and np.isfinite(ic), f"daily_ic not finite: {ic}"
    assert ic > 0, f"expected positive IC on planted signal, got {ic}"
    prec = bundle["metrics"]["precision_at_n"]
    assert prec is not None and prec >= 0.5, f"precision_at_n={prec}"


def test_train_regression_returns_bundle():
    panel = _make_planted_panel()
    bundle, info = cm.train_cs_model(
        panel, config=_cfg(objective="regression"), macro_enabled=False, seed=42
    )
    assert bundle is not None, f"expected a bundle, info={info}"
    assert info["objective"] == "regression"
    m = bundle["metrics"]
    assert m["daily_ic"] is not None and np.isfinite(m["daily_ic"])
    assert m["topn_realized_return"] is not None and np.isfinite(
        m["topn_realized_return"]
    )


def test_predict_shape_and_rank():
    panel = _make_planted_panel()
    bundle, info = cm.train_cs_model(
        panel, config=_cfg(objective="ranker"), macro_enabled=False, seed=42
    )
    assert bundle is not None, info

    # Latest cross-section: drop labels to mimic inference input.
    latest = panel[panel["date"] == panel["date"].max()].drop(
        columns=["fwd_return", "target_up", "target_vol_norm", "target_rank_bucket"]
    )
    preds = cm.predict_cs_model(bundle, latest)

    n = latest["ticker"].nunique()
    assert len(preds) == n, f"expected {n} rows, got {len(preds)}"
    assert list(preds.columns) == [
        "ticker",
        "raw_score",
        "cs_rank",
        "score_pct",
        "prob_up",
        "expected_ret",
    ]
    # cs_rank is a permutation of 1..N
    assert sorted(preds["cs_rank"].tolist()) == list(range(1, n + 1))
    # prob_up in [0, 1]
    assert preds["prob_up"].between(0.0, 1.0).all()
    # sorted by cs_rank ascending
    assert preds["cs_rank"].is_monotonic_increasing


def test_ranker_fallback_to_regression():
    panel = _small_group_panel(names_per_date=3)
    bundle, info = cm.train_cs_model(
        panel, config=_cfg(objective="ranker"), macro_enabled=False, seed=42
    )
    assert bundle is not None, info
    assert bundle["objective"] == "regression", bundle["objective"]
    assert info["fallback_reason"], "expected a fallback_reason to be recorded"
    assert "ranker_groups_too_small" in info["fallback_reason"]


def test_fit_and_apply_calibration_monotonic():
    panel = _make_planted_panel()
    bundle, info = cm.train_cs_model(
        panel, config=_cfg(objective="ranker"), macro_enabled=False, seed=42
    )
    assert bundle is not None, info
    cal = bundle["calibration"]
    assert cal["applied"], cal

    # apply over the bucket centres -> prob in [0,1], and an overall up trend.
    centres = [
        (cal["edges"][i] + cal["edges"][i + 1]) / 2.0 for i in range(cal["n_buckets"])
    ]
    probs = [cm.apply_score_calibration(cal, c)[0] for c in centres]
    assert all(0.0 <= p <= 1.0 for p in probs), probs
    # Monotone non-decreasing TREND overall: top bucket prob > bottom bucket prob.
    assert probs[-1] > probs[0], f"expected up-trend in calibrated prob: {probs}"

    # None calibration -> safe defaults.
    assert cm.apply_score_calibration(None, 0.5) == (0.5, 0.0)


def test_cs_metrics_perfect_ranking():
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2024-01-01", periods=10)
    rows = []
    for d in dates:
        ret = rng.normal(size=12)
        for i in range(12):
            rows.append(
                {
                    "date": d,
                    "ticker": f"{i}.JP",
                    "raw_score": float(ret[i]),  # score == fwd_return exactly
                    "fwd_return": float(ret[i]),
                    "target_up": 1.0 if ret[i] > 0 else 0.0,
                    "target_vol_norm": float(ret[i]),
                    "target_rank_bucket": 0.0,
                }
            )
    oos = pd.DataFrame(rows)
    m = cm.cs_metrics(oos, top_n=4)
    assert m["daily_ic"] is not None and abs(m["daily_ic"] - 1.0) < 1e-6, m["daily_ic"]
    assert m["rank_ic"] is not None and abs(m["rank_ic"] - 1.0) < 1e-6, m["rank_ic"]
    assert m["precision_at_n"] is not None and m["precision_at_n"] >= 0.5
    assert m["top_bottom_spread"] is not None and m["top_bottom_spread"] > 0
    assert m["turnover"] is not None and np.isfinite(m["turnover"])


def test_insufficient_panel_returns_none():
    # 5 tickers x 4 dates => far below train_min_rows and fold-date requirement.
    panel = _make_planted_panel(n_tickers=5, n_dates=4, macro_enabled=False)
    bundle, info = cm.train_cs_model(
        panel, config=_cfg(objective="ranker"), macro_enabled=False, seed=42
    )
    assert bundle is None
    assert "reason" in info and info["reason"]


def test_determinism():
    panel = _make_planted_panel()
    b1, _ = cm.train_cs_model(
        panel, config=_cfg(objective="ranker"), macro_enabled=False, seed=42
    )
    b2, _ = cm.train_cs_model(
        panel, config=_cfg(objective="ranker"), macro_enabled=False, seed=42
    )
    assert b1 is not None and b2 is not None
    assert b1["metrics"]["daily_ic"] == b2["metrics"]["daily_ic"], (
        b1["metrics"]["daily_ic"],
        b2["metrics"]["daily_ic"],
    )


ALL_TESTS = [
    test_train_ranker_returns_bundle,
    test_train_regression_returns_bundle,
    test_predict_shape_and_rank,
    test_ranker_fallback_to_regression,
    test_fit_and_apply_calibration_monotonic,
    test_cs_metrics_perfect_ranking,
    test_insufficient_panel_returns_none,
    test_determinism,
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
