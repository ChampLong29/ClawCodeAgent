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

import textwrap

from .agent_runtime import LocalCodingAgent
from .agent_types import AgentPermissions, ModelConfig, BudgetConfig, AgentRunResult
from .bash_security import validate_bash_command, SecurityResult
from .agent_tools import execute_tool, ToolExecutionContext
from .api_config import APIConfigRuntime
from .agent_slash_commands import execute_slash_command, default_command_registry
from .devflow_runtime import DevFlowRuntime, DevFlowSession, DevFlowStep
from .session_store import list_sessions, load_agent_session

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

        # DevFlow runtime
        self._devflow_rt = DevFlowRuntime(cwd=cwd)

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

        # Show recent sessions
        self._print_recent_sessions()

        _print_colored("Type /help for commands, /exit to quit.", Colors.DIM)

    def _print_permissions(self):
        """Show current permission state."""
        shell = Colors.GREEN + "ON" if self.permissions.allow_shell else Colors.RED + "OFF"
        write = Colors.GREEN + "ON" if self.permissions.allow_write else Colors.RED + "OFF"
        _print_colored(
            f"  Shell: {shell}{Colors.RESET}{Colors.DIM} | Write: {write}",
            Colors.DIM,
        )

    def _print_recent_sessions(self):
        """Show recent sessions from session store."""
        try:
            sessions = list_sessions(self.cwd)
            if sessions:
                sessions.sort(key=lambda s: s.get("updated_at") or 0, reverse=True)
                _print_colored("")
                _print_colored("  Recent sessions:", Colors.DIM)
                for s in sessions[:5]:
                    stop = s.get("stop_reason") or "active"
                    msgs = s.get("message_count", 0)
                    sid = s.get("session_id", "")[:8]
                    _print_colored(
                        f"    {sid}  {msgs:>3} msgs  {stop:<12}",
                        Colors.DIM,
                    )
                _print_colored("  Use /sessions to list all, /resume <id> to continue", Colors.DIM)
        except Exception:
            pass

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
        elif cmd == "/sessions":
            self._cmd_sessions()
            return True
        elif cmd.startswith("/resume "):
            session_id = cmd[len("/resume "):].strip()
            self._cmd_resume(session_id)
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

        # DevFlow commands
        if cmd.startswith("/devflow"):
            self._handle_devflow(cmd)
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
  /sessions      List all saved agent sessions
  /resume <id>   Resume a saved session
  /compact       Compact conversation history
  /budget        Show token budget usage
  /retry         Retry last assistant message
  /clear         Clear screen
  /exit, /quit   Exit the REPL

DevFlow Commands:
  /devflow start <goal>   Start structured development workflow
  /devflow status         Show progress and dependency tree
  /devflow step           Show current step details
  /devflow accept         Approve architecture / steps / verified step
  /devflow reject [reason] Reject and request regeneration
  /devflow skip           Skip current step
  /devflow archive        Save session report to file

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

    # ------------------------------------------------------------------
    # DevFlow commands
    # ------------------------------------------------------------------

    def _handle_devflow(self, cmd: str) -> None:
        """Dispatch /devflow sub-commands."""
        parts = cmd.split(None, 2)
        sub = parts[1] if len(parts) > 1 else ""
        rest = parts[2] if len(parts) > 2 else ""

        if sub == "start":
            self._devflow_start(rest)
        elif sub == "status":
            self._devflow_status()
        elif sub == "step":
            self._devflow_step_detail()
        elif sub == "accept":
            self._devflow_accept()
        elif sub == "reject":
            self._devflow_reject(rest)
        elif sub == "skip":
            self._devflow_skip()
        elif sub == "archive":
            self._devflow_archive()
        elif sub == "load":
            self._devflow_load(rest)
        elif sub == "list":
            self._devflow_list()
        else:
            _print_colored(
                "DevFlow commands:\n"
                "  /devflow start <goal>    Start a new DevFlow session\n"
                "  /devflow status          Show progress and dependency tree\n"
                "  /devflow step            Show current step details\n"
                "  /devflow accept          Accept architecture / steps / verified result\n"
                "  /devflow reject [reason] Reject and request regeneration\n"
                "  /devflow skip            Skip current step\n"
                "  /devflow archive         Save session report to file\n"
                "  /devflow list            List saved sessions\n"
                "  /devflow load <id>       Load a saved session",
                Colors.DIM,
            )

    def _devflow_ensure_agent(self) -> LocalCodingAgent:
        """Ensure the agent exists with DevFlow runtime attached."""
        if self.agent is None:
            self._new_agent()

        # Attach DevFlow runtime if needed
        if self.agent and "devflow" not in getattr(self.agent, "_runtime_instances", {}):
            self.agent._runtime_instances["devflow"] = self._devflow_rt
            self.agent.runtimes.append(self._devflow_rt)

        return self.agent

    def _devflow_start(self, goal: str) -> None:
        """Start a new DevFlow session."""
        if not goal:
            _print_colored("Usage: /devflow start <development goal>", Colors.YELLOW)
            return

        agent = self._devflow_ensure_agent()
        session = self._devflow_rt.start_session(goal)

        _print_colored("")
        _print_colored("╔══════════════════════════════════════════════╗", Colors.CYAN)
        _print_colored("║  DevFlow: Structured Development Workflow    ║", Colors.BOLD + Colors.CYAN)
        _print_colored("╠══════════════════════════════════════════════╣", Colors.CYAN)
        _print_colored(f"║  Session: {session.session_id}                          ║", Colors.CYAN)
        _print_colored(f"║  Phase:   ARCHITECTURE                       ║", Colors.CYAN)
        _print_colored("╚══════════════════════════════════════════════╝", Colors.CYAN)
        _print_colored("")
        _print_colored(f"Goal: {goal}", Colors.BOLD)
        _print_colored("")
        _print_colored("Generating architecture proposal...", Colors.DIM)

        # Run the architect phase
        try:
            architecture = self._devflow_rt.propose_architecture(agent)
            _print_colored("")
            _print_colored("╭─ Proposed Architecture ──────────────────────╮", Colors.GREEN)
            for line in architecture.split("\n"):
                _print_colored(f"│ {line}", Colors.DIM)
            _print_colored("╰──────────────────────────────────────────────╯", Colors.GREEN)
            _print_colored("")
            _print_colored("Review the architecture above.", Colors.BOLD)
            _print_colored("  /devflow accept  — approve and proceed to step planning", Colors.DIM)
            _print_colored("  /devflow reject [feedback] — request changes", Colors.DIM)
        except Exception as e:
            _print_colored(f"Error generating architecture: {e}", Colors.RED)

    def _devflow_accept(self) -> None:
        """Accept the current phase's output and advance."""
        session = self._devflow_rt.get_session()
        if not session:
            _print_colored("No active DevFlow session. Use /devflow start <goal>", Colors.YELLOW)
            return

        agent = self._devflow_ensure_agent()

        if session.phase == "ARCHITECTURE":
            _print_colored("Architecture approved. Generating steps...", Colors.DIM)
            try:
                self._devflow_rt.approve_architecture()
                steps = self._devflow_rt.generate_steps(agent)
                self._print_devflow_tree()
                _print_colored("")
                _print_colored("Review the steps above.", Colors.BOLD)
                _print_colored("  /devflow accept  — approve and start implementing", Colors.DIM)
                _print_colored("  /devflow reject [feedback] — request changes", Colors.DIM)
            except Exception as e:
                _print_colored(f"Error generating steps: {e}", Colors.RED)

        elif session.phase == "STEP_DEFINITION":
            _print_colored("Steps approved. Starting implementation...", Colors.DIM)
            try:
                self._devflow_rt.approve_steps()
                self._devflow_run_implement_verify_cycle(agent)
            except Exception as e:
                _print_colored(f"Error: {e}", Colors.RED)

        elif session.phase in ("IMPLEMENTATION", "VERIFY"):
            # After verify, accept moves to next step
            step = session.get_current_step()
            if step and step.status == "verified":
                _print_colored(f"Step '{step.title}' verified. Moving to next step...", Colors.GREEN)
                has_next = self._devflow_rt.next_step()
                if has_next:
                    self._devflow_run_implement_verify_cycle(agent)
                else:
                    self._print_devflow_tree()
                    _print_colored("")
                    _print_colored("All steps complete! Session archived.", Colors.GREEN)
                    try:
                        archive_path = self._devflow_rt.archive()
                        _print_colored(f"Report saved to: {archive_path}", Colors.DIM)
                    except Exception:
                        pass
            elif step and step.status == "implemented":
                _print_colored("Running verification...", Colors.DIM)
                self._devflow_run_verify_phase(agent)
            else:
                _print_colored(
                    f"Nothing to accept in phase '{session.phase}'. "
                    f"Current step status: {step.status if step else 'N/A'}",
                    Colors.YELLOW,
                )

        elif session.phase == "DONE":
            _print_colored("DevFlow session is already complete.", Colors.GREEN)
            self._print_devflow_tree()

        else:
            _print_colored(f"Cannot accept in phase '{session.phase}'", Colors.YELLOW)

    def _devflow_reject(self, reason: str) -> None:
        """Reject the current phase's output and regenerate."""
        session = self._devflow_rt.get_session()
        if not session:
            _print_colored("No active DevFlow session.", Colors.YELLOW)
            return

        agent = self._devflow_ensure_agent()

        if session.phase == "ARCHITECTURE":
            _print_colored(f"Regenerating architecture... (feedback: {reason or 'none'})", Colors.DIM)
            if reason:
                # Append feedback as user constraint
                old_constraints = session.user_constraints
                session.user_constraints = f"{old_constraints}\nUser feedback: {reason}" if old_constraints else f"User feedback: {reason}"
            try:
                architecture = self._devflow_rt.propose_architecture(agent)
                _print_colored("")
                _print_colored("╭─ Revised Architecture ───────────────────────╮", Colors.GREEN)
                for line in architecture.split("\n"):
                    _print_colored(f"│ {line}", Colors.DIM)
                _print_colored("╰──────────────────────────────────────────────╯", Colors.GREEN)
            except Exception as e:
                _print_colored(f"Error: {e}", Colors.RED)

        elif session.phase == "STEP_DEFINITION":
            _print_colored("Regenerating steps...", Colors.DIM)
            try:
                steps = self._devflow_rt.generate_steps(agent)
                self._print_devflow_tree()
            except Exception as e:
                _print_colored(f"Error: {e}", Colors.RED)

        elif session.phase in ("IMPLEMENTATION", "VERIFY"):
            step = session.get_current_step()
            if step:
                _print_colored(f"Retrying step '{step.title}'...", Colors.DIM)
                self._devflow_rt.retry_step()
                self._devflow_run_implement_verify_cycle(agent)

        elif session.phase == "DONE":
            _print_colored("Session is already complete. Start a new one with /devflow start", Colors.YELLOW)

    def _devflow_skip(self) -> None:
        """Skip the current step."""
        session = self._devflow_rt.get_session()
        if not session:
            _print_colored("No active DevFlow session.", Colors.YELLOW)
            return

        step = session.get_current_step()
        if not step:
            _print_colored("No current step to skip.", Colors.YELLOW)
            return

        agent = self._devflow_ensure_agent()
        _print_colored(f"Skipping step: {step.title}", Colors.YELLOW)

        has_next = self._devflow_rt.skip_step()
        if has_next:
            self._devflow_run_implement_verify_cycle(agent)
        else:
            self._print_devflow_tree()
            _print_colored("No more steps. Session complete.", Colors.GREEN)

    def _devflow_status(self) -> None:
        """Show DevFlow progress and dependency tree."""
        session = self._devflow_rt.get_session()
        if not session:
            _print_colored("No active DevFlow session. Use /devflow start <goal>", Colors.YELLOW)
            sessions = self._devflow_rt.list_sessions()
            if sessions:
                _print_colored("")
                _print_colored("Saved sessions:", Colors.DIM)
                for s in sessions:
                    icon = "✅" if s["completed"] else "●"
                    _print_colored(
                        f"  {icon} {s['session_id']}: {s['overall_goal'][:60]} "
                        f"({s['phase']}, {s['steps_count']} steps)",
                        Colors.DIM,
                    )
                _print_colored("")
                _print_colored("Use /devflow load <id> to resume a session.", Colors.DIM)
            return

        self._print_devflow_tree()

    def _devflow_step_detail(self) -> None:
        """Show current step details."""
        session = self._devflow_rt.get_session()
        if not session:
            _print_colored("No active DevFlow session.", Colors.YELLOW)
            return

        step = session.get_current_step()
        if not step:
            _print_colored("No current step.", Colors.YELLOW)
            return

        self._print_step_detail(step, session)

    def _devflow_archive(self) -> None:
        """Archive the current session to a markdown file."""
        session = self._devflow_rt.get_session()
        if not session:
            _print_colored("No active DevFlow session.", Colors.YELLOW)
            return

        try:
            path = self._devflow_rt.archive()
            _print_colored(f"Session archived to: {path}", Colors.GREEN)
        except Exception as e:
            _print_colored(f"Error archiving: {e}", Colors.RED)

    def _devflow_list(self) -> None:
        """List saved DevFlow sessions."""
        sessions = self._devflow_rt.list_sessions()
        if not sessions:
            _print_colored("No saved DevFlow sessions.", Colors.DIM)
            return

        _print_colored("")
        _print_colored("Saved DevFlow Sessions:", Colors.BOLD)
        for s in sessions:
            icon = "✅" if s["completed"] else "●"
            _print_colored(
                f"  {icon} {s['session_id']}: {s['overall_goal'][:60]} "
                f"({s['phase']}, {s['steps_count']} steps)",
                Colors.DIM,
            )

    def _devflow_load(self, session_id: str) -> None:
        """Load a saved DevFlow session."""
        if not session_id:
            _print_colored("Usage: /devflow load <session_id>", Colors.YELLOW)
            return

        session = self._devflow_rt.load(session_id.strip())
        if not session:
            _print_colored(f"Session '{session_id}' not found.", Colors.YELLOW)
            return

        _print_colored(f"Loaded session: {session.session_id}", Colors.GREEN)
        _print_colored(f"Goal: {session.overall_goal}", Colors.BOLD)
        _print_colored(f"Phase: {session.phase}", Colors.DIM)
        self._print_devflow_tree()

    # ------------------------------------------------------------------
    # DevFlow execution cycle
    # ------------------------------------------------------------------

    def _devflow_run_implement_verify_cycle(self, agent: LocalCodingAgent) -> None:
        """Run the implement-then-verify cycle for the current step."""
        self._devflow_run_implement_phase(agent)
        self._devflow_run_verify_phase(agent)

    def _devflow_run_implement_phase(self, agent: LocalCodingAgent) -> None:
        """Run the implement phase for the current step."""
        session = self._devflow_rt.get_session()
        if not session:
            return

        step = session.get_current_step()
        if not step:
            return

        _print_colored("")
        self._print_step_detail(step, session)
        _print_colored("")
        _print_colored(f"Implementing step: {step.title}...", Colors.DIM)

        # Enable write permissions for implementation
        old_write = self.permissions.allow_write
        self.permissions.allow_write = True
        if agent.permissions:
            agent.permissions["allow_write"] = True

        try:
            result = self._devflow_rt.execute_step(agent)
            _print_colored("")
            _print_colored("╭─ Implementation Result ──────────────────────╮", Colors.MAGENTA)
            for line in result.split("\n"):
                _print_colored(f"│ {line}", Colors.DIM)
            _print_colored("╰──────────────────────────────────────────────╯", Colors.MAGENTA)
        except Exception as e:
            _print_colored(f"Error implementing step: {e}", Colors.RED)
            self._devflow_rt.mark_step_failed(str(e))
        finally:
            if not old_write:
                self.permissions.allow_write = False
                if agent.permissions:
                    agent.permissions["allow_write"] = False

    def _devflow_run_verify_phase(self, agent: LocalCodingAgent) -> None:
        """Run the verify phase for the current step."""
        session = self._devflow_rt.get_session()
        if not session:
            return

        step = session.get_current_step()
        if not step or step.status != "implemented":
            return

        _print_colored("")
        _print_colored(f"Verifying step: {step.title}...", Colors.DIM)

        try:
            result = self._devflow_rt.verify_step(agent)
            _print_colored("")
            _print_colored("╭─ Verification Result ────────────────────────╮", Colors.CYAN)
            for line in result.split("\n"):
                _print_colored(f"│ {line}", Colors.DIM)
            _print_colored("╰──────────────────────────────────────────────╯", Colors.CYAN)

            if step.status == "verified":
                _print_colored("")
                _print_colored(f"Step verified: {step.title}", Colors.GREEN)
                _print_colored("  /devflow accept — confirm and continue to next step", Colors.DIM)
                _print_colored("  /devflow reject [reason] — retry this step", Colors.DIM)
                _print_colored("  /devflow skip — skip this step", Colors.DIM)
            else:
                _print_colored("")
                _print_colored(f"Verification found issues in: {step.title}", Colors.YELLOW)
                _print_colored("  /devflow reject [reason] — retry implementation", Colors.DIM)
                _print_colored("  /devflow skip — skip this step", Colors.DIM)
        except Exception as e:
            _print_colored(f"Error verifying step: {e}", Colors.RED)

    # ------------------------------------------------------------------
    # Terminal visualization
    # ------------------------------------------------------------------

    def _print_devflow_tree(self) -> None:
        """Print the DevFlow dependency tree and progress bar."""
        session = self._devflow_rt.get_session()
        if not session:
            _print_colored("No active DevFlow session.", Colors.DIM)
            return

        progress = session.progress()

        # Header
        title = f"DevFlow: {session.overall_goal}"
        if len(title) > 58:
            title = title[:55] + "..."
        _print_colored("")
        _print_colored(f"╭─ {title} ─{'─' * max(1, 56 - len(title))}╮", Colors.CYAN)
        _print_colored(f"│ {' ' * 60} │", Colors.CYAN)

        # Build step tree
        steps_by_id = {s.id: s for s in session.steps}
        root_steps = [s for s in session.steps if not s.depends_on or all(
            d not in steps_by_id for d in s.depends_on
        )]

        # If no clear roots, treat step-1 as root
        if not root_steps and session.steps:
            root_steps = [session.steps[0]]

        visited = set()

        def _render_step(step: DevFlowStep, prefix: str, is_last: bool) -> None:
            if step.id in visited:
                return
            visited.add(step.id)

            icon = {
                "pending": "◇",
                "in_progress": "▶",
                "implemented": "●",
                "verified": "✅",
                "failed": "✖",
            }.get(step.status, "?")

            status_color = {
                "pending": Colors.DIM,
                "in_progress": Colors.YELLOW,
                "implemented": Colors.MAGENTA,
                "verified": Colors.GREEN,
                "failed": Colors.RED,
            }.get(step.status, Colors.DIM)

            connector = "└──" if is_last else "├──"
            _print_colored(
                f"│ {prefix}{connector} {icon} {step.title:<40} [{step.status:<12}] ",
                status_color,
            )

            # Find children (steps that depend on this one)
            children = [s for s in session.steps if step.id in s.depends_on]
            child_visited = set()
            for j, child in enumerate(children):
                if child.id not in visited and child.id not in child_visited:
                    child_visited.add(child.id)
                    child_prefix = prefix + ("    " if is_last else "│   ")
                    _render_step(child, child_prefix, j == len(children) - 1)

        # Render tree
        rendered = set()
        for i, step in enumerate(root_steps):
            if step.id not in rendered:
                rendered.add(step.id)
                icon = {
                    "pending": "◇",
                    "in_progress": "▶",
                    "implemented": "●",
                    "verified": "✅",
                    "failed": "✖",
                }.get(step.status, "?")

                status_color = {
                    "pending": Colors.DIM,
                    "in_progress": Colors.YELLOW,
                    "implemented": Colors.MAGENTA,
                    "verified": Colors.GREEN,
                    "failed": Colors.RED,
                }.get(step.status, Colors.DIM)

                connector = "└──" if i == len(root_steps) - 1 else "├──"
                _print_colored(
                    f"│ {connector} {icon} {step.title:<40} [{step.status:<12}] ",
                    status_color,
                )

                children = [s for s in session.steps if step.id in s.depends_on]
                child_visited = set()
                prefix = "    " if i == len(root_steps) - 1 else "│   "
                for j, child in enumerate(children):
                    if child.id not in child_visited:
                        child_visited.add(child.id)
                        _render_step(child, prefix, j == len(children) - 1)

        # Progress bar
        _print_colored(f"│ {' ' * 60} │", Colors.CYAN)
        bar_width = 40
        filled = int(bar_width * progress["percent"] / 100)
        bar = "█" * filled + "░" * (bar_width - filled)
        _print_colored(
            f"│ Progress: {bar} {progress['percent']}% "
            f"({progress['verified']}/{progress['total']} verified) │",
            Colors.CYAN,
        )
        _print_colored(f"╰{'─' * 62}╯", Colors.CYAN)

    def _print_step_detail(self, step: DevFlowStep, session: DevFlowSession) -> None:
        """Print detailed view of a step."""
        all_steps = {s.id: s for s in session.steps}

        # Dependency status
        dep_lines = []
        for dep_id in step.depends_on:
            dep = all_steps.get(dep_id)
            if dep:
                icon = {
                    "pending": "◇",
                    "in_progress": "▶",
                    "implemented": "●",
                    "verified": "✅",
                    "failed": "✖",
                }.get(dep.status, "?")
                dep_lines.append(f"{icon} {dep.title} ({dep_id})")
            else:
                dep_lines.append(f"? {dep_id} (not found)")

        deps_text = "\n".join(f"  {d}" for d in dep_lines) if dep_lines else "  None"

        total = len(session.steps)
        idx = session.current_step_index + 1

        _print_colored("")
        _print_colored(
            f"╭─ Step {idx}/{total}: {step.title} "
            f"{'─' * max(1, 48 - len(step.title))}╮",
            Colors.CYAN,
        )
        _print_colored(f"│ {' ' * 60} │", Colors.CYAN)

        # Goal
        goal_lines = textwrap.wrap(step.goal, width=56) if step.goal else ["(none)"]
        _print_colored(f"│ Goal:", Colors.BOLD)
        for line in goal_lines:
            _print_colored(f"│   {line}", Colors.DIM)

        _print_colored(f"│ {' ' * 60} │", Colors.CYAN)

        # Constraints
        _print_colored(f"│ Constraints:", Colors.BOLD)
        constraint_lines = textwrap.wrap(step.constraints, width=56) if step.constraints else ["None"]
        for line in constraint_lines:
            _print_colored(f"│   {line}", Colors.DIM)

        _print_colored(f"│ {' ' * 60} │", Colors.CYAN)

        # Acceptance criteria
        _print_colored(f"│ Acceptance Criteria:", Colors.BOLD)
        if step.acceptance_criteria:
            for line in step.acceptance_criteria.split("\n"):
                wrapped = textwrap.wrap(line.strip(), width=56)
                for w in wrapped:
                    _print_colored(f"│   {w}", Colors.DIM)
        else:
            _print_colored(f"│   None specified", Colors.DIM)

        _print_colored(f"│ {' ' * 60} │", Colors.CYAN)

        # Dependencies
        _print_colored(f"│ Dependencies: {deps_text.split(chr(10))[0] if dep_lines else 'None'}", Colors.DIM)
        for extra_dep in deps_text.split("\n")[1:]:
            _print_colored(f"│{extra_dep}", Colors.DIM)

        # Status
        status_color = {
            "pending": Colors.DIM,
            "in_progress": Colors.YELLOW,
            "implemented": Colors.MAGENTA,
            "verified": Colors.GREEN,
            "failed": Colors.RED,
        }.get(step.status, Colors.DIM)

        _print_colored(f"│ {' ' * 60} │", Colors.CYAN)
        _print_colored(f"│ Status: {step.status}", status_color)
        _print_colored(f"╰{'─' * 62}╯", Colors.CYAN)

    def _cmd_sessions(self):
        """List all saved agent sessions."""
        try:
            sessions = list_sessions(self.cwd)
            if not sessions:
                _print_colored("No saved sessions.", Colors.DIM)
                return
            sessions.sort(key=lambda s: s.get("updated_at") or 0, reverse=True)
            _print_colored("")
            _print_colored("Saved Sessions:", Colors.BOLD)
            _print_colored(f"  {'ID':<10} {'Msgs':>5}  {'Status':<14}  {'Model':<25}  {'CWD'}", Colors.CYAN)
            for s in sessions:
                sid = (s.get("session_id") or "")[:8]
                msgs = s.get("message_count", 0)
                stop = s.get("stop_reason") or "active"
                model = (s.get("model") or "").split("/")[-1] or "?"
                cwd = s.get("cwd") or ""
                if len(model) > 24:
                    model = model[:21] + "..."
                if len(cwd) > 40:
                    cwd = "..." + cwd[-37:]
                _print_colored(
                    f"  {sid:<10} {msgs:>5}  {stop:<14}  {model:<25}  {cwd}",
                    Colors.DIM,
                )
        except Exception as e:
            _print_colored(f"Error listing sessions: {e}", Colors.YELLOW)

    def _cmd_resume(self, session_id: str):
        """Resume a saved agent session."""
        if not session_id:
            _print_colored("Usage: /resume <session_id>", Colors.YELLOW)
            return
        try:
            session = load_agent_session(session_id, self.cwd)
            _print_colored(f"Resumed session: {session_id}", Colors.GREEN)
            _print_colored(f"  Messages: {len(session.messages)}", Colors.DIM)
            _print_colored(f"  Model: {session.model or 'unknown'}", Colors.DIM)
            if session.cwd:
                _print_colored(f"  CWD: {session.cwd}", Colors.DIM)

            # Create agent with the resumed session
            model_config = ModelConfig(name=self.model_name, temperature=self.temperature)
            self.agent = LocalCodingAgent.from_session(
                session_id=session_id,
                cwd=self.cwd,
                model_config=model_config,
            )
            self.agent.permission_callback = self._handle_interactive_permission
            self.session_id = session_id
            self._first_run = False
            _print_colored("Session loaded. Enter your prompt to continue.", Colors.DIM)
        except FileNotFoundError:
            _print_colored(f"Session '{session_id}' not found.", Colors.YELLOW)
        except Exception as e:
            _print_colored(f"Error resuming session: {e}", Colors.RED)

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
