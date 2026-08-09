"""
Pure performance-detail analytics for the Phase 3 dashboard.

No DB or network dependency. Imports only numpy and src.calibration/db_records.

All functions operate on "detail rows" — dicts with keys:
    market_as_of_date, entry_date, eval_date (str "YYYY-MM-DD"), ticker, name,
    action, conviction (float|None), contract_version, price-basis provenance,
    horizon_days (int), realized_ret (float|None), benchmark_ret (float|None),
    excess_ret (float|None), hit (bool|None), mae, mfe, exit_reason.

Population std (np.std default, ddof=0) is used throughout for Sharpe calculation
— consistent with the existing calibration module's convention.
"""

from __future__ import annotations

import numpy as np

from .db_records import LONG_ACTIONS
from . import calibration
from .execution import (
    EXECUTION_CONTRACT_VERSION,
    SAME_BASIS_BENCHMARK,
    execution_contract_metadata,
)


ACCOUNTING_METHOD = "non_overlapping_cohorts_v1"
DEFAULT_COST_BPS = 10.0
DEFAULT_SLIPPAGE_BPS = 5.0


def _nonnegative_bps(value, default: float) -> float:
    """Normalize a cost input without making best-effort dashboard export fail."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not np.isfinite(numeric) or numeric < 0.0:
        return float(default)
    return numeric


def equity_cost_metadata(
    cost_bps: float = DEFAULT_COST_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
) -> dict:
    """Cost convention used by compact and detailed performance equity."""
    resolved_cost = _nonnegative_bps(cost_bps, DEFAULT_COST_BPS)
    resolved_slippage = _nonnegative_bps(slippage_bps, DEFAULT_SLIPPAGE_BPS)
    per_side = (resolved_cost + resolved_slippage) / 10000.0
    return {
        "return_basis": "net_after_entry_exit_costs",
        "gross_return_source": "signal_outcomes.realized_ret",
        "cost_model": "full_capital_entry_and_exit",
        "benchmark_return_basis": "net_after_same_entry_exit_costs",
        "benchmark_cost_model": "same_round_trip_cost_as_strategy",
        "cost_bps_per_side": resolved_cost,
        "slippage_bps_per_side": resolved_slippage,
        "round_trip_cost_rate": 2.0 * per_side,
    }


def _contract_rows(rows: list[dict]) -> tuple[list[dict], dict]:
    """Keep v2 rows separate from legacy measurements.

    Explicitly-versioned inputs fail closed to the active v2 contract.  The
    unversioned fallback exists only for pure-function callers/old fixtures;
    production DB reads are expected to return ``contract_version``.
    """
    counts: dict[str, int] = {}
    has_explicit_version = False
    for row in rows:
        version = row.get("contract_version")
        if version:
            has_explicit_version = True
            key = str(version)
        else:
            key = "unversioned"
        counts[key] = counts.get(key, 0) + 1

    if has_explicit_version:
        compatible = [
            row
            for row in rows
            if row.get("contract_version") == EXECUTION_CONTRACT_VERSION
        ]
        fallback = None
    else:
        compatible = list(rows)
        fallback = "unversioned_input_assumed_compatible"

    return compatible, {
        "required_contract_version": EXECUTION_CONTRACT_VERSION,
        "source_counts": dict(sorted(counts.items())),
        "included_rows": len(compatible),
        "excluded_rows": len(rows) - len(compatible),
        "fallback_assumption": fallback,
    }


def _eligible_long_rows(rows: list[dict], horizon: int) -> list[dict]:
    compatible, _ = _contract_rows(rows)
    return [
        row
        for row in compatible
        if row.get("action") in LONG_ACTIONS
        and int(row.get("horizon_days") or 0) == int(horizon)
        and row.get("realized_ret") is not None
        and row.get("entry_date")
    ]


def _non_overlapping_cohorts(
    rows: list[dict], horizon: int
) -> tuple[list[tuple[str, str | None, list[dict]]], dict]:
    """Select deterministic, capital-feasible cohorts.

    Preferred selection uses the persisted evaluation date: a new cohort may
    enter only after the prior selected cohort has exited.  If old/unversioned
    input lacks eval_date, choose every H-th available entry cohort.  The
    fallback is explicit in returned metadata and never compounds every
    overlapping H-day outcome.
    """
    filtered = _eligible_long_rows(rows, horizon)
    by_entry: dict[str, list[dict]] = {}
    for row in filtered:
        by_entry.setdefault(str(row["entry_date"]), []).append(row)
    ordered = [(date, by_entry[date]) for date in sorted(by_entry)]

    if not ordered:
        return [], {
            "name": ACCOUNTING_METHOD,
            "selection": "eval_date_non_overlap",
            "fallback_reason": None,
            "eligible_cohorts": 0,
            "selected_cohorts": 0,
            "overlapping_horizon_returns_compounded": False,
            "capital_per_cohort": 1.0,
        }

    eval_dates_available = all(
        all(
            row.get("eval_date") and str(row["eval_date"]) >= str(entry_date)
            for row in cohort_rows
        )
        for entry_date, cohort_rows in ordered
    )
    selected: list[tuple[str, str | None, list[dict]]] = []

    if eval_dates_available:
        active_until: str | None = None
        for entry_date, cohort_rows in ordered:
            if active_until is not None and entry_date <= active_until:
                continue
            cohort_exit = max(str(row["eval_date"]) for row in cohort_rows)
            selected.append((entry_date, cohort_exit, cohort_rows))
            active_until = cohort_exit
        selection = "eval_date_non_overlap"
        fallback_reason = None
    else:
        stride = max(1, int(horizon))
        for entry_date, cohort_rows in ordered[::stride]:
            known_exits = [
                str(row["eval_date"]) for row in cohort_rows if row.get("eval_date")
            ]
            cohort_exit = max(known_exits) if known_exits else None
            selected.append((entry_date, cohort_exit, cohort_rows))
        selection = "horizon_stride_fallback"
        fallback_reason = "eval_date_missing_or_invalid"

    return selected, {
        "name": ACCOUNTING_METHOD,
        "selection": selection,
        "fallback_reason": fallback_reason,
        "eligible_cohorts": len(ordered),
        "selected_cohorts": len(selected),
        "overlapping_horizon_returns_compounded": False,
        "capital_per_cohort": 1.0,
    }


def _equity_curve_with_metadata(
    rows: list[dict],
    horizon: int,
    *,
    cost_bps: float = DEFAULT_COST_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
) -> tuple[list[dict], dict, dict]:
    selected, accounting = _non_overlapping_cohorts(rows, horizon)
    cost_metadata = equity_cost_metadata(cost_bps, slippage_bps)
    accounting = {**accounting, **cost_metadata}
    if not selected:
        coverage = {
            "basis": "same_execution_window_only",
            "available": False,
            "selected_cohorts": 0,
            "available_cohorts": 0,
            "coverage_ratio": None,
            "reason": "no_selected_cohorts",
        }
        return [], accounting, coverage

    periods: list[dict] = []
    for entry_date, eval_date, cohort_rows in selected:
        gross_strategy_ret = float(
            np.mean([row["realized_ret"] for row in cohort_rows])
        )
        cost_return = float(cost_metadata["round_trip_cost_rate"])
        strategy_ret = gross_strategy_ret - cost_return
        benchmark_values = [
            row["benchmark_ret"]
            for row in cohort_rows
            if row.get("benchmark_ret") is not None
        ]
        gross_benchmark_ret = (
            float(np.mean(benchmark_values))
            if len(benchmark_values) == len(cohort_rows)
            else None
        )
        benchmark_ret = (
            gross_benchmark_ret - cost_return
            if gross_benchmark_ret is not None
            else None
        )
        periods.append(
            {
                "entry_date": entry_date,
                "date": eval_date or entry_date,
                "gross_strategy_ret": gross_strategy_ret,
                "cost_return": cost_return,
                "strategy_ret": strategy_ret,
                "gross_benchmark_ret": gross_benchmark_ret,
                "benchmark_ret": benchmark_ret,
                "n": len(cohort_rows),
            }
        )

    available_benchmark = sum(
        1 for period in periods if period["benchmark_ret"] is not None
    )
    complete_benchmark = available_benchmark == len(periods)
    if available_benchmark == 0:
        benchmark_reason = "unavailable_same_basis"
    elif not complete_benchmark:
        benchmark_reason = "partial_same_basis_coverage"
    else:
        benchmark_reason = None

    strategy_equity = 1.0
    benchmark_equity: float | None = 1.0 if complete_benchmark else None
    curve = []
    for period in periods:
        strategy_equity *= 1.0 + period["strategy_ret"]
        if benchmark_equity is not None:
            benchmark_equity *= 1.0 + period["benchmark_ret"]
        curve.append(
            {
                "date": period["date"],
                "entry_date": period["entry_date"],
                "strategy": strategy_equity,
                "benchmark": benchmark_equity,
                "gross_period_return": period["gross_strategy_ret"],
                "cost_return": period["cost_return"],
                "period_return": period["strategy_ret"],
                "gross_benchmark_return": period["gross_benchmark_ret"],
                "benchmark_return": period["benchmark_ret"],
                "n": period["n"],
            }
        )

    coverage = {
        "basis": "same_execution_window_only",
        "available": benchmark_reason is None,
        "selected_cohorts": len(periods),
        "available_cohorts": available_benchmark,
        "coverage_ratio": available_benchmark / len(periods),
        "reason": benchmark_reason,
    }
    return curve, accounting, coverage


def build_equity_curves(
    rows: list[dict],
    horizon: int = 1,
    *,
    cost_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> list[dict]:
    """
    Compound only non-overlapping LONG cohorts at ``horizon``.

    The date axis is the cohort exit date when available.  Benchmark equity is
    ``None`` unless every selected cohort has a same-basis benchmark; missing
    observations are never represented as a flat 1.0 market series.
    """
    curve, _, _ = _equity_curve_with_metadata(
        rows,
        horizon,
        cost_bps=cost_bps,
        slippage_bps=slippage_bps,
    )
    return curve


def build_drawdown(curve: list[dict]) -> list[dict]:
    """
    Compute running drawdown of the strategy equity curve.

    Input = output of build_equity_curves.
    running_peak = max strategy seen so far.
    drawdown = strategy / peak - 1.0  (<= 0).

    Returns list of {"date", "drawdown"}. Empty -> [].
    """
    if not curve:
        return []

    result = []
    peak = 1.0  # equity-curve origin (capital before any compounding)
    for entry in curve:
        s = entry["strategy"]
        if s > peak:
            peak = s
        dd = s / peak - 1.0
        result.append({"date": entry["date"], "drawdown": dd})

    return result


def rolling_metrics(
    rows: list[dict],
    window: int = 20,
    horizon: int | None = None,
    *,
    cost_bps: float = 0.0,
    slippage_bps: float = 0.0,
) -> dict:
    """
    Rolling performance metrics over LONG rows.

    - hit_rate_20d:    mean hit (1/0) over rows in last `window` distinct entry_dates.
    - avg_return_20d:  mean realized_ret over rows in last `window` distinct entry_dates.
    - excess_return_20d: mean excess_ret (non-None) over rows in last `window` dates.
    - sharpe_60d:      non-overlapping cohort returns selected from the last 60
                       signal dates; mean/std * sqrt(252/H). None if <2 cohorts
                       or std==0. This is deliberately not computed from the
                       overlapping H-day signal-quality observations.

    All keys always present; values may be None.
    """
    compatible_rows, _ = _contract_rows(rows)
    long_rows = [
        r
        for r in compatible_rows
        if r.get("action") in LONG_ACTIONS
        and r.get("realized_ret") is not None
        and r.get("entry_date")
    ]
    long_rows_sorted = sorted(long_rows, key=lambda r: r["entry_date"])

    distinct_dates = sorted({r["entry_date"] for r in long_rows_sorted})
    recent_dates = set(distinct_dates[-window:]) if distinct_dates else set()
    recent_rows = [r for r in long_rows_sorted if r["entry_date"] in recent_dates]

    # hit_rate_20d
    hit_vals = [
        1 if r.get("hit") else 0 for r in recent_rows if r.get("hit") is not None
    ]
    hit_rate_20d = float(np.mean(hit_vals)) if hit_vals else None

    # avg_return_20d
    ret_vals = [r["realized_ret"] for r in recent_rows]
    avg_return_20d = float(np.mean(ret_vals)) if ret_vals else None

    # excess_return_20d
    exc_vals = [r["excess_ret"] for r in recent_rows if r.get("excess_ret") is not None]
    excess_return_20d = float(np.mean(exc_vals)) if exc_vals else None

    resolved_horizon = int(horizon or 0)
    if resolved_horizon <= 0:
        horizons = sorted(
            {
                int(row.get("horizon_days") or 0)
                for row in long_rows_sorted
                if int(row.get("horizon_days") or 0) > 0
            }
        )
        resolved_horizon = horizons[0] if len(horizons) == 1 else 1

    # Sharpe: non-overlapping cohorts inside the last 60 distinct signal dates.
    dates_60 = set(distinct_dates[-60:]) if distinct_dates else set()
    rows_60 = [
        r
        for r in long_rows_sorted
        if r["entry_date"] in dates_60
        and int(r.get("horizon_days") or 0) == resolved_horizon
    ]
    sharpe_60d = None
    sharpe_observations = 0
    sharpe_selection = "eval_date_non_overlap"
    sharpe_fallback_reason = None
    sharpe_cost_metadata = equity_cost_metadata(cost_bps, slippage_bps)
    if rows_60:
        selected, accounting = _non_overlapping_cohorts(rows_60, resolved_horizon)
        period_rets = [
            float(np.mean([row["realized_ret"] for row in cohort_rows]))
            - float(sharpe_cost_metadata["round_trip_cost_rate"])
            for _, _, cohort_rows in selected
        ]
        sharpe_observations = len(period_rets)
        sharpe_selection = accounting["selection"]
        sharpe_fallback_reason = accounting["fallback_reason"]
        if len(period_rets) >= 2:
            std = float(np.std(period_rets))
            if std != 0.0:
                periods_per_year = 252.0 / max(1, resolved_horizon)
                sharpe_60d = float(np.mean(period_rets)) / std * (periods_per_year**0.5)

    return {
        "hit_rate_20d": hit_rate_20d,
        "avg_return_20d": avg_return_20d,
        "excess_return_20d": excess_return_20d,
        "sharpe_60d": sharpe_60d,
        "sharpe_observations": sharpe_observations,
        "sharpe_annualization_periods": 252.0 / max(1, resolved_horizon),
        "sharpe_selection": sharpe_selection,
        "sharpe_fallback_reason": sharpe_fallback_reason,
        "sharpe_return_basis": "net_after_entry_exit_costs",
        "sharpe_round_trip_cost_rate": sharpe_cost_metadata["round_trip_cost_rate"],
    }


def build_reliability(pred_rows: list[dict], n_bins: int = 10) -> dict:
    """
    Reliability diagram data from prediction outcome rows.

    Each pred_row needs: prob_up, realized_ret.
    label = 1 if (realized_ret or 0) > 0 else 0.

    Returns {"brier": float|None, "bins": [{bin_low, bin_high, mean_prob, frac_up, count}]}.
    Empty pred_rows -> brier=None; bins has n_bins entries all with count=0.
    """
    valid = [r for r in pred_rows if r.get("realized_ret") is not None]
    prob = [r.get("prob_up") for r in valid]
    label = [1 if r["realized_ret"] > 0 else 0 for r in valid]

    brier = calibration.brier_score(prob, label)
    raw_bins = calibration.reliability_bins(prob, label, n_bins)

    bins = [
        {
            "bin_low": b["lo"],
            "bin_high": b["hi"],
            "mean_prob": b["mean_pred"],
            "frac_up": b["mean_obs"],
            "count": b["count"],
        }
        for b in raw_bins
    ]

    return {"brier": brier, "bins": bins}


def build_recent_outcomes(rows: list[dict], limit: int = 200) -> list[dict]:
    """
    Return the most recent `limit` outcome rows sorted by entry_date DESC, then ticker.

    Includes all actions. Maps each row to the contract dict:
    {entry_date, ticker, name, action, conviction, horizon_days, realized_ret,
     benchmark_ret, excess_ret, hit, mae, mfe, exit_reason}.
    """
    # Stable two-key sort: ticker ASC, then entry_date DESC (Python sort is stable),
    # giving rows ordered newest-date-first with ascending ticker within a date.
    compatible_rows, _ = _contract_rows(rows)
    sorted_rows = sorted(compatible_rows, key=lambda r: str(r.get("ticker") or ""))
    sorted_rows = sorted(
        sorted_rows, key=lambda r: str(r.get("entry_date") or ""), reverse=True
    )

    taken = sorted_rows[:limit]

    return [
        {
            "market_as_of_date": r.get("market_as_of_date"),
            "entry_date": r.get("entry_date"),
            "eval_date": r.get("eval_date"),
            "ticker": r.get("ticker"),
            "name": r.get("name"),
            "action": r.get("action"),
            "conviction": r.get("conviction"),
            "horizon_days": r.get("horizon_days"),
            "realized_ret": r.get("realized_ret"),
            "benchmark_ret": r.get("benchmark_ret"),
            "excess_ret": r.get("excess_ret"),
            "hit": r.get("hit"),
            "mae": r.get("mae"),
            "mfe": r.get("mfe"),
            "exit_reason": r.get("exit_reason"),
            "entry_price": r.get("entry_price"),
            "exit_price": r.get("exit_price"),
            "entry_price_basis": r.get("entry_price_basis"),
            "exit_price_basis": r.get("exit_price_basis"),
            "contract_version": r.get("contract_version"),
            "benchmark_basis": r.get("benchmark_basis"),
        }
        for r in taken
    ]


def recent_outcomes_execution_contract(
    recent_rows: list[dict],
    *,
    cost_bps: float | None = None,
    slippage_bps: float | None = None,
) -> dict:
    """Execution-contract metadata for the ``signal_outcomes_recent.json`` rows.

    ``recent_rows`` is the exact list being exported (the output of
    ``build_recent_outcomes``, already limited/sorted). Declares
    ``SAME_BASIS_BENCHMARK`` only when that list is non-empty and every row in
    it carries a non-null ``benchmark_ret``; an empty list means nothing was
    measured, not that coverage is complete, so it keeps the fail-closed
    ``BENCHMARK_BASIS`` from ``execution_contract_metadata()``.
    """
    contract = execution_contract_metadata(cost_bps=cost_bps, slippage_bps=slippage_bps)
    complete = bool(recent_rows) and all(
        row.get("benchmark_ret") is not None for row in recent_rows
    )
    if complete:
        contract["benchmark_basis"] = SAME_BASIS_BENCHMARK
    return contract


def build_performance_detail(
    rows: list[dict],
    pred_rows: list[dict],
    horizon: int,
    history_days: int,
    n_bins: int,
    *,
    cost_bps: float = DEFAULT_COST_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
) -> dict:
    """
    Assemble all performance detail components into one dict.

    Does NOT add available/generated_at — the dashboard export wraps that.

    Returns:
        {"horizon_days", "history_days", "equity_curve", "drawdown_curve",
         "rolling", "reliability"}
    """
    compatible_rows, contract_coverage = _contract_rows(rows)
    equity, accounting, benchmark_coverage = _equity_curve_with_metadata(
        compatible_rows,
        horizon=horizon,
        cost_bps=cost_bps,
        slippage_bps=slippage_bps,
    )
    drawdown = build_drawdown(equity)
    rolling = rolling_metrics(
        compatible_rows,
        horizon=horizon,
        cost_bps=accounting["cost_bps_per_side"],
        slippage_bps=accounting["slippage_bps_per_side"],
    )
    reliability = build_reliability(pred_rows, n_bins=n_bins)

    execution_contract = execution_contract_metadata(
        cost_bps=accounting["cost_bps_per_side"],
        slippage_bps=accounting["slippage_bps_per_side"],
    )
    execution_contract["return_basis"] = "net_after_entry_exit_costs"
    execution_contract["cost_treatment"] = "deducted_from_performance_equity"
    if benchmark_coverage.get("available"):
        execution_contract["benchmark_basis"] = SAME_BASIS_BENCHMARK

    return {
        "horizon_days": horizon,
        "history_days": history_days,
        "execution_contract": execution_contract,
        "contract_coverage": contract_coverage,
        "accounting_method": accounting,
        "benchmark_coverage": benchmark_coverage,
        "signal_quality": {
            "overlapping_samples_allowed": True,
            "compounded_into_equity": False,
            "return_basis": "raw_market_return_before_costs",
            "metrics": [
                "hit_rate_20d",
                "avg_return_20d",
                "excess_return_20d",
            ],
        },
        "equity_curve": equity,
        "drawdown_curve": drawdown,
        "rolling": rolling,
        "reliability": reliability,
    }
