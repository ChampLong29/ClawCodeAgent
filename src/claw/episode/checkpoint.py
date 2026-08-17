"""Atomic Git/session/runtime checkpoints for isolated episodes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

from ..agent_session import AgentSession
from .state import atomic_write_json, utc_now


CHECKPOINT_SCHEMA_VERSION = "episode_checkpoint.v1"
_IGNORED_NAMES = {
    ".git", ".port_sessions", "__pycache__", ".pytest_cache", ".mypy_cache"
}


class CheckpointIntegrityError(RuntimeError):
    """Raised when restored files do not match a committed checkpoint."""


def validate_workspace_symlinks(root: Union[str, os.PathLike[str]]) -> None:
    """Reject links that lexically escape a workspace before preserving them."""
    base = os.path.abspath(str(root))
    for current, directories, filenames in os.walk(base, followlinks=False):
        for name in [*directories, *filenames]:
            candidate = Path(current) / name
            if not candidate.is_symlink():
                continue
            target = os.readlink(candidate)
            if os.path.isabs(target):
                raise CheckpointIntegrityError(
                    f"workspace symlink must be relative: {candidate}"
                )
            lexical_target = os.path.abspath(os.path.join(current, target))
            try:
                inside = os.path.commonpath([base, lexical_target]) == base
            except ValueError:
                inside = False
            if not inside:
                raise CheckpointIntegrityError(
                    f"workspace symlink escapes root: {candidate}"
                )


def workspace_hash(
    root: Union[str, os.PathLike[str]],
    *,
    ignored_names: Optional[Iterable[str]] = None,
) -> str:
    """Hash relative paths, file modes, and bytes in a workspace."""
    base = Path(root).resolve()
    ignored = set(ignored_names or _IGNORED_NAMES)
    digest = hashlib.sha256()
    entries = []
    for current, directories, filenames in os.walk(base, followlinks=False):
        directories[:] = sorted(
            name for name in directories if name not in ignored
        )
        current_path = Path(current)
        for name in sorted(filenames):
            if name not in ignored:
                entries.append(current_path / name)
        for name in directories:
            candidate = current_path / name
            if candidate.is_symlink():
                entries.append(candidate)

    for path in sorted(entries, key=lambda item: item.relative_to(base).as_posix()):
        relative = path.relative_to(base).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8"))
        else:
            executable = bool(path.stat().st_mode & stat.S_IXUSR)
            digest.update(b"755" if executable else b"644")
            digest.update(b"\0")
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _run_git(workspace: Path, args: List[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise CheckpointIntegrityError(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def initialize_git(workspace: Union[str, os.PathLike[str]]) -> str:
    """Initialize a self-contained repository with a deterministic local identity."""
    path = Path(workspace).resolve()
    if not (path / ".git").exists():
        _run_git(path, ["init", "-q"])
    exclude = path / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing_excludes = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    managed_excludes = [
        ".port_sessions/", "__pycache__/", ".pytest_cache/", ".mypy_cache/"
    ]
    missing = [item for item in managed_excludes if item not in existing_excludes]
    if missing:
        with exclude.open("a", encoding="utf-8", newline="\n") as handle:
            if existing_excludes and not existing_excludes.endswith("\n"):
                handle.write("\n")
            handle.write("\n".join(missing) + "\n")
    _run_git(path, ["config", "user.email", "claw-episode@localhost"])
    _run_git(path, ["config", "user.name", "Claw Episode"])
    _run_git(path, ["add", "-A"])
    _run_git(path, ["commit", "-q", "--allow-empty", "-m", "episode: initial state"])
    return _run_git(path, ["rev-parse", "HEAD"])


@dataclass
class CheckpointSnapshot:
    checkpoint_id: str
    episode_id: str
    git_commit: str
    workspace_hash: str
    session_state: Optional[Dict[str, Any]]
    runtime_states: Dict[str, Any]
    runtime_config: Dict[str, Any] = field(default_factory=dict)
    environment: Dict[str, str] = field(default_factory=dict)
    unfinished_process_ids: List[int] = field(default_factory=list)
    label: str = ""
    created_at: str = field(default_factory=utc_now)
    committed: bool = True
    schema_version: str = CHECKPOINT_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointIntegrityError(
                f"unsupported checkpoint schema: {self.schema_version}"
            )
        for name in ("checkpoint_id", "episode_id", "git_commit", "workspace_hash"):
            if not str(getattr(self, name)).strip():
                raise CheckpointIntegrityError(f"{name} must not be empty")
        if not self.committed:
            raise CheckpointIntegrityError("checkpoint is not committed")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckpointSnapshot":
        snapshot = cls(**data)
        snapshot.validate()
        return snapshot


class CheckpointManager:
    """Create and restore checkpoints spanning code, session, and runtime state."""

    def __init__(
        self,
        *,
        episode_id: str,
        episode_dir: Union[str, os.PathLike[str]],
        workspace: Union[str, os.PathLike[str]],
        environment_allowlist: Optional[Iterable[str]] = None,
    ):
        self.episode_id = episode_id
        self.episode_dir = Path(episode_dir).resolve()
        self.workspace = Path(workspace).resolve()
        self.checkpoint_dir = self.episode_dir / "checkpoints"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.environment_allowlist = tuple(environment_allowlist or ())

    def create(
        self,
        *,
        label: str = "",
        agent_session: Optional[Union[AgentSession, Dict[str, Any]]] = None,
        runtime_states: Optional[Dict[str, Any]] = None,
        runtime_config: Optional[Dict[str, Any]] = None,
        unfinished_process_ids: Optional[List[int]] = None,
        checkpoint_id: Optional[str] = None,
    ) -> CheckpointSnapshot:
        checkpoint_id = checkpoint_id or f"cp_{uuid.uuid4().hex[:20]}"
        _run_git(self.workspace, ["add", "-A"])
        _run_git(
            self.workspace,
            [
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                f"episode checkpoint: {checkpoint_id} {label}".strip(),
            ],
        )
        commit = _run_git(self.workspace, ["rev-parse", "HEAD"])
        session_state = (
            agent_session.to_dict()
            if isinstance(agent_session, AgentSession)
            else dict(agent_session)
            if agent_session is not None
            else None
        )
        environment = {
            name: os.environ[name]
            for name in self.environment_allowlist
            if name in os.environ
        }
        snapshot = CheckpointSnapshot(
            checkpoint_id=checkpoint_id,
            episode_id=self.episode_id,
            git_commit=commit,
            workspace_hash=workspace_hash(self.workspace),
            session_state=session_state,
            runtime_states=dict(runtime_states or {}),
            runtime_config=dict(runtime_config or {}),
            environment=environment,
            unfinished_process_ids=list(unfinished_process_ids or []),
            label=label,
        )
        snapshot.validate()
        atomic_write_json(self.path_for(checkpoint_id), snapshot.to_dict())
        return snapshot

    def load(self, checkpoint_id: str) -> CheckpointSnapshot:
        with self.path_for(checkpoint_id).open("r", encoding="utf-8") as handle:
            return CheckpointSnapshot.from_dict(json.load(handle))

    def restore(
        self,
        checkpoint_id: str,
        *,
        agent_session: Optional[AgentSession] = None,
        runtime_restorers: Optional[Dict[str, Callable[[Any], None]]] = None,
        environment_restorer: Optional[Callable[[Dict[str, str]], None]] = None,
    ) -> Tuple[Optional[AgentSession], Dict[str, Any]]:
        snapshot = self.load(checkpoint_id)
        if snapshot.episode_id != self.episode_id:
            raise CheckpointIntegrityError(
                f"checkpoint belongs to episode {snapshot.episode_id}"
            )

        _run_git(self.workspace, ["reset", "--hard", snapshot.git_commit])
        _run_git(self.workspace, ["clean", "-fdx"])
        actual_hash = workspace_hash(self.workspace)
        if actual_hash != snapshot.workspace_hash:
            raise CheckpointIntegrityError(
                f"workspace hash mismatch after reset: "
                f"expected {snapshot.workspace_hash}, got {actual_hash}"
            )

        restored_session: Optional[AgentSession] = None
        if snapshot.session_state is not None:
            restored_session = AgentSession.from_dict(snapshot.session_state)
            if agent_session is not None:
                agent_session.__dict__.clear()
                agent_session.__dict__.update(restored_session.__dict__)
                restored_session = agent_session

        for name, state in snapshot.runtime_states.items():
            restorer = (runtime_restorers or {}).get(name)
            if restorer is not None:
                restorer(state)
        if environment_restorer is not None:
            environment_restorer(dict(snapshot.environment))

        return restored_session, dict(snapshot.runtime_states)

    def path_for(self, checkpoint_id: str) -> Path:
        if (
            not checkpoint_id
            or "/" in checkpoint_id
            or "\\" in checkpoint_id
            or checkpoint_id in {".", ".."}
        ):
            raise ValueError("checkpoint_id must be a simple path-safe identifier")
        return self.checkpoint_dir / f"{checkpoint_id}.json"
