#!/usr/bin/env python3
"""
Unit tests for main._prev_target_weights / db.fetch_latest_portfolio_snapshot.

A same-day rerun (retry or manual dispatch after today's Phase 2 snapshot was
already written) must diff against the previous session's book, not against
today's own snapshot — otherwise every diff collapses to "hold" and the shadow
turnover for that day is understated.

Runnable: uv run python tests/test_prev_target_weights.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
import src.dashboard as dashboard  # noqa: E402
import src.db as dbmod  # noqa: E402


class _Cur:
    def __init__(self, row):
        self.row = row
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row=None):
        self.cur = _Cur(row)

    def cursor(self, row_factory=None):
        return self.cur

    def close(self):
        pass


def _snapshot(run_date):
    return {
        "available": True,
        "run_date": run_date,
        "positions": [
            {"ticker": "7011.JP", "target_weight": 0.2, "prev_weight": 0.1},
            {"ticker": "6501.JP", "target_weight": 0.1, "prev_weight": 0.0},
            {"ticker": "8306.JP", "target_weight": 0.15, "prev_weight": 0.15},
        ],
    }


def _with_json_only(payload, fn):
    orig_enabled = main.db.db_enabled
    orig_file = dashboard.PORTFOLIO_LATEST_FILE
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "portfolio_latest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        main.db.db_enabled = lambda: False
        dashboard.PORTFOLIO_LATEST_FILE = path
        try:
            return fn()
        finally:
            main.db.db_enabled = orig_enabled
            dashboard.PORTFOLIO_LATEST_FILE = orig_file


def test_fetch_latest_snapshot_filters_before_run_date():
    conn = _Conn({"run_date": "2026-11-13"})
    dbmod.fetch_latest_portfolio_snapshot(conn, before_run_date="2026-11-16")
    assert "WHERE run_date < %(before)s" in conn.cur.sql, conn.cur.sql
    assert conn.cur.params == {"before": "2026-11-16"}
    assert conn.cur.sql.rstrip().endswith("ORDER BY run_date DESC LIMIT 1")


def test_fetch_latest_snapshot_unfiltered_by_default():
    conn = _Conn(None)
    assert dbmod.fetch_latest_portfolio_snapshot(conn) is None
    assert "WHERE" not in conn.cur.sql
    assert conn.cur.params is None


def test_db_path_passes_run_date():
    seen = {}

    def fake_fetch(conn, before_run_date=None):
        seen["before"] = before_run_date
        return {"positions": [{"ticker": "7011.JP", "target_weight": 0.3}]}

    orig = (main.db.db_enabled, main.db.connect, main.db.fetch_latest_portfolio_snapshot)
    main.db.db_enabled = lambda: True
    main.db.connect = lambda: _Conn()
    main.db.fetch_latest_portfolio_snapshot = fake_fetch
    try:
        got = main._prev_target_weights("2026-11-16")
    finally:
        (
            main.db.db_enabled,
            main.db.connect,
            main.db.fetch_latest_portfolio_snapshot,
        ) = orig
    assert seen["before"] == "2026-11-16"
    assert got == {"7011.JP": 0.3}


def test_json_fallback_previous_day_uses_target_weights():
    got = _with_json_only(
        _snapshot("2026-11-13"), lambda: main._prev_target_weights("2026-11-16")
    )
    assert got == {"7011.JP": 0.2, "6501.JP": 0.1, "8306.JP": 0.15}, got


def test_json_fallback_same_day_uses_prev_weights():
    got = _with_json_only(
        _snapshot("2026-11-16"), lambda: main._prev_target_weights("2026-11-16")
    )
    assert got == {"7011.JP": 0.1, "8306.JP": 0.15}, got


ALL_TESTS = [
    test_fetch_latest_snapshot_filters_before_run_date,
    test_fetch_latest_snapshot_unfiltered_by_default,
    test_db_path_passes_run_date,
    test_json_fallback_previous_day_uses_target_weights,
    test_json_fallback_same_day_uses_prev_weights,
]


def main_() -> int:
    failures = 0
    for t in ALL_TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main_())
