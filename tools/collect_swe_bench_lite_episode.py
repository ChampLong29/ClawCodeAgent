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
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--completion-reminder-turns", type=int, default=8)
    parser.add_argument("--completion-critical-turns", type=int, default=3)
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
        max_turns=args.max_turns,
        completion_reminder_turns=args.completion_reminder_turns,
        completion_critical_turns=args.completion_critical_turns,
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
                "manifest": str(result.manifest_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if episode.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
