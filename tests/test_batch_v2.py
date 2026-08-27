from datetime import datetime, timedelta
import unittest

from checkup_scheduler import (
    BatchPlannerConfig,
    DepartmentState,
    Exam,
    PatientState,
    TimeWindow,
    TravelTimeMatrix,
    build_batch_schedule,
)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 22, hour, minute)


DAY = TimeWindow(at(8), at(17))


class BatchV2ConstraintTests(unittest.TestCase):
    def test_patient_break_moves_exam_to_next_available_window(self):
        patient = PatientState(
            "p1",
            (
                Exam("long", "d", 160),
                Exam("after", "d", 60, prerequisites=("long",)),
            ),
            at(8),
            "d",
            availability_windows=(
                TimeWindow(at(8), at(11)),
                TimeWindow(at(13), at(17)),
            ),
        )
        result = build_batch_schedule(
            (patient,),
            {"d": DepartmentState("d", at(8), service_windows=(DAY,))},
            TravelTimeMatrix(),
            DAY,
        )

        self.assertTrue(result.feasible)
        steps = {step.exam_id: step for step in result.steps}
        self.assertEqual(steps["long"].start_at, at(8))
        self.assertEqual(steps["after"].start_at, at(13))

    def test_exam_cannot_cross_department_closing_time(self):
        patient = PatientState(
            "p1",
            (Exam("mri", "mri", 90),),
            at(16),
            "lobby",
            availability_windows=(DAY,),
        )
        result = build_batch_schedule(
            (patient,),
            {
                "mri": DepartmentState(
                    "mri",
                    at(8),
                    service_windows=(TimeWindow(at(8), at(17)),),
                )
            },
            TravelTimeMatrix(),
            DAY,
        )

        self.assertFalse(result.feasible)
        self.assertEqual(result.metrics.unscheduled_exam_count, 1)

    def test_single_resource_never_overlaps_patients(self):
        patients = tuple(
            PatientState(
                f"p{index}",
                (Exam("blood", "lab", 30),),
                at(8),
                "lab",
            )
            for index in range(2)
        )
        result = build_batch_schedule(
            patients,
            {"lab": DepartmentState("lab", at(8), capacity=1)},
            TravelTimeMatrix(),
            DAY,
        )

        ordered = sorted(result.steps, key=lambda step: step.start_at)
        self.assertEqual(ordered[0].start_at, at(8))
        self.assertGreaterEqual(ordered[1].start_at, ordered[0].finish_at)

    def test_two_resources_can_run_in_parallel(self):
        patients = tuple(
            PatientState(
                f"p{index}",
                (Exam("blood", "lab", 30),),
                at(8),
                "lab",
            )
            for index in range(2)
        )
        result = build_batch_schedule(
            patients,
            {"lab": DepartmentState("lab", at(8), capacity=2)},
            TravelTimeMatrix(),
            DAY,
        )

        self.assertEqual({step.start_at for step in result.steps}, {at(8)})
        self.assertEqual({step.resource_index for step in result.steps}, {0, 1})

    def test_hard_window_change_overrides_freeze(self):
        original = PatientState(
            "p1",
            (Exam("blood", "lab", 30),),
            at(8),
            "lab",
        )
        department = {"lab": DepartmentState("lab", at(8))}
        first = build_batch_schedule(
            (original,), department, TravelTimeMatrix(), DAY
        )
        changed = PatientState(
            "p1",
            original.exams,
            at(8),
            "lab",
            availability_windows=(TimeWindow(at(9), at(17)),),
        )
        second = build_batch_schedule(
            (changed,),
            department,
            TravelTimeMatrix(),
            DAY,
            previous_schedule=first,
            config=BatchPlannerConfig(freeze_window_minutes=60),
        )

        self.assertEqual(second.steps[0].start_at, at(9))
        self.assertFalse(second.steps[0].locked)

    def test_valid_near_term_step_is_frozen(self):
        original = PatientState(
            "p1",
            (Exam("blood", "lab", 30),),
            at(8),
            "lab",
        )
        department = {"lab": DepartmentState("lab", at(8))}
        first = build_batch_schedule(
            (original,), department, TravelTimeMatrix(), DAY
        )
        second = build_batch_schedule(
            (original,),
            department,
            TravelTimeMatrix(),
            DAY,
            previous_schedule=first,
            config=BatchPlannerConfig(freeze_window_minutes=60),
        )

        self.assertTrue(second.steps[0].locked)


class BatchV2ScaleTests(unittest.TestCase):
    def test_two_hundred_patients_with_twelve_exams_each(self):
        department_states = {
            f"d{index}": DepartmentState(
                f"d{index}",
                at(8),
                service_windows=(DAY,),
                capacity=20,
            )
            for index in range(10)
        }
        patients = tuple(
            PatientState(
                patient_id=f"p{patient_index:03d}",
                exams=tuple(
                    Exam(f"e{exam_index:02d}", f"d{exam_index % 10}", 5)
                    for exam_index in range(12)
                ),
                now=at(8),
                location_id="lobby",
                availability_windows=(DAY,),
            )
            for patient_index in range(200)
        )

        result = build_batch_schedule(
            patients,
            department_states,
            TravelTimeMatrix(default_minutes=1),
            DAY,
        )

        self.assertTrue(result.feasible)
        self.assertEqual(result.metrics.scheduled_exam_count, 2400)
        self.assertEqual(result.metrics.unscheduled_exam_count, 0)


if __name__ == "__main__":
    unittest.main()
