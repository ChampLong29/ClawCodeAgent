"""Workflow routes for GUI."""

from __future__ import annotations

from typing import Any, Dict

from ..workflow_runtime import WorkflowRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle workflows API requests."""
    cwd = db.agent_state.cwd
    runtime = WorkflowRuntime(cwd=cwd)

    if method == "GET":
        if path == "/api/workflows" or path == "/api/workflows/status":
            handler.send_json(runtime.get_state())
        elif path == "/api/workflows/list":
            handler.send_json({"workflows": runtime.list_workflows()})
        elif path.startswith("/api/workflows/"):
            parts = path.split("/")
            if len(parts) >= 4:
                workflow_name = parts[3]
                workflow = runtime.get_workflow(workflow_name)
                handler.send_json(workflow or {"error": "Workflow not found"}, 404 if not workflow else 200)
            else:
                handler.send_json({"error": "Not found"}, 404)
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "POST":
        if path == "/api/workflows/run":
            workflow_name = data.get("workflow")
            workflow = runtime.get_workflow(workflow_name)
            if workflow:
                handler.send_json({"status": "running", "workflow": workflow_name, "steps": workflow.get("steps", [])})
            else:
                handler.send_json({"error": "Workflow not found"}, 404)
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)
