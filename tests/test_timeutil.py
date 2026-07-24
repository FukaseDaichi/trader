#!/usr/bin/env python3
"""Unit tests for canonical JST timestamps."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.timeutil import now_jst_iso  # noqa: E402


def test_now_jst_iso_is_timezone_aware():
    from datetime import datetime

    parsed = datetime.fromisoformat(now_jst_iso())
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(hours=9)
    assert parsed.microsecond == 0


ALL_TESTS = [test_now_jst_iso_is_timezone_aware]


def main() -> int:
    failures = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
