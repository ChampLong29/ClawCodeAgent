"""Tests for the P0 experiment schemas, artifacts, and trajectory migration."""

import json
import tempfile
import unittest

from claw.experiment.artifacts import ArtifactStore
from claw.experiment.schemas import (
    DatasetManifest,
    SchemaValidationError,
    TaskSpec,
    VerificationReport,
    VerificationSignal,
)
from claw.trajectory import Trajectory, TrajectoryMigrator, rollout_result_to_trajectory


class TestTaskAndVerificationSchemas(unittest.TestCase):
    def _task(self, **overrides):
        values = {
            "task_id": "task-1",
            "task_version": "1.0.0",
            "family_id": "family-1",
            "domain": "python-cli",
            "task_type": "fix_bug",
            "difficulty": "easy",
            "split": "train",
            "prompt": "Fix the parser.",
            "template_ref": "templates/parser",
            "template_hash": "a" * 64,
            "initial_checks": ["python -m unittest"],
            "test_commands": ["python -m unittest"],
            "timeout_seconds": 60.0,
            "source": "internal",
            "license": "MIT",
        }
        values.update(overrides)
        return TaskSpec(**values)

    def test_task_content_hash_round_trip(self):
        task = self._task()
        data = task.to_dict()
        self.assertEqual(len(data["content_hash"]), 64)
        restored = TaskSpec.from_dict(data)
        self.assertEqual(restored.content_hash, data["content_hash"])

    def test_task_rejects_invalid_split(self):
        with self.assertRaises(SchemaValidationError):
            self._task(split="hidden").validate()

    def test_hard_failure_cannot_be_overridden(self):
        hard_failure = VerificationSignal(
            name="tests", kind="hard", status="fail", score=0.0
        )
        report = VerificationReport(
            report_id="report-1",
            trajectory_ref="trajectory-1",
            verifier_bundle_version="1.0.0",
            verdict="success",
            hard_gate_passed=True,
            signals=[hard_failure],
            aggregate_score=1.0,
        )
        with self.assertRaises(SchemaValidationError):
            report.validate()

    def test_dataset_manifest_rejects_duplicate_sources(self):
        manifest = DatasetManifest(
            dataset_id="dataset-1",
            version="1.0.0",
            strategy="raw",
            source_trajectory_ids=["t1", "t1"],
            filter_config_hash="b" * 64,
            split="train",
            sample_count=2,
            content_hash="c" * 64,
            generation_commit="abc123",
        )
        with self.assertRaises(SchemaValidationError):
            manifest.validate()


class TestArtifactStore(unittest.TestCase):
    def test_content_addressing_deduplicates_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(directory)
            first = store.put_json({"message": "hello"})
            second = store.put_json({"message": "hello"})
            self.assertEqual(first.uri, second.uri)
            self.assertTrue(store.verify(first))
            self.assertEqual(store.get_json(first), {"message": "hello"})

    def test_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(directory)
            ref = store.put_text("original")
            with open(store.path_for(ref), "wb") as handle:
                handle.write(b"changed")
            self.assertFalse(store.verify(ref))


class TestTrajectoryMigration(unittest.TestCase):
    def _legacy(self):
        return {
            "task_id": "task-1",
            "session_id": "session-1",
            "stop_reason": "completed",
            "reward": 0.95,
            "messages": [
                {"role": "user", "content": "Create x.py"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "write_file",
                            "arguments": {"file_path": "x.py", "content": "x = 1"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": "written",
                },
                {"role": "assistant", "content": "Done"},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "execution_time": 1.25,
            "test_result": {"passed_tests": 1, "total_tests": 1},
            "diff_result": {"matches": 1, "total_files": 1},
        }

    def test_rollout_conversion_is_append_only_and_separates_reward(self):
        trajectory = rollout_result_to_trajectory(self._legacy())
        data = trajectory.to_dict()
        self.assertEqual(data["header"]["schema_version"], "agent_trajectory.v2")
        self.assertEqual(
            [event["seq"] for event in data["events"]],
            list(range(1, len(data["events"]) + 1)),
        )
        self.assertEqual(data["events"][-1]["event_type"], "episode_terminated")
        self.assertNotIn("reward", json.dumps(data))
        tool_result = next(
            event for event in trajectory.events if event.event_type == "tool_result"
        )
        self.assertIsNotNone(tool_result.parent_event_id)

    def test_current_schema_round_trip_through_migrator(self):
        original = rollout_result_to_trajectory(self._legacy())
        restored = TrajectoryMigrator.migrate(original.to_dict())
        self.assertIsInstance(restored, Trajectory)
        self.assertEqual(restored.header.trajectory_id, original.header.trajectory_id)

    def test_large_payload_moves_to_artifact_store(self):
        legacy = self._legacy()
        legacy["messages"][0]["content"] = "x" * 100
        legacy["messages"][1]["tool_calls"][0]["arguments"]["content"] = "y" * 100
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(directory)
            trajectory = rollout_result_to_trajectory(
                legacy, artifact_store=store, artifact_threshold_bytes=32
            )
            request = next(
                event
                for event in trajectory.events
                if event.event_type == "model_request"
            )
            self.assertEqual(len(request.artifact_refs), 1)
            self.assertTrue(store.verify(request.artifact_refs[0]))
            tool_call = next(
                event
                for event in trajectory.events
                if event.event_type == "tool_call"
            )
            self.assertEqual(len(tool_call.artifact_refs), 1)
            self.assertTrue(store.verify(tool_call.artifact_refs[0]))


if __name__ == "__main__":
    unittest.main()
