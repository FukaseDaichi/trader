#!/usr/bin/env python3
"""
Technical screening for AI ticker curation (daily).

Reads local price parquet for the candidate pool + current enabled + watchlist,
computes technical indicators (reusing src.model.add_features), and writes:

  - docs/curation/technical_features.json   raw numbers (input for the agent)
  - docs/curation/technical_latest.json     deterministic baseline scores
  - docs/curation/technical_<DATE>.json     dated copy

The deterministic score keeps the system functional even without the LLM step.
The technical agent (skill jp-stock-technical-screen) may refine
technical_latest.json with judgment + rationale, preserving the schema.

See specification_document/ai_ticker_curation/01_agent_design.md (§2).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from curation_common import (
    CURATION_DIR,
    DATA_DIR,
    WATCHLIST_DIR,
    enabled_codes,
    get_curation_settings,
    load_pool,
    load_tickers_config,
    now_jst_iso,
    today_jst_iso,
    write_json,
)

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.model import add_features  # noqa: E402


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _scale(x: float, lo: float, hi: float) -> float:
    if hi == lo:
        return 0.0
    return _clamp((x - lo) / (hi - lo))


def _load_price(code: str) -> pd.DataFrame | None:
    """Prefer top-level data/, fall back to data/watchlist/."""
    for path in (DATA_DIR / f"{code}.parquet", WATCHLIST_DIR / f"{code}.parquet"):
        if path.exists():
            try:
                df = pd.read_parquet(path)
            except Exception:
                continue
            if df is None or df.empty or "date" not in df.columns:
                continue
            df = df.copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
            if not df.empty:
                return df
    return None


def _safe(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(f) or np.isinf(f):
        return None
    return f


def compute_entry(code: str, name: str, sector: str | None) -> dict:
    """Compute raw technical features for one ticker. Always returns a dict."""
    df = _load_price(code)
    rows = 0 if df is None else int(len(df))
    entry = {
        "code": code,
        "name": name,
        "sector": sector,
        "rows": rows,
    }
    if df is None or rows < 60:
        entry["insufficient_data"] = True
        return entry

    feat = add_features(df, dropna=False)
    last = feat.iloc[-1]

    close = _safe(last.get("close"))
    ma_200 = _safe(df["close"].rolling(200).mean().iloc[-1]) if rows >= 200 else None
    high_60d = _safe(df["high"].rolling(60).max().iloc[-1]) if rows >= 60 else None
    ret_60d = _safe(df["close"].pct_change(60).iloc[-1]) if rows >= 61 else None

    entry.update(
        {
            "data_through": df["date"].iloc[-1].strftime("%Y-%m-%d"),
            "close": close,
            "ret_5d": _safe(last.get("return_5d")),
            "ret_20d": _safe(last.get("return_20d")),
            "ret_60d": ret_60d,
            "ma_5": _safe(last.get("ma_5")),
            "ma_20": _safe(last.get("ma_20")),
            "ma_60": _safe(last.get("ma_60")),
            "ma_200": ma_200,
            "div_ma_60": _safe(last.get("div_ma_60")),
            "rsi14": _safe(last.get("rsi")),
            "macd_hist": _safe(last.get("macd_hist")),
            "macd_hist_change": _safe(last.get("macd_hist_change")),
            "atr_pct": _safe(last.get("atr_pct")),
            "vol_ratio": _safe(last.get("vol_ratio")),
            "high_20d": _safe(last.get("high_20d")),
            "high_60d": high_60d,
            "price_position_20d": _safe(last.get("price_position_20d")),
        }
    )
    return entry


def score_entry(e: dict) -> tuple[float, dict]:
    """
    Transparent 0-100 technical score from sub-signals (weights sum to 100).
    Returns (score, signals_summary).
    """
    if e.get("insufficient_data"):
        return 0.0, {"trend": "n/a"}

    ma5, ma20, ma60 = e.get("ma_5"), e.get("ma_20"), e.get("ma_60")
    close = e.get("close")

    # 1. Trend / MA stack (25)
    trend_pts = 0.0
    ma_stack = "mixed"
    if None not in (ma5, ma20, ma60):
        if ma5 > ma20 > ma60:
            trend_pts, ma_stack = 25.0, "MA5>MA20>MA60"
        elif ma5 > ma20 and ma20 <= ma60:
            trend_pts, ma_stack = 16.0, "MA5>MA20"
        elif ma5 <= ma20 <= ma60:
            trend_pts, ma_stack = 4.0, "downtrend"
        else:
            trend_pts, ma_stack = 10.0, "mixed"
    ma200 = e.get("ma_200")
    if ma200 is not None and close is not None and close > ma200:
        trend_pts = min(25.0, trend_pts + 3.0)

    # 2. Medium momentum (20): map ret_20d in [-10%, +15%]
    ret20 = e.get("ret_20d")
    mom20_pts = 20.0 * _scale(ret20, -0.10, 0.15) if ret20 is not None else 8.0

    # 3. Short momentum (10): ret_5d in [-5%, +7%]
    ret5 = e.get("ret_5d")
    mom5_pts = 10.0 * _scale(ret5, -0.05, 0.07) if ret5 is not None else 4.0

    # 4. RSI health (15): reward 50-65, taper above, penalize extremes
    rsi = e.get("rsi14")
    if rsi is None:
        rsi_pts = 6.0
    elif rsi >= 80:
        rsi_pts = 4.0           # overbought
    elif rsi >= 70:
        rsi_pts = 10.0
    elif rsi >= 55:
        rsi_pts = 15.0
    elif rsi >= 50:
        rsi_pts = 13.0
    elif rsi >= 45:
        rsi_pts = 9.0
    elif rsi >= 40:
        rsi_pts = 6.0
    else:
        rsi_pts = 3.0

    # 5. MACD (10): histogram > 0 and rising
    mh = e.get("macd_hist")
    mhc = e.get("macd_hist_change")
    macd_pts = 0.0
    macd_state = "bear"
    if mh is not None:
        if mh > 0:
            macd_pts, macd_state = 7.0, "bull"
            if mhc is not None and mhc > 0:
                macd_pts = 10.0
        else:
            macd_pts = 3.0 if (mhc is not None and mhc > 0) else 0.0

    # 6. Volume support (10): vol_ratio (5d/20d) in [0.8, 1.6]
    vr = e.get("vol_ratio")
    vol_pts = 10.0 * _scale(vr, 0.8, 1.6) if vr is not None else 4.0

    # 7. Breakout / position (10): near 20d high
    pos = e.get("price_position_20d")
    high20 = e.get("high_20d")
    breakout = bool(close is not None and high20 is not None and close >= high20 * 0.995)
    pos_pts = 10.0 * _scale(pos, 0.4, 1.0) if pos is not None else 4.0
    if breakout:
        pos_pts = max(pos_pts, 9.0)

    score = trend_pts + mom20_pts + mom5_pts + rsi_pts + macd_pts + vol_pts + pos_pts
    score = round(_clamp(score, 0.0, 100.0), 1)

    signals = {
        "trend": "up" if ma_stack.startswith("MA5>MA20>MA60") else ("down" if ma_stack == "downtrend" else "mixed"),
        "ma_stack": ma_stack,
        "rsi14": e.get("rsi14"),
        "macd": macd_state,
        "atr_pct": e.get("atr_pct"),
        "vol_ratio": e.get("vol_ratio"),
        "breakout_20d": breakout,
        "ret_20d": e.get("ret_20d"),
        "ret_5d": e.get("ret_5d"),
    }
    return score, signals


def _rationale(score: float, signals: dict) -> str:
    if signals.get("trend") == "n/a":
        return "データ不足のため評価不可"
    bits = []
    bits.append({"up": "上昇トレンド", "down": "下降トレンド", "mixed": "もみ合い"}.get(signals.get("trend"), ""))
    if signals.get("macd") == "bull":
        bits.append("MACD強気")
    if signals.get("breakout_20d"):
        bits.append("20日高値圏ブレイク")
    rsi = signals.get("rsi14")
    if rsi is not None:
        bits.append(f"RSI{rsi:.0f}")
    return "・".join([b for b in bits if b]) or "中立"


def build_codes(cfg: dict, pool: list[dict]) -> dict[str, dict]:
    """Union of pool + enabled + watchlist, keyed by code (pool/yaml metadata)."""
    catalog: dict[str, dict] = {}
    for p in pool:
        catalog[p["code"]] = {"name": p.get("name", p["code"]), "sector": p.get("sector")}
    for t in cfg.get("tickers", []):
        if isinstance(t, dict) and t.get("code"):
            catalog.setdefault(t["code"], {"name": t.get("name", t["code"]), "sector": t.get("sector")})
            if t["code"] in catalog and not catalog[t["code"]].get("name"):
                catalog[t["code"]]["name"] = t.get("name", t["code"])
    for w in cfg.get("watchlist", []) or []:
        if isinstance(w, dict) and w.get("code"):
            catalog.setdefault(w["code"], {"name": w.get("name", w["code"]), "sector": w.get("sector")})
    return catalog


def run(pool_path: Path, features_out: Path, latest_out: Path, date_str: str) -> int:
    cfg = load_tickers_config()
    settings = get_curation_settings(cfg)
    min_rows = int(settings["min_warmup_rows"])
    pool = load_pool(pool_path)
    catalog = build_codes(cfg, pool)

    feature_entries = []
    candidates = []
    data_through = None

    for code in sorted(catalog):
        meta = catalog[code]
        e = compute_entry(code, meta.get("name", code), meta.get("sector"))
        feature_entries.append(e)
        if e.get("data_through"):
            data_through = max(data_through or "", e["data_through"])

        score, signals = score_entry(e)
        rows = int(e.get("rows", 0))
        candidates.append(
            {
                "code": code,
                "name": meta.get("name", code),
                "sector": meta.get("sector"),
                "score": score,
                "signals": signals,
                "horizon_days": 5,
                "rationale": _rationale(score, signals),
                "rows_available": rows,
                "warmup_ok": rows >= min_rows,
            }
        )

    candidates.sort(key=lambda c: (-c["score"], c["code"]))

    write_json(
        features_out,
        {
            "generated_at": now_jst_iso(),
            "data_through": data_through,
            "min_warmup_rows": min_rows,
            "entries": feature_entries,
        },
    )

    latest_payload = {
        "schema_version": 1,
        "agent": "technical",
        "model": "deterministic-baseline",
        "generated_at": now_jst_iso(),
        "as_of": date_str,
        "data_through": data_through,
        "candidates": candidates,
        "universe_evaluated": sorted(catalog),
        "notes": "Deterministic baseline from technical_screen.py. May be refined by the technical agent.",
    }
    write_json(latest_out, latest_payload)
    write_json(CURATION_DIR / f"technical_{date_str}.json", latest_payload)

    ranked = [f"{c['code']}={c['score']}" for c in candidates[:10]]
    print(f"Technical screen: {len(candidates)} tickers, data_through={data_through}")
    print("Top:", ", ".join(ranked))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Technical screening for AI ticker curation")
    p.add_argument("--pool", default=None, help="path to curation_pool.yml")
    p.add_argument("--out", default=None, help="path to technical_features.json")
    p.add_argument("--latest-out", default=None, help="path to technical_latest.json")
    p.add_argument("--date", default=None, help="YYYY-MM-DD JST (default: today JST)")
    return p


def main() -> int:
    args = build_parser().parse_args()
    pool_path = Path(args.pool) if args.pool else None
    features_out = Path(args.out) if args.out else (CURATION_DIR / "technical_features.json")
    latest_out = Path(args.latest_out) if args.latest_out else (CURATION_DIR / "technical_latest.json")
    date_str = args.date or today_jst_iso()
    return run(pool_path, features_out, latest_out, date_str)


if __name__ == "__main__":
    raise SystemExit(main())
