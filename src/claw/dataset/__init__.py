"""Verified dataset selection, leakage checks, conversion, and manifests."""

from .builder import DatasetBuildResult, DatasetBuilder, DatasetRecord
from .converters import (
    DatasetValidationError,
    extract_messages,
    segment_messages,
    validate_tool_alignment,
)
from .leakage import LeakageError, LeakageIssue, LeakageReport

__all__ = [
    "DatasetBuildResult",
    "DatasetBuilder",
    "DatasetRecord",
    "DatasetValidationError",
    "LeakageError",
    "LeakageIssue",
    "LeakageReport",
    "extract_messages",
    "segment_messages",
    "validate_tool_alignment",
]
