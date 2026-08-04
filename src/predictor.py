import math


# Default triple-barrier widths — kept in sync with src.config.get_label_config
# (TRADER_TB_TP_ATR / TRADER_TB_SL_ATR / TRADER_TB_MAX_DAYS). The displayed
# take-profit / stop-loss lines use the SAME widths the Phase 1 model is trained
# on, so `prob_up` stays interpretable as "P(take-profit hit before stop-loss
# within the time barrier)".
DEFAULT_TB_TP_ATR = 1.5
DEFAULT_TB_SL_ATR = 1.0
DEFAULT_TB_MAX_DAYS = 5


DEFAULT_SIGNAL_THRESHOLDS = {
    "buy": 0.80,  # ~P80  — top 20% conviction
    "mild_buy": 0.65,  # ~P55  — moderate positive lean
    "mild_sell": 0.25,  # ~P25  — moderate negative lean
    "sell": 0.10,  # ~P10  — bottom 10% conviction
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

    for key, value in resolved.items():
        if not math.isfinite(value):
            raise ValueError(f"thresholds.{key} must be finite")

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
        raise ValueError(
            "threshold ordering must satisfy sell < mild_sell < mild_buy < buy"
        )
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


def _is_missing_or_nonfinite(value):
    if value is None:
        return True
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def _barrier_widths(label_config=None):
    """Take-profit / stop-loss ATR multiples + time barrier from the label config.

    Falls back to the module defaults so predictor.py stays free of config /
    network imports and unit-testable in isolation.
    """
    cfg = label_config if isinstance(label_config, dict) else {}
    tp_atr = cfg.get("tb_tp_atr", DEFAULT_TB_TP_ATR)
    sl_atr = cfg.get("tb_sl_atr", DEFAULT_TB_SL_ATR)
    max_days = cfg.get("tb_max_days", DEFAULT_TB_MAX_DAYS)
    try:
        tp_atr = max(0.0, float(tp_atr))
        sl_atr = max(0.0, float(sl_atr))
        max_days = max(1, int(max_days))
    except (TypeError, ValueError):
        tp_atr, sl_atr, max_days = (
            DEFAULT_TB_TP_ATR,
            DEFAULT_TB_SL_ATR,
            DEFAULT_TB_MAX_DAYS,
        )
    return tp_atr, sl_atr, max_days


def build_long_exit_plan(close_price, atr, label_config=None):
    """
    ATR-based take-profit / stop-loss / time-exit plan for a LONG entry, using
    the same triple-barrier widths the model is trained on (López de Prado
    label = which barrier is touched first). Returns ``None`` when close/ATR is
    missing or non-positive, so callers degrade to "no plan" rather than break.

    Prices are rounded to whole yen (JP equities trade in integer prices);
    percentages are signed relative moves from the entry close.
    """
    if _is_missing_or_nonfinite(close_price) or _is_missing_or_nonfinite(atr):
        return None
    close_price = float(close_price)
    atr = float(atr)
    if close_price <= 0 or atr <= 0:
        return None

    tp_atr, sl_atr, max_days = _barrier_widths(label_config)
    take_profit_price = round(close_price + tp_atr * atr)
    stop_price = round(close_price - sl_atr * atr)
    return {
        "take_profit_price": take_profit_price,
        "stop_price": stop_price,
        "take_profit_pct": (take_profit_price - close_price) / close_price,
        "stop_pct": (stop_price - close_price) / close_price,
        "time_exit_days": max_days,
        "atr": round(atr, 2),
        "tp_atr_mult": tp_atr,
        "sl_atr_mult": sl_atr,
    }


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


def generate_signal(df, prob_up, ticker_info, thresholds=None, label_config=None):
    """
    Generate a 5-level signal based on the predicted probability of price increase.

    Levels (designed so HOLD is the most common outcome):
        BUY      - Very strong upside conviction   (prob_up >= 80%)
        MILD_BUY - Moderate upside lean             (65% <= prob_up < 80%)
        HOLD     - Insufficient conviction either way (25% < prob_up < 65%)
        MILD_SELL- Moderate downside lean            (10% <= prob_up <= 25%)
        SELL     - Very strong downside conviction   (prob_up < 10%)

    Additional rule: BUY is downgraded to MILD_BUY when volatility is high.

    For long entries (BUY / MILD_BUY) an ATR-based exit plan is attached
    (``exit_plan`` + flattened ``take_profit_price`` / ``stop_price`` /
    ``time_exit_days``), using the same triple-barrier widths (``label_config``)
    the model is trained on. ``stop_loss`` now carries the ATR stop price
    (previously a fixed 2%). The plan is None when ATR is unavailable, so the
    daily run never breaks on a missing feature.
    """
    latest = df.iloc[-1]
    close_price = latest["close"]
    volatility = latest["volatility"]
    atr = latest["atr"] if "atr" in df.columns else None

    signal = {
        "ticker": ticker_info["code"],
        "name": ticker_info["name"],
        "date": latest["date"].strftime("%Y-%m-%d"),
        "close": close_price,
        "prob_up": prob_up,
        "action": "HOLD",
        "reason": "",
        "limit_price": None,
        "stop_loss": None,
        "take_profit_price": None,
        "stop_price": None,
        "take_profit_pct": None,
        "stop_pct": None,
        "time_exit_days": None,
        "exit_plan": None,
    }

    # --- Decision logic ---
    t = resolve_thresholds(thresholds)
    signal["thresholds"] = t
    action = action_from_probability(prob_up, volatility=volatility, thresholds=t)
    signal["action"] = action

    if action == "BUY":
        signal["limit_price"] = int(close_price * (1 - 0.005))
        _attach_exit_plan(signal, close_price, atr, label_config)
        signal["reason"] = (
            f"強い上昇シグナル (上昇確率 {prob_up:.0%})・ボラティリティ低 ({volatility:.1%})"
        )

    elif action == "MILD_BUY" and prob_up >= t["buy"]:
        _attach_exit_plan(signal, close_price, atr, label_config)
        signal["reason"] = (
            f"上昇シグナルだがボラティリティ高 ({volatility:.1%})・様子見推奨 (上昇確率 {prob_up:.0%})"
        )

    elif action == "MILD_BUY":
        _attach_exit_plan(signal, close_price, atr, label_config)
        signal["reason"] = f"やや上昇傾向 (上昇確率 {prob_up:.0%})"

    elif action == "SELL":
        signal["limit_price"] = int(close_price * (1 + 0.005))
        signal["reason"] = f"強い下落シグナル (上昇確率 {prob_up:.0%})"

    elif action == "MILD_SELL":
        signal["reason"] = f"やや下落傾向 (上昇確率 {prob_up:.0%})"

    else:
        signal["reason"] = f"判断材料不足 (上昇確率 {prob_up:.0%})"

    return signal


def _attach_exit_plan(signal, close_price, atr, label_config):
    """Populate the ATR take-profit / stop-loss / time-exit fields on a long signal."""
    plan = build_long_exit_plan(close_price, atr, label_config)
    if plan is None:
        return
    signal["exit_plan"] = plan
    signal["take_profit_price"] = plan["take_profit_price"]
    signal["stop_price"] = plan["stop_price"]
    signal["stop_loss"] = plan["stop_price"]  # DB `stop_loss` column, now ATR-based
    signal["take_profit_pct"] = plan["take_profit_pct"]
    signal["stop_pct"] = plan["stop_pct"]
    signal["time_exit_days"] = plan["time_exit_days"]
