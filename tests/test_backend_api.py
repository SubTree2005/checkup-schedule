import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.backend.checkup_backend.database import Base
from apps.backend.checkup_backend.main import create_app
from apps.backend.checkup_backend.models import ExamPlan, UserInfo


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
        admin_page = self.client.get("/").text
        self.assertIn("智检云", admin_page)
        self.assertIn("套餐管理", admin_page)
        self.assertIn("renderMap", self.client.get("/assets/app.js").text)
        self.assertIn("renderPackages", self.client.get("/assets/app.js").text)
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

    def test_patient_miniprogram_end_to_end(self):
        admin = self.register()
        department = self.create_department()
        first_exam = self.create_exam(department["deptID"], name="腹部超声")
        second_exam = self.create_exam(department["deptID"], name="甲状腺超声")
        package_response = self.client.post(
            "/api/packages",
            json={
                "packageName": "基础套餐",
                "packageType": "入职体检",
                "price": 580,
                "tag": "热门",
                "description": "患者端联调套餐",
                "includedItemIDs": [first_exam["itemID"], second_exam["itemID"]],
                "defaultDuration": 0,
                "suitable": ["18 岁以上人群"],
                "notice": ["检查前保持空腹"],
                "isPublished": True,
            },
        )
        self.assertEqual(package_response.status_code, 201, package_response.text)
        package = package_response.json()
        package_id = package["packageID"]
        self.assertEqual(package["defaultDuration"], 24)
        invalid_clear = self.client.patch(f"/api/packages/{package_id}", json={"includedItemIDs": None})
        self.assertEqual(invalid_clear.status_code, 422)
        blocked_deactivate = self.client.patch(
            f"/api/exams/{first_exam['itemID']}", json={"isActive": False}
        )
        self.assertEqual(blocked_deactivate.status_code, 409)

        with TestClient(self.app) as second_admin_client:
            self.register(second_admin_client, phone="13800000009", hospital="第二医院")
            self.assertEqual(second_admin_client.get("/api/packages").json(), [])
            cross_update = second_admin_client.patch(
                f"/api/packages/{package_id}", json={"isPublished": False}
            )
            self.assertEqual(cross_update.status_code, 404)

        with TestClient(self.app) as patient_client:
            registered = patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000001",
                    "password": "patient-pass-123",
                    "name": "体检用户",
                    "gender": "女",
                    "age": 28,
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)
            self.assertTrue(registered.json()["token"])
            self.assertEqual(patient_client.get("/api/patient/auth/me").json()["name"], "体检用户")

            hospitals = patient_client.get("/api/patient/hospitals")
            self.assertEqual(hospitals.status_code, 200, hospitals.text)
            self.assertEqual(hospitals.json()[0]["hospitalID"], admin["hospital"]["hospitalID"])
            catalog = patient_client.get(
                f"/api/patient/hospitals/{admin['hospital']['hospitalID']}/catalog"
            )
            self.assertEqual(catalog.status_code, 200, catalog.text)
            self.assertEqual(catalog.json()["packages"][0]["packageID"], package_id)
            self.assertEqual(catalog.json()["packages"][0]["price"], 580)
            self.assertEqual(catalog.json()["packages"][0]["type"], "入职体检")

            created = patient_client.post(
                "/api/patient/plans",
                json={
                    "hospitalID": admin["hospital"]["hospitalID"],
                    "packageID": package_id,
                    "profile": {"fasting": "yes", "bladder": "normal"},
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            plan = created.json()
            self.assertEqual(plan["packageName"], "基础套餐")
            self.assertEqual(plan["totalSteps"], 2)
            self.assertEqual(plan["steps"][0]["status"], "active")
            with self.app.state.session_factory() as session:
                stored_plan = session.get(ExamPlan, plan["planID"])
                self.assertEqual(stored_plan.package_id, package_id)
                self.assertEqual(stored_plan.selected_item_ids, [first_exam["itemID"], second_exam["itemID"]])

            first = plan["steps"][0]
            completed = patient_client.post(
                f"/api/patient/plans/{plan['planID']}/steps/{first['detailID']}/complete"
            )
            self.assertEqual(completed.status_code, 200, completed.text)
            self.assertEqual(completed.json()["progress"], 50)
            self.assertEqual(completed.json()["steps"][1]["status"], "active")
            navigation = patient_client.get(
                f"/api/patient/plans/{plan['planID']}/navigation",
                params={"detailID": completed.json()["steps"][1]["detailID"]},
            )
            self.assertEqual(navigation.status_code, 200, navigation.text)
            self.assertEqual(navigation.json()["toName"], "超声科")

            second = completed.json()["steps"][1]
            finished = patient_client.post(
                f"/api/patient/plans/{plan['planID']}/steps/{second['detailID']}/complete"
            )
            self.assertTrue(finished.json()["finished"])
            history = patient_client.get("/api/patient/plans")
            self.assertEqual(history.status_code, 200, history.text)
            self.assertEqual(history.json()[0]["status"], "已完成")

            unpublished = self.client.patch(f"/api/packages/{package_id}", json={"isPublished": False})
            self.assertEqual(unpublished.status_code, 200, unpublished.text)
            catalog_after_unpublish = patient_client.get(
                f"/api/patient/hospitals/{admin['hospital']['hospitalID']}/catalog"
            )
            self.assertEqual(catalog_after_unpublish.json()["packages"], [])
            stale_create = patient_client.post(
                "/api/patient/plans",
                json={
                    "hospitalID": admin["hospital"]["hospitalID"],
                    "packageID": package_id,
                    "profile": {},
                },
            )
            self.assertEqual(stale_create.status_code, 404)
            protected_history = self.client.delete(f"/api/packages/{package_id}")
            self.assertEqual(protected_history.status_code, 409)


if __name__ == "__main__":
    unittest.main()
