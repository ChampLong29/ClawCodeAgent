# Testing Guide

## Quick Run

```bash
# Full suite
python3 -m unittest discover -s tests -v

# Specific module
python3 -m unittest tests.test_agent_tools -v
python3 -m unittest tests.test_bash_security -v
python3 -m unittest tests.test_compact -v
```

## Test Structure

```
tests/
├── test_agent_tools.py        # Tool registry, execution, error handling
├── test_agent_types.py        # Type serialization / deserialization
├── test_bash_security.py      # Security result enum, validators, command checks
├── test_compact.py            # Auto-compact threshold, message compaction
├── test_mcp_runtime.py        # MCP config discovery and parsing
├── test_search_runtime.py     # Search provider discovery, field compatibility
├── test_session_store.py      # Session save / load / list / delete
├── test_task_runtime.py       # Task creation and state management
├── test_team_runtime.py       # Team config discovery (`.claude/teams.json`)
└── test_worktree_runtime.py   # Worktree state path resolution
```

## Coverage by Layer

### Layer 0: Types & Configuration
- `test_agent_types.py` — `UsageStats`, `ModelConfig`, `BudgetConfig`, `AgentRunResult` serialization

### Layer 1: Tools & Security
- `test_agent_tools.py` — `ToolRegistry` has 8+ tools, `execute_tool()` for known/unknown tools, `read_file` success path
- `test_bash_security.py` — `SecurityResult` enum values, 18 validators tracked, safe commands allowed, dangerous patterns (pipe injection, `rm -rf`) trigger ASK/DENY

### Layer 2: Session & Persistence
- `test_session_store.py` — `save_agent_session()` writes JSON, `load_agent_session()` restores messages, nonexistent session raises `FileNotFoundError`, `list_sessions()` returns summary

### Layer 3: Runtime Discovery
- `test_mcp_runtime.py` — Config file discovery, `get_state()` returns dict or None
- `test_search_runtime.py` — Provider field name compatibility (camelCase / snake_case)
- `test_task_runtime.py` — `create_task()` and `get_state()`
- `test_team_runtime.py` — `.claw-teams.json` and `.claude/teams.json` discovery
- `test_worktree_runtime.py` — `get_state()` for worktree configurations

### Layer 4: Context Management
- `test_compact.py` — `AUTOCOMPACT_BUFFER_TOKENS` constant, `should_compact()` logic, `compact_messages()` reduces long lists, preserves short lists

## Writing New Tests

```python
import unittest
from src.your_module import YourClass

class TestYourClass(unittest.TestCase):
    def test_basic_behavior(self):
        obj = YourClass(cwd=".")
        result = obj.some_method()
        self.assertTrue(result)

if __name__ == "__main__":
    unittest.main()
```

### Patterns Used in This Project

1. **Dataclass testing** — verify `to_dict()` / `from_dict()` round-trip
2. **Discovery testing** — create temp config files, instantiate runtime, verify `get_state()` shape
3. **Security testing** — feed dangerous inputs, assert `DENY` / `ASK` results
4. **Persistence testing** — save to temp dir, load back, assert message equality, then clean up

### Known Test Issues

- `test_agent_types.py` — import error (`ToolExecutionResult` moved to `agent_tools.py`). This test needs a one-line import fix but is otherwise structurally correct.

## CI Compatibility

All tests use only the standard library (`unittest`, `tempfile`, `os`, `json`). No external test runners or fixtures required. Run on any machine with Python ≥ 3.10.
