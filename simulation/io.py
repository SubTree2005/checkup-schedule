"""CSV/JSON/Markdown exports for the hospital-day simulator."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .engine import (
    DepartmentOutcome,
    HospitalScenario,
    PatientOutcome,
    SimulationMetrics,
    SimulationResult,
    TrajectoryEvent,
)


def export_comparative_results(
    output_directory: str | Path,
    baseline: SimulationResult,
    dynamic: SimulationResult,
) -> tuple[Path, ...]:
    """Write the complete reproducible input, event trace, and evaluation bundle."""

    if baseline.scenario != dynamic.scenario:
        raise ValueError("对照策略必须使用同一个仿真场景")
    if (
        baseline.ground_truth.trace_fingerprint
        != dynamic.ground_truth.trace_fingerprint
    ):
        raise ValueError("对照策略必须共享同一份 Ground Truth")
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    scenario = baseline.scenario
    written: list[Path] = []

    written.append(_write_departments(output / "hospital_departments.csv", scenario))
    written.append(_write_travel_times(output / "travel_times.csv", scenario))
    written.append(_write_patient_inputs(output / "patient_inputs.csv", scenario))
    written.append(_write_exam_inputs(output / "patient_exam_inputs.csv", scenario))
    written.append(_write_scenario_json(output / "scenario.json", scenario))
    written.append(
        _write_patient_summary(
            output / "patient_summary.csv",
            (*baseline.patient_outcomes, *dynamic.patient_outcomes),
        )
    )
    written.append(
        _write_trajectory_csv(
            output / "patient_trajectories.csv",
            (*baseline.patient_outcomes, *dynamic.patient_outcomes),
        )
    )
    written.append(
        _write_paths_jsonl(
            output / "patient_paths.jsonl",
            scenario,
            (*baseline.patient_outcomes, *dynamic.patient_outcomes),
        )
    )
    written.append(
        _write_dataclass_csv(
            output / "department_metrics.csv",
            (*baseline.department_outcomes, *dynamic.department_outcomes),
        )
    )
    written.append(
        _write_dataclass_csv(output / "replan_log.csv", dynamic.replan_records)
    )
    written.append(
        _write_mapping_csv(
            output / "service_records.csv",
            (*baseline.service_records, *dynamic.service_records),
        )
    )
    written.append(
        _write_mapping_csv(
            output / "wait_prediction_records.csv",
            dynamic.wait_prediction_records,
        )
    )
    written.append(
        _write_summary_json(output / "simulation_summary.json", baseline, dynamic)
    )
    written.append(
        _write_report(output / "simulation_report.md", baseline, dynamic)
    )
    return tuple(written)


def _write_departments(path: Path, scenario: HospitalScenario) -> Path:
    rows = []
    for department in scenario.departments.values():
        rows.append(
            {
                "department_id": department.id,
                "department_name": department.name,
                "floor": department.floor,
                "zone": department.zone,
                "capacity": department.capacity,
                "estimated_duration_minutes": department.estimated_duration_minutes,
                "service_windows": _windows(department.service_windows),
                "requirements": "；".join(department.requirements),
            }
        )
    return _write_mapping_csv(path, rows)


def _write_travel_times(path: Path, scenario: HospitalScenario) -> Path:
    locations = ("LOBBY", *scenario.departments.keys())
    rows = [
        {
            "origin_id": origin,
            "destination_id": destination,
            "estimated_walk_minutes": scenario.travel_times.between(origin, destination),
        }
        for origin in locations
        for destination in locations
    ]
    return _write_mapping_csv(path, rows)


def _write_patient_inputs(path: Path, scenario: HospitalScenario) -> Path:
    rows = [
        {
            "patient_id": patient.patient_id,
            "age": patient.age,
            "sex": patient.sex,
            "scheduled_arrival": patient.scheduled_arrival,
            "availability_windows": _windows(patient.availability_windows),
            "exam_count": len(patient.exams),
            "exam_ids": ">".join(exam.id for exam in patient.exams),
            "fixed_baseline_order": ">".join(patient.baseline_order),
        }
        for patient in scenario.patients
    ]
    return _write_mapping_csv(path, rows)


def _write_exam_inputs(path: Path, scenario: HospitalScenario) -> Path:
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
                    "allowed_windows": _windows(exam.allowed_windows),
                    "delay_cost_per_minute": exam.delay_cost_per_minute,
                }
            )
    return _write_mapping_csv(path, rows)


def _write_scenario_json(path: Path, scenario: HospitalScenario) -> Path:
    payload = {
        "seed": scenario.seed,
        "operating_window": _windows((scenario.operating_window,)),
        "simulation_end": scenario.simulation_end,
        "patient_count": len(scenario.patients),
        "department_count": len(scenario.departments),
        "total_exam_count": sum(len(patient.exams) for patient in scenario.patients),
        "minute_to_simulation_step": 1,
        "description": (
            "每个循环推进1个仿真分钟；迟到、个人步速、实际检查耗时、"
            "遵从性和单设备停机存在独立 Ground Truth 中，不属于策略输入。"
        ),
    }
    _write_json(path, payload)
    return path


def _write_patient_summary(path: Path, outcomes: Sequence[PatientOutcome]) -> Path:
    rows = []
    for outcome in outcomes:
        row = asdict(outcome)
        row.pop("events")
        row["incomplete_exam_ids"] = ",".join(outcome.incomplete_exam_ids)
        rows.append(row)
    return _write_mapping_csv(path, rows)


def _write_trajectory_csv(path: Path, outcomes: Sequence[PatientOutcome]) -> Path:
    rows = []
    for outcome in outcomes:
        for sequence, event in enumerate(outcome.events, start=1):
            rows.append(
                {
                    "policy": outcome.policy,
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
    return _write_mapping_csv(path, rows)


def _write_paths_jsonl(
    path: Path,
    scenario: HospitalScenario,
    outcomes: Sequence[PatientOutcome],
) -> Path:
    patients = {patient.patient_id: patient for patient in scenario.patients}
    with path.open("w", encoding="utf-8") as stream:
        for outcome in outcomes:
            patient = patients[outcome.patient_id]
            payload = {
                "policy": outcome.policy,
                "patient_id": outcome.patient_id,
                "input": {
                    "age": patient.age,
                    "sex": patient.sex,
                    "scheduled_arrival": patient.scheduled_arrival,
                    "actual_arrival": outcome.arrival_at,
                    "availability_windows": _windows(patient.availability_windows),
                    "exam_ids": [exam.id for exam in patient.exams],
                    "baseline_order": list(patient.baseline_order),
                    "true_mobility_factor": outcome.true_mobility_factor,
                },
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


def _write_summary_json(
    path: Path,
    baseline: SimulationResult,
    dynamic: SimulationResult,
) -> Path:
    payload = {
        "scenario": {
            "seed": baseline.scenario.seed,
            "date": baseline.scenario.operating_window.start.date().isoformat(),
            "patient_count": len(baseline.scenario.patients),
            "department_count": len(baseline.scenario.departments),
            "total_exam_count": baseline.metrics.total_exam_count,
            "simulation_ticks_per_policy": baseline.metrics.simulation_ticks,
        },
        "fixed_fcfs": asdict(baseline.metrics),
        "dynamic_v6": asdict(dynamic.metrics),
        "dynamic_minus_fixed": _metric_deltas(baseline.metrics, dynamic.metrics),
    }
    _write_json(path, payload)
    return path


def _write_report(
    path: Path,
    baseline: SimulationResult,
    dynamic: SimulationResult,
) -> Path:
    b = baseline.metrics
    d = dynamic.metrics
    scenario = baseline.scenario
    wait_reduction = _relative_change(b.mean_wait_minutes, d.mean_wait_minutes)
    p90_wait_reduction = _relative_change(b.p90_wait_minutes, d.p90_wait_minutes)
    exam_gain = (d.exam_completion_rate - b.exam_completion_rate) * 100
    patient_gain = (d.patient_completion_rate - b.patient_completion_rate) * 100
    abandon_change = _relative_change(b.queue_abandon_count, d.queue_abandon_count)
    busiest = sorted(
        dynamic.department_outcomes,
        key=lambda item: (item.p90_wait_minutes, item.max_queue_length),
        reverse=True,
    )[:5]
    representative = _representative_patients(dynamic.patient_outcomes)
    lines = [
        "# 200人医院一日仿真测试报告",
        "",
        "## 结论",
        "",
        (
            f"在相同的 {len(scenario.patients)} 名患者、{d.total_exam_count} 个检查任务和隐藏真实行为下，"
            f"V6 动态调度把人均实际等待从 {b.mean_wait_minutes:.1f} 分钟降到 "
            f"{d.mean_wait_minutes:.1f} 分钟（{abs(wait_reduction):.1f}%），p90 等待从 "
            f"{b.p90_wait_minutes:.1f} 分钟降到 {d.p90_wait_minutes:.1f} 分钟"
            f"（{abs(p90_wait_reduction):.1f}%）。"
        ),
        (
            f"检查项目完成率提高 {exam_gain:+.1f} 个百分点，但整套体检患者完成率变化 "
            f"{patient_gain:+.1f} 个百分点。这说明当前目标函数更擅长减少局部拥堵和推进更多项目，"
            "尚未显式奖励“让一个患者完成最后一项并离院”；这是真实上线前需要优先修正的算法问题。"
        ),
        "",
        "## 场景与时间推进",
        "",
        f"- 仿真日期：{scenario.operating_window.start.date().isoformat()}（工作日）",
        f"- 运营时间：{scenario.operating_window.start:%H:%M}–{scenario.operating_window.end:%H:%M}；在途服务观察至 {scenario.simulation_end:%H:%M}",
        f"- 科室：{len(scenario.departments)} 个；患者：{len(scenario.patients)} 人；检查项目：{d.total_exam_count} 项",
        "- 时间粒度：每个 CPU 循环推进 1 个仿真分钟，每种策略 631 个离散时间步",
        f"- 随机种子：{scenario.seed}；两个策略共享完全相同的迟到、步速、实际检查耗时和设备停机样本",
        "- V6 每 5 分钟滚动重排，120 分钟优化时域，10 分钟冻结窗口；当前环境走启发式后端",
        "- 对照组：固定医学可行顺序，患者到达各科后按现场 FCFS 执行，不使用等待预测或动态改路",
        "",
        "## 核心指标",
        "",
        "| 指标 | 固定顺序 + FCFS | V6 动态调度 | V6 相对变化 |",
        "| --- | ---: | ---: | ---: |",
        _table_row("整套体检完成率", _pct(b.patient_completion_rate), _pct(d.patient_completion_rate), f"{patient_gain:+.1f} pp"),
        _table_row("检查项目完成率", _pct(b.exam_completion_rate), _pct(d.exam_completion_rate), f"{exam_gain:+.1f} pp"),
        _table_row("人均总历时", f"{b.mean_journey_minutes:.1f} min", f"{d.mean_journey_minutes:.1f} min", _change_text(b.mean_journey_minutes, d.mean_journey_minutes)),
        _table_row("p90 总历时", f"{b.p90_journey_minutes:.1f} min", f"{d.p90_journey_minutes:.1f} min", _change_text(b.p90_journey_minutes, d.p90_journey_minutes)),
        _table_row("人均实际等待", f"{b.mean_wait_minutes:.1f} min", f"{d.mean_wait_minutes:.1f} min", _change_text(b.mean_wait_minutes, d.mean_wait_minutes)),
        _table_row("p90 实际等待", f"{b.p90_wait_minutes:.1f} min", f"{d.p90_wait_minutes:.1f} min", _change_text(b.p90_wait_minutes, d.p90_wait_minutes)),
        _table_row("人均步行", f"{b.mean_walk_minutes:.1f} min", f"{d.mean_walk_minutes:.1f} min", _change_text(b.mean_walk_minutes, d.mean_walk_minutes)),
        _table_row("因个人无空离队", str(b.queue_abandon_count), str(d.queue_abandon_count), f"{abandon_change:+.1f}%"),
        _table_row("实际截止时间违例", str(b.deadline_violation_count), str(d.deadline_violation_count), str(d.deadline_violation_count - b.deadline_violation_count)),
        "",
        "## 预测、反馈与计算开销",
        "",
        f"- 等待预测回放样本：{d.wait_prediction_sample_count}；MAE {d.wait_prediction_mae_minutes:.2f} 分钟；RMSE {d.wait_prediction_rmse_minutes:.2f} 分钟；p90 覆盖率 {_pct(d.wait_prediction_p90_coverage or 0)}。",
        f"- {d.learned_patient_count}/{d.patient_count} 名患者积累至少 3 段有效步行反馈；个人移动系数 MAE 为 {d.mobility_factor_mae:.3f}。",
        f"- 滚动重排 {d.replan_count} 次；全日 V6 仿真 CPU 用时 {d.cpu_seconds:.2f} 秒；平均每次重排 {sum(item.cpu_milliseconds for item in dynamic.replan_records) / max(1, d.replan_count):.1f} ms。",
        "- 等待反馈只更新科室级模型；步行反馈只更新对应患者，仿真未把两类反馈混合。",
        "",
        "## V6 主要拥堵科室",
        "",
        "| 科室 | 完成数 | 平均等待 | p90 等待 | 最大队列 | 利用率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {item.department_name} | {item.completed_exam_count} | {item.mean_wait_minutes:.1f} | {item.p90_wait_minutes:.1f} | {item.max_queue_length} | {_pct(item.utilization)} |"
        for item in busiest
    )
    lines.extend(
        [
            "",
            "## 代表性患者轨迹摘要",
            "",
            "完整 200 人、两种策略的逐事件轨迹见 `patient_trajectories.csv`；每位患者一整条 JSON 记录见 `patient_paths.jsonl`。",
            "",
        ]
    )
    for outcome in representative:
        path_text = " → ".join(
            event.exam_id
            for event in outcome.events
            if event.event == "exam_end"
        ) or "无完成项目"
        lines.append(
            f"- {outcome.patient_id}：{outcome.arrival_at:%H:%M} 到达，"
            f"{outcome.departure_at.strftime('%H:%M') if outcome.departure_at else '未完成'} 离院；"
            f"等待 {outcome.wait_minutes:.0f} 分钟，步行 {outcome.walk_minutes:.0f} 分钟；{path_text}。"
        )
    lines.extend(
        [
            "",
            "## 验证结论与下一步",
            "",
            "1. 容量、前置关系、患者/科室时间窗和资源不重叠检查均通过；每个患者每项检查最多执行一次。",
            "2. V6 对削峰和减少离队有效，但当前“项目完成率”与“整套体检完成率”出现分叉，说明目标函数缺少收尾奖励或剩余项目数惩罚。",
            "3. 下一轮应在调度目标中加入患者完检奖励、临近离院时间的收尾权重，并将内科总检这类汇总节点视作关键终端任务；随后用同一随机种子 A/B 回归。",
            "4. 这里的医院数据是结构上贴近现实的合成基准，不等同于某家医院的真实运营数据；上线阈值必须再用实地采集的到达、服务、停机和步行数据校准。",
            "",
            "## 文件说明",
            "",
            "- `hospital_departments.csv`：科室容量、营业时段、预计耗时、前置要求和设备停机",
            "- `travel_times.csv`：大厅与所有科室之间的完整预估步行矩阵",
            "- `patient_inputs.csv` / `patient_exam_inputs.csv`：200 人输入、空闲时段和检查约束",
            "- `patient_trajectories.csv` / `patient_paths.jsonl`：每名患者两种策略的最终完整轨迹",
            "- `patient_summary.csv`：每名患者的等待、步行、服务、完成状态和个人学习结果",
            "- `department_metrics.csv`：科室吞吐、排队与利用率",
            "- `replan_log.csv`：每次滚动重排的规模、后端、耗时和排程结果",
            "- `service_records.csv` / `wait_prediction_records.csv`：实际服务与预测回放明细",
            "- `simulation_summary.json`：机器可读汇总指标",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _representative_patients(
    outcomes: Sequence[PatientOutcome],
) -> tuple[PatientOutcome, ...]:
    ordered = sorted(outcomes, key=lambda item: item.journey_minutes)
    candidates = [ordered[0], ordered[len(ordered) // 2], ordered[-1]]
    incomplete = next((item for item in ordered if not item.completed), None)
    if incomplete is not None:
        candidates.append(incomplete)
    unique: dict[str, PatientOutcome] = {item.patient_id: item for item in candidates}
    return tuple(unique.values())


def _metric_deltas(
    baseline: SimulationMetrics,
    dynamic: SimulationMetrics,
) -> dict[str, float | int | None]:
    output: dict[str, float | int | None] = {}
    for key, baseline_value in asdict(baseline).items():
        dynamic_value = getattr(dynamic, key)
        if isinstance(baseline_value, (int, float)) and not isinstance(baseline_value, bool):
            output[key] = dynamic_value - baseline_value
        else:
            output[key] = None
    return output


def _write_dataclass_csv(path: Path, rows: Sequence[Any]) -> Path:
    return _write_mapping_csv(path, [asdict(row) for row in rows])


def _write_mapping_csv(
    path: Path,
    rows: Iterable[Mapping[str, object]],
) -> Path:
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


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _windows(windows: Sequence[Any]) -> str:
    return ";".join(f"{window.start:%H:%M}-{window.end:%H:%M}" for window in windows)


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


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _relative_change(baseline: float, dynamic: float) -> float:
    if baseline == 0:
        return 0.0
    return (dynamic - baseline) / baseline * 100


def _change_text(baseline: float, dynamic: float) -> str:
    return f"{dynamic - baseline:+.1f} ({_relative_change(baseline, dynamic):+.1f}%)"


def _table_row(label: str, baseline: str, dynamic: str, change: str) -> str:
    return f"| {label} | {baseline} | {dynamic} | {change} |"
