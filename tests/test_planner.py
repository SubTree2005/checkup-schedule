from datetime import datetime, timedelta
import unittest

from checkup_scheduler import (
    DepartmentAvailabilityUpdated,
    DepartmentState,
    DynamicScheduler,
    Exam,
    ExamCompleted,
    ExamStarted,
    PatientState,
    PlannerConfig,
    QueueWaitUpdated,
    TravelTimeMatrix,
    build_schedule,
)


NOW = datetime(2026, 8, 21, 8, 0)


def departments(**waits: int) -> dict[str, DepartmentState]:
    return {
        department_id: DepartmentState(
            id=department_id,
            observed_at=NOW,
            expected_wait_minutes=wait,
        )
        for department_id, wait in waits.items()
    }


class PlannerTests(unittest.TestCase):
    def test_uses_other_exam_while_long_queue_clears(self):
        patient = PatientState(
            "p1",
            (
                Exam("a", "a", 10),
                Exam("b", "b", 10),
            ),
            NOW,
            "lobby",
        )
        result = build_schedule(
            patient,
            departments(a=30, b=0),
            TravelTimeMatrix(default_minutes=0),
        )

        self.assertTrue(result.feasible)
        self.assertEqual(result.order, ("b", "a"))
        self.assertEqual(result.metrics.completion_minutes, 40)

    def test_respects_prerequisites_even_when_queue_is_worse(self):
        patient = PatientState(
            "p1",
            (
                Exam("blood", "lab", 5),
                Exam("ct", "ct", 10, prerequisites=("blood",)),
            ),
            NOW,
            "lobby",
        )
        result = build_schedule(
            patient,
            departments(lab=30, ct=0),
            TravelTimeMatrix(),
        )

        self.assertEqual(result.order, ("blood", "ct"))

    def test_medical_delay_cost_can_prioritize_exam(self):
        patient = PatientState(
            "p1",
            (
                Exam("normal", "normal", 10),
                Exam("urgent", "urgent", 10, delay_cost_per_minute=2),
            ),
            NOW,
            "lobby",
        )
        result = build_schedule(
            patient,
            departments(normal=0, urgent=0),
            TravelTimeMatrix(),
        )

        self.assertEqual(result.order, ("urgent", "normal"))

    def test_reports_infeasible_time_window(self):
        patient = PatientState(
            "p1",
            (
                Exam(
                    "blood",
                    "lab",
                    10,
                    latest_finish=NOW + timedelta(minutes=5),
                ),
            ),
            NOW,
            "lobby",
        )
        result = build_schedule(
            patient,
            departments(lab=0),
            TravelTimeMatrix(),
        )

        self.assertFalse(result.feasible)
        self.assertIn("最晚完成时间", "".join(result.reasons))

    def test_reports_cycle_in_prerequisites(self):
        patient = PatientState(
            "p1",
            (
                Exam("a", "a", 5, prerequisites=("b",)),
                Exam("b", "b", 5, prerequisites=("a",)),
            ),
            NOW,
            "lobby",
        )
        result = build_schedule(
            patient,
            departments(a=0, b=0),
            TravelTimeMatrix(),
        )

        self.assertFalse(result.feasible)
        self.assertIn("闭环", "".join(result.reasons))

    def test_stability_penalty_avoids_unnecessary_swap(self):
        patient = PatientState(
            "p1",
            (Exam("a", "a", 10), Exam("b", "b", 10)),
            NOW,
            "lobby",
            previous_order=("b", "a"),
        )
        result = build_schedule(
            patient,
            departments(a=0, b=0),
            TravelTimeMatrix(),
            PlannerConfig(reorder_penalty_per_inversion=4),
        )

        self.assertEqual(result.order, ("b", "a"))

    def test_in_progress_exam_is_fixed_and_not_reordered(self):
        patient = PatientState(
            "p1",
            (Exam("a", "a", 10), Exam("b", "b", 10)),
            NOW,
            "a",
            in_progress_exam_id="a",
            in_progress_finish_at=NOW + timedelta(minutes=7),
        )
        result = build_schedule(
            patient,
            departments(a=0, b=0),
            TravelTimeMatrix(),
        )

        self.assertEqual(result.order, ("a", "b"))
        self.assertTrue(result.steps[0].fixed_in_progress)

    def test_rejects_negative_initial_queue_wait(self):
        patient = PatientState(
            "p1",
            (Exam("a", "a", 10),),
            NOW,
            "lobby",
        )

        with self.assertRaisesRegex(ValueError, "预计等待时间不能为负数"):
            build_schedule(
                patient,
                departments(a=-1),
                TravelTimeMatrix(),
            )


class DynamicSchedulerTests(unittest.TestCase):
    def test_queue_update_reorders_remaining_exams(self):
        patient = PatientState(
            "p1",
            (Exam("a", "a", 10), Exam("b", "b", 10)),
            NOW,
            "lobby",
        )
        scheduler = DynamicScheduler(
            patient,
            departments(a=0, b=20),
            TravelTimeMatrix(),
            PlannerConfig(reorder_penalty_per_inversion=1),
        )
        self.assertEqual(scheduler.current_plan().order, ("a", "b"))

        outcome = scheduler.apply_event(
            QueueWaitUpdated(NOW + timedelta(minutes=1), "a", 40)
        )

        self.assertTrue(outcome.remaining_order_changed)
        self.assertEqual(outcome.current.order, ("b", "a"))

    def test_department_outage_makes_route_infeasible(self):
        patient = PatientState(
            "p1",
            (Exam("a", "a", 10),),
            NOW,
            "lobby",
        )
        scheduler = DynamicScheduler(
            patient,
            departments(a=0),
            TravelTimeMatrix(),
        )

        outcome = scheduler.apply_event(
            DepartmentAvailabilityUpdated(
                NOW + timedelta(minutes=1),
                "a",
                accepting_patients=False,
            )
        )

        self.assertFalse(outcome.current.feasible)
        self.assertIn("不可接诊", outcome.explanation)

    def test_completion_removes_exam_and_updates_location(self):
        patient = PatientState(
            "p1",
            (Exam("a", "a", 10), Exam("b", "b", 10)),
            NOW,
            "lobby",
        )
        scheduler = DynamicScheduler(
            patient,
            departments(a=0, b=0),
            TravelTimeMatrix({("a", "b"): 3}),
        )

        outcome = scheduler.apply_event(
            ExamCompleted(NOW + timedelta(minutes=10), "a")
        )

        self.assertEqual(outcome.current.order, ("b",))
        self.assertEqual(scheduler.patient.location_id, "a")
        self.assertIn("a", scheduler.patient.completed_exam_ids)

    def test_queue_event_cannot_interrupt_running_exam(self):
        patient = PatientState(
            "p1",
            (Exam("a", "a", 10), Exam("b", "b", 10)),
            NOW,
            "lobby",
        )
        scheduler = DynamicScheduler(
            patient,
            departments(a=0, b=0),
            TravelTimeMatrix(),
        )
        scheduler.apply_event(
            ExamStarted(NOW, "a", NOW + timedelta(minutes=10))
        )
        outcome = scheduler.apply_event(
            QueueWaitUpdated(NOW + timedelta(minutes=1), "b", 30)
        )

        self.assertEqual(outcome.current.order[0], "a")
        self.assertTrue(outcome.current.steps[0].fixed_in_progress)


if __name__ == "__main__":
    unittest.main()
