import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.backend.checkup_backend.database import Base
from apps.backend.checkup_backend.main import create_app
from apps.backend.checkup_backend.models import (
    DemoPatientProfile,
    ExamPlan,
    HospitalSettings,
    UserConsent,
    UserInfo,
    UserSession,
    UserStatusInfo,
    WechatReminder,
)
from apps.backend.checkup_backend.security import issue_session, session_digest
from apps.backend.checkup_backend.wechat_reminders import WechatAPIError


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
            "hospital_settings",
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
            "user_consent",
            "wechat_reminder",
        }
        self.assertTrue(expected.issubset(Base.metadata.tables))

    def test_patient_agent_uses_server_managed_model_configuration(self):
        unauthenticated = self.client.get("/api/patient/agent/status")
        self.assertEqual(unauthenticated.status_code, 401)

        with TestClient(self.app) as patient_client:
            registered = patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000661",
                    "password": "patient-pass-123",
                    "name": "AI 助手测试用户",
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)

            with patch.dict(os.environ, {"CHATANYWHERE_API_KEY": ""}, clear=False):
                status_response = patient_client.get("/api/patient/agent/status")
                self.assertEqual(status_response.status_code, 200, status_response.text)
                self.assertFalse(status_response.json()["configured"])
                missing_config = patient_client.post(
                    "/api/patient/agent/chat",
                    json={"messages": [{"role": "user", "content": "空腹检查要注意什么？"}]},
                )
                self.assertEqual(missing_config.status_code, 503, missing_config.text)

            environment = {
                "CHATANYWHERE_API_KEY": "server-only-test-key",
                "CHATANYWHERE_MODEL": "deepseek-v4-flash",
            }
            with patch.dict(os.environ, environment, clear=False), patch(
                "apps.backend.checkup_backend.agent_api._post_chatanywhere",
                return_value={"choices": [{"message": {"content": "请按项目要求保持空腹。"}}]},
            ) as upstream:
                response = patient_client.post(
                    "/api/patient/agent/chat",
                    json={
                        "messages": [{"role": "user", "content": "空腹检查要注意什么？"}],
                        "currentPage": "pages/preparation-reminder/preparation-reminder",
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"reply": "请按项目要求保持空腹。", "model": "deepseek-v4-flash"})
            api_url, api_key, request_payload = upstream.call_args.args
            self.assertEqual(api_url, "https://api.chatanywhere.tech/v1/chat/completions")
            self.assertEqual(api_key, "server-only-test-key")
            self.assertEqual(request_payload["messages"][-1], {"role": "user", "content": "空腹检查要注意什么？"})
            self.assertNotIn("server-only-test-key", response.text)

    def test_patient_catalog_exposes_explicit_bladder_preparation_flag(self):
        admin = self.register()
        department = self.create_department("泌尿超声科")
        exam = self.client.post(
            "/api/exams",
            json={
                "deptID": department["deptID"],
                "itemName": "泌尿系超声",
                "duration": 12,
                "prerequisites": {"bladderRequired": True},
                "conflicts": [],
                "priority": 6,
                "allowedTimeSlots": {"start": "08:00", "end": "11:30"},
                "isCritical": False,
                "isActive": True,
            },
        )
        self.assertEqual(exam.status_code, 201, exam.text)

        with TestClient(self.app) as patient_client:
            registered = patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000889",
                    "password": "patient-pass-123",
                    "name": "准备要求患者",
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)
            catalog = patient_client.get(
                f"/api/patient/hospitals/{admin['hospital']['hospitalID']}/catalog"
            )
            self.assertEqual(catalog.status_code, 200, catalog.text)
            projects = [
                project
                for department_row in catalog.json()["departments"]
                for project in department_row["projects"]
            ]
            row = next(project for project in projects if project["itemID"] == exam.json()["itemID"])
            self.assertTrue(row["bladderRequired"])

    def test_wechat_reminder_requires_real_subscription_and_dispatches_with_audit_state(self):
        admin = self.register(hospital="浙江大学校医院（紫金港院区）")
        hospital_id = admin["hospital"]["hospitalID"]
        environment = {
            "WECHAT_APP_ID": "wx-test-app",
            "WECHAT_APP_SECRET": "test-secret",
            "WECHAT_REMINDER_TEMPLATE_ID": "template-checkup-reminder",
            "WECHAT_REMINDER_DATA_TEMPLATE": json.dumps(
                {
                    "thing1": {"value": "{hospital}"},
                    "time2": {"value": "{appointment}"},
                    "thing3": {"value": "{package}"},
                },
                ensure_ascii=False,
            ),
            "WECHAT_TRUST_CLOUDBASE_IDENTITY": "true",
            "REMINDER_DISPATCH_TOKEN": "dispatch-test-token",
        }
        with TestClient(self.app) as patient_client, patch.dict(os.environ, environment, clear=False):
            registered = patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000888",
                    "password": "patient-pass-123",
                    "name": "微信提醒患者",
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)
            config = patient_client.get("/api/patient/reminders/config")
            self.assertEqual(config.status_code, 200, config.text)
            self.assertTrue(config.json()["available"])
            self.assertEqual(config.json()["templateIDs"], ["template-checkup-reminder"])

            package_id = patient_client.get(
                f"/api/patient/hospitals/{hospital_id}/catalog"
            ).json()["packages"][0]["packageID"]
            availability = patient_client.get(
                f"/api/patient/hospitals/{hospital_id}/appointment-slots"
            ).json()
            slot = next(
                slot
                for day in availability["dates"]
                for slot in day["slots"]
                if slot["available"]
            )
            missing_identity = patient_client.post(
                "/api/patient/plans",
                json={
                    "hospitalID": hospital_id,
                    "packageID": package_id,
                    "appointmentAt": slot["appointmentAt"],
                    "reminderSubscription": {
                        "templateID": "template-checkup-reminder",
                        "permission": "accept",
                    },
                },
            )
            self.assertEqual(missing_identity.status_code, 503, missing_identity.text)
            self.assertIn("云托管", missing_identity.json()["detail"])

            created = patient_client.post(
                "/api/patient/plans",
                headers={"X-WX-OPENID": "openid-patient", "X-WX-APPID": "wx-test-app"},
                json={
                    "hospitalID": hospital_id,
                    "packageID": package_id,
                    "appointmentAt": slot["appointmentAt"],
                    "profile": {"booked": "yes"},
                    "reminderSubscription": {
                        "templateID": "template-checkup-reminder",
                        "permission": "accept",
                    },
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
            plan_id = created.json()["planID"]
            reminder_rows = patient_client.get("/api/patient/reminders")
            self.assertEqual(reminder_rows.status_code, 200, reminder_rows.text)
            self.assertEqual(reminder_rows.json()[0]["planID"], plan_id)
            self.assertEqual(reminder_rows.json()[0]["status"], "pending")

            with self.app.state.session_factory() as session:
                reminder = session.scalar(
                    select(WechatReminder).where(WechatReminder.plan_id == plan_id)
                )
                self.assertEqual(reminder.open_id, "openid-patient")
                self.assertEqual(reminder.message_data["thing3"]["value"], "注册演示套餐")
                reminder.next_attempt_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
                session.commit()

            unauthorized = patient_client.post("/api/internal/reminders/dispatch")
            self.assertEqual(unauthorized.status_code, 401, unauthorized.text)
            with patch(
                "apps.backend.checkup_backend.wechat_reminders.send_subscription_message",
                side_effect=WechatAPIError("temporary failure"),
            ):
                failed = patient_client.post(
                    "/api/internal/reminders/dispatch",
                    headers={"X-Reminder-Dispatch-Token": "dispatch-test-token"},
                )
            self.assertEqual(failed.status_code, 200, failed.text)
            self.assertEqual(failed.json()["failed"], 1)
            self.assertEqual(patient_client.get("/api/patient/reminders").json()[0]["status"], "failed")

            with self.app.state.session_factory() as session:
                reminder = session.scalar(
                    select(WechatReminder).where(WechatReminder.plan_id == plan_id)
                )
                reminder.status = "pending"
                reminder.next_attempt_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
                session.commit()
            with patch("apps.backend.checkup_backend.wechat_reminders.send_subscription_message") as sender:
                dispatched = patient_client.post(
                    "/api/internal/reminders/dispatch",
                    headers={"X-Reminder-Dispatch-Token": "dispatch-test-token"},
                )
            self.assertEqual(dispatched.status_code, 200, dispatched.text)
            self.assertEqual(dispatched.json()["sent"], 1)
            sender.assert_called_once()
            self.assertEqual(patient_client.get("/api/patient/reminders").json()[0]["status"], "sent")

    def test_hospital_cover_campuses_and_appointment_capacity_share_one_contract(self):
        first_admin = self.register(hospital="浙江大学校医院（紫金港院区）")
        hospital_id = first_admin["hospital"]["hospitalID"]
        invalid_cover = self.client.patch(
            "/api/hospital",
            json={"coverImageUrl": "data:text/plain;base64,SGVsbG8="},
        )
        self.assertEqual(invalid_cover.status_code, 422, invalid_cover.text)

        cover = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        updated = self.client.patch(
            "/api/hospital",
            json={
                "coverImageUrl": cover,
                "hospitalLevel": "一级甲等",
                "positioning": "校内医疗服务",
                "isAvailable": True,
                "appointmentSlotMinutes": 30,
                "appointmentSlotCapacity": 1,
                "appointmentDaysAhead": 3,
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["coverImageUrl"], cover)
        self.assertEqual(updated.json()["hospitalLevel"], "一级甲等")
        self.assertEqual(updated.json()["positioning"], "校内医疗服务")
        self.assertEqual(updated.json()["appointmentPolicy"], {
            "slotMinutes": 30,
            "slotCapacity": 1,
            "daysAhead": 3,
        })
        with self.app.state.session_factory() as session:
            settings = session.get(HospitalSettings, hospital_id)
            self.assertIsNotNone(settings)
            self.assertEqual(settings.cover_image_url, cover)
            self.assertEqual(settings.appointment_slot_capacity, 1)

        with TestClient(self.app) as second_admin_client:
            second_admin = self.register(
                second_admin_client,
                phone="13800000019",
                hospital="浙江大学校医院（玉泉院区）",
            )

        with TestClient(self.app) as first_patient_client:
            registered = first_patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000071",
                    "password": "patient-pass-123",
                    "name": "第一位预约患者",
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)
            hospitals = first_patient_client.get("/api/patient/hospitals")
            self.assertEqual(hospitals.status_code, 200, hospitals.text)
            institution = next(row for row in hospitals.json() if row["name"] == "浙江大学校医院")
            self.assertEqual(institution["coverImageUrl"], cover)
            self.assertEqual(institution["hospitalLevel"], "一级甲等")
            self.assertEqual(institution["positioning"], "校内医疗服务")
            self.assertEqual(
                {campus["name"] for campus in institution["campuses"]},
                {"紫金港院区", "玉泉院区"},
            )
            self.assertEqual(
                {campus["hospitalID"] for campus in institution["campuses"]},
                {hospital_id, second_admin["hospital"]["hospitalID"]},
            )

            fixed_now = datetime(2026, 8, 31, 0, 0)
            with patch("apps.backend.checkup_backend.patient_api.utcnow", return_value=fixed_now):
                availability = first_patient_client.get(
                    f"/api/patient/hospitals/{hospital_id}/appointment-slots"
                )
                self.assertEqual(availability.status_code, 200, availability.text)
                slot = next(
                    slot
                    for day in availability.json()["dates"]
                    for slot in day["slots"]
                    if slot["available"]
                )
                package_id = first_patient_client.get(
                    f"/api/patient/hospitals/{hospital_id}/catalog"
                ).json()["packages"][0]["packageID"]
                created = first_patient_client.post(
                    "/api/patient/plans",
                    json={
                        "hospitalID": hospital_id,
                        "packageID": package_id,
                        "appointmentAt": slot["appointmentAt"],
                        "profile": {"booked": "yes"},
                    },
                )
                self.assertEqual(created.status_code, 201, created.text)

                with TestClient(self.app) as second_patient_client:
                    registered_second = second_patient_client.post(
                        "/api/patient/auth/register",
                        json={
                            "phone": "13900000072",
                            "password": "patient-pass-123",
                            "name": "第二位预约患者",
                            "privacyConsent": True,
                            "privacyConsentVersion": "v0.3.1-2026-08-31",
                        },
                    )
                    self.assertEqual(registered_second.status_code, 201, registered_second.text)
                    full = second_patient_client.get(
                        f"/api/patient/hospitals/{hospital_id}/appointment-slots"
                    )
                    same_slot = next(
                        candidate
                        for day in full.json()["dates"]
                        for candidate in day["slots"]
                        if candidate["appointmentAt"] == slot["appointmentAt"]
                    )
                    self.assertEqual(same_slot["booked"], 1)
                    self.assertFalse(same_slot["available"])
                    rejected = second_patient_client.post(
                        "/api/patient/plans",
                        json={
                            "hospitalID": hospital_id,
                            "packageID": package_id,
                            "appointmentAt": slot["appointmentAt"],
                            "profile": {"booked": "yes"},
                        },
                    )
                    self.assertEqual(rejected.status_code, 409, rejected.text)
                    self.assertIn("号源已满", rejected.json()["detail"])

                paused = self.client.patch("/api/hospital", json={"isAvailable": False})
                self.assertEqual(paused.status_code, 200, paused.text)
                self.assertFalse(paused.json()["isAvailable"])
                refreshed = first_patient_client.get("/api/patient/hospitals").json()
                campus = next(
                    campus
                    for row in refreshed
                    if row["name"] == "浙江大学校医院"
                    for campus in row["campuses"]
                    if campus["hospitalID"] == hospital_id
                )
                self.assertFalse(campus["available"])
                blocked = first_patient_client.post(
                    "/api/patient/plans",
                    json={
                        "hospitalID": hospital_id,
                        "packageID": package_id,
                        "appointmentAt": slot["appointmentAt"],
                        "profile": {"booked": "yes"},
                    },
                )
                self.assertEqual(blocked.status_code, 409, blocked.text)
                self.assertIn("暂停开放", blocked.json()["detail"])

    def test_patient_registration_records_privacy_consent_and_account_can_be_deleted(self):
        rejected = self.client.post(
            "/api/patient/auth/register",
            json={
                "phone": "13900000090",
                "password": "patient-pass-123",
                "name": "未同意用户",
            },
        )
        self.assertEqual(rejected.status_code, 422, rejected.text)

        with TestClient(self.app) as patient_client:
            registered = patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000091",
                    "password": "patient-pass-123",
                    "name": "隐私测试用户",
                    "medicalHistory": "高血压史",
                    "allergens": "青霉素",
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)
            self.assertEqual(registered.json()["user"]["profile"]["medicalHistory"], "高血压史")
            self.assertEqual(registered.json()["user"]["profile"]["allergens"], "青霉素")
            user_id = registered.json()["user"]["userID"]
            updated = patient_client.patch(
                "/api/patient/profile",
                json={
                    "medicalHistory": "测试病史",
                    "allergens": "测试过敏原",
                    "avatarUrl": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
                },
            )
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertTrue(updated.json()["avatarUrl"].startswith("data:image/png;base64,"))
            wrong_password = patient_client.request(
                "DELETE",
                "/api/patient/account",
                json={"password": "wrong-password"},
            )
            self.assertEqual(wrong_password.status_code, 401, wrong_password.text)
            deleted = patient_client.request(
                "DELETE",
                "/api/patient/account",
                json={"password": "patient-pass-123"},
            )
            self.assertEqual(deleted.status_code, 204, deleted.text)
            self.assertEqual(patient_client.get("/api/patient/auth/me").status_code, 401)

        with self.app.state.session_factory() as session:
            self.assertIsNone(session.get(UserInfo, user_id))
            self.assertIsNone(session.scalar(select(UserConsent).where(UserConsent.user_id == user_id)))
            self.assertIsNone(session.scalar(select(UserStatusInfo).where(UserStatusInfo.user_id == user_id)))
            self.assertIsNone(session.scalar(select(UserSession).where(UserSession.user_id == user_id)))

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
            demo_users = session.scalars(
                select(UserInfo).where(UserInfo.user_id.in_([row.user_id for row in demos]))
            ).all()
            self.assertEqual(len(demo_users), 100)
            self.assertTrue(all(user.role == "演示患者" for user in demo_users))
            self.assertTrue(all(user.password.startswith("pbkdf2_sha256$") for user in demo_users))
            self.assertFalse(any("disabled-demo-account" in user.password for user in demo_users))
            demo_phone = demo_users[0].phone
            demo_token = issue_session(session, demo_users[0].user_id, "127.0.0.1")
            session.commit()
            self.assertEqual(session.scalars(select(ExamPlan)).all(), [])

        demo_login = self.client.post(
            "/api/patient/auth/login",
            json={"phone": demo_phone, "password": "disabled-demo-account"},
        )
        self.assertEqual(demo_login.status_code, 401, demo_login.text)
        demo_session = self.client.get(
            "/api/patient/auth/me",
            headers={"Authorization": f"Bearer {demo_token}"},
        )
        self.assertEqual(demo_session.status_code, 403, demo_session.text)
        with self.app.state.session_factory() as session:
            self.assertIsNone(session.get(UserSession, session_digest(demo_token)))

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

    def test_registration_rejects_invalid_exam_constraints(self):
        cases = []

        missing_prerequisite = self.registration_workspace("遗漏前置项目医院")
        missing_prerequisite["exams"].append(
            {
                **missing_prerequisite["exams"][0],
                "key": "follow-up-exam",
                "itemName": "后续检查",
                "prerequisiteItemKeys": ["registration-exam"],
            }
        )
        missing_prerequisite["packages"][0]["includedItemKeys"] = ["follow-up-exam"]
        cases.append(("13800000071", missing_prerequisite, "缺少前置项目"))

        cyclic = self.registration_workspace("循环前置医院")
        cyclic["exams"][0]["prerequisiteItemKeys"] = ["follow-up-exam"]
        cyclic["exams"].append(
            {
                **cyclic["exams"][0],
                "key": "follow-up-exam",
                "itemName": "后续检查",
                "prerequisiteItemKeys": ["registration-exam"],
            }
        )
        cyclic["packages"][0]["includedItemKeys"] = ["registration-exam", "follow-up-exam"]
        cases.append(("13800000072", cyclic, "存在循环"))

        conflicting = self.registration_workspace("互斥项目医院")
        conflicting["exams"][0]["conflictItemKeys"] = ["follow-up-exam"]
        conflicting["exams"].append(
            {
                **conflicting["exams"][0],
                "key": "follow-up-exam",
                "itemName": "互斥检查",
                "conflictItemKeys": [],
            }
        )
        conflicting["packages"][0]["includedItemKeys"] = ["registration-exam", "follow-up-exam"]
        cases.append(("13800000073", conflicting, "互斥"))

        for phone, workspace, expected in cases:
            with self.subTest(expected=expected):
                response = self.client.post(
                    "/api/auth/register",
                    json={
                        "phone": phone,
                        "password": "secure-pass-123",
                        "adminName": "测试管理员",
                        "workspace": workspace,
                    },
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn(expected, response.text)
                with self.app.state.session_factory() as session:
                    self.assertIsNone(session.scalar(select(UserInfo).where(UserInfo.phone == phone)))

    def test_package_and_patient_plan_enforce_exam_constraints(self):
        admin = self.register()
        department = self.create_department()
        prerequisite = self.create_exam(department["deptID"], name="前置检查")
        follow_up = self.create_exam(department["deptID"], name="后续检查")
        updated = self.client.patch(
            f"/api/exams/{follow_up['itemID']}",
            json={"prerequisites": {"itemIDs": [prerequisite["itemID"]]}},
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        incomplete_package = self.client.post(
            "/api/packages",
            json={
                "packageName": "遗漏前置项目套餐",
                "includedItemIDs": [follow_up["itemID"]],
                "isPublished": True,
            },
        )
        self.assertEqual(incomplete_package.status_code, 422, incomplete_package.text)
        self.assertIn("缺少前置项目", incomplete_package.text)

        package = self.client.post(
            "/api/packages",
            json={
                "packageName": "合法前置项目套餐",
                "includedItemIDs": [prerequisite["itemID"], follow_up["itemID"]],
                "isPublished": True,
            },
        )
        self.assertEqual(package.status_code, 201, package.text)

        cyclic_update = self.client.patch(
            f"/api/exams/{prerequisite['itemID']}",
            json={"prerequisites": {"itemIDs": [follow_up["itemID"]]}},
        )
        self.assertEqual(cyclic_update.status_code, 422, cyclic_update.text)
        self.assertIn("存在循环", cyclic_update.text)
        stored_prerequisite = next(
            row for row in self.client.get("/api/exams").json() if row["itemID"] == prerequisite["itemID"]
        )
        self.assertNotIn("itemIDs", stored_prerequisite["prerequisites"])
        self.assertEqual(
            self.client.patch(f"/api/exams/{prerequisite['itemID']}", json={"prerequisites": None}).status_code,
            422,
        )
        invalid_format = self.client.patch(
            f"/api/exams/{prerequisite['itemID']}",
            json={"prerequisites": {"itemIDs": "not-an-array"}},
        )
        self.assertEqual(invalid_format.status_code, 422, invalid_format.text)
        self.assertIn("必须是项目 ID 数组", invalid_format.text)

        conflicting = self.create_exam(department["deptID"], name="互斥检查")
        conflict_update = self.client.patch(
            f"/api/exams/{conflicting['itemID']}",
            json={"conflicts": [prerequisite["itemID"]]},
        )
        self.assertEqual(conflict_update.status_code, 200, conflict_update.text)
        conflict_package = self.client.post(
            "/api/packages",
            json={
                "packageName": "互斥项目套餐",
                "includedItemIDs": [prerequisite["itemID"], conflicting["itemID"]],
                "isPublished": False,
            },
        )
        self.assertEqual(conflict_package.status_code, 422, conflict_package.text)
        self.assertIn("互斥", conflict_package.text)

        with TestClient(self.app) as patient_client:
            registered = patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000071",
                    "password": "patient-pass-123",
                    "name": "前置约束患者",
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)
            invalid_plan = patient_client.post(
                "/api/patient/plans",
                json={
                    "hospitalID": admin["hospital"]["hospitalID"],
                    "packageID": package.json()["packageID"],
                    "selectedItemIDs": [follow_up["itemID"]],
                },
            )
            self.assertEqual(invalid_plan.status_code, 422, invalid_plan.text)
            self.assertIn("缺少前置项目", invalid_plan.text)
            with patch(
                "apps.backend.checkup_backend.patient_api.utcnow",
                return_value=datetime(2026, 8, 31, 0, 15),
            ):
                valid_plan = patient_client.post(
                    "/api/patient/plans",
                    json={
                        "hospitalID": admin["hospital"]["hospitalID"],
                        "packageID": package.json()["packageID"],
                        "selectedItemIDs": [prerequisite["itemID"], follow_up["itemID"]],
                    },
                )
                self.assertEqual(valid_plan.status_code, 201, valid_plan.text)
                self.assertEqual(valid_plan.json()["totalSteps"], 2)
                replanned = patient_client.post(f"/api/patient/plans/{valid_plan.json()['planID']}/replan")
            self.assertEqual(replanned.status_code, 200, replanned.text)
            self.assertEqual(replanned.json()["totalSteps"], 2)
            self.assertTrue(replanned.json()["replanNotice"])
            initial_steps = {step["itemID"]: step for step in valid_plan.json()["steps"]}
            replanned_steps = {step["itemID"]: step for step in replanned.json()["steps"]}
            active_end = datetime.fromisoformat(
                initial_steps[prerequisite["itemID"]]["estimatedEnd"].removesuffix("Z")
            )
            pending_start = datetime.fromisoformat(
                replanned_steps[follow_up["itemID"]]["estimatedStart"].removesuffix("Z")
            )
            self.assertGreaterEqual(pending_start, active_end)

    def test_time_fields_are_validated_at_api_boundaries(self):
        self.register()
        valid_hospital = self.client.patch(
            "/api/hospital",
            json={"openTime": "工作日08:00-12:00,13:30-17:00；急诊24小时"},
        )
        self.assertEqual(valid_hospital.status_code, 200, valid_hospital.text)
        invalid_hospital = self.client.patch("/api/hospital", json={"openTime": "早八点到晚五点"})
        self.assertEqual(invalid_hospital.status_code, 422, invalid_hospital.text)

        department = self.create_department()
        invalid_exam = self.client.post(
            "/api/exams",
            json={
                "deptID": department["deptID"],
                "itemName": "错误时段项目",
                "duration": 10,
                "allowedTimeSlots": {"start": "11:00", "end": "08:00"},
            },
        )
        self.assertEqual(invalid_exam.status_code, 422, invalid_exam.text)
        exam = self.create_exam(department["deptID"])
        invalid_clear = self.client.patch(
            f"/api/exams/{exam['itemID']}",
            json={"allowedTimeSlots": None},
        )
        self.assertEqual(invalid_clear.status_code, 422, invalid_clear.text)

        workspace = self.registration_workspace("时段错误医院")
        workspace["exams"][0]["allowedTimeSlots"] = {"start": "8:00", "end": "11:00"}
        invalid_registration = self.client.post(
            "/api/auth/register",
            json={
                "phone": "13800000076",
                "password": "secure-pass-123",
                "adminName": "测试管理员",
                "workspace": workspace,
            },
        )
        self.assertEqual(invalid_registration.status_code, 422, invalid_registration.text)
        with self.app.state.session_factory() as session:
            self.assertIsNone(session.scalar(select(UserInfo).where(UserInfo.phone == "13800000076")))

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
        self.assertIn('id="closeDialog"', admin_page)
        self.assertNotIn('<form method="dialog" class="dialog-frame">', admin_page)
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
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
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

    def test_patient_schedule_respects_hospital_lunch_break(self):
        admin = self.register()
        updated = self.client.patch(
            "/api/hospital",
            json={"openTime": "工作日08:00-12:00,13:30-17:00"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        department = self.create_department()
        exam = self.client.post(
            "/api/exams",
            json={
                "deptID": department["deptID"],
                "itemName": "下午检查",
                "duration": 12,
                "allowedTimeSlots": {},
                "isActive": True,
            },
        )
        self.assertEqual(exam.status_code, 201, exam.text)
        package = self.client.post(
            "/api/packages",
            json={
                "packageName": "午休验证套餐",
                "includedItemIDs": [exam.json()["itemID"]],
                "isPublished": True,
            },
        )
        self.assertEqual(package.status_code, 201, package.text)

        with TestClient(self.app) as patient_client:
            registered = patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000074",
                    "password": "patient-pass-123",
                    "name": "午休验证患者",
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)
            with patch("apps.backend.checkup_backend.patient_api.utcnow", return_value=datetime(2026, 8, 31, 4, 30)):
                created = patient_client.post(
                    "/api/patient/plans",
                    json={
                        "hospitalID": admin["hospital"]["hospitalID"],
                        "packageID": package.json()["packageID"],
                        "profile": {},
                    },
                )
            self.assertEqual(created.status_code, 201, created.text)
            estimated_start = datetime.fromisoformat(
                created.json()["steps"][0]["estimatedStart"].removesuffix("Z")
            )
            self.assertGreaterEqual(estimated_start, datetime(2026, 8, 31, 5, 30))

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
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
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
            route_package = next(
                row for row in catalog.json()["packages"] if row["packageName"] == "基础体检套餐"
            )
            with patch(
                "apps.backend.checkup_backend.patient_api.utcnow",
                return_value=datetime(2026, 8, 31, 0, 15),
            ):
                plan = patient_client.post(
                    "/api/patient/plans",
                    json={
                        "hospitalID": admin["hospital"]["hospitalID"],
                        "packageID": route_package["packageID"],
                        "profile": {"fasting": "yes", "bladder": "normal"},
                    },
                )
            self.assertEqual(plan.status_code, 201, plan.text)
            self.assertIn("hospitalCoverUrl", plan.json())
            first_step, second_step = plan.json()["steps"]
            completed = patient_client.post(
                f"/api/patient/plans/{plan.json()['planID']}/steps/{first_step['detailID']}/complete"
            )
            self.assertEqual(completed.status_code, 200, completed.text)
            navigation = patient_client.get(
                f"/api/patient/plans/{plan.json()['planID']}/navigation",
                params={"detailID": second_step["detailID"]},
            )
            self.assertEqual(navigation.status_code, 200, navigation.text)
            self.assertEqual(navigation.json()["distanceMeters"], 60)
            self.assertEqual(navigation.json()["map"]["floorKey"], "1F")
            self.assertEqual(navigation.json()["map"]["routeCoordinates"], [[80.0, 30.0], [20.0, 30.0]])
            self.assertIn("蓝色路线", navigation.json()["floorInstruction"])

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
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
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

    def test_patient_plans_keep_their_status_snapshots(self):
        admin = self.register()
        department = self.create_department()
        exam = self.create_exam(department["deptID"], name="状态快照检查")
        package = self.client.post(
            "/api/packages",
            json={
                "packageName": "状态快照套餐",
                "includedItemIDs": [exam["itemID"]],
                "isPublished": True,
            },
        )
        self.assertEqual(package.status_code, 201, package.text)

        with TestClient(self.app) as patient_client:
            registered = patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000072",
                    "password": "patient-pass-123",
                    "name": "状态快照患者",
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)
            first_profile = patient_client.patch(
                "/api/patient/profile",
                json={"medicalHistory": "高血压史", "allergens": "青霉素"},
            )
            self.assertEqual(first_profile.status_code, 200, first_profile.text)
            with patch(
                "apps.backend.checkup_backend.patient_api.utcnow",
                return_value=datetime(2026, 8, 31, 0, 15),
            ):
                first = patient_client.post(
                    "/api/patient/plans",
                    json={
                        "hospitalID": admin["hospital"]["hospitalID"],
                        "packageID": package.json()["packageID"],
                        "profile": {"fasting": "yes", "bladder": "normal"},
                    },
                )
            self.assertEqual(first.status_code, 201, first.text)
            self.assertEqual(first.json()["profileSnapshot"]["medicalHistory"], "高血压史")
            first_step = first.json()["steps"][0]
            finished = patient_client.post(
                f"/api/patient/plans/{first.json()['planID']}/steps/{first_step['detailID']}/complete"
            )
            self.assertEqual(finished.status_code, 200, finished.text)
            self.assertTrue(finished.json()["finished"])

            second_profile = patient_client.patch(
                "/api/patient/profile",
                json={"medicalHistory": "无", "allergens": "无"},
            )
            self.assertEqual(second_profile.status_code, 200, second_profile.text)
            with patch(
                "apps.backend.checkup_backend.patient_api.utcnow",
                return_value=datetime(2026, 8, 31, 0, 30),
            ):
                second = patient_client.post(
                    "/api/patient/plans",
                    json={
                        "hospitalID": admin["hospital"]["hospitalID"],
                        "packageID": package.json()["packageID"],
                        "profile": {"fasting": "no", "bladder": "recentUrination"},
                    },
                )
            self.assertEqual(second.status_code, 201, second.text)
            history = patient_client.get("/api/patient/plans")
            self.assertEqual(history.status_code, 200, history.text)
            snapshots = {row["planID"]: row["profileSnapshot"] for row in history.json()}
            self.assertEqual(snapshots[first.json()["planID"]]["fasting"], "yes")
            self.assertEqual(snapshots[first.json()["planID"]]["medicalHistory"], "高血压史")
            self.assertEqual(snapshots[first.json()["planID"]]["allergens"], "青霉素")
            self.assertEqual(snapshots[second.json()["planID"]]["fasting"], "no")
            self.assertEqual(snapshots[second.json()["planID"]]["medicalHistory"], "无")
            self.assertEqual(snapshots[second.json()["planID"]]["allergens"], "无")

            with self.app.state.session_factory() as session:
                first_plan = session.get(ExamPlan, first.json()["planID"])
                second_plan = session.get(ExamPlan, second.json()["planID"])
                self.assertIsNotNone(first_plan.record_id)
                self.assertIsNotNone(second_plan.record_id)
                self.assertNotEqual(first_plan.record_id, second_plan.record_id)
                self.assertEqual(session.get(UserStatusInfo, first_plan.record_id).profile_data["fasting"], "yes")
                self.assertEqual(session.get(UserStatusInfo, second_plan.record_id).profile_data["fasting"], "no")

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
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
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

    def test_patient_can_create_multiple_scheduled_appointments(self):
        admin = self.register()
        department = self.create_department()
        exam = self.create_exam(department["deptID"], name="预约检查")
        package = self.client.post(
            "/api/packages",
            json={
                "packageName": "预约套餐",
                "includedItemIDs": [exam["itemID"]],
                "isPublished": True,
            },
        ).json()

        with TestClient(self.app) as patient_client:
            registered = patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000081",
                    "password": "patient-pass-123",
                    "name": "多预约患者",
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)
            created_plans = []
            with patch("apps.backend.checkup_backend.patient_api.utcnow", return_value=datetime(2026, 8, 31, 0, 0)):
                for appointment_at in ("2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"):
                    created = patient_client.post(
                        "/api/patient/plans",
                        json={
                            "hospitalID": admin["hospital"]["hospitalID"],
                            "packageID": package["packageID"],
                            "appointmentAt": appointment_at,
                            "profile": {"booked": "yes"},
                        },
                    )
                    self.assertEqual(created.status_code, 201, created.text)
                    created_plans.append(created.json())

            self.assertTrue(all(plan["planStatus"] == "待执行" for plan in created_plans))
            self.assertTrue(all(plan["steps"][0]["status"] == "pending" for plan in created_plans))
            self.assertEqual(len(patient_client.get("/api/patient/plans").json()), 2)

    def test_patient_can_pause_resume_and_end_active_plan(self):
        admin = self.register()
        department = self.create_department()
        exam = self.create_exam(department["deptID"], name="状态流转检查")
        package = self.client.post(
            "/api/packages",
            json={
                "packageName": "状态流转套餐",
                "includedItemIDs": [exam["itemID"]],
                "isPublished": True,
            },
        ).json()

        with TestClient(self.app) as patient_client:
            registered = patient_client.post(
                "/api/patient/auth/register",
                json={
                    "phone": "13900000082",
                    "password": "patient-pass-123",
                    "name": "状态流转患者",
                    "privacyConsent": True,
                    "privacyConsentVersion": "v0.3.1-2026-08-31",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)
            with patch("apps.backend.checkup_backend.patient_api.utcnow", return_value=datetime(2026, 8, 31, 0, 0)):
                created = patient_client.post(
                    "/api/patient/plans",
                    json={
                        "hospitalID": admin["hospital"]["hospitalID"],
                        "packageID": package["packageID"],
                        "profile": {},
                    },
                )
            self.assertEqual(created.status_code, 201, created.text)
            plan_id = created.json()["planID"]

            paused = patient_client.post(f"/api/patient/plans/{plan_id}/pause")
            self.assertEqual(paused.status_code, 200, paused.text)
            self.assertEqual(paused.json()["planStatus"], "已中断")
            self.assertEqual(patient_client.get("/api/patient/plans/current").json()["planID"], plan_id)

            with patch("apps.backend.checkup_backend.patient_api.utcnow", return_value=datetime(2026, 8, 31, 0, 0)):
                resumed = patient_client.post(f"/api/patient/plans/{plan_id}/resume")
            self.assertEqual(resumed.status_code, 200, resumed.text)
            self.assertEqual(resumed.json()["planStatus"], "进行中")
            self.assertTrue(resumed.json()["replanNotice"])

            ended = patient_client.post(f"/api/patient/plans/{plan_id}/finish")
            self.assertEqual(ended.status_code, 200, ended.text)
            self.assertEqual(ended.json()["planStatus"], "已结束")
            self.assertTrue(ended.json()["completedAt"])
            self.assertTrue(ended.json()["finished"])
            self.assertTrue(ended.json()["ended"])
            self.assertEqual(ended.json()["steps"][0]["status"], "skipped")
            self.assertIsNone(patient_client.get("/api/patient/plans/current").json())


if __name__ == "__main__":
    unittest.main()
