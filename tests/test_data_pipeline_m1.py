"""M0/M1 contracts for Silver records and LLaMAFactory export."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from claw.data_pipeline import (
    AgentSFTGovernancePipeline,
    AgentTrainingRecord,
    AgentTrainingRecordError,
    ClawRecordReader,
    DomainDifficultyBalancer,
    EpisodeSourceError,
    FailureTaxonomyAnnotator,
    LeakageGuardOperator,
    SilverDatasetBuilder,
    ToolAlignmentValidator,
    TrajectoryQualityScorer,
    load_episode_dataset_record,
    to_training_record,
)
from claw.dataset import DatasetRecord
from claw.dataset import LeakageError
from claw.dataset.converters import extract_messages
from claw.episode import EpisodeManifest, EpisodeState
from claw.experiment.schemas import TaskSpec
from claw.integrations.llamafactory import (
    LlamaFactoryExportError,
    LlamaFactoryExporter,
    to_sharegpt_sample,
)
from claw.training.reviewer import ReviewReport
from claw.trajectory import Trajectory, TrajectoryHeader, rollout_result_to_trajectory
from claw.verification import (
    ReviewerEvidence,
    VerificationContext,
    VerificationPolicy,
    VerifierPipeline,
)


def _task(task_id="silver-task", *, split="train"):
    item = TaskSpec(
        task_id=task_id,
        task_version="1.0.0",
        family_id=f"family-{task_id}",
        domain="python-cli",
        task_type="fix_bug",
        difficulty="medium",
        split=split,
        prompt=f"Fix {task_id}",
        template_ref=f"templates/{task_id}",
        template_hash=(task_id.encode().hex() + "f" * 64)[:64],
        test_commands=["python -m unittest"],
        initial_checks=["python -m unittest"],
        oracle_ref="private/oracle.patch",
        timeout_seconds=30,
        source="fixture",
        license="MIT",
        tags=["silver"],
    )
    item.content_hash = item.compute_content_hash()
    return item


def _source(
    task_id="silver-task", *, split="train", tool_name="write_file", passed=True
):
    trajectory = rollout_result_to_trajectory(
        {
            "task_id": task_id,
            "session_id": f"session-{task_id}",
            "stop_reason": "completed",
            "messages": [
                {"role": "system", "content": "You are a coding agent."},
                {"role": "user", "content": f"Fix {task_id}"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"call-{task_id}",
                            "name": tool_name,
                            "arguments": {
                                "file_path": "src/main.py",
                                "content": "VALUE = 1",
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": f"call-{task_id}",
                    "content": "written",
                },
                {"role": "assistant", "content": "Done."},
            ],
            "test_result": {"passed_tests": 1 if passed else 0, "total_tests": 1},
            "diff_result": {"changed_files": ["src/main.py"]},
        }
    )
    policy = VerificationPolicy(
        version="silver-verifier.v1",
        required_signals=[
            "test_pass_rate",
            "build",
            "static_check",
            "diff_scope",
            "process_permission",
            "format_schema",
        ],
    )
    report = VerifierPipeline().verify(
        VerificationContext(
            trajectory=trajectory,
            task=_task(task_id, split=split),
            policy=policy,
            allowed_path_patterns=["src/**"],
            facts={
                "build": True,
                "static_check": True,
                "process_trace_complete": True,
                "format_valid": True,
            },
        ),
        reviewer=ReviewerEvidence(
            report=ReviewReport(overall_score=0.92, dimensions={}),
            model="reviewer",
            prompt_version="review.v1",
            session_id="review-session",
        ),
    )
    return DatasetRecord(
        task=_task(task_id, split=split),
        trajectory=trajectory,
        verification=report,
    )


TOOL_SCHEMA = {
    "name": "write_file",
    "description": "Write a file",
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["file_path", "content"],
    },
}


def _runtime_v2_trajectory(task_id="runtime-v2-task"):
    trajectory = Trajectory(
        header=TrajectoryHeader(
            trajectory_id=f"trajectory-{task_id}",
            episode_id=f"episode-{task_id}",
            task_ref=f"{task_id}@1.0.0",
            model_version="runtime-v2-model",
            runtime_version="local-agent-runtime.v1",
            prompt_version="runtime-v2-prompt.v1",
            tool_version="runtime-v2-tools.v1",
            config_version="runtime-v2-config.v1",
            artifact_base="artifact://sha256/",
            started_at="2026-08-03T00:00:00+00:00",
        )
    )
    prefix = [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Inspect the project."},
    ]
    request = trajectory.append("model_request", payload={"messages": prefix})
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-runtime-v2",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path":"README.md"}',
                },
            }
        ],
    }
    trajectory.append(
        "model_response",
        payload={"content": "", "tool_calls": assistant["tool_calls"]},
        parent_event_id=request.event_id,
    )
    call = trajectory.append(
        "tool_call",
        payload={
            "call_id": "call-runtime-v2",
            "tool_name": "read_file",
            "arguments": '{"path":"README.md"}',
        },
        parent_event_id=request.event_id,
    )
    tool_message = {
        "role": "tool",
        "tool_call_id": "call-runtime-v2",
        "name": "read_file",
        "content": "project readme",
    }
    trajectory.append(
        "tool_result",
        payload={
            "call_id": "call-runtime-v2",
            "tool_name": "read_file",
            "result": "project readme",
        },
        parent_event_id=call.event_id,
    )
    second_request = trajectory.append(
        "model_request", payload={"messages": [*prefix, assistant, tool_message]}
    )
    trajectory.append(
        "model_response",
        payload={"content": "Inspection complete.", "tool_calls": []},
        parent_event_id=second_request.event_id,
    )
    trajectory.terminate("completed", usage={"model_calls": 2, "tool_calls": 1})
    return trajectory


class TestAgentTrainingRecord(unittest.TestCase):
    def test_runtime_v2_events_reconstruct_complete_nonduplicated_chat(self):
        messages = extract_messages(_runtime_v2_trajectory())
        self.assertEqual(
            [message["role"] for message in messages],
            ["system", "user", "assistant", "tool", "assistant"],
        )
        self.assertEqual(messages[2]["tool_calls"][0]["id"], "call-runtime-v2")
        self.assertEqual(messages[3]["content"], "project readme")
        self.assertEqual(messages[-1]["content"], "Inspection complete.")

    def test_conversion_is_stable_and_strips_evaluation_secrets(self):
        source = _source()
        first = to_training_record(source, generation_commit="commit-123")
        second = to_training_record(source, generation_commit="commit-123")
        self.assertEqual(first.record_id, second.record_id)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertTrue(first.verification["tests_passed"])
        self.assertEqual(first.verification["reviewer_score"], 0.92)
        self.assertEqual(first.lineage["trajectory_ref"], source.trajectory.header.trajectory_id)
        self.assertNotIn("oracle_ref", first.task)
        self.assertNotIn("test_commands", first.task)
        self.assertNotIn("initial_checks", first.task)

    def test_conversion_replaces_episode_workspace_paths(self):
        source = _source("workspace-path-task")
        workspace = r"C:\Users\alice\project\episodes\episode-1\workspace"
        source.trajectory.events[0].payload["cwd"] = workspace
        tool_result = next(
            event
            for event in source.trajectory.events
            if event.event_type == "tool_result"
        )
        tool_result.payload["message"]["content"] = json.dumps(
            {"path": workspace + r"\src\main.py"}
        )
        record = to_training_record(source, generation_commit="commit-123")
        encoded = json.dumps(record.messages)
        self.assertNotIn("alice", encoded)
        self.assertIn("<workspace>", encoded)

    def test_test_split_is_rejected(self):
        with self.assertRaisesRegex(AgentTrainingRecordError, "test split"):
            to_training_record(_source(split="test"), generation_commit="commit-123")

    def test_missing_verification_is_rejected(self):
        source = _source()
        source.verification = None
        with self.assertRaisesRegex(AgentTrainingRecordError, "require verification"):
            to_training_record(source, generation_commit="commit-123")

    def test_oracle_reference_in_messages_is_rejected_by_silver_builder(self):
        source = _source()
        source.trajectory.events[1].payload["message"]["content"] = (
            "Read private/oracle.patch"
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(LeakageError):
                SilverDatasetBuilder(generation_commit="commit-123").build(
                    [source], output_dir=temporary
                )

    def test_round_trip_detects_broken_tool_alignment(self):
        record = to_training_record(_source(), generation_commit="commit-123")
        payload = record.to_dict()
        payload["messages"][3]["tool_call_id"] = "orphan"
        payload["record_id"] = ""
        payload["content_hash"] = ""
        with self.assertRaisesRegex(AgentTrainingRecordError, "mismatch"):
            AgentTrainingRecord.from_dict(payload)

    def test_silver_manifest_is_deterministic_and_sorted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_a = _source("task-a")
            task_b = _source("task-b")
            first = SilverDatasetBuilder(generation_commit="commit-123").build(
                [task_b, task_a], output_dir=root / "one"
            )
            second = SilverDatasetBuilder(generation_commit="commit-123").build(
                [task_a, task_b], output_dir=root / "two"
            )
            self.assertEqual(first.manifest.dataset_id, second.manifest.dataset_id)
            self.assertEqual(first.manifest.content_hash, second.manifest.content_hash)
            self.assertEqual(first.manifest.record_ids, sorted(first.manifest.record_ids))
            self.assertTrue(first.manifest.leakage_report["passed"])
            self.assertTrue((root / "one" / "silver-records.jsonl").is_file())
            self.assertTrue((root / "one" / "silver-manifest.json").is_file())


class TestEpisodeSource(unittest.TestCase):
    def _write_episode(self, root, *, state=EpisodeState.ARCHIVED):
        source = _source("episode-source")
        task = source.task
        task_ref = f"{task.task_id}@{task.task_version}"
        source.trajectory.header.episode_id = "episode-source-1"
        source.trajectory.header.task_ref = task_ref
        trajectory_path = root / "trajectory.json"
        verification_path = root / "verification.json"
        root.mkdir(parents=True)
        trajectory_path.write_text(
            json.dumps(source.trajectory.to_dict()), encoding="utf-8"
        )
        verification_path.write_text(
            json.dumps(source.verification.to_dict()), encoding="utf-8"
        )
        manifest = EpisodeManifest(
            episode_id="episode-source-1",
            task_ref=task_ref,
            workspace_path=str(root / "workspace"),
            template_hash=task.template_hash,
            initial_commit="fixture-commit",
            current_state=state,
            trajectory_ref=str(trajectory_path.resolve()),
            verification_ref=str(verification_path.resolve()),
            metadata={"task_content_hash": task.content_hash},
        )
        manifest.save(root / "episode.json")
        return task

    def test_loads_archived_episode_with_verified_lineage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "episode"
            task = self._write_episode(root)
            record = load_episode_dataset_record(root, task)
            self.assertEqual(record.task.task_id, "episode-source")
            self.assertEqual(record.trajectory.header.episode_id, "episode-source-1")
            self.assertTrue(record.verification.hard_gate_passed)
            self.assertTrue(record.messages)

    def test_rejects_non_archived_episode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "episode"
            task = self._write_episode(root, state=EpisodeState.FAILED)
            with self.assertRaisesRegex(EpisodeSourceError, "ARCHIVED"):
                load_episode_dataset_record(root, task)


class TestLlamaFactoryExporter(unittest.TestCase):
    def test_maps_tool_use_to_sharegpt_roles(self):
        record = to_training_record(_source(), generation_commit="commit-123")
        sample = to_sharegpt_sample(record, tool_schemas=[TOOL_SCHEMA])
        roles = [message["from"] for message in sample["conversations"]]
        self.assertEqual(
            roles,
            ["system", "human", "function_call", "observation", "gpt"],
        )
        function_call = json.loads(sample["conversations"][2]["value"])
        self.assertEqual(function_call[0]["name"], "write_file")
        self.assertEqual(json.loads(sample["tools"])[0]["name"], "write_file")

    def test_parallel_calls_and_thought_are_collapsed_for_role_alternation(self):
        record = to_training_record(_source(), generation_commit="commit-123")
        record.messages[2]["content"] = "I will inspect both files."
        record.messages[2]["tool_calls"].append(
            {
                "id": "call-second",
                "name": "write_file",
                "arguments": {
                    "file_path": "src/second.py",
                    "content": "VALUE = 2",
                },
            }
        )
        record.messages.insert(
            4,
            {
                "role": "tool",
                "tool_call_id": "call-second",
                "content": "written second",
            },
        )
        record.record_id = record.compute_record_id()
        record.content_hash = record.compute_content_hash()
        sample = to_sharegpt_sample(record, tool_schemas=[TOOL_SCHEMA])
        self.assertEqual(
            [item["from"] for item in sample["conversations"]],
            ["system", "human", "function_call", "observation", "gpt"],
        )
        function_value = sample["conversations"][2]["value"]
        self.assertTrue(function_value.startswith("<think>\n"))
        functions = json.loads(function_value.split("</think>\n\n", 1)[1])
        self.assertEqual(len(functions), 2)
        self.assertIn("</tool_response>", sample["conversations"][3]["value"])

    def test_missing_tool_schema_is_rejected(self):
        record = to_training_record(
            _source(tool_name="custom_tool"), generation_commit="commit-123"
        )
        with self.assertRaisesRegex(LlamaFactoryExportError, "missing schemas"):
            to_sharegpt_sample(record, tool_schemas=[TOOL_SCHEMA])

    def test_export_writes_dataset_info_and_lineage_manifest(self):
        record = to_training_record(_source(), generation_commit="commit-123")
        with tempfile.TemporaryDirectory() as temporary:
            result = LlamaFactoryExporter(tool_schemas=[TOOL_SCHEMA]).export(
                [record], output_dir=temporary
            )
            dataset_info = json.loads(result.dataset_info_path.read_text("utf-8"))
            manifest = json.loads(result.manifest_path.read_text("utf-8"))
            self.assertEqual(dataset_info["claw_agent_sft"]["formatting"], "sharegpt")
            self.assertEqual(
                dataset_info["claw_agent_sft"]["columns"]["tools"], "tools"
            )
            self.assertEqual(manifest["llamafactory_min_version"], "0.9.4")
            self.assertEqual(manifest["record_ids"], [record.record_id])
            self.assertEqual(result.sample_count, 1)


class TestUpstreamCompatibilityBaseline(unittest.TestCase):
    def test_pinned_upstream_contract_is_versioned(self):
        path = Path("configs/integrations/data-centric-upstreams.json")
        payload = json.loads(path.read_text("utf-8"))
        self.assertEqual(payload["schema_version"], "data_centric_upstreams.v1")
        self.assertEqual(len(payload["upstreams"]["dataflow"]["commit"]), 40)
        self.assertEqual(len(payload["upstreams"]["dataflex"]["commit"]), 40)
        self.assertEqual(
            payload["upstreams"]["llamafactory"]["format"], "sharegpt"
        )

    def test_committed_fixture_and_native_dataflow_evidence(self):
        fixture_root = Path("examples/data_pipeline/silver")
        records, manifest = ClawRecordReader().read(
            fixture_root / "silver-records.jsonl",
            fixture_root / "silver-manifest.json",
        )
        evidence = json.loads(
            Path(
                "configs/integrations/dataflow-agent-sft-v1-smoke.json"
            ).read_text("utf-8")
        )
        self.assertEqual(manifest.dataset_id, "silver_6a0d33976a57930c6350")
        self.assertEqual(len(records), 5)
        self.assertTrue(all(record.task["split"] == "train" for record in records))
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["output"]["step_count"], 6)
        self.assertEqual(evidence["output"]["selected_record_count"], 3)
        digest = hashlib.sha256(
            (fixture_root / "silver-records.jsonl").read_bytes()
        ).hexdigest()
        self.assertEqual(digest, evidence["input"]["sha256"])

    def test_real_episode_pilot_evidence_keeps_claim_boundary(self):
        evidence = json.loads(
            Path(
                "configs/integrations/dataflow-real-episode-pilot.json"
            ).read_text("utf-8")
        )
        tokenization_config = json.loads(
            Path(
                "configs/training/llamafactory-qwen25-coder-tokenization-smoke.json"
            ).read_text("utf-8")
        )
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["collection"]["split"], "train")
        self.assertEqual(evidence["collection"]["task_count"], 1)
        self.assertEqual(evidence["silver"]["record_count"], 1)
        self.assertEqual(evidence["dataflow"]["step_count"], 6)
        self.assertEqual(evidence["gold"]["record_count"], 1)
        self.assertTrue(
            evidence["llamafactory_export"]["tokenization_verified"]
        )
        self.assertEqual(evidence["silver"]["workspace_path_leak_hits"], 0)
        self.assertEqual(evidence["tokenization_smoke"]["sample_count"], 1)
        self.assertEqual(evidence["tokenization_smoke"]["truncated_sample_count"], 0)
        self.assertGreater(
            evidence["tokenization_smoke"]["supervised_token_counts"][0], 0
        )
        self.assertFalse(evidence["tokenization_smoke"]["weights_loaded"])
        self.assertFalse(evidence["tokenization_smoke"]["training_performed"])
        self.assertEqual(
            tokenization_config["model_revision"],
            evidence["tokenization_smoke"]["model_revision"],
        )
        self.assertEqual(
            tokenization_config["llamafactory_version"],
            evidence["tokenization_smoke"]["llamafactory"],
        )
        self.assertFalse(evidence["artifact_policy"]["raw_episode_committed"])

    def test_real_episode_batch2_evidence_keeps_scale_claim_honest(self):
        evidence = json.loads(
            Path(
                "configs/integrations/dataflow-real-episode-batch2.json"
            ).read_text("utf-8")
        )
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["collection_summary"]["split"], "train")
        self.assertEqual(evidence["collection_summary"]["task_count"], 2)
        self.assertEqual(len(evidence["collection_summary"]["domains"]), 2)
        self.assertEqual(len(evidence["collection_summary"]["task_types"]), 2)
        self.assertEqual(evidence["silver"]["record_count"], 2)
        self.assertEqual(evidence["gold"]["record_count"], 2)
        self.assertEqual(evidence["tokenization_smoke"]["sample_count"], 2)
        self.assertEqual(evidence["tokenization_smoke"]["truncated_sample_count"], 0)
        self.assertFalse(evidence["tokenization_smoke"]["training_performed"])
        self.assertFalse(
            evidence["artifact_policy"]["contains_training_effect_claim"]
        )


class TestDeterministicGovernanceOperators(unittest.TestCase):
    def test_quality_formula_and_failure_taxonomy_are_versioned(self):
        success = to_training_record(_source(), generation_commit="commit-123")
        failure = to_training_record(
            _source("failed-task", passed=False), generation_commit="commit-123"
        )
        alignment = ToolAlignmentValidator().evaluate(success)
        scored = TrajectoryQualityScorer().apply(success, alignment=alignment)
        classified = FailureTaxonomyAnnotator().apply(failure)
        self.assertTrue(alignment.passed)
        self.assertGreater(scored.features["quality_score"], 0.9)
        self.assertEqual(
            scored.features["quality_policy_version"], "trajectory_quality.v1"
        )
        self.assertEqual(classified.features["failure_type"], "test_failure")

    def test_record_hash_survives_pandas_float_round_trip(self):
        record = to_training_record(_source(), generation_commit="commit-123")
        scored = TrajectoryQualityScorer().apply(record)
        payload = scored.to_dict()
        payload["features"]["quality_score"] += 1e-16
        restored = AgentTrainingRecord.from_dict(payload)
        self.assertEqual(restored.content_hash, scored.content_hash)

    def test_leakage_guard_reports_labels_without_secret_content(self):
        record = to_training_record(_source(), generation_commit="commit-123")
        payload = record.to_dict()
        payload["messages"][1]["content"] = "Use private/oracle.patch"
        payload["record_id"] = ""
        payload["content_hash"] = ""
        changed = AgentTrainingRecord.from_dict(payload)
        changed.record_id = changed.compute_record_id()
        changed.content_hash = changed.compute_content_hash()
        decision = LeakageGuardOperator().evaluate(changed)
        self.assertFalse(decision.passed)
        self.assertIn("private_oracle", decision.reasons)
        self.assertNotIn("private/oracle.patch", json.dumps(decision.evidence))

    def test_balancer_uses_weights_without_duplicating_records(self):
        records = [
            to_training_record(_source(f"task-{index}"), generation_commit="commit-123")
            for index in range(3)
        ]
        records[2].task["difficulty"] = "hard"
        records[2].record_id = records[2].compute_record_id()
        records[2].content_hash = records[2].compute_content_hash()
        balanced, report = DomainDifficultyBalancer().apply(records)
        self.assertEqual(len(balanced), len(records))
        self.assertEqual(len({item.record_id for item in balanced}), len(records))
        self.assertAlmostEqual(
            sum(item.features["sample_probability"] for item in balanced),
            1.0,
            places=8,
        )
        self.assertEqual(report["record_count"], 3)


class TestAgentSFTGovernancePipeline(unittest.TestCase):
    def test_reader_rejects_silver_content_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            SilverDatasetBuilder(generation_commit="commit-123").build(
                [_source()], output_dir=root
            )
            records_path = root / "silver-records.jsonl"
            records_path.write_text(
                records_path.read_text("utf-8").replace("Done.", "Tampered."),
                encoding="utf-8",
            )
            with self.assertRaises(AgentTrainingRecordError):
                ClawRecordReader().read(
                    records_path, root / "silver-manifest.json"
                )

    def test_pipeline_writes_gold_evidence_and_sharegpt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            silver = SilverDatasetBuilder(generation_commit="commit-123").build(
                [_source("success"), _source("failure", passed=False)],
                output_dir=root / "silver",
            )
            result = AgentSFTGovernancePipeline().run(
                silver.records,
                source_manifest=silver.manifest,
                output_dir=root / "gold",
            )
            self.assertEqual(result.manifest.pipeline_version, "agent_sft_v1")
            self.assertEqual(result.manifest.record_count, 1)
            self.assertEqual(result.report["source_record_count"], 2)
            self.assertEqual(result.report["excluded_record_count"], 1)
            self.assertEqual(result.report["processing_cost"]["model_calls"], 0)
            self.assertTrue((root / "gold" / "gold-manifest.json").is_file())
            self.assertTrue((root / "gold" / "data-card.md").is_file())
            self.assertTrue(
                (root / "gold" / "llamafactory" / "dataset_info.json").is_file()
            )


if __name__ == "__main__":
    unittest.main()
