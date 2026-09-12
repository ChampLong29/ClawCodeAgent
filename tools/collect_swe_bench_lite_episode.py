"""Collect one leakage-safe SWE-bench Lite Dev Episode."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from claw.agent_types import DEFAULT_MODEL_NAME
from claw.data_pipeline import collect_swe_bench_lite_dev_episode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", required=True, type=Path)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--generation-commit", required=True)
    parser.add_argument("--api-config-root", type=Path, default=Path.cwd())
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME)
    parser.add_argument(
        "--local-model-path",
        type=Path,
        help="Use a local Transformers snapshot instead of the configured API.",
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument(
        "--thinking-mode",
        choices=("auto", "enabled", "disabled"),
        help=(
            "Per-request thinking mode. DeepSeek Anthropic compatibility "
            "ignores thinking budget_tokens, so use disabled for a bounded "
            "action experiment."
        ),
    )
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--completion-reminder-turns", type=int, default=8)
    parser.add_argument("--completion-critical-turns", type=int, default=3)
    parser.add_argument(
        "--force-final-response-at-critical",
        action="store_true",
        help="At the critical threshold, hide tools and require a final response.",
    )
    parser.add_argument("--implementation-deadline-turns", type=int, default=12)
    parser.add_argument("--implementation-escalation-turns", type=int, default=4)
    parser.add_argument(
        "--force-direct-mutation-after-escalation",
        action="store_true",
        help=(
            "On the escalation request, expose only write_file/edit_file and "
            "require a tool call."
        ),
    )
    parser.add_argument(
        "--implementation-target-read-allowance",
        type=int,
        default=0,
        help=(
            "Set to 1 to allow one read of an allowlisted target file after "
            "escalation; the following tool-bearing response must edit."
        ),
    )
    parser.add_argument(
        "--implementation-constraint-repair-attempts",
        type=int,
        default=0,
        help=(
            "Set to 1 to allow one no-new-information corrective model request "
            "when the model violates edit-only mode after consuming its target read."
        ),
    )
    parser.add_argument(
        "--reject-repeated-readonly-actions",
        action="store_true",
        help=(
            "Reject an identical successful read/search/outline request when no "
            "side-effect-capable action has run since the prior result."
        ),
    )
    parser.add_argument(
        "--repeated-action-repair-attempts",
        type=int,
        choices=(0, 1),
        default=0,
        help=(
            "Allow one no-new-information corrective request after a repeated "
            "read-only action is rejected."
        ),
    )
    parser.add_argument(
        "--no-post-edit-contract-guidance",
        action="store_false",
        dest="post_edit_contract_guidance",
        help="Disable the one-time post-edit compatibility verification notice.",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--max-total-tokens",
        type=int,
        default=250000,
        help="Stop the episode when cumulative input and output tokens reach this limit.",
    )
    parser.add_argument("--allow-path", action="append", required=True)
    parser.add_argument(
        "--container-image",
        help=(
            "Run Agent shell commands in a disposable Docker/Podman workspace. "
            "Without this option, shell is fail-closed while write allowlists are active."
        ),
    )
    parser.add_argument(
        "--container-engine", choices=("auto", "docker", "podman"), default="auto"
    )
    parser.add_argument(
        "--prompt-version", default="swe-bench-lite-dev.deepseek-flash.v1"
    )
    args = parser.parse_args()
    agent_command_runner = None
    if args.container_image:
        from claw.container_runtime import OCIContainerConfig, OCIContainerRunner

        agent_command_runner = OCIContainerRunner(
            OCIContainerConfig(
                image=args.container_image,
                engine=args.container_engine,
                ephemeral_workspace=True,
            )
        )
    model_client = None
    if args.local_model_path:
        from claw.transformers_client import TransformersToolClient

        model_client = TransformersToolClient(
            args.local_model_path,
            model_name=args.model,
            enable_thinking=args.thinking_mode == "enabled",
            default_max_new_tokens=args.max_tokens or 1024,
        )
    evaluator_script = Path(__file__).resolve().with_name(
        "evaluate_swe_bench_lite_candidate.py"
    )
    result = collect_swe_bench_lite_dev_episode(
        benchmark_root=args.benchmark_root,
        instance_id=args.instance_id,
        python_executable=args.python,
        evaluator_script=evaluator_script,
        output_root=args.output_root,
        generation_commit=args.generation_commit,
        api_config_root=args.api_config_root,
        model_ref=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        max_total_tokens=args.max_total_tokens,
        thinking_mode=args.thinking_mode,
        max_turns=args.max_turns,
        completion_reminder_turns=args.completion_reminder_turns,
        completion_critical_turns=args.completion_critical_turns,
        force_final_response_at_critical=args.force_final_response_at_critical,
        implementation_deadline_turns=args.implementation_deadline_turns,
        implementation_escalation_turns=args.implementation_escalation_turns,
        force_direct_mutation_after_escalation=(
            args.force_direct_mutation_after_escalation
        ),
        implementation_target_read_allowance=(
            args.implementation_target_read_allowance
        ),
        implementation_constraint_repair_attempts=(
            args.implementation_constraint_repair_attempts
        ),
        reject_repeated_readonly_actions=(
            args.reject_repeated_readonly_actions
        ),
        repeated_action_repair_attempts=(
            args.repeated_action_repair_attempts
        ),
        post_edit_contract_guidance=args.post_edit_contract_guidance,
        timeout_seconds=args.timeout,
        prompt_version=args.prompt_version,
        allowed_path_patterns=args.allow_path,
        model_client=model_client,
        agent_command_runner=agent_command_runner,
    )
    episode = result.episodes[0]
    print(
        json.dumps(
            {
                "collection_id": result.manifest.collection_id,
                "instance_id": episode.task_id,
                "success": episode.success,
                "test_pass_rate": episode.test_pass_rate,
                "turns": episode.turns,
                "tool_calls": episode.tool_calls,
                "total_tokens": episode.input_tokens + episode.output_tokens,
                "behavior_diagnostics": episode.behavior_diagnostics,
                "manifest": str(result.manifest_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if episode.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
