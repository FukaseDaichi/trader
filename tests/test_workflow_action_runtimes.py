#!/usr/bin/env python3
"""Regression guard for GitHub Actions that still embed deprecated Node 20."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

# First releases of these actions whose JavaScript runtime is Node 24.
MIN_NODE24_MAJOR = {
    "actions/checkout": 5,
    "actions/setup-node": 5,
    "astral-sh/setup-uv": 7,
}
ACTION_REF_RE = re.compile(r"uses:\s*([^\s@]+)@v(\d+)(?:[.\s]|$)")


def test_direct_javascript_actions_use_node24_releases():
    checked = {action: 0 for action in MIN_NODE24_MAJOR}
    failures = []

    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        for action, major_text in ACTION_REF_RE.findall(
            path.read_text(encoding="utf-8")
        ):
            if action not in MIN_NODE24_MAJOR:
                continue
            checked[action] += 1
            major = int(major_text)
            minimum = MIN_NODE24_MAJOR[action]
            if major < minimum:
                failures.append(f"{path.name}: {action}@v{major} < v{minimum}")

    assert all(checked.values()), f"expected action references missing: {checked}"
    assert not failures, "Node 20 action reference(s): " + ", ".join(failures)


ALL_TESTS = [test_direct_javascript_actions_use_node24_releases]


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
