#!/usr/bin/env python3
"""
Unit tests for the outbox replay hardening in src/db.py (no real DB).

Covers the 2026-07 incident: FK violations (unknown ticker / unregistered
model_version) poisoned the all-or-nothing outbox replay, so DB write-through
silently stalled for weeks while the outbox grew daily.

  1. _apply_events must ensure FK parent rows (tickers / model_registry)
     before upserting predictions/signals.
  2. flush_outbox must apply events one-by-one (savepoints), quarantine
     poison events to data/outbox/dead/ and still clear the main outbox.

Runnable two ways:
  uv run python tests/test_db_outbox_replay.py
  uv run pytest tests/test_db_outbox_replay.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import json  # noqa: E402
import os  # noqa: E402
import tempfile  # noqa: E402

import src.db as dbmod  # noqa: E402


class FakeFKViolation(Exception):
    pass


class FakeCursor:
    """Records executed SQL; raises on configured poison tickers."""

    def __init__(self, fail_tickers=(), fail_kinds=("predictions", "signals")):
        self.executed: list[tuple[str, object]] = []
        self.fail_tickers = set(fail_tickers)
        self.fail_kinds = set(fail_kinds)
        self._pred_id = 0
        self._last_returning = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._last_returning = None
        if "INSERT INTO predictions" in sql and isinstance(params, dict):
            if (
                "predictions" in self.fail_kinds
                and params.get("ticker") in self.fail_tickers
            ):
                raise FakeFKViolation(f"fk violation for {params.get('ticker')}")
            self._pred_id += 1
            self._last_returning = (self._pred_id,)
        elif "INSERT INTO signals" in sql and isinstance(params, dict):
            if (
                "signals" in self.fail_kinds
                and params.get("ticker") in self.fail_tickers
            ):
                raise FakeFKViolation(f"fk violation for {params.get('ticker')}")

    fail_parent_stubs = False

    def executemany(self, sql, rows):
        self.executed.append((sql, list(rows)))
        if self.fail_parent_stubs and (
            "INSERT INTO tickers" in sql or "INSERT INTO model_registry" in sql
        ):
            raise FakeFKViolation("parent stub insert failed")

    def fetchone(self):
        return self._last_returning

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1


def _pred_event(ticker, model_version="per-ticker-v1-20260704", run_date="2026-07-09"):
    return {
        "event_id": f"{run_date}:{ticker}:pred",
        "kind": "prediction",
        "row": {
            "run_date": run_date,
            "as_of_date": "2026-07-08",
            "ticker": ticker,
            "model_version": model_version,
            "horizon_days": 5,
            "raw_score": 0.5,
            "prob_up": 0.6,
            "expected_ret": 0.01,
            "cs_rank": None,
            "features_hash": None,
        },
    }


def _sig_event(ticker, run_date="2026-07-09"):
    return {
        "event_id": f"{run_date}:{ticker}:sig",
        "kind": "signal",
        "row": {
            "run_date": run_date,
            "as_of_date": "2026-07-08",
            "ticker": ticker,
            "action": "MILD_BUY",
            "raw_action": "MILD_BUY",
            "conviction": 0.6,
            "target_weight": None,
            "thresholds": None,
            "gate_passed": True,
            "limit_price": None,
            "stop_loss": None,
            "reason": "test",
            "status": "ok",
        },
    }


def test_apply_events_ensures_fk_parents():
    """Before any prediction/signal upsert, missing tickers and model
    versions must be inserted with ON CONFLICT DO NOTHING stubs."""
    cur = FakeCursor()
    conn = FakeConn(cur)
    events = [
        _pred_event("8604.JP", model_version="per-ticker-v1-20260613"),
        _pred_event("4452.JP", model_version="cs-v1-20260610"),
        _sig_event("8604.JP"),
    ]
    applied = dbmod._apply_events(conn, events)
    assert applied == 3

    ticker_stmts = [(sql, p) for sql, p in cur.executed if "INSERT INTO tickers" in sql]
    assert ticker_stmts, "no tickers stub insert executed"
    sql, rows = ticker_stmts[0]
    assert "ON CONFLICT" in sql and "DO NOTHING" in sql
    codes = {r[0] for r in rows}
    assert codes == {"8604.JP", "4452.JP"}

    model_stmts = [
        (sql, p) for sql, p in cur.executed if "INSERT INTO model_registry" in sql
    ]
    assert model_stmts, "no model_registry stub insert executed"
    sql, rows = model_stmts[0]
    assert "ON CONFLICT" in sql and "DO NOTHING" in sql
    versions = {r[0] for r in rows}
    assert versions == {"per-ticker-v1-20260613", "cs-v1-20260610"}

    # Parent stubs must come before the first child upsert.
    first_child = min(
        i
        for i, (sql, _) in enumerate(cur.executed)
        if "INSERT INTO predictions" in sql or "INSERT INTO signals" in sql
    )
    first_parent = min(
        i
        for i, (sql, _) in enumerate(cur.executed)
        if "INSERT INTO tickers" in sql or "INSERT INTO model_registry" in sql
    )
    assert first_parent < first_child


def _with_outbox(tmp, events):
    out = Path(tmp) / "outbox"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "20260620000000.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    os.environ["TRADER_DB_FALLBACK_DIR"] = str(out)
    return out


def test_flush_outbox_quarantines_poison_events():
    """One poison event must not block the rest: good events apply, the bad
    one lands in dead/, and the main outbox is cleared."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = _with_outbox(
                tmp,
                [
                    _pred_event("7011.JP"),
                    _pred_event("BAD.JP"),
                    _sig_event("7011.JP"),
                ],
            )
            cur = FakeCursor(fail_tickers={"BAD.JP"})
            conn = FakeConn(cur)
            applied = dbmod.flush_outbox(conn)
            assert applied == 2, f"expected 2 applied, got {applied}"

            # Main outbox cleared.
            assert list(out.glob("*.jsonl")) == []

            # Poison event quarantined with a reason.
            dead_files = list((out / "dead").glob("*.jsonl"))
            assert len(dead_files) == 1, dead_files
            dead = [
                json.loads(line)
                for line in dead_files[0].read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            assert len(dead) == 1
            assert dead[0]["row"]["ticker"] == "BAD.JP"
            assert "FakeFKViolation" in dead[0]["dead_reason"]

            # Savepoint rollback happened (not a whole-transaction abort).
            stmts = [sql for sql, _ in cur.executed]
            assert any("SAVEPOINT" in s for s in stmts)
            assert any("ROLLBACK TO SAVEPOINT" in s for s in stmts)
            assert conn.commits >= 1
        finally:
            os.environ.pop("TRADER_DB_FALLBACK_DIR", None)


def test_flush_outbox_all_good_leaves_no_dead_letters():
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = _with_outbox(tmp, [_pred_event("7011.JP"), _sig_event("7011.JP")])
            conn = FakeConn(FakeCursor())
            applied = dbmod.flush_outbox(conn)
            assert applied == 2
            assert list(out.glob("*.jsonl")) == []
            assert (
                not (out / "dead").exists()
                or list((out / "dead").glob("*.jsonl")) == []
            )
        finally:
            os.environ.pop("TRADER_DB_FALLBACK_DIR", None)


def test_flush_outbox_dedups_by_event_id():
    """The same event queued twice (e.g. retry day) must apply once."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            ev = _pred_event("7011.JP")
            _with_outbox(tmp, [ev, ev])
            conn = FakeConn(FakeCursor())
            applied = dbmod.flush_outbox(conn)
            assert applied == 1
        finally:
            os.environ.pop("TRADER_DB_FALLBACK_DIR", None)


def test_flush_outbox_survives_parent_stub_failure():
    """If the FK-parent stub insert itself fails, the replay must degrade to
    per-event quarantine — not revert to the all-or-nothing stall."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = _with_outbox(tmp, [_pred_event("7011.JP"), _sig_event("7011.JP")])
            cur = FakeCursor()
            cur.fail_parent_stubs = True
            conn = FakeConn(cur)
            applied = dbmod.flush_outbox(conn)
            # Parents were "already seeded" in this scenario, so both events
            # still apply individually.
            assert applied == 2, f"expected 2 applied, got {applied}"
            assert list(out.glob("*.jsonl")) == []
            assert conn.commits >= 1
        finally:
            os.environ.pop("TRADER_DB_FALLBACK_DIR", None)


ALL_TESTS = [
    test_apply_events_ensures_fk_parents,
    test_flush_outbox_survives_parent_stub_failure,
    test_flush_outbox_quarantines_poison_events,
    test_flush_outbox_all_good_leaves_no_dead_letters,
    test_flush_outbox_dedups_by_event_id,
]


def main() -> int:
    failures = 0
    for t in ALL_TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
