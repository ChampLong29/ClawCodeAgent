# Claw Code Agent

Python implementation of a Claude Code-style agent runtime with OpenAI-compatible model interfaces, multi-turn tool calling, and an extensible runtime module system.

## Quick Start

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .

# Configure (example values)
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=local-token
export OPENAI_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct

# Run
python3 -m src.main agent "Explain the project structure" --cwd . --stream

# Interactive REPL
python3 -m src.main agent-chat --cwd .
```

See `.env.example` for the full list of supported environment variables.

## Architecture

```
User (CLI / REPL / GUI)
  └── main.py (argparse + routing)
       └── LocalCodingAgent (agent_runtime.py)
            ├── System Prompt (agent_context.py + agent_prompting.py)
            ├── Model Client (openai_compat.py / anthropic_compat.py)
            ├── Tool Registry + Executor (agent_tools.py)
            ├── Session + Store (agent_session.py + session_store.py)
            └── Runtime Modules (19 modules: mcp, search, remote, devflow, ...)
```

### Key Components

| Module | Role |
|--------|------|
| `agent_runtime.py` | Agent main loop, tool call execution, budget control, turn management |
| `agent_tools.py` | 10 built-in tools + MCP tools + plugin virtual tools |
| `bash_security.py` | Command safety validation (ALLOW / ASK / DENY / PASSTHROUGH) |
| `compact.py` | Context compression when tokens exceed threshold |
| `token_budget.py` | Per-run token / model-call / tool-call budget enforcement |
| `mcp_runtime.py` | MCP protocol: server process management + JSON-RPC tool bridging |
| `plugin_runtime.py` | Plugin discovery, virtual tool registration, tool aliases / blocks |
| `devflow_runtime.py` | Structured development workflow: lifecycle state machine, step execution, persistence |
| `repl.py` | Interactive terminal with permission prompts, /devflow commands, session history + resume |
| `query_engine.py` | Facade API for embedding the agent in other applications |
| `gui/server.py` | ThreadingHTTPServer with chat UI, SSE streaming, and interactive permission confirmation |
| `gui/permission_manager.py` | Thread-safe permission request manager for GUI agent workflows |
| `gui/sse_routes.py` | Server-Sent Events endpoint for real-time agent streaming |
| `gui/session_routes.py` | Session CRUD API: list, detail, resume, delete |

### Built-in Tools

`list_dir` `read_file` `write_file` `edit_file` `glob_search` `grep_search` `bash`
`non_tool_call` `web_search` `web_fetch` `use_skill`

Plus dynamically registered MCP tools (`mcp__{server}__{tool}`) and plugin virtual tools.

## Configuration

### CLI

```bash
python3 -m src.main agent "prompt" --cwd . --max-turns 50 --stream
python3 -m src.main agent-chat --cwd . --max-turns 30
```

### Permission Modes (REPL)

| Command | Effect |
|---------|--------|
| `/allow-shell` | Enable bash execution |
| `/deny-shell` | Disable bash execution |
| `/allow-write` | Enable file writes |
| `/deny-write` | Disable file writes |
| `/permissions` | Show current state |
| `/status` | Agent + runtime status dump |
| `/compact` | Trigger context compaction |
| `/budget` | Show token usage |
| `/sessions` | List all saved agent sessions |
| `/resume <id>` | Resume a saved session |
| `/devflow start <goal>` | Start structured development workflow |
| `/devflow status` | Show DevFlow progress + dependency tree |
| `/devflow accept` | Accept architecture / steps / verified result |
| `/devflow reject` | Reject and request regeneration |

### Plugin Configuration

Drop a `plugin.json` in `.claw-plugin/`, `.codex-plugin/`, or `plugins/<name>/`:

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "blocked_tools": ["bash"],
  "tool_aliases": [{"name": "search", "target": "grep_search"}],
  "virtual_tools": [
    {
      "name": "deploy",
      "description": "Deploy the project",
      "command": "kubectl apply -f {file}",
      "parameters": {"type": "object", "properties": {"file": {"type": "string"}}}
    }
  ]
}
```

### MCP Server Config

Create `.claw-mcp.json` in the project root to bridge external MCP servers:

```json
{
  "servers": [
    {
      "name": "filesystem",
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
    }
  ]
}
```

Tools are auto-discovered and registered as `mcp__{server}__{tool}` at agent startup.

## Testing

```bash
python3 -m unittest discover -s tests -v
```

See `TESTING_GUIDE.md` for detailed test documentation.

## License

MIT
