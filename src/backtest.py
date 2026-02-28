from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .config import DOCS_DIR
from .model import FEATURE_COLS, _train_single_fold
from .predictor import action_from_probability, resolve_thresholds

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


def _prepare_labelled_data(df, config):
    labelled = df.copy()
    labelled["next_close"] = labelled["close"].shift(-1)
    labelled = labelled.dropna(subset=["next_close"]).reset_index(drop=True)
    labelled["target"] = (labelled["next_close"] > labelled["close"]).astype(int)

    max_date = labelled["date"].max()
    start_date = max_date - timedelta(days=365 * int(config["validation_years"]))
    labelled = labelled[labelled["date"] >= start_date].reset_index(drop=True)
    return labelled


def _collect_oos_predictions(labelled, config):
    n = len(labelled)
    val_size = int(config["val_size"])
    purge_gap = int(config["purge_gap"])
    n_folds = int(config["n_folds"])
    min_train_rows = int(config["train_min_rows"])
    fold_frames = []

    for fold_idx in range(n_folds):
        val_end = n - fold_idx * val_size
        val_start = val_end - val_size
        train_end = val_start - purge_gap

        if val_start < 0:
            break
        if train_end < min_train_rows:
            continue

        train_fold = labelled.iloc[:train_end]
        val_fold = labelled.iloc[val_start:val_end]
        if val_fold.empty:
            continue

        model_fold = _train_single_fold(
            train_fold[FEATURE_COLS], train_fold["target"],
            val_fold[FEATURE_COLS], val_fold["target"],
            seed=42 + fold_idx,
        )

        predicted = val_fold[["date", "close", "next_close", "volatility"]].copy()
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
    return oos


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
                    for volatility_limit in _AUTO_THRESHOLD_CANDIDATES["volatility_limit"]:
                        if buy - mild_buy < min_gap:
                            continue
                        if mild_buy - mild_sell < min_gap:
                            continue
                        if mild_sell - sell < min_gap:
                            continue

                        try:
                            threshold = resolve_thresholds({
                                "buy": buy,
                                "mild_buy": mild_buy,
                                "mild_sell": mild_sell,
                                "sell": sell,
                                "volatility_limit": volatility_limit,
                            })
                        except ValueError:
                            continue

                        signature = _threshold_signature(threshold)
                        if signature in seen:
                            continue
                        seen.add(signature)
                        candidates.append(threshold)

    return candidates


def _simulate_strategy(oos, config, thresholds=None):
    if oos.empty:
        return oos

    t = resolve_thresholds(thresholds)
    sim = oos.copy()
    sim["action"] = [
        action_from_probability(prob_up=row.prob_up, volatility=row.volatility, thresholds=t)
        for row in sim.itertuples(index=False)
    ]
    sim["position"] = [
        _to_position(action, allow_short=bool(config["allow_short"]))
        for action in sim["action"]
    ]
    sim["next_return"] = sim["next_close"] / sim["close"] - 1.0
    sim["prev_position"] = sim["position"].shift(1).fillna(0.0)
    sim["turnover"] = (sim["position"] - sim["prev_position"]).abs()
    fee_rate = (float(config["cost_bps"]) + float(config["slippage_bps"])) / 10000.0
    sim["gross_return"] = sim["position"] * sim["next_return"]
    sim["cost_return"] = sim["turnover"] * fee_rate
    sim["net_return"] = sim["gross_return"] - sim["cost_return"]
    sim["equity"] = (1.0 + sim["net_return"]).cumprod()
    return sim


def _compute_metrics(sim):
    if sim.empty:
        return {
            "oos_days": 0,
            "trades": 0,
            "cagr": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "expectancy": 0.0,
            "turnover": 0.0,
            "net_return_total": 0.0,
        }

    oos_days = len(sim)
    trade_mask = sim["turnover"] > 0
    trades = int(trade_mask.sum())

    total_return = float(sim["equity"].iloc[-1] - 1.0)
    years = oos_days / 252.0
    if years > 0 and sim["equity"].iloc[-1] > 0:
        cagr = float(sim["equity"].iloc[-1] ** (1.0 / years) - 1.0)
    else:
        cagr = -1.0

    peaks = sim["equity"].cummax()
    drawdowns = sim["equity"] / peaks - 1.0
    max_drawdown = float(drawdowns.min()) if not drawdowns.empty else 0.0

    daily_mean = float(sim["net_return"].mean())
    daily_std = float(sim["net_return"].std(ddof=0))
    if daily_std > 0:
        sharpe = float(np.sqrt(252.0) * daily_mean / daily_std)
    else:
        sharpe = 0.0

    expectancy = float(sim.loc[trade_mask, "net_return"].mean()) if trades > 0 else 0.0
    turnover = float(sim["turnover"].mean())

    return {
        "oos_days": int(oos_days),
        "trades": trades,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "expectancy": expectancy,
        "turnover": turnover,
        "net_return_total": total_return,
    }


def _evaluate_gate_rules(metrics, config):
    failures = []

    if metrics["trades"] < int(config["min_trades"]):
        failures.append(f"trades<{int(config['min_trades'])}")
    if metrics["cagr"] < float(config["min_cagr"]):
        failures.append(f"cagr<{float(config['min_cagr']):.1%}")
    if metrics["expectancy"] < float(config["min_expectancy"]):
        failures.append(f"expectancy<{float(config['min_expectancy']):.3%}")
    if abs(metrics["max_drawdown"]) > float(config["max_drawdown"]):
        failures.append(f"max_dd>{float(config['max_drawdown']):.1%}")
    if metrics["sharpe"] < float(config["min_sharpe"]):
        failures.append(f"sharpe<{float(config['min_sharpe']):.2f}")

    return failures


def _score_for_objective(metrics, objective):
    if objective == "cagr":
        return float(metrics["cagr"])
    if objective == "sharpe":
        return float(metrics["sharpe"])
    if objective == "net_return":
        return float(metrics["net_return_total"])
    return float(metrics["expectancy"])


def _optimize_thresholds(oos, config):
    default_thresholds = resolve_thresholds()
    enabled = bool(config.get("auto_threshold_enabled", True))
    objective = str(config.get("auto_threshold_objective", "expectancy")).strip().lower()
    min_trades = int(config.get("auto_threshold_min_trades", 8))

    if oos.empty or not enabled:
        return default_thresholds, {
            "enabled": enabled,
            "optimized": False,
            "objective": objective,
            "min_trades": min_trades,
            "candidate_count": 1,
            "selection": "default",
        }

    candidates = _build_threshold_candidates(config)
    best_any = None
    best_feasible = None

    for threshold in candidates:
        sim = _simulate_strategy(oos, config, thresholds=threshold)
        metrics = _compute_metrics(sim)
        score = _score_for_objective(metrics, objective)
        rank = (score, metrics["sharpe"], metrics["cagr"], metrics["net_return_total"])
        candidate = {
            "thresholds": threshold,
            "metrics": metrics,
            "rank": rank,
            "trades": int(metrics["trades"]),
            "score": score,
        }

        if best_any is None or candidate["rank"] > best_any["rank"]:
            best_any = candidate

        if candidate["trades"] >= min_trades:
            if best_feasible is None or candidate["rank"] > best_feasible["rank"]:
                best_feasible = candidate

    selected = best_feasible if best_feasible is not None else best_any
    if selected is None:
        return default_thresholds, {
            "enabled": enabled,
            "optimized": False,
            "objective": objective,
            "min_trades": min_trades,
            "candidate_count": len(candidates),
            "selection": "default",
        }

    selected_thresholds = resolve_thresholds(selected["thresholds"])
    optimized = _threshold_signature(selected_thresholds) != _threshold_signature(default_thresholds)
    selection = "feasible_best" if best_feasible is not None else "fallback_best_any"

    return selected_thresholds, {
        "enabled": enabled,
        "optimized": optimized,
        "objective": objective,
        "min_trades": min_trades,
        "candidate_count": len(candidates),
        "selection": selection,
        "selected_score": float(selected["score"]),
        "selected_trades": int(selected["trades"]),
    }


def evaluate_kpi_gate(df, config):
    default_thresholds = resolve_thresholds()
    if not bool(config.get("enabled", True)):
        return {
            "passed": True,
            "skipped": True,
            "reason": "gate_disabled",
            "metrics": _compute_metrics(pd.DataFrame()),
            "failures": [],
            "thresholds": default_thresholds,
            "threshold_optimization": {
                "enabled": bool(config.get("auto_threshold_enabled", True)),
                "optimized": False,
                "objective": str(config.get("auto_threshold_objective", "expectancy")).strip().lower(),
                "min_trades": int(config.get("auto_threshold_min_trades", 8)),
                "candidate_count": 1,
                "selection": "default",
            },
        }

    labelled = _prepare_labelled_data(df, config)
    min_required = int(config["train_min_rows"]) + int(config["val_size"]) + int(config["purge_gap"])
    if len(labelled) < min_required:
        return {
            "passed": False,
            "skipped": False,
            "reason": "insufficient_rows",
            "metrics": _compute_metrics(pd.DataFrame()),
            "failures": [f"rows<{min_required}"],
            "thresholds": default_thresholds,
            "threshold_optimization": {
                "enabled": bool(config.get("auto_threshold_enabled", True)),
                "optimized": False,
                "objective": str(config.get("auto_threshold_objective", "expectancy")).strip().lower(),
                "min_trades": int(config.get("auto_threshold_min_trades", 8)),
                "candidate_count": 1,
                "selection": "default",
            },
        }

    oos = _collect_oos_predictions(labelled, config)
    thresholds, threshold_optimization = _optimize_thresholds(oos, config)
    sim = _simulate_strategy(oos, config, thresholds=thresholds)
    metrics = _compute_metrics(sim)
    failures = _evaluate_gate_rules(metrics, config)

    return {
        "passed": len(failures) == 0,
        "skipped": False,
        "reason": "ok" if not failures else "kpi_failed",
        "metrics": metrics,
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
        f"Exp={metrics.get('expectancy', 0.0):.3%}, "
        f"Trades={metrics.get('trades', 0)}, "
        f"Thr{optimized_flag}=B{thresholds['buy']:.2f}/MB{thresholds['mild_buy']:.2f}/"
        f"MS{thresholds['mild_sell']:.2f}/S{thresholds['sell']:.2f}"
    )


def write_backtest_report(entries):
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entries": entries,
    }
    output_path = DOCS_DIR / "backtest_report.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return output_path
