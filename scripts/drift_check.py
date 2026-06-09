#!/usr/bin/env python3
"""
Phase 1 drift check (roadmap §6.3, W3).

For the active model it computes, per ticker:
  - rolling IC / Brier / hit-rate from the DB (predictions x signal_outcomes),
  - feature PSI of a recent window vs the training feature reference (artifact).

Writes docs/drift_report.json and, when the DB is enabled, persists a row in
drift_reports. Returns exit code 2 when a real threshold breach is detected
(sufficient outcome sample), so a CI step can open an Issue; insufficient
sample or no active model is a no-op warning (exit 0).

PSI works without a DB (artifact + current prices only); IC/Brier need the DB.

Usage:
  uv run python scripts/drift_check.py
  uv run python scripts/drift_check.py --as-of 2026-06-09
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from src import db, model_store, phase1  # noqa: E402
from src.calibration import brier_score, ic_score  # noqa: E402
from src.config import DOCS_DIR, TICKERS, get_label_config, get_model_runtime_config  # noqa: E402
from src.data_loader import load_data  # noqa: E402
from src.labels import effective_horizon  # noqa: E402
from src.macro import load_macro_panel  # noqa: E402
from src.model import build_feature_frame  # noqa: E402
from scripts.curation_common import today_jst_iso  # noqa: E402

DRIFT_REPORT_FILE = DOCS_DIR / "drift_report.json"

BREACH_EXIT_CODE = 2


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw not in (None, "") else float(default)
    except ValueError:
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw not in (None, "") else int(default)
    except ValueError:
        return int(default)


def _write(payload: dict) -> None:
    DRIFT_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    DRIFT_REPORT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Drift report written to {DRIFT_REPORT_FILE}")


def _db_outcomes_by_ticker(model_version: str, horizon: int) -> dict[str, list[dict]]:
    if not db.db_enabled():
        return {}
    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"drift: DB unreachable ({type(exc).__name__}); IC/Brier skipped.")
        return {}
    try:
        rows = db.fetch_prediction_outcomes(conn, model_version, horizon)
    except Exception as exc:  # noqa: BLE001
        print(f"drift: outcome fetch failed (ignored): {type(exc).__name__}: {exc}")
        rows = []
    finally:
        conn.close()

    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)
    return by_ticker


def _drift_reasons(metric_status: str, ic, brier, psi_max, thresholds: dict) -> tuple[list[str], list[str]]:
    """
    Return (dashboard warning reasons, CI-breach reasons).

    PSI can be useful as an early dashboard warning, but it should only trigger
    a CI breach once realized outcome metrics have enough sample to evaluate the
    active model. This keeps initial rollout from opening noisy Issues.
    """
    reasons: list[str] = []
    breach_reasons: list[str] = []

    if metric_status == "ok":
        if ic is not None and ic < thresholds["min_ic"]:
            reason = f"ic<{thresholds['min_ic']}"
            reasons.append(reason)
            breach_reasons.append(reason)
        if brier is not None and brier > thresholds["max_brier"]:
            reason = f"brier>{thresholds['max_brier']}"
            reasons.append(reason)
            breach_reasons.append(reason)

    if psi_max is not None and psi_max > thresholds["max_psi"]:
        reason = f"psi>{thresholds['max_psi']}"
        reasons.append(reason)
        if metric_status == "ok":
            breach_reasons.append(reason)

    return reasons, breach_reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 model drift check")
    parser.add_argument("--as-of", default=today_jst_iso())
    args = parser.parse_args()

    now = today_jst_iso()
    thresholds = {
        "min_outcomes": _env_int("TRADER_DRIFT_MIN_OUTCOMES", 30),
        "min_ic": _env_float("TRADER_DRIFT_MIN_IC", -0.02),
        "max_brier": _env_float("TRADER_DRIFT_MAX_BRIER", 0.30),
        "max_psi": _env_float("TRADER_DRIFT_MAX_PSI", 0.25),
        # Matches build_feature_reference(ref_rows) so PSI compares like-length
        # windows (≈0 right after retraining, grows only on real drift).
        "psi_window": _env_int("TRADER_DRIFT_PSI_WINDOW", 250),
    }

    active = model_store.read_active_model()
    if not active:
        _write({"available": False, "reason": "no_active_model", "generated_at": now})
        print("drift: no active model; nothing to check.")
        return 0

    version = active.get("version")
    label_cfg = get_label_config()
    model_cfg = get_model_runtime_config()
    macro_enabled = bool(active.get(
        "macro_features_enabled",
        model_cfg.get("macro_features_enabled", True),
    ))
    horizon = active.get("horizon_days") or effective_horizon(label_cfg)
    outcomes_by_ticker = _db_outcomes_by_ticker(version, horizon)
    macro_panel = load_macro_panel()

    by_ticker: dict[str, dict] = {}
    warned: list[str] = []
    insufficient = 0

    for ticker in TICKERS:
        code = ticker["code"]
        bundle = model_store.load_model_bundle(version, code)
        feature_reference = (bundle or {}).get("feature_reference") or {}

        # PSI (no DB needed): recent feature window vs training reference.
        psi_max = None
        worst_feature = None
        if feature_reference:
            df = load_data(code)
            if df is not None and not df.empty:
                featured = build_feature_frame(
                    df,
                    macro_panel=macro_panel,
                    ticker_info=ticker,
                    macro_enabled=macro_enabled,
                )
                window = featured.tail(thresholds["psi_window"])
                psi_max, psi_by = phase1.feature_psi(feature_reference, window)
                if psi_by:
                    finite = {k: v for k, v in psi_by.items() if v is not None}
                    if finite:
                        worst_feature = max(finite, key=finite.get)

        # IC / Brier / hit from DB outcomes.
        rows = outcomes_by_ticker.get(code, [])
        n = len(rows)
        ic = brier = hr = None
        metric_status = "insufficient_sample"
        if n >= thresholds["min_outcomes"]:
            metric_status = "ok"
            prob = [r.get("prob_up") for r in rows]
            ret = [r.get("realized_ret") for r in rows]
            up_label = [1 if (r.get("realized_ret") or 0) > 0 else 0 for r in rows]
            hits = [1 if r.get("hit") else 0 for r in rows if r.get("hit") is not None]
            ic = ic_score(prob, ret)
            brier = brier_score(prob, up_label)
            hr = float(np.mean(hits)) if hits else None
        else:
            insufficient += 1

        reasons, breach_reasons = _drift_reasons(metric_status, ic, brier, psi_max, thresholds)

        warning = len(reasons) > 0
        breached_ticker = len(breach_reasons) > 0
        if warning:
            warned.append(code)

        by_ticker[code] = {
            "ic": ic,
            "brier": brier,
            "hit_rate": hr,
            "n_outcomes": n,
            "metric_status": metric_status,
            "psi_max": psi_max,
            "psi_worst_feature": worst_feature,
            "warning": warning,
            "breached": breached_ticker,
            "reasons": reasons,
            "breach_reasons": breach_reasons,
        }

    breached_tickers = [code for code, row in by_ticker.items() if row.get("breached")]
    breached = len(breached_tickers) > 0
    status = "warning" if breached else ("insufficient_sample" if insufficient else "ok")
    payload = {
        "available": True,
        "generated_at": now,
        "as_of": args.as_of,
        "model_version": version,
        "horizon_days": horizon,
        "thresholds": thresholds,
        "summary": {
            "tickers": len(by_ticker),
            "breached": breached,
            "warned_tickers": warned,
            "breached_tickers": breached_tickers,
            "insufficient_sample_tickers": insufficient,
        },
        "by_ticker": by_ticker,
    }
    _write(payload)

    if db.db_enabled():
        try:
            conn = db.connect()
            try:
                db.insert_drift_report(conn, now, version, "global", status, breached, payload["summary"])
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            print(f"drift: report persist skipped (ignored): {type(exc).__name__}: {exc}")

    if breached:
        print(f"DRIFT_BREACH: model {version} breached for {warned}")
        return BREACH_EXIT_CODE
    print(f"drift: ok (model {version}; {insufficient} ticker(s) insufficient sample).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
