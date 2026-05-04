"""Tasks routes for GUI."""

from __future__ import annotations

from typing import Any, Dict

from ..task_runtime import TaskRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle tasks API requests."""
    cwd = db.agent_state.cwd
    runtime = TaskRuntime(cwd=cwd)

    if method == "GET":
        if path == "/api/tasks" or path == "/api/tasks/status":
            handler.send_json(runtime.get_state())
        elif path == "/api/tasks/list":
            handler.send_json({"tasks": runtime.list_tasks(status=data.get("status"))})
        elif path.startswith("/api/tasks/"):
            parts = path.split("/")
            if len(parts) >= 4:
                task_id = parts[3]
                task = runtime.get_task(task_id)
                handler.send_json(task or {"error": "Not found"}, 404 if not task else 200)
            else:
                handler.send_json({"error": "Not found"}, 404)
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "POST":
        if path == "/api/tasks/create":
            title = data.get("title", "New Task")
            detail = data.get("detail")
            blocked_by = data.get("blocked_by")
            task_id_param = data.get("task_id")
            task_id = runtime.create_task(title, detail=detail, blocked_by=blocked_by, task_id=task_id_param)
            handler.send_json({"task_id": task_id, "title": title})
        elif path == "/api/tasks/update":
            task_id = data.get("task_id")
            status = data.get("status")
            detail = data.get("detail")
            if not task_id:
                handler.send_json({"error": "task_id required"}, 400)
            else:
                ok = runtime.update_task(task_id, status=status, detail=detail)
                handler.send_json({"success": ok})
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)
