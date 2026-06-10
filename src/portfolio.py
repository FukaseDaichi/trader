"""
Phase 2 long-only portfolio construction (roadmap §6.2).

Pure logic — pandas / numpy only, NO database or network. Given the daily
cross-sectional predictions (one row per ticker: ticker, cs_rank, prob_up,
expected_ret, and optionally name / sector / volatility / close) this module
builds a long-only target portfolio that simultaneously satisfies:

  * per-name weight cap          (max_name_weight)
  * per-sector weight cap        (sector_cap)
  * gross exposure cap           (max_gross)
  * minimum position size        (min_weight, exit / never-enter below it)
  * no-trade band hysteresis     (notrade_band vs the previous book)

plus annualized volatility targeting (target_vol) with a risk-off gross
multiplier. Covariance is estimated from price history with a graceful
diagonal / epsilon fallback when there is not enough overlapping data.

The public functions keep stable names / signatures because Task 7 (KPI gate /
backtest) and Task 8 (snapshot serialization + dashboard) depend on them.

Design notes
------------
Caps are enforced on the NORMALIZED weights (sum 1.0) BEFORE vol-scaling via
``enforce_caps``, which alternates the per-name and per-sector projections
until both hold simultaneously (sector redistribution can re-violate the name
cap and vice-versa). After ``scale_to_target_vol`` the absolute weights sum to
``gross`` (<= max_gross). Per-position limit / stop levels reuse the long-side
convention from ``src/predictor.generate_signal``.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

# Tolerance used throughout for cap / sum comparisons.
_EPS = 1e-9
# Tiny positive variance floor so a degenerate ticker never has zero / NaN vol.
_VAR_FLOOR = 1e-8

__all__ = [
    "select_candidates",
    "estimate_covariance",
    "initial_inverse_vol_weights",
    "apply_name_cap",
    "apply_sector_cap",
    "enforce_caps",
    "scale_to_target_vol",
    "apply_hysteresis",
    "diff_positions",
    "build_portfolio_snapshot",
    "merge_target_weights",
    "read_portfolio_gate",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _is_missing(value) -> bool:
    """True for None / NaN / non-numeric."""
    if value is None:
        return True
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def _to_float(value, default=None):
    """Best-effort float coercion; return ``default`` for missing values."""
    if _is_missing(value):
        return default
    return float(value)


def _records(predictions) -> list[dict]:
    """Normalize a DataFrame or iterable-of-dicts into a list of plain dicts."""
    if predictions is None:
        return []
    if isinstance(predictions, pd.DataFrame):
        return predictions.to_dict("records")
    out: list[dict] = []
    for row in predictions:
        if isinstance(row, dict):
            out.append(dict(row))
        elif hasattr(row, "_asdict"):  # namedtuple-ish
            out.append(dict(row._asdict()))
        else:  # pandas Series or mapping-like
            out.append(dict(row))
    return out


def _median_positive(values) -> float | None:
    """Median of the strictly-positive, finite entries of ``values``."""
    arr = [float(v) for v in values if not _is_missing(v) and float(v) > 0.0]
    if not arr:
        return None
    return float(np.median(arr))


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def select_candidates(predictions, *, top_n, min_expected_ret=0.0):
    """Filter, sort and truncate the cross-sectional predictions.

    Keep rows whose ``expected_ret`` >= ``min_expected_ret``. A row with a
    missing ``expected_ret`` is eligible only when ``min_expected_ret`` <= 0
    (otherwise it is excluded, since we cannot confirm the floor is met).
    Sort by ``cs_rank`` ascending (1 = best), take the first ``top_n``.

    Returns a list of candidate dicts preserving every passed field. ``[]``
    when nothing qualifies.
    """
    rows = _records(predictions)
    if not rows:
        return []

    floor = _to_float(min_expected_ret, 0.0) or 0.0

    eligible: list[dict] = []
    for row in rows:
        er = _to_float(row.get("expected_ret"))
        if er is None:
            # Missing expected return: only eligible if the floor is <= 0.
            if floor <= 0.0:
                eligible.append(row)
            continue
        if er >= floor - _EPS:
            eligible.append(row)

    # Sort by cs_rank ascending; missing ranks sink to the bottom (stable).
    def _rank_key(row):
        r = _to_float(row.get("cs_rank"))
        return (r is None, r if r is not None else math.inf)

    eligible.sort(key=_rank_key)

    try:
        n = int(top_n)
    except (TypeError, ValueError):
        n = len(eligible)
    if n < 0:
        n = 0
    return eligible[:n]


# ---------------------------------------------------------------------------
# Covariance estimation
# ---------------------------------------------------------------------------

def estimate_covariance(price_frames, tickers, *, lookback_days=60, min_obs=20,
                        trading_days=252):
    """Estimate an annualized covariance matrix aligned to ``tickers``.

    Parameters
    ----------
    price_frames : dict ``ticker -> DataFrame`` with columns ``date`` and
        ``close`` (daily). Frames missing a ticker are tolerated.
    tickers : ordered list defining the row/column order of the output.
    lookback_days : most-recent N daily returns to use per ticker.
    min_obs : minimum aligned overlapping daily returns required to trust the
        full sample covariance; below this we fall back to a diagonal matrix.
    trading_days : annualization factor for variance (daily var * trading_days).

    Returns
    -------
    (cov, vol, method)
        cov    : ``np.ndarray`` (n x n) annualized covariance.
        vol    : dict ``ticker -> annualized stdev`` (sqrt of the diagonal).
        method : ``"sample"`` when >= ``min_obs`` aligned overlapping rows
                 exist across the tickers, else ``"diagonal"`` (per-ticker
                 variance from each ticker's own returns; off-diagonals 0).

    Never raises: degrades to diagonal / epsilon on any failure. A ticker with
    no usable returns gets the cross-ticker median variance (or a tiny epsilon).
    """
    tickers = list(tickers)
    n = len(tickers)
    if n == 0:
        return np.zeros((0, 0), dtype="float64"), {}, "diagonal"

    price_frames = price_frames or {}

    # Per-ticker daily simple-return series, most recent `lookback_days`.
    ret_series: dict[str, pd.Series] = {}
    for tk in tickers:
        frame = price_frames.get(tk)
        s = _daily_returns(frame, lookback_days)
        if s is not None and len(s) >= 2:
            ret_series[tk] = s

    # Per-ticker annualized variance from each ticker's own returns (diagonal).
    own_var: dict[str, float] = {}
    for tk in tickers:
        s = ret_series.get(tk)
        if s is not None and len(s) >= 2:
            v = float(np.nanvar(s.to_numpy(dtype="float64"), ddof=1))
            if math.isfinite(v) and v > 0.0:
                own_var[tk] = v * trading_days
    median_var = _median_positive(own_var.values())

    def _fallback_var(tk: str) -> float:
        v = own_var.get(tk)
        if v is not None and v > 0.0:
            return v
        if median_var is not None:
            return median_var
        return _VAR_FLOOR

    # --- Try the full sample covariance on the aligned (overlapping) panel. ---
    method = "diagonal"
    cov = np.zeros((n, n), dtype="float64")
    try:
        if len(ret_series) >= 1:
            aligned = pd.DataFrame(ret_series).dropna(how="any")
            if aligned.shape[0] >= min_obs and aligned.shape[1] >= 1:
                sample = aligned.cov(ddof=1) * trading_days  # annualized
                # Place the sample block into the full matrix; fill any ticker
                # absent from the aligned panel with its own variance on the
                # diagonal (off-diagonals stay 0 for it).
                idx = {tk: i for i, tk in enumerate(tickers)}
                for tk_i in tickers:
                    for tk_j in tickers:
                        i, j = idx[tk_i], idx[tk_j]
                        if tk_i in sample.index and tk_j in sample.columns:
                            cov[i, j] = float(sample.loc[tk_i, tk_j])
                # Diagonal repair for tickers not in the aligned panel.
                for k, tk in enumerate(tickers):
                    if tk not in sample.index or not math.isfinite(cov[k, k]) or cov[k, k] <= 0.0:
                        cov[k, k] = _fallback_var(tk)
                method = "sample"
    except Exception:  # noqa: BLE001 — never raise; fall through to diagonal.
        method = "diagonal"

    if method != "sample":
        cov = np.zeros((n, n), dtype="float64")
        for k, tk in enumerate(tickers):
            cov[k, k] = _fallback_var(tk)

    # Final sanitation: symmetrize, repair non-finite / non-positive diagonal.
    cov = np.nan_to_num(cov, nan=0.0, posinf=0.0, neginf=0.0)
    cov = 0.5 * (cov + cov.T)
    for k, tk in enumerate(tickers):
        if not math.isfinite(cov[k, k]) or cov[k, k] <= 0.0:
            cov[k, k] = _fallback_var(tk)

    vol = {tk: math.sqrt(max(cov[k, k], 0.0)) for k, tk in enumerate(tickers)}
    return cov, vol, method


def _daily_returns(frame, lookback_days):
    """Most-recent ``lookback_days`` daily simple returns from a price frame."""
    if frame is None:
        return None
    try:
        if isinstance(frame, pd.DataFrame):
            if "close" not in frame.columns:
                return None
            df = frame
            if "date" in df.columns:
                df = df.sort_values("date")
            close = pd.to_numeric(df["close"], errors="coerce")
        else:  # already a close series
            close = pd.to_numeric(pd.Series(frame), errors="coerce")
        close = close.dropna()
        if len(close) < 3:
            return None
        rets = close.pct_change().dropna()
        if lookback_days and lookback_days > 0:
            rets = rets.tail(int(lookback_days))
        rets = rets.replace([np.inf, -np.inf], np.nan).dropna()
        return rets.reset_index(drop=True) if len(rets) >= 2 else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Initial weights
# ---------------------------------------------------------------------------

def initial_inverse_vol_weights(candidates, vol):
    """Inverse-volatility weights normalized to sum 1.0.

    ``weight_i`` proportional to ``1 / vol_i`` where ``vol_i`` is taken from the
    ``vol`` dict, falling back to the candidate's own ``volatility`` field.
    Missing / zero vol is treated as the median vol across candidates; if every
    vol is missing, weights are equal. Returns ``{}`` for no candidates.
    """
    rows = _records(candidates)
    if not rows:
        return {}
    vol = vol or {}

    tickers = [r.get("ticker") for r in rows]
    raw_vols: dict[str, float | None] = {}
    for r in rows:
        tk = r.get("ticker")
        v = _to_float(vol.get(tk))
        if v is None or v <= 0.0:
            v = _to_float(r.get("volatility"))
        raw_vols[tk] = v if (v is not None and v > 0.0) else None

    median_vol = _median_positive([v for v in raw_vols.values() if v is not None])

    if median_vol is None:
        # All vols missing -> equal weights.
        w = 1.0 / len(tickers)
        return {tk: w for tk in tickers}

    inv = {tk: 1.0 / (raw_vols[tk] if raw_vols[tk] is not None else median_vol)
           for tk in tickers}
    total = sum(inv.values())
    if total <= 0.0:
        w = 1.0 / len(tickers)
        return {tk: w for tk in tickers}
    return {tk: v / total for tk, v in inv.items()}


# ---------------------------------------------------------------------------
# Cap projections
# ---------------------------------------------------------------------------

def apply_name_cap(weights, max_name_weight):
    """Cap each weight at ``max_name_weight``, redistributing the excess.

    Excess from capped names is redistributed to the still-uncapped names in
    proportion to their current weight; iterate until stable. The total sum is
    preserved (within numerical tolerance). Returns a dict.
    """
    if not weights:
        return {}
    cap = float(max_name_weight)
    w = {k: float(v) for k, v in weights.items()}
    total = sum(w.values())
    if cap <= 0.0 or total <= 0.0:
        return w

    for _ in range(100):
        over = {k: v for k, v in w.items() if v > cap + _EPS}
        if not over:
            break
        excess = sum(v - cap for v in over.values())
        for k in over:
            w[k] = cap
        under = {k: v for k, v in w.items() if v < cap - _EPS}
        pool = sum(under.values())
        if pool <= _EPS:
            # No headroom left to absorb the excess -> all names at the cap.
            break
        for k in under:
            w[k] += excess * (w[k] / pool)
    return w


def apply_sector_cap(weights, sectors, sector_cap):
    """Cap each sector's total at ``sector_cap``, redistributing the excess.

    ``sectors`` maps ``ticker -> sector`` (``None`` allowed -> the ticker forms
    its own singleton bucket). Over-cap sectors are scaled down proportionally
    and the freed weight is redistributed to under-cap sectors in proportion to
    their current totals; iterate until stable. The grand total is preserved.
    Returns a dict.
    """
    if not weights:
        return {}
    cap = float(sector_cap)
    w = {k: float(v) for k, v in weights.items()}
    total = sum(w.values())
    if cap <= 0.0 or total <= 0.0:
        return w

    sectors = sectors or {}

    def _bucket(tk):
        s = sectors.get(tk)
        # None / NaN -> unique singleton bucket so it is never capped jointly.
        return s if (s is not None and not (isinstance(s, float) and math.isnan(s))) \
            else f"__solo__{tk}"

    members: dict[Any, list] = {}
    for tk in w:
        members.setdefault(_bucket(tk), []).append(tk)

    for _ in range(100):
        sector_tot = {sec: sum(w[tk] for tk in mem) for sec, mem in members.items()}
        over = {sec: t for sec, t in sector_tot.items() if t > cap + _EPS}
        if not over:
            break
        excess = 0.0
        for sec, t in over.items():
            scale = cap / t if t > 0.0 else 0.0
            for tk in members[sec]:
                w[tk] *= scale
            excess += t - cap
        # Redistribute to sectors strictly under the cap, by their total.
        under = {sec: t for sec, t in sector_tot.items() if t < cap - _EPS}
        pool = sum(under.values())
        if pool <= _EPS:
            break
        for sec in under:
            sec_total = sector_tot[sec]
            add = excess * (sec_total / pool)
            # Distribute the sector's share across its names proportionally.
            for tk in members[sec]:
                share = (w[tk] / sec_total) if sec_total > 0.0 else (1.0 / len(members[sec]))
                w[tk] += add * share
    return w


def enforce_caps(weights, sectors, *, max_name_weight, sector_cap, max_iter=20):
    """Project ``weights`` so BOTH the name cap and sector cap hold at once.

    Alternates ``apply_name_cap`` and ``apply_sector_cap`` until neither is
    violated (within 1e-9) or ``max_iter`` is reached. This alternation is
    required: sector redistribution can push a name back over the name cap and
    capping a name can push its sector back over the sector cap.

    If the caps are jointly infeasible (e.g. a sector with fewer names than
    ``sector_cap / max_name_weight`` requires) the alternation cannot reach a
    fixed point; a final hard clamp (name cap, then sector cap) is applied so
    the returned dict is the best feasible projection (sector cap strictly
    satisfied; name cap satisfied wherever feasible). The caller records a
    warning by checking the result.
    """
    if not weights:
        return {}
    w = {k: float(v) for k, v in weights.items()}

    for _ in range(max(1, int(max_iter))):
        w = apply_name_cap(w, max_name_weight)
        w = apply_sector_cap(w, sectors, sector_cap)
        if _caps_satisfied(w, sectors, max_name_weight, sector_cap):
            return w

    # Did not converge (likely jointly infeasible). Hard clamp without
    # redistribution so we never exceed either cap where it is feasible. We
    # clamp names first, then clamp sectors (scaling down only), which can only
    # lower weights and therefore cannot re-violate the name cap.
    w = _hard_clamp_names(w, max_name_weight)
    w = _hard_clamp_sectors(w, sectors, sector_cap)
    return w


def _caps_satisfied(weights, sectors, max_name_weight, sector_cap) -> bool:
    if not weights:
        return True
    if any(v > float(max_name_weight) + _EPS for v in weights.values()):
        return False
    for sec_total in _sector_totals(weights, sectors).values():
        if sec_total > float(sector_cap) + _EPS:
            return False
    return True


def _sector_totals(weights, sectors) -> dict:
    sectors = sectors or {}
    totals: dict[Any, float] = {}
    for tk, v in weights.items():
        s = sectors.get(tk)
        key = s if (s is not None and not (isinstance(s, float) and math.isnan(s))) \
            else f"__solo__{tk}"
        totals[key] = totals.get(key, 0.0) + float(v)
    return totals


def _hard_clamp_names(weights, max_name_weight):
    cap = float(max_name_weight)
    return {k: min(float(v), cap) for k, v in weights.items()}


def _hard_clamp_sectors(weights, sectors, sector_cap):
    cap = float(sector_cap)
    sectors = sectors or {}
    w = {k: float(v) for k, v in weights.items()}
    members: dict[Any, list] = {}
    for tk in w:
        s = sectors.get(tk)
        key = s if (s is not None and not (isinstance(s, float) and math.isnan(s))) \
            else f"__solo__{tk}"
        members.setdefault(key, []).append(tk)
    for sec, mem in members.items():
        tot = sum(w[tk] for tk in mem)
        if tot > cap + _EPS and tot > 0.0:
            scale = cap / tot
            for tk in mem:
                w[tk] *= scale
    return w


# ---------------------------------------------------------------------------
# Volatility targeting
# ---------------------------------------------------------------------------

def scale_to_target_vol(weights, cov, tickers, *, target_vol, max_gross,
                        regime_multiplier=1.0):
    """Scale normalized weights to hit ``target_vol`` subject to ``max_gross``.

    Computes the portfolio annualized vol at gross=1: ``pvol = sqrt(wᵀ Σ w)``.
    Then ``gross = min(max_gross, target_vol / pvol) * regime_multiplier``,
    clamped to ``[0, max_gross]``. When ``pvol <= 0`` -> ``gross =
    max_gross * regime_multiplier`` (clamped).

    Returns ``(scaled_weights, expected_vol, gross)`` where
    ``scaled_weights = normalized_weights * gross`` and
    ``expected_vol = gross * pvol``.
    """
    target_vol = float(target_vol)
    max_gross = float(max_gross)
    regime_multiplier = float(regime_multiplier)

    if not weights:
        return {}, 0.0, 0.0

    tickers = list(tickers)
    # Normalize to sum 1 for the vol computation (defensive).
    total = sum(float(v) for v in weights.values())
    if total <= 0.0:
        return {tk: 0.0 for tk in weights}, 0.0, 0.0
    norm = {k: float(v) / total for k, v in weights.items()}

    w_vec = np.array([norm.get(tk, 0.0) for tk in tickers], dtype="float64")
    pvol = 0.0
    if cov is not None and len(tickers) == np.asarray(cov).shape[0] and w_vec.size:
        var = float(w_vec @ np.asarray(cov, dtype="float64") @ w_vec)
        if math.isfinite(var) and var > 0.0:
            pvol = math.sqrt(var)

    if pvol > 0.0:
        gross = min(max_gross, target_vol / pvol) * regime_multiplier
    else:
        gross = max_gross * regime_multiplier
    gross = max(0.0, min(gross, max_gross))

    scaled = {k: v * gross for k, v in norm.items()}
    expected_vol = gross * pvol
    return scaled, expected_vol, gross


# ---------------------------------------------------------------------------
# Hysteresis (no-trade band + min-weight floor)
# ---------------------------------------------------------------------------

def apply_hysteresis(new_weights, prev_weights, *, notrade_band, min_weight):
    """Apply the no-trade band then the minimum-weight floor.

    Starting from ``new_weights`` (absolute, post vol-scaling), for each ticker
    present in either dict:
      * if a previous weight exists and ``|new - prev| < notrade_band`` keep the
        previous weight (no trade);
      * then any resulting weight ``< min_weight`` is dropped to 0 (exit or
        never-enter).

    Returns a dict ``ticker -> weight`` containing only the nonzero entries.
    Names absent from both inputs are absent from the result.
    """
    new_weights = new_weights or {}
    prev_weights = prev_weights or {}
    band = float(notrade_band)
    floor = float(min_weight)

    tickers = set(new_weights) | set(prev_weights)
    out: dict[str, float] = {}
    for tk in tickers:
        new_w = float(new_weights.get(tk, 0.0))
        has_prev = tk in prev_weights
        prev_w = float(prev_weights.get(tk, 0.0))

        if has_prev and abs(new_w - prev_w) < band:
            w = prev_w
        else:
            w = new_w

        if w < floor:
            w = 0.0
        if w > 0.0:
            out[tk] = w
    return out


# ---------------------------------------------------------------------------
# Position diff
# ---------------------------------------------------------------------------

def diff_positions(prev_weights, current_weights):
    """Classify the change for every ticker in either book.

    diff_type:
      * ``"new"``       prev <= 0 and curr > 0
      * ``"exit"``      prev > 0 and curr <= 0
      * ``"increase"``  curr > prev (both > 0)
      * ``"decrease"``  curr < prev (both > 0)
      * ``"hold"``      otherwise (unchanged, or both <= 0)

    Returns a list of ``{ticker, prev_weight, target_weight, diff_type}``.
    """
    prev_weights = prev_weights or {}
    current_weights = current_weights or {}
    tickers = sorted(set(prev_weights) | set(current_weights))

    out = []
    for tk in tickers:
        prev = float(prev_weights.get(tk, 0.0))
        curr = float(current_weights.get(tk, 0.0))
        if prev <= 0.0 and curr > 0.0:
            diff_type = "new"
        elif prev > 0.0 and curr <= 0.0:
            diff_type = "exit"
        elif prev > 0.0 and curr > 0.0 and curr > prev + _EPS:
            diff_type = "increase"
        elif prev > 0.0 and curr > 0.0 and curr < prev - _EPS:
            diff_type = "decrease"
        else:
            diff_type = "hold"
        out.append({
            "ticker": tk,
            "prev_weight": prev,
            "target_weight": curr,
            "diff_type": diff_type,
        })
    return out


# ---------------------------------------------------------------------------
# End-to-end snapshot
# ---------------------------------------------------------------------------

def _enrich_lookup(candidates, override, field):
    """Build a ``ticker -> field`` lookup, preferring ``override`` dict."""
    out: dict[str, Any] = {}
    for c in candidates:
        tk = c.get("ticker")
        if tk is None:
            continue
        if field in c and c.get(field) is not None:
            out[tk] = c.get(field)
    if override:
        for tk, val in override.items():
            if val is not None:
                out[tk] = val
    return out


def _limit_stop(close):
    """Long-side limit / stop from close, mirroring predictor.generate_signal."""
    c = _to_float(close)
    if c is None or c <= 0.0:
        return None, None
    return int(c * (1 - 0.005)), int(c * (1 - 0.02))


def build_portfolio_snapshot(predictions, price_frames, prev_weights, config, *,
                             sectors=None, names=None, volatilities=None, closes=None,
                             regime="neutral", run_date=None, as_of_date=None,
                             model_version=None, mode="shadow"):
    """Orchestrate the full long-only portfolio build.

    Pipeline: ``select_candidates`` (top_n from cross-section config) ->
    ``estimate_covariance`` -> ``initial_inverse_vol_weights`` -> ``enforce_caps``
    -> ``scale_to_target_vol`` (risk-off multiplier when ``regime == 'risk_off'``)
    -> ``apply_hysteresis`` (vs ``prev_weights``) -> ``diff_positions``.

    The ``sectors`` / ``names`` / ``volatilities`` / ``closes`` dicts enrich the
    output; when ``None`` the corresponding field is pulled from the candidate
    dicts when present.

    Cash portfolio: when no candidate is eligible, returns ``status='ok'`` with
    empty positions, gross 0 and ``warnings=['no_eligible_candidates']``.

    Never raises: on internal degradation a warning is appended and the build
    continues with the best available result. Returns the snapshot dict whose
    schema is documented in the module / task spec.
    """
    warnings: list[str] = []
    cfg = dict(config or {})

    target_vol = float(cfg.get("target_vol", 0.12))
    max_name_weight = float(cfg.get("max_name_weight", 0.20))
    sector_cap = float(cfg.get("sector_cap", 0.40))
    max_gross = float(cfg.get("max_gross", 1.00))
    min_weight = float(cfg.get("min_weight", 0.03))
    notrade_band = float(cfg.get("notrade_band", 0.02))
    min_expected_ret = float(cfg.get("min_expected_ret", 0.0))
    risk_off_mult = float(cfg.get("risk_off_gross_mult", 0.50))
    cov_lookback_days = int(cfg.get("cov_lookback_days", 60))

    top_n = cfg.get("top_n")
    if top_n is None:
        try:
            from src.config import get_cross_section_config
            top_n = get_cross_section_config().get("top_n", 8)
        except Exception:  # noqa: BLE001
            top_n = 8
    top_n = int(top_n)

    regime = (regime or "neutral").strip().lower()
    regime_multiplier = risk_off_mult if regime == "risk_off" else 1.0

    prev_weights = {k: float(v) for k, v in (prev_weights or {}).items()}

    constraints = {
        "target_vol": target_vol,
        "max_name_weight": max_name_weight,
        "sector_cap": sector_cap,
        "max_gross": max_gross,
        "min_weight": min_weight,
        "notrade_band": notrade_band,
        "top_n": top_n,
        "cov_method": None,
        "regime": regime,
        "regime_multiplier": regime_multiplier,
    }

    def _empty_snapshot(status="ok", extra_warnings=None):
        wl = list(warnings)
        if extra_warnings:
            wl.extend(extra_warnings)
        # Even a cash book may exit previous positions; surface those diffs.
        diffs = diff_positions(prev_weights, {})
        n_exit = sum(1 for d in diffs if d["diff_type"] == "exit")
        return {
            "run_date": run_date,
            "as_of_date": as_of_date,
            "mode": mode,
            "status": status,
            "model_version": model_version,
            "gross_exposure": 0.0,
            "net_exposure": 0.0,
            "expected_vol": 0.0,
            "expected_ret": 0.0,
            "sector_exposure": {},
            "diff_summary": {"add": 0, "trim": 0, "exit": n_exit, "hold": 0},
            "positions": [],
            "constraints": constraints,
            "warnings": wl,
        }

    try:
        candidates = select_candidates(
            predictions, top_n=top_n, min_expected_ret=min_expected_ret
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"select_candidates_failed:{type(exc).__name__}")
        return _empty_snapshot(status="ok", extra_warnings=["no_eligible_candidates"])

    if not candidates:
        return _empty_snapshot(status="ok", extra_warnings=["no_eligible_candidates"])

    tickers = [c.get("ticker") for c in candidates]

    # Enrichment lookups (override dict wins over candidate fields).
    sector_lk = _enrich_lookup(candidates, sectors, "sector")
    name_lk = _enrich_lookup(candidates, names, "name")
    vol_lk = _enrich_lookup(candidates, volatilities, "volatility")
    close_lk = _enrich_lookup(candidates, closes, "close")

    # --- Covariance (never raises). ---
    try:
        cov, vol, cov_method = estimate_covariance(
            price_frames or {}, tickers, lookback_days=cov_lookback_days
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"covariance_failed:{type(exc).__name__}")
        cov = np.diag([1.0] * len(tickers))
        vol = {tk: 1.0 for tk in tickers}
        cov_method = "diagonal"
    constraints["cov_method"] = cov_method
    if cov_method == "diagonal":
        warnings.append("covariance_diagonal_fallback")

    # Merge model/price vol with any externally supplied vol (for inv-vol init).
    vol_for_init = dict(vol)
    for tk, v in vol_lk.items():
        fv = _to_float(v)
        if fv is not None and fv > 0.0:
            vol_for_init[tk] = fv

    # --- Initial inverse-vol weights -> caps -> vol target. ---
    try:
        init_w = initial_inverse_vol_weights(candidates, vol_for_init)
        capped = enforce_caps(
            init_w, sector_lk,
            max_name_weight=max_name_weight, sector_cap=sector_cap,
        )
        if not _caps_satisfied(capped, sector_lk, max_name_weight, sector_cap):
            warnings.append("caps_infeasible_best_effort")

        scaled, expected_vol, gross = scale_to_target_vol(
            capped, cov, tickers,
            target_vol=target_vol, max_gross=max_gross,
            regime_multiplier=regime_multiplier,
        )
        target_weights = apply_hysteresis(
            scaled, prev_weights,
            notrade_band=notrade_band, min_weight=min_weight,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"construction_failed:{type(exc).__name__}")
        return _empty_snapshot(status="failed", extra_warnings=["construction_error"])

    # Recompute gross / expected_vol on the final (post-hysteresis) book so the
    # reported numbers match the actual target weights.
    gross_exposure = float(sum(target_weights.values()))
    final_vec = np.array([target_weights.get(tk, 0.0) for tk in tickers], dtype="float64")
    final_var = 0.0
    if cov is not None and final_vec.size == np.asarray(cov).shape[0]:
        fv = float(final_vec @ np.asarray(cov, dtype="float64") @ final_vec)
        if math.isfinite(fv) and fv > 0.0:
            final_var = fv
    expected_vol = math.sqrt(final_var)

    # --- Diff vs the previous book. ---
    diffs = diff_positions(prev_weights, target_weights)
    diff_by_ticker = {d["ticker"]: d for d in diffs}

    add = sum(1 for d in diffs if d["diff_type"] in ("new", "increase"))
    trim = sum(1 for d in diffs if d["diff_type"] == "decrease")
    n_exit = sum(1 for d in diffs if d["diff_type"] == "exit")
    hold = sum(1 for d in diffs if d["diff_type"] == "hold")

    # Lookups by ticker for candidate scalar fields.
    cand_by_ticker = {c.get("ticker"): c for c in candidates}

    expected_ret_total = 0.0
    positions = []
    sector_exposure: dict[Any, float] = {}
    for tk, w in target_weights.items():
        c = cand_by_ticker.get(tk, {})
        er = _to_float(c.get("expected_ret"))
        if er is not None:
            expected_ret_total += w * er
        sec = sector_lk.get(tk)
        sector_exposure[sec] = sector_exposure.get(sec, 0.0) + w
        close = close_lk.get(tk)
        limit_price, stop_loss = _limit_stop(close)
        d = diff_by_ticker.get(tk, {})
        positions.append({
            "ticker": tk,
            "name": name_lk.get(tk),
            "sector": sec,
            "target_weight": w,
            "prev_weight": float(prev_weights.get(tk, 0.0)),
            "diff_type": d.get("diff_type", "hold"),
            "cs_rank": c.get("cs_rank"),
            "expected_ret": er,
            "prob_up": _to_float(c.get("prob_up")),
            "volatility": vol_for_init.get(tk),
            "limit_price": limit_price,
            "stop_loss": stop_loss,
        })

    positions.sort(key=lambda p: p["target_weight"], reverse=True)

    return {
        "run_date": run_date,
        "as_of_date": as_of_date,
        "mode": mode,
        "status": "ok",
        "model_version": model_version,
        "gross_exposure": gross_exposure,
        "net_exposure": gross_exposure,  # long only
        "expected_vol": expected_vol,
        "expected_ret": expected_ret_total,
        "sector_exposure": sector_exposure,
        "diff_summary": {"add": add, "trim": trim, "exit": n_exit, "hold": hold},
        "positions": positions,
        "constraints": constraints,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Active-mode signal wiring
# ---------------------------------------------------------------------------

def merge_target_weights(signals, snapshot, gate_passed):
    """Reflect active-mode portfolio target weights into Phase 1 signals.

    Active wiring only: when snapshot mode=="active" AND status=="ok" AND
    gate_passed, return a NEW list where each signal gets target_weight (0.0 if
    not a book position) and book members get a reason suffix. ACTION IS NEVER
    CHANGED. Otherwise (shadow / fallback / gate-fail / no snapshot) return the
    input list UNCHANGED (shadow byte-for-byte guarantee)."""
    if not (snapshot and snapshot.get("status") == "ok"
            and snapshot.get("mode") == "active" and gate_passed):
        return signals
    pos_by_ticker = {p.get("ticker"): p for p in (snapshot.get("positions") or [])}
    out = []
    for s in signals:
        s2 = dict(s)
        p = pos_by_ticker.get(s.get("ticker"))
        if p:
            w = float(p.get("target_weight") or 0.0)
            s2["target_weight"] = w
            rank = p.get("cs_rank")
            rank_str = f" (rank {rank})" if rank is not None else ""
            base = s.get("reason") or ""
            sep = "／" if base else ""
            s2["reason"] = f"{base}{sep}建玉 {w:.0%}{rank_str}"
        else:
            s2["target_weight"] = 0.0
        out.append(s2)
    return out


def read_portfolio_gate(path=None) -> bool:
    """Whether the weekly portfolio backtest cleared its gate (active-mode safety).

    Reads docs/portfolio_backtest.json. Missing/unavailable/error -> False.

    Current contract: ``run_portfolio_backtest`` emits no explicit pass/fail, so a
    successful weekly OOS backtest (``available==True``) IS the gate. The
    ``gate.passed`` branch below is a reserved extension point: if a future
    backtest report carries an explicit ``{"gate": {"passed": bool}}`` it takes
    precedence over the ``available`` fallback.
    """
    from pathlib import Path
    import json
    from .config import DOCS_DIR
    p = Path(path) if path is not None else (DOCS_DIR / "portfolio_backtest.json")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/corrupt -> not passed
        return False
    if not isinstance(data, dict) or not data.get("available"):
        return False
    gate = data.get("gate")
    if isinstance(gate, dict) and "passed" in gate:  # reserved extension point
        return bool(gate["passed"])
    return True  # available OOS backtest is the gate today
