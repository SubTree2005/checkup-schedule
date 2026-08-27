"""Exports and report generation for repeated simulation experiments."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .engine import SimulationResult
from .experiments import (
    METRIC_DIRECTIONS,
    POLICY_LABELS,
    AggregateMetric,
    PairedComparison,
    RepeatedExperimentResult,
    ReplicationMetrics,
)


def export_repeated_experiment(
    output_directory: str | Path,
    experiment: RepeatedExperimentResult,
) -> tuple[Path, ...]:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    representative = experiment.representative_results
    if not representative:
        raise ValueError("重复实验缺少代表性运行")
    scenario = representative[0].scenario
    ground_truth = representative[0].ground_truth
    written: list[Path] = []
    written.append(_write_replication_metrics(output / "replication_metrics.csv", experiment.replication_metrics))
    written.append(_write_rows(output / "aggregate_metrics.csv", [asdict(item) for item in experiment.aggregate_metrics]))
    written.append(_write_rows(output / "paired_comparisons.csv", [asdict(item) for item in experiment.paired_comparisons]))
    written.append(_write_rows(output / "replication_patient_summary.csv", [asdict(item) for item in experiment.patient_summaries]))
    written.append(_write_manifest(output / "experiment_manifest.json", experiment))
    written.append(_write_observable_departments(output / "observable_departments.csv", scenario))
    written.append(_write_observable_patients(output / "observable_patients.csv", scenario))
    written.append(_write_observable_exams(output / "observable_exams.csv", scenario))
    written.append(_write_travel_matrix(output / "observable_travel_times.csv", scenario))
    written.append(_write_truth_patients(output / "ground_truth_patients.csv", representative[0]))
    written.append(_write_truth_services(output / "ground_truth_services.csv", representative[0]))
    written.append(_write_truth_walks(output / "ground_truth_walks.csv", representative[0]))
    written.append(_write_truth_downtimes(output / "ground_truth_downtimes.csv", representative[0]))
    written.append(_write_trajectories(output / "representative_patient_trajectories.csv", representative))
    written.append(_write_paths(output / "representative_patient_paths.jsonl", representative))
    written.append(_write_rows(output / "representative_department_metrics.csv", [asdict(item) for result in representative for item in result.department_outcomes]))
    written.append(_write_rows(output / "representative_replan_log.csv", [_replan_row(result, item) for result in representative for item in result.replan_records]))
    written.append(_write_report(output / "experiment_report.md", experiment))
    return tuple(written)


def _write_replication_metrics(path: Path, rows: Sequence[ReplicationMetrics]) -> Path:
    output = []
    for row in rows:
        flattened = {
            "replication_index": row.replication_index,
            "scenario_seed": row.scenario_seed,
            "ground_truth_seed": row.ground_truth_seed,
            "ground_truth_fingerprint": row.ground_truth_fingerprint,
            "policy": row.policy,
            "scenario_name": row.scenario_name,
            "observation_wait_bias_fraction": row.observation_wait_bias_fraction,
        }
        flattened.update(asdict(row.metrics))
        output.append(flattened)
    return _write_rows(path, output)


def _write_manifest(path: Path, experiment: RepeatedExperimentResult) -> Path:
    unique_runs: dict[tuple[str, float, int], ReplicationMetrics] = {}
    for row in experiment.replication_metrics:
        unique_runs.setdefault(
            (
                row.scenario_name,
                row.observation_wait_bias_fraction,
                row.replication_index,
            ),
            row,
        )
    payload = {
        "design": "paired common-random-numbers experiment",
        "ground_truth_isolation": (
            "All hidden arrivals, service times, walking times, adherence, and "
            "resource outages are sampled before policies are instantiated."
        ),
        "config": asdict(experiment.config),
        "elapsed_seconds": experiment.elapsed_seconds,
        "replications": [
            {
                "replication_index": row.replication_index,
                "scenario_seed": row.scenario_seed,
                "ground_truth_seed": row.ground_truth_seed,
                "ground_truth_fingerprint": row.ground_truth_fingerprint,
                "scenario_name": row.scenario_name,
                "observation_wait_bias_fraction": row.observation_wait_bias_fraction,
            }
            for row in unique_runs.values()
        ],
        "policy_labels": {
            policy: POLICY_LABELS[policy] for policy in experiment.config.policies
        },
        "metric_directions": METRIC_DIRECTIONS,
        "confidence_interval": "two-sided 95% Student-t interval across replications",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _write_observable_departments(path: Path, scenario: Any) -> Path:
    return _write_rows(
        path,
        [
            {
                "department_id": item.id,
                "department_name": item.name,
                "floor": item.floor,
                "zone": item.zone,
                "capacity": item.capacity,
                "estimated_duration_minutes": item.estimated_duration_minutes,
                "service_windows": _windows(item.service_windows),
                "requirements": "；".join(item.requirements),
            }
            for item in scenario.departments.values()
        ],
    )


def _write_observable_patients(path: Path, scenario: Any) -> Path:
    return _write_rows(
        path,
        [
            {
                "patient_id": item.patient_id,
                "age": item.age,
                "sex": item.sex,
                "scheduled_arrival": item.scheduled_arrival,
                "declared_availability_windows": _windows(item.availability_windows),
                "exam_count": len(item.exams),
                "fixed_baseline_order": ">".join(item.baseline_order),
            }
            for item in scenario.patients
        ],
    )


def _write_observable_exams(path: Path, scenario: Any) -> Path:
    rows = []
    for patient in scenario.patients:
        for exam in patient.exams:
            rows.append(
                {
                    "patient_id": patient.patient_id,
                    "exam_id": exam.id,
                    "department_id": exam.department_id,
                    "estimated_duration_minutes": exam.duration_minutes,
                    "prerequisites": ",".join(exam.prerequisites),
                    "earliest_start": exam.earliest_start,
                    "latest_finish": exam.latest_finish,
                    "delay_cost_per_minute": exam.delay_cost_per_minute,
                }
            )
    return _write_rows(path, rows)


def _write_travel_matrix(path: Path, scenario: Any) -> Path:
    locations = ("LOBBY", *scenario.departments.keys())
    return _write_rows(
        path,
        [
            {
                "origin_id": origin,
                "destination_id": destination,
                "estimated_walk_minutes": scenario.travel_times.between(origin, destination),
            }
            for origin in locations
            for destination in locations
        ],
    )


def _write_truth_patients(path: Path, result: SimulationResult) -> Path:
    return _write_rows(
        path,
        [
            {
                "patient_id": item.patient_id,
                "actual_arrival": item.actual_arrival,
                "true_mobility_factor": item.true_mobility_factor,
                "adherence_delay_minutes": item.adherence_delay_minutes,
                "hidden_interruption_windows": _windows(item.interruption_windows),
            }
            for item in result.ground_truth.patients.values()
        ],
    )


def _write_truth_services(path: Path, result: SimulationResult) -> Path:
    return _write_rows(
        path,
        [
            {
                "patient_id": patient.patient_id,
                "exam_id": exam_id,
                "actual_service_minutes": minutes,
            }
            for patient in result.ground_truth.patients.values()
            for exam_id, minutes in patient.service_minutes_by_exam.items()
        ],
    )


def _write_truth_walks(path: Path, result: SimulationResult) -> Path:
    return _write_rows(
        path,
        [
            {
                "patient_id": patient.patient_id,
                "origin_id": origin,
                "destination_id": destination,
                "actual_walk_minutes": minutes,
            }
            for patient in result.ground_truth.patients.values()
            for (origin, destination), minutes in patient.walk_minutes_by_edge.items()
        ],
    )


def _write_truth_downtimes(path: Path, result: SimulationResult) -> Path:
    return _write_rows(
        path,
        [
            {
                "department_id": department_id,
                "resource_index": resource_index,
                "downtime_start": window.start,
                "downtime_end": window.end,
            }
            for department_id, resources in result.ground_truth.resource_downtimes.items()
            for resource_index, windows in resources.items()
            for window in windows
        ],
    )


def _write_trajectories(path: Path, results: Sequence[SimulationResult]) -> Path:
    rows = []
    for result in results:
        for outcome in result.patient_outcomes:
            for sequence, event in enumerate(outcome.events, start=1):
                rows.append(
                    {
                        "policy": result.policy,
                        "patient_id": outcome.patient_id,
                        "sequence": sequence,
                        "timestamp": event.at,
                        "event": event.event,
                        "location_id": event.location_id,
                        "exam_id": event.exam_id,
                        "department_id": event.department_id,
                        "details": event.details,
                    }
                )
    return _write_rows(path, rows)


def _write_paths(path: Path, results: Sequence[SimulationResult]) -> Path:
    with path.open("w", encoding="utf-8") as stream:
        for result in results:
            for outcome in result.patient_outcomes:
                payload = {
                    "policy": result.policy,
                    "patient_id": outcome.patient_id,
                    "outcome": {
                        key: value
                        for key, value in asdict(outcome).items()
                        if key != "events"
                    },
                    "events": [asdict(event) for event in outcome.events],
                }
                stream.write(json.dumps(payload, ensure_ascii=False, default=_json_default))
                stream.write("\n")
    return path


def _replan_row(result: SimulationResult, item: Any) -> dict[str, object]:
    row = {"policy": result.policy}
    row.update(asdict(item))
    return row


def _write_report(path: Path, experiment: RepeatedExperimentResult) -> Path:
    config = experiment.config
    aggregate = {
        (
            item.scenario_name,
            item.observation_wait_bias_fraction,
            item.policy,
            item.metric,
        ): item
        for item in experiment.aggregate_metrics
    }
    paired = {
        (
            item.scenario_name,
            item.observation_wait_bias_fraction,
            item.baseline_policy,
            item.metric,
        ): item
        for item in experiment.paired_comparisons
    }
    lines = [
        "# 多基线、重复 Ground Truth 配对仿真实验报告",
        "",
        "## 实验设计",
        "",
        f"本实验运行 {config.replications} 个独立随机种子，每次包含 {config.patient_count} 名患者，比较 {len(config.policies)} 种策略。每个种子先生成一份不可变 Ground Truth，再让所有策略回放同一份真实迟到、检查耗时、步行耗时、依从延迟和设备停机，因此策略之间是配对比较。",
        "",
        "Ground Truth 生成模块不导入调度器、预测器或反馈控制器；调度策略只接收可观察的预约、时间窗、估计耗时、队列和完成事件。隐藏值只由执行引擎用于推进现实状态和事后评分。",
        "",
        "## 策略",
        "",
    ]
    lines.extend(
        f"- `{policy}`：{POLICY_LABELS[policy]}"
        for policy in config.policies
    )
    lines.extend(
        [
            "",
        ]
    )
    cases = sorted(
        {
            (item.scenario_name, item.observation_wait_bias_fraction)
            for item in experiment.aggregate_metrics
        }
    )
    required = set(config.metric_names)
    for scenario_name, observation_bias in cases:
        label = scenario_name
        if observation_bias:
            label += f" / wait bias {observation_bias:+.0%}"
        lines.extend([f"## 场景：{label}", ""])
        v10_report = "p99_wait_minutes" in required
        if v10_report:
            lines.extend(
                [
                    "| 策略 | mean wait | median | P90 | P95 | P99 | max | >60m | >90m | >120m | 改线/人 |",
                    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
                ]
            )
        else:
            lines.extend(
                [
                    "| 策略 | 整套完成率 | 项目完成率 | 人均等待/min | P90等待/min | P95等待/min | P90总历时/min | P95总历时/min |",
                    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
                ]
            )
        for policy in config.policies:
            key = lambda metric: aggregate[(scenario_name, observation_bias, policy, metric)]
            if v10_report:
                lines.append(
                    "| " + POLICY_LABELS[policy]
                    + " | " + _agg(key("mean_wait_minutes"))
                    + " | " + _agg(key("median_wait_minutes"))
                    + " | " + _agg(key("p90_wait_minutes"))
                    + " | " + _agg(key("p95_wait_minutes"))
                    + " | " + _agg(key("p99_wait_minutes"))
                    + " | " + _agg(key("max_wait_minutes"))
                    + " | " + _agg(key("patients_waiting_over_60m"))
                    + " | " + _agg(key("patients_waiting_over_90m"))
                    + " | " + _agg(key("patients_waiting_over_120m"))
                    + " | " + _agg(key("route_change_notifications_per_patient"))
                    + " |"
                )
            else:
                lines.append(
                    "| " + POLICY_LABELS[policy]
                    + " | " + _agg(key("patient_completion_rate"), percent=True)
                    + " | " + _agg(key("exam_completion_rate"), percent=True)
                    + " | " + _agg(key("mean_wait_minutes"))
                    + " | " + _agg(key("p90_wait_minutes"))
                    + " | " + _agg(key("p95_wait_minutes"))
                    + " | " + _agg(key("p90_journey_minutes"))
                    + " | " + _agg(key("p95_journey_minutes"))
                    + " |"
                )
        if v10_report:
            lines.extend(
                [
                    "",
                    "### 体验、系统与副作用监控",
                    "",
                    "| 策略 | 步行/min | 检查间 idle/min | 利用率 | queue P90 | peak queue | throughput/h | 整套完成率 | 项目完成率 | INTERNAL完成率 | 未完成项目 |",
                    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
                ]
            )
            for policy in config.policies:
                key = lambda metric: aggregate[(scenario_name, observation_bias, policy, metric)]
                lines.append(
                    "| " + POLICY_LABELS[policy]
                    + " | " + _agg(key("mean_walk_minutes"))
                    + " | " + _agg(key("mean_idle_between_exams_minutes"))
                    + " | " + _agg(key("mean_department_utilization"), percent=True)
                    + " | " + _agg(key("p90_department_queue_length"))
                    + " | " + _agg(key("peak_department_queue_length"))
                    + " | " + _agg(key("throughput_exams_per_hour"))
                    + " | " + _agg(key("full_completion_rate"), percent=True)
                    + " | " + _agg(key("exam_completion_rate"), percent=True)
                    + " | " + _agg(key("terminal_exam_completion_rate"), percent=True)
                    + " | " + _agg(key("unfinished_exam_count"))
                    + " |"
                )
        lines.extend(
            [
                "",
                f"### {POLICY_LABELS[config.treatment_policy]} 的配对效应",
                "",
                "统一为 treatment − baseline；等待时间与改线为负更好。",
                "",
                (
                    "| baseline | mean wait | P90 | P95 | P99 | max | 改线/人 |"
                    if v10_report
                    else "| baseline | 整套完成率/pp | 人均等待/min | P90等待/min | P95等待/min | P90总历时/min | P95总历时/min |"
                ),
                (
                    "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"
                    if v10_report
                    else "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"
                ),
            ]
        )
        for baseline in config.policies:
            if baseline == config.treatment_policy:
                continue
            lookup = lambda metric: paired[(scenario_name, observation_bias, baseline, metric)]
            if v10_report:
                lines.append(
                    f"| {POLICY_LABELS[baseline]}"
                    f" | {_paired(lookup('mean_wait_minutes'))}"
                    f" | {_paired(lookup('p90_wait_minutes'))}"
                    f" | {_paired(lookup('p95_wait_minutes'))}"
                    f" | {_paired(lookup('p99_wait_minutes'))}"
                    f" | {_paired(lookup('max_wait_minutes'))}"
                    f" | {_paired(lookup('route_change_notifications_per_patient'))} |"
                )
            else:
                lines.append(
                    f"| {POLICY_LABELS[baseline]}"
                    f" | {_paired(lookup('patient_completion_rate'), percent_points=True)}"
                    f" | {_paired(lookup('mean_wait_minutes'))}"
                    f" | {_paired(lookup('p90_wait_minutes'))}"
                    f" | {_paired(lookup('p95_wait_minutes'))}"
                    f" | {_paired(lookup('p90_journey_minutes'))}"
                    f" | {_paired(lookup('p95_journey_minutes'))} |"
                )
        lines.append("")
    lines.extend(
        [
            "## 输出说明",
            "",
            "- `replication_metrics.csv`：每个种子、每种策略的完整指标",
            "- `aggregate_metrics.csv`：均值、标准差、95% CI、最小值和最大值",
            f"- `paired_comparisons.csv`：{POLICY_LABELS[config.treatment_policy]} 相对各 baseline 的逐种子配对效应和胜率",
            "- `replication_patient_summary.csv`：所有重复实验的逐患者结果",
            "- `observable_*.csv`：策略允许看到的医院与患者输入",
            "- `ground_truth_*.csv`：代表性种子的隐藏现实，仅供仿真执行与事后审计",
            f"- `representative_patient_trajectories.csv` / `representative_patient_paths.jsonl`：第一个种子的 {len(config.policies)} 策略完整患者轨迹",
            "- `experiment_manifest.json`：种子、Ground Truth 指纹、策略和统计方法",
            "",
            f"总实验墙钟时间：{experiment.elapsed_seconds:.1f} 秒。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _effect_sentence(
    label: str,
    item: PairedComparison,
    *,
    percent_points: bool = False,
) -> str:
    scale = 100 if percent_points else 1
    unit = " 个百分点" if percent_points else " 分钟"
    stability = "95% CI 不跨 0" if item.ci95_low * item.ci95_high > 0 else "95% CI 跨 0"
    return (
        f"- {label}：{item.mean_paired_effect * scale:+.2f}{unit}，"
        f"95% CI [{item.ci95_low * scale:+.2f}, {item.ci95_high * scale:+.2f}]，"
        f"{stability}。"
    )


def _agg(item: AggregateMetric, *, percent: bool = False) -> str:
    scale = 100 if percent else 1
    suffix = "%" if percent else ""
    return (
        f"{item.mean * scale:.1f}{suffix} "
        f"[{item.ci95_low * scale:.1f}, {item.ci95_high * scale:.1f}]"
    )


def _paired(item: PairedComparison, *, percent_points: bool = False) -> str:
    scale = 100 if percent_points else 1
    return (
        f"{item.mean_paired_effect * scale:+.2f} "
        f"[{item.ci95_low * scale:+.2f}, {item.ci95_high * scale:+.2f}]"
    )


def _write_rows(path: Path, rows: Iterable[Mapping[str, object]]) -> Path:
    materialized = list(rows)
    if not materialized:
        path.write_text("", encoding="utf-8")
        return path
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(materialized[0].keys()))
        writer.writeheader()
        for row in materialized:
            writer.writerow({key: _cell(value) for key, value in row.items()})
    return path


def _windows(windows: Sequence[Any]) -> str:
    return ";".join(f"{item.start:%H:%M}-{item.end:%H:%M}" for item in windows)


def _cell(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, (tuple, list)):
        return ",".join(str(item) for item in value)
    if value is None:
        return ""
    return value


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"无法序列化 {type(value).__name__}")
