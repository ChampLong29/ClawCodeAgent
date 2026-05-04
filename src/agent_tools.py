"""Tool protocol and execution for CodeAgent."""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
import subprocess
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, AsyncIterator

from .bash_security import validate_bash_command, SecurityResult


@dataclass
class AgentTool:
    """A tool available to the agent."""
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., Any]
    tags: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass
class ToolExecutionContext:
    """Context passed to tool execution."""
    cwd: str
    runtime_context: Optional[Dict[str, Any]] = None
    permissions: Optional[Dict[str, Any]] = None


@dataclass
class ToolRegistry:
    """Registry of available tools."""
    tools: Dict[str, AgentTool] = field(default_factory=dict)

    def register(self, tool: AgentTool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Optional[AgentTool]:
        return self.tools.get(name)

    def list_tools(self) -> List[AgentTool]:
        return list(self.tools.values())

    def list_tool_names(self) -> List[str]:
        return list(self.tools.keys())


# Global registry
_global_registry: Optional[ToolRegistry] = None


def default_tool_registry() -> ToolRegistry:
    """Get or create the default tool registry with built-in tools."""
    global _global_registry
    if _global_registry is None:
        _global_registry = _build_default_registry()
    return _global_registry


def _build_default_registry() -> ToolRegistry:
    """Build the default tool registry with 8 built-in tools."""
    registry = ToolRegistry()

    # list_dir tool
    registry.register(AgentTool(
        name="list_dir",
        description="List directory contents",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list"},
            },
            "required": ["path"],
        },
        handler=_list_dir,
    ))

    # read_file tool
    registry.register(AgentTool(
        name="read_file",
        description="Read file contents",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
                "limit": {"type": "integer", "description": "Max lines to read"},
                "offset": {"type": "integer", "description": "Line offset to start from"},
            },
            "required": ["path"],
        },
        handler=_read_file,
    ))

    # write_file tool
    registry.register(AgentTool(
        name="write_file",
        description="Write content to a file",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
        handler=_write_file,
    ))

    # edit_file tool
    registry.register(AgentTool(
        name="edit_file",
        description="Edit a file with replacements",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit"},
                "old_string": {"type": "string", "description": "String to replace"},
                "new_string": {"type": "string", "description": "Replacement string"},
                "count": {"type": "integer", "description": "Number of replacements (-1 = all, default 1)"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        handler=_edit_file,
    ))

    # glob_search tool
    registry.register(AgentTool(
        name="glob_search",
        description="Search for files matching a pattern",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern to match"},
                "cwd": {"type": "string", "description": "Working directory"},
            },
            "required": ["pattern"],
        },
        handler=_glob_search,
    ))

    # grep_search tool
    registry.register(AgentTool(
        name="grep_search",
        description="Search for text in files",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search"},
                "path": {"type": "string", "description": "Directory or file to search"},
                "recursive": {"type": "boolean", "description": "Search recursively"},
            },
            "required": ["pattern"],
        },
        handler=_grep_search,
    ))

    # bash tool
    registry.register(AgentTool(
        name="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to execute"},
            },
            "required": ["command"],
        },
        handler=_bash,
    ))

    # non_tool_call tool
    registry.register(AgentTool(
        name="non_tool_call",
        description="Respond without calling a tool",
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Content to respond with"},
            },
            "required": ["content"],
        },
        handler=_non_tool_call,
    ))

    # web_search tool
    registry.register(AgentTool(
        name="web_search",
        description="Search the web for information using configured search providers (SearXNG, Brave, Tavily)",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "provider": {"type": "string", "description": "Search provider name (optional, uses first available)"},
                "max_results": {"type": "integer", "description": "Maximum number of results to return (default 5)"},
            },
            "required": ["query"],
        },
        handler=_web_search,
        tags=["search", "web"],
    ))

    # web_fetch tool
    registry.register(AgentTool(
        name="web_fetch",
        description="Fetch and extract content from a URL",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "extract_text": {"type": "boolean", "description": "Extract readable text from HTML (default true)"},
                "max_length": {"type": "integer", "description": "Maximum length of returned content (default 10000)"},
            },
            "required": ["url"],
        },
        handler=_web_fetch,
        tags=["web", "fetch"],
    ))

    # use_skill tool
    registry.register(AgentTool(
        name="use_skill",
        description="Invoke a bundled skill — explain-code, review-code, generate-tests, "
                    "document-code, devflow-architect, devflow-step-planner, devflow-step-analyzer, "
                    "devflow-implementer, devflow-verifier, lifecycle-requirements, lifecycle-design, "
                    "lifecycle-code-review, lifecycle-unit-test, lifecycle-integration-test, lifecycle-acceptance",
        parameters={
            "type": "object",
            "properties": {
                "skill": {"type": "string", "description": "Skill name (see description for full list)"},
                "code": {"type": "string", "description": "Code or goal to apply the skill to"},
                "language": {"type": "string", "description": "Programming language (for generate-tests)"},
            },
            "required": ["skill"],
        },
        handler=_use_skill,
        tags=["skill"],
    ))

    return registry


# Tool handlers

def _list_dir(path: str, **kwargs) -> Dict[str, Any]:
    """List directory contents."""
    try:
        entries = os.listdir(path)
        result = []
        for entry in entries:
            full_path = os.path.join(path, entry)
            try:
                stat = os.stat(full_path)
                result.append({
                    "name": entry,
                    "type": "dir" if os.path.isdir(full_path) else "file",
                    "size": stat.st_size,
                })
            except OSError:
                result.append({"name": entry, "type": "unknown", "size": 0})
        return {"ok": True, "entries": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _read_file(path: str, limit: Optional[int] = None, offset: Optional[int] = None, **kwargs) -> Dict[str, Any]:
    """Read file contents."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            if offset:
                f.seek(offset)
            lines = []
            for i, line in enumerate(f):
                if limit and i >= limit:
                    break
                lines.append(line.rstrip("\n"))
        content = "\n".join(lines)
        return {"ok": True, "content": content, "path": path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _write_file(path: str, content: str, **kwargs) -> Dict[str, Any]:
    """Write content to a file."""
    try:
        # Ensure directory exists
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"ok": True, "path": path}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _edit_file(path: str, old_string: str, new_string: str, count: int = 1, **kwargs) -> Dict[str, Any]:
    """Edit a file by replacing text.

    Args:
        path: File path to edit
        old_string: String to replace
        new_string: Replacement string
        count: Number of occurrences to replace (-1 = replace all, default 1)
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        if old_string not in content:
            return {"ok": False, "error": f"old_string not found in file"}

        if count == -1:
            # Replace all occurrences
            new_content = content.replace(old_string, new_string)
            replaced = content.count(old_string)
        else:
            # Replace first N occurrences
            new_content = content.replace(old_string, new_string, count)
            replaced = min(count, content.count(old_string))

        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return {"ok": True, "path": path, "replaced": replaced}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _glob_search(pattern: str, cwd: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """Search for files matching a pattern."""
    try:
        search_dir = cwd or "."
        matches = glob.glob(os.path.join(search_dir, pattern), recursive=True)
        return {"ok": True, "matches": matches, "pattern": pattern}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _grep_search(pattern: str, path: Optional[str] = None, recursive: bool = False, **kwargs) -> Dict[str, Any]:
    """Search for text in files using regex."""
    try:
        search_path = path or "."
        results = []

        if os.path.isfile(search_path):
            files_to_search = [search_path]
        elif os.path.isdir(search_path):
            if recursive:
                files_to_search = []
                for root, dirs, files in os.walk(search_path):
                    # Skip hidden directories
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for file in files:
                        if not file.startswith("."):
                            files_to_search.append(os.path.join(root, file))
            else:
                files_to_search = [
                    os.path.join(search_path, f)
                    for f in os.listdir(search_path)
                    if os.path.isfile(os.path.join(search_path, f)) and not f.startswith(".")
                ]
        else:
            return {"ok": False, "error": f"Invalid path: {search_path}"}

        regex = re.compile(pattern)
        for filepath in files_to_search:
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, 1):
                        if regex.search(line):
                            results.append({
                                "file": filepath,
                                "line": lineno,
                                "text": line.rstrip(),
                            })
            except (IOError, OSError):
                continue

        return {"ok": True, "matches": results, "count": len(results)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _bash(command: str, **kwargs) -> Dict[str, Any]:
    """Execute a bash command with security validation."""
    # Security validation — MUST run before subprocess.run
    security_result = validate_bash_command(command)
    permissions = kwargs.get("permissions", {})

    if security_result == SecurityResult.DENY:
        return {"ok": False, "error": f"Command blocked by security policy: {command}"}
    elif security_result == SecurityResult.ASK:
        if not permissions.get("allow_shell", False):
            if permissions.get("_has_permission_callback"):
                return {"ok": False, "need_permission": True, "command": command, "security": "ASK"}
            return {"ok": False, "error": f"Shell access not permitted. Set allow_shell=True to run: {command}"}
    # ALLOW or PASSTHROUGH proceed normally

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Command timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _non_tool_call(content: str, **kwargs) -> Dict[str, Any]:
    """Respond without calling a tool."""
    return {"ok": True, "content": content}


def _web_search(query: str, provider: Optional[str] = None, max_results: int = 5, **kwargs) -> Dict[str, Any]:
    """Search the web using configured search providers.

    Uses SearchRuntime to dispatch to SearXNG, Brave, or Tavily.
    """
    try:
        from .search_runtime import SearchRuntime
        cwd = kwargs.get("_cwd", os.getcwd())
        search_runtime = SearchRuntime(cwd=cwd)

        result = search_runtime.search(query, provider_name=provider)

        # Truncate to max_results
        results = result.get("results", [])[:max_results]
        result["results"] = results

        return {
            "ok": True,
            "query": query,
            "provider": result.get("provider"),
            "results": results,
            "count": len(results),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _web_fetch(url: str, extract_text: bool = True, max_length: int = 10000, **kwargs) -> Dict[str, Any]:
    """Fetch content from a URL.

    Args:
        url: The URL to fetch
        extract_text: If True, attempt to extract readable text from HTML
        max_length: Maximum length of returned content
    """
    try:
        req = urllib.request.Request(
            url,
            method="GET",
            headers={
                "User-Agent": "ClawCodeAgent/1.0",
                "Accept": "text/html,text/plain,application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw_data = resp.read()

            # Decode
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].split(";")[0].strip()
            text = raw_data.decode(charset, errors="replace")

            # Simple HTML text extraction
            if extract_text and ("text/html" in content_type or text.strip().startswith("<")):
                text = _extract_text_from_html(text)

            # Truncate
            if len(text) > max_length:
                text = text[:max_length] + f"... [truncated {len(text) - max_length} chars]"

            return {
                "ok": True,
                "url": url,
                "content_type": content_type,
                "content": text,
                "status": resp.status,
            }
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}", "url": url}
    except Exception as e:
        return {"ok": False, "error": str(e), "url": url}


def _extract_text_from_html(html: str) -> str:
    """Simple HTML text extraction without external dependencies."""
    import re
    # Remove scripts and styles
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)
    # Decode common entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    return text.strip()


def _use_skill(skill: str, code: str = "", language: str = "python", **kwargs) -> Dict[str, Any]:
    """Invoke a bundled skill by name, returning its formatted prompt."""
    try:
        from .bundled_skills import get_skill

        skill_def = get_skill(skill)
        if not skill_def:
            from .bundled_skills import list_skills
            available = ", ".join(s.name for s in list_skills())
            return {"ok": False, "error": f"Unknown skill: {skill}. Available: {available}"}

        # Format the prompt template with all provided arguments
        format_args = {k: v for k, v in kwargs.items() if v}
        format_args.setdefault("code", code)
        format_args.setdefault("language", language)
        prompt = skill_def.prompt.format(**format_args)

        return {
            "ok": True,
            "skill": skill,
            "description": skill_def.description,
            "prompt": prompt,
        }
    except KeyError as e:
        return {"ok": False, "error": f"Missing parameter in skill template: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _mcp_tool(tool_name: str, server_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an MCP tool by forwarding to the appropriate MCP server client.

    Args:
        tool_name: The MCP tool name (without mcp__ prefix)
        server_name: The MCP server name
        arguments: Tool arguments
    """
    try:
        from .mcp_runtime import get_mcp_client
        client = get_mcp_client(server_name)
        if not client:
            return {"ok": False, "error": f"MCP server '{server_name}' is not running"}
        if not client.is_running:
            return {"ok": False, "error": f"MCP server '{server_name}' has stopped"}
        return client.call_tool(tool_name, arguments)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _virtual_tool_handler(tool_name: str, config: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a virtual tool defined by a plugin.

    Supports two modes:
    1. **Command mode**: if config has `command`, run it via subprocess
       - `command`: shell command string (supports {arg} placeholder substitution)
       - `cwd`: optional working directory for the command
    2. **Prompt mode** (fallback): return tool description and arguments as context
       for the model to use in its next reasoning step.

    Args:
        tool_name: The virtual tool name
        config: The full virtual tool config dict from plugin.json
        arguments: Tool call arguments from the model
    """
    command_template = config.get("command")
    if command_template:
        # Command mode: execute via subprocess with placeholder substitution
        try:
            cmd = command_template
            for key, value in arguments.items():
                cmd = cmd.replace(f"{{{key}}}", shlex.quote(str(value)))
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=config.get("cwd") or arguments.get("_cwd", "."),
            )
            return {
                "ok": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Virtual tool command timed out"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # Prompt mode: return context for the model
    return {
        "ok": True,
        "tool": tool_name,
        "description": config.get("description", ""),
        "arguments": arguments,
        "message": f"Virtual tool '{tool_name}' executed. Use this context in your next response.",
    }


@dataclass
class ToolExecutionResult:
    """Result of a tool execution."""
    ok: bool
    tool_name: str
    result: Optional[Any] = None
    error: Optional[str] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "tool_name": self.tool_name,
            "result": self.result,
            "error": self.error,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def execute_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """Execute a tool by name with arguments."""
    registry = default_tool_registry()
    tool = registry.get(tool_name)

    if not tool:
        return ToolExecutionResult(
            ok=False,
            tool_name=tool_name,
            error=f"Unknown tool: {tool_name}",
        )

    try:
        # Pass permissions and cwd from context to handler
        kwargs = dict(arguments)
        if context:
            kwargs["_cwd"] = context.cwd
            if context.permissions:
                kwargs["permissions"] = context.permissions
        result = tool.handler(**kwargs)

        # Normalize result to dict
        if isinstance(result, dict):
            if result.get("ok", True) is False:
                return ToolExecutionResult(
                    ok=False,
                    tool_name=tool_name,
                    error=result.get("error", "Unknown error"),
                    stderr=result.get("stderr"),
                )
            return ToolExecutionResult(
                ok=True,
                tool_name=tool_name,
                result=result,
                stdout=result.get("stdout"),
                stderr=result.get("stderr"),
            )
        else:
            return ToolExecutionResult(
                ok=True,
                tool_name=tool_name,
                result=result,
            )
    except Exception as e:
        return ToolExecutionResult(
            ok=False,
            tool_name=tool_name,
            error=str(e),
        )


def execute_tool_streaming(
    tool_name: str,
    arguments: Dict[str, Any],
    context: Optional[ToolExecutionContext] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Execute a tool with streaming output (for bash)."""
    if tool_name != "bash":
        # Non-streaming fallback
        result = execute_tool(tool_name, arguments, context)
        yield result.to_dict()
        return

    command = arguments.get("command", "")

    # Security validation before streaming execution
    security_result = validate_bash_command(command)
    permissions = context.permissions if context else {}

    if security_result == SecurityResult.DENY:
        yield {"ok": False, "tool_name": "bash", "error": f"Command blocked by security policy: {command}"}
        return
    elif security_result == SecurityResult.ASK:
        if not permissions.get("allow_shell", False):
            yield {"ok": False, "tool_name": "bash", "error": f"Shell access not permitted. Set allow_shell=True to run: {command}"}
            return

    try:
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        import select

        while True:
            readable, _, _ = select.select([process.stdout, process.stderr], [], [], 0.1)
            if process.stdout in readable:
                line = process.stdout.readline()
                if line:
                    yield {"ok": True, "stdout": line, "tool_name": "bash"}
            if process.stderr in readable:
                line = process.stderr.readline()
                if line:
                    yield {"ok": True, "stderr": line, "tool_name": "bash"}
            if process.poll() is not None:
                break

        # Read remaining output
        remaining_out = process.stdout.read()
        if remaining_out:
            yield {"ok": True, "stdout": remaining_out, "tool_name": "bash"}
        remaining_err = process.stderr.read()
        if remaining_err:
            yield {"ok": True, "stderr": remaining_err, "tool_name": "bash"}

        yield {
            "ok": process.returncode == 0,
            "tool_name": "bash",
            "returncode": process.returncode,
        }

    except Exception as e:
        yield {"ok": False, "tool_name": "bash", "error": str(e)}