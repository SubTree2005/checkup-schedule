from __future__ import annotations

import os
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


DEFAULT_HOSPITAL_TIMEZONE = "Asia/Shanghai"
TIME_TEXT = r"(?:[01]\d|2[0-3]):[0-5]\d"
OPEN_TIME_RANGE_PATTERN = re.compile(
    rf"({TIME_TEXT})\s*(?:-|–|—|~|～|至)\s*({TIME_TEXT})"
)


def hospital_timezone() -> ZoneInfo:
    return ZoneInfo(os.getenv("HOSPITAL_TIMEZONE", DEFAULT_HOSPITAL_TIMEZONE))


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def _parse_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def parse_open_time_ranges(value: str) -> tuple[tuple[str, str], ...]:
    """Extract ordered daily time ranges from a human-readable hospital schedule."""
    ranges = tuple(dict.fromkeys(OPEN_TIME_RANGE_PATTERN.findall(value or "")))
    if ranges:
        return ranges
    if re.search(r"(?:24\s*小时|全天)", value or ""):
        return (("00:00", "00:00"),)
    raise ValueError("开放时间至少需要一个 HH:MM-HH:MM 时段")


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
    return next_daily_windows_utc(now_utc, ((start_text, end_text),))[0]


def next_daily_windows_utc(
    now_utc: datetime,
    ranges: Sequence[tuple[str, str]],
    *,
    weekdays_only: bool = False,
) -> tuple[tuple[datetime, datetime], ...]:
    """Return the remaining windows in the current or next eligible local service day."""
    if not ranges:
        raise ValueError("至少需要一个开放时段")
    now = _utc_naive(_as_utc(now_utc))
    local_today = hospital_local_date(now)

    previous_day = local_today - timedelta(days=1)
    if not weekdays_only or previous_day.weekday() < 5:
        active_overnight = []
        for start_text, end_text in ranges:
            if start_text < end_text:
                continue
            start, end = _daily_window_utc(previous_day, start_text, end_text)
            if start <= now < end:
                active_overnight.append((now, end))
        if active_overnight:
            return _merge_windows(active_overnight)

    for day_offset in range(8):
        local_day = local_today + timedelta(days=day_offset)
        if weekdays_only and local_day.weekday() >= 5:
            continue
        windows = []
        for start_text, end_text in ranges:
            start, end = _daily_window_utc(local_day, start_text, end_text)
            if end > now:
                windows.append((max(now, start), end))
        if windows:
            return _merge_windows(windows)
    raise ValueError("未来一周没有可用开放时段")


def _merge_windows(
    windows: Sequence[tuple[datetime, datetime]],
) -> tuple[tuple[datetime, datetime], ...]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(windows):
        if not merged or merged[-1][1] < start:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return tuple(merged)


def daily_intersections_utc(
    bound_start_utc: datetime,
    bound_end_utc: datetime,
    start_text: str,
    end_text: str,
) -> tuple[tuple[datetime, datetime], ...]:
    """Intersect a recurring local-time window with one UTC planning interval."""
    bound_start_utc = _utc_naive(_as_utc(bound_start_utc))
    bound_end_utc = _utc_naive(_as_utc(bound_end_utc))
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
