"""Lightweight web frontend for browsing training rollouts.

Reads JSONL files produced by ``RolloutRunner.export_to_jsonl`` and renders
them as a sortable index plus per-rollout detail pages. Run with::

    claw train-web --results-dir /path/to/results --port 8080

This module is OPTIONAL — install with ``uv sync --extra web``.
"""

from .app import build_app

__all__ = ["build_app"]
