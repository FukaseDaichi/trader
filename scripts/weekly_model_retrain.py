#!/usr/bin/env python3
"""
Weekly model retrain (Phase 1, W3).

Trains a persisted per-ticker horizon-aware LightGBM ensemble (technical + macro
features), calibrates it on out-of-sample folds, saves the artifacts under
data/models/<version>/<ticker>/, and registers the version in model_registry
(when the DB is enabled), flipping the active pointer to the new version.

Robustness:
  - A ticker that fails to train is recorded and skipped; others continue.
  - When the DB is unreachable, artifacts + report + active pointer are still
    written locally (registry registration is the only step skipped), so the
    next daily run can use the new model after the commit.

Usage:
  uv run python scripts/weekly_model_retrain.py --output docs/weekly_retrain_report.json
  uv run python scripts/weekly_model_retrain.py --version per-ticker-v1-20260613
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src import db, model_store  # noqa: E402
from src.backtest import evaluate_kpi_gate  # noqa: E402
from src.config import (  # noqa: E402
    BACKTEST_GATE_CONFIG,
    TICKERS,
    get_label_config,
    get_model_runtime_config,
)
from src.data_loader import load_data, update_data  # noqa: E402
from src.macro import load_macro_panel  # noqa: E402
from src.model import PHASE1_FEATURE_COLS, build_feature_frame  # noqa: E402
from src.phase1 import train_ticker_bundle  # noqa: E402
from scripts.curation_common import now_jst_iso, today_jst_iso  # noqa: E402

MODEL_KIND = "per_ticker_horizon_v1"


def _default_version() -> str:
    return f"per-ticker-v1-{today_jst_iso().replace('-', '')}"


def _failure_entry(ticker: dict, reason: str, error: str | None = None,
                   warnings: list[str] | None = None) -> dict:
    return {
        "ticker": ticker["code"],
        "name": ticker["name"],
        "status": "failed",
        "model_ready": False,
        "reason": reason,
        "error": error,
        "data_validation_warnings": warnings or [],
    }


def _median(values):
    nums = [v for v in values if isinstance(v, (int, float))]
    return float(statistics.median(nums)) if nums else None


def run_retrain(output_path: Path, version: str) -> int:
    label_cfg = get_label_config()
    model_cfg = get_model_runtime_config()
    macro_panel = load_macro_panel()
    if macro_panel is None:
        print("weekly: no macro panel found; training on technical features only "
              "(macro columns will be NaN).")

    entries = []
    calibration_map: dict[str, dict | None] = {}
    cv_by_ticker: dict[str, dict] = {}
    quality_rows: list[dict] = []
    run_date = today_jst_iso()

    for ticker in TICKERS:
        code = ticker["code"]
        warnings: list[str] = []
        try:
            updated = update_data(code)
            if updated is not None:
                warnings = updated.attrs.get("validation_warnings", []) or []

            df = load_data(code)
            if df is not None:
                warnings = list(dict.fromkeys(warnings + (df.attrs.get("validation_warnings", []) or [])))
            if df is None or len(df) < 60:
                entries.append(_failure_entry(ticker, "insufficient_data", warnings=warnings))
                continue

            featured = build_feature_frame(df, macro_panel=macro_panel, ticker_info=ticker)
            if featured.empty:
                entries.append(_failure_entry(ticker, "empty_features", warnings=warnings))
                continue

            result, info = train_ticker_bundle(featured, BACKTEST_GATE_CONFIG, label_cfg, model_cfg)
            if result is None:
                entries.append(_failure_entry(ticker, info.get("reason", "training_failed"), warnings=warnings))
                continue

            model_store.save_model_bundle(version, code, result["boosters"], result["metadata"])

            # KPI gate for report continuity (uses the same label config).
            gate = evaluate_kpi_gate(featured, BACKTEST_GATE_CONFIG, label_config=label_cfg)
            cv = result["cv_metrics"]
            calibration_map[code] = result["metadata"].get("calibration")
            cv_by_ticker[code] = cv
            quality_rows.append({
                "run_date": run_date,
                "model_version": version,
                "ticker": code,
                "horizon_days": gate.get("horizon_days"),
                "brier": cv.get("brier"),
                "brier_raw": cv.get("brier_raw"),
                "ic": cv.get("ic"),
                "auc": cv.get("auc"),
                "hit_rate": cv.get("hit_rate"),
                "calibration_rows": (cv.get("calibration") or {}).get("rows"),
                "psi_max": None,
                "warning": False,
            })

            entries.append({
                "ticker": code,
                "name": ticker["name"],
                "status": "ok",
                "model_ready": True,
                "model_version": version,
                "horizon_days": gate.get("horizon_days"),
                "label_mode": gate.get("label_mode"),
                "cv_metrics": cv,
                "calibration_applied": (cv.get("calibration") or {}).get("applied"),
                "gate_passed": bool(gate.get("passed", False)),
                "gate_reason": gate.get("reason"),
                "failures": gate.get("failures", []),
                "metrics": gate.get("metrics", {}),
                "data_validation_warnings": warnings,
            })
        except Exception as e:  # noqa: BLE001
            entries.append(_failure_entry(
                ticker, "ticker_processing_failed",
                error=f"{type(e).__name__}: {e}", warnings=warnings,
            ))

    ok_entries = [e for e in entries if e.get("status") == "ok"]
    aggregate = {
        "median_ic": _median([cv_by_ticker[c].get("ic") for c in cv_by_ticker]),
        "median_brier": _median([cv_by_ticker[c].get("brier") for c in cv_by_ticker]),
        "median_auc": _median([cv_by_ticker[c].get("auc") for c in cv_by_ticker]),
    }

    # Version metadata + active pointer (written even when the DB is down so the
    # committed artifacts are usable by the next daily run).
    version_metadata = {
        "version": version,
        "kind": MODEL_KIND,
        "generated_at": now_jst_iso(),
        "horizon_days": label_cfg_horizon(label_cfg),
        "label_mode": label_cfg.get("label_mode"),
        "label_config": label_cfg,
        "feature_set": PHASE1_FEATURE_COLS,
        "universe": [t["code"] for t in TICKERS],
        "trained_tickers": [e["ticker"] for e in ok_entries],
        "cv_metrics": {"by_ticker": cv_by_ticker, "aggregate": aggregate},
    }

    if ok_entries:
        model_store.save_version_metadata(version, version_metadata)
        model_store.write_active_model(version, {
            "kind": MODEL_KIND,
            "generated_at": version_metadata["generated_at"],
            "horizon_days": version_metadata["horizon_days"],
            "label_mode": version_metadata["label_mode"],
            "n_models": len(ok_entries),
        })
        print(f"weekly: saved {len(ok_entries)} model bundles under version {version}; "
              f"active pointer updated.")
    else:
        print("weekly: no ticker trained successfully; active pointer left unchanged.")

    db_registered = False
    if ok_entries and db.db_enabled():
        try:
            conn = db.connect()
            try:
                db.register_model_version(
                    conn, version,
                    kind=MODEL_KIND,
                    universe=version_metadata["universe"],
                    feature_set=PHASE1_FEATURE_COLS,
                    params={"lgb": "see src.model._LGB_PARAMS", "label_config": label_cfg},
                    cv_metrics={"by_ticker": cv_by_ticker, "aggregate": aggregate},
                    calibration=calibration_map,
                    artifact_uri=model_store.artifact_uri(version),
                    make_active=True,
                )
                for row in quality_rows:
                    db.upsert_model_quality(conn, row)
                db_registered = True
                print(f"weekly: registered {version} in model_registry (active).")
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            print(f"weekly: DB registration skipped (ignored): {type(e).__name__}: {e}")
    elif ok_entries:
        print("weekly: DB disabled; model_registry registration skipped "
              "(artifacts + active pointer written locally).")

    # Refresh docs/model_quality.json so the dashboard reflects the new model.
    if ok_entries:
        try:
            from src.dashboard import export_model_quality
            export_model_quality()
        except Exception as e:  # noqa: BLE001
            print(f"weekly: model_quality export skipped (ignored): {type(e).__name__}: {e}")

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model_version": version if ok_entries else None,
        "active_set": bool(ok_entries),
        "db_registered": db_registered,
        "label_mode": label_cfg.get("label_mode"),
        "horizon_days": version_metadata["horizon_days"],
        "feature_count": len(PHASE1_FEATURE_COLS),
        "aggregate_cv": aggregate,
        "summary": {
            "total_tickers": len(entries),
            "ok_tickers": len(ok_entries),
            "failed_tickers": len(entries) - len(ok_entries),
            "model_ready_tickers": len(ok_entries),
            "gate_passed_tickers": sum(1 for e in ok_entries if e.get("gate_passed")),
        },
        "entries": entries,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Weekly model retrain report exported to {output_path}")
    return 0


def label_cfg_horizon(label_cfg: dict) -> int:
    from src.labels import effective_horizon
    return effective_horizon(label_cfg)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weekly Phase 1 model retrain")
    parser.add_argument("--output", default="docs/weekly_retrain_report.json")
    parser.add_argument("--version", default=None, help="Model version label (default per-ticker-v1-YYYYMMDD)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    version = args.version or _default_version()
    return run_retrain(Path(args.output), version)


if __name__ == "__main__":
    raise SystemExit(main())
