#!/usr/bin/env python3
"""Deployment-safety tests for scripts/weekly_model_retrain.py."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import lightgbm as lgb  # noqa: E402

from scripts import weekly_model_retrain as weekly  # noqa: E402
from src import model_store  # noqa: E402


def _toy_booster() -> lgb.Booster:
    rng = np.random.default_rng(42)
    X = rng.normal(size=(100, 2))
    y = (X[:, 0] > 0).astype(int)
    return lgb.train(
        {"objective": "binary", "verbosity": -1, "seed": 42},
        lgb.Dataset(X, label=y),
        num_boost_round=5,
    )


def test_default_versions_are_unique_and_source_identified():
    first = weekly._default_version()
    second = weekly._default_version()
    assert first != second
    assert first.startswith("per-ticker-v1-")
    assert weekly._git_commit() in first


def _successful_result(booster: lgb.Booster, version: str) -> dict:
    artifact_contract = model_store.build_phase1_artifact_contract(
        label_config={
            "label_mode": "triple_barrier",
            "horizon_days": 5,
            "tb_max_days": 5,
        },
        feature_columns=["f0", "f1"],
        macro_features_enabled=False,
        calibration_mode="none",
    )
    gate_contract = model_store.build_phase1_gate_contract(
        weekly.BACKTEST_GATE_CONFIG, artifact_contract
    )
    boosters = {"folds": [], "final": booster}
    model_hash = model_store.booster_bundle_sha256(boosters)
    thresholds = {
        "buy": 0.8,
        "mild_buy": 0.6,
        "mild_sell": 0.4,
        "sell": 0.2,
        "volatility_limit": 0.04,
    }
    metrics_tuning = {
        "metrics_schema_version": 2,
        "round_trips": 20,
        "cagr": 0.10,
        "avg_daily_net_return": 0.001,
        "max_drawdown": -0.10,
        "sharpe": 0.8,
    }
    metrics_holdout = {**metrics_tuning, "sharpe": 1.0}
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
    evidence = model_store.build_phase1_gate_evidence(
        model_version=version,
        model_bundle_sha256=model_hash,
        artifact_contract=artifact_contract,
        gate_contract=gate_contract,
        calibrator_sha256=model_store.payload_sha256(None),
        applied_calibration_id="identity-v1",
        oos_prediction_sha256=model_store.payload_sha256({"rows": 180}),
        split=split,
        passed=True,
        skipped=False,
        reason="ok",
        failures=[],
        thresholds=thresholds,
        threshold_optimization=threshold_optimization,
        metrics_tuning=metrics_tuning,
        metrics_holdout=metrics_holdout,
    )
    return {
        "boosters": boosters,
        "metadata": {
            "calibration": None,
            "feature_reference": {"feature_cols": ["f0", "f1"]},
            "artifact_contract": artifact_contract,
            "gate_contract": gate_contract,
            **model_store.phase1_gate_contract_metadata_fields(gate_contract),
            "model_version": version,
            "model_bundle_sha256": model_hash,
            "calibrator_sha256": model_store.payload_sha256(None),
            "applied_calibration_id": "identity-v1",
            "gate_evidence": evidence,
        },
        "cv_metrics": {
            "ic": 0.1,
            "brier": 0.2,
            "brier_raw": 0.21,
            "auc": 0.6,
            "hit_rate": 0.55,
            "calibration": {"rows": 60, "applied": False},
        },
        "gate_result": model_store.phase1_gate_result_from_evidence(evidence),
    }


def _run_mocked_retrain(
    report_path: Path,
    candidate: str,
    *,
    tickers: list[dict],
    frame: pd.DataFrame,
    train_side_effect,
    gate: dict,
    db_enabled: bool = False,
) -> int:
    with (
        patch.object(weekly, "TICKERS", tickers),
        patch.object(weekly, "load_macro_panel", return_value=None),
        patch.object(weekly, "update_data", return_value=None),
        patch.object(weekly, "load_data", return_value=frame),
        patch.object(weekly, "build_feature_frame", return_value=frame),
        patch.object(weekly, "train_ticker_bundle", side_effect=train_side_effect),
        patch.object(weekly, "phase1_feature_cols", return_value=["f0", "f1"]),
        patch.object(
            weekly,
            "get_label_config",
            return_value={
                "label_mode": "triple_barrier",
                "horizon_days": 5,
                "tb_max_days": 5,
            },
        ),
        patch.object(
            weekly,
            "get_model_runtime_config",
            return_value={
                "macro_features_enabled": False,
                "calibration_mode": "none",
                "min_calibration_rows": 60,
            },
        ),
        patch.object(weekly.db, "db_enabled", return_value=db_enabled),
        patch("src.dashboard.export_model_quality", return_value=None),
    ):
        return weekly.run_retrain(report_path, candidate)


def test_partial_weekly_run_keeps_old_active_and_cleans_staging():
    with tempfile.TemporaryDirectory() as tmp:
        model_dir = Path(tmp) / "models"
        report_path = Path(tmp) / "weekly_report.json"
        env = {
            "TRADER_MODEL_DIR": str(model_dir),
            "TRADER_DB_ENABLED": "false",
            "TRADER_DB_FALLBACK_DIR": str(Path(tmp) / "outbox"),
        }
        tickers = [
            {"code": "7011.JP", "name": "成功銘柄"},
            {"code": "6501.JP", "name": "失敗銘柄"},
        ]
        old_version = "per-ticker-v1-old"
        candidate = "per-ticker-v1-20260720T100000-abc123-00000001"
        frame = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=60)})
        booster = _toy_booster()
        successful_result = _successful_result(booster, candidate)
        gate = {
            "passed": True,
            "reason": "ok",
            "failures": [],
            "metrics": {"sharpe": 1.0},
            "horizon_days": 5,
            "label_mode": "triple_barrier",
        }

        def train_side_effect(*_args, **_kwargs):
            if train_side_effect.calls == 0:
                train_side_effect.calls += 1
                return successful_result, {"reason": "ok"}
            return None, {"reason": "training_failed"}

        train_side_effect.calls = 0

        with patch.dict(os.environ, env, clear=False):
            model_store.write_active_model(old_version)
            model_store.save_version_metadata(
                old_version,
                {
                    "version": old_version,
                    "universe": [t["code"] for t in tickers],
                    "trained_tickers": [t["code"] for t in tickers],
                },
            )
            assert (
                _run_mocked_retrain(
                    report_path,
                    candidate,
                    tickers=tickers,
                    frame=frame,
                    train_side_effect=train_side_effect,
                    gate=gate,
                )
                == 0
            )

            active = model_store.read_active_model()
            assert active["version"] == old_version
            assert not (model_dir / candidate).exists()
            staging_parent = model_dir / ".staging"
            assert not staging_parent.exists() or not any(staging_parent.iterdir())

        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["active_set"] is False
        assert report["active_model_version"] == old_version
        assert report["deployment"]["status"] == "rejected"
        assert report["deployment"]["staging_cleanup"]["status"] == "removed"
        failure_codes = {
            row["code"]
            for row in report["deployment"]["candidate_validation"]["failures"]
        }
        assert "target_coverage_incomplete" in failure_codes


def test_complete_weekly_run_activates_manifested_candidate():
    with tempfile.TemporaryDirectory() as tmp:
        model_dir = Path(tmp) / "models"
        report_path = Path(tmp) / "weekly_report.json"
        candidate = "per-ticker-v1-20260720T110000-abc123-00000002"
        ticker = {"code": "7011.JP", "name": "成功銘柄"}
        frame = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=60)})
        result = _successful_result(_toy_booster(), candidate)
        gate = {
            "passed": True,
            "reason": "ok",
            "failures": [],
            "metrics": {"sharpe": 1.0},
            "horizon_days": 5,
            "label_mode": "triple_barrier",
        }
        env = {
            "TRADER_MODEL_DIR": str(model_dir),
            "TRADER_DB_ENABLED": "false",
            "TRADER_DB_FALLBACK_DIR": str(Path(tmp) / "outbox"),
        }
        with patch.dict(os.environ, env, clear=False):
            assert (
                _run_mocked_retrain(
                    report_path,
                    candidate,
                    tickers=[ticker],
                    frame=frame,
                    train_side_effect=lambda *_args, **_kwargs: (
                        result,
                        {"reason": "ok"},
                    ),
                    gate=gate,
                )
                == 0
            )
            active = model_store.read_active_model()
            assert active["version"] == candidate
            assert active["manifest_sha256"] == model_store.phase1_manifest_sha256(
                candidate
            )
            assert model_store.verify_phase1_manifest(candidate)["passed"] is True
            version_metadata = model_store.read_version_metadata(candidate)
            bundle = model_store.load_model_bundle(candidate, "7011.JP")
            assert bundle is not None
            contract = active["artifact_contract"]
            required_fields = {
                "artifact_schema_version",
                "label_config",
                "effective_horizon_days",
                "feature_columns",
                "feature_schema_hash",
                "macro_features_enabled",
                "calibration_mode",
                "calibration_id",
                "execution_contract_version",
            }
            for metadata in (active, version_metadata, bundle["ticker_metadata"]):
                assert metadata["artifact_contract"] == contract
                assert required_fields.issubset(metadata)
            staging_parent = model_dir / ".staging"
            assert not staging_parent.exists() or not any(staging_parent.iterdir())

            outbox_files = list((Path(tmp) / "outbox").glob("*.jsonl"))
            assert len(outbox_files) == 1
            events = [
                json.loads(line)
                for line in outbox_files[0].read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            assert len(events) == 1
            registry_event = events[0]
            assert registry_event["kind"] == "model_registry"
            assert registry_event["row"]["version"] == candidate
            pointer = registry_event["row"]["params"]["file_active_pointer"]
            assert pointer["version"] == candidate
            assert pointer["manifest_sha256"] == active["manifest_sha256"]
            assert pointer["config_sha256"] == active["config_sha256"]

        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["active_set"] is True
        assert report["deployment"]["status"] == "activated"
        assert report["deployment"]["candidate_validation"]["passed"] is True
        assert report["deployment"]["staging_cleanup"]["status"] == "removed"
        assert report["db_registered"] is False
        assert report["db_registry_status"] == "queued"
        assert report["db_registry_queued"] == 1
        assert report["db_registry_error"] == "db_disabled"
        assert report["deployment"]["registry_sync"]["status"] == "queued"
        assert report["deployment"]["registry_sync"]["queue_error"] is None


def test_registry_failure_after_activation_is_queued_and_reported():
    class FakeConn:
        closed = False

        def close(self):
            self.closed = True

    with tempfile.TemporaryDirectory() as tmp:
        model_dir = Path(tmp) / "models"
        report_path = Path(tmp) / "weekly_report.json"
        candidate = "per-ticker-v1-20260720T120000-abc123-00000003"
        ticker = {"code": "7011.JP", "name": "成功銘柄"}
        frame = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=60)})
        result = _successful_result(_toy_booster(), candidate)
        gate = {
            "passed": True,
            "reason": "ok",
            "failures": [],
            "metrics": {"sharpe": 1.0},
            "horizon_days": 5,
            "label_mode": "triple_barrier",
        }
        env = {
            "TRADER_MODEL_DIR": str(model_dir),
            "TRADER_DB_ENABLED": "true",
            "DATABASE_URL": "postgresql://unused.invalid/test",
            "TRADER_DB_FALLBACK_DIR": str(Path(tmp) / "outbox"),
        }
        fake_conn = FakeConn()
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(weekly.db, "connect", return_value=fake_conn),
            patch.object(
                weekly.db,
                "register_model_version",
                side_effect=RuntimeError("registry unavailable"),
            ),
        ):
            assert (
                _run_mocked_retrain(
                    report_path,
                    candidate,
                    tickers=[ticker],
                    frame=frame,
                    train_side_effect=lambda *_args, **_kwargs: (
                        result,
                        {"reason": "ok"},
                    ),
                    gate=gate,
                    db_enabled=True,
                )
                == 0
            )

        assert fake_conn.closed is True
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["active_set"] is True
        assert report["db_registered"] is False
        assert report["db_registry_status"] == "queued"
        assert report["db_registry_queued"] == 1
        assert "RuntimeError: registry unavailable" in report["db_registry_error"]
        assert report["deployment"]["registry_sync"]["status"] == "queued"


ALL_TESTS = [
    test_default_versions_are_unique_and_source_identified,
    test_partial_weekly_run_keeps_old_active_and_cleans_staging,
    test_complete_weekly_run_activates_manifested_candidate,
    test_registry_failure_after_activation_is_queued_and_reported,
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
