"""System prompt building for CodeAgent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PromptSection:
    """A section of the system prompt."""
    name: str
    content: str
    priority: int = 0  # Lower = earlier in prompt

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "content": self.content,
            "priority": self.priority,
        }


@dataclass
class SystemPromptBuilder:
    """Builds system prompts from runtime capabilities."""
    sections: List[PromptSection] = field(default_factory=list)

    def add_section(self, name: str, content: str, priority: int = 0) -> None:
        """Add a section to the prompt."""
        self.sections.append(PromptSection(name=name, content=content, priority=priority))

    def render(self) -> str:
        """Render the prompt from sections, sorted by priority."""
        sorted_sections = sorted(self.sections, key=lambda s: s.priority)
        return "\n\n".join(s.content for s in sorted_sections if s.content)

    def clear(self) -> None:
        """Clear all sections."""
        self.sections = []

    @classmethod
    def from_runtimes(cls, runtimes: List[Any]) -> SystemPromptBuilder:
        """Build prompt from runtime modules."""
        builder = cls()

        # Core instruction section (highest priority)
        builder.add_section(
            "core_instructions",
            "You are a helpful coding assistant. Follow the user's instructions precisely.",
            priority=0
        )

        # Add runtime-specific guidance
        for runtime in runtimes:
            if hasattr(runtime, "get_prompt_guidance"):
                try:
                    guidance = runtime.get_prompt_guidance()
                    if guidance:
                        builder.add_section(
                            f"runtime_{runtime.__class__.__name__}",
                            guidance,
                            priority=getattr(runtime, "prompt_priority", 50)
                        )
                except Exception:
                    pass

        # Security section
        builder.add_section(
            "security",
            "Do not execute destructive commands unless explicitly approved. "
            "Always validate user intent before potentially harmful operations.",
            priority=90
        )

        return builder


def build_system_prompt_parts(
    runtimes: Optional[List[Any]] = None,
    extra_sections: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Build system prompt as a list of strings.

    Returns parts that can be joined with newlines.
    """
    parts = []

    # Core
    parts.append("You are a helpful coding assistant.")

    # Context sections
    if extra_sections:
        for name, content in extra_sections.items():
            parts.append(f"[{name}]\n{content}")

    # Runtime guidance
    if runtimes:
        for runtime in runtimes:
            if hasattr(runtime, "get_prompt_guidance"):
                try:
                    guidance = runtime.get_prompt_guidance()
                    if guidance:
                        parts.append(guidance)
                except Exception:
                    pass

    return parts


def render_system_prompt(
    runtimes: Optional[List[Any]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """Render the full system prompt."""
    builder = SystemPromptBuilder.from_runtimes(runtimes or [])

    # Add context info if provided
    if context:
        if context.get("claude_md"):
            builder.add_section(
                "claude_md",
                f"[Project Guidelines from CLAUDE.md]\n{context['claude_md']}",
                priority=10
            )

    return builder.render()