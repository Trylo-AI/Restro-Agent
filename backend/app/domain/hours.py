"""Opening hours, last-order cutoff and pause (docs/backend.md: paused / closed / near closing).

`cafes.opening_hours`: {"mon": [["08:00", "23:00"]], ...} in `cafes.timezone`; an empty object means always open.
A window may end after midnight ("18:00"-"02:00").
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.db.repositories.cafes import Cafe

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class Hours:
    open: bool
    paused: bool
    near_closing: bool
    opens_at: str | None  # "8:00 AM tomorrow"; None when open or unknown


def _parse(hhmm: str) -> time:
    h, m = hhmm.split(":")
    return time(int(h) % 24, int(m))


def _windows(hours: dict[str, Any], day: datetime) -> list[tuple[datetime, datetime]]:
    out = []
    for start_s, end_s in hours.get(DAYS[day.weekday()], []) or []:
        start = datetime.combine(day.date(), _parse(start_s), day.tzinfo)
        end = datetime.combine(day.date(), _parse(end_s), day.tzinfo)
        if end <= start:
            end += timedelta(days=1)
        out.append((start, end))
    return out


def _label(at: datetime, now: datetime) -> str:
    when = at.strftime("%I:%M %p").lstrip("0")
    if at.date() == now.date():
        return when
    if at.date() == (now + timedelta(days=1)).date():
        return f"{when} tomorrow"
    return f"{when} {at.strftime('%A')}"


def status(cafe: Cafe, now: datetime | None = None) -> Hours:
    tz = ZoneInfo(cafe.timezone or "Asia/Kolkata")
    now = (now or datetime.now(tz)).astimezone(tz)
    paused = bool(cafe.settings.get("paused"))
    hours = cafe.opening_hours or {}
    if not any(hours.get(d) for d in DAYS):
        return Hours(open=True, paused=paused, near_closing=False, opens_at=None)

    windows = _windows(hours, now - timedelta(days=1)) + _windows(hours, now)
    for start, end in windows:
        if start <= now < end:
            near = now >= end - timedelta(minutes=cafe.cutoff_minutes or 0)
            return Hours(open=True, paused=paused, near_closing=near, opens_at=None)

    for offset in range(0, 8):
        day = now + timedelta(days=offset)
        for start, _ in sorted(_windows(hours, day)):
            if start > now:
                return Hours(open=False, paused=paused, near_closing=False, opens_at=_label(start, now))
    return Hours(open=False, paused=paused, near_closing=False, opens_at=None)
