from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from claw.agent_runtime import LocalCodingAgent
from claw.agent_types import AgentPermissions
from claw.episode import EpisodeOrchestrator, EpisodeState, RuntimeAdapter
from claw.episode.checkpoint import workspace_hash
from claw.experiment.schemas import TaskSpec
from claw.trajectory import TrajectoryRecorder
from claw.verification import VerificationContext, VerifierPipeline


class SequencedClient:
    def __init__(self, responses):
        self.model = "test/model@revision"
        self.responses = list(responses)

    def complete(self, **_kwargs):
        if not self.responses:
            raise AssertionError("unexpected model call")
        return self.responses.pop(0)


def response_with_tool(name: str, arguments: dict, call_id: str = "call-1"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments),
                },
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "model_calls": 1,
            "tool_calls": 1,
        },
    }


def final_response(content: str = "done"):
    return {
        "role": "assistant",
        "content": content,
        "finish_reason": "stop",
        "usage": {
            "input_tokens": 8,
            "output_tokens": 2,
            "model_calls": 1,
            "tool_calls": 0,
        },
    }


class TrajectoryRecorderTests(unittest.TestCase):
    def test_atomic_reopen_redaction_and_artifact_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trajectory.json"
            recorder = TrajectoryRecorder.create(
                path,
                trajectory_id="trajectory-1",
                episode_id="episode-1",
                task_ref="task-1@1",
                model_version="model@revision",
                runtime_version="runtime.v1",
                prompt_version="prompt.v1",
                tool_version="tool.v1",
                config_version="config.v1",
                max_inline_bytes=256,
            )
            small = recorder.record(
                "episode_started",
                payload={
                    "token": "do-not-store",
                    "service_access_token": "also-do-not-store",
                    "input_tokens": 17,
                    "nested": {
                        "password": "also-secret",
                        "note": "API_KEY=top-secret",
                    },
                },
            )
            large = recorder.record(
                "model_request",
                payload={"messages": [{"role": "user", "content": "x" * 500}]},
                parent_event_id=small.event_id,
            )

            on_disk = json.loads(path.read_text(encoding="utf-8"))
            reopened = TrajectoryRecorder.open(path, max_inline_bytes=256)

            self.assertEqual(
                small.payload,
                {
                    "token": "[REDACTED]",
                    "service_access_token": "[REDACTED]",
                    "input_tokens": 17,
                    "nested": {
                        "password": "[REDACTED]",
                        "note": "API_KEY=[REDACTED]",
                    },
                },
            )
            self.assertEqual(large.payload["storage"], "artifact")
            self.assertEqual(
                reopened.resolve_payload(large)["messages"][0]["content"],
                "x" * 500,
            )
            self.assertEqual(len(on_disk["events"]), 2)
            self.assertTrue(
                reopened.artifact_store.verify(large.artifact_refs[0])
            )

    def test_termination_and_evaluation_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trajectory.json"
            recorder = TrajectoryRecorder.create(
                path,
                trajectory_id="trajectory-1",
                episode_id="episode-1",
                task_ref="task-1@1",
                model_version="model@revision",
                runtime_version="runtime.v1",
                prompt_version="prompt.v1",
                tool_version="tool.v1",
                config_version="config.v1",
            )
            first = recorder.terminate(
                "completed", detail="done", usage={"input_tokens": 1}
            )
            repeated = recorder.terminate(
                "completed", detail="done", usage={"input_tokens": 1}
            )
            recorder.attach_evaluation("verification-1.json")
            recorder.attach_evaluation("verification-1.json")

            self.assertEqual(first.event_id, repeated.event_id)
            self.assertEqual(
                recorder.trajectory.evaluation_refs,
                ["verification-1.json"],
            )
            with self.assertRaises(ValueError):
                recorder.terminate("failed", detail="different")


class RuntimeAdapterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def prepare_episode(self, episode_id: str):
        template = self.root / f"template-{episode_id}"
        template.mkdir()
        (template / "README.md").write_text("starter\n", encoding="utf-8")
        task = TaskSpec(
            task_id=f"task-{episode_id}",
            task_version="1.0.0",
            family_id=f"family-{episode_id}",
            domain="python-cli",
            task_type="add_feature",
            difficulty="easy",
            split="train",
            prompt="Create solution.txt containing done.",
            template_ref=str(template),
            template_hash=workspace_hash(template),
            initial_checks=[
                (
                    "python -c \"import pathlib; "
                    "raise SystemExit(0 if pathlib.Path('solution.txt').exists() "
                    "else 1)\""
                )
            ],
            test_commands=[
                (
                    "python -c \"import pathlib; "
                    "assert pathlib.Path('solution.txt').read_text().strip() "
                    "== 'done'\""
                )
            ],
            timeout_seconds=15,
            source="generated",
            license="MIT",
        )
        task.content_hash = task.compute_content_hash()
        orchestrator = EpisodeOrchestrator(
            self.root / "episodes",
            project_root=self.root,
        )
        orchestrator.prepare(task, episode_id=episode_id)
        return task, orchestrator

    def make_adapter(self, orchestrator, task):
        return RuntimeAdapter.create(
            orchestrator,
            model_version="test/model@revision",
            runtime_version="runtime.v1",
            prompt_version="prompt.v1",
            tool_version="tool.v1",
            config_version="config.v1",
            task=task,
            max_inline_bytes=512,
        )

    def test_agent_run_records_correlated_model_tool_and_termination_events(
        self,
    ):
        task, orchestrator = self.prepare_episode("episode-write")
        adapter = self.make_adapter(orchestrator, task)
        agent = LocalCodingAgent(
            cwd=str(orchestrator.workspace),
            permissions=AgentPermissions(allow_write=True).to_dict(),
            completion_reminder_turns=2,
        )
        agent.client = SequencedClient(
            [
                response_with_tool(
                    "write_file",
                    {"path": "solution.txt", "content": "done\n"},
                ),
                final_response(),
            ]
        )

        result = adapter.run(agent, task.prompt, max_turns=3)
        trajectory = adapter.recorder.trajectory
        events = trajectory.events

        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(
            orchestrator.manifest.current_state,
            EpisodeState.VERIFYING,
        )
        self.assertEqual(
            orchestrator.manifest.trajectory_ref,
            str(adapter.recorder.path),
        )
        self.assertEqual(
            (orchestrator.workspace / "solution.txt").read_text(
                encoding="utf-8"
            ),
            "done\n",
        )
        self.assertIsNone(agent.runtime_observer)
        self.assertEqual(
            trajectory.header.termination.reason,
            "completed",
        )
        self.assertEqual(
            trajectory.header.termination.usage["model_calls"],
            2,
        )
        self.assertEqual(
            trajectory.header.termination.usage["tool_calls"],
            1,
        )
        event_types = [event.event_type for event in events]
        self.assertEqual(event_types.count("model_request"), 2)
        self.assertEqual(event_types.count("model_response"), 2)
        self.assertEqual(event_types.count("tool_call"), 1)
        self.assertEqual(event_types.count("tool_result"), 1)
        self.assertEqual(event_types.count("runtime_guidance"), 1)
        self.assertEqual(event_types.count("workspace_diff"), 1)
        self.assertEqual(event_types.count("test_result"), 1)
        test_event = next(
            event for event in events if event.event_type == "test_result"
        )
        diff_event = next(
            event for event in events if event.event_type == "workspace_diff"
        )
        self.assertEqual(
            adapter.recorder.resolve_payload(test_event)["test_result"][
                "passed_tests"
            ],
            1,
        )
        self.assertIn(
            "solution.txt",
            adapter.recorder.resolve_payload(diff_event)["diff_result"][
                "changed_files"
            ],
        )

        model_request = next(
            event for event in events if event.event_type == "model_request"
        )
        tool_call = next(
            event for event in events if event.event_type == "tool_call"
        )
        tool_result = next(
            event for event in events if event.event_type == "tool_result"
        )
        self.assertEqual(tool_call.parent_event_id, model_request.event_id)
        self.assertEqual(tool_result.parent_event_id, tool_call.event_id)
        self.assertTrue(
            adapter.recorder.resolve_payload(tool_result)[
                "side_effect_possible"
            ]
        )
        context = VerificationContext(
            trajectory=trajectory,
            task=task,
            allowed_path_patterns=["solution.txt"],
            facts={
                "environment_valid": True,
                "process_trace_complete": True,
                "format_valid": True,
            },
        )
        report, verification_ref = adapter.verify_and_archive(
            VerifierPipeline(), context
        )
        self.assertEqual(report.verdict, "success")
        self.assertEqual(
            orchestrator.manifest.current_state, EpisodeState.ARCHIVED
        )
        self.assertTrue(Path(verification_ref).is_file())
        self.assertEqual(
            trajectory.evaluation_refs, [verification_ref]
        )

    def test_model_failure_still_records_tests_and_failed_termination(self):
        task, orchestrator = self.prepare_episode("episode-model-error")
        adapter = self.make_adapter(orchestrator, task)
        agent = LocalCodingAgent(
            cwd=str(orchestrator.workspace),
            permissions=AgentPermissions(allow_write=True).to_dict(),
        )
        agent.client = SequencedClient([])

        result = adapter.run(agent, task.prompt, max_turns=1)
        event_types = [
            event.event_type
            for event in adapter.recorder.trajectory.events
        ]

        self.assertEqual(result.stop_reason, "error")
        self.assertEqual(
            adapter.recorder.trajectory.header.termination.reason,
            "failed",
        )
        self.assertIn("runtime_error", event_types)
        self.assertIn("test_result", event_types)
        self.assertIn("workspace_diff", event_types)
        self.assertEqual(
            orchestrator.manifest.current_state, EpisodeState.VERIFYING
        )

    def test_token_limited_response_records_explicit_runtime_stop(self):
        task, orchestrator = self.prepare_episode("episode-token-limit")
        adapter = self.make_adapter(orchestrator, task)
        agent = LocalCodingAgent(
            cwd=str(orchestrator.workspace),
            permissions=AgentPermissions(allow_write=True).to_dict(),
        )
        agent.client = SequencedClient([{
            "role": "assistant",
            "content": "",
            "finish_reason": "max_tokens",
            "usage": {
                "input_tokens": 8,
                "output_tokens": 4096,
                "model_calls": 1,
                "tool_calls": 0,
            },
        }])

        result = adapter.run(agent, task.prompt, max_turns=2)
        trajectory = adapter.recorder.trajectory
        stop_event = next(
            event for event in trajectory.events
            if event.event_type == "runtime_stop"
        )

        self.assertEqual(result.stop_reason, "stopped")
        self.assertEqual(
            adapter.recorder.resolve_payload(stop_event)["reason"],
            "model_output_truncated",
        )
        self.assertEqual(trajectory.header.termination.reason, "cancelled")
        self.assertIn("token limit", trajectory.header.termination.detail)
        self.assertEqual(
            orchestrator.manifest.current_state, EpisodeState.VERIFYING
        )

    def test_permission_denial_is_preserved_without_shell_side_effect(self):
        task, orchestrator = self.prepare_episode("episode-permission")
        adapter = self.make_adapter(orchestrator, task)
        agent = LocalCodingAgent(
            cwd=str(orchestrator.workspace),
            permissions=AgentPermissions(allow_shell=False).to_dict(),
        )
        agent.client = SequencedClient(
            [
                response_with_tool("bash", {"command": "echo forbidden"}),
                final_response("permission handled"),
            ]
        )
        agent.permission_callback = lambda _tool, _args: False

        result = adapter.run(agent, task.prompt, max_turns=3)
        permission = next(
            event
            for event in adapter.recorder.trajectory.events
            if event.event_type == "permission_decision"
        )
        denied = [
            event
            for event in adapter.recorder.trajectory.events
            if event.event_type == "tool_result"
        ][0]

        self.assertEqual(result.stop_reason, "completed")
        self.assertFalse(permission.payload["allowed"])
        self.assertEqual(denied.parent_event_id, permission.parent_event_id)
        self.assertIn("denied", denied.payload["error"].lower())


if __name__ == "__main__":
    unittest.main()
