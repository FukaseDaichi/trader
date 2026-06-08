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
                             model_version: str = LEGACY_MODEL_VERSION,
                             horizon_days: int = LEGACY_PREDICTION_HORIZON) -> dict | None:
    """
    Map a daily signal to a `predictions` row. Returns None when there is no
    probability to record (e.g. failed tickers), so we don't store empty rows.
    """
    prob_up = _as_float(signal.get("prob_up"))
    if prob_up is None:
        return None

    return {
        "run_date": run_date,
        "as_of_date": signal.get("date"),
        "ticker": signal.get("ticker"),
        "model_version": model_version,
        "horizon_days": int(horizon_days),
        "raw_score": prob_up,
        "prob_up": prob_up,
        "expected_ret": None,   # Phase 1 (regression head)
        "cs_rank": None,        # Phase 2 (cross-sectional)
        "features_hash": None,  # Phase 1 (reproducibility)
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
