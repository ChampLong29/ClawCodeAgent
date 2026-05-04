# Module Registry

All modules in src/, their import paths, primary responsibilities, and status.

| Module | Path | Import | Responsibility | Status |
|--------|------|--------|---------------|--------|
| **agent_runtime** | `src/agent_runtime.py` | `from src.agent_runtime import LocalCodingAgent` | Main agent loop, tool orchestration, hook integration, streaming, retry | Active |
| **agent_tools** | `src/agent_tools.py` | `from src.agent_tools import execute_tool, ToolRegistry` | 11 built-in tools, tool registry, execution context | Active |
| **agent_types** | `src/agent_types.py` | `from src.agent_types import ModelConfig, AgentPermissions` | Core type definitions (configs, stats, results) | Active |
| **agent_session** | `src/agent_session.py` | `from src.agent_session import AgentSession` | Session message mutation | Active |
| **session_store** | `src/session_store.py` | `from src.session_store import save_agent_session` | Session persistence to `.port_sessions/agent/<id>.json` | Active |
| **agent_context** | `src/agent_context.py` | `from src.agent_context import get_user_context` | Context injection (git, shell, platform, CLAUDE.md) | Active |
| **agent_prompting** | `src/agent_prompting.py` | `from src.agent_prompting import render_system_prompt` | System prompt assembly by runtime capability | Active |
| **openai_compat** | `src/openai_compat.py` | `from src.openai_compat import OpenAICompatClient, AnthropicClient` | OpenAI + Anthropic API clients with streaming | Active |
| **api_config** | `src/api_config.py` | `from src.api_config import APIConfigRuntime` | API provider detection and configuration | Active |
| **bash_security** | `src/bash_security.py` | `from src.bash_security import validate_bash_command, SecurityResult` | 19 security validators (140+ patterns) | Active |
| **compact** | `src/compact.py` | `from src.compact import compact_messages, should_compact` | HYBRID/SUMMARY/TRUNCATE compaction strategies | Active |
| **microcompact** | `src/microcompact.py` | `from src.microcompact import truncate_tool_result` | Lightweight text/message truncation | Active |
| **token_budget** | `src/token_budget.py` | `from src.token_budget import TokenBudget` | Token budget tracking and preflight checks | Active |
| **hook_policy** | `src/hook_policy.py` | `from src.hook_policy import HookPolicyRuntime` | Walk-up policy discovery, budget/deny rules | Active |
| **plugin_runtime** | `src/plugin_runtime.py` | `from src.plugin_runtime import PluginRuntime` | Plugin discovery, aliases, virtual tools | Active |
| **search_runtime** | `src/search_runtime.py` | `from src.search_runtime import SearchRuntime` | SearXNG/Brave/Tavily search execution | Active |
| **plan_runtime** | `src/plan_runtime.py` | `from src.plan_runtime import PlanRuntime` | Multi-step plan management with plan→task sync | Active |
| **task_runtime** | `src/task_runtime.py` | `from src.task_runtime import TaskRuntime` | Task management with dependency tracking | Active |
| **mcp_runtime** | `src/mcp_runtime.py` | `from src.mcp_runtime import MCPRuntime` | MCP protocol integration | Active |
| **remote_runtime** | `src/remote_runtime.py` | `from src.remote_runtime import RemoteRuntime` | SSH/Teleport connections | Active |
| **account_runtime** | `src/account_runtime.py` | `from src.account_runtime import AccountRuntime` | Account profile management | Active |
| **remote_trigger_runtime** | `src/remote_trigger_runtime.py` | `from src.remote_trigger_runtime import RemoteTriggerRuntime` | Remote trigger management | Active |
| **workflow_runtime** | `src/workflow_runtime.py` | `from src.workflow_runtime import WorkflowRuntime` | Workflow management | Active |
| **worktree_runtime** | `src/worktree_runtime.py` | `from src.worktree_runtime import WorktreeRuntime` | Git worktree management | Active |
| **team_runtime** | `src/team_runtime.py` | `from src.team_runtime import TeamRuntime` | Team configuration | Active |
| **agent_manager** | `src/agent_manager.py` | `from src.agent_manager import AgentManagerRuntime` | Agent instance lifecycle + message queue | Active |
| **devflow_runtime** | `src/devflow_runtime.py` | `from src.devflow_runtime import DevFlowRuntime` | DevFlow state machine, session/step/module management, persistence | Active |
| **devflow_skills** | `src/devflow_skills.py` | `from src.devflow_skills import get_devflow_skill` | 5 DevFlow skill templates (architect, step-planner, step-analyzer, implementer, verifier) | Active |
| **lifecycle_runtime** | `src/lifecycle_runtime.py` | `from src.lifecycle_runtime import LifecycleRuntime` | Full 10-phase software lifecycle, wraps DevFlow, configurable phases | Active |
| **lifecycle_skills** | `src/lifecycle_skills.py` | `from src.lifecycle_skills import LIFECYCLE_SKILLS` | 6 Lifecycle skill templates (requirements, design, code-review, unit-test, integration-test, acceptance) | Active |
| **bridge_runtime** | `src/bridge_runtime.py` | `from src.bridge_runtime import BridgeRuntime` | External platform bridge, session routing, webhook ingress | Active |
| **bundled_skills** | `src/bundled_skills.py` | `from src.bundled_skills import get_skill` | 15 bundled skills registry (4 general + 5 DevFlow + 6 Lifecycle) | Active |
| **session_store** | `src/session_store.py` | `from src.session_store import save_agent_session, list_sessions_by_prefix` | Session persistence, list by name prefix | Active |
| **query_engine** | `src/query_engine.py` | `from src.query_engine import QueryEngine` | Facade for LocalCodingAgent; supports permission_callback for GUI | Active |
| **main** | `src/main.py` | CLI entry `python3 -m src.main` | CLI routing (agent, sessions, devflow, lifecycle, bridge, train) | Active |
| **repl** | `src/repl.py` | `from src.repl import ClawRepl` | Interactive REPL: /devflow, /lifecycle, /name, streaming, permissions, session history | Active |
| **permission_manager** | `src/gui/permission_manager.py` | `from src.gui.permission_manager import PermissionManager` | Thread-safe permission request manager with threading.Event | Active |
| **sse_routes** | `src/gui/sse_routes.py` | `from src.gui.sse_routes import handle_sse, push_event` | SSE /api/stream endpoint for real-time event push | Active |
| **session_routes** | `src/gui/session_routes.py` | `from src.gui.session_routes import handle_request` | Session CRUD API (list enriched, detail, resume, delete) | Active |
| **bridge_routes** | `src/gui/bridge_routes.py` | `from src.gui.bridge_routes import handle_request` | Bridge webhook ingress, routing table, session lookup | Active |
| **anthropic_compat** | `src/anthropic_compat.py` | `from src.anthropic_compat import AnthropicCompatClient` | **DEPRECATED** — use `AnthropicClient` from `openai_compat` | Legacy |

## GUI Modules

| Module | Path | Responsibility |
|--------|------|----------------|
| **server** | `src/gui/server.py` | ThreadingHTTPServer, chat UI HTML, async query with SSE, permission response routing |
| **permission_manager** | `src/gui/permission_manager.py` | Thread-safe blocking permission request manager (threading.Event) |
| **sse_routes** | `src/gui/sse_routes.py` | SSE endpoint `/api/stream` for real-time event streaming |
| **session_routes** | `src/gui/session_routes.py` | Session CRUD API: list (enriched), detail, resume, delete |
| **bridge_routes** | `src/gui/bridge_routes.py` | `/api/bridge/*` — webhook ingress, routing table, sessions |
| **plans_routes** | `src/gui/plans_routes.py` | `/api/plans/*` — plan CRUD + sync |
| **tasks_routes** | `src/gui/tasks_routes.py` | `/api/tasks/*` — task CRUD |
| **workflow_routes** | `src/gui/workflow_routes.py` | `/api/workflows/*` — workflow status/list |
| **remote_trigger_routes** | `src/gui/remote_trigger_routes.py` | `/api/triggers/*` — trigger management |
| **search_routes** | `src/gui/search_routes.py` | `/api/search/*` — search execution |
| **mcp_routes** | `src/gui/mcp_routes.py` | `/api/mcp/*` — MCP status |
| **remote_routes** | `src/gui/remote_routes.py` | `/api/remote/*` — SSH/Teleport status |
| **account_routes** | `src/gui/account_routes.py` | `/api/account/*` — account profiles |
| **worktree_routes** | `src/gui/worktree_routes.py` | `/api/worktree/*` — worktree management |
| **team_routes** | `src/gui/team_routes.py` | `/api/teams/*` — team config |
| **plugins_routes** | `src/gui/plugins_routes.py` | `/api/plugins/*` — plugin listing |
| **memory_routes** | `src/gui/memory_routes.py` | `/api/memory/*` — session memory |
| **diagnostics_routes** | `src/gui/diagnostics_routes.py` | `/api/diagnostics/*` — system diagnostics |
| **background_routes** | `src/gui/background_routes.py` | `/api/background/*` — background tasks |
| **ask_user_routes** | `src/gui/ask_user_routes.py` | `/api/ask-user/*` — user interaction |

## Training Modules

| Module | Path | Responsibility |
|--------|------|----------------|
| **tasks** | `src/training/tasks.py` | CodingTask definitions and serialization |
| **runner** | `src/training/runner.py` | RolloutRunner — parallel/serial episode execution |
| **sandbox** | `src/training/sandbox.py` | Sandbox environment management |
| **agent_env** | `src/training/agent_env.py` | Agent environment and configuration |
| **determinism** | `src/training/determinism.py` | Determinism utilities (seed control) |
