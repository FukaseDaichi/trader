#!/usr/bin/env python3
"""Regression tests for auto-mode legacy prediction fallback (no network/DB).

Runnable two ways:
  uv run python tests/test_main_predict_fallback.py
  uv run pytest tests/test_main_predict_fallback.py
"""

from __future__ import annotations

from copy import deepcopy
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main as trader_main  # noqa: E402
from src import db_records  # noqa: E402


TICKER = {"code": "8750.JP", "name": "第一生命"}
LABEL_CFG = {"label_mode": "triple_barrier", "tb_max_days": 5}


def _auto_context() -> dict:
    return {
        "model_cfg": {"model_mode": "auto"},
        "label_cfg": LABEL_CFG,
        "active": {"version": "phase1-test-v1"},
    }


def _fallback_version(label_cfg=None, gate_config=None) -> str:
    artifact = trader_main.model_store.build_phase1_artifact_contract(
        label_config=label_cfg or LABEL_CFG,
        feature_columns=trader_main.phase1_feature_cols(False),
        macro_features_enabled=False,
        calibration_mode="none",
    )
    gate = trader_main.model_store.build_phase1_gate_contract(
        gate_config or trader_main.BACKTEST_GATE_CONFIG, artifact
    )
    return trader_main.model_store.phase1_ephemeral_model_version(artifact, gate)


def test_auto_missing_bundle_uses_successful_legacy_fallback():
    featured = pd.DataFrame({"f": [1.0]})
    trained_result = {
        "boosters": {"folds": [], "final": object()},
        "metadata": {
            "calibration": None,
            "feature_reference": {},
            "artifact_contract": {},
            "gate_contract": {},
        },
    }
    pred = {
        "prob_up": 0.73,
        "horizon_days": 5,
        "raw_score": 0.72,
        "expected_ret": 0.01,
        "features_hash": "features",
        "artifact_schema_version": 3,
        "label_config": LABEL_CFG,
        "feature_schema_hash": "feature-schema",
        "macro_features_enabled": False,
        "calibration_mode": "none",
        "calibration_id": "identity-v1",
        "applied_calibration_id": "identity-v1",
        "execution_contract_version": "next_session_open_to_close_v2",
        "model_bundle_sha256": "model-hash",
        "gate_config_hash": "gate-config-hash",
        "gate_evidence_sha256": "evidence-hash",
        "gate_result": {"passed": True, "thresholds": {"buy": 0.8}},
    }

    with (
        patch.object(
            trader_main.model_store, "load_model_bundle", return_value=None
        ) as load_bundle,
        patch.object(
            trader_main.phase1,
            "train_ticker_bundle",
            return_value=(trained_result, {"reason": "ok"}),
        ) as train,
        patch.object(trader_main.phase1, "predict_ticker", return_value=pred),
    ):
        prob_up, model_ready, fields = trader_main._predict_for_ticker(
            featured, TICKER, _auto_context()
        )

    load_bundle.assert_called_once_with("phase1-test-v1", "8750.JP")
    expected_version = _fallback_version()
    train.assert_called_once_with(
        featured,
        trader_main.BACKTEST_GATE_CONFIG,
        LABEL_CFG,
        {
            "macro_features_enabled": False,
            "calibration_mode": "none",
            "min_calibration_rows": 60,
        },
        model_version=expected_version,
    )
    assert prob_up == 0.73
    assert model_ready is True
    assert fields["model_version"] == expected_version
    assert fields["raw_score"] == 0.72
    assert fields["gate_result"]["passed"] is True
    assert fields["gate_evidence_sha256"] == "evidence-hash"


def test_auto_missing_bundle_handles_failed_legacy_fallback():
    featured = pd.DataFrame({"f": [1.0]})

    with (
        patch.object(trader_main.model_store, "load_model_bundle", return_value=None),
        patch.object(
            trader_main.phase1,
            "train_ticker_bundle",
            return_value=(None, {"reason": "insufficient_rows"}),
        ) as train,
    ):
        prob_up, model_ready, fields = trader_main._predict_for_ticker(
            featured, TICKER, _auto_context()
        )

    train.assert_called_once()
    assert prob_up == 0.5
    assert model_ready is False
    assert fields["model_version"] == _fallback_version()
    assert fields["horizon_days"] == 5
    assert fields["model_error"] == "insufficient_rows"


def test_ephemeral_version_separates_label_horizon_barrier_and_gate_contracts():
    triple_h5 = {
        "label_mode": "triple_barrier",
        "horizon_days": 5,
        "tb_tp_atr": 1.5,
        "tb_sl_atr": 1.0,
        "tb_max_days": 5,
    }
    triple_h1 = {**triple_h5, "horizon_days": 1, "tb_max_days": 1}
    binary_h1 = {**triple_h1, "label_mode": "binary_1d"}
    changed_barrier = {**triple_h5, "tb_tp_atr": 2.0}
    changed_cost = {
        **trader_main.BACKTEST_GATE_CONFIG,
        "cost_bps": float(trader_main.BACKTEST_GATE_CONFIG["cost_bps"]) + 1.0,
    }

    versions = {
        _fallback_version(triple_h5),
        _fallback_version(triple_h1),
        _fallback_version(binary_h1),
        _fallback_version(changed_barrier),
        _fallback_version(triple_h5, changed_cost),
    }
    assert len(versions) == 5
    assert all(
        version.startswith(db_records.EPHEMERAL_PHASE1_MODEL_VERSION_PREFIX)
        for version in versions
    )


def test_active_portfolio_merge_uses_exact_snapshot_model_gate():
    signals = [{"ticker": "7011.JP", "action": "BUY", "reason": "model signal"}]
    snapshot = {
        "status": "ok",
        "mode": "active",
        "model_version": "cs-v1-new",
        "positions": [{"ticker": "7011.JP", "target_weight": 0.18, "cs_rank": 1}],
    }

    with patch.object(
        trader_main.portfolio, "read_portfolio_gate", return_value=True
    ) as read_gate:
        merged = trader_main._merge_portfolio_target_weights(signals, snapshot)

    read_gate.assert_called_once_with(expected_model_version="cs-v1-new")
    assert merged[0]["target_weight"] == 0.18
    assert signals[0] == {
        "ticker": "7011.JP",
        "action": "BUY",
        "reason": "model signal",
    }


def test_shadow_portfolio_merge_is_byte_for_byte_and_skips_gate_read():
    signals = [
        {
            "ticker": "7011.JP",
            "action": "BUY",
            "reason": "model signal",
            "nested": {"thresholds": [0.8, 0.65]},
        }
    ]
    original = deepcopy(signals)
    snapshot = {
        "status": "ok",
        "mode": "shadow",
        "model_version": "cs-v1-new",
        "positions": [{"ticker": "7011.JP", "target_weight": 0.18}],
    }

    with patch.object(
        trader_main.portfolio,
        "read_portfolio_gate",
        side_effect=AssertionError("shadow must not read active gate"),
    ):
        merged = trader_main._merge_portfolio_target_weights(signals, snapshot)

    assert merged is signals
    assert merged == original


ALL_TESTS = [
    test_auto_missing_bundle_uses_successful_legacy_fallback,
    test_auto_missing_bundle_handles_failed_legacy_fallback,
    test_ephemeral_version_separates_label_horizon_barrier_and_gate_contracts,
    test_active_portfolio_merge_uses_exact_snapshot_model_gate,
    test_shadow_portfolio_merge_is_byte_for_byte_and_skips_gate_read,
]


def main() -> int:
    failures = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
