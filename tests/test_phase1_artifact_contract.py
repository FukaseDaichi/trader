#!/usr/bin/env python3
"""DR-005 tests for Phase 1 artifact compatibility and purge safety."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main as trader_main  # noqa: E402
from src import model, model_store, phase1  # noqa: E402


LABEL_CONFIG = {
    "label_mode": "triple_barrier",
    "horizon_days": 5,
    "tb_tp_atr": 1.5,
    "tb_sl_atr": 1.0,
    "tb_max_days": 5,
    "vol_col": "volatility",
}
MODEL_CONFIG = {
    "model_mode": "auto",
    "macro_features_enabled": False,
    "calibration_mode": "none",
}


class DummyBooster:
    best_iteration = 25

    def __init__(self, score=0.7, identity="dummy-booster"):
        self.score = score
        self.identity = identity

    def predict(self, frame):
        return np.full(len(frame), self.score)

    def model_to_string(self):
        return f"model={self.identity};score={self.score}"

    def current_iteration(self):
        return self.best_iteration


def _contract(label_config=None, feature_columns=None):
    return model_store.build_phase1_artifact_contract(
        label_config=label_config or LABEL_CONFIG,
        feature_columns=feature_columns or ["f1", "f2"],
        macro_features_enabled=False,
        calibration_mode="none",
    )


def _gate_contract(contract=None):
    return model_store.build_phase1_gate_contract(
        trader_main.BACKTEST_GATE_CONFIG, contract or _contract()
    )


def _gate_metrics(sharpe):
    return {
        "metrics_schema_version": 2,
        "round_trips": 20,
        "cagr": 0.10,
        "avg_daily_net_return": 0.001,
        "max_drawdown": -0.10,
        "sharpe": sharpe,
    }


def _valid_gate_evidence(version, contract, gate_contract, booster, calibration=None):
    boosters = {"folds": [], "final": booster}
    split = {
        "data_split": "chronological_embargoed_holdout",
        "holdout_used": True,
        "holdout_rows": 60,
        "tuning_rows": 115,
        "embargo_rows": 5,
        "execution_window_overlap": False,
        "threshold_tuning_used": True,
        "gate_evaluation_split": "holdout",
        "calibration_fit_split": "tuning",
        "threshold_fit_split": "tuning",
        "holdout_model_is_persisted_final": True,
        "external_oos_used_for_early_stopping": False,
    }
    threshold_optimization = {
        "optimized": True,
        **{
            field: split[field]
            for field in (
                "data_split",
                "tuning_rows",
                "embargo_rows",
                "holdout_rows",
                "holdout_used",
                "threshold_tuning_used",
                "execution_window_overlap",
            )
        },
    }
    return model_store.build_phase1_gate_evidence(
        model_version=version,
        model_bundle_sha256=model_store.booster_bundle_sha256(boosters),
        artifact_contract=contract,
        gate_contract=gate_contract,
        calibrator_sha256=model_store.payload_sha256(calibration),
        applied_calibration_id="identity-v1",
        oos_prediction_sha256=model_store.payload_sha256({"rows": 180}),
        split=split,
        passed=True,
        skipped=False,
        reason="ok",
        failures=[],
        thresholds={
            "buy": 0.8,
            "mild_buy": 0.6,
            "mild_sell": 0.4,
            "sell": 0.2,
            "volatility_limit": 0.04,
        },
        threshold_optimization=threshold_optimization,
        metrics_tuning=_gate_metrics(0.8),
        metrics_holdout=_gate_metrics(1.0),
    )


def test_contract_detects_label_horizon_and_feature_order_changes():
    saved = _contract()
    changed_label = {**LABEL_CONFIG, "tb_max_days": 10}
    expected = _contract(label_config=changed_label)
    result = model_store.compare_phase1_artifact_contract(saved, expected)
    assert result["compatible"] is False
    fields = {row.get("field") for row in result["reasons"]}
    assert "label_config.tb_max_days" in fields
    assert "effective_horizon_days" in fields

    reordered = _contract(feature_columns=["f2", "f1"])
    result = model_store.compare_phase1_artifact_contract(saved, reordered)
    fields = {row.get("field") for row in result["reasons"]}
    assert "feature_columns" in fields
    assert "feature_schema_hash" in fields


def test_canonical_label_config_normalizes_removed_mode_and_rejects_unknown():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        normalized = model_store.canonical_label_config(
            {**LABEL_CONFIG, "label_mode": "vol_norm"}
        )
    assert normalized["label_mode"] == "triple_barrier"
    assert any(item.category is RuntimeWarning for item in caught)

    try:
        model_store.canonical_label_config(
            {**LABEL_CONFIG, "label_mode": "unsupported_mode"}
        )
    except ValueError as exc:
        assert "unknown label_mode" in str(exc)
    else:
        raise AssertionError("unknown label mode must fail closed")


def test_contract_detects_schema_macro_calibration_and_execution_changes():
    saved = _contract()
    old_schema = {**saved, "artifact_schema_version": 1}
    result = model_store.compare_phase1_artifact_contract(old_schema, saved)
    assert "artifact_schema_version" in {row.get("field") for row in result["reasons"]}

    macro_changed = model_store.build_phase1_artifact_contract(
        label_config=LABEL_CONFIG,
        feature_columns=["f1", "f2"],
        macro_features_enabled=True,
        calibration_mode="none",
    )
    result = model_store.compare_phase1_artifact_contract(saved, macro_changed)
    assert "macro_features_enabled" in {row.get("field") for row in result["reasons"]}

    calibration_changed = model_store.build_phase1_artifact_contract(
        label_config=LABEL_CONFIG,
        feature_columns=["f1", "f2"],
        macro_features_enabled=False,
        calibration_mode="isotonic",
    )
    result = model_store.compare_phase1_artifact_contract(saved, calibration_changed)
    fields = {row.get("field") for row in result["reasons"]}
    assert "calibration_mode" in fields
    assert "calibration_id" in fields

    execution_changed = model_store.build_phase1_artifact_contract(
        label_config=LABEL_CONFIG,
        feature_columns=["f1", "f2"],
        macro_features_enabled=False,
        calibration_mode="none",
        execution_contract_version="future_execution_contract_v99",
    )
    result = model_store.compare_phase1_artifact_contract(saved, execution_changed)
    assert "execution_contract_version" in {
        row.get("field") for row in result["reasons"]
    }


def test_active_compatibility_checks_pointer_metadata_and_manifest():
    expected = trader_main._runtime_artifact_contract(MODEL_CONFIG, LABEL_CONFIG)
    gate_contract = model_store.build_phase1_gate_contract(
        trader_main.BACKTEST_GATE_CONFIG, expected
    )
    active = {
        "version": "phase1-contract-v2",
        **model_store.phase1_contract_metadata_fields(expected),
        **model_store.phase1_gate_contract_metadata_fields(gate_contract),
        "artifact_contract": expected,
        "gate_contract": gate_contract,
        "manifest_sha256": "manifest-hash",
    }
    metadata = {
        **model_store.phase1_contract_metadata_fields(expected),
        **model_store.phase1_gate_contract_metadata_fields(gate_contract),
        "artifact_contract": expected,
        "gate_contract": gate_contract,
    }
    manifest = {
        "artifact_contract": expected,
        "gate_contract": gate_contract,
        "candidate_validation": {"passed": True},
    }
    with (
        patch.object(
            trader_main.model_store,
            "read_version_metadata",
            return_value=metadata,
        ),
        patch.object(
            trader_main.model_store,
            "read_phase1_manifest",
            return_value=manifest,
        ),
        patch.object(
            trader_main.model_store,
            "phase1_manifest_sha256",
            return_value="manifest-hash",
        ),
        patch.object(
            trader_main.model_store,
            "verify_phase1_manifest",
            return_value={"passed": True, "failures": []},
        ),
    ):
        result = trader_main._active_model_compatibility(
            active, MODEL_CONFIG, LABEL_CONFIG
        )
    assert result["compatible"] is True
    assert result["reasons"] == []

    runtime_changed = {**LABEL_CONFIG, "tb_max_days": 10}
    with (
        patch.object(
            trader_main.model_store,
            "read_version_metadata",
            return_value=metadata,
        ),
        patch.object(
            trader_main.model_store,
            "read_phase1_manifest",
            return_value=manifest,
        ),
        patch.object(
            trader_main.model_store,
            "phase1_manifest_sha256",
            return_value="manifest-hash",
        ),
        patch.object(
            trader_main.model_store,
            "verify_phase1_manifest",
            return_value={"passed": True, "failures": []},
        ),
    ):
        result = trader_main._active_model_compatibility(
            active, MODEL_CONFIG, runtime_changed
        )
    assert result["compatible"] is False
    fields = {row.get("field") for row in result["reasons"]}
    assert "effective_horizon_days" in fields
    assert "label_config.tb_max_days" in fields


def test_old_active_pointer_without_contract_is_rejected_with_reason():
    active = {"version": "legacy-date-only-version"}
    with patch.object(
        trader_main.model_store, "read_version_metadata", return_value=None
    ):
        result = trader_main._active_model_compatibility(
            active, MODEL_CONFIG, LABEL_CONFIG
        )
    assert result["compatible"] is False
    codes = {row["code"] for row in result["reasons"]}
    assert "artifact_contract_missing" in codes
    assert "active_version_metadata_missing" in codes


def test_predict_uses_bundle_horizon_not_runtime_horizon():
    contract = _contract()
    gate_contract = _gate_contract(contract)
    booster = DummyBooster()
    version = "phase1-contract-v3"
    evidence = _valid_gate_evidence(
        version, contract, gate_contract, booster, calibration=None
    )
    bundle = {
        "version": version,
        "folds": [],
        "final": booster,
        "calibration": None,
        "feature_reference": {"feature_cols": ["f1", "f2"]},
        "ticker_metadata": {
            "artifact_contract": contract,
            "gate_contract": gate_contract,
            "calibrator_sha256": model_store.payload_sha256(None),
            "applied_calibration_id": "identity-v1",
            "gate_evidence": evidence,
        },
    }
    featured = pd.DataFrame({"f1": [1.0], "f2": [2.0]})
    runtime_changed = {**LABEL_CONFIG, "tb_max_days": 20}
    result = phase1.predict_ticker(featured, bundle, runtime_changed)
    assert result is not None
    assert result["horizon_days"] == 5
    assert result["label_config"] == contract["label_config"]
    assert (
        result["execution_contract_version"] == contract["execution_contract_version"]
    )


def test_incompatible_saved_bundle_degrades_to_legacy_in_auto_mode():
    active_contract = _contract()
    incompatible_contract = _contract(feature_columns=["f2", "f1"])
    active = {"version": "phase1-contract-v2", "artifact_contract": active_contract}
    bundle = {
        "ticker_metadata": {
            **model_store.phase1_contract_metadata_fields(incompatible_contract),
            "model_version": "phase1-contract-v2",
            "artifact_contract": incompatible_contract,
        },
        "version_metadata": {
            **model_store.phase1_contract_metadata_fields(active_contract),
            "artifact_contract": active_contract,
        },
    }
    context = {
        "model_cfg": MODEL_CONFIG,
        "label_cfg": LABEL_CONFIG,
        "active": active,
    }
    fallback_result = {
        "boosters": {"folds": [], "final": DummyBooster(score=0.73)},
        "metadata": {
            "calibration": None,
            "feature_reference": {},
            "artifact_contract": active_contract,
            "gate_contract": _gate_contract(active_contract),
        },
    }
    fallback_pred = {
        "prob_up": 0.73,
        "horizon_days": 5,
        "raw_score": 0.73,
        "expected_ret": None,
        "features_hash": "daily-features",
        "artifact_schema_version": model_store.PHASE1_ARTIFACT_SCHEMA_VERSION,
        "label_config": active_contract["label_config"],
        "feature_schema_hash": active_contract["feature_schema_hash"],
        "macro_features_enabled": False,
        "calibration_mode": "none",
        "calibration_id": "identity-v1",
        "applied_calibration_id": "identity-v1",
        "execution_contract_version": active_contract["execution_contract_version"],
        "model_bundle_sha256": "legacy-bundle-hash",
        "gate_config_hash": "legacy-gate-hash",
        "gate_evidence_sha256": "legacy-evidence-hash",
        "gate_result": {"passed": True, "thresholds": {}},
    }
    with (
        patch.object(trader_main.model_store, "load_model_bundle", return_value=bundle),
        patch.object(
            trader_main.phase1,
            "train_ticker_bundle",
            return_value=(fallback_result, {"reason": "ok"}),
        ),
        patch.object(trader_main.phase1, "predict_ticker", return_value=fallback_pred),
    ):
        prob_up, ready, provenance = trader_main._predict_for_ticker(
            pd.DataFrame({"f1": [1.0]}),
            {"code": "7011.JP", "name": "三菱重工"},
            context,
        )
    assert prob_up == 0.73
    assert ready is True
    fallback_artifact = model_store.build_phase1_artifact_contract(
        label_config=LABEL_CONFIG,
        feature_columns=trader_main.phase1_feature_cols(False),
        macro_features_enabled=False,
        calibration_mode="none",
    )
    fallback_gate = model_store.build_phase1_gate_contract(
        trader_main.BACKTEST_GATE_CONFIG, fallback_artifact
    )
    assert provenance["model_version"] == model_store.phase1_ephemeral_model_version(
        fallback_artifact, fallback_gate
    )


def test_incompatible_saved_bundle_is_failed_hold_in_phase1_mode():
    active_contract = _contract()
    incompatible_contract = _contract(feature_columns=["f2", "f1"])
    active = {"version": "phase1-contract-v2", "artifact_contract": active_contract}
    bundle = {
        "ticker_metadata": {
            **model_store.phase1_contract_metadata_fields(incompatible_contract),
            "model_version": "phase1-contract-v2",
            "artifact_contract": incompatible_contract,
        },
        "version_metadata": {
            **model_store.phase1_contract_metadata_fields(active_contract),
            "artifact_contract": active_contract,
        },
    }
    context = {
        "model_cfg": {**MODEL_CONFIG, "model_mode": "phase1"},
        "label_cfg": LABEL_CONFIG,
        "active": active,
    }
    with (
        patch.object(trader_main.model_store, "load_model_bundle", return_value=bundle),
        patch.object(trader_main.phase1, "train_ticker_bundle") as legacy_train,
    ):
        prob_up, ready, provenance = trader_main._predict_for_ticker(
            pd.DataFrame({"f1": [1.0]}),
            {"code": "7011.JP", "name": "三菱重工"},
            context,
        )
    legacy_train.assert_not_called()
    assert prob_up == 0.5
    assert ready is False
    assert provenance["model_version"] == "phase1-contract-v2"
    assert "feature_schema_hash" in provenance["model_error"]


def test_purge_gap_is_at_least_effective_horizon_for_every_split():
    assert model.resolve_purge_gap({"purge_gap": 2}, effective_horizon_days=10) == 10
    labelled = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=150),
            "f": np.linspace(0.0, 1.0, 150),
            "fwd_return": np.linspace(-0.1, 0.1, 150),
            "target_class": [0, 1] * 75,
        }
    )
    selector_splits = []
    refit_pools = []

    def capture_split(train_X, _train_y, val_X, _val_y, seed):
        selector_splits.append((list(train_X.index), list(val_X.index), seed))
        return DummyBooster(score=0.5, identity=f"selector-{seed}")

    def capture_refit(train_X, _train_y, *, seed, num_boost_round):
        refit_pools.append((list(train_X.index), seed, num_boost_round))
        return DummyBooster(score=0.5, identity=f"refit-{seed}")

    with (
        patch.object(model, "_train_single_fold", side_effect=capture_split),
        patch.object(model, "_refit_fixed_rounds", side_effect=capture_refit),
    ):
        folds, final, _oos = model.train_horizon_models(
            labelled,
            ["f"],
            {
                "purge_gap": 2,
                "val_size": 10,
                "n_folds": 2,
                "train_min_rows": 50,
            },
            effective_horizon_days=10,
        )
    assert folds == []
    assert final is not None
    assert len(selector_splits) == 2
    assert len(refit_pools) == 2
    for train_index, val_index, _seed in selector_splits:
        assert min(val_index) - max(train_index) - 1 >= 10
    split = _oos.attrs["deployment_split"]
    assert split["external_oos_used_for_early_stopping"] is False
    assert split["holdout_model_is_persisted_final"] is True
    assert len(_oos[_oos["oos_role"] == "deployment_candidate_holdout"]) == 10
    assert len(_oos[_oos["oos_role"] == "embargo"]) == 10
    assert max(refit_pools[0][0]) + 1 == split["tuning_internal"]["train_pool_end"]
    assert max(refit_pools[1][0]) + 1 == split["deployment_internal"]["train_pool_end"]
    assert split["deployment_internal"]["refit_uses_full_permitted_pool"] is True


def test_single_fold_saved_candidate_fails_closed():
    labelled = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=150),
            "f": np.linspace(0.0, 1.0, 150),
            "fwd_return": np.linspace(-0.1, 0.1, 150),
            "target_class": [0, 1] * 75,
        }
    )
    folds, final, oos = model.train_horizon_models(
        labelled,
        ["f"],
        {"val_size": 10, "n_folds": 1, "train_min_rows": 50, "purge_gap": 5},
        effective_horizon_days=5,
    )
    assert folds == []
    assert final is None
    assert oos.empty
    assert (
        oos.attrs["deployment_split"]["reason"]
        == "at_least_two_folds_required_for_tuning_holdout"
    )


def test_gate_evidence_rejects_logical_contradictions_and_old_source():
    contract = _contract()
    gate_contract = _gate_contract(contract)
    booster = DummyBooster()
    version = "phase1-logical-evidence-v3"
    valid = _valid_gate_evidence(version, contract, gate_contract, booster)
    model_hash = model_store.booster_bundle_sha256({"folds": [], "final": booster})

    contradictions = []
    passed_with_failures = {
        **valid,
        "passed": True,
        "reason": "ok",
        "failures": ["sharpe<0.20"],
    }
    contradictions.append(
        model_store.finalize_phase1_gate_evidence(passed_with_failures)
    )
    bad_metrics = {
        **valid,
        "metrics_holdout": {**valid["metrics_holdout"], "metrics_schema_version": 1},
    }
    contradictions.append(model_store.finalize_phase1_gate_evidence(bad_metrics))
    for field, invalid in (("cagr", float("nan")), ("sharpe", float("inf"))):
        nonfinite = {
            **valid,
            "metrics_holdout": {**valid["metrics_holdout"], field: invalid},
        }
        contradictions.append(model_store.finalize_phase1_gate_evidence(nonfinite))
    missing_metric = {
        **valid,
        "metrics_holdout": {
            field: value
            for field, value in valid["metrics_holdout"].items()
            if field != "round_trips"
        },
    }
    contradictions.append(model_store.finalize_phase1_gate_evidence(missing_metric))
    bad_split = {
        **valid,
        "threshold_optimization": {
            **valid["threshold_optimization"],
            "holdout_rows": valid["split"]["holdout_rows"] + 1,
        },
    }
    contradictions.append(model_store.finalize_phase1_gate_evidence(bad_split))
    malformed_split = {
        **valid,
        "split": {**valid["split"], "holdout_rows": "not-an-integer"},
    }
    contradictions.append(model_store.finalize_phase1_gate_evidence(malformed_split))
    old_source = {**valid, "source": "weekly_report_continuity_gate"}
    contradictions.append(model_store.finalize_phase1_gate_evidence(old_source))

    for evidence in contradictions:
        reasons = model_store.verify_phase1_gate_evidence(
            evidence,
            model_version=version,
            artifact_contract=contract,
            gate_contract=gate_contract,
            model_bundle_sha256=model_hash,
            calibrator_sha256=model_store.payload_sha256(None),
            applied_calibration_id="identity-v1",
        )
        assert reasons


def test_train_ticker_bundle_passes_effective_horizon_to_model_training():
    rows = 170
    horizon = 12
    labelled = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=rows),
            "f": np.linspace(0.0, 1.0, rows),
            "fwd_return": np.linspace(-0.1, 0.1, rows),
            "target_class": [0, 1] * (rows // 2),
            "volatility": np.full(rows, 0.01),
        }
    )
    oos_rows = 42
    oos = labelled.tail(oos_rows).copy().reset_index(drop=True)
    oos["raw_score"] = np.linspace(0.2, 0.8, oos_rows)
    oos["oos_role"] = (
        ["calibration_threshold_tuning"] * 20
        + ["embargo"] * horizon
        + ["deployment_candidate_holdout"] * 10
    )
    oos["market_row_number"] = np.arange(oos_rows)
    oos["market_as_of_date"] = oos["date"]
    oos["entry_date"] = oos["date"] + pd.Timedelta(days=1)
    oos["execution_exit_date"] = oos["date"] + pd.Timedelta(days=horizon)
    oos["entry_price"] = 100.0
    oos["execution_exit_price"] = 101.0
    oos["entry_session_return"] = 0.001
    oos["continuation_session_return"] = 0.001
    oos["execution_contract_version"] = "next_session_open_to_close_v2"
    oos["execution_path_returns"] = [[0.001] * horizon for _ in range(oos_rows)]
    oos["execution_path_dates"] = [
        [
            pd.Timestamp("2025-01-01") + pd.Timedelta(days=i + offset + 1)
            for offset in range(horizon)
        ]
        for i in range(oos_rows)
    ]
    oos["execution_path_market_rows"] = [
        list(range(i + 1, i + horizon + 1)) for i in range(oos_rows)
    ]
    oos.attrs["deployment_split"] = {
        "reason": "ok",
        "tuning_rows": 20,
        "embargo_rows": horizon,
        "holdout_rows": 10,
        "holdout_model_is_persisted_final": True,
        "external_oos_used_for_early_stopping": False,
        "deployment_internal": {
            "train_pool_end": 100,
            "external_oos_used_for_training": False,
            "refit_uses_full_permitted_pool": True,
        },
    }
    label_config = {**LABEL_CONFIG, "tb_max_days": 12}
    final = DummyBooster(score=0.7, identity="persisted-final")
    with (
        patch.object(phase1, "build_labelled_frame", return_value=labelled),
        patch.object(phase1, "phase1_feature_cols", return_value=["f"]),
        patch.object(
            phase1,
            "train_horizon_models",
            return_value=([], final, oos),
        ) as train,
        patch.object(
            phase1, "fit_calibrator", wraps=phase1.fit_calibrator
        ) as fit_calibrator,
        patch.object(
            phase1.backtest_module,
            "_optimize_thresholds",
            wraps=phase1.backtest_module._optimize_thresholds,
        ) as optimize_thresholds,
    ):
        result, info = phase1.train_ticker_bundle(
            labelled,
            {
                "validation_years": 4,
                "train_min_rows": 50,
                "val_size": 10,
                "n_folds": 3,
                "purge_gap": 2,
                "enabled": False,
                "auto_threshold_enabled": False,
            },
            label_config,
            {
                "macro_features_enabled": False,
                "calibration_mode": "none",
                "min_calibration_rows": 60,
            },
            model_version="phase1-test-v3",
        )
    assert info["reason"] == "ok", info
    assert result["metadata"]["effective_horizon_days"] == 12
    assert result["metadata"]["effective_purge_gap"] == 12
    assert result["metadata"]["label_config"]["tb_max_days"] == 12
    assert result["metadata"][
        "feature_schema_hash"
    ] == model_store.phase1_feature_schema_hash(["f"])
    assert result["metadata"]["calibration_id"] == "identity-v1"
    assert result["metadata"]["execution_contract_version"]
    assert result["metadata"]["gate_evidence"]["source"] == (
        model_store.PHASE1_GATE_EVIDENCE_SOURCE
    )
    assert (
        result["metadata"]["gate_evidence"]["split"]["calibration_fit_split"]
        == "tuning"
    )
    assert (
        result["metadata"]["gate_evidence"]["split"]["gate_evaluation_split"]
        == "holdout"
    )
    assert len(fit_calibrator.call_args.args[0]) == 20
    assert len(fit_calibrator.call_args.args[1]) == 20
    assert len(optimize_thresholds.call_args.args[0]) == 20
    assert (
        result["metadata"]["gate_evidence"]["metrics_holdout"]
        == result["gate_result"]["metrics"]
    )
    assert train.call_args.kwargs["effective_horizon_days"] == 12


def test_daily_process_uses_exact_model_gate_thresholds():
    raw = pd.DataFrame({"close": np.linspace(90.0, 100.0, 60)})
    featured = pd.DataFrame({"date": [pd.Timestamp("2026-07-17")], "close": [100.0]})
    thresholds = {
        "buy": 0.91,
        "mild_buy": 0.71,
        "mild_sell": 0.29,
        "sell": 0.09,
        "volatility_limit": 0.04,
    }
    metrics = trader_main._empty_metrics()
    gate_result = {
        "passed": True,
        "reason": "ok",
        "failures": [],
        "metrics": metrics,
        "metrics_tuning": metrics,
        "metrics_holdout": metrics,
        "thresholds": thresholds,
        "threshold_optimization": {"optimized": True},
        "gate_source": model_store.PHASE1_GATE_EVIDENCE_SOURCE,
        "gate_evidence_sha256": "saved-evidence-hash",
    }
    signal_template = {
        "action": "HOLD",
        "reason": "test",
        "prob_up": 0.85,
        "raw_score": 0.84,
        "expected_ret": 0.01,
        "features_hash": "features",
    }
    ctx = {
        "label_cfg": LABEL_CONFIG,
        "model_cfg": MODEL_CONFIG,
        "macro_panel": None,
        "active": {"version": "phase1-v3"},
    }
    with (
        patch.object(trader_main, "update_data", return_value=None),
        patch.object(trader_main, "load_data", return_value=raw),
        patch.object(trader_main, "build_feature_frame", return_value=featured),
        patch.object(
            trader_main,
            "_predict_for_ticker",
            return_value=(
                0.85,
                True,
                {
                    "model_version": "phase1-v3",
                    "horizon_days": 5,
                    "gate_result": gate_result,
                },
            ),
        ),
        patch.object(
            trader_main, "generate_signal", return_value=dict(signal_template)
        ) as generate,
    ):
        signal, backtest_entry = trader_main._process_ticker(
            {"code": "7011.JP", "name": "三菱重工"}, ctx
        )
    assert generate.call_args.kwargs["thresholds"] == thresholds
    assert signal["gate_passed"] is True
    assert signal["thresholds"] == thresholds
    assert backtest_entry["gate_source"] == model_store.PHASE1_GATE_EVIDENCE_SOURCE
    assert backtest_entry["gate_evidence_sha256"] == "saved-evidence-hash"


def test_strict_missing_or_mismatched_gate_evidence_forces_hold():
    raw = pd.DataFrame({"close": np.linspace(90.0, 100.0, 60)})
    featured = pd.DataFrame({"date": [pd.Timestamp("2026-07-17")], "close": [100.0]})
    ctx = {
        "label_cfg": LABEL_CONFIG,
        "model_cfg": {**MODEL_CONFIG, "model_mode": "phase1"},
        "macro_panel": None,
        "active": {"version": "phase1-v3"},
    }
    with (
        patch.object(trader_main, "update_data", return_value=None),
        patch.object(trader_main, "load_data", return_value=raw),
        patch.object(trader_main, "build_feature_frame", return_value=featured),
        patch.object(
            trader_main,
            "_predict_for_ticker",
            return_value=(
                0.5,
                False,
                {
                    "model_version": "phase1-v3",
                    "horizon_days": 5,
                    "model_error": "gate_evidence_field_mismatch:calibrator_sha256",
                },
            ),
        ),
        patch.object(
            trader_main,
            "generate_signal",
            return_value={"action": "BUY", "reason": "would buy", "prob_up": 0.9},
        ),
    ):
        signal, backtest_entry = trader_main._process_ticker(
            {"code": "7011.JP", "name": "三菱重工"}, ctx
        )
    assert signal["action"] == "HOLD"
    assert signal["gate_passed"] is False
    assert signal["status"] == "failed"
    assert backtest_entry["passed"] is False
    assert backtest_entry["gate_source"] == "unavailable"


def test_model_quality_hides_incompatible_active_artifact():
    captured = {}

    def capture_write(_path, payload, indent=None):
        captured.update(payload)

    with (
        patch.object(
            trader_main.dashboard.model_store,
            "read_active_model",
            return_value={"version": "schema-v1-old"},
        ),
        patch.object(
            trader_main.dashboard.model_store,
            "validate_runtime_active_phase1",
            return_value={
                "compatible": False,
                "reasons": [{"code": "artifact_contract_missing"}],
            },
        ),
        patch.object(
            trader_main.dashboard, "_atomic_write_json", side_effect=capture_write
        ),
    ):
        trader_main.dashboard.export_model_quality()
    assert captured["available"] is False
    assert captured["reason"] == "active_model_incompatible"
    assert captured["active_model_version"] == "schema-v1-old"
    assert captured["incompatibilities"] == [{"code": "artifact_contract_missing"}]


ALL_TESTS = [
    test_contract_detects_label_horizon_and_feature_order_changes,
    test_canonical_label_config_normalizes_removed_mode_and_rejects_unknown,
    test_contract_detects_schema_macro_calibration_and_execution_changes,
    test_active_compatibility_checks_pointer_metadata_and_manifest,
    test_old_active_pointer_without_contract_is_rejected_with_reason,
    test_predict_uses_bundle_horizon_not_runtime_horizon,
    test_incompatible_saved_bundle_degrades_to_legacy_in_auto_mode,
    test_incompatible_saved_bundle_is_failed_hold_in_phase1_mode,
    test_purge_gap_is_at_least_effective_horizon_for_every_split,
    test_single_fold_saved_candidate_fails_closed,
    test_gate_evidence_rejects_logical_contradictions_and_old_source,
    test_train_ticker_bundle_passes_effective_horizon_to_model_training,
    test_daily_process_uses_exact_model_gate_thresholds,
    test_strict_missing_or_mismatched_gate_evidence_forces_hold,
    test_model_quality_hides_incompatible_active_artifact,
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
