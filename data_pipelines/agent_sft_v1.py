"""DataFlow-native deterministic pipeline for Claw Agent Silver records."""

from __future__ import annotations

import argparse

from dataflow.utils.storage import FileStorage

from data_pipelines.operators import (
    ClawRecordReader,
    DomainDifficultyBalancer,
    FailureTaxonomyAnnotator,
    LeakageGuardOperator,
    ToolAlignmentValidator,
    TrajectoryQualityScorer,
)


class AgentSFTV1Pipeline:
    """Run M2 operators using DataFlow step storage and JSONL caches."""

    def __init__(
        self,
        *,
        silver_records: str,
        silver_manifest: str,
        cache_path: str = "./cache/agent_sft_v1",
        quality_threshold: float = 0.70,
    ):
        self.storage = FileStorage(
            first_entry_file_name=silver_records,
            cache_path=cache_path,
            file_name_prefix="agent_sft_v1",
            cache_type="jsonl",
        )
        self.operators = [
            ClawRecordReader(silver_manifest),
            ToolAlignmentValidator(),
            LeakageGuardOperator(),
            FailureTaxonomyAnnotator(),
            TrajectoryQualityScorer(),
            DomainDifficultyBalancer(quality_threshold=quality_threshold),
        ]

    def forward(self) -> None:
        for operator in self.operators:
            operator.run(storage=self.storage.step())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Claw agent_sft_v1 in an isolated DataFlow environment"
    )
    parser.add_argument("--silver-records", required=True)
    parser.add_argument("--silver-manifest", required=True)
    parser.add_argument("--cache-path", default="./cache/agent_sft_v1")
    parser.add_argument("--quality-threshold", type=float, default=0.70)
    args = parser.parse_args()
    AgentSFTV1Pipeline(
        silver_records=args.silver_records,
        silver_manifest=args.silver_manifest,
        cache_path=args.cache_path,
        quality_threshold=args.quality_threshold,
    ).forward()


if __name__ == "__main__":
    main()
