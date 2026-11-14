#!/usr/bin/env python3
"""
export_dashboard_data must survive one ticker's unreadable price data.

The daily job runs update_dashboard() after the LINE digest and the DB write;
a crash there skipped the commit step and made the retry run re-send the
digest. A bad parquet now costs only that ticker's chart rows.

Runnable: uv run python tests/test_dashboard_export_resilience.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.dashboard as dash  # noqa: E402


def _frame():
    dates = pd.bdate_range("2026-09-01", periods=60)
    close = pd.Series(range(100, 160), dtype="float64")
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1_000_000.0,
        }
    )


def test_one_bad_ticker_does_not_abort_export():
    tickers = [
        {"code": "7011.JP", "name": "A"},
        {"code": "6501.JP", "name": "B"},
    ]

    def fake_load(code):
        if code == "7011.JP":
            raise OSError("corrupt parquet")
        return _frame()

    saved = {
        name: getattr(dash, name)
        for name in (
            "TICKERS",
            "load_data",
            "STATE_FILE",
            "TICKER_EXPORT_DIR",
            "DASHBOARD_INDEX_FILE",
            "LEGACY_HISTORY_FILE",
            "_sync_dev_public_assets",
        )
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state = tmp / "state.json"
        state.write_text(
            json.dumps({"history": [], "last_update": "2026-11-16 01:10:00"}),
            encoding="utf-8",
        )
        dash.TICKERS = tickers
        dash.load_data = fake_load
        dash.STATE_FILE = state
        dash.TICKER_EXPORT_DIR = tmp / "tickers"
        dash.DASHBOARD_INDEX_FILE = tmp / "dashboard_index.json"
        dash.LEGACY_HISTORY_FILE = tmp / "history_data.json"
        dash._sync_dev_public_assets = lambda: None
        try:
            dash.export_dashboard_data()
            index = json.loads(dash.DASHBOARD_INDEX_FILE.read_text(encoding="utf-8"))
            assert set(index["tickers"]) == {"7011.JP", "6501.JP"}
            assert index["tickers"]["7011.JP"]["rows"] == 0
            assert index["tickers"]["6501.JP"]["rows"] > 0
            bad = json.loads((tmp / "tickers" / "7011.JP.json").read_text("utf-8"))
            assert bad["data"] == []
        finally:
            for name, value in saved.items():
                setattr(dash, name, value)


ALL_TESTS = [test_one_bad_ticker_does_not_abort_export]


def main() -> int:
    failures = 0
    for t in ALL_TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
