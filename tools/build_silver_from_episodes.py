"""Build deterministic Silver records from archived Claw Episode bundles."""

from __future__ import annotations

import argparse
from pathlib import Path

from claw.data_pipeline import (
    SilverDatasetBuilder,
    load_episode_batch,
    load_episode_dataset_record,
)
from claw.experiment.schemas import TaskSpec
from claw.task_suite import TaskSuiteManifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize verified train/dev Episodes as Silver records."
    )
    task_source = parser.add_mutually_exclusive_group(required=True)
    task_source.add_argument("--task-suite", type=Path)
    task_source.add_argument(
        "--task-spec",
        type=Path,
        help="One materialized TaskSpec shared by all selected Episodes.",
    )
    parser.add_argument(
        "--episode-dir",
        required=True,
        action="append",
        type=Path,
        help="Archived Episode directory; repeat for a batch.",
    )
    parser.add_argument("--generation-commit", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    if args.task_suite is not None:
        suite = TaskSuiteManifest.load(args.task_suite)
        records = load_episode_batch(args.episode_dir, suite)
    else:
        import json

        task = TaskSpec.from_dict(
            json.loads(args.task_spec.read_text(encoding="utf-8"))
        )
        records = [
            load_episode_dataset_record(episode_dir, task)
            for episode_dir in sorted(args.episode_dir, key=lambda item: str(item))
        ]
    result = SilverDatasetBuilder(
        generation_commit=args.generation_commit
    ).build(records, output_dir=args.output_dir)
    print(
        f"dataset_id={result.manifest.dataset_id} "
        f"records={result.manifest.record_count} "
        f"output={args.output_dir.resolve()}"
    )


if __name__ == "__main__":
    main()
