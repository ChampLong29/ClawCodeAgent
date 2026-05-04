"""Workflow runtime for CodeAgent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


@dataclass
class WorkflowStep:
    """A step in a workflow."""
    title: str
    command: Optional[str] = None
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "command": self.command,
            "detail": self.detail,
        }


@dataclass
class Workflow:
    """A workflow definition."""
    name: str
    description: str = ""
    prompt: str = ""
    steps: List[WorkflowStep] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "steps": [s.to_dict() for s in self.steps],
        }


class WorkflowRuntime(RuntimeBase):
    """Workflow management runtime.

    Discovery paths:
    - .claw-workflows.json
    - .claw-workflow.json
    """

    CONFIG_FILES = [".claw-workflows.json", ".claw-workflow.json"]

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.workflows = self._discover()

    def _discover(self) -> List[Workflow]:
        """Discover workflow configurations."""
        for filename in self.CONFIG_FILES:
            filepath = os.path.join(self.cwd, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return self._parse_workflows(data)
                except (json.JSONDecodeError, OSError):
                    continue
        return []

    def _parse_workflows(self, data: Dict[str, Any]) -> List[Workflow]:
        """Parse workflows from configuration."""
        workflows = []
        for w_data in data.get("workflows", []):
            steps = []
            for s_data in w_data.get("steps", []):
                steps.append(WorkflowStep(
                    title=s_data.get("title", ""),
                    command=s_data.get("command"),
                    detail=s_data.get("detail"),
                ))

            workflows.append(Workflow(
                name=w_data.get("name", ""),
                description=w_data.get("description", ""),
                prompt=w_data.get("prompt", ""),
                steps=steps,
            ))
        return workflows

    def get_workflow(self, name: str) -> Optional[Workflow]:
        """Get a workflow by name."""
        for wf in self.workflows:
            if wf.name == name:
                return wf
        return None

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "workflows": [w.to_dict() for w in self.workflows],
            "count": len(self.workflows),
        }

    def list_workflows(self) -> List[Dict[str, Any]]:
        """List all workflows."""
        return [w.to_dict() for w in self.workflows]

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.workflows:
            return "No workflows configured."

        names = [w.name for w in self.workflows]
        return f"[Workflows] {', '.join(names)}"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.workflows:
            return ""

        return "Workflows are available. Use /workflow command to run one."