from datetime import datetime, timedelta
import inspect
import unittest
from unittest.mock import patch

from checkup_scheduler import (
    AdaptiveQueuePredictor,
    BatchPlannerConfig,
    DepartmentState,
    Exam,
    GlobalWaitFeedbackController,
    HybridPlannerConfig,
    PatientState,
    QueueSnapshot,
    RobustFeedbackConfig,
    TimeWindow,
    TravelTimeMatrix,
    WaitPrediction,
    WaitTimingFeedback,
    build_batch_schedule,
    build_hybrid_schedule,
    cp_sat_available,
    propagate_effective_deadlines,
)
from simulation.engine import (
    FEEDBACK_ABLATION_POLICIES,
    SIMULATION_SCENARIOS,
    HospitalDaySimulator,
    build_realistic_hospital_scenario,
    run_comparative_simulation,
)
from simulation.ground_truth import generate_ground_truth
from simulation.ground_truth import scenario_fingerprint
from simulation.experiments import (
    ExperimentConfig,
    run_repeated_experiment,
)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 24, hour, minute)


DAY = TimeWindow(at(8), at(17))


class DeadlinePropagationTests(unittest.TestCase):
    def test_single_level_prerequisite(self):
        exams = (
            Exam("A", "a", 10),
            Exam("T", "t", 20, prerequisites=("A",)),
        )
        states = propagate_effective_deadlines(
            exams,
            {"A": at(17), "T": at(16)},
            TravelTimeMatrix({("a", "t"): 5}),
            required_buffer_minutes=5,
        )
        self.assertEqual(states["A"].effective_latest_finish, at(15, 30))

    def test_multilevel_dag(self):
        exams = (
            Exam("A", "a", 10),
            Exam("B", "b", 15, prerequisites=("A",)),
            Exam("T", "t", 20, prerequisites=("B",)),
        )
        states = propagate_effective_deadlines(
            exams,
            {exam.id: at(17) for exam in exams},
            TravelTimeMatrix(default_minutes=5),
            required_buffer_minutes=5,
        )
        self.assertEqual(states["B"].effective_latest_finish, at(16, 30))
        self.assertEqual(states["A"].effective_latest_finish, at(16, 5))

    def test_multiple_successors_take_earliest_deadline(self):
        exams = (
            Exam("A", "a", 10),
            Exam("B", "b", 20, prerequisites=("A",)),
            Exam("C", "c", 10, prerequisites=("A",)),
        )
        states = propagate_effective_deadlines(
            exams,
            {"A": at(17), "B": at(16), "C": at(15)},
            TravelTimeMatrix(default_minutes=5),
            required_buffer_minutes=5,
        )
        self.assertEqual(states["A"].effective_latest_finish, at(14, 40))
        self.assertEqual(states["A"].successor_ids, ("B", "C"))

    def test_cycle_is_rejected(self):
        exams = (
            Exam("A", "a", 10, prerequisites=("B",)),
            Exam("B", "b", 10, prerequisites=("A",)),
        )
        with self.assertRaisesRegex(ValueError, "闭环"):
            propagate_effective_deadlines(
                exams,
                {"A": at(17), "B": at(17)},
                TravelTimeMatrix(),
            )

    def test_terminal_deadline_advances_upstream(self):
        critical = PatientState(
            "critical",
            (
                Exam("A", "shared", 10),
                Exam("T", "terminal", 10, prerequisites=("A",)),
            ),
            at(8),
            "lobby",
            availability_windows=(DAY,),
        )
        safe = PatientState(
            "safe",
            (Exam("S", "shared", 10),),
            at(8),
            "shared",
            availability_windows=(DAY,),
        )
        result = build_batch_schedule(
            (safe, critical),
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
            config=BatchPlannerConfig(high_completion_risk_slack_minutes=60),
        )
        steps = {(step.patient_id, step.exam_id): step for step in result.steps}
        self.assertLess(
            steps[("critical", "A")].effective_latest_finish,
            at(9),
        )
        self.assertLess(
            steps[("critical", "A")].start_at,
            steps[("safe", "S")].start_at,
        )

    def test_leaf_task_keeps_own_deadline(self):
        exam = Exam("leaf", "d", 10)
        states = propagate_effective_deadlines(
            (exam,),
            {"leaf": at(16)},
            TravelTimeMatrix(),
        )
        self.assertEqual(states["leaf"].effective_latest_finish, at(16))

    def test_completion_risk_beats_small_wait_advantage(self):
        critical = PatientState(
            "p-critical",
            (
                Exam("upstream", "shared", 10),
                Exam("terminal", "terminal", 10, prerequisites=("upstream",)),
            ),
            at(8),
            "lobby",
            availability_windows=(DAY,),
        )
        safe = PatientState(
            "p-safe",
            (Exam("ordinary", "shared", 10),),
            at(8),
            "shared",
            availability_windows=(DAY,),
        )
        result = build_batch_schedule(
            (safe, critical),
            {
                "shared": DepartmentState("shared", at(8), service_windows=(DAY,)),
                "terminal": DepartmentState("terminal", at(8), service_windows=(TimeWindow(at(8), at(9)),)),
            },
            TravelTimeMatrix({("lobby", "shared"): 5, ("shared", "terminal"): 5}),
            DAY,
            config=BatchPlannerConfig(high_completion_risk_slack_minutes=60),
        )
        ordered = [step for step in result.steps if step.department_id == "shared"]
        self.assertEqual(ordered[0].patient_id, "p-critical")
        self.assertGreater(ordered[0].completion_risk, ordered[1].completion_risk)

    def test_frozen_task_is_not_changed_by_critical_logic(self):
        patient = PatientState(
            "p",
            (Exam("A", "d", 10), Exam("T", "t", 10, prerequisites=("A",))),
            at(8),
            "d",
            availability_windows=(DAY,),
        )
        departments = {
            "d": DepartmentState("d", at(8), service_windows=(DAY,)),
            "t": DepartmentState("t", at(8), service_windows=(DAY,)),
        }
        first = build_batch_schedule((patient,), departments, TravelTimeMatrix(), DAY)
        second = build_batch_schedule(
            (patient,),
            departments,
            TravelTimeMatrix(),
            DAY,
            previous_schedule=first,
            config=BatchPlannerConfig(freeze_window_minutes=60),
        )
        self.assertTrue(second.steps[0].locked)
        self.assertEqual(second.steps[0].start_at, first.steps[0].start_at)


@unittest.skipUnless(cp_sat_available(), "OR-Tools optimization extra is required")
class CpSatConstraintTests(unittest.TestCase):
    def _solve(self, patients, departments, travel=TravelTimeMatrix()):
        result = build_hybrid_schedule(
            patients,
            departments,
            travel,
            DAY,
            hybrid_config=HybridPlannerConfig(
                strategy="cp_sat",
                cp_sat_time_limit_seconds=5,
                cp_sat_num_workers=1,
            ),
        )
        self.assertTrue(result.cp_sat_invoked)
        self.assertNotEqual(result.status, "FALLBACK")
        return result.schedule

    def test_cp_sat_respects_time_windows(self):
        patient = PatientState(
            "p",
            (Exam("e", "d", 20, allowed_windows=(TimeWindow(at(9), at(10)),)),),
            at(8),
            "d",
            availability_windows=(DAY,),
        )
        schedule = self._solve((patient,), {"d": DepartmentState("d", at(8), service_windows=(DAY,))})
        self.assertGreaterEqual(schedule.steps[0].start_at, at(9))
        self.assertLessEqual(schedule.steps[0].finish_at, at(10))

    def test_cp_sat_respects_capacity(self):
        patients = tuple(
            PatientState(f"p{i}", (Exam("e", "d", 30),), at(8), "d", availability_windows=(DAY,))
            for i in range(2)
        )
        schedule = self._solve(patients, {"d": DepartmentState("d", at(8), service_windows=(DAY,), capacity=1)})
        ordered = sorted(schedule.steps, key=lambda item: item.start_at)
        self.assertGreaterEqual(ordered[1].start_at, ordered[0].finish_at)

    def test_cp_sat_respects_prerequisites(self):
        patient = PatientState(
            "p",
            (Exam("a", "a", 20), Exam("b", "b", 20, prerequisites=("a",))),
            at(8),
            "a",
            availability_windows=(DAY,),
        )
        schedule = self._solve(
            (patient,),
            {
                "a": DepartmentState("a", at(8), service_windows=(DAY,)),
                "b": DepartmentState("b", at(8), service_windows=(DAY,)),
            },
            TravelTimeMatrix({("a", "b"): 5}),
        )
        steps = {step.exam_id: step for step in schedule.steps}
        self.assertGreaterEqual(steps["b"].start_at, steps["a"].finish_at + timedelta(minutes=5))


class SafetyAndIsolationTests(unittest.TestCase):
    def test_formal_cp_sat_experiment_fails_fast_without_ortools(self):
        config = ExperimentConfig(
            patient_count=2,
            replications=2,
            policies=("rolling_heuristic", "rolling_cp_sat"),
            treatment_policy="rolling_cp_sat",
        )
        with patch(
            "simulation.experiments.cp_sat_available",
            return_value=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "不会静默回退"):
                run_repeated_experiment(config)

    def test_cp_sat_fallback_is_reliable(self):
        patient = PatientState("p", (Exam("e", "d", 10),), at(8), "d")
        with patch("checkup_scheduler.hybrid.cp_sat_available", return_value=False):
            result = build_hybrid_schedule(
                (patient,),
                {"d": DepartmentState("d", at(8), service_windows=(DAY,))},
                TravelTimeMatrix(),
                DAY,
                hybrid_config=HybridPlannerConfig(strategy="cp_sat"),
            )
        self.assertEqual(result.backend, "heuristic")
        self.assertEqual(result.status, "FALLBACK")
        self.assertTrue(result.cp_sat_invoked)

    def test_feedback_ablation_configuration(self):
        scenario = build_realistic_hospital_scenario(2, seed=4)
        truth = generate_ground_truth(scenario, seed=5)
        configs = {
            policy: HospitalDaySimulator(scenario, truth, policy=policy).policy_config
            for policy in FEEDBACK_ABLATION_POLICIES
        }
        self.assertEqual(
            {
                key: (value.wait_feedback, value.personal_activity_feedback)
                for key, value in configs.items()
            },
            {
                "no_feedback": (False, False),
                "wait_feedback_only": (True, False),
                "personal_activity_feedback_only": (False, True),
                "dual_feedback": (True, True),
            },
        )

    def test_feedback_update_cap(self):
        predictor = AdaptiveQueuePredictor(
            default_service_minutes=10,
            bias_min_samples=5,
            simulation_samples=32,
            max_wait_bias_update_minutes=1,
        )
        controller = GlobalWaitFeedbackController(
            predictor,
            config=RobustFeedbackConfig(
                min_batch_size=5,
                max_mean_update_minutes=5,
                max_p90_update_minutes=5,
            ),
        )
        snapshot = QueueSnapshot("d", at(8), 1, 1, queued_service_minutes=(10,))
        before = predictor.predict(snapshot)
        for index in range(5):
            controller.ingest(
                WaitTimingFeedback(
                    f"e{index}", "d", at(8), 100, before
                )
            )
        after = predictor.predict(snapshot)
        self.assertLessEqual(after.mean_minutes - before.mean_minutes, 1.0)

    def test_duplicate_feedback_does_not_learn_twice(self):
        class Recorder:
            calls = 0

            def observe_wait_feedback_batch(self, department_id, *, mean_residuals, p90_residuals):
                self.calls += 1

        recorder = Recorder()
        controller = GlobalWaitFeedbackController(recorder)
        prediction = WaitPrediction("d", at(8), 10, 20, "test", 0)
        for index in range(5):
            item = WaitTimingFeedback(f"e{index}", "d", at(8), 15, prediction)
            controller.ingest(item)
            controller.ingest(item)
        self.assertEqual(recorder.calls, 1)

    def test_scenarios_share_ground_truth_across_policies_without_oracle_leak(self):
        for name in SIMULATION_SCENARIOS:
            scenario = build_realistic_hospital_scenario(4, seed=7, scenario_name=name)
            truth = generate_ground_truth(scenario, seed=8, scenario_name=name)
            results = run_comparative_simulation(
                scenario,
                truth,
                policies=("no_feedback", "dual_feedback"),
                observation_wait_bias_fraction=(0.20 if name == "predictor_bias" else 0.0),
            )
            self.assertEqual(
                {item.ground_truth.trace_fingerprint for item in results},
                {truth.trace_fingerprint},
            )
        for method in (
            HospitalDaySimulator._initialize_static_batch_plan,
            HospitalDaySimulator._replan,
            HospitalDaySimulator._patient_state,
            HospitalDaySimulator._next_exam,
        ):
            self.assertNotIn("ground_truth", inspect.getsource(method))

    def test_patient_interruptions_are_hidden_from_observable_scenario(self):
        normal = build_realistic_hospital_scenario(
            12,
            seed=17,
            scenario_name="normal_day",
        )
        interrupted = build_realistic_hospital_scenario(
            12,
            seed=17,
            scenario_name="patient_interruption",
        )
        self.assertEqual(scenario_fingerprint(normal), scenario_fingerprint(interrupted))
        truth = generate_ground_truth(
            interrupted,
            seed=18,
            scenario_name="patient_interruption",
        )
        self.assertTrue(
            any(item.interruption_windows for item in truth.patients.values())
        )


if __name__ == "__main__":
    unittest.main()
