import io
import json
import os
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import requests
import yfinance as yf

from .config import DATA_DIR

REQUIRED_COLS = ["date", "open", "high", "low", "close", "volume"]
DEFAULT_HTTP_TIMEOUT_SEC = 20
DEFAULT_STALE_OPEN_DAYS = 0
DEFAULT_YF_FALLBACK_ENABLED = True
JPX_HOLIDAY_CACHE = DATA_DIR / "jpx_holidays.json"


def _get_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return int(default)
    try:
        return int(raw)
    except ValueError:
        print(f"Invalid {name}={raw!r}. Falling back to {default}.")
        return int(default)


def _get_env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return bool(default)
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    print(f"Invalid {name}={raw!r}. Falling back to {default}.")
    return bool(default)


def _today_jst() -> date:
    return (datetime.now(UTC) + timedelta(hours=9)).date()


def _load_jpx_holiday_set(cache_path=JPX_HOLIDAY_CACHE) -> set[date]:
    if not cache_path.exists():
        return set()

    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return set()

    if isinstance(payload, dict) and isinstance(payload.get("holidays"), dict):
        keys = payload["holidays"].keys()
    elif isinstance(payload, dict):
        keys = payload.keys()
    else:
        return set()

    holidays = set()
    for key in keys:
        try:
            holidays.add(date.fromisoformat(str(key)[:10]))
        except ValueError:
            continue
    return holidays


def _is_open_day(target: date, holidays: set[date]) -> bool:
    return target.weekday() <= 4 and target not in holidays


def _latest_completed_open_day(today_jst: date, holidays: set[date]) -> date:
    cursor = today_jst - timedelta(days=1)
    for _ in range(370):
        if _is_open_day(cursor, holidays):
            return cursor
        cursor -= timedelta(days=1)
    return today_jst - timedelta(days=1)


def _open_day_gap(latest: date, required_latest: date, holidays: set[date]) -> int:
    if latest >= required_latest:
        return 0
    gap = 0
    cursor = latest + timedelta(days=1)
    while cursor <= required_latest:
        if _is_open_day(cursor, holidays):
            gap += 1
        cursor += timedelta(days=1)
    return gap


def _assess_freshness(df, stale_open_days: int) -> dict:
    if df is None or df.empty or "date" not in df.columns:
        return {
            "is_stale": True,
            "latest_date": None,
            "required_latest_date": None,
            "open_day_gap": None,
        }

    holidays = _load_jpx_holiday_set()
    required_latest = _latest_completed_open_day(_today_jst(), holidays)
    latest_ts = pd.to_datetime(df["date"]).max()
    latest_date = latest_ts.date()
    gap = _open_day_gap(latest_date, required_latest, holidays)
    return {
        "is_stale": gap > stale_open_days,
        "latest_date": latest_date,
        "required_latest_date": required_latest,
        "open_day_gap": gap,
    }


def _normalize_ohlcv(df):
    if df is None or df.empty:
        return None

    normalized = df.copy()
    normalized.columns = [str(col).lower().replace(" ", "_") for col in normalized.columns]

    if "datetime" in normalized.columns and "date" not in normalized.columns:
        normalized = normalized.rename(columns={"datetime": "date"})
    if "adj_close" in normalized.columns and "close" not in normalized.columns:
        normalized["close"] = normalized["adj_close"]

    missing = [col for col in REQUIRED_COLS if col not in normalized.columns]
    if missing:
        return None

    normalized = normalized[REQUIRED_COLS].copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized = normalized.dropna(subset=["date"])

    for col in ["open", "high", "low", "close", "volume"]:
        normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
    normalized = normalized.dropna(subset=["open", "high", "low", "close", "volume"])

    normalized["date"] = normalized["date"].dt.tz_localize(None)
    normalized = normalized.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return normalized


def _to_yfinance_symbol(ticker_code):
    code = str(ticker_code).strip().upper()
    if code.endswith(".JP"):
        return f"{code[:-3]}.T"
    return code


def download_stooq_data(ticker_code):
    """
    Download historical data from Stooq.
    Stooq URL format for CSV download: https://stooq.com/q/d/l/?s={code}&i=d
    """
    url = f"https://stooq.com/q/d/l/?s={ticker_code}&i=d"
    headers = {"User-Agent": "Mozilla/5.0"}
    timeout_sec = _get_env_int("TRADER_DATA_HTTP_TIMEOUT_SEC", DEFAULT_HTTP_TIMEOUT_SEC)
    
    try:
        response = requests.get(url, headers=headers, timeout=timeout_sec)
        response.raise_for_status()
        
        # Check if content is valid CSV (sometimes Stooq returns an HTML page on error)
        text = response.text.strip()
        if text.startswith("No data"):
            print(f"Error fetching data for {ticker_code}: No data from Stooq.")
            return None
        if "Preceded by" in text: # Typical Stooq error message start
             print(f"Error fetching data for {ticker_code}: Invalid ticker or data not found.")
             return None
             
        df = pd.read_csv(io.StringIO(text))
        normalized = _normalize_ohlcv(df)
        if normalized is None:
            print(f"Error: Missing columns in Stooq data for {ticker_code}")
            return None

        return normalized
    
    except Exception as e:
        print(f"Failed to download data for {ticker_code}: {e}")
        return None


def download_yfinance_data(ticker_code):
    """
    Download historical daily OHLCV from Yahoo Finance.
    """
    symbol = _to_yfinance_symbol(ticker_code)
    timeout_sec = _get_env_int("TRADER_DATA_HTTP_TIMEOUT_SEC", DEFAULT_HTTP_TIMEOUT_SEC)

    try:
        df = yf.download(
            symbol,
            period="max",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=timeout_sec,
        )
        if df is None or df.empty:
            print(f"No data found on yfinance for {ticker_code} ({symbol}).")
            return None

        # yfinance may return MultiIndex columns depending on version/settings.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

        normalized = _normalize_ohlcv(df.reset_index())
        if normalized is None:
            print(f"Error: Missing columns in yfinance data for {ticker_code} ({symbol})")
            return None

        return normalized
    except Exception as e:
        print(f"Failed to download yfinance data for {ticker_code} ({symbol}): {e}")
        return None


def _download_with_fallback(ticker_code):
    stale_open_days = max(0, _get_env_int("TRADER_DATA_STALE_OPEN_DAYS", DEFAULT_STALE_OPEN_DAYS))
    use_yf_fallback = _get_env_bool("TRADER_YF_FALLBACK_ENABLED", DEFAULT_YF_FALLBACK_ENABLED)

    stooq_df = download_stooq_data(ticker_code)
    stooq_freshness = _assess_freshness(stooq_df, stale_open_days=stale_open_days)

    if stooq_df is not None and not stooq_freshness["is_stale"]:
        return stooq_df, "stooq", stooq_freshness

    if stooq_df is None:
        print(f"Stooq download failed for {ticker_code}.")
    else:
        print(
            "Stooq data is stale for "
            f"{ticker_code}: latest={stooq_freshness['latest_date']}, "
            f"required>={stooq_freshness['required_latest_date']} "
            f"(open-day gap={stooq_freshness['open_day_gap']})."
        )

    if not use_yf_fallback:
        return stooq_df, "stooq", stooq_freshness

    print(f"Trying yfinance fallback for {ticker_code}...")
    yf_df = download_yfinance_data(ticker_code)
    yf_freshness = _assess_freshness(yf_df, stale_open_days=stale_open_days)

    if yf_df is not None and not yf_freshness["is_stale"]:
        return yf_df, "yfinance", yf_freshness

    if yf_df is not None:
        print(
            "yfinance data is also stale for "
            f"{ticker_code}: latest={yf_freshness['latest_date']}, "
            f"required>={yf_freshness['required_latest_date']} "
            f"(open-day gap={yf_freshness['open_day_gap']})."
        )

    if stooq_df is None and yf_df is None:
        return None, "none", yf_freshness

    if stooq_df is None:
        return yf_df, "yfinance", yf_freshness
    if yf_df is None:
        return stooq_df, "stooq", stooq_freshness

    # If both are stale, use the fresher one by latest date.
    stooq_latest = stooq_freshness["latest_date"]
    yf_latest = yf_freshness["latest_date"]
    if yf_latest and (stooq_latest is None or yf_latest > stooq_latest):
        print(f"Using yfinance for {ticker_code} because it is fresher than Stooq.")
        return yf_df, "yfinance", yf_freshness

    return stooq_df, "stooq", stooq_freshness


def update_data(ticker_code):
    """
    Update local parquet file using Stooq and automatic yfinance fallback.
    """
    print(f"Updating data for {ticker_code}...")
    
    # Download fresh data with stale-data fallback.
    new_df, source, freshness = _download_with_fallback(ticker_code)
    
    if new_df is None or new_df.empty:
        print(f"No new data found for {ticker_code}.")
        return None
    print(f"Selected source for {ticker_code}: {source}")
    
    file_path = DATA_DIR / f"{ticker_code}.parquet"
    
    if file_path.exists():
        old_df = pd.read_parquet(file_path)
        # Combine and drop duplicates based on Date
        combined_df = pd.concat([old_df, new_df]).drop_duplicates(subset=['date'], keep='last')
        combined_df = combined_df.sort_values('date').reset_index(drop=True)
    else:
        combined_df = new_df
        
    # Save to parquet
    combined_df.to_parquet(file_path)
    latest = combined_df["date"].max().strftime("%Y-%m-%d")
    stale_note = ""
    if freshness.get("is_stale"):
        stale_note = (
            f" [stale: latest={freshness.get('latest_date')}, "
            f"required>={freshness.get('required_latest_date')}, "
            f"open-day gap={freshness.get('open_day_gap')}]"
        )
    print(f"Data saved to {file_path}. Latest date: {latest} (source={source}){stale_note}")
    
    return combined_df

def load_data(ticker_code):
    file_path = DATA_DIR / f"{ticker_code}.parquet"
    if file_path.exists():
        return pd.read_parquet(file_path)
    return None


def sync_data_files(active_ticker_codes):
    """
    Remove local parquet files that are not in the active ticker list.
    """
    active_codes = {code for code in active_ticker_codes if isinstance(code, str) and code}
    removed_codes = []

    for file_path in DATA_DIR.glob("*.parquet"):
        ticker_code = file_path.stem
        if ticker_code in active_codes:
            continue

        file_path.unlink(missing_ok=True)
        removed_codes.append(ticker_code)

    if removed_codes:
        removed_codes.sort()
        print(f"Removed stale data files: {', '.join(removed_codes)}")

    return removed_codes
