"""Collect one leakage-safe SWE-bench Lite Dev Episode."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union

from ..agent_runtime import LocalCodingAgent
from ..agent_types import AgentPermissions, BudgetConfig, ModelConfig
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


ENVIRONMENT_CONTRACT_SCHEMA_VERSION = "swe_bench_environment_contract.v2"


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
    candidates = [candidate]
    # Repositories commonly use a language suffix that is not part of the
    # import package (for example ``pvlib-python`` imports as ``pvlib``).
    for suffix in ("_python", "_py"):
        if candidate.endswith(suffix) and len(candidate) > len(suffix):
            candidates.append(candidate[: -len(suffix)])
    workspace = Path(cwd)
    for root in (workspace / "src", workspace):
        for import_name in candidates:
            if (root / import_name).is_dir() or (
                root / f"{import_name}.py"
            ).is_file():
                return import_name
    return None


def _probe_workspace_import(
    *,
    cwd: str,
    python_executable: Path,
    import_name: Optional[str],
    environment: Dict[str, str],
    required_modules: Sequence[str] = ("pytest",),
) -> Dict[str, Any]:
    """Fail closed when workspace imports or evaluator dependencies are invalid."""
    if import_name is None:
        raise BenchmarkError(
            "primary workspace import name could not be inferred"
        )
    script = (
        "import importlib,json,pathlib,sys;"
        "root=pathlib.Path(sys.argv[2]).resolve();"
        "module=importlib.import_module(sys.argv[1]);"
        "origin=pathlib.Path(module.__file__).resolve();"
        "relative=origin.relative_to(root);"
        "required={name:getattr(importlib.import_module(name),'__version__','unknown') "
        "for name in sys.argv[3:]};"
        "print(json.dumps({'python_version':sys.version.split()[0],"
        "'import_name':sys.argv[1],'module_file':relative.as_posix(),"
        "'required_modules':required}))"
    )
    modules = [str(item).strip() for item in required_modules if str(item).strip()]
    completed = subprocess.run(
        [str(python_executable), "-c", script, import_name, cwd, *modules],
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
            "historical Python environment does not import the Episode workspace "
            "or required evaluator modules: "
            + (completed.stderr.strip() or completed.stdout.strip())[:500]
        )
    payload = json.loads(completed.stdout)
    return {
        "schema_version": ENVIRONMENT_CONTRACT_SCHEMA_VERSION,
        "status": "passed",
        "python_version": payload["python_version"],
        "import_name": payload["import_name"],
        "module_file": payload["module_file"],
        "required_modules": payload["required_modules"],
        "pythonpath_layouts": ["src", "root"],
        "claim_boundary": (
            "Import-path and evaluator-dependency preflight only; not a "
            "task-quality result."
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
    thinking_mode: Optional[str] = None,
    max_turns: int = 50,
    max_total_tokens: int = 250000,
    completion_reminder_turns: int = 8,
    completion_critical_turns: int = 3,
    force_final_response_at_critical: bool = False,
    implementation_deadline_turns: int = 12,
    implementation_escalation_turns: int = 4,
    force_direct_mutation_after_escalation: bool = False,
    implementation_target_read_allowance: int = 0,
    implementation_constraint_repair_attempts: int = 0,
    reject_repeated_readonly_actions: bool = False,
    repeated_action_repair_attempts: int = 0,
    post_edit_contract_guidance: bool = True,
    timeout_seconds: float = 300.0,
    runtime_version: str = "local-agent-runtime.v1",
    prompt_version: str = "swe-bench-lite-dev.v2",
    tool_version: str = "tool-schema.v3",
    verifier_version: str = "verifier-policy.v3",
    config_version: str = "swe-bench-lite-collection.v1",
    allowed_path_patterns: Sequence[str] = (),
    input_token_price_per_million: float = 0.0,
    output_token_price_per_million: float = 0.0,
    agent_factory: Optional[AgentFactory] = None,
    model_client: Optional[Any] = None,
    agent_command_runner: Optional[Any] = None,
) -> TrainingEpisodeCollectionResult:
    """Run one Dev issue while keeping evaluator assets outside agent context."""
    if not str(generation_commit).strip():
        raise BenchmarkError("generation_commit must not be empty")
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
    if max_turns <= 0 or timeout_seconds <= 0:
        raise BenchmarkError("turn and timeout limits must be positive")
    if max_total_tokens <= 0:
        raise BenchmarkError("max_total_tokens must be positive")
    if completion_reminder_turns < 0:
        raise BenchmarkError("completion_reminder_turns must be non-negative")
    if completion_critical_turns < 0:
        raise BenchmarkError("completion_critical_turns must be non-negative")
    if implementation_deadline_turns < 0:
        raise BenchmarkError("implementation_deadline_turns must be non-negative")
    if implementation_escalation_turns < 0:
        raise BenchmarkError("implementation_escalation_turns must be non-negative")
    if implementation_escalation_turns > 0 and implementation_deadline_turns <= 0:
        raise BenchmarkError(
            "implementation_escalation_turns requires a positive "
            "implementation_deadline_turns"
        )
    if (
        force_direct_mutation_after_escalation
        and implementation_escalation_turns <= 0
    ):
        raise BenchmarkError(
            "force_direct_mutation_after_escalation requires a positive "
            "implementation_escalation_turns"
        )
    if implementation_target_read_allowance not in {0, 1}:
        raise BenchmarkError(
            "implementation_target_read_allowance must be 0 or 1"
        )
    if implementation_constraint_repair_attempts not in {0, 1}:
        raise BenchmarkError(
            "implementation_constraint_repair_attempts must be 0 or 1"
        )
    if repeated_action_repair_attempts not in {0, 1}:
        raise BenchmarkError(
            "repeated_action_repair_attempts must be 0 or 1"
        )
    if repeated_action_repair_attempts > 0 and not reject_repeated_readonly_actions:
        raise BenchmarkError(
            "repeated_action_repair_attempts requires "
            "reject_repeated_readonly_actions"
        )
    if (
        implementation_target_read_allowance > 0
        and not force_direct_mutation_after_escalation
    ):
        raise BenchmarkError(
            "implementation_target_read_allowance requires "
            "force_direct_mutation_after_escalation"
        )
    if (
        implementation_constraint_repair_attempts > 0
        and implementation_target_read_allowance != 1
    ):
        raise BenchmarkError(
            "implementation_constraint_repair_attempts requires "
            "implementation_target_read_allowance=1"
        )
    if (
        completion_reminder_turns > 0
        and completion_critical_turns > completion_reminder_turns
    ):
        raise BenchmarkError(
            "completion_critical_turns must not exceed completion_reminder_turns"
        )
    if force_final_response_at_critical and completion_critical_turns <= 0:
        raise BenchmarkError(
            "force_final_response_at_critical requires a positive "
            "completion_critical_turns"
        )
    patterns = [str(item) for item in allowed_path_patterns if str(item).strip()]
    if not patterns:
        raise BenchmarkError("SWE-bench collection requires explicit allowed paths")

    benchmark = SweBenchLiteDevAdapter(benchmark_root)
    tasks = {
        task.instance_id: task
        for task in benchmark.load_agent_tasks(instance_id=instance_id)
    }
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
        allowed_path_patterns=patterns,
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
        "max_total_tokens": max_total_tokens,
        "completion_reminder_turns": completion_reminder_turns,
        "completion_critical_turns": completion_critical_turns,
        "force_final_response_at_critical": force_final_response_at_critical,
        "implementation_deadline_turns": implementation_deadline_turns,
        "implementation_escalation_turns": implementation_escalation_turns,
        "force_direct_mutation_after_escalation": (
            force_direct_mutation_after_escalation
        ),
        "implementation_target_read_allowance": (
            implementation_target_read_allowance
        ),
        "implementation_constraint_repair_attempts": (
            implementation_constraint_repair_attempts
        ),
        "reject_repeated_readonly_actions": reject_repeated_readonly_actions,
        "repeated_action_repair_attempts": repeated_action_repair_attempts,
        "post_edit_contract_guidance": post_edit_contract_guidance,
        "implementation_path_patterns": patterns,
    }
    if max_tokens is not None:
        decoding_config["max_tokens"] = max_tokens
    if thinking_mode is not None:
        decoding_config["thinking_mode"] = thinking_mode

    configured_python = Path(python_executable).expanduser().absolute()
    original_factory = agent_factory
    if original_factory is None:
        def original_factory(cwd: str, inference_config: Dict[str, Any]):
            agent = LocalCodingAgent(
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
                    allowed_write_paths=list(patterns),
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
                budget=BudgetConfig(
                    max_total_tokens=int(inference_config["max_total_tokens"])
                ),
                completion_reminder_turns=int(
                    inference_config.get("completion_reminder_turns", 0)
                ),
                completion_critical_turns=int(
                    inference_config.get("completion_critical_turns", 0)
                ),
                force_final_response_at_critical=bool(
                    inference_config.get("force_final_response_at_critical", False)
                ),
                implementation_deadline_turns=int(
                    inference_config.get("implementation_deadline_turns", 0)
                ),
                implementation_escalation_turns=int(
                    inference_config.get("implementation_escalation_turns", 0)
                ),
                force_direct_mutation_after_escalation=bool(
                    inference_config.get(
                        "force_direct_mutation_after_escalation", False
                    )
                ),
                implementation_target_read_allowance=int(
                    inference_config.get(
                        "implementation_target_read_allowance", 0
                    )
                ),
                implementation_constraint_repair_attempts=int(
                    inference_config.get(
                        "implementation_constraint_repair_attempts", 0
                    )
                ),
                reject_repeated_readonly_actions=bool(
                    inference_config.get(
                        "reject_repeated_readonly_actions", False
                    )
                ),
                repeated_action_repair_attempts=int(
                    inference_config.get(
                        "repeated_action_repair_attempts", 0
                    )
                ),
                post_edit_contract_guidance=bool(
                    inference_config.get("post_edit_contract_guidance", False)
                ),
                implementation_path_patterns=tuple(
                    inference_config.get("implementation_path_patterns", ())
                ),
                command_runner=agent_command_runner,
            )
            if model_client is not None:
                agent.client = model_client
            return agent

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
        agent = original_factory(cwd, inference_config)
        if agent_command_runner is not None:
            setattr(agent, "command_runner", agent_command_runner)
        return agent

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
