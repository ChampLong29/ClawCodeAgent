"""Phase-level context and memory management.

Builds a *read-only* context view for the LLM from the complete
session record.  The session itself is never modified — it remains
an append-only ledger of everything that happened.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_session import AgentSession


# ---------------------------------------------------------------------------
# PhaseContextPolicy
# ---------------------------------------------------------------------------

@dataclass
class PhaseContextPolicy:
    """What to keep / discard when building the LLM context view."""

    keep_phase_boundaries: bool = True
    keep_last_n_exchanges: int = 3
    max_tokens_per_phase_summary: int = 500


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    char_limit = max_tokens * 4
    if len(text) <= char_limit:
        return text
    return text[:char_limit] + "\n\n... [truncated]"


def _get_phase_name_from_boundary(msg: Dict[str, Any]) -> Optional[str]:
    meta = msg.get("metadata", {})
    if isinstance(meta, dict) and meta.get("phase_boundary"):
        return meta.get("phase_name")
    return None


# ---------------------------------------------------------------------------
# ContextManager
# ---------------------------------------------------------------------------

class ContextManager:
    """Builds a compact LLM context view from the full session record.

    The session's ``messages`` list is **never modified**.  Instead,
    ``build_context()`` returns a fresh list every call — safe to
    truncate, safe to re-compute (e.g. after a rollback).
    """

    def __init__(self, policy: Optional[PhaseContextPolicy] = None):
        self.policy = policy or PhaseContextPolicy()

    # ------------------------------------------------------------------
    # Context builder (read-only — session untouched)
    # ------------------------------------------------------------------

    def build_context(
        self,
        session: "AgentSession",
        current_phase: str,
        completed_phase_outputs: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """Build a compact context view for the LLM.

        Returns a **new** list — the session's ``messages`` are not
        touched.  Call this at the start of every ``_run_loop`` turn.

        Strategy
        --------
        1. Keep everything before the first phase-boundary marker.
        2. Keep every phase-boundary marker.
        3. For *completed* phases: replace all messages with one
           system summary (truncated output).
        4. For the *current* phase: keep the last few exchanges.
        """
        policy = self.policy
        messages = session.messages

        boundary_indices: Dict[str, int] = {
            _get_phase_name_from_boundary(m): i
            for i, m in enumerate(messages)
            if _get_phase_name_from_boundary(m)
        }

        if not boundary_indices:
            return list(messages)

        sorted_phases = sorted(
            boundary_indices.keys(), key=lambda n: boundary_indices[n]
        )

        kept: List[Dict[str, Any]] = []
        first_boundary_idx = min(boundary_indices.values())
        kept.extend(messages[:first_boundary_idx])

        for i, phase_name in enumerate(sorted_phases):
            start = boundary_indices[phase_name]
            end = (
                boundary_indices[sorted_phases[i + 1]]
                if i + 1 < len(sorted_phases)
                else len(messages)
            )

            if policy.keep_phase_boundaries:
                kept.append(messages[start])

            if phase_name == current_phase:
                phase_msgs = messages[start + 1:end]
                keep_count = policy.keep_last_n_exchanges * 2
                kept.extend(phase_msgs[-keep_count:])
            else:
                output = completed_phase_outputs.get(phase_name)
                if output:
                    summary = _truncate_to_tokens(
                        output, policy.max_tokens_per_phase_summary
                    )
                    kept.append({
                        "role": "system",
                        "content": f"[Phase {phase_name} summary]\n{summary}",
                        "metadata": {
                            "phase_summary": True,
                            "phase_name": phase_name,
                        },
                    })

        return kept

    # ------------------------------------------------------------------
    # Structured output extraction (read-only)
    # ------------------------------------------------------------------

    def extract_structured_output(
        self,
        session: "AgentSession",
        phase_name: str,
    ) -> Optional[str]:
        phase_msgs = session.get_phase_messages(phase_name)
        for msg in reversed(phase_msgs):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return None

    # ------------------------------------------------------------------
    # System prompt injection
    # ------------------------------------------------------------------

    def build_phase_context_injection(
        self,
        completed_phase_outputs: Dict[str, str],
        current_phase: str,
        overall_goal: str,
    ) -> str:
        if not completed_phase_outputs:
            return ""

        parts: List[str] = ["## Previously Completed Phases\n"]
        for phase_name, output in completed_phase_outputs.items():
            summary = _truncate_to_tokens(
                output, self.policy.max_tokens_per_phase_summary
            )
            parts.append(f"### {phase_name}\n{summary}\n")

        parts.append(
            f"\nCurrent phase: **{current_phase}**. "
            f"Focus exclusively on this phase. "
            f"Do not re-execute or modify work from completed phases "
            f"unless the user explicitly asks."
        )
        return "\n".join(parts)
