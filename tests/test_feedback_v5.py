from datetime import datetime, timedelta
import inspect
import unittest

from checkup_scheduler import (
    AdaptiveQueuePredictor,
    GlobalWaitFeedbackController,
    QueueSnapshot,
    RobustFeedbackConfig,
    RollingHorizonScheduler,
    WaitTimingFeedback,
)


BASE = datetime(2026, 8, 24, 8)


class RecordingWaitPredictor:
    def __init__(self) -> None:
        self.wait_batches = []

    def observe_wait_feedback_batch(
        self,
        department_id,
        *,
        mean_residuals,
        p90_residuals,
    ):
        self.wait_batches.append(
            (department_id, tuple(mean_residuals), tuple(p90_residuals))
        )


def wait_feedback(index, prediction, *, department_id="ct", actual=20):
    return WaitTimingFeedback(
        event_id=f"wait-{department_id}-{index}",
        department_id=department_id,
        occurred_at=BASE + timedelta(minutes=index),
        actual_wait_minutes=actual,
        prediction=prediction,
    )


class GlobalWaitFeedbackBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.prediction = AdaptiveQueuePredictor(
            default_service_minutes=10,
            cold_start_uncertainty_minutes=0,
        ).predict(QueueSnapshot("ct", BASE, 1, 1))

    def test_scheduler_consumes_prediction_contracts_not_feedback_models(self):
        parameters = inspect.signature(RollingHorizonScheduler.replan).parameters

        self.assertIn("wait_predictions", parameters)
        self.assertIn("activity_predictions", parameters)
        self.assertNotIn("feedback", parameters)
        self.assertNotIn("predictor", parameters)

    def test_wait_feedback_object_has_no_personal_activity_field(self):
        parameters = inspect.signature(WaitTimingFeedback).parameters

        self.assertIn("actual_wait_minutes", parameters)
        self.assertNotIn("actual_activity_minutes", parameters)
        self.assertNotIn("patient_id", parameters)

    def test_single_wait_is_buffered_without_model_update(self):
        predictor = RecordingWaitPredictor()
        controller = GlobalWaitFeedbackController(predictor)

        result = controller.ingest(wait_feedback(1, self.prediction))

        self.assertTrue(result.accepted)
        self.assertFalse(result.model_updated)
        self.assertEqual(result.wait_samples_buffered, 1)
        self.assertEqual(predictor.wait_batches, [])

    def test_departments_have_independent_global_buffers(self):
        predictor = RecordingWaitPredictor()
        controller = GlobalWaitFeedbackController(predictor)
        lab_prediction = AdaptiveQueuePredictor(
            default_service_minutes=8,
            cold_start_uncertainty_minutes=0,
        ).predict(QueueSnapshot("lab", BASE, 1, 1))

        for index in range(4):
            controller.ingest(wait_feedback(index, self.prediction))
            controller.ingest(
                wait_feedback(
                    index,
                    lab_prediction,
                    department_id="lab",
                )
            )

        self.assertEqual(controller.pending_count("ct"), 4)
        self.assertEqual(controller.pending_count("lab"), 4)
        self.assertEqual(predictor.wait_batches, [])

    def test_duplicate_wait_event_is_idempotent(self):
        predictor = RecordingWaitPredictor()
        controller = GlobalWaitFeedbackController(predictor)
        event = wait_feedback(1, self.prediction)
        controller.ingest(event)

        duplicate = controller.ingest(event)

        self.assertTrue(duplicate.duplicate)
        self.assertFalse(duplicate.accepted)
        self.assertEqual(controller.pending_count("ct"), 1)

    def test_wait_outlier_is_winsorized_before_training(self):
        predictor = RecordingWaitPredictor()
        controller = GlobalWaitFeedbackController(predictor)

        for index, actual in enumerate((12, 13, 12, 14, 300)):
            result = controller.ingest(
                wait_feedback(index, self.prediction, actual=actual)
            )

        self.assertTrue(result.model_updated)
        _, means, p90s = predictor.wait_batches[0]
        self.assertLess(max(means), 10)
        self.assertLess(max(p90s), 10)


class AdaptiveGlobalWaitFeedbackTests(unittest.TestCase):
    def test_consistent_wait_feedback_adjusts_mean_then_p90(self):
        predictor = AdaptiveQueuePredictor(
            default_service_minutes=10,
            smoothing=0.2,
            cold_start_uncertainty_minutes=0,
            bias_min_samples=5,
            calibration_min_samples=20,
        )
        controller = GlobalWaitFeedbackController(predictor)
        snapshot = QueueSnapshot("ct", BASE, 1, 1)
        original = predictor.predict(snapshot)

        for index in range(5):
            controller.ingest(wait_feedback(index, original, actual=20))
        after_first_batch = predictor.predict(snapshot)

        self.assertEqual(after_first_batch.mean_minutes, 12)
        self.assertEqual(after_first_batch.p90_minutes, 12)

        for index in range(5, 20):
            controller.ingest(wait_feedback(index, original, actual=20))
        calibrated = predictor.predict(snapshot)

        self.assertAlmostEqual(calibrated.mean_minutes, 15.904)
        self.assertEqual(calibrated.p90_minutes, 20)

    def test_v4_predictor_state_can_be_migrated(self):
        predictor = AdaptiveQueuePredictor(default_service_minutes=10)
        predictor.observe_service_completion("ct", 12, BASE)
        old_state = predictor.export_state()
        old_state["model_version"] = "adaptive-multiserver-v2"
        old_state["config"].pop("bias_min_samples")
        old_state.pop("wait_bias_stats")

        restored = AdaptiveQueuePredictor.from_state(old_state)

        snapshot = QueueSnapshot("ct", BASE, 1, 1)
        self.assertEqual(
            predictor.predict(snapshot).mean_minutes,
            restored.predict(snapshot).mean_minutes,
        )


class FeedbackConfigTests(unittest.TestCase):
    def test_wait_minimum_batch_cannot_be_one(self):
        with self.assertRaises(ValueError):
            RobustFeedbackConfig(min_batch_size=1)


if __name__ == "__main__":
    unittest.main()
