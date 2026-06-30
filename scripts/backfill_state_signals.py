#!/usr/bin/env python3
"""
Backfill the measurement DB from docs/state.json (Phase 1 Task 0).

Seeds the recent signal history (up to ~30 days) into the `signals` /
`predictions` tables so the realized-outcome ledger and A/B verification have
data before the live write-through accumulates it. Outcomes are filled
afterwards by scripts/settle_outcomes.py.

Idempotent: uses the same upserts as the daily write-through, so re-running
does not create duplicates.

Exits 0 (no-op) when the DB is disabled / unreachable.

Usage:
  uv run python scripts/backfill_state_signals.py
  uv run python scripts/backfill_state_signals.py --state docs/state.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.config import STATE_FILE  # noqa: E402
from scripts.curation_common import read_json  # noqa: E402


def _history_days(state: dict) -> list[dict]:
    history = state.get("history") if isinstance(state, dict) else None
    if not isinstance(history, list):
        return []
    days = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        date = entry.get("date")
        signals = entry.get("signals")
        if not date or not isinstance(signals, list):
            continue
        # run_date is the day label; each signal carries its own as_of date.
        days.append({"run_date": date, "signals": signals})
    return days


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill signals/predictions from state.json"
    )
    parser.add_argument("--state", default=str(STATE_FILE))
    args = parser.parse_args()

    if not db.db_enabled():
        print("DB disabled or DATABASE_URL unset; skipping backfill.")
        return 0

    state = read_json(Path(args.state))
    if not state:
        print(f"No state file at {args.state}; nothing to backfill.")
        return 0

    days = _history_days(state)
    if not days:
        print("state.json has no usable history; nothing to backfill.")
        return 0

    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not connect for backfill (ignored): {type(exc).__name__}: {exc}")
        return 0

    try:
        result = db.apply_signal_history(conn, days)
        print(
            f"Backfill from {args.state}: {len(days)} day(s), "
            f"{result['events']} events, {result['applied']} upserts, "
            f"{result['linked']} prediction links."
        )
        print("Run scripts/settle_outcomes.py next to fill realized outcomes.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
