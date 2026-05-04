"""Bundled skill definitions for CodeAgent.

Skills that can be invoked via the Skill tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class BundledSkill:
    """A bundled skill that can be invoked."""
    name: str
    description: str
    prompt: str
    parameters: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "parameters": self.parameters,
        }


# Bundled skills registry
BUNDLED_SKILLS = {
    "explain-code": BundledSkill(
        name="explain-code",
        description="Explain how code works",
        prompt="Explain the following code in detail:\n\n{code}\n\nProvide a clear explanation of what the code does, how it works, and any notable patterns or practices.",
        parameters={"type": "object", "properties": {"code": {"type": "string"}}},
    ),
    "review-code": BundledSkill(
        name="review-code",
        description="Review code for issues and improvements",
        prompt="Review the following code and provide feedback on:\n1. Potential bugs or issues\n2. Code quality and style\n3. Performance concerns\n4. Security considerations\n5. Suggestions for improvement\n\nCode:\n\n{code}",
        parameters={"type": "object", "properties": {"code": {"type": "string"}}},
    ),
    "generate-tests": BundledSkill(
        name="generate-tests",
        description="Generate unit tests for code",
        prompt="Generate comprehensive unit tests for the following code. Include edge cases and typical use cases.\n\nCode:\n\n{code}\n\nLanguage/Framework: {language}",
        parameters={"type": "object", "properties": {"code": {"type": "string"}, "language": {"type": "string"}}},
    ),
    "document-code": BundledSkill(
        name="document-code",
        description="Generate documentation for code",
        prompt="Generate documentation for the following code. Include:\n1. Overview of what the code does\n2. Function/class documentation\n3. Usage examples\n4. Parameter descriptions\n\nCode:\n\n{code}",
        parameters={"type": "object", "properties": {"code": {"type": "string"}}},
    ),
}


def get_skill(skill_name: str) -> Optional[BundledSkill]:
    """Get a bundled skill by name."""
    return BUNDLED_SKILLS.get(skill_name)


def list_skills() -> List[BundledSkill]:
    """List all bundled skills."""
    return list(BUNDLED_SKILLS.values())