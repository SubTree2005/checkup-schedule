"""Deterministic minute-resolution hospital-day simulation for scheduler replay.

The simulator deliberately keeps the physical world separate from the scheduling
model.  The scheduler sees declared availability, estimated durations, queue
snapshots, and prediction contracts; the simulator owns hidden walking speeds,
late arrivals, service-time noise, lunch/absence behavior, and equipment outages.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from math import ceil
from random import Random
from statistics import mean, median
from time import perf_counter
from typing import Iterable, Mapping, Sequence

from checkup_scheduler.activity_prediction import (
    AdaptivePersonalActivityPredictor,
    PersonalActivityFeedback,
    PersonalActivityFeedbackController,
)
from checkup_scheduler.batch import BatchPlanStep, BatchPlannerConfig, build_batch_schedule
from checkup_scheduler.feedback import (
    GlobalWaitFeedbackController,
    RobustFeedbackConfig,
    WaitTimingFeedback,
)
from checkup_scheduler.hybrid import HybridPlannerConfig
from checkup_scheduler.models import DepartmentState, Exam, PatientState, TimeWindow, TravelTimeMatrix
from checkup_scheduler.rolling import RollingHorizonConfig, RollingHorizonScheduler
from .ground_truth import (
    SimulationGroundTruth,
    validate_ground_truth,
)
from checkup_scheduler.wait_prediction import (
    AdaptiveQueuePredictor,
    QueueSnapshot,
    WaitPrediction,
    evaluate_wait_predictions,
)


SIMULATION_DATE = date(2026, 8, 24)
LOBBY_ID = "LOBBY"
DEFAULT_SIMULATION_POLICIES = (
    "fixed_fcfs",
    "shortest_queue",
    "static_batch",
    "rolling_no_feedback",
    "dynamic_v6",
)
FEEDBACK_ABLATION_POLICIES = (
    "no_feedback",
    "wait_feedback_only",
    "personal_activity_feedback_only",
    "dual_feedback",
)
V9_COMPARISON_POLICIES = (
    "rolling_no_feedback",
    "dynamic_v6",
    *FEEDBACK_ABLATION_POLICIES,
    "feedback_cp_sat",
)
V10_COMPARISON_POLICIES = (
    "no_feedback",
    "v10_no_feedback",
    "v10_wait_feedback_only",
    "v10_personal_activity_feedback_only",
    "v10_dual_feedback",
)


@dataclass(frozen=True, slots=True)
class SimulationPolicyConfig:
    rolling: bool
    critical_path: bool
    wait_feedback: bool
    personal_activity_feedback: bool
    optimizer_strategy: str = "heuristic"
    wait_oriented: bool = False
    minimum_replan_improvement_minutes: float = 0.0


_ROLLING_POLICIES: dict[str, SimulationPolicyConfig] = {
    # Frozen V8 comparators.
    "rolling_no_feedback": SimulationPolicyConfig(True, False, False, False),
    "dynamic_v6": SimulationPolicyConfig(True, False, True, True),
    # Formal V9 2x2 feedback ablation.
    "no_feedback": SimulationPolicyConfig(True, True, False, False),
    "wait_feedback_only": SimulationPolicyConfig(True, True, True, False),
    "personal_activity_feedback_only": SimulationPolicyConfig(True, True, False, True),
    "dual_feedback": SimulationPolicyConfig(True, True, True, True),
    # Required public aliases and CP-SAT rolling-horizon variants.
    "rolling_heuristic": SimulationPolicyConfig(True, True, False, False),
    "rolling_cp_sat": SimulationPolicyConfig(True, True, False, False, "cp_sat"),
    "feedback_heuristic": SimulationPolicyConfig(True, True, True, True),
    "feedback_cp_sat": SimulationPolicyConfig(True, True, True, True, "cp_sat"),
    # V10 keeps V9 constraints but makes waiting and starvation the primary objective.
    "v10_no_feedback": SimulationPolicyConfig(
        True, True, False, False, wait_oriented=True,
        minimum_replan_improvement_minutes=2.0,
    ),
    "v10_wait_feedback_only": SimulationPolicyConfig(
        True, True, True, False, wait_oriented=True,
        minimum_replan_improvement_minutes=2.0,
    ),
    "v10_personal_activity_feedback_only": SimulationPolicyConfig(
        True, True, False, True, wait_oriented=True,
        minimum_replan_improvement_minutes=2.0,
    ),
    "v10_dual_feedback": SimulationPolicyConfig(
        True, True, True, True, wait_oriented=True,
        minimum_replan_improvement_minutes=2.0,
    ),
}
SUPPORTED_SIMULATION_POLICIES = (
    "fixed_fcfs",
    "shortest_queue",
    "static_batch",
    *_ROLLING_POLICIES,
)
SIMULATION_SCENARIOS = (
    "normal_day",
    "morning_peak",
    "terminal_bottleneck",
    "late_arrival",
    "device_breakdown",
    "service_slowdown",
    "predictor_bias",
    "patient_interruption",
)


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(SIMULATION_DATE, datetime.min.time()).replace(
        hour=hour,
        minute=minute,
    )


@dataclass(frozen=True, slots=True)
class HospitalDepartmentSpec:
    id: str
    name: str
    floor: int
    zone: str
    capacity: int
    estimated_duration_minutes: int
    service_windows: tuple[TimeWindow, ...]
    requirements: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SyntheticPatientInput:
    patient_id: str
    age: int
    sex: str
    scheduled_arrival: datetime
    availability_windows: tuple[TimeWindow, ...]
    exams: tuple[Exam, ...]
    baseline_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HospitalScenario:
    seed: int
    operating_window: TimeWindow
    simulation_end: datetime
    departments: Mapping[str, HospitalDepartmentSpec]
    travel_times: TravelTimeMatrix
    patients: tuple[SyntheticPatientInput, ...]


@dataclass(frozen=True, slots=True)
class TrajectoryEvent:
    at: datetime
    event: str
    location_id: str
    exam_id: str = ""
    department_id: str = ""
    details: str = ""


@dataclass(frozen=True, slots=True)
class PatientOutcome:
    policy: str
    patient_id: str
    completed: bool
    completed_exam_count: int
    total_exam_count: int
    arrival_at: datetime
    departure_at: datetime | None
    journey_minutes: float
    wait_minutes: float
    walk_minutes: float
    service_minutes: float
    unavailable_minutes: float
    route_change_count: int
    queue_abandon_count: int
    deadline_violation_count: int
    final_status: str
    incomplete_exam_ids: tuple[str, ...]
    learned_mobility_factor: float | None
    true_mobility_factor: float
    events: tuple[TrajectoryEvent, ...]
    unfinished_exam_count: int = 0
    terminal_exam_count: int = 0
    completed_terminal_exam_count: int = 0
    critical_path_missed: bool = False
    high_completion_risk_seen: bool = False
    replan_count: int = 0
    route_change_notification_count: int = 0
    idle_between_exams_minutes: float = 0.0
    max_continuous_wait_minutes: float = 0.0
    suppressed_route_change_count: int = 0


@dataclass(frozen=True, slots=True)
class DepartmentOutcome:
    policy: str
    department_id: str
    department_name: str
    completed_exam_count: int
    mean_wait_minutes: float
    p90_wait_minutes: float
    max_queue_length: int
    busy_resource_minutes: float
    nominal_capacity_minutes: float
    utilization: float
    mean_queue_length: float = 0.0
    p90_queue_length: float = 0.0


@dataclass(frozen=True, slots=True)
class ReplanRecord:
    at: datetime
    active_patient_count: int
    scheduled_exam_count: int
    unscheduled_exam_count: int
    backend: str
    optimizer_status: str
    cpu_milliseconds: float
    predicted_total_wait_minutes: float
    cp_sat_invoked: bool = False
    solve_seconds: float = 0.0
    objective_improvement: float = 0.0
    completion_risk_improvement: float = 0.0


@dataclass(frozen=True, slots=True)
class SimulationMetrics:
    policy: str
    patient_count: int
    completed_patient_count: int
    patient_completion_rate: float
    completed_exam_count: int
    total_exam_count: int
    exam_completion_rate: float
    mean_journey_minutes: float
    median_journey_minutes: float
    p90_journey_minutes: float
    p95_journey_minutes: float
    mean_wait_minutes: float
    p90_wait_minutes: float
    p95_wait_minutes: float
    mean_walk_minutes: float
    p90_walk_minutes: float
    wait_p90_p10_gap_minutes: float
    deadline_violation_count: int
    queue_abandon_count: int
    route_change_count: int
    replan_count: int
    simulation_ticks: int
    cpu_seconds: float
    wait_prediction_sample_count: int
    wait_prediction_mae_minutes: float | None
    wait_prediction_rmse_minutes: float | None
    wait_prediction_bias_minutes: float | None
    wait_prediction_p90_coverage: float | None
    learned_patient_count: int
    mobility_factor_mae: float | None
    full_completion_rate: float = 0.0
    unfinished_exam_count: int = 0
    deadline_miss_count: int = 0
    terminal_exam_completion_rate: float = 0.0
    critical_path_miss_count: int = 0
    patients_at_high_completion_risk: int = 0
    mean_replans_per_patient: float = 0.0
    route_change_notifications_per_patient: float = 0.0
    patients_missing_one_exam: int = 0
    missing_exam_counts: Mapping[str, int] = field(default_factory=dict)
    cp_sat_invocation_count: int = 0
    cp_sat_optimal_count: int = 0
    cp_sat_feasible_count: int = 0
    cp_sat_timeout_count: int = 0
    cp_sat_fallback_count: int = 0
    cp_sat_mean_solve_seconds: float = 0.0
    cp_sat_p90_solve_seconds: float = 0.0
    cp_sat_p95_solve_seconds: float = 0.0
    cp_sat_objective_improvement: float = 0.0
    cp_sat_completion_risk_improvement: float = 0.0
    median_wait_minutes: float = 0.0
    p99_wait_minutes: float = 0.0
    max_wait_minutes: float = 0.0
    patients_waiting_over_60m: int = 0
    patients_waiting_over_90m: int = 0
    patients_waiting_over_120m: int = 0
    max_continuous_wait_minutes: float = 0.0
    mean_idle_between_exams_minutes: float = 0.0
    suppressed_route_change_count: int = 0
    mean_department_utilization: float = 0.0
    p90_department_queue_length: float = 0.0
    peak_department_queue_length: int = 0
    throughput_exams_per_hour: float = 0.0


@dataclass(frozen=True, slots=True)
class SimulationResult:
    scenario: HospitalScenario
    ground_truth: SimulationGroundTruth
    policy: str
    metrics: SimulationMetrics
    patient_outcomes: tuple[PatientOutcome, ...]
    department_outcomes: tuple[DepartmentOutcome, ...]
    replan_records: tuple[ReplanRecord, ...]
    service_records: tuple[Mapping[str, object], ...]
    wait_prediction_records: tuple[Mapping[str, object], ...]


@dataclass(slots=True)
class _PatientRuntime:
    spec: SyntheticPatientInput
    status: str = "not_arrived"
    location_id: str = LOBBY_ID
    completed: set[str] = field(default_factory=set)
    blocked: set[str] = field(default_factory=set)
    current_exam_id: str | None = None
    planned_order: tuple[str, ...] = ()
    planned_steps: dict[str, BatchPlanStep] = field(default_factory=dict)
    walk_origin_id: str | None = None
    walk_destination_id: str | None = None
    walk_started_at: datetime | None = None
    walk_finishes_at: datetime | None = None
    queue_joined_at: datetime | None = None
    queue_prediction: WaitPrediction | None = None
    service_started_at: datetime | None = None
    service_finishes_at: datetime | None = None
    departure_at: datetime | None = None
    ready_since_at: datetime | None = None
    wait_minutes: float = 0.0
    walk_minutes: float = 0.0
    service_minutes: float = 0.0
    unavailable_minutes: float = 0.0
    route_change_count: int = 0
    queue_abandon_count: int = 0
    deadline_violation_count: int = 0
    last_available: bool | None = None
    events: list[TrajectoryEvent] = field(default_factory=list)
    high_completion_risk_seen: bool = False
    replan_count: int = 0
    last_completion_at: datetime | None = None
    idle_between_exams_minutes: float = 0.0
    max_continuous_wait_minutes: float = 0.0
    suppressed_route_change_count: int = 0


@dataclass(slots=True)
class _ServiceSlot:
    patient_id: str
    exam_id: str
    started_at: datetime
    finishes_at: datetime
    actual_duration_minutes: int


@dataclass(slots=True)
class _DepartmentRuntime:
    spec: HospitalDepartmentSpec
    queue: list[str] = field(default_factory=list)
    resources: list[_ServiceSlot | None] = field(default_factory=list)
    recent_service_minutes: list[float] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)
    completed_exam_count: int = 0
    busy_resource_minutes: float = 0.0
    max_queue_length: int = 0
    queue_lengths: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.resources:
            self.resources = [None] * self.spec.capacity


def build_realistic_hospital_scenario(
    patient_count: int = 200,
    *,
    seed: int = 20260824,
    scenario_name: str = "normal_day",
) -> HospitalScenario:
    """Create reproducible policy-visible hospital and patient inputs only."""

    if patient_count <= 0:
        raise ValueError("仿真患者数必须为正数")
    if scenario_name not in SIMULATION_SCENARIOS:
        raise ValueError(f"未知仿真场景: {scenario_name}")
    operating_window = TimeWindow(_at(7, 30), _at(17, 0))
    departments = _build_departments(scenario_name)
    travel_times = _build_travel_matrix(departments)
    rng = Random(seed)
    patients = tuple(
        _build_patient(index + 1, departments, rng, scenario_name)
        for index in range(patient_count)
    )
    scenario = HospitalScenario(
        seed=seed,
        operating_window=operating_window,
        simulation_end=_at(18, 0),
        departments=departments,
        travel_times=travel_times,
        patients=patients,
    )
    validate_scenario(scenario)
    return scenario


def _build_departments(
    scenario_name: str = "normal_day",
) -> dict[str, HospitalDepartmentSpec]:
    morning_early = (TimeWindow(_at(7, 30), _at(11, 0)),)
    standard = (TimeWindow(_at(8), _at(12)), TimeWindow(_at(13), _at(16, 30)))
    shorter = (TimeWindow(_at(8), _at(11, 45)), TimeWindow(_at(13), _at(16, 15)))
    return {
        "BLOOD": HospitalDepartmentSpec(
            "BLOOD", "采血中心", 1, "A", 5, 5,
            morning_early + (TimeWindow(_at(13), _at(16)),),
            ("空腹项目优先在11:00前完成", "采血后按压3–5分钟"),
        ),
        "URINE": HospitalDepartmentSpec(
            "URINE", "尿液检验", 1, "A", 3, 4,
            (TimeWindow(_at(7, 30), _at(11, 30)), TimeWindow(_at(13), _at(16))),
            ("留取中段尿",),
        ),
        "VITALS": HospitalDepartmentSpec(
            "VITALS", "一般测量", 1, "B", 4, 6,
            (TimeWindow(_at(7, 30), _at(12)), TimeWindow(_at(13), _at(16, 30))),
            ("测量前静坐3分钟",),
        ),
        "ECG": HospitalDepartmentSpec(
            "ECG", "心电图室", 2, "B", 3, 9, standard,
            ("检查前避免剧烈运动",),
        ),
        "ULTRASOUND": HospitalDepartmentSpec(
            "ULTRASOUND", "腹部超声", 2, "D", 4, 13, shorter,
            ("上腹部项目需空腹",),
        ),
        "XRAY": HospitalDepartmentSpec(
            "XRAY", "数字X线摄影", 1, "C", 2, 8, standard,
            ("去除检查部位金属物",),
        ),
        "CT": HospitalDepartmentSpec(
            "CT", "CT检查", 0, "C", 2, 14, standard,
            ("增强CT需先完成肾功能采血",),
        ),
        "EYE": HospitalDepartmentSpec(
            "EYE", "眼科检查", 3, "A", 2, 10, shorter,
            ("佩戴隐形眼镜者需提前摘除",),
        ),
        "ENT": HospitalDepartmentSpec(
            "ENT", "耳鼻喉科", 3, "B", 2, 9, shorter,
            ("依次完成耳、鼻、咽喉检查",),
        ),
        "LUNG": HospitalDepartmentSpec(
            "LUNG", "肺功能室", 2, "C", 2, 9,
            (TimeWindow(_at(8), _at(12)), TimeWindow(_at(13), _at(16))),
            ("检查前30分钟避免吸烟和剧烈运动",),
        ),
        "WOMEN": HospitalDepartmentSpec(
            "WOMEN", "妇科检查", 3, "D", 2, 12, shorter,
            ("部分超声项目前需适度充盈膀胱",),
        ),
        "INTERNAL": HospitalDepartmentSpec(
            "INTERNAL", "内科总检", 2, "A", 5, 12,
            (
                TimeWindow(_at(8), _at(12)),
                TimeWindow(
                    _at(13),
                    _at(16) if scenario_name == "terminal_bottleneck" else _at(16, 45),
                ),
            ),
            ("主要检查结果齐全后进行总检",),
        ),
    }


def _build_travel_matrix(
    departments: Mapping[str, HospitalDepartmentSpec],
) -> TravelTimeMatrix:
    coordinates: dict[str, tuple[int, int, int]] = {
        LOBBY_ID: (0, 0, 1),
        "BLOOD": (1, 0, 1),
        "URINE": (2, 0, 1),
        "VITALS": (0, 1, 1),
        "ECG": (1, 1, 2),
        "ULTRASOUND": (3, 1, 2),
        "XRAY": (0, 2, 1),
        "CT": (2, 2, 0),
        "EYE": (1, 2, 3),
        "ENT": (2, 2, 3),
        "LUNG": (3, 2, 2),
        "WOMEN": (3, 3, 3),
        "INTERNAL": (0, 3, 2),
    }
    edges: dict[tuple[str, str], int] = {}
    locations = (LOBBY_ID, *departments.keys())
    for origin in locations:
        ox, oy, ofloor = coordinates[origin]
        for destination in locations:
            if origin == destination:
                continue
            dx, dy, dfloor = coordinates[destination]
            horizontal = abs(ox - dx) + abs(oy - dy)
            floor_change = abs(ofloor - dfloor)
            elevator_overhead = 1.0 if floor_change else 0.0
            edges[(origin, destination)] = max(
                1,
                ceil(horizontal * 1.15 + floor_change * 2.1 + elevator_overhead),
            )
    return TravelTimeMatrix(edges, default_minutes=6)


def _build_patient(
    index: int,
    departments: Mapping[str, HospitalDepartmentSpec],
    rng: Random,
    scenario_name: str = "normal_day",
) -> SyntheticPatientInput:
    patient_id = f"P{index:03d}"
    scheduled_arrival = _sample_scheduled_arrival(rng, scenario_name)
    age = round(rng.triangular(18, 82, 48))
    sex = "F" if rng.random() < 0.52 else "M"
    availability = _patient_availability(scheduled_arrival, rng)

    selected = {"VITALS", "INTERNAL"}
    probabilities = {
        "BLOOD": 0.86,
        "URINE": 0.72,
        "ECG": 0.65,
        "ULTRASOUND": 0.48 if scheduled_arrival < _at(10, 30) else 0.08,
        "XRAY": 0.55,
        "CT": 0.20,
        "EYE": 0.34,
        "ENT": 0.35,
        "LUNG": 0.38,
    }
    for department_id, probability in probabilities.items():
        if rng.random() < probability:
            selected.add(department_id)
    if sex == "F" and rng.random() < 0.32:
        selected.update(("WOMEN", "URINE"))
    while len(selected) < 4:
        selected.add(rng.choice(("BLOOD", "URINE", "ECG", "XRAY", "ENT")))
    if len(selected) > 8:
        removable = sorted(selected - {"VITALS", "INTERNAL"})
        rng.shuffle(removable)
        while len(selected) > 8:
            selected.remove(removable.pop())

    exams: list[Exam] = []
    non_final_ids = sorted(selected - {"INTERNAL"})
    for department_id in non_final_ids:
        spec = departments[department_id]
        prerequisites: tuple[str, ...] = ()
        earliest_start = None
        latest_finish = None
        delay_cost = 0.05
        if department_id == "ULTRASOUND":
            latest_finish = _at(11, 45) if scheduled_arrival < _at(10, 30) else None
            delay_cost = 0.45
        elif department_id == "BLOOD":
            delay_cost = 0.35
        elif department_id == "CT" and "BLOOD" in selected:
            prerequisites = ("BLOOD",)
            earliest_start = scheduled_arrival + timedelta(minutes=60)
            delay_cost = 0.20
        elif department_id == "WOMEN" and "URINE" in selected:
            prerequisites = ("URINE",)
            delay_cost = 0.15
        exams.append(
            Exam(
                id=department_id,
                department_id=department_id,
                duration_minutes=spec.estimated_duration_minutes,
                prerequisites=prerequisites,
                earliest_start=earliest_start,
                latest_finish=latest_finish,
                delay_cost_per_minute=delay_cost,
                is_critical=(department_id == "ULTRASOUND"),
            )
        )
    internal = departments["INTERNAL"]
    exams.append(
        Exam(
            id="INTERNAL",
            department_id="INTERNAL",
            duration_minutes=internal.estimated_duration_minutes,
            prerequisites=tuple(non_final_ids),
            delay_cost_per_minute=0.10,
        )
    )
    fixed_rank = {
        value: rank
        for rank, value in enumerate(
            (
                "BLOOD", "URINE", "VITALS", "ECG", "ULTRASOUND", "XRAY",
                "CT", "LUNG", "EYE", "ENT", "WOMEN", "INTERNAL",
            )
        )
    }
    baseline_order = tuple(
        exam.id for exam in sorted(exams, key=lambda item: fixed_rank[item.id])
    )
    return SyntheticPatientInput(
        patient_id=patient_id,
        age=age,
        sex=sex,
        scheduled_arrival=scheduled_arrival,
        availability_windows=availability,
        exams=tuple(exams),
        baseline_order=baseline_order,
    )


def _sample_scheduled_arrival(
    rng: Random,
    scenario_name: str = "normal_day",
) -> datetime:
    periods = ((0, 60), (60, 120), (120, 210), (300, 360), (360, 405))
    weights = {
        "morning_peak": (68, 20, 7, 4, 1),
        "late_arrival": (22, 18, 15, 25, 20),
    }.get(scenario_name, (42, 26, 16, 11, 5))
    start_minute, end_minute = rng.choices(
        periods,
        weights=weights,
    )[0]
    return _at(7, 30) + timedelta(minutes=rng.randrange(start_minute, end_minute))


def _patient_availability(
    scheduled_arrival: datetime,
    rng: Random,
) -> tuple[TimeWindow, ...]:
    end = _at(15, 30) if rng.random() < 0.08 else _at(17)
    breaks: list[tuple[datetime, datetime]] = []
    if scheduled_arrival < _at(11, 20) and rng.random() < 0.55:
        break_start = _at(11, 45) + timedelta(minutes=rng.randint(-10, 10))
        breaks.append((break_start, break_start + timedelta(minutes=rng.randint(30, 55))))
    if rng.random() < 0.10:
        break_start = _at(13, 45) + timedelta(minutes=rng.randint(0, 30))
        breaks.append((break_start, break_start + timedelta(minutes=rng.randint(30, 55))))
    cursor = scheduled_arrival
    windows: list[TimeWindow] = []
    for break_start, break_end in sorted(breaks):
        if break_end <= cursor or break_start >= end:
            continue
        if cursor < break_start:
            windows.append(TimeWindow(cursor, min(break_start, end)))
        cursor = max(cursor, break_end)
    if cursor < end:
        windows.append(TimeWindow(cursor, end))
    if not windows:
        windows.append(TimeWindow(scheduled_arrival, end))
    return tuple(windows)


def validate_scenario(scenario: HospitalScenario) -> None:
    if len({patient.patient_id for patient in scenario.patients}) != len(
        scenario.patients
    ):
        raise ValueError("患者编号必须唯一")
    for patient in scenario.patients:
        exam_ids = {exam.id for exam in patient.exams}
        if len(exam_ids) != len(patient.exams):
            raise ValueError(f"{patient.patient_id} 存在重复检查")
        if set(patient.baseline_order) != exam_ids:
            raise ValueError(f"{patient.patient_id} 的固定顺序未覆盖全部检查")
        for exam in patient.exams:
            if exam.department_id not in scenario.departments:
                raise ValueError(f"未知科室: {exam.department_id}")
            if not set(exam.prerequisites).issubset(exam_ids):
                raise ValueError(f"{patient.patient_id}/{exam.id} 前置项目不存在")


class HospitalDaySimulator:
    """Advance the hospital one simulated minute per loop iteration."""

    def __init__(
        self,
        scenario: HospitalScenario,
        ground_truth: SimulationGroundTruth,
        *,
        policy: str,
        observation_wait_bias_fraction: float = 0.0,
        critical_risk_slack_minutes: float = 180.0,
        minimum_replan_improvement_minutes: float | None = None,
    ) -> None:
        if policy not in SUPPORTED_SIMULATION_POLICIES:
            raise ValueError(f"未知仿真策略: {policy}")
        validate_ground_truth(scenario, ground_truth)
        self.scenario = scenario
        self.ground_truth = ground_truth
        self.policy = policy
        if not -0.9 <= observation_wait_bias_fraction <= 3.0:
            raise ValueError("等待预测观察偏差必须位于 [-0.9, 3.0]")
        self.observation_wait_bias_fraction = observation_wait_bias_fraction
        if critical_risk_slack_minutes <= 0:
            raise ValueError("关键路径高风险阈值必须为正数")
        self.critical_risk_slack_minutes = critical_risk_slack_minutes
        self.policy_config = _ROLLING_POLICIES.get(policy)
        configured_replan_improvement = (
            self.policy_config.minimum_replan_improvement_minutes
            if self.policy_config is not None
            else 0.0
        )
        self.minimum_replan_improvement_minutes = (
            configured_replan_improvement
            if minimum_replan_improvement_minutes is None
            else minimum_replan_improvement_minutes
        )
        if self.minimum_replan_improvement_minutes < 0:
            raise ValueError("最小改线收益不能为负数")
        self.patients = {
            item.patient_id: _PatientRuntime(
                spec=item,
                planned_order=(item.baseline_order if policy == "fixed_fcfs" else ()),
            )
            for item in scenario.patients
        }
        self.departments = {
            item.id: _DepartmentRuntime(item)
            for item in scenario.departments.values()
        }
        self.wait_predictor: AdaptiveQueuePredictor | None = None
        self.wait_feedback: GlobalWaitFeedbackController | None = None
        self.activity_predictor: AdaptivePersonalActivityPredictor | None = None
        self.activity_feedback: PersonalActivityFeedbackController | None = None
        self.scheduler: RollingHorizonScheduler | None = None
        if self.policy_config is not None:
            self.wait_predictor = AdaptiveQueuePredictor(
                default_service_minutes=10,
                smoothing=0.20,
                random_seed=ground_truth.seed,
                max_wait_bias_update_minutes=(
                    1_000_000.0 if policy == "dynamic_v6" else 5.0
                ),
            )
            for department in scenario.departments.values():
                historical = tuple(
                    (
                        department.estimated_duration_minutes * factor,
                        scenario.operating_window.start - timedelta(days=day),
                    )
                    for day, factor in enumerate(
                        (0.92, 1.05, 1.00, 1.12, 0.95, 1.08, 0.98, 1.03),
                        start=1,
                    )
                )
                self.wait_predictor.observe_service_batch(department.id, historical)
            if self.policy_config.wait_feedback:
                self.wait_feedback = GlobalWaitFeedbackController(
                    self.wait_predictor,
                    config=(
                        RobustFeedbackConfig(
                            max_mean_update_minutes=1_000_000.0,
                            max_p90_update_minutes=1_000_000.0,
                            guarded_update_enabled=False,
                        )
                        if policy == "dynamic_v6"
                        else RobustFeedbackConfig()
                    ),
                )
            if self.policy_config.personal_activity_feedback:
                self.activity_predictor = AdaptivePersonalActivityPredictor(
                    max_factor_update=(
                        1_000_000.0 if policy == "dynamic_v6" else 0.25
                    )
                )
                self.activity_feedback = PersonalActivityFeedbackController(
                    self.activity_predictor
                )
            self.scheduler = RollingHorizonScheduler(
                scenario.operating_window,
                scenario.travel_times,
                config=RollingHorizonConfig(
                    optimization_horizon_minutes=120,
                    replan_interval_minutes=5,
                    freeze_window_minutes=10,
                ),
                batch_config=BatchPlannerConfig(
                    critical_path_enabled=self.policy_config.critical_path,
                    critical_path_buffer_minutes=5.0,
                    high_completion_risk_slack_minutes=critical_risk_slack_minutes,
                    wait_oriented=self.policy_config.wait_oriented,
                    fairness_weight=(0.0 if self.policy_config.wait_oriented else 0.20),
                    travel_weight=(0.20 if self.policy_config.wait_oriented else 0.10),
                    previous_order_weight=(3.0 if self.policy_config.wait_oriented else 2.0),
                ),
                hybrid_config=HybridPlannerConfig(
                    strategy=self.policy_config.optimizer_strategy,
                    use_p90_wait=True,
                    cp_sat_time_limit_seconds=0.25,
                    cp_sat_max_neighborhood_tasks=60,
                ),
            )
        if policy == "static_batch":
            self._initialize_static_batch_plan()
        self.replan_records: list[ReplanRecord] = []
        self.service_records: list[dict[str, object]] = []
        self.wait_prediction_records: list[dict[str, object]] = []
        self._ticks = 0

    def _initialize_static_batch_plan(self) -> None:
        """V2-style one-shot plan using only declared inputs and estimates."""

        patients = tuple(
            PatientState(
                patient_id=item.patient_id,
                exams=item.exams,
                now=item.scheduled_arrival,
                location_id=LOBBY_ID,
                availability_windows=item.availability_windows,
                age_years=item.age,
                gender=item.sex,
            )
            for item in self.scenario.patients
        )
        departments = {
            item.id: DepartmentState(
                id=item.id,
                observed_at=self.scenario.operating_window.start,
                service_windows=item.service_windows,
                capacity=item.capacity,
                floor=item.floor,
            )
            for item in self.scenario.departments.values()
        }
        schedule = build_batch_schedule(
            patients,
            departments,
            self.scenario.travel_times,
            self.scenario.operating_window,
            config=BatchPlannerConfig(critical_path_enabled=False),
        )
        by_patient: dict[str, list[BatchPlanStep]] = {}
        for step in schedule.steps:
            by_patient.setdefault(step.patient_id, []).append(step)
        for patient_id, runtime in self.patients.items():
            steps = sorted(by_patient.get(patient_id, ()), key=lambda item: item.start_at)
            runtime.planned_order = tuple(step.exam_id for step in steps)
            runtime.planned_steps = {step.exam_id: step for step in steps}

    def run(self) -> SimulationResult:
        started = perf_counter()
        now = self.scenario.operating_window.start
        while now <= self.scenario.simulation_end:
            self._ticks += 1
            self._complete_services(now)
            self._arrive_patients(now)
            self._update_availability(now)
            if self.policy_config is not None and now < self.scenario.operating_window.end:
                elapsed = int(
                    (now - self.scenario.operating_window.start).total_seconds() // 60
                )
                if elapsed % 5 == 0:
                    self._replan(now)
            self._complete_walks(now)
            self._remove_unavailable_queued(now)
            self._dispatch_idle_patients(now)
            self._start_services(now)
            for runtime in self.patients.values():
                if (
                    runtime.status == "idle"
                    and runtime.last_available is True
                    and runtime.completed
                ):
                    runtime.idle_between_exams_minutes += 1
                continuous_wait = 0.0
                if runtime.status == "queue" and runtime.queue_joined_at is not None:
                    continuous_wait = (now - runtime.queue_joined_at).total_seconds() / 60
                elif runtime.status == "idle" and runtime.ready_since_at is not None:
                    continuous_wait = (now - runtime.ready_since_at).total_seconds() / 60
                runtime.max_continuous_wait_minutes = max(
                    runtime.max_continuous_wait_minutes,
                    continuous_wait,
                )
            for department in self.departments.values():
                department.queue_lengths.append(len(department.queue))
                department.max_queue_length = max(
                    department.max_queue_length,
                    len(department.queue),
                )
            now += timedelta(minutes=1)
        self._finalize_incomplete()
        cpu_seconds = perf_counter() - started
        return self._build_result(cpu_seconds)

    def _arrive_patients(self, now: datetime) -> None:
        for runtime in self.patients.values():
            truth = self.ground_truth.patient(runtime.spec.patient_id)
            if runtime.status != "not_arrived" or truth.actual_arrival > now:
                continue
            runtime.status = "idle"
            runtime.location_id = LOBBY_ID
            runtime.ready_since_at = now
            runtime.last_completion_at = now
            self._event(
                runtime,
                now,
                "arrive",
                details=(
                    f"scheduled={runtime.spec.scheduled_arrival.strftime('%H:%M')};"
                    f"actual={truth.actual_arrival.strftime('%H:%M')}"
                ),
            )

    def _update_availability(self, now: datetime) -> None:
        for runtime in self.patients.values():
            if runtime.status in {"not_arrived", "done", "incomplete"}:
                continue
            truth = self.ground_truth.patient(runtime.spec.patient_id)
            available = _available(
                runtime.spec.availability_windows,
                now,
            ) and not any(
                window.start <= now < window.end
                for window in truth.interruption_windows
            )
            if runtime.last_available is None:
                runtime.last_available = available
            elif runtime.last_available != available:
                self._event(
                    runtime,
                    now,
                    "availability_resume" if available else "availability_pause",
                )
                runtime.last_available = available
            if not available and runtime.status not in {"service", "walking"}:
                runtime.unavailable_minutes += 1

    def _replan(self, now: datetime) -> None:
        assert self.scheduler is not None
        assert self.wait_predictor is not None
        active = [
            runtime
            for runtime in self.patients.values()
            if runtime.status not in {"not_arrived", "done", "incomplete"}
        ]
        if not active:
            return
        snapshots = tuple(
            self._queue_snapshot(item.spec.id, now)
            for item in self.departments.values()
        )
        predictions = {
            department_id: self._biased_wait_prediction(prediction)
            for department_id, prediction in self.wait_predictor.predict_many(
                snapshots
            ).items()
        }
        activity_predictions = (
            self.activity_predictor.predict_many(
                [runtime.spec.patient_id for runtime in active],
                now,
                demographics={
                    runtime.spec.patient_id: (runtime.spec.age, runtime.spec.sex)
                    for runtime in active
                },
            )
            if self.activity_predictor is not None
            else None
        )
        patient_states = tuple(self._patient_state(runtime, now) for runtime in active)
        department_states = {
            spec.id: DepartmentState(
                id=spec.id,
                observed_at=now,
                expected_wait_minutes=0,
                accepting_patients=True,
                service_windows=spec.service_windows,
                capacity=spec.capacity,
                floor=spec.floor,
            )
            for spec in self.scenario.departments.values()
        }
        plan_started = perf_counter()
        plan = self.scheduler.replan(
            now,
            patient_states,
            department_states,
            wait_predictions=predictions,
            activity_predictions=activity_predictions,
            force=False,
            triggered_by="five_minute_tick",
        )
        cpu_ms = (perf_counter() - plan_started) * 1000
        if not plan.replanned:
            return
        self.replan_records.append(
            ReplanRecord(
                at=now,
                active_patient_count=len(active),
                scheduled_exam_count=plan.schedule.metrics.scheduled_exam_count,
                unscheduled_exam_count=plan.schedule.metrics.unscheduled_exam_count,
                backend=plan.backend,
                optimizer_status=plan.optimizer_status,
                cpu_milliseconds=cpu_ms,
                predicted_total_wait_minutes=plan.schedule.metrics.total_wait_minutes,
                cp_sat_invoked=plan.cp_sat_invoked,
                solve_seconds=plan.solve_seconds,
                objective_improvement=plan.objective_improvement,
                completion_risk_improvement=plan.completion_risk_improvement,
            )
        )
        by_patient: dict[str, list[BatchPlanStep]] = {}
        for step in plan.schedule.steps:
            by_patient.setdefault(step.patient_id, []).append(step)
        for runtime in active:
            steps = sorted(by_patient.get(runtime.spec.patient_id, ()), key=lambda item: item.start_at)
            new_order = tuple(
                step.exam_id
                for step in steps
                if step.exam_id not in runtime.completed and step.exam_id not in runtime.blocked
            )
            if runtime.current_exam_id is not None and runtime.status in {
                "walking", "queue", "service"
            }:
                new_order = (runtime.current_exam_id,) + tuple(
                    exam_id for exam_id in new_order if exam_id != runtime.current_exam_id
                )
            previous_remaining = tuple(
                exam_id
                for exam_id in runtime.planned_order
                if exam_id not in runtime.completed and exam_id not in runtime.blocked
            )
            if (
                self.policy_config is not None
                and self.policy_config.wait_oriented
                and previous_remaining
                and new_order
                and previous_remaining[0] != new_order[0]
                and self.minimum_replan_improvement_minutes > 0
            ):
                steps_by_exam = {step.exam_id: step for step in steps}
                previous_first = steps_by_exam.get(previous_remaining[0])
                new_first = steps_by_exam.get(new_order[0])
                if previous_first is not None and new_first is not None:
                    predicted_improvement = (
                        previous_first.wait_minutes - new_first.wait_minutes
                    )
                    if predicted_improvement < self.minimum_replan_improvement_minutes:
                        new_order = (previous_remaining[0],) + tuple(
                            exam_id
                            for exam_id in new_order
                            if exam_id != previous_remaining[0]
                        )
                        runtime.suppressed_route_change_count += 1
            if (
                previous_remaining
                and new_order
                and previous_remaining[0] != new_order[0]
            ):
                runtime.route_change_count += 1
                self._event(
                    runtime,
                    now,
                    "route_update",
                    details=f"{'>'.join(previous_remaining)} -> {'>'.join(new_order)}",
                )
            elif not previous_remaining and new_order:
                self._event(
                    runtime,
                    now,
                    "route_assigned",
                    details=">".join(new_order),
                )
            runtime.planned_order = new_order
            runtime.planned_steps = {step.exam_id: step for step in steps}
            runtime.replan_count += 1
            if any(step.completion_risk >= 0.5 for step in steps):
                runtime.high_completion_risk_seen = True

    def _patient_state(
        self,
        runtime: _PatientRuntime,
        now: datetime,
    ) -> PatientState:
        current_queue_wait = (
            (now - runtime.queue_joined_at).total_seconds() / 60
            if runtime.status == "queue" and runtime.queue_joined_at is not None
            else 0.0
        )
        continuous_wait = current_queue_wait
        if runtime.status == "idle" and runtime.ready_since_at is not None:
            continuous_wait = (now - runtime.ready_since_at).total_seconds() / 60
        last_completion = runtime.last_completion_at or now
        return PatientState(
            patient_id=runtime.spec.patient_id,
            exams=runtime.spec.exams,
            now=now,
            location_id=runtime.location_id,
            completed_exam_ids=frozenset(runtime.completed),
            in_progress_exam_id=(runtime.current_exam_id if runtime.status == "service" else None),
            in_progress_finish_at=(runtime.service_finishes_at if runtime.status == "service" else None),
            previous_order=runtime.planned_order,
            availability_windows=runtime.spec.availability_windows,
            accumulated_wait_minutes=runtime.wait_minutes,
            continuous_wait_minutes=continuous_wait,
            minutes_since_last_completion=max(
                0.0,
                (now - last_completion).total_seconds() / 60,
            ),
            age_years=runtime.spec.age,
            gender=runtime.spec.sex,
        )

    def _dispatch_idle_patients(self, now: datetime) -> None:
        for runtime in self.patients.values():
            if runtime.status != "idle" or runtime.last_available is not True:
                continue
            if len(runtime.completed | runtime.blocked) == len(runtime.spec.exams):
                if len(runtime.completed) == len(runtime.spec.exams):
                    self._finish_patient(runtime, now)
                continue
            exam = self._next_exam(runtime, now)
            if exam is None:
                continue
            delay = self.ground_truth.patient(
                runtime.spec.patient_id
            ).adherence_delay_minutes
            ready_since = runtime.ready_since_at or now
            if now < ready_since + timedelta(minutes=delay):
                continue
            baseline = self.scenario.travel_times.between(
                runtime.location_id,
                exam.department_id,
            )
            if baseline == 0:
                runtime.current_exam_id = exam.id
                self._join_queue(runtime, exam, now)
                continue
            duration = self._actual_walk_minutes(runtime, exam, baseline)
            runtime.status = "walking"
            runtime.ready_since_at = None
            runtime.current_exam_id = exam.id
            runtime.walk_origin_id = runtime.location_id
            runtime.walk_destination_id = exam.department_id
            runtime.walk_started_at = now
            runtime.walk_finishes_at = now + timedelta(minutes=duration)
            self._event(
                runtime,
                now,
                "walk_start",
                exam_id=exam.id,
                department_id=exam.department_id,
                details=f"baseline={baseline};actual={duration}",
            )

    def _complete_walks(self, now: datetime) -> None:
        for runtime in self.patients.values():
            if runtime.status != "walking" or runtime.walk_finishes_at is None:
                continue
            if runtime.walk_finishes_at > now:
                continue
            assert runtime.current_exam_id is not None
            assert runtime.walk_started_at is not None
            assert runtime.walk_origin_id is not None
            assert runtime.walk_destination_id is not None
            exam = _exam(runtime.spec, runtime.current_exam_id)
            actual = (now - runtime.walk_started_at).total_seconds() / 60
            baseline = self.scenario.travel_times.between(
                runtime.walk_origin_id,
                runtime.walk_destination_id,
            )
            runtime.walk_minutes += actual
            runtime.location_id = runtime.walk_destination_id
            self._event(
                runtime,
                now,
                "walk_end",
                exam_id=exam.id,
                department_id=exam.department_id,
                details=f"baseline={baseline};actual={actual:.1f}",
            )
            if self.activity_feedback is not None and baseline > 0:
                self.activity_feedback.ingest(
                    PersonalActivityFeedback(
                        event_id=f"walk-{self.policy}-{runtime.spec.patient_id}-{exam.id}-{now.isoformat()}",
                        patient_id=runtime.spec.patient_id,
                        origin_id=runtime.walk_origin_id,
                        destination_id=runtime.walk_destination_id,
                        occurred_at=now,
                        actual_activity_minutes=actual,
                        baseline_travel_minutes=baseline,
                        source="simulated_phone_accelerometer",
                        confidence=0.92,
                        distance_meters=(
                            baseline
                            * 60.0
                            * self.activity_predictor.reference_speed_mps
                        ),
                    )
                )
            runtime.walk_started_at = None
            runtime.walk_finishes_at = None
            runtime.walk_origin_id = None
            runtime.walk_destination_id = None
            if runtime.last_available is True:
                self._join_queue(runtime, exam, now)
            else:
                runtime.status = "idle"
                runtime.current_exam_id = None
                runtime.ready_since_at = now

    def _join_queue(
        self,
        runtime: _PatientRuntime,
        exam: Exam,
        now: datetime,
    ) -> None:
        department = self.departments[exam.department_id]
        if runtime.spec.patient_id in department.queue:
            return
        runtime.status = "queue"
        runtime.ready_since_at = None
        runtime.current_exam_id = exam.id
        runtime.queue_joined_at = now
        runtime.queue_prediction = (
            self._biased_wait_prediction(
                self.wait_predictor.predict(
                    self._queue_snapshot(exam.department_id, now)
                )
            )
            if self.wait_predictor is not None
            else None
        )
        department.queue.append(runtime.spec.patient_id)
        self._event(
            runtime,
            now,
            "queue_join",
            exam_id=exam.id,
            department_id=exam.department_id,
            details=(
                f"queue_ahead={len(department.queue) - 1};"
                + (
                    f"pred_mean={runtime.queue_prediction.mean_minutes:.1f};"
                    f"pred_p90={runtime.queue_prediction.p90_minutes:.1f}"
                    if runtime.queue_prediction is not None
                    else "prediction=none"
                )
            ),
        )

    def _remove_unavailable_queued(self, now: datetime) -> None:
        for runtime in self.patients.values():
            if runtime.status != "queue":
                continue
            if runtime.last_available is True:
                continue
            assert runtime.current_exam_id is not None
            exam = _exam(runtime.spec, runtime.current_exam_id)
            department = self.departments[exam.department_id]
            if runtime.spec.patient_id in department.queue:
                department.queue.remove(runtime.spec.patient_id)
            waited = (
                (now - runtime.queue_joined_at).total_seconds() / 60
                if runtime.queue_joined_at is not None
                else 0.0
            )
            runtime.wait_minutes += waited
            runtime.queue_abandon_count += 1
            self._event(
                runtime,
                now,
                "queue_leave_for_unavailability",
                exam_id=exam.id,
                department_id=exam.department_id,
                details=f"waited={waited:.1f}",
            )
            runtime.status = "idle"
            runtime.current_exam_id = None
            runtime.ready_since_at = now
            runtime.queue_joined_at = None
            runtime.queue_prediction = None

    def _start_services(self, now: datetime) -> None:
        for department in self.departments.values():
            for resource_index, slot in enumerate(department.resources):
                if slot is not None or self._resource_in_downtime(
                    department.spec, resource_index, now
                ):
                    continue
                candidate_id = self._first_service_candidate(department, now)
                if candidate_id is None:
                    continue
                runtime = self.patients[candidate_id]
                assert runtime.current_exam_id is not None
                exam = _exam(runtime.spec, runtime.current_exam_id)
                department.queue.remove(candidate_id)
                actual_duration = self._actual_service_minutes(runtime, exam, now)
                finishes_at = now + timedelta(minutes=actual_duration)
                waited = (
                    (now - runtime.queue_joined_at).total_seconds() / 60
                    if runtime.queue_joined_at is not None
                    else 0.0
                )
                runtime.wait_minutes += waited
                runtime.service_minutes += actual_duration
                runtime.status = "service"
                runtime.service_started_at = now
                runtime.service_finishes_at = finishes_at
                department.waits.append(waited)
                department.busy_resource_minutes += actual_duration
                prediction = runtime.queue_prediction
                if prediction is not None:
                    self.wait_prediction_records.append(
                        {
                            "policy": self.policy,
                            "patient_id": runtime.spec.patient_id,
                            "exam_id": exam.id,
                            "department_id": exam.department_id,
                            "prediction_at": prediction.generated_at,
                            "predicted_mean_minutes": prediction.mean_minutes,
                            "predicted_p90_minutes": prediction.p90_minutes,
                            "actual_wait_minutes": waited,
                        }
                    )
                    if self.wait_feedback is not None:
                        self.wait_feedback.ingest(
                            WaitTimingFeedback(
                                event_id=f"wait-{self.policy}-{runtime.spec.patient_id}-{exam.id}-{now.isoformat()}",
                                department_id=exam.department_id,
                                occurred_at=now,
                                actual_wait_minutes=waited,
                                prediction=prediction,
                            )
                        )
                runtime.queue_joined_at = None
                runtime.queue_prediction = None
                department.resources[resource_index] = _ServiceSlot(
                    patient_id=runtime.spec.patient_id,
                    exam_id=exam.id,
                    started_at=now,
                    finishes_at=finishes_at,
                    actual_duration_minutes=actual_duration,
                )
                self._event(
                    runtime,
                    now,
                    "exam_start",
                    exam_id=exam.id,
                    department_id=exam.department_id,
                    details=(
                        f"resource={resource_index};wait={waited:.1f};"
                        f"estimated={exam.duration_minutes};actual={actual_duration}"
                    ),
                )

    def _complete_services(self, now: datetime) -> None:
        for department in self.departments.values():
            for resource_index, slot in enumerate(department.resources):
                if slot is None or slot.finishes_at > now:
                    continue
                runtime = self.patients[slot.patient_id]
                exam = _exam(runtime.spec, slot.exam_id)
                runtime.completed.add(slot.exam_id)
                runtime.location_id = exam.department_id
                runtime.status = "idle"
                runtime.ready_since_at = now
                runtime.last_completion_at = now
                runtime.current_exam_id = None
                runtime.service_started_at = None
                runtime.service_finishes_at = None
                department.resources[resource_index] = None
                department.completed_exam_count += 1
                department.recent_service_minutes.append(slot.actual_duration_minutes)
                del department.recent_service_minutes[:-20]
                if (
                    self.policy_config is not None
                    and self.policy_config.wait_feedback
                    and self.wait_predictor is not None
                ):
                    self.wait_predictor.observe_service_completion(
                        exam.department_id,
                        slot.actual_duration_minutes,
                        now,
                    )
                violated = _deadline_violated(exam, slot.started_at, now)
                if violated:
                    runtime.deadline_violation_count += 1
                self.service_records.append(
                    {
                        "policy": self.policy,
                        "patient_id": runtime.spec.patient_id,
                        "exam_id": exam.id,
                        "department_id": exam.department_id,
                        "resource_index": resource_index,
                        "start_at": slot.started_at,
                        "finish_at": now,
                        "actual_duration_minutes": slot.actual_duration_minutes,
                        "estimated_duration_minutes": exam.duration_minutes,
                        "deadline_violated": violated,
                    }
                )
                self._event(
                    runtime,
                    now,
                    "exam_end",
                    exam_id=exam.id,
                    department_id=exam.department_id,
                    details=f"deadline_violated={str(violated).lower()}",
                )
                if len(runtime.completed) == len(runtime.spec.exams):
                    self._finish_patient(runtime, now)

    def _first_service_candidate(
        self,
        department: _DepartmentRuntime,
        now: datetime,
    ) -> str | None:
        ordered = sorted(
            department.queue,
            key=lambda patient_id: (
                self.patients[patient_id].queue_joined_at or now,
                patient_id,
            ),
        )
        for patient_id in ordered:
            runtime = self.patients[patient_id]
            if runtime.current_exam_id is None:
                continue
            exam = _exam(runtime.spec, runtime.current_exam_id)
            if not set(exam.prerequisites).issubset(runtime.completed):
                continue
            if runtime.last_available is not True:
                continue
            estimated_end = now + timedelta(minutes=exam.duration_minutes)
            if not _available_for_interval(
                runtime.spec.availability_windows,
                now,
                estimated_end,
            ):
                continue
            if not _available_for_interval(
                department.spec.service_windows,
                now,
                estimated_end,
            ):
                continue
            if exam.allowed_windows and not _available_for_interval(
                exam.allowed_windows,
                now,
                estimated_end,
            ):
                continue
            if exam.earliest_start is not None and now < exam.earliest_start:
                continue
            if exam.latest_finish is not None and estimated_end > exam.latest_finish:
                continue
            return patient_id
        return None

    def _next_exam(
        self,
        runtime: _PatientRuntime,
        now: datetime,
    ) -> Exam | None:
        order = (
            tuple(exam.id for exam in runtime.spec.exams)
            if self.policy == "shortest_queue"
            else runtime.planned_order
        )
        feasible: list[Exam] = []
        for exam_id in order:
            if exam_id in runtime.completed or exam_id in runtime.blocked:
                continue
            exam = _exam(runtime.spec, exam_id)
            if not set(exam.prerequisites).issubset(runtime.completed):
                continue
            if not _has_future_exam_slot(
                exam,
                runtime.spec.availability_windows,
                self.scenario.departments[exam.department_id].service_windows,
                now,
                self.scenario.operating_window.end,
            ):
                runtime.blocked.add(exam.id)
                self._event(
                    runtime,
                    now,
                    "exam_unavailable_for_rest_of_day",
                    exam_id=exam.id,
                    department_id=exam.department_id,
                )
                continue
            feasible.append(exam)
        if not feasible:
            return None
        if self.policy != "shortest_queue":
            return feasible[0]

        def greedy_score(exam: Exam) -> tuple[float, float, str]:
            department = self.departments[exam.department_id]
            visible_work = (
                len(department.queue) + sum(slot is not None for slot in department.resources)
            ) * exam.duration_minutes / department.spec.capacity
            travel = self.scenario.travel_times.between(
                runtime.location_id,
                exam.department_id,
            )
            deadline = (
                (exam.latest_finish - now).total_seconds() / 60
                if exam.latest_finish is not None
                else 24 * 60
            )
            return (visible_work + travel - exam.delay_cost_per_minute * 20, deadline, exam.id)

        return min(feasible, key=greedy_score)

    def _queue_snapshot(self, department_id: str, now: datetime) -> QueueSnapshot:
        department = self.departments[department_id]
        remaining = tuple(
            max(0.0, (slot.finishes_at - now).total_seconds() / 60)
            for slot in department.resources
            if slot is not None
        )
        downtime = 0.0
        for resource_index in range(department.spec.capacity):
            for window in self.ground_truth.resource_downtimes.get(
                department_id, {}
            ).get(resource_index, ()):
                if window.start <= now < window.end:
                    downtime = max(downtime, (window.end - now).total_seconds() / 60)
        return QueueSnapshot(
            department_id=department_id,
            observed_at=now,
            queued_patients=len(department.queue),
            capacity=department.spec.capacity,
            in_service_remaining_minutes=remaining,
            recent_service_minutes=tuple(department.recent_service_minutes),
            operational_delay_minutes=downtime,
        )

    def _biased_wait_prediction(self, prediction: WaitPrediction) -> WaitPrediction:
        """Apply an observation-only stress bias; Ground Truth remains untouched."""

        factor = 1.0 + self.observation_wait_bias_fraction
        return replace(
            prediction,
            mean_minutes=max(0.0, prediction.mean_minutes * factor),
            p90_minutes=max(0.0, prediction.p90_minutes * factor),
            model_version=(
                f"{prediction.model_version}|observation-bias={self.observation_wait_bias_fraction:+.0%}"
            ),
        )

    def _actual_walk_minutes(
        self,
        runtime: _PatientRuntime,
        exam: Exam,
        baseline: int,
    ) -> int:
        return self.ground_truth.walk_minutes(
            runtime.spec.patient_id,
            runtime.location_id,
            exam.department_id,
        )

    def _actual_service_minutes(
        self,
        runtime: _PatientRuntime,
        exam: Exam,
        now: datetime,
    ) -> int:
        return self.ground_truth.service_minutes(
            runtime.spec.patient_id,
            exam.id,
            now,
        )

    def _resource_in_downtime(
        self,
        spec: HospitalDepartmentSpec,
        resource_index: int,
        now: datetime,
    ) -> bool:
        return any(
            window.start <= now < window.end
            for window in self.ground_truth.resource_downtimes.get(
                spec.id, {}
            ).get(resource_index, ())
        )

    def _finish_patient(self, runtime: _PatientRuntime, now: datetime) -> None:
        runtime.status = "done"
        runtime.departure_at = now
        self._event(runtime, now, "depart_complete")

    def _finalize_incomplete(self) -> None:
        for runtime in self.patients.values():
            if runtime.status == "done":
                continue
            runtime.status = "incomplete"
            self._event(
                runtime,
                self.scenario.simulation_end,
                "depart_incomplete",
                details=",".join(
                    exam.id
                    for exam in runtime.spec.exams
                    if exam.id not in runtime.completed
                ),
            )

    def _event(
        self,
        runtime: _PatientRuntime,
        at: datetime,
        event: str,
        *,
        exam_id: str = "",
        department_id: str = "",
        details: str = "",
    ) -> None:
        runtime.events.append(
            TrajectoryEvent(
                at=at,
                event=event,
                location_id=runtime.location_id,
                exam_id=exam_id,
                department_id=department_id,
                details=details,
            )
        )

    def _build_result(self, cpu_seconds: float) -> SimulationResult:
        outcomes: list[PatientOutcome] = []
        learned_errors: list[float] = []
        learned_count = 0
        for runtime in sorted(self.patients.values(), key=lambda item: item.spec.patient_id):
            truth = self.ground_truth.patient(runtime.spec.patient_id)
            learned_factor: float | None = None
            if self.activity_predictor is not None:
                prediction = self.activity_predictor.predict(
                    runtime.spec.patient_id,
                    self.scenario.simulation_end,
                    age_years=runtime.spec.age,
                    gender=runtime.spec.sex,
                )
                if prediction.sample_count >= 3:
                    learned_factor = prediction.travel_time_factor
                    learned_count += 1
                    learned_errors.append(
                        abs(learned_factor - truth.true_mobility_factor)
                    )
            journey_end = runtime.departure_at or self.scenario.operating_window.end
            arrival = truth.actual_arrival
            journey = max(0.0, (journey_end - arrival).total_seconds() / 60)
            incomplete = tuple(
                exam.id for exam in runtime.spec.exams if exam.id not in runtime.completed
            )
            terminal_exam_ids = _terminal_aggregator_ids(runtime.spec.exams)
            completed_terminal_count = len(terminal_exam_ids & runtime.completed)
            outcomes.append(
                PatientOutcome(
                    policy=self.policy,
                    patient_id=runtime.spec.patient_id,
                    completed=not incomplete,
                    completed_exam_count=len(runtime.completed),
                    total_exam_count=len(runtime.spec.exams),
                    arrival_at=arrival,
                    departure_at=runtime.departure_at,
                    journey_minutes=journey,
                    wait_minutes=runtime.wait_minutes,
                    walk_minutes=runtime.walk_minutes,
                    service_minutes=runtime.service_minutes,
                    unavailable_minutes=runtime.unavailable_minutes,
                    route_change_count=runtime.route_change_count,
                    queue_abandon_count=runtime.queue_abandon_count,
                    deadline_violation_count=runtime.deadline_violation_count,
                    final_status=runtime.status,
                    incomplete_exam_ids=incomplete,
                    learned_mobility_factor=learned_factor,
                    true_mobility_factor=truth.true_mobility_factor,
                    events=tuple(runtime.events),
                    unfinished_exam_count=len(incomplete),
                    terminal_exam_count=len(terminal_exam_ids),
                    completed_terminal_exam_count=completed_terminal_count,
                    critical_path_missed=bool(
                        terminal_exam_ids - runtime.completed
                    ),
                    high_completion_risk_seen=runtime.high_completion_risk_seen,
                    replan_count=runtime.replan_count,
                    route_change_notification_count=runtime.route_change_count,
                    idle_between_exams_minutes=runtime.idle_between_exams_minutes,
                    max_continuous_wait_minutes=runtime.max_continuous_wait_minutes,
                    suppressed_route_change_count=runtime.suppressed_route_change_count,
                )
            )
        departments = tuple(
            self._department_outcome(runtime)
            for runtime in sorted(self.departments.values(), key=lambda item: item.spec.id)
        )
        prediction_metrics = None
        if self.wait_prediction_records:
            prediction_metrics = evaluate_wait_predictions(
                tuple(
                    (
                        WaitPrediction(
                        department_id=str(item["department_id"]),
                        generated_at=item["prediction_at"],
                        mean_minutes=float(item["predicted_mean_minutes"]),
                        p90_minutes=float(item["predicted_p90_minutes"]),
                        model_version="simulation-replay",
                        sample_count=0,
                        ),
                        float(item["actual_wait_minutes"]),
                    )
                    for item in self.wait_prediction_records
                )
            )
        journeys = [item.journey_minutes for item in outcomes]
        waits = [item.wait_minutes for item in outcomes]
        walks = [item.walk_minutes for item in outcomes]
        completed_patients = sum(item.completed for item in outcomes)
        completed_exams = sum(item.completed_exam_count for item in outcomes)
        total_exams = sum(item.total_exam_count for item in outcomes)
        unfinished_exam_count = sum(item.unfinished_exam_count for item in outcomes)
        total_terminal_exams = sum(item.terminal_exam_count for item in outcomes)
        completed_terminal_exams = sum(
            item.completed_terminal_exam_count for item in outcomes
        )
        missing_exam_counts: dict[str, int] = {}
        for item in outcomes:
            for exam_id in item.incomplete_exam_ids:
                missing_exam_counts[exam_id] = missing_exam_counts.get(exam_id, 0) + 1
        cp_records = [item for item in self.replan_records if item.cp_sat_invoked]
        solve_times = [item.solve_seconds for item in cp_records]
        operating_hours = (
            self.scenario.operating_window.end
            - self.scenario.operating_window.start
        ).total_seconds() / 3600
        metrics = SimulationMetrics(
            policy=self.policy,
            patient_count=len(outcomes),
            completed_patient_count=completed_patients,
            patient_completion_rate=completed_patients / len(outcomes),
            completed_exam_count=completed_exams,
            total_exam_count=total_exams,
            exam_completion_rate=completed_exams / total_exams,
            mean_journey_minutes=mean(journeys),
            median_journey_minutes=median(journeys),
            p90_journey_minutes=_percentile(journeys, 0.90),
            p95_journey_minutes=_percentile(journeys, 0.95),
            mean_wait_minutes=mean(waits),
            p90_wait_minutes=_percentile(waits, 0.90),
            p95_wait_minutes=_percentile(waits, 0.95),
            mean_walk_minutes=mean(walks),
            p90_walk_minutes=_percentile(walks, 0.90),
            wait_p90_p10_gap_minutes=_percentile(waits, 0.90) - _percentile(waits, 0.10),
            deadline_violation_count=sum(item.deadline_violation_count for item in outcomes),
            queue_abandon_count=sum(item.queue_abandon_count for item in outcomes),
            route_change_count=sum(item.route_change_count for item in outcomes),
            replan_count=len(self.replan_records),
            simulation_ticks=self._ticks,
            cpu_seconds=cpu_seconds,
            wait_prediction_sample_count=(prediction_metrics.sample_count if prediction_metrics else 0),
            wait_prediction_mae_minutes=(prediction_metrics.mae_minutes if prediction_metrics else None),
            wait_prediction_rmse_minutes=(prediction_metrics.rmse_minutes if prediction_metrics else None),
            wait_prediction_bias_minutes=(prediction_metrics.mean_error_minutes if prediction_metrics else None),
            wait_prediction_p90_coverage=(prediction_metrics.p90_coverage if prediction_metrics else None),
            learned_patient_count=learned_count,
            mobility_factor_mae=(mean(learned_errors) if learned_errors else None),
            full_completion_rate=completed_patients / len(outcomes),
            unfinished_exam_count=unfinished_exam_count,
            deadline_miss_count=sum(
                item.deadline_violation_count for item in outcomes
            ),
            terminal_exam_completion_rate=(
                completed_terminal_exams / total_terminal_exams
                if total_terminal_exams
                else 1.0
            ),
            critical_path_miss_count=sum(
                item.critical_path_missed for item in outcomes
            ),
            patients_at_high_completion_risk=sum(
                item.high_completion_risk_seen for item in outcomes
            ),
            mean_replans_per_patient=mean(
                [item.replan_count for item in outcomes]
            ),
            route_change_notifications_per_patient=(
                sum(item.route_change_notification_count for item in outcomes)
                / len(outcomes)
            ),
            patients_missing_one_exam=sum(
                item.unfinished_exam_count == 1 for item in outcomes
            ),
            missing_exam_counts=dict(sorted(missing_exam_counts.items())),
            cp_sat_invocation_count=len(cp_records),
            cp_sat_optimal_count=sum(
                item.optimizer_status.startswith("OPTIMAL") for item in cp_records
            ),
            cp_sat_feasible_count=sum(
                item.optimizer_status.startswith("FEASIBLE") for item in cp_records
            ),
            cp_sat_timeout_count=sum(
                "UNKNOWN" in item.optimizer_status for item in cp_records
            ),
            cp_sat_fallback_count=sum(
                item.backend != "cp_sat" for item in cp_records
            ),
            cp_sat_mean_solve_seconds=(mean(solve_times) if solve_times else 0.0),
            cp_sat_p90_solve_seconds=_percentile(solve_times, 0.90),
            cp_sat_p95_solve_seconds=_percentile(solve_times, 0.95),
            cp_sat_objective_improvement=sum(
                item.objective_improvement for item in cp_records
            ),
            cp_sat_completion_risk_improvement=sum(
                item.completion_risk_improvement for item in cp_records
            ),
            median_wait_minutes=median(waits),
            p99_wait_minutes=_percentile(waits, 0.99),
            max_wait_minutes=max(waits, default=0.0),
            patients_waiting_over_60m=sum(wait > 60 for wait in waits),
            patients_waiting_over_90m=sum(wait > 90 for wait in waits),
            patients_waiting_over_120m=sum(wait > 120 for wait in waits),
            max_continuous_wait_minutes=max(
                (item.max_continuous_wait_minutes for item in outcomes),
                default=0.0,
            ),
            mean_idle_between_exams_minutes=mean(
                [item.idle_between_exams_minutes for item in outcomes]
            ),
            suppressed_route_change_count=sum(
                item.suppressed_route_change_count for item in outcomes
            ),
            mean_department_utilization=mean(
                [item.utilization for item in departments]
            ),
            p90_department_queue_length=_percentile(
                [item.p90_queue_length for item in departments],
                0.90,
            ),
            peak_department_queue_length=max(
                (item.max_queue_length for item in departments),
                default=0,
            ),
            throughput_exams_per_hour=(
                completed_exams / operating_hours if operating_hours else 0.0
            ),
        )
        return SimulationResult(
            scenario=self.scenario,
            ground_truth=self.ground_truth,
            policy=self.policy,
            metrics=metrics,
            patient_outcomes=tuple(outcomes),
            department_outcomes=departments,
            replan_records=tuple(self.replan_records),
            service_records=tuple(self.service_records),
            wait_prediction_records=tuple(self.wait_prediction_records),
        )

    def _department_outcome(
        self,
        runtime: _DepartmentRuntime,
    ) -> DepartmentOutcome:
        open_minutes = sum(
            (window.end - window.start).total_seconds() / 60
            for window in runtime.spec.service_windows
        )
        downtime_minutes = sum(
            (window.end - window.start).total_seconds() / 60
            for windows in self.ground_truth.resource_downtimes.get(
                runtime.spec.id, {}
            ).values()
            for window in windows
        )
        nominal = open_minutes * runtime.spec.capacity - downtime_minutes
        return DepartmentOutcome(
            policy=self.policy,
            department_id=runtime.spec.id,
            department_name=runtime.spec.name,
            completed_exam_count=runtime.completed_exam_count,
            mean_wait_minutes=(mean(runtime.waits) if runtime.waits else 0.0),
            p90_wait_minutes=_percentile(runtime.waits, 0.90),
            max_queue_length=runtime.max_queue_length,
            busy_resource_minutes=runtime.busy_resource_minutes,
            nominal_capacity_minutes=nominal,
            utilization=(runtime.busy_resource_minutes / nominal if nominal else 0.0),
            mean_queue_length=(mean(runtime.queue_lengths) if runtime.queue_lengths else 0.0),
            p90_queue_length=_percentile(runtime.queue_lengths, 0.90),
        )


def run_comparative_simulation(
    scenario: HospitalScenario,
    ground_truth: SimulationGroundTruth,
    *,
    policies: Sequence[str] = DEFAULT_SIMULATION_POLICIES,
    observation_wait_bias_fraction: float = 0.0,
    critical_risk_slack_minutes: float = 180.0,
    minimum_replan_improvement_minutes: float | None = None,
) -> tuple[SimulationResult, ...]:
    """Replay multiple policies against one immutable Ground Truth trace."""

    results = tuple(
        HospitalDaySimulator(
            scenario,
            ground_truth,
            policy=policy,
            observation_wait_bias_fraction=observation_wait_bias_fraction,
            critical_risk_slack_minutes=critical_risk_slack_minutes,
            minimum_replan_improvement_minutes=minimum_replan_improvement_minutes,
        ).run()
        for policy in policies
    )
    for result in results:
        validate_simulation_result(result)
    return results


def validate_simulation_result(result: SimulationResult) -> None:
    if len(result.patient_outcomes) != len(result.scenario.patients):
        raise AssertionError("患者结果数量与输入不一致")
    for outcome in result.patient_outcomes:
        timestamps = [event.at for event in outcome.events]
        if timestamps != sorted(timestamps):
            raise AssertionError(f"{outcome.patient_id} 轨迹时间逆序")
        exam_starts = [
            event.exam_id for event in outcome.events if event.event == "exam_start"
        ]
        if len(exam_starts) != len(set(exam_starts)):
            raise AssertionError(f"{outcome.patient_id} 存在重复检查")
    by_resource: dict[tuple[str, int], list[tuple[datetime, datetime]]] = {}
    patients = {patient.patient_id: patient for patient in result.scenario.patients}
    completed_at: dict[tuple[str, str], datetime] = {}
    for record in result.service_records:
        key = (str(record["department_id"]), int(record["resource_index"]))
        patient = patients[str(record["patient_id"])]
        exam = _exam(patient, str(record["exam_id"]))
        start = record["start_at"]
        finish = record["finish_at"]
        estimated_end = start + timedelta(minutes=exam.duration_minutes)
        department = result.scenario.departments[exam.department_id]
        if not _available_for_interval(
            patient.availability_windows,
            start,
            estimated_end,
        ):
            raise AssertionError(f"{patient.patient_id}/{exam.id} 违反患者时间窗")
        if not _available_for_interval(
            department.service_windows,
            start,
            estimated_end,
        ):
            raise AssertionError(f"{patient.patient_id}/{exam.id} 违反科室时间窗")
        if exam.earliest_start is not None and start < exam.earliest_start:
            raise AssertionError(f"{patient.patient_id}/{exam.id} 早于最早开始时间")
        if exam.latest_finish is not None and estimated_end > exam.latest_finish:
            raise AssertionError(f"{patient.patient_id}/{exam.id} 晚于预计截止时间")
        for prerequisite in exam.prerequisites:
            if completed_at.get((patient.patient_id, prerequisite), finish) > start:
                raise AssertionError(f"{patient.patient_id}/{exam.id} 违反前置关系")
        completed_at[(patient.patient_id, exam.id)] = finish
        by_resource.setdefault(key, []).append(
            (start, finish)
        )
    for key, intervals in by_resource.items():
        ordered = sorted(intervals)
        for previous, current in zip(ordered, ordered[1:]):
            if previous[1] > current[0]:
                raise AssertionError(f"资源 {key} 发生重叠服务")


def _exam(patient: SyntheticPatientInput, exam_id: str) -> Exam:
    return next(exam for exam in patient.exams if exam.id == exam_id)


def _terminal_aggregator_ids(exams: Sequence[Exam]) -> set[str]:
    """Identify downstream aggregators without relying on department names."""

    successors = {exam.id: 0 for exam in exams}
    for exam in exams:
        for prerequisite in exam.prerequisites:
            successors[prerequisite] += 1
    return {
        exam.id
        for exam in exams
        if successors[exam.id] == 0 and len(exam.prerequisites) >= 2
    }


def _available(windows: Sequence[TimeWindow], at: datetime) -> bool:
    return any(window.start <= at < window.end for window in windows)


def _available_for_interval(
    windows: Sequence[TimeWindow],
    start: datetime,
    end: datetime,
) -> bool:
    return any(window.contains(start, end) for window in windows)


def _has_future_exam_slot(
    exam: Exam,
    patient_windows: Sequence[TimeWindow],
    department_windows: Sequence[TimeWindow],
    now: datetime,
    day_end: datetime,
) -> bool:
    duration = timedelta(minutes=exam.duration_minutes)
    lower = max(now, exam.earliest_start or now)
    upper = min(day_end, exam.latest_finish or day_end)
    if lower >= upper:
        return False
    exam_windows = exam.allowed_windows or (TimeWindow(lower, upper),)
    for patient_window in patient_windows:
        for department_window in department_windows:
            for exam_window in exam_windows:
                start = max(lower, patient_window.start, department_window.start, exam_window.start)
                end = min(upper, patient_window.end, department_window.end, exam_window.end)
                if start + duration <= end:
                    return True
    return False


def _deadline_violated(exam: Exam, start: datetime, finish: datetime) -> bool:
    if exam.latest_finish is not None and finish > exam.latest_finish:
        return True
    if exam.allowed_windows and not _available_for_interval(exam.allowed_windows, start, finish):
        return True
    return False


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = probability * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def minutes(value: timedelta) -> float:
    """Public convenience helper used by exporters and notebooks."""

    return value.total_seconds() / 60


def iter_patient_events(result: SimulationResult) -> Iterable[tuple[PatientOutcome, TrajectoryEvent]]:
    for outcome in result.patient_outcomes:
        for event in outcome.events:
            yield outcome, event
