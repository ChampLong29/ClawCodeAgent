"""Plugins routes for GUI."""

from __future__ import annotations

from typing import Any, Dict

from ..plugin_runtime import PluginRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle plugins API requests."""
    cwd = db.agent_state.cwd
    runtime = PluginRuntime(cwd=cwd)

    if method == "GET":
        if path == "/api/plugins" or path == "/api/plugins/status":
            handler.send_json(runtime.get_state())
        elif path == "/api/plugins/list":
            handler.send_json({"plugins": runtime.list_plugins()})
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)