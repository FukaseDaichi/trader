"""
Probability calibration (roadmap §6.3, W7) with NO extra dependencies.

scikit-learn is not installed, so isotonic regression is implemented here with
the Pool Adjacent Violators Algorithm (PAVA). Calibrators are plain JSON-able
dicts ({"method": "isotonic", "x": [...], "y": [...]}) so they can be stored in
`model_registry.calibration` per ticker and reloaded from a model artifact.

Pure numpy logic; unit-tested standalone in tests/test_calibration.py.
"""

from __future__ import annotations

import numpy as np


def _clean_pairs(scores, labels):
    s = np.asarray(scores, dtype="float64").ravel()
    y = np.asarray(labels, dtype="float64").ravel()
    n = min(s.size, y.size)
    s, y = s[:n], y[:n]
    mask = np.isfinite(s) & np.isfinite(y)
    return s[mask], y[mask]


def fit_isotonic_pava(scores, labels) -> dict | None:
    """
    Fit a non-decreasing isotonic map from raw score -> empirical P(label=1).

    Returns a JSON-serializable calibrator (knots x, fitted y), or None when
    there is no usable data.
    """
    s, y = _clean_pairs(scores, labels)
    if s.size == 0:
        return None

    order = np.argsort(s, kind="mergesort")
    s, y = s[order], y[order]

    # Collapse tied scores into unique knots (weighted mean label).
    ux, inv, counts = np.unique(s, return_inverse=True, return_counts=True)
    sums = np.zeros(ux.size, dtype="float64")
    np.add.at(sums, inv, y)
    means = sums / counts

    # PAVA: merge adjacent blocks that violate monotonicity (weighted by counts).
    block_val: list[float] = []
    block_w: list[float] = []
    block_n: list[int] = []  # number of unique knots covered by the block
    for value, weight in zip(means.tolist(), counts.astype(float).tolist()):
        cur_v, cur_w, cur_n = value, weight, 1
        while block_val and block_val[-1] > cur_v:
            pv = block_val.pop()
            pw = block_w.pop()
            pn = block_n.pop()
            cur_v = (pv * pw + cur_v * cur_w) / (pw + cur_w)
            cur_w = pw + cur_w
            cur_n = pn + cur_n
        block_val.append(cur_v)
        block_w.append(cur_w)
        block_n.append(cur_n)

    fitted: list[float] = []
    for value, n in zip(block_val, block_n):
        fitted.extend([value] * n)
    fitted_arr = np.clip(np.asarray(fitted, dtype="float64"), 0.0, 1.0)

    return {"method": "isotonic", "x": ux.tolist(), "y": fitted_arr.tolist()}


def apply_isotonic(calibrator, scores):
    """
    Map raw scores through the calibrator. If calibrator is None/empty, the
    scores are returned clipped to [0, 1] (identity calibration).
    Always returns a numpy array.
    """
    s = np.asarray(scores, dtype="float64").ravel()
    if not calibrator or not calibrator.get("x"):
        return np.clip(s, 0.0, 1.0)

    x = np.asarray(calibrator["x"], dtype="float64")
    y = np.asarray(calibrator["y"], dtype="float64")
    if x.size == 0:
        return np.clip(s, 0.0, 1.0)
    if x.size == 1:
        return np.clip(np.full(s.shape, y[0]), 0.0, 1.0)

    # np.interp clamps to y[0] / y[-1] outside the fitted range (out_of_bounds=clip).
    return np.clip(np.interp(s, x, y), 0.0, 1.0)


def brier_score(prob, labels):
    """Mean squared error between predicted probability and 0/1 label."""
    p, y = _clean_pairs(prob, labels)
    if p.size == 0:
        return None
    return float(np.mean((p - y) ** 2))


def reliability_bins(prob, labels, n_bins: int = 10) -> list[dict]:
    """
    Reliability-curve bins: for each equal-width probability bin, the count,
    mean predicted probability, and mean observed frequency.
    """
    p, y = _clean_pairs(prob, labels)
    edges = np.linspace(0.0, 1.0, int(n_bins) + 1)
    bins = []
    for i in range(int(n_bins)):
        lo, hi = float(edges[i]), float(edges[i + 1])
        if i == int(n_bins) - 1:
            sel = (p >= lo) & (p <= hi)
        else:
            sel = (p >= lo) & (p < hi)
        cnt = int(sel.sum())
        bins.append(
            {
                "bin": i,
                "lo": lo,
                "hi": hi,
                "count": cnt,
                "mean_pred": float(np.mean(p[sel])) if cnt else None,
                "mean_obs": float(np.mean(y[sel])) if cnt else None,
            }
        )
    return bins


def _rankdata(values: np.ndarray) -> np.ndarray:
    """1-based average ranks (ties share the mean rank)."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size, dtype="float64")
    sorted_v = values[order]
    i, n = 0, values.size
    while i < n:
        j = i
        while j + 1 < n and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        ranks[order[i : j + 1]] = avg_rank
        i = j + 1
    return ranks


def ic_score(scores, values):
    """Information coefficient: Pearson corr between score and forward value."""
    s, v = _clean_pairs(scores, values)
    if s.size < 3:
        return None
    if np.std(s) == 0 or np.std(v) == 0:
        return 0.0
    return float(np.corrcoef(s, v)[0, 1])


def auc_score(scores, labels):
    """ROC AUC via the rank-sum (Mann-Whitney) identity. None if single-class."""
    s, y = _clean_pairs(scores, labels)
    if s.size == 0:
        return None
    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = _rankdata(s)
    sum_pos = float(ranks[y == 1].sum())
    return float((sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def hit_rate(prob, labels, threshold: float = 0.5):
    """Fraction where the thresholded prediction matches the 0/1 label."""
    p, y = _clean_pairs(prob, labels)
    if p.size == 0:
        return None
    pred = (p >= threshold).astype("float64")
    return float(np.mean(pred == y))


def fit_calibrator(scores, labels, mode: str = "isotonic", min_rows: int = 60):
    """
    Fit a calibrator with a guard rail (Task 5 acceptance):
      - mode != 'isotonic'              -> no calibration
      - fewer than min_rows samples     -> no calibration
      - calibrated Brier worse than raw -> fall back to no calibration

    Returns (calibrator_or_None, info) where info carries brier_raw/brier_cal,
    whether calibration was applied, and the reason.
    """
    s, y = _clean_pairs(scores, labels)
    n = int(s.size)
    brier_raw = brier_score(s, y)

    if mode != "isotonic":
        return None, {
            "applied": False,
            "reason": "mode_none",
            "rows": n,
            "brier_raw": brier_raw,
            "brier_cal": brier_raw,
        }
    if n < int(min_rows):
        return None, {
            "applied": False,
            "reason": "insufficient_rows",
            "rows": n,
            "brier_raw": brier_raw,
            "brier_cal": brier_raw,
        }

    calibrator = fit_isotonic_pava(s, y)
    cal_prob = apply_isotonic(calibrator, s)
    brier_cal = brier_score(cal_prob, y)

    if brier_cal is None or (brier_raw is not None and brier_cal > brier_raw + 1e-12):
        return None, {
            "applied": False,
            "reason": "worsened_fallback_none",
            "rows": n,
            "brier_raw": brier_raw,
            "brier_cal": brier_cal,
        }

    return calibrator, {
        "applied": True,
        "reason": "ok",
        "rows": n,
        "brier_raw": brier_raw,
        "brier_cal": brier_cal,
    }
