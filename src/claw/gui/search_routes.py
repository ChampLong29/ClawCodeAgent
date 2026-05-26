"""Search routes for GUI."""

from __future__ import annotations

from typing import Any, Dict

from ..search_runtime import SearchRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle search API requests."""
    cwd = db.agent_state.cwd
    runtime = SearchRuntime(cwd=cwd)

    if method == "GET":
        if path == "/api/search" or path == "/api/search/status":
            handler.send_json(runtime.get_state())
        elif path == "/api/search/providers":
            handler.send_json({"providers": runtime.list_providers()})
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "POST":
        if path == "/api/search/query":
            query = data.get("query", "")
            provider = data.get("provider")
            result = runtime.search(query, provider)
            handler.send_json(result)
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)