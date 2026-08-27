"""Deadline propagation and completion-risk utilities for prerequisite DAGs.

This module is deliberately independent of predictors, feedback controllers,
and scheduling backends.  It turns policy-visible task windows into a reusable
critical-path contract that either the heuristic or CP-SAT planner may consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import exp, isfinite
from typing import Mapping, Sequence

from .models import Exam, TravelTimeMatrix


@dataclass(frozen=True, slots=True)
class CriticalTaskState:
    exam_id: str
    effective_latest_finish: datetime
    own_latest_finish: datetime
    successor_ids: tuple[str, ...]
    downstream_task_count: int
    terminal_descendant_count: int
    is_terminal_aggregator: bool


def propagate_effective_deadlines(
    exams: Sequence[Exam],
    own_latest_finishes: Mapping[str, datetime],
    travel_times: TravelTimeMatrix,
    *,
    required_buffer_minutes: float = 5.0,
) -> dict[str, CriticalTaskState]:
    """Propagate successor deadlines backwards through a prerequisite DAG.

    For an edge ``predecessor -> successor``, the predecessor must finish early
    enough to leave travel, the successor's duration, and a safety buffer before
    the successor's effective finish deadline.  Multiple successors take the
    minimum candidate deadline.  A cycle is rejected before any schedule is
    generated.
    """

    if not isfinite(required_buffer_minutes) or required_buffer_minutes < 0:
        raise ValueError("关键路径安全缓冲必须是非负有限数值")
    by_id = {exam.id: exam for exam in exams}
    if len(by_id) != len(exams):
        raise ValueError("关键路径检查编号不能重复")
    missing_deadlines = set(by_id) - set(own_latest_finishes)
    if missing_deadlines:
        raise ValueError(f"缺少检查自身截止时间: {sorted(missing_deadlines)}")

    successors: dict[str, list[str]] = {exam_id: [] for exam_id in by_id}
    indegree = {exam_id: 0 for exam_id in by_id}
    for exam in exams:
        unknown = set(exam.prerequisites) - set(by_id)
        if unknown:
            raise ValueError(f"{exam.id} 的前置检查不存在: {sorted(unknown)}")
        for predecessor in exam.prerequisites:
            successors[predecessor].append(exam.id)
            indegree[exam.id] += 1

    ready = sorted(exam_id for exam_id, degree in indegree.items() if degree == 0)
    topological: list[str] = []
    while ready:
        exam_id = ready.pop(0)
        topological.append(exam_id)
        for successor_id in sorted(successors[exam_id]):
            indegree[successor_id] -= 1
            if indegree[successor_id] == 0:
                ready.append(successor_id)
                ready.sort()
    if len(topological) != len(by_id):
        cyclic = sorted(exam_id for exam_id, degree in indegree.items() if degree > 0)
        raise ValueError(f"检查前置关系存在闭环: {cyclic}")

    effective = dict(own_latest_finishes)
    descendants: dict[str, set[str]] = {exam_id: set() for exam_id in by_id}
    terminals: dict[str, set[str]] = {exam_id: set() for exam_id in by_id}
    terminal_aggregators = {
        exam.id
        for exam in exams
        if not successors[exam.id] and len(exam.prerequisites) >= 2
    }
    buffer = timedelta(minutes=required_buffer_minutes)
    for exam_id in reversed(topological):
        exam = by_id[exam_id]
        for successor_id in successors[exam_id]:
            successor = by_id[successor_id]
            candidate = (
                effective[successor_id]
                - timedelta(minutes=successor.duration_minutes)
                - timedelta(
                    minutes=travel_times.between(
                        exam.department_id,
                        successor.department_id,
                    )
                )
                - buffer
            )
            effective[exam_id] = min(effective[exam_id], candidate)
            descendants[exam_id].add(successor_id)
            descendants[exam_id].update(descendants[successor_id])
            if successor_id in terminal_aggregators:
                terminals[exam_id].add(successor_id)
            terminals[exam_id].update(terminals[successor_id])

    return {
        exam_id: CriticalTaskState(
            exam_id=exam_id,
            effective_latest_finish=effective[exam_id],
            own_latest_finish=own_latest_finishes[exam_id],
            successor_ids=tuple(sorted(successors[exam_id])),
            downstream_task_count=len(descendants[exam_id]),
            terminal_descendant_count=len(terminals[exam_id]),
            is_terminal_aggregator=exam_id in terminal_aggregators,
        )
        for exam_id in by_id
    }


def nonlinear_deadline_pressure(
    critical_slack_minutes: float,
    *,
    high_risk_slack_minutes: float = 45.0,
) -> float:
    """Return a smooth, sharply increasing urgency in ``[0, 1]``.

    The logistic curve is intentionally nearly flat for comfortable slack and
    steep around the high-risk boundary; this avoids treating five minutes of
    local waiting as equivalent to losing the day's terminal examination.
    """

    if not isfinite(critical_slack_minutes):
        raise ValueError("关键路径余量必须是有限数值")
    if not isfinite(high_risk_slack_minutes) or high_risk_slack_minutes <= 0:
        raise ValueError("高风险余量阈值必须为正数")
    scale = max(5.0, high_risk_slack_minutes / 6.0)
    exponent = max(-60.0, min(60.0, (critical_slack_minutes - high_risk_slack_minutes) / scale))
    return 1.0 / (1.0 + exp(exponent))
