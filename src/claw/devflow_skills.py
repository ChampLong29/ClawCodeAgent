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

**重要：请使用简体中文回答。保持简洁——每节控制在3-5个要点以内，避免冗长。**

## Goal
{goal}

## Additional Constraints
{constraints}

## Instructions

Analyze the goal above and produce a structured architecture document with the following sections:

### 1. Overview
用一两句话概述要构建的系统及其解决的核心问题。

### 2. Components
列出系统的主要组件/模块。对每个组件描述：
- 职责
- 对外接口（关键函数/类/端点）
- 对其他组件的依赖

### 3. Data Flow
描述数据在系统中的流动方式：
- 请求/响应流程（API项目）
- 数据转换管道
- 状态管理方案

### 4. Technology Choices
对每项技术选择说明：
- 推荐的技术
- 选择理由
- 需要关注的权衡点

### 5. Trade-offs and Risks
- 架构中的已知权衡
- 潜在风险与缓解策略
- 后续可能需要迭代的领域

输出格式为规范的 Markdown 文档。要求具体、可执行——避免适用于任何项目的泛泛而谈。控制在2000字以内。"""


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

**重要：请使用简体中文输出验证报告。**

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
   - **[通过]** — Criterion is fully met with evidence
   - **[部分通过]** — Criterion is partially met (describe the gap)
   - **[未通过]** — Criterion is not met (describe what's missing)

### Output Format

```
## 验证报告: {step_title}

| # | 验收标准 | 结果 | 证据/差距 |
|---|---------|------|----------|
| 1 | ...     | 通过 | ...      |
| 2 | ...     | 未通过 | ...     |

### 总体结论: [通过 / 未通过]

### 摘要
(验证发现的简要总结)

### 建议 (如果未通过)
(修复失败项所需的具体操作)
```

If the verdict is FAIL, describe exactly what needs to be fixed. Be specific — reference file paths, line numbers, and expected behavior."""


DEVFLOW_STEP_ANALYZER_PROMPT = """You are acting as a Step Analyzer. Your task is to break a development step into individual implementation modules.

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
- **constraints**: technical constraints specific to this module (inheriting from step-level constraints)
- **acceptance_criteria**: 1-3 specific, verifiable criteria to check if this module is correctly implemented

## Output Format

Output a JSON array ONLY (no surrounding text, no markdown code block):

```json
[
  {{
    "id": "module-1",
    "file_path": "src/models/user.py",
    "goal": "Define the User model with fields: id, username, email, password_hash, created_at",
    "constraints": "Use SQLAlchemy 2.0 async, UUID primary key, bcrypt for password",
    "acceptance_criteria": "1. Model imports without errors\\n2. Table name is 'users'\\n3. All fields have correct types and constraints"
  }},
  ...
]
```

## Rules
- Each module must be independently testable
- Order modules so dependencies are resolved (e.g., models before services, services before APIs)
- Module-level constraints must be more specific than step-level constraints
- Acceptance criteria must be verifiable with tools (read_file, grep, bash test, etc.)
- Maximum 2-4 modules per typical step (don't over-fragment)
- If the step is already simple enough (single file), a single module is acceptable"""


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
    "devflow-step-analyzer": DevFlowSkill(
        name="devflow-step-analyzer",
        description="Break a development step into individual implementation modules. "
                    "Each module corresponds to one file or independently testable component. "
                    "Outputs a JSON array of module objects.",
        prompt=DEVFLOW_STEP_ANALYZER_PROMPT,
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
