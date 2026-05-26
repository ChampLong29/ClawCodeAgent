"""Sequential single-question interaction runtime.

The runtime — not the agent — controls question pacing.  The agent
generates the question list once; afterwards the runtime drives the
one-at-a-time interaction with back/forward navigation.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .hook_policy import RuntimeBase
from .session_naming import make_session_id

if TYPE_CHECKING:
    from .agent_runtime import LocalCodingAgent


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Question:
    """A single question in a managed questionnaire."""

    id: str                          # "q1"
    text: str                        # question text
    answer: Optional[str] = None
    status: str = "pending"          # pending | answered | skipped

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "answer": self.answer,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Question:
        return cls(
            id=data.get("id", ""),
            text=data.get("text", ""),
            answer=data.get("answer"),
            status=data.get("status", "pending"),
        )


@dataclass
class Questionnaire:
    """A managed questionnaire with sequential interaction."""

    session_id: str
    overall_goal: str
    questions: List[Question] = field(default_factory=list)
    current_question_index: int = 0
    status: str = "awaiting_generation"  # awaiting_generation | active | completed
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self):
        if self.created_at == 0.0:
            self.created_at = time.time()
        if self.updated_at == 0.0:
            self.updated_at = time.time()

    def get_current_question(self) -> Optional[Question]:
        if 0 <= self.current_question_index < len(self.questions):
            return self.questions[self.current_question_index]
        return None

    def all_answered(self) -> bool:
        return all(q.status in ("answered", "skipped") for q in self.questions)

    def progress(self) -> Dict[str, Any]:
        total = len(self.questions)
        answered = sum(1 for q in self.questions if q.status == "answered")
        skipped = sum(1 for q in self.questions if q.status == "skipped")
        pending = total - answered - skipped
        return {
            "total": total,
            "answered": answered,
            "skipped": skipped,
            "pending": pending,
            "current_index": self.current_question_index,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "overall_goal": self.overall_goal,
            "questions": [q.to_dict() for q in self.questions],
            "current_question_index": self.current_question_index,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Questionnaire:
        return cls(
            session_id=data.get("session_id", ""),
            overall_goal=data.get("overall_goal", ""),
            questions=[Question.from_dict(q) for q in data.get("questions", [])],
            current_question_index=data.get("current_question_index", 0),
            status=data.get("status", "awaiting_generation"),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
        )


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

QUESTIONNAIRE_GENERATOR_PROMPT = """You are helping to gather requirements for a software project.

## Project Goal
{goal}

## Constraints
{constraints}

## Instructions
Generate a list of 5-10 clarifying questions that will help define the
requirements more precisely. Each question should be specific and
actionable. Output as a JSON array:

```json
[
  {{"id": "q1", "text": "Who are the primary users of this application?"}},
  {{"id": "q2", "text": "What platforms need to be supported?"}}
]
```

Rules:
- Each question must have a unique id (q1, q2, ...).
- Questions should cover: users, platforms, core features, constraints, integrations.
- Only output the JSON array — no other text.
"""


class QuestionnaireRuntime(RuntimeBase):
    """Runtime-managed sequential single-question interaction.

    The runtime drives the interaction; the agent generates the question
    list once and is not consulted again during the questionnaire.

    State machine::

        AWAITING_GENERATION  →  ACTIVE  →  COMPLETED
    """

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.questionnaire: Optional[Questionnaire] = None
        self._sessions_dir = os.path.join(cwd, ".port_sessions", "questionnaire")
        os.makedirs(self._sessions_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start(self, goal: str, constraints: str = "") -> Questionnaire:
        """Create a new questionnaire session (AWAITING_GENERATION)."""
        session_id = make_session_id(goal, "qa")
        self.questionnaire = Questionnaire(
            session_id=session_id,
            overall_goal=goal,
            status="awaiting_generation",
        )
        self.questionnaire.metadata = {"constraints": constraints}  # type: ignore[attr-defined]
        self.save()
        return self.questionnaire

    def load(self, session_id: str) -> Optional[Questionnaire]:
        path = self._session_path(session_id)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.questionnaire = Questionnaire.from_dict(data)
        return self.questionnaire

    def save(self) -> None:
        if not self.questionnaire:
            return
        self.questionnaire.updated_at = time.time()
        path = self._session_path(self.questionnaire.session_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.questionnaire.to_dict(), f, indent=2,
                      ensure_ascii=False)

    def _session_path(self, session_id: str) -> str:
        return os.path.join(self._sessions_dir, f"{session_id}.json")

    # ------------------------------------------------------------------
    # Question generation (agent consulted ONCE)
    # ------------------------------------------------------------------

    def generate_questions(
        self, agent: "LocalCodingAgent", constraints: str = ""
    ) -> List[Question]:
        """Ask the agent to generate the question list.

        The agent is consulted exactly once.  After this call the runtime
        owns the interaction.
        """
        if not self.questionnaire:
            raise RuntimeError("No active questionnaire. Call start() first.")

        prompt = QUESTIONNAIRE_GENERATOR_PROMPT.format(
            goal=self.questionnaire.overall_goal,
            constraints=constraints or "None",
        )

        result = agent.run(prompt=prompt, stream=False)
        raw = result.final_message or ""

        # Parse JSON from agent output
        questions_data = self._parse_questions_json(raw)
        self.questionnaire.questions = [
            Question.from_dict(q) for q in questions_data
        ]
        self.questionnaire.status = "active"
        self.questionnaire.current_question_index = 0
        self.save()
        return self.questionnaire.questions

    def _parse_questions_json(self, raw: str) -> List[Dict[str, Any]]:
        import re
        # Try direct parse
        try:
            data = json.loads(raw.strip())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
        # Try markdown code block
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
        if m:
            try:
                data = json.loads(m.group(1).strip())
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
        # Try to find a JSON array anywhere
        m = re.search(r'\[[\s\S]*\]', raw)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
        return []

    # ------------------------------------------------------------------
    # Navigation & answering
    # ------------------------------------------------------------------

    def get_current_question(self) -> Optional[Question]:
        if not self.questionnaire:
            return None
        return self.questionnaire.get_current_question()

    def has_more(self) -> bool:
        if not self.questionnaire:
            return False
        return self.questionnaire.status == "active"

    def answer_current(self, answer: str) -> None:
        """Record an answer and advance to the next question."""
        q = self.get_current_question()
        if not q:
            return
        if answer.strip():
            q.answer = answer.strip()
            q.status = "answered"
        else:
            q.status = "skipped"
        self.questionnaire.current_question_index += 1  # type: ignore[union-attr]
        self._check_completion()
        self.save()

    def skip_current(self) -> None:
        """Skip the current question."""
        q = self.get_current_question()
        if q:
            q.status = "skipped"
        self.questionnaire.current_question_index += 1  # type: ignore[union-attr]
        self._check_completion()
        self.save()

    def go_back(self) -> Optional[Question]:
        """Navigate to the previous question."""
        if not self.questionnaire:
            return None
        if self.questionnaire.current_question_index > 0:
            self.questionnaire.current_question_index -= 1
            self.save()
        return self.get_current_question()

    def go_to(self, index: int) -> Optional[Question]:
        """Jump to a specific question (0-based)."""
        if not self.questionnaire:
            return None
        if 0 <= index < len(self.questionnaire.questions):
            self.questionnaire.current_question_index = index
            self.save()
        return self.get_current_question()

    def revise_answer(self, index: int, new_answer: str) -> None:
        """Revise an already-answered question without moving position."""
        if not self.questionnaire:
            return
        if 0 <= index < len(self.questionnaire.questions):
            q = self.questionnaire.questions[index]
            q.answer = new_answer.strip() if new_answer.strip() else None
            q.status = "answered" if new_answer.strip() else "pending"
            self.save()

    def _check_completion(self) -> None:
        if not self.questionnaire:
            return
        if self.questionnaire.current_question_index >= len(self.questionnaire.questions):
            self.questionnaire.status = "completed"

    # ------------------------------------------------------------------
    # Finalize
    # ------------------------------------------------------------------

    def finalize(self) -> str:
        """Compile all answers into a structured Markdown document."""
        if not self.questionnaire:
            return ""

        lines = [
            f"# Requirements Gathering Results\n",
            f"**Goal**: {self.questionnaire.overall_goal}\n",
            "## Questions & Answers\n",
        ]

        for q in self.questionnaire.questions:
            status_icon = "✓" if q.status == "answered" else "✗"
            answer_text = q.answer or "(no answer)"
            lines.append(f"### {status_icon} {q.text}")
            lines.append(f"{answer_text}\n")

        lines.append("---")
        progress = self.questionnaire.progress()
        lines.append(
            f"*{progress['answered']} answered, "
            f"{progress['skipped']} skipped, "
            f"{progress['total']} total*"
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # RuntimeBase
    # ------------------------------------------------------------------

    def get_state(self) -> Optional[Dict[str, Any]]:
        if not self.questionnaire:
            return None
        return self.questionnaire.to_dict()

    def render_summary(self) -> str:
        if not self.questionnaire:
            return "[Questionnaire] No active session"
        p = self.questionnaire.progress()
        return (
            f"[Questionnaire] Q{p['current_index'] + 1}/{p['total']} "
            f"({p['answered']} answered, {p['skipped']} skipped)"
        )

    def get_prompt_guidance(self) -> str:
        if not self.questionnaire:
            return ""
        if self.questionnaire.status != "active":
            return ""
        q = self.get_current_question()
        if not q:
            return ""
        return (
            f"[Questionnaire] Current question: **{q.text}**\n"
            f"Wait for the user's answer before proceeding."
        )
