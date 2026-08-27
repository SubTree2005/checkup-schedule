from datetime import datetime
import json
import unittest

from checkup_scheduler import (
    AdaptiveQueuePredictor,
    QueueSnapshot,
    WaitPrediction,
    evaluate_wait_predictions,
)


def at(hour: int, minute: int = 0, *, day: int = 24) -> datetime:
    return datetime(2026, 8, day, hour, minute)


class MultiServerPredictionTests(unittest.TestCase):
    def test_idle_server_means_no_wait(self):
        predictor = AdaptiveQueuePredictor(default_service_minutes=10)
        prediction = predictor.predict(
            QueueSnapshot(
                "ct",
                at(8),
                queued_patients=0,
                capacity=2,
                in_service_remaining_minutes=(20,),
            )
        )

        self.assertEqual(prediction.mean_minutes, 0)
        self.assertEqual(prediction.p90_minutes, 0)

    def test_asymmetric_servers_are_simulated_instead_of_averaged(self):
        predictor = AdaptiveQueuePredictor(default_service_minutes=10)
        prediction = predictor.predict(
            QueueSnapshot(
                "ct",
                at(8),
                queued_patients=0,
                capacity=2,
                in_service_remaining_minutes=(1, 100),
            )
        )

        self.assertEqual(prediction.mean_minutes, 1)

    def test_known_queue_durations_are_used_in_fcfs_order(self):
        predictor = AdaptiveQueuePredictor(default_service_minutes=99)
        prediction = predictor.predict(
            QueueSnapshot(
                "ultrasound",
                at(8),
                queued_patients=3,
                capacity=2,
                in_service_remaining_minutes=(5, 9),
                queued_service_minutes=(4, 20, 3),
                operational_delay_minutes=2,
            )
        )

        self.assertEqual(prediction.mean_minutes, 14)
        self.assertEqual(prediction.p90_minutes, 14)

    def test_unknown_service_p90_is_deterministic(self):
        predictor = AdaptiveQueuePredictor(default_service_minutes=10, random_seed=7)
        snapshot = QueueSnapshot(
            "lab",
            at(9),
            queued_patients=5,
            capacity=2,
            recent_service_minutes=(4, 8, 12, 20),
        )

        self.assertEqual(predictor.predict(snapshot), predictor.predict(snapshot))

    def test_invalid_known_queue_shape_is_rejected(self):
        predictor = AdaptiveQueuePredictor()
        with self.assertRaises(ValueError):
            predictor.predict(
                QueueSnapshot(
                    "ct",
                    at(8),
                    queued_patients=2,
                    capacity=1,
                    queued_service_minutes=(10,),
                )
            )

    def test_non_finite_snapshot_value_is_rejected(self):
        predictor = AdaptiveQueuePredictor()
        with self.assertRaises(ValueError):
            predictor.predict(
                QueueSnapshot(
                    "ct",
                    at(8),
                    queued_patients=1,
                    capacity=1,
                    recent_service_minutes=(float("nan"),),
                )
            )


class OnlineLearningTests(unittest.TestCase):
    def test_time_bucket_learns_peak_service_duration(self):
        predictor = AdaptiveQueuePredictor(
            default_service_minutes=10,
            smoothing=1,
            min_bucket_samples=2,
        )
        predictor.observe_service_completion("ct", 10, at(8))
        predictor.observe_service_completion("ct", 10, at(8, 30))
        predictor.observe_service_completion("ct", 30, at(15))
        predictor.observe_service_completion("ct", 30, at(15, 30))

        morning = predictor.predict(QueueSnapshot("ct", at(8, 45), 1, 1))
        afternoon = predictor.predict(QueueSnapshot("ct", at(15, 45), 1, 1))

        self.assertEqual(morning.mean_minutes, 10)
        self.assertEqual(afternoon.mean_minutes, 30)

    def test_wait_outcomes_raise_undercovered_p90(self):
        predictor = AdaptiveQueuePredictor(
            default_service_minutes=10,
            cold_start_uncertainty_minutes=0,
            calibration_min_samples=3,
        )
        snapshot = QueueSnapshot("ct", at(8), 1, 1)
        before = predictor.predict(snapshot)
        for _ in range(3):
            predictor.observe_wait_outcome(before, before.p90_minutes + 7)

        after = predictor.predict(snapshot)

        self.assertEqual(after.p90_minutes, before.p90_minutes + 7)

    def test_state_round_trip_preserves_prediction(self):
        predictor = AdaptiveQueuePredictor(
            default_service_minutes=12,
            calibration_min_samples=2,
            random_seed=9,
        )
        predictor.observe_service_completion("ct", 8, at(8))
        predictor.observe_service_completion("ct", 16, at(8, 30))
        snapshot = QueueSnapshot("ct", at(8, 45), 4, 2)
        prediction = predictor.predict(snapshot)
        predictor.observe_wait_outcome(prediction, prediction.p90_minutes + 3)
        predictor.observe_wait_outcome(prediction, prediction.p90_minutes + 5)

        serialized = json.loads(json.dumps(predictor.export_state()))
        restored = AdaptiveQueuePredictor.from_state(serialized)

        self.assertEqual(predictor.predict(snapshot), restored.predict(snapshot))


class EvaluationTests(unittest.TestCase):
    def test_metrics_include_bias_and_p90_coverage(self):
        outcomes = (
            (WaitPrediction("a", at(8), 10, 15, "test", 1), 12),
            (WaitPrediction("a", at(9), 20, 25, "test", 1), 30),
        )

        metrics = evaluate_wait_predictions(outcomes)

        self.assertEqual(metrics.sample_count, 2)
        self.assertEqual(metrics.mae_minutes, 6)
        self.assertEqual(metrics.mean_error_minutes, -6)
        self.assertAlmostEqual(metrics.rmse_minutes, 7.211102550927978)
        self.assertEqual(metrics.p90_coverage, 0.5)


if __name__ == "__main__":
    unittest.main()
