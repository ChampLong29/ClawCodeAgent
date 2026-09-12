"""Run one controlled local SWE-bench Lite Dev comparison for Claw and Pi."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from claw.agent_types import DEFAULT_MODEL_NAME
from claw.data_pipeline.swe_bench_runtime_comparison import (
    run_swe_bench_lite_runtime_comparison,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", type=Path, default=Path("benchmarks/swe_bench_lite"))
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--api-config-root", type=Path, default=Path.cwd())
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--model-backend-version", default="v4-flash-9_10")
    parser.add_argument(
        "--pi-executable",
        type=Path,
        default=Path(".port_sessions/pi-runtime/node_modules/.bin/pi"),
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--max-turns", type=int, default=24)
    parser.add_argument("--max-total-tokens", type=int, default=250000)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--allow-path", action="append", required=True)
    parser.add_argument("--generation-commit")
    args = parser.parse_args()
    generation_commit = args.generation_commit
    if not generation_commit:
        generation_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=args.api_config_root,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
    result = run_swe_bench_lite_runtime_comparison(
        benchmark_root=args.benchmark_root,
        instance_id=args.instance_id,
        python_executable=args.python,
        evaluator_script=Path(__file__).resolve().with_name(
            "evaluate_swe_bench_lite_candidate.py"
        ),
        output_root=args.output_root,
        generation_commit=generation_commit,
        api_config_root=args.api_config_root,
        model_ref=args.model,
        model_backend_version=args.model_backend_version,
        pi_executable=args.pi_executable,
        allowed_path_patterns=args.allow_path,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        max_turns=args.max_turns,
        max_total_tokens=args.max_total_tokens,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "comparable" else 2


if __name__ == "__main__":
    raise SystemExit(main())
