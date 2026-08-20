"""Hard-gated multi-signal verification and bad-case classification."""

from .bad_case import (
    BAD_CASE_CATEGORIES,
    BadCaseClassifier,
    BadCaseRecord,
    BadCaseRevision,
)
from .base import VerificationContext, VerificationPolicy
from .pipeline import VerifierPipeline
from .reviewer_adapter import ReviewerAdapter, ReviewerEvidence
from .soft_signals import (
    FINAL_RESPONSE_QUALITY_SCHEMA_VERSION,
    FinalResponseQualityAssessment,
    FinalResponseQualityVerifier,
    assess_final_response,
)

__all__ = [
    "BAD_CASE_CATEGORIES",
    "BadCaseClassifier",
    "BadCaseRecord",
    "BadCaseRevision",
    "FINAL_RESPONSE_QUALITY_SCHEMA_VERSION",
    "FinalResponseQualityAssessment",
    "FinalResponseQualityVerifier",
    "ReviewerAdapter",
    "ReviewerEvidence",
    "VerificationContext",
    "VerificationPolicy",
    "VerifierPipeline",
    "assess_final_response",
]
