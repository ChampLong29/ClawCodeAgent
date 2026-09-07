"""Check the pinned official SWE-bench package and Docker SDK connection."""

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
    OfficialSweBenchHarnessError,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--harness-python",
        default=str(PROJECT_ROOT / ".venv-swebench" / "bin" / "python"),
    )
    parser.add_argument("--harness-version", default="5.0.2")
    parser.add_argument("--docker", default="docker")
    args = parser.parse_args()
    harness = OfficialSweBenchHarness(
        OfficialSweBenchHarnessConfig(
            python_executable=args.harness_python,
            expected_version=args.harness_version,
            docker_executable=args.docker,
        )
    )
    try:
        evidence = harness.preflight()
    except OfficialSweBenchHarnessError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "official_swebench_preflight.v1",
                    "status": "blocked",
                    "official_swebench_harness": True,
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
