"""
Universe selection pure functions for Phase 2 cross-sectional portfolio.

These functions are purely computational (no network I/O, no tickers.yml writes).
The script ``scripts/universe_select.py`` is the only entry point allowed to
write ``tickers.yml``.

Data contract — a **candidate** dict:
    {
        "code":      str,           e.g. "7011.JP"
        "name":      str,           e.g. "三菱重工業"
        "sector":    str | None,    e.g. "機械・重工"  (None for manual-only tickers)
        "combined":  float | None,  combined curation score (None if not scored)
        "rows":      int,           warmup rows available (0 if no parquet)
        "liquidity": float | None,  20-day avg trading value (None if unknown)
        "source":    str,           "enabled" | "watchlist" | "pool"
    }

``load_universe_candidates`` returns candidates WITHOUT rows/liquidity filled —
those are enriched by the script after reading parquets.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    pass

_NEG_INF = float("-inf")


# ---------------------------------------------------------------------------
# Candidate assembly
# ---------------------------------------------------------------------------

def load_universe_candidates(
    tickers_cfg: dict,
    pool: list[dict],
) -> list[dict]:
    """
    Merge enabled tickers + watchlist + pool into a deduplicated candidate list.

    Priority for name/combined/sector when the same code appears in multiple
    sources: enabled > watchlist > pool.  ``source`` reflects the *first*
    appearance (highest-priority source).

    Sector is backfilled from the pool when the ticker or watchlist entry lacks
    one (common for ``source: manual`` entries in tickers.yml).

    Returns candidates with ``rows=0`` and ``liquidity=None``; the caller
    (``scripts/universe_select.py``) enriches those from parquets.
    """
    # Build a sector lookup from the pool
    pool_by_code: dict[str, dict] = {p["code"]: p for p in pool if p.get("code")}

    seen: dict[str, dict] = {}  # code -> candidate

    # 1. Enabled tickers (highest priority)
    for t in tickers_cfg.get("tickers", []):
        if not isinstance(t, dict) or not t.get("code"):
            continue
        code = t["code"]
        if code in seen:
            continue
        sector = t.get("sector") or (pool_by_code.get(code, {}).get("sector"))
        seen[code] = {
            "code": code,
            "name": t.get("name", code),
            "sector": sector,
            "combined": _to_float(t.get("combined")),
            "rows": 0,
            "liquidity": None,
            "source": "enabled",
        }

    # 2. Watchlist
    for w in tickers_cfg.get("watchlist", []):
        if not isinstance(w, dict) or not w.get("code"):
            continue
        code = w["code"]
        if code in seen:
            continue
        sector = w.get("sector") or (pool_by_code.get(code, {}).get("sector"))
        seen[code] = {
            "code": code,
            "name": w.get("name", code),
            "sector": sector,
            "combined": _to_float(w.get("combined")),
            "rows": 0,
            "liquidity": None,
            "source": "watchlist",
        }

    # 3. Pool (lowest priority)
    for p in pool:
        if not isinstance(p, dict) or not p.get("code"):
            continue
        code = p["code"]
        if code in seen:
            # Backfill sector only if still missing
            if seen[code]["sector"] is None and p.get("sector"):
                seen[code]["sector"] = p["sector"]
            continue
        seen[code] = {
            "code": code,
            "name": p.get("name", code),
            "sector": p.get("sector"),
            "combined": None,
            "rows": 0,
            "liquidity": None,
            "source": "pool",
        }

    return list(seen.values())


# ---------------------------------------------------------------------------
# Liquidity helper
# ---------------------------------------------------------------------------

def compute_liquidity(df: pd.DataFrame | None, window: int = 20) -> float | None:
    """
    Compute the 20-day average trading value (close * volume) from the last
    ``window`` rows of *df*.

    Returns None when:
    - *df* is None or empty
    - required columns (``close``, ``volume``) are missing
    - fewer than 1 usable row remains after dropping NaN
    """
    if df is None or df.empty:
        return None
    if not {"close", "volume"}.issubset(df.columns):
        return None

    tail = df.tail(window).copy()
    trading_value = tail["close"] * tail["volume"]
    trading_value = trading_value.dropna()
    if trading_value.empty:
        return None
    return float(trading_value.mean())


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def rank_candidates(
    candidates: list[dict],
    *,
    min_warmup_rows: int,
) -> tuple[list[dict], list[dict]]:
    """
    Filter and sort candidates.

    Note: The planning document lists ``rank_candidates(candidates, liquidity,
    combined_score, warmup_rows)`` as separate arguments.  The implementation
    accepts enriched candidate dicts (each carrying ``.rows``, ``.liquidity``,
    and ``.combined``), which is equivalent and cleaner.

    Warmup filter: candidates with ``rows < min_warmup_rows`` are dropped and
    returned as the second element of the tuple.

    Sort order (deterministic):
        1. combined score desc  (None treated as -inf so scored names rank above)
        2. liquidity desc       (None treated as -inf)
        3. code asc             (stable tiebreak)

    Returns:
        (ranked_passing, filtered_low_warmup)
    """
    passing: list[dict] = []
    low_warmup: list[dict] = []
    for c in candidates:
        if (c.get("rows") or 0) >= min_warmup_rows:
            passing.append(c)
        else:
            low_warmup.append(c)

    def sort_key(c: dict) -> tuple:
        combined = c.get("combined")
        liq = c.get("liquidity")
        return (
            -(combined if combined is not None else _NEG_INF),
            -(liq if liq is not None else _NEG_INF),
            c.get("code", ""),
        )

    passing.sort(key=sort_key)
    return passing, low_warmup


# ---------------------------------------------------------------------------
# Sector cap
# ---------------------------------------------------------------------------

def apply_sector_cap(
    ranked: list[dict],
    target_size: int,
    sector_cap_pct: float,
) -> tuple[list[dict], list[dict]]:
    """
    Walk *ranked* and select up to *target_size* candidates while enforcing a
    per-sector cap.

    Cap per sector = ``max(1, floor(sector_cap_pct / 100 * target_size))``.

    Candidates whose ``sector`` is None are each treated as their own unique
    synthetic sector bucket (keyed as ``_nosector_<code>``) so they are never
    capped against each other.  This mirrors the intent of the existing
    ``_sector_increase_ok`` helper in ``scripts/curation_merge.py``, which
    also avoids cross-capping unknown sectors.

    Returns:
        (selected, dropped_for_cap)
    """
    if target_size <= 0:
        return [], list(ranked)

    sector_limit = max(1, math.floor(sector_cap_pct / 100.0 * target_size))
    sector_counts: dict[str, int] = {}
    selected: list[dict] = []
    dropped: list[dict] = []

    for c in ranked:
        if len(selected) >= target_size:
            dropped.append(c)
            continue
        sector = c.get("sector")
        bucket = sector if sector is not None else f"_nosector_{c['code']}"
        count = sector_counts.get(bucket, 0)
        if count < sector_limit:
            selected.append(c)
            sector_counts[bucket] = count + 1
        else:
            dropped.append(c)

    return selected, dropped


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def select_target_universe(
    candidates: list[dict],
    *,
    target_size: int,
    min_warmup_rows: int,
    sector_cap_pct: float,
    min_universe: int,
) -> dict:
    """
    Orchestrate rank + sector cap and return a structured result.

    ``min_universe`` is typically read from
    ``src.config.get_cross_section_config()["min_universe"]`` (default 30) and
    passed in by the calling script so this function stays pure.

    Returns a dict with keys:
        status              "ok" | "insufficient_universe"
        target_size         int
        selected_size       int
        selected            list[candidate]   ([] when insufficient)
        sector_exposure     {sector: count}
        dropped_for_cap     list[candidate]
        filtered_low_warmup list[candidate]
        warnings            list[str]
    """
    warnings: list[str] = []

    ranked, filtered_low_warmup = rank_candidates(
        candidates, min_warmup_rows=min_warmup_rows
    )

    selected, dropped_for_cap = apply_sector_cap(ranked, target_size, sector_cap_pct)

    threshold = min(target_size, min_universe)
    sufficient = len(selected) >= threshold

    sector_exposure: dict[str, int] = {}
    for c in selected:
        sector = c.get("sector") or "(none)"
        sector_exposure[sector] = sector_exposure.get(sector, 0) + 1

    if not sufficient:
        warnings.append(
            f"insufficient_universe: {len(selected)} candidates passed warmup+cap "
            f"(need >= {threshold}: min(target_size={target_size}, "
            f"min_universe={min_universe}))"
        )

    final_selected = selected if sufficient else []
    return {
        "status": "ok" if sufficient else "insufficient_universe",
        "target_size": target_size,
        "selected_size": len(final_selected),
        "selected": final_selected,
        "sector_exposure": sector_exposure if sufficient else {},
        "dropped_for_cap": dropped_for_cap,
        "filtered_low_warmup": filtered_low_warmup,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _to_float(value) -> float | None:
    """Convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
