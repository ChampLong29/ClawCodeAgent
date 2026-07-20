"""Claw Code Agent TUI — Textual-based terminal interface.

Launch with: claw tui --cwd /path/to/project
"""

import os
import sys
from typing import Any


def _load_app_class() -> Any:
    """Load the Textual app only when a TUI is actually started."""
    try:
        from .app import ClawTUIApp as app_cls
    except ImportError:
        return None
    return app_cls


def __getattr__(name: str) -> Any:
    """Keep `from claw.tui import ClawTUIApp` compatible and lazy."""
    if name == "ClawTUIApp":
        return _load_app_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _tui_install_hint() -> str:
    """Detect install type and return the right command to install textual."""
    import claw
    claw_path = os.path.dirname(os.path.abspath(claw.__file__))

    # Walk up from claw package looking for pyproject.toml (editable install)
    probe = claw_path
    for _ in range(6):
        if os.path.exists(os.path.join(probe, "pyproject.toml")):
            return f"  cd {probe} && uv sync --extra tui"
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent

    # Not an editable install — assume global/tool install
    return "  pip install claw-code-agent[tui]\n  (or uv tool install --reinstall claw-code-agent[tui])"


def main():
    """Entry point for claw-tui command."""
    import argparse

    parser = argparse.ArgumentParser(description="Claw Code Agent TUI")
    parser.add_argument("--cwd", default=".", help="Working directory")
    parser.add_argument("--model", default=None, help="Model override")
    args = parser.parse_args()

    app_cls = _load_app_class()
    if app_cls is None:
        print("Error: TUI requires 'textual' package.", file=sys.stderr)
        print(_tui_install_hint(), file=sys.stderr)
        sys.exit(1)

    app = app_cls(cwd=args.cwd, model=args.model)
    app.run()


if __name__ == "__main__":
    main()
