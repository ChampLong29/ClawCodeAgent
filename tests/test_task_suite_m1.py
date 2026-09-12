"""M1 executable task-suite coverage and split contracts."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claw.episode import EpisodeOrchestrator, EpisodeState
from claw.experiment.schemas import SchemaValidationError
from claw.task_suite import TaskSuiteManifest, TaskSuiteValidator
from tools.generate_task_suite import generate_suite


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "task_suites" / "manifest.json"


class TestCoreTaskSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = TaskSuiteManifest.load(MANIFEST)
        cls.report = TaskSuiteValidator(cls.manifest).validate_all()

    def test_manifest_has_required_scale_domains_types_and_splits(self):
        summary = self.manifest.summary()
        self.assertEqual(summary["task_count"], 32)
        self.assertEqual(
            summary["by_domain"],
            {"python-cli": 16, "python-library": 16},
        )
        self.assertEqual(
            summary["by_task_type"],
            {"add_feature": 16, "fix_bug": 16},
        )
        self.assertEqual(
            summary["by_split"],
            {"dev": 4, "test": 8, "train": 20},
        )
        self.assertEqual(summary["family_count"], 32)

    def test_all_templates_fail_initially_and_references_pass(self):
        self.assertTrue(self.report.passed)
        self.assertEqual(len(self.report.tasks), 32)
        self.assertTrue(all(item.initial_failed for item in self.report.tasks))
        self.assertTrue(
            all(item.reference_passed for item in self.report.tasks)
        )

    def test_validator_resolves_bare_python_without_ambient_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
                results = TaskSuiteValidator._run(
                    ['python -c "print(\'suite-runtime-ok\')"'],
                    Path(directory),
                    5,
                )

        self.assertEqual(results[0].returncode, 0, results[0])
        self.assertEqual(results[0].stdout.strip(), "suite-runtime-ok")
        self.assertIsNotNone(results[0].resolved_command)

    def test_validation_report_is_reconstructable_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "validation.json"
            TaskSuiteValidator.write_report(self.report, path)
            stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(stored["passed"])
        self.assertEqual(stored["task_count"], 32)
        self.assertEqual(
            stored["suite_content_hash"], self.manifest.content_hash
        )
        self.assertEqual(stored["report_id"], self.report.report_id)

    def test_manifest_rejects_template_tampering(self):
        manifest = TaskSuiteManifest.load(MANIFEST, verify_templates=False)
        manifest.tasks[0].template_hash = "0" * 64
        manifest.tasks[0].content_hash = (
            manifest.tasks[0].compute_content_hash()
        )
        manifest.content_hash = manifest.compute_content_hash()
        with self.assertRaises(SchemaValidationError):
            manifest.verify_asset_hashes()

    def test_family_cannot_cross_splits(self):
        manifest = TaskSuiteManifest.load(MANIFEST, verify_templates=False)
        train_task = manifest.tasks_for_split("train")[0]
        test_task = manifest.tasks_for_split("test")[0]
        test_task.family_id = train_task.family_id
        test_task.content_hash = test_task.compute_content_hash()
        manifest.content_hash = manifest.compute_content_hash()
        with self.assertRaises(SchemaValidationError) as captured:
            manifest.validate(verify_templates=False)
        self.assertIn("cross splits", str(captured.exception))

    def test_task_integrates_with_episode_prepare(self):
        task = self.manifest.get("python-cli-add_feature-01")
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = EpisodeOrchestrator(
                Path(directory) / "episodes",
                project_root=ROOT,
            )
            episode = orchestrator.prepare(
                task,
                episode_id="suite-integration",
                verify_template_hash=True,
            )
            self.assertEqual(episode.current_state, EpisodeState.READY)
            self.assertEqual(
                episode.metadata["task_content_hash"],
                task.content_hash,
            )


class TestTaskSuiteGenerator(unittest.TestCase):
    def test_generation_is_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            first = generate_suite(Path(directory) / "first" / "task_suites")
            second = generate_suite(Path(directory) / "second" / "task_suites")
            first_data = json.loads(first.read_text(encoding="utf-8"))
            second_data = json.loads(second.read_text(encoding="utf-8"))
        self.assertEqual(first_data["content_hash"], second_data["content_hash"])
        self.assertEqual(first_data["tasks"], second_data["tasks"])


if __name__ == "__main__":
    unittest.main()
