"""Department-level waiting feedback, isolated from patient activity data."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from math import isfinite, sqrt
from statistics import mean
from threading import RLock
from typing import Protocol, Sequence

from .robust import winsorize
from .wait_prediction import WaitPrediction


@dataclass(frozen=True, slots=True)
class WaitTimingFeedback:
    """One idempotent actual-wait observation for a department model."""

    event_id: str
    department_id: str
    occurred_at: datetime
    actual_wait_minutes: float
    prediction: WaitPrediction


# V5 import compatibility. The V6 object intentionally has no activity field.
PatientTimingFeedback = WaitTimingFeedback


@dataclass(frozen=True, slots=True)
class RobustFeedbackConfig:
    """Safety policy for department-level waiting feedback."""

    min_batch_size: int = 5
    mad_threshold: float = 3.5
    max_wait_minutes: float = 12 * 60.0
    max_seen_event_ids: int = 10_000
    max_mean_update_minutes: float = 30.0
    max_p90_update_minutes: float = 45.0
    guard_window_size: int = 40
    max_error_regression_fraction: float = 0.05
    guarded_update_enabled: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.min_batch_size, int)
            or isinstance(self.min_batch_size, bool)
            or self.min_batch_size < 5
        ):
            raise ValueError("等待反馈最小批量不能少于 5")
        if not isfinite(self.mad_threshold) or self.mad_threshold <= 0:
            raise ValueError("MAD 阈值必须为正数")
        if not isfinite(self.max_wait_minutes) or self.max_wait_minutes <= 0:
            raise ValueError("最大等待时间必须为正数")
        if (
            not isinstance(self.max_seen_event_ids, int)
            or isinstance(self.max_seen_event_ids, bool)
            or self.max_seen_event_ids <= 0
        ):
            raise ValueError("反馈去重窗口必须为正数")
        if (
            not isfinite(self.max_mean_update_minutes)
            or self.max_mean_update_minutes <= 0
            or not isfinite(self.max_p90_update_minutes)
            or self.max_p90_update_minutes <= 0
        ):
            raise ValueError("等待反馈单次更新上限必须为正数")
        if self.guard_window_size < self.min_batch_size:
            raise ValueError("保护窗口不能小于最小反馈批量")
        if (
            not isfinite(self.max_error_regression_fraction)
            or self.max_error_regression_fraction < 0
        ):
            raise ValueError("候选模型误差退化容忍度不能为负数")


@dataclass(frozen=True, slots=True)
class FeedbackUpdate:
    """Result returned after accepting or deduplicating one wait event."""

    event_id: str
    accepted: bool
    duplicate: bool
    model_updated: bool
    wait_samples_applied: int = 0
    wait_samples_buffered: int = 0
    # Zero-valued compatibility fields for V5 response consumers.
    activity_samples_applied: int = 0
    activity_samples_buffered: int = 0
    guard_accepted: bool | None = None
    guard_reason: str | None = None


class WaitFeedbackTrainablePredictor(Protocol):
    """Training-only boundary; the scheduler never depends on it."""

    def observe_wait_feedback_batch(
        self,
        department_id: str,
        *,
        mean_residuals: Sequence[float],
        p90_residuals: Sequence[float],
    ) -> None: ...


class FeedbackAcceptanceRule(Protocol):
    """Extension point for a future full production/candidate shadow model."""

    def accept(
        self,
        recent_mean_residuals: Sequence[float],
        proposed_correction: float,
    ) -> tuple[bool, str]: ...


@dataclass(frozen=True, slots=True)
class ResidualGuardedUpdateRule:
    """Lightweight shadow evaluation on a recent residual replay window."""

    max_error_regression_fraction: float = 0.05

    def accept(
        self,
        recent_mean_residuals: Sequence[float],
        proposed_correction: float,
    ) -> tuple[bool, str]:
        if not recent_mean_residuals:
            return False, "empty_guard_window"
        production = tuple(recent_mean_residuals)
        candidate = tuple(value - proposed_correction for value in production)
        production_bias = abs(mean(production))
        candidate_bias = abs(mean(candidate))
        production_mae = mean(abs(value) for value in production)
        candidate_mae = mean(abs(value) for value in candidate)
        production_rmse = sqrt(mean(value * value for value in production))
        candidate_rmse = sqrt(mean(value * value for value in candidate))
        tolerance = 1.0 + self.max_error_regression_fraction
        accepted = (
            candidate_bias <= production_bias + 1e-9
            and candidate_mae <= production_mae * tolerance + 1e-9
            and candidate_rmse <= production_rmse * tolerance + 1e-9
        )
        return accepted, ("bias_and_error_guard_passed" if accepted else "candidate_error_guard_rejected")


# V5 import compatibility.
FeedbackTrainablePredictor = WaitFeedbackTrainablePredictor


@dataclass(frozen=True, slots=True)
class _WaitResidual:
    mean: float
    p90: float


class GlobalWaitFeedbackController:
    """Apply robust micro-batches only to the global department wait model."""

    def __init__(
        self,
        predictor: WaitFeedbackTrainablePredictor,
        *,
        config: RobustFeedbackConfig = RobustFeedbackConfig(),
        acceptance_rule: FeedbackAcceptanceRule | None = None,
    ) -> None:
        self._predictor = predictor
        self._config = config
        self._buffers: dict[str, list[_WaitResidual]] = {}
        self._seen_ids: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._guard_history: dict[str, deque[float]] = {}
        self._acceptance_rule = acceptance_rule or ResidualGuardedUpdateRule(
            config.max_error_regression_fraction
        )
        self._lock = RLock()

    def ingest(self, feedback: WaitTimingFeedback) -> FeedbackUpdate:
        """Validate, deduplicate, buffer, and possibly update one department."""

        with self._lock:
            if feedback.event_id in self._seen_ids:
                return FeedbackUpdate(
                    event_id=feedback.event_id,
                    accepted=False,
                    duplicate=True,
                    model_updated=False,
                    wait_samples_buffered=self.pending_count(
                        feedback.department_id
                    ),
                )
            self._validate(feedback)
            self._buffers.setdefault(feedback.department_id, []).append(
                _WaitResidual(
                    mean=(
                        feedback.actual_wait_minutes
                        - feedback.prediction.mean_minutes
                    ),
                    p90=(
                        feedback.actual_wait_minutes
                        - feedback.prediction.p90_minutes
                    ),
                )
            )
            self._remember(feedback.event_id)
            applied, guard_accepted, guard_reason = self._flush_if_ready(
                feedback.department_id
            )
            return FeedbackUpdate(
                event_id=feedback.event_id,
                accepted=True,
                duplicate=False,
                model_updated=bool(applied),
                wait_samples_applied=applied,
                wait_samples_buffered=self.pending_count(
                    feedback.department_id
                ),
                guard_accepted=guard_accepted,
                guard_reason=guard_reason,
            )

    def pending_count(self, department_id: str) -> int:
        with self._lock:
            return len(self._buffers.get(department_id, ()))

    def pending_counts(self, department_id: str) -> tuple[int, int]:
        """V5-compatible ``(wait, activity)`` shape; activity is always zero."""

        return self.pending_count(department_id), 0

    def _flush_if_ready(self, department_id: str) -> tuple[int, bool | None, str | None]:
        buffer = self._buffers.get(department_id)
        if buffer is None or len(buffer) < self._config.min_batch_size:
            return 0, None, None
        robust_means = winsorize(
            [item.mean for item in buffer],
            self._config.mad_threshold,
        )
        robust_p90s = winsorize(
            [item.p90 for item in buffer],
            self._config.mad_threshold,
        )
        capped_means = tuple(
            min(
                self._config.max_mean_update_minutes,
                max(-self._config.max_mean_update_minutes, value),
            )
            for value in robust_means
        )
        capped_p90s = tuple(
            min(
                self._config.max_p90_update_minutes,
                max(-self._config.max_p90_update_minutes, value),
            )
            for value in robust_p90s
        )
        history = self._guard_history.setdefault(
            department_id,
            deque(maxlen=self._config.guard_window_size),
        )
        history.extend(robust_means)
        proposed_correction = mean(capped_means)
        if self._config.guarded_update_enabled:
            accepted, reason = self._acceptance_rule.accept(
                tuple(history),
                proposed_correction,
            )
        else:
            accepted, reason = True, "guard_disabled_for_legacy_replay"
        applied = len(buffer)
        buffer.clear()
        if not accepted:
            return 0, False, reason
        self._predictor.observe_wait_feedback_batch(
            department_id,
            mean_residuals=capped_means,
            p90_residuals=capped_p90s,
        )
        return applied, True, reason

    def _validate(self, feedback: WaitTimingFeedback) -> None:
        if not isinstance(feedback.event_id, str) or not feedback.event_id.strip():
            raise ValueError("反馈事件编号不能为空")
        if (
            not isinstance(feedback.department_id, str)
            or not feedback.department_id.strip()
        ):
            raise ValueError("科室编号不能为空")
        if not isinstance(feedback.occurred_at, datetime):
            raise ValueError("反馈发生时间格式错误")
        if (
            isinstance(feedback.actual_wait_minutes, bool)
            or not isfinite(feedback.actual_wait_minutes)
            or not 0
            <= feedback.actual_wait_minutes
            <= self._config.max_wait_minutes
        ):
            raise ValueError("实际等待时间超出允许范围")
        if feedback.prediction.department_id != feedback.department_id:
            raise ValueError("反馈科室与原始预测不一致")

    def _remember(self, event_id: str) -> None:
        self._seen_ids.add(event_id)
        self._seen_order.append(event_id)
        while len(self._seen_order) > self._config.max_seen_event_ids:
            expired = self._seen_order.popleft()
            self._seen_ids.remove(expired)


# V5 import compatibility. Its responsibility is now global waiting only.
RobustFeedbackController = GlobalWaitFeedbackController
