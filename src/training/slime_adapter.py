"""SLIME data format adapter.

Converts Claw Code Agent rollout trajectories into slime-compatible
training data (prompt-response-reward triples).  Supports both SFT
(cold start) and RL export formats.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .reviewer import ReviewReport


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SlimeTrainingSample:
    """One training sample in slime-compatible format."""

    prompt: List[Dict[str, Any]]     # system + user messages
    response: List[Dict[str, Any]]   # assistant + tool messages
    reward: float                    # combined reward [0, 1]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt": self.prompt,
            "response": self.response,
            "reward": self.reward,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SlimeTrainingSample:
        return cls(
            prompt=data.get("prompt", []),
            response=data.get("response", []),
            reward=float(data.get("reward", 0)),
            metadata=data.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# SlimeDataAdapter
# ---------------------------------------------------------------------------

class SlimeDataAdapter:
    """Convert rollout results to slime-compatible training data."""

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    @staticmethod
    def to_slime_sample(
        messages: List[Dict[str, Any]],
        reward: float,
        task_id: str = "",
        domain: str = "",
        difficulty: str = "",
        review: Optional[ReviewReport] = None,
    ) -> SlimeTrainingSample:
        """Split agent session messages into prompt and response portions.

        The system message and first user message become the *prompt*;
        everything after becomes the *response*.
        """
        prompt: List[Dict[str, Any]] = []
        response: List[Dict[str, Any]] = []
        user_seen = False

        for msg in messages:
            role = msg.get("role", "")
            if role == "system" or (role == "user" and not user_seen):
                prompt.append(msg)
                if role == "user":
                    user_seen = True
            else:
                # Everything after the first user message
                response.append(msg)

        # Extract tool calls and assistant content as metadata
        tool_calls_count = sum(
            1 for m in response if m.get("role") == "assistant" and m.get("tool_calls")
        )
        assistant_msgs_count = sum(
            1 for m in response if m.get("role") == "assistant"
        )

        metadata: Dict[str, Any] = {
            "task_id": task_id,
            "domain": domain,
            "difficulty": difficulty,
            "tool_calls": tool_calls_count,
            "assistant_messages": assistant_msgs_count,
        }

        if review:
            metadata["review"] = review.to_dict()

        return SlimeTrainingSample(
            prompt=prompt,
            response=response,
            reward=reward,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # SFT export (cold start)
    # ------------------------------------------------------------------

    @staticmethod
    def export_sft_dataset(
        results: List[Dict[str, Any]],
        output_path: str,
        min_reward: float = 0.8,
    ) -> int:
        """Export high-quality trajectories as SFT training data.

        Only includes samples with ``reward >= min_reward``.  SFT data
        is prompt → response pairs (reward is discarded).

        Returns the number of exported samples.
        """
        samples: List[Dict[str, Any]] = []
        for r in results:
            reward = float(r.get("reward", 0))
            if reward < min_reward:
                continue
            sample = {
                "prompt": r.get("prompt", []),
                "response": r.get("response", []),
                "metadata": r.get("metadata", {}),
            }
            samples.append(sample)

        with open(output_path, "w", encoding="utf-8") as f:
            for s in samples:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")

        return len(samples)

    # ------------------------------------------------------------------
    # RL export
    # ------------------------------------------------------------------

    @staticmethod
    def export_rl_dataset(
        results: List[Dict[str, Any]],
        output_path: str,
    ) -> int:
        """Export all trajectories as RL training data.

        Unlike SFT export, all samples are included regardless of
        reward.  slime uses advantage to distinguish good from bad.

        Returns the number of exported samples.
        """
        with open(output_path, "w", encoding="utf-8") as f:
            for r in results:
                sample = {
                    "prompt": r.get("prompt", []),
                    "response": r.get("response", []),
                    "reward": r.get("reward", 0),
                    "metadata": r.get("metadata", {}),
                }
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")

        return len(results)

    # ------------------------------------------------------------------
    # Quality filtering (data flywheel)
    # ------------------------------------------------------------------

    @staticmethod
    def filter_by_quality(
        results: List[Dict[str, Any]],
        min_reward: float = 0.8,
        min_review_score: float = 0.7,
    ) -> List[Dict[str, Any]]:
        """Filter results for data flywheel re-injection.

        A sample passes if:
        - reward >= min_reward
        - review overall_score >= min_review_score (if review exists)
        """
        filtered: List[Dict[str, Any]] = []
        for r in results:
            reward = float(r.get("reward", 0))
            if reward < min_reward:
                continue
            meta = r.get("metadata", {})
            review = meta.get("review", {})
            if review:
                review_score = float(review.get("overall_score", 0))
                if review_score < min_review_score:
                    continue
            filtered.append(r)
        return filtered
