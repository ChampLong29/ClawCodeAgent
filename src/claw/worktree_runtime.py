"""Worktree runtime for CodeAgent."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


@dataclass
class WorktreeInfo:
    """Information about a worktree."""
    name: str
    branch: str
    path: str
    is_current: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "branch": self.branch,
            "path": self.path,
            "is_current": self.is_current,
        }


class WorktreeRuntime(RuntimeBase):
    """Git worktree management runtime.

    State path (priority order):
    1. <git_common_dir>/claw_worktree_runtime.json
    2. .port_sessions/worktree_runtime.json
    """

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.worktrees = self._discover()
        self._state_path = self._get_state_path()

    def _get_git_common_dir(self) -> Optional[str]:
        """Get the git common directory."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _get_state_path(self) -> str:
        """Determine the state file path."""
        git_common = self._get_git_common_dir()
        if git_common:
            # Use git common dir
            return os.path.join(git_common, "claw_worktree_runtime.json")

        # Fall back to .port_sessions
        sessions_dir = os.path.join(self.cwd, ".port_sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        return os.path.join(sessions_dir, "worktree_runtime.json")

    def _discover(self) -> List[WorktreeInfo]:
        """Discover worktrees."""
        worktrees = []

        try:
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                current_branch = ""
                for line in result.stdout.strip().split("\n"):
                    if line.startswith("worktree "):
                        path = line[9:]
                    elif line.startswith("branch "):
                        branch = line[8:]
                        if path:
                            worktrees.append(WorktreeInfo(
                                name=os.path.basename(path),
                                branch=branch,
                                path=path,
                            ))
        except Exception:
            pass

        return worktrees

    def list_worktrees(self) -> List[Dict[str, Any]]:
        """List all worktrees."""
        return [w.to_dict() for w in self.worktrees]

    def create_worktree(self, name: str, branch: str, path: str) -> bool:
        """Create a new worktree."""
        try:
            result = subprocess.run(
                ["git", "worktree", "add", "-b", f"claw/{name}", path],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                self.worktrees.append(WorktreeInfo(
                    name=name,
                    branch=f"claw/{name}",
                    path=path,
                ))
                self._save_state()
                return True
        except Exception:
            pass
        return False

    def remove_worktree(self, name: str) -> bool:
        """Remove a worktree."""
        for wt in self.worktrees:
            if wt.name == name:
                try:
                    result = subprocess.run(
                        ["git", "worktree", "remove", wt.path],
                        cwd=self.cwd,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result.returncode == 0:
                        self.worktrees.remove(wt)
                        self._save_state()
                        return True
                except Exception:
                    pass
                return False
        return False

    def _save_state(self) -> None:
        """Save state to file."""
        state = {
            "worktrees": [w.to_dict() for w in self.worktrees],
        }
        with open(self._state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "worktrees": [w.to_dict() for w in self.worktrees],
            "count": len(self.worktrees),
            "state_path": self._state_path,
        }

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.worktrees:
            return "No git worktrees."

        return f"[Git Worktrees] {len(self.worktrees)} worktree(s)"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.worktrees:
            return ""

        return "Git worktrees are available for parallel development."