"""Protocol and trajectory tests for the Pi RPC baseline adapter."""

import json
import os
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from claw.agent_types import UsageStats
from claw.benchmark.pi_rpc_adapter import (
    PiDockerRpcClient,
    PiRpcAgent,
    PiRpcBenchmarkAdapter,
    PiRpcClient,
    PiRpcRun,
)
from claw.benchmark.runner import BenchmarkError


class TestPiRpcClient(unittest.TestCase):
    def test_builds_digest_pinned_constrained_docker_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = os.path.join(tmp, "config")
            os.mkdir(config)
            environment = dict(os.environ)
            environment["CLAW_PI_API_KEY"] = "secret"
            environment["PI_CODING_AGENT_DIR"] = config
            client = PiDockerRpcClient(
                tmp,
                docker_image="claw/pi@sha256:" + "a" * 64,
                executable="/opt/pi/node_modules/.bin/pi",
                provider="claw-openai-compat",
                model="deepseek-flash",
                process_environment=environment,
            )
            command = client._build_command()

        self.assertEqual(command[:3], ["docker", "run", "--rm"])
        self.assertIn("--pull=never", command)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop=ALL", command)
        self.assertIn("no-new-privileges", command)
        self.assertIn("claw/pi@sha256:" + "a" * 64, command)
        self.assertIn("CLAW_PI_API_KEY", command)
        self.assertNotIn("secret", command)
        self.assertEqual(
            command[-4:],
            ["--provider", "claw-openai-compat", "--model", "deepseek-flash"],
        )

    def test_docker_command_requires_digest_and_config_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "digest-pinned"):
                PiDockerRpcClient(tmp, docker_image="claw/pi:latest")
            with self.assertRaisesRegex(ValueError, "PI_CODING_AGENT_DIR"):
                PiDockerRpcClient(
                    tmp,
                    docker_image="claw/pi@sha256:" + "b" * 64,
                )
            with self.assertRaisesRegex(ValueError, "does not exist"):
                PiDockerRpcClient(
                    tmp,
                    docker_image="claw/pi@sha256:" + "b" * 64,
                    process_environment={
                        "PI_CODING_AGENT_DIR": os.path.join(tmp, "missing")
                    },
                )

    def test_passes_explicit_environment_without_persisting_secret(self):
        server = textwrap.dedent(
            """
            import json, os, sys
            request = json.loads(sys.stdin.readline())
            text = "present" if os.environ.get("PI_TEST_SECRET") == "secret" else "missing"
            for record in [
                {"id": request["id"], "type": "response", "command": "prompt", "success": True},
                {"type": "turn_start"},
                {"type": "message_end", "message": {"role": "assistant", "content": text, "stopReason": "stop"}},
                {"type": "turn_end", "message": {}, "toolResults": []},
                {"type": "agent_settled"},
            ]:
                print(json.dumps(record), flush=True)
            stats = json.loads(sys.stdin.readline())
            print(json.dumps({"id": stats["id"], "type": "response", "success": True, "data": {}}), flush=True)
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            environment = dict(os.environ)
            environment["PI_TEST_SECRET"] = "secret"
            client = PiRpcClient(
                tmp,
                command=[sys.executable, "-u", "-c", server],
                process_environment=environment,
            )
            run = client.run("work", timeout_seconds=5)

        self.assertEqual(run.final_message, "present")

    def test_wraps_command_with_requested_macos_seatbelt(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = PiRpcClient(
                tmp,
                command=["pi-test"],
                enforce_macos_seatbelt=True,
            )
            with mock.patch(
                "claw.benchmark.pi_rpc_adapter.os.path.isfile", return_value=True
            ):
                command = client._build_command()

        self.assertEqual(command[:2], ["/usr/bin/sandbox-exec", "-p"])
        self.assertEqual(command[-1], "pi-test")
        self.assertIn("deny writes outside sandbox", command[2])

    def test_strict_jsonl_prompt_settlement_and_stats(self):
        server = textwrap.dedent(
            """
            import json, sys
            request = json.loads(sys.stdin.readline())
            records = [
                {"id": request["id"], "type": "response", "command": "prompt", "success": True},
                {"type": "agent_start"},
                {"type": "turn_start"},
                {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}], "stopReason": "stop"}},
                {"type": "turn_end", "message": {}, "toolResults": []},
                {"type": "agent_end", "messages": [], "willRetry": False},
                {"type": "agent_settled"},
            ]
            for record in records:
                print(json.dumps(record), flush=True)
            stats = json.loads(sys.stdin.readline())
            print(json.dumps({"id": stats["id"], "type": "response", "command": "get_session_stats", "success": True, "data": {"toolCalls": 0, "tokens": {"input": 11, "output": 3, "cacheRead": 7, "cacheWrite": 2}}}), flush=True)
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            client = PiRpcClient(
                tmp,
                command=[sys.executable, "-u", "-c", server],
            )
            run = client.run("work", timeout_seconds=5, max_turns=3)

        self.assertEqual(run.stop_reason, "completed")
        self.assertEqual(run.final_message, "done")
        self.assertEqual(run.usage.input_tokens, 20)
        self.assertEqual(run.usage.output_tokens, 3)
        self.assertEqual(run.usage.model_calls, 1)

    def test_aborts_after_tool_turn_reaches_total_token_budget(self):
        server = textwrap.dedent(
            """
            import json, sys
            request = json.loads(sys.stdin.readline())
            for record in [
                {"id": request["id"], "type": "response", "command": "prompt", "success": True},
                {"type": "turn_start"},
                {"type": "message_end", "message": {"role": "assistant", "content": "edit", "usage": {"input": 8, "output": 4}, "stopReason": "toolUse"}},
                {"type": "turn_end", "message": {}, "toolResults": [{"ok": True}]},
            ]:
                print(json.dumps(record), flush=True)
            abort = json.loads(sys.stdin.readline())
            assert abort["type"] == "abort"
            print(json.dumps({"type": "agent_settled"}), flush=True)
            stats = json.loads(sys.stdin.readline())
            print(json.dumps({"id": stats["id"], "type": "response", "success": True, "data": {"tokens": {"input": 8, "output": 4}}}), flush=True)
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            client = PiRpcClient(
                tmp,
                command=[sys.executable, "-u", "-c", server],
            )
            run = client.run(
                "work",
                timeout_seconds=5,
                max_turns=3,
                max_total_tokens=10,
            )

        self.assertEqual(run.stop_reason, "budget_exceeded")
        self.assertEqual(run.error, "Pi run reached max_total_tokens=10")


class _Observer:
    def __init__(self):
        self.events = []
        self.finished = None

    def on_run_start(self, _agent, *, prompt, phase_id):
        self.events.append(("start", {"prompt": prompt, "phase_id": phase_id}))

    def record(self, event_type, *, payload, phase_id, parent_event_id=None):
        event_id = f"event-{len(self.events)}"
        self.events.append(
            (event_type, {**payload, "phase_id": phase_id, "parent": parent_event_id})
        )
        return event_id

    def on_run_finish(self, result, *, phase_id):
        self.finished = (result, phase_id)


class _FakeClient:
    def __init__(self, *_args, **_kwargs):
        pass

    def run(self, _prompt, **_kwargs):
        return PiRpcRun(
            events=[
                {"type": "turn_start"},
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "editing"}],
                        "usage": {"input": 4, "output": 2},
                        "stopReason": "toolUse",
                    },
                },
                {
                    "type": "tool_execution_start",
                    "toolCallId": "call-1",
                    "toolName": "edit",
                    "args": {"path": "a.py"},
                },
                {
                    "type": "tool_execution_end",
                    "toolCallId": "call-1",
                    "toolName": "edit",
                    "result": {"content": []},
                    "isError": False,
                },
            ],
            final_message="editing",
            usage=UsageStats(input_tokens=4, output_tokens=2, model_calls=1, tool_calls=1),
        )


class TestPiRpcAgent(unittest.TestCase):
    def test_maps_pi_events_to_claw_trajectory_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = PiRpcAgent(tmp, model="provider/model", client_factory=_FakeClient)
            observer = _Observer()
            agent.runtime_observer = observer
            result = agent.run("fix it", max_turns=5)

        event_types = [event_type for event_type, _payload in observer.events]
        self.assertEqual(result.stop_reason, "completed")
        self.assertIn("model_request", event_types)
        self.assertIn("model_response", event_types)
        self.assertEqual(event_types.count("tool_call"), 1)
        self.assertEqual(event_types.count("tool_result"), 1)
        tool_result = next(
            payload for event_type, payload in observer.events if event_type == "tool_result"
        )
        self.assertTrue(tool_result["arguments_valid"])
        self.assertTrue(tool_result["side_effect_possible"])
        self.assertEqual(tool_result["requested_tool_name"], "edit")
        self.assertEqual(tool_result["actual_tool_name"], "edit_file")
        self.assertIs(observer.finished[0], result)

    def test_agent_exposes_secret_free_isolation_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = PiRpcAgent(
                tmp,
                model="provider/model",
                isolation_attestation="docker-profile-v1",
                isolation_metadata={
                    "boundary": "pi_docker_rpc",
                    "image": "claw/pi@sha256:" + "a" * 64,
                    "tool_network_matches_claw_shell": False,
                },
            )

        self.assertEqual(
            agent.permissions["isolation_metadata"]["boundary"],
            "pi_docker_rpc",
        )
        self.assertFalse(
            agent.permissions["isolation_metadata"][
                "tool_network_matches_claw_shell"
            ]
        )

    def test_benchmark_adapter_requires_isolation_attestation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BenchmarkError):
                PiRpcBenchmarkAdapter(
                    tmp,
                    model_ref="provider/model",
                    runtime_version="pi@test",
                    prompt_version="prompt.v1",
                    tool_version="tools.v1",
                    config_version="config.v1",
                    sandbox_attestation="",
                )


if __name__ == "__main__":
    unittest.main()
