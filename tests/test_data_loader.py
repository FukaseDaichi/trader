#!/usr/bin/env python3
"""Unit tests for OHLCV boundary validation."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_loader import _validate_ohlcv  # noqa: E402


def test_nonfinite_ohlcv_rows_are_rejected():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-07-21", periods=3, freq="D"),
            "open": [100.0, 101.0, 102.0],
            "high": [102.0, 103.0, float("inf")],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, float("inf"), 102.0],
            "volume": [1000.0, 1100.0, 1200.0],
        }
    )

    validated = _validate_ohlcv(frame, ticker_code="9999.JP", source="test")

    assert len(validated) == 1
    assert validated.iloc[0]["date"] == pd.Timestamp("2026-07-21")
    assert "invalid_non_finite_ohlcv:2" in validated.attrs["validation_warnings"]


ALL_TESTS = [test_nonfinite_ohlcv_rows_are_rejected]


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
