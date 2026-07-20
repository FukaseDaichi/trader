"""
Phase 1 per-ticker training & inference bridge.

Ties together labels + features + horizon models + calibration + model_store so
the weekly retrain (training), the daily run (inference), and drift_check (PSI)
share one implementation instead of duplicating it.

Pure-ish: no DB or network. LightGBM/pandas/numpy only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import numpy as np
import pandas as pd

from . import backtest as backtest_module
from .calibration import (
    apply_isotonic,
    auc_score,
    brier_score,
    fit_calibrator,
    hit_rate,
    ic_score,
    reliability_bins,
)
from .labels import build_labelled_frame, effective_horizon
from .model import (
    phase1_feature_cols,
    phase1_training_min_rows,
    predict_prob_with_bundle,
    resolve_purge_gap,
    train_horizon_models,
)
from .model_store import (
    PHASE1_ARTIFACT_SCHEMA_VERSION,
    booster_bundle_sha256,
    build_phase1_artifact_contract,
    build_phase1_gate_evidence,
    build_phase1_gate_contract,
    payload_sha256,
    phase1_calibration_id,
    phase1_feature_schema_hash,
    phase1_gate_contract_metadata_fields,
    phase1_gate_result_from_evidence,
    verify_phase1_gate_evidence,
)


# --- PSI / feature reference ------------------------------------------------

# Calendar / cyclical / near-constant features are excluded from PSI: a short
# recent window naturally covers only part of the cycle (e.g. ~6 of 12 months),
# which produces a huge but meaningless PSI. The model saw every season in
# training, so these are not "drift". (Binary/constant features are also skipped
# automatically by the <3-unique-values guard in _psi_reference_for.)
PSI_EXCLUDE_FEATURES = frozenset(
    {
        # calendar / cyclical
        "day_of_week",
        "month",
        "is_month_end",
        "is_month_start",
        # absolute price-scale (in yen, grows with the price level -> non-stationary)
        "macd",
        "macd_signal",
        "macd_hist",
        "macd_hist_change",
        # qualitative constant
        "macro_bias_score",
    }
)


def _psi_reference_for(values, n_bins: int = 10) -> dict:
    v = np.asarray(values, dtype="float64")
    v = v[np.isfinite(v)]
    if v.size < n_bins * 5 or np.unique(v).size < 3:
        return {"edges": None, "ref_props": None}
    edges = np.unique(np.nanquantile(v, np.linspace(0.0, 1.0, n_bins + 1)))
    if edges.size < 3:
        return {"edges": None, "ref_props": None}
    clipped = np.clip(v, edges[0], edges[-1])
    counts, _ = np.histogram(clipped, bins=edges)
    total = counts.sum()
    if total == 0:
        return {"edges": None, "ref_props": None}
    return {"edges": edges.tolist(), "ref_props": (counts / total).tolist()}


def psi_for(reference: dict, current_values, eps: float = 1e-4):
    """Population Stability Index of current values vs a stored reference bin."""
    if not reference:
        return None
    edges = reference.get("edges")
    ref_props = reference.get("ref_props")
    if not edges or not ref_props:
        return None
    edges = np.asarray(edges, dtype="float64")
    v = np.asarray(current_values, dtype="float64")
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None
    clipped = np.clip(v, edges[0], edges[-1])
    counts, _ = np.histogram(clipped, bins=edges)
    total = counts.sum()
    if total == 0:
        return None
    cur = np.clip(counts / total, eps, None)
    ref = np.clip(np.asarray(ref_props, dtype="float64"), eps, None)
    return float(np.sum((cur - ref) * np.log(cur / ref)))


def build_feature_reference(
    labelled: pd.DataFrame, feature_cols, n_bins: int = 10, ref_rows: int = 250
) -> dict:
    """
    Reference distribution (for PSI) + expected-return stats from training data.

    The PSI reference uses the most recent `ref_rows` rows (~1y) so it MATCHES
    the length of the drift-check window. Comparing a short recent window to the
    full multi-year training range would otherwise flag harmless window-length
    mismatch for non-stationary features (recent values concentrate in a few
    bins of the long-run distribution -> huge PSI). With matched windows, PSI is
    ~0 right after retraining and only grows as the trailing window truly drifts.
    Expected-return stats use the full labelled frame for stability.
    """
    psi_frame = (
        labelled.tail(ref_rows) if ref_rows and len(labelled) > ref_rows else labelled
    )
    psi_ref = {}
    for feat in feature_cols:
        if feat in PSI_EXCLUDE_FEATURES or feat not in psi_frame.columns:
            psi_ref[feat] = {"edges": None, "ref_props": None}
        else:
            psi_ref[feat] = _psi_reference_for(psi_frame[feat].to_numpy(), n_bins)

    up = labelled.loc[labelled["target_class"] == 1, "fwd_return"]
    dn = labelled.loc[labelled["target_class"] == 0, "fwd_return"]
    return {
        "feature_cols": list(feature_cols),
        "avg_up_ret": float(up.mean()) if len(up) else None,
        "avg_dn_ret": float(dn.mean()) if len(dn) else None,
        "n_bins": n_bins,
        "psi_ref_rows": int(min(len(labelled), ref_rows))
        if ref_rows
        else int(len(labelled)),
        "psi": psi_ref,
    }


def feature_psi(feature_reference: dict, current_frame: pd.DataFrame):
    """(max PSI, per-feature PSI dict) for the current feature frame."""
    psis = {}
    for feat, ref in (feature_reference or {}).get("psi", {}).items():
        if feat in PSI_EXCLUDE_FEATURES:
            continue
        if feat in current_frame.columns:
            psis[feat] = psi_for(ref, current_frame[feat].to_numpy())
    finite = [p for p in psis.values() if p is not None]
    return (max(finite) if finite else None), psis


def expected_return(prob_up, feature_reference: dict):
    """Expected H-day return ≈ p·avg_up + (1-p)·avg_dn from training stats."""
    if prob_up is None or not feature_reference:
        return None
    up = feature_reference.get("avg_up_ret")
    dn = feature_reference.get("avg_dn_ret")
    if up is None or dn is None:
        return None
    return float(prob_up * up + (1.0 - prob_up) * dn)


def features_hash(feature_row, feature_cols) -> str:
    """Stable short hash of the (rounded) inference feature vector."""
    vals = []
    for col in feature_cols:
        try:
            f = float(feature_row.get(col))
            vals.append(None if f != f else round(f, 6))  # NaN -> None
        except (TypeError, ValueError):
            vals.append(None)
    payload = json.dumps(vals, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _iso_oos_date(frame: pd.DataFrame, position: int) -> str | None:
    if frame.empty or "date" not in frame.columns:
        return None
    value = pd.Timestamp(frame["date"].iloc[position])
    if pd.isna(value):
        return None
    return value.strftime("%Y-%m-%d")


def _finite_or_none(value):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None


def _oos_prediction_hash(tuning: pd.DataFrame, holdout: pd.DataFrame) -> str:
    records = []
    for split_name, frame in (("tuning", tuning), ("holdout", holdout)):
        for row in frame.itertuples(index=False):
            records.append(
                {
                    "split": split_name,
                    "date": pd.Timestamp(getattr(row, "date")).strftime("%Y-%m-%d"),
                    "raw_score": _finite_or_none(getattr(row, "raw_score", None)),
                    "prob_up": _finite_or_none(getattr(row, "prob_up", None)),
                    "target_class": int(getattr(row, "target_class")),
                    "fwd_return": _finite_or_none(getattr(row, "fwd_return", None)),
                    "oos_role": getattr(row, "oos_role", None),
                }
            )
    return payload_sha256(records)


# --- training ---------------------------------------------------------------


def train_ticker_bundle(
    featured: pd.DataFrame,
    gate_config: dict,
    label_config: dict,
    model_cfg: dict,
    *,
    model_version: str,
):
    """
    Train one ticker's exact Phase 1 candidate from a feature frame that
    already has technical + macro columns (model.build_feature_frame). Returns
    (result_dict | None, info_dict). result_dict carries boosters, metadata
    (calibration + feature_reference + cv_metrics), feature_cols, and OOS preds.
    """
    labelled = build_labelled_frame(featured, label_config)
    if labelled.empty:
        return None, {"reason": "no_labelled_rows"}

    max_date = labelled["date"].max()
    start_date = max_date - timedelta(
        days=365 * int(gate_config.get("validation_years", 4))
    )
    labelled = labelled[labelled["date"] >= start_date].reset_index(drop=True)

    horizon_days = effective_horizon(label_config)
    purge_gap = resolve_purge_gap(gate_config, effective_horizon_days=horizon_days)
    min_required = phase1_training_min_rows(
        gate_config, effective_horizon_days=horizon_days
    )
    if len(labelled) < min_required:
        return None, {"reason": "insufficient_rows", "rows": int(len(labelled))}

    feature_cols = phase1_feature_cols(model_cfg.get("macro_features_enabled", True))
    artifact_contract = build_phase1_artifact_contract(
        label_config=label_config,
        feature_columns=feature_cols,
        macro_features_enabled=model_cfg.get("macro_features_enabled", True),
        calibration_mode=model_cfg.get("calibration_mode", "isotonic"),
    )
    gate_contract = build_phase1_gate_contract(gate_config, artifact_contract)
    canonical_gate_config = gate_contract["gate_config"]
    folds, final, oos = train_horizon_models(
        labelled,
        feature_cols,
        canonical_gate_config,
        effective_horizon_days=horizon_days,
    )
    if final is None:
        return None, {
            "reason": "training_failed",
            "deployment_split": oos.attrs.get("deployment_split", {}),
        }

    deployment_split = dict(oos.attrs.get("deployment_split") or {})
    tuning_oos, holdout_oos, split_info = backtest_module._split_oos_for_thresholding(
        oos, canonical_gate_config, horizon=horizon_days
    )
    if holdout_oos.empty:
        return None, {"reason": "holdout_oos_missing", "split": split_info}

    calibrator, cal_info = fit_calibrator(
        tuning_oos.get("raw_score"),
        tuning_oos.get("target_class"),
        mode=model_cfg.get("calibration_mode", "isotonic"),
        min_rows=int(model_cfg.get("min_calibration_rows", 60)),
    )

    tuning_oos = tuning_oos.copy()
    holdout_oos = holdout_oos.copy()
    tuning_oos["prob_up"] = apply_isotonic(calibrator, tuning_oos.get("raw_score"))
    holdout_oos["prob_up"] = apply_isotonic(calibrator, holdout_oos.get("raw_score"))
    thresholds, threshold_optimization = backtest_module._optimize_thresholds(
        tuning_oos, canonical_gate_config, horizon=horizon_days
    )
    sim_tuning = backtest_module._simulate_strategy(
        tuning_oos,
        canonical_gate_config,
        thresholds=thresholds,
        horizon=horizon_days,
    )
    sim_holdout = backtest_module._simulate_strategy(
        holdout_oos,
        canonical_gate_config,
        thresholds=thresholds,
        horizon=horizon_days,
    )
    metrics_tuning = backtest_module._compute_metrics(sim_tuning)
    metrics_holdout = backtest_module._compute_metrics(sim_holdout)
    gate_disabled = not bool(canonical_gate_config.get("enabled", True))
    failures = (
        []
        if gate_disabled
        else backtest_module._evaluate_gate_rules(
            metrics_holdout, canonical_gate_config
        )
    )
    threshold_optimization = {
        **threshold_optimization,
        **split_info,
        "threshold_tuning_used": bool(
            split_info.get("threshold_tuning_used")
            and threshold_optimization.get("enabled")
        ),
    }

    raw_scores = holdout_oos.get("raw_score")
    labels = holdout_oos.get("target_class")
    fwd = holdout_oos.get("fwd_return")
    cal_prob = holdout_oos.get("prob_up")
    cv_metrics = {
        "ic": ic_score(raw_scores, fwd),
        "auc": auc_score(raw_scores, labels),
        "brier": brier_score(cal_prob, labels),
        "brier_raw": brier_score(raw_scores, labels),
        "hit_rate": hit_rate(cal_prob, labels),
        "oos_rows": int(len(tuning_oos) + len(holdout_oos)),
        "tuning_rows": int(len(tuning_oos)),
        "holdout_rows": int(len(holdout_oos)),
        "calibration": cal_info,
        "reliability": reliability_bins(cal_prob, labels) if len(oos) else [],
    }

    reference_end = int(
        (deployment_split.get("deployment_internal") or {}).get(
            "train_pool_end", len(labelled)
        )
    )
    feature_reference = build_feature_reference(
        labelled.iloc[:reference_end], feature_cols
    )
    boosters = {"folds": folds, "final": final}
    exact_model_hash = booster_bundle_sha256(boosters)
    applied_calibration_id = phase1_calibration_id("isotonic" if calibrator else "none")
    calibrator_hash = payload_sha256(calibrator)
    split_evidence = {
        **split_info,
        **deployment_split,
        "tuning_start_date": _iso_oos_date(tuning_oos, 0),
        "tuning_end_date": _iso_oos_date(tuning_oos, -1),
        "holdout_start_date": _iso_oos_date(holdout_oos, 0),
        "holdout_end_date": _iso_oos_date(holdout_oos, -1),
        "calibration_fit_split": "tuning",
        "threshold_tuning_used": bool(
            threshold_optimization.get("threshold_tuning_used")
        ),
        "threshold_fit_split": (
            "tuning"
            if threshold_optimization.get("threshold_tuning_used")
            else "fixed_defaults_no_tuning"
        ),
        "gate_evaluation_split": "holdout",
        "holdout_model_is_persisted_final": True,
        "external_oos_used_for_early_stopping": False,
    }
    gate_evidence = build_phase1_gate_evidence(
        model_version=model_version,
        model_bundle_sha256=exact_model_hash,
        artifact_contract=artifact_contract,
        gate_contract=gate_contract,
        calibrator_sha256=calibrator_hash,
        applied_calibration_id=applied_calibration_id,
        oos_prediction_sha256=_oos_prediction_hash(tuning_oos, holdout_oos),
        split=split_evidence,
        passed=gate_disabled or not failures,
        skipped=gate_disabled,
        reason=(
            "gate_disabled"
            if gate_disabled
            else ("ok" if not failures else "kpi_failed")
        ),
        failures=failures,
        thresholds=thresholds,
        threshold_optimization=threshold_optimization,
        metrics_tuning=metrics_tuning,
        metrics_holdout=metrics_holdout,
    )
    evidence_failures = verify_phase1_gate_evidence(
        gate_evidence,
        model_version=model_version,
        artifact_contract=artifact_contract,
        gate_contract=gate_contract,
        model_bundle_sha256=exact_model_hash,
        calibrator_sha256=calibrator_hash,
        applied_calibration_id=applied_calibration_id,
    )
    if evidence_failures:
        return None, {
            "reason": "gate_evidence_invalid",
            "failures": evidence_failures,
        }

    metadata = {
        "calibration": calibrator,
        "feature_reference": feature_reference,
        "cv_metrics": cv_metrics,
        "model_version": model_version,
        "model_bundle_sha256": exact_model_hash,
        "artifact_contract": artifact_contract,
        "gate_contract": gate_contract,
        **phase1_gate_contract_metadata_fields(gate_contract),
        "gate_evidence": gate_evidence,
        "artifact_schema_version": PHASE1_ARTIFACT_SCHEMA_VERSION,
        "label_config": artifact_contract["label_config"],
        "effective_horizon_days": artifact_contract["effective_horizon_days"],
        "feature_columns": artifact_contract["feature_columns"],
        "feature_schema_hash": artifact_contract["feature_schema_hash"],
        "macro_features_enabled": artifact_contract["macro_features_enabled"],
        "calibration_mode": artifact_contract["calibration_mode"],
        "calibration_id": artifact_contract["calibration_id"],
        "calibration_applied": bool(calibrator),
        "applied_calibration_id": applied_calibration_id,
        "calibrator_sha256": calibrator_hash,
        "execution_contract_version": artifact_contract["execution_contract_version"],
        "effective_purge_gap": purge_gap,
    }
    result = {
        "boosters": boosters,
        "metadata": metadata,
        "feature_cols": feature_cols,
        "cv_metrics": cv_metrics,
        "calibration_info": cal_info,
        "oos": oos,
        "gate_result": phase1_gate_result_from_evidence(gate_evidence),
    }
    return result, {
        "reason": "ok",
        "rows": int(len(labelled)),
        "gate_passed": gate_evidence["passed"],
    }


# --- inference --------------------------------------------------------------


def predict_ticker(
    featured: pd.DataFrame, bundle: dict, label_config: dict | None = None
):
    """
    Phase 1 inference for the most recent row of a feature frame, using a
    persisted bundle (folds/final + calibration + feature_reference). Returns a
    dict with raw_score, calibrated prob_up, expected_ret, features_hash,
    horizon_days and artifact provenance; or None when inference is not
    possible. ``label_config`` is retained for call compatibility but saved
    inference never derives provenance from runtime configuration.
    """
    if featured is None or featured.empty:
        return None

    ticker_metadata = bundle.get("ticker_metadata") or {}
    artifact_contract = ticker_metadata.get("artifact_contract")
    if not isinstance(artifact_contract, dict):
        return None
    if (
        artifact_contract.get("artifact_schema_version")
        != PHASE1_ARTIFACT_SCHEMA_VERSION
    ):
        return None

    feature_reference = bundle.get("feature_reference") or {}
    feature_cols = list(artifact_contract.get("feature_columns") or [])
    if not feature_cols:
        return None
    if phase1_feature_schema_hash(feature_cols) != artifact_contract.get(
        "feature_schema_hash"
    ):
        return None
    if list(feature_reference.get("feature_cols") or []) != feature_cols:
        return None

    # Align to the trained feature order; missing columns become NaN.
    X = featured.iloc[[-1]].reindex(columns=feature_cols)
    raw = predict_prob_with_bundle(bundle, X)
    if raw is None:
        return None

    calibrator = bundle.get("calibration")
    if payload_sha256(calibrator) != ticker_metadata.get("calibrator_sha256"):
        return None
    gate_contract = ticker_metadata.get("gate_contract")
    gate_evidence = ticker_metadata.get("gate_evidence")
    version = bundle.get("version") or ticker_metadata.get("model_version")
    try:
        exact_model_hash = booster_bundle_sha256(
            {"folds": bundle.get("folds") or [], "final": bundle.get("final")}
        )
    except (TypeError, ValueError):
        return None
    evidence_failures = verify_phase1_gate_evidence(
        gate_evidence,
        model_version=version,
        artifact_contract=artifact_contract,
        gate_contract=gate_contract or {},
        model_bundle_sha256=exact_model_hash,
        calibrator_sha256=payload_sha256(calibrator),
        applied_calibration_id=ticker_metadata.get("applied_calibration_id"),
    )
    if evidence_failures:
        return None
    prob_up = float(apply_isotonic(calibrator, [raw])[0])
    return {
        "raw_score": float(raw),
        "prob_up": prob_up,
        "expected_ret": expected_return(prob_up, feature_reference),
        "features_hash": features_hash(X.iloc[0], feature_cols),
        "horizon_days": int(artifact_contract["effective_horizon_days"]),
        "feature_cols": feature_cols,
        "artifact_contract": artifact_contract,
        "artifact_schema_version": artifact_contract["artifact_schema_version"],
        "label_config": artifact_contract["label_config"],
        "feature_schema_hash": artifact_contract["feature_schema_hash"],
        "macro_features_enabled": artifact_contract["macro_features_enabled"],
        "calibration_mode": artifact_contract["calibration_mode"],
        "calibration_id": artifact_contract["calibration_id"],
        "applied_calibration_id": ticker_metadata.get("applied_calibration_id"),
        "execution_contract_version": artifact_contract["execution_contract_version"],
        "gate_result": phase1_gate_result_from_evidence(gate_evidence),
        "gate_evidence_sha256": gate_evidence.get("evidence_sha256"),
        "gate_config_hash": gate_evidence.get("gate_config_hash"),
        "model_bundle_sha256": exact_model_hash,
    }
