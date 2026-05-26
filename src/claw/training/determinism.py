"""Deterministic configuration and snapshot verification for agent training."""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DeterministicConfig:
    """Configuration for reproducible agent rollouts.

    Apply this config to eliminate sources of non-determinism:
    - Set temperature to 0.0 (greedy decoding)
    - Use a pre-set session_id instead of random UUID
    - Disable context-injecting runtimes (git status, CLAUDE.md, etc.)
    """
    temperature: float = 0.0
    session_id: Optional[str] = None
    disabled_runtimes: List[str] = field(default_factory=list)  # e.g. ["search", "mcp"]
    seed: int = 42

    def to_dict(self) -> Dict[str, Any]:
        return {
            "temperature": self.temperature,
            "session_id": self.session_id,
            "disabled_runtimes": self.disabled_runtimes,
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DeterministicConfig:
        return cls(
            temperature=data.get("temperature", 0.0),
            session_id=data.get("session_id"),
            disabled_runtimes=data.get("disabled_runtimes", []),
            seed=data.get("seed", 42),
        )


def apply_deterministic_config(agent, config: DeterministicConfig) -> None:
    """Apply deterministic configuration to a LocalCodingAgent instance.

    Sets temperature, seeds the RNG, fixes session ID, and disables
    non-deterministic runtimes.
    """
    # Seed Python's RNG for reproducibility
    random.seed(config.seed)

    if agent.model_config:
        agent.model_config.temperature = config.temperature
        # Also seed model-level randomness if the config supports it
        if hasattr(agent.model_config, "seed"):
            agent.model_config.seed = config.seed

    if config.session_id and agent.session:
        agent.session.session_id = config.session_id

    # Disable runtimes that inject non-deterministic context
    if config.disabled_runtimes:
        agent.runtimes = [
            r for r in agent.runtimes
            if type(r).__name__.replace("Runtime", "").lower() not in config.disabled_runtimes
        ]


class SnapshotVerifier:
    """Records and verifies agent rollout trajectories against golden snapshots.

    Used for procedural testing: record a known-good trajectory, then verify
    that future rollouts produce the same sequence of tool calls and results.
    """

    def __init__(self, snapshot_dir: str = ".training_snapshots"):
        self.snapshot_dir = snapshot_dir
        os.makedirs(snapshot_dir, exist_ok=True)

    def record_snapshot(
        self,
        task_id: str,
        trajectory: List[Dict[str, Any]],
        result: Dict[str, Any],
    ) -> str:
        """Record a trajectory snapshot for a task.

        Returns the path to the snapshot file.
        """
        snapshot = {
            "task_id": task_id,
            "trajectory": trajectory,
            "result": result,
        }
        path = os.path.join(self.snapshot_dir, f"{task_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        return path

    def verify_snapshot(
        self,
        task_id: str,
        trajectory: List[Dict[str, Any]],
        result: Dict[str, Any],
        strict: bool = False,
    ) -> Dict[str, Any]:
        """Verify a trajectory against a recorded snapshot.

        In non-strict mode, compares stop_reason, tool_calls count, and final state.
        In strict mode, compares the full trajectory byte-for-byte.

        Returns a dict with "match" (bool) and "differences" (list of str).
        """
        path = os.path.join(self.snapshot_dir, f"{task_id}.json")
        if not os.path.exists(path):
            return {"match": False, "differences": [f"No snapshot found for task '{task_id}'"]}

        with open(path, "r", encoding="utf-8") as f:
            snapshot = json.load(f)

        differences = []
        snap_traj = snapshot.get("trajectory", [])
        snap_result = snapshot.get("result", {})

        if strict:
            # Byte-for-byte comparison
            if json.dumps(trajectory, sort_keys=True) != json.dumps(snap_traj, sort_keys=True):
                differences.append("Trajectory differs from snapshot")
            if json.dumps(result, sort_keys=True) != json.dumps(snap_result, sort_keys=True):
                differences.append("Result differs from snapshot")
        else:
            # Semantic comparison
            if result.get("stop_reason") != snap_result.get("stop_reason"):
                differences.append(
                    f"stop_reason: {result.get('stop_reason')} != {snap_result.get('stop_reason')}"
                )

            # Compare tool call counts
            actual_tools = sum(1 for m in trajectory if m.get("role") == "tool")
            snap_tools = sum(1 for m in snap_traj if m.get("role") == "tool")
            if actual_tools != snap_tools:
                differences.append(f"Tool call count: {actual_tools} != {snap_tools}")

        return {
            "match": len(differences) == 0,
            "differences": differences,
        }
