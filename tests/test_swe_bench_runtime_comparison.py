from __future__ import annotations

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
                for key in resolve_claw_ablation_profile("base")
            },
            resolve_claw_ablation_profile("base"),
        )
        self.assertEqual(
            {
                key: plan["arms"]["claw_enhanced"][key]
                for key in resolve_claw_ablation_profile("enhanced")
            },
            resolve_claw_ablation_profile("enhanced"),
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
                "claw.data_pipeline.swe_bench_runtime_comparison."
                "collect_swe_bench_lite_dev_episode",
                side_effect=collect,
            ):
                runtime.return_value.get_config.return_value = api
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
                )

            self.assertEqual(len(calls), 3)
            self.assertEqual(
                [Path(call["output_root"]).name for call in calls],
                ["claw-base", "claw-enhanced", "pi-raw"],
            )
            self.assertEqual(calls[0]["implementation_deadline_turns"], 0)
            self.assertFalse(calls[0]["post_edit_contract_guidance"])
            self.assertEqual(calls[1]["implementation_deadline_turns"], 10)
            self.assertTrue(calls[1]["post_edit_contract_guidance"])
            self.assertEqual(
                {call["max_total_tokens"] for call in calls}, {250000}
            )
            self.assertEqual(result["status"], "comparable")
            self.assertEqual(
                result["controls"]["decoding_config"]["max_total_tokens"],
                250000,
            )
            self.assertEqual(
                set(result["results"]), {"claw_base", "claw_enhanced", "pi_raw"}
            )
            self.assertTrue((output / "runtime-ablation.json").is_file())
            self.assertTrue((output / "report.md").is_file())
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output.rglob("*.json")
            )
            self.assertNotIn("do-not-persist", persisted)


if __name__ == "__main__":
    unittest.main()
