"""Training environment for LLM agent development.

Provides:
- AgentEnv: Gym-style environment wrapper for deterministic agent rollouts
- SandboxManager: Isolated execution sandboxes
- TaskSuite / CodingTask: Task definitions and management
- RolloutRunner: Batch episode execution with parallelism
- DeterministicConfig: Reproducibility controls and snapshot verification
"""

from .agent_env import AgentEnv, EnvObservation
from .sandbox import SandboxManager, TestResult, DiffResult
from .tasks import CodingTask, TaskSuite
from .runner import RolloutRunner, RolloutConfig, RolloutResult
from .determinism import DeterministicConfig, SnapshotVerifier
