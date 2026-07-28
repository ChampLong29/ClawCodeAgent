"""Versioned schemas shared by task, verification, dataset, and experiment flows."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


TASK_SCHEMA_VERSION = "coding_task.v2"
VERIFICATION_SCHEMA_VERSION = "verification_report.v2"
DATASET_SCHEMA_VERSION = "dataset_manifest.v2"
EXPERIMENT_SCHEMA_VERSION = "experiment_run.v1"

_SPLITS = {"train", "dev", "test"}
_SIGNAL_KINDS = {"hard", "soft"}
_SIGNAL_STATUSES = {"pass", "fail", "unknown", "not_applicable"}
_DATASET_STRATEGIES = {"raw", "success_only", "verifier_filtered"}
_VERDICTS = {"success", "failure", "not_verifiable"}
EXPERIMENT_STATUSES = {
    "created",
    "training",
    "trained",
    "contract_verified",
    "benchmarking",
    "completed",
    "failed",
    "recovery_required",
}


class SchemaValidationError(ValueError):
    """Raised when a versioned object violates its schema invariants."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{name} must be a non-empty string")


@dataclass
class TaskSpec:
    """Executable and traceable coding task schema."""

    task_id: str
    task_version: str
    family_id: str
    domain: str
    task_type: str
    difficulty: str
    split: str
    prompt: str
    template_ref: str
    template_hash: str
    test_commands: List[str]
    timeout_seconds: float
    source: str
    license: str
    initial_checks: List[str] = field(default_factory=list)
    oracle_ref: Optional[str] = None
    resource_limits: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    content_hash: str = ""
    schema_version: str = TASK_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != TASK_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"unsupported task schema: {self.schema_version}"
            )
        for name in (
            "task_id",
            "task_version",
            "family_id",
            "domain",
            "task_type",
            "difficulty",
            "prompt",
            "template_ref",
            "template_hash",
            "source",
            "license",
        ):
            _require_text(name, getattr(self, name))
        if self.split not in _SPLITS:
            raise SchemaValidationError(f"split must be one of {sorted(_SPLITS)}")
        if not self.test_commands:
            raise SchemaValidationError("test_commands must not be empty")
        if self.timeout_seconds <= 0:
            raise SchemaValidationError("timeout_seconds must be positive")
        expected = self.compute_content_hash()
        if self.content_hash and self.content_hash != expected:
            raise SchemaValidationError(
                f"content_hash mismatch: expected {expected}, got {self.content_hash}"
            )

    def compute_content_hash(self) -> str:
        return canonical_hash(
            {
                "task_id": self.task_id,
                "task_version": self.task_version,
                "family_id": self.family_id,
                "domain": self.domain,
                "task_type": self.task_type,
                "difficulty": self.difficulty,
                "split": self.split,
                "prompt": self.prompt,
                "template_ref": self.template_ref,
                "template_hash": self.template_hash,
                "initial_checks": self.initial_checks,
                "test_commands": self.test_commands,
                "oracle_ref": self.oracle_ref,
                "timeout_seconds": self.timeout_seconds,
                "resource_limits": self.resource_limits,
                "source": self.source,
                "license": self.license,
                "tags": self.tags,
            }
        )

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["content_hash"] = self.content_hash or self.compute_content_hash()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskSpec":
        spec = cls(**data)
        spec.validate()
        if not spec.content_hash:
            spec.content_hash = spec.compute_content_hash()
        return spec


@dataclass
class VerificationSignal:
    name: str
    kind: str
    status: str
    score: Optional[float] = None
    weight: float = 1.0
    evidence_event_ids: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    required: bool = True
    verifiable: bool = True

    def validate(self) -> None:
        _require_text("signal.name", self.name)
        if self.kind not in _SIGNAL_KINDS:
            raise SchemaValidationError(
                f"signal.kind must be one of {sorted(_SIGNAL_KINDS)}"
            )
        if self.status not in _SIGNAL_STATUSES:
            raise SchemaValidationError(
                f"signal.status must be one of {sorted(_SIGNAL_STATUSES)}"
            )
        if self.score is not None and not 0.0 <= self.score <= 1.0:
            raise SchemaValidationError("signal.score must be within [0, 1]")
        if self.weight < 0:
            raise SchemaValidationError("signal.weight must be non-negative")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VerificationSignal":
        signal = cls(**data)
        signal.validate()
        return signal


@dataclass
class VerificationReport:
    report_id: str
    trajectory_ref: str
    verifier_bundle_version: str
    verdict: str
    hard_gate_passed: bool
    signals: List[VerificationSignal]
    aggregate_score: Optional[float]
    evidence_refs: List[str] = field(default_factory=list)
    bad_cases: List[Dict[str, Any]] = field(default_factory=list)
    reviewer_metadata: Dict[str, Any] = field(default_factory=dict)
    generated_at: str = field(default_factory=utc_now)
    schema_version: str = VERIFICATION_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != VERIFICATION_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"unsupported verification schema: {self.schema_version}"
            )
        for name in ("report_id", "trajectory_ref", "verifier_bundle_version"):
            _require_text(name, getattr(self, name))
        if self.verdict not in _VERDICTS:
            raise SchemaValidationError(
                f"verdict must be one of {sorted(_VERDICTS)}"
            )
        for signal in self.signals:
            signal.validate()
        hard_signals = [signal for signal in self.signals if signal.kind == "hard"]
        hard_failed = any(signal.status == "fail" for signal in hard_signals)
        required_unknown = any(
            signal.required and signal.status == "unknown"
            for signal in hard_signals
        )
        verifiable_passed = any(
            signal.verifiable and signal.status == "pass"
            for signal in hard_signals
        )
        expected_verdict = (
            "failure"
            if hard_failed
            else "not_verifiable"
            if required_unknown or not verifiable_passed
            else "success"
        )
        expected_gate = expected_verdict == "success"
        if self.hard_gate_passed != expected_gate:
            raise SchemaValidationError(
                "hard_gate_passed must be derived from verifiable hard signals"
            )
        if self.verdict != expected_verdict:
            raise SchemaValidationError(
                f"verdict must be {expected_verdict!r} for the supplied hard signals"
            )
        if self.aggregate_score is not None and not 0.0 <= self.aggregate_score <= 1.0:
            raise SchemaValidationError("aggregate_score must be within [0, 1]")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["signals"] = [signal.to_dict() for signal in self.signals]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VerificationReport":
        payload = dict(data)
        payload["signals"] = [
            VerificationSignal.from_dict(item)
            for item in payload.get("signals", [])
        ]
        report = cls(**payload)
        report.validate()
        return report


@dataclass
class DatasetManifest:
    dataset_id: str
    version: str
    strategy: str
    source_trajectory_ids: List[str]
    filter_config_hash: str
    split: str
    sample_count: int
    content_hash: str
    generation_commit: str
    filter_config: Dict[str, Any] = field(default_factory=dict)
    quality_summary: Dict[str, Any] = field(default_factory=dict)
    leakage_report: Dict[str, Any] = field(default_factory=dict)
    output_refs: List[str] = field(default_factory=list)
    generated_at: str = field(default_factory=utc_now)
    schema_version: str = DATASET_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != DATASET_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"unsupported dataset schema: {self.schema_version}"
            )
        for name in (
            "dataset_id",
            "version",
            "filter_config_hash",
            "split",
            "content_hash",
            "generation_commit",
        ):
            _require_text(name, getattr(self, name))
        if self.strategy not in _DATASET_STRATEGIES:
            raise SchemaValidationError(
                f"strategy must be one of {sorted(_DATASET_STRATEGIES)}"
            )
        if self.split not in _SPLITS:
            raise SchemaValidationError(f"split must be one of {sorted(_SPLITS)}")
        if self.filter_config and canonical_hash(self.filter_config) != self.filter_config_hash:
            raise SchemaValidationError("filter_config_hash does not match filter_config")
        if self.sample_count < 0:
            raise SchemaValidationError("sample_count must be non-negative")
        if self.sample_count > 0 and not self.source_trajectory_ids:
            raise SchemaValidationError(
                "non-empty datasets require source_trajectory_ids"
            )
        if len(set(self.source_trajectory_ids)) != len(self.source_trajectory_ids):
            raise SchemaValidationError("source_trajectory_ids must be unique")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DatasetManifest":
        manifest = cls(**data)
        manifest.validate()
        return manifest


@dataclass
class ExperimentRun:
    experiment_id: str
    hypothesis: str
    base_model_ref: str
    dataset_manifest_ref: str
    training_backend: str
    training_config: Dict[str, Any]
    seed: int
    status: str
    adapter_ref: Optional[str] = None
    benchmark_ref: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    artifact_refs: List[str] = field(default_factory=list)
    git_commit: str = ""
    environment: Dict[str, Any] = field(default_factory=dict)
    inference_config: Dict[str, Any] = field(default_factory=dict)
    training_run_ref: Optional[str] = None
    result_refs: List[str] = field(default_factory=list)
    status_history: List[Dict[str, Any]] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    schema_version: str = EXPERIMENT_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != EXPERIMENT_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"unsupported experiment schema: {self.schema_version}"
            )
        for name in (
            "experiment_id",
            "hypothesis",
            "base_model_ref",
            "dataset_manifest_ref",
            "training_backend",
            "status",
        ):
            _require_text(name, getattr(self, name))
        if self.status not in EXPERIMENT_STATUSES:
            raise SchemaValidationError(
                f"status must be one of {sorted(EXPERIMENT_STATUSES)}"
            )
        if self.seed < 0:
            raise SchemaValidationError("seed must be non-negative")
        if not isinstance(self.training_config, dict):
            raise SchemaValidationError("training_config must be an object")
        if not isinstance(self.environment, dict):
            raise SchemaValidationError("environment must be an object")
        if not isinstance(self.inference_config, dict):
            raise SchemaValidationError("inference_config must be an object")
        if len(set(self.artifact_refs)) != len(self.artifact_refs):
            raise SchemaValidationError("artifact_refs must be unique")
        if len(set(self.result_refs)) != len(self.result_refs):
            raise SchemaValidationError("result_refs must be unique")
        for event in self.status_history:
            if not isinstance(event, dict):
                raise SchemaValidationError(
                    "status_history entries must be objects"
                )
            if event.get("status") not in EXPERIMENT_STATUSES:
                raise SchemaValidationError(
                    "status_history contains an unsupported status"
                )

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExperimentRun":
        run = cls(**data)
        run.validate()
        return run
