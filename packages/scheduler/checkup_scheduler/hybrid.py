"""Heuristic-first scheduling with an optional CP-SAT neighborhood optimizer."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import ceil, floor
from time import perf_counter
from typing import Mapping, Sequence

from .activity_prediction import (
    PersonalActivityPrediction,
    personalized_travel_minutes,
)
from .batch import (
    BatchPlanStep,
    BatchPlannerConfig,
    BatchScheduleResult,
    _result,
    _windows_for,
    build_batch_schedule,
)
from .critical_path import nonlinear_deadline_pressure
from .medical_rules import MedicalEligibilityRule
from .models import DepartmentState, PatientState, TimeWindow, TravelTimeMatrix
from .wait_prediction import WaitPrediction, apply_wait_predictions


@dataclass(frozen=True, slots=True)
class HybridPlannerConfig:
    strategy: str = "auto"
    cp_sat_time_limit_seconds: float = 2.0
    cp_sat_num_workers: int = 4
    cp_sat_max_neighborhood_tasks: int = 120
    cp_sat_stability_weight: int = 5
    use_p90_wait: bool = True
    wait_safety_buffer_minutes: float = 0.0
    max_prediction_age_minutes: float = 15.0


@dataclass(frozen=True, slots=True)
class HybridScheduleResult:
    schedule: BatchScheduleResult
    backend: str
    status: str
    improved: bool
    fallback_reason: str | None = None
    optimized_task_count: int = 0
    cp_sat_invoked: bool = False
    solve_seconds: float = 0.0
    objective_improvement: float = 0.0
    completion_risk_improvement: float = 0.0


def cp_sat_available() -> bool:
    try:
        from ortools.sat.python import cp_model  # noqa: F401
    except ImportError:
        return False
    return True


def build_hybrid_schedule(
    patients: Sequence[PatientState],
    departments: Mapping[str, DepartmentState],
    travel_times: TravelTimeMatrix,
    planning_window: TimeWindow,
    *,
    optimization_horizon_end: datetime | None = None,
    wait_predictions: Mapping[str, WaitPrediction] | None = None,
    activity_predictions: Mapping[str, PersonalActivityPrediction] | None = None,
    previous_schedule: BatchScheduleResult | None = None,
    batch_config: BatchPlannerConfig = BatchPlannerConfig(),
    hybrid_config: HybridPlannerConfig = HybridPlannerConfig(),
    medical_rules: Sequence[MedicalEligibilityRule] = (),
) -> HybridScheduleResult:
    """Create a safe heuristic schedule, then improve its near-term neighborhood."""

    _validate_hybrid_config(hybrid_config)
    predicted_departments = apply_wait_predictions(
        departments,
        wait_predictions,
        use_p90=hybrid_config.use_p90_wait,
        safety_buffer_minutes=hybrid_config.wait_safety_buffer_minutes,
        now=planning_window.start,
        max_age_minutes=hybrid_config.max_prediction_age_minutes,
    )
    heuristic = build_batch_schedule(
        patients,
        predicted_departments,
        travel_times,
        planning_window,
        previous_schedule=previous_schedule,
        config=batch_config,
        activity_predictions=activity_predictions,
        medical_rules=medical_rules,
    )
    if hybrid_config.strategy == "heuristic":
        return HybridScheduleResult(
            schedule=heuristic,
            backend="heuristic",
            status="HEURISTIC_ONLY",
            improved=False,
        )
    if medical_rules:
        return HybridScheduleResult(
            schedule=heuristic,
            backend="heuristic",
            status="HEURISTIC_MEDICAL_RULES",
            improved=False,
            fallback_reason=(
                "自定义医学规则未翻译为 CP-SAT 约束，保留启发式可行解"
            ),
        )
    if not cp_sat_available():
        return HybridScheduleResult(
            schedule=heuristic,
            backend="heuristic",
            status="FALLBACK",
            improved=False,
            fallback_reason="OR-Tools 未安装，已使用启发式安全降级",
            cp_sat_invoked=True,
        )

    horizon_end = min(
        optimization_horizon_end or planning_window.end,
        planning_window.end,
    )
    solve_started = perf_counter()
    try:
        optimized, status, task_count = _optimize_with_cp_sat(
            heuristic,
            patients,
            predicted_departments,
            travel_times,
            activity_predictions,
            horizon_end,
            hybrid_config,
        )
    except Exception as error:
        solve_seconds = perf_counter() - solve_started
        return HybridScheduleResult(
            schedule=heuristic,
            backend="heuristic",
            status="FALLBACK",
            improved=False,
            fallback_reason=f"CP-SAT 建模或求解失败: {type(error).__name__}",
            cp_sat_invoked=True,
            solve_seconds=solve_seconds,
        )
    solve_seconds = perf_counter() - solve_started
    if optimized is None:
        return HybridScheduleResult(
            schedule=heuristic,
            backend="heuristic",
            status=status,
            improved=False,
            fallback_reason="CP-SAT 未在时限内给出更优可行解",
            optimized_task_count=task_count,
            cp_sat_invoked=True,
            solve_seconds=solve_seconds,
        )
    before = _quality(heuristic)
    after = _quality(optimized)
    return HybridScheduleResult(
        schedule=optimized,
        backend="cp_sat",
        status=status,
        improved=True,
        optimized_task_count=task_count,
        cp_sat_invoked=True,
        solve_seconds=solve_seconds,
        objective_improvement=max(0.0, before[2] - after[2]),
        completion_risk_improvement=max(0.0, before[1] - after[1]),
    )


def _optimize_with_cp_sat(
    heuristic: BatchScheduleResult,
    patients: Sequence[PatientState],
    departments: Mapping[str, DepartmentState],
    travel_times: TravelTimeMatrix,
    activity_predictions: Mapping[str, PersonalActivityPrediction] | None,
    optimization_horizon_end: datetime,
    config: HybridPlannerConfig,
) -> tuple[BatchScheduleResult | None, str, int]:
    from ortools.sat.python import cp_model

    origin = heuristic.planning_window.start
    horizon_minutes = max(1, _minute_floor(origin, heuristic.planning_window.end))
    patients_by_id = {patient.patient_id: patient for patient in patients}
    exams = {
        (patient.patient_id, exam.id): exam
        for patient in patients
        for exam in patient.exams
    }
    ordered_steps = sorted(
        heuristic.steps,
        key=lambda step: (step.patient_id, step.start_at, step.exam_id),
    )
    eligible = [
        step
        for step in ordered_steps
        if not step.locked and step.start_at < optimization_horizon_end
    ]
    department_pressure: dict[str, int] = {}
    for step in eligible:
        department_pressure[step.department_id] = (
            department_pressure.get(step.department_id, 0) + 1
        )
    eligible.sort(
        key=lambda step: (
            step.critical_slack_minutes if step.critical_slack_minutes is not None else float("inf"),
            -department_pressure[step.department_id],
            step.start_at,
            -exams[step.task_key].delay_cost_per_minute,
            step.patient_id,
            step.exam_id,
        )
    )
    selected = {
        step.task_key
        for step in eligible[: config.cp_sat_max_neighborhood_tasks]
    }
    if not selected:
        return None, "NO_CP_SAT_NEIGHBORHOOD", 0

    model = cp_model.CpModel()
    starts: dict[tuple[str, str], object | int] = {}
    ends: dict[tuple[str, str], object | int] = {}
    selected_options: dict[tuple[str, str], list[tuple[object, int]]] = {}
    resource_intervals: dict[tuple[str, int], list[object]] = {
        (department_id, resource): []
        for department_id, department in departments.items()
        for resource in range(department.capacity)
    }
    deviations: list[object] = []
    critical_lateness: list[object] = []
    critical_violations: list[object] = []
    window_cache: dict[tuple[str, str], tuple[TimeWindow, ...]] = {}

    for step in ordered_steps:
        key = step.task_key
        duration = max(1, int(round((step.finish_at - step.start_at).total_seconds() / 60)))
        original_start = _minute_round(origin, step.start_at)
        original_end = original_start + duration
        if key not in selected:
            starts[key] = original_start
            ends[key] = original_end
            if step.start_at < optimization_horizon_end and step.finish_at > origin:
                fixed = model.new_fixed_size_interval_var(
                    original_start,
                    duration,
                    f"fixed_{step.patient_id}_{step.exam_id}",
                )
                resource_intervals[(step.department_id, step.resource_index)].append(fixed)
            continue

        patient = patients_by_id[step.patient_id]
        exam = exams[key]
        department = departments[step.department_id]
        main_start = model.new_int_var(0, horizon_minutes, f"start_{step.patient_id}_{step.exam_id}")
        main_end = model.new_int_var(0, horizon_minutes, f"end_{step.patient_id}_{step.exam_id}")
        starts[key] = main_start
        ends[key] = main_end
        presences: list[object] = []
        options: list[tuple[object, int]] = []
        windows = _windows_for(
            patient,
            exam,
            department,
            heuristic.planning_window,
            window_cache,
        )
        for resource_index in range(department.capacity):
            for window_index, window in enumerate(windows):
                clipped_end = min(window.end, optimization_horizon_end)
                lower_time = max(window.start, department.queue_ready_at)
                lower = _minute_ceil(origin, lower_time)
                upper = _minute_floor(origin, clipped_end) - duration
                if lower > upper:
                    continue
                presence = model.new_bool_var(
                    f"use_{step.patient_id}_{step.exam_id}_{resource_index}_{window_index}"
                )
                option_start = model.new_int_var(
                    lower,
                    upper,
                    f"opt_start_{step.patient_id}_{step.exam_id}_{resource_index}_{window_index}",
                )
                option_end = model.new_int_var(
                    lower + duration,
                    upper + duration,
                    f"opt_end_{step.patient_id}_{step.exam_id}_{resource_index}_{window_index}",
                )
                interval = model.new_optional_interval_var(
                    option_start,
                    duration,
                    option_end,
                    presence,
                    f"interval_{step.patient_id}_{step.exam_id}_{resource_index}_{window_index}",
                )
                model.add(main_start == option_start).only_enforce_if(presence)
                model.add(main_end == option_end).only_enforce_if(presence)
                resource_intervals[(step.department_id, resource_index)].append(interval)
                presences.append(presence)
                options.append((presence, resource_index))
        if not presences:
            return None, "CP_SAT_EMPTY_DOMAIN", len(selected)
        model.add_exactly_one(presences)
        selected_options[key] = options
        deviation = model.new_int_var(0, horizon_minutes, f"change_{step.patient_id}_{step.exam_id}")
        model.add_abs_equality(deviation, main_start - original_start)
        deviations.append(deviation)
        if step.effective_latest_finish is not None:
            critical_deadline = _minute_floor(
                origin,
                step.effective_latest_finish,
            )
            lateness = model.new_int_var(
                0,
                horizon_minutes * 2,
                f"critical_lateness_{step.patient_id}_{step.exam_id}",
            )
            model.add_max_equality(lateness, (0, main_end - critical_deadline))
            violated = model.new_bool_var(
                f"critical_violation_{step.patient_id}_{step.exam_id}"
            )
            model.add(main_end > critical_deadline).only_enforce_if(violated)
            model.add(main_end <= critical_deadline).only_enforce_if(violated.Not())
            critical_lateness.append(lateness)
            critical_violations.append(violated)
        model.add_hint(main_start, original_start)
        model.add_hint(main_end, original_end)

    for intervals in resource_intervals.values():
        if len(intervals) > 1:
            model.add_no_overlap(intervals)

    by_patient: dict[str, list[BatchPlanStep]] = {}
    for step in ordered_steps:
        by_patient.setdefault(step.patient_id, []).append(step)
    for patient_steps in by_patient.values():
        patient_steps.sort(key=lambda step: step.start_at)
        first = patient_steps[0]
        first_patient = patients_by_id[first.patient_id]
        first_ready = max(first_patient.now, heuristic.planning_window.start)
        first_travel = personalized_travel_minutes(
            travel_times.between(
                first_patient.location_id,
                first.department_id,
            ),
            first.patient_id,
            activity_predictions,
        )
        model.add(
            starts[first.task_key]
            >= _minute_ceil(origin, first_ready) + first_travel
        )
        for previous, current in zip(patient_steps, patient_steps[1:]):
            travel = personalized_travel_minutes(
                travel_times.between(
                    previous.department_id,
                    current.department_id,
                ),
                current.patient_id,
                activity_predictions,
            )
            model.add(starts[current.task_key] >= ends[previous.task_key] + travel)

    # Keep prerequisite semantics explicit even when the heuristic's current
    # route order already implies them.  This protects future neighborhood
    # selection changes from silently relaxing the DAG.
    for patient in patients:
        for exam in patient.exams:
            current_key = (patient.patient_id, exam.id)
            if current_key not in starts:
                continue
            for prerequisite_id in exam.prerequisites:
                prerequisite_key = (patient.patient_id, prerequisite_id)
                if prerequisite_key not in ends:
                    continue
                prerequisite = exams[prerequisite_key]
                travel = personalized_travel_minutes(
                    travel_times.between(
                        prerequisite.department_id,
                        exam.department_id,
                    ),
                    patient.patient_id,
                    activity_predictions,
                )
                model.add(starts[current_key] >= ends[prerequisite_key] + travel)

    completion_terms = [ends[steps[-1].task_key] for steps in by_patient.values() if steps]
    model.minimize(
        sum(critical_violations) * 100_000_000
        + sum(critical_lateness) * 1_000_000
        + sum(completion_terms) * 100
        + sum(deviations) * config.cp_sat_stability_weight
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = config.cp_sat_time_limit_seconds
    solver.parameters.num_search_workers = config.cp_sat_num_workers
    status_code = solver.solve(model)
    status = solver.status_name(status_code)
    if status_code not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        return None, status, len(selected)

    solved_steps: list[BatchPlanStep] = []
    solved_by_key: dict[tuple[str, str], tuple[datetime, datetime, int]] = {}
    for step in ordered_steps:
        key = step.task_key
        if key in selected:
            start = origin + timedelta(minutes=solver.value(starts[key]))
            end = origin + timedelta(minutes=solver.value(ends[key]))
            resource = next(
                resource_index
                for presence, resource_index in selected_options[key]
                if solver.boolean_value(presence)
            )
        else:
            start, end, resource = step.start_at, step.finish_at, step.resource_index
        solved_by_key[key] = (start, end, resource)

    for patient_id, patient_steps in by_patient.items():
        patient = patients_by_id[patient_id]
        previous_finish = max(patient.now, heuristic.planning_window.start)
        previous_location = patient.location_id
        for old in patient_steps:
            start, end, resource = solved_by_key[old.task_key]
            travel = personalized_travel_minutes(
                travel_times.between(previous_location, old.department_id),
                patient_id,
                activity_predictions,
            )
            arrival = previous_finish + timedelta(minutes=travel)
            solved_steps.append(
                replace(
                    old,
                    resource_index=resource,
                    travel_minutes=travel,
                    arrival_at=arrival,
                    start_at=start,
                    finish_at=end,
                    critical_slack_minutes=(
                        (old.effective_latest_finish - end).total_seconds() / 60
                        if old.effective_latest_finish is not None
                        else None
                    ),
                    completion_risk=(
                        nonlinear_deadline_pressure(
                            (old.effective_latest_finish - end).total_seconds() / 60
                        )
                        if old.effective_latest_finish is not None
                        else 0.0
                    ),
                )
            )
            previous_finish = end
            previous_location = old.department_id

    solved_steps.sort(key=lambda step: (step.start_at, step.department_id, step.patient_id))
    candidate = _result(
        patients,
        heuristic.planning_window,
        solved_steps,
        heuristic.unscheduled,
    )
    if _quality(candidate) >= _quality(heuristic):
        return None, f"{status}_NO_IMPROVEMENT", len(selected)
    return candidate, status, len(selected)


def _quality(schedule: BatchScheduleResult) -> tuple[float, float, float, float, float]:
    last_finish: dict[str, datetime] = {}
    for step in schedule.steps:
        last_finish[step.patient_id] = max(
            last_finish.get(step.patient_id, step.finish_at),
            step.finish_at,
        )
    completion_sum = sum(
        (finish - schedule.planning_window.start).total_seconds() / 60
        for finish in last_finish.values()
    )
    return (
        float(
            sum(
                step.critical_slack_minutes is not None
                and step.critical_slack_minutes < 0
                for step in schedule.steps
            )
        ),
        sum(step.completion_risk for step in schedule.steps),
        completion_sum,
        schedule.metrics.total_wait_minutes,
        schedule.metrics.makespan_minutes,
    )


def _validate_hybrid_config(config: HybridPlannerConfig) -> None:
    if config.strategy not in {"auto", "heuristic", "cp_sat"}:
        raise ValueError("strategy 必须是 auto、heuristic 或 cp_sat")
    if config.cp_sat_time_limit_seconds <= 0:
        raise ValueError("CP-SAT 求解时限必须为正数")
    if config.cp_sat_num_workers <= 0 or config.cp_sat_max_neighborhood_tasks <= 0:
        raise ValueError("CP-SAT 工作线程数和邻域大小必须为正数")


def _minute_ceil(origin: datetime, value: datetime) -> int:
    return int(ceil((value - origin).total_seconds() / 60))


def _minute_floor(origin: datetime, value: datetime) -> int:
    return int(floor((value - origin).total_seconds() / 60))


def _minute_round(origin: datetime, value: datetime) -> int:
    return int(round((value - origin).total_seconds() / 60))
