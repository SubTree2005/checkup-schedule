import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.backend.checkup_backend.database import Base
from apps.backend.checkup_backend.main import create_app
from apps.backend.checkup_backend.models import UserInfo


class BackendAPITest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_url = f"sqlite:///{Path(self.temp_dir.name) / 'test.db'}"
        static_dir = Path(__file__).resolve().parents[1] / "apps" / "admin-web"
        self.app = create_app(database_url=database_url, static_dir=static_dir)
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def register(self, client=None, phone="13800000001", hospital="测试医院"):
        client = client or self.client
        response = client.post(
            "/api/auth/register",
            json={
                "phone": phone,
                "password": "secure-pass-123",
                "adminName": "测试管理员",
                "hospitalName": hospital,
                "address": "测试路 1 号",
                "openTime": "08:00-17:00",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def create_department(self, name="超声科"):
        response = self.client.post(
            "/api/departments",
            json={
                "deptName": name,
                "location": "1F-A12",
                "openTimeStart": "08:00",
                "openTimeEnd": "17:00",
                "capacity": 2,
                "isAvailable": True,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def create_exam(self, dept_id, name="腹部超声"):
        response = self.client.post(
            "/api/exams",
            json={
                "deptID": dept_id,
                "itemName": name,
                "duration": 12,
                "prerequisites": {"fastingHours": 8},
                "conflicts": [],
                "priority": 6,
                "allowedTimeSlots": {"start": "08:00", "end": "11:30"},
                "isCritical": True,
                "isActive": True,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_schema_matches_requirement_entities(self):
        expected = {
            "user_info",
            "hospital_info",
            "department_info",
            "exam_info",
            "package_info",
            "user_status_info",
            "exam_plan",
            "plan_execution_detail",
            "anomaly_report",
            "department_distance",
            "user_mobility_profile",
            "walk_speed_preset",
            "queue_snapshot",
            "department_waiting_stats",
            "department_resource_calendar",
        }
        self.assertTrue(expected.issubset(Base.metadata.tables))

    def test_register_login_and_password_hash(self):
        result = self.register()
        self.assertEqual(result["hospital"]["hospitalName"], "测试医院")
        self.assertIn("checkup_admin_session", self.client.cookies)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)
        with self.app.state.session_factory() as session:
            user = session.scalar(select(UserInfo).where(UserInfo.phone == "13800000001"))
            self.assertTrue(user.password.startswith("pbkdf2_sha256$"))
            self.assertNotIn("secure-pass-123", user.password)

    def test_admin_page_assets_and_logout(self):
        self.assertIn("智检云", self.client.get("/").text)
        self.assertIn("renderMap", self.client.get("/assets/app.js").text)
        self.register()
        logout = self.client.post("/api/auth/logout")
        self.assertEqual(logout.status_code, 204)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_hospital_data_isolation(self):
        self.register()
        first_department = self.create_department()
        with TestClient(self.app) as second_client:
            self.register(second_client, phone="13800000002", hospital="第二医院")
            self.assertEqual(second_client.get("/api/departments").json(), [])
            cross_update = second_client.patch(
                f"/api/departments/{first_department['deptID']}",
                json={"deptName": "越权修改"},
            )
            self.assertEqual(cross_update.status_code, 404)

    def test_gis_queue_and_dashboard_flow(self):
        self.register()
        department = self.create_department()
        exam = self.create_exam(department["deptID"])
        queue = self.client.post(
            "/api/queues",
            json={"itemID": exam["itemID"], "queueCount": 9, "estimatedWaitTime": 1200, "validMinutes": 30},
        )
        self.assertEqual(queue.status_code, 201, queue.text)
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "featureType": "department",
                        "deptID": department["deptID"],
                        "name": "超声科",
                    },
                    "geometry": {"type": "Point", "coordinates": [20, 30]},
                },
                {
                    "type": "Feature",
                    "properties": {"featureType": "room"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [40, 0], [40, 40], [0, 40], [0, 0]]],
                    },
                },
            ],
        }
        upload = self.client.put("/api/gis/1F", json={"geojson": geojson})
        self.assertEqual(upload.status_code, 200, upload.text)
        dashboard = self.client.get("/api/dashboard/map/1F")
        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        flow = {item["deptID"]: item for item in dashboard.json()["flow"]}
        self.assertEqual(flow[department["deptID"]]["peopleFlow"], 9)
        self.assertEqual(flow[department["deptID"]]["estimatedWaitTime"], 1200)

    def test_anomaly_closes_and_reopens_department(self):
        self.register()
        department = self.create_department()
        created = self.client.post(
            "/api/anomalies",
            json={"deptID": department["deptID"], "anomalyType": "设备故障", "description": "设备校准"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        departments = self.client.get("/api/departments").json()
        self.assertFalse(departments[0]["isAvailable"])
        resolved = self.client.post(
            f"/api/anomalies/{created.json()['reportID']}/resolve",
            json={"reopenDepartment": True},
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertTrue(self.client.get("/api/departments").json()[0]["isAvailable"])


if __name__ == "__main__":
    unittest.main()
