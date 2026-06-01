"""FastAPI app for browsing training rollouts.

Routes
------
- ``GET /``                              — index of JSONL files in results-dir
- ``GET /runs/{filename}``               — table of rollouts in one file
- ``GET /runs/{filename}/{idx}``         — single rollout detail (messages + reward)
- ``GET /api/runs``                      — JSON list of files
- ``GET /api/runs/{filename}``           — JSON list of rollouts
- ``GET /api/runs/{filename}/{idx}``     — JSON for one rollout

The app is read-only: it never modifies files in ``results_dir``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates


TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _list_jsonl_files(results_dir: Path) -> List[Dict[str, Any]]:
    """Return metadata for every *.jsonl file in results_dir."""
    if not results_dir.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(results_dir.glob("*.jsonl")):
        try:
            stat = p.stat()
            # Count lines cheaply
            with p.open("r", encoding="utf-8") as f:
                n = sum(1 for _ in f)
            out.append({
                "filename": p.name,
                "size_bytes": stat.st_size,
                "rollouts": n,
                "modified": stat.st_mtime,
            })
        except OSError:
            continue
    return out


def _load_jsonl(results_dir: Path, filename: str) -> List[Dict[str, Any]]:
    """Load and parse a single JSONL file. Raises 404 if missing."""
    # Defensive: prevent path traversal
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="invalid filename")
    fp = results_dir / filename
    if not fp.is_file():
        raise HTTPException(status_code=404, detail=f"not found: {filename}")
    rows: List[Dict[str, Any]] = []
    with fp.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _summarize_rollout(row: Dict[str, Any]) -> Dict[str, Any]:
    """Project a rollout into the columns shown in the index table."""
    test = row.get("test_result") or {}
    diff = row.get("diff_result") or {}
    usage = row.get("usage") or {}
    return {
        "task_id": row.get("task_id", ""),
        "session_id": row.get("session_id", ""),
        "stop_reason": row.get("stop_reason", ""),
        "reward": row.get("reward", 0.0),
        "test_passed": f"{test.get('passed_tests', 0)}/{test.get('total_tests', 0)}",
        "diff_matches": f"{diff.get('matches', 0)}/{diff.get('total', 0)}",
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "messages_count": len(row.get("messages") or []),
        "execution_time": row.get("execution_time", 0.0),
        "error": row.get("error"),
    }


def build_app(results_dir: str) -> FastAPI:
    """Construct the FastAPI app bound to ``results_dir``."""
    app = FastAPI(title="Claw Training Rollouts")
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    base = Path(results_dir).expanduser().resolve()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        files = _list_jsonl_files(base)
        return templates.TemplateResponse(
            request, "index.html",
            {"files": files, "results_dir": str(base)},
        )

    @app.get("/runs/{filename}", response_class=HTMLResponse)
    def run_index(request: Request, filename: str):
        rows = _load_jsonl(base, filename)
        rollouts = [
            {**_summarize_rollout(r), "_index": i}
            for i, r in enumerate(rows)
        ]
        # Aggregate stats
        rewards = [r["reward"] for r in rollouts]
        stats = {
            "count": len(rollouts),
            "avg_reward": (sum(rewards) / len(rewards)) if rewards else 0.0,
            "max_reward": max(rewards) if rewards else 0.0,
            "min_reward": min(rewards) if rewards else 0.0,
        }
        return templates.TemplateResponse(
            request, "run.html",
            {
                "filename": filename,
                "rollouts": rollouts,
                "stats": stats,
            },
        )

    @app.get("/runs/{filename}/{idx}", response_class=HTMLResponse)
    def rollout_detail(request: Request, filename: str, idx: int):
        rows = _load_jsonl(base, filename)
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail=f"index {idx} out of range")
        row = rows[idx]
        return templates.TemplateResponse(
            request, "rollout.html",
            {
                "filename": filename,
                "idx": idx,
                "row": row,
                "summary": _summarize_rollout(row),
            },
        )

    # ---------------- JSON API (for future frontend reuse) ----------------

    @app.get("/api/runs")
    def api_list_runs():
        return {"results_dir": str(base), "files": _list_jsonl_files(base)}

    @app.get("/api/runs/{filename}")
    def api_run_summary(filename: str):
        rows = _load_jsonl(base, filename)
        return {
            "filename": filename,
            "count": len(rows),
            "rollouts": [_summarize_rollout(r) for r in rows],
        }

    @app.get("/api/runs/{filename}/{idx}")
    def api_rollout(filename: str, idx: int):
        rows = _load_jsonl(base, filename)
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="index out of range")
        return JSONResponse(rows[idx])

    return app
