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
    index_file = Path(args.index_file if args.index_file else args.history_file)
    ticker_dir = Path(args.ticker_dir)
    report_file = Path(args.report_file)
    tickers_file = Path(args.tickers_file)
    max_index_bytes = int(args.max_index_bytes)
    max_ticker_total_bytes = int(args.max_ticker_total_bytes)

    state = _load_json(state_file)
    if state is None:
        failures.append(f"missing_or_invalid:{state_file}")
    else:
        history = state.get("history", [])
        has_today = any(isinstance(item, dict) and item.get("date") == today for item in history)
        if not has_today:
            failures.append("state_not_updated_today")

    index_data = _load_json(index_file)
    if index_data is None:
        failures.append(f"missing_or_invalid:{index_file}")
    else:
        if index_file.exists() and index_file.stat().st_size > max_index_bytes:
            failures.append(f"dashboard_index_too_large:{index_file.stat().st_size}>{max_index_bytes}")

        last_update = index_data.get("last_update")
        if not isinstance(last_update, str) or not last_update:
            failures.append("dashboard_index_missing_last_update")
        elif not last_update.startswith(today):
            failures.append("dashboard_index_not_updated_today")

        index_tickers = index_data.get("tickers")
        if not isinstance(index_tickers, dict):
            failures.append("dashboard_index_missing_tickers")
        else:
            enabled = _load_enabled_tickers(tickers_file)
            expected = len(enabled)
            if expected > 0 and len(index_tickers) < expected:
                failures.append(f"dashboard_index_tickers_short:{len(index_tickers)}/{expected}")

            missing_codes = [code for code in enabled if code not in index_tickers]
            if missing_codes:
                failures.append(f"dashboard_index_missing_codes:{','.join(missing_codes)}")

            total_ticker_bytes = 0
            for code in enabled:
                ticker_file = ticker_dir / f"{code}.json"
                ticker_payload = _load_json(ticker_file)
                if ticker_payload is None:
                    failures.append(f"missing_or_invalid:{ticker_file}")
                    continue
                total_ticker_bytes += ticker_file.stat().st_size
                if not isinstance(ticker_payload.get("data"), list):
                    failures.append(f"ticker_data_missing_rows:{code}")

            if total_ticker_bytes > max_ticker_total_bytes:
                failures.append(
                    f"ticker_payloads_too_large:{total_ticker_bytes}>{max_ticker_total_bytes}"
                )

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
    parser.add_argument("--index-file", default="docs/dashboard_index.json")
    parser.add_argument("--ticker-dir", default="docs/tickers")
    # Backward-compatible alias used by older calls.
    parser.add_argument("--history-file", default="docs/dashboard_index.json", help=argparse.SUPPRESS)
    parser.add_argument("--report-file", default="docs/backtest_report.json")
    parser.add_argument("--tickers-file", default="tickers.yml")
    parser.add_argument("--max-index-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-ticker-total-bytes", type=int, default=10_000_000)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_daily_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
