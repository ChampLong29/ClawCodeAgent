"""Domain configuration system for multi-domain generalization.

Domain-specific variables (tech stack, test framework, review criteria,
etc.) are injected into lifecycle/devflow skill prompts, while the
underlying phase structure remains constant across domains.

This is how the system achieves generalization: the lifecycle phases
are the universal framework; domain configs are the variables.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# DomainConfig
# ---------------------------------------------------------------------------

@dataclass
class DomainConfig:
    """Configuration that makes one lifecycle adaptable to many domains."""

    name: str                                    # "web-backend"
    display_name: str = ""                       # "Web Backend (FastAPI)"
    description: str = ""                        # Human-readable description

    # --- injected into ARCHITECTURE / SYSTEM_DESIGN phases ---
    tech_stack: Dict[str, str] = field(default_factory=dict)
    recommended_patterns: List[str] = field(default_factory=list)

    # --- injected into IMPLEMENTATION phase ---
    project_structure: Optional[str] = None       # Suggested directory layout
    coding_standards: List[str] = field(default_factory=list)

    # --- injected into UNIT_TEST / INTEGRATION_TEST phases ---
    test_framework: str = "pytest"
    test_template: str = ""                       # Boilerplate for test files

    # --- injected into CODE_REVIEW phase ---
    review_criteria: List[str] = field(default_factory=list)

    # --- injected into ACCEPTANCE phase ---
    acceptance_checklist: List[str] = field(default_factory=list)

    # --- optional per-phase skill prompt overrides ---
    skill_overrides: Dict[str, str] = field(default_factory=dict)

    def to_prompt_context(self) -> str:
        """Format domain config as a compact string for prompt injection."""
        parts: List[str] = [f"**Domain**: {self.display_name or self.name}"]

        if self.tech_stack:
            items = ", ".join(f"{k}={v}" for k, v in self.tech_stack.items())
            parts.append(f"**Tech Stack**: {items}")

        if self.recommended_patterns:
            parts.append(
                "**Recommended Patterns**: " + ", ".join(self.recommended_patterns)
            )

        if self.coding_standards:
            parts.append(
                "**Coding Standards**: " + "; ".join(self.coding_standards)
            )

        if self.test_framework:
            parts.append(f"**Test Framework**: {self.test_framework}")

        if self.review_criteria:
            items = "\n- " + "\n- ".join(self.review_criteria)
            parts.append(f"**Review Criteria**:{items}")

        if self.acceptance_checklist:
            items = "\n- [ ] " + "\n- [ ] ".join(self.acceptance_checklist)
            parts.append(f"**Acceptance Checklist**:{items}")

        return "\n".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "tech_stack": self.tech_stack,
            "recommended_patterns": self.recommended_patterns,
            "project_structure": self.project_structure,
            "coding_standards": self.coding_standards,
            "test_framework": self.test_framework,
            "test_template": self.test_template,
            "review_criteria": self.review_criteria,
            "acceptance_checklist": self.acceptance_checklist,
            "skill_overrides": self.skill_overrides,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DomainConfig:
        return cls(
            name=data.get("name", ""),
            display_name=data.get("display_name", ""),
            description=data.get("description", ""),
            tech_stack=data.get("tech_stack", {}),
            recommended_patterns=data.get("recommended_patterns", []),
            project_structure=data.get("project_structure"),
            coding_standards=data.get("coding_standards", []),
            test_framework=data.get("test_framework", "pytest"),
            test_template=data.get("test_template", ""),
            review_criteria=data.get("review_criteria", []),
            acceptance_checklist=data.get("acceptance_checklist", []),
            skill_overrides=data.get("skill_overrides", {}),
        )


# ---------------------------------------------------------------------------
# DomainRegistry
# ---------------------------------------------------------------------------

class DomainRegistry:
    """Registry of domain configurations, loadable from JSON files."""

    def __init__(self):
        self._domains: Dict[str, DomainConfig] = {}

    def register(self, config: DomainConfig) -> None:
        self._domains[config.name] = config

    def get(self, name: str) -> Optional[DomainConfig]:
        return self._domains.get(name)

    def list_names(self) -> List[str]:
        return sorted(self._domains.keys())

    def list_all(self) -> List[DomainConfig]:
        return [self._domains[k] for k in sorted(self._domains.keys())]

    def load_from_dir(self, dir_path: str) -> int:
        """Load all ``*.json`` files from *dir_path* as domain configs.

        Returns the number of configs loaded.
        """
        if not os.path.isdir(dir_path):
            return 0
        count = 0
        for fname in sorted(os.listdir(dir_path)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(dir_path, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.register(DomainConfig.from_dict(data))
                count += 1
            except (json.JSONDecodeError, KeyError):
                pass
        return count

    def to_dict(self) -> Dict[str, Any]:
        return {"domains": [d.to_dict() for d in self.list_all()]}


# ---------------------------------------------------------------------------
# Built-in domain definitions
# ---------------------------------------------------------------------------

BUILTIN_DOMAINS: Dict[str, DomainConfig] = {
    "web-backend": DomainConfig(
        name="web-backend",
        display_name="Web Backend (REST API)",
        description="REST API development with database, authentication, and CRUD operations",
        tech_stack={
            "framework": "FastAPI",
            "database": "PostgreSQL",
            "orm": "SQLAlchemy 2.0",
            "auth": "JWT + OAuth2",
        },
        recommended_patterns=[
            "Repository pattern", "Dependency injection",
            "Service layer separation", "API versioning",
        ],
        project_structure="backend/{models,routes,services,schemas}/",
        coding_standards=[
            "Type hints on all functions",
            "Pydantic models for request/response validation",
            "Async handlers with async database sessions",
        ],
        test_framework="pytest + httpx + testcontainers",
        review_criteria=[
            "SQL injection prevention",
            "Input validation completeness",
            "API error handling consistency",
            "N+1 query avoidance",
            "Authentication on every protected endpoint",
        ],
        acceptance_checklist=[
            "OpenAPI docs auto-generated at /docs",
            "All endpoints have test coverage",
            "Database migrations are reversible",
            "Health check endpoint returns 200",
        ],
    ),
    "web-frontend": DomainConfig(
        name="web-frontend",
        display_name="Web Frontend (SPA)",
        description="Single-page application with components, routing, and state management",
        tech_stack={
            "framework": "React",
            "language": "TypeScript",
            "styling": "Tailwind CSS",
            "state": "React Context / Zustand",
        },
        recommended_patterns=[
            "Component composition over inheritance",
            "Custom hooks for reusable logic",
            "Controlled form components",
        ],
        project_structure="src/{components,pages,hooks,utils,types}/",
        coding_standards=[
            "TypeScript strict mode",
            "One component per file",
            "Unit tests for hooks and utilities",
        ],
        test_framework="vitest + @testing-library/react",
        review_criteria=[
            "XSS prevention (no dangerouslySetInnerHTML)",
            "Accessibility (aria labels, keyboard navigation)",
            "Component re-render optimization",
            "Form validation completeness",
        ],
        acceptance_checklist=[
            "Lighthouse accessibility score ≥ 90",
            "All form fields have validation",
            "Responsive layout on mobile",
        ],
    ),
    "cli-tool": DomainConfig(
        name="cli-tool",
        display_name="CLI Tool",
        description="Command-line tools with argument parsing, pipelines, and output formatting",
        tech_stack={
            "language": "Python",
            "cli_framework": "click",
            "packaging": "setuptools + entry_points",
        },
        recommended_patterns=[
            "Single responsibility per command",
            "Compose commands via subcommands",
            "Streaming output for large data",
        ],
        coding_standards=[
            "Argparse/click types for validation",
            "Exit codes follow convention (0=success, 1=error, 2=usage)",
            "stdout for data, stderr for diagnostics",
        ],
        test_framework="pytest + CliRunner",
        review_criteria=[
            "Argument validation and help text completeness",
            "Exit code correctness",
            "Stdout/stderr separation",
            "Pipe and redirect compatibility",
        ],
        acceptance_checklist=[
            "--help works for every command",
            "Piped input works (stdin → command → stdout)",
            "Shell completion scripts generated",
        ],
    ),
    "data-pipeline": DomainConfig(
        name="data-pipeline",
        display_name="Data Pipeline (ETL)",
        description="Extract-transform-load pipelines with data validation and error handling",
        tech_stack={
            "language": "Python",
            "data_lib": "pandas / polars",
            "validation": "pandera / pydantic",
            "storage": "Parquet / SQL",
        },
        recommended_patterns=[
            "Schema-first data validation",
            "Idempotent pipeline stages",
            "Checkpoint-based recovery",
        ],
        coding_standards=[
            "Column-level type annotations",
            "Null handling policy documented",
            "Pipeline stages independently testable",
        ],
        test_framework="pytest + fixture-based sample data",
        review_criteria=[
            "Null/missing value handling",
            "Data type consistency checks",
            "Memory efficiency for large datasets",
            "Error logging and recovery",
        ],
        acceptance_checklist=[
            "Pipeline runs end-to-end on sample data",
            "Invalid data is rejected with clear error messages",
            "Output schema matches specification",
        ],
    ),
    "sdk-library": DomainConfig(
        name="sdk-library",
        display_name="SDK / Library",
        description="Reusable library with public API, documentation, and backward compatibility",
        tech_stack={
            "language": "Python",
            "docs": "Sphinx / mkdocs",
            "linting": "ruff + mypy",
        },
        recommended_patterns=[
            "Facade pattern for public API",
            "Semantic versioning",
            "Deprecation warnings before removal",
        ],
        coding_standards=[
            "All public functions have docstrings",
            "Type stubs generated (.pyi)",
            "Configuration via constructor, not globals",
        ],
        test_framework="pytest + doctest",
        review_criteria=[
            "Public API surface is minimal and consistent",
            "Error messages are user-friendly",
            "Thread-safety where applicable",
            "Circular import prevention",
        ],
        acceptance_checklist=[
            "Documentation builds without warnings",
            "All public API has examples in docs",
            "Backward compatibility with previous minor version",
        ],
    ),
}


def default_registry() -> DomainRegistry:
    """Return a DomainRegistry pre-loaded with built-in domains."""
    registry = DomainRegistry()
    for config in BUILTIN_DOMAINS.values():
        registry.register(config)
    return registry
