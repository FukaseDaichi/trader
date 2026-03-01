#!/usr/bin/env python3
"""
Daily workflow watchdog checks for freshness and output completeness.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml


def _today_jst_iso() -> str:
    return (datetime.now(UTC) + timedelta(hours=9)).strftime("%Y-%m-%d")


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_enabled_tickers(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []

    result: list[str] = []
    for item in config.get("tickers", []):
        if not isinstance(item, dict):
            continue
        if item.get("enabled", True) and isinstance(item.get("code"), str):
            result.append(item["code"])
    return result


def run_daily_check(args: argparse.Namespace) -> int:
    today = args.today or _today_jst_iso()
    failures: list[str] = []

    state_file = Path(args.state_file)
    history_file = Path(args.history_file)
    report_file = Path(args.report_file)
    tickers_file = Path(args.tickers_file)

    state = _load_json(state_file)
    if state is None:
        failures.append(f"missing_or_invalid:{state_file}")
    else:
        history = state.get("history", [])
        has_today = any(isinstance(item, dict) and item.get("date") == today for item in history)
        if not has_today:
            failures.append("state_not_updated_today")

    history_data = _load_json(history_file)
    if history_data is None:
        failures.append(f"missing_or_invalid:{history_file}")
    else:
        if history_data.get("last_update") in ("", None):
            failures.append("history_data_missing_last_update")
        if not isinstance(history_data.get("tickers"), dict):
            failures.append("history_data_missing_tickers")

    report = _load_json(report_file)
    if report is None:
        failures.append(f"missing_or_invalid:{report_file}")
    else:
        entries = report.get("entries")
        if not isinstance(entries, list):
            failures.append("backtest_report_missing_entries")
        else:
            enabled = _load_enabled_tickers(tickers_file)
            expected = len(enabled)
            if expected > 0 and len(entries) < expected:
                failures.append(f"backtest_entries_short:{len(entries)}/{expected}")

    payload = {
        "date": today,
        "ok": len(failures) == 0,
        "failures": failures,
    }
    print(json.dumps(payload, ensure_ascii=False))

    return 0 if not failures else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Workflow watchdog")
    parser.add_argument("--today", help="YYYY-MM-DD (JST)")
    parser.add_argument("--state-file", default="docs/state.json")
    parser.add_argument("--history-file", default="docs/history_data.json")
    parser.add_argument("--report-file", default="docs/backtest_report.json")
    parser.add_argument("--tickers-file", default="tickers.yml")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_daily_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
