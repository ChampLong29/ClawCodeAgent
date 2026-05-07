"""Unified skill registry — merges built-in and external skills.

Provides a single lookup point so the rest of the codebase doesn't
need to know whether a skill came from Python code or a .md file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# ExternalSkill — loaded from .md files
# ---------------------------------------------------------------------------

@dataclass
class ExternalSkill:
    """A skill loaded from a .md file on disk.

    Compatible with the Claude Code skill format (YAML frontmatter +
    Markdown body).  Supports optional ``parameters`` for {param}
    template substitution.
    """

    name: str
    description: str
    prompt: str                        # full markdown body
    parameters: Optional[Dict[str, Any]] = None
    source: str = ""                   # file path (for debugging)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "parameters": self.parameters,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------

class SkillRegistry:
    """Unified skill lookup — built-ins first, externals as fallback.

    Usage::

        registry = get_skill_registry()
        registry.register_external(my_skill)
        skill = registry.get("pair-programming")
    """

    def __init__(self):
        self._externals: Dict[str, ExternalSkill] = {}

    # -- external skill management --

    def register_external(self, skill: ExternalSkill) -> None:
        self._externals[skill.name] = skill

    def register_externals(self, skills: List[ExternalSkill]) -> None:
        for s in skills:
            self._externals[s.name] = s

    def unregister_external(self, name: str) -> bool:
        if name in self._externals:
            del self._externals[name]
            return True
        return False

    # -- lookup (built-ins first, then externals) --

    def get(self, name: str) -> Optional[Any]:
        """Return a skill by name.

        Checks built-in skills first (from ``bundled_skills.BUNDLED_SKILLS``),
        then falls back to externally-registered skills.
        """
        from .bundled_skills import BUNDLED_SKILLS
        builtin = BUNDLED_SKILLS.get(name)
        if builtin is not None:
            return builtin
        return self._externals.get(name)

    # -- listing --

    def list_names(self) -> List[str]:
        """Return all available skill names (built-ins + externals)."""
        from .bundled_skills import BUNDLED_SKILLS
        names = list(BUNDLED_SKILLS.keys())
        names.extend(self._externals.keys())
        return sorted(set(names))

    def list_all(self) -> List[Dict[str, Any]]:
        """Return metadata for all skills."""
        from .bundled_skills import BUNDLED_SKILLS
        result: List[Dict[str, Any]] = []
        for name, s in BUNDLED_SKILLS.items():
            result.append({
                "name": name,
                "description": s.description,
                "source": "builtin",
            })
        for name, s in self._externals.items():
            result.append({
                "name": name,
                "description": s.description,
                "source": s.source or "external",
            })
        return result

    @property
    def external_count(self) -> int:
        return len(self._externals)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_registry: Optional[SkillRegistry] = None


def get_skill_registry() -> SkillRegistry:
    """Return the global ``SkillRegistry`` singleton."""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry


def reset_skill_registry() -> None:
    """Reset the global registry (useful for testing)."""
    global _registry
    _registry = None
