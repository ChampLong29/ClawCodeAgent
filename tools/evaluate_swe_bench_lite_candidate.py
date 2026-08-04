"""Evaluate a SWE-bench Lite candidate with hidden assets in a temp copy."""

from __future__ import annotations

import argparse
import json

from claw.benchmark import evaluate_swe_bench_lite_candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()
    try:
        result = evaluate_swe_bench_lite_candidate(
            ".",
            args.asset,
            python_executable=args.python,
            timeout_seconds=args.timeout,
        )
        print(json.dumps(result.to_evidence(), ensure_ascii=False, sort_keys=True))
        return 0 if result.passed else 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "evaluation_error",
                    "error_type": type(exc).__name__,
                    "official_swebench_harness": False,
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
