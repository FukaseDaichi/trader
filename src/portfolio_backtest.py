"""
Phase 2 walk-forward, period-rebalanced, long-only portfolio backtest
(roadmap §6.2, Task 7A).

Pure logic — pandas / numpy + reuse of ``src/portfolio.py``; NO database or
network. The backtest is driven by the cross-sectional model OOS prediction
frame (``date, ticker, raw_score, fwd_return, target_up, target_vol_norm,
target_rank_bucket``). ``fwd_return`` is the realized H-day forward return for
that ``(date, ticker)`` — i.e. exactly the holding-period return earned by a
position opened on ``date``.

At each (thinned) rebalance date the long-only book is rebuilt with the same
pipeline used in production (``select_candidates`` -> ``estimate_covariance``
-> ``initial_inverse_vol_weights`` -> ``enforce_caps`` -> ``scale_to_target_vol``
-> ``apply_hysteresis``) and the realized period return / turnover / cost are
recorded, compounded into an equity curve, and compared against a TOPIX
benchmark from the macro panel. Risk-adjusted metrics (Sharpe, Sortino, Calmar,
IR, alpha/beta, tracking error, turnover, hit rate, …) are then computed.

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
   With ``rebalance_days == label_horizon_days`` (the default) this makes the
   holding windows non-overlapping, ≈ rebalancing every H trading days.

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
    H-day ``fwd_return`` windows of consecutive picks do not overlap when
    ``rebalance_days == label_horizon_days``.

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
    """Return a sorted, deduped ``date``/``topix`` frame, or None if absent."""
    if macro_panel is None or not isinstance(macro_panel, pd.DataFrame):
        return None
    if "date" not in macro_panel.columns or "topix" not in macro_panel.columns:
        return None
    tp = macro_panel[["date", "topix"]].copy()
    tp["date"] = pd.to_datetime(tp["date"], errors="coerce")
    tp["topix"] = pd.to_numeric(tp["topix"], errors="coerce")
    tp = tp.dropna(subset=["date", "topix"]).sort_values("date")
    tp = tp.drop_duplicates(subset="date", keep="last").reset_index(drop=True)
    return tp if not tp.empty else None


def _topix_asof(topix, d):
    """Most-recent topix level with date <= d (backward as-of). None if none."""
    if topix is None:
        return None
    mask = topix["date"] <= d
    if not bool(mask.any()):
        return None
    return float(topix.loc[mask, "topix"].iloc[-1])


def _topix_after(topix, d):
    """Topix level at the FIRST date strictly after d (for the last period)."""
    if topix is None:
        return None
    mask = topix["date"] > d
    if not bool(mask.any()):
        return None
    return float(topix.loc[mask, "topix"].iloc[0])


# ---------------------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------------------

def run_portfolio_backtest(oos_predictions, price_frames, macro_panel, config, *,
                           sectors=None, rebalance_days=None, label_horizon_days=5,
                           cost_bps=10.0, slippage_bps=5.0, trading_days=252) -> dict:
    """Walk-forward, period-rebalanced, long-only portfolio backtest.

    Parameters
    ----------
    oos_predictions : DataFrame with at least ``date, ticker, raw_score,
        fwd_return``. ``fwd_return`` is the realized ``label_horizon_days``
        forward return for a position opened on ``date`` (the holding-period
        return). Selection NEVER reads ``fwd_return``.
    price_frames : dict ``ticker -> DataFrame[date, close]`` for the as-of
        covariance estimate (sliced to ``date <= d`` at each rebalance).
    macro_panel : DataFrame with ``date`` + ``topix`` for the benchmark, or
        ``None`` (then benchmark returns are 0.0 and IR/alpha/beta degrade
        gracefully).
    config : dict with portfolio params (see ``src.config.get_portfolio_config``)
        plus ``top_n`` (else pulled from ``get_cross_section_config``).
    sectors : optional ``ticker -> sector`` map for the sector cap.
    rebalance_days : distinct-OOS-date spacing between rebalances; defaults to
        ``label_horizon_days`` to keep holding windows non-overlapping.
    cost_bps, slippage_bps : per-unit-turnover trading cost in basis points.
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
        "cost_bps": cost_bps,
        "slippage_bps": slippage_bps,
    }

    # --- 1. Normalize the OOS frame. ---
    def _insufficient(equity=None):
        return {
            "status": "insufficient",
            "n_periods": 0 if not equity else len(equity),
            "rebalance_days": rebalance_days,
            "cost_bps": cost_bps,
            "slippage_bps": slippage_bps,
            "metrics": {},
            "equity": equity or [],
            "params": params,
        }

    if oos_predictions is None or not isinstance(oos_predictions, pd.DataFrame) \
            or oos_predictions.empty:
        return _insufficient()

    oos = oos_predictions.copy()
    required = {"date", "ticker", "raw_score"}
    if not required.issubset(set(oos.columns)):
        return _insufficient()
    oos["date"] = pd.to_datetime(oos["date"], errors="coerce")
    oos["raw_score"] = pd.to_numeric(oos["raw_score"], errors="coerce")
    if "fwd_return" in oos.columns:
        oos["fwd_return"] = pd.to_numeric(oos["fwd_return"], errors="coerce")
    else:
        oos["fwd_return"] = np.nan
    oos = oos.dropna(subset=["date", "ticker", "raw_score"])
    if oos.empty:
        return _insufficient()

    # --- 2. Candidate rebalance dates -> thin to non-overlapping windows. ---
    all_dates = sorted(oos["date"].unique())
    rebalance_dates = _thin_rebalance_dates(all_dates, rebalance_days)
    if len(rebalance_dates) < 2:
        return _insufficient()

    # --- 3. Walk each rebalance date. ---
    cost_rate = (cost_bps + slippage_bps) / 10000.0
    topix = _prepare_topix(macro_panel)

    periods: list[dict] = []
    topn_realized: list[float] = []
    prev_w: dict[str, float] = {}

    for k, d in enumerate(rebalance_dates):
        cross = oos[oos["date"] == d]
        if cross.empty:
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
        if not selected:
            # No book this period: realize a flat (cash) period, still pay any
            # turnover from exiting the previous book.
            turnover = sum(abs(0.0 - prev_w.get(tk, 0.0)) for tk in prev_w)
            cost = turnover * cost_rate
            period_ret = -cost  # gross 0
            bench_ret = _benchmark_return(topix, d, rebalance_dates, k)
            periods.append({
                "date": d, "period_return": period_ret, "benchmark_return": bench_ret,
                "gross_exposure": 0.0, "turnover": turnover,
            })
            prev_w = {}
            continue

        tickers = [c["ticker"] for c in selected]

        # 3b. As-of covariance (NO LEAKAGE): slice price frames to date <= d.
        pf_asof = _slice_price_frames_asof(price_frames, d)
        cov, vol, _method = estimate_covariance(
            pf_asof, tickers, lookback_days=cov_lookback_days
        )

        # 3c. inv-vol -> caps -> vol target (regime 1.0) -> hysteresis.
        init_w = initial_inverse_vol_weights(selected, vol)
        capped = enforce_caps(
            init_w, sectors,
            max_name_weight=max_name_weight, sector_cap=sector_cap,
        )
        scaled, _evol, _gross = scale_to_target_vol(
            capped, cov, tickers,
            target_vol=target_vol, max_gross=max_gross, regime_multiplier=1.0,
        )
        w_d = apply_hysteresis(
            scaled, prev_w, notrade_band=notrade_band, min_weight=min_weight,
        )

        # 3d. Turnover + cost over the union of tickers.
        union = set(w_d) | set(prev_w)
        turnover = sum(abs(w_d.get(tk, 0.0) - prev_w.get(tk, 0.0)) for tk in union)
        cost = turnover * cost_rate

        # 3e. Realized gross period return = sum(w_d * fwd_return_d).
        gross_return = 0.0
        for tk, w in w_d.items():
            r = _finite(fwd_by_ticker.get(tk))
            if r is not None:
                gross_return += w * r
        period_ret = gross_return - cost

        # Long-leg signal quality: equal-weight mean fwd_return of selected.
        sel_rets = [_finite(fwd_by_ticker.get(tk)) for tk in tickers]
        sel_rets = [r for r in sel_rets if r is not None]
        if sel_rets:
            topn_realized.append(float(np.mean(sel_rets)))

        # 3f. Benchmark period return.
        bench_ret = _benchmark_return(topix, d, rebalance_dates, k)

        # 3g. Record + advance.
        periods.append({
            "date": d,
            "period_return": period_ret,
            "benchmark_return": bench_ret,
            "gross_exposure": float(sum(w_d.values())),
            "turnover": float(turnover),
        })
        prev_w = w_d

    if len(periods) < 2:
        return _insufficient()

    # --- 4. Compound equity + benchmark equity + drawdown. ---
    net = np.array([p["period_return"] for p in periods], dtype="float64")
    bench = np.array([p["benchmark_return"] for p in periods], dtype="float64")
    n_periods = len(periods)

    equity_vals = np.cumprod(1.0 + net)
    bench_equity_vals = np.cumprod(1.0 + bench)
    running_max = np.maximum.accumulate(equity_vals)
    drawdown_vals = np.where(running_max > 0, equity_vals / running_max - 1.0, 0.0)

    equity_rows = []
    for i, p in enumerate(periods):
        equity_rows.append({
            "date": _date_str(p["date"]),
            "equity": float(equity_vals[i]),
            "benchmark_equity": float(bench_equity_vals[i]),
            "period_return": float(net[i]),
            "benchmark_return": float(bench[i]),
            "drawdown": float(drawdown_vals[i]),
            "gross_exposure": float(p["gross_exposure"]),
            "turnover": float(p["turnover"]),
        })

    # --- 6. Metrics (NaN-safe; un-computable -> None). ---
    periods_per_year = trading_days / rebalance_days
    sqrt_ppy = math.sqrt(periods_per_year)

    metrics = _compute_metrics(
        net, bench, drawdown_vals, periods, topn_realized,
        equity_final=float(equity_vals[-1]), n_periods=n_periods,
        periods_per_year=periods_per_year, sqrt_ppy=sqrt_ppy,
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
    }


def _benchmark_return(topix, d, rebalance_dates, k) -> float:
    """TOPIX period return from ``d`` to the next rebalance date.

    Uses backward as-of levels: ``topix(d)`` and ``topix(d_next)``. For the LAST
    rebalance (no ``d_next``) we use the first topix level strictly AFTER ``d``
    (so the final holding period still gets a benchmark when panel data extends
    past it); if neither is available the period return is ``0.0``.
    """
    if topix is None:
        return 0.0
    level_d = _topix_asof(topix, d)
    if level_d is None or level_d <= 0.0:
        return 0.0
    if k + 1 < len(rebalance_dates):
        level_next = _topix_asof(topix, rebalance_dates[k + 1])
    else:
        # Last period: use the first available topix strictly after d.
        level_next = _topix_after(topix, d)
    if level_next is None or not math.isfinite(level_next):
        return 0.0
    return level_next / level_d - 1.0


def _compute_metrics(net, bench, drawdown_vals, periods, topn_realized, *,
                     equity_final, n_periods, periods_per_year, sqrt_ppy,
                     rebalance_days) -> dict:
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
      * alpha/beta from OLS of net on benchmark: beta = cov/var (0 if var==0);
        alpha annualized = (mean(net) - beta*mean(bench)) * ppy.
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

    # Alpha / beta (OLS of net on benchmark).
    mean_bench = _mean(bench)
    var_bench = float(np.var(bench, ddof=0)) if bench.size else 0.0
    if mean_net is None or mean_bench is None:
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
    active = net - bench
    mean_active = _mean(active)
    std_active = _std(active, ddof=0)
    if mean_active is None or std_active is None:
        information_ratio = None
    elif std_active == 0.0:
        information_ratio = None
    else:
        information_ratio = float(sqrt_ppy * mean_active / std_active)
    tracking_error = (
        float(std_active * sqrt_ppy) if std_active is not None else None
    )

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


# ---------------------------------------------------------------------------
# JSON report writer (no DB)
# ---------------------------------------------------------------------------

def write_portfolio_backtest_report(result, output_path="docs/portfolio_backtest.json", *,
                                    model_version=None, run_date=None,
                                    generated_at=None, gate=None) -> str:
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
