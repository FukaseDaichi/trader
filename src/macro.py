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
#
# Source reality as of 2026-07-19 (issue #1): Stooq's CSV endpoint returns 404
# for EVERY symbol (also from GitHub Actions), so each series lives or dies by
# its yfinance fallback. The Stooq symbols are kept as a dormant primary in
# case the endpoint revives.
#   - topix: the index itself has no daily history on Yahoo (^TPX is an empty
#     stub), so TOPIX ETF 1305 serves as the benchmark proxy. 1306 was retired
#     here because Yahoo retains unadjusted 10:1 discontinuities even with
#     auto_adjust=True. Only returns/shape-based features consume this proxy.
#     The topix entry also opts into `open` levels: the Phase 2 backtest
#     compares strategy returns on a next-session-open to horizon-session-close
#     basis, which a close-only series cannot express. No other series needs it.
#   - nikkei_vi / jgb10y: disabled — no Yahoo listing exists and the Stooq
#     symbols are unverifiable while the endpoint is down (JGB 10y candidate:
#     `10jpy.b`). Their levels/features stay NaN per the robustness rule;
#     re-enable only with a source verified end-to-end.
DEFAULT_MARKET_SERIES = {
    "usdjpy": {"stooq": "usdjpy", "yfinance": "JPY=X"},
    "topix": {"stooq": "1305.jp", "yfinance": "1305.T", "open": True},
    "nikkei": {"stooq": "^nkx", "yfinance": "^N225"},
    "nikkei_vi": {"stooq": None, "yfinance": None},
    "jgb10y": {"stooq": None, "yfinance": None},
}

# Raw level columns kept in the panel (for the macro_snapshots DB row).
MACRO_LEVEL_COLS = ["usdjpy", "topix", "nikkei", "nikkei_vi", "jgb10y"]

# Benchmark-only level columns. These are NOT model features and NOT part of
# the macro_snapshots DB row; they exist so the Phase 2 backtest can measure a
# same-basis benchmark (next-session open -> horizon-session close).
MACRO_AUX_LEVEL_COLS = ["topix_open"]

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

_BIAS_SCORE = {
    "risk_on": 1.0,
    "neutral": 0.0,
    "risk_off": -1.0,
    "bullish": 1.0,
    "bearish": -1.0,
}

# Macro inputs are broad-market indices/proxies and FX, not individual stocks.
# A one-session move above 50% is therefore a data/corporate-action discontinuity
# for the configured series. Reject the whole provider result instead of letting
# one bad level poison rolling returns, portfolio benchmarks, and retraining.
_MAX_MARKET_DAILY_MOVE = 0.50


def encode_market_bias(value) -> float:
    """Map a qualitative macro bias label to a numeric auxiliary feature."""
    if value is None:
        return 0.0
    return _BIAS_SCORE.get(str(value).strip().lower(), 0.0)


# --- network fetch (best-effort) -------------------------------------------


def _extreme_move(values: pd.Series) -> tuple[int, float] | None:
    """First index whose day-over-day move breaches the discontinuity limit.

    Returns ``(index, return)`` or ``None`` when the series is continuous.
    NaN gaps propagate as NaN returns and never trigger a false positive.
    """
    returns = values.pct_change(fill_method=None)
    extreme = returns.abs() > _MAX_MARKET_DAILY_MOVE
    if not extreme.any():
        return None
    idx = int(np.flatnonzero(extreme.to_numpy())[0])
    return idx, float(returns.iloc[idx])


def _open_rejection_reason(frame: pd.DataFrame) -> str | None:
    """Why this frame's ``open`` cannot be trusted, or None when it is sound.

    Three failure modes, each of which would otherwise corrupt the same-basis
    benchmark silently: unusable levels, a split-scale jump inside the open
    series, and an open carried on a different adjustment basis than the close.
    The last one leaves both series internally continuous, so only the intraday
    open-to-close ratio exposes it.

    A NaN on some dates is not a failure: those dates simply have no benchmark
    and the consumer reports incomplete coverage instead of inventing a return.
    """
    opens = frame["open"]
    present = opens.notna()
    if not present.any():
        return "no open levels supplied"
    if not np.isfinite(opens[present]).all() or (opens[present] <= 0).any():
        return "non-finite or non-positive open levels"

    bad = _extreme_move(opens)
    if bad is not None:
        idx, ret = bad
        date = frame["date"].iloc[idx].strftime("%Y-%m-%d")
        return (
            f"daily open move {ret:.1%} on {date} exceeds {_MAX_MARKET_DAILY_MOVE:.0%}"
        )

    intraday = frame["close"] / opens - 1.0
    extreme = intraday.abs() > _MAX_MARKET_DAILY_MOVE
    if extreme.any():
        idx = int(np.flatnonzero(extreme.to_numpy())[0])
        date = frame["date"].iloc[idx].strftime("%Y-%m-%d")
        return (
            f"open/close basis mismatch: intraday move "
            f"{float(intraday.iloc[idx]):.1%} on {date} exceeds "
            f"{_MAX_MARKET_DAILY_MOVE:.0%}"
        )
    return None


def _validated_market_frame(
    df: pd.DataFrame | None,
    *,
    source: str,
    symbol: str,
    want_open: bool = False,
) -> pd.DataFrame | None:
    """Normalize and reject split-scale discontinuities in a market series.

    Returns ``[date, close]``, plus an ``open`` column when the caller asked for
    one and the provider supplied one that passes the same integrity checks.
    A close-side failure rejects the whole series as before. An open-side
    failure drops only the open: the close and every macro feature built from
    it must survive so the daily run never breaks.
    """
    if df is None or df.empty or not {"date", "close"}.issubset(df.columns):
        return None

    cols = ["date", "close"]
    has_open = bool(want_open) and "open" in df.columns
    if has_open:
        cols.append("open")

    out = df[cols].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.tz_localize(None)
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    if has_open:
        out["open"] = pd.to_numeric(out["open"], errors="coerce")
    out = (
        out.dropna(subset=["date", "close"])
        .sort_values("date")
        .drop_duplicates("date", keep="last")
        .reset_index(drop=True)
    )
    if out.empty or not np.isfinite(out["close"]).all() or (out["close"] <= 0).any():
        print(f"macro: rejected invalid {source} levels for {symbol}")
        return None

    bad = _extreme_move(out["close"])
    if bad is not None:
        bad_idx, bad_return = bad
        bad_date = out["date"].iloc[bad_idx].strftime("%Y-%m-%d")
        print(
            f"macro: rejected {source} series for {symbol}: "
            f"daily move {bad_return:.1%} on {bad_date} exceeds "
            f"{_MAX_MARKET_DAILY_MOVE:.0%}"
        )
        return None

    if has_open:
        reason = _open_rejection_reason(out)
        if reason is not None:
            print(
                f"macro: dropped {source} open for {symbol}: {reason} (close retained)"
            )
            out = out.drop(columns=["open"])

    return out


def fetch_market_series(spec: dict) -> pd.DataFrame | None:
    """
    Fetch one series as a [date, close] frame, trying Stooq then yfinance.
    A spec with ``open: True`` also carries an ``open`` column when the provider
    supplies a trustworthy one (the same-basis benchmark needs it).
    Returns None on failure (caller treats the series as unavailable).
    """
    from .data_loader import download_stooq_data

    want_open = bool(spec.get("open"))

    stooq_symbol = spec.get("stooq")
    if stooq_symbol:
        df = download_stooq_data(stooq_symbol)
        if df is not None and not df.empty and "close" in df.columns:
            out = _validated_market_frame(
                df,
                source="Stooq",
                symbol=str(stooq_symbol),
                want_open=want_open,
            )
            if out is not None:
                return out

    yf_symbol = spec.get("yfinance")
    if yf_symbol:
        # period="max" breaks yfinance's range resolution for some symbols,
        # so retry once with a
        # bounded period — 10y of daily closes covers every macro feature.
        for period in ("max", "10y"):
            try:
                import yfinance as yf

                raw = yf.download(
                    yf_symbol,
                    period=period,
                    interval="1d",
                    # Macro levels/returns must be continuous across ETF splits.
                    # With current yfinance this makes Close the adjusted close.
                    auto_adjust=True,
                    progress=False,
                    threads=False,
                )
                if raw is None or raw.empty:
                    continue
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = [
                        c[0] if isinstance(c, tuple) else c for c in raw.columns
                    ]
                raw = raw.reset_index()
                raw.columns = [str(c).lower() for c in raw.columns]
                # Some older yfinance versions and test doubles still return an
                # explicit Adj Close even with auto_adjust=True. Prefer it when
                # present so compatibility never regresses to a raw close.
                # That path can pair an adjusted close with a raw open; the
                # intraday basis check in _open_rejection_reason catches it.
                close_col = "adj close" if "adj close" in raw.columns else "close"
                if close_col in raw.columns and "date" in raw.columns:
                    take = ["date", close_col]
                    if want_open and "open" in raw.columns:
                        take.append("open")
                    out = raw[take].rename(columns={close_col: "close"})
                    out = _validated_market_frame(
                        out,
                        source="yfinance",
                        symbol=str(yf_symbol),
                        want_open=want_open,
                    )
                    if out is not None:
                        return out
                    # An extreme/invalid frame is a provider-integrity failure,
                    # not the empty-response range bug handled by the 10y retry.
                    return None
            except Exception as exc:  # noqa: BLE001
                print(
                    f"macro: yfinance fetch failed for {yf_symbol} "
                    f"(period={period}): {type(exc).__name__}: {exc}"
                )
    return None


def fetch_all_series(series_config: dict | None = None) -> dict[str, pd.DataFrame]:
    cfg = series_config or DEFAULT_MARKET_SERIES
    out: dict[str, pd.DataFrame] = {}
    for key, spec in cfg.items():
        df = fetch_market_series(spec)
        if df is not None and not df.empty:
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
            out[key] = (
                df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
            )
            print(f"macro: fetched {key} ({len(out[key])} rows)")
        else:
            print(f"macro: series unavailable, skipping: {key}")
    return out


# --- panel construction (pure) ---------------------------------------------


def _aligned_levels(series_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Outer-join each series' close on date, forward-filled, into one frame.

    A series carrying an ``open`` also contributes ``<key>_open``. Only closes
    are forward-filled. A forward-filled open would pair yesterday's open with
    yesterday's close on a date the instrument never traded and manufacture a
    benchmark return that passes every downstream check, so opens stay NaN
    off-session and exact-date consumers exclude those dates.
    """
    panel = None
    close_cols: list[str] = []
    for key, df in series_data.items():
        if df is None or df.empty or "close" not in df.columns:
            continue
        take = ["date", "close"]
        renames = {"close": key}
        if "open" in df.columns:
            take.append("open")
            renames["open"] = f"{key}_open"
        col = df[take].copy()
        col["date"] = pd.to_datetime(col["date"]).dt.tz_localize(None)
        col = col.rename(columns=renames).sort_values("date")
        panel = col if panel is None else panel.merge(col, on="date", how="outer")
        close_cols.append(key)
    if panel is None:
        return pd.DataFrame(columns=["date"])
    panel = panel.sort_values("date").reset_index(drop=True)
    for key in close_cols:
        if key in panel.columns:
            panel[key] = panel[key].ffill()
    return panel


def build_macro_panel(
    series_data: dict[str, pd.DataFrame], qualitative: dict | None = None
) -> pd.DataFrame:
    """
    Build a date-indexed panel with raw levels + derived MACRO_FEATURE_COLS.
    Missing series leave their derived columns as NaN. Pure (no network).
    """
    panel = _aligned_levels(series_data or {})
    if panel.empty:
        return pd.DataFrame(
            columns=(
                ["date"]
                + MACRO_LEVEL_COLS
                + MACRO_AUX_LEVEL_COLS
                + MACRO_FEATURE_COLS
            )
        )

    # Guarantee level columns exist for the snapshot row, and the auxiliary
    # benchmark levels so the panel schema never depends on availability.
    for col in MACRO_LEVEL_COLS + MACRO_AUX_LEVEL_COLS:
        if col not in panel.columns:
            panel[col] = np.nan

    def _ret(series, n):
        return series / series.shift(n) - 1.0

    def _vol(series, n):
        return (
            series.pct_change(fill_method=None)
            .rolling(n, min_periods=max(2, n // 2))
            .std()
        )

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

    cols = (
        ["date"] + MACRO_LEVEL_COLS + MACRO_AUX_LEVEL_COLS + MACRO_FEATURE_COLS
    )
    return panel[[c for c in cols if c in panel.columns]].reset_index(drop=True)


def add_macro_features(
    stock_df: pd.DataFrame,
    macro_panel: pd.DataFrame | None,
    ticker_info: dict | None = None,
) -> pd.DataFrame:
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


def latest_snapshot_row(
    panel: pd.DataFrame, qualitative: dict | None = None
) -> dict | None:
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
        "regime": (
            qualitative.get("regime")
            if isinstance(qualitative.get("regime"), str)
            else None
        ),
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
