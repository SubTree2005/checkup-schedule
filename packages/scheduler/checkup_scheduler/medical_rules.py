"""Extension contract for business-specific medical eligibility rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence

from .models import DepartmentState, Exam, PatientState


@dataclass(frozen=True, slots=True)
class MedicalRuleContext:
    """Read-only scheduling facts exposed to one medical rule."""

    patient: PatientState
    exam: Exam
    department: DepartmentState
    completed_exam_ids: frozenset[str]
    proposed_start: datetime
    proposed_finish: datetime


class MedicalEligibilityRule(Protocol):
    """Return a rejection reason, or ``None`` when the proposal is allowed."""

    def evaluate(self, context: MedicalRuleContext) -> str | None: ...


def first_medical_rule_rejection(
    rules: Sequence[MedicalEligibilityRule],
    context: MedicalRuleContext,
) -> str | None:
    for rule in rules:
        reason = rule.evaluate(context)
        if reason is not None:
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("医学规则拒绝原因必须是非空字符串")
            return reason
    return None
