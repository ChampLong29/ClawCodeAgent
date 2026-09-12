"""Backend-neutral contracts for model-directed execution isolation.

The first implementation is :class:`HostBackend`, a compatibility backend
that centralizes execution and environment filtering but deliberately does not
claim an OS isolation boundary.  Container and remote implementations can use
the same contracts without leaking provider-specific argv into agent code.
"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Protocol, Sequence, Tuple, Union

from .task_commands import (
    SANDBOX_INHERITED_ENV_NAMES,
    sandbox_subprocess_environment,
)


DEFAULT_EXEC_OUTPUT_LIMIT_BYTES = 1_000_000


class SandboxRuntimeTier(str, Enum):
    HOST = "host"
    CONTAINER = "container"
    USER_KERNEL = "user_kernel"
    MICROVM = "microvm"


class SandboxState(str, Enum):
    NEW = "new"
    READY = "ready"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    DESTROYED = "destroyed"


class NetworkMode(str, Enum):
    NONE = "none"
    PROXY_ONLY = "proxy_only"
    ALLOWLIST = "allowlist"
    UNRESTRICTED = "unrestricted"


class SandboxErrorCode(str, Enum):
    SPEC_INVALID = "spec_invalid"
    DENIED_BY_POLICY = "denied_by_policy"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    IMAGE_UNAVAILABLE = "image_unavailable"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    START_FAILED = "start_failed"
    EXEC_TIMEOUT = "exec_timeout"
    EXEC_CANCELLED = "exec_cancelled"
    INSTANCE_LOST = "instance_lost"
    SNAPSHOT_FAILED = "snapshot_failed"
    DESTROY_INCOMPLETE = "destroy_incomplete"
    UNKNOWN = "unknown"


class SandboxBackendError(RuntimeError):
    """Stable backend failure independent of provider error strings."""

    def __init__(
        self,
        code: SandboxErrorCode,
        detail: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail
        self.retryable = bool(retryable)


@dataclass(frozen=True)
class ImageRef:
    reference: str = ""
    digest: str = ""


@dataclass(frozen=True)
class WorkspaceSpec:
    host_path: str
    sandbox_path: str = "/workspace"
    mode: str = "rw"


@dataclass(frozen=True)
class MountSpec:
    host_path: str
    sandbox_path: str
    mode: str = "ro"
    kind: str = "allowlisted_extra"
    owner_scope: str = ""


@dataclass(frozen=True)
class EnvironmentSpec:
    inherited_names: Tuple[str, ...] = SANDBOX_INHERITED_ENV_NAMES
    values: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NetworkPolicy:
    mode: NetworkMode = NetworkMode.UNRESTRICTED
    allowed_domains: Tuple[str, ...] = ()
    allowed_cidrs: Tuple[str, ...] = ()
    allowed_ports: Tuple[int, ...] = ()
    block_metadata: bool = True


@dataclass(frozen=True)
class ResourceLimits:
    cpus: Optional[float] = None
    memory_mb: Optional[int] = None
    pids: Optional[int] = None
    disk_mb: Optional[int] = None
    output_bytes: Optional[int] = None
    wall_time_seconds: Optional[float] = None

    def requested_names(self) -> Tuple[str, ...]:
        return tuple(
            name
            for name, value in asdict(self).items()
            if value is not None
        )


@dataclass(frozen=True)
class LifecyclePolicy:
    idle_timeout_seconds: Optional[float] = None
    ttl_seconds: Optional[float] = None
    preserve_on_stop: bool = True


@dataclass(frozen=True)
class ProvenanceSpec:
    operator_claim: str = ""
    image_source: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxSpec:
    sandbox_id: str
    owner_kind: str
    owner_id: str
    backend: str
    runtime_tier: SandboxRuntimeTier
    workspace: WorkspaceSpec
    image: Optional[ImageRef] = None
    mounts: Tuple[MountSpec, ...] = ()
    environment: EnvironmentSpec = field(default_factory=EnvironmentSpec)
    network: NetworkPolicy = field(default_factory=NetworkPolicy)
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    lifecycle: LifecyclePolicy = field(default_factory=LifecyclePolicy)
    security_profile: str = "host_development"
    provenance: ProvenanceSpec = field(default_factory=ProvenanceSpec)

    @classmethod
    def for_host_workspace(
        cls,
        cwd: str,
        *,
        owner_kind: str = "interactive_session",
        owner_id: str = "",
        sandbox_id: str = "",
    ) -> "SandboxSpec":
        resolved = os.path.realpath(os.path.abspath(cwd))
        return cls(
            sandbox_id=sandbox_id or f"host-{uuid.uuid4().hex[:16]}",
            owner_kind=owner_kind,
            owner_id=owner_id or resolved,
            backend="host",
            runtime_tier=SandboxRuntimeTier.HOST,
            workspace=WorkspaceSpec(host_path=resolved),
            network=NetworkPolicy(
                mode=NetworkMode.UNRESTRICTED,
                block_metadata=False,
            ),
        )

    @classmethod
    def for_docker_workspace(
        cls,
        cwd: str,
        *,
        image: str,
        owner_kind: str = "interactive_session",
        owner_id: str = "",
        sandbox_id: str = "",
        security_profile: str = "isolated_development",
        network_mode: NetworkMode = NetworkMode.NONE,
        resources: Optional[ResourceLimits] = None,
    ) -> "SandboxSpec":
        resolved = os.path.realpath(os.path.abspath(cwd))
        image_reference, image_digest = _split_image_reference(image)
        return cls(
            sandbox_id=sandbox_id or f"docker-{uuid.uuid4().hex[:16]}",
            owner_kind=owner_kind,
            owner_id=owner_id or resolved,
            backend="docker",
            runtime_tier=SandboxRuntimeTier.CONTAINER,
            workspace=WorkspaceSpec(host_path=resolved),
            image=ImageRef(
                reference=image_reference,
                digest=image_digest,
            ),
            network=NetworkPolicy(mode=network_mode),
            resources=resources or ResourceLimits(
                cpus=2.0,
                memory_mb=2048,
                pids=256,
                output_bytes=DEFAULT_EXEC_OUTPUT_LIMIT_BYTES,
                wall_time_seconds=60.0,
            ),
            security_profile=security_profile,
        )

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(self)

    def fingerprint(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class SandboxCapabilities:
    isolation_tiers: Tuple[SandboxRuntimeTier, ...]
    network_modes: Tuple[NetworkMode, ...]
    supports_snapshots: bool = False
    supports_pause_resume: bool = False
    supports_streaming_exec: bool = False
    supports_exec_cancel: bool = False
    supports_fs_api: bool = False
    supports_resource_limits: Tuple[str, ...] = ()
    supports_verified_image_identity: bool = False
    admission_enforced_out_of_process: bool = False
    unsupported_fields: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(self)


@dataclass
class SandboxHandle:
    sandbox_id: str
    owner_kind: str
    owner_id: str
    backend_name: str
    workspace_path: str
    spec_hash: str
    state: SandboxState = SandboxState.NEW
    generation: int = 1
    created_at: float = field(default_factory=time.time)
    backend_metadata: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecRequest:
    command: Union[str, Tuple[str, ...]]
    cwd: str = "."
    environment: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 60.0
    output_limit_bytes: Optional[int] = DEFAULT_EXEC_OUTPUT_LIMIT_BYTES
    call_id: str = ""


@dataclass(frozen=True)
class ExecResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: Optional[int]
    timed_out: bool = False
    cancelled: bool = False
    error: str = ""
    error_code: Optional[SandboxErrorCode] = None
    started_at: float = 0.0
    ended_at: float = 0.0
    output_truncated: bool = False
    backend_name: str = ""
    sandbox_id: str = ""
    spec_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True)
class ExecStreamEvent:
    stdout: str = ""
    stderr: str = ""
    result: Optional[ExecResult] = None


@dataclass(frozen=True)
class SandboxStatus:
    sandbox_id: str
    owner_kind: str
    owner_id: str
    backend_name: str
    state: SandboxState
    spec_hash: str
    generation: int


class SandboxBackend(Protocol):
    """Provider-neutral lifecycle and execution contract."""

    name: str

    def capabilities(self) -> SandboxCapabilities: ...

    def prepare(self, spec: SandboxSpec) -> SandboxHandle: ...

    def start(self, handle: SandboxHandle) -> None: ...

    def exec(self, handle: SandboxHandle, request: ExecRequest) -> ExecResult: ...

    def stream_exec(
        self, handle: SandboxHandle, request: ExecRequest
    ) -> Iterator[ExecStreamEvent]: ...

    def stop(self, handle: SandboxHandle, reason: str = "") -> None: ...

    def destroy(self, handle: SandboxHandle) -> None: ...

    def inspect(self, handle: SandboxHandle) -> SandboxStatus: ...

    def list_owned(self, owner_id: str) -> Sequence[SandboxStatus]: ...


class HostBackend:
    """Compatibility backend with filtered environment and no OS isolation."""

    name = "host"

    def __init__(self) -> None:
        self._handles: Dict[str, SandboxHandle] = {}
        self._specs: Dict[str, SandboxSpec] = {}

    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            isolation_tiers=(SandboxRuntimeTier.HOST,),
            network_modes=(NetworkMode.UNRESTRICTED,),
            supports_streaming_exec=os.name == "posix",
            supports_exec_cancel=False,
            supports_fs_api=False,
            unsupported_fields=(
                "mounts",
                "resource_limits",
                "network_isolation",
                "snapshots",
                "pause_resume",
            ),
        )

    def prepare(self, spec: SandboxSpec) -> SandboxHandle:
        existing = self._handles.get(spec.sandbox_id)
        if existing and existing.state != SandboxState.DESTROYED:
            if existing.spec_hash != spec.fingerprint():
                raise SandboxBackendError(
                    SandboxErrorCode.SPEC_INVALID,
                    f"sandbox id {spec.sandbox_id!r} already has a different spec",
                )
            return existing

        self._validate_spec(spec)
        workspace = os.path.realpath(os.path.abspath(spec.workspace.host_path))
        handle = SandboxHandle(
            sandbox_id=spec.sandbox_id,
            owner_kind=spec.owner_kind,
            owner_id=spec.owner_id,
            backend_name=self.name,
            workspace_path=workspace,
            spec_hash=spec.fingerprint(),
            state=SandboxState.READY,
            generation=(existing.generation + 1 if existing else 1),
        )
        self._handles[handle.sandbox_id] = handle
        self._specs[handle.sandbox_id] = spec
        return handle

    def start(self, handle: SandboxHandle) -> None:
        self._require_known(handle)
        if handle.state == SandboxState.DESTROYED:
            raise SandboxBackendError(
                SandboxErrorCode.INSTANCE_LOST,
                f"sandbox {handle.sandbox_id!r} was destroyed",
            )
        if handle.state not in (SandboxState.READY, SandboxState.STOPPED, SandboxState.RUNNING):
            raise SandboxBackendError(
                SandboxErrorCode.START_FAILED,
                f"sandbox {handle.sandbox_id!r} is {handle.state.value}",
            )
        handle.state = SandboxState.RUNNING

    def exec(self, handle: SandboxHandle, request: ExecRequest) -> ExecResult:
        self._require_running(handle)
        _validate_exec_request(request)
        cwd = self._resolve_exec_cwd(handle, request.cwd)
        environment = self._execution_environment(handle, request.environment)
        command, shell = _subprocess_command(request.command)
        started_at = time.time()
        process = _spawn_process(command, shell=shell, cwd=cwd, env=environment)
        try:
            stdout_bytes, stderr_bytes = process.communicate(
                timeout=request.timeout_seconds
            )
            ended_at = time.time()
            stdout, stderr, truncated = _limit_output(
                _coerce_text(stdout_bytes),
                _coerce_text(stderr_bytes),
                request.output_limit_bytes,
            )
            return ExecResult(
                ok=process.returncode == 0,
                stdout=stdout,
                stderr=stderr,
                returncode=process.returncode,
                error=(
                    "" if process.returncode == 0
                    else f"Command exited with code {process.returncode}"
                ),
                started_at=started_at,
                ended_at=ended_at,
                output_truncated=truncated,
                backend_name=self.name,
                sandbox_id=handle.sandbox_id,
                spec_hash=handle.spec_hash,
            )
        except subprocess.TimeoutExpired:
            _terminate_process(process)
            stdout_bytes, stderr_bytes = process.communicate()
            ended_at = time.time()
            stdout, stderr, truncated = _limit_output(
                _coerce_text(stdout_bytes),
                _coerce_text(stderr_bytes),
                request.output_limit_bytes,
            )
            return ExecResult(
                ok=False,
                stdout=stdout,
                stderr=stderr,
                returncode=None,
                timed_out=True,
                error=f"Command timed out after {request.timeout_seconds:g} seconds",
                error_code=SandboxErrorCode.EXEC_TIMEOUT,
                started_at=started_at,
                ended_at=ended_at,
                output_truncated=truncated,
                backend_name=self.name,
                sandbox_id=handle.sandbox_id,
                spec_hash=handle.spec_hash,
            )

    def stream_exec(
        self, handle: SandboxHandle, request: ExecRequest
    ) -> Iterator[ExecStreamEvent]:
        self._require_running(handle)
        _validate_exec_request(request)
        if os.name != "posix":
            result = self.exec(handle, request)
            if result.stdout:
                yield ExecStreamEvent(stdout=result.stdout)
            if result.stderr:
                yield ExecStreamEvent(stderr=result.stderr)
            yield ExecStreamEvent(result=result)
            return
        cwd = self._resolve_exec_cwd(handle, request.cwd)
        environment = self._execution_environment(handle, request.environment)
        command, shell = _subprocess_command(request.command)
        started_at = time.time()
        process = _spawn_process(command, shell=shell, cwd=cwd, env=environment)
        assert process.stdout is not None
        assert process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        stdout_parts = []
        stderr_parts = []
        output_remaining = request.output_limit_bytes
        output_truncated = False
        timed_out = False
        deadline = time.monotonic() + request.timeout_seconds
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    _terminate_process(process)
                    break
                for key, _ in selector.select(timeout=min(0.1, remaining)):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    kept, output_remaining, chunk_truncated = _limit_chunk(
                        chunk, output_remaining
                    )
                    output_truncated = output_truncated or chunk_truncated
                    if key.data == "stdout":
                        stdout_parts.append(kept)
                        if kept:
                            yield ExecStreamEvent(stdout=_coerce_text(kept))
                    else:
                        stderr_parts.append(kept)
                        if kept:
                            yield ExecStreamEvent(stderr=_coerce_text(kept))
            process.wait(timeout=1)
        finally:
            selector.close()
            if process.poll() is None:
                _terminate_process(process)
                process.wait()
            process.stdout.close()
            process.stderr.close()

        stdout = _coerce_text(b"".join(stdout_parts))
        stderr = _coerce_text(b"".join(stderr_parts))
        returncode = None if timed_out else process.returncode
        result = ExecResult(
            ok=not timed_out and returncode == 0,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            timed_out=timed_out,
            error=(
                f"Command timed out after {request.timeout_seconds:g} seconds"
                if timed_out
                else ""
                if returncode == 0
                else f"Command exited with code {returncode}"
            ),
            error_code=(SandboxErrorCode.EXEC_TIMEOUT if timed_out else None),
            started_at=started_at,
            ended_at=time.time(),
            output_truncated=output_truncated,
            backend_name=self.name,
            sandbox_id=handle.sandbox_id,
            spec_hash=handle.spec_hash,
        )
        yield ExecStreamEvent(result=result)

    def stop(self, handle: SandboxHandle, reason: str = "") -> None:
        del reason
        self._require_known(handle)
        if handle.state != SandboxState.DESTROYED:
            handle.state = SandboxState.STOPPED

    def destroy(self, handle: SandboxHandle) -> None:
        self._require_known(handle)
        handle.state = SandboxState.DESTROYED

    def inspect(self, handle: SandboxHandle) -> SandboxStatus:
        self._require_known(handle)
        return SandboxStatus(
            sandbox_id=handle.sandbox_id,
            owner_kind=handle.owner_kind,
            owner_id=handle.owner_id,
            backend_name=handle.backend_name,
            state=handle.state,
            spec_hash=handle.spec_hash,
            generation=handle.generation,
        )

    def list_owned(self, owner_id: str) -> Sequence[SandboxStatus]:
        return tuple(
            self.inspect(handle)
            for handle in self._handles.values()
            if handle.owner_id == owner_id
        )

    def _validate_spec(self, spec: SandboxSpec) -> None:
        if spec.backend != self.name:
            raise SandboxBackendError(
                SandboxErrorCode.SPEC_INVALID,
                f"HostBackend cannot realize backend {spec.backend!r}",
            )
        if spec.runtime_tier != SandboxRuntimeTier.HOST:
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                f"HostBackend cannot provide {spec.runtime_tier.value} isolation",
            )
        if spec.security_profile != "host_development":
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                f"HostBackend cannot enforce security profile {spec.security_profile!r}",
            )
        if spec.network.mode != NetworkMode.UNRESTRICTED:
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                f"HostBackend cannot enforce network mode {spec.network.mode.value!r}",
            )
        if spec.network.block_metadata:
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                "HostBackend cannot guarantee cloud metadata blocking",
            )
        requested_limits = spec.resources.requested_names()
        if requested_limits:
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                "HostBackend cannot enforce resource limits: "
                + ", ".join(requested_limits),
            )
        if spec.mounts:
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                "HostBackend does not realize additional mounts",
            )
        if spec.workspace.mode != "rw":
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                "HostBackend currently requires a read-write workspace",
            )
        workspace = os.path.realpath(os.path.abspath(spec.workspace.host_path))
        if not os.path.isdir(workspace):
            raise SandboxBackendError(
                SandboxErrorCode.SPEC_INVALID,
                f"workspace does not exist or is not a directory: {workspace}",
            )
        try:
            sandbox_subprocess_environment(
                cwd=workspace,
                inherited_names=spec.environment.inherited_names,
                values=spec.environment.values,
            )
        except ValueError as exc:
            raise SandboxBackendError(
                SandboxErrorCode.DENIED_BY_POLICY,
                str(exc),
            ) from exc

    def _execution_environment(
        self,
        handle: SandboxHandle,
        request_values: Mapping[str, str],
    ) -> Dict[str, str]:
        spec = self._specs[handle.sandbox_id]
        values = dict(spec.environment.values)
        values.update({str(key): str(value) for key, value in request_values.items()})
        try:
            return sandbox_subprocess_environment(
                cwd=handle.workspace_path,
                inherited_names=spec.environment.inherited_names,
                values=values,
            )
        except ValueError as exc:
            raise SandboxBackendError(
                SandboxErrorCode.DENIED_BY_POLICY,
                str(exc),
            ) from exc

    def _resolve_exec_cwd(self, handle: SandboxHandle, requested: str) -> str:
        candidate = (
            requested
            if os.path.isabs(requested)
            else os.path.join(handle.workspace_path, requested)
        )
        candidate = os.path.realpath(os.path.abspath(candidate))
        try:
            inside = os.path.commonpath([handle.workspace_path, candidate]) == handle.workspace_path
        except ValueError:
            inside = False
        if not inside:
            raise SandboxBackendError(
                SandboxErrorCode.DENIED_BY_POLICY,
                f"execution cwd is outside the workspace: {requested}",
            )
        if not os.path.isdir(candidate):
            raise SandboxBackendError(
                SandboxErrorCode.SPEC_INVALID,
                f"execution cwd is not a directory: {requested}",
            )
        return candidate

    def _require_known(self, handle: SandboxHandle) -> None:
        registered = self._handles.get(handle.sandbox_id)
        if registered is not handle:
            raise SandboxBackendError(
                SandboxErrorCode.INSTANCE_LOST,
                f"unknown sandbox handle: {handle.sandbox_id}",
            )

    def _require_running(self, handle: SandboxHandle) -> None:
        self._require_known(handle)
        if handle.state != SandboxState.RUNNING:
            raise SandboxBackendError(
                SandboxErrorCode.INSTANCE_LOST,
                f"sandbox {handle.sandbox_id!r} is not running ({handle.state.value})",
            )


def execute_with_backend(
    command: Union[str, Iterable[str]],
    *,
    cwd: str,
    backend: Optional[SandboxBackend] = None,
    handle: Optional[SandboxHandle] = None,
    timeout_seconds: float = 60.0,
    environment: Optional[Mapping[str, str]] = None,
    output_limit_bytes: Optional[int] = DEFAULT_EXEC_OUTPUT_LIMIT_BYTES,
    call_id: str = "",
) -> ExecResult:
    """Execute through an explicit backend, defaulting to compatibility Host."""
    if handle is not None and backend is None:
        raise SandboxBackendError(
            SandboxErrorCode.SPEC_INVALID,
            "an explicit sandbox handle requires its owning backend",
        )
    effective_backend: SandboxBackend = backend or HostBackend()
    effective_handle = handle
    transient = effective_handle is None
    if effective_handle is None:
        if getattr(effective_backend, "name", "") != "host":
            raise SandboxBackendError(
                SandboxErrorCode.SPEC_INVALID,
                "a non-host backend requires an explicit sandbox handle",
            )
        spec = SandboxSpec.for_host_workspace(cwd)
        effective_handle = effective_backend.prepare(spec)
        effective_backend.start(effective_handle)
    request_cwd = _backend_request_cwd(effective_handle, cwd, transient)
    try:
        return effective_backend.exec(
            effective_handle,
            ExecRequest(
                command=_normalize_command(command),
                cwd=request_cwd,
                environment=dict(environment or {}),
                timeout_seconds=timeout_seconds,
                output_limit_bytes=output_limit_bytes,
                call_id=call_id,
            ),
        )
    finally:
        if transient:
            effective_backend.destroy(effective_handle)


def stream_with_backend(
    command: Union[str, Iterable[str]],
    *,
    cwd: str,
    backend: Optional[SandboxBackend] = None,
    handle: Optional[SandboxHandle] = None,
    timeout_seconds: float = 60.0,
    environment: Optional[Mapping[str, str]] = None,
    output_limit_bytes: Optional[int] = DEFAULT_EXEC_OUTPUT_LIMIT_BYTES,
    call_id: str = "",
) -> Iterator[ExecStreamEvent]:
    """Stream execution through a backend with Host compatibility fallback."""
    if handle is not None and backend is None:
        raise SandboxBackendError(
            SandboxErrorCode.SPEC_INVALID,
            "an explicit sandbox handle requires its owning backend",
        )
    effective_backend: SandboxBackend = backend or HostBackend()
    effective_handle = handle
    transient = effective_handle is None
    if effective_handle is None:
        if getattr(effective_backend, "name", "") != "host":
            raise SandboxBackendError(
                SandboxErrorCode.SPEC_INVALID,
                "a non-host backend requires an explicit sandbox handle",
            )
        spec = SandboxSpec.for_host_workspace(cwd)
        effective_handle = effective_backend.prepare(spec)
        effective_backend.start(effective_handle)
    request_cwd = _backend_request_cwd(effective_handle, cwd, transient)
    try:
        yield from effective_backend.stream_exec(
            effective_handle,
            ExecRequest(
                command=_normalize_command(command),
                cwd=request_cwd,
                environment=dict(environment or {}),
                timeout_seconds=timeout_seconds,
                output_limit_bytes=output_limit_bytes,
                call_id=call_id,
            ),
        )
    finally:
        if transient:
            effective_backend.destroy(effective_handle)


def _normalize_command(command: Union[str, Iterable[str]]) -> Union[str, Tuple[str, ...]]:
    if isinstance(command, str):
        return command
    return tuple(str(part) for part in command)


def _split_image_reference(image: str) -> Tuple[str, str]:
    value = str(image).strip()
    if "@" not in value:
        return value, ""
    reference, digest = value.rsplit("@", 1)
    return reference, digest


def _validate_exec_request(request: ExecRequest) -> None:
    if request.timeout_seconds <= 0:
        raise SandboxBackendError(
            SandboxErrorCode.SPEC_INVALID,
            "timeout_seconds must be positive",
        )
    if request.output_limit_bytes is not None and request.output_limit_bytes < 0:
        raise SandboxBackendError(
            SandboxErrorCode.SPEC_INVALID,
            "output_limit_bytes must not be negative",
        )


def _backend_request_cwd(
    handle: SandboxHandle,
    cwd: str,
    transient: bool,
) -> str:
    if transient:
        return "."
    if not os.path.isabs(cwd):
        return cwd
    candidate = os.path.realpath(os.path.abspath(cwd))
    workspace = os.path.realpath(os.path.abspath(handle.workspace_path))
    try:
        if os.path.commonpath([workspace, candidate]) == workspace:
            relative = os.path.relpath(candidate, workspace)
            return "." if relative == os.curdir else relative
    except ValueError:
        pass
    return cwd


def _subprocess_command(
    command: Union[str, Tuple[str, ...]],
) -> Tuple[Union[str, Sequence[str]], bool]:
    if isinstance(command, str):
        return command, True
    if not command:
        raise SandboxBackendError(
            SandboxErrorCode.SPEC_INVALID,
            "command argv must not be empty",
        )
    return list(command), False


def _spawn_process(
    command: Union[str, Sequence[str]],
    *,
    shell: bool,
    cwd: str,
    env: Mapping[str, str],
) -> subprocess.Popen:
    try:
        return subprocess.Popen(
            command,
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(env),
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        raise SandboxBackendError(
            SandboxErrorCode.BACKEND_UNAVAILABLE,
            f"failed to start command: {exc}",
            retryable=False,
        ) from exc


def _limit_output(
    stdout: str,
    stderr: str,
    limit_bytes: Optional[int],
) -> Tuple[str, str, bool]:
    if limit_bytes is None:
        return stdout, stderr, False
    if limit_bytes < 0:
        raise SandboxBackendError(
            SandboxErrorCode.SPEC_INVALID,
            "output_limit_bytes must not be negative",
        )
    remaining = limit_bytes
    stdout_bytes = stdout.encode("utf-8", errors="replace")
    kept_stdout = stdout_bytes[:remaining]
    remaining -= len(kept_stdout)
    stderr_bytes = stderr.encode("utf-8", errors="replace")
    kept_stderr = stderr_bytes[:remaining]
    truncated = len(kept_stdout) < len(stdout_bytes) or len(kept_stderr) < len(stderr_bytes)
    return (
        kept_stdout.decode("utf-8", errors="replace"),
        kept_stderr.decode("utf-8", errors="replace"),
        truncated,
    )


def _limit_chunk(
    chunk: bytes,
    remaining: Optional[int],
) -> Tuple[bytes, Optional[int], bool]:
    if remaining is None:
        return chunk, None, False
    if remaining < 0:
        raise SandboxBackendError(
            SandboxErrorCode.SPEC_INVALID,
            "output_limit_bytes must not be negative",
        )
    kept = chunk[:remaining]
    return kept, remaining - len(kept), len(kept) < len(chunk)


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
    process.kill()


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "DEFAULT_EXEC_OUTPUT_LIMIT_BYTES",
    "EnvironmentSpec",
    "ExecRequest",
    "ExecResult",
    "ExecStreamEvent",
    "HostBackend",
    "ImageRef",
    "LifecyclePolicy",
    "MountSpec",
    "NetworkMode",
    "NetworkPolicy",
    "ProvenanceSpec",
    "ResourceLimits",
    "SandboxBackend",
    "SandboxBackendError",
    "SandboxCapabilities",
    "SandboxErrorCode",
    "SandboxHandle",
    "SandboxRuntimeTier",
    "SandboxSpec",
    "SandboxState",
    "SandboxStatus",
    "WorkspaceSpec",
    "execute_with_backend",
    "stream_with_backend",
]
