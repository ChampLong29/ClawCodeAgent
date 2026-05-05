"""Phase-level context and memory management.

Provides phase-boundary compaction that keeps structured outputs from
completed phases while discarding intermediate tool-call chatter.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_session import AgentSession


# ---------------------------------------------------------------------------
# PhaseContextPolicy
# ---------------------------------------------------------------------------

@dataclass
class PhaseContextPolicy:
    """What to keep / discard during phase-boundary compaction."""

    keep_phase_boundaries: bool = True
    keep_last_n_exchanges: int = 3
    max_tokens_per_phase_summary: int = 500


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to roughly *max_tokens* tokens (4 chars ≈ 1 token)."""
    char_limit = max_tokens * 4
    if len(text) <= char_limit:
        return text
    return text[:char_limit] + "\n\n... [truncated]"


def _get_phase_name_from_boundary(msg: Dict[str, Any]) -> Optional[str]:
    """Extract phase name from a phase-boundary system message."""
    meta = msg.get("metadata", {})
    if isinstance(meta, dict) and meta.get("phase_boundary"):
        return meta.get("phase_name")
    return None


# ---------------------------------------------------------------------------
# ContextManager
# ---------------------------------------------------------------------------

class ContextManager:
    """Manages phase-level context: compaction, structured-output extraction,
    and cross-phase memory injection."""

    def __init__(self, policy: Optional[PhaseContextPolicy] = None):
        self.policy = policy or PhaseContextPolicy()

    # ------------------------------------------------------------------
    # compaction
    # ------------------------------------------------------------------

    def compact_at_phase_transition(
        self,
        session: "AgentSession",
        current_phase: str,
        completed_phase_outputs: Dict[str, str],
    ) -> None:
        """Compact session messages at a phase transition.

        Strategy
        --------
        1. Keep everything before the first phase-boundary marker (system
           prompt, initial user message, etc.).
        2. Keep every phase-boundary marker.
        3. For *completed* phases: discard all intermediate messages,
           replace with a single system summary (truncated).
        4. For the *current* (just-finished) phase: keep the last few
           exchanges for continuity.  Its full output will be summarised
           when the *next* phase completes.
        """
        policy = self.policy
        messages = session.messages

        boundary_indices: Dict[str, int] = {
            _get_phase_name_from_boundary(m): i
            for i, m in enumerate(messages)
            if _get_phase_name_from_boundary(m)
        }

        if not boundary_indices:
            return  # nothing to compact yet

        sorted_phases = sorted(boundary_indices.keys(),
                               key=lambda n: boundary_indices[n])

        kept: List[Dict[str, Any]] = []

        # Everything before the first boundary (system prompt, initial user
        # message, etc.)
        first_boundary_idx = min(boundary_indices.values())
        kept.extend(messages[:first_boundary_idx])

        for i, phase_name in enumerate(sorted_phases):
            start = boundary_indices[phase_name]
            # End of this phase = next boundary (or end of messages)
            if i + 1 < len(sorted_phases):
                end = boundary_indices[sorted_phases[i + 1]]
            else:
                end = len(messages)

            # Keep the boundary marker
            if policy.keep_phase_boundaries:
                kept.append(messages[start])

            if phase_name == current_phase:
                # Just-finished phase — keep recent exchanges for continuity
                phase_msgs = messages[start + 1:end]
                keep_count = policy.keep_last_n_exchanges * 2
                kept.extend(phase_msgs[-keep_count:])
            else:
                # Older completed phase — replace with structured summary
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

        session.messages = kept
        session.updated_at = time.time()

    # ------------------------------------------------------------------
    # structured output extraction
    # ------------------------------------------------------------------

    def extract_structured_output(
        self,
        session: "AgentSession",
        phase_name: str,
    ) -> Optional[str]:
        """Return the last assistant message from *phase_name*, or ``None``."""
        phase_msgs = session.get_phase_messages(phase_name)
        for msg in reversed(phase_msgs):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return None

    # ------------------------------------------------------------------
    # context injection
    # ------------------------------------------------------------------

    def build_phase_context_injection(
        self,
        completed_phase_outputs: Dict[str, str],
        current_phase: str,
        overall_goal: str,
    ) -> str:
        """Build a system-prompt section summarising completed phases."""
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
