"""SLIME rollout function for Claw Code Agent.

This module implements the `--rollout-function-path` interface required by SLIME.
It runs multi-turn agent episodes in sandboxed environments, collecting
token-level log-probabilities from SGLang for on-policy RL training.

Usage with SLIME:
    python train.py \\
        --rollout-function-path claw.slime_integration.rollout:generate_rollout \\
        --custom-rm-path claw.slime_integration.reward:claw_reward \\
        --prompt-data tasks.jsonl \\
        ...
"""

from __future__ import annotations

import json
import logging
import os
import time
from argparse import Namespace
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Lazy imports to avoid hard dependency on SLIME when in normal mode
_SLIME_AVAILABLE = False
try:
    from slime.rollout.base_types import RolloutFnEvalOutput, RolloutFnTrainOutput
    from slime.utils.types import Sample
    _SLIME_AVAILABLE = True
except ImportError:
    pass


def generate_rollout(
    args: Namespace,
    rollout_id: int,
    data_source: Any,
    evaluation: bool = False,
):
    """SLIME rollout function entry point.

    This function is called by SLIME's rollout worker. It:
    1. Gets task samples from data_source
    2. For each task, creates a sandboxed agent with SGLang client
    3. Runs the multi-turn agent loop (tool calls + execution)
    4. Collects log-probs from each generation step
    5. Computes rewards
    6. Returns RolloutFnTrainOutput with filled samples

    Args:
        args: SLIME training arguments (contains sglang_router_ip, etc.)
        rollout_id: Iteration counter for deterministic data generation
        data_source: SLIME DataSource providing task samples
        evaluation: If True, return RolloutFnEvalOutput for eval metrics

    Returns:
        RolloutFnTrainOutput (training) or RolloutFnEvalOutput (evaluation)
    """
    if not _SLIME_AVAILABLE:
        raise ImportError(
            "SLIME is not installed. Install it to use on-policy training mode."
        )

    from .sglang_client import SGLangTrainingClient
    from ..training.sandbox import SandboxManager
    from ..lifecycle_runtime import LifecycleRuntime, PHASE_ALLOWED_TOOLS

    # Get samples from data source
    num_samples = getattr(args, "num_rollout", 8)
    sample_groups: List[List[Sample]] = data_source.get_samples(num_samples)

    # Configure SGLang client
    sglang_url = f"http://{args.sglang_router_ip}:{args.sglang_router_port}"
    sglang_client = SGLangTrainingClient(
        sglang_url=sglang_url,
        model=getattr(args, "actor_model_name_or_path", ""),
    )

    sandbox_mgr = SandboxManager()
    completed_groups: List[List[Sample]] = []
    aborted_samples: List[List[Sample]] = []

    for group in sample_groups:
        completed_group = []
        for sample in group:
            try:
                result = _run_agent_episode(
                    args=args,
                    sample=sample,
                    sglang_client=sglang_client,
                    sandbox_mgr=sandbox_mgr,
                    evaluation=evaluation,
                )
                completed_group.append(result)
            except Exception as e:
                logger.warning(f"Episode failed for sample {sample.group_index}: {e}")
                sample.status = Sample.Status.FAILED
                sample.metadata["error"] = str(e)
                completed_group.append(sample)

        completed_groups.append(completed_group)

    # Return aborted samples to data source for retry
    if aborted_samples:
        data_source.add_samples(aborted_samples)

    if evaluation:
        # Compute eval metrics
        metrics = _compute_eval_metrics(completed_groups)
        return RolloutFnEvalOutput(data=metrics)

    return RolloutFnTrainOutput(samples=completed_groups)


def _run_agent_episode(
    args: Namespace,
    sample: Sample,
    sglang_client: Any,
    sandbox_mgr: Any,
    evaluation: bool = False,
) -> Sample:
    """Run a single multi-turn agent episode.

    Creates a sandboxed environment, runs the agent loop with SGLang
    for inference (collecting log-probs), executes tool calls, and
    fills in sample fields.
    """
    from ..agent_runtime import LocalCodingAgent
    from ..agent_types import AgentPermissions, ModelConfig, BudgetConfig
    from ..agent_tools import execute_tool, ToolExecutionContext
    from ..lifecycle_runtime import LifecycleRuntime

    # Extract task info from sample
    task_metadata = sample.metadata or {}
    task_prompt = sample.prompt if isinstance(sample.prompt, str) else ""
    max_turns = task_metadata.get("max_turns", getattr(args, "max_turns", 30))

    # Create sandbox
    sandbox_path = sandbox_mgr.create_sandbox(
        task_id=f"rollout_{sample.group_index}_{sample.index}",
        template_dir=task_metadata.get("template_dir"),
    )

    try:
        # Create agent with SGLang client
        agent = LocalCodingAgent(
            cwd=sandbox_path,
            model_config=ModelConfig(name=sglang_client.model),
            permissions=AgentPermissions(allow_write=True, allow_shell=True).to_dict(),
        )
        # Inject SGLang training client
        agent.client = sglang_client

        # Apply phase constraints if lifecycle phase specified
        phase = task_metadata.get("lifecycle_phase", "IMPLEMENTATION")
        lifecycle_rt = LifecycleRuntime(cwd=sandbox_path)
        lifecycle_rt.apply_phase_constraints(agent, phase)

        # Run multi-turn agent loop collecting log-probs
        all_log_probs: List[float] = []
        full_response_parts: List[str] = []
        trajectory: List[Dict[str, Any]] = []
        turn_count = 0

        from ..agent_context import get_user_context
        from ..agent_prompting import render_system_prompt

        context = get_user_context(sandbox_path, runtimes=agent.runtimes)
        system_prompt = render_system_prompt(runtimes=agent.runtimes, context=context)

        messages = [{"role": "system", "content": system_prompt}]
        messages.append({"role": "user", "content": task_prompt})

        while turn_count < max_turns:
            # Call SGLang (returns log_probs)
            response = sglang_client.complete(
                messages=messages,
                tools=agent._get_toolspec(),
                temperature=getattr(args, "temperature", 0.7),
                max_tokens=getattr(args, "rollout_max_response_len", 4096),
            )

            # Collect log-probs from this generation turn
            turn_log_probs = response.get("_log_probs", [])
            all_log_probs.extend(turn_log_probs)

            content = response.get("content", "")
            tool_calls = response.get("tool_calls")

            if content:
                full_response_parts.append(content)

            # Record turn in trajectory
            turn_record = {
                "turn": turn_count,
                "content": content,
                "tool_calls": tool_calls,
                "log_probs_count": len(turn_log_probs),
            }

            if tool_calls:
                # Execute tool calls
                messages.append(response)  # Add assistant message
                tool_results = []

                for tc in tool_calls:
                    tool_name = tc["function"]["name"]
                    raw_args = tc["function"]["arguments"]
                    if isinstance(raw_args, str):
                        try:
                            tool_args = json.loads(raw_args)
                        except (json.JSONDecodeError, TypeError):
                            tool_args = {}
                    else:
                        tool_args = raw_args

                    # Execute tool in sandbox
                    result = execute_tool(
                        tool_name,
                        tool_args,
                        context=ToolExecutionContext(
                            cwd=sandbox_path,
                            permissions=agent.permissions,
                        ),
                    )

                    result_str = json.dumps(result.result) if result.ok else f"Error: {result.error}"
                    # Truncate long results
                    if len(result_str) > 4000:
                        result_str = result_str[:4000] + "... [truncated]"

                    tool_results.append({
                        "tool_name": tool_name,
                        "ok": result.ok,
                        "result_preview": result_str[:200],
                    })

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result_str,
                    })

                turn_record["tool_results"] = tool_results
                full_response_parts.append(f"[Tool: {', '.join(tc['function']['name'] for tc in tool_calls)}]")
            else:
                # No tool calls — agent is done
                trajectory.append(turn_record)
                break

            trajectory.append(turn_record)
            turn_count += 1

        # Fill sample fields
        sample.response = "\n".join(full_response_parts)
        sample.response_length = len(all_log_probs)  # Token count
        sample.rollout_log_probs = all_log_probs
        sample.status = Sample.Status.COMPLETED if turn_count < max_turns else Sample.Status.TRUNCATED
        sample.metadata["trajectory"] = trajectory
        sample.metadata["turn_count"] = turn_count
        sample.metadata["sandbox_path"] = sandbox_path

        # Compute reward (if not in eval mode, reward computed by SLIME's RM pipeline)
        if not evaluation:
            from .reward import compute_episode_reward
            sample.reward = compute_episode_reward(
                args=args,
                sample=sample,
                sandbox_path=sandbox_path,
                sandbox_mgr=sandbox_mgr,
            )

    finally:
        # Cleanup sandbox
        sandbox_mgr.cleanup(sandbox_path)

    return sample


def _compute_eval_metrics(groups: List[List[Any]]) -> Dict[str, Dict[str, Any]]:
    """Compute evaluation metrics from completed episodes."""
    total = sum(len(g) for g in groups)
    completed = sum(
        1 for g in groups for s in g
        if hasattr(s, 'status') and s.status == Sample.Status.COMPLETED
    )
    avg_reward = 0.0
    reward_count = 0
    for g in groups:
        for s in g:
            if hasattr(s, 'reward') and s.reward is not None:
                r = s.reward if isinstance(s.reward, (int, float)) else 0.0
                avg_reward += r
                reward_count += 1

    return {
        "claw_agent": {
            "total_episodes": total,
            "completed": completed,
            "completion_rate": completed / max(total, 1),
            "avg_reward": avg_reward / max(reward_count, 1),
        }
    }
