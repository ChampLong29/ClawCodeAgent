"""Command-facing orchestration for reproducible local Agent benchmarks."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Sequence, Union

from ..agent_runtime import LocalCodingAgent
from ..agent_types import AgentPermissions, ModelConfig
from ..api_config import APIConfigRuntime
from ..experiment.schemas import TaskSpec
from ..task_suite import TaskSuiteManifest
from ..verification import VerificationPolicy
from .local_agent_adapter import AgentFactory, LocalAgentBenchmarkAdapter
from .runner import BenchmarkConfig, BenchmarkError, BenchmarkRunResult, BenchmarkRunner


def _manifest_ref(manifest: TaskSuiteManifest) -> str:
    assert manifest.source_path is not None
    try:
        return manifest.source_path.relative_to(manifest.project_root).as_posix()
    except ValueError:
        return str(manifest.source_path)


def _select_test_tasks(
    manifest: TaskSuiteManifest,
    task_ids: Sequence[str],
    limit: Optional[int],
):
    tasks = manifest.tasks_for_split("test")
    requested = list(task_ids)
    if len(set(requested)) != len(requested):
        raise BenchmarkError("task_ids must be unique")
    if requested:
        known = {task.task_id for task in tasks}
        unknown = sorted(set(requested) - known)
        if unknown:
            raise BenchmarkError(
                f"requested tasks are not in the test split: {unknown}"
            )
        selected = set(requested)
        tasks = [task for task in tasks if task.task_id in selected]
    if limit is not None:
        if limit <= 0:
            raise BenchmarkError("limit must be positive")
        tasks = tasks[:limit]
    if not tasks:
        raise BenchmarkError("benchmark selection contains no test tasks")
    return tasks


def _oracle_allowed_paths(
    manifest: TaskSuiteManifest, task: TaskSpec
) -> Sequence[str]:
    """Derive the writable diff scope from a task's versioned oracle."""
    if not task.oracle_ref:
        raise BenchmarkError(
            f"task {task.task_id} requires oracle_ref or explicit allowed paths"
        )
    oracle = manifest.resolve_ref(task.oracle_ref)
    paths = sorted(
        path.relative_to(oracle).as_posix()
        for path in oracle.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(oracle).parts
    )
    if not paths:
        raise BenchmarkError(
            f"task {task.task_id} oracle contains no allowed files"
        )
    return paths


def run_local_benchmark(
    *,
    manifest_path: Union[str, Path],
    output_root: Union[str, Path],
    episodes_root: Optional[Union[str, Path]] = None,
    group_name: str = "base",
    model_ref: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    max_turns: int = 50,
    seed: int = 42,
    runtime_version: str = "local-agent-runtime.v1",
    prompt_version: str = "agent-system-prompt.v1",
    tool_version: str = "tool-schema.v1",
    verifier_version: str = "verifier-policy.v2",
    config_version: str = "benchmark-cli.v1",
    task_ids: Sequence[str] = (),
    limit: Optional[int] = None,
    allowed_path_patterns: Sequence[str] = (),
    dataset_manifest_ref: str = "",
    training_run_ref: str = "",
    experiment_ref: str = "",
    input_token_price_per_million: float = 0.0,
    output_token_price_per_million: float = 0.0,
    agent_factory: Optional[AgentFactory] = None,
) -> BenchmarkRunResult:
    """Run selected test tasks with a fixed, fully recorded protocol."""
    if not 0.0 <= temperature <= 2.0:
        raise BenchmarkError("temperature must be within [0, 2]")
    if max_tokens is not None and max_tokens <= 0:
        raise BenchmarkError("max_tokens must be positive")
    if max_turns <= 0:
        raise BenchmarkError("max_turns must be positive")
    if input_token_price_per_million < 0 or output_token_price_per_million < 0:
        raise BenchmarkError("token prices must be non-negative")
    patterns = [str(pattern) for pattern in allowed_path_patterns if str(pattern)]

    manifest = TaskSuiteManifest.load(manifest_path)
    tasks = _select_test_tasks(manifest, task_ids, limit)
    project_root = manifest.project_root
    resolved_model = model_ref or APIConfigRuntime(
        cwd=str(project_root)
    ).get_config().model
    if not str(resolved_model).strip():
        raise BenchmarkError("model_ref must not be empty")

    decoding_config: Dict[str, object] = {
        "temperature": temperature,
        "max_turns": max_turns,
    }
    if max_tokens is not None:
        decoding_config["max_tokens"] = max_tokens

    if agent_factory is None:
        def agent_factory(cwd: str, inference_config: Dict[str, object]):
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
    policy = VerificationPolicy(version=verifier_version)
    allowed_paths_resolver = (
        (lambda _task: list(patterns))
        if patterns
        else (lambda task: list(_oracle_allowed_paths(manifest, task)))
    )
    adapter = LocalAgentBenchmarkAdapter(
        episode_output,
        project_root=project_root,
        agent_factory=agent_factory,
        model_ref=str(resolved_model),
        runtime_version=runtime_version,
        prompt_version=prompt_version,
        tool_version=tool_version,
        config_version=config_version,
        verification_policy=policy,
        allowed_paths_resolver=allowed_paths_resolver,
        input_token_price=input_token_price_per_million / 1_000_000,
        output_token_price=output_token_price_per_million / 1_000_000,
    )
    config = BenchmarkConfig(
        group_name=group_name,
        model_ref=str(resolved_model),
        test_manifest_ref=_manifest_ref(manifest),
        decoding_config=decoding_config,
        tool_schema_version=tool_version,
        runtime_version=runtime_version,
        verifier_bundle_version=verifier_version,
        prompt_version=prompt_version,
        dataset_manifest_ref=dataset_manifest_ref,
        training_run_ref=training_run_ref,
        experiment_ref=experiment_ref,
        seed=seed,
    )
    return BenchmarkRunner(output).run(tasks, adapter, config)
