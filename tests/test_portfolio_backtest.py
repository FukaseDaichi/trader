#!/usr/bin/env python3
"""
Unit tests for src/portfolio_backtest.py (walk-forward long-only backtest).

Pure logic — synthetic inputs only, NO database or network.

Runnable two ways:
  TRADER_DB_ENABLED=false uv run python tests/test_portfolio_backtest.py   # standalone
  uv run pytest tests/test_portfolio_backtest.py                           # if pytest is present
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import portfolio_backtest as pbt  # noqa: E402
from src.backtest import evaluate_portfolio_kpi_gate, format_portfolio_gate_summary  # noqa: E402
from src.execution import EXECUTION_CONTRACT_VERSION  # noqa: E402

N_TICKERS = 30
N_DATES = 250
H = 5  # label horizon / rebalance spacing


# ---------------------------------------------------------------------------
# Synthetic data builders
# ---------------------------------------------------------------------------


def _tickers(n=N_TICKERS):
    return [f"{1000 + i}.JP" for i in range(n)]


def _sectors(tickers, n_sectors=5):
    return {tk: f"SEC{i % n_sectors}" for i, tk in enumerate(tickers)}


def _price_frames(tickers, n_rows=N_DATES, seed=0):
    """Per-ticker (date, close) gentle random walks; enough rows for cov."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_rows)
    frames = {}
    for tk in tickers:
        log_ret = rng.normal(0.0002, 0.012, size=n_rows)
        close = 1000.0 * np.exp(np.cumsum(log_ret))
        frames[tk] = pd.DataFrame({"date": dates, "close": close})
    return frames


def _oos_predictions(tickers, n_rows=N_DATES, seed=1, signal=0.04):
    """OOS frame with a planted cross-sectional signal.

    raw_score carries a per-date cross-sectional ranking signal; fwd_return is
    positively correlated with raw_score (plus noise) so the long-top-N strategy
    earns positive return. ``signal`` scales how strongly fwd_return tracks the
    standardized raw_score.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_rows)
    n = len(tickers)
    rows = []
    # The last H market-as-of rows have no executable H-session exit and would
    # be absent from a trained model's labelled OOS predictions.
    for date_idx, d in enumerate(dates[:-H]):
        # Cross-sectional raw scores for this date (distinct per ticker).
        raw = rng.normal(0.0, 1.0, size=n)
        # Standardize within the date so signal scaling is comparable.
        z = (raw - raw.mean()) / (raw.std() + 1e-9)
        noise = rng.normal(0.0, 0.01, size=n)
        fwd = signal * z + noise  # fwd_return tracks the score + noise
        for i, tk in enumerate(tickers):
            rows.append(
                {
                    "date": d,
                    "ticker": tk,
                    "raw_score": float(raw[i]),
                    "fwd_return": float(fwd[i]),
                    "target_up": int(fwd[i] > 0),
                    "target_vol_norm": 1.0,
                    "target_rank_bucket": int(min(4, max(0, (z[i] + 2) // 1))),
                    "market_as_of_date": d,
                    "entry_date": dates[date_idx + 1],
                    "execution_exit_date": dates[date_idx + H],
                    "execution_contract_version": EXECUTION_CONTRACT_VERSION,
                }
            )
    return pd.DataFrame(rows)


def _macro_panel(n_rows=N_DATES, seed=2, drift=0.0003, *, include_open=False):
    """Synthetic TOPIX panel; open is opt-in to model the current close-only feed."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_rows)
    topix = 2000.0 * np.exp(np.cumsum(rng.normal(drift, 0.008, size=n_rows)))
    panel = pd.DataFrame({"date": dates, "topix": topix})
    if include_open:
        panel["topix_open"] = topix * np.exp(rng.normal(0.0, 0.002, size=n_rows))
    return panel


def _config(**overrides):
    base = {
        "target_vol": 0.12,
        "max_name_weight": 0.20,
        "sector_cap": 0.40,
        "max_gross": 1.00,
        "min_weight": 0.03,
        "notrade_band": 0.02,
        "min_expected_ret": 0.0,
        "cov_lookback_days": 60,
        "top_n": 8,
    }
    base.update(overrides)
    return base


_METRIC_KEYS = {
    "cagr",
    "sharpe",
    "sortino",
    "max_drawdown",
    "calmar",
    "turnover",
    "turnover_annualized",
    "avg_gross",
    "capacity_proxy",
    "alpha",
    "beta",
    "information_ratio",
    "tracking_error",
    "hit_rate",
    "topn_realized_return",
    "n_periods",
}


def _finite_or_none(v) -> bool:
    return v is None or (isinstance(v, (int, float)) and math.isfinite(float(v)))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_backtest_runs_end_to_end():
    tickers = _tickers()
    frames = _price_frames(tickers, seed=4)
    oos = _oos_predictions(tickers, seed=5)
    macro = _macro_panel()
    res = pbt.run_portfolio_backtest(
        oos,
        frames,
        macro,
        _config(),
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert res["status"] == "ok", res
    assert res["n_periods"] >= 2, res["n_periods"]
    assert res["equity"], "expected non-empty equity curve"
    assert res["execution_contract"]["contract_version"] == EXECUTION_CONTRACT_VERSION
    assert res["benchmark_coverage"]["available"] is False
    # All metric keys present and finite-or-None.
    metrics = res["metrics"]
    assert _METRIC_KEYS.issubset(set(metrics)), set(metrics) ^ _METRIC_KEYS
    for k, v in metrics.items():
        assert _finite_or_none(v), (k, v)
    # Equity rows well-formed.
    for row in res["equity"]:
        assert set(row) >= {
            "date",
            "equity",
            "benchmark_equity",
            "gross_period_return",
            "cost_return",
            "period_return",
            "gross_benchmark_return",
            "benchmark_cost_return",
            "benchmark_return",
            "drawdown",
            "gross_exposure",
            "entry_turnover",
            "exit_turnover",
            "terminal_exit_turnover",
            "turnover",
            "benchmark_turnover",
            "decision_date",
            "entry_date",
            "exit_date",
        }
        # Date is YYYY-MM-DD.
        assert len(row["date"]) == 10 and row["date"][4] == "-"
    # n_periods consistent.
    assert metrics["n_periods"] == len(res["equity"]) == res["n_periods"]


def test_backtest_positive_signal_beats_cash():
    tickers = _tickers()
    frames = _price_frames(tickers, seed=6)
    # Strong planted signal -> long-top-N should earn positive return.
    oos = _oos_predictions(tickers, seed=7, signal=0.06)
    macro = _macro_panel()
    res = pbt.run_portfolio_backtest(
        oos,
        frames,
        macro,
        _config(),
        sectors=_sectors(tickers),
        label_horizon_days=H,
        cost_bps=5.0,
        slippage_bps=2.0,
    )
    assert res["status"] == "ok", res
    final_equity = res["equity"][-1]["equity"]
    assert final_equity > 1.0, final_equity
    assert res["metrics"]["topn_realized_return"] > 0, res["metrics"][
        "topn_realized_return"
    ]
    assert res["metrics"]["hit_rate"] > 0.5, res["metrics"]["hit_rate"]


def test_backtest_turnover_and_costs():
    tickers = _tickers()
    frames = _price_frames(tickers, seed=8)
    oos = _oos_predictions(tickers, seed=9, signal=0.05)
    macro = _macro_panel()
    cfg = _config()

    zero_cost = pbt.run_portfolio_backtest(
        oos,
        frames,
        macro,
        cfg,
        sectors=_sectors(tickers),
        label_horizon_days=H,
        cost_bps=0.0,
        slippage_bps=0.0,
    )
    high_cost = pbt.run_portfolio_backtest(
        oos,
        frames,
        macro,
        cfg,
        sectors=_sectors(tickers),
        label_horizon_days=H,
        cost_bps=50.0,
        slippage_bps=25.0,
    )
    assert zero_cost["status"] == "ok" and high_cost["status"] == "ok"
    eq_zero = zero_cost["equity"][-1]["equity"]
    eq_high = high_cost["equity"][-1]["equity"]
    # Costs reduce returns: higher cost_bps -> lower final equity.
    assert eq_high < eq_zero, (eq_high, eq_zero)
    # And there is real turnover to be charged.
    assert zero_cost["metrics"]["turnover"] >= 0.0
    assert high_cost["metrics"]["turnover"] > 0.0, high_cost["metrics"]["turnover"]


def test_nonoverlap_turnover_charges_full_exit_and_entry():
    tickers = _tickers(10)
    cost_bps = 10.0
    slippage_bps = 5.0
    result = pbt.run_portfolio_backtest(
        _oos_predictions(tickers, n_rows=40, seed=90),
        _price_frames(tickers, n_rows=40, seed=91),
        _macro_panel(n_rows=40, include_open=True),
        _config(top_n=5),
        sectors=_sectors(tickers),
        label_horizon_days=H,
        cost_bps=cost_bps,
        slippage_bps=slippage_bps,
    )
    assert result["status"] == "ok", result
    rows = result["equity"]
    cost_rate = (cost_bps + slippage_bps) / 10000.0

    for idx, row in enumerate(rows):
        expected_previous_exit = 0.0 if idx == 0 else rows[idx - 1]["gross_exposure"]
        assert abs(row["exit_turnover"] - expected_previous_exit) < 1e-12, row
        assert abs(row["entry_turnover"] - row["gross_exposure"]) < 1e-12, row
        expected_terminal_exit = row["gross_exposure"] if idx == len(rows) - 1 else 0.0
        assert abs(row["terminal_exit_turnover"] - expected_terminal_exit) < 1e-12
        expected_turnover = (
            row["exit_turnover"] + row["entry_turnover"] + row["terminal_exit_turnover"]
        )
        assert abs(row["turnover"] - expected_turnover) < 1e-12, row
        assert abs(row["cost_return"] - row["turnover"] * cost_rate) < 1e-12
        assert (
            abs(
                row["period_return"] - (row["gross_period_return"] - row["cost_return"])
            )
            < 1e-12
        )

    # Each book's full notional is charged once on entry and once on exit.
    assert (
        abs(
            sum(row["turnover"] for row in rows)
            - 2.0 * sum(row["gross_exposure"] for row in rows)
        )
        < 1e-12
    )


def test_backtest_no_lookahead_cov():
    """The first rebalance's weights must not depend on LATER-than-d prices.

    We run the backtest once with the full price panel, then re-run with every
    price frame TRUNCATED so any close dated after the first rebalance date is
    removed. Because the covariance at rebalance d is computed as-of (date <= d),
    the first period's weights/return/turnover must be byte-identical between the
    two runs. If there were look-ahead, blanking the future would change the
    first period.
    """
    tickers = _tickers()
    frames = _price_frames(tickers, seed=10)
    oos = _oos_predictions(tickers, seed=11, signal=0.05)
    macro = _macro_panel()
    cfg = _config()

    full = pbt.run_portfolio_backtest(
        oos,
        frames,
        macro,
        cfg,
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert full["status"] == "ok"
    first_date = pd.Timestamp(full["equity"][0]["date"])

    # Truncate every frame to date <= first rebalance date (drop the future).
    truncated = {
        tk: f[pd.to_datetime(f["date"]) <= first_date].reset_index(drop=True)
        for tk, f in frames.items()
    }
    trunc = pbt.run_portfolio_backtest(
        oos,
        truncated,
        macro,
        cfg,
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert trunc["status"] == "ok"

    # First period identical (no look-ahead in the as-of covariance).
    a, b = full["equity"][0], trunc["equity"][0]
    assert a["date"] == b["date"]
    assert abs(a["period_return"] - b["period_return"]) < 1e-12, (a, b)
    assert abs(a["turnover"] - b["turnover"]) < 1e-12, (a, b)
    assert abs(a["gross_exposure"] - b["gross_exposure"]) < 1e-12, (a, b)


def test_backtest_benchmark_alpha_beta():
    tickers = _tickers()
    frames = _price_frames(tickers, seed=12)
    oos = _oos_predictions(tickers, seed=13, signal=0.05)
    cfg = _config()

    # The production macro feed is close-only. It is not a valid v2 benchmark
    # and must not be silently treated as cash or a close-to-close proxy.
    close_only = pbt.run_portfolio_backtest(
        oos,
        frames,
        _macro_panel(),
        cfg,
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert close_only["status"] == "ok"
    assert close_only["benchmark_coverage"]["available"] is False
    assert close_only["benchmark_coverage"]["available_periods"] == 0
    assert close_only["benchmark_coverage"]["reason"] == (
        "topix_open_unavailable_same_basis"
    )
    for row in close_only["equity"]:
        assert row["benchmark_return"] is None, row
        assert row["benchmark_equity"] is None, row
    for key in ("alpha", "beta", "information_ratio", "tracking_error"):
        assert close_only["metrics"][key] is None, (key, close_only["metrics"])

    gate = evaluate_portfolio_kpi_gate(close_only, _gate_config())
    assert gate["passed"] is False, gate
    assert any("ir" in failure for failure in gate["failures"]), gate["failures"]

    # Future-compatible exact TOPIX open/close coverage produces comparison
    # metrics without changing the required basis.
    with_exact_bench = pbt.run_portfolio_backtest(
        oos,
        frames,
        _macro_panel(include_open=True),
        cfg,
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert with_exact_bench["benchmark_coverage"]["available"] is True
    contract = with_exact_bench["execution_contract"]
    assert contract["return_basis"] == "net_after_entry_exit_costs"
    assert contract["cost_treatment"] == (
        "deducted_from_portfolio_and_benchmark_returns"
    )
    assert contract["benchmark_return_basis"] == ("net_after_same_entry_exit_costs")
    assert contract["round_trip_cost_rate"] == 0.003
    exact_rows = with_exact_bench["equity"]
    assert (
        abs(sum(row["benchmark_turnover"] for row in exact_rows) - 2 * len(exact_rows))
        < 1e-12
    )
    for row in exact_rows:
        assert row["gross_benchmark_return"] is not None
        assert (
            abs(
                row["benchmark_return"]
                - (row["gross_benchmark_return"] - row["benchmark_cost_return"])
            )
            < 1e-12
        )
    m = with_exact_bench["metrics"]
    assert isinstance(m["beta"], float) and math.isfinite(m["beta"]), m["beta"]
    assert isinstance(m["information_ratio"], float) and math.isfinite(
        m["information_ratio"]
    ), m["information_ratio"]

    # macro_panel=None follows the same unavailable contract.
    no_bench = pbt.run_portfolio_backtest(
        oos,
        frames,
        None,
        cfg,
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert no_bench["status"] == "ok"
    for row in no_bench["equity"]:
        assert row["benchmark_return"] is None, row
        assert row["benchmark_equity"] is None, row
    assert no_bench["metrics"]["beta"] is None


def test_macro_panel_with_open_feeds_benchmark_preparation():
    """End-to-end contract: the producer (src.macro.build_macro_panel) and the
    consumer (_prepare_topix / _benchmark_return) must agree on the same-basis
    benchmark columns. Each side is tested in isolation elsewhere; this is the
    test that fails if they drift apart."""
    from src.macro import build_macro_panel

    dates = pd.bdate_range("2026-01-05", periods=10)
    top = pd.DataFrame(
        {
            "date": dates,
            "close": [2800.0 + i * 5 for i in range(10)],
            "open": [2795.0 + i * 5 for i in range(10)],
        }
    )

    panel = build_macro_panel({"topix": top})
    prepared = pbt._prepare_topix(panel)
    assert prepared is not None
    assert list(prepared.columns) == ["date", "topix_open", "topix"]
    assert len(prepared) == 10

    # entry open -> exit close, exactly the v2 execution contract
    ret = pbt._benchmark_return(prepared, dates[1], dates[5])
    expected = (2800.0 + 5 * 5) / (2795.0 + 1 * 5) - 1.0
    assert abs(ret - expected) < 1e-12

    # a close-only panel stays fail-closed rather than substituting a basis
    close_only = build_macro_panel({"topix": top[["date", "close"]]})
    assert pbt._prepare_topix(close_only) is None


def test_partial_topix_open_coverage_is_incomplete_and_ir_stays_none():
    """A same-basis benchmark that's missing topix_open on SOME dates (a
    provider gap, or the per-date NaN Task 4 introduced for isolated bad
    values) must not be silently treated as full coverage. This state was
    unreachable before Task 4 -- an invalid open used to kill the whole
    series -- and had no test at the backtest level until now."""
    tickers = _tickers()
    frames = _price_frames(tickers, seed=12)
    oos = _oos_predictions(tickers, seed=13, signal=0.05)
    cfg = _config()

    macro = _macro_panel(include_open=True)
    # Null every 3rd date's open so at least some (but not all) rebalance
    # periods lose their benchmark, regardless of exact entry/exit alignment.
    macro.loc[macro.index[::3], "topix_open"] = np.nan

    result = pbt.run_portfolio_backtest(
        oos,
        frames,
        macro,
        cfg,
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert result["status"] == "ok"
    coverage = result["benchmark_coverage"]
    assert coverage["available"] is False
    assert coverage["reason"] == "incomplete_same_basis_coverage"
    assert 0.0 < coverage["coverage_ratio"] < 1.0, coverage
    assert result["metrics"]["information_ratio"] is None

    gate = evaluate_portfolio_kpi_gate(result, _gate_config())
    assert gate["passed"] is False, gate
    assert any("ir" in failure for failure in gate["failures"]), gate["failures"]


def test_backtest_execution_windows_do_not_overlap():
    tickers = _tickers()
    res = pbt.run_portfolio_backtest(
        _oos_predictions(tickers, seed=33),
        _price_frames(tickers, seed=34),
        _macro_panel(include_open=True),
        _config(),
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert res["status"] == "ok"
    rows = res["equity"]
    for row in rows:
        decision = pd.Timestamp(row["decision_date"])
        entry = pd.Timestamp(row["entry_date"])
        exit_ = pd.Timestamp(row["exit_date"])
        assert decision < entry <= exit_
    for previous, current in zip(rows, rows[1:]):
        assert pd.Timestamp(current["entry_date"]) > pd.Timestamp(previous["exit_date"])


def test_cross_execution_provenance_mismatch_excludes_period():
    tickers = _tickers(10)
    base_oos = _oos_predictions(tickers, n_rows=40, seed=92)
    frames = _price_frames(tickers, n_rows=40, seed=93)
    macro = _macro_panel(n_rows=40, include_open=True)
    first_date = base_oos["date"].min()
    first_idx = base_oos.index[base_oos["date"] == first_date][0]
    cases = [
        (
            "execution_contract_version",
            "close_to_close_v1",
            "execution_contract_mismatch",
        ),
        (
            "market_as_of_date",
            pd.Timestamp(first_date) + pd.Timedelta(days=1),
            "market_as_of_date_inconsistent",
        ),
        (
            "entry_date",
            pd.Timestamp(base_oos.loc[first_idx, "entry_date"]) + pd.Timedelta(days=1),
            "entry_date_inconsistent",
        ),
        (
            "execution_exit_date",
            pd.Timestamp(base_oos.loc[first_idx, "execution_exit_date"])
            + pd.Timedelta(days=1),
            "exit_date_inconsistent",
        ),
    ]

    for column, invalid_value, expected_reason in cases:
        oos = base_oos.copy()
        oos.loc[first_idx, column] = invalid_value
        result = pbt.run_portfolio_backtest(
            oos,
            frames,
            macro,
            _config(top_n=5),
            sectors=_sectors(tickers),
            label_horizon_days=H,
        )
        assert result["status"] == "ok", (column, result)
        quality = result["data_quality"]
        assert quality["excluded_periods"] == 1, (column, quality)
        assert quality["exclusions"][0]["reason"] == expected_reason, (
            column,
            quality,
        )
        assert result["start_date"] != pd.Timestamp(first_date).strftime("%Y-%m-%d")


def test_overlapping_window_and_duplicate_cross_rows_fail_closed():
    tickers = _tickers(10)
    base_oos = _oos_predictions(tickers, n_rows=40, seed=98)
    frames = _price_frames(tickers, n_rows=40, seed=99)
    macro = _macro_panel(n_rows=40, include_open=True)
    picked_dates = pbt._thin_rebalance_dates(sorted(base_oos["date"].unique()), H)

    overlapping = base_oos.copy()
    first_date, second_date = picked_dates[:2]
    second_entry = overlapping.loc[
        overlapping["date"] == second_date, "entry_date"
    ].iloc[0]
    overlapping.loc[overlapping["date"] == first_date, "execution_exit_date"] = (
        second_entry
    )
    overlap_result = pbt.run_portfolio_backtest(
        overlapping,
        frames,
        macro,
        _config(top_n=5),
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert overlap_result["status"] == "ok", overlap_result
    assert "overlapping_execution_window" in {
        item["reason"] for item in overlap_result["data_quality"]["exclusions"]
    }

    duplicated = base_oos.copy()
    first_indices = duplicated.index[duplicated["date"] == first_date]
    duplicated.loc[first_indices[1], "ticker"] = duplicated.loc[
        first_indices[0], "ticker"
    ]
    duplicate_result = pbt.run_portfolio_backtest(
        duplicated,
        frames,
        macro,
        _config(top_n=5),
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert duplicate_result["status"] == "ok", duplicate_result
    assert duplicate_result["data_quality"]["exclusions"][0]["reason"] == (
        "duplicate_ticker"
    )


def test_all_contract_mismatch_is_insufficient_not_cash():
    tickers = _tickers(10)
    oos = _oos_predictions(tickers, n_rows=30, seed=94)
    oos["execution_contract_version"] = "close_to_close_v1"
    result = pbt.run_portfolio_backtest(
        oos,
        _price_frames(tickers, n_rows=30, seed=95),
        _macro_panel(n_rows=30, include_open=True),
        _config(top_n=5),
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert result["status"] == "insufficient", result
    assert result["reason"] == "insufficient_valid_periods"
    assert result["n_periods"] == 0
    assert result["equity"] == []
    assert result["data_quality"]["excluded_periods"] >= 2
    assert {item["reason"] for item in result["data_quality"]["exclusions"]} == {
        "execution_contract_mismatch"
    }


def test_selected_missing_forward_return_is_excluded_not_zero():
    tickers = _tickers(10)
    oos = _oos_predictions(tickers, n_rows=30, seed=96)
    dates = sorted(oos["date"].unique())
    for decision_date in pbt._thin_rebalance_dates(dates, H):
        cross_indices = oos.index[oos["date"] == decision_date]
        top_index = oos.loc[cross_indices, "raw_score"].idxmax()
        oos.loc[top_index, "fwd_return"] = np.nan

    result = pbt.run_portfolio_backtest(
        oos,
        _price_frames(tickers, n_rows=30, seed=97),
        _macro_panel(n_rows=30, include_open=True),
        _config(top_n=5),
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert result["status"] == "insufficient", result
    assert result["equity"] == []
    assert {item["reason"] for item in result["data_quality"]["exclusions"]} == {
        "selected_fwd_return_unavailable"
    }


def test_backtest_insufficient_periods():
    tickers = _tickers()
    frames = _price_frames(tickers, seed=14)
    # OOS frame with a SINGLE date -> cannot form >= 2 rebalances.
    one_date = pd.bdate_range("2024-01-01", periods=1)[0]
    rows = [
        {"date": one_date, "ticker": tk, "raw_score": float(i), "fwd_return": 0.01}
        for i, tk in enumerate(tickers)
    ]
    oos = pd.DataFrame(rows)
    res = pbt.run_portfolio_backtest(
        oos,
        frames,
        _macro_panel(),
        _config(),
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert res["status"] == "insufficient", res
    assert res["metrics"] == {}
    assert isinstance(res["equity"], list)


def test_single_valid_period_writes_json_safe_unavailable_report():
    tickers = _tickers(10)
    oos = _oos_predictions(tickers, n_rows=30, seed=100)
    picked_dates = pbt._thin_rebalance_dates(sorted(oos["date"].unique()), H)
    assert len(picked_dates) >= 2

    # Keep exactly one valid period while leaving multiple candidate dates.
    oos["execution_contract_version"] = "close_to_close_v1"
    oos.loc[oos["date"] == picked_dates[0], "execution_contract_version"] = (
        EXECUTION_CONTRACT_VERSION
    )
    result = pbt.run_portfolio_backtest(
        oos,
        _price_frames(tickers, n_rows=30, seed=101),
        _macro_panel(n_rows=30, include_open=True),
        _config(top_n=5),
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )

    assert result["status"] == "insufficient", result
    assert result["reason"] == "insufficient_valid_periods"
    assert result["data_quality"]["candidate_periods"] == len(picked_dates)
    assert result["data_quality"]["valid_periods"] == 1
    assert result["data_quality"]["excluded_periods"] == len(picked_dates) - 1
    assert result["n_periods"] == 0
    assert result["equity"] == []

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "portfolio_backtest.json"
        pbt.write_portfolio_backtest_report(result, output_path=output)
        payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["available"] is False
    assert payload["reason"] == "insufficient_valid_periods"
    assert payload["n_periods"] == 0
    assert payload["equity"] == []


def test_write_report_roundtrip():
    tickers = _tickers()
    frames = _price_frames(tickers, seed=15)
    oos = _oos_predictions(tickers, seed=16, signal=0.05)
    res = pbt.run_portfolio_backtest(
        oos,
        frames,
        _macro_panel(),
        _config(),
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert res["status"] == "ok"

    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "portfolio_backtest.json")
        path = pbt.write_portfolio_backtest_report(
            res,
            output_path=out,
            model_version="cs-v1-test",
            run_date="2026-06-10",
            generated_at="2026-06-10T06:00:00Z",
        )
        assert path == out
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["available"] is True
        assert data["model_version"] == "cs-v1-test"
        assert data["run_date"] == "2026-06-10"
        assert data["generated_at"] == "2026-06-10T06:00:00Z"
        assert "metrics" in data and "sharpe" in data["metrics"]
        assert data["status"] == "ok"
        assert data["execution_contract"]["contract_version"] == (
            EXECUTION_CONTRACT_VERSION
        )
        assert "benchmark_coverage" in data

        # Insufficient result -> available: false.
        insuff = {"status": "insufficient", "metrics": {}, "equity": []}
        out2 = str(Path(tmp) / "insufficient.json")
        pbt.write_portfolio_backtest_report(insuff, output_path=out2)
        data2 = json.loads(Path(out2).read_text(encoding="utf-8"))
        assert data2["available"] is False, data2
        assert data2["reason"] == "insufficient"

        # None result -> available: false, no crash.
        out3 = str(Path(tmp) / "none.json")
        pbt.write_portfolio_backtest_report(None, output_path=out3)
        data3 = json.loads(Path(out3).read_text(encoding="utf-8"))
        assert data3["available"] is False, data3


def test_write_report_embeds_gate_and_reader_honors_it():
    """Issue #2: the weekly report must carry the evaluated KPI gate so
    read_portfolio_gate() (active-mode safety + active_readiness) checks the
    actual pass/fail instead of mere report existence."""
    from src.portfolio import read_portfolio_gate

    expected_model_version = "cs-v1-gate-test"

    ok_result = {
        "status": "ok",
        "n_periods": 10,
        "metrics": {"sharpe": 0.1},
        "equity": [],
        "params": {},
        "execution_contract": {
            "contract_version": EXECUTION_CONTRACT_VERSION,
            "return_basis": "net_after_entry_exit_costs",
            "benchmark_return_basis": "net_after_same_entry_exit_costs",
            "round_trip_cost_rate": 0.003,
        },
        "benchmark_coverage": {
            "available": True,
            "return_basis": "net_after_same_entry_exit_costs",
            "total_periods": 10,
            "available_periods": 10,
            "coverage_ratio": 1.0,
            "reason": None,
        },
    }

    with tempfile.TemporaryDirectory() as tmp:
        # Failing gate on an available backtest -> gate key present, reader False.
        out = str(Path(tmp) / "portfolio_backtest.json")
        failing_gate = {
            "passed": False,
            "skipped": False,
            "reason": "kpi_failed",
            "metrics": {"sharpe": 0.1},
            "failures": ["sharpe 0.10 < min 0.30"],
        }
        pbt.write_portfolio_backtest_report(
            ok_result,
            output_path=out,
            gate=failing_gate,
            model_version=expected_model_version,
        )
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["available"] is True
        assert data["gate"] == {"passed": False, "failures": ["sharpe 0.10 < min 0.30"]}
        assert (
            read_portfolio_gate(out, expected_model_version=expected_model_version)
            is False
        )

        # Passing gate -> reader True.
        out2 = str(Path(tmp) / "passing.json")
        passing_gate = {
            "passed": True,
            "skipped": False,
            "reason": "ok",
            "metrics": {"sharpe": 0.9},
            "failures": [],
        }
        pbt.write_portfolio_backtest_report(
            ok_result,
            output_path=out2,
            gate=passing_gate,
            model_version=expected_model_version,
        )
        data2 = json.loads(Path(out2).read_text(encoding="utf-8"))
        assert data2["gate"] == {"passed": True, "failures": []}
        assert (
            read_portfolio_gate(out2, expected_model_version=expected_model_version)
            is True
        )
        assert read_portfolio_gate(out2, expected_model_version="cs-v1-stale") is False

        # No gate supplied -> no gate key; legacy availability is rejected.
        out3 = str(Path(tmp) / "legacy.json")
        pbt.write_portfolio_backtest_report(
            ok_result, output_path=out3, model_version=expected_model_version
        )
        data3 = json.loads(Path(out3).read_text(encoding="utf-8"))
        assert "gate" not in data3
        assert (
            read_portfolio_gate(out3, expected_model_version=expected_model_version)
            is False
        )


# ---------------------------------------------------------------------------
# Portfolio KPI gate tests
# ---------------------------------------------------------------------------


def _gate_config(**overrides):
    """Threshold config matching get_portfolio_config() defaults."""
    base = {
        "backtest_min_sharpe": 0.30,
        "backtest_max_dd": 0.25,
        "backtest_min_ir": 0.00,
        "backtest_max_turnover": 0.40,
    }
    base.update(overrides)
    return base


def _ok_result(
    sharpe=0.80, max_drawdown=-0.10, information_ratio=0.50, turnover=0.20, cagr=0.12
):
    """Passing-metrics result stub."""
    return {
        "status": "ok",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "n_periods": 50,
        "metrics": {
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "information_ratio": information_ratio,
            "turnover": turnover,
            "cagr": cagr,
        },
    }


def test_gate_passes_when_all_metrics_ok():
    result = _ok_result()
    gate = evaluate_portfolio_kpi_gate(result, _gate_config())
    assert gate["passed"] is True, gate
    assert gate["reason"] == "ok"
    assert gate["failures"] == []
    assert gate["skipped"] is False


def test_gate_fails_on_max_dd_breach():
    # max_drawdown = -0.30 -> abs = 0.30 > threshold 0.25
    result = _ok_result(max_drawdown=-0.30)
    gate = evaluate_portfolio_kpi_gate(result, _gate_config(backtest_max_dd=0.25))
    assert gate["passed"] is False, gate
    assert any("max_dd" in f for f in gate["failures"]), gate["failures"]


def test_gate_fails_on_low_sharpe():
    result = _ok_result(sharpe=0.10)
    gate = evaluate_portfolio_kpi_gate(result, _gate_config(backtest_min_sharpe=0.30))
    assert gate["passed"] is False, gate
    assert any("sharpe" in f for f in gate["failures"]), gate["failures"]


def test_gate_fails_on_low_ir():
    # IR = -0.05 < threshold 0.00
    result = _ok_result(information_ratio=-0.05)
    gate = evaluate_portfolio_kpi_gate(result, _gate_config(backtest_min_ir=0.00))
    assert gate["passed"] is False, gate
    assert any("ir" in f for f in gate["failures"]), gate["failures"]


def test_gate_fails_on_high_turnover():
    result = _ok_result(turnover=0.50)
    gate = evaluate_portfolio_kpi_gate(result, _gate_config(backtest_max_turnover=0.40))
    assert gate["passed"] is False, gate
    assert any("turnover" in f for f in gate["failures"]), gate["failures"]


def test_gate_insufficient_status_not_passed():
    result = {"status": "insufficient", "metrics": {}, "equity": []}
    gate = evaluate_portfolio_kpi_gate(result, _gate_config())
    assert gate["passed"] is False
    assert "insufficient" in gate["reason"]


def test_gate_none_result_not_passed():
    gate = evaluate_portfolio_kpi_gate(None, _gate_config())
    assert gate["passed"] is False


def test_gate_none_metrics_fields_fail():
    # Every required unavailable metric has an explicit fail-closed reason.
    result = _ok_result()
    result["metrics"] = {
        "sharpe": None,
        "max_drawdown": None,
        "information_ratio": None,
        "turnover": None,
        "cagr": 0.12,
    }
    gate = evaluate_portfolio_kpi_gate(result, _gate_config())
    assert gate["passed"] is False
    assert gate["failures"] == [
        "max_dd_unavailable",
        "sharpe_unavailable",
        "ir_unavailable_same_basis",
        "turnover_unavailable",
    ]


def test_gate_nonfinite_metrics_fail_closed():
    result = _ok_result(
        sharpe=float("nan"),
        max_drawdown=float("inf"),
        information_ratio=float("nan"),
        turnover=float("inf"),
    )
    gate = evaluate_portfolio_kpi_gate(result, _gate_config())
    assert gate["passed"] is False
    assert gate["failures"] == [
        "max_dd_unavailable",
        "sharpe_unavailable",
        "ir_unavailable_same_basis",
        "turnover_unavailable",
    ]


def test_gate_summary_is_string_with_pass_fail():
    passing = evaluate_portfolio_kpi_gate(_ok_result(), _gate_config())
    s = format_portfolio_gate_summary(passing)
    assert isinstance(s, str)
    assert "PASS" in s
    assert "Sharpe" in s

    failing = evaluate_portfolio_kpi_gate(_ok_result(sharpe=0.05), _gate_config())
    sf = format_portfolio_gate_summary(failing)
    assert "FAIL" in sf


def test_gate_with_real_backtest_result():
    """Close-only TOPIX data makes the real-result gate fail closed on IR."""
    tickers = _tickers()
    frames = _price_frames(tickers, seed=20)
    oos = _oos_predictions(tickers, seed=21, signal=0.05)
    macro = _macro_panel()
    res = pbt.run_portfolio_backtest(
        oos,
        frames,
        macro,
        _config(),
        sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert res["status"] == "ok"
    gate = evaluate_portfolio_kpi_gate(res, _gate_config())
    assert gate["passed"] is False, gate
    assert any("ir" in failure for failure in gate["failures"]), gate["failures"]
    assert isinstance(gate["failures"], list)
    assert gate["metrics"] is res["metrics"]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_TESTS = [
    test_backtest_runs_end_to_end,
    test_backtest_positive_signal_beats_cash,
    test_backtest_turnover_and_costs,
    test_nonoverlap_turnover_charges_full_exit_and_entry,
    test_backtest_no_lookahead_cov,
    test_backtest_benchmark_alpha_beta,
    test_macro_panel_with_open_feeds_benchmark_preparation,
    test_partial_topix_open_coverage_is_incomplete_and_ir_stays_none,
    test_backtest_execution_windows_do_not_overlap,
    test_cross_execution_provenance_mismatch_excludes_period,
    test_overlapping_window_and_duplicate_cross_rows_fail_closed,
    test_all_contract_mismatch_is_insufficient_not_cash,
    test_selected_missing_forward_return_is_excluded_not_zero,
    test_backtest_insufficient_periods,
    test_single_valid_period_writes_json_safe_unavailable_report,
    test_write_report_roundtrip,
    test_write_report_embeds_gate_and_reader_honors_it,
    test_gate_passes_when_all_metrics_ok,
    test_gate_fails_on_max_dd_breach,
    test_gate_fails_on_low_sharpe,
    test_gate_fails_on_low_ir,
    test_gate_fails_on_high_turnover,
    test_gate_insufficient_status_not_passed,
    test_gate_none_result_not_passed,
    test_gate_none_metrics_fields_fail,
    test_gate_nonfinite_metrics_fail_closed,
    test_gate_summary_is_string_with_pass_fail,
    test_gate_with_real_backtest_result,
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
