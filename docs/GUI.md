# GUI

## Overview

HTTP-based GUI server providing a web chat interface and REST API for all agent runtimes. Features:
- **Chat UI** — Three-panel layout (session list, chat area, input area) with real-time SSE streaming
- **Permission system** — Interactive shell permission confirmation cards in the chat UI
- **Session management** — Full CRUD via API + session list sidebar in the web UI

## Starting the GUI

```bash
python3 -m src.gui --cwd . --host 127.0.0.1 --port 8765 --stream
```

## AgentState

```python
@dataclass
class AgentState:
    model_name: str                # Model identifier
    allowed_tools: List[str]       # Whitelist of allowed tools
    permission_mode: str           # read-only, allow-write, allow-shell
    session_id: Optional[str]      # Current session ID
    streaming: bool                # Whether to use streaming mode
    cwd: str                       # Working directory
    api_config: Optional[dict]     # API configuration for runtime sharing
```

## GUIDatabase

The in-memory state hub for the GUI server:

```python
class GUIDatabase:
    cwd: str                       # Working directory
    agent_state: AgentState        # Current agent configuration
    sessions: Dict[str, Any]       # Session cache (synced from disk)
    event_queue: queue.Queue       # SSE event bus (thread-safe)
    permission_manager: PermissionManager  # Thread-safe permission request manager
```

## Server Architecture

The GUI uses `ThreadingHTTPServer` (HTTPServer + ThreadingMixIn) so each request is handled in its own thread. This allows:

- **SSE long-polling** — `/api/stream` holds a connection open in one thread while other threads serve API calls
- **Blocking permission callbacks** — Agent thread blocks on `PermissionManager.wait_for_response()` while the HTTP handler thread calls `PermissionManager.respond()`
- **Background agent execution** — `/api/query` spawns a daemon thread for the agent, returning immediately with `{"session_id": "...", "status": "started"}`

### Query Flow

```
Browser POST /api/query
  → server.py spawns background thread
  → returns {"session_id": "...", "status": "started"} immediately
  → Background thread:
      → creates LocalCodingAgent with gui_permission_callback
      → agent runs, pushing events to event_queue:
          "status" (started)
          "text" (streaming content)
          "permission_required" (bash permission needed)
          "session" (new session ID)
          "done" (complete / error)
  → Browser SSE connection reads from event_queue
      → Renders text in chat
      → Shows permission card on "permission_required"
```

### Permission Flow

```
Agent calls bash → SecurityValidator returns ASK
  → gui_permission_callback:
      → PermissionManager.create_request(tool_name, cmd)
      → Pushes "permission_required" SSE event to queue
      → PermissionManager.wait_for_response() blocks agent thread
  → Browser receives SSE event → shows permission card
  → User clicks [Allow Once] → Browser POSTs /api/permission-response
      → PermissionManager.respond(request_id, "allow")
      → Agent thread unblocks → returns True → bash executes
```

## Route System

Each runtime module has a corresponding route file:

| Route Prefix | Route File | Runtime / Feature |
|-------------|------------|---------|
| `/api/state` | server.py | Full AgentState |
| `/api/query` | server.py | Agent query execution (async, background thread) |
| `/api/permission-response` | server.py | User response to permission prompt |
| `/api/stream` | sse_routes.py | SSE real-time event stream |
| `/api/sessions` | session_routes.py | Session CRUD (list, detail, resume, delete) |
| `/api/plans` | plans_routes.py | PlanRuntime |
| `/api/tasks` | tasks_routes.py | TaskRuntime |
| `/api/workflows` | workflow_routes.py | WorkflowRuntime |
| `/api/triggers` | remote_trigger_routes.py | RemoteTriggerRuntime |
| `/api/search` | search_routes.py | SearchRuntime |
| `/api/mcp` | mcp_routes.py | MCPRuntime |
| `/api/remote` | remote_routes.py | RemoteRuntime |
| `/api/account` | account_routes.py | AccountRuntime |
| `/api/worktree` | worktree_routes.py | WorktreeRuntime |
| `/api/teams` | team_routes.py | TeamRuntime |
| `/api/plugins` | plugins_routes.py | PluginRuntime |
| `/api/memory` | memory_routes.py | Session memory |
| `/api/diagnostics` | diagnostics_routes.py | System diagnostics |
| `/api/background` | background_routes.py | Background tasks |
| `/api/ask-user` | ask_user_routes.py | User interaction |
| `/api/bridge` | bridge_routes.py | BridgeRuntime — webhook ingress, routing, sessions |

## API Endpoints

### State
```
GET  /api/state        → Full dashboard state
POST /api/agent-state  → Update agent configuration
```

### Query
```
POST /api/query        → Execute agent query (returns immediately, results via SSE)
Body: {"prompt": "do something", "session_id": "optional-session-id"}
```

### Permission
```
POST /api/permission-response  → Respond to a pending permission request
Body: {"request_id": "abc123", "action": "allow|deny|allow_all"}
```

### SSE Stream
```
GET /api/stream        → SSE event stream (long-lived connection)
Events:
  event: status        → {"status": "started", "session_id": "..."}
  event: text          → {"text": "streaming content..."}
  event: tool_call     → {"tool_name": "bash", "args": {...}}
  event: permission_required → {"request_id": "...", "command": "ls -la"}
  event: session       → {"session_id": "abc12345"}
  event: done          → {"stop_reason": "completed", "final_message": "..."}
  event: error         → {"message": "Error description"}
  event: ping          → {} (keepalive every 30s)
```

### Sessions
```
GET    /api/sessions                → List all sessions (enriched: model, cwd, stop_reason)
GET    /api/sessions/<id>           → Session detail with recent messages
POST   /api/sessions/<id>/resume    → Resume a session (sets it as active)
DELETE /api/sessions/<id>           → Delete a session file
```

### Plans
```
GET  /api/plans/status     → PlanRuntime.get_state()
POST /api/plans/create     → Create plan with steps
POST /api/plans/update     → Update plan step status
```

### Tasks
```
GET  /api/tasks/status     → TaskRuntime.get_state()
GET  /api/tasks/list       → List all tasks
GET  /api/tasks/<id>       → Get task by ID
POST /api/tasks/create     → Create new task
POST /api/tasks/update     → Update task status/detail
```

### Workflows
```
GET  /api/workflows/status → WorkflowRuntime.get_state()
GET  /api/workflows/list   → List workflows
POST /api/workflows/run    → Run a workflow
```

### Triggers
```
GET  /api/triggers/status  → RemoteTriggerRuntime.get_state()
GET  /api/triggers/list    → List triggers
POST /api/triggers/run     → Execute a trigger
```

## Handler Pattern

All route files follow the same pattern:
```python
def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    cwd = db.agent_state.cwd
    runtime = SomeRuntime(cwd=cwd)
    # Dispatch by method and path
    handler.send_json(result)
```
