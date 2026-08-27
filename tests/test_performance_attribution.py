#!/usr/bin/env python3
"""Hand-computable tests for the weekly-review gap attribution script."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.performance_attribution import (  # noqa: E402
    attribute_period,
    attribute_report,
    main_verdict,
)


def _assert_close(actual, expected, tolerance=1e-12):
    assert abs(actual - expected) < tolerance, (actual, expected)


def _row(
    *,
    gross_period_return,
    cost_return,
    gross_benchmark_return,
    benchmark_cost_return,
    gross_exposure,
    date="2026-01-01",
):
    return {
        "date": date,
        "gross_period_return": gross_period_return,
        "cost_return": cost_return,
        "gross_benchmark_return": gross_benchmark_return,
        "benchmark_cost_return": benchmark_cost_return,
        "gross_exposure": gross_exposure,
        "period_return": gross_period_return - cost_return,
        "benchmark_return": gross_benchmark_return - benchmark_cost_return,
    }


def test_single_period_decomposition_hand_computed():
    # Market +2% gross, strategy at half exposure returns +1.5% gross,
    # strategy pays 30bps, same-basis benchmark pays 30bps too.
    row = _row(
        gross_period_return=0.015,
        cost_return=0.003,
        gross_benchmark_return=0.02,
        benchmark_cost_return=0.003,
        gross_exposure=0.5,
    )
    a = attribute_period(row)
    # Exposure effect: (0.5 - 1) * 0.02 = -0.01
    _assert_close(a["exposure_effect"], -0.01)
    # Selection effect: 0.015 - 0.5 * 0.02 = +0.005
    _assert_close(a["selection_effect"], 0.005)
    # Cost effect vs same-basis benchmark: -(0.003 - 0.003) = 0
    _assert_close(a["cost_effect"], 0.0)
    # Net gap vs same-basis benchmark: (0.015-0.003) - (0.02-0.003) = -0.005
    _assert_close(a["net_gap"], -0.005)
    # Identity: the three effects sum to the net gap.
    _assert_close(
        a["exposure_effect"] + a["selection_effect"] + a["cost_effect"],
        a["net_gap"],
    )
    # Gap vs a real zero-cost buy-and-hold benchmark: 0.012 - 0.02 = -0.008
    _assert_close(a["net_gap_vs_real_benchmark"], -0.008)


def test_identity_holds_on_arbitrary_rows():
    rows = [
        _row(
            gross_period_return=0.0123,
            cost_return=0.0007,
            gross_benchmark_return=0.0281,
            benchmark_cost_return=0.0015,
            gross_exposure=0.4485,
            date="2025-11-18",
        ),
        _row(
            gross_period_return=-0.0014,
            cost_return=0.0015,
            gross_benchmark_return=-0.0109,
            benchmark_cost_return=0.003,
            gross_exposure=0.5508,
            date="2025-11-26",
        ),
    ]
    for row in rows:
        a = attribute_period(row)
        _assert_close(
            a["exposure_effect"] + a["selection_effect"] + a["cost_effect"],
            a["net_gap"],
            tolerance=1e-9,
        )
        _assert_close(
            a["exposure_effect"] + a["selection_effect"] - row["cost_return"],
            a["net_gap_vs_real_benchmark"],
            tolerance=1e-9,
        )


def test_report_totals_are_sums_of_periods():
    report = {
        "available": True,
        "equity": [
            _row(
                gross_period_return=0.01,
                cost_return=0.002,
                gross_benchmark_return=0.02,
                benchmark_cost_return=0.003,
                gross_exposure=0.5,
                date="2026-01-01",
            ),
            _row(
                gross_period_return=-0.005,
                cost_return=0.001,
                gross_benchmark_return=-0.01,
                benchmark_cost_return=0.003,
                gross_exposure=0.6,
                date="2026-01-08",
            ),
        ],
    }
    result = attribute_report(report)
    assert len(result["periods"]) == 2
    totals = result["totals"]
    for key in (
        "exposure_effect",
        "selection_effect",
        "cost_effect",
        "net_gap",
        "net_gap_vs_real_benchmark",
    ):
        _assert_close(
            totals[key], sum(p[key] for p in result["periods"]), tolerance=1e-9
        )
    _assert_close(
        totals["exposure_effect"] + totals["selection_effect"] + totals["cost_effect"],
        totals["net_gap"],
        tolerance=1e-9,
    )


def test_main_verdict_names_largest_negative_effect():
    assert (
        main_verdict(
            {"exposure_effect": -0.05, "selection_effect": 0.01, "cost_effect": -0.02}
        )
        == "exposure"
    )
    assert (
        main_verdict(
            {"exposure_effect": 0.01, "selection_effect": 0.02, "cost_effect": -0.001}
        )
        == "cost"
    )
    assert (
        main_verdict(
            {"exposure_effect": 0.01, "selection_effect": 0.02, "cost_effect": 0.0}
        )
        == "none"
    )


def test_unavailable_report_is_handled():
    result = attribute_report({"available": False, "reason": "no_data"})
    assert result["available"] is False
    assert result["periods"] == []


def test_missing_fields_skip_row_not_crash():
    report = {
        "available": True,
        "equity": [
            {"date": "2026-01-01"},  # malformed row: no returns at all
            _row(
                gross_period_return=0.01,
                cost_return=0.002,
                gross_benchmark_return=0.02,
                benchmark_cost_return=0.003,
                gross_exposure=0.5,
                date="2026-01-08",
            ),
        ],
    }
    result = attribute_report(report)
    assert len(result["periods"]) == 1
    assert result["n_skipped"] == 1


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
