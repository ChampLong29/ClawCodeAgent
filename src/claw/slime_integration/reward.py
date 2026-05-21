"""Composite reward function for Claw Code Agent training.

Combines multiple reward signals into a single scalar for RL training:
1. test_pass_rate — 沙箱中运行测试命令的通过率
2. diff_accuracy — 生成代码与 ground truth 的匹配度
3. compliance_bonus — 是否遵循 lifecycle 阶段约束
4. process_reward — 每个 step 的正向贡献评估

Usage with SLIME:
    --custom-rm-path claw.slime_integration.reward:claw_reward
"""

from __future__ import annotations

import json
import logging
from argparse import Namespace
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reward weights (configurable via args.reward_weights or metadata)
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS = {
    "test_pass": 0.4,
    "diff_accuracy": 0.3,
    "compliance": 0.1,
    "process": 0.2,
}


# ---------------------------------------------------------------------------
# SLIME custom RM entry point (async)
# ---------------------------------------------------------------------------

async def claw_reward(args: Namespace, sample, **kwargs) -> float:
    """SLIME-compatible async reward function.

    This is the `--custom-rm-path` entry point. Called by SLIME's
    rm_hub.async_rm() for each completed sample.

    Args:
        args: SLIME training arguments
        sample: slime.utils.types.Sample with filled response and metadata

    Returns:
        float: Combined reward in [0, 1]
    """
    metadata = sample.metadata or {}
    sandbox_path = metadata.get("sandbox_path", "")

    # If sandbox already cleaned up, use cached trajectory for reward
    trajectory = metadata.get("trajectory", [])
    task_meta = metadata

    reward = compute_episode_reward(
        args=args,
        sample=sample,
        sandbox_path=sandbox_path,
        sandbox_mgr=None,  # Use pre-computed results if available
    )

    return reward


# ---------------------------------------------------------------------------
# Core reward computation (sync, usable from both rollout and RM)
# ---------------------------------------------------------------------------

def compute_episode_reward(
    args: Namespace,
    sample: Any,
    sandbox_path: str = "",
    sandbox_mgr: Any = None,
) -> float:
    """Compute combined reward for a completed agent episode.

    Can be called from:
    - rollout.py: immediately after episode completion (sandbox still alive)
    - reward.py (claw_reward): as SLIME's custom RM (sandbox may be gone)
    """
    metadata = sample.metadata or {}
    weights = getattr(args, "reward_weights", None) or DEFAULT_WEIGHTS

    scores = {
        "test_pass": 0.0,
        "diff_accuracy": 0.0,
        "compliance": 0.0,
        "process": 0.0,
    }

    # 1. Test pass rate
    test_commands = metadata.get("test_commands", [])
    if test_commands and sandbox_path and sandbox_mgr:
        try:
            from ..training.sandbox import SandboxManager
            test_result = sandbox_mgr.execute_tests(sandbox_path, test_commands)
            scores["test_pass"] = (
                test_result.passed_tests / max(test_result.total_tests, 1)
            )
        except Exception as e:
            logger.debug(f"Test execution failed: {e}")
    elif "test_result" in metadata:
        # Use cached test result
        tr = metadata["test_result"]
        scores["test_pass"] = tr.get("passed_tests", 0) / max(tr.get("total_tests", 1), 1)

    # 2. Diff accuracy
    ground_truth = metadata.get("ground_truth_files", {})
    if ground_truth and sandbox_path and sandbox_mgr:
        try:
            diff_result = sandbox_mgr.compute_diff(sandbox_path, ground_truth)
            scores["diff_accuracy"] = (
                diff_result.matches / max(diff_result.total, 1)
            )
        except Exception as e:
            logger.debug(f"Diff computation failed: {e}")
    elif "diff_result" in metadata:
        dr = metadata["diff_result"]
        scores["diff_accuracy"] = dr.get("matches", 0) / max(dr.get("total", 1), 1)

    # 3. Compliance bonus
    scores["compliance"] = _compute_compliance_score(sample, metadata)

    # 4. Process reward (step-level)
    scores["process"] = _compute_process_reward(sample, metadata)

    # Weighted sum
    final = sum(weights.get(k, 0.0) * v for k, v in scores.items())

    # Store breakdown in metadata for analysis
    metadata["reward_breakdown"] = scores
    metadata["reward_weights"] = weights

    return min(1.0, max(0.0, final))


# ---------------------------------------------------------------------------
# Component reward functions
# ---------------------------------------------------------------------------

def _compute_compliance_score(sample: Any, metadata: Dict[str, Any]) -> float:
    """Check if the agent respected lifecycle phase constraints.

    Awards bonus if:
    - No write operations in read-only phases
    - No tool calls to blocked tools
    - Proper use of allowed tools only

    Returns 1.0 for full compliance, 0.0 for violations.
    """
    trajectory = metadata.get("trajectory", [])
    phase = metadata.get("lifecycle_phase", "IMPLEMENTATION")

    # If phase is IMPLEMENTATION, compliance is trivially satisfied
    if phase in ("IMPLEMENTATION", "UNIT_TEST", "INTEGRATION_TEST"):
        return 1.0

    # For read-only phases, check that no write tools were called
    write_tools = {"write_file", "edit_file"}
    violations = 0
    total_calls = 0

    for turn in trajectory:
        tool_calls = turn.get("tool_calls") or []
        for tc in tool_calls:
            total_calls += 1
            tool_name = tc.get("function", {}).get("name", "")
            if tool_name in write_tools:
                violations += 1

    if total_calls == 0:
        return 1.0

    return max(0.0, 1.0 - (violations / total_calls))


def _compute_process_reward(sample: Any, metadata: Dict[str, Any]) -> float:
    """Evaluate step-level process quality.

    Analyzes each agent step to determine if it positively contributed
    to the final outcome. Rewards:
    - Steps that gather relevant information (read correct files)
    - Steps that make meaningful edits
    - Steps that run tests and respond to results

    Penalizes:
    - Redundant/repeated operations
    - Errors that could have been avoided
    - Unnecessary file reads (reading the same file multiple times)

    Returns score in [0, 1].
    """
    trajectory = metadata.get("trajectory", [])
    if not trajectory:
        return 0.5  # Neutral if no trajectory info

    total_turns = len(trajectory)
    if total_turns == 0:
        return 0.5

    positive_signals = 0
    negative_signals = 0

    seen_reads: set = set()
    seen_writes: set = set()

    for turn in trajectory:
        tool_calls = turn.get("tool_calls") or []
        tool_results = turn.get("tool_results") or []

        for i, tc in enumerate(tool_calls):
            tool_name = tc.get("function", {}).get("name", "")
            args_raw = tc.get("function", {}).get("arguments", "{}")
            try:
                tool_args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except (json.JSONDecodeError, TypeError):
                tool_args = {}

            path = tool_args.get("path", "")

            # Positive: meaningful write/edit operations
            if tool_name in ("write_file", "edit_file") and path:
                if path not in seen_writes:
                    positive_signals += 1
                    seen_writes.add(path)
                else:
                    # Rewriting same file might be ok (refinement) — neutral
                    pass

            # Positive: reading a new file (exploration)
            elif tool_name == "read_file" and path:
                if path not in seen_reads:
                    positive_signals += 1
                    seen_reads.add(path)
                else:
                    # Reading same file again — slightly negative
                    negative_signals += 0.5

            # Positive: running commands (bash)
            elif tool_name == "bash":
                positive_signals += 0.5

            # Check tool results for errors
            if i < len(tool_results):
                result = tool_results[i]
                if not result.get("ok", True):
                    negative_signals += 0.5

    total_signals = positive_signals + negative_signals
    if total_signals == 0:
        return 0.5

    # Score = positive proportion
    raw_score = positive_signals / (positive_signals + negative_signals)

    # Bonus for efficiency (fewer turns for same result)
    efficiency_bonus = max(0, 1.0 - (total_turns / 30)) * 0.2

    return min(1.0, raw_score + efficiency_bonus)
