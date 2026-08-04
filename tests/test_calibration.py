#!/usr/bin/env python3
"""
Unit tests for src/calibration.py (pure numpy, no DB / no network).

Runnable two ways:
  uv run python tests/test_calibration.py     # standalone
  uv run pytest tests/test_calibration.py      # if pytest is available
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.calibration import (  # noqa: E402
    apply_isotonic,
    brier_score,
    fit_calibrator,
    fit_isotonic_pava,
    reliability_bins,
)


def test_apply_identity_when_no_calibrator():
    out = apply_isotonic(None, [0.1, 0.5, 1.5, -0.2])
    assert list(out) == [0.1, 0.5, 1.0, 0.0]  # clipped to [0,1]


def test_isotonic_is_monotonic_nondecreasing():
    # Noisy but upward-trending labels; calibrated map must be non-decreasing.
    scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    labels = [0, 0, 1, 0, 1, 1, 0, 1, 1]
    cal = fit_isotonic_pava(scores, labels)
    grid = np.linspace(0.0, 1.0, 50)
    mapped = apply_isotonic(cal, grid)
    assert np.all(np.diff(mapped) >= -1e-12)
    assert mapped.min() >= 0.0 and mapped.max() <= 1.0


def test_isotonic_improves_brier_on_miscalibrated():
    # x=0.2 always label 0; x=0.8 is 50/50 -> calibrated 0.8 should map to ~0.5.
    scores = [0.2] * 50 + [0.8] * 50
    labels = [0] * 50 + ([0] * 25 + [1] * 25)
    cal = fit_isotonic_pava(scores, labels)
    raw = brier_score(scores, labels)
    calibrated = brier_score(apply_isotonic(cal, scores), labels)
    assert calibrated < raw
    # x=0.8 maps near 0.5
    assert abs(float(apply_isotonic(cal, [0.8])[0]) - 0.5) < 1e-6
    assert abs(float(apply_isotonic(cal, [0.2])[0]) - 0.0) < 1e-6


def test_brier_score_basic_and_empty():
    assert abs(brier_score([1.0, 0.0], [1, 0]) - 0.0) < 1e-12
    assert abs(brier_score([0.5, 0.5], [1, 0]) - 0.25) < 1e-12
    assert brier_score([], []) is None
    # NaNs are ignored
    assert abs(brier_score([0.5, float("nan")], [1, 1]) - 0.25) < 1e-12


def test_reliability_bins_partition():
    prob = [0.05, 0.15, 0.15, 0.95]
    labels = [0, 0, 1, 1]
    bins = reliability_bins(prob, labels, n_bins=10)
    assert len(bins) == 10
    assert sum(b["count"] for b in bins) == 4
    # bin index 1 covers [0.1, 0.2): two points, mean_obs = 0.5
    assert bins[1]["count"] == 2
    assert abs(bins[1]["mean_obs"] - 0.5) < 1e-12
    # last bin includes 1.0 edge
    assert bins[9]["count"] == 1


def test_fit_calibrator_guard_insufficient_rows():
    scores = [0.2, 0.8, 0.5]
    labels = [0, 1, 1]
    cal, info = fit_calibrator(scores, labels, mode="isotonic", min_rows=60)
    assert cal is None
    assert info["applied"] is False
    assert info["reason"] == "insufficient_rows"


def test_fit_calibrator_mode_none():
    cal, info = fit_calibrator([0.2] * 100, [0] * 100, mode="none", min_rows=10)
    assert cal is None
    assert info["reason"] == "mode_none"


def test_fit_calibrator_applies_and_reports_brier():
    scores = [0.2] * 50 + [0.8] * 50
    labels = [0] * 50 + ([0] * 25 + [1] * 25)
    cal, info = fit_calibrator(scores, labels, mode="isotonic", min_rows=60)
    assert cal is not None
    assert info["applied"] is True
    assert info["brier_cal"] <= info["brier_raw"]


def test_calibrator_is_json_serializable():
    import json

    cal = fit_isotonic_pava([0.1, 0.5, 0.9], [0, 1, 1])
    s = json.dumps(cal)
    back = json.loads(s)
    assert back["method"] == "isotonic"
    assert len(back["x"]) == len(back["y"])


ALL_TESTS = [
    test_apply_identity_when_no_calibrator,
    test_isotonic_is_monotonic_nondecreasing,
    test_isotonic_improves_brier_on_miscalibrated,
    test_brier_score_basic_and_empty,
    test_reliability_bins_partition,
    test_fit_calibrator_guard_insufficient_rows,
    test_fit_calibrator_mode_none,
    test_fit_calibrator_applies_and_reports_brier,
    test_calibrator_is_json_serializable,
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
