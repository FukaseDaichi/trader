import pandas as pd
import numpy as np
import lightgbm as lgb
from datetime import timedelta


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
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


# ---------------------------------------------------------------------------
# Feature Engineering
# ---------------------------------------------------------------------------

def add_features(df, dropna=True):
    """
    Add a comprehensive set of technical indicators as features.
    """
    df = df.copy()
    df = df.sort_values('date').reset_index(drop=True)

    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']

    # --- Price Returns (multi-horizon) ---
    for d in [1, 2, 3, 5, 10, 20]:
        df[f'return_{d}d'] = close.pct_change(d)

    # --- Moving Averages ---
    for w in [5, 10, 20, 60]:
        col = f'ma_{w}'
        df[col] = close.rolling(window=w).mean()
        df[f'div_{col}'] = (close - df[col]) / df[col]

    # --- MA cross signals ---
    df['ma_5_20_cross'] = df['ma_5'] / df['ma_20'] - 1     # golden/dead cross proximity
    df['ma_20_60_cross'] = df['ma_20'] / df['ma_60'] - 1

    # --- RSI ---
    df['rsi'] = calculate_rsi(close, 14)
    df['rsi_change'] = df['rsi'].diff()

    # --- MACD ---
    macd_line, macd_signal, macd_hist = calculate_macd(close)
    df['macd'] = macd_line
    df['macd_signal'] = macd_signal
    df['macd_hist'] = macd_hist
    df['macd_hist_change'] = macd_hist.diff()  # momentum of momentum

    # --- Bollinger Bands ---
    df['bb_pct_b'], df['bb_bandwidth'] = calculate_bollinger_bands(close)

    # --- ATR (volatility) ---
    df['atr'] = calculate_atr(high, low, close, 14)
    df['atr_pct'] = df['atr'] / close  # ATR as % of price

    # --- Volatility (rolling std of returns) ---
    df['volatility'] = df['return_1d'].rolling(window=20).std()

    # --- Volume features ---
    df['vol_change'] = volume.pct_change()
    df['vol_ma_5'] = volume.rolling(window=5).mean()
    df['vol_ma_20'] = volume.rolling(window=20).mean()
    df['vol_ratio'] = df['vol_ma_5'] / df['vol_ma_20']  # short-term volume surge

    # --- Candlestick features ---
    body = close - df['open']
    candle_range = high - low
    df['candle_body_pct'] = body / candle_range.replace(0, np.nan)  # body vs range
    df['upper_shadow_pct'] = (high - pd.concat([close, df['open']], axis=1).max(axis=1)) / candle_range.replace(0, np.nan)
    df['lower_shadow_pct'] = (pd.concat([close, df['open']], axis=1).min(axis=1) - low) / candle_range.replace(0, np.nan)

    # --- Calendar features ---
    df['day_of_week'] = df['date'].dt.dayofweek          # Mon=0 ... Fri=4
    df['month'] = df['date'].dt.month
    df['is_month_end'] = df['date'].dt.is_month_end.astype(int)
    df['is_month_start'] = (df['date'].dt.day <= 3).astype(int)

    # --- Streak: consecutive up/down days ---
    up = (df['return_1d'] > 0).astype(int)
    streak = up.copy()
    for i in range(1, len(streak)):
        if up.iloc[i] == up.iloc[i - 1]:
            streak.iloc[i] = streak.iloc[i - 1] + 1
        else:
            streak.iloc[i] = 1
    df['streak'] = streak * up.replace(0, -1)  # positive = consecutive ups

    # --- Gap (overnight gap) ---
    df['gap'] = df['open'] / close.shift(1) - 1

    # --- High / Low position ---
    df['high_20d'] = high.rolling(window=20).max()
    df['low_20d'] = low.rolling(window=20).min()
    df['price_position_20d'] = (close - df['low_20d']) / (df['high_20d'] - df['low_20d']).replace(0, np.nan)

    if dropna:
        df = df.dropna().reset_index(drop=True)

    return df


# ---------------------------------------------------------------------------
# Feature columns used for training
# ---------------------------------------------------------------------------

FEATURE_COLS = [
    # Returns
    'return_1d', 'return_2d', 'return_3d', 'return_5d', 'return_10d', 'return_20d',
    # MA divergence
    'div_ma_5', 'div_ma_10', 'div_ma_20', 'div_ma_60',
    # MA cross
    'ma_5_20_cross', 'ma_20_60_cross',
    # RSI
    'rsi', 'rsi_change',
    # MACD
    'macd', 'macd_signal', 'macd_hist', 'macd_hist_change',
    # Bollinger
    'bb_pct_b', 'bb_bandwidth',
    # Volatility
    'atr_pct', 'volatility',
    # Volume
    'vol_change', 'vol_ratio',
    # Candlestick
    'candle_body_pct', 'upper_shadow_pct', 'lower_shadow_pct',
    # Calendar
    'day_of_week', 'month', 'is_month_end', 'is_month_start',
    # Streak / Gap / Position
    'streak', 'gap', 'price_position_20d',
]


# ---------------------------------------------------------------------------
# Training & Prediction
# ---------------------------------------------------------------------------

_LGB_PARAMS = {
    'objective': 'binary',
    'metric': 'binary_logloss',
    'boosting_type': 'gbdt',
    'learning_rate': 0.03,
    'num_leaves': 15,
    'max_depth': 4,
    'min_child_samples': 30,
    'feature_fraction': 0.6,
    'bagging_fraction': 0.7,
    'bagging_freq': 5,
    'lambda_l1': 0.5,
    'lambda_l2': 2.0,
    'min_data_in_bin': 5,
    'verbosity': -1,
    'seed': 42,
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
    params = {**_LGB_PARAMS, 'seed': seed}
    train_data = lgb.Dataset(X_train, label=y_train)
    val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)

    callbacks = [
        lgb.early_stopping(stopping_rounds=30, min_delta=1e-5),
        lgb.log_evaluation(period=0),
    ]

    model = lgb.train(
        params, train_data, num_boost_round=500,
        valid_sets=[val_data], valid_names=['val'],
        callbacks=callbacks,
    )

    # Guard: if early stopping fired before MIN rounds, retrain with fixed rounds
    if model.best_iteration < _MIN_BOOST_ROUND:
        model = lgb.train(
            params, train_data, num_boost_round=_MIN_BOOST_ROUND,
            valid_sets=[val_data], valid_names=['val'],
            callbacks=[lgb.log_evaluation(period=0)],
        )

    return model


def train_and_predict(df, runtime_config=None):
    """
    Train a LightGBM ensemble via walk-forward cross-validation and predict
    whether tomorrow's close > today's close.

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
    purge_gap = _config_int(config, "purge_gap", 5, minimum=0)
    n_folds = _config_int(config, "n_folds", 3, minimum=1)
    min_train_rows = _config_int(config, "train_min_rows", 200, minimum=50)

    # Target: did price go up NEXT day?
    df['target'] = (df['close'].shift(-1) > df['close']).astype(int)

    # Drop the last row (target unknown)
    labelled = df.dropna(subset=['target']).copy()

    # Use recent history window (default: last 4 years)
    max_date = labelled['date'].max()
    start_date = max_date - timedelta(days=365 * validation_years)
    labelled = labelled[labelled['date'] >= start_date].reset_index(drop=True)

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
            train_fold[FEATURE_COLS], train_fold['target'],
            val_fold[FEATURE_COLS], val_fold['target'],
            seed=42 + fold_idx,
        )
        fold_predictions.append(model_fold.predict(latest_row)[0])

    # ------------------------------------------------------------------
    # Final model trained on ALL labelled data (uses most recent val
    # split purely for early stopping, then prediction comes from the
    # ensemble average for stability).
    # ------------------------------------------------------------------
    train_all = labelled.iloc[:-val_size]
    val_all = labelled.iloc[-val_size:]
    if train_all.empty or val_all.empty or len(train_all) < min_train_rows:
        print("Not enough data to train final model.")
        return None, 0.5

    final_model = _train_single_fold(
        train_all[FEATURE_COLS], train_all['target'],
        val_all[FEATURE_COLS], val_all['target'],
        seed=42,
    )
    fold_predictions.append(final_model.predict(latest_row)[0])

    # Ensemble: simple average of all fold predictions
    prob_up = float(np.mean(fold_predictions))

    return final_model, prob_up
