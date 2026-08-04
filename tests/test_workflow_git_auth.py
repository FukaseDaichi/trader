#!/usr/bin/env python3
"""Regression guards for workflows where Claude Action and git push coexist."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def _claude_push_workflows() -> list[Path]:
    paths = []
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if (
            "anthropics/claude-code-action@" in text
            and "commit-and-push.sh" in text
        ):
            paths.append(path)
    return paths


def _checkout_step(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "uses: actions/checkout@" not in line:
            continue

        action_indent = len(line) - len(line.lstrip())
        step_indent = max(action_indent - 2, 0)
        end = len(lines)
        for candidate in range(index + 1, len(lines)):
            stripped = lines[candidate].lstrip()
            indent = len(lines[candidate]) - len(stripped)
            if stripped.startswith("- ") and indent <= step_indent:
                end = candidate
                break
        return "\n".join(lines[index:end])

    raise AssertionError("checkout action is missing")


def test_claude_push_workflows_disable_checkout_credentials():
    paths = _claude_push_workflows()
    assert paths, "expected at least one Claude Action workflow that pushes"

    failures = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if "persist-credentials: false" not in _checkout_step(text):
            failures.append(path.name)

    assert not failures, (
        "Claude Action workflows must disable checkout credential persistence "
        "so commit-and-push.sh is the sole auth owner: " + ", ".join(failures)
    )


def test_claude_push_workflows_supply_push_token():
    paths = _claude_push_workflows()
    assert paths, "expected at least one Claude Action workflow that pushes"

    failures = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" not in text:
            failures.append(path.name)

    assert not failures, (
        "Claude Action workflows must supply GH_TOKEN to commit-and-push.sh: "
        + ", ".join(failures)
    )


ALL_TESTS = [
    test_claude_push_workflows_disable_checkout_credentials,
    test_claude_push_workflows_supply_push_token,
]


def main() -> int:
    failures = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
