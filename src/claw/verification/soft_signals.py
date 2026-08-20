"""Deterministic advisory signals that do not alter hard-gate verdicts."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import List

from ..experiment.schemas import VerificationSignal
from .base import VerificationContext


FINAL_RESPONSE_QUALITY_SCHEMA_VERSION = "final_response_quality.v1"

_RAW_TOOL_CALL_PATTERNS = (
    re.compile(r"<\|\|DSML\|\|tool_calls>", re.IGNORECASE),
    re.compile(r"<｜｜DSML｜｜tool_calls>", re.IGNORECASE),
    re.compile(r"^\s*<tool_calls?>", re.IGNORECASE),
    re.compile(r"^\s*<function_calls?>", re.IGNORECASE),
)
_THINKING_BLOCK = re.compile(
    r"^\s*<(?:think|thinking)>.*?</(?:think|thinking)>\s*$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class FinalResponseQualityAssessment:
    """A conservative, replayable assessment of the recorded final response."""

    status: str
    classification: str
    reasons: List[str]
    response_length: int
    schema_version: str = FINAL_RESPONSE_QUALITY_SCHEMA_VERSION

    def to_dict(self):
        return asdict(self)


def assess_final_response(detail: str, *, completed: bool) -> FinalResponseQualityAssessment:
    """Classify obvious final-response failures without judging correctness."""

    text = str(detail or "").strip()
    if not completed:
        return FinalResponseQualityAssessment(
            status="unknown",
            classification="not_completed",
            reasons=["episode_did_not_complete"],
            response_length=len(text),
        )
    if not text:
        return FinalResponseQualityAssessment(
            status="fail",
            classification="empty",
            reasons=["empty_final_response"],
            response_length=0,
        )
    if text.startswith("legacy stop_reason="):
        return FinalResponseQualityAssessment(
            status="unknown",
            classification="legacy_placeholder",
            reasons=["legacy_trajectory_did_not_record_final_response"],
            response_length=len(text),
        )
    if any(pattern.search(text) for pattern in _RAW_TOOL_CALL_PATTERNS):
        return FinalResponseQualityAssessment(
            status="fail",
            classification="raw_tool_call_markup",
            reasons=["raw_tool_call_markup_in_final_response"],
            response_length=len(text),
        )
    if _THINKING_BLOCK.fullmatch(text):
        return FinalResponseQualityAssessment(
            status="fail",
            classification="thinking_only",
            reasons=["final_response_contains_only_private_reasoning_markup"],
            response_length=len(text),
        )
    return FinalResponseQualityAssessment(
        status="pass",
        classification="user_facing_text",
        reasons=[],
        response_length=len(text),
    )


class FinalResponseQualityVerifier:
    """Record obvious delivery defects separately from termination compliance."""

    name = "final_response_quality"

    def evaluate(self, context: VerificationContext) -> VerificationSignal:
        termination = context.trajectory.header.termination
        if termination is None:
            assessment = FinalResponseQualityAssessment(
                status="unknown",
                classification="missing_termination",
                reasons=["trajectory_has_no_termination_record"],
                response_length=0,
            )
        else:
            assessment = assess_final_response(
                termination.detail,
                completed=termination.reason == "completed",
            )
        evidence = (
            [context.trajectory.events[-1].event_id]
            if context.trajectory.events
            else []
        )
        score = {"pass": 1.0, "fail": 0.0}.get(assessment.status)
        return VerificationSignal(
            name=self.name,
            kind="soft",
            status=assessment.status,
            score=score,
            weight=0.0,
            evidence_event_ids=evidence,
            details=assessment.to_dict(),
            required=False,
            verifiable=True,
        )


DEFAULT_SOFT_VERIFIERS = [FinalResponseQualityVerifier()]
