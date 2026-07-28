"""
Model artifact store (roadmap §5 Phase 1, W3).

Persists per-ticker LightGBM ensembles plus their calibration and feature
reference to disk, and tracks the single active model version via a small
pointer file. The weekly retrain writes artifacts; the daily run reads the
active model for inference. Auto mode trains an exact-contract ephemeral
candidate when the pointer or a compatible ticker bundle is unavailable.

Artifact layout (committed to git so daily CI can read it)::

    data/models/
      active_model.json
      .staging/<unique-run>/per-ticker-v1-.../  (never active)
      per-ticker-v1-.../                       (immutable after promotion)
        manifest.json
        metadata.json
        7011.JP/
          fold_0.txt fold_1.txt fold_2.txt final.txt
          calibration.json
          feature_reference.json
          ticker_metadata.json   (optional: cv_metrics, expected-return stats)
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import warnings
from pathlib import Path

from .config import DATA_DIR
from .db_records import EPHEMERAL_PHASE1_MODEL_VERSION_PREFIX


PHASE1_ARTIFACT_SCHEMA_VERSION = 3
# Backward-compatible name used by the DR-008 deployment helpers/tests.
PHASE1_MANIFEST_SCHEMA_VERSION = PHASE1_ARTIFACT_SCHEMA_VERSION
PHASE1_MANIFEST_FILE = "manifest.json"
PHASE1_GATE_EVIDENCE_SCHEMA_VERSION = 1
PHASE1_GATE_EVIDENCE_SOURCE = "deployed_candidate_purged_oos_v1"
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_LABEL_CONTRACT_FIELDS = (
    "label_mode",
    "horizon_days",
    "tb_tp_atr",
    "tb_sl_atr",
    "tb_max_days",
    "vol_col",
)
_CALIBRATION_IDS = {
    "isotonic": "isotonic-pava-v1",
    "none": "identity-v1",
}
_GATE_THRESHOLD_FIELDS = (
    "buy",
    "mild_buy",
    "mild_sell",
    "sell",
    "volatility_limit",
)


def _model_dir(model_dir: str | None = None) -> Path:
    base = model_dir or os.environ.get("TRADER_MODEL_DIR") or str(DATA_DIR / "models")
    return Path(base)


def _active_file(active_file: str | None = None, model_dir: str | None = None) -> Path:
    raw = active_file or os.environ.get("TRADER_MODEL_ACTIVE_FILE")
    if raw:
        return Path(raw)
    return _model_dir(model_dir) / "active_model.json"


def version_dir(version: str, model_dir: str | None = None) -> Path:
    _validate_version(version)
    return _model_dir(model_dir) / version


def ticker_dir(version: str, ticker: str, model_dir: str | None = None) -> Path:
    return version_dir(version, model_dir) / ticker


def artifact_uri(version: str, model_dir: str | None = None) -> str:
    """Path recorded in model_registry.artifact_uri."""
    return str(version_dir(version, model_dir) / "metadata.json")


def _write_json(path: Path, payload) -> None:
    """Durably write JSON, replacing the destination in one filesystem step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _validate_version(version: str) -> None:
    """Reject path traversal and ambiguous model-version names."""
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid model version: {version!r}")


def _ensure_version_mutable(version: str, model_dir: str | None = None) -> None:
    """A Phase 1 directory becomes immutable once its manifest is written."""
    manifest = version_dir(version, model_dir) / PHASE1_MANIFEST_FILE
    if manifest.exists():
        raise FileExistsError(
            f"model version {version!r} is finalized and cannot be overwritten"
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_sha256(payload) -> str:
    """Stable SHA-256 for JSON-compatible configuration/provenance payloads."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def booster_bundle_sha256(boosters: dict) -> str:
    """Stable identity of the exact boosters used by persisted inference."""
    digest = hashlib.sha256(b"phase1-booster-bundle-v1\0")
    ordered = [*(boosters.get("folds") or []), boosters.get("final")]
    usable = [booster for booster in ordered if booster is not None]
    if not usable:
        raise ValueError("Phase 1 booster bundle is empty")
    for index, booster in enumerate(usable):
        model_to_string = getattr(booster, "model_to_string", None)
        if not callable(model_to_string):
            raise TypeError("Phase 1 booster does not support model_to_string")
        encoded = model_to_string().encode("utf-8")
        digest.update(str(index).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(len(encoded)).encode("ascii"))
        digest.update(b"\0")
        digest.update(encoded)
        digest.update(b"\0")
    return digest.hexdigest()


def canonical_phase1_gate_config(
    gate_config: dict | None, *, effective_horizon_days: int
) -> dict:
    """Normalize only settings that affect Phase 1 training/gating semantics."""
    cfg = gate_config or {}
    horizon = max(1, int(effective_horizon_days))
    objective = (
        str(cfg.get("auto_threshold_objective", "avg_daily_net_return")).strip().lower()
    )
    if objective == "expectancy":
        objective = "avg_daily_net_return"
    sample_metric = str(
        cfg.get("sample_sufficiency_metric", "independent_signal_cohorts")
    ).strip()
    if sample_metric not in {"round_trips", "independent_signal_cohorts"}:
        raise ValueError(
            f"unsupported Phase 1 sample sufficiency metric: {sample_metric!r}"
        )
    configured_purge = max(0, int(cfg.get("purge_gap", 5)))
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "validation_years": max(1, int(cfg.get("validation_years", 4))),
        "val_size": max(1, int(cfg.get("val_size", 60))),
        "purge_gap": max(configured_purge, horizon),
        "n_folds": max(1, int(cfg.get("n_folds", 3))),
        "train_min_rows": max(50, int(cfg.get("train_min_rows", 200))),
        "cost_bps": float(cfg.get("cost_bps", 10.0)),
        "slippage_bps": float(cfg.get("slippage_bps", 5.0)),
        "allow_short": bool(cfg.get("allow_short", False)),
        "min_cagr": float(cfg.get("min_cagr", 0.03)),
        "min_avg_daily_net_return": float(
            cfg.get(
                "min_avg_daily_net_return",
                cfg.get("min_expectancy", 0.0001),
            )
        ),
        "max_drawdown": float(cfg.get("max_drawdown", 0.25)),
        "min_sharpe": float(cfg.get("min_sharpe", 0.20)),
        "min_round_trips": int(cfg.get("min_round_trips", cfg.get("min_trades", 10))),
        "sample_sufficiency_metric": sample_metric,
        "min_independent_signal_cohorts": int(
            cfg.get("min_independent_signal_cohorts", 5)
        ),
        "auto_threshold_enabled": bool(cfg.get("auto_threshold_enabled", True)),
        "auto_threshold_min_round_trips": int(
            cfg.get(
                "auto_threshold_min_round_trips",
                cfg.get("auto_threshold_min_trades", 8),
            )
        ),
        "auto_threshold_min_independent_signal_cohorts": int(
            cfg.get("auto_threshold_min_independent_signal_cohorts", 8)
        ),
        "auto_threshold_objective": objective,
        "auto_threshold_min_gap": float(cfg.get("auto_threshold_min_gap", 0.05)),
        "metrics_schema_version": 3,
    }


def build_phase1_gate_contract(gate_config: dict, artifact_contract: dict) -> dict:
    """Build the runtime/deployment contract for KPI evidence."""
    from .execution import execution_contract_metadata  # noqa: PLC0415

    canonical = canonical_phase1_gate_config(
        gate_config,
        effective_horizon_days=int(artifact_contract["effective_horizon_days"]),
    )
    execution = execution_contract_metadata(
        cost_bps=canonical["cost_bps"],
        slippage_bps=canonical["slippage_bps"],
    )
    base = {
        "gate_evidence_schema_version": PHASE1_GATE_EVIDENCE_SCHEMA_VERSION,
        "source": PHASE1_GATE_EVIDENCE_SOURCE,
        "artifact_contract_sha256": artifact_contract.get("contract_sha256"),
        "gate_config": canonical,
        "gate_config_hash": payload_sha256(canonical),
        "execution_contract": execution,
        "execution_contract_version": artifact_contract.get(
            "execution_contract_version"
        ),
    }
    return {**base, "gate_contract_sha256": payload_sha256(base)}


def phase1_ephemeral_model_version(artifact_contract: dict, gate_contract: dict) -> str:
    """Stable registry/evidence identity for an in-memory Phase 1 candidate.

    Daily fallback candidates are not persisted, but their prediction rows must
    still remain segregated by the complete label/feature/calibration contract
    and gate/execution configuration.  The exact booster remains independently
    bound by ``model_bundle_sha256`` in gate evidence.
    """
    schema_version = artifact_contract.get("artifact_schema_version")
    artifact_hash = artifact_contract.get("contract_sha256")
    gate_hash = gate_contract.get("gate_contract_sha256")
    if schema_version != PHASE1_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("ephemeral Phase 1 artifact schema is unsupported")
    for field, value in (
        ("artifact_contract.contract_sha256", artifact_hash),
        ("gate_contract.gate_contract_sha256", gate_hash),
    ):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(f"{field} is missing or invalid")
    return (
        f"{EPHEMERAL_PHASE1_MODEL_VERSION_PREFIX}{schema_version}-"
        f"{artifact_hash[:16]}-{gate_hash[:16]}"
    )


def compare_phase1_gate_contract(actual: dict | None, expected: dict) -> dict:
    """Return structured mismatches for gate/runtime compatibility."""
    if not isinstance(actual, dict):
        return {"compatible": False, "reasons": [{"code": "gate_contract_missing"}]}
    reasons = []
    for field in (
        "gate_evidence_schema_version",
        "source",
        "artifact_contract_sha256",
        "gate_config_hash",
        "execution_contract_version",
        "gate_contract_sha256",
    ):
        if actual.get(field) != expected.get(field):
            reasons.append(
                {
                    "code": "gate_contract_field_mismatch",
                    "field": field,
                    "expected": expected.get(field),
                    "actual": actual.get(field),
                }
            )
    for field in ("gate_config", "execution_contract"):
        if actual.get(field) != expected.get(field):
            reasons.append(
                {
                    "code": "gate_contract_field_mismatch",
                    "field": field,
                    "expected": expected.get(field),
                    "actual": actual.get(field),
                }
            )
    return {"compatible": not reasons, "reasons": reasons}


def phase1_gate_contract_metadata_fields(contract: dict) -> dict:
    """Flatten gate identity fields copied onto pointer/version metadata."""
    return {
        "gate_evidence_schema_version": contract.get("gate_evidence_schema_version"),
        "gate_evidence_source": contract.get("source"),
        "gate_config_hash": contract.get("gate_config_hash"),
        "gate_contract_sha256": contract.get("gate_contract_sha256"),
    }


def verify_phase1_gate_contract_metadata_fields(
    metadata: dict, contract: dict
) -> list[dict]:
    expected = phase1_gate_contract_metadata_fields(contract)
    return [
        {
            "code": "gate_contract_flat_field_mismatch",
            "field": field,
            "expected": value,
            "actual": metadata.get(field),
        }
        for field, value in expected.items()
        if metadata.get(field) != value
    ]


def finalize_phase1_gate_evidence(evidence: dict) -> dict:
    """Attach a stable self-checksum after all evidence fields are populated."""
    base = {k: v for k, v in evidence.items() if k != "evidence_sha256"}
    return {**base, "evidence_sha256": payload_sha256(base)}


def build_phase1_gate_evidence(
    *,
    model_version: str,
    model_bundle_sha256: str,
    artifact_contract: dict,
    gate_contract: dict,
    calibrator_sha256: str,
    applied_calibration_id: str,
    oos_prediction_sha256: str,
    split: dict,
    passed: bool,
    skipped: bool,
    reason: str,
    failures: list,
    thresholds: dict,
    threshold_optimization: dict,
    metrics_tuning: dict,
    metrics_holdout: dict,
) -> dict:
    """Build complete immutable evidence for one deployed ticker candidate."""
    return finalize_phase1_gate_evidence(
        {
            "gate_evidence_schema_version": gate_contract[
                "gate_evidence_schema_version"
            ],
            "source": gate_contract["source"],
            "model_version": model_version,
            "model_bundle_sha256": model_bundle_sha256,
            "artifact_contract_sha256": artifact_contract["contract_sha256"],
            "feature_schema_hash": artifact_contract["feature_schema_hash"],
            "label_config": artifact_contract["label_config"],
            "label_config_hash": payload_sha256(artifact_contract["label_config"]),
            "effective_horizon_days": artifact_contract["effective_horizon_days"],
            "calibration_mode": artifact_contract["calibration_mode"],
            "calibration_id": artifact_contract["calibration_id"],
            "applied_calibration_id": applied_calibration_id,
            "calibrator_sha256": calibrator_sha256,
            "execution_contract_version": artifact_contract[
                "execution_contract_version"
            ],
            "execution_contract": gate_contract["execution_contract"],
            "gate_config": gate_contract["gate_config"],
            "gate_config_hash": gate_contract["gate_config_hash"],
            "gate_contract_sha256": gate_contract["gate_contract_sha256"],
            "oos_prediction_sha256": oos_prediction_sha256,
            "split": split,
            "passed": bool(passed),
            "skipped": bool(skipped),
            "reason": reason,
            "failures": list(failures),
            "thresholds": dict(thresholds),
            "threshold_optimization": dict(threshold_optimization),
            "metrics_tuning": dict(metrics_tuning),
            "metrics_holdout": dict(metrics_holdout),
        }
    )


def verify_phase1_gate_evidence(
    evidence: dict | None,
    *,
    model_version: str,
    artifact_contract: dict,
    gate_contract: dict,
    model_bundle_sha256: str,
    calibrator_sha256: str,
    applied_calibration_id: str,
) -> list[dict]:
    """Verify that saved gate evidence belongs to the exact deployed bundle."""
    if not isinstance(evidence, dict):
        return [{"code": "gate_evidence_missing"}]

    reasons: list[dict] = []
    expected = {
        "gate_evidence_schema_version": PHASE1_GATE_EVIDENCE_SCHEMA_VERSION,
        "source": PHASE1_GATE_EVIDENCE_SOURCE,
        "model_version": model_version,
        "model_bundle_sha256": model_bundle_sha256,
        "artifact_contract_sha256": artifact_contract.get("contract_sha256"),
        "feature_schema_hash": artifact_contract.get("feature_schema_hash"),
        "label_config": artifact_contract.get("label_config"),
        "label_config_hash": payload_sha256(artifact_contract.get("label_config")),
        "effective_horizon_days": artifact_contract.get("effective_horizon_days"),
        "calibration_mode": artifact_contract.get("calibration_mode"),
        "calibration_id": artifact_contract.get("calibration_id"),
        "applied_calibration_id": applied_calibration_id,
        "calibrator_sha256": calibrator_sha256,
        "execution_contract_version": artifact_contract.get(
            "execution_contract_version"
        ),
        "execution_contract": gate_contract.get("execution_contract"),
        "gate_config": gate_contract.get("gate_config"),
        "gate_config_hash": gate_contract.get("gate_config_hash"),
        "gate_contract_sha256": gate_contract.get("gate_contract_sha256"),
    }
    for field, value in expected.items():
        if evidence.get(field) != value:
            reasons.append(
                {
                    "code": "gate_evidence_field_mismatch",
                    "field": field,
                    "expected": value,
                    "actual": evidence.get(field),
                }
            )

    checksum = evidence.get("evidence_sha256")
    actual_checksum = payload_sha256(
        {k: v for k, v in evidence.items() if k != "evidence_sha256"}
    )
    if checksum != actual_checksum:
        reasons.append(
            {
                "code": "gate_evidence_checksum_mismatch",
                "expected": actual_checksum,
                "actual": checksum,
            }
        )

    split = evidence.get("split")
    if not isinstance(split, dict):
        reasons.append({"code": "gate_evidence_split_missing"})
    else:
        structural = {
            "holdout_used": True,
            "execution_window_overlap": False,
            "gate_evaluation_split": "holdout",
            "calibration_fit_split": "tuning",
            "holdout_model_is_persisted_final": True,
            "external_oos_used_for_early_stopping": False,
        }
        for field, expected_value in structural.items():
            if split.get(field) != expected_value:
                reasons.append(
                    {
                        "code": "gate_evidence_split_invalid",
                        "field": field,
                        "expected": expected_value,
                        "actual": split.get(field),
                    }
                )
        for field in ("tuning_rows", "embargo_rows", "holdout_rows"):
            value = split.get(field)
            minimum = 1 if field == "holdout_rows" else 0
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                reasons.append(
                    {
                        "code": "gate_evidence_split_invalid",
                        "field": field,
                        "actual": value,
                    }
                )
        threshold_fit = split.get("threshold_fit_split")
        if threshold_fit not in {"tuning", "fixed_defaults_no_tuning"}:
            reasons.append(
                {
                    "code": "gate_evidence_split_invalid",
                    "field": "threshold_fit_split",
                    "actual": threshold_fit,
                }
            )

    thresholds = evidence.get("thresholds")
    if not isinstance(thresholds, dict) or any(
        field not in thresholds for field in _GATE_THRESHOLD_FIELDS
    ):
        reasons.append({"code": "gate_evidence_thresholds_missing_or_invalid"})
    else:
        try:
            from .predictor import resolve_thresholds  # noqa: PLC0415

            resolve_thresholds(thresholds)
        except (TypeError, ValueError):
            reasons.append({"code": "gate_evidence_thresholds_missing_or_invalid"})
    for field in ("metrics_tuning", "metrics_holdout", "threshold_optimization"):
        if not isinstance(evidence.get(field), dict):
            reasons.append({"code": "gate_evidence_payload_missing", "field": field})
    for field in ("metrics_tuning", "metrics_holdout"):
        metrics = evidence.get(field)
        if isinstance(metrics, dict) and metrics.get("metrics_schema_version") != 3:
            reasons.append(
                {
                    "code": "gate_evidence_metrics_schema_invalid",
                    "field": field,
                    "actual": metrics.get("metrics_schema_version"),
                }
            )
    holdout_metrics = evidence.get("metrics_holdout")
    if isinstance(holdout_metrics, dict):
        for field in (
            "round_trips",
            "independent_signal_cohorts",
            "cagr",
            "avg_daily_net_return",
            "max_drawdown",
            "sharpe",
        ):
            try:
                value = float(holdout_metrics.get(field))
            except (TypeError, ValueError):
                value = None
            if value is None or not math.isfinite(value):
                reasons.append(
                    {
                        "code": "gate_evidence_holdout_metric_invalid",
                        "field": field,
                        "actual": holdout_metrics.get(field),
                    }
                )

    passed = evidence.get("passed")
    skipped = evidence.get("skipped")
    failures = evidence.get("failures")
    reason = evidence.get("reason")
    if not isinstance(passed, bool):
        reasons.append({"code": "gate_evidence_payload_missing", "field": "passed"})
    if not isinstance(skipped, bool):
        reasons.append({"code": "gate_evidence_payload_missing", "field": "skipped"})
    if not isinstance(failures, list):
        reasons.append({"code": "gate_evidence_payload_missing", "field": "failures"})
        failures = []
    gate_enabled = bool((gate_contract.get("gate_config") or {}).get("enabled", True))
    if skipped:
        if gate_enabled or passed is not True or reason != "gate_disabled" or failures:
            reasons.append({"code": "gate_evidence_disabled_state_inconsistent"})
    elif not gate_enabled:
        reasons.append({"code": "gate_evidence_disabled_state_inconsistent"})
    elif passed is True and (failures or reason != "ok"):
        reasons.append({"code": "gate_evidence_pass_state_inconsistent"})
    elif passed is False and (not failures or reason != "kpi_failed"):
        reasons.append({"code": "gate_evidence_fail_state_inconsistent"})

    optimization = evidence.get("threshold_optimization")
    if isinstance(split, dict) and isinstance(optimization, dict):
        for field in (
            "data_split",
            "tuning_rows",
            "embargo_rows",
            "holdout_rows",
            "holdout_used",
            "threshold_tuning_used",
            "execution_window_overlap",
        ):
            if optimization.get(field) != split.get(field):
                reasons.append(
                    {
                        "code": "gate_evidence_threshold_split_mismatch",
                        "field": field,
                        "expected": split.get(field),
                        "actual": optimization.get(field),
                    }
                )
        expected_fit = (
            "tuning"
            if optimization.get("threshold_tuning_used")
            else "fixed_defaults_no_tuning"
        )
        if split.get("threshold_fit_split") != expected_fit:
            reasons.append(
                {
                    "code": "gate_evidence_threshold_split_mismatch",
                    "field": "threshold_fit_split",
                    "expected": expected_fit,
                    "actual": split.get("threshold_fit_split"),
                }
            )
    return reasons


def phase1_gate_result_from_evidence(evidence: dict) -> dict:
    """Project verified immutable evidence into the daily gate-result shape."""
    return {
        "passed": bool(evidence["passed"]),
        "skipped": bool(evidence.get("skipped", False)),
        "reason": evidence.get("reason", "unknown"),
        "horizon_days": evidence.get("effective_horizon_days"),
        "label_mode": (evidence.get("label_config") or {}).get("label_mode"),
        "execution_contract": evidence.get("execution_contract"),
        "metrics": evidence.get("metrics_holdout") or {},
        "metrics_tuning": evidence.get("metrics_tuning") or {},
        "metrics_holdout": evidence.get("metrics_holdout") or {},
        "failures": list(evidence.get("failures") or []),
        "thresholds": evidence.get("thresholds"),
        "threshold_optimization": evidence.get("threshold_optimization") or {},
        "gate_source": PHASE1_GATE_EVIDENCE_SOURCE,
        "gate_evidence_sha256": evidence.get("evidence_sha256"),
        "model_version": evidence.get("model_version"),
    }


def canonical_label_config(label_config: dict | None) -> dict:
    """Return the complete, type-normalized Phase 1 label contract."""
    cfg = label_config or {}
    mode = str(cfg.get("label_mode", "triple_barrier")).strip().lower()
    if mode == "vol_norm":
        warnings.warn(
            "Phase 1 label_mode='vol_norm' is no longer supported; "
            "falling back to 'triple_barrier'",
            RuntimeWarning,
            stacklevel=2,
        )
        mode = "triple_barrier"
    if mode not in {"triple_barrier", "binary_1d"}:
        raise ValueError(
            f"unknown label_mode: {mode!r} (expected 'triple_barrier' or 'binary_1d')"
        )
    return {
        "label_mode": mode,
        "horizon_days": max(1, int(cfg.get("horizon_days", 5))),
        "tb_tp_atr": float(cfg.get("tb_tp_atr", 1.5)),
        "tb_sl_atr": float(cfg.get("tb_sl_atr", 1.0)),
        "tb_max_days": max(1, int(cfg.get("tb_max_days", cfg.get("horizon_days", 5)))),
        "vol_col": str(cfg.get("vol_col", "volatility")),
    }


def phase1_feature_schema_hash(feature_columns: list[str]) -> str:
    """Stable, order-sensitive hash of the exact inference feature schema."""
    return payload_sha256({"feature_columns": [str(c) for c in feature_columns]})


def phase1_calibration_id(mode: str) -> str:
    """Versioned implementation identifier for a configured calibration mode."""
    normalized = str(mode or "none").strip().lower()
    if normalized not in _CALIBRATION_IDS:
        raise ValueError(f"unsupported Phase 1 calibration mode: {mode!r}")
    return _CALIBRATION_IDS[normalized]


def build_phase1_artifact_contract(
    *,
    label_config: dict,
    feature_columns: list[str],
    macro_features_enabled: bool,
    calibration_mode: str,
    execution_contract_version: str | None = None,
) -> dict:
    """Build the canonical training/runtime compatibility contract."""
    from .execution import EXECUTION_CONTRACT_VERSION  # noqa: PLC0415
    from .labels import effective_horizon  # noqa: PLC0415

    normalized_label = canonical_label_config(label_config)
    columns = [str(c) for c in feature_columns]
    mode = str(calibration_mode or "none").strip().lower()
    base = {
        "artifact_schema_version": PHASE1_ARTIFACT_SCHEMA_VERSION,
        "label_config": normalized_label,
        "effective_horizon_days": effective_horizon(normalized_label),
        "feature_columns": columns,
        "feature_schema_hash": phase1_feature_schema_hash(columns),
        "macro_features_enabled": bool(macro_features_enabled),
        "calibration_mode": mode,
        "calibration_id": phase1_calibration_id(mode),
        "execution_contract_version": (
            execution_contract_version or EXECUTION_CONTRACT_VERSION
        ),
    }
    return {**base, "contract_sha256": payload_sha256(base)}


def compare_phase1_artifact_contract(actual: dict | None, expected: dict) -> dict:
    """Return structured incompatibility reasons for two Phase 1 contracts."""
    if not isinstance(actual, dict):
        return {
            "compatible": False,
            "reasons": [{"code": "artifact_contract_missing"}],
        }

    reasons: list[dict] = []
    scalar_fields = (
        "artifact_schema_version",
        "effective_horizon_days",
        "feature_schema_hash",
        "macro_features_enabled",
        "calibration_mode",
        "calibration_id",
        "execution_contract_version",
        "contract_sha256",
    )
    for field in scalar_fields:
        if actual.get(field) != expected.get(field):
            reasons.append(
                {
                    "code": "artifact_contract_field_mismatch",
                    "field": field,
                    "expected": expected.get(field),
                    "actual": actual.get(field),
                }
            )

    actual_label = actual.get("label_config")
    expected_label = expected.get("label_config") or {}
    if not isinstance(actual_label, dict):
        reasons.append({"code": "artifact_label_config_missing"})
    else:
        for field in _LABEL_CONTRACT_FIELDS:
            if actual_label.get(field) != expected_label.get(field):
                reasons.append(
                    {
                        "code": "artifact_contract_field_mismatch",
                        "field": f"label_config.{field}",
                        "expected": expected_label.get(field),
                        "actual": actual_label.get(field),
                    }
                )

    if list(actual.get("feature_columns") or []) != list(
        expected.get("feature_columns") or []
    ):
        reasons.append(
            {
                "code": "artifact_contract_field_mismatch",
                "field": "feature_columns",
                "expected": expected.get("feature_columns"),
                "actual": actual.get("feature_columns"),
            }
        )
    return {"compatible": not reasons, "reasons": reasons}


def phase1_contract_metadata_fields(contract: dict) -> dict:
    """Flatten the contract fields required on pointers and artifact metadata."""
    return {
        "artifact_schema_version": contract.get("artifact_schema_version"),
        "label_config": contract.get("label_config"),
        "effective_horizon_days": contract.get("effective_horizon_days"),
        "feature_columns": contract.get("feature_columns"),
        "feature_schema_hash": contract.get("feature_schema_hash"),
        "macro_features_enabled": contract.get("macro_features_enabled"),
        "calibration_mode": contract.get("calibration_mode"),
        "calibration_id": contract.get("calibration_id"),
        "execution_contract_version": contract.get("execution_contract_version"),
    }


def verify_phase1_contract_metadata_fields(
    metadata: dict, contract: dict
) -> list[dict]:
    """Return fields whose flattened audit copy differs from the contract."""
    expected = phase1_contract_metadata_fields(contract)
    return [
        {
            "code": "artifact_contract_flat_field_mismatch",
            "field": field,
            "expected": value,
            "actual": metadata.get(field),
        }
        for field, value in expected.items()
        if metadata.get(field) != value
    ]


def create_staging_model_dir(version: str, model_dir: str | None = None) -> str:
    """
    Create an isolated Phase 1 staging root under the final model filesystem.

    The returned path is itself a temporary ``model_dir``; callers use the
    normal save APIs with it, which creates ``<returned>/<version>/...``.
    Promotion can therefore rename the complete version directory atomically.
    """
    _validate_version(version)
    final_dir = version_dir(version, model_dir)
    if final_dir.exists():
        raise FileExistsError(f"model version already exists: {version}")

    staging_parent = _model_dir(model_dir) / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=f"{version}-", dir=str(staging_parent)))
    (stage_root / ".phase1-staging").write_text(version, encoding="utf-8")
    return str(stage_root)


def _validated_staging_root(
    staging_model_dir: str, version: str, model_dir: str | None = None
) -> Path:
    """Resolve a staging root only when it was created for this version/base."""
    _validate_version(version)
    root = Path(staging_model_dir).resolve()
    expected_parent = (_model_dir(model_dir) / ".staging").resolve()
    marker = root / ".phase1-staging"
    try:
        marked_version = marker.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"not a managed Phase 1 staging directory: {root}") from exc
    if root.parent != expected_parent or marked_version != version:
        raise ValueError(f"staging directory does not belong to {version}: {root}")
    return root


def discard_staging_model_dir(
    staging_model_dir: str, version: str, model_dir: str | None = None
) -> bool:
    """Delete only a validated, unpromoted Phase 1 staging root."""
    root = _validated_staging_root(staging_model_dir, version, model_dir)
    shutil.rmtree(root)
    return True


def promote_staged_version(
    version: str,
    staging_model_dir: str,
    model_dir: str | None = None,
) -> str:
    """Atomically move a complete staged version into the immutable store."""
    _validated_staging_root(staging_model_dir, version, model_dir)
    source = version_dir(version, staging_model_dir)
    destination = version_dir(version, model_dir)
    if not source.is_dir():
        raise FileNotFoundError(f"staged model version is missing: {source}")
    if destination.exists():
        raise FileExistsError(f"model version already exists: {version}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)
    # The marker/root are deliberately retained until the caller records a
    # successful pointer swap; discard_staging_model_dir then removes them.
    return str(destination)


def _artifact_checksums(version: str, model_dir: str | None = None) -> dict[str, str]:
    root = version_dir(version, model_dir)
    checksums: dict[str, str] = {}
    if not root.is_dir():
        return checksums
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == PHASE1_MANIFEST_FILE or path.name.endswith(".tmp"):
            continue
        checksums[relative] = _sha256_file(path)
    return checksums


def create_phase1_manifest(
    version: str,
    *,
    target_tickers: list[str],
    trained_tickers: list[str],
    failures: list[dict],
    config_payload: dict,
    artifact_contract: dict,
    gate_contract: dict,
    generated_at: str,
    git_commit: str,
    model_dir: str | None = None,
) -> dict:
    """Build a manifest for a fully-written staged Phase 1 candidate."""
    targets = list(dict.fromkeys(str(t) for t in target_tickers))
    trained = list(dict.fromkeys(str(t) for t in trained_tickers))
    failed = [dict(row) for row in failures]
    target_count = len(targets)
    trained_count = len(trained)
    evidence_by_ticker = {}
    for ticker in trained:
        ticker_meta = _read_json(
            ticker_dir(version, ticker, model_dir) / "ticker_metadata.json"
        )
        evidence = (
            ticker_meta.get("gate_evidence") if isinstance(ticker_meta, dict) else None
        )
        evidence_by_ticker[ticker] = {
            "present": isinstance(evidence, dict),
            "source": evidence.get("source") if isinstance(evidence, dict) else None,
            "model_version": (
                evidence.get("model_version") if isinstance(evidence, dict) else None
            ),
            "model_bundle_sha256": (
                evidence.get("model_bundle_sha256")
                if isinstance(evidence, dict)
                else None
            ),
            "feature_schema_hash": (
                evidence.get("feature_schema_hash")
                if isinstance(evidence, dict)
                else None
            ),
            "label_config_hash": (
                evidence.get("label_config_hash")
                if isinstance(evidence, dict)
                else None
            ),
            "calibration_id": (
                evidence.get("calibration_id") if isinstance(evidence, dict) else None
            ),
            "calibrator_sha256": (
                evidence.get("calibrator_sha256")
                if isinstance(evidence, dict)
                else None
            ),
            "gate_config_hash": (
                evidence.get("gate_config_hash") if isinstance(evidence, dict) else None
            ),
            "execution_contract_version": (
                evidence.get("execution_contract_version")
                if isinstance(evidence, dict)
                else None
            ),
            "evidence_sha256": (
                evidence.get("evidence_sha256") if isinstance(evidence, dict) else None
            ),
        }

    return {
        "artifact_schema_version": PHASE1_MANIFEST_SCHEMA_VERSION,
        "version": version,
        "kind": "per_ticker_horizon_v1",
        "generated_at": generated_at,
        "git_commit": git_commit,
        "immutable": True,
        "config_sha256": payload_sha256(config_payload),
        "artifact_contract": dict(artifact_contract),
        "gate_contract": dict(gate_contract),
        "target_tickers": targets,
        "trained_tickers": trained,
        "failed_tickers": failed,
        "coverage": {
            "target_count": target_count,
            "trained_count": trained_count,
            "failed_count": max(0, target_count - trained_count),
            "ratio": (trained_count / target_count) if target_count else 0.0,
        },
        "gate_evidence": {
            "by_ticker": evidence_by_ticker,
            "provenance_match_required": True,
            "source": PHASE1_GATE_EVIDENCE_SOURCE,
            "gate_contract_sha256": gate_contract.get("gate_contract_sha256"),
        },
        "files": _artifact_checksums(version, model_dir),
    }


def save_phase1_manifest(
    version: str, manifest: dict, model_dir: str | None = None
) -> str:
    """Finalize a staged version; subsequent bundle/metadata writes are refused."""
    path = version_dir(version, model_dir) / PHASE1_MANIFEST_FILE
    if path.exists():
        raise FileExistsError(f"manifest already exists for model version {version}")
    _write_json(path, manifest)
    return str(path)


def record_phase1_candidate_validation(
    version: str,
    decision: dict,
    *,
    staging_model_dir: str,
    model_dir: str | None = None,
) -> str:
    """Record the one-time candidate decision while the version is staged."""
    _validated_staging_root(staging_model_dir, version, model_dir)
    path = version_dir(version, staging_model_dir) / PHASE1_MANIFEST_FILE
    manifest = _read_json(path)
    if not isinstance(manifest, dict):
        raise ValueError(f"manifest is missing or invalid for {version}")
    if "candidate_validation" in manifest:
        raise FileExistsError(f"candidate validation already recorded for {version}")
    updated = dict(manifest)
    updated["candidate_validation"] = decision
    _write_json(path, updated)
    return str(path)


def read_phase1_manifest(version: str, model_dir: str | None = None) -> dict | None:
    data = _read_json(version_dir(version, model_dir) / PHASE1_MANIFEST_FILE)
    return data if isinstance(data, dict) else None


def verify_phase1_manifest(version: str, model_dir: str | None = None) -> dict:
    """Verify manifest identity and the exact artifact file/checksum set."""
    manifest = read_phase1_manifest(version, model_dir)
    failures: list[dict] = []
    if manifest is None:
        return {
            "passed": False,
            "failures": [{"code": "manifest_missing_or_invalid"}],
        }
    if manifest.get("version") != version:
        failures.append({"code": "manifest_version_mismatch"})
    if manifest.get("artifact_schema_version") != PHASE1_MANIFEST_SCHEMA_VERSION:
        failures.append({"code": "manifest_schema_unsupported"})
    if manifest.get("immutable") is not True:
        failures.append({"code": "manifest_not_immutable"})
    config_sha256 = manifest.get("config_sha256")
    if not isinstance(config_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", config_sha256
    ):
        failures.append({"code": "config_checksum_missing_or_invalid"})
    artifact_contract = manifest.get("artifact_contract")
    if not isinstance(artifact_contract, dict):
        failures.append({"code": "artifact_contract_missing"})
    gate_contract = manifest.get("gate_contract")
    if not isinstance(gate_contract, dict):
        failures.append({"code": "gate_contract_missing"})

    targets = list(manifest.get("target_tickers") or [])
    trained = list(manifest.get("trained_tickers") or [])
    failed_rows = manifest.get("failed_tickers") or []
    failed_tickers = {row.get("ticker") for row in failed_rows if isinstance(row, dict)}
    expected_failed = set(targets) - set(trained)
    if failed_tickers != expected_failed:
        failures.append({"code": "failed_tickers_manifest_mismatch"})
    coverage = manifest.get("coverage") or {}
    expected_coverage = {
        "target_count": len(targets),
        "trained_count": len(trained),
        "failed_count": len(expected_failed),
        "ratio": (len(trained) / len(targets)) if targets else 0.0,
    }
    if coverage != expected_coverage:
        failures.append({"code": "coverage_manifest_mismatch"})

    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        failures.append({"code": "manifest_files_missing"})
        expected = {}
    actual = _artifact_checksums(version, model_dir)
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    mismatched = sorted(
        rel for rel in set(expected) & set(actual) if expected[rel] != actual[rel]
    )
    if missing:
        failures.append({"code": "artifact_files_missing", "files": missing})
    if unexpected:
        failures.append({"code": "artifact_files_unexpected", "files": unexpected})
    if mismatched:
        failures.append({"code": "artifact_checksum_mismatch", "files": mismatched})

    metadata = read_version_metadata(version, model_dir)
    if not isinstance(metadata, dict):
        failures.append({"code": "version_metadata_missing_or_invalid"})
    else:
        if metadata.get("version") != version:
            failures.append({"code": "metadata_version_mismatch"})
        if metadata.get("artifact_schema_version") != PHASE1_MANIFEST_SCHEMA_VERSION:
            failures.append({"code": "metadata_schema_mismatch"})
        if metadata.get("config_sha256") != config_sha256:
            failures.append({"code": "metadata_config_checksum_mismatch"})
        contract_check = compare_phase1_artifact_contract(
            metadata.get("artifact_contract"), artifact_contract or {}
        )
        if not contract_check["compatible"]:
            failures.append(
                {
                    "code": "metadata_artifact_contract_mismatch",
                    "reasons": contract_check["reasons"],
                }
            )
        flat_failures = verify_phase1_contract_metadata_fields(
            metadata, artifact_contract or {}
        )
        if flat_failures:
            failures.append(
                {
                    "code": "metadata_artifact_contract_flat_fields_mismatch",
                    "reasons": flat_failures,
                }
            )
        gate_contract_check = compare_phase1_gate_contract(
            metadata.get("gate_contract"), gate_contract or {}
        )
        if not gate_contract_check["compatible"]:
            failures.append(
                {
                    "code": "metadata_gate_contract_mismatch",
                    "reasons": gate_contract_check["reasons"],
                }
            )
        gate_flat_failures = verify_phase1_gate_contract_metadata_fields(
            metadata, gate_contract or {}
        )
        if gate_flat_failures:
            failures.append(
                {
                    "code": "metadata_gate_contract_flat_fields_mismatch",
                    "reasons": gate_flat_failures,
                }
            )
        if list(metadata.get("universe") or []) != targets:
            failures.append({"code": "metadata_universe_mismatch"})
        if list(metadata.get("trained_tickers") or []) != trained:
            failures.append({"code": "metadata_trained_tickers_mismatch"})

    return {
        "passed": not failures,
        "failures": failures,
        "checked_files": len(actual),
    }


def evaluate_phase1_candidate(
    version: str,
    *,
    previous_metadata: dict | None = None,
    model_dir: str | None = None,
    required_gate_provenance: dict | None = None,
) -> dict:
    """
    Validate a candidate before deployment.

    Phase 1 deployment is deliberately fail-closed: every target ticker must
    have a loadable bundle whose gate evidence matches the exact persisted
    booster, calibrator, artifact contract, gate config and execution contract.
    ``required_gate_provenance`` can add caller-specific fixed fields, but the
    intrinsic evidence checks are always mandatory.
    """
    integrity = verify_phase1_manifest(version, model_dir)
    failures = list(integrity.get("failures") or [])
    manifest = read_phase1_manifest(version, model_dir) or {}
    targets = list(manifest.get("target_tickers") or [])
    trained = list(manifest.get("trained_tickers") or [])
    target_set = set(targets)
    trained_set = set(trained)

    if not targets:
        failures.append({"code": "target_universe_empty"})
    missing_targets = sorted(target_set - trained_set)
    unexpected_tickers = sorted(trained_set - target_set)
    if missing_targets:
        failures.append(
            {"code": "target_coverage_incomplete", "tickers": missing_targets}
        )
    if unexpected_tickers:
        failures.append(
            {"code": "trained_tickers_outside_target", "tickers": unexpected_tickers}
        )

    previous_trained = set((previous_metadata or {}).get("trained_tickers") or [])
    previous_in_scope = previous_trained & target_set
    coverage_drop = len(previous_in_scope - trained_set)
    if coverage_drop:
        failures.append(
            {
                "code": "active_coverage_regression",
                "tickers": sorted(previous_in_scope - trained_set),
            }
        )

    missing_gate_evidence = []
    provenance_mismatches: dict[str, list[dict]] = {}
    contract_mismatches: dict[str, list[dict]] = {}
    unloadable = []
    expected_contract = manifest.get("artifact_contract") or {}
    expected_gate_contract = manifest.get("gate_contract") or {}
    for ticker in trained:
        bundle = load_model_bundle(version, ticker, model_dir)
        if bundle is None:
            unloadable.append(ticker)
            continue
        evidence = (bundle.get("ticker_metadata") or {}).get("gate_evidence")
        ticker_contract = (bundle.get("ticker_metadata") or {}).get("artifact_contract")
        contract_check = compare_phase1_artifact_contract(
            ticker_contract, expected_contract
        )
        if not contract_check["compatible"]:
            contract_mismatches[ticker] = contract_check["reasons"]
        flat_failures = verify_phase1_contract_metadata_fields(
            bundle.get("ticker_metadata") or {}, expected_contract
        )
        if flat_failures:
            contract_mismatches.setdefault(ticker, []).extend(flat_failures)
        if not isinstance(evidence, dict):
            missing_gate_evidence.append(ticker)
            continue
        ticker_metadata = bundle.get("ticker_metadata") or {}
        gate_contract_check = compare_phase1_gate_contract(
            ticker_metadata.get("gate_contract"), expected_gate_contract
        )
        gate_failures = list(gate_contract_check["reasons"])
        gate_failures.extend(
            verify_phase1_gate_contract_metadata_fields(
                ticker_metadata, expected_gate_contract
            )
        )
        try:
            exact_bundle_hash = booster_bundle_sha256(
                {"folds": bundle.get("folds") or [], "final": bundle.get("final")}
            )
        except Exception as exc:  # noqa: BLE001
            gate_failures.append(
                {
                    "code": "model_bundle_hash_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            exact_bundle_hash = ""
        gate_failures.extend(
            verify_phase1_gate_evidence(
                evidence,
                model_version=version,
                artifact_contract=expected_contract,
                gate_contract=expected_gate_contract,
                model_bundle_sha256=exact_bundle_hash,
                calibrator_sha256=payload_sha256(bundle.get("calibration")),
                applied_calibration_id=ticker_metadata.get("applied_calibration_id"),
            )
        )
        if gate_failures:
            provenance_mismatches[ticker] = gate_failures
        if required_gate_provenance:
            fields = [
                field
                for field, expected in required_gate_provenance.items()
                if evidence.get(field) != expected
            ]
            if fields:
                provenance_mismatches.setdefault(ticker, []).extend(
                    {
                        "code": "required_gate_provenance_mismatch",
                        "field": field,
                    }
                    for field in fields
                )
    if unloadable:
        failures.append({"code": "model_bundle_unloadable", "tickers": unloadable})
    if missing_gate_evidence:
        failures.append(
            {"code": "gate_evidence_missing", "tickers": missing_gate_evidence}
        )
    if contract_mismatches:
        failures.append(
            {
                "code": "ticker_artifact_contract_mismatch",
                "by_ticker": contract_mismatches,
            }
        )
    if provenance_mismatches:
        failures.append(
            {
                "code": "gate_provenance_mismatch",
                "by_ticker": provenance_mismatches,
            }
        )

    return {
        "passed": not failures,
        "failures": failures,
        "integrity": integrity,
        "coverage": manifest.get("coverage") or {},
        "previous_in_scope_count": len(previous_in_scope),
        "required_gate_provenance": required_gate_provenance,
    }


def phase1_manifest_sha256(version: str, model_dir: str | None = None) -> str:
    path = version_dir(version, model_dir) / PHASE1_MANIFEST_FILE
    if not path.is_file():
        raise FileNotFoundError(f"manifest is missing for {version}")
    return _sha256_file(path)


def validate_active_phase1_contract(
    active: dict | None,
    *,
    artifact_contract: dict,
    gate_contract: dict,
    model_dir: str | None = None,
) -> dict:
    """Shared fail-closed validation for daily, drift and dashboard readers."""
    if not isinstance(active, dict):
        return {
            "compatible": False,
            "reasons": [{"code": "active_model_missing"}],
            "artifact_contract": artifact_contract,
            "gate_contract": gate_contract,
        }

    reasons = list(
        compare_phase1_artifact_contract(
            active.get("artifact_contract"), artifact_contract
        )["reasons"]
    )
    reasons.extend(verify_phase1_contract_metadata_fields(active, artifact_contract))
    reasons.extend(
        compare_phase1_gate_contract(active.get("gate_contract"), gate_contract)[
            "reasons"
        ]
    )
    reasons.extend(verify_phase1_gate_contract_metadata_fields(active, gate_contract))

    version = active.get("version")
    try:
        metadata = read_version_metadata(version, model_dir)
        if not isinstance(metadata, dict):
            reasons.append({"code": "active_version_metadata_missing"})
        else:
            reasons.extend(
                {**reason, "source": "version_metadata"}
                for reason in compare_phase1_artifact_contract(
                    metadata.get("artifact_contract"), artifact_contract
                )["reasons"]
            )
            reasons.extend(
                {**reason, "source": "version_metadata"}
                for reason in verify_phase1_contract_metadata_fields(
                    metadata, artifact_contract
                )
            )
            reasons.extend(
                {**reason, "source": "version_metadata"}
                for reason in compare_phase1_gate_contract(
                    metadata.get("gate_contract"), gate_contract
                )["reasons"]
            )
            reasons.extend(
                {**reason, "source": "version_metadata"}
                for reason in verify_phase1_gate_contract_metadata_fields(
                    metadata, gate_contract
                )
            )

        manifest = read_phase1_manifest(version, model_dir)
        if not isinstance(manifest, dict):
            reasons.append({"code": "active_manifest_missing"})
        else:
            reasons.extend(
                {**reason, "source": "manifest"}
                for reason in compare_phase1_artifact_contract(
                    manifest.get("artifact_contract"), artifact_contract
                )["reasons"]
            )
            reasons.extend(
                {**reason, "source": "manifest"}
                for reason in compare_phase1_gate_contract(
                    manifest.get("gate_contract"), gate_contract
                )["reasons"]
            )
            expected_manifest_hash = active.get("manifest_sha256")
            actual_manifest_hash = phase1_manifest_sha256(version, model_dir)
            if expected_manifest_hash != actual_manifest_hash:
                reasons.append(
                    {
                        "code": "active_manifest_checksum_mismatch",
                        "expected": expected_manifest_hash,
                        "actual": actual_manifest_hash,
                    }
                )
            integrity = verify_phase1_manifest(version, model_dir)
            if not integrity.get("passed"):
                reasons.append(
                    {
                        "code": "active_manifest_integrity_failed",
                        "failures": integrity.get("failures") or [],
                    }
                )
            candidate_validation = manifest.get("candidate_validation")
            if not isinstance(
                candidate_validation, dict
            ) or not candidate_validation.get("passed"):
                reasons.append({"code": "active_candidate_validation_not_passed"})
    except Exception as exc:  # noqa: BLE001 -- every artifact reader fails closed
        reasons.append(
            {
                "code": "active_artifact_validation_failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )

    return {
        "compatible": not reasons,
        "reasons": reasons,
        "artifact_contract": artifact_contract,
        "gate_contract": gate_contract,
    }


def validate_runtime_active_phase1(
    active: dict | None,
    *,
    model_config: dict,
    label_config: dict,
    gate_config: dict,
    model_dir: str | None = None,
) -> dict:
    """Build current runtime contracts and validate an active pointer once."""
    from .model import phase1_feature_cols  # noqa: PLC0415

    macro_enabled = bool(model_config.get("macro_features_enabled", True))
    artifact_contract = build_phase1_artifact_contract(
        label_config=label_config,
        feature_columns=phase1_feature_cols(macro_enabled),
        macro_features_enabled=macro_enabled,
        calibration_mode=model_config.get("calibration_mode", "isotonic"),
    )
    gate_contract = build_phase1_gate_contract(gate_config, artifact_contract)
    return validate_active_phase1_contract(
        active,
        artifact_contract=artifact_contract,
        gate_contract=gate_contract,
        model_dir=model_dir,
    )


def activate_staged_phase1_version(
    version: str,
    staging_model_dir: str,
    *,
    pointer_metadata: dict,
    model_dir: str | None = None,
    active_file: str | None = None,
) -> str:
    """
    Promote a validated candidate, then atomically swap the active pointer.

    If pointer writing fails, its previous contents remain intact because
    ``_write_json`` uses ``os.replace``; the just-promoted inactive candidate
    is then removed, restoring the pre-activation filesystem state.
    """
    manifest = read_phase1_manifest(version, staging_model_dir) or {}
    decision = manifest.get("candidate_validation") or {}
    if decision.get("passed") is not True:
        raise ValueError(f"candidate {version} has not passed validation")
    integrity = verify_phase1_manifest(version, staging_model_dir)
    if not integrity.get("passed"):
        raise ValueError(f"candidate {version} failed integrity verification")

    promoted = promote_staged_version(version, staging_model_dir, model_dir)
    metadata = dict(pointer_metadata)
    metadata["manifest_sha256"] = phase1_manifest_sha256(version, model_dir)
    try:
        write_active_model(
            version,
            metadata,
            model_dir=model_dir,
            active_file=active_file,
        )
    except Exception:
        # Pointer replacement is atomic. If it did not select this version,
        # remove only the exact candidate promoted by this call so a failed
        # deployment does not get committed as an inactive orphan.
        active = read_active_model(active_file=active_file, model_dir=model_dir)
        if not active or active.get("version") != version:
            promoted_path = Path(promoted)
            expected = version_dir(version, model_dir)
            if promoted_path.resolve() == expected.resolve():
                shutil.rmtree(promoted_path)
        raise
    return promoted


def save_model_bundle(
    version: str,
    ticker: str,
    boosters: dict,
    metadata: dict,
    model_dir: str | None = None,
) -> str:
    """
    Persist one ticker's ensemble + metadata.

    boosters: {"folds": [lgb.Booster, ...], "final": lgb.Booster}
    metadata: {"calibration": {...}|None, "feature_reference": {...}, ...}
    """
    _ensure_version_mutable(version, model_dir)
    d = ticker_dir(version, ticker, model_dir)
    d.mkdir(parents=True, exist_ok=True)

    folds = boosters.get("folds") or []
    for i, booster in enumerate(folds):
        booster.save_model(str(d / f"fold_{i}.txt"))
    final = boosters.get("final")
    if final is not None:
        final.save_model(str(d / "final.txt"))

    _write_json(d / "calibration.json", metadata.get("calibration"))
    _write_json(d / "feature_reference.json", metadata.get("feature_reference") or {})
    extra = {
        k: v
        for k, v in metadata.items()
        if k not in ("calibration", "feature_reference")
    }
    if extra:
        _write_json(d / "ticker_metadata.json", extra)
    return str(d)


def load_model_bundle(
    version: str, ticker: str, model_dir: str | None = None
) -> dict | None:
    """Load a ticker's ensemble + calibration + feature reference, or None."""
    import lightgbm as lgb

    d = ticker_dir(version, ticker, model_dir)
    final_path = d / "final.txt"
    if not d.exists() or not final_path.exists():
        return None

    try:
        folds = []
        i = 0
        while (d / f"fold_{i}.txt").exists():
            folds.append(lgb.Booster(model_file=str(d / f"fold_{i}.txt")))
            i += 1
        final = lgb.Booster(model_file=str(final_path))
    except Exception:  # noqa: BLE001 — corrupt artifact -> treat as missing
        return None

    return {
        "version": version,
        "folds": folds,
        "final": final,
        "calibration": _read_json(d / "calibration.json"),
        "feature_reference": _read_json(d / "feature_reference.json") or {},
        "ticker_metadata": _read_json(d / "ticker_metadata.json") or {},
        "version_metadata": read_version_metadata(version, model_dir) or {},
    }


def save_version_metadata(
    version: str, metadata: dict, model_dir: str | None = None
) -> str:
    """Write the version-level metadata.json (artifact_uri target)."""
    _ensure_version_mutable(version, model_dir)
    path = version_dir(version, model_dir) / "metadata.json"
    _write_json(path, metadata)
    return str(path)


def read_version_metadata(version: str, model_dir: str | None = None) -> dict | None:
    return _read_json(version_dir(version, model_dir) / "metadata.json")


def write_active_model(
    version: str,
    metadata: dict | None = None,
    model_dir: str | None = None,
    active_file: str | None = None,
) -> str:
    """Point the active model at `version`. metadata is merged into the pointer."""
    _validate_version(version)
    payload = dict(metadata or {})
    payload["version"] = version
    path = _active_file(active_file, model_dir)
    _write_json(path, payload)
    return str(path)


def read_active_model(
    active_file: str | None = None, model_dir: str | None = None
) -> dict | None:
    """Return the active-model pointer, or None when missing / corrupt / invalid."""
    path = _active_file(active_file, model_dir)
    if not path.exists():
        return None
    data = _read_json(path)
    if not isinstance(data, dict) or not data.get("version"):
        return None
    try:
        _validate_version(data["version"])
    except ValueError:
        return None
    return data


def clear_active_model(
    active_file: str | None = None, model_dir: str | None = None
) -> None:
    """Remove the active pointer (used to force an auto-mode ephemeral fallback)."""
    path = _active_file(active_file, model_dir)
    if path.exists():
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Phase 2 — cross-sectional (single-model) bundle
# ---------------------------------------------------------------------------


def _active_cs_file(
    active_file: str | None = None, model_dir: str | None = None
) -> Path:
    raw = active_file or os.environ.get("TRADER_CS_MODEL_ACTIVE_FILE")
    if raw:
        return Path(raw)
    return _model_dir(model_dir) / "active_cs_model.json"


def save_cs_bundle(
    version: str,
    booster,
    *,
    feature_schema: dict,
    calibration: dict | None = None,
    feature_reference: dict | None = None,
    sector_encoder: dict | None = None,
    universe: list | None = None,
    oos_predictions=None,
    version_metadata: dict | None = None,
    model_dir: str | None = None,
) -> str:
    """
    Persist the single cross-sectional model bundle under version_dir.

    booster: a trained lightgbm.Booster (saved to model.txt).
    feature_schema: dict, REQUIRED (written to feature_schema.json).
    calibration / feature_reference: dicts (JSON), optional -> None when absent.
    sector_encoder: dict, optional -> {} when absent.
    universe: list[str], optional -> [] when absent.
    oos_predictions: pandas.DataFrame -> oos_predictions.parquet (skipped when None/empty).
    version_metadata: dict -> metadata.json via save_version_metadata (skipped when None).
    Returns the version_dir path as str.
    """
    vdir = version_dir(version, model_dir)
    vdir.mkdir(parents=True, exist_ok=True)

    booster.save_model(str(vdir / "model.txt"))

    _write_json(vdir / "feature_schema.json", feature_schema)
    _write_json(vdir / "calibration.json", calibration)
    _write_json(vdir / "feature_reference.json", feature_reference)
    _write_json(
        vdir / "sector_encoder.json",
        sector_encoder if sector_encoder is not None else {},
    )
    _write_json(vdir / "universe.json", universe if universe is not None else [])

    if oos_predictions is not None:
        import pandas as pd  # noqa: PLC0415 — lazy import

        if isinstance(oos_predictions, pd.DataFrame) and not oos_predictions.empty:
            oos_predictions.to_parquet(str(vdir / "oos_predictions.parquet"))

    if version_metadata is not None:
        save_version_metadata(version, version_metadata, model_dir)

    return str(vdir)


def load_cs_bundle(version: str, model_dir: str | None = None) -> dict | None:
    """
    Load the CS bundle, or None when model.txt is missing/corrupt.

    Returns:
      {"version": version, "booster": lgb.Booster,
       "feature_schema": {...}, "calibration": {...}|None,
       "feature_reference": {...}|None, "sector_encoder": {...},
       "universe": [...], "oos_predictions": pd.DataFrame|None,
       "metadata": {...}|None}
    """
    import lightgbm as lgb  # noqa: PLC0415 — lazy import

    vdir = version_dir(version, model_dir)
    model_path = vdir / "model.txt"
    if not model_path.exists():
        return None

    try:
        booster = lgb.Booster(model_file=str(model_path))
    except Exception:  # noqa: BLE001 — corrupt artifact -> treat as missing
        return None

    feature_schema = _read_json(vdir / "feature_schema.json") or {}
    calibration = _read_json(vdir / "calibration.json")
    feature_reference = _read_json(vdir / "feature_reference.json")
    sector_encoder = _read_json(vdir / "sector_encoder.json") or {}
    universe_raw = _read_json(vdir / "universe.json")
    universe = universe_raw if isinstance(universe_raw, list) else []

    oos_predictions = None
    parquet_path = vdir / "oos_predictions.parquet"
    if parquet_path.exists():
        try:
            import pandas as pd  # noqa: PLC0415 — lazy import

            oos_predictions = pd.read_parquet(str(parquet_path))
        except Exception:  # noqa: BLE001 — corrupt/missing -> None
            oos_predictions = None

    metadata = read_version_metadata(version, model_dir)

    feature_cols = feature_schema.get("feature_cols")
    if not isinstance(feature_cols, list):
        feature_cols = []

    return {
        "version": version,
        "booster": booster,
        "feature_cols": feature_cols,
        "feature_schema": feature_schema,
        "calibration": calibration,
        "feature_reference": feature_reference,
        "sector_encoder": sector_encoder,
        "universe": universe,
        "oos_predictions": oos_predictions,
        "metadata": metadata,
    }


def write_active_cs_model(
    version: str,
    metadata: dict | None = None,
    model_dir: str | None = None,
    active_file: str | None = None,
) -> str:
    """Point the CS active model at `version`; metadata merged into the pointer."""
    _validate_version(version)
    payload = dict(metadata or {})
    payload["version"] = version
    path = _active_cs_file(active_file, model_dir)
    _write_json(path, payload)
    return str(path)


def read_active_cs_model(
    active_file: str | None = None, model_dir: str | None = None
) -> dict | None:
    """Return the CS active-model pointer, or None when missing/corrupt/invalid (no 'version')."""
    path = _active_cs_file(active_file, model_dir)
    if not path.exists():
        return None
    data = _read_json(path)
    if not isinstance(data, dict) or not data.get("version"):
        return None
    try:
        _validate_version(data["version"])
    except ValueError:
        return None
    return data


def clear_active_cs_model(
    active_file: str | None = None, model_dir: str | None = None
) -> None:
    """Remove the CS active pointer (rollback parity with clear_active_model)."""
    path = _active_cs_file(active_file, model_dir)
    if path.exists():
        path.unlink(missing_ok=True)
