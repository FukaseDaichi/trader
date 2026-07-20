#!/usr/bin/env python3
"""Model-registry outbox replay tests; no real database or network."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import db, model_store  # noqa: E402


PHASE1_KIND = "per_ticker_horizon_v1"
CS_KIND = "cross_sectional_ranker_v1"


class RegistryPoison(Exception):
    pass


class FakeRegistryCursor:
    def __init__(self, *, fail_versions=()):
        self.executed: list[tuple[str, object]] = []
        self.fail_versions = set(fail_versions)
        self._last_fetchone = None
        self.rows = {
            "phase1-old": {"kind": PHASE1_KIND, "active": True},
            "cs-current": {"kind": CS_KIND, "active": True},
        }

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._last_fetchone = None
        if "SELECT kind FROM model_registry" in sql:
            row = self.rows.get(params[0])
            self._last_fetchone = (row["kind"],) if row is not None else None
        elif "INSERT INTO model_registry" in sql and isinstance(params, tuple):
            version, kind = params[0], params[1]
            if version in self.fail_versions:
                raise RegistryPoison(f"invalid registry row: {version}")
            current_active = self.rows.get(version, {}).get("active", False)
            self.rows[version] = {
                "kind": kind,
                # ON CONFLICT doesn't overwrite active; a new row uses the
                # supplied value before the scoped UPDATE below.
                "active": current_active if version in self.rows else bool(params[8]),
                "params": params[4].obj,
            }
        elif "UPDATE model_registry SET active = (version = %s) WHERE kind = %s" in sql:
            version, kind = params
            for candidate, row in self.rows.items():
                if row["kind"] == kind:
                    row["active"] = candidate == version
        elif "UPDATE model_registry SET active = FALSE" in sql:
            version, kind = params
            if version in self.rows and self.rows[version]["kind"] == kind:
                self.rows[version]["active"] = False

    def executemany(self, sql, rows):
        self.executed.append((sql, list(rows)))

    def fetchone(self):
        return self._last_fetchone

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1


def _registry_args(version: str) -> dict:
    return {
        "version": version,
        "kind": PHASE1_KIND,
        "universe": ["7011.JP"],
        "feature_set": ["return_5d"],
        "params": {
            "file_active_pointer": {
                "version": version,
                "manifest_sha256": "manifest-sha",
                "config_sha256": "config-sha",
            }
        },
        "cv_metrics": {"aggregate": {"median_ic": 0.1}},
        "calibration": {"7011.JP": None},
        "artifact_uri": f"data/models/{version}/metadata.json",
        "make_active": True,
    }


def _write_events(outbox: Path, events: list[dict]) -> None:
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / "20260720000000.jsonl"
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )


def test_registry_queue_replays_idempotently_and_preserves_other_kind() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "outbox"
        env = {
            "TRADER_DB_ENABLED": "false",
            "TRADER_DB_FALLBACK_DIR": str(outbox),
            "TRADER_MODEL_DIR": str(Path(tmp) / "models"),
        }
        args = _registry_args("phase1-new")
        with _patched_env(env):
            model_store.write_active_model(
                "phase1-new",
                {
                    "manifest_sha256": "manifest-sha",
                    "config_sha256": "config-sha",
                },
            )
            first = db.queue_model_registry_event(**args)
            second = db.queue_model_registry_event(**args)
            assert first["ok"] is True and second["ok"] is True
            assert first["event_id"] == second["event_id"]

            cursor = FakeRegistryCursor()
            applied = db.flush_outbox(FakeConn(cursor))

        assert applied == 1
        assert cursor.rows["phase1-new"]["active"] is True
        assert cursor.rows["phase1-old"]["active"] is False
        assert cursor.rows["cs-current"]["active"] is True
        assert (
            cursor.rows["phase1-new"]["params"]["file_active_pointer"]
            == (args["params"]["file_active_pointer"])
        )

        inserts = [
            sql for sql, _ in cursor.executed if "INSERT INTO model_registry" in sql
        ]
        assert len(inserts) == 1
        assert "ON CONFLICT (version) DO UPDATE" in inserts[0]
        scoped_updates = [
            (sql, params)
            for sql, params in cursor.executed
            if "UPDATE model_registry SET active" in sql
        ]
        assert scoped_updates == [
            (
                "UPDATE model_registry SET active = (version = %s) WHERE kind = %s",
                ("phase1-new", PHASE1_KIND),
            )
        ]
        assert list(outbox.glob("*.jsonl")) == []


def test_registry_poison_is_quarantined_without_blocking_valid_event() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "outbox"
        bad = db.build_model_registry_event(**_registry_args("phase1-bad"))
        good = db.build_model_registry_event(**_registry_args("phase1-good"))
        _write_events(outbox, [bad, good])

        with _patched_env(
            {
                "TRADER_DB_FALLBACK_DIR": str(outbox),
                "TRADER_MODEL_DIR": str(Path(tmp) / "models"),
            }
        ):
            model_store.write_active_model(
                "phase1-good",
                {
                    "manifest_sha256": "manifest-sha",
                    "config_sha256": "config-sha",
                },
            )
            cursor = FakeRegistryCursor(fail_versions={"phase1-bad"})
            applied = db.flush_outbox(FakeConn(cursor))

        assert applied == 1
        assert cursor.rows["phase1-good"]["active"] is True
        assert cursor.rows["cs-current"]["active"] is True
        dead_files = list((outbox / "dead").glob("*.jsonl"))
        assert len(dead_files) == 1
        dead = json.loads(dead_files[0].read_text(encoding="utf-8").strip())
        assert dead["row"]["version"] == "phase1-bad"
        assert "RegistryPoison" in dead["dead_reason"]
        assert list(outbox.glob("*.jsonl")) == []


def test_stale_registry_event_is_consumed_but_forced_inactive() -> None:
    """An older queued event must not roll DB active behind the file pointer."""
    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "outbox"
        stale = db.build_model_registry_event(**_registry_args("phase1-old"))
        _write_events(outbox, [stale])
        env = {
            "TRADER_DB_FALLBACK_DIR": str(outbox),
            "TRADER_MODEL_DIR": str(Path(tmp) / "models"),
        }
        with _patched_env(env):
            model_store.write_active_model(
                "phase1-new",
                {
                    "manifest_sha256": "new-manifest-sha",
                    "config_sha256": "new-config-sha",
                },
            )
            cursor = FakeRegistryCursor()
            applied = db.flush_outbox(FakeConn(cursor))

        assert applied == 1
        assert cursor.rows["phase1-old"]["active"] is False
        assert cursor.rows["cs-current"]["active"] is True
        assert not any("active = (version = %s)" in sql for sql, _ in cursor.executed)
        assert any("active = FALSE" in sql for sql, _ in cursor.executed)
        assert list(outbox.glob("*.jsonl")) == []


def test_legacy_set_active_model_resolves_and_updates_only_target_kind() -> None:
    cursor = FakeRegistryCursor()
    cursor.rows["phase1-new"] = {"kind": PHASE1_KIND, "active": False}
    conn = FakeConn(cursor)

    db.set_active_model(conn, "phase1-new")

    assert cursor.rows["phase1-old"]["active"] is False
    assert cursor.rows["phase1-new"]["active"] is True
    assert cursor.rows["cs-current"]["active"] is True
    assert conn.commits == 1
    assert cursor.executed[-1] == (
        "UPDATE model_registry SET active = (version = %s) WHERE kind = %s",
        ("phase1-new", PHASE1_KIND),
    )


def test_legacy_set_active_model_rejects_unknown_version() -> None:
    cursor = FakeRegistryCursor()
    conn = FakeConn(cursor)

    try:
        db.set_active_model(conn, "missing")
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("unknown model registry version must be rejected")

    assert conn.commits == 0
    assert not any("UPDATE model_registry" in sql for sql, _ in cursor.executed)


class _patched_env:
    def __init__(self, updates: dict[str, str]):
        self.updates = updates
        self.previous: dict[str, str | None] = {}

    def __enter__(self):
        for key, value in self.updates.items():
            self.previous[key] = os.environ.get(key)
            os.environ[key] = value

    def __exit__(self, *_exc):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


ALL_TESTS = [
    test_registry_queue_replays_idempotently_and_preserves_other_kind,
    test_registry_poison_is_quarantined_without_blocking_valid_event,
    test_stale_registry_event_is_consumed_but_forced_inactive,
    test_legacy_set_active_model_resolves_and_updates_only_target_kind,
    test_legacy_set_active_model_rejects_unknown_version,
]


def main() -> int:
    failures = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
