import pandas as pd
import numpy as np
import lightgbm as lgb
from datetime import timedelta

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def add_features(df, dropna=True):
    """
    Add technical indicators as features.
    """
    df = df.copy()
    
    # Sort by date
    df = df.sort_values('date').reset_index(drop=True)
    
    # Returns
    df['return_1d'] = df['close'].pct_change(1)
    df['return_5d'] = df['close'].pct_change(5)
    df['return_20d'] = df['close'].pct_change(20)
    
    # Moving Averages
    df['ma_5'] = df['close'].rolling(window=5).mean()
    df['ma_20'] = df['close'].rolling(window=20).mean()
    df['ma_60'] = df['close'].rolling(window=60).mean()
    
    # MA Divergence
    df['div_ma_5'] = (df['close'] - df['ma_5']) / df['ma_5']
    df['div_ma_20'] = (df['close'] - df['ma_20']) / df['ma_20']
    df['div_ma_60'] = (df['close'] - df['ma_60']) / df['ma_60']
    
    # RSI
    df['rsi'] = calculate_rsi(df['close'], 14)
    
    # Volatility (20 days rolling std of daily returns)
    df['volatility'] = df['return_1d'].rolling(window=20).std()
    
    # Volume Change
    df['vol_change'] = df['volume'].pct_change()
    
    # Drop NaN
    if dropna:
        df = df.dropna().reset_index(drop=True)
    
    return df

def train_and_predict(df):
    """
    Train LightGBM model using data up to yesterday, and predict for today (using today's features).
    Actually, we want to predict NEXT day movement based on CURRENT day data.
    
    Target: 1 if Close(T+1) > Close(T), else 0.
    """
    # Create target: Did price go up NEXT day?
    # We shift -1 to align T's features with T+1's price action result
    df['target'] = (df['close'].shift(-1) > df['close']).astype(int)
    
    # Features columns
    feature_cols = [
        'return_1d', 'return_5d', 'return_20d',
        'div_ma_5', 'div_ma_20', 'div_ma_60',
        'rsi', 'volatility', 'vol_change'
    ]
    
    # Filter valid data
    # Last row has NaN target because we don't know T+1 yet.
    # We use the previous rows for training.
    train_df = df.dropna(subset=['target'])
    
    # Use last 4 years for training if available
    max_date = train_df['date'].max()
    start_date = max_date - timedelta(days=365 * 4)
    train_df = train_df[train_df['date'] >= start_date]
    
    if len(train_df) < 100:
        print("Not enough data to train model.")
        return None, 0.5
    
    X = train_df[feature_cols]
    y = train_df['target']
    
    # LightGBM Dataset
    train_data = lgb.Dataset(X, label=y)
    
    # Parameters
    params = {
        'objective': 'binary',
        'metric': 'binary_logloss',
        'boosting_type': 'gbdt',
        'verbosity': -1,
        'seed': 42
    }
    
    # Train
    model = lgb.train(params, train_data, num_boost_round=100)
    
    # Predict for the LATEST available data point (Today/Yesterday close)
    # This row was dropped from train_df because target is NaN, but it has features.
    latest_row = df.iloc[[-1]][feature_cols]
    
    # Probability of class 1 (Up)
    prob_up = model.predict(latest_row)[0]
    
    return model, prob_up
