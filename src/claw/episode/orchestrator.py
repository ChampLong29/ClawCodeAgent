"""Episode orchestration across prepare, run, verify, archive, reset, and rerun."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from ..agent_session import AgentSession
from ..experiment.schemas import TaskSpec
from .checkpoint import (
    CheckpointManager,
    CheckpointSnapshot,
    initialize_git,
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
    ):
        self.episodes_root = Path(episodes_root).resolve()
        self.episodes_root.mkdir(parents=True, exist_ok=True)
        self.project_root = Path(project_root or os.getcwd()).resolve()
        self.environment_allowlist = tuple(environment_allowlist or ())
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
            shutil.copytree(template, self.workspace, dirs_exist_ok=True)

            actual_template_hash = workspace_hash(self.workspace)
            if verify_template_hash and actual_template_hash != task.template_hash:
                raise InitialValidationError(
                    f"template hash mismatch: expected {task.template_hash}, "
                    f"got {actual_template_hash}"
                )
            self.manifest.metadata["observed_template_hash"] = actual_template_hash
            self.manifest.initial_commit = initialize_git(self.workspace)

            initial_results = self._run_initial_checks(
                task.initial_checks, timeout=task.timeout_seconds
            )
            self.manifest.metadata["initial_check_results"] = initial_results
            if any(result.get("timed_out") for result in initial_results):
                raise InitialValidationError("initial task validation timed out")
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
        if Path(self.manifest.workspace_path).resolve() != self.workspace:
            raise EpisodeStateError("manifest workspace does not match episode path")
        self.checkpoints = CheckpointManager(
            episode_id=episode_id,
            episode_dir=self.episode_dir,
            workspace=self.workspace,
            environment_allowlist=self.environment_allowlist,
        )
        return self.manifest

    def start_run(self) -> None:
        self._require_bound()
        assert self.manifest is not None
        self.manifest.transition(EpisodeState.RUNNING)
        self._save()

    def begin_verification(self) -> None:
        self._require_bound()
        assert self.manifest is not None
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

    def _run_initial_checks(
        self, commands: List[str], *, timeout: float
    ) -> List[Dict[str, Any]]:
        assert self.workspace is not None
        results = []
        for command in commands:
            try:
                completed = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(self.workspace),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                results.append(
                    {
                        "command": command,
                        "returncode": completed.returncode,
                        "stdout": completed.stdout[-4000:],
                        "stderr": completed.stderr[-4000:],
                    }
                )
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
