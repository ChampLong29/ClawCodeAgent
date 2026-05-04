"""Remote trigger runtime for CodeAgent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


@dataclass
class RemoteTrigger:
    """A remote trigger configuration."""
    trigger_id: str
    name: str
    schedule: str = ""  # cron expression
    workflow: str = ""
    remote_target: str = ""
    body: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "name": self.name,
            "schedule": self.schedule,
            "workflow": self.workflow,
            "remote_target": self.remote_target,
            "body": self.body,
        }


class RemoteTriggerRuntime(RuntimeBase):
    """Remote trigger runtime.

    Discovery paths (NO walk-up, only cwd + additional directories):
    - .claw-remote-triggers.json
    - .claw-triggers.json

    IMPORTANT: Unlike hook_policy and remote_runtime, this does NOT walk up parent directories.
    """

    CONFIG_FILES = [".claw-remote-triggers.json", ".claw-triggers.json"]

    def __init__(
        self,
        cwd: str,
        additional_directories: Optional[List[str]] = None,
    ):
        self.cwd = cwd
        self.additional_directories = additional_directories or []
        self.triggers = self._discover()

    def _discover(self) -> List[RemoteTrigger]:
        """Discover trigger configurations."""
        # Check additional directories
        for directory in self.additional_directories:
            triggers = self._check_directory(directory)
            if triggers:
                return triggers

        # Only check cwd (no walk-up)
        return self._check_directory(self.cwd)

    def _check_directory(self, directory: str) -> List[RemoteTrigger]:
        """Check a directory for configuration files."""
        for filename in self.CONFIG_FILES:
            filepath = os.path.join(directory, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return self._parse_triggers(data)
                except (json.JSONDecodeError, OSError):
                    continue
        return []

    def _parse_triggers(self, data: Dict[str, Any]) -> List[RemoteTrigger]:
        """Parse triggers from configuration."""
        triggers = []
        for t_data in data.get("triggers", []):
            triggers.append(RemoteTrigger(
                trigger_id=t_data.get("trigger_id", ""),
                name=t_data.get("name", ""),
                schedule=t_data.get("schedule", ""),
                workflow=t_data.get("workflow", ""),
                remote_target=t_data.get("remote_target", ""),
                body=t_data.get("body", {}),
            ))
        return triggers

    def get_trigger(self, trigger_id: str) -> Optional[RemoteTrigger]:
        """Get a trigger by ID."""
        for trigger in self.triggers:
            if trigger.trigger_id == trigger_id:
                return trigger
        return None

    def get_state(self) -> Optional[Dict[str, Any]]:
        """Get current state."""
        if not self.triggers:
            return None
        return {
            "triggers": [t.to_dict() for t in self.triggers],
            "count": len(self.triggers),
        }

    def list_triggers(self) -> List[Dict[str, Any]]:
        """List all triggers."""
        return [t.to_dict() for t in self.triggers]

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.triggers:
            return "No remote triggers configured."

        names = [t.name for t in self.triggers]
        return f"[Remote Triggers] {', '.join(names)}"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.triggers:
            return ""

        return "Remote triggers are configured for scheduled execution."