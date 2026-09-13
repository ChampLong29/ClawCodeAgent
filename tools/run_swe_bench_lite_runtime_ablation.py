"""Run one local Pi Raw / Claw Base / Claw Enhanced SWE-bench Lite ablation."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from claw.agent_types import DEFAULT_MODEL_NAME
from claw.data_pipeline.swe_bench_runtime_comparison import (
    run_swe_bench_lite_runtime_ablation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark-root", type=Path, default=Path("benchmarks/swe_bench_lite")
    )
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
    parser.add_argument(
        "--claw-sandbox-backend",
        choices=("host", "docker"),
        default="host",
        help="Execution backend for the Claw Base and Claw Enhanced arms.",
    )
    parser.add_argument(
        "--claw-sandbox-image",
        help="Digest-pinned image required for Docker Claw arms.",
    )
    parser.add_argument(
        "--claw-sandbox-python",
        help=(
            "Python executable inside the pinned Claw image; the image must "
            "contain Claw and the task's evaluator dependencies."
        ),
    )
    parser.add_argument(
        "--pi-sandbox-attestation",
        help=(
            "Operator evidence for the Pi isolation boundary; required when "
            "macOS Seatbelt is disabled."
        ),
    )
    parser.add_argument(
        "--no-macos-seatbelt",
        action="store_true",
        help=(
            "Disable the required macOS seatbelt only in an independently "
            "isolated environment."
        ),
    )
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
    result = run_swe_bench_lite_runtime_ablation(
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
        enforce_macos_seatbelt=not args.no_macos_seatbelt,
        sandbox_attestation=args.pi_sandbox_attestation,
        claw_sandbox_backend=args.claw_sandbox_backend,
        claw_sandbox_image=args.claw_sandbox_image,
        claw_sandbox_python=args.claw_sandbox_python,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "comparable" else 2


if __name__ == "__main__":
    raise SystemExit(main())
