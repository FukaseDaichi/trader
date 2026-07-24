"""Single home for JST date/time helpers.

Japan has no DST, so a fixed UTC+9 offset and ZoneInfo("Asia/Tokyo") agree;
ZoneInfo is the one canonical implementation here.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def now_jst() -> datetime:
    return datetime.now(JST)


def now_jst_iso() -> str:
    return now_jst().isoformat(timespec="seconds")


def today_jst() -> date:
    return now_jst().date()
