"""Background routes for GUI."""

from __future__ import annotations

from typing import Any, Dict

from ..background_runtime import BackgroundRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle background API requests."""
    cwd = db.agent_state.cwd
    runtime = BackgroundRuntime(cwd=cwd)

    if method == "GET":
        if path == "/api/background" or path == "/api/background/status":
            handler.send_json(runtime.get_state())
        elif path == "/api/background/tasks":
            handler.send_json({"tasks": runtime.list_tasks()})
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "POST":
        if path == "/api/background/create":
            name = data.get("name", "Untitled")
            task_id = runtime.create_task(name)
            handler.send_json({"task_id": task_id, "name": name})
        elif path == "/api/background/cancel":
            task_id = data.get("task_id")
            success = runtime.cancel_task(task_id)
            handler.send_json({"success": success})
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)