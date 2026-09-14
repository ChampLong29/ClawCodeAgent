"""Command-facing orchestration for reproducible Pi RPC benchmarks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple, Union

from ..api_config import APIConfigRuntime, APIProvider
from ..task_suite import TaskSuiteManifest
from ..training_backends.base import atomic_write_text
from ..verification import VerificationPolicy
from .local_command import (
    _manifest_ref,
    _oracle_allowed_paths,
    _select_test_tasks,
)
from .pi_rpc_adapter import PiClientFactory, PiRpcBenchmarkAdapter, PiRpcClient
from .runner import BenchmarkConfig, BenchmarkError, BenchmarkRunResult, BenchmarkRunner


def run_pi_benchmark(
    *,
    manifest_path: Union[str, Path],
    output_root: Union[str, Path],
    model_ref: str,
    runtime_version: str,
    tool_version: str,
    sandbox_attestation: str,
    episodes_root: Optional[Union[str, Path]] = None,
    provider: Optional[str] = None,
    pi_executable: str = "pi",
    pi_extra_args: Sequence[str] = (),
    use_claw_api_config: bool = False,
    process_environment: Optional[Mapping[str, str]] = None,
    enforce_macos_seatbelt: bool = False,
    max_tokens: int = 4096,
    max_total_tokens: int = 250000,
    temperature: float = 0.0,
    max_turns: int = 50,
    timeout_seconds: float = 900.0,
    seed: int = 42,
    prompt_version: str = "task-prompt.v1",
    verifier_version: str = "verifier-policy.v1",
    config_version: str = "pi-rpc-cli.v1",
    task_ids: Sequence[str] = (),
    limit: Optional[int] = None,
    allowed_path_patterns: Sequence[str] = (),
    input_token_price_per_million: float = 0.0,
    output_token_price_per_million: float = 0.0,
    client_factory: PiClientFactory = PiRpcClient,
) -> BenchmarkRunResult:
    """Run Pi against Claw's immutable tasks and independent verifier."""
    for name, value in (
        ("model_ref", model_ref),
        ("runtime_version", runtime_version),
        ("tool_version", tool_version),
        ("sandbox_attestation", sandbox_attestation),
        ("pi_executable", pi_executable),
    ):
        if not str(value).strip():
            raise BenchmarkError(f"{name} must not be empty")
    if max_turns <= 0:
        raise BenchmarkError("max_turns must be positive")
    if max_tokens <= 0:
        raise BenchmarkError("max_tokens must be positive")
    if max_total_tokens <= 0:
        raise BenchmarkError("max_total_tokens must be positive")
    if not 0.0 <= temperature <= 2.0:
        raise BenchmarkError("temperature must be within [0, 2]")
    if timeout_seconds <= 0:
        raise BenchmarkError("timeout_seconds must be positive")
    if input_token_price_per_million < 0 or output_token_price_per_million < 0:
        raise BenchmarkError("token prices must be non-negative")

    manifest = TaskSuiteManifest.load(manifest_path)
    tasks = _select_test_tasks(manifest, task_ids, limit)
    executable_path = Path(pi_executable)
    resolved_pi_executable = str(pi_executable)
    if not executable_path.is_absolute() and len(executable_path.parts) > 1:
        resolved_pi_executable = str(
            (manifest.project_root / executable_path).resolve()
        )
    patterns = [str(pattern) for pattern in allowed_path_patterns if str(pattern)]
    allowed_paths_resolver = (
        (lambda _task: list(patterns))
        if patterns
        else (lambda task: list(_oracle_allowed_paths(manifest, task)))
    )
    output = Path(output_root).resolve()
    episode_output = Path(episodes_root or (output / "episodes")).resolve()
    effective_provider = provider
    effective_environment = process_environment
    temperature_is_explicit = False
    if use_claw_api_config:
        (
            effective_provider,
            effective_environment,
            temperature_is_explicit,
        ) = _prepare_claw_api_config(
            project_root=manifest.project_root,
            output_root=output,
            model_ref=str(model_ref),
            max_tokens=max_tokens,
            temperature=temperature,
            base_environment=process_environment,
        )
    decoding_config = {
        "temperature": float(temperature),
        "temperature_is_explicit": temperature_is_explicit,
        "max_tokens": int(max_tokens),
        "max_total_tokens": int(max_total_tokens),
        "max_turns": int(max_turns),
        "timeout_seconds": float(timeout_seconds),
        "pi_extra_args": [str(value) for value in pi_extra_args],
        "sandbox_attestation": str(sandbox_attestation),
    }
    adapter = PiRpcBenchmarkAdapter(
        episode_output,
        project_root=manifest.project_root,
        model_ref=str(model_ref),
        provider=effective_provider,
        pi_executable=resolved_pi_executable,
        pi_extra_args=tuple(str(value) for value in pi_extra_args),
        timeout_seconds=float(timeout_seconds),
        process_environment=effective_environment,
        enforce_macos_seatbelt=enforce_macos_seatbelt,
        runtime_version=str(runtime_version),
        prompt_version=str(prompt_version),
        tool_version=str(tool_version),
        config_version=str(config_version),
        sandbox_attestation=str(sandbox_attestation),
        verification_policy=VerificationPolicy(version=verifier_version),
        allowed_paths_resolver=allowed_paths_resolver,
        input_token_price=input_token_price_per_million / 1_000_000,
        output_token_price=output_token_price_per_million / 1_000_000,
        client_factory=client_factory,
    )
    config = BenchmarkConfig(
        group_name="base",
        model_ref=str(model_ref),
        test_manifest_ref=_manifest_ref(manifest),
        decoding_config=decoding_config,
        tool_schema_version=str(tool_version),
        runtime_version=str(runtime_version),
        verifier_bundle_version=str(verifier_version),
        prompt_version=str(prompt_version),
        seed=int(seed),
    )
    return BenchmarkRunner(output).run(tasks, adapter, config)


def _prepare_claw_api_config(
    *,
    project_root: Path,
    output_root: Path,
    model_ref: str,
    max_tokens: int,
    temperature: float,
    base_environment: Optional[Mapping[str, str]],
) -> Tuple[str, Dict[str, str], bool]:
    """Create a secret-free Pi provider config for Claw's active endpoint."""
    api_config = APIConfigRuntime(cwd=str(project_root)).get_config()
    if api_config.model != model_ref:
        raise BenchmarkError(
            "--model must match Claw's active model when --use-claw-api-config "
            f"is enabled (active={api_config.model!r})"
        )
    if not api_config.api_key:
        raise BenchmarkError("Claw's active API configuration has no API key")
    if api_config.provider == APIProvider.ANTHROPIC:
        provider_id = "claw-anthropic-compat"
        api_name = "anthropic-messages"
        temperature_is_explicit = False
    else:
        provider_id = "claw-openai-compat"
        api_name = "openai-completions"
        temperature_is_explicit = True

    config_dir = output_root / "pi-config"
    model: Dict[str, object] = {
        "id": model_ref,
        "name": f"Claw comparison: {model_ref}",
        "reasoning": False,
        "input": ["text"],
        "contextWindow": 1_000_000,
        "maxTokens": int(max_tokens),
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
    }
    if api_name.startswith("openai-"):
        model["samplingParams"] = {"temperature": float(temperature)}
    payload = {
        "providers": {
            provider_id: {
                "baseUrl": api_config.base_url,
                "api": api_name,
                "apiKey": "$CLAW_PI_API_KEY",
                "models": [model],
            }
        }
    }
    atomic_write_text(
        config_dir / "models.json",
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    # Pi opens auth.json during startup even when the custom provider reads its
    # key from an environment reference. Seed a valid secret-free store; Docker
    # Pi may then take its adjacent lock in the narrow run-scoped config mount.
    atomic_write_text(config_dir / "auth.json", "{}\n")
    environment = dict(base_environment or os.environ)
    environment["CLAW_PI_API_KEY"] = api_config.api_key
    environment["PI_CODING_AGENT_DIR"] = str(config_dir)
    environment["PI_OFFLINE"] = "1"
    return provider_id, environment, temperature_is_explicit
