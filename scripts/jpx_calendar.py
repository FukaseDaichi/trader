#!/usr/bin/env python3
"""
JPX calendar helper.

Features:
1) Decide whether a given JST date is an open trading day.
2) Sync holiday cache file from public holiday source.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Dict

import requests

TOKYO_TZ = "Asia/Tokyo"
DEFAULT_CACHE_PATH = Path("data/jpx_holidays.json")
DEFAULT_HOLIDAY_SOURCE = "https://holidays-jp.github.io/api/v1/date.json"


@dataclass(frozen=True)
class OpenDayResult:
    target_date: str
    is_open: bool
    reason: str


def _parse_yyyy_mm_dd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _today_jst() -> date:
    now = datetime.now().astimezone()
    if getattr(now.tzinfo, "key", None) == TOKYO_TZ:
        return now.date()
    # Environment timezone may not be JST; use offset conversion.
    return (datetime.now(UTC) + timedelta(hours=9)).date()


def _load_cache(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    if isinstance(payload, dict) and "holidays" in payload and isinstance(payload["holidays"], dict):
        return {str(k): str(v) for k, v in payload["holidays"].items()}
    if isinstance(payload, dict):
        return {str(k): str(v) for k, v in payload.items()}
    return {}


def _fetch_public_holidays(source_url: str, timeout_sec: int = 15) -> Dict[str, str]:
    response = requests.get(source_url, timeout=timeout_sec)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Holiday API response must be a JSON object")
    return {str(k): str(v) for k, v in payload.items()}


def _add_known_exchange_closures(holidays: Dict[str, str], years: list[int]) -> Dict[str, str]:
    enriched = dict(holidays)
    for y in years:
        enriched.setdefault(f"{y}-01-02", "Exchange New Year Holiday")
        enriched.setdefault(f"{y}-01-03", "Exchange New Year Holiday")
        enriched.setdefault(f"{y}-12-31", "Exchange Year-End Holiday")
    return enriched


def _is_weekday(d: date) -> bool:
    return d.weekday() <= 4


def _compute_open_day(target: date, holidays: Dict[str, str]) -> OpenDayResult:
    key = target.isoformat()
    if not _is_weekday(target):
        return OpenDayResult(target_date=key, is_open=False, reason="weekend")
    if key in holidays:
        return OpenDayResult(target_date=key, is_open=False, reason=f"holiday:{holidays[key]}")
    return OpenDayResult(target_date=key, is_open=True, reason="weekday_non_holiday")


def _write_github_output(result: OpenDayResult) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"is_open={'true' if result.is_open else 'false'}\n")
        f.write(f"market_reason={result.reason}\n")
        f.write(f"target_date={result.target_date}\n")


def cmd_is_open(args: argparse.Namespace) -> int:
    target = _parse_yyyy_mm_dd(args.date) if args.date else _today_jst()
    cache_path = Path(args.cache_path)
    holidays = _load_cache(cache_path)

    # Try remote first. Fallback to local cache for resilience.
    try:
        remote = _fetch_public_holidays(args.source_url)
        years = [target.year - 1, target.year, target.year + 1]
        holidays = _add_known_exchange_closures(remote, years)
        if args.write_cache:
            _persist_cache(cache_path, holidays, source_url=args.source_url)
    except Exception:
        if not holidays:
            years = [target.year - 1, target.year, target.year + 1]
            holidays = _add_known_exchange_closures({}, years)

    result = _compute_open_day(target, holidays)

    payload = {
        "target_date": result.target_date,
        "is_open": result.is_open,
        "reason": result.reason,
    }
    print(json.dumps(payload, ensure_ascii=False))

    if args.github_output:
        _write_github_output(result)
    return 0


def _persist_cache(path: Path, holidays: Dict[str, str], source_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_url": source_url,
        "holidays": dict(sorted(holidays.items())),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_sync(args: argparse.Namespace) -> int:
    cache_path = Path(args.cache_path)
    holidays = _fetch_public_holidays(args.source_url, timeout_sec=args.timeout_sec)

    today = _today_jst()
    min_year = today.year - args.years_back
    max_year = today.year + args.years_forward
    target_years = list(range(min_year, max_year + 1))
    holidays = _add_known_exchange_closures(holidays, target_years)
    filtered = {k: v for k, v in holidays.items() if min_year <= int(k[:4]) <= max_year}

    _persist_cache(cache_path, filtered, source_url=args.source_url)

    report = {
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cache_path": str(cache_path),
        "source_url": args.source_url,
        "years": {"from": min_year, "to": max_year},
        "holiday_count": len(filtered),
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JPX calendar helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_is_open = subparsers.add_parser("is-open", help="Check if target date is open day")
    p_is_open.add_argument("--date", help="Target date in YYYY-MM-DD (default: today JST)")
    p_is_open.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    p_is_open.add_argument("--source-url", default=DEFAULT_HOLIDAY_SOURCE)
    p_is_open.add_argument("--github-output", action="store_true")
    p_is_open.add_argument("--write-cache", action="store_true")
    p_is_open.set_defaults(func=cmd_is_open)

    p_sync = subparsers.add_parser("sync", help="Sync holiday cache")
    p_sync.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    p_sync.add_argument("--source-url", default=DEFAULT_HOLIDAY_SOURCE)
    p_sync.add_argument("--years-back", type=int, default=2)
    p_sync.add_argument("--years-forward", type=int, default=3)
    p_sync.add_argument("--timeout-sec", type=int, default=20)
    p_sync.set_defaults(func=cmd_sync)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
