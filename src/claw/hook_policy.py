"""Hook/policy runtime with walk-up behavior."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class HookPolicyConfig:
    """Configuration for hook/policy."""
    trusted: bool = False
    deny_tool_prefixes: List[str] = field(default_factory=list)
    hooks: Dict[str, Any] = field(default_factory=dict)
    budget: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trusted": self.trusted,
            "deny_tool_prefixes": self.deny_tool_prefixes,
            "hooks": self.hooks,
            "budget": self.budget,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> HookPolicyConfig:
        return cls(
            trusted=data.get("trusted", False),
            deny_tool_prefixes=data.get("deny_tool_prefixes", []),
            hooks=data.get("hooks", {}),
            budget=data.get("budget"),
        )


class RuntimeBase:
    """Base class for all runtimes."""

    def get_state(self) -> Optional[Dict[str, Any]]:
        return None

    def render_summary(self) -> str:
        return ""

    def get_prompt_guidance(self) -> str:
        return ""


class HookPolicyRuntime(RuntimeBase):
    """Hook/policy runtime with walk-up behavior.

    This runtime discovers configuration by walking up the directory tree,
    searching for:
    - .claw-policy.json
    - .codex-policy.json
    - .claw-hooks.json

    The walk-up continues to the root directory.
    """

    CONFIG_FILES = [
        ".claw-policy.json",
        ".codex-policy.json",
        ".claw-hooks.json",
    ]

    def __init__(
        self,
        cwd: str,
        additional_directories: Optional[List[str]] = None,
    ):
        self.cwd = cwd
        self.additional_directories = additional_directories or []
        self.config = self._discover()
        self.prompt_priority = 40

    def _discover(self) -> Optional[HookPolicyConfig]:
        """Discover configuration by walking up directories."""
        # First, check additional directories
        for directory in self.additional_directories:
            config = self._check_directory(directory)
            if config:
                return config

        # Walk up from cwd to root
        current_dir = os.path.abspath(self.cwd)
        root = os.path.dirname(current_dir)

        while True:
            config = self._check_directory(current_dir)
            if config:
                return config

            parent = os.path.dirname(current_dir)
            if parent == current_dir:  # Reached root
                break
            current_dir = parent

        return None

    def _check_directory(self, directory: str) -> Optional[HookPolicyConfig]:
        """Check a directory for configuration files."""
        for filename in self.CONFIG_FILES:
            filepath = os.path.join(directory, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return HookPolicyConfig.from_dict(data)
                except (json.JSONDecodeError, OSError):
                    continue
        return None

    def get_state(self) -> Optional[Dict[str, Any]]:
        """Get current state."""
        if self.config:
            return self.config.to_dict()
        return None

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.config:
            return "No hook/policy configuration found."

        parts = ["[Hook/Policy Configuration]"]
        if self.config.trusted:
            parts.append("- Trusted environment")
        if self.config.deny_tool_prefixes:
            parts.append(f"- Denied tool prefixes: {', '.join(self.config.deny_tool_prefixes)}")
        if self.config.hooks:
            parts.append(f"- Hooks configured: {len(self.config.hooks)}")

        return "\n".join(parts)

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.config:
            return ""

        parts = []
        if self.config.hooks:
            parts.append("This environment has custom hooks configured.")
        if self.config.budget:
            parts.append("Budget constraints are enforced by policy.")

        return "\n".join(parts) if parts else ""


# Alias for discovery
def discover(cwd: str, additional_directories: Optional[List[str]] = None) -> HookPolicyRuntime:
    """Discover hook/policy configuration."""
    return HookPolicyRuntime(cwd=cwd, additional_directories=additional_directories)