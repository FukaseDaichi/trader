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

def _upsert_prediction(cur, row: dict) -> int | None:
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
        "  cs_rank=EXCLUDED.cs_rank, features_hash=EXCLUDED.features_hash"
        " RETURNING id",
        row,
    )
    returned = cur.fetchone()
    return returned[0] if returned else None


def _upsert_signal(cur, row: dict, prediction_id: int | None = None) -> None:
    from psycopg.types.json import Jsonb
    params = dict(row)
    params["prediction_id"] = prediction_id
    params["thresholds"] = Jsonb(row.get("thresholds")) if row.get("thresholds") is not None else None
    cur.execute(
        "INSERT INTO signals"
        " (run_date, as_of_date, ticker, prediction_id, action, raw_action, conviction,"
        "  target_weight, thresholds, gate_passed, limit_price, stop_loss, reason, status)"
        " VALUES (%(run_date)s, %(as_of_date)s, %(ticker)s, %(prediction_id)s,"
        "  %(action)s, %(raw_action)s, %(conviction)s, %(target_weight)s,"
        "  %(thresholds)s, %(gate_passed)s, %(limit_price)s, %(stop_loss)s,"
        "  %(reason)s, %(status)s)"
        " ON CONFLICT (run_date, ticker) DO UPDATE SET"
        "  as_of_date=EXCLUDED.as_of_date, prediction_id=EXCLUDED.prediction_id,"
        "  action=EXCLUDED.action, raw_action=EXCLUDED.raw_action,"
        "  conviction=EXCLUDED.conviction, target_weight=EXCLUDED.target_weight,"
        "  thresholds=EXCLUDED.thresholds, gate_passed=EXCLUDED.gate_passed,"
        "  limit_price=EXCLUDED.limit_price, stop_loss=EXCLUDED.stop_loss,"
        "  reason=EXCLUDED.reason, status=EXCLUDED.status",
        params,
    )


def _apply_events(conn, events: list[dict]) -> int:
    """Idempotently upsert a list of outbox events. Dedup by event_id."""
    seen = set()
    applied = 0
    prediction_ids: dict[tuple, int | None] = {}
    with conn.cursor() as cur:
        for ev in events:
            eid = ev.get("event_id")
            if eid in seen:
                continue
            seen.add(eid)
            if ev.get("kind") == "prediction":
                pred_id = _upsert_prediction(cur, ev["row"])
                row = ev["row"]
                prediction_ids[(row.get("run_date"), row.get("ticker"))] = pred_id
            elif ev.get("kind") == "signal":
                row = ev["row"]
                pred_id = prediction_ids.get((row.get("run_date"), row.get("ticker")))
                _upsert_signal(cur, row, prediction_id=pred_id)
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


def _link_prediction_ids(conn) -> int:
    """
    Best-effort refresh of signals.prediction_id from the latest matching
    prediction (same run_date/ticker). Idempotent and fixes rerun drift.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE signals s SET prediction_id = p.id"
            " FROM predictions p"
            " WHERE s.status = 'ok'"
            "   AND p.run_date = s.run_date AND p.ticker = s.ticker"
            "   AND p.id = (SELECT max(id) FROM predictions p2"
            "               WHERE p2.run_date = s.run_date AND p2.ticker = s.ticker)"
            "   AND s.prediction_id IS DISTINCT FROM p.id"
        )
        linked = cur.rowcount
    conn.commit()
    return linked


def apply_signal_history(conn, history_days: list[dict]) -> dict:
    """
    Seed historical predictions/signals from a list of
    {"run_date": ..., "signals": [...]} days (e.g. docs/state.json). Idempotent
    via the same upserts as the daily write-through, so re-running is safe.
    """
    all_events: list[dict] = []
    for day in history_days:
        run_date = day.get("run_date") or day.get("date")
        signals = day.get("signals") or []
        if not run_date or not signals:
            continue
        all_events.extend(_build_events(signals, run_date))
    applied = _apply_events(conn, all_events)
    linked = _link_prediction_ids(conn)
    return {"events": len(all_events), "applied": applied, "linked": linked}


def record_cs_predictions(cs_rows: list[dict], run_date: str) -> dict:
    """
    Write-through cross-sectional ``predictions`` rows (model_version cs-v1-*).

    Never raises. On DB-disabled or any failure, queues events to the outbox
    so the next run flushes them. Each event uses kind="prediction" so
    ``_apply_events`` upserts via ``_upsert_prediction``, which already handles
    cs_rank / expected_ret. The cs-v1-* model_version means these never collide
    with Phase 1 "pred" rows that use per-ticker-v1-* / legacy-daily-v0 versions.
    """
    events = []
    for row in cs_rows:
        ticker = row.get("ticker")
        if not ticker:
            continue
        events.append({
            "event_id": db_records.make_event_id(run_date, ticker, "cs_pred"),
            "kind": "prediction",
            "row": row,
        })

    n = len(events)
    if not db_enabled():
        queued = _queue_events(events)
        return {"ok": False, "reason": "db_disabled", "queued": queued}

    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001
        queued = _queue_events(events)
        return {"ok": False, "reason": f"connect_failed: {type(exc).__name__}", "queued": queued}

    try:
        applied = _apply_events(conn, events)
        return {"ok": True, "applied": applied}
    except Exception as exc:  # noqa: BLE001
        queued = _queue_events(events)
        return {"ok": False, "reason": f"write_failed: {type(exc).__name__}", "queued": queued}
    finally:
        conn.close()


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
        linked = _link_prediction_ids(conn)
        return {"ok": True, "applied": applied, "flushed_backlog": flushed, "linked": linked}
    except Exception as exc:  # noqa: BLE001
        queued = _queue_events(events)
        return {"ok": False, "reason": f"write_failed: {type(exc).__name__}", "queued": queued}
    finally:
        conn.close()


# --- Phase 2: portfolio backtest write-through -----------------------------

def insert_backtest_run(conn, row: dict, equity_rows: list[dict]) -> int:
    """
    Insert one ``backtest_runs`` row and its associated ``backtest_equity`` rows
    in a single transaction.

    ``row`` must contain all non-auto columns for ``backtest_runs`` (see
    ``db_records.backtest_run_row`` for the mapping). ``equity_rows`` is the list
    from ``db_records.backtest_equity_rows``; each entry must have all columns
    except ``run_id`` (injected here from the RETURNING id).

    Returns the generated ``backtest_runs.id``.
    """
    from psycopg.types.json import Jsonb
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO backtest_runs"
            " (run_date, model_version, scope, start_date, end_date, params, metrics)"
            " VALUES (%(run_date)s, %(model_version)s, %(scope)s, %(start_date)s,"
            "  %(end_date)s, %(params)s, %(metrics)s)"
            " RETURNING id",
            {
                **row,
                "params": Jsonb(row.get("params", {})),
                "metrics": Jsonb(row.get("metrics", {})),
            },
        )
        run_id = cur.fetchone()[0]

        if equity_rows:
            cur.executemany(
                "INSERT INTO backtest_equity"
                " (run_id, date, equity, benchmark_equity, daily_return,"
                "  benchmark_return, drawdown, gross_exposure, turnover)"
                " VALUES (%(run_id)s, %(date)s, %(equity)s, %(benchmark_equity)s,"
                "  %(daily_return)s, %(benchmark_return)s, %(drawdown)s,"
                "  %(gross_exposure)s, %(turnover)s)",
                [{"run_id": run_id, **eq} for eq in equity_rows],
            )

    conn.commit()
    return run_id


def record_backtest_run(result: dict, run_date: str, *,
                        model_version: str | None = None,
                        scope: str = "portfolio") -> dict:
    """
    Write-through a ``run_portfolio_backtest`` result to the DB. Never raises.

    When DB is disabled or any error occurs the function returns
    ``{"ok": False, "reason": ...}`` — the caller is responsible for deciding
    whether to log. The JSON report (``docs/portfolio_backtest.json``) is written
    by the caller regardless of this function's outcome.

    Returns ``{"ok": True, "run_id": <int>}`` on success.
    """
    run_row = db_records.backtest_run_row(result, run_date, model_version=model_version,
                                         scope=scope)
    if run_row is None:
        return {"ok": False, "reason": "insufficient_or_no_result"}

    equity_rows = db_records.backtest_equity_rows(result)

    if not db_enabled():
        return {"ok": False, "reason": "db_disabled"}

    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"connect_failed: {type(exc).__name__}"}

    try:
        run_id = insert_backtest_run(conn, run_row, equity_rows)
        return {"ok": True, "run_id": run_id}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"write_failed: {type(exc).__name__}"}
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


# --- Phase 1: macro snapshots ----------------------------------------------

def upsert_macro_snapshot(conn, row: dict) -> None:
    """Upsert one macro_snapshots row (keyed by date)."""
    from psycopg.types.json import Jsonb
    params = dict(row)
    params["raw"] = Jsonb(row["raw"]) if row.get("raw") is not None else None
    params.setdefault("market_bias", None)
    params.setdefault("regime", None)
    for col in ("usdjpy", "topix", "nikkei", "nikkei_vi", "jgb10y"):
        params.setdefault(col, None)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO macro_snapshots"
            " (date, usdjpy, topix, nikkei, nikkei_vi, jgb10y, market_bias, regime, raw)"
            " VALUES (%(date)s, %(usdjpy)s, %(topix)s, %(nikkei)s, %(nikkei_vi)s,"
            "  %(jgb10y)s, %(market_bias)s, %(regime)s, %(raw)s)"
            " ON CONFLICT (date) DO UPDATE SET"
            "  usdjpy=EXCLUDED.usdjpy, topix=EXCLUDED.topix, nikkei=EXCLUDED.nikkei,"
            "  nikkei_vi=EXCLUDED.nikkei_vi, jgb10y=EXCLUDED.jgb10y,"
            "  market_bias=EXCLUDED.market_bias, regime=EXCLUDED.regime, raw=EXCLUDED.raw",
            params,
        )
    conn.commit()


# --- Phase 1: model registry + quality -------------------------------------

def register_model_version(conn, version: str, *, kind: str, universe, feature_set,
                           params, cv_metrics, calibration=None, artifact_uri=None,
                           make_active: bool = True) -> None:
    """
    Upsert a model_registry row. When make_active, mark exactly this version
    active (all others become inactive) so there is a single active model.
    """
    from psycopg.types.json import Jsonb
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO model_registry"
            " (version, trained_at, kind, universe, feature_set, params, cv_metrics,"
            "  calibration, artifact_uri, active)"
            " VALUES (%s, now(), %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (version) DO UPDATE SET"
            "  trained_at=now(), kind=EXCLUDED.kind, universe=EXCLUDED.universe,"
            "  feature_set=EXCLUDED.feature_set, params=EXCLUDED.params,"
            "  cv_metrics=EXCLUDED.cv_metrics, calibration=EXCLUDED.calibration,"
            "  artifact_uri=EXCLUDED.artifact_uri",
            (version, kind, Jsonb(universe), Jsonb(feature_set), Jsonb(params),
             Jsonb(cv_metrics), Jsonb(calibration) if calibration is not None else None,
             artifact_uri, bool(make_active)),
        )
        if make_active:
            cur.execute(
                "UPDATE model_registry SET active = (version = %s) WHERE kind = %s",
                (version, kind),
            )
    conn.commit()


def set_active_model(conn, version: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE model_registry SET active = (version = %s)", (version,))
    conn.commit()


def active_model_version(conn) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT version FROM model_registry WHERE active = TRUE"
            " ORDER BY trained_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    return row[0] if row else None


def active_model_version_for_kind(conn, kind: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT version FROM model_registry WHERE active = TRUE AND kind = %s"
            " ORDER BY trained_at DESC LIMIT 1",
            (kind,),
        )
        row = cur.fetchone()
    return row[0] if row else None


_MODEL_QUALITY_DEFAULTS = {
    "brier": None, "brier_raw": None, "ic": None, "auc": None, "hit_rate": None,
    "calibration_rows": None, "psi_max": None, "warning": False,
}


def upsert_model_quality(conn, row: dict) -> None:
    params = {**_MODEL_QUALITY_DEFAULTS, **row}
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO model_quality_snapshots"
            " (run_date, model_version, ticker, horizon_days, brier, brier_raw, ic, auc,"
            "  hit_rate, calibration_rows, psi_max, warning)"
            " VALUES (%(run_date)s, %(model_version)s, %(ticker)s, %(horizon_days)s,"
            "  %(brier)s, %(brier_raw)s, %(ic)s, %(auc)s, %(hit_rate)s,"
            "  %(calibration_rows)s, %(psi_max)s, %(warning)s)"
            " ON CONFLICT (run_date, model_version, ticker, horizon_days) DO UPDATE SET"
            "  brier=EXCLUDED.brier, brier_raw=EXCLUDED.brier_raw, ic=EXCLUDED.ic,"
            "  auc=EXCLUDED.auc, hit_rate=EXCLUDED.hit_rate,"
            "  calibration_rows=EXCLUDED.calibration_rows, psi_max=EXCLUDED.psi_max,"
            "  warning=EXCLUDED.warning",
            params,
        )
    conn.commit()


# --- Phase 1: drift -------------------------------------------------------

def fetch_prediction_outcomes(conn, model_version: str, horizon_days: int) -> list[dict]:
    """
    Joined (predictions x signals x signal_outcomes) rows for one model version
    at one horizon: prob_up, raw_score, realized_ret, hit. Used by drift_check.
    """
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT p.ticker, p.prob_up, p.raw_score, o.realized_ret, o.hit"
            " FROM predictions p"
            " JOIN signals s ON s.run_date = p.run_date AND s.ticker = p.ticker"
            " JOIN signal_outcomes o ON o.signal_id = s.id AND o.horizon_days = %s"
            " WHERE p.model_version = %s AND p.horizon_days = %s",
            (horizon_days, model_version, horizon_days),
        )
        return cur.fetchall()


def insert_drift_report(conn, run_date: str, model_version: str | None, scope: str,
                        status: str, breached: bool, metrics: dict) -> None:
    from psycopg.types.json import Jsonb
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO drift_reports"
            " (run_date, model_version, scope, status, breached, metrics)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (run_date, model_version, scope, status, bool(breached), Jsonb(metrics)),
        )
    conn.commit()
