"""Collect one leakage-safe SWE-bench Lite Dev Episode."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union

from ..agent_runtime import LocalCodingAgent
from ..agent_types import AgentPermissions, ModelConfig
from ..api_config import APIConfigRuntime
from ..benchmark import (
    SweBenchLiteDevAdapter,
    SweBenchLiteEpisodeTaskMaterializer,
)
from ..benchmark.local_agent_adapter import AgentFactory
from ..benchmark.metrics import compute_metrics
from ..benchmark.runner import BenchmarkError
from ..experiment.schemas import canonical_hash
from ..trajectory.schema import stable_id
from ..verification import VerificationPolicy
from .collection import (
    LocalAgentTrainingAdapter,
    TrainingEpisodeCollectionManifest,
    TrainingEpisodeCollectionResult,
    _atomic_write,
)


ENVIRONMENT_CONTRACT_SCHEMA_VERSION = "swe_bench_environment_contract.v1"


def _workspace_pythonpath(cwd: str, existing: str = "") -> str:
    """Return a deterministic Python path for src-layout and root-layout repos."""
    workspace = Path(cwd).resolve()
    entries = []
    source = workspace / "src"
    if source.is_dir():
        entries.append(str(source))
    entries.append(str(workspace))
    entries.extend(
        item for item in existing.split(os.pathsep) if item and item not in entries
    )
    return os.pathsep.join(entries)


def _infer_workspace_import_name(repo: str, cwd: str) -> Optional[str]:
    """Infer the primary import package without importing evaluator assets."""
    candidate = repo.rsplit("/", 1)[-1].replace("-", "_").strip()
    if not candidate:
        return None
    workspace = Path(cwd)
    for root in (workspace / "src", workspace):
        if (root / candidate).is_dir() or (root / f"{candidate}.py").is_file():
            return candidate
    return None


def _probe_workspace_import(
    *,
    cwd: str,
    python_executable: Path,
    import_name: Optional[str],
    environment: Dict[str, str],
) -> Dict[str, Any]:
    """Fail closed when the configured interpreter imports another checkout."""
    if import_name is None:
        return {
            "schema_version": ENVIRONMENT_CONTRACT_SCHEMA_VERSION,
            "status": "skipped",
            "reason": "primary_import_name_not_inferred",
        }
    script = (
        "import importlib,json,pathlib,sys;"
        "root=pathlib.Path(sys.argv[2]).resolve();"
        "module=importlib.import_module(sys.argv[1]);"
        "origin=pathlib.Path(module.__file__).resolve();"
        "relative=origin.relative_to(root);"
        "print(json.dumps({'python_version':sys.version.split()[0],"
        "'import_name':sys.argv[1],'module_file':relative.as_posix()}))"
    )
    completed = subprocess.run(
        [str(python_executable), "-c", script, import_name, cwd],
        cwd=cwd,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise BenchmarkError(
            "historical Python environment does not import the Episode workspace: "
            + (completed.stderr.strip() or completed.stdout.strip())[:500]
        )
    payload = json.loads(completed.stdout)
    return {
        "schema_version": ENVIRONMENT_CONTRACT_SCHEMA_VERSION,
        "status": "passed",
        "python_version": payload["python_version"],
        "import_name": payload["import_name"],
        "module_file": payload["module_file"],
        "pythonpath_layouts": ["src", "root"],
        "claim_boundary": (
            "Import-path compatibility preflight only; not a task-quality result."
        ),
    }


def collect_swe_bench_lite_dev_episode(
    *,
    benchmark_root: Union[str, Path],
    instance_id: str,
    python_executable: Union[str, Path],
    evaluator_script: Union[str, Path],
    output_root: Union[str, Path],
    generation_commit: str,
    api_config_root: Optional[Union[str, Path]] = None,
    model_ref: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    max_turns: int = 50,
    completion_reminder_turns: int = 8,
    completion_critical_turns: int = 3,
    timeout_seconds: float = 300.0,
    runtime_version: str = "local-agent-runtime.v1",
    prompt_version: str = "swe-bench-lite-dev.v1",
    tool_version: str = "tool-schema.v1",
    verifier_version: str = "verifier-policy.v1",
    config_version: str = "swe-bench-lite-collection.v1",
    allowed_path_patterns: Sequence[str] = (),
    input_token_price_per_million: float = 0.0,
    output_token_price_per_million: float = 0.0,
    agent_factory: Optional[AgentFactory] = None,
) -> TrainingEpisodeCollectionResult:
    """Run one Dev issue while keeping evaluator assets outside agent context."""
    if not str(generation_commit).strip():
        raise BenchmarkError("generation_commit must not be empty")
    if not 0.0 <= temperature <= 2.0:
        raise BenchmarkError("temperature must be within [0, 2]")
    if max_tokens is not None and max_tokens <= 0:
        raise BenchmarkError("max_tokens must be positive")
    if max_turns <= 0 or timeout_seconds <= 0:
        raise BenchmarkError("turn and timeout limits must be positive")
    if completion_reminder_turns < 0:
        raise BenchmarkError("completion_reminder_turns must be non-negative")
    if completion_critical_turns < 0:
        raise BenchmarkError("completion_critical_turns must be non-negative")
    if (
        completion_reminder_turns > 0
        and completion_critical_turns > completion_reminder_turns
    ):
        raise BenchmarkError(
            "completion_critical_turns must not exceed completion_reminder_turns"
        )
    patterns = [str(item) for item in allowed_path_patterns if str(item).strip()]
    if not patterns:
        raise BenchmarkError("SWE-bench collection requires explicit allowed paths")

    benchmark = SweBenchLiteDevAdapter(benchmark_root)
    tasks = {task.instance_id: task for task in benchmark.load_agent_tasks()}
    if instance_id not in tasks:
        raise BenchmarkError(f"instance is not in selected pilot: {instance_id}")
    public_task = tasks[instance_id]
    private_bundle = benchmark.load_evaluation_bundle(instance_id)
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    materialized = SweBenchLiteEpisodeTaskMaterializer(
        evaluator_script
    ).materialize(
        public_task,
        private_bundle,
        output_root=output / "materialized",
        python_executable=python_executable,
        timeout_seconds=timeout_seconds,
    )
    task = materialized.task_spec
    config_root = Path(api_config_root or Path.cwd()).resolve()
    resolved_model = model_ref or APIConfigRuntime(
        cwd=str(config_root)
    ).get_config().model
    if not str(resolved_model).strip():
        raise BenchmarkError("model_ref must not be empty")
    decoding_config: Dict[str, Any] = {
        "temperature": temperature,
        "max_turns": max_turns,
        "completion_reminder_turns": completion_reminder_turns,
        "completion_critical_turns": completion_critical_turns,
    }
    if max_tokens is not None:
        decoding_config["max_tokens"] = max_tokens

    configured_python = Path(python_executable).expanduser().absolute()
    original_factory = agent_factory
    if original_factory is None:
        def original_factory(cwd: str, inference_config: Dict[str, Any]):
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
                ),
                permissions=AgentPermissions(
                    allow_write=True,
                    allow_shell=True,
                    restrict_workspace=True,
                ).to_dict(),
                completion_reminder_turns=int(
                    inference_config.get("completion_reminder_turns", 0)
                ),
                completion_critical_turns=int(
                    inference_config.get("completion_critical_turns", 0)
                ),
            )

    environment_before = {
        name: os.environ.get(name) for name in ("PATH", "PYTHONPATH", "VIRTUAL_ENV")
    }

    environment_contract: Dict[str, Any] = {
        "schema_version": ENVIRONMENT_CONTRACT_SCHEMA_VERSION,
        "status": "not_run",
    }

    def configured_agent_factory(cwd: str, inference_config: Dict[str, Any]):
        nonlocal environment_contract
        python_bin = str(configured_python.parent)
        existing_path = environment_before["PATH"] or ""
        os.environ["PATH"] = (
            python_bin + os.pathsep + existing_path if existing_path else python_bin
        )
        existing_pythonpath = environment_before["PYTHONPATH"] or ""
        os.environ["PYTHONPATH"] = _workspace_pythonpath(
            cwd, existing_pythonpath
        )
        os.environ["VIRTUAL_ENV"] = str(configured_python.parent.parent)
        try:
            environment_contract = _probe_workspace_import(
                cwd=cwd,
                python_executable=configured_python,
                import_name=_infer_workspace_import_name(public_task.repo, cwd),
                environment=dict(os.environ),
            )
        except Exception as exc:
            environment_contract = {
                "schema_version": ENVIRONMENT_CONTRACT_SCHEMA_VERSION,
                "status": "failed",
                "error_type": type(exc).__name__,
                "reason": str(exc)[:500],
                "claim_boundary": (
                    "Import-path compatibility preflight only; not a "
                    "task-quality result."
                ),
            }
            raise
        assert original_factory is not None
        return original_factory(cwd, inference_config)

    adapter = LocalAgentTrainingAdapter(
        output / "episodes",
        project_root=config_root,
        agent_factory=configured_agent_factory,
        model_ref=str(resolved_model),
        runtime_version=runtime_version,
        prompt_version=prompt_version,
        tool_version=tool_version,
        config_version=config_version,
        verification_policy=VerificationPolicy(version=verifier_version),
        allowed_paths_resolver=lambda _task: list(patterns),
        input_token_price=input_token_price_per_million / 1_000_000,
        output_token_price=output_token_price_per_million / 1_000_000,
    )
    try:
        result = adapter.run(task, dict(decoding_config))
    finally:
        _atomic_write(
            output / "environment-contract.json",
            json.dumps(
                environment_contract,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        for name, value in environment_before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    payload = result.to_dict()
    result_content_hash = canonical_hash([payload])
    suite_content_hash = canonical_hash(
        {
            "agent_task": public_task.to_agent_payload(),
            "evaluator_fingerprint": materialized.evaluator_fingerprint,
            "allowed_path_patterns": patterns,
        }
    )
    episode_ref = str(Path(result.trajectory_ref or "").parent)
    manifest = TrainingEpisodeCollectionManifest(
        collection_id=stable_id(
            "episode_collection",
            {
                "suite_content_hash": suite_content_hash,
                "split": "dev",
                "task_ids": [task.task_id],
                "result_content_hash": result_content_hash,
                "generation_commit": generation_commit,
            },
        ),
        suite_id="swe-bench-lite-dev-pilot",
        suite_version=public_task.dataset_revision,
        suite_content_hash=suite_content_hash,
        suite_ref=str(Path(benchmark_root).resolve() / "pilot-agent-inputs.json"),
        split="dev",
        task_ids=[task.task_id],
        episode_refs=[episode_ref],
        trajectory_refs=[str(result.trajectory_ref)],
        verification_refs=[str(result.verification_ref)],
        generation_commit=generation_commit,
        model_ref=str(resolved_model),
        decoding_config=decoding_config,
        runtime_version=runtime_version,
        prompt_version=prompt_version,
        tool_version=tool_version,
        verifier_version=verifier_version,
        result_content_hash=result_content_hash,
        metrics=compute_metrics([result]),
    )
    results_path = output / "episode-results.jsonl"
    manifest_path = output / "collection-manifest.json"
    _atomic_write(
        results_path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
    )
    _atomic_write(
        manifest_path,
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
    )
    _atomic_write(
        output / "evaluator-fingerprint.json",
        json.dumps(
            {
                "schema_version": "swe_bench_lite_episode_context.v1",
                "instance_id": instance_id,
                "dataset_revision": public_task.dataset_revision,
                "agent_input_hash": canonical_hash(public_task.to_agent_payload()),
                "evaluator_fingerprint": materialized.evaluator_fingerprint,
                "allowed_path_patterns": patterns,
                "official_swebench_harness": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    _atomic_write(
        output / "task-spec.json",
        json.dumps(
            task.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
        )
        + "\n",
    )
    return TrainingEpisodeCollectionResult(
        manifest=manifest,
        episodes=[result],
        manifest_path=manifest_path,
        results_path=results_path,
    )
