#!/usr/bin/env python3
"""
Regression guard for the daily workflow schedule (cron is UTC, the system is JST).

GitHub starts scheduled runs hours late, so the daily jobs sit at the earliest
slot that still falls on the JST trading day. Each workflow derives its run
date from ``TZ=Asia/Tokyo date``; a cron that fires before 00:00 JST would make
the run date the previous trading day. These checks pin that rule, the
curation -> core -> retry order, and the nightly refresh staying after the
same-day bar-confirmation cutoff.

Runnable: uv run python tests/test_daily_schedule.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data_loader import SESSION_BAR_CONFIRMED_AT_JST  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
JST = timezone(timedelta(hours=9))
JST_WEEKDAYS = {0, 1, 2, 3, 4}  # Mon-Fri (datetime.weekday)
OLD_CORE_JST_MINUTES = 6 * 60  # 06:00 JST before the 2026-11-14 move


def _crons(name: str) -> list[str]:
    payload = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as boolean True.
    triggers = payload.get("on", payload.get(True))
    return [item["cron"] for item in triggers["schedule"]]


def _cron_dows(field: str) -> set[int]:
    days: set[int] = set()
    for part in field.split(","):
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-"))
            days.update(range(lo, hi + 1))
        else:
            days.add(int(part))
    return days


def _firings_jst(cron: str) -> list[datetime]:
    """JST datetimes of one week of firings for a `M H * * DOW` cron."""
    minute, hour, dom, month, dow = cron.split()
    assert dom == "*" and month == "*", f"unsupported cron {cron!r}"
    cron_days = _cron_dows(dow)  # cron: 0=Sun .. 6=Sat
    week_start = datetime(2026, 11, 15, tzinfo=timezone.utc)  # a Sunday
    out = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        if (day.weekday() + 1) % 7 in cron_days:
            fire = day.replace(hour=int(hour), minute=int(minute))
            out.append(fire.astimezone(JST))
    return out


def _single_jst_minutes(cron: str) -> int:
    times = {(f.hour * 60 + f.minute) for f in _firings_jst(cron)}
    assert len(times) == 1
    return times.pop()


def _assert_on_jst_trading_days(cron: str) -> None:
    firings = _firings_jst(cron)
    assert {f.weekday() for f in firings} == JST_WEEKDAYS, (
        f"{cron!r} does not fire exactly on JST Mon-Fri: "
        f"{[f.strftime('%a %H:%M') for f in firings]}"
    )


def test_daily_preopen_jobs_fire_on_the_jst_trading_day():
    for name in (
        "daily-ticker-curation.yml",
        "daily-preopen-core.yml",
        "daily-preopen-retry.yml",
    ):
        for cron in _crons(name):
            _assert_on_jst_trading_days(cron)
            assert cron.split()[0] != "0", (
                f"{name} {cron!r} fires at the top of the hour (peak delay)"
            )


def test_curation_core_retry_order_and_core_moved_earlier():
    curation = [_single_jst_minutes(c) for c in _crons("daily-ticker-curation.yml")]
    core = [_single_jst_minutes(c) for c in _crons("daily-preopen-core.yml")]
    retry = [_single_jst_minutes(c) for c in _crons("daily-preopen-retry.yml")]
    assert len(core) == 1 and len(retry) == 2
    assert max(curation) + 60 <= core[0], "curation must start >=1h before core"
    assert all(core[0] < r for r in retry), "retry must follow core"
    assert core[0] < OLD_CORE_JST_MINUTES, "core must stay ahead of 06:00 JST"


def test_nightly_refresh_runs_after_bar_confirmation_same_day():
    cutoff = (
        SESSION_BAR_CONFIRMED_AT_JST.hour * 60 + SESSION_BAR_CONFIRMED_AT_JST.minute
    )
    for cron in _crons("nightly-rotating-refresh.yml"):
        _assert_on_jst_trading_days(cron)
        assert _single_jst_minutes(cron) >= cutoff, (
            "nightly refresh would fetch today's bar before it is confirmed"
        )


ALL_TESTS = [
    test_daily_preopen_jobs_fire_on_the_jst_trading_day,
    test_curation_core_retry_order_and_core_moved_earlier,
    test_nightly_refresh_runs_after_bar_confirmation_same_day,
]


def main() -> int:
    failures = 0
    for test in ALL_TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except (AssertionError, KeyError, StopIteration) as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
