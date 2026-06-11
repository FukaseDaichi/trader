#!/usr/bin/env python3
"""
Issue #3: the qualitative regime (docs/curation/macro_latest.json market_bias)
must reach build_portfolio_snapshot so the risk-off gross brake
(TRADER_PORTFOLIO_RISK_OFF_GROSS_MULT) actually engages.

Runnable two ways:
  uv run python tests/test_portfolio_regime.py    # standalone
  uv run pytest tests/test_portfolio_regime.py     # if pytest is available
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from src.config import get_portfolio_config  # noqa: E402


def test_load_portfolio_regime_normalizes_labels():
    load = main._load_portfolio_regime
    assert load({"market_bias": "risk_off"}) == "risk_off"
    assert load({"market_bias": " RISK_OFF "}) == "risk_off"
    assert load({"market_bias": "risk_on"}) == "risk_on"
    assert load({"market_bias": "neutral"}) == "neutral"
    # Missing / unknown labels degrade to neutral (brake stays off, no raise).
    assert load({"market_bias": None}) == "neutral"
    assert load({}) == "neutral"
    assert load({"market_bias": "apocalyptic"}) == "neutral"


def _phase2_stub(n=3, rows=80):
    """Synthetic Phase 2 inference result: deterministic frames + predictions."""
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2026-02-02", periods=rows)
    tickers_data = []
    predictions = []
    for i in range(n):
        code = f"{6000 + i}.JP"
        close = 1000.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, size=rows)))
        df = pd.DataFrame({"date": dates, "close": close})
        info = {"code": code, "name": f"T{i}", "sector": f"SEC{i}"}
        tickers_data.append((info, df))
        predictions.append({
            "ticker": code, "cs_rank": i + 1,
            "expected_ret": 0.02 - i * 0.001, "prob_up": 0.6,
        })
    return {
        "status": "ok", "mode": "shadow", "model_version": "cs-test",
        "as_of_date": "2026-06-11", "predictions": predictions,
        "tickers_data": tickers_data,
    }


def _run_snapshot_with_bias(bias):
    """Run _run_portfolio_snapshot with side effects stubbed out."""
    orig_regime = main._build_macro_regime
    orig_export = main.dashboard.export_portfolio_latest
    orig_db = main.db.record_portfolio_snapshot
    orig_prev = main._prev_target_weights

    main._build_macro_regime = lambda panel: {"market_bias": bias, "usdjpy": None}
    main.dashboard.export_portfolio_latest = lambda *a, **k: None
    main.db.record_portfolio_snapshot = lambda *a, **k: {"ok": False, "reason": "test"}
    main._prev_target_weights = lambda: {}
    try:
        return main._run_portfolio_snapshot(_phase2_stub(), "2026-06-11")
    finally:
        main._build_macro_regime = orig_regime
        main.dashboard.export_portfolio_latest = orig_export
        main.db.record_portfolio_snapshot = orig_db
        main._prev_target_weights = orig_prev


def test_run_portfolio_snapshot_wires_regime_to_risk_brake():
    mult = get_portfolio_config()["risk_off_gross_mult"]

    snap_neutral = _run_snapshot_with_bias("neutral")
    assert snap_neutral["status"] == "ok"
    assert snap_neutral["constraints"]["regime"] == "neutral"
    assert snap_neutral["constraints"]["regime_multiplier"] == 1.0
    assert snap_neutral["gross_exposure"] > 0.0

    snap_off = _run_snapshot_with_bias("risk_off")
    assert snap_off["constraints"]["regime"] == "risk_off"
    assert snap_off["constraints"]["regime_multiplier"] == mult
    # Same inputs, halved gross (hysteresis floor not binding at these weights).
    assert abs(snap_off["gross_exposure"] - mult * snap_neutral["gross_exposure"]) < 1e-9


def test_run_portfolio_snapshot_missing_macro_latest_defaults_neutral():
    snap = _run_snapshot_with_bias(None)  # screen output absent / key missing
    assert snap["constraints"]["regime"] == "neutral"
    assert snap["constraints"]["regime_multiplier"] == 1.0


ALL_TESTS = [
    test_load_portfolio_regime_normalizes_labels,
    test_run_portfolio_snapshot_wires_regime_to_risk_brake,
    test_run_portfolio_snapshot_missing_macro_latest_defaults_neutral,
]


def main_() -> int:
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
    raise SystemExit(main_())
