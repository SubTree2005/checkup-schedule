import os
import unittest
from unittest.mock import patch

from apps.backend.checkup_backend.main import resolve_database_url


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


if __name__ == "__main__":
    unittest.main()
