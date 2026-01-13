import sys
from src.config import TICKERS
from src.data_loader import update_data, load_data
from src.model import add_features, train_and_predict
from src.predictor import generate_signal
from src.notifier import send_notification
from src.dashboard import update_dashboard

def main():
    print("Starting daily stock prediction job...")
    
    signals = []
    
    for ticker_info in TICKERS:
        code = ticker_info['code']
        print(f"\nProcessing {code} ({ticker_info['name']})...")
        
        # 1. Update Data
        # In B-unyo, we run at 06:00 JST, so we should have data up to yesterday.
        # Stooq usually updates around midnight UTC or later?
        # Actually Stooq data for JP market closes at 15:00 JST, available shortly after.
        # 06:00 JST next day is safe.
        update_data(code)
        
        # 2. Load Data
        df = load_data(code)
        if df is None or len(df) < 60: # Need 60 for MA60
            print(f"Insufficient data for {code}. Skipping.")
            continue
            
        # 3. Feature Engineering
        df = add_features(df)
        if df.empty:
            print(f"Data empty after feature engineering for {code}. Skipping.")
            continue
            
        # 4. Train & Predict
        model, prob_up = train_and_predict(df)
        if model is None:
            print(f"Model training failed for {code}. Skipping.")
            continue
            
        print(f"Prediction for {code}: Up Probability = {prob_up:.2%}")
        
        # 5. Generate Signal
        signal = generate_signal(df, prob_up, ticker_info)
        signals.append(signal)
        
        # 6. Notify
        send_notification(signal)
        
    # 7. Update Dashboard
    if signals:
        update_dashboard(signals)
    else:
        print("No signals generated to update dashboard.")
        
    print("\nDaily job completed.")

if __name__ == "__main__":
    main()
