"""Side-effect-free trace replay and controlled-rerun difference analysis."""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..experiment.schemas import canonical_hash
from .schema import Trajectory


REPLAY_SCHEMA_VERSION = "trace_replay.v1"
REPLAY_DIFF_SCHEMA_VERSION = "replay_diff.v1"


@dataclass(frozen=True)
class ReplayFrame:
    seq: int
    phase_id: str
    event_type: str
    payload: Dict[str, Any]
    parent_seq: Optional[int]
    artifact_hashes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TraceReplayResult:
    trajectory_id: str
    frames: List[ReplayFrame]
    content_hash: str
    schema_version: str = REPLAY_SCHEMA_VERSION

    @property
    def event_count(self) -> int:
        return len(self.frames)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trajectory_id": self.trajectory_id,
            "event_count": self.event_count,
            "frames": [frame.to_dict() for frame in self.frames],
            "content_hash": self.content_hash,
        }


class TraceReplayEngine:
    """Rebuild a timeline solely from recorded facts.

    No model, tool, network, process, or workspace callback is accepted by this
    API, making accidental external execution impossible during trace replay.
    """

    def replay(self, trajectory: Trajectory) -> TraceReplayResult:
        trajectory.validate()
        event_to_seq = {event.event_id: event.seq for event in trajectory.events}
        frames = [
            ReplayFrame(
                seq=event.seq,
                phase_id=event.phase_id,
                event_type=event.event_type,
                payload=copy.deepcopy(event.payload),
                parent_seq=event_to_seq.get(event.parent_event_id),
                artifact_hashes=[ref.sha256 for ref in event.artifact_refs],
            )
            for event in trajectory.events
        ]
        serialized = [frame.to_dict() for frame in frames]
        return TraceReplayResult(
            trajectory_id=trajectory.header.trajectory_id,
            frames=frames,
            content_hash=canonical_hash(serialized),
        )


@dataclass
class ReplayDifference:
    seq: int
    category: str
    expected: Optional[Dict[str, Any]]
    actual: Optional[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReplayDiff:
    source_trajectory_id: str
    rerun_trajectory_id: str
    differences: List[ReplayDifference]
    summary: Dict[str, int]
    equivalent: bool
    schema_version: str = REPLAY_DIFF_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_trajectory_id": self.source_trajectory_id,
            "rerun_trajectory_id": self.rerun_trajectory_id,
            "equivalent": self.equivalent,
            "summary": dict(self.summary),
            "differences": [item.to_dict() for item in self.differences],
        }


class ReplayComparator:
    """Compare recorded facts while ignoring generated IDs and timestamps."""

    def __init__(self):
        self._engine = TraceReplayEngine()

    def compare(self, source: Trajectory, rerun: Trajectory) -> ReplayDiff:
        expected = self._engine.replay(source)
        actual = self._engine.replay(rerun)
        differences: List[ReplayDifference] = []
        length = max(expected.event_count, actual.event_count)
        for index in range(length):
            left = (
                expected.frames[index].to_dict()
                if index < expected.event_count
                else None
            )
            right = (
                actual.frames[index].to_dict()
                if index < actual.event_count
                else None
            )
            if left == right:
                continue
            event_type = str(
                (right or left or {}).get("event_type", "sequence")
            )
            differences.append(
                ReplayDifference(
                    seq=index + 1,
                    category=self._category(event_type, left, right),
                    expected=left,
                    actual=right,
                )
            )

        source_termination = (
            source.header.termination.to_dict()
            if source.header.termination
            else None
        )
        rerun_termination = (
            rerun.header.termination.to_dict()
            if rerun.header.termination
            else None
        )
        if source_termination != rerun_termination:
            differences.append(
                ReplayDifference(
                    seq=length + 1,
                    category="termination",
                    expected=source_termination,
                    actual=rerun_termination,
                )
            )

        counts = Counter(item.category for item in differences)
        return ReplayDiff(
            source_trajectory_id=source.header.trajectory_id,
            rerun_trajectory_id=rerun.header.trajectory_id,
            differences=differences,
            summary=dict(sorted(counts.items())),
            equivalent=not differences,
        )

    @staticmethod
    def _category(
        event_type: str,
        expected: Optional[Dict[str, Any]],
        actual: Optional[Dict[str, Any]],
    ) -> str:
        if expected is None or actual is None:
            return "structure"
        if event_type in {"model_request", "model_response"}:
            return "model"
        if event_type in {"tool_call", "tool_result"}:
            return "tool"
        if event_type in {"workspace_diff", "test_result"}:
            return "workspace"
        if event_type in {
            "phase_entered",
            "phase_exited",
            "permission_decision",
            "checkpoint_created",
            "rollback_completed",
            "runtime_guidance",
            "sandbox_lifecycle",
        }:
            return "process"
        if event_type in {"runtime_stop", "runtime_error", "episode_terminated"}:
            return "termination"
        return "event"


__all__ = [
    "ReplayComparator",
    "ReplayDiff",
    "ReplayDifference",
    "ReplayFrame",
    "TraceReplayEngine",
    "TraceReplayResult",
]
