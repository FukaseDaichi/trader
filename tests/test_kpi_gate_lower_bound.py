#!/usr/bin/env python3
"""
KPI gate: holdout mean daily net return must stay > 0 at the lower end of a
block-bootstrap confidence interval (src/backtest.py).

A 60-day holdout with ~12 independent 5-day cohorts passes a Sharpe 0.20 bar
by luck too often; the lower-bound rule makes "probably just noise" a failure.

Runnable: uv run python tests/test_kpi_gate_lower_bound.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import model_store  # noqa: E402
from src.backtest import (  # noqa: E402
    _block_bootstrap_mean_lower_bound,
    _evaluate_gate_rules,
    attach_holdout_significance,
)
from src.config import BACKTEST_GATE_CONFIG  # noqa: E402


def _passing_metrics(**overrides):
    metrics = {
        "independent_signal_cohorts": 12,
        "round_trips": 12,
        "cagr": 0.5,
        "avg_daily_net_return": 0.002,
        "max_drawdown": -0.05,
        "sharpe": 1.5,
    }
    metrics.update(overrides)
    return metrics


def _bound(values, **kw):
    return _block_bootstrap_mean_lower_bound(
        values,
        block_days=kw.get("block_days", 5),
        n_samples=kw.get("n_samples", 2000),
        confidence=kw.get("confidence", 0.90),
    )


def test_bound_is_deterministic():
    rng = np.random.default_rng(1)
    values = rng.normal(0.0005, 0.01, 64)
    assert _bound(values) == _bound(values)


def test_bound_separates_signal_from_noise():
    rng = np.random.default_rng(2)
    strong = rng.normal(0.004, 0.005, 64)
    noisy = rng.normal(0.0004, 0.02, 64)
    assert _bound(strong) > 0.0
    assert _bound(noisy) <= 0.0
    # The bound never exceeds the sample mean.
    assert _bound(noisy) <= float(np.mean(noisy))


def test_bound_unavailable_for_too_short_holdout():
    assert _bound([0.01] * 9, block_days=5) is None
    assert _bound([0.01] * 10, block_days=5) is not None


def test_attach_adds_bound_and_parameters():
    sim = pd.DataFrame({"net_return": np.linspace(0.001, 0.003, 60)})
    cfg = {"bootstrap_confidence": 0.9, "bootstrap_samples": 500}
    out = attach_holdout_significance({"sharpe": 1.0}, sim, cfg, horizon=5)
    assert out["sharpe"] == 1.0
    assert out["avg_daily_net_return_lower_bound"] > 0.0
    assert out["bootstrap_block_days"] == 5
    assert out["bootstrap_samples"] == 500
    empty = attach_holdout_significance({}, pd.DataFrame(), cfg, horizon=5)
    assert empty["avg_daily_net_return_lower_bound"] is None


def test_gate_rule_requires_positive_bound_when_enabled():
    cfg = {**BACKTEST_GATE_CONFIG, "require_positive_lower_bound": True}
    ok = _passing_metrics(avg_daily_net_return_lower_bound=0.0001)
    assert _evaluate_gate_rules(ok, cfg) == []
    lucky = _passing_metrics(avg_daily_net_return_lower_bound=-0.0015)
    assert _evaluate_gate_rules(lucky, cfg) == ["avg_daily_net_return_lb90%<=0"]
    missing = _passing_metrics()
    assert _evaluate_gate_rules(missing, cfg) == [
        "avg_daily_net_return_lower_bound_unavailable"
    ]


def test_gate_rule_is_off_without_the_flag():
    cfg = {**BACKTEST_GATE_CONFIG, "require_positive_lower_bound": False}
    lucky = _passing_metrics(avg_daily_net_return_lower_bound=-0.0015)
    assert _evaluate_gate_rules(lucky, cfg) == []


def test_runtime_default_enables_rule_and_changes_gate_contract():
    assert BACKTEST_GATE_CONFIG["require_positive_lower_bound"] is True
    on = model_store.canonical_phase1_gate_config(
        BACKTEST_GATE_CONFIG, effective_horizon_days=5
    )
    off = model_store.canonical_phase1_gate_config(
        {**BACKTEST_GATE_CONFIG, "require_positive_lower_bound": False},
        effective_horizon_days=5,
    )
    assert on["require_positive_lower_bound"] is True
    assert on["bootstrap_confidence"] == BACKTEST_GATE_CONFIG["bootstrap_confidence"]
    # Models gated under the old rule must not count as passing the new one.
    assert model_store.payload_sha256(on) != model_store.payload_sha256(off)


ALL_TESTS = [
    test_bound_is_deterministic,
    test_bound_separates_signal_from_noise,
    test_bound_unavailable_for_too_short_holdout,
    test_attach_adds_bound_and_parameters,
    test_gate_rule_requires_positive_bound_when_enabled,
    test_gate_rule_is_off_without_the_flag,
    test_runtime_default_enables_rule_and_changes_gate_contract,
]


def main() -> int:
    failures = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
