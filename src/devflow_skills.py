"""DevFlow skill definitions for structured development workflow.

Four skills that guide the agent through the development lifecycle:
- devflow-architect: Analyze requirements and propose architecture
- devflow-step-planner: Decompose architecture into ordered steps
- devflow-implementer: Implement a single step with constraints
- devflow-verifier: Verify implementation against acceptance criteria
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class DevFlowSkill:
    """A DevFlow skill with prompt template."""
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


# ---------------------------------------------------------------------------
# DevFlow skill prompt templates
# ---------------------------------------------------------------------------

DEVFLOW_ARCHITECT_PROMPT = """You are acting as a Software Architect. Your task is to analyze the development goal and propose a comprehensive architecture.

## Goal
{goal}

## Additional Constraints
{constraints}

## Instructions

Analyze the goal above and produce a structured architecture document with the following sections:

### 1. Overview
A concise summary of what will be built and the core problem being solved.

### 2. Components
List the major components/modules of the system. For each component, describe:
- Its responsibility
- Its public interface (key functions/classes/endpoints)
- Its dependencies on other components

### 3. Data Flow
Describe how data moves through the system. Include:
- Request/response flows (for APIs)
- Data transformation pipelines
- State management approach

### 4. Technology Choices
For each technology choice, explain:
- What technology is proposed
- Why it was chosen over alternatives
- Any trade-offs to be aware of

### 5. Trade-offs and Risks
- Known trade-offs in the proposed architecture
- Potential risks and mitigation strategies
- Areas that may need future iteration

Output your architecture as a well-formatted Markdown document. Be specific and concrete — avoid generic statements that could apply to any project."""


DEVFLOW_STEP_PLANNER_PROMPT = """You are acting as a Development Planner. Your task is to decompose an architecture into an ordered, executable sequence of implementation steps.

## Goal
{goal}

## Architecture
{architecture}

## Instructions

Break the architecture down into concrete implementation steps. Each step must be small enough to be completed in a single agent session but meaningful enough to represent real progress.

For each step, provide:

1. **id**: A unique step identifier (e.g., "step-1", "step-2")
2. **title**: A short, descriptive title
3. **goal**: What this step accomplishes — one or two sentences
4. **constraints**: Specific technical constraints that must be followed (file paths, patterns, libraries, APIs, naming conventions, etc.)
5. **acceptance_criteria**: A list of verifiable conditions that prove the step is complete
6. **depends_on**: List of step IDs that must be completed before this step can start

### Rules for Step Planning

- Order steps by dependency: foundational steps (data models, config) come before dependent steps (features, endpoints)
- Each step should take roughly similar effort — if a step is too large, split it
- Constraints should be specific and actionable: "Use SQLAlchemy ORM" not "Use a database"
- Acceptance criteria must be testable: "GET /users returns 200 with JSON array" not "API works correctly"
- At most 3-5 depends_on entries per step to keep the dependency graph manageable
- The first step should set up the project structure and configuration
- The last step should be integration testing or documentation

Output your steps as a JSON array of objects with this exact schema:

```json
[
  {{
    "id": "step-1",
    "title": "...",
    "goal": "...",
    "constraints": "...",
    "acceptance_criteria": "...",
    "depends_on": []
  }},
  ...
]
```

Output ONLY the JSON array — no additional text before or after."""


DEVFLOW_IMPLEMENTER_PROMPT = """You are acting as an Implementer. Your task is to implement a single step of a development plan, strictly following the given constraints and acceptance criteria.

## Overall Goal
{goal}

## Architecture
{architecture}

## Current Step: {step_title}
**Step Goal**: {step_goal}

**Constraints**:
{step_constraints}

**Acceptance Criteria**:
{acceptance_criteria}

## Previous Steps Completed
{previous_steps_summary}

## Instructions

1. **Implement exactly what the step asks for** — no more, no less. Do NOT implement features from future steps.
2. **Follow all constraints strictly** — they override any defaults or preferences.
3. **Write real, working code** — all files must be syntactically correct and complete.
4. **After implementing**, self-check against each acceptance criterion and confirm the result.
5. **If you encounter issues**, describe them clearly so the verifier can assess.

### Output Format

After implementation, provide:
1. A summary of what was implemented (files created/modified)
2. A self-check against each acceptance criterion: [PASS] or [FAIL] with explanation
3. Any notes for the verifier

Use the project's existing tools (write_file, edit_file, bash, etc.) to make changes. Do NOT simulate or describe changes — actually make them."""


DEVFLOW_VERIFIER_PROMPT = """You are acting as a Verifier. Your task is to verify that an implementation meets its acceptance criteria.

## Step: {step_title}

**Acceptance Criteria**:
{acceptance_criteria}

## Implementation Result
{implementation_result}

## Instructions

For EACH acceptance criterion above:

1. Check if the criterion is met by examining the actual implementation
2. Use available tools (read_file, grep_search, bash, etc.) to verify — do NOT assume
3. Mark each criterion as:
   - **[PASS]** — Criterion is fully met with evidence
   - **[PARTIAL]** — Criterion is partially met (describe the gap)
   - **[FAIL]** — Criterion is not met (describe what's missing)

### Output Format

```
## Verification Report: {step_title}

| # | Criterion | Result | Evidence / Gap |
|---|-----------|--------|----------------|
| 1 | ...       | PASS   | ...            |
| 2 | ...       | FAIL   | ...            |

### Overall Verdict: [PASS / FAIL]

### Summary
(Brief summary of verification findings)

### Recommendations (if FAIL)
(Specific actions needed to address failures)
```

If the verdict is FAIL, describe exactly what needs to be fixed. Be specific — reference file paths, line numbers, and expected behavior."""


# ---------------------------------------------------------------------------
# DevFlow skills definitions
# ---------------------------------------------------------------------------

DEVFLOW_SKILLS: Dict[str, DevFlowSkill] = {
    "devflow-architect": DevFlowSkill(
        name="devflow-architect",
        description="Analyze development requirements and propose system architecture. "
                    "Outputs structured Markdown with Overview, Components, Data Flow, "
                    "Technology Choices, and Trade-offs.",
        prompt=DEVFLOW_ARCHITECT_PROMPT,
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The overall development goal"},
                "constraints": {"type": "string", "description": "Additional user constraints"},
            },
            "required": ["goal"],
        },
    ),
    "devflow-step-planner": DevFlowSkill(
        name="devflow-step-planner",
        description="Decompose architecture into ordered implementation steps. "
                    "Each step includes goal, constraints, acceptance criteria, "
                    "and dependency information. Outputs a JSON array.",
        prompt=DEVFLOW_STEP_PLANNER_PROMPT,
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The overall development goal"},
                "architecture": {"type": "string", "description": "The approved architecture document"},
            },
            "required": ["goal", "architecture"],
        },
    ),
    "devflow-implementer": DevFlowSkill(
        name="devflow-implementer",
        description="Implement a single development step following constraints "
                    "and acceptance criteria. Performs a self-check after implementation.",
        prompt=DEVFLOW_IMPLEMENTER_PROMPT,
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The overall development goal"},
                "architecture": {"type": "string", "description": "The approved architecture document"},
                "step_title": {"type": "string", "description": "Title of the current step"},
                "step_goal": {"type": "string", "description": "Goal of the current step"},
                "step_constraints": {"type": "string", "description": "Constraints for the current step"},
                "acceptance_criteria": {"type": "string", "description": "Acceptance criteria for the current step"},
                "previous_steps_summary": {"type": "string", "description": "Summary of previously completed steps"},
            },
            "required": ["goal", "step_title", "step_goal"],
        },
    ),
    "devflow-verifier": DevFlowSkill(
        name="devflow-verifier",
        description="Verify implementation against acceptance criteria. "
                    "Checks each criterion, reports PASS/FAIL with evidence, "
                    "and provides specific fix recommendations for failures.",
        prompt=DEVFLOW_VERIFIER_PROMPT,
        parameters={
            "type": "object",
            "properties": {
                "step_title": {"type": "string", "description": "Title of the step to verify"},
                "acceptance_criteria": {"type": "string", "description": "Acceptance criteria to verify against"},
                "implementation_result": {"type": "string", "description": "Result from the implement step"},
            },
            "required": ["step_title", "acceptance_criteria"],
        },
    ),
}


def get_devflow_skill(skill_name: str) -> Optional[DevFlowSkill]:
    """Get a DevFlow skill by name."""
    return DEVFLOW_SKILLS.get(skill_name)


def list_devflow_skills():
    """List all DevFlow skills."""
    return list(DEVFLOW_SKILLS.values())
