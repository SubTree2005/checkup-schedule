from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import DepartmentInfo, ExamInfo, ExamPlan, PlanExecutionDetail, utcnow


@dataclass(frozen=True)
class DepartmentQueueState:
    department_id: str
    waiting_count: int
    active_count: int
    estimated_wait_time: int


@dataclass(frozen=True)
class ItemQueueState:
    item_id: str
    item_name: str
    department_id: str
    queue_count: int
    active_count: int
    estimated_wait_time: int


def system_department_queues(
    db: Session,
    hospital_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, DepartmentQueueState]:
    """Derive the live queue from each active plan's current actionable step.

    A plan contributes exactly once: its running step is active, otherwise its
    earliest pending step is waiting. Future appointments and interrupted plans
    are intentionally excluded until the patient starts or resumes the plan.
    """
    observed_at = now or utcnow()
    rows = db.execute(
        select(PlanExecutionDetail, ExamInfo, DepartmentInfo)
        .join(ExamPlan, ExamPlan.plan_id == PlanExecutionDetail.plan_id)
        .join(ExamInfo, ExamInfo.item_id == PlanExecutionDetail.item_id)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(
            ExamPlan.hospital_id == hospital_id,
            ExamPlan.plan_status == "进行中",
            PlanExecutionDetail.exec_status.in_(["进行中", "待开始"]),
        )
        .order_by(PlanExecutionDetail.plan_id, PlanExecutionDetail.step_order)
    ).all()

    by_plan: dict[str, list[tuple[PlanExecutionDetail, ExamInfo, DepartmentInfo]]] = {}
    for row in rows:
        by_plan.setdefault(row[0].plan_id, []).append(row)

    counts: dict[str, dict[str, float | int]] = {}
    capacities: dict[str, int] = {}
    for plan_rows in by_plan.values():
        current = next((row for row in plan_rows if row[0].exec_status == "进行中"), plan_rows[0])
        detail, exam, department = current
        state = counts.setdefault(
            department.dept_id,
            {"waiting": 0, "active": 0, "workload_minutes": 0.0},
        )
        capacities[department.dept_id] = max(1, department.capacity)
        if detail.exec_status == "进行中":
            state["active"] += 1
            if detail.estimated_end and detail.estimated_end > observed_at:
                remaining = max(1, math.ceil((detail.estimated_end - observed_at).total_seconds() / 60))
            else:
                remaining = max(1, exam.duration)
            state["workload_minutes"] += remaining
        else:
            state["waiting"] += 1
            state["workload_minutes"] += max(1, exam.duration)

    return {
        department_id: DepartmentQueueState(
            department_id=department_id,
            waiting_count=int(values["waiting"]),
            active_count=int(values["active"]),
            estimated_wait_time=math.ceil(
                float(values["workload_minutes"]) / capacities[department_id] * 60
            ),
        )
        for department_id, values in counts.items()
    }


def system_item_queues(
    db: Session,
    hospital_id: str,
    item_ids: list[str] | set[str] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, ItemQueueState]:
    filters = [DepartmentInfo.hospital_id == hospital_id]
    if item_ids is not None:
        unique_ids = set(item_ids)
        if not unique_ids:
            return {}
        filters.append(ExamInfo.item_id.in_(unique_ids))
    exams = db.execute(
        select(ExamInfo, DepartmentInfo)
        .join(DepartmentInfo, DepartmentInfo.dept_id == ExamInfo.dept_id)
        .where(*filters)
    ).all()
    department_states = system_department_queues(db, hospital_id, now=now)
    result: dict[str, ItemQueueState] = {}
    for exam, department in exams:
        state = department_states.get(department.dept_id)
        result[exam.item_id] = ItemQueueState(
            item_id=exam.item_id,
            item_name=exam.item_name,
            department_id=department.dept_id,
            queue_count=state.waiting_count if state else 0,
            active_count=state.active_count if state else 0,
            estimated_wait_time=state.estimated_wait_time if state else 0,
        )
    return result
