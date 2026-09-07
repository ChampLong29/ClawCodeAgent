"""Prepare or execute one frozen pilot patch with the official SWE-bench Harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from claw.benchmark.swe_bench_official import (  # noqa: E402
    OfficialSweBenchHarness,
    OfficialSweBenchHarnessConfig,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance-id", required=True)
    patch_source = parser.add_mutually_exclusive_group(required=True)
    patch_source.add_argument("--patch-file", type=Path)
    patch_source.add_argument("--episode-workspace", type=Path)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--benchmark-root", type=Path, default=PROJECT_ROOT / "benchmarks" / "swe_bench_lite")
    parser.add_argument("--harness-python", default=str(PROJECT_ROOT / ".venv-swebench" / "bin" / "python"))
    parser.add_argument("--harness-version", default="5.0.2")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--open-file-limit", type=int, default=4096)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--task-repo", type=Path, default=None,
                        help="Pinned SWE-bench task repository checkout for v5 evaluation")
    args = parser.parse_args()

    harness = OfficialSweBenchHarness(
        OfficialSweBenchHarnessConfig(
            python_executable=args.harness_python,
            expected_version=args.harness_version,
            docker_executable=args.docker,
            max_workers=args.max_workers,
            timeout_seconds=args.timeout,
            open_file_limit=args.open_file_limit,
        )
    )
    model_patch = (
        args.patch_file.read_text(encoding="utf-8")
        if args.patch_file
        else harness.model_patch_from_workspace(args.episode_workspace)
    )
    prepared = harness.prepare(
        benchmark_root=args.benchmark_root,
        instance_id=args.instance_id,
        model_patch=model_patch,
        model_name_or_path=args.model_name,
        output_root=args.output,
        run_id=args.run_id,
        task_repo=args.task_repo,
    )
    if args.prepare_only:
        payload = json.loads(prepared.input_manifest_path.read_text(encoding="utf-8"))
    else:
        payload = harness.run(prepared)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("status") in {"prepared", "completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
