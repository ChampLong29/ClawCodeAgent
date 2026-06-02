"""FastAPI app for the Claw training console.

Pages
-----
- ``GET /``              — index of *.jsonl files in results-dir
- ``GET /runs/{file}``   — table of rollouts in one file
- ``GET /runs/{file}/{idx}`` — single rollout detail
- ``GET /chat``          — interactive chat form
- ``GET /rollout``       — rollout-trigger form
- ``GET /export``        — SFT/RL export form
- ``POST /export``       — run export, redirect to /export?msg=...

JSON / SSE
----------
- ``GET /api/runs``                            — list jsonl files
- ``GET /api/runs/{file}``                     — list rollouts in file
- ``GET /api/runs/{file}/{idx}``               — single rollout
- ``GET /api/chat/stream?prompt=&model=&...``  — SSE: live chat
- ``GET /api/rollout/stream?suite=&mode=&...`` — SSE: live rollout

The chat / rollout SSE handlers run blocking work in a background thread
and tail the agent's session for new messages, emitting them as
``event-stream`` deltas. The browser uses ``EventSource`` to consume them.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates


TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


# ---------------------------------------------------------------------------
# Helpers — read JSONL, summarize rollouts
# ---------------------------------------------------------------------------

def _list_jsonl_files(results_dir: Path) -> List[Dict[str, Any]]:
    if not results_dir.is_dir():
        return []
    out = []
    for p in sorted(results_dir.glob("*.jsonl")):
        try:
            stat = p.stat()
            with p.open("r", encoding="utf-8") as f:
                n = sum(1 for _ in f)
            out.append({"filename": p.name, "size_bytes": stat.st_size,
                        "rollouts": n, "modified": stat.st_mtime})
        except OSError:
            continue
    return out


def _safe_filename(name: str) -> str:
    """Reject path-traversal attempts; allow alphanumerics, dot, dash, underscore."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status_code=400, detail=f"invalid filename: {name}")
    return name


def _load_jsonl(results_dir: Path, filename: str) -> List[Dict[str, Any]]:
    fp = results_dir / _safe_filename(filename)
    if not fp.is_file():
        raise HTTPException(status_code=404, detail=f"not found: {filename}")
    rows = []
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


def _sse_event(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def build_app(results_dir: str) -> FastAPI:
    app = FastAPI(title="Claw Training Console")
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    base = Path(results_dir).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)

    # ---------------- Browse ----------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(
            request, "index.html",
            {"files": _list_jsonl_files(base), "results_dir": str(base)},
        )

    @app.get("/runs/{filename}", response_class=HTMLResponse)
    def run_index(request: Request, filename: str):
        rows = _load_jsonl(base, filename)
        rollouts = [{**_summarize_rollout(r), "_index": i} for i, r in enumerate(rows)]
        rewards = [r["reward"] for r in rollouts]
        stats = {
            "count": len(rollouts),
            "avg_reward": (sum(rewards) / len(rewards)) if rewards else 0.0,
            "max_reward": max(rewards) if rewards else 0.0,
            "min_reward": min(rewards) if rewards else 0.0,
        }
        return templates.TemplateResponse(
            request, "run.html",
            {"filename": filename, "rollouts": rollouts, "stats": stats},
        )

    @app.get("/runs/{filename}/{idx}", response_class=HTMLResponse)
    def rollout_detail(request: Request, filename: str, idx: int):
        rows = _load_jsonl(base, filename)
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail=f"index {idx} out of range")
        row = rows[idx]
        return templates.TemplateResponse(
            request, "rollout_detail.html",
            {"filename": filename, "idx": idx, "row": row,
             "summary": _summarize_rollout(row)},
        )

    # ---------------- Chat ----------------

    @app.get("/chat", response_class=HTMLResponse)
    def chat_form(request: Request):
        default_model = os.environ.get("OPENAI_MODEL") or os.environ.get("ANTHROPIC_MODEL") or ""
        return templates.TemplateResponse(
            request, "chat.html", {"default_model": default_model},
        )

    @app.get("/api/chat/stream")
    def chat_stream(prompt: str, model: str = "", max_turns: int = 10, allow_shell: str = "true"):
        return StreamingResponse(
            _chat_event_stream(prompt, model, max_turns, allow_shell == "true"),
            media_type="text/event-stream",
        )

    # ---------------- Rollout ----------------

    @app.get("/rollout", response_class=HTMLResponse)
    def rollout_form(request: Request):
        return templates.TemplateResponse(
            request, "rollout.html",
            {
                "results_dir": str(base),
                "default_suite": _find_default_suite(),
                "now": datetime.now().strftime("%Y%m%d_%H%M%S"),
            },
        )

    @app.get("/api/rollout/stream")
    def rollout_stream(suite: str, output: str, mode: str = "mock",
                       model: str = "", repeats: int = 1):
        return StreamingResponse(
            _rollout_event_stream(base, suite, output, mode, model, repeats),
            media_type="text/event-stream",
        )

    # ---------------- Export ----------------

    @app.get("/export", response_class=HTMLResponse)
    def export_form(request: Request, msg: str = "", msg_kind: str = "success",
                    source: str = ""):
        return templates.TemplateResponse(
            request, "export.html",
            {
                "results_dir": str(base),
                "files": _list_jsonl_files(base),
                "msg": msg,
                "msg_kind": msg_kind,
                "selected_source": source,
            },
        )

    @app.post("/export")
    def export_run(
        source: str = Form(...),
        dataset_type: str = Form(...),
        min_reward: float = Form(0.8),
        domain: str = Form("cli-tool"),
        output: str = Form(...),
    ):
        try:
            n, out_path = _run_export(base, source, dataset_type, min_reward, domain, output)
            kind = "success"
            msg = (f"已导出 <strong>{n}</strong> 条样本"
                   f"（{dataset_type.upper()}）至 <code>{out_path.name}</code>。"
                   f'<a href="/runs/{out_path.name}">查看</a>')
        except Exception as e:
            kind = "error"
            msg = f"导出失败：{e}"
        return RedirectResponse(
            url=f"/export?msg={msg}&msg_kind={kind}",
            status_code=303,
        )

    # ---------------- JSON API ----------------

    @app.get("/api/runs")
    def api_list_runs():
        return {"results_dir": str(base), "files": _list_jsonl_files(base)}

    @app.get("/api/runs/{filename}")
    def api_run_summary(filename: str):
        rows = _load_jsonl(base, filename)
        return {"filename": filename, "count": len(rows),
                "rollouts": [_summarize_rollout(r) for r in rows]}

    @app.get("/api/runs/{filename}/{idx}")
    def api_rollout(filename: str, idx: int):
        rows = _load_jsonl(base, filename)
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="index out of range")
        return JSONResponse(rows[idx])

    return app


# ---------------------------------------------------------------------------
# Chat SSE — runs LocalCodingAgent in a thread, tails session for new messages
# ---------------------------------------------------------------------------

def _chat_event_stream(prompt: str, model: str, max_turns: int, allow_shell: bool):
    # Lazy import — avoid pulling LocalCodingAgent into web app on startup
    try:
        from ...agent_runtime import LocalCodingAgent
        from ...agent_types import ModelConfig, BudgetConfig, AgentPermissions
    except ImportError as e:
        yield _sse_event({"type": "error", "error": f"agent runtime import failed: {e}"})
        return

    sandbox_dir = tempfile.mkdtemp(prefix="claw_chat_")
    try:
        model_config = ModelConfig(name=model) if model else ModelConfig()
        permissions = AgentPermissions(
            allow_write=True, allow_shell=allow_shell,
        )
        agent = LocalCodingAgent(
            cwd=sandbox_dir,
            model_config=model_config,
            budget=BudgetConfig(max_total_tokens=80000, max_model_calls=max_turns + 2),
            permissions=permissions.to_dict(),
        )

        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, Any] = {}

        def runner():
            try:
                result_holder["result"] = agent.run(
                    prompt=prompt, max_turns=max_turns, stream=False,
                )
            except Exception as e:
                error_holder["error"] = str(e)
                error_holder["trace"] = traceback.format_exc()

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

        # Tail session for new messages
        last_seen = 0
        while thread.is_alive() or last_seen < _msg_count(agent):
            msgs = agent.session.get_messages() if agent.session else []
            for m in msgs[last_seen:]:
                yield _sse_event({"type": "message", "msg": m})
            last_seen = len(msgs)
            if not thread.is_alive():
                break
            time.sleep(0.25)

        thread.join(timeout=2.0)

        # Final flush of any messages that arrived in the last window
        msgs = agent.session.get_messages() if agent.session else []
        for m in msgs[last_seen:]:
            yield _sse_event({"type": "message", "msg": m})

        if error_holder:
            yield _sse_event({"type": "error", "error": error_holder.get("error", "unknown")})
            return

        result = result_holder.get("result")
        yield _sse_event({
            "type": "done",
            "stop_reason": result.stop_reason if result else "unknown",
            "usage": result.usage.to_dict() if result and result.usage else {},
        })
    finally:
        # Don't auto-delete sandbox — agent may have created files user wants to inspect.
        # In practice, /tmp gets cleaned up by the OS.
        pass


def _msg_count(agent) -> int:
    try:
        return len(agent.session.get_messages()) if agent.session else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Rollout SSE — runs RolloutRunner; mock or real
# ---------------------------------------------------------------------------

def _rollout_event_stream(results_dir: Path, suite_path: str, output: str,
                          mode: str, model: str, repeats: int):
    output = _safe_filename(output)
    out_path = results_dir / output
    try:
        from ..tasks import TaskSuite
        from ..sandbox import SandboxManager
        from ..agent_env import AgentEnv
        from ..runner import RolloutRunner, RolloutResult
        from ..determinism import DeterministicConfig
    except ImportError as e:
        yield _sse_event({"type": "error", "error": f"training import failed: {e}"})
        return

    # Resolve suite path
    suite_fp = Path(suite_path).expanduser()
    if not suite_fp.is_absolute():
        suite_fp = (Path.cwd() / suite_fp).resolve()
    if not suite_fp.is_file():
        yield _sse_event({"type": "error", "error": f"suite not found: {suite_fp}"})
        return

    try:
        suite = TaskSuite.load_from_json(str(suite_fp))
    except Exception as e:
        yield _sse_event({"type": "error", "error": f"suite parse error: {e}"})
        return

    # Build expanded task list (with repeats)
    tasks = []
    for task in suite.tasks:
        for r in range(repeats):
            tasks.append((task, f"{task.id}__r{r}" if repeats > 1 else task.id))

    total = len(tasks)
    yield _sse_event({"type": "start", "total": total, "mode": mode})

    # Prepare mock script if needed
    mock_script = None
    if mode == "mock":
        mock_script = _build_mock_client_factory()

    results: List[Any] = []
    for idx, (task, run_id) in enumerate(tasks):
        yield _sse_event({"type": "task_start", "idx": idx, "total": total,
                          "task_id": run_id})
        try:
            mgr = SandboxManager()
            det = DeterministicConfig(
                temperature=0.0, session_id=f"rollout/{run_id}",
            )
            env = AgentEnv(
                sandbox_manager=mgr, deterministic=det,
                model_name=model or "",
            )
            env.reset(task)

            if mode == "mock":
                # Inject the scripted fake client AFTER agent construction
                env._agent.client = mock_script(task)

            obs, reward, done, info = env.step()
            r = RolloutResult(
                task_id=run_id,
                session_id=obs.session_id,
                stop_reason=obs.stop_reason,
                reward=reward,
                messages=obs.messages,
                usage=info.get("usage", {}),
                test_result=info.get("test_result"),
                diff_result=info.get("diff_result"),
            )
            results.append(r)
            tr = info.get("test_result") or {}
            dr = info.get("diff_result") or {}
            yield _sse_event({
                "type": "task_done",
                "idx": idx,
                "total": total,
                "task_id": run_id,
                "reward": reward,
                "stop_reason": obs.stop_reason,
                "tests": f"{tr.get('passed_tests',0)}/{tr.get('total_tests',0)}",
                "diff": f"{dr.get('matches',0)}/{dr.get('total',0)}",
            })
            env.close()
        except Exception as e:
            tb = traceback.format_exc()
            yield _sse_event({
                "type": "task_done",
                "idx": idx,
                "total": total,
                "task_id": run_id,
                "reward": 0.0,
                "stop_reason": "error",
                "tests": "0/0", "diff": "0/0",
                "error": f"{e}\n{tb[-400:]}",
            })

    # Persist
    try:
        runner = RolloutRunner()
        runner.export_to_jsonl(results, str(out_path))
        summary = runner.summary(results)
    except Exception as e:
        yield _sse_event({"type": "error", "error": f"export failed: {e}"})
        return

    yield _sse_event({
        "type": "done",
        "output_path": str(out_path),
        "output_filename": out_path.name,
        "summary": summary,
    })


def _build_mock_client_factory():
    """Returns a function task -> FakeOpenAIClient.

    The mock writes the *correct* ground_truth content for the task
    (so reward is high), unless the task has the 'negative' tag in which
    case it writes a wrong content (so reward is low). This mimics a
    flywheel where the model usually succeeds but sometimes fails.
    """
    import json as _json

    class FakeClient:
        def __init__(self, scripted):
            self._queue = list(scripted)
            self.model = "mock-model"
        def complete(self, *args, **kwargs):
            if self._queue:
                return self._queue.pop(0)
            return {"content": "Done.", "tool_calls": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1, "model_calls": 1}}
        def stream(self, *args, **kwargs):
            yield self.complete()

    def factory(task):
        # Determine target filename + content
        gt = task.ground_truth_files or {}
        if not gt:
            # No ground truth — model just declares done
            return FakeClient([])
        path, expected = next(iter(gt.items()))
        # Write wrong content for "negative" tagged tasks
        if "negative" in (task.tags or []):
            content = expected.replace("hello", "wrong") if "hello" in expected else "WRONG\n"
        else:
            content = expected
        return FakeClient([
            {
                "content": "I'll create the file.",
                "tool_calls": [{
                    "id": "call_1",
                    "function": {
                        "name": "write_file",
                        "arguments": _json.dumps({"path": path, "content": content}),
                    },
                }],
                "usage": {"input_tokens": 50, "output_tokens": 20, "model_calls": 1},
            },
            {"content": "Created.", "tool_calls": None,
             "usage": {"input_tokens": 30, "output_tokens": 10, "model_calls": 1}},
        ])
    return factory


# ---------------------------------------------------------------------------
# Export — call SlimeDataAdapter
# ---------------------------------------------------------------------------

def _run_export(results_dir: Path, source: str, dataset_type: str,
                min_reward: float, domain: str, output: str):
    source = _safe_filename(source)
    output = _safe_filename(output)
    src_path = results_dir / source
    out_path = results_dir / output
    if not src_path.is_file():
        raise FileNotFoundError(f"source not found: {source}")

    from ..slime_adapter import SlimeDataAdapter

    rows = []
    with src_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    # Convert raw rollouts -> slime samples (only if they don't already look like samples)
    samples = []
    for r in rows:
        if "prompt" in r and "response" in r:
            # Already in slime sample format
            samples.append(r)
        else:
            sample = SlimeDataAdapter.to_slime_sample(
                r.get("messages", []),
                reward=float(r.get("reward", 0.0)),
                task_id=r.get("task_id", ""),
                domain=domain,
            ).to_dict()
            samples.append(sample)

    if dataset_type == "sft":
        n = SlimeDataAdapter.export_sft_dataset(samples, str(out_path), min_reward=min_reward)
    elif dataset_type == "rl":
        n = SlimeDataAdapter.export_rl_dataset(samples, str(out_path))
    else:
        raise ValueError(f"unknown dataset_type: {dataset_type}")

    return n, out_path


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def _find_default_suite() -> str:
    """Best-effort: locate examples/training/sample_suite.json from cwd."""
    candidates = [
        Path.cwd() / "examples" / "training" / "sample_suite.json",
        Path(__file__).resolve().parents[4] / "examples" / "training" / "sample_suite.json",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return "examples/training/sample_suite.json"
