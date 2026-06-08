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

LIFECYCLE_REQUIREMENTS_PROMPT = """You are acting as a Requirements Analyst. Your task is to produce a concise requirements document for the following goal.

**重要：请使用简体中文回答。保持简洁——每节控制在关键要点即可，避免冗长叙述。**

## Goal
{goal}

## Constraints
{constraints}

## Instructions

Produce a structured requirements document with the following sections:

### 1. 需求概述
简要描述系统要达成的目标（2-3句话）。

### 2. 用户故事
格式："作为[角色]，我希望[功能]，以便[价值]"。每则故事附带验收标准。最多列出3-5个核心用户故事。

### 3. 功能需求 (FR)
编号的功能需求列表，使用 SHALL 格式：
- **FR-1**: 系统应[具体行为]

### 4. 非功能需求 (NFR)
- **性能**: 响应时间、吞吐量期望
- **安全**: 认证、授权、数据保护
- **可靠性**: 可用性、容错

### 5. 约束与假设
技术约束、业务约束、已做的假设。

### 6. 范围
本迭代的范围内/外内容。

### 输出格式
输出规范的 Markdown 文档，控制在1500字以内。将保存为 docs/requirements.md。"""


# ---------------------------------------------------------------------------
# System Design
# ---------------------------------------------------------------------------

LIFECYCLE_DESIGN_PROMPT = """You are acting as a System Designer. Your task is to produce a concise system design document.

**重要：请使用简体中文回答。保持简洁——每节控制在2-4个要点，避免冗长。**

## Goal
{goal}

## Requirements
{requirements_summary}

## Constraints
{constraints}

## Instructions

Produce a structured design document with the following sections:

### 1. 系统概述
系统的高层次描述及其边界（2-3句话）。

### 2. 模块分解
对每个主要模块/组件：
- **名称**
- **职责**
- **对外接口**: 暴露的关键接口
- **依赖**: 依赖的其他模块
- **数据**: 管理的数据

### 3. 数据模型
- 关键实体及其关系
- 数据库Schema概要（如适用）

### 4. API设计
- 端点定义（REST API项目）
- 请求/响应格式
- 认证和授权流程

### 5. 技术栈
- 语言、框架、数据库
- 每项选择的理由

### 6. 安全设计
- 认证机制
- 授权模型
- 数据保护（加密、脱敏）

### 输出格式
输出规范的 Markdown 文档，控制在2000字以内。将保存为 docs/design.md。"""


# ---------------------------------------------------------------------------
# Code Review
# ---------------------------------------------------------------------------

LIFECYCLE_CODE_REVIEW_PROMPT = """You are acting as a Code Reviewer. Your task is to review the implemented code.

**重要：请使用简体中文输出审查报告。保持简洁——每个类别列出关键发现即可。**

## Goal
{goal}

## Implementation Summary
{implementation_summary}

## Instructions

Review the actual implementation files using read_file, grep, and bash tools. Evaluate each area:

### 1. 安全性
- 是否存在注入漏洞（SQL、命令、XSS）？
- 认证和授权是否正确实现？
- 代码中是否暴露了密钥？
- 输入验证是否充分？

### 2. 性能
- 是否存在明显的 N+1 查询或低效循环？
- 资源清理是否正确（文件句柄、连接）？
- 是否存在不必要的内存分配？

### 3. 错误处理
- 异常是否恰当捕获？
- 错误信息是否泄露敏感信息？
- 错误后系统是否处于一致状态？

### 4. 代码质量
- 代码是否遵循项目规范？
- 函数和变量命名是否恰当？
- 是否有重复代码？

### 5. 可维护性
- 代码是否模块化、松耦合？
- 是否存在合适的抽象？
- 新开发者能否理解此代码？

### 输出格式

```
## 代码审查报告

### 安全性
[附文件路径和行号的发现]

### 性能
[附具体代码引用的发现]

### 错误处理
[发现]

### 代码质量
[发现]

### 可维护性
[发现]

### 总体评估
[通过 / 有建议的通过 / 需要返工]

### 建议
[按优先级排列的改进建议]
```"""


# ---------------------------------------------------------------------------
# Unit Test
# ---------------------------------------------------------------------------

LIFECYCLE_UNIT_TEST_PROMPT = """You are acting as a Test Engineer. Your task is to write unit tests for the implemented code.

**重要：请使用简体中文输出测试报告。**

## Goal
{goal}

## Implementation Summary
{implementation_summary}

## Instructions

1. First examine the actual implementation using read_file and grep tools.
2. Identify all public functions, methods, and classes that need tests.
3. Write comprehensive unit tests covering:

### 测试覆盖
- **正常路径**: 常规使用场景
- **边界情况**: 空输入、边界值、特殊字符
- **错误情况**: 无效输入、异常路径

### 测试质量标准
- 测试必须自包含且隔离
- 对外部依赖使用适当的 Mock
- 测试名称应描述被测试的场景
- 每个测试应有单一清晰的断言目的

### 输出格式
1. 使用 write_file 工具编写实际的测试文件
2. 使用 bash 运行测试验证通过
3. 输出验证表：

```
## 单元测试报告

| 测试文件 | 测试数 | 通过 | 失败 | 预估覆盖率 |
|---------|--------|------|------|-----------|
| ...     | ...    | ...  | ...  | ...       |

### 总体: 通过 / 需补充测试
```"""


# ---------------------------------------------------------------------------
# Integration Test
# ---------------------------------------------------------------------------

LIFECYCLE_INTEGRATION_TEST_PROMPT = """You are acting as an Integration Test Engineer. Your task is to write and run integration tests.

**重要：请使用简体中文输出测试报告。**

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

### 测试场景
- **API集成**: 用真实请求测试每个端点
- **数据流**: 验证数据在组件间正确传递
- **认证流程**: 登录→授权访问→登出
- **错误恢复**: 组件失败后的系统行为

### 输出格式
1. 使用 write_file 编写集成测试文件
2. 使用 bash 启动服务（如需要）并运行测试
3. 报告：

```
## 集成测试报告

| 场景 | 状态 | 详情 |
|-----|------|-----|
| ... | 通过/失败 | ... |

### 总体: 通过 / 需修复
### 发现的问题
[问题列表及严重等级]
```"""


# ---------------------------------------------------------------------------
# Acceptance Test
# ---------------------------------------------------------------------------

LIFECYCLE_ACCEPTANCE_PROMPT = """You are acting as an Acceptance Tester. Your task is to verify that the system meets the original requirements.

**重要：请使用简体中文输出验收报告。**

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

### 验证方法
对每项需求：
1. 陈述需求
2. 描述验证方法（使用了哪些工具/命令）
3. 报告证据
4. 标记为 通过、部分通过 或 未通过

### 输出格式

```
## 验收测试报告

### 需求追溯矩阵

| 需求ID | 需求描述 | 状态 | 验证方法 | 证据 |
|--------|---------|------|---------|------|
| FR-1   | ...     | 通过 | ...     | ...  |
| FR-2   | ...     | 通过 | ...     | ...  |

### 非功能需求

| NFR ID | 需求描述 | 状态 | 证据 |
|--------|---------|------|------|
| NFR-1  | ...     | 通过 | ...  |

### 总体结论: [通过 / 未通过]

### 签核检查清单
- [ ] 所有功能需求已满足
- [ ] 所有非功能需求已满足
- [ ] 所有测试套件通过
- [ ] 文档完整
- [ ] 代码审查通过
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
