"""Evaluate a SWE-bench Lite candidate with hidden assets in a temp copy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# The evaluator runs with an Episode workspace as cwd. Resolve Claw relative to
# this versioned script instead of trusting a caller-owned relative PYTHONPATH.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from claw.benchmark import evaluate_swe_bench_lite_candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--allow-path",
        action="append",
        help=(
            "Override the evaluator asset's candidate overlay allowlist. "
            "Intended for immutable supplemental evaluation of legacy Episodes."
        ),
    )
    args = parser.parse_args()
    try:
        result = evaluate_swe_bench_lite_candidate(
            ".",
            args.asset,
            python_executable=args.python,
            timeout_seconds=args.timeout,
            allowed_path_patterns=args.allow_path,
        )
        print(json.dumps(result.to_evidence(), ensure_ascii=False, sort_keys=True))
        return 0 if result.passed else 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "evaluation_error",
                    "error_type": type(exc).__name__,
                    "evaluation_prepared": False,
                    "tests_executed": False,
                    "official_swebench_harness": False,
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
