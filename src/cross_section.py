"""
Phase 2 cross-sectional panel builder (roadmap §7).

Assembles a long panel (one row per date × ticker) with:
  - per-ticker technical + macro features (from src.model.build_feature_frame),
  - within-date z-score and percentile-rank cross-sectional features,
  - within-(date, sector) relative-rank features,
  - liquidity features (turnover, adv20),
  - forward-return labels (fwd_return, target_vol_norm, target_up,
    target_rank_bucket).

Design constraints
------------------
- NO future leakage: all cross-sectional normalizations use only same-date rows
  (groupby("date").transform). Labels use per-ticker shift(-H), which only peeks
  H rows ahead within that ticker's time series — no cross-ticker contamination.
- Robustness: missing macro / sector / liquidity never raise; they leave NaN
  columns that LightGBM tolerates.
- Deterministic: no random state in pure functions.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import get_cross_section_config
from .macro import MACRO_FEATURE_COLS
from .model import build_feature_frame

# ---------------------------------------------------------------------------
# Module-level column lists (stable contract — Tasks 4/5 depend on these)
# ---------------------------------------------------------------------------

# Raw per-ticker features that get cross-sectionally normalised within each date.
CS_BASE_FEATURES = [
    "return_5d",
    "return_20d",
    "rsi",
    "macd_hist",
    "atr_pct",
    "vol_ratio",
    "adv20",
]

# Features that also get a within-date, within-SECTOR relative rank.
SECTOR_REL_FEATURES = ["return_20d", "return_5d"]


# ---------------------------------------------------------------------------
# 1. build_ticker_feature_frame
# ---------------------------------------------------------------------------


def build_ticker_feature_frame(
    df: pd.DataFrame,
    ticker_info: dict,
    macro_panel: pd.DataFrame | None = None,
    macro_enabled: bool = True,
) -> pd.DataFrame:
    """
    Build a per-ticker feature frame.

    Calls model.build_feature_frame for technical + macro features, then
    enriches with:
      - ``ticker`` column (from ticker_info["code"])
      - ``sector`` column (from ticker_info.get("sector"), may be None)
      - ``turnover = close * volume``
      - ``adv20 = turnover.rolling(20, min_periods=5).mean()``

    Returns an empty DataFrame when the underlying frame is empty.
    """
    frame = build_feature_frame(
        df,
        macro_panel=macro_panel,
        ticker_info=ticker_info,
        dropna_features=True,
        macro_enabled=macro_enabled,
    )
    if frame is None or frame.empty:
        return pd.DataFrame()

    frame = frame.copy()
    frame["ticker"] = ticker_info["code"]
    frame["sector"] = ticker_info.get("sector")  # may be None

    # Liquidity features computed from raw OHLCV columns (always present after
    # add_features because add_features keeps original columns).
    if "close" in frame.columns and "volume" in frame.columns:
        turnover = frame["close"] * frame["volume"]
        frame["turnover"] = turnover
        frame["adv20"] = turnover.rolling(20, min_periods=5).mean()
    else:
        frame["turnover"] = np.nan
        frame["adv20"] = np.nan

    return frame


# ---------------------------------------------------------------------------
# 2. build_panel
# ---------------------------------------------------------------------------


def build_panel(
    tickers_data: list[tuple[dict, pd.DataFrame]],
    macro_panel: pd.DataFrame | None = None,
    macro_enabled: bool = True,
) -> pd.DataFrame:
    """
    Build a stacked LONG panel from a list of (ticker_info, ohlcv_df) tuples.

    Each entry is processed by build_ticker_feature_frame; empty results are
    skipped. The resulting rows are concatenated and sorted by ["date", "ticker"].

    Returns an empty DataFrame when no ticker yields usable data.
    """
    frames: list[pd.DataFrame] = []
    for ticker_info, ohlcv_df in tickers_data:
        try:
            f = build_ticker_feature_frame(
                ohlcv_df,
                ticker_info=ticker_info,
                macro_panel=macro_panel,
                macro_enabled=macro_enabled,
            )
        except Exception:  # noqa: BLE001 — robustness: skip broken tickers
            continue
        if f is not None and not f.empty:
            frames.append(f)

    if not frames:
        return pd.DataFrame()

    panel = pd.concat(frames, ignore_index=True)
    panel = panel.sort_values(["date", "ticker"]).reset_index(drop=True)
    return panel


# ---------------------------------------------------------------------------
# 3. add_cross_sectional_features
# ---------------------------------------------------------------------------


def add_cross_sectional_features(panel: pd.DataFrame) -> pd.DataFrame:
    """
    Add within-date z-score and percentile-rank columns for CS_BASE_FEATURES.

    For each feature ``f`` present in the panel:
      - ``cs_z_<f>``    : within-date z-score (NaN-aware, ddof=0).
                          When std == 0 or NaN: finite x -> 0.0, NaN x -> NaN.
      - ``cs_rank_<f>`` : within-date percentile rank in (0, 1] via
                          rank(pct=True, method="average"). NaN stays NaN.

    Uses only same-date rows — NO future leakage.
    """
    panel = panel.copy()

    for f in CS_BASE_FEATURES:
        if f not in panel.columns:
            continue

        g = panel.groupby("date")[f]

        # --- z-score (NaN-aware, population std ddof=0) ---
        mean = g.transform("mean")
        std_pop = g.transform(lambda s: s.std(ddof=0))

        col_z = f"cs_z_{f}"
        raw = panel[f]
        denom = std_pop.where(std_pop > 0)  # NaN when std==0 or NaN
        z = (raw - mean) / denom
        # Where x is finite but denom is NaN -> z becomes NaN; replace with 0.0
        finite_x = raw.notna() & np.isfinite(raw.values.astype(float))
        bad_denom = denom.isna()
        z = z.where(~(finite_x & bad_denom), other=0.0)
        panel[col_z] = z

        # --- percentile rank ---
        col_r = f"cs_rank_{f}"
        panel[col_r] = g.rank(pct=True, method="average")

    return panel


# ---------------------------------------------------------------------------
# 4. add_sector_features
# ---------------------------------------------------------------------------


def add_sector_features(panel: pd.DataFrame) -> pd.DataFrame:
    """
    Add within-(date, sector) percentile-rank columns for SECTOR_REL_FEATURES.

    ``sect_rank_<f>`` = within (date, sector) pct rank.

    Rows with sector None form their own group via a ``"__NA__"`` sentinel,
    so they are ranked among themselves rather than silently dropped.
    """
    panel = panel.copy()

    # Fill sentinel for None/NaN sectors so they are not dropped by groupby.
    sector_col = panel["sector"].fillna("__NA__")

    for f in SECTOR_REL_FEATURES:
        if f not in panel.columns:
            continue
        col_r = f"sect_rank_{f}"
        panel[col_r] = (
            panel.groupby([panel["date"], sector_col])[f]
            .rank(pct=True, method="average")
            .values  # align back by position (same order)
        )

    return panel


# ---------------------------------------------------------------------------
# 5. add_liquidity_features
# ---------------------------------------------------------------------------


def add_liquidity_features(panel: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure liquidity cross-sectional columns exist on the panel.

    ``adv20`` is already in CS_BASE_FEATURES so ``cs_z_adv20`` and
    ``cs_rank_adv20`` are created by add_cross_sectional_features; this
    function is idempotent: only adds them if missing.

    Additionally adds ``cs_rank_turnover`` (within-date pct rank of turnover).
    """
    panel = panel.copy()

    # Idempotent: adv20 cross-sectional columns (added by add_cross_sectional_features).
    for col in ("cs_z_adv20", "cs_rank_adv20"):
        if col not in panel.columns:
            # Fallback: add from raw adv20 if present.
            if "adv20" in panel.columns:
                g = panel.groupby("date")["adv20"]
                if col == "cs_z_adv20":
                    mean = g.transform("mean")
                    std_pop = g.transform(lambda s: s.std(ddof=0))
                    denom = std_pop.where(std_pop > 0)
                    raw = panel["adv20"]
                    z = (raw - mean) / denom
                    finite_x = raw.notna() & np.isfinite(raw.values.astype(float))
                    bad_denom = denom.isna()
                    z = z.where(~(finite_x & bad_denom), other=0.0)
                    panel[col] = z
                else:
                    panel[col] = g.rank(pct=True, method="average")
            else:
                panel[col] = np.nan

    # Within-date percentile rank of raw turnover.
    if "cs_rank_turnover" not in panel.columns:
        if "turnover" in panel.columns:
            panel["cs_rank_turnover"] = panel.groupby("date")["turnover"].rank(
                pct=True, method="average"
            )
        else:
            panel["cs_rank_turnover"] = np.nan

    return panel


# ---------------------------------------------------------------------------
# 6. build_cs_labels
# ---------------------------------------------------------------------------


def build_cs_labels(
    panel: pd.DataFrame,
    label_config: dict | None = None,
) -> pd.DataFrame:
    """
    Add forward-return labels to the panel.

    Labels (H = label_horizon_days from config or label_config):
      - ``fwd_return``        : H-day forward return, computed PER TICKER (no
                                cross-ticker bleed). NaN for the last H rows of
                                each ticker.
      - ``target_vol_norm``   : fwd_return / volatility (NaN where vol <= 0).
      - ``target_up``         : 1.0 if fwd_return > 0 else 0.0; NaN where
                                fwd_return is NaN.
      - ``target_rank_bucket``: within-date integer 0..4 relevance grade
                                (higher = better forward return). Computed
                                deterministically via pct rank + floor*5.
                                NaN where fwd_return is NaN.

    Does NOT drop NaN-label rows; the caller decides.
    """
    cfg = get_cross_section_config()
    if label_config is not None:
        h = int(
            label_config.get(
                "label_horizon_days",
                label_config.get("horizon_days", cfg["label_horizon_days"]),
            )
        )
    else:
        h = int(cfg["label_horizon_days"])
    h = max(1, h)

    panel = panel.copy()

    # Per-ticker forward return: sort by date within ticker, shift(-h).
    # This is the ONLY place we look forward; it's within a ticker's own series.
    panel = panel.sort_values(["ticker", "date"]).reset_index(drop=True)

    def _fwd(grp):
        close = grp["close"]
        return close.shift(-h) / close - 1.0

    panel["fwd_return"] = panel.groupby("ticker", group_keys=False).apply(
        _fwd, include_groups=False
    )

    # target_vol_norm
    if "volatility" in panel.columns:
        vol = pd.to_numeric(panel["volatility"], errors="coerce")
        safe_vol = vol.where(vol > 0)
        panel["target_vol_norm"] = panel["fwd_return"] / safe_vol
    else:
        panel["target_vol_norm"] = np.nan

    # target_up
    fwd = panel["fwd_return"]
    target_up = pd.Series(np.nan, index=panel.index, dtype="float64")
    known = fwd.notna()
    target_up[known] = (fwd[known] > 0).astype("float64")
    panel["target_up"] = target_up

    # target_rank_bucket: within-date 0..4 grade, NaN where fwd_return is NaN.
    # Deterministic: rank(pct=True, method="first") -> floor * 5, clip [0,4].
    bucket = pd.Series(np.nan, index=panel.index, dtype="float64")

    def _bucket_for_date(grp):
        fwd_col = grp["fwd_return"]
        valid = fwd_col.notna()
        if valid.any():
            rp = fwd_col[valid].rank(pct=True, method="first")
            b = (rp * 5).apply(math.floor).clip(0, 4).astype("float64")
            return b
        return pd.Series(dtype="float64")

    for date_val, grp in panel.groupby("date"):
        b = _bucket_for_date(grp)
        bucket.loc[b.index] = b

    panel["target_rank_bucket"] = bucket

    # Re-sort to natural panel order.
    panel = panel.sort_values(["date", "ticker"]).reset_index(drop=True)
    return panel


# ---------------------------------------------------------------------------
# 7. cross_sectional_feature_cols
# ---------------------------------------------------------------------------


def cross_sectional_feature_cols(macro_enabled: bool = True) -> list[str]:
    """
    Stable ordered list of cross-sectional model feature columns.

    Includes:
      - cs_z_<f> and cs_rank_<f> for each f in CS_BASE_FEATURES
      - sect_rank_<f> for each f in SECTOR_REL_FEATURES
      - cs_rank_turnover
      - (when macro_enabled) MACRO_FEATURE_COLS (raw, not within-date normalised)
    """
    cols: list[str] = []
    for f in CS_BASE_FEATURES:
        cols.append(f"cs_z_{f}")
        cols.append(f"cs_rank_{f}")
    for f in SECTOR_REL_FEATURES:
        cols.append(f"sect_rank_{f}")
    cols.append("cs_rank_turnover")
    if macro_enabled:
        cols.extend(MACRO_FEATURE_COLS)
    return cols


# ---------------------------------------------------------------------------
# 8. drop_small_date_groups
# ---------------------------------------------------------------------------


def drop_small_date_groups(
    panel: pd.DataFrame,
    min_names: int | None = None,
) -> pd.DataFrame:
    """
    Drop all rows whose date has fewer than ``min_names`` tickers.

    Defaults to get_cross_section_config()["min_daily_names"].
    Used to discard training dates with too thin a cross-section.
    """
    if min_names is None:
        min_names = get_cross_section_config()["min_daily_names"]
    min_names = max(1, int(min_names))

    counts = panel.groupby("date")["ticker"].transform("count")
    return panel[counts >= min_names].reset_index(drop=True)


# ---------------------------------------------------------------------------
# 9. build_cs_panel  (convenience pipeline)
# ---------------------------------------------------------------------------


def build_cs_panel(
    tickers_data: list[tuple[dict, pd.DataFrame]],
    macro_panel: pd.DataFrame | None = None,
    *,
    macro_enabled: bool = True,
    with_labels: bool = True,
    label_config: dict | None = None,
) -> pd.DataFrame:
    """
    Full cross-sectional panel pipeline.

    Steps:
      1. build_panel
      2. add_cross_sectional_features
      3. add_sector_features
      4. add_liquidity_features
      5. (if with_labels) build_cs_labels

    Does NOT drop small-group dates or NaN labels — caller decides.
    Suitable for both training (with_labels=True) and daily inference
    (with_labels=False, then take the latest date).
    """
    panel = build_panel(
        tickers_data, macro_panel=macro_panel, macro_enabled=macro_enabled
    )
    if panel.empty:
        return panel

    panel = add_cross_sectional_features(panel)
    panel = add_sector_features(panel)
    panel = add_liquidity_features(panel)
    if with_labels:
        panel = build_cs_labels(panel, label_config=label_config)

    return panel
