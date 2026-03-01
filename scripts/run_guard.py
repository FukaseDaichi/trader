#!/usr/bin/env python3
"""
Helpers for workflow guard decisions.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _today_jst_iso() -> str:
    return (datetime.now(UTC) + timedelta(hours=9)).strftime("%Y-%m-%d")


def _write_output(key: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _has_today_entry(state: dict, today: str) -> bool:
    history = state.get("history")
    if not isinstance(history, list):
        return False
    for item in history:
        if isinstance(item, dict) and item.get("date") == today:
            return True
    return False


def cmd_needs_core_run(args: argparse.Namespace) -> int:
    today = args.date or _today_jst_iso()
    state = _read_state(Path(args.state_file))
    has_today = _has_today_entry(state, today)
    needs_run = not has_today

    result = {
        "date": today,
        "needs_run": needs_run,
        "reason": "already_updated_today" if has_today else "missing_today_entry",
    }
    print(json.dumps(result, ensure_ascii=False))

    if args.github_output:
        _write_output("needs_run", "true" if needs_run else "false")
        _write_output("guard_reason", result["reason"])
        _write_output("guard_date", today)
    return 0


def cmd_has_today_update(args: argparse.Namespace) -> int:
    today = args.date or _today_jst_iso()
    state = _read_state(Path(args.state_file))
    has_today = _has_today_entry(state, today)
    result = {
        "date": today,
        "has_today_update": has_today,
        "reason": "updated_today" if has_today else "not_updated_today",
    }
    print(json.dumps(result, ensure_ascii=False))

    if args.github_output:
        _write_output("has_today_update", "true" if has_today else "false")
        _write_output("guard_reason", result["reason"])
        _write_output("guard_date", today)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Workflow run guard utilities")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_needs = subparsers.add_parser("needs-core-run")
    p_needs.add_argument("--state-file", default="docs/state.json")
    p_needs.add_argument("--date", help="YYYY-MM-DD JST")
    p_needs.add_argument("--github-output", action="store_true")
    p_needs.set_defaults(func=cmd_needs_core_run)

    p_has = subparsers.add_parser("has-today-update")
    p_has.add_argument("--state-file", default="docs/state.json")
    p_has.add_argument("--date", help="YYYY-MM-DD JST")
    p_has.add_argument("--github-output", action="store_true")
    p_has.set_defaults(func=cmd_has_today_update)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
