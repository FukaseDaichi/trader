#!/usr/bin/env python3
"""
Warm up candidate price data for AI ticker curation.

Downloads/refreshes parquet for pool + watchlist tickers that are NOT already in
the enabled universe, writing them to data/watchlist/ (a subdirectory that
src.data_loader.sync_data_files does not archive). This lets new candidates
accumulate history before promotion so the KPI gate does not cold-start.

See specification_document/ai_ticker_curation/02_merge_guardrails.md (§7).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from curation_common import (
    CURATION_DIR,
    WATCHLIST_DIR,
    enabled_codes,
    load_pool,
    load_tickers_config,
    now_jst_iso,
    write_json,
)

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.data_loader import update_data  # noqa: E402


def run(pool_path: Path | None, out_dir: Path) -> int:
    cfg = load_tickers_config()
    enabled = set(enabled_codes(cfg))
    pool_codes = [p["code"] for p in load_pool(pool_path)]
    watch_codes = [
        w["code"]
        for w in (cfg.get("watchlist") or [])
        if isinstance(w, dict) and w.get("code")
    ]

    # Warm pool + watchlist candidates that are not already enabled
    # (enabled tickers get their top-level data refreshed by main.py).
    targets = []
    for code in pool_codes + watch_codes:
        if code in enabled or code in targets:
            continue
        targets.append(code)

    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    ok = 0
    for code in targets:
        try:
            df = update_data(code, dest_dir=out_dir)
        except Exception as exc:  # network/parse errors must not break the job
            print(f"Warmup error for {code}: {exc}")
            df = None
        rows = int(len(df)) if df is not None else 0
        latest = (
            df["date"].max().strftime("%Y-%m-%d")
            if df is not None and not df.empty and "date" in df.columns
            else None
        )
        if rows:
            ok += 1
        results.append({"code": code, "rows": rows, "latest_date": latest})

    write_json(
        CURATION_DIR / "warmup_report.json",
        {
            "generated_at": now_jst_iso(),
            "out_dir": str(out_dir),
            "targets": len(targets),
            "succeeded": ok,
            "entries": results,
        },
    )
    print(f"Warmup complete: {ok}/{len(targets)} candidates refreshed into {out_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Warm up candidate data for AI ticker curation"
    )
    p.add_argument("--pool", default=None, help="path to curation_pool.yml")
    p.add_argument(
        "--out-dir", default=None, help="output dir (default: data/watchlist)"
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    pool_path = Path(args.pool) if args.pool else None
    out_dir = Path(args.out_dir) if args.out_dir else WATCHLIST_DIR
    return run(pool_path, out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
