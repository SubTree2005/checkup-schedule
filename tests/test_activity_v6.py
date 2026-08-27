from datetime import datetime, timedelta
import unittest

from checkup_scheduler import (
    AccelerometerBatch,
    AccelerometerSample,
    AdaptivePersonalActivityPredictor,
    DepartmentState,
    DynamicScheduler,
    Exam,
    PatientState,
    PersonalActivityFeedback,
    PersonalActivityFeedbackController,
    PersonalActivityPrediction,
    TimeWindow,
    TravelTimeMatrix,
    build_batch_schedule,
    preset_walking_speed_mps,
)


BASE = datetime(2026, 8, 24, 8)
DAY = TimeWindow(BASE, BASE.replace(hour=17))


class RecordingActivityPredictor:
    def __init__(self) -> None:
        self.batches = []

    def observe_activity_batch(self, patient_id, travel_time_factors):
        self.batches.append((patient_id, tuple(travel_time_factors)))


def activity_feedback(
    index,
    *,
    patient_id="p1",
    actual=10,
    baseline=5,
    confidence=1.0,
    distance_meters=None,
):
    return PersonalActivityFeedback(
        event_id=f"activity-{patient_id}-{index}",
        patient_id=patient_id,
        origin_id="lobby",
        destination_id="ct",
        occurred_at=BASE + timedelta(minutes=index),
        actual_activity_minutes=actual,
        baseline_travel_minutes=baseline,
        source="phone_accelerometer",
        confidence=confidence,
        distance_meters=distance_meters,
    )


def prediction(patient_id, factor):
    return PersonalActivityPrediction(
        patient_id=patient_id,
        generated_at=BASE,
        travel_time_factor=factor,
        model_version="test",
        sample_count=10,
    )


class ElapsedTimeSensorAdapter:
    def to_activity_feedback(self, batch):
        minutes = (batch.ended_at - batch.started_at).total_seconds() / 60
        return PersonalActivityFeedback(
            event_id=batch.event_id,
            patient_id=batch.patient_id,
            origin_id=batch.origin_id,
            destination_id=batch.destination_id,
            occurred_at=batch.ended_at,
            actual_activity_minutes=minutes,
            baseline_travel_minutes=batch.baseline_travel_minutes,
            source=batch.source,
            confidence=0.9,
        )


def sensor_batch():
    return AccelerometerBatch(
        event_id="sensor-1",
        patient_id="p1",
        origin_id="lobby",
        destination_id="ct",
        started_at=BASE,
        ended_at=BASE + timedelta(minutes=6),
        baseline_travel_minutes=5,
        samples=(
            AccelerometerSample(BASE, 0.0, 0.0, 1.0),
            AccelerometerSample(
                BASE + timedelta(seconds=200),
                0.1,
                0.2,
                0.9,
            ),
        ),
    )


class PersonalActivityFeedbackTests(unittest.TestCase):
    def test_single_trip_does_not_update_personal_model(self):
        predictor = RecordingActivityPredictor()
        controller = PersonalActivityFeedbackController(predictor)

        result = controller.ingest(activity_feedback(1))

        self.assertTrue(result.accepted)
        self.assertFalse(result.model_updated)
        self.assertEqual(result.samples_buffered, 1)
        self.assertEqual(predictor.batches, [])

    def test_patients_have_independent_activity_buffers(self):
        predictor = RecordingActivityPredictor()
        controller = PersonalActivityFeedbackController(predictor)
        controller.ingest(activity_feedback(1, patient_id="p1"))
        controller.ingest(activity_feedback(1, patient_id="p2"))

        self.assertEqual(controller.pending_count("p1"), 1)
        self.assertEqual(controller.pending_count("p2"), 1)

    def test_one_atypical_trip_does_not_move_personal_median(self):
        predictor = AdaptivePersonalActivityPredictor(smoothing=0.2)
        controller = PersonalActivityFeedbackController(predictor)

        for index, actual in enumerate((5, 5, 100)):
            controller.ingest(activity_feedback(index, actual=actual))

        personal = predictor.predict("p1", BASE)
        self.assertEqual(personal.travel_time_factor, 1.0)

    def test_repeated_slow_trips_update_only_that_patient(self):
        predictor = AdaptivePersonalActivityPredictor(smoothing=0.2)
        controller = PersonalActivityFeedbackController(predictor)

        for index in range(3):
            controller.ingest(activity_feedback(index, actual=10, baseline=5))

        slow = predictor.predict("p1", BASE)
        untouched = predictor.predict("p2", BASE)
        self.assertAlmostEqual(slow.travel_time_factor, 1.2)
        self.assertEqual(untouched.travel_time_factor, 1.0)

    def test_low_confidence_sensor_result_is_ignored(self):
        predictor = RecordingActivityPredictor()
        controller = PersonalActivityFeedbackController(predictor)

        result = controller.ingest(activity_feedback(1, confidence=0.1))

        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_reason, "sensor_confidence_too_low")
        self.assertEqual(controller.pending_count("p1"), 0)

    def test_sensor_adapter_port_converts_raw_xyz_batch(self):
        predictor = RecordingActivityPredictor()
        controller = PersonalActivityFeedbackController(predictor)

        result = controller.ingest_sensor_batch(
            sensor_batch(),
            ElapsedTimeSensorAdapter(),
        )

        self.assertIsNotNone(result)
        self.assertTrue(result.accepted)
        self.assertEqual(result.samples_buffered, 1)

    def test_sensor_adapter_cannot_change_patient_identity(self):
        class WrongPatientAdapter(ElapsedTimeSensorAdapter):
            def to_activity_feedback(self, batch):
                value = super().to_activity_feedback(batch)
                return PersonalActivityFeedback(
                    event_id=value.event_id,
                    patient_id="other",
                    origin_id=value.origin_id,
                    destination_id=value.destination_id,
                    occurred_at=value.occurred_at,
                    actual_activity_minutes=value.actual_activity_minutes,
                    baseline_travel_minutes=value.baseline_travel_minutes,
                )

        controller = PersonalActivityFeedbackController(
            RecordingActivityPredictor()
        )

        with self.assertRaises(ValueError):
            controller.ingest_sensor_batch(sensor_batch(), WrongPatientAdapter())


class PersonalActivityPredictionTests(unittest.TestCase):
    def test_six_demographic_presets_initialize_first_plan(self):
        predictor = AdaptivePersonalActivityPredictor()

        young = predictor.predict("young", BASE, age_years=25, gender="M")
        older = predictor.predict("older", BASE, age_years=70, gender="F")

        self.assertEqual(young.sample_count, 0)
        self.assertEqual(young.profile_version, 1)
        self.assertAlmostEqual(young.current_speed_mps, 1.43)
        self.assertAlmostEqual(older.current_speed_mps, 1.29)
        self.assertLess(young.travel_time_factor, older.travel_time_factor)
        self.assertGreater(
            preset_walking_speed_mps(25, "M"),
            preset_walking_speed_mps(70, "F"),
        )

    def test_distance_feedback_updates_speed_profile_and_version(self):
        predictor = AdaptivePersonalActivityPredictor(smoothing=0.2)
        predictor.register_profile("p1", 25, "M", registered_at=BASE)
        controller = PersonalActivityFeedbackController(predictor)

        for index in range(3):
            controller.ingest(
                activity_feedback(
                    index,
                    actual=5,
                    baseline=4,
                    distance_meters=300,
                )
            )

        profile = predictor.predict("p1", BASE + timedelta(minutes=10))
        self.assertLess(profile.current_speed_mps, 1.43)
        self.assertEqual(profile.profile_version, 2)
        self.assertEqual(profile.total_trips, 3)
        self.assertEqual(profile.total_distance_meters, 900)
        self.assertAlmostEqual(profile.confidence, 0.3)

    def test_state_round_trip_preserves_personal_factor(self):
        predictor = AdaptivePersonalActivityPredictor(smoothing=0.2)
        predictor.observe_activity_batch("p1", (2.0, 2.0, 2.0))

        restored = AdaptivePersonalActivityPredictor.from_state(
            predictor.export_state()
        )

        self.assertEqual(
            predictor.predict("p1", BASE),
            restored.predict("p1", BASE),
        )

    def test_batch_scheduler_applies_different_travel_times_per_patient(self):
        patients = (
            PatientState("fast", (Exam("ct", "ct", 10),), BASE, "lobby"),
            PatientState("slow", (Exam("ct", "ct", 10),), BASE, "lobby"),
        )
        result = build_batch_schedule(
            patients,
            {"ct": DepartmentState("ct", BASE, capacity=2)},
            TravelTimeMatrix({("lobby", "ct"): 4}),
            DAY,
            activity_predictions={
                "fast": prediction("fast", 0.5),
                "slow": prediction("slow", 2.0),
            },
        )
        travel_by_patient = {
            step.patient_id: step.travel_minutes for step in result.steps
        }

        self.assertEqual(travel_by_patient, {"fast": 2, "slow": 8})

    def test_dynamic_scheduler_can_receive_new_prediction_contract(self):
        patient = PatientState(
            "p1",
            (Exam("ct", "ct", 10),),
            BASE,
            "lobby",
        )
        scheduler = DynamicScheduler(
            patient,
            {"ct": DepartmentState("ct", BASE)},
            TravelTimeMatrix({("lobby", "ct"): 5}),
        )
        self.assertEqual(scheduler.current_plan().steps[0].travel_minutes, 5)

        scheduler.update_activity_prediction(prediction("p1", 2.0))

        self.assertEqual(scheduler.current_plan().steps[0].travel_minutes, 10)


if __name__ == "__main__":
    unittest.main()
