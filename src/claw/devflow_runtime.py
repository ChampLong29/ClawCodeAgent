"""DevFlow runtime — structured development workflow system.

Provides a lifecycle state machine guiding the agent through:
  INIT → ARCHITECTURE → STEP_DEFINITION → IMPLEMENTATION → VERIFY → DONE

Each phase has a corresponding DevFlow skill that injects specialized prompts
into the system prompt to constrain agent behavior.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .hook_policy import RuntimeBase
from .session_naming import make_session_id, make_project_dir_name

if TYPE_CHECKING:
    from .agent_runtime import LocalCodingAgent


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DevFlowStep:
    """A single step in the DevFlow plan."""
    id: str
    title: str
    goal: str = ""
    constraints: str = ""
    acceptance_criteria: str = ""
    status: str = "pending"  # pending | in_progress | implemented | verified | failed
    depends_on: List[str] = field(default_factory=list)
    modules: List[DevFlowModule] = field(default_factory=list)
    current_module_index: int = 0
    implementation_result: Optional[str] = None
    verification_result: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "constraints": self.constraints,
            "acceptance_criteria": self.acceptance_criteria,
            "status": self.status,
            "depends_on": self.depends_on,
            "modules": [m.to_dict() for m in self.modules],
            "current_module_index": self.current_module_index,
            "implementation_result": self.implementation_result,
            "verification_result": self.verification_result,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DevFlowStep:
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            goal=data.get("goal", ""),
            constraints=data.get("constraints", ""),
            acceptance_criteria=data.get("acceptance_criteria", ""),
            status=data.get("status", "pending"),
            depends_on=data.get("depends_on", []),
            modules=[DevFlowModule.from_dict(m) for m in data.get("modules", [])],
            current_module_index=data.get("current_module_index", 0),
            implementation_result=data.get("implementation_result"),
            verification_result=data.get("verification_result"),
        )

    def get_current_module(self) -> Optional[DevFlowModule]:
        """Get the current module being worked on, if modules exist."""
        if 0 <= self.current_module_index < len(self.modules):
            return self.modules[self.current_module_index]
        return None

    def has_modules(self) -> bool:
        """Check if this step has been broken down into modules."""
        return len(self.modules) > 0

    def can_start(self, all_steps: Dict[str, DevFlowStep]) -> bool:
        """Check if all dependencies are verified."""
        for dep_id in self.depends_on:
            dep = all_steps.get(dep_id)
            if dep is None or dep.status != "verified":
                return False
        return True


@dataclass
class DevFlowModule:
    """A single module/file within a DevFlow step.

    Each module is an independently implementable unit (typically one file).
    The agent implements one module at a time, with user confirmation before each.
    """
    id: str
    file_path: str
    goal: str = ""
    constraints: str = ""
    acceptance_criteria: str = ""
    status: str = "pending"  # pending | in_progress | implemented | verified | failed
    implementation_result: Optional[str] = None
    verification_result: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "goal": self.goal,
            "constraints": self.constraints,
            "acceptance_criteria": self.acceptance_criteria,
            "status": self.status,
            "implementation_result": self.implementation_result,
            "verification_result": self.verification_result,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DevFlowModule:
        return cls(
            id=data.get("id", ""),
            file_path=data.get("file_path", ""),
            goal=data.get("goal", ""),
            constraints=data.get("constraints", ""),
            acceptance_criteria=data.get("acceptance_criteria", ""),
            status=data.get("status", "pending"),
            implementation_result=data.get("implementation_result"),
            verification_result=data.get("verification_result"),
        )


@dataclass
class DevFlowSession:
    """A DevFlow development session."""
    session_id: str
    overall_goal: str
    user_constraints: str = ""
    architecture: Optional[str] = None
    steps: List[DevFlowStep] = field(default_factory=list)
    current_step_index: int = 0
    phase: str = "INIT"
    created_at: float = 0.0
    updated_at: float = 0.0
    completed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "overall_goal": self.overall_goal,
            "user_constraints": self.user_constraints,
            "architecture": self.architecture,
            "steps": [s.to_dict() for s in self.steps],
            "current_step_index": self.current_step_index,
            "phase": self.phase,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed": self.completed,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DevFlowSession:
        return cls(
            session_id=data.get("session_id", ""),
            overall_goal=data.get("overall_goal", ""),
            user_constraints=data.get("user_constraints", ""),
            architecture=data.get("architecture"),
            steps=[DevFlowStep.from_dict(s) for s in data.get("steps", [])],
            current_step_index=data.get("current_step_index", 0),
            phase=data.get("phase", "INIT"),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            completed=data.get("completed", False),
        )

    def get_current_step(self) -> Optional[DevFlowStep]:
        """Get the currently active step."""
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def get_step_by_id(self, step_id: str) -> Optional[DevFlowStep]:
        """Get a step by its ID."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def steps_by_id(self) -> Dict[str, DevFlowStep]:
        """Get a dict of steps keyed by ID."""
        return {s.id: s for s in self.steps}

    def progress(self) -> Dict[str, Any]:
        """Get progress statistics."""
        total = len(self.steps)
        if total == 0:
            return {"total": 0, "verified": 0, "in_progress": 0, "pending": 0, "failed": 0, "percent": 0}

        verified = sum(1 for s in self.steps if s.status == "verified")
        in_progress = sum(1 for s in self.steps if s.status == "in_progress")
        pending = sum(1 for s in self.steps if s.status == "pending")
        failed = sum(1 for s in self.steps if s.status == "failed")
        implemented = sum(1 for s in self.steps if s.status == "implemented")

        return {
            "total": total,
            "verified": verified,
            "implemented": implemented,
            "in_progress": in_progress,
            "pending": pending,
            "failed": failed,
            "percent": int((verified / total) * 100),
        }


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

# Phase-to-skill mapping
PHASE_SKILL_MAP = {
    "ARCHITECTURE": "devflow-architect",
    "STEP_DEFINITION": "devflow-step-planner",
    "STEP_ANALYSIS": "devflow-step-analyzer",
    "IMPLEMENTATION": "devflow-implementer",
    "VERIFY": "devflow-verifier",
}


class DevFlowRuntime(RuntimeBase):
    """DevFlow runtime — drives the structured development lifecycle.

    Usage:
        rt = DevFlowRuntime(cwd=".")
        session = rt.start_session("Build user auth system", "Use JWT + Redis")
        # rt.propose_architecture(agent)  → phase advances to ARCHITECTURE
        # rt.generate_steps(agent)        → phase advances to STEP_DEFINITION
        # rt.execute_step(agent)          → phase advances to IMPLEMENTATION
        # rt.verify_step(agent)           → phase advances to VERIFY
        # rt.next_step()                  → loop or advance to DONE
    """

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.session: Optional[DevFlowSession] = None
        self._sessions_dir = os.path.join(cwd, ".port_sessions", "devflow")
        self._project_dir: Optional[str] = None
        os.makedirs(self._sessions_dir, exist_ok=True)

        from .context_manager import ContextManager
        self.context_manager = ContextManager()
        self._completed_step_outputs: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(self, goal: str, constraints: str = "") -> DevFlowSession:
        """Start a new DevFlow session with a dedicated project directory."""
        session_id = make_session_id(goal, "devflow")
        now = time.time()

        # Create project directory
        if not self._project_dir:
            proj_name = make_project_dir_name(goal)
            self._project_dir = os.path.join(self.cwd, "projects", proj_name)
            os.makedirs(self._project_dir, exist_ok=True)

        self.session = DevFlowSession(
            session_id=session_id,
            overall_goal=goal,
            user_constraints=constraints,
            phase="ARCHITECTURE",
            created_at=now,
            updated_at=now,
        )
        self.save()
        return self.session

    def get_project_dir(self) -> Optional[str]:
        """Return the dedicated project directory, if created."""
        return self._project_dir

    def get_session(self) -> Optional[DevFlowSession]:
        """Get the current session."""
        return self.session

    def has_active_session(self) -> bool:
        """Check if there is an active (uncompleted) session."""
        return self.session is not None and not self.session.completed

    # ------------------------------------------------------------------
    # Phase: ARCHITECTURE
    # ------------------------------------------------------------------

    def propose_architecture(self, agent: LocalCodingAgent) -> str:
        """Run the agent to propose an architecture.

        In REPL mode, the caller should display the result and ask the user
        to accept/modify before calling approve_architecture().

        Returns the agent's output string.
        """
        if not self.session:
            raise RuntimeError("No active DevFlow session. Call start_session() first.")

        self.session.phase = "ARCHITECTURE"

        from .bundled_skills import get_skill
        skill = get_skill("devflow-architect")
        if not skill:
            raise RuntimeError("devflow-architect skill not found")

        prompt = skill.prompt.format(
            goal=self.session.overall_goal,
            constraints=self.session.user_constraints or "None",
        )

        result = agent.run(prompt=prompt, stream=False)
        architecture = result.final_message or ""
        self.session.architecture = architecture
        self.session.updated_at = time.time()
        self.save()
        return architecture

    def approve_architecture(self, architecture: Optional[str] = None,
                             agent_session=None) -> None:
        """Approve (and optionally replace) the architecture.

        Moves the phase from ARCHITECTURE to STEP_DEFINITION.
        """
        if not self.session:
            raise RuntimeError("No active DevFlow session.")

        if architecture is not None:
            self.session.architecture = architecture

        if not self.session.architecture:
            raise RuntimeError("Cannot approve: no architecture defined.")

        self.session.phase = "STEP_DEFINITION"
        self.session.updated_at = time.time()

        if agent_session is not None:
            agent_session.mark_phase_boundary("ARCHITECTURE")

        self.save()

    # ------------------------------------------------------------------
    # Phase: STEP_DEFINITION
    # ------------------------------------------------------------------

    def generate_steps(self, agent: LocalCodingAgent) -> List[DevFlowStep]:
        """Run the agent to generate implementation steps.

        Parses the agent's JSON output into DevFlowStep objects.
        """
        if not self.session:
            raise RuntimeError("No active DevFlow session.")
        if not self.session.architecture:
            raise RuntimeError("No architecture defined. Approve architecture first.")

        self.session.phase = "STEP_DEFINITION"

        from .bundled_skills import get_skill
        skill = get_skill("devflow-step-planner")
        if not skill:
            raise RuntimeError("devflow-step-planner skill not found")

        prompt = skill.prompt.format(
            goal=self.session.overall_goal,
            architecture=self.session.architecture,
        )

        result = agent.run(prompt=prompt, stream=False)
        raw = result.final_message or ""

        # Parse JSON from agent output — handle markdown code blocks
        steps_data = self._parse_steps_json(raw)
        self.session.steps = [DevFlowStep.from_dict(s) for s in steps_data]
        self.session.current_step_index = 0
        self.session.updated_at = time.time()
        self.save()
        return self.session.steps

    def _parse_steps_json(self, raw: str) -> List[Dict[str, Any]]:
        """Parse steps JSON from agent output, handling markdown code blocks."""
        # Try direct JSON parse first
        try:
            data = json.loads(raw.strip())
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

        # Try extracting from ```json ... ``` code block
        import re
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
        if json_match:
            try:
                data = json.loads(json_match.group(1).strip())
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        # Try finding JSON array in the text
        array_match = re.search(r'\[\s*\{[\s\S]*\}\s*\]', raw)
        if array_match:
            try:
                data = json.loads(array_match.group(0))
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        return []

    def approve_steps(self, steps: Optional[List[Dict[str, Any]]] = None,
                      agent_session=None) -> None:
        """Approve (and optionally replace) the step list.

        Moves the phase from STEP_DEFINITION to STEP_ANALYSIS.
        """
        if not self.session:
            raise RuntimeError("No active DevFlow session.")

        if steps is not None:
            self.session.steps = [DevFlowStep.from_dict(s) for s in steps]
            self.session.current_step_index = 0

        if not self.session.steps:
            raise RuntimeError("Cannot approve: no steps defined.")

        self._advance_to_next_ready_step()
        self.session.phase = "STEP_ANALYSIS"
        self.session.updated_at = time.time()

        if agent_session is not None:
            agent_session.mark_phase_boundary("STEP_DEFINITION")

        self.save()

    # ------------------------------------------------------------------
    # Phase: STEP_ANALYSIS (module breakdown)
    # ------------------------------------------------------------------

    def analyze_step(self, agent: LocalCodingAgent) -> List[DevFlowModule]:
        """Run the agent to break a step into implementation modules.

        Each module typically corresponds to one file or component.
        Returns the list of modules parsed from the agent's output.
        """
        if not self.session:
            raise RuntimeError("No active DevFlow session.")

        step = self.session.get_current_step()
        if not step:
            raise RuntimeError("No current step to analyze.")

        self.session.phase = "STEP_ANALYSIS"

        from .bundled_skills import get_skill
        skill = get_skill("devflow-step-analyzer")
        if not skill:
            raise RuntimeError("devflow-step-analyzer skill not found")

        prompt = skill.prompt.format(
            goal=self.session.overall_goal,
            architecture=self.session.architecture or "See overall goal above.",
            step_title=step.title,
            step_goal=step.goal,
            step_constraints=step.constraints or "No specific constraints.",
        )

        result = agent.run(prompt=prompt, stream=False)
        raw = result.final_message or ""

        # Parse JSON from agent output
        modules_data = self._parse_steps_json(raw)  # reuse JSON parser
        step.modules = [DevFlowModule.from_dict(m) for m in modules_data]
        step.current_module_index = 0
        self.session.updated_at = time.time()
        self.save()
        return step.modules

    def approve_modules(self, modules: Optional[List[Dict[str, Any]]] = None,
                        agent_session=None) -> None:
        """Approve (and optionally replace) the module breakdown.

        Moves the phase from STEP_ANALYSIS to IMPLEMENTATION with module mode.
        """
        if not self.session:
            raise RuntimeError("No active DevFlow session.")

        step = self.session.get_current_step()
        if not step:
            raise RuntimeError("No current step.")

        if modules is not None:
            step.modules = [DevFlowModule.from_dict(m) for m in modules]
            step.current_module_index = 0

        if not step.modules:
            raise RuntimeError("Cannot approve: no modules defined.")

        self.session.phase = "IMPLEMENTATION"
        self.session.updated_at = time.time()

        if agent_session is not None:
            agent_session.mark_phase_boundary("STEP_ANALYSIS")
            agent_session.mark_phase_boundary(step.id)

        self.save()

    # ------------------------------------------------------------------
    # Phase: IMPLEMENTATION
    # ------------------------------------------------------------------

    def get_current_step(self) -> Optional[DevFlowStep]:
        """Get the step currently being worked on."""
        if not self.session:
            return None
        return self.session.get_current_step()

    def get_current_module(self) -> Optional[DevFlowModule]:
        """Get the module currently being worked on, if in module mode."""
        if not self.session:
            return None
        step = self.session.get_current_step()
        if step:
            return step.get_current_module()
        return None

    def execute_step(self, agent: LocalCodingAgent) -> str:
        """Run the agent to implement the current step.

        If the step has modules, this delegates to execute_module() for
        the current module. Otherwise it implements the entire step at once.
        """
        if not self.session:
            raise RuntimeError("No active DevFlow session.")

        step = self.session.get_current_step()
        if not step:
            raise RuntimeError("No current step to execute.")

        # If step has modules, delegate to per-module execution
        if step.has_modules():
            return self.execute_module(agent)

        # Legacy mode: implement entire step at once
        return self._execute_step_full(agent)

    def execute_module(self, agent: LocalCodingAgent) -> str:
        """Run the agent to implement a single module of the current step."""
        if not self.session:
            raise RuntimeError("No active DevFlow session.")

        step = self.session.get_current_step()
        if not step:
            raise RuntimeError("No current step to execute.")

        module = step.get_current_module()
        if not module:
            raise RuntimeError("No current module to execute.")

        self.session.phase = "IMPLEMENTATION"
        module.status = "in_progress"
        step.status = "in_progress"
        self.save()

        from .bundled_skills import get_skill
        skill = get_skill("devflow-implementer")
        if not skill:
            raise RuntimeError("devflow-implementer skill not found")

        prev_summary = self._build_previous_steps_summary()

        # Build module-specific prompt
        prompt = skill.prompt.format(
            goal=self.session.overall_goal,
            architecture=self.session.architecture or "See overall goal above.",
            step_title=f"{step.title} — Module: {module.file_path}",
            step_goal=f"Implement ONLY the file/component: {module.file_path}\n\nModule Goal: {module.goal}",
            step_constraints=f"Step-level constraints: {step.constraints or 'None'}\n\nModule-specific constraints: {module.constraints or 'None'}",
            acceptance_criteria=module.acceptance_criteria or step.acceptance_criteria or "No specific acceptance criteria.",
            previous_steps_summary=prev_summary or "None — this is the first step.",
        )

        # Execute with write permissions enabled
        if agent.permissions:
            old_write = agent.permissions.get("allow_write", False)
            agent.permissions["allow_write"] = True
        else:
            old_write = False

        try:
            result = agent.run(prompt=prompt, stream=False)
            output = result.final_message or ""
        finally:
            if agent.permissions:
                agent.permissions["allow_write"] = old_write

        module.implementation_result = output
        module.status = "implemented"
        self.session.updated_at = time.time()
        self.save()
        return output

    def _execute_step_full(self, agent: LocalCodingAgent) -> str:
        """Legacy mode: implement the entire step in one call (no modules)."""
        if not self.session:
            raise RuntimeError("No active DevFlow session.")

        step = self.session.get_current_step()
        if not step:
            raise RuntimeError("No current step to execute.")

        self.session.phase = "IMPLEMENTATION"
        step.status = "in_progress"
        self.save()

        from .bundled_skills import get_skill
        skill = get_skill("devflow-implementer")
        if not skill:
            raise RuntimeError("devflow-implementer skill not found")

        prev_summary = self._build_previous_steps_summary()

        prompt = skill.prompt.format(
            goal=self.session.overall_goal,
            architecture=self.session.architecture or "See overall goal above.",
            step_title=step.title,
            step_goal=step.goal,
            step_constraints=step.constraints or "No specific constraints.",
            acceptance_criteria=step.acceptance_criteria or "No specific acceptance criteria.",
            previous_steps_summary=prev_summary or "None — this is the first step.",
        )

        if agent.permissions:
            old_write = agent.permissions.get("allow_write", False)
            agent.permissions["allow_write"] = True
        else:
            old_write = False

        try:
            result = agent.run(prompt=prompt, stream=False)
            output = result.final_message or ""
        finally:
            if agent.permissions:
                agent.permissions["allow_write"] = old_write

        step.implementation_result = output
        step.status = "implemented"
        self.session.updated_at = time.time()
        self.save()
        return output

    def _build_previous_steps_summary(self) -> str:
        """Build a summary of previously completed steps for context."""
        if not self.session:
            return ""

        parts = []
        for step in self.session.steps:
            if step.status in ("verified",) and step.id != self.session.get_current_step().id if self.session.get_current_step() else True:
                parts.append(f"- **{step.title}** ({step.id}): VERIFIED")
                if step.implementation_result:
                    # Include a brief excerpt (first 200 chars)
                    excerpt = step.implementation_result[:200]
                    if len(step.implementation_result) > 200:
                        excerpt += "..."
                    parts.append(f"  Summary: {excerpt}")
            elif step.status == "implemented" and step.id != (self.session.get_current_step().id if self.session.get_current_step() else None):
                parts.append(f"- **{step.title}** ({step.id}): IMPLEMENTED (awaiting verification)")

        return "\n".join(parts) if parts else "None — this is the first step."

    # ------------------------------------------------------------------
    # Phase: VERIFY
    # ------------------------------------------------------------------

    def verify_step(self, agent: LocalCodingAgent) -> str:
        """Run the agent to verify the current step's implementation.

        If in module mode, verifies the current module. Otherwise verifies
        the entire step.
        """
        if not self.session:
            raise RuntimeError("No active DevFlow session.")

        step = self.session.get_current_step()
        if not step:
            raise RuntimeError("No current step to verify.")

        # If in module mode, verify the current module
        if step.has_modules():
            return self.verify_module(agent)

        if step.status not in ("implemented",):
            raise RuntimeError(f"Cannot verify step with status '{step.status}'. Must be 'implemented'.")

        self.session.phase = "VERIFY"

        from .bundled_skills import get_skill
        skill = get_skill("devflow-verifier")
        if not skill:
            raise RuntimeError("devflow-verifier skill not found")

        prompt = skill.prompt.format(
            step_title=step.title,
            acceptance_criteria=step.acceptance_criteria or "No specific acceptance criteria.",
            implementation_result=step.implementation_result or "No implementation result available.",
        )

        result = agent.run(prompt=prompt, stream=False)
        output = result.final_message or ""

        step.verification_result = output

        # Auto-detect verdict
        if "Overall Verdict: PASS" in output or "Overall Verdict: **PASS**" in output or "### Overall Verdict: PASS" in output:
            step.status = "verified"
        elif "Overall Verdict: FAIL" in output or "Overall Verdict: **FAIL**" in output or "### Overall Verdict: FAIL" in output:
            step.status = "failed"
        else:
            step.status = "verified"

        self.session.updated_at = time.time()
        self.save()
        return output

    def verify_module(self, agent: LocalCodingAgent) -> str:
        """Run the agent to verify a single module's implementation."""
        if not self.session:
            raise RuntimeError("No active DevFlow session.")

        step = self.session.get_current_step()
        if not step:
            raise RuntimeError("No current step.")

        module = step.get_current_module()
        if not module:
            raise RuntimeError("No current module to verify.")

        if module.status not in ("implemented",):
            raise RuntimeError(f"Cannot verify module with status '{module.status}'. Must be 'implemented'.")

        self.session.phase = "VERIFY"

        from .bundled_skills import get_skill
        skill = get_skill("devflow-verifier")
        if not skill:
            raise RuntimeError("devflow-verifier skill not found")

        criteria = module.acceptance_criteria or step.acceptance_criteria or "No specific acceptance criteria."
        prompt = skill.prompt.format(
            step_title=f"{step.title} — Module: {module.file_path}",
            acceptance_criteria=criteria,
            implementation_result=module.implementation_result or "No implementation result available.",
        )

        result = agent.run(prompt=prompt, stream=False)
        output = result.final_message or ""

        module.verification_result = output

        if "Overall Verdict: PASS" in output or "Overall Verdict: **PASS**" in output or "### Overall Verdict: PASS" in output:
            module.status = "verified"
        elif "Overall Verdict: FAIL" in output or "Overall Verdict: **FAIL**" in output or "### Overall Verdict: FAIL" in output:
            module.status = "failed"
        else:
            module.status = "verified"

        # If all modules are verified, mark step as verified
        if all(m.status == "verified" for m in step.modules):
            step.status = "verified"

        self.session.updated_at = time.time()
        self.save()
        return output

    # ------------------------------------------------------------------
    # Step navigation
    # ------------------------------------------------------------------

    def next_step(self, agent_session=None) -> bool:
        """Advance to the next ready step. Returns False if no more steps.

        Args:
            agent_session: Optional AgentSession for phase-boundary compaction.
        """
        if not self.session:
            return False

        current_step = self.session.get_current_step()
        if current_step and current_step.status == "verified":
            # Store verified step output
            output = current_step.verification_result or current_step.implementation_result or ""
            self._completed_step_outputs[current_step.id] = output

        # Compact agent session at step boundary
        if agent_session is not None:
            self.context_manager.compact_at_phase_transition(
                agent_session,
                current_phase=current_step.id if current_step else "",
                completed_phase_outputs=self._completed_step_outputs,
            )

        self._advance_to_next_ready_step()

        if self.session.current_step_index >= len(self.session.steps):
            all_done = all(s.status == "verified" for s in self.session.steps)
            if all_done:
                self.session.phase = "DONE"
                self.session.completed = True
                self.session.updated_at = time.time()
                self.save()
                return False

            self.session.phase = "DONE"
            self.session.completed = True
            self.session.updated_at = time.time()
            self.save()
            return False

        # Mark new step boundary in agent session
        if agent_session is not None:
            new_step = self.session.get_current_step()
            if new_step:
                agent_session.mark_phase_boundary(new_step.id)

        self.session.phase = "IMPLEMENTATION"
        self.session.updated_at = time.time()
        self.save()
        return True

    def _advance_to_next_ready_step(self) -> None:
        """Find the next step whose dependencies are met."""
        if not self.session:
            return

        steps_by_id = self.session.steps_by_id()

        for i, step in enumerate(self.session.steps):
            if step.status in ("pending",) and step.can_start(steps_by_id):
                self.session.current_step_index = i
                return

        # If no pending steps found, move past the end
        # Check if there are any failed steps that could be retried
        for i, step in enumerate(self.session.steps):
            if step.status in ("failed", "implemented"):
                self.session.current_step_index = i
                return

        self.session.current_step_index = len(self.session.steps)

    def next_module(self) -> bool:
        """Advance to the next module in the current step. Returns False if no more modules."""
        if not self.session:
            return False

        step = self.session.get_current_step()
        if not step or not step.has_modules():
            return False

        step.current_module_index += 1
        if step.current_module_index >= len(step.modules):
            # All modules done — step is complete
            return False

        self.session.updated_at = time.time()
        self.save()
        return True

    def skip_step(self, agent_session=None) -> bool:
        """Skip the current step and advance. Returns False if no more steps."""
        if not self.session:
            return False

        step = self.session.get_current_step()
        if step:
            step.status = "failed"
            step.verification_result = "Skipped by user."

        return self.next_step(agent_session=agent_session)

    def mark_step_failed(self, reason: str = "") -> None:
        """Mark the current step as failed."""
        if not self.session:
            return

        step = self.session.get_current_step()
        if step:
            step.status = "failed"
            if reason:
                step.verification_result = f"FAILED: {reason}"

        self.session.updated_at = time.time()
        self.save()

    def retry_step(self) -> None:
        """Reset the current step to pending for retry."""
        if not self.session:
            return

        step = self.session.get_current_step()
        if step:
            step.status = "pending"
            step.implementation_result = None
            step.verification_result = None

        self.session.phase = "IMPLEMENTATION"
        self.session.updated_at = time.time()
        self.save()

    # ------------------------------------------------------------------
    # Plan editing — modify steps without regenerating from scratch
    # ------------------------------------------------------------------

    def edit_step(
        self,
        step_id: str,
        title: Optional[str] = None,
        goal: Optional[str] = None,
        constraints: Optional[str] = None,
        acceptance_criteria: Optional[str] = None,
    ) -> bool:
        """Edit fields of a step.  Only non-None values are updated.

        Returns ``True`` if the step was found and updated.
        """
        if not self.session:
            return False

        step = self._find_step(step_id)
        if not step:
            return False

        if title is not None:
            step.title = title
        if goal is not None:
            step.goal = goal
        if constraints is not None:
            step.constraints = constraints
        if acceptance_criteria is not None:
            step.acceptance_criteria = acceptance_criteria

        self.session.updated_at = time.time()
        self.save()
        return True

    def remove_step(self, step_id: str) -> bool:
        """Remove a step by ID.  Cleans up dependency references.

        Returns ``True`` if the step was found and removed.
        """
        if not self.session:
            return False

        step = self._find_step(step_id)
        if not step:
            return False

        self.session.steps.remove(step)

        # Clean up depends_on references in remaining steps
        for s in self.session.steps:
            if step_id in s.depends_on:
                s.depends_on.remove(step_id)

        # Adjust current_step_index if needed
        if self.session.current_step_index >= len(self.session.steps):
            self.session.current_step_index = max(0, len(self.session.steps) - 1)

        self.session.updated_at = time.time()
        self.save()
        return True

    def add_step(
        self,
        title: str,
        goal: str = "",
        after_step_id: Optional[str] = None,
        depends_on: Optional[List[str]] = None,
        constraints: str = "",
        acceptance_criteria: str = "",
    ) -> bool:
        """Insert a new step after *after_step_id* (or at the end).

        Returns ``True`` on success.
        """
        if not self.session:
            return False

        step_id = f"step-{len(self.session.steps) + 1}"
        # Ensure unique ID
        existing_ids = {s.id for s in self.session.steps}
        counter = len(self.session.steps) + 1
        while step_id in existing_ids:
            counter += 1
            step_id = f"step-{counter}"

        new_step = DevFlowStep(
            id=step_id,
            title=title,
            goal=goal,
            constraints=constraints,
            acceptance_criteria=acceptance_criteria,
            depends_on=depends_on or [],
        )

        if after_step_id:
            idx = self._find_step_index(after_step_id)
            if idx >= 0:
                self.session.steps.insert(idx + 1, new_step)
            else:
                self.session.steps.append(new_step)
        else:
            self.session.steps.append(new_step)

        self.session.updated_at = time.time()
        self.save()
        return True

    def move_step(self, step_id: str, before_step_id: str) -> bool:
        """Move *step_id* to just before *before_step_id*.

        Returns ``True`` on success.
        """
        if not self.session:
            return False

        step = self._find_step(step_id)
        if not step:
            return False

        target_idx = self._find_step_index(before_step_id)
        if target_idx < 0:
            return False

        self.session.steps.remove(step)
        # Recalculate target index after removal
        target_idx = self._find_step_index(before_step_id)
        if target_idx < 0:
            target_idx = len(self.session.steps)
        self.session.steps.insert(target_idx, step)

        self.session.updated_at = time.time()
        self.save()
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_step(self, step_id: str) -> Optional[DevFlowStep]:
        if not self.session:
            return None
        for s in self.session.steps:
            if s.id == step_id:
                return s
        return None

    def _find_step_index(self, step_id: str) -> int:
        if not self.session:
            return -1
        for i, s in enumerate(self.session.steps):
            if s.id == step_id:
                return i
        return -1

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def rollback_to_step(self, step_id: str, agent_session=None) -> bool:
        """Roll back to a specific step by ID.

        Resets the target step and all subsequent steps to *pending*.
        Returns ``True`` on success.
        """
        if not self.session:
            return False

        target_idx = None
        for i, step in enumerate(self.session.steps):
            if step.id == step_id:
                target_idx = i
                break

        if target_idx is None:
            return False

        # Reset target and all subsequent steps
        for i in range(target_idx, len(self.session.steps)):
            s = self.session.steps[i]
            s.status = "pending"
            s.implementation_result = None
            s.verification_result = None
            s.current_module_index = 0

        self.session.current_step_index = target_idx
        self.session.phase = "IMPLEMENTATION"
        self.session.updated_at = time.time()

        # Clean completed_step_outputs after rollback point
        step_ids_to_keep = {s.id for s in self.session.steps[:target_idx]}
        self._completed_step_outputs = {
            k: v for k, v in self._completed_step_outputs.items()
            if k in step_ids_to_keep
        }

        self.save()
        return True

    def rollback_to_phase(self, phase_name: str, agent_session=None) -> bool:
        """Roll back to a DevFlow phase.

        Valid phase names: ARCHITECTURE, STEP_DEFINITION, STEP_ANALYSIS,
        IMPLEMENTATION, VERIFY.

        Returns ``True`` on success.
        """
        if not self.session:
            return False

        phase_order = [
            "ARCHITECTURE", "STEP_DEFINITION", "STEP_ANALYSIS",
            "IMPLEMENTATION", "VERIFY", "DONE",
        ]

        if phase_name not in phase_order:
            return False

        target_idx = phase_order.index(phase_name)
        current_idx = (phase_order.index(self.session.phase)
                       if self.session.phase in phase_order
                       else len(phase_order))

        if target_idx >= current_idx:
            return False  # cannot roll forward

        # Reset state based on target phase
        if target_idx <= phase_order.index("ARCHITECTURE"):
            self.session.architecture = None
            self.session.steps = []
            self.session.current_step_index = 0

        if target_idx <= phase_order.index("STEP_DEFINITION"):
            self.session.steps = []
            self.session.current_step_index = 0

        if target_idx <= phase_order.index("STEP_ANALYSIS"):
            for step in self.session.steps:
                step.modules = []
                step.current_module_index = 0
                step.status = "pending"
                step.implementation_result = None
                step.verification_result = None

        if target_idx <= phase_order.index("IMPLEMENTATION"):
            self.session.current_step_index = 0
            for step in self.session.steps:
                step.status = "pending"
                step.implementation_result = None
                step.verification_result = None
                step.current_module_index = 0

        self.session.phase = phase_name
        self.session.completed = False
        self.session.updated_at = time.time()
        self._completed_step_outputs = {}

        self.save()
        return True

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

    def load(self, session_id: str) -> Optional[DevFlowSession]:
        """Load a session from disk."""
        path = self._session_path(session_id)
        if not os.path.exists(path):
            return None

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.session = DevFlowSession.from_dict(data)
        return self.session

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all saved DevFlow sessions."""
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
                        "phase": data.get("phase", "UNKNOWN"),
                        "completed": data.get("completed", False),
                        "created_at": data.get("created_at", 0),
                        "steps_count": len(data.get("steps", [])),
                    })
                except (json.JSONDecodeError, OSError):
                    pass

        sessions.sort(key=lambda s: s.get("created_at", 0), reverse=True)
        return sessions

    def archive(self, output_path: Optional[str] = None) -> str:
        """Archive the full session to a readable file."""
        if not self.session:
            raise RuntimeError("No active session to archive.")

        path = output_path or os.path.join(
            self.cwd, f"devflow-{self.session.session_id}.md"
        )

        lines = [
            f"# DevFlow Session: {self.session.overall_goal}",
            f"",
            f"- **Session ID**: {self.session.session_id}",
            f"- **Phase**: {self.session.phase}",
            f"- **Completed**: {self.session.completed}",
            f"- **Created**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.session.created_at))}",
            f"",
        ]

        if self.session.architecture:
            lines.append("## Architecture")
            lines.append("")
            lines.append(self.session.architecture)
            lines.append("")

        if self.session.steps:
            lines.append("## Steps")
            lines.append("")
            status_icons = {
                "pending": "◇",
                "in_progress": "▶",
                "implemented": "●",
                "verified": "✅",
                "failed": "✖",
            }
            for step in self.session.steps:
                icon = status_icons.get(step.status, "?")
                lines.append(f"### {icon} {step.title} ({step.id})")
                lines.append(f"- **Status**: {step.status}")
                lines.append(f"- **Goal**: {step.goal}")
                lines.append(f"- **Constraints**: {step.constraints}")
                lines.append(f"- **Acceptance Criteria**: {step.acceptance_criteria}")
                lines.append(f"- **Depends On**: {', '.join(step.depends_on) if step.depends_on else 'None'}")
                if step.implementation_result:
                    lines.append(f"")
                    lines.append(f"#### Implementation Result")
                    lines.append(f"")
                    lines.append(step.implementation_result)
                if step.verification_result:
                    lines.append(f"")
                    lines.append(f"#### Verification Result")
                    lines.append(f"")
                    lines.append(step.verification_result)
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
            return {"active": False}

        return {
            "active": True,
            "session": self.session.to_dict(),
            "progress": self.session.progress(),
        }

    def render_summary(self) -> str:
        """Render a one-line summary for context injection."""
        if not self.session:
            return ""

        progress = self.session.progress()
        step = self.session.get_current_step()
        step_info = f"Step {progress['verified'] + 1}/{progress['total']}: {step.title}" if step else "No active step"
        return f"[DevFlow] {step_info} — Phase: {self.session.phase}"

    def get_prompt_guidance(self) -> str:
        """Get phase-specific guidance for the system prompt.

        This is the key injection point — it selects the right skill prompt
        template for the current phase and fills in the session's data.
        """
        if not self.session:
            return ""

        step = self.session.get_current_step()

        if self.session.phase == "ARCHITECTURE":
            return f"""[DevFlow - ARCHITECTURE Phase]
You are in the Architecture phase of a structured development workflow.

**Overall Goal**: {self.session.overall_goal}
**User Constraints**: {self.session.user_constraints or 'None'}

Analyze the requirements and propose a comprehensive architecture. Cover:
1. Overview — what will be built
2. Components — major modules and their responsibilities
3. Data Flow — how data moves through the system
4. Technology Choices — with rationale and trade-offs
5. Trade-offs and Risks

Output your architecture as a well-formatted Markdown document."""

        elif self.session.phase == "STEP_DEFINITION":
            return f"""[DevFlow - STEP_DEFINITION Phase]
You are in the Step Definition phase. The architecture has been approved.

**Overall Goal**: {self.session.overall_goal}
**Architecture**:
{self.session.architecture or 'Not yet defined'}

Decompose the architecture into ordered, executable implementation steps.
Output a JSON array where each step has: id, title, goal, constraints,
acceptance_criteria, and depends_on."""

        elif self.session.phase == "STEP_ANALYSIS":
            if not step:
                return ""
            return f"""[DevFlow - STEP_ANALYSIS Phase]
You are analyzing a step to break it into individual implementation modules.

**Overall Goal**: {self.session.overall_goal}
**Current Step**: {step.title}
**Step Goal**: {step.goal}
**Step Constraints**: {step.constraints or 'None'}

Break this step into modules — each module should correspond to one file or
one independently implementable component. For each module define:
- file_path: the file to create/change
- goal: what this specific module must achieve
- constraints: technical constraints specific to this module
- acceptance_criteria: how to verify this module independently

Output a JSON array of module objects."""

        elif self.session.phase == "IMPLEMENTATION":
            if not step:
                return ""
            prev_summary = self._build_previous_steps_summary()

            module = step.get_current_module()
            if module:
                # Module-level implementation
                return f"""[DevFlow - IMPLEMENTATION Phase (Module Mode)]
You are implementing a single module of the current step.
Focus ONLY on this one file/component — do NOT implement anything else.

**Overall Goal**: {self.session.overall_goal}
**Step**: {step.title}
**Module File**: {module.file_path}
**Module Goal**: {module.goal}
**Module Constraints**: {module.constraints or step.constraints or 'None'}
**Acceptance Criteria**: {module.acceptance_criteria or step.acceptance_criteria or 'None'}

**Previous Steps**:
{prev_summary}

Implement ONLY the file {module.file_path}. Use write_file or edit_file to
make changes. After implementation, self-check against each criterion."""

            # Legacy full-step implementation
            return f"""[DevFlow - IMPLEMENTATION Phase]
You are implementing a specific step of the development plan.
Focus ONLY on this step — do not implement future steps.

**Overall Goal**: {self.session.overall_goal}
**Current Step**: {step.title}
**Step Goal**: {step.goal}
**Constraints**: {step.constraints or 'None'}
**Acceptance Criteria**: {step.acceptance_criteria or 'None'}

**Previous Steps**:
{prev_summary}

Implement this step using the available tools. After implementation,
self-check against each acceptance criterion."""

        elif self.session.phase == "VERIFY":
            if not step:
                return ""
            module = step.get_current_module()
            if module:
                return f"""[DevFlow - VERIFY Phase (Module Mode)]
You are verifying a single module's implementation.

**Step**: {step.title}
**Module**: {module.file_path}
**Acceptance Criteria**: {module.acceptance_criteria or step.acceptance_criteria or 'None'}
**Implementation Result**: {module.implementation_result or 'No result available'}

For each criterion, check if it is met using available tools. Report
[PASS], [PARTIAL], or [FAIL] with evidence. Provide an overall verdict
and specific fix recommendations for any failures."""

            return f"""[DevFlow - VERIFY Phase]
You are verifying that an implementation meets its acceptance criteria.

**Step**: {step.title}
**Acceptance Criteria**: {step.acceptance_criteria or 'None'}
**Implementation Result**: {step.implementation_result or 'No result available'}

For each criterion, check if it is met using available tools. Report
[PASS], [PARTIAL], or [FAIL] with evidence. Provide an overall verdict
and specific fix recommendations for any failures."""

        elif self.session.phase == "DONE":
            progress = self.session.progress()
            return f"""[DevFlow - DONE]
All steps completed: {progress['verified']}/{progress['total']} verified."""

        return ""

    def get_step_context_for_agent(self) -> str:
        """Get rich step context to prepend to agent messages.

        This is separate from get_prompt_guidance() and can be injected
        directly into the user message to give the agent the current step
        context without modifying the system prompt.
        """
        if not self.session or not self.has_active_session():
            return ""

        step = self.session.get_current_step()
        if not step:
            return ""

        phase = self.session.phase

        if phase == "IMPLEMENTATION":
            return f"""
[DevFlow Context]
Phase: IMPLEMENTATION
Step: {step.title} ({step.id})
Goal: {step.goal}
Constraints: {step.constraints or 'None'}
Acceptance Criteria: {step.acceptance_criteria or 'None'}
"""
        elif phase == "VERIFY":
            return f"""
[DevFlow Context]
Phase: VERIFY
Step: {step.title} ({step.id})
Acceptance Criteria: {step.acceptance_criteria or 'None'}
"""
        return ""
