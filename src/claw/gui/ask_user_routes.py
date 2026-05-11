"""Ask user routes for GUI."""

from __future__ import annotations

from typing import Any, Dict

from ..ask_user_runtime import AskUserRuntime


def handle_request(handler, method: str, path: str, data: Dict[str, Any], db) -> None:
    """Handle ask-user API requests."""
    cwd = db.agent_state.cwd
    runtime = AskUserRuntime(cwd=cwd)

    if method == "GET":
        if path == "/api/ask-user" or path == "/api/ask-user/status":
            handler.send_json(runtime.get_state() or {"answers": [], "count": 0})
        elif path == "/api/ask-user/answers":
            handler.send_json({"answers": runtime.list_answers()})
        else:
            handler.send_json({"error": "Not found"}, 404)

    elif method == "POST":
        if path == "/api/ask-user/query":
            question = data.get("question", "")
            answer = runtime.find_answer(question)
            handler.send_json({"answer": answer, "question": question})
        else:
            handler.send_json({"error": "Not found"}, 404)
    else:
        handler.send_json({"error": "Method not allowed"}, 405)