"""Lifecycle skill definitions for full software engineering workflow.

Six skills covering the complete software lifecycle beyond DevFlow:
- lifecycle-requirements: Requirements analysis (EARS format, user stories)
- lifecycle-design: System design (modules, data models, interfaces)
- lifecycle-code-review: Code review (security, performance, maintainability)
- lifecycle-unit-test: Unit test generation (coverage, edge cases)
- lifecycle-integration-test: Integration test (API tests, e2e scenarios)
- lifecycle-acceptance: Acceptance testing (requirements verification)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class LifecycleSkill:
    """A Lifecycle skill with prompt template."""
    name: str
    description: str
    prompt: str
    parameters: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Requirements Analysis
# ---------------------------------------------------------------------------

LIFECYCLE_REQUIREMENTS_PROMPT = """You are acting as a Requirements Analyst. Your task is to produce a comprehensive requirements document for the following goal.

## Goal
{goal}

## Constraints
{constraints}

## Instructions

Produce a structured requirements document with the following sections:

### 1. Executive Summary
Brief overview of what the system will accomplish.

### 2. User Stories
Use the format: "As a [role], I want [feature] so that [benefit]". Include acceptance criteria for each story.

### 3. Functional Requirements (FR)
Numbered list of specific functional requirements. Use EARS format where applicable:
- **FR-1**: The system SHALL [do something]
- **FR-2**: The system SHALL [do something else]

### 4. Non-Functional Requirements (NFR)
- **Performance**: Response times, throughput expectations
- **Security**: Authentication, authorization, data protection
- **Reliability**: Availability, fault tolerance
- **Scalability**: Expected growth, scaling strategy
- **Maintainability**: Code quality, documentation standards

### 5. Constraints and Assumptions
Technical constraints, business constraints, assumptions made.

### 6. Scope
What is IN scope and OUT of scope for this iteration.

### Output Format
Output as a well-formatted Markdown document. This will be saved as docs/requirements.md."""


# ---------------------------------------------------------------------------
# System Design
# ---------------------------------------------------------------------------

LIFECYCLE_DESIGN_PROMPT = """You are acting as a System Designer. Your task is to produce a system design document.

## Goal
{goal}

## Requirements
{requirements_summary}

## Constraints
{constraints}

## Instructions

Produce a structured design document with the following sections:

### 1. System Overview
High-level description of the system and its boundaries.

### 2. Module Decomposition
For each major module/component:
- **Name**: Module name
- **Responsibility**: What it owns and does
- **Public API**: Key interfaces it exposes
- **Dependencies**: Other modules it depends on
- **Data**: Data it manages

### 3. Data Model
- Key entities and their relationships
- Database schema (tables, collections) if applicable
- Data flow between components

### 4. API Design
- Endpoint definitions (if REST API)
- Request/response formats
- Authentication and authorization flow

### 5. Sequence Diagrams (textual)
For critical flows, describe:
- Which components interact
- Message order and content
- Error handling paths

### 6. Technology Stack
- Languages, frameworks, databases
- Justification for each choice

### 7. Security Design
- Authentication mechanism
- Authorization model
- Data protection (encryption, sanitization)
- Threat mitigations

### Output Format
Output as a well-formatted Markdown document. This will be saved as docs/design.md."""


# ---------------------------------------------------------------------------
# Code Review
# ---------------------------------------------------------------------------

LIFECYCLE_CODE_REVIEW_PROMPT = """You are acting as a Code Reviewer. Your task is to review the implemented code.

## Goal
{goal}

## Implementation Summary
{implementation_summary}

## Instructions

Review the actual implementation files using read_file, grep, and bash tools. Evaluate each area:

### 1. Security
- Are there any injection vulnerabilities (SQL, command, XSS)?
- Is authentication and authorization properly implemented?
- Are secrets exposed in code?
- Is input validation sufficient?

### 2. Performance
- Are there obvious N+1 queries or inefficient loops?
- Is resource cleanup proper (file handles, connections)?
- Are there unnecessary allocations?

### 3. Error Handling
- Are exceptions caught appropriately?
- Do error messages leak sensitive information?
- Is the system in a consistent state after errors?

### 4. Code Quality
- Does the code follow the project's conventions?
- Are functions and variables well-named?
- Is there duplicate code?
- Are there appropriate comments?

### 5. Maintainability
- Is the code modular and loosely coupled?
- Are there appropriate abstractions?
- Would a new developer understand this code?

### Output Format
Output a code review report in Markdown:

```
## Code Review Report

### Security
[findings with file paths and line numbers]

### Performance
[findings with specific code references]

### Error Handling
[findings]

### Code Quality
[findings]

### Maintainability
[findings]

### Overall Assessment
[PASS / PASS WITH SUGGESTIONS / NEEDS REWORK]

### Recommendations
[prioritized list of recommended changes]
```"""


# ---------------------------------------------------------------------------
# Unit Test
# ---------------------------------------------------------------------------

LIFECYCLE_UNIT_TEST_PROMPT = """You are acting as a Test Engineer. Your task is to write unit tests for the implemented code.

## Goal
{goal}

## Implementation Summary
{implementation_summary}

## Instructions

1. First examine the actual implementation using read_file and grep tools.
2. Identify all public functions, methods, and classes that need tests.
3. Write comprehensive unit tests covering:

### Test Coverage
- **Happy path**: Normal usage scenarios
- **Edge cases**: Empty inputs, boundary values, special characters
- **Error cases**: Invalid inputs, exception paths
- **State transitions**: Where applicable

### Test Quality Standards
- Tests must be self-contained and isolated
- Use appropriate mocking for external dependencies
- Test names should describe the scenario being tested
- Each test should have a single clear assertion purpose
- Aim for >80% code coverage on new code

### Test Framework
Use the project's existing test framework. For Python projects, use pytest or unittest.

### Output Format
1. Write actual test files using write_file tool
2. Run the tests using bash to verify they pass
3. Report a summary:
   - Number of tests written
   - Test coverage estimate
   - Any tests that need further investigation
4. Output a verification table:

```
## Unit Test Report

| Test File | Tests Written | Passed | Failed | Coverage Estimate |
|-----------|---------------|--------|--------|-------------------|
| ...       | ...           | ...    | ...    | ...               |

### Overall: PASS / NEEDS MORE TESTS
```"""


# ---------------------------------------------------------------------------
# Integration Test
# ---------------------------------------------------------------------------

LIFECYCLE_INTEGRATION_TEST_PROMPT = """You are acting as an Integration Test Engineer. Your task is to write and run integration tests.

## Goal
{goal}

## Requirements Summary
{requirements_summary}

## Implementation Summary
{implementation_summary}

## Instructions

1. First examine the system architecture and API endpoints.
2. Write integration tests that verify:
   - End-to-end user flows work correctly
   - Components integrate without errors
   - API endpoints return expected responses
   - Data persists correctly across operations
   - Error scenarios are handled gracefully

### Test Scenarios
- **API Integration**: Test each endpoint with realistic requests
- **Data Flow**: Verify data moves correctly between components
- **Authentication Flow**: Test login → authorized access → logout
- **Concurrent Access**: Basic concurrent operation testing
- **Error Recovery**: System behavior after component failures

### Output Format
1. Write integration test files using write_file
2. Start the service if needed (using bash)
3. Run the tests and capture results
4. Report:

```
## Integration Test Report

| Scenario | Status | Details |
|----------|--------|---------|
| ...      | PASS/FAIL | ...  |

### Overall: PASS / NEEDS FIXES
### Issues Found
[list of issues with severity]
```"""


# ---------------------------------------------------------------------------
# Acceptance Test
# ---------------------------------------------------------------------------

LIFECYCLE_ACCEPTANCE_PROMPT = """You are acting as an Acceptance Tester. Your task is to verify that the system meets the original requirements.

## Goal
{goal}

## Requirements
{requirements_summary}

## Implementation Summary
{implementation_summary}

## Instructions

1. Read the requirements document (docs/requirements_*.md) if available.
2. For each functional requirement, verify it is met by the implementation.
3. Use read_file, grep, and bash tools to check the actual system.

### Verification Method
For each requirement:
1. State the requirement
2. Describe how you verified it (what tools/commands you used)
3. Report the evidence
4. Mark as PASS, PARTIAL, or FAIL

### Output Format

```
## Acceptance Test Report

### Requirements Traceability Matrix

| Req ID | Requirement | Status | Verification Method | Evidence |
|--------|-------------|--------|---------------------|----------|
| FR-1   | ...         | PASS   | ...                 | ...      |
| FR-2   | ...         | PASS   | ...                 | ...      |

### Non-Functional Requirements

| NFR ID | Requirement | Status | Evidence |
|--------|-------------|--------|----------|
| NFR-1  | ...         | PASS   | ...      |

### Overall Verdict: [PASS / FAIL]

### Sign-off Checklist
- [ ] All functional requirements met
- [ ] All non-functional requirements met
- [ ] All test suites passing
- [ ] Documentation complete
- [ ] Code review passed
```"""


# ---------------------------------------------------------------------------
# Skill Registry
# ---------------------------------------------------------------------------

LIFECYCLE_SKILLS: Dict[str, LifecycleSkill] = {
    "lifecycle-requirements": LifecycleSkill(
        name="lifecycle-requirements",
        description="Analyze requirements and produce a structured requirements document "
                    "with user stories, functional/non-functional requirements, and scope.",
        prompt=LIFECYCLE_REQUIREMENTS_PROMPT,
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The overall development goal"},
                "constraints": {"type": "string", "description": "User constraints"},
            },
            "required": ["goal"],
        },
    ),
    "lifecycle-design": LifecycleSkill(
        name="lifecycle-design",
        description="Produce a system design document with module decomposition, "
                    "data models, API design, and technology choices.",
        prompt=LIFECYCLE_DESIGN_PROMPT,
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The overall development goal"},
                "requirements_summary": {"type": "string", "description": "Summary of approved requirements"},
                "constraints": {"type": "string", "description": "User constraints"},
            },
            "required": ["goal", "requirements_summary"],
        },
    ),
    "lifecycle-code-review": LifecycleSkill(
        name="lifecycle-code-review",
        description="Review implemented code for security, performance, error handling, "
                    "code quality, and maintainability.",
        prompt=LIFECYCLE_CODE_REVIEW_PROMPT,
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The overall development goal"},
                "implementation_summary": {"type": "string", "description": "Summary of what was implemented"},
            },
            "required": ["goal", "implementation_summary"],
        },
    ),
    "lifecycle-unit-test": LifecycleSkill(
        name="lifecycle-unit-test",
        description="Write comprehensive unit tests covering happy paths, edge cases, "
                    "and error scenarios with >80% coverage target.",
        prompt=LIFECYCLE_UNIT_TEST_PROMPT,
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The overall development goal"},
                "implementation_summary": {"type": "string", "description": "Summary of what was implemented"},
            },
            "required": ["goal", "implementation_summary"],
        },
    ),
    "lifecycle-integration-test": LifecycleSkill(
        name="lifecycle-integration-test",
        description="Write and run integration tests for API endpoints, data flow, "
                    "and end-to-end scenarios.",
        prompt=LIFECYCLE_INTEGRATION_TEST_PROMPT,
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The overall development goal"},
                "requirements_summary": {"type": "string", "description": "Summary of requirements"},
                "implementation_summary": {"type": "string", "description": "Summary of what was implemented"},
            },
            "required": ["goal", "implementation_summary"],
        },
    ),
    "lifecycle-acceptance": LifecycleSkill(
        name="lifecycle-acceptance",
        description="Verify the system against original requirements with a traceability matrix.",
        prompt=LIFECYCLE_ACCEPTANCE_PROMPT,
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "The overall development goal"},
                "requirements_summary": {"type": "string", "description": "Summary of requirements"},
                "implementation_summary": {"type": "string", "description": "Summary of what was implemented"},
            },
            "required": ["goal"],
        },
    ),
}


def get_lifecycle_skill(skill_name: str) -> Optional[LifecycleSkill]:
    """Get a Lifecycle skill by name."""
    return LIFECYCLE_SKILLS.get(skill_name)
