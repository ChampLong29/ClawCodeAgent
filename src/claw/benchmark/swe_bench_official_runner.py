"""CLI bridge for SWE-bench 5.x, including local task-repository support."""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset_name", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--instance_ids", nargs="+", required=True)
    parser.add_argument("--predictions_path", required=True)
    parser.add_argument("--max_workers", type=int, required=True)
    parser.add_argument("--run_id", required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--open_file_limit", type=int, required=True)
    parser.add_argument("--report_dir", required=True)
    parser.add_argument("--rewrite_reports", required=True)
    parser.add_argument("--task_repo", default=None)
    args = parser.parse_args()

    from swebench.harness.run_evaluation import main as official_main

    official_main(
        dataset_name=args.dataset_name,
        split=args.split,
        instance_ids=args.instance_ids,
        predictions_path=args.predictions_path,
        max_workers=args.max_workers,
        run_id=args.run_id,
        timeout=args.timeout,
        open_file_limit=args.open_file_limit,
        report_dir=args.report_dir,
        rewrite_reports=args.rewrite_reports.lower() == "true",
        modal=False,
        task_repo=args.task_repo,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
