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
from . import db, model_store, performance
from .db_records import summarize_performance

MAX_HISTORY_DAYS = 30
MAX_DASHBOARD_ROWS = 500
JST = ZoneInfo("Asia/Tokyo")
RUN_DATE_ENV = "RUN_DATE_JST"

DASHBOARD_INDEX_FILE = DOCS_DIR / "dashboard_index.json"
TICKER_EXPORT_DIR = DOCS_DIR / "tickers"
LEGACY_HISTORY_FILE = DOCS_DIR / "history_data.json"
PERFORMANCE_FILE = DOCS_DIR / "performance_summary.json"
PERFORMANCE_DETAIL_FILE = DOCS_DIR / "performance_detail.json"
SIGNAL_OUTCOMES_RECENT_FILE = DOCS_DIR / "signal_outcomes_recent.json"
MODEL_QUALITY_FILE = DOCS_DIR / "model_quality.json"
DRIFT_REPORT_FILE = DOCS_DIR / "drift_report.json"
PORTFOLIO_LATEST_FILE = DOCS_DIR / "portfolio_latest.json"
PORTFOLIO_BACKTEST_FILE = DOCS_DIR / "portfolio_backtest.json"
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

    # 3. Phase 0: export realized-performance summary from the DB (best-effort).
    export_performance_summary()

    # 4. Phase 1: export model-quality summary from the active model artifact.
    export_model_quality()


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


def export_performance_summary():
    """
    Write docs/performance_summary.json from the measurement DB. Best-effort:
    if the DB is disabled or unreachable, write an "unavailable" marker and
    keep the previous summary untouched on disk if present.
    """
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    if not db.db_enabled():
        if not PERFORMANCE_FILE.exists():
            _atomic_write_json(PERFORMANCE_FILE, {
                "available": False, "reason": "db_disabled", "generated_at": now_str,
            }, indent=2)
        return

    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"performance_summary: DB unreachable ({type(exc).__name__}); leaving file as-is.")
        if not PERFORMANCE_FILE.exists():
            _atomic_write_json(PERFORMANCE_FILE, {
                "available": False, "reason": "db_unreachable", "generated_at": now_str,
            }, indent=2)
        return

    try:
        rows = db.fetch_outcome_rows(conn)
        summary = summarize_performance(rows, curve_horizon=1)
        size_mb = db.db_size_mb(conn)
        warn_mb = float(os.getenv("TRADER_DB_STORAGE_WARN_MB", "400"))
        payload = {
            "available": True,
            "generated_at": now_str,
            "as_of": _resolve_run_date_jst(datetime.now(JST)),
            "n_long_signals": summary["n_long_signals"],
            "horizons": summary["horizons"],
            "equity_curve": summary["equity_curve"],
            "db_size_mb": size_mb,
            "storage_warning": size_mb >= warn_mb,
        }
        if payload["storage_warning"]:
            print(f"WARNING: DB size {size_mb}MB >= {warn_mb}MB threshold.")
        _atomic_write_json(PERFORMANCE_FILE, payload, indent=2)
        print(f"Performance summary exported to {PERFORMANCE_FILE}")
    except Exception as exc:  # noqa: BLE001
        print(f"performance_summary: export failed (ignored): {type(exc).__name__}: {exc}")
    finally:
        conn.close()


def export_performance_detail():
    """
    Write docs/performance_detail.json (Phase 3). Best-effort: on DB disabled or
    unreachable, only write an unavailable marker if the file does not already
    exist (preserve last-good).
    """
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    if not db.db_enabled():
        if not PERFORMANCE_DETAIL_FILE.exists():
            _atomic_write_json(PERFORMANCE_DETAIL_FILE, {
                "available": False, "reason": "db_disabled", "generated_at": now_str,
            }, indent=2)
        return

    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"performance_detail: DB unreachable ({type(exc).__name__}); leaving file as-is.")
        if not PERFORMANCE_DETAIL_FILE.exists():
            _atomic_write_json(PERFORMANCE_DETAIL_FILE, {
                "available": False, "reason": "db_unreachable", "generated_at": now_str,
            }, indent=2)
        return

    try:
        horizon = 5
        history_days = int(os.getenv("TRADER_PERF_HISTORY_DAYS", "180") or 180)
        n_bins = int(os.getenv("TRADER_PERF_RELIABILITY_BINS", "10") or 10)

        rows = db.fetch_outcome_detail_rows(conn, horizon_days=horizon, history_days=history_days)
        mv = db.active_model_version(conn)
        pred_rows = db.fetch_prediction_outcomes(conn, mv, horizon) if mv else []

        if not rows:
            _atomic_write_json(PERFORMANCE_DETAIL_FILE, {
                "available": False, "reason": "insufficient_data", "generated_at": now_str,
            }, indent=2)
        else:
            detail = performance.build_performance_detail(rows, pred_rows, horizon,
                                                          history_days, n_bins)
            payload = {
                "available": True,
                "generated_at": now_str,
                "as_of": _resolve_run_date_jst(datetime.now(JST)),
                **detail,
            }
            _atomic_write_json(PERFORMANCE_DETAIL_FILE, payload, indent=2)
            print(f"Performance detail exported to {PERFORMANCE_DETAIL_FILE}")
    except Exception as exc:  # noqa: BLE001
        print(f"performance_detail: export failed (ignored): {type(exc).__name__}: {exc}")
    finally:
        conn.close()


def export_signal_outcomes_recent():
    """
    Write docs/signal_outcomes_recent.json (Phase 3). Best-effort: on DB disabled or
    unreachable, only write an unavailable marker if the file does not already
    exist (preserve last-good).
    """
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    if not db.db_enabled():
        if not SIGNAL_OUTCOMES_RECENT_FILE.exists():
            _atomic_write_json(SIGNAL_OUTCOMES_RECENT_FILE, {
                "available": False, "reason": "db_disabled", "generated_at": now_str,
            }, indent=2)
        return

    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"signal_outcomes_recent: DB unreachable ({type(exc).__name__}); leaving file as-is.")
        if not SIGNAL_OUTCOMES_RECENT_FILE.exists():
            _atomic_write_json(SIGNAL_OUTCOMES_RECENT_FILE, {
                "available": False, "reason": "db_unreachable", "generated_at": now_str,
            }, indent=2)
        return

    try:
        history_days = int(os.getenv("TRADER_PERF_HISTORY_DAYS", "180") or 180)
        rows = db.fetch_outcome_detail_rows(conn, horizon_days=5, history_days=history_days)
        recent = performance.build_recent_outcomes(rows, limit=200)

        if not recent:
            _atomic_write_json(SIGNAL_OUTCOMES_RECENT_FILE, {
                "available": False, "reason": "insufficient_data", "generated_at": now_str,
            }, indent=2)
        else:
            payload = {
                "available": True,
                "generated_at": now_str,
                "rows": recent,
            }
            _atomic_write_json(SIGNAL_OUTCOMES_RECENT_FILE, payload, indent=2)
            print(f"Signal outcomes recent exported to {SIGNAL_OUTCOMES_RECENT_FILE}")
    except Exception as exc:  # noqa: BLE001
        print(f"signal_outcomes_recent: export failed (ignored): {type(exc).__name__}: {exc}")
    finally:
        conn.close()


def _median(values):
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not nums:
        return None
    nums = sorted(nums)
    mid = len(nums) // 2
    if len(nums) % 2:
        return float(nums[mid])
    return float((nums[mid - 1] + nums[mid]) / 2.0)


def _load_drift_overlay() -> dict:
    if not DRIFT_REPORT_FILE.exists():
        return {}
    try:
        data = json.loads(DRIFT_REPORT_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def export_model_quality():
    """
    Write docs/model_quality.json (Phase 1). Sourced from the committed active
    model artifact's metadata (so it works without a DB), enriched with the
    drift report when present. When no active model exists the file is marked
    unavailable and the dashboard card hides itself.
    """
    now_str = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

    active = model_store.read_active_model()
    if not active:
        _atomic_write_json(MODEL_QUALITY_FILE, {
            "available": False, "reason": "no_active_model", "generated_at": now_str,
        }, indent=2)
        return

    version = active.get("version")
    meta = model_store.read_version_metadata(version) or {}
    cv_by_ticker = ((meta.get("cv_metrics") or {}).get("by_ticker") or {})

    drift = _load_drift_overlay()
    drift_by = drift.get("by_ticker", {}) if isinstance(drift.get("by_ticker"), dict) else {}

    allowed = {t["code"] for t in TICKERS}
    by_ticker: dict[str, Any] = {}
    briers: list[float] = []
    ics: list[float] = []
    any_warning = False

    for code, metrics in cv_by_ticker.items():
        if code not in allowed or not isinstance(metrics, dict):
            continue
        cal = metrics.get("calibration") or {}
        drift_row = drift_by.get(code, {}) if isinstance(drift_by.get(code), dict) else {}
        warning = bool(drift_row.get("warning", False))
        any_warning = any_warning or warning

        brier = metrics.get("brier")
        ic = metrics.get("ic")
        if isinstance(brier, (int, float)):
            briers.append(brier)
        if isinstance(ic, (int, float)):
            ics.append(ic)

        by_ticker[code] = {
            "brier": brier,
            "brier_raw": metrics.get("brier_raw"),
            "ic": ic,
            "auc": metrics.get("auc"),
            "calibration_rows": cal.get("rows"),
            "psi_max": drift_row.get("psi_max"),
            "warning": warning,
        }

    drift_warning = bool(drift.get("breached")) or any_warning
    payload = {
        "available": True,
        "generated_at": now_str,
        "active_model_version": version,
        "horizon_days": active.get("horizon_days") or meta.get("horizon_days"),
        "summary": {
            "tickers": len(by_ticker),
            "median_brier": _median(briers),
            "median_ic": _median(ics),
            "drift_warning": drift_warning,
        },
        "by_ticker": by_ticker,
    }
    _atomic_write_json(MODEL_QUALITY_FILE, payload, indent=2)
    print(f"Model quality summary exported to {MODEL_QUALITY_FILE}")


# --- Phase 2: portfolio dashboard exports ----------------------------------

def export_portfolio_latest(snapshot, *, run_date=None, reason=None,
                            generated_at=None, output_path=None):
    """
    Write docs/portfolio_latest.json from a ``build_portfolio_snapshot`` result.

    Available: when ``snapshot`` is present and its status is ``"ok"`` the file
    is ``{"available": True, "generated_at": ..., **snapshot}`` (the snapshot
    already carries run_date/as_of_date/mode/status/positions/etc.).

    Unavailable: when ``snapshot is None`` OR its status is not ``"ok"`` (e.g.
    a Phase 2 fallback), the file is ``{"available": False, "reason": <reason or
    status>, "generated_at": ...}``. Pass ``reason`` to surface the fallback
    cause (e.g. ``"insufficient_universe"``).

    ``generated_at`` is only stamped when supplied — this function never calls
    ``datetime.now`` so tests stay deterministic (the caller passes a JST
    timestamp). ``run_date`` is stamped onto the unavailable payload too so the
    card can show the date. Written atomically (indent=2). Returns the path str.
    """
    path = Path(output_path) if output_path is not None else PORTFOLIO_LATEST_FILE

    if snapshot is not None and snapshot.get("status") == "ok":
        payload: dict[str, Any] = {"available": True}
        payload.update(snapshot)
    else:
        resolved_reason = reason
        if resolved_reason is None and snapshot is not None:
            resolved_reason = snapshot.get("status")
        payload = {"available": False, "reason": resolved_reason}
        if run_date is not None:
            payload["run_date"] = run_date

    if generated_at:
        payload["generated_at"] = generated_at

    _atomic_write_json(path, payload, indent=2)
    print(f"Portfolio snapshot exported to {path} (available={payload['available']})")
    return str(path)


def export_portfolio_backtest(result, *, model_version=None, run_date=None,
                              generated_at=None, output_path=None):
    """
    Write docs/portfolio_backtest.json from a ``run_portfolio_backtest`` result.

    Thin delegate to ``portfolio_backtest.write_portfolio_backtest_report`` so
    the docs export lives in the dashboard module per convention; the report
    logic (available true/false, atomic write) is owned there. Returns the path.
    """
    from .portfolio_backtest import write_portfolio_backtest_report
    out = str(output_path) if output_path is not None else str(PORTFOLIO_BACKTEST_FILE)
    return write_portfolio_backtest_report(
        result, output_path=out, model_version=model_version,
        run_date=run_date, generated_at=generated_at,
    )
