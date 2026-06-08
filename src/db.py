"""
Phase 0 measurement layer: psycopg I/O, isolated so the daily pipeline never
breaks when the database is unreachable.

Write path is write-through with an on-disk fallback queue (data/outbox/*.jsonl).
Every helper that touches the network is wrapped by callers in try/except;
record_run() itself never raises.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import DATA_DIR
from . import db_records
from .db_records import LEGACY_MODEL_VERSION, OUTCOME_HORIZONS  # re-exported

DEFAULT_FALLBACK_DIR = DATA_DIR / "outbox"


# --- env helpers (mirror src/data_loader.py style) -------------------------

def _env_str(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    return raw if raw not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def database_url() -> str | None:
    url = _env_str("DATABASE_URL")
    return url or None


def db_enabled() -> bool:
    return _env_bool("TRADER_DB_ENABLED", True) and database_url() is not None


def _fallback_dir() -> Path:
    return Path(_env_str("TRADER_DB_FALLBACK_DIR", str(DEFAULT_FALLBACK_DIR)))


def connect():
    """Open a psycopg connection. Raises on failure (callers handle it)."""
    import psycopg
    timeout = _env_int("TRADER_DB_WRITE_TIMEOUT_SEC", 15)
    return psycopg.connect(database_url(), connect_timeout=timeout)


# --- outbox (filesystem only, no network) ----------------------------------

def _queue_events(events: list[dict]) -> int:
    if not events:
        return 0
    out_dir = _fallback_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (datetime.now(UTC) + timedelta(hours=9)).strftime("%Y%m%d%H%M%S")
    path = out_dir / f"{stamp}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return len(events)


def _read_outbox_events() -> list[dict]:
    out_dir = _fallback_dir()
    if not out_dir.exists():
        return []
    events = []
    for path in sorted(out_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _clear_outbox() -> None:
    out_dir = _fallback_dir()
    if not out_dir.exists():
        return
    for path in out_dir.glob("*.jsonl"):
        path.unlink(missing_ok=True)


def _build_events(signals: list[dict], run_date: str) -> list[dict]:
    """Turn daily signals into idempotent outbox events (pred + sig)."""
    events = []
    for s in signals:
        ticker = s.get("ticker")
        if not ticker:
            continue
        pred = db_records.signal_to_prediction_row(s, run_date)
        if pred is not None:
            events.append({
                "event_id": db_records.make_event_id(run_date, ticker, "pred"),
                "kind": "prediction", "row": pred,
            })
        events.append({
            "event_id": db_records.make_event_id(run_date, ticker, "sig"),
            "kind": "signal",
            "row": db_records.signal_to_signal_row(s, run_date),
        })
    return events


# --- upserts ---------------------------------------------------------------

def _upsert_prediction(cur, row: dict) -> None:
    cur.execute(
        "INSERT INTO predictions"
        " (run_date, as_of_date, ticker, model_version, horizon_days,"
        "  raw_score, prob_up, expected_ret, cs_rank, features_hash)"
        " VALUES (%(run_date)s, %(as_of_date)s, %(ticker)s, %(model_version)s,"
        "  %(horizon_days)s, %(raw_score)s, %(prob_up)s, %(expected_ret)s,"
        "  %(cs_rank)s, %(features_hash)s)"
        " ON CONFLICT (run_date, ticker, model_version, horizon_days) DO UPDATE SET"
        "  as_of_date=EXCLUDED.as_of_date, raw_score=EXCLUDED.raw_score,"
        "  prob_up=EXCLUDED.prob_up, expected_ret=EXCLUDED.expected_ret,"
        "  cs_rank=EXCLUDED.cs_rank, features_hash=EXCLUDED.features_hash",
        row,
    )


def _upsert_signal(cur, row: dict) -> None:
    from psycopg.types.json import Jsonb
    params = dict(row)
    params["thresholds"] = Jsonb(row.get("thresholds")) if row.get("thresholds") is not None else None
    cur.execute(
        "INSERT INTO signals"
        " (run_date, as_of_date, ticker, action, raw_action, conviction,"
        "  target_weight, thresholds, gate_passed, limit_price, stop_loss, reason, status)"
        " VALUES (%(run_date)s, %(as_of_date)s, %(ticker)s, %(action)s, %(raw_action)s,"
        "  %(conviction)s, %(target_weight)s, %(thresholds)s, %(gate_passed)s,"
        "  %(limit_price)s, %(stop_loss)s, %(reason)s, %(status)s)"
        " ON CONFLICT (run_date, ticker) DO UPDATE SET"
        "  as_of_date=EXCLUDED.as_of_date, action=EXCLUDED.action,"
        "  raw_action=EXCLUDED.raw_action, conviction=EXCLUDED.conviction,"
        "  target_weight=EXCLUDED.target_weight, thresholds=EXCLUDED.thresholds,"
        "  gate_passed=EXCLUDED.gate_passed, limit_price=EXCLUDED.limit_price,"
        "  stop_loss=EXCLUDED.stop_loss, reason=EXCLUDED.reason, status=EXCLUDED.status",
        params,
    )


def _apply_events(conn, events: list[dict]) -> int:
    """Idempotently upsert a list of outbox events. Dedup by event_id."""
    seen = set()
    applied = 0
    with conn.cursor() as cur:
        for ev in events:
            eid = ev.get("event_id")
            if eid in seen:
                continue
            seen.add(eid)
            if ev.get("kind") == "prediction":
                _upsert_prediction(cur, ev["row"])
            elif ev.get("kind") == "signal":
                _upsert_signal(cur, ev["row"])
            applied += 1
    conn.commit()
    return applied


def flush_outbox(conn) -> int:
    events = _read_outbox_events()
    if not events:
        return 0
    applied = _apply_events(conn, events)
    _clear_outbox()
    return applied


def record_run(signals: list[dict], run_date: str) -> dict:
    """
    Write-through the day's predictions+signals. Never raises.
    On any failure, events are queued to the outbox for the next run.
    """
    events = _build_events(signals, run_date)
    if not db_enabled():
        queued = _queue_events(events)
        return {"ok": False, "reason": "db_disabled", "queued": queued}

    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001
        queued = _queue_events(events)
        return {"ok": False, "reason": f"connect_failed: {type(exc).__name__}", "queued": queued}

    try:
        flushed = flush_outbox(conn)
        applied = _apply_events(conn, events)
        return {"ok": True, "applied": applied, "flushed_backlog": flushed}
    except Exception as exc:  # noqa: BLE001
        queued = _queue_events(events)
        return {"ok": False, "reason": f"write_failed: {type(exc).__name__}", "queued": queued}
    finally:
        conn.close()


# --- settlement support (read) ---------------------------------------------

def fetch_unsettled(conn) -> list[dict]:
    """Actionable signals and which OUTCOME_HORIZONS are still missing."""
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT s.id AS signal_id, s.ticker, s.as_of_date, s.action,"
            " COALESCE(array_agg(o.horizon_days) FILTER (WHERE o.horizon_days IS NOT NULL), '{}') AS settled"
            " FROM signals s LEFT JOIN signal_outcomes o ON o.signal_id = s.id"
            " WHERE s.status = 'ok' AND s.action IN ('BUY','MILD_BUY','SELL','MILD_SELL')"
            " GROUP BY s.id, s.ticker, s.as_of_date, s.action"
        )
        rows = cur.fetchall()
    result = []
    for r in rows:
        settled = set(r["settled"] or [])
        missing = [h for h in OUTCOME_HORIZONS if h not in settled]
        if missing:
            result.append({**r, "missing_horizons": missing})
    return result


def upsert_outcome(conn, signal_id: int, horizon_days: int, payload: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signal_outcomes"
            " (signal_id, horizon_days, entry_date, eval_date, entry_close, exit_close,"
            "  realized_ret, benchmark_ret, excess_ret, hit, mae, mfe, exit_reason)"
            " VALUES (%(signal_id)s, %(horizon_days)s, %(entry_date)s, %(eval_date)s,"
            "  %(entry_close)s, %(exit_close)s, %(realized_ret)s, %(benchmark_ret)s,"
            "  %(excess_ret)s, %(hit)s, %(mae)s, %(mfe)s, %(exit_reason)s)"
            " ON CONFLICT (signal_id, horizon_days) DO UPDATE SET"
            "  eval_date=EXCLUDED.eval_date, entry_close=EXCLUDED.entry_close,"
            "  exit_close=EXCLUDED.exit_close, realized_ret=EXCLUDED.realized_ret,"
            "  benchmark_ret=EXCLUDED.benchmark_ret, excess_ret=EXCLUDED.excess_ret,"
            "  hit=EXCLUDED.hit, mae=EXCLUDED.mae, mfe=EXCLUDED.mfe, exit_reason=EXCLUDED.exit_reason",
            {"signal_id": signal_id, "horizon_days": horizon_days, **payload},
        )
    conn.commit()


def fetch_outcome_rows(conn) -> list[dict]:
    """Joined rows for summarize_performance()."""
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT s.as_of_date AS entry_date, s.action, o.horizon_days,"
            " o.realized_ret, o.hit"
            " FROM signal_outcomes o JOIN signals s ON s.id = o.signal_id"
            " WHERE s.action IN ('BUY','MILD_BUY','SELL','MILD_SELL')"
        )
        rows = cur.fetchall()
    # Normalize dates to ISO strings for the pure summarizer.
    for r in rows:
        if r.get("entry_date") is not None:
            r["entry_date"] = str(r["entry_date"])
    return rows


def db_size_mb(conn) -> float:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database())")
        size_bytes = cur.fetchone()[0]
    return round(size_bytes / (1024 * 1024), 2)
