#!/usr/bin/env python3
"""
Idempotency guard for the daily curation workflow.

Mirrors scripts/run_guard.py: prevents duplicate curation runs on the same JST
day (manual re-runs, retries). A run is considered done when a dated decision
log exists for today, or decision_latest.json carries today's date.

See specification_document/ai_ticker_curation/03_workflows_cicd.md (§6).
"""

from __future__ import annotations

import argparse
import os

from curation_common import CURATION_DIR, read_json, today_jst_iso


def _write_output(key: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def _already_ran(today: str) -> bool:
    if (CURATION_DIR / f"decision_{today}.json").exists():
        return True
    latest = read_json(CURATION_DIR / "decision_latest.json")
    return bool(latest and latest.get("date") == today)


def cmd_needs_run(args: argparse.Namespace) -> int:
    today = args.date or today_jst_iso()
    done = _already_ran(today)
    needs_run = not done
    reason = "already_curated_today" if done else "not_curated_today"
    print(
        f'{{"date": "{today}", "needs_run": {str(needs_run).lower()}, "reason": "{reason}"}}'
    )
    if args.github_output:
        _write_output("needs_run", "true" if needs_run else "false")
        _write_output("guard_reason", reason)
        _write_output("guard_date", today)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Curation workflow run guard")
    sub = p.add_subparsers(dest="command", required=True)
    n = sub.add_parser("needs-run")
    n.add_argument("--date", help="YYYY-MM-DD JST")
    n.add_argument("--github-output", action="store_true")
    n.set_defaults(func=cmd_needs_run)
    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
