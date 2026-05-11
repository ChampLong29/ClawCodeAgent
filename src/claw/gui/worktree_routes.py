"""Worktree routes for GUI."""

from __future__ import annotations

from typing import Any, Dict

from ..worktree_runtime import WorktreeRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle worktree API requests."""
    cwd = db.agent_state.cwd
    runtime = WorktreeRuntime(cwd=cwd)

    if method == "GET":
        if path == "/api/worktree" or path == "/api/worktree/status":
            handler.send_json(runtime.get_state())
        elif path == "/api/worktree/list":
            handler.send_json({"worktrees": runtime.list_worktrees()})
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "POST":
        if path == "/api/worktree/create":
            name = data.get("name")
            branch = data.get("branch")
            path_ = data.get("path")
            success = runtime.create_worktree(name, branch, path_)
            handler.send_json({"success": success})
        elif path == "/api/worktree/remove":
            name = data.get("name")
            success = runtime.remove_worktree(name)
            handler.send_json({"success": success})
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)