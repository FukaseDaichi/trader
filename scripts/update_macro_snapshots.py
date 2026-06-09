#!/usr/bin/env python3
"""
Phase 1 macro snapshot updater.

Fetches market context series (USD/JPY, TOPIX, Nikkei, Nikkei VI, JGB10y),
folds in the qualitative bias from docs/curation/macro_latest.json, writes the
derived macro panel to data/macro/macro_panel.parquet, and (when the DB is
enabled) upserts the latest snapshot into macro_snapshots.

Best-effort: a missing series or an unreachable DB never fails the run, so the
daily pipeline that runs afterwards is never blocked (roadmap §5 risk note).

Usage:
  uv run python scripts/update_macro_snapshots.py
  uv run python scripts/update_macro_snapshots.py --as-of 2026-06-09
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db, macro  # noqa: E402
from scripts.curation_common import CURATION_DIR, read_json, today_jst_iso  # noqa: E402


def _load_qualitative() -> dict:
    """Pull market_bias / regime from the weekly macro screen output."""
    payload = read_json(CURATION_DIR / "macro_latest.json") or {}
    regime = payload.get("regime")
    return {
        "market_bias": payload.get("market_bias"),
        "regime": regime if isinstance(regime, str) else None,
        "as_of": payload.get("as_of"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Update macro snapshot parquet + DB")
    parser.add_argument("--as-of", default=today_jst_iso(),
                        help="JST date label (informational).")
    args = parser.parse_args()

    qualitative = _load_qualitative()
    series_data = macro.fetch_all_series()
    panel = macro.build_macro_panel(series_data, qualitative=qualitative)

    if panel is None or panel.empty:
        print("macro: no series fetched; leaving existing panel untouched.")
        return 0

    path = macro.save_macro_panel(panel)
    print(f"macro: panel saved to {path} ({len(panel)} rows, "
          f"series={sorted(series_data.keys())}).")

    snapshot = macro.latest_snapshot_row(panel, qualitative=qualitative)
    if snapshot is None:
        print("macro: no snapshot row to persist.")
        return 0

    if not db.db_enabled():
        print(f"macro: DB disabled; snapshot for {snapshot['date']} not persisted "
              f"(parquet updated). as-of={args.as_of}")
        return 0

    try:
        conn = db.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"macro: DB unreachable, snapshot not persisted (ignored): "
              f"{type(exc).__name__}: {exc}")
        return 0

    try:
        snapshot["raw"] = {k: v for k, v in qualitative.items() if v is not None}
        db.upsert_macro_snapshot(conn, snapshot)
        print(f"macro: upserted macro_snapshots for {snapshot['date']}.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"macro: snapshot upsert failed (ignored): {type(exc).__name__}: {exc}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
