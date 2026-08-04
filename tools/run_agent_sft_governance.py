"""Run the deterministic Agent SFT governance pipeline on Silver data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from claw.data_pipeline import AgentSFTGovernancePipeline, ClawRecordReader


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--silver-records", required=True, type=Path)
    parser.add_argument("--silver-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--quality-threshold", type=float, default=0.70)
    parser.add_argument(
        "--keep-environment-failures",
        action="store_true",
        help="Retain environment failures instead of excluding them.",
    )
    args = parser.parse_args()

    records, manifest = ClawRecordReader().read(
        args.silver_records, args.silver_manifest
    )
    result = AgentSFTGovernancePipeline().run(
        records,
        source_manifest=manifest,
        output_dir=args.output_dir,
        quality_threshold=args.quality_threshold,
        exclude_environment_failures=not args.keep_environment_failures,
    )
    print(
        json.dumps(
            {
                "dataset_id": result.manifest.dataset_id,
                "source_count": result.report["source_record_count"],
                "selected_count": result.report["selected_record_count"],
                "excluded_count": result.report["excluded_record_count"],
                "output": str(args.output_dir.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
