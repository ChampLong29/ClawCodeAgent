"""Collect verified Train/Dev Episodes without weakening Benchmark isolation."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from ..agent_runtime import LocalCodingAgent
from ..agent_types import AgentPermissions, ModelConfig
from ..api_config import APIConfigRuntime
from ..benchmark.local_agent_adapter import (
    AgentFactory,
    LocalAgentBenchmarkAdapter,
)
from ..benchmark.metrics import BenchmarkEpisodeResult, compute_metrics
from ..benchmark.runner import BenchmarkError
from ..experiment.schemas import TaskSpec, canonical_hash, utc_now
from ..task_suite import TaskSuiteManifest
from ..trajectory.schema import stable_id
from ..verification import VerificationPolicy


TRAINING_COLLECTION_SCHEMA_VERSION = "training_episode_collection.v1"


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


def _select_collection_tasks(
    manifest: TaskSuiteManifest,
    *,
    split: str,
    task_ids: Sequence[str],
    limit: Optional[int],
) -> List[TaskSpec]:
    if split not in {"train", "dev"}:
        raise BenchmarkError("training collection split must be train or dev")
    tasks = manifest.tasks_for_split(split)
    requested = list(task_ids)
    if len(set(requested)) != len(requested):
        raise BenchmarkError("task_ids must be unique")
    if requested:
        known = {task.task_id for task in tasks}
        unknown = sorted(set(requested) - known)
        if unknown:
            raise BenchmarkError(
                f"requested tasks are not in the {split} split: {unknown}"
            )
        selected = set(requested)
        tasks = [task for task in tasks if task.task_id in selected]
    if limit is not None:
        if limit <= 0:
            raise BenchmarkError("limit must be positive")
        tasks = tasks[:limit]
    if not tasks:
        raise BenchmarkError(f"collection contains no {split} tasks")
    return tasks


def _allowed_paths(manifest: TaskSuiteManifest, task: TaskSpec) -> List[str]:
    if not task.oracle_ref:
        raise BenchmarkError(
            f"task {task.task_id} requires oracle_ref for diff-scope verification"
        )
    oracle = manifest.resolve_ref(task.oracle_ref)
    paths = sorted(
        path.relative_to(oracle).as_posix()
        for path in oracle.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(oracle).parts
    )
    if not paths:
        raise BenchmarkError(f"task {task.task_id} oracle contains no allowed files")
    return paths


class LocalAgentTrainingAdapter(LocalAgentBenchmarkAdapter):
    """Episode adapter that accepts Train/Dev and explicitly rejects Test."""

    def __init__(self, *args: Any, **kwargs: Any):
        kwargs.setdefault("episode_prefix", "training")
        super().__init__(*args, **kwargs)

    def run(
        self,
        task: TaskSpec,
        inference_config: Dict[str, Any],
    ) -> BenchmarkEpisodeResult:
        task.validate()
        if task.split not in {"train", "dev"}:
            raise BenchmarkError("training adapter rejects test tasks")
        return self._run_verified_episode(task, inference_config)


@dataclass
class TrainingEpisodeCollectionManifest:
    collection_id: str
    suite_id: str
    suite_version: str
    suite_content_hash: str
    suite_ref: str
    split: str
    task_ids: List[str]
    episode_refs: List[str]
    trajectory_refs: List[str]
    verification_refs: List[str]
    generation_commit: str
    model_ref: str
    decoding_config: Dict[str, Any]
    runtime_version: str
    prompt_version: str
    tool_version: str
    verifier_version: str
    result_content_hash: str
    metrics: Dict[str, Any]
    generated_at: str = field(default_factory=utc_now)
    schema_version: str = TRAINING_COLLECTION_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != TRAINING_COLLECTION_SCHEMA_VERSION:
            raise ValueError(f"unsupported collection schema: {self.schema_version}")
        if self.split not in {"train", "dev"}:
            raise ValueError("collection split must be train or dev")
        for name in (
            "collection_id",
            "suite_id",
            "suite_version",
            "suite_content_hash",
            "suite_ref",
            "generation_commit",
            "model_ref",
            "runtime_version",
            "prompt_version",
            "tool_version",
            "verifier_version",
            "result_content_hash",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        count = len(self.task_ids)
        if not count:
            raise ValueError("collection must contain at least one task")
        if len(set(self.task_ids)) != count:
            raise ValueError("collection task_ids must be unique")
        for name in ("episode_refs", "trajectory_refs", "verification_refs"):
            values = getattr(self, name)
            if len(values) != count or len(set(values)) != count:
                raise ValueError(f"{name} must contain one unique ref per task")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)


@dataclass
class TrainingEpisodeCollectionResult:
    manifest: TrainingEpisodeCollectionManifest
    episodes: List[BenchmarkEpisodeResult]
    manifest_path: Path
    results_path: Path


def collect_local_training_episodes(
    *,
    manifest_path: Union[str, Path],
    output_root: Union[str, Path],
    generation_commit: str,
    split: str = "train",
    episodes_root: Optional[Union[str, Path]] = None,
    model_ref: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    max_turns: int = 50,
    runtime_version: str = "local-agent-runtime.v1",
    prompt_version: str = "agent-system-prompt.v1",
    tool_version: str = "tool-schema.v3",
    verifier_version: str = "verifier-policy.v3",
    config_version: str = "training-collection.v1",
    task_ids: Sequence[str] = (),
    limit: Optional[int] = None,
    input_token_price_per_million: float = 0.0,
    output_token_price_per_million: float = 0.0,
    agent_factory: Optional[AgentFactory] = None,
) -> TrainingEpisodeCollectionResult:
    """Collect a small, auditable Train/Dev batch for Silver materialization."""
    if not str(generation_commit).strip():
        raise BenchmarkError("generation_commit must not be empty")
    if not 0.0 <= temperature <= 2.0:
        raise BenchmarkError("temperature must be within [0, 2]")
    if max_tokens is not None and max_tokens <= 0:
        raise BenchmarkError("max_tokens must be positive")
    if max_turns <= 0:
        raise BenchmarkError("max_turns must be positive")
    if input_token_price_per_million < 0 or output_token_price_per_million < 0:
        raise BenchmarkError("token prices must be non-negative")

    suite = TaskSuiteManifest.load(manifest_path)
    tasks = _select_collection_tasks(
        suite, split=split, task_ids=task_ids, limit=limit
    )
    project_root = suite.project_root
    resolved_model = model_ref or APIConfigRuntime(
        cwd=str(project_root)
    ).get_config().model
    if not str(resolved_model).strip():
        raise BenchmarkError("model_ref must not be empty")
    decoding_config: Dict[str, Any] = {
        "temperature": temperature,
        "max_turns": max_turns,
    }
    if max_tokens is not None:
        decoding_config["max_tokens"] = max_tokens

    if agent_factory is None:
        def agent_factory(cwd: str, inference_config: Dict[str, Any]):
            return LocalCodingAgent(
                cwd=cwd,
                api_config_cwd=str(project_root),
                model_config=ModelConfig(
                    name=str(resolved_model),
                    temperature=float(inference_config["temperature"]),
                    max_tokens=(
                        int(inference_config["max_tokens"])
                        if inference_config.get("max_tokens") is not None
                        else None
                    ),
                ),
                permissions=AgentPermissions(
                    allow_write=True,
                    allow_shell=True,
                ).to_dict(),
            )

    output = Path(output_root).resolve()
    episode_output = Path(episodes_root or (output / "episodes")).resolve()
    adapter = LocalAgentTrainingAdapter(
        episode_output,
        project_root=project_root,
        agent_factory=agent_factory,
        model_ref=str(resolved_model),
        runtime_version=runtime_version,
        prompt_version=prompt_version,
        tool_version=tool_version,
        config_version=config_version,
        verification_policy=VerificationPolicy(version=verifier_version),
        allowed_paths_resolver=lambda task: _allowed_paths(suite, task),
        input_token_price=input_token_price_per_million / 1_000_000,
        output_token_price=output_token_price_per_million / 1_000_000,
    )
    results = [adapter.run(task, dict(decoding_config)) for task in tasks]
    payloads = [result.to_dict() for result in results]
    result_content_hash = canonical_hash(payloads)
    episode_refs = [str(Path(result.trajectory_ref or "").parent) for result in results]
    assert suite.source_path is not None
    try:
        suite_ref = suite.source_path.relative_to(project_root).as_posix()
    except ValueError:
        suite_ref = str(suite.source_path)
    manifest = TrainingEpisodeCollectionManifest(
        collection_id=stable_id(
            "episode_collection",
            {
                "suite_content_hash": suite.content_hash,
                "split": split,
                "task_ids": [task.task_id for task in tasks],
                "result_content_hash": result_content_hash,
                "generation_commit": generation_commit,
            },
        ),
        suite_id=suite.suite_id,
        suite_version=suite.version,
        suite_content_hash=suite.content_hash,
        suite_ref=suite_ref,
        split=split,
        task_ids=[task.task_id for task in tasks],
        episode_refs=episode_refs,
        trajectory_refs=[str(result.trajectory_ref) for result in results],
        verification_refs=[str(result.verification_ref) for result in results],
        generation_commit=generation_commit,
        model_ref=str(resolved_model),
        decoding_config=decoding_config,
        runtime_version=runtime_version,
        prompt_version=prompt_version,
        tool_version=tool_version,
        verifier_version=verifier_version,
        result_content_hash=result_content_hash,
        metrics=compute_metrics(results),
    )
    results_path = output / "episode-results.jsonl"
    manifest_path_out = output / "collection-manifest.json"
    _atomic_write(
        results_path,
        "".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
            for item in payloads
        ),
    )
    _atomic_write(
        manifest_path_out,
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
    )
    return TrainingEpisodeCollectionResult(
        manifest=manifest,
        episodes=results,
        manifest_path=manifest_path_out,
        results_path=results_path,
    )
