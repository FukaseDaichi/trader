#!/usr/bin/env python3
"""Unit tests for finite environment-number parsing."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.env import get_env_float  # noqa: E402


_NAME = "TRADER_TEST_FINITE_FLOAT"


def test_finite_float_is_returned():
    with patch.dict(os.environ, {_NAME: "-1.25e-3"}):
        assert get_env_float(_NAME, 2.0) == -0.00125


def test_nonfinite_float_falls_back_by_default():
    for raw in ("nan", "NaN", "inf", "+Infinity", "-inf"):
        with patch.dict(os.environ, {_NAME: raw}):
            assert get_env_float(_NAME, 2.5) == 2.5


def test_nonfinite_float_raises_for_critical_config():
    for raw in ("nan", "inf", "-inf"):
        with patch.dict(os.environ, {_NAME: raw}):
            try:
                get_env_float(_NAME, 2.5, invalid="raise")
            except ValueError as exc:
                assert "finite float" in str(exc)
                continue
            raise AssertionError(f"critical config accepted {raw}")


ALL_TESTS = [
    test_finite_float_is_returned,
    test_nonfinite_float_falls_back_by_default,
    test_nonfinite_float_raises_for_critical_config,
]


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
