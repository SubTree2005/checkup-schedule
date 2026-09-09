from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import test_patient_route_recovery as route_tests
from apps.backend.checkup_backend.models import DepartmentInfo, ExamInfo, HospitalInfo, HospitalSettings
from apps.backend.checkup_backend.security import AdminContext, get_current_admin


class DemoRestrictionsTest(unittest.TestCase):
    register = route_tests.PatientRouteRecoveryTest.register
    registration_workspace = route_tests.PatientRouteRecoveryTest.registration_workspace
    create_department = route_tests.PatientRouteRecoveryTest.create_department
    exam = route_tests.PatientRouteRecoveryTest.exam
    plan = route_tests.PatientRouteRecoveryTest.plan
    action = route_tests.PatientRouteRecoveryTest.action
    close = route_tests.PatientRouteRecoveryTest.close

    def setUp(self):
        route_tests.PatientRouteRecoveryTest.setUp(self)
        self.now = datetime(2026, 9, 12, 15, 50)  # Saturday, 23:50 in the hospital timezone.
        for module in ("patient_api", "api", "demo_restrictions"):
            self.enterContext(patch(f"apps.backend.checkup_backend.{module}.utcnow", return_value=self.now))
        self.hospital_id = self.admin["hospital"]["hospitalID"]

    def toggle(self, enabled):
        result = self.client.put("/api/demo-patients/restrictions", json={"enabled": enabled})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["restrictions"]["enabled"], enabled)
        return result.json()

    def expire(self):
        with self.app.state.session_factory() as db:
            db.get(HospitalSettings, self.hospital_id).demo_unrestricted_until = self.now
            db.commit()

    def test_night_route_ignores_restrictions_without_changing_original_configuration(self):
        first = self.exam("项目甲")
        prerequisite = self.exam("未选择的前置项目")
        second = self.exam("空腹憋尿检查", {"itemIDs": [prerequisite["itemID"]], "fastingHours": 8, "bladderReady": True})
        with self.app.state.session_factory() as db:
            db.get(HospitalInfo, self.hospital_id).open_time = "工作日08:00-09:00"
            db.get(HospitalSettings, self.hospital_id).is_available = False
            db.get(ExamInfo, first["itemID"]).is_active = False
            exam = db.get(ExamInfo, second["itemID"])
            exam.conflicts = [first["itemID"]]
            exam.allowed_time_slots = {"start": "08:00", "end": "08:10"}
            for item in (first, second):
                db.get(DepartmentInfo, item["deptID"]).is_available = False
            db.commit()
        payload = {"hospitalID": self.hospital_id, "selectedItemIDs": [first["itemID"], second["itemID"]], "profile": {"fasting": "no", "bladder": "recentUrination"}}
        self.assertEqual(self.patient.post("/api/patient/plans", json=payload).status_code, 409)
        enabled = self.toggle(True)
        self.assertEqual(enabled["restrictions"]["expiresAt"], "2026-09-12T17:50:00Z")
        catalog = self.patient.get(f"/api/patient/hospitals/{self.hospital_id}/catalog").json()
        self.assertTrue(catalog["hospital"]["isAvailable"])
        self.assertIn(first["itemID"], {item["itemID"] for item in catalog["exams"]})
        plan = self.plan([first, second], profile=payload["profile"])
        self.assertTrue(plan["demoUnrestricted"])
        self.assertEqual(plan["profileSnapshot"]["fasting"], "no")
        self.assertLess(datetime.fromisoformat(plan["steps"][0]["estimatedStart"].rstrip("Z")), self.now + timedelta(minutes=60))
        current = self.patient.get(f"/api/patient/plans/{plan['planID']}").json()
        self.assertFalse(any(step["status"] == "skipped" for step in current["steps"]))
        replan = self.patient.post(f"/api/patient/plans/{plan['planID']}/replan")
        self.assertEqual(replan.status_code, 200, replan.text)
        plan = replan.json()
        while not plan["finished"]:
            step = plan["steps"][plan["currentStepIndex"]]
            plan = self.action(plan, step, "start")
            plan = self.action(plan, step, "complete")
        self.assertEqual(plan["completedSteps"], 2)
        self.toggle(False)
        self.assertEqual(self.patient.post("/api/patient/plans", json=payload).status_code, 409)
        with self.app.state.session_factory() as db:
            self.assertFalse(db.get(HospitalSettings, self.hospital_id).is_available)
            self.assertFalse(db.get(ExamInfo, first["itemID"]).is_active)
            self.assertFalse(db.get(DepartmentInfo, second["deptID"]).is_available)
            self.assertEqual(db.get(HospitalInfo, self.hospital_id).open_time, "工作日08:00-09:00")
            exam = db.get(ExamInfo, second["itemID"])
            self.assertEqual(exam.prerequisites["fastingHours"], 8)
            self.assertEqual(exam.prerequisites["itemIDs"], [prerequisite["itemID"]])
            self.assertEqual(exam.conflicts, [first["itemID"]])
            self.assertEqual(exam.allowed_time_slots, {"start": "08:00", "end": "08:10"})

    def test_expiry_restores_preparation_and_live_closure_checks(self):
        exam = self.exam("空腹检查", {"fastingHours": 8})
        self.toggle(True)
        plan = self.plan([exam], appointmentAt="2026-09-12T16:00:00Z", profile={"fasting": "no"})
        self.expire()
        self.assertFalse(self.client.get("/api/demo-patients").json()["restrictions"]["enabled"])
        start = self.patient.post(f"/api/patient/plans/{plan['planID']}/steps/{plan['steps'][0]['detailID']}/start")
        self.assertEqual(start.status_code, 409, start.text)
        self.assertIn("准备条件", start.json()["detail"])
        replan = self.patient.post(f"/api/patient/plans/{plan['planID']}/replan")
        self.assertEqual(replan.status_code, 409, replan.text)
        self.toggle(True)
        self.action(plan, plan["steps"][0], "start")
        self.expire()
        self.close(exam)
        recovered = self.patient.get(f"/api/patient/plans/{plan['planID']}").json()
        self.assertFalse(recovered["demoUnrestricted"])
        self.assertEqual(recovered["steps"][0]["status"], "skipped")
        self.assertTrue(recovered["finished"])

    def test_weekend_midnight_booking_can_exceed_normal_slot_capacity(self):
        exam = self.exam("检查", {"fastingHours": 8})
        with self.app.state.session_factory() as db:
            db.get(HospitalInfo, self.hospital_id).open_time = "工作日08:00-09:00"
            settings = db.get(HospitalSettings, self.hospital_id)
            settings.is_available = False
            settings.appointment_slot_capacity = 1
            settings.appointment_days_ahead = 1
            db.commit()
        self.toggle(True)
        url = f"/api/patient/hospitals/{self.hospital_id}/appointment-slots"
        slots = self.patient.get(url).json()
        sunday = next(day for day in slots["dates"] if day["date"] == "2026-09-13")
        midnight = sunday["slots"][0]
        self.assertEqual(midnight["start"], "00:00")
        for _ in range(2):
            plan = self.plan([exam], appointmentAt=midnight["appointmentAt"], profile={"fasting": "no"})
            self.assertEqual(plan["planStatus"], "待执行")
        sunday = next(day for day in self.patient.get(url).json()["dates"] if day["date"] == "2026-09-13")
        self.assertTrue(sunday["slots"][0]["available"])
        self.assertEqual(sunday["slots"][0]["booked"], 2)
        self.expire()
        self.assertFalse(any(day["date"] == "2026-09-13" for day in self.patient.get(url).json()["dates"]))

    def test_toggle_is_owner_only_and_scoped_to_the_current_hospital(self):
        with TestClient(self.app) as other_client:
            other = self.register(other_client, phone="13800000002", hospital="另一医院")
            self.toggle(True)
            self.assertFalse(other_client.get("/api/demo-patients").json()["restrictions"]["enabled"])
            catalog = self.patient.get(f"/api/patient/hospitals/{other['hospital']['hospitalID']}/catalog").json()
            self.assertFalse(catalog["hospital"]["demoUnrestricted"])
        self.assertEqual(self.patient.put("/api/demo-patients/restrictions", json={"enabled": False}).status_code, 401)
        self.app.dependency_overrides[get_current_admin] = lambda: AdminContext("other", self.hospital_id, "管理员", "13800000003", False)
        try:
            self.assertEqual(self.client.put("/api/demo-patients/restrictions", json={"enabled": False}).status_code, 403)
        finally:
            del self.app.dependency_overrides[get_current_admin]
        self.assertTrue(self.client.get("/api/demo-patients").json()["restrictions"]["enabled"])


if __name__ == "__main__":
    unittest.main()
