"""Claw Code Agent TUI — Textual-based terminal interface.

Launch with: claw tui --cwd /path/to/project
"""

from .app import ClawTUIApp


def main():
    """Entry point for claw-tui command."""
    import argparse
    parser = argparse.ArgumentParser(description="Claw Code Agent TUI")
    parser.add_argument("--cwd", default=".", help="Working directory")
    parser.add_argument("--model", default=None, help="Model override")
    args = parser.parse_args()

    app = ClawTUIApp(cwd=args.cwd, model=args.model)
    app.run()


if __name__ == "__main__":
    main()
