"""Collect one leakage-safe SWE-bench Lite Dev Episode."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from claw.data_pipeline import collect_swe_bench_lite_dev_episode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", required=True, type=Path)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--generation-commit", required=True)
    parser.add_argument("--api-config-root", type=Path, default=Path.cwd())
    parser.add_argument("--model", default="deepseek-v4-flash")
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
        "--no-post-edit-contract-guidance",
        action="store_false",
        dest="post_edit_contract_guidance",
        help="Disable the one-time post-edit compatibility verification notice.",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--allow-path", action="append", required=True)
    parser.add_argument(
        "--prompt-version", default="swe-bench-lite-dev.deepseek-v4-flash.v1"
    )
    args = parser.parse_args()
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
        post_edit_contract_guidance=args.post_edit_contract_guidance,
        timeout_seconds=args.timeout,
        prompt_version=args.prompt_version,
        allowed_path_patterns=args.allow_path,
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
