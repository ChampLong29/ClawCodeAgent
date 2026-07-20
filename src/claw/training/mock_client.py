"""Scripted model client for deterministic training demos and tests."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from .tasks import CodingTask


class ScriptedTrainingClient:
    """Minimal model-client shim that returns pre-scripted completions."""

    def __init__(self, scripted: List[Dict[str, Any]]):
        self._queue = list(scripted)
        self.model = "mock-model"

    def complete(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        if self._queue:
            return self._queue.pop(0)
        return {
            "content": "Done.",
            "tool_calls": None,
            "usage": {"input_tokens": 1, "output_tokens": 1, "model_calls": 1},
        }

    def stream(self, *args: Any, **kwargs: Any):
        yield self.complete()


def build_mock_client_factory() -> Callable[[CodingTask], ScriptedTrainingClient]:
    """Return a task-aware fake client for deterministic rollout demos.

    The mock writes the ground-truth content for normal tasks and writes an
    intentionally wrong value for tasks tagged ``negative``. This keeps the
    demo deterministic while still showing that reward/diff distinguish good
    and bad trajectories.
    """

    def factory(task: CodingTask) -> ScriptedTrainingClient:
        ground_truth = task.ground_truth_files or {}
        if not ground_truth:
            return ScriptedTrainingClient([])

        path, expected = next(iter(ground_truth.items()))
        if "negative" in (task.tags or []):
            content = expected.replace("hello", "wrong") if "hello" in expected else "WRONG\n"
        else:
            content = expected

        return ScriptedTrainingClient([
            {
                "content": "I'll create the file.",
                "tool_calls": [{
                    "id": "call_1",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": path, "content": content}),
                    },
                }],
                "usage": {"input_tokens": 50, "output_tokens": 20, "model_calls": 1},
            },
            {
                "content": "Created.",
                "tool_calls": None,
                "usage": {"input_tokens": 30, "output_tokens": 10, "model_calls": 1},
            },
        ])

    return factory
