"""Append-only agent trajectory v2 data model."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..experiment.artifacts import ArtifactRef
from ..experiment.schemas import SchemaValidationError


TRAJECTORY_SCHEMA_VERSION = "agent_trajectory.v2"

EVENT_TYPES = {
    "episode_started",
    "phase_entered",
    "phase_exited",
    "model_request",
    "model_response",
    "tool_call",
    "tool_result",
    "permission_decision",
    "checkpoint_created",
    "rollback_completed",
    "workspace_diff",
    "test_result",
    "runtime_guidance",
    "sandbox_lifecycle",
    "runtime_stop",
    "runtime_error",
    "episode_terminated",
}

TERMINATION_REASONS = {
    "completed",
    "failed",
    "timeout",
    "budget_exceeded",
    "cancelled",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:20]}"


@dataclass
class Termination:
    reason: str
    detail: str = ""
    final_seq: int = 0
    usage: Dict[str, Any] = field(default_factory=dict)
    cost: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.reason not in TERMINATION_REASONS:
            raise SchemaValidationError(
                f"termination reason must be one of {sorted(TERMINATION_REASONS)}"
            )
        if self.final_seq < 0:
            raise SchemaValidationError("termination.final_seq must be non-negative")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Termination":
        termination = cls(**data)
        termination.validate()
        return termination


@dataclass
class TrajectoryEvent:
    event_id: str
    seq: int
    timestamp: str
    phase_id: str
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    parent_event_id: Optional[str] = None
    artifact_refs: List[ArtifactRef] = field(default_factory=list)

    def validate(self) -> None:
        if not self.event_id:
            raise SchemaValidationError("event_id must not be empty")
        if self.seq <= 0:
            raise SchemaValidationError("event seq must be positive")
        if self.event_type not in EVENT_TYPES:
            raise SchemaValidationError(f"unsupported event_type: {self.event_type}")
        if not isinstance(self.payload, dict):
            raise SchemaValidationError("event payload must be an object")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["artifact_refs"] = [ref.to_dict() for ref in self.artifact_refs]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrajectoryEvent":
        payload = dict(data)
        payload["artifact_refs"] = [
            ArtifactRef.from_dict(item) for item in payload.get("artifact_refs", [])
        ]
        event = cls(**payload)
        event.validate()
        return event


@dataclass
class TrajectoryHeader:
    trajectory_id: str
    episode_id: str
    task_ref: str
    model_version: str
    runtime_version: str
    prompt_version: str
    tool_version: str
    config_version: str
    artifact_base: str
    started_at: str
    finished_at: Optional[str] = None
    termination: Optional[Termination] = None
    schema_version: str = TRAJECTORY_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != TRAJECTORY_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"unsupported trajectory schema: {self.schema_version}"
            )
        for name in (
            "trajectory_id",
            "episode_id",
            "task_ref",
            "model_version",
            "runtime_version",
            "prompt_version",
            "tool_version",
            "config_version",
        ):
            if not getattr(self, name):
                raise SchemaValidationError(f"{name} must not be empty")
        if self.termination:
            self.termination.validate()

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        data = asdict(self)
        if self.termination:
            data["termination"] = self.termination.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrajectoryHeader":
        payload = dict(data)
        if payload.get("termination"):
            payload["termination"] = Termination.from_dict(payload["termination"])
        header = cls(**payload)
        header.validate()
        return header


@dataclass
class Trajectory:
    header: TrajectoryHeader
    events: List[TrajectoryEvent] = field(default_factory=list)
    evaluation_refs: List[str] = field(default_factory=list)

    def append(
        self,
        event_type: str,
        *,
        phase_id: str = "runtime",
        payload: Optional[Dict[str, Any]] = None,
        parent_event_id: Optional[str] = None,
        artifact_refs: Optional[List[ArtifactRef]] = None,
        timestamp: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> TrajectoryEvent:
        if self.header.termination is not None:
            raise SchemaValidationError("cannot append to a terminated trajectory")
        seq = len(self.events) + 1
        event_payload = payload or {}
        event = TrajectoryEvent(
            event_id=event_id
            or stable_id(
                "evt",
                {
                    "trajectory_id": self.header.trajectory_id,
                    "seq": seq,
                    "event_type": event_type,
                    "payload": event_payload,
                },
            ),
            seq=seq,
            timestamp=timestamp or utc_now(),
            phase_id=phase_id,
            event_type=event_type,
            payload=event_payload,
            parent_event_id=parent_event_id,
            artifact_refs=artifact_refs or [],
        )
        event.validate()
        if parent_event_id and parent_event_id not in {
            existing.event_id for existing in self.events
        }:
            raise SchemaValidationError(
                f"parent_event_id does not reference an earlier event: {parent_event_id}"
            )
        self.events.append(event)
        return event

    def terminate(
        self,
        reason: str,
        *,
        detail: str = "",
        usage: Optional[Dict[str, Any]] = None,
        cost: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> TrajectoryEvent:
        event = self.append(
            "episode_terminated",
            payload={"reason": reason, "detail": detail},
            timestamp=timestamp,
        )
        self.header.finished_at = event.timestamp
        self.header.termination = Termination(
            reason=reason,
            detail=detail,
            final_seq=event.seq,
            usage=usage or {},
            cost=cost or {},
        )
        return event

    def validate(self) -> None:
        self.header.validate()
        seen = set()
        for expected_seq, event in enumerate(self.events, start=1):
            event.validate()
            if event.seq != expected_seq:
                raise SchemaValidationError(
                    f"event seq must be contiguous; expected {expected_seq}, "
                    f"got {event.seq}"
                )
            if event.event_id in seen:
                raise SchemaValidationError(f"duplicate event_id: {event.event_id}")
            if event.parent_event_id and event.parent_event_id not in seen:
                raise SchemaValidationError(
                    f"event parent must precede child: {event.parent_event_id}"
                )
            seen.add(event.event_id)
        termination = self.header.termination
        if termination:
            if not self.events or self.events[-1].event_type != "episode_terminated":
                raise SchemaValidationError(
                    "terminated trajectory must end with episode_terminated"
                )
            if termination.final_seq != len(self.events):
                raise SchemaValidationError(
                    "termination.final_seq must match the last event seq"
                )

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "header": self.header.to_dict(),
            "events": [event.to_dict() for event in self.events],
            "evaluation_refs": list(self.evaluation_refs),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Trajectory":
        trajectory = cls(
            header=TrajectoryHeader.from_dict(data["header"]),
            events=[
                TrajectoryEvent.from_dict(item) for item in data.get("events", [])
            ],
            evaluation_refs=list(data.get("evaluation_refs", [])),
        )
        trajectory.validate()
        return trajectory
