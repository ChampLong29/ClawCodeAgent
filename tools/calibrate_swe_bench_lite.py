"""Run a non-official local baseline/reference calibration for one Dev pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from claw.benchmark import LocalSweBenchLiteCalibrationRunner, SweBenchLiteDevAdapter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", required=True, type=Path)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace = output_dir / "workspace"
    adapter = SweBenchLiteDevAdapter(args.benchmark_root)
    tasks = {
        task.instance_id: task
        for task in adapter.load_agent_tasks(instance_id=args.instance_id)
    }
    bundle = adapter.load_evaluation_bundle(args.instance_id)
    result = LocalSweBenchLiteCalibrationRunner().calibrate_reference(
        tasks[args.instance_id],
        bundle,
        python_executable=args.python,
        workspace=workspace,
        timeout_seconds=args.timeout,
    )
    evidence = result.to_evidence()
    result_path = output_dir / "local-calibration-result.json"
    result_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "instance_id": result.instance_id,
                "result": str(result_path),
            },
            sort_keys=True,
        )
    )
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
