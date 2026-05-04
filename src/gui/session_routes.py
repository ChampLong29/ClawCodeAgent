"""Session routes for GUI."""

from __future__ import annotations

from typing import Any, Dict

from ..session_store import list_sessions, load_agent_session, delete_agent_session
from ..agent_runtime import LocalCodingAgent
from ..agent_types import ModelConfig


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle session API requests."""
    cwd = db.agent_state.cwd

    if method == "GET":
        if path == "/api/sessions":
            sessions = list_sessions(cwd)
            # Sort by updated_at descending
            sessions.sort(key=lambda s: s.get("updated_at") or 0, reverse=True)
            handler.send_json({"sessions": sessions})
        elif path.startswith("/api/sessions/"):
            session_id = path.split("/api/sessions/")[1]
            if not session_id:
                handler.send_json({"error": "Missing session ID"}, 400)
                return
            try:
                session = load_agent_session(session_id, cwd)
                # Return session detail with recent messages
                messages = session.get_messages()
                detail = {
                    "session_id": session.session_id,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                    "model": session.model,
                    "stop_reason": session.stop_reason,
                    "cwd": session.cwd,
                    "message_count": len(messages),
                    "recent_messages": messages[-10:] if len(messages) > 10 else messages,
                }
                handler.send_json(detail)
            except FileNotFoundError:
                handler.send_json({"error": "Session not found"}, 404)
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "POST":
        if path.startswith("/api/sessions/") and path.endswith("/resume"):
            session_id = path.split("/api/sessions/")[1].rsplit("/resume")[0]
            if not session_id:
                handler.send_json({"error": "Missing session ID"}, 400)
                return
            try:
                # Load the session and set it as active
                session = load_agent_session(session_id, cwd)
                db.agent_state.session_id = session_id
                handler.send_json({
                    "session_id": session_id,
                    "model": session.model,
                    "cwd": session.cwd or cwd,
                    "message_count": len(session.messages),
                    "status": "resumed",
                })
            except FileNotFoundError:
                handler.send_json({"error": "Session not found"}, 404)
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "DELETE":
        if path.startswith("/api/sessions/"):
            session_id = path.split("/api/sessions/")[1]
            if not session_id:
                handler.send_json({"error": "Missing session ID"}, 400)
                return
            success = delete_agent_session(session_id, cwd)
            if db.agent_state.session_id == session_id:
                db.agent_state.session_id = None
            handler.send_json({"deleted": success})
        else:
            handler.send_json({"error": "Not found"}, 404)

    else:
        handler.send_json({"error": "Method not allowed"}, 405)
