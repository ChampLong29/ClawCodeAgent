"""Remote runtime for CodeAgent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


@dataclass
class RemoteProfile:
    """A remote connection profile."""
    name: str
    mode: str = "ssh"  # ssh, teleport
    target: str = ""
    workspace_cwd: Optional[str] = None
    session_url: Optional[str] = None
    description: str = ""

    # Support camelCase
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RemoteProfile:
        return cls(
            name=data.get("name", ""),
            mode=data.get("mode", "ssh"),
            target=data.get("target", ""),
            workspace_cwd=data.get("workspaceCwd") or data.get("workspace_cwd"),
            session_url=data.get("sessionUrl") or data.get("session_url"),
            description=data.get("description", ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "mode": self.mode,
            "target": self.target,
            "workspace_cwd": self.workspace_cwd,
            "session_url": self.session_url,
            "description": self.description,
        }


class RemoteRuntime(RuntimeBase):
    """Remote connection runtime.

    Discovery paths (walk-up behavior):
    - .claw-remote.json
    - .remote.json
    - .codex-remote.json
    - remote.json
    """

    CONFIG_FILES = [".claw-remote.json", ".remote.json", ".codex-remote.json", "remote.json"]

    def __init__(
        self,
        cwd: str,
        additional_directories: Optional[List[str]] = None,
    ):
        self.cwd = cwd
        self.additional_directories = additional_directories or []
        self.profiles = self._discover()

    def _discover(self) -> List[RemoteProfile]:
        """Discover remote profiles."""
        # Check additional directories first
        for directory in self.additional_directories:
            profiles = self._check_directory(directory)
            if profiles:
                return profiles

        # Walk up from cwd
        current_dir = os.path.abspath(self.cwd)

        while True:
            profiles = self._check_directory(current_dir)
            if profiles:
                return profiles

            parent = os.path.dirname(current_dir)
            if parent == current_dir:
                break
            current_dir = parent

        return []

    def _check_directory(self, directory: str) -> List[RemoteProfile]:
        """Check a directory for remote configuration."""
        for filename in self.CONFIG_FILES:
            filepath = os.path.join(directory, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return self._parse_profiles(data)
                except (json.JSONDecodeError, OSError):
                    continue
        return []

    def _parse_profiles(self, data: Dict[str, Any]) -> List[RemoteProfile]:
        """Parse profiles from configuration."""
        profiles = []
        for p_data in data.get("profiles", []):
            profiles.append(RemoteProfile.from_dict(p_data))
        return profiles

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "profiles": [p.to_dict() for p in self.profiles],
            "count": len(self.profiles),
        }

    def list_profiles(self) -> List[Dict[str, Any]]:
        """List all profiles."""
        return [p.to_dict() for p in self.profiles]

    def get_profile(self, name: str) -> Optional[RemoteProfile]:
        """Get a profile by name."""
        for p in self.profiles:
            if p.name == name:
                return p
        return None

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.profiles:
            return "No remote profiles configured."

        names = [p.name for p in self.profiles]
        return f"[Remote Profiles] {', '.join(names)}"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.profiles:
            return ""

        ssh_profiles = [p for p in self.profiles if p.mode == "ssh"]
        teleport_profiles = [p for p in self.profiles if p.mode == "teleport"]

        parts = []
        if ssh_profiles:
            parts.append(f"SSH profiles: {', '.join(p.name for p in ssh_profiles)}")
        if teleport_profiles:
            parts.append(f"Teleport profiles: {', '.join(p.name for p in teleport_profiles)}")

        return "\n".join(parts)