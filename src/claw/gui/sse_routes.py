"""SSE (Server-Sent Events) handler for real-time streaming."""

from __future__ import annotations

import json
import queue
import time
from typing import Any, Dict, Optional


def handle_sse(handler, db) -> None:
    """Handle GET /api/stream — SSE endpoint for real-time updates.

    Events are read from db.event_queue. The agent thread pushes events
    while the SSE handler writes them to the client.
    """
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()

    event_queue = getattr(db, "event_queue", None)
    if event_queue is None:
        _write_sse(handler, "error", {"message": "No event queue"})
        _write_sse(handler, "done", {})
        return

    try:
        while True:
            try:
                event = event_queue.get(timeout=30)
                event_type = event.get("type", "message")
                data = event.get("data", {})
                _write_sse(handler, event_type, data)

                if event_type in ("done", "error"):
                    break
            except queue.Empty:
                # Send keepalive ping
                _write_sse(handler, "ping", {})
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass  # Client disconnected


def _write_sse(handler, event_type: str, data: Dict[str, Any]) -> None:
    """Write a single SSE frame."""
    payload = json.dumps(data, ensure_ascii=False)
    frame = f"event: {event_type}\ndata: {payload}\n\n"
    handler.wfile.write(frame.encode("utf-8"))
    handler.wfile.flush()


def push_event(db, event_type: str, data: Dict[str, Any]) -> None:
    """Push an event to the event queue (called from agent thread)."""
    event_queue = getattr(db, "event_queue", None)
    if event_queue:
        event_queue.put({"type": event_type, "data": data, "timestamp": time.time()})
