"""Bridge routes for GUI."""

from __future__ import annotations

import json
from typing import Any, Dict

from ..bridge_runtime import BridgeRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle bridge API requests."""
    cwd = db.agent_state.cwd
    bridge_rt = BridgeRuntime(cwd=cwd)

    if method == "GET":
        if path == "/api/bridge/status":
            handler.send_json(bridge_rt.get_state())
        elif path == "/api/bridge/routing":
            handler.send_json(bridge_rt.get_routing_table())
        elif path.startswith("/api/bridge/") and path.endswith("/sessions"):
            # /api/bridge/{name}/sessions
            bridge_name = path.split("/api/bridge/")[1].rsplit("/sessions")[0]
            sessions = bridge_rt.get_bridge_sessions(bridge_name)
            handler.send_json({"bridge": bridge_name, "sessions": sessions})
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "POST":
        if path.startswith("/api/bridge/") and path.endswith("/webhook"):
            # /api/bridge/{name}/webhook — webhook ingress from external platforms
            bridge_name = path.split("/api/bridge/")[1].rsplit("/webhook")[0]
            if bridge_name not in bridge_rt.bridges:
                handler.send_json({"error": f"Bridge '{bridge_name}' not found"}, 404)
                return

            # Extract message content from webhook payload
            # Supports both Feishu and WeCom webhook formats
            user_id, chat_id, content = _parse_webhook_payload(bridge_name, data)

            if not content:
                handler.send_json({"error": "No message content in payload"}, 400)
                return

            result = bridge_rt.route_message(
                bridge_name=bridge_name,
                user_id=user_id or "unknown",
                chat_id=chat_id or "direct",
                content=content,
            )
            handler.send_json(result)
        else:
            handler.send_json({"error": "Not found"}, 404)

    else:
        handler.send_json({"error": "Method not allowed"}, 405)


def _parse_webhook_payload(bridge_name: str, data: Dict[str, Any]) -> tuple:
    """Parse incoming webhook payload into (user_id, chat_id, content).

    Handles common webhook formats:
    - Feishu: {"event": {"sender": {"open_id": "..."}, "message": {"chat_id": "...", "content": "..."}}}
    - WeCom: {"msgtype": "text", "text": {"content": "..."}, "from": {"userid": "..."}}
    - Generic: {"user_id": "...", "chat_id": "...", "content": "..."}
    """
    # Generic format
    if "content" in data and isinstance(data["content"], str):
        return (
            data.get("user_id", ""),
            data.get("chat_id", ""),
            data["content"],
        )

    # Feishu event format
    event = data.get("event", {})
    if event:
        sender = event.get("sender", {})
        message = event.get("message", {})
        return (
            sender.get("open_id", ""),
            message.get("chat_id", ""),
            message.get("content", ""),
        )

    # WeCom message format
    if "text" in data and isinstance(data["text"], dict):
        return (
            data.get("from", {}).get("userid", ""),
            data.get("chatid", ""),
            data["text"].get("content", ""),
        )

    return ("", "", "")
