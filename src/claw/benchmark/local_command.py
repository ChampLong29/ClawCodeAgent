"""Command-facing orchestration for reproducible local Agent benchmarks."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Sequence, Union

from ..agent_runtime import LocalCodingAgent
from ..agent_types import AgentPermissions, ModelConfig
from ..api_config import APIConfigRuntime
from ..container_runtime import OCIContainerConfig, OCIContainerRunner
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
    api_config_root: Optional[Union[str, Path]] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    thinking_mode: Optional[str] = None,
    max_turns: int = 50,
    reject_repeated_readonly_actions: bool = False,
    repeated_action_repair_attempts: int = 0,
    seed: int = 42,
    runtime_version: str = "local-agent-runtime.v1",
    prompt_version: str = "agent-system-prompt.v1",
    tool_version: str = "tool-schema.v3",
    verifier_version: str = "verifier-policy.v3",
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
    container_image: Optional[str] = None,
    container_engine: str = "auto",
    container_cpus: float = 2.0,
    container_memory: str = "4g",
    container_pids_limit: int = 256,
    container_user: Optional[str] = None,
) -> BenchmarkRunResult:
    """Run selected test tasks with a fixed, fully recorded protocol."""
    if not 0.0 <= temperature <= 2.0:
        raise BenchmarkError("temperature must be within [0, 2]")
    if max_tokens is not None and max_tokens <= 0:
        raise BenchmarkError("max_tokens must be positive")
    if thinking_mode is not None and thinking_mode not in {
        "auto", "enabled", "disabled"
    }:
        raise BenchmarkError(
            "thinking_mode must be one of auto, enabled, or disabled"
        )
    if max_turns <= 0:
        raise BenchmarkError("max_turns must be positive")
    if repeated_action_repair_attempts not in {0, 1}:
        raise BenchmarkError(
            "repeated_action_repair_attempts must be 0 or 1"
        )
    if repeated_action_repair_attempts and not reject_repeated_readonly_actions:
        raise BenchmarkError(
            "repeated_action_repair_attempts requires "
            "reject_repeated_readonly_actions"
        )
    if input_token_price_per_million < 0 or output_token_price_per_million < 0:
        raise BenchmarkError("token prices must be non-negative")
    patterns = [str(pattern) for pattern in allowed_path_patterns if str(pattern)]
    command_runner = None
    if container_image:
        command_runner = OCIContainerRunner(
            OCIContainerConfig(
                image=container_image,
                engine=container_engine,
                cpus=container_cpus,
                memory=container_memory,
                pids_limit=container_pids_limit,
                user=container_user,
            )
        )
        command_runner.probe()

    manifest = TaskSuiteManifest.load(manifest_path)
    tasks = _select_test_tasks(manifest, task_ids, limit)
    project_root = manifest.project_root
    config_root = Path(api_config_root).resolve() if api_config_root else project_root
    resolved_model = model_ref or APIConfigRuntime(
        cwd=str(config_root)
    ).get_config().model
    if not str(resolved_model).strip():
        raise BenchmarkError("model_ref must not be empty")

    decoding_config: Dict[str, object] = {
        "temperature": temperature,
        "max_turns": max_turns,
        "reject_repeated_readonly_actions": reject_repeated_readonly_actions,
        "repeated_action_repair_attempts": repeated_action_repair_attempts,
        "execution_backend": (
            command_runner.describe()
            if command_runner is not None
            else {"kind": "native"}
        ),
    }
    if max_tokens is not None:
        decoding_config["max_tokens"] = max_tokens
    if thinking_mode is not None:
        decoding_config["thinking_mode"] = thinking_mode

    if agent_factory is None:
        def agent_factory(cwd: str, inference_config: Dict[str, object]):
            return LocalCodingAgent(
                cwd=cwd,
                api_config_cwd=str(config_root),
                model_config=ModelConfig(
                    name=str(resolved_model),
                    temperature=float(inference_config["temperature"]),
                    max_tokens=(
                        int(inference_config["max_tokens"])
                        if inference_config.get("max_tokens") is not None
                        else None
                    ),
                    thinking_mode=inference_config.get("thinking_mode"),
                ),
                permissions=AgentPermissions(
                    allow_write=True,
                    allow_shell=True,
                    restrict_workspace=True,
                    allowed_tools=[
                        "list_dir",
                        "read_file",
                        "code_outline",
                        "write_file",
                        "edit_file",
                        "glob_search",
                        "grep_search",
                        "bash",
                    ],
                ).to_dict(),
                reject_repeated_readonly_actions=(
                    reject_repeated_readonly_actions
                ),
                repeated_action_repair_attempts=(
                    repeated_action_repair_attempts
                ),
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
        command_runner=command_runner,
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
