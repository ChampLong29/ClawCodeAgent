"""Diagnostics routes for GUI."""

from __future__ import annotations

import platform
import os
from typing import Any, Dict


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle diagnostics API requests."""
    cwd = db.agent_state.cwd

    if method == "GET":
        if path == "/api/diagnostics" or path == "/api/diagnostics/status":
            diagnostics = {
                "platform": platform.system(),
                "python_version": platform.python_version(),
                "cwd": cwd,
                "environment": {
                    "PATH": os.environ.get("PATH", "")[:100],
                    "HOME": os.environ.get("HOME", ""),
                },
            }
            handler.send_json(diagnostics)
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)