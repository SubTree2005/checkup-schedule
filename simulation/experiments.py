"""Paired, repeated experiments over immutable Ground Truth traces."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean, stdev
from time import perf_counter
from typing import Mapping, Sequence

from checkup_scheduler.hybrid import cp_sat_available
from .engine import (
    DEFAULT_SIMULATION_POLICIES,
    PatientOutcome,
    SimulationMetrics,
    SimulationResult,
    SIMULATION_SCENARIOS,
    SUPPORTED_SIMULATION_POLICIES,
    build_realistic_hospital_scenario,
    run_comparative_simulation,
)
from .ground_truth import generate_ground_truth


POLICY_LABELS = {
    "fixed_fcfs": "固定顺序 + FCFS",
    "shortest_queue": "最短可见队列贪心",
    "static_batch": "V2 静态批量启发式",
    "rolling_no_feedback": "滚动调度（无反馈）",
    "dynamic_v6": "V6 滚动调度 + 双反馈",
    "no_feedback": "V9 关键路径（无反馈）",
    "wait_feedback_only": "V9 关键路径 + 等待反馈",
    "personal_activity_feedback_only": "V9 关键路径 + 个人活动反馈",
    "dual_feedback": "V9 关键路径 + 双反馈",
    "rolling_heuristic": "V9 关键路径滚动启发式",
    "rolling_cp_sat": "V9 关键路径滚动 CP-SAT",
    "feedback_heuristic": "V9 关键路径双反馈启发式",
    "feedback_cp_sat": "V9 关键路径双反馈 + CP-SAT",
    "v10_no_feedback": "V10 等待优先（无反馈）",
    "v10_wait_feedback_only": "V10 等待优先 + 等待反馈",
    "v10_personal_activity_feedback_only": "V10 等待优先 + 个人活动反馈",
    "v10_dual_feedback": "V10 等待优先 + 双反馈",
}

# Higher is favorable only for completion; time, instability, and failure are lower-better.
METRIC_DIRECTIONS = {
    "patient_completion_rate": "higher",
    "exam_completion_rate": "higher",
    "mean_journey_minutes": "lower",
    "median_journey_minutes": "lower",
    "p90_journey_minutes": "lower",
    "p95_journey_minutes": "lower",
    "mean_wait_minutes": "lower",
    "p90_wait_minutes": "lower",
    "p95_wait_minutes": "lower",
    "mean_walk_minutes": "lower",
    "wait_p90_p10_gap_minutes": "lower",
    "queue_abandon_count": "lower",
    "route_change_count": "lower",
    "deadline_violation_count": "lower",
    "cpu_seconds": "lower",
    "full_completion_rate": "higher",
    "unfinished_exam_count": "lower",
    "deadline_miss_count": "lower",
    "terminal_exam_completion_rate": "higher",
    "critical_path_miss_count": "lower",
    "patients_at_high_completion_risk": "lower",
    "mean_replans_per_patient": "lower",
    "route_change_notifications_per_patient": "lower",
    "patients_missing_one_exam": "lower",
    "cp_sat_invocation_count": "higher",
    "cp_sat_optimal_count": "higher",
    "cp_sat_feasible_count": "higher",
    "cp_sat_timeout_count": "lower",
    "cp_sat_fallback_count": "lower",
    "cp_sat_mean_solve_seconds": "lower",
    "cp_sat_p90_solve_seconds": "lower",
    "cp_sat_p95_solve_seconds": "lower",
    "cp_sat_objective_improvement": "higher",
    "cp_sat_completion_risk_improvement": "higher",
    "median_wait_minutes": "lower",
    "p99_wait_minutes": "lower",
    "max_wait_minutes": "lower",
    "patients_waiting_over_60m": "lower",
    "patients_waiting_over_90m": "lower",
    "patients_waiting_over_120m": "lower",
    "max_continuous_wait_minutes": "lower",
    "mean_idle_between_exams_minutes": "lower",
    "suppressed_route_change_count": "higher",
    "mean_department_utilization": "higher",
    "p90_department_queue_length": "lower",
    "peak_department_queue_length": "lower",
    "throughput_exams_per_hour": "higher",
}
LEGACY_METRIC_NAMES = (
    "patient_completion_rate",
    "exam_completion_rate",
    "mean_journey_minutes",
    "median_journey_minutes",
    "p90_journey_minutes",
    "p95_journey_minutes",
    "mean_wait_minutes",
    "p90_wait_minutes",
    "p95_wait_minutes",
    "mean_walk_minutes",
    "wait_p90_p10_gap_minutes",
    "queue_abandon_count",
    "route_change_count",
    "deadline_violation_count",
    "cpu_seconds",
)
V9_METRIC_NAMES = tuple(METRIC_DIRECTIONS)
V10_METRIC_NAMES = tuple(METRIC_DIRECTIONS)
CP_SAT_EXPERIMENT_POLICIES = frozenset({"rolling_cp_sat", "feedback_cp_sat"})


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    patient_count: int = 200
    replications: int = 10
    base_seed: int = 20260824
    policies: tuple[str, ...] = DEFAULT_SIMULATION_POLICIES
    treatment_policy: str = "dynamic_v6"
    scenario_names: tuple[str, ...] = ("normal_day",)
    predictor_bias_fractions: tuple[float, ...] = (-0.30, -0.20, -0.10, 0.10, 0.20, 0.30)
    metric_names: tuple[str, ...] = LEGACY_METRIC_NAMES
    minimum_replan_improvement_minutes: float | None = None

    def __post_init__(self) -> None:
        if self.patient_count <= 0:
            raise ValueError("重复实验患者数必须为正数")
        if self.replications < 2:
            raise ValueError("重复实验至少需要 2 次")
        if len(set(self.policies)) != len(self.policies):
            raise ValueError("实验策略不能重复")
        if self.treatment_policy not in self.policies:
            raise ValueError("主实验策略必须包含在策略列表中")
        unknown = set(self.policies) - set(SUPPORTED_SIMULATION_POLICIES)
        if unknown:
            raise ValueError(f"未知实验策略: {sorted(unknown)}")
        unknown_scenarios = set(self.scenario_names) - set(SIMULATION_SCENARIOS)
        if unknown_scenarios:
            raise ValueError(f"未知实验场景: {sorted(unknown_scenarios)}")
        if not self.scenario_names:
            raise ValueError("实验至少需要一个场景")
        unknown_metrics = set(self.metric_names) - set(METRIC_DIRECTIONS)
        if unknown_metrics or not self.metric_names:
            raise ValueError(f"未知或空的实验指标: {sorted(unknown_metrics)}")
        if (
            self.minimum_replan_improvement_minutes is not None
            and self.minimum_replan_improvement_minutes < 0
        ):
            raise ValueError("最小改线收益不能为负数")


@dataclass(frozen=True, slots=True)
class ReplicationMetrics:
    replication_index: int
    scenario_seed: int
    ground_truth_seed: int
    ground_truth_fingerprint: str
    policy: str
    metrics: SimulationMetrics
    scenario_name: str = "normal_day"
    observation_wait_bias_fraction: float = 0.0


@dataclass(frozen=True, slots=True)
class ReplicationPatientSummary:
    replication_index: int
    scenario_seed: int
    ground_truth_seed: int
    policy: str
    patient_id: str
    completed: bool
    completed_exam_count: int
    total_exam_count: int
    journey_minutes: float
    wait_minutes: float
    walk_minutes: float
    route_change_count: int
    queue_abandon_count: int
    incomplete_exam_count: int
    idle_between_exams_minutes: float = 0.0
    max_continuous_wait_minutes: float = 0.0
    suppressed_route_change_count: int = 0
    scenario_name: str = "normal_day"
    observation_wait_bias_fraction: float = 0.0


@dataclass(frozen=True, slots=True)
class AggregateMetric:
    policy: str
    metric: str
    favorable_direction: str
    replication_count: int
    mean: float
    standard_deviation: float
    ci95_low: float
    ci95_high: float
    minimum: float
    maximum: float
    scenario_name: str = "normal_day"
    observation_wait_bias_fraction: float = 0.0


@dataclass(frozen=True, slots=True)
class PairedComparison:
    treatment_policy: str
    baseline_policy: str
    metric: str
    favorable_direction: str
    replication_count: int
    # Effect is always treatment minus baseline; negative time is favorable.
    mean_paired_effect: float
    standard_deviation: float
    ci95_low: float
    ci95_high: float
    treatment_win_rate: float
    scenario_name: str = "normal_day"
    observation_wait_bias_fraction: float = 0.0


@dataclass(frozen=True, slots=True)
class RepeatedExperimentResult:
    config: ExperimentConfig
    replication_metrics: tuple[ReplicationMetrics, ...]
    patient_summaries: tuple[ReplicationPatientSummary, ...]
    aggregate_metrics: tuple[AggregateMetric, ...]
    paired_comparisons: tuple[PairedComparison, ...]
    representative_results: tuple[SimulationResult, ...]
    elapsed_seconds: float


def run_repeated_experiment(
    config: ExperimentConfig = ExperimentConfig(),
) -> RepeatedExperimentResult:
    """Run a common-random-numbers paired experiment over several seeds."""

    requested_cp_sat = CP_SAT_EXPERIMENT_POLICIES.intersection(config.policies)
    if requested_cp_sat and not cp_sat_available():
        requested = ", ".join(sorted(requested_cp_sat))
        raise RuntimeError(
            "正式实验请求 CP-SAT 策略 "
            f"({requested})，但当前环境未安装 OR-Tools。"
            "请先运行 python -m pip install -e \".[optimization]\"；"
            "实验已中止，不会静默回退到 heuristic。"
        )

    started = perf_counter()
    replication_rows: list[ReplicationMetrics] = []
    patient_rows: list[ReplicationPatientSummary] = []
    representative: tuple[SimulationResult, ...] = ()
    for scenario_index, scenario_name in enumerate(config.scenario_names):
        biases = (
            config.predictor_bias_fractions
            if scenario_name == "predictor_bias"
            else (0.0,)
        )
        for observation_bias in biases:
            for replication_index in range(config.replications):
                # Preserve V8 seeds exactly for the normal-day paired comparison.
                scenario_seed = config.base_seed + replication_index
                ground_truth_seed = config.base_seed + 1_000_003 + replication_index * 7_919
                scenario = build_realistic_hospital_scenario(
                    config.patient_count,
                    seed=scenario_seed,
                    scenario_name=scenario_name,
                )
                ground_truth = generate_ground_truth(
                    scenario,
                    seed=ground_truth_seed,
                    scenario_name=scenario_name,
                )
                results = run_comparative_simulation(
                    scenario,
                    ground_truth,
                    policies=config.policies,
                    observation_wait_bias_fraction=observation_bias,
                    minimum_replan_improvement_minutes=(
                        config.minimum_replan_improvement_minutes
                    ),
                )
                if not representative:
                    representative = results
                for result in results:
                    replication_rows.append(
                        ReplicationMetrics(
                            replication_index=replication_index,
                            scenario_seed=scenario_seed,
                            ground_truth_seed=ground_truth_seed,
                            ground_truth_fingerprint=ground_truth.trace_fingerprint,
                            policy=result.policy,
                            metrics=result.metrics,
                            scenario_name=scenario_name,
                            observation_wait_bias_fraction=observation_bias,
                        )
                    )
                    patient_rows.extend(
                        _patient_summaries(
                            replication_index,
                            scenario_seed,
                            ground_truth_seed,
                            result.policy,
                            result.patient_outcomes,
                            scenario_name=scenario_name,
                            observation_wait_bias_fraction=observation_bias,
                        )
                    )
    aggregates = _aggregate_metrics(
        replication_rows,
        config.policies,
        config.metric_names,
    )
    comparisons = _paired_comparisons(
        replication_rows,
        config.policies,
        config.treatment_policy,
        config.metric_names,
    )
    return RepeatedExperimentResult(
        config=config,
        replication_metrics=tuple(replication_rows),
        patient_summaries=tuple(patient_rows),
        aggregate_metrics=aggregates,
        paired_comparisons=comparisons,
        representative_results=representative,
        elapsed_seconds=perf_counter() - started,
    )


def _patient_summaries(
    replication_index: int,
    scenario_seed: int,
    ground_truth_seed: int,
    policy: str,
    outcomes: Sequence[PatientOutcome],
    *,
    scenario_name: str = "normal_day",
    observation_wait_bias_fraction: float = 0.0,
) -> tuple[ReplicationPatientSummary, ...]:
    return tuple(
        ReplicationPatientSummary(
            replication_index=replication_index,
            scenario_seed=scenario_seed,
            ground_truth_seed=ground_truth_seed,
            policy=policy,
            patient_id=item.patient_id,
            completed=item.completed,
            completed_exam_count=item.completed_exam_count,
            total_exam_count=item.total_exam_count,
            journey_minutes=item.journey_minutes,
            wait_minutes=item.wait_minutes,
            walk_minutes=item.walk_minutes,
            route_change_count=item.route_change_count,
            queue_abandon_count=item.queue_abandon_count,
            incomplete_exam_count=len(item.incomplete_exam_ids),
            idle_between_exams_minutes=item.idle_between_exams_minutes,
            max_continuous_wait_minutes=item.max_continuous_wait_minutes,
            suppressed_route_change_count=item.suppressed_route_change_count,
            scenario_name=scenario_name,
            observation_wait_bias_fraction=observation_wait_bias_fraction,
        )
        for item in outcomes
    )


def _aggregate_metrics(
    rows: Sequence[ReplicationMetrics],
    policies: Sequence[str],
    metric_names: Sequence[str] = LEGACY_METRIC_NAMES,
) -> tuple[AggregateMetric, ...]:
    output: list[AggregateMetric] = []
    cases = sorted(
        {(row.scenario_name, row.observation_wait_bias_fraction) for row in rows}
    )
    for scenario_name, observation_bias in cases:
      for policy in policies:
        policy_rows = [
            row for row in rows
            if row.policy == policy
            and row.scenario_name == scenario_name
            and row.observation_wait_bias_fraction == observation_bias
        ]
        for metric in metric_names:
            direction = METRIC_DIRECTIONS[metric]
            values = [float(getattr(row.metrics, metric)) for row in policy_rows]
            center, spread, low, high = _confidence_interval(values)
            output.append(
                AggregateMetric(
                    policy=policy,
                    metric=metric,
                    favorable_direction=direction,
                    replication_count=len(values),
                    mean=center,
                    standard_deviation=spread,
                    ci95_low=low,
                    ci95_high=high,
                    minimum=min(values),
                    maximum=max(values),
                    scenario_name=scenario_name,
                    observation_wait_bias_fraction=observation_bias,
                )
            )
    return tuple(output)


def _paired_comparisons(
    rows: Sequence[ReplicationMetrics],
    policies: Sequence[str],
    treatment: str,
    metric_names: Sequence[str] = LEGACY_METRIC_NAMES,
) -> tuple[PairedComparison, ...]:
    output: list[PairedComparison] = []
    cases = sorted(
        {(row.scenario_name, row.observation_wait_bias_fraction) for row in rows}
    )
    for scenario_name, observation_bias in cases:
      case_rows = [
          row for row in rows
          if row.scenario_name == scenario_name
          and row.observation_wait_bias_fraction == observation_bias
      ]
      indexed: dict[tuple[int, str], SimulationMetrics] = {
          (row.replication_index, row.policy): row.metrics for row in case_rows
      }
      replication_indices = sorted({row.replication_index for row in case_rows})
      for baseline in policies:
        if baseline == treatment:
            continue
        for metric in metric_names:
            direction = METRIC_DIRECTIONS[metric]
            effects = [
                float(getattr(indexed[(index, treatment)], metric))
                - float(getattr(indexed[(index, baseline)], metric))
                for index in replication_indices
            ]
            center, spread, low, high = _confidence_interval(effects)
            wins = sum(
                effect > 0 if direction == "higher" else effect < 0
                for effect in effects
            )
            output.append(
                PairedComparison(
                    treatment_policy=treatment,
                    baseline_policy=baseline,
                    metric=metric,
                    favorable_direction=direction,
                    replication_count=len(effects),
                    mean_paired_effect=center,
                    standard_deviation=spread,
                    ci95_low=low,
                    ci95_high=high,
                    treatment_win_rate=wins / len(effects),
                    scenario_name=scenario_name,
                    observation_wait_bias_fraction=observation_bias,
                )
            )
    return tuple(output)


def _confidence_interval(values: Sequence[float]) -> tuple[float, float, float, float]:
    if len(values) < 2:
        raise ValueError("置信区间至少需要两个样本")
    center = mean(values)
    spread = stdev(values)
    critical = _t95(len(values) - 1)
    margin = critical * spread / sqrt(len(values))
    return center, spread, center - margin, center + margin


def _t95(degrees_of_freedom: int) -> float:
    # Two-sided 95% Student-t critical values; normal approximation above 30.
    table: Mapping[int, float] = {
        1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
        6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
        11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
        16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
        21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
        26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
    }
    return table.get(degrees_of_freedom, 1.96)
