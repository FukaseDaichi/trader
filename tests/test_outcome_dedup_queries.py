#!/usr/bin/env python3
"""
Performance / digest outcome queries count one trade once.

A run on stale prices re-issues a signal for a market date an earlier run
already covered; both settle to the same entry/exit. Drift already keeps one
outcome per (ticker, entry_date) (``_DRIFT_ONE_OUTCOME_PER_ENTRY``, 2026-10-22);
the performance summary/detail queries must do the same or the published hit
rate, mean return and equity curve double-weight re-issued trades.

Runnable: uv run python tests/test_outcome_dedup_queries.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.db as dbmod  # noqa: E402


class _Cur:
    def __init__(self):
        self.sql = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())

    def fetchall(self):
        return []


class _Conn:
    def __init__(self):
        self.cur = _Cur()

    def cursor(self, row_factory=None):
        return self.cur


def test_outcome_rows_one_per_trade_and_horizon():
    conn = _Conn()
    dbmod.fetch_outcome_rows(conn)
    sql = conn.cur.sql
    assert "SELECT DISTINCT ON (s.ticker, o.entry_date, o.horizon_days)" in sql, sql
    assert "ORDER BY s.ticker, o.entry_date, o.horizon_days, s.run_date, s.id" in sql


def test_outcome_detail_rows_one_per_trade_keeps_display_order():
    conn = _Conn()
    dbmod.fetch_outcome_detail_rows(conn, horizon_days=5, history_days=7)
    sql = conn.cur.sql
    assert "SELECT DISTINCT ON (s.ticker, o.entry_date)" in sql, sql
    assert "ORDER BY s.ticker, o.entry_date, s.run_date, s.id" in sql
    assert sql.endswith("ORDER BY entry_date DESC, ticker"), sql


ALL_TESTS = [
    test_outcome_rows_one_per_trade_and_horizon,
    test_outcome_detail_rows_one_per_trade_keeps_display_order,
]


def main() -> int:
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
    sys.exit(main())
