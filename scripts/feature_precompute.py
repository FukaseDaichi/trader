#!/usr/bin/env python3
"""
Precompute feature snapshots for active tickers.
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

from src.config import DATA_DIR, TICKERS
from src.data_loader import load_data
from src.model import add_features


def run_precompute(output_path: Path) -> int:
    feature_dir = DATA_DIR / "features"
    feature_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    failed = []
    for item in TICKERS:
        code = item["code"]
        name = item["name"]
        try:
            df = load_data(code)
            if df is None or df.empty:
                entries.append({"ticker": code, "name": name, "status": "missing_data"})
                continue

            featured = add_features(df, dropna=False)
            out_path = feature_dir / f"{code}.parquet"
            featured.to_parquet(out_path)
            entries.append({
                "ticker": code,
                "name": name,
                "status": "ok",
                "rows": int(len(featured)),
                "output_path": str(out_path),
            })
        except Exception as e:
            failed.append({"ticker": code, "name": name, "error": str(e)})

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entries": entries,
        "failed": failed,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Feature precompute report exported to {output_path}")
    return 0 if not failed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Feature precompute for active tickers")
    parser.add_argument("--output", default="docs/feature_precompute_report.json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_precompute(Path(args.output))


if __name__ == "__main__":
    raise SystemExit(main())
