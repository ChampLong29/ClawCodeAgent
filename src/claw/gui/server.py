"""GUI server for CodeAgent."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

from ..agent_runtime import LocalCodingAgent
from ..agent_types import AgentPermissions, ModelConfig, BudgetConfig
from ..query_engine import QueryEngine, QueryEngineConfig
from ..session_store import list_sessions, save_agent_session
from .permission_manager import PermissionManager


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server that handles each request in a new thread."""
    daemon_threads = True


@dataclass
class AgentState:
    """GUI agent state."""
    model_name: str = ""
    allowed_tools: List[str] = field(default_factory=list)
    permission_mode: str = "read-only"
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
        self.event_queue: queue.Queue = queue.Queue()
        self.permission_manager = PermissionManager()

        # Populate sessions from disk
        for s in list_sessions(cwd):
            self.sessions[s["session_id"]] = s

        # Wire permission callback to push events to SSE queue
        def on_permission_request(req):
            self.event_queue.put({
                "type": "permission_required",
                "data": {
                    "request_id": req.request_id,
                    "tool_name": req.tool_name,
                    "command": req.command,
                },
            })

        self.permission_manager.set_on_request(on_permission_request)

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
            "sessions": list_sessions(self.cwd),
            "runtime_count": len(self.runtime_states),
        }

    def update_agent_state(self, updates: Dict[str, Any]) -> None:
        """Update agent state."""
        for key, value in updates.items():
            if hasattr(self.agent_state, key):
                setattr(self.agent_state, key, value)

    def _get_permissions_dict(self) -> Dict[str, Any]:
        """Convert permission_mode string to permissions dict."""
        mode = self.agent_state.permission_mode
        if mode == "allow-shell":
            return AgentPermissions(allow_write=True, allow_shell=True).to_dict()
        elif mode == "allow-write":
            return AgentPermissions(allow_write=True, allow_shell=False).to_dict()
        else:
            return AgentPermissions(allow_write=False, allow_shell=False).to_dict()


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

        if path == "/api/state":
            self.send_json(db.get_state())
        elif path == "/api/health":
            self.send_json({"status": "ok"})
        elif path == "/api/stream":
            from .sse_routes import handle_sse
            handle_sse(self, db)
        elif path.startswith("/api/sessions"):
            from .session_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/account"):
            from .account_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/ask-user"):
            from .ask_user_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/background"):
            from .background_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/diagnostics"):
            from .diagnostics_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/mcp"):
            from .mcp_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/memory"):
            from .memory_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/plans"):
            from .plans_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/plugins"):
            from .plugins_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/remote"):
            from .remote_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/triggers"):
            from .remote_trigger_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/search"):
            from .search_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/tasks"):
            from .tasks_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/teams"):
            from .team_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/workflows"):
            from .workflow_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/worktree"):
            from .worktree_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path.startswith("/api/bridge"):
            from .bridge_routes import handle_request
            handle_request(self, "GET", path, {}, db)
        elif path == "/" or path == "/index":
            self.send_html(self._get_index_html())
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        """Handle POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}

        db = get_db()

        if path == "/api/query":
            self._handle_query(data, db)
        elif path == "/api/permission-response":
            self._handle_permission_response(data, db)
        elif path == "/api/agent-state":
            db.update_agent_state(data)
            self.send_json({"status": "ok"})
        elif path.startswith("/api/sessions"):
            from .session_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/account"):
            from .account_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/ask-user"):
            from .ask_user_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/background"):
            from .background_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/diagnostics"):
            from .diagnostics_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/mcp"):
            from .mcp_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/memory"):
            from .memory_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/plans"):
            from .plans_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/plugins"):
            from .plugins_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/remote"):
            from .remote_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/triggers"):
            from .remote_trigger_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/search"):
            from .search_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/tasks"):
            from .tasks_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/teams"):
            from .team_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/workflows"):
            from .workflow_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/worktree"):
            from .worktree_routes import handle_request
            handle_request(self, "POST", path, data, db)
        elif path.startswith("/api/bridge"):
            from .bridge_routes import handle_request
            handle_request(self, "POST", path, data, db)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        """Handle DELETE requests."""
        parsed = urlparse(self.path)
        path = parsed.path

        db = get_db()

        if path.startswith("/api/sessions"):
            from .session_routes import handle_request
            handle_request(self, "DELETE", path, {}, db)
        else:
            self.send_json({"error": "Not found"}, 404)

    def _handle_query(self, data: Dict[str, Any], db: GUIDatabase) -> None:
        """Handle agent query — runs agent in background thread, returns immediately."""
        prompt = data.get("prompt", "")
        if not prompt:
            self.send_json({"error": "No prompt provided"}, 400)
            return

        config = QueryEngineConfig(
            model=ModelConfig(name=db.agent_state.model_name) if db.agent_state.model_name else None,
            budget=BudgetConfig(),
            permissions=db._get_permissions_dict(),
            # GUI currently renders the final assistant message via the done event.
            # Keep streaming disabled here to avoid empty web-chat responses until
            # token callbacks are wired through LocalCodingAgent.
            stream=False,
        )

        # Build a permission callback that works with the GUI event system
        eq = db.event_queue

        def gui_permission_callback(tool_name: str, context: Dict[str, Any]) -> bool:
            """Permission callback that blocks agent thread, waits for GUI response."""
            cmd = context.get("command", "")
            request_id = db.permission_manager.create_request(tool_name, cmd)
            result = db.permission_manager.wait_for_response(request_id, timeout=300)
            if result == "allow" or result == "allow_all":
                if result == "allow_all":
                    # Grant shell permissions permanently for this session
                    db.agent_state.permission_mode = "allow-shell"
                return True
            return False

        config.permission_callback = gui_permission_callback

        session_id = db.agent_state.session_id

        def run_agent():
            """Run agent in background thread, pushing events to SSE queue."""
            event_id = str(uuid.uuid4())[:8]
            try:
                eq.put({"type": "status", "data": {"event_id": event_id, "status": "started", "session_id": session_id}})

                if session_id:
                    agent = LocalCodingAgent.from_session(
                        session_id=session_id,
                        cwd=db.agent_state.cwd,
                        model_config=config.model,
                        budget=config.budget,
                    )
                    agent.permissions = config.permissions
                    agent.permission_callback = gui_permission_callback
                    result = agent.resume(prompt, stream=config.stream)
                else:
                    agent = LocalCodingAgent(
                        cwd=db.agent_state.cwd,
                        model_config=config.model,
                        budget=config.budget,
                        permissions=config.permissions,
                    )
                    agent.permission_callback = gui_permission_callback
                    result = agent.run(prompt, max_turns=config.max_turns, stream=config.stream)
                    if agent.session:
                        session_id_new = agent.session.session_id
                        db.agent_state.session_id = session_id_new
                        eq.put({"type": "session", "data": {"session_id": session_id_new}})


                # Persist session
                if agent.session:
                    save_agent_session(agent.session, db.agent_state.cwd)
                    # Refresh session list
                    db.sessions = {}
                    for s in list_sessions(db.agent_state.cwd):
                        db.sessions[s["session_id"]] = s

                eq.put({
                    "type": "done",
                    "data": {
                        "event_id": event_id,
                        "stop_reason": result.stop_reason if result else "unknown",
                        "final_message": result.final_message if result else "",
                        "error": result.error if result else None,
                    },
                })
            except Exception as e:
                eq.put({
                    "type": "error",
                    "data": {"event_id": event_id, "message": str(e)},
                })

        thread = threading.Thread(target=run_agent, daemon=True)
        thread.start()

        self.send_json({"session_id": session_id, "status": "started"})

    def _handle_permission_response(self, data: Dict[str, Any], db: GUIDatabase) -> None:
        """Handle user response to a permission request."""
        request_id = data.get("request_id", "")
        action = data.get("action", "deny")

        if request_id:
            ok = db.permission_manager.respond(request_id, action)
            self.send_json({"acknowledged": ok, "request_id": request_id, "action": action})
        else:
            self.send_json({"error": "Missing request_id"}, 400)

    def send_html(self, html: str) -> None:
        """Send HTML response."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _get_index_html(self) -> str:
        """Get the main HTML page — chat UI with session list and permission modal."""
        return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claw Code Agent</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #e0e0e0; height: 100vh; display: flex; }
a { color: #00d4ff; }

#sidebar { width: 260px; min-width: 260px; background: #16213e; display: flex; flex-direction: column; border-right: 1px solid #2a2a4a; }
#sidebar h3 { padding: 16px; color: #00d4ff; font-size: 14px; border-bottom: 1px solid #2a2a4a; }
#session-list { flex: 1; overflow-y: auto; padding: 8px; }
#session-list .btn-new { display: block; width: 100%; padding: 10px; margin-bottom: 8px; background: #00d4ff; color: #1a1a2e; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 13px; }
#session-list .btn-new:hover { background: #00b8e6; }
.session-item { padding: 10px 12px; margin-bottom: 4px; border-radius: 6px; cursor: pointer; font-size: 12px; transition: background .15s; }
.session-item:hover { background: #1e2d50; }
.session-item.active { background: #1e3a5f; border-left: 3px solid #00d4ff; }
.session-item .sess-id { color: #00d4ff; font-weight: 600; font-size: 11px; }
.session-item .sess-meta { color: #888; margin-top: 4px; font-size: 11px; }
.session-item .sess-model { color: #666; font-size: 10px; }

#main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
#header { padding: 12px 20px; background: #16213e; border-bottom: 1px solid #2a2a4a; display: flex; align-items: center; gap: 12px; }
#header h2 { font-size: 16px; color: #00d4ff; }
#header select, #header button { padding: 6px 12px; border-radius: 4px; border: 1px solid #3a3a5a; background: #0f0f23; color: #eee; font-size: 12px; cursor: pointer; }

#chat { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
.chat-empty { text-align: center; color: #666; margin-top: 80px; font-size: 14px; }

.msg { max-width: 85%; padding: 10px 14px; border-radius: 12px; font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.msg.user { align-self: flex-end; background: #005f8c; color: #fff; border-bottom-right-radius: 4px; }
.msg.assistant { align-self: flex-start; background: #1e2d50; color: #ddd; border-bottom-left-radius: 4px; }
.msg.tool { align-self: flex-start; background: #2a2a1a; color: #cc0; font-size: 11px; font-family: monospace; border-left: 3px solid #cc0; }
.msg.error { align-self: flex-start; background: #3a1a1a; color: #f66; border-left: 3px solid #f66; }

.permission-card { align-self: center; background: #3a2a0a; border: 2px solid #f90; border-radius: 12px; padding: 16px; max-width: 500px; width: 100%; }
.permission-card h4 { color: #f90; margin-bottom: 8px; }
.permission-card pre { background: #1a1a0a; padding: 10px; border-radius: 4px; font-size: 12px; overflow-x: auto; color: #f90; margin-bottom: 12px; }
.permission-card .btns { display: flex; gap: 8px; }
.permission-card button { flex: 1; padding: 8px; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 12px; }
.permission-card .btn-deny { background: #c33; color: #fff; }
.permission-card .btn-allow { background: #3a3; color: #fff; }
.permission-card .btn-allow-all { background: #08a; color: #fff; }

#input-area { padding: 12px 20px; background: #16213e; border-top: 1px solid #2a2a4a; display: flex; gap: 8px; }
#input-area textarea { flex: 1; padding: 10px; border-radius: 8px; border: 1px solid #3a3a5a; background: #0f0f23; color: #eee; font-size: 13px; resize: none; min-height: 40px; max-height: 120px; font-family: inherit; }
#input-area textarea:focus { outline: none; border-color: #00d4ff; }
#input-area button { padding: 8px 20px; background: #00d4ff; color: #1a1a2e; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 13px; }
#input-area button:hover { background: #00b8e6; }
#input-area button:disabled { background: #555; cursor: not-allowed; }

.spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #00d4ff; border-top-color: transparent; border-radius: 50%; animation: spin .6s linear infinite; vertical-align: middle; margin-right: 6px; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<div id="sidebar">
  <h3>Sessions</h3>
  <div id="session-list">
    <button class="btn-new" onclick="newSession()">+ New Session</button>
  </div>
</div>

<div id="main">
  <div id="header">
    <h2>Claw Code Agent</h2>
    <select id="perm-mode" onchange="updatePermMode()">
      <option value="read-only">Read Only</option>
      <option value="allow-write">Allow Write</option>
      <option value="allow-shell">Allow Shell</option>
    </select>
    <span id="status-indicator" style="font-size:11px;color:#888;"></span>
  </div>
  <div id="chat"><div class="chat-empty">Enter a prompt to start</div></div>
  <div id="input-area">
    <textarea id="prompt-input" rows="1" placeholder="Type your prompt... (Enter to send)"
      onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendQuery();}"></textarea>
    <button id="send-btn" onclick="sendQuery()">Send</button>
  </div>
</div>

<script>
let currentSessionId = null;
let sseSource = null;
let pendingPermission = null;

function init() {
  loadSessions();
  connectSSE();
}

function connectSSE() {
  if (sseSource) sseSource.close();
  sseSource = new EventSource('/api/stream');
  sseSource.addEventListener('status', e => {
    const d = JSON.parse(e.data);
    if (d.status === 'started') showThinking(true);
  });
  sseSource.addEventListener('text', e => {
    const d = JSON.parse(e.data);
    appendText(d.text || '');
  });
  sseSource.addEventListener('session', e => {
    const d = JSON.parse(e.data);
    if (d.session_id) {
      currentSessionId = d.session_id;
      loadSessions();
    }
  });
  sseSource.addEventListener('permission_required', e => {
    const d = JSON.parse(e.data);
    showPermissionCard(d);
  });
  sseSource.addEventListener('done', e => {
    const d = JSON.parse(e.data);
    showThinking(false);
    if (d.final_message) appendText('\\n\\n' + d.final_message);
    if (d.error) appendError(d.error);
    loadSessions();
  });
  sseSource.addEventListener('error', e => {
    try {
      const d = JSON.parse(e.data);
      appendError(d.message || 'Unknown error');
    } catch(_) {}
    showThinking(false);
  });
  sseSource.addEventListener('ping', e => {});
}

function showThinking(active) {
  const btn = document.getElementById('send-btn');
  btn.disabled = active;
  const status = document.getElementById('status-indicator');
  status.innerHTML = active ? '<span class="spinner"></span>Thinking...' : '';
}

function appendText(text) {
  const chat = document.getElementById('chat');
  const last = chat.lastElementChild;
  if (last && last.classList.contains('msg') && last.classList.contains('assistant') && last.dataset.streaming === 'true') {
    last.textContent += text;
  } else {
    const div = document.createElement('div');
    div.className = 'msg assistant';
    div.dataset.streaming = 'true';
    div.textContent = text;
    chat.appendChild(div);
  }
}

function appendError(text) {
  const chat = document.getElementById('chat');
  const div = document.createElement('div');
  div.className = 'msg error';
  div.textContent = 'Error: ' + text;
  chat.appendChild(div);
}

function showPermissionCard(req) {
  const chat = document.getElementById('chat');
  const div = document.createElement('div');
  div.className = 'permission-card';
  div.id = 'perm-' + req.request_id;
  div.innerHTML = '<h4>Shell Permission Required</h4>'
    + '<pre>' + escapeHtml(req.command) + '</pre>'
    + '<div class="btns">'
    + '<button class="btn-deny" onclick="respondPerm(\\'' + req.request_id + '\\', \\'deny\\')">Deny</button>'
    + '<button class="btn-allow" onclick="respondPerm(\\'' + req.request_id + '\\', \\'allow\\')">Allow Once</button>'
    + '<button class="btn-allow-all" onclick="respondPerm(\\'' + req.request_id + '\\', \\'allow_all\\')">Allow All Shell</button>'
    + '</div>';
  chat.appendChild(div);
  div.scrollIntoView({behavior: 'smooth'});
}

function respondPerm(requestId, action) {
  fetch('/api/permission-response', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({request_id: requestId, action: action})
  }).then(r => r.json()).then(d => {
    const card = document.getElementById('perm-' + requestId);
    if (card) card.remove();
  });
}

function sendQuery() {
  const input = document.getElementById('prompt-input');
  const prompt = input.value.trim();
  if (!prompt) return;
  input.value = '';

  const chat = document.getElementById('chat');
  const userDiv = document.createElement('div');
  userDiv.className = 'msg user';
  userDiv.textContent = prompt;
  chat.appendChild(userDiv);

  // Mark all streaming messages as done
  chat.querySelectorAll('.msg[data-streaming]').forEach(m => delete m.dataset.streaming);

  fetch('/api/query', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      prompt: prompt,
      session_id: currentSessionId
    })
  }).then(r => r.json()).then(d => {
    if (d.session_id && !currentSessionId) {
      currentSessionId = d.session_id;
      loadSessions();
    }
  }).catch(err => appendError(err.message));
}

function newSession() {
  currentSessionId = null;
  fetch('/api/agent-state', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: null})
  }).catch(() => {});
  document.getElementById('chat').innerHTML = '<div class="chat-empty">New session — enter a prompt to start</div>';
  document.querySelectorAll('.session-item.active').forEach(el => el.classList.remove('active'));
  connectSSE();
}

function renderSessionMessages(messages) {
  const chat = document.getElementById('chat');
  chat.innerHTML = '';
  if (!messages || messages.length === 0) {
    chat.innerHTML = '<div class="chat-empty">No messages yet — enter a prompt to continue</div>';
    return;
  }
  messages.forEach(m => {
    const role = m.role || '';
    const content = m.content || '';
    if (!content) return;
    const div = document.createElement('div');
    div.className = 'msg ' + (role === 'user' ? 'user' : (role === 'assistant' ? 'assistant' : 'tool'));
    div.textContent = content;
    chat.appendChild(div);
  });
}

function selectSession(sid) {
  currentSessionId = sid;
  document.querySelectorAll('.session-item').forEach(el => el.classList.toggle('active', el.dataset.sid === sid));
  fetch('/api/sessions/' + encodeURIComponent(sid) + '/resume', {method: 'POST'})
    .then(r => r.json())
    .then(() => fetch('/api/sessions/' + encodeURIComponent(sid)))
    .then(r => r.json())
    .then(d => renderSessionMessages(d.recent_messages || []))
    .catch(err => appendError(err.message));
  connectSSE();
}


function loadSessions() {
  fetch('/api/sessions').then(r => r.json()).then(d => {
    const list = document.getElementById('session-list');
    list.innerHTML = '<button class="btn-new" onclick="newSession()">+ New Session</button>';
    (d.sessions || []).forEach(s => {
      const div = document.createElement('div');
      div.className = 'session-item';
      if (s.session_id === currentSessionId) div.classList.add('active');
      div.dataset.sid = s.session_id;
      const date = s.created_at ? new Date(s.created_at * 1000).toLocaleString() : 'unknown';
      const modelShort = (s.model || '').split('/').pop() || '?';
      div.innerHTML = '<div class="sess-id">' + escapeHtml(s.session_id) + '</div>'
        + '<div class="sess-meta">' + s.message_count + ' msgs &middot; ' + (s.stop_reason || 'active') + '</div>'
        + '<div class="sess-model">' + escapeHtml(modelShort) + ' &middot; ' + date + '</div>';
      div.onclick = () => selectSession(s.session_id);
      list.appendChild(div);
    });
  });
}

function updatePermMode() {
  const mode = document.getElementById('perm-mode').value;
  fetch('/api/agent-state', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({permission_mode: mode})
  });
}

function escapeHtml(s) {
  const el = document.createElement('span');
  el.textContent = s || '';
  return el.innerHTML;
}

init();
// Reconnect SSE periodically
setInterval(() => { if (!sseSource || sseSource.readyState === EventSource.CLOSED) connectSSE(); }, 5000);
fetch('/api/state').then(r => r.json()).then(d => {
  if (d.agent_state && d.agent_state.permission_mode) {
    document.getElementById('perm-mode').value = d.agent_state.permission_mode;
  }
});
</script>
</body>
</html>"""


def run_server(cwd: str, host: str, port: int, stream: bool) -> None:
    """Run the GUI server."""
    global _db
    _db = GUIDatabase(cwd=cwd)
    _db.agent_state.streaming = stream

    # Try to auto-detect model from API config
    try:
        from ..api_config import APIConfigRuntime
        api_cfg = APIConfigRuntime(cwd=cwd).get_config()
        _db.agent_state.model_name = api_cfg.model
        _db.agent_state.api_config = {
            "base_url": api_cfg.base_url,
            "model": api_cfg.model,
            "provider": api_cfg.provider,
        }
    except Exception:
        pass

    server = ThreadingHTTPServer((host, port), GUIRequestHandler)
    print(f"GUI server running at http://{host}:{port}")
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()
