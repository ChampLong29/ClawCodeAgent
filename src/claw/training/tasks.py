"""Task definitions and task suite management for agent training."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CodingTask:
    """A single coding task for agent training/evaluation.

    Tasks represent real software engineering problems: fix a bug, add a
    feature, refactor code, write tests, or implement from a specification.
    """
    id: str
    prompt: str                           # Natural language task description
    type: str = "add_feature"              # fix_bug | add_feature | refactor | write_tests | implement_from_spec
    difficulty: str = "easy"              # easy | medium | hard
    ground_truth_files: Dict[str, str] = field(default_factory=dict)  # path → expected content
    test_commands: List[str] = field(default_factory=list)  # Shell commands to verify solution
    expected_output: Optional[str] = None # Expected stdout from test_commands
    template_dir: Optional[str] = None    # Path to sandbox template directory
    tags: List[str] = field(default_factory=list)  # For curriculum learning and filtering
    max_turns: int = 50
    timeout_seconds: float = 300.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "type": self.type,
            "difficulty": self.difficulty,
            "ground_truth_files": self.ground_truth_files,
            "test_commands": self.test_commands,
            "expected_output": self.expected_output,
            "template_dir": self.template_dir,
            "tags": self.tags,
            "max_turns": self.max_turns,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CodingTask:
        return cls(
            id=data.get("id", ""),
            prompt=data.get("prompt", ""),
            type=data.get("type", "add_feature"),
            difficulty=data.get("difficulty", "easy"),
            ground_truth_files=data.get("ground_truth_files", {}),
            test_commands=data.get("test_commands", []),
            expected_output=data.get("expected_output"),
            template_dir=data.get("template_dir"),
            tags=data.get("tags", []),
            max_turns=data.get("max_turns", 50),
            timeout_seconds=data.get("timeout_seconds", 300.0),
        )


class TaskSuite:
    """Collection of CodingTask with metadata, loading, and filtering."""

    def __init__(self, tasks: Optional[List[CodingTask]] = None):
        self.tasks: List[CodingTask] = tasks or []
        self._metadata: Dict[str, Any] = {}

    @classmethod
    def load_from_json(cls, path: str) -> TaskSuite:
        """Load a task suite from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        tasks = [CodingTask.from_dict(t) for t in data.get("tasks", [])]
        suite = cls(tasks)
        suite._metadata = {k: v for k, v in data.items() if k != "tasks"}
        return suite

    def save_to_json(self, path: str) -> None:
        """Save the task suite to a JSON file."""
        data = {"tasks": [t.to_dict() for t in self.tasks], **self._metadata}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_tasks_by_difficulty(self, difficulty: str) -> List[CodingTask]:
        """Filter tasks by difficulty level."""
        return [t for t in self.tasks if t.difficulty == difficulty]

    def get_tasks_by_type(self, task_type: str) -> List[CodingTask]:
        """Filter tasks by type."""
        return [t for t in self.tasks if t.type == task_type]

    def get_tasks_by_tag(self, tag: str) -> List[CodingTask]:
        """Filter tasks by tag."""
        return [t for t in self.tasks if tag in t.tags]

    def get_curriculum(self) -> List[CodingTask]:
        """Get tasks ordered for curriculum learning (easy → hard)."""
        difficulty_order = {"easy": 0, "medium": 1, "hard": 2}
        return sorted(self.tasks, key=lambda t: (difficulty_order.get(t.difficulty, 99), t.id))

    def get_task_by_id(self, task_id: str) -> Optional[CodingTask]:
        """Get a single task by ID."""
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def add_task(self, task: CodingTask) -> None:
        """Add a task to the suite."""
        self.tasks.append(task)

    def __len__(self) -> int:
        return len(self.tasks)

    def __iter__(self):
        return iter(self.tasks)

    def summary(self) -> Dict[str, Any]:
        """Get a summary of the task suite."""
        types = {}
        difficulties = {}
        for t in self.tasks:
            types[t.type] = types.get(t.type, 0) + 1
            difficulties[t.difficulty] = difficulties.get(t.difficulty, 0) + 1

        return {
            "total_tasks": len(self.tasks),
            "by_type": types,
            "by_difficulty": difficulties,
            "tags": list(set(tag for t in self.tasks for tag in t.tags)),
        }
