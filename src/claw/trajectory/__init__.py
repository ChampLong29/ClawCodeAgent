"""Append-only trajectory v2 schemas and migration."""

from .migration import (
    TrajectoryMigrationError,
    TrajectoryMigrator,
    rollout_result_to_trajectory,
)
from .diagnostics import (
    DIAGNOSTICS_SCHEMA_VERSION,
    RolloutBehaviorDiagnostics,
    analyze_rollout_behavior,
)
from .recorder import TrajectoryRecorder
from .replay import ReplayComparator, ReplayDiff, TraceReplayEngine, TraceReplayResult
from .schema import (
    TRAJECTORY_SCHEMA_VERSION,
    Termination,
    Trajectory,
    TrajectoryEvent,
    TrajectoryHeader,
)

__all__ = [
    "TRAJECTORY_SCHEMA_VERSION",
    "DIAGNOSTICS_SCHEMA_VERSION",
    "RolloutBehaviorDiagnostics",
    "Termination",
    "Trajectory",
    "TrajectoryEvent",
    "TrajectoryHeader",
    "TrajectoryMigrationError",
    "TrajectoryRecorder",
    "TrajectoryMigrator",
    "ReplayComparator",
    "ReplayDiff",
    "TraceReplayEngine",
    "TraceReplayResult",
    "rollout_result_to_trajectory",
    "analyze_rollout_behavior",
]
