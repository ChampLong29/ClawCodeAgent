from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claw.benchmark.runner import BenchmarkError
from claw.data_pipeline.swe_bench_runtime_comparison import (
    resolve_claw_ablation_profile,
    run_swe_bench_lite_runtime_ablation,
    run_swe_bench_lite_runtime_comparison,
)


class SweBenchRuntimeComparisonTests(unittest.TestCase):
    def test_runtime_environment_manifest_covers_frozen_pilot(self):
        repository = Path(__file__).resolve().parents[1]
        plan = json.loads(
            (
                repository
                / "configs/integrations/swe-bench-lite-pi-claw-ablation-plan-v1.json"
            ).read_text(encoding="utf-8")
        )
        environments = json.loads(
            (
                repository
                / "configs/integrations/swe-bench-lite-runtime-environments-v1.json"
            ).read_text(encoding="utf-8")
        )
        task_ids = {task["instance_id"] for task in plan["tasks"]}

        self.assertEqual(set(environments["tasks"]), task_ids)
        for task in environments["tasks"].values():
            self.assertRegex(task["image"], r"@sha256:[0-9a-f]{64}$")
            self.assertTrue(task["task_python"].startswith("/"))
            self.assertTrue(task["evaluator_python"].startswith("/"))
            self.assertTrue(task["allow_paths"])
        self.assertRegex(
            environments["pi"]["image"], r"@sha256:[0-9a-f]{64}$"
        )

    def test_v2_ablation_plan_preserves_v1_and_pins_reconstructable_pi(self):
        repository = Path(__file__).resolve().parents[1]
        v1_path = (
            repository
            / "configs/integrations/swe-bench-lite-pi-claw-ablation-plan-v1.json"
        )
        plan = json.loads(
            (
                repository
                / "configs/integrations/swe-bench-lite-pi-claw-ablation-plan-v2.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            plan["extends"]["sha256"],
            hashlib.sha256(v1_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            plan["pi_package"]["logical_runtime_version"], "pi@0.85.1"
        )
        self.assertRegex(
            plan["container_boundaries"]["pi_image"],
            r"@sha256:[0-9a-f]{64}$",
        )
        self.assertFalse(plan["admission"]["model_calls_started"])

    def test_v3_ablation_plan_excludes_diagnostic_v2_and_freezes_corrections(self):
        repository = Path(__file__).resolve().parents[1]
        v2_path = (
            repository
            / "configs/integrations/swe-bench-lite-pi-claw-ablation-plan-v2.json"
        )
        plan = json.loads(
            (
                repository
                / "configs/integrations/swe-bench-lite-pi-claw-ablation-plan-v3.json"
            ).read_text(encoding="utf-8")
        )
        admission = json.loads(
            (
                repository
                / "configs/integrations/"
                "swe-bench-lite-pi-claw-ablation-v3-admission.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            plan["extends"]["sha256"],
            hashlib.sha256(v2_path.read_bytes()).hexdigest(),
        )
        self.assertFalse(plan["retrospective_exclusions"]["aggregate_with_v3"])
        self.assertEqual(plan["corrected_controls"]["thinking_mode"], "disabled")
        self.assertEqual(
            plan["corrected_controls"]["verifier_version"],
            "verifier-policy.v3",
        )
        self.assertTrue(
            plan["claw_enhanced"]["force_direct_mutation_after_escalation"]
        )
        self.assertEqual(
            plan["admission_before_model_calls"]["docker_backend"], "required"
        )
        self.assertEqual(plan["status"], "ready_for_corrected_micro_task")
        self.assertTrue(admission["claw_shell"]["verified"])
        self.assertTrue(admission["provider_probe"]["verified"])
        self.assertFalse(admission["provider_probe"]["repository_content_sent"])

    def test_first_ablation_result_preserves_raw_and_budgeted_outcomes(self):
        repository = Path(__file__).resolve().parents[1]
        plan_path = (
            repository
            / "configs/integrations/swe-bench-lite-pi-claw-ablation-plan-v2.json"
        )
        result = json.loads(
            (
                repository
                / "configs/integrations/"
                "swe-bench-lite-pi-claw-ablation-marshmallow1343-result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            result["protocol"]["sha256"],
            hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        )
        self.assertFalse(result["first_run"]["claw_base"]["raw_resolved"])
        self.assertFalse(result["first_run"]["claw_enhanced"]["raw_resolved"])
        pi_retry = result["pi_infrastructure_retries"][-1]
        self.assertTrue(pi_retry["raw_resolved"])
        self.assertFalse(pi_retry["policy_compliant_resolved"])
        self.assertFalse(pi_retry["budgeted_resolved"])
        self.assertEqual(pi_retry["termination"], "max_total_tokens")
        self.assertEqual(result["pi_infrastructure_retries"][0]["model_calls"], 0)
        self.assertIn("not an official SWE-bench score", result["claim_boundary"])

    def test_second_ablation_result_preserves_infrastructure_continuation(self):
        repository = Path(__file__).resolve().parents[1]
        plan_path = (
            repository
            / "configs/integrations/swe-bench-lite-pi-claw-ablation-plan-v2.json"
        )
        result = json.loads(
            (
                repository
                / "configs/integrations/"
                "swe-bench-lite-pi-claw-ablation-astroid1196-result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            result["protocol"]["sha256"],
            hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        )
        self.assertIn(
            "Base Episode was not rerun",
            result["infrastructure_continuation"]["continuation_scope"],
        )
        self.assertFalse(result["results"]["claw_base"]["raw_resolved"])
        self.assertTrue(result["results"]["claw_enhanced"]["raw_resolved"])
        self.assertFalse(
            result["results"]["claw_enhanced"]["policy_compliant_resolved"]
        )
        self.assertFalse(result["results"]["pi_raw"]["raw_resolved"])
        self.assertIn("not an official SWE-bench score", result["claim_boundary"])

    def test_third_ablation_result_preserves_verifier_correction(self):
        repository = Path(__file__).resolve().parents[1]
        plan_path = (
            repository
            / "configs/integrations/swe-bench-lite-pi-claw-ablation-plan-v2.json"
        )
        environment_path = (
            repository
            / "configs/integrations/swe-bench-lite-runtime-environments-v1.json"
        )
        result = json.loads(
            (
                repository
                / "configs/integrations/"
                "swe-bench-lite-pi-claw-ablation-sqlfluff1763-result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            result["protocol"]["sha256"],
            hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            result["reproducibility_remediation"][
                "manifest_sha256_at_capture"
            ],
            hashlib.sha256(environment_path.read_bytes()).hexdigest(),
        )
        self.assertIn(
            "Base was not rerun",
            result["infrastructure_continuation"]["path_fix"],
        )
        for arm in ("claw_base", "claw_enhanced", "pi_raw"):
            self.assertFalse(result["results"][arm]["raw_resolved"])
            self.assertFalse(
                result["results"][arm]["policy_compliant_resolved"]
            )
            supplemental = result["supplemental_clean_verification"][arm]
            self.assertEqual(supplemental["pass_to_pass"], "66 passed, 0 failed")
            self.assertEqual(supplemental["fail_to_pass"], "0 passed, 3 failed")
        self.assertEqual(
            result["supplemental_clean_verification"]["model_calls"], 0
        )
        self.assertIn("not an official SWE-bench score", result["claim_boundary"])

    def test_fourth_ablation_result_preserves_partial_protocol_failure(self):
        repository = Path(__file__).resolve().parents[1]
        plan_path = (
            repository
            / "configs/integrations/swe-bench-lite-pi-claw-ablation-plan-v2.json"
        )
        environment_path = (
            repository
            / "configs/integrations/swe-bench-lite-runtime-environments-v1.json"
        )
        result = json.loads(
            (
                repository
                / "configs/integrations/"
                "swe-bench-lite-pi-claw-ablation-pydicom1139-result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            result["protocol"]["sha256"],
            hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            result["reproducibility"]["runtime_environment_manifest_sha256"],
            hashlib.sha256(environment_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(result["reproducibility"]["quality_retries"], 0)
        self.assertEqual(
            result["reproducibility"]["infrastructure_retries"], 0
        )
        for arm in ("claw_base", "claw_enhanced", "pi_raw"):
            arm_result = result["results"][arm]
            self.assertFalse(arm_result["raw_resolved"])
            self.assertFalse(arm_result["policy_compliant_resolved"])
            self.assertEqual(
                arm_result["pass_to_pass_group"], "38 passed, 0 failed"
            )
            self.assertEqual(
                arm_result["changed_files"], ["pydicom/valuerep.py"]
            )
        self.assertEqual(
            result["results"]["pi_raw"]["termination"], "max_total_tokens"
        )
        self.assertIn("not an official SWE-bench score", result["claim_boundary"])

    def test_fifth_ablation_result_preserves_termination_correction(self):
        repository = Path(__file__).resolve().parents[1]
        plan_path = (
            repository
            / "configs/integrations/swe-bench-lite-pi-claw-ablation-plan-v2.json"
        )
        environment_path = (
            repository
            / "configs/integrations/swe-bench-lite-runtime-environments-v1.json"
        )
        result = json.loads(
            (
                repository
                / "configs/integrations/"
                "swe-bench-lite-pi-claw-ablation-pvlib1707-result.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            result["protocol"]["sha256"],
            hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            result["reproducibility"]["runtime_environment_manifest_sha256"],
            hashlib.sha256(environment_path.read_bytes()).hexdigest(),
        )
        self.assertFalse(
            result["termination_semantics_correction"][
                "original_records_rewritten"
            ]
        )
        for arm in ("claw_base", "claw_enhanced", "pi_raw"):
            arm_result = result["results"][arm]
            self.assertFalse(arm_result["raw_resolved"])
            self.assertEqual(arm_result["pass_to_pass"], "30 passed, 0 failed")
            self.assertEqual(arm_result["changed_files"], [])
        self.assertEqual(
            result["results"]["claw_base"][
                "derived_bad_case_after_classifier_fix"
            ],
            "budget_or_timeout",
        )
        self.assertEqual(
            result["results"]["pi_raw"]["terminal_provider_finish_reason"],
            "length",
        )
        self.assertIn("not an official SWE-bench score", result["claim_boundary"])

    def test_versioned_ablation_plan_is_calibrated_selection_subset(self):
        repository = Path(__file__).resolve().parents[1]
        plan = json.loads(
            (
                repository
                / "configs/integrations/swe-bench-lite-pi-claw-ablation-plan-v1.json"
            ).read_text(encoding="utf-8")
        )
        selection = json.loads(
            (
                repository / "benchmarks/swe_bench_lite/pilot-selection.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(plan["status"], "pilot_ready")
        planned_ids = [task["instance_id"] for task in plan["tasks"]]
        selected_ids = [task["instance_id"] for task in selection["selected"]]
        self.assertEqual(len(planned_ids), len(set(planned_ids)))
        self.assertEqual(plan["pilot_gate"]["task_count"], len(planned_ids))
        self.assertTrue(set(planned_ids).issubset(selected_ids))
        self.assertEqual(
            planned_ids,
            [instance_id for instance_id in selected_ids if instance_id in planned_ids],
        )
        self.assertEqual(
            {
                key: plan["arms"]["claw_base"][key]
                for key in plan["arms"]["claw_base"]
                if key != "runtime"
            },
            {
                key: value
                for key, value in resolve_claw_ablation_profile("base").items()
                if key in plan["arms"]["claw_base"]
            },
        )
        self.assertEqual(
            {
                key: plan["arms"]["claw_enhanced"][key]
                for key in plan["arms"]["claw_enhanced"]
                if key != "runtime"
            },
            {
                key: value
                for key, value in resolve_claw_ablation_profile("enhanced").items()
                if key in plan["arms"]["claw_enhanced"]
            },
        )
        self.assertTrue(
            all(
                task["environment_status"].startswith("calibrated_")
                for task in plan["tasks"]
            )
        )

    def test_ablation_profiles_isolate_runtime_guidance(self):
        base = resolve_claw_ablation_profile("base")
        enhanced = resolve_claw_ablation_profile("enhanced")

        self.assertEqual(base["completion_reminder_turns"], 0)
        self.assertEqual(base["completion_critical_turns"], 0)
        self.assertEqual(base["implementation_deadline_turns"], 0)
        self.assertEqual(base["implementation_escalation_turns"], 0)
        self.assertFalse(base["post_edit_contract_guidance"])
        self.assertGreater(enhanced["completion_reminder_turns"], 0)
        self.assertGreater(enhanced["implementation_deadline_turns"], 0)
        self.assertGreater(enhanced["implementation_escalation_turns"], 0)
        self.assertTrue(enhanced["force_direct_mutation_after_escalation"])
        self.assertEqual(enhanced["implementation_target_read_allowance"], 1)
        self.assertEqual(enhanced["implementation_constraint_repair_attempts"], 1)
        self.assertTrue(enhanced["reject_repeated_readonly_actions"])
        self.assertEqual(enhanced["repeated_action_repair_attempts"], 1)
        self.assertTrue(enhanced["force_final_response_at_critical"])
        self.assertTrue(enhanced["post_edit_contract_guidance"])

        with self.assertRaisesRegex(BenchmarkError, "unknown Claw ablation profile"):
            resolve_claw_ablation_profile("experimental")

    def test_matched_runs_write_guarded_secret_free_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "pi"
            executable.write_text("", encoding="utf-8")
            output = root / "comparison"
            api = SimpleNamespace(
                model="deepseek-flash",
                api_key="do-not-persist",
                base_url="https://api.deepseek.com/anthropic",
            )
            calls = []

            def collect(**kwargs):
                calls.append(kwargs)
                destination = Path(kwargs["output_root"])
                destination.mkdir(parents=True)
                context = {
                    "evaluator_fingerprint": {"test_ids_hash": "same"},
                    "allowed_path_patterns": ["src/marshmallow/schema.py"],
                }
                environment = {
                    "schema_version": "swe_bench_environment_contract.v2",
                    "status": "passed",
                    "python_version": "3.8.20",
                }
                (destination / "evaluator-fingerprint.json").write_text(
                    json.dumps(context), encoding="utf-8"
                )
                (destination / "environment-contract.json").write_text(
                    json.dumps(environment), encoding="utf-8"
                )
                decoding = {
                    "temperature": kwargs["temperature"],
                    "max_turns": kwargs["max_turns"],
                    "max_total_tokens": kwargs["max_total_tokens"],
                    "completion_reminder_turns": kwargs["completion_reminder_turns"],
                    "completion_critical_turns": kwargs["completion_critical_turns"],
                    "implementation_deadline_turns": kwargs["implementation_deadline_turns"],
                    "implementation_escalation_turns": kwargs["implementation_escalation_turns"],
                    "post_edit_contract_guidance": kwargs["post_edit_contract_guidance"],
                    "implementation_path_patterns": list(
                        kwargs["allowed_path_patterns"]
                    ),
                    "max_tokens": kwargs["max_tokens"],
                }
                manifest = SimpleNamespace(
                    collection_id=f"collection-{len(calls)}",
                    task_ids=[kwargs["instance_id"]],
                    suite_id="swe-bench-lite-dev-pilot",
                    suite_version="dataset-revision",
                    suite_content_hash="same-suite",
                    model_ref=kwargs["model_ref"],
                    prompt_version=kwargs["prompt_version"],
                    verifier_version=kwargs["verifier_version"],
                    decoding_config=decoding,
                )
                episode = SimpleNamespace(
                    success=True,
                    test_pass_rate=1.0,
                    turns=3,
                    tool_calls=2,
                    input_tokens=100,
                    output_tokens=20,
                    latency_seconds=1.5,
                    format_valid=True,
                    process_violations=0,
                    bad_cases=[],
                    error=None,
                    behavior_diagnostics={},
                    trajectory_ref=destination / "trajectory.json",
                    verification_ref=destination / "verification.json",
                )
                return SimpleNamespace(
                    manifest=manifest,
                    episodes=[episode],
                    manifest_path=destination / "collection-manifest.json",
                )

            with patch(
                "claw.data_pipeline.swe_bench_runtime_comparison.APIConfigRuntime"
            ) as runtime, patch(
                "claw.data_pipeline.swe_bench_runtime_comparison."
                "collect_swe_bench_lite_dev_episode",
                side_effect=collect,
            ):
                runtime.return_value.get_config.return_value = api
                result = run_swe_bench_lite_runtime_comparison(
                    benchmark_root=root / "benchmark",
                    instance_id="marshmallow-code__marshmallow-1343",
                    python_executable=root / "python",
                    evaluator_script=root / "evaluator.py",
                    output_root=output,
                    generation_commit="commit",
                    api_config_root=root,
                    model_ref="deepseek-flash",
                    model_backend_version="v4-flash-9_10",
                    pi_executable=executable,
                    allowed_path_patterns=["src/marshmallow/schema.py"],
                )

            self.assertEqual(len(calls), 2)
            self.assertEqual(result["status"], "comparable")
            self.assertTrue(result["comparability"]["temperature_is_explicit"]["pi"])
            config = (output / "pi-runtime-config" / "models.json").read_text(
                encoding="utf-8"
            )
            self.assertEqual(
                (output / "pi-runtime-config" / "auth.json").read_text(
                    encoding="utf-8"
                ),
                "{}\n",
            )
            self.assertNotIn("do-not-persist", config)
            self.assertIn("$CLAW_PI_API_KEY", config)
            self.assertIn('"temperature": 0.0', config)
            self.assertTrue((output / "runtime-comparison.json").is_file())
            self.assertTrue((output / "report.md").is_file())
            self.assertEqual(
                result["schema_version"], "swe_bench_lite_runtime_comparison.v2"
            )

    def test_three_arm_ablation_collects_pi_once_and_writes_secret_free_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "pi"
            executable.write_text("", encoding="utf-8")
            output = root / "ablation"
            api = SimpleNamespace(
                model="deepseek-flash",
                api_key="do-not-persist",
                base_url="https://api.deepseek.com/anthropic",
            )
            calls = []

            def collect(**kwargs):
                calls.append(kwargs)
                destination = Path(kwargs["output_root"])
                destination.mkdir(parents=True)
                context = {
                    "evaluator_fingerprint": {"test_ids_hash": "same"},
                    "allowed_path_patterns": ["src/marshmallow/schema.py"],
                }
                environment = {
                    "schema_version": "swe_bench_environment_contract.v2",
                    "status": "passed",
                    "python_version": "3.8.20",
                }
                (destination / "evaluator-fingerprint.json").write_text(
                    json.dumps(context), encoding="utf-8"
                )
                (destination / "environment-contract.json").write_text(
                    json.dumps(environment), encoding="utf-8"
                )
                decoding = {
                    "temperature": kwargs["temperature"],
                    "max_turns": kwargs["max_turns"],
                    "max_total_tokens": kwargs["max_total_tokens"],
                    "completion_reminder_turns": kwargs["completion_reminder_turns"],
                    "completion_critical_turns": kwargs["completion_critical_turns"],
                    "implementation_deadline_turns": kwargs["implementation_deadline_turns"],
                    "implementation_escalation_turns": kwargs["implementation_escalation_turns"],
                    "force_direct_mutation_after_escalation": kwargs[
                        "force_direct_mutation_after_escalation"
                    ],
                    "implementation_target_read_allowance": kwargs[
                        "implementation_target_read_allowance"
                    ],
                    "implementation_constraint_repair_attempts": kwargs[
                        "implementation_constraint_repair_attempts"
                    ],
                    "reject_repeated_readonly_actions": kwargs[
                        "reject_repeated_readonly_actions"
                    ],
                    "repeated_action_repair_attempts": kwargs[
                        "repeated_action_repair_attempts"
                    ],
                    "force_final_response_at_critical": kwargs[
                        "force_final_response_at_critical"
                    ],
                    "post_edit_contract_guidance": kwargs["post_edit_contract_guidance"],
                    "implementation_path_patterns": list(
                        kwargs["allowed_path_patterns"]
                    ),
                    "max_tokens": kwargs["max_tokens"],
                    "thinking_mode": kwargs["thinking_mode"],
                }
                manifest = SimpleNamespace(
                    collection_id=f"collection-{len(calls)}",
                    task_ids=[kwargs["instance_id"]],
                    suite_id="swe-bench-lite-dev-pilot",
                    suite_version="dataset-revision",
                    suite_content_hash="same-suite",
                    model_ref=kwargs["model_ref"],
                    prompt_version=kwargs["prompt_version"],
                    verifier_version=kwargs["verifier_version"],
                    decoding_config=decoding,
                )
                episode = SimpleNamespace(
                    success=destination.name != "pi-raw",
                    test_pass_rate=1.0 if destination.name != "pi-raw" else 0.5,
                    turns=3,
                    tool_calls=2,
                    input_tokens=100,
                    output_tokens=20,
                    latency_seconds=1.5,
                    format_valid=True,
                    process_violations=0,
                    bad_cases=[],
                    error=None,
                    behavior_diagnostics={},
                    trajectory_ref=destination / "trajectory.json",
                    verification_ref=destination / "verification.json",
                )
                return SimpleNamespace(
                    manifest=manifest,
                    episodes=[episode],
                    manifest_path=destination / "collection-manifest.json",
                )

            with patch(
                "claw.data_pipeline.swe_bench_runtime_comparison.APIConfigRuntime"
            ) as runtime, patch(
                "claw.data_pipeline.swe_bench_runtime_comparison.OCIContainerRunner"
            ) as runner_class, patch(
                "claw.data_pipeline.swe_bench_runtime_comparison."
                "collect_swe_bench_lite_dev_episode",
                side_effect=collect,
            ):
                runtime.return_value.get_config.return_value = api
                runner_class.return_value.verify_disposable_workspace_contract.return_value = {
                    "status": "passed",
                    "candidate_snapshot_visible": True,
                    "shell_mutations_discarded": True,
                }
                result = run_swe_bench_lite_runtime_ablation(
                    benchmark_root=root / "benchmark",
                    instance_id="marshmallow-code__marshmallow-1343",
                    python_executable=root / "python",
                    evaluator_script=root / "evaluator.py",
                    output_root=output,
                    generation_commit="commit",
                    api_config_root=root,
                    model_ref="deepseek-flash",
                    model_backend_version="v4-flash-9_10",
                    pi_executable=executable,
                    allowed_path_patterns=["src/marshmallow/schema.py"],
                    claw_sandbox_backend="docker",
                    claw_sandbox_image=(
                        "claw/swe-pilot@sha256:" + "a" * 64
                    ),
                    claw_sandbox_python="/opt/task/bin/python3.8",
                    claw_sandbox_evaluator_python="/usr/local/bin/python",
                )

            self.assertEqual(len(calls), 3)
            for call in calls:
                self.assertTrue(Path(call["benchmark_root"]).is_absolute())
                self.assertTrue(Path(call["python_executable"]).is_absolute())
                self.assertTrue(Path(call["evaluator_script"]).is_absolute())
            self.assertEqual(
                [Path(call["output_root"]).name for call in calls],
                ["claw-base", "claw-enhanced", "pi-raw"],
            )
            self.assertEqual(calls[0]["implementation_deadline_turns"], 0)
            self.assertFalse(calls[0]["post_edit_contract_guidance"])
            self.assertEqual(calls[1]["implementation_deadline_turns"], 10)
            self.assertTrue(calls[1]["post_edit_contract_guidance"])
            self.assertFalse(calls[0]["force_direct_mutation_after_escalation"])
            self.assertTrue(calls[1]["force_direct_mutation_after_escalation"])
            self.assertTrue(calls[1]["reject_repeated_readonly_actions"])
            self.assertTrue(calls[1]["force_final_response_at_critical"])
            self.assertIs(calls[0]["agent_command_runner"], runner_class.return_value)
            self.assertIs(calls[1]["agent_command_runner"], runner_class.return_value)
            self.assertIsNone(calls[2].get("agent_command_runner"))
            self.assertEqual(
                [call.get("sandbox_backend_name") for call in calls],
                ["docker", "docker", "docker"],
            )
            self.assertEqual(
                [call.get("sandbox_image") for call in calls],
                [
                    "claw/swe-pilot@sha256:" + "a" * 64,
                    "claw/swe-pilot@sha256:" + "a" * 64,
                    "claw/swe-pilot@sha256:" + "a" * 64,
                ],
            )
            self.assertEqual(
                [call.get("sandbox_python_executable") for call in calls],
                [
                    "/opt/task/bin/python3.8",
                    "/opt/task/bin/python3.8",
                    "/opt/task/bin/python3.8",
                ],
            )
            self.assertEqual(
                [
                    call.get("sandbox_evaluator_python_executable")
                    for call in calls
                ],
                [
                    "/usr/local/bin/python",
                    "/usr/local/bin/python",
                    "/usr/local/bin/python",
                ],
            )
            self.assertEqual(
                [call.get("manage_agent_sandbox") for call in calls],
                [None, None, False],
            )
            self.assertEqual(
                {call["max_total_tokens"] for call in calls}, {250000}
            )
            self.assertEqual(result["status"], "comparable")
            self.assertEqual(
                result["controls"]["decoding_config"]["max_total_tokens"],
                250000,
            )
            self.assertEqual(
                result["controls"]["decoding_config"]["thinking_mode"],
                "disabled",
            )
            self.assertEqual(result["admission"]["claw_disposable_shell"]["status"], "passed")
            self.assertEqual(
                set(result["results"]), {"claw_base", "claw_enhanced", "pi_raw"}
            )
            self.assertTrue((output / "runtime-ablation.json").is_file())
            self.assertTrue((output / "report.md").is_file())
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output.rglob("*.json")
            )
            self.assertEqual(
                (output / "pi-runtime-config" / "auth.json").read_text(
                    encoding="utf-8"
                ),
                "{}\n",
            )
            self.assertNotIn("do-not-persist", persisted)

    def test_three_arm_ablation_fails_closed_on_unpinned_docker_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "pi"
            executable.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(BenchmarkError, "digest-pinned"):
                run_swe_bench_lite_runtime_ablation(
                    benchmark_root=root / "benchmark",
                    instance_id="unused",
                    python_executable=root / "python",
                    evaluator_script=root / "evaluator.py",
                    output_root=root / "output",
                    generation_commit="commit",
                    api_config_root=root,
                    model_ref="deepseek-flash",
                    model_backend_version="v4-flash-9_10",
                    pi_executable=executable,
                    allowed_path_patterns=["target.py"],
                    claw_sandbox_backend="docker",
                    claw_sandbox_image="claw/swe-pilot:latest",
                )

    def test_three_arm_ablation_fails_closed_on_unpinned_pi_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(BenchmarkError, "Pi Docker image"):
                run_swe_bench_lite_runtime_ablation(
                    benchmark_root=root / "benchmark",
                    instance_id="unused",
                    python_executable=root / "python",
                    evaluator_script=root / "evaluator.py",
                    output_root=root / "output",
                    generation_commit="commit",
                    api_config_root=root,
                    model_ref="deepseek-flash",
                    model_backend_version="v4-flash-9_10",
                    pi_executable=root / "unused-pi",
                    pi_docker_image="claw/pi:latest",
                    allowed_path_patterns=["target.py"],
                )

    def test_non_macos_pi_requires_explicit_sandbox_attestation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "pi"
            executable.write_text("", encoding="utf-8")
            with patch(
                "claw.data_pipeline.swe_bench_runtime_comparison.OCIContainerRunner"
            ) as runner_class:
                runner_class.return_value.verify_disposable_workspace_contract.return_value = {
                    "status": "passed"
                }
                with self.assertRaisesRegex(BenchmarkError, "sandbox_attestation"):
                    run_swe_bench_lite_runtime_ablation(
                        benchmark_root=root / "benchmark",
                        instance_id="unused",
                        python_executable=None,
                        evaluator_script=root / "evaluator.py",
                        output_root=root / "output",
                        generation_commit="commit",
                        api_config_root=root,
                        model_ref="deepseek-flash",
                        model_backend_version="v4-flash-9_10",
                        pi_executable=executable,
                        allowed_path_patterns=["target.py"],
                        enforce_macos_seatbelt=False,
                        claw_sandbox_backend="docker",
                        claw_sandbox_image=(
                            "claw/swe-pilot@sha256:" + "a" * 64
                        ),
                        claw_sandbox_python="/opt/task/bin/python3.8",
                    )


if __name__ == "__main__":
    unittest.main()
