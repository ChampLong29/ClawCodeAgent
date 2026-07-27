"""Deterministic dataset strategy filters and deduplication."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

from ..experiment.schemas import canonical_hash


STRATEGIES = {"raw", "success_only", "verifier_filtered"}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def strategy_accepts(
    record: Any,
    *,
    strategy: str,
    quality_threshold: float,
    reviewer_threshold: float,
) -> Tuple[bool, str]:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown dataset strategy: {strategy}")
    if strategy == "raw":
        return True, "accepted"
    report = record.verification
    if report is None:
        return False, "missing_verification"
    if not report.hard_gate_passed or report.verdict != "success":
        return False, f"verdict:{report.verdict}"
    if strategy == "success_only":
        return True, "accepted"
    if report.aggregate_score is None:
        return False, "missing_quality_score"
    if report.aggregate_score < quality_threshold:
        return False, "quality_below_threshold"
    reviewer = next(
        (
            signal
            for signal in report.signals
            if signal.name == "independent_reviewer"
        ),
        None,
    )
    if reviewer is not None and (
        reviewer.score is None or reviewer.score < reviewer_threshold
    ):
        return False, "reviewer_below_threshold"
    return True, "accepted"


def deduplicate(records: Iterable[Any]) -> Tuple[List[Any], List[Dict[str, str]]]:
    kept = []
    exclusions: List[Dict[str, str]] = []
    task_signatures: Dict[str, str] = {}
    trajectory_signatures = set()
    for record in records:
        task_signature = canonical_hash(
            {
                "template_hash": record.task.template_hash,
                "prompt": normalize_text(record.task.prompt),
            }
        )
        prior_task_id = task_signatures.get(task_signature)
        if prior_task_id is not None and prior_task_id != record.task.task_id:
            exclusions.append(
                {
                    "trajectory_id": record.trajectory.header.trajectory_id,
                    "reason": f"duplicate_task_of:{prior_task_id}",
                }
            )
            continue
        task_signatures[task_signature] = record.task.task_id

        assistant_tool_sequence = [
            message
            for message in record.messages
            if message.get("role") in {"assistant", "tool"}
        ]
        trajectory_signature = canonical_hash(
            {
                "task_id": record.task.task_id,
                "sequence": assistant_tool_sequence,
            }
        )
        if trajectory_signature in trajectory_signatures:
            exclusions.append(
                {
                    "trajectory_id": record.trajectory.header.trajectory_id,
                    "reason": "duplicate_trajectory",
                }
            )
            continue
        trajectory_signatures.add(trajectory_signature)
        kept.append(record)
    return kept, exclusions
