# Architecture

## System Overview

Claw Code Agent is a Python implementation of a Claude Code-style agent runtime. It provides an autonomous coding agent with tool calling, session management, hook/policy enforcement, and runtime module extensibility.

## Data Flow

```
User Input (CLI/GUI)
    -> src/main.py (argument parsing + routing)
        -> QueryEngine (facade)
            -> LocalCodingAgent (agent loop)
                -> PromptContext (system prompt + context)
                -> ModelClient (API call)
                -> ToolExecutor (tool dispatch)
                -> SessionState (message history)
                -> Runtime Modules (context injection)
```

## Request Lifecycle

1. **Initialization**: `LocalCodingAgent.__post_init__()` sets up API client, permissions, hook/policy runtime, plugin runtime, and blocked_tools/tool_aliases/virtual_tools.
2. **Session Start**: `run()` creates a new session or `resume()` loads an existing one.
3. **Main Loop** (`_run_loop()`):
   - Budget override from policy config
   - Context assembly (git, shell, platform, CLAUDE.md, runtime summaries)
   - System prompt rendering with hook/plugin guidance
   - Before-prompt hook injection
   - Compact check via `should_compact()`
   - Model call (streaming or non-streaming) with retry on 429/503/5xx
   - Response handling (content + tool calls)
   - Tool preflight (blocked_tools check, alias mapping)
   - Tool execution with security validation (bash commands)
   - Post-hook result processing
   - Session persistence

## Hook Execution Order

Per `CLAUDE.md`, hooks execute in this strict order:
1. **Budget Override**: Policy configuration overrides budget limits
2. **Plugin Registration**: Collects blocked_tools, tool_aliases, virtual_tools
3. **Before-Prompt**: Policy/plugin guidance injected into system prompt
4. **Tool Preflight**: Blocked tool check, alias mapping before execution
5. **Tool Result Post-Hooks**: Process tool results after execution

## Module Layering

```
src/main.py              — CLI entry point
src/query_engine.py       — Facade for LocalCodingAgent
src/agent_runtime.py      — Core agent loop (~5000 lines)
src/agent_tools.py        — 11 built-in tools + execution
src/agent_session.py      — Session mutation
src/session_store.py      — Session persistence
src/agent_context.py      — Context injection
src/agent_prompting.py    — System prompt assembly
src/openai_compat.py      — OpenAI + Anthropic API clients
src/bash_security.py      — 19 security validators
src/compact.py            — Context compression (HYBRID/SUMMARY/TRUNCATE)
src/microcompact.py       — Lightweight compaction helpers
src/token_budget.py       — Token budget tracking
src/hook_policy.py        — Hook/policy discovery (walk-up)
src/plugin_runtime.py     — Plugin discovery and management
src/api_config.py         — API configuration provider detection

# Runtime Modules (each provides get_state(), render_summary(), get_prompt_guidance())
src/search_runtime.py     — SearXNG, Brave, Tavily search providers
src/plan_runtime.py       — Multi-step plan management
src/task_runtime.py       — Task management with dependency tracking
src/mcp_runtime.py        — MCP protocol integration
src/remote_runtime.py     — SSH/Teleport connections
src/account_runtime.py    — Account profiles
src/remote_trigger_runtime.py — Remote triggers
src/workflow_runtime.py   — Workflow management
src/worktree_runtime.py   — Git worktree management
src/team_runtime.py       — Team configuration
src/agent_manager.py      — Agent instance lifecycle
src/devflow_runtime.py    — DevFlow structured development workflow (module-by-module)
src/devflow_skills.py     — 5 DevFlow skill prompt templates
src/lifecycle_runtime.py  — Full 10-phase software engineering lifecycle (wraps DevFlow)
src/lifecycle_skills.py   — 6 Lifecycle skill prompt templates
src/bridge_runtime.py     — External platform bridge integration
src/bundled_skills.py     — 15 bundled skills registry

# Training Subsystem
src/training/tasks.py     — CodingTask definitions
src/training/runner.py    — RolloutRunner for parallel/serial episode execution
src/training/sandbox.py   — Sandbox environment management

# GUI Subsystem
src/gui/server.py         — HTTP server + AgentState
src/gui/*_routes.py       — 19 API route modules (including bridge_routes)
```
