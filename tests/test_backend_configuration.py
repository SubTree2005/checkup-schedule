import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import inspect, text

from apps.backend.checkup_backend.database import Base, build_engine
from apps.backend.checkup_backend.main import ensure_compatible_columns, resolve_database_url


class BackendConfigurationTest(unittest.TestCase):
    def test_explicit_database_url_has_priority(self):
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///environment.db"}, clear=True):
            self.assertEqual(resolve_database_url("sqlite:///explicit.db"), "sqlite:///explicit.db")

    def test_wechat_mysql_environment_builds_encoded_url(self):
        with patch.dict(
            os.environ,
            {
                "MYSQL_ADDRESS": "10.30.106.234:3306",
                "MYSQL_DATABASE": "checkup_schedule",
                "MYSQL_USERNAME": "checkup_app",
                "MYSQL_PASSWORD": "p@ss:/?word",
            },
            clear=True,
        ):
            self.assertEqual(
                resolve_database_url(),
                "mysql+pymysql://checkup_app:p%40ss%3A%2F%3Fword@10.30.106.234:3306/checkup_schedule?charset=utf8mb4",
            )

    def test_partial_wechat_mysql_environment_is_rejected(self):
        with patch.dict(os.environ, {"MYSQL_ADDRESS": "10.30.106.234:3306"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "MYSQL_USERNAME, MYSQL_PASSWORD"):
                resolve_database_url()

    def test_compatible_upgrade_restores_queue_snapshot_lookup_index(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.db"
            engine = build_engine(f"sqlite:///{database_path}")
            try:
                Base.metadata.create_all(bind=engine)
                with engine.begin() as connection:
                    connection.execute(text("DROP INDEX ix_queue_snapshot_item_valid_created"))
                self.assertNotIn(
                    "ix_queue_snapshot_item_valid_created",
                    {index["name"] for index in inspect(engine).get_indexes("queue_snapshot")},
                )

                ensure_compatible_columns(engine)

                self.assertIn(
                    "ix_queue_snapshot_item_valid_created",
                    {index["name"] for index in inspect(engine).get_indexes("queue_snapshot")},
                )
            finally:
                engine.dispose()

    def test_compatible_upgrade_adds_and_backfills_appointment_time(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy-plan.db"
            engine = build_engine(f"sqlite:///{database_path}")
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "CREATE TABLE user_status_info ("
                            "recordID VARCHAR(64) PRIMARY KEY, profileData JSON)"
                        )
                    )
                    connection.execute(
                        text(
                            "CREATE TABLE exam_plan ("
                            "planID VARCHAR(64) PRIMARY KEY, hospitalID VARCHAR(64), "
                            "recordID VARCHAR(64), generateTime DATETIME, planStatus VARCHAR(20))"
                        )
                    )
                    connection.execute(
                        text(
                            "INSERT INTO user_status_info (recordID, profileData) "
                            "VALUES ('record-1', :profile)"
                        ),
                        {"profile": '{"appointmentAt":"2026-09-04T01:30:00Z"}'},
                    )
                    connection.execute(
                        text(
                            "INSERT INTO exam_plan "
                            "(planID, hospitalID, recordID, generateTime, planStatus) "
                            "VALUES ('plan-1', 'hospital-1', 'record-1', "
                            "'2026-09-03 01:00:00', '待执行')"
                        )
                    )

                ensure_compatible_columns(engine)

                self.assertIn(
                    "appointmentAt",
                    {column["name"] for column in inspect(engine).get_columns("exam_plan")},
                )
                self.assertIn(
                    "ix_plan_hospital_appointment_status",
                    {index["name"] for index in inspect(engine).get_indexes("exam_plan")},
                )
                with engine.connect() as connection:
                    appointment = connection.scalar(
                        text("SELECT appointmentAt FROM exam_plan WHERE planID = 'plan-1'")
                    )
                self.assertTrue(str(appointment).startswith("2026-09-04 01:30:00"))
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
