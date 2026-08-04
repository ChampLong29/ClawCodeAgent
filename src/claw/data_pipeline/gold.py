"""Gold dataset manifest and deterministic DataFlow-style governance pipeline."""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from ..experiment.schemas import SchemaValidationError, canonical_hash, utc_now
from ..integrations.llamafactory import LlamaFactoryExporter
from ..trajectory.schema import stable_id
from .manifest import SilverDatasetManifest
from .operators import (
    OPERATOR_VERSIONS,
    DomainDifficultyBalancer,
    FailureTaxonomyAnnotator,
    LeakageGuardOperator,
    ToolAlignmentValidator,
    TrajectoryQualityScorer,
)
from .schemas import (
    AgentTrainingRecord,
    AgentTrainingRecordError,
    record_collection_hash,
)


GOLD_MANIFEST_SCHEMA_VERSION = "gold_dataset_manifest.v1"
GOVERNANCE_PIPELINE_VERSION = "agent_sft_v1"


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


def _jsonl(items: Iterable[Dict[str, Any]]) -> str:
    return "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
        for item in items
    )


@dataclass
class GoldDatasetManifest:
    dataset_id: str
    source_silver_id: str
    record_count: int
    record_ids: List[str]
    content_hash: str
    generation_commit: str
    filter_config_hash: str
    filter_config: Dict[str, Any]
    operator_versions: Dict[str, str]
    quality_summary: Dict[str, Any]
    output_refs: List[str]
    pipeline_version: str = GOVERNANCE_PIPELINE_VERSION
    generated_at: str = field(default_factory=utc_now)
    schema_version: str = GOLD_MANIFEST_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != GOLD_MANIFEST_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"unsupported Gold manifest schema: {self.schema_version}"
            )
        for name in (
            "dataset_id",
            "source_silver_id",
            "content_hash",
            "generation_commit",
            "filter_config_hash",
            "pipeline_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise SchemaValidationError(f"{name} must be a non-empty string")
        if self.record_count != len(self.record_ids):
            raise SchemaValidationError("record_count does not match record_ids")
        if len(self.record_ids) != len(set(self.record_ids)):
            raise SchemaValidationError("record_ids must be unique")
        if canonical_hash(self.filter_config) != self.filter_config_hash:
            raise SchemaValidationError("filter_config_hash does not match filter_config")
        if not self.operator_versions:
            raise SchemaValidationError("operator_versions must not be empty")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoldDatasetManifest":
        manifest = cls(**data)
        manifest.validate()
        return manifest


class ClawRecordReader:
    """Verify Silver JSONL against its manifest before DataFlow processing."""

    version = "claw_record_reader.v1"

    def read(
        self,
        records_path: Union[str, os.PathLike[str]],
        manifest_path: Union[str, os.PathLike[str]],
    ) -> tuple[List[AgentTrainingRecord], SilverDatasetManifest]:
        manifest = SilverDatasetManifest.from_dict(
            json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        )
        payloads = []
        with Path(records_path).open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    payloads.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise AgentTrainingRecordError(
                        f"invalid Silver JSON at line {line_number}"
                    ) from exc
        records = [AgentTrainingRecord.from_dict(item) for item in payloads]
        for record in records:
            record.validate(for_training=True)
        record_ids = [record.record_id for record in records]
        if record_ids != manifest.record_ids:
            raise AgentTrainingRecordError(
                "Silver record order/identity does not match manifest"
            )
        if record_collection_hash(payloads) != manifest.content_hash:
            raise AgentTrainingRecordError("Silver content hash does not match manifest")
        return records, manifest


@dataclass
class GoldBuildResult:
    manifest: GoldDatasetManifest
    records: List[AgentTrainingRecord]
    exclusions: List[Dict[str, Any]]
    report: Dict[str, Any]


class AgentSFTGovernancePipeline:
    """M2 deterministic pipeline: validate, guard, annotate, score and balance."""

    version = GOVERNANCE_PIPELINE_VERSION

    def __init__(self):
        self.alignment = ToolAlignmentValidator()
        self.leakage = LeakageGuardOperator()
        self.taxonomy = FailureTaxonomyAnnotator()
        self.scorer = TrajectoryQualityScorer()
        self.balancer = DomainDifficultyBalancer()

    def run(
        self,
        records: Iterable[AgentTrainingRecord],
        *,
        source_manifest: SilverDatasetManifest,
        output_dir: Union[str, os.PathLike[str]],
        quality_threshold: float = 0.70,
        exclude_environment_failures: bool = True,
        export_llamafactory: bool = True,
    ) -> GoldBuildResult:
        if not 0.0 <= quality_threshold <= 1.0:
            raise ValueError("quality_threshold must be within [0, 1]")
        source_manifest.validate()
        items = sorted(records, key=lambda record: record.record_id)
        if [record.record_id for record in items] != source_manifest.record_ids:
            raise AgentTrainingRecordError(
                "pipeline records do not match source Silver manifest"
            )
        filter_config = {
            "quality_threshold": quality_threshold,
            "exclude_environment_failures": exclude_environment_failures,
            "export_llamafactory": export_llamafactory,
        }
        exclusions: List[Dict[str, Any]] = []
        candidates: List[AgentTrainingRecord] = []
        leakage_flag_count = 0
        for record in items:
            record.validate(for_training=True)
            alignment = self.alignment.evaluate(record)
            if not alignment.passed:
                exclusions.append(
                    {
                        "record_id": record.record_id,
                        "stage": "tool_alignment",
                        "reasons": list(alignment.reasons),
                    }
                )
                continue
            leakage = self.leakage.evaluate(record)
            if not leakage.passed:
                leakage_flag_count += 1
                exclusions.append(
                    {
                        "record_id": record.record_id,
                        "stage": "leakage_guard",
                        "reasons": list(leakage.reasons),
                    }
                )
                continue
            annotated = self.taxonomy.apply(record)
            annotated = self.scorer.apply(annotated, alignment=alignment)
            if (
                exclude_environment_failures
                and annotated.features.get("failure_type") == "environment_failure"
            ):
                exclusions.append(
                    {
                        "record_id": record.record_id,
                        "stage": "failure_taxonomy",
                        "reasons": ["environment_failure"],
                    }
                )
                continue
            if annotated.features["quality_score"] < quality_threshold:
                exclusions.append(
                    {
                        "record_id": record.record_id,
                        "stage": "quality_filter",
                        "reasons": ["quality_below_threshold"],
                        "quality_score": annotated.features["quality_score"],
                    }
                )
                continue
            candidates.append(annotated)
        selected, balance_report = self.balancer.apply(candidates)
        selected.sort(key=lambda record: record.record_id)
        payloads = [record.to_dict(for_training=True) for record in selected]
        content_hash = record_collection_hash(payloads)
        record_ids = [record.record_id for record in selected]
        failure_counts = Counter(
            record.features.get("failure_type") or "success" for record in selected
        )
        exclusion_counts = Counter(
            reason
            for exclusion in exclusions
            for reason in exclusion.get("reasons", [])
        )
        report = {
            "pipeline_version": self.version,
            "source_record_count": len(items),
            "selected_record_count": len(selected),
            "excluded_record_count": len(exclusions),
            "retention_rate": len(selected) / len(items) if items else 0.0,
            "leakage_rate": leakage_flag_count / len(items) if items else 0.0,
            "failure_counts": dict(sorted(failure_counts.items())),
            "exclusion_counts": dict(sorted(exclusion_counts.items())),
            "balance": balance_report,
            "processing_cost": {
                "model_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "api_cost": 0.0,
            },
        }
        output = Path(output_dir).resolve()
        output_refs = [
            "gold-records.jsonl",
            "gold-exclusions.jsonl",
            "gold-quality-report.json",
            "data-card.md",
        ]
        manifest = GoldDatasetManifest(
            dataset_id=stable_id(
                "gold",
                {
                    "source_silver_id": source_manifest.dataset_id,
                    "record_ids": record_ids,
                    "content_hash": content_hash,
                    "filter_config": filter_config,
                    "operator_versions": OPERATOR_VERSIONS,
                },
            ),
            source_silver_id=source_manifest.dataset_id,
            record_count=len(selected),
            record_ids=record_ids,
            content_hash=content_hash,
            generation_commit=source_manifest.generation_commit,
            filter_config_hash=canonical_hash(filter_config),
            filter_config=filter_config,
            operator_versions={
                "record_reader": ClawRecordReader.version,
                **OPERATOR_VERSIONS,
            },
            quality_summary=report,
            output_refs=output_refs,
        )
        _atomic_write(output / "gold-records.jsonl", _jsonl(payloads))
        _atomic_write(output / "gold-exclusions.jsonl", _jsonl(exclusions))
        _atomic_write(
            output / "gold-quality-report.json",
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        _atomic_write(output / "data-card.md", self._data_card(manifest, report))
        _atomic_write(
            output / "gold-manifest.json",
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        if export_llamafactory:
            export = LlamaFactoryExporter().export(
                selected,
                output_dir=output / "llamafactory",
                dataset_name="claw_agent_gold_sft",
                source_manifest=manifest,
            )
            manifest.output_refs.extend(
                [
                    str(export.dataset_path.relative_to(output)).replace("\\", "/"),
                    str(export.dataset_info_path.relative_to(output)).replace("\\", "/"),
                    str(export.manifest_path.relative_to(output)).replace("\\", "/"),
                ]
            )
            _atomic_write(
                output / "gold-manifest.json",
                json.dumps(
                    manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
                )
                + "\n",
            )
        return GoldBuildResult(
            manifest=manifest,
            records=selected,
            exclusions=exclusions,
            report=report,
        )

    @staticmethod
    def _data_card(manifest: GoldDatasetManifest, report: Dict[str, Any]) -> str:
        lines = [
            "# Claw Agent Gold Dataset Card",
            "",
            f"- Dataset ID: `{manifest.dataset_id}`",
            f"- Source Silver ID: `{manifest.source_silver_id}`",
            f"- Pipeline: `{manifest.pipeline_version}`",
            f"- Selected records: {manifest.record_count}",
            f"- Retention rate: {report['retention_rate']:.2%}",
            f"- Leakage rate: {report['leakage_rate']:.2%}",
            "- LLM/API processing cost: 0 (deterministic operators only)",
            "",
            "## Governance",
            "",
            "Test split and protected evaluation fields are rejected before export. ",
            "Tool alignment, leakage, failure taxonomy, quality scoring and balance ",
            "weights are versioned in the Gold manifest.",
            "",
            "## Intended use",
            "",
            "Static tool-use SFT and controlled DataFlex experiments. This dataset ",
            "does not itself establish a model-quality improvement.",
            "",
        ]
        return "\n".join(lines)
