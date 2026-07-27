"""P2 contracts for verified selection, dedupe, leakage, and SFT output."""

import tempfile
import unittest
from pathlib import Path

from claw.dataset import (
    DatasetBuilder,
    DatasetRecord,
    DatasetValidationError,
    LeakageError,
)
from claw.experiment.artifacts import ArtifactStore
from claw.experiment.schemas import TaskSpec
from claw.training.reviewer import ReviewReport
from claw.trajectory import rollout_result_to_trajectory
from claw.verification import (
    ReviewerEvidence,
    VerificationContext,
    VerificationPolicy,
    VerifierPipeline,
)


def task(task_id, *, split="train", family_id=None):
    value = TaskSpec(
        task_id=task_id,
        task_version="1.0.0",
        family_id=family_id or f"family-{task_id}",
        domain="python-cli",
        task_type="fix_bug",
        difficulty="easy",
        split=split,
        prompt=f"Fix unique task {task_id}",
        template_ref=f"templates/{task_id}",
        template_hash=(task_id.encode("utf-8").hex() + "f" * 64)[:64],
        initial_checks=["python -m unittest"],
        test_commands=["python -m unittest"],
        timeout_seconds=30,
        source="test",
        license="MIT",
    )
    value.content_hash = value.compute_content_hash()
    return value


def trajectory(task_id, *, passed=True, suffix=""):
    return rollout_result_to_trajectory(
        {
            "task_id": task_id,
            "session_id": f"session-{task_id}-{suffix}",
            "stop_reason": "completed",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": f"Fix {task_id}"},
                {
                    "role": "assistant",
                    "content": f"edit {suffix}",
                    "tool_calls": [
                        {
                            "id": f"call-{task_id}-{suffix}",
                            "name": "write_file",
                            "arguments": {"file_path": "src/main.py"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": f"call-{task_id}-{suffix}",
                    "content": "written",
                },
                {"role": "assistant", "content": "done"},
            ],
            "test_result": {
                "passed_tests": 1 if passed else 0,
                "total_tests": 1,
            },
            "diff_result": {"changed_files": ["src/main.py"]},
        }
    )


def verification(item, *, review_score):
    policy = VerificationPolicy(
        version="dataset-verifier.v1",
        required_signals=[
            "test_pass_rate",
            "build",
            "static_check",
            "diff_scope",
            "process_permission",
            "format_schema",
        ],
    )
    context = VerificationContext(
        trajectory=item,
        policy=policy,
        allowed_path_patterns=["src/**"],
        facts={
            "build": True,
            "static_check": True,
            "process_trace_complete": True,
            "format_valid": True,
        },
    )
    reviewer = ReviewerEvidence(
        report=ReviewReport(overall_score=review_score, dimensions={}),
        model="reviewer",
        prompt_version="review.v1",
        session_id=f"review-{item.header.trajectory_id}",
    )
    return VerifierPipeline().verify(context, reviewer=reviewer)


def record(task_id, *, passed=True, review_score=0.9, split="train", family=None):
    item = trajectory(task_id, passed=passed)
    return DatasetRecord(
        task=task(task_id, split=split, family_id=family),
        trajectory=item,
        verification=verification(item, review_score=review_score),
    )


class TestDatasetStrategies(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.records = [
            record("high", review_score=0.95),
            record("low-review", review_score=0.40),
            record("failed", passed=False, review_score=1.0),
        ]

    def tearDown(self):
        self.temp.cleanup()

    def build(self, strategy):
        return DatasetBuilder(generation_commit="commit-123").build(
            self.records,
            strategy=strategy,
            split="train",
            output_dir=self.root / strategy,
            quality_threshold=0.8,
            reviewer_threshold=0.7,
            max_messages=4,
        )

    def test_three_standard_strategies(self):
        raw = self.build("raw")
        success = self.build("success_only")
        filtered = self.build("verifier_filtered")
        self.assertEqual(raw.manifest.quality_summary["selected_trajectories"], 3)
        self.assertEqual(
            success.manifest.quality_summary["selected_trajectories"], 2
        )
        self.assertEqual(
            filtered.manifest.quality_summary["selected_trajectories"], 1
        )
        self.assertEqual(
            filtered.manifest.source_trajectory_ids,
            [self.records[0].trajectory.header.trajectory_id],
        )
        self.assertEqual(
            filtered.manifest.filter_config["reviewer_threshold"], 0.7
        )
        self.assertEqual(len(filtered.manifest.output_refs), 3)

    def test_generation_is_reproducible_for_same_inputs_and_config(self):
        first = self.build("verifier_filtered")
        second = DatasetBuilder(generation_commit="commit-123").build(
            self.records,
            strategy="verifier_filtered",
            split="train",
            output_dir=self.root / "repeated",
            quality_threshold=0.8,
            reviewer_threshold=0.7,
            max_messages=4,
        )
        self.assertEqual(first.manifest.dataset_id, second.manifest.dataset_id)
        self.assertEqual(first.manifest.content_hash, second.manifest.content_hash)
        self.assertEqual(
            first.manifest.filter_config_hash,
            second.manifest.filter_config_hash,
        )

    def test_sft_roles_and_tool_pairs_survive_segmentation(self):
        result = self.build("verifier_filtered")
        self.assertGreaterEqual(result.manifest.sample_count, 1)
        for sample in result.samples:
            for message in sample["messages"]:
                self.assertEqual(
                    message["trainable"], message["role"] == "assistant"
                )
            calls = {
                call["id"]
                for message in sample["messages"]
                for call in message.get("tool_calls", [])
            }
            results = {
                message["tool_call_id"]
                for message in sample["messages"]
                if message["role"] == "tool"
            }
            self.assertEqual(calls, results)
        self.assertTrue(
            (self.root / "verifier_filtered" / "verifier_filtered-manifest.json").is_file()
        )

    def test_duplicate_trajectory_is_removed_reproducibly(self):
        duplicate = DatasetRecord(
            task=self.records[0].task,
            trajectory=self.records[0].trajectory,
            verification=self.records[0].verification,
        )
        result = DatasetBuilder(generation_commit="commit-123").build(
            [self.records[0], duplicate],
            strategy="raw",
            split="train",
            output_dir=self.root / "dedupe",
        )
        self.assertEqual(
            result.manifest.quality_summary["selected_trajectories"], 1
        )
        self.assertIn("duplicate_trajectory", result.exclusions[0]["reason"])


    def test_artifact_backed_tool_call_preserves_correlation_id(self):
        store = ArtifactStore(self.root / "artifacts")
        call_id = "call-artifact-backed"
        item = rollout_result_to_trajectory(
            {
                "task_id": "artifact-backed",
                "session_id": "session-artifact-backed",
                "stop_reason": "completed",
                "messages": [
                    {"role": "user", "content": "Write a large file"},
                    {
                        "role": "assistant",
                        "content": "writing",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "name": "write_file",
                                "arguments": {
                                    "file_path": "src/main.py",
                                    "content": "x" * 1024,
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": "written",
                    },
                    {"role": "assistant", "content": "done"},
                ],
            },
            artifact_store=store,
            artifact_threshold_bytes=32,
        )
        result = DatasetBuilder(generation_commit="commit-123").build(
            [
                DatasetRecord(
                    task=task("artifact-backed"),
                    trajectory=item,
                    artifact_store=store,
                )
            ],
            strategy="raw",
            split="train",
            output_dir=self.root / "artifact-backed",
        )
        sample = result.samples[0]
        call_ids = {
            call["id"]
            for message in sample["messages"]
            for call in message.get("tool_calls", [])
        }
        result_ids = {
            message["tool_call_id"]
            for message in sample["messages"]
            if message["role"] == "tool"
        }
        self.assertEqual(call_ids, {call_id})
        self.assertEqual(result_ids, {call_id})


class TestDatasetLeakageAndValidation(unittest.TestCase):
    def test_test_split_is_rejected_from_training_data(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(LeakageError) as captured:
                DatasetBuilder(generation_commit="commit-123").build(
                    [record("held-out", split="test")],
                    strategy="raw",
                    split="train",
                    output_dir=directory,
                )
            self.assertEqual(
                captured.exception.report.issues[0].kind,
                "test_split_in_training_data",
            )

    def test_family_cannot_cross_splits(self):
        with tempfile.TemporaryDirectory() as directory:
            records = [
                record("train-a", split="train", family="shared"),
                record("dev-a", split="dev", family="shared"),
            ]
            with self.assertRaises(LeakageError) as captured:
                DatasetBuilder(generation_commit="commit-123").build(
                    records,
                    strategy="raw",
                    split="train",
                    output_dir=directory,
                )
            kinds = {issue.kind for issue in captured.exception.report.issues}
            self.assertIn("family_cross_split", kinds)
            self.assertIn("split_mismatch", kinds)

    def test_orphan_tool_result_is_rejected(self):
        item = trajectory("broken")
        data = item.to_dict()
        tool_result = next(
            event
            for event in data["events"]
            if event["event_type"] == "tool_result"
        )
        tool_result["payload"]["call_id"] = "orphan-call"
        tool_result["payload"]["message"]["tool_call_id"] = "orphan-call"
        broken = type(item).from_dict(data)
        bad_record = DatasetRecord(task=task("broken"), trajectory=broken)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DatasetValidationError):
                DatasetBuilder(generation_commit="commit-123").build(
                    [bad_record],
                    strategy="raw",
                    split="train",
                    output_dir=directory,
                )


if __name__ == "__main__":
    unittest.main()
