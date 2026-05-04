"""GUI server for CodeAgent."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from ..agent_runtime import LocalCodingAgent
from ..agent_types import ModelConfig, BudgetConfig
from ..query_engine import QueryEngine, QueryEngineConfig


@dataclass
class AgentState:
    """GUI agent state."""
    model_name: str = "Qwen/Qwen3-Coder-30B-A3B-Instruct"
    allowed_tools: List[str] = field(default_factory=list)
    permission_mode: str = "read-only"  # read-only, allow-write, allow-shell
    session_id: Optional[str] = None
    streaming: bool = False
    cwd: str = "."
    api_config: Optional[Dict[str, Any]] = None


class GUIDatabase:
    """In-memory database for GUI state."""

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.agent_state = AgentState(cwd=cwd)
        self.sessions: Dict[str, Any] = {}
        self.runtime_states: Dict[str, Any] = {}

    def get_state(self) -> Dict[str, Any]:
        """Get full GUI state."""
        return {
            "agent_state": {
                "model_name": self.agent_state.model_name,
                "allowed_tools": self.agent_state.allowed_tools,
                "permission_mode": self.agent_state.permission_mode,
                "session_id": self.agent_state.session_id,
                "streaming": self.agent_state.streaming,
                "cwd": self.agent_state.cwd,
                "api_config": self.agent_state.api_config,
            },
            "sessions": list(self.sessions.keys()),
            "runtime_count": len(self.runtime_states),
        }

    def update_agent_state(self, updates: Dict[str, Any]) -> None:
        """Update agent state."""
        for key, value in updates.items():
            if hasattr(self.agent_state, key):
                setattr(self.agent_state, key, value)


# Global database instance
_db: Optional[GUIDatabase] = None


def get_db(cwd: str = ".") -> GUIDatabase:
    """Get or create database instance."""
    global _db
    if _db is None:
        _db = GUIDatabase(cwd=cwd)
    return _db


class GUIRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for GUI."""

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        """Send JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path

        db = get_db()

        # API routes
        if path == "/api/state":
            self.send_json(db.get_state())
        elif path == "/api/health":
            self.send_json({"status": "ok"})
        elif path == "/api/sessions":
            self.send_json({"sessions": list(db.sessions.keys())})
        elif path.startswith("/api/account"):
            from .account_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/ask-user"):
            from .ask_user_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/background"):
            from .background_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/diagnostics"):
            from .diagnostics_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/mcp"):
            from .mcp_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/memory"):
            from .memory_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/plans"):
            from .plans_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/plugins"):
            from .plugins_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/remote"):
            from .remote_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/triggers"):
            from .remote_trigger_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/search"):
            from .search_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/tasks"):
            from .tasks_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/teams"):
            from .team_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/workflows"):
            from .workflow_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path.startswith("/api/worktree"):
            from .worktree_routes import handle_request
            handle_request(self, "GET", parsed.path, {}, db)
        elif path == "/" or path == "/index":
            self.send_html(self._get_index_html())
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        """Handle POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path

        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}

        db = get_db()

        if path == "/api/query":
            self._handle_query(data, db)
        elif path == "/api/agent-state":
            db.update_agent_state(data)
            self.send_json({"status": "ok"})
        elif path.startswith("/api/account"):
            from .account_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/ask-user"):
            from .ask_user_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/background"):
            from .background_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/diagnostics"):
            from .diagnostics_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/mcp"):
            from .mcp_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/memory"):
            from .memory_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/plans"):
            from .plans_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/plugins"):
            from .plugins_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/remote"):
            from .remote_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/triggers"):
            from .remote_trigger_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/search"):
            from .search_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/tasks"):
            from .tasks_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/teams"):
            from .team_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/workflows"):
            from .workflow_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        elif path.startswith("/api/worktree"):
            from .worktree_routes import handle_request
            handle_request(self, "POST", parsed.path, data, db)
        else:
            self.send_json({"error": "Not found"}, 404)

    def _handle_query(self, data: Dict[str, Any], db: GUIDatabase) -> None:
        """Handle agent query request."""
        prompt = data.get("prompt", "")
        if not prompt:
            self.send_json({"error": "No prompt provided"}, 400)
            return

        config = QueryEngineConfig(
            model=ModelConfig(name=db.agent_state.model_name),
            budget=BudgetConfig(),
            stream=db.agent_state.streaming,
        )

        engine = QueryEngine(db.agent_state.cwd, config)
        result = engine.query(prompt, db.agent_state.session_id)

        # Update session ID
        if result and hasattr(result, 'usage') and result.usage:
            # Session already handled in engine
            pass

        response = {
            "stop_reason": result.stop_reason if result else "unknown",
            "final_message": result.final_message if result else "",
            "error": result.error if result else None,
        }
        self.send_json(response)

    def send_html(self, html: str) -> None:
        """Send HTML response."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _get_index_html(self) -> str:
        """Get the main HTML page."""
        return """<!DOCTYPE html>
<html>
<head>
    <title>Claw Code Agent</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #1a1a2e; color: #eee; }
        h1 { color: #00d4ff; }
        .status { background: #16213e; padding: 15px; border-radius: 8px; margin: 10px 0; }
        .nav { margin: 20px 0; }
        .nav a { color: #00d4ff; margin: 0 10px; text-decoration: none; }
        pre { background: #0f0f23; padding: 10px; border-radius: 4px; overflow-x: auto; }
    </style>
</head>
<body>
    <h1>Claw Code Agent</h1>
    <div class="status">
        <h2>Status</h2>
        <div id="state">Loading...</div>
    </div>
    <div class="nav">
        <a href="/api/account">Account</a>
        <a href="/api/ask-user">Ask User</a>
        <a href="/api/background">Background</a>
        <a href="/api/diagnostics">Diagnostics</a>
        <a href="/api/mcp">MCP</a>
        <a href="/api/memory">Memory</a>
        <a href="/api/plans">Plans</a>
        <a href="/api/plugins">Plugins</a>
        <a href="/api/remote">Remote</a>
        <a href="/api/triggers">Triggers</a>
        <a href="/api/search">Search</a>
        <a href="/api/tasks">Tasks</a>
        <a href="/api/teams">Teams</a>
        <a href="/api/workflows">Workflows</a>
        <a href="/api/worktree">Worktree</a>
    </div>
    <script>
        fetch('/api/state')
            .then(r => r.json())
            .then(d => document.getElementById('state').innerHTML = '<pre>' + JSON.stringify(d, null, 2) + '</pre>');
    </script>
</body>
</html>"""


def run_server(cwd: str, host: str, port: int, stream: bool) -> None:
    """Run the GUI server."""
    global _db
    _db = GUIDatabase(cwd=cwd)
    _db.agent_state.streaming = stream

    server = HTTPServer((host, port), GUIRequestHandler)
    print(f"GUI server running at http://{host}:{port}")
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()