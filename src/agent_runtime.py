"""Agent runtime - Main agent loop for CodeAgent."""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, AsyncIterator

from .agent_types import (
    AgentPermissions,
    AgentRunResult,
    BudgetConfig,
    ToolCall,
    UsageStats,
)
from .agent_session import AgentSession
from .agent_tools import ToolExecutionResult
from .agent_context import get_user_context, format_context_for_prompt
from .agent_prompting import render_system_prompt
from .agent_tools import execute_tool, execute_tool_streaming, default_tool_registry, ToolExecutionContext
from .openai_compat import OpenAICompatClient, AnthropicClient, OpenAICompatError
from .api_config import APIConfigRuntime, APIProvider
from .session_store import save_agent_session, load_agent_session
from .token_budget import TokenBudget
from .hook_policy import HookPolicyRuntime
from .plugin_runtime import PluginRuntime
from .compact import compact_messages, should_compact, AUTOCOMPACT_BUFFER_TOKENS
from .microcompact import truncate_tool_result

# Runtime modules for context injection
from .search_runtime import SearchRuntime
from .mcp_runtime import MCPRuntime
from .plan_runtime import PlanRuntime
from .task_runtime import TaskRuntime
from .remote_runtime import RemoteRuntime
from .account_runtime import AccountRuntime
from .ask_user_runtime import AskUserRuntime
from .config_runtime import ConfigRuntime
from .lsp_runtime import LSPRuntime
from .team_runtime import TeamRuntime
from .workflow_runtime import WorkflowRuntime
from .remote_trigger_runtime import RemoteTriggerRuntime
from .worktree_runtime import WorktreeRuntime
from .background_runtime import BackgroundRuntime
from .tokenizer_runtime import TokenizerRuntime
from .agent_manager import AgentManagerRuntime
from .devflow_runtime import DevFlowRuntime
from .lifecycle_runtime import LifecycleRuntime
from .bridge_runtime import BridgeRuntime

# Auto-retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1.5  # seconds


@dataclass
class RuntimeState:
    """State for runtime modules."""
    pass


@dataclass
class LocalCodingAgent:
    """Main coding agent with tool calling and session management."""

    cwd: str
    model_config: Optional[Any] = None
    budget: Optional[BudgetConfig] = None
    permissions: Optional[Dict[str, Any]] = None

    # Internal state
    session: Optional[AgentSession] = None
    client: Optional[OpenAICompatClient] = None
    runtime_context: Dict[str, Any] = field(default_factory=dict)
    runtimes: List[Any] = field(default_factory=list)

    # Hook and plugin runtimes
    hook_policy: Optional[HookPolicyRuntime] = None
    plugin_runtime: Optional[PluginRuntime] = None

    # Plugin-derived tool management
    _blocked_tools: List[str] = field(default_factory=list)
    _tool_aliases: Dict[str, str] = field(default_factory=dict)
    _virtual_tools: List[Dict[str, Any]] = field(default_factory=list)

    # Permission callback for interactive permission requests (e.g., REPL)
    permission_callback: Optional[Any] = None

    # Statistics
    usage: UsageStats = field(default_factory=UsageStats)
    turns: int = 0

    def __post_init__(self):
        """Initialize the agent after construction."""
        # Use API config to determine provider and client type
        api_config_runtime = APIConfigRuntime(cwd=self.cwd)
        api_config = api_config_runtime.get_config()

        # Honor explicit model_config override if provided
        active_model = api_config.model
        if self.model_config and self.model_config.name and self.model_config.name != api_config.model:
            active_model = self.model_config.name
            api_config.model = active_model

        # Create appropriate client based on provider
        if api_config.provider == APIProvider.ANTHROPIC:
            self.client = AnthropicClient(
                base_url=api_config.base_url,
                api_key=api_config.api_key,
                model=api_config.model,
            )
        else:
            # OpenAI compatible (vLLM, Ollama, LiteLLM, etc.)
            self.client = OpenAICompatClient(
                base_url=api_config.base_url,
                api_key=api_config.api_key,
                model=api_config.model,
            )

        # Initialize permissions with safe defaults
        if self.permissions is None:
            self.permissions = AgentPermissions(
                allow_write=False,
                allow_shell=False,
            ).to_dict()

        # Initialize hook/policy and plugin runtimes
        self.hook_policy = HookPolicyRuntime(cwd=self.cwd)
        self.plugin_runtime = PluginRuntime(cwd=self.cwd)

        # Collect plugin registrations: blocked_tools, tool_aliases, virtual_tools
        self._blocked_tools = []
        self._tool_aliases = {}
        self._virtual_tools = []

        if self.hook_policy.config:
            if self.hook_policy.config.deny_tool_prefixes:
                self._blocked_tools.extend(self.hook_policy.config.deny_tool_prefixes)

        for plugin in self.plugin_runtime.plugins:
            self._blocked_tools.extend(plugin.blocked_tools)
            for alias in plugin.tool_aliases:
                if isinstance(alias, dict):
                    self._tool_aliases[alias.get("name", "")] = alias.get("target", "")
            for vt in plugin.virtual_tools:
                self._virtual_tools.append(vt)
                # Register virtual tool in global ToolRegistry so it has an execution path
                tool_name = vt.get("name", "")
                if tool_name:
                    from .agent_tools import _virtual_tool_handler, AgentTool
                    # Capture vt by value for closure
                    def _make_vt_handler(name: str, cfg: Dict[str, Any]):
                        return lambda **kwargs: _virtual_tool_handler(name, cfg, kwargs)
                    default_tool_registry().register(AgentTool(
                        name=tool_name,
                        description=vt.get("description", f"Virtual tool: {tool_name}"),
                        parameters=vt.get("parameters", {"type": "object", "properties": {}}),
                        handler=_make_vt_handler(tool_name, vt),
                        tags=["plugin", "virtual", plugin.name],
                    ))

        # Mount all runtime modules for context injection and system prompt guidance.
        # Each runtime provides get_state(), render_summary(), get_prompt_guidance().
        self.runtimes = []
        self._runtime_instances: Dict[str, Any] = {}

        _runtime_classes = [
            ("search", SearchRuntime),
            ("mcp", MCPRuntime),
            ("plan", PlanRuntime),
            ("task", TaskRuntime),
            ("remote", RemoteRuntime),
            ("account", AccountRuntime),
            ("ask_user", AskUserRuntime),
            ("config", ConfigRuntime),
            ("lsp", LSPRuntime),
            ("team", TeamRuntime),
            ("workflow", WorkflowRuntime),
            ("remote_trigger", RemoteTriggerRuntime),
            ("worktree", WorktreeRuntime),
            ("background", BackgroundRuntime),
            ("tokenizer", TokenizerRuntime),
            ("agent_manager", AgentManagerRuntime),
            ("devflow", DevFlowRuntime),
            ("lifecycle", LifecycleRuntime),
            ("bridge", BridgeRuntime),
        ]

        for name, cls in _runtime_classes:
            try:
                instance = cls(cwd=self.cwd)
                self._runtime_instances[name] = instance
                self.runtimes.append(instance)
            except Exception:
                pass  # Runtime not configured in this environment

        # Start MCP servers and register their tools
        mcp_runtime = self._runtime_instances.get("mcp")
        if mcp_runtime and hasattr(mcp_runtime, "config") and mcp_runtime.config:
            try:
                from .mcp_runtime import start_mcp_servers
                start_mcp_servers(mcp_runtime, self.cwd)
            except Exception:
                pass  # MCP server startup failure is non-fatal

        # Ensure sessions directory exists
        os.makedirs(os.path.join(self.cwd, ".port_sessions", "agent"), exist_ok=True)

    @classmethod
    def from_session(
        cls,
        session_id: str,
        cwd: str,
        model_config: Optional[Any] = None,
        budget: Optional[BudgetConfig] = None,
    ) -> LocalCodingAgent:
        """Resume an agent from an existing session."""
        agent = cls(cwd=cwd, model_config=model_config, budget=budget)

        try:
            agent.session = load_agent_session(session_id, cwd)
        except FileNotFoundError:
            agent.session = AgentSession(session_id=session_id)

        return agent

    def run(
        self,
        prompt: str,
        max_turns: Optional[int] = None,
        stream: bool = False,
    ) -> AgentRunResult:
        """Run the agent with a new session."""
        # Reset turn counter for each new query
        self.turns = 0

        if self.session is None:
            self.session = AgentSession(session_id=str(uuid.uuid4())[:8])

        self.session.cwd = self.cwd
        if self.client:
            self.session.model = self.client.model

        self.session.add_user_message(prompt)

        max_turns = max_turns or 100

        result = self._run_loop(max_turns=max_turns, stream=stream)
        save_agent_session(self.session, self.cwd)
        return result

    def resume(self, prompt: str, stream: bool = False) -> AgentRunResult:
        """Resume an existing session."""
        if self.session is None:
            raise ValueError("No session to resume. Use run() for new sessions.")

        self.session.add_user_message(prompt)
        result = self._run_loop(max_turns=100, stream=stream)
        save_agent_session(self.session, self.cwd)
        return result

    def _run_loop(self, max_turns: int, stream: bool) -> AgentRunResult:
        """Main agent loop."""
        budget = TokenBudget.create(self.budget)

        # Hook point 1: Budget override from policy config
        if self.hook_policy and self.hook_policy.config and self.hook_policy.config.budget:
            policy_budget = self.hook_policy.config.budget
            if "max_total_tokens" in policy_budget:
                budget.max_total_tokens = min(budget.max_total_tokens, policy_budget["max_total_tokens"])
            if "max_output_tokens" in policy_budget:
                budget.max_output_tokens = min(budget.max_output_tokens, policy_budget["max_output_tokens"])
            if "max_model_calls" in policy_budget:
                budget.max_model_calls = min(budget.max_model_calls, policy_budget["max_model_calls"])

        context = get_user_context(self.cwd, runtimes=self.runtimes)
        system_prompt = render_system_prompt(runtimes=self.runtimes, context=context)

        # Hook point 2: Before-prompt — inject policy/plugin guidance into system prompt
        hook_guidance = ""
        if self.hook_policy:
            hook_guidance += self.hook_policy.get_prompt_guidance()
        if self.plugin_runtime:
            hook_guidance += self.plugin_runtime.get_prompt_guidance()
        if hook_guidance:
            system_prompt = system_prompt + "\n\n[Hook/Plugin Guidance]\n" + hook_guidance

        # Build messages - extract system prompt for Anthropic
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self.session.get_messages())

        # Check if using Anthropic client
        is_anthropic = isinstance(self.client, AnthropicClient)

        try:
            while self.turns < max_turns:
                # Check budget
                allowed, reason = budget.check()
                if not allowed:
                    self.session.stop_reason = "budget_exceeded"
                    return AgentRunResult(
                        stop_reason="budget_exceeded",
                        usage=self.usage,
                        error=reason,
                    )

                # Compact if needed
                if should_compact(messages, threshold=AUTOCOMPACT_BUFFER_TOKENS):
                    messages = self._compact_messages(messages)

                # Extract system message for Anthropic client
                if is_anthropic:
                    system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
                    system_content = system_msg.get("content", "") if system_msg else ""
                    # Remove system message from messages for Anthropic (it's passed separately)
                    non_system_messages = [m for m in messages if m.get("role") != "system"]

                    if stream:
                        response = self._stream_anthropic(non_system_messages, system_content)
                    else:
                        response = self._retry_call(
                            self.client.complete,
                            messages=non_system_messages,
                            system_prompt=system_content,
                            tools=self._get_toolspec(),
                        )
                else:
                    # OpenAI-compatible client
                    if stream:
                        response = self._stream_openai(messages)
                    else:
                        response = self._retry_call(
                            self.client.complete,
                            messages=messages,
                            tools=self._get_toolspec(),
                        )

                # Update usage
                if "usage" in response:
                    usage_data = response["usage"]
                    self.usage += UsageStats(
                        input_tokens=usage_data.get("input_tokens", 0),
                        output_tokens=usage_data.get("output_tokens", 0),
                        model_calls=usage_data.get("model_calls", 0),
                        tool_calls=usage_data.get("tool_calls", 0),
                    )
                    budget.update_usage(UsageStats(
                        input_tokens=usage_data.get("input_tokens", 0),
                        output_tokens=usage_data.get("output_tokens", 0),
                        model_calls=usage_data.get("model_calls", 0),
                        tool_calls=usage_data.get("tool_calls", 0),
                    ))

                # Handle response
                content = response.get("content", "")
                tool_calls = response.get("tool_calls")

                # Add assistant message
                if tool_calls:
                    self.session.add_assistant_message(content=content, tool_calls=[
                        ToolCall(id=tc["id"], name=tc["function"]["name"], arguments=json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"])
                        for tc in tool_calls
                    ])
                elif content:
                    self.session.add_assistant_message(content=content)

                messages.append(response)

                # Execute tool calls
                if tool_calls:
                    for tc in tool_calls:
                        tool_name = tc["function"]["name"]
                        args = tc["function"]["arguments"]
                        if isinstance(args, str):
                            args = json.loads(args)

                        # Show tool call for visibility
                        self._print_tool_call(tool_name, args)

                        # Hook point 3: Tool preflight — check blocked tools and apply aliases
                        if tool_name in self._blocked_tools:
                            result_str = f"Error: Tool '{tool_name}' is blocked by policy"
                            self.session.add_tool_message(
                                tool_call_id=tc["id"],
                                content=result_str[:4000],
                                tool_name=tool_name,
                            )
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": result_str[:4000],
                            })
                            continue

                        # Apply tool alias mapping
                        actual_tool_name = self._tool_aliases.get(tool_name, tool_name)

                        # Build permissions context with callback flag
                        tool_perms = dict(self.permissions) if self.permissions else {}
                        if self.permission_callback is not None:
                            tool_perms["_has_permission_callback"] = True

                        # Execute tool
                        result = execute_tool(
                            actual_tool_name,
                            args,
                            context=ToolExecutionContext(cwd=self.cwd, permissions=tool_perms),
                        )

                        # Check if tool needs interactive permission
                        if (not result.ok and isinstance(result.result, dict)
                                and result.result.get("need_permission")):
                            if self.permission_callback is not None:
                                # Ask user for permission
                                cmd = result.result.get("command", "")
                                allowed = self.permission_callback("bash", {"command": cmd})
                                if allowed:
                                    # Retry with allow_shell=True
                                    tool_perms["allow_shell"] = True
                                    if self.permissions:
                                        self.permissions["allow_shell"] = True
                                    result = execute_tool(
                                        actual_tool_name,
                                        args,
                                        context=ToolExecutionContext(cwd=self.cwd, permissions=tool_perms),
                                    )
                                else:
                                    # User denied permission — skip this tool call
                                    deny_msg = f"Error: User denied shell permission for: {cmd}"
                                    self.session.add_tool_message(
                                        tool_call_id=tc["id"],
                                        content=deny_msg[:4000],
                                        tool_name=tool_name,
                                    )
                                    messages.append({
                                        "role": "tool",
                                        "tool_call_id": tc["id"],
                                        "content": deny_msg[:4000],
                                    })
                                    continue
                            # else: no callback, fall through to normal error handling

                        self.usage.tool_calls += 1
                        budget.update_usage(UsageStats(tool_calls=1))

                        # Format result
                        if result.ok:
                            tool_result = result.result
                            if isinstance(tool_result, dict):
                                result_str = json.dumps(tool_result)
                            else:
                                result_str = str(tool_result)
                        else:
                            result_str = f"Error: {result.error}"

                        # Truncate tool result if too long
                        truncated = truncate_tool_result(result_str)

                        # Add tool message
                        self.session.add_tool_message(
                            tool_call_id=tc["id"],
                            content=truncated,
                            tool_name=tool_name,
                        )

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": truncated,
                        })

                    self.turns += 1
                else:
                    # No tool call - we're done
                    self.session.stop_reason = "completed"
                    # In streaming mode, content was already printed to stdout
                    return AgentRunResult(
                        stop_reason="completed",
                        final_message=None if stream else content,
                        usage=self.usage,
                    )

        except Exception as e:
            return AgentRunResult(
                stop_reason="error",
                error=str(e),
                usage=self.usage,
            )

        # Max turns reached
        self.session.stop_reason = "stopped"
        return AgentRunResult(
            stop_reason="stopped",
            usage=self.usage,
            final_message="Max turns reached",
        )

    def _retry_call(self, call_fn, *args, **kwargs) -> Any:
        """Call a function with exponential backoff retry on HTTP errors.

        Retries on: HTTP 429 (rate limit), 503 (service unavailable), 5xx errors.
        """
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return call_fn(*args, **kwargs)
            except OpenAICompatError as e:
                last_error = e
                if e.status_code and (e.status_code == 429 or e.status_code == 503 or e.status_code >= 500):
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_BACKOFF_BASE ** (attempt + 1)
                        time.sleep(delay)
                        continue
                raise
            except Exception:
                raise
        raise last_error  # type: ignore[misc]

    def _stream_openai(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Stream completion from OpenAI-compatible API and accumulate response."""
        content_parts = []
        tool_calls_map: Dict[int, Dict[str, Any]] = {}
        usage = {"input_tokens": 0, "output_tokens": 0, "model_calls": 1, "tool_calls": 0}

        for chunk in self.client.stream(messages=messages, tools=self._get_toolspec()):
            if "content" in chunk and chunk["content"]:
                text = chunk["content"]
                content_parts.append(text)
                print(text, end="", flush=True)

            if "tool_calls" in chunk:
                for tc in chunk["tool_calls"]:
                    idx = tc.get("index", 0)
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {
                            "id": tc.get("id", ""),
                            "function": {"name": "", "arguments": ""},
                        }
                    if "id" in tc:
                        tool_calls_map[idx]["id"] = tc["id"]
                    if "function" in tc:
                        func = tc["function"]
                        if "name" in func and func["name"]:
                            tool_calls_map[idx]["function"]["name"] = func["name"]
                        if "arguments" in func:
                            tool_calls_map[idx]["function"]["arguments"] += func["arguments"]

        if content_parts:
            print()  # newline after streaming

        response: Dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts),
        }

        if tool_calls_map:
            tool_calls = [tool_calls_map[i] for i in sorted(tool_calls_map.keys())]
            response["tool_calls"] = tool_calls
            usage["tool_calls"] = len(tool_calls)

        response["usage"] = usage
        return response

    def _stream_anthropic(self, messages: List[Dict[str, Any]], system_prompt: str) -> Dict[str, Any]:
        """Stream completion from Anthropic API and accumulate response."""
        content_parts = []
        tool_call_buffer: Dict[str, Dict[str, Any]] = {}
        usage = {"input_tokens": 0, "output_tokens": 0, "model_calls": 1, "tool_calls": 0}

        for chunk in self.client.stream(
            messages=messages,
            system_prompt=system_prompt,
            tools=self._get_toolspec(),
        ):
            if "content" in chunk and chunk["content"]:
                text = chunk["content"]
                content_parts.append(text)
                print(text, end="", flush=True)

            if "tool_call" in chunk:
                tc = chunk["tool_call"]
                tc_id = tc.get("id", "")
                if tc_id not in tool_call_buffer:
                    tool_call_buffer[tc_id] = {
                        "id": tc_id,
                        "function": {"name": tc.get("name", ""), "arguments": ""},
                    }
            if "partial_args" in chunk:
                # Find the most recent tool call being built
                if tool_call_buffer:
                    last_tc = list(tool_call_buffer.values())[-1]
                    last_tc["function"]["arguments"] += chunk["partial_args"]

        if content_parts:
            print()  # newline after streaming

        tool_calls = list(tool_call_buffer.values())
        response: Dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts),
        }

        if tool_calls:
            response["tool_calls"] = tool_calls
            usage["tool_calls"] = len(tool_calls)

        response["usage"] = usage
        return response

    def _get_toolspec(self) -> List[Dict[str, Any]]:
        """Get tool specifications for the model, respecting blocked tools and adding virtuals."""
        registry = default_tool_registry()
        tools = []

        is_anthropic = isinstance(self.client, AnthropicClient)

        # Collect blocked tool names from policy and plugins
        blocked_names = set(self._blocked_tools)

        for tool in registry.list_tools():
            # Skip blocked tools — the model should not see them
            if tool.name in blocked_names:
                continue

            if is_anthropic:
                tools.append({
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                })
            else:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    }
                })

        # Add virtual tools from plugins
        for vt in self._virtual_tools:
            if is_anthropic:
                tools.append({
                    "name": vt.get("name", ""),
                    "description": vt.get("description", ""),
                    "input_schema": vt.get("parameters", {"type": "object", "properties": {}}),
                })
            else:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": vt.get("name", ""),
                        "description": vt.get("description", ""),
                        "parameters": vt.get("parameters", {"type": "object", "properties": {}}),
                    }
                })

        return tools

    def _compact_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compact messages using HYBRID strategy: summarize middle, keep head and tail."""
        return compact_messages(messages)

    def _print_tool_call(self, tool_name: str, args: Dict[str, Any]) -> None:
        """Print a visible tool call indicator."""
        import sys

        # Truncate args for display
        args_str = json.dumps(args, ensure_ascii=False)
        if len(args_str) > 120:
            args_str = args_str[:117] + "..."

        # Icon per tool type
        icons = {
            "bash": "⚡",
            "read_file": "📖",
            "write_file": "✏️",
            "edit_file": "✂️",
            "list_dir": "📁",
            "glob_search": "🔍",
            "grep_search": "🔎",
            "non_tool_call": "💬",
            "web_search": "🌐",
            "web_fetch": "🌍",
        }
        icon = icons.get(tool_name, "🔧")

        if sys.stdout.isatty():
            print(f"\n  {icon} \033[90m{tool_name}\033[0m {args_str}")
        else:
            print(f"\n  [{tool_name}] {args_str}")

    def get_state(self) -> Dict[str, Any]:
        """Get agent state for introspection."""
        return {
            "session_id": self.session.session_id if self.session else None,
            "turns": self.turns,
            "usage": self.usage.to_dict() if self.usage else {},
            "runtimes": [type(r).__name__ for r in self.runtimes],
        }


import json  # For tool call parsing