"""DataFlow OperatorABC adapters for AgentTrainingRecord governance.

This module is imported only inside the isolated DataFlow environment. Business
rules remain in ``claw.data_pipeline.operators`` so the same policy is tested
without adding pandas/DataFlow to Claw's runtime dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path

from dataflow.core import OperatorABC
from dataflow.utils.storage import DataFlowStorage

from claw.data_pipeline import AgentTrainingRecord, SilverDatasetManifest
from claw.data_pipeline.schemas import record_collection_hash
from claw.data_pipeline.operators import (
    DomainDifficultyBalancer as CoreBalancer,
    FailureTaxonomyAnnotator as CoreTaxonomy,
    LeakageGuardOperator as CoreLeakageGuard,
    ToolAlignmentValidator as CoreAlignmentValidator,
    TrajectoryQualityScorer as CoreQualityScorer,
)


_RECORD_FIELDS = (
    "schema_version",
    "record_id",
    "content_hash",
    "task",
    "messages",
    "verification",
    "execution",
    "features",
    "lineage",
)


def _record_from_row(row) -> AgentTrainingRecord:
    return AgentTrainingRecord.from_dict({key: row[key] for key in _RECORD_FIELDS})


def _write_record(dataframe, index, record: AgentTrainingRecord) -> None:
    for key, value in record.to_dict(for_training=True).items():
        dataframe.at[index, key] = value


def _active(dataframe, index) -> bool:
    return bool(dataframe.at[index, "claw_keep"])


def _exclude(dataframe, index, stage: str, reasons) -> None:
    dataframe.at[index, "claw_keep"] = False
    dataframe.at[index, "claw_exclusion_stage"] = stage
    dataframe.at[index, "claw_exclusion_reasons"] = list(reasons)


class ClawRecordReader(OperatorABC):
    """Validate DataFlow input rows and the source Silver manifest."""

    def __init__(self, manifest_path: str):
        super().__init__()
        self.manifest_path = Path(manifest_path)

    def run(self, storage: DataFlowStorage):
        dataframe = storage.read("dataframe")
        records = [_record_from_row(row) for _, row in dataframe.iterrows()]
        for record in records:
            record.validate(for_training=True)
        manifest = SilverDatasetManifest.from_dict(
            json.loads(self.manifest_path.read_text(encoding="utf-8"))
        )
        payloads = [record.to_dict(for_training=True) for record in records]
        if [record.record_id for record in records] != manifest.record_ids:
            raise ValueError("DataFlow rows do not match the Silver manifest")
        if record_collection_hash(payloads) != manifest.content_hash:
            raise ValueError("DataFlow input hash does not match the Silver manifest")
        dataframe["claw_keep"] = True
        dataframe["claw_exclusion_stage"] = ""
        dataframe["claw_exclusion_reasons"] = [[] for _ in range(len(dataframe))]
        storage.write(dataframe)
        return ""


class ToolAlignmentValidator(OperatorABC):
    def __init__(self):
        super().__init__()
        self.validator = CoreAlignmentValidator()

    def run(self, storage: DataFlowStorage):
        dataframe = storage.read("dataframe")
        dataframe["claw_tool_alignment_valid"] = False
        for index, row in dataframe.iterrows():
            if not _active(dataframe, index):
                continue
            decision = self.validator.evaluate(_record_from_row(row))
            dataframe.at[index, "claw_tool_alignment_valid"] = decision.passed
            if not decision.passed:
                _exclude(dataframe, index, "tool_alignment", decision.reasons)
        storage.write(dataframe)
        return ""


class LeakageGuardOperator(OperatorABC):
    def __init__(self):
        super().__init__()
        self.guard = CoreLeakageGuard()

    def run(self, storage: DataFlowStorage):
        dataframe = storage.read("dataframe")
        dataframe["claw_leakage_flags"] = [[] for _ in range(len(dataframe))]
        for index, row in dataframe.iterrows():
            if not _active(dataframe, index):
                continue
            decision = self.guard.evaluate(_record_from_row(row))
            dataframe.at[index, "claw_leakage_flags"] = list(decision.reasons)
            if not decision.passed:
                _exclude(dataframe, index, "leakage_guard", decision.reasons)
        storage.write(dataframe)
        return ""


class FailureTaxonomyAnnotator(OperatorABC):
    def __init__(self):
        super().__init__()
        self.annotator = CoreTaxonomy()

    def run(self, storage: DataFlowStorage):
        dataframe = storage.read("dataframe")
        for index, row in dataframe.iterrows():
            if not _active(dataframe, index):
                continue
            _write_record(dataframe, index, self.annotator.apply(_record_from_row(row)))
        storage.write(dataframe)
        return ""


class TrajectoryQualityScorer(OperatorABC):
    def __init__(self):
        super().__init__()
        self.scorer = CoreQualityScorer()
        self.alignment = CoreAlignmentValidator()

    def run(self, storage: DataFlowStorage):
        dataframe = storage.read("dataframe")
        for index, row in dataframe.iterrows():
            if not _active(dataframe, index):
                continue
            record = _record_from_row(row)
            _write_record(
                dataframe,
                index,
                self.scorer.apply(record, alignment=self.alignment.evaluate(record)),
            )
        storage.write(dataframe)
        return ""


class DomainDifficultyBalancer(OperatorABC):
    """Filter by score, then add weights without duplicating samples."""

    def __init__(self, quality_threshold: float = 0.70):
        super().__init__()
        if not 0.0 <= quality_threshold <= 1.0:
            raise ValueError("quality_threshold must be within [0, 1]")
        self.quality_threshold = quality_threshold
        self.balancer = CoreBalancer()

    def run(self, storage: DataFlowStorage):
        dataframe = storage.read("dataframe")
        active_indices = []
        records = []
        for index, row in dataframe.iterrows():
            if not _active(dataframe, index):
                continue
            record = _record_from_row(row)
            if record.features.get("failure_type") == "environment_failure":
                _exclude(dataframe, index, "failure_taxonomy", ["environment_failure"])
                continue
            if record.features.get("quality_score", 0.0) < self.quality_threshold:
                _exclude(dataframe, index, "quality_filter", ["quality_below_threshold"])
                continue
            active_indices.append(index)
            records.append(record)
        balanced, _ = self.balancer.apply(records)
        for index, record in zip(active_indices, balanced):
            _write_record(dataframe, index, record)
        storage.write(dataframe[dataframe["claw_keep"]].copy())
        return ""
