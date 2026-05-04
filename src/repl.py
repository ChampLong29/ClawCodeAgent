"""Interactive REPL for Claw Code Agent.

Provides a rich terminal experience with:
- GNU readline line editing (history, cursor, delete by word)
- Streaming output with real-time token display
- Tool call visibility and progress indication
- Interactive permission prompts for ASK-level commands
- Slash commands for permission management
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

from .agent_runtime import LocalCodingAgent
from .agent_types import AgentPermissions, ModelConfig, BudgetConfig, AgentRunResult
from .bash_security import validate_bash_command, SecurityResult
from .agent_tools import execute_tool, ToolExecutionContext
from .api_config import APIConfigRuntime
from .agent_slash_commands import execute_slash_command, default_command_registry

# Terminal colors
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BRIGHT_BLACK = "\033[90m"

def _setup_readline():
    """Configure readline for better line editing."""
    try:
        import readline
        import atexit
        import rlcompleter  # noqa: F401

        # History file
        histfile = os.path.join(os.path.expanduser("~"), ".claw_history")
        try:
            readline.read_history_file(histfile)
            readline.set_history_length(1000)
        except (FileNotFoundError, PermissionError, OSError):
            pass
        try:
            atexit.register(readline.write_history_file, histfile)
        except (PermissionError, OSError):
            pass

        # Better tab completion
        readline.parse_and_bind("tab: complete")
        # Allow deleting entire words
        readline.parse_and_bind("set editing-mode emacs")
    except ImportError:
        pass  # readline not available on this platform


def _print_colored(text: str, color: str = "", end: str = "\n", flush: bool = True):
    """Print text with optional color."""
    if sys.stdout.isatty() and color:
        print(f"{color}{text}{Colors.RESET}", end=end, flush=flush)
    else:
        print(text, end=end, flush=flush)


class ClawRepl:
    """Interactive REPL for Claw Code Agent."""

    def __init__(
        self,
        cwd: str,
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        max_turns: Optional[int] = None,
    ):
        self.cwd = cwd

        # Read actual model from API config (files + .env + env vars)
        api_config = APIConfigRuntime(cwd=cwd).get_config()
        self.model_name = model or api_config.model
        self.api_provider = api_config.provider.value
        self.api_base_url = api_config.base_url
        self.temperature = temperature or api_config.temperature
        self.max_turns = max_turns

        # Permissions — start safe
        self.permissions = AgentPermissions(
            allow_write=False,
            allow_shell=False,
        )

        # Agent instance
        self.agent: Optional[LocalCodingAgent] = None
        self.session_id = str(uuid.uuid4())[:8]
        self._first_run = True

        _setup_readline()

    def run(self):
        """Start the REPL loop."""
        self._print_banner()

        # Create initial agent
        self._new_agent()

        while True:
            try:
                prompt = self._read_input()
                if prompt is None:
                    break  # EOF
                if not prompt.strip():
                    continue

                # Handle slash commands
                if prompt.startswith("/"):
                    if self._handle_slash(prompt):
                        continue
                    else:
                        break  # /exit or /quit

                # Execute query
                self._execute(prompt)

            except KeyboardInterrupt:
                _print_colored("\nInterrupted. Type /exit to quit.", Colors.YELLOW)
                continue
            except EOFError:
                break

        _print_colored("\nGoodbye!", Colors.GREEN)

    def _print_banner(self):
        """Print welcome banner."""
        width = 60
        _print_colored("=" * width, Colors.CYAN)
        _print_colored("  Claw Code Agent - Interactive REPL", Colors.BOLD + Colors.CYAN)
        _print_colored("=" * width, Colors.CYAN)
        _print_colored(f"  Session:  {self.session_id}", Colors.DIM)
        _print_colored(f"  Provider: {self.api_provider}", Colors.DIM)
        _print_colored(f"  Model:    {self.model_name}", Colors.DIM)
        _print_colored(f"  Base URL: {self.api_base_url}", Colors.DIM)
        _print_colored(f"  CWD:      {self.cwd}", Colors.DIM)
        self._print_permissions()
        _print_colored("=" * width, Colors.CYAN)
        _print_colored("Type /help for commands, /exit to quit.", Colors.DIM)

    def _print_permissions(self):
        """Show current permission state."""
        shell = Colors.GREEN + "ON" if self.permissions.allow_shell else Colors.RED + "OFF"
        write = Colors.GREEN + "ON" if self.permissions.allow_write else Colors.RED + "OFF"
        _print_colored(
            f"  Shell: {shell}{Colors.RESET}{Colors.DIM} | Write: {write}",
            Colors.DIM,
        )

    def _read_input(self) -> Optional[str]:
        """Read a line of input with proper prompt."""
        try:
            shell_status = "[S]" if self.permissions.allow_shell else "[s]"
            write_status = "[W]" if self.permissions.allow_write else "[w]"
            prompt = f"\n{Colors.CYAN}╭─ {shell_status}{write_status}{Colors.RESET} {Colors.BOLD}You{Colors.RESET}\n{Colors.CYAN}╰> {Colors.RESET}"
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            return None

    def _handle_slash(self, command: str) -> bool:
        """Handle slash commands. Returns True to continue, False to exit."""
        cmd = command.strip().lower()

        # Exit commands
        if cmd in ("/exit", "/quit", "/q"):
            return False

        # REPL-specific commands
        if cmd == "/permissions":
            self._print_permissions()
            return True
        elif cmd == "/allow-shell":
            self.permissions.allow_shell = True
            if self.agent:
                self.agent.permissions = self.permissions.to_dict()
            _print_colored("Shell execution ENABLED", Colors.GREEN)
            return True
        elif cmd == "/deny-shell":
            self.permissions.allow_shell = False
            if self.agent:
                self.agent.permissions = self.permissions.to_dict()
            _print_colored("Shell execution DISABLED", Colors.YELLOW)
            return True
        elif cmd == "/allow-write":
            self.permissions.allow_write = True
            if self.agent:
                self.agent.permissions = self.permissions.to_dict()
            _print_colored("File write ENABLED", Colors.GREEN)
            return True
        elif cmd == "/deny-write":
            self.permissions.allow_write = False
            if self.agent:
                self.agent.permissions = self.permissions.to_dict()
            _print_colored("File write DISABLED", Colors.YELLOW)
            return True
        elif cmd == "/clear":
            os.system("clear" if os.name != "nt" else "cls")
            return True
        elif cmd == "/status":
            if self.agent:
                state = self.agent.get_state()
                # Add runtime states
                if self.agent._runtime_instances:
                    runtime_states = {}
                    for name, rt in self.agent._runtime_instances.items():
                        try:
                            runtime_states[name] = rt.get_state()
                        except Exception:
                            runtime_states[name] = {"error": "unavailable"}
                    state["runtimes"] = runtime_states
                _print_colored(json.dumps(state, indent=2, default=str), Colors.DIM)
            else:
                _print_colored("No active agent", Colors.YELLOW)
            return True

        # Delegate to shared slash command registry for /help, /retry, /compact, /budget
        context = {
            "agent": self.agent,
            "cwd": self.cwd,
            "permissions": self.permissions,
        }
        result = execute_slash_command(command, context)

        if result is None:
            # Show REPL-specific help
            _print_colored("""
REPL Commands:
  /help          Show this help
  /permissions   Show current permissions
  /allow-shell   Enable shell execution
  /deny-shell    Disable shell execution
  /allow-write   Enable file write operations
  /deny-write    Disable file write operations
  /status        Show agent and runtime status
  /compact       Compact conversation history
  /budget        Show token budget usage
  /retry         Retry last assistant message
  /clear         Clear screen
  /exit, /quit   Exit the REPL

Type any other text to query the agent.
""", Colors.DIM)
            return True

        if isinstance(result, dict) and "error" in result:
            _print_colored(str(result["error"]), Colors.YELLOW)
        elif isinstance(result, str):
            _print_colored(result, Colors.DIM)
        return True

    def _new_agent(self):
        """Create a new agent instance with current settings."""
        model_config = ModelConfig(name=self.model_name, temperature=self.temperature)

        self.agent = LocalCodingAgent(
            cwd=self.cwd,
            model_config=model_config,
            permissions=self.permissions.to_dict(),
        )
        # Register permission callback for interactive bash permissions
        self.agent.permission_callback = self._handle_interactive_permission
        # Set session ID
        self.agent.session = None  # Will be set on first run()

    def _execute(self, prompt: str):
        """Execute a user prompt with streaming and tool visibility."""
        if self.agent is None:
            self._new_agent()

        if self._first_run:
            self.agent.session = None  # run() will create new session
            self._first_run = False
        else:
            # Resume existing session
            pass

        _print_colored("", Colors.RESET)
        _print_colored("╭─ Agent", Colors.MAGENTA)
        _print_colored("╰", Colors.MAGENTA, end="")

        try:
            result = self.agent.run(
                prompt=prompt,
                max_turns=self.max_turns,
                stream=True,  # Always stream in REPL
            )

            # Show stop reason if not completed
            if result.stop_reason == "budget_exceeded":
                _print_colored(
                    f"\n⚠ Budget exceeded: {result.error}",
                    Colors.YELLOW,
                )
            elif result.stop_reason == "error":
                _print_colored(
                    f"\n✖ Error: {result.error}",
                    Colors.RED,
                )
            elif result.stop_reason == "stopped":
                _print_colored(
                    f"\n⚠ Max turns reached: {result.final_message}",
                    Colors.YELLOW,
                )

        except Exception as e:
            _print_colored(f"\n✖ Agent error: {e}", Colors.RED)

    def _handle_interactive_permission(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> bool:
        """Ask user for permission to execute a tool.

        Called when security validation returns ASK for a bash command
        and allow_shell is not enabled.
        """
        if tool_name == "bash":
            command = arguments.get("command", "")
            security = validate_bash_command(command)

            if security == SecurityResult.DENY:
                _print_colored(
                    f"  🚫 Blocked (dangerous): {Colors.RED}{command}{Colors.RESET}",
                    Colors.RED,
                )
                return False

            if security == SecurityResult.ASK and not self.permissions.allow_shell:
                _print_colored(
                    f"\n  ⚡ Permission required for: {Colors.YELLOW}{command}{Colors.RESET}",
                    Colors.YELLOW,
                )

                # Fall back to deny if not in a TTY (e.g., piped input)
                if not sys.stdin.isatty():
                    _print_colored("  Auto-denied (non-interactive mode)", Colors.RED)
                    return False

                try:
                    choice = input(
                        f"  {Colors.YELLOW}Execute? [y]es / [n]o / [a]llow all shell: {Colors.RESET}"
                    ).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    _print_colored("  Cancelled", Colors.RED)
                    return False

                if choice == "a":
                    self.permissions.allow_shell = True
                    if self.agent:
                        self.agent.permissions = self.permissions.to_dict()
                    _print_colored("  Shell access ENABLED for session", Colors.GREEN)
                    return True
                elif choice == "y":
                    return True
                else:
                    return False

        # Allow by default for non-bash tools
        return True
