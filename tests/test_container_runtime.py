"""Contracts for the cross-platform Docker/Podman execution boundary."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from claw.agent_tools import (
    ToolExecutionContext,
    execute_tool,
    execute_tool_streaming,
)
from claw.container_runtime import OCIContainerConfig, OCIContainerRunner
from claw.episode import EpisodeOrchestrator, EpisodeState, workspace_hash
from claw.experiment.schemas import TaskSpec


class RecordingExecutor:
    def __init__(self, *, time_out_run=False):
        self.calls = []
        self.time_out_run = time_out_run
        self.create_args = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), dict(kwargs)))
        if args[1] == "version":
            return subprocess.CompletedProcess(args, 0, "Docker 27\n", "")
        if args[1:3] == ["image", "inspect"]:
            payload = [{"Id": "sha256:image", "RepoDigests": ["image@sha256:digest"]}]
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        if args[1] == "create":
            self.create_args = list(args)
            return subprocess.CompletedProcess(args, 0, "container-id\n", "")
        if args[1] == "start" and self.time_out_run:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        if args[1] == "start" and "claw-shell-contract=ok" in self.create_args[-1]:
            return subprocess.CompletedProcess(
                args,
                0,
                "claw-shell-contract=ok\n",
                "claw-workspace-ready\nclaw-shell-started\n",
            )
        if args[1] == "start":
            return subprocess.CompletedProcess(
                args, 0, "inside\n", "claw-workspace-ready\nclaw-shell-started\n"
            )
        if args[1] == "inspect":
            return subprocess.CompletedProcess(args, 0, '{"ExitCode":1}\n', "")
        return subprocess.CompletedProcess(args, 0, "inside\n", "")


class EpisodeCommandRunner:
    def __init__(self):
        self.commands = []

    def describe(self):
        return {
            "kind": "oci-container",
            "engine": "docker",
            "image_id": "sha256:image",
        }

    def run(self, command, *, cwd, timeout):
        self.commands.append((command, cwd, timeout))
        return subprocess.CompletedProcess(command, 1, "", "expected failure")

    def result_metadata(self, result):
        return {
            "execution_backend": "oci-container",
            "effective_command": result.args,
        }


class OCIContainerRunnerTests(unittest.TestCase):
    def test_disposable_workspace_contract_is_a_fail_closed_admission_gate(self):
        executor = RecordingExecutor()
        runner = OCIContainerRunner(
            OCIContainerConfig(image="python:3.11-slim"),
            executable="docker",
            executor=executor,
        )

        result = runner.verify_disposable_workspace_contract()

        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["candidate_snapshot_visible"])
        self.assertTrue(result["shell_mutations_discarded"])
        self.assertTrue(any(args[1] == "create" for args, _ in executor.calls))

    def test_builds_hardened_portable_run_invocation_and_pins_image(self):
        executor = RecordingExecutor()
        runner = OCIContainerRunner(
            OCIContainerConfig(image="python:3.11-slim"),
            executable="docker",
            executor=executor,
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = runner.run("python -V", cwd=temporary, timeout=12)

        self.assertEqual(result.returncode, 0)
        run = next(args for args, _ in executor.calls if args[1] == "create")
        for required in (
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--pids-limit",
            "--entrypoint",
        ):
            self.assertIn(required, run)
        self.assertEqual(run[-4:-1], ["/bin/sh", "python:3.11-slim", "-lc"])
        self.assertIn("exec /bin/sh -lc 'python -V'", run[-1])
        source_mount = run[run.index("--mount") + 1]
        self.assertIn("target=/claw-source,readonly", source_mount)
        self.assertTrue(
            any("/workspace:rw,exec,nosuid" in item for item in run)
        )
        if hasattr(__import__("os"), "getuid") and __import__("os").getuid() > 0:
            self.assertIn("--user", run)
        description = runner.describe()
        self.assertEqual(description["image_id"], "sha256:image")
        self.assertEqual(description["image_digest"], "image@sha256:digest")

    def test_timeout_force_removes_only_the_named_ephemeral_container(self):
        executor = RecordingExecutor(time_out_run=True)
        runner = OCIContainerRunner(
            OCIContainerConfig(image="python:3.11-slim"),
            executable="podman",
            executor=executor,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(subprocess.TimeoutExpired):
                runner.run("sleep 60", cwd=temporary, timeout=0.1)
        cleanup = [args for args, _ in executor.calls if args[1:3] == ["rm", "-f"]]
        self.assertEqual(len(cleanup), 1)
        self.assertEqual(cleanup[0][3], "-v")
        self.assertTrue(cleanup[0][4].startswith("claw-episode-"))

    def test_agent_bash_dispatches_through_configured_runner(self):
        executor = RecordingExecutor()
        runner = OCIContainerRunner(
            OCIContainerConfig(image="python:3.11-slim"),
            executable="docker",
            executor=executor,
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = execute_tool(
                "bash",
                {"command": "python -V"},
                ToolExecutionContext(
                    cwd=temporary,
                    permissions={"allow_shell": True},
                    command_runner=runner,
                ),
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, "inside\n")
        self.assertEqual(result.result["execution_backend"], "oci-container")

    def test_translates_model_visible_workspace_path_inside_shell_command(self):
        executor = RecordingExecutor()
        runner = OCIContainerRunner(
            OCIContainerConfig(image="python:3.11-slim"),
            executable="docker",
            executor=executor,
        )
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            runner.run(
                f"cd {workspace.as_posix()} && python -V",
                cwd=str(workspace),
                timeout=12,
            )
        run = next(args for args, _ in executor.calls if args[1] == "create")
        self.assertIn("cd /workspace && python -V", run[-1])

    def test_streaming_agent_bash_preserves_container_audit_metadata(self):
        executor = RecordingExecutor()
        runner = OCIContainerRunner(
            OCIContainerConfig(image="python:3.11-slim"),
            executable="docker",
            executor=executor,
        )
        with tempfile.TemporaryDirectory() as temporary:
            events = list(
                execute_tool_streaming(
                    "bash",
                    {"command": "python -V"},
                    ToolExecutionContext(
                        cwd=temporary,
                        permissions={"allow_shell": True},
                        command_runner=runner,
                    ),
                )
            )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["execution_backend"], "oci-container")
        self.assertEqual(events[0]["effective_command"], "python -V")

    def test_container_mounts_source_readonly_and_discards_shell_writes(self):
        mounted_sources = []
        create_args = []

        def executor(args, **kwargs):
            if args[1] == "version":
                return subprocess.CompletedProcess(args, 0, "Docker 27\n", "")
            if args[1:3] == ["image", "inspect"]:
                return subprocess.CompletedProcess(
                    args, 0, json.dumps([{"Id": "sha256:image"}]), ""
                )
            if args[1] == "create":
                create_args[:] = args
                mount = args[args.index("--mount") + 1]
                source = mount.split("source=", 1)[1].split(",target=", 1)[0]
                mounted_sources.append(Path(source))
                return subprocess.CompletedProcess(args, 0, "container-id\n", "")
            if args[1] == "start":
                return subprocess.CompletedProcess(
                    args,
                    0,
                    "inside\n",
                    "claw-workspace-ready\nclaw-shell-started\n",
                )
            return subprocess.CompletedProcess(args, 0, "inside\n", "")

        runner = OCIContainerRunner(
            OCIContainerConfig(image="python:3.11-slim"),
            executable="docker",
            executor=executor,
        )
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "tests.txt").write_text("original\n", encoding="utf-8")
            result = runner.run("echo mutated > tests.txt", cwd=temporary, timeout=12)
            self.assertEqual(
                (workspace / "tests.txt").read_text(encoding="utf-8"), "original\n"
            )

        self.assertEqual(result.returncode, 0)
        self.assertTrue(mounted_sources)
        self.assertEqual(mounted_sources[0], workspace.resolve())
        self.assertTrue(
            any("target=/claw-source,readonly" in item for item in create_args)
        )
        self.assertEqual(runner.result_metadata(result)["workspace_mutations"], "discarded")
        self.assertEqual(runner.result_metadata(result)["workspace_copy_mode"], "container")

    def test_diagnostics_distinguish_create_workspace_and_task_failures(self):
        class FailingExecutor(RecordingExecutor):
            def __init__(self, failure):
                super().__init__()
                self.failure = failure

            def __call__(self, args, **kwargs):
                if args[1] == "create" and self.failure == "create":
                    return subprocess.CompletedProcess(args, 1, "", "mount denied")
                if args[1] == "start" and self.failure == "workspace":
                    return subprocess.CompletedProcess(
                        args, 1, "", "claw-shell-started\ncopy failed\n"
                    )
                if args[1] == "start" and self.failure == "task":
                    return subprocess.CompletedProcess(
                        args,
                        1,
                        "",
                        "claw-workspace-ready\nclaw-shell-started\ntest failed\n",
                    )
                return super().__call__(args, **kwargs)

        expected = {
            "create": "container_create",
            "workspace": "workspace_materialization",
            "task": "task_command",
        }
        for failure, stage in expected.items():
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                runner = OCIContainerRunner(
                    OCIContainerConfig(image="python:3.11-slim"),
                    executable="docker",
                    executor=FailingExecutor(failure),
                )
                result = runner.run("false", cwd=temporary, timeout=12)
                metadata = runner.result_metadata(result)
                self.assertEqual(metadata["failure_stage"], stage)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("claw-shell-started", result.stderr)

    def test_cleanup_failure_converts_success_to_infrastructure_failure(self):
        class CleanupFailingExecutor(RecordingExecutor):
            def __call__(self, args, **kwargs):
                if args[1:3] == ["rm", "-f"]:
                    return subprocess.CompletedProcess(args, 1, "", "daemon busy")
                return super().__call__(args, **kwargs)

        runner = OCIContainerRunner(
            OCIContainerConfig(image="python:3.11-slim"),
            executable="docker",
            executor=CleanupFailingExecutor(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = runner.run("true", cwd=temporary, timeout=12)

        metadata = runner.result_metadata(result)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(metadata["failure_stage"], "container_cleanup")
        self.assertEqual(metadata["container_cleanup_returncode"], 1)
        self.assertIn("daemon busy", result.stderr)

    def test_rejects_non_isolated_network_profile(self):
        with self.assertRaisesRegex(ValueError, "network=none"):
            OCIContainerConfig(
                image="python:3.11-slim", network="bridge"
            ).validate()

    def test_rejects_workspace_target_that_conflicts_with_internal_mount(self):
        with self.assertRaisesRegex(ValueError, "internal mount"):
            OCIContainerConfig(
                image="python:3.11-slim", workspace_target="/claw-source"
            ).validate()

    def test_windows_cli_translates_wsl_drive_mount(self):
        runner = OCIContainerRunner(
            OCIContainerConfig(image="python:3.11-slim"),
            executable="docker.exe",
            executor=RecordingExecutor(),
        )
        self.assertEqual(
            runner._mount_source(Path("/mnt/d/project/workspace")),
            "D:\\project\\workspace",
        )

    def test_episode_initial_checks_use_runner_and_archive_backend_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template = root / "template"
            template.mkdir()
            (template / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
            task = TaskSpec(
                task_id="container-task",
                task_version="1.0.0",
                family_id="container-family",
                domain="python-cli",
                task_type="fix_bug",
                difficulty="easy",
                split="test",
                prompt="Set VALUE to 1.",
                template_ref=str(template),
                template_hash=workspace_hash(template, normalize_exec=True),
                initial_checks=["python -c 'raise SystemExit(1)'"],
                test_commands=["python -c 'import app'"],
                timeout_seconds=30,
                source="test",
                license="MIT",
            )
            task.content_hash = task.compute_content_hash()
            runner = EpisodeCommandRunner()
            orchestrator = EpisodeOrchestrator(
                root / "episodes", project_root=root, command_runner=runner
            )
            manifest = orchestrator.prepare(task, episode_id="ep-container")

        self.assertEqual(manifest.current_state, EpisodeState.READY)
        self.assertEqual(len(runner.commands), 1)
        self.assertEqual(
            manifest.metadata["execution_backend"]["image_id"],
            "sha256:image",
        )
        self.assertEqual(
            manifest.metadata["initial_check_results"][0]["execution_backend"],
            "oci-container",
        )


if __name__ == "__main__":
    unittest.main()
