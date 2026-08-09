"""
Phase 0 measurement layer: psycopg I/O, isolated so the daily pipeline never
breaks when the database is unreachable.

Write path is write-through with an on-disk fallback queue (data/outbox/*.jsonl).
Every helper that touches the network is wrapped by callers in try/except;
record_run() itself never raises.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .config import DATA_DIR
from . import db_records
from .db_records import OUTCOME_HORIZONS  # re-exported
from .env import get_env_bool, get_env_int, get_env_str
from .timeutil import now_jst
from .utils import log_exc

DEFAULT_FALLBACK_DIR = DATA_DIR / "outbox"

PHASE1_MODEL_KIND = "per_ticker_horizon_v1"
AUTO_STUB_MODEL_KIND = "auto_stub"
PHASE1_MODEL_VERSION_PREFIX = "per-ticker-v1-"
EPHEMERAL_PHASE1_MODEL_VERSION_PREFIX = db_records.EPHEMERAL_PHASE1_MODEL_VERSION_PREFIX
MODEL_REGISTRY_EVENT_KIND = "model_registry"


# --- env helpers (canonical implementations live in src/env.py) ------------


def _env_str(name: str, default: str = "") -> str:
    return get_env_str(name, default)


def _env_bool(name: str, default: bool) -> bool:
    return get_env_bool(name, default, invalid="false")


def _env_int(name: str, default: int) -> int:
    return get_env_int(name, default)


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
    stamp = now_jst().strftime("%Y%m%d%H%M%S")
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
            events.append(
                {
                    "event_id": db_records.make_event_id(run_date, ticker, "pred"),
                    "kind": "prediction",
                    "row": pred,
                }
            )
        events.append(
            {
                "event_id": db_records.make_event_id(run_date, ticker, "sig"),
                "kind": "signal",
                "row": db_records.signal_to_signal_row(s, run_date),
            }
        )
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
    params["thresholds"] = (
        Jsonb(row.get("thresholds")) if row.get("thresholds") is not None else None
    )
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


def _model_registry_row(
    version: str,
    *,
    kind: str,
    universe,
    feature_set,
    params,
    cv_metrics,
    calibration=None,
    artifact_uri=None,
    make_active: bool = True,
) -> dict:
    """Build the JSON-safe row shared by direct registration and outbox replay."""
    return {
        "version": version,
        "kind": kind,
        "universe": universe,
        "feature_set": feature_set,
        "params": params,
        "cv_metrics": cv_metrics,
        "calibration": calibration,
        "artifact_uri": artifact_uri,
        "make_active": bool(make_active),
    }


def _upsert_model_registry(cur, row: dict) -> None:
    """Idempotently upsert one registry row and scope activation by kind."""
    from psycopg.types.json import Jsonb

    version = row.get("version")
    kind = row.get("kind")
    if not isinstance(version, str) or not version:
        raise ValueError("model_registry event requires a non-empty version")
    if not isinstance(kind, str) or not kind:
        raise ValueError("model_registry event requires a non-empty kind")

    make_active = bool(row.get("make_active", True))
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
        (
            version,
            kind,
            Jsonb(row.get("universe") or []),
            Jsonb(row.get("feature_set") or []),
            Jsonb(row.get("params") or {}),
            Jsonb(row.get("cv_metrics") or {}),
            Jsonb(row.get("calibration"))
            if row.get("calibration") is not None
            else None,
            row.get("artifact_uri"),
            make_active,
        ),
    )
    if make_active:
        # Phase 1 and Phase 2 models are independently active. Replaying a
        # Phase 1 event must never deactivate the active CS model (or vice versa).
        cur.execute(
            "UPDATE model_registry SET active = (version = %s) WHERE kind = %s",
            (version, kind),
        )
    elif row.get("_force_inactive"):
        cur.execute(
            "UPDATE model_registry SET active = FALSE WHERE version = %s AND kind = %s",
            (version, kind),
        )


def _registry_replay_row(row: dict) -> dict:
    """Fail closed when a queued Phase 1 activation is stale.

    The filesystem pointer is authoritative. A registry event queued for an
    older weekly version may remain after a newer file activation. Replaying it
    should preserve the historical registry row but must not roll DB active
    state back to that old version.
    """
    replay_row = dict(row)
    if replay_row.get("kind") != PHASE1_MODEL_KIND or not replay_row.get(
        "make_active", True
    ):
        return replay_row

    params = replay_row.get("params") or {}
    expected = params.get("file_active_pointer") if isinstance(params, dict) else None
    expected = expected if isinstance(expected, dict) else {}
    try:
        from . import model_store

        current = model_store.read_active_model() or {}
    except Exception as exc:  # noqa: BLE001
        log_exc("outbox replay: active model pointer read failed", exc)
        current = {}

    required = ("version", "manifest_sha256", "config_sha256")
    pointer_matches = replay_row.get("version") == expected.get("version") and all(
        isinstance(expected.get(field), str)
        and bool(expected[field])
        and current.get(field) == expected[field]
        for field in required
    )
    if pointer_matches:
        return replay_row

    replay_row["make_active"] = False
    replay_row["_force_inactive"] = True
    print(
        "outbox replay: model_registry activation is stale; "
        f"upserting {replay_row.get('version')} inactive"
    )
    return replay_row


def build_model_registry_event(
    version: str,
    *,
    kind: str,
    universe,
    feature_set,
    params,
    cv_metrics,
    calibration=None,
    artifact_uri=None,
    make_active: bool = True,
) -> dict:
    """Build a stable, deduplicable model-registry outbox event."""
    row = _model_registry_row(
        version,
        kind=kind,
        universe=universe,
        feature_set=feature_set,
        params=params,
        cv_metrics=cv_metrics,
        calibration=calibration,
        artifact_uri=artifact_uri,
        make_active=make_active,
    )
    return {
        "event_id": f"{MODEL_REGISTRY_EVENT_KIND}:{kind}:{version}",
        "kind": MODEL_REGISTRY_EVENT_KIND,
        "schema_version": 1,
        "row": row,
    }


def queue_model_registry_event(
    version: str,
    *,
    kind: str,
    universe,
    feature_set,
    params,
    cv_metrics,
    calibration=None,
    artifact_uri=None,
    make_active: bool = True,
) -> dict:
    """Queue one registry event without touching the network; never raises."""
    event_id = f"{MODEL_REGISTRY_EVENT_KIND}:{kind}:{version}"
    try:
        event = build_model_registry_event(
            version,
            kind=kind,
            universe=universe,
            feature_set=feature_set,
            params=params,
            cv_metrics=cv_metrics,
            calibration=calibration,
            artifact_uri=artifact_uri,
            make_active=make_active,
        )
        queued = _queue_events([event])
        return {
            "ok": queued == 1,
            "queued": queued,
            "event_id": event_id,
        }
    except Exception as exc:  # noqa: BLE001
        log_exc("model_registry event could not be queued", exc)
        return {
            "ok": False,
            "queued": 0,
            "event_id": event_id,
            "reason": f"queue_failed: {type(exc).__name__}: {exc}",
        }


def _ensure_fk_parents(cur, events: list[dict]) -> None:
    """
    Stub-insert missing FK parent rows referenced by the events, so that a
    ticker added by curation (DB seeding is manual) or an unregistered
    model_version can never fail the predictions/signals upserts. Stubs are
    ON CONFLICT DO NOTHING; db_migrate re-seeding enriches them later.
    """
    from psycopg.types.json import Jsonb

    tickers: set[str] = set()
    versions: set[str] = set()
    for ev in events:
        row = ev.get("row") or {}
        if row.get("ticker"):
            tickers.add(row["ticker"])
        if ev.get("kind") == "prediction" and row.get("model_version"):
            versions.add(row["model_version"])
    if tickers:
        cur.executemany(
            "INSERT INTO tickers (code, name, enabled)"
            " VALUES (%s, %s, TRUE)"
            " ON CONFLICT (code) DO NOTHING",
            [(code, code) for code in sorted(tickers)],
        )
    if versions:
        cur.executemany(
            "INSERT INTO model_registry"
            " (version, trained_at, kind, universe, feature_set, params,"
            "  cv_metrics, active)"
            " VALUES (%s, now(), 'auto_stub', %s, %s, %s, %s, FALSE)"
            " ON CONFLICT (version) DO NOTHING",
            [(v, Jsonb([]), Jsonb([]), Jsonb({}), Jsonb({})) for v in sorted(versions)],
        )


def _apply_one(cur, ev: dict, prediction_ids: dict) -> None:
    if ev.get("kind") == "prediction":
        pred_id = _upsert_prediction(cur, ev["row"])
        row = ev["row"]
        prediction_ids[(row.get("run_date"), row.get("ticker"))] = pred_id
    elif ev.get("kind") == "signal":
        row = ev["row"]
        pred_id = prediction_ids.get((row.get("run_date"), row.get("ticker")))
        _upsert_signal(cur, row, prediction_id=pred_id)
    elif ev.get("kind") == MODEL_REGISTRY_EVENT_KIND:
        _upsert_model_registry(cur, _registry_replay_row(ev["row"]))
    else:
        # In tolerant replay this becomes a dead letter, preserving the
        # existing property that one poison event cannot stall valid events.
        raise ValueError(f"unsupported outbox event kind: {ev.get('kind')!r}")


def _apply_events(conn, events: list[dict]) -> int:
    """Idempotently upsert a list of outbox events. Dedup by event_id."""
    seen = set()
    applied = 0
    prediction_ids: dict[tuple, int | None] = {}
    with conn.cursor() as cur:
        _ensure_fk_parents(cur, events)
        for ev in events:
            eid = ev.get("event_id")
            if eid in seen:
                continue
            seen.add(eid)
            _apply_one(cur, ev, prediction_ids)
            applied += 1
    conn.commit()
    return applied


def _apply_events_tolerant(conn, events: list[dict]) -> tuple[int, list[dict]]:
    """
    Apply events one-by-one inside savepoints. A failing event is rolled back
    and returned as a dead letter instead of aborting the whole batch.
    """
    seen = set()
    applied = 0
    dead: list[dict] = []
    prediction_ids: dict[tuple, int | None] = {}
    with conn.cursor() as cur:
        # A parent-stub failure must not revert the flush to all-or-nothing:
        # events whose parents are truly missing fail individually below.
        cur.execute("SAVEPOINT outbox_parents")
        try:
            _ensure_fk_parents(cur, events)
            cur.execute("RELEASE SAVEPOINT outbox_parents")
        except Exception as exc:  # noqa: BLE001
            cur.execute("ROLLBACK TO SAVEPOINT outbox_parents")
            log_exc("outbox replay: FK parent stub insert failed", exc)
        for ev in events:
            eid = ev.get("event_id")
            if eid in seen:
                continue
            seen.add(eid)
            cur.execute("SAVEPOINT outbox_event")
            try:
                _apply_one(cur, ev, prediction_ids)
                cur.execute("RELEASE SAVEPOINT outbox_event")
                applied += 1
            except Exception as exc:  # noqa: BLE001
                cur.execute("ROLLBACK TO SAVEPOINT outbox_event")
                dead.append({**ev, "dead_reason": f"{type(exc).__name__}: {exc}"[:500]})
    conn.commit()
    return applied, dead


def _quarantine_events(events: list[dict]) -> int:
    if not events:
        return 0
    dead_dir = _fallback_dir() / "dead"
    dead_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_jst().strftime("%Y%m%d%H%M%S")
    path = dead_dir / f"{stamp}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return len(events)


def flush_outbox(conn) -> int:
    """
    Replay queued outbox events. Poison events (e.g. FK violations) are
    quarantined to <outbox>/dead/ instead of aborting the whole replay —
    an all-or-nothing replay let one bad event stall write-through for
    weeks in 2026-07 while the backlog grew daily.
    """
    events = _read_outbox_events()
    if not events:
        return 0
    applied, dead = _apply_events_tolerant(conn, events)
    if dead:
        n = _quarantine_events(dead)
        print(f"outbox replay: quarantined {n} poison event(s) to dead/")
    _clear_outbox()
    return applied


def _link_prediction_ids(conn) -> int:
    """
    Best-effort refresh of ``signals.prediction_id`` from the latest matching
    *Phase 1* prediction (same run_date/ticker).

    Phase 2 predictions share the predictions table and may be inserted after
    Phase 1 on a rerun/outbox replay. Selecting max(id) without a model-kind
    guard can therefore relink a human-facing Phase 1 signal to a CS score.
    Keep the link on per-ticker predictions only; ``cs_rank IS NULL`` is an
    additional guard for auto-stub registry rows written during DB recovery.
    """
    with conn.cursor() as cur:
        cur.execute(
            "WITH phase1_candidates AS ("
            " SELECT DISTINCT ON (p.run_date, p.ticker)"
            "  p.run_date, p.ticker, p.id"
            " FROM predictions p"
            " JOIN model_registry mr ON mr.version = p.model_version"
            " WHERE p.cs_rank IS NULL"
            "   AND (mr.kind = %s"
            "        OR p.model_version = %s"
            "        OR (mr.kind = %s"
            "            AND (p.model_version LIKE %s"
            "                 OR p.model_version LIKE %s)))"
            " ORDER BY p.run_date, p.ticker, p.id DESC"
            ")"
            " UPDATE signals s SET prediction_id = p.id"
            " FROM phase1_candidates p"
            " WHERE s.status = 'ok'"
            "   AND p.run_date = s.run_date AND p.ticker = s.ticker"
            "   AND s.prediction_id IS DISTINCT FROM p.id",
            (
                PHASE1_MODEL_KIND,
                db_records.LEGACY_MODEL_VERSION,
                AUTO_STUB_MODEL_KIND,
                f"{PHASE1_MODEL_VERSION_PREFIX}%",
                f"{EPHEMERAL_PHASE1_MODEL_VERSION_PREFIX}%",
            ),
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
        events.append(
            {
                "event_id": db_records.make_event_id(run_date, ticker, "cs_pred"),
                "kind": "prediction",
                "row": row,
            }
        )

    if not db_enabled():
        queued = _queue_events(events)
        return {"ok": False, "reason": "db_disabled", "queued": queued}

    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001
        queued = _queue_events(events)
        return {
            "ok": False,
            "reason": f"connect_failed: {type(exc).__name__}",
            "queued": queued,
        }

    try:
        applied = _apply_events(conn, events)
        return {"ok": True, "applied": applied}
    except Exception as exc:  # noqa: BLE001
        log_exc("DB write failed; events queued to outbox", exc)
        queued = _queue_events(events)
        return {
            "ok": False,
            "reason": f"write_failed: {type(exc).__name__}",
            "queued": queued,
        }
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
        return {
            "ok": False,
            "reason": f"connect_failed: {type(exc).__name__}",
            "queued": queued,
        }

    try:
        flushed = flush_outbox(conn)
        applied = _apply_events(conn, events)
        linked = _link_prediction_ids(conn)
        return {
            "ok": True,
            "applied": applied,
            "flushed_backlog": flushed,
            "linked": linked,
        }
    except Exception as exc:  # noqa: BLE001
        log_exc("DB record_run write failed; events queued to outbox", exc)
        queued = _queue_events(events)
        return {
            "ok": False,
            "reason": f"write_failed: {type(exc).__name__}",
            "queued": queued,
        }
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


def record_backtest_run(
    result: dict,
    run_date: str,
    *,
    model_version: str | None = None,
    scope: str = "portfolio",
) -> dict:
    """
    Write-through a ``run_portfolio_backtest`` result to the DB. Never raises.

    When DB is disabled or any error occurs the function returns
    ``{"ok": False, "reason": ...}`` — the caller is responsible for deciding
    whether to log. The JSON report (``docs/portfolio_backtest.json``) is written
    by the caller regardless of this function's outcome.

    Returns ``{"ok": True, "run_id": <int>}`` on success.
    """
    run_row = db_records.backtest_run_row(
        result, run_date, model_version=model_version, scope=scope
    )
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


# --- Phase 2: daily portfolio snapshot write-through -----------------------


def upsert_portfolio_snapshot(conn, row: dict) -> None:
    """
    Upsert one ``portfolio_snapshots`` row (keyed by ``run_date``).

    ``row`` must contain every non-auto column (see
    ``db_records.portfolio_snapshot_row``). JSONB columns (positions,
    diff_from_prev, sector_exposure, constraints, warnings) are wrapped in
    ``Jsonb`` here. ``constraints`` is a SQL reserved word -> quoted.
    """
    from psycopg.types.json import Jsonb

    params = dict(row)
    params["positions"] = Jsonb(row.get("positions") or [])
    for col in ("diff_from_prev", "sector_exposure", "constraints", "warnings"):
        params[col] = Jsonb(row[col]) if row.get(col) is not None else None
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO portfolio_snapshots"
            " (run_date, as_of_date, model_version, mode, status, positions,"
            "  diff_from_prev, gross_exposure, net_exposure, sector_exposure,"
            '  expected_ret, expected_vol, "constraints", warnings)'
            " VALUES (%(run_date)s, %(as_of_date)s, %(model_version)s, %(mode)s,"
            "  %(status)s, %(positions)s, %(diff_from_prev)s, %(gross_exposure)s,"
            "  %(net_exposure)s, %(sector_exposure)s, %(expected_ret)s,"
            "  %(expected_vol)s, %(constraints)s, %(warnings)s)"
            " ON CONFLICT (run_date) DO UPDATE SET"
            "  as_of_date=EXCLUDED.as_of_date, model_version=EXCLUDED.model_version,"
            "  mode=EXCLUDED.mode, status=EXCLUDED.status, positions=EXCLUDED.positions,"
            "  diff_from_prev=EXCLUDED.diff_from_prev, gross_exposure=EXCLUDED.gross_exposure,"
            "  net_exposure=EXCLUDED.net_exposure, sector_exposure=EXCLUDED.sector_exposure,"
            "  expected_ret=EXCLUDED.expected_ret, expected_vol=EXCLUDED.expected_vol,"
            '  "constraints"=EXCLUDED."constraints", warnings=EXCLUDED.warnings',
            params,
        )
    conn.commit()


def fetch_latest_portfolio_snapshot(conn) -> dict | None:
    """
    Return the most recent ``portfolio_snapshots`` row (max ``run_date``) as a
    dict, or ``None`` when the table is empty. JSONB columns come back as plain
    Python (e.g. ``positions`` is a list of dicts).
    """
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT run_date, as_of_date, model_version, mode, status, positions,"
            " diff_from_prev, gross_exposure, net_exposure, sector_exposure,"
            ' expected_ret, expected_vol, "constraints", warnings'
            " FROM portfolio_snapshots ORDER BY run_date DESC LIMIT 1"
        )
        return cur.fetchone()


def record_portfolio_snapshot(snapshot: dict, run_date: str) -> dict:
    """
    Write-through the daily portfolio snapshot to ``portfolio_snapshots``.
    Never raises.

    Best-effort only (no outbox): the daily snapshot is regenerated every run,
    so a transient failure simply means the row is rewritten next time. Returns
    ``{"ok": True}`` on success or ``{"ok": False, "reason": ...}`` otherwise.
    The caller logs the result; ``docs/portfolio_latest.json`` is written by the
    dashboard layer regardless of this function's outcome.
    """
    row = db_records.portfolio_snapshot_row(snapshot, run_date=run_date)
    if row is None:
        return {"ok": False, "reason": "no_persistable_snapshot"}

    if not db_enabled():
        return {"ok": False, "reason": "db_disabled"}

    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"connect_failed: {type(exc).__name__}"}

    try:
        upsert_portfolio_snapshot(conn, row)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"write_failed: {type(exc).__name__}"}
    finally:
        conn.close()


# --- settlement support (read) ---------------------------------------------


def fetch_unsettled(conn) -> list[dict]:
    """Actionable signals missing outcomes under the current execution contract."""
    from psycopg.rows import dict_row
    from .execution import EXECUTION_CONTRACT_VERSION

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT s.id AS signal_id, s.ticker, s.as_of_date, s.action,"
            " COALESCE(array_agg(o.horizon_days) FILTER (WHERE o.horizon_days IS NOT NULL), '{}') AS settled"
            " FROM signals s LEFT JOIN signal_outcomes o ON o.signal_id = s.id"
            "  AND o.contract_version = %s"
            " WHERE s.status = 'ok' AND s.action IN ('BUY','MILD_BUY','SELL','MILD_SELL')"
            " GROUP BY s.id, s.ticker, s.as_of_date, s.action",
            (EXECUTION_CONTRACT_VERSION,),
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
    from .execution import (
        BENCHMARK_BASIS,
        ENTRY_PRICE_BASIS,
        EXECUTION_CONTRACT_VERSION,
        EXIT_PRICE_BASIS,
    )

    params = {
        "market_as_of_date": payload.get("market_as_of_date"),
        "entry_price": payload.get("entry_price", payload.get("entry_close")),
        "exit_price": payload.get("exit_price", payload.get("exit_close")),
        "entry_price_basis": payload.get("entry_price_basis", ENTRY_PRICE_BASIS),
        "exit_price_basis": payload.get("exit_price_basis", EXIT_PRICE_BASIS),
        "contract_version": payload.get("contract_version", EXECUTION_CONTRACT_VERSION),
        "benchmark_basis": payload.get("benchmark_basis", BENCHMARK_BASIS),
        "signal_id": signal_id,
        "horizon_days": horizon_days,
        **payload,
    }
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signal_outcomes"
            " (signal_id, horizon_days, market_as_of_date, entry_date, eval_date,"
            "  entry_close, exit_close, entry_price, exit_price, entry_price_basis,"
            "  exit_price_basis, contract_version, benchmark_basis, realized_ret,"
            "  benchmark_ret, excess_ret, hit, mae, mfe, exit_reason)"
            " VALUES (%(signal_id)s, %(horizon_days)s, %(market_as_of_date)s,"
            "  %(entry_date)s, %(eval_date)s, %(entry_close)s, %(exit_close)s, %(entry_price)s,"
            "  %(exit_price)s, %(entry_price_basis)s, %(exit_price_basis)s,"
            "  %(contract_version)s, %(benchmark_basis)s, %(realized_ret)s,"
            "  %(benchmark_ret)s, %(excess_ret)s, %(hit)s, %(mae)s, %(mfe)s,"
            "  %(exit_reason)s)"
            " ON CONFLICT (signal_id, horizon_days) DO UPDATE SET"
            "  market_as_of_date=EXCLUDED.market_as_of_date,"
            "  entry_date=EXCLUDED.entry_date, eval_date=EXCLUDED.eval_date,"
            "  entry_close=EXCLUDED.entry_close, exit_close=EXCLUDED.exit_close,"
            "  entry_price=EXCLUDED.entry_price, exit_price=EXCLUDED.exit_price,"
            "  entry_price_basis=EXCLUDED.entry_price_basis,"
            "  exit_price_basis=EXCLUDED.exit_price_basis,"
            "  contract_version=EXCLUDED.contract_version,"
            "  benchmark_basis=EXCLUDED.benchmark_basis,"
            "  realized_ret=EXCLUDED.realized_ret,"
            "  benchmark_ret=EXCLUDED.benchmark_ret, excess_ret=EXCLUDED.excess_ret,"
            "  hit=EXCLUDED.hit, mae=EXCLUDED.mae, mfe=EXCLUDED.mfe, exit_reason=EXCLUDED.exit_reason",
            params,
        )
    conn.commit()


def fetch_signals_for_outcome_restatement(conn) -> list[dict]:
    """Return every actionable signal for one-off execution-contract restatement."""
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT s.id AS signal_id, s.ticker, s.as_of_date, s.action"
            " FROM signals s"
            " WHERE s.status = 'ok'"
            "   AND s.action IN ('BUY','MILD_BUY','SELL','MILD_SELL')"
            " ORDER BY s.as_of_date, s.id"
        )
        rows = cur.fetchall()
    for row in rows:
        if row.get("as_of_date") is not None:
            row["as_of_date"] = str(row["as_of_date"])
    return rows


def fetch_outcome_rows(conn) -> list[dict]:
    """Joined rows for summarize_performance()."""
    from psycopg.rows import dict_row
    from .execution import EXECUTION_CONTRACT_VERSION

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT o.entry_date, s.action, o.horizon_days,"
            " o.realized_ret, o.hit"
            " FROM signal_outcomes o JOIN signals s ON s.id = o.signal_id"
            " WHERE s.action IN ('BUY','MILD_BUY','SELL','MILD_SELL')"
            "   AND o.contract_version = %s",
            (EXECUTION_CONTRACT_VERSION,),
        )
        rows = cur.fetchall()
    # Normalize dates to ISO strings for the pure summarizer.
    for r in rows:
        if r.get("entry_date") is not None:
            r["entry_date"] = str(r["entry_date"])
    return rows


def fetch_outcomes_missing_benchmark(conn) -> list[dict]:
    """Contract-v2 settled rows whose same-basis benchmark is still NULL."""
    from psycopg.rows import dict_row
    from .execution import EXECUTION_CONTRACT_VERSION

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT signal_id, horizon_days, entry_date, eval_date, realized_ret"
            " FROM signal_outcomes"
            " WHERE benchmark_ret IS NULL AND realized_ret IS NOT NULL"
            "   AND contract_version = %s",
            (EXECUTION_CONTRACT_VERSION,),
        )
        rows = cur.fetchall()
    for r in rows:
        if r.get("entry_date") is not None:
            r["entry_date"] = str(r["entry_date"])
        if r.get("eval_date") is not None:
            r["eval_date"] = str(r["eval_date"])
    return rows


def update_outcome_benchmark(
    conn,
    signal_id: int,
    horizon_days: int,
    benchmark_ret: float | None,
    excess_ret: float | None,
    benchmark_basis: str,
) -> None:
    """Idempotently write benchmark_ret/excess_ret/basis for one settled row."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE signal_outcomes"
            " SET benchmark_ret=%s, excess_ret=%s, benchmark_basis=%s"
            " WHERE signal_id=%s AND horizon_days=%s",
            (benchmark_ret, excess_ret, benchmark_basis, signal_id, horizon_days),
        )
    conn.commit()


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


def register_model_version(
    conn,
    version: str,
    *,
    kind: str,
    universe,
    feature_set,
    params,
    cv_metrics,
    calibration=None,
    artifact_uri=None,
    make_active: bool = True,
) -> None:
    """
    Upsert a model_registry row. When make_active, mark exactly this version
    active within its kind; active models of other kinds are untouched.
    """
    with conn.cursor() as cur:
        _upsert_model_registry(
            cur,
            _model_registry_row(
                version,
                kind=kind,
                universe=universe,
                feature_set=feature_set,
                params=params,
                cv_metrics=cv_metrics,
                calibration=calibration,
                artifact_uri=artifact_uri,
                make_active=make_active,
            ),
        )
    conn.commit()


def set_active_model(conn, version: str) -> None:
    """Activate ``version`` without disturbing active models of other kinds.

    The signature is retained for compatibility, but the target version must
    already exist so its registry kind can be resolved unambiguously.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT kind FROM model_registry WHERE version = %s FOR UPDATE",
            (version,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"model registry version not found: {version}")
        kind = row[0]
        cur.execute(
            "UPDATE model_registry SET active = (version = %s) WHERE kind = %s",
            (version, kind),
        )
    conn.commit()


def active_model_version(conn) -> str | None:
    """Return the newest active version across kinds (ambiguous legacy API).

    New callers should use :func:`active_model_version_for_kind`.
    """
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
    "brier": None,
    "brier_raw": None,
    "ic": None,
    "auc": None,
    "hit_rate": None,
    "calibration_rows": None,
    "psi_max": None,
    "warning": False,
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


def fetch_prediction_outcomes(
    conn, model_version: str, horizon_days: int
) -> list[dict]:
    """
    Outcomes for predictions actually selected by their signal.

    Drift must not pick a different Phase 1/CS prediction that merely shares a
    run date and ticker. Only outcomes settled under the current execution
    contract are comparable with the active model's probabilities.
    """
    from psycopg.rows import dict_row
    from .execution import EXECUTION_CONTRACT_VERSION

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT p.ticker, p.prob_up, p.raw_score, o.realized_ret, o.hit"
            " FROM signals s"
            " JOIN predictions p ON p.id = s.prediction_id"
            " JOIN signal_outcomes o ON o.signal_id = s.id"
            "  AND o.horizon_days = %(horizon)s"
            "  AND o.contract_version = %(contract)s"
            " WHERE p.model_version = %(version)s"
            "  AND p.horizon_days = %(horizon)s",
            {
                "horizon": horizon_days,
                "version": model_version,
                "contract": EXECUTION_CONTRACT_VERSION,
            },
        )
        return cur.fetchall()


def fetch_prediction_outcomes_for_kind(
    conn, model_kind: str, horizon_days: int
) -> list[dict]:
    """
    Outcomes pooled across every model version of one lineage (registry kind).

    Weekly retrains rotate ``model_version`` faster than 5-session outcomes can
    settle, so a per-version filter never accumulates a drift sample. Versions
    of the same kind share the training recipe and feature semantics, which
    keeps pooled IC/hit-rate comparable; the registry join also excludes the
    legacy version (never registered under this kind) and ``cs_rank IS NULL``
    keeps cross-sectional predictions out even if a kind ever collided.
    """
    from psycopg.rows import dict_row
    from .execution import EXECUTION_CONTRACT_VERSION

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT p.ticker, p.prob_up, p.raw_score, o.realized_ret, o.hit"
            " FROM signals s"
            " JOIN predictions p ON p.id = s.prediction_id"
            " JOIN model_registry mr ON mr.version = p.model_version"
            " JOIN signal_outcomes o ON o.signal_id = s.id"
            "  AND o.horizon_days = %(horizon)s"
            "  AND o.contract_version = %(contract)s"
            " WHERE mr.kind = %(kind)s"
            "  AND p.cs_rank IS NULL"
            "  AND p.horizon_days = %(horizon)s",
            {
                "horizon": horizon_days,
                "kind": model_kind,
                "contract": EXECUTION_CONTRACT_VERSION,
            },
        )
        return cur.fetchall()


def _is_phase1_prediction_row(row: dict) -> bool:
    """Return True only for per-ticker predictions used by Phase 1 signals."""
    if row.get("cs_rank") is not None:
        return False
    kind = str(row.get("model_kind") or "")
    version = str(row.get("model_version") or "")
    return (
        kind == PHASE1_MODEL_KIND
        or version == db_records.LEGACY_MODEL_VERSION
        or (
            kind == AUTO_STUB_MODEL_KIND
            and (
                version.startswith(PHASE1_MODEL_VERSION_PREFIX)
                or version.startswith(EPHEMERAL_PHASE1_MODEL_VERSION_PREFIX)
            )
        )
    )


def _as_probability(value) -> float | None:
    """Normalize a stored probability and reject NaN/out-of-range values."""
    try:
        probability = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        return None
    return probability


def _model_contract(row: dict) -> tuple[str, dict] | None:
    """
    Return the compatibility signature + JSON-safe contract for a prediction.

    ``model_registry.params.label_config`` is already persisted for Phase 1
    weekly models. Exact canonical JSON equality is deliberately conservative:
    differing label/barrier settings must never share a reliability bin. An
    optional execution_contract key is included automatically when later model
    manifests persist it. Old auto-stub/legacy rows have no such provenance and
    therefore cannot be proven compatible across model versions.
    """
    params = row.get("model_params")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError:
            return None
    if not isinstance(params, dict):
        return None

    label_config = params.get("label_config")
    if not isinstance(label_config, dict) or not label_config:
        return None

    contract = {
        "horizon_days": int(row["prediction_horizon_days"]),
        "label_config": label_config,
        "execution_contract_version": row.get("outcome_contract_version"),
    }
    execution_contract = params.get("execution_contract")
    if execution_contract is not None:
        contract["execution_contract"] = execution_contract
    try:
        signature = json.dumps(
            contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError):
        return None
    return signature, contract


def _select_signal_reliability_rows(source_rows: list[dict], horizon_days: int) -> dict:
    """
    Select traceable Phase 1 probabilities for reliability aggregation.

    The source of truth is ``signals.prediction_id``. ``signals.conviction`` is
    used only when that link is absent, never to mask a broken/non-Phase-1
    linked prediction. Among linked rows, the newest known label contract is
    the reference and compatible model versions are aggregated together.
    """
    exclusions: dict[str, int] = {}

    def exclude(reason: str) -> None:
        exclusions[reason] = exclusions.get(reason, 0) + 1

    linked_candidates: list[dict] = []
    fallback_candidates: list[dict] = []
    outcome_contract_versions = sorted(
        {
            str(row["outcome_contract_version"])
            for row in source_rows
            if row.get("outcome_contract_version") is not None
        }
    )
    outcome_contract_version = (
        outcome_contract_versions[0] if len(outcome_contract_versions) == 1 else None
    )

    for source in source_rows:
        realized_ret = source.get("realized_ret")
        if realized_ret is None:
            exclude("missing_realized_return")
            continue

        prediction_id = source.get("prediction_id")
        if prediction_id is None:
            fallback_prob = _as_probability(source.get("conviction"))
            if fallback_prob is None:
                exclude("missing_fallback_probability")
                continue
            fallback_candidates.append(
                {
                    **source,
                    "prob_up": fallback_prob,
                    "probability_source": "signals.conviction",
                    "model_version": None,
                }
            )
            continue

        if source.get("linked_prediction_id") is None:
            exclude("broken_prediction_link")
            continue
        if not _is_phase1_prediction_row(source):
            exclude("non_phase1_prediction")
            continue
        if int(source.get("prediction_horizon_days") or 0) != int(horizon_days):
            exclude("prediction_horizon_mismatch")
            continue

        probability = _as_probability(source.get("prediction_prob_up"))
        if probability is None:
            # The fallback is intentionally limited to absent prediction_id.
            exclude("missing_prediction_probability")
            continue

        contract_info = _model_contract(source)
        linked_candidates.append(
            {
                **source,
                "prob_up": probability,
                "probability_source": "predictions.prob_up",
                "_contract_signature": contract_info[0] if contract_info else None,
                "_contract": contract_info[1] if contract_info else None,
            }
        )

    known_contract_rows = [
        row for row in linked_candidates if row["_contract_signature"] is not None
    ]
    reference_signature = None
    reference_contract = None
    reference_version = None
    if known_contract_rows:
        reference = max(
            known_contract_rows,
            key=lambda row: (
                str(row.get("run_date") or ""),
                int(row.get("signal_id") or 0),
            ),
        )
        reference_signature = reference["_contract_signature"]
        reference_contract = reference["_contract"]
    elif linked_candidates:
        # With no persisted label contract, cross-version compatibility cannot
        # be established. Fail closed to the newest linked model version.
        reference = max(
            linked_candidates,
            key=lambda row: (
                str(row.get("run_date") or ""),
                int(row.get("signal_id") or 0),
            ),
        )
        reference_version = reference.get("model_version")
        reference_contract = {
            "horizon_days": int(horizon_days),
            "label_config": None,
            "execution_contract_version": outcome_contract_version,
            "compatibility_scope": "single_model_version_missing_contract",
        }
    else:
        reference_contract = {
            "horizon_days": int(horizon_days),
            "label_config": None,
            "execution_contract_version": outcome_contract_version,
            "compatibility_scope": "conviction_fallback_only",
        }

    selected_linked: list[dict] = []
    for row in linked_candidates:
        if reference_signature is not None:
            if row["_contract_signature"] is None:
                exclude("missing_compatibility_contract")
                continue
            if row["_contract_signature"] != reference_signature:
                exclude("incompatible_contract")
                continue
        elif row.get("model_version") != reference_version:
            exclude("missing_compatibility_contract")
            continue
        selected_linked.append(row)

    selected = selected_linked + fallback_candidates
    selected.sort(
        key=lambda row: (
            str(row.get("run_date") or ""),
            int(row.get("signal_id") or 0),
        )
    )

    version_counts: dict[str, int] = {}
    for row in selected_linked:
        version = str(row.get("model_version") or "unknown")
        version_counts[version] = version_counts.get(version, 0) + 1

    entry_dates = sorted(
        str(row["entry_date"]) for row in selected if row.get("entry_date") is not None
    )
    provenance = {
        "phase": "phase1",
        "source": "signals.prediction_id",
        "candidate_signal_count": len(source_rows),
        "observation_count": len(selected),
        "linked_prediction_count": len(selected_linked),
        "conviction_fallback_count": len(fallback_candidates),
        "excluded_count": sum(exclusions.values()),
        "exclusions": dict(sorted(exclusions.items())),
        "model_versions": [
            {"model_version": version, "count": count}
            for version, count in sorted(version_counts.items())
        ],
        "first_entry_date": entry_dates[0] if entry_dates else None,
        "last_entry_date": entry_dates[-1] if entry_dates else None,
        "outcome_contract_versions": outcome_contract_versions,
        "compatibility_contract": reference_contract,
        "fallback_contract_assumption": (
            "requested_outcome_horizon_and_execution_contract"
            if fallback_candidates
            else None
        ),
    }

    clean_rows = []
    for row in selected:
        clean_rows.append(
            {
                key: value
                for key, value in row.items()
                if not key.startswith("_contract")
            }
        )
    return {"rows": clean_rows, "provenance": provenance}


def fetch_signal_reliability_rows(
    conn, horizon_days: int = 5, history_days: int = 180
) -> dict:
    """
    Fetch Phase 1 reliability observations through ``signals.prediction_id``.

    The result contains selected rows plus provenance/fallback/exclusion counts
    suitable for embedding under ``performance_detail.reliability``. It never
    consults a generic active model and can aggregate compatible weekly model
    versions without admitting cross-sectional predictions.
    """
    from psycopg.rows import dict_row
    from .execution import EXECUTION_CONTRACT_VERSION

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT s.id AS signal_id, s.run_date, s.ticker, s.prediction_id,"
            " s.conviction, o.entry_date, o.realized_ret,"
            " o.contract_version AS outcome_contract_version,"
            " p.id AS linked_prediction_id, p.prob_up AS prediction_prob_up,"
            " p.model_version, p.horizon_days AS prediction_horizon_days, p.cs_rank,"
            " mr.kind AS model_kind, mr.params AS model_params"
            " FROM signal_outcomes o"
            " JOIN signals s ON s.id = o.signal_id"
            " LEFT JOIN predictions p ON p.id = s.prediction_id"
            " LEFT JOIN model_registry mr ON mr.version = p.model_version"
            " WHERE o.horizon_days = %(h)s"
            "   AND o.contract_version = %(contract)s"
            "   AND o.entry_date >= (CURRENT_DATE - %(d)s::int)"
            " ORDER BY s.run_date DESC, s.id DESC",
            {
                "h": horizon_days,
                "d": history_days,
                "contract": EXECUTION_CONTRACT_VERSION,
            },
        )
        rows = cur.fetchall()
    for row in rows:
        for key in ("run_date", "entry_date"):
            if row.get(key) is not None:
                row[key] = str(row[key])
    return _select_signal_reliability_rows(rows, horizon_days)


def fetch_outcome_detail_rows(
    conn, horizon_days: int = 5, history_days: int = 180
) -> list[dict]:
    """
    Joined rows for performance detail exports (Phase 3).

    Returns signal_outcomes joined with signals and tickers for the given
    horizon and date window. All fields needed by src/performance.py functions.
    entry_date is normalized to ISO string.
    """
    from psycopg.rows import dict_row
    from .execution import EXECUTION_CONTRACT_VERSION

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT o.market_as_of_date, o.entry_date, o.eval_date,"
            " o.entry_price, o.exit_price, o.entry_price_basis, o.exit_price_basis,"
            " o.contract_version, o.benchmark_basis,"
            " s.ticker, t.name, s.action,"
            " s.conviction, o.horizon_days,"
            " o.realized_ret, o.benchmark_ret, o.excess_ret, o.hit, o.mae, o.mfe, o.exit_reason"
            " FROM signal_outcomes o"
            " JOIN signals s ON s.id = o.signal_id"
            " LEFT JOIN tickers t ON t.code = s.ticker"
            " WHERE o.horizon_days = %(h)s AND o.entry_date >= (CURRENT_DATE - %(d)s::int)"
            "   AND o.contract_version = %(contract)s"
            " ORDER BY o.entry_date DESC, s.ticker",
            {
                "h": horizon_days,
                "d": history_days,
                "contract": EXECUTION_CONTRACT_VERSION,
            },
        )
        rows = cur.fetchall()
    for r in rows:
        for key in ("market_as_of_date", "entry_date", "eval_date"):
            if r.get(key) is not None:
                r[key] = str(r[key])
    return rows


def insert_drift_report(
    conn,
    run_date: str,
    model_version: str | None,
    scope: str,
    status: str,
    breached: bool,
    metrics: dict,
) -> None:
    from psycopg.types.json import Jsonb

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO drift_reports"
            " (run_date, model_version, scope, status, breached, metrics)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (run_date, model_version, scope, status, bool(breached), Jsonb(metrics)),
        )
    conn.commit()
