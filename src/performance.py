"""
Pure performance-detail analytics for the Phase 3 dashboard.

No DB or network dependency. Imports only numpy and src.calibration/db_records.

All functions operate on "detail rows" — dicts with keys:
    entry_date (str "YYYY-MM-DD"), ticker, name, action, conviction (float|None),
    horizon_days (int), realized_ret (float|None), benchmark_ret (float|None),
    excess_ret (float|None), hit (bool|None), mae, mfe, exit_reason.

Population std (np.std default, ddof=0) is used throughout for Sharpe calculation
— consistent with the existing calibration module's convention.
"""

from __future__ import annotations

import numpy as np

from .db_records import LONG_ACTIONS
from . import calibration


def build_equity_curves(rows: list[dict], horizon: int = 1) -> list[dict]:
    """
    Compound equity curves for strategy and benchmark over LONG rows at `horizon`.

    Filters: action in LONG_ACTIONS, horizon_days == horizon, realized_ret not None,
    entry_date not None/empty.

    Groups by entry_date (ascending). For each date:
    - strat_ret = mean of realized_ret
    - bench_ret = mean of benchmark_ret (ignoring None), or None if all None
    Strategy compounds always; benchmark carries on None bench_ret days.

    Returns list of {"date", "strategy", "benchmark", "n"}.
    Empty input -> [].
    """
    filtered = [
        r
        for r in rows
        if r.get("action") in LONG_ACTIONS
        and int(r.get("horizon_days") or 0) == int(horizon)
        and r.get("realized_ret") is not None
        and r.get("entry_date")
    ]
    if not filtered:
        return []

    # Group by date (ascending)
    by_date: dict[str, list[dict]] = {}
    for r in filtered:
        d = r["entry_date"]
        by_date.setdefault(d, []).append(r)

    result = []
    strategy = 1.0
    benchmark = 1.0

    for date in sorted(by_date.keys()):
        day_rows = by_date[date]
        strat_ret = float(np.mean([r["realized_ret"] for r in day_rows]))
        bench_rets = [
            r["benchmark_ret"] for r in day_rows if r.get("benchmark_ret") is not None
        ]
        bench_ret = float(np.mean(bench_rets)) if bench_rets else None

        strategy *= 1.0 + strat_ret
        if bench_ret is not None:
            benchmark *= 1.0 + bench_ret
        # else: benchmark carries (unchanged)

        result.append(
            {
                "date": date,
                "strategy": strategy,
                "benchmark": benchmark,
                "n": len(day_rows),
            }
        )

    return result


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


def rolling_metrics(rows: list[dict], window: int = 20) -> dict:
    """
    Rolling performance metrics over LONG rows.

    - hit_rate_20d:    mean hit (1/0) over rows in last `window` distinct entry_dates.
    - avg_return_20d:  mean realized_ret over rows in last `window` distinct entry_dates.
    - excess_return_20d: mean excess_ret (non-None) over rows in last `window` dates.
    - sharpe_60d:      per-date mean strategy return over last 60 distinct dates;
                       mean/std * sqrt(252). Uses population std (np.std, ddof=0).
                       None if <2 dates or std==0.

    All keys always present; values may be None.
    """
    long_rows = [
        r
        for r in rows
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

    # sharpe_60d: per-date mean strategy return over last 60 distinct dates
    dates_60 = set(distinct_dates[-60:]) if distinct_dates else set()
    rows_60 = [r for r in long_rows_sorted if r["entry_date"] in dates_60]
    sharpe_60d = None
    if rows_60:
        # group by date, compute mean return per date
        by_date_60: dict[str, list[float]] = {}
        for r in rows_60:
            by_date_60.setdefault(r["entry_date"], []).append(r["realized_ret"])
        date_rets = [float(np.mean(v)) for _, v in sorted(by_date_60.items())]
        if len(date_rets) >= 2:
            std = float(np.std(date_rets))
            if std != 0.0:
                sharpe_60d = float(np.mean(date_rets)) / std * (252**0.5)

    return {
        "hit_rate_20d": hit_rate_20d,
        "avg_return_20d": avg_return_20d,
        "excess_return_20d": excess_return_20d,
        "sharpe_60d": sharpe_60d,
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
    sorted_rows = sorted(rows, key=lambda r: str(r.get("ticker") or ""))
    sorted_rows = sorted(
        sorted_rows, key=lambda r: str(r.get("entry_date") or ""), reverse=True
    )

    taken = sorted_rows[:limit]

    return [
        {
            "entry_date": r.get("entry_date"),
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
        }
        for r in taken
    ]


def build_performance_detail(
    rows: list[dict],
    pred_rows: list[dict],
    horizon: int,
    history_days: int,
    n_bins: int,
) -> dict:
    """
    Assemble all performance detail components into one dict.

    Does NOT add available/generated_at — the dashboard export wraps that.

    Returns:
        {"horizon_days", "history_days", "equity_curve", "drawdown_curve",
         "rolling", "reliability"}
    """
    equity = build_equity_curves(rows, horizon=horizon)
    drawdown = build_drawdown(equity)
    rolling = rolling_metrics(rows)
    reliability = build_reliability(pred_rows, n_bins=n_bins)

    return {
        "horizon_days": horizon,
        "history_days": history_days,
        "equity_curve": equity,
        "drawdown_curve": drawdown,
        "rolling": rolling,
        "reliability": reliability,
    }
