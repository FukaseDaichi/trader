"""
Phase 2 cross-sectional LightGBM model (roadmap §7, Task 4B).

Train a single cross-sectional model over a long panel (one row per
date x ticker), produce daily relative scores, calibrate those scores into
P(up) / expected-return estimates, and report walk-forward out-of-sample
(OOS) ranking metrics.

This module is intentionally pure-ish ML logic (pandas / numpy / lightgbm):
there is NO database or network access here. Persistence of the resulting
bundle is owned by ``src.model_store`` (Task 4A).

Key design points
------------------
- **No leakage walk-forward by DATE.** Folds split on the sorted unique dates,
  with a ``purge_gap`` between the train tail and the validation block, so a
  forward-looking label (computed via shift(-H) per ticker upstream) of a
  training row can never overlap the validation window.
- **Ranker vs regression.** ``objective="ranker"`` trains a LambdaRank model on
  the integer relevance grade ``target_rank_bucket`` with per-date ``group``
  arrays; ``objective="regression"`` trains an L2 model on the volatility-
  normalised forward return ``target_vol_norm``. A ranker on tiny daily groups
  is meaningless, so we fall back to regression when the median group size < 5.
- **Determinism.** LightGBM is configured with ``deterministic=True``,
  ``num_threads=1`` and fixed seeds so tests are byte-stable.

Ranker ``group`` contract (important)
-------------------------------------
LightGBM's LambdaRank consumes the training matrix as a sequence of *query
groups*; the ``group`` array lists the number of consecutive rows belonging to
each group, and those rows MUST be contiguous in the matrix. We therefore
ALWAYS sort the rows by ``date`` first and then derive the group sizes from
``df.groupby("date", sort=True).size().tolist()`` on that already-sorted frame.
Because both the matrix and the group sizes come from the same sorted frame,
``sum(group) == len(df)`` and every group is contiguous by construction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .calibration import brier_score
from .config import BACKTEST_GATE_CONFIG, get_cross_section_config
from .cross_section import cross_sectional_feature_cols, drop_small_date_groups

try:  # pragma: no cover - import guard
    import lightgbm as lgb
except Exception:  # pragma: no cover
    lgb = None  # type: ignore


# Minimum median daily group size below which a ranker objective is pointless.
_RANKER_MIN_GROUP = 5
# Fixed boosting rounds (no early stopping -> deterministic on small panels and
# avoids ranker eval-group bookkeeping).
_NUM_BOOST_ROUND = 300

# OOS frame schema (the model_store bundle / portfolio layer rely on these).
_OOS_COLS = [
    "date",
    "ticker",
    "raw_score",
    "fwd_return",
    "target_up",
    "target_vol_norm",
    "target_rank_bucket",
]

# Prediction frame schema returned by predict_cs_model.
_PRED_COLS = ["ticker", "raw_score", "cs_rank", "score_pct", "prob_up", "expected_ret"]


# ---------------------------------------------------------------------------
# LightGBM params (mirror src.model._LGB_PARAMS regularisation, per-objective)
# ---------------------------------------------------------------------------

def _lgb_params(objective: str, seed: int) -> dict:
    """Deterministic, regularised params for the resolved objective."""
    params = {
        "boosting_type": "gbdt",
        "learning_rate": 0.03,
        "num_leaves": 15,
        "max_depth": 4,
        "min_child_samples": 30,
        "feature_fraction": 0.6,
        "bagging_fraction": 0.7,
        "bagging_freq": 5,
        "lambda_l1": 0.5,
        "lambda_l2": 2.0,
        "min_data_in_bin": 5,
        "verbosity": -1,
        "deterministic": True,
        "num_threads": 1,
        "seed": int(seed),
        "bagging_seed": int(seed),
        "feature_fraction_seed": int(seed),
        "data_random_seed": int(seed),
    }
    if objective == "ranker":
        params["objective"] = "lambdarank"
        params["metric"] = "ndcg"
        # Cap the +ve relevance grades at the bucket maximum (0..4).
        params["label_gain"] = [float(2 ** i - 1) for i in range(5)]
    else:
        params["objective"] = "regression_l2"
        params["metric"] = "l2"
    return params


def _group_sizes(df: pd.DataFrame) -> list[int]:
    """Per-date row counts on an already date-sorted frame (ranker group array).

    The caller MUST have sorted ``df`` by ``date`` first. ``sort=True`` keeps the
    group order aligned with the row order, so ``sum`` equals ``len(df)``.
    """
    return df.groupby("date", sort=True).size().tolist()


# ---------------------------------------------------------------------------
# train_cs_model
# ---------------------------------------------------------------------------

def train_cs_model(panel, config=None, *, macro_enabled: bool = True, seed: int = 42):
    """Train a cross-sectional LightGBM model with a walk-forward OOS evaluation.

    Returns ``(bundle_dict | None, info_dict)``. Never raises for "not enough
    data" — structural shortfalls return ``(None, {"reason": ...})``.
    """
    if lgb is None:  # pragma: no cover
        return None, {"reason": "lightgbm_unavailable"}

    cfg = {**get_cross_section_config(), **BACKTEST_GATE_CONFIG}
    if config:
        cfg.update(config)

    if panel is None or len(panel) == 0:
        return None, {"reason": "empty_panel"}

    feature_cols = [c for c in cross_sectional_feature_cols(macro_enabled) if c in panel.columns]
    if not feature_cols:
        return None, {"reason": "no_feature_cols"}

    # --- Preprocess: lookback window + drop thin dates ---
    work = panel.copy()
    work["date"] = pd.to_datetime(work["date"])
    lookback_years = int(cfg.get("panel_lookback_years", 5))
    max_date = work["date"].max()
    cutoff = max_date - pd.DateOffset(years=lookback_years)
    work = work[work["date"] >= cutoff]
    work = drop_small_date_groups(work, cfg.get("min_daily_names"))
    if work.empty:
        return None, {"reason": "empty_after_preprocess"}

    # --- Objective resolution (ranker -> regression fallback on tiny groups) ---
    requested_objective = cfg.get("objective", "ranker")
    group_sizes_all = work.groupby("date", sort=True).size()
    median_group_size = float(group_sizes_all.median()) if len(group_sizes_all) else 0.0

    fallback_reason = None
    objective = requested_objective
    if requested_objective == "ranker" and median_group_size < _RANKER_MIN_GROUP:
        objective = "regression"
        fallback_reason = (
            f"ranker_groups_too_small(median={median_group_size:.1f}<{_RANKER_MIN_GROUP})"
        )

    # --- Label per objective ---
    label_col = "target_rank_bucket" if objective == "ranker" else "target_vol_norm"
    if label_col not in work.columns:
        return None, {"reason": f"missing_label:{label_col}"}

    usable = work[work[label_col].notna()].copy()
    usable = usable.sort_values(["date", "ticker"]).reset_index(drop=True)

    train_min_rows = int(cfg.get("train_min_rows", 200))
    n_folds = int(cfg.get("n_folds", 3))
    unique_dates = np.sort(usable["date"].unique())
    n_dates = len(unique_dates)

    if len(usable) < train_min_rows or n_dates < (n_folds + 1):
        return None, {
            "reason": "insufficient_panel",
            "rows": int(len(usable)),
            "dates": int(n_dates),
            "objective": objective,
        }

    # --- Walk-forward folds by date ---
    val_dates = min(max(20, int(cfg.get("val_size", 60))), n_dates - 1)
    purge_gap = int(cfg.get("purge_gap", 5))
    # Need at least one training date after purge.
    min_train_dates = 1

    oos_parts: list[pd.DataFrame] = []
    for fold_idx in range(n_folds):
        val_end = n_dates - fold_idx * val_dates
        val_start = val_end - val_dates
        train_end = val_start - purge_gap
        if val_start < 0 or train_end < min_train_dates:
            continue

        # NOTE: index with the numpy datetime64 slices directly. Building a set
        # via ``.tolist()`` would coerce datetime64[ns] to int nanoseconds and
        # silently match nothing in ``isin`` against a datetime column.
        train_dates = unique_dates[:train_end]
        val_dates_slice = unique_dates[val_start:val_end]

        train_rows = usable[usable["date"].isin(train_dates)]
        val_rows = usable[usable["date"].isin(val_dates_slice)]
        if train_rows.empty or val_rows.empty:
            continue

        # Sort by date so ranker group arrays line up with the row order.
        train_rows = train_rows.sort_values(["date", "ticker"]).reset_index(drop=True)
        val_rows = val_rows.sort_values(["date", "ticker"]).reset_index(drop=True)

        booster = _fit_booster(train_rows, feature_cols, label_col, objective, seed)
        if booster is None:
            continue

        val_X = val_rows[feature_cols]
        preds = booster.predict(val_X)

        part = pd.DataFrame({
            "date": val_rows["date"].values,
            "ticker": val_rows["ticker"].values,
            "raw_score": np.asarray(preds, dtype="float64"),
            "fwd_return": pd.to_numeric(val_rows.get("fwd_return"), errors="coerce").values
            if "fwd_return" in val_rows.columns else np.nan,
            "target_up": pd.to_numeric(val_rows.get("target_up"), errors="coerce").values
            if "target_up" in val_rows.columns else np.nan,
            "target_vol_norm": pd.to_numeric(val_rows.get("target_vol_norm"), errors="coerce").values
            if "target_vol_norm" in val_rows.columns else np.nan,
            "target_rank_bucket": pd.to_numeric(val_rows.get("target_rank_bucket"), errors="coerce").values
            if "target_rank_bucket" in val_rows.columns else np.nan,
        })
        oos_parts.append(part)

    if oos_parts:
        oos_df = pd.concat(oos_parts, ignore_index=True)
        # If a date appears in multiple folds keep the first occurrence.
        oos_df = oos_df.drop_duplicates(subset=["date", "ticker"], keep="first")
        oos_df = oos_df.sort_values(["date", "ticker"]).reset_index(drop=True)
    else:
        oos_df = pd.DataFrame(columns=_OOS_COLS)

    # --- Final booster on ALL usable rows (inference model) ---
    final_rows = usable.sort_values(["date", "ticker"]).reset_index(drop=True)
    final_booster = _fit_booster(final_rows, feature_cols, label_col, objective, seed)
    if final_booster is None:
        return None, {"reason": "final_train_failed", "objective": objective}

    # --- Calibration + metrics ---
    calibration = fit_score_calibration(oos_df)
    metrics = cs_metrics(oos_df, top_n=int(cfg.get("top_n", 8)))

    # Brier on calibrated P(up) vs target_up over OOS rows.
    brier = None
    if not oos_df.empty and "target_up" in oos_df.columns:
        pct = oos_df.groupby("date")["raw_score"].rank(pct=True)
        cal_prob = np.array(
            [apply_score_calibration(calibration, float(p))[0] if np.isfinite(p) else np.nan
             for p in pct.to_numpy()],
            dtype="float64",
        )
        brier = brier_score(cal_prob, oos_df["target_up"].to_numpy())

    metrics["brier"] = brier
    metrics["n_oos_rows"] = int(len(oos_df))
    metrics["n_oos_dates"] = int(oos_df["date"].nunique()) if not oos_df.empty else 0
    metrics["objective"] = objective
    metrics["median_group_size"] = median_group_size

    # --- Feature drift reference (mean/std over final training rows) ---
    feature_means: dict[str, float] = {}
    feature_stds: dict[str, float] = {}
    for col in feature_cols:
        series = pd.to_numeric(final_rows[col], errors="coerce")
        m = series.mean()
        s = series.std(ddof=0)
        feature_means[col] = float(m) if pd.notna(m) else None
        feature_stds[col] = float(s) if pd.notna(s) else None

    bundle = {
        "booster": final_booster,
        "feature_cols": list(feature_cols),
        "objective": objective,
        "macro_enabled": bool(macro_enabled),
        "calibration": calibration,
        "oos_predictions": oos_df,
        "metrics": metrics,
        "universe": sorted(panel["ticker"].dropna().unique().tolist()),
        "sector_encoder": {},  # reserved; sector currently only feeds sect_rank features
        "feature_reference": {
            "feature_cols": list(feature_cols),
            "feature_means": feature_means,
            "feature_stds": feature_stds,
        },
    }
    info = {
        "reason": "ok",
        "rows": int(len(usable)),
        "objective": objective,
        "fallback_reason": fallback_reason,
    }
    return bundle, info


def _fit_booster(rows, feature_cols, label_col, objective, seed):
    """Train one booster on a date-sorted frame. Returns None on failure."""
    if rows.empty:
        return None
    X = rows[feature_cols]
    y = pd.to_numeric(rows[label_col], errors="coerce").to_numpy()
    params = _lgb_params(objective, seed)

    if objective == "ranker":
        group = _group_sizes(rows)
        if sum(group) != len(rows):  # defensive: should never trip
            return None
        dataset = lgb.Dataset(X, label=y, group=group, free_raw_data=False)
    else:
        dataset = lgb.Dataset(X, label=y, free_raw_data=False)

    try:
        booster = lgb.train(params, dataset, num_boost_round=_NUM_BOOST_ROUND)
    except Exception:  # noqa: BLE001 — robustness: treat as a failed fold
        return None
    return booster


# ---------------------------------------------------------------------------
# predict_cs_model
# ---------------------------------------------------------------------------

def predict_cs_model(bundle, latest_panel) -> pd.DataFrame:
    """Score the latest cross-section. One row per ticker, sorted by cs_rank asc."""
    empty = pd.DataFrame(columns=_PRED_COLS)
    if bundle is None or latest_panel is None or len(latest_panel) == 0:
        return empty

    panel = latest_panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    latest_date = panel["date"].max()
    rows = panel[panel["date"] == latest_date].copy()
    if rows.empty:
        return empty

    feature_cols = bundle.get("feature_cols", [])
    X = rows.reindex(columns=feature_cols)  # missing cols -> NaN (LightGBM tolerates)

    raw_score = np.asarray(bundle["booster"].predict(X), dtype="float64")
    out = pd.DataFrame({
        "ticker": rows["ticker"].values,
        "raw_score": raw_score,
    })

    # cs_rank: 1 = highest raw_score (descending), ordinal via method="first".
    out["cs_rank"] = out["raw_score"].rank(method="first", ascending=False).astype(int)
    # score_pct: within-date percentile rank in [0, 1].
    out["score_pct"] = out["raw_score"].rank(pct=True)

    probs = np.empty(len(out), dtype="float64")
    rets = np.empty(len(out), dtype="float64")
    calibration = bundle.get("calibration")
    for i, sp in enumerate(out["score_pct"].to_numpy()):
        p, r = apply_score_calibration(calibration, float(sp) if np.isfinite(sp) else 0.5)
        probs[i] = p
        rets[i] = r
    out["prob_up"] = probs
    out["expected_ret"] = rets

    out = out.sort_values("cs_rank").reset_index(drop=True)
    return out[_PRED_COLS]


# ---------------------------------------------------------------------------
# fit_score_calibration / apply_score_calibration
# ---------------------------------------------------------------------------

def fit_score_calibration(oos_predictions, n_buckets: int = 10) -> dict:
    """Map within-date score percentile -> empirical P(up) / expected return.

    Buckets the per-date percentile rank of ``raw_score`` into ``n_buckets``
    equal-width bins and stores the mean ``target_up`` / ``fwd_return`` per bin.
    """
    def _global(df):
        gp = 0.5
        gr = 0.0
        if df is not None and len(df):
            if "target_up" in df.columns:
                m = pd.to_numeric(df["target_up"], errors="coerce").mean()
                if pd.notna(m):
                    gp = float(m)
            if "fwd_return" in df.columns:
                m = pd.to_numeric(df["fwd_return"], errors="coerce").mean()
                if pd.notna(m):
                    gr = float(m)
        return gp, gr

    n_buckets = max(1, int(n_buckets))
    if oos_predictions is None or len(oos_predictions) < 2 * n_buckets:
        gp, gr = _global(oos_predictions)
        return {"applied": False, "n_buckets": 0,
                "global_prob_up": gp, "global_expected_ret": gr}

    df = oos_predictions.copy()
    gp, gr = _global(df)

    pct = df.groupby("date")["raw_score"].rank(pct=True).to_numpy()
    edges = np.linspace(0.0, 1.0, n_buckets + 1)
    # Bucket index in [0, n_buckets-1]; right edge inclusive in last bin.
    idx = np.clip(np.searchsorted(edges, pct, side="right") - 1, 0, n_buckets - 1)

    target_up = pd.to_numeric(df.get("target_up"), errors="coerce").to_numpy() \
        if "target_up" in df.columns else np.full(len(df), np.nan)
    fwd_return = pd.to_numeric(df.get("fwd_return"), errors="coerce").to_numpy() \
        if "fwd_return" in df.columns else np.full(len(df), np.nan)

    prob_up = []
    expected_ret = []
    for b in range(n_buckets):
        sel = idx == b
        if sel.any():
            up_vals = target_up[sel]
            ret_vals = fwd_return[sel]
            up_mean = np.nanmean(up_vals) if np.isfinite(up_vals).any() else np.nan
            ret_mean = np.nanmean(ret_vals) if np.isfinite(ret_vals).any() else np.nan
            prob_up.append(float(up_mean) if np.isfinite(up_mean) else gp)
            expected_ret.append(float(ret_mean) if np.isfinite(ret_mean) else gr)
        else:
            prob_up.append(gp)
            expected_ret.append(gr)

    return {
        "applied": True,
        "n_buckets": n_buckets,
        "edges": edges.tolist(),
        "prob_up": prob_up,
        "expected_ret": expected_ret,
        "global_prob_up": gp,
        "global_expected_ret": gr,
    }


def apply_score_calibration(calibration, score_pct):
    """Map a scalar score percentile in [0, 1] -> (prob_up, expected_ret).

    Scalar-only contract: callers apply it row-wise. Returns (0.5, 0.0) when
    calibration is None, and the global means when not ``applied``.
    """
    if not calibration:
        return 0.5, 0.0
    if not calibration.get("applied"):
        return (
            float(calibration.get("global_prob_up", 0.5)),
            float(calibration.get("global_expected_ret", 0.0)),
        )

    gp = float(calibration.get("global_prob_up", 0.5))
    gr = float(calibration.get("global_expected_ret", 0.0))
    edges = calibration.get("edges") or []
    prob_up = calibration.get("prob_up") or []
    expected_ret = calibration.get("expected_ret") or []
    n_buckets = int(calibration.get("n_buckets", len(prob_up)))
    if n_buckets <= 0 or not prob_up:
        return gp, gr

    sp = float(score_pct)
    if not np.isfinite(sp):
        return gp, gr
    sp = min(max(sp, 0.0), 1.0)
    edges_arr = np.asarray(edges, dtype="float64")
    idx = int(np.clip(np.searchsorted(edges_arr, sp, side="right") - 1, 0, n_buckets - 1))

    p = prob_up[idx] if idx < len(prob_up) else None
    r = expected_ret[idx] if idx < len(expected_ret) else None
    p_out = float(p) if (p is not None and np.isfinite(p)) else gp
    r_out = float(r) if (r is not None and np.isfinite(r)) else gr
    return p_out, r_out


# ---------------------------------------------------------------------------
# cs_metrics
# ---------------------------------------------------------------------------

def _pearson(a: np.ndarray, b: np.ndarray):
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 2:
        return None
    a, b = a[mask], b[mask]
    if np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _spearman(a: np.ndarray, b: np.ndarray):
    """Spearman corr = Pearson corr of the ranks (scipy-free)."""
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 2:
        return None
    ra = pd.Series(a[mask]).rank().to_numpy()
    rb = pd.Series(b[mask]).rank().to_numpy()
    if np.std(ra) == 0 or np.std(rb) == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def _mean_or_none(values: list):
    vals = [v for v in values if v is not None and np.isfinite(v)]
    if not vals:
        return None
    return float(np.mean(vals))


def cs_metrics(oos_predictions, top_n: int = 8) -> dict:
    """Per-date ranking metrics averaged across dates (all NaN-safe).

    For top-N stats on a small date, the effective N is ``min(top_n, n // 2)``
    so a thin cross-section still yields a long/short split. Dates with < 2 rows
    are skipped for correlation; a metric that can never be computed is None.
    """
    top_n = max(1, int(top_n))
    base = {
        "daily_ic": None,
        "rank_ic": None,
        "precision_at_n": None,
        "hit_rate_at_n": None,
        "top_bottom_spread": None,
        "topn_realized_return": None,
        "turnover": None,
        "top_n": top_n,
    }
    if oos_predictions is None or len(oos_predictions) == 0:
        return base

    df = oos_predictions.copy()
    df["raw_score"] = pd.to_numeric(df["raw_score"], errors="coerce")
    df["fwd_return"] = pd.to_numeric(df.get("fwd_return"), errors="coerce")

    ics, rank_ics = [], []
    precisions, hit_rates = [], []
    spreads, realized = [], []
    prev_top: set | None = None
    turnovers: list[float] = []

    for _date, grp in df.groupby("date", sort=True):
        grp = grp.dropna(subset=["raw_score"])
        n = len(grp)
        if n < 2:
            prev_top = None  # break turnover continuity across an unusable date
            continue

        score = grp["raw_score"].to_numpy(dtype="float64")
        ret = grp["fwd_return"].to_numpy(dtype="float64")

        ics.append(_pearson(score, ret))
        rank_ics.append(_spearman(score, ret))

        eff_n = min(top_n, n // 2)
        if eff_n < 1:
            eff_n = 1
        order = np.argsort(-score, kind="mergesort")  # descending, stable
        top_idx = order[:eff_n]
        bottom_idx = order[-eff_n:]

        top_ret = ret[top_idx]
        bottom_ret = ret[bottom_idx]

        if np.isfinite(top_ret).any():
            precisions.append(float(np.mean(top_ret[np.isfinite(top_ret)] > 0)))
            med = np.nanmedian(ret)
            if np.isfinite(med):
                hit_rates.append(float(np.mean(top_ret[np.isfinite(top_ret)] > med)))
            realized.append(float(np.nanmean(top_ret)))
        if np.isfinite(top_ret).any() and np.isfinite(bottom_ret).any():
            spreads.append(float(np.nanmean(top_ret) - np.nanmean(bottom_ret)))

        # Turnover: fraction of the top set that changed vs the previous date.
        top_tickers = set(grp.iloc[top_idx]["ticker"].tolist())
        if prev_top is not None and len(top_tickers) > 0:
            inter = len(top_tickers & prev_top)
            turnovers.append(1.0 - inter / float(len(top_tickers)))
        prev_top = top_tickers

    base["daily_ic"] = _mean_or_none(ics)
    base["rank_ic"] = _mean_or_none(rank_ics)
    base["precision_at_n"] = _mean_or_none(precisions)
    base["hit_rate_at_n"] = _mean_or_none(hit_rates)
    base["top_bottom_spread"] = _mean_or_none(spreads)
    base["topn_realized_return"] = _mean_or_none(realized)
    base["turnover"] = _mean_or_none(turnovers) if turnovers else None
    return base
