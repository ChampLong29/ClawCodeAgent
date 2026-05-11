"""Remote trigger routes for GUI."""

from __future__ import annotations

from typing import Any, Dict

from ..remote_trigger_runtime import RemoteTriggerRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle remote trigger API requests."""
    cwd = db.agent_state.cwd
    runtime = RemoteTriggerRuntime(cwd=cwd)

    if method == "GET":
        if path == "/api/triggers" or path == "/api/triggers/status":
            state = runtime.get_state()
            handler.send_json(state or {"triggers": [], "count": 0})
        elif path == "/api/triggers/list":
            handler.send_json({"triggers": runtime.list_triggers()})
        elif path.startswith("/api/triggers/"):
            parts = path.split("/")
            if len(parts) >= 4:
                trigger_id = parts[3]
                trigger = runtime.get_trigger(trigger_id)
                handler.send_json(trigger or {"error": "Trigger not found"}, 404 if not trigger else 200)
            else:
                handler.send_json({"error": "Not found"}, 404)
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "POST":
        if path == "/api/triggers/run":
            trigger_id = data.get("trigger_id")
            if trigger_id:
                trigger = runtime.get_trigger(trigger_id)
                if trigger:
                    handler.send_json({"status": "triggered", "trigger_id": trigger_id, "trigger": trigger})
                else:
                    handler.send_json({"error": "Trigger not found"}, 404)
            else:
                handler.send_json({"error": "trigger_id required"}, 400)
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)
