"""Event-driven state updates and replanning for one patient."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from .activity_prediction import PersonalActivityPrediction
from .models import (
    DepartmentAvailabilityUpdated,
    DepartmentState,
    ExamCompleted,
    ExamStarted,
    PatientState,
    PlannerConfig,
    QueueWaitUpdated,
    ReplanOutcome,
    ScheduleResult,
    SchedulingEvent,
    TimeAdvanced,
    TravelTimeMatrix,
)
from .planner import build_schedule


class DynamicScheduler:
    """Maintain a patient snapshot and replan after chronological events."""

    def __init__(
        self,
        patient: PatientState,
        departments: Mapping[str, DepartmentState],
        travel_times: TravelTimeMatrix,
        config: PlannerConfig = PlannerConfig(),
        activity_prediction: PersonalActivityPrediction | None = None,
    ) -> None:
        if (
            activity_prediction is not None
            and activity_prediction.patient_id != patient.patient_id
        ):
            raise ValueError("个人活动预测与当前患者不一致")
        self._patient = patient
        self._departments = dict(departments)
        self._travel_times = travel_times
        self._config = config
        self._activity_prediction = activity_prediction
        self._last_plan: ScheduleResult | None = None

    @property
    def patient(self) -> PatientState:
        return self._patient

    @property
    def departments(self) -> Mapping[str, DepartmentState]:
        return dict(self._departments)

    def current_plan(self) -> ScheduleResult:
        if self._last_plan is None:
            self._last_plan = build_schedule(
                self._patient,
                self._departments,
                self._travel_times,
                self._config,
                self._activity_prediction,
            )
        return self._last_plan

    def update_activity_prediction(
        self,
        prediction: PersonalActivityPrediction,
    ) -> None:
        """Accept a new personal movement prediction without owning its model."""

        if prediction.patient_id != self._patient.patient_id:
            raise ValueError("个人活动预测与当前患者不一致")
        self._activity_prediction = prediction
        self._last_plan = None

    def apply_event(self, event: SchedulingEvent) -> ReplanOutcome:
        previous = self.current_plan()
        if event.at < self._patient.now:
            raise ValueError("事件时间不能早于当前调度时间")

        if isinstance(event, QueueWaitUpdated):
            department = self._require_department(event.department_id)
            if event.expected_wait_minutes < 0:
                raise ValueError("预计等待时间不能为负数")
            self._departments[event.department_id] = replace(
                department,
                observed_at=event.at,
                expected_wait_minutes=event.expected_wait_minutes,
            )
            self._patient = replace(self._patient, now=event.at)
        elif isinstance(event, DepartmentAvailabilityUpdated):
            department = self._require_department(event.department_id)
            if event.available_from is not None and event.available_from < event.at:
                raise ValueError("恢复接诊时间不能早于事件时间")
            self._departments[event.department_id] = replace(
                department,
                accepting_patients=event.accepting_patients,
                available_from=event.available_from,
            )
            self._patient = replace(self._patient, now=event.at)
        elif isinstance(event, ExamStarted):
            exam = self._require_exam(event.exam_id)
            if event.exam_id in self._patient.completed_exam_ids:
                raise ValueError("已完成的检查不能再次开始")
            if (
                self._patient.in_progress_exam_id is not None
                and self._patient.in_progress_exam_id != event.exam_id
            ):
                raise ValueError("已有另一项检查正在进行")
            if event.expected_finish_at <= event.at:
                raise ValueError("预计结束时间必须晚于开始时间")
            self._patient = replace(
                self._patient,
                now=event.at,
                location_id=exam.department_id,
                in_progress_exam_id=event.exam_id,
                in_progress_finish_at=event.expected_finish_at,
            )
        elif isinstance(event, ExamCompleted):
            exam = self._require_exam(event.exam_id)
            completed = self._patient.completed_exam_ids | {event.exam_id}
            clear_in_progress = self._patient.in_progress_exam_id == event.exam_id
            self._patient = replace(
                self._patient,
                now=event.at,
                location_id=event.location_id or exam.department_id,
                completed_exam_ids=completed,
                in_progress_exam_id=(
                    None if clear_in_progress else self._patient.in_progress_exam_id
                ),
                in_progress_finish_at=(
                    None if clear_in_progress else self._patient.in_progress_finish_at
                ),
            )
        elif isinstance(event, TimeAdvanced):
            self._patient = replace(
                self._patient,
                now=event.at,
                location_id=event.location_id or self._patient.location_id,
            )
        else:
            raise TypeError(f"不支持的调度事件: {type(event).__name__}")

        previous_remaining = _remaining_order(previous, self._patient)
        self._patient = replace(self._patient, previous_order=previous_remaining)
        current = build_schedule(
            self._patient,
            self._departments,
            self._travel_times,
            self._config,
            self._activity_prediction,
        )
        self._last_plan = current
        current_remaining = _remaining_order(current, self._patient)
        changed = previous_remaining != current_remaining
        return ReplanOutcome(
            event_name=type(event).__name__,
            previous=previous,
            current=current,
            remaining_order_changed=changed,
            explanation=_explain_change(previous_remaining, current_remaining, current),
        )

    def _require_department(self, department_id: str) -> DepartmentState:
        try:
            return self._departments[department_id]
        except KeyError as error:
            raise ValueError(f"未知科室: {department_id}") from error

    def _require_exam(self, exam_id: str):
        for exam in self._patient.exams:
            if exam.id == exam_id:
                return exam
        raise ValueError(f"未知检查: {exam_id}")


def _remaining_order(
    plan: ScheduleResult,
    patient: PatientState,
) -> tuple[str, ...]:
    return tuple(
        exam_id
        for exam_id in plan.order
        if exam_id not in patient.completed_exam_ids
        and exam_id != patient.in_progress_exam_id
    )


def _explain_change(
    previous_order: tuple[str, ...],
    current_order: tuple[str, ...],
    current: ScheduleResult,
) -> str:
    if not current.feasible:
        return "事件发生后暂无可行路线：" + "；".join(current.reasons)
    if previous_order == current_order:
        return "事件发生后，剩余检查顺序无需调整"
    before = " → ".join(previous_order) or "无"
    after = " → ".join(current_order) or "无"
    return f"剩余检查顺序由 {before} 调整为 {after}"
