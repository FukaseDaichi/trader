#!/usr/bin/env python3
"""
Monthly portfolio/system audit report.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
import sys

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.backtest import evaluate_kpi_gate
from src.config import BACKTEST_GATE_CONFIG, TICKERS
from src.data_loader import load_data
from src.model import add_features


def _safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(np.mean(values))


def run_audit(output_path: Path) -> int:
    entries = []
    for ticker in TICKERS:
        code = ticker["code"]
        name = ticker["name"]
        df = load_data(code)
        if df is None or df.empty:
            entries.append({
                "ticker": code,
                "name": name,
                "status": "missing_data",
            })
            continue

        featured = add_features(df)
        if featured.empty:
            entries.append({
                "ticker": code,
                "name": name,
                "status": "empty_features",
            })
            continue

        gate = evaluate_kpi_gate(featured, BACKTEST_GATE_CONFIG)
        entries.append({
            "ticker": code,
            "name": name,
            "status": "ok",
            "passed": gate.get("passed", False),
            "reason": gate.get("reason"),
            "failures": gate.get("failures", []),
            "metrics": gate.get("metrics", {}),
            "metrics_tuning": gate.get("metrics_tuning", {}),
            "metrics_holdout": gate.get("metrics_holdout", {}),
            "thresholds": gate.get("thresholds", {}),
            "threshold_optimization": gate.get("threshold_optimization", {}),
        })

    ok_entries = [e for e in entries if e.get("status") == "ok"]
    metric_rows = [e.get("metrics", {}) for e in ok_entries]

    summary = {
        "total_tickers": len(entries),
        "ok_tickers": len(ok_entries),
        "passed_tickers": sum(1 for e in ok_entries if e.get("passed")),
        "failed_tickers": sum(1 for e in ok_entries if not e.get("passed")),
        "avg_cagr": _safe_mean([float(m.get("cagr", 0.0)) for m in metric_rows]),
        "avg_max_drawdown": _safe_mean([float(m.get("max_drawdown", 0.0)) for m in metric_rows]),
        "avg_sharpe": _safe_mean([float(m.get("sharpe", 0.0)) for m in metric_rows]),
        "avg_expectancy": _safe_mean([float(m.get("expectancy", 0.0)) for m in metric_rows]),
        "avg_turnover": _safe_mean([float(m.get("turnover", 0.0)) for m in metric_rows]),
    }

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "entries": entries,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Monthly audit exported to {output_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monthly audit report generator")
    parser.add_argument("--output", default="docs/monthly_audit.json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_audit(Path(args.output))


if __name__ == "__main__":
    raise SystemExit(main())
