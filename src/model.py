import pandas as pd
import numpy as np
import lightgbm as lgb
from datetime import timedelta

from .config import get_label_config
from .labels import build_labelled_frame, effective_horizon
from .macro import MACRO_FEATURE_COLS, add_macro_features


# ---------------------------------------------------------------------------
# Technical Indicators
# ---------------------------------------------------------------------------


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_bollinger_bands(series, period=20, num_std=2):
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = sma + num_std * std
    lower = sma - num_std * std
    # Percent B: where is price relative to the bands (0 = lower, 1 = upper)
    pct_b = (series - lower) / (upper - lower)
    # Bandwidth: how wide are the bands relative to the SMA
    bandwidth = (upper - lower) / sma
    return pct_b, bandwidth


def calculate_atr(high, low, close, period=14):
    """Average True Range — a volatility measure."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(window=period).mean()


# ---------------------------------------------------------------------------
# Feature Engineering
# ---------------------------------------------------------------------------


def add_features(df, dropna=True):
    """
    Add a comprehensive set of technical indicators as features.
    """
    df = df.copy()
    df = df.sort_values("date").reset_index(drop=True)

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # --- Price Returns (multi-horizon) ---
    for d in [1, 2, 3, 5, 10, 20]:
        df[f"return_{d}d"] = close.pct_change(d)

    # --- Moving Averages ---
    for w in [5, 10, 20, 60]:
        col = f"ma_{w}"
        df[col] = close.rolling(window=w).mean()
        df[f"div_{col}"] = (close - df[col]) / df[col]

    # --- MA cross signals ---
    df["ma_5_20_cross"] = df["ma_5"] / df["ma_20"] - 1  # golden/dead cross proximity
    df["ma_20_60_cross"] = df["ma_20"] / df["ma_60"] - 1

    # --- RSI ---
    df["rsi"] = calculate_rsi(close, 14)
    df["rsi_change"] = df["rsi"].diff()

    # --- MACD ---
    macd_line, macd_signal, macd_hist = calculate_macd(close)
    df["macd"] = macd_line
    df["macd_signal"] = macd_signal
    df["macd_hist"] = macd_hist
    df["macd_hist_change"] = macd_hist.diff()  # momentum of momentum

    # --- Bollinger Bands ---
    df["bb_pct_b"], df["bb_bandwidth"] = calculate_bollinger_bands(close)

    # --- ATR (volatility) ---
    df["atr"] = calculate_atr(high, low, close, 14)
    df["atr_pct"] = df["atr"] / close  # ATR as % of price

    # --- Volatility (rolling std of returns) ---
    df["volatility"] = df["return_1d"].rolling(window=20).std()

    # --- Volume features ---
    df["vol_change"] = volume.pct_change()
    df["vol_ma_5"] = volume.rolling(window=5).mean()
    df["vol_ma_20"] = volume.rolling(window=20).mean()
    df["vol_ratio"] = df["vol_ma_5"] / df["vol_ma_20"]  # short-term volume surge

    # --- Candlestick features ---
    body = close - df["open"]
    candle_range = high - low
    df["candle_body_pct"] = body / candle_range.replace(0, np.nan)  # body vs range
    df["upper_shadow_pct"] = (
        high - pd.concat([close, df["open"]], axis=1).max(axis=1)
    ) / candle_range.replace(0, np.nan)
    df["lower_shadow_pct"] = (
        pd.concat([close, df["open"]], axis=1).min(axis=1) - low
    ) / candle_range.replace(0, np.nan)

    # --- Calendar features ---
    df["day_of_week"] = df["date"].dt.dayofweek  # Mon=0 ... Fri=4
    df["month"] = df["date"].dt.month
    df["is_month_end"] = df["date"].dt.is_month_end.astype(int)
    df["is_month_start"] = (df["date"].dt.day <= 3).astype(int)

    # --- Streak: consecutive up/down days ---
    up = (df["return_1d"] > 0).astype(int)
    streak = up.copy()
    for i in range(1, len(streak)):
        if up.iloc[i] == up.iloc[i - 1]:
            streak.iloc[i] = streak.iloc[i - 1] + 1
        else:
            streak.iloc[i] = 1
    df["streak"] = streak * up.replace(0, -1)  # positive = consecutive ups

    # --- Gap (overnight gap) ---
    df["gap"] = df["open"] / close.shift(1) - 1

    # --- High / Low position ---
    df["high_20d"] = high.rolling(window=20).max()
    df["low_20d"] = low.rolling(window=20).min()
    df["price_position_20d"] = (close - df["low_20d"]) / (
        df["high_20d"] - df["low_20d"]
    ).replace(0, np.nan)

    if dropna:
        df = df.dropna().reset_index(drop=True)

    return df


# ---------------------------------------------------------------------------
# Feature columns used for training
# ---------------------------------------------------------------------------

FEATURE_COLS = [
    # Returns
    "return_1d",
    "return_2d",
    "return_3d",
    "return_5d",
    "return_10d",
    "return_20d",
    # MA divergence
    "div_ma_5",
    "div_ma_10",
    "div_ma_20",
    "div_ma_60",
    # MA cross
    "ma_5_20_cross",
    "ma_20_60_cross",
    # RSI
    "rsi",
    "rsi_change",
    # MACD
    "macd",
    "macd_signal",
    "macd_hist",
    "macd_hist_change",
    # Bollinger
    "bb_pct_b",
    "bb_bandwidth",
    # Volatility
    "atr_pct",
    "volatility",
    # Volume
    "vol_change",
    "vol_ratio",
    # Candlestick
    "candle_body_pct",
    "upper_shadow_pct",
    "lower_shadow_pct",
    # Calendar
    "day_of_week",
    "month",
    "is_month_end",
    "is_month_start",
    # Streak / Gap / Position
    "streak",
    "gap",
    "price_position_20d",
]


# ---------------------------------------------------------------------------
# Training & Prediction
# ---------------------------------------------------------------------------

_LGB_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
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
    "seed": 42,
}

# Minimum boosting rounds before early stopping takes effect
_MIN_BOOST_ROUND = 50


def _config_int(config, key, default, minimum=0):
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        value = int(default)
    return max(minimum, value)


def _train_single_fold(X_train, y_train, X_val, y_val, seed=42):
    """Train a single LightGBM model with early stopping + minimum round guard."""
    params = {**_LGB_PARAMS, "seed": seed}
    train_data = lgb.Dataset(X_train, label=y_train)
    val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)

    callbacks = [
        lgb.early_stopping(stopping_rounds=30, min_delta=1e-5),
        lgb.log_evaluation(period=0),
    ]

    model = lgb.train(
        params,
        train_data,
        num_boost_round=500,
        valid_sets=[val_data],
        valid_names=["val"],
        callbacks=callbacks,
    )

    # Guard: if early stopping fired before MIN rounds, retrain with fixed rounds
    if model.best_iteration < _MIN_BOOST_ROUND:
        model = lgb.train(
            params,
            train_data,
            num_boost_round=_MIN_BOOST_ROUND,
            valid_sets=[val_data],
            valid_names=["val"],
            callbacks=[lgb.log_evaluation(period=0)],
        )

    return model


def _refit_fixed_rounds(X_train, y_train, *, seed=42, num_boost_round=50):
    """Refit on all permitted rows after internal early-stopping selection."""
    params = {**_LGB_PARAMS, "seed": seed}
    train_data = lgb.Dataset(X_train, label=y_train)
    return lgb.train(
        params,
        train_data,
        num_boost_round=max(_MIN_BOOST_ROUND, int(num_boost_round)),
        callbacks=[lgb.log_evaluation(period=0)],
    )


def train_and_predict(df, runtime_config=None, label_config=None):
    """
    Train a LightGBM ensemble via walk-forward cross-validation and predict
    the probability that price rises over the configured horizon.

    Phase 1: the target is built by src.labels (default 5-day triple-barrier).
    Set TRADER_LABEL_MODE=binary_1d to reproduce the legacy next-day target.

    Strategy
    --------
    1. **Walk-forward CV**: Train on expanding windows, validate on
       successive blocks. This mirrors real deployment (train on past,
       predict future) and avoids look-ahead bias.
    2. **Purge gap**: Gap between train and validation prevents label
       leakage from overlapping return windows.
    3. **Ensemble**: Average predictions from fold-models + 1 full-data
       model for the final probability. This reduces variance and provides
       more stable signals.
    4. **Regularised LightGBM**: Shallow trees (depth 4, 15 leaves), strong
       L1/L2, aggressive sub-sampling to combat the low signal-to-noise
       ratio inherent in daily stock returns.
    5. **35 features**: Multi-horizon returns, MACD, Bollinger Bands, ATR,
       RSI, candlestick patterns, calendar effects, streaks, overnight gaps.
    """

    df = df.copy()
    config = runtime_config or {}

    validation_years = _config_int(config, "validation_years", 4, minimum=1)
    val_size = _config_int(config, "val_size", 60, minimum=1)
    n_folds = _config_int(config, "n_folds", 3, minimum=1)
    min_train_rows = _config_int(config, "train_min_rows", 200, minimum=50)

    # Phase 1: horizon-aware label via src.labels (binary_1d == legacy next-day).
    label_cfg = label_config or get_label_config()
    purge_gap = resolve_purge_gap(
        config, effective_horizon_days=effective_horizon(label_cfg)
    )
    labelled = build_labelled_frame(df, label_cfg)
    if labelled.empty:
        print("No labelled rows after target construction.")
        return None, 0.5

    # Use recent history window (default: last 4 years)
    max_date = labelled["date"].max()
    start_date = max_date - timedelta(days=365 * validation_years)
    labelled = labelled[labelled["date"] >= start_date].reset_index(drop=True)

    min_required = min_train_rows + val_size + purge_gap
    if len(labelled) < min_required:
        print("Not enough data to train model.")
        return None, 0.5

    # ------------------------------------------------------------------
    # Walk-forward ensemble (3 folds)
    # ------------------------------------------------------------------
    latest_row = df.iloc[[-1]][FEATURE_COLS]
    fold_predictions = []

    n = len(labelled)

    for fold_idx in range(n_folds):
        val_end = n - fold_idx * val_size
        val_start = val_end - val_size
        train_end = val_start - purge_gap

        if val_start < 0:
            break
        if train_end < min_train_rows:
            continue

        train_fold = labelled.iloc[:train_end]
        val_fold = labelled.iloc[val_start:val_end]
        if val_fold.empty:
            continue

        model_fold = _train_single_fold(
            train_fold[FEATURE_COLS],
            train_fold["target_class"],
            val_fold[FEATURE_COLS],
            val_fold["target_class"],
            seed=42 + fold_idx,
        )
        fold_predictions.append(model_fold.predict(latest_row)[0])

    # ------------------------------------------------------------------
    # Final model trained on ALL labelled data (uses most recent val
    # split purely for early stopping, then prediction comes from the
    # ensemble average for stability).
    # ------------------------------------------------------------------
    final_train_end = len(labelled) - val_size - purge_gap
    train_all = labelled.iloc[:final_train_end]
    val_all = labelled.iloc[-val_size:]
    if train_all.empty or val_all.empty or len(train_all) < min_train_rows:
        print("Not enough data to train final model.")
        return None, 0.5

    final_model = _train_single_fold(
        train_all[FEATURE_COLS],
        train_all["target_class"],
        val_all[FEATURE_COLS],
        val_all["target_class"],
        seed=42,
    )
    fold_predictions.append(final_model.predict(latest_row)[0])

    # Ensemble: simple average of all fold predictions
    prob_up = float(np.mean(fold_predictions))

    return final_model, prob_up


# ---------------------------------------------------------------------------
# Phase 1: persisted-model training & inference (W3)
# ---------------------------------------------------------------------------


def resolve_purge_gap(runtime_config=None, *, effective_horizon_days: int = 1) -> int:
    """Enforce a leakage-safe purge at least as long as the label horizon."""
    config = runtime_config or {}
    configured = _config_int(config, "purge_gap", 5, minimum=0)
    horizon = max(1, int(effective_horizon_days))
    return max(configured, horizon)


def train_with_purged_internal_validation(
    labelled,
    feature_cols,
    *,
    train_pool_end: int,
    runtime_config=None,
    effective_horizon_days: int = 1,
    seed: int = 42,
):
    """Train without exposing an external OOS window to early stopping.

    ``train_pool_end`` is the exclusive end of data that may be used by this
    model.  The helper takes its early-stopping validation block from the tail
    of that pool and inserts a horizon-aware purge between the internal train
    and validation blocks.  Callers can therefore keep their external tuning
    or holdout block completely unseen until prediction time.

    Returns ``(booster_or_none, split_info)``.  The integer offsets in
    ``split_info`` make the no-overlap property directly auditable in tests and
    gate evidence.
    """
    config = runtime_config or {}
    val_size = _config_int(config, "val_size", 60, minimum=1)
    min_train_rows = _config_int(config, "train_min_rows", 200, minimum=50)
    purge_gap = resolve_purge_gap(config, effective_horizon_days=effective_horizon_days)
    pool_end = min(max(0, int(train_pool_end)), len(labelled))
    internal_val_end = pool_end
    internal_val_start = internal_val_end - val_size
    internal_train_end = internal_val_start - purge_gap
    info = {
        "train_pool_end": pool_end,
        "internal_train_start": 0,
        "internal_train_end": max(0, internal_train_end),
        "internal_validation_start": max(0, internal_val_start),
        "internal_validation_end": max(0, internal_val_end),
        "internal_validation_rows": max(0, internal_val_end - internal_val_start),
        "purge_gap": purge_gap,
        "external_oos_used_for_training": False,
    }
    if internal_train_end < min_train_rows or internal_val_start < 0:
        return None, {**info, "reason": "insufficient_internal_training_rows"}

    train_fold = labelled.iloc[:internal_train_end]
    internal_val = labelled.iloc[internal_val_start:internal_val_end]
    if train_fold.empty or internal_val.empty:
        return None, {**info, "reason": "empty_internal_training_split"}

    selector = _train_single_fold(
        train_fold[feature_cols],
        train_fold["target_class"],
        internal_val[feature_cols],
        internal_val["target_class"],
        seed=seed,
    )
    selected_rounds = getattr(selector, "best_iteration", 0) or 0
    if selected_rounds <= 0:
        current_iteration = getattr(selector, "current_iteration", None)
        selected_rounds = current_iteration() if callable(current_iteration) else 0
    selected_rounds = max(_MIN_BOOST_ROUND, int(selected_rounds or 0))

    # The internal validation chooses only the number of rounds.  Refit the
    # returned candidate on every row permitted by train_pool_end, including
    # the internal validation rows, while the external OOS remains untouched.
    refit_pool = labelled.iloc[:pool_end]
    booster = _refit_fixed_rounds(
        refit_pool[feature_cols],
        refit_pool["target_class"],
        seed=seed,
        num_boost_round=selected_rounds,
    )
    return booster, {
        **info,
        "reason": "ok",
        "selected_boost_rounds": selected_rounds,
        "refit_train_start": 0,
        "refit_train_end": pool_end,
        "refit_train_rows": int(len(refit_pool)),
        "refit_uses_full_permitted_pool": True,
    }


def phase1_training_min_rows(runtime_config=None, *, effective_horizon_days: int = 1):
    """Minimum rows for isolated tuning, holdout and internal validation."""
    config = runtime_config or {}
    val_size = _config_int(config, "val_size", 60, minimum=1)
    n_folds = _config_int(config, "n_folds", 3, minimum=1)
    min_train_rows = _config_int(config, "train_min_rows", 200, minimum=50)
    purge_gap = resolve_purge_gap(config, effective_horizon_days=effective_horizon_days)
    horizon = max(1, int(effective_horizon_days))
    tuning_rows = val_size * max(1, n_folds - 1)
    return (
        min_train_rows
        + purge_gap  # internal train -> internal validation
        + val_size  # internal early-stopping validation
        + purge_gap  # tuning model -> external tuning OOS
        + tuning_rows
        + horizon  # tuning -> holdout embargo
        + val_size  # final gate holdout
    )


def train_horizon_models(
    labelled,
    feature_cols,
    runtime_config=None,
    *,
    effective_horizon_days: int = 1,
):
    """
    Train a deployable candidate plus chronologically isolated OOS predictions.

    The returned final booster is the exact booster used for the final holdout
    predictions and later persisted for daily inference.  It is trained only on
    rows before the holdout (with a purge), and its early-stopping validation is
    further isolated inside that training pool.  A separate earlier model
    generates the tuning scores used to fit calibration and thresholds.  The H
    rows between tuning and holdout are returned as explicit embargo rows and
    are never scored by downstream KPI logic.

    No tuning or holdout label is passed to either model's early-stopping
    validation.  ``folds`` is intentionally empty: persisting the earlier
    tuning model in the inference ensemble would change the probability space
    relative to the holdout evidence.

    `labelled` must already carry `target_class`, `fwd_return`, and `date`
    (see src.labels.build_labelled_frame). `feature_cols` lets the caller train
    on technical + macro features without touching the legacy FEATURE_COLS list.
    """
    config = runtime_config or {}
    val_size = _config_int(config, "val_size", 60, minimum=1)
    purge_gap = resolve_purge_gap(config, effective_horizon_days=effective_horizon_days)
    n_folds = _config_int(config, "n_folds", 3, minimum=1)
    horizon = max(1, int(effective_horizon_days))
    tuning_rows = val_size * max(1, n_folds - 1)
    n = len(labelled)

    if n_folds < 2:
        empty = pd.DataFrame(
            columns=["date", "fwd_return", "target_class", "raw_score", "oos_role"]
        )
        empty.attrs["deployment_split"] = {
            "reason": "at_least_two_folds_required_for_tuning_holdout",
            "actual_n_folds": n_folds,
            "external_oos_used_for_early_stopping": False,
        }
        return [], None, empty

    holdout_end = n
    holdout_start = holdout_end - val_size
    tuning_end = holdout_start - horizon
    tuning_start = tuning_end - tuning_rows
    tuning_train_pool_end = tuning_start - purge_gap
    deployment_train_pool_end = holdout_start - purge_gap

    empty = pd.DataFrame(
        columns=["date", "fwd_return", "target_class", "raw_score", "oos_role"]
    )
    if tuning_start < 0 or tuning_train_pool_end <= 0:
        empty.attrs["deployment_split"] = {
            "reason": "insufficient_rows_for_external_oos",
            "required_rows": phase1_training_min_rows(
                config, effective_horizon_days=horizon
            ),
            "actual_rows": n,
        }
        return [], None, empty

    tuning_model, tuning_internal = train_with_purged_internal_validation(
        labelled,
        feature_cols,
        train_pool_end=tuning_train_pool_end,
        runtime_config=config,
        effective_horizon_days=horizon,
        seed=43,
    )
    final_model, deployment_internal = train_with_purged_internal_validation(
        labelled,
        feature_cols,
        train_pool_end=deployment_train_pool_end,
        runtime_config=config,
        effective_horizon_days=horizon,
        seed=42,
    )
    if tuning_model is None or final_model is None:
        empty.attrs["deployment_split"] = {
            "reason": "internal_training_failed",
            "required_rows": phase1_training_min_rows(
                config, effective_horizon_days=horizon
            ),
            "actual_rows": n,
            "tuning_internal": tuning_internal,
            "deployment_internal": deployment_internal,
        }
        return [], None, empty

    # Keep all execution metadata produced by labels.py.  The backtest helpers
    # need the path columns to enforce the no-overlap execution contract.
    tuning_oos = labelled.iloc[tuning_start:tuning_end].copy()
    tuning_oos["raw_score"] = tuning_model.predict(tuning_oos[feature_cols])
    tuning_oos["oos_role"] = "calibration_threshold_tuning"

    embargo = labelled.iloc[tuning_end:holdout_start].copy()
    embargo["raw_score"] = np.nan
    embargo["oos_role"] = "embargo"

    holdout_oos = labelled.iloc[holdout_start:holdout_end].copy()
    holdout_oos["raw_score"] = final_model.predict(holdout_oos[feature_cols])
    holdout_oos["oos_role"] = "deployment_candidate_holdout"

    oos_df = pd.concat(
        [tuning_oos, embargo, holdout_oos], ignore_index=True
    ).reset_index(drop=True)
    oos_df.attrs["deployment_split"] = {
        "reason": "ok",
        "effective_horizon_days": horizon,
        "effective_purge_gap": purge_gap,
        "tuning_start_index": tuning_start,
        "tuning_end_index": tuning_end,
        "embargo_start_index": tuning_end,
        "embargo_end_index": holdout_start,
        "holdout_start_index": holdout_start,
        "holdout_end_index": holdout_end,
        "tuning_rows": int(len(tuning_oos)),
        "embargo_rows": int(len(embargo)),
        "holdout_rows": int(len(holdout_oos)),
        "tuning_internal": tuning_internal,
        "deployment_internal": deployment_internal,
        "holdout_model_is_persisted_final": True,
        "external_oos_used_for_early_stopping": False,
    }
    return [], final_model, oos_df


def predict_prob_with_bundle(bundle, feature_row):
    """
    Ensemble (folds + final) raw probability for one feature row.
    `feature_row` is a 1-row DataFrame aligned to the bundle's feature columns.
    Returns None when the bundle has no usable boosters.
    """
    preds = []
    for booster in bundle.get("folds", []) or []:
        preds.append(float(booster.predict(feature_row)[0]))
    final_model = bundle.get("final")
    if final_model is not None:
        preds.append(float(final_model.predict(feature_row)[0]))
    if not preds:
        return None
    return float(np.mean(preds))


# Phase 1 default feature set = legacy technical features + macro/regime features.
PHASE1_FEATURE_COLS = FEATURE_COLS + MACRO_FEATURE_COLS


def phase1_feature_cols(macro_enabled=True):
    """Return the Phase 1 feature schema for the current macro-feature setting."""
    if macro_enabled:
        return list(PHASE1_FEATURE_COLS)
    return list(FEATURE_COLS)


def build_feature_frame(
    df, macro_panel=None, ticker_info=None, dropna_features=True, macro_enabled=True
):
    """
    Technical (add_features) + macro (add_macro_features) feature frame.

    Technical features are NaN-dropped as usual; macro columns are joined with a
    backward as-of merge and may be NaN (missing series), which LightGBM tolerates.
    When macro_enabled is false, only legacy technical columns are emitted.
    """
    featured = add_features(df, dropna=dropna_features)
    if featured.empty:
        return featured
    if not macro_enabled:
        return featured
    return add_macro_features(featured, macro_panel, ticker_info)
