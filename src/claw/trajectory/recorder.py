"""Crash-safe trajectory recording with content-addressed large payloads."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..experiment.artifacts import ArtifactRef, ArtifactStore
from .schema import Trajectory, TrajectoryEvent, TrajectoryHeader, utc_now


_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
}


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).lower().replace("-", "_")
    if normalized in _SENSITIVE_KEYS:
        return True
    if any(
        marker in normalized
        for marker in (
            "api_key",
            "apikey",
            "authorization",
            "password",
            "secret",
        )
    ):
        return True
    return normalized == "token" or normalized.endswith("_token")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if _is_sensitive_key(key)
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(
            r"\b(?:sk|ghp|github_pat)-?[A-Za-z0-9_\-]{12,}\b",
            "[REDACTED]",
            value,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r"(?i)((?:api[_-]?key|access[_-]?token|password|secret)"
            r"\s*[:=]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            value,
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return repr(value)


class TrajectoryRecorder:
    """Append events and atomically persist a valid Trajectory after each fact."""

    def __init__(
        self,
        path: Union[str, os.PathLike[str]],
        trajectory: Trajectory,
        *,
        artifact_store: Optional[ArtifactStore] = None,
        max_inline_bytes: int = 8192,
    ):
        if max_inline_bytes <= 0:
            raise ValueError("max_inline_bytes must be positive")
        self.path = Path(path).resolve()
        self.trajectory = trajectory
        self.artifact_store = artifact_store or ArtifactStore(
            self.path.parent / "artifacts"
        )
        self.max_inline_bytes = max_inline_bytes
        self._lock = threading.RLock()

    @classmethod
    def create(
        cls,
        path: Union[str, os.PathLike[str]],
        *,
        trajectory_id: str,
        episode_id: str,
        task_ref: str,
        model_version: str,
        runtime_version: str,
        prompt_version: str,
        tool_version: str,
        config_version: str,
        artifact_store: Optional[ArtifactStore] = None,
        max_inline_bytes: int = 8192,
    ) -> "TrajectoryRecorder":
        destination = Path(path).resolve()
        if destination.exists():
            raise FileExistsError(f"trajectory already exists: {destination}")
        recorder = cls(
            destination,
            Trajectory(
                header=TrajectoryHeader(
                    trajectory_id=trajectory_id,
                    episode_id=episode_id,
                    task_ref=task_ref,
                    model_version=model_version,
                    runtime_version=runtime_version,
                    prompt_version=prompt_version,
                    tool_version=tool_version,
                    config_version=config_version,
                    artifact_base=ArtifactStore.URI_PREFIX,
                    started_at=utc_now(),
                )
            ),
            artifact_store=artifact_store,
            max_inline_bytes=max_inline_bytes,
        )
        recorder._persist()
        return recorder

    @classmethod
    def open(
        cls,
        path: Union[str, os.PathLike[str]],
        *,
        artifact_store: Optional[ArtifactStore] = None,
        max_inline_bytes: int = 8192,
    ) -> "TrajectoryRecorder":
        source = Path(path).resolve()
        trajectory = Trajectory.from_dict(
            json.loads(source.read_text(encoding="utf-8"))
        )
        return cls(
            source,
            trajectory,
            artifact_store=artifact_store,
            max_inline_bytes=max_inline_bytes,
        )

    def record(
        self,
        event_type: str,
        *,
        phase_id: str = "runtime",
        payload: Optional[Dict[str, Any]] = None,
        parent_event_id: Optional[str] = None,
        artifact_refs: Optional[List[ArtifactRef]] = None,
    ) -> TrajectoryEvent:
        with self._lock:
            inline_payload, stored_refs = self._materialize_payload(payload or {})
            event = self.trajectory.append(
                event_type,
                phase_id=phase_id,
                payload=inline_payload,
                parent_event_id=parent_event_id,
                artifact_refs=[
                    *(artifact_refs or []),
                    *stored_refs,
                ],
            )
            self._persist()
            return event

    def terminate(
        self,
        reason: str,
        *,
        detail: str = "",
        usage: Optional[Dict[str, Any]] = None,
        cost: Optional[Dict[str, Any]] = None,
        phase_id: str = "runtime",
    ) -> TrajectoryEvent:
        with self._lock:
            if self.trajectory.header.termination:
                termination = self.trajectory.header.termination
                if termination.reason != reason or termination.detail != detail:
                    raise ValueError(
                        "terminated trajectory cannot receive another conclusion"
                    )
                return self.trajectory.events[-1]
            event = self.trajectory.terminate(
                reason,
                detail=detail,
                usage=_redact(usage or {}),
                cost=_redact(cost or {}),
            )
            event.phase_id = phase_id
            self._persist()
            return event

    def attach_evaluation(self, evaluation_ref: str) -> None:
        if not evaluation_ref.strip():
            raise ValueError("evaluation_ref must not be empty")
        with self._lock:
            if evaluation_ref not in self.trajectory.evaluation_refs:
                self.trajectory.evaluation_refs.append(evaluation_ref)
                self._persist()

    def resolve_payload(
        self, event: Union[TrajectoryEvent, str]
    ) -> Dict[str, Any]:
        selected = event
        if isinstance(event, str):
            selected = next(
                item
                for item in self.trajectory.events
                if item.event_id == event
            )
        assert isinstance(selected, TrajectoryEvent)
        if selected.payload.get("storage") != "artifact":
            return dict(selected.payload)
        if not selected.artifact_refs:
            raise ValueError("artifact-backed event has no artifact reference")
        value = self.artifact_store.get_json(selected.artifact_refs[-1])
        if not isinstance(value, dict):
            raise ValueError("trajectory event artifact must contain an object")
        return value

    def _materialize_payload(
        self, payload: Dict[str, Any]
    ) -> tuple[Dict[str, Any], List[ArtifactRef]]:
        redacted = _redact(payload)
        encoded = json.dumps(
            redacted,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) <= self.max_inline_bytes:
            return redacted, []
        ref = self.artifact_store.put_bytes(
            encoded,
            media_type="application/json",
            encoding="utf-8",
        )
        return (
            {
                "storage": "artifact",
                "artifact_ref": ref.to_dict(),
                "artifact_uri": ref.uri,
                "sha256": ref.sha256,
                "size_bytes": ref.size_bytes,
                "keys": sorted(redacted),
            },
            [ref],
        )

    def _persist(self) -> None:
        self.trajectory.validate()
        _atomic_write_text(
            self.path,
            json.dumps(
                self.trajectory.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
