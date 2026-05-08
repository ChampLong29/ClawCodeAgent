"""Skill runtime — discovers external .md skills from the filesystem.

Scans project-level directories for SKILL.md files in the Claude Code
compatible format (YAML frontmatter + Markdown body).  Discovered skills
are registered into the global ``SkillRegistry``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from .hook_policy import RuntimeBase
from .skill_registry import ExternalSkill, get_skill_registry


# ---------------------------------------------------------------------------
# YAML frontmatter parser (stdlib-only, no PyYAML dependency)
# ---------------------------------------------------------------------------

def _parse_skill_md(file_path: str) -> Optional[ExternalSkill]:
    """Parse a SKILL.md file into an ExternalSkill.

    Expected format::

        ---
        name: My Skill
        description: What it does
        parameters:           # optional JSON Schema
          code:
            type: string
        ---

        # My Skill

        Skill prompt body...
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return None

    # Extract YAML frontmatter between --- delimiters
    m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not m:
        return None

    frontmatter_str = m.group(1)

    # Parse frontmatter only — body is loaded lazily on first access
    fm = _parse_simple_yaml(frontmatter_str)

    name = fm.get("name", "")
    description = fm.get("description", "")

    if not name:
        name = os.path.basename(os.path.dirname(file_path))

    parameters = fm.get("parameters")
    if parameters is not None and not isinstance(parameters, dict):
        parameters = None

    return ExternalSkill(
        name=name,
        description=description,
        parameters=parameters,
        source=file_path,           # body 懒加载
    )


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    """Parse a minimal YAML subset: ``key: value`` pairs and nested dicts.

    Supports:
    - ``key: value``
    - ``key: "quoted value"``
    - Nested blocks via indentation (2-space indent)
    - JSON inline for complex values

    Does NOT support: lists, anchors, multiline strings (|, >).
    """
    result: Dict[str, Any] = {}
    current_path: List[str] = []
    current_dicts: List[Dict[str, Any]] = [result]

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Determine nesting level
        indent = len(line) - len(line.lstrip(" "))
        level = indent // 2

        # Pop back to correct nesting
        while len(current_dicts) > level + 1:
            current_dicts.pop()
            if current_path:
                current_path.pop()

        if ":" not in stripped:
            continue

        key, _, value_str = stripped.partition(":")
        key = key.strip()
        value_str = value_str.strip()

        # Remove surrounding quotes
        if len(value_str) >= 2 and value_str[0] == value_str[-1] and value_str[0] in ('"', "'"):
            value_str = value_str[1:-1]

        # Check if value is empty → start of nested block
        if value_str == "":
            nested: Dict[str, Any] = {}
            current_dicts[-1][key] = nested
            current_dicts.append(nested)
            current_path.append(key)
            continue

        # Try JSON parse for complex values
        if value_str.startswith("{") or value_str.startswith("["):
            try:
                value_str = json.loads(value_str)  # type: ignore[assignment]
            except json.JSONDecodeError:
                pass

        current_dicts[-1][key] = value_str

    return result


# ---------------------------------------------------------------------------
# SkillRuntime
# ---------------------------------------------------------------------------

class SkillRuntime(RuntimeBase):
    """Discovers external .md skills from the filesystem.

    Search paths (project-level only):
    1. ``<cwd>/.claw-skills/<name>/SKILL.md``
    2. ``<cwd>/.claw-skills/<name>.md``
    3. ``<cwd>/plugins/<name>/SKILL.md``
    """

    SEARCH_DIRS = [".claw-skills", "plugins"]
    SKILL_FILE = "SKILL.md"

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.skills: List[ExternalSkill] = []
        self._discover()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover(self) -> None:
        """Scan search directories and register discovered skills."""
        self.skills = []
        seen: set = set()

        for dir_name in self.SEARCH_DIRS:
            search_dir = os.path.join(self.cwd, dir_name)
            if not os.path.isdir(search_dir):
                continue

            for entry in sorted(os.listdir(search_dir)):
                entry_path = os.path.join(search_dir, entry)

                # Pattern 1: <dir>/<name>.md
                if os.path.isfile(entry_path) and entry.endswith(".md"):
                    skill = _parse_skill_md(entry_path)
                    if skill and skill.name not in seen:
                        self.skills.append(skill)
                        seen.add(skill.name)

                # Pattern 2: <dir>/<name>/SKILL.md
                elif os.path.isdir(entry_path):
                    skill_path = os.path.join(entry_path, self.SKILL_FILE)
                    if os.path.isfile(skill_path):
                        skill = _parse_skill_md(skill_path)
                        if skill and skill.name not in seen:
                            self.skills.append(skill)
                            seen.add(skill.name)

        # Register into global SkillRegistry
        if self.skills:
            registry = get_skill_registry()
            registry.register_externals(self.skills)

    # ------------------------------------------------------------------
    # RuntimeBase
    # ------------------------------------------------------------------

    def get_state(self) -> Optional[Dict[str, Any]]:
        return {
            "skills": [s.to_dict() for s in self.skills],
            "count": len(self.skills),
        }

    def render_summary(self) -> str:
        if not self.skills:
            return "[Skills] No external skills loaded"
        names = ", ".join(s.name for s in self.skills)
        return f"[Skills] {len(self.skills)} external: {names}"

    def get_prompt_guidance(self) -> str:
        if not self.skills:
            return ""
        lines = ["## Available External Skills\n"]
        for s in self.skills:
            lines.append(f"- **{s.name}**: {s.description}")
        return "\n".join(lines)
