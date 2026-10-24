from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.repositories.cafes import Cafe
from app.domain import hours

IST = ZoneInfo("Asia/Kolkata")


def cafe(opening: dict[str, list[list[str]]], cutoff: int = 30, paused: bool = False) -> Cafe:
    return Cafe(id="c", name="Kaapi", status="live", phone_number_id="p", display_phone_number=None, token_ref=None,
                timezone="Asia/Kolkata", opening_hours=opening, cutoff_minutes=cutoff, settings={"paused": paused})


DAILY = {d: [["08:00", "23:00"]] for d in hours.DAYS}


def at(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=IST)  # 26 Oct 2026 is a Monday


def test_open_closed_and_near_closing() -> None:
    assert hours.status(cafe(DAILY), at(26, 12)).open
    near = hours.status(cafe(DAILY), at(26, 22, 40))
    assert near.open and near.near_closing
    closed = hours.status(cafe(DAILY), at(26, 23, 30))
    assert not closed.open and closed.opens_at == "08:00 AM tomorrow".lstrip("0").replace("08", "8")


def test_before_opening_today() -> None:
    s = hours.status(cafe(DAILY), at(26, 6))
    assert not s.open and s.opens_at == "8:00 AM"


def test_window_past_midnight() -> None:
    late = {d: [["18:00", "02:00"]] for d in hours.DAYS}
    assert hours.status(cafe(late), at(27, 1)).open
    assert not hours.status(cafe(late), at(27, 3)).open


def test_empty_hours_means_always_open_and_pause_is_reported() -> None:
    s = hours.status(cafe({}, paused=True), at(26, 3))
    assert s.open and s.paused


def test_closed_day_points_to_the_next_open_day() -> None:
    weekdays_only = {d: [["09:00", "17:00"]] for d in hours.DAYS[:5]}
    s = hours.status(cafe(weekdays_only), at(31, 12))  # Saturday
    assert not s.open and s.opens_at == "9:00 AM Monday"
