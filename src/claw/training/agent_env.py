"""Gym-style agent environment wrapper for LLM agent training."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..agent_runtime import LocalCodingAgent
from ..agent_types import AgentRunResult, ModelConfig, BudgetConfig, AgentPermissions
from ..query_engine import QueryEngine, QueryEngineConfig
from .tasks import CodingTask
from .sandbox import SandboxManager, TestResult, DiffResult
from .determinism import DeterministicConfig, apply_deterministic_config


@dataclass
class EnvObservation:
    """Observation from the agent environment after a step/reset."""
    session_id: str
    messages: List[Dict[str, Any]]
    sandbox_path: str
    turn_count: int
    stop_reason: str = ""


class AgentEnv:
    """Gym-style environment for agent training.

    Each episode:
    1. reset(task) → creates fresh sandbox, initializes agent
    2. step() → runs full agent rollout
    3. compute_reward() → evaluates result against ground truth
    4. close() → cleans up sandbox

    Supports both episode-level RL (one rollout = one episode) and
    step-level RL (each model turn is one RL step, for future extension).
    """

    def __init__(
        self,
        sandbox_manager: Optional[SandboxManager] = None,
        deterministic: Optional[DeterministicConfig] = None,
        model_name: Optional[str] = None,
    ):
        self.sandbox_manager = sandbox_manager or SandboxManager()
        self.deterministic = deterministic or DeterministicConfig()
        self.model_name = model_name or ""
        self._agent: Optional[LocalCodingAgent] = None
        self._task: Optional[CodingTask] = None
        self._sandbox_path: str = ""
        self._episode_complete: bool = False
        # Cached results to avoid double-computation
        self._cached_test_result: Optional[TestResult] = None
        self._cached_diff_result: Optional[DiffResult] = None

    def reset(self, task: CodingTask) -> EnvObservation:
        """Reset the environment with a new task.

        Creates a fresh sandbox, initializes the agent with deterministic config.
        """
        self._task = task
        self._episode_complete = False

        # Create isolated sandbox
        self._sandbox_path = self.sandbox_manager.create_sandbox(
            task_id=task.id,
            template_dir=task.template_dir,
        )

        # Create agent targeting the sandbox
        model_config = ModelConfig(
            name=self.model_name,
            temperature=self.deterministic.temperature,
        )

        budget = BudgetConfig(max_total_tokens=50000, max_model_calls=100)
        permissions = AgentPermissions(allow_write=True, allow_shell=True)

        self._agent = LocalCodingAgent(
            cwd=self._sandbox_path,
            model_config=model_config,
            budget=budget,
            permissions=permissions.to_dict(),
        )

        # Apply deterministic config
        apply_deterministic_config(self._agent, self.deterministic)

        return EnvObservation(
            session_id=self.deterministic.session_id or "",
            messages=[],
            sandbox_path=self._sandbox_path,
            turn_count=0,
        )

    def step(self) -> Tuple[EnvObservation, float, bool, Dict[str, Any]]:
        """Run a full agent rollout for the current task.

        Returns (observation, reward, done, info).
        In episode-level mode, this runs the full rollout and returns done=True.
        """
        if not self._agent or not self._task:
            raise RuntimeError("Call reset(task) before step()")

        result = self._agent.run(
            prompt=self._task.prompt,
            max_turns=self._task.max_turns,
            stream=False,
        )

        messages = self._agent.session.get_messages() if self._agent.session else []

        observation = EnvObservation(
            session_id=self._agent.session.session_id if self._agent.session else "",
            messages=messages,
            sandbox_path=self._sandbox_path,
            turn_count=self._agent.turns,
            stop_reason=result.stop_reason,
        )

        reward = self.compute_reward(result)
        self._episode_complete = True

        info: Dict[str, Any] = {
            "stop_reason": result.stop_reason,
            "error": result.error,
            "usage": result.usage.to_dict() if result.usage else {},
            "task_id": self._task.id,
        }

        # Include cached results so runner can reuse them
        if self._cached_test_result is not None:
            info["test_result"] = {
                "passed": self._cached_test_result.passed,
                "passed_tests": self._cached_test_result.passed_tests,
                "total_tests": self._cached_test_result.total_tests,
                "output": self._cached_test_result.output,
            }
        if self._cached_diff_result is not None:
            info["diff_result"] = {
                "match": self._cached_diff_result.match,
                "matches": self._cached_diff_result.matches,
                "total": self._cached_diff_result.total,
            }

        return (observation, reward, True, info)

    def compute_reward(self, result: AgentRunResult) -> float:
        """Compute the reward for a completed rollout.

        Reward components:
        - Test pass: 0.0 to 1.0 based on test pass rate
        - Diff accuracy: 0.0 to 1.0 based on ground truth file match
        - Penalty for errors or budget exceeded

        Results are cached so ``run_episode()`` can reuse them without
        re-running tests.
        """
        if not self._task:
            return 0.0

        reward = 0.0
        self._cached_test_result = None
        self._cached_diff_result = None

        # Test results (cached)
        if self._task.test_commands:
            self._cached_test_result = self.sandbox_manager.execute_tests(
                self._sandbox_path,
                self._task.test_commands,
            )
            test_score = (
                self._cached_test_result.passed_tests
                / max(self._cached_test_result.total_tests, 1)
            )
            reward += 0.5 * test_score

        # Diff against ground truth (cached)
        if self._task.ground_truth_files:
            self._cached_diff_result = self.sandbox_manager.compute_diff(
                self._sandbox_path,
                self._task.ground_truth_files,
            )
            diff_score = (
                self._cached_diff_result.matches
                / max(self._cached_diff_result.total, 1)
            )
            reward += 0.5 * diff_score

        # If no tests and no ground truth, reward based on completion
        if not self._task.test_commands and not self._task.ground_truth_files:
            if result.stop_reason == "completed":
                reward = 0.5  # neutral reward for finishing
            else:
                reward = 0.0

        # Penalties
        if result.stop_reason == "error":
            reward = max(0.0, reward - 0.3)
        elif result.stop_reason == "budget_exceeded":
            reward = max(0.0, reward - 0.2)

        return min(1.0, max(0.0, reward))

    def close(self) -> None:
        """Clean up the sandbox and release resources."""
        if self._sandbox_path:
            self.sandbox_manager.cleanup(self._sandbox_path)
        self._agent = None
        self._task = None
        self._sandbox_path = ""
        self._episode_complete = True
