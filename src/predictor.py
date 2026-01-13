def generate_signal(df, prob_up, ticker_info):
    """
    Generate BUY/SELL/HOLD signal based on probability and rules.
    """
    latest = df.iloc[-1]
    close_price = latest['close']
    volatility = latest['volatility']
    
    # Thresholds
    BUY_THRESHOLD = 0.62
    SELL_THRESHOLD = 0.38
    
    # Volatility threshold (e.g., avoid trading if daily vol > 3%)
    VOLATILITY_LIMIT = 0.03
    
    signal = {
        "ticker": ticker_info['code'],
        "name": ticker_info['name'],
        "date": latest['date'].strftime('%Y-%m-%d'),
        "close": close_price,
        "prob_up": prob_up,
        "action": "HOLD",
        "reason": "",
        "limit_price": None,
        "stop_loss": None
    }
    
    # Logic
    if prob_up >= BUY_THRESHOLD:
        if volatility <= VOLATILITY_LIMIT:
            signal["action"] = "BUY"
            signal["limit_price"] = int(close_price * (1 - 0.005)) # -0.5%
            signal["stop_loss"] = int(close_price * (1 - 0.02))   # -2.0%
            signal["reason"] = f"High prob ({prob_up:.2f}) & Low Vol ({volatility:.1%})"
        else:
            signal["reason"] = f"High prob ({prob_up:.2f}) but High Vol ({volatility:.1%})"
            
    elif prob_up <= SELL_THRESHOLD:
        signal["action"] = "SELL"
        signal["limit_price"] = int(close_price * (1 + 0.005)) # +0.5%
        signal["reason"] = f"Low prob ({prob_up:.2f})"
    
    else:
        signal["reason"] = f"Neutral probability ({prob_up:.2f})"
        
    return signal
