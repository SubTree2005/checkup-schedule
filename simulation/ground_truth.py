"""Hidden physical reality for simulation, isolated from every scheduling policy.

This module does not import planners, predictors, feedback controllers, or the
simulation engine.  A policy receives only the observable ``HospitalScenario``;
the engine alone receives this immutable trace and uses it to execute actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import blake2b, sha256
import json
from math import ceil
from random import Random
from types import MappingProxyType
from typing import Any, Mapping, TYPE_CHECKING

from checkup_scheduler.models import TimeWindow

if TYPE_CHECKING:
    from .engine import HospitalScenario


@dataclass(frozen=True, slots=True)
class PatientGroundTruth:
    patient_id: str
    actual_arrival: datetime
    true_mobility_factor: float
    adherence_delay_minutes: int
    service_minutes_by_exam: Mapping[str, int]
    walk_minutes_by_edge: Mapping[tuple[str, str], int]
    interruption_windows: tuple[TimeWindow, ...] = ()


@dataclass(frozen=True, slots=True)
class SimulationGroundTruth:
    seed: int
    scenario_fingerprint: str
    trace_fingerprint: str
    patients: Mapping[str, PatientGroundTruth]
    resource_downtimes: Mapping[str, Mapping[int, tuple[TimeWindow, ...]]]
    service_slowdowns: Mapping[str, tuple[tuple[TimeWindow, float], ...]]

    def patient(self, patient_id: str) -> PatientGroundTruth:
        return self.patients[patient_id]

    def walk_minutes(
        self,
        patient_id: str,
        origin_id: str,
        destination_id: str,
    ) -> int:
        if origin_id == destination_id:
            return 0
        return self.patients[patient_id].walk_minutes_by_edge[
            (origin_id, destination_id)
        ]

    def service_minutes(
        self,
        patient_id: str,
        exam_id: str,
        started_at: datetime | None = None,
    ) -> int:
        base = self.patients[patient_id].service_minutes_by_exam[exam_id]
        if started_at is None:
            return base
        factor = 1.0
        for window, candidate in self.service_slowdowns.get(exam_id, ()):
            if window.start <= started_at < window.end:
                factor = max(factor, candidate)
        return max(1, ceil(base * factor))


def generate_ground_truth(
    scenario: HospitalScenario,
    *,
    seed: int,
    scenario_name: str = "normal_day",
) -> SimulationGroundTruth:
    """Pre-sample all hidden values before any policy is instantiated."""

    locations = ("LOBBY", *scenario.departments.keys())
    patients: dict[str, PatientGroundTruth] = {}
    for patient in scenario.patients:
        arrival_rng = _keyed_random(seed, "arrival", patient.patient_id)
        late_probability = 0.30 if scenario_name == "late_arrival" else 0.08
        if arrival_rng.random() < late_probability:
            lateness = arrival_rng.randint(
                35 if scenario_name == "late_arrival" else 20,
                75 if scenario_name == "late_arrival" else 40,
            )
        else:
            lateness = max(-5, min(20, round(arrival_rng.gauss(5, 7))))
        mobility_rng = _keyed_random(seed, "mobility", patient.patient_id)
        age_mobility = 1.0 + max(0, patient.age - 55) * 0.008
        mobility = min(
            1.75,
            max(0.75, mobility_rng.lognormvariate(0, 0.12) * age_mobility),
        )
        adherence_rng = _keyed_random(seed, "adherence", patient.patient_id)
        adherence = adherence_rng.choices(
            (0, 1, 2, 3, 5, 12),
            weights=(12, 18, 20, 20, 15, 15) if scenario_name == "patient_interruption" else (25, 30, 25, 15, 5, 0),
        )[0]
        service: dict[str, int] = {}
        for exam in patient.exams:
            rng = _keyed_random(seed, "service", patient.patient_id, exam.id)
            age_factor = 1.0 + max(0, patient.age - 60) * 0.004
            complexity = 1.0
            if rng.random() < 0.07:
                complexity += rng.uniform(0.25, 0.65)
            service[exam.id] = max(
                2,
                ceil(
                    exam.duration_minutes
                    * age_factor
                    * complexity
                    * rng.lognormvariate(-0.03, 0.16)
                ),
            )
        walks: dict[tuple[str, str], int] = {}
        for origin in locations:
            for destination in locations:
                if origin == destination:
                    continue
                baseline = scenario.travel_times.between(origin, destination)
                rng = _keyed_random(
                    seed,
                    "walk",
                    patient.patient_id,
                    origin,
                    destination,
                )
                noise = rng.lognormvariate(0, 0.08)
                wrong_turn = rng.randint(3, 7) if rng.random() < 0.025 else 0
                walks[(origin, destination)] = max(
                    1,
                    ceil(baseline * mobility * noise + wrong_turn),
                )
        interruptions: tuple[TimeWindow, ...] = ()
        if scenario_name == "patient_interruption":
            interruption_rng = _keyed_random(
                seed,
                "interruption",
                patient.patient_id,
            )
            if interruption_rng.random() < 0.40:
                interruption_start = max(
                    patient.scheduled_arrival + timedelta(minutes=75),
                    scenario.operating_window.start
                    + timedelta(minutes=180 + interruption_rng.randint(0, 210)),
                )
                interruption_end = min(
                    scenario.operating_window.end,
                    interruption_start
                    + timedelta(minutes=interruption_rng.randint(35, 75)),
                )
                if interruption_start < interruption_end:
                    interruptions = (
                        TimeWindow(interruption_start, interruption_end),
                    )
        patients[patient.patient_id] = PatientGroundTruth(
            patient_id=patient.patient_id,
            actual_arrival=patient.scheduled_arrival + timedelta(minutes=lateness),
            true_mobility_factor=round(mobility, 4),
            adherence_delay_minutes=adherence,
            service_minutes_by_exam=MappingProxyType(service),
            walk_minutes_by_edge=MappingProxyType(walks),
            interruption_windows=interruptions,
        )
    downtimes = _resource_downtimes(scenario, seed, scenario_name)
    slowdowns = _service_slowdowns(scenario, scenario_name)
    scenario_hash = scenario_fingerprint(scenario)
    raw = {
        "seed": seed,
        "scenario_fingerprint": scenario_hash,
        "patients": {
            patient_id: {
                "arrival": truth.actual_arrival.isoformat(),
                "mobility": truth.true_mobility_factor,
                "adherence": truth.adherence_delay_minutes,
                "service": dict(truth.service_minutes_by_exam),
                "walk": {
                    f"{origin}>{destination}": minutes
                    for (origin, destination), minutes in truth.walk_minutes_by_edge.items()
                },
                "interruptions": [
                    (window.start.isoformat(), window.end.isoformat())
                    for window in truth.interruption_windows
                ],
            }
            for patient_id, truth in patients.items()
        },
        "downtimes": {
            department_id: {
                str(resource): [
                    (window.start.isoformat(), window.end.isoformat())
                    for window in windows
                ]
                for resource, windows in resources.items()
            }
            for department_id, resources in downtimes.items()
        },
        "service_slowdowns": {
            exam_id: [
                (window.start.isoformat(), window.end.isoformat(), factor)
                for window, factor in windows
            ]
            for exam_id, windows in slowdowns.items()
        },
    }
    trace_hash = sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    result = SimulationGroundTruth(
        seed=seed,
        scenario_fingerprint=scenario_hash,
        trace_fingerprint=trace_hash,
        patients=MappingProxyType(patients),
        resource_downtimes=MappingProxyType(
            {
                department_id: MappingProxyType(dict(resources))
                for department_id, resources in downtimes.items()
            }
        ),
        service_slowdowns=MappingProxyType(slowdowns),
    )
    validate_ground_truth(scenario, result)
    return result


def scenario_fingerprint(scenario: HospitalScenario) -> str:
    observable = {
        "operating_window": (
            scenario.operating_window.start.isoformat(),
            scenario.operating_window.end.isoformat(),
        ),
        "departments": {
            item.id: {
                "capacity": item.capacity,
                "duration": item.estimated_duration_minutes,
                "windows": [
                    (window.start.isoformat(), window.end.isoformat())
                    for window in item.service_windows
                ],
            }
            for item in scenario.departments.values()
        },
        "patients": {
            item.patient_id: {
                "arrival": item.scheduled_arrival.isoformat(),
                "availability": [
                    (window.start.isoformat(), window.end.isoformat())
                    for window in item.availability_windows
                ],
                "exams": [
                    {
                        "id": exam.id,
                        "department": exam.department_id,
                        "duration": exam.duration_minutes,
                        "prerequisites": exam.prerequisites,
                    }
                    for exam in item.exams
                ],
            }
            for item in scenario.patients
        },
    }
    return sha256(
        json.dumps(observable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_ground_truth(
    scenario: HospitalScenario,
    ground_truth: SimulationGroundTruth,
) -> None:
    if ground_truth.scenario_fingerprint != scenario_fingerprint(scenario):
        raise ValueError("Ground Truth 与可观察场景不匹配")
    expected_ids = {patient.patient_id for patient in scenario.patients}
    if set(ground_truth.patients) != expected_ids:
        raise ValueError("Ground Truth 患者集合与场景不一致")
    for patient in scenario.patients:
        truth = ground_truth.patient(patient.patient_id)
        if set(truth.service_minutes_by_exam) != {exam.id for exam in patient.exams}:
            raise ValueError(f"{patient.patient_id} 的真实检查耗时不完整")
        if truth.actual_arrival >= scenario.operating_window.end:
            raise ValueError(f"{patient.patient_id} 的真实到达时间超出运营日")


def _resource_downtimes(
    scenario: HospitalScenario,
    seed: int,
    scenario_name: str = "normal_day",
) -> dict[str, dict[int, tuple[TimeWindow, ...]]]:
    day_start = scenario.operating_window.start
    ct_rng = _keyed_random(seed, "downtime", "CT", "1")
    ct_start = day_start + timedelta(minutes=165 + ct_rng.randint(0, 30))
    ultrasound_rng = _keyed_random(seed, "downtime", "ULTRASOUND", "2")
    ultrasound_start = day_start + timedelta(
        minutes=350 + ultrasound_rng.randint(0, 35)
    )
    downtimes = {
        "CT": {
            1: (TimeWindow(ct_start, ct_start + timedelta(minutes=30)),),
        },
        "ULTRASOUND": {
            2: (
                TimeWindow(
                    ultrasound_start,
                    ultrasound_start + timedelta(minutes=30),
                ),
            ),
        },
    }
    if scenario_name == "device_breakdown":
        breakdown_rng = _keyed_random(seed, "stress-downtime", "ULTRASOUND")
        start = day_start + timedelta(minutes=110 + breakdown_rng.randint(0, 45))
        downtimes["ULTRASOUND"][0] = (
            TimeWindow(start, start + timedelta(minutes=105)),
        )
        ct_start = day_start + timedelta(minutes=255 + breakdown_rng.randint(0, 30))
        downtimes["CT"][0] = (
            TimeWindow(ct_start, ct_start + timedelta(minutes=90)),
        )
    return downtimes


def _service_slowdowns(
    scenario: HospitalScenario,
    scenario_name: str,
) -> dict[str, tuple[tuple[TimeWindow, float], ...]]:
    if scenario_name != "service_slowdown":
        return {}
    start = scenario.operating_window.start + timedelta(minutes=150)
    end = start + timedelta(minutes=210)
    return {
        department_id: ((TimeWindow(start, end), 1.30),)
        for department_id in scenario.departments
    }


def _keyed_random(seed: int, *parts: str) -> Random:
    payload = "\x1f".join((str(seed), *parts)).encode("utf-8")
    value = int.from_bytes(blake2b(payload, digest_size=8).digest(), "big")
    return Random(value)
