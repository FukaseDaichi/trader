import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import BASE_DIR, DOCS_DIR, STATE_FILE, TICKERS
from .data_loader import load_data
from .model import add_features

MAX_HISTORY_DAYS = 30
MAX_DASHBOARD_ROWS = 500
JST = ZoneInfo("Asia/Tokyo")
RUN_DATE_ENV = "RUN_DATE_JST"

DASHBOARD_INDEX_FILE = DOCS_DIR / "dashboard_index.json"
TICKER_EXPORT_DIR = DOCS_DIR / "tickers"
LEGACY_HISTORY_FILE = DOCS_DIR / "history_data.json"
EXPORT_COLUMNS = [
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "ma_5",
    "ma_20",
    "ma_60",
    "rsi",
]


def _atomic_write_json(path: Path, payload: Any, indent: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=indent)
    temp_path.replace(path)


def _normalize_signals(signals: Any, allowed_tickers: set[str] | None = None):
    """
    Keep signal list stable, remove duplicate tickers in a day,
    and optionally filter by allowed tickers.
    """
    if not isinstance(signals, list):
        return []

    normalized = []
    seen_tickers = set()

    for signal in signals:
        if not isinstance(signal, dict):
            continue

        ticker = signal.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            continue

        if allowed_tickers is not None and ticker not in allowed_tickers:
            continue

        if ticker in seen_tickers:
            continue
        seen_tickers.add(ticker)

        normalized.append(signal)

    return normalized


def _normalize_history(history: Any, allowed_tickers: set[str] | None = None):
    """
    Ensure history has at most one entry per date and valid structure.
    """
    if not isinstance(history, list):
        return []

    normalized = []
    seen_dates = set()

    for entry in history:
        if not isinstance(entry, dict):
            continue

        date = entry.get("date")
        if not isinstance(date, str) or not date or date in seen_dates:
            continue

        seen_dates.add(date)
        normalized.append(
            {
                "date": date,
                "signals": _normalize_signals(
                    entry.get("signals", []),
                    allowed_tickers=allowed_tickers,
                ),
            }
        )

        if len(normalized) >= MAX_HISTORY_DAYS:
            break

    return normalized


def _ticker_signal_history(
    normalized_history: list[dict[str, Any]],
    ticker_code: str,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for day_entry in normalized_history:
        signals = day_entry.get("signals", [])
        if not isinstance(signals, list):
            continue
        for signal in signals:
            if isinstance(signal, dict) and signal.get("ticker") == ticker_code:
                entries.append({"date": day_entry["date"], "signal": signal})
                break
    return entries


def _to_dashboard_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    export_cols = [col for col in EXPORT_COLUMNS if col in df.columns]
    if not export_cols:
        return []

    trimmed = df[export_cols].tail(MAX_DASHBOARD_ROWS).copy()
    trimmed["date"] = pd.to_datetime(trimmed["date"]).dt.strftime("%Y-%m-%d")
    trimmed = trimmed.replace({np.nan: None})
    return trimmed.to_dict(orient="records")


def _calc_avg_volume(records: list[dict[str, Any]], window: int = 20) -> float | None:
    recent = records[-window:]
    volumes: list[float] = []
    for row in recent:
        volume = row.get("volume")
        if isinstance(volume, (int, float)) and not isinstance(volume, bool):
            volumes.append(float(volume))

    if not volumes:
        return None
    return float(sum(volumes) / len(volumes))


def _sync_dev_public_assets() -> None:
    # Also copy to web/public/ for local development (npm run dev).
    dev_public_dir = BASE_DIR / "web" / "public"
    if not dev_public_dir.exists():
        return

    if DASHBOARD_INDEX_FILE.exists():
        shutil.copy2(DASHBOARD_INDEX_FILE, dev_public_dir / "dashboard_index.json")

    dev_ticker_dir = dev_public_dir / "tickers"
    if dev_ticker_dir.exists():
        shutil.rmtree(dev_ticker_dir)
    if TICKER_EXPORT_DIR.exists():
        shutil.copytree(TICKER_EXPORT_DIR, dev_ticker_dir)

    legacy_file = dev_public_dir / "history_data.json"
    if legacy_file.exists():
        legacy_file.unlink(missing_ok=True)


def _resolve_run_date_jst(now_jst: datetime) -> str:
    override = os.getenv(RUN_DATE_ENV, "").strip()
    if not override:
        return now_jst.strftime("%Y-%m-%d")
    try:
        datetime.strptime(override, "%Y-%m-%d")
    except ValueError:
        print(f"Invalid {RUN_DATE_ENV}={override!r}; falling back to current JST date.")
        return now_jst.strftime("%Y-%m-%d")
    return override


def update_dashboard(signals):
    """
    Update state.json and export lightweight dashboard JSON assets.
    """
    # 1. Update state.json (history of signals).
    update_state(signals)

    # 2. Export dashboard_index.json + tickers/{code}.json.
    export_dashboard_data()


def update_state(signals):
    """
    Update state.json with new signals.
    """
    allowed_tickers = {t["code"] for t in TICKERS}

    if STATE_FILE.exists():
        with STATE_FILE.open("r", encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            state = {"history": [], "last_update": ""}
    else:
        state = {"history": [], "last_update": ""}

    history = _normalize_history(state.get("history", []), allowed_tickers=allowed_tickers)

    # Use JST so retry guard (which also uses JST) can detect same-day updates correctly.
    now_jst = datetime.now(JST)
    today_str = now_jst.strftime("%Y-%m-%d %H:%M:%S")
    today_date = _resolve_run_date_jst(now_jst)

    day_entry = {
        "date": today_date,
        "signals": _normalize_signals(signals, allowed_tickers=allowed_tickers),
    }

    # Replace today's entry when re-running in the same day.
    history = [entry for entry in history if entry["date"] != today_date]
    history.insert(0, day_entry)

    state["last_update"] = today_str
    state["history"] = history[:MAX_HISTORY_DAYS]
    _atomic_write_json(STATE_FILE, state, indent=2)


def export_dashboard_data():
    """
    Export dashboard index and per-ticker detail files.
    """
    allowed_tickers = {t["code"] for t in TICKERS}

    state: dict[str, Any] = {}
    if STATE_FILE.exists():
        with STATE_FILE.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            state = loaded

    normalized_history = _normalize_history(state.get("history", []), allowed_tickers=allowed_tickers)
    if state.get("history") != normalized_history:
        state["history"] = normalized_history
        _atomic_write_json(STATE_FILE, state, indent=2)

    if TICKER_EXPORT_DIR.exists():
        shutil.rmtree(TICKER_EXPORT_DIR)
    TICKER_EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    last_update = state.get("last_update", "")
    dashboard_index = {
        "last_update": last_update,
        "tickers": {},
    }

    for ticker_info in TICKERS:
        code = ticker_info["code"]
        name = ticker_info["name"]
        signal_history = _ticker_signal_history(normalized_history, code)
        latest_signal = signal_history[0]["signal"] if signal_history else None

        records: list[dict[str, Any]] = []
        df = load_data(code)
        if df is not None and not df.empty:
            # Add features without dropping NaNs to preserve chart continuity.
            featured = add_features(df, dropna=False)
            records = _to_dashboard_records(featured)

        latest_data = records[-1] if records else None
        avg_volume_20 = _calc_avg_volume(records, window=20)

        ticker_payload = {
            "last_update": last_update,
            "ticker": code,
            "name": name,
            "latest_signal": latest_signal,
            "signals": signal_history,
            "data": records,
        }
        _atomic_write_json(TICKER_EXPORT_DIR / f"{code}.json", ticker_payload)

        dashboard_index["tickers"][code] = {
            "ticker": code,
            "name": name,
            "latest_data": latest_data,
            "avg_volume_20": avg_volume_20,
            "latest_signal": latest_signal,
            "data_file": f"tickers/{code}.json",
            "rows": len(records),
        }

    _atomic_write_json(DASHBOARD_INDEX_FILE, dashboard_index)

    if LEGACY_HISTORY_FILE.exists():
        LEGACY_HISTORY_FILE.unlink(missing_ok=True)

    print(f"Dashboard index exported to {DASHBOARD_INDEX_FILE}")
    print(f"Ticker detail files exported to {TICKER_EXPORT_DIR}")
    _sync_dev_public_assets()


def export_history_data():
    """
    Backward-compatible wrapper.
    """
    export_dashboard_data()
