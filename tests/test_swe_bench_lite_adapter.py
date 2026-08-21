import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from claw.benchmark.swe_bench_lite_adapter import (
    FORBIDDEN_AGENT_FIELDS,
    LocalSweBenchLiteCalibrationRunner,
    SweBenchLiteEpisodeTaskMaterializer,
    SweBenchLiteLocalCalibrationResult,
    SweBenchLiteTestExecution,
    SweBenchLiteDevAdapter,
    _normalize_pytest_node_ids,
    evaluate_swe_bench_lite_candidate,
)


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = ROOT / "benchmarks" / "swe_bench_lite"


def _canonical_text_sha256(path: Path) -> str:
    content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class SweBenchLiteDevAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = SweBenchLiteDevAdapter(BENCHMARK_ROOT)

    def test_truncated_parameter_node_id_expands_to_full_function(self):
        normalized, changes = _normalize_pytest_node_ids(
            [
                "test/example_test.py::test_plain",
                "test/example_test.py::test_case[incomplete:",
                "test/example_test.py::test_case[incomplete:",
                "test/example_test.py::test_nested[value[index]",
                "test/example_test.py::test_cast[value::TYPE",
                "test/example_test.py::test_other[complete]",
            ]
        )
        self.assertEqual(changes, 4)
        self.assertEqual(
            normalized,
            [
                "test/example_test.py::test_plain",
                "test/example_test.py::test_case",
                "test/example_test.py::test_nested",
                "test/example_test.py::test_cast",
                "test/example_test.py::test_other[complete]",
            ],
        )

    def test_agent_tasks_match_selected_snapshots_without_private_fields(self):
        tasks = self.adapter.load_agent_tasks()
        selection = json.loads(
            (BENCHMARK_ROOT / "pilot-selection.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(tasks), len(selection["selected"]))
        for task in tasks:
            payload = task.to_agent_payload()
            self.assertFalse(FORBIDDEN_AGENT_FIELDS.intersection(payload))
            self.assertEqual(len(task.base_commit), 40)
            self.assertEqual(len(task.dataset_revision), 40)
            self.assertTrue(task.workspace_path.is_dir())

    def test_evaluator_script_imports_claw_outside_project_cwd(self):
        script = ROOT / "tools" / "evaluate_swe_bench_lite_candidate.py"
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=directory,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--asset", completed.stdout)

    def test_private_patch_content_never_appears_in_agent_payload(self):
        for task in self.adapter.load_agent_tasks():
            public_json = json.dumps(task.to_agent_payload(), sort_keys=True)
            private = self.adapter.load_evaluation_bundle(task.instance_id)
            self.assertNotIn(private.patch, public_json)
            self.assertNotIn(private.test_patch, public_json)
            if private.hints_text:
                self.assertNotIn(private.hints_text, public_json)

    def test_evaluation_fingerprint_contains_hashes_not_secrets(self):
        for task in self.adapter.load_agent_tasks():
            private = self.adapter.load_evaluation_bundle(task.instance_id)
            fingerprint = private.to_fingerprint()
            serialized = json.dumps(fingerprint, sort_keys=True)
            self.assertEqual(len(fingerprint["patch_sha256"]), 64)
            self.assertEqual(len(fingerprint["test_patch_sha256"]), 64)
            self.assertEqual(len(fingerprint["test_ids_hash"]), 64)
            self.assertNotIn(private.patch, serialized)
            self.assertNotIn(private.test_patch, serialized)

    def test_unknown_instance_cannot_open_evaluator_boundary(self):
        with self.assertRaises(KeyError):
            self.adapter.load_evaluation_bundle("unknown__repo-1")

    def test_patch_is_applied_inside_parent_git_repository(self):
        session_root = ROOT / ".port_sessions"
        session_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=session_root) as temporary:
            workspace = Path(temporary)
            target = workspace / "sample.txt"
            target.write_text("before\n", encoding="utf-8")
            patch = (
                "diff --git a/sample.txt b/sample.txt\n"
                "--- a/sample.txt\n"
                "+++ b/sample.txt\n"
                "@@ -1 +1 @@\n"
                "-before\n"
                "+after\n"
            )
            LocalSweBenchLiteCalibrationRunner()._apply_patch(
                workspace, patch, label="test patch"
            )
            self.assertEqual(target.read_text(encoding="utf-8"), "after\n")

    def test_local_calibration_requires_expected_baseline_and_reference_results(self):
        def run(group, returncode):
            return SweBenchLiteTestExecution(
                group=group,
                returncode=returncode,
                test_count=1,
                duration_seconds=0.1,
                stdout_sha256="a" * 64,
                stderr_sha256="b" * 64,
            )

        result = SweBenchLiteLocalCalibrationResult(
            instance_id="repo__project-1",
            dataset_revision="c" * 40,
            base_commit="d" * 40,
            python_version="Python 3.8.20",
            evaluator_fingerprint={"patch_sha256": "e" * 64},
            baseline_fail_to_pass=run("baseline_fail_to_pass", 1),
            baseline_pass_to_pass=run("baseline_pass_to_pass", 0),
            reference_fail_to_pass=run("reference_fail_to_pass", 0),
            reference_pass_to_pass=run("reference_pass_to_pass", 0),
        )
        evidence = result.to_evidence()
        self.assertTrue(result.passed)
        self.assertEqual(evidence["status"], "passed")
        self.assertFalse(evidence["environment"]["official_swebench_harness"])
        self.assertIn("not an official SWE-bench score", evidence["claim_boundary"])

    def test_versioned_local_calibration_evidence_has_honest_claim_boundary(self):
        filenames = (
            "swe-bench-lite-marshmallow-local-calibration.json",
            "swe-bench-lite-astroid-local-calibration.json",
        )
        for filename in filenames:
            with self.subTest(filename=filename):
                evidence = json.loads(
                    (ROOT / "configs" / "integrations" / filename).read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(evidence["status"], "passed")
                self.assertFalse(
                    evidence["environment"]["official_swebench_harness"]
                )
                self.assertFalse(
                    evidence["runs"]["baseline_fail_to_pass"]["passed"]
                )
                self.assertTrue(
                    evidence["runs"]["baseline_pass_to_pass"]["passed"]
                )
                self.assertTrue(
                    evidence["runs"]["reference_fail_to_pass"]["passed"]
                )
                self.assertTrue(
                    evidence["runs"]["reference_pass_to_pass"]["passed"]
                )
                self.assertIn(
                    "not an official SWE-bench score", evidence["claim_boundary"]
                )

    def test_real_rollout_evidence_rejects_test_passing_process_failure_from_gold(self):
        evidence = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-marshmallow-deepseek-rollouts.json"
            ).read_text(encoding="utf-8")
        )
        guided = evidence["rollouts"][1]
        self.assertFalse(evidence["official_swebench_harness"])
        self.assertFalse(guided["success"])
        self.assertEqual(guided["test_pass_rate"], 1.0)
        self.assertEqual(guided["termination"], "max_turns")
        self.assertFalse(evidence["data_admission"]["gold_sft_eligible"])
        self.assertFalse(
            evidence["data_admission"]["contains_training_effect_claim"]
        )

    def test_astroid_rollout_evidence_separates_infrastructure_and_model_failure(self):
        evidence = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-astroid-deepseek-rollouts.json"
            ).read_text(encoding="utf-8")
        )
        interrupted, retry = evidence["attempts"]
        self.assertFalse(evidence["official_swebench_harness"])
        self.assertTrue(evidence["generation_worktree_dirty"])
        self.assertFalse(interrupted["valid_model_quality_sample"])
        self.assertEqual(interrupted["termination"], "runtime_schema_error")
        self.assertTrue(retry["valid_model_quality_sample"])
        self.assertFalse(retry["fail_to_pass_passed"])
        self.assertTrue(retry["pass_to_pass_passed"])
        self.assertTrue(retry["completion_reminder_recorded"])
        self.assertEqual(retry["termination"], "max_turns")
        self.assertFalse(evidence["data_admission"]["gold_sft_eligible"])
        self.assertFalse(
            evidence["data_admission"]["contains_training_effect_claim"]
        )

    def test_runtime_remediation_evidence_preserves_failures_and_claim_boundary(self):
        evidence = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-runtime-remediation.json"
            ).read_text(encoding="utf-8")
        )
        self.assertGreaterEqual(len(evidence["observed_failures"]), 5)
        self.assertIn(
            "fail_closed_workspace_import_preflight",
            evidence["implemented_changes"],
        )
        self.assertEqual(
            evidence["verification"]["targeted_tests"]["failed"], 0
        )
        self.assertEqual(
            len(evidence["verification"]["historical_environment_probes"]),
            2,
        )
        self.assertFalse(evidence["contains_training_effect_claim"])
        self.assertIn("not an official SWE-bench score", evidence["claim_boundary"])

    def test_marshmallow_remediation_evidence_keeps_process_gate_separate(self):
        evidence = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-marshmallow-remediation-rollouts.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["status"], "completed_without_compliant_success")
        self.assertTrue(evidence["generation_worktree_dirty"])
        self.assertFalse(evidence["official_swebench_harness"])
        attempts = evidence["attempts"]
        self.assertFalse(attempts[0]["valid_model_quality_sample"])
        self.assertEqual(attempts[0]["failure"], "configured_python_missing_pytest")
        self.assertEqual(attempts[1]["test_pass_rate"], 1.0)
        self.assertEqual(attempts[1]["termination"], "api_transport_failure")
        self.assertTrue(attempts[2]["valid_model_quality_sample"])
        self.assertEqual(attempts[2]["test_pass_rate"], 1.0)
        self.assertEqual(attempts[2]["termination"], "max_turns")
        self.assertFalse(evidence["data_admission"]["gold_sft_eligible"])
        self.assertIn("not an official SWE-bench score", evidence["claim_boundary"])

    def test_bounded_action_success_evidence_keeps_causal_boundary_explicit(self):
        evidence = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-marshmallow-bounded-action-success.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["status"], "local_dev_episode_success")
        self.assertFalse(evidence["official_swebench_harness"])
        self.assertTrue(evidence["verification"]["hard_gate_passed"])
        self.assertTrue(evidence["verification"]["fail_to_pass"]["passed"])
        self.assertTrue(evidence["verification"]["pass_to_pass"]["passed"])
        self.assertTrue(evidence["verification"]["diff_scope_passed"])
        self.assertTrue(
            evidence["verification"]["termination_compliance_passed"]
        )
        self.assertIsNone(
            evidence["observed_behavior"]["implementation_escalation_turn"]
        )
        self.assertIsNone(
            evidence["observed_behavior"]["completion_critical_turn"]
        )
        self.assertIn(
            "does not estimate",
            evidence["policy_interpretation"]["causal_boundary"],
        )
        self.assertIn("not an official", evidence["claim_boundary"])

    def test_fresh_marshmallow_replication_preserves_failed_hard_gate(self):
        calibration = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-marshmallow-1359-local-calibration.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(calibration["status"], "passed")
        self.assertFalse(calibration["verification"]["baseline_fail_to_pass"])
        self.assertTrue(calibration["verification"]["reference_fail_to_pass"])
        self.assertFalse(calibration["environment"]["official_swebench_harness"])

        evidence = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-marshmallow-1359-bounded-action-rollouts.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["status"], "fresh_task_replication_failed")
        self.assertFalse(evidence["official_swebench_harness"])
        self.assertEqual(len(evidence["attempts"]), 2)
        self.assertIsNone(
            evidence["attempts"][0]["behavior"]["implementation_escalation_turn"]
        )
        self.assertTrue(
            evidence["attempts"][1]["behavior"][
                "forced_direct_mutation_request_triggered"
            ]
        )
        for attempt in evidence["attempts"]:
            self.assertFalse(attempt["verification"]["hard_gate_passed"])
            self.assertFalse(attempt["verification"]["fail_to_pass"])
            self.assertTrue(attempt["verification"]["pass_to_pass"])
            self.assertTrue(attempt["verification"]["termination_compliance_passed"])
        self.assertFalse(evidence["data_admission"]["gold_sft_eligible"])
        self.assertIn("not an official", evidence["claim_boundary"])

    def test_preregistered_pyvista_comparison_preserves_null_correctness_result(self):
        protocol = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-bounded-action-confirmatory-protocol.json"
            ).read_text(encoding="utf-8")
        )
        protocol_path = ROOT / protocol["protocol_document"]
        self.assertEqual(
            _canonical_text_sha256(protocol_path),
            protocol["protocol_sha256"],
        )
        self.assertEqual(protocol["maximum_valid_episodes_per_arm"], 1)
        self.assertEqual(protocol["quality_retries"], 0)
        self.assertTrue(protocol["analysis_after_both_arms"])

        calibration = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-pyvista-4315-local-calibration.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(calibration["model_calls_before_admission"], 0)
        admitted = calibration["calibration_attempts"][-1]
        self.assertTrue(admitted["baseline_pass_to_pass"])
        self.assertTrue(admitted["reference_fail_to_pass"])
        self.assertTrue(admitted["reference_pass_to_pass"])

        result = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-pyvista-4315-confirmatory-comparison.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            result["status"],
            "both_arms_success_no_correctness_gain_demonstrated",
        )
        self.assertEqual(result["protocol_sha256"], protocol["protocol_sha256"])
        self.assertEqual(len(result["arms"]), 2)
        self.assertFalse(result["arms"][0]["post_edit_contract_guidance"])
        self.assertTrue(result["arms"][1]["post_edit_contract_guidance"])
        for arm in result["arms"]:
            self.assertTrue(arm["verification"]["hard_gate_passed"])
            self.assertTrue(arm["verification"]["fail_to_pass"])
            self.assertTrue(arm["verification"]["pass_to_pass"])
        control_quality = result["arms"][0]["verification"][
            "final_response_quality_advisory"
        ]
        treatment_quality = result["arms"][1]["verification"][
            "final_response_quality_advisory"
        ]
        self.assertEqual(control_quality["status"], "fail")
        self.assertEqual(
            control_quality["classification"], "raw_tool_call_markup"
        )
        self.assertEqual(treatment_quality["status"], "pass")
        self.assertEqual(
            treatment_quality["classification"], "user_facing_text"
        )
        self.assertEqual(
            result["interpretation"]["preregistered_matrix_cell"],
            "control_pass_treatment_pass",
        )
        self.assertIn("no correctness improvement", result["claim_boundary"])

    def test_progressive_action_constraint_protocol_is_frozen_before_runs(self):
        protocol = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-progressive-action-constraint-protocol.json"
            ).read_text(encoding="utf-8")
        )
        protocol_path = ROOT / protocol["protocol_document"]
        self.assertEqual(
            _canonical_text_sha256(protocol_path),
            protocol["protocol_sha256"],
        )
        self.assertEqual(protocol["maximum_valid_episodes"], 4)
        self.assertEqual(protocol["quality_retries"], 0)
        self.assertEqual(
            protocol["arms"]["strict"]["implementation_target_read_allowance"],
            0,
        )
        self.assertEqual(
            protocol["arms"]["progressive"][
                "implementation_target_read_allowance"
            ],
            1,
        )
        self.assertEqual(
            protocol["instances"][0]["arm_order"],
            ["strict", "progressive"],
        )
        self.assertEqual(
            protocol["instances"][1]["arm_order"],
            ["progressive", "strict"],
        )
        for name, regressions in (
            ("astroid-1978", 12),
            ("pydicom-1256", 22),
        ):
            calibration = json.loads(
                (
                    ROOT
                    / "configs"
                    / "integrations"
                    / f"swe-bench-lite-{name}-local-calibration.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(calibration["model_calls_before_admission"], 0)
            self.assertTrue(
                calibration["repository_acquisition"]["final_head_verified"]
            )
            self.assertFalse(
                calibration["runs"]["baseline_fail_to_pass"]["passed"]
            )
            self.assertTrue(
                calibration["runs"]["baseline_pass_to_pass"]["passed"]
            )
            self.assertTrue(
                calibration["runs"]["reference_fail_to_pass"]["passed"]
            )
            self.assertTrue(
                calibration["runs"]["reference_pass_to_pass"]["passed"]
            )
            self.assertEqual(
                calibration["evaluator_fingerprint"]["pass_to_pass_count"],
                regressions,
            )

    def test_progressive_action_constraint_result_preserves_inconclusive_pairs(
        self,
    ):
        protocol = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-progressive-action-constraint-protocol.json"
            ).read_text(encoding="utf-8")
        )
        result = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-progressive-action-constraint-result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(result["protocol_sha256"], protocol["protocol_sha256"])
        self.assertEqual(
            result["schema_version"],
            "progressive_action_constraint_result.v1",
        )
        self.assertEqual(result["status"], "completed_4_of_4_valid_episodes")
        self.assertEqual(len(result["completed_arms"]), 4)
        strict, progressive, pydicom_progressive, pydicom_strict = result[
            "completed_arms"
        ]
        self.assertEqual(strict["instance_id"], "pylint-dev__astroid-1978")
        self.assertEqual(strict["arm"], "strict")
        self.assertFalse(strict["success"])
        self.assertFalse(strict["verification"]["fail_to_pass"])
        self.assertTrue(strict["verification"]["pass_to_pass"])
        self.assertIsNone(
            strict["behavior_v4"]["implementation_escalation_turn"]
        )
        self.assertFalse(
            strict["failure_analysis"]["action_constraint_failure"]
        )
        self.assertEqual(progressive["arm"], "progressive")
        self.assertEqual(
            progressive["behavior_v4"]["implementation_escalation_turn"], 6
        )
        self.assertTrue(
            progressive["progressive_constraint"]["target_read_option_offered"]
        )
        self.assertFalse(
            progressive["progressive_constraint"][
                "target_read_allowance_consumed"
            ]
        )
        self.assertEqual(
            result["completed_pair_summaries"][0]["protocol_interpretation"],
            "inconclusive_identical_hard_outcomes",
        )
        self.assertEqual(pydicom_progressive["arm"], "progressive")
        self.assertTrue(
            pydicom_progressive["progressive_constraint"][
                "target_read_allowance_consumed"
            ]
        )
        self.assertEqual(pydicom_strict["arm"], "strict")
        self.assertEqual(
            result["completed_pair_summaries"][1]["protocol_interpretation"],
            "inconclusive_progressive_delayed_but_did_not_eliminate_constraint_stop",
        )
        self.assertEqual(result["aggregate"]["valid_episodes"], 4)
        self.assertEqual(result["aggregate"]["hard_gate_successes"], 0)
        self.assertFalse(result["decision"]["prefer_progressive"])
        self.assertEqual(
            result["decision"]["result"], "inconclusive_keep_existing_default"
        )
        self.assertIn("Completed preregistered", result["claim_boundary"])

    def test_read_to_edit_repair_protocol_is_frozen_before_runs(self):
        protocol = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-read-to-edit-repair-protocol.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            _canonical_text_sha256(ROOT / protocol["protocol_document"]),
            protocol["protocol_sha256"],
        )
        self.assertEqual(
            _canonical_text_sha256(ROOT / protocol["design_document"]),
            protocol["design_sha256"],
        )
        self.assertEqual(
            _canonical_text_sha256(ROOT / protocol["source_evidence"]),
            protocol["source_evidence_sha256"],
        )
        self.assertEqual(protocol["maximum_valid_episodes"], 4)
        self.assertEqual(protocol["quality_retries"], 0)
        self.assertEqual(
            protocol["shared_policy"]["implementation_target_read_allowance"],
            1,
        )
        self.assertEqual(
            protocol["arms"]["control"][
                "implementation_constraint_repair_attempts"
            ],
            0,
        )
        self.assertEqual(
            protocol["arms"]["repair"][
                "implementation_constraint_repair_attempts"
            ],
            1,
        )
        self.assertEqual(
            [item["instance_id"] for item in protocol["instances"]],
            [
                "sqlfluff__sqlfluff-1517",
                "pylint-dev__astroid-1866",
            ],
        )
        self.assertEqual(
            protocol["instances"][0]["arm_order"], ["control", "repair"]
        )
        self.assertEqual(
            protocol["instances"][1]["arm_order"], ["repair", "control"]
        )

    def test_read_to_edit_admissible_protocol_preserves_v1_closure(self):
        protocol = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-read-to-edit-repair-admissible-protocol.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            _canonical_text_sha256(ROOT / protocol["protocol_document"]),
            protocol["protocol_sha256"],
        )
        self.assertEqual(
            _canonical_text_sha256(ROOT / protocol["design_document"]),
            protocol["design_sha256"],
        )
        self.assertEqual(
            _canonical_text_sha256(
                ROOT / protocol["predecessor_calibration_result"]
            ),
            protocol["predecessor_calibration_sha256"],
        )
        self.assertEqual(protocol["predecessor_model_calls"], 0)
        self.assertEqual(
            protocol["shared_policy"]["prompt_version"],
            "swe-bench-lite-dev.deepseek-v4-flash.v1",
        )
        self.assertTrue(
            protocol["selection_rule"][
                "exclude_visibly_truncated_parameter_ids"
            ]
        )
        self.assertEqual(
            [item["instance_id"] for item in protocol["instances"]],
            [
                "pylint-dev__astroid-1268",
                "pydicom__pydicom-1694",
            ],
        )
        self.assertEqual(
            protocol["instances"][0]["arm_order"], ["control", "repair"]
        )
        self.assertEqual(
            protocol["instances"][1]["arm_order"], ["repair", "control"]
        )

    def test_read_to_edit_admissible_calibration_separates_observation_from_effect(self):
        result = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-read-to-edit-repair-admissible-calibration-result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(result["status"], "admitted")
        self.assertEqual(result["model_calls_before_admission"], 0)
        self.assertIn("no evidence", result["conclusion"]["not_supported"])
        for item in result["instances"]:
            observations = item["observations"]
            self.assertFalse(observations["baseline_fail_to_pass"]["passed"])
            self.assertTrue(observations["baseline_pass_to_pass"]["passed"])
            self.assertTrue(observations["reference_fail_to_pass"]["passed"])
            self.assertTrue(observations["reference_pass_to_pass"]["passed"])
            self.assertEqual(len(item["raw_evidence_sha256"]), 64)

    def test_multitask_replication_preserves_partial_result_and_tradeoff(self):
        protocol = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-bounded-action-multitask-protocol.json"
            ).read_text(encoding="utf-8")
        )
        protocol_path = ROOT / protocol["protocol_document"]
        self.assertEqual(
            _canonical_text_sha256(protocol_path),
            protocol["protocol_sha256"],
        )
        self.assertEqual(protocol["maximum_valid_episodes_per_task"], 1)
        self.assertEqual(protocol["quality_retries"], 0)

        result = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-bounded-action-multitask-result.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(result["status"], "partial_success_1_of_2")
        self.assertEqual(result["protocol_sha256"], protocol["protocol_sha256"])
        self.assertTrue(result["admission"]["all_tasks_admitted"])
        self.assertEqual(result["aggregate"]["hard_gate_successes"], 1)
        self.assertEqual(result["aggregate"]["hard_gate_success_rate"], 0.5)
        pvlib, sqlfluff = result["episodes"]
        self.assertTrue(pvlib["success"])
        self.assertFalse(sqlfluff["success"])
        self.assertEqual(
            sqlfluff["failure_analysis"][
                "primary_category_after_deterministic_replay"
            ],
            "action_constraint_violation",
        )
        self.assertEqual(sqlfluff["behavior_v4"]["first_target_path_turn"], 7)
        self.assertEqual(sqlfluff["behavior_v4"]["rejected_tool_calls"], 1)
        self.assertIn("not an official", result["claim_boundary"])

    def test_sqlfluff_calibration_records_conservative_node_id_policy(self):
        evidence = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-sqlfluff-local-calibration.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["status"], "passed")
        self.assertFalse(evidence["environment"]["official_swebench_harness"])
        self.assertEqual(
            evidence["node_id_policy"]["coverage_effect"],
            "conservative_superset",
        )
        self.assertEqual(
            evidence["node_id_policy"]["normalized_pass_to_pass_ids"], 1
        )
        self.assertFalse(evidence["runs"]["baseline_fail_to_pass"]["passed"])
        self.assertTrue(evidence["runs"]["reference_fail_to_pass"]["passed"])
        self.assertIn("not an official SWE-bench score", evidence["claim_boundary"])

    def test_sqlfluff_rollout_separates_original_and_supplemental_evaluation(self):
        evidence = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-sqlfluff-deepseek-rollout.json"
            ).read_text(encoding="utf-8")
        )
        prepare_failure, rollout = evidence["attempts"]
        self.assertEqual(prepare_failure["model_calls"], 0)
        self.assertTrue(rollout["valid_model_quality_sample"])
        self.assertEqual(rollout["changed_files"], [])
        self.assertEqual(rollout["termination"], "max_turns")
        self.assertEqual(
            rollout["original_verification"]["status"], "evaluation_error"
        )
        supplemental = rollout["supplemental_offline_evaluation"]
        self.assertFalse(supplemental["fail_to_pass_passed"])
        self.assertTrue(supplemental["pass_to_pass_passed"])
        self.assertFalse(evidence["data_admission"]["gold_sft_eligible"])

    def test_pydicom_deadline_evidence_preserves_infrastructure_failure(self):
        calibration = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-pydicom-local-calibration.json"
            ).read_text(encoding="utf-8")
        )
        rollout = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-pydicom-deadline-rollout.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            calibration["status"], "passed_after_environment_remediation"
        )
        self.assertFalse(calibration["runs"]["baseline_fail_to_pass"]["passed"])
        self.assertTrue(calibration["runs"]["reference_pass_to_pass"]["passed"])
        self.assertTrue(rollout["original_verification"]["preserved"])
        self.assertFalse(
            rollout["supplemental_offline_evaluation"]["fail_to_pass_passed"]
        )
        self.assertFalse(rollout["policy_assessment"]["effectiveness_verified"])
        self.assertFalse(rollout["data_admission"]["gold_sft_eligible"])

    def test_pvlib_escalation_evidence_separates_timing_and_quality(self):
        calibration = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-pvlib-local-calibration.json"
            ).read_text(encoding="utf-8")
        )
        rollout = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-pvlib-escalation-rollout.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            calibration["status"], "passed_after_environment_remediation"
        )
        self.assertFalse(calibration["runs"]["baseline_fail_to_pass"]["passed"])
        self.assertTrue(calibration["runs"]["reference_pass_to_pass"]["passed"])
        self.assertEqual(
            rollout["behavior_diagnostics"]["implementation_escalation_turn"], 12
        )
        self.assertEqual(
            rollout["behavior_diagnostics"]["first_direct_mutation_turn"], 13
        )
        self.assertTrue(
            rollout["policy_assessment"]["edit_on_next_model_turn_after_escalation"]
        )
        self.assertFalse(rollout["policy_assessment"]["effectiveness_verified"])
        self.assertFalse(rollout["verification"]["pass_to_pass_passed"])
        self.assertFalse(rollout["data_admission"]["gold_sft_eligible"])

    def test_pydicom_1413_evidence_preserves_both_failure_classes(self):
        calibration = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-pydicom-1413-local-calibration.json"
            ).read_text(encoding="utf-8")
        )
        rollout = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-pydicom-1413-post-edit-rollouts.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(calibration["status"], "passed")
        self.assertEqual(
            calibration["runs"]["reference_pass_to_pass"]["test_count"], 301
        )
        self.assertEqual(
            rollout["attempts"][0]["classification"], "infrastructure_failure"
        )
        self.assertEqual(
            rollout["attempts"][1]["classification"], "valid_model_bad_case"
        )
        self.assertFalse(rollout["attempts"][1]["target_source_changed"])
        self.assertFalse(rollout["data_admission"]["gold_sft_eligible"])
        self.assertIn("not an official SWE-bench score", rollout["claim_boundary"])

    def test_astroid_1333_evidence_keeps_runtime_stop_failure_explicit(self):
        calibration = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-astroid-1333-local-calibration.json"
            ).read_text(encoding="utf-8")
        )
        rollout = json.loads(
            (
                ROOT
                / "configs"
                / "integrations"
                / "swe-bench-lite-astroid-1333-path-scoped-rollout.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            calibration["status"], "passed_after_environment_reconstruction"
        )
        self.assertEqual(
            calibration["runs"]["reference_pass_to_pass"]["test_count"], 46
        )
        self.assertEqual(
            rollout["observed_behavior"]["final_provider_finish_reason"],
            "max_tokens",
        )
        self.assertIsNone(
            rollout["observed_behavior"]["post_edit_contract_guidance_turn"]
        )
        self.assertTrue(
            rollout["infrastructure_failure"]["original_episode_preserved"]
        )
        self.assertFalse(rollout["infrastructure_failure"]["rerun_performed"])
        self.assertFalse(rollout["data_admission"]["gold_sft_eligible"])

    def test_episode_materialization_keeps_private_assets_out_of_agent_payload(self):
        task = {
            item.instance_id: item for item in self.adapter.load_agent_tasks()
        }["marshmallow-code__marshmallow-1343"]
        bundle = self.adapter.load_evaluation_bundle(task.instance_id)
        evaluator_script = ROOT / "tools" / "evaluate_swe_bench_lite_candidate.py"
        with tempfile.TemporaryDirectory() as temporary:
            materialized = SweBenchLiteEpisodeTaskMaterializer(
                evaluator_script
            ).materialize(
                task,
                bundle,
                output_root=Path(temporary) / "materialized",
                python_executable=sys.executable,
            )
            public = json.dumps(task.to_agent_payload(), sort_keys=True)
            task_spec = json.dumps(materialized.task_spec.to_dict(), sort_keys=True)
            private_asset = (
                materialized.evaluator_assets_root / "evaluation.json"
            ).read_text(encoding="utf-8")
            private_payload = json.loads(private_asset)
            self.assertNotIn(bundle.patch, public)
            self.assertNotIn(bundle.test_patch, public)
            self.assertNotIn(bundle.patch, task_spec)
            self.assertNotIn(bundle.test_patch, task_spec)
            self.assertIn("make the smallest correct source change", task_spec)
            self.assertIn("do not browse the web", task_spec)
            self.assertNotIn(bundle.patch, private_asset)
            if bundle.hints_text:
                self.assertNotIn(bundle.hints_text, private_asset)
            self.assertEqual(private_payload["test_patch"], bundle.test_patch)
            self.assertFalse((materialized.template_root / ".git").exists())

    def test_candidate_evaluation_uses_temp_copy_and_preserves_episode_workspace(self):
        test_patch = (
            "diff --git a/tests/test_regression.py b/tests/test_regression.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/tests/test_regression.py\n"
            "@@ -0,0 +1 @@\n"
            "+# hidden regression\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "candidate"
            workspace.mkdir()
            (workspace / "pkg.py").write_text("VALUE = 'wrong'\n", encoding="utf-8")
            asset = root / "evaluation.json"
            asset.write_text(
                json.dumps(
                    {
                        "schema_version": "swe_bench_lite_episode_evaluator.v1",
                        "instance_id": "repo__project-1",
                        "dataset_revision": "a" * 40,
                        "test_patch": test_patch,
                        "fail_to_pass": ["hidden::regression"],
                        "pass_to_pass": ["public::existing"],
                    }
                ),
                encoding="utf-8",
            )

            def fake_run_tests(
                _runner,
                evaluation_root,
                _python,
                _test_ids,
                *,
                group,
                timeout_seconds,
            ):
                self.assertGreater(timeout_seconds, 0)
                self.assertTrue(
                    (evaluation_root / "tests" / "test_regression.py").is_file()
                )
                fixed = "fixed" in (evaluation_root / "pkg.py").read_text("utf-8")
                return SweBenchLiteTestExecution(
                    group=group,
                    returncode=0 if group == "pass_to_pass" or fixed else 1,
                    test_count=1,
                    duration_seconds=0.01,
                    stdout_sha256="a" * 64,
                    stderr_sha256="b" * 64,
                )

            with mock.patch.object(
                LocalSweBenchLiteCalibrationRunner,
                "_run_tests",
                new=fake_run_tests,
            ):
                baseline = evaluate_swe_bench_lite_candidate(
                    workspace,
                    asset,
                    python_executable=sys.executable,
                )
                self.assertFalse(baseline.passed)
                self.assertFalse((workspace / "tests").exists())
                (workspace / "pkg.py").write_text(
                    "VALUE = 'fixed'\n", encoding="utf-8"
                )
                fixed = evaluate_swe_bench_lite_candidate(
                    workspace,
                    asset,
                    python_executable=sys.executable,
                )
                self.assertTrue(fixed.passed)
                self.assertFalse((workspace / "tests").exists())


if __name__ == "__main__":
    unittest.main()
