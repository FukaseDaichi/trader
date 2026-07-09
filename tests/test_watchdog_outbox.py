#!/usr/bin/env python3
"""
Unit tests for the outbox-depth check in scripts/workflow_watchdog.py.

The 2026-07 incident: DB write-through failed daily for 3+ weeks while the
watchdog stayed green because it only checked docs/ freshness. The watchdog
must fail (→ GitHub Issue) when the committed outbox backlog looks stuck.

Ages are computed from the filename stamp (YYYYMMDDHHMMSS.jsonl), NOT mtime —
CI checkouts reset mtimes.

Runnable two ways:
  uv run python tests/test_watchdog_outbox.py
  uv run pytest tests/test_watchdog_outbox.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tempfile  # noqa: E402

from scripts.workflow_watchdog import _outbox_problems, build_parser  # noqa: E402

TODAY = "2026-07-09"


def _touch(outbox: Path, name: str, sub: str | None = None) -> None:
    d = outbox / sub if sub else outbox
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text('{"event_id": "x"}\n', encoding="utf-8")


def test_missing_or_empty_outbox_is_ok():
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "nope"
        assert _outbox_problems(missing, TODAY) == ([], [])
        empty = Path(tmp) / "outbox"
        empty.mkdir()
        assert _outbox_problems(empty, TODAY) == ([], [])


def test_fresh_file_is_ok():
    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "outbox"
        _touch(outbox, "20260709071600.jsonl")
        failures, warnings = _outbox_problems(outbox, TODAY)
        assert failures == []
        assert warnings == []


def test_stale_file_fails():
    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "outbox"
        _touch(outbox, "20260616075152.jsonl")  # 23 days old
        _touch(outbox, "20260709071600.jsonl")
        failures, _ = _outbox_problems(outbox, TODAY, max_age_days=5)
        assert len(failures) == 1
        assert failures[0].startswith("outbox_stale_files:1:oldest=20260616")


def test_backlog_count_fails():
    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "outbox"
        for i in range(12):
            _touch(outbox, f"202607090716{i:02d}.jsonl")
        failures, _ = _outbox_problems(outbox, TODAY, max_files=10)
        assert "outbox_backlog:12" in failures


def test_dead_letters_fail():
    """Quarantined events are lost measurement data — must open an Issue,
    not just warn."""
    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "outbox"
        _touch(outbox, "20260709071600.jsonl", sub="dead")
        failures, warnings = _outbox_problems(outbox, TODAY)
        assert failures == ["outbox_dead_letters:1"]
        assert warnings == []


def test_unparsable_name_warns():
    with tempfile.TemporaryDirectory() as tmp:
        outbox = Path(tmp) / "outbox"
        _touch(outbox, "not-a-stamp.jsonl")
        failures, warnings = _outbox_problems(outbox, TODAY)
        assert failures == []
        assert warnings == ["outbox_unparsable:not-a-stamp.jsonl"]


def test_parser_has_outbox_defaults():
    args = build_parser().parse_args([])
    assert args.outbox_dir == "data/outbox"
    assert int(args.max_outbox_age_days) == 5
    assert int(args.max_outbox_files) == 10


ALL_TESTS = [
    test_missing_or_empty_outbox_is_ok,
    test_fresh_file_is_ok,
    test_stale_file_fails,
    test_backlog_count_fails,
    test_dead_letters_fail,
    test_unparsable_name_warns,
    test_parser_has_outbox_defaults,
]


def main() -> int:
    failures = 0
    for t in ALL_TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
