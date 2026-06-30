#!/usr/bin/env python3
"""
Unit tests for src/portfolio_shadow.py (Phase 2 shadow-validation comparison).

PURE logic — synthetic in-memory records only, NO database or network.

Runnable two ways:
  uv run python tests/test_portfolio_shadow.py          # standalone runner
  uv run pytest tests/test_portfolio_shadow.py          # if pytest is present
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import portfolio_shadow as ps  # noqa: E402


def _approx(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


# ---------------------------------------------------------------------------
# Synthetic record builders
# ---------------------------------------------------------------------------


def _planted_records(n_dates=12, n_tickers=10, seed=7):
    """Phase 2 cs_rank strongly aligned with realized_ret; Phase 1 prob_up noise.

    For each date: draw distinct "true alpha" per ticker; realized_ret tracks it
    closely. cs_rank = ascending rank of -alpha (so rank 1 = highest alpha = best
    realized). p1_prob_up is independent uniform noise. Phase 2 portfolio holds
    the top-N at fixed equal weight (constant -> ~0 turnover).
    """
    rng = np.random.default_rng(seed)
    tickers = [f"{1000 + i}.JP" for i in range(n_tickers)]
    top_n = 4
    records = []
    for d in range(n_dates):
        date_str = f"2026-01-{d + 1:02d}"
        alpha = rng.normal(0.0, 1.0, size=n_tickers)
        realized = 0.02 * alpha + rng.normal(0.0, 0.001, size=n_tickers)
        # cs_rank: 1 = best (highest alpha). argsort desc -> ordinal ranks.
        order = np.argsort(-alpha, kind="mergesort")
        cs_rank = np.empty(n_tickers, dtype=int)
        for pos, idx in enumerate(order):
            cs_rank[idx] = pos + 1
        p1_prob = rng.uniform(0.3, 0.7, size=n_tickers)  # pure noise vs realized
        for i, tk in enumerate(tickers):
            in_top = cs_rank[i] <= top_n
            records.append(
                {
                    "date": date_str,
                    "ticker": tk,
                    "realized_ret": float(realized[i]),
                    "p1_prob_up": float(p1_prob[i]),
                    "p1_action": "HOLD",
                    "p2_cs_rank": int(cs_rank[i]),
                    "p2_expected_ret": float(0.02 * alpha[i]),
                    "p2_prob_up": float(0.5 + 0.1 * alpha[i]),
                    "p2_weight": 0.25 if in_top else 0.0,
                    "p2_prev_weight": 0.25 if in_top else 0.0,
                }
            )
    return records, top_n


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_planted_signal_phase2_beats_phase1():
    records, top_n = _planted_records()
    ic_p2 = ps.daily_ic(records, score_key="p2_expected_ret", method="spearman")
    ic_p1 = ps.daily_ic(records, score_key="p1_prob_up", method="spearman")
    assert ic_p2 is not None and ic_p1 is not None
    assert ic_p2 > ic_p1, (ic_p2, ic_p1)

    ret_p2 = ps.topn_realized_return(
        records, rank_key="p2_cs_rank", top_n=top_n, ascending=True
    )
    ret_p1 = ps.topn_realized_return(
        records, rank_key="p1_prob_up", top_n=top_n, ascending=False
    )
    assert ret_p2 is not None and ret_p1 is not None
    assert ret_p2 > ret_p1, (ret_p2, ret_p1)

    cmp = ps.compare_phase1_phase2(records, top_n=top_n)
    assert cmp["verdict"]["phase2_topn_ge_phase1"] is True
    assert cmp["verdict"]["phase2_rank_ic_ge_phase1"] is True
    assert cmp["delta"]["topn_realized_return"] > 0


def test_topn_realized_return_handcomputed():
    # One date, 4 tickers. Phase 2 cs_rank ascending top-2 = ranks 1,2.
    records = [
        {"date": "2026-02-01", "ticker": "A", "p2_cs_rank": 1, "realized_ret": 0.10},
        {"date": "2026-02-01", "ticker": "B", "p2_cs_rank": 2, "realized_ret": 0.02},
        {"date": "2026-02-01", "ticker": "C", "p2_cs_rank": 3, "realized_ret": -0.05},
        {"date": "2026-02-01", "ticker": "D", "p2_cs_rank": 4, "realized_ret": -0.20},
    ]
    # top-2 realized = mean(0.10, 0.02) = 0.06
    r = ps.topn_realized_return(records, rank_key="p2_cs_rank", top_n=2, ascending=True)
    assert _approx(r, 0.06), r
    # Phase 1 by prob_up desc: give B the highest prob -> top-1 = B = 0.02
    for rec, p in zip(records, [0.4, 0.9, 0.5, 0.1]):
        rec["p1_prob_up"] = p
    r1 = ps.topn_realized_return(
        records, rank_key="p1_prob_up", top_n=1, ascending=False
    )
    assert _approx(r1, 0.02), r1


def test_hit_rate_topn():
    records = [
        {"date": "2026-02-01", "ticker": "A", "p2_cs_rank": 1, "realized_ret": 0.10},
        {"date": "2026-02-01", "ticker": "B", "p2_cs_rank": 2, "realized_ret": -0.02},
        {"date": "2026-02-01", "ticker": "C", "p2_cs_rank": 3, "realized_ret": 0.05},
        {"date": "2026-02-01", "ticker": "D", "p2_cs_rank": 4, "realized_ret": -0.20},
    ]
    # top-2 = {A:+, B:-} -> 1 of 2 positive = 0.5
    hr = ps.hit_rate_topn(records, rank_key="p2_cs_rank", top_n=2, ascending=True)
    assert _approx(hr, 0.5), hr


def test_turnover_two_dates():
    # Date 1: A=0.5, B=0.5. Date 2: A=0.5, C=0.5 (B exits, C enters).
    records = [
        {"date": "2026-03-01", "ticker": "A", "p2_weight": 0.5, "p2_prev_weight": 0.0},
        {"date": "2026-03-01", "ticker": "B", "p2_weight": 0.5, "p2_prev_weight": 0.0},
        {"date": "2026-03-02", "ticker": "A", "p2_weight": 0.5, "p2_prev_weight": 0.5},
        {"date": "2026-03-02", "ticker": "B", "p2_weight": 0.0, "p2_prev_weight": 0.5},
        {"date": "2026-03-02", "ticker": "C", "p2_weight": 0.5, "p2_prev_weight": 0.0},
    ]
    # Date 1: 0.5*(|0.5-0|+|0.5-0|) = 0.5
    # Date 2: 0.5*(|0.5-0.5|+|0-0.5|+|0.5-0|) = 0.5*(0+0.5+0.5) = 0.5
    # mean = 0.5
    t = ps.turnover(records)
    assert _approx(t, 0.5), t


def test_turnover_none_without_weights():
    records = [
        {"date": "2026-03-01", "ticker": "A", "realized_ret": 0.01},
        {"date": "2026-03-02", "ticker": "A", "realized_ret": 0.02},
    ]
    assert ps.turnover(records) is None


def test_max_drawdown_known_sequence():
    # [+0.1, -0.2, +0.05] -> equity [1.1, 0.88, 0.924]; peak 1.1.
    # min drawdown = 0.88/1.1 - 1 = -0.2
    dd = ps.max_drawdown_from_period_returns([0.1, -0.2, 0.05])
    assert _approx(dd, -0.2), dd
    # Monotonic up -> 0.0 drawdown.
    assert _approx(ps.max_drawdown_from_period_returns([0.01, 0.02, 0.03]), 0.0)
    # Empty / all-None -> None.
    assert ps.max_drawdown_from_period_returns([]) is None
    assert ps.max_drawdown_from_period_returns([None, float("nan")]) is None


def test_expected_ret_calibration_bias_sign():
    # Phase 2 top-2 over-predicts: expected >> realized -> positive bias.
    records = [
        {
            "date": "2026-04-01",
            "ticker": "A",
            "p2_cs_rank": 1,
            "p2_expected_ret": 0.05,
            "realized_ret": 0.01,
        },
        {
            "date": "2026-04-01",
            "ticker": "B",
            "p2_cs_rank": 2,
            "p2_expected_ret": 0.04,
            "realized_ret": 0.00,
        },
        {
            "date": "2026-04-01",
            "ticker": "C",
            "p2_cs_rank": 3,
            "p2_expected_ret": -0.01,
            "realized_ret": -0.02,
        },
    ]
    cal = ps.expected_ret_calibration(records, top_n=2)
    # top-2 expected mean = 0.045, realized mean = 0.005, bias = +0.04
    assert _approx(cal["mean_expected_ret"], 0.045), cal
    assert _approx(cal["mean_realized_ret"], 0.005), cal
    assert cal["bias"] is not None and cal["bias"] > 0, cal


def test_build_report_insufficient_history():
    # 3 distinct dates < MIN_SHADOW_DATES (5) -> available false.
    records = [
        {
            "date": f"2026-05-0{d}",
            "ticker": "A",
            "p2_cs_rank": 1,
            "p2_expected_ret": 0.01,
            "realized_ret": 0.01,
            "p1_prob_up": 0.6,
        }
        for d in range(1, 4)
    ]
    rep = ps.build_shadow_report(records, top_n=4)
    assert rep["available"] is False
    assert rep["reason"] == "insufficient_shadow_history"
    assert rep["n_dates"] == 3
    # No generated_at unless supplied (deterministic).
    assert "generated_at" not in rep


def test_build_report_available_with_comparison():
    records, top_n = _planted_records(n_dates=8)
    rep = ps.build_shadow_report(
        records,
        top_n=top_n,
        generated_at="2026-06-10T06:00:00+09:00",
        model_version="cs-v1-20260606",
        window={"start": "2026-01-01", "end": "2026-01-08", "lookback_days": 8},
    )
    assert rep["available"] is True
    assert rep["generated_at"] == "2026-06-10T06:00:00+09:00"
    assert rep["model_version"] == "cs-v1-20260606"
    assert "comparison" in rep
    cmp = rep["comparison"]
    for side in ("phase1", "phase2"):
        assert side in cmp
        assert "topn_realized_return" in cmp[side]
    assert "verdict" in cmp and "delta" in cmp
    # Phase 2 carries turnover + calibration; Phase 1 carries neither.
    assert cmp["phase2"]["turnover"] is not None
    assert "expected_ret_calibration" in cmp["phase2"]
    assert cmp["phase1"]["turnover"] is None


def test_none_safety_missing_keys_and_none_returns():
    # Records with None realized_ret and missing keys must not raise.
    records = [
        {"date": "2026-07-01", "ticker": "A", "p2_cs_rank": 1, "realized_ret": None},
        {"date": "2026-07-01", "ticker": "B"},  # missing nearly everything
        {
            "date": "2026-07-02",
            "ticker": "A",
            "p2_cs_rank": 1,
            "p2_expected_ret": None,
            "realized_ret": None,
            "p1_prob_up": None,
        },
    ]
    # None of these should throw; metrics should be None (nothing finite).
    assert ps.daily_ic(records, score_key="p2_expected_ret") is None
    assert (
        ps.topn_realized_return(records, rank_key="p2_cs_rank", top_n=2, ascending=True)
        is None
    )
    assert (
        ps.hit_rate_topn(records, rank_key="p2_cs_rank", top_n=2, ascending=True)
        is None
    )
    cmp = ps.compare_phase1_phase2(records, top_n=2)
    # Deltas/verdicts must be None (never spurious True) when sides undefined.
    assert cmp["delta"]["topn_realized_return"] is None
    assert cmp["verdict"]["phase2_topn_ge_phase1"] is None


def test_empty_records():
    assert ps.daily_ic([], score_key="p1_prob_up") is None
    assert ps.turnover([]) is None
    assert ps.max_drawdown_from_period_returns([]) is None
    rep = ps.build_shadow_report([], top_n=4)
    assert rep["available"] is False
    assert rep["reason"] == "insufficient_shadow_history"
    assert rep["n_records"] == 0


# ---------------------------------------------------------------------------
# _active_readiness (from scripts/portfolio_shadow_report.py)
# ---------------------------------------------------------------------------

import importlib  # noqa: E402


def _load_psr():
    """Import scripts/portfolio_shadow_report as a module."""
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("scripts.portfolio_shadow_report")


def test_active_readiness_short_window():
    psr = _load_psr()
    report = {"n_dates": 4}
    result = psr._active_readiness(report, gate_passed=True)
    assert result["active_ready"] is False
    assert any("shadow_days" in r for r in result["reasons"])


def test_active_readiness_ready_case():
    psr = _load_psr()
    report = {
        "n_dates": 12,
        "comparison": {"delta": {"daily_ic": 0.012}},
    }
    result = psr._active_readiness(report, gate_passed=True)
    assert result["active_ready"] is True
    assert result["reasons"] == []


def test_active_readiness_gate_false():
    psr = _load_psr()
    report = {
        "n_dates": 12,
        "comparison": {"delta": {"daily_ic": 0.012}},
    }
    result = psr._active_readiness(report, gate_passed=False)
    assert result["active_ready"] is False
    assert any("portfolio_gate" in r for r in result["reasons"])


ALL_TESTS = [
    test_planted_signal_phase2_beats_phase1,
    test_topn_realized_return_handcomputed,
    test_hit_rate_topn,
    test_turnover_two_dates,
    test_turnover_none_without_weights,
    test_max_drawdown_known_sequence,
    test_expected_ret_calibration_bias_sign,
    test_build_report_insufficient_history,
    test_build_report_available_with_comparison,
    test_none_safety_missing_keys_and_none_returns,
    test_empty_records,
    test_active_readiness_short_window,
    test_active_readiness_ready_case,
    test_active_readiness_gate_false,
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
