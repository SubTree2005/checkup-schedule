"""Run the repeated, paired, multi-baseline hospital-day experiment."""

from __future__ import annotations

import argparse
from pathlib import Path

from .experiment_io import export_repeated_experiment
from .experiments import (
    ExperimentConfig,
    POLICY_LABELS,
    V9_METRIC_NAMES,
    V10_METRIC_NAMES,
    run_repeated_experiment,
)
from .engine import V9_COMPARISON_POLICIES, V10_COMPARISON_POLICIES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patients", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260824)
    parser.add_argument("--replications", type=int, default=10)
    parser.add_argument("--output", type=Path, default=Path("experiment_results"))
    parser.add_argument("--v9", action="store_true", help="运行 V8 comparator + V9 2x2 消融")
    parser.add_argument("--v10", action="store_true", help="运行 V9 baseline + V10 等待优先四策略")
    parser.add_argument(
        "--include-cp-sat",
        action="store_true",
        help="运行 V9 critical-path 的 heuristic/CP-SAT × 无反馈/双反馈四组对照",
    )
    parser.add_argument(
        "--scenarios",
        default="normal_day",
        help="逗号分隔的场景名",
    )
    parser.add_argument(
        "--minimum-replan-improvement-minutes",
        type=float,
        default=None,
        help="V10 改线 hysteresis 阈值；省略时使用策略默认值",
    )
    args = parser.parse_args()

    if args.v9 and args.v10:
        parser.error("--v9 与 --v10 不能同时使用")
    if args.v10:
        policies = V10_COMPARISON_POLICIES
    elif args.v9 and args.include_cp_sat:
        policies = (
            "rolling_heuristic",
            "rolling_cp_sat",
            "feedback_heuristic",
            "feedback_cp_sat",
        )
    elif args.v9:
        policies = tuple(
            policy for policy in V9_COMPARISON_POLICIES
            if policy != "feedback_cp_sat"
        )
    else:
        policies = ExperimentConfig().policies
    experiment = run_repeated_experiment(
        ExperimentConfig(
            patient_count=args.patients,
            replications=args.replications,
            base_seed=args.seed,
            policies=policies,
            treatment_policy=(
                "v10_dual_feedback"
                if args.v10
                else "rolling_cp_sat"
                if args.v9 and args.include_cp_sat
                else ("no_feedback" if args.v9 else "dynamic_v6")
            ),
            scenario_names=tuple(
                name.strip() for name in args.scenarios.split(",") if name.strip()
            ),
            metric_names=(
                V10_METRIC_NAMES
                if args.v10
                else V9_METRIC_NAMES
                if args.v9
                else ExperimentConfig().metric_names
            ),
            minimum_replan_improvement_minutes=(
                args.minimum_replan_improvement_minutes
            ),
        )
    )
    written = export_repeated_experiment(args.output, experiment)

    print(
        f"重复实验: {args.replications}; 每次患者: {args.patients}; "
        f"策略: {len(experiment.config.policies)}"
    )
    for policy in experiment.config.policies:
        completion = next(
            item for item in experiment.aggregate_metrics
            if item.policy == policy
            and item.metric == "patient_completion_rate"
            and item.scenario_name == experiment.config.scenario_names[0]
            and item.observation_wait_bias_fraction == 0.0
        )
        wait = next(
            item for item in experiment.aggregate_metrics
            if item.policy == policy
            and item.metric == "mean_wait_minutes"
            and item.scenario_name == experiment.config.scenario_names[0]
            and item.observation_wait_bias_fraction == 0.0
        )
        print(
            f"{POLICY_LABELS[policy]}: 完成率={completion.mean:.1%}, "
            f"人均等待={wait.mean:.1f} min"
        )
    print(f"已写入 {len(written)} 个文件: {args.output.resolve()}")


if __name__ == "__main__":
    main()
