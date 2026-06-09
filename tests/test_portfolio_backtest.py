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
    for d in dates:
        # Cross-sectional raw scores for this date (distinct per ticker).
        raw = rng.normal(0.0, 1.0, size=n)
        # Standardize within the date so signal scaling is comparable.
        z = (raw - raw.mean()) / (raw.std() + 1e-9)
        noise = rng.normal(0.0, 0.01, size=n)
        fwd = signal * z + noise  # fwd_return tracks the score + noise
        for i, tk in enumerate(tickers):
            rows.append({
                "date": d,
                "ticker": tk,
                "raw_score": float(raw[i]),
                "fwd_return": float(fwd[i]),
                "target_up": int(fwd[i] > 0),
                "target_vol_norm": 1.0,
                "target_rank_bucket": int(min(4, max(0, (z[i] + 2) // 1))),
            })
    return pd.DataFrame(rows)


def _macro_panel(n_rows=N_DATES, seed=2, drift=0.0003):
    """Synthetic macro panel with date + topix (mild uptrend)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_rows)
    topix = 2000.0 * np.exp(np.cumsum(rng.normal(drift, 0.008, size=n_rows)))
    return pd.DataFrame({"date": dates, "topix": topix})


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
    "cagr", "sharpe", "sortino", "max_drawdown", "calmar", "turnover",
    "turnover_annualized", "avg_gross", "capacity_proxy", "alpha", "beta",
    "information_ratio", "tracking_error", "hit_rate", "topn_realized_return",
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
        oos, frames, macro, _config(), sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert res["status"] == "ok", res
    assert res["n_periods"] >= 2, res["n_periods"]
    assert res["equity"], "expected non-empty equity curve"
    # All metric keys present and finite-or-None.
    metrics = res["metrics"]
    assert _METRIC_KEYS.issubset(set(metrics)), set(metrics) ^ _METRIC_KEYS
    for k, v in metrics.items():
        assert _finite_or_none(v), (k, v)
    # Equity rows well-formed.
    for row in res["equity"]:
        assert set(row) >= {
            "date", "equity", "benchmark_equity", "period_return",
            "benchmark_return", "drawdown", "gross_exposure", "turnover",
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
        oos, frames, macro, _config(), sectors=_sectors(tickers),
        label_horizon_days=H, cost_bps=5.0, slippage_bps=2.0,
    )
    assert res["status"] == "ok", res
    final_equity = res["equity"][-1]["equity"]
    assert final_equity > 1.0, final_equity
    assert res["metrics"]["topn_realized_return"] > 0, res["metrics"]["topn_realized_return"]
    assert res["metrics"]["hit_rate"] > 0.5, res["metrics"]["hit_rate"]


def test_backtest_turnover_and_costs():
    tickers = _tickers()
    frames = _price_frames(tickers, seed=8)
    oos = _oos_predictions(tickers, seed=9, signal=0.05)
    macro = _macro_panel()
    cfg = _config()

    zero_cost = pbt.run_portfolio_backtest(
        oos, frames, macro, cfg, sectors=_sectors(tickers),
        label_horizon_days=H, cost_bps=0.0, slippage_bps=0.0,
    )
    high_cost = pbt.run_portfolio_backtest(
        oos, frames, macro, cfg, sectors=_sectors(tickers),
        label_horizon_days=H, cost_bps=50.0, slippage_bps=25.0,
    )
    assert zero_cost["status"] == "ok" and high_cost["status"] == "ok"
    eq_zero = zero_cost["equity"][-1]["equity"]
    eq_high = high_cost["equity"][-1]["equity"]
    # Costs reduce returns: higher cost_bps -> lower final equity.
    assert eq_high < eq_zero, (eq_high, eq_zero)
    # And there is real turnover to be charged.
    assert zero_cost["metrics"]["turnover"] >= 0.0
    assert high_cost["metrics"]["turnover"] > 0.0, high_cost["metrics"]["turnover"]


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
        oos, frames, macro, cfg, sectors=_sectors(tickers), label_horizon_days=H,
    )
    assert full["status"] == "ok"
    first_date = pd.Timestamp(full["equity"][0]["date"])

    # Truncate every frame to date <= first rebalance date (drop the future).
    truncated = {
        tk: f[pd.to_datetime(f["date"]) <= first_date].reset_index(drop=True)
        for tk, f in frames.items()
    }
    trunc = pbt.run_portfolio_backtest(
        oos, truncated, macro, cfg, sectors=_sectors(tickers), label_horizon_days=H,
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

    # With benchmark: beta + IR are finite floats.
    with_bench = pbt.run_portfolio_backtest(
        oos, frames, _macro_panel(), cfg, sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert with_bench["status"] == "ok"
    m = with_bench["metrics"]
    assert isinstance(m["beta"], float) and math.isfinite(m["beta"]), m["beta"]
    assert isinstance(m["information_ratio"], float) and math.isfinite(m["information_ratio"]), \
        m["information_ratio"]

    # macro_panel=None: benchmark returns are 0, run still completes.
    no_bench = pbt.run_portfolio_backtest(
        oos, frames, None, cfg, sectors=_sectors(tickers), label_horizon_days=H,
    )
    assert no_bench["status"] == "ok"
    for row in no_bench["equity"]:
        assert row["benchmark_return"] == 0.0, row
        assert row["benchmark_equity"] == 1.0, row  # cumprod(1+0)=1
    # Beta should be 0 when benchmark has zero variance.
    assert no_bench["metrics"]["beta"] == 0.0, no_bench["metrics"]["beta"]


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
        oos, frames, _macro_panel(), _config(), sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert res["status"] == "insufficient", res
    assert res["metrics"] == {}
    assert isinstance(res["equity"], list)


def test_write_report_roundtrip():
    tickers = _tickers()
    frames = _price_frames(tickers, seed=15)
    oos = _oos_predictions(tickers, seed=16, signal=0.05)
    res = pbt.run_portfolio_backtest(
        oos, frames, _macro_panel(), _config(), sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert res["status"] == "ok"

    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "portfolio_backtest.json")
        path = pbt.write_portfolio_backtest_report(
            res, output_path=out, model_version="cs-v1-test",
            run_date="2026-06-10", generated_at="2026-06-10T06:00:00Z",
        )
        assert path == out
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["available"] is True
        assert data["model_version"] == "cs-v1-test"
        assert data["run_date"] == "2026-06-10"
        assert data["generated_at"] == "2026-06-10T06:00:00Z"
        assert "metrics" in data and "sharpe" in data["metrics"]
        assert data["status"] == "ok"

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


def _ok_result(sharpe=0.80, max_drawdown=-0.10, information_ratio=0.50,
               turnover=0.20, cagr=0.12):
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
    # sharpe=None should trigger sharpe failure (< threshold).
    result = _ok_result()
    result["metrics"]["sharpe"] = None
    result["metrics"]["information_ratio"] = None
    gate = evaluate_portfolio_kpi_gate(result, _gate_config())
    assert gate["passed"] is False
    assert any("sharpe" in f for f in gate["failures"]), gate["failures"]
    assert any("ir" in f for f in gate["failures"]), gate["failures"]


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
    """End-to-end: run a real backtest and put the result through the gate."""
    tickers = _tickers()
    frames = _price_frames(tickers, seed=20)
    oos = _oos_predictions(tickers, seed=21, signal=0.05)
    macro = _macro_panel()
    res = pbt.run_portfolio_backtest(
        oos, frames, macro, _config(), sectors=_sectors(tickers),
        label_horizon_days=H,
    )
    assert res["status"] == "ok"
    gate = evaluate_portfolio_kpi_gate(res, _gate_config())
    # Gate evaluates without error; result is consistent.
    assert isinstance(gate["passed"], bool)
    assert isinstance(gate["failures"], list)
    assert gate["metrics"] is res["metrics"]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_TESTS = [
    test_backtest_runs_end_to_end,
    test_backtest_positive_signal_beats_cash,
    test_backtest_turnover_and_costs,
    test_backtest_no_lookahead_cov,
    test_backtest_benchmark_alpha_beta,
    test_backtest_insufficient_periods,
    test_write_report_roundtrip,
    test_gate_passes_when_all_metrics_ok,
    test_gate_fails_on_max_dd_breach,
    test_gate_fails_on_low_sharpe,
    test_gate_fails_on_low_ir,
    test_gate_fails_on_high_turnover,
    test_gate_insufficient_status_not_passed,
    test_gate_none_result_not_passed,
    test_gate_none_metrics_fields_fail,
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
