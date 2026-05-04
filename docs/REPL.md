# REPL (Interactive Shell)

## Overview

`src/repl.py` provides a rich interactive terminal experience for Claw Code Agent.

## Starting

```bash
python3 -m src.main agent-chat --cwd .
```

## Features

### Line Editing

Uses GNU `readline` for:
- Command history (persisted to `~/.claw_history`, 1000 entries)
- Cursor movement (arrow keys, Ctrl-A/E, Alt-F/B)
- Word deletion (Ctrl-W, Alt-D)
- Tab completion
- Emacs keybindings

### Streaming Output

All agent responses are streamed token-by-token in real-time. No waiting for the full response.

### Tool Call Visibility

Each tool call displays an icon + tool name + truncated arguments:

```
  📖 read_file {"path": "src/main.py"}
  ✏️ write_file {"path": "src/test.py"}
  ⚡ bash {"command": "ls -la"}
  🔍 glob_search {"pattern": "*.py"}
  🔎 grep_search {"pattern": "class.*Agent"}
```

Tool icons:
| Icon | Tool |
|------|------|
| ⚡ | bash |
| 📖 | read_file |
| ✏️ | write_file |
| ✂️ | edit_file |
| 📁 | list_dir |
| 🔍 | glob_search |
| 🔎 | grep_search |
| 💬 | non_tool_call |

### Session History

On startup, the REPL shows the 5 most recent sessions:

```
  Recent sessions:
    a1b2c3d4   12 msgs  completed
    e5f6g7h8    5 msgs  stopped
    9i0j1k2l    8 msgs  active
  Use /sessions to list all, /resume <id> to continue
```

Use `/sessions` to see all sessions with full details, or `/resume <id>` to pick up where you left off. Each session persists its `cwd` (working directory), `model`, and full message history.

### Permission Prompts

When the agent tries to execute an ASK-level bash command without `allow_shell`:

```
  ⚡ Permission required for: curl http://example.com
  Execute? [y]es / [n]o / [a]llow all shell: █
```

- `y` — execute this one command
- `n` — skip this command
- `a` — enable shell access for the rest of the session

DENY-level commands are always blocked with `🚫 Blocked`.

### Session Commands

| Command | Action |
|---------|--------|
| `/name` | Show current session name |
| `/name <text>` | Set session name (persisted to session store) |
| `/sessions` | List all saved agent sessions (enriched: model, cwd, stop_reason, name) |
| `/resume <id>` | Resume a saved session by its session ID |

### General Slash Commands

| Command | Action |
|---------|--------|
| `/help`, `/h` | Show full grouped help (Session, Context, DevFlow, Lifecycle) |
| `/permissions` | Show current permission state |
| `/allow-shell` | Enable shell execution |
| `/deny-shell` | Disable shell execution |
| `/allow-write` | Enable file writing |
| `/deny-write` | Disable file writing |
| `/status` | Show agent state (JSON) with all runtime states |
| `/compact` | Trigger context compaction |
| `/budget` | Show token budget usage |
| `/retry` | Retry last assistant message |
| `/clear` | Clear terminal |
| `/exit`, `/quit`, `/q` | Exit REPL |

### DevFlow Commands

Structured development workflow with module-by-module implementation:

| Command | Action |
|---------|--------|
| `/devflow start <goal>` | Start a new DevFlow session with the given development goal |
| `/devflow status` | Show progress bar, dependency tree, and step statuses |
| `/devflow step` | Show detailed view of current step (goal, constraints, acceptance criteria, modules, deps) |
| `/devflow accept` | Approve current output (architecture, steps, modules, or verified result) and advance |
| `/devflow reject [reason]` | Reject current output and request regeneration |
| `/devflow skip` | Skip the current step or module |
| `/devflow archive` | Save the full session report to a Markdown file |
| `/devflow list` | List all saved DevFlow sessions |
| `/devflow load <id>` | Load a previously saved DevFlow session |

### Lifecycle Commands

Full software engineering lifecycle (10 configurable phases):

| Command | Action |
|---------|--------|
| `/lifecycle start <goal>` | Start a new lifecycle session with the given development goal |
| `/lifecycle status` | Show lifecycle phase progress with status icons |
| `/lifecycle accept` | Approve current phase output and advance to next phase |
| `/lifecycle reject [reason]` | Reject current phase output and request regeneration |
| `/lifecycle skip-phase` | Skip the current phase |
| `/lifecycle archive` | Export full lifecycle report to Markdown |
| `/lifecycle list` | List all saved lifecycle sessions |
| `/lifecycle load <id>` | Load a previously saved lifecycle session |

### DevFlow Visualization

**Dependency Tree** (`/devflow status`):
```
╭─ DevFlow: User Auth System ───────────────────╮
│  ✅ step-1: Define data model      [verified] │
│  ├── ▶ step-2: Implement register [in_progress]│
│  └── ◇ step-3: Password hashing    [pending]  │
│  Progress: ████████░░░░ 33% (1/3 verified)    │
╰───────────────────────────────────────────────╯
```

Icons: `✅` verified, `▶` in_progress, `●` implemented, `◇` pending, `✖` failed

**Step Detail** (`/devflow step`):
Shows the current step's goal, constraints, acceptance criteria, dependency status, module list (file_path, goal, status), and current status with color-coded formatting.

### Lifecycle Visualization

**Phase Progress** (`/lifecycle status`):
```
╭─ Lifecycle: Build user auth system ───────────╮
│  ✅ REQUIREMENTS              [completed]      │
│  ✅ SYSTEM_DESIGN             [completed]      │
│  ▶ ARCHITECTURE               [in_progress] ← current
│  ◇ STEP_DEFINITION            [pending]        │
│  ◇ IMPLEMENTATION             [pending]        │
│  ⏭️ CODE_REVIEW              [skipped]        │
│  ◇ UNIT_TEST                  [pending]        │
│  Progress: ██████░░░░░░ 25% (2/8)             │
╰───────────────────────────────────────────────╯
```

Icons: `✅` completed, `▶` in_progress, `◇` pending, `⏭️` skipped, `✖` failed

### Prompt Format

```
╭─ [S][w] You       ← S=shell ON, s=shell OFF; W=write ON, w=write OFF
╰> your query here
```

## Architecture

```
ClawRepl.__init__()
  → _setup_readline()          # Configure line editing + history
  → AgentPermissions(...)       # Safe defaults (allow_shell=False)
  → DevFlowRuntime(cwd)        # DevFlow session manager
  → LifecycleRuntime(cwd)      # Lifecycle session manager

ClawRepl.run()
  → _print_banner()            # Session ID, model, permissions
  → loop:
      _read_input()            # Read with styled prompt
      → /devflow → _handle_devflow()     # DevFlow sub-commands
      → /lifecycle → _handle_lifecycle()  # Lifecycle sub-commands
      → /help → _print_full_help()       # Full grouped help
      → /name → session naming           # Set or show name
      → shared registry → /retry, /compact, /budget
      _execute(prompt)         # agent.run(prompt, stream=True)
        → LocalCodingAgent._run_loop(stream=True)
          → _stream_openai() / _stream_anthropic()  # Real-time token output
          → _print_tool_call()                       # Tool visibility
          → execute_tool() with permissions          # Security validation
```

## Class Reference

```python
class ClawRepl:
    def __init__(self, cwd, model=None, temperature=0.1, max_tokens=None)
    def run(self)                                         # Start REPL loop
    def _read_input(self) -> Optional[str]                # Read user input
    def _handle_slash(self, command) -> bool              # Process /commands
    def _execute(self, prompt)                            # Run agent query
    def _handle_interactive_permission(self, tool, args)  # Permission prompt
    def _print_permissions(self)                          # Show perm state
    def _print_banner(self)                               # Welcome message
    def _print_recent_sessions(self)                      # Show recent 5 sessions
    def _cmd_sessions(self)                               # List all saved sessions
    def _cmd_resume(self, session_id)                     # Resume a saved session
    def _new_agent(self)                                  # Create/reset agent
    # DevFlow methods
    def _handle_devflow(self, cmd)                        # Dispatch /devflow sub-commands
    def _devflow_start(self, goal)                        # Start new DevFlow session
    def _devflow_status(self)                             # Show tree + progress
    def _devflow_step_detail(self)                        # Show step + module details
    def _devflow_accept(self)                             # Accept phase/module output
    def _devflow_reject(self, reason)                     # Reject and regenerate
    def _devflow_skip(self)                               # Skip current step or module
    def _devflow_archive(self)                            # Save to Markdown
    def _devflow_list(self)                               # List saved DevFlow sessions
    def _devflow_load(self, session_id)                   # Load saved DevFlow session
    def _devflow_run_analyze_phase(self, agent)           # Run STEP_ANALYSIS phase
    def _devflow_run_module_cycle(self, agent)            # Module-by-module execution
    def _print_devflow_tree(self)                         # Render dependency tree
    def _print_step_detail(self, step, session)           # Render step detail panel
    # Lifecycle methods
    def _handle_lifecycle(self, cmd)                      # Dispatch /lifecycle sub-commands
    def _lifecycle_start(self, goal)                      # Start new lifecycle session
    def _lifecycle_status(self)                           # Show phase progress
    def _lifecycle_accept(self)                           # Accept current phase
    def _lifecycle_reject(self, reason)                   # Reject and retry phase
    def _lifecycle_skip_phase(self)                       # Skip current phase
    def _lifecycle_archive(self)                          # Export full report
    def _lifecycle_list(self)                             # List saved sessions
    def _lifecycle_load(self, session_id)                 # Load saved session
    def _print_lifecycle_status(self)                     # Render phase progress panel
    def _print_full_help(self)                            # Render full grouped help
```
