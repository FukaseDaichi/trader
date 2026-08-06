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
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import drift_check  # noqa: E402

_drift_reasons = drift_check._drift_reasons


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


def test_outcomes_pool_across_lineage_when_kind_available():
    calls = []

    class FakeConn:
        def close(self):
            return None

    def fake_fetch_for_kind(conn, model_kind, horizon):
        calls.append((model_kind, horizon))
        return [
            {"ticker": "7011.JP", "prob_up": 0.7, "realized_ret": 0.02, "hit": True},
            {"ticker": "7011.JP", "prob_up": 0.4, "realized_ret": -0.01, "hit": False},
            {"ticker": "6501.JP", "prob_up": 0.6, "realized_ret": 0.01, "hit": True},
        ]

    with (
        mock.patch.object(drift_check.db, "db_enabled", return_value=True),
        mock.patch.object(drift_check.db, "connect", return_value=FakeConn()),
        mock.patch.object(
            drift_check.db,
            "fetch_prediction_outcomes_for_kind",
            side_effect=fake_fetch_for_kind,
        ),
    ):
        by_ticker = drift_check._db_outcomes_by_ticker(
            "per-ticker-v1-20260801T090258-x-y", "per_ticker_horizon_v1", 5
        )

    assert calls == [("per_ticker_horizon_v1", 5)]
    assert sorted(by_ticker) == ["6501.JP", "7011.JP"]
    assert len(by_ticker["7011.JP"]) == 2


def test_outcomes_fall_back_to_exact_version_without_kind():
    calls = []

    class FakeConn:
        def close(self):
            return None

    def fake_fetch_by_version(conn, model_version, horizon):
        calls.append((model_version, horizon))
        return []

    with (
        mock.patch.object(drift_check.db, "db_enabled", return_value=True),
        mock.patch.object(drift_check.db, "connect", return_value=FakeConn()),
        mock.patch.object(
            drift_check.db,
            "fetch_prediction_outcomes",
            side_effect=fake_fetch_by_version,
        ),
    ):
        by_ticker = drift_check._db_outcomes_by_ticker("some-version", None, 5)

    assert calls == [("some-version", 5)]
    assert by_ticker == {}


def test_incompatible_active_model_is_reported_unavailable():
    payloads = []
    incompatibilities = [{"code": "active_manifest_integrity_failed"}]
    with (
        mock.patch.object(
            drift_check.model_store,
            "read_active_model",
            return_value={"version": "phase1-invalid"},
        ),
        mock.patch.object(
            drift_check.model_store,
            "validate_runtime_active_phase1",
            return_value={
                "compatible": False,
                "reasons": incompatibilities,
            },
        ) as validate,
        mock.patch.object(drift_check, "_write", side_effect=payloads.append),
        mock.patch.object(sys, "argv", ["drift_check.py"]),
    ):
        assert drift_check.main() == 0

    validate.assert_called_once()
    assert payloads == [
        {
            "available": False,
            "reason": "active_model_incompatible",
            "generated_at": payloads[0]["generated_at"],
            "model_version": "phase1-invalid",
            "incompatibilities": incompatibilities,
        }
    ]


ALL_TESTS = [
    test_psi_warning_is_not_breach_when_outcomes_insufficient,
    test_psi_warning_becomes_breach_when_outcomes_sufficient,
    test_metric_threshold_breach_requires_sufficient_outcomes,
    test_outcomes_pool_across_lineage_when_kind_available,
    test_outcomes_fall_back_to_exact_version_without_kind,
    test_incompatible_active_model_is_reported_unavailable,
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
