#!/usr/bin/env python3
"""
Weekly realized-signal performance summary via LINE (Phase 3, Task 6).

Fetches horizon-5 settled outcomes for the past 7 days, builds a short
summary via the PURE digest.build_weekly_summary, and sends it via LINE.

Runs as a ``continue-on-error`` weekly step — NEVER hard-fails (always exits 0).

Usage:
  uv run python scripts/weekly_performance_notify.py
  TRADER_DB_ENABLED=false uv run python scripts/weekly_performance_notify.py  # no-op
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db, digest, notifier  # noqa: E402
from scripts.curation_common import today_jst_iso  # noqa: E402


def main() -> int:
    try:
        if not db.db_enabled():
            print("weekly-perf-notify: DB disabled or DATABASE_URL unset; skipping.")
            return 0

        today = today_jst_iso()
        week_end = today
        week_start = (date.fromisoformat(today) - timedelta(days=7)).isoformat()

        try:
            conn = db.connect()
        except Exception as exc:  # noqa: BLE001
            print(f"weekly-perf-notify: DB connect failed ({type(exc).__name__}); skipping.")
            return 0

        try:
            rows = db.fetch_outcome_detail_rows(conn, horizon_days=5, history_days=7)
        finally:
            conn.close()

        # Best-effort: build the GitHub URL for this week's report.
        url = ""
        try:
            from scripts.curation_notify import report_url as _report_url  # noqa: E402
            url = _report_url(f"reports/weekly_{today}.md")
        except Exception:  # noqa: BLE001
            url = ""

        text = digest.build_weekly_summary(rows, week_start, week_end, url)
        if text is None:
            print("weekly perf: no actionable outcomes; skipping.")
            return 0

        ok = notifier.send_line_text(text)
        print(f"weekly-perf-notify: LINE send {'ok' if ok else 'failed (non-fatal)'}.")
        return 0

    except Exception as exc:  # noqa: BLE001 — last-resort guard: never fail the weekly step
        print(f"weekly-perf-notify: unexpected error ({type(exc).__name__}: {exc}); skipping.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
