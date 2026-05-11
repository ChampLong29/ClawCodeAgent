"""Sandbox management for isolated agent execution.

Thin compatibility re-export — all logic lives in ``src.sandbox``.
"""

from ..sandbox import (
    SandboxConfig,
    WorkspaceSandbox as SandboxManager,
    TestResult,
    DiffResult,
)

__all__ = ["SandboxManager", "TestResult", "DiffResult", "SandboxConfig"]
