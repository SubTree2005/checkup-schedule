import os
import unittest
from datetime import datetime
from unittest.mock import patch

from apps.backend.checkup_backend.hospital_time import (
    daily_intersections_utc,
    hospital_local_date,
    local_day_bounds_utc,
    next_daily_window_utc,
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


if __name__ == "__main__":
    unittest.main()
