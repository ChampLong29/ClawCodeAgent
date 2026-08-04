"""Run a tokenizer-only smoke test through LlamaFactory's native SFT loader.

This tool deliberately avoids importing Claw internals.  It must run in an
environment where the pinned LlamaFactory source and its dependencies are on
``PYTHONPATH``.  Only tokenizer artifacts are downloaded; model weights are
never loaded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config = _read_json(args.config.resolve())
    dataset_dir = args.dataset_dir.resolve()
    output_dir = args.output_dir.resolve()
    tokenized_path = output_dir / "tokenized-dataset"
    trainer_output = output_dir / "trainer-output-unused"
    cache_dir = args.cache_dir.resolve() if args.cache_dir else output_dir / "hf-cache"

    dataset_info = dataset_dir / "dataset_info.json"
    if not dataset_info.is_file():
        raise FileNotFoundError(dataset_info)
    dataset_name = str(config["dataset_name"])
    dataset_catalog = _read_json(dataset_info)
    dataset_entry = dataset_catalog.get(dataset_name)
    if not isinstance(dataset_entry, dict) or not isinstance(
        dataset_entry.get("file_name"), str
    ):
        raise ValueError(f"Dataset {dataset_name!r} is missing a file_name entry")
    dataset_file = (dataset_dir / dataset_entry["file_name"]).resolve()
    if dataset_dir not in dataset_file.parents or not dataset_file.is_file():
        raise ValueError(f"Dataset file is missing or escapes dataset_dir: {dataset_file}")

    # Imports stay inside main so --help works without the optional stack.
    import accelerate
    import datasets
    import llamafactory
    import peft
    import torch
    import transformers
    from transformers import AutoTokenizer, Seq2SeqTrainingArguments

    from llamafactory.data import get_dataset, get_template_and_fix_tokenizer
    from llamafactory.hparams import DataArguments, ModelArguments

    expected_version = str(config["llamafactory_version"])
    if llamafactory.__version__ != expected_version:
        raise RuntimeError(
            f"LlamaFactory version mismatch: {llamafactory.__version__} != {expected_version}"
        )

    model_name = str(config["model_name_or_path"])
    revision = str(config["model_revision"])
    tokenizer = AutoTokenizer.from_pretrained(
        model_name,
        revision=revision,
        cache_dir=str(cache_dir),
        use_fast=True,
        padding_side="right",
        trust_remote_code=False,
    )

    data_args = DataArguments(
        template=str(config["template"]),
        dataset=dataset_name,
        dataset_dir=str(dataset_dir),
        cutoff_len=int(config["cutoff_len"]),
        max_samples=int(config["max_samples"]),
        preprocessing_num_workers=int(config["preprocessing_num_workers"]),
        overwrite_cache=True,
        tokenized_path=str(tokenized_path),
    )
    model_args = ModelArguments(
        model_name_or_path=model_name,
        model_revision=revision,
        cache_dir=str(cache_dir),
        trust_remote_code=False,
    )
    training_args = Seq2SeqTrainingArguments(
        output_dir=str(trainer_output),
        do_train=True,
        per_device_train_batch_size=1,
        report_to=[],
    )
    template = get_template_and_fix_tokenizer(tokenizer, data_args)
    dataset_module = get_dataset(
        template=template,
        model_args=model_args,
        data_args=data_args,
        training_args=training_args,
        stage="sft",
        tokenizer=tokenizer,
        processor=None,
    )

    train_dataset = dataset_module.get("train_dataset")
    if train_dataset is None or len(train_dataset) == 0:
        raise RuntimeError("LlamaFactory produced no train samples")

    lengths = [len(row["input_ids"]) for row in train_dataset]
    supervised = [sum(1 for token in row["labels"] if token != -100) for row in train_dataset]
    if any(count == 0 for count in supervised):
        raise RuntimeError("At least one tokenized sample has no supervised labels")

    output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": "llamafactory_tokenization_smoke_result.v1",
        "status": "passed",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "runtime": {
            "llamafactory": llamafactory.__version__,
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "datasets": datasets.__version__,
            "accelerate": accelerate.__version__,
            "peft": peft.__version__,
            "cuda_available": torch.cuda.is_available(),
            "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        },
        "input": {
            "dataset_info_sha256": _sha256(dataset_info),
            "dataset_sha256": _sha256(dataset_file),
        },
        "output": {
            "sample_count": len(train_dataset),
            "columns": sorted(train_dataset.column_names),
            "token_lengths": lengths,
            "supervised_token_counts": supervised,
            "truncated_sample_count": sum(
                1 for length in lengths if length >= int(config["cutoff_len"])
            ),
            "tokenized_path": "tokenized-dataset",
        },
        "claim_boundary": "Tokenizer and native SFT data-loader compatibility only; no weights were trained.",
    }
    result_path = output_dir / "tokenization-smoke-result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
