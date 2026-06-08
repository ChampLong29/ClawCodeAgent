"""Claw Code Agent TUI — Textual-based terminal interface.

Launch with: claw tui --cwd /path/to/project
"""

import os
import sys

# Module-level attempt — if textual is missing, ClawTUIApp will be None.
# cmd_tui in main.py and the claw-tui entry point both handle this gracefully.
try:
    from .app import ClawTUIApp
except ImportError:
    ClawTUIApp = None  # type: ignore[assignment]


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

    if ClawTUIApp is None:
        print("Error: TUI requires 'textual' package.", file=sys.stderr)
        print(_tui_install_hint(), file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Claw Code Agent TUI")
    parser.add_argument("--cwd", default=".", help="Working directory")
    parser.add_argument("--model", default=None, help="Model override")
    args = parser.parse_args()

    app = ClawTUIApp(cwd=args.cwd, model=args.model)
    app.run()


if __name__ == "__main__":
    main()
