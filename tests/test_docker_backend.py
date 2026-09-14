"""Docker backend contract tests that do not require a Docker daemon."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from claw.agent_runtime import LocalCodingAgent
from claw.agent_session import AgentSession
from claw.agent_tools import execute_tool
from claw.docker_backend import DockerBackend, DockerCLIResult
from claw.episode import EpisodeOrchestrator, workspace_hash
from claw.experiment.schemas import TaskSpec
from claw.sandbox_backend import (
    ExecRequest,
    MountSpec,
    NetworkMode,
    NetworkPolicy,
    SandboxBackendError,
    SandboxErrorCode,
    SandboxRuntimeTier,
    SandboxSpec,
    SandboxState,
    WorkspaceSpec,
)


IMAGE_DIGEST = "sha256:" + "a" * 64


class FakeDockerRunner:
    def __init__(self) -> None:
        self.calls = []
        self.state = "created"
        self.exec_result = DockerCLIResult(0, "container-output\n", "")
        self.exec_results = []
        self.timeout_on_exec = False

    def __call__(self, argv, timeout):
        argv = tuple(argv)
        self.calls.append((argv, timeout))
        action = argv[1]
        if action == "version":
            return DockerCLIResult(0, "27.1.0\n", "")
        if action == "image":
            image_ref = argv[-1]
            digest = (
                image_ref.rsplit("@", 1)[-1]
                if "@" in image_ref
                else IMAGE_DIGEST
            )
            repository = image_ref.split("@", 1)[0]
            return DockerCLIResult(
                0,
                json.dumps(
                    [{"Id": digest, "RepoDigests": [f"{repository}@{digest}"]}]
                ),
                "",
            )
        if action == "create":
            self.state = "created"
            return DockerCLIResult(0, "container-id\n", "")
        if action == "start":
            self.state = "running"
            return DockerCLIResult(0, argv[-1] + "\n", "")
        if action == "inspect":
            return DockerCLIResult(0, json.dumps({"Status": self.state}), "")
        if action == "exec":
            if self.timeout_on_exec:
                raise subprocess.TimeoutExpired(
                    argv,
                    timeout,
                    output=b"partial-output",
                    stderr=b"",
                )
            if self.exec_results:
                return self.exec_results.pop(0)
            return self.exec_result
        if action in {"stop", "kill"}:
            self.state = "exited"
            return DockerCLIResult(0, "", "")
        if action == "rm":
            self.state = "removed"
            return DockerCLIResult(0, "", "")
        raise AssertionError(f"unexpected Docker argv: {argv}")

    def calls_for(self, action):
        return [argv for argv, _timeout in self.calls if argv[1] == action]


class DockerBackendContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace_context = tempfile.TemporaryDirectory()
        self.workspace = self.workspace_context.name
        self.runner = FakeDockerRunner()
        self.backend = DockerBackend(runner=self.runner, cleanup_on_exit=False)
        self.spec = SandboxSpec.for_docker_workspace(
            self.workspace,
            image="python:3.12-slim",
            owner_id="docker-test-owner",
            sandbox_id="docker-test-sandbox",
        )

    def tearDown(self) -> None:
        self.workspace_context.cleanup()

    def _running_handle(self):
        handle = self.backend.prepare(self.spec)
        self.backend.start(handle)
        return handle

    def test_capabilities_report_container_without_unsupported_claims(self) -> None:
        capabilities = self.backend.capabilities()
        self.assertEqual(
            capabilities.isolation_tiers,
            (SandboxRuntimeTier.CONTAINER,),
        )
        self.assertIn(NetworkMode.NONE, capabilities.network_modes)
        self.assertNotIn(NetworkMode.ALLOWLIST, capabilities.network_modes)
        self.assertFalse(capabilities.supports_streaming_exec)
        self.assertTrue(capabilities.supports_verified_image_identity)
        self.assertFalse(capabilities.admission_enforced_out_of_process)

    def test_prepare_builds_hardened_structured_create_argv(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-enter"}):
            handle = self.backend.prepare(self.spec)

        create = self.runner.calls_for("create")[0]
        create_text = "\n".join(create)
        self.assertEqual(handle.state, SandboxState.READY)
        self.assertEqual(
            handle.backend_metadata["docker_server_version"],
            "27.1.0",
        )
        self.assertIn("--read-only", create)
        self.assertIn("--init", create)
        self.assertIn("--cap-drop", create)
        self.assertEqual(create[create.index("--cap-drop") + 1], "ALL")
        self.assertIn("no-new-privileges=true", create)
        self.assertEqual(create[create.index("--network") + 1], "none")
        self.assertNotEqual(
            create[create.index("--user") + 1].split(":")[0],
            "0",
        )
        self.assertIn("--cpus", create)
        self.assertIn("--memory", create)
        self.assertIn("--pids-limit", create)
        tmpfs_mounts = [
            create[index + 1]
            for index, value in enumerate(create)
            if value == "--tmpfs"
        ]
        self.assertIn("/tmp:rw,nosuid,nodev,size=256m", tmpfs_mounts)
        self.assertIn(
            "/dev/shm:rw,nosuid,nodev,noexec,size=64m",
            tmpfs_mounts,
        )
        self.assertNotIn("--privileged", create)
        self.assertNotIn("OPENAI_API_KEY", create_text)
        mount = create[create.index("--mount") + 1]
        self.assertIn(f"src={os.path.realpath(self.workspace)}", mount)
        self.assertIn("dst=/workspace", mount)

    def test_start_exec_stop_destroy_lifecycle(self) -> None:
        handle = self._running_handle()
        result = self.backend.exec(
            handle,
            ExecRequest(
                command="printf hello",
                cwd=".",
                environment={"CLAW_SAFE_FLAG": "yes"},
            ),
        )
        exec_argv = self.runner.calls_for("exec")[0]
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, "container-output\n")
        self.assertEqual(result.backend_name, "docker")
        self.assertEqual(
            exec_argv[exec_argv.index("--workdir") + 1],
            "/workspace",
        )
        self.assertIn("CLAW_SAFE_FLAG=yes", exec_argv)
        self.assertEqual(
            exec_argv[-3:],
            ("/bin/sh", "-lc", "printf hello"),
        )

        self.backend.stop(handle)
        self.assertEqual(handle.state, SandboxState.STOPPED)
        self.backend.destroy(handle)
        self.assertEqual(handle.state, SandboxState.DESTROYED)
        self.backend.destroy(handle)

    def test_stop_is_idempotent_before_first_start(self) -> None:
        handle = self.backend.prepare(self.spec)

        self.backend.stop(handle)
        self.backend.stop(handle)

        self.assertEqual(handle.state, SandboxState.STOPPED)
        self.assertEqual(self.runner.calls_for("stop"), [])
        self.backend.start(handle)
        self.assertEqual(handle.state, SandboxState.RUNNING)

    def test_timeout_kills_container_and_returns_structured_result(self) -> None:
        handle = self._running_handle()
        self.runner.timeout_on_exec = True
        result = self.backend.exec(
            handle,
            ExecRequest(command="sleep 30", timeout_seconds=0.1),
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.error_code, SandboxErrorCode.EXEC_TIMEOUT)
        self.assertEqual(result.stdout, "partial-output")
        self.assertEqual(handle.state, SandboxState.STOPPED)
        self.assertEqual(len(self.runner.calls_for("kill")), 1)

    def test_request_environment_rejects_secrets_and_managed_values(self) -> None:
        handle = self._running_handle()
        for environment in (
            {"SERVICE_TOKEN": "secret"},
            {"HOME": "/root"},
            {"LD_PRELOAD": "/tmp/inject.so"},
        ):
            with self.subTest(environment=environment):
                with self.assertRaises(SandboxBackendError) as raised:
                    self.backend.exec(
                        handle,
                        ExecRequest(command=("true",), environment=environment),
                    )
                self.assertEqual(
                    raised.exception.code,
                    SandboxErrorCode.DENIED_BY_POLICY,
                )

    def test_isolated_profiles_reject_network_and_missing_limits(self) -> None:
        online = replace(
            self.spec,
            network=NetworkPolicy(
                mode=NetworkMode.UNRESTRICTED,
                block_metadata=False,
            ),
        )
        with self.assertRaises(SandboxBackendError) as raised:
            self.backend.prepare(online)
        self.assertEqual(raised.exception.code, SandboxErrorCode.DENIED_BY_POLICY)

        without_limits = replace(
            self.spec,
            sandbox_id="docker-no-limits",
            resources=replace(self.spec.resources, memory_mb=None),
        )
        with self.assertRaises(SandboxBackendError) as raised:
            self.backend.prepare(without_limits)
        self.assertEqual(raised.exception.code, SandboxErrorCode.DENIED_BY_POLICY)

        allowlist = replace(
            self.spec,
            sandbox_id="docker-allowlist",
            network=NetworkPolicy(
                mode=NetworkMode.ALLOWLIST,
                allowed_domains=("pypi.org",),
            ),
        )
        with self.assertRaises(SandboxBackendError) as raised:
            self.backend.prepare(allowlist)
        self.assertEqual(
            raised.exception.code,
            SandboxErrorCode.CAPABILITY_UNAVAILABLE,
        )

    def test_benchmark_profile_requires_pinned_image(self) -> None:
        unpinned = replace(
            self.spec,
            sandbox_id="benchmark-unpinned",
            security_profile="benchmark_offline",
        )
        with self.assertRaises(SandboxBackendError) as raised:
            self.backend.prepare(unpinned)
        self.assertEqual(raised.exception.code, SandboxErrorCode.DENIED_BY_POLICY)

        pinned = SandboxSpec.for_docker_workspace(
            self.workspace,
            image=f"python:3.12-slim@{IMAGE_DIGEST}",
            owner_kind="episode",
            owner_id="benchmark-owner",
            sandbox_id="benchmark-pinned",
            security_profile="benchmark_offline",
        )
        handle = self.backend.prepare(pinned)
        self.assertEqual(
            handle.backend_metadata["image_reference"],
            f"python:3.12-slim@{IMAGE_DIGEST}",
        )

    def test_mount_admission_requires_owner_allowlist_and_read_only_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as mount_root:
            runtime_dir = os.path.join(mount_root, "runtime")
            os.mkdir(runtime_dir)
            backend = DockerBackend(
                runner=self.runner,
                allowed_mount_roots=(mount_root,),
                cleanup_on_exit=False,
            )
            admitted = replace(
                self.spec,
                sandbox_id="docker-extra-mount",
                mounts=(
                    MountSpec(
                        host_path=runtime_dir,
                        sandbox_path="/opt/claw-runtime",
                        mode="ro",
                        kind="runtime",
                        owner_scope=self.spec.owner_id,
                    ),
                ),
            )
            backend.prepare(admitted)
            create = self.runner.calls_for("create")[-1]
            mount_values = [
                create[index + 1]
                for index, value in enumerate(create)
                if value == "--mount"
            ]
            self.assertTrue(
                any("dst=/opt/claw-runtime" in item for item in mount_values)
            )
            self.assertTrue(
                any(item.endswith(",readonly") for item in mount_values)
            )

            wrong_owner = replace(
                admitted,
                sandbox_id="docker-wrong-owner",
                mounts=(replace(admitted.mounts[0], owner_scope="someone-else"),),
            )
            with self.assertRaises(SandboxBackendError):
                backend.prepare(wrong_owner)

            writable_runtime = replace(
                admitted,
                sandbox_id="docker-writable-runtime",
                mounts=(replace(admitted.mounts[0], mode="rw"),),
            )
            with self.assertRaises(SandboxBackendError):
                backend.prepare(writable_runtime)

    def test_mount_symlink_escape_and_target_traversal_are_denied(self) -> None:
        with tempfile.TemporaryDirectory() as mount_root:
            with tempfile.TemporaryDirectory() as outside:
                symlink = os.path.join(mount_root, "escape")
                os.symlink(outside, symlink)
                backend = DockerBackend(
                    runner=self.runner,
                    allowed_mount_roots=(mount_root,),
                    cleanup_on_exit=False,
                )
                escaped = replace(
                    self.spec,
                    sandbox_id="docker-symlink-escape",
                    mounts=(
                        MountSpec(
                            host_path=symlink,
                            sandbox_path="/external",
                            mode="ro",
                            kind="allowlisted_extra",
                            owner_scope=self.spec.owner_id,
                        ),
                    ),
                )
                with self.assertRaises(SandboxBackendError) as raised:
                    backend.prepare(escaped)
                self.assertEqual(
                    raised.exception.code,
                    SandboxErrorCode.DENIED_BY_POLICY,
                )

        traversing = replace(
            self.spec,
            sandbox_id="docker-target-traversal",
            workspace=WorkspaceSpec(
                host_path=self.workspace,
                sandbox_path="/workspace/../host",
            ),
        )
        with self.assertRaises(SandboxBackendError) as raised:
            self.backend.prepare(traversing)
        self.assertEqual(raised.exception.code, SandboxErrorCode.SPEC_INVALID)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires Unix special files")
    def test_special_file_mount_is_denied(self) -> None:
        with tempfile.TemporaryDirectory() as mount_root:
            special_path = os.path.join(mount_root, "docker.sock")
            os.mkfifo(special_path)
            backend = DockerBackend(
                runner=self.runner,
                allowed_mount_roots=(mount_root,),
                cleanup_on_exit=False,
            )
            spec = replace(
                self.spec,
                sandbox_id="docker-socket-mount",
                mounts=(
                    MountSpec(
                        host_path=special_path,
                        sandbox_path="/socket",
                        mode="ro",
                        kind="allowlisted_extra",
                        owner_scope=self.spec.owner_id,
                    ),
                ),
            )
            with self.assertRaises(SandboxBackendError) as raised:
                backend.prepare(spec)
            self.assertEqual(
                raised.exception.code,
                SandboxErrorCode.DENIED_BY_POLICY,
            )

    def test_missing_docker_cli_does_not_fallback_to_host(self) -> None:
        backend = DockerBackend(docker_command="/definitely/missing/docker")
        with self.assertRaises(SandboxBackendError) as raised:
            backend.prepare(self.spec)
        self.assertEqual(
            raised.exception.code,
            SandboxErrorCode.BACKEND_UNAVAILABLE,
        )
        self.assertEqual(backend.list_owned(self.spec.owner_id), ())

    def test_agent_shell_uses_docker_handle_and_forces_workspace_scope(self) -> None:
        agent = LocalCodingAgent(
            cwd=self.workspace,
            permissions={"allow_shell": True, "restrict_workspace": False},
            sandbox_backend=self.backend,
            sandbox_spec=self.spec,
        )
        agent.session = AgentSession(session_id="docker-agent-session")
        context = agent._tool_execution_context(agent.permissions)
        result = execute_tool(
            "bash",
            {"command": "printf hello"},
            context=context,
        )
        self.assertTrue(context.permissions["restrict_workspace"])
        self.assertTrue(result.ok)
        self.assertEqual(result.result["backend_name"], "docker")
        self.assertEqual(result.result["sandbox_id"], self.spec.sandbox_id)
        self.assertEqual(
            agent.session.metadata["sandbox"]["image_reference"],
            "python:3.12-slim",
        )
        self.assertEqual(
            agent.session.metadata["sandbox"]["security_profile"],
            "isolated_development",
        )
        self.assertEqual(agent.session.metadata["sandbox"]["network_mode"], "none")
        self.assertEqual(agent.session.metadata["sandbox"]["generation"], 1)

    def test_agent_docker_selection_requires_explicit_image(self) -> None:
        agent = LocalCodingAgent(
            cwd=self.workspace,
            sandbox_backend_name="docker",
        )
        agent.session = AgentSession(session_id="docker-missing-image")
        with self.assertRaisesRegex(ValueError, "explicit sandbox_image"):
            agent._tool_execution_context({"allow_shell": True})

        with self.assertRaisesRegex(ValueError, "Unsupported sandbox backend"):
            LocalCodingAgent(
                cwd=self.workspace,
                sandbox_backend_name="mystery",
            )

    def test_episode_uses_fresh_digest_pinned_docker_verifiers(self) -> None:
        template = Path(self.workspace) / "template"
        template.mkdir()
        (template / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
        episodes = Path(self.workspace) / "episodes"
        image = f"python@{IMAGE_DIGEST}"
        task = TaskSpec(
            task_id="docker-episode-task",
            task_version="1.0.0",
            family_id="docker-episode-family",
            domain="python-cli",
            task_type="fix_bug",
            difficulty="easy",
            split="test",
            prompt="Set VALUE to 1.",
            template_ref=str(template),
            template_hash=workspace_hash(template),
            initial_checks=["python -c 'raise SystemExit(1)'"],
            test_commands=["python -c 'import app; assert app.VALUE == 1'"],
            timeout_seconds=30,
            source="test",
            license="MIT",
        )
        task.content_hash = task.compute_content_hash()
        self.runner.exec_results = [
            DockerCLIResult(1, "", "expected initial failure"),
            DockerCLIResult(0, "verified\n", ""),
        ]
        orchestrator = EpisodeOrchestrator(
            episodes,
            project_root=self.workspace,
            sandbox_backend_name="docker",
            sandbox_image=image,
            sandbox_security_profile="benchmark_offline",
            verification_backend=self.backend,
        )

        manifest = orchestrator.prepare(task, episode_id="docker-episode")
        (orchestrator.workspace / "app.py").write_text(
            "VALUE = 1\n",
            encoding="utf-8",
        )
        orchestrator.start_run()
        facts = orchestrator.collect_verification_facts(task)

        self.assertEqual(facts["test_result"]["passed_tests"], 1)
        evidence = manifest.metadata["sandbox_executions"]
        self.assertEqual(len(evidence), 2)
        self.assertEqual(
            [item["purpose"] for item in evidence],
            ["initial_check", "final_verification"],
        )
        self.assertEqual(len({item["sandbox_id"] for item in evidence}), 2)
        self.assertTrue(
            all(item["owner_kind"] == "verification" for item in evidence)
        )
        self.assertTrue(
            all(item["security_profile"] == "benchmark_offline" for item in evidence)
        )
        self.assertTrue(all(item["network_mode"] == "none" for item in evidence))
        self.assertTrue(
            all(IMAGE_DIGEST in item["image_identity"] for item in evidence)
        )
        self.assertEqual(len(self.runner.calls_for("create")), 2)
        self.assertEqual(len(self.runner.calls_for("rm")), 2)

        reopened = EpisodeOrchestrator(
            episodes,
            project_root=self.workspace,
        )
        reopened.open("docker-episode")
        self.assertEqual(reopened.sandbox_backend_name, "docker")
        self.assertEqual(reopened.sandbox_image, image)
        self.assertEqual(
            reopened.sandbox_security_profile,
            "benchmark_offline",
        )


@unittest.skipUnless(
    os.environ.get("CLAW_TEST_DOCKER_IMAGE"),
    "set CLAW_TEST_DOCKER_IMAGE to run the live Docker contract",
)
class DockerBackendLiveTest(unittest.TestCase):
    def test_live_container_is_offline_non_root_and_secret_free(self) -> None:
        image = os.environ["CLAW_TEST_DOCKER_IMAGE"]
        with tempfile.TemporaryDirectory() as workspace:
            backend = DockerBackend(cleanup_on_exit=False)
            spec = SandboxSpec.for_docker_workspace(
                workspace,
                image=image,
                owner_id="live-docker-test",
                sandbox_id="live-docker-test",
            )
            with patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-enter"}):
                handle = backend.prepare(spec)
                try:
                    backend.start(handle)
                    result = backend.exec(
                        handle,
                        ExecRequest(
                            command=(
                                "/bin/sh",
                                "-c",
                                "test \"$(id -u)\" != 0 && "
                                "test -z \"$OPENAI_API_KEY\" && "
                                "! grep -q '^[^ ]*[[:space:]]00000000' "
                                "/proc/net/route",
                            )
                        ),
                    )
                    self.assertTrue(result.ok, result.stderr)
                finally:
                    backend.destroy(handle)


if __name__ == "__main__":
    unittest.main()
