"""Scalable rolling-horizon scheduler for many patients and shared resources."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from heapq import heappop, heappush
from typing import Mapping, Sequence

from .activity_prediction import PersonalActivityPrediction
from .critical_path import (
    CriticalTaskState,
    nonlinear_deadline_pressure,
    propagate_effective_deadlines,
)
from .models import (
    DepartmentState,
    Exam,
    PatientState,
    TimeWindow,
    TravelTimeMatrix,
)
from .medical_rules import (
    MedicalEligibilityRule,
    MedicalRuleContext,
    first_medical_rule_rejection,
)


TaskKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class BatchPlannerConfig:
    """Dispatch weights and rolling-plan stability settings."""

    freeze_window_minutes: int = 20
    dispatch_lookahead_minutes: int = 10
    medical_priority_bonus: float = 30.0
    fairness_weight: float = 0.20
    travel_weight: float = 0.10
    previous_order_weight: float = 2.0
    critical_path_enabled: bool = True
    critical_path_buffer_minutes: float = 5.0
    high_completion_risk_slack_minutes: float = 45.0
    minimum_completion_risk_slack_minutes: float = 45.0
    critical_load_saturation_tasks_per_capacity: float = 25.0
    wait_oriented: bool = False
    predicted_wait_weight: float = 1.0
    future_congestion_weight: float = 0.35
    accumulated_wait_weight: float = 0.15
    long_wait_threshold_minutes: float = 60.0
    long_wait_risk_weight: float = 0.02
    stagnation_weight: float = 0.10
    critical_path_soft_weight: float = 4.0
    future_congestion_horizon_minutes: int = 60
    large_exam_duration_threshold_minutes: int = 20
    large_exam_early_weight: float = 0.50
    static_critical_bonus: float = 12.0
    slow_mobility_extra_travel_weight: float = 0.50
    slow_mobility_floor_change_penalty: float = 4.0


@dataclass(frozen=True, slots=True)
class BatchPlanStep:
    patient_id: str
    exam_id: str
    department_id: str
    resource_index: int
    travel_minutes: int
    arrival_at: datetime
    start_at: datetime
    finish_at: datetime
    locked: bool = False
    effective_latest_finish: datetime | None = None
    critical_slack_minutes: float | None = None
    completion_risk: float = 0.0
    terminal_pressure: float = 0.0

    @property
    def task_key(self) -> TaskKey:
        return (self.patient_id, self.exam_id)

    @property
    def wait_minutes(self) -> float:
        return max(0.0, _minutes(self.arrival_at, self.start_at))


@dataclass(frozen=True, slots=True)
class UnscheduledExam:
    patient_id: str
    exam_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class BatchScheduleMetrics:
    patient_count: int = 0
    scheduled_exam_count: int = 0
    unscheduled_exam_count: int = 0
    locked_exam_count: int = 0
    makespan_minutes: float = 0.0
    total_travel_minutes: int = 0
    total_wait_minutes: float = 0.0
    high_completion_risk_task_count: int = 0
    critical_deadline_miss_count: int = 0


@dataclass(frozen=True, slots=True)
class BatchScheduleResult:
    generated_at: datetime
    planning_window: TimeWindow
    feasible: bool
    steps: tuple[BatchPlanStep, ...]
    unscheduled: tuple[UnscheduledExam, ...]
    metrics: BatchScheduleMetrics

    def patient_order(self, patient_id: str) -> tuple[str, ...]:
        return tuple(
            step.exam_id
            for step in sorted(self.steps, key=lambda item: item.start_at)
            if step.patient_id == patient_id
        )


@dataclass(order=True, frozen=True, slots=True)
class _Reservation:
    start: datetime
    end: datetime
    task_key: TaskKey = field(compare=False)


class _ResourceCalendar:
    def __init__(self) -> None:
        self.reservations: list[_Reservation] = []
        self.starts: list[datetime] = []

    def is_free(self, start: datetime, end: datetime) -> bool:
        index = bisect_right(self.starts, start)
        if index > 0 and self.reservations[index - 1].end > start:
            return False
        return index >= len(self.reservations) or self.reservations[index].start >= end

    def add(self, reservation: _Reservation) -> None:
        if not self.is_free(reservation.start, reservation.end):
            raise ValueError("同一科室资源出现重叠预约")
        index = bisect_left(self.starts, reservation.start)
        self.starts.insert(index, reservation.start)
        self.reservations.insert(index, reservation)

    def earliest_slot(
        self,
        windows: tuple[TimeWindow, ...],
        earliest: datetime,
        duration: timedelta,
    ) -> tuple[datetime, datetime] | None:
        for window in windows:
            cursor = max(earliest, window.start)
            if cursor + duration > window.end:
                continue
            while cursor + duration <= window.end:
                index = bisect_right(self.starts, cursor)
                if index > 0 and self.reservations[index - 1].end > cursor:
                    cursor = self.reservations[index - 1].end
                    continue
                if (
                    index < len(self.reservations)
                    and self.reservations[index].start < cursor + duration
                ):
                    cursor = self.reservations[index].end
                    continue
                return cursor, window.end
        return None


@dataclass(slots=True)
class _PatientCursor:
    patient: PatientState
    exams: dict[str, Exam]
    pending: dict[str, Exam]
    done: set[str]
    time: datetime
    location: str
    previous_positions: dict[str, int]
    activity_prediction: PersonalActivityPrediction | None
    critical_states: dict[str, CriticalTaskState] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Proposal:
    patient_id: str
    exam: Exam
    resource_index: int
    travel_minutes: int
    arrival_at: datetime
    start_at: datetime
    finish_at: datetime
    effective_latest_finish: datetime
    critical_slack_minutes: float
    completion_risk: float
    terminal_pressure: float
    predicted_wait_minutes: float
    future_congestion_score: float
    long_wait_risk: float
    dispatch_key: tuple

    @property
    def signature(self) -> tuple:
        return (
            self.exam.id,
            self.resource_index,
            self.start_at,
            self.finish_at,
            self.dispatch_key,
        )


def build_batch_schedule(
    patients: Sequence[PatientState],
    departments: Mapping[str, DepartmentState],
    travel_times: TravelTimeMatrix,
    planning_window: TimeWindow,
    previous_schedule: BatchScheduleResult | None = None,
    config: BatchPlannerConfig = BatchPlannerConfig(),
    activity_predictions: Mapping[str, PersonalActivityPrediction] | None = None,
    medical_rules: Sequence[MedicalEligibilityRule] = (),
) -> BatchScheduleResult:
    """Build a feasible rolling schedule for many patients.

    The algorithm uses a serial schedule-generation scheme with lazy heap
    updates. It is intentionally heuristic: unlike the V1 factorial search it
    scales to hundreds of patients while enforcing all time and capacity rules.
    """

    _validate_inputs(
        patients,
        departments,
        travel_times,
        planning_window,
        config,
        activity_predictions,
    )
    if config.critical_path_enabled:
        pending_task_count = sum(
            exam.id not in patient.completed_exam_ids
            and exam.id != patient.in_progress_exam_id
            for patient in patients
            for exam in patient.exams
        )
        total_capacity = max(1, sum(item.capacity for item in departments.values()))
        tasks_per_capacity = pending_task_count / total_capacity
        load_factor = min(
            1.0,
            tasks_per_capacity / config.critical_load_saturation_tasks_per_capacity,
        )
        config = replace(
            config,
            high_completion_risk_slack_minutes=max(
                config.minimum_completion_risk_slack_minutes,
                config.high_completion_risk_slack_minutes * load_factor,
            ),
        )
    calendars = {
        department_id: [_ResourceCalendar() for _ in range(department.capacity)]
        for department_id, department in departments.items()
    }
    previous_positions = _previous_positions(previous_schedule)
    cursors = _make_cursors(
        patients,
        planning_window,
        previous_positions,
        activity_predictions,
    )
    scheduled: list[BatchPlanStep] = []
    unscheduled: list[UnscheduledExam] = []
    marked_unscheduled: set[TaskKey] = set()
    window_cache: dict[TaskKey, tuple[TimeWindow, ...]] = {}

    _lock_in_progress(
        cursors,
        departments,
        calendars,
        planning_window,
        scheduled,
    )
    if previous_schedule is not None and config.freeze_window_minutes > 0:
        _freeze_near_term_steps(
            previous_schedule,
            cursors,
            departments,
            calendars,
            travel_times,
            planning_window,
            config,
            scheduled,
            window_cache,
            medical_rules,
        )

    _attach_critical_profiles(
        cursors,
        departments,
        travel_times,
        planning_window,
        config,
        window_cache,
    )

    heap: list[tuple[tuple, str, int, _Proposal]] = []
    serial = 0
    for patient_id in sorted(cursors):
        proposal = _best_proposal(
            cursors[patient_id],
            departments,
            calendars,
            travel_times,
            planning_window,
            config,
            window_cache,
            medical_rules,
        )
        if proposal is None:
            _mark_remaining(
                cursors[patient_id],
                departments,
                unscheduled,
                marked_unscheduled,
            )
            continue
        heappush(heap, (proposal.dispatch_key, patient_id, serial, proposal))
        serial += 1

    while heap:
        _, patient_id, _, stale = heappop(heap)
        cursor = cursors[patient_id]
        fresh = _best_proposal(
            cursor,
            departments,
            calendars,
            travel_times,
            planning_window,
            config,
            window_cache,
            medical_rules,
        )
        if fresh is None:
            _mark_remaining(cursor, departments, unscheduled, marked_unscheduled)
            continue
        if fresh.signature != stale.signature:
            heappush(heap, (fresh.dispatch_key, patient_id, serial, fresh))
            serial += 1
            continue

        step = BatchPlanStep(
            patient_id=patient_id,
            exam_id=fresh.exam.id,
            department_id=fresh.exam.department_id,
            resource_index=fresh.resource_index,
            travel_minutes=fresh.travel_minutes,
            arrival_at=fresh.arrival_at,
            start_at=fresh.start_at,
            finish_at=fresh.finish_at,
            effective_latest_finish=fresh.effective_latest_finish,
            critical_slack_minutes=fresh.critical_slack_minutes,
            completion_risk=fresh.completion_risk,
            terminal_pressure=fresh.terminal_pressure,
        )
        calendars[step.department_id][step.resource_index].add(
            _Reservation(step.start_at, step.finish_at, step.task_key)
        )
        scheduled.append(step)
        cursor.pending.pop(step.exam_id)
        cursor.done.add(step.exam_id)
        cursor.time = step.finish_at
        cursor.location = step.department_id

        next_proposal = _best_proposal(
            cursor,
            departments,
            calendars,
            travel_times,
            planning_window,
            config,
            window_cache,
            medical_rules,
        )
        if next_proposal is None:
            _mark_remaining(cursor, departments, unscheduled, marked_unscheduled)
        else:
            heappush(
                heap,
                (next_proposal.dispatch_key, patient_id, serial, next_proposal),
            )
            serial += 1

    scheduled.sort(key=lambda step: (step.start_at, step.department_id, step.patient_id))
    unscheduled.sort(key=lambda item: (item.patient_id, item.exam_id))
    return _result(patients, planning_window, scheduled, unscheduled)


def _make_cursors(
    patients: Sequence[PatientState],
    planning_window: TimeWindow,
    previous_positions: dict[str, dict[str, int]],
    activity_predictions: Mapping[str, PersonalActivityPrediction] | None,
) -> dict[str, _PatientCursor]:
    cursors: dict[str, _PatientCursor] = {}
    for patient in patients:
        exams = {exam.id: exam for exam in patient.exams}
        done = set(patient.completed_exam_ids)
        pending = {
            exam_id: exam
            for exam_id, exam in exams.items()
            if exam_id not in done and exam_id != patient.in_progress_exam_id
        }
        cursors[patient.patient_id] = _PatientCursor(
            patient=patient,
            exams=exams,
            pending=pending,
            done=done,
            time=max(patient.now, planning_window.start),
            location=patient.location_id,
            previous_positions=previous_positions.get(patient.patient_id, {}),
            activity_prediction=(
                activity_predictions.get(patient.patient_id)
                if activity_predictions is not None
                else None
            ),
        )
    return cursors


def _lock_in_progress(
    cursors: Mapping[str, _PatientCursor],
    departments: Mapping[str, DepartmentState],
    calendars: Mapping[str, list[_ResourceCalendar]],
    planning_window: TimeWindow,
    scheduled: list[BatchPlanStep],
) -> None:
    for patient_id in sorted(cursors):
        cursor = cursors[patient_id]
        exam_id = cursor.patient.in_progress_exam_id
        if exam_id is None:
            continue
        finish = cursor.patient.in_progress_finish_at
        assert finish is not None
        if finish <= planning_window.start:
            cursor.done.add(exam_id)
            continue
        exam = cursor.exams[exam_id]
        start = max(cursor.patient.now, planning_window.start)
        resource_index = _first_free_resource(
            calendars[exam.department_id], start, finish
        )
        if resource_index is None:
            raise ValueError(f"进行中检查超过科室容量: {patient_id}/{exam_id}")
        step = BatchPlanStep(
            patient_id=patient_id,
            exam_id=exam_id,
            department_id=exam.department_id,
            resource_index=resource_index,
            travel_minutes=0,
            arrival_at=start,
            start_at=start,
            finish_at=finish,
            locked=True,
        )
        calendars[exam.department_id][resource_index].add(
            _Reservation(start, finish, step.task_key)
        )
        scheduled.append(step)
        cursor.done.add(exam_id)
        cursor.time = finish
        cursor.location = exam.department_id


def _freeze_near_term_steps(
    previous: BatchScheduleResult,
    cursors: Mapping[str, _PatientCursor],
    departments: Mapping[str, DepartmentState],
    calendars: Mapping[str, list[_ResourceCalendar]],
    travel_times: TravelTimeMatrix,
    planning_window: TimeWindow,
    config: BatchPlannerConfig,
    scheduled: list[BatchPlanStep],
    window_cache: dict[TaskKey, tuple[TimeWindow, ...]],
    medical_rules: Sequence[MedicalEligibilityRule],
) -> None:
    freeze_before = planning_window.start + timedelta(
        minutes=config.freeze_window_minutes
    )
    for old in sorted(previous.steps, key=lambda step: step.start_at):
        if not (planning_window.start <= old.start_at < freeze_before):
            continue
        cursor = cursors.get(old.patient_id)
        if cursor is None or old.exam_id not in cursor.pending:
            continue
        exam = cursor.pending[old.exam_id]
        if old.department_id != exam.department_id:
            continue
        if old.finish_at - old.start_at != timedelta(minutes=exam.duration_minutes):
            continue
        if not set(exam.prerequisites).issubset(cursor.done):
            continue
        department = departments[exam.department_id]
        if old.resource_index >= department.capacity:
            continue
        travel = _travel_minutes(cursor, travel_times, exam.department_id)
        arrival = cursor.time + timedelta(minutes=travel)
        windows = _windows_for(
            cursor.patient,
            exam,
            department,
            planning_window,
            window_cache,
        )
        if arrival > old.start_at or not any(
            window.contains(old.start_at, old.finish_at) for window in windows
        ):
            continue
        if not _department_ready(department, old.start_at):
            continue
        if first_medical_rule_rejection(
            medical_rules,
            MedicalRuleContext(
                patient=cursor.patient,
                exam=exam,
                department=department,
                completed_exam_ids=frozenset(cursor.done),
                proposed_start=old.start_at,
                proposed_finish=old.finish_at,
            ),
        ) is not None:
            continue
        calendar = calendars[exam.department_id][old.resource_index]
        if not calendar.is_free(old.start_at, old.finish_at):
            continue
        step = BatchPlanStep(
            patient_id=old.patient_id,
            exam_id=old.exam_id,
            department_id=old.department_id,
            resource_index=old.resource_index,
            travel_minutes=travel,
            arrival_at=arrival,
            start_at=old.start_at,
            finish_at=old.finish_at,
            locked=True,
        )
        calendar.add(_Reservation(step.start_at, step.finish_at, step.task_key))
        scheduled.append(step)
        cursor.pending.pop(step.exam_id)
        cursor.done.add(step.exam_id)
        cursor.time = step.finish_at
        cursor.location = step.department_id


def _best_proposal(
    cursor: _PatientCursor,
    departments: Mapping[str, DepartmentState],
    calendars: Mapping[str, list[_ResourceCalendar]],
    travel_times: TravelTimeMatrix,
    planning_window: TimeWindow,
    config: BatchPlannerConfig,
    window_cache: dict[TaskKey, tuple[TimeWindow, ...]],
    medical_rules: Sequence[MedicalEligibilityRule],
) -> _Proposal | None:
    best: _Proposal | None = None
    for exam in sorted(cursor.pending.values(), key=lambda item: item.id):
        if not set(exam.prerequisites).issubset(cursor.done):
            continue
        department = departments[exam.department_id]
        windows = _windows_for(
            cursor.patient,
            exam,
            department,
            planning_window,
            window_cache,
        )
        if not windows:
            continue
        travel = _travel_minutes(cursor, travel_times, exam.department_id)
        arrival = cursor.time + timedelta(minutes=travel)
        earliest = max(arrival, department.queue_ready_at)
        if department.available_from is not None:
            earliest = max(earliest, department.available_from)
        duration = timedelta(minutes=exam.duration_minutes)

        placement: tuple[datetime, datetime, int] | None = None
        containing_end: datetime | None = None
        for resource_index, calendar in enumerate(calendars[exam.department_id]):
            slot = calendar.earliest_slot(windows, earliest, duration)
            if slot is None:
                continue
            start, window_end = slot
            candidate = (start, start + duration, resource_index)
            if placement is None or candidate < placement:
                placement = candidate
                containing_end = window_end
        if placement is None or containing_end is None:
            continue

        start, finish, resource_index = placement
        if first_medical_rule_rejection(
            medical_rules,
            MedicalRuleContext(
                patient=cursor.patient,
                exam=exam,
                department=department,
                completed_exam_ids=frozenset(cursor.done),
                proposed_start=start,
                proposed_finish=finish,
            ),
        ) is not None:
            continue
        latest_possible_finish = max(window.end for window in windows)
        slack = _minutes(finish, latest_possible_finish)
        critical_state = cursor.critical_states.get(exam.id)
        effective_latest_finish = (
            critical_state.effective_latest_finish
            if critical_state is not None
            else latest_possible_finish
        )
        critical_slack = _minutes(finish, effective_latest_finish)
        completion_risk = (
            nonlinear_deadline_pressure(
                critical_slack,
                high_risk_slack_minutes=config.high_completion_risk_slack_minutes,
            )
            if config.critical_path_enabled
            else 0.0
        )
        terminal_pressure = (
            completion_risk
            * (
                critical_state.terminal_descendant_count
                + int(critical_state.is_terminal_aggregator)
            )
            if critical_state is not None
            else 0.0
        )
        waiting = max(0.0, _minutes(cursor.time, start))
        predicted_wait = max(0.0, _minutes(arrival, start))
        future_congestion = _future_congestion_score(
            calendars[exam.department_id],
            arrival,
            config.future_congestion_horizon_minutes,
        )
        accumulated_wait = (
            cursor.patient.accumulated_wait_minutes
            + cursor.patient.continuous_wait_minutes
        )
        excess_wait = max(
            0.0,
            accumulated_wait - config.long_wait_threshold_minutes,
        )
        long_wait_risk = (
            accumulated_wait * config.accumulated_wait_weight
            + (excess_wait ** 2)
            / config.long_wait_threshold_minutes
            * config.long_wait_risk_weight
            + cursor.patient.minutes_since_last_completion
            * config.stagnation_weight
        )
        previous_rank = cursor.previous_positions.get(
            exam.id,
            len(cursor.previous_positions) if cursor.previous_positions else 0,
        )
        lookahead_seconds = config.dispatch_lookahead_minutes * 60
        bucket = int(
            max(0.0, (start - planning_window.start).total_seconds())
            // lookahead_seconds
        )
        planning_minutes = max(1.0, _minutes(planning_window.start, planning_window.end))
        day_progress = min(
            1.0,
            max(0.0, _minutes(planning_window.start, cursor.time) / planning_minutes),
        )
        large_exam_minutes = max(
            0,
            exam.duration_minutes - config.large_exam_duration_threshold_minutes,
        )
        large_exam_early_bonus = (
            large_exam_minutes
            * config.large_exam_early_weight
            * (1.0 + day_progress)
        )
        static_critical_bonus = (
            config.static_critical_bonus if exam.is_critical else 0.0
        )
        mobility_factor = (
            cursor.activity_prediction.travel_time_factor
            if cursor.activity_prediction is not None
            else 1.0
        )
        slow_mobility = max(0.0, mobility_factor - 1.0)
        origin_floor = (
            departments[cursor.location].floor
            if cursor.location in departments
            else None
        )
        floor_change = (
            abs(origin_floor - department.floor)
            if origin_floor is not None and department.floor is not None
            else 0
        )
        mobility_burden = slow_mobility * (
            travel * config.slow_mobility_extra_travel_weight
            + floor_change * config.slow_mobility_floor_change_penalty
        )
        priority_score = (
            slack
            - exam.delay_cost_per_minute * config.medical_priority_bonus
            - waiting * config.fairness_weight
            + travel * config.travel_weight
            + previous_rank * config.previous_order_weight
            + mobility_burden
            - large_exam_early_bonus
            - static_critical_bonus
        )
        if config.wait_oriented:
            wait_priority_score = (
                predicted_wait * config.predicted_wait_weight
                - future_congestion * config.future_congestion_weight
                - long_wait_risk
                + travel * config.travel_weight
                + previous_rank * config.previous_order_weight
                + mobility_burden
                - exam.delay_cost_per_minute * config.medical_priority_bonus
                - completion_risk * config.critical_path_soft_weight
                - large_exam_early_bonus
                - static_critical_bonus
            )
            key = (
                round(wait_priority_score, 6),
                bucket,
                start,
                finish,
                cursor.patient.patient_id,
                exam.id,
            )
        else:
            completion_tier = int(
                not config.critical_path_enabled
                or critical_slack > config.high_completion_risk_slack_minutes
            )
            key = (
                completion_tier,
                (
                    round(critical_slack, 6)
                    if completion_tier == 0
                    else bucket
                ),
                bucket,
                round(priority_score, 6),
                start,
                finish,
                cursor.patient.patient_id,
                exam.id,
            )
        proposal = _Proposal(
            patient_id=cursor.patient.patient_id,
            exam=exam,
            resource_index=resource_index,
            travel_minutes=travel,
            arrival_at=arrival,
            start_at=start,
            finish_at=finish,
            effective_latest_finish=effective_latest_finish,
            critical_slack_minutes=critical_slack,
            completion_risk=completion_risk,
            terminal_pressure=terminal_pressure,
            predicted_wait_minutes=predicted_wait,
            future_congestion_score=future_congestion,
            long_wait_risk=long_wait_risk,
            dispatch_key=key,
        )
        if best is None or proposal.dispatch_key < best.dispatch_key:
            best = proposal
    return best


def _future_congestion_score(
    calendars: Sequence[_ResourceCalendar],
    arrival: datetime,
    horizon_minutes: int,
) -> float:
    """Return reserved capacity-minutes per resource after estimated arrival."""

    horizon_end = arrival + timedelta(minutes=horizon_minutes)
    reserved_minutes = 0.0
    for calendar in calendars:
        for reservation in calendar.reservations:
            overlap_start = max(arrival, reservation.start)
            overlap_end = min(horizon_end, reservation.end)
            if overlap_start < overlap_end:
                reserved_minutes += _minutes(overlap_start, overlap_end)
    return reserved_minutes / max(1, len(calendars))


def _attach_critical_profiles(
    cursors: Mapping[str, _PatientCursor],
    departments: Mapping[str, DepartmentState],
    travel_times: TravelTimeMatrix,
    planning_window: TimeWindow,
    config: BatchPlannerConfig,
    window_cache: dict[TaskKey, tuple[TimeWindow, ...]],
) -> None:
    """Compute one immutable deadline profile per patient and replan."""

    for cursor in cursors.values():
        own_deadlines: dict[str, datetime] = {}
        for exam in cursor.exams.values():
            windows = _windows_for(
                cursor.patient,
                exam,
                departments[exam.department_id],
                planning_window,
                window_cache,
            )
            own_deadlines[exam.id] = (
                max(window.end for window in windows)
                if windows
                else planning_window.start
            )
        if config.critical_path_enabled:
            cursor.critical_states = propagate_effective_deadlines(
                tuple(cursor.exams.values()),
                own_deadlines,
                travel_times,
                required_buffer_minutes=config.critical_path_buffer_minutes,
            )
        else:
            cursor.critical_states = {
                exam_id: CriticalTaskState(
                    exam_id=exam_id,
                    effective_latest_finish=deadline,
                    own_latest_finish=deadline,
                    successor_ids=(),
                    downstream_task_count=0,
                    terminal_descendant_count=0,
                    is_terminal_aggregator=False,
                )
                for exam_id, deadline in own_deadlines.items()
            }


def _windows_for(
    patient: PatientState,
    exam: Exam,
    department: DepartmentState,
    planning_window: TimeWindow,
    cache: dict[TaskKey, tuple[TimeWindow, ...]],
) -> tuple[TimeWindow, ...]:
    key = (patient.patient_id, exam.id)
    if key in cache:
        return cache[key]
    if not department.accepting_patients and department.available_from is None:
        cache[key] = ()
        return ()

    groups = (
        patient.availability_windows,
        department.service_windows,
        exam.allowed_windows,
    )
    windows: tuple[TimeWindow, ...] = (planning_window,)
    for group in groups:
        if group:
            windows = _intersect(windows, _normalize(group))
            if not windows:
                break

    lower = planning_window.start
    upper = planning_window.end
    if exam.earliest_start is not None:
        lower = max(lower, exam.earliest_start)
    if exam.latest_finish is not None:
        upper = min(upper, exam.latest_finish)
    if department.available_from is not None:
        lower = max(lower, department.available_from)
    if lower >= upper:
        windows = ()
    elif windows:
        windows = _intersect(windows, (TimeWindow(lower, upper),))
    cache[key] = windows
    return windows


def _normalize(windows: Sequence[TimeWindow]) -> tuple[TimeWindow, ...]:
    merged: list[TimeWindow] = []
    for window in sorted(windows):
        if not merged or merged[-1].end < window.start:
            merged.append(window)
        else:
            merged[-1] = TimeWindow(
                merged[-1].start,
                max(merged[-1].end, window.end),
            )
    return tuple(merged)


def _intersect(
    left: Sequence[TimeWindow],
    right: Sequence[TimeWindow],
) -> tuple[TimeWindow, ...]:
    result: list[TimeWindow] = []
    left_index = right_index = 0
    while left_index < len(left) and right_index < len(right):
        start = max(left[left_index].start, right[right_index].start)
        end = min(left[left_index].end, right[right_index].end)
        if start < end:
            result.append(TimeWindow(start, end))
        if left[left_index].end <= right[right_index].end:
            left_index += 1
        else:
            right_index += 1
    return tuple(result)


def _department_ready(department: DepartmentState, at: datetime) -> bool:
    if not department.accepting_patients and department.available_from is None:
        return False
    if department.available_from is not None and at < department.available_from:
        return False
    return at >= department.queue_ready_at


def _first_free_resource(
    calendars: Sequence[_ResourceCalendar],
    start: datetime,
    end: datetime,
) -> int | None:
    for index, calendar in enumerate(calendars):
        if calendar.is_free(start, end):
            return index
    return None


def _previous_positions(
    previous: BatchScheduleResult | None,
) -> dict[str, dict[str, int]]:
    if previous is None:
        return {}
    result: dict[str, dict[str, int]] = {}
    by_patient: dict[str, list[BatchPlanStep]] = {}
    for step in previous.steps:
        by_patient.setdefault(step.patient_id, []).append(step)
    for patient_id, steps in by_patient.items():
        ordered = sorted(steps, key=lambda step: step.start_at)
        result[patient_id] = {
            step.exam_id: index for index, step in enumerate(ordered)
        }
    return result


def _mark_remaining(
    cursor: _PatientCursor,
    departments: Mapping[str, DepartmentState],
    output: list[UnscheduledExam],
    marked: set[TaskKey],
) -> None:
    for exam in sorted(cursor.pending.values(), key=lambda item: item.id):
        key = (cursor.patient.patient_id, exam.id)
        if key in marked:
            continue
        department = departments[exam.department_id]
        if not department.accepting_patients and department.available_from is None:
            reason = "科室在规划时段内不接诊"
        elif not set(exam.prerequisites).issubset(cursor.done):
            reason = "前置检查未排入计划或前置关系存在闭环"
        else:
            reason = "患者时间、科室时间或共享容量内无可行时段"
        output.append(UnscheduledExam(key[0], key[1], reason))
        marked.add(key)


def _result(
    patients: Sequence[PatientState],
    planning_window: TimeWindow,
    scheduled: Sequence[BatchPlanStep],
    unscheduled: Sequence[UnscheduledExam],
) -> BatchScheduleResult:
    makespan = (
        max(_minutes(planning_window.start, step.finish_at) for step in scheduled)
        if scheduled
        else 0.0
    )
    metrics = BatchScheduleMetrics(
        patient_count=len(patients),
        scheduled_exam_count=len(scheduled),
        unscheduled_exam_count=len(unscheduled),
        locked_exam_count=sum(step.locked for step in scheduled),
        makespan_minutes=makespan,
        total_travel_minutes=sum(step.travel_minutes for step in scheduled),
        total_wait_minutes=sum(step.wait_minutes for step in scheduled),
        high_completion_risk_task_count=sum(
            step.completion_risk >= 0.5 for step in scheduled
        ),
        critical_deadline_miss_count=sum(
            step.critical_slack_minutes is not None
            and step.critical_slack_minutes < 0
            for step in scheduled
        ),
    )
    return BatchScheduleResult(
        generated_at=planning_window.start,
        planning_window=planning_window,
        feasible=not unscheduled,
        steps=tuple(scheduled),
        unscheduled=tuple(unscheduled),
        metrics=metrics,
    )


def _validate_inputs(
    patients: Sequence[PatientState],
    departments: Mapping[str, DepartmentState],
    travel_times: TravelTimeMatrix,
    planning_window: TimeWindow,
    config: BatchPlannerConfig,
    activity_predictions: Mapping[str, PersonalActivityPrediction] | None,
) -> None:
    if config.freeze_window_minutes < 0:
        raise ValueError("冻结窗口不能为负数")
    if config.dispatch_lookahead_minutes <= 0:
        raise ValueError("调度观察窗口必须为正数")
    if config.critical_path_buffer_minutes < 0:
        raise ValueError("关键路径安全缓冲不能为负数")
    if config.high_completion_risk_slack_minutes <= 0:
        raise ValueError("高完成风险余量阈值必须为正数")
    if config.minimum_completion_risk_slack_minutes <= 0:
        raise ValueError("最低完成风险余量阈值必须为正数")
    if config.critical_load_saturation_tasks_per_capacity <= 0:
        raise ValueError("关键路径负载饱和阈值必须为正数")
    nonnegative_weights = (
        config.predicted_wait_weight,
        config.future_congestion_weight,
        config.accumulated_wait_weight,
        config.long_wait_risk_weight,
        config.stagnation_weight,
        config.critical_path_soft_weight,
        config.large_exam_early_weight,
        config.static_critical_bonus,
        config.slow_mobility_extra_travel_weight,
        config.slow_mobility_floor_change_penalty,
    )
    if any(value < 0 for value in nonnegative_weights):
        raise ValueError("V10 等待目标权重不能为负数")
    if config.long_wait_threshold_minutes <= 0:
        raise ValueError("长等待阈值必须为正数")
    if config.future_congestion_horizon_minutes <= 0:
        raise ValueError("未来拥堵观察窗口必须为正数")
    if config.large_exam_duration_threshold_minutes <= 0:
        raise ValueError("大项目时长阈值必须为正整数")
    if travel_times.default_minutes < 0 or any(
        value < 0 for value in travel_times.minutes_by_edge.values()
    ):
        raise ValueError("步行时间不能为负数")
    patient_ids: set[str] = set()
    for patient in patients:
        if patient.patient_id in patient_ids:
            raise ValueError(f"患者 id 重复: {patient.patient_id}")
        patient_ids.add(patient.patient_id)
        if (patient.age_years is None) != (patient.gender is None):
            raise ValueError("患者年龄和性别必须同时提供")
        if patient.age_years is not None and (
            isinstance(patient.age_years, bool)
            or not isinstance(patient.age_years, int)
            or patient.age_years < 0
        ):
            raise ValueError("患者年龄必须是非负整数")
        if any(
            value < 0
            for value in (
                patient.accumulated_wait_minutes,
                patient.continuous_wait_minutes,
                patient.minutes_since_last_completion,
            )
        ):
            raise ValueError("患者等待与最近完成时长不能为负数")
        exam_ids: set[str] = set()
        for exam in patient.exams:
            if exam.id in exam_ids:
                raise ValueError(f"患者 {patient.patient_id} 的检查 id 重复: {exam.id}")
            exam_ids.add(exam.id)
            if not isinstance(exam.is_critical, bool):
                raise ValueError("静态关键项目标记必须是布尔值")
            if exam.duration_minutes <= 0:
                raise ValueError(f"检查耗时必须为正数: {patient.patient_id}/{exam.id}")
            if exam.delay_cost_per_minute < 0:
                raise ValueError(
                    f"医疗延迟代价不能为负数: {patient.patient_id}/{exam.id}"
                )
            if exam.department_id not in departments:
                raise ValueError(f"缺少科室状态: {exam.department_id}")
        for exam in patient.exams:
            unknown = set(exam.prerequisites) - exam_ids
            if unknown:
                raise ValueError(
                    f"{patient.patient_id}/{exam.id} 的前置检查不存在: {sorted(unknown)}"
                )
        propagate_effective_deadlines(
            patient.exams,
            {exam.id: planning_window.end for exam in patient.exams},
            travel_times,
            required_buffer_minutes=config.critical_path_buffer_minutes,
        )
        if not set(patient.completed_exam_ids).issubset(exam_ids):
            raise ValueError(f"患者 {patient.patient_id} 存在未知的已完成检查")
        if patient.in_progress_exam_id is not None:
            if patient.in_progress_exam_id not in exam_ids:
                raise ValueError(f"患者 {patient.patient_id} 的进行中检查不存在")
            if patient.in_progress_finish_at is None:
                raise ValueError("进行中检查必须提供预计结束时间")
            if patient.in_progress_exam_id in patient.completed_exam_ids:
                raise ValueError("同一检查不能同时处于进行中和已完成状态")
            if patient.in_progress_finish_at < patient.now:
                raise ValueError("进行中检查的预计结束时间不能早于当前时间")
    if activity_predictions is not None:
        for patient_id, prediction in activity_predictions.items():
            if prediction.patient_id != patient_id:
                raise ValueError("个人活动预测映射键与患者编号不一致")
    for department_id, department in departments.items():
        if department.id != department_id:
            raise ValueError(f"科室映射键与状态 id 不一致: {department_id}")
        if department.capacity <= 0:
            raise ValueError(f"科室容量必须为正数: {department_id}")
        if department.expected_wait_minutes < 0:
            raise ValueError(f"预计等待时间不能为负数: {department_id}")
        if department.floor is not None and (
            isinstance(department.floor, bool)
            or not isinstance(department.floor, int)
        ):
            raise ValueError(f"科室楼层必须是整数: {department_id}")


def _travel_minutes(
    cursor: _PatientCursor,
    travel_times: TravelTimeMatrix,
    destination_id: str,
) -> int:
    baseline = travel_times.between(cursor.location, destination_id)
    if cursor.activity_prediction is None:
        return baseline
    return cursor.activity_prediction.estimate_minutes(baseline)


def _minutes(start: datetime, finish: datetime) -> float:
    return (finish - start).total_seconds() / 60
