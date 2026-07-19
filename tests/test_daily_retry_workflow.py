#!/usr/bin/env python3
"""
Regression guard for the daily retry workflow.

The retry is the same-day replacement for a failed core run. Its prediction
environment and data-producing side steps must therefore stay aligned with
core while remaining behind the needs-core-run guard.

Runnable: uv run python tests/test_daily_retry_workflow.py
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CORE = WORKFLOWS / "daily-preopen-core.yml"
RETRY = WORKFLOWS / "daily-preopen-retry.yml"
RETRY_RUN_IF = (
    "${{ steps.market.outputs.is_open == 'true' "
    "&& steps.guard.outputs.needs_run == 'true' }}"
)


def _steps(path: Path, job_name: str) -> list[dict]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload["jobs"][job_name]["steps"]


def _step(steps: list[dict], name: str) -> dict:
    return next(item for item in steps if item.get("name") == name)


def _core_steps() -> list[dict]:
    return _steps(CORE, "preopen-core")


def _retry_steps() -> list[dict]:
    return _steps(RETRY, "preopen-retry")


def test_retry_prediction_env_matches_core():
    core = _step(_core_steps(), "Run prediction script")
    retry = _step(_retry_steps(), "Run retry prediction script")
    assert retry["env"] == core["env"], (
        "retry main.py env drifted from core; update both workflows together"
    )
    assert retry["run"] == core["run"] == "uv run python main.py"


def test_retry_macro_and_settlement_match_core():
    core = _core_steps()
    retry = _retry_steps()
    for name in ("Update macro snapshots", "Settle realized outcomes"):
        core_step = _step(core, name)
        retry_step = _step(retry, name)
        assert retry_step["env"] == core_step["env"], f"{name} env differs from core"
        assert retry_step["run"] == core_step["run"], f"{name} command differs from core"
        assert retry_step.get("continue-on-error") is True


def test_retry_data_steps_are_guarded_and_ordered():
    steps = _retry_steps()
    names = [item.get("name") for item in steps]
    ordered = [
        "Update macro snapshots",
        "Run retry prediction script",
        "Settle realized outcomes",
        "Commit and push retry updates",
    ]
    assert [names.index(name) for name in ordered] == sorted(
        names.index(name) for name in ordered
    )
    for name in ordered:
        assert _step(steps, name).get("if") == RETRY_RUN_IF, (
            f"{name} must only run when the core output is missing"
        )


ALL_TESTS = [
    test_retry_prediction_env_matches_core,
    test_retry_macro_and_settlement_match_core,
    test_retry_data_steps_are_guarded_and_ordered,
]


def main() -> int:
    failures = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except (AssertionError, KeyError, StopIteration) as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
