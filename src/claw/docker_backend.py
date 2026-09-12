"""Docker implementation of the model-directed sandbox execution contract.

The Docker CLI is treated as a trusted control-plane client and is always
invoked with structured argv.  Model-generated commands only appear after
``docker exec`` and never participate in construction of container options.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

from .sandbox_backend import (
    ExecRequest,
    ExecResult,
    ExecStreamEvent,
    MountSpec,
    NetworkMode,
    SandboxBackendError,
    SandboxCapabilities,
    SandboxErrorCode,
    SandboxHandle,
    SandboxRuntimeTier,
    SandboxSpec,
    SandboxState,
    SandboxStatus,
)
from .task_commands import (
    runtime_subprocess_environment,
    sandbox_subprocess_environment,
)


_DOCKER_CONTAINER_PATH = (
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)
_DOCKER_PROFILES = {
    "host_development",
    "isolated_development",
    "benchmark_offline",
}
_ISOLATED_PROFILES = {"isolated_development", "benchmark_offline"}
_MOUNT_KINDS = {"runtime", "test_fixture", "allowlisted_extra"}
_PROTECTED_MOUNT_PATHS = {
    "/",
    "/boot",
    "/dev",
    "/etc",
    "/proc",
    "/run",
    "/sys",
    "/var/run",
}
_PROTECTED_CONTAINER_TARGETS = (
    "/dev",
    "/proc",
    "/run",
    "/sys",
    "/var/run",
)
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class DockerCLIResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


DockerRunner = Callable[[Sequence[str], float], DockerCLIResult]


class DockerBackend:
    """Hardened, fail-closed Docker backend for one container per owner."""

    name = "docker"

    def __init__(
        self,
        *,
        docker_command: str = "docker",
        allowed_mount_roots: Sequence[str] = (),
        container_user: Optional[str] = None,
        runner: Optional[DockerRunner] = None,
        cleanup_on_exit: bool = True,
    ) -> None:
        self.docker_command = str(docker_command)
        self.allowed_mount_roots = tuple(
            os.path.realpath(os.path.abspath(path))
            for path in allowed_mount_roots
        )
        self.container_user = container_user or _default_container_user()
        self._runner = runner
        self._handles: Dict[str, SandboxHandle] = {}
        self._specs: Dict[str, SandboxSpec] = {}
        self._server_version = ""
        if cleanup_on_exit:
            atexit.register(self.close)

    def close(self) -> None:
        """Best-effort cleanup for containers owned by this backend instance."""
        for handle in tuple(self._handles.values()):
            if handle.state == SandboxState.DESTROYED:
                continue
            try:
                self.destroy(handle)
            except SandboxBackendError:
                handle.state = SandboxState.FAILED

    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            isolation_tiers=(SandboxRuntimeTier.CONTAINER,),
            network_modes=(NetworkMode.NONE, NetworkMode.UNRESTRICTED),
            supports_snapshots=False,
            supports_pause_resume=False,
            supports_streaming_exec=False,
            supports_exec_cancel=False,
            supports_fs_api=False,
            supports_resource_limits=(
                "cpus",
                "memory_mb",
                "pids",
                "output_bytes",
                "wall_time_seconds",
            ),
            supports_verified_image_identity=True,
            admission_enforced_out_of_process=False,
            unsupported_fields=(
                "disk_mb",
                "proxy_only_network",
                "allowlist_network",
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

        mounts = self._validate_spec(spec)
        self._ensure_backend_available()
        image_ref, image_identity = self._inspect_image(spec)
        generation = existing.generation + 1 if existing else 1
        container_name = _container_name(spec.sandbox_id, generation)
        create_args = self._build_create_args(
            spec,
            mounts=mounts,
            image_ref=image_ref,
            container_name=container_name,
        )
        try:
            created = self._invoke(create_args, timeout=60.0)
        except subprocess.TimeoutExpired as exc:
            raise SandboxBackendError(
                SandboxErrorCode.START_FAILED,
                "Docker timed out while creating the sandbox container",
                retryable=True,
            ) from exc
        if created.returncode != 0:
            raise SandboxBackendError(
                SandboxErrorCode.START_FAILED,
                _docker_error("failed to create sandbox container", created),
                retryable=_looks_transient(created.stderr),
            )

        handle = SandboxHandle(
            sandbox_id=spec.sandbox_id,
            owner_kind=spec.owner_kind,
            owner_id=spec.owner_id,
            backend_name=self.name,
            workspace_path=os.path.realpath(spec.workspace.host_path),
            spec_hash=spec.fingerprint(),
            state=SandboxState.READY,
            generation=generation,
            backend_metadata={
                "container_id": created.stdout.strip(),
                "container_name": container_name,
                "docker_server_version": self._server_version,
                "image_identity": image_identity,
                "image_reference": image_ref,
            },
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
        if handle.state == SandboxState.RUNNING:
            return
        container = self._container(handle)
        try:
            result = self._invoke(("start", container), timeout=30.0)
        except subprocess.TimeoutExpired as exc:
            handle.state = SandboxState.FAILED
            raise SandboxBackendError(
                SandboxErrorCode.START_FAILED,
                f"Docker timed out while starting {container}",
                retryable=True,
            ) from exc
        if result.returncode != 0:
            handle.state = SandboxState.FAILED
            raise SandboxBackendError(
                SandboxErrorCode.START_FAILED,
                _docker_error("failed to start sandbox container", result),
                retryable=_looks_transient(result.stderr),
            )
        state = self._read_container_state(handle)
        if state != SandboxState.RUNNING:
            handle.state = SandboxState.FAILED
            raise SandboxBackendError(
                SandboxErrorCode.START_FAILED,
                f"sandbox container did not remain running (state={state.value})",
            )
        handle.state = SandboxState.RUNNING

    def exec(self, handle: SandboxHandle, request: ExecRequest) -> ExecResult:
        self._require_running(handle)
        timeout_seconds, output_limit = self._effective_limits(handle, request)
        exec_args = self._build_exec_args(handle, request)
        started_at = time.time()
        try:
            completed = self._invoke(exec_args, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self._kill_after_timeout(handle)
            stdout, stderr, truncated = _limit_output(
                _coerce_text(exc.stdout),
                _coerce_text(exc.stderr),
                output_limit,
            )
            return ExecResult(
                ok=False,
                stdout=stdout,
                stderr=stderr,
                returncode=None,
                timed_out=True,
                error=f"Command timed out after {timeout_seconds:g} seconds",
                error_code=SandboxErrorCode.EXEC_TIMEOUT,
                started_at=started_at,
                ended_at=time.time(),
                output_truncated=truncated,
                backend_name=self.name,
                sandbox_id=handle.sandbox_id,
                spec_hash=handle.spec_hash,
            )

        stdout, stderr, truncated = _limit_output(
            completed.stdout,
            completed.stderr,
            output_limit,
        )
        error_code = (
            SandboxErrorCode.RESOURCE_EXHAUSTED
            if completed.returncode in (137, 143)
            else None
        )
        return ExecResult(
            ok=completed.returncode == 0,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            error=(
                ""
                if completed.returncode == 0
                else f"Command exited with code {completed.returncode}"
            ),
            error_code=error_code,
            started_at=started_at,
            ended_at=time.time(),
            output_truncated=truncated,
            backend_name=self.name,
            sandbox_id=handle.sandbox_id,
            spec_hash=handle.spec_hash,
        )

    def stream_exec(
        self, handle: SandboxHandle, request: ExecRequest
    ) -> Iterator[ExecStreamEvent]:
        result = self.exec(handle, request)
        if result.stdout:
            yield ExecStreamEvent(stdout=result.stdout)
        if result.stderr:
            yield ExecStreamEvent(stderr=result.stderr)
        yield ExecStreamEvent(result=result)

    def stop(self, handle: SandboxHandle, reason: str = "") -> None:
        del reason
        self._require_known(handle)
        if handle.state in (SandboxState.STOPPED, SandboxState.DESTROYED):
            return
        if handle.state == SandboxState.READY:
            handle.state = SandboxState.STOPPED
            return
        container = self._container(handle)
        try:
            result = self._invoke(("stop", "--time", "5", container), timeout=15.0)
        except subprocess.TimeoutExpired as exc:
            raise SandboxBackendError(
                SandboxErrorCode.BACKEND_UNAVAILABLE,
                f"Docker timed out while stopping {container}",
                retryable=True,
            ) from exc
        if result.returncode != 0 and "No such container" not in result.stderr:
            raise SandboxBackendError(
                SandboxErrorCode.BACKEND_UNAVAILABLE,
                _docker_error("failed to stop sandbox container", result),
                retryable=_looks_transient(result.stderr),
            )
        handle.state = SandboxState.STOPPED

    def destroy(self, handle: SandboxHandle) -> None:
        self._require_known(handle)
        if handle.state == SandboxState.DESTROYED:
            return
        container = self._container(handle)
        try:
            result = self._invoke(
                ("rm", "--force", "--volumes", container),
                timeout=30.0,
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxBackendError(
                SandboxErrorCode.DESTROY_INCOMPLETE,
                f"Docker timed out while destroying {container}",
                retryable=True,
            ) from exc
        if result.returncode != 0 and "No such container" not in result.stderr:
            raise SandboxBackendError(
                SandboxErrorCode.DESTROY_INCOMPLETE,
                _docker_error("failed to destroy sandbox container", result),
                retryable=_looks_transient(result.stderr),
            )
        handle.state = SandboxState.DESTROYED

    def inspect(self, handle: SandboxHandle) -> SandboxStatus:
        self._require_known(handle)
        if handle.state != SandboxState.DESTROYED:
            handle.state = self._read_container_state(handle)
        return _status(handle)

    def list_owned(self, owner_id: str) -> Sequence[SandboxStatus]:
        statuses = []
        for handle in self._handles.values():
            if handle.owner_id != owner_id:
                continue
            try:
                statuses.append(self.inspect(handle))
            except SandboxBackendError:
                handle.state = SandboxState.FAILED
                statuses.append(_status(handle))
        return tuple(statuses)

    def _validate_spec(self, spec: SandboxSpec) -> Tuple[MountSpec, ...]:
        if spec.backend != self.name:
            raise SandboxBackendError(
                SandboxErrorCode.SPEC_INVALID,
                f"DockerBackend cannot realize backend {spec.backend!r}",
            )
        if spec.runtime_tier != SandboxRuntimeTier.CONTAINER:
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                "DockerBackend provides container isolation only",
            )
        if spec.security_profile not in _DOCKER_PROFILES:
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                f"DockerBackend cannot enforce profile {spec.security_profile!r}",
            )
        if not spec.sandbox_id or not spec.owner_id or not spec.owner_kind:
            raise SandboxBackendError(
                SandboxErrorCode.SPEC_INVALID,
                "sandbox_id, owner_id and owner_kind are required",
            )
        self._validate_image_spec(spec)
        self._validate_network(spec)
        self._validate_resources(spec)
        self._validate_lifecycle(spec)
        self._validate_container_user()
        mounts = self._admit_mounts(spec)
        self._container_environment(spec, request_values={})
        return mounts

    def _validate_image_spec(self, spec: SandboxSpec) -> None:
        if spec.image is None or not spec.image.reference.strip():
            raise SandboxBackendError(
                SandboxErrorCode.SPEC_INVALID,
                "DockerBackend requires an explicit image reference",
            )
        digest = spec.image.digest.strip()
        if digest and _DIGEST_PATTERN.fullmatch(digest) is None:
            raise SandboxBackendError(
                SandboxErrorCode.SPEC_INVALID,
                f"invalid image digest: {digest!r}",
            )
        if spec.security_profile == "benchmark_offline" and not digest:
            raise SandboxBackendError(
                SandboxErrorCode.DENIED_BY_POLICY,
                "benchmark_offline requires an image pinned by sha256 digest",
            )

    def _validate_network(self, spec: SandboxSpec) -> None:
        network = spec.network
        if network.mode not in self.capabilities().network_modes:
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                f"DockerBackend cannot enforce network mode {network.mode.value!r}",
            )
        if network.allowed_domains or network.allowed_cidrs or network.allowed_ports:
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                "domain, CIDR and port allowlists require a network controller",
            )
        if spec.security_profile in _ISOLATED_PROFILES and network.mode != NetworkMode.NONE:
            raise SandboxBackendError(
                SandboxErrorCode.DENIED_BY_POLICY,
                f"profile {spec.security_profile!r} requires offline networking",
            )
        if network.mode == NetworkMode.UNRESTRICTED and network.block_metadata:
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                "unrestricted Docker networking cannot guarantee metadata blocking",
            )

    def _validate_resources(self, spec: SandboxSpec) -> None:
        resources = spec.resources
        numeric = {
            "cpus": resources.cpus,
            "memory_mb": resources.memory_mb,
            "pids": resources.pids,
            "output_bytes": resources.output_bytes,
            "wall_time_seconds": resources.wall_time_seconds,
        }
        for name, value in numeric.items():
            if value is not None and value <= 0:
                raise SandboxBackendError(
                    SandboxErrorCode.SPEC_INVALID,
                    f"resource limit {name} must be positive",
                )
        if resources.disk_mb is not None:
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                "portable Docker writable-layer disk limits are unavailable",
            )
        if spec.security_profile in _ISOLATED_PROFILES:
            missing = [name for name, value in numeric.items() if value is None]
            if missing:
                raise SandboxBackendError(
                    SandboxErrorCode.DENIED_BY_POLICY,
                    "isolated Docker profiles require limits for: "
                    + ", ".join(missing),
                )

    def _validate_lifecycle(self, spec: SandboxSpec) -> None:
        if (
            spec.lifecycle.idle_timeout_seconds is not None
            or spec.lifecycle.ttl_seconds is not None
        ):
            raise SandboxBackendError(
                SandboxErrorCode.CAPABILITY_UNAVAILABLE,
                "DockerBackend lifecycle reaping is not implemented yet",
            )

    def _validate_container_user(self) -> None:
        user_id = self.container_user.split(":", 1)[0]
        if not user_id.isdigit() or int(user_id) <= 0:
            raise SandboxBackendError(
                SandboxErrorCode.DENIED_BY_POLICY,
                "Docker sandbox must use a non-root numeric user",
            )

    def _admit_mounts(self, spec: SandboxSpec) -> Tuple[MountSpec, ...]:
        workspace_host = _canonical_host_source(
            spec.workspace.host_path,
            require_directory=True,
        )
        workspace_target = _canonical_container_target(spec.workspace.sandbox_path)
        if workspace_host in _protected_host_paths():
            raise SandboxBackendError(
                SandboxErrorCode.DENIED_BY_POLICY,
                f"protected host path cannot be a workspace: {workspace_host}",
            )
        if spec.workspace.mode not in ("ro", "rw"):
            raise SandboxBackendError(
                SandboxErrorCode.SPEC_INVALID,
                f"invalid workspace mount mode: {spec.workspace.mode!r}",
            )
        admitted: List[MountSpec] = [
            MountSpec(
                host_path=workspace_host,
                sandbox_path=workspace_target,
                mode=spec.workspace.mode,
                kind="workspace",
                owner_scope=spec.owner_id,
            )
        ]

        for mount in spec.mounts:
            if mount.kind not in _MOUNT_KINDS:
                raise SandboxBackendError(
                    SandboxErrorCode.DENIED_BY_POLICY,
                    f"mount kind is not allowed: {mount.kind!r}",
                )
            if mount.kind == "test_fixture" and spec.owner_kind not in {
                "verifier",
                "verification",
            }:
                raise SandboxBackendError(
                    SandboxErrorCode.DENIED_BY_POLICY,
                    "test fixtures cannot be mounted into an Agent sandbox",
                )
            if mount.owner_scope != spec.owner_id:
                raise SandboxBackendError(
                    SandboxErrorCode.DENIED_BY_POLICY,
                    "mount owner_scope must exactly match the sandbox owner",
                )
            if mount.mode not in ("ro", "rw"):
                raise SandboxBackendError(
                    SandboxErrorCode.SPEC_INVALID,
                    f"invalid mount mode: {mount.mode!r}",
                )
            if mount.kind in {"runtime", "test_fixture"} and mount.mode != "ro":
                raise SandboxBackendError(
                    SandboxErrorCode.DENIED_BY_POLICY,
                    f"{mount.kind} mounts must be read-only",
                )
            host_path = _canonical_host_source(mount.host_path)
            if not any(_is_within(root, host_path) for root in self.allowed_mount_roots):
                raise SandboxBackendError(
                    SandboxErrorCode.DENIED_BY_POLICY,
                    f"mount source is outside configured allowlisted roots: {host_path}",
                )
            target = _canonical_container_target(mount.sandbox_path)
            admitted.append(
                MountSpec(
                    host_path=host_path,
                    sandbox_path=target,
                    mode=mount.mode,
                    kind=mount.kind,
                    owner_scope=mount.owner_scope,
                )
            )

        _reject_mount_conflicts(admitted)
        return tuple(admitted)

    def _ensure_backend_available(self) -> None:
        if self._server_version:
            return
        try:
            result = self._invoke(
                ("version", "--format", "{{.Server.Version}}"),
                timeout=10.0,
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxBackendError(
                SandboxErrorCode.BACKEND_UNAVAILABLE,
                "Docker daemon availability check timed out",
                retryable=True,
            ) from exc
        if result.returncode != 0:
            raise SandboxBackendError(
                SandboxErrorCode.BACKEND_UNAVAILABLE,
                _docker_error("Docker daemon is unavailable", result),
                retryable=True,
            )
        self._server_version = result.stdout.strip() or "unknown"

    def _inspect_image(self, spec: SandboxSpec) -> Tuple[str, str]:
        assert spec.image is not None
        image_ref = spec.image.reference
        if spec.image.digest:
            image_ref = f"{image_ref}@{spec.image.digest}"
        try:
            result = self._invoke(("image", "inspect", image_ref), timeout=30.0)
        except subprocess.TimeoutExpired as exc:
            raise SandboxBackendError(
                SandboxErrorCode.IMAGE_UNAVAILABLE,
                f"Docker image inspection timed out: {image_ref}",
                retryable=True,
            ) from exc
        if result.returncode != 0:
            raise SandboxBackendError(
                SandboxErrorCode.IMAGE_UNAVAILABLE,
                _docker_error(f"Docker image is unavailable: {image_ref}", result),
            )
        try:
            image_data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise SandboxBackendError(
                SandboxErrorCode.IMAGE_UNAVAILABLE,
                "Docker returned invalid image inspection data",
            ) from exc
        serialized = json.dumps(image_data, sort_keys=True)
        if spec.image.digest and spec.image.digest not in serialized:
            raise SandboxBackendError(
                SandboxErrorCode.IMAGE_UNAVAILABLE,
                "local image identity does not match the requested digest",
            )
        image_identity = _find_image_identity(image_data) or spec.image.digest
        return image_ref, image_identity

    def _build_create_args(
        self,
        spec: SandboxSpec,
        *,
        mounts: Sequence[MountSpec],
        image_ref: str,
        container_name: str,
    ) -> Tuple[str, ...]:
        args: List[str] = [
            "create",
            "--name",
            container_name,
            "--pull",
            "never",
            "--init",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--user",
            self.container_user,
            "--hostname",
            "claw-sandbox",
            "--ipc",
            "none",
            "--workdir",
            spec.workspace.sandbox_path,
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=256m",
            "--network",
            "none" if spec.network.mode == NetworkMode.NONE else "bridge",
        ]
        labels = {
            "com.claw.sandbox": "true",
            "com.claw.sandbox-id-hash": _short_hash(spec.sandbox_id),
            "com.claw.owner-id-hash": _short_hash(spec.owner_id),
            "com.claw.spec-hash": spec.fingerprint(),
        }
        for name, value in sorted(labels.items()):
            args.extend(("--label", f"{name}={value}"))

        resources = spec.resources
        if resources.cpus is not None:
            args.extend(("--cpus", str(resources.cpus)))
        if resources.memory_mb is not None:
            args.extend(("--memory", f"{resources.memory_mb}m"))
        if resources.pids is not None:
            args.extend(("--pids-limit", str(resources.pids)))
        for mount in mounts:
            args.extend(("--mount", _docker_mount_value(mount)))
        for name, value in sorted(self._container_environment(spec, {}).items()):
            args.extend(("--env", f"{name}={value}"))
        args.extend(
            (
                "--entrypoint",
                "/bin/sh",
                image_ref,
                "-c",
                "trap 'exit 0' TERM INT; while :; do sleep 3600; done",
            )
        )
        return tuple(args)

    def _build_exec_args(
        self,
        handle: SandboxHandle,
        request: ExecRequest,
    ) -> Tuple[str, ...]:
        cwd = self._container_cwd(handle, request.cwd)
        args: List[str] = ["exec", "--workdir", cwd]
        spec = self._specs[handle.sandbox_id]
        request_environment = self._container_environment(
            spec,
            request_values=request.environment,
            inherit=False,
        )
        for name, value in sorted(request_environment.items()):
            args.extend(("--env", f"{name}={value}"))
        args.append(self._container(handle))
        if isinstance(request.command, str):
            if not request.command.strip():
                raise SandboxBackendError(
                    SandboxErrorCode.SPEC_INVALID,
                    "command must not be empty",
                )
            args.extend(("/bin/sh", "-lc", request.command))
        else:
            if not request.command:
                raise SandboxBackendError(
                    SandboxErrorCode.SPEC_INVALID,
                    "command argv must not be empty",
                )
            args.extend(str(part) for part in request.command)
        return tuple(args)

    def _container_environment(
        self,
        spec: SandboxSpec,
        request_values: Mapping[str, str],
        *,
        inherit: bool = True,
    ) -> Dict[str, str]:
        values = dict(spec.environment.values) if inherit else {}
        values.update({str(key): str(value) for key, value in request_values.items()})
        inherited_names = spec.environment.inherited_names if inherit else ()
        try:
            return sandbox_subprocess_environment(
                cwd=spec.workspace.host_path,
                inherited_names=inherited_names,
                values=values,
                managed_path=_DOCKER_CONTAINER_PATH,
                managed_home=spec.workspace.sandbox_path,
                managed_tmpdir="/tmp",
            )
        except ValueError as exc:
            raise SandboxBackendError(
                SandboxErrorCode.DENIED_BY_POLICY,
                str(exc),
            ) from exc

    def _container_cwd(self, handle: SandboxHandle, requested: str) -> str:
        spec = self._specs[handle.sandbox_id]
        root = _canonical_container_target(spec.workspace.sandbox_path)
        if posixpath.isabs(requested):
            candidate = _canonical_container_target(requested)
        else:
            candidate = _canonical_container_target(
                posixpath.normpath(posixpath.join(root, requested))
            )
        if not _is_container_path_within(root, candidate):
            raise SandboxBackendError(
                SandboxErrorCode.DENIED_BY_POLICY,
                f"execution cwd is outside the container workspace: {requested}",
            )
        return candidate

    def _effective_limits(
        self,
        handle: SandboxHandle,
        request: ExecRequest,
    ) -> Tuple[float, Optional[int]]:
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
        resources = self._specs[handle.sandbox_id].resources
        timeout = request.timeout_seconds
        if resources.wall_time_seconds is not None:
            timeout = min(timeout, resources.wall_time_seconds)
        output_limit = request.output_limit_bytes
        if resources.output_bytes is not None:
            output_limit = (
                resources.output_bytes
                if output_limit is None
                else min(output_limit, resources.output_bytes)
            )
        return timeout, output_limit

    def _read_container_state(self, handle: SandboxHandle) -> SandboxState:
        container = self._container(handle)
        try:
            result = self._invoke(
                ("inspect", "--format", "{{json .State}}", container),
                timeout=10.0,
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxBackendError(
                SandboxErrorCode.BACKEND_UNAVAILABLE,
                f"Docker timed out while inspecting {container}",
                retryable=True,
            ) from exc
        if result.returncode != 0:
            raise SandboxBackendError(
                SandboxErrorCode.INSTANCE_LOST,
                _docker_error("sandbox container is unavailable", result),
                retryable=_looks_transient(result.stderr),
            )
        try:
            state_data = json.loads(result.stdout)
            docker_state = str(state_data.get("Status", "")).lower()
        except (AttributeError, json.JSONDecodeError) as exc:
            raise SandboxBackendError(
                SandboxErrorCode.BACKEND_UNAVAILABLE,
                "Docker returned invalid container state data",
                retryable=True,
            ) from exc
        mapping = {
            "created": SandboxState.READY,
            "running": SandboxState.RUNNING,
            "paused": SandboxState.STOPPED,
            "restarting": SandboxState.FAILED,
            "removing": SandboxState.FAILED,
            "exited": SandboxState.STOPPED,
            "dead": SandboxState.FAILED,
        }
        return mapping.get(docker_state, SandboxState.FAILED)

    def _kill_after_timeout(self, handle: SandboxHandle) -> None:
        container = self._container(handle)
        try:
            result = self._invoke(("kill", container), timeout=10.0)
        except (SandboxBackendError, subprocess.TimeoutExpired):
            handle.state = SandboxState.FAILED
            return
        handle.state = (
            SandboxState.STOPPED
            if result.returncode == 0 or "is not running" in result.stderr
            else SandboxState.FAILED
        )

    def _invoke(self, args: Sequence[str], timeout: float) -> DockerCLIResult:
        argv = (self.docker_command, *tuple(str(item) for item in args))
        if self._runner is not None:
            return self._runner(argv, timeout)
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=runtime_subprocess_environment(),
            )
        except FileNotFoundError as exc:
            raise SandboxBackendError(
                SandboxErrorCode.BACKEND_UNAVAILABLE,
                f"Docker CLI was not found: {self.docker_command}",
            ) from exc
        except OSError as exc:
            raise SandboxBackendError(
                SandboxErrorCode.BACKEND_UNAVAILABLE,
                f"Docker CLI could not be started: {exc}",
                retryable=True,
            ) from exc
        return DockerCLIResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def _container(self, handle: SandboxHandle) -> str:
        container = handle.backend_metadata.get("container_name", "")
        if not container:
            raise SandboxBackendError(
                SandboxErrorCode.INSTANCE_LOST,
                f"sandbox {handle.sandbox_id!r} has no Docker container identity",
            )
        return container

    def _require_known(self, handle: SandboxHandle) -> None:
        if self._handles.get(handle.sandbox_id) is not handle:
            raise SandboxBackendError(
                SandboxErrorCode.INSTANCE_LOST,
                f"unknown sandbox handle: {handle.sandbox_id}",
            )

    def _require_running(self, handle: SandboxHandle) -> None:
        self._require_known(handle)
        state = self._read_container_state(handle)
        handle.state = state
        if state != SandboxState.RUNNING:
            raise SandboxBackendError(
                SandboxErrorCode.INSTANCE_LOST,
                f"sandbox {handle.sandbox_id!r} is not running ({state.value})",
            )


def _default_container_user() -> str:
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        uid = os.getuid()
        gid = os.getgid()
        if uid > 0:
            return f"{uid}:{gid}"
    return "65532:65532"


def _canonical_host_source(path: str, *, require_directory: bool = False) -> str:
    raw = str(path)
    if not raw or any(character in raw for character in ("\x00", "\n", ",")):
        raise SandboxBackendError(
            SandboxErrorCode.SPEC_INVALID,
            f"invalid Docker mount source: {raw!r}",
        )
    resolved = os.path.realpath(os.path.abspath(raw))
    if not os.path.exists(resolved):
        raise SandboxBackendError(
            SandboxErrorCode.SPEC_INVALID,
            f"Docker mount source does not exist: {resolved}",
        )
    metadata = os.stat(resolved)
    if require_directory and not stat.S_ISDIR(metadata.st_mode):
        raise SandboxBackendError(
            SandboxErrorCode.SPEC_INVALID,
            f"Docker workspace must be a directory: {resolved}",
        )
    if not stat.S_ISDIR(metadata.st_mode) and not stat.S_ISREG(metadata.st_mode):
        raise SandboxBackendError(
            SandboxErrorCode.DENIED_BY_POLICY,
            f"special files cannot be mounted into a sandbox: {resolved}",
        )
    if resolved in _protected_host_paths():
        raise SandboxBackendError(
            SandboxErrorCode.DENIED_BY_POLICY,
            f"protected host path cannot be mounted: {resolved}",
        )
    return resolved


def _canonical_container_target(path: str) -> str:
    raw = str(path)
    if not raw or any(character in raw for character in ("\x00", "\n", ",")):
        raise SandboxBackendError(
            SandboxErrorCode.SPEC_INVALID,
            f"invalid container mount target: {raw!r}",
        )
    if not posixpath.isabs(raw):
        raise SandboxBackendError(
            SandboxErrorCode.SPEC_INVALID,
            f"container path must be absolute: {raw!r}",
        )
    normalized = posixpath.normpath(raw)
    if normalized != raw:
        raise SandboxBackendError(
            SandboxErrorCode.SPEC_INVALID,
            f"container path must be canonical: {raw!r}",
        )
    if normalized == "/" or any(
        _is_container_path_within(protected, normalized)
        for protected in _PROTECTED_CONTAINER_TARGETS
    ):
        raise SandboxBackendError(
            SandboxErrorCode.DENIED_BY_POLICY,
            f"protected container path cannot be mounted or selected: {normalized}",
        )
    return normalized


def _reject_mount_conflicts(mounts: Sequence[MountSpec]) -> None:
    for index, mount in enumerate(mounts):
        for other in mounts[index + 1 :]:
            if (
                _is_container_path_within(mount.sandbox_path, other.sandbox_path)
                or _is_container_path_within(other.sandbox_path, mount.sandbox_path)
            ):
                raise SandboxBackendError(
                    SandboxErrorCode.DENIED_BY_POLICY,
                    "overlapping container mount targets are not allowed: "
                    f"{mount.sandbox_path}, {other.sandbox_path}",
                )
            if _is_within(mount.host_path, other.host_path) or _is_within(
                other.host_path, mount.host_path
            ):
                raise SandboxBackendError(
                    SandboxErrorCode.DENIED_BY_POLICY,
                    "overlapping host mount sources are not allowed: "
                    f"{mount.host_path}, {other.host_path}",
                )


def _docker_mount_value(mount: MountSpec) -> str:
    value = f"type=bind,src={mount.host_path},dst={mount.sandbox_path}"
    if mount.mode == "ro":
        value += ",readonly"
    return value


def _protected_host_paths() -> set:
    paths = set(_PROTECTED_MOUNT_PATHS)
    home = os.path.realpath(os.path.expanduser("~"))
    if home and home != "/":
        paths.add(home)
    return paths


def _is_within(root: str, candidate: str) -> bool:
    try:
        return os.path.commonpath([root, candidate]) == root
    except ValueError:
        return False


def _is_container_path_within(root: str, candidate: str) -> bool:
    root_parts = tuple(part for part in root.split("/") if part)
    candidate_parts = tuple(part for part in candidate.split("/") if part)
    return candidate_parts[: len(root_parts)] == root_parts


def _container_name(sandbox_id: str, generation: int) -> str:
    return f"claw-sbx-{_short_hash(sandbox_id)}-g{generation}"


def _short_hash(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:20]


def _find_image_identity(value: object) -> str:
    if isinstance(value, list):
        for item in value:
            found = _find_image_identity(item)
            if found:
                return found
    if isinstance(value, dict):
        repo_digests = value.get("RepoDigests")
        if isinstance(repo_digests, list) and repo_digests:
            return str(repo_digests[0])
        image_id = value.get("Id")
        if image_id:
            return str(image_id)
    return ""


def _status(handle: SandboxHandle) -> SandboxStatus:
    return SandboxStatus(
        sandbox_id=handle.sandbox_id,
        owner_kind=handle.owner_kind,
        owner_id=handle.owner_id,
        backend_name=handle.backend_name,
        state=handle.state,
        spec_hash=handle.spec_hash,
        generation=handle.generation,
    )


def _docker_error(prefix: str, result: DockerCLIResult) -> str:
    detail = (result.stderr or result.stdout).strip()
    return prefix if not detail else f"{prefix}: {detail}"


def _looks_transient(detail: str) -> bool:
    normalized = detail.lower()
    return any(
        marker in normalized
        for marker in ("connection refused", "deadline exceeded", "temporarily unavailable")
    )


def _limit_output(
    stdout: str,
    stderr: str,
    limit_bytes: Optional[int],
) -> Tuple[str, str, bool]:
    if limit_bytes is None:
        return stdout, stderr, False
    stdout_bytes = stdout.encode("utf-8", errors="replace")
    kept_stdout = stdout_bytes[:limit_bytes]
    remaining = max(0, limit_bytes - len(kept_stdout))
    stderr_bytes = stderr.encode("utf-8", errors="replace")
    kept_stderr = stderr_bytes[:remaining]
    truncated = (
        len(kept_stdout) < len(stdout_bytes)
        or len(kept_stderr) < len(stderr_bytes)
    )
    return (
        kept_stdout.decode("utf-8", errors="replace"),
        kept_stderr.decode("utf-8", errors="replace"),
        truncated,
    )


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


__all__ = ["DockerBackend", "DockerCLIResult", "DockerRunner"]
