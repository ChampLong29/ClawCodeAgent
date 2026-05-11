"""Bundled skill definitions for CodeAgent.

Skills that can be invoked via the Skill tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .lifecycle_skills import LIFECYCLE_SKILLS, LifecycleSkill


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
    # DevFlow skills — structured development workflow
    "devflow-architect": BundledSkill(
        name="devflow-architect",
        description="Analyze development requirements and propose system architecture with Overview, Components, Data Flow, Technology Choices, and Trade-offs",
        prompt="""You are acting as a Software Architect. Your task is to analyze the development goal and propose a comprehensive architecture.

## Goal
{goal}

## Additional Constraints
{constraints}

## Instructions

Analyze the goal above and produce a structured architecture document with the following sections:

### 1. Overview
A concise summary of what will be built and the core problem being solved.

### 2. Components
List the major components/modules of the system. For each component, describe its responsibility, public interface, and dependencies.

### 3. Data Flow
Describe how data moves through the system. Include request/response flows, data transformation pipelines, and state management approach.

### 4. Technology Choices
For each technology choice, explain what is proposed, why it was chosen, and any trade-offs.

### 5. Trade-offs and Risks
Known trade-offs, potential risks, mitigation strategies, and areas needing future iteration.

Output your architecture as a well-formatted Markdown document. Be specific and concrete.""",
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The overall development goal"},
                "constraints": {"type": "string", "description": "Additional user constraints"},
            },
            "required": ["goal"],
        },
    ),
    "devflow-step-planner": BundledSkill(
        name="devflow-step-planner",
        description="Decompose architecture into ordered, executable implementation steps with goal, constraints, acceptance criteria, and dependencies",
        prompt="""You are acting as a Development Planner. Your task is to decompose an architecture into an ordered, executable sequence of implementation steps.

## Goal
{goal}

## Architecture
{architecture}

## Instructions

Break the architecture down into concrete implementation steps. Each step must be small enough to be completed in a single agent session but meaningful enough to represent real progress.

For each step, provide: id, title, goal, constraints, acceptance_criteria, and depends_on.

### Rules for Step Planning
- Order steps by dependency: foundational steps come before dependent steps
- Each step should take roughly similar effort
- Constraints should be specific and actionable
- Acceptance criteria must be testable
- At most 3-5 depends_on entries per step
- The first step should set up the project structure
- The last step should be integration testing or documentation

Output your steps as a JSON array of objects with this schema:
```json
[
  {
    "id": "step-1",
    "title": "...",
    "goal": "...",
    "constraints": "...",
    "acceptance_criteria": "...",
    "depends_on": []
  }
]
```
Output ONLY the JSON array — no additional text before or after.""",
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The overall development goal"},
                "architecture": {"type": "string", "description": "The approved architecture document"},
            },
            "required": ["goal", "architecture"],
        },
    ),
    "devflow-implementer": BundledSkill(
        name="devflow-implementer",
        description="Implement a single development step following constraints and acceptance criteria, with self-check after implementation",
        prompt="""You are acting as an Implementer. Your task is to implement a single step of a development plan, strictly following constraints and acceptance criteria.

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

1. Implement exactly what the step asks for — no more, no less
2. Follow all constraints strictly — they override any defaults
3. Write real, working code — all files must be syntactically correct and complete
4. After implementing, self-check against each acceptance criterion
5. If you encounter issues, describe them clearly so the verifier can assess

### Output Format
1. Summary of what was implemented (files created/modified)
2. Self-check against each criterion: [PASS] or [FAIL] with explanation
3. Any notes for the verifier

Use the project's tools to make actual changes. Do NOT simulate or describe changes — actually make them.""",
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
    "devflow-step-analyzer": BundledSkill(
        name="devflow-step-analyzer",
        description="Break a development step into individual implementation modules (one file per module)",
        prompt="""You are acting as a Step Analyzer. Your task is to break a development step into individual implementation modules.

## Overall Goal
{goal}

## Architecture
{architecture}

## Current Step
**Title**: {step_title}
**Goal**: {step_goal}
**Constraints**: {step_constraints}

## Instructions

Break this step into a sequence of modules. Each module should:
1. Correspond to **one file** or **one independently implementable component**
2. Be small enough to implement in a single operation
3. Be ordered by dependency (foundational files first, integrations last)

For each module, define:
- **id**: unique identifier (e.g., "module-1", "module-2")
- **file_path**: the file to create or modify (e.g., "src/models/user.py")
- **goal**: what this specific module must achieve
- **constraints**: technical constraints specific to this module
- **acceptance_criteria**: 1-3 specific, verifiable criteria

## Output Format

Output a JSON array ONLY (no surrounding text):

```json
[
  {{
    "id": "module-1",
    "file_path": "src/models/user.py",
    "goal": "Define the User model with fields: id, username, email, password_hash, created_at",
    "constraints": "Use SQLAlchemy 2.0 async, UUID primary key, bcrypt for password",
    "acceptance_criteria": "1. Model imports without errors\\n2. Table name is 'users'\\n3. All fields have correct types"
  }}
]
```

## Rules
- Each module must be independently testable
- Order modules so dependencies are resolved
- Module-level constraints must be more specific than step-level constraints
- Acceptance criteria must be verifiable with tools
- Maximum 2-4 modules per typical step""",
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The overall development goal"},
                "architecture": {"type": "string", "description": "The approved architecture document"},
                "step_title": {"type": "string", "description": "Title of the step to analyze"},
                "step_goal": {"type": "string", "description": "Goal of the step"},
                "step_constraints": {"type": "string", "description": "Constraints for the step"},
            },
            "required": ["goal", "step_title", "step_goal"],
        },
    ),
    # --- Lifecycle skills (6) ---
    "lifecycle-requirements": BundledSkill(
        name=LIFECYCLE_SKILLS["lifecycle-requirements"].name,
        description=LIFECYCLE_SKILLS["lifecycle-requirements"].description,
        prompt=LIFECYCLE_SKILLS["lifecycle-requirements"].prompt,
        parameters=LIFECYCLE_SKILLS["lifecycle-requirements"].parameters,
    ),
    "lifecycle-design": BundledSkill(
        name=LIFECYCLE_SKILLS["lifecycle-design"].name,
        description=LIFECYCLE_SKILLS["lifecycle-design"].description,
        prompt=LIFECYCLE_SKILLS["lifecycle-design"].prompt,
        parameters=LIFECYCLE_SKILLS["lifecycle-design"].parameters,
    ),
    "lifecycle-code-review": BundledSkill(
        name=LIFECYCLE_SKILLS["lifecycle-code-review"].name,
        description=LIFECYCLE_SKILLS["lifecycle-code-review"].description,
        prompt=LIFECYCLE_SKILLS["lifecycle-code-review"].prompt,
        parameters=LIFECYCLE_SKILLS["lifecycle-code-review"].parameters,
    ),
    "lifecycle-unit-test": BundledSkill(
        name=LIFECYCLE_SKILLS["lifecycle-unit-test"].name,
        description=LIFECYCLE_SKILLS["lifecycle-unit-test"].description,
        prompt=LIFECYCLE_SKILLS["lifecycle-unit-test"].prompt,
        parameters=LIFECYCLE_SKILLS["lifecycle-unit-test"].parameters,
    ),
    "lifecycle-integration-test": BundledSkill(
        name=LIFECYCLE_SKILLS["lifecycle-integration-test"].name,
        description=LIFECYCLE_SKILLS["lifecycle-integration-test"].description,
        prompt=LIFECYCLE_SKILLS["lifecycle-integration-test"].prompt,
        parameters=LIFECYCLE_SKILLS["lifecycle-integration-test"].parameters,
    ),
    "lifecycle-acceptance": BundledSkill(
        name=LIFECYCLE_SKILLS["lifecycle-acceptance"].name,
        description=LIFECYCLE_SKILLS["lifecycle-acceptance"].description,
        prompt=LIFECYCLE_SKILLS["lifecycle-acceptance"].prompt,
        parameters=LIFECYCLE_SKILLS["lifecycle-acceptance"].parameters,
    ),
    "devflow-verifier": BundledSkill(
        name="devflow-verifier",
        description="Verify implementation against acceptance criteria, report PASS/FAIL with evidence, and provide fix recommendations",
        prompt="""You are acting as a Verifier. Your task is to verify that an implementation meets its acceptance criteria.

## Step: {step_title}

**Acceptance Criteria**:
{acceptance_criteria}

## Implementation Result
{implementation_result}

## Instructions

For EACH acceptance criterion:
1. Check if the criterion is met by examining the actual implementation
2. Use available tools to verify — do NOT assume
3. Mark each as [PASS], [PARTIAL], or [FAIL] with evidence

### Output Format
```
## Verification Report: {step_title}

| # | Criterion | Result | Evidence / Gap |
|---|-----------|--------|----------------|
| 1 | ...       | PASS   | ...            |
| 2 | ...       | FAIL   | ...            |

### Overall Verdict: [PASS / FAIL]
### Summary
### Recommendations (if FAIL)
```

If the verdict is FAIL, describe exactly what needs to be fixed with specific file paths, line numbers, and expected behavior.""",
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


def get_skill(skill_name: str) -> Optional[BundledSkill]:
    """Get a skill by name — checks built-ins first, then externals."""
    from .skill_registry import get_skill_registry
    return get_skill_registry().get(skill_name)


def list_skills() -> List[BundledSkill]:
    """List all available skills (built-ins + externals)."""
    # For backward compatibility, return BundledSkill objects for built-ins
    # and synthesized BundledSkill wrappers for externals.
    from .skill_registry import get_skill_registry
    registry = get_skill_registry()
    result = list(BUNDLED_SKILLS.values())
    for name in registry.list_names():
        if name not in BUNDLED_SKILLS:
            ext = registry.get(name)
            if ext:
                result.append(BundledSkill(
                    name=ext.name,
                    description=ext.description,
                    prompt=ext.prompt,
                    parameters=ext.parameters,
                ))
    return result