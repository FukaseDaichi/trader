#!/usr/bin/env python3
"""
Regression test for scripts/db_migrate.py seeding helpers (no real DB).

2026-07-09: manual-db-migrate crashed with AttributeError because
LEGACY_MODEL_VERSION moved from src.db to src.db_records, leaving the legacy
model row unseeded after a re-seed run.

Runnable two ways:
  uv run python tests/test_db_migrate_seed.py
  uv run pytest tests/test_db_migrate_seed.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import db_migrate  # noqa: E402


class FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def executemany(self, sql, rows):
        self.executed.append((sql, list(rows)))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self):
        self._cursor = FakeCursor()
        self.commits = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1


def test_seed_legacy_model_references_existing_constant():
    conn = FakeConn()
    db_migrate._seed_legacy_model(conn)
    stmts = [(sql, p) for sql, p in conn._cursor.executed]
    assert len(stmts) == 1
    sql, params = stmts[0]
    assert "INSERT INTO model_registry" in sql
    assert params[0] == "legacy-daily-v0"
    assert conn.commits == 1


ALL_TESTS = [test_seed_legacy_model_references_existing_constant]


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
