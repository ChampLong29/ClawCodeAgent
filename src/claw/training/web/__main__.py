"""CLI entry point: ``python -m claw.training.web --results-dir ./results``."""

from __future__ import annotations

import argparse
import sys

from .app import build_app


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="claw-train-web",
        description="Browse training rollout JSONL files in a web UI.",
    )
    parser.add_argument(
        "--results-dir", "-d", required=True,
        help="Directory containing *.jsonl rollout files",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reload", action="store_true",
                        help="Enable auto-reload (development)")
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed. Run: uv sync --extra web", file=sys.stderr)
        return 1

    app = build_app(args.results_dir)
    print(f"Serving rollouts from {args.results_dir} at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
