#!/usr/bin/env python3
"""
Quarterly stress test report.

Runs KPI gate under stressed cost/slippage assumptions.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.backtest import evaluate_kpi_gate
from src.config import BACKTEST_GATE_CONFIG, TICKERS
from src.data_loader import load_data
from src.model import add_features


def run_stress_test(output_path: Path, cost_bps: float, slippage_bps: float) -> int:
    stressed_config = copy.deepcopy(BACKTEST_GATE_CONFIG)
    stressed_config["cost_bps"] = float(cost_bps)
    stressed_config["slippage_bps"] = float(slippage_bps)

    entries = []
    for item in TICKERS:
        code = item["code"]
        name = item["name"]
        df = load_data(code)
        if df is None or df.empty:
            entries.append({"ticker": code, "name": name, "status": "missing_data"})
            continue

        featured = add_features(df)
        if featured.empty:
            entries.append({"ticker": code, "name": name, "status": "empty_features"})
            continue

        gate = evaluate_kpi_gate(featured, stressed_config)
        entries.append({
            "ticker": code,
            "name": name,
            "status": "ok",
            "passed": gate["passed"],
            "reason": gate["reason"],
            "failures": gate["failures"],
            "metrics": gate["metrics"],
            "metrics_tuning": gate.get("metrics_tuning", {}),
            "metrics_holdout": gate.get("metrics_holdout", {}),
        })

    ok_entries = [e for e in entries if e.get("status") == "ok"]
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "assumption": {"cost_bps": cost_bps, "slippage_bps": slippage_bps},
        "summary": {
            "total": len(entries),
            "ok": len(ok_entries),
            "passed": sum(1 for e in ok_entries if e.get("passed")),
            "failed": sum(1 for e in ok_entries if not e.get("passed")),
        },
        "entries": entries,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Stress test report exported to {output_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quarterly stress test")
    parser.add_argument("--output", default="docs/stress_test_report.json")
    parser.add_argument("--cost-bps", type=float, default=20.0)
    parser.add_argument("--slippage-bps", type=float, default=10.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_stress_test(Path(args.output), cost_bps=args.cost_bps, slippage_bps=args.slippage_bps)


if __name__ == "__main__":
    raise SystemExit(main())
