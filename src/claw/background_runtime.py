"""Background runtime for CodeAgent."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable

from .hook_policy import RuntimeBase


@dataclass
class BackgroundTask:
    """A background task."""
    id: str
    name: str
    status: str = "running"  # running, completed, failed, cancelled
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    result: Optional[Any] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
        }


class BackgroundRuntime(RuntimeBase):
    """Background task management runtime.

    Manages background tasks with state persistence.
    """

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.tasks: Dict[str, BackgroundTask] = {}
        self._load_state()

    def _get_state_path(self) -> str:
        """Get the state file path."""
        return os.path.join(self.cwd, ".port_sessions", "background_runtime.json")

    def _load_state(self) -> None:
        """Load state from file."""
        state_path = self._get_state_path()
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for t_data in data.get("tasks", []):
                    task = BackgroundTask(
                        id=t_data["id"],
                        name=t_data["name"],
                        status=t_data.get("status", "completed"),
                        created_at=t_data.get("created_at", time.time()),
                        completed_at=t_data.get("completed_at"),
                        result=t_data.get("result"),
                        error=t_data.get("error"),
                    )
                    self.tasks[task.id] = task
            except (json.JSONDecodeError, OSError):
                pass

    def _save_state(self) -> None:
        """Save state to file."""
        state_path = self._get_state_path()
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({
                "tasks": [t.to_dict() for t in self.tasks.values()],
            }, f, indent=2)

    def create_task(self, name: str) -> str:
        """Create a new background task."""
        task_id = str(uuid.uuid4())[:8]
        task = BackgroundTask(id=task_id, name=name)
        self.tasks[task_id] = task
        self._save_state()
        return task_id

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a task by ID."""
        if task_id in self.tasks:
            return self.tasks[task_id].to_dict()
        return None

    def list_tasks(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all tasks, optionally filtered."""
        tasks = list(self.tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return [t.to_dict() for t in tasks]

    def update_task(self, task_id: str, status: str, result: Any = None, error: Optional[str] = None) -> bool:
        """Update a task status."""
        if task_id not in self.tasks:
            return False

        task = self.tasks[task_id]
        task.status = status
        if result is not None:
            task.result = result
        if error:
            task.error = error
        if status in ("completed", "failed", "cancelled"):
            task.completed_at = time.time()

        self._save_state()
        return True

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        return self.update_task(task_id, "cancelled")

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "tasks": [t.to_dict() for t in self.tasks.values()],
            "count": len(self.tasks),
            "running": len([t for t in self.tasks.values() if t.status == "running"]),
        }

    def render_summary(self) -> str:
        """Render summary for context injection."""
        running = len([t for t in self.tasks.values() if t.status == "running"])
        completed = len([t for t in self.tasks.values() if t.status == "completed"])

        if running > 0:
            return f"[Background] {running} running, {completed} completed"
        return f"[Background] {completed} completed tasks"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        return ""