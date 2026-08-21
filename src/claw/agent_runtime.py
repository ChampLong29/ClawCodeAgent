"""Agent runtime - Main agent loop for CodeAgent."""

from __future__ import annotations

import fnmatch
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, AsyncIterator, Sequence, Set

from .agent_types import (
    AgentPermissions,
    AgentRunResult,
    BudgetConfig,
    ModelConfig,
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
from .skill_runtime import SkillRuntime

# Auto-retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1.5  # seconds
_DIRECT_MUTATION_TOOLS = {"write_file", "edit_file"}


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
    api_config_cwd: Optional[str] = None
    completion_reminder_turns: int = 0
    completion_critical_turns: int = 0
    force_final_response_at_critical: bool = False
    implementation_deadline_turns: int = 0
    implementation_escalation_turns: int = 0
    force_direct_mutation_after_escalation: bool = False
    implementation_target_read_allowance: int = 0
    implementation_constraint_repair_attempts: int = 0
    post_edit_contract_guidance: bool = False
    implementation_path_patterns: Sequence[str] = ()

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

    # Optional side-channel observer for append-only runtime tracing.
    runtime_observer: Optional[Any] = None

    # Statistics
    usage: UsageStats = field(default_factory=UsageStats)
    turns: int = 0

    def __post_init__(self):
        """Initialize the agent after construction."""
        if self.completion_reminder_turns < 0:
            raise ValueError("completion_reminder_turns must be non-negative")
        if self.completion_critical_turns < 0:
            raise ValueError("completion_critical_turns must be non-negative")
        if self.implementation_deadline_turns < 0:
            raise ValueError("implementation_deadline_turns must be non-negative")
        if self.implementation_escalation_turns < 0:
            raise ValueError("implementation_escalation_turns must be non-negative")
        if (
            self.implementation_escalation_turns > 0
            and self.implementation_deadline_turns <= 0
        ):
            raise ValueError(
                "implementation_escalation_turns requires a positive "
                "implementation_deadline_turns"
            )
        if (
            self.force_direct_mutation_after_escalation
            and self.implementation_escalation_turns <= 0
        ):
            raise ValueError(
                "force_direct_mutation_after_escalation requires a positive "
                "implementation_escalation_turns"
            )
        if self.implementation_target_read_allowance not in {0, 1}:
            raise ValueError(
                "implementation_target_read_allowance must be 0 or 1"
            )
        if self.implementation_constraint_repair_attempts not in {0, 1}:
            raise ValueError(
                "implementation_constraint_repair_attempts must be 0 or 1"
            )
        if (
            self.implementation_target_read_allowance > 0
            and not self.force_direct_mutation_after_escalation
        ):
            raise ValueError(
                "implementation_target_read_allowance requires "
                "force_direct_mutation_after_escalation"
            )
        if (
            self.implementation_constraint_repair_attempts > 0
            and self.implementation_target_read_allowance != 1
        ):
            raise ValueError(
                "implementation_constraint_repair_attempts requires "
                "implementation_target_read_allowance=1"
            )
        if self.force_final_response_at_critical and self.completion_critical_turns <= 0:
            raise ValueError(
                "force_final_response_at_critical requires a positive "
                "completion_critical_turns"
            )
        self.implementation_path_patterns = tuple(
            str(pattern).replace("\\", "/")
            for pattern in self.implementation_path_patterns
            if str(pattern).strip()
        )
        if (
            self.implementation_target_read_allowance > 0
            and not self.implementation_path_patterns
        ):
            raise ValueError(
                "implementation_target_read_allowance requires explicit "
                "implementation_path_patterns"
            )

        if (
            self.completion_reminder_turns > 0
            and self.completion_critical_turns > self.completion_reminder_turns
        ):
            raise ValueError(
                "completion_critical_turns must not exceed "
                "completion_reminder_turns"
            )
        # Use API config to determine provider and client type
        api_config_runtime = APIConfigRuntime(cwd=self.api_config_cwd or self.cwd)
        api_config = api_config_runtime.get_config()

        # Materialize the effective decoding configuration once so every
        # provider call and trace uses the same audited values.
        if self.model_config is None:
            self.model_config = ModelConfig(
                name=api_config.model,
                temperature=api_config.temperature,
                max_tokens=api_config.max_tokens,
            )
        elif self.model_config.name and self.model_config.name != api_config.model:
            api_config.model = self.model_config.name

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
            ("skill", SkillRuntime),
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

    def _is_implementation_path(self, path: Any) -> bool:
        """Return whether a direct edit targets a configured implementation path."""
        if not self.implementation_path_patterns:
            return True
        normalized = str(path or "").replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return any(
            fnmatch.fnmatchcase(normalized, pattern)
            for pattern in self.implementation_path_patterns
        )

    @staticmethod
    def _tool_call_path(tool_call: Dict[str, Any]) -> Any:
        """Extract a path from a model tool call without dispatching it."""
        function = tool_call.get("function") or {}
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (TypeError, ValueError):
                return None
        if not isinstance(arguments, dict):
            return None
        return arguments.get("path")

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
        if self.runtime_observer is not None:
            self.runtime_observer.on_run_start(
                self, prompt=prompt, phase_id=self._current_phase_id()
            )

        result = self._run_loop(max_turns=max_turns, stream=stream)
        if self.runtime_observer is not None:
            self.runtime_observer.on_run_finish(
                result, phase_id=self._current_phase_id()
            )
        save_agent_session(self.session, self.cwd)
        return result

    def resume(self, prompt: str, stream: bool = False) -> AgentRunResult:
        """Resume an existing session."""
        if self.session is None:
            raise ValueError("No session to resume. Use run() for new sessions.")

        self.session.add_user_message(prompt)
        if self.runtime_observer is not None:
            self.runtime_observer.on_run_start(
                self, prompt=prompt, phase_id=self._current_phase_id()
            )
        result = self._run_loop(max_turns=100, stream=stream)
        if self.runtime_observer is not None:
            self.runtime_observer.on_run_finish(
                result, phase_id=self._current_phase_id()
            )
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

        # Build messages — use compacted context view if lifecycle is active
        session_messages = self.session.get_messages()

        lifecycle_rt = self._runtime_instances.get("lifecycle")
        if lifecycle_rt and lifecycle_rt.has_active_session():
            phase = lifecycle_rt.session.get_current_phase()
            current_name = phase.name if phase else ""
            ctx = lifecycle_rt.context_manager.build_context(
                self.session,
                current_phase=current_name,
                completed_phase_outputs=lifecycle_rt._completed_phase_outputs,
            )
            # Only replace if we actually got a compacted view
            if len(ctx) < len(session_messages):
                session_messages = ctx

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(session_messages)

        # Check if using Anthropic client
        is_anthropic = isinstance(self.client, AnthropicClient)

        try:
            completion_reminder_sent = False
            completion_critical_sent = False
            implementation_deadline_sent = False
            implementation_escalation_sent = False
            post_edit_contract_guidance_sent = False
            post_edit_contract_guidance_pending = False
            first_direct_mutation_turn: Optional[int] = None
            direct_mutation_succeeded = False
            force_direct_mutation_request = False
            target_reads_remaining = self.implementation_target_read_allowance
            target_read_consumed = False
            constraint_repairs_used = 0
            force_final_response_request = False
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

                remaining_tool_turns = max_turns - self.turns
                if (
                    self.post_edit_contract_guidance
                    and post_edit_contract_guidance_pending
                    and not post_edit_contract_guidance_sent
                ):
                    contract_guidance = (
                        "[Runtime post-edit contract notice] A direct file edit "
                        "succeeded. Before finalizing or broadening the change, run "
                        "the smallest focused verification that covers the reported "
                        "behavior and one relevant existing regression. Check not only "
                        "values and exceptions, but also compatibility contracts such "
                        "as scalar versus collection inputs, container and return "
                        "types, shape, ordering, null handling, and metadata when they "
                        "apply. When adding a guard, default, or fallback around a "
                        "nested object, first identify which object owns the "
                        "configuration and whether the codebase already exposes a "
                        "root or parent chain abstraction. Then set an explicit "
                        "non-default configuration at that owner and verify that it "
                        "propagates to the nested leaf; setting the value directly "
                        "on the leaf or merely avoiding the exception is not "
                        "sufficient. If any "
                        "check fails, read the failure and repair the implementation "
                        "before returning a final response."
                    )
                    messages.append({"role": "user", "content": contract_guidance})
                    self._trace(
                        "runtime_guidance",
                        payload={
                            "guidance_type": "post_edit_contract",
                            "first_direct_mutation_turn": first_direct_mutation_turn,
                            "remaining_tool_turns": remaining_tool_turns,
                        },
                    )
                    post_edit_contract_guidance_sent = True
                    post_edit_contract_guidance_pending = False
                if (
                    self.implementation_deadline_turns > 0
                    and not implementation_deadline_sent
                    and not direct_mutation_succeeded
                    and self.turns >= self.implementation_deadline_turns
                    and (
                        self.completion_reminder_turns <= 0
                        or remaining_tool_turns > self.completion_reminder_turns
                    )
                ):
                    deadline = (
                        "[Runtime implementation notice] You have used "
                        f"{self.turns} tool-bearing turns without a successful "
                        "direct file edit. If the likely implementation path is known, "
                        "stop expanding the search and make the smallest defensible "
                        "change now, followed by a targeted test. If it is not known, "
                        "use the next turn only to state and test one concrete hypothesis."
                    )
                    messages.append({"role": "user", "content": deadline})
                    self._trace(
                        "runtime_guidance",
                        payload={
                            "guidance_type": "implementation_deadline",
                            "tool_turns_without_direct_mutation": self.turns,
                            "configured_threshold": self.implementation_deadline_turns,
                            "remaining_tool_turns": remaining_tool_turns,
                        },
                    )
                    implementation_deadline_sent = True
                if (
                    implementation_deadline_sent
                    and self.implementation_escalation_turns > 0
                    and not implementation_escalation_sent
                    and not direct_mutation_succeeded
                    and self.turns
                    >= (
                        self.implementation_deadline_turns
                        + self.implementation_escalation_turns
                    )
                    and (
                        self.completion_reminder_turns <= 0
                        or remaining_tool_turns > self.completion_reminder_turns
                    )
                ):
                    escalation = (
                        "[Runtime implementation escalation] The earlier "
                        "implementation deadline was not followed and no successful "
                        "direct file edit has been observed. "
                    )
                    if (
                        self.force_direct_mutation_after_escalation
                        and target_reads_remaining > 0
                    ):
                        escalation += (
                            "In the next tool-bearing response, either make the "
                            "smallest defensible direct edit based on current "
                            "evidence, or read exactly one file matching the allowed "
                            "implementation paths. If you read it, the following "
                            "tool-bearing response must directly edit an allowed "
                            "implementation path. Do not call search, outline, shell, "
                            "or environment-inspection tools."
                        )
                    else:
                        escalation += (
                            "Do not call read, search, outline, or "
                            "environment-inspection tools again. In the next "
                            "tool-bearing response, either make the smallest "
                            "defensible direct edit based on current evidence, or "
                            "run exactly one focused reproducer that distinguishes "
                            "two concrete implementation choices and then edit "
                            "immediately."
                        )
                    if self.implementation_path_patterns:
                        escalation += (
                            " Direct edits must target one of these implementation "
                            "path patterns: "
                            + ", ".join(self.implementation_path_patterns)
                            + "."
                        )
                    messages.append({"role": "user", "content": escalation})
                    self._trace(
                        "runtime_guidance",
                        payload={
                            "guidance_type": "implementation_escalation",
                            "tool_turns_without_direct_mutation": self.turns,
                            "configured_delay": self.implementation_escalation_turns,
                            "deadline_threshold": self.implementation_deadline_turns,
                            "remaining_tool_turns": remaining_tool_turns,
                            "action_constraint": (
                                (
                                    "target_read_or_direct_mutation"
                                    if target_reads_remaining > 0
                                    else "required_direct_mutation"
                                )
                                if self.force_direct_mutation_after_escalation
                                else "guidance_only"
                            ),
                            "target_reads_remaining": target_reads_remaining,
                        },
                    )
                    implementation_escalation_sent = True
                    force_direct_mutation_request = (
                        self.force_direct_mutation_after_escalation
                    )
                if (
                    self.completion_reminder_turns > 0
                    and not completion_reminder_sent
                    and remaining_tool_turns <= self.completion_reminder_turns
                ):
                    reminder = (
                        "[Runtime budget notice] You have at most "
                        f"{remaining_tool_turns} additional tool-bearing turns. "
                        "Stop broad investigation now. Commit to the smallest plausible "
                        "implementation, use only targeted checks, and reserve time for "
                        "a final response. Do not start environment or version archaeology "
                        "unless a concrete command has failed for that reason."
                    )
                    messages.append({"role": "user", "content": reminder})
                    self._trace(
                        "runtime_guidance",
                        payload={
                            "guidance_type": "completion_reminder",
                            "remaining_tool_turns": remaining_tool_turns,
                            "configured_threshold": self.completion_reminder_turns,
                        },
                    )
                    completion_reminder_sent = True

                if (
                    self.completion_critical_turns > 0
                    and not completion_critical_sent
                    and remaining_tool_turns <= self.completion_critical_turns
                ):
                    critical = (
                        "[Runtime finalization notice] You have at most "
                        f"{remaining_tool_turns} additional tool-bearing turns. "
                        "Do not begin new investigation. Make at most one essential "
                        "change or run one targeted verification, then return the final "
                        "response. If the task is incomplete, state that clearly instead "
                        "of spending the remaining budget on exploration."
                    )
                    messages.append({"role": "user", "content": critical})
                    self._trace(
                        "runtime_guidance",
                        payload={
                            "guidance_type": "completion_critical",
                            "remaining_tool_turns": remaining_tool_turns,
                            "configured_threshold": self.completion_critical_turns,
                            "action_constraint": (
                                "final_response_only"
                                if self.force_final_response_at_critical
                                else "guidance_only"
                            ),
                        },
                    )
                    completion_critical_sent = True
                    force_final_response_request = (
                        self.force_final_response_at_critical
                    )

                inference_config = self._model_inference_kwargs()
                if (
                    implementation_escalation_sent
                    and self.force_direct_mutation_after_escalation
                    and not direct_mutation_succeeded
                    and not force_final_response_request
                ):
                    force_direct_mutation_request = True
                allowed_request_tools: Optional[Set[str]] = None
                allow_target_read_request = (
                    force_direct_mutation_request
                    and target_reads_remaining > 0
                )
                if force_final_response_request:
                    allowed_request_tools = set()
                    inference_config["tool_choice"] = "none"
                elif force_direct_mutation_request:
                    allowed_request_tools = set(_DIRECT_MUTATION_TOOLS)
                    if allow_target_read_request:
                        allowed_request_tools.add("read_file")
                    inference_config["tool_choice"] = "required"
                request_tools = self._get_toolspec(
                    allowed_names=allowed_request_tools
                )
                model_request_event_id = self._trace(
                    "model_request",
                    payload={
                        "model": getattr(self.client, "model", ""),
                        "messages": messages,
                        "tools": request_tools,
                        "stream": stream,
                        "turn": self.turns,
                        "inference_config": inference_config,
                        "action_constraint": (
                            (
                                "target_read_or_direct_mutation"
                                if allow_target_read_request
                                else "required_direct_mutation"
                            )
                            if force_direct_mutation_request
                            else (
                                "final_response_only"
                                if force_final_response_request
                                else None
                            )
                        ),
                    },
                )
                model_started = time.monotonic()

                # Extract system message for Anthropic client
                if is_anthropic:
                    system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
                    system_content = system_msg.get("content", "") if system_msg else ""
                    # Remove system message from messages for Anthropic (it's passed separately)
                    non_system_messages = [m for m in messages if m.get("role") != "system"]

                    if stream:
                        response = self._stream_anthropic(
                            non_system_messages,
                            system_content,
                            tools=request_tools,
                            inference_config=inference_config,
                        )
                    else:
                        response = self._retry_call(
                            self.client.complete,
                            messages=non_system_messages,
                            system_prompt=system_content,
                            tools=request_tools,
                            **inference_config,
                        )
                else:
                    # OpenAI-compatible client
                    if stream:
                        response = self._stream_openai(
                            messages,
                            tools=request_tools,
                            inference_config=inference_config,
                        )
                    else:
                        response = self._retry_call(
                            self.client.complete,
                            messages=messages,
                            tools=request_tools,
                            **inference_config,
                        )

                self._trace(
                    "model_response",
                    payload={
                        "content": response.get("content", ""),
                        "tool_calls": response.get("tool_calls") or [],
                        "usage": response.get("usage", {}),
                        "finish_reason": response.get("finish_reason"),
                        "duration_seconds": time.monotonic() - model_started,
                    },
                    parent_event_id=model_request_event_id,
                )

                # Update usage. Count model-requested tool calls from the
                # response itself so providers cannot omit or double-report
                # them, and so rejected/malformed calls still consume budget.
                if "usage" in response:
                    usage_data = response["usage"]
                    response_tool_calls = len(response.get("tool_calls") or [])
                    self.usage += UsageStats(
                        input_tokens=usage_data.get("input_tokens", 0),
                        output_tokens=usage_data.get("output_tokens", 0),
                        model_calls=usage_data.get("model_calls", 0),
                        tool_calls=response_tool_calls,
                    )
                    budget.update_usage(UsageStats(
                        input_tokens=usage_data.get("input_tokens", 0),
                        output_tokens=usage_data.get("output_tokens", 0),
                        model_calls=usage_data.get("model_calls", 0),
                        tool_calls=response_tool_calls,
                    ))

                # Handle response
                content = response.get("content", "")
                tool_calls = response.get("tool_calls")

                if force_direct_mutation_request:
                    force_direct_mutation_request = False
                    requested_names = {
                        str((item.get("function") or {}).get("name", ""))
                        for item in (tool_calls or [])
                    }
                    direct_mutation_request = bool(tool_calls) and (
                        requested_names.issubset(_DIRECT_MUTATION_TOOLS)
                    ) and all(
                        self._tool_call_path(item) is not None
                        and self._is_implementation_path(
                            self._tool_call_path(item)
                        )
                        for item in (tool_calls or [])
                    )
                    target_read_request = (
                        allow_target_read_request
                        and len(tool_calls or []) == 1
                        and requested_names == {"read_file"}
                        and self._tool_call_path((tool_calls or [])[0]) is not None
                        and self._is_implementation_path(
                            self._tool_call_path((tool_calls or [])[0])
                        )
                    )
                    if target_read_request:
                        target_reads_remaining -= 1
                        target_read_consumed = True
                        self._trace(
                            "runtime_guidance",
                            payload={
                                "guidance_type": "implementation_target_read_consumed",
                                "path": self._tool_call_path((tool_calls or [])[0]),
                                "target_reads_remaining": target_reads_remaining,
                                "next_action_constraint": "required_direct_mutation",
                            },
                            parent_event_id=model_request_event_id,
                        )
                    if not direct_mutation_request and not target_read_request:
                        if (
                            target_read_consumed
                            and constraint_repairs_used
                            < self.implementation_constraint_repair_attempts
                        ):
                            constraint_repairs_used += 1
                            repair_guidance = (
                                "[Runtime action-constraint correction] Your previous "
                                "tool request was not executed. The single allowed "
                                "target-file read has already been consumed, so no new "
                                "file or test information is available. In the next "
                                "tool-bearing response, directly edit one allowed "
                                "implementation path using write_file or edit_file. "
                                "Do not request another read, search, outline, shell, "
                                "or environment-inspection tool."
                            )
                            if self.implementation_path_patterns:
                                repair_guidance += (
                                    " Allowed implementation path patterns: "
                                    + ", ".join(self.implementation_path_patterns)
                                    + "."
                                )
                            messages.append(
                                {"role": "user", "content": repair_guidance}
                            )
                            self._trace(
                                "runtime_guidance",
                                payload={
                                    "guidance_type": (
                                        "implementation_action_constraint_repair"
                                    ),
                                    "repair_attempt": constraint_repairs_used,
                                    "maximum_repair_attempts": (
                                        self.implementation_constraint_repair_attempts
                                    ),
                                    "violating_requested_tool_names": sorted(
                                        requested_names
                                    ),
                                    "rejected_before_dispatch": True,
                                    "new_task_information_provided": False,
                                    "target_reads_remaining": target_reads_remaining,
                                    "next_action_constraint": (
                                        "required_direct_mutation"
                                    ),
                                },
                                parent_event_id=model_request_event_id,
                            )
                            self.turns += 1
                            force_direct_mutation_request = True
                            continue
                        detail = (
                            "provider did not satisfy the bounded implementation "
                            "action constraint"
                        )
                        self._trace(
                            "runtime_stop",
                            payload={
                                "reason": "action_constraint_unsatisfied",
                                "detail": detail,
                                "requested_tool_names": sorted(requested_names),
                                "constraint_repairs_used": constraint_repairs_used,
                                "constraint_repairs_exhausted": (
                                    constraint_repairs_used
                                    >= self.implementation_constraint_repair_attempts
                                ),
                            },
                            parent_event_id=model_request_event_id,
                        )
                        self.session.stop_reason = "stopped"
                        return AgentRunResult(
                            stop_reason="stopped",
                            final_message=content or None,
                            error=detail,
                            usage=self.usage,
                        )
                if force_final_response_request:
                    force_final_response_request = False
                    if tool_calls:
                        requested_names = {
                            str((item.get("function") or {}).get("name", ""))
                            for item in tool_calls
                        }
                        detail = (
                            "provider did not satisfy the final-response-only "
                            "action constraint"
                        )
                        self._trace(
                            "runtime_stop",
                            payload={
                                "reason": "action_constraint_unsatisfied",
                                "detail": detail,
                                "requested_tool_names": sorted(requested_names),
                            },
                            parent_event_id=model_request_event_id,
                        )
                        self.session.stop_reason = "stopped"
                        return AgentRunResult(
                            stop_reason="stopped",
                            final_message=content or None,
                            error=detail,
                            usage=self.usage,
                        )

                # Extract thinking metadata for session persistence
                _thinking = response.get("_thinking")
                _thinking_signature = response.get("_thinking_signature")

                # Add assistant message
                if tool_calls:
                    parsed_tool_calls = []
                    for tc in tool_calls:
                        raw_args = tc["function"]["arguments"]
                        if isinstance(raw_args, str):
                            try:
                                parsed_args = json.loads(raw_args)
                            except (json.JSONDecodeError, TypeError):
                                # Try to salvage malformed JSON (strip thinking tags, etc.)
                                import re
                                cleaned = re.sub(r'<think>.*?</think>', '', raw_args, flags=re.DOTALL).strip()
                                try:
                                    parsed_args = json.loads(cleaned)
                                except (json.JSONDecodeError, TypeError):
                                    parsed_args = {"_raw_arguments": raw_args}
                        else:
                            parsed_args = raw_args
                        parsed_tool_calls.append(
                            ToolCall(id=tc["id"], name=tc["function"]["name"], arguments=parsed_args)
                        )
                    self.session.add_assistant_message(
                        content=content, tool_calls=parsed_tool_calls,
                        thinking=_thinking, thinking_signature=_thinking_signature,
                    )
                elif content:
                    self.session.add_assistant_message(
                        content=content,
                        thinking=_thinking, thinking_signature=_thinking_signature,
                    )

                messages.append(response)

                finish_reason = str(response.get("finish_reason") or "").lower()
                if not tool_calls and finish_reason in {"length", "max_tokens"}:
                    detail = (
                        "Model response reached its per-request token limit before "
                        "producing a complete response."
                    )
                    self._trace(
                        "runtime_stop",
                        payload={
                            "reason": "model_output_truncated",
                            "finish_reason": finish_reason,
                            "content_present": bool(content),
                        },
                        parent_event_id=model_request_event_id,
                    )
                    self.session.stop_reason = "stopped"
                    return AgentRunResult(
                        stop_reason="stopped",
                        final_message=None if stream else (content or None),
                        error=detail,
                        usage=self.usage,
                    )

                # Execute tool calls
                if tool_calls:
                    for tc in tool_calls:
                        tool_name = tc["function"]["name"]
                        args = tc["function"]["arguments"]
                        tool_call_event_id = self._trace(
                            "tool_call",
                            payload={
                                "call_id": tc.get("id", ""),
                                "tool_name": tool_name,
                                "arguments": args,
                            },
                            parent_event_id=model_request_event_id,
                        )
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except (json.JSONDecodeError, TypeError):
                                import re
                                cleaned = re.sub(r'<think>.*?</think>', '', args, flags=re.DOTALL).strip()
                                try:
                                    args = json.loads(cleaned)
                                except (json.JSONDecodeError, TypeError):
                                    # Cannot parse — report error to model
                                    error_msg = f"Error: Failed to parse tool arguments as JSON: {args[:200]}"
                                    self.session.add_tool_message(
                                        tool_call_id=tc.get("id", ""),
                                        content=error_msg,
                                        tool_name=tool_name,
                                    )
                                    messages.append({
                                        "role": "tool",
                                        "tool_call_id": tc.get("id", ""),
                                        "content": error_msg,
                                    })
                                    self._trace(
                                        "tool_result",
                                        payload={
                                            "call_id": tc.get("id", ""),
                                            "tool_name": tool_name,
                                            "ok": False,
                                            "error": error_msg,
                                            "selection_valid": (
                                                default_tool_registry().get(
                                                    self._tool_aliases.get(
                                                        tool_name, tool_name
                                                    )
                                                ) is not None
                                            ),
                                            "arguments_valid": False,
                                        },
                                        parent_event_id=tool_call_event_id,
                                    )
                                    continue

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
                            self._trace(
                                "tool_result",
                                payload={
                                    "call_id": tc["id"],
                                    "tool_name": tool_name,
                                    "ok": False,
                                    "error": result_str,
                                    "policy_blocked": True,
                                    "selection_valid": True,
                                    "arguments_valid": True,
                                },
                                parent_event_id=tool_call_event_id,
                            )
                            continue

                        # Apply tool alias mapping
                        actual_tool_name = self._tool_aliases.get(tool_name, tool_name)

                        # Build permissions context with callback flag
                        tool_perms = dict(self.permissions) if self.permissions else {}
                        if self.permission_callback is not None:
                            tool_perms["_has_permission_callback"] = True

                        # Execute tool
                        tool_started = time.monotonic()
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
                                self._trace(
                                    "permission_decision",
                                    payload={
                                        "call_id": tc["id"],
                                        "tool_name": tool_name,
                                        "permission": "allow_shell",
                                        "allowed": bool(allowed),
                                        "decision": (
                                            "allowed" if allowed else "denied"
                                        ),
                                        "tool_executed": bool(allowed),
                                    },
                                    parent_event_id=tool_call_event_id,
                                )
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
                                    self._trace(
                                        "tool_result",
                                        payload={
                                            "call_id": tc["id"],
                                            "tool_name": tool_name,
                                            "ok": False,
                                            "error": deny_msg,
                                            "selection_valid": True,
                                            "arguments_valid": True,
                                            "duration_seconds": (
                                                time.monotonic() - tool_started
                                            ),
                                        },
                                        parent_event_id=tool_call_event_id,
                                    )
                                    continue
                            # else: no callback, fall through to normal error handling

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
                        result_payload = result.to_dict()
                        result_payload.update({
                            "call_id": tc["id"],
                            "requested_tool_name": tool_name,
                            "actual_tool_name": actual_tool_name,
                            "duration_seconds": time.monotonic() - tool_started,
                            "exit_code": (
                                result.result.get("returncode")
                                if isinstance(result.result, dict)
                                else None
                            ),
                            "side_effect_possible": actual_tool_name in {
                                "write_file", "edit_file", "bash"
                            },
                        })
                        self._trace(
                            "tool_result",
                            payload=result_payload,
                            parent_event_id=tool_call_event_id,
                        )
                        if (
                            result.ok
                            and actual_tool_name in _DIRECT_MUTATION_TOOLS
                            and self._is_implementation_path(args.get("path"))
                        ):
                            if not direct_mutation_succeeded:
                                first_direct_mutation_turn = self.turns + 1
                                post_edit_contract_guidance_pending = True
                            direct_mutation_succeeded = True

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
            self.session.stop_reason = "error"
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

    def _trace(
        self,
        event_type: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        parent_event_id: Optional[str] = None,
    ) -> Optional[str]:
        if self.runtime_observer is None:
            return None
        return self.runtime_observer.record(
            event_type,
            payload=payload or {},
            parent_event_id=parent_event_id,
            phase_id=self._current_phase_id(),
        )

    def _current_phase_id(self) -> str:
        for runtime_name in ("lifecycle", "devflow"):
            runtime = self._runtime_instances.get(runtime_name)
            session = getattr(runtime, "session", None) if runtime else None
            if session is None:
                continue
            try:
                phase = (
                    session.get_current_phase()
                    if hasattr(session, "get_current_phase")
                    else getattr(session, "phase", None)
                )
                if phase is None:
                    continue
                value = getattr(phase, "name", None) or getattr(
                    phase, "value", None
                ) or str(phase)
                normalized = str(value).strip().lower().replace(" ", "_")
                if normalized:
                    return f"{runtime_name}:{normalized}"
            except Exception:
                continue
        return "runtime"

    def _retry_call(self, call_fn, *args, **kwargs) -> Any:
        """Call a function with exponential backoff retry on API transport errors.

        Retries on HTTP 429/5xx and transient connection/timeout failures.
        """
        import sys

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                return call_fn(*args, **kwargs)
            except OpenAICompatError as e:
                last_error = e
                retryable = bool(
                    e.status_code
                    and (
                        e.status_code == 429
                        or e.status_code == 503
                        or e.status_code >= 500
                    )
                ) or (
                    e.status_code is None
                    and str(e).startswith("Connection error:")
                )
                if retryable:
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_BACKOFF_BASE ** (attempt + 1)
                        detail = (
                            f"HTTP {e.status_code}"
                            if e.status_code is not None
                            else "connection failure"
                        )
                        print(f"  \033[33m⚠ API error ({detail}), retrying in {delay:.1f}s... (attempt {attempt + 1}/{MAX_RETRIES})\033[0m", file=sys.stderr)
                        time.sleep(delay)
                        continue
                # Non-retryable error — print for visibility
                print(f"  \033[31m✖ API error: {e}\033[0m", file=sys.stderr)
                raise
            except (ConnectionError, TimeoutError) as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BACKOFF_BASE ** (attempt + 1)
                    print(f"  \033[33m⚠ API transport error, retrying in {delay:.1f}s... (attempt {attempt + 1}/{MAX_RETRIES})\033[0m", file=sys.stderr)
                    time.sleep(delay)
                    continue
                print(f"  \033[31m✖ API transport error: {e}\033[0m", file=sys.stderr)
                raise
            except Exception as e:
                print(f"  \033[31m✖ Unexpected error in API call: {e}\033[0m", file=sys.stderr)
                raise
        raise last_error  # type: ignore[misc]

    def _model_inference_kwargs(self) -> Dict[str, Any]:
        """Return the exact decoding arguments sent to the model client."""
        if self.model_config is None:
            return {}
        kwargs: Dict[str, Any] = {
            "temperature": self.model_config.temperature,
        }
        if self.model_config.max_tokens is not None:
            kwargs["max_tokens"] = self.model_config.max_tokens
        thinking_mode = getattr(self.model_config, "thinking_mode", None)
        if thinking_mode is not None:
            normalized = str(thinking_mode).strip().lower()
            if normalized not in {"auto", "enabled", "disabled"}:
                raise ValueError(
                    "thinking_mode must be one of auto, enabled, or disabled"
                )
            kwargs["thinking_mode"] = normalized
        return kwargs

    def _stream_openai(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: List[Dict[str, Any]],
        inference_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Stream completion from OpenAI-compatible API and accumulate response."""
        content_parts = []
        tool_calls_map: Dict[int, Dict[str, Any]] = {}
        usage = {"input_tokens": 0, "output_tokens": 0, "model_calls": 1, "tool_calls": 0}

        for chunk in self.client.stream(
            messages=messages,
            tools=tools,
            **inference_config,
        ):
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

    def _stream_anthropic(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        *,
        tools: List[Dict[str, Any]],
        inference_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Stream completion from Anthropic API and accumulate response."""
        content_parts = []
        tool_call_buffer: Dict[str, Dict[str, Any]] = {}
        usage = {"input_tokens": 0, "output_tokens": 0, "model_calls": 1, "tool_calls": 0}
        thinking_text = None
        thinking_signature = None

        for chunk in self.client.stream(
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            **inference_config,
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

            # Capture thinking metadata emitted at end of stream
            if "_thinking" in chunk:
                thinking_text = chunk["_thinking"]
            if "_thinking_signature" in chunk:
                thinking_signature = chunk["_thinking_signature"]

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

        # Pass thinking metadata through so it can be persisted in session
        if thinking_text:
            response["_thinking"] = thinking_text
        if thinking_signature:
            response["_thinking_signature"] = thinking_signature

        return response

    def _get_toolspec(
        self,
        allowed_names: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
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
            if allowed_names is not None and tool.name not in allowed_names:
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
            if (
                allowed_names is not None
                and vt.get("name", "") not in allowed_names
            ):
                continue
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
            "blocked_tools": list(self._blocked_tools),
        }

    # ------------------------------------------------------------------
    # Dynamic tool constraint API (for lifecycle phase-based action masking)
    # ------------------------------------------------------------------

    def set_blocked_tools(self, tool_names: List[str]) -> None:
        """Dynamically set which tools are blocked.

        This is the primary mechanism for phase-based action masking:
        the lifecycle runtime calls this before each phase to restrict
        which tools the agent can see and invoke.

        Blocked tools are:
        - Removed from the tool spec sent to the model (invisible)
        - Rejected at execution time if called anyway (defense in depth)
        """
        self._blocked_tools = list(tool_names)

    def add_blocked_tools(self, tool_names: List[str]) -> None:
        """Add tools to the blocked list without removing existing ones."""
        for name in tool_names:
            if name not in self._blocked_tools:
                self._blocked_tools.append(name)

    def remove_blocked_tools(self, tool_names: List[str]) -> None:
        """Remove tools from the blocked list (re-enable them)."""
        self._blocked_tools = [t for t in self._blocked_tools if t not in tool_names]

    def clear_blocked_tools(self) -> None:
        """Clear all dynamically blocked tools (restore full tool access)."""
        self._blocked_tools = []


import json  # For tool call parsing
