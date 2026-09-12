"""Bridge LocalCodingAgent runtime facts into an Episode trajectory."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from ..agent_types import AgentRunResult
from ..experiment.schemas import TaskSpec
from ..trajectory.recorder import TrajectoryRecorder
from .orchestrator import EpisodeOrchestrator
from .state import EpisodeState, EpisodeStateError


_TERMINATION_MAP = {
    "completed": "completed",
    "budget_exceeded": "budget_exceeded",
    "error": "failed",
    "failed": "failed",
    "timeout": "timeout",
    "cancelled": "cancelled",
    "stopped": "cancelled",
}


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8", newline="\n"
        ) as handle:
            json.dump(
                payload, handle, ensure_ascii=False, indent=2, sort_keys=True
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class RuntimeAdapter:
    """Observe an agent without changing its model or tool decisions."""

    def __init__(
        self,
        orchestrator: EpisodeOrchestrator,
        recorder: TrajectoryRecorder,
        *,
        task: Optional[TaskSpec] = None,
    ):
        if orchestrator.manifest is None:
            raise EpisodeStateError("prepare or open an episode first")
        if (
            recorder.trajectory.header.episode_id
            != orchestrator.manifest.episode_id
        ):
            raise ValueError("trajectory and episode IDs do not match")
        if task is not None:
            task.validate()
            expected_ref = f"{task.task_id}@{task.task_version}"
            if orchestrator.manifest.task_ref != expected_ref:
                raise ValueError("task and episode references do not match")
        self.orchestrator = orchestrator
        self.recorder = recorder
        self.task = task
        self._running_agent: Optional[Any] = None
        self._active_phase: Optional[str] = None
        self._started = any(
            event.event_type == "episode_started"
            for event in recorder.trajectory.events
        )

    @classmethod
    def create(
        cls,
        orchestrator: EpisodeOrchestrator,
        *,
        model_version: str,
        runtime_version: str,
        prompt_version: str,
        tool_version: str,
        config_version: str,
        task: Optional[TaskSpec] = None,
        max_inline_bytes: int = 8192,
    ) -> "RuntimeAdapter":
        if orchestrator.manifest is None or orchestrator.episode_dir is None:
            raise EpisodeStateError("prepare or open an episode first")
        manifest = orchestrator.manifest
        recorder = TrajectoryRecorder.create(
            Path(orchestrator.episode_dir) / "trajectory.json",
            trajectory_id=f"trajectory_{manifest.episode_id}",
            episode_id=manifest.episode_id,
            task_ref=manifest.task_ref,
            model_version=model_version,
            runtime_version=runtime_version,
            prompt_version=prompt_version,
            tool_version=tool_version,
            config_version=config_version,
            max_inline_bytes=max_inline_bytes,
        )
        return cls(orchestrator, recorder, task=task)

    def run(
        self,
        agent: Any,
        prompt: str,
        *,
        max_turns: Optional[int] = None,
        stream: bool = False,
    ) -> AgentRunResult:
        previous = getattr(agent, "runtime_observer", None)
        agent.runtime_observer = self
        self._running_agent = agent
        try:
            return agent.run(
                prompt,
                max_turns=max_turns,
                stream=stream,
            )
        finally:
            if getattr(agent, "sandbox_handle", None) is not None:
                try:
                    agent.destroy_sandbox()
                except Exception:
                    pass
            self._running_agent = None
            agent.runtime_observer = previous

    def on_run_start(
        self,
        agent: Any,
        *,
        prompt: str,
        phase_id: str,
    ) -> None:
        manifest = self.orchestrator.manifest
        assert manifest is not None
        if manifest.current_state == EpisodeState.READY:
            self.orchestrator.start_run()
        elif manifest.current_state != EpisodeState.RUNNING:
            raise EpisodeStateError(
                f"cannot run agent from {manifest.current_state.value}"
            )
        if not self._started:
            session = getattr(agent, "session", None)
            self.recorder.record(
                "episode_started",
                phase_id=phase_id,
                payload={
                    "session_id": getattr(session, "session_id", ""),
                    "model": getattr(getattr(agent, "client", None), "model", ""),
                    "permissions": dict(getattr(agent, "permissions", {}) or {}),
                    "prompt_chars": len(prompt),
                    "cwd": str(getattr(agent, "cwd", "")),
                    "sandbox_backend": getattr(
                        agent,
                        "sandbox_backend_name",
                        "external",
                    ),
                    "episode_sandbox_executions": list(
                        manifest.metadata.get("sandbox_executions", [])
                    ),
                },
            )
            self._started = True
        self._switch_phase(phase_id)

    def record(
        self,
        event_type: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        parent_event_id: Optional[str] = None,
        phase_id: str = "runtime",
    ) -> str:
        if not self._started:
            raise RuntimeError("runtime observer has not started")
        self._switch_phase(phase_id)
        event = self.recorder.record(
            event_type,
            phase_id=phase_id,
            payload=payload,
            parent_event_id=parent_event_id,
        )
        return event.event_id

    def on_run_finish(
        self,
        result: AgentRunResult,
        *,
        phase_id: str,
    ) -> None:
        if self.recorder.trajectory.header.termination is not None:
            return
        self._switch_phase(phase_id)
        collection_error = ""
        agent = self._running_agent
        if agent is not None and getattr(agent, "sandbox_handle", None) is not None:
            sandbox_handle = agent.sandbox_handle
            try:
                agent.destroy_sandbox()
                self.recorder.record(
                    "sandbox_lifecycle",
                    phase_id=phase_id,
                    payload={
                        "action": "destroyed_before_verification",
                        "backend_name": sandbox_handle.backend_name,
                        "sandbox_id": sandbox_handle.sandbox_id,
                        "spec_hash": sandbox_handle.spec_hash,
                        "generation": sandbox_handle.generation,
                        "owner_kind": sandbox_handle.owner_kind,
                        "owner_id": sandbox_handle.owner_id,
                        "final_state": sandbox_handle.state.value,
                        "runtime_tier": (
                            agent.sandbox_spec.runtime_tier.value
                            if getattr(agent, "sandbox_spec", None) is not None
                            else ""
                        ),
                        "security_profile": (
                            agent.sandbox_spec.security_profile
                            if getattr(agent, "sandbox_spec", None) is not None
                            else ""
                        ),
                        "network_mode": (
                            agent.sandbox_spec.network.mode.value
                            if getattr(agent, "sandbox_spec", None) is not None
                            else ""
                        ),
                        **{
                            key: value
                            for key in (
                                "docker_server_version",
                                "image_identity",
                                "image_reference",
                            )
                            if (
                                value := sandbox_handle.backend_metadata.get(key)
                            )
                        },
                    },
                )
            except Exception as exc:
                collection_error = f"{type(exc).__name__}: {exc}"
                self.recorder.record(
                    "runtime_error",
                    phase_id=phase_id,
                    payload={
                        "error": collection_error,
                        "stage": "agent_sandbox_cleanup",
                    },
                )
        if result.error:
            self.recorder.record(
                "runtime_error",
                phase_id=phase_id,
                payload={
                    "error": result.error,
                    "stop_reason": result.stop_reason,
                },
            )
        if self.task is not None and not collection_error:
            try:
                facts = self.orchestrator.collect_verification_facts(
                    self.task
                )
                self.recorder.record(
                    "workspace_diff",
                    phase_id=phase_id,
                    payload={"diff_result": facts["diff_result"]},
                )
                self.recorder.record(
                    "test_result",
                    phase_id=phase_id,
                    payload={"test_result": facts["test_result"]},
                )
            except Exception as exc:
                collection_error = f"{type(exc).__name__}: {exc}"
                self.recorder.record(
                    "runtime_error",
                    phase_id=phase_id,
                    payload={
                        "error": collection_error,
                        "stage": "verification_fact_collection",
                    },
                )
        if self._active_phase:
            self.recorder.record(
                "phase_exited",
                phase_id=self._active_phase,
                payload={"phase_id": self._active_phase, "next_phase_id": None},
            )
        reason = (
            "failed"
            if collection_error
            else _TERMINATION_MAP.get(result.stop_reason, "failed")
        )
        detail = collection_error or result.error or result.final_message or ""
        usage = result.usage.to_dict() if result.usage else {}
        self.recorder.terminate(
            reason,
            detail=detail,
            usage=usage,
            phase_id=phase_id,
        )
        self.orchestrator.begin_verification(
            trajectory_ref=str(self.recorder.path)
        )

    def archive_verification(self, report: Any) -> str:
        report.validate()
        trajectory_id = self.recorder.trajectory.header.trajectory_id
        if report.trajectory_ref != trajectory_id:
            raise ValueError(
                "verification report does not reference this trajectory"
            )
        if self.orchestrator.episode_dir is None:
            raise EpisodeStateError("episode directory is not available")
        destination = (
            Path(self.orchestrator.episode_dir) / "verification.json"
        )
        _atomic_write_json(destination, report.to_dict())
        verification_ref = str(destination)
        self.recorder.attach_evaluation(verification_ref)
        self.orchestrator.archive(
            trajectory_ref=str(self.recorder.path),
            verification_ref=verification_ref,
        )
        return verification_ref

    def verify_and_archive(
        self,
        pipeline: Any,
        context: Any,
        *,
        reviewer: Optional[Any] = None,
    ) -> tuple[Any, str]:
        if (
            context.trajectory.header.trajectory_id
            != self.recorder.trajectory.header.trajectory_id
        ):
            raise ValueError("verification context uses another trajectory")
        context.trajectory = self.recorder.trajectory
        if context.artifact_store is None:
            context.artifact_store = self.recorder.artifact_store
        report = pipeline.verify(context, reviewer=reviewer)
        return report, self.archive_verification(report)

    def _switch_phase(self, phase_id: str) -> None:
        phase_id = phase_id or "runtime"
        if phase_id == self._active_phase:
            return
        previous = self._active_phase
        if previous:
            self.recorder.record(
                "phase_exited",
                phase_id=previous,
                payload={
                    "phase_id": previous,
                    "next_phase_id": phase_id,
                },
            )
        self.recorder.record(
            "phase_entered",
            phase_id=phase_id,
            payload={
                "phase_id": phase_id,
                "previous_phase_id": previous,
            },
        )
        self._active_phase = phase_id
