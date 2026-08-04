import json
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
    evaluate_swe_bench_lite_candidate,
)


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = ROOT / "benchmarks" / "swe_bench_lite"


class SweBenchLiteDevAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = SweBenchLiteDevAdapter(BENCHMARK_ROOT)

    def test_agent_tasks_match_selected_snapshots_without_private_fields(self):
        tasks = self.adapter.load_agent_tasks()
        self.assertEqual(len(tasks), 3)
        for task in tasks:
            payload = task.to_agent_payload()
            self.assertFalse(FORBIDDEN_AGENT_FIELDS.intersection(payload))
            self.assertEqual(len(task.base_commit), 40)
            self.assertEqual(len(task.dataset_revision), 40)
            self.assertTrue(task.workspace_path.is_dir())

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
