from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claw.agent_runtime import LocalCodingAgent
from claw.agent_types import AgentPermissions, ModelConfig
from claw.benchmark import BenchmarkConfig, BenchmarkError, run_local_benchmark
from claw.benchmark.runner import BenchmarkRunner
from claw.main import main


class SequencedClient:
    def __init__(self, model, responses):
        self.model = model
        self.responses = list(responses)
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected model call")
        return self.responses.pop(0)


def write_response(content):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "call-write",
            "function": {
                "name": "write_file",
                "arguments": json.dumps({
                    "path": "challenge.py",
                    "content": content,
                }),
            },
        }],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "model_calls": 1,
            "tool_calls": 1,
        },
    }


def final_response():
    return {
        "role": "assistant",
        "content": "implemented",
        "finish_reason": "stop",
        "usage": {
            "input_tokens": 8,
            "output_tokens": 2,
            "model_calls": 1,
            "tool_calls": 0,
        },
    }


class LocalBenchmarkCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name)
        self.project_root = Path(__file__).resolve().parents[1]
        self.manifest = self.project_root / "task_suites" / "manifest.json"
        self.model = "benchmark/model@revision"

    def tearDown(self):
        self.temporary.cleanup()

    def test_versioned_manifest_runs_real_agent_and_writes_audit_artifacts(self):
        oracle = (
            self.project_root
            / "task_suites"
            / "oracles"
            / "python-cli-add_feature-07"
            / "challenge.py"
        ).read_text(encoding="utf-8")
        clients = []

        def factory(cwd, inference_config):
            agent = LocalCodingAgent(
                cwd=cwd,
                model_config=ModelConfig(
                    name=self.model,
                    temperature=inference_config["temperature"],
                    max_tokens=inference_config["max_tokens"],
                ),
                permissions=AgentPermissions(
                    allow_write=True,
                    allow_shell=True,
                ).to_dict(),
            )
            client = SequencedClient(
                self.model,
                [write_response(oracle), final_response()],
            )
            clients.append(client)
            agent.client = client
            return agent

        result = run_local_benchmark(
            manifest_path=self.manifest,
            output_root=self.output / "benchmark",
            episodes_root=self.output / "episodes",
            model_ref=self.model,
            temperature=0.2,
            max_tokens=333,
            max_turns=3,
            prompt_version="prompt.test.v1",
            task_ids=["python-cli-add_feature-07"],
            input_token_price_per_million=1.0,
            output_token_price_per_million=2.0,
            agent_factory=factory,
        )

        self.assertEqual(result.task_ids, ["python-cli-add_feature-07"])
        self.assertTrue(
            result.episodes[0].success,
            Path(result.episodes[0].verification_ref).read_text(
                encoding="utf-8"
            ),
        )
        self.assertEqual(result.config.prompt_version, "prompt.test.v1")
        self.assertEqual(
            result.config.test_manifest_ref,
            "task_suites/manifest.json",
        )
        self.assertEqual(clients[0].calls[0]["temperature"], 0.2)
        self.assertEqual(clients[0].calls[0]["max_tokens"], 333)
        self.assertAlmostEqual(result.episodes[0].token_cost, 0.000032)
        self.assertTrue(
            (self.output / "benchmark" / "base" / "benchmark-run.json").is_file()
        )
        self.assertTrue(Path(result.episodes[0].trajectory_ref).is_file())
        self.assertTrue(Path(result.episodes[0].verification_ref).is_file())
        verification = json.loads(
            Path(result.episodes[0].verification_ref).read_text(
                encoding="utf-8"
            )
        )
        diff_scope = next(
            signal
            for signal in verification["signals"]
            if signal["name"] == "diff_scope"
        )
        self.assertEqual(
            diff_scope["details"]["allowed_patterns"],
            ["challenge.py"],
        )

    def test_explicit_allowed_paths_override_oracle_scope(self):
        oracle = (
            self.project_root
            / "task_suites"
            / "oracles"
            / "python-cli-add_feature-07"
            / "challenge.py"
        ).read_text(encoding="utf-8")

        def factory(cwd, inference_config):
            agent = LocalCodingAgent(
                cwd=cwd,
                model_config=ModelConfig(
                    name=self.model,
                    temperature=inference_config["temperature"],
                    max_tokens=inference_config.get("max_tokens"),
                ),
                permissions=AgentPermissions(
                    allow_write=True,
                    allow_shell=True,
                ).to_dict(),
            )
            agent.client = SequencedClient(
                self.model,
                [write_response(oracle), final_response()],
            )
            return agent

        result = run_local_benchmark(
            manifest_path=self.manifest,
            output_root=self.output / "benchmark-override",
            episodes_root=self.output / "episodes-override",
            model_ref=self.model,
            max_turns=3,
            task_ids=["python-cli-add_feature-07"],
            allowed_path_patterns=["custom/**"],
            agent_factory=factory,
        )
        verification = json.loads(
            Path(result.episodes[0].verification_ref).read_text(
                encoding="utf-8"
            )
        )
        diff_scope = next(
            signal
            for signal in verification["signals"]
            if signal["name"] == "diff_scope"
        )
        self.assertEqual(
            diff_scope["details"]["allowed_patterns"],
            ["custom/**"],
        )

    def test_container_benchmark_gates_shell_and_applies_write_allowlist(self):
        image = "python@sha256:" + "a" * 64
        fake_result = SimpleNamespace(run_id="not-executed")
        with patch(
            "claw.benchmark.local_command.OCIContainerRunner"
        ) as runner_class, patch.object(
            BenchmarkRunner, "run", return_value=fake_result
        ) as benchmark_run:
            runner = runner_class.return_value
            runner.describe.return_value = {
                "kind": "oci-container",
                "image_digest": image,
            }
            runner.verify_disposable_workspace_contract.return_value = {
                "status": "passed",
                "candidate_snapshot_visible": True,
                "shell_mutations_discarded": True,
            }
            result = run_local_benchmark(
                manifest_path=self.manifest,
                output_root=self.output / "container-admission",
                model_ref=self.model,
                task_ids=["python-cli-add_feature-07"],
                allowed_path_patterns=["challenge.py"],
                container_image=image,
            )

        self.assertIs(result, fake_result)
        runner.verify_disposable_workspace_contract.assert_called_once_with()
        _tasks, adapter, config = benchmark_run.call_args.args
        agent = adapter.agent_factory(
            str(self.output), dict(config.decoding_config)
        )
        self.assertEqual(agent.permissions["allowed_write_paths"], ["challenge.py"])
        self.assertEqual(
            config.decoding_config["disposable_shell_contract"]["status"],
            "passed",
        )

    def test_unknown_or_non_test_task_is_rejected_before_agent_creation(self):
        calls = []

        with self.assertRaisesRegex(BenchmarkError, "not in the test split"):
            run_local_benchmark(
                manifest_path=self.manifest,
                output_root=self.output / "benchmark",
                model_ref=self.model,
                task_ids=["python-cli-add_feature-01"],
                agent_factory=lambda *_args: calls.append(True),
            )

        self.assertEqual(calls, [])

    def test_repeated_action_repair_requires_guard(self):
        with self.assertRaisesRegex(
            BenchmarkError,
            "requires reject_repeated_readonly_actions",
        ):
            run_local_benchmark(
                manifest_path=self.manifest,
                output_root=self.output / "benchmark",
                model_ref=self.model,
                repeated_action_repair_attempts=1,
            )

    def test_docker_benchmark_rejects_unpinned_image_before_episode(self):
        calls = []
        with self.assertRaisesRegex(BenchmarkError, "must be pinned"):
            run_local_benchmark(
                manifest_path=self.manifest,
                output_root=self.output / "benchmark-docker",
                model_ref=self.model,
                sandbox_backend="docker",
                sandbox_image="python:3.12-slim",
                agent_factory=lambda *_args: calls.append(True),
            )
        self.assertEqual(calls, [])

    def test_prompt_version_changes_protocol_fingerprint(self):
        common = dict(
            group_name="base",
            model_ref=self.model,
            test_manifest_ref="task_suites/manifest.json",
            decoding_config={"temperature": 0.0},
            tool_schema_version="tool.v1",
            runtime_version="runtime.v1",
            verifier_bundle_version="verifier.v1",
        )

        first = BenchmarkConfig(**common, prompt_version="prompt.v1")
        second = BenchmarkConfig(**common, prompt_version="prompt.v2")

        self.assertNotEqual(
            first.protocol_fingerprint,
            second.protocol_fingerprint,
        )

    def test_cli_dispatches_compact_auditable_summary(self):
        config = BenchmarkConfig(
            group_name="base",
            model_ref=self.model,
            test_manifest_ref="task_suites/manifest.json",
            decoding_config={"temperature": 0.0, "max_turns": 2},
            tool_schema_version="tool.v1",
            runtime_version="runtime.v1",
            verifier_bundle_version="verifier.v1",
            prompt_version="prompt.v1",
        )
        result = SimpleNamespace(
            run_id="benchmark_test",
            config=config,
            task_ids=["task-1"],
            metrics={"task_success_rate": 0.0},
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("claw.benchmark.run_local_benchmark", return_value=result) as run:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main([
                    "benchmark-run",
                    "--cwd", str(self.project_root),
                    "--manifest", "task_suites/manifest.json",
                    "--model", self.model,
                    "--api-config-root", "model-config",
                    "--limit", "1",
                    "--prompt-version", "prompt.v1",
                    "--thinking-mode", "disabled",
                    "--reject-repeated-readonly-actions",
                    "--repeated-action-repair-attempts", "1",
                ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(payload["run_id"], "benchmark_test")
        self.assertEqual(payload["task_count"], 1)
        self.assertEqual(run.call_args.kwargs["limit"], 1)
        self.assertTrue(
            run.call_args.kwargs["reject_repeated_readonly_actions"]
        )
        self.assertEqual(
            run.call_args.kwargs["repeated_action_repair_attempts"],
            1,
        )
        self.assertEqual(run.call_args.kwargs["thinking_mode"], "disabled")
        self.assertEqual(
            run.call_args.kwargs["api_config_root"],
            str(self.project_root / "model-config"),
        )
        self.assertEqual(
            run.call_args.kwargs["manifest_path"],
            str(self.manifest),
        )

    def test_cli_forwards_pinned_docker_benchmark_boundary(self):
        config = BenchmarkConfig(
            group_name="base",
            model_ref=self.model,
            test_manifest_ref="task_suites/manifest.json",
            decoding_config={"temperature": 0.0},
            tool_schema_version="tool.v1",
            runtime_version="runtime.v1",
            verifier_bundle_version="verifier.v1",
        )
        result = SimpleNamespace(
            run_id="benchmark_docker",
            config=config,
            task_ids=["task-1"],
            metrics={},
        )
        digest = "sha256:" + "a" * 64
        with patch(
            "claw.benchmark.run_local_benchmark",
            return_value=result,
        ) as run:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = main([
                    "benchmark-run",
                    "--cwd", str(self.project_root),
                    "--manifest", "task_suites/manifest.json",
                    "--model", self.model,
                    "--sandbox-backend", "docker",
                    "--sandbox-image", f"python@{digest}",
                ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(run.call_args.kwargs["sandbox_backend"], "docker")
        self.assertEqual(
            run.call_args.kwargs["sandbox_image"],
            f"python@{digest}",
        )


if __name__ == "__main__":
    unittest.main()
