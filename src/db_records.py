"""
Pure record-mapping and analytics logic for the Phase 0 measurement layer.

This module has NO database or network dependency on purpose, so it can be
unit-tested standalone (see tests/test_db_records.py). The psycopg I/O lives
in src/db.py and imports from here.
"""

from __future__ import annotations

LEGACY_MODEL_VERSION = "legacy-daily-v0"
LEGACY_PREDICTION_HORIZON = 1  # the legacy model predicts next-day direction

# Outcome horizons we evaluate every signal at (independent of the model's horizon).
OUTCOME_HORIZONS = (1, 5, 10)

LONG_ACTIONS = {"BUY", "MILD_BUY"}
AVOID_ACTIONS = {"SELL", "MILD_SELL"}


def make_event_id(run_date: str, ticker: str, event_type: str) -> str:
    """Stable, idempotent key for the outbox fallback queue."""
    return f"{run_date}:{ticker}:{event_type}"


def _as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def signal_to_prediction_row(signal: dict, run_date: str,
                             model_version: str | None = None,
                             horizon_days: int | None = None) -> dict | None:
    """
    Map a daily signal to a `predictions` row. Returns None when there is no
    probability to record (e.g. failed tickers), so we don't store empty rows.

    Phase 1 fields (model_version / horizon_days / raw_score / expected_ret /
    features_hash) are taken from the signal when present; otherwise they fall
    back to the legacy daily model (next-day binary).
    """
    prob_up = _as_float(signal.get("prob_up"))
    if prob_up is None:
        return None

    resolved_version = model_version or signal.get("model_version") or LEGACY_MODEL_VERSION
    if horizon_days is not None:
        resolved_horizon = int(horizon_days)
    elif signal.get("horizon_days") is not None:
        resolved_horizon = int(signal["horizon_days"])
    else:
        resolved_horizon = LEGACY_PREDICTION_HORIZON

    raw_score = _as_float(signal.get("raw_score"))
    if raw_score is None:
        raw_score = prob_up

    return {
        "run_date": run_date,
        "as_of_date": signal.get("date"),
        "ticker": signal.get("ticker"),
        "model_version": resolved_version,
        "horizon_days": resolved_horizon,
        "raw_score": raw_score,
        "prob_up": prob_up,
        "expected_ret": _as_float(signal.get("expected_ret")),
        "cs_rank": None,        # Phase 2 (cross-sectional)
        "features_hash": signal.get("features_hash"),
    }


def cs_prediction_row(pred: dict, run_date: str, *,
                      model_version: str,
                      horizon_days: int,
                      as_of_date=None) -> dict | None:
    """
    Map one cross-sectional prediction (ticker, raw_score, cs_rank, prob_up,
    expected_ret, features_hash) to a `predictions` table row.

    Returns None when there is no ticker. cs_rank is an int (1 = top);
    prob_up / expected_ret / raw_score are floats or None.

    Keys match the predictions upsert schema exactly:
      run_date, as_of_date, ticker, model_version, horizon_days,
      raw_score, prob_up, expected_ret, cs_rank, features_hash.
    """
    ticker = pred.get("ticker")
    if not ticker:
        return None

    cs_rank = pred.get("cs_rank")
    cs_rank_int = int(cs_rank) if cs_rank is not None else None

    return {
        "run_date": run_date,
        "as_of_date": as_of_date,
        "ticker": ticker,
        "model_version": model_version,
        "horizon_days": int(horizon_days),
        "raw_score": _as_float(pred.get("raw_score")),
        "prob_up": _as_float(pred.get("prob_up")),
        "expected_ret": _as_float(pred.get("expected_ret")),
        "cs_rank": cs_rank_int,
        "features_hash": pred.get("features_hash"),
    }


def signal_to_signal_row(signal: dict, run_date: str) -> dict:
    """Map a daily signal to a `signals` row (one per run_date/ticker)."""
    prob_up = _as_float(signal.get("prob_up"))
    return {
        "run_date": run_date,
        "as_of_date": signal.get("date"),
        "ticker": signal.get("ticker"),
        "action": signal.get("action", "HOLD"),
        "raw_action": signal.get("raw_action"),
        "conviction": prob_up,            # calibrated in Phase 1
        "target_weight": None,            # Phase 2 (portfolio)
        "thresholds": signal.get("thresholds"),
        "gate_passed": bool(signal.get("gate_passed", False)),
        "limit_price": _as_float(signal.get("limit_price")),
        "stop_loss": _as_float(signal.get("stop_loss")),
        "reason": signal.get("reason"),
        "status": signal.get("status", "ok"),
    }


def backtest_run_row(result: dict, run_date: str, *,
                     model_version: str | None = None,
                     scope: str = "portfolio") -> dict | None:
    """
    Map a ``run_portfolio_backtest`` result dict to a ``backtest_runs`` INSERT row.

    Returns ``None`` when the result is None or its status is not ``"ok"``
    (insufficient runs should not be persisted).

    Column mapping:
      run_date      <- caller-supplied
      model_version <- caller-supplied (may be None)
      scope         <- caller-supplied (default "portfolio")
      start_date    <- result["start_date"]
      end_date      <- result["end_date"]
      params        <- result["params"]  (JSONB)
      metrics       <- result["metrics"] (JSONB)
    """
    if result is None or result.get("status") != "ok":
        return None

    return {
        "run_date": run_date,
        "model_version": model_version,
        "scope": scope,
        "start_date": result["start_date"],
        "end_date": result["end_date"],
        "params": result.get("params", {}),
        "metrics": result.get("metrics", {}),
    }


def backtest_equity_rows(result: dict) -> list[dict]:
    """
    Map the ``equity`` list from a ``run_portfolio_backtest`` result to a list
    of ``backtest_equity`` INSERT rows (run_id is NOT yet set — the caller
    injects it after the backtest_runs INSERT returns the generated id).

    Key rename: ``period_return`` -> ``daily_return`` (matches the DB column).

    Returns an empty list when result is None, status != "ok", or equity is
    empty.
    """
    if result is None or result.get("status") != "ok":
        return []

    rows = []
    for eq in result.get("equity") or []:
        rows.append({
            "date": eq["date"],
            "equity": eq["equity"],
            "benchmark_equity": eq.get("benchmark_equity"),
            "daily_return": eq.get("period_return"),   # rename: period_return -> daily_return
            "benchmark_return": eq.get("benchmark_return"),
            "drawdown": eq.get("drawdown"),
            "gross_exposure": eq.get("gross_exposure"),
            "turnover": eq.get("turnover"),
        })
    return rows


def compute_outcome(action: str, entry_close: float, exit_close: float,
                    path_highs, path_lows) -> dict:
    """
    Compute the realized outcome of a single signal at one horizon.

    - realized_ret: raw stock return entry->exit (objective fact, sign-agnostic).
    - hit: directional correctness given the action's STANCE
        * long  (BUY/MILD_BUY)  -> hit if price rose
        * avoid (SELL/MILD_SELL)-> hit if price fell (avoiding a loss was correct)
        * HOLD / unknown        -> None (no directional claim)
    - mfe/mae: max favorable / adverse excursion vs entry over the holding path.
    - exit_reason: always "time" in Phase 0 (triple-barrier TP/SL is Phase 1).
    """
    entry = float(entry_close)
    if entry <= 0:
        raise ValueError("entry_close must be positive")

    realized_ret = float(exit_close) / entry - 1.0

    highs = [float(h) for h in (path_highs or [])]
    lows = [float(low) for low in (path_lows or [])]
    mfe = (max(highs) / entry - 1.0) if highs else realized_ret
    mae = (min(lows) / entry - 1.0) if lows else realized_ret

    if action in LONG_ACTIONS:
        hit = realized_ret > 0
    elif action in AVOID_ACTIONS:
        hit = realized_ret < 0
    else:
        hit = None

    return {
        "realized_ret": realized_ret,
        "hit": hit,
        "mae": mae,
        "mfe": mfe,
        "exit_reason": "time",
    }


def _mean(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def summarize_performance(rows, curve_horizon: int = 1) -> dict:
    """
    Aggregate joined (signals x signal_outcomes) rows into a dashboard summary.

    Each row: {entry_date, action, horizon_days, realized_ret, hit}.
    Hit-rate and the equity curve use LONG actions only (BUY / MILD_BUY);
    the long-only equity curve compounds the per-day mean realized return at
    `curve_horizon` (default 1 day).
    """
    long_rows = [r for r in rows if r.get("action") in LONG_ACTIONS]

    horizons = {}
    for h in OUTCOME_HORIZONS:
        h_rows = [r for r in long_rows if int(r.get("horizon_days", 0)) == h]
        rets = [r.get("realized_ret") for r in h_rows if r.get("realized_ret") is not None]
        hits = [r.get("hit") for r in h_rows if r.get("hit") is not None]
        horizons[str(h)] = {
            "count": len(rets),
            "hit_rate": (sum(1 for x in hits if x) / len(hits)) if hits else None,
            "avg_return": _mean(rets),
        }

    # Equity curve: group curve_horizon long rows by entry_date, compound daily means.
    by_date: dict[str, list[float]] = {}
    for r in long_rows:
        if int(r.get("horizon_days", 0)) != curve_horizon:
            continue
        ret = r.get("realized_ret")
        if ret is None or not r.get("entry_date"):
            continue
        by_date.setdefault(str(r["entry_date"]), []).append(float(ret))

    equity_curve = []
    equity = 1.0
    for d in sorted(by_date):
        daily_return = _mean(by_date[d]) or 0.0
        equity *= (1.0 + daily_return)
        equity_curve.append({
            "date": d,
            "equity": equity,
            "daily_return": daily_return,
            "n": len(by_date[d]),
        })

    return {
        "n_long_signals": len(long_rows),
        "horizons": horizons,
        "equity_curve": equity_curve,
    }
