"""Patient-specific movement prediction and phone-sensor feedback contracts."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from math import ceil, isfinite
from statistics import median
from threading import RLock
from typing import Any, Mapping, Protocol, Sequence

from .robust import winsorize


@dataclass(frozen=True, slots=True)
class PersonalActivityPrediction:
    """Patient-specific multiplier applied to baseline walking times."""

    patient_id: str
    generated_at: datetime
    travel_time_factor: float
    model_version: str
    sample_count: int
    current_speed_mps: float | None = None
    confidence: float = 0.0
    profile_version: int = 0
    total_distance_meters: float = 0.0
    total_trips: int = 0

    def __post_init__(self) -> None:
        if not self.patient_id:
            raise ValueError("患者编号不能为空")
        if (
            not isfinite(self.travel_time_factor)
            or self.travel_time_factor <= 0
        ):
            raise ValueError("个人移动时间系数必须为正数")
        if (
            not isinstance(self.sample_count, int)
            or isinstance(self.sample_count, bool)
            or self.sample_count < 0
        ):
            raise ValueError("个人活动样本数量不能为负数")
        if self.current_speed_mps is not None and (
            not isfinite(self.current_speed_mps) or self.current_speed_mps <= 0
        ):
            raise ValueError("当前步行速度必须为正数")
        if not isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("个人移动画像置信度必须位于 [0, 1]")
        if self.profile_version < 0 or self.total_trips < 0:
            raise ValueError("个人移动画像版本和行程数不能为负数")
        if not isfinite(self.total_distance_meters) or self.total_distance_meters < 0:
            raise ValueError("累计步行距离不能为负数")

    def estimate_minutes(self, baseline_minutes: int) -> int:
        if (
            not isinstance(baseline_minutes, int)
            or isinstance(baseline_minutes, bool)
            or baseline_minutes < 0
        ):
            raise ValueError("基准移动时间必须是非负整数")
        if baseline_minutes == 0:
            return 0
        return max(1, ceil(baseline_minutes * self.travel_time_factor))


@dataclass(frozen=True, slots=True)
class PersonalActivityFeedback:
    """One measured trip used only for one patient's mobility profile."""

    event_id: str
    patient_id: str
    origin_id: str
    destination_id: str
    occurred_at: datetime
    actual_activity_minutes: float
    baseline_travel_minutes: float
    source: str = "user_timing"
    confidence: float = 1.0
    distance_meters: float | None = None


@dataclass(frozen=True, slots=True)
class AccelerometerSample:
    """Platform-neutral three-axis acceleration sample."""

    captured_at: datetime
    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class AccelerometerBatch:
    """Raw sensor window plus route context supplied by the client."""

    event_id: str
    patient_id: str
    origin_id: str
    destination_id: str
    started_at: datetime
    ended_at: datetime
    baseline_travel_minutes: float
    samples: tuple[AccelerometerSample, ...]
    source: str = "phone_accelerometer"
    distance_meters: float | None = None


AGE_GROUP_YOUNG = "young"
AGE_GROUP_MIDDLE = "middle"
AGE_GROUP_OLDER = "older"

# Operational defaults derived from comfortable adult gait-speed references.
# They remain configuration values and must be calibrated with hospital data.
DEFAULT_WALKING_SPEED_PRESETS_MPS: Mapping[tuple[str, str], float] = {
    (AGE_GROUP_YOUNG, "M"): 1.43,
    (AGE_GROUP_YOUNG, "F"): 1.42,
    (AGE_GROUP_MIDDLE, "M"): 1.43,
    (AGE_GROUP_MIDDLE, "F"): 1.40,
    (AGE_GROUP_OLDER, "M"): 1.35,
    (AGE_GROUP_OLDER, "F"): 1.29,
}


def walking_age_group(age_years: int) -> str:
    if isinstance(age_years, bool) or not isinstance(age_years, int) or age_years < 0:
        raise ValueError("年龄必须是非负整数")
    if age_years < 40:
        return AGE_GROUP_YOUNG
    if age_years < 60:
        return AGE_GROUP_MIDDLE
    return AGE_GROUP_OLDER


def normalized_gender(gender: str) -> str:
    normalized = gender.strip().upper()
    aliases = {"男": "M", "MALE": "M", "M": "M", "女": "F", "FEMALE": "F", "F": "F"}
    if normalized not in aliases:
        raise ValueError("性别必须是 M/F、male/female 或男/女")
    return aliases[normalized]


def preset_walking_speed_mps(
    age_years: int,
    gender: str,
    presets: Mapping[tuple[str, str], float] = DEFAULT_WALKING_SPEED_PRESETS_MPS,
) -> float:
    key = (walking_age_group(age_years), normalized_gender(gender))
    try:
        speed = float(presets[key])
    except KeyError as error:
        raise ValueError(f"缺少步行速度预设值: {key}") from error
    if not isfinite(speed) or speed <= 0:
        raise ValueError("步行速度预设值必须为正数")
    return speed


class ActivitySensorAdapter(Protocol):
    """Port for WeChat, native-app, or future wearable sensor adapters."""

    def to_activity_feedback(
        self,
        batch: AccelerometerBatch,
    ) -> PersonalActivityFeedback | None: ...


class PersonalActivityTrainablePredictor(Protocol):
    def observe_activity_batch(
        self,
        patient_id: str,
        travel_time_factors: Sequence[float],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class PersonalActivityFeedbackConfig:
    min_batch_size: int = 3
    mad_threshold: float = 3.5
    min_travel_time_factor: float = 0.5
    max_travel_time_factor: float = 3.0
    max_activity_minutes: float = 180.0
    min_confidence: float = 0.5
    max_seen_event_ids: int = 10_000

    def __post_init__(self) -> None:
        if (
            not isinstance(self.min_batch_size, int)
            or isinstance(self.min_batch_size, bool)
            or self.min_batch_size < 3
        ):
            raise ValueError("个人活动反馈最小批量不能少于 3")
        numeric = (
            self.mad_threshold,
            self.min_travel_time_factor,
            self.max_travel_time_factor,
            self.max_activity_minutes,
            self.min_confidence,
        )
        if any(not isfinite(value) for value in numeric):
            raise ValueError("个人活动反馈参数必须是有限数值")
        if self.mad_threshold <= 0:
            raise ValueError("MAD 阈值必须为正数")
        if not 0 < self.min_travel_time_factor < self.max_travel_time_factor:
            raise ValueError("个人移动时间系数边界无效")
        if self.max_activity_minutes <= 0:
            raise ValueError("最大活动时间必须为正数")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("最低传感器置信度必须位于 [0, 1]")
        if (
            not isinstance(self.max_seen_event_ids, int)
            or isinstance(self.max_seen_event_ids, bool)
            or self.max_seen_event_ids <= 0
        ):
            raise ValueError("反馈去重窗口必须为正数")


@dataclass(frozen=True, slots=True)
class ActivityFeedbackUpdate:
    event_id: str
    patient_id: str
    accepted: bool
    duplicate: bool
    model_updated: bool
    samples_applied: int = 0
    samples_buffered: int = 0
    rejection_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _ActivityObservation:
    factor: float
    speed_mps: float | None
    distance_meters: float
    occurred_at: datetime


class PersonalActivityFeedbackController:
    """Build one robust mobility profile per patient, never per department."""

    def __init__(
        self,
        predictor: PersonalActivityTrainablePredictor,
        *,
        config: PersonalActivityFeedbackConfig = PersonalActivityFeedbackConfig(),
    ) -> None:
        self._predictor = predictor
        self._config = config
        self._buffers: dict[str, list[_ActivityObservation]] = {}
        self._seen_ids: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._lock = RLock()

    def ingest(self, feedback: PersonalActivityFeedback) -> ActivityFeedbackUpdate:
        with self._lock:
            if feedback.event_id in self._seen_ids:
                return ActivityFeedbackUpdate(
                    event_id=feedback.event_id,
                    patient_id=feedback.patient_id,
                    accepted=False,
                    duplicate=True,
                    model_updated=False,
                    samples_buffered=self.pending_count(feedback.patient_id),
                )
            self._validate_feedback(feedback)
            self._remember(feedback.event_id)
            if feedback.confidence < self._config.min_confidence:
                return ActivityFeedbackUpdate(
                    event_id=feedback.event_id,
                    patient_id=feedback.patient_id,
                    accepted=False,
                    duplicate=False,
                    model_updated=False,
                    samples_buffered=self.pending_count(feedback.patient_id),
                    rejection_reason="sensor_confidence_too_low",
                )
            raw_factor = (
                feedback.actual_activity_minutes
                / feedback.baseline_travel_minutes
            )
            bounded_factor = min(
                self._config.max_travel_time_factor,
                max(self._config.min_travel_time_factor, raw_factor),
            )
            speed_mps = None
            if feedback.distance_meters is not None:
                route_baseline_speed = feedback.distance_meters / (
                    feedback.baseline_travel_minutes * 60.0
                )
                # Keep the physical-speed observation consistent with the
                # already bounded travel-time factor for the same trip.
                speed_mps = route_baseline_speed / bounded_factor
            self._buffers.setdefault(feedback.patient_id, []).append(
                _ActivityObservation(
                    factor=bounded_factor,
                    speed_mps=speed_mps,
                    distance_meters=feedback.distance_meters or 0.0,
                    occurred_at=feedback.occurred_at,
                )
            )
            applied = self._flush_if_ready(feedback.patient_id)
            return ActivityFeedbackUpdate(
                event_id=feedback.event_id,
                patient_id=feedback.patient_id,
                accepted=True,
                duplicate=False,
                model_updated=bool(applied),
                samples_applied=applied,
                samples_buffered=self.pending_count(feedback.patient_id),
            )

    def ingest_sensor_batch(
        self,
        batch: AccelerometerBatch,
        adapter: ActivitySensorAdapter,
    ) -> ActivityFeedbackUpdate | None:
        """Convert a platform sensor batch through an injected adapter."""

        self._validate_sensor_batch(batch)
        feedback = adapter.to_activity_feedback(batch)
        if feedback is None:
            return None
        if (
            feedback.event_id != batch.event_id
            or feedback.patient_id != batch.patient_id
            or feedback.origin_id != batch.origin_id
            or feedback.destination_id != batch.destination_id
            or feedback.baseline_travel_minutes
            != batch.baseline_travel_minutes
            or feedback.distance_meters != batch.distance_meters
        ):
            raise ValueError("传感器适配结果与原始行程上下文不一致")
        elapsed_minutes = (batch.ended_at - batch.started_at).total_seconds() / 60
        if (
            not batch.started_at <= feedback.occurred_at <= batch.ended_at
            or feedback.actual_activity_minutes > elapsed_minutes
        ):
            raise ValueError("传感器适配结果超出原始行程时间窗")
        return self.ingest(feedback)

    def pending_count(self, patient_id: str) -> int:
        with self._lock:
            return len(self._buffers.get(patient_id, ()))

    def _flush_if_ready(self, patient_id: str) -> int:
        buffer = self._buffers.get(patient_id)
        if buffer is None or len(buffer) < self._config.min_batch_size:
            return 0
        robust_factors = winsorize(
            [item.factor for item in buffer],
            self._config.mad_threshold,
        )
        speed_observer = getattr(self._predictor, "observe_speed_batch", None)
        speeds = [item.speed_mps for item in buffer]
        if speed_observer is not None and all(speed is not None for speed in speeds):
            robust_speeds = winsorize(
                [float(speed) for speed in speeds],
                self._config.mad_threshold,
            )
            speed_observer(
                patient_id,
                robust_speeds,
                distances_meters=[item.distance_meters for item in buffer],
                occurred_at=max(item.occurred_at for item in buffer),
            )
        else:
            self._predictor.observe_activity_batch(patient_id, robust_factors)
        applied = len(buffer)
        buffer.clear()
        return applied

    def _validate_feedback(self, feedback: PersonalActivityFeedback) -> None:
        for label, value in (
            ("活动反馈事件编号", feedback.event_id),
            ("患者编号", feedback.patient_id),
            ("移动起点", feedback.origin_id),
            ("移动终点", feedback.destination_id),
            ("反馈来源", feedback.source),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label}不能为空")
        if feedback.origin_id == feedback.destination_id:
            raise ValueError("移动起点和终点不能相同")
        if not isinstance(feedback.occurred_at, datetime):
            raise ValueError("反馈发生时间格式错误")
        if (
            isinstance(feedback.actual_activity_minutes, bool)
            or not isfinite(feedback.actual_activity_minutes)
            or not 0
            < feedback.actual_activity_minutes
            <= self._config.max_activity_minutes
        ):
            raise ValueError("实际活动时间超出允许范围")
        if (
            isinstance(feedback.baseline_travel_minutes, bool)
            or not isfinite(feedback.baseline_travel_minutes)
            or feedback.baseline_travel_minutes <= 0
        ):
            raise ValueError("基准移动时间必须为正数")
        if (
            isinstance(feedback.confidence, bool)
            or not isfinite(feedback.confidence)
            or not 0 <= feedback.confidence <= 1
        ):
            raise ValueError("活动反馈置信度必须位于 [0, 1]")
        if feedback.distance_meters is not None and (
            isinstance(feedback.distance_meters, bool)
            or not isfinite(feedback.distance_meters)
            or feedback.distance_meters <= 0
        ):
            raise ValueError("步行距离必须为正数")

    def _validate_sensor_batch(self, batch: AccelerometerBatch) -> None:
        for label, value in (
            ("传感器事件编号", batch.event_id),
            ("患者编号", batch.patient_id),
            ("移动起点", batch.origin_id),
            ("移动终点", batch.destination_id),
            ("传感器来源", batch.source),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label}不能为空")
        if batch.origin_id == batch.destination_id:
            raise ValueError("移动起点和终点不能相同")
        if not isinstance(batch.started_at, datetime) or not isinstance(
            batch.ended_at,
            datetime,
        ):
            raise ValueError("传感器行程时间格式错误")
        if batch.ended_at <= batch.started_at:
            raise ValueError("传感器采样结束时间必须晚于开始时间")
        if not batch.samples:
            raise ValueError("加速度传感器采样不能为空")
        if (
            isinstance(batch.baseline_travel_minutes, bool)
            or not isfinite(batch.baseline_travel_minutes)
            or batch.baseline_travel_minutes <= 0
        ):
            raise ValueError("传感器行程的基准移动时间必须为正数")
        if batch.distance_meters is not None and (
            isinstance(batch.distance_meters, bool)
            or not isfinite(batch.distance_meters)
            or batch.distance_meters <= 0
        ):
            raise ValueError("传感器行程距离必须为正数")
        previous: datetime | None = None
        for sample in batch.samples:
            if not isinstance(sample.captured_at, datetime):
                raise ValueError("加速度采样时间格式错误")
            if not batch.started_at <= sample.captured_at <= batch.ended_at:
                raise ValueError("加速度采样时间超出行程窗口")
            if previous is not None and sample.captured_at < previous:
                raise ValueError("加速度采样必须按时间排序")
            if any(
                isinstance(value, bool) or not isfinite(value)
                for value in (sample.x, sample.y, sample.z)
            ):
                raise ValueError("加速度采样必须是有限数值")
            previous = sample.captured_at

    def _remember(self, event_id: str) -> None:
        self._seen_ids.add(event_id)
        self._seen_order.append(event_id)
        while len(self._seen_order) > self._config.max_seen_event_ids:
            expired = self._seen_order.popleft()
            self._seen_ids.remove(expired)


@dataclass(slots=True)
class _ActivityStats:
    factor: float = 1.0
    count: int = 0
    current_speed_mps: float | None = None
    initial_speed_mps: float | None = None
    total_distance_meters: float = 0.0
    total_trips: int = 0
    version: int = 0
    updated_at: datetime | None = None


class AdaptivePersonalActivityPredictor:
    """EWMA mobility model with demographic presets and robust trip feedback."""

    model_version = "personal-activity-v2"

    def __init__(
        self,
        *,
        smoothing: float = 0.20,
        min_samples: int = 3,
        min_factor: float = 0.5,
        max_factor: float = 3.0,
        max_factor_update: float = 0.25,
        reference_speed_mps: float = 1.35,
        max_speed_update_mps: float = 0.30,
        confidence_full_samples: int = 10,
        speed_presets_mps: Mapping[tuple[str, str], float] | None = None,
    ) -> None:
        if not isfinite(smoothing) or not 0 < smoothing <= 1:
            raise ValueError("个人活动平滑系数必须位于 (0, 1]")
        if (
            not isinstance(min_samples, int)
            or isinstance(min_samples, bool)
            or min_samples <= 0
        ):
            raise ValueError("个人活动最小样本数必须为正整数")
        if not 0 < min_factor < max_factor:
            raise ValueError("个人移动时间系数边界无效")
        if not isfinite(max_factor_update) or max_factor_update <= 0:
            raise ValueError("个人活动单次更新上限必须为正数")
        if not isfinite(reference_speed_mps) or reference_speed_mps <= 0:
            raise ValueError("基准步行速度必须为正数")
        if not isfinite(max_speed_update_mps) or max_speed_update_mps <= 0:
            raise ValueError("个人速度单次更新上限必须为正数")
        if (
            isinstance(confidence_full_samples, bool)
            or not isinstance(confidence_full_samples, int)
            or confidence_full_samples <= 0
        ):
            raise ValueError("满置信度样本数必须为正整数")
        self._smoothing = smoothing
        self._min_samples = min_samples
        self._min_factor = min_factor
        self._max_factor = max_factor
        self._max_factor_update = max_factor_update
        self._reference_speed_mps = reference_speed_mps
        self._max_speed_update_mps = max_speed_update_mps
        self._confidence_full_samples = confidence_full_samples
        self._speed_presets_mps = dict(
            speed_presets_mps or DEFAULT_WALKING_SPEED_PRESETS_MPS
        )
        for key in DEFAULT_WALKING_SPEED_PRESETS_MPS:
            preset_walking_speed_mps(
                20 if key[0] == AGE_GROUP_YOUNG else 45 if key[0] == AGE_GROUP_MIDDLE else 65,
                key[1],
                self._speed_presets_mps,
            )
        self._stats: dict[str, _ActivityStats] = {}

    @property
    def reference_speed_mps(self) -> float:
        return self._reference_speed_mps

    def register_profile(
        self,
        patient_id: str,
        age_years: int,
        gender: str,
        *,
        registered_at: datetime | None = None,
    ) -> None:
        if not patient_id:
            raise ValueError("患者编号不能为空")
        if patient_id in self._stats:
            return
        speed = preset_walking_speed_mps(
            age_years,
            gender,
            self._speed_presets_mps,
        )
        factor = min(
            self._max_factor,
            max(self._min_factor, self._reference_speed_mps / speed),
        )
        self._stats[patient_id] = _ActivityStats(
            factor=factor,
            current_speed_mps=speed,
            initial_speed_mps=speed,
            version=1,
            updated_at=registered_at,
        )

    def observe_activity_batch(
        self,
        patient_id: str,
        travel_time_factors: Sequence[float],
    ) -> None:
        if not patient_id:
            raise ValueError("患者编号不能为空")
        if not travel_time_factors:
            raise ValueError("个人活动反馈批次不能为空")
        if any(
            not isfinite(value)
            or not self._min_factor <= value <= self._max_factor
            for value in travel_time_factors
        ):
            raise ValueError("个人移动时间系数超出模型边界")
        # The median keeps one atypical trip from moving a patient's profile.
        batch_factor = median(travel_time_factors)
        stats = self._stats.setdefault(
            patient_id,
            _ActivityStats(current_speed_mps=self._reference_speed_mps),
        )
        adjustment = self._smoothing * (batch_factor - stats.factor)
        adjustment = min(
            self._max_factor_update,
            max(-self._max_factor_update, adjustment),
        )
        stats.factor += adjustment
        stats.factor = min(
            self._max_factor,
            max(self._min_factor, stats.factor),
        )
        stats.count += len(travel_time_factors)
        stats.current_speed_mps = self._reference_speed_mps / stats.factor
        stats.total_trips += len(travel_time_factors)
        stats.version = max(1, stats.version + 1)

    def observe_speed_batch(
        self,
        patient_id: str,
        speeds_mps: Sequence[float],
        *,
        distances_meters: Sequence[float],
        occurred_at: datetime,
    ) -> None:
        if not patient_id:
            raise ValueError("患者编号不能为空")
        if not speeds_mps or len(speeds_mps) != len(distances_meters):
            raise ValueError("速度与距离样本必须非空且一一对应")
        min_speed = self._reference_speed_mps / self._max_factor
        max_speed = self._reference_speed_mps / self._min_factor
        if any(not isfinite(value) or value <= 0 for value in speeds_mps):
            raise ValueError("实测步行速度必须为正数")
        if any(not isfinite(value) or value <= 0 for value in distances_meters):
            raise ValueError("实测步行距离必须为正数")
        bounded_speeds = [
            min(max_speed, max(min_speed, value)) for value in speeds_mps
        ]
        batch_speed = median(bounded_speeds)
        stats = self._stats.setdefault(
            patient_id,
            _ActivityStats(current_speed_mps=self._reference_speed_mps),
        )
        current_speed = stats.current_speed_mps or self._reference_speed_mps
        adjustment = self._smoothing * (batch_speed - current_speed)
        adjustment = min(
            self._max_speed_update_mps,
            max(-self._max_speed_update_mps, adjustment),
        )
        stats.current_speed_mps = min(
            max_speed,
            max(min_speed, current_speed + adjustment),
        )
        stats.factor = min(
            self._max_factor,
            max(self._min_factor, self._reference_speed_mps / stats.current_speed_mps),
        )
        stats.count += len(speeds_mps)
        stats.total_distance_meters += sum(distances_meters)
        stats.total_trips += len(speeds_mps)
        stats.version = max(1, stats.version + 1)
        stats.updated_at = occurred_at

    def predict(
        self,
        patient_id: str,
        generated_at: datetime,
        *,
        age_years: int | None = None,
        gender: str | None = None,
    ) -> PersonalActivityPrediction:
        if not patient_id:
            raise ValueError("患者编号不能为空")
        if patient_id not in self._stats and (age_years is not None or gender is not None):
            if age_years is None or gender is None:
                raise ValueError("初始化个人移动画像时必须同时提供年龄和性别")
            self.register_profile(
                patient_id,
                age_years,
                gender,
                registered_at=generated_at,
            )
        stats = self._stats.get(patient_id)
        count = stats.count if stats is not None else 0
        factor = (
            stats.factor
            if stats is not None
            and (stats.initial_speed_mps is not None or count >= self._min_samples)
            else 1.0
        )
        return PersonalActivityPrediction(
            patient_id=patient_id,
            generated_at=generated_at,
            travel_time_factor=factor,
            model_version=self.model_version,
            sample_count=count,
            current_speed_mps=(
                stats.current_speed_mps if stats is not None else self._reference_speed_mps
            ),
            confidence=min(1.0, count / self._confidence_full_samples),
            profile_version=(stats.version if stats is not None else 0),
            total_distance_meters=(
                stats.total_distance_meters if stats is not None else 0.0
            ),
            total_trips=(stats.total_trips if stats is not None else 0),
        )

    def predict_many(
        self,
        patient_ids: Sequence[str],
        generated_at: datetime,
        *,
        demographics: Mapping[str, tuple[int, str]] | None = None,
    ) -> dict[str, PersonalActivityPrediction]:
        if len(set(patient_ids)) != len(patient_ids):
            raise ValueError("个人活动预测患者编号不能重复")
        return {
            patient_id: self.predict(
                patient_id,
                generated_at,
                age_years=(demographics[patient_id][0] if demographics and patient_id in demographics else None),
                gender=(demographics[patient_id][1] if demographics and patient_id in demographics else None),
            )
            for patient_id in patient_ids
        }

    def export_state(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "config": {
                "smoothing": self._smoothing,
                "min_samples": self._min_samples,
                "min_factor": self._min_factor,
                "max_factor": self._max_factor,
                "max_factor_update": self._max_factor_update,
                "reference_speed_mps": self._reference_speed_mps,
                "max_speed_update_mps": self._max_speed_update_mps,
                "confidence_full_samples": self._confidence_full_samples,
                "speed_presets_mps": {
                    f"{age_group}:{gender}": speed
                    for (age_group, gender), speed in self._speed_presets_mps.items()
                },
            },
            "patient_stats": {
                patient_id: {
                    "factor": stats.factor,
                    "count": stats.count,
                    "current_speed_mps": stats.current_speed_mps,
                    "initial_speed_mps": stats.initial_speed_mps,
                    "total_distance_meters": stats.total_distance_meters,
                    "total_trips": stats.total_trips,
                    "version": stats.version,
                    "updated_at": (
                        stats.updated_at.isoformat() if stats.updated_at is not None else None
                    ),
                }
                for patient_id, stats in self._stats.items()
            },
        }

    @classmethod
    def from_state(
        cls,
        state: Mapping[str, Any],
    ) -> AdaptivePersonalActivityPredictor:
        state_version = state.get("model_version")
        if state_version not in {"personal-activity-v1", cls.model_version}:
            raise ValueError("个人活动模型版本不兼容")
        config = state.get("config")
        if not isinstance(config, Mapping):
            raise ValueError("个人活动模型状态缺少配置")
        restored_config = dict(config)
        raw_presets = restored_config.pop("speed_presets_mps", None)
        if raw_presets is not None:
            restored_config["speed_presets_mps"] = {
                tuple(key.split(":", 1)): float(value)
                for key, value in raw_presets.items()
            }
        predictor = cls(**restored_config)
        raw_stats = state.get("patient_stats", {})
        if not isinstance(raw_stats, Mapping):
            raise ValueError("个人活动模型患者状态格式错误")
        for patient_id, raw in raw_stats.items():
            if not isinstance(raw, Mapping):
                raise ValueError("个人活动模型患者状态格式错误")
            factor = float(raw["factor"])
            count = int(raw["count"])
            if not predictor._min_factor <= factor <= predictor._max_factor:
                raise ValueError("个人活动模型状态系数超出边界")
            if count < 0:
                raise ValueError("个人活动模型状态样本数不能为负数")
            current_speed = raw.get("current_speed_mps")
            initial_speed = raw.get("initial_speed_mps")
            updated_at = raw.get("updated_at")
            predictor._stats[str(patient_id)] = _ActivityStats(
                factor=factor,
                count=count,
                current_speed_mps=(
                    float(current_speed)
                    if current_speed is not None
                    else predictor._reference_speed_mps / factor
                ),
                initial_speed_mps=(
                    float(initial_speed) if initial_speed is not None else None
                ),
                total_distance_meters=float(raw.get("total_distance_meters", 0.0)),
                total_trips=int(raw.get("total_trips", count)),
                version=int(raw.get("version", int(count > 0))),
                updated_at=(
                    datetime.fromisoformat(str(updated_at)) if updated_at else None
                ),
            )
        return predictor


def personalized_travel_minutes(
    baseline_minutes: int,
    patient_id: str,
    predictions: Mapping[str, PersonalActivityPrediction] | None,
) -> int:
    """Apply a patient prediction without exposing its model to scheduling."""

    if predictions is None or patient_id not in predictions:
        return baseline_minutes
    prediction = predictions[patient_id]
    if prediction.patient_id != patient_id:
        raise ValueError("个人活动预测映射键与患者编号不一致")
    return prediction.estimate_minutes(baseline_minutes)
