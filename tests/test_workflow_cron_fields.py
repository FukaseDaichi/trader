#!/usr/bin/env python3
"""
Guard against the cron day-of-month / day-of-week OR trap.

When a cron restricts BOTH day-of-month and day-of-week, it fires when EITHER
field matches. ``0 0 1-7 * 0`` therefore ran on days 1-7 every day plus every
Sunday (~11 runs/month) instead of "first Sunday" (Monthly Full Audit,
2026-10/11). Restrict one field in cron and do the other check in a step.

Runnable: uv run python tests/test_workflow_cron_fields.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _schedules():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        triggers = payload.get("on", payload.get(True)) or {}
        if not isinstance(triggers, dict):
            continue
        for item in triggers.get("schedule") or []:
            yield path.name, item["cron"]


def test_no_cron_restricts_both_day_fields():
    bad = []
    for name, cron in _schedules():
        fields = cron.split()
        assert len(fields) == 5, f"{name}: unexpected cron {cron!r}"
        _minute, _hour, dom, _month, dow = fields
        if dom != "*" and dow != "*":
            bad.append(f"{name}: {cron!r}")
    assert not bad, (
        "cron with both day-of-month and day-of-week fires on EITHER match: "
        + ", ".join(bad)
    )


def test_monthly_audit_guards_first_sunday():
    text = (WORKFLOWS / "monthly-full-audit.yml").read_text(encoding="utf-8")
    assert "First Sunday guard" in text
    assert "steps.guard.outputs.run == 'true'" in text


ALL_TESTS = [
    test_no_cron_restricts_both_day_fields,
    test_monthly_audit_guards_first_sunday,
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
