"""Account routes for GUI."""

from __future__ import annotations

import json
from typing import Any, Dict

from ..account_runtime import AccountRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle account API requests."""
    cwd = db.agent_state.cwd

    if method == "GET":
        runtime = AccountRuntime(cwd=cwd)
        state = runtime.get_state()

        if path == "/api/account" or path == "/api/account/status":
            handler.send_json(state or {"profiles": [], "count": 0})
        elif path == "/api/account/profiles":
            handler.send_json({"profiles": runtime.list_profiles()})
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "POST":
        if path == "/api/account/login":
            profile_name = data.get("profile")
            handler.send_json({"status": "logged_in", "profile": profile_name})
        elif path == "/api/account/logout":
            handler.send_json({"status": "logged_out"})
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)