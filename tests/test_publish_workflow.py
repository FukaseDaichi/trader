#!/usr/bin/env python3
"""
Guard: every docs/-root JSON the pipeline writes must be excluded from the
publish workflow's `rsync --delete`, or it is wiped on the next publish
(this actually happened to Phase 1/2 outputs on 2026-06-10, commit 2e63ff2).

Runnable: uv run python tests/test_publish_workflow.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "daily-publish-dashboard.yml"

# Canonical list of docs/-root JSON files written by the pipeline.
# When you add a new export, add it HERE and to the workflow excludes.
EXPECTED_PRESERVED = {
    "state.json", "backtest_report.json", "performance_summary.json",
    "monthly_audit.json", "universe_refresh_report.json",
    "weekly_retrain_report.json", "feature_precompute_report.json",
    "rotating_refresh_report.json", "stress_test_report.json",
    "model_quality.json", "drift_report.json",
    "portfolio_latest.json", "portfolio_backtest.json",
    "cs_model_quality.json", "portfolio_shadow_report.json",
    "performance_detail.json", "signal_outcomes_recent.json",
}

# Served from web/public via the build instead of the exclude list.
BUILD_EMBEDDED = {"dashboard_index.json"}

# Legacy literals still present in code but no longer published to docs/
# (src/dashboard.py export_history_data / LEGACY_HISTORY_FILE).
LEGACY_NOT_PUBLISHED = {"history_data.json"}


def _workflow_excludes() -> set[str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    return set(re.findall(r"--exclude '([^']+)'", text))


def _json_literals_in_code() -> set[str]:
    """Scan src/ and scripts/ for docs-root *.json output names."""
    found: set[str] = set()
    pat_docs = re.compile(r"docs/([a-z0-9_]+\.json)")
    pat_dir = re.compile(r"DOCS_DIR\s*/\s*\"([a-z0-9_]+\.json)\"")
    for base in (ROOT / "src", ROOT / "scripts"):
        for path in base.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            found.update(pat_docs.findall(text))
            found.update(pat_dir.findall(text))
    return found


def test_expected_files_are_excluded():
    excludes = _workflow_excludes()
    missing = EXPECTED_PRESERVED - excludes
    assert not missing, f"publish would delete: {sorted(missing)}"


def test_no_stale_excludes():
    excludes = _workflow_excludes()
    json_excludes = {e for e in excludes if e.endswith(".json")}
    stale = json_excludes - EXPECTED_PRESERVED - BUILD_EMBEDDED
    assert not stale, f"excluded but never written (typo?): {sorted(stale)}"


def test_code_outputs_covered():
    known = EXPECTED_PRESERVED | BUILD_EMBEDDED | LEGACY_NOT_PUBLISHED
    unknown = _json_literals_in_code() - known
    assert not unknown, (
        f"docs-root JSON written in code but not in EXPECTED_PRESERVED: "
        f"{sorted(unknown)} — add to this test AND the publish workflow"
    )


ALL_TESTS = [test_expected_files_are_excluded, test_no_stale_excludes,
             test_code_outputs_covered]


def main() -> int:
    failures = 0
    for t in ALL_TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
