"""Memory routes for GUI."""

from __future__ import annotations

from typing import Any, Dict


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle memory API requests."""
    if method == "GET":
        if path == "/api/memory" or path == "/api/memory/status":
            handler.send_json({"sessions": list(db.sessions.keys())})
        elif path == "/api/memory/sessions":
            handler.send_json({"sessions": list(db.sessions.keys())})
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "POST":
        if path == "/api/memory/compact":
            handler.send_json({"status": "compacted"})
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)