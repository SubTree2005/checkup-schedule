from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from checkup_scheduler import (
    AdaptiveQueuePredictor,
    DepartmentState,
    Exam,
    HybridPlannerConfig,
    PatientState,
    QueueSnapshot,
    RollingHorizonConfig,
    RollingHorizonScheduler,
    TimeWindow,
    TravelTimeMatrix,
    WaitPrediction,
    apply_wait_predictions,
    build_hybrid_schedule,
    cp_sat_available,
)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 22, hour, minute)


DAY = TimeWindow(at(8), at(17))


class WaitPredictionTests(unittest.TestCase):
    def test_queue_baseline_returns_mean_and_risk_upper_bound(self):
        predictor = AdaptiveQueuePredictor(default_service_minutes=15)
        prediction = predictor.predict(
            QueueSnapshot(
                department_id="ct",
                observed_at=at(8),
                queued_patients=2,
                capacity=1,
                in_service_remaining_minutes=(5,),
                recent_service_minutes=(10, 10),
            )
        )

        self.assertEqual(prediction.mean_minutes, 25)
        self.assertGreater(prediction.p90_minutes, prediction.mean_minutes)
        self.assertEqual(prediction.sample_count, 2)

    def test_online_completion_updates_service_time(self):
        predictor = AdaptiveQueuePredictor(default_service_minutes=20, smoothing=1)
        predictor.observe_service_completion("ct", 8)
        prediction = predictor.predict(
            QueueSnapshot("ct", at(8), queued_patients=3, capacity=1)
        )

        self.assertEqual(prediction.mean_minutes, 24)
        self.assertEqual(prediction.sample_count, 1)

    def test_stale_prediction_does_not_replace_department_snapshot(self):
        department = DepartmentState("ct", at(8), expected_wait_minutes=7)
        prediction = WaitPrediction("ct", at(8), 20, 30, "test", 10)
        updated = apply_wait_predictions(
            {"ct": department},
            {"ct": prediction},
            now=at(8, 20),
            max_age_minutes=15,
        )

        self.assertEqual(updated["ct"].expected_wait_minutes, 7)


class HybridAndRollingTests(unittest.TestCase):
    def test_hybrid_uses_p90_and_falls_back_without_ortools(self):
        patient = PatientState(
            "p1",
            (Exam("ct", "ct", 10),),
            at(8),
            "ct",
        )
        prediction = WaitPrediction("ct", at(8), 5, 25, "test", 100)
        with patch("checkup_scheduler.hybrid.cp_sat_available", return_value=False):
            result = build_hybrid_schedule(
                (patient,),
                {"ct": DepartmentState("ct", at(8))},
                TravelTimeMatrix(),
                DAY,
                wait_predictions={"ct": prediction},
            )

        self.assertEqual(result.backend, "heuristic")
        self.assertEqual(result.status, "FALLBACK")
        self.assertEqual(result.schedule.steps[0].start_at, at(8, 25))

    def test_prediction_change_can_change_route_without_model_coupling(self):
        patient = PatientState(
            "p1",
            (Exam("a", "a", 10), Exam("b", "b", 10)),
            at(8),
            "lobby",
        )
        departments = {
            "a": DepartmentState("a", at(8)),
            "b": DepartmentState("b", at(8), expected_wait_minutes=15),
        }
        initial = build_hybrid_schedule(
            (patient,),
            departments,
            TravelTimeMatrix(),
            DAY,
            hybrid_config=HybridPlannerConfig(strategy="heuristic"),
        )
        changed = build_hybrid_schedule(
            (patient,),
            departments,
            TravelTimeMatrix(),
            DAY,
            wait_predictions={
                "a": WaitPrediction("a", at(8), 30, 40, "test", 100)
            },
            hybrid_config=HybridPlannerConfig(strategy="heuristic"),
        )

        self.assertEqual(initial.schedule.patient_order("p1"), ("a", "b"))
        self.assertEqual(changed.schedule.patient_order("p1"), ("b", "a"))

    def test_rolling_horizon_respects_cadence_and_force_trigger(self):
        scheduler = RollingHorizonScheduler(
            DAY,
            TravelTimeMatrix(),
            config=RollingHorizonConfig(
                optimization_horizon_minutes=120,
                replan_interval_minutes=5,
                freeze_window_minutes=10,
            ),
            hybrid_config=HybridPlannerConfig(strategy="heuristic"),
        )
        patient = PatientState(
            "p1",
            (Exam("blood", "lab", 10),),
            at(8),
            "lab",
        )
        departments = {"lab": DepartmentState("lab", at(8))}

        first = scheduler.replan(at(8), (patient,), departments)
        early = scheduler.replan(at(8, 2), (patient,), departments)
        forced = scheduler.replan(
            at(8, 3),
            (patient,),
            departments,
            force=True,
            triggered_by="queue_jump",
        )

        self.assertTrue(first.replanned)
        self.assertEqual(first.optimization_horizon.end, at(10))
        self.assertFalse(early.replanned)
        self.assertTrue(forced.replanned)
        self.assertEqual(forced.triggered_by, "queue_jump")

    @unittest.skipUnless(cp_sat_available(), "OR-Tools optional dependency not installed")
    def test_cp_sat_improves_short_job_order_on_shared_resource(self):
        patients = (
            PatientState("long", (Exam("exam", "d", 60),), at(8), "d"),
            PatientState("short", (Exam("exam", "d", 10),), at(8), "d"),
        )
        result = build_hybrid_schedule(
            patients,
            {"d": DepartmentState("d", at(8), capacity=1)},
            TravelTimeMatrix(),
            DAY,
            hybrid_config=HybridPlannerConfig(
                strategy="cp_sat",
                cp_sat_time_limit_seconds=5,
            ),
        )

        self.assertEqual(result.backend, "cp_sat")
        self.assertEqual(result.schedule.steps[0].patient_id, "short")


if __name__ == "__main__":
    unittest.main()
