import math


DEFAULT_SIGNAL_THRESHOLDS = {
    "buy": 0.80,            # ~P80  — top 20% conviction
    "mild_buy": 0.65,       # ~P55  — moderate positive lean
    "mild_sell": 0.25,      # ~P25  — moderate negative lean
    "sell": 0.10,           # ~P10  — bottom 10% conviction
    "volatility_limit": 0.04,  # 4% daily vol — avoid strong BUY in wild markets
}


def resolve_thresholds(thresholds=None):
    """
    Return validated threshold dict by overlaying optional custom values
    on top of defaults.
    """
    resolved = dict(DEFAULT_SIGNAL_THRESHOLDS)
    if isinstance(thresholds, dict):
        for key in DEFAULT_SIGNAL_THRESHOLDS:
            if key in thresholds and thresholds[key] is not None:
                resolved[key] = float(thresholds[key])

    sell = resolved["sell"]
    mild_sell = resolved["mild_sell"]
    mild_buy = resolved["mild_buy"]
    buy = resolved["buy"]

    if not (0.0 <= sell <= 1.0):
        raise ValueError("thresholds.sell must be in [0, 1]")
    if not (0.0 <= mild_sell <= 1.0):
        raise ValueError("thresholds.mild_sell must be in [0, 1]")
    if not (0.0 <= mild_buy <= 1.0):
        raise ValueError("thresholds.mild_buy must be in [0, 1]")
    if not (0.0 <= buy <= 1.0):
        raise ValueError("thresholds.buy must be in [0, 1]")
    if not (sell < mild_sell < mild_buy < buy):
        raise ValueError("threshold ordering must satisfy sell < mild_sell < mild_buy < buy")
    if resolved["volatility_limit"] < 0.0:
        raise ValueError("thresholds.volatility_limit must be >= 0")

    return resolved


def _is_missing_or_nan(value):
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True


def action_from_probability(prob_up, volatility=None, thresholds=None):
    """
    Map model probability (+ optional volatility) to a discrete action.
    """
    t = resolve_thresholds(thresholds)
    if prob_up >= t["buy"]:
        if _is_missing_or_nan(volatility) or volatility <= t["volatility_limit"]:
            return "BUY"
        return "MILD_BUY"

    if prob_up >= t["mild_buy"]:
        return "MILD_BUY"

    if prob_up <= t["sell"]:
        return "SELL"

    if prob_up <= t["mild_sell"]:
        return "MILD_SELL"

    return "HOLD"


def generate_signal(df, prob_up, ticker_info, thresholds=None):
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
    t = resolve_thresholds(thresholds)
    action = action_from_probability(prob_up, volatility=volatility, thresholds=t)
    signal["action"] = action

    if action == "BUY":
        signal["limit_price"] = int(close_price * (1 - 0.005))
        signal["stop_loss"] = int(close_price * (1 - 0.02))
        signal["reason"] = f"強い上昇シグナル (上昇確率 {prob_up:.0%})・ボラティリティ低 ({volatility:.1%})"

    elif action == "MILD_BUY" and prob_up >= t["buy"]:
        signal["reason"] = f"上昇シグナルだがボラティリティ高 ({volatility:.1%})・様子見推奨 (上昇確率 {prob_up:.0%})"

    elif action == "MILD_BUY":
        signal["reason"] = f"やや上昇傾向 (上昇確率 {prob_up:.0%})"

    elif action == "SELL":
        signal["limit_price"] = int(close_price * (1 + 0.005))
        signal["reason"] = f"強い下落シグナル (上昇確率 {prob_up:.0%})"

    elif action == "MILD_SELL":
        signal["reason"] = f"やや下落傾向 (上昇確率 {prob_up:.0%})"

    else:
        signal["reason"] = f"判断材料不足 (上昇確率 {prob_up:.0%})"

    return signal
