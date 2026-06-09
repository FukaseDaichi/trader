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
from .model import PHASE1_FEATURE_COLS, predict_prob_with_bundle, train_horizon_models


# --- PSI / feature reference ------------------------------------------------

# Calendar / cyclical / near-constant features are excluded from PSI: a short
# recent window naturally covers only part of the cycle (e.g. ~6 of 12 months),
# which produces a huge but meaningless PSI. The model saw every season in
# training, so these are not "drift". (Binary/constant features are also skipped
# automatically by the <3-unique-values guard in _psi_reference_for.)
PSI_EXCLUDE_FEATURES = frozenset({
    # calendar / cyclical
    "day_of_week", "month", "is_month_end", "is_month_start",
    # absolute price-scale (in yen, grows with the price level -> non-stationary)
    "macd", "macd_signal", "macd_hist", "macd_hist_change",
    # qualitative constant
    "macro_bias_score",
})


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


def build_feature_reference(labelled: pd.DataFrame, feature_cols, n_bins: int = 10,
                            ref_rows: int = 250) -> dict:
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
    psi_frame = labelled.tail(ref_rows) if ref_rows and len(labelled) > ref_rows else labelled
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
        "psi_ref_rows": int(min(len(labelled), ref_rows)) if ref_rows else int(len(labelled)),
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


# --- training ---------------------------------------------------------------

def train_ticker_bundle(featured: pd.DataFrame, gate_config: dict,
                        label_config: dict, model_cfg: dict):
    """
    Train one ticker's Phase 1 ensemble from a feature frame that already has
    technical + macro columns (model.build_feature_frame). Returns
    (result_dict | None, info_dict). result_dict carries boosters, metadata
    (calibration + feature_reference + cv_metrics), feature_cols, and OOS preds.
    """
    labelled = build_labelled_frame(featured, label_config)
    if labelled.empty:
        return None, {"reason": "no_labelled_rows"}

    max_date = labelled["date"].max()
    start_date = max_date - timedelta(days=365 * int(gate_config.get("validation_years", 4)))
    labelled = labelled[labelled["date"] >= start_date].reset_index(drop=True)

    min_required = (int(gate_config.get("train_min_rows", 200))
                    + int(gate_config.get("val_size", 60))
                    + int(gate_config.get("purge_gap", 5)))
    if len(labelled) < min_required:
        return None, {"reason": "insufficient_rows", "rows": int(len(labelled))}

    feature_cols = list(PHASE1_FEATURE_COLS)
    folds, final, oos = train_horizon_models(labelled, feature_cols, gate_config)
    if final is None and not folds:
        return None, {"reason": "training_failed"}

    calibrator, cal_info = fit_calibrator(
        oos.get("raw_score"), oos.get("target_class"),
        mode=model_cfg.get("calibration_mode", "isotonic"),
        min_rows=int(model_cfg.get("min_calibration_rows", 60)),
    )

    raw_scores = oos.get("raw_score")
    labels = oos.get("target_class")
    fwd = oos.get("fwd_return")
    cal_prob = apply_isotonic(calibrator, raw_scores) if len(oos) else []
    cv_metrics = {
        "ic": ic_score(raw_scores, fwd),
        "auc": auc_score(raw_scores, labels),
        "brier": brier_score(cal_prob, labels),
        "brier_raw": brier_score(raw_scores, labels),
        "hit_rate": hit_rate(cal_prob, labels),
        "oos_rows": int(len(oos)),
        "calibration": cal_info,
        "reliability": reliability_bins(cal_prob, labels) if len(oos) else [],
    }

    feature_reference = build_feature_reference(labelled, feature_cols)
    metadata = {
        "calibration": calibrator,
        "feature_reference": feature_reference,
        "cv_metrics": cv_metrics,
    }
    result = {
        "boosters": {"folds": folds, "final": final},
        "metadata": metadata,
        "feature_cols": feature_cols,
        "cv_metrics": cv_metrics,
        "calibration_info": cal_info,
        "oos": oos,
    }
    return result, {"reason": "ok", "rows": int(len(labelled))}


# --- inference --------------------------------------------------------------

def predict_ticker(featured: pd.DataFrame, bundle: dict, label_config: dict):
    """
    Phase 1 inference for the most recent row of a feature frame, using a
    persisted bundle (folds/final + calibration + feature_reference). Returns a
    dict with raw_score, calibrated prob_up, expected_ret, features_hash,
    horizon_days; or None when inference is not possible.
    """
    if featured is None or featured.empty:
        return None

    feature_reference = bundle.get("feature_reference") or {}
    feature_cols = feature_reference.get("feature_cols") or list(PHASE1_FEATURE_COLS)

    # Align to the trained feature order; missing columns become NaN.
    X = featured.iloc[[-1]].reindex(columns=feature_cols)
    raw = predict_prob_with_bundle(bundle, X)
    if raw is None:
        return None

    calibrator = bundle.get("calibration")
    prob_up = float(apply_isotonic(calibrator, [raw])[0])
    return {
        "raw_score": float(raw),
        "prob_up": prob_up,
        "expected_ret": expected_return(prob_up, feature_reference),
        "features_hash": features_hash(X.iloc[0], feature_cols),
        "horizon_days": effective_horizon(label_config),
        "feature_cols": feature_cols,
    }
