#!/usr/bin/env python3
"""
Unit tests for src/performance.py (pure logic, no DB / no network).

Runnable standalone:
  uv run python tests/test_performance.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.performance import (  # noqa: E402
    build_equity_curves,
    build_drawdown,
    rolling_metrics,
    build_reliability,
    build_recent_outcomes,
    build_performance_detail,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _row(entry_date, action, horizon, realized_ret, benchmark_ret=None,
         excess_ret=None, hit=None, ticker="7011.JP", name="三菱重工業",
         conviction=0.7, mae=None, mfe=None, exit_reason="time"):
    return {
        "entry_date": entry_date,
        "ticker": ticker,
        "name": name,
        "action": action,
        "conviction": conviction,
        "horizon_days": horizon,
        "realized_ret": realized_ret,
        "benchmark_ret": benchmark_ret,
        "excess_ret": excess_ret,
        "hit": hit,
        "mae": mae,
        "mfe": mfe,
        "exit_reason": exit_reason,
    }


# ---------------------------------------------------------------------------
# build_equity_curves
# ---------------------------------------------------------------------------

def test_equity_curves_basic_strategy_and_benchmark():
    """Strategy and benchmark compound from 1.0 over two dates."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.02, benchmark_ret=0.01),
        _row("2026-05-01", "MILD_BUY", 5, 0.04, benchmark_ret=0.01),
        _row("2026-05-05", "BUY", 5, 0.03, benchmark_ret=0.02),
    ]
    curves = build_equity_curves(rows, horizon=5)
    assert len(curves) == 2

    # Day 1: strategy = 1.0 * (1 + mean(0.02, 0.04)) = 1.0 * 1.03 = 1.03
    # benchmark = 1.0 * (1 + 0.01) = 1.01
    assert abs(curves[0]["strategy"] - 1.03) < 1e-9
    assert abs(curves[0]["benchmark"] - 1.01) < 1e-9
    assert curves[0]["date"] == "2026-05-01"
    assert curves[0]["n"] == 2

    # Day 2: strategy = 1.03 * (1 + 0.03) = 1.0609
    # benchmark = 1.01 * (1 + 0.02) = 1.0302
    assert abs(curves[1]["strategy"] - 1.03 * 1.03) < 1e-9
    assert abs(curves[1]["benchmark"] - 1.01 * 1.02) < 1e-9
    assert curves[1]["date"] == "2026-05-05"
    assert curves[1]["n"] == 1


def test_equity_curves_same_date_axis():
    """Both series share the same date axis."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.02, benchmark_ret=0.01),
        _row("2026-05-05", "BUY", 5, 0.03, benchmark_ret=0.02),
    ]
    curves = build_equity_curves(rows, horizon=5)
    strategy_dates = [c["date"] for c in curves]
    benchmark_dates = [c["date"] for c in curves]
    assert strategy_dates == benchmark_dates


def test_equity_curves_benchmark_none_carry():
    """When benchmark_ret=None for a day, benchmark carries; strategy still moves."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.02, benchmark_ret=0.01),
        _row("2026-05-05", "BUY", 5, 0.04, benchmark_ret=None),  # benchmark carry
        _row("2026-05-10", "BUY", 5, 0.06, benchmark_ret=0.05),
    ]
    curves = build_equity_curves(rows, horizon=5)
    assert len(curves) == 3

    day1_strat = 1.0 * 1.02   # 1.02
    day1_bench = 1.0 * 1.01   # 1.01

    # Day 2: benchmark_ret=None -> carry, strategy moves
    day2_strat = day1_strat * 1.04
    day2_bench = day1_bench   # carry

    day3_strat = day2_strat * 1.06
    day3_bench = day2_bench * 1.05

    assert abs(curves[0]["strategy"] - day1_strat) < 1e-9
    assert abs(curves[0]["benchmark"] - day1_bench) < 1e-9
    assert abs(curves[1]["strategy"] - day2_strat) < 1e-9
    assert abs(curves[1]["benchmark"] - day2_bench) < 1e-9
    assert abs(curves[2]["strategy"] - day3_strat) < 1e-9
    assert abs(curves[2]["benchmark"] - day3_bench) < 1e-9


def test_equity_curves_filters_non_long_or_wrong_horizon():
    """SELL, HOLD and wrong-horizon rows are excluded."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.02),
        _row("2026-05-01", "SELL", 5, -0.01),   # not LONG
        _row("2026-05-01", "HOLD", 5, 0.0),      # not LONG
        _row("2026-05-01", "BUY", 1, 0.03),      # wrong horizon
    ]
    curves = build_equity_curves(rows, horizon=5)
    assert len(curves) == 1
    assert abs(curves[0]["strategy"] - 1.02) < 1e-9
    assert curves[0]["n"] == 1


def test_equity_curves_excludes_none_realized_ret():
    """Rows with realized_ret=None are excluded."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.02),
        _row("2026-05-02", "BUY", 5, None),   # excluded
    ]
    curves = build_equity_curves(rows, horizon=5)
    assert len(curves) == 1


def test_equity_curves_empty_input():
    assert build_equity_curves([], horizon=5) == []


def test_equity_curves_ascending_dates():
    """Output is in ascending date order."""
    rows = [
        _row("2026-05-10", "BUY", 5, 0.02),
        _row("2026-05-01", "BUY", 5, 0.01),
    ]
    curves = build_equity_curves(rows, horizon=5)
    dates = [c["date"] for c in curves]
    assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# build_drawdown
# ---------------------------------------------------------------------------

def test_drawdown_values_nonpositive():
    """All drawdown values must be <= 0."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.05),
        _row("2026-05-05", "BUY", 5, -0.10),
        _row("2026-05-10", "BUY", 5, 0.08),
    ]
    curves = build_equity_curves(rows, horizon=5)
    dd = build_drawdown(curves)
    for entry in dd:
        assert entry["drawdown"] <= 0.0 + 1e-12


def test_drawdown_recovers_to_zero_after_new_peak():
    """After a new high, drawdown returns to 0 (within tolerance)."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.10),   # strategy = 1.10
        _row("2026-05-05", "BUY", 5, -0.09),  # strategy = 1.10 * 0.91 = ~1.001
        _row("2026-05-10", "BUY", 5, 0.10),   # strategy = ~1.001 * 1.10 = ~1.101
    ]
    curves = build_equity_curves(rows, horizon=5)
    dd = build_drawdown(curves)
    # day 0: no drawdown yet (first point IS the peak)
    assert abs(dd[0]["drawdown"]) < 1e-9
    # day 1: drawdown is negative
    assert dd[1]["drawdown"] < 0.0
    # day 2: new peak, drawdown should be >=0 (within tolerance)
    assert dd[2]["drawdown"] <= 1e-9


def test_drawdown_exact_values():
    """Exact drawdown calculation over three days."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.0),   # strategy = 1.0; peak = 1.0; dd = 0
        _row("2026-05-05", "BUY", 5, -0.20), # strategy = 0.8; peak = 1.0; dd = -0.2
        _row("2026-05-10", "BUY", 5, 0.50),  # strategy = 1.2; new peak; dd = 0
    ]
    curves = build_equity_curves(rows, horizon=5)
    dd = build_drawdown(curves)
    assert abs(dd[0]["drawdown"] - 0.0) < 1e-9
    assert abs(dd[1]["drawdown"] - (-0.20)) < 1e-9
    assert abs(dd[2]["drawdown"] - 0.0) < 1e-9


def test_drawdown_first_day_negative_from_origin():
    """A losing first day shows drawdown measured from the 1.0 origin, not zero."""
    rows = [
        _row("2026-05-01", "BUY", 5, -0.05),  # strategy 0.95; peak 1.0; dd -0.05
        _row("2026-05-02", "BUY", 5, 0.10),   # strategy 1.045; new peak; dd 0
    ]
    dd = build_drawdown(build_equity_curves(rows, horizon=5))
    assert abs(dd[0]["drawdown"] - (-0.05)) < 1e-9
    assert abs(dd[1]["drawdown"] - 0.0) < 1e-9


def test_drawdown_empty():
    assert build_drawdown([]) == []


def test_drawdown_dates_match_curves():
    """Drawdown dates match equity curve dates."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.02),
        _row("2026-05-05", "BUY", 5, -0.01),
    ]
    curves = build_equity_curves(rows, horizon=5)
    dd = build_drawdown(curves)
    assert [d["date"] for d in dd] == [c["date"] for c in curves]


# ---------------------------------------------------------------------------
# rolling_metrics
# ---------------------------------------------------------------------------

def test_rolling_hit_rate_and_avg_return():
    """rolling_metrics over a small window."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.02, hit=True),
        _row("2026-05-02", "BUY", 5, -0.04, hit=False),
        _row("2026-05-03", "BUY", 5, 0.06, hit=True),
        _row("2026-05-04", "BUY", 5, 0.01, hit=True),
    ]
    m = rolling_metrics(rows, window=3)
    # Last 3 distinct dates: 2026-05-02, -03, -04
    # hit_rate_20d: 2 True out of 3 = 2/3
    assert abs(m["hit_rate_20d"] - (2.0 / 3.0)) < 1e-9
    # avg_return_20d: mean(-0.04, 0.06, 0.01) = 0.03/3 = 0.01
    assert abs(m["avg_return_20d"] - ((-0.04 + 0.06 + 0.01) / 3.0)) < 1e-9


def test_rolling_excess_return():
    """excess_return_20d over last window distinct dates."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.02, excess_ret=0.01),
        _row("2026-05-02", "BUY", 5, 0.04, excess_ret=0.02),
        _row("2026-05-03", "BUY", 5, 0.06, excess_ret=0.03),
    ]
    m = rolling_metrics(rows, window=2)
    # Last 2 dates: 2026-05-02, 2026-05-03
    assert abs(m["excess_return_20d"] - ((0.02 + 0.03) / 2.0)) < 1e-9


def test_rolling_empty_input():
    """Empty rows yield all-None metrics."""
    m = rolling_metrics([], window=20)
    assert m["hit_rate_20d"] is None
    assert m["avg_return_20d"] is None
    assert m["excess_return_20d"] is None
    assert m["sharpe_60d"] is None


def test_rolling_keys_always_present():
    """All four metric keys are always present."""
    m = rolling_metrics([], window=20)
    for k in ("hit_rate_20d", "avg_return_20d", "excess_return_20d", "sharpe_60d"):
        assert k in m


def test_rolling_non_long_excluded():
    """SELL / HOLD rows are excluded."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.10, hit=True),
        _row("2026-05-01", "SELL", 5, -0.05, hit=True),
        _row("2026-05-01", "HOLD", 5, 0.0, hit=None),
    ]
    m = rolling_metrics(rows, window=20)
    assert abs(m["hit_rate_20d"] - 1.0) < 1e-9
    assert abs(m["avg_return_20d"] - 0.10) < 1e-9


def test_rolling_sharpe_two_days():
    """sharpe_60d with exactly 2 dates is not None (std != 0)."""
    import numpy as np
    rows = [
        _row("2026-05-01", "BUY", 5, 0.02),
        _row("2026-05-02", "BUY", 5, 0.04),
    ]
    m = rolling_metrics(rows, window=60)
    rets = [0.02, 0.04]
    expected = np.mean(rets) / np.std(rets) * (252 ** 0.5)
    assert m["sharpe_60d"] is not None
    assert abs(m["sharpe_60d"] - expected) < 1e-9


def test_rolling_sharpe_single_day_is_none():
    """sharpe_60d is None when only 1 distinct date."""
    rows = [_row("2026-05-01", "BUY", 5, 0.02)]
    m = rolling_metrics(rows, window=60)
    assert m["sharpe_60d"] is None


def test_rolling_sharpe_zero_std_is_none():
    """sharpe_60d is None when std == 0 (constant returns)."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.02),
        _row("2026-05-02", "BUY", 5, 0.02),  # identical return
    ]
    m = rolling_metrics(rows, window=60)
    assert m["sharpe_60d"] is None


# ---------------------------------------------------------------------------
# build_reliability
# ---------------------------------------------------------------------------

def _pred_row(prob_up, realized_ret):
    return {"prob_up": prob_up, "realized_ret": realized_ret}


def test_reliability_keys_present():
    """Each bin has the correct keys."""
    pred_rows = [_pred_row(0.6, 0.01), _pred_row(0.4, -0.01), _pred_row(0.8, 0.03)]
    result = build_reliability(pred_rows, n_bins=5)
    assert "brier" in result
    assert "bins" in result
    assert len(result["bins"]) == 5
    for b in result["bins"]:
        for key in ("bin_low", "bin_high", "mean_prob", "frac_up", "count"):
            assert key in b


def test_reliability_brier_none_on_empty():
    """Empty pred_rows -> brier=None; bins still has n_bins entries."""
    result = build_reliability([], n_bins=10)
    assert result["brier"] is None
    assert len(result["bins"]) == 10
    for b in result["bins"]:
        assert b["count"] == 0


def test_reliability_brier_value():
    """Brier score is sensible (0 to 1)."""
    pred_rows = [_pred_row(0.8, 0.05), _pred_row(0.2, -0.03), _pred_row(0.6, 0.01)]
    result = build_reliability(pred_rows, n_bins=5)
    assert result["brier"] is not None
    assert 0.0 <= result["brier"] <= 1.0


def test_reliability_excludes_none_realized():
    """pred_rows with realized_ret None are dropped from brier/bins."""
    pred_rows = [_pred_row(0.6, 0.01), _pred_row(0.7, None), _pred_row(0.4, -0.02)]
    result = build_reliability(pred_rows, n_bins=5)
    assert sum(b["count"] for b in result["bins"]) == 2  # None-realized row dropped
    assert result["brier"] is not None


def test_reliability_bin_count_sum():
    """Sum of bin counts == number of pred_rows with valid prob."""
    n = 8
    pred_rows = [_pred_row(i / n, (i / n) - 0.5) for i in range(n)]
    result = build_reliability(pred_rows, n_bins=4)
    total = sum(b["count"] for b in result["bins"])
    assert total == n


def test_reliability_bin_boundaries():
    """bin_low/bin_high cover [0, 1] with n_bins equal-width buckets."""
    result = build_reliability([], n_bins=5)
    lows = [b["bin_low"] for b in result["bins"]]
    highs = [b["bin_high"] for b in result["bins"]]
    assert abs(lows[0] - 0.0) < 1e-9
    assert abs(highs[-1] - 1.0) < 1e-9
    # Each bucket width ~= 0.2
    for lo, hi in zip(lows, highs):
        assert abs((hi - lo) - 0.2) < 1e-9


# ---------------------------------------------------------------------------
# build_recent_outcomes
# ---------------------------------------------------------------------------

def test_recent_outcomes_sorted_desc_and_limited():
    """Sorted by entry_date DESC, limited to 'limit'."""
    rows = [_row(f"2026-05-{i:02d}", "BUY", 5, 0.01) for i in range(1, 11)]
    recent = build_recent_outcomes(rows, limit=5)
    assert len(recent) == 5
    dates = [r["entry_date"] for r in recent]
    assert dates == sorted(dates, reverse=True)


def test_recent_outcomes_key_set():
    """Each row has the required keys."""
    rows = [_row("2026-05-01", "BUY", 5, 0.02, benchmark_ret=0.01, excess_ret=0.01,
                  hit=True, mae=-0.005, mfe=0.025)]
    recent = build_recent_outcomes(rows, limit=10)
    assert len(recent) == 1
    expected_keys = {
        "entry_date", "ticker", "name", "action", "conviction", "horizon_days",
        "realized_ret", "benchmark_ret", "excess_ret", "hit", "mae", "mfe", "exit_reason",
    }
    assert expected_keys == set(recent[0].keys())


def test_recent_outcomes_includes_all_actions():
    """All action types are included (no filtering)."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.02),
        _row("2026-05-02", "SELL", 5, -0.01),
        _row("2026-05-03", "HOLD", 5, 0.0),
    ]
    recent = build_recent_outcomes(rows, limit=10)
    actions = {r["action"] for r in recent}
    assert actions == {"BUY", "SELL", "HOLD"}


def test_recent_outcomes_secondary_sort_by_ticker():
    """Within the same date, secondary sort is by ticker."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.01, ticker="9999.JP"),
        _row("2026-05-01", "BUY", 5, 0.02, ticker="1111.JP"),
        _row("2026-05-01", "BUY", 5, 0.03, ticker="5555.JP"),
    ]
    recent = build_recent_outcomes(rows, limit=10)
    tickers = [r["ticker"] for r in recent]
    assert tickers == sorted(tickers)


def test_recent_outcomes_empty():
    assert build_recent_outcomes([], limit=10) == []


# ---------------------------------------------------------------------------
# build_performance_detail
# ---------------------------------------------------------------------------

def test_build_performance_detail_structure_on_empty():
    """Empty rows and pred_rows -> empty curves, rolling all-None, reliability brier None."""
    result = build_performance_detail([], [], horizon=5, history_days=180, n_bins=10)
    assert result["horizon_days"] == 5
    assert result["history_days"] == 180
    assert result["equity_curve"] == []
    assert result["drawdown_curve"] == []
    m = result["rolling"]
    assert m["hit_rate_20d"] is None
    assert m["avg_return_20d"] is None
    assert m["excess_return_20d"] is None
    assert m["sharpe_60d"] is None
    rel = result["reliability"]
    assert rel["brier"] is None
    assert len(rel["bins"]) == 10


def test_build_performance_detail_keys_present():
    """All expected top-level keys are present."""
    result = build_performance_detail([], [], horizon=5, history_days=180, n_bins=10)
    for k in ("horizon_days", "history_days", "equity_curve", "drawdown_curve",
              "rolling", "reliability"):
        assert k in result


def test_build_performance_detail_with_data():
    """Non-empty data produces non-empty curves and non-None rolling."""
    rows = [
        _row("2026-05-01", "BUY", 5, 0.02, hit=True),
        _row("2026-05-02", "BUY", 5, -0.01, hit=False),
        _row("2026-05-03", "BUY", 5, 0.03, hit=True),
    ]
    pred_rows = [
        _pred_row(0.7, 0.02),
        _pred_row(0.3, -0.01),
        _pred_row(0.8, 0.03),
    ]
    result = build_performance_detail(rows, pred_rows, horizon=5, history_days=180, n_bins=5)
    assert len(result["equity_curve"]) == 3
    assert len(result["drawdown_curve"]) == 3
    assert result["rolling"]["hit_rate_20d"] is not None
    assert result["reliability"]["brier"] is not None


ALL_TESTS = [
    test_equity_curves_basic_strategy_and_benchmark,
    test_equity_curves_same_date_axis,
    test_equity_curves_benchmark_none_carry,
    test_equity_curves_filters_non_long_or_wrong_horizon,
    test_equity_curves_excludes_none_realized_ret,
    test_equity_curves_empty_input,
    test_equity_curves_ascending_dates,
    test_drawdown_values_nonpositive,
    test_drawdown_recovers_to_zero_after_new_peak,
    test_drawdown_exact_values,
    test_drawdown_first_day_negative_from_origin,
    test_drawdown_empty,
    test_drawdown_dates_match_curves,
    test_rolling_hit_rate_and_avg_return,
    test_rolling_excess_return,
    test_rolling_empty_input,
    test_rolling_keys_always_present,
    test_rolling_non_long_excluded,
    test_rolling_sharpe_two_days,
    test_rolling_sharpe_single_day_is_none,
    test_rolling_sharpe_zero_std_is_none,
    test_reliability_keys_present,
    test_reliability_brier_none_on_empty,
    test_reliability_brier_value,
    test_reliability_excludes_none_realized,
    test_reliability_bin_count_sum,
    test_reliability_bin_boundaries,
    test_recent_outcomes_sorted_desc_and_limited,
    test_recent_outcomes_key_set,
    test_recent_outcomes_includes_all_actions,
    test_recent_outcomes_secondary_sort_by_ticker,
    test_recent_outcomes_empty,
    test_build_performance_detail_structure_on_empty,
    test_build_performance_detail_keys_present,
    test_build_performance_detail_with_data,
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
