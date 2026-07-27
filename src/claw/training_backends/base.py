"""Stable contracts and manifests for lightweight SFT backends."""

from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..experiment.schemas import (
    DatasetManifest,
    ExperimentRun,
    SchemaValidationError,
    canonical_hash,
)


TRAINING_RUN_SCHEMA_VERSION = "training_run.v1"
TRAINING_BACKENDS = {"peft_sft", "dry_run"}


class TrainingBackendError(RuntimeError):
    """Raised when a training backend cannot safely complete a run."""


class TrainingDependencyError(TrainingBackendError):
    """Raised when an optional training dependency is unavailable."""


def atomic_write_text(path: Path, content: str) -> None:
    """Write a text artifact without exposing a partial file."""
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


@dataclass
class SFTTrainingConfig:
    """Reproducible single-machine LoRA/QLoRA configuration."""

    output_dir: str
    model_revision: str
    mode: str = "lora"
    seed: int = 42
    max_seq_length: int = 4096
    num_train_epochs: float = 1.0
    learning_rate: float = 2e-4
    per_device_train_batch_size: int = 1
    gradient_accumulation_steps: int = 8
    warmup_ratio: float = 0.03
    logging_steps: int = 1
    save_steps: int = 50
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: List[str] = field(default_factory=list)
    bf16: bool = False
    fp16: bool = False
    gradient_checkpointing: bool = True
    resume_from_checkpoint: Optional[str] = None
    trust_remote_code: bool = False

    def validate(self) -> None:
        if self.mode not in {"lora", "qlora"}:
            raise SchemaValidationError("mode must be 'lora' or 'qlora'")
        if not self.output_dir:
            raise SchemaValidationError("output_dir must not be empty")
        if not self.model_revision:
            raise SchemaValidationError("model_revision must not be empty")
        if self.seed < 0:
            raise SchemaValidationError("seed must be non-negative")
        if self.max_seq_length <= 0:
            raise SchemaValidationError("max_seq_length must be positive")
        if self.num_train_epochs <= 0:
            raise SchemaValidationError("num_train_epochs must be positive")
        if self.learning_rate <= 0:
            raise SchemaValidationError("learning_rate must be positive")
        if self.per_device_train_batch_size <= 0:
            raise SchemaValidationError(
                "per_device_train_batch_size must be positive"
            )
        if self.gradient_accumulation_steps <= 0:
            raise SchemaValidationError(
                "gradient_accumulation_steps must be positive"
            )
        if self.lora_r <= 0 or self.lora_alpha <= 0:
            raise SchemaValidationError("LoRA rank and alpha must be positive")
        if self.bf16 and self.fp16:
            raise SchemaValidationError("bf16 and fp16 cannot both be enabled")

    @property
    def config_hash(self) -> str:
        self.validate()
        return canonical_hash(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SFTTrainingConfig":
        config = cls(**data)
        config.validate()
        return config


@dataclass
class TrainingDatasetSource:
    """A manifest joined to its materialized Tool-use SFT JSONL."""

    manifest: DatasetManifest
    samples_path: Union[str, os.PathLike[str]]

    def load_samples(self) -> List[Dict[str, Any]]:
        self.manifest.validate()
        if self.manifest.split != "train":
            raise TrainingBackendError("SFT data must use the train split")
        samples: List[Dict[str, Any]] = []
        path = Path(self.samples_path).resolve()
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    sample = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise TrainingBackendError(
                        f"invalid JSONL at line {line_number}: {exc}"
                    ) from exc
                if not isinstance(sample, dict):
                    raise TrainingBackendError(
                        f"sample at line {line_number} must be an object"
                    )
                samples.append(sample)
        if len(samples) != self.manifest.sample_count:
            raise TrainingBackendError(
                "dataset sample count does not match its manifest"
            )
        if canonical_hash(samples) != self.manifest.content_hash:
            raise TrainingBackendError(
                "dataset content hash does not match its manifest"
            )
        return samples


@dataclass
class TrainingRunRecord:
    """Auditable training evidence independent of a Web UI."""

    experiment_id: str
    backend: str
    base_model_ref: str
    base_model_revision: str
    dataset_id: str
    dataset_content_hash: str
    training_config: Dict[str, Any]
    training_config_hash: str
    seed: int
    status: str = "created"
    adapter_ref: Optional[str] = None
    resume_from_checkpoint: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    environment: Dict[str, Any] = field(default_factory=dict)
    artifact_refs: List[str] = field(default_factory=list)
    error: Optional[str] = None
    training_verified: bool = False
    schema_version: str = TRAINING_RUN_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != TRAINING_RUN_SCHEMA_VERSION:
            raise SchemaValidationError(
                f"unsupported training run schema: {self.schema_version}"
            )
        if self.backend not in TRAINING_BACKENDS:
            raise SchemaValidationError(f"unsupported backend: {self.backend}")
        if self.status not in {
            "created",
            "prepared",
            "running",
            "completed",
            "failed",
        }:
            raise SchemaValidationError(f"unsupported training status: {self.status}")
        for name in (
            "experiment_id",
            "base_model_ref",
            "base_model_revision",
            "dataset_id",
            "dataset_content_hash",
            "training_config_hash",
        ):
            if not str(getattr(self, name)).strip():
                raise SchemaValidationError(f"{name} must not be empty")
        if canonical_hash(self.training_config) != self.training_config_hash:
            raise SchemaValidationError("training_config_hash mismatch")
        if self.training_verified and (
            self.backend != "peft_sft"
            or self.status != "completed"
            or not self.adapter_ref
        ):
            raise SchemaValidationError(
                "training_verified requires a completed PeFT run and adapter"
            )

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrainingRunRecord":
        record = cls(**data)
        record.validate()
        return record


class TrainingBackend(ABC):
    """Backend lifecycle shared by real PeFT and CI dry runs."""

    @abstractmethod
    def prepare(
        self, run: ExperimentRun, dataset: TrainingDatasetSource
    ) -> TrainingRunRecord:
        raise NotImplementedError

    @abstractmethod
    def train(self, run: ExperimentRun) -> TrainingRunRecord:
        raise NotImplementedError

    @abstractmethod
    def evaluate_checkpoint(self, run: ExperimentRun) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def collect_artifacts(self, run: ExperimentRun) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def cleanup(self, run: ExperimentRun) -> None:
        raise NotImplementedError
