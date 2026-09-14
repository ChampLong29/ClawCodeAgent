"""Pi coding-agent RPC baseline integrated with Claw's benchmark evidence path."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union

from ..agent_session import AgentSession
from ..agent_types import AgentRunResult, UsageStats
from ..sandbox import _generate_seatbelt_profile
from .local_agent_adapter import LocalAgentBenchmarkAdapter
from .runner import BenchmarkError


class PiRpcError(RuntimeError):
    """Raised when Pi's JSONL process violates or rejects the RPC contract."""


@dataclass
class PiRpcRun:
    events: List[Dict[str, Any]] = field(default_factory=list)
    final_message: Optional[str] = None
    usage: UsageStats = field(default_factory=UsageStats)
    stop_reason: str = "completed"
    error: Optional[str] = None


class PiRpcClient:
    """Small strict-JSONL client for ``pi --mode rpc``.

    A custom ``command`` is accepted for protocol tests or wrappers.  Otherwise
    the official CLI flags are assembled from ``executable``, ``provider``, and
    ``model``.
    """

    def __init__(
        self,
        cwd: Union[str, Path],
        *,
        executable: str = "pi",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        extra_args: Sequence[str] = (),
        command: Optional[Sequence[str]] = None,
        process_environment: Optional[Mapping[str, str]] = None,
        enforce_macos_seatbelt: bool = False,
    ) -> None:
        self.cwd = str(Path(cwd).resolve())
        self.executable = executable
        self.provider = provider
        self.model = model or ""
        self.extra_args = tuple(str(value) for value in extra_args)
        self.command = list(command) if command is not None else None
        self.process_environment = (
            {str(key): str(value) for key, value in process_environment.items()}
            if process_environment is not None
            else None
        )
        self.enforce_macos_seatbelt = bool(enforce_macos_seatbelt)

    def _build_command(self) -> List[str]:
        if self.command is not None:
            command = list(self.command)
        else:
            command = [self.executable, "--mode", "rpc", "--no-session"]
            if self.provider:
                command.extend(["--provider", self.provider])
            if self.model:
                command.extend(["--model", self.model])
            command.extend(self.extra_args)
        if not self.enforce_macos_seatbelt:
            return command
        if not os.path.isfile("/usr/bin/sandbox-exec"):
            raise PiRpcError(
                "macOS Seatbelt was requested but /usr/bin/sandbox-exec is unavailable"
            )
        profile = _generate_seatbelt_profile(
            self.cwd,
            ["~/.ssh", "~/.aws", "~/.config/gcloud", "~/.kube"],
            [22, 5432, 6379],
        )
        return ["/usr/bin/sandbox-exec", "-p", profile, *command]

    def run(
        self,
        prompt: str,
        *,
        timeout_seconds: float = 900.0,
        max_turns: Optional[int] = None,
        max_total_tokens: Optional[int] = None,
    ) -> PiRpcRun:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_turns is not None and max_turns <= 0:
            raise ValueError("max_turns must be positive")
        if max_total_tokens is not None and max_total_tokens <= 0:
            raise ValueError("max_total_tokens must be positive")
        try:
            process = subprocess.Popen(
                self._build_command(),
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=self.process_environment,
            )
        except OSError as exc:
            raise PiRpcError(f"unable to start Pi RPC process: {exc}") from exc

        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        stderr_lines: List[str] = []
        stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(process.stderr, stderr_lines),
            daemon=True,
        )
        stderr_thread.start()
        stdout_records: "queue.Queue[Optional[str]]" = queue.Queue()
        stdout_thread = threading.Thread(
            target=self._drain_stdout,
            args=(process.stdout, stdout_records),
            daemon=True,
        )
        stdout_thread.start()
        request_id = f"claw-{uuid.uuid4().hex[:12]}"
        events: List[Dict[str, Any]] = []
        deadline = time.monotonic() + timeout_seconds
        accepted = False
        settled = False
        aborted_for_turn_limit = False
        aborted_for_token_budget = False
        completed_turns = 0
        processed_tokens = 0
        try:
            self._send(
                process,
                {"id": request_id, "type": "prompt", "message": prompt},
            )
            while not settled:
                record = self._read_record(
                    process, stdout_records, deadline, stderr_lines
                )
                if (
                    record.get("type") == "response"
                    and record.get("id") == request_id
                    and record.get("command") == "prompt"
                ):
                    if not record.get("success"):
                        raise PiRpcError(
                            "Pi rejected prompt: " + self._response_error(record)
                        )
                    accepted = True
                    continue
                events.append(record)
                event_type = record.get("type")
                if event_type == "message_end":
                    processed_tokens += _assistant_message_processed_tokens(record)
                elif event_type == "turn_end":
                    completed_turns += 1
                    can_continue = bool(record.get("toolResults"))
                    token_budget_reached = (
                        max_total_tokens is not None
                        and processed_tokens >= max_total_tokens
                    )
                    turn_limit_reached = (
                        max_turns is not None
                        and completed_turns >= max_turns
                    )
                    if can_continue and token_budget_reached:
                        self._send(process, {"type": "abort"})
                        aborted_for_token_budget = True
                    elif can_continue and turn_limit_reached:
                        self._send(process, {"type": "abort"})
                        aborted_for_turn_limit = True
                elif event_type == "agent_settled":
                    settled = True

            if not accepted:
                raise PiRpcError("Pi settled without acknowledging the prompt")
            stats = self._request_stats(
                process, stdout_records, deadline, stderr_lines
            )
            run = self._build_run(events, stats)
            if aborted_for_token_budget:
                run.stop_reason = "budget_exceeded"
                run.error = f"Pi run reached max_total_tokens={max_total_tokens}"
            elif aborted_for_turn_limit:
                run.stop_reason = "stopped"
                run.error = f"Pi run reached max_turns={max_turns}"
            return run
        finally:
            self._stop_process(process)
            stderr_thread.join(timeout=0.2)
            stdout_thread.join(timeout=0.2)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    @staticmethod
    def _send(process: subprocess.Popen, payload: Dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

    @staticmethod
    def _read_record(
        process: subprocess.Popen,
        stdout_records: "queue.Queue[Optional[str]]",
        deadline: float,
        stderr_lines: List[str],
    ) -> Dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PiRpcError("Pi RPC timed out")
        try:
            line = stdout_records.get(timeout=remaining)
        except queue.Empty:
            raise PiRpcError("Pi RPC timed out")
        if line is None:
            detail = "".join(stderr_lines).strip()
            code = process.poll()
            raise PiRpcError(
                f"Pi RPC exited before agent_settled (exit={code})"
                + (f": {detail[-2000:]}" if detail else "")
            )
        try:
            record = json.loads(line.rstrip("\r\n"))
        except json.JSONDecodeError as exc:
            raise PiRpcError(f"Pi emitted invalid JSONL: {line[:300]!r}") from exc
        if not isinstance(record, dict):
            raise PiRpcError("Pi RPC record must be a JSON object")
        return record

    def _request_stats(
        self,
        process: subprocess.Popen,
        stdout_records: "queue.Queue[Optional[str]]",
        deadline: float,
        stderr_lines: List[str],
    ) -> Dict[str, Any]:
        stats_id = f"stats-{uuid.uuid4().hex[:12]}"
        self._send(process, {"id": stats_id, "type": "get_session_stats"})
        while True:
            record = self._read_record(
                process, stdout_records, deadline, stderr_lines
            )
            if record.get("type") == "response" and record.get("id") == stats_id:
                if not record.get("success"):
                    return {}
                data = record.get("data", {})
                return dict(data) if isinstance(data, dict) else {}

    @staticmethod
    def _build_run(
        events: List[Dict[str, Any]],
        stats: Dict[str, Any],
    ) -> PiRpcRun:
        final_message: Optional[str] = None
        error: Optional[str] = None
        model_calls = 0
        for event in events:
            if event.get("type") != "message_end":
                continue
            message = event.get("message", {})
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            model_calls += 1
            text = _message_text(message)
            if text:
                final_message = text
            if str(message.get("stopReason", "")).lower() == "error":
                error = str(message.get("errorMessage") or "Pi model request failed")
        tokens = stats.get("tokens", {})
        if not isinstance(tokens, dict):
            tokens = {}
        # Pi reports uncached input and cache traffic separately. Claw's
        # ``input_tokens`` contract represents all prompt tokens processed by
        # the provider, so include cache reads/writes instead of making Pi look
        # artificially cheaper than OpenAI-compatible runtime usage.
        input_tokens = sum(
            int(tokens.get(name, 0) or 0)
            for name in ("input", "cacheRead", "cacheWrite")
        )
        tool_calls = int(stats.get("toolCalls", 0) or 0)
        if not tool_calls:
            tool_calls = sum(
                event.get("type") == "tool_execution_start" for event in events
            )
        usage = UsageStats(
            input_tokens=input_tokens,
            output_tokens=int(tokens.get("output", 0) or 0),
            model_calls=model_calls,
            tool_calls=tool_calls,
        )
        return PiRpcRun(
            events=events,
            final_message=final_message,
            usage=usage,
            stop_reason="error" if error else "completed",
            error=error,
        )

    @staticmethod
    def _response_error(record: Dict[str, Any]) -> str:
        error = record.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        return str(error or "unknown error")

    @staticmethod
    def _drain_stderr(stream: Any, destination: List[str]) -> None:
        for line in stream:
            destination.append(line)

    @staticmethod
    def _drain_stdout(
        stream: Any,
        destination: "queue.Queue[Optional[str]]",
    ) -> None:
        for line in stream:
            destination.put(line)
        destination.put(None)

    def _stop_process(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)


class PiDockerRpcClient(PiRpcClient):
    """Run the complete Pi RPC process in a constrained Docker container.

    Pi performs provider requests and tool execution in the same process, so
    this boundary needs provider network access.  That tool-network difference
    from Claw's offline Shell sandbox must remain visible in experiment claims.
    """

    def __init__(
        self,
        cwd: Union[str, Path],
        *,
        docker_image: str,
        docker_executable: str = "docker",
        network_mode: str = "bridge",
        memory_limit: str = "1g",
        cpu_limit: float = 2.0,
        pids_limit: int = 256,
        **kwargs: Any,
    ) -> None:
        if kwargs.get("command") is not None:
            raise ValueError("Pi Docker RPC does not accept a custom command")
        if kwargs.get("enforce_macos_seatbelt"):
            raise ValueError("Pi Docker RPC cannot also enforce macOS Seatbelt")
        if re.fullmatch(
            r"[^@\s]+@sha256:[0-9a-fA-F]{64}", str(docker_image)
        ) is None:
            raise ValueError("Pi Docker image must be digest-pinned")
        if network_mode not in {"bridge", "host"}:
            raise ValueError("Pi Docker network_mode must be bridge or host")
        if cpu_limit <= 0 or pids_limit <= 0:
            raise ValueError("Pi Docker resource limits must be positive")
        super().__init__(cwd, **kwargs)
        config_dir = (self.process_environment or {}).get("PI_CODING_AGENT_DIR")
        if not config_dir:
            raise ValueError("Pi Docker RPC requires PI_CODING_AGENT_DIR")
        resolved_config_dir = Path(config_dir).resolve()
        if not resolved_config_dir.is_dir():
            raise ValueError("Pi Docker RPC config directory does not exist")
        self.config_dir = str(resolved_config_dir)
        for label, value in (("workspace", self.cwd), ("config", self.config_dir)):
            if "," in value:
                raise ValueError(f"Pi Docker {label} path must not contain a comma")
        self.docker_image = str(docker_image)
        self.docker_executable = str(docker_executable)
        self.network_mode = network_mode
        self.memory_limit = str(memory_limit)
        self.cpu_limit = float(cpu_limit)
        self.pids_limit = int(pids_limit)
        self.container_name = f"claw-pi-rpc-{uuid.uuid4().hex[:12]}"

    def _build_command(self) -> List[str]:
        command = [
            self.docker_executable,
            "run",
            "--rm",
            "--interactive",
            "--pull=never",
            "--name",
            self.container_name,
            "--network",
            self.network_mode,
            "--read-only",
            "--user",
            "1000:1000",
            "--cap-drop=ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory_limit,
            "--cpus",
            str(self.cpu_limit),
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=256m",
            "--mount",
            f"type=bind,src={self.cwd},dst=/workspace",
            "--mount",
            f"type=bind,src={self.config_dir},dst=/pi-config,readonly",
            "--workdir",
            "/workspace",
            "--env",
            "CLAW_PI_API_KEY",
            "--env",
            "PI_CODING_AGENT_DIR=/pi-config",
            "--env",
            "PI_OFFLINE=1",
            "--env",
            "HOME=/tmp/pi-home",
            self.docker_image,
            self.executable,
            "--mode",
            "rpc",
            "--no-session",
        ]
        if self.provider:
            command.extend(["--provider", self.provider])
        if self.model:
            command.extend(["--model", self.model])
        command.extend(self.extra_args)
        return command

    def _stop_process(self, process: subprocess.Popen) -> None:
        super()._stop_process(process)
        subprocess.run(
            [self.docker_executable, "rm", "--force", self.container_name],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env=self.process_environment,
        )


def _message_text(message: Dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "".join(parts)


def _assistant_message_processed_tokens(event: Dict[str, Any]) -> int:
    """Count provider-processed tokens for one completed assistant response."""
    message = event.get("message", {})
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return 0
    usage = message.get("usage", {})
    if not isinstance(usage, dict):
        return 0
    return sum(
        int(usage.get(name, 0) or 0)
        for name in ("input", "output", "cacheRead", "cacheWrite")
    )


def _canonical_pi_tool_name(name: str) -> str:
    return {
        "read": "read_file",
        "write": "write_file",
        "edit": "edit_file",
    }.get(name, name)


class _PiClientIdentity:
    def __init__(self, model: str) -> None:
        self.model = model


PiClientFactory = Callable[..., PiRpcClient]


class PiRpcAgent:
    """Agent-shaped Pi wrapper consumable by ``RuntimeAdapter``."""

    def __init__(
        self,
        cwd: str,
        *,
        model: str,
        provider: Optional[str] = None,
        executable: str = "pi",
        extra_args: Sequence[str] = (),
        timeout_seconds: float = 900.0,
        max_total_tokens: Optional[int] = None,
        isolation_attestation: str = "",
        isolation_metadata: Optional[Mapping[str, Any]] = None,
        process_environment: Optional[Mapping[str, str]] = None,
        enforce_macos_seatbelt: bool = False,
        client_factory: PiClientFactory = PiRpcClient,
    ) -> None:
        self.cwd = cwd
        self.provider = provider
        self.executable = executable
        self.extra_args = tuple(extra_args)
        self.timeout_seconds = timeout_seconds
        if max_total_tokens is not None and max_total_tokens <= 0:
            raise ValueError("max_total_tokens must be positive")
        self.max_total_tokens = max_total_tokens
        self.isolation_attestation = isolation_attestation
        self.isolation_metadata = dict(isolation_metadata or {})
        self.process_environment = process_environment
        self.enforce_macos_seatbelt = bool(enforce_macos_seatbelt)
        self.client_factory = client_factory
        self.client = _PiClientIdentity(model)
        self.permissions: Dict[str, Any] = {
            "external_runtime": "pi_rpc",
            "isolation_attestation": isolation_attestation,
            "isolation_metadata": self.isolation_metadata,
        }
        self.session: Optional[AgentSession] = None
        self.runtime_observer: Optional[Any] = None

    def run(
        self,
        prompt: str,
        max_turns: Optional[int] = None,
        stream: bool = False,
    ) -> AgentRunResult:
        del stream  # Pi RPC events are collected independently of terminal display.
        self.session = AgentSession(session_id=f"pi-{uuid.uuid4().hex[:12]}")
        self.session.cwd = self.cwd
        self.session.model = self.client.model
        self.session.add_user_message(prompt)
        if self.runtime_observer is not None:
            self.runtime_observer.on_run_start(self, prompt=prompt, phase_id="runtime")
        try:
            rpc = self.client_factory(
                self.cwd,
                executable=self.executable,
                provider=self.provider,
                model=self.client.model,
                extra_args=self.extra_args,
                process_environment=self.process_environment,
                enforce_macos_seatbelt=self.enforce_macos_seatbelt,
            )
            run = rpc.run(
                prompt,
                timeout_seconds=self.timeout_seconds,
                max_turns=max_turns,
                max_total_tokens=self.max_total_tokens,
            )
            self._record_events(run.events)
            if run.final_message:
                self.session.add_assistant_message(run.final_message)
            result = AgentRunResult(
                stop_reason=run.stop_reason,
                final_message=run.final_message,
                usage=run.usage,
                error=run.error,
            )
        except Exception as exc:
            result = AgentRunResult(
                stop_reason="error",
                usage=UsageStats(),
                error=f"{type(exc).__name__}: {exc}",
            )
        if self.runtime_observer is not None:
            self.runtime_observer.on_run_finish(result, phase_id="runtime")
        return result

    def _record_events(self, events: List[Dict[str, Any]]) -> None:
        if self.runtime_observer is None:
            return
        active_model_event: Optional[str] = None
        tool_event_ids: Dict[str, str] = {}
        tool_arguments_valid: Dict[str, bool] = {}
        for event in events:
            event_type = event.get("type")
            if event_type == "turn_start":
                active_model_event = self.runtime_observer.record(
                    "model_request",
                    payload={"source": "pi_rpc"},
                    phase_id="runtime",
                )
            elif event_type == "message_end":
                message = event.get("message", {})
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    continue
                usage = message.get("usage", {})
                self.runtime_observer.record(
                    "model_response",
                    payload={
                        "source": "pi_rpc",
                        "content": _message_text(message),
                        "usage": usage if isinstance(usage, dict) else {},
                        "finish_reason": message.get("stopReason"),
                    },
                    parent_event_id=active_model_event,
                    phase_id="runtime",
                )
            elif event_type == "tool_execution_start":
                call_id = str(event.get("toolCallId", ""))
                requested_name = str(event.get("toolName", ""))
                canonical_name = _canonical_pi_tool_name(requested_name)
                recorded_id = self.runtime_observer.record(
                    "tool_call",
                    payload={
                        "source": "pi_rpc",
                        "call_id": call_id,
                        "tool_name": canonical_name,
                        "requested_tool_name": requested_name,
                        "arguments": event.get("args", {}),
                    },
                    parent_event_id=active_model_event,
                    phase_id="runtime",
                )
                tool_event_ids[call_id] = recorded_id
                tool_arguments_valid[call_id] = isinstance(event.get("args"), dict)
            elif event_type == "tool_execution_end":
                call_id = str(event.get("toolCallId", ""))
                tool_name = str(event.get("toolName", ""))
                canonical_name = _canonical_pi_tool_name(tool_name)
                self.runtime_observer.record(
                    "tool_result",
                    payload={
                        "source": "pi_rpc",
                        "call_id": call_id,
                        "requested_tool_name": tool_name,
                        "actual_tool_name": canonical_name,
                        "ok": not bool(event.get("isError", False)),
                        "result": event.get("result"),
                        "selection_valid": bool(tool_name),
                        "arguments_valid": tool_arguments_valid.get(call_id, False),
                        "side_effect_possible": canonical_name in {
                            "bash", "write_file", "edit_file"
                        },
                    },
                    parent_event_id=tool_event_ids.get(call_id),
                    phase_id="runtime",
                )
            elif event_type in {
                "compaction_start",
                "compaction_end",
                "auto_retry_start",
                "auto_retry_end",
                "extension_error",
            }:
                self.runtime_observer.record(
                    "runtime_guidance",
                    payload={"source": "pi_rpc", "pi_event": event},
                    phase_id="runtime",
                )


class PiRpcBenchmarkAdapter(LocalAgentBenchmarkAdapter):
    """Run Pi as a reproducible external baseline under Claw verification."""

    def __init__(
        self,
        episodes_root: Union[str, Path],
        *,
        model_ref: str,
        runtime_version: str,
        prompt_version: str,
        tool_version: str,
        config_version: str,
        sandbox_attestation: str,
        provider: Optional[str] = None,
        pi_executable: str = "pi",
        pi_extra_args: Sequence[str] = (),
        timeout_seconds: float = 900.0,
        process_environment: Optional[Mapping[str, str]] = None,
        enforce_macos_seatbelt: bool = False,
        client_factory: PiClientFactory = PiRpcClient,
        **kwargs: Any,
    ) -> None:
        if not str(sandbox_attestation).strip():
            raise BenchmarkError(
                "Pi baseline requires a non-empty sandbox_attestation; "
                "the upstream runtime has no built-in permission sandbox"
            )

        def agent_factory(cwd: str, inference_config: Dict[str, Any]) -> PiRpcAgent:
            return PiRpcAgent(
                cwd,
                model=model_ref,
                provider=provider,
                executable=pi_executable,
                extra_args=pi_extra_args,
                timeout_seconds=float(
                    inference_config.get("timeout_seconds", timeout_seconds)
                ),
                max_total_tokens=(
                    int(inference_config["max_total_tokens"])
                    if inference_config.get("max_total_tokens") is not None
                    else None
                ),
                isolation_attestation=str(sandbox_attestation),
                process_environment=process_environment,
                enforce_macos_seatbelt=enforce_macos_seatbelt,
                client_factory=client_factory,
            )

        super().__init__(
            episodes_root,
            agent_factory=agent_factory,
            model_ref=model_ref,
            runtime_version=runtime_version,
            prompt_version=prompt_version,
            tool_version=tool_version,
            config_version=config_version,
            episode_prefix="pi-baseline",
            **kwargs,
        )
