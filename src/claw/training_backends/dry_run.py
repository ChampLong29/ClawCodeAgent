"""Dependency-free backend used to verify the training lifecycle in CI."""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any, Dict, List

from ..experiment.schemas import ExperimentRun, canonical_hash
from .base import (
    SFTTrainingConfig,
    TrainingBackend,
    TrainingBackendError,
    TrainingDatasetSource,
    TrainingRunRecord,
    atomic_write_text,
)


class DryRunBackend(TrainingBackend):
    """Validate data and materialize non-training contract evidence."""

    backend_name = "dry_run"

    def __init__(self, config: SFTTrainingConfig):
        config.validate()
        self.config = config
        self._records: Dict[str, TrainingRunRecord] = {}
        self._samples: Dict[str, List[Dict[str, Any]]] = {}

    @property
    def output_dir(self) -> Path:
        return Path(self.config.output_dir).resolve()

    def _write_record(self, record: TrainingRunRecord) -> None:
        atomic_write_text(
            self.output_dir / "training-run.json",
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )

    def prepare(
        self, run: ExperimentRun, dataset: TrainingDatasetSource
    ) -> TrainingRunRecord:
        run.validate()
        if run.training_backend != self.backend_name:
            raise TrainingBackendError(
                f"experiment requests {run.training_backend!r}, not dry_run"
            )
        if run.seed != self.config.seed:
            raise TrainingBackendError("experiment seed and training seed differ")
        if canonical_hash(run.training_config) != self.config.config_hash:
            raise TrainingBackendError(
                "experiment training_config differs from backend config"
            )
        if run.dataset_manifest_ref != dataset.manifest.dataset_id:
            raise TrainingBackendError(
                "experiment dataset reference differs from supplied manifest"
            )
        samples = dataset.load_samples()
        record = TrainingRunRecord(
            experiment_id=run.experiment_id,
            backend=self.backend_name,
            base_model_ref=run.base_model_ref,
            base_model_revision=self.config.model_revision,
            dataset_id=dataset.manifest.dataset_id,
            dataset_content_hash=dataset.manifest.content_hash,
            training_config=self.config.to_dict(),
            training_config_hash=self.config.config_hash,
            seed=self.config.seed,
            status="prepared",
            resume_from_checkpoint=self.config.resume_from_checkpoint,
            environment={
                "python": platform.python_version(),
                "platform": platform.platform(),
                "contract_only": True,
            },
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._records[run.experiment_id] = record
        self._samples[run.experiment_id] = samples
        run.status = "prepared"
        self._write_record(record)
        return record

    def train(self, run: ExperimentRun) -> TrainingRunRecord:
        record = self._records.get(run.experiment_id)
        if record is None:
            raise TrainingBackendError("prepare must be called before train")
        record.status = "running"
        run.status = "running"
        self._write_record(record)

        adapter_dir = self.output_dir / "dry-run-adapter"
        adapter_dir.mkdir(parents=True, exist_ok=True)
        adapter_metadata = {
            "backend": self.backend_name,
            "contract_verified": True,
            "training_verified": False,
            "base_model_ref": record.base_model_ref,
            "base_model_revision": record.base_model_revision,
            "dataset_id": record.dataset_id,
            "dataset_content_hash": record.dataset_content_hash,
            "training_config_hash": record.training_config_hash,
            "sample_count": len(self._samples[run.experiment_id]),
        }
        adapter_path = adapter_dir / "adapter_config.json"
        atomic_write_text(
            adapter_path,
            json.dumps(
                adapter_metadata, ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n",
        )
        record.status = "completed"
        record.adapter_ref = str(adapter_dir)
        record.metrics = {
            "sample_count": len(self._samples[run.experiment_id]),
            "contract_verified": True,
            "training_verified": False,
        }
        record.artifact_refs = [str(adapter_path)]
        run.status = "completed"
        run.adapter_ref = record.adapter_ref
        run.metrics = dict(record.metrics)
        run.artifact_refs = list(record.artifact_refs)
        self._write_record(record)
        return record

    def evaluate_checkpoint(self, run: ExperimentRun) -> Dict[str, Any]:
        record = self._records.get(run.experiment_id)
        if record is None or record.status != "completed":
            raise TrainingBackendError("dry-run checkpoint is not complete")
        return {
            "contract_verified": True,
            "training_verified": False,
            "sample_count": len(self._samples[run.experiment_id]),
        }

    def collect_artifacts(self, run: ExperimentRun) -> List[str]:
        record = self._records.get(run.experiment_id)
        if record is None:
            raise TrainingBackendError("unknown training run")
        return list(record.artifact_refs)

    def cleanup(self, run: ExperimentRun) -> None:
        self._samples.pop(run.experiment_id, None)
