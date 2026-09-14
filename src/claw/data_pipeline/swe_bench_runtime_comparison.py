"""Controlled local SWE-bench Lite comparison for Claw and Pi RPC."""

from __future__ import annotations

import json
import os
import re
from functools import partial
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union

from ..agent_runtime import LocalCodingAgent
from ..agent_types import AgentPermissions, BudgetConfig, ModelConfig
from ..api_config import APIConfigRuntime
from ..benchmark.metrics import BenchmarkEpisodeResult
from ..benchmark.pi_rpc_adapter import PiDockerRpcClient, PiRpcAgent
from ..benchmark.runner import BenchmarkError
from ..experiment.schemas import canonical_hash
from ..openai_compat import OpenAICompatClient
from ..trajectory.schema import stable_id
from .collection import (
    TrainingEpisodeCollectionManifest,
    TrainingEpisodeCollectionResult,
    _atomic_write,
)
from .swe_bench_collection import collect_swe_bench_lite_dev_episode


SWE_BENCH_RUNTIME_COMPARISON_SCHEMA_VERSION = (
    "swe_bench_lite_runtime_comparison.v2"
)
SWE_BENCH_RUNTIME_ABLATION_SCHEMA_VERSION = (
    "swe_bench_lite_runtime_ablation.v1"
)
DEFAULT_PI_COMPARISON_ARGS = (
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-context-files",
    "--no-approve",
    "--offline",
    "--thinking",
    "off",
)
_CLAW_ABLATION_PROFILES = {
    "base": {
        "completion_reminder_turns": 0,
        "completion_critical_turns": 0,
        "implementation_deadline_turns": 0,
        "implementation_escalation_turns": 0,
        "post_edit_contract_guidance": False,
    },
    "enhanced": {
        "completion_reminder_turns": 8,
        "completion_critical_turns": 3,
        "implementation_deadline_turns": 10,
        "implementation_escalation_turns": 4,
        "post_edit_contract_guidance": True,
    },
}
_CLAW_TREATMENT_FIELDS = frozenset(
    next(iter(_CLAW_ABLATION_PROFILES.values())).keys()
)


def resolve_claw_ablation_profile(profile: str) -> Dict[str, Any]:
    """Return a copy of one frozen Claw treatment profile."""
    try:
        return dict(_CLAW_ABLATION_PROFILES[profile])
    except KeyError as exc:
        choices = ", ".join(sorted(_CLAW_ABLATION_PROFILES))
        raise BenchmarkError(
            f"unknown Claw ablation profile {profile!r}; expected one of: {choices}"
        ) from exc


def _read_object(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BenchmarkError(f"expected JSON object: {path}")
    return payload


def _episode_summary(collection: TrainingEpisodeCollectionResult) -> Dict[str, Any]:
    episode = collection.episodes[0]
    reported_input = episode.input_tokens
    reported_output = episode.output_tokens
    processed_input = reported_input
    processed_output = reported_output
    cache_read_input = 0
    cache_write_input = 0
    trajectory_path = Path(episode.trajectory_ref or "")
    if trajectory_path.is_file():
        trajectory = _read_object(trajectory_path)
        usages = [
            event.get("payload", {}).get("usage", {})
            for event in trajectory.get("events", [])
            if isinstance(event, dict) and event.get("event_type") == "model_response"
        ]
        usages = [usage for usage in usages if isinstance(usage, dict)]
        if usages and any("input" in usage for usage in usages):
            uncached_input = sum(int(usage.get("input", 0) or 0) for usage in usages)
            cache_read_input = sum(
                int(usage.get("cacheRead", 0) or 0) for usage in usages
            )
            cache_write_input = sum(
                int(usage.get("cacheWrite", 0) or 0) for usage in usages
            )
            processed_input = uncached_input + cache_read_input + cache_write_input
            processed_output = sum(
                int(usage.get("output", 0) or 0) for usage in usages
            )
    return {
        "success": episode.success,
        "test_pass_rate": episode.test_pass_rate,
        "turns": episode.turns,
        "tool_calls": episode.tool_calls,
        "input_tokens": processed_input,
        "output_tokens": processed_output,
        "total_tokens": processed_input + processed_output,
        "runtime_reported_usage": {
            "input_tokens": reported_input,
            "output_tokens": reported_output,
        },
        "cache_read_input_tokens": cache_read_input,
        "cache_write_input_tokens": cache_write_input,
        "latency_seconds": episode.latency_seconds,
        "format_valid": episode.format_valid,
        "process_violations": episode.process_violations,
        "bad_cases": list(episode.bad_cases),
        "error": episode.error,
        "behavior_diagnostics": episode.behavior_diagnostics,
        "collection_manifest_ref": str(collection.manifest_path),
        "trajectory_ref": str(episode.trajectory_ref),
        "verification_ref": str(episode.verification_ref),
    }


def build_swe_bench_runtime_comparison(
    *,
    output_root: Union[str, Path],
    claw: TrainingEpisodeCollectionResult,
    pi: TrainingEpisodeCollectionResult,
    model_backend_version: str,
    protocol: str,
) -> Dict[str, Any]:
    """Build a guarded comparison without treating runtime differences as controls."""
    root = Path(output_root).resolve()
    claw_context = _read_object(root / "claw" / "evaluator-fingerprint.json")
    pi_context = _read_object(root / "pi" / "evaluator-fingerprint.json")
    claw_environment = _read_object(root / "claw" / "environment-contract.json")
    pi_environment = _read_object(root / "pi" / "environment-contract.json")

    controlled = {
        "task_ids": (claw.manifest.task_ids, pi.manifest.task_ids),
        "suite_content_hash": (
            claw.manifest.suite_content_hash,
            pi.manifest.suite_content_hash,
        ),
        "model_ref": (claw.manifest.model_ref, pi.manifest.model_ref),
        "prompt_version": (
            claw.manifest.prompt_version,
            pi.manifest.prompt_version,
        ),
        "verifier_version": (
            claw.manifest.verifier_version,
            pi.manifest.verifier_version,
        ),
        "decoding_config": (
            claw.manifest.decoding_config,
            pi.manifest.decoding_config,
        ),
        "evaluator_fingerprint": (
            claw_context.get("evaluator_fingerprint"),
            pi_context.get("evaluator_fingerprint"),
        ),
        "allowed_path_patterns": (
            claw_context.get("allowed_path_patterns"),
            pi_context.get("allowed_path_patterns"),
        ),
        "environment_contract": (claw_environment, pi_environment),
    }
    mismatches = {
        name: {"claw": values[0], "pi": values[1]}
        for name, values in controlled.items()
        if canonical_hash(values[0]) != canonical_hash(values[1])
    }
    claw_result = _episode_summary(claw)
    pi_result = _episode_summary(pi)
    payload: Dict[str, Any] = {
        "schema_version": SWE_BENCH_RUNTIME_COMPARISON_SCHEMA_VERSION,
        "comparison_id": stable_id(
            "swe_bench_runtime_comparison",
            {
                "claw_collection": claw.manifest.collection_id,
                "pi_collection": pi.manifest.collection_id,
                "protocol": protocol,
            },
        ),
        "status": "comparable" if not mismatches else "not_comparable",
        "comparability": {
            "comparable": not mismatches,
            "temperature_is_explicit": {"claw": True, "pi": True},
            "controlled_fields": sorted(controlled),
            "mismatches": mismatches,
            "treatment_variables": [
                "runtime",
                "system_prompt",
                "tool_schema",
                "runtime_guidance",
            ],
        },
        "task": {
            "instance_id": claw.manifest.task_ids[0],
            "suite_id": claw.manifest.suite_id,
            "suite_version": claw.manifest.suite_version,
            "official_swebench_harness": False,
        },
        "model": {
            "api_model_id": claw.manifest.model_ref,
            "backend_version": model_backend_version,
            "protocol": protocol,
        },
        "controls": {
            "decoding_config": claw.manifest.decoding_config,
            "prompt_version": claw.manifest.prompt_version,
            "verifier_version": claw.manifest.verifier_version,
            "evaluator_fingerprint": claw_context.get("evaluator_fingerprint"),
            "allowed_path_patterns": claw_context.get("allowed_path_patterns"),
            "environment_contract": claw_environment,
        },
        "results": {"claw": claw_result, "pi": pi_result},
        "delta_pi_minus_claw": {
            "success": int(pi_result["success"]) - int(claw_result["success"]),
            "test_pass_rate": (
                pi_result["test_pass_rate"] - claw_result["test_pass_rate"]
            ),
            "turns": pi_result["turns"] - claw_result["turns"],
            "tool_calls": pi_result["tool_calls"] - claw_result["tool_calls"],
            "total_tokens": pi_result["total_tokens"] - claw_result["total_tokens"],
            "latency_seconds": (
                pi_result["latency_seconds"] - claw_result["latency_seconds"]
            ),
        },
        "claim_boundary": (
            "One controlled local SWE-bench Lite Dev comparison. This is not an "
            "official SWE-bench score and does not establish statistical superiority."
        ),
    }
    return payload


def _render_report(payload: Dict[str, Any]) -> str:
    claw = payload["results"]["claw"]
    pi = payload["results"]["pi"]
    delta = payload["delta_pi_minus_claw"]
    lines = [
        "# Claw–Pi local SWE-bench Lite comparison",
        "",
        f"- Status: **{payload['status']}**",
        f"- Instance: `{payload['task']['instance_id']}`",
        f"- Model: `{payload['model']['api_model_id']}` "
        f"(`{payload['model']['backend_version']}`)",
        f"- Protocol: `{payload['model']['protocol']}`",
        "- Official SWE-bench Harness: **no**",
        "",
        "| Metric | Claw | Pi | Pi - Claw |",
        "|---|---:|---:|---:|",
        f"| success | {int(claw['success'])} | {int(pi['success'])} | {delta['success']} |",
        f"| test_pass_rate | {claw['test_pass_rate']} | {pi['test_pass_rate']} | {delta['test_pass_rate']} |",
        f"| turns | {claw['turns']} | {pi['turns']} | {delta['turns']} |",
        f"| tool_calls | {claw['tool_calls']} | {pi['tool_calls']} | {delta['tool_calls']} |",
        f"| total_tokens | {claw['total_tokens']} | {pi['total_tokens']} | {delta['total_tokens']} |",
        f"| latency_seconds | {claw['latency_seconds']:.3f} | {pi['latency_seconds']:.3f} | {delta['latency_seconds']:.3f} |",
        "",
        "## Comparability",
        "",
    ]
    mismatches = payload["comparability"]["mismatches"]
    if mismatches:
        lines.extend(f"- `{name}` differs." for name in sorted(mismatches))
    else:
        lines.append("- All declared controls match, including explicit temperature.")
    lines.extend(
        [
            "",
            "## Claim boundary",
            "",
            f"- {payload['claim_boundary']}",
            "- Runtime, system prompt, tool schema, and runtime guidance are treatment variables.",
            "",
        ]
    )
    return "\n".join(lines)


def _collection_artifact(
    collection: TrainingEpisodeCollectionResult, name: str
) -> Dict[str, Any]:
    root = Path(collection.manifest_path).resolve().parent
    return _read_object(root / name)


def _controlled_decoding_config(
    collection: TrainingEpisodeCollectionResult,
) -> Dict[str, Any]:
    return {
        key: value
        for key, value in collection.manifest.decoding_config.items()
        if key not in _CLAW_TREATMENT_FIELDS
    }


def _comparison_mismatches(
    collections: Dict[str, TrainingEpisodeCollectionResult],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    controls_by_arm: Dict[str, Dict[str, Any]] = {}
    for arm, collection in collections.items():
        evaluator = _collection_artifact(collection, "evaluator-fingerprint.json")
        controls_by_arm[arm] = {
            "task_ids": collection.manifest.task_ids,
            "suite_content_hash": collection.manifest.suite_content_hash,
            "model_ref": collection.manifest.model_ref,
            "prompt_version": collection.manifest.prompt_version,
            "verifier_version": collection.manifest.verifier_version,
            "decoding_config": _controlled_decoding_config(collection),
            "evaluator_fingerprint": evaluator.get("evaluator_fingerprint"),
            "allowed_path_patterns": evaluator.get("allowed_path_patterns"),
            "environment_contract": _collection_artifact(
                collection, "environment-contract.json"
            ),
        }
    first_arm = next(iter(controls_by_arm))
    first = controls_by_arm[first_arm]
    mismatches: Dict[str, Any] = {}
    for field in first:
        values = {arm: controls[field] for arm, controls in controls_by_arm.items()}
        if len({canonical_hash(value) for value in values.values()}) != 1:
            mismatches[field] = values
    return first, mismatches


def build_swe_bench_runtime_ablation(
    *,
    collections: Dict[str, TrainingEpisodeCollectionResult],
    model_backend_version: str,
    protocol: str,
) -> Dict[str, Any]:
    """Build one guarded Pi Raw / Claw Base / Claw Enhanced comparison."""
    expected = {"claw_base", "claw_enhanced", "pi_raw"}
    if set(collections) != expected:
        raise BenchmarkError(
            "runtime ablation requires exactly claw_base, claw_enhanced, and pi_raw"
        )
    controls, mismatches = _comparison_mismatches(collections)
    base = collections["claw_base"]
    results = {
        arm: _episode_summary(collection)
        for arm, collection in collections.items()
    }

    def delta(left: str, right: str) -> Dict[str, Any]:
        left_result = results[left]
        right_result = results[right]
        return {
            "success": int(left_result["success"]) - int(right_result["success"]),
            "test_pass_rate": (
                left_result["test_pass_rate"] - right_result["test_pass_rate"]
            ),
            "turns": left_result["turns"] - right_result["turns"],
            "tool_calls": left_result["tool_calls"] - right_result["tool_calls"],
            "total_tokens": (
                left_result["total_tokens"] - right_result["total_tokens"]
            ),
            "latency_seconds": (
                left_result["latency_seconds"] - right_result["latency_seconds"]
            ),
        }

    treatment_config = {
        arm: {
            key: collection.manifest.decoding_config.get(key)
            for key in sorted(_CLAW_TREATMENT_FIELDS)
        }
        for arm, collection in collections.items()
    }
    payload: Dict[str, Any] = {
        "schema_version": SWE_BENCH_RUNTIME_ABLATION_SCHEMA_VERSION,
        "ablation_id": stable_id(
            "swe_bench_runtime_ablation",
            {
                "collections": {
                    arm: collection.manifest.collection_id
                    for arm, collection in sorted(collections.items())
                },
                "protocol": protocol,
            },
        ),
        "status": "comparable" if not mismatches else "not_comparable",
        "comparability": {
            "comparable": not mismatches,
            "temperature_is_explicit": {
                arm: True for arm in sorted(collections)
            },
            "controlled_fields": sorted(controls),
            "mismatches": mismatches,
            "treatment_variables": [
                "runtime",
                "system_prompt",
                "tool_schema",
                "runtime_guidance",
            ],
        },
        "task": {
            "instance_id": base.manifest.task_ids[0],
            "suite_id": base.manifest.suite_id,
            "suite_version": base.manifest.suite_version,
            "official_swebench_harness": False,
        },
        "model": {
            "api_model_id": base.manifest.model_ref,
            "backend_version": model_backend_version,
            "protocol": protocol,
        },
        "controls": controls,
        "treatments": treatment_config,
        "results": results,
        "deltas": {
            "claw_enhanced_minus_claw_base": delta(
                "claw_enhanced", "claw_base"
            ),
            "pi_raw_minus_claw_base": delta("pi_raw", "claw_base"),
        },
        "claim_boundary": (
            "One three-arm local SWE-bench Lite Dev ablation. This is not an "
            "official SWE-bench score and does not establish statistical superiority."
        ),
    }
    return payload


def _render_ablation_report(payload: Dict[str, Any]) -> str:
    arms = ("claw_base", "claw_enhanced", "pi_raw")
    labels = {
        "claw_base": "Claw Base",
        "claw_enhanced": "Claw Enhanced",
        "pi_raw": "Pi Raw",
    }
    lines = [
        "# Pi Raw / Claw Base / Claw Enhanced local SWE-bench Lite ablation",
        "",
        f"- Status: **{payload['status']}**",
        f"- Instance: `{payload['task']['instance_id']}`",
        f"- Model: `{payload['model']['api_model_id']}` "
        f"(`{payload['model']['backend_version']}`)",
        f"- Protocol: `{payload['model']['protocol']}`",
        "- Official SWE-bench Harness: **no**",
        "",
        "| Metric | Claw Base | Claw Enhanced | Pi Raw |",
        "|---|---:|---:|---:|",
    ]
    for metric in (
        "success",
        "test_pass_rate",
        "turns",
        "tool_calls",
        "total_tokens",
        "latency_seconds",
    ):
        values = []
        for arm in arms:
            value = payload["results"][arm][metric]
            if metric == "success":
                value = int(value)
            elif metric == "latency_seconds":
                value = f"{value:.3f}"
            values.append(str(value))
        lines.append(f"| {metric} | " + " | ".join(values) + " |")
    lines.extend(["", "## Frozen treatment profiles", ""])
    for arm in arms:
        treatment = json.dumps(
            payload["treatments"][arm], ensure_ascii=False, sort_keys=True
        )
        lines.append(f"- **{labels[arm]}**: `{treatment}`")
    lines.extend(["", "## Comparability", ""])
    mismatches = payload["comparability"]["mismatches"]
    if mismatches:
        lines.extend(f"- `{name}` differs." for name in sorted(mismatches))
    else:
        lines.append("- All declared non-treatment controls match.")
    lines.extend(
        [
            "",
            "## Claim boundary",
            "",
            f"- {payload['claim_boundary']}",
            "- Pi Raw is a fixed generic-harness reference, not a claimed competitive baseline.",
            "",
        ]
    )
    return "\n".join(lines)


def write_swe_bench_runtime_ablation(
    *,
    output_root: Union[str, Path],
    collections: Dict[str, TrainingEpisodeCollectionResult],
    model_backend_version: str,
    protocol: str,
) -> Dict[str, Any]:
    root = Path(output_root).resolve()
    payload = build_swe_bench_runtime_ablation(
        collections=collections,
        model_backend_version=model_backend_version,
        protocol=protocol,
    )
    _atomic_write(
        root / "runtime-ablation.json",
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write(root / "report.md", _render_ablation_report(payload))
    return payload


def load_swe_bench_collection_result(
    collection_root: Union[str, Path],
) -> TrainingEpisodeCollectionResult:
    """Load one immutable collection so derived reports can be rebuilt."""
    root = Path(collection_root).resolve()
    manifest_path = root / "collection-manifest.json"
    results_path = root / "episode-results.jsonl"
    manifest = TrainingEpisodeCollectionManifest(**_read_object(manifest_path))
    manifest.validate()
    lines = [
        line for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(lines) != 1:
        raise BenchmarkError("SWE-bench comparison expects exactly one Episode")
    episode = BenchmarkEpisodeResult.from_dict(json.loads(lines[0]))
    return TrainingEpisodeCollectionResult(
        manifest=manifest,
        episodes=[episode],
        manifest_path=manifest_path,
        results_path=results_path,
    )


def write_swe_bench_runtime_comparison(
    *,
    output_root: Union[str, Path],
    claw: TrainingEpisodeCollectionResult,
    pi: TrainingEpisodeCollectionResult,
    model_backend_version: str,
    protocol: str,
    json_name: str = "runtime-comparison.json",
    report_name: str = "report.md",
) -> Dict[str, Any]:
    """Write derived comparison artifacts while leaving source Episodes unchanged."""
    root = Path(output_root).resolve()
    payload = build_swe_bench_runtime_comparison(
        output_root=root,
        claw=claw,
        pi=pi,
        model_backend_version=model_backend_version,
        protocol=protocol,
    )
    _atomic_write(
        root / json_name,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write(root / report_name, _render_report(payload))
    return payload


def run_swe_bench_lite_runtime_comparison(
    *,
    benchmark_root: Union[str, Path],
    instance_id: str,
    python_executable: Union[str, Path],
    evaluator_script: Union[str, Path],
    output_root: Union[str, Path],
    generation_commit: str,
    api_config_root: Union[str, Path],
    model_ref: str,
    model_backend_version: str,
    pi_executable: Union[str, Path],
    allowed_path_patterns: Sequence[str],
    temperature: float = 0.0,
    max_tokens: int = 4096,
    max_turns: int = 24,
    max_total_tokens: int = 250000,
    timeout_seconds: float = 900.0,
    completion_reminder_turns: int = 8,
    completion_critical_turns: int = 3,
    implementation_deadline_turns: int = 10,
    implementation_escalation_turns: int = 4,
    post_edit_contract_guidance: bool = True,
    prompt_version: str = "swe-bench-lite-dev.runtime-comparison.v1",
    verifier_version: str = "verifier-policy.v1",
    pi_runtime_version: str = "pi@0.85.1",
    pi_tool_version: str = "pi-builtins@0.85.1",
    pi_extra_args: Sequence[str] = DEFAULT_PI_COMPARISON_ARGS,
    enforce_macos_seatbelt: bool = True,
    sandbox_attestation: str = (
        "macos-seatbelt:episode-parent-write-deny+sensitive-paths-v2"
    ),
) -> Dict[str, Any]:
    """Run Claw and Pi on one identical local SWE-bench Lite Dev instance."""
    root = Path(output_root).resolve()
    if root.exists():
        raise FileExistsError(root)
    if not model_ref.strip() or not model_backend_version.strip():
        raise BenchmarkError("model_ref and model_backend_version must not be empty")
    if not allowed_path_patterns:
        raise BenchmarkError("allowed_path_patterns must not be empty")
    config_root = Path(api_config_root).resolve()
    api_config = APIConfigRuntime(cwd=str(config_root)).get_config()
    if api_config.model != model_ref:
        raise BenchmarkError(
            f"active model {api_config.model!r} does not match {model_ref!r}"
        )
    if not api_config.api_key:
        raise BenchmarkError("active API configuration has no key")
    openai_base_url = api_config.base_url.rstrip("/")
    if openai_base_url.endswith("/anthropic"):
        openai_base_url = openai_base_url[: -len("/anthropic")]

    root.mkdir(parents=True)
    pi_config_dir = root / "pi-runtime-config"
    pi_config = {
        "providers": {
            "claw-openai-compat": {
                "baseUrl": openai_base_url,
                "api": "openai-completions",
                "apiKey": "$CLAW_PI_API_KEY",
                "models": [
                    {
                        "id": model_ref,
                        "name": f"Claw comparison: {model_ref}",
                        "reasoning": False,
                        "input": ["text"],
                        "contextWindow": 1_000_000,
                        "maxTokens": max_tokens,
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                        "samplingParams": {"temperature": temperature},
                    }
                ],
            }
        }
    }
    _atomic_write(
        pi_config_dir / "models.json",
        json.dumps(pi_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write(pi_config_dir / "auth.json", "{}\n")

    def claw_factory(cwd: str, inference_config: Dict[str, Any]) -> LocalCodingAgent:
        agent = LocalCodingAgent(
            cwd=cwd,
            api_config_cwd=str(config_root),
            model_config=ModelConfig(
                name=model_ref,
                temperature=float(inference_config["temperature"]),
                max_tokens=int(inference_config["max_tokens"]),
            ),
            permissions=AgentPermissions(
                allow_write=True,
                allow_shell=True,
                restrict_workspace=True,
                allowed_write_paths=list(allowed_path_patterns),
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
            budget=BudgetConfig(max_total_tokens=max_total_tokens),
            completion_reminder_turns=completion_reminder_turns,
            completion_critical_turns=completion_critical_turns,
            implementation_deadline_turns=implementation_deadline_turns,
            implementation_escalation_turns=implementation_escalation_turns,
            post_edit_contract_guidance=post_edit_contract_guidance,
            implementation_path_patterns=tuple(allowed_path_patterns),
        )
        agent.client = OpenAICompatClient(
            base_url=openai_base_url,
            api_key=api_config.api_key,
            model=model_ref,
        )
        return agent

    resolved_pi = Path(pi_executable).expanduser()
    if not resolved_pi.is_absolute():
        resolved_pi = (config_root / resolved_pi).resolve()
    if not resolved_pi.is_file():
        raise FileNotFoundError(resolved_pi)

    def pi_factory(cwd: str, _inference_config: Dict[str, Any]) -> PiRpcAgent:
        environment = dict(os.environ)
        environment["CLAW_PI_API_KEY"] = api_config.api_key
        environment["PI_CODING_AGENT_DIR"] = str(pi_config_dir)
        environment["PI_OFFLINE"] = "1"
        return PiRpcAgent(
            cwd,
            model=model_ref,
            provider="claw-openai-compat",
            executable=str(resolved_pi),
            extra_args=tuple(pi_extra_args),
            timeout_seconds=timeout_seconds,
            max_total_tokens=max_total_tokens,
            isolation_attestation=sandbox_attestation,
            isolation_metadata={"boundary": "operator_attested_process"},
            process_environment=environment,
            enforce_macos_seatbelt=enforce_macos_seatbelt,
        )

    shared = {
        "benchmark_root": benchmark_root,
        "instance_id": instance_id,
        "python_executable": python_executable,
        "evaluator_script": evaluator_script,
        "generation_commit": generation_commit,
        "api_config_root": config_root,
        "model_ref": model_ref,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "max_turns": max_turns,
        "max_total_tokens": max_total_tokens,
        "completion_reminder_turns": completion_reminder_turns,
        "completion_critical_turns": completion_critical_turns,
        "implementation_deadline_turns": implementation_deadline_turns,
        "implementation_escalation_turns": implementation_escalation_turns,
        "post_edit_contract_guidance": post_edit_contract_guidance,
        "timeout_seconds": timeout_seconds,
        "prompt_version": prompt_version,
        "verifier_version": verifier_version,
        "allowed_path_patterns": tuple(allowed_path_patterns),
    }
    claw = collect_swe_bench_lite_dev_episode(
        output_root=root / "claw",
        runtime_version="local-agent-runtime.v1",
        tool_version="tool-schema.v1",
        config_version="swe-bench-lite-claw-openai-comparison.v1",
        agent_factory=claw_factory,
        **shared,
    )
    pi = collect_swe_bench_lite_dev_episode(
        output_root=root / "pi",
        runtime_version=pi_runtime_version,
        tool_version=pi_tool_version,
        config_version="swe-bench-lite-pi-openai-comparison.v1",
        agent_factory=pi_factory,
        **shared,
    )
    return write_swe_bench_runtime_comparison(
        output_root=root,
        claw=claw,
        pi=pi,
        model_backend_version=model_backend_version,
        protocol="openai-completions",
    )


def run_swe_bench_lite_runtime_ablation(
    *,
    benchmark_root: Union[str, Path],
    instance_id: str,
    python_executable: Union[str, Path],
    evaluator_script: Union[str, Path],
    output_root: Union[str, Path],
    generation_commit: str,
    api_config_root: Union[str, Path],
    model_ref: str,
    model_backend_version: str,
    pi_executable: Union[str, Path],
    allowed_path_patterns: Sequence[str],
    temperature: float = 0.0,
    max_tokens: int = 4096,
    max_turns: int = 24,
    max_total_tokens: int = 250000,
    timeout_seconds: float = 900.0,
    prompt_version: str = "swe-bench-lite-dev.runtime-ablation.v1",
    verifier_version: str = "verifier-policy.v1",
    pi_runtime_version: str = "pi@0.85.1",
    pi_tool_version: str = "pi-builtins@0.85.1",
    pi_extra_args: Sequence[str] = DEFAULT_PI_COMPARISON_ARGS,
    enforce_macos_seatbelt: bool = True,
    sandbox_attestation: Optional[str] = None,
    claw_sandbox_backend: str = "host",
    claw_sandbox_image: Optional[str] = None,
    claw_sandbox_python: Optional[str] = None,
    claw_sandbox_evaluator_python: Optional[str] = None,
    pi_docker_image: Optional[str] = None,
    pi_docker_executable: str = "docker",
    pi_container_executable: str = "/opt/pi/node_modules/.bin/pi",
) -> Dict[str, Any]:
    """Run Pi once beside frozen Claw Base and Claw Enhanced profiles."""
    root = Path(output_root).resolve()
    if root.exists():
        raise FileExistsError(root)
    if not model_ref.strip() or not model_backend_version.strip():
        raise BenchmarkError("model_ref and model_backend_version must not be empty")
    if not allowed_path_patterns:
        raise BenchmarkError("allowed_path_patterns must not be empty")
    if pi_docker_image and re.fullmatch(
        r"[^@\s]+@sha256:[0-9a-fA-F]{64}", str(pi_docker_image)
    ) is None:
        raise BenchmarkError("Pi Docker image must be digest-pinned")
    if pi_docker_image and enforce_macos_seatbelt:
        raise BenchmarkError(
            "Pi Docker mode requires disabling macOS Seatbelt explicitly"
        )
    claw_sandbox_backend = str(claw_sandbox_backend).strip().lower()
    if claw_sandbox_backend not in {"host", "docker"}:
        raise BenchmarkError(
            "claw_sandbox_backend must be either 'host' or 'docker'"
        )
    if claw_sandbox_backend == "docker":
        if re.fullmatch(
            r"[^@\s]+@sha256:[0-9a-fA-F]{64}",
            str(claw_sandbox_image or ""),
        ) is None:
            raise BenchmarkError(
                "Claw Docker ablation requires a digest-pinned "
                "claw_sandbox_image"
            )
        if not str(claw_sandbox_python or "").strip():
            raise BenchmarkError(
                "Claw Docker ablation requires claw_sandbox_python"
            )
    elif claw_sandbox_image:
        raise BenchmarkError(
            "claw_sandbox_image is only valid with the Docker backend"
        )
    elif claw_sandbox_python:
        raise BenchmarkError(
            "claw_sandbox_python is only valid with the Docker backend"
        )
    elif claw_sandbox_evaluator_python:
        raise BenchmarkError(
            "claw_sandbox_evaluator_python is only valid with the Docker backend"
        )
    if sandbox_attestation is None and enforce_macos_seatbelt:
        sandbox_attestation = (
            "macos-seatbelt:episode-parent-write-deny+sensitive-paths-v2"
        )
    if not str(sandbox_attestation or "").strip():
        raise BenchmarkError(
            "Pi requires an explicit sandbox_attestation when macOS Seatbelt "
            "is disabled"
        )
    config_root = Path(api_config_root).resolve()
    api_config = APIConfigRuntime(cwd=str(config_root)).get_config()
    if api_config.model != model_ref:
        raise BenchmarkError(
            f"active model {api_config.model!r} does not match {model_ref!r}"
        )
    if not api_config.api_key:
        raise BenchmarkError("active API configuration has no key")
    openai_base_url = api_config.base_url.rstrip("/")
    if openai_base_url.endswith("/anthropic"):
        openai_base_url = openai_base_url[: -len("/anthropic")]

    resolved_pi = Path(pi_executable).expanduser()
    if pi_docker_image:
        resolved_pi_executable = str(pi_container_executable).strip()
        if not resolved_pi_executable.startswith("/"):
            raise BenchmarkError("Pi container executable must be absolute")
        pi_client_factory = partial(
            PiDockerRpcClient,
            docker_image=str(pi_docker_image),
            docker_executable=str(pi_docker_executable),
        )
    else:
        if not resolved_pi.is_absolute():
            resolved_pi = (config_root / resolved_pi).resolve()
        if not resolved_pi.is_file():
            raise FileNotFoundError(resolved_pi)
        resolved_pi_executable = str(resolved_pi)
        pi_client_factory = None

    root.mkdir(parents=True)
    pi_config_dir = root / "pi-runtime-config"
    pi_config = {
        "providers": {
            "claw-openai-compat": {
                "baseUrl": openai_base_url,
                "api": "openai-completions",
                "apiKey": "$CLAW_PI_API_KEY",
                "models": [
                    {
                        "id": model_ref,
                        "name": f"Claw ablation: {model_ref}",
                        "reasoning": False,
                        "input": ["text"],
                        "contextWindow": 1_000_000,
                        "maxTokens": max_tokens,
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                        "samplingParams": {"temperature": temperature},
                    }
                ],
            }
        }
    }
    _atomic_write(
        pi_config_dir / "models.json",
        json.dumps(pi_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    # Pi initializes its credential store even though this provider receives
    # the key through $CLAW_PI_API_KEY. An empty valid store never persists the
    # key and lets Docker Pi take its lock in the run-scoped config mount.
    _atomic_write(pi_config_dir / "auth.json", "{}\n")

    def claw_factory(profile: Dict[str, Any]):
        def factory(cwd: str, inference_config: Dict[str, Any]) -> LocalCodingAgent:
            agent = LocalCodingAgent(
                cwd=cwd,
                api_config_cwd=str(config_root),
                model_config=ModelConfig(
                    name=model_ref,
                    temperature=float(inference_config["temperature"]),
                    max_tokens=int(inference_config["max_tokens"]),
                ),
                permissions=AgentPermissions(
                    allow_write=True,
                    allow_shell=True,
                    restrict_workspace=True,
                    allowed_write_paths=list(allowed_path_patterns),
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
                budget=BudgetConfig(max_total_tokens=max_total_tokens),
                completion_reminder_turns=int(
                    profile["completion_reminder_turns"]
                ),
                completion_critical_turns=int(
                    profile["completion_critical_turns"]
                ),
                implementation_deadline_turns=int(
                    profile["implementation_deadline_turns"]
                ),
                implementation_escalation_turns=int(
                    profile["implementation_escalation_turns"]
                ),
                post_edit_contract_guidance=bool(
                    profile["post_edit_contract_guidance"]
                ),
                implementation_path_patterns=tuple(allowed_path_patterns),
            )
            agent.client = OpenAICompatClient(
                base_url=openai_base_url,
                api_key=api_config.api_key,
                model=model_ref,
            )
            return agent

        return factory

    def pi_factory(cwd: str, _inference_config: Dict[str, Any]) -> PiRpcAgent:
        environment = dict(os.environ)
        environment["CLAW_PI_API_KEY"] = api_config.api_key
        environment["PI_CODING_AGENT_DIR"] = str(pi_config_dir)
        environment["PI_OFFLINE"] = "1"
        return PiRpcAgent(
            cwd,
            model=model_ref,
            provider="claw-openai-compat",
            executable=resolved_pi_executable,
            extra_args=tuple(pi_extra_args),
            timeout_seconds=timeout_seconds,
            max_total_tokens=max_total_tokens,
            isolation_attestation=sandbox_attestation,
            isolation_metadata=(
                {
                    "boundary": "pi_docker_rpc",
                    "image": str(pi_docker_image),
                    "network_mode": "bridge",
                    "tool_network_matches_claw_shell": False,
                }
                if pi_docker_image
                else {"boundary": "operator_attested_process"}
            ),
            process_environment=environment,
            enforce_macos_seatbelt=enforce_macos_seatbelt,
            **(
                {"client_factory": pi_client_factory}
                if pi_client_factory is not None
                else {}
            ),
        )

    shared = {
        "benchmark_root": benchmark_root,
        "instance_id": instance_id,
        "python_executable": python_executable,
        "evaluator_script": evaluator_script,
        "generation_commit": generation_commit,
        "api_config_root": config_root,
        "model_ref": model_ref,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "max_turns": max_turns,
        "max_total_tokens": max_total_tokens,
        "timeout_seconds": timeout_seconds,
        "prompt_version": prompt_version,
        "verifier_version": verifier_version,
        "allowed_path_patterns": tuple(allowed_path_patterns),
    }
    collections: Dict[str, TrainingEpisodeCollectionResult] = {}
    for arm, profile_name in (
        ("claw_base", "base"),
        ("claw_enhanced", "enhanced"),
    ):
        profile = resolve_claw_ablation_profile(profile_name)
        collections[arm] = collect_swe_bench_lite_dev_episode(
            output_root=root / arm.replace("_", "-"),
            runtime_version="local-agent-runtime.v1",
            tool_version="tool-schema.v1",
            config_version=f"swe-bench-lite-claw-{profile_name}.v1",
            agent_factory=claw_factory(profile),
            sandbox_backend_name=claw_sandbox_backend,
            sandbox_image=claw_sandbox_image,
            sandbox_python_executable=claw_sandbox_python,
            sandbox_evaluator_python_executable=(
                claw_sandbox_evaluator_python
            ),
            **profile,
            **shared,
        )

    pi_profile = resolve_claw_ablation_profile("base")
    collections["pi_raw"] = collect_swe_bench_lite_dev_episode(
        output_root=root / "pi-raw",
        runtime_version=pi_runtime_version,
        tool_version=pi_tool_version,
        config_version="swe-bench-lite-pi-raw.v1",
        agent_factory=pi_factory,
        sandbox_backend_name=claw_sandbox_backend,
        sandbox_image=claw_sandbox_image,
        sandbox_python_executable=claw_sandbox_python,
        sandbox_evaluator_python_executable=claw_sandbox_evaluator_python,
        manage_agent_sandbox=False,
        **pi_profile,
        **shared,
    )
    return write_swe_bench_runtime_ablation(
        output_root=root,
        collections=collections,
        model_backend_version=model_backend_version,
        protocol="openai-completions",
    )
