"""Task runtime for CodeAgent."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


@dataclass
class Task:
    """A task definition."""
    id: str
    title: str
    status: str = "pending"  # pending, in_progress, completed, failed, blocked
    blocked_by: List[str] = field(default_factory=list)
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "blocked_by": self.blocked_by,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Task:
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            title=data.get("title", ""),
            status=data.get("status", "pending"),
            blocked_by=data.get("blocked_by", []),
            detail=data.get("detail"),
        )


class TaskRuntime(RuntimeBase):
    """Task management runtime.

    Manages tasks with dependency tracking.
    """

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.tasks: Dict[str, Task] = {}

    def create_task(self, title: str, detail: Optional[str] = None, blocked_by: Optional[List[str]] = None, task_id: Optional[str] = None) -> str:
        """Create a new task.

        Args:
            title: Task title
            detail: Optional task detail/description
            blocked_by: List of task IDs that block this task
            task_id: Optional deterministic task ID (used for plan-task sync)
        """
        task_id = task_id or str(uuid.uuid4())[:8]
        # If task already exists with this ID, update it instead
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.title = title
            if detail is not None:
                task.detail = detail
            if blocked_by is not None:
                task.blocked_by = blocked_by
        else:
            task = Task(
                id=task_id,
                title=title,
                detail=detail,
                blocked_by=blocked_by or [],
            )
            self.tasks[task_id] = task
        return task_id

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a task by ID."""
        if task_id in self.tasks:
            return self.tasks[task_id].to_dict()
        return None

    def list_tasks(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all tasks, optionally filtered by status."""
        tasks = list(self.tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return [t.to_dict() for t in tasks]

    def update_task(self, task_id: str, status: Optional[str] = None, detail: Optional[str] = None) -> bool:
        """Update a task."""
        if task_id not in self.tasks:
            return False

        task = self.tasks[task_id]
        if status:
            task.status = status
        if detail is not None:
            task.detail = detail

        # Check if this unblocks other tasks
        if status == "completed":
            self._check_unblock(task_id)

        return True

    def _check_unblock(self, completed_task_id: str) -> None:
        """Check if completing a task unblocks others."""
        for task in self.tasks.values():
            if completed_task_id in task.blocked_by:
                # Check if all blockers are now completed
                all_blockers_done = all(
                    self.tasks[bid].status == "completed"
                    for bid in task.blocked_by
                    if bid in self.tasks
                )
                if all_blockers_done:
                    task.blocked_by = [b for b in task.blocked_by if b != completed_task_id]
                    if task.status == "blocked":
                        task.status = "pending"

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "tasks": [t.to_dict() for t in self.tasks.values()],
            "count": len(self.tasks),
            "by_status": {
                "pending": len([t for t in self.tasks.values() if t.status == "pending"]),
                "in_progress": len([t for t in self.tasks.values() if t.status == "in_progress"]),
                "completed": len([t for t in self.tasks.values() if t.status == "completed"]),
                "blocked": len([t for t in self.tasks.values() if t.status == "blocked"]),
            },
        }

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.tasks:
            return "No tasks."

        pending = len([t for t in self.tasks.values() if t.status == "pending"])
        completed = len([t for t in self.tasks.values() if t.status == "completed"])

        return f"[Tasks] {completed} completed, {pending} pending"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        blocked = [t for t in self.tasks.values() if t.status == "blocked"]
        if blocked:
            return f"{len(blocked)} task(s) are blocked by dependencies."

        return ""