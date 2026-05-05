"""Training environment for LLM agent development.

Provides:
- AgentEnv: Gym-style environment wrapper for deterministic agent rollouts
- SandboxManager: Isolated execution sandboxes
- TaskSuite / CodingTask: Task definitions and management
- RolloutRunner: Batch episode execution with parallelism
- DeterministicConfig: Reproducibility controls and snapshot verification
- ReviewerAgent: Independent code quality assessment (评测分离)
- DomainConfig: Domain-specific configuration for multi-domain generalization
- SlimeDataAdapter: SLIME-compatible training data format export
"""

from .agent_env import AgentEnv, EnvObservation
from .sandbox import SandboxManager, TestResult, DiffResult
from .tasks import CodingTask, TaskSuite
from .runner import RolloutRunner, RolloutConfig, RolloutResult
from .determinism import DeterministicConfig, SnapshotVerifier
from .domain_config import DomainConfig, DomainRegistry, default_registry
from .reviewer import ReviewerAgent, ReviewReport, ReviewScore, ReviewIssue
from .slime_adapter import SlimeDataAdapter, SlimeTrainingSample
