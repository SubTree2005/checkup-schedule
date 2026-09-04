"""Online, multi-server waiting-time prediction for checkup departments."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from hashlib import blake2s
from heapq import heapify, heappop, heappush
from math import ceil, exp, isfinite, log, sqrt
from random import Random
from typing import Any, Mapping, Protocol, Sequence

from .models import DepartmentState


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """Features observable when a patient joins a department queue.

    ``queued_patients`` excludes patients already in service and the patient
    for whom the prediction is made. When the service requirements of every
    queued patient are known, ``queued_service_minutes`` may contain them in
    FCFS order. Otherwise it must be empty and learned service-time estimates
    are used.
    """

    department_id: str
    observed_at: datetime
    queued_patients: int
    capacity: int
    in_service_remaining_minutes: tuple[float, ...] = ()
    recent_service_minutes: tuple[float, ...] = ()
    queued_service_minutes: tuple[float, ...] = ()
    operational_delay_minutes: float = 0.0


@dataclass(frozen=True, slots=True)
class WaitPrediction:
    """Prediction contract consumed by scheduling, independent of model type."""

    department_id: str
    generated_at: datetime
    mean_minutes: float
    p90_minutes: float
    model_version: str
    sample_count: int

    def __post_init__(self) -> None:
        if not isfinite(self.mean_minutes) or self.mean_minutes < 0:
            raise ValueError("平均等待时间不能为负数")
        if not isfinite(self.p90_minutes) or self.p90_minutes < self.mean_minutes:
            raise ValueError("p90 等待时间不能小于平均等待时间")
        if (
            not isinstance(self.sample_count, int)
            or isinstance(self.sample_count, bool)
            or self.sample_count < 0
        ):
            raise ValueError("样本数量不能为负数")


@dataclass(frozen=True, slots=True)
class PredictionMetrics:
    """Offline or replay metrics for a group of completed predictions."""

    sample_count: int
    mae_minutes: float
    rmse_minutes: float
    mean_error_minutes: float
    p90_coverage: float


class WaitTimePredictor(Protocol):
    def predict(self, snapshot: QueueSnapshot) -> WaitPrediction: ...


@dataclass(slots=True)
class _OnlineStats:
    mean: float
    variance: float = 0.0
    count: int = 0

    def update(self, value: float, smoothing: float) -> None:
        self.update_batch((value,), smoothing)

    def update_batch(
        self,
        values: Sequence[float],
        smoothing: float,
        *,
        initialize_from_batch: bool = True,
        max_mean_change: float | None = None,
    ) -> None:
        batch_mean, batch_variance = _moments(values)
        if self.count == 0 and initialize_from_batch:
            self.mean = batch_mean
            self.variance = batch_variance
            self.count = len(values)
            return
        delta = batch_mean - self.mean
        adjustment = smoothing * delta
        if max_mean_change is not None:
            adjustment = min(max_mean_change, max(-max_mean_change, adjustment))
        self.mean += adjustment
        self.variance = (
            (1 - smoothing)
            * (self.variance + smoothing * delta * delta)
            + smoothing * batch_variance
        )
        self.count += len(values)


@dataclass(slots=True)
class _ResidualHistory:
    values: list[float] = field(default_factory=list)

    def append(self, value: float, max_size: int) -> None:
        self.values.append(value)
        overflow = len(self.values) - max_size
        if overflow > 0:
            del self.values[:overflow]


class AdaptiveQueuePredictor:
    """Online predictor combining queue simulation and adaptive service times.

    The point estimate is produced by an FCFS multi-server workload simulation,
    rather than by dividing total work by capacity. Service-time statistics are
    learned per department and, when timestamps are supplied, per weekday/weekend
    time bucket. A deterministic Monte Carlo pass estimates the queueing p90;
    completed wait outcomes can then correct p90 under-coverage by residual
    quantile calibration.

    The class deliberately depends only on the Python standard library so it can
    be used as a cold-start baseline before a hospital has enough data for a
    separate LightGBM/XGBoost service.
    """

    model_version = "adaptive-multiserver-v3"

    def __init__(
        self,
        default_service_minutes: float = 15.0,
        smoothing: float = 0.20,
        cold_start_uncertainty_minutes: float = 8.0,
        *,
        time_bucket_minutes: int = 60,
        min_bucket_samples: int = 8,
        simulation_samples: int = 256,
        calibration_min_samples: int = 20,
        bias_min_samples: int = 5,
        max_residual_history: int = 512,
        random_seed: int = 0,
        max_wait_bias_update_minutes: float = 5.0,
    ) -> None:
        if not isfinite(default_service_minutes) or default_service_minutes <= 0:
            raise ValueError("默认检查耗时必须为正数")
        if not isfinite(smoothing) or not 0 < smoothing <= 1:
            raise ValueError("平滑系数必须位于 (0, 1]")
        if (
            not isfinite(cold_start_uncertainty_minutes)
            or cold_start_uncertainty_minutes < 0
        ):
            raise ValueError("冷启动不确定度不能为负数")
        if (
            not isinstance(time_bucket_minutes, int)
            or isinstance(time_bucket_minutes, bool)
            or not 1 <= time_bucket_minutes <= 24 * 60
        ):
            raise ValueError("时间桶长度必须位于 1 到 1440 分钟")
        if (
            not isinstance(min_bucket_samples, int)
            or isinstance(min_bucket_samples, bool)
            or min_bucket_samples <= 0
        ):
            raise ValueError("时间桶最小样本数必须为正数")
        if (
            not isinstance(simulation_samples, int)
            or isinstance(simulation_samples, bool)
            or simulation_samples < 32
        ):
            raise ValueError("仿真样本数不能少于 32")
        if (
            not isinstance(calibration_min_samples, int)
            or isinstance(calibration_min_samples, bool)
            or not isinstance(bias_min_samples, int)
            or isinstance(bias_min_samples, bool)
            or not isinstance(max_residual_history, int)
            or isinstance(max_residual_history, bool)
            or calibration_min_samples <= 0
            or bias_min_samples <= 0
            or max_residual_history <= 0
        ):
            raise ValueError("偏差/校准样本阈值和残差窗口必须为正数")
        if not isinstance(random_seed, int) or isinstance(random_seed, bool):
            raise ValueError("随机种子必须为整数")
        if (
            not isfinite(max_wait_bias_update_minutes)
            or max_wait_bias_update_minutes <= 0
        ):
            raise ValueError("等待偏差单次更新上限必须为正数")
        self._default_service = default_service_minutes
        self._smoothing = smoothing
        self._cold_start_uncertainty = cold_start_uncertainty_minutes
        self._time_bucket_minutes = time_bucket_minutes
        self._min_bucket_samples = min_bucket_samples
        self._simulation_samples = simulation_samples
        self._calibration_min_samples = calibration_min_samples
        self._bias_min_samples = bias_min_samples
        self._max_residual_history = max_residual_history
        self._random_seed = random_seed
        self._max_wait_bias_update = max_wait_bias_update_minutes
        self._stats: dict[str, _OnlineStats] = {}
        self._bucket_stats: dict[tuple[str, bool, int], _OnlineStats] = {}
        self._wait_bias_stats: dict[str, _OnlineStats] = {}
        self._wait_residuals: dict[str, _ResidualHistory] = {}

    def observe_service_completion(
        self,
        department_id: str,
        duration_minutes: float,
        observed_at: datetime | None = None,
    ) -> None:
        """Update service-time estimates after one examination completes."""

        self.observe_service_batch(
            department_id,
            ((duration_minutes, observed_at),),
        )

    def observe_service_batch(
        self,
        department_id: str,
        observations: Sequence[tuple[float, datetime | None]],
    ) -> None:
        """Apply one bounded update from a validated group of service times."""

        if not department_id:
            raise ValueError("科室编号不能为空")
        if not observations:
            raise ValueError("服务耗时批次不能为空")
        durations: list[float] = []
        by_bucket: dict[tuple[str, bool, int], list[float]] = {}
        for duration_minutes, observed_at in observations:
            if not isfinite(duration_minutes) or duration_minutes <= 0:
                raise ValueError("实际检查耗时必须为正数")
            durations.append(duration_minutes)
            if observed_at is not None:
                key = self._bucket_key(department_id, observed_at)
                by_bucket.setdefault(key, []).append(duration_minutes)
        stats = self._stats.setdefault(
            department_id,
            _OnlineStats(mean=durations[0]),
        )
        stats.update_batch(durations, self._smoothing)
        for key, bucket_durations in by_bucket.items():
            bucket = self._bucket_stats.setdefault(
                key,
                _OnlineStats(mean=bucket_durations[0]),
            )
            bucket.update_batch(bucket_durations, self._smoothing)

    def observe_wait_outcome(
        self,
        prediction: WaitPrediction,
        actual_wait_minutes: float,
    ) -> None:
        """Calibrate future p90 values using a completed wait observation."""

        if not isfinite(actual_wait_minutes) or actual_wait_minutes < 0:
            raise ValueError("实际等待时间不能为负数")
        self.observe_wait_feedback_batch(
            prediction.department_id,
            mean_residuals=(actual_wait_minutes - prediction.mean_minutes,),
            p90_residuals=(actual_wait_minutes - prediction.p90_minutes,),
        )

    def observe_wait_feedback_batch(
        self,
        department_id: str,
        *,
        mean_residuals: Sequence[float],
        p90_residuals: Sequence[float],
    ) -> None:
        """Update mean bias and p90 calibration from a validated feedback batch."""

        if not department_id:
            raise ValueError("科室编号不能为空")
        if not mean_residuals or len(mean_residuals) != len(p90_residuals):
            raise ValueError("等待反馈残差必须非空且一一对应")
        if any(not isfinite(value) for value in (*mean_residuals, *p90_residuals)):
            raise ValueError("等待反馈残差必须是有限数值")
        bias = self._wait_bias_stats.setdefault(
            department_id,
            _OnlineStats(mean=0.0),
        )
        bias.update_batch(
            mean_residuals,
            self._smoothing,
            initialize_from_batch=False,
            max_mean_change=self._max_wait_bias_update,
        )
        history = self._wait_residuals.setdefault(
            department_id,
            _ResidualHistory(),
        )
        for residual in p90_residuals:
            history.append(residual, self._max_residual_history)

    def predict(self, snapshot: QueueSnapshot) -> WaitPrediction:
        _validate_snapshot(snapshot)
        service_mean, service_std, sample_count = self._service_distribution(snapshot)
        simulated_waits = self._simulate_waits(
            snapshot,
            service_mean,
            service_std,
        )
        raw_mean_wait = sum(simulated_waits) / len(simulated_waits)
        mean_wait = max(
            0.0,
            raw_mean_wait + self._mean_bias_correction(snapshot.department_id),
        )
        unknown_jobs = (
            0 if snapshot.queued_service_minutes else snapshot.queued_patients
        )
        epistemic_margin = (
            1.2816
            * self._cold_start_uncertainty
            * sqrt(unknown_jobs)
            / (snapshot.capacity * sqrt(sample_count + 1))
        )
        base_p90 = _quantile(simulated_waits, 0.90) + epistemic_margin
        calibrated_margin = self._calibration_margin(snapshot.department_id)
        p90_wait = max(mean_wait, base_p90 + calibrated_margin)
        return WaitPrediction(
            department_id=snapshot.department_id,
            generated_at=snapshot.observed_at,
            mean_minutes=max(0.0, mean_wait),
            p90_minutes=max(0.0, p90_wait),
            model_version=self.model_version,
            sample_count=sample_count,
        )

    def predict_many(
        self,
        snapshots: Sequence[QueueSnapshot],
    ) -> dict[str, WaitPrediction]:
        result: dict[str, WaitPrediction] = {}
        for snapshot in snapshots:
            if snapshot.department_id in result:
                raise ValueError(f"科室 {snapshot.department_id} 出现重复快照")
            result[snapshot.department_id] = self.predict(snapshot)
        return result

    def export_state(self) -> dict[str, Any]:
        """Return JSON-serializable learned state for database persistence."""

        return {
            "model_version": self.model_version,
            "config": {
                "default_service_minutes": self._default_service,
                "smoothing": self._smoothing,
                "cold_start_uncertainty_minutes": self._cold_start_uncertainty,
                "time_bucket_minutes": self._time_bucket_minutes,
                "min_bucket_samples": self._min_bucket_samples,
                "simulation_samples": self._simulation_samples,
                "calibration_min_samples": self._calibration_min_samples,
                "bias_min_samples": self._bias_min_samples,
                "max_residual_history": self._max_residual_history,
                "random_seed": self._random_seed,
                "max_wait_bias_update_minutes": self._max_wait_bias_update,
            },
            "department_stats": {
                department_id: _stats_to_dict(stats)
                for department_id, stats in self._stats.items()
            },
            "bucket_stats": [
                {
                    "department_id": department_id,
                    "weekend": weekend,
                    "bucket": bucket,
                    **_stats_to_dict(stats),
                }
                for (department_id, weekend, bucket), stats in sorted(
                    self._bucket_stats.items()
                )
            ],
            "wait_bias_stats": {
                department_id: _stats_to_dict(stats)
                for department_id, stats in self._wait_bias_stats.items()
            },
            "wait_residuals": {
                department_id: list(history.values)
                for department_id, history in self._wait_residuals.items()
            },
        }

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> AdaptiveQueuePredictor:
        """Restore state produced by :meth:`export_state`."""

        if state.get("model_version") not in {
            cls.model_version,
            "adaptive-multiserver-v2",
        }:
            raise ValueError("等待预测模型版本不兼容")
        config = state.get("config")
        if not isinstance(config, Mapping):
            raise ValueError("预测器状态缺少配置")
        predictor = cls(**dict(config))
        for department_id, raw in _mapping(state.get("department_stats")).items():
            predictor._stats[str(department_id)] = _stats_from_dict(raw)
        bucket_rows = state.get("bucket_stats", ())
        if not isinstance(bucket_rows, Sequence) or isinstance(bucket_rows, (str, bytes)):
            raise ValueError("时间桶状态格式错误")
        for row in bucket_rows:
            if not isinstance(row, Mapping):
                raise ValueError("时间桶状态格式错误")
            key = (
                str(row["department_id"]),
                bool(row["weekend"]),
                int(row["bucket"]),
            )
            predictor._bucket_stats[key] = _stats_from_dict(row)
        for department_id, raw in _mapping(state.get("wait_bias_stats")).items():
            predictor._wait_bias_stats[str(department_id)] = _stats_from_dict(
                raw,
                allow_nonpositive_mean=True,
            )
        for department_id, values in _mapping(state.get("wait_residuals")).items():
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise ValueError("等待残差状态格式错误")
            history = _ResidualHistory([float(value) for value in values])
            if len(history.values) > predictor._max_residual_history:
                history.values = history.values[-predictor._max_residual_history :]
            predictor._wait_residuals[str(department_id)] = history
        return predictor

    def _service_distribution(
        self,
        snapshot: QueueSnapshot,
    ) -> tuple[float, float, int]:
        global_stats = self._stats.get(snapshot.department_id)
        bucket_stats = self._bucket_stats.get(
            self._bucket_key(snapshot.department_id, snapshot.observed_at)
        )
        learned_mean: float | None = None
        learned_variance = 0.0
        learned_count = 0
        if global_stats is not None:
            learned_mean = global_stats.mean
            learned_variance = global_stats.variance
            learned_count = global_stats.count
        if bucket_stats is not None:
            if learned_mean is None:
                learned_mean = bucket_stats.mean
                learned_variance = bucket_stats.variance
            else:
                weight = min(1.0, bucket_stats.count / self._min_bucket_samples)
                learned_mean, learned_variance = _blend_distributions(
                    learned_mean,
                    learned_variance,
                    bucket_stats.mean,
                    bucket_stats.variance,
                    weight,
                )
            learned_count = max(learned_count, bucket_stats.count)

        recent = tuple(value for value in snapshot.recent_service_minutes if value > 0)
        recent_mean, recent_variance = _moments(recent)
        if learned_mean is None and recent:
            return recent_mean, sqrt(recent_variance), len(recent)
        if learned_mean is None:
            return self._default_service, self._cold_start_uncertainty, 0
        if recent:
            recent_weight = min(0.60, len(recent) / (len(recent) + 4))
            learned_mean, learned_variance = _blend_distributions(
                learned_mean,
                learned_variance,
                recent_mean,
                recent_variance,
                recent_weight,
            )
            learned_count = max(learned_count, len(recent))
        return learned_mean, sqrt(max(0.0, learned_variance)), learned_count

    def _simulate_waits(
        self,
        snapshot: QueueSnapshot,
        service_mean: float,
        service_std: float,
    ) -> list[float]:
        if snapshot.queued_service_minutes:
            exact_wait = _fcfs_wait_minutes(
                snapshot.capacity,
                snapshot.in_service_remaining_minutes,
                snapshot.queued_service_minutes,
                snapshot.operational_delay_minutes,
            )
            return [exact_wait]
        rng = Random(self._stable_seed(snapshot))
        waits: list[float] = []
        for _ in range(self._simulation_samples):
            durations = tuple(
                _sample_positive_duration(rng, service_mean, service_std)
                for _ in range(snapshot.queued_patients)
            )
            waits.append(
                _fcfs_wait_minutes(
                    snapshot.capacity,
                    snapshot.in_service_remaining_minutes,
                    durations,
                    snapshot.operational_delay_minutes,
                )
            )
        return waits

    def _calibration_margin(self, department_id: str) -> float:
        history = self._wait_residuals.get(department_id)
        if history is None or len(history.values) < self._calibration_min_samples:
            return 0.0
        return _quantile(history.values, 0.90)

    def _mean_bias_correction(self, department_id: str) -> float:
        stats = self._wait_bias_stats.get(department_id)
        if stats is None or stats.count < self._bias_min_samples:
            return 0.0
        return stats.mean

    def _bucket_key(
        self,
        department_id: str,
        observed_at: datetime,
    ) -> tuple[str, bool, int]:
        minute_of_day = observed_at.hour * 60 + observed_at.minute
        return (
            department_id,
            observed_at.weekday() >= 5,
            minute_of_day // self._time_bucket_minutes,
        )

    def _stable_seed(self, snapshot: QueueSnapshot) -> int:
        payload = repr(
            (
                self._random_seed,
                snapshot.department_id,
                snapshot.observed_at.isoformat(),
                snapshot.queued_patients,
                snapshot.capacity,
                snapshot.in_service_remaining_minutes,
                snapshot.recent_service_minutes,
                snapshot.queued_service_minutes,
                snapshot.operational_delay_minutes,
            )
        ).encode("utf-8")
        return int.from_bytes(blake2s(payload, digest_size=8).digest(), "big")


def evaluate_wait_predictions(
    outcomes: Sequence[tuple[WaitPrediction, float]],
) -> PredictionMetrics:
    """Calculate replay metrics from ``(prediction, actual_wait)`` pairs."""

    if not outcomes:
        raise ValueError("至少需要一条预测结果")
    errors: list[float] = []
    covered = 0
    for prediction, actual in outcomes:
        if not isfinite(actual) or actual < 0:
            raise ValueError("实际等待时间不能为负数")
        errors.append(prediction.mean_minutes - actual)
        covered += actual <= prediction.p90_minutes
    count = len(errors)
    return PredictionMetrics(
        sample_count=count,
        mae_minutes=sum(abs(error) for error in errors) / count,
        rmse_minutes=sqrt(sum(error * error for error in errors) / count),
        mean_error_minutes=sum(errors) / count,
        p90_coverage=covered / count,
    )


def apply_wait_predictions(
    departments: Mapping[str, DepartmentState],
    predictions: Mapping[str, WaitPrediction] | None,
    *,
    use_p90: bool = True,
    safety_buffer_minutes: float = 0.0,
    now: datetime | None = None,
    max_age_minutes: float = 15.0,
) -> dict[str, DepartmentState]:
    """Adapt prediction output to existing department snapshots.

    Missing, stale, mismatched, or future-dated predictions leave the original
    queue estimate untouched so scheduling degrades safely.
    """

    if safety_buffer_minutes < 0 or max_age_minutes < 0:
        raise ValueError("安全缓冲和最大预测年龄不能为负数")
    result = dict(departments)
    if not predictions:
        return result
    reference_time = now or max(
        (prediction.generated_at for prediction in predictions.values()),
        default=None,
    )
    for department_id, prediction in predictions.items():
        department = result.get(department_id)
        if department is None or prediction.department_id != department_id:
            continue
        if reference_time is not None:
            age = (reference_time - prediction.generated_at).total_seconds() / 60
            if age < 0 or age > max_age_minutes:
                continue
        predicted = prediction.p90_minutes if use_p90 else prediction.mean_minutes
        result[department_id] = replace(
            department,
            observed_at=prediction.generated_at,
            expected_wait_minutes=ceil(max(0.0, predicted + safety_buffer_minutes)),
        )
    return result


def _fcfs_wait_minutes(
    capacity: int,
    in_service_remaining: Sequence[float],
    queued_service: Sequence[float],
    operational_delay: float,
) -> float:
    server_ready = [float(value) for value in in_service_remaining]
    idle_servers = capacity - len(server_ready)
    heapify(server_ready)
    for duration in queued_service:
        if idle_servers:
            heappush(server_ready, duration)
            idle_servers -= 1
            continue
        earliest = heappop(server_ready)
        heappush(server_ready, earliest + duration)
    earliest_ready = 0.0 if idle_servers else server_ready[0]
    return earliest_ready + operational_delay


def _sample_positive_duration(
    rng: Random,
    mean: float,
    standard_deviation: float,
) -> float:
    if standard_deviation <= 1e-9:
        return mean
    variance_ratio = (standard_deviation / mean) ** 2
    sigma_squared = log(1 + variance_ratio)
    mu = log(mean) - sigma_squared / 2
    return exp(rng.normalvariate(mu, sqrt(sigma_squared)))


def _moments(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return mean, variance


def _blend_distributions(
    base_mean: float,
    base_variance: float,
    local_mean: float,
    local_variance: float,
    local_weight: float,
) -> tuple[float, float]:
    base_weight = 1 - local_weight
    mean = base_weight * base_mean + local_weight * local_mean
    variance = (
        base_weight * (base_variance + (base_mean - mean) ** 2)
        + local_weight * (local_variance + (local_mean - mean) ** 2)
    )
    return mean, variance


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("分位数计算至少需要一个值")
    ordered = sorted(values)
    rank = probability * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _stats_to_dict(stats: _OnlineStats) -> dict[str, float | int]:
    return {
        "mean": stats.mean,
        "variance": stats.variance,
        "count": stats.count,
    }


def _stats_from_dict(
    raw: Any,
    *,
    allow_nonpositive_mean: bool = False,
) -> _OnlineStats:
    if not isinstance(raw, Mapping):
        raise ValueError("统计状态格式错误")
    stats = _OnlineStats(
        mean=float(raw["mean"]),
        variance=float(raw["variance"]),
        count=int(raw["count"]),
    )
    invalid_mean = not isfinite(stats.mean) or (
        stats.mean <= 0 and not allow_nonpositive_mean
    )
    if (
        invalid_mean
        or not isfinite(stats.variance)
        or stats.variance < 0
        or stats.count <= 0
    ):
        raise ValueError("统计状态数值无效")
    return stats


def _mapping(raw: Any) -> Mapping[Any, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("预测器状态格式错误")
    return raw


def _validate_snapshot(snapshot: QueueSnapshot) -> None:
    if not snapshot.department_id:
        raise ValueError("科室编号不能为空")
    if (
        not isinstance(snapshot.queued_patients, int)
        or isinstance(snapshot.queued_patients, bool)
        or snapshot.queued_patients < 0
    ):
        raise ValueError("排队人数不能为负数")
    if (
        not isinstance(snapshot.capacity, int)
        or isinstance(snapshot.capacity, bool)
        or snapshot.capacity <= 0
    ):
        raise ValueError("科室容量必须为正数")
    if len(snapshot.in_service_remaining_minutes) > snapshot.capacity:
        raise ValueError("进行中项目数量不能超过科室容量")
    if any(
        not isfinite(value) or value < 0
        for value in snapshot.in_service_remaining_minutes
    ):
        raise ValueError("进行中项目的剩余时间不能为负数")
    if any(
        not isfinite(value) or value <= 0
        for value in snapshot.recent_service_minutes
    ):
        raise ValueError("近期检查耗时必须为正数")
    if snapshot.queued_service_minutes and (
        len(snapshot.queued_service_minutes) != snapshot.queued_patients
    ):
        raise ValueError("已知排队检查耗时必须与排队人数一一对应")
    if any(
        not isfinite(value) or value <= 0
        for value in snapshot.queued_service_minutes
    ):
        raise ValueError("排队检查耗时必须为正数")
    if (
        not isfinite(snapshot.operational_delay_minutes)
        or snapshot.operational_delay_minutes < 0
    ):
        raise ValueError("固定运行延迟不能为负数")
