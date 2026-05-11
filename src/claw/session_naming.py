"""Human-readable session ID generation from project goals."""

from __future__ import annotations

import re
import uuid


def make_session_id(goal: str, prefix: str = "") -> str:
    """Generate a human-readable session ID from a project goal.

    Examples::

        >>> make_session_id("Build a club management system")
        'club-management-system-a1b2'
        >>> make_session_id("实现学生社团管理系统")
        'project-a1b2'

    The result is filesystem-safe and includes a short hash suffix for
    uniqueness.
    """
    short_hash = str(uuid.uuid4())[:4]

    # Try to extract English / alphanumeric words
    ascii_words = re.findall(r'[a-zA-Z0-9]{2,}', goal)
    if ascii_words:
        base = "-".join(w.lower() for w in ascii_words[:5])
        # Cap at 50 chars
        if len(base) > 50:
            base = base[:50].rstrip("-")
        return f"{base}-{short_hash}"

    # For Chinese / non-ASCII goals, use a generic prefix
    if prefix:
        return f"{prefix}-{short_hash}"

    # Try to extract alphanumeric from mixed content
    fallback = re.sub(r'[^a-zA-Z0-9-]', '', goal)[:30].strip("-").lower()
    if fallback:
        return f"{fallback}-{short_hash}"

    return f"project-{short_hash}"


def make_project_dir_name(goal: str) -> str:
    """Generate a filesystem-safe project directory name from a goal.

    Examples::

        >>> make_project_dir_name("Build a club management system")
        'club-management-system'
        >>> make_project_dir_name("实现学生社团管理系统 web 端")
        'project-a1b2c3d4'
    """
    # Try English words first
    ascii_words = re.findall(r'[a-zA-Z0-9]{2,}', goal)
    if ascii_words:
        base = "-".join(w.lower() for w in ascii_words[:4])
        return base[:50].rstrip("-")

    # Fallback: project-<short_hash>
    return f"project-{str(uuid.uuid4())[:8]}"
