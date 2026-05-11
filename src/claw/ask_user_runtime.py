"""Ask-user runtime for CodeAgent."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase


@dataclass
class AskUserAnswer:
    """A predefined Q&A answer."""
    question: str
    answer: str
    match: str = "exact"  # exact, contains, regex
    consume: bool = True  # consume after use

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "match": self.match,
            "consume": self.consume,
        }


class AskUserRuntime(RuntimeBase):
    """Ask-user predefined answers runtime.

    Discovery paths:
    - .claw-ask-user.json
    - .claude/ask-user.json
    """

    CONFIG_FILES = [".claw-ask-user.json", ".claude/ask-user.json"]

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.answers = self._discover()

    def _discover(self) -> List[AskUserAnswer]:
        """Discover ask-user configuration."""
        for filename in self.CONFIG_FILES:
            filepath = os.path.join(self.cwd, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return self._parse_answers(data)
                except (json.JSONDecodeError, OSError):
                    continue
        return []

    def _parse_answers(self, data: Dict[str, Any]) -> List[AskUserAnswer]:
        """Parse answers from configuration."""
        answers = []
        for a_data in data.get("answers", []):
            answers.append(AskUserAnswer(
                question=a_data.get("question", ""),
                answer=a_data.get("answer", ""),
                match=a_data.get("match", "exact"),
                consume=a_data.get("consume", True),
            ))
        return answers

    def find_answer(self, question: str) -> Optional[str]:
        """Find an answer for the given question."""
        for answer in self.answers:
            if answer.match == "exact" and question == answer.question:
                if answer.consume:
                    self.answers.remove(answer)
                return answer.answer
            elif answer.match == "contains" and answer.question in question:
                if answer.consume:
                    self.answers.remove(answer)
                return answer.answer
            elif answer.match == "regex":
                import re
                if re.search(answer.question, question):
                    if answer.consume:
                        self.answers.remove(answer)
                    return answer.answer
        return None

    def get_state(self) -> Dict[str, Any]:
        """Get current state."""
        return {
            "answers": [a.to_dict() for a in self.answers],
            "count": len(self.answers),
        }

    def list_answers(self) -> List[Dict[str, Any]]:
        """List all answers."""
        return [a.to_dict() for a in self.answers]

    def render_summary(self) -> str:
        """Render summary for context injection."""
        if not self.answers:
            return "No predefined answers configured."

        return f"[Predefined Q&A] {len(self.answers)} answers available"

    def get_prompt_guidance(self) -> str:
        """Get guidance for system prompt."""
        if not self.answers:
            return ""

        return "Some questions have predefined answers."