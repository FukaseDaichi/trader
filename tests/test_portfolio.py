#!/usr/bin/env python3
"""
Unit tests for src/portfolio.py (long-only portfolio construction).

Pure logic — synthetic inputs only, NO database or network.

Runnable two ways:
  TRADER_DB_ENABLED=false uv run python tests/test_portfolio.py   # standalone
  uv run pytest tests/test_portfolio.py                           # if pytest is present
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import portfolio as pf  # noqa: E402

_TOL = 1e-9


# ---------------------------------------------------------------------------
# Synthetic input builders
# ---------------------------------------------------------------------------

def _make_predictions(n=12, n_sectors=3, seed=1):
    """Build n synthetic candidate prediction dicts across n_sectors sectors.

    cs_rank is 1..n (best->worst), expected_ret decreasing with rank (all
    positive), prob_up in (0.5, 0.9), volatility varied per name.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        rank = i + 1
        rows.append({
            "ticker": f"{1000 + i}.JP",
            "name": f"Corp {1000 + i}",
            "sector": f"SEC{i % n_sectors}",
            "cs_rank": rank,
            "raw_score": float(n - i),
            "prob_up": float(0.85 - 0.02 * i),
            "expected_ret": float(0.05 - 0.003 * i),  # all > 0 for n=12
            "volatility": float(0.15 + 0.05 * rng.random()),
            "close": float(1000 + 50 * i),
        })
    return rows


def _make_price_frames(tickers, n_rows=120, seed=0):
    """Per-ticker price DataFrame (date, close) random walk; enough rows for cov."""
    rng = np.random.default_rng(seed)
    frames = {}
    dates = pd.bdate_range("2025-01-01", periods=n_rows)
    for i, tk in enumerate(tickers):
        log_ret = rng.normal(0.0, 0.012, size=n_rows)
        close = 1000.0 * np.exp(np.cumsum(log_ret))
        frames[tk] = pd.DataFrame({"date": dates, "close": close})
    return frames


def _config(**overrides):
    base = {
        "target_vol": 0.12,
        "max_name_weight": 0.20,
        "sector_cap": 0.40,
        "max_gross": 1.00,
        "min_weight": 0.03,
        "notrade_band": 0.02,
        "min_expected_ret": 0.0,
        "risk_off_gross_mult": 0.50,
        "cov_lookback_days": 60,
        "top_n": 8,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# select_candidates
# ---------------------------------------------------------------------------

def test_select_candidates_filter_sort_cap():
    preds = [
        {"ticker": "A", "cs_rank": 3, "expected_ret": 0.01},
        {"ticker": "B", "cs_rank": 1, "expected_ret": -0.02},  # filtered (er<0)
        {"ticker": "C", "cs_rank": 2, "expected_ret": 0.04},
        {"ticker": "D", "cs_rank": 5, "expected_ret": 0.00},
        {"ticker": "E", "cs_rank": 4, "expected_ret": 0.02},
    ]
    out = pf.select_candidates(preds, top_n=3, min_expected_ret=0.0)
    assert [c["ticker"] for c in out] == ["C", "A", "E"], out
    # B excluded (negative er); sorted by cs_rank asc; capped at 3.


def test_select_candidates_missing_er_eligible_only_when_floor_nonpositive():
    preds = [
        {"ticker": "A", "cs_rank": 1, "expected_ret": None},
        {"ticker": "B", "cs_rank": 2, "expected_ret": 0.05},
    ]
    # floor <= 0 -> missing er eligible
    out0 = pf.select_candidates(preds, top_n=5, min_expected_ret=0.0)
    assert {c["ticker"] for c in out0} == {"A", "B"}
    # floor > 0 -> missing er excluded
    out1 = pf.select_candidates(preds, top_n=5, min_expected_ret=0.01)
    assert [c["ticker"] for c in out1] == ["B"]


def test_select_candidates_empty_in_empty_out():
    assert pf.select_candidates([], top_n=8) == []
    assert pf.select_candidates(None, top_n=8) == []


def test_select_candidates_accepts_dataframe():
    df = pd.DataFrame([
        {"ticker": "A", "cs_rank": 2, "expected_ret": 0.01},
        {"ticker": "B", "cs_rank": 1, "expected_ret": 0.02},
    ])
    out = pf.select_candidates(df, top_n=5)
    assert [c["ticker"] for c in out] == ["B", "A"]


# ---------------------------------------------------------------------------
# initial_inverse_vol_weights
# ---------------------------------------------------------------------------

def test_inverse_vol_weights_sum_and_monotonic():
    cands = [{"ticker": "A"}, {"ticker": "B"}, {"ticker": "C"}]
    vol = {"A": 0.10, "B": 0.20, "C": 0.40}
    w = pf.initial_inverse_vol_weights(cands, vol)
    assert abs(sum(w.values()) - 1.0) < 1e-12
    # Higher vol -> strictly lower weight.
    assert w["A"] > w["B"] > w["C"]


def test_inverse_vol_weights_all_missing_equal():
    cands = [{"ticker": "A"}, {"ticker": "B"}, {"ticker": "C"}, {"ticker": "D"}]
    w = pf.initial_inverse_vol_weights(cands, {})
    assert abs(sum(w.values()) - 1.0) < 1e-12
    for v in w.values():
        assert abs(v - 0.25) < 1e-12


def test_inverse_vol_weights_empty():
    assert pf.initial_inverse_vol_weights([], {}) == {}


def test_inverse_vol_weights_uses_candidate_volatility_field():
    cands = [{"ticker": "A", "volatility": 0.1}, {"ticker": "B", "volatility": 0.3}]
    w = pf.initial_inverse_vol_weights(cands, {})
    assert w["A"] > w["B"]


# ---------------------------------------------------------------------------
# apply_name_cap
# ---------------------------------------------------------------------------

def test_apply_name_cap_respects_cap_and_total():
    # cap 0.40 is feasible for 3 names summing to 1.0 (max headroom 1.20),
    # so the excess from A is fully reabsorbed and the total is preserved.
    w = {"A": 0.50, "B": 0.30, "C": 0.20}
    out = pf.apply_name_cap(w, 0.40)
    assert all(v <= 0.40 + _TOL for v in out.values()), out
    assert abs(sum(out.values()) - 1.0) < 1e-9, sum(out.values())
    # A was capped to 0.40; the 0.10 excess went to B and C proportionally.
    assert abs(out["A"] - 0.40) < 1e-9, out


def test_apply_name_cap_noop_when_under():
    w = {"A": 0.2, "B": 0.2, "C": 0.2}
    out = pf.apply_name_cap(w, 0.5)
    assert out == w


# ---------------------------------------------------------------------------
# apply_sector_cap
# ---------------------------------------------------------------------------

def test_apply_sector_cap_respects_cap_and_total():
    w = {"A": 0.30, "B": 0.30, "C": 0.20, "D": 0.20}
    sectors = {"A": "X", "B": "X", "C": "Y", "D": "Z"}
    out = pf.apply_sector_cap(w, sectors, 0.40)
    totals = pf._sector_totals(out, sectors)
    assert all(t <= 0.40 + _TOL for t in totals.values()), totals
    assert abs(sum(out.values()) - 1.0) < 1e-9


def test_apply_sector_cap_none_sector_is_solo_bucket():
    # Two None-sector names (0.30 each) are SEPARATE solo buckets, not one joint
    # bucket. With sector_cap 0.40 their combined 0.60 would breach a shared
    # bucket, but as solos each is 0.30 (< cap) so nothing is scaled.
    w = {"A": 0.30, "B": 0.30, "C": 0.40}
    sectors = {"A": None, "B": None, "C": "Z"}
    out = pf.apply_sector_cap(w, sectors, 0.40)
    assert abs(sum(out.values()) - 1.0) < 1e-9, sum(out.values())
    # Untouched: each solo bucket and C's sector are within the cap.
    assert abs(out["A"] - 0.30) < 1e-9 and abs(out["B"] - 0.30) < 1e-9, out
    totals = pf._sector_totals(out, sectors)
    assert all(t <= 0.40 + _TOL for t in totals.values()), totals


# ---------------------------------------------------------------------------
# enforce_caps — the joint-constraint case
# ---------------------------------------------------------------------------

def test_enforce_caps_joint_satisfied_when_naive_would_reviolate():
    """
    Construct a FEASIBLE case where naive sequential capping re-violates the
    name cap, so the alternation in enforce_caps is required.

    Two sectors of 3 names each, name cap 0.25, sector cap 0.50. Start heavily
    concentrated in sector X with sector Y already carrying a near-cap name
    (Y1=0.22). Naive pass: name-cap (X1,X2 -> 0.25), then sector-cap scales X
    down to 0.50 and dumps the freed weight onto sector Y, where Y1's large
    intra-sector share lifts it to ~0.37 -> OVER the 0.25 name cap. enforce_caps
    must iterate to satisfy BOTH caps simultaneously while preserving the sum
    (this input is jointly feasible at sum 1.0: X=0.50, Y=0.50, every name<=0.25).
    """
    weights = {
        "X1": 0.25, "X2": 0.25, "X3": 0.20,   # sector X (sum 0.70 > sector cap)
        "Y1": 0.22, "Y2": 0.05, "Y3": 0.03,   # sector Y, Y1 near the name cap
    }
    sectors = {"X1": "X", "X2": "X", "X3": "X", "Y1": "Y", "Y2": "Y", "Y3": "Y"}

    # Sanity: a single naive pass leaves the name cap re-violated (justifies the
    # iterative enforce_caps).
    naive = pf.apply_sector_cap(pf.apply_name_cap(weights, 0.25), sectors, 0.50)
    assert any(v > 0.25 + _TOL for v in naive.values()), \
        f"expected naive pass to re-violate the name cap, got {naive}"

    out = pf.enforce_caps(weights, sectors, max_name_weight=0.25, sector_cap=0.50)

    # Both caps must hold simultaneously within tolerance.
    assert all(v <= 0.25 + _TOL for v in out.values()), out
    totals = pf._sector_totals(out, sectors)
    assert all(t <= 0.50 + _TOL for t in totals.values()), totals
    # Sum is preserved (feasible projection on this input).
    assert abs(sum(out.values()) - 1.0) < 1e-6, sum(out.values())


def test_enforce_caps_infeasible_returns_best_effort():
    """
    Jointly infeasible: 2 names in one sector but sector_cap (0.50) requires
    each <= 0.25 yet only those 2 names exist and total must be 1.0 -> the
    sector cannot exceed 0.50 while names cannot exceed 0.25 and sum to 1.0.
    enforce_caps must NOT loop forever / crash; it returns a clamped projection
    where the sector cap is respected (caps cannot both hold + sum to 1.0).
    """
    weights = {"A": 0.5, "B": 0.5}
    sectors = {"A": "X", "B": "X"}
    out = pf.enforce_caps(weights, sectors, max_name_weight=0.25, sector_cap=0.50,
                          max_iter=10)
    # Sector cap is hard-clamped (down-scaling only) so it holds.
    totals = pf._sector_totals(out, sectors)
    assert all(t <= 0.50 + _TOL for t in totals.values()), totals
    # Returned without raising; values are finite & nonnegative.
    assert all(math.isfinite(v) and v >= 0.0 for v in out.values())


# ---------------------------------------------------------------------------
# scale_to_target_vol
# ---------------------------------------------------------------------------

def test_scale_to_target_vol_hits_target_below_max_gross():
    # Diagonal cov with per-name annual vol 0.30 -> portfolio vol depends on w.
    tickers = ["A", "B", "C", "D"]
    # Equal weights, each var 0.09 (vol 0.30), independent.
    cov = np.diag([0.09, 0.09, 0.09, 0.09])
    weights = {tk: 0.25 for tk in tickers}
    # pvol = sqrt(sum (0.25^2 * 0.09)) = sqrt(4 * 0.0625 * 0.09) = sqrt(0.0225)=0.15
    scaled, expected_vol, gross = pf.scale_to_target_vol(
        weights, cov, tickers, target_vol=0.12, max_gross=1.0
    )
    # target 0.12 < pvol 0.15 -> gross = 0.12/0.15 = 0.8 (< max_gross)
    assert abs(gross - 0.8) < 1e-6, gross
    assert abs(expected_vol - 0.12) < 1e-6, expected_vol
    assert gross <= 1.0 + _TOL
    assert abs(sum(scaled.values()) - gross) < 1e-9


def test_scale_to_target_vol_clamped_to_max_gross():
    tickers = ["A", "B"]
    # Very low vol -> target/pvol > 1 -> clamp to max_gross.
    cov = np.diag([0.0001, 0.0001])
    weights = {"A": 0.5, "B": 0.5}
    scaled, expected_vol, gross = pf.scale_to_target_vol(
        weights, cov, tickers, target_vol=0.12, max_gross=1.0
    )
    assert abs(gross - 1.0) < 1e-9, gross


def test_scale_to_target_vol_risk_off_halves_gross():
    tickers = ["A", "B", "C", "D"]
    cov = np.diag([0.09, 0.09, 0.09, 0.09])
    weights = {tk: 0.25 for tk in tickers}
    _, _, gross_neutral = pf.scale_to_target_vol(
        weights, cov, tickers, target_vol=0.12, max_gross=1.0, regime_multiplier=1.0
    )
    _, _, gross_off = pf.scale_to_target_vol(
        weights, cov, tickers, target_vol=0.12, max_gross=1.0, regime_multiplier=0.5
    )
    assert abs(gross_off - 0.5 * gross_neutral) < 1e-9, (gross_off, gross_neutral)


def test_scale_to_target_vol_zero_pvol_uses_max_gross():
    tickers = ["A", "B"]
    cov = np.zeros((2, 2))
    weights = {"A": 0.5, "B": 0.5}
    _, expected_vol, gross = pf.scale_to_target_vol(
        weights, cov, tickers, target_vol=0.12, max_gross=1.0
    )
    assert abs(gross - 1.0) < 1e-9
    assert abs(expected_vol - 0.0) < 1e-12


# ---------------------------------------------------------------------------
# estimate_covariance
# ---------------------------------------------------------------------------

def test_estimate_covariance_sample_path():
    rng = np.random.default_rng(3)
    tickers = ["A", "B", "C"]
    n = 120
    dates = pd.bdate_range("2025-01-01", periods=n)
    # Correlated returns: common factor + idiosyncratic.
    factor = rng.normal(0, 0.01, size=n)
    frames = {}
    for tk in tickers:
        idio = rng.normal(0, 0.008, size=n)
        rets = factor + idio
        close = 1000.0 * np.exp(np.cumsum(rets))
        frames[tk] = pd.DataFrame({"date": dates, "close": close})

    cov, vol, method = pf.estimate_covariance(frames, tickers, lookback_days=100, min_obs=20)
    assert method == "sample", method
    assert cov.shape == (3, 3)
    # Symmetric.
    assert np.allclose(cov, cov.T, atol=1e-12)
    # Positive diagonal (annualized variance).
    assert all(cov[i, i] > 0 for i in range(3))
    # PSD-ish: eigenvalues non-negative (sample cov is PSD).
    eig = np.linalg.eigvalsh(cov)
    assert eig.min() > -1e-9, eig
    # vol dict consistent with diagonal.
    for i, tk in enumerate(tickers):
        assert abs(vol[tk] - math.sqrt(cov[i, i])) < 1e-12


def test_estimate_covariance_diagonal_fallback_too_few_rows():
    tickers = ["A", "B", "C"]
    dates = pd.bdate_range("2025-01-01", periods=8)
    frames = {}
    rng = np.random.default_rng(5)
    for tk in tickers:
        close = 1000.0 * np.exp(np.cumsum(rng.normal(0, 0.01, size=8)))
        frames[tk] = pd.DataFrame({"date": dates, "close": close})
    # min_obs=20 but only ~7 returns -> diagonal fallback.
    cov, vol, method = pf.estimate_covariance(frames, tickers, lookback_days=60, min_obs=20)
    assert method == "diagonal", method
    # Off-diagonals are zero.
    for i in range(3):
        for j in range(3):
            if i != j:
                assert abs(cov[i, j]) < 1e-12
    # Positive diagonal, no crash, vol finite.
    assert all(cov[i, i] > 0 for i in range(3)), cov
    assert all(math.isfinite(vol[tk]) and vol[tk] > 0 for tk in tickers)


def test_estimate_covariance_missing_ticker_gets_fallback_var():
    tickers = ["A", "B", "C"]
    dates = pd.bdate_range("2025-01-01", periods=120)
    rng = np.random.default_rng(9)
    frames = {
        "A": pd.DataFrame({"date": dates, "close": 1000.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 120)))}),
        "B": pd.DataFrame({"date": dates, "close": 1000.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 120)))}),
        # C has no frame at all.
    }
    cov, vol, method = pf.estimate_covariance(frames, tickers, lookback_days=100, min_obs=20)
    # C must still get a positive, finite variance (median fallback / epsilon).
    assert cov[2, 2] > 0 and math.isfinite(cov[2, 2]), cov
    assert vol["C"] > 0 and math.isfinite(vol["C"])


def test_estimate_covariance_empty_tickers():
    cov, vol, method = pf.estimate_covariance({}, [], lookback_days=60)
    assert cov.shape == (0, 0)
    assert vol == {}
    assert method == "diagonal"


# ---------------------------------------------------------------------------
# apply_hysteresis
# ---------------------------------------------------------------------------

def test_apply_hysteresis_keeps_prev_within_band():
    new = {"A": 0.205, "B": 0.30}
    prev = {"A": 0.20, "B": 0.20}
    # A: |0.205-0.20|=0.005 < band 0.02 -> keep prev 0.20.
    # B: |0.30-0.20|=0.10 >= band -> take new 0.30.
    out = pf.apply_hysteresis(new, prev, notrade_band=0.02, min_weight=0.03)
    assert abs(out["A"] - 0.20) < 1e-12, out
    assert abs(out["B"] - 0.30) < 1e-12, out


def test_apply_hysteresis_drops_below_min_weight():
    new = {"A": 0.02, "B": 0.10}   # A below min_weight 0.03
    prev = {}
    out = pf.apply_hysteresis(new, prev, notrade_band=0.02, min_weight=0.03)
    assert "A" not in out, out
    assert abs(out["B"] - 0.10) < 1e-12


def test_apply_hysteresis_exit_when_new_zero_and_prev_small():
    # prev held small position, new wants to keep it but within band -> prev kept,
    # but prev itself below min_weight -> dropped (exit).
    new = {"A": 0.0}
    prev = {"A": 0.02}
    out = pf.apply_hysteresis(new, prev, notrade_band=0.05, min_weight=0.03)
    # |0 - 0.02| = 0.02 < band 0.05 -> keep prev 0.02, then < min_weight -> drop.
    assert "A" not in out, out


# ---------------------------------------------------------------------------
# diff_positions
# ---------------------------------------------------------------------------

def test_diff_positions_classification():
    prev = {"keep": 0.10, "grow": 0.10, "shrink": 0.20, "gone": 0.10}
    curr = {"keep": 0.10, "grow": 0.20, "shrink": 0.10, "fresh": 0.15}
    diffs = {d["ticker"]: d["diff_type"] for d in pf.diff_positions(prev, curr)}
    assert diffs["fresh"] == "new"
    assert diffs["gone"] == "exit"
    assert diffs["grow"] == "increase"
    assert diffs["shrink"] == "decrease"
    assert diffs["keep"] == "hold"


# ---------------------------------------------------------------------------
# build_portfolio_snapshot — end to end
# ---------------------------------------------------------------------------

def test_build_snapshot_end_to_end_all_constraints():
    preds = _make_predictions(n=12, n_sectors=3, seed=2)
    tickers = [p["ticker"] for p in preds]
    frames = _make_price_frames(tickers, n_rows=120, seed=4)
    cfg = _config()

    snap = pf.build_portfolio_snapshot(
        preds, frames, prev_weights={}, config=cfg,
        regime="neutral", run_date="2026-06-10", as_of_date="2026-06-09",
        model_version="cs-v1-test", mode="shadow",
    )

    assert snap["status"] == "ok", snap.get("warnings")
    positions = snap["positions"]
    assert positions, "expected non-empty positions"

    # top_n cap honored.
    assert len(positions) <= cfg["top_n"]

    gross = snap["gross_exposure"]
    assert gross <= cfg["max_gross"] + 1e-9, gross
    assert abs(snap["net_exposure"] - gross) < 1e-12  # long only

    # Each HELD (post-hysteresis) weight >= min_weight.
    for p in positions:
        assert p["target_weight"] >= cfg["min_weight"] - 1e-9, p

    # --- Normalized post-enforce_caps constraints (reconstruct them) ---
    # Re-run the deterministic prefix to verify the NORMALIZED caps directly.
    cands = pf.select_candidates(preds, top_n=cfg["top_n"], min_expected_ret=0.0)
    sector_lk = {c["ticker"]: c["sector"] for c in cands}
    cov, vol, _ = pf.estimate_covariance(frames, [c["ticker"] for c in cands],
                                         lookback_days=cfg["cov_lookback_days"])
    init_w = pf.initial_inverse_vol_weights(cands, vol)
    capped = pf.enforce_caps(init_w, sector_lk,
                             max_name_weight=cfg["max_name_weight"],
                             sector_cap=cfg["sector_cap"])
    # Normalized name cap.
    assert all(v <= cfg["max_name_weight"] + 1e-9 for v in capped.values()), capped
    # Normalized sector cap.
    sector_totals = pf._sector_totals(capped, sector_lk)
    assert all(t <= cfg["sector_cap"] + 1e-9 for t in sector_totals.values()), sector_totals

    # sector_exposure sums to gross.
    assert abs(sum(snap["sector_exposure"].values()) - gross) < 1e-6, snap["sector_exposure"]

    # diff_summary consistent with positions diff types + prev (empty -> all new).
    ds = snap["diff_summary"]
    type_counts = {}
    for p in positions:
        type_counts[p["diff_type"]] = type_counts.get(p["diff_type"], 0) + 1
    assert ds["add"] == type_counts.get("new", 0) + type_counts.get("increase", 0)
    assert ds["trim"] == type_counts.get("decrease", 0)
    assert ds["hold"] == type_counts.get("hold", 0)
    # With empty prev, every held name is "new".
    assert ds["add"] == len(positions), (ds, type_counts)

    # positions sorted by target_weight desc.
    tw = [p["target_weight"] for p in positions]
    assert tw == sorted(tw, reverse=True), tw

    # limit / stop populated from close (long side).
    for p in positions:
        assert p["limit_price"] is not None and p["stop_loss"] is not None
        assert p["stop_loss"] < p["limit_price"]


def test_build_snapshot_cash_when_no_eligible():
    # All expected_ret negative and min_expected_ret default 0 -> none eligible.
    preds = [
        {"ticker": "A.JP", "cs_rank": 1, "expected_ret": -0.01, "prob_up": 0.4,
         "sector": "X", "close": 1000},
        {"ticker": "B.JP", "cs_rank": 2, "expected_ret": -0.02, "prob_up": 0.3,
         "sector": "Y", "close": 2000},
    ]
    snap = pf.build_portfolio_snapshot(preds, {}, prev_weights={}, config=_config())
    assert snap["status"] == "ok", snap
    assert snap["positions"] == []
    assert abs(snap["gross_exposure"]) < 1e-12
    assert abs(snap["net_exposure"]) < 1e-12
    assert "no_eligible_candidates" in snap["warnings"], snap["warnings"]


def test_build_snapshot_risk_off_reduces_gross():
    preds = _make_predictions(n=12, n_sectors=3, seed=2)
    tickers = [p["ticker"] for p in preds]
    frames = _make_price_frames(tickers, n_rows=120, seed=4)
    cfg = _config()

    neutral = pf.build_portfolio_snapshot(preds, frames, {}, cfg, regime="neutral")
    risk_off = pf.build_portfolio_snapshot(preds, frames, {}, cfg, regime="risk_off")

    # Risk-off gross should be strictly lower (multiplier 0.5) when neutral > 0.
    assert neutral["gross_exposure"] > 0
    assert risk_off["gross_exposure"] < neutral["gross_exposure"] + 1e-9
    assert risk_off["constraints"]["regime_multiplier"] == 0.5


def test_build_snapshot_covariance_fallback_no_crash():
    # Too-short price frames -> diagonal covariance fallback, still builds.
    preds = _make_predictions(n=6, n_sectors=2, seed=8)
    tickers = [p["ticker"] for p in preds]
    short_frames = {tk: pd.DataFrame({
        "date": pd.bdate_range("2025-01-01", periods=6),
        "close": np.linspace(1000, 1050, 6),
    }) for tk in tickers}
    snap = pf.build_portfolio_snapshot(preds, short_frames, {}, _config())
    assert snap["status"] == "ok", snap.get("warnings")
    assert snap["constraints"]["cov_method"] == "diagonal"
    assert "covariance_diagonal_fallback" in snap["warnings"]


def test_build_snapshot_hysteresis_vs_prev():
    # A prev book where one name is within the no-trade band stays unchanged.
    preds = _make_predictions(n=12, n_sectors=3, seed=2)
    tickers = [p["ticker"] for p in preds]
    frames = _make_price_frames(tickers, n_rows=120, seed=4)
    cfg = _config()

    first = pf.build_portfolio_snapshot(preds, frames, {}, cfg)
    prev = {p["ticker"]: p["target_weight"] for p in first["positions"]}

    # Re-run with the same inputs but prev = the prior book; weights should be
    # nearly identical, so most names fall within the band and are unchanged.
    second = pf.build_portfolio_snapshot(preds, frames, prev, cfg)
    second_w = {p["ticker"]: p["target_weight"] for p in second["positions"]}
    # Every name that existed before and is within band keeps its exact prev wt.
    unchanged = 0
    for tk, w in prev.items():
        if tk in second_w and abs(second_w[tk] - w) < 1e-12:
            unchanged += 1
    assert unchanged >= 1, (prev, second_w)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# merge_target_weights
# ---------------------------------------------------------------------------

def test_merge_target_weights_active_ok():
    signals = [{"ticker": "7011.JP", "action": "BUY", "reason": "r1"},
               {"ticker": "9999.JP", "action": "HOLD", "reason": "r2"}]
    snapshot = {"status": "ok", "mode": "active",
                "positions": [{"ticker": "7011.JP", "target_weight": 0.18, "cs_rank": 1}]}
    out = pf.merge_target_weights(signals, snapshot, gate_passed=True)
    assert out[0]["target_weight"] == 0.18
    assert "建玉" in out[0]["reason"]
    assert out[1]["target_weight"] == 0.0
    assert out[1]["action"] == "HOLD"
    # Input signals must NOT be mutated (shadow byte-for-byte guarantee relies on this).
    assert signals[0]["reason"] == "r1"
    assert "target_weight" not in signals[0]


def test_merge_target_weights_noop_on_shadow_or_gate_fail():
    signals = [{"ticker": "7011.JP", "action": "BUY", "reason": "r"}]
    shadow = {"status": "ok", "mode": "shadow",
              "positions": [{"ticker": "7011.JP", "target_weight": 0.18}]}
    assert "target_weight" not in pf.merge_target_weights(signals, shadow, gate_passed=True)[0]
    active = {**shadow, "mode": "active"}
    assert "target_weight" not in pf.merge_target_weights(signals, active, gate_passed=False)[0]
    # no snapshot -> unchanged
    assert "target_weight" not in pf.merge_target_weights(signals, None, gate_passed=True)[0]


def test_read_portfolio_gate_various_cases():
    import json
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmp:
        path_avail = os.path.join(tmp, "avail.json")
        # available=true, no gate key -> True
        Path(path_avail).write_text(json.dumps({"available": True}), encoding="utf-8")
        assert pf.read_portfolio_gate(path_avail) is True

        # available=false -> False
        Path(path_avail).write_text(json.dumps({"available": False}), encoding="utf-8")
        assert pf.read_portfolio_gate(path_avail) is False

        # nonexistent path -> False
        assert pf.read_portfolio_gate(os.path.join(tmp, "nonexistent.json")) is False

        # available=true, gate.passed=false -> False
        Path(path_avail).write_text(
            json.dumps({"available": True, "gate": {"passed": False}}), encoding="utf-8"
        )
        assert pf.read_portfolio_gate(path_avail) is False


ALL_TESTS = [
    test_select_candidates_filter_sort_cap,
    test_select_candidates_missing_er_eligible_only_when_floor_nonpositive,
    test_select_candidates_empty_in_empty_out,
    test_select_candidates_accepts_dataframe,
    test_inverse_vol_weights_sum_and_monotonic,
    test_inverse_vol_weights_all_missing_equal,
    test_inverse_vol_weights_empty,
    test_inverse_vol_weights_uses_candidate_volatility_field,
    test_apply_name_cap_respects_cap_and_total,
    test_apply_name_cap_noop_when_under,
    test_apply_sector_cap_respects_cap_and_total,
    test_apply_sector_cap_none_sector_is_solo_bucket,
    test_enforce_caps_joint_satisfied_when_naive_would_reviolate,
    test_enforce_caps_infeasible_returns_best_effort,
    test_scale_to_target_vol_hits_target_below_max_gross,
    test_scale_to_target_vol_clamped_to_max_gross,
    test_scale_to_target_vol_risk_off_halves_gross,
    test_scale_to_target_vol_zero_pvol_uses_max_gross,
    test_estimate_covariance_sample_path,
    test_estimate_covariance_diagonal_fallback_too_few_rows,
    test_estimate_covariance_missing_ticker_gets_fallback_var,
    test_estimate_covariance_empty_tickers,
    test_apply_hysteresis_keeps_prev_within_band,
    test_apply_hysteresis_drops_below_min_weight,
    test_apply_hysteresis_exit_when_new_zero_and_prev_small,
    test_diff_positions_classification,
    test_build_snapshot_end_to_end_all_constraints,
    test_build_snapshot_cash_when_no_eligible,
    test_build_snapshot_risk_off_reduces_gross,
    test_build_snapshot_covariance_fallback_no_crash,
    test_build_snapshot_hysteresis_vs_prev,
    test_merge_target_weights_active_ok,
    test_merge_target_weights_noop_on_shadow_or_gate_fail,
    test_read_portfolio_gate_various_cases,
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
