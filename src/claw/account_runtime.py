"""Account runtime for CodeAgent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


@dataclass
class AccountProfile:
    """An account profile configuration."""
    name: str
    provider: str = "openai"
    identity: str = ""
    auth_mode: str = "api_key"
    org: Optional[str] = None
    api_base: Optional[str] = None

    # Support camelCase
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AccountProfile:
        return cls(
            name=data.get("name", ""),
            provider=data.get("provider", "openai"),
            identity=data.get("identity", ""),
            auth_mode=data.get("authMode") or data.get("auth_mode", "api_key"),
            org=data.get("org"),
            api_base=data.get("apiBase") or data.get("api_base"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "identity": self.identity,
            "auth_mode": self.auth_mode,
            "org": self.org,
            "api_base": self.api_base,
        }


class AccountRuntime(RuntimeBase):
    """Account profile runtime.

    Discovery paths:
    - .claw-account.json
    - .claude/account.json
    - .claude/auth.json
    """

    CONFIG_FILES = [".claw-account.json", ".claude/account.json", ".claude/auth.json"]

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.profiles = self._discover()

    def _discover(self) -> List[AccountProfile]:
        """Discover account profiles."""
        for filename in self.CONFIG_FILES:
            filepath = os.path.join(self.cwd, filename)
            # Handle .claude paths
            if filename.startswith(".claude/"):
                filepath = os.path.join(self.cwd, filename)

            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return self._parse_profiles(data)
                except (json.JSONDecodeError, OSError):
                    continue
            # Also check relative to cwd
            rel_path = os.path.join(self.cwd, filename)
            if os.path.exists(rel_path):
                try:
                    with open(rel_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return self._parse_profiles(data)
                except (json.JSONDecodeError, OSError):
                    continue
        return []

    def _parse_profiles(self, data: Dict[str, Any]) -> List[AccountProfile]:
        """Parse profiles from configuration."""
        profiles = []
        for p_data in data.get("profiles", []):
            profiles.append(AccountProfile.from_dict(p_data))
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

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.profiles:
            return "No account profiles configured."

        names = [p.name for p in self.profiles]
        return f"[Account Profiles] {', '.join(names)}"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.profiles:
            return ""

        return "Account profiles are available for authentication."