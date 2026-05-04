"""Batch rollout runner for parallel agent training episodes."""

from __future__ import annotations

import json
import time
import multiprocessing
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .tasks import CodingTask, TaskSuite
from .sandbox import SandboxManager
from .agent_env import AgentEnv, EnvObservation
from .determinism import DeterministicConfig


@dataclass
class RolloutConfig:
    """Configuration for rollout execution."""
    temperature: float = 0.0
    max_turns: int = 50
    timeout_seconds: float = 600.0  # Per-episode timeout
    num_workers: int = 1
    seed: int = 42
    session_prefix: str = "train"  # For session naming: "train/episode_0"
    disabled_runtimes: List[str] = field(default_factory=list)


@dataclass
class RolloutResult:
    """Complete result from a single rollout."""
    task_id: str
    session_id: str
    stop_reason: str
    reward: float
    messages: List[Dict[str, Any]]
    usage: Dict[str, Any]
    error: Optional[str] = None
    execution_time: float = 0.0
    test_result: Optional[Dict[str, Any]] = None
    diff_result: Optional[Dict[str, Any]] = None


class RolloutRunner:
    """Manages batch execution of agent episodes for training.

    Supports:
    - Single episode execution
    - Parallel batch execution via multiprocessing.Pool
    - Curriculum learning (ordered task sequences)
    - Trajectory export to JSONL and HuggingFace datasets formats
    """

    def __init__(
        self,
        config: Optional[RolloutConfig] = None,
        model_name: Optional[str] = None,
    ):
        self.config = config or RolloutConfig()
        self.model_name = model_name or ""

    def run_episode(self, task: CodingTask) -> RolloutResult:
        """Run a single episode for one task.

        Creates a fresh sandbox and agent, runs the task, and returns
        the complete rollout result.
        """
        start = time.time()
        sandbox_mgr = SandboxManager()

        det = DeterministicConfig(
            temperature=self.config.temperature,
            session_id=f"{self.config.session_prefix}/episode_{task.id}",
            disabled_runtimes=self.config.disabled_runtimes,
            seed=self.config.seed,
        )

        env = AgentEnv(
            sandbox_manager=sandbox_mgr,
            deterministic=det,
            model_name=self.model_name,
        )

        try:
            env.reset(task)
            obs, reward, done, info = env.step()

            # Run tests for result capture
            test_result = None
            diff_result = None

            if task.test_commands and sandbox_mgr.get_sandbox(task.id):
                tr = sandbox_mgr.execute_tests(
                    sandbox_mgr.get_sandbox(task.id),
                    task.test_commands,
                )
                test_result = {
                    "passed": tr.passed,
                    "passed_tests": tr.passed_tests,
                    "total_tests": tr.total_tests,
                    "output": tr.output,
                }

            if task.ground_truth_files and sandbox_mgr.get_sandbox(task.id):
                dr = sandbox_mgr.compute_diff(
                    sandbox_mgr.get_sandbox(task.id),
                    task.ground_truth_files,
                )
                diff_result = {
                    "match": dr.match,
                    "matches": dr.matches,
                    "total": dr.total,
                }

            return RolloutResult(
                task_id=task.id,
                session_id=obs.session_id,
                stop_reason=obs.stop_reason,
                reward=reward,
                messages=obs.messages,
                usage=info.get("usage", {}),
                error=info.get("error"),
                execution_time=time.time() - start,
                test_result=test_result,
                diff_result=diff_result,
            )
        except Exception as e:
            return RolloutResult(
                task_id=task.id,
                session_id="",
                stop_reason="error",
                reward=0.0,
                messages=[],
                usage={},
                error=str(e),
                execution_time=time.time() - start,
            )
        finally:
            env.close()

    def run_batch(self, tasks: List[CodingTask]) -> List[RolloutResult]:
        """Run multiple episodes, optionally in parallel.

        Uses multiprocessing.Pool when num_workers > 1.
        """
        if self.config.num_workers <= 1:
            return [self.run_episode(task) for task in tasks]

        # Use multiprocessing for parallel execution
        # Wrap in a module-level function for pickling
        results = []
        with multiprocessing.Pool(processes=self.config.num_workers) as pool:
            futures = []
            for i, task in enumerate(tasks):
                seed = self.config.seed + i
                future = pool.apply_async(
                    _run_episode_worker,
                    (task.to_dict(), self.config.to_dict(), self.model_name, seed),
                )
                futures.append((task.id, future))

            for task_id, future in futures:
                try:
                    result_dict = future.get(timeout=self.config.timeout_seconds)
                    results.append(RolloutResult(**result_dict))
                except multiprocessing.TimeoutError:
                    results.append(RolloutResult(
                        task_id=task_id,
                        session_id="",
                        stop_reason="timeout",
                        reward=0.0,
                        messages=[],
                        usage={},
                        error="Episode timed out",
                    ))
                except Exception as e:
                    results.append(RolloutResult(
                        task_id=task_id,
                        session_id="",
                        stop_reason="error",
                        reward=0.0,
                        messages=[],
                        usage={},
                        error=str(e),
                    ))

        return results

    def run_suite(self, suite: TaskSuite) -> List[RolloutResult]:
        """Run all tasks in a task suite."""
        return self.run_batch(list(suite.tasks))

    def run_curriculum(self, suite: TaskSuite) -> List[RolloutResult]:
        """Run tasks in curriculum learning order (easy → hard)."""
        curriculum = suite.get_curriculum()
        return self.run_batch(curriculum)

    def export_to_jsonl(self, results: List[RolloutResult], output_path: str) -> None:
        """Export rollout trajectories to JSONL format."""
        with open(output_path, "w", encoding="utf-8") as f:
            for r in results:
                entry = {
                    "task_id": r.task_id,
                    "session_id": r.session_id,
                    "stop_reason": r.stop_reason,
                    "reward": r.reward,
                    "usage": r.usage,
                    "error": r.error,
                    "execution_time": r.execution_time,
                    "test_result": r.test_result,
                    "diff_result": r.diff_result,
                    "messages": r.messages,
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def summary(self, results: List[RolloutResult]) -> Dict[str, Any]:
        """Compute aggregate statistics from rollout results."""
        if not results:
            return {"total": 0}

        total = len(results)
        completed = sum(1 for r in results if r.stop_reason == "completed")
        errors = sum(1 for r in results if r.stop_reason == "error")
        budget_exceeded = sum(1 for r in results if r.stop_reason == "budget_exceeded")
        avg_reward = sum(r.reward for r in results) / total if total > 0 else 0.0
        avg_time = sum(r.execution_time for r in results) / total if total > 0 else 0.0
        total_tokens = sum(
            r.usage.get("input_tokens", 0) + r.usage.get("output_tokens", 0)
            for r in results
        )

        return {
            "total": total,
            "completed": completed,
            "errors": errors,
            "budget_exceeded": budget_exceeded,
            "avg_reward": avg_reward,
            "avg_time_seconds": avg_time,
            "total_tokens": total_tokens,
        }


def _run_episode_worker(
    task_dict: Dict[str, Any],
    config_dict: Dict[str, Any],
    model_name: str,
    seed: int,
) -> Dict[str, Any]:
    """Worker function for multiprocessing pool execution.

    Must be a module-level function so it can be pickled.
    """
    task = CodingTask.from_dict(task_dict)
    config = RolloutConfig(**config_dict)
    config.seed = seed

    runner = RolloutRunner(config=config, model_name=model_name)
    result = runner.run_episode(task)
    return {
        "task_id": result.task_id,
        "session_id": result.session_id,
        "stop_reason": result.stop_reason,
        "reward": result.reward,
        "messages": result.messages,
        "usage": result.usage,
        "error": result.error,
        "execution_time": result.execution_time,
        "test_result": result.test_result,
        "diff_result": result.diff_result,
    }
