"""Contracts for the official SWE-bench Docker Harness boundary."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from claw.benchmark.swe_bench_official import (
    HARNESS_MODULE,
    OfficialSweBenchHarness,
    OfficialSweBenchHarnessConfig,
    OfficialSweBenchHarnessError,
)


class FakeHarnessExecutor:
    def __init__(self, version="5.0.2"):
        self.calls = []
        self.version = version

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), dict(kwargs)))
        if args[0] == "docker":
            return subprocess.CompletedProcess(args, 0, "Docker 29\n", "")
        if args[-1] == "--help":
            options = " ".join(
                (
                    "--dataset_name",
                    "--split",
                    "--instance_ids",
                    "--predictions_path",
                    "--max_workers",
                    "--run_id",
                    "--timeout",
                    "--open_file_limit",
                    "--report_dir",
                    "--rewrite_reports",
                )
            )
            return subprocess.CompletedProcess(args, 0, options, "")
        if args[1] == "-c" and "docker.from_env" in args[2]:
            payload = {"Version": "29.7.2", "Os": "linux", "Arch": "amd64"}
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        if args[1] == "-c":
            payload = {"version": self.version, "module": "/swebench/run_evaluation.py"}
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(args, 0, "evaluation complete\n", "")


def build_frozen_fixture(root: Path, instance_id="repo__project-1") -> Path:
    benchmark = root / "benchmark"
    raw = benchmark / "raw"
    raw.mkdir(parents=True)
    row = {
        "repo": "repo/project",
        "instance_id": instance_id,
        "base_commit": "a" * 40,
        "patch": "gold secret",
        "test_patch": "test secret",
        "problem_statement": "Fix it",
        "hints_text": "private hint",
        "created_at": "2024-01-01",
        "version": "1.0",
        "FAIL_TO_PASS": '["tests/test_fix.py::test_fix"]',
        "PASS_TO_PASS": '["tests/test_old.py::test_old"]',
        "environment_setup_commit": "b" * 40,
    }
    rows = json.dumps({"rows": [{"row_idx": 0, "row": row}]}, separators=(",", ":"))
    rows_path = raw / "dev.rows.json"
    rows_path.write_text(rows, encoding="utf-8")
    revision = "c" * 40
    (benchmark / "snapshot.json").write_text(
        json.dumps(
            {
                "dataset_revision": revision,
                "rows_sha256": hashlib.sha256(rows.encode("utf-8")).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    (benchmark / "pilot-selection.json").write_text(
        json.dumps(
            {
                "dataset_revision": revision,
                "selected": [{"instance_id": instance_id}],
            }
        ),
        encoding="utf-8",
    )
    return benchmark


class OfficialSweBenchHarnessTests(unittest.TestCase):
    def make_harness(self, executor=None):
        return OfficialSweBenchHarness(
            OfficialSweBenchHarnessConfig(python_executable="python"),
            executor=executor or FakeHarnessExecutor(),
        )

    def test_preflight_pins_package_cli_contract_and_docker(self):
        evidence = self.make_harness().preflight()
        self.assertEqual(evidence["status"], "contract_verified")
        self.assertTrue(evidence["official_swebench_harness"])
        self.assertEqual(evidence["package"]["version"], "5.0.2")
        self.assertEqual(evidence["docker_sdk"]["os"], "linux")

    def test_preflight_rejects_unpinned_observed_version(self):
        with self.assertRaisesRegex(OfficialSweBenchHarnessError, "version mismatch"):
            self.make_harness(FakeHarnessExecutor(version="5.0.1")).preflight()

    def test_prepare_uses_frozen_selected_row_and_official_prediction_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark = build_frozen_fixture(root)
            prepared = self.make_harness().prepare(
                benchmark_root=benchmark,
                instance_id="repo__project-1",
                model_patch="diff --git a/a.py b/a.py\n+candidate only\n",
                model_name_or_path="claw/test-model",
                output_root=root / "runs",
                run_id="frozen-run",
            )
            prediction = json.loads(prepared.predictions_path.read_text(encoding="utf-8"))
            dataset = json.loads(prepared.dataset_path.read_text(encoding="utf-8"))
            manifest = json.loads(prepared.input_manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(
            set(prediction),
            {"instance_id", "model_name_or_path", "model_patch"},
        )
        self.assertNotIn("gold secret", json.dumps(prediction))
        self.assertEqual(dataset[0]["patch"], "gold secret")
        self.assertEqual(manifest["status"], "prepared")
        self.assertNotIn("candidate only", json.dumps(manifest))
        self.assertEqual(Path(prepared.command[1]).name, "official-eval-runner.py")
        self.assertIn("--instance_ids", prepared.command)
        self.assertIn("--split", prepared.command)
        self.assertIn("dev", prepared.command)

    def test_relative_harness_python_is_frozen_before_run_cwd_changes(self):
        relative = str(Path(".venv-swebench") / "bin" / "python")
        harness = OfficialSweBenchHarness(
            OfficialSweBenchHarnessConfig(python_executable=relative),
            executor=FakeHarnessExecutor(),
        )
        self.assertEqual(
            harness.config.python_executable,
            str((Path.cwd() / relative).absolute()),
        )
        self.assertTrue(harness.config.python_executable.endswith(relative))

    def test_prepare_refuses_cached_run_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark = build_frozen_fixture(root)
            harness = self.make_harness()
            kwargs = {
                "benchmark_root": benchmark,
                "instance_id": "repo__project-1",
                "model_patch": "candidate patch",
                "model_name_or_path": "model",
                "output_root": root / "runs",
                "run_id": "same-run",
            }
            harness.prepare(**kwargs)
            with self.assertRaises(FileExistsError):
                harness.prepare(**kwargs)

    def test_prepare_rejects_tampered_frozen_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark = build_frozen_fixture(root)
            with (benchmark / "raw" / "dev.rows.json").open("a", encoding="utf-8") as stream:
                stream.write(" ")
            with self.assertRaisesRegex(OfficialSweBenchHarnessError, "hash mismatch"):
                self.make_harness().prepare(
                    benchmark_root=benchmark,
                    instance_id="repo__project-1",
                    model_patch="candidate patch",
                    model_name_or_path="model",
                    output_root=root / "runs",
                )

    def test_exports_tracked_episode_patch_without_mutating_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            target = workspace / "module.py"
            target.write_text("VALUE = 0\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(workspace), "add", "module.py"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(workspace),
                    "-c",
                    "user.name=Claw Test",
                    "-c",
                    "user.email=claw@example.invalid",
                    "commit",
                    "-qm",
                    "baseline",
                ],
                check=True,
            )
            target.write_text("VALUE = 1\n", encoding="utf-8")
            before = subprocess.run(
                ["git", "-C", str(workspace), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            patch = self.make_harness().model_patch_from_workspace(workspace)
            after = subprocess.run(
                ["git", "-C", str(workspace), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout

        self.assertIn("+VALUE = 1", patch)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
