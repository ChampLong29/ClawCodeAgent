"""Stable Silver record schema used at external data-pipeline boundaries."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Dict, List

from ..dataset.converters import validate_tool_alignment
from ..experiment.schemas import canonical_hash
from ..trajectory.schema import stable_id


AGENT_TRAINING_RECORD_SCHEMA_VERSION = "agent_training_record.v1"
_SPLITS = {"train", "dev", "test"}
_TASK_FIELDS = {
    "task_id",
    "task_version",
    "family_id",
    "split",
    "domain",
    "task_type",
    "difficulty",
    "source",
    "license",
    "tags",
    "content_hash",
}
_SENSITIVE_TASK_FIELDS = {
    "initial_checks",
    "test_commands",
    "oracle_ref",
    "test_assets_ref",
    "test_assets_hash",
    "resource_limits",
    "template_ref",
    "template_hash",
}


class AgentTrainingRecordError(ValueError):
    """Raised when a record is unsafe or inconsistent for data processing."""


def normalize_record_numbers(value: Any) -> Any:
    """Normalize JSON numbers for stable hashes across pandas round-trips."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AgentTrainingRecordError("record contains a non-finite number")
        return round(value, 10)
    if isinstance(value, dict):
        return {
            str(key): normalize_record_numbers(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [normalize_record_numbers(child) for child in value]
    if isinstance(value, tuple):
        return [normalize_record_numbers(child) for child in value]
    return value


def record_collection_hash(records: List[Dict[str, Any]]) -> str:
    return canonical_hash(normalize_record_numbers(records))


def _require_mapping(name: str, value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentTrainingRecordError(f"{name} must be an object")
    return value


def _require_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentTrainingRecordError(f"{name} must be a non-empty string")
    return value


@dataclass
class AgentTrainingRecord:
    """Tool-use training record with immutable evidence and derived features."""

    task: Dict[str, Any]
    messages: List[Dict[str, Any]]
    verification: Dict[str, Any]
    execution: Dict[str, Any]
    features: Dict[str, Any]
    lineage: Dict[str, Any]
    record_id: str = ""
    content_hash: str = ""
    schema_version: str = AGENT_TRAINING_RECORD_SCHEMA_VERSION

    def identity_payload(self) -> Dict[str, Any]:
        """Return facts that identify a source record, excluding derived features."""
        return {
            "schema_version": self.schema_version,
            "task": self.task,
            "messages": self.messages,
            "verification": self.verification,
            "execution": self.execution,
            "lineage": self.lineage,
        }

    def compute_record_id(self) -> str:
        return stable_id("record", normalize_record_numbers(self.identity_payload()))

    def hash_payload(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "record_id": self.record_id or self.compute_record_id(),
            "task": self.task,
            "messages": self.messages,
            "verification": self.verification,
            "execution": self.execution,
            "features": self.features,
            "lineage": self.lineage,
        }

    def compute_content_hash(self) -> str:
        return canonical_hash(normalize_record_numbers(self.hash_payload()))

    def validate(self, *, for_training: bool = False) -> None:
        if self.schema_version != AGENT_TRAINING_RECORD_SCHEMA_VERSION:
            raise AgentTrainingRecordError(
                f"unsupported record schema: {self.schema_version}"
            )
        task = _require_mapping("task", self.task)
        for field_name in (
            "task_id",
            "task_version",
            "family_id",
            "domain",
            "task_type",
            "difficulty",
            "source",
            "license",
            "content_hash",
        ):
            _require_text(f"task.{field_name}", task.get(field_name))
        split = task.get("split")
        if split not in _SPLITS:
            raise AgentTrainingRecordError(
                f"task.split must be one of {sorted(_SPLITS)}"
            )
        if for_training and split == "test":
            raise AgentTrainingRecordError("test split cannot enter training export")
        unknown = sorted(set(task) - _TASK_FIELDS)
        sensitive = sorted(set(task) & _SENSITIVE_TASK_FIELDS)
        if sensitive:
            raise AgentTrainingRecordError(
                f"task contains protected evaluation fields: {sensitive}"
            )
        if unknown:
            raise AgentTrainingRecordError(f"task contains unsupported fields: {unknown}")
        if not isinstance(self.messages, list) or not self.messages:
            raise AgentTrainingRecordError("messages must be a non-empty list")
        try:
            validate_tool_alignment(self.messages)
        except ValueError as exc:
            raise AgentTrainingRecordError(str(exc)) from exc
        _require_mapping("verification", self.verification)
        _require_mapping("execution", self.execution)
        _require_mapping("features", self.features)
        lineage = _require_mapping("lineage", self.lineage)
        for field_name in (
            "episode_ref",
            "trajectory_ref",
            "verification_ref",
            "generation_commit",
            "task_content_hash",
            "model_version",
            "runtime_version",
            "prompt_version",
            "tool_version",
            "config_version",
            "verifier_bundle_version",
        ):
            _require_text(f"lineage.{field_name}", lineage.get(field_name))
        expected_id = self.compute_record_id()
        if self.record_id and self.record_id != expected_id:
            raise AgentTrainingRecordError(
                f"record_id mismatch: expected {expected_id}, got {self.record_id}"
            )
        expected_hash = canonical_hash(normalize_record_numbers(
            {
                **self.hash_payload(),
                "record_id": expected_id,
            }
        ))
        if self.content_hash and self.content_hash != expected_hash:
            raise AgentTrainingRecordError(
                "content_hash does not match the normalized record"
            )

    def to_dict(self, *, for_training: bool = False) -> Dict[str, Any]:
        self.validate(for_training=for_training)
        record_id = self.record_id or self.compute_record_id()
        payload = {
            "schema_version": self.schema_version,
            "record_id": record_id,
            "task": copy.deepcopy(self.task),
            "messages": copy.deepcopy(self.messages),
            "verification": copy.deepcopy(self.verification),
            "execution": copy.deepcopy(self.execution),
            "features": copy.deepcopy(self.features),
            "lineage": copy.deepcopy(self.lineage),
        }
        payload["content_hash"] = self.content_hash or canonical_hash(
            normalize_record_numbers(payload)
        )
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentTrainingRecord":
        record = cls(
            task=copy.deepcopy(data.get("task")),
            messages=copy.deepcopy(data.get("messages")),
            verification=copy.deepcopy(data.get("verification")),
            execution=copy.deepcopy(data.get("execution")),
            features=copy.deepcopy(data.get("features")),
            lineage=copy.deepcopy(data.get("lineage")),
            record_id=data.get("record_id", ""),
            content_hash=data.get("content_hash", ""),
            schema_version=data.get(
                "schema_version", AGENT_TRAINING_RECORD_SCHEMA_VERSION
            ),
        )
        record.validate()
        return record
