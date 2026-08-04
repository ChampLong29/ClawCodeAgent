"""Manifest for deterministic Silver dataset materialization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from ..experiment.schemas import SchemaValidationError, utc_now


SILVER_MANIFEST_SCHEMA_VERSION = "silver_dataset_manifest.v1"


@dataclass
class SilverDatasetManifest:
    dataset_id: str
    record_count: int
    record_ids: List[str]
    content_hash: str
    generation_commit: str
    leakage_report: Dict[str, Any] = field(default_factory=dict)
    output_file: str = "silver-records.jsonl"
    source_schema: str = "agent_trajectory.v2"
    record_schema: str = "agent_training_record.v1"
    generated_at: str = field(default_factory=utc_now)
    schema_version: str = SILVER_MANIFEST_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SILVER_MANIFEST_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"unsupported Silver manifest schema: {self.schema_version}"
            )
        for name in (
            "dataset_id",
            "content_hash",
            "generation_commit",
            "output_file",
            "source_schema",
            "record_schema",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise SchemaValidationError(f"{name} must be a non-empty string")
        if self.record_count != len(self.record_ids):
            raise SchemaValidationError("record_count does not match record_ids")
        if len(set(self.record_ids)) != len(self.record_ids):
            raise SchemaValidationError("record_ids must be unique")
        if self.leakage_report and not self.leakage_report.get("passed", False):
            raise SchemaValidationError("Silver manifest requires a passed leakage report")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SilverDatasetManifest":
        manifest = cls(**data)
        manifest.validate()
        return manifest
