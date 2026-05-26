"""Thread-safe permission request manager for GUI."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class PermissionRequest:
    """A pending permission request."""
    request_id: str
    tool_name: str
    command: str
    event: threading.Event = field(default_factory=threading.Event)
    response: Optional[str] = None  # "allow", "deny", "allow_all"


class PermissionManager:
    """Thread-safe permission request manager.

    Agent thread calls create_request + wait_for_response (blocking).
    HTTP thread calls respond (signals the event).
    """

    def __init__(self):
        self._pending: Dict[str, PermissionRequest] = {}
        self._lock = threading.Lock()
        self._on_request: Optional[Callable] = None

    def set_on_request(self, callback: Callable) -> None:
        """Set callback invoked when a new permission request is created."""
        self._on_request = callback

    def create_request(self, tool_name: str, command: str) -> str:
        """Create a permission request and return its ID."""
        request_id = str(uuid.uuid4())[:8]
        req = PermissionRequest(
            request_id=request_id,
            tool_name=tool_name,
            command=command,
        )
        with self._lock:
            self._pending[request_id] = req

        if self._on_request:
            self._on_request(req)

        return request_id

    def wait_for_response(self, request_id: str, timeout: float = 300) -> Optional[str]:
        """Block until a response is received. Returns the action or None on timeout."""
        with self._lock:
            req = self._pending.get(request_id)
        if req is None:
            return None

        if req.event.wait(timeout):
            return req.response
        return None

    def respond(self, request_id: str, action: str) -> bool:
        """Respond to a pending request. action: 'allow', 'deny', 'allow_all'."""
        with self._lock:
            req = self._pending.pop(request_id, None)
        if req is None:
            return False
        req.response = action
        req.event.set()
        return True

    def get_pending(self) -> Dict[str, PermissionRequest]:
        """Get all pending requests (for SSE polling)."""
        with self._lock:
            return dict(self._pending)
