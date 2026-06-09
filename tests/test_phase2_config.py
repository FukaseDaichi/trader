#!/usr/bin/env python3
"""
Unit tests for Phase 2 config functions and kind-scoped model registry.

Runnable two ways:
  uv run python tests/test_phase2_config.py     # standalone
  uv run pytest tests/test_phase2_config.py      # if pytest is available
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import get_cross_section_config, get_portfolio_config  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CS_KEYS = {
    "TRADER_CS_OBJECTIVE",
    "TRADER_CS_MODEL_ACTIVE_FILE",
    "TRADER_CS_MIN_UNIVERSE",
    "TRADER_CS_TOP_N",
    "TRADER_CS_LABEL_HORIZON_DAYS",
    "TRADER_CS_MIN_DAILY_NAMES",
    "TRADER_CS_PANEL_LOOKBACK_YEARS",
    "TRADER_UNIVERSE_TARGET_SIZE",
}

_PORT_KEYS = {
    "TRADER_PORTFOLIO_ENABLED",
    "TRADER_PORTFOLIO_MODE",
    "TRADER_PORTFOLIO_TARGET_VOL",
    "TRADER_PORTFOLIO_MAX_NAME_WEIGHT",
    "TRADER_PORTFOLIO_SECTOR_CAP",
    "TRADER_PORTFOLIO_MAX_GROSS",
    "TRADER_PORTFOLIO_MIN_WEIGHT",
    "TRADER_PORTFOLIO_NOTRADE_BAND",
    "TRADER_PORTFOLIO_MIN_EXPECTED_RET",
    "TRADER_PORTFOLIO_RISK_OFF_GROSS_MULT",
    "TRADER_PORTFOLIO_COV_LOOKBACK_DAYS",
    "TRADER_PORTFOLIO_BACKTEST_MIN_SHARPE",
    "TRADER_PORTFOLIO_BACKTEST_MAX_DD",
    "TRADER_PORTFOLIO_BACKTEST_MIN_IR",
    "TRADER_PORTFOLIO_BACKTEST_MAX_TURNOVER",
}


def _clear_env(keys):
    """Remove keys from os.environ, return dict of original values."""
    saved = {}
    for k in keys:
        if k in os.environ:
            saved[k] = os.environ.pop(k)
    return saved


def _restore_env(saved):
    for k, v in saved.items():
        os.environ[k] = v


# ---------------------------------------------------------------------------
# Tests: get_cross_section_config defaults
# ---------------------------------------------------------------------------

def test_cs_config_defaults():
    saved = _clear_env(_CS_KEYS)
    try:
        cfg = get_cross_section_config()
        assert cfg["objective"] == "ranker", f"expected ranker, got {cfg['objective']}"
        assert cfg["min_universe"] == 30, f"expected 30, got {cfg['min_universe']}"
        assert cfg["top_n"] == 8, f"expected 8, got {cfg['top_n']}"
        assert cfg["label_horizon_days"] == 5, f"expected 5, got {cfg['label_horizon_days']}"
        assert cfg["min_daily_names"] == 20, f"expected 20, got {cfg['min_daily_names']}"
        assert cfg["panel_lookback_years"] == 5, f"expected 5, got {cfg['panel_lookback_years']}"
        assert cfg["universe_target_size"] == 40, f"expected 40, got {cfg['universe_target_size']}"
        # active_model_file ends with active_cs_model.json
        assert cfg["active_model_file"].endswith("active_cs_model.json"), \
            f"unexpected active_model_file: {cfg['active_model_file']}"
    finally:
        _restore_env(saved)


def test_cs_config_label_horizon_days_minimum_one():
    """label_horizon_days is clamped to >= 1 even with a tiny or zero value."""
    saved = _clear_env(_CS_KEYS)
    try:
        os.environ["TRADER_CS_LABEL_HORIZON_DAYS"] = "0"
        cfg = get_cross_section_config()
        assert cfg["label_horizon_days"] >= 1, \
            f"label_horizon_days should be >= 1, got {cfg['label_horizon_days']}"
    finally:
        _restore_env(saved)


def test_cs_config_panel_lookback_years_minimum_one():
    """panel_lookback_years is clamped to >= 1."""
    saved = _clear_env(_CS_KEYS)
    try:
        os.environ["TRADER_CS_PANEL_LOOKBACK_YEARS"] = "0"
        cfg = get_cross_section_config()
        assert cfg["panel_lookback_years"] >= 1, \
            f"panel_lookback_years should be >= 1, got {cfg['panel_lookback_years']}"
    finally:
        _restore_env(saved)


def test_cs_config_env_override_objective_regression():
    saved = _clear_env(_CS_KEYS)
    try:
        os.environ["TRADER_CS_OBJECTIVE"] = "regression"
        cfg = get_cross_section_config()
        assert cfg["objective"] == "regression", f"expected regression, got {cfg['objective']}"
    finally:
        _restore_env(saved)


def test_cs_config_invalid_objective_raises():
    saved = _clear_env(_CS_KEYS)
    try:
        os.environ["TRADER_CS_OBJECTIVE"] = "invalid_choice"
        raised = False
        try:
            get_cross_section_config()
        except ValueError:
            raised = True
        assert raised, "Expected ValueError for invalid TRADER_CS_OBJECTIVE"
    finally:
        _restore_env(saved)


# ---------------------------------------------------------------------------
# Tests: get_portfolio_config defaults
# ---------------------------------------------------------------------------

def test_portfolio_config_defaults():
    saved = _clear_env(_PORT_KEYS)
    try:
        cfg = get_portfolio_config()
        assert cfg["enabled"] is False, f"expected False, got {cfg['enabled']}"
        assert cfg["mode"] == "shadow", f"expected shadow, got {cfg['mode']}"
        assert abs(cfg["target_vol"] - 0.12) < 1e-9, f"expected 0.12, got {cfg['target_vol']}"
        assert abs(cfg["max_name_weight"] - 0.20) < 1e-9
        assert abs(cfg["sector_cap"] - 0.40) < 1e-9
        assert abs(cfg["max_gross"] - 1.00) < 1e-9
        assert abs(cfg["min_weight"] - 0.03) < 1e-9
        assert abs(cfg["notrade_band"] - 0.02) < 1e-9
        assert cfg["min_expected_ret"] == 0.0
        assert abs(cfg["risk_off_gross_mult"] - 0.50) < 1e-9
        assert cfg["cov_lookback_days"] == 60
        assert abs(cfg["backtest_min_sharpe"] - 0.30) < 1e-9
        assert abs(cfg["backtest_max_dd"] - 0.25) < 1e-9
        assert cfg["backtest_min_ir"] == 0.00
        assert abs(cfg["backtest_max_turnover"] - 0.40) < 1e-9
    finally:
        _restore_env(saved)


def test_portfolio_config_disabled_when_env_unset():
    """When TRADER_PORTFOLIO_ENABLED is unset, portfolio is disabled (safe default)."""
    saved = _clear_env(_PORT_KEYS)
    try:
        cfg = get_portfolio_config()
        assert cfg["enabled"] is False, \
            "Portfolio must default to disabled when env var is unset"
    finally:
        _restore_env(saved)


def test_portfolio_config_mode_override_active():
    saved = _clear_env(_PORT_KEYS)
    try:
        os.environ["TRADER_PORTFOLIO_MODE"] = "active"
        cfg = get_portfolio_config()
        assert cfg["mode"] == "active", f"expected active, got {cfg['mode']}"
    finally:
        _restore_env(saved)


def test_portfolio_config_invalid_mode_raises():
    saved = _clear_env(_PORT_KEYS)
    try:
        os.environ["TRADER_PORTFOLIO_MODE"] = "live"
        raised = False
        try:
            get_portfolio_config()
        except ValueError:
            raised = True
        assert raised, "Expected ValueError for invalid TRADER_PORTFOLIO_MODE"
    finally:
        _restore_env(saved)


def test_portfolio_config_env_enabled_true():
    saved = _clear_env(_PORT_KEYS)
    try:
        os.environ["TRADER_PORTFOLIO_ENABLED"] = "true"
        cfg = get_portfolio_config()
        assert cfg["enabled"] is True, f"expected True, got {cfg['enabled']}"
    finally:
        _restore_env(saved)


def test_portfolio_config_float_overrides():
    saved = _clear_env(_PORT_KEYS)
    try:
        os.environ["TRADER_PORTFOLIO_TARGET_VOL"] = "0.15"
        os.environ["TRADER_PORTFOLIO_MAX_NAME_WEIGHT"] = "0.25"
        cfg = get_portfolio_config()
        assert abs(cfg["target_vol"] - 0.15) < 1e-9
        assert abs(cfg["max_name_weight"] - 0.25) < 1e-9
    finally:
        _restore_env(saved)


# ---------------------------------------------------------------------------
# DB test: kind-scoped active (skipped when DB is not configured)
# ---------------------------------------------------------------------------

def test_kind_scoped_active_model_registry():
    """
    When DB is available: registers two model versions with different kinds,
    asserts both can be active simultaneously via active_model_version_for_kind.
    Skipped (counts as PASS) when TRADER_DB_ENABLED=false or DATABASE_URL is unset.
    """
    db_enabled_raw = os.environ.get("TRADER_DB_ENABLED", "true").strip().lower()
    db_url = os.environ.get("DATABASE_URL", "")
    if db_enabled_raw in {"0", "false", "no", "off"} or not db_url:
        print("SKIP test_kind_scoped_active_model_registry (no DB)")
        return

    try:
        import src.db as dbmod
        conn = dbmod.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP test_kind_scoped_active_model_registry (connect failed: {exc})")
        return

    try:
        v1 = "test-per-ticker-v1-phase2"
        v2 = "test-cross-sectional-v1-phase2"
        kind1 = "per_ticker_horizon_v1"
        kind2 = "cross_sectional_ranker_v1"
        try:
            from psycopg.types.json import Jsonb
            with conn.cursor() as cur:
                # Clean up from previous test runs
                cur.execute(
                    "DELETE FROM model_registry WHERE version IN (%s, %s)",
                    (v1, v2),
                )
            conn.commit()

            dbmod.register_model_version(
                conn, v1, kind=kind1, universe=[], feature_set=[],
                params={}, cv_metrics={}, make_active=True,
            )
            dbmod.register_model_version(
                conn, v2, kind=kind2, universe=[], feature_set=[],
                params={}, cv_metrics={}, make_active=True,
            )

            active1 = dbmod.active_model_version_for_kind(conn, kind1)
            active2 = dbmod.active_model_version_for_kind(conn, kind2)
            assert active1 == v1, f"expected {v1}, got {active1}"
            assert active2 == v2, f"expected {v2}, got {active2}"
        finally:
            # Clean up
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM model_registry WHERE version IN (%s, %s)",
                    (v1, v2),
                )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        raise
    finally:
        conn.close()


ALL_TESTS = [
    test_cs_config_defaults,
    test_cs_config_label_horizon_days_minimum_one,
    test_cs_config_panel_lookback_years_minimum_one,
    test_cs_config_env_override_objective_regression,
    test_cs_config_invalid_objective_raises,
    test_portfolio_config_defaults,
    test_portfolio_config_disabled_when_env_unset,
    test_portfolio_config_mode_override_active,
    test_portfolio_config_invalid_mode_raises,
    test_portfolio_config_env_enabled_true,
    test_portfolio_config_float_overrides,
    test_kind_scoped_active_model_registry,
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
