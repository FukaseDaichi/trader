#!/usr/bin/env python3
"""
Nightly rotating data refresh for a subset of active tickers.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.config import TICKERS
from src.data_loader import update_data


def _today_jst_weekday() -> int:
    # Monday=0 .. Sunday=6
    return (datetime.now(UTC) + timedelta(hours=9)).weekday()


def run_rotating_refresh(output_path: Path, buckets: int) -> int:
    weekday = _today_jst_weekday()
    selected = []
    for idx, ticker in enumerate(TICKERS):
        if idx % buckets == weekday % buckets:
            selected.append(ticker)

    refreshed = []
    failed = []
    for item in selected:
        code = item["code"]
        name = item["name"]
        try:
            df = update_data(code)
            refreshed.append({
                "ticker": code,
                "name": name,
                "status": "ok" if df is not None else "no_data",
            })
        except Exception as e:
            failed.append({"ticker": code, "name": name, "error": str(e)})

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "bucket_count": buckets,
        "weekday_jst": weekday,
        "selected_count": len(selected),
        "refreshed": refreshed,
        "failed": failed,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Rotating refresh report exported to {output_path}")
    return 0 if not failed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nightly rotating refresh")
    parser.add_argument("--output", default="docs/rotating_refresh_report.json")
    parser.add_argument("--buckets", type=int, default=5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    buckets = max(1, int(args.buckets))
    return run_rotating_refresh(Path(args.output), buckets=buckets)


if __name__ == "__main__":
    raise SystemExit(main())
