"""Fixed-protocol independent benchmark execution and artifact generation."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Protocol, Sequence, Union

from ..experiment.schemas import TaskSpec, canonical_hash
from ..training_backends.base import atomic_write_text
from .metrics import (
    BenchmarkEpisodeResult,
    bad_case_distribution,
    compute_metrics,
    cost_summary,
)


BENCHMARK_SCHEMA_VERSION = "benchmark_run.v1"
ABLATION_GROUPS = {"base", "raw_sft", "success_sft", "verifier_sft"}


class BenchmarkError(RuntimeError):
    """Raised when a benchmark would violate the comparison protocol."""


class ModelAdapter(Protocol):
    """Model/runtime adapter consumed by BenchmarkRunner."""

    def run(
        self, task: TaskSpec, inference_config: Dict[str, Any]
    ) -> Union[BenchmarkEpisodeResult, Dict[str, Any]]:
        ...


@dataclass
class BenchmarkConfig:
    group_name: str
    model_ref: str
    test_manifest_ref: str
    decoding_config: Dict[str, Any]
    tool_schema_version: str
    runtime_version: str
    verifier_bundle_version: str
    prompt_version: str = "unknown"
    dataset_manifest_ref: str = ""
    training_run_ref: str = ""
    experiment_ref: str = ""
    seed: int = 42

    def validate(self) -> None:
        if self.group_name not in ABLATION_GROUPS:
            raise BenchmarkError(
                f"group_name must be one of {sorted(ABLATION_GROUPS)}"
            )
        for name in (
            "model_ref",
            "test_manifest_ref",
            "tool_schema_version",
            "runtime_version",
            "verifier_bundle_version",
            "prompt_version",
        ):
            if not str(getattr(self, name)).strip():
                raise BenchmarkError(f"{name} must not be empty")
        if self.seed < 0:
            raise BenchmarkError("seed must be non-negative")
        if self.group_name != "base":
            for name in (
                "dataset_manifest_ref",
                "training_run_ref",
                "experiment_ref",
            ):
                if not str(getattr(self, name)).strip():
                    raise BenchmarkError(
                        f"trained benchmark group requires {name}"
                    )

    @property
    def protocol_fingerprint(self) -> str:
        self.validate()
        return canonical_hash(
            {
                "test_manifest_ref": self.test_manifest_ref,
                "decoding_config": self.decoding_config,
                "tool_schema_version": self.tool_schema_version,
                "runtime_version": self.runtime_version,
                "verifier_bundle_version": self.verifier_bundle_version,
                "prompt_version": self.prompt_version,
                "seed": self.seed,
            }
        )

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["protocol_fingerprint"] = self.protocol_fingerprint
        return data


@dataclass
class BenchmarkRunResult:
    run_id: str
    config: BenchmarkConfig
    task_ids: List[str]
    metrics: Dict[str, Any]
    episodes: List[BenchmarkEpisodeResult]
    output_refs: List[str] = field(default_factory=list)
    schema_version: str = BENCHMARK_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != BENCHMARK_SCHEMA_VERSION:
            raise BenchmarkError(
                f"unsupported benchmark schema: {self.schema_version}"
            )
        self.config.validate()
        if len(set(self.task_ids)) != len(self.task_ids):
            raise BenchmarkError("benchmark task_ids must be unique")
        if self.task_ids != [episode.task_id for episode in self.episodes]:
            raise BenchmarkError("task_ids must match episode order")
        for episode in self.episodes:
            episode.validate()

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "config": self.config.to_dict(),
            "task_ids": list(self.task_ids),
            "metrics": self.metrics,
            "episodes": [episode.to_dict() for episode in self.episodes],
            "output_refs": list(self.output_refs),
        }


def _jsonl(items: Sequence[Dict[str, Any]]) -> str:
    return "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
        for item in items
    )


class BenchmarkRunner:
    """Execute one group against an immutable test-only task set."""

    def __init__(self, output_root: Union[str, Path]):
        self.output_root = Path(output_root).resolve()

    def run(
        self,
        tasks: Sequence[TaskSpec],
        adapter: ModelAdapter,
        config: BenchmarkConfig,
    ) -> BenchmarkRunResult:
        config.validate()
        if not tasks:
            raise BenchmarkError("benchmark requires at least one test task")
        seen_ids = set()
        seen_families = set()
        for task in tasks:
            task.validate()
            if task.split != "test":
                raise BenchmarkError(
                    f"benchmark task {task.task_id} is not in the test split"
                )
            if task.task_id in seen_ids:
                raise BenchmarkError(f"duplicate benchmark task: {task.task_id}")
            if task.family_id in seen_families:
                raise BenchmarkError(
                    f"duplicate test family in benchmark: {task.family_id}"
                )
            seen_ids.add(task.task_id)
            seen_families.add(task.family_id)

        episodes: List[BenchmarkEpisodeResult] = []
        for task in tasks:
            try:
                raw = adapter.run(task, dict(config.decoding_config))
                episode = (
                    raw
                    if isinstance(raw, BenchmarkEpisodeResult)
                    else BenchmarkEpisodeResult.from_dict(raw)
                )
                if episode.task_id != task.task_id:
                    raise BenchmarkError(
                        "model adapter returned a result for the wrong task"
                    )
                for name in ("family_id", "domain", "difficulty"):
                    if getattr(episode, name) != getattr(task, name):
                        raise BenchmarkError(
                            f"model adapter returned mismatched {name}"
                        )
            except Exception as exc:
                episode = BenchmarkEpisodeResult(
                    task_id=task.task_id,
                    family_id=task.family_id,
                    domain=task.domain,
                    difficulty=task.difficulty,
                    success=False,
                    test_pass_rate=0.0,
                    tool_calls=0,
                    valid_tool_selections=0,
                    valid_tool_arguments=0,
                    format_valid=False,
                    process_violations=0,
                    turns=0,
                    input_tokens=0,
                    output_tokens=0,
                    token_cost=0.0,
                    latency_seconds=0.0,
                    bad_cases=["environment_or_infra"],
                    error=f"{type(exc).__name__}: {exc}",
                )
            episode.validate()
            episodes.append(episode)

        metrics = compute_metrics(episodes)
        task_ids = [task.task_id for task in tasks]
        run_id = "benchmark_" + canonical_hash(
            {
                "group": config.group_name,
                "model": config.model_ref,
                "protocol": config.protocol_fingerprint,
                "task_ids": task_ids,
                "episodes": [episode.to_dict() for episode in episodes],
            }
        )[:20]
        output = self.output_root / re.sub(
            r"[^a-z0-9_-]+", "-", config.group_name.lower()
        )
        output.mkdir(parents=True, exist_ok=True)
        refs = [
            "results.jsonl",
            "metrics.json",
            "bad-case-distribution.json",
            "cost-summary.json",
            "benchmark-run.json",
        ]
        result = BenchmarkRunResult(
            run_id=run_id,
            config=config,
            task_ids=task_ids,
            metrics=metrics,
            episodes=episodes,
            output_refs=refs,
        )
        atomic_write_text(
            output / "results.jsonl",
            _jsonl([episode.to_dict() for episode in episodes]),
        )
        atomic_write_text(
            output / "metrics.json",
            json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        atomic_write_text(
            output / "bad-case-distribution.json",
            json.dumps(
                bad_case_distribution(episodes),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        atomic_write_text(
            output / "cost-summary.json",
            json.dumps(
                cost_summary(episodes),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        atomic_write_text(
            output / "benchmark-run.json",
            json.dumps(
                result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
            )
            + "\n",
        )
        return result
