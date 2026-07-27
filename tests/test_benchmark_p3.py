"""P3 contracts for independent benchmark metrics and four-group reports."""

import json
import tempfile
import unittest
from pathlib import Path

from claw.benchmark import (
    AblationReportGenerator,
    BenchmarkConfig,
    BenchmarkEpisodeResult,
    BenchmarkError,
    BenchmarkRunner,
    compute_metrics,
)
from claw.experiment.schemas import TaskSpec


def task(task_id, *, split="test", domain="cli-tool", difficulty="easy"):
    value = TaskSpec(
        task_id=task_id,
        task_version="1.0.0",
        family_id=f"family-{task_id}",
        domain=domain,
        task_type="fix_bug",
        difficulty=difficulty,
        split=split,
        prompt=f"Fix {task_id}",
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


def config(group, *, temperature=0.0):
    trained = group != "base"
    return BenchmarkConfig(
        group_name=group,
        model_ref=f"model/{group}",
        test_manifest_ref="test-manifest-v1",
        decoding_config={"temperature": temperature, "max_tokens": 256},
        tool_schema_version="tools.v1",
        runtime_version="runtime.v1",
        verifier_bundle_version="verifier.v1",
        dataset_manifest_ref=f"dataset/{group}" if trained else "",
        training_run_ref=f"training/{group}" if trained else "",
        experiment_ref=f"experiment/{group}" if trained else "",
        seed=42,
    )


class ScriptedAdapter:
    def __init__(self, success_by_task):
        self.success_by_task = success_by_task

    def run(self, item, inference_config):
        outcome = self.success_by_task[item.task_id]
        if isinstance(outcome, Exception):
            raise outcome
        return BenchmarkEpisodeResult(
            task_id=item.task_id,
            family_id=item.family_id,
            domain=item.domain,
            difficulty=item.difficulty,
            success=outcome,
            test_pass_rate=1.0 if outcome else 0.5,
            tool_calls=2,
            valid_tool_selections=2 if outcome else 1,
            valid_tool_arguments=2 if outcome else 1,
            format_valid=outcome,
            process_violations=0 if outcome else 1,
            turns=3 if outcome else 5,
            input_tokens=100,
            output_tokens=50,
            token_cost=0.01,
            latency_seconds=1.5,
            bad_cases=[] if outcome else ["test_failure"],
            trajectory_ref=f"trajectory-{item.task_id}",
        )


class TestBenchmarkMetrics(unittest.TestCase):
    def test_required_metrics_and_slices_are_computed(self):
        items = [
            ScriptedAdapter({"a": True}).run(task("a"), {}),
            ScriptedAdapter({"b": False}).run(
                task("b", domain="sdk-library", difficulty="hard"), {}
            ),
        ]
        metrics = compute_metrics(items)
        self.assertEqual(metrics["task_success_rate"], 0.5)
        self.assertEqual(metrics["test_pass_rate"], 0.75)
        self.assertEqual(metrics["tool_selection_validity"], 0.75)
        self.assertEqual(metrics["tool_argument_validity"], 0.75)
        self.assertEqual(metrics["schema_format_validity"], 0.5)
        self.assertEqual(metrics["process_violation_rate"], 0.5)
        self.assertEqual(metrics["average_turns"], 4.0)
        self.assertEqual(metrics["average_tokens"], 150.0)
        self.assertEqual(metrics["bad_case_distribution"], {"test_failure": 1})
        self.assertEqual(set(metrics["by_domain"]), {"cli-tool", "sdk-library"})
        self.assertEqual(set(metrics["by_difficulty"]), {"easy", "hard"})


class TestBenchmarkRunnerAndReport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tasks = [
            task("a"),
            task("b", domain="sdk-library", difficulty="hard"),
        ]

    def tearDown(self):
        self.temp.cleanup()

    def test_runner_preserves_adapter_failure_and_writes_all_artifacts(self):
        runner = BenchmarkRunner(self.root)
        result = runner.run(
            self.tasks,
            ScriptedAdapter({"a": True, "b": RuntimeError("model failed")}),
            config("base"),
        )
        self.assertEqual(len(result.episodes), 2)
        failed = result.episodes[1]
        self.assertFalse(failed.success)
        self.assertEqual(failed.bad_cases, ["environment_or_infra"])
        self.assertIn("RuntimeError", failed.error)
        for name in (
            "results.jsonl",
            "metrics.json",
            "bad-case-distribution.json",
            "cost-summary.json",
            "benchmark-run.json",
        ):
            self.assertTrue((self.root / "base" / name).is_file())
        lines = [
            json.loads(line)
            for line in (self.root / "base" / "results.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(lines), 2)

    def test_trained_group_requires_dataset_and_training_lineage(self):
        incomplete = BenchmarkConfig(
            group_name="raw_sft",
            model_ref="model/raw",
            test_manifest_ref="test-manifest-v1",
            decoding_config={"temperature": 0.0},
            tool_schema_version="tools.v1",
            runtime_version="runtime.v1",
            verifier_bundle_version="verifier.v1",
        )
        with self.assertRaises(BenchmarkError):
            incomplete.validate()

    def test_training_split_is_rejected(self):
        with self.assertRaises(BenchmarkError):
            BenchmarkRunner(self.root).run(
                [task("train-task", split="train")],
                ScriptedAdapter({"train-task": True}),
                config("base"),
            )

    def test_four_group_report_enforces_protocol_and_keeps_failures(self):
        runner = BenchmarkRunner(self.root / "runs")
        outcomes = {
            "base": {"a": False, "b": False},
            "raw_sft": {"a": True, "b": False},
            "success_sft": {"a": True, "b": False},
            "verifier_sft": {"a": True, "b": True},
        }
        runs = [
            runner.run(
                self.tasks,
                ScriptedAdapter(outcomes[group]),
                config(group),
            )
            for group in (
                "base",
                "raw_sft",
                "success_sft",
                "verifier_sft",
            )
        ]
        report = AblationReportGenerator(self.root / "report").generate(runs)
        self.assertEqual(set(report["groups"]), set(outcomes))
        self.assertEqual(
            report["groups"]["verifier_sft"]["task_success_delta_vs_base"],
            1.0,
        )
        self.assertTrue(report["limitations"])
        markdown = (self.root / "report" / "report.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("no statistical significance is claimed", markdown)
        self.assertIn("Raw-SFT", markdown)
        self.assertIn("a, b", markdown)

    def test_mismatched_inference_protocol_is_rejected(self):
        runner = BenchmarkRunner(self.root / "mismatch")
        groups = [
            "base",
            "raw_sft",
            "success_sft",
            "verifier_sft",
        ]
        runs = []
        for group in groups:
            temperature = 0.2 if group == "verifier_sft" else 0.0
            runs.append(
                runner.run(
                    self.tasks,
                    ScriptedAdapter({"a": True, "b": True}),
                    config(group, temperature=temperature),
                )
            )
        with self.assertRaises(BenchmarkError):
            AblationReportGenerator(self.root / "bad-report").generate(runs)


if __name__ == "__main__":
    unittest.main()
