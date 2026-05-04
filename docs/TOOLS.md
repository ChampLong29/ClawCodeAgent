# Tools

## 11 Built-in Tools

| Tool | Handler | Icon | Description | Key Parameters |
|------|---------|------|-------------|---------------|
| `list_dir` | `_list_dir` | 📁 | List directory contents | `path` |
| `read_file` | `_read_file` | 📖 | Read file contents | `path`, `limit`, `offset` |
| `write_file` | `_write_file` | ✏️ | Write content to file | `path`, `content` |
| `edit_file` | `_edit_file` | ✂️ | Replace text in file | `path`, `old_string`, `new_string`, `count` |
| `glob_search` | `_glob_search` | 🔍 | Find files by pattern | `pattern`, `cwd` |
| `grep_search` | `_grep_search` | 🔎 | Search text with regex | `pattern`, `path`, `recursive` |
| `bash` | `_bash` | ⚡ | Execute bash command | `command` |
| `non_tool_call` | `_non_tool_call` | 💬 | Respond without tool | `content` |
| `web_search` | `_web_search` | 🌐 | Search the web | `query` |
| `web_fetch` | `_web_fetch` | 🌍 | Fetch URL content | `url`, `prompt` |
| `use_skill` | `_use_skill` | 🎯 | Invoke a bundled skill | `skill`, `code`, `**kwargs` |

Tool call icons are displayed in the REPL via `_print_tool_call()` during agent execution for real-time visibility.

## Bundled Skills (15)

Skills are invoked via the `use_skill` tool and provide structured prompt templates for specific tasks.

### General Skills (4)

| Skill | Description | Parameters |
|-------|-------------|------------|
| `explain-code` | Explain how code works | `code` |
| `review-code` | Review code for issues | `code` |
| `generate-tests` | Generate unit tests | `code`, `language` |
| `document-code` | Generate documentation | `code` |

### DevFlow Skills (5)

| Skill | Description | Parameters |
|-------|-------------|------------|
| `devflow-architect` | Analyze requirements and propose architecture | `goal`, `constraints` |
| `devflow-step-planner` | Break architecture into steps with dependencies | `goal`, `architecture` |
| `devflow-step-analyzer` | Break a step into file-level modules | `goal`, `architecture`, `step_title`, `step_goal`, `step_constraints` |
| `devflow-implementer` | Implement a single step or module | `goal`, `architecture`, `step_title`, `step_goal`, `step_constraints`, `acceptance_criteria` |
| `devflow-verifier` | Verify implementation against acceptance criteria | `step_title`, `acceptance_criteria`, `implementation_result` |

### Lifecycle Skills (6)

| Skill | Description | Parameters |
|-------|-------------|------------|
| `lifecycle-requirements` | Requirements analysis (EARS, user stories) | `goal`, `constraints` |
| `lifecycle-design` | System design (modules, data model, API) | `goal`, `requirements_summary`, `constraints` |
| `lifecycle-code-review` | Code review (security, performance, quality) | `goal`, `implementation_summary` |
| `lifecycle-unit-test` | Unit test generation (>80% coverage) | `goal`, `implementation_summary` |
| `lifecycle-integration-test` | Integration test (API, E2E, data flow) | `goal`, `requirements_summary`, `implementation_summary` |
| `lifecycle-acceptance` | Acceptance testing (requirements traceability) | `goal`, `requirements_summary`, `implementation_summary` |

## Handler Signature

```python
def handler(param1: str, param2: Optional[int] = None, **kwargs) -> Dict[str, Any]:
    """Tool handler function.

    Args:
        param1, param2: Tool-specific parameters from model
        **kwargs: Includes `permissions` dict when execute_tool() passes context

    Returns:
        Dict with at minimum {"ok": True/False}. May include:
        - "error": error message string
        - "stdout": standard output (bash)
        - "stderr": standard error (bash)
        - "content": text content (read_file, non_tool_call)
        - "entries": directory entries (list_dir)
        - "matches": search matches (glob_search, grep_search)
    """
```

## Permissions Flow

The `**kwargs` of tool handlers receive `permissions` dict from `ToolExecutionContext`:

```python
# execute_tool() passes permissions from context:
kwargs = dict(arguments)
if context and context.permissions:
    kwargs["permissions"] = context.permissions
result = tool.handler(**kwargs)

# _bash() reads permissions:
def _bash(command, **kwargs):
    permissions = kwargs.get("permissions", {})
    if security_result == SecurityResult.ASK:
        if not permissions.get("allow_shell", False):
            return {"ok": False, "error": "Shell access not permitted"}
```

## Bash Security Validation

Before any `subprocess.run()`, the `_bash()` handler calls `validate_bash_command()`:
- `DENY`: Returns error immediately, no execution
- `ASK`: Requires `permissions.allow_shell=True` to proceed
- `ALLOW`: Executes normally
- `PASSTHROUGH`: Executes with warning

## Adding a New Tool

1. Define handler function in `src/agent_tools.py`:
```python
def _my_new_tool(arg1: str, **kwargs) -> Dict[str, Any]:
    try:
        # Do something
        return {"ok": True, "result": "success"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

2. Register in `_build_default_registry()`:
```python
registry.register(AgentTool(
    name="my_new_tool",
    description="Does something useful",
    parameters={
        "type": "object",
        "properties": {
            "arg1": {"type": "string", "description": "First argument"},
        },
        "required": ["arg1"],
    },
    handler=_my_new_tool,
))
```

3. The tool automatically appears in `_get_toolspec()` and is callable by the model.

## edit_file `count` Parameter

The `edit_file` tool supports a `count` parameter:
- `count=1` (default): Replace first occurrence only
- `count=N`: Replace first N occurrences
- `count=-1`: Replace all occurrences
