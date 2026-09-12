"""Episode orchestration across prepare, run, verify, archive, reset, and rerun."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import uuid
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from ..agent_session import AgentSession
from ..experiment.schemas import TaskSpec
from ..sandbox_backend import (
    ExecRequest,
    HostBackend,
    SandboxBackend,
    SandboxBackendError,
    SandboxHandle,
    SandboxSpec,
)
from ..task_commands import resolve_task_command
from .checkpoint import (
    CheckpointIntegrityError,
    CheckpointManager,
    CheckpointSnapshot,
    initialize_git,
    validate_workspace_symlinks,
    workspace_hash,
)
from .state import EpisodeManifest, EpisodeState, EpisodeStateError


def _remove_readonly(function, path, _error_info):
    """Allow deletion of read-only Git objects on Windows."""
    os.chmod(path, stat.S_IWRITE)
    function(path)


class InitialValidationError(RuntimeError):
    """Raised when an initial task template is already passing or cannot run."""


class RerunMode(str, Enum):
    FULL = "full-rerun"
    CHECKPOINT = "checkpoint-rerun"


class EpisodeOrchestrator:
    """Own one isolated episode directory and its durable lifecycle manifest."""

    MANIFEST_NAME = "episode.json"

    def __init__(
        self,
        episodes_root: Union[str, os.PathLike[str]],
        *,
        project_root: Optional[Union[str, os.PathLike[str]]] = None,
        environment_allowlist: Optional[Iterable[str]] = None,
        command_runner: Optional[Any] = None,
        sandbox_backend_name: Optional[str] = None,
        sandbox_image: Optional[str] = None,
        sandbox_security_profile: Optional[str] = None,
        verification_backend: Optional[SandboxBackend] = None,
    ):
        self.episodes_root = Path(episodes_root).resolve()
        self.episodes_root.mkdir(parents=True, exist_ok=True)
        self.project_root = Path(project_root or os.getcwd()).resolve()
        self.environment_allowlist = tuple(environment_allowlist or ())
        self.command_runner = command_runner
        inferred_backend = (
            str(getattr(verification_backend, "name", "")).strip().lower()
            if verification_backend is not None
            else ""
        )
        self._sandbox_backend_explicit = sandbox_backend_name is not None
        self.sandbox_backend_name = str(
            sandbox_backend_name or inferred_backend or "host"
        ).strip().lower()
        if self.sandbox_backend_name not in {"host", "docker"}:
            raise ValueError(
                f"Unsupported episode sandbox backend: {sandbox_backend_name!r}"
            )
        if verification_backend is not None:
            backend_name = str(getattr(verification_backend, "name", ""))
            if backend_name != self.sandbox_backend_name:
                raise ValueError(
                    "verification_backend does not match sandbox_backend_name"
                )
        if self.sandbox_backend_name == "docker" and not sandbox_image:
            raise ValueError(
                "Docker episode sandbox requires an explicit sandbox_image"
            )
        if self.sandbox_backend_name != "docker" and sandbox_image:
            raise ValueError(
                "sandbox_image is only valid for the Docker episode backend"
            )
        self.sandbox_image = sandbox_image
        self.sandbox_security_profile = sandbox_security_profile or (
            "isolated_development"
            if self.sandbox_backend_name == "docker"
            else "host_development"
        )
        if (
            self.sandbox_backend_name == "host"
            and self.sandbox_security_profile != "host_development"
        ):
            raise ValueError(
                "Host episode backend requires host_development profile"
            )
        self._verification_backend = verification_backend
        self._verification_handle: Optional[SandboxHandle] = None
        if command_runner is not None and (
            self.sandbox_backend_name != "host"
            or verification_backend is not None
        ):
            raise ValueError(
                "command_runner cannot be combined with an explicit sandbox backend"
            )
        self.manifest: Optional[EpisodeManifest] = None
        self.episode_dir: Optional[Path] = None
        self.workspace: Optional[Path] = None
        self.checkpoints: Optional[CheckpointManager] = None
        self._processes: List[Any] = []

    def prepare(
        self,
        task: TaskSpec,
        *,
        episode_id: Optional[str] = None,
        runtime_config_ref: str = "",
        model_config_ref: str = "",
        prompt_version: str = "unknown",
        tool_version: str = "unknown",
        verify_template_hash: bool = True,
    ) -> EpisodeManifest:
        task.validate()
        if not task.initial_checks:
            raise InitialValidationError(
                "task requires initial_checks proving the template initially fails"
            )
        episode_id = episode_id or f"ep_{uuid.uuid4().hex[:20]}"
        self._bind_episode(episode_id)
        assert self.episode_dir is not None
        assert self.workspace is not None

        if self.episode_dir.exists():
            raise FileExistsError(f"episode already exists: {self.episode_dir}")
        self.workspace.mkdir(parents=True)
        self.manifest = EpisodeManifest(
            episode_id=episode_id,
            task_ref=f"{task.task_id}@{task.task_version}",
            workspace_path=str(self.workspace),
            template_hash=task.template_hash,
            initial_commit="",
            runtime_config_ref=runtime_config_ref,
            model_config_ref=model_config_ref,
            prompt_version=prompt_version,
            tool_version=tool_version,
            metadata={
                "task_schema_version": task.schema_version,
                "task_content_hash": task.content_hash or task.compute_content_hash(),
                "execution_backend": (
                    self.command_runner.describe()
                    if self.command_runner is not None
                    else {"kind": "native"}
                ),
                "sandbox_config": {
                    "backend": self.sandbox_backend_name,
                    "image_reference": self.sandbox_image or "",
                    "security_profile": self.sandbox_security_profile,
                },
            },
        )
        self._save()
        self.manifest.transition(EpisodeState.PREPARING)
        self._save()

        try:
            template = Path(task.template_ref)
            if not template.is_absolute():
                template = self.project_root / template
            template = template.resolve()
            if not template.is_dir():
                raise FileNotFoundError(f"task template not found: {template}")
            try:
                validate_workspace_symlinks(template)
            except CheckpointIntegrityError as exc:
                raise InitialValidationError(str(exc)) from exc
            shutil.copytree(
                template,
                self.workspace,
                dirs_exist_ok=True,
                symlinks=True,
            )

            actual_template_hash = workspace_hash(
                self.workspace, normalize_exec=True
            )
            if verify_template_hash and actual_template_hash != task.template_hash:
                raise InitialValidationError(
                    f"template hash mismatch: expected {task.template_hash}, "
                    f"got {actual_template_hash}"
                )
            self.manifest.metadata["observed_template_hash"] = actual_template_hash
            self.manifest.initial_commit = initialize_git(self.workspace)

            initial_results = self._run_task_checks(
                task,
                task.initial_checks,
                timeout=task.timeout_seconds,
                purpose="initial_check",
            )
            if self.command_runner is not None:
                self.manifest.metadata["execution_backend"] = (
                    self.command_runner.describe()
                )
            self.manifest.metadata["initial_check_results"] = initial_results
            if any(result.get("timed_out") for result in initial_results):
                raise InitialValidationError("initial task validation timed out")
            sandbox_failures = [
                result
                for result in initial_results
                if result.get("sandbox_error_code")
            ]
            if sandbox_failures:
                raise InitialValidationError(
                    "initial task validation sandbox failed: "
                    + str(sandbox_failures[0]["sandbox_error_code"])
                )
            if all(result["returncode"] == 0 for result in initial_results):
                raise InitialValidationError(
                    "initial task validation unexpectedly passed all checks"
                )

            self._reset_git(self.manifest.initial_commit)
            self.checkpoints = CheckpointManager(
                episode_id=episode_id,
                episode_dir=self.episode_dir,
                workspace=self.workspace,
                environment_allowlist=self.environment_allowlist,
            )
            initial_checkpoint = self.checkpoints.create(label="initial")
            self.manifest.add_checkpoint(initial_checkpoint.checkpoint_id)
            self.manifest.initial_checkpoint_id = initial_checkpoint.checkpoint_id
            self.manifest.transition(EpisodeState.READY)
            self._save()
            return self.manifest
        except Exception as exc:
            if self.manifest.current_state == EpisodeState.PREPARING:
                self.manifest.transition(EpisodeState.FAILED, error=str(exc))
                self._save()
            raise

    def open(self, episode_id: str) -> EpisodeManifest:
        self._bind_episode(episode_id)
        assert self.episode_dir is not None
        assert self.workspace is not None
        self.manifest = EpisodeManifest.load(
            self.episode_dir / self.MANIFEST_NAME
        )
        if not self._sandbox_backend_explicit:
            self._restore_persisted_sandbox_config()
        if Path(self.manifest.workspace_path).resolve() != self.workspace:
            raise EpisodeStateError("manifest workspace does not match episode path")
        self.checkpoints = CheckpointManager(
            episode_id=episode_id,
            episode_dir=self.episode_dir,
            workspace=self.workspace,
            environment_allowlist=self.environment_allowlist,
        )
        return self.manifest

    def _restore_persisted_sandbox_config(self) -> None:
        assert self.manifest is not None
        config = self.manifest.metadata.get("sandbox_config", {})
        if not isinstance(config, dict) or not config:
            return
        backend_name = str(config.get("backend", "")).strip().lower()
        if backend_name not in {"host", "docker"}:
            raise EpisodeStateError(
                f"unsupported persisted episode sandbox backend: {backend_name!r}"
            )
        image_reference = str(config.get("image_reference", "") or "")
        security_profile = str(
            config.get("security_profile", "")
            or (
                "benchmark_offline"
                if backend_name == "docker"
                else "host_development"
            )
        )
        if backend_name == "docker" and not image_reference:
            raise EpisodeStateError(
                "persisted Docker episode has no image reference"
            )
        if self._verification_backend is not None and getattr(
            self._verification_backend,
            "name",
            "",
        ) != backend_name:
            raise EpisodeStateError(
                "persisted episode backend does not match verification backend"
            )
        self.sandbox_backend_name = backend_name
        self.sandbox_image = image_reference or None
        self.sandbox_security_profile = security_profile

    def start_run(self) -> None:
        self._require_bound()
        assert self.manifest is not None
        self.manifest.transition(EpisodeState.RUNNING)
        self._save()

    def begin_verification(
        self, *, trajectory_ref: Optional[str] = None
    ) -> None:
        self._require_bound()
        assert self.manifest is not None
        if trajectory_ref:
            self.manifest.trajectory_ref = trajectory_ref
        self.manifest.transition(EpisodeState.VERIFYING)
        self._save()

    def archive(
        self,
        *,
        trajectory_ref: str,
        verification_ref: str,
    ) -> EpisodeManifest:
        self._require_bound()
        assert self.manifest is not None
        if self.manifest.current_state == EpisodeState.ARCHIVED:
            if (
                self.manifest.trajectory_ref != trajectory_ref
                or self.manifest.verification_ref != verification_ref
            ):
                raise EpisodeStateError(
                    "archived episode cannot be given different conclusions"
                )
            return self.manifest
        self.manifest.trajectory_ref = trajectory_ref
        self.manifest.verification_ref = verification_ref
        self.manifest.transition(EpisodeState.ARCHIVED)
        self._save()
        return self.manifest

    def create_checkpoint(
        self,
        *,
        label: str = "",
        agent_session: Optional[Union[AgentSession, Dict[str, Any]]] = None,
        runtime_states: Optional[Dict[str, Any]] = None,
        runtime_config: Optional[Dict[str, Any]] = None,
    ) -> CheckpointSnapshot:
        self._require_bound()
        assert self.manifest is not None
        assert self.checkpoints is not None
        if self.manifest.current_state not in {
            EpisodeState.READY,
            EpisodeState.RUNNING,
            EpisodeState.VERIFYING,
        }:
            raise EpisodeStateError(
                f"cannot checkpoint state {self.manifest.current_state.value}"
            )
        snapshot = self.checkpoints.create(
            label=label,
            agent_session=agent_session,
            runtime_states=runtime_states,
            runtime_config=runtime_config,
            unfinished_process_ids=self.active_process_ids(),
        )
        self.manifest.add_checkpoint(snapshot.checkpoint_id)
        self._save()
        return snapshot

    def reset(
        self,
        *,
        checkpoint_id: Optional[str] = None,
        agent_session: Optional[AgentSession] = None,
        runtime_restorers: Optional[Dict[str, Any]] = None,
        environment_restorer: Optional[Any] = None,
    ) -> Tuple[Optional[AgentSession], Dict[str, Any]]:
        self._require_bound()
        assert self.manifest is not None
        assert self.checkpoints is not None
        target = checkpoint_id or self.manifest.initial_checkpoint_id
        if not target:
            raise EpisodeStateError("episode has no checkpoint to reset to")

        self.manifest.transition(EpisodeState.RESETTING)
        self._save()
        self.terminate_processes()
        try:
            restored = self.checkpoints.restore(
                target,
                agent_session=agent_session,
                runtime_restorers=runtime_restorers,
                environment_restorer=environment_restorer,
            )
            self.manifest.replay_generation += 1
            self.manifest.trajectory_ref = None
            self.manifest.verification_ref = None
            self.manifest.rerun_mode = None
            self.manifest.source_trajectory_ref = None
            self.manifest.source_checkpoint_id = None
            self.manifest.last_error = None
            self.manifest.recovery_state = None
            self.manifest.transition(EpisodeState.READY)
            self._save()
            return restored
        except Exception as exc:
            self.manifest.transition(EpisodeState.FAILED, error=str(exc))
            self._save()
            raise

    def start_rerun(
        self,
        *,
        mode: RerunMode,
        source_trajectory_ref: str,
        checkpoint_id: Optional[str] = None,
        agent_session: Optional[AgentSession] = None,
        runtime_restorers: Optional[Dict[str, Any]] = None,
        environment_restorer: Optional[Any] = None,
    ) -> None:
        mode = RerunMode(mode)
        if mode == RerunMode.CHECKPOINT and not checkpoint_id:
            raise ValueError("checkpoint-rerun requires checkpoint_id")
        if mode == RerunMode.FULL and checkpoint_id is not None:
            raise ValueError("full-rerun always starts from the initial checkpoint")
        self.reset(
            checkpoint_id=checkpoint_id,
            agent_session=agent_session,
            runtime_restorers=runtime_restorers,
            environment_restorer=environment_restorer,
        )
        assert self.manifest is not None
        self.manifest.rerun_mode = mode.value
        self.manifest.source_trajectory_ref = source_trajectory_ref
        self.manifest.source_checkpoint_id = checkpoint_id
        self.manifest.transition(EpisodeState.RUNNING)
        self._save()

    def mark_failed(self, error: str) -> None:
        self._require_bound()
        assert self.manifest is not None
        self.terminate_processes()
        self.manifest.transition(EpisodeState.FAILED, error=error)
        self._save()

    def register_process(self, process: Any) -> None:
        self._processes.append(process)

    def active_process_ids(self) -> List[int]:
        result = []
        for process in self._processes:
            try:
                if process.poll() is None and getattr(process, "pid", None):
                    result.append(int(process.pid))
            except Exception:
                continue
        return result

    def terminate_processes(self) -> None:
        for process in list(self._processes):
            try:
                if process.poll() is not None:
                    continue
                process.terminate()
                try:
                    process.wait(timeout=5)
                except (subprocess.TimeoutExpired, TimeoutError):
                    process.kill()
                    process.wait(timeout=5)
            except Exception:
                continue
        self._processes.clear()

    def destroy(self) -> None:
        self._require_bound()
        assert self.manifest is not None
        assert self.episode_dir is not None
        self.terminate_processes()
        if self.manifest.current_state in {
            EpisodeState.PREPARING,
            EpisodeState.RUNNING,
            EpisodeState.VERIFYING,
        }:
            self.manifest.transition(EpisodeState.CANCELLED)
        self.manifest.transition(EpisodeState.DESTROYED)
        self._save()
        target = self.episode_dir.resolve()
        if target.parent != self.episodes_root or target == self.episodes_root:
            raise EpisodeStateError(f"refusing to destroy unsafe path: {target}")
        shutil.rmtree(target, onerror=_remove_readonly)

    def collect_verification_facts(
        self, task: TaskSpec
    ) -> Dict[str, Dict[str, Any]]:
        self._require_bound()
        assert self.manifest is not None
        assert self.workspace is not None
        task.validate()
        expected_ref = f"{task.task_id}@{task.task_version}"
        if self.manifest.task_ref != expected_ref:
            raise EpisodeStateError("verification task does not match episode")
        if self.manifest.current_state != EpisodeState.RUNNING:
            raise EpisodeStateError(
                "verification facts can only be collected after a running agent"
            )

        command_results = self._run_task_checks(
            task,
            task.test_commands,
            timeout=task.timeout_seconds,
            purpose="final_verification",
        )
        evaluator_results = [
            result for result in command_results
            if "evaluation_prepared" in result or "tests_executed" in result
        ]
        evaluation_prepared = all(
            bool(result.get("evaluation_prepared", False))
            for result in evaluator_results
        ) if evaluator_results else True
        tests_executed = all(
            bool(result.get("tests_executed", False))
            for result in evaluator_results
        ) if evaluator_results else True
        sandbox_failures = [
            result
            for result in command_results
            if result.get("sandbox_error_code")
        ]
        if sandbox_failures:
            raise EpisodeStateError(
                "verification sandbox execution failed: "
                + str(sandbox_failures[0]["sandbox_error_code"])
            )
        passed = sum(
            1 for result in command_results
            if result.get("returncode") == 0
            and not result.get("timed_out", False)
        )
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(self.workspace),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        changed_files = []
        for line in status.stdout.splitlines():
            path = line[3:].strip() if len(line) > 3 else ""
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if path:
                changed_files.append(path.replace("\\", "/"))
        patch = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--binary", "HEAD"],
            cwd=str(self.workspace),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
        return {
            "test_result": {
                "total_tests": len(command_results) if tests_executed else 0,
                "passed_tests": passed if tests_executed else 0,
                "failed_tests": len(command_results) - passed if tests_executed else 0,
                "evaluation_prepared": evaluation_prepared,
                "tests_executed": tests_executed,
                "evaluation_errors": [
                    {
                        "error_type": result.get("evaluation_error_type", "unknown"),
                        "returncode": result.get("returncode"),
                    }
                    for result in evaluator_results
                    if not result.get("evaluation_prepared", False)
                    or not result.get("tests_executed", False)
                ],
                "commands": command_results,
            },
            "diff_result": {
                "changed_files": sorted(set(changed_files)),
                "patch": patch,
                "workspace_hash": workspace_hash(self.workspace),
            },
        }

    def _run_initial_checks(
        self,
        commands: List[str],
        *,
        timeout: float,
        purpose: str = "verification",
    ) -> List[Dict[str, Any]]:
        assert self.workspace is not None
        results = []
        if self.command_runner is not None:
            for command in commands:
                try:
                    completed = self.command_runner.run(
                        command, cwd=str(self.workspace), timeout=timeout
                    )
                    result = {
                        "command": command,
                        "returncode": completed.returncode,
                        "stdout": completed.stdout[-4000:],
                        "stderr": completed.stderr[-4000:],
                        "timed_out": False,
                    }
                    self._merge_evaluator_payload(result)
                    if hasattr(self.command_runner, "result_metadata"):
                        result.update(self.command_runner.result_metadata(completed))
                    results.append(result)
                except subprocess.TimeoutExpired as exc:
                    results.append(
                        {
                            "command": command,
                            "returncode": -1,
                            "stdout": str(exc.stdout or "")[-4000:],
                            "stderr": f"timeout after {timeout}s",
                            "timed_out": True,
                        }
                    )
            return results

        backend, handle = self._start_verification_sandbox(purpose)
        try:
            for command in commands:
                resolved_command = (
                    resolve_task_command(command)
                    if handle.backend_name == "host"
                    else command
                )
                try:
                    execution = backend.exec(
                        handle,
                        ExecRequest(
                            command=resolved_command,
                            cwd=".",
                            timeout_seconds=timeout,
                        ),
                    )
                    result = {
                        "command": command,
                        "returncode": (
                            execution.returncode
                            if execution.returncode is not None
                            else -1
                        ),
                        "stdout": execution.stdout[-4000:],
                        "stderr": execution.stderr[-4000:],
                        "timed_out": execution.timed_out,
                        "backend_name": execution.backend_name,
                        "sandbox_id": execution.sandbox_id,
                        "spec_hash": execution.spec_hash,
                        "output_truncated": execution.output_truncated,
                    }
                    if execution.error_code is not None:
                        result["sandbox_error_code"] = execution.error_code.value
                    if resolved_command != command:
                        result["resolved_command"] = resolved_command
                    self._merge_evaluator_payload(result)
                    results.append(result)
                except SandboxBackendError as exc:
                    results.append({
                        "command": command,
                        "returncode": -1,
                        "stdout": "",
                        "stderr": exc.detail[-4000:],
                        "timed_out": False,
                        "backend_name": handle.backend_name,
                        "sandbox_id": handle.sandbox_id,
                        "spec_hash": handle.spec_hash,
                        "sandbox_error_code": exc.code.value,
                        "retryable": exc.retryable,
                    })
                    break
        finally:
            try:
                try:
                    backend.destroy(handle)
                except Exception as exc:
                    self._record_sandbox_destruction(
                        handle,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    raise
                else:
                    self._record_sandbox_destruction(handle)
            finally:
                self._verification_handle = None
        return results

    @staticmethod
    def _merge_evaluator_payload(result: Dict[str, Any]) -> None:
        """Copy structured evaluator status from the last JSON output line."""
        for line in reversed(str(result.get("stdout", "")).splitlines()):
            try:
                evaluator_payload = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(evaluator_payload, dict):
                continue
            if "evaluation_prepared" in evaluator_payload:
                result["evaluation_prepared"] = bool(
                    evaluator_payload["evaluation_prepared"]
                )
            if "tests_executed" in evaluator_payload:
                result["tests_executed"] = bool(
                    evaluator_payload["tests_executed"]
                )
            if evaluator_payload.get("status") == "evaluation_error":
                result["evaluation_error_type"] = str(
                    evaluator_payload.get("error_type", "unknown")
                )
            break

    def _run_task_checks(
        self,
        task: TaskSpec,
        commands: List[str],
        *,
        timeout: float,
        purpose: str,
    ) -> List[Dict[str, Any]]:
        with self._staged_test_assets(task):
            return self._run_initial_checks(
                commands,
                timeout=timeout,
                purpose=purpose,
            )

    def _start_verification_sandbox(
        self,
        purpose: str,
    ) -> Tuple[SandboxBackend, SandboxHandle]:
        assert self.workspace is not None
        if self._verification_handle is not None:
            raise EpisodeStateError("a verifier sandbox is already active")
        backend = self._verification_backend
        if backend is None:
            if self.sandbox_backend_name == "host":
                backend = HostBackend()
            else:
                from ..docker_backend import DockerBackend

                backend = DockerBackend(cleanup_on_exit=False)
            self._verification_backend = backend

        episode_id = (
            self.manifest.episode_id
            if self.manifest is not None
            else "unbound"
        )
        sandbox_id = (
            f"{self.sandbox_backend_name}-verifier-{episode_id}-"
            f"{uuid.uuid4().hex[:12]}"
        )
        owner_id = f"{episode_id}:verifier"
        if self.sandbox_backend_name == "host":
            spec = SandboxSpec.for_host_workspace(
                str(self.workspace),
                owner_kind="verification",
                owner_id=owner_id,
                sandbox_id=sandbox_id,
            )
        else:
            assert self.sandbox_image is not None
            spec = SandboxSpec.for_docker_workspace(
                str(self.workspace),
                image=self.sandbox_image,
                owner_kind="verification",
                owner_id=owner_id,
                sandbox_id=sandbox_id,
                security_profile=self.sandbox_security_profile,
            )
        handle = backend.prepare(spec)
        try:
            backend.start(handle)
        except Exception:
            try:
                backend.destroy(handle)
            except Exception:
                pass
            raise
        self._verification_handle = handle
        self._record_sandbox_evidence(purpose, spec, handle)
        return backend, handle

    def _record_sandbox_evidence(
        self,
        purpose: str,
        spec: SandboxSpec,
        handle: SandboxHandle,
    ) -> None:
        if self.manifest is None:
            return
        evidence = {
            "purpose": purpose,
            "backend": handle.backend_name,
            "sandbox_id": handle.sandbox_id,
            "spec_hash": handle.spec_hash,
            "runtime_tier": spec.runtime_tier.value,
            "security_profile": spec.security_profile,
            "network_mode": spec.network.mode.value,
            "generation": handle.generation,
            "owner_kind": handle.owner_kind,
            "owner_id": handle.owner_id,
            "destroyed": False,
            "final_state": handle.state.value,
        }
        for key in (
            "docker_server_version",
            "image_identity",
            "image_reference",
        ):
            value = handle.backend_metadata.get(key)
            if value:
                evidence[key] = value
        executions = self.manifest.metadata.setdefault(
            "sandbox_executions",
            [],
        )
        if not isinstance(executions, list):
            raise EpisodeStateError(
                "episode sandbox execution evidence must be a list"
            )
        executions.append(evidence)
        self._save()

    def _record_sandbox_destruction(
        self,
        handle: SandboxHandle,
        *,
        error: str = "",
    ) -> None:
        if self.manifest is None:
            return
        executions = self.manifest.metadata.get("sandbox_executions", [])
        if not isinstance(executions, list):
            raise EpisodeStateError(
                "episode sandbox execution evidence must be a list"
            )
        for evidence in reversed(executions):
            if not isinstance(evidence, dict):
                continue
            if evidence.get("sandbox_id") != handle.sandbox_id:
                continue
            evidence["destroyed"] = not bool(error)
            evidence["final_state"] = handle.state.value
            if error:
                evidence["destroy_error"] = error
            self._save()
            return
        raise EpisodeStateError(
            f"missing sandbox creation evidence for {handle.sandbox_id!r}"
        )

    @contextmanager
    def _staged_test_assets(self, task: TaskSpec):
        assert self.workspace is not None
        if not task.test_assets_ref:
            yield
            return
        source = Path(task.test_assets_ref)
        if not source.is_absolute():
            source = self.project_root / source
        source = source.resolve()
        observed_hash = workspace_hash(source, normalize_exec=True)
        if observed_hash != task.test_assets_hash:
            raise InitialValidationError(
                f"test assets hash mismatch: expected {task.test_assets_hash}, "
                f"got {observed_hash}"
            )
        destination = self.workspace / ".claw_hidden_tests"
        if destination.exists():
            raise InitialValidationError(
                "workspace already contains reserved .claw_hidden_tests"
            )
        shutil.copytree(source, destination)
        try:
            yield
        finally:
            shutil.rmtree(destination, ignore_errors=True)

    def _reset_git(self, commit: str) -> None:
        assert self.workspace is not None
        subprocess.run(
            ["git", "reset", "--hard", commit],
            cwd=str(self.workspace),
            capture_output=True,
            check=True,
            timeout=30,
        )
        subprocess.run(
            ["git", "clean", "-fdx"],
            cwd=str(self.workspace),
            capture_output=True,
            check=True,
            timeout=30,
        )

    def _bind_episode(self, episode_id: str) -> None:
        if (
            not episode_id
            or "/" in episode_id
            or "\\" in episode_id
            or episode_id in {".", ".."}
        ):
            raise ValueError("episode_id must be a simple path-safe identifier")
        self.episode_dir = (self.episodes_root / episode_id).resolve()
        if self.episode_dir.parent != self.episodes_root:
            raise ValueError("episode path escapes episodes_root")
        self.workspace = self.episode_dir / "workspace"

    def _require_bound(self) -> None:
        if not self.manifest or not self.episode_dir or not self.checkpoints:
            raise RuntimeError("prepare() or open() an episode first")

    def _save(self) -> None:
        assert self.manifest is not None
        assert self.episode_dir is not None
        self.manifest.save(self.episode_dir / self.MANIFEST_NAME)
