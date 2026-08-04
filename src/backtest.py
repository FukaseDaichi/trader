from __future__ import annotations

import json
import warnings
from datetime import timedelta

import numpy as np
import pandas as pd

from .config import DOCS_DIR, get_label_config
from .execution import EXECUTION_CONTRACT_VERSION, execution_contract_metadata
from .labels import build_labelled_frame, effective_horizon
from .model import (
    FEATURE_COLS,
    phase1_training_min_rows,
    resolve_purge_gap,
    train_with_purged_internal_validation,
)
from .predictor import action_from_probability, resolve_thresholds
from .timeutil import now_jst_iso

_LONG_ONLY_POSITION = {
    "BUY": 1.0,
    "MILD_BUY": 0.5,
    "HOLD": 0.0,
    "MILD_SELL": 0.0,
    "SELL": 0.0,
}

_LONG_SHORT_POSITION = {
    "BUY": 1.0,
    "MILD_BUY": 0.5,
    "HOLD": 0.0,
    "MILD_SELL": -0.5,
    "SELL": -1.0,
}

_AUTO_THRESHOLD_CANDIDATES = {
    "buy": [0.70, 0.75, 0.80, 0.85, 0.90],
    "mild_buy": [0.55, 0.60, 0.65, 0.70, 0.75],
    "mild_sell": [0.20, 0.25, 0.30, 0.35, 0.40],
    "sell": [0.05, 0.10, 0.15, 0.20],
    "volatility_limit": [0.03, 0.04, 0.05],
}


def _prepare_labelled_data(df, config, label_config):
    """
    Build a horizon-aware labelled frame carrying the executable entry-session
    and continuation-session returns required for sleeve mark-to-market, plus
    the fixed-H ``fwd_return`` and probability-head label.
    """
    work = df.copy().sort_values("date").reset_index(drop=True)

    labelled = build_labelled_frame(work, label_config)
    if labelled.empty:
        return labelled
    labelled = labelled.dropna(
        subset=["entry_session_return", "continuation_session_return"]
    ).reset_index(drop=True)

    max_date = labelled["date"].max()
    start_date = max_date - timedelta(days=365 * int(config["validation_years"]))
    labelled = labelled[labelled["date"] >= start_date].reset_index(drop=True)
    return labelled


def _collect_oos_predictions(labelled, config, horizon=1):
    """Predict external OOS folds using only internally validated models.

    Boosting rounds are selected from a purged validation block inside
    ``train_pool_end``. The following external fold is passed only to
    ``predict()``, so it remains genuine OOS evidence.
    """
    n = len(labelled)
    val_size = int(config["val_size"])
    effective_horizon_days = max(1, int(horizon))
    purge_gap = resolve_purge_gap(
        config,
        effective_horizon_days=effective_horizon_days,
    )
    n_folds = int(config["n_folds"])
    min_train_rows = int(config["train_min_rows"])
    fold_frames = []
    training_splits = []

    for fold_idx in range(n_folds):
        val_end = n - fold_idx * val_size
        val_start = val_end - val_size
        train_end = val_start - purge_gap

        if val_start < 0:
            break
        if train_end < min_train_rows:
            continue

        val_fold = labelled.iloc[val_start:val_end]
        if val_fold.empty:
            continue

        model_fold, internal_split = train_with_purged_internal_validation(
            labelled,
            FEATURE_COLS,
            train_pool_end=train_end,
            runtime_config=config,
            effective_horizon_days=effective_horizon_days,
            seed=42 + fold_idx,
        )
        if model_fold is None:
            continue
        training_splits.append(
            {
                "fold": fold_idx,
                "external_oos_start": val_start,
                "external_oos_end": val_end,
                "external_purge_gap": purge_gap,
                **internal_split,
            }
        )

        predicted = val_fold[
            [
                "date",
                "market_as_of_date",
                "entry_date",
                "execution_exit_date",
                "market_row_number",
                "entry_price",
                "execution_exit_price",
                "entry_session_return",
                "continuation_session_return",
                "execution_path_returns",
                "execution_path_dates",
                "execution_path_market_rows",
                "fwd_return",
                "volatility",
                "target_class",
                "execution_contract_version",
            ]
        ].copy()
        predicted["prob_up"] = model_fold.predict(val_fold[FEATURE_COLS])
        fold_frames.append(predicted)

    if not fold_frames:
        return pd.DataFrame()

    oos = (
        pd.concat(fold_frames, ignore_index=True)
        .sort_values("date")
        .drop_duplicates(subset=["date"], keep="first")
        .reset_index(drop=True)
    )
    oos.attrs["training_splits"] = training_splits
    oos.attrs["effective_purge_gap"] = purge_gap
    oos.attrs["external_oos_used_for_training"] = False
    return oos


def _execution_window_bounds(frame, horizon):
    """Return inclusive execution-row bounds for an OOS decision frame."""
    if frame.empty:
        return None, None

    execution_rows = []
    if "execution_path_market_rows" in frame.columns:
        for value in frame["execution_path_market_rows"]:
            if isinstance(value, (list, tuple, np.ndarray)):
                execution_rows.extend(int(row) for row in value)
    if execution_rows:
        return min(execution_rows), max(execution_rows)

    if "market_row_number" in frame.columns:
        decision_rows = pd.to_numeric(
            frame["market_row_number"], errors="coerce"
        ).dropna()
        if not decision_rows.empty:
            h = max(1, int(horizon))
            return int(decision_rows.min()) + 1, int(decision_rows.max()) + h
    return None, None


def _holdout_only_split(oos, horizon, reason):
    """Use fixed thresholds and all OOS rows when tuning cannot be isolated."""
    holdout_start, holdout_end = _execution_window_bounds(oos, horizon)
    return (
        oos.iloc[:0].copy(),
        oos.copy().reset_index(drop=True),
        {
            "data_split": "holdout_only_fixed_thresholds",
            "split_reason": reason,
            "tuning_rows": 0,
            "embargo_rows": 0,
            "embargo_required_rows": max(1, int(horizon)),
            "holdout_rows": int(len(oos)),
            "holdout_used": not oos.empty,
            "threshold_tuning_used": False,
            "tuning_execution_end_row": None,
            "holdout_execution_start_row": holdout_start,
            "holdout_execution_end_row": holdout_end,
            "execution_window_overlap": False,
        },
    )


def _split_oos_for_thresholding(oos, config, horizon=1):
    """
    Split OOS chronologically into tuning and final-gate holdout data.

    At least H decision rows are embargoed between the two sets, where H is
    the execution horizon. If an isolated tuning set is unavailable, threshold
    optimization is disabled and all OOS rows become a fixed-threshold
    holdout. Threshold selection and KPI gating therefore never share rows or
    overlapping execution-return windows.
    """
    h = max(1, int(horizon))
    if oos.empty:
        return (
            oos.copy(),
            oos.copy(),
            {
                "data_split": "empty",
                "split_reason": "no_oos_rows",
                "tuning_rows": 0,
                "embargo_rows": 0,
                "embargo_required_rows": h,
                "holdout_rows": 0,
                "holdout_used": False,
                "threshold_tuning_used": False,
                "tuning_execution_end_row": None,
                "holdout_execution_start_row": None,
                "holdout_execution_end_row": None,
                "execution_window_overlap": False,
            },
        )

    val_size = max(1, int(config.get("val_size", 60)))
    n_folds = max(1, int(config.get("n_folds", 3)))
    if n_folds < 2:
        return _holdout_only_split(oos, h, reason="fewer_than_two_oos_folds")

    min_rows_for_split = val_size + h + 1
    if len(oos) < min_rows_for_split:
        return _holdout_only_split(
            oos,
            h,
            reason="insufficient_rows_for_embargoed_holdout",
        )

    holdout_rows = min(val_size, max(len(oos) - 1, 0))
    split_index = len(oos) - holdout_rows
    tuning_end = split_index - h
    if holdout_rows <= 0 or tuning_end <= 0:
        return _holdout_only_split(oos, h, reason="no_rows_before_embargo")

    tuning_oos = oos.iloc[:tuning_end].reset_index(drop=True)
    embargo_oos = oos.iloc[tuning_end:split_index].reset_index(drop=True)
    holdout_oos = oos.iloc[split_index:].reset_index(drop=True)
    if tuning_oos.empty or holdout_oos.empty:
        return _holdout_only_split(oos, h, reason="empty_side_after_embargo")

    _, tuning_execution_end = _execution_window_bounds(tuning_oos, h)
    holdout_execution_start, holdout_execution_end = _execution_window_bounds(
        holdout_oos, h
    )
    overlap = (
        tuning_execution_end is not None
        and holdout_execution_start is not None
        and tuning_execution_end >= holdout_execution_start
    )
    if overlap:
        return _holdout_only_split(
            oos,
            h,
            reason="execution_windows_overlap_after_embargo",
        )

    return (
        tuning_oos,
        holdout_oos,
        {
            "data_split": "chronological_embargoed_holdout",
            "split_reason": "ok",
            "tuning_rows": int(len(tuning_oos)),
            "embargo_rows": int(len(embargo_oos)),
            "embargo_required_rows": h,
            "holdout_rows": int(len(holdout_oos)),
            "holdout_used": True,
            "threshold_tuning_used": True,
            "tuning_execution_end_row": tuning_execution_end,
            "holdout_execution_start_row": holdout_execution_start,
            "holdout_execution_end_row": holdout_execution_end,
            "execution_window_overlap": False,
        },
    )


def _to_position(action, allow_short):
    if allow_short:
        return _LONG_SHORT_POSITION.get(action, 0.0)
    return _LONG_ONLY_POSITION.get(action, 0.0)


def _threshold_signature(thresholds):
    t = resolve_thresholds(thresholds)
    return (
        round(t["buy"], 6),
        round(t["mild_buy"], 6),
        round(t["mild_sell"], 6),
        round(t["sell"], 6),
        round(t["volatility_limit"], 6),
    )


def _build_threshold_candidates(config):
    default_thresholds = resolve_thresholds()
    if not bool(config.get("auto_threshold_enabled", True)):
        return [default_thresholds]

    min_gap = float(config.get("auto_threshold_min_gap", 0.05))
    candidates = [default_thresholds]
    seen = {_threshold_signature(default_thresholds)}

    for buy in _AUTO_THRESHOLD_CANDIDATES["buy"]:
        for mild_buy in _AUTO_THRESHOLD_CANDIDATES["mild_buy"]:
            for mild_sell in _AUTO_THRESHOLD_CANDIDATES["mild_sell"]:
                for sell in _AUTO_THRESHOLD_CANDIDATES["sell"]:
                    for volatility_limit in _AUTO_THRESHOLD_CANDIDATES[
                        "volatility_limit"
                    ]:
                        if buy - mild_buy < min_gap:
                            continue
                        if mild_buy - mild_sell < min_gap:
                            continue
                        if mild_sell - sell < min_gap:
                            continue

                        try:
                            threshold = resolve_thresholds(
                                {
                                    "buy": buy,
                                    "mild_buy": mild_buy,
                                    "mild_sell": mild_sell,
                                    "sell": sell,
                                    "volatility_limit": volatility_limit,
                                }
                            )
                        except ValueError:
                            continue

                        signature = _threshold_signature(threshold)
                        if signature in seen:
                            continue
                        seen.add(signature)
                        candidates.append(threshold)

    return candidates


def _simulate_strategy(oos, config, thresholds=None, horizon=1):
    """
    Executable overlapping-sleeve simulation.

    Every decision opens a sleeve of at most ``1 / horizon`` capital at the
    next session open and holds it through the H-th session close.  A new
    sleeve earns next-open-to-close on its first day; sleeves already held at
    the previous close earn close-to-close, including the overnight move.
    Entry and scheduled-exit notional are both charged configured cost and
    slippage.  This keeps aggregate gross capital <= 1 and avoids applying one
    intraday return to positions that were already exposed overnight.
    """
    if oos.empty:
        return oos

    t = resolve_thresholds(thresholds)
    h = max(1, int(horizon))
    sim = oos.copy()
    sim["action"] = [
        action_from_probability(
            prob_up=row.prob_up, volatility=row.volatility, thresholds=t
        )
        for row in sim.itertuples(index=False)
    ]
    sim["position"] = [
        _to_position(action, allow_short=bool(config["allow_short"]))
        for action in sim["action"]
    ]
    events: dict[int, dict] = {}
    for row in sim.itertuples(index=False):
        path_returns = row.execution_path_returns
        path_dates = row.execution_path_dates
        path_market_rows = row.execution_path_market_rows
        if not (
            isinstance(path_returns, list)
            and isinstance(path_dates, list)
            and isinstance(path_market_rows, list)
            and len(path_returns) == h
            and len(path_dates) == h
            and len(path_market_rows) == h
        ):
            continue

        sleeve = float(row.position) / h
        for offset, (asset_return, session_date, market_row) in enumerate(
            zip(path_returns, path_dates, path_market_rows)
        ):
            event = events.setdefault(
                int(market_row),
                {
                    "date": session_date,
                    "market_row_number": int(market_row),
                    "exposure": 0.0,
                    "gross_exposure": 0.0,
                    "gross_return": 0.0,
                    "turnover": 0.0,
                    "active_sleeves": 0,
                    "entry_cohorts": 0,
                    "exit_exposure": 0.0,
                },
            )
            event["exposure"] += sleeve
            event["gross_exposure"] += abs(sleeve)
            event["gross_return"] += sleeve * float(asset_return)
            if sleeve != 0.0:
                event["active_sleeves"] += 1
            if offset == 0 and sleeve != 0.0:
                event["entry_cohorts"] += 1
                event["turnover"] += abs(sleeve)
            if offset == h - 1 and sleeve != 0.0:
                event["turnover"] += abs(sleeve)
                event["exit_exposure"] += sleeve

    if not events:
        return pd.DataFrame()

    sim = pd.DataFrame([events[key] for key in sorted(events)])
    fee_rate = (float(config["cost_bps"]) + float(config["slippage_bps"])) / 10000.0
    sim["cost_return"] = sim["turnover"] * fee_rate
    sim["net_return"] = sim["gross_return"] - sim["cost_return"]
    sim["equity"] = (1.0 + sim["net_return"]).cumprod()
    sim["ending_exposure"] = sim["exposure"] - sim["exit_exposure"]
    sim["execution_contract_version"] = EXECUTION_CONTRACT_VERSION
    return sim


_METRICS_SEMANTICS = {
    "turnover_days": "sessions with non-zero entry or exit notional",
    "round_trips": "completed aggregate signed-position episodes",
    "signal_cohorts": "non-zero signal sleeves opened",
    "independent_signal_cohorts": (
        "entry sessions separated by at least the effective horizon"
    ),
    "avg_daily_net_return": "mean net return across every simulated session",
    "expectancy_per_trade": "mean compounded net return of completed round trips",
    "avg_daily_turnover": "mean entry-plus-exit notional across sessions",
    "trades": "deprecated alias of round_trips",
    "expectancy": "deprecated alias of expectancy_per_trade",
    "turnover": "deprecated alias of avg_daily_turnover",
}

_METRICS_SCHEMA_VERSION = 3


def _position_sign(value, tolerance=1e-12):
    numeric = float(value)
    if numeric > tolerance:
        return 1
    if numeric < -tolerance:
        return -1
    return 0


def _completed_round_trip_returns(sim):
    """Compound net returns for completed aggregate-position episodes."""
    if sim.empty or "net_return" not in sim.columns or "exposure" not in sim.columns:
        return []

    net_returns = pd.to_numeric(sim["net_return"], errors="coerce").fillna(0.0)
    exposures = pd.to_numeric(sim["exposure"], errors="coerce").fillna(0.0)
    if "ending_exposure" in sim.columns:
        ending_exposures = pd.to_numeric(
            sim["ending_exposure"], errors="coerce"
        ).fillna(0.0)
    else:
        # Compatibility for pre-v2 simulation frames: the next session's
        # exposure approximates the position remaining after this close.
        ending_exposures = exposures.shift(-1, fill_value=0.0)

    completed = []
    active_sign = 0
    growth = 1.0

    for net_return, exposure, ending_exposure in zip(
        net_returns, exposures, ending_exposures
    ):
        session_sign = _position_sign(exposure)
        ending_sign = _position_sign(ending_exposure)

        # A sign change before this session begins completes the old episode.
        if active_sign != 0 and session_sign != active_sign:
            completed.append(float(growth - 1.0))
            active_sign = 0
            growth = 1.0
        if active_sign == 0 and session_sign != 0:
            active_sign = session_sign

        if active_sign != 0:
            growth *= 1.0 + float(net_return)

        # Scheduled exits (or an end-of-session reversal) complete the episode
        # after including this session's return and transaction costs.
        if active_sign != 0 and ending_sign != active_sign:
            completed.append(float(growth - 1.0))
            active_sign = ending_sign
            growth = 1.0

    return completed


def _independent_signal_cohorts(sim, horizon=1):
    """Count entry sessions after conservatively removing horizon overlap."""
    if sim.empty or "entry_cohorts" not in sim.columns:
        return 0

    h = max(1, int(horizon))
    entries = pd.to_numeric(sim["entry_cohorts"], errors="coerce").fillna(0)
    if "market_row_number" in sim.columns:
        positions = pd.to_numeric(
            sim["market_row_number"], errors="coerce"
        ).reset_index(drop=True)
    else:
        positions = pd.Series(np.arange(len(sim)), dtype=float)

    count = 0
    next_eligible = None
    for position, entry_count in zip(positions, entries.reset_index(drop=True)):
        if entry_count <= 0 or not np.isfinite(position):
            continue
        current = int(position)
        if next_eligible is None or current >= next_eligible:
            count += 1
            next_eligible = current + h
    return count


def _compute_metrics(sim, horizon=1):
    if sim.empty:
        avg_daily_net_return = 0.0
        expectancy_per_trade = 0.0
        avg_daily_turnover = 0.0
        round_trips = 0
        return {
            "metrics_schema_version": _METRICS_SCHEMA_VERSION,
            "metrics_semantics": dict(_METRICS_SEMANTICS),
            "oos_days": 0,
            "turnover_days": 0,
            "round_trips": round_trips,
            "signal_cohorts": 0,
            "independent_signal_cohorts": 0,
            "avg_daily_net_return": avg_daily_net_return,
            "expectancy_per_trade": expectancy_per_trade,
            "avg_daily_turnover": avg_daily_turnover,
            "cagr": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "net_return_total": 0.0,
            "trades": round_trips,
            "expectancy": expectancy_per_trade,
            "turnover": avg_daily_turnover,
        }

    oos_days = len(sim)
    net_returns = pd.to_numeric(sim["net_return"], errors="coerce").fillna(0.0)
    turnover = pd.to_numeric(sim["turnover"], errors="coerce").fillna(0.0)
    equity = (1.0 + net_returns).cumprod()
    turnover_days = int((turnover > 0).sum())
    round_trip_returns = _completed_round_trip_returns(sim)
    round_trips = len(round_trip_returns)
    signal_cohorts = (
        int(pd.to_numeric(sim["entry_cohorts"], errors="coerce").fillna(0).sum())
        if "entry_cohorts" in sim.columns
        else 0
    )
    independent_signal_cohorts = _independent_signal_cohorts(sim, horizon=horizon)

    total_return = float(equity.iloc[-1] - 1.0)
    years = oos_days / 252.0
    if years > 0 and equity.iloc[-1] > 0:
        cagr = float(equity.iloc[-1] ** (1.0 / years) - 1.0)
    else:
        cagr = -1.0

    peaks = equity.cummax()
    drawdowns = equity / peaks - 1.0
    max_drawdown = float(drawdowns.min()) if not drawdowns.empty else 0.0

    avg_daily_net_return = float(net_returns.mean())
    daily_std = float(net_returns.std(ddof=0))
    if daily_std > 0:
        sharpe = float(np.sqrt(252.0) * avg_daily_net_return / daily_std)
    else:
        sharpe = 0.0

    expectancy_per_trade = (
        float(np.mean(round_trip_returns)) if round_trip_returns else 0.0
    )
    avg_daily_turnover = float(turnover.mean())

    return {
        "metrics_schema_version": _METRICS_SCHEMA_VERSION,
        "metrics_semantics": dict(_METRICS_SEMANTICS),
        "oos_days": int(oos_days),
        "turnover_days": turnover_days,
        "round_trips": round_trips,
        "signal_cohorts": signal_cohorts,
        "independent_signal_cohorts": independent_signal_cohorts,
        "avg_daily_net_return": avg_daily_net_return,
        "expectancy_per_trade": expectancy_per_trade,
        "avg_daily_turnover": avg_daily_turnover,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "net_return_total": total_return,
        # Compatibility aliases. Their v2 meanings are explicitly declared in
        # metrics_semantics; no turnover-day-only return mean is emitted.
        "trades": round_trips,
        "expectancy": expectancy_per_trade,
        "turnover": avg_daily_turnover,
    }


def _sample_sufficiency(config, *, auto_threshold=False):
    metric = str(config.get("sample_sufficiency_metric", "round_trips")).strip()
    if metric == "independent_signal_cohorts":
        key = (
            "auto_threshold_min_independent_signal_cohorts"
            if auto_threshold
            else "min_independent_signal_cohorts"
        )
        default = 8 if auto_threshold else 5
    elif metric == "round_trips":
        key = "auto_threshold_min_round_trips" if auto_threshold else "min_round_trips"
        legacy_key = "auto_threshold_min_trades" if auto_threshold else "min_trades"
        default = 8 if auto_threshold else 10
        return metric, int(config.get(key, config.get(legacy_key, default)))
    else:
        raise ValueError(f"unsupported sample sufficiency metric: {metric!r}")
    return metric, int(config.get(key, default))


def _evaluate_gate_rules(metrics, config):
    failures = []

    def finite_metric(name):
        try:
            value = float(metrics.get(name))
        except (AttributeError, TypeError, ValueError):
            return None
        return value if np.isfinite(value) else None

    sample_metric, min_sample_count = _sample_sufficiency(config)
    min_avg_daily_net_return = float(
        config.get(
            "min_avg_daily_net_return",
            config.get("min_expectancy", 0.0001),
        )
    )

    sample_count = finite_metric(sample_metric)
    cagr = finite_metric("cagr")
    avg_daily_net_return = finite_metric("avg_daily_net_return")
    max_drawdown = finite_metric("max_drawdown")
    sharpe = finite_metric("sharpe")

    if sample_count is None:
        failures.append(f"{sample_metric}_unavailable")
    elif sample_count < min_sample_count:
        failures.append(f"{sample_metric}<{min_sample_count}")
    if cagr is None:
        failures.append("cagr_unavailable")
    elif cagr < float(config["min_cagr"]):
        failures.append(f"cagr<{float(config['min_cagr']):.1%}")
    if avg_daily_net_return is None:
        failures.append("avg_daily_net_return_unavailable")
    elif avg_daily_net_return < min_avg_daily_net_return:
        failures.append(f"avg_daily_net_return<{min_avg_daily_net_return:.3%}")
    if max_drawdown is None:
        failures.append("max_drawdown_unavailable")
    elif abs(max_drawdown) > float(config["max_drawdown"]):
        failures.append(f"max_dd>{float(config['max_drawdown']):.1%}")
    if sharpe is None:
        failures.append("sharpe_unavailable")
    elif sharpe < float(config["min_sharpe"]):
        failures.append(f"sharpe<{float(config['min_sharpe']):.2f}")

    return failures


def _canonical_threshold_objective(objective):
    value = str(objective).strip().lower()
    if value == "expectancy":
        warnings.warn(
            "threshold objective 'expectancy' is deprecated; "
            "using 'avg_daily_net_return'",
            FutureWarning,
            stacklevel=2,
        )
        return "avg_daily_net_return"
    if value not in {"avg_daily_net_return", "cagr", "sharpe", "net_return"}:
        raise ValueError(f"unsupported threshold objective: {value!r}")
    return value


def _score_for_objective(metrics, objective):
    objective = _canonical_threshold_objective(objective)
    if objective == "cagr":
        return float(metrics["cagr"])
    if objective == "sharpe":
        return float(metrics["sharpe"])
    if objective == "net_return":
        return float(metrics["net_return_total"])
    return float(metrics["avg_daily_net_return"])


def _optimize_thresholds(tuning_oos, config, horizon=1):
    default_thresholds = resolve_thresholds()
    enabled = bool(config.get("auto_threshold_enabled", True))
    objective = _canonical_threshold_objective(
        config.get("auto_threshold_objective", "avg_daily_net_return")
    )
    sample_metric, min_sample_count = _sample_sufficiency(
        config, auto_threshold=True
    )

    if tuning_oos.empty or not enabled:
        return default_thresholds, {
            "enabled": enabled,
            "optimized": False,
            "objective": objective,
            "sample_sufficiency_metric": sample_metric,
            "min_sample_count": min_sample_count,
            "candidate_count": 1,
            "selection": "default_no_tuning" if tuning_oos.empty else "default",
        }

    candidates = _build_threshold_candidates(config)
    best_any = None
    best_feasible = None
    default_candidate = None

    for threshold in candidates:
        sim = _simulate_strategy(
            tuning_oos, config, thresholds=threshold, horizon=horizon
        )
        metrics = _compute_metrics(sim, horizon=horizon)
        score = _score_for_objective(metrics, objective)
        rank = (score, metrics["sharpe"], metrics["cagr"], metrics["net_return_total"])
        candidate = {
            "thresholds": threshold,
            "metrics": metrics,
            "rank": rank,
            "round_trips": int(metrics["round_trips"]),
            "sample_count": int(metrics[sample_metric]),
            "score": score,
        }
        if _threshold_signature(threshold) == _threshold_signature(default_thresholds):
            default_candidate = candidate

        if best_any is None or candidate["rank"] > best_any["rank"]:
            best_any = candidate

        if candidate["sample_count"] >= min_sample_count:
            if best_feasible is None or candidate["rank"] > best_feasible["rank"]:
                best_feasible = candidate

    if best_feasible is None:
        diagnostic = best_any or default_candidate
        selected_default = default_candidate
        metadata = {
            "enabled": enabled,
            "optimized": False,
            "objective": objective,
            "sample_sufficiency_metric": sample_metric,
            "min_sample_count": min_sample_count,
            "candidate_count": len(candidates),
            "selection": "default_no_feasible_candidate",
        }
        if selected_default is not None:
            metadata.update(
                {
                    "selected_score": float(selected_default["score"]),
                    "selected_round_trips": int(selected_default["round_trips"]),
                    "selected_trades": int(selected_default["round_trips"]),
                    "selected_sample_count": int(selected_default["sample_count"]),
                    "selected_metrics": selected_default["metrics"],
                }
            )
        if diagnostic is not None:
            metadata["best_any_diagnostic"] = {
                "thresholds": resolve_thresholds(diagnostic["thresholds"]),
                "score": float(diagnostic["score"]),
                "round_trips": int(diagnostic["round_trips"]),
                "sample_count": int(diagnostic["sample_count"]),
                "metrics": diagnostic["metrics"],
                "rejected_reason": f"{sample_metric}<{min_sample_count}",
            }
        return default_thresholds, metadata

    selected = best_feasible
    if selected is None:
        return default_thresholds, {
            "enabled": enabled,
            "optimized": False,
            "objective": objective,
            "sample_sufficiency_metric": sample_metric,
            "min_sample_count": min_sample_count,
            "candidate_count": len(candidates),
            "selection": "default",
        }

    selected_thresholds = resolve_thresholds(selected["thresholds"])
    optimized = _threshold_signature(selected_thresholds) != _threshold_signature(
        default_thresholds
    )
    return selected_thresholds, {
        "enabled": enabled,
        "optimized": optimized,
        "objective": objective,
        "sample_sufficiency_metric": sample_metric,
        "min_sample_count": min_sample_count,
        "candidate_count": len(candidates),
        "selection": "feasible_best",
        "selected_score": float(selected["score"]),
        "selected_round_trips": int(selected["round_trips"]),
        "selected_trades": int(selected["round_trips"]),
        "selected_sample_count": int(selected["sample_count"]),
        "selected_metrics": selected["metrics"],
    }


def evaluate_kpi_gate(df, config, label_config=None):
    default_thresholds = resolve_thresholds()
    label_cfg = label_config or get_label_config()
    horizon = effective_horizon(label_cfg)
    requested_label_mode = label_cfg.get("label_mode", "triple_barrier")
    label_mode = (
        "triple_barrier" if requested_label_mode == "vol_norm" else requested_label_mode
    )
    objective = _canonical_threshold_objective(
        config.get("auto_threshold_objective", "avg_daily_net_return")
    )
    sample_metric, min_sample_count = _sample_sufficiency(
        config, auto_threshold=True
    )
    execution_contract = execution_contract_metadata(
        cost_bps=config.get("cost_bps"),
        slippage_bps=config.get("slippage_bps"),
    )
    if not bool(config.get("enabled", True)):
        return {
            "passed": True,
            "skipped": True,
            "reason": "gate_disabled",
            "horizon_days": horizon,
            "label_mode": label_mode,
            "execution_contract": execution_contract,
            "metrics": _compute_metrics(pd.DataFrame(), horizon=horizon),
            "metrics_tuning": _compute_metrics(pd.DataFrame(), horizon=horizon),
            "metrics_holdout": _compute_metrics(pd.DataFrame(), horizon=horizon),
            "failures": [],
            "thresholds": default_thresholds,
            "threshold_optimization": {
                "enabled": bool(config.get("auto_threshold_enabled", True)),
                "optimized": False,
                "objective": objective,
                "sample_sufficiency_metric": sample_metric,
                "min_sample_count": min_sample_count,
                "candidate_count": 1,
                "selection": "default",
                "data_split": "disabled",
                "tuning_rows": 0,
                "embargo_rows": 0,
                "embargo_required_rows": horizon,
                "holdout_rows": 0,
                "holdout_used": False,
                "threshold_tuning_used": False,
                "execution_window_overlap": False,
            },
        }

    labelled = _prepare_labelled_data(df, config, label_cfg)
    effective_purge_gap = resolve_purge_gap(
        config,
        effective_horizon_days=horizon,
    )
    min_required = phase1_training_min_rows(
        config,
        effective_horizon_days=horizon,
    )
    if len(labelled) < min_required:
        return {
            "passed": False,
            "skipped": False,
            "reason": "insufficient_rows",
            "horizon_days": horizon,
            "label_mode": label_mode,
            "execution_contract": execution_contract,
            "metrics": _compute_metrics(pd.DataFrame(), horizon=horizon),
            "metrics_tuning": _compute_metrics(pd.DataFrame(), horizon=horizon),
            "metrics_holdout": _compute_metrics(pd.DataFrame(), horizon=horizon),
            "failures": [f"rows<{min_required}"],
            "thresholds": default_thresholds,
            "threshold_optimization": {
                "enabled": bool(config.get("auto_threshold_enabled", True)),
                "optimized": False,
                "objective": objective,
                "sample_sufficiency_metric": sample_metric,
                "min_sample_count": min_sample_count,
                "candidate_count": 1,
                "selection": "default",
                "data_split": "insufficient_rows",
                "tuning_rows": 0,
                "embargo_rows": 0,
                "embargo_required_rows": horizon,
                "holdout_rows": 0,
                "holdout_used": False,
                "threshold_tuning_used": False,
                "execution_window_overlap": False,
                "effective_purge_gap": effective_purge_gap,
                "required_rows": min_required,
                "external_oos_used_for_training": False,
                "oos_training_splits": [],
            },
        }

    oos = _collect_oos_predictions(labelled, config, horizon=horizon)
    oos_training_splits = list(oos.attrs.get("training_splits") or [])
    tuning_oos, holdout_oos, split_info = _split_oos_for_thresholding(
        oos, config, horizon=horizon
    )
    thresholds, threshold_optimization = _optimize_thresholds(
        tuning_oos, config, horizon=horizon
    )

    sim_tuning = _simulate_strategy(
        tuning_oos, config, thresholds=thresholds, horizon=horizon
    )
    metrics_tuning = _compute_metrics(sim_tuning, horizon=horizon)

    sim_holdout = _simulate_strategy(
        holdout_oos, config, thresholds=thresholds, horizon=horizon
    )
    metrics_holdout = _compute_metrics(sim_holdout, horizon=horizon)

    metrics_for_gate = metrics_holdout
    failures = _evaluate_gate_rules(metrics_for_gate, config)
    threshold_optimization = {
        **threshold_optimization,
        **split_info,
        "threshold_tuning_used": bool(
            split_info.get("threshold_tuning_used")
            and threshold_optimization.get("enabled")
        ),
        "effective_purge_gap": effective_purge_gap,
        "external_oos_used_for_training": bool(
            oos.attrs.get("external_oos_used_for_training", False)
        ),
        "oos_training_splits": oos_training_splits,
    }

    return {
        "passed": len(failures) == 0,
        "skipped": False,
        "reason": "ok" if not failures else "kpi_failed",
        "horizon_days": horizon,
        "label_mode": label_mode,
        "execution_contract": execution_contract,
        "metrics": metrics_for_gate,
        "metrics_tuning": metrics_tuning,
        "metrics_holdout": metrics_holdout,
        "failures": failures,
        "thresholds": thresholds,
        "threshold_optimization": threshold_optimization,
    }


def format_gate_summary(result):
    metrics = result.get("metrics", {})
    thresholds = resolve_thresholds(result.get("thresholds"))
    optimization = result.get("threshold_optimization", {}) or {}
    optimized_flag = "*" if optimization.get("optimized") else "-"
    return (
        f"CAGR={metrics.get('cagr', 0.0):.1%}, "
        f"MaxDD={metrics.get('max_drawdown', 0.0):.1%}, "
        f"Sharpe={metrics.get('sharpe', 0.0):.2f}, "
        f"AvgDailyNet={metrics.get('avg_daily_net_return', 0.0):.3%}, "
        f"IndependentCohorts={metrics.get('independent_signal_cohorts', 0)}, "
        f"RoundTrips={metrics.get('round_trips', 0)}, "
        f"Thr{optimized_flag}=B{thresholds['buy']:.2f}/MB{thresholds['mild_buy']:.2f}/"
        f"MS{thresholds['mild_sell']:.2f}/S{thresholds['sell']:.2f}"
    )


def _evaluate_portfolio_gate_rules(metrics: dict, config: dict) -> list[str]:
    """
    Check portfolio backtest metrics against the four gate thresholds from
    get_portfolio_config():

      backtest_max_dd       max abs drawdown allowed (e.g. 0.25 means 25%).
      backtest_min_sharpe   minimum annualised Sharpe.
      backtest_min_ir       minimum information ratio.
      backtest_max_turnover maximum mean per-period turnover.

    ``max_drawdown`` from the backtest is <= 0, so the rule is
    ``abs(max_drawdown) > backtest_max_dd``.
    """
    failures = []

    def finite_metric(name):
        try:
            value = float(metrics.get(name))
        except (TypeError, ValueError):
            return None
        return value if np.isfinite(value) else None

    max_dd = finite_metric("max_drawdown")
    if max_dd is None:
        failures.append("max_dd_unavailable")
    elif abs(max_dd) > float(config["backtest_max_dd"]):
        failures.append(f"max_dd>{float(config['backtest_max_dd']):.1%}")

    sharpe = finite_metric("sharpe")
    if sharpe is None:
        failures.append("sharpe_unavailable")
    elif sharpe < float(config["backtest_min_sharpe"]):
        failures.append(f"sharpe<{float(config['backtest_min_sharpe']):.2f}")

    ir = finite_metric("information_ratio")
    if ir is None:
        failures.append("ir_unavailable_same_basis")
    elif ir < float(config["backtest_min_ir"]):
        failures.append(f"ir<{float(config['backtest_min_ir']):.2f}")

    turnover = finite_metric("turnover")
    if turnover is None:
        failures.append("turnover_unavailable")
    elif turnover > float(config["backtest_max_turnover"]):
        failures.append(f"turnover>{float(config['backtest_max_turnover']):.2f}")

    return failures


def evaluate_portfolio_kpi_gate(result: dict, config: dict) -> dict:
    """
    Evaluate a portfolio KPI gate from an already-computed
    ``run_portfolio_backtest`` result dict.

    This wrapper consumes the *already-computed* metrics — it does NOT re-run
    the simulation. The gate checks four thresholds read from ``config``
    (expected keys: backtest_max_dd, backtest_min_sharpe, backtest_min_ir,
    backtest_max_turnover). All are present on the dict returned by
    ``get_portfolio_config()``.

    Returns a dict with the same shape as ``evaluate_kpi_gate()``:
      ``{passed, skipped, reason, metrics, failures}``
    """
    # Insufficient / missing result.
    if result is None or result.get("status") != "ok":
        status_str = (result or {}).get("status", "no_result")
        return {
            "passed": False,
            "skipped": False,
            "reason": status_str,
            "metrics": (result or {}).get("metrics", {}),
            "failures": [f"status={status_str}"],
        }

    metrics = result.get("metrics") or {}

    # Empty metrics dict (should not occur for status="ok", but guard anyway).
    if not metrics:
        return {
            "passed": False,
            "skipped": False,
            "reason": "no_metrics",
            "metrics": {},
            "failures": ["no_metrics"],
        }

    failures = _evaluate_portfolio_gate_rules(metrics, config)
    return {
        "passed": len(failures) == 0,
        "skipped": False,
        "reason": "ok" if not failures else "kpi_failed",
        "metrics": metrics,
        "failures": failures,
    }


def format_portfolio_gate_summary(result: dict) -> str:
    """One-line human-readable summary of a portfolio gate result."""
    metrics = result.get("metrics", {})
    failures = result.get("failures", [])
    passed_str = "PASS" if result.get("passed") else "FAIL"
    sharpe = metrics.get("sharpe")
    max_dd = metrics.get("max_drawdown")
    ir = metrics.get("information_ratio")
    turnover = metrics.get("turnover")
    cagr = metrics.get("cagr")
    cagr_s = f"{cagr:.1%}" if cagr is not None else "None"
    max_dd_s = f"{max_dd:.1%}" if max_dd is not None else "None"
    sharpe_s = f"{sharpe:.2f}" if sharpe is not None else "None"
    ir_s = f"{ir:.2f}" if ir is not None else "None"
    turnover_s = f"{turnover:.2f}" if turnover is not None else "None"
    suffix = f" [{', '.join(failures)}]" if failures else ""
    return (
        f"{passed_str} CAGR={cagr_s}, MaxDD={max_dd_s}, "
        f"Sharpe={sharpe_s}, IR={ir_s}, Turnover={turnover_s}{suffix}"
    )


def summarize_holdout(entries):
    """Aggregate holdout usage across gate-passed entries.

    Threshold tuning never shares rows or execution windows with the holdout.
    ``holdout_used=False`` now identifies legacy/malformed report entries (or
    runs that never reached OOS evaluation), and remains surfaced so the
    watchdog and active-mode decision can reject them conservatively.
    """
    gate_passed = 0
    without_holdout = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("passed"):
            continue
        if entry.get("skipped"):
            # gate_disabled passes are not "tuned on the same rows" — they
            # never went through threshold optimization at all.
            continue
        gate_passed += 1
        optimization = entry.get("threshold_optimization") or {}
        if not optimization.get("holdout_used"):
            without_holdout.append(entry.get("ticker"))
    return {
        "gate_passed": gate_passed,
        "passed_without_holdout": len(without_holdout),
        "tickers_without_holdout": without_holdout,
    }


def write_backtest_report(entries):
    label_cfg = get_label_config()
    payload = {
        "generated_at": now_jst_iso(),
        "label_mode": label_cfg.get("label_mode"),
        "horizon_days": effective_horizon(label_cfg),
        "execution_contract": execution_contract_metadata(),
        "holdout_summary": summarize_holdout(entries),
        "entries": entries,
    }
    output_path = DOCS_DIR / "backtest_report.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return output_path
