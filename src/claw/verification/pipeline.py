"""Hard-first verifier pipeline producing versioned reports."""

from __future__ import annotations

from typing import Iterable, List, Optional

from ..experiment.schemas import VerificationReport, VerificationSignal
from ..trajectory.schema import stable_id
from .bad_case import BadCaseClassifier
from .base import SignalVerifier, VerificationContext
from .hard_signals import DEFAULT_HARD_VERIFIERS
from .reviewer_adapter import ReviewerAdapter, ReviewerEvidence
from .soft_signals import DEFAULT_SOFT_VERIFIERS


class VerifierPipeline:
    def __init__(
        self,
        hard_verifiers: Optional[Iterable[SignalVerifier]] = None,
        *,
        soft_verifiers: Optional[Iterable[SignalVerifier]] = None,
        reviewer_adapter: Optional[ReviewerAdapter] = None,
        bad_case_classifier: Optional[BadCaseClassifier] = None,
    ):
        self.hard_verifiers = list(hard_verifiers or DEFAULT_HARD_VERIFIERS)
        self.soft_verifiers = list(
            DEFAULT_SOFT_VERIFIERS if soft_verifiers is None else soft_verifiers
        )
        self.reviewer_adapter = reviewer_adapter or ReviewerAdapter()
        self.bad_case_classifier = bad_case_classifier or BadCaseClassifier()

    def verify(
        self,
        context: VerificationContext,
        *,
        reviewer: Optional[ReviewerEvidence] = None,
    ) -> VerificationReport:
        context.policy.validate()
        signals: List[VerificationSignal] = [
            verifier.evaluate(context) for verifier in self.hard_verifiers
        ]
        signals.extend(verifier.evaluate(context) for verifier in self.soft_verifiers)
        reviewer_metadata = {}
        if reviewer is not None:
            signals.append(
                self.reviewer_adapter.to_signal(
                    reviewer,
                    weight=context.policy.signal_weights.get(
                        "independent_reviewer", 0.0
                    ),
                )
            )
            reviewer_metadata = self.reviewer_adapter.metadata(reviewer)

        verdict = self._verdict(signals)
        hard_gate_passed = verdict == "success"
        aggregate = self._aggregate(signals)
        report = VerificationReport(
            report_id=stable_id(
                "verify",
                {
                    "trajectory_id": context.trajectory.header.trajectory_id,
                    "policy": context.policy.version,
                    "signals": [signal.to_dict() for signal in signals],
                },
            ),
            trajectory_ref=context.trajectory.header.trajectory_id,
            verifier_bundle_version=context.policy.version,
            verdict=verdict,
            hard_gate_passed=hard_gate_passed,
            signals=signals,
            aggregate_score=aggregate,
            evidence_refs=[],
            reviewer_metadata=reviewer_metadata,
        )
        report.validate()
        bad_case = self.bad_case_classifier.classify(report, context.trajectory)
        if bad_case is not None:
            report.bad_cases.append(bad_case.to_dict())
        return report

    @staticmethod
    def _verdict(signals: List[VerificationSignal]) -> str:
        hard = [signal for signal in signals if signal.kind == "hard"]
        if any(signal.status == "fail" for signal in hard):
            return "failure"
        if any(
            signal.required and signal.status == "unknown"
            for signal in hard
        ):
            return "not_verifiable"
        if not any(
            signal.verifiable and signal.status == "pass"
            for signal in hard
        ):
            return "not_verifiable"
        return "success"

    @staticmethod
    def _aggregate(signals: List[VerificationSignal]) -> Optional[float]:
        available = [
            signal
            for signal in signals
            if signal.score is not None and signal.weight > 0
        ]
        total_weight = sum(signal.weight for signal in available)
        if total_weight <= 0:
            return None
        score = sum(
            float(signal.score) * signal.weight for signal in available
        ) / total_weight
        return max(0.0, min(1.0, score))
