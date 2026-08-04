#!/usr/bin/env python3
"""Hand-computable regression tests for KPI metric contract v3 (DR-007)."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest import (  # noqa: E402
    _collect_oos_predictions,
    _compute_metrics,
    _evaluate_gate_rules,
    _optimize_thresholds,
    _score_for_objective,
    _split_oos_for_thresholding,
    evaluate_kpi_gate,
)
from src.config import get_backtest_gate_config  # noqa: E402
from src.execution import EXECUTION_CONTRACT_VERSION  # noqa: E402
from src.predictor import resolve_thresholds  # noqa: E402


def _sim(net_returns, exposures, ending_exposures, turnovers, entry_cohorts):
    frame = pd.DataFrame(
        {
            "net_return": net_returns,
            "exposure": exposures,
            "ending_exposure": ending_exposures,
            "turnover": turnovers,
            "entry_cohorts": entry_cohorts,
        }
    )
    frame["equity"] = (1.0 + frame["net_return"]).cumprod()
    return frame


def _assert_close(actual, expected, tolerance=1e-12):
    assert abs(actual - expected) < tolerance, (actual, expected)


def test_no_trade_metrics_are_zero():
    metrics = _compute_metrics(
        _sim(
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0],
            [0, 0, 0],
        )
    )
    assert metrics["turnover_days"] == 0
    assert metrics["round_trips"] == 0
    assert metrics["signal_cohorts"] == 0
    assert metrics["independent_signal_cohorts"] == 0
    assert metrics["avg_daily_net_return"] == 0.0
    assert metrics["expectancy_per_trade"] == 0.0


def test_continuous_holding_is_one_round_trip():
    metrics = _compute_metrics(
        _sim(
            [0.01, 0.02, -0.01],
            [1.0, 1.0, 1.0],
            [1.0, 1.0, 0.0],
            [1.0, 0.0, 1.0],
            [1, 0, 0],
        )
    )
    expected_trade_return = 1.01 * 1.02 * 0.99 - 1.0
    assert metrics["turnover_days"] == 2
    assert metrics["round_trips"] == 1
    assert metrics["signal_cohorts"] == 1
    _assert_close(metrics["avg_daily_net_return"], 0.02 / 3.0)
    _assert_close(metrics["expectancy_per_trade"], expected_trade_return)
    assert metrics["trades"] == metrics["round_trips"]
    assert metrics["expectancy"] == metrics["expectancy_per_trade"]
    assert metrics["metrics_schema_version"] == 3


def test_horizon_overlap_is_removed_from_independent_signal_cohorts():
    metrics = _compute_metrics(
        _sim(
            [0.0] * 11,
            [1.0] * 11,
            [1.0] * 10 + [0.0],
            [1.0] * 11,
            [1, 1, 0, 0, 1, 1, 0, 0, 0, 0, 1],
        ),
        horizon=5,
    )
    assert metrics["signal_cohorts"] == 5
    assert metrics["independent_signal_cohorts"] == 3


def test_market_row_positions_define_independent_cohort_spacing():
    sim = _sim(
        [0.0, 0.0, 0.0],
        [1.0, 1.0, 1.0],
        [1.0, 1.0, 0.0],
        [1.0, 1.0, 1.0],
        [1, 1, 1],
    )
    sim["market_row_number"] = [10, 12, 15]
    metrics = _compute_metrics(sim, horizon=5)
    assert metrics["signal_cohorts"] == 3
    assert metrics["independent_signal_cohorts"] == 2


def test_staged_position_is_not_counted_as_multiple_round_trips():
    metrics = _compute_metrics(
        _sim(
            [0.01, 0.02, 0.03],
            [0.5, 1.0, 0.5],
            [0.5, 1.0, 0.0],
            [0.5, 0.5, 1.0],
            [1, 1, 0],
        )
    )
    assert metrics["turnover_days"] == 3
    assert metrics["round_trips"] == 1
    assert metrics["signal_cohorts"] == 2
    _assert_close(metrics["expectancy_per_trade"], 1.01 * 1.02 * 1.03 - 1.0)


def test_position_reversal_completes_two_round_trips():
    metrics = _compute_metrics(
        _sim(
            [0.01, 0.02, -0.01],
            [1.0, -1.0, -1.0],
            [1.0, -1.0, 0.0],
            [1.0, 2.0, 1.0],
            [1, 1, 0],
        )
    )
    first = 0.01
    second = 1.02 * 0.99 - 1.0
    assert metrics["round_trips"] == 2
    assert metrics["signal_cohorts"] == 2
    _assert_close(metrics["expectancy_per_trade"], (first + second) / 2.0)


def test_gate_and_objective_use_all_session_net_returns():
    metrics = _compute_metrics(
        _sim(
            [0.10, -0.10, -0.10],
            [1.0, 1.0, 1.0],
            [1.0, 1.0, 0.0],
            [1.0, 0.0, 1.0],
            [1, 0, 0],
        )
    )
    assert metrics["avg_daily_net_return"] < 0
    assert _score_for_objective(metrics, "avg_daily_net_return") < 0
    failures = _evaluate_gate_rules(
        metrics,
        {
            "min_round_trips": 1,
            "min_avg_daily_net_return": 0.0,
            "min_cagr": -100.0,
            "max_drawdown": 1.0,
            "min_sharpe": -100.0,
        },
    )
    assert "avg_daily_net_return<0.000%" in failures
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _score_for_objective(metrics, "expectancy") < 0
    assert any("deprecated" in str(item.message) for item in caught)


def test_gate_required_metrics_fail_closed_when_missing_or_nonfinite():
    valid = {
        "round_trips": 1,
        "cagr": 0.0,
        "avg_daily_net_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe": 0.0,
    }
    config = {
        "min_round_trips": 0,
        "min_avg_daily_net_return": -1.0,
        "min_cagr": -1.0,
        "max_drawdown": 1.0,
        "min_sharpe": -1.0,
    }
    assert _evaluate_gate_rules(valid, config) == []

    expected = {
        "round_trips": "round_trips_unavailable",
        "cagr": "cagr_unavailable",
        "avg_daily_net_return": "avg_daily_net_return_unavailable",
        "max_drawdown": "max_drawdown_unavailable",
        "sharpe": "sharpe_unavailable",
    }
    for field, failure in expected.items():
        missing = {key: value for key, value in valid.items() if key != field}
        assert _evaluate_gate_rules(missing, config).count(failure) == 1
        for invalid in (None, float("nan"), float("inf"), float("-inf")):
            metrics = {**valid, field: invalid}
            failures = _evaluate_gate_rules(metrics, config)
            assert failures.count(failure) == 1, (field, invalid, failures)


def test_gate_uses_independent_signal_cohort_minimum_when_configured():
    metrics = {
        "round_trips": 1,
        "independent_signal_cohorts": 4,
        "cagr": 0.10,
        "avg_daily_net_return": 0.001,
        "max_drawdown": -0.10,
        "sharpe": 1.0,
    }
    config = {
        "sample_sufficiency_metric": "independent_signal_cohorts",
        "min_independent_signal_cohorts": 5,
        "min_round_trips": 10,
        "min_avg_daily_net_return": 0.0,
        "min_cagr": 0.0,
        "max_drawdown": 0.25,
        "min_sharpe": 0.2,
    }
    assert _evaluate_gate_rules(metrics, config) == [
        "independent_signal_cohorts<5"
    ]
    metrics["independent_signal_cohorts"] = 5
    assert _evaluate_gate_rules(metrics, config) == []


def _oos_frame(rows, horizon):
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=rows, freq="D"),
            "market_row_number": list(range(rows)),
            "execution_path_market_rows": [
                list(range(row + 1, row + horizon + 1)) for row in range(rows)
            ],
        }
    )


def test_threshold_holdout_has_horizon_embargo_and_no_return_overlap():
    horizon = 5
    tuning, holdout, info = _split_oos_for_thresholding(
        _oos_frame(30, horizon),
        {"val_size": 10, "n_folds": 3},
        horizon=horizon,
    )
    assert len(tuning) == 15
    assert len(holdout) == 10
    assert info["embargo_rows"] == horizon
    assert info["threshold_tuning_used"] is True
    assert info["execution_window_overlap"] is False
    tuning_last_return_row = max(tuning.iloc[-1]["execution_path_market_rows"])
    holdout_first_return_row = min(holdout.iloc[0]["execution_path_market_rows"])
    assert tuning_last_return_row < holdout_first_return_row


def test_insufficient_boundary_uses_fixed_threshold_holdout():
    oos = _oos_frame(15, horizon=5)
    tuning, holdout, info = _split_oos_for_thresholding(
        oos,
        {"val_size": 10, "n_folds": 2},
        horizon=5,
    )
    assert tuning.empty
    assert len(holdout) == len(oos)
    assert info["data_split"] == "holdout_only_fixed_thresholds"
    assert info["threshold_tuning_used"] is False
    assert info["holdout_used"] is True


def test_reported_execution_boundary_overlap_disables_tuning():
    oos = _oos_frame(30, horizon=5)
    # A malformed/non-contiguous source path crosses the nominal embargo.
    # The split must fail closed instead of tuning on an overlapping return.
    oos.at[14, "execution_path_market_rows"] = [15, 16, 17, 18, 21]
    tuning, holdout, info = _split_oos_for_thresholding(
        oos,
        {"val_size": 10, "n_folds": 3},
        horizon=5,
    )
    assert tuning.empty
    assert len(holdout) == len(oos)
    assert info["split_reason"] == "execution_windows_overlap_after_embargo"
    assert info["threshold_tuning_used"] is False


def _labelled_oos_fixture(rows=40):
    dates = pd.date_range("2026-01-01", periods=rows, freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "market_as_of_date": dates,
            "entry_date": dates,
            "execution_exit_date": dates,
            "market_row_number": range(rows),
            "entry_price": np.full(rows, 100.0),
            "execution_exit_price": np.full(rows, 101.0),
            "entry_session_return": np.full(rows, 0.01),
            "continuation_session_return": np.full(rows, 0.0),
            "execution_path_returns": [[0.01] * 4 for _ in range(rows)],
            "execution_path_dates": [[date] * 4 for date in dates],
            "execution_path_market_rows": [
                list(range(row + 1, row + 5)) for row in range(rows)
            ],
            "fwd_return": np.full(rows, 0.01),
            "volatility": np.full(rows, 0.02),
            "target_class": np.tile([0, 1], rows // 2),
            "execution_contract_version": EXECUTION_CONTRACT_VERSION,
            "f": np.arange(rows, dtype=float),
        }
    )


def test_oos_collection_uses_only_purged_internal_validation_for_training():
    class DummyBooster:
        def predict(self, features):
            return np.full(len(features), 0.5)

    labelled = _labelled_oos_fixture()
    config = {
        "val_size": 5,
        "n_folds": 2,
        "train_min_rows": 10,
        "purge_gap": 2,
    }
    split_info = {
        "internal_train_end": 15,
        "internal_validation_start": 19,
        "external_oos_used_for_training": False,
    }

    with (
        patch("src.backtest.FEATURE_COLS", ["f"]),
        patch(
            "src.backtest.train_with_purged_internal_validation",
            return_value=(DummyBooster(), split_info),
        ) as train_mock,
    ):
        oos = _collect_oos_predictions(labelled, config, horizon=4)

    assert len(oos) == 10
    assert oos["date"].tolist() == labelled.iloc[30:40]["date"].tolist()
    assert oos.attrs["effective_purge_gap"] == 4
    assert oos.attrs["external_oos_used_for_training"] is False
    assert len(oos.attrs["training_splits"]) == 2

    calls = train_mock.call_args_list
    assert [call.kwargs["train_pool_end"] for call in calls] == [31, 26]
    assert all(call.kwargs["effective_horizon_days"] == 4 for call in calls)
    assert all(call.args[0] is labelled for call in calls)
    for call, val_start in zip(calls, [35, 30]):
        # All externally purged rows remain outside the permitted train pool;
        # the external fold itself is used only by DummyBooster.predict().
        assert call.kwargs["train_pool_end"] <= val_start - 4


def test_gate_minimum_rows_uses_horizon_aware_training_requirement():
    labelled = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=100)})
    config = {
        "enabled": True,
        "auto_threshold_enabled": True,
        "auto_threshold_objective": "avg_daily_net_return",
        "auto_threshold_min_round_trips": 8,
        "cost_bps": 10.0,
        "slippage_bps": 5.0,
        "purge_gap": 2,
    }
    label_config = {"label_mode": "triple_barrier", "tb_max_days": 4}

    with (
        patch("src.backtest._prepare_labelled_data", return_value=labelled),
        patch("src.backtest.phase1_training_min_rows", return_value=101) as minimum,
    ):
        result = evaluate_kpi_gate(pd.DataFrame(), config, label_config)

    minimum.assert_called_once_with(config, effective_horizon_days=4)
    assert result["reason"] == "insufficient_rows"
    assert result["failures"] == ["rows<101"]
    optimization = result["threshold_optimization"]
    assert optimization["effective_purge_gap"] == 4
    assert optimization["required_rows"] == 101
    assert optimization["external_oos_used_for_training"] is False
    assert optimization["oos_training_splits"] == []


def test_sparse_best_threshold_is_diagnostic_only_when_no_candidate_is_feasible():
    default = resolve_thresholds()
    sparse = resolve_thresholds({**default, "buy": 0.85})
    tuning_oos = pd.DataFrame({"row": [1]})
    config = {
        "auto_threshold_enabled": True,
        "auto_threshold_objective": "avg_daily_net_return",
        "sample_sufficiency_metric": "independent_signal_cohorts",
        "auto_threshold_min_independent_signal_cohorts": 8,
        # A deliberately lower global gate minimum must not make the sparse
        # optimized threshold actionable.
        "min_independent_signal_cohorts": 1,
        "min_avg_daily_net_return": -1.0,
        "min_cagr": -1.0,
        "max_drawdown": 1.0,
        "min_sharpe": -1.0,
    }

    def fake_simulation(_oos, _config, *, thresholds, horizon):
        frame = pd.DataFrame({"net_return": [0.0]})
        frame.attrs["threshold_kind"] = (
            "sparse" if thresholds["buy"] == sparse["buy"] else "default"
        )
        return frame

    def fake_metrics(simulation, horizon=1):
        sparse_candidate = simulation.attrs["threshold_kind"] == "sparse"
        return {
            "round_trips": 2 if sparse_candidate else 1,
            "independent_signal_cohorts": 2 if sparse_candidate else 1,
            "avg_daily_net_return": 0.50 if sparse_candidate else 0.01,
            "sharpe": 2.0 if sparse_candidate else 0.5,
            "cagr": 1.0 if sparse_candidate else 0.1,
            "net_return_total": 1.0 if sparse_candidate else 0.1,
            "max_drawdown": -0.1,
        }

    with (
        patch(
            "src.backtest._build_threshold_candidates", return_value=[default, sparse]
        ),
        patch("src.backtest._simulate_strategy", side_effect=fake_simulation),
        patch("src.backtest._compute_metrics", side_effect=fake_metrics),
    ):
        selected, metadata = _optimize_thresholds(tuning_oos, config, horizon=5)

    assert selected == default
    assert metadata["optimized"] is False
    assert metadata["selection"] == "default_no_feasible_candidate"
    assert metadata["selected_round_trips"] == 1
    diagnostic = metadata["best_any_diagnostic"]
    assert diagnostic["thresholds"] == sparse
    assert diagnostic["round_trips"] == 2
    assert diagnostic["sample_count"] == 2
    assert diagnostic["rejected_reason"] == "independent_signal_cohorts<8"
    # The global KPI gate can pass the default's one trip, but never receives
    # the rejected high-scoring sparse threshold.
    assert _evaluate_gate_rules(metadata["selected_metrics"], config) == []


def test_canonical_kpi_env_names_populate_compatibility_aliases():
    with patch.dict(
        "os.environ",
        {
            "TRADER_KPI_MIN_AVG_DAILY_NET_RETURN": "0.002",
            "TRADER_KPI_MIN_ROUND_TRIPS": "12",
            "TRADER_AUTO_THRESHOLD_MIN_ROUND_TRIPS": "9",
            "TRADER_AUTO_THRESHOLD_OBJECTIVE": "avg_daily_net_return",
        },
        clear=True,
    ):
        config = get_backtest_gate_config()
    assert config["min_avg_daily_net_return"] == 0.002
    assert config["min_expectancy"] == 0.002
    assert config["min_round_trips"] == 12
    assert config["min_trades"] == 12
    assert config["auto_threshold_min_round_trips"] == 9
    assert config["auto_threshold_min_trades"] == 9
    assert config["sample_sufficiency_metric"] == "independent_signal_cohorts"
    assert config["min_independent_signal_cohorts"] == 5
    assert config["auto_threshold_min_independent_signal_cohorts"] == 8


def test_legacy_kpi_env_names_warn_and_map_to_canonical_contract():
    with patch.dict(
        "os.environ",
        {
            "TRADER_KPI_MIN_EXPECTANCY": "0.003",
            "TRADER_KPI_MIN_TRADES": "13",
            "TRADER_AUTO_THRESHOLD_MIN_TRADES": "7",
            "TRADER_AUTO_THRESHOLD_OBJECTIVE": "expectancy",
        },
        clear=True,
    ):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config = get_backtest_gate_config()
    assert config["min_avg_daily_net_return"] == 0.003
    assert config["min_round_trips"] == 13
    assert config["auto_threshold_min_round_trips"] == 7
    assert config["auto_threshold_objective"] == "avg_daily_net_return"
    assert len([item for item in caught if item.category is FutureWarning]) == 4


ALL_TESTS = [
    test_no_trade_metrics_are_zero,
    test_continuous_holding_is_one_round_trip,
    test_horizon_overlap_is_removed_from_independent_signal_cohorts,
    test_market_row_positions_define_independent_cohort_spacing,
    test_staged_position_is_not_counted_as_multiple_round_trips,
    test_position_reversal_completes_two_round_trips,
    test_gate_and_objective_use_all_session_net_returns,
    test_gate_required_metrics_fail_closed_when_missing_or_nonfinite,
    test_gate_uses_independent_signal_cohort_minimum_when_configured,
    test_threshold_holdout_has_horizon_embargo_and_no_return_overlap,
    test_insufficient_boundary_uses_fixed_threshold_holdout,
    test_reported_execution_boundary_overlap_disables_tuning,
    test_oos_collection_uses_only_purged_internal_validation_for_training,
    test_gate_minimum_rows_uses_horizon_aware_training_requirement,
    test_sparse_best_threshold_is_diagnostic_only_when_no_candidate_is_feasible,
    test_canonical_kpi_env_names_populate_compatibility_aliases,
    test_legacy_kpi_env_names_warn_and_map_to_canonical_contract,
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
