from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claw.agent_runtime import LocalCodingAgent
from claw.agent_types import AgentPermissions, ModelConfig
from claw.benchmark.runner import BenchmarkError
from claw.data_pipeline import (
    SilverDatasetBuilder,
    collect_local_training_episodes,
    load_episode_batch,
)
from claw.data_pipeline.swe_bench_collection import (
    collect_swe_bench_lite_dev_episode,
    _infer_workspace_import_name,
    _probe_agent_shell_workspace,
    _probe_docker_workspace_import,
    _probe_workspace_import,
    _workspace_pythonpath,
)
from claw.episode import EpisodeManifest, EpisodeState
from claw.task_suite import TaskSuiteManifest


class SequencedCollectionClient:
    def __init__(self, model, responses):
        self.model = model
        self.responses = list(responses)

    def complete(self, **_kwargs):
        if not self.responses:
            raise AssertionError("unexpected model call")
        return self.responses.pop(0)


def _write_response(content):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-collection-write",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps(
                        {"path": "challenge.py", "content": content}
                    ),
                },
            }
        ],
        "usage": {
            "input_tokens": 20,
            "output_tokens": 8,
            "model_calls": 1,
            "tool_calls": 1,
        },
    }


def _final_response():
    return {
        "role": "assistant",
        "content": "Implemented and verified.",
        "finish_reason": "stop",
        "usage": {
            "input_tokens": 12,
            "output_tokens": 4,
            "model_calls": 1,
            "tool_calls": 0,
        },
    }


class TrainingEpisodeCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name)
        self.project_root = Path(__file__).resolve().parents[1]
        self.suite_path = self.project_root / "task_suites" / "manifest.json"
        self.suite = TaskSuiteManifest.load(self.suite_path)
        self.task = self.suite.get("python-cli-add_feature-01")
        self.model = "collection/model@revision"
        self.oracle = self.suite.resolve_ref(self.task.oracle_ref).joinpath(
            "challenge.py"
        ).read_text(encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def _factory(self, cwd, inference_config):
        agent = LocalCodingAgent(
            cwd=cwd,
            model_config=ModelConfig(
                name=self.model,
                temperature=inference_config["temperature"],
                max_tokens=inference_config.get("max_tokens"),
            ),
            permissions=AgentPermissions(
                allow_write=True,
                allow_shell=True,
            ).to_dict(),
        )
        agent.client = SequencedCollectionClient(
            self.model,
            [_write_response(self.oracle), _final_response()],
        )
        return agent

    def test_train_collection_materializes_archived_episode_and_silver(self):
        result = collect_local_training_episodes(
            manifest_path=self.suite_path,
            output_root=self.output / "collection",
            generation_commit="collection-commit",
            task_ids=[self.task.task_id],
            model_ref=self.model,
            max_tokens=512,
            max_turns=3,
            prompt_version="collection-prompt.v1",
            agent_factory=self._factory,
        )

        self.assertEqual(result.manifest.split, "train")
        self.assertEqual(result.manifest.task_ids, [self.task.task_id])
        self.assertEqual(result.manifest.metrics["task_success_rate"], 1.0)
        self.assertTrue(result.manifest_path.is_file())
        self.assertTrue(result.results_path.is_file())
        episode_dir = Path(result.manifest.episode_refs[0])
        self.assertTrue(episode_dir.name.startswith("training-"))
        self.assertEqual(
            EpisodeManifest.load(episode_dir / "episode.json").current_state,
            EpisodeState.ARCHIVED,
        )

        records = load_episode_batch([episode_dir], self.suite)
        silver = SilverDatasetBuilder(
            generation_commit="collection-commit"
        ).build(records, output_dir=self.output / "silver")
        self.assertEqual(silver.manifest.record_count, 1)
        self.assertEqual(silver.records[0].task["split"], "train")

    def test_test_task_is_rejected_before_agent_creation(self):
        calls = []
        with self.assertRaisesRegex(BenchmarkError, "not in the train split"):
            collect_local_training_episodes(
                manifest_path=self.suite_path,
                output_root=self.output / "collection",
                generation_commit="collection-commit",
                task_ids=["python-cli-add_feature-07"],
                model_ref=self.model,
                agent_factory=lambda *_args: calls.append(True),
            )
        self.assertEqual(calls, [])

    def test_collection_api_rejects_test_split(self):
        with self.assertRaisesRegex(BenchmarkError, "train or dev"):
            collect_local_training_episodes(
                manifest_path=self.suite_path,
                output_root=self.output / "collection",
                generation_commit="collection-commit",
                split="test",
                model_ref=self.model,
                agent_factory=self._factory,
        )


class SweBenchEnvironmentContractTests(unittest.TestCase):
    def test_agent_shell_probe_uses_exact_workspace_and_fails_closed(self):
        class Runner:
            def __init__(self, returncode=0):
                self.returncode = returncode
                self.calls = []

            def run(self, command, *, cwd, timeout):
                self.calls.append((command, cwd, timeout))
                return SimpleNamespace(
                    returncode=self.returncode,
                    stdout=("agent-shell-workspace=ok\n" if not self.returncode else ""),
                    stderr="",
                )

            def result_metadata(self, _result):
                return {
                    "execution_backend": "oci-container",
                    "container_image_digest": "image@sha256:digest",
                }

        with tempfile.TemporaryDirectory() as temporary:
            runner = Runner()
            result = _probe_agent_shell_workspace(
                command_runner=runner,
                cwd=temporary,
                python_executable="/opt/task/bin/python3.8",
                import_name="package",
            )
            self.assertEqual(result["status"], "passed")
            self.assertEqual(runner.calls[0][1], str(Path(temporary).resolve()))
            self.assertIn(".claw-agent-shell-admission", runner.calls[0][0])
            self.assertFalse(
                (Path(temporary) / ".claw-agent-shell-admission").exists()
            )

            with self.assertRaisesRegex(
                BenchmarkError, "exact task workspace.*without stdout/stderr"
            ):
                _probe_agent_shell_workspace(
                    command_runner=Runner(returncode=1),
                    cwd=temporary,
                    python_executable="/opt/task/bin/python3.8",
                    import_name="package",
                )

            silent_runner = Runner()
            silent_runner.run = lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0, stdout="", stderr=""
            )
            with self.assertRaisesRegex(BenchmarkError, "did not execute"):
                _probe_agent_shell_workspace(
                    command_runner=silent_runner,
                    cwd=temporary,
                    python_executable="/opt/task/bin/python3.8",
                    import_name="package",
                )

    def test_docker_import_probe_uses_pinned_container_environment(self):
        fake_backend = SimpleNamespace()
        fake_handle = SimpleNamespace(
            backend_metadata={"image_identity": "sha256:" + "a" * 64}
        )
        fake_backend.prepare = lambda _spec: fake_handle
        fake_backend.start = lambda _handle: None
        fake_backend.destroy = lambda _handle: None
        fake_backend.exec = lambda _handle, _request: SimpleNamespace(
            ok=True,
            stdout=json.dumps(
                {
                    "python_version": "3.8.20",
                    "import_name": "demo",
                    "module_file": "src/demo/__init__.py",
                    "required_modules": {"pytest": "8.3.5"},
                    "runtime_capabilities": {
                        "multiprocessing_semaphore": True
                    },
                }
            ),
            stderr="",
        )
        with tempfile.TemporaryDirectory() as temporary, patch(
            "claw.data_pipeline.swe_bench_collection.DockerBackend",
            return_value=fake_backend,
        ):
            result = _probe_docker_workspace_import(
                cwd=temporary,
                image="claw/demo@sha256:" + "b" * 64,
                python_executable="/opt/task/bin/python3.8",
                import_name="demo",
            )

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["backend"], "docker")
        self.assertEqual(result["python_version"], "3.8.20")
        self.assertEqual(result["image_identity"], "sha256:" + "a" * 64)
        self.assertTrue(
            result["runtime_capabilities"]["multiprocessing_semaphore"]
        )

    def test_import_probe_fails_closed_without_inferred_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                BenchmarkError, "import name could not be inferred"
            ):
                _probe_workspace_import(
                    cwd=temporary,
                    python_executable=Path(sys.executable),
                    import_name=None,
                    environment=dict(os.environ),
                )

    def test_collection_rejects_escalation_without_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                BenchmarkError, "requires a positive implementation_deadline_turns"
            ):
                collect_swe_bench_lite_dev_episode(
                    benchmark_root=temporary,
                    instance_id="unused",
                    python_executable=sys.executable,
                    evaluator_script=Path(temporary) / "evaluator.py",
                    output_root=Path(temporary) / "output",
                    generation_commit="test-commit",
                    implementation_deadline_turns=0,
                    implementation_escalation_turns=1,
                    allowed_path_patterns=("target.py",),
                )

    def test_collection_rejects_constraint_repair_without_target_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                BenchmarkError,
                "requires implementation_target_read_allowance=1",
            ):
                collect_swe_bench_lite_dev_episode(
                    benchmark_root=temporary,
                    instance_id="unused",
                    python_executable=sys.executable,
                    evaluator_script=Path(temporary) / "evaluator.py",
                    output_root=Path(temporary) / "output",
                    generation_commit="test-commit",
                    implementation_deadline_turns=1,
                    implementation_escalation_turns=1,
                    force_direct_mutation_after_escalation=True,
                    implementation_constraint_repair_attempts=1,
                    allowed_path_patterns=("target.py",),
                )

    def test_collection_rejects_repeated_action_repair_without_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                BenchmarkError,
                "requires reject_repeated_readonly_actions",
            ):
                collect_swe_bench_lite_dev_episode(
                    benchmark_root=temporary,
                    instance_id="unused",
                    python_executable=sys.executable,
                    evaluator_script=Path(temporary) / "evaluator.py",
                    output_root=Path(temporary) / "output",
                    generation_commit="test-commit",
                    repeated_action_repair_attempts=1,
                    allowed_path_patterns=("target.py",),
                )

    def test_collection_rejects_non_positive_total_token_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                BenchmarkError, "max_total_tokens must be positive"
            ):
                collect_swe_bench_lite_dev_episode(
                    benchmark_root=temporary,
                    instance_id="unused",
                    python_executable=sys.executable,
                    evaluator_script=Path(temporary) / "evaluator.py",
                    output_root=Path(temporary) / "output",
                    generation_commit="test-commit",
                    max_total_tokens=0,
                    allowed_path_patterns=("target.py",),
                )

    def test_docker_collection_requires_digest_pinned_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(BenchmarkError, "digest-pinned"):
                collect_swe_bench_lite_dev_episode(
                    benchmark_root=temporary,
                    instance_id="unused",
                    python_executable=sys.executable,
                    evaluator_script=Path(temporary) / "evaluator.py",
                    output_root=Path(temporary) / "output",
                    generation_commit="test-commit",
                    allowed_path_patterns=("target.py",),
                    sandbox_backend_name="docker",
                    sandbox_image="claw/swe-pilot:latest",
                    sandbox_python_executable="python",
                )

    def test_docker_collection_requires_container_python(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                BenchmarkError, "sandbox_python_executable"
            ):
                collect_swe_bench_lite_dev_episode(
                    benchmark_root=temporary,
                    instance_id="unused",
                    python_executable=sys.executable,
                    evaluator_script=Path(temporary) / "evaluator.py",
                    output_root=Path(temporary) / "output",
                    generation_commit="test-commit",
                    allowed_path_patterns=("target.py",),
                    sandbox_backend_name="docker",
                    sandbox_image="claw/swe-pilot@sha256:" + "a" * 64,
                )

    def test_workspace_pythonpath_supports_src_and_root_layouts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            value = _workspace_pythonpath(str(root), "external-entry")
            entries = value.split(os.pathsep)
            self.assertEqual(entries[0], str((root / "src").resolve()))
            self.assertEqual(entries[1], str(root.resolve()))
            self.assertEqual(entries[2], "external-entry")

    def test_import_inference_strips_python_repository_suffix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pvlib").mkdir()
            self.assertEqual(
                _infer_workspace_import_name("pvlib/pvlib-python", str(root)),
                "pvlib",
            )

    def test_import_probe_proves_module_comes_from_episode_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "sample_repo"
            package.mkdir()
            (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
            environment = dict(os.environ)
            environment["PYTHONPATH"] = _workspace_pythonpath(str(root))
            import_name = _infer_workspace_import_name(
                "owner/sample-repo", str(root)
            )
            result = _probe_workspace_import(
                cwd=str(root),
                python_executable=Path(sys.executable),
                import_name=import_name,
                environment=environment,
                required_modules=("json",),
            )
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["import_name"], "sample_repo")
            self.assertEqual(result["module_file"], "sample_repo/__init__.py")
            self.assertIn("json", result["required_modules"])

    def test_import_probe_rejects_missing_evaluator_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "sample_repo"
            package.mkdir()
            (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
            environment = dict(os.environ)
            environment["PYTHONPATH"] = _workspace_pythonpath(str(root))
            with self.assertRaisesRegex(
                BenchmarkError, "required evaluator modules"
            ):
                _probe_workspace_import(
                    cwd=str(root),
                    python_executable=Path(sys.executable),
                    import_name="sample_repo",
                    environment=environment,
                    required_modules=("claw_missing_evaluator_dependency",),
                )

    def test_import_probe_rejects_module_from_another_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            external = root / "external"
            package = external / "sample_repo"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(external)
            with self.assertRaisesRegex(
                BenchmarkError, "does not import the Episode workspace"
            ):
                _probe_workspace_import(
                    cwd=str(workspace),
                    python_executable=Path(sys.executable),
                    import_name="sample_repo",
                    environment=environment,
                )


if __name__ == "__main__":
    unittest.main()
