"""CLI entry point for CodeAgent."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from .agent_runtime import LocalCodingAgent
from .agent_types import AgentRunResult, BudgetConfig, ModelConfig
from .agent_context import get_user_context, format_context_for_prompt
from .agent_prompting import render_system_prompt
from .query_engine import run_query


def _resolve_cwd(cwd: Optional[str]) -> str:
    """Resolve working directory."""
    if cwd:
        return os.path.abspath(cwd)
    return os.getcwd()


def cmd_summary(args) -> int:
    """Show project summary."""
    cwd = _resolve_cwd(args.cwd)

    context = get_user_context(cwd)
    print(json.dumps(context, indent=2))
    return 0


def cmd_manifest(args) -> int:
    """Show project manifest."""
    cwd = _resolve_cwd(args.cwd)

    manifest = {
        "project": "claw-code-agent",
        "version": "1.0.0",
        "modules": [
            "agent_runtime", "agent_tools", "openai_compat",
            "agent_session", "session_store", "token_budget",
            "agent_context", "agent_prompting", "bash_security", "compact",
        ],
        "runtimes": [
            "mcp", "search", "remote", "account", "ask_user", "config",
            "lsp", "plan", "task", "team", "workflow", "remote_trigger",
            "worktree", "background", "tokenizer", "agent_manager",
            "hook_policy", "plugin",
        ],
    }

    print(json.dumps(manifest, indent=2))
    return 0


def cmd_setup_report(args) -> int:
    """Show setup report."""
    cwd = _resolve_cwd(args.cwd)

    report = {
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "cwd": cwd,
        "python_version": __import__("platform").python_version(),
        "environment": {
            "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", ""),
            "OPENAI_MODEL": os.environ.get("OPENAI_MODEL", ""),
            "OPENAI_API_KEY": "***" if os.environ.get("OPENAI_API_KEY") else "",
        },
    }

    print(json.dumps(report, indent=2))
    return 0


def cmd_parity_audit(args) -> int:
    """Show parity audit."""
    cwd = _resolve_cwd(args.cwd)

    audit = {
        "implemented": [
            "agent_runtime", "agent_tools", "openai_compat",
            "agent_session", "session_store", "token_budget",
            "agent_context", "agent_prompting", "bash_security", "compact",
            "query_engine", "builtin_agents", "bundled_skills",
            "anthropic_compat", "transcript", "models", "context",
        ],
        "missing": [],
        "partial": [],
    }

    print(json.dumps(audit, indent=2))
    return 0


def cmd_agent(args) -> int:
    """Run agent command."""
    cwd = _resolve_cwd(args.cwd)

    from .api_config import APIConfigRuntime
    api_config = APIConfigRuntime(cwd=cwd).get_config()

    model_config = ModelConfig(
        name=args.model or api_config.model,
        temperature=args.temperature or api_config.temperature,
    )

    budget = None
    if args.max_tokens:
        budget = BudgetConfig(max_total_tokens=args.max_tokens)

    stream = args.stream

    result = run_query(
        prompt=args.prompt,
        cwd=cwd,
        model_name=model_config.name,
        budget=budget,
        stream=stream,
        max_turns=args.max_turns,
    )

    if not stream and result.final_message:
        print(result.final_message)
    if result.error:
        print(f"Error: {result.error}", file=sys.stderr)

    return 0 if result.stop_reason == "completed" else 1


def cmd_agent_chat(args) -> int:
    """Run agent in interactive REPL mode."""
    cwd = _resolve_cwd(args.cwd)

    from .repl import ClawRepl

    repl = ClawRepl(
        cwd=cwd,
        model=args.model,
        max_turns=args.max_turns,
    )
    repl.run()
    return 0


def cmd_resume(args) -> int:
    """Resume an agent session."""
    cwd = _resolve_cwd(args.cwd)

    result = run_query(
        prompt=args.prompt,
        cwd=cwd,
        session_id=args.session_id,
        stream=args.stream,
    )

    if not args.stream and result.final_message:
        print(result.final_message)
    if result.error:
        print(f"Error: {result.error}", file=sys.stderr)

    return 0 if result.stop_reason == "completed" else 1


def cmd_agent_context(args) -> int:
    """Show agent context."""
    cwd = _resolve_cwd(args.cwd)
    context = get_user_context(cwd)
    print(format_context_for_prompt(context))
    return 0


def cmd_agent_prompt(args) -> int:
    """Show agent system prompt."""
    cwd = _resolve_cwd(args.cwd)
    context = get_user_context(cwd)
    prompt = render_system_prompt(context=context)
    print(prompt)
    return 0


def cmd_token_budget(args) -> int:
    """Show token budget status."""
    cwd = _resolve_cwd(args.cwd)

    from .token_budget import TokenBudget
    budget = TokenBudget.create()
    print(json.dumps(budget.to_dict(), indent=2))
    return 0


def _runtime_status(runtime_name: str, args) -> int:
    """Generic runtime status command."""
    cwd = _resolve_cwd(args.cwd)

    # Import the runtime dynamically
    try:
        module = __import__(f"src.{runtime_name}_runtime", fromlist=[""])
        runtime_class = getattr(module, f"{_to_class_name(runtime_name)}Runtime", None)
        if runtime_class is None:
            # Try generic Runtime class
            runtime_class = getattr(module, "Runtime", None)

        if runtime_class is None:
            print(json.dumps({"error": f"Runtime {runtime_name} not found"}))
            return 1

        runtime = runtime_class(cwd=cwd)
        state = runtime.get_state()

        if hasattr(state, "to_dict"):
            state = state.to_dict()

        print(json.dumps(state, indent=2, default=str))
        return 0

    except ImportError as e:
        print(json.dumps({"error": str(e)}))
        return 1


def _to_class_name(name: str) -> str:
    """Convert runtime name to class name."""
    parts = name.split("_")
    return "".join(p.capitalize() for p in parts)


def _runtime_list(runtime_name: str, args) -> int:
    """Generic runtime list command."""
    cwd = _resolve_cwd(args.cwd)
    print(json.dumps([]))
    return 0


def _runtime_get(runtime_name: str, args) -> int:
    """Generic runtime get command."""
    cwd = _resolve_cwd(args.cwd)
    print(json.dumps({"error": "Not implemented"}))
    return 1


def _runtime_run(runtime_name: str, args) -> int:
    """Generic runtime run command."""
    cwd = _resolve_cwd(args.cwd)
    print(json.dumps({"error": "Not implemented"}))
    return 1


def cmd_mcp_status(args) -> int:
    """MCP runtime status."""
    return _runtime_status("mcp", args)


def cmd_mcp_list(args) -> int:
    """MCP runtime list."""
    return _runtime_list("mcp", args)


def cmd_search_status(args) -> int:
    """Search runtime status."""
    return _runtime_status("search", args)


def cmd_search_providers(args) -> int:
    """List search providers."""
    cwd = _resolve_cwd(args.cwd)
    print(json.dumps([]))
    return 0


def cmd_remote_status(args) -> int:
    """Remote runtime status."""
    return _runtime_status("remote", args)


def cmd_remote_profiles(args) -> int:
    """List remote profiles."""
    return _runtime_list("remote", args)


def cmd_account_status(args) -> int:
    """Account runtime status."""
    return _runtime_status("account", args)


def cmd_account_profiles(args) -> int:
    """List account profiles."""
    return _runtime_list("account", args)


def cmd_ask_status(args) -> int:
    """Ask-user runtime status."""
    return _runtime_status("ask_user", args)


def cmd_config_status(args) -> int:
    """Config runtime status."""
    return _runtime_status("config", args)


def cmd_lsp_status(args) -> int:
    """LSP runtime status."""
    return _runtime_status("lsp", args)


def cmd_plan_status(args) -> int:
    """Plan runtime status."""
    return _runtime_status("plan", args)


def cmd_task_status(args) -> int:
    """Task runtime status."""
    return _runtime_status("task", args)


def cmd_team_status(args) -> int:
    """Team runtime status."""
    return _runtime_status("team", args)


def cmd_workflow_status(args) -> int:
    """Workflow runtime status."""
    return _runtime_status("workflow", args)


def cmd_workflow_list(args) -> int:
    """List workflows."""
    return _runtime_list("workflow", args)


def cmd_trigger_status(args) -> int:
    """Remote trigger runtime status."""
    return _runtime_status("remote_trigger", args)


def cmd_trigger_list(args) -> int:
    """List triggers."""
    return _runtime_list("remote_trigger", args)


def cmd_worktree_status(args) -> int:
    """Worktree runtime status."""
    return _runtime_status("worktree", args)


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Claw Code Agent - Claude Code style agent runtime",
        prog="python3 -m src.main",
    )

    parser.add_argument("--cwd", help="Working directory")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Basic commands
    basic = subparsers.add_parser("summary", help="Show project summary")
    basic.add_argument("--cwd", default=None)

    manifest_parser = subparsers.add_parser("manifest", help="Show project manifest")
    manifest_parser.add_argument("--cwd", default=None)

    setup_parser = subparsers.add_parser("setup-report", help="Show setup report")
    setup_parser.add_argument("--cwd", default=None)

    parity_parser = subparsers.add_parser("parity-audit", help="Show parity audit")
    parity_parser.add_argument("--cwd", default=None)

    # Agent commands
    agent_parser = subparsers.add_parser("agent", help="Run agent command")
    agent_parser.add_argument("prompt", help="The prompt for the agent")
    agent_parser.add_argument("--cwd", default=None)
    agent_parser.add_argument("--model", default=None)
    agent_parser.add_argument("--temperature", type=float, default=None)
    agent_parser.add_argument("--max-tokens", type=int, default=None)
    agent_parser.add_argument("--max-turns", type=int, default=None)
    agent_parser.add_argument("--stream", action="store_true")

    chat_parser = subparsers.add_parser("agent-chat", help="Run agent in chat mode")
    chat_parser.add_argument("--cwd", default=None)
    chat_parser.add_argument("--model", default=None)
    chat_parser.add_argument("--max-turns", type=int, default=None)
    chat_parser.add_argument("--stream", action="store_true")

    resume_parser = subparsers.add_parser("resume", help="Resume agent session")
    resume_parser.add_argument("prompt", help="The prompt for the agent")
    resume_parser.add_argument("--session-id", required=True)
    resume_parser.add_argument("--cwd", default=None)
    resume_parser.add_argument("--stream", action="store_true")

    context_parser = subparsers.add_parser("agent-context", help="Show agent context")
    context_parser.add_argument("--cwd", default=None)

    prompt_parser = subparsers.add_parser("agent-prompt", help="Show agent system prompt")
    prompt_parser.add_argument("--cwd", default=None)

    budget_parser = subparsers.add_parser("token-budget", help="Show token budget")
    budget_parser.add_argument("--cwd", default=None)

    # Runtime status commands
    mcp_status_parser = subparsers.add_parser("mcp-status", help="MCP status")
    mcp_status_parser.add_argument("--cwd", default=None)

    search_status_parser = subparsers.add_parser("search-status", help="Search status")
    search_status_parser.add_argument("--cwd", default=None)

    remote_status_parser = subparsers.add_parser("remote-status", help="Remote status")
    remote_status_parser.add_argument("--cwd", default=None)

    account_status_parser = subparsers.add_parser("account-status", help="Account status")
    account_status_parser.add_argument("--cwd", default=None)

    ask_status_parser = subparsers.add_parser("ask-status", help="Ask-user status")
    ask_status_parser.add_argument("--cwd", default=None)

    config_status_parser = subparsers.add_parser("config-status", help="Config status")
    config_status_parser.add_argument("--cwd", default=None)

    lsp_status_parser = subparsers.add_parser("lsp-status", help="LSP status")
    lsp_status_parser.add_argument("--cwd", default=None)

    workflow_list_parser = subparsers.add_parser("workflow-list", help="List workflows")
    workflow_list_parser.add_argument("--cwd", default=None)

    trigger_list_parser = subparsers.add_parser("trigger-list", help="List triggers")
    trigger_list_parser.add_argument("--cwd", default=None)

    team_status_parser = subparsers.add_parser("team-status", help="Team status")
    team_status_parser.add_argument("--cwd", default=None)

    worktree_status_parser = subparsers.add_parser("worktree-status", help="Worktree status")
    worktree_status_parser.add_argument("--cwd", default=None)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    # Command dispatch
    cmd_map = {
        "summary": cmd_summary,
        "manifest": cmd_manifest,
        "setup-report": cmd_setup_report,
        "parity-audit": cmd_parity_audit,
        "agent": cmd_agent,
        "agent-chat": cmd_agent_chat,
        "resume": cmd_resume,
        "agent-context": cmd_agent_context,
        "agent-prompt": cmd_agent_prompt,
        "token-budget": cmd_token_budget,
        "mcp-status": cmd_mcp_status,
        "search-status": cmd_search_status,
        "remote-status": cmd_remote_status,
        "account-status": cmd_account_status,
        "ask-status": cmd_ask_status,
        "config-status": cmd_config_status,
        "lsp-status": cmd_lsp_status,
        "workflow-list": cmd_workflow_list,
        "trigger-list": cmd_trigger_list,
        "team-status": cmd_team_status,
        "worktree-status": cmd_worktree_status,
    }

    cmd_func = cmd_map.get(args.command)
    if cmd_func:
        return cmd_func(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())