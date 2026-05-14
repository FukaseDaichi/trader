#!/usr/bin/env python3
"""
Weekly model maintenance run.

This intentionally does not update state.json or dashboard JSON. The project
does not persist trained model files, so the weekly job refreshes data and
records whether each ticker can pass the same feature/gate/training path used
by the daily run.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.backtest import evaluate_kpi_gate
from src.config import BACKTEST_GATE_CONFIG, TICKERS
from src.data_loader import load_data, update_data
from src.model import add_features, train_and_predict


def _failure_entry(ticker: dict, reason: str, error: str | None = None, warnings: list[str] | None = None) -> dict:
    return {
        "ticker": ticker["code"],
        "name": ticker["name"],
        "status": "failed",
        "reason": reason,
        "error": error,
        "data_validation_warnings": warnings or [],
    }


def run_retrain(output_path: Path) -> int:
    entries = []

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

            featured = add_features(df)
            if featured.empty:
                entries.append(_failure_entry(ticker, "empty_features", warnings=warnings))
                continue

            gate = evaluate_kpi_gate(featured, BACKTEST_GATE_CONFIG)
            model, prob_up = train_and_predict(featured, runtime_config=BACKTEST_GATE_CONFIG)

            entries.append({
                "ticker": code,
                "name": ticker["name"],
                "status": "ok",
                "model_ready": bool(model is not None),
                "prob_up": float(prob_up),
                "gate_passed": bool(gate.get("passed", False)),
                "gate_reason": gate.get("reason"),
                "failures": gate.get("failures", []),
                "metrics": gate.get("metrics", {}),
                "metrics_tuning": gate.get("metrics_tuning", {}),
                "metrics_holdout": gate.get("metrics_holdout", {}),
                "thresholds": gate.get("thresholds", {}),
                "threshold_optimization": gate.get("threshold_optimization", {}),
                "data_validation_warnings": warnings,
            })
        except Exception as e:
            entries.append(_failure_entry(ticker, "ticker_processing_failed", error=f"{type(e).__name__}: {e}", warnings=warnings))

    ok_entries = [entry for entry in entries if entry.get("status") == "ok"]
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "total_tickers": len(entries),
            "ok_tickers": len(ok_entries),
            "failed_tickers": len(entries) - len(ok_entries),
            "model_ready_tickers": sum(1 for entry in ok_entries if entry.get("model_ready")),
            "gate_passed_tickers": sum(1 for entry in ok_entries if entry.get("gate_passed")),
        },
        "entries": entries,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Weekly model retrain report exported to {output_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weekly model maintenance report")
    parser.add_argument("--output", default="docs/weekly_retrain_report.json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_retrain(Path(args.output))


if __name__ == "__main__":
    raise SystemExit(main())
