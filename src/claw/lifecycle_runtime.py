"""Lifecycle runtime — full software engineering lifecycle management.

Wraps DevFlow for development phases while adding requirements analysis,
system design, code review, testing, and acceptance phases.

Phase list is configurable via .claw-lifecycle.json. Default: full 10-phase lifecycle.
"""

from __future__ import annotations

import collections
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .hook_policy import RuntimeBase
from .session_naming import make_session_id, make_project_dir_name

if TYPE_CHECKING:
    from .agent_runtime import LocalCodingAgent


# ---------------------------------------------------------------------------
# Default lifecycle phases
# ---------------------------------------------------------------------------

DEFAULT_LIFECYCLE_PHASES = [
    "REQUIREMENTS",
    "SYSTEM_DESIGN",
    "ARCHITECTURE",
    "STEP_DEFINITION",
    "MODULE_ANALYSIS",
    "IMPLEMENTATION",
    "CODE_REVIEW",
    "UNIT_TEST",
    "INTEGRATION_TEST",
    "ACCEPTANCE",
]

# Phases that delegate to DevFlow
DEVFLOW_PHASES = {
    "ARCHITECTURE", "STEP_DEFINITION", "MODULE_ANALYSIS",
    "IMPLEMENTATION", "VERIFY",
}

# Phases that require write permissions
WRITE_PHASES = {"IMPLEMENTATION", "UNIT_TEST", "INTEGRATION_TEST"}

# Phases that support interactive questionnaire before execution
# When these phases are pending, the system will first ask the user
# clarifying questions, then feed the answers into the skill prompt.
QUESTIONNAIRE_PHASES = {"REQUIREMENTS", "SYSTEM_DESIGN", "ARCHITECTURE"}

# Phase-specific questionnaire prompts
_PHASE_QUESTIONNAIRE_PROMPTS = {
    "REQUIREMENTS": """You are helping to gather requirements for a software project.

## Project Goal
{goal}

## Instructions
Generate 5-8 clarifying questions to better define the requirements.
Focus on: users/roles, core features, scope boundaries, non-functional needs,
constraints, integration points, and success criteria.

Output as a JSON array:
```json
[
  {{"id": "q1", "text": "Who are the primary users of this system?"}},
  {{"id": "q2", "text": "What are the must-have vs nice-to-have features?"}}
]
```

Rules:
- Each question must have a unique id (q1, q2, ...).
- Questions should be specific and actionable.
- Only output the JSON array — no other text.""",

    "SYSTEM_DESIGN": """You are designing the architecture for a software system.

## Project Goal
{goal}

## Requirements Summary
{requirements_summary}

## Instructions
Generate 5-8 clarifying questions to make better architecture/design decisions.
Focus on: technology choices, deployment architecture, data storage, API design
patterns, authentication/authorization approach, scalability needs, and
integration with external systems.

Output as a JSON array:
```json
[
  {{"id": "q1", "text": "Which database technology best fits the data model?"}},
  {{"id": "q2", "text": "Should the system use a monolith or microservices?"}}
]
```

Rules:
- Each question must have a unique id (q1, q2, ...).
- Questions should be specific and actionable.
- Only output the JSON array — no other text.""",

    "ARCHITECTURE": """You are making architecture decisions for a software project.

## Project Goal
{goal}

## Requirements Summary
{requirements_summary}

## Design Summary
{design_summary}

## Instructions
Generate 5-8 clarifying questions to refine the architecture.
Focus on: component boundaries, data flow, caching strategy, error handling,
deployment topology, monitoring/observability, and security architecture.

Output as a JSON array:
```json
[
  {{"id": "q1", "text": "What caching strategy should be used?"}},
  {{"id": "q2", "text": "How should components communicate (REST, gRPC, message queue)?"}}
]
```

Rules:
- Each question must have a unique id (q1, q2, ...).
- Questions should be specific and actionable.
- Only output the JSON array — no other text.""",
}

# ---------------------------------------------------------------------------
# Phase-level tool access control (action masking)
# ---------------------------------------------------------------------------
# Each phase defines which tools are ALLOWED. Tools not in the allowed set
# are blocked (invisible to the model + rejected at execution time).
#
# This implements "hard constraints" as described in the project design:
# the agent literally cannot call write_file during requirements analysis,
# not because a prompt says "don't", but because the tool doesn't exist
# in its action space.
#
# For RL training: this is equivalent to action masking — illegal actions
# have probability 0 because they're not in the model's tool spec.
# ---------------------------------------------------------------------------

# All read-only tools (always available)
_READ_TOOLS = {"list_dir", "read_file", "glob_search", "grep_search", "web_search", "web_fetch", "use_skill"}

# Write tools (only available in write phases)
_WRITE_TOOLS = {"write_file", "edit_file"}

# Shell tool (only available in implementation/test phases)
_SHELL_TOOLS = {"bash"}

# Phase → set of allowed tool names
# Tools NOT in this set will be blocked (action masked)
PHASE_ALLOWED_TOOLS: Dict[str, set] = {
    # Analysis/design phases: read-only, no code writing, no shell
    "REQUIREMENTS":      _READ_TOOLS,
    "SYSTEM_DESIGN":     _READ_TOOLS,
    "ARCHITECTURE":      _READ_TOOLS,
    "STEP_DEFINITION":   _READ_TOOLS,
    "MODULE_ANALYSIS":   _READ_TOOLS,

    # Implementation phase: full access
    "IMPLEMENTATION":    _READ_TOOLS | _WRITE_TOOLS | _SHELL_TOOLS,

    # Review: read + shell (for running tests) but no writing
    "CODE_REVIEW":       _READ_TOOLS | _SHELL_TOOLS,

    # Test phases: full access (need to write test files + run them)
    "UNIT_TEST":         _READ_TOOLS | _WRITE_TOOLS | _SHELL_TOOLS,
    "INTEGRATION_TEST":  _READ_TOOLS | _WRITE_TOOLS | _SHELL_TOOLS,

    # Acceptance: read + shell (run final checks)
    "ACCEPTANCE":        _READ_TOOLS | _SHELL_TOOLS,
}

# Phase → skill mapping
PHASE_SKILL_MAP = {
    "REQUIREMENTS": "lifecycle-requirements",
    "SYSTEM_DESIGN": "lifecycle-design",
    "CODE_REVIEW": "lifecycle-code-review",
    "UNIT_TEST": "lifecycle-unit-test",
    "INTEGRATION_TEST": "lifecycle-integration-test",
    "ACCEPTANCE": "lifecycle-acceptance",
    # DevFlow phases use their own skills (devflow-*)
}

# Phase → artifact path template
PHASE_ARTIFACTS = {
    "REQUIREMENTS": "docs/requirements_{session_id}.md",
    "SYSTEM_DESIGN": "docs/design_{session_id}.md",
    "CODE_REVIEW": "docs/code-review_{session_id}.md",
    "UNIT_TEST": "docs/unit-test_{session_id}.md",
    "INTEGRATION_TEST": "docs/integration-test_{session_id}.md",
    "ACCEPTANCE": "docs/acceptance_{session_id}.md",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class LifecyclePhase:
    """A single phase in the lifecycle."""
    name: str
    status: str = "pending"  # pending | in_progress | completed | skipped | failed | awaiting_input
    output: Optional[str] = None
    artifact_path: Optional[str] = None
    # Interactive questionnaire state (for QUESTIONNAIRE_PHASES)
    questionnaire: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "name": self.name,
            "status": self.status,
            "output": self.output,
            "artifact_path": self.artifact_path,
        }
        if self.questionnaire:
            d["questionnaire"] = self.questionnaire
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LifecyclePhase:
        return cls(
            name=data.get("name", ""),
            status=data.get("status", "pending"),
            output=data.get("output"),
            artifact_path=data.get("artifact_path"),
            questionnaire=data.get("questionnaire"),
        )


@dataclass
class LifecycleSession:
    """A full lifecycle development session."""
    session_id: str
    overall_goal: str
    user_constraints: str = ""
    phases: List[LifecyclePhase] = field(default_factory=list)
    current_phase_index: int = 0
    devflow_session_id: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    completed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "overall_goal": self.overall_goal,
            "user_constraints": self.user_constraints,
            "phases": [p.to_dict() for p in self.phases],
            "current_phase_index": self.current_phase_index,
            "devflow_session_id": self.devflow_session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed": self.completed,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LifecycleSession:
        return cls(
            session_id=data.get("session_id", ""),
            overall_goal=data.get("overall_goal", ""),
            user_constraints=data.get("user_constraints", ""),
            phases=[LifecyclePhase.from_dict(p) for p in data.get("phases", [])],
            current_phase_index=data.get("current_phase_index", 0),
            devflow_session_id=data.get("devflow_session_id"),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            completed=data.get("completed", False),
        )

    def get_current_phase(self) -> Optional[LifecyclePhase]:
        """Get the current active phase."""
        if 0 <= self.current_phase_index < len(self.phases):
            return self.phases[self.current_phase_index]
        return None

    def progress(self) -> Dict[str, Any]:
        """Get progress statistics."""
        total = len(self.phases)
        if total == 0:
            return {"total": 0, "completed": 0, "in_progress": 0, "pending": 0, "percent": 0}

        completed = sum(1 for p in self.phases if p.status == "completed")
        in_progress = sum(1 for p in self.phases if p.status == "in_progress")
        pending = sum(1 for p in self.phases if p.status == "pending")
        skipped = sum(1 for p in self.phases if p.status == "skipped")

        return {
            "total": total,
            "completed": completed,
            "in_progress": in_progress,
            "pending": pending,
            "skipped": skipped,
            "percent": int((completed / total) * 100),
        }

    def get_phase_by_name(self, name: str) -> Optional[LifecyclePhase]:
        """Get a phase by its name."""
        for p in self.phases:
            if p.name == name:
                return p
        return None

    def get_completed_output(self, phase_name: str) -> str:
        """Get the output of a completed phase for context."""
        phase = self.get_phase_by_name(phase_name)
        if phase and phase.output:
            return phase.output
        return "Not available."


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class LifecycleRuntime(RuntimeBase):
    """Lifecycle runtime — manages the full software engineering lifecycle.

    Usage:
        rt = LifecycleRuntime(cwd=".")
        session = rt.start_session("Build user auth system")
        rt.execute_phase(agent)    # Run current phase
        rt.advance_phase()         # Accept and move to next phase
    """

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.session: Optional[LifecycleSession] = None
        self._sessions_dir = os.path.join(cwd, ".port_sessions", "lifecycle")
        self._phase_config: Optional[List[str]] = None
        self._skip_phases: List[str] = []
        self._reviewer_config: Dict[str, Any] = {
            "enabled": True,
            "auto_review_phases": ["IMPLEMENTATION", "CODE_REVIEW"],
            "strictness": "normal",
        }
        self._sandbox_config: Dict[str, Any] = {
            "enabled": True,
            "seatbelt": True,
            "git_tracking": True,
            "timeout": 120,
        }
        self._sandbox: Any = None  # WorkspaceSandbox instance
        self._project_dir: Optional[str] = None

        from .context_manager import ContextManager
        self.context_manager = ContextManager()
        self._completed_phase_outputs: Dict[str, str] = {}

        os.makedirs(self._sessions_dir, exist_ok=True)
        self._discover_config()

    # ------------------------------------------------------------------
    # Config discovery
    # ------------------------------------------------------------------

    def _discover_config(self) -> None:
        """Discover .claw-lifecycle.json configuration."""
        config_path = os.path.join(self.cwd, ".claw-lifecycle.json")
        if not os.path.isfile(config_path):
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if "phases" in data and isinstance(data["phases"], list):
                self._phase_config = data["phases"]
            if "skip_phases" in data and isinstance(data["skip_phases"], list):
                self._skip_phases = data["skip_phases"]
            if "reviewer" in data and isinstance(data["reviewer"], dict):
                rv = data["reviewer"]
                self._reviewer_config["enabled"] = rv.get("enabled", True)
                if "auto_review_phases" in rv:
                    self._reviewer_config["auto_review_phases"] = rv["auto_review_phases"]
                if "strictness" in rv:
                    self._reviewer_config["strictness"] = rv["strictness"]
            if "sandbox" in data and isinstance(data["sandbox"], dict):
                sb = data["sandbox"]
                self._sandbox_config["enabled"] = sb.get("enabled", True)
                self._sandbox_config["seatbelt"] = sb.get("seatbelt", True)
                self._sandbox_config["git_tracking"] = sb.get("git_tracking", True)
                if "timeout" in sb:
                    self._sandbox_config["timeout"] = sb["timeout"]
        except (json.JSONDecodeError, OSError):
            pass

    def get_phase_list(self) -> List[str]:
        """Get the configured phase list (or default)."""
        if self._phase_config:
            return self._phase_config
        return list(DEFAULT_LIFECYCLE_PHASES)

    # ------------------------------------------------------------------
    # Action masking — hard tool constraints per phase
    # ------------------------------------------------------------------

    def get_blocked_tools_for_phase(self, phase_name: str) -> List[str]:
        """Compute which tools should be BLOCKED for a given phase.

        This implements action masking: tools not in PHASE_ALLOWED_TOOLS
        for the current phase are returned here and should be passed to
        agent.set_blocked_tools() before execution.

        Returns:
            List of tool names to block. Empty list means no restrictions.
        """
        allowed = PHASE_ALLOWED_TOOLS.get(phase_name)
        if allowed is None:
            # Unknown phase — no restrictions (fail-open for custom phases)
            return []

        # Get all registered tool names
        from .agent_tools import default_tool_registry
        registry = default_tool_registry()
        all_tools = set(registry.list_tool_names())

        # Block = all tools minus allowed tools
        blocked = all_tools - allowed
        return sorted(blocked)

    def apply_phase_constraints(self, agent: 'LocalCodingAgent', phase_name: str) -> None:
        """Apply hard tool constraints for a phase to an agent instance.

        This is the primary enforcement point: before executing any phase,
        call this method to configure the agent's action space.

        Effects:
        1. Sets agent.blocked_tools (tools invisible to model + rejected at runtime)
        2. Sets agent.permissions.allow_write based on WRITE_PHASES
        3. Sets agent.permissions.allow_shell based on phase
        """
        # 1. Block tools not allowed in this phase
        blocked = self.get_blocked_tools_for_phase(phase_name)
        agent.set_blocked_tools(blocked)

        # 2. Set write permission
        is_write_phase = phase_name in WRITE_PHASES
        if agent.permissions:
            agent.permissions["allow_write"] = is_write_phase

        # 3. Set shell permission (phases with bash in allowed set)
        allowed = PHASE_ALLOWED_TOOLS.get(phase_name, set())
        has_shell = "bash" in allowed
        if agent.permissions:
            agent.permissions["allow_shell"] = has_shell

    def release_phase_constraints(self, agent: 'LocalCodingAgent') -> None:
        """Release all phase constraints (restore full tool access).

        Call after phase execution if the agent will be reused outside
        lifecycle control (e.g., for interactive chat).
        """
        agent.clear_blocked_tools()
        if agent.permissions:
            agent.permissions["allow_write"] = False
            agent.permissions["allow_shell"] = False

    def is_reviewer_enabled(self) -> bool:
        """Check if Reviewer is enabled globally."""
        return bool(self._reviewer_config.get("enabled", True))

    def set_reviewer_enabled(self, enabled: bool) -> None:
        """Enable or disable the Reviewer."""
        self._reviewer_config["enabled"] = enabled

    def is_reviewer_enabled_for(self, phase_name: str) -> bool:
        """Check if Reviewer should auto-run for a specific phase."""
        if not self.is_reviewer_enabled():
            return False
        auto_phases = self._reviewer_config.get("auto_review_phases", [])
        return phase_name in auto_phases

    def get_reviewer_config(self) -> Dict[str, Any]:
        """Get the full reviewer configuration."""
        return dict(self._reviewer_config)

    def get_sandbox(self) -> Any:
        """Return the WorkspaceSandbox instance (or None)."""
        return self._sandbox

    def get_sandbox_config(self) -> Dict[str, Any]:
        """Get sandbox configuration."""
        return dict(self._sandbox_config)

    def is_phase_skipped(self, phase_name: str) -> bool:
        """Check if a phase is configured to be skipped."""
        return phase_name in self._skip_phases

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(
        self,
        goal: str,
        constraints: str = "",
        phase_list: Optional[List[str]] = None,
    ) -> LifecycleSession:
        """Start a new lifecycle session.

        Creates a dedicated project directory under
        ``<cwd>/projects/<project-name>/`` for all generated artifacts.

        Args:
            goal: The overall development goal.
            constraints: User-specified constraints.
            phase_list: Optional custom phase list (overrides config).
        """
        session_id = make_session_id(goal, "lifecycle")
        now = time.time()

        # Create a dedicated project directory with sandbox isolation
        proj_name = make_project_dir_name(goal)
        proj_dir = os.path.join(self.cwd, "projects", proj_name)
        os.makedirs(proj_dir, exist_ok=True)

        from .sandbox import WorkspaceSandbox, SandboxConfig
        sb_config = SandboxConfig(
            enabled=self._sandbox_config.get("enabled", True),
            seatbelt=self._sandbox_config.get("seatbelt", True),
            git_tracking=self._sandbox_config.get("git_tracking", True),
            timeout=self._sandbox_config.get("timeout", 120),
        )
        self._sandbox = WorkspaceSandbox(cwd=proj_dir, config=sb_config)
        self._project_dir = self._sandbox.cwd

        phases_to_use = phase_list or self.get_phase_list()

        # Build phases, marking skipped ones
        phases = []
        for name in phases_to_use:
            if self.is_phase_skipped(name):
                phases.append(LifecyclePhase(name=name, status="skipped"))
            else:
                phases.append(LifecyclePhase(name=name, status="pending"))

        self.session = LifecycleSession(
            session_id=session_id,
            overall_goal=goal,
            user_constraints=constraints,
            phases=phases,
            created_at=now,
            updated_at=now,
        )

        # Advance to first non-skipped phase
        self._advance_to_next_phase()
        self.save()
        return self.session

    def get_project_dir(self) -> Optional[str]:
        """Return the dedicated project directory, if created."""
        return self._project_dir

    def get_session(self) -> Optional[LifecycleSession]:
        """Get the current session."""
        return self.session

    def has_active_session(self) -> bool:
        """Check if there is an active session."""
        return self.session is not None and not self.session.completed

    # ------------------------------------------------------------------
    # Phase navigation
    # ------------------------------------------------------------------

    def get_current_phase(self) -> Optional[LifecyclePhase]:
        """Get the currently active phase."""
        if not self.session:
            return None
        return self.session.get_current_phase()

    def _advance_to_next_phase(self) -> bool:
        """Find the next non-skipped, non-completed phase. Returns False if done."""
        if not self.session:
            return False

        phases = self.session.phases
        start = self.session.current_phase_index

        for i in range(start, len(phases)):
            if phases[i].status in ("pending",):
                self.session.current_phase_index = i
                return True

        # All done
        self.session.completed = True
        return False

    def advance_phase(self, agent_session=None) -> bool:
        """Mark current phase complete and move to next. Returns False if done.

        Args:
            agent_session: Optional AgentSession for phase-boundary compaction
                           and snapshot save.
        """
        if not self.session:
            raise RuntimeError("No active lifecycle session.")

        phase = self.session.get_current_phase()
        if phase and phase.status == "in_progress":
            phase.status = "completed"
            # Store output for cross-phase context injection
            if phase.output:
                self._completed_phase_outputs[phase.name] = phase.output

        # Save snapshot before advancing (so we can rollback to this point)
        if phase and phase.status == "completed":
            self._save_phase_snapshot(agent_session)
            # Also commit code changes in sandbox
            if self._sandbox and self._sandbox.config.git_tracking:
                self._sandbox.save_phase_snapshot(phase.name)

        has_next = self._advance_to_next_phase()

        # Mark next phase boundary in agent session
        if has_next and agent_session is not None:
            next_phase = self.session.get_current_phase()
            if next_phase:
                agent_session.mark_phase_boundary(next_phase.name)

        self.session.updated_at = time.time()
        self.save()
        return has_next

    def skip_phase(self, agent_session=None) -> bool:
        """Skip the current phase and advance."""
        if not self.session:
            raise RuntimeError("No active lifecycle session.")

        phase = self.session.get_current_phase()
        if phase:
            phase.status = "skipped"
            phase.output = "Skipped by user."

        has_next = self._advance_to_next_phase()

        if has_next and agent_session is not None:
            next_phase = self.session.get_current_phase()
            if next_phase:
                agent_session.mark_phase_boundary(next_phase.name)

        self.session.updated_at = time.time()
        self.save()
        return has_next

    def retry_phase(self) -> None:
        """Reset the current phase to pending for retry."""
        if not self.session:
            raise RuntimeError("No active lifecycle session.")

        phase = self.session.get_current_phase()
        if phase:
            phase.status = "pending"
            phase.output = None

        self.session.updated_at = time.time()
        self.save()

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def list_rollback_targets(self) -> List[Dict[str, Any]]:
        """List phases that can be rolled back to.

        Returns a list of dicts with *name*, *index*, *status*, and
        *snapshot_exists* keys.
        """
        if not self.session:
            return []

        targets = []
        snapshot_dir = os.path.join(
            self._sessions_dir, f"{self.session.session_id}_snapshots"
        )

        for i, phase in enumerate(self.session.phases):
            if i >= self.session.current_phase_index:
                break  # only show past phases
            snapshot_path = os.path.join(
                snapshot_dir, f"phase_{i}_{phase.name}.json"
            )
            targets.append({
                "name": phase.name,
                "index": i,
                "status": phase.status,
                "snapshot_exists": os.path.isfile(snapshot_path),
            })

        return targets

    def rollback_to_phase(self, phase_name: str, agent_session=None) -> bool:
        """Roll back to a specific phase by name.

        Restores the lifecycle session (and optionally the agent session)
        from the snapshot saved at that phase's completion.  All subsequent
        phases are reset to *pending*.

        Returns ``True`` on success, ``False`` if no snapshot exists.
        """
        if not self.session:
            raise RuntimeError("No active lifecycle session.")

        # Find phase index
        target_idx = None
        for i, phase in enumerate(self.session.phases):
            if phase.name == phase_name:
                target_idx = i
                break

        if target_idx is None:
            return False

        return self.rollback_to_phase_index(target_idx, agent_session)

    def rollback_to_phase_index(
        self, index: int, agent_session=None
    ) -> bool:
        """Roll back to a phase by index (0-based)."""
        if not self.session:
            raise RuntimeError("No active lifecycle session.")

        if index < 0 or index >= len(self.session.phases):
            return False

        # Try to restore from snapshot
        snapshot_dir = os.path.join(
            self._sessions_dir, f"{self.session.session_id}_snapshots"
        )
        target_phase = self.session.phases[index]
        snapshot_path = os.path.join(
            snapshot_dir, f"phase_{index}_{target_phase.name}.json"
        )

        if os.path.isfile(snapshot_path):
            self._restore_from_snapshot(snapshot_path, agent_session)
        else:
            self._manual_rollback_to(index)

        # Reset the target phase so it can be re-executed
        target_phase = self.session.phases[index]
        target_phase.status = "pending"
        target_phase.output = None
        target_phase.artifact_path = None

        # Reset all phases after target
        for i in range(index + 1, len(self.session.phases)):
            self.session.phases[i].status = "pending"
            self.session.phases[i].output = None
            self.session.phases[i].artifact_path = None

        self.session.current_phase_index = index
        self.session.completed = False
        self.session.updated_at = time.time()

        # Clear completed outputs after rollback point
        phase_names_after = {
            self.session.phases[i].name
            for i in range(index + 1, len(self.session.phases))
        }
        self._completed_phase_outputs = {
            k: v for k, v in self._completed_phase_outputs.items()
            if k not in phase_names_after
        }

        self.save()

        # Also rollback code files in sandbox
        if self._sandbox and self._sandbox.config.git_tracking:
            # Try resetting to the target phase's git commit
            reset_ok = self._sandbox.reset_to_phase(target_phase.name)
            if reset_ok:
                self._log_rollback(target_phase.name, index)
                return True
            # Fallback: if no git commit for this phase, reset to initial
            self._sandbox.reset_to_phase("initial") if "initial" in getattr(
                self._sandbox, "_commits", {}
            ) else None

        self._log_rollback(target_phase.name, index)
        return True

    def _manual_rollback_to(self, index: int) -> None:
        """Reset state without a snapshot file (fallback)."""
        if not self.session:
            return
        target_phase = self.session.phases[index]
        target_phase.status = "pending"
        target_phase.output = None
        target_phase.artifact_path = None

    def _save_phase_snapshot(
        self, agent_session=None
    ) -> Optional[str]:
        """Save a snapshot of the current session state.

        Called automatically by ``advance_phase()``.  The snapshot includes
        the full LifecycleSession state and (if provided) the compacted
        AgentSession messages.
        """
        if not self.session:
            return None

        phase = self.session.get_current_phase()
        if not phase:
            return None

        snapshot_dir = os.path.join(
            self._sessions_dir, f"{self.session.session_id}_snapshots"
        )
        os.makedirs(snapshot_dir, exist_ok=True)

        snapshot: Dict[str, Any] = {
            "phase_name": phase.name,
            "phase_index": self.session.current_phase_index,
            "lifecycle_session": self.session.to_dict(),
            "completed_phase_outputs": dict(self._completed_phase_outputs),
            "timestamp": time.time(),
        }

        if agent_session is not None:
            snapshot["agent_messages"] = agent_session.messages
            snapshot["agent_phase_boundaries"] = dict(
                agent_session.phase_boundaries
            )

        path = os.path.join(
            snapshot_dir,
            f"phase_{self.session.current_phase_index}_{phase.name}.json",
        )
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)

        return path

    def _restore_from_snapshot(
        self, snapshot_path: str, agent_session=None
    ) -> None:
        """Restore lifecycle (and optionally agent) session from a snapshot."""
        with open(snapshot_path, "r", encoding="utf-8") as f:
            snapshot = json.load(f)

        # Restore LifecycleSession
        self.session = LifecycleSession.from_dict(
            snapshot["lifecycle_session"]
        )
        self._completed_phase_outputs = snapshot.get(
            "completed_phase_outputs", {}
        )

        # Restore AgentSession if provided
        if agent_session is not None and "agent_messages" in snapshot:
            agent_session.messages = snapshot["agent_messages"]
            agent_session.phase_boundaries = snapshot.get(
                "agent_phase_boundaries", {}
            )

    def _log_rollback(self, phase_name: str, index: int) -> None:
        """Write a rollback event to the audit log."""
        if not self.session:
            return
        log_path = os.path.join(
            self._sessions_dir,
            f"{self.session.session_id}_rollbacks.jsonl",
        )
        entry = {
            "event": "rollback",
            "phase_name": phase_name,
            "phase_index": index,
            "timestamp": time.time(),
        }
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass  # best-effort logging

    # ------------------------------------------------------------------
    # Phase execution
    # ------------------------------------------------------------------

    def execute_phase(self, agent: LocalCodingAgent, questionnaire_results: str = "") -> str:
        """Run the agent for the current phase.

        Returns the agent's output. For read-only phases, uses the
        lifecycle skill prompt. For DevFlow phases, delegates to DevFlow.
        For write phases, temporarily enables write permissions.

        Args:
            agent: The agent instance to run.
            questionnaire_results: Optional compiled answers from an
                interactive questionnaire, injected as extra context.
        """
        if not self.session:
            raise RuntimeError("No active lifecycle session.")

        phase = self.session.get_current_phase()
        if not phase:
            raise RuntimeError("No current phase to execute.")

        # Check if this is a DevFlow phase → delegate
        if phase.name in DEVFLOW_PHASES:
            return self._execute_devflow_phase(agent, phase)

        # Get the appropriate skill
        skill_name = PHASE_SKILL_MAP.get(phase.name)
        if not skill_name:
            raise RuntimeError(f"No skill defined for phase '{phase.name}'")

        from .bundled_skills import get_skill
        skill = get_skill(skill_name)
        if not skill:
            raise RuntimeError(f"Skill '{skill_name}' not found")

        # Build prompt context (use defaultdict so templates can safely
        # reference optional keys like {questionnaire_results} without KeyError)
        requirements_summary = self.session.get_completed_output("REQUIREMENTS")
        implementation_summary = self.session.get_completed_output("IMPLEMENTATION")
        design_summary = self.session.get_completed_output("SYSTEM_DESIGN")

        format_kwargs: Dict[str, str] = collections.defaultdict(str, dict(
            goal=self.session.overall_goal,
            constraints=self.session.user_constraints or "None",
            requirements_summary=requirements_summary,
            implementation_summary=implementation_summary,
            design_summary=design_summary,
            questionnaire_results=questionnaire_results or "",
        ))
        prompt = skill.prompt.format_map(format_kwargs)

        # Apply hard tool constraints for this phase (action masking)
        self.apply_phase_constraints(agent, phase.name)

        try:
            result = agent.run(prompt=prompt, stream=False)
            output = result.final_message or ""
            # Detect truncated / incomplete output
            if result.stop_reason in ("stopped", "budget_exceeded"):
                warning = (
                    f"\n\n⚠️ **WARNING: Output may be truncated** "
                    f"(stop_reason: {result.stop_reason}). "
                    f"Consider increasing --max-turns or budget, "
                    f"then use /lifecycle reject to retry."
                )
                output += warning
        finally:
            # Release constraints after phase execution
            self.release_phase_constraints(agent)

        # Store output
        phase.output = output
        phase.status = "in_progress"

        # Save artifact if applicable
        artifact_template = PHASE_ARTIFACTS.get(phase.name)
        if artifact_template and output:
            base_dir = self._project_dir or self.cwd
            artifact_path = os.path.join(
                base_dir,
                artifact_template.format(session_id=self.session.session_id),
            )
            os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
            with open(artifact_path, "w", encoding="utf-8") as f:
                f.write(output)
            phase.artifact_path = artifact_path

        self.session.updated_at = time.time()
        self.save()
        return output

    # ------------------------------------------------------------------
    # Interactive questionnaire (auto-triggered for decision phases)
    # ------------------------------------------------------------------

    def phase_supports_questionnaire(self) -> bool:
        """Check if the current phase supports interactive Q&A."""
        if not self.session:
            return False
        phase = self.session.get_current_phase()
        return phase is not None and phase.name in QUESTIONNAIRE_PHASES

    def has_phase_questionnaire(self) -> bool:
        """Check if the current phase already has questionnaire data."""
        phase = self.session.get_current_phase() if self.session else None
        return phase is not None and bool(phase.questionnaire)

    def generate_phase_questionnaire(self, agent: LocalCodingAgent) -> List[Dict]:
        """Generate clarifying questions for the current phase.

        Runs the agent with a phase-specific prompt to produce a list of
        questions, then stores them on the phase.  Sets phase status to
        ``"awaiting_input"``.

        Returns the question list.
        """
        if not self.session:
            raise RuntimeError("No active lifecycle session.")
        phase = self.session.get_current_phase()
        if not phase:
            raise RuntimeError("No current phase.")

        prompt_template = _PHASE_QUESTIONNAIRE_PROMPTS.get(phase.name)
        if not prompt_template:
            raise RuntimeError(f"No questionnaire prompt for phase '{phase.name}'")

        prompt = prompt_template.format(
            goal=self.session.overall_goal,
            requirements_summary=self.session.get_completed_output("REQUIREMENTS") or "",
            design_summary=self.session.get_completed_output("SYSTEM_DESIGN") or "",
        )

        # Generate questions (read-only, quick LLM call)
        self.apply_phase_constraints(agent, phase.name)
        try:
            result = agent.run(prompt=prompt, stream=False)
            raw = result.final_message or ""
        finally:
            self.release_phase_constraints(agent)

        # Parse JSON from agent output
        questions = self._parse_questionnaire_json(raw)
        if not questions:
            raise RuntimeError(f"Failed to parse questionnaire JSON from agent output")

        phase.questionnaire = {
            "questions": questions,
            "current_index": 0,
        }
        phase.status = "awaiting_input"
        self.session.updated_at = time.time()
        self.save()
        return questions

    def _parse_questionnaire_json(self, raw: str) -> List[Dict]:
        """Extract a JSON question array from agent output."""
        # Try to find JSON block first
        m = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', raw, re.DOTALL)
        if m:
            raw = m.group(1)
        data = json.loads(raw)
        if isinstance(data, list):
            return [{"id": item.get("id", f"q{i+1}"), "text": item["text"]}
                    for i, item in enumerate(data) if "text" in item]
        return []

    def get_current_phase_question(self) -> Optional[Dict]:
        """Return the current question dict, or None."""
        phase = self.session.get_current_phase() if self.session else None
        qdata = phase.questionnaire if phase else None
        if not qdata:
            return None
        idx = qdata.get("current_index", 0)
        questions = qdata.get("questions", [])
        if 0 <= idx < len(questions):
            return questions[idx]
        return None

    def has_more_phase_questions(self) -> bool:
        """Check if there are unanswered questions remaining."""
        q = self.get_current_phase_question()
        return q is not None

    def answer_current_phase_question(self, answer: str) -> bool:
        """Record an answer for the current question and advance.

        Returns True if there are more questions, False if this was the last.
        """
        phase = self.session.get_current_phase() if self.session else None
        if not phase or not phase.questionnaire:
            return False
        idx = phase.questionnaire["current_index"]
        questions = phase.questionnaire["questions"]
        if 0 <= idx < len(questions):
            questions[idx]["answer"] = answer
        phase.questionnaire["current_index"] = idx + 1
        self.session.updated_at = time.time()
        self.save()
        return self.has_more_phase_questions()

    def skip_current_phase_question(self) -> bool:
        """Skip the current question (no answer recorded).

        Returns True if there are more questions, False if this was the last.
        """
        phase = self.session.get_current_phase() if self.session else None
        if not phase or not phase.questionnaire:
            return False
        idx = phase.questionnaire["current_index"]
        phase.questionnaire["current_index"] = idx + 1
        self.session.updated_at = time.time()
        self.save()
        return self.has_more_phase_questions()

    def phase_questionnaire_progress(self) -> Dict[str, int]:
        """Return {total, current_index, answered, skipped, pending}."""
        phase = self.session.get_current_phase() if self.session else None
        qdata = phase.questionnaire if phase else None
        if not qdata:
            return {"total": 0, "current_index": 0, "answered": 0, "skipped": 0, "pending": 0}
        questions = qdata.get("questions", [])
        idx = qdata.get("current_index", 0)
        answered = sum(1 for q in questions[:idx] if q.get("answer"))
        skipped = sum(1 for q in questions[:idx] if not q.get("answer"))
        pending = len(questions) - idx
        return {
            "total": len(questions),
            "current_index": idx + 1,  # 1-based for display
            "answered": answered,
            "skipped": skipped,
            "pending": pending,
        }

    def finalize_phase_questionnaire(self) -> str:
        """Compile all answers into a Markdown context string.

        Resets questionnaire state and sets phase back to ``"pending"``
        so the main ``execute_phase()`` can proceed with enriched context.
        """
        phase = self.session.get_current_phase() if self.session else None
        if not phase or not phase.questionnaire:
            return ""

        questions = phase.questionnaire.get("questions", [])
        answered = [q for q in questions if q.get("answer")]
        if not answered:
            return ""

        lines = ["## 用户澄清答复 (Interactive Questionnaire)", ""]
        for q in answered:
            lines.append(f"**Q: {q['text']}**")
            lines.append(f"A: {q['answer']}")
            lines.append("")

        # Reset questionnaire state; restore phase to pending for execution
        phase.questionnaire = None
        phase.status = "pending"
        self.session.updated_at = time.time()
        self.save()
        return "\n".join(lines).strip()

    def cancel_phase_questionnaire(self) -> None:
        """Cancel the questionnaire and reset phase to pending."""
        phase = self.session.get_current_phase() if self.session else None
        if phase:
            phase.questionnaire = None
            phase.status = "pending"
        self.session.updated_at = time.time()
        self.save()

    def _execute_devflow_phase(self, agent: LocalCodingAgent, phase: LifecyclePhase) -> str:
        """Delegate execution to DevFlow for development phases.

        If no DevFlow session exists yet, creates one. If one exists,
        resumes it and advances its phase.
        """
        from .devflow_runtime import DevFlowRuntime

        devflow_rt = DevFlowRuntime(cwd=self.cwd)

        if self.session.devflow_session_id:
            # Resume existing DevFlow session
            existing = devflow_rt.load(self.session.devflow_session_id)
            if not existing:
                # Session lost, create new
                devflow_rt.start_session(
                    self.session.overall_goal,
                    self.session.user_constraints,
                )
        else:
            # Create new DevFlow session
            devflow_rt.start_session(
                self.session.overall_goal,
                self.session.user_constraints,
            )
            self.session.devflow_session_id = devflow_rt.session.session_id
            self.save()

        # Run the appropriate DevFlow phase
        devflow_session = devflow_rt.get_session()
        if not devflow_session:
            return "Error: DevFlow session not available."

        output = ""

        if phase.name == "ARCHITECTURE":
            if devflow_session.phase == "ARCHITECTURE" and not devflow_session.architecture:
                output = devflow_rt.propose_architecture(agent)
                devflow_rt.approve_architecture()
            else:
                output = devflow_session.architecture or "Architecture already generated."

        elif phase.name == "STEP_DEFINITION":
            if devflow_session.phase == "STEP_DEFINITION" and not devflow_session.steps:
                devflow_rt.generate_steps(agent)
                devflow_rt.approve_steps()
                output = f"Generated {len(devflow_session.steps)} steps."
            else:
                output = f"Steps already generated: {len(devflow_session.steps)} steps."

        elif phase.name == "MODULE_ANALYSIS":
            step = devflow_session.get_current_step()
            if step and not step.has_modules():
                devflow_rt.analyze_step(agent)
                devflow_rt.approve_modules()
                output = f"Generated {len(step.modules)} modules for step '{step.title}'."
            elif step:
                output = f"Modules already generated: {len(step.modules)} modules."

        elif phase.name == "IMPLEMENTATION":
            # Run through all DevFlow implementation steps
            results = []
            while not devflow_session.completed and devflow_session.phase != "DONE":
                step = devflow_session.get_current_step()
                if not step:
                    break

                step_output = devflow_rt.execute_step(agent)
                results.append(f"Step '{step.title}': {step_output[:500]}...")

                # Verify
                if step.status == "implemented":
                    devflow_rt.verify_step(agent)

                # Advance
                if step.status == "verified":
                    devflow_rt.next_step()

            output = "\n\n".join(results) if results else "Implementation complete."

        else:
            output = f"DevFlow phase '{phase.name}' handled automatically."

        phase.output = output
        phase.status = "in_progress"
        self.session.updated_at = time.time()
        self.save()

        return output

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _session_path(self, session_id: Optional[str] = None) -> str:
        sid = session_id or (self.session.session_id if self.session else None)
        if not sid:
            raise RuntimeError("No session ID available.")
        return os.path.join(self._sessions_dir, f"{sid}.json")

    def save(self) -> None:
        """Persist the current session to disk."""
        if not self.session:
            return
        path = self._session_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.session.to_dict(), f, indent=2, ensure_ascii=False)

    def load(self, session_id: str) -> Optional[LifecycleSession]:
        """Load a session from disk."""
        path = self._session_path(session_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.session = LifecycleSession.from_dict(data)
        return self.session

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all saved lifecycle sessions."""
        sessions = []
        if not os.path.isdir(self._sessions_dir):
            return sessions
        for fname in os.listdir(self._sessions_dir):
            if fname.endswith(".json"):
                path = os.path.join(self._sessions_dir, fname)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    sessions.append({
                        "session_id": data.get("session_id", fname[:-5]),
                        "overall_goal": data.get("overall_goal", ""),
                        "completed": data.get("completed", False),
                        "created_at": data.get("created_at", 0),
                        "phase_count": len(data.get("phases", [])),
                        "current_phase": (
                            data["phases"][data["current_phase_index"]]["name"]
                            if data.get("phases") and data.get("current_phase_index", 0) < len(data["phases"])
                            else "DONE"
                        ),
                    })
                except (json.JSONDecodeError, OSError):
                    pass
        sessions.sort(key=lambda s: s.get("created_at", 0), reverse=True)
        return sessions

    def archive(self, output_path: Optional[str] = None) -> str:
        """Archive the full lifecycle session to a Markdown report."""
        if not self.session:
            raise RuntimeError("No active session to archive.")

        path = output_path or os.path.join(
            self.cwd, f"lifecycle-{self.session.session_id}.md"
        )

        lines = [
            f"# Lifecycle Report: {self.session.overall_goal}",
            "",
            f"- **Session ID**: {self.session.session_id}",
            f"- **Completed**: {self.session.completed}",
            f"- **Created**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.session.created_at))}",
            "",
            "## Phase Summary",
            "",
            "| # | Phase | Status | Artifact |",
            "|---|-------|--------|----------|",
        ]

        status_icons = {
            "pending": "◇",
            "in_progress": "▶",
            "completed": "✅",
            "skipped": "⏭️",
            "failed": "✖",
        }

        for i, phase in enumerate(self.session.phases):
            icon = status_icons.get(phase.status, "?")
            artifact = phase.artifact_path or "-"
            lines.append(f"| {i+1} | {icon} {phase.name} | {phase.status} | {artifact} |")

        lines.append("")

        # Include phase outputs
        for phase in self.session.phases:
            if phase.output:
                lines.append(f"## {phase.name}")
                lines.append("")
                lines.append(phase.output)
                lines.append("")

        content = "\n".join(lines)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    # ------------------------------------------------------------------
    # RuntimeBase interface
    # ------------------------------------------------------------------

    def get_state(self) -> Dict[str, Any]:
        """Get current state for introspection."""
        if not self.session:
            return {
                "active": False,
                "configured_phases": self.get_phase_list(),
                "skip_phases": self._skip_phases,
                "phase_count": len(self.get_phase_list()),
            }

        return {
            "active": True,
            "session": self.session.to_dict(),
            "progress": self.session.progress(),
            "configured_phases": self.get_phase_list(),
            "skip_phases": self._skip_phases,
            "phase_count": len(self.get_phase_list()),
        }

    def render_summary(self) -> str:
        """Render a one-line summary for context injection."""
        if not self.session:
            return ""

        progress = self.session.progress()
        phase = self.session.get_current_phase()
        phase_name = phase.name if phase else "DONE"
        return f"[Lifecycle] {progress['completed']}/{progress['total']} phases — {phase_name}"

    def get_prompt_guidance(self) -> str:
        """Get phase-specific guidance injected into the system prompt."""
        if not self.session:
            return ""

        phase = self.session.get_current_phase()
        if not phase:
            return f"""[Lifecycle - DONE]
All lifecycle phases complete. Goal: {self.session.overall_goal}"""

        completed_phases = [p.name for p in self.session.phases if p.status == "completed"]
        pending_phases = [p.name for p in self.session.phases if p.status == "pending"]

        return f"""[Lifecycle - {phase.name} Phase]
You are in the **{phase.name}** phase of a structured software engineering lifecycle.

**Overall Goal**: {self.session.overall_goal}
**Completed Phases**: {', '.join(completed_phases) if completed_phases else 'None'}
**Upcoming Phases**: {', '.join(pending_phases[:5]) if pending_phases else 'None'}

**Previous Phase Outputs**:
{self._build_context_summary()}

Focus ONLY on the current phase ({phase.name}). Use the available tools to
produce the required artifacts. Do NOT implement anything unless you are
in the IMPLEMENTATION or test phases."""

    def _build_context_summary(self) -> str:
        """Build a summary of completed phases for context."""
        if not self.session:
            return ""

        phase = self.session.get_current_phase()
        current_name = phase.name if phase else ""
        return self.context_manager.build_phase_context_injection(
            completed_phase_outputs=self._completed_phase_outputs,
            current_phase=current_name,
            overall_goal=self.session.overall_goal,
        )
