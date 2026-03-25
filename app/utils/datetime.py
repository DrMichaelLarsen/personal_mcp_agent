from __future__ import annotations

from datetime import date, datetime, time, timedelta


def combine_day_and_hour(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour=hour, minute=0))


def add_minutes(value: datetime, minutes: int) -> datetime:
    return value + timedelta(minutes=minutes)


def minutes_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))
