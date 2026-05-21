"""Main Textual App for Claw Code Agent TUI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Footer, Header, Static, TextArea, ListView, ListItem, Label, RichLog
from textual.reactive import reactive
from textual.worker import Worker, get_current_worker
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
            NavItem("Settings", "settings", "⚙️"),
            id="nav-list",
        )
        yield Label("", classes="section-header")
        yield Label("[b]Status[/b]", classes="section-header")
        yield Label("Shell: OFF", id="shell-status", classes="status-line")
        yield Label("Write: OFF", id="write-status", classes="status-line")
        yield Label("Model: --", id="model-status", classes="status-line")
        yield Label("Turns: 0", id="turns-status", classes="status-line")


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


# ---------------------------------------------------------------------------
# Input Box
# ---------------------------------------------------------------------------

class InputSubmitted(Message):
    """Fired when user submits input (Ctrl+Enter)."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ChatInput(TextArea):
    """Multi-line input with Ctrl+Enter to submit."""

    BINDINGS = [
        Binding("ctrl+j", "submit", "Send", show=True),  # Ctrl+Enter
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
        Binding("ctrl+1", "view_chat", "Chat", show=False),
        Binding("ctrl+2", "view_devflow", "DevFlow", show=False),
        Binding("ctrl+3", "view_lifecycle", "Lifecycle", show=False),
        Binding("ctrl+s", "toggle_shell", "Toggle Shell"),
        Binding("ctrl+w", "toggle_write", "Toggle Write"),
    ]

    shell_enabled = reactive(False)
    write_enabled = reactive(False)

    def __init__(self, cwd: str = ".", model: Optional[str] = None):
        super().__init__()
        self.cwd = os.path.abspath(cwd)
        self.model_override = model
        self._agent = None
        self._agent_running = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-container"):
            with Vertical(id="sidebar"):
                yield Sidebar()
            with Vertical(id="chat-panel"):
                yield MessageDisplay(id="message-list", highlight=True, markup=True)
                yield Label(
                    " Phase: -- │ Turns: 0 │ Tokens: 0 ",
                    id="status-bar",
                )
        with Vertical(id="input-container"):
            yield ChatInput(id="input-box")
            yield Label(
                "Ctrl+J: Send │ Ctrl+S: Shell │ Ctrl+W: Write │ Ctrl+Q: Quit",
                id="input-hint",
            )
        yield Footer()

    def on_mount(self) -> None:
        """Initialize agent on mount."""
        self._init_agent()
        messages = self.query_one("#message-list", MessageDisplay)
        messages.write(f"[dim]Session started | CWD: {self.cwd}[/dim]")
        messages.write(f"[dim]Model: {self._get_model_name()}[/dim]")
        messages.write("[dim]Type your message below. Ctrl+J to send.[/dim]\n")

    def _init_agent(self) -> None:
        """Create the agent instance."""
        from ..agent_runtime import LocalCodingAgent
        from ..agent_types import ModelConfig, AgentPermissions
        from ..api_config import APIConfigRuntime

        api_config = APIConfigRuntime(cwd=self.cwd).get_config()
        model_name = self.model_override or api_config.model

        model_config = ModelConfig(name=model_name, temperature=0.1)
        self._agent = LocalCodingAgent(
            cwd=self.cwd,
            model_config=model_config,
            permissions=AgentPermissions(
                allow_write=self.write_enabled,
                allow_shell=self.shell_enabled,
            ).to_dict(),
        )
        # Set permission callback
        self._agent.permission_callback = self._handle_permission

    def _get_model_name(self) -> str:
        if self._agent and self._agent.client:
            return getattr(self._agent.client, "model", "unknown")
        return "unknown"

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def on_input_submitted(self, event: InputSubmitted) -> None:
        """Handle user message submission."""
        if self._agent_running:
            return  # Ignore input while agent is running

        text = event.text
        messages = self.query_one("#message-list", MessageDisplay)
        messages.add_user_message(text)

        # Run agent in background worker
        self._agent_running = True
        self.run_worker(self._run_agent(text), name="agent-run")

    async def _run_agent(self, prompt: str) -> None:
        """Run agent query in background worker."""
        messages = self.query_one("#message-list", MessageDisplay)

        try:
            if self._agent is None:
                self._init_agent()

            # Update permissions
            if self._agent.permissions:
                self._agent.permissions["allow_write"] = self.write_enabled
                self._agent.permissions["allow_shell"] = self.shell_enabled

            result = self._agent.run(prompt=prompt, stream=False, max_turns=50)

            if result.final_message:
                messages.add_assistant_message(result.final_message)

            if result.error:
                messages.add_error(result.error)

            # Update status
            self._update_status()

        except Exception as e:
            messages.add_error(str(e))
        finally:
            self._agent_running = False

    # ------------------------------------------------------------------
    # Permission handling
    # ------------------------------------------------------------------

    def _handle_permission(self, tool_name: str, arguments: dict) -> bool:
        """Permission callback — for now auto-deny, TODO: modal."""
        # In future: show a Modal and wait for user response
        # For now: auto-allow if shell_enabled
        if tool_name == "bash":
            return self.shell_enabled
        return True

    # ------------------------------------------------------------------
    # Status updates
    # ------------------------------------------------------------------

    def _update_status(self) -> None:
        """Update the status bar and sidebar indicators."""
        status_bar = self.query_one("#status-bar", Label)
        turns = self._agent.turns if self._agent else 0
        tokens = 0
        if self._agent and self._agent.usage:
            tokens = self._agent.usage.input_tokens + self._agent.usage.output_tokens

        status_bar.update(
            f" Turns: {turns} │ Tokens: {tokens:,} │ "
            f"Shell: {'ON' if self.shell_enabled else 'OFF'} │ "
            f"Write: {'ON' if self.write_enabled else 'OFF'}"
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_toggle_shell(self) -> None:
        self.shell_enabled = not self.shell_enabled
        self._update_status()
        self.notify(f"Shell: {'ON' if self.shell_enabled else 'OFF'}")

    def action_toggle_write(self) -> None:
        self.write_enabled = not self.write_enabled
        self._update_status()
        self.notify(f"Write: {'ON' if self.write_enabled else 'OFF'}")

    def action_view_chat(self) -> None:
        self.notify("Chat view (active)")

    def action_view_devflow(self) -> None:
        self.notify("DevFlow view (coming soon)")

    def action_view_lifecycle(self) -> None:
        self.notify("Lifecycle view (coming soon)")
