from __future__ import annotations

import unittest
from datetime import datetime

from checkup_scheduler.batch import BatchPlannerConfig, build_batch_schedule
from checkup_scheduler.models import (
    DepartmentState,
    Exam,
    PatientState,
    TimeWindow,
    TravelTimeMatrix,
)
from checkup_scheduler.activity_prediction import PersonalActivityPrediction
from checkup_scheduler.medical_rules import MedicalRuleContext
from simulation.engine import (
    HospitalDaySimulator,
    V10_COMPARISON_POLICIES,
    build_realistic_hospital_scenario,
)
from simulation.ground_truth import generate_ground_truth


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 24, hour, minute)


DAY = TimeWindow(at(8), at(17))


class WaitOrientedHeuristicTests(unittest.TestCase):
    def test_large_exam_is_softly_pulled_forward(self):
        patient = PatientState(
            "p",
            (Exam("a_short", "d", 5), Exam("z_long", "d", 40)),
            at(8),
            "d",
            availability_windows=(DAY,),
        )
        result = build_batch_schedule(
            (patient,),
            {"d": DepartmentState("d", at(8), service_windows=(DAY,))},
            TravelTimeMatrix(),
            DAY,
            config=BatchPlannerConfig(wait_oriented=True),
        )
        self.assertEqual(result.patient_order("p")[0], "z_long")

    def test_static_critical_flag_is_respected(self):
        patient = PatientState(
            "p",
            (
                Exam("a_ordinary", "d", 10),
                Exam("z_critical", "d", 10, is_critical=True),
            ),
            at(8),
            "d",
            availability_windows=(DAY,),
        )
        result = build_batch_schedule(
            (patient,),
            {"d": DepartmentState("d", at(8), service_windows=(DAY,))},
            TravelTimeMatrix(),
            DAY,
            config=BatchPlannerConfig(wait_oriented=True),
        )
        self.assertEqual(result.patient_order("p")[0], "z_critical")

    def test_slow_patient_prefers_same_floor_when_other_costs_tie(self):
        patient = PatientState(
            "slow",
            (Exam("a_cross", "cross", 10), Exam("z_same", "same", 10)),
            at(8),
            "origin",
            availability_windows=(DAY,),
        )
        prediction = PersonalActivityPrediction(
            patient_id="slow",
            generated_at=at(8),
            travel_time_factor=1.5,
            model_version="test",
            sample_count=3,
        )
        result = build_batch_schedule(
            (patient,),
            {
                "origin": DepartmentState("origin", at(8), floor=1),
                "cross": DepartmentState("cross", at(8), service_windows=(DAY,), floor=2),
                "same": DepartmentState("same", at(8), service_windows=(DAY,), floor=1),
            },
            TravelTimeMatrix(
                {("origin", "cross"): 5, ("origin", "same"): 5}
            ),
            DAY,
            config=BatchPlannerConfig(wait_oriented=True),
            activity_predictions={"slow": prediction},
        )
        self.assertEqual(result.patient_order("slow")[0], "z_same")

    def test_medical_rule_interface_can_reject_candidate(self):
        class RejectRestrictedExam:
            def evaluate(self, context: MedicalRuleContext) -> str | None:
                return "requires_business_clearance" if context.exam.id == "restricted" else None

        patient = PatientState(
            "p",
            (Exam("allowed", "d", 10), Exam("restricted", "d", 10)),
            at(8),
            "d",
            availability_windows=(DAY,),
        )
        result = build_batch_schedule(
            (patient,),
            {"d": DepartmentState("d", at(8), service_windows=(DAY,))},
            TravelTimeMatrix(),
            DAY,
            config=BatchPlannerConfig(wait_oriented=True),
            medical_rules=(RejectRestrictedExam(),),
        )
        self.assertEqual(result.patient_order("p"), ("allowed",))
        self.assertEqual(result.unscheduled[0].exam_id, "restricted")

    def test_accumulated_wait_prioritizes_starved_patient(self):
        fresh = PatientState(
            "fresh",
            (Exam("e", "d", 10),),
            at(8),
            "d",
            availability_windows=(DAY,),
        )
        starved = PatientState(
            "starved",
            (Exam("e", "d", 10),),
            at(8),
            "d",
            availability_windows=(DAY,),
            accumulated_wait_minutes=120,
            continuous_wait_minutes=30,
            minutes_since_last_completion=90,
        )
        result = build_batch_schedule(
            (fresh, starved),
            {"d": DepartmentState("d", at(8), service_windows=(DAY,))},
            TravelTimeMatrix(),
            DAY,
            config=BatchPlannerConfig(wait_oriented=True),
        )
        self.assertEqual(result.steps[0].patient_id, "starved")

    def test_wait_objective_can_downgrade_critical_path_to_soft_tiebreaker(self):
        critical = PatientState(
            "critical",
            (
                Exam("upstream", "shared", 10),
                Exam("terminal", "terminal", 10, prerequisites=("upstream",)),
            ),
            at(8),
            "lobby",
            availability_windows=(DAY,),
        )
        ordinary = PatientState(
            "ordinary",
            (Exam("ordinary", "shared", 10),),
            at(8),
            "shared",
            availability_windows=(DAY,),
        )
        result = build_batch_schedule(
            (critical, ordinary),
            {
                "shared": DepartmentState("shared", at(8), service_windows=(DAY,)),
                "terminal": DepartmentState(
                    "terminal",
                    at(8),
                    service_windows=(TimeWindow(at(8), at(9)),),
                ),
            },
            TravelTimeMatrix({("lobby", "shared"): 5, ("shared", "terminal"): 5}),
            DAY,
            config=BatchPlannerConfig(
                wait_oriented=True,
                critical_path_soft_weight=0,
            ),
        )
        shared = [step for step in result.steps if step.department_id == "shared"]
        self.assertEqual(shared[0].patient_id, "ordinary")

    def test_wait_state_rejects_negative_values(self):
        patient = PatientState(
            "p",
            (Exam("e", "d", 10),),
            at(8),
            "d",
            accumulated_wait_minutes=-1,
        )
        with self.assertRaisesRegex(ValueError, "不能为负数"):
            build_batch_schedule(
                (patient,),
                {"d": DepartmentState("d", at(8), service_windows=(DAY,))},
                TravelTimeMatrix(),
                DAY,
                config=BatchPlannerConfig(wait_oriented=True),
            )


class V10SimulationTests(unittest.TestCase):
    def test_v10_policy_matrix_and_tail_metrics(self):
        scenario = build_realistic_hospital_scenario(6, seed=17)
        truth = generate_ground_truth(scenario, seed=18)
        configs = {
            policy: HospitalDaySimulator(scenario, truth, policy=policy).policy_config
            for policy in V10_COMPARISON_POLICIES[1:]
        }
        self.assertTrue(all(config.wait_oriented for config in configs.values()))
        self.assertEqual(
            {
                policy: (config.wait_feedback, config.personal_activity_feedback)
                for policy, config in configs.items()
            },
            {
                "v10_no_feedback": (False, False),
                "v10_wait_feedback_only": (True, False),
                "v10_personal_activity_feedback_only": (False, True),
                "v10_dual_feedback": (True, True),
            },
        )
        result = HospitalDaySimulator(
            scenario,
            truth,
            policy="v10_no_feedback",
        ).run()
        self.assertGreaterEqual(result.metrics.p99_wait_minutes, result.metrics.p95_wait_minutes)
        self.assertGreaterEqual(result.metrics.max_wait_minutes, result.metrics.p99_wait_minutes)
        self.assertGreaterEqual(
            result.metrics.patients_waiting_over_60m,
            result.metrics.patients_waiting_over_90m,
        )
        self.assertGreaterEqual(
            result.metrics.patients_waiting_over_90m,
            result.metrics.patients_waiting_over_120m,
        )
        self.assertGreaterEqual(result.metrics.peak_department_queue_length, 0)


if __name__ == "__main__":
    unittest.main()
