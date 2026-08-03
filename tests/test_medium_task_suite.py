import json
import shutil
import tempfile
import unittest
from pathlib import Path

from claw.episode import EpisodeOrchestrator
from claw.experiment.schemas import SchemaValidationError
from claw.task_suite import TaskSuiteManifest, TaskSuiteValidator
from tools.generate_medium_task_suite import generate_suite


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "task_suites" / "medium" / "manifest.json"


class MediumTaskSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = TaskSuiteManifest.load(MANIFEST)

    def test_small_curated_manifest_uses_explicit_validation_policy(self):
        self.assertEqual(self.manifest.summary()["task_count"], 2)
        self.assertEqual(self.manifest.summary()["by_split"], {"test": 2})
        self.assertEqual(
            self.manifest.validation["required_splits"], ["test"]
        )

    def test_hidden_assets_are_hashed_and_not_copied_into_ready_workspace(self):
        task = self.manifest.tasks[0]
        self.assertTrue(task.test_assets_ref)
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = EpisodeOrchestrator(
                Path(directory) / "episodes", project_root=ROOT
            )
            orchestrator.prepare(task, episode_id="hidden-assets")
            self.assertFalse(
                (orchestrator.workspace / ".claw_hidden_tests").exists()
            )

    def test_templates_fail_and_oracles_pass_hidden_tests(self):
        report = TaskSuiteValidator(self.manifest).validate_all()
        self.assertTrue(report.passed)
        self.assertEqual(len(report.tasks), 2)

    def test_hidden_asset_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "repo"
            shutil.copytree(ROOT / "task_suites" / "medium", copied / "task_suites" / "medium")
            shutil.copy2(ROOT / "pyproject.toml", copied / "pyproject.toml")
            hidden = next((copied / "task_suites" / "medium" / "tests").rglob("*.py"))
            hidden.write_text("# tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(SchemaValidationError, "test assets hash mismatch"):
                TaskSuiteManifest.load(copied / "task_suites" / "medium" / "manifest.json")

    def test_generation_is_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            first = generate_suite(Path(directory) / "first" / "task_suites" / "medium")
            second = generate_suite(Path(directory) / "second" / "task_suites" / "medium")
            first_data = json.loads(first.read_text(encoding="utf-8"))
            second_data = json.loads(second.read_text(encoding="utf-8"))
        self.assertEqual(first_data["content_hash"], second_data["content_hash"])
        self.assertEqual(first_data["tasks"], second_data["tasks"])


if __name__ == "__main__":
    unittest.main()
