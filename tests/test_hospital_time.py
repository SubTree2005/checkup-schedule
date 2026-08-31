import os
import unittest
from datetime import datetime
from unittest.mock import patch

from apps.backend.checkup_backend.hospital_time import (
    daily_intersections_utc,
    hospital_local_date,
    local_day_bounds_utc,
    next_daily_window_utc,
    next_daily_windows_utc,
    parse_open_time_ranges,
)


class HospitalLocalTimeTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"HOSPITAL_TIMEZONE": "Asia/Shanghai"})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_local_day_uses_shanghai_midnight(self):
        now = datetime(2026, 8, 31, 17, 0)
        self.assertEqual(hospital_local_date(now).isoformat(), "2026-09-01")
        self.assertEqual(
            local_day_bounds_utc(now),
            (datetime(2026, 8, 31, 16, 0), datetime(2026, 9, 1, 16, 0)),
        )

    def test_daily_window_is_converted_to_utc(self):
        now = datetime(2026, 8, 31, 0, 30)
        self.assertEqual(
            next_daily_window_utc(now, "08:00", "17:00"),
            (datetime(2026, 8, 31, 0, 30), datetime(2026, 8, 31, 9, 0)),
        )
        self.assertEqual(
            daily_intersections_utc(
                datetime(2026, 8, 31, 0, 30),
                datetime(2026, 8, 31, 9, 0),
                "10:00",
                "10:30",
            ),
            ((datetime(2026, 8, 31, 2, 0), datetime(2026, 8, 31, 2, 30)),),
        )

    def test_multiple_windows_keep_the_local_lunch_break(self):
        ranges = parse_open_time_ranges("工作日08:00-12:00,13:30-17:00；急诊24小时")
        self.assertEqual(ranges, (("08:00", "12:00"), ("13:30", "17:00")))
        self.assertEqual(
            next_daily_windows_utc(datetime(2026, 8, 31, 3, 0), ranges, weekdays_only=True),
            (
                (datetime(2026, 8, 31, 3, 0), datetime(2026, 8, 31, 4, 0)),
                (datetime(2026, 8, 31, 5, 30), datetime(2026, 8, 31, 9, 0)),
            ),
        )

    def test_weekday_schedule_skips_the_weekend(self):
        self.assertEqual(
            next_daily_windows_utc(
                datetime(2026, 9, 4, 10, 0),
                (("08:00", "17:00"),),
                weekdays_only=True,
            ),
            ((datetime(2026, 9, 7, 0, 0), datetime(2026, 9, 7, 9, 0)),),
        )

    def test_full_day_text_is_supported(self):
        ranges = parse_open_time_ranges("全天开放")
        self.assertEqual(ranges, (("00:00", "00:00"),))
        self.assertEqual(
            next_daily_windows_utc(datetime(2026, 8, 31, 3, 0), ranges),
            ((datetime(2026, 8, 31, 3, 0), datetime(2026, 8, 31, 16, 0)),),
        )
        with self.assertRaisesRegex(ValueError, "HH:MM-HH:MM"):
            parse_open_time_ranges("早八点到晚五点")


if __name__ == "__main__":
    unittest.main()
