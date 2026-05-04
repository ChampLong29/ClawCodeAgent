"""Remote routes for GUI."""

from __future__ import annotations

from typing import Any, Dict

from ..remote_runtime import RemoteRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle remote API requests."""
    cwd = db.agent_state.cwd
    runtime = RemoteRuntime(cwd=cwd)

    if method == "GET":
        if path == "/api/remote" or path == "/api/remote/status":
            handler.send_json(runtime.get_state())
        elif path == "/api/remote/profiles":
            handler.send_json({"profiles": runtime.list_profiles()})
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "POST":
        if path == "/api/remote/connect":
            profile = data.get("profile")
            handler.send_json({"status": "connecting", "profile": profile})
        elif path == "/api/remote/disconnect":
            handler.send_json({"status": "disconnected"})
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)