"""Validate an executable task suite and optionally persist evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from claw.task_suite import TaskSuiteManifest, TaskSuiteValidator


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        default=str(PROJECT_ROOT / "task_suites" / "manifest.json"),
    )
    parser.add_argument("--output")
    args = parser.parse_args()

    manifest = TaskSuiteManifest.load(args.manifest)
    report = TaskSuiteValidator(manifest).validate_all()
    if args.output:
        TaskSuiteValidator.write_report(report, args.output)
    print(
        json.dumps(
            {
                "report_id": report.report_id,
                "suite_id": report.suite_id,
                "task_count": len(report.tasks),
                "passed": report.passed,
                "failed_task_ids": [
                    item.task_id for item in report.tasks if not item.passed
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
