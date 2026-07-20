"""
Phase 2 walk-forward, period-rebalanced, long-only portfolio backtest
(roadmap §6.2, Task 7A).

Pure logic — pandas / numpy + reuse of ``src/portfolio.py``; NO database or
network. The backtest is driven by the cross-sectional model OOS prediction
frame (``date, ticker, raw_score, fwd_return, target_up, target_vol_norm,
target_rank_bucket`` plus execution provenance). ``date`` is the pre-open
decision's market-as-of session. Under execution contract v2, ``fwd_return``
enters at the next session open and exits at the H-th session close.

At each (thinned) rebalance date the long-only book is rebuilt with the same
pipeline used in production (``select_candidates`` -> ``estimate_covariance``
-> ``initial_inverse_vol_weights`` -> ``enforce_caps`` -> ``scale_to_target_vol``
-> ``apply_hysteresis``) and the realized period return / turnover / cost are
recorded and compounded into an equity curve. TOPIX is compared only when its
open at the exact entry session and close at the exact exit session are both
available. The current close-only macro feed is therefore explicitly marked
unavailable; it is never substituted with cash/zero return or a close proxy.
Risk-adjusted metrics (Sharpe, Sortino, Calmar, IR, alpha/beta, tracking error,
turnover, hit rate, …) are then computed where their inputs exist.

Two correctness concerns dominate the design and are handled explicitly:

1. **No look-ahead in the covariance.** For each rebalance date ``d`` the price
   frames are sliced to rows with ``date <= d`` *before* estimating the
   covariance (``_slice_price_frames_asof``). A past rebalance therefore can
   never see future volatility / correlation. Selection likewise never peeks at
   ``fwd_return``: candidate ``expected_ret`` is set to ``None`` and the
   ``select_candidates`` floor is a no-op, so ordering is purely by the
   cross-sectional rank of ``raw_score`` at ``d``.

2. **No double-counting of overlapping holding windows.** The OOS frame is
   daily, so consecutive daily ``fwd_return`` windows overlap by ``H - 1`` days.
   We thin the rebalance dates so that at least ``rebalance_days`` *distinct OOS
   dates* have elapsed between consecutive picks (``_thin_rebalance_dates``).
   With ``rebalance_days == label_horizon_days`` (the default), a decision at
   row i enters at i+1 and exits at i+H; the next decision is at i+H and its
   entry at i+H+1. This makes the executable holding windows non-overlapping.

The JSON report writer (``write_portfolio_backtest_report``) needs no DB and
writes ``docs/portfolio_backtest.json`` atomically; an insufficient / missing
result still produces ``{"available": false, ...}``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.execution import (
    ENTRY_PRICE_BASIS,
    EXECUTION_CONTRACT_VERSION,
    EXIT_PRICE_BASIS,
    execution_contract_metadata,
)
from src.portfolio import (
    apply_hysteresis,
    enforce_caps,
    estimate_covariance,
    initial_inverse_vol_weights,
    scale_to_target_vol,
    select_candidates,
)

__all__ = [
    "run_portfolio_backtest",
    "write_portfolio_backtest_report",
]

# Numerical guards.
_EPS = 1e-12
# Capacity-proxy denominator floor (turnover can legitimately be ~0).
_CAP_EPS = 1e-6


# ---------------------------------------------------------------------------
# Small numeric / NaN-safe helpers
# ---------------------------------------------------------------------------


def _finite(value) -> float | None:
    """Return ``float(value)`` when finite, else ``None``."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _mean(arr) -> float | None:
    a = np.asarray(arr, dtype="float64")
    if a.size == 0:
        return None
    m = float(np.mean(a))
    return m if math.isfinite(m) else None


def _std(arr, ddof: int = 0) -> float | None:
    """Population (ddof=0 default) standard deviation; None when undefined."""
    a = np.asarray(arr, dtype="float64")
    if a.size == 0 or a.size <= ddof:
        return None
    s = float(np.std(a, ddof=ddof))
    return s if math.isfinite(s) else None


# ---------------------------------------------------------------------------
# Rebalance-date thinning (non-overlapping H-day windows)
# ---------------------------------------------------------------------------


def _thin_rebalance_dates(sorted_dates, rebalance_days: int):
    """Thin a sorted list of unique OOS dates to non-overlapping rebalances.

    Walks ``sorted_dates`` in order, always picking the first, then skipping
    until at least ``rebalance_days`` *distinct OOS dates* have elapsed since the
    last pick (measured by position in the sorted-unique list, which for a daily
    OOS frame ≈ ``rebalance_days`` trading days). This guarantees the realized
    executable H-session windows do not overlap when ``rebalance_days ==
    label_horizon_days``: decision i -> entry i+1 -> exit i+H, followed by
    decision i+H -> entry i+H+1.

    Returns the list of picked ``pd.Timestamp`` rebalance dates.
    """
    if rebalance_days is None or rebalance_days < 1:
        rebalance_days = 1
    rebalance_days = int(rebalance_days)

    picked: list = []
    last_idx: int | None = None
    for i, d in enumerate(sorted_dates):
        if last_idx is None or (i - last_idx) >= rebalance_days:
            picked.append(d)
            last_idx = i
    return picked


def _slice_price_frames_asof(price_frames, d) -> dict:
    """Slice each price frame to rows with ``date <= d`` (leakage-free cov).

    A frame without a ``date`` column is passed through unchanged (the caller's
    covariance estimator already tail-limits by ``lookback_days``; but the
    canonical OOS-driven path always supplies dated frames).
    """
    out: dict[str, pd.DataFrame] = {}
    for tk, frame in (price_frames or {}).items():
        if isinstance(frame, pd.DataFrame) and "date" in frame.columns:
            dates = pd.to_datetime(frame["date"], errors="coerce")
            out[tk] = frame[dates <= d]
        else:
            out[tk] = frame
    return out


# ---------------------------------------------------------------------------
# Benchmark (TOPIX) helpers
# ---------------------------------------------------------------------------


def _prepare_topix(macro_panel) -> pd.DataFrame | None:
    """Return exact-date TOPIX open/close data, or None when unavailable.

    The normal macro panel currently exposes only the ``topix`` close. That is
    intentionally insufficient for the v2 next-open-to-H-close contract: using
    it would create a basis mismatch with portfolio returns.
    """
    if macro_panel is None or not isinstance(macro_panel, pd.DataFrame):
        return None
    required = {"date", "topix_open", "topix"}
    if not required.issubset(macro_panel.columns):
        return None
    tp = macro_panel[["date", "topix_open", "topix"]].copy()
    tp["date"] = pd.to_datetime(tp["date"], errors="coerce")
    tp["topix_open"] = pd.to_numeric(tp["topix_open"], errors="coerce")
    tp["topix"] = pd.to_numeric(tp["topix"], errors="coerce")
    tp = tp.dropna(subset=["date", "topix_open", "topix"]).sort_values("date")
    tp = tp[(tp["topix_open"] > 0) & (tp["topix"] > 0)]
    tp = tp.drop_duplicates(subset="date", keep="last").reset_index(drop=True)
    return tp if not tp.empty else None


def _single_execution_date(cross: pd.DataFrame, column: str):
    """Return one unambiguous execution date shared by the cross section."""
    if column not in cross.columns:
        return None
    converted = pd.to_datetime(cross[column], errors="coerce")
    if converted.isna().any():
        return None
    values = converted.unique()
    if len(values) != 1:
        return None
    return pd.Timestamp(values[0])


def _benchmark_coverage(total: int, available: int, reason: str | None) -> dict:
    """Build the JSON-safe same-basis benchmark coverage contract."""
    ratio = float(available / total) if total > 0 else None
    complete = total > 0 and available == total
    return {
        "available": complete,
        "scope": "portfolio_rebalance_periods",
        "required_basis": f"{ENTRY_PRICE_BASIS}_to_{EXIT_PRICE_BASIS}",
        "return_basis": "net_after_same_entry_exit_costs",
        "required_open_column": "topix_open",
        "total_periods": int(total),
        "available_periods": int(available),
        "coverage_ratio": ratio,
        "reason": None if complete else (reason or "incomplete_same_basis_coverage"),
    }


def _cross_execution_window(cross: pd.DataFrame, decision_date):
    """Validate one cross section's shared v2 execution provenance.

    A period is usable only when every row names the current contract and all
    tickers agree on market-as-of, entry, and exit dates. Returning an explicit
    reason lets the caller exclude malformed periods without converting them
    into cash returns.
    """
    required = {
        "execution_contract_version",
        "market_as_of_date",
        "entry_date",
        "execution_exit_date",
    }
    missing = sorted(required.difference(cross.columns))
    if missing:
        return None, None, None, f"missing_execution_columns:{','.join(missing)}"
    if cross["ticker"].astype(str).duplicated().any():
        return None, None, None, "duplicate_ticker"

    versions = cross["execution_contract_version"]
    if versions.isna().any() or not bool(
        versions.astype(str).eq(EXECUTION_CONTRACT_VERSION).all()
    ):
        return None, None, None, "execution_contract_mismatch"

    market_as_of = _single_execution_date(cross, "market_as_of_date")
    entry_date = _single_execution_date(cross, "entry_date")
    exit_date = _single_execution_date(cross, "execution_exit_date")
    if market_as_of is None:
        return None, None, None, "market_as_of_date_inconsistent"
    if market_as_of != pd.Timestamp(decision_date):
        return None, None, None, "market_as_of_date_mismatch"
    if entry_date is None:
        return None, None, None, "entry_date_inconsistent"
    if exit_date is None:
        return None, None, None, "exit_date_inconsistent"
    if not market_as_of < entry_date <= exit_date:
        return None, None, None, "execution_date_order_invalid"
    return market_as_of, entry_date, exit_date, None


# ---------------------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------------------


def run_portfolio_backtest(
    oos_predictions,
    price_frames,
    macro_panel,
    config,
    *,
    sectors=None,
    rebalance_days=None,
    label_horizon_days=5,
    cost_bps=10.0,
    slippage_bps=5.0,
    trading_days=252,
) -> dict:
    """Walk-forward, period-rebalanced, long-only portfolio backtest.

    Parameters
    ----------
    oos_predictions : DataFrame with at least ``date, ticker, raw_score,
        fwd_return``. In the production contract it also carries
        ``entry_date`` and ``execution_exit_date``. ``date`` is the decision's
        market-as-of date; ``fwd_return`` is next-session-open to H-th-session-
        close. Selection NEVER reads ``fwd_return``.
    price_frames : dict ``ticker -> DataFrame[date, close]`` for the as-of
        covariance estimate (sliced to ``date <= d`` at each rebalance).
    macro_panel : A same-basis benchmark requires ``date``, ``topix_open`` and
        ``topix`` (close). A close-only or missing panel is explicitly marked
        unavailable; benchmark returns and IR/alpha/beta stay ``None``.
    config : dict with portfolio params (see ``src.config.get_portfolio_config``)
        plus ``top_n`` (else pulled from ``get_cross_section_config``).
    sectors : optional ``ticker -> sector`` map for the sector cap.
    rebalance_days : distinct-OOS-date spacing between rebalances; defaults to
        ``label_horizon_days`` to keep holding windows non-overlapping.
    cost_bps, slippage_bps : per-side, per-unit-turnover trading cost in basis
        points. Because v2 periods do not overlap, the previous book is fully
        exited and the new book fully entered; identical names are not netted.
    trading_days : annualization base; ``periods_per_year = trading_days /
        rebalance_days``.

    Returns the result dict documented in the task spec. Status ``"insufficient"``
    when fewer than 2 rebalance periods are available; ``"ok"`` otherwise.
    """
    cfg = dict(config or {})

    target_vol = float(cfg.get("target_vol", 0.12))
    max_name_weight = float(cfg.get("max_name_weight", 0.20))
    sector_cap = float(cfg.get("sector_cap", 0.40))
    max_gross = float(cfg.get("max_gross", 1.00))
    min_weight = float(cfg.get("min_weight", 0.03))
    notrade_band = float(cfg.get("notrade_band", 0.02))
    cov_lookback_days = int(cfg.get("cov_lookback_days", 60))

    top_n = cfg.get("top_n")
    if top_n is None:
        try:
            from src.config import get_cross_section_config

            top_n = get_cross_section_config().get("top_n", 8)
        except Exception:  # noqa: BLE001
            top_n = 8
    top_n = int(top_n)

    label_horizon_days = max(1, int(label_horizon_days))
    if rebalance_days is None:
        rebalance_days = label_horizon_days
    rebalance_days = max(1, int(rebalance_days))

    cost_bps = float(cost_bps)
    slippage_bps = float(slippage_bps)
    trading_days = float(trading_days)
    sectors = sectors or {}
    topix = _prepare_topix(macro_panel)
    execution_contract = execution_contract_metadata(
        cost_bps=cost_bps,
        slippage_bps=slippage_bps,
    )
    cost_rate = (cost_bps + slippage_bps) / 10000.0
    execution_contract.update(
        {
            "return_basis": "net_after_entry_exit_costs",
            "gross_return_source": "cross_section_oos.fwd_return",
            "cost_treatment": "deducted_from_portfolio_and_benchmark_returns",
            "cost_model": "full_exit_then_entry_between_non_overlapping_periods",
            "round_trip_cost_rate": 2.0 * cost_rate,
            "benchmark_return_basis": (
                "net_after_same_entry_exit_costs"
                if topix is not None
                else "unavailable_same_basis"
            ),
            "benchmark_gross_return_basis": "raw_market_price_before_costs",
            "benchmark_cost_model": (
                "full_capital_exit_then_entry_between_non_overlapping_periods"
            ),
        }
    )
    if topix is not None:
        execution_contract["benchmark_basis"] = (
            f"{ENTRY_PRICE_BASIS}_to_{EXIT_PRICE_BASIS}"
        )

    params = {
        "target_vol": target_vol,
        "max_name_weight": max_name_weight,
        "sector_cap": sector_cap,
        "max_gross": max_gross,
        "min_weight": min_weight,
        "notrade_band": notrade_band,
        "top_n": top_n,
        "cov_lookback_days": cov_lookback_days,
        "rebalance_days": rebalance_days,
        "label_horizon_days": label_horizon_days,
        "cost_bps": cost_bps,
        "slippage_bps": slippage_bps,
        "execution_contract_version": EXECUTION_CONTRACT_VERSION,
        "decision_date_basis": "market_as_of_date",
        "entry_price_basis": ENTRY_PRICE_BASIS,
        "exit_price_basis": EXIT_PRICE_BASIS,
    }

    # --- 1. Normalize the OOS frame. ---
    def _insufficient(
        *,
        reason="insufficient_periods",
        candidate_periods=0,
        valid_periods=0,
        exclusions=None,
    ):
        """Return a fail-closed, JSON-safe unavailable result.

        A single internally evaluated period is not an equity curve and may
        still contain pandas timestamps. Never expose those unfinished period
        dictionaries through the report contract.
        """
        excluded = list(exclusions or [])
        return {
            "status": "insufficient",
            "reason": reason,
            "n_periods": 0,
            "rebalance_days": rebalance_days,
            "cost_bps": cost_bps,
            "slippage_bps": slippage_bps,
            "metrics": {},
            "equity": [],
            "params": params,
            "execution_contract": execution_contract,
            "data_quality": {
                "candidate_periods": int(candidate_periods),
                "valid_periods": int(valid_periods),
                "excluded_periods": len(excluded),
                "exclusions": excluded,
            },
            "benchmark_coverage": _benchmark_coverage(
                0,
                0,
                "topix_open_unavailable_same_basis"
                if topix is None
                else "insufficient_periods",
            ),
        }

    if (
        oos_predictions is None
        or not isinstance(oos_predictions, pd.DataFrame)
        or oos_predictions.empty
    ):
        return _insufficient()

    oos = oos_predictions.copy()
    required = {
        "date",
        "ticker",
        "raw_score",
        "fwd_return",
        "execution_contract_version",
        "market_as_of_date",
        "entry_date",
        "execution_exit_date",
    }
    if not required.issubset(set(oos.columns)):
        missing = sorted(required.difference(oos.columns))
        return _insufficient(reason=f"missing_required_columns:{','.join(missing)}")
    oos["date"] = pd.to_datetime(oos["date"], errors="coerce")
    for col in ("market_as_of_date", "entry_date", "execution_exit_date"):
        if col in oos.columns:
            oos[col] = pd.to_datetime(oos[col], errors="coerce")
    oos["raw_score"] = pd.to_numeric(oos["raw_score"], errors="coerce")
    oos["fwd_return"] = pd.to_numeric(oos["fwd_return"], errors="coerce")
    oos = oos.dropna(subset=["date", "ticker", "raw_score"])
    if oos.empty:
        return _insufficient()

    # --- 2. Candidate rebalance dates -> thin to non-overlapping windows. ---
    all_dates = sorted(oos["date"].unique())
    rebalance_dates = _thin_rebalance_dates(all_dates, rebalance_days)
    if len(rebalance_dates) < 2:
        return _insufficient()

    # --- 3. Walk each rebalance date. ---
    periods: list[dict] = []
    topn_realized: list[float] = []
    prev_w: dict[str, float] = {}
    exclusions: list[dict] = []

    for d in rebalance_dates:
        cross = oos[oos["date"] == d]
        if cross.empty:
            continue

        _, entry_date, exit_date, invalid_reason = _cross_execution_window(cross, d)
        if invalid_reason is not None:
            exclusions.append({"date": _date_str(d), "reason": invalid_reason})
            continue
        if periods and entry_date <= periods[-1]["exit_date"]:
            exclusions.append(
                {"date": _date_str(d), "reason": "overlapping_execution_window"}
            )
            continue

        # cs_rank within d: rank raw_score descending (1 = best).
        cross = cross.sort_values("raw_score", ascending=False).reset_index(drop=True)
        # Realized fwd_return per ticker at d (selection must NOT use this).
        fwd_by_ticker = dict(zip(cross["ticker"], cross["fwd_return"]))

        # Build candidates with expected_ret=None so the floor is a no-op and
        # ordering is purely by cs_rank (NO peeking at fwd_return).
        cands = [
            {"ticker": str(row.ticker), "cs_rank": i + 1, "expected_ret": None}
            for i, row in enumerate(cross.itertuples(index=False))
        ]
        selected = select_candidates(cands, top_n=top_n, min_expected_ret=0.0)
        tickers = [c["ticker"] for c in selected]
        missing_selected_returns = [
            tk for tk in tickers if _finite(fwd_by_ticker.get(tk)) is None
        ]
        if missing_selected_returns:
            exclusions.append(
                {
                    "date": _date_str(d),
                    "reason": "selected_fwd_return_unavailable",
                    "tickers": missing_selected_returns,
                }
            )
            continue

        if not selected:
            # No book this period: realize a flat (cash) period, still pay any
            # turnover from fully exiting the previous non-overlapping book.
            exit_turnover = sum(abs(weight) for weight in prev_w.values())
            entry_turnover = 0.0
            turnover = exit_turnover
            cost = turnover * cost_rate
            period_ret = -cost  # gross 0
            gross_bench_ret = _benchmark_return(topix, entry_date, exit_date)
            benchmark_turnover = 1.0 + (1.0 if periods else 0.0)
            benchmark_cost = benchmark_turnover * cost_rate
            bench_ret = (
                gross_bench_ret - benchmark_cost
                if gross_bench_ret is not None
                else None
            )
            periods.append(
                {
                    "date": d,
                    "decision_date": d,
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "gross_period_return": 0.0,
                    "cost_return": cost,
                    "period_return": period_ret,
                    "gross_benchmark_return": gross_bench_ret,
                    "benchmark_cost_return": benchmark_cost,
                    "benchmark_return": bench_ret,
                    "gross_exposure": 0.0,
                    "entry_turnover": entry_turnover,
                    "exit_turnover": exit_turnover,
                    "terminal_exit_turnover": 0.0,
                    "turnover": turnover,
                    "benchmark_turnover": benchmark_turnover,
                }
            )
            prev_w = {}
            continue

        # 3b. As-of covariance (NO LEAKAGE): slice price frames to date <= d.
        pf_asof = _slice_price_frames_asof(price_frames, d)
        cov, vol, _method = estimate_covariance(
            pf_asof, tickers, lookback_days=cov_lookback_days
        )

        # 3c. inv-vol -> caps -> vol target (regime 1.0) -> hysteresis.
        init_w = initial_inverse_vol_weights(selected, vol)
        capped = enforce_caps(
            init_w,
            sectors,
            max_name_weight=max_name_weight,
            sector_cap=sector_cap,
        )
        scaled, _evol, _gross = scale_to_target_vol(
            capped,
            cov,
            tickers,
            target_vol=target_vol,
            max_gross=max_gross,
            regime_multiplier=1.0,
        )
        w_d = apply_hysteresis(
            scaled,
            prev_w,
            notrade_band=notrade_band,
            min_weight=min_weight,
        )
        missing_weighted_returns = [
            tk for tk in w_d if _finite(fwd_by_ticker.get(tk)) is None
        ]
        if missing_weighted_returns:
            exclusions.append(
                {
                    "date": _date_str(d),
                    "reason": "portfolio_fwd_return_unavailable",
                    "tickers": missing_weighted_returns,
                }
            )
            continue

        # 3d. The v2 holding windows do not overlap: the old book is fully
        # liquidated at its H-close and the new book is established at the next
        # period's open. Netting identical names would understate both sides.
        exit_turnover = sum(abs(weight) for weight in prev_w.values())
        entry_turnover = sum(abs(weight) for weight in w_d.values())
        turnover = exit_turnover + entry_turnover
        cost = turnover * cost_rate

        # 3e. Realized gross period return = sum(w_d * fwd_return_d).
        # Every selected return was validated above; none can silently become
        # a zero contribution.
        gross_return = sum(w * float(fwd_by_ticker[tk]) for tk, w in w_d.items())
        period_ret = gross_return - cost

        # Long-leg signal quality: equal-weight mean fwd_return of selected.
        sel_rets = [float(fwd_by_ticker[tk]) for tk in tickers]
        topn_realized.append(float(np.mean(sel_rets)))

        # 3f. Benchmark period return.
        gross_bench_ret = _benchmark_return(topix, entry_date, exit_date)
        benchmark_turnover = 1.0 + (1.0 if periods else 0.0)
        benchmark_cost = benchmark_turnover * cost_rate
        bench_ret = (
            gross_bench_ret - benchmark_cost if gross_bench_ret is not None else None
        )

        # 3g. Record + advance.
        periods.append(
            {
                "date": d,
                "decision_date": d,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "gross_period_return": float(gross_return),
                "cost_return": float(cost),
                "period_return": period_ret,
                "gross_benchmark_return": gross_bench_ret,
                "benchmark_cost_return": benchmark_cost,
                "benchmark_return": bench_ret,
                "gross_exposure": float(sum(w_d.values())),
                "entry_turnover": float(entry_turnover),
                "exit_turnover": float(exit_turnover),
                "terminal_exit_turnover": 0.0,
                "turnover": float(turnover),
                "benchmark_turnover": benchmark_turnover,
            }
        )
        prev_w = w_d

    # Charge the final close-out explicitly. Every non-overlapping strategy and
    # benchmark book then has one entry and one exit in the reported run.
    if periods:
        terminal_exit = sum(abs(weight) for weight in prev_w.values())
        terminal_cost = terminal_exit * cost_rate
        periods[-1]["terminal_exit_turnover"] = float(terminal_exit)
        periods[-1]["turnover"] += float(terminal_exit)
        periods[-1]["cost_return"] += float(terminal_cost)
        periods[-1]["period_return"] -= float(terminal_cost)

        periods[-1]["benchmark_turnover"] += 1.0
        periods[-1]["benchmark_cost_return"] += cost_rate
        if periods[-1]["benchmark_return"] is not None:
            periods[-1]["benchmark_return"] -= cost_rate

    if len(periods) < 2:
        return _insufficient(
            reason="insufficient_valid_periods",
            candidate_periods=len(rebalance_dates),
            valid_periods=len(periods),
            exclusions=exclusions,
        )

    # --- 4. Compound equity + benchmark equity + drawdown. ---
    net = np.array([p["period_return"] for p in periods], dtype="float64")
    n_periods = len(periods)

    # Comparison metrics are all-or-nothing. Partial exact-basis coverage would
    # silently compare different period sets, so it is marked unavailable too.
    available_benchmark_periods = sum(
        _finite(p.get("benchmark_return")) is not None for p in periods
    )
    if available_benchmark_periods == n_periods:
        bench = np.array([p["benchmark_return"] for p in periods], dtype="float64")
        benchmark_reason = None
    else:
        bench = None
        if topix is None:
            benchmark_reason = "topix_open_unavailable_same_basis"
        elif any(
            p.get("entry_date") is None or p.get("exit_date") is None for p in periods
        ):
            benchmark_reason = "execution_dates_unavailable"
        else:
            benchmark_reason = "incomplete_same_basis_coverage"
        # Do not publish a partial comparison as if it covered the whole run.
        for p in periods:
            p["benchmark_return"] = None

    benchmark_coverage = _benchmark_coverage(
        n_periods,
        available_benchmark_periods,
        benchmark_reason,
    )

    equity_vals = np.cumprod(1.0 + net)
    bench_equity_vals = np.cumprod(1.0 + bench) if bench is not None else None
    running_max = np.maximum.accumulate(equity_vals)
    drawdown_vals = np.where(running_max > 0, equity_vals / running_max - 1.0, 0.0)

    equity_rows = []
    for i, p in enumerate(periods):
        equity_rows.append(
            {
                "date": _date_str(p["date"]),
                "decision_date": _date_str(p["decision_date"]),
                "entry_date": _date_str_or_none(p["entry_date"]),
                "exit_date": _date_str_or_none(p["exit_date"]),
                "equity": float(equity_vals[i]),
                "benchmark_equity": (
                    float(bench_equity_vals[i])
                    if bench_equity_vals is not None
                    else None
                ),
                "gross_period_return": float(p["gross_period_return"]),
                "cost_return": float(p["cost_return"]),
                "period_return": float(net[i]),
                "gross_benchmark_return": (
                    float(p["gross_benchmark_return"])
                    if _finite(p.get("gross_benchmark_return")) is not None
                    else None
                ),
                "benchmark_cost_return": float(p["benchmark_cost_return"]),
                "benchmark_return": float(bench[i]) if bench is not None else None,
                "drawdown": float(drawdown_vals[i]),
                "gross_exposure": float(p["gross_exposure"]),
                "entry_turnover": float(p["entry_turnover"]),
                "exit_turnover": float(p["exit_turnover"]),
                "terminal_exit_turnover": float(p["terminal_exit_turnover"]),
                "turnover": float(p["turnover"]),
                "benchmark_turnover": float(p["benchmark_turnover"]),
            }
        )

    # --- 6. Metrics (NaN-safe; un-computable -> None). ---
    periods_per_year = trading_days / rebalance_days
    sqrt_ppy = math.sqrt(periods_per_year)

    metrics = _compute_metrics(
        net,
        bench,
        drawdown_vals,
        periods,
        topn_realized,
        equity_final=float(equity_vals[-1]),
        n_periods=n_periods,
        periods_per_year=periods_per_year,
        sqrt_ppy=sqrt_ppy,
        rebalance_days=rebalance_days,
    )

    first_d = periods[0]["date"]
    last_d = periods[-1]["date"]

    return {
        "status": "ok",
        "start_date": _date_str(first_d),
        "end_date": _date_str(last_d),
        "n_periods": n_periods,
        "rebalance_days": rebalance_days,
        "cost_bps": cost_bps,
        "slippage_bps": slippage_bps,
        "metrics": metrics,
        "equity": equity_rows,
        "params": params,
        "execution_contract": execution_contract,
        "benchmark_coverage": benchmark_coverage,
        "data_quality": {
            "candidate_periods": len(rebalance_dates),
            "valid_periods": n_periods,
            "excluded_periods": len(exclusions),
            "exclusions": exclusions,
        },
    }


def _benchmark_return(topix, entry_date, exit_date) -> float | None:
    """Return exact TOPIX entry-open to exit-close performance, if present."""
    if topix is None or entry_date is None or exit_date is None:
        return None
    entry_rows = topix[topix["date"] == pd.Timestamp(entry_date)]
    exit_rows = topix[topix["date"] == pd.Timestamp(exit_date)]
    if entry_rows.empty or exit_rows.empty:
        return None
    entry_open = _finite(entry_rows["topix_open"].iloc[-1])
    exit_close = _finite(exit_rows["topix"].iloc[-1])
    if entry_open is None or entry_open <= 0.0 or exit_close is None:
        return None
    return exit_close / entry_open - 1.0


def _compute_metrics(
    net,
    bench,
    drawdown_vals,
    periods,
    topn_realized,
    *,
    equity_final,
    n_periods,
    periods_per_year,
    sqrt_ppy,
    rebalance_days,
) -> dict:
    """Compute the risk-adjusted metric block (all NaN-safe; None when undefined).

    Standard definitions, annualized with ``periods_per_year = trading_days /
    rebalance_days``:
      * cagr     = equity_final ** (periods_per_year / n_periods) - 1
                   (-1.0 when equity_final <= 0).
      * sharpe   = sqrt(ppy) * mean(net) / std(net, ddof=0).
      * sortino  = sqrt(ppy) * mean(net) / downside_std, downside_std =
                   std(min(net, 0), ddof=0).
      * max_drawdown = min(drawdown) (<= 0).
      * calmar   = cagr / |max_drawdown| (None when dd == 0).
      * turnover = mean per-rebalance turnover; turnover_annualized = * ppy.
      * avg_gross = mean gross exposure.
      * capacity_proxy = avg_gross / max(turnover, 1e-6) — a ROUGH churn proxy
        (higher = less churn / more capacity), NOT a notional capacity estimate.
      * alpha/beta from OLS of net strategy return on net benchmark return
        after the same entry/exit cost model, when the benchmark has complete
        same-basis coverage. Otherwise both are None.
      * information_ratio = sqrt(ppy) * mean(active) / std(active, ddof=0),
        active = net - bench.
      * tracking_error = std(active, ddof=0) * sqrt(ppy).
      * hit_rate = fraction of periods with net > 0.
      * topn_realized_return = mean over rebalance dates of the equal-weight
        mean fwd_return of the selected top-N (raw long-leg signal quality).
    """
    mean_net = _mean(net)
    std_net = _std(net, ddof=0)

    # CAGR.
    if equity_final <= 0.0:
        cagr = -1.0
    else:
        cagr = float(equity_final ** (periods_per_year / n_periods) - 1.0)
        if not math.isfinite(cagr):
            cagr = None

    # Sharpe.
    if mean_net is None or std_net is None:
        sharpe = None
    elif std_net == 0.0:
        sharpe = 0.0
    else:
        sharpe = float(sqrt_ppy * mean_net / std_net)

    # Sortino (downside deviation of negative net returns).
    downside = np.minimum(net, 0.0)
    downside_std = _std(downside, ddof=0)
    if mean_net is None or downside_std is None:
        sortino = None
    elif downside_std == 0.0:
        sortino = None  # no downside observed -> undefined ratio
    else:
        sortino = float(sqrt_ppy * mean_net / downside_std)

    # Max drawdown / Calmar.
    max_dd = float(np.min(drawdown_vals)) if drawdown_vals.size else 0.0
    if max_dd >= 0.0 or cagr is None:
        calmar = None
    else:
        calmar = float(cagr / abs(max_dd))

    # Turnover / gross / capacity proxy.
    turnover = _mean([p["turnover"] for p in periods])
    avg_gross = _mean([p["gross_exposure"] for p in periods])
    turnover_annualized = (
        float(turnover * periods_per_year) if turnover is not None else None
    )
    if avg_gross is None:
        capacity_proxy = None
    else:
        capacity_proxy = float(avg_gross / max(turnover or 0.0, _CAP_EPS))

    # Alpha / beta (OLS of net on benchmark). A missing benchmark is not cash:
    # it must remain None so the portfolio KPI gate fails closed on missing IR.
    mean_bench = _mean(bench) if bench is not None else None
    var_bench = (
        float(np.var(bench, ddof=0)) if bench is not None and bench.size else None
    )
    if mean_net is None or mean_bench is None or var_bench is None:
        beta = None
        alpha = None
    elif var_bench <= 0.0:
        beta = 0.0
        alpha = float((mean_net - 0.0) * periods_per_year)
    else:
        cov_nb = float(np.mean((net - mean_net) * (bench - mean_bench)))
        beta = float(cov_nb / var_bench)
        alpha_per_period = mean_net - beta * mean_bench
        alpha = float(alpha_per_period * periods_per_year)

    # Active-return metrics (IR, tracking error).
    active = net - bench if bench is not None else None
    mean_active = _mean(active) if active is not None else None
    std_active = _std(active, ddof=0) if active is not None else None
    if mean_active is None or std_active is None:
        information_ratio = None
    elif std_active == 0.0:
        information_ratio = None
    else:
        information_ratio = float(sqrt_ppy * mean_active / std_active)
    tracking_error = float(std_active * sqrt_ppy) if std_active is not None else None

    # Hit rate.
    hit_rate = float(np.mean(net > 0.0)) if net.size else None

    # Long-leg signal quality.
    topn_realized_return = _mean(topn_realized) if topn_realized else None

    return {
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "turnover": float(turnover) if turnover is not None else None,
        "turnover_annualized": turnover_annualized,
        "avg_gross": float(avg_gross) if avg_gross is not None else None,
        "capacity_proxy": capacity_proxy,
        "alpha": alpha,
        "beta": beta,
        "information_ratio": information_ratio,
        "tracking_error": tracking_error,
        "hit_rate": hit_rate,
        "topn_realized_return": topn_realized_return,
        "n_periods": int(n_periods),
    }


def _date_str(value) -> str:
    """Format a date-like value as ``YYYY-MM-DD``."""
    ts = pd.Timestamp(value)
    return ts.strftime("%Y-%m-%d")


def _date_str_or_none(value) -> str | None:
    """Format a date-like value, preserving unavailable execution dates."""
    if value is None or pd.isna(value):
        return None
    return _date_str(value)


# ---------------------------------------------------------------------------
# JSON report writer (no DB)
# ---------------------------------------------------------------------------


def write_portfolio_backtest_report(
    result,
    output_path="docs/portfolio_backtest.json",
    *,
    model_version=None,
    run_date=None,
    generated_at=None,
    gate=None,
) -> str:
    """Write the portfolio backtest report JSON (no DB needed).

    Produces ``{available: result['status']=='ok', generated_at, run_date,
    model_version, **result}``. When ``result`` is ``None`` or its status is not
    ``"ok"`` (e.g. ``"insufficient"``) the file is still written with
    ``{available: false, reason: ...}`` plus whatever fields the result carries.

    ``gate`` is the (already evaluated) ``evaluate_portfolio_kpi_gate`` result;
    when supplied it is embedded as ``{"gate": {"passed", "failures"}}`` so
    ``portfolio.read_portfolio_gate`` checks the actual KPI verdict instead of
    falling back to mere availability (active-mode safety, issue #2).

    ``generated_at`` is only stamped when a (string) value is supplied — the
    caller passes a timestamp; we never call ``datetime.now`` so tests stay
    deterministic. Written atomically (temp file + ``os.replace``). Returns the
    output path as a string.
    """
    path = Path(output_path)

    if result is None:
        payload: dict[str, Any] = {"available": False, "reason": "no_result"}
    elif result.get("status") == "ok":
        payload = {"available": True}
        payload.update(result)
    else:
        payload = {
            "available": False,
            "reason": result.get("status", "unavailable"),
        }
        payload.update(result)

    if isinstance(gate, dict):
        payload["gate"] = {
            "passed": bool(gate.get("passed")),
            "failures": list(gate.get("failures") or []),
        }
    if generated_at:
        payload["generated_at"] = generated_at
    if run_date is not None:
        payload["run_date"] = run_date
    if model_version is not None:
        payload["model_version"] = model_version

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return str(path)
