#!/usr/bin/env python3
"""
Unit tests for src/model_store.py (file I/O + LightGBM round-trip, no DB).

Runnable two ways:
  uv run python tests/test_model_store.py     # standalone
  uv run pytest tests/test_model_store.py      # if pytest is available
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import lightgbm as lgb  # noqa: E402

from src import model_store as ms  # noqa: E402


def _toy_booster(seed: int = 0) -> lgb.Booster:
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(200, 4))
    # Learnable signal so the booster is non-trivial.
    y = (X[:, 0] + 0.5 * X[:, 1] > 0).astype(int)
    params = {"objective": "binary", "num_leaves": 7, "verbosity": -1, "seed": seed}
    return lgb.train(params, lgb.Dataset(X, label=y), num_boost_round=20)


def test_save_load_roundtrip_same_prediction():
    with tempfile.TemporaryDirectory() as tmp:
        version = "per-ticker-v1-20260613"
        ticker = "7011.JP"
        fold = _toy_booster(1)
        final = _toy_booster(2)
        feature_reference = {"feature_cols": ["a", "b", "c", "d"], "avg_up_ret": 0.03}
        calibration = {"method": "isotonic", "x": [0.1, 0.9], "y": [0.2, 0.8]}

        ms.save_model_bundle(
            version,
            ticker,
            {"folds": [fold], "final": final},
            {
                "calibration": calibration,
                "feature_reference": feature_reference,
                "cv_metrics": {"ic": 0.05},
            },
            model_dir=tmp,
        )

        bundle = ms.load_model_bundle(version, ticker, model_dir=tmp)
        assert bundle is not None
        assert len(bundle["folds"]) == 1
        assert bundle["calibration"] == calibration
        assert bundle["feature_reference"]["feature_cols"] == ["a", "b", "c", "d"]
        assert bundle["ticker_metadata"]["cv_metrics"]["ic"] == 0.05

        # Predictions must match the original boosters exactly.
        X = np.random.default_rng(99).normal(size=(10, 4))
        assert np.allclose(bundle["final"].predict(X), final.predict(X))
        assert np.allclose(bundle["folds"][0].predict(X), fold.predict(X))


def test_load_missing_version_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        assert ms.load_model_bundle("nope-version", "7011.JP", model_dir=tmp) is None


def test_active_model_write_read_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        active = str(Path(tmp) / "active_model.json")
        ms.write_active_model(
            "per-ticker-v1-20260613",
            {
                "version": "must-not-override",
                "horizon_days": 5,
                "kind": "per_ticker_horizon_v1",
            },
            active_file=active,
        )
        data = ms.read_active_model(active_file=active)
        assert data["version"] == "per-ticker-v1-20260613"
        assert data["horizon_days"] == 5


def test_read_active_model_missing_is_none():
    with tempfile.TemporaryDirectory() as tmp:
        assert ms.read_active_model(active_file=str(Path(tmp) / "missing.json")) is None


def test_read_active_model_corrupt_is_none():
    with tempfile.TemporaryDirectory() as tmp:
        active = Path(tmp) / "active_model.json"
        active.write_text("{ this is not valid json", encoding="utf-8")
        assert ms.read_active_model(active_file=str(active)) is None
        # missing "version" key is also invalid
        active.write_text('{"foo": 1}', encoding="utf-8")
        assert ms.read_active_model(active_file=str(active)) is None
        active.write_text('{"version": "../escape"}', encoding="utf-8")
        assert ms.read_active_model(active_file=str(active)) is None


def test_version_metadata_roundtrip_and_artifact_uri():
    with tempfile.TemporaryDirectory() as tmp:
        version = "per-ticker-v1-20260613"
        meta = {
            "kind": "per_ticker_horizon_v1",
            "horizon_days": 5,
            "universe": ["7011.JP"],
        }
        path = ms.save_version_metadata(version, meta, model_dir=tmp)
        assert path == ms.artifact_uri(version, model_dir=tmp)
        assert ms.read_version_metadata(version, model_dir=tmp)["horizon_days"] == 5


def test_clear_active_model():
    with tempfile.TemporaryDirectory() as tmp:
        active = str(Path(tmp) / "active_model.json")
        ms.write_active_model("v1", {}, active_file=active)
        assert ms.read_active_model(active_file=active) is not None
        ms.clear_active_model(active_file=active)
        assert ms.read_active_model(active_file=active) is None


def _staged_phase1_candidate(
    model_dir: str,
    version: str,
    *,
    target_tickers: list[str],
    trained_tickers: list[str],
) -> tuple[str, dict]:
    staging = ms.create_staging_model_dir(version, model_dir=model_dir)
    artifact_contract = ms.build_phase1_artifact_contract(
        label_config={
            "label_mode": "triple_barrier",
            "horizon_days": 5,
            "tb_tp_atr": 1.5,
            "tb_sl_atr": 1.0,
            "tb_max_days": 5,
            "vol_col": "volatility",
        },
        feature_columns=["a", "b", "c", "d"],
        macro_features_enabled=False,
        calibration_mode="none",
    )
    gate_contract = ms.build_phase1_gate_contract(
        {
            "val_size": 10,
            "n_folds": 3,
            "train_min_rows": 50,
            "purge_gap": 5,
        },
        artifact_contract,
    )
    thresholds = {
        "buy": 0.8,
        "mild_buy": 0.6,
        "mild_sell": 0.4,
        "sell": 0.2,
        "volatility_limit": 0.04,
    }
    split = {
        "data_split": "chronological_embargoed_holdout",
        "holdout_used": True,
        "holdout_rows": 10,
        "tuning_rows": 15,
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
    for idx, ticker in enumerate(trained_tickers):
        booster = _toy_booster(100 + idx)
        boosters = {"folds": [], "final": booster}
        model_hash = ms.booster_bundle_sha256(boosters)
        evidence = ms.build_phase1_gate_evidence(
            model_version=version,
            model_bundle_sha256=model_hash,
            artifact_contract=artifact_contract,
            gate_contract=gate_contract,
            calibrator_sha256=ms.payload_sha256(None),
            applied_calibration_id="identity-v1",
            oos_prediction_sha256=ms.payload_sha256({"ticker": ticker, "rows": 30}),
            split=split,
            passed=True,
            skipped=False,
            reason="ok",
            failures=[],
            thresholds=thresholds,
            threshold_optimization=threshold_optimization,
            metrics_tuning={
                "metrics_schema_version": 3,
                "round_trips": 20,
                "independent_signal_cohorts": 20,
                "cagr": 0.10,
                "avg_daily_net_return": 0.001,
                "max_drawdown": -0.10,
                "sharpe": 0.4,
            },
            metrics_holdout={
                "metrics_schema_version": 3,
                "round_trips": 20,
                "independent_signal_cohorts": 20,
                "cagr": 0.10,
                "avg_daily_net_return": 0.001,
                "max_drawdown": -0.10,
                "sharpe": 0.5,
            },
        )
        ms.save_model_bundle(
            version,
            ticker,
            boosters,
            {
                "calibration": None,
                "feature_reference": {"feature_cols": ["a", "b", "c", "d"]},
                **ms.phase1_contract_metadata_fields(artifact_contract),
                **ms.phase1_gate_contract_metadata_fields(gate_contract),
                "model_version": version,
                "model_bundle_sha256": model_hash,
                "artifact_contract": artifact_contract,
                "gate_contract": gate_contract,
                "calibrator_sha256": ms.payload_sha256(None),
                "applied_calibration_id": "identity-v1",
                "gate_evidence": evidence,
            },
            model_dir=staging,
        )

    config_payload = {"label_mode": "triple_barrier", "horizon_days": 5}
    metadata = {
        "version": version,
        "kind": "per_ticker_horizon_v1",
        **ms.phase1_contract_metadata_fields(artifact_contract),
        **ms.phase1_gate_contract_metadata_fields(gate_contract),
        "artifact_contract": artifact_contract,
        "gate_contract": gate_contract,
        "config_sha256": ms.payload_sha256(config_payload),
        "universe": target_tickers,
        "trained_tickers": trained_tickers,
    }
    ms.save_version_metadata(version, metadata, model_dir=staging)
    failures = [
        {"ticker": ticker, "reason": "training_failed"}
        for ticker in target_tickers
        if ticker not in trained_tickers
    ]
    manifest = ms.create_phase1_manifest(
        version,
        target_tickers=target_tickers,
        trained_tickers=trained_tickers,
        failures=failures,
        config_payload=config_payload,
        artifact_contract=artifact_contract,
        gate_contract=gate_contract,
        generated_at="2026-07-20T10:00:00+09:00",
        git_commit="abc123def456",
        model_dir=staging,
    )
    ms.save_phase1_manifest(version, manifest, model_dir=staging)
    return staging, metadata


def _record_candidate_decision(
    model_dir: str, staging: str, version: str, previous_metadata=None
) -> dict:
    decision = ms.evaluate_phase1_candidate(
        version,
        previous_metadata=previous_metadata,
        model_dir=staging,
    )
    ms.record_phase1_candidate_validation(
        version,
        decision,
        staging_model_dir=staging,
        model_dir=model_dir,
    )
    return decision


def test_phase1_candidate_promotes_and_becomes_immutable():
    with tempfile.TemporaryDirectory() as tmp:
        version = "per-ticker-v1-20260720T100000-abc123-00000001"
        tickers = ["7011.JP", "6501.JP"]
        staging, _ = _staged_phase1_candidate(
            tmp, version, target_tickers=tickers, trained_tickers=tickers
        )
        decision = _record_candidate_decision(tmp, staging, version)
        assert decision["passed"] is True

        active_file = str(Path(tmp) / "active_model.json")
        promoted = ms.activate_staged_phase1_version(
            version,
            staging,
            pointer_metadata={"kind": "per_ticker_horizon_v1", "n_models": 2},
            model_dir=tmp,
            active_file=active_file,
        )
        assert Path(promoted) == Path(tmp) / version
        active = ms.read_active_model(active_file=active_file)
        assert active["version"] == version
        assert active["manifest_sha256"] == ms.phase1_manifest_sha256(
            version, model_dir=tmp
        )
        assert ms.verify_phase1_manifest(version, model_dir=tmp)["passed"] is True

        try:
            ms.save_version_metadata(version, {"version": "mutated"}, model_dir=tmp)
            assert False, "finalized version must reject metadata overwrite"
        except FileExistsError:
            pass

        assert ms.discard_staging_model_dir(staging, version, model_dir=tmp)


def test_partial_candidate_rejected_and_old_active_preserved():
    with tempfile.TemporaryDirectory() as tmp:
        old_version = "per-ticker-v1-old"
        active_file = str(Path(tmp) / "active_model.json")
        ms.write_active_model(old_version, active_file=active_file)

        version = "per-ticker-v1-partial"
        targets = ["7011.JP", "6501.JP"]
        staging, _ = _staged_phase1_candidate(
            tmp,
            version,
            target_tickers=targets,
            trained_tickers=["7011.JP"],
        )
        decision = _record_candidate_decision(
            tmp,
            staging,
            version,
            previous_metadata={"trained_tickers": targets},
        )
        assert decision["passed"] is False
        codes = {row["code"] for row in decision["failures"]}
        assert "target_coverage_incomplete" in codes
        assert "active_coverage_regression" in codes

        try:
            ms.activate_staged_phase1_version(
                version,
                staging,
                pointer_metadata={"kind": "per_ticker_horizon_v1"},
                model_dir=tmp,
                active_file=active_file,
            )
            assert False, "rejected candidate must not activate"
        except ValueError:
            pass
        assert ms.read_active_model(active_file=active_file)["version"] == old_version
        assert not (Path(tmp) / version).exists()
        assert ms.discard_staging_model_dir(staging, version, model_dir=tmp)


def test_manifest_detects_artifact_tampering():
    with tempfile.TemporaryDirectory() as tmp:
        version = "per-ticker-v1-tamper"
        staging, _ = _staged_phase1_candidate(
            tmp,
            version,
            target_tickers=["7011.JP"],
            trained_tickers=["7011.JP"],
        )
        final_model = Path(staging) / version / "7011.JP" / "final.txt"
        with final_model.open("a", encoding="utf-8") as fh:
            fh.write("\n# tampered\n")
        result = ms.verify_phase1_manifest(version, model_dir=staging)
        assert result["passed"] is False
        assert "artifact_checksum_mismatch" in {
            row["code"] for row in result["failures"]
        }
        assert ms.discard_staging_model_dir(staging, version, model_dir=tmp)


def test_candidate_gate_provenance_extension_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        version = "per-ticker-v1-provenance"
        staging, _ = _staged_phase1_candidate(
            tmp,
            version,
            target_tickers=["7011.JP"],
            trained_tickers=["7011.JP"],
        )
        decision = ms.evaluate_phase1_candidate(
            version,
            model_dir=staging,
            required_gate_provenance={
                "source": "wrong-source",
            },
        )
        assert decision["passed"] is False
        mismatch = next(
            row
            for row in decision["failures"]
            if row["code"] == "gate_provenance_mismatch"
        )
        assert {row.get("field") for row in mismatch["by_ticker"]["7011.JP"]} == {
            "source"
        }
        assert ms.discard_staging_model_dir(staging, version, model_dir=tmp)


def test_existing_version_name_is_never_reused():
    with tempfile.TemporaryDirectory() as tmp:
        version = "per-ticker-v1-existing"
        (Path(tmp) / version).mkdir()
        try:
            ms.create_staging_model_dir(version, model_dir=tmp)
            assert False, "existing version must never be reused"
        except FileExistsError:
            pass


def test_pointer_failure_rolls_back_promoted_candidate():
    with tempfile.TemporaryDirectory() as tmp:
        old_version = "per-ticker-v1-old"
        active_file = str(Path(tmp) / "active_model.json")
        ms.write_active_model(old_version, active_file=active_file)

        version = "per-ticker-v1-pointer-failure"
        staging, _ = _staged_phase1_candidate(
            tmp,
            version,
            target_tickers=["7011.JP"],
            trained_tickers=["7011.JP"],
        )
        assert _record_candidate_decision(tmp, staging, version)["passed"] is True

        with patch.object(ms, "write_active_model", side_effect=OSError("disk full")):
            try:
                ms.activate_staged_phase1_version(
                    version,
                    staging,
                    pointer_metadata={"kind": "per_ticker_horizon_v1"},
                    model_dir=tmp,
                    active_file=active_file,
                )
                assert False, "pointer failure must propagate"
            except OSError:
                pass
        assert ms.read_active_model(active_file=active_file)["version"] == old_version
        assert not (Path(tmp) / version).exists()
        assert ms.discard_staging_model_dir(staging, version, model_dir=tmp)


def test_cs_bundle_save_load_roundtrip():
    import pandas as pd

    with tempfile.TemporaryDirectory() as tmp:
        version = "cs-v1-20260613"
        booster = _toy_booster(seed=42)
        feature_schema = {
            "feature_cols": ["f0", "f1", "f2", "f3"],
            "objective": "ranker",
            "macro_enabled": False,
        }
        calibration = {
            "bucket_0": {"up_prob": 0.2, "expected_ret": -0.01},
            "bucket_1": {"up_prob": 0.8, "expected_ret": 0.03},
        }
        sector_encoder = {"Technology": 0, "Finance": 1}
        universe = ["7011.JP", "9984.JP", "6758.JP"]
        oos_df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-01-05", "2026-01-06"]),
                "ticker": ["7011.JP", "9984.JP"],
                "raw_score": [0.6, 0.4],
                "fwd_return": [0.02, -0.01],
                "target_up": [1, 0],
                "target_vol_norm": [1.1, 0.9],
                "target_rank_bucket": [2, 1],
            }
        )

        ms.save_cs_bundle(
            version,
            booster,
            feature_schema=feature_schema,
            calibration=calibration,
            sector_encoder=sector_encoder,
            universe=universe,
            oos_predictions=oos_df,
            model_dir=tmp,
        )

        bundle = ms.load_cs_bundle(version, model_dir=tmp)
        assert bundle is not None
        assert bundle["version"] == version

        # Booster predictions must match original.
        X = np.random.default_rng(7).normal(size=(10, 4))
        assert np.allclose(bundle["booster"].predict(X), booster.predict(X))

        # Scalar fields round-trip.
        assert bundle["feature_cols"] == feature_schema["feature_cols"]
        assert bundle["feature_schema"] == feature_schema
        assert bundle["calibration"] == calibration
        assert bundle["sector_encoder"] == sector_encoder
        assert bundle["universe"] == universe

        # OOS DataFrame round-trips (same shape + columns).
        assert bundle["oos_predictions"] is not None
        assert bundle["oos_predictions"].shape == oos_df.shape
        assert list(bundle["oos_predictions"].columns) == list(oos_df.columns)


def test_cs_bundle_missing_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        assert ms.load_cs_bundle("nope", model_dir=tmp) is None


def test_cs_bundle_oos_optional():
    with tempfile.TemporaryDirectory() as tmp:
        version = "cs-v1-20260614"
        booster = _toy_booster(seed=5)
        feature_schema = {
            "feature_cols": ["f0", "f1", "f2", "f3"],
            "objective": "ranker",
        }

        ms.save_cs_bundle(
            version,
            booster,
            feature_schema=feature_schema,
            oos_predictions=None,
            model_dir=tmp,
        )

        bundle = ms.load_cs_bundle(version, model_dir=tmp)
        assert bundle is not None
        assert bundle["oos_predictions"] is None

        # Booster still loads correctly.
        X = np.random.default_rng(8).normal(size=(10, 4))
        assert np.allclose(bundle["booster"].predict(X), booster.predict(X))


def test_active_cs_model_roundtrip_and_isolation():
    with tempfile.TemporaryDirectory() as tmp:
        cs_file = str(Path(tmp) / "active_cs_model.json")
        p1_file = str(Path(tmp) / "active_model.json")

        # Write CS pointer.
        ms.write_active_cs_model(
            "cs-v1-20260613",
            {"kind": "cross_sectional_ranker_v1", "horizon_days": 5},
            active_file=cs_file,
        )

        # Write Phase 1 pointer to a DIFFERENT file.
        ms.write_active_model(
            "per-ticker-v1-x",
            {"kind": "per_ticker_horizon_v1"},
            active_file=p1_file,
        )

        # Read CS pointer back and check fields.
        cs_data = ms.read_active_cs_model(active_file=cs_file)
        assert cs_data is not None
        assert cs_data["version"] == "cs-v1-20260613"
        assert cs_data["kind"] == "cross_sectional_ranker_v1"
        assert cs_data["horizon_days"] == 5

        # Read Phase 1 pointer back independently.
        p1_data = ms.read_active_model(active_file=p1_file)
        assert p1_data is not None
        assert p1_data["version"] == "per-ticker-v1-x"

        # Cross-reads must not collide: CS file lacks Phase 1 version string, P1 lacks CS kind.
        assert cs_data["version"] != p1_data["version"]


def test_clear_active_cs_model():
    with tempfile.TemporaryDirectory() as tmp:
        cs_file = str(Path(tmp) / "active_cs_model.json")
        ms.write_active_cs_model("cs-v1-20260613", active_file=cs_file)
        assert ms.read_active_cs_model(active_file=cs_file) is not None
        ms.clear_active_cs_model(active_file=cs_file)
        assert ms.read_active_cs_model(active_file=cs_file) is None


ALL_TESTS = [
    test_save_load_roundtrip_same_prediction,
    test_load_missing_version_returns_none,
    test_active_model_write_read_roundtrip,
    test_read_active_model_missing_is_none,
    test_read_active_model_corrupt_is_none,
    test_version_metadata_roundtrip_and_artifact_uri,
    test_clear_active_model,
    test_phase1_candidate_promotes_and_becomes_immutable,
    test_partial_candidate_rejected_and_old_active_preserved,
    test_manifest_detects_artifact_tampering,
    test_candidate_gate_provenance_extension_fails_closed,
    test_existing_version_name_is_never_reused,
    test_pointer_failure_rolls_back_promoted_candidate,
    test_cs_bundle_save_load_roundtrip,
    test_cs_bundle_missing_returns_none,
    test_cs_bundle_oos_optional,
    test_active_cs_model_roundtrip_and_isolation,
    test_clear_active_cs_model,
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
