"""Cross-platform OCI container command execution for isolated Episodes.

The implementation intentionally targets the common Docker/Podman CLI surface
instead of a platform-specific daemon SDK.  Docker Desktop and Podman Desktop
therefore use the same audited invocation on Windows, macOS, and Linux.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence


class ContainerRuntimeError(RuntimeError):
    """Raised when a configured OCI execution boundary is unavailable."""


@dataclass(frozen=True)
class OCIContainerConfig:
    """Portable, deny-by-default settings for one-shot OCI commands."""

    image: str
    engine: str = "auto"
    workspace_target: str = "/workspace"
    network: str = "none"
    cpus: float = 2.0
    memory: str = "4g"
    pids_limit: int = 256
    tmpfs_size: str = "256m"
    read_only_root: bool = True
    ephemeral_workspace: bool = True
    user: Optional[str] = None

    def validate(self) -> None:
        if not self.image.strip() or any(c in self.image for c in "\r\n\0"):
            raise ValueError("container image must be a non-empty single-line value")
        if self.engine not in {"auto", "docker", "podman"}:
            raise ValueError("container engine must be auto, docker, or podman")
        if not self.workspace_target.startswith("/"):
            raise ValueError("container workspace_target must be absolute")
        if self.network != "none":
            raise ValueError("the isolated OCI profile currently requires network=none")
        if self.cpus <= 0:
            raise ValueError("container cpus must be positive")
        if self.pids_limit <= 0:
            raise ValueError("container pids_limit must be positive")
        for name, value in (("memory", self.memory), ("tmpfs_size", self.tmpfs_size)):
            if not re.fullmatch(r"[1-9][0-9]*[bkmgBKMG]?", value):
                raise ValueError(f"container {name} must be a positive size")
        if self.user is not None and any(c in self.user for c in "\r\n\0"):
            raise ValueError("container user must be a single-line value")


class OCIContainerRunner:
    """Run shell commands in disposable Docker or Podman containers."""

    persistent_workspace_mutations = False

    def __init__(
        self,
        config: OCIContainerConfig,
        *,
        executable: Optional[str] = None,
        executor: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        config.validate()
        self.config = config
        self.executable = executable or self._resolve_engine(config.engine)
        self._executor = executor
        self._probe: Dict[str, Any] = {}

    def _effective_user(self) -> Optional[str]:
        if self.config.user:
            return self.config.user
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            uid = int(os.getuid())
            gid = int(os.getgid())
            if uid > 0:
                return f"{uid}:{gid}"
        return None

    @staticmethod
    def _resolve_engine(engine: str) -> str:
        if engine == "auto":
            candidates: Sequence[str] = (
                "docker.exe",
                "docker",
                "podman.exe",
                "podman",
            )
        else:
            candidates = (f"{engine}.exe", engine)
        for candidate in candidates:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        requested = "Docker or Podman" if engine == "auto" else engine
        raise ContainerRuntimeError(f"{requested} CLI was not found on PATH")

    @property
    def engine_name(self) -> str:
        name = Path(self.executable).name.lower()
        return "podman" if "podman" in name else "docker"

    def _mount_source(self, workspace: Path) -> str:
        """Translate WSL paths when invoking a Windows container CLI."""
        source = os.fspath(workspace)
        if not self.executable.lower().endswith(".exe") or os.name == "nt":
            if not self.executable.lower().endswith(".exe"):
                return source
        normalized_source = source.replace("\\", "/")
        drive_mount = re.match(
            r"^/mnt/([a-zA-Z])(?:/(.*))?$", normalized_source
        )
        if drive_mount:
            drive = drive_mount.group(1).upper()
            remainder = (drive_mount.group(2) or "").replace("/", "\\")
            return f"{drive}:\\{remainder}" if remainder else f"{drive}:\\"
        distro = os.environ.get("WSL_DISTRO_NAME", "").strip()
        if distro and source.startswith("/"):
            remainder = source.lstrip("/").replace("/", "\\")
            return f"\\\\wsl.localhost\\{distro}\\{remainder}"
        return source

    def _translate_command(self, command: str, workspace: Path) -> str:
        """Map model-visible host workspace paths to the container mount."""
        variants = {
            os.fspath(workspace),
            workspace.as_posix(),
            self._mount_source(workspace),
        }
        translated = command
        for source in sorted((item for item in variants if item), key=len, reverse=True):
            translated = translated.replace(source, self.config.workspace_target)
        return translated

    def result_metadata(
        self, result: subprocess.CompletedProcess
    ) -> Dict[str, Any]:
        args = result.args if isinstance(result.args, (list, tuple)) else ()
        return {
            "execution_backend": "oci-container",
            "container_engine": self.engine_name,
            "container_image_id": self._probe.get("image_id", ""),
            "container_image_digest": self._probe.get("image_digest", ""),
            "effective_command": str(args[-1]) if args else "",
            "workspace_mutations": (
                "discarded" if self.config.ephemeral_workspace else "persistent"
            ),
        }

    def probe(self) -> Dict[str, Any]:
        """Verify daemon/image availability and pin the observed image identity."""
        version = self._executor(
            [self.executable, "version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if version.returncode != 0:
            details = "\n".join(
                part for part in (version.stdout.strip(), version.stderr.strip())
                if part
            )
            raise ContainerRuntimeError(
                f"{self.engine_name} daemon is unavailable "
                f"(exit {version.returncode}): {details}"
            )
        inspected = self._executor(
            [self.executable, "image", "inspect", self.config.image],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if inspected.returncode != 0:
            details = "\n".join(
                part for part in (inspected.stdout.strip(), inspected.stderr.strip())
                if part
            )
            raise ContainerRuntimeError(
                f"container image is unavailable locally "
                f"(exit {inspected.returncode}): {self.config.image}: {details}"
            )
        try:
            image_data = json.loads(inspected.stdout)[0]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ContainerRuntimeError("container image inspection returned invalid JSON") from exc
        repo_digests = image_data.get("RepoDigests") or []
        self._probe = {
            "engine_version_output": version.stdout.strip()[-4000:],
            "image_id": str(image_data.get("Id", "")),
            "image_digest": str(repo_digests[0]) if repo_digests else "",
        }
        return self.describe()

    def describe(self) -> Dict[str, Any]:
        return {
            "kind": "oci-container",
            "engine": self.engine_name,
            "config": asdict(self.config),
            "effective_user": self._effective_user(),
            **self._probe,
        }

    def run(
        self,
        command: str,
        *,
        cwd: str,
        timeout: float,
    ) -> subprocess.CompletedProcess:
        """Execute one shell command with only the workspace bind-mounted."""
        if not self._probe:
            self.probe()
        workspace = Path(cwd).resolve()
        if not workspace.is_dir():
            raise ContainerRuntimeError(f"container workspace does not exist: {workspace}")
        if any(c in os.fspath(workspace) for c in ",\r\n\0"):
            raise ContainerRuntimeError(
                "container workspace path contains an unsupported mount character"
            )
        if not self.config.ephemeral_workspace:
            raise ContainerRuntimeError(
                "persistent workspace mounts are not allowed for agent shell execution"
            )
        name = f"claw-episode-{uuid.uuid4().hex[:16]}"
        # Keep the disposable copy on the same host-visible volume as the
        # Episode workspace.  In particular, Docker Desktop can bind a WSL
        # /mnt/<drive> path through docker.exe even when direct distro mounts
        # such as \\wsl.localhost\... are unavailable.
        temporary = tempfile.TemporaryDirectory(
            prefix="claw-shell-", dir=workspace.parent
        )
        disposable_workspace = Path(temporary.name) / "workspace"

        def ignore(_directory: str, names: Sequence[str]) -> Sequence[str]:
            blocked = {"__pycache__", ".pytest_cache", ".mypy_cache", ".port_sessions"}
            return [name for name in names if name in blocked]

        shutil.copytree(
            workspace,
            disposable_workspace,
            symlinks=True,
            ignore=ignore,
        )
        effective_command = self._translate_command(command, workspace)
        mount_source = self._mount_source(disposable_workspace)
        mount = (
            f"type=bind,source={mount_source},"
            f"target={self.config.workspace_target}"
        )
        args = [
            self.executable,
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            self.config.network,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.config.pids_limit),
            "--memory",
            self.config.memory,
            "--cpus",
            str(self.config.cpus),
            "--mount",
            mount,
            "--workdir",
            self.config.workspace_target,
        ]
        if self.config.read_only_root:
            args.extend(
                [
                    "--read-only",
                    "--tmpfs",
                    f"/tmp:rw,noexec,nosuid,size={self.config.tmpfs_size}",
                ]
            )
        effective_user = self._effective_user()
        if effective_user:
            args.extend(["--user", effective_user])
        args.extend([self.config.image, "/bin/sh", "-lc", effective_command])
        try:
            return self._executor(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, KeyboardInterrupt, OSError):
            self._cleanup(name)
            raise
        finally:
            temporary.cleanup()

    def _cleanup(self, container_name: str) -> None:
        self._executor(
            [self.executable, "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
