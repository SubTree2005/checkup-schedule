"""Domain models shared by the planner and the event-driven scheduler."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import inf
from typing import Mapping


@dataclass(frozen=True, slots=True, order=True)
class TimeWindow:
    """Half-open interval ``[start, end)`` in which an activity may run."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError("时间段起点必须早于终点")

    def contains(self, start: datetime, end: datetime) -> bool:
        return self.start <= start and end <= self.end


@dataclass(frozen=True, slots=True)
class Exam:
    """One examination the patient still needs to complete."""

    id: str
    department_id: str
    duration_minutes: int
    prerequisites: tuple[str, ...] = ()
    earliest_start: datetime | None = None
    latest_finish: datetime | None = None
    delay_cost_per_minute: float = 0.0
    allowed_windows: tuple[TimeWindow, ...] = ()
    is_critical: bool = False


@dataclass(frozen=True, slots=True)
class DepartmentState:
    """Latest queue and availability snapshot for one department."""

    id: str
    observed_at: datetime
    expected_wait_minutes: int = 0
    accepting_patients: bool = True
    available_from: datetime | None = None
    service_windows: tuple[TimeWindow, ...] = ()
    capacity: int = 1
    floor: int | None = None

    @property
    def queue_ready_at(self) -> datetime:
        return self.observed_at + timedelta(minutes=self.expected_wait_minutes)


@dataclass(frozen=True, slots=True)
class TravelTimeMatrix:
    """Walking time between locations; missing edges use ``default_minutes``."""

    minutes_by_edge: Mapping[tuple[str, str], int] = field(default_factory=dict)
    default_minutes: int = 0

    def between(self, origin: str, destination: str) -> int:
        if origin == destination:
            return 0
        direct = self.minutes_by_edge.get((origin, destination))
        if direct is not None:
            return direct
        reverse = self.minutes_by_edge.get((destination, origin))
        if reverse is not None:
            return reverse
        return self.default_minutes


@dataclass(frozen=True, slots=True)
class PatientState:
    """Current execution state for one patient's checkup route."""

    patient_id: str
    exams: tuple[Exam, ...]
    now: datetime
    location_id: str
    completed_exam_ids: frozenset[str] = frozenset()
    in_progress_exam_id: str | None = None
    in_progress_finish_at: datetime | None = None
    previous_order: tuple[str, ...] = ()
    availability_windows: tuple[TimeWindow, ...] = ()
    accumulated_wait_minutes: float = 0.0
    continuous_wait_minutes: float = 0.0
    minutes_since_last_completion: float = 0.0
    age_years: int | None = None
    gender: str | None = None


@dataclass(frozen=True, slots=True)
class PlannerConfig:
    """Weights and explicit MVP size limit for exact search."""

    travel_penalty_per_minute: float = 0.15
    reorder_penalty_per_inversion: float = 4.0
    max_exact_pending_exams: int = 8


@dataclass(frozen=True, slots=True)
class PlanStep:
    exam_id: str
    department_id: str
    travel_minutes: int
    arrival_at: datetime
    wait_minutes: float
    start_at: datetime
    finish_at: datetime
    fixed_in_progress: bool = False


@dataclass(frozen=True, slots=True)
class ScheduleMetrics:
    completion_minutes: float = 0.0
    travel_minutes: int = 0
    wait_minutes: float = 0.0
    medical_delay_cost: float = 0.0
    reorder_inversions: int = 0


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    patient_id: str
    generated_at: datetime
    feasible: bool
    steps: tuple[PlanStep, ...] = ()
    objective_score: float = inf
    metrics: ScheduleMetrics = ScheduleMetrics()
    reasons: tuple[str, ...] = ()

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(step.exam_id for step in self.steps)


@dataclass(frozen=True, slots=True)
class QueueWaitUpdated:
    at: datetime
    department_id: str
    expected_wait_minutes: int


@dataclass(frozen=True, slots=True)
class DepartmentAvailabilityUpdated:
    at: datetime
    department_id: str
    accepting_patients: bool
    available_from: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExamStarted:
    at: datetime
    exam_id: str
    expected_finish_at: datetime


@dataclass(frozen=True, slots=True)
class ExamCompleted:
    at: datetime
    exam_id: str
    location_id: str | None = None


@dataclass(frozen=True, slots=True)
class TimeAdvanced:
    at: datetime
    location_id: str | None = None


SchedulingEvent = (
    QueueWaitUpdated
    | DepartmentAvailabilityUpdated
    | ExamStarted
    | ExamCompleted
    | TimeAdvanced
)


@dataclass(frozen=True, slots=True)
class ReplanOutcome:
    event_name: str
    previous: ScheduleResult
    current: ScheduleResult
    remaining_order_changed: bool
    explanation: str
