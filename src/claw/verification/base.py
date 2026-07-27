"""Shared verification context, policy, and verifier protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from ..experiment.artifacts import ArtifactStore
from ..experiment.schemas import TaskSpec, VerificationSignal
from ..trajectory.schema import Trajectory, TrajectoryEvent


@dataclass
class VerificationPolicy:
    version: str = "verifier-policy.v1"
    required_signals: List[str] = field(
        default_factory=lambda: ["test_pass_rate"]
    )
    signal_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "test_pass_rate": 0.35,
            "build": 0.10,
            "static_check": 0.10,
            "diff_scope": 0.15,
            "process_permission": 0.10,
            "format_schema": 0.05,
            "independent_reviewer": 0.15,
        }
    )
    verifier_filtered_threshold: float = 0.80
    reviewer_threshold: float = 0.70

    def validate(self) -> None:
        if not self.version:
            raise ValueError("verification policy version must not be empty")
        if any(weight < 0 for weight in self.signal_weights.values()):
            raise ValueError("verification signal weights must be non-negative")
        if not 0.0 <= self.verifier_filtered_threshold <= 1.0:
            raise ValueError("verifier_filtered_threshold must be within [0, 1]")
        if not 0.0 <= self.reviewer_threshold <= 1.0:
            raise ValueError("reviewer_threshold must be within [0, 1]")


@dataclass
class VerificationContext:
    trajectory: Trajectory
    task: Optional[TaskSpec] = None
    policy: VerificationPolicy = field(default_factory=VerificationPolicy)
    artifact_store: Optional[ArtifactStore] = None
    allowed_path_patterns: List[str] = field(default_factory=list)
    facts: Dict[str, Any] = field(default_factory=dict)

    def resolve_payload(self, event: TrajectoryEvent) -> Dict[str, Any]:
        payload = dict(event.payload)
        if "artifact_ref" not in payload:
            return payload
        if self.artifact_store is None:
            return payload
        resolved = self.artifact_store.get_json(payload["artifact_ref"])
        if not isinstance(resolved, dict):
            return payload
        merged = dict(payload)
        merged.update(resolved)
        return merged

    def latest_event(self, event_type: str) -> Optional[TrajectoryEvent]:
        for event in reversed(self.trajectory.events):
            if event.event_type == event_type:
                return event
        return None

    def is_required(self, signal_name: str) -> bool:
        return signal_name in self.policy.required_signals


class SignalVerifier(Protocol):
    name: str

    def evaluate(self, context: VerificationContext) -> VerificationSignal:
        ...
