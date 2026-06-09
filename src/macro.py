"""
Macro / regime features (roadmap §6.2, W4).

Phase 1 adds market-context features (USD/JPY, TOPIX, Nikkei, Nikkei VI, JGB10y
plus a qualitative bias from docs/curation/macro_latest.json) to the per-ticker
model. Three concerns are separated so the pure logic is unit-testable:

  - fetching market series (network; best-effort, missing series are skipped),
  - building a date-indexed macro panel with derived features (pure),
  - joining the panel onto a stock frame with a forward-only as-of merge (pure).

Robustness rule (roadmap §5 risk note): a missing/failed series must never stop
the daily model. add_macro_features always emits the full MACRO_FEATURE_COLS
schema, filling unavailable features with NaN (LightGBM tolerates NaN).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .config import DATA_DIR

MACRO_DIR = DATA_DIR / "macro"
MACRO_PANEL_FILE = MACRO_DIR / "macro_panel.parquet"

# Series we try to fetch. Symbols are configurable so they are not hard-coded
# into the fetch logic (Stooq and yfinance disagree on index/FX tickers).
DEFAULT_MARKET_SERIES = {
    "usdjpy": {"stooq": "usdjpy", "yfinance": "JPY=X"},
    "topix": {"stooq": "^tpx", "yfinance": "^TPX"},
    "nikkei": {"stooq": "^nkx", "yfinance": "^N225"},
    "nikkei_vi": {"stooq": "^nkvix", "yfinance": "^NIVI"},
    "jgb10y": {"stooq": "10jgby.b", "yfinance": None},
}

# Raw level columns kept in the panel (for the macro_snapshots DB row).
MACRO_LEVEL_COLS = ["usdjpy", "topix", "nikkei", "nikkei_vi", "jgb10y"]

# Stable model-feature schema. add_macro_features always emits exactly these.
MACRO_FEATURE_COLS = [
    "macro_usdjpy_ret_20",
    "macro_usdjpy_ret_60",
    "macro_usdjpy_vol_20",
    "macro_topix_ret_20",
    "macro_topix_vol_20",
    "macro_topix_above_200dma",
    "macro_nikkei_ret_20",
    "macro_nikkei_above_200dma",
    "macro_nikkei_vi",
    "macro_jgb10y",
    "macro_bias_score",
]

_BIAS_SCORE = {"risk_on": 1.0, "neutral": 0.0, "risk_off": -1.0,
               "bullish": 1.0, "bearish": -1.0}


def encode_market_bias(value) -> float:
    """Map a qualitative macro bias label to a numeric auxiliary feature."""
    if value is None:
        return 0.0
    return _BIAS_SCORE.get(str(value).strip().lower(), 0.0)


# --- network fetch (best-effort) -------------------------------------------

def fetch_market_series(spec: dict) -> pd.DataFrame | None:
    """
    Fetch one series as a [date, close] frame, trying Stooq then yfinance.
    Returns None on failure (caller treats the series as unavailable).
    """
    from .data_loader import download_stooq_data

    stooq_symbol = spec.get("stooq")
    if stooq_symbol:
        df = download_stooq_data(stooq_symbol)
        if df is not None and not df.empty and "close" in df.columns:
            return df[["date", "close"]].copy()

    yf_symbol = spec.get("yfinance")
    if yf_symbol:
        try:
            import yfinance as yf

            raw = yf.download(yf_symbol, period="max", interval="1d",
                              auto_adjust=False, progress=False, threads=False)
            if raw is not None and not raw.empty:
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = [c[0] if isinstance(c, tuple) else c for c in raw.columns]
                raw = raw.reset_index()
                raw.columns = [str(c).lower() for c in raw.columns]
                close_col = "close" if "close" in raw.columns else "adj close"
                if close_col in raw.columns and "date" in raw.columns:
                    out = raw[["date", close_col]].rename(columns={close_col: "close"})
                    out["date"] = pd.to_datetime(out["date"]).dt.tz_localize(None)
                    return out.dropna()
        except Exception as exc:  # noqa: BLE001
            print(f"macro: yfinance fetch failed for {yf_symbol}: {type(exc).__name__}: {exc}")
    return None


def fetch_all_series(series_config: dict | None = None) -> dict[str, pd.DataFrame]:
    cfg = series_config or DEFAULT_MARKET_SERIES
    out: dict[str, pd.DataFrame] = {}
    for key, spec in cfg.items():
        df = fetch_market_series(spec)
        if df is not None and not df.empty:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
            out[key] = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
            print(f"macro: fetched {key} ({len(out[key])} rows)")
        else:
            print(f"macro: series unavailable, skipping: {key}")
    return out


# --- panel construction (pure) ---------------------------------------------

def _aligned_levels(series_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Outer-join each series' close on date, forward-filled, into one frame."""
    panel = None
    for key, df in series_data.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        col = df[["date", "close"]].copy()
        col["date"] = pd.to_datetime(col["date"]).dt.tz_localize(None)
        col = col.rename(columns={"close": key}).sort_values("date")
        panel = col if panel is None else panel.merge(col, on="date", how="outer")
    if panel is None:
        return pd.DataFrame(columns=["date"])
    panel = panel.sort_values("date").reset_index(drop=True)
    for key in series_data:
        if key in panel.columns:
            panel[key] = panel[key].ffill()
    return panel


def build_macro_panel(series_data: dict[str, pd.DataFrame],
                      qualitative: dict | None = None) -> pd.DataFrame:
    """
    Build a date-indexed panel with raw levels + derived MACRO_FEATURE_COLS.
    Missing series leave their derived columns as NaN. Pure (no network).
    """
    panel = _aligned_levels(series_data or {})
    if panel.empty:
        return pd.DataFrame(columns=["date"] + MACRO_LEVEL_COLS + MACRO_FEATURE_COLS)

    # Guarantee level columns exist for the snapshot row.
    for col in MACRO_LEVEL_COLS:
        if col not in panel.columns:
            panel[col] = np.nan

    def _ret(series, n):
        return series / series.shift(n) - 1.0

    def _vol(series, n):
        return series.pct_change(fill_method=None).rolling(n, min_periods=max(2, n // 2)).std()

    def _above_200(series):
        ma = series.rolling(200, min_periods=100).mean()
        return (series > ma).astype("float64").where(ma.notna())

    panel["macro_usdjpy_ret_20"] = _ret(panel["usdjpy"], 20)
    panel["macro_usdjpy_ret_60"] = _ret(panel["usdjpy"], 60)
    panel["macro_usdjpy_vol_20"] = _vol(panel["usdjpy"], 20)
    panel["macro_topix_ret_20"] = _ret(panel["topix"], 20)
    panel["macro_topix_vol_20"] = _vol(panel["topix"], 20)
    panel["macro_topix_above_200dma"] = _above_200(panel["topix"])
    panel["macro_nikkei_ret_20"] = _ret(panel["nikkei"], 20)
    panel["macro_nikkei_above_200dma"] = _above_200(panel["nikkei"])
    panel["macro_nikkei_vi"] = panel["nikkei_vi"]
    panel["macro_jgb10y"] = panel["jgb10y"]

    bias = encode_market_bias((qualitative or {}).get("market_bias"))
    panel["macro_bias_score"] = bias

    cols = ["date"] + MACRO_LEVEL_COLS + MACRO_FEATURE_COLS
    return panel[[c for c in cols if c in panel.columns]].reset_index(drop=True)


def add_macro_features(stock_df: pd.DataFrame, macro_panel: pd.DataFrame | None,
                       ticker_info: dict | None = None) -> pd.DataFrame:
    """
    Join macro features onto a stock frame with a backward as-of merge so each
    stock date only sees macro data from on/before that date (no future leak).

    Always emits the full MACRO_FEATURE_COLS schema; unavailable features are
    NaN. ticker_info is accepted for forward-compatibility (sector-relative
    momentum is Phase 2) and currently unused.
    """
    out = stock_df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.tz_localize(None)
    out = out.sort_values("date").reset_index(drop=True)

    if macro_panel is None or macro_panel.empty or "date" not in macro_panel.columns:
        for col in MACRO_FEATURE_COLS:
            out[col] = np.nan
        return out

    feature_cols = [c for c in MACRO_FEATURE_COLS if c in macro_panel.columns]
    right = macro_panel[["date"] + feature_cols].copy()
    right["date"] = pd.to_datetime(right["date"]).dt.tz_localize(None)
    right = right.sort_values("date").reset_index(drop=True)

    merged = pd.merge_asof(out, right, on="date", direction="backward")

    # Ensure every macro feature column exists (NaN if the series was missing).
    for col in MACRO_FEATURE_COLS:
        if col not in merged.columns:
            merged[col] = np.nan
    return merged


def latest_snapshot_row(panel: pd.DataFrame, qualitative: dict | None = None) -> dict | None:
    """Extract the most recent row as a macro_snapshots DB payload, or None."""
    if panel is None or panel.empty:
        return None
    row = panel.sort_values("date").iloc[-1]

    def _num(col):
        if col not in panel.columns:
            return None
        val = row.get(col)
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    qualitative = qualitative or {}
    return {
        "date": pd.to_datetime(row["date"]).strftime("%Y-%m-%d"),
        "usdjpy": _num("usdjpy"),
        "topix": _num("topix"),
        "nikkei": _num("nikkei"),
        "nikkei_vi": _num("nikkei_vi"),
        "jgb10y": _num("jgb10y"),
        "market_bias": qualitative.get("market_bias"),
        "regime": (qualitative.get("regime") if isinstance(qualitative.get("regime"), str)
                   else None),
    }


# --- panel parquet I/O ------------------------------------------------------

def save_macro_panel(panel: pd.DataFrame, path: str | Path | None = None) -> str:
    out_path = Path(path or MACRO_PANEL_FILE)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(out_path)
    return str(out_path)


def load_macro_panel(path: str | Path | None = None) -> pd.DataFrame | None:
    """Load the cached macro panel, or None when it is absent/unreadable."""
    in_path = Path(path or MACRO_PANEL_FILE)
    if not in_path.exists():
        return None
    try:
        panel = pd.read_parquet(in_path)
    except Exception:  # noqa: BLE001
        return None
    if "date" in panel.columns:
        panel["date"] = pd.to_datetime(panel["date"]).dt.tz_localize(None)
    return panel
