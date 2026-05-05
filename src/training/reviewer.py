"""Independent Reviewer Agent for code quality assessment.

The Reviewer uses a separate AgentSession to prevent self-assessment
bias — the work agent never reviews its own code.  This gives cleaner
RL reward signals by capturing dimensions that automated tests miss
(security, code quality, architecture, etc.).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..agent_runtime import LocalCodingAgent
    from ..agent_types import ModelConfig


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ReviewIssue:
    """A single issue found during review."""

    severity: str            # "critical" | "major" | "minor"
    dimension: str           # "security" | "quality" | "performance" | ...
    file_path: str           # affected file (empty if global)
    description: str         # what the issue is
    suggestion: str          # how to fix it

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "dimension": self.dimension,
            "file_path": self.file_path,
            "description": self.description,
            "suggestion": self.suggestion,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReviewIssue:
        return cls(
            severity=data.get("severity", "minor"),
            dimension=data.get("dimension", ""),
            file_path=data.get("file_path", ""),
            description=data.get("description", ""),
            suggestion=data.get("suggestion", ""),
        )


@dataclass
class ReviewScore:
    """Score for one review dimension."""

    score: float             # 0.0 - 1.0
    comment: str             # rationale

    def to_dict(self) -> Dict[str, Any]:
        return {"score": self.score, "comment": self.comment}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReviewScore:
        return cls(
            score=float(data.get("score", 0)),
            comment=data.get("comment", ""),
        )


@dataclass
class ReviewReport:
    """Structured code review output from ReviewerAgent."""

    overall_score: float
    dimensions: Dict[str, ReviewScore]
    issues: List[ReviewIssue] = field(default_factory=list)
    summary: str = ""

    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "critical")

    def major_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "major")

    def minor_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "minor")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "dimensions": {
                k: v.to_dict() for k, v in self.dimensions.items()
            },
            "issues": [i.to_dict() for i in self.issues],
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReviewReport:
        return cls(
            overall_score=float(data.get("overall_score", 0)),
            dimensions={
                k: ReviewScore.from_dict(v)
                for k, v in data.get("dimensions", {}).items()
            },
            issues=[ReviewIssue.from_dict(i)
                    for i in data.get("issues", [])],
            summary=data.get("summary", ""),
        )

    @staticmethod
    def empty() -> ReviewReport:
        """Return a neutral report (used when reviewer is unavailable)."""
        return ReviewReport(
            overall_score=0.5,
            dimensions={},
            summary="Review skipped (reviewer unavailable).",
        )


# ---------------------------------------------------------------------------
# Reviewer prompt
# ---------------------------------------------------------------------------

REVIEW_PROMPT = """You are a strict code reviewer.  Evaluate the following work
output against the criteria below.  Be critical — flag every issue,
even minor ones.  Your review will be used as a training signal.

## Review Criteria
{criteria}

## Work Output

### Task
{task_prompt}

### Written Files
{files_section}

### Test Results
{test_results}

### Architecture (if available)
{architecture}

## Instructions

Output a JSON object with this structure:

```json
{{
  "overall_score": 0.85,
  "dimensions": {{
    "security": {{"score": 0.9, "comment": "No SQL injection, good input validation"}},
    "code_quality": {{"score": 0.8, "comment": "Readable but some functions are too long"}}
  }},
  "issues": [
    {{
      "severity": "major",
      "dimension": "code_quality",
      "file_path": "src/models.py",
      "description": "The User model lacks a unique constraint on email",
      "suggestion": "Add unique=True to the email column"
    }}
  ],
  "summary": "Overall good implementation with minor quality issues."
}}
```

**Rules**:
- overall_score: 0.0 to 1.0
- dimensions: score each criterion (0.0 to 1.0) with a brief comment
- issues: every problem found (empty list if perfect)
- severity: "critical" (security/data-loss), "major" (functional/design), "minor" (style/naming)
- Only output the JSON — no other text."""


# ---------------------------------------------------------------------------
# ReviewerAgent
# ---------------------------------------------------------------------------

DEFAULT_CRITERIA = [
    "**security**: SQL injection, XSS, auth bypass, data exposure — any "
    "exploitable vulnerability is critical.",

    "**code_quality**: Readability, naming, function length, DRY, "
    "SOLID principles, error handling completeness.",

    "**performance**: N+1 queries, unnecessary allocations, missing "
    "indexes, blocking I/O on the event loop.",

    "**test_coverage**: Edge cases, error paths, boundary conditions, "
    "integration scenarios — what tests are missing?",

    "**architecture**: Does the design follow the recommended patterns "
    "for this domain?  Are there unnecessary abstractions?",

    "**completeness**: Does the implementation fully satisfy the task "
    "requirements?  Are there any missing pieces?",
]


class ReviewerAgent:
    """Independent agent for code review and quality assessment.

    Uses a **separate** ``LocalCodingAgent`` instance with its own
    ``AgentSession`` to prevent self-assessment bias and context
    pollution.
    """

    def __init__(
        self,
        model_config: "ModelConfig",
        cwd: str = ".",
        criteria: Optional[List[str]] = None,
    ):
        self.model_config = model_config
        self.cwd = cwd
        self.criteria = criteria or DEFAULT_CRITERIA

    def review(
        self,
        task_prompt: str,
        files: Dict[str, str],
        test_results: str = "",
        architecture: str = "",
        agent_factory: Optional[callable] = None,  # type: ignore[assignment]
    ) -> ReviewReport:
        """Review work output and return a structured report.

        Args:
            task_prompt: The original task description.
            files: Dict of ``{relative_path: content}`` written by the
                   work agent.
            test_results: stdout/stderr from running test_commands.
            architecture: Architecture document (optional).
            agent_factory: Callable that returns a fresh
                           ``LocalCodingAgent``.  If ``None``, creates
                           one from *model_config*.

        Returns:
            Structured ``ReviewReport``.
        """
        # Build the files section
        files_parts: List[str] = []
        for path, content in files.items():
            # Truncate very long files
            snippet = content if len(content) < 3000 else content[:3000] + "\n... [truncated]"
            files_parts.append(f"**{path}**:\n```\n{snippet}\n```")
        files_section = "\n\n".join(files_parts) if files_parts else "(no files written)"

        criteria_text = "\n".join(f"- {c}" for c in self.criteria)
        prompt = REVIEW_PROMPT.format(
            criteria=criteria_text,
            task_prompt=task_prompt,
            files_section=files_section,
            test_results=test_results or "(no test results)",
            architecture=architecture or "(no architecture document)",
        )

        try:
            if agent_factory:
                agent = agent_factory()
            else:
                from ..agent_runtime import LocalCodingAgent
                agent = LocalCodingAgent(
                    cwd=self.cwd,
                    model_config=self.model_config,
                )
        except Exception:
            return ReviewReport.empty()

        try:
            result = agent.run(prompt=prompt, stream=False, max_turns=3)
            raw = result.final_message or ""
            return self._parse_review_response(raw)
        except Exception:
            return ReviewReport.empty()

    def _parse_review_response(self, raw: str) -> ReviewReport:
        """Parse JSON from the reviewer agent's output."""
        # Try direct JSON
        try:
            data = json.loads(raw.strip())
            return ReviewReport.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            pass

        # Try markdown code block
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
        if m:
            try:
                data = json.loads(m.group(1).strip())
                return ReviewReport.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                pass

        # Try to find JSON object anywhere
        m = re.search(r'\{[\s\S]*"overall_score"[\s\S]*\}', raw)
        if m:
            try:
                data = json.loads(m.group(0))
                return ReviewReport.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                pass

        return ReviewReport.empty()

    # ------------------------------------------------------------------
    # Static helper — compute combined reward
    # ------------------------------------------------------------------

    @staticmethod
    def combined_reward(
        test_pass_rate: float,
        diff_accuracy: float,
        review: Optional[ReviewReport] = None,
        weights: Optional[Dict[str, float]] = None,
    ) -> float:
        """Compute combined reward from test, diff, and review signals.

        Default weights: test 0.4, diff 0.3, review 0.3.
        """
        w = weights or {"test": 0.4, "diff": 0.3, "review": 0.3}
        reward = test_pass_rate * w["test"] + diff_accuracy * w["diff"]
        if review:
            reward += review.overall_score * w["review"]
        else:
            # If no review, redistribute review weight to test/diff
            remaining = w["review"] / 2
            reward += test_pass_rate * remaining + diff_accuracy * remaining
        return max(0.0, min(1.0, reward))
