def generate_signal(df, prob_up, ticker_info):
    """
    Generate a 5-level signal based on the predicted probability of price increase.

    Levels (designed so HOLD is the most common outcome):
        BUY      - Very strong upside conviction   (prob_up >= 80%)
        MILD_BUY - Moderate upside lean             (65% <= prob_up < 80%)
        HOLD     - Insufficient conviction either way (25% < prob_up < 65%)
        MILD_SELL- Moderate downside lean            (10% <= prob_up <= 25%)
        SELL     - Very strong downside conviction   (prob_up < 10%)

    Additional rule: BUY is downgraded to MILD_BUY when volatility is high.
    """
    latest = df.iloc[-1]
    close_price = latest['close']
    volatility = latest['volatility']

    # --- Thresholds (calibrated from historical prediction distribution) ---
    BUY_THRESHOLD = 0.80       # ~P80  — top 20% conviction
    MILD_BUY_THRESHOLD = 0.65  # ~P55  — moderate positive lean
    MILD_SELL_THRESHOLD = 0.25 # ~P25  — moderate negative lean
    SELL_THRESHOLD = 0.10      # ~P10  — bottom 10% conviction

    VOLATILITY_LIMIT = 0.04    # 4% daily vol — avoid strong BUY in wild markets

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

    # --- Decision logic ---
    if prob_up >= BUY_THRESHOLD:
        if volatility <= VOLATILITY_LIMIT:
            signal["action"] = "BUY"
            signal["limit_price"] = int(close_price * (1 - 0.005))
            signal["stop_loss"] = int(close_price * (1 - 0.02))
            signal["reason"] = f"強い上昇シグナル (上昇確率 {prob_up:.0%})・ボラティリティ低 ({volatility:.1%})"
        else:
            # Downgrade to MILD_BUY due to high volatility
            signal["action"] = "MILD_BUY"
            signal["reason"] = f"上昇シグナルだがボラティリティ高 ({volatility:.1%})・様子見推奨 (上昇確率 {prob_up:.0%})"

    elif prob_up >= MILD_BUY_THRESHOLD:
        signal["action"] = "MILD_BUY"
        signal["reason"] = f"やや上昇傾向 (上昇確率 {prob_up:.0%})"

    elif prob_up <= SELL_THRESHOLD:
        signal["action"] = "SELL"
        signal["limit_price"] = int(close_price * (1 + 0.005))
        signal["reason"] = f"強い下落シグナル (上昇確率 {prob_up:.0%})"

    elif prob_up <= MILD_SELL_THRESHOLD:
        signal["action"] = "MILD_SELL"
        signal["reason"] = f"やや下落傾向 (上昇確率 {prob_up:.0%})"

    else:
        signal["action"] = "HOLD"
        signal["reason"] = f"判断材料不足 (上昇確率 {prob_up:.0%})"

    return signal
