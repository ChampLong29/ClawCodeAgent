"""Versioned episode manifest and idempotent lifecycle state machine."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


EPISODE_SCHEMA_VERSION = "episode_manifest.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EpisodeState(str, Enum):
    CREATED = "CREATED"
    PREPARING = "PREPARING"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    ARCHIVED = "ARCHIVED"
    RESETTING = "RESETTING"
    DESTROYED = "DESTROYED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class EpisodeStateError(ValueError):
    """Raised for an illegal episode state transition."""


_ALLOWED_TRANSITIONS = {
    EpisodeState.CREATED: {
        EpisodeState.PREPARING,
        EpisodeState.CANCELLED,
        EpisodeState.FAILED,
    },
    EpisodeState.PREPARING: {
        EpisodeState.READY,
        EpisodeState.FAILED,
        EpisodeState.CANCELLED,
        EpisodeState.RECOVERY_REQUIRED,
    },
    EpisodeState.READY: {
        EpisodeState.RUNNING,
        EpisodeState.RESETTING,
        EpisodeState.CANCELLED,
        EpisodeState.FAILED,
        EpisodeState.DESTROYED,
    },
    EpisodeState.RUNNING: {
        EpisodeState.VERIFYING,
        EpisodeState.FAILED,
        EpisodeState.TIMED_OUT,
        EpisodeState.CANCELLED,
        EpisodeState.RECOVERY_REQUIRED,
        EpisodeState.RESETTING,
    },
    EpisodeState.VERIFYING: {
        EpisodeState.ARCHIVED,
        EpisodeState.FAILED,
        EpisodeState.TIMED_OUT,
        EpisodeState.CANCELLED,
        EpisodeState.RECOVERY_REQUIRED,
    },
    EpisodeState.ARCHIVED: {
        EpisodeState.RESETTING,
        EpisodeState.DESTROYED,
    },
    EpisodeState.FAILED: {
        EpisodeState.RESETTING,
        EpisodeState.DESTROYED,
        EpisodeState.RECOVERY_REQUIRED,
    },
    EpisodeState.TIMED_OUT: {
        EpisodeState.RESETTING,
        EpisodeState.DESTROYED,
        EpisodeState.RECOVERY_REQUIRED,
    },
    EpisodeState.CANCELLED: {
        EpisodeState.RESETTING,
        EpisodeState.DESTROYED,
    },
    EpisodeState.RECOVERY_REQUIRED: {
        EpisodeState.RESETTING,
        EpisodeState.FAILED,
        EpisodeState.DESTROYED,
    },
    EpisodeState.RESETTING: {
        EpisodeState.READY,
        EpisodeState.FAILED,
        EpisodeState.RECOVERY_REQUIRED,
        EpisodeState.DESTROYED,
    },
    EpisodeState.DESTROYED: set(),
}


def atomic_write_json(path: Union[str, os.PathLike[str]], value: Any) -> None:
    """Write JSON through a sibling temp file and atomically replace the target."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass
class EpisodeManifest:
    episode_id: str
    task_ref: str
    workspace_path: str
    template_hash: str
    initial_commit: str
    current_state: EpisodeState = EpisodeState.CREATED
    runtime_config_ref: str = ""
    model_config_ref: str = ""
    prompt_version: str = "unknown"
    tool_version: str = "unknown"
    checkpoint_refs: List[str] = field(default_factory=list)
    initial_checkpoint_id: Optional[str] = None
    trajectory_ref: Optional[str] = None
    verification_ref: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    finished_at: Optional[str] = None
    recovery_state: Optional[str] = None
    last_error: Optional[str] = None
    replay_generation: int = 0
    rerun_mode: Optional[str] = None
    source_trajectory_ref: Optional[str] = None
    source_checkpoint_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = EPISODE_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != EPISODE_SCHEMA_VERSION:
            raise EpisodeStateError(
                f"unsupported episode schema: {self.schema_version}"
            )
        for name in (
            "episode_id",
            "task_ref",
            "workspace_path",
            "template_hash",
        ):
            if not str(getattr(self, name)).strip():
                raise EpisodeStateError(f"{name} must not be empty")
        if self.replay_generation < 0:
            raise EpisodeStateError("replay_generation must be non-negative")
        if len(set(self.checkpoint_refs)) != len(self.checkpoint_refs):
            raise EpisodeStateError("checkpoint_refs must be unique")
        if self.initial_checkpoint_id:
            expected = f"checkpoints/{self.initial_checkpoint_id}.json"
            if expected not in self.checkpoint_refs:
                raise EpisodeStateError(
                    "initial_checkpoint_id must reference checkpoint_refs"
                )

    def transition(
        self,
        target: EpisodeState,
        *,
        error: Optional[str] = None,
        recovery_state: Optional[str] = None,
    ) -> bool:
        """Move to *target*. Repeating the current state is an idempotent no-op."""
        target = EpisodeState(target)
        current = EpisodeState(self.current_state)
        if target == current:
            return False
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise EpisodeStateError(
                f"illegal episode transition: {current.value} -> {target.value}"
            )
        self.current_state = target
        self.updated_at = utc_now()
        if error is not None:
            self.last_error = error
        if recovery_state is not None:
            self.recovery_state = recovery_state
        if target in {
            EpisodeState.ARCHIVED,
            EpisodeState.FAILED,
            EpisodeState.TIMED_OUT,
            EpisodeState.CANCELLED,
            EpisodeState.DESTROYED,
        }:
            self.finished_at = self.updated_at
        elif target in {EpisodeState.READY, EpisodeState.RUNNING}:
            self.finished_at = None
        return True

    def add_checkpoint(self, checkpoint_id: str) -> None:
        ref = f"checkpoints/{checkpoint_id}.json"
        if ref not in self.checkpoint_refs:
            self.checkpoint_refs.append(ref)
            self.updated_at = utc_now()

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["current_state"] = EpisodeState(self.current_state).value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EpisodeManifest":
        payload = dict(data)
        payload["current_state"] = EpisodeState(
            payload.get("current_state", EpisodeState.CREATED.value)
        )
        manifest = cls(**payload)
        manifest.validate()
        return manifest

    def save(self, path: Union[str, os.PathLike[str]]) -> None:
        atomic_write_json(path, self.to_dict())

    @classmethod
    def load(cls, path: Union[str, os.PathLike[str]]) -> "EpisodeManifest":
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))
