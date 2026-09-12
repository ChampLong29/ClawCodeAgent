from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claw.agent_types import UsageStats
from claw.benchmark.pi_rpc_adapter import PiRpcRun
from claw.main import main


class _EditingPiClient:
    def __init__(self, cwd, **_kwargs):
        self.cwd = Path(cwd)

    def run(self, _prompt, **_kwargs):
        (self.cwd / "challenge.py").write_text(
            '"""Command formatting challenge."""\n\n'
            "def build_command(name):\n"
            "    if not isinstance(name, str) or not name:\n"
            '        raise ValueError("name must be a non-empty string")\n'
            '    return f"run7:{name}"\n',
            encoding="utf-8",
        )
        return PiRpcRun(
            events=[
                {"type": "turn_start"},
                {
                    "type": "tool_execution_start",
                    "toolCallId": "pi-call-1",
                    "toolName": "write",
                    "args": {"path": "challenge.py"},
                },
                {
                    "type": "tool_execution_end",
                    "toolCallId": "pi-call-1",
                    "toolName": "write",
                    "result": {"content": []},
                    "isError": False,
                },
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "implemented"}],
                        "stopReason": "stop",
                    },
                },
            ],
            final_message="implemented",
            usage=UsageStats(
                input_tokens=20,
                output_tokens=5,
                model_calls=1,
                tool_calls=1,
            ),
        )


class PiBenchmarkCommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name)
        self.project_root = Path(__file__).resolve().parents[1]
        self.manifest = self.project_root / "task_suites" / "manifest.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_versioned_manifest_runs_pi_adapter_and_writes_audit_artifacts(self):
        from claw.benchmark import run_pi_benchmark

        result = run_pi_benchmark(
            manifest_path=self.manifest,
            output_root=self.output / "benchmark",
            episodes_root=self.output / "episodes",
            model_ref="provider/model@revision",
            provider="provider",
            runtime_version="pi@1.2.3",
            tool_version="pi-builtins@1.2.3",
            sandbox_attestation="test-sandbox:isolated-tempdir",
            task_ids=["python-cli-add_feature-07"],
            max_turns=3,
            client_factory=_EditingPiClient,
        )

        self.assertEqual(result.task_ids, ["python-cli-add_feature-07"])
        self.assertTrue(result.episodes[0].success)
        self.assertEqual(result.episodes[0].test_pass_rate, 1.0)
        self.assertEqual(result.episodes[0].tool_calls, 1)
        self.assertEqual(result.config.runtime_version, "pi@1.2.3")
        self.assertEqual(
            result.config.decoding_config["sandbox_attestation"],
            "test-sandbox:isolated-tempdir",
        )
        self.assertEqual(result.config.decoding_config["max_total_tokens"], 250000)
        self.assertTrue(
            (self.output / "benchmark" / "base" / "benchmark-run.json").is_file()
        )
        self.assertTrue(Path(result.episodes[0].trajectory_ref).is_file())
        self.assertTrue(Path(result.episodes[0].verification_ref).is_file())

    def test_cli_dispatches_pi_benchmark_with_required_provenance(self):
        result = SimpleNamespace(
            run_id="benchmark_pi_test",
            config=SimpleNamespace(
                group_name="base",
                model_ref="provider/model@revision",
                protocol_fingerprint="fingerprint",
            ),
            task_ids=["task-1"],
            metrics={"task_success_rate": 1.0},
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("claw.benchmark.run_pi_benchmark", return_value=result) as run:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main([
                    "benchmark-pi",
                    "--cwd", str(self.project_root),
                    "--manifest", "task_suites/manifest.json",
                    "--model", "provider/model@revision",
                    "--provider", "provider",
                    "--runtime-version", "pi@1.2.3",
                    "--tool-version", "pi-builtins@1.2.3",
                    "--sandbox-attestation", "docker:image@sha256:test",
                    "--use-claw-api-config",
                    "--enforce-macos-seatbelt",
                    "--max-total-tokens", "12345",
                    "--limit", "1",
                ])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(payload["run_id"], "benchmark_pi_test")
        self.assertEqual(payload["runtime"], "pi_rpc")
        self.assertEqual(run.call_args.kwargs["limit"], 1)
        self.assertEqual(run.call_args.kwargs["runtime_version"], "pi@1.2.3")
        self.assertEqual(
            run.call_args.kwargs["sandbox_attestation"],
            "docker:image@sha256:test",
        )
        self.assertTrue(run.call_args.kwargs["use_claw_api_config"])
        self.assertTrue(run.call_args.kwargs["enforce_macos_seatbelt"])
        self.assertEqual(run.call_args.kwargs["max_total_tokens"], 12345)

    def test_claw_api_config_writes_only_environment_reference(self):
        from claw.benchmark.pi_command import _prepare_claw_api_config

        fake = SimpleNamespace(
            model="same-model",
            api_key="top-secret",
            base_url="https://example.invalid",
            provider=__import__(
                "claw.api_config", fromlist=["APIProvider"]
            ).APIProvider.ANTHROPIC,
        )
        with patch(
            "claw.benchmark.pi_command.APIConfigRuntime"
        ) as runtime:
            runtime.return_value.get_config.return_value = fake
            provider, environment, temperature_is_explicit = _prepare_claw_api_config(
                project_root=self.project_root,
                output_root=self.output / "configured",
                model_ref="same-model",
                max_tokens=4096,
                temperature=0.0,
                base_environment={"PATH": os.environ.get("PATH", "")},
            )

        config_path = self.output / "configured" / "pi-config" / "models.json"
        text = config_path.read_text(encoding="utf-8")
        self.assertEqual(provider, "claw-anthropic-compat")
        self.assertNotIn("top-secret", text)
        self.assertIn("$CLAW_PI_API_KEY", text)
        self.assertEqual(environment["CLAW_PI_API_KEY"], "top-secret")
        self.assertFalse(temperature_is_explicit)


if __name__ == "__main__":
    unittest.main()
