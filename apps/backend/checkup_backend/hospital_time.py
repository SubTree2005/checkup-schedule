from __future__ import annotations

import os
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


DEFAULT_HOSPITAL_TIMEZONE = "Asia/Shanghai"


def hospital_timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("HOSPITAL_TIMEZONE", DEFAULT_HOSPITAL_TIMEZONE))


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _parse_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def hospital_local_date(now_utc: datetime) -> date:
    return _as_utc(now_utc).astimezone(hospital_timezone()).date()


def local_day_bounds_utc(now_utc: datetime) -> tuple[datetime, datetime]:
    timezone = hospital_timezone()
    local_day = hospital_local_date(now_utc)
    start = datetime.combine(local_day, time.min, tzinfo=timezone)
    end = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=timezone)
    return _utc_naive(start), _utc_naive(end)


def _daily_window_utc(local_day: date, start_text: str, end_text: str) -> tuple[datetime, datetime]:
    timezone = hospital_timezone()
    start = datetime.combine(local_day, _parse_time(start_text), tzinfo=timezone)
    end = datetime.combine(local_day, _parse_time(end_text), tzinfo=timezone)
    if end <= start:
        end += timedelta(days=1)
    return _utc_naive(start), _utc_naive(end)


def next_daily_window_utc(now_utc: datetime, start_text: str, end_text: str) -> tuple[datetime, datetime]:
    now = _utc_naive(_as_utc(now_utc))
    local_day = hospital_local_date(now)
    start, end = _daily_window_utc(local_day, start_text, end_text)
    if now >= end:
        start, end = _daily_window_utc(local_day + timedelta(days=1), start_text, end_text)
    return max(now, start), end


def daily_intersections_utc(
    bound_start_utc: datetime,
    bound_end_utc: datetime,
    start_text: str,
    end_text: str,
) -> tuple[tuple[datetime, datetime], ...]:
    """Intersect a recurring local-time window with one UTC planning interval."""
    if bound_start_utc >= bound_end_utc:
        return ()
    first_day = hospital_local_date(bound_start_utc) - timedelta(days=1)
    last_day = hospital_local_date(bound_end_utc) + timedelta(days=1)
    intersections: list[tuple[datetime, datetime]] = []
    local_day = first_day
    while local_day <= last_day:
        start, end = _daily_window_utc(local_day, start_text, end_text)
        intersection_start = max(bound_start_utc, start)
        intersection_end = min(bound_end_utc, end)
        if intersection_start < intersection_end:
            intersections.append((intersection_start, intersection_end))
        local_day += timedelta(days=1)
    return tuple(intersections)
