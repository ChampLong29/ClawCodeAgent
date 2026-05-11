"""Plans routes for GUI."""

from __future__ import annotations

from typing import Any, Dict

from ..plan_runtime import PlanRuntime
from ..task_runtime import TaskRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle plans API requests."""
    cwd = db.agent_state.cwd
    runtime = PlanRuntime(cwd=cwd)

    if method == "GET":
        if path == "/api/plans" or path == "/api/plans/status":
            handler.send_json(runtime.get_state())
        elif path.startswith("/api/plans/"):
            parts = path.split("/")
            if len(parts) >= 4:
                plan_id = parts[3]
                plan = runtime.get_plan(plan_id)
                handler.send_json(plan or {"error": "Plan not found"}, 404 if not plan else 200)
            else:
                handler.send_json({"error": "Not found"}, 404)
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "POST":
        if path == "/api/plans/create":
            title = data.get("title", "New Plan")
            steps = data.get("steps", [])
            plan_id = runtime.create_plan(title, steps)

            # Optionally sync to tasks
            if data.get("sync_tasks"):
                task_runtime = TaskRuntime(cwd=cwd)
                runtime.update_plan(sync_tasks=True, task_runtime=task_runtime)

            handler.send_json({"plan_id": plan_id, "title": title})
        elif path == "/api/plans/update":
            plan_id = data.get("plan_id", runtime.active_plan_id)
            step_id = data.get("step_id")
            status = data.get("status")
            detail = data.get("detail")

            if step_id:
                ok = runtime.update_step(step_id, status=status, detail=detail, plan_id=plan_id)
                handler.send_json({"success": ok})
            else:
                sync_tasks = data.get("sync_tasks", False)
                if sync_tasks:
                    task_runtime = TaskRuntime(cwd=cwd)
                    ok = runtime.update_plan(plan_id=plan_id, sync_tasks=True, task_runtime=task_runtime)
                else:
                    ok = runtime.update_plan(plan_id=plan_id)
                handler.send_json({"success": ok})
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)
