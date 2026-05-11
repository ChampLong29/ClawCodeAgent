"""GUI entry point for CodeAgent."""

from __future__ import annotations

import argparse
import sys


def main(argv=None):
    """Main GUI entry point."""
    from .server import run_server
    parser = argparse.ArgumentParser(
        description="Claw Code Agent GUI",
        prog="python3 -m claw.gui",
    )
    parser.add_argument("--cwd", default=".", help="Working directory")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind to")
    parser.add_argument("--stream", action="store_true", help="Enable streaming")

    args = parser.parse_args(argv)

    run_server(
        cwd=args.cwd,
        host=args.host,
        port=args.port,
        stream=args.stream,
    )


if __name__ == "__main__":
    main()