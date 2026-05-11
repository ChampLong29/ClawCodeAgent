"""Plan runtime for CodeAgent."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


@dataclass
class PlanStep:
    """A step in a plan."""
    id: str
    title: str
    status: str = "pending"  # pending, in_progress, completed, failed
    detail: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "depends_on": self.depends_on,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PlanStep:
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            title=data.get("title", ""),
            status=data.get("status", "pending"),
            detail=data.get("detail"),
            depends_on=data.get("depends_on", []),
        )


class PlanRuntime(RuntimeBase):
    """Plan management runtime.

    Manages multi-step plans with dependencies.
    """

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.plans: Dict[str, List[PlanStep]] = {}
        self.active_plan_id: Optional[str] = None

    def create_plan(self, title: str, steps: List[Dict[str, Any]]) -> str:
        """Create a new plan."""
        plan_id = str(uuid.uuid4())[:8]
        plan_steps = []

        for step_data in steps:
            plan_steps.append(PlanStep(
                id=step_data.get("id", str(uuid.uuid4())[:8]),
                title=step_data.get("title", ""),
                detail=step_data.get("detail"),
                depends_on=step_data.get("depends_on", []),
            ))

        self.plans[plan_id] = plan_steps
        self.active_plan_id = plan_id

        return plan_id

    def get_plan(self, plan_id: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """Get a plan by ID."""
        target_id = plan_id or self.active_plan_id
        if target_id and target_id in self.plans:
            return [s.to_dict() for s in self.plans[target_id]]
        return None

    def update_step(
        self,
        step_id: str,
        status: Optional[str] = None,
        detail: Optional[str] = None,
        plan_id: Optional[str] = None,
    ) -> bool:
        """Update a plan step."""
        target_id = plan_id or self.active_plan_id
        if not target_id or target_id not in self.plans:
            return False

        for step in self.plans[target_id]:
            if step.id == step_id:
                if status:
                    step.status = status
                if detail is not None:
                    step.detail = detail
                return True

        return False

    def update_plan(self, plan_id: Optional[str] = None, sync_tasks: bool = False, task_runtime: Optional[Any] = None) -> bool:
        """Update plan status and optionally sync to task runtime.

        When sync_tasks is True and a task_runtime is provided, each plan step
        is synced as a task with deterministic IDs (plan-{plan_id}-step-{step_id}).
        Dependencies (depends_on) are mapped to task blocked_by relationships.
        """
        target_id = plan_id or self.active_plan_id
        if not target_id or target_id not in self.plans:
            return False

        plan = self.plans[target_id]

        if sync_tasks and task_runtime:
            for step in plan:
                task_id = f"plan-{target_id}-step-{step.id}"

                # Map depends_on to task blocked_by IDs
                blocked_by = [
                    f"plan-{target_id}-step-{dep_id}"
                    for dep_id in step.depends_on
                ]

                # Determine task status based on plan step status and dependencies
                task_status = step.status
                if step.status == "pending":
                    # Check if dependencies are completed
                    deps_completed = all(
                        dep_step.status == "completed"
                        for dep_step in plan
                        if dep_step.id in step.depends_on
                    )
                    if not deps_completed and step.depends_on:
                        task_status = "blocked"

                # Create or update the corresponding task
                task_runtime.create_task(
                    title=step.title,
                    detail=step.detail,
                    blocked_by=blocked_by,
                    task_id=task_id,
                )
                # Set the correct status
                task_runtime.update_task(task_id, status=task_status)

            # After syncing all tasks, check for unblocks
            for step in plan:
                if step.status == "completed":
                    task_id = f"plan-{target_id}-step-{step.id}"
                    task_runtime.update_task(task_id, status="completed")

        return True

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "plans": {pid: [s.to_dict() for s in steps] for pid, steps in self.plans.items()},
            "active_plan_id": self.active_plan_id,
        }

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.plans:
            return "No active plans."

        active_steps = self.plans.get(self.active_plan_id, [])
        completed = sum(1 for s in active_steps if s.status == "completed")
        total = len(active_steps)

        return f"[Plan] {completed}/{total} steps completed"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.active_plan_id or self.active_plan_id not in self.plans:
            return ""

        return "A plan is active. Follow the plan steps."