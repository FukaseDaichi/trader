#!/usr/bin/env python3
"""Regression tests for auto-mode legacy prediction fallback (no network/DB).

Runnable two ways:
  uv run python tests/test_main_predict_fallback.py
  uv run pytest tests/test_main_predict_fallback.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main as trader_main  # noqa: E402
from src import db_records  # noqa: E402


TICKER = {"code": "8750.JP", "name": "第一生命"}
LABEL_CFG = {"label_mode": "triple_barrier", "tb_max_days": 5}


def _auto_context() -> dict:
    return {
        "model_cfg": {"model_mode": "auto"},
        "label_cfg": LABEL_CFG,
        "active": {"version": "phase1-test-v1"},
    }


def test_auto_missing_bundle_uses_successful_legacy_fallback():
    featured = object()
    trained_model = object()

    with (
        patch.object(
            trader_main.model_store, "load_model_bundle", return_value=None
        ) as load_bundle,
        patch.object(
            trader_main,
            "train_and_predict",
            return_value=(trained_model, 0.73),
        ) as train,
    ):
        prob_up, model_ready, fields = trader_main._predict_for_ticker(
            featured, TICKER, _auto_context()
        )

    load_bundle.assert_called_once_with("phase1-test-v1", "8750.JP")
    train.assert_called_once_with(
        featured,
        runtime_config=trader_main.BACKTEST_GATE_CONFIG,
        label_config=LABEL_CFG,
    )
    assert prob_up == 0.73
    assert model_ready is True
    assert fields == {
        "model_version": db_records.LEGACY_MODEL_VERSION,
        "horizon_days": 5,
        "raw_score": 0.73,
        "expected_ret": None,
        "features_hash": None,
    }


def test_auto_missing_bundle_handles_failed_legacy_fallback():
    featured = object()

    with (
        patch.object(trader_main.model_store, "load_model_bundle", return_value=None),
        patch.object(
            trader_main, "train_and_predict", return_value=(None, 0.5)
        ) as train,
    ):
        prob_up, model_ready, fields = trader_main._predict_for_ticker(
            featured, TICKER, _auto_context()
        )

    train.assert_called_once()
    assert prob_up == 0.5
    assert model_ready is False
    assert fields == {
        "model_version": db_records.LEGACY_MODEL_VERSION,
        "horizon_days": 5,
    }


ALL_TESTS = [
    test_auto_missing_bundle_uses_successful_legacy_fallback,
    test_auto_missing_bundle_handles_failed_legacy_fallback,
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
