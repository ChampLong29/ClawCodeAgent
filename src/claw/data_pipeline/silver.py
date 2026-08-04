"""Convert verified Claw trajectories into deterministic Silver records."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from ..dataset.builder import DatasetRecord
from ..dataset.leakage import LeakageError, check_leakage
from ..trajectory.schema import stable_id
from .manifest import SilverDatasetManifest
from .schemas import (
    AgentTrainingRecord,
    AgentTrainingRecordError,
    record_collection_hash,
)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _duration_seconds(started_at: str, finished_at: Optional[str]) -> Optional[float]:
    if not finished_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return max(0.0, (finished - started).total_seconds())


def _signal_summary(record: DatasetRecord) -> Dict[str, Any]:
    report = record.verification
    if report is None:
        return {
            "report_id": None,
            "verdict": "unevaluated",
            "hard_gate_passed": False,
            "aggregate_score": None,
            "tests_passed": None,
            "diff_scope_passed": None,
            "reviewer_score": None,
            "signals": {},
        }
    signals = {
        signal.name: {
            "kind": signal.kind,
            "status": signal.status,
            "score": signal.score,
        }
        for signal in report.signals
    }

    def passed(name: str) -> Optional[bool]:
        signal = signals.get(name)
        return None if signal is None else signal["status"] == "pass"

    reviewer = signals.get("independent_reviewer") or {}
    return {
        "report_id": report.report_id,
        "verdict": report.verdict,
        "hard_gate_passed": report.hard_gate_passed,
        "aggregate_score": report.aggregate_score,
        "tests_passed": passed("test_pass_rate"),
        "diff_scope_passed": passed("diff_scope"),
        "reviewer_score": reviewer.get("score"),
        "signals": signals,
    }


def _failure_type(record: DatasetRecord) -> Optional[str]:
    report = record.verification
    if report is None or not report.bad_cases:
        return None
    bad_case = report.bad_cases[0]
    return bad_case.get(
        "effective_primary_category", bad_case.get("primary_category")
    )


def _workspace_markers(source: DatasetRecord) -> List[str]:
    markers = set()
    for event in source.trajectory.events:
        if event.event_type != "episode_started":
            continue
        cwd = event.payload.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            continue
        normalized = cwd.rstrip("\\/")
        variants = {
            normalized,
            normalized.replace("\\", "/"),
            normalized.replace("/", "\\"),
        }
        escaped_variants = {
            json.dumps(item, ensure_ascii=False)[1:-1] for item in tuple(variants)
        }
        variants.update(escaped_variants)
        markers.update(item for item in variants if item)
    return sorted(markers, key=len, reverse=True)


def _sanitize_workspace_paths(value: Any, markers: List[str]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_workspace_paths(child, markers)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_workspace_paths(child, markers) for child in value]
    if isinstance(value, tuple):
        return [_sanitize_workspace_paths(child, markers) for child in value]
    if isinstance(value, str):
        sanitized = value
        for marker in markers:
            sanitized = sanitized.replace(marker, "<workspace>")
        return sanitized
    return value


def to_training_record(
    source: DatasetRecord,
    *,
    generation_commit: str,
    allow_test: bool = False,
) -> AgentTrainingRecord:
    """Create a leakage-safe Silver record from one verified trajectory."""
    if not generation_commit.strip():
        raise AgentTrainingRecordError("generation_commit must not be empty")
    if source.verification is None:
        raise AgentTrainingRecordError("Silver records require verification evidence")
    source.prepare()
    task = source.task
    if task.split == "test" and not allow_test:
        raise AgentTrainingRecordError("test split cannot enter Silver training data")
    header = source.trajectory.header
    termination = header.termination
    usage = dict(termination.usage) if termination else {}
    verification = _signal_summary(source)
    safe_task = {
        "task_id": task.task_id,
        "task_version": task.task_version,
        "family_id": task.family_id,
        "split": task.split,
        "domain": task.domain,
        "task_type": task.task_type,
        "difficulty": task.difficulty,
        "source": task.source,
        "license": task.license,
        "tags": list(task.tags),
        "content_hash": task.content_hash or task.compute_content_hash(),
    }
    record = AgentTrainingRecord(
        task=safe_task,
        messages=_sanitize_workspace_paths(
            copy.deepcopy(source.messages), _workspace_markers(source)
        ),
        verification=verification,
        execution={
            "termination_reason": termination.reason if termination else None,
            "turns": usage.get("turns", usage.get("model_calls")),
            "model_calls": usage.get("model_calls"),
            "tool_calls": usage.get("tool_calls"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cost": copy.deepcopy(termination.cost) if termination else {},
            "latency_seconds": _duration_seconds(
                header.started_at, header.finished_at
            ),
        },
        features={
            "failure_type": _failure_type(source),
            "quality_score": verification.get("aggregate_score"),
            "efficiency_score": None,
            "sample_weight": None,
        },
        lineage={
            "episode_ref": header.episode_id,
            "trajectory_ref": header.trajectory_id,
            "verification_ref": (
                source.verification.report_id if source.verification else None
            ),
            "generation_commit": generation_commit,
            "task_content_hash": safe_task["content_hash"],
            "model_version": header.model_version,
            "runtime_version": header.runtime_version,
            "prompt_version": header.prompt_version,
            "tool_version": header.tool_version,
            "config_version": header.config_version,
            "verifier_bundle_version": (
                source.verification.verifier_bundle_version
                if source.verification
                else None
            ),
        },
    )
    record.record_id = record.compute_record_id()
    record.content_hash = record.compute_content_hash()
    record.validate(for_training=not allow_test)
    return record


@dataclass
class SilverBuildResult:
    manifest: SilverDatasetManifest
    records: List[AgentTrainingRecord]


class SilverDatasetBuilder:
    """Materialize sorted Silver JSONL and a content-addressed manifest."""

    def __init__(self, *, generation_commit: str):
        if not generation_commit.strip():
            raise ValueError("generation_commit must not be empty")
        self.generation_commit = generation_commit

    def build(
        self,
        sources: Iterable[DatasetRecord],
        *,
        output_dir: Union[str, os.PathLike[str]],
    ) -> SilverBuildResult:
        source_records = list(sources)
        for source in source_records:
            source.prepare()
        splits = {source.task.split for source in source_records}
        if "test" in splits:
            raise AgentTrainingRecordError("test split cannot enter Silver training data")
        if len(splits) > 1:
            raise AgentTrainingRecordError(
                f"Silver build requires one split, got {sorted(splits)}"
            )
        target_split = next(iter(splits), "train")
        leakage = check_leakage(source_records, target_split=target_split)
        if not leakage.passed:
            raise LeakageError(leakage)
        records = [
            to_training_record(
                source,
                generation_commit=self.generation_commit,
            )
            for source in source_records
        ]
        records.sort(key=lambda item: item.record_id)
        record_ids = [item.record_id for item in records]
        if len(set(record_ids)) != len(record_ids):
            raise AgentTrainingRecordError("duplicate Silver record_id")
        payloads = [item.to_dict(for_training=True) for item in records]
        content_hash = record_collection_hash(payloads)
        dataset_id = stable_id(
            "silver",
            {
                "record_ids": record_ids,
                "content_hash": content_hash,
                "generation_commit": self.generation_commit,
            },
        )
        manifest = SilverDatasetManifest(
            dataset_id=dataset_id,
            record_count=len(records),
            record_ids=record_ids,
            content_hash=content_hash,
            generation_commit=self.generation_commit,
            leakage_report=leakage.to_dict(),
        )
        output = Path(output_dir).resolve()
        jsonl = "".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
            for item in payloads
        )
        _atomic_write(output / manifest.output_file, jsonl)
        _atomic_write(
            output / "silver-manifest.json",
            json.dumps(
                manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n",
        )
        return SilverBuildResult(manifest=manifest, records=records)
