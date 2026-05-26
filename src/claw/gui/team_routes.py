"""Team routes for GUI."""

from __future__ import annotations

from typing import Any, Dict

from ..team_runtime import TeamRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle teams API requests."""
    cwd = db.agent_state.cwd
    runtime = TeamRuntime(cwd=cwd)

    if method == "GET":
        if path == "/api/teams" or path == "/api/teams/status":
            handler.send_json(runtime.get_state())
        elif path == "/api/teams/list":
            handler.send_json({"teams": runtime.list_teams()})
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)