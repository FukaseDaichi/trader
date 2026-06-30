#!/usr/bin/env python3
"""
Unit tests for src/db_records.py (pure logic, no DB / no network).

Runnable two ways:
  uv run python tests/test_db_records.py     # standalone
  uv run pytest tests/test_db_records.py      # if pytest is available
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import json  # noqa: E402
import tempfile  # noqa: E402

from src.db_records import (  # noqa: E402
    backtest_equity_rows,
    backtest_run_row,
    compute_benchmark_ret,
    compute_outcome,
    cs_prediction_row,
    make_event_id,
    portfolio_snapshot_row,
    signal_to_prediction_row,
    signal_to_signal_row,
    summarize_performance,
)

import src.db as dbmod  # noqa: E402

OK_SIGNAL = {
    "ticker": "7011.JP",
    "name": "三菱重工業",
    "date": "2026-06-05",
    "close": 4586.0,
    "prob_up": 0.72,
    "action": "MILD_BUY",
    "raw_action": "MILD_BUY",
    "gate_passed": True,
    "status": "ok",
    "thresholds": {
        "buy": 0.8,
        "mild_buy": 0.65,
        "mild_sell": 0.25,
        "sell": 0.1,
        "volatility_limit": 0.04,
    },
    "limit_price": None,
    "stop_loss": None,
    "reason": "やや上昇傾向 (上昇確率 72%)",
}

FAILED_SIGNAL = {
    "ticker": "9999.JP",
    "name": "失敗銘柄",
    "date": "2026-06-08",
    "close": None,
    "prob_up": None,
    "action": "HOLD",
    "raw_action": "HOLD",
    "gate_passed": False,
    "status": "failed",
}


def test_event_id_is_stable_and_namespaced():
    assert make_event_id("2026-06-08", "7011.JP", "sig") == "2026-06-08:7011.JP:sig"
    assert make_event_id("2026-06-08", "7011.JP", "pred") != make_event_id(
        "2026-06-08", "7011.JP", "sig"
    )


def test_prediction_row_for_ok_signal():
    row = signal_to_prediction_row(OK_SIGNAL, run_date="2026-06-08")
    assert row is not None
    assert row["run_date"] == "2026-06-08"
    assert row["as_of_date"] == "2026-06-05"  # signal['date'] = latest price date
    assert row["ticker"] == "7011.JP"
    assert row["model_version"] == "legacy-daily-v0"
    assert row["horizon_days"] == 1  # legacy model predicts next-day
    assert abs(row["prob_up"] - 0.72) < 1e-9
    assert abs(row["raw_score"] - 0.72) < 1e-9


def test_prediction_row_is_none_when_prob_missing():
    assert signal_to_prediction_row(FAILED_SIGNAL, run_date="2026-06-08") is None


def test_signal_row_maps_core_fields():
    row = signal_to_signal_row(OK_SIGNAL, run_date="2026-06-08")
    assert row["run_date"] == "2026-06-08"
    assert row["as_of_date"] == "2026-06-05"
    assert row["ticker"] == "7011.JP"
    assert row["action"] == "MILD_BUY"
    assert row["gate_passed"] is True
    assert row["status"] == "ok"
    assert abs(row["conviction"] - 0.72) < 1e-9
    assert row["target_weight"] is None  # Phase 2
    assert row["thresholds"]["buy"] == 0.8


def test_signal_row_for_failed_signal():
    row = signal_to_signal_row(FAILED_SIGNAL, run_date="2026-06-08")
    assert row["ticker"] == "9999.JP"
    assert row["action"] == "HOLD"
    assert row["gate_passed"] is False
    assert row["status"] == "failed"
    assert row["conviction"] is None


def test_outcome_long_profit():
    # entry 100, exit 110, path high 112 / low 99
    o = compute_outcome(
        "BUY",
        entry_close=100.0,
        exit_close=110.0,
        path_highs=[105.0, 112.0],
        path_lows=[99.0, 108.0],
    )
    assert abs(o["realized_ret"] - 0.10) < 1e-9
    assert o["hit"] is True
    assert abs(o["mfe"] - 0.12) < 1e-9  # 112/100 - 1
    assert abs(o["mae"] - (-0.01)) < 1e-9  # 99/100 - 1
    assert o["exit_reason"] == "time"


def test_outcome_long_loss_is_not_hit():
    o = compute_outcome(
        "MILD_BUY",
        entry_close=100.0,
        exit_close=95.0,
        path_highs=[101.0],
        path_lows=[94.0],
    )
    assert abs(o["realized_ret"] - (-0.05)) < 1e-9
    assert o["hit"] is False


def test_outcome_avoid_hit_when_price_falls():
    # SELL/avoid: "hit" means avoiding was correct, i.e. price fell.
    o = compute_outcome(
        "SELL", entry_close=100.0, exit_close=90.0, path_highs=[101.0], path_lows=[89.0]
    )
    assert o["hit"] is True
    o2 = compute_outcome(
        "MILD_SELL",
        entry_close=100.0,
        exit_close=105.0,
        path_highs=[106.0],
        path_lows=[100.0],
    )
    assert o2["hit"] is False


def test_outcome_hold_has_no_hit():
    o = compute_outcome(
        "HOLD",
        entry_close=100.0,
        exit_close=101.0,
        path_highs=[102.0],
        path_lows=[100.0],
    )
    assert o["hit"] is None
    assert abs(o["realized_ret"] - 0.01) < 1e-9


def test_outcome_rejects_bad_entry():
    raised = False
    try:
        compute_outcome(
            "BUY", entry_close=0.0, exit_close=100.0, path_highs=[], path_lows=[]
        )
    except ValueError:
        raised = True
    assert raised is True


def test_outcome_empty_path_uses_realized():
    o = compute_outcome(
        "BUY", entry_close=100.0, exit_close=103.0, path_highs=[], path_lows=[]
    )
    assert abs(o["mfe"] - 0.03) < 1e-9
    assert abs(o["mae"] - 0.03) < 1e-9


def _row(entry_date, action, horizon, ret, hit):
    return {
        "entry_date": entry_date,
        "action": action,
        "horizon_days": horizon,
        "realized_ret": ret,
        "hit": hit,
    }


def test_summary_hit_rate_and_curve():
    rows = [
        _row("2026-05-01", "BUY", 1, 0.02, True),
        _row("2026-05-01", "MILD_BUY", 1, -0.01, False),  # same day -> averaged
        _row("2026-05-02", "BUY", 1, 0.03, True),
        _row("2026-05-01", "BUY", 5, 0.05, True),
        _row("2026-05-01", "SELL", 1, -0.04, True),  # avoid hit, excluded from curve
    ]
    s = summarize_performance(rows, curve_horizon=1)

    # 1d long hit-rate: 3 long 1d rows (0.02 T, -0.01 F, 0.03 T) -> 2/3
    assert s["horizons"]["1"]["count"] == 3
    assert abs(s["horizons"]["1"]["hit_rate"] - (2.0 / 3.0)) < 1e-9

    # equity curve: day1 mean(0.02,-0.01)=0.005 ; day2 = 0.03
    curve = s["equity_curve"]
    assert [p["date"] for p in curve] == ["2026-05-01", "2026-05-02"]
    assert abs(curve[0]["equity"] - 1.005) < 1e-9
    assert abs(curve[1]["equity"] - 1.005 * 1.03) < 1e-9
    assert s["n_long_signals"] == 4  # BUY/MILD_BUY rows across all horizons


def test_summary_handles_empty():
    s = summarize_performance([], curve_horizon=1)
    assert s["n_long_signals"] == 0
    assert s["equity_curve"] == []
    assert s["horizons"]["5"]["count"] == 0
    assert s["horizons"]["5"]["hit_rate"] is None


def test_cs_prediction_row_maps_fields():
    pred = {
        "ticker": "7011.JP",
        "raw_score": 1.234,
        "cs_rank": 3,
        "prob_up": 0.72,
        "expected_ret": 0.015,
        "features_hash": None,
    }
    row = cs_prediction_row(
        pred,
        run_date="2026-06-09",
        model_version="cs-v1-20260609",
        horizon_days=5,
        as_of_date="2026-06-06",
    )
    assert row is not None
    assert row["run_date"] == "2026-06-09"
    assert row["as_of_date"] == "2026-06-06"
    assert row["ticker"] == "7011.JP"
    assert row["model_version"] == "cs-v1-20260609"
    assert row["horizon_days"] == 5
    assert isinstance(row["cs_rank"], int) and row["cs_rank"] == 3
    assert abs(row["raw_score"] - 1.234) < 1e-9
    assert abs(row["prob_up"] - 0.72) < 1e-9
    assert abs(row["expected_ret"] - 0.015) < 1e-9
    assert row["features_hash"] is None


def test_cs_prediction_row_missing_ticker_is_none():
    # No ticker -> must return None
    row = cs_prediction_row(
        {"raw_score": 0.5, "cs_rank": 1, "prob_up": 0.6, "expected_ret": 0.01},
        run_date="2026-06-09",
        model_version="cs-v1-20260609",
        horizon_days=5,
    )
    assert row is None

    # Explicit None ticker -> also None
    row2 = cs_prediction_row(
        {"ticker": None, "raw_score": 0.5, "cs_rank": 1, "prob_up": 0.6},
        run_date="2026-06-09",
        model_version="cs-v1-20260609",
        horizon_days=5,
    )
    assert row2 is None


def _bt_result_ok():
    """Minimal run_portfolio_backtest-shaped result with status='ok'."""
    return {
        "status": "ok",
        "start_date": "2024-01-05",
        "end_date": "2024-12-20",
        "n_periods": 48,
        "rebalance_days": 5,
        "cost_bps": 10.0,
        "slippage_bps": 5.0,
        "params": {
            "target_vol": 0.12,
            "max_name_weight": 0.20,
            "top_n": 8,
            "rebalance_days": 5,
            "cost_bps": 10.0,
            "slippage_bps": 5.0,
        },
        "metrics": {
            "cagr": 0.15,
            "sharpe": 0.80,
            "sortino": 1.1,
            "max_drawdown": -0.10,
            "information_ratio": 0.50,
            "turnover": 0.20,
            "n_periods": 48,
        },
        "equity": [
            {
                "date": "2024-01-05",
                "equity": 1.0,
                "benchmark_equity": 1.0,
                "period_return": 0.01,
                "benchmark_return": 0.005,
                "drawdown": 0.0,
                "gross_exposure": 0.85,
                "turnover": 0.30,
            },
            {
                "date": "2024-01-12",
                "equity": 1.01,
                "benchmark_equity": 1.005,
                "period_return": 0.01,
                "benchmark_return": 0.003,
                "drawdown": 0.0,
                "gross_exposure": 0.87,
                "turnover": 0.05,
            },
        ],
    }


def test_backtest_run_row_maps_core_fields():
    result = _bt_result_ok()
    row = backtest_run_row(
        result, run_date="2026-06-10", model_version="cs-v1-20260610"
    )
    assert row is not None
    assert row["run_date"] == "2026-06-10"
    assert row["model_version"] == "cs-v1-20260610"
    assert row["scope"] == "portfolio"
    assert row["start_date"] == "2024-01-05"
    assert row["end_date"] == "2024-12-20"
    # params and metrics are dicts (JSON-serializable).
    assert isinstance(row["params"], dict)
    assert isinstance(row["metrics"], dict)
    assert row["metrics"]["sharpe"] == 0.80
    assert row["params"]["top_n"] == 8
    # Verify JSON-serialisability (no non-serialisable types).
    json.dumps(row["params"])
    json.dumps(row["metrics"])


def test_backtest_run_row_custom_scope():
    result = _bt_result_ok()
    row = backtest_run_row(result, run_date="2026-06-10", scope="custom_scope")
    assert row["scope"] == "custom_scope"


def test_backtest_run_row_none_model_version():
    result = _bt_result_ok()
    row = backtest_run_row(result, run_date="2026-06-10", model_version=None)
    assert row is not None
    assert row["model_version"] is None


def test_backtest_run_row_insufficient_returns_none():
    result = {"status": "insufficient", "metrics": {}, "equity": []}
    assert backtest_run_row(result, run_date="2026-06-10") is None


def test_backtest_run_row_none_result_returns_none():
    assert backtest_run_row(None, run_date="2026-06-10") is None


def test_backtest_equity_rows_period_return_renamed():
    result = _bt_result_ok()
    rows = backtest_equity_rows(result)
    assert len(rows) == 2

    # period_return must be renamed to daily_return.
    assert "daily_return" in rows[0], rows[0]
    assert "period_return" not in rows[0], rows[0]

    # Values preserved.
    assert rows[0]["daily_return"] == 0.01
    assert rows[1]["daily_return"] == 0.01


def test_backtest_equity_rows_all_keys_present():
    result = _bt_result_ok()
    rows = backtest_equity_rows(result)
    expected_keys = {
        "date",
        "equity",
        "benchmark_equity",
        "daily_return",
        "benchmark_return",
        "drawdown",
        "gross_exposure",
        "turnover",
    }
    for row in rows:
        assert expected_keys.issubset(set(row)), (
            f"Missing keys: {expected_keys - set(row)}"
        )


def test_backtest_equity_rows_insufficient_is_empty():
    result = {"status": "insufficient", "metrics": {}, "equity": []}
    assert backtest_equity_rows(result) == []


def test_backtest_equity_rows_none_result_is_empty():
    assert backtest_equity_rows(None) == []


def test_backtest_equity_rows_json_serializable():
    result = _bt_result_ok()
    rows = backtest_equity_rows(result)
    # All values must be JSON-serializable (no numpy/datetime objects).
    for row in rows:
        json.dumps(row)


def _snapshot_ok(as_of_date="2026-06-06"):
    """Minimal build_portfolio_snapshot-shaped result with status='ok'."""
    return {
        "run_date": "2026-06-09",
        "as_of_date": as_of_date,
        "mode": "shadow",
        "status": "ok",
        "model_version": "cs-v1-20260609",
        "gross_exposure": 0.85,
        "net_exposure": 0.85,
        "expected_vol": 0.11,
        "expected_ret": 0.012,
        "sector_exposure": {"Industrials": 0.4, None: 0.45},
        "diff_summary": {"add": 2, "trim": 1, "exit": 0, "hold": 5},
        "positions": [
            {
                "ticker": "7011.JP",
                "name": "三菱重工業",
                "sector": "Industrials",
                "target_weight": 0.2,
                "prev_weight": 0.15,
                "diff_type": "increase",
                "cs_rank": 1,
                "expected_ret": 0.02,
                "prob_up": 0.7,
                "volatility": 0.25,
                "limit_price": None,
                "stop_loss": None,
            },
        ],
        "constraints": {"target_vol": 0.12, "top_n": 8, "regime": "neutral"},
        "warnings": ["covariance_diagonal_fallback"],
    }


def test_portfolio_snapshot_row_maps_all_columns():
    snap = _snapshot_ok()
    row = portfolio_snapshot_row(snap)
    assert row is not None
    assert row["run_date"] == "2026-06-09"
    assert row["as_of_date"] == "2026-06-06"
    assert row["model_version"] == "cs-v1-20260609"
    assert row["mode"] == "shadow"
    assert row["status"] == "ok"
    # diff_summary -> diff_from_prev column rename.
    assert row["diff_from_prev"] == {"add": 2, "trim": 1, "exit": 0, "hold": 5}
    assert "diff_summary" not in row
    assert row["positions"] == snap["positions"]
    assert abs(row["gross_exposure"] - 0.85) < 1e-9
    assert abs(row["net_exposure"] - 0.85) < 1e-9
    assert abs(row["expected_ret"] - 0.012) < 1e-9
    assert abs(row["expected_vol"] - 0.11) < 1e-9
    assert row["sector_exposure"] == {"Industrials": 0.4, None: 0.45}
    assert row["constraints"]["top_n"] == 8
    assert row["warnings"] == ["covariance_diagonal_fallback"]
    # Every value must be JSON-serializable (None dict key -> "null").
    json.dumps(row)


def test_portfolio_snapshot_row_run_date_override():
    snap = _snapshot_ok()
    row = portfolio_snapshot_row(snap, run_date="2026-06-10")
    assert row["run_date"] == "2026-06-10"  # explicit arg wins over snapshot


def test_portfolio_snapshot_row_as_of_date_falls_back_to_run_date():
    # as_of_date None must fall back to run_date (NOT NULL column).
    snap = _snapshot_ok(as_of_date=None)
    row = portfolio_snapshot_row(snap, run_date="2026-06-09")
    assert row["as_of_date"] == "2026-06-09"


def test_portfolio_snapshot_row_none_input_is_none():
    assert portfolio_snapshot_row(None) is None
    # Missing status -> not persistable.
    assert portfolio_snapshot_row({"run_date": "2026-06-09"}) is None


def test_portfolio_snapshot_row_persists_failed_status():
    failed = {
        "run_date": "2026-06-09",
        "as_of_date": "2026-06-06",
        "mode": "shadow",
        "status": "failed",
        "model_version": "cs-v1-20260609",
        "gross_exposure": 0.0,
        "net_exposure": 0.0,
        "expected_vol": 0.0,
        "expected_ret": 0.0,
        "sector_exposure": {},
        "diff_summary": {"add": 0, "trim": 0, "exit": 0, "hold": 0},
        "positions": [],
        "constraints": {"top_n": 8},
        "warnings": ["construction_error"],
    }
    row = portfolio_snapshot_row(failed)
    assert row is not None
    assert row["status"] == "failed"
    assert row["positions"] == []
    json.dumps(row)


def test_benchmark_ret_basic():
    topix = {"2026-06-09": 2900.0, "2026-06-16": 2958.0}
    r = compute_benchmark_ret(topix, "2026-06-09", "2026-06-16")
    assert abs(r - 0.02) < 1e-9


def test_benchmark_ret_missing_date_is_none():
    assert (
        compute_benchmark_ret({"2026-06-09": 2900.0}, "2026-06-09", "2026-06-16")
        is None
    )
    assert compute_benchmark_ret({}, "2026-06-09", "2026-06-16") is None


def test_benchmark_ret_zero_entry_is_none():
    assert compute_benchmark_ret({"a": 0.0, "b": 2958.0}, "a", "b") is None


def test_outbox_queue_and_dedup():
    import os

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["TRADER_DB_FALLBACK_DIR"] = tmp
        os.environ["TRADER_DB_ENABLED"] = "false"  # force fallback path
        os.environ.pop("DATABASE_URL", None)
        try:
            signals = [
                {
                    "ticker": "7011.JP",
                    "date": "2026-06-05",
                    "prob_up": 0.7,
                    "action": "MILD_BUY",
                    "gate_passed": True,
                    "status": "ok",
                },
                {
                    "ticker": "9999.JP",
                    "date": "2026-06-08",
                    "prob_up": None,
                    "action": "HOLD",
                    "gate_passed": False,
                    "status": "failed",
                },
            ]
            res = dbmod.record_run(signals, run_date="2026-06-08")
            assert res["ok"] is False
            # 7011 -> pred + sig (2), 9999 -> sig only (1) = 3 events
            assert res["queued"] == 3

            events = dbmod._read_outbox_events()
            ids = {e["event_id"] for e in events}
            assert "2026-06-08:7011.JP:pred" in ids
            assert "2026-06-08:7011.JP:sig" in ids
            assert "2026-06-08:9999.JP:sig" in ids
            assert "2026-06-08:9999.JP:pred" not in ids  # no prob_up -> no prediction
        finally:
            os.environ.pop("TRADER_DB_FALLBACK_DIR", None)
            os.environ.pop("TRADER_DB_ENABLED", None)


def test_signal_row_target_weight_passthrough():
    """target_weight in signal must be preserved in the DB row; absent key -> None."""
    sig_with_weight = {**OK_SIGNAL, "target_weight": 0.18}
    row_with = signal_to_signal_row(sig_with_weight, run_date="2026-06-08")
    assert row_with["target_weight"] == 0.18

    # Original OK_SIGNAL (no target_weight key) must still produce None
    row_without = signal_to_signal_row(OK_SIGNAL, run_date="2026-06-08")
    assert row_without["target_weight"] is None


ALL_TESTS = [
    test_benchmark_ret_basic,
    test_benchmark_ret_missing_date_is_none,
    test_benchmark_ret_zero_entry_is_none,
    test_event_id_is_stable_and_namespaced,
    test_prediction_row_for_ok_signal,
    test_prediction_row_is_none_when_prob_missing,
    test_signal_row_maps_core_fields,
    test_signal_row_for_failed_signal,
    test_outcome_long_profit,
    test_outcome_long_loss_is_not_hit,
    test_outcome_avoid_hit_when_price_falls,
    test_outcome_hold_has_no_hit,
    test_outcome_rejects_bad_entry,
    test_outcome_empty_path_uses_realized,
    test_summary_hit_rate_and_curve,
    test_summary_handles_empty,
    test_outbox_queue_and_dedup,
    test_cs_prediction_row_maps_fields,
    test_cs_prediction_row_missing_ticker_is_none,
    test_backtest_run_row_maps_core_fields,
    test_backtest_run_row_custom_scope,
    test_backtest_run_row_none_model_version,
    test_backtest_run_row_insufficient_returns_none,
    test_backtest_run_row_none_result_returns_none,
    test_backtest_equity_rows_period_return_renamed,
    test_backtest_equity_rows_all_keys_present,
    test_backtest_equity_rows_insufficient_is_empty,
    test_backtest_equity_rows_none_result_is_empty,
    test_backtest_equity_rows_json_serializable,
    test_portfolio_snapshot_row_maps_all_columns,
    test_portfolio_snapshot_row_run_date_override,
    test_portfolio_snapshot_row_as_of_date_falls_back_to_run_date,
    test_portfolio_snapshot_row_none_input_is_none,
    test_portfolio_snapshot_row_persists_failed_status,
    test_signal_row_target_weight_passthrough,
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
