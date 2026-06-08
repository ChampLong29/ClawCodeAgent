"""Main Textual App for Claw Code Agent TUI.

Navigation modes (independent contexts):
  💬 Chat      — free-form conversation, NOT coding-related
  🔧 DevFlow   — fast structured development
  📋 Lifecycle — full engineering lifecycle (REQUIREMENTS→…→ACCEPTANCE)

Each mode owns its own AgentSession — switching modes preserves but
isolates conversation history.  DevFlow phases executed inside Lifecycle
still run within the Lifecycle agent context.

Deep-dive (🔬): available as a callable action during DevFlow/Lifecycle
execution.  Creates isolated agent sessions so research never pollutes
the main context window.  Triggered via Ctrl+D or the deep-dive button.

Questionnaire: embedded UI card flow within DevFlow/Lifecycle chat area.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional, Tuple

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import (
    Button, Footer, Header, Input, Label, ListItem, ListView,
    RichLog, Static, TextArea,
)
from textual.reactive import reactive
from textual.message import Message


# ---------------------------------------------------------------------------
# Sidebar Navigation
# ---------------------------------------------------------------------------

class NavItem(ListItem):
    """A clickable navigation item in the sidebar."""

    def __init__(self, label: str, view_id: str, icon: str = ""):
        super().__init__()
        self.label_text = label
        self.view_id = view_id
        self.icon = icon

    def compose(self) -> ComposeResult:
        yield Label(f"{self.icon} {self.label_text}")


class Sidebar(Vertical):
    """Left sidebar with navigation menu and status."""

    def compose(self) -> ComposeResult:
        yield Label("[b]Navigation[/b]", classes="section-header")
        yield ListView(
            NavItem("Chat", "chat", "💬"),
            NavItem("DevFlow", "devflow", "🔧"),
            NavItem("Lifecycle", "lifecycle", "📋"),
            NavItem("History", "history", "📂"),
            NavItem("Settings", "settings", "⚙️"),
            id="nav-list",
        )
        yield Label("", classes="section-header")
        yield Label("[b]Status[/b]", classes="section-header")
        yield Label("Shell: OFF", id="shell-status", classes="status-line")
        yield Label("Write: OFF", id="write-status", classes="status-line")
        yield Label("Mode: Chat", id="mode-status", classes="status-line")
        yield Label("Model: --", id="model-status", classes="status-line")
        yield Label("Turns: 0", id="turns-status", classes="status-line")


# ---------------------------------------------------------------------------
# Mode panel (shown above message list)
# ---------------------------------------------------------------------------

class ModePanel(Label):
    """Shows which mode is active and brief guidance."""


# ---------------------------------------------------------------------------
# Settings panel
# ---------------------------------------------------------------------------

class SettingsPanel(Vertical):
    """Settings overview panel — replaces message list when Settings is active."""

    def compose(self) -> ComposeResult:
        yield Label("[b]Configuration[/b]", classes="section-header")
        yield RichLog(id="settings-content", highlight=True, markup=True)


# ---------------------------------------------------------------------------
# Session History panel
# ---------------------------------------------------------------------------

class HistoryItem(ListItem):
    """A clickable session history entry."""

    def __init__(self, sid: str, mode: str, display_label: str):
        super().__init__()
        self.session_id = sid
        self.tui_mode = mode
        self._display = display_label

    def compose(self) -> ComposeResult:
        yield Label(self._display)


class SessionHistoryPanel(Vertical):
    """Shows saved sessions grouped by mode, with click-to-restore."""

    def compose(self) -> ComposeResult:
        yield Label("[b]📂 Session History[/b]", classes="section-header")
        yield Label("Click a session to restore it into its original mode. Current session will be saved first.", id="history-hint")
        yield ListView(id="history-list")


# ---------------------------------------------------------------------------
# Message Display
# ---------------------------------------------------------------------------

class MessageDisplay(RichLog):
    """Scrollable message display area with rich text support."""

    def add_user_message(self, text: str) -> None:
        self.write(f"\n[bold cyan]╭─ You[/bold cyan]")
        self.write(f"[cyan]│[/cyan] {text}")
        self.write(f"[bold cyan]╰─[/bold cyan]")

    def add_assistant_message(self, text: str) -> None:
        self.write(f"\n[bold magenta]╭─ Agent[/bold magenta]")
        for line in text.split("\n"):
            self.write(f"[magenta]│[/magenta] {line}")
        self.write(f"[bold magenta]╰─[/bold magenta]")

    def add_tool_call(self, tool_name: str, args_preview: str) -> None:
        icons = {
            "bash": "⚡", "read_file": "📖", "write_file": "✏️",
            "edit_file": "✂️", "list_dir": "📁", "glob_search": "🔍",
            "grep_search": "🔎", "web_search": "🌐",
        }
        icon = icons.get(tool_name, "🔧")
        self.write(f"  {icon} [dim]{tool_name}[/dim] {args_preview[:80]}")

    def add_streaming_token(self, token: str) -> None:
        """Append a single token during streaming (no newline)."""
        self.write(token, expand=True)

    def add_error(self, error: str) -> None:
        self.write(f"\n[bold red]✖ Error:[/bold red] {error}")

    def add_info(self, text: str) -> None:
        """Add an informational / system message."""
        self.write(f"\n[dim]{text}[/dim]")


# ---------------------------------------------------------------------------
# Deep-dive modal input
# ---------------------------------------------------------------------------

class DeepDiveRequest(Message):
    """Fired when user confirms a deep-dive query."""

    def __init__(self, technology: str) -> None:
        super().__init__()
        self.technology = technology


class DeepDiveModal(Vertical):
    """A compact inline modal for deep-dive technology input."""

    def compose(self) -> ComposeResult:
        yield Label("[bold]🔬 Deep-Dive Research[/bold]", classes="dd-title")
        yield Label("Enter a technology or topic to research in an isolated session:")
        yield Input(placeholder="e.g. PostgreSQL, Redis caching, FastAPI middleware", id="dd-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if text:
            self.post_message(DeepDiveRequest(text))
            self.remove()


# ---------------------------------------------------------------------------
# Input Box
# ---------------------------------------------------------------------------

class InputSubmitted(Message):
    """Fired when user submits input (Ctrl+J / Cmd+Enter)."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ChatInput(TextArea):
    """Multi-line input with Ctrl+J to submit."""

    BINDINGS = [
        Binding("ctrl+j", "submit", "Send", show=True),
    ]

    def action_submit(self) -> None:
        text = self.text.strip()
        if text:
            self.post_message(InputSubmitted(text))
            self.clear()


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

class ClawTUIApp(App):
    """Claw Code Agent — Terminal User Interface."""

    TITLE = "Claw Code Agent"
    SUB_TITLE = "TUI"
    CSS_PATH = "styles/app.tcss"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+1", "switch_chat", "Chat", show=False),
        Binding("ctrl+2", "switch_devflow", "DevFlow", show=False),
        Binding("ctrl+3", "switch_lifecycle", "Lifecycle", show=False),
        Binding("ctrl+s", "toggle_shell", "Toggle Shell"),
        Binding("ctrl+w", "toggle_write", "Toggle Write"),
        Binding("ctrl+d", "deep_dive", "Deep-dive", show=True),
    ]

    shell_enabled = reactive(False)
    write_enabled = reactive(False)
    active_mode = reactive("chat")  # chat | devflow | lifecycle | settings

    # ---- mode metadata ------------------------------------------------
    MODE_META = {
        "chat":      ("💬 Chat — free-form conversation", "chat", 0),
        "devflow":   ("🔧 DevFlow — structured development", "devflow", 1),
        "lifecycle": ("📋 Lifecycle — full engineering lifecycle", "lifecycle", 2),
        "history":   ("📂 Session History — browse & restore", "history", 3),
        "settings":  ("⚙️ Settings — configuration overview", "settings", 4),
    }
    MODE_INDEX = {v[1]: v[2] for v in MODE_META.values()}
    CODING_MODES = frozenset({"chat", "devflow", "lifecycle"})

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self, cwd: str = ".", model: Optional[str] = None):
        super().__init__()
        self.cwd = os.path.abspath(cwd)
        self.model_override = model

        # Three independent agents
        self._agents: Dict[str, Any] = {"chat": None, "devflow": None, "lifecycle": None}

        # Message stores per mode — list of (method_name, *args) for replay
        self._msg_stores: Dict[str, List[Tuple[str, tuple]]] = {
            "chat": [], "devflow": [], "lifecycle": [],
        }

        self._agent_running = False

        # Deep-dive runtime (shared across modes — creates isolated sessions)
        self._deepdive_rt = None
        self._dd_session_started = False

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-container"):
            with Vertical(id="sidebar"):
                yield Sidebar()
            with Vertical(id="chat-panel"):
                yield ModePanel("", id="mode-panel")
                yield MessageDisplay(id="message-list", highlight=True, markup=True)
                yield SettingsPanel(id="settings-panel")
                yield SessionHistoryPanel(id="history-panel")
                yield Label("", id="status-bar")
        with Vertical(id="input-container"):
            with Horizontal(id="input-row"):
                yield ChatInput(id="input-box")
                yield Button("🔬 Deep-Dive", id="dd-button", classes="dd-button")
            yield Label(
                "Ctrl+J: Send │ Ctrl+D: Deep-dive │ Ctrl+S: Shell │ Ctrl+W: Write │ Ctrl+Q: Quit",
                id="input-hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        self._init_agent("chat")
        messages = self.query_one("#message-list", MessageDisplay)
        messages.write(f"[dim]Session started | CWD: {self.cwd}[/dim]")
        messages.write(f"[dim]Model: {self._get_model_name('chat')}[/dim]")
        messages.write("[dim]Type your message below. Ctrl+J to send.[/dim]\n")
        self._msg_stores["chat"].append(("add_info", (f"Session started | CWD: {self.cwd}",)))
        self._msg_stores["chat"].append(("add_info", (f"Model: {self._get_model_name('chat')}",)))
        self._msg_stores["chat"].append(("add_info", ("Type your message below. Ctrl+J to send.",)))

        self._update_sidebar_status()
        self._apply_mode("chat")

    # ------------------------------------------------------------------
    # Agent helpers
    # ------------------------------------------------------------------

    def _get_agent(self, mode: Optional[str] = None) -> Any:
        mode = mode or self.active_mode
        return self._agents.get(mode)

    def _ensure_agent(self, mode: str) -> None:
        """Create the agent for *mode* if it doesn't exist yet."""
        if self._agents.get(mode) is not None:
            return

        from ..agent_runtime import LocalCodingAgent
        from ..agent_types import ModelConfig, AgentPermissions
        from ..api_config import APIConfigRuntime

        api_config = APIConfigRuntime(cwd=self.cwd).get_config()
        model_name = self.model_override or api_config.model

        model_config = ModelConfig(name=model_name, temperature=0.1)
        agent = LocalCodingAgent(
            cwd=self.cwd,
            model_config=model_config,
            permissions=AgentPermissions(
                allow_write=self.write_enabled,
                allow_shell=self.shell_enabled,
            ).to_dict(),
        )
        agent.permission_callback = self._handle_permission
        self._agents[mode] = agent

    def _init_agent(self, mode: str) -> None:
        """Create or re-create the agent for *mode* (used on first mount)."""
        self._agents[mode] = None
        self._ensure_agent(mode)

    def _get_model_name(self, mode: Optional[str] = None) -> str:
        agent = self._get_agent(mode)
        if agent and agent.client:
            return getattr(agent.client, "model", "unknown")
        return "unknown"

    # ------------------------------------------------------------------
    # Mode switching
    # ------------------------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        # History items: restore session instead of mode switch
        if isinstance(event.item, HistoryItem):
            self._restore_session(event.item.session_id, event.item.tui_mode)
            return
        target = getattr(event.item, "view_id", "chat")
        self._request_mode_switch(target)

    def _request_mode_switch(self, target: str) -> None:
        if target == self.active_mode:
            return
        if self._agent_running:
            self.notify("Wait for agent to finish before switching modes.", severity="warning")
            return

        # Check for active flow confirmation (skip for history/settings)
        if target in self.CODING_MODES and self.active_mode in self.CODING_MODES:
            active_flow = self._get_active_flow_name()
            if active_flow and target != self.active_mode:
                if getattr(self, "_switch_confirm_target", None) == target:
                    self._do_mode_switch(target)
                    self._switch_confirm_target = None
                else:
                    self._switch_confirm_target = target
                    self.notify(
                        f"⚠ Active {active_flow} flow in progress. "
                        f"Press again or use Ctrl+{self._mode_to_key(target)} to confirm switch.",
                        severity="warning",
                        timeout=6,
                    )
                return

        self._do_mode_switch(target)

    def _mode_to_key(self, mode: str) -> str:
        return {"chat": "1", "devflow": "2", "lifecycle": "3", "history": "4", "settings": "5"}.get(mode, "1")

    def _do_mode_switch(self, target: str) -> None:
        # Save current coding-mode session before switching away
        if self.active_mode in self.CODING_MODES:
            self._save_session(self.active_mode)
            self._store_message("add_info", "Session auto-saved.")
        self._load_messages(target)
        self._apply_mode(target)
        self._switch_confirm_target = None

    def _save_current_messages(self) -> None:
        """Save current RichLog content isn't directly accessible, so we keep
        _msg_stores updated incrementally during operation.  This is a no-op
        when the store mirrors the display."""
        pass

    def _load_messages(self, mode: str) -> None:
        """Replay stored messages into the display for *mode*."""
        messages = self.query_one("#message-list", MessageDisplay)
        messages.clear()
        for method_name, args in self._msg_stores.get(mode, []):
            fn = getattr(messages, method_name, None)
            if fn:
                fn(*args)

    def _apply_mode(self, mode: str) -> None:
        """Update UI for the active mode."""
        self.active_mode = mode
        panel = self.query_one("#mode-panel", ModePanel)
        msg_list = self.query_one("#message-list", MessageDisplay)
        settings_panel = self.query_one("#settings-panel", SettingsPanel)
        history_panel = self.query_one("#history-panel", SessionHistoryPanel)
        input_box = self.query_one("#input-box", ChatInput)
        dd_button = self.query_one("#dd-button", Button)

        # Sync sidebar highlight
        nav = self.query_one("#nav-list", ListView)
        nav.index = self.MODE_INDEX.get(mode, 0)

        show_chat = mode in self.CODING_MODES
        show_settings = mode == "settings"
        show_history = mode == "history"
        show_input = mode in self.CODING_MODES

        label, _, _ = self.MODE_META.get(mode, self.MODE_META["chat"])
        panel.update(f"[bold]{label}[/bold]")

        msg_list.display = show_chat
        settings_panel.display = show_settings
        history_panel.display = show_history
        input_box.display = show_input
        dd_button.display = show_input

        if mode in self.CODING_MODES:
            self._ensure_agent(mode)
        if mode == "settings":
            self._render_settings()
        if mode == "history":
            self._load_history()

        self._update_sidebar_status()
        self._update_status()

    def _get_active_flow_name(self) -> Optional[str]:
        """Return a human-readable name for the active flow, if any."""
        agent = self._get_agent()
        if agent is None or not hasattr(agent, "_runtime_instances"):
            return None

        # DevFlow
        df_rt = agent._runtime_instances.get("devflow")
        if df_rt and hasattr(df_rt, "has_active_session") and df_rt.has_active_session():
            session = df_rt.session
            if session and not session.completed:
                return f"DevFlow ({session.phase})"

        # Lifecycle
        lc_rt = agent._runtime_instances.get("lifecycle")
        if lc_rt and hasattr(lc_rt, "has_active_session") and lc_rt.has_active_session():
            session = lc_rt.session
            if session and not session.completed:
                phase = session.get_current_phase() if session else None
                return f"Lifecycle ({phase.name if phase else '?'})"

        return None

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    def _save_session(self, mode: str) -> None:
        """Force-save the current agent session to disk with tui_mode metadata."""
        agent = self._agents.get(mode)
        if agent is None or agent.session is None:
            return
        try:
            from ..session_store import save_agent_session_checkpoint
            agent.session.metadata["tui_mode"] = mode
            save_agent_session_checkpoint(agent.session, self.cwd)
        except Exception:
            pass  # non-fatal

    def _load_history(self) -> None:
        """Populate the history panel with saved sessions grouped by mode."""
        history_list = self.query_one("#history-list", ListView)
        history_list.clear()

        try:
            from ..session_store import list_sessions
            sessions = list_sessions(self.cwd)
            sessions.sort(key=lambda s: s.get("updated_at") or 0, reverse=True)

            if not sessions:
                history_list.append(ListItem(Label("[dim]  No saved sessions yet.[/dim]")))
                return

            for s in sessions:
                sid = s.get("session_id", "")[:8]
                name = s.get("name") or sid
                msg_count = s.get("message_count", 0)
                # tui_mode is now a top-level field from list_sessions (extracted from nested metadata)
                tui_mode = s.get("tui_mode") or self._guess_mode_from_name(name or sid)
                updated = s.get("updated_at", 0)
                import datetime
                date_str = datetime.datetime.fromtimestamp(updated).strftime("%m/%d %H:%M") if updated else "?"
                icon = {"chat": "💬", "devflow": "🔧", "lifecycle": "📋"}.get(tui_mode, "📄")
                label = f"  {icon} [{tui_mode}] {name}  │  {msg_count} msgs  │  {date_str}"
                item = HistoryItem(sid=s.get("session_id", ""), mode=tui_mode, display_label=label)
                history_list.append(item)

        except Exception as e:
            history_list.append(ListItem(Label(f"[red]Error loading sessions: {e}[/red]")))

    @staticmethod
    def _guess_mode_from_name(name: str) -> str:
        """Heuristic: guess tui_mode from session name without metadata."""
        nl = name.lower()
        if "devflow" in nl or "df-" in nl:
            return "devflow"
        if "lifecycle" in nl or "life" in nl or "lc-" in nl or "acceptance" in nl:
            return "lifecycle"
        if "chat" in nl:
            return "chat"
        return "chat"  # default

    def _restore_session(self, session_id: str, target_mode: str) -> None:
        """Save current session, then load and restore a saved session."""
        # Save current session first
        if self.active_mode in self.CODING_MODES:
            self._save_session(self.active_mode)

        # Load session from disk
        from ..session_store import load_agent_session
        from ..agent_runtime import LocalCodingAgent
        from ..agent_types import ModelConfig, AgentPermissions
        from ..api_config import APIConfigRuntime

        try:
            loaded = load_agent_session(session_id, self.cwd)
        except FileNotFoundError:
            self.notify(f"Session {session_id[:8]} not found.", severity="error")
            return

        # Build agent with loaded session
        api_config = APIConfigRuntime(cwd=self.cwd).get_config()
        model_name = self.model_override or api_config.model
        model_config = ModelConfig(name=model_name, temperature=0.1)

        agent = LocalCodingAgent(
            cwd=self.cwd,
            model_config=model_config,
            permissions=AgentPermissions(
                allow_write=self.write_enabled,
                allow_shell=self.shell_enabled,
            ).to_dict(),
        )
        agent.session = loaded
        agent.permission_callback = self._handle_permission
        self._agents[target_mode] = agent

        # Rebuild message store from loaded messages
        store: List[Tuple[str, tuple]] = []
        for msg in loaded.messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            if role == "user":
                store.append(("add_user_message", (content,)))
            elif role == "assistant":
                store.append(("add_assistant_message", (content,)))
            elif role == "tool":
                tc_id = msg.get("tool_call_id", "")
                store.append(("add_info", (f"  🔧 tool result [{tc_id[:8] if tc_id else '?'}]",)))
        self._msg_stores[target_mode] = store

        # Switch to target mode
        self._do_mode_switch(target_mode)
        self.notify(f"Restored [{target_mode}] session: {loaded.name or session_id[:8]}")

    # ------------------------------------------------------------------
    # Message recording (used by _store_message)
    # ------------------------------------------------------------------

    def _store_message(self, method: str, *args) -> None:
        """Record a display call so it can be replayed on mode switch."""
        store = self._msg_stores.get(self.active_mode)
        if store is not None:
            store.append((method, args))

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def on_input_submitted(self, event: InputSubmitted) -> None:
        if self._agent_running:
            return
        mode = self.active_mode
        if mode in ("settings", "history"):
            return

        text = event.text
        messages = self.query_one("#message-list", MessageDisplay)
        messages.add_user_message(text)
        self._store_message("add_user_message", text)

        self._agent_running = True
        self.run_worker(self._run_agent(text), name="agent-run")

    async def _run_agent(self, prompt: str) -> None:
        messages = self.query_one("#message-list", MessageDisplay)
        mode = self.active_mode
        agent = self._get_agent(mode)

        try:
            if agent is None:
                self._ensure_agent(mode)
                agent = self._get_agent(mode)

            # Sync permissions
            if agent and agent.permissions:
                agent.permissions["allow_write"] = self.write_enabled
                agent.permissions["allow_shell"] = self.shell_enabled

            messages.write("[dim]Agent is working...[/dim]")
            self._store_message("add_info", "Agent is working...")
            self._set_status_working(True)

            result = await asyncio.to_thread(
                agent.run, prompt=prompt, stream=False, max_turns=50
            )

            self._set_status_working(False)

            if result.final_message:
                messages.add_assistant_message(result.final_message)
                self._store_message("add_assistant_message", result.final_message)

            if result.error:
                messages.add_error(result.error)
                self._store_message("add_error", result.error)

            self._update_status()
            self._update_sidebar_status()

        except Exception as e:
            self._set_status_working(False)
            err_msg = str(e)
            messages.add_error(err_msg)
            self._store_message("add_error", err_msg)
        finally:
            self._agent_running = False

    # ------------------------------------------------------------------
    # Deep-dive
    # ------------------------------------------------------------------

    def action_deep_dive(self) -> None:
        """Ctrl+D: open deep-dive inline modal."""
        if self.active_mode not in self.CODING_MODES:
            return
        self.query_one("#chat-panel").mount(DeepDiveModal(id="dd-modal"))

    def on_deep_dive_request(self, event: DeepDiveRequest) -> None:
        """Execute a deep-dive query in the background."""
        messages = self.query_one("#message-list", MessageDisplay)
        tech = event.technology

        messages.write(f"\n[bold yellow]🔬 Deep-diving into: {tech}[/bold yellow]")
        messages.write("[dim]Creating isolated research session...[/dim]")
        self._store_message("add_info", f"🔬 Deep-diving into: {tech}")
        self._store_message("add_info", "Creating isolated research session...")
        self._set_status_working(True)

        self.run_worker(self._execute_deep_dive(tech), name="deep-dive", thread=True)

    async def _execute_deep_dive(self, technology: str) -> None:
        """Run deep-dive in a background thread (blocks on LLM calls)."""
        messages = self.query_one("#message-list", MessageDisplay)

        try:
            from ..deep_dive_runtime import DeepDiveRuntime

            if self._deepdive_rt is None:
                self._deepdive_rt = DeepDiveRuntime(cwd=self.cwd)

            # Start session on first use
            if not self._dd_session_started:
                mode = self.active_mode
                agent = self._get_agent(mode)
                session_id = agent.session.session_id if (agent and agent.session) else "dd-session"
                self._deepdive_rt.start_session(parent_phase=mode, parent_session_id=session_id)
                self._dd_session_started = True

            # Set agent factory (creates isolated agents)
            from ..agent_types import ModelConfig, AgentPermissions
            from ..api_config import APIConfigRuntime

            api_config = APIConfigRuntime(cwd=self.cwd).get_config()
            model_name = self.model_override or api_config.model

            def _make_agent():
                from ..agent_runtime import LocalCodingAgent
                return LocalCodingAgent(
                    cwd=self.cwd,
                    model_config=ModelConfig(name=model_name, temperature=0.1),
                    permissions=AgentPermissions(
                        allow_write=self.write_enabled,
                        allow_shell=self.shell_enabled,
                    ).to_dict(),
                )

            self._deepdive_rt.set_agent_factory(_make_agent)

            query = self._deepdive_rt.add_query(technology)
            result = await asyncio.to_thread(self._deepdive_rt.execute_query, query.id)

            messages.write(f"\n[bold green]## {technology} — Deep Analysis[/bold green]")
            for line in result.split("\n"):
                messages.write(line)
            self._store_message("add_info", f"## {technology} — Deep Analysis")
            self._store_message("add_info", result)

        except Exception as e:
            err_msg = f"Deep-dive error: {e}"
            messages.add_error(err_msg)
            self._store_message("add_error", err_msg)
        finally:
            self._set_status_working(False)

    # ------------------------------------------------------------------
    # Permission handling
    # ------------------------------------------------------------------

    def _handle_permission(self, tool_name: str, arguments: dict) -> bool:
        if tool_name == "bash":
            return self.shell_enabled
        return True

    # ------------------------------------------------------------------
    # Status updates
    # ------------------------------------------------------------------

    def _set_status_working(self, working: bool) -> None:
        status_bar = self.query_one("#status-bar", Label)
        if working:
            status_bar.update(" ⏳ Agent is working...")
        else:
            self._update_status()

    def _update_status(self) -> None:
        status_bar = self.query_one("#status-bar", Label)
        agent = self._get_agent()
        turns = agent.turns if agent else 0
        tokens = 0
        if agent and agent.usage:
            tokens = agent.usage.input_tokens + agent.usage.output_tokens

        flow = self._get_active_flow_name()
        phase = flow or "--"
        mode_label_map = {"chat": "Chat", "devflow": "DevFlow", "lifecycle": "Lifecycle", "history": "History", "settings": "Settings"}
        mode_label = mode_label_map.get(self.active_mode, self.active_mode)

        status_bar.update(
            f" {mode_label} │ Phase: {phase} │ Turns: {turns} │ Tokens: {tokens:,} │ "
            f"Shell: {'ON' if self.shell_enabled else 'OFF'} │ "
            f"Write: {'ON' if self.write_enabled else 'OFF'}"
        )

    def _update_sidebar_status(self) -> None:
        """Update sidebar status labels."""
        agent = self._get_agent()
        shell_label = self.query_one("#shell-status", Label)
        shell_label.update(
            "Shell: [bold green]ON[/bold green]" if self.shell_enabled
            else "Shell: [dim]OFF[/dim]"
        )
        write_label = self.query_one("#write-status", Label)
        write_label.update(
            "Write: [bold green]ON[/bold green]" if self.write_enabled
            else "Write: [dim]OFF[/dim]"
        )
        mode_map = {"chat": "Chat", "devflow": "DevFlow", "lifecycle": "Lifecycle", "history": "History", "settings": "Settings"}
        self.query_one("#mode-status", Label).update(
            f"Mode: {mode_map.get(self.active_mode, self.active_mode)}"
        )
        self.query_one("#model-status", Label).update(
            f"Model: {self._get_model_name()}"
        )
        self.query_one("#turns-status", Label).update(
            f"Turns: {agent.turns if agent else 0}"
        )

    # ------------------------------------------------------------------
    # Settings panel
    # ------------------------------------------------------------------

    def _render_settings(self) -> None:
        log = self.query_one("#settings-content", RichLog)
        log.clear()
        log.write("[bold]Claw Code Agent — Settings[/bold]\n")
        log.write(f"Working Directory : [bold]{self.cwd}[/bold]")
        log.write(f"Model             : [bold]{self._get_model_name('chat')}[/bold]")
        log.write(f"Shell Enabled     : [bold]{'ON' if self.shell_enabled else 'OFF'}[/bold]")
        log.write(f"Write Enabled     : [bold]{'ON' if self.write_enabled else 'OFF'}[/bold]")
        log.write(f"Active Mode       : [bold]{self.active_mode}[/bold]")
        log.write("")
        log.write("[bold]Independent Contexts[/bold]")
        for m in ("chat", "devflow", "lifecycle"):
            a = self._agents.get(m)
            status = "created" if a else "not yet"
            log.write(f"  {m:<12} : {status}")
        log.write("")
        log.write("[bold]API Configuration[/bold]")
        try:
            from ..api_config import APIConfigRuntime
            cfg = APIConfigRuntime(cwd=self.cwd).get_config()
            log.write(f"  Provider    : {cfg.provider.value}")
            log.write(f"  Base URL    : {cfg.base_url}")
            log.write(f"  Model       : {cfg.model}")
            log.write(f"  Temperature : {cfg.temperature}")
        except Exception as e:
            log.write(f"  [red]Config error: {e}[/red]")
        log.write("")
        log.write("[bold]Shortcuts[/bold]")
        log.write("  Ctrl+J  — Send message")
        log.write("  Ctrl+1  — Chat mode")
        log.write("  Ctrl+2  — DevFlow mode")
        log.write("  Ctrl+3  — Lifecycle mode")
        log.write("  Ctrl+D  — Deep-dive research")
        log.write("  Ctrl+S  — Toggle shell")
        log.write("  Ctrl+W  — Toggle write")
        log.write("  Ctrl+Q  — Quit")

    # ------------------------------------------------------------------
    # Actions (shortcuts)
    # ------------------------------------------------------------------

    def action_toggle_shell(self) -> None:
        self.shell_enabled = not self.shell_enabled
        self._update_sidebar_status()
        self._update_status()
        self.notify(f"Shell: {'ON' if self.shell_enabled else 'OFF'}")

    def action_toggle_write(self) -> None:
        self.write_enabled = not self.write_enabled
        self._update_sidebar_status()
        self._update_status()
        self.notify(f"Write: {'ON' if self.write_enabled else 'OFF'}")

    def action_switch_chat(self) -> None:
        self._request_mode_switch("chat")

    def action_switch_devflow(self) -> None:
        self._request_mode_switch("devflow")

    def action_switch_lifecycle(self) -> None:
        self._request_mode_switch("lifecycle")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "dd-button":
            self.action_deep_dive()
