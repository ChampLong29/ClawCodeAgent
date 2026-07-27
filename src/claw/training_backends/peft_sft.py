"""Transformers + PEFT LoRA/QLoRA backend with lazy dependencies."""

from __future__ import annotations

import importlib
import json
import math
import platform
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..experiment.schemas import ExperimentRun, canonical_hash
from .base import (
    SFTTrainingConfig,
    TrainingBackend,
    TrainingBackendError,
    TrainingDatasetSource,
    TrainingDependencyError,
    TrainingRunRecord,
    atomic_write_text,
)
from .chat_template import AssistantOnlyDataCollator, ToolUseChatEncoder


class _ListDataset:
    def __init__(self, samples: List[Dict[str, Any]]):
        self.samples = samples

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> Dict[str, Any]:
        return self.samples[index]


class PeFTSFTBackend(TrainingBackend):
    """Real single-machine LoRA/QLoRA training backend."""

    backend_name = "peft_sft"

    def __init__(self, config: SFTTrainingConfig):
        config.validate()
        self.config = config
        self._states: Dict[str, Dict[str, Any]] = {}

    @property
    def output_dir(self) -> Path:
        return Path(self.config.output_dir).resolve()

    def _dependencies(self) -> Dict[str, Any]:
        missing = []
        modules = {}
        names = ["torch", "transformers", "peft", "accelerate"]
        if self.config.mode == "qlora":
            names.append("bitsandbytes")
        for name in names:
            try:
                modules[name] = importlib.import_module(name)
            except ImportError:
                missing.append(name)
        if missing:
            raise TrainingDependencyError(
                "PeFT training requires optional dependencies: "
                + ", ".join(sorted(missing))
            )
        return modules

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
                f"experiment requests {run.training_backend!r}, not peft_sft"
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
        if not samples:
            raise TrainingBackendError("cannot train on an empty dataset")
        deps = self._dependencies()
        torch = deps["torch"]
        transformers = deps["transformers"]
        peft = deps["peft"]

        self.output_dir.mkdir(parents=True, exist_ok=True)
        tokenizer = transformers.AutoTokenizer.from_pretrained(
            run.base_model_ref,
            revision=self.config.model_revision,
            trust_remote_code=self.config.trust_remote_code,
        )
        if tokenizer.pad_token_id is None:
            if tokenizer.eos_token_id is None:
                raise TrainingBackendError(
                    "tokenizer must define an EOS or padding token"
                )
            tokenizer.pad_token = tokenizer.eos_token
        encoder = ToolUseChatEncoder(
            tokenizer, max_length=self.config.max_seq_length
        )
        encoded = encoder.encode_samples(samples)

        model_kwargs: Dict[str, Any] = {
            "revision": self.config.model_revision,
            "trust_remote_code": self.config.trust_remote_code,
        }
        if self.config.mode == "qlora":
            compute_dtype = torch.bfloat16 if self.config.bf16 else torch.float16
            model_kwargs["quantization_config"] = transformers.BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=compute_dtype,
            )
            model_kwargs["device_map"] = "auto"
        model = transformers.AutoModelForCausalLM.from_pretrained(
            run.base_model_ref, **model_kwargs
        )
        if self.config.mode == "qlora":
            model = peft.prepare_model_for_kbit_training(
                model,
                use_gradient_checkpointing=self.config.gradient_checkpointing,
            )
        elif self.config.gradient_checkpointing:
            model.gradient_checkpointing_enable()
        lora_config = peft.LoraConfig(
            r=self.config.lora_r,
            lora_alpha=self.config.lora_alpha,
            lora_dropout=self.config.lora_dropout,
            target_modules=self.config.target_modules or "all-linear",
            bias="none",
            task_type=peft.TaskType.CAUSAL_LM,
        )
        model = peft.get_peft_model(model, lora_config)

        arguments = transformers.TrainingArguments(
            output_dir=str(self.output_dir / "checkpoints"),
            num_train_epochs=self.config.num_train_epochs,
            learning_rate=self.config.learning_rate,
            per_device_train_batch_size=self.config.per_device_train_batch_size,
            gradient_accumulation_steps=self.config.gradient_accumulation_steps,
            warmup_ratio=self.config.warmup_ratio,
            logging_steps=self.config.logging_steps,
            save_steps=self.config.save_steps,
            seed=self.config.seed,
            data_seed=self.config.seed,
            bf16=self.config.bf16,
            fp16=self.config.fp16,
            report_to=[],
            remove_unused_columns=False,
        )
        train_dataset = _ListDataset(encoded)
        trainer = transformers.Trainer(
            model=model,
            args=arguments,
            train_dataset=train_dataset,
            data_collator=AssistantOnlyDataCollator(tokenizer),
        )
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
                "torch": getattr(torch, "__version__", "unknown"),
                "transformers": getattr(transformers, "__version__", "unknown"),
                "peft": getattr(peft, "__version__", "unknown"),
                "cuda_available": bool(torch.cuda.is_available()),
            },
        )
        self._states[run.experiment_id] = {
            "record": record,
            "trainer": trainer,
            "tokenizer": tokenizer,
            "model": model,
            "dataset": train_dataset,
            "torch": torch,
        }
        run.status = "prepared"
        self._write_record(record)
        return record

    def train(self, run: ExperimentRun) -> TrainingRunRecord:
        state = self._states.get(run.experiment_id)
        if state is None:
            raise TrainingBackendError("prepare must be called before train")
        record: TrainingRunRecord = state["record"]
        trainer = state["trainer"]
        torch = state["torch"]
        record.status = "running"
        run.status = "running"
        self._write_record(record)
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        try:
            output = trainer.train(
                resume_from_checkpoint=self.config.resume_from_checkpoint
            )
            adapter_dir = self.output_dir / "adapter"
            trainer.model.save_pretrained(str(adapter_dir))
            state["tokenizer"].save_pretrained(str(adapter_dir))
            metrics = {
                key: float(value) if isinstance(value, (int, float)) else value
                for key, value in dict(output.metrics or {}).items()
            }
            if torch.cuda.is_available():
                metrics["peak_cuda_memory_bytes"] = int(
                    torch.cuda.max_memory_allocated()
                )
            record.status = "completed"
            record.adapter_ref = str(adapter_dir)
            record.metrics = metrics
            record.training_verified = True
            record.artifact_refs = [
                str(path)
                for path in sorted(adapter_dir.rglob("*"))
                if path.is_file()
            ]
            run.status = "completed"
            run.adapter_ref = record.adapter_ref
            run.metrics = dict(metrics)
            run.artifact_refs = list(record.artifact_refs)
            self._write_record(record)
            return record
        except Exception as exc:
            record.status = "failed"
            record.error = f"{type(exc).__name__}: {exc}"
            record.training_verified = False
            run.status = "failed"
            self._write_record(record)
            raise TrainingBackendError(record.error) from exc

    def evaluate_checkpoint(self, run: ExperimentRun) -> Dict[str, Any]:
        state = self._states.get(run.experiment_id)
        if state is None:
            raise TrainingBackendError("unknown training run")
        record: TrainingRunRecord = state["record"]
        if record.status != "completed":
            raise TrainingBackendError("adapter is not complete")
        metrics = state["trainer"].evaluate(eval_dataset=state["dataset"])
        normalized = {
            key: float(value) if isinstance(value, (int, float)) else value
            for key, value in dict(metrics or {}).items()
        }
        loss = normalized.get("eval_loss")
        if isinstance(loss, float) and loss < 50:
            normalized["perplexity"] = math.exp(loss)
        normalized["evaluation_dataset"] = "train"
        return normalized

    def collect_artifacts(self, run: ExperimentRun) -> List[str]:
        state = self._states.get(run.experiment_id)
        if state is None:
            raise TrainingBackendError("unknown training run")
        return list(state["record"].artifact_refs)

    def cleanup(self, run: ExperimentRun) -> None:
        state: Optional[Dict[str, Any]] = self._states.pop(
            run.experiment_id, None
        )
        if state is not None:
            torch = state.get("torch")
            state.clear()
            if torch is not None and torch.cuda.is_available():
                torch.cuda.empty_cache()
