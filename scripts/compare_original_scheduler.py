"""Reproduce the original mini-program ordering in the shared simulation.

Run from the repository root: .venv/Scripts/python scripts/compare_original_scheduler.py
This compares ordering rules, not a byte-for-byte replay of the old application.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages/scheduler"))

from simulation import experiments
from simulation.engine import validate_scenario
from simulation.experiment_io import export_repeated_experiment

GROUPS = {
    "VITALS": "basic_measurement", "BLOOD": "blood_draw",
    "URINE": "urine_test", "INTERNAL": "internal_surgery",
    "EYE": "eye_ent", "ENT": "eye_ent", "ULTRASOUND": "ultrasound_all",
    "ECG": "ecg", "XRAY": "radiology",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patients", type=int, default=100)
    parser.add_argument("--replications", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260909)
    parser.add_argument("--output", type=Path, default=ROOT / "simulation/output/original-vs-v10-20260909")
    args = parser.parse_args()
    source = ROOT.parent / "_v1_extract/v1/utils/planner.js"
    raw = source.read_bytes()
    match = re.search(r"const fixedOrder = \[([^\]]+)\]", raw.decode("utf-8"))
    if not match:
        raise RuntimeError("Original fixedOrder not found")
    order = re.findall(r"'([^']+)'", match.group(1))
    assert set(order) == set(GROUPS.values())
    original_builder = experiments.build_realistic_hospital_scenario

    def build_common_scenario(*positional, **kwargs):
        scenario = original_builder(*positional, **kwargs)
        patients = []
        for patient in scenario.patients:
            ids = {exam.id for exam in patient.exams if exam.department_id in GROUPS}
            # The simulation's INTERNAL means final consultation; the original
            # app's internal_surgery is a regular exam. Use the latter for both.
            exams = tuple(replace(exam, prerequisites=(
                ("BLOOD",) if exam.id == "URINE" and "BLOOD" in ids else ()
            )) for exam in patient.exams if exam.id in ids)
            baseline = tuple(exam.id for exam in sorted(
                exams, key=lambda exam: (order.index(GROUPS[exam.department_id]), exam.id)
            ))
            assert set(baseline) == ids
            assert all(set(exam.prerequisites) <= ids for exam in exams)
            patients.append(replace(patient, exams=exams, baseline_order=baseline))
        scenario = replace(scenario, patients=tuple(patients))
        validate_scenario(scenario)
        print(f"Scenario seed={scenario.seed}, patients={len(patients)}", flush=True)
        return scenario

    experiments.build_realistic_hospital_scenario = build_common_scenario
    experiments.POLICY_LABELS["fixed_fcfs"] = "最初小程序固定排序规则复现"
    config = experiments.ExperimentConfig(
        patient_count=args.patients, replications=args.replications, base_seed=args.seed,
        policies=("fixed_fcfs", "v10_no_feedback", "v10_dual_feedback"),
        treatment_policy="v10_dual_feedback", metric_names=experiments.V10_METRIC_NAMES,
    )
    result = experiments.run_repeated_experiment(config)
    export_repeated_experiment(args.output, result)
    protocol = {
        "original_source": str(source), "source_sha256": hashlib.sha256(raw).hexdigest(),
        "original_fixed_order": order, "department_mapping": GROUPS,
        "scope": "Original ordering-rule reproduction, not full legacy application replay",
        "controls": ["Same patients, arrivals, actual service/walk times and downtime per seed",
                     "Same FCFS execution, capacities, windows and exam durations for all policies",
                     "Only common exam departments; CT/LUNG/WOMEN excluded",
                     "INTERNAL is regular internal/surgical exam, not final consultation",
                     "URINE follows BLOOD when both selected, matching original data",
                     "EYE and ENT remain separate shared-simulation resources, adjacent in baseline",
                     "Original merged-duration 0.6 heuristic is not applied to either policy",
                     "Both V10 variants run simulation rolling scheduling, not the deployed API"],
        "patients_per_seed": args.patients, "replications": args.replications,
        "elapsed_seconds": result.elapsed_seconds,
    }
    (args.output / "comparison_protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")
    wanted = {"mean_wait_minutes", "mean_walk_minutes", "mean_journey_minutes", "patient_completion_rate", "mean_route_changes", "p90_wait_minutes"}
    for metric in result.aggregate_metrics:
        if metric.metric in wanted:
            print(f"{metric.policy}: {metric.metric}={metric.mean:.4f}", flush=True)
    print(f"Finished in {result.elapsed_seconds:.1f}s; output={args.output}", flush=True)


if __name__ == "__main__":
    main()
