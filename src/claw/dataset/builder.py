"""Reproducible verified dataset builder with manifest and analysis outputs."""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..experiment.artifacts import ArtifactStore
from ..experiment.schemas import (
    DatasetManifest,
    TaskSpec,
    VerificationReport,
    canonical_hash,
)
from ..trajectory.schema import Trajectory, stable_id
from .converters import extract_messages, segment_messages, to_sft_sample
from .filters import STRATEGIES, deduplicate, strategy_accepts
from .leakage import LeakageError, LeakageReport, check_leakage


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _jsonl(items: List[Dict[str, Any]]) -> str:
    return "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
        for item in items
    )


@dataclass
class DatasetRecord:
    task: TaskSpec
    trajectory: Trajectory
    verification: Optional[VerificationReport] = None
    artifact_store: Optional[ArtifactStore] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)

    def prepare(self) -> None:
        self.task.validate()
        self.trajectory.validate()
        if self.verification is not None:
            self.verification.validate()
            if (
                self.verification.trajectory_ref
                != self.trajectory.header.trajectory_id
            ):
                raise ValueError("verification does not reference trajectory")
        self.messages = extract_messages(
            self.trajectory, artifact_store=self.artifact_store
        )


@dataclass
class DatasetBuildResult:
    manifest: DatasetManifest
    samples: List[Dict[str, Any]]
    analysis: List[Dict[str, Any]]
    exclusions: List[Dict[str, str]]
    leakage_report: LeakageReport


class DatasetBuilder:
    def __init__(
        self,
        *,
        generation_commit: str,
        artifact_store: Optional[ArtifactStore] = None,
    ):
        if not generation_commit:
            raise ValueError("generation_commit must not be empty")
        self.generation_commit = generation_commit
        self.artifact_store = artifact_store

    def build(
        self,
        records: List[DatasetRecord],
        *,
        strategy: str,
        split: str,
        output_dir: Union[str, os.PathLike[str]],
        quality_threshold: float = 0.80,
        reviewer_threshold: float = 0.70,
        max_messages: int = 128,
        dataset_version: str = "1.0.0",
    ) -> DatasetBuildResult:
        if strategy not in STRATEGIES:
            raise ValueError(f"unknown dataset strategy: {strategy}")
        if split not in {"train", "dev", "test"}:
            raise ValueError("split must be train, dev, or test")
        if not 0.0 <= quality_threshold <= 1.0:
            raise ValueError("quality_threshold must be within [0, 1]")
        if not 0.0 <= reviewer_threshold <= 1.0:
            raise ValueError("reviewer_threshold must be within [0, 1]")

        for record in records:
            record.prepare()
        leakage = check_leakage(records, target_split=split)
        if not leakage.passed:
            raise LeakageError(leakage)

        deduped, exclusions = deduplicate(records)
        selected = []
        for record in deduped:
            accepted, reason = strategy_accepts(
                record,
                strategy=strategy,
                quality_threshold=quality_threshold,
                reviewer_threshold=reviewer_threshold,
            )
            if accepted:
                selected.append(record)
            else:
                exclusions.append(
                    {
                        "trajectory_id": record.trajectory.header.trajectory_id,
                        "reason": reason,
                    }
                )

        samples: List[Dict[str, Any]] = []
        analysis: List[Dict[str, Any]] = []
        materialized = []
        sample_hashes = set()
        for record in selected:
            segments = segment_messages(
                record.messages, max_messages=max_messages
            )
            added = False
            for index, segment in enumerate(segments):
                sample = to_sft_sample(
                    segment,
                    task_id=record.task.task_id,
                    trajectory_id=record.trajectory.header.trajectory_id,
                    segment_index=index,
                )
                sample_hash = canonical_hash(sample["messages"])
                if sample_hash in sample_hashes:
                    exclusions.append(
                        {
                            "trajectory_id": record.trajectory.header.trajectory_id,
                            "reason": f"duplicate_sample:{index}",
                        }
                    )
                    continue
                sample_hashes.add(sample_hash)
                samples.append(sample)
                added = True
            if not added:
                continue
            materialized.append(record)
            analysis.append(
                {
                    "task_id": record.task.task_id,
                    "family_id": record.task.family_id,
                    "trajectory_id": record.trajectory.header.trajectory_id,
                    "verification": (
                        record.verification.to_dict()
                        if record.verification is not None
                        else None
                    ),
                }
            )

        filter_config = {
            "strategy": strategy,
            "split": split,
            "quality_threshold": quality_threshold,
            "reviewer_threshold": reviewer_threshold,
            "max_messages": max_messages,
        }
        source_ids = [
            record.trajectory.header.trajectory_id for record in materialized
        ]
        dataset_id = stable_id(
            "dataset",
            {
                "sources": source_ids,
                "filter_config": filter_config,
                "samples": samples,
            },
        )
        output = Path(output_dir).resolve()
        sft_name = f"{strategy}-sft.jsonl"
        analysis_name = f"{strategy}-analysis.jsonl"
        exclusions_name = f"{strategy}-exclusions.jsonl"
        sft_content = _jsonl(samples)
        analysis_content = _jsonl(analysis)
        exclusions_content = _jsonl(exclusions)
        _atomic_write_text(output / sft_name, sft_content)
        _atomic_write_text(output / analysis_name, analysis_content)
        _atomic_write_text(output / exclusions_name, exclusions_content)

        output_refs = [sft_name, analysis_name, exclusions_name]
        if self.artifact_store is not None:
            output_refs = [
                self.artifact_store.put_text(
                    sft_content,
                    media_type="application/x-ndjson",
                ).uri,
                self.artifact_store.put_text(
                    analysis_content,
                    media_type="application/x-ndjson",
                ).uri,
                self.artifact_store.put_text(
                    exclusions_content,
                    media_type="application/x-ndjson",
                ).uri,
            ]

        verdict_counts = Counter(
            (
                record.verification.verdict
                if record.verification is not None
                else "unevaluated"
            )
            for record in materialized
        )
        bad_case_counts = Counter(
            bad_case.get(
                "effective_primary_category",
                bad_case.get("primary_category", "unknown"),
            )
            for record in materialized
            if record.verification is not None
            for bad_case in record.verification.bad_cases
        )
        manifest = DatasetManifest(
            dataset_id=dataset_id,
            version=dataset_version,
            strategy=strategy,
            source_trajectory_ids=source_ids,
            filter_config_hash=canonical_hash(filter_config),
            split=split,
            sample_count=len(samples),
            content_hash=canonical_hash(samples),
            generation_commit=self.generation_commit,
            filter_config=filter_config,
            quality_summary={
                "selected_trajectories": len(materialized),
                "excluded_trajectories": len(exclusions),
                "verdict_counts": dict(sorted(verdict_counts.items())),
                "bad_case_counts": dict(sorted(bad_case_counts.items())),
            },
            leakage_report=leakage.to_dict(),
            output_refs=output_refs,
        )
        manifest.validate()
        _atomic_write_text(
            output / f"{strategy}-manifest.json",
            json.dumps(
                manifest.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        return DatasetBuildResult(
            manifest=manifest,
            samples=samples,
            analysis=analysis,
            exclusions=exclusions,
            leakage_report=leakage,
        )
