"""Lifecycle runtime — full software engineering lifecycle management.

Wraps DevFlow for development phases while adding requirements analysis,
system design, code review, testing, and acceptance phases.

Phase list is configurable via .claw-lifecycle.json. Default: full 10-phase lifecycle.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .hook_policy import RuntimeBase

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
    status: str = "pending"  # pending | in_progress | completed | skipped | failed
    output: Optional[str] = None
    artifact_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "output": self.output,
            "artifact_path": self.artifact_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LifecyclePhase:
        return cls(
            name=data.get("name", ""),
            status=data.get("status", "pending"),
            output=data.get("output"),
            artifact_path=data.get("artifact_path"),
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
        except (json.JSONDecodeError, OSError):
            pass

    def get_phase_list(self) -> List[str]:
        """Get the configured phase list (or default)."""
        if self._phase_config:
            return self._phase_config
        return list(DEFAULT_LIFECYCLE_PHASES)

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

        Args:
            goal: The overall development goal.
            constraints: User-specified constraints.
            phase_list: Optional custom phase list (overrides config).
        """
        session_id = str(uuid.uuid4())[:8]
        now = time.time()

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

    def advance_phase(self) -> bool:
        """Mark current phase complete and move to next. Returns False if done."""
        if not self.session:
            raise RuntimeError("No active lifecycle session.")

        phase = self.session.get_current_phase()
        if phase and phase.status == "in_progress":
            phase.status = "completed"

        has_next = self._advance_to_next_phase()
        self.session.updated_at = time.time()
        self.save()
        return has_next

    def skip_phase(self) -> bool:
        """Skip the current phase and advance."""
        if not self.session:
            raise RuntimeError("No active lifecycle session.")

        phase = self.session.get_current_phase()
        if phase:
            phase.status = "skipped"
            phase.output = "Skipped by user."

        has_next = self._advance_to_next_phase()
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
    # Phase execution
    # ------------------------------------------------------------------

    def execute_phase(self, agent: LocalCodingAgent) -> str:
        """Run the agent for the current phase.

        Returns the agent's output. For read-only phases, uses the
        lifecycle skill prompt. For DevFlow phases, delegates to DevFlow.
        For write phases, temporarily enables write permissions.
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

        # Build prompt context
        requirements_summary = self.session.get_completed_output("REQUIREMENTS")
        implementation_summary = self.session.get_completed_output("IMPLEMENTATION")

        prompt = skill.prompt.format(
            goal=self.session.overall_goal,
            constraints=self.session.user_constraints or "None",
            requirements_summary=requirements_summary,
            implementation_summary=implementation_summary,
        )

        # Execute with appropriate permissions
        is_write_phase = phase.name in WRITE_PHASES
        if is_write_phase and agent.permissions:
            old_write = agent.permissions.get("allow_write", False)
            agent.permissions["allow_write"] = True
        else:
            old_write = None

        try:
            result = agent.run(prompt=prompt, stream=False)
            output = result.final_message or ""
        finally:
            if is_write_phase and old_write is not None and agent.permissions:
                agent.permissions["allow_write"] = old_write

        # Store output
        phase.output = output
        phase.status = "in_progress"

        # Save artifact if applicable
        artifact_template = PHASE_ARTIFACTS.get(phase.name)
        if artifact_template and output:
            artifact_path = os.path.join(
                self.cwd,
                artifact_template.format(session_id=self.session.session_id),
            )
            os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
            with open(artifact_path, "w", encoding="utf-8") as f:
                f.write(output)
            phase.artifact_path = artifact_path

        self.session.updated_at = time.time()
        self.save()
        return output

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

        parts = []
        for phase in self.session.phases:
            if phase.status in ("completed", "in_progress") and phase.output:
                summary = phase.output[:300]
                if len(phase.output) > 300:
                    summary += "..."
                parts.append(f"- **{phase.name}**: {summary}")

        return "\n".join(parts) if parts else "No prior phase output available."
