#!/usr/bin/env python3
"""
Unit tests for drift-check decision logic (no DB / no network).

Runnable two ways:
  uv run python tests/test_drift_check.py
  uv run pytest tests/test_drift_check.py      # if pytest is available
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.drift_check import _drift_reasons  # noqa: E402


THRESHOLDS = {
    "min_ic": -0.02,
    "max_brier": 0.30,
    "max_psi": 0.25,
}


def test_psi_warning_is_not_breach_when_outcomes_insufficient():
    reasons, breach_reasons = _drift_reasons(
        "insufficient_sample", ic=None, brier=None, psi_max=0.5, thresholds=THRESHOLDS
    )
    assert reasons == ["psi>0.25"]
    assert breach_reasons == []


def test_psi_warning_becomes_breach_when_outcomes_sufficient():
    reasons, breach_reasons = _drift_reasons(
        "ok", ic=0.01, brier=0.20, psi_max=0.5, thresholds=THRESHOLDS
    )
    assert reasons == ["psi>0.25"]
    assert breach_reasons == ["psi>0.25"]


def test_metric_threshold_breach_requires_sufficient_outcomes():
    reasons, breach_reasons = _drift_reasons(
        "ok", ic=-0.10, brier=0.40, psi_max=0.1, thresholds=THRESHOLDS
    )
    assert reasons == ["ic<-0.02", "brier>0.3"]
    assert breach_reasons == ["ic<-0.02", "brier>0.3"]

    reasons, breach_reasons = _drift_reasons(
        "insufficient_sample", ic=-0.10, brier=0.40, psi_max=0.1, thresholds=THRESHOLDS
    )
    assert reasons == []
    assert breach_reasons == []


ALL_TESTS = [
    test_psi_warning_is_not_breach_when_outcomes_insufficient,
    test_psi_warning_becomes_breach_when_outcomes_sufficient,
    test_metric_threshold_breach_requires_sufficient_outcomes,
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
