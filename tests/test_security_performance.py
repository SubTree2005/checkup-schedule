import json
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from apps.backend.checkup_backend.agent_api import _read_json_response
from apps.backend.checkup_backend.demo_patients import _ordered_package_items
from apps.backend.checkup_backend.exam_constraints import validate_prerequisite_graph
from apps.backend.checkup_backend.main import create_app, resolve_allowed_origins
from apps.backend.checkup_backend.schemas import GISUpload, MAX_GIS_FEATURES
from apps.backend.checkup_backend.security import hash_password, verify_login_password, verify_password
from checkup_scheduler.wait_prediction import _fcfs_wait_minutes


class SecurityBoundaryTest(unittest.TestCase):
    def _app(self, database_path: Path):
        static_dir = Path(__file__).resolve().parents[1] / "apps" / "admin-web"
        return create_app(database_url=f"sqlite:///{database_path}", static_dir=static_dir)

    def test_sensitive_responses_have_security_and_no_store_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(self._app(Path(directory) / "headers.db")) as client:
                api_response = client.get("/api/health")
                self.assertEqual(api_response.status_code, 200)
                self.assertEqual(api_response.headers["cache-control"], "no-store")
                self.assertEqual(api_response.headers["x-content-type-options"], "nosniff")
                self.assertEqual(api_response.headers["x-frame-options"], "DENY")

                admin_response = client.get("/")
                self.assertEqual(admin_response.status_code, 200)
                content_security_policy = admin_response.headers["content-security-policy"]
                self.assertIn("frame-ancestors 'none'", content_security_policy)
                self.assertNotIn("'unsafe-inline'", content_security_policy)

    def test_request_body_limit_rejects_before_schema_processing(self):
        environment = {"MAX_REQUEST_BODY_BYTES": "128"}
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", environment, clear=False):
            with TestClient(self._app(Path(directory) / "body-limit.db")) as client:
                response = client.post(
                    "/api/patient/auth/register",
                    content=json.dumps({"padding": "x" * 256}),
                    headers={"content-type": "application/json"},
                )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "请求体过大")

    def test_auth_rate_limit_ignores_untrusted_forwarded_address(self):
        environment = {
            "AUTH_RATE_LIMIT_ATTEMPTS": "2",
            "RATE_LIMIT_WINDOW_SECONDS": "60",
            "TRUST_PROXY_HEADERS": "false",
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", environment, clear=False):
            with TestClient(self._app(Path(directory) / "rate-limit.db")) as client:
                payload = {"phone": "13900000000", "password": "wrong-password"}
                first = client.post("/api/patient/auth/login", json=payload, headers={"x-forwarded-for": "1.1.1.1"})
                second = client.post("/api/patient/auth/login", json=payload, headers={"x-forwarded-for": "2.2.2.2"})
                blocked = client.post("/api/patient/auth/login", json=payload, headers={"x-forwarded-for": "3.3.3.3"})
        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 401)
        self.assertEqual(blocked.status_code, 429)
        self.assertGreaterEqual(int(blocked.headers["retry-after"]), 1)

    def test_ai_and_scheduler_limits_are_distinct_and_scoped_to_each_app(self):
        environment = {
            "AI_RATE_LIMIT_REQUESTS": "1",
            "SCHEDULER_RATE_LIMIT_REQUESTS": "1",
            "RATE_LIMIT_WINDOW_SECONDS": "60",
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", environment, clear=False):
            database_path = Path(directory) / "isolated-rate-limits.db"
            with TestClient(self._app(database_path)) as first_client:
                ai_first = first_client.post("/api/patient/agent/chat", json={})
                ai_blocked = first_client.post("/api/patient/agent/chat", json={})
                scheduler_first = first_client.post("/api/patient/plans", json={})
                scheduler_blocked = first_client.post("/api/patient/plans", json={})

            with TestClient(self._app(database_path)) as second_client:
                isolated_ai = second_client.post("/api/patient/agent/chat", json={})
                isolated_scheduler = second_client.post("/api/patient/plans/example/resume", json={})
                resume_blocked = second_client.post("/api/patient/plans/example/resume", json={})

        self.assertNotEqual(ai_first.status_code, 429)
        self.assertEqual(ai_blocked.status_code, 429)
        self.assertNotEqual(scheduler_first.status_code, 429)
        self.assertEqual(scheduler_blocked.status_code, 429)
        self.assertNotEqual(isolated_ai.status_code, 429)
        self.assertNotEqual(isolated_scheduler.status_code, 429)
        self.assertEqual(resume_blocked.status_code, 429)


class InputAndCredentialHardeningTest(unittest.TestCase):
    def test_login_verification_uses_dummy_hash_without_authenticating(self):
        self.assertFalse(verify_login_password("any-password", None))
        encoded = hash_password("correct-password")
        self.assertTrue(verify_login_password("correct-password", encoded))
        self.assertFalse(verify_login_password("wrong-password", encoded))

    def test_password_hash_rejects_hostile_cost_and_malformed_material(self):
        self.assertFalse(verify_password("password", "pbkdf2_sha256$999999999$aGVsbG8=$aGVsbG8="))
        self.assertFalse(verify_password("password", "pbkdf2_sha256$310000$not-base64$not-base64"))

    def test_gis_rejects_excess_features_and_malformed_coordinate_nesting(self):
        point = {
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Point", "coordinates": [1, 2]},
        }
        with self.assertRaises(ValidationError):
            GISUpload.model_validate(
                {"geojson": {"type": "FeatureCollection", "features": [point] * (MAX_GIS_FEATURES + 1)}}
            )
        with self.assertRaises(ValidationError):
            GISUpload.model_validate(
                {
                    "geojson": {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "properties": {},
                                "geometry": {"type": "LineString", "coordinates": [[[[1, 2]]], [3, 4]]},
                            }
                        ],
                    }
                }
            )

    def test_cors_origins_must_be_exact(self):
        self.assertEqual(
            resolve_allowed_origins("https://admin.example.test/, http://localhost:3000"),
            ["https://admin.example.test", "http://localhost:3000"],
        )
        for value in ("*", "javascript:alert(1)", "https://admin.example.test/path"):
            with self.assertRaises(RuntimeError):
                resolve_allowed_origins(value)


class UpstreamAndAlgorithmBoundTest(unittest.TestCase):
    def test_long_prerequisite_chain_is_validated_iteratively(self):
        item_ids = [f"exam-{index}" for index in range(5000)]
        prerequisites = {
            item_id: (() if index == 0 else (item_ids[index - 1],))
            for index, item_id in enumerate(item_ids)
        }
        validate_prerequisite_graph(item_ids, prerequisites)
        prerequisites[item_ids[0]] = (item_ids[-1],)
        with self.assertRaisesRegex(ValueError, "循环"):
            validate_prerequisite_graph(item_ids, prerequisites)

    def test_ai_response_reader_enforces_declared_and_streamed_limits(self):
        class Response:
            def __init__(self, payload: bytes, declared: str | None = None):
                self.payload = payload
                self.headers = {} if declared is None else {"Content-Length": declared}

            def read(self, size: int) -> bytes:
                return self.payload[:size]

        with patch.dict("os.environ", {"CHATANYWHERE_MAX_RESPONSE_BYTES": "1024"}, clear=False):
            with self.assertRaisesRegex(ValueError, "响应过大"):
                _read_json_response(Response(b"{}", "2048"))
            with self.assertRaisesRegex(ValueError, "响应过大"):
                _read_json_response(Response(b"x" * 1025))
            self.assertEqual(_read_json_response(Response(b'{"choices": []}')), {"choices": []})

    def test_fcfs_heap_implementation_preserves_results_for_large_capacity(self):
        observed = _fcfs_wait_minutes(
            1_000_000,
            (12.0, 7.0),
            (5.0, 4.0, 3.0),
            2.0,
        )
        self.assertEqual(observed, 2.0)

        observed_busy = _fcfs_wait_minutes(
            2,
            (5.0, 9.0),
            (4.0, 7.0, 2.0),
            1.0,
        )
        self.assertEqual(observed_busy, 12.0)

    def test_demo_package_ordering_visits_each_dependency_a_constant_number_of_times(self):
        item_ids = [f"exam-{index}" for index in range(500)]
        package = SimpleNamespace(package_name="large package", included_item_ids=item_ids)
        exams = {
            item_id: SimpleNamespace(prerequisites={}, conflicts=[])
            for item_id in item_ids
        }
        calls = 0

        def counted_prerequisites(value):
            nonlocal calls
            calls += 1
            return ()

        with patch(
            "apps.backend.checkup_backend.demo_patients.prerequisite_item_ids",
            side_effect=counted_prerequisites,
        ):
            ordered = _ordered_package_items(package, exams, random.Random(7))

        self.assertEqual(set(ordered), set(item_ids))
        self.assertLessEqual(calls, len(item_ids) * 2)


if __name__ == "__main__":
    unittest.main()
