"""Config runtime for CodeAgent."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


class ConfigRuntime(RuntimeBase):
    """Configuration discovery runtime.

    Discovery paths (NO walk-up, only cwd + additional directories):
    - .claude/settings.json
    - .claude/settings.local.json
    - .claw-config.json
    - .codex-config.json
    """

    CONFIG_FILES = [
        ".claude/settings.json",
        ".claude/settings.local.json",
        ".claw-config.json",
        ".codex-config.json",
    ]

    def __init__(
        self,
        cwd: str,
        additional_directories: Optional[List[str]] = None,
    ):
        self.cwd = cwd
        self.additional_directories = additional_directories or []
        self.config = self._discover()

    def _discover(self) -> Optional[Dict[str, Any]]:
        """Discover configuration."""
        # Check additional directories
        for directory in self.additional_directories:
            config = self._check_directory(directory)
            if config:
                return config

        # Only check cwd (no walk-up)
        return self._check_directory(self.cwd)

    def _check_directory(self, directory: str) -> Optional[Dict[str, Any]]:
        """Check a directory for configuration files."""
        for filename in self.CONFIG_FILES:
            filepath = os.path.join(directory, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        return json.load(f)
                except (json.JSONDecodeError, OSError):
                    continue
        return None

    def get_state(self) -> Optional[Dict[str, Any]]:
        """Get current state."""
        return self.config

    def get_value(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by key path (e.g., 'model.name')."""
        if not self.config:
            return default

        parts = key.split(".")
        value = self.config
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return default
        return value if value is not None else default

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.config:
            return "No configuration found."

        model_name = self.get_value("model.name", "unknown")
        return f"[Config] Model: {model_name}"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.config:
            return ""

        review_mode = self.get_value("review.strict", None)
        if review_mode is not None:
            return f"Review mode: {'strict' if review_mode else 'normal'}"

        return ""