from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from claw.benchmark.metrics import BenchmarkEpisodeResult, compute_metrics
from claw.benchmark.runner import (
    BenchmarkConfig,
    BenchmarkRunResult,
)
from claw.experiment import (
    ExperimentRegistry,
    ExperimentRegistryError,
    ExperimentRun,
)
from claw.experiment.schemas import canonical_hash
from claw.training_backends.base import TrainingRunRecord


class ExperimentRegistryM4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = ExperimentRegistry(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_run(
        self,
        experiment_id: str = "experiment-1",
        backend: str = "peft_sft",
    ) -> ExperimentRun:
        return ExperimentRun(
            experiment_id=experiment_id,
            hypothesis="Verifier-filtered data improves task success.",
            base_model_ref="model/base@revision",
            dataset_manifest_ref="dataset-1",
            training_backend=backend,
            training_config={
                "learning_rate": 0.0002,
                "epochs": 1,
            },
            seed=42,
            status="created",
            git_commit="0123456789abcdef",
            environment={
                "python": "3.11.9",
                "platform": "test",
            },
            inference_config={"temperature": 0},
        )

    def make_training(
        self,
        run: ExperimentRun,
        *,
        backend: str = "peft_sft",
        verified: bool = True,
        status: str = "completed",
    ) -> TrainingRunRecord:
        return TrainingRunRecord(
            experiment_id=run.experiment_id,
            backend=backend,
            base_model_ref=run.base_model_ref,
            base_model_revision="revision",
            dataset_id=run.dataset_manifest_ref,
            dataset_content_hash="dataset-hash",
            training_config=dict(run.training_config),
            training_config_hash=canonical_hash(run.training_config),
            seed=run.seed,
            status=status,
            adapter_ref="adapters/experiment-1" if verified else None,
            metrics={"train_loss": 0.25},
            environment={"torch": "2.5"},
            artifact_refs=["trainer-state.json"],
            training_verified=verified,
        )

    def make_benchmark(
        self, run: ExperimentRun, training_ref: str
    ) -> BenchmarkRunResult:
        episode = BenchmarkEpisodeResult(
            task_id="task-test-1",
            family_id="family-test-1",
            domain="python-cli",
            difficulty="medium",
            success=False,
            test_pass_rate=0.5,
            tool_calls=2,
            valid_tool_selections=2,
            valid_tool_arguments=1,
            format_valid=True,
            process_violations=0,
            turns=3,
            input_tokens=100,
            output_tokens=25,
            token_cost=0.01,
            latency_seconds=1.2,
            bad_cases=["incorrect_patch"],
            verification_ref="verification-1.json",
            trajectory_ref="trajectory-1.json",
            error="one assertion failed",
        )
        config = BenchmarkConfig(
            group_name="verifier_sft",
            model_ref="adapters/experiment-1",
            test_manifest_ref="task_suites/test-manifest.json",
            decoding_config={"temperature": 0},
            tool_schema_version="tool.v1",
            runtime_version="runtime.v1",
            verifier_bundle_version="verifier.v1",
            dataset_manifest_ref=run.dataset_manifest_ref,
            training_run_ref=training_ref,
            experiment_ref=run.experiment_id,
            seed=run.seed,
        )
        return BenchmarkRunResult(
            run_id="benchmark-1",
            config=config,
            task_ids=[episode.task_id],
            metrics=compute_metrics([episode]),
            episodes=[episode],
            output_refs=["results.jsonl", "metrics.json"],
        )

    def test_create_load_and_list_preserve_reproducibility_fields(self) -> None:
        run = self.make_run()
        path = self.registry.create(run)

        loaded = self.registry.load(run.experiment_id)
        self.assertEqual(path.name, "experiment-run.json")
        self.assertEqual(loaded.git_commit, run.git_commit)
        self.assertEqual(loaded.environment["python"], "3.11.9")
        self.assertEqual(loaded.status_history[0]["status"], "created")
        self.assertEqual(
            [item.experiment_id for item in self.registry.list_runs()],
            ["experiment-1"],
        )

    def test_create_rejects_duplicate_unsafe_or_untraceable_runs(self) -> None:
        run = self.make_run()
        self.registry.create(run)
        with self.assertRaises(FileExistsError):
            self.registry.create(run)
        with self.assertRaises(ExperimentRegistryError):
            self.registry.create(self.make_run("../escape"))
        missing_commit = self.make_run("missing-commit")
        missing_commit.git_commit = ""
        with self.assertRaises(ExperimentRegistryError):
            self.registry.create(missing_commit)
        already_completed = self.make_run("already-completed")
        already_completed.status = "completed"
        with self.assertRaisesRegex(
            ExperimentRegistryError, "created state"
        ):
            self.registry.create(already_completed)

    def test_state_machine_is_auditable_and_rejects_illegal_transition(self) -> None:
        self.registry.create(self.make_run())
        training = self.registry.transition(
            "experiment-1", "training", "worker acquired run"
        )
        self.assertEqual(training.status, "training")
        same = self.registry.transition("experiment-1", "training")
        self.assertEqual(len(same.status_history), 2)
        self.registry.transition("experiment-1", "failed", "GPU unavailable")
        with self.assertRaises(ExperimentRegistryError):
            self.registry.transition("experiment-1", "completed")

    def test_recover_interrupted_marks_only_active_runs(self) -> None:
        self.registry.create(self.make_run("training-run"))
        self.registry.create(self.make_run("idle-run"))
        self.registry.transition("training-run", "training")

        recovered = self.registry.recover_interrupted()

        self.assertEqual(recovered, ["training-run"])
        self.assertEqual(
            self.registry.load("training-run").status,
            "recovery_required",
        )
        self.assertEqual(self.registry.load("idle-run").status, "created")

    def test_attach_verified_training_preserves_lineage_and_truth(self) -> None:
        run = self.make_run()
        self.registry.create(run)
        record = self.make_training(run)

        updated = self.registry.attach_training(
            run.experiment_id, record, "training-run.json"
        )

        self.assertEqual(updated.status, "trained")
        self.assertEqual(updated.training_run_ref, "training-run.json")
        self.assertTrue(
            updated.evidence["training"]["training_verified"]
        )
        self.assertIn("trainer-state.json", updated.artifact_refs)
        repeated = self.registry.attach_training(
            run.experiment_id, record, "training-run.json"
        )
        self.assertEqual(repeated.status_history, updated.status_history)

    def test_attach_training_rejects_lineage_mismatch(self) -> None:
        run = self.make_run()
        self.registry.create(run)
        record = self.make_training(run)
        record.dataset_id = "different-dataset"

        with self.assertRaisesRegex(
            ExperimentRegistryError, "dataset mismatch"
        ):
            self.registry.attach_training(
                run.experiment_id, record, "training-run.json"
            )

    def test_dry_run_is_never_reported_as_real_training(self) -> None:
        run = self.make_run("dry-experiment", backend="dry_run")
        self.registry.create(run)
        record = self.make_training(
            run, backend="dry_run", verified=False
        )
        updated = self.registry.attach_training(
            run.experiment_id, record, "dry-run.json"
        )
        paths = self.registry.render_report(run.experiment_id)
        markdown = Path(paths["markdown"]).read_text(encoding="utf-8")

        self.assertEqual(updated.status, "contract_verified")
        self.assertIn("not verified as real training", markdown)
        self.assertNotIn("verified real PeFT training**", markdown)

    def test_benchmark_evidence_reconstructs_json_and_markdown_without_ui(
        self,
    ) -> None:
        run = self.make_run()
        self.registry.create(run)
        self.registry.attach_training(
            run.experiment_id,
            self.make_training(run),
            "training-run.json",
        )
        result = self.make_benchmark(run, "training-run.json")

        updated = self.registry.attach_benchmark(
            run.experiment_id, result, "benchmark-run.json"
        )
        paths = self.registry.render_report(run.experiment_id)
        payload = json.loads(
            Path(paths["json"]).read_text(encoding="utf-8")
        )
        markdown = Path(paths["markdown"]).read_text(encoding="utf-8")

        self.assertEqual(updated.status, "completed")
        self.assertTrue(self.registry.verify_report(payload))
        self.assertEqual(
            payload["run"]["evidence"]["benchmark"]["episodes"][0][
                "bad_cases"
            ],
            ["incorrect_patch"],
        )
        self.assertIn("one assertion failed", markdown)
        self.assertIn("0123456789abcdef", markdown)
        repeated = self.registry.attach_benchmark(
            run.experiment_id, result, "benchmark-run.json"
        )
        self.assertEqual(repeated.status, "completed")
        result.metrics["sample_count"] = 99
        with self.assertRaisesRegex(
            ExperimentRegistryError, "cannot be replaced"
        ):
            self.registry.attach_benchmark(
                run.experiment_id, result, "benchmark-run.json"
            )
        payload["run"]["metrics"]["benchmark"]["sample_count"] = 99
        self.assertFalse(self.registry.verify_report(payload))

    def test_trained_benchmark_rejects_dry_run_evidence(self) -> None:
        run = self.make_run("dry-experiment", backend="dry_run")
        self.registry.create(run)
        self.registry.attach_training(
            run.experiment_id,
            self.make_training(
                run, backend="dry_run", verified=False
            ),
            "dry-run.json",
        )
        result = self.make_benchmark(run, "dry-run.json")
        result.config.experiment_ref = run.experiment_id

        with self.assertRaisesRegex(
            ExperimentRegistryError, "verified training"
        ):
            self.registry.attach_benchmark(
                run.experiment_id, result, "benchmark-run.json"
            )


if __name__ == "__main__":
    unittest.main()
