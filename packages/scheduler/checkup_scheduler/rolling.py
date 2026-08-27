"""Rolling-horizon orchestration around prediction and hybrid optimization."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Mapping, Sequence

from .activity_prediction import PersonalActivityPrediction
from .batch import BatchPlannerConfig, BatchScheduleResult
from .hybrid import (
    HybridPlannerConfig,
    HybridScheduleResult,
    build_hybrid_schedule,
)
from .models import DepartmentState, PatientState, TimeWindow, TravelTimeMatrix
from .medical_rules import MedicalEligibilityRule
from .wait_prediction import WaitPrediction


@dataclass(frozen=True, slots=True)
class RollingHorizonConfig:
    optimization_horizon_minutes: int = 120
    replan_interval_minutes: int = 5
    freeze_window_minutes: int = 15


@dataclass(frozen=True, slots=True)
class RollingPlanResult:
    schedule: BatchScheduleResult
    optimization_horizon: TimeWindow
    valid_until: datetime
    replanned: bool
    triggered_by: str
    backend: str
    optimizer_status: str
    fallback_reason: str | None = None
    cp_sat_invoked: bool = False
    solve_seconds: float = 0.0
    objective_improvement: float = 0.0
    completion_risk_improvement: float = 0.0


class RollingHorizonScheduler:
    """Replan the remaining day while optimizing only a near-term horizon."""

    def __init__(
        self,
        operating_window: TimeWindow,
        travel_times: TravelTimeMatrix,
        *,
        config: RollingHorizonConfig = RollingHorizonConfig(),
        batch_config: BatchPlannerConfig = BatchPlannerConfig(),
        hybrid_config: HybridPlannerConfig = HybridPlannerConfig(),
        medical_rules: Sequence[MedicalEligibilityRule] = (),
    ) -> None:
        if config.optimization_horizon_minutes <= 0:
            raise ValueError("滚动优化时域必须为正数")
        if config.replan_interval_minutes <= 0:
            raise ValueError("重排间隔必须为正数")
        if config.freeze_window_minutes < 0:
            raise ValueError("冻结窗口不能为负数")
        self._operating_window = operating_window
        self._travel_times = travel_times
        self._config = config
        self._batch_config = replace(
            batch_config,
            freeze_window_minutes=config.freeze_window_minutes,
        )
        self._hybrid_config = hybrid_config
        self._medical_rules = tuple(medical_rules)
        self._last_plan: RollingPlanResult | None = None

    @property
    def last_plan(self) -> RollingPlanResult | None:
        return self._last_plan

    def replan(
        self,
        now: datetime,
        patients: Sequence[PatientState],
        departments: Mapping[str, DepartmentState],
        *,
        wait_predictions: Mapping[str, WaitPrediction] | None = None,
        activity_predictions: Mapping[str, PersonalActivityPrediction] | None = None,
        force: bool = False,
        triggered_by: str = "interval",
    ) -> RollingPlanResult:
        if not self._operating_window.start <= now < self._operating_window.end:
            raise ValueError("当前时间不在本次运营时段内")
        if (
            not force
            and self._last_plan is not None
            and now < self._last_plan.valid_until
        ):
            return replace(
                self._last_plan,
                replanned=False,
                triggered_by="cadence_not_due",
            )

        remaining_day = TimeWindow(now, self._operating_window.end)
        horizon_end = min(
            now + timedelta(minutes=self._config.optimization_horizon_minutes),
            self._operating_window.end,
        )
        hybrid: HybridScheduleResult = build_hybrid_schedule(
            patients,
            departments,
            self._travel_times,
            remaining_day,
            optimization_horizon_end=horizon_end,
            wait_predictions=wait_predictions,
            activity_predictions=activity_predictions,
            previous_schedule=(
                self._last_plan.schedule if self._last_plan is not None else None
            ),
            batch_config=self._batch_config,
            hybrid_config=self._hybrid_config,
            medical_rules=self._medical_rules,
        )
        result = RollingPlanResult(
            schedule=hybrid.schedule,
            optimization_horizon=TimeWindow(now, horizon_end),
            valid_until=min(
                now + timedelta(minutes=self._config.replan_interval_minutes),
                self._operating_window.end,
            ),
            replanned=True,
            triggered_by=triggered_by,
            backend=hybrid.backend,
            optimizer_status=hybrid.status,
            fallback_reason=hybrid.fallback_reason,
            cp_sat_invoked=hybrid.cp_sat_invoked,
            solve_seconds=hybrid.solve_seconds,
            objective_improvement=hybrid.objective_improvement,
            completion_risk_improvement=hybrid.completion_risk_improvement,
        )
        self._last_plan = result
        return result
