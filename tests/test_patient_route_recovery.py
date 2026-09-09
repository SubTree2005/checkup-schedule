"""Patient route closures, skips and follow-up appointments through the real API."""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import test_backend_api as backend_tests


class PatientRouteRecoveryTest(unittest.TestCase):
    register = backend_tests.BackendAPITest.register
    registration_workspace = backend_tests.BackendAPITest.registration_workspace
    create_department = backend_tests.BackendAPITest.create_department

    def setUp(self):
        backend_tests.BackendAPITest.setUp(self)
        self.addCleanup(lambda: backend_tests.BackendAPITest.tearDown(self))
        self.clock = patch("apps.backend.checkup_backend.patient_api.utcnow", return_value=datetime(2026, 9, 9, 0, 0))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.admin = self.register()
        self.patient = self.enterContext(TestClient(self.app))
        response = self.patient.post("/api/patient/auth/register", json={
            "phone": "13900000401", "password": "patient-pass-123", "name": "路线测试",
            "privacyConsent": True, "privacyConsentVersion": "v0.3.1-2026-08-31",
        })
        self.assertEqual(response.status_code, 201, response.text)

    def exam(self, name, prerequisites=None):
        department = self.create_department(name + "科室")
        response = self.client.post("/api/exams", json={
            "deptID": department["deptID"], "itemName": name, "duration": 5,
            "prerequisites": prerequisites or {}, "conflicts": [], "priority": 1,
            "allowedTimeSlots": {}, "isCritical": False, "isActive": True,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def plan(self, exams, **extra):
        response = self.patient.post("/api/patient/plans", json={
            "hospitalID": self.admin["hospital"]["hospitalID"],
            "selectedItemIDs": [exam["itemID"] for exam in exams], **extra,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def close(self, exam):
        response = self.client.patch(f"/api/departments/{exam['deptID']}", json={"isAvailable": False})
        self.assertEqual(response.status_code, 200, response.text)

    def action(self, plan, step, action):
        response = self.patient.post(f"/api/patient/plans/{plan['planID']}/steps/{step['detailID']}/{action}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_live_get_skips_closed_current_department_and_finishes_with_unfinished_items(self):
        exams = [self.exam("检查甲"), self.exam("检查乙")]
        plan = self.plan(exams)
        first = plan["steps"][0]
        self.close(next(exam for exam in exams if exam["itemID"] == first["itemID"]))
        response = self.patient.get(f"/api/patient/plans/{plan['planID']}")
        self.assertEqual(response.status_code, 200, response.text)
        recovered = response.json()
        self.assertEqual([step["status"] for step in recovered["steps"]], ["pending", "skipped"])
        self.assertEqual(recovered["completedSteps"], 0)
        self.assertEqual(recovered["currentStepIndex"], 0)
        self.assertTrue(recovered["replanNotice"])
        self.assertEqual(recovered["steps"][1]["actualEnd"], None)
        active = self.action(recovered, recovered["steps"][0], "start")
        ended = self.action(active, active["steps"][0], "complete")
        self.assertEqual(ended["planStatus"], "已结束")
        self.assertEqual(ended["completedSteps"], 1)
        self.assertEqual(ended["unfinishedItemIDs"], [first["itemID"]])
        self.assertIsNone(self.patient.get("/api/patient/plans/current").json())

    def test_complete_preserves_completion_when_a_later_department_closes(self):
        exams = [self.exam("甲"), self.exam("乙"), self.exam("丙")]
        plan = self.plan(exams)
        self.close(next(exam for exam in exams if exam["itemID"] == plan["steps"][1]["itemID"]))
        updated = self.action(plan, plan["steps"][0], "complete")
        self.assertEqual(updated["completedSteps"], 1)
        self.assertEqual([step["status"] for step in updated["steps"]], ["done", "pending", "skipped"])

    def test_all_departments_closed_current_endpoint_ends_without_fake_completion(self):
        exams = [self.exam("甲"), self.exam("乙")]
        plan = self.plan(exams)
        for exam in exams:
            self.close(exam)
        response = self.patient.get("/api/patient/plans/current")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["planStatus"], "已结束")
        self.assertEqual(response.json()["progress"], 0)
        self.assertEqual(set(response.json()["unfinishedItemIDs"]), {exam["itemID"] for exam in exams})

    def test_manual_skip_defers_transitive_dependents_and_keeps_other_items(self):
        first = self.exam("前置检查")
        second = self.exam("依赖检查", {"itemIDs": [first["itemID"]]})
        third = self.exam("后续检查", {"itemIDs": [second["itemID"]]})
        independent = self.exam("独立检查")
        plan = self.plan([first, second, third, independent])
        # Finish any independent step preceding the prerequisite.
        while plan["steps"][plan["currentStepIndex"]]["itemID"] != first["itemID"]:
            current = next(step for step in plan["steps"] if step["status"] in {"active", "pending"})
            if current["status"] == "pending":
                plan = self.action(plan, current, "start")
            plan = self.action(plan, current, "complete")
        current = next(step for step in plan["steps"] if step["itemID"] == first["itemID"])
        skipped = self.action(plan, current, "skip")
        by_item = {step["itemID"]: step for step in skipped["steps"]}
        for exam in (first, second, third):
            self.assertEqual(by_item[exam["itemID"]]["status"], "skipped")
        self.assertNotEqual(by_item[independent["itemID"]]["status"], "skipped")
        self.assertEqual(sorted(step["step"] for step in skipped["steps"]), [1, 2, 3, 4])

    def test_failed_skip_rolls_back_and_rejects_noncurrent_and_foreign_steps(self):
        exams = [self.exam("甲"), self.exam("乙"), self.exam("丙")]
        plan = self.plan(exams)
        prefix = f"/api/patient/plans/{plan['planID']}/steps/"
        self.assertEqual(self.patient.post(prefix + plan["steps"][1]["detailID"] + "/skip").status_code, 409)
        self.assertEqual(self.patient.post(prefix + "foreign/skip").status_code, 404)
        with patch("apps.backend.checkup_backend.patient_api._run_scheduler", return_value=SimpleNamespace(feasible=False)):
            failed = self.patient.post(prefix + plan["steps"][0]["detailID"] + "/skip")
        self.assertEqual(failed.status_code, 422, failed.text)
        stored = self.patient.get(f"/api/patient/plans/{plan['planID']}").json()
        self.assertEqual([step["status"] for step in stored["steps"]], ["active", "pending", "pending"])
        skipped = self.action(plan, plan["steps"][0], "skip")
        repeated = self.action(skipped, plan["steps"][0], "skip")
        self.assertEqual(repeated["steps"], skipped["steps"])

    def test_follow_up_books_only_unfinished_items_while_department_is_closed(self):
        exams = [self.exam("甲"), self.exam("乙")]
        plan = self.plan(exams)
        completed = self.action(plan, plan["steps"][0], "complete")
        ended = self.patient.post(f"/api/patient/plans/{plan['planID']}/finish").json()
        unfinished = next(exam for exam in exams if exam["itemID"] == ended["unfinishedItemIDs"][0])
        self.close(unfinished)
        follow_up = self.plan([unfinished], appointmentAt="2026-09-10T00:00:00Z", followUpPlanID=plan["planID"])
        self.assertEqual(follow_up["planStatus"], "待执行")
        self.assertEqual([step["itemID"] for step in follow_up["steps"]], [unfinished["itemID"]])
        original = self.patient.get(f"/api/patient/plans/{plan['planID']}").json()
        self.assertEqual(original["steps"], ended["steps"])
        invalid = self.patient.post("/api/patient/plans", json={
            "hospitalID": ended["hospitalID"], "selectedItemIDs": [completed["steps"][0]["itemID"]],
            "appointmentAt": "2026-09-10T00:00:00Z", "followUpPlanID": plan["planID"],
        })
        self.assertEqual(invalid.status_code, 422, invalid.text)

    def test_follow_up_retains_completed_prerequisite_credit_when_replanned(self):
        first = self.exam("前置检查")
        second = self.exam("后置检查", {"itemIDs": [first["itemID"]]})
        plan = self.plan([first, second])
        self.action(plan, plan["steps"][0], "complete")
        self.patient.post(f"/api/patient/plans/{plan['planID']}/finish")
        follow_up = self.plan([second], appointmentAt="2026-09-10T00:00:00Z", followUpPlanID=plan["planID"])
        response = self.patient.post(f"/api/patient/plans/{follow_up['planID']}/replan")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["steps"][0]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
