"""Exact constraint-aware planner for the 4–5 examination MVP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping

from .activity_prediction import PersonalActivityPrediction
from .models import (
    DepartmentState,
    Exam,
    PatientState,
    PlannerConfig,
    PlanStep,
    ScheduleMetrics,
    ScheduleResult,
    TravelTimeMatrix,
)


@dataclass(frozen=True, slots=True)
class _Candidate:
    steps: tuple[PlanStep, ...]
    finish_at: datetime
    travel_minutes: int
    wait_minutes: float
    medical_delay_cost: float


def build_schedule(
    patient: PatientState,
    departments: Mapping[str, DepartmentState],
    travel_times: TravelTimeMatrix,
    config: PlannerConfig = PlannerConfig(),
    activity_prediction: PersonalActivityPrediction | None = None,
) -> ScheduleResult:
    """Return the lowest-cost feasible order for the patient's remaining exams.

    The search is exhaustive within the configured MVP limit, so the returned route
    is optimal for the stated objective rather than merely a greedy suggestion.
    """

    exams_by_id = _validate(patient, departments, travel_times, config)
    if (
        activity_prediction is not None
        and activity_prediction.patient_id != patient.patient_id
    ):
        raise ValueError("个人活动预测与当前患者不一致")
    completed = set(patient.completed_exam_ids)
    fixed_steps: tuple[PlanStep, ...] = ()
    search_time = patient.now
    search_location = patient.location_id

    if patient.in_progress_exam_id is not None:
        exam = exams_by_id[patient.in_progress_exam_id]
        finish_at = patient.in_progress_finish_at
        assert finish_at is not None
        fixed_steps = (
            PlanStep(
                exam_id=exam.id,
                department_id=exam.department_id,
                travel_minutes=0,
                arrival_at=patient.now,
                wait_minutes=0.0,
                start_at=patient.now,
                finish_at=finish_at,
                fixed_in_progress=True,
            ),
        )
        completed.add(exam.id)
        search_time = finish_at
        search_location = exam.department_id

    remaining = tuple(
        exam
        for exam in patient.exams
        if exam.id not in completed
    )
    if not remaining:
        completion = _minutes(patient.now, search_time)
        return ScheduleResult(
            patient_id=patient.patient_id,
            generated_at=patient.now,
            feasible=True,
            steps=fixed_steps,
            objective_score=completion,
            metrics=ScheduleMetrics(completion_minutes=completion),
        )

    best: tuple[tuple[float, tuple[str, ...]], _Candidate, int] | None = None

    def search(
        pending: tuple[Exam, ...],
        done: frozenset[str],
        current_time: datetime,
        location: str,
        steps: tuple[PlanStep, ...],
        travel_total: int,
        wait_total: float,
        delay_total: float,
    ) -> None:
        nonlocal best
        if not pending:
            order = tuple(step.exam_id for step in steps)
            inversions = _reorder_inversions(order, patient.previous_order)
            completion = _minutes(patient.now, current_time)
            score = (
                completion
                + travel_total * config.travel_penalty_per_minute
                + delay_total
                + inversions * config.reorder_penalty_per_inversion
            )
            key = (round(score, 9), order)
            candidate = _Candidate(
                steps=steps,
                finish_at=current_time,
                travel_minutes=travel_total,
                wait_minutes=wait_total,
                medical_delay_cost=delay_total,
            )
            if best is None or key < best[0]:
                best = (key, candidate, inversions)
            return

        for exam in sorted(pending, key=lambda item: item.id):
            if not set(exam.prerequisites).issubset(done):
                continue
            step = _place_exam(
                exam=exam,
                current_time=current_time,
                current_location=location,
                department=departments[exam.department_id],
                travel_times=travel_times,
                activity_prediction=activity_prediction,
            )
            if step is None:
                continue
            new_delay = _minutes(patient.now, step.start_at) * exam.delay_cost_per_minute
            search(
                pending=tuple(item for item in pending if item.id != exam.id),
                done=done | {exam.id},
                current_time=step.finish_at,
                location=exam.department_id,
                steps=steps + (step,),
                travel_total=travel_total + step.travel_minutes,
                wait_total=wait_total + step.wait_minutes,
                delay_total=delay_total + new_delay,
            )

    search(
        pending=remaining,
        done=frozenset(completed),
        current_time=search_time,
        location=search_location,
        steps=(),
        travel_total=0,
        wait_total=0.0,
        delay_total=0.0,
    )

    if best is None:
        return ScheduleResult(
            patient_id=patient.patient_id,
            generated_at=patient.now,
            feasible=False,
            steps=fixed_steps,
            reasons=_infeasible_reasons(remaining, completed, departments),
        )

    (score, _), candidate, inversions = best
    completion = _minutes(patient.now, candidate.finish_at)
    all_steps = fixed_steps + candidate.steps
    return ScheduleResult(
        patient_id=patient.patient_id,
        generated_at=patient.now,
        feasible=True,
        steps=all_steps,
        objective_score=score,
        metrics=ScheduleMetrics(
            completion_minutes=completion,
            travel_minutes=candidate.travel_minutes,
            wait_minutes=candidate.wait_minutes,
            medical_delay_cost=candidate.medical_delay_cost,
            reorder_inversions=inversions,
        ),
    )


def _place_exam(
    exam: Exam,
    current_time: datetime,
    current_location: str,
    department: DepartmentState,
    travel_times: TravelTimeMatrix,
    activity_prediction: PersonalActivityPrediction | None,
) -> PlanStep | None:
    if not department.accepting_patients and department.available_from is None:
        return None

    baseline_travel = travel_times.between(current_location, exam.department_id)
    travel = (
        activity_prediction.estimate_minutes(baseline_travel)
        if activity_prediction is not None
        else baseline_travel
    )
    arrival = current_time + timedelta(minutes=travel)
    ready_at = department.queue_ready_at
    if department.available_from is not None:
        ready_at = max(ready_at, department.available_from)
    start = max(arrival, ready_at)
    if exam.earliest_start is not None:
        start = max(start, exam.earliest_start)
    finish = start + timedelta(minutes=exam.duration_minutes)
    if exam.latest_finish is not None and finish > exam.latest_finish:
        return None

    return PlanStep(
        exam_id=exam.id,
        department_id=exam.department_id,
        travel_minutes=travel,
        arrival_at=arrival,
        wait_minutes=_minutes(arrival, start),
        start_at=start,
        finish_at=finish,
    )


def _validate(
    patient: PatientState,
    departments: Mapping[str, DepartmentState],
    travel_times: TravelTimeMatrix,
    config: PlannerConfig,
) -> dict[str, Exam]:
    if config.max_exact_pending_exams < 1:
        raise ValueError("max_exact_pending_exams 必须至少为 1")
    if config.travel_penalty_per_minute < 0 or config.reorder_penalty_per_inversion < 0:
        raise ValueError("代价权重不能为负数")
    if travel_times.default_minutes < 0 or any(
        minutes < 0 for minutes in travel_times.minutes_by_edge.values()
    ):
        raise ValueError("步行时间不能为负数")

    exams_by_id: dict[str, Exam] = {}
    for exam in patient.exams:
        if not exam.id:
            raise ValueError("检查 id 不能为空")
        if exam.id in exams_by_id:
            raise ValueError(f"检查 id 重复: {exam.id}")
        if exam.duration_minutes <= 0:
            raise ValueError(f"检查耗时必须为正数: {exam.id}")
        if exam.delay_cost_per_minute < 0:
            raise ValueError(f"医疗延迟代价不能为负数: {exam.id}")
        if exam.department_id not in departments:
            raise ValueError(f"缺少科室状态: {exam.department_id}")
        if (
            exam.earliest_start is not None
            and exam.latest_finish is not None
            and exam.earliest_start >= exam.latest_finish
        ):
            raise ValueError(f"检查时间窗无效: {exam.id}")
        exams_by_id[exam.id] = exam

    for department_id, department in departments.items():
        if department.id != department_id:
            raise ValueError(
                f"科室映射键与状态 id 不一致: {department_id} != {department.id}"
            )
        if department.expected_wait_minutes < 0:
            raise ValueError(f"预计等待时间不能为负数: {department_id}")

    known_ids = set(exams_by_id)
    unknown_completed = set(patient.completed_exam_ids) - known_ids
    if unknown_completed:
        raise ValueError(f"已完成检查不存在: {sorted(unknown_completed)}")
    for exam in patient.exams:
        unknown = set(exam.prerequisites) - known_ids
        if unknown:
            raise ValueError(f"{exam.id} 的前置检查不存在: {sorted(unknown)}")

    if patient.in_progress_exam_id is None:
        if patient.in_progress_finish_at is not None:
            raise ValueError("没有进行中检查时不能设置预计结束时间")
    else:
        if patient.in_progress_exam_id not in exams_by_id:
            raise ValueError("进行中检查不存在")
        if patient.in_progress_exam_id in patient.completed_exam_ids:
            raise ValueError("同一检查不能同时处于进行中和已完成状态")
        if patient.in_progress_finish_at is None:
            raise ValueError("进行中检查必须设置预计结束时间")
        if patient.in_progress_finish_at < patient.now:
            raise ValueError("进行中检查的预计结束时间不能早于当前时间")

    pending_count = len(patient.exams) - len(patient.completed_exam_ids)
    if patient.in_progress_exam_id is not None:
        pending_count -= 1
    if pending_count > config.max_exact_pending_exams:
        raise ValueError(
            f"待检查项目为 {pending_count} 个，超过精确搜索上限 "
            f"{config.max_exact_pending_exams} 个"
        )
    return exams_by_id


def _reorder_inversions(order: tuple[str, ...], previous_order: tuple[str, ...]) -> int:
    current = [exam_id for exam_id in order if exam_id in previous_order]
    previous_positions = {
        exam_id: index
        for index, exam_id in enumerate(previous_order)
        if exam_id in current
    }
    positions = [previous_positions[exam_id] for exam_id in current]
    return sum(
        1
        for left in range(len(positions))
        for right in range(left + 1, len(positions))
        if positions[left] > positions[right]
    )


def _infeasible_reasons(
    remaining: tuple[Exam, ...],
    completed: set[str],
    departments: Mapping[str, DepartmentState],
) -> tuple[str, ...]:
    reasons: list[str] = []
    permanently_closed = sorted(
        exam.id
        for exam in remaining
        if not departments[exam.department_id].accepting_patients
        and departments[exam.department_id].available_from is None
    )
    if permanently_closed:
        reasons.append(f"科室当前不可接诊: {', '.join(permanently_closed)}")

    if not _has_topological_order(remaining, completed):
        reasons.append("检查前置关系存在闭环，无法生成顺序")

    if any(exam.latest_finish is not None for exam in remaining):
        reasons.append("至少一个检查无法在最晚完成时间前结束")
    if not reasons:
        reasons.append("当前队列、时间窗和前置关系下不存在可行顺序")
    return tuple(reasons)


def _has_topological_order(remaining: tuple[Exam, ...], completed: set[str]) -> bool:
    pending = {exam.id: exam for exam in remaining}
    done = set(completed)
    while pending:
        ready = sorted(
            exam_id
            for exam_id, exam in pending.items()
            if set(exam.prerequisites).issubset(done)
        )
        if not ready:
            return False
        for exam_id in ready:
            pending.pop(exam_id)
            done.add(exam_id)
    return True


def _minutes(start: datetime, finish: datetime) -> float:
    return (finish - start).total_seconds() / 60
