"""MCP routes for GUI."""

from __future__ import annotations

from typing import Any, Dict

from ..mcp_runtime import MCPRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle MCP API requests."""
    cwd = db.agent_state.cwd
    runtime = MCPRuntime(cwd=cwd)

    if method == "GET":
        if path == "/api/mcp" or path == "/api/mcp/status":
            handler.send_json(runtime.get_state() or {"resources": [], "servers": []})
        elif path == "/api/mcp/resources":
            handler.send_json({"resources": runtime.list_resources()})
        elif path == "/api/mcp/tools":
            handler.send_json({"tools": []})  # MCP tools would be listed here
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)