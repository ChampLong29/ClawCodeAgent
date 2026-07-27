"""Append-only trajectory v2 schemas and migration."""

from .migration import (
    TrajectoryMigrationError,
    TrajectoryMigrator,
    rollout_result_to_trajectory,
)
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
    "Termination",
    "Trajectory",
    "TrajectoryEvent",
    "TrajectoryHeader",
    "TrajectoryMigrationError",
    "TrajectoryMigrator",
    "ReplayComparator",
    "ReplayDiff",
    "TraceReplayEngine",
    "TraceReplayResult",
    "rollout_result_to_trajectory",
]
