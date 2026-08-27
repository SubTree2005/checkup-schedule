from pathlib import Path
from tempfile import TemporaryDirectory
import inspect
import unittest

from simulation.engine import (
    DEFAULT_SIMULATION_POLICIES,
    build_realistic_hospital_scenario,
    run_comparative_simulation,
    validate_scenario,
    validate_simulation_result,
    HospitalDaySimulator,
)
from simulation.experiment_io import export_repeated_experiment
from simulation.experiments import (
    ExperimentConfig,
    run_repeated_experiment,
)
from simulation.ground_truth import (
    generate_ground_truth,
    validate_ground_truth,
)


class GroundTruthIsolationTests(unittest.TestCase):
    def test_observable_scenario_has_no_hidden_reality(self):
        scenario = build_realistic_hospital_scenario(200, seed=11)

        validate_scenario(scenario)
        self.assertEqual(len(scenario.patients), 200)
        self.assertEqual(len(scenario.departments), 12)
        self.assertTrue(all(4 <= len(item.exams) <= 8 for item in scenario.patients))
        patient = scenario.patients[0]
        self.assertFalse(hasattr(patient, "actual_arrival"))
        self.assertFalse(hasattr(patient, "true_mobility_factor"))
        self.assertFalse(hasattr(patient, "adherence_delay_minutes"))

    def test_ground_truth_is_precomputed_and_bound_to_scenario(self):
        scenario = build_realistic_hospital_scenario(15, seed=99)
        truth = generate_ground_truth(scenario, seed=1001)

        validate_ground_truth(scenario, truth)
        self.assertEqual(set(truth.patients), {item.patient_id for item in scenario.patients})
        self.assertTrue(truth.trace_fingerprint)
        self.assertTrue(
            all(
                set(truth.patient(item.patient_id).service_minutes_by_exam)
                == {exam.id for exam in item.exams}
                for item in scenario.patients
            )
        )

    def test_different_truth_seeds_do_not_change_observable_inputs(self):
        scenario = build_realistic_hospital_scenario(15, seed=99)
        first = generate_ground_truth(scenario, seed=1001)
        second = generate_ground_truth(scenario, seed=1002)

        self.assertNotEqual(first.trace_fingerprint, second.trace_fingerprint)
        self.assertEqual(first.scenario_fingerprint, second.scenario_fingerprint)

    def test_policy_decision_methods_do_not_reference_ground_truth(self):
        for method in (
            HospitalDaySimulator._initialize_static_batch_plan,
            HospitalDaySimulator._replan,
            HospitalDaySimulator._patient_state,
            HospitalDaySimulator._next_exam,
        ):
            self.assertNotIn("ground_truth", inspect.getsource(method))


class MultiBaselineExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = build_realistic_hospital_scenario(20, seed=7)
        cls.truth = generate_ground_truth(cls.scenario, seed=1007)
        cls.results = run_comparative_simulation(cls.scenario, cls.truth)

    def test_all_baselines_and_treatment_use_same_ground_truth(self):
        self.assertEqual(
            tuple(item.policy for item in self.results),
            DEFAULT_SIMULATION_POLICIES,
        )
        self.assertEqual(
            {item.ground_truth.trace_fingerprint for item in self.results},
            {self.truth.trace_fingerprint},
        )

    def test_every_policy_has_all_patient_trajectories(self):
        for result in self.results:
            validate_simulation_result(result)
            self.assertEqual(len(result.patient_outcomes), 20)
            self.assertTrue(all(item.events for item in result.patient_outcomes))
            self.assertTrue(
                all(item.events[-1].event.startswith("depart_") for item in result.patient_outcomes)
            )

    def test_rolling_variants_replan_but_static_baselines_do_not(self):
        replans = {item.policy: item.metrics.replan_count for item in self.results}
        self.assertGreater(replans["dynamic_v6"], 0)
        self.assertGreater(replans["rolling_no_feedback"], 0)
        self.assertEqual(replans["fixed_fcfs"], 0)
        self.assertEqual(replans["shortest_queue"], 0)
        self.assertEqual(replans["static_batch"], 0)

    def test_only_full_v6_learns_personal_activity(self):
        learned = {item.policy: item.metrics.learned_patient_count for item in self.results}
        self.assertGreater(learned["dynamic_v6"], 0)
        self.assertEqual(learned["rolling_no_feedback"], 0)


class RepeatedExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.experiment = run_repeated_experiment(
            ExperimentConfig(
                patient_count=15,
                replications=3,
                base_seed=1234,
            )
        )

    def test_repetitions_produce_aggregate_ci_and_paired_effects(self):
        experiment = self.experiment
        self.assertEqual(len(experiment.replication_metrics), 3 * 5)
        self.assertEqual(len(experiment.aggregate_metrics), 5 * 15)
        self.assertEqual(len(experiment.paired_comparisons), 4 * 15)
        self.assertEqual(len(experiment.patient_summaries), 3 * 5 * 15)
        self.assertTrue(
            all(item.ci95_low <= item.mean <= item.ci95_high for item in experiment.aggregate_metrics)
        )

    def test_each_replication_is_paired_on_one_truth_fingerprint(self):
        by_replication = {}
        for row in self.experiment.replication_metrics:
            by_replication.setdefault(row.replication_index, set()).add(
                row.ground_truth_fingerprint
            )
        self.assertTrue(all(len(values) == 1 for values in by_replication.values()))

    def test_export_contains_ground_truth_audit_and_repeated_results(self):
        with TemporaryDirectory() as directory:
            written = export_repeated_experiment(directory, self.experiment)

            self.assertEqual(len(written), 18)
            self.assertTrue(Path(directory, "experiment_report.md").exists())
            self.assertTrue(Path(directory, "paired_comparisons.csv").exists())
            self.assertTrue(Path(directory, "ground_truth_services.csv").exists())
            lines = Path(directory, "representative_patient_paths.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(lines), 5 * 15)


if __name__ == "__main__":
    unittest.main()
