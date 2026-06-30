"""
Phase 2 shadow-validation comparison logic (roadmap Task 10).

Compares the Phase 1 per-ticker model against the Phase 2 cross-sectional (CS)
model + portfolio over a shadow window, on the metrics that decide whether to
flip Phase 2 from shadow to active: daily IC, top-N realized return, hit rate,
turnover, drawdown, and expected-return calibration.

This module is PURE: pandas / numpy only — NO database, network, or file I/O.
The CLI (``scripts/portfolio_shadow_report.py``) owns all DB/IO and feeds this
module a list of normalized per-(date, ticker) records:

    {
      "date": "YYYY-MM-DD", "ticker": "7011.JP",
      "realized_ret": float | None,   # market H-day fwd return for (date,ticker)
      "p1_prob_up": float | None,     # Phase 1 per-ticker prediction
      "p1_action": str | None,        # BUY / MILD_BUY / HOLD / ...
      "p2_cs_rank": int | None,       # Phase 2 cross-sectional rank, 1 = best
      "p2_expected_ret": float | None,
      "p2_prob_up": float | None,
      "p2_weight": float | None,      # portfolio target_weight that day (or None)
      "p2_prev_weight": float | None,
    }

The market ``realized_ret`` is a property of (date, ticker, horizon) — it
applies to ANY model's prediction of that ticker on that date — so it is shared
by the Phase 1 and Phase 2 evaluations of the same name.

IC / top-N / hit-rate conventions deliberately MIRROR ``src.cs_model.cs_metrics``
(``_spearman`` = Pearson-of-ranks, NaN-safe ``None`` returns, per-date mean over
dates) so the two modules report comparable numbers. ``cs_rank`` is ASCENDING
(1 = best), so for Phase 2 we correlate ``-cs_rank`` (higher = better) to match
the "higher score = better" convention used for Phase 1 ``prob_up``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Minimum distinct shadow dates required before a comparison is meaningful.
MIN_SHADOW_DATES = 5


# ---------------------------------------------------------------------------
# NaN-safe correlation / aggregation helpers (mirror src.cs_model)
# ---------------------------------------------------------------------------


def _pearson(a: np.ndarray, b: np.ndarray):
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 2:
        return None
    a, b = a[mask], b[mask]
    if np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _spearman(a: np.ndarray, b: np.ndarray):
    """Spearman corr = Pearson corr of the ranks (scipy-free)."""
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 2:
        return None
    ra = pd.Series(a[mask]).rank().to_numpy()
    rb = pd.Series(b[mask]).rank().to_numpy()
    if np.std(ra) == 0 or np.std(rb) == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def _mean_or_none(values):
    vals = [v for v in values if v is not None and np.isfinite(v)]
    if not vals:
        return None
    return float(np.mean(vals))


def _records_to_frame(records) -> pd.DataFrame:
    """Normalize the list-of-dicts into a typed DataFrame (empty-safe)."""
    cols = [
        "date",
        "ticker",
        "realized_ret",
        "p1_prob_up",
        "p1_action",
        "p2_cs_rank",
        "p2_expected_ret",
        "p2_prob_up",
        "p2_weight",
        "p2_prev_weight",
    ]
    if not records:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(list(records))
    for c in cols:
        if c not in df.columns:
            df[c] = None
    # Numeric coercion (everything except date / ticker / p1_action).
    for c in [
        "realized_ret",
        "p1_prob_up",
        "p2_cs_rank",
        "p2_expected_ret",
        "p2_prob_up",
        "p2_weight",
        "p2_prev_weight",
    ]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = df["date"].astype(str)
    return df[cols]


# ---------------------------------------------------------------------------
# Daily IC
# ---------------------------------------------------------------------------


def daily_ic(records, *, score_key, ret_key="realized_ret", method="spearman"):
    """Mean per-date rank/IC correlation between ``score_key`` and ``ret_key``.

    Higher ``score_key`` must mean "more bullish". For Phase 2 pass a score where
    higher = better (e.g. derive ``-cs_rank`` upstream, or pass
    ``p2_expected_ret``). Dates with < 2 finite pairs are skipped; ``None`` when
    no date yields a defined correlation (mirrors ``cs_metrics``).
    """
    df = _records_to_frame(records)
    if df.empty or score_key not in df.columns:
        return None
    corr = _spearman if method == "spearman" else _pearson
    per_date = []
    for _date, grp in df.groupby("date", sort=True):
        score = pd.to_numeric(grp[score_key], errors="coerce").to_numpy(dtype="float64")
        ret = pd.to_numeric(grp[ret_key], errors="coerce").to_numpy(dtype="float64")
        per_date.append(corr(score, ret))
    return _mean_or_none(per_date)


# ---------------------------------------------------------------------------
# Top-N selection helpers
# ---------------------------------------------------------------------------


def _select_topn_per_date(
    grp: pd.DataFrame, rank_key: str, top_n: int, ascending: bool
) -> pd.DataFrame:
    """Return the top-N rows of one date by ``rank_key`` (NaN ranks excluded).

    ``ascending=True`` -> smallest values win (e.g. cs_rank where 1 = best).
    ``ascending=False`` -> largest values win (e.g. prob_up). Stable order.
    """
    sub = grp.dropna(subset=[rank_key])
    if sub.empty:
        return sub
    sub = sub.sort_values(rank_key, ascending=ascending, kind="mergesort")
    return sub.head(max(1, int(top_n)))


def topn_realized_return(
    records, *, rank_key, ret_key="realized_ret", top_n=8, ascending
):
    """Mean over dates of the equal-weight realized return of the top-N names.

    Phase 1: ``rank_key="p1_prob_up", ascending=False`` (highest prob first).
    Phase 2: ``rank_key="p2_cs_rank", ascending=True`` (rank 1 = best first).
    Per-date mean uses only finite ``ret_key`` values; a date with no finite
    realized return contributes nothing. ``None`` when no date is usable.
    """
    per_date = topn_per_date_returns(
        records, rank_key=rank_key, ret_key=ret_key, top_n=top_n, ascending=ascending
    )
    return _mean_or_none(list(per_date.values()))


def topn_per_date_returns(
    records, *, rank_key, ret_key="realized_ret", top_n=8, ascending
) -> dict:
    """Ordered {date -> equal-weight top-N realized return} (finite only).

    The per-date series that ``max_drawdown_from_period_returns`` consumes to
    build a strategy equity curve. Dates whose top-N has no finite realized
    return are omitted (no spurious 0.0 period).
    """
    df = _records_to_frame(records)
    out: dict[str, float] = {}
    if df.empty or rank_key not in df.columns:
        return out
    for date, grp in df.groupby("date", sort=True):
        top = _select_topn_per_date(grp, rank_key, top_n, ascending)
        if top.empty:
            continue
        ret = pd.to_numeric(top[ret_key], errors="coerce").to_numpy(dtype="float64")
        finite = ret[np.isfinite(ret)]
        if finite.size == 0:
            continue
        out[str(date)] = float(np.mean(finite))
    return out


def hit_rate_topn(records, *, rank_key, ret_key="realized_ret", top_n=8, ascending):
    """Fraction of selected top-N names (pooled across dates) with ret > 0.

    Mirrors ``cs_metrics`` precision_at_n semantics (positive-return share of the
    long book). ``None`` when no selected name has a finite realized return.
    """
    df = _records_to_frame(records)
    if df.empty or rank_key not in df.columns:
        return None
    rets: list[float] = []
    for _date, grp in df.groupby("date", sort=True):
        top = _select_topn_per_date(grp, rank_key, top_n, ascending)
        if top.empty:
            continue
        r = pd.to_numeric(top[ret_key], errors="coerce").to_numpy(dtype="float64")
        rets.extend(r[np.isfinite(r)].tolist())
    if not rets:
        return None
    arr = np.asarray(rets, dtype="float64")
    return float(np.mean(arr > 0.0))


# ---------------------------------------------------------------------------
# Turnover (Phase 2 portfolio only)
# ---------------------------------------------------------------------------


def turnover(records):
    """Mean per-date ``0.5 * sum|p2_weight - p2_prev_weight|`` (Phase 2 weights).

    A name present on one side only contributes its full weight (missing weight
    treated as 0). ``None`` when no date carries any Phase 2 weight, so a window
    with no portfolio snapshots reports ``None`` rather than 0.
    """
    df = _records_to_frame(records)
    if df.empty:
        return None
    per_date: list[float] = []
    for _date, grp in df.groupby("date", sort=True):
        w = (
            pd.to_numeric(grp["p2_weight"], errors="coerce")
            .fillna(0.0)
            .to_numpy(dtype="float64")
        )
        pw = (
            pd.to_numeric(grp["p2_prev_weight"], errors="coerce")
            .fillna(0.0)
            .to_numpy(dtype="float64")
        )
        has_weight = (
            grp["p2_weight"].notna().any() or grp["p2_prev_weight"].notna().any()
        )
        if not has_weight:
            continue
        per_date.append(0.5 * float(np.sum(np.abs(w - pw))))
    if not per_date:
        return None
    return float(np.mean(per_date))


# ---------------------------------------------------------------------------
# Max drawdown from a period-return sequence
# ---------------------------------------------------------------------------


def max_drawdown_from_period_returns(per_date_returns):
    """Most-negative drawdown of the cumulative-product equity curve (<= 0).

    ``per_date_returns`` is an iterable of per-period simple returns (e.g. the
    values of ``topn_per_date_returns``). Equity starts at 1.0 and compounds.
    Returns 0.0 for an empty / all-non-finite / monotonically-rising curve;
    always <= 0.0 otherwise. ``None`` only when there is nothing to evaluate.
    """
    if per_date_returns is None:
        return None
    rets = [float(r) for r in per_date_returns if r is not None and np.isfinite(r)]
    if not rets:
        return None
    equity = np.cumprod(1.0 + np.asarray(rets, dtype="float64"))
    running_max = np.maximum.accumulate(equity)
    drawdowns = equity / running_max - 1.0
    return float(np.min(drawdowns))


# ---------------------------------------------------------------------------
# Expected-return calibration (Phase 2)
# ---------------------------------------------------------------------------


def expected_ret_calibration(records, *, top_n=8):
    """Phase 2 top-N predicted vs realized return (pooled across dates).

    Selects the Phase 2 top-N (``p2_cs_rank`` ascending) each date, then compares
    ``mean(p2_expected_ret)`` against ``mean(realized_ret)`` over the pooled
    selected names. ``bias = mean_expected - mean_realized`` (positive = the
    model over-predicts). Fields are ``None`` when undefined.
    """
    df = _records_to_frame(records)
    base = {
        "mean_expected_ret": None,
        "mean_realized_ret": None,
        "bias": None,
        "n": 0,
    }
    if df.empty or "p2_cs_rank" not in df.columns:
        return base
    exp_vals: list[float] = []
    real_vals: list[float] = []
    for _date, grp in df.groupby("date", sort=True):
        top = _select_topn_per_date(grp, "p2_cs_rank", top_n, ascending=True)
        if top.empty:
            continue
        exp_vals.extend(
            pd.to_numeric(top["p2_expected_ret"], errors="coerce").dropna().tolist()
        )
        real_vals.extend(
            pd.to_numeric(top["realized_ret"], errors="coerce").dropna().tolist()
        )
    mean_exp = float(np.mean(exp_vals)) if exp_vals else None
    mean_real = float(np.mean(real_vals)) if real_vals else None
    bias = (
        (mean_exp - mean_real)
        if (mean_exp is not None and mean_real is not None)
        else None
    )
    return {
        "mean_expected_ret": mean_exp,
        "mean_realized_ret": mean_real,
        "bias": bias,
        "n": int(max(len(exp_vals), len(real_vals))),
    }


# ---------------------------------------------------------------------------
# Per-strategy metric bundles
# ---------------------------------------------------------------------------


def _phase1_metrics(records, *, top_n) -> dict:
    """Phase 1 per-ticker side: rank by prob_up (higher = better)."""
    per_date = topn_per_date_returns(
        records, rank_key="p1_prob_up", top_n=top_n, ascending=False
    )
    return {
        "daily_ic": daily_ic(records, score_key="p1_prob_up", method="pearson"),
        "rank_ic": daily_ic(records, score_key="p1_prob_up", method="spearman"),
        "topn_realized_return": _mean_or_none(list(per_date.values())),
        "hit_rate": hit_rate_topn(
            records, rank_key="p1_prob_up", top_n=top_n, ascending=False
        ),
        "max_drawdown": max_drawdown_from_period_returns(per_date.values()),
        "turnover": None,  # Phase 1 has no portfolio weights in this comparison
        "n_dates_with_topn": len(per_date),
    }


def _phase2_metrics(records, *, top_n) -> dict:
    """Phase 2 CS/portfolio side: rank by cs_rank (1 = best -> ascending).

    IC correlates ``p2_expected_ret`` (higher = better) against realized return,
    which is rank-equivalent to ``-cs_rank`` and avoids materializing a negated
    column.
    """
    per_date = topn_per_date_returns(
        records, rank_key="p2_cs_rank", top_n=top_n, ascending=True
    )
    return {
        "daily_ic": daily_ic(records, score_key="p2_expected_ret", method="pearson"),
        "rank_ic": daily_ic(records, score_key="p2_expected_ret", method="spearman"),
        "topn_realized_return": _mean_or_none(list(per_date.values())),
        "hit_rate": hit_rate_topn(
            records, rank_key="p2_cs_rank", top_n=top_n, ascending=True
        ),
        "max_drawdown": max_drawdown_from_period_returns(per_date.values()),
        "turnover": turnover(records),
        "expected_ret_calibration": expected_ret_calibration(records, top_n=top_n),
        "n_dates_with_topn": len(per_date),
    }


def _delta(p2, p1):
    """p2 - p1, ``None`` when either side is None/non-finite."""
    if p1 is None or p2 is None:
        return None
    if not (np.isfinite(p1) and np.isfinite(p2)):
        return None
    return float(p2 - p1)


def _ge(p2, p1):
    """``p2 >= p1`` verdict, ``None`` when either side is undefined."""
    if p1 is None or p2 is None:
        return None
    if not (np.isfinite(p1) and np.isfinite(p2)):
        return None
    return bool(p2 >= p1)


def compare_phase1_phase2(records, *, top_n=8) -> dict:
    """Full Phase 1 vs Phase 2 comparison: per-side metrics, deltas, verdict.

    Returns ``{"phase1": {...}, "phase2": {...}, "delta": {...},
    "verdict": {...}}``. All deltas/verdicts are NaN/None-safe: a ``None`` metric
    on either side yields ``None`` for the corresponding delta/verdict (never a
    spurious ``True``).
    """
    p1 = _phase1_metrics(records, top_n=top_n)
    p2 = _phase2_metrics(records, top_n=top_n)

    delta = {
        "daily_ic": _delta(p2["daily_ic"], p1["daily_ic"]),
        "rank_ic": _delta(p2["rank_ic"], p1["rank_ic"]),
        "topn_realized_return": _delta(
            p2["topn_realized_return"], p1["topn_realized_return"]
        ),
        "hit_rate": _delta(p2["hit_rate"], p1["hit_rate"]),
        "max_drawdown": _delta(p2["max_drawdown"], p1["max_drawdown"]),
    }
    verdict = {
        "phase2_ic_ge_phase1": _ge(p2["daily_ic"], p1["daily_ic"]),
        "phase2_rank_ic_ge_phase1": _ge(p2["rank_ic"], p1["rank_ic"]),
        "phase2_topn_ge_phase1": _ge(
            p2["topn_realized_return"], p1["topn_realized_return"]
        ),
        "phase2_hit_rate_ge_phase1": _ge(p2["hit_rate"], p1["hit_rate"]),
        # Drawdown is <= 0; "no worse" means p2 drawdown is shallower (>= p1).
        "phase2_drawdown_no_worse": _ge(p2["max_drawdown"], p1["max_drawdown"]),
    }
    return {"phase1": p1, "phase2": p2, "delta": delta, "verdict": verdict}


# ---------------------------------------------------------------------------
# Top-level report payload
# ---------------------------------------------------------------------------


def build_shadow_report(
    records, *, top_n=8, window=None, generated_at=None, model_version=None
) -> dict:
    """Assemble the shadow-validation report payload from normalized records.

    ``available=False`` with ``reason="insufficient_shadow_history"`` when there
    are fewer than ``MIN_SHADOW_DATES`` distinct dates (or zero records).
    Otherwise ``available=True`` with a full ``comparison`` block.

    ``generated_at`` is only stamped into the payload when supplied (so tests can
    assert a deterministic dict). ``window`` (e.g. ``{"start": ..., "end": ...,
    "lookback_days": ...}``) and ``model_version`` are echoed when provided.
    """
    df = _records_to_frame(records)
    n_records = int(len(df))
    n_dates = int(df["date"].nunique()) if not df.empty else 0

    payload: dict = {
        "available": False,
        "n_dates": n_dates,
        "n_records": n_records,
        "top_n": int(top_n),
    }
    if generated_at is not None:
        payload["generated_at"] = generated_at
    if window is not None:
        payload["window"] = window
    if model_version is not None:
        payload["model_version"] = model_version

    if n_records == 0 or n_dates < MIN_SHADOW_DATES:
        payload["reason"] = "insufficient_shadow_history"
        payload["min_dates_required"] = MIN_SHADOW_DATES
        return payload

    payload["available"] = True
    payload["comparison"] = compare_phase1_phase2(records, top_n=top_n)
    return payload
