#!/usr/bin/env python3
"""
Regression guard for the Dependabot auto-merge classifier.

`.github/scripts/dependabot_bump_kind.py` decides whether a Dependabot PR is
merged automatically. A wrong "non-major" lets a breaking upgrade into `main`
unreviewed, so major and undeterminable bumps must stay manual. Requirement
range PRs ("Update X requirement from >=A to >=B") carry no update-type and are
judged from the versions in the message.

Runnable: uv run python tests/test_dependabot_bump_kind.py
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "dependabot_bump_kind.py"
WORKFLOW = ROOT / ".github" / "workflows" / "dependabot-auto-merge.yml"

_spec = importlib.util.spec_from_file_location("dependabot_bump_kind", SCRIPT)
bump = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump)

SIGNOFF = "\nSigned-off-by: dependabot[bot] <support@github.com>\n"

# PR #58 (2026-11-14): skipped by the old workflow for lack of update-type.
REQUIREMENT_PATCH = (
    "Update psycopg[binary] requirement from >=3.3.6 to >=3.3.8\n\n"
    "Updates the requirements on [psycopg[binary]](https://github.com/psycopg/psycopg)"
    " to permit the latest version.\n"
    "- [Changelog](https://github.com/psycopg/psycopg/blob/master/docs/news.rst)\n"
    "- [Commits](https://github.com/psycopg/psycopg/compare/3.3.6...3.3.8)\n\n"
    "---\n"
    "updated-dependencies:\n"
    "- dependency-name: psycopg[binary]\n"
    "  dependency-version: 3.3.8\n"
    "  dependency-type: direct:production\n"
    "...\n" + SIGNOFF
)

# PR #57: grouped, update-type present.
GROUP_PATCH = (
    "Bump pandas from 3.1.0 to 3.1.1 in the python-minor-patch group\n\n"
    "Bumps the python-minor-patch group with 1 update: [pandas](https://github.com/pandas-dev/pandas).\n\n\n"
    "Updates `pandas` from 3.1.0 to 3.1.1\n"
    "- [Release notes](https://github.com/pandas-dev/pandas/releases)\n\n"
    "---\n"
    "updated-dependencies:\n"
    "- dependency-name: pandas\n"
    "  dependency-version: 3.1.1\n"
    "  dependency-type: direct:production\n"
    "  update-type: version-update:semver-patch\n"
    "  dependency-group: python-minor-patch\n"
    "...\n" + SIGNOFF
)

# PR #54 shape: npm major with update-type.
NPM_MAJOR = (
    "Bump soupsieve from 3.0.4 to 4.0 in /web\n\n"
    "Bumps [soupsieve](https://github.com/facelessuser/soupsieve) from 3.0.4 to 4.0.\n\n"
    "---\n"
    "updated-dependencies:\n"
    "- dependency-name: soupsieve\n"
    "  dependency-version: '4.0'\n"
    "  dependency-type: indirect\n"
    "  update-type: version-update:semver-major\n"
    "...\n" + SIGNOFF
)


def _requirement(old: str, new: str, recorded: str, name: str = "foo") -> str:
    return (
        f"Update {name} requirement from {old} to {new}\n\n"
        "---\n"
        "updated-dependencies:\n"
        f"- dependency-name: {name}\n"
        f"  dependency-version: {recorded}\n"
        "  dependency-type: direct:production\n"
        "...\n" + SIGNOFF
    )


def test_requirement_patch_is_non_major():
    assert bump.classify(REQUIREMENT_PATCH) == "non-major"


def test_update_type_metadata_still_decides():
    assert bump.classify(GROUP_PATCH) == "non-major"
    assert bump.classify(NPM_MAJOR) == "major"


def test_requirement_major_stays_manual():
    assert bump.classify(_requirement(">=3.3.6", ">=4.0.0", "4.0.0")) == "major"


def test_zero_x_minor_counts_as_major():
    assert bump.classify(_requirement(">=0.4.1", ">=0.5.0", "0.5.0")) == "major"
    assert bump.classify(_requirement(">=0.4.1", ">=0.4.2", "0.4.2")) == "non-major"


def test_requirement_minor_is_non_major():
    assert bump.classify(_requirement(">=1.2.0", ">=1.3", "1.3")) == "non-major"
    assert bump.classify(_requirement("^1.2.0", "^1.4.1", "1.4.1")) == "non-major"


def test_ranges_and_prereleases_are_unknown():
    assert bump.classify(_requirement(">=1.2,<2", ">=1.3,<2", "1.3")) == "unknown"
    assert bump.classify(_requirement(">=1.2.0", ">=1.3.0rc1", "1.3.0rc1")) == "unknown"


def test_version_mismatch_or_downgrade_is_unknown():
    assert bump.classify(_requirement(">=1.2.0", ">=1.3.0", "2.0.0")) == "unknown"
    assert bump.classify(_requirement(">=1.3.0", ">=1.2.0", "1.2.0")) == "unknown"


def test_missing_metadata_or_versions_is_unknown():
    assert bump.classify("Update foo requirement from >=1.0 to >=1.1\n") == "unknown"
    no_versions = (
        "Bump the python-minor-patch group with 1 update\n\n"
        "---\nupdated-dependencies:\n- dependency-name: foo\n"
        "  dependency-version: 1.1.0\n...\n"
    )
    assert bump.classify(no_versions) == "unknown"


def test_group_with_one_undeterminable_entry_is_unknown():
    message = (
        "Bump the web-minor-patch group in /web with 2 updates\n\n"
        "Updates `next` from 16.4.5 to 16.4.6\n"
        "Updates `foo` to 1.2.0\n\n"
        "---\nupdated-dependencies:\n"
        "- dependency-name: next\n  dependency-version: 16.4.6\n"
        "  update-type: version-update:semver-patch\n"
        "- dependency-name: foo\n  dependency-version: 1.2.0\n"
        "...\n"
    )
    assert bump.classify(message) == "unknown"


def test_table_row_versions_are_read():
    message = (
        "Bump the web-minor-patch group in /web with 1 update\n\n"
        "| Package | From | To |\n| --- | --- | --- |\n"
        "| [lucide-react](https://github.com/lucide-icons/lucide) | `1.67.0` | `1.71.0` |\n\n"
        "---\nupdated-dependencies:\n"
        "- dependency-name: lucide-react\n  dependency-version: 1.71.0\n...\n"
    )
    assert bump.classify(message) == "non-major"


def test_workflow_uses_main_copy_of_classifier():
    payload = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = payload["jobs"]["auto-merge"]["steps"]
    checkout = next(
        step for step in steps if "actions/checkout" in step.get("uses", "")
    )
    # No ref: workflow_run checks out the default branch, never the PR head.
    assert "ref" not in checkout.get("with", {}), checkout
    assert checkout["with"]["persist-credentials"] is False
    merge = next(step for step in steps if step.get("name") == "Merge if eligible")
    assert "dependabot_bump_kind.py" in merge["run"]
    assert "non-major) ;;" in merge["run"]
    assert "--match-head-commit" in merge["run"]


ALL_TESTS = [
    test_requirement_patch_is_non_major,
    test_update_type_metadata_still_decides,
    test_requirement_major_stays_manual,
    test_zero_x_minor_counts_as_major,
    test_requirement_minor_is_non_major,
    test_ranges_and_prereleases_are_unknown,
    test_version_mismatch_or_downgrade_is_unknown,
    test_missing_metadata_or_versions_is_unknown,
    test_group_with_one_undeterminable_entry_is_unknown,
    test_table_row_versions_are_read,
    test_workflow_uses_main_copy_of_classifier,
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
