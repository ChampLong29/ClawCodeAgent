"""Run one local Pi Raw / Claw Base / Claw Enhanced SWE-bench Lite ablation."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Dict

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
    parser.add_argument(
        "--python",
        type=Path,
        help="Host task Python; required only when Claw uses the host backend.",
    )
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--api-config-root", type=Path, default=Path.cwd())
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--model-backend-version", default="v4-flash-9_10")
    parser.add_argument("--pi-runtime-version", default="pi@0.85.1")
    parser.add_argument("--pi-tool-version", default="pi-builtins@0.85.1")
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
    parser.add_argument("--allow-path", action="append")
    parser.add_argument(
        "--runtime-environments",
        type=Path,
        default=(
            Path(__file__).resolve().parents[1]
            / "configs/integrations/swe-bench-lite-runtime-environments-v1.json"
        ),
        help=(
            "Versioned task-to-image manifest. Explicit image, interpreter, and "
            "allow-path flags override its values."
        ),
    )
    parser.add_argument("--generation-commit")
    parser.add_argument(
        "--claw-sandbox-backend",
        choices=("host", "docker"),
        default="host",
        help="Execution backend for the Claw Base and Claw Enhanced arms.",
    )
    parser.add_argument(
        "--pi-docker-image",
        help="Digest-pinned image for running the complete Pi RPC process.",
    )
    parser.add_argument(
        "--pi-container-executable",
        default="/opt/pi/node_modules/.bin/pi",
        help="Absolute Pi executable path inside --pi-docker-image.",
    )
    parser.add_argument(
        "--claw-sandbox-image",
        help="Digest-pinned image required for Docker Claw arms.",
    )
    parser.add_argument(
        "--claw-sandbox-python",
        help=(
            "Task-test Python executable inside the pinned Claw image."
        ),
    )
    parser.add_argument(
        "--claw-sandbox-evaluator-python",
        help=(
            "Optional Python executable used to run the staged Claw evaluator; "
            "defaults to --claw-sandbox-python."
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
    if not args.runtime_environments.is_file():
        parser.error(
            f"runtime environment manifest not found: {args.runtime_environments}"
        )
    environment_manifest: Dict[str, Any] = json.loads(
        args.runtime_environments.read_text(encoding="utf-8")
    )
    if environment_manifest.get("schema_version") != (
        "swe_bench_lite_runtime_environments.v1"
    ):
        parser.error("unsupported --runtime-environments schema_version")
    task_environment = environment_manifest.get("tasks", {}).get(
        args.instance_id, {}
    )
    if not isinstance(task_environment, dict):
        parser.error("task runtime environment must be a JSON object")
    allow_paths = args.allow_path or task_environment.get("allow_paths") or []
    if not allow_paths:
        parser.error(
            "--allow-path is required when the task is absent from the runtime manifest"
        )
    if args.claw_sandbox_backend == "docker":
        args.claw_sandbox_image = (
            args.claw_sandbox_image or task_environment.get("image")
        )
        args.claw_sandbox_python = (
            args.claw_sandbox_python or task_environment.get("task_python")
        )
        args.claw_sandbox_evaluator_python = (
            args.claw_sandbox_evaluator_python
            or task_environment.get("evaluator_python")
        )
    elif args.python is None:
        parser.error("--python is required with --claw-sandbox-backend host")
    pi_environment = environment_manifest.get("pi", {})
    if not isinstance(pi_environment, dict):
        parser.error("Pi runtime environment must be a JSON object")
    args.pi_docker_image = args.pi_docker_image or pi_environment.get("image")
    if args.pi_container_executable == "/opt/pi/node_modules/.bin/pi":
        args.pi_container_executable = pi_environment.get(
            "executable", args.pi_container_executable
        )
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
        pi_runtime_version=args.pi_runtime_version,
        pi_tool_version=args.pi_tool_version,
        allowed_path_patterns=allow_paths,
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
        claw_sandbox_evaluator_python=args.claw_sandbox_evaluator_python,
        pi_docker_image=args.pi_docker_image,
        pi_container_executable=args.pi_container_executable,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "comparable" else 2


if __name__ == "__main__":
    raise SystemExit(main())
