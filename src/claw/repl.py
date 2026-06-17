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
from .lifecycle_runtime import LifecycleRuntime, LifecycleSession, LifecyclePhase
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

        # Lifecycle runtime
        self._lifecycle_rt = LifecycleRuntime(cwd=cwd)

        # Questionnaire runtime
        from .questionnaire_runtime import QuestionnaireRuntime
        self._questionnaire_rt = QuestionnaireRuntime(cwd=cwd)

        # Deep-dive runtime
        from .deep_dive_runtime import DeepDiveRuntime
        self._deepdive_rt = DeepDiveRuntime(cwd=cwd)

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

                # If questionnaire is active, treat input as answer
                if self._is_questionnaire_active():
                    self._questionnaire_rt.answer_current(prompt)
                    if self._questionnaire_rt.has_more():
                        self._show_current_question()
                    else:
                        self._questionnaire_done()
                    continue

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

    def _print_full_help(self):
        """Print comprehensive help with all command groups."""
        _print_colored("")
        _print_colored("Session & Permissions:", Colors.BOLD)
        _print_colored("  /permissions   Show current permissions", Colors.DIM)
        _print_colored("  /allow-shell   Enable shell execution", Colors.DIM)
        _print_colored("  /deny-shell    Disable shell execution", Colors.DIM)
        _print_colored("  /allow-write   Enable file write operations", Colors.DIM)
        _print_colored("  /deny-write    Disable file write operations", Colors.DIM)
        _print_colored("  /status        Show agent and runtime status", Colors.DIM)
        _print_colored("  /sessions      List all saved agent sessions", Colors.DIM)
        _print_colored("  /resume <id>   Resume a saved session", Colors.DIM)
        _print_colored("  /name <name>   Set or show session name", Colors.DIM)

        _print_colored("")
        _print_colored("Context Management:", Colors.BOLD)
        _print_colored("  /compact       Compact conversation history", Colors.DIM)
        _print_colored("  /budget        Show token budget usage", Colors.DIM)
        _print_colored("  /retry         Retry last assistant message", Colors.DIM)
        _print_colored("  /clear         Clear screen", Colors.DIM)
        _print_colored("  /help, /h      Show this help", Colors.DIM)

        _print_colored("")
        _print_colored("DevFlow Commands:", Colors.BOLD)
        _print_colored("  /deep-dive <technology>   Deep-dive analysis in isolated context", Colors.DIM)
        _print_colored("  /deep-dive scan           Scan current output for technologies", Colors.DIM)
        _print_colored("  /deep-dive list           List deep-dive queries", Colors.DIM)
        _print_colored("  /deep-dive view <id>      View a deep-dive result", Colors.DIM)
        _print_colored("  /deep-dive inject <id>    Inject summary into main context", Colors.DIM)
        _print_colored("")
        _print_colored("  /questionnaire start <goal> Start sequential Q&A requirements gathering", Colors.DIM)
        _print_colored("  /questionnaire status       Show questionnaire progress", Colors.DIM)
        _print_colored("  /q back / /q skip / /q goto <N> Navigate during questionnaire", Colors.DIM)
        _print_colored("")
        _print_colored("  /devflow start <goal>    Start structured development workflow", Colors.DIM)
        _print_colored("  /devflow status          Show progress and dependency tree", Colors.DIM)
        _print_colored("  /devflow step            Show current step and module details", Colors.DIM)
        _print_colored("  /devflow accept          Approve architecture / steps / modules", Colors.DIM)
        _print_colored("  /devflow reject [reason] Reject and request regeneration", Colors.DIM)
        _print_colored("  /devflow skip            Skip current step or module", Colors.DIM)
        _print_colored("  /devflow archive         Save session report to file", Colors.DIM)
        _print_colored("  /devflow list            List saved DevFlow sessions", Colors.DIM)
        _print_colored("  /devflow load <id>       Load a saved DevFlow session", Colors.DIM)

        _print_colored("")
        _print_colored("Lifecycle Commands:", Colors.BOLD)
        _print_colored("  /lifecycle start <goal>  Start full software engineering lifecycle", Colors.DIM)
        _print_colored("  /lifecycle status        Show lifecycle progress", Colors.DIM)
        _print_colored("  /lifecycle accept        Approve current phase and advance", Colors.DIM)
        _print_colored("  /lifecycle reject [msg]  Reject phase and request regeneration", Colors.DIM)
        _print_colored("  /lifecycle skip-phase    Skip current phase", Colors.DIM)
        _print_colored("  /lifecycle archive       Export full lifecycle report", Colors.DIM)
        _print_colored("  /lifecycle list          List saved lifecycle sessions", Colors.DIM)
        _print_colored("  /lifecycle load <id>     Load a saved lifecycle session", Colors.DIM)

        _print_colored("")
        _print_colored("Type any other text to query the agent.", Colors.DIM)
        _print_colored("")

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
        elif cmd == "/name":
            if self.agent and self.agent.session:
                name = self.agent.session.name or "(unnamed)"
                _print_colored(f"Session name: {name}", Colors.DIM)
                _print_colored("Use /name <new_name> to set a session name", Colors.DIM)
            else:
                _print_colored("No active session.", Colors.YELLOW)
            return True
        elif cmd.startswith("/name "):
            name = cmd[len("/name "):].strip()
            if self.agent and self.agent.session:
                self.agent.session.name = name
                from .session_store import save_agent_session
                save_agent_session(self.agent.session, self.cwd)
                _print_colored(f"Session name set to: {name}", Colors.GREEN)
            else:
                _print_colored("No active session.", Colors.YELLOW)
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

        # Reviewer commands
        if cmd.startswith("/reviewer"):
            self._handle_reviewer(cmd)
            return True

        # Deep-dive commands
        if cmd.startswith("/deep-dive") or cmd.startswith("/dd "):
            self._handle_deepdive(cmd)
            return True

        # Questionnaire commands
        if cmd.startswith("/questionnaire") or cmd.startswith("/q "):
            self._handle_questionnaire(cmd)
            return True

        # DevFlow commands
        if cmd.startswith("/devflow"):
            self._handle_devflow(cmd)
            return True

        # Lifecycle commands
        if cmd.startswith("/lifecycle"):
            self._handle_lifecycle(cmd)
            return True

        # /help — show full REPL help with all command groups
        if cmd == "/help" or cmd == "/h":
            self._print_banner()
            _print_colored("")
            self._print_full_help()
            return True

        # Delegate to shared slash command registry for /retry, /compact, /budget
        context = {
            "agent": self.agent,
            "cwd": self.cwd,
            "permissions": self.permissions,
        }
        result = execute_slash_command(command, context)

        if result is None:
            # Show REPL-specific help (fallback for unknown commands)
            self._print_full_help()
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
        """Execute a user prompt with streaming and tool visibility.

        Routing priority:
        1. Lifecycle phase questionnaire input (awaiting_input + non-slash prompt)
        2. Lifecycle pending phase auto-execution
        3. Normal agent chat
        """
        # ---- Lifecycle: handle questionnaire input ----
        if self._lifecycle_rt.has_active_session() and self._lifecycle_rt.has_phase_questionnaire():
            phase = self._lifecycle_rt.session.get_current_phase() if self._lifecycle_rt.session else None
            if phase and phase.status == "awaiting_input":
                prompt_stripped = prompt.strip()
                if prompt_stripped.lower() in ("skip", "s"):
                    has_more = self._lifecycle_rt.skip_current_phase_question()
                    if has_more:
                        self._show_phase_question()
                    else:
                        self._finalize_and_execute_phase_questionnaire()
                    return
                elif prompt_stripped.lower() in ("cancel", "quit"):
                    self._lifecycle_rt.cancel_phase_questionnaire()
                    _print_colored("Questionnaire cancelled. Phase reset to pending.", Colors.YELLOW)
                    return
                else:
                    # Treat as answer to current question
                    has_more = self._lifecycle_rt.answer_current_phase_question(prompt_stripped)
                    if has_more:
                        self._show_phase_question()
                    else:
                        self._finalize_and_execute_phase_questionnaire()
                    return

        # ---- Lifecycle: auto-execute pending phase ----
        if self._lifecycle_rt.has_active_session():
            phase = self._lifecycle_rt.session.get_current_phase() if self._lifecycle_rt.session else None
            if phase and phase.status == "pending":
                if self.agent is None:
                    self._new_agent()
                self._run_lifecycle_current_phase(self.agent)
                return

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

    # ------------------------------------------------------------------
    # Reviewer handlers
    # ------------------------------------------------------------------

    def _handle_reviewer(self, cmd: str) -> None:
        parts = cmd.split(None, 1)
        sub = parts[1] if len(parts) > 1 else ""

        if sub == "on":
            self._lifecycle_rt.set_reviewer_enabled(True)
            _print_colored("Reviewer ENABLED", Colors.GREEN)
        elif sub == "off":
            self._lifecycle_rt.set_reviewer_enabled(False)
            _print_colored("Reviewer DISABLED", Colors.YELLOW)
        elif sub == "status":
            cfg = self._lifecycle_rt.get_reviewer_config()
            status = "enabled" if cfg["enabled"] else "disabled"
            _print_colored(f"Reviewer: {status}", Colors.BOLD)
            _print_colored(f"  Auto-review phases: {', '.join(cfg['auto_review_phases'])}", Colors.DIM)
            _print_colored(f"  Strictness: {cfg['strictness']}", Colors.DIM)
        elif sub.startswith("phase"):
            # /reviewer phase <name> — manual review trigger
            phase_name = sub[5:].strip()
            if not phase_name:
                session = self._lifecycle_rt.get_session()
                if session:
                    phase = session.get_current_phase()
                    phase_name = phase.name if phase else ""
            if phase_name:
                _print_colored(f"Running Reviewer on phase: {phase_name}...", Colors.DIM)
                # Placeholder for manual review execution
                _print_colored("Manual review execution not yet wired.", Colors.YELLOW)
            else:
                _print_colored("Usage: /reviewer phase <phase-name>", Colors.YELLOW)
        else:
            _print_colored(
                "/reviewer on          Enable reviewer\n"
                "/reviewer off         Disable reviewer\n"
                "/reviewer status      Show reviewer config\n"
                "/reviewer phase <p>   Manually trigger review",
                Colors.DIM,
            )

    # ------------------------------------------------------------------
    # Deep-dive handlers
    # ------------------------------------------------------------------

    def _handle_deepdive(self, cmd: str) -> None:
        """Dispatch deep-dive sub-commands. Also handles /dd shorthand."""
        if cmd.startswith("/dd "):
            parts = ["/deep-dive"] + cmd[4:].split(None, 2)
        else:
            parts = cmd.split(None, 2)

        sub = parts[1] if len(parts) > 1 else ""
        rest = parts[2] if len(parts) > 2 else ""

        if sub in ("start", "analyze"):
            self._deepdive_analyze(rest)
        elif sub == "list":
            self._deepdive_list()
        elif sub == "view":
            self._deepdive_view(rest)
        elif sub == "inject":
            self._deepdive_inject(rest)
        elif sub == "scan":
            self._deepdive_scan()
        else:
            # Treat as technology name directly: /deep-dive PostgreSQL
            self._deepdive_analyze(sub if not rest else f"{sub} {rest}")

    def _deepdive_analyze(self, technology: str) -> None:
        """Start a deep-dive analysis for a technology."""
        if not technology:
            _print_colored("Usage: /deep-dive <technology>", Colors.YELLOW)
            _print_colored("Example: /deep-dive PostgreSQL", Colors.DIM)
            return

        technology = technology.strip()

        # Ensure session
        if not self._deepdive_rt.session:
            parent_id = ""
            parent_phase = ""
            if self._lifecycle_rt.session:
                parent_id = self._lifecycle_rt.session.session_id
                phase = self._lifecycle_rt.session.get_current_phase()
                parent_phase = phase.name if phase else ""
            elif self._devflow_rt.session:
                parent_id = self._devflow_rt.session.session_id
                parent_phase = self._devflow_rt.session.phase
            self._deepdive_rt.start_session(parent_phase, parent_id)

        # Set up agent factory for isolated sessions
        def _make_agent():
            model_config = ModelConfig(name=self.model_name, temperature=self.temperature)
            agent = LocalCodingAgent(
                model_config=model_config,
                cwd=self.cwd,
                budget=self.budget,
                tool_registry=self.tool_registry if hasattr(self, 'tool_registry') else None,
            )
            return agent

        self._deepdive_rt.set_agent_factory(_make_agent)

        query = self._deepdive_rt.add_query(technology)
        _print_colored(f"Deep-diving into: {technology}...", Colors.BOLD)
        _print_colored("(Creating isolated agent session — this won't affect your main context)", Colors.DIM)

        try:
            result = self._deepdive_rt.execute_query(query.id)
            _print_colored(f"\n## {technology} — Deep Analysis\n", Colors.BOLD)
            _print_colored(result)
            _print_colored(f"\nDeep-dive saved: {query.id}", Colors.DIM)
            _print_colored(f"Use /deep-dive inject {query.id} to add a summary to the main context", Colors.DIM)
        except Exception as e:
            _print_colored(f"Deep-dive error: {e}", Colors.RED)

    def _deepdive_list(self) -> None:
        """List all deep-dive queries."""
        session = self._deepdive_rt.session
        if not session or not session.queries:
            _print_colored("No deep-dive queries yet.", Colors.DIM)
            _print_colored("Use /deep-dive <technology> to start one.", Colors.DIM)
            return

        _print_colored(f"Deep-dive session: {session.session_id}", Colors.BOLD)
        for q in session.queries:
            icon = "✓" if q.status == "completed" else ("⏳" if q.status == "in_progress" else "✗")
            size = f"({len(q.result)} chars)" if q.result else ""
            _print_colored(f"  {icon} {q.id}: {q.technology} ({q.status}) {size}", Colors.DIM)

    def _deepdive_view(self, query_id: str) -> None:
        """View a completed deep-dive result."""
        if not query_id:
            _print_colored("Usage: /deep-dive view <query_id>", Colors.YELLOW)
            return
        result = self._deepdive_rt.get_result(query_id.strip())
        if result:
            _print_colored(result)
        else:
            _print_colored(f"Query '{query_id}' not found or not completed.", Colors.YELLOW)

    def _deepdive_inject(self, query_id: str) -> None:
        """Inject deep-dive summary into the main agent context."""
        if not query_id:
            _print_colored("Usage: /deep-dive inject <query_id>", Colors.YELLOW)
            return
        summary = self._deepdive_rt.format_for_parent(query_id.strip())
        if not summary:
            _print_colored(f"Query '{query_id}' not found or not completed.", Colors.YELLOW)
            return

        if self.agent and self.agent.session:
            self.agent.session.add_system_message(summary)
            _print_colored("Deep-dive summary injected into main agent context.", Colors.GREEN)
        else:
            _print_colored("No active agent session to inject into.", Colors.YELLOW)
        _print_colored(summary, Colors.DIM)

    def _deepdive_scan(self) -> None:
        """Scan the current phase output for technology names."""
        # Check lifecycle
        if self._lifecycle_rt.session:
            phase = self._lifecycle_rt.session.get_current_phase()
            if phase and phase.output:
                techs = self._deepdive_rt.extract_technologies(phase.output)
                if techs:
                    _print_colored(f"Technologies found in '{phase.name}' output:", Colors.BOLD)
                    for t in techs:
                        _print_colored(f"  - {t}", Colors.DIM)
                    _print_colored(f"\nUse /deep-dive <technology> to explore.", Colors.DIM)
                else:
                    _print_colored("No recognized technologies found.", Colors.YELLOW)
                return

        # Check devflow
        if self._devflow_rt.session and self._devflow_rt.session.architecture:
            techs = self._deepdive_rt.extract_technologies(
                self._devflow_rt.session.architecture
            )
            if techs:
                _print_colored("Technologies found in architecture:", Colors.BOLD)
                for t in techs:
                    _print_colored(f"  - {t}", Colors.DIM)
                _print_colored(f"\nUse /deep-dive <technology> to explore.", Colors.DIM)
                return

        _print_colored("No phase output to scan. Run a phase first.", Colors.YELLOW)

    # ------------------------------------------------------------------
    # Questionnaire handlers
    # ------------------------------------------------------------------

    def _handle_questionnaire(self, cmd: str) -> None:
        """Dispatch questionnaire sub-commands. Also handles /q shorthand."""
        # Normalize: /q xxx → /questionnaire xxx
        if cmd.startswith("/q "):
            parts = ["/questionnaire"] + cmd[3:].split(None, 2)
        elif cmd.startswith("/q"):
            # /q alone or /qback, etc.
            rest = cmd[2:].strip()
            if rest in ("back", "b"):
                self._questionnaire_back()
                return
            elif rest in ("skip", "s"):
                self._questionnaire_skip()
                return
            elif rest == "done":
                self._questionnaire_done()
                return
            elif rest.startswith("goto "):
                self._questionnaire_goto(rest[5:].strip())
                return
            elif rest.startswith("revise "):
                args = rest[7:].strip().split(None, 1)
                idx = int(args[0]) - 1 if args else -1  # 1-based → 0-based
                answer = args[1] if len(args) > 1 else ""
                self._questionnaire_revise(idx, answer)
                return
            else:
                self._show_questionnaire_help()
                return
        else:
            parts = cmd.split(None, 2)

        sub = parts[1] if len(parts) > 1 else ""
        rest = parts[2] if len(parts) > 2 else ""

        if sub == "start" or sub == "generate":
            self._questionnaire_start(rest)
        elif sub == "status":
            self._questionnaire_status()
        elif sub == "back" or sub == "b":
            self._questionnaire_back()
        elif sub == "skip" or sub == "s":
            self._questionnaire_skip()
        elif sub == "goto":
            self._questionnaire_goto(rest)
        elif sub == "revise":
            args = rest.split(None, 1)
            idx = int(args[0]) - 1 if args else -1
            answer = args[1] if len(args) > 1 else ""
            self._questionnaire_revise(idx, answer)
        elif sub == "done":
            self._questionnaire_done()
        else:
            self._show_questionnaire_help()

    def _show_questionnaire_help(self) -> None:
        _print_colored(
            "Questionnaire commands:\n"
            "  /questionnaire start <goal>  Start Q&A session\n"
            "  /questionnaire status        Show progress\n"
            "  /questionnaire back          Previous question\n"
            "  /questionnaire skip          Skip current question\n"
            "  /questionnaire goto <N>      Jump to question N\n"
            "  /questionnaire revise <N> <a> Revise answer to question N\n"
            "  /questionnaire done          Finish and compile results\n"
            "\nShorthand: /q back, /q skip, /q goto 3, /q done",
            Colors.DIM,
        )

    def _questionnaire_start(self, goal: str) -> None:
        if not goal:
            _print_colored("Usage: /questionnaire start <project goal>", Colors.YELLOW)
            return

        agent = self._ensure_agent_for_questionnaire()
        if not agent:
            return

        self._questionnaire_rt.start(goal)
        _print_colored(f"Generating questions for: {goal}", Colors.DIM)

        try:
            questions = self._questionnaire_rt.generate_questions(agent)
            _print_colored(f"Generated {len(questions)} questions.", Colors.GREEN)
            self._show_current_question()
        except Exception as e:
            _print_colored(f"Error generating questions: {e}", Colors.RED)

    def _questionnaire_status(self) -> None:
        q = self._questionnaire_rt.questionnaire
        if not q or q.status == "awaiting_generation":
            _print_colored("No active questionnaire. Use /questionnaire start <goal>", Colors.YELLOW)
            return
        p = q.progress()
        _print_colored(f"Questionnaire: {q.overall_goal}", Colors.BOLD)
        _print_colored(f"Status: {q.status}  |  Progress: Q{p['current_index'] + 1}/{p['total']}", Colors.DIM)
        _print_colored(f"  Answered: {p['answered']}  Skipped: {p['skipped']}  Pending: {p['pending']}", Colors.DIM)
        for i, qq in enumerate(q.questions):
            marker = "→" if i == q.current_question_index else " "
            icon = "✓" if qq.status == "answered" else ("-" if qq.status == "skipped" else "?")
            _print_colored(f"  {marker} [{i + 1}] {icon} {qq.text}", Colors.DIM)

    def _questionnaire_back(self) -> None:
        prev = self._questionnaire_rt.go_back()
        if prev:
            self._show_current_question()
        else:
            _print_colored("Already at the first question.", Colors.YELLOW)

    def _questionnaire_skip(self) -> None:
        self._questionnaire_rt.skip_current()
        if self._questionnaire_rt.has_more():
            self._show_current_question()
        else:
            self._questionnaire_done()

    def _questionnaire_goto(self, num_str: str) -> None:
        try:
            idx = int(num_str.strip()) - 1  # 1-based → 0-based
        except ValueError:
            _print_colored("Usage: /questionnaire goto <number>", Colors.YELLOW)
            return
        q = self._questionnaire_rt.go_to(idx)
        if q:
            self._show_current_question()
        else:
            _print_colored(f"Question {idx + 1} out of range.", Colors.YELLOW)

    def _questionnaire_revise(self, index: int, new_answer: str) -> None:
        q = self._questionnaire_rt.questionnaire
        if not q:
            _print_colored("No active questionnaire.", Colors.YELLOW)
            return
        if index < 0 or index >= len(q.questions):
            _print_colored(f"Question {index + 1} out of range.", Colors.YELLOW)
            return
        self._questionnaire_rt.revise_answer(index, new_answer)
        qq = q.questions[index]
        _print_colored(f"Revised Q{index + 1}: {qq.text}", Colors.GREEN)
        _print_colored(f"  New answer: {qq.answer or '(cleared)'}", Colors.DIM)

    def _questionnaire_done(self) -> None:
        q = self._questionnaire_rt.questionnaire
        if not q:
            _print_colored("No active questionnaire.", Colors.YELLOW)
            return

        result = self._questionnaire_rt.finalize()
        _print_colored(result, Colors.DIM)

        p = q.progress()
        remaining = p["total"] - p["answered"] - p["skipped"]
        if remaining > 0:
            _print_colored(
                f"Note: {remaining} question(s) remaining. Use /questionnaire goto <N> to address them.",
                Colors.YELLOW,
            )
        _print_colored("Questionnaire complete!", Colors.GREEN)

        # If lifecycle is in REQUIREMENTS phase, store the output
        lifecycle_session = self._lifecycle_rt.get_session()
        if lifecycle_session:
            lifecycle_phase = lifecycle_session.get_current_phase()
            if lifecycle_phase and lifecycle_phase.name == "REQUIREMENTS":
                lifecycle_phase.output = result
                lifecycle_phase.status = "in_progress"
                self._lifecycle_rt.save()
                _print_colored("Output stored in REQUIREMENTS phase.", Colors.DIM)
                _print_colored("Use /lifecycle accept to advance.", Colors.DIM)

    def _show_current_question(self) -> None:
        q = self._questionnaire_rt.get_current_question()
        if not q:
            return
        p = self._questionnaire_rt.questionnaire.progress() if self._questionnaire_rt.questionnaire else {}
        total = p.get("total", 0)
        current = p.get("current_index", 0) + 1
        _print_colored(f"[Q {current}/{total}] {q.text}", Colors.BOLD)

    def _ensure_agent_for_questionnaire(self):
        if self.agent is None:
            self._new_agent()
        return self.agent

    def _is_questionnaire_active(self) -> bool:
        """Check if the user is currently in a questionnaire session."""
        q = self._questionnaire_rt.questionnaire
        return q is not None and q.status == "active"

    # ------------------------------------------------------------------
    # DevFlow handlers
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
        elif sub == "edit-step":
            self._devflow_edit_step(rest)
        elif sub == "remove-step":
            self._devflow_remove_step(rest)
        elif sub == "add-step":
            self._devflow_add_step(rest)
        elif sub == "move-step":
            self._devflow_move_step(rest)
        elif sub == "rollback":
            self._devflow_rollback_step(rest)
        elif sub == "rollback-phase":
            self._devflow_rollback_phase(rest)
        elif sub == "rollback-targets":
            self._devflow_rollback_targets()
        elif sub == "archive":
            self._devflow_archive()
        elif sub == "load":
            self._devflow_load(rest)
        elif sub == "list":
            self._devflow_list()
        else:
            _print_colored(
                "DevFlow commands:\n"
                "  /devflow start <goal>       Start a new DevFlow session\n"
                "  /devflow status             Show progress and dependency tree\n"
                "  /devflow step               Show current step details\n"
                "  /devflow accept             Accept architecture / steps / verified result\n"
                "  /devflow reject [reason]    Reject and request regeneration\n"
                "  /devflow skip               Skip current step\n"
                "  /devflow edit-step <id>     Edit step fields\n"
                "  /devflow remove-step <id>   Remove a step\n"
                "  /devflow add-step <title>   Add a new step\n"
                "  /devflow move-step <id>     Reorder steps\n"
                "  /devflow rollback <step-id> Roll back to a specific step\n"
                "  /devflow rollback-phase <p> Roll back to a specific phase\n"
                "  /devflow rollback-targets   List steps/phases for rollback\n"
                "  /devflow archive            Save session report to file\n"
                "  /devflow list               List saved sessions\n"
                "  /devflow load <id>          Load a saved session",
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

        # Set agent's working directory to the dedicated project folder
        project_dir = self._devflow_rt.get_project_dir()
        if project_dir and agent:
            agent.cwd = project_dir

        _print_colored("")
        _print_colored("╔══════════════════════════════════════════════╗", Colors.CYAN)
        _print_colored("║  DevFlow: Structured Development Workflow    ║", Colors.BOLD + Colors.CYAN)
        _print_colored("╠══════════════════════════════════════════════╣", Colors.CYAN)
        sid = session.session_id
        if len(sid) > 34:
            sid = sid[:31] + "..."
        _print_colored(f"║  Session: {sid:<34}║", Colors.CYAN)
        _print_colored(f"║  Phase:   ARCHITECTURE                       ║", Colors.CYAN)
        _print_colored("╚══════════════════════════════════════════════╝", Colors.CYAN)
        _print_colored("")
        _print_colored(f"Goal: {goal}", Colors.BOLD)
        if project_dir:
            _print_colored(f"Project: {project_dir}", Colors.DIM)
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
                self._devflow_rt.approve_architecture(
                    agent_session=agent.session if agent else None
                )
                steps = self._devflow_rt.generate_steps(agent)
                self._print_devflow_tree()
                _print_colored("")
                _print_colored("Review the steps above.", Colors.BOLD)
                _print_colored("  /devflow accept  — approve and analyze first step", Colors.DIM)
                _print_colored("  /devflow reject [feedback] — request changes", Colors.DIM)
            except Exception as e:
                _print_colored(f"Error generating steps: {e}", Colors.RED)

        elif session.phase == "STEP_DEFINITION":
            _print_colored("Steps approved. Analyzing first step...", Colors.DIM)
            try:
                self._devflow_rt.approve_steps(
                    agent_session=agent.session if agent else None
                )
                # STEP_ANALYSIS: generate module breakdown
                self._devflow_run_analyze_phase(agent)
            except Exception as e:
                _print_colored(f"Error: {e}", Colors.RED)

        elif session.phase == "STEP_ANALYSIS":
            # Approve modules and start implementing
            step = session.get_current_step()
            if step and step.has_modules():
                _print_colored("Modules approved. Starting module-by-module implementation...", Colors.DIM)
                try:
                    self._devflow_rt.approve_modules(
                        agent_session=agent.session if agent else None
                    )
                    self._devflow_run_module_cycle(agent)
                except Exception as e:
                    _print_colored(f"Error: {e}", Colors.RED)
            else:
                # No modules generated — skip to legacy mode
                _print_colored("No modules generated. Using full-step mode...", Colors.YELLOW)
                try:
                    self._devflow_rt.approve_modules(
                        agent_session=agent.session if agent else None
                    )  # will fail if no modules
                except Exception:
                    # Fallback: manually set phase to IMPLEMENTATION
                    self._devflow_rt.session.phase = "IMPLEMENTATION"
                    self._devflow_rt.save()
                self._devflow_run_implement_verify_cycle(agent)

        elif session.phase in ("IMPLEMENTATION", "VERIFY"):
            step = session.get_current_step()
            if step and step.has_modules():
                # Module mode: accept current module → verify → advance
                module = step.get_current_module()
                if module and module.status == "implemented":
                    _print_colored(f"Verifying module: {module.file_path}...", Colors.DIM)
                    self._devflow_run_verify_phase(agent)
                    # After verify, show result and prompt for next module
                    if module.status == "verified":
                        has_next = self._devflow_rt.next_module()
                        if has_next:
                            self._prompt_module_confirm()
                        else:
                            # All modules done, advance to next step
                            _print_colored(f"All modules for step '{step.title}' complete!", Colors.GREEN)
                            self._devflow_rt.next_step(
                                agent_session=agent.session if agent else None
                            )
                            self._devflow_run_analyze_phase(agent) if self._devflow_rt.session.phase != "DONE" else self._print_devflow_tree()
                    else:
                        _print_colored(f"Module verification failed. Use /devflow reject [reason] to retry.", Colors.YELLOW)
                elif module and module.status == "verified":
                    # Module already verified, advance to next
                    has_next = self._devflow_rt.next_module()
                    if has_next:
                        self._prompt_module_confirm()
                    else:
                        _print_colored(f"All modules for step '{step.title}' complete!", Colors.GREEN)
                        self._devflow_rt.next_step(
                            agent_session=agent.session if agent else None
                        )
                        if self._devflow_rt.session.phase != "DONE":
                            self._devflow_run_analyze_phase(agent)
                        else:
                            self._print_devflow_tree()
                            _print_colored("All steps complete!", Colors.GREEN)
                else:
                    _print_colored("Nothing to accept. Enter /devflow reject [reason] to retry.", Colors.YELLOW)
            else:
                # Legacy full-step mode
                if step and step.status == "verified":
                    _print_colored(f"Step '{step.title}' verified. Moving to next step...", Colors.GREEN)
                    has_next = self._devflow_rt.next_step(
                        agent_session=agent.session if agent else None
                    )
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

        elif session.phase == "STEP_ANALYSIS":
            _print_colored("Regenerating module breakdown...", Colors.DIM)
            try:
                modules = self._devflow_rt.analyze_step(agent)
                if modules:
                    self._devflow_rt.session.phase = "STEP_ANALYSIS"
                    self._devflow_rt.save()
                    _print_colored(f"Generated {len(modules)} modules.", Colors.GREEN)
                    _print_colored("  /devflow accept — approve modules", Colors.DIM)
                    _print_colored("  /devflow reject — regenerate", Colors.DIM)
            except Exception as e:
                _print_colored(f"Error: {e}", Colors.RED)

        elif session.phase in ("IMPLEMENTATION", "VERIFY"):
            step = session.get_current_step()
            if step:
                if step.has_modules():
                    module = step.get_current_module()
                    if module:
                        _print_colored(f"Retrying module '{module.file_path}'...", Colors.DIM)
                        module.status = "pending"
                        module.implementation_result = None
                        module.verification_result = None
                        self._devflow_rt.save()
                        self._prompt_module_confirm()
                else:
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

        has_next = self._devflow_rt.skip_step(
            agent_session=agent.session if agent else None
        )
        if has_next:
            self._devflow_run_implement_verify_cycle(agent)
        else:
            self._print_devflow_tree()
            _print_colored("No more steps. Session complete.", Colors.GREEN)

    def _devflow_edit_step(self, args: str) -> None:
        """Edit a step's fields."""
        session = self._devflow_rt.get_session()
        if not session:
            _print_colored("No active DevFlow session.", Colors.YELLOW)
            return

        parts = args.split(None, 1)
        step_id = parts[0] if parts else ""

        if not step_id:
            _print_colored("Usage: /devflow edit-step <step-id> [--title ...] [--goal ...] [--constraints ...] [--acceptance ...]", Colors.YELLOW)
            return

        step = next((s for s in session.steps if s.id == step_id), None)
        if not step:
            _print_colored(f"Step '{step_id}' not found.", Colors.YELLOW)
            return

        # Parse keyword arguments
        rest = parts[1] if len(parts) > 1 else ""
        kw = self._parse_step_kwargs(rest)

        ok = self._devflow_rt.edit_step(
            step_id,
            title=kw.get("title"),
            goal=kw.get("goal"),
            constraints=kw.get("constraints"),
            acceptance_criteria=kw.get("acceptance"),
        )
        if ok:
            _print_colored(f"Step '{step_id}' updated.", Colors.GREEN)
            self._print_devflow_tree()
        else:
            _print_colored("Edit failed.", Colors.RED)

    def _devflow_remove_step(self, step_id: str) -> None:
        """Remove a step from the plan."""
        if not step_id:
            _print_colored("Usage: /devflow remove-step <step-id>", Colors.YELLOW)
            return

        session = self._devflow_rt.get_session()
        if not session:
            _print_colored("No active DevFlow session.", Colors.YELLOW)
            return

        step = next((s for s in session.steps if s.id == step_id), None)
        if not step:
            _print_colored(f"Step '{step_id}' not found.", Colors.YELLOW)
            return

        _print_colored(f"Remove step '{step.title}'? (y/N) ", Colors.YELLOW, end="")
        try:
            confirm = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            _print_colored("Cancelled.", Colors.DIM)
            return

        if confirm != "y":
            _print_colored("Cancelled.", Colors.DIM)
            return

        ok = self._devflow_rt.remove_step(step_id)
        if ok:
            _print_colored(f"Step '{step_id}' removed.", Colors.GREEN)
            self._print_devflow_tree()
        else:
            _print_colored("Remove failed.", Colors.RED)

    def _devflow_add_step(self, args: str) -> None:
        """Add a new step to the plan."""
        parts = args.split(None, 1)
        title = parts[0] if parts else ""

        if not title:
            _print_colored("Usage: /devflow add-step <title> [--after <id>] [--goal ...] [--constraints ...] [--acceptance ...]", Colors.YELLOW)
            return

        rest = parts[1] if len(parts) > 1 else ""
        kw = self._parse_step_kwargs(rest)

        ok = self._devflow_rt.add_step(
            title=title,
            after_step_id=kw.get("after"),
            goal=kw.get("goal", ""),
            constraints=kw.get("constraints", ""),
            acceptance_criteria=kw.get("acceptance", ""),
        )
        if ok:
            _print_colored(f"Step '{title}' added.", Colors.GREEN)
            self._print_devflow_tree()
        else:
            _print_colored("Add failed.", Colors.RED)

    def _devflow_move_step(self, args: str) -> None:
        """Move a step before another step."""
        parts = args.split()
        step_id = parts[0] if parts else ""
        before_id = ""

        # Try --before <id> or positional second arg
        if len(parts) >= 3 and parts[1] == "--before":
            before_id = parts[2]
        elif len(parts) >= 2:
            before_id = parts[1]

        if not step_id or not before_id:
            _print_colored("Usage: /devflow move-step <step-id> --before <target-id>", Colors.YELLOW)
            return

        ok = self._devflow_rt.move_step(step_id, before_id)
        if ok:
            _print_colored(f"Step '{step_id}' moved before '{before_id}'.", Colors.GREEN)
            self._print_devflow_tree()
        else:
            _print_colored(f"Move failed. Check that both step IDs exist.", Colors.RED)

    def _parse_step_kwargs(self, rest: str) -> Dict[str, str]:
        """Parse --key value pairs from a rest argument string."""
        kw: Dict[str, str] = {}
        if not rest:
            return kw
        tokens = rest.split()
        i = 0
        while i < len(tokens):
            if tokens[i].startswith("--"):
                key = tokens[i][2:]
                i += 1
                if i < len(tokens) and not tokens[i].startswith("--"):
                    kw[key] = tokens[i]
                    i += 1
                else:
                    kw[key] = ""
            else:
                i += 1
        return kw

    def _devflow_rollback_step(self, step_id: str) -> None:
        """Roll back to a specific DevFlow step."""
        if not step_id:
            _print_colored("Usage: /devflow rollback <step-id>", Colors.YELLOW)
            return

        session = self._devflow_rt.get_session()
        if not session:
            _print_colored("No active DevFlow session.", Colors.YELLOW)
            return

        step_id = step_id.strip()
        step = next((s for s in session.steps if s.id == step_id), None)
        if not step:
            _print_colored(f"Step '{step_id}' not found.", Colors.YELLOW)
            return

        current_step = session.get_current_step()
        if current_step and current_step.id == step_id:
            _print_colored(f"Already at step '{step_id}'.", Colors.YELLOW)
            return

        agent = self._devflow_ensure_agent()
        agent_session = agent.session if agent else None
        ok = self._devflow_rt.rollback_to_step(step_id, agent_session)
        if ok:
            _print_colored(f"Rolled back to step '{step.title}'.", Colors.GREEN)
            self._print_devflow_tree()
        else:
            _print_colored("Rollback failed.", Colors.RED)

    def _devflow_rollback_phase(self, phase_name: str) -> None:
        """Roll back to a DevFlow phase."""
        if not phase_name:
            _print_colored(
                "Usage: /devflow rollback-phase <phase>\n"
                "Valid phases: ARCHITECTURE, STEP_DEFINITION, STEP_ANALYSIS",
                Colors.YELLOW,
            )
            return

        session = self._devflow_rt.get_session()
        if not session:
            _print_colored("No active DevFlow session.", Colors.YELLOW)
            return

        agent = self._devflow_ensure_agent()
        agent_session = agent.session if agent else None
        ok = self._devflow_rt.rollback_to_phase(phase_name.strip(), agent_session)
        if ok:
            _print_colored(f"Rolled back to phase '{phase_name}'.", Colors.GREEN)
            self._print_devflow_tree()
        else:
            _print_colored(f"Cannot roll back to '{phase_name}'.", Colors.YELLOW)

    def _devflow_rollback_targets(self) -> None:
        """List available rollback targets for DevFlow."""
        session = self._devflow_rt.get_session()
        if not session:
            _print_colored("No active DevFlow session.", Colors.YELLOW)
            return

        _print_colored("Rollback targets:", Colors.BOLD)

        # Show phases
        phase_order = [
            "ARCHITECTURE", "STEP_DEFINITION", "STEP_ANALYSIS",
            "IMPLEMENTATION",
        ]
        current_phase_idx = (
            phase_order.index(session.phase)
            if session.phase in phase_order
            else len(phase_order)
        )
        _print_colored("  Phases:", Colors.DIM)
        for i, p in enumerate(phase_order):
            if i < current_phase_idx:
                _print_colored(f"    - {p} (rollback available)", Colors.DIM)
            elif i == current_phase_idx:
                _print_colored(f"    - {p} (current)", Colors.BOLD)

        # Show steps
        current_step = session.get_current_step()
        if session.steps:
            _print_colored("  Steps:", Colors.DIM)
            for s in session.steps:
                if current_step and s.id == current_step.id:
                    _print_colored(f"    - {s.id}: {s.title} (current, {s.status})", Colors.BOLD)
                elif s.status in ("verified", "implemented", "failed"):
                    _print_colored(f"    - {s.id}: {s.title} ({s.status})", Colors.DIM)

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
    # DevFlow STEP_ANALYSIS + Module execution
    # ------------------------------------------------------------------

    def _devflow_run_analyze_phase(self, agent: LocalCodingAgent) -> None:
        """Run the STEP_ANALYSIS phase to generate module breakdown."""
        session = self._devflow_rt.get_session()
        if not session:
            return

        step = session.get_current_step()
        if not step:
            return

        _print_colored("")
        _print_colored(f"Analyzing step: {step.title}...", Colors.DIM)
        _print_colored("Breaking down into implementation modules...", Colors.DIM)

        try:
            modules = self._devflow_rt.analyze_step(agent)
            if not modules:
                _print_colored("No modules generated. Will use full-step mode.", Colors.YELLOW)
                return

            _print_colored("")
            _print_colored("╭─ Module Breakdown ────────────────────────────╮", Colors.CYAN)
            for i, m in enumerate(modules):
                idx = f"{i + 1}/{len(modules)}"
                _print_colored(f"│                                               │", Colors.CYAN)
                _print_colored(f"│ Module {idx}: {m.file_path}", Colors.BOLD)
                _print_colored(f"│   Goal: {m.goal[:55]}", Colors.DIM)
                if m.constraints:
                    _print_colored(f"│   Constraints: {m.constraints[:52]}", Colors.DIM)
                if m.acceptance_criteria:
                    _print_colored(f"│   Criteria: {m.acceptance_criteria[:52]}", Colors.DIM)
            _print_colored("╰───────────────────────────────────────────────╯", Colors.CYAN)
            _print_colored("")
            _print_colored("Review the module breakdown above.", Colors.BOLD)
            _print_colored("  /devflow accept  — approve modules and start implementing", Colors.DIM)
            _print_colored("  /devflow reject [feedback] — request changes", Colors.DIM)
        except Exception as e:
            _print_colored(f"Error analyzing step: {e}", Colors.RED)

    def _prompt_module_confirm(self) -> None:
        """Show the current module details and prompt user for confirmation."""
        module = self._devflow_rt.get_current_module()
        if not module:
            return

        step = self._devflow_rt.get_current_step()
        total = len(step.modules) if step else 0
        idx = (step.current_module_index if step else 0) + 1

        _print_colored("")
        width = 56
        title = f"Module {idx}/{total}: {module.file_path}"
        if len(title) > width - 4:
            title = title[:width - 7] + "..."
        _print_colored(f"╭─ {title} ─{'─' * max(1, width - len(title) - 4)}╮", Colors.CYAN)
        _print_colored(f"│ {' ' * width} │", Colors.CYAN)
        _print_colored(f"│ Goal:", Colors.BOLD)
        for line in textwrap.wrap(module.goal, width=width - 4):
            _print_colored(f"│   {line}", Colors.DIM)
        if module.constraints:
            _print_colored(f"│ {' ' * width} │", Colors.CYAN)
            _print_colored(f"│ Constraints:", Colors.BOLD)
            for line in textwrap.wrap(module.constraints, width=width - 4):
                _print_colored(f"│   {line}", Colors.DIM)
        if module.acceptance_criteria:
            _print_colored(f"│ {' ' * width} │", Colors.CYAN)
            _print_colored(f"│ Acceptance Criteria:", Colors.BOLD)
            for line in module.acceptance_criteria.split("\n"):
                for w in textwrap.wrap(line.strip(), width=width - 4):
                    _print_colored(f"│   {w}", Colors.DIM)
        _print_colored(f"╰{'─' * (width + 2)}╯", Colors.CYAN)
        _print_colored("")
        _print_colored("Proceed? [y]es / [m]odify / [s]kip module", Colors.YELLOW)

        try:
            choice = input(f"  {Colors.YELLOW}Choice: {Colors.RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _print_colored("  Cancelled", Colors.RED)
            return

        agent = self._devflow_ensure_agent()

        if choice == "y" or choice == "yes":
            self._devflow_run_implement_phase(agent)
            self._devflow_run_verify_phase(agent)
            # After verify, check if we need to advance
            module_after = self._devflow_rt.get_current_module()
            if module_after and module_after.status == "verified":
                has_next = self._devflow_rt.next_module()
                if has_next:
                    self._prompt_module_confirm()
                else:
                    _print_colored(f"All modules complete!", Colors.GREEN)
        elif choice == "m" or choice == "modify":
            _print_colored("Enter modified goal/constraints/criteria (or leave blank to keep):", Colors.DIM)
            try:
                new_goal = input(f"  Goal [{module.goal[:40]}...]: ").strip()
                new_constraints = input(f"  Constraints [{module.constraints[:40]}...]: ").strip()
                new_criteria = input(f"  Criteria [{module.acceptance_criteria[:40]}...]: ").strip()
                if new_goal:
                    module.goal = new_goal
                if new_constraints:
                    module.constraints = new_constraints
                if new_criteria:
                    module.acceptance_criteria = new_criteria
                self._devflow_rt.save()
                _print_colored("Module updated. Showing updated details...", Colors.GREEN)
                self._prompt_module_confirm()
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice == "s" or choice == "skip":
            module.status = "failed"
            module.verification_result = "Skipped by user."
            self._devflow_rt.save()
            _print_colored(f"Skipped module: {module.file_path}", Colors.YELLOW)
            has_next = self._devflow_rt.next_module()
            if has_next:
                self._prompt_module_confirm()
        else:
            _print_colored("Invalid choice. Use /devflow accept when ready.", Colors.YELLOW)

    def _devflow_run_module_cycle(self, agent: LocalCodingAgent) -> None:
        """Start module-by-module implementation for the current step."""
        step = self._devflow_rt.get_current_step()
        if not step or not step.has_modules():
            _print_colored("No modules to implement.", Colors.YELLOW)
            return

        self._prompt_module_confirm()

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
            _print_colored(f"  {'ID':<10} {'Name':<30} {'Msgs':>5}  {'Status':<14}  {'Model':<20}", Colors.CYAN)
            for s in sessions:
                sid = (s.get("session_id") or "")[:8]
                name = (s.get("name") or "")[:28] or "(unnamed)"
                msgs = s.get("message_count", 0)
                stop = s.get("stop_reason") or "active"
                model = (s.get("model") or "").split("/")[-1] or "?"
                if len(model) > 18:
                    model = model[:15] + "..."
                _print_colored(
                    f"  {sid:<10} {name:<30} {msgs:>5}  {stop:<14}  {model:<20}",
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

    # ------------------------------------------------------------------
    # Lifecycle commands
    # ------------------------------------------------------------------

    def _handle_lifecycle(self, cmd: str) -> None:
        """Dispatch /lifecycle sub-commands."""
        parts = cmd.split(None, 2)
        sub = parts[1] if len(parts) > 1 else ""
        rest = parts[2] if len(parts) > 2 else ""

        if sub == "start":
            self._lifecycle_start(rest)
        elif sub == "status":
            self._lifecycle_status()
        elif sub == "accept":
            self._lifecycle_accept()
        elif sub == "reject":
            self._lifecycle_reject(rest)
        elif sub == "skip-phase":
            self._lifecycle_skip_phase()
        elif sub == "rollback":
            self._lifecycle_rollback(rest)
        elif sub == "rollback-targets":
            self._lifecycle_rollback_targets()
        elif sub == "archive":
            self._lifecycle_archive()
        elif sub == "list":
            self._lifecycle_list()
        elif sub == "load":
            self._lifecycle_load(rest)
        else:
            _print_colored(
                "Lifecycle commands:\n"
                "  /lifecycle start <goal>       Start a full software engineering lifecycle\n"
                "  /lifecycle status             Show lifecycle progress\n"
                "  /lifecycle accept             Approve current phase output and advance\n"
                "  /lifecycle reject [reason]    Reject and request regeneration\n"
                "  /lifecycle skip-phase         Skip current phase\n"
                "  /lifecycle rollback <phase>   Roll back to a specific phase\n"
                "  /lifecycle rollback-targets   List phases you can roll back to\n"
                "  /lifecycle archive         Export full lifecycle report\n"
                "  /lifecycle list            List saved sessions\n"
                "  /lifecycle load <id>       Load a saved session",
                Colors.DIM,
            )

    def _lifecycle_ensure_agent(self) -> LocalCodingAgent:
        """Ensure an agent is ready for lifecycle operations."""
        if self.agent is None:
            self._new_agent()
        return self.agent

    def _run_lifecycle_current_phase(self, agent: LocalCodingAgent) -> None:
        """Execute the current lifecycle phase and display the output.

        For questionnaire-enabled phases (REQUIREMENTS, SYSTEM_DESIGN,
        ARCHITECTURE), this first generates clarifying questions and
        enters an interactive Q&A loop.  User answers are fed into the
        skill prompt as authoritative input.
        """
        session = self._lifecycle_rt.get_session()
        if not session:
            return

        phase = session.get_current_phase()
        if not phase:
            return

        if phase.status not in ("pending",):
            return

        # — Questionnaire-eligible phase → generate questions first —
        if self._lifecycle_rt.phase_supports_questionnaire():
            if not self._lifecycle_rt.has_phase_questionnaire():
                _print_colored("")
                _print_colored(f"Generating clarifying questions for {phase.name}...", Colors.DIM)
                try:
                    questions = self._lifecycle_rt.generate_phase_questionnaire(agent)
                    _print_colored(
                        f"I have {len(questions)} questions about your {phase.name.lower()}. ",
                        Colors.GREEN,
                    )
                    _print_colored("Type your answer (or 'skip' / 'cancel'):", Colors.DIM)
                    self._show_phase_question()
                except Exception as e:
                    _print_colored(f"Error generating questions: {e}", Colors.RED)
                    _print_colored("Falling back to direct phase execution.", Colors.YELLOW)
                    self._lifecycle_rt.cancel_phase_questionnaire()
                    self._execute_phase_direct(agent, phase)
                return

        # — Direct execution (non-questionnaire phase) —
        self._execute_phase_direct(agent, phase)

    def _execute_phase_direct(self, agent: LocalCodingAgent, phase) -> None:
        """Run the phase with the lifecycle skill prompt directly."""
        _print_colored("")
        _print_colored(f"Running {phase.name} phase...", Colors.DIM)

        try:
            output = self._lifecycle_rt.execute_phase(agent)
            _print_colored("")
            _print_colored(f"╭─ {phase.name} Output ─────────────────────────╮", Colors.GREEN)
            for line in output.split("\n"):
                _print_colored(f"│ {line}", Colors.DIM)
            _print_colored("╰──────────────────────────────────────────────╯", Colors.GREEN)
            _print_colored("")
            _print_colored("Review the output above.", Colors.BOLD)
            _print_colored("  /lifecycle accept  — approve and advance to next phase", Colors.DIM)
            _print_colored("  /lifecycle reject [feedback] — request regeneration", Colors.DIM)
        except Exception as e:
            _print_colored(f"Error executing phase: {e}", Colors.RED)

    def _show_phase_question(self) -> None:
        """Display the current questionnaire question."""
        q = self._lifecycle_rt.get_current_phase_question()
        if not q:
            return
        prog = self._lifecycle_rt.phase_questionnaire_progress()
        _print_colored("")
        _print_colored(f"╭─ Q{prog['current_index']}/{prog['total']} ─────────────────────────────────", Colors.CYAN)
        _print_colored(f"│ {q['text']}", Colors.BOLD)
        _print_colored("╰", Colors.CYAN, end="")
        _print_colored(" > ", Colors.CYAN, end="")

    def _finalize_and_execute_phase_questionnaire(self) -> None:
        """Compile questionnaire answers and feed them into phase execution."""
        _print_colored("")
        _print_colored("All questions answered! Generating phase output...", Colors.GREEN)
        try:
            results = self._lifecycle_rt.finalize_phase_questionnaire()
            if self.agent is None:
                self._new_agent()
            phase = self._lifecycle_rt.session.get_current_phase() if self._lifecycle_rt.session else None
            if phase:
                _print_colored("")
                _print_colored(f"Running {phase.name} with your input...", Colors.DIM)
                output = self._lifecycle_rt.execute_phase(self.agent, questionnaire_results=results)
                _print_colored("")
                _print_colored(f"╭─ {phase.name} Output ─────────────────────────╮", Colors.GREEN)
                for line in output.split("\n"):
                    _print_colored(f"│ {line}", Colors.DIM)
                _print_colored("╰──────────────────────────────────────────────╯", Colors.GREEN)
                _print_colored("")
                _print_colored("Review the output above.", Colors.BOLD)
                _print_colored("  /lifecycle accept  — approve and advance to next phase", Colors.DIM)
                _print_colored("  /lifecycle reject [feedback] — request regeneration", Colors.DIM)
        except Exception as e:
            _print_colored(f"Error executing phase: {e}", Colors.RED)

    def _lifecycle_start(self, goal: str) -> None:
        """Start a new lifecycle session and auto-execute the first phase."""
        if not goal:
            _print_colored("Usage: /lifecycle start <development goal>", Colors.YELLOW)
            return

        agent = self._lifecycle_ensure_agent()
        session = self._lifecycle_rt.start_session(goal)

        # Set agent's working directory to the dedicated project folder
        project_dir = self._lifecycle_rt.get_project_dir()
        if project_dir and agent:
            agent.cwd = project_dir

        _print_colored("")
        _print_colored("╔══════════════════════════════════════════════╗", Colors.CYAN)
        _print_colored("║  Lifecycle: Full Software Engineering        ║", Colors.BOLD + Colors.CYAN)
        _print_colored("╠══════════════════════════════════════════════╣", Colors.CYAN)
        sid = session.session_id
        if len(sid) > 34:
            sid = sid[:31] + "..."
        _print_colored(f"║  Session: {sid:<34}║", Colors.CYAN)
        _print_colored("╚══════════════════════════════════════════════╝", Colors.CYAN)
        _print_colored("")
        _print_colored(f"Goal: {goal}", Colors.BOLD)
        if project_dir:
            _print_colored(f"Project: {project_dir}", Colors.DIM)
        _print_colored("")
        self._print_lifecycle_status()

        # Auto-execute the first phase (like DevFlow auto-runs architect)
        self._run_lifecycle_current_phase(agent)

    def _lifecycle_status(self) -> None:
        """Show lifecycle progress."""
        session = self._lifecycle_rt.get_session()
        if not session:
            _print_colored("No active lifecycle session. Use /lifecycle start <goal>", Colors.YELLOW)
            sessions = self._lifecycle_rt.list_sessions()
            if sessions:
                _print_colored("")
                _print_colored("Saved sessions:", Colors.DIM)
                for s in sessions:
                    icon = "✅" if s.get("completed") else "●"
                    _print_colored(
                        f"  {icon} {s['session_id']}: {s['overall_goal'][:60]} "
                        f"({s.get('current_phase', '?')})",
                        Colors.DIM,
                    )
                _print_colored("")
                _print_colored("Use /lifecycle load <id> to resume a session.", Colors.DIM)
            return

        self._print_lifecycle_status()

    def _lifecycle_accept(self) -> None:
        """Accept the current phase output and advance to the next phase."""
        session = self._lifecycle_rt.get_session()
        if not session:
            _print_colored("No active lifecycle session.", Colors.YELLOW)
            return

        phase = session.get_current_phase()
        if not phase:
            _print_colored("All phases complete!", Colors.GREEN)
            return

        if phase.status not in ("in_progress", "completed"):
            _print_colored(
                f"Current phase '{phase.name}' is '{phase.status}'. "
                f"Run the agent first to execute this phase.",
                Colors.YELLOW,
            )
            return

        _print_colored(f"Phase '{phase.name}' accepted.", Colors.GREEN)
        has_next = self._lifecycle_rt.advance_phase(
            agent_session=self.agent.session if self.agent else None
        )

        if has_next:
            new_phase = session.get_current_phase()
            if new_phase:
                _print_colored(f"Next phase: {new_phase.name}", Colors.BOLD)
            self._print_lifecycle_status()
            # Auto-execute the next phase
            agent = self._lifecycle_ensure_agent()
            self._run_lifecycle_current_phase(agent)
        else:
            _print_colored("")
            _print_colored("All lifecycle phases complete!", Colors.GREEN)
            try:
                archive_path = self._lifecycle_rt.archive()
                _print_colored(f"Report saved to: {archive_path}", Colors.DIM)
            except Exception:
                pass

    def _lifecycle_reject(self, reason: str) -> None:
        """Reject the current phase output and request retry."""
        session = self._lifecycle_rt.get_session()
        if not session:
            _print_colored("No active lifecycle session.", Colors.YELLOW)
            return

        phase = session.get_current_phase()
        if not phase:
            _print_colored("No current phase to reject.", Colors.YELLOW)
            return

        _print_colored(f"Rejecting phase '{phase.name}'... (feedback: {reason or 'none'})", Colors.YELLOW)

        # Append feedback to constraints so the re-execution can use it
        if reason:
            current = session.user_constraints or ""
            session.user_constraints = f"{current}\nRejection feedback: {reason}".strip()
            self._lifecycle_rt.save()

        self._lifecycle_rt.retry_phase()

        # Auto-retry the phase with feedback
        agent = self._lifecycle_ensure_agent()
        self._run_lifecycle_current_phase(agent)

    def _lifecycle_skip_phase(self) -> None:
        """Skip the current phase."""
        session = self._lifecycle_rt.get_session()
        if not session:
            _print_colored("No active lifecycle session.", Colors.YELLOW)
            return

        phase = session.get_current_phase()
        if not phase:
            _print_colored("No current phase to skip.", Colors.YELLOW)
            return

        _print_colored(f"Skipping phase: {phase.name}", Colors.YELLOW)
        has_next = self._lifecycle_rt.skip_phase(
            agent_session=self.agent.session if self.agent else None
        )

        if has_next:
            new_phase = session.get_current_phase()
            if new_phase:
                _print_colored(f"Next phase: {new_phase.name}", Colors.BOLD)
        else:
            _print_colored("All phases complete!", Colors.GREEN)

    def _lifecycle_archive(self) -> None:
        """Archive the current lifecycle session to a markdown report."""
        session = self._lifecycle_rt.get_session()
        if not session:
            _print_colored("No active lifecycle session.", Colors.YELLOW)
            return

        try:
            path = self._lifecycle_rt.archive()
            _print_colored(f"Lifecycle report saved to: {path}", Colors.GREEN)
        except Exception as e:
            _print_colored(f"Error archiving: {e}", Colors.RED)

    def _lifecycle_list(self) -> None:
        """List saved lifecycle sessions."""
        sessions = self._lifecycle_rt.list_sessions()
        if not sessions:
            _print_colored("No saved lifecycle sessions.", Colors.DIM)
            return

        _print_colored("")
        _print_colored("Saved Lifecycle Sessions:", Colors.BOLD)
        for s in sessions:
            icon = "✅" if s.get("completed") else "●"
            _print_colored(
                f"  {icon} {s['session_id']}: {s['overall_goal'][:60]} "
                f"({s.get('current_phase', '?')}, {s.get('phase_count', 0)} phases)",
                Colors.DIM,
            )

    def _lifecycle_load(self, session_id: str) -> None:
        """Load a saved lifecycle session."""
        if not session_id:
            _print_colored("Usage: /lifecycle load <session_id>", Colors.YELLOW)
            return

        session = self._lifecycle_rt.load(session_id.strip())
        if not session:
            _print_colored(f"Session '{session_id}' not found.", Colors.YELLOW)
            sessions = self._lifecycle_rt.list_sessions()
            if sessions:
                _print_colored("Available sessions:", Colors.DIM)
                for s in sessions:
                    _print_colored(f"  {s['session_id']}: {s['overall_goal'][:60]}", Colors.DIM)
            return

        _print_colored(f"Loaded lifecycle session: {session.session_id}", Colors.GREEN)
        _print_colored(f"Goal: {session.overall_goal}", Colors.BOLD)
        self._print_lifecycle_status()

    def _lifecycle_rollback(self, phase_name: str) -> None:
        """Roll back to a specific lifecycle phase."""
        if not phase_name:
            _print_colored("Usage: /lifecycle rollback <phase_name>", Colors.YELLOW)
            _print_colored("Use /lifecycle rollback-targets to see available phases.", Colors.DIM)
            return

        session = self._lifecycle_rt.get_session()
        if not session:
            _print_colored("No active lifecycle session.", Colors.YELLOW)
            return

        phase_name = phase_name.strip()
        targets = self._lifecycle_rt.list_rollback_targets()
        target = next((t for t in targets if t["name"] == phase_name), None)

        if not target:
            _print_colored(f"Phase '{phase_name}' not found or not eligible for rollback.", Colors.YELLOW)
            if targets:
                names = ", ".join(t["name"] for t in targets)
                _print_colored(f"Eligible phases: {names}", Colors.DIM)
            return

        # Show what will be discarded
        current_phase = session.get_current_phase()
        discarded = [
            p.name for p in session.phases[target["index"]:session.current_phase_index + 1]
        ]
        _print_colored(f"Rolling back to '{phase_name}'.", Colors.YELLOW)
        _print_colored(f"This will discard: {', '.join(discarded)}", Colors.YELLOW)
        _print_colored("Continue? (y/N) ", Colors.BOLD, end="")

        try:
            confirm = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            _print_colored("Rollback cancelled.", Colors.DIM)
            return

        if confirm != "y":
            _print_colored("Rollback cancelled.", Colors.DIM)
            return

        agent_session = self.agent.session if self.agent else None
        ok = self._lifecycle_rt.rollback_to_phase(phase_name, agent_session)
        if ok:
            _print_colored(f"Rolled back to {phase_name}.", Colors.GREEN)
            new_phase = session.get_current_phase()
            if new_phase:
                _print_colored(f"Current phase: {new_phase.name}", Colors.BOLD)
            self._print_lifecycle_status()
        else:
            _print_colored("Rollback failed.", Colors.RED)

    def _lifecycle_rollback_targets(self) -> None:
        """List phases available for rollback."""
        targets = self._lifecycle_rt.list_rollback_targets()
        if not targets:
            _print_colored("No phases available for rollback.", Colors.DIM)
            return

        _print_colored("Phases you can roll back to:", Colors.BOLD)
        for t in targets:
            snap = "snapshot" if t["snapshot_exists"] else "no snapshot"
            _print_colored(f"  [{t['index']}] {t['name']} ({t['status']}, {snap})", Colors.DIM)

    def _build_phase_bar(self, progress: Dict[str, Any]) -> str:
        """Build a progress bar string."""
        if progress["total"] == 0:
            return ""
        bar_width = 20
        filled = int((progress["percent"] / 100) * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        return f"{bar} {progress['percent']}% ({progress['completed']}/{progress['total']})"

    def _print_lifecycle_status(self) -> None:
        """Print the lifecycle progress overview."""
        session = self._lifecycle_rt.get_session()
        if not session:
            return

        progress = session.progress()

        _print_colored("")
        _print_colored(
            f"╭─ Lifecycle: {session.overall_goal[:50]} ─────────────────────╮",
            Colors.CYAN,
        )

        status_icons = {
            "pending": "◇",
            "in_progress": "▶",
            "completed": "✅",
            "skipped": "⏭️",
            "failed": "✖",
        }

        for phase in session.phases:
            icon = status_icons.get(phase.status, "?")
            marker = " ← current" if (session.get_current_phase() and phase.name == session.get_current_phase().name) else ""
            artifact = f" → {phase.artifact_path}" if phase.artifact_path else ""
            _print_colored(
                f"│  {icon} {phase.name:<22} [{phase.status}]{marker}{artifact}",
                Colors.DIM,
            )

        bar = self._build_phase_bar(progress)
        _print_colored(f"│  Progress: {bar}", Colors.DIM)
        _print_colored("╰──────────────────────────────────────────────────╯", Colors.CYAN)
