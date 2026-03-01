#!/usr/bin/env python3
"""
Weekly universe refresh placeholder/report generator.

This version snapshots current enabled universe and basic data availability.
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

from src.config import TICKERS
from src.data_loader import load_data


def run_universe_refresh(output_path: Path) -> int:
    entries = []
    for item in TICKERS:
        code = item["code"]
        name = item["name"]
        df = load_data(code)
        entries.append({
            "ticker": code,
            "name": name,
            "has_data": bool(df is not None and not df.empty),
            "rows": int(len(df)) if df is not None else 0,
            "latest_date": (
                df["date"].max().strftime("%Y-%m-%d")
                if df is not None and not df.empty and "date" in df.columns
                else None
            ),
        })

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "universe_size": len(entries),
        "active_tickers": [e["ticker"] for e in entries],
        "entries": entries,
        "note": "Phase-1 snapshot. Phase-2 will include broader candidate universe rotation.",
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Universe refresh report exported to {output_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weekly universe refresh report generator")
    parser.add_argument("--output", default="docs/universe_refresh_report.json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_universe_refresh(Path(args.output))


if __name__ == "__main__":
    raise SystemExit(main())
