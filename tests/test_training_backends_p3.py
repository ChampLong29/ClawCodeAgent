"""P3 contracts for assistant-only masking and SFT backend evidence."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claw.experiment.schemas import (
    DatasetManifest,
    ExperimentRun,
    SchemaValidationError,
    canonical_hash,
)
from claw.training_backends import (
    IGNORE_INDEX,
    DryRunBackend,
    PeFTSFTBackend,
    SFTTrainingConfig,
    ToolUseChatEncoder,
    TrainingBackendError,
    TrainingDatasetSource,
    TrainingDependencyError,
)


class CharacterTokenizer:
    bos_token_id = 1
    eos_token_id = 2
    pad_token_id = 0

    def encode(self, text, add_special_tokens=False):
        return [ord(character) + 10 for character in text]


def samples():
    return [
        {
            "messages": [
                {"role": "system", "content": "system", "trainable": False},
                {"role": "user", "content": "write file", "trainable": False},
                {
                    "role": "assistant",
                    "content": "working",
                    "trainable": True,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "write_file",
                            "arguments": {"path": "hello.py"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": "written",
                    "trainable": False,
                },
                {"role": "assistant", "content": "done", "trainable": True},
            ],
            "loss_roles": ["assistant"],
            "metadata": {
                "task_id": "task-1",
                "trajectory_id": "trajectory-1",
                "segment_index": 0,
            },
        }
    ]


def manifest(items, *, split="train"):
    value = DatasetManifest(
        dataset_id="dataset-1",
        version="1.0.0",
        strategy="verifier_filtered",
        source_trajectory_ids=["trajectory-1"],
        filter_config_hash=canonical_hash({}),
        split=split,
        sample_count=len(items),
        content_hash=canonical_hash(items),
        generation_commit="commit-123",
    )
    value.validate()
    return value


def experiment(config, *, backend="dry_run"):
    return ExperimentRun(
        experiment_id="experiment-1",
        hypothesis="Verifier-filtered data improves task success",
        base_model_ref="example/tiny-model",
        dataset_manifest_ref="dataset-1",
        training_backend=backend,
        training_config=config.to_dict(),
        seed=config.seed,
        status="created",
    )


class TestAssistantOnlyLossMask(unittest.TestCase):
    def test_only_assistant_text_and_tool_calls_are_trainable(self):
        tokenizer = CharacterTokenizer()
        encoder = ToolUseChatEncoder(tokenizer, max_length=10000)
        messages = samples()[0]["messages"]
        encoded = encoder.encode(messages)
        expected = sum(
            len(tokenizer.encode(encoder.render_message(message)))
            for message in messages
            if message["role"] == "assistant"
        ) + 1
        self.assertEqual(encoded.trainable_token_count, expected)

        assistant_tool_text = encoder.render_message(messages[2])
        tool_call_tokens = tokenizer.encode("<tool_call>")
        start = next(
            index
            for index in range(len(encoded.input_ids))
            if encoded.input_ids[index : index + len(tool_call_tokens)]
            == tool_call_tokens
        )
        self.assertEqual(
            encoded.labels[start : start + len(tool_call_tokens)],
            tool_call_tokens,
        )

        tool_result_tokens = tokenizer.encode("<|tool|>")
        result_start = next(
            index
            for index in range(len(encoded.input_ids))
            if encoded.input_ids[index : index + len(tool_result_tokens)]
            == tool_result_tokens
        )
        self.assertTrue(
            all(
                label == IGNORE_INDEX
                for label in encoded.labels[
                    result_start : result_start + len(tool_result_tokens)
                ]
            )
        )
        self.assertIn("<tool_call>", assistant_tool_text)

    def test_length_policy_keeps_tool_call_and_result_together(self):
        tokenizer = CharacterTokenizer()
        messages = samples()[0]["messages"]
        sizing = ToolUseChatEncoder(tokenizer, max_length=10000)
        kept = messages[:4]
        max_length = 2 + sum(
            len(tokenizer.encode(sizing.render_message(message)))
            for message in kept
        )
        encoded = ToolUseChatEncoder(
            tokenizer, max_length=max_length
        ).encode(messages)
        expected = [tokenizer.bos_token_id]
        for message in kept:
            expected.extend(tokenizer.encode(sizing.render_message(message)))
        expected.append(tokenizer.eos_token_id)
        self.assertEqual(encoded.input_ids, expected)
        full_length = 2 + sum(
            len(tokenizer.encode(sizing.render_message(message)))
            for message in messages
        )
        self.assertLess(len(encoded.input_ids), full_length)

    def test_truncation_before_assistant_fails_closed(self):
        encoder = ToolUseChatEncoder(CharacterTokenizer(), max_length=3)
        with self.assertRaises(TrainingBackendError):
            encoder.encode(
                [
                    {"role": "user", "content": "very long user prompt"},
                    {"role": "assistant", "content": "answer"},
                ]
            )


class TestTrainingDatasetAndDryRun(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.items = samples()
        self.samples_path = self.root / "samples.jsonl"
        self.samples_path.write_text(
            "".join(json.dumps(item) + "\n" for item in self.items),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_dataset_hash_mismatch_is_rejected(self):
        changed = samples()
        changed[0]["metadata"]["task_id"] = "changed"
        path = self.root / "changed.jsonl"
        path.write_text(json.dumps(changed[0]) + "\n", encoding="utf-8")
        source = TrainingDatasetSource(manifest(self.items), path)
        with self.assertRaises(TrainingBackendError):
            source.load_samples()

    def test_test_split_cannot_be_used_for_sft(self):
        source = TrainingDatasetSource(
            manifest(self.items, split="test"), self.samples_path
        )
        with self.assertRaises(TrainingBackendError):
            source.load_samples()

    def test_dry_run_preserves_dataset_and_never_claims_real_training(self):
        config = SFTTrainingConfig(
            output_dir=str(self.root / "run"),
            model_revision="revision-abc",
            seed=7,
        )
        run = experiment(config)
        backend = DryRunBackend(config)
        source = TrainingDatasetSource(manifest(self.items), self.samples_path)
        prepared = backend.prepare(run, source)
        self.assertEqual(prepared.status, "prepared")
        completed = backend.train(run)
        self.assertEqual(completed.status, "completed")
        self.assertFalse(completed.training_verified)
        self.assertFalse(
            json.loads(
                (Path(completed.adapter_ref) / "adapter_config.json").read_text(
                    encoding="utf-8"
                )
            )["training_verified"]
        )
        self.assertEqual(
            backend.evaluate_checkpoint(run)["contract_verified"], True
        )
        backend.cleanup(run)
        self.assertTrue(self.samples_path.is_file())
        self.assertTrue((self.root / "run" / "training-run.json").is_file())

    def test_experiment_and_dataset_lineage_must_match_backend_config(self):
        config = SFTTrainingConfig(
            output_dir=str(self.root / "lineage"),
            model_revision="revision-abc",
            seed=7,
        )
        source = TrainingDatasetSource(manifest(self.items), self.samples_path)
        mismatched_config = experiment(config)
        mismatched_config.training_config["learning_rate"] = 1e-5
        with self.assertRaises(TrainingBackendError):
            DryRunBackend(config).prepare(mismatched_config, source)

        mismatched_dataset = experiment(config)
        mismatched_dataset.dataset_manifest_ref = "another-dataset"
        with self.assertRaises(TrainingBackendError):
            DryRunBackend(config).prepare(mismatched_dataset, source)

    def test_qlora_explicitly_requires_bitsandbytes(self):
        config = SFTTrainingConfig(
            output_dir=str(self.root / "qlora"),
            model_revision="revision-abc",
            mode="qlora",
        )
        backend = PeFTSFTBackend(config)

        def import_dependency(name):
            if name == "bitsandbytes":
                raise ImportError("missing bitsandbytes")
            return object()

        with patch(
            "claw.training_backends.peft_sft.importlib.import_module",
            side_effect=import_dependency,
        ):
            with self.assertRaises(TrainingDependencyError) as captured:
                backend._dependencies()
        self.assertIn("bitsandbytes", str(captured.exception))

    def test_peft_dependency_failure_is_explicit_and_non_destructive(self):
        config = SFTTrainingConfig(
            output_dir=str(self.root / "peft"),
            model_revision="revision-abc",
            seed=7,
        )
        run = experiment(config, backend="peft_sft")
        backend = PeFTSFTBackend(config)
        source = TrainingDatasetSource(manifest(self.items), self.samples_path)
        with patch(
            "claw.training_backends.peft_sft.importlib.import_module",
            side_effect=ImportError("not installed"),
        ):
            with self.assertRaises(TrainingDependencyError):
                backend.prepare(run, source)
        self.assertTrue(self.samples_path.is_file())

    def test_legacy_training_facade_exposes_new_backends(self):
        from claw.training import DryRunBackend as FacadeDryRunBackend
        from claw.training import PeFTSFTBackend as FacadePeFTSFTBackend

        self.assertIs(FacadeDryRunBackend, DryRunBackend)
        self.assertIs(FacadePeFTSFTBackend, PeFTSFTBackend)

    def test_invalid_mixed_precision_config_is_rejected(self):
        with self.assertRaises(SchemaValidationError):
            SFTTrainingConfig(
                output_dir=str(self.root / "bad"),
                model_revision="revision-abc",
                bf16=True,
                fp16=True,
            ).validate()


if __name__ == "__main__":
    unittest.main()
