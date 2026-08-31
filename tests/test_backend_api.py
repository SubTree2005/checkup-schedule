import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.backend.checkup_backend.database import Base
from apps.backend.checkup_backend.main import create_app
from apps.backend.checkup_backend.models import DemoPatientProfile, ExamPlan, UserInfo, UserStatusInfo


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

    def registration_workspace(self, hospital="测试医院"):
        return {
            "formatVersion": "1.0",
            "mode": "upsert",
            "hospital": {
                "hospitalName": hospital,
                "address": "测试路 1 号",
                "openTime": "08:00-17:00",
                "floorMapUrl": None,
            },
            "departments": [
                {
                    "key": "registration-department",
                    "deptName": "注册初始化科室",
                    "location": "1F-R01",
                    "openTimeStart": "08:00",
                    "openTimeEnd": "17:00",
                    "capacity": 2,
                    "isAvailable": True,
                }
            ],
            "exams": [
                {
                    "key": "registration-exam",
                    "departmentKey": "registration-department",
                    "itemName": "注册初始化项目",
                    "duration": 10,
                    "prerequisites": {},
                    "prerequisiteItemKeys": [],
                    "conflictItemKeys": [],
                    "priority": 1,
                    "allowedTimeSlots": {},
                    "isCritical": False,
                    "isActive": True,
                }
            ],
            "packages": [
                {
                    "key": "registration-package",
                    "packageName": "注册演示套餐",
                    "packageType": "健康体检",
                    "price": 100,
                    "tag": "注册数据",
                    "description": "测试注册时完整导入",
                    "includedItemKeys": ["registration-exam"],
                    "defaultDuration": 0,
                    "suitable": [],
                    "notice": [],
                    "isPublished": True,
                }
            ],
            "gis": [
                {
                    "floorKey": "1F",
                    "geojson": {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "properties": {
                                    "featureType": "department",
                                    "departmentKey": "registration-department",
                                },
                                "geometry": {"type": "Point", "coordinates": [10, 10]},
                            }
                        ],
                    },
                }
            ],
        }

    def register(self, client=None, phone="13800000001", hospital="测试医院", workspace=None):
        client = client or self.client
        response = client.post(
            "/api/auth/register",
            json={
                "phone": phone,
                "password": "secure-pass-123",
                "adminName": "测试管理员",
                "workspace": workspace or self.registration_workspace(hospital),
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
            "demo_patient_profile",
        }
        self.assertTrue(expected.issubset(Base.metadata.tables))

    def test_register_login_and_password_hash(self):
        result = self.register()
        self.assertEqual(result["hospital"]["hospitalName"], "测试医院")
        self.assertEqual(result["demoPool"], {"prepared": 100, "active": 0})
        self.assertIn("checkup_admin_session", self.client.cookies)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)
        with self.app.state.session_factory() as session:
            user = session.scalar(select(UserInfo).where(UserInfo.phone == "13800000001"))
            self.assertTrue(user.password.startswith("pbkdf2_sha256$"))
            self.assertNotIn("secure-pass-123", user.password)
            demos = session.scalars(
                select(DemoPatientProfile).where(DemoPatientProfile.hospital_id == result["hospital"]["hospitalID"])
            ).all()
            self.assertEqual(len(demos), 100)
            self.assertFalse(any(row.is_active for row in demos))
            self.assertEqual(session.scalars(select(ExamPlan)).all(), [])

    def test_registration_requires_complete_workspace(self):
        template = self.client.get("/api/auth/register-template")
        self.assertEqual(template.status_code, 200, template.text)
        self.assertTrue(template.json()["hospital"])
        self.assertTrue(all(template.json()[name] for name in ("departments", "exams", "packages", "gis")))
        workspace = self.registration_workspace("不完整医院")
        workspace["gis"] = []
        response = self.client.post(
            "/api/auth/register",
            json={
                "phone": "13800000077",
                "password": "secure-pass-123",
                "adminName": "测试管理员",
                "workspace": workspace,
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        with self.app.state.session_factory() as session:
            self.assertIsNone(session.scalar(select(UserInfo).where(UserInfo.phone == "13800000077")))

    def test_demo_patient_pool_can_activate_resize_withdraw_and_reuse(self):
        admin = self.register()
        hospital_id = admin["hospital"]["hospitalID"]
        initial = self.client.get("/api/demo-patients")
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertEqual(initial.json()["prepared"], 100)
        self.assertEqual(initial.json()["active"], 0)

        with self.app.state.session_factory() as session:
            prepared = session.scalars(
                select(DemoPatientProfile)
                .where(DemoPatientProfile.hospital_id == hospital_id)
                .order_by(DemoPatientProfile.ordinal)
            ).all()
            fixed_profiles = [(row.user_id, row.package_id, list(row.selected_item_ids)) for row in prepared]
            demo_package_id = prepared[0].package_id

        protected_package = self.client.delete(f"/api/packages/{demo_package_id}")
        self.assertEqual(protected_package.status_code, 409, protected_package.text)

        activated = self.client.post("/api/demo-patients/active", json={"count": 7})
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()["active"], 7)
        self.assertEqual(activated.json()["changed"], 7)
        dashboard = self.client.get("/api/dashboard/summary").json()
        self.assertEqual(dashboard["metrics"]["todayPlans"], 7)
        self.assertEqual(dashboard["metrics"]["inProgressPlans"], 7)
        self.assertEqual(sum(row["activeCount"] for row in dashboard["flow"]), 7)

        resized = self.client.post("/api/demo-patients/active", json={"count": 3})
        self.assertEqual(resized.status_code, 200, resized.text)
        self.assertEqual(resized.json()["active"], 3)
        first_plan_ids = [row["planID"] for row in resized.json()["activePatients"]]

        withdrawn = self.client.delete("/api/demo-patients/active")
        self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
        self.assertEqual(withdrawn.json()["active"], 0)
        with self.app.state.session_factory() as session:
            prepared = session.scalars(
                select(DemoPatientProfile)
                .where(DemoPatientProfile.hospital_id == hospital_id)
                .order_by(DemoPatientProfile.ordinal)
            ).all()
            self.assertEqual(len(prepared), 100)
            self.assertEqual(
                [(row.user_id, row.package_id, list(row.selected_item_ids)) for row in prepared],
                fixed_profiles,
            )
            self.assertFalse(any(row.is_active for row in prepared))
            self.assertEqual(session.scalars(select(ExamPlan).where(ExamPlan.hospital_id == hospital_id)).all(), [])
            self.assertEqual(
                session.scalars(
                    select(UserStatusInfo).where(UserStatusInfo.user_id.in_([row.user_id for row in prepared]))
                ).all(),
                [],
            )

        reactivated = self.client.post("/api/demo-patients/active", json={"count": 3})
        self.assertEqual(reactivated.status_code, 200, reactivated.text)
        self.assertEqual([row["planID"] for row in reactivated.json()["activePatients"]], first_plan_ids)

    def test_admin_page_assets_and_logout(self):
        admin_page = self.client.get("/").text
        self.assertIn("智检云", admin_page)
        self.assertIn("一键导入", admin_page)
        self.assertIn("套餐管理", admin_page)
        self.assertIn("renderMap", self.client.get("/assets/app.js").text)
        self.assertIn("renderPackages", self.client.get("/assets/app.js").text)
        self.assertIn("workspaceImportForm", self.client.get("/assets/app.js").text)
        self.assertIn("demoPatientTrigger", admin_page)
        self.assertIn("workspaceFile", admin_page)
        self.register()
        logout = self.client.post("/api/auth/logout")
        self.assertEqual(logout.status_code, 204)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_hospital_data_isolation(self):
        self.register()
        first_department = self.create_department()
        with TestClient(self.app) as second_client:
            second_admin = self.register(second_client, phone="13800000002", hospital="第二医院")
            second_departments = second_client.get("/api/departments").json()
            self.assertEqual(len(second_departments), 1)
            self.assertNotEqual(second_departments[0]["deptID"], first_department["deptID"])
            cross_update = second_client.patch(
                f"/api/departments/{first_department['deptID']}",
                json={"deptName": "越权修改"},
            )
            self.assertEqual(cross_update.status_code, 404)
        with self.app.state.session_factory() as session:
            first_demo = session.scalars(
                select(DemoPatientProfile).where(DemoPatientProfile.hospital_id == first_department["hospitalID"])
            ).all()
            second_demo = session.scalars(
                select(DemoPatientProfile).where(
                    DemoPatientProfile.hospital_id == second_admin["hospital"]["hospitalID"]
                )
            ).all()
            self.assertEqual(len(first_demo), 100)
            self.assertEqual(len(second_demo), 100)
            self.assertTrue(
                {item for row in first_demo for item in row.selected_item_ids}.isdisjoint(
                    {item for row in second_demo for item in row.selected_item_ids}
                )
            )

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

    def test_patient_schedule_uses_local_department_hours(self):
        admin = self.register()
        department_response = self.client.post(
            "/api/departments",
            json={
                "deptName": "限时检查科",
                "location": "1F-T01",
                "openTimeStart": "10:00",
                "openTimeEnd": "10:30",
                "capacity": 1,
                "isAvailable": True,
            },
        )
        self.assertEqual(department_response.status_code, 201, department_response.text)
        exam = self.create_exam(department_response.json()["deptID"], name="限时检查")
        package = self.client.post(
            "/api/packages",
            json={
                "packageName": "限时套餐",
                "includedItemIDs": [exam["itemID"]],
                "isPublished": True,
            },
        )
        self.assertEqual(package.status_code, 201, package.text)

        with TestClient(self.app) as patient_client:
            registered = patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000073",
                    "password": "patient-pass-123",
                    "name": "本地时间患者",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)
            with patch("apps.backend.checkup_backend.patient_api.utcnow", return_value=datetime(2026, 8, 31, 1, 0)):
                created = patient_client.post(
                    "/api/patient/plans",
                    json={
                        "hospitalID": admin["hospital"]["hospitalID"],
                        "packageID": package.json()["packageID"],
                        "profile": {},
                    },
                )
            self.assertEqual(created.status_code, 201, created.text)
            step = created.json()["steps"][0]
            estimated_start = datetime.fromisoformat(step["estimatedStart"].removesuffix("Z"))
            estimated_end = datetime.fromisoformat(step["estimatedEnd"].removesuffix("Z"))
            self.assertGreaterEqual(estimated_start, datetime(2026, 8, 31, 2, 0))
            self.assertLessEqual(estimated_end, datetime(2026, 8, 31, 2, 30))

    def test_dashboard_uses_hospital_local_day(self):
        admin = self.register()
        with self.app.state.session_factory() as session:
            session.add_all(
                [
                    ExamPlan(
                        user_id=admin["user"]["userID"],
                        hospital_id=admin["hospital"]["hospitalID"],
                        selected_item_ids=[],
                        generate_time=datetime(2026, 8, 31, 15, 30),
                        plan_status="已完成",
                    ),
                    ExamPlan(
                        user_id=admin["user"]["userID"],
                        hospital_id=admin["hospital"]["hospitalID"],
                        selected_item_ids=[],
                        generate_time=datetime(2026, 8, 31, 16, 30),
                        plan_status="进行中",
                    ),
                ]
            )
            session.commit()
        with patch("apps.backend.checkup_backend.api.utcnow", return_value=datetime(2026, 8, 31, 17, 0)):
            dashboard = self.client.get("/api/dashboard/summary")
        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        self.assertEqual(dashboard.json()["metrics"]["todayPlans"], 1)
        self.assertEqual(dashboard.json()["metrics"]["inProgressPlans"], 1)
        self.assertEqual(dashboard.json()["metrics"]["completedPlans"], 0)

    def test_workspace_import_is_atomic_and_idempotent(self):
        admin = self.register()
        template_response = self.client.get("/api/imports/template")
        self.assertEqual(template_response.status_code, 200, template_response.text)
        template = template_response.json()
        self.assertEqual(template["formatVersion"], "1.0")
        self.assertEqual(len(template["packages"]), 2)

        imported = self.client.post("/api/imports/workspace", json=template)
        self.assertEqual(imported.status_code, 200, imported.text)
        result = imported.json()
        self.assertEqual(result["summary"]["hospital"], {"updated": 0})
        self.assertEqual(result["summary"]["departments"], {"created": 2, "updated": 0})
        self.assertEqual(result["summary"]["exams"], {"created": 2, "updated": 0})
        self.assertEqual(result["summary"]["packages"], {"created": 2, "updated": 0})
        self.assertEqual(result["summary"]["gis"], {"created": 0, "updated": 1})
        self.assertEqual(len(self.client.get("/api/departments").json()), 3)
        self.assertEqual(len(self.client.get("/api/exams").json()), 3)
        packages = self.client.get("/api/packages").json()
        self.assertEqual(len(packages), 3)
        self.assertEqual(sum(package["isPublished"] for package in packages), 2)

        gis = self.client.get("/api/gis/1F").json()
        department_features = [
            feature
            for feature in gis["geojson"]["features"]
            if feature["properties"].get("featureType") == "department"
        ]
        self.assertTrue(all(feature["properties"].get("deptID") for feature in department_features))
        self.assertTrue(all("departmentKey" not in feature["properties"] for feature in department_features))

        repeated = self.client.post("/api/imports/workspace", json=template)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        repeated_result = repeated.json()
        self.assertEqual(repeated_result["summary"]["departments"], {"created": 0, "updated": 2})
        self.assertEqual(repeated_result["summary"]["packages"], {"created": 0, "updated": 2})
        self.assertEqual(repeated_result["gisVersions"]["1F"], 3)
        self.assertEqual(len(self.client.get("/api/departments").json()), 3)

        invalid = self.client.get("/api/imports/template").json()
        invalid["departments"][0]["location"] = "不应写入"
        route = next(
            feature
            for feature in invalid["gis"][0]["geojson"]["features"]
            if feature["properties"].get("featureType") == "route"
        )
        route["properties"]["distanceMeters"] = -1
        rejected = self.client.post("/api/imports/workspace", json=invalid)
        self.assertEqual(rejected.status_code, 422, rejected.text)
        departments = {row["deptName"]: row for row in self.client.get("/api/departments").json()}
        self.assertEqual(departments["超声科"]["location"], "1F-A12")

        with TestClient(self.app) as patient_client:
            registered = patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000008",
                    "password": "patient-pass-123",
                    "name": "批量导入患者",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)
            catalog = patient_client.get(
                f"/api/patient/hospitals/{admin['hospital']['hospitalID']}/catalog"
            )
            self.assertEqual(catalog.status_code, 200, catalog.text)
            self.assertEqual(
                {row["packageName"] for row in catalog.json()["packages"]},
                {"基础体检套餐", "注册演示套餐"},
            )

    def test_zijingang_example_bundle_imports_end_to_end(self):
        example_path = (
            Path(__file__).resolve().parents[1]
            / "examples"
            / "hospitals"
            / "zijingang-campus-hospital"
            / "workspace.json"
        )
        payload = json.loads(example_path.read_text(encoding="utf-8"))
        admin = self.register(hospital="待更新医院", workspace=payload)
        summary = admin["workspaceSummary"]
        self.assertEqual(summary["hospital"], {"updated": 1})
        self.assertEqual(summary["departments"], {"created": 49, "updated": 0})
        self.assertEqual(summary["exams"], {"created": 79, "updated": 0})
        self.assertEqual(summary["packages"], {"created": 7, "updated": 0})
        self.assertEqual(summary["gis"], {"created": 3, "updated": 0})
        self.assertEqual(admin["demoPool"], {"prepared": 100, "active": 0})

        me = self.client.get("/api/auth/me").json()
        self.assertEqual(me["hospital"]["hospitalName"], "浙江大学校医院（紫金港校区）")
        self.assertEqual(me["hospital"]["address"], "杭州市余杭塘路866号")

        departments = self.client.get("/api/departments").json()
        self.assertEqual(len(departments), 49)
        self.assertFalse(any("304" in row["location"] for row in departments))
        self.assertIn("放射登记窗口", next(row for row in departments if row["deptName"].startswith("放射科"))["location"])

        packages = self.client.get("/api/packages").json()
        self.assertEqual(sorted(row["price"] for row in packages), [80, 120, 280, 398, 580, 800, 1350])
        self.assertTrue(all(row["isPublished"] for row in packages))

        department_feature_count = 0
        for floor_key in ("1F", "2F", "3F"):
            floor = self.client.get(f"/api/gis/{floor_key}")
            self.assertEqual(floor.status_code, 200, floor.text)
            department_features = [
                feature
                for feature in floor.json()["geojson"]["features"]
                if feature["properties"].get("featureType") == "department"
            ]
            self.assertTrue(all(feature["properties"].get("deptID") for feature in department_features))
            department_feature_count += len(department_features)
        self.assertEqual(department_feature_count, 49)

        with TestClient(self.app) as patient_client:
            registered = patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000009",
                    "password": "patient-pass-123",
                    "name": "紫金港示例患者",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)
            catalog = patient_client.get(
                f"/api/patient/hospitals/{admin['hospital']['hospitalID']}/catalog"
            )
            self.assertEqual(catalog.status_code, 200, catalog.text)
            self.assertEqual(len(catalog.json()["packages"]), 7)

    def test_anomaly_closes_and_reopens_department(self):
        self.register()
        department = self.create_department()
        created = self.client.post(
            "/api/anomalies",
            json={"deptID": department["deptID"], "anomalyType": "设备故障", "description": "设备校准"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        departments = {row["deptID"]: row for row in self.client.get("/api/departments").json()}
        self.assertFalse(departments[department["deptID"]]["isAvailable"])
        resolved = self.client.post(
            f"/api/anomalies/{created.json()['reportID']}/resolve",
            json={"reopenDepartment": True},
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        departments = {row["deptID"]: row for row in self.client.get("/api/departments").json()}
        self.assertTrue(departments[department["deptID"]]["isAvailable"])

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
            self.assertNotIn(package_id, {row["packageID"] for row in second_admin_client.get("/api/packages").json()})
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

            with patch(
                "apps.backend.checkup_backend.patient_api.utcnow",
                return_value=datetime(2026, 8, 31, 0, 15),
            ):
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
            self.assertNotIn(package_id, {row["packageID"] for row in catalog_after_unpublish.json()["packages"]})
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
