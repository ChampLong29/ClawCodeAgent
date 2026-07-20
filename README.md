# Claw Code Agent

Python 实现的 Claude Code 风格 Agent 运行时。通过**运行时状态机**驱动完整软件工程生命周期，在结构化流程控制、上下文管理、交互体验和回滚能力上超越纯 skill prompt 方案。

## 目录

- [与 Claude Code Skill 的对比优势](#与-claude-code-skill-的对比优势)
- [核心能力](#核心能力)
- [快速开始](#快速开始)
- [API 配置](#api-配置)
- [架构概览](#架构概览)
- [功能详解](#功能详解)
  - [Lifecycle — 完整软件工程生命周期](#lifecycle--完整软件工程生命周期)
  - [DevFlow — 结构化开发工作流](#devflow--结构化开发工作流)
  - [Questionnaire — 顺序单题交互](#questionnaire--顺序单题交互)
  - [Deep-Dive — 技术栈深钻](#deep-dive--技术栈深钻)
  - [Rollback — 任意阶段回滚](#rollback--任意阶段回滚)
  - [Context Management — 阶段级上下文管理](#context-management--阶段级上下文管理)
- [案例演示](#案例演示)
- [CLI 命令参考](#cli-命令参考)
- [安装与开发](#安装与开发)

---

## 与 Claude Code Skill 的对比优势

Claude Code 通过 skill prompt 约束 agent 按步骤执行，但 skill 本质上是**一次性注入的提示词**，缺乏运行时状态管理。本项目用**状态机运行时**替代纯 prompt 约束，在以下方面具有根本性优势：

| 能力 | Claude Code (skill 方式) | Claw Code Agent (运行时方式) |
|------|--------------------------|------------------------------|
| **流程控制** | 依赖 agent 自觉遵守 prompt 指令，可能跳步或一次性执行多步 | 运行时状态机强制执行阶段顺序，agent 无法越权 |
| **交互模式** | agent 一次性抛出 5-10 个问题，用户需逐一手打回答 | 运行时控制逐题交互，支持回退修改、跳转、修订 |
| **技术方案评估** | agent 简要列举技术栈，用户无法深入了解 | 独立 Agent Session 深钻，上下文隔离，不污染主开发流程 |
| **上下文管理** | 所有对话堆在一个 session，容易超 token 限制 | 阶段边界主动压缩，保留结构化输出，丢弃中间探索 |
| **回滚能力** | 无法回退到之前步骤 | 快照机制支持回滚到任意历史阶段 |
| **状态持久化** | prompt 文本无结构化状态 | 每个阶段的状态、输出、文件路径都被结构化存储 | **核心差异**：skill 告诉 agent "你应该怎么做"（靠自觉），运行时确保 agent "只能这么做"（靠约束）。

---

## 核心能力

- **10 阶段软件工程生命周期**：需求分析 → 系统设计 → 架构设计 → 步骤规划 → 模块分析 → 编码实现 → 代码审查 → 单元测试 → 集成测试 → 验收
- **结构化开发工作流 (DevFlow)**：架构提议 → 步骤定义 → 逐文件模块拆解 → 逐模块实现 → 逐条验收
- **顺序单题问卷**：运行时管控问答节奏，支持回退、跳转、修改
- **技术栈深钻**：独立 Agent Session，隔离上下文，一键注入摘要
- **任意阶段回滚**：基于快照的状态 + 代码双重回滚（Git reset），回滚日志可审计
- **工作区沙箱**：三层防御——macOS Seatbelt 内核隔离 + Bash 正则安全校验 + Git 变更追踪，非 macOS 自动降级，可配置开关
- **阶段权限硬约束（Action Masking）**：按阶段动态限制可用工具——需求分析阶段工具列表中不存在 write_file，agent 物理上无法写代码；等同于 RL 训练中的 action masking
- **阶段级上下文管理**：从完整会话记录动态构建精简 LLM 上下文视图（不修改原始会话），历史阶段自动压缩为摘要
- **双 API 支持**：Anthropic 原生 Messages API（tool_use/tool_result content blocks）+ OpenAI 兼容 `/chat/completions`
- **交互式 REPL + TUI**：经典 REPL 命令行 + 基于 Textual 的三栏 TUI 界面（`claw tui`）
- **Agent 训练子系统**：Gym 风格环境、沙箱隔离、多进程批量 Rollout、SLIME 数据飞轮集成（详见 [训练指南](TRAINING_GUIDE.md)）
- **SLIME 集成**：自定义 rollout function + 组合 reward model + SGLang training client，支持 PPO/GRPO/OPD
- **评测分离**：独立 Reviewer Agent 评审代码质量（安全性、架构、性能），与 Work Agent 上下文隔离
- **多领域泛化**：共用 Lifecycle 流程框架 + 可配置领域变量（Web 后端/前端/CLI/数据流水线/SDK），跨领域训练数据
- **Web GUI**：三栏布局，SSE 实时推送，可点击权限卡片
- **训练控制台 Web 端**：独立的飞轮工作台（`claw train-web`）—— 浏览 Rollout、实时对话、触发 Rollout（mock 或真实模型）、一键导出 SFT/RL 数据集，全中文 UI，SSE 流式
- **插件系统**：工具别名、虚拟工具、工具屏蔽、策略配置
- **可插拔技能系统**：15 个内置技能 + 外部 `.md`文件技能（YAML frontmatter），兼容 Claude Code skill 格式，可选`{param}`模板替换

---

## 快速开始

```bash
# 1. 克隆
git clone git@github.com:ChampLong29/ClawCodeAgent.git
cd ClawCodeAgent

# 2. 安装 uv（如尚未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. 同步环境（自动创建 .venv、安装所有依赖）
uv sync

# 4. 配置 API（选一种）
cp .env.example .env
vim .env  # 填入你的 API key

# 5. 演示或日常使用时，建议从本项目根目录启动
cd "/Users/longmozhou/Documents/文稿 - LongMacBook Pro/大模型/claudecode-python"

# 启动交互式 REPL
.venv/bin/claw agent-chat --cwd .

# 启动 TUI 界面
uv sync --extra tui
.venv/bin/claw-tui --cwd .

```

> 演示注意：不要把`uv run claw ...`作为首选演示命令。`uv run`会先检查并同步项目环境，可能出现`Uninstalled 1 package`/`Installed 1 package`，并可能覆盖`.venv/bin/claw`这类本地包装脚本；在答辩或现场展示时更容易引入不必要的不确定性。环境已经同步后，优先使用`.venv/bin/claw`、`.venv/bin/claw-tui`、`.venv/bin/claw-train-web`。

> **macOS 用户注意**：macOS Spotlight 会将`_`开头的`.pth`文件标记为隐藏，导致 editable install 的包无法导入。本项目已配置`UV_NO_EDITABLE=1`环境变量（写入`~/.zshrc`），使用非 editable 模式安装。如遇 `ModuleNotFoundError: No module named 'claw'`，请运行：
>

```bash
export UV_NO_EDITABLE=1
uv sync --extra tui --extra web

```

### 训练控制台 Web 端（数据飞轮工作台）

```bash
# 1. 安装 web 依赖
uv sync --extra web

# 2. （可选）先跑一次端到端 mock 测试，确认飞轮通路无问题（不耗 Token）
uv run pytest tests/test_training_pipeline_e2e.py

# 3. 启动控制台
.venv/bin/claw-train-web --results-dir ./results --port 8080
# 浏览器打开 http://127.0.0.1:8080/

```

如果端口已被占用，例如出现`[Errno 48] address already in use`，说明本机已经有进程监听了同一个端口。处理方式如下：

```bash
# 查看 8080 端口被哪个进程占用
lsof -nP -iTCP:8080 -sTCP:LISTEN

# 结束对应 PID，例如上一步看到 PID 是 41687
kill 41687

# 如果进程没有退出，再强制结束
kill -9 41687

# 或者临时换一个端口启动
.venv/bin/claw-train-web --results-dir ./results --port 8081

```

如果你是通过`uv run claw train-web --results-dir ./results --port 8080`启动，前面的`Uninstalled/Installed`只是`uv run`在同步本项目包，不是 Web 服务本身报错；真正导致启动失败的是后面的 8080 端口占用。

如果 Web 对话页显示`Connection error: [Errno 61] Connection refused`，表示 Agent 连不上模型服务，不是当前任务代码错误。请确认已从项目根目录启动`.venv/bin/claw-train-web --results-dir ./results --port 8080`，并重启旧 Web 进程；训练 Web 对话页会从项目根目录`.env`读取 API 地址、token 和模型名。

四个页面：

| 页面 | 用途 |
|------|------|
| 浏览  | 列出`*.jsonl`，按 reward / 测试 / diff 排序，点开看完整消息时间线 |
| 对话  | 用 `.env`配置的真实模型与智能体单轮对话，工具齐全，SSE 推送每条消息 |
| Rollout | 选任务集 → 选 mock / 真实模型 → 一键跑批，进度条 + 实时 reward |
| 导出 | 选源`*.jsonl`→ 选 SFT（按阈值过滤）/ RL（全保留） → 落盘新 dataset |

仓库自带最小示例任务集`examples/training/sample_suite.json`（2 任务，覆盖成功 / 失败两条 reward 路径）。

---

## API 配置

### 方案 A：Anthropic 原生 API

```bash
export ANTHROPIC_BASE_URL=https://api.anthropic.com
export ANTHROPIC_API_KEY=sk-ant-your-key-here
export ANTHROPIC_MODEL=claude-sonnet-4-6

```

适用于 Anthropic 官方 API 或 MiniMax 等兼容代理。代理使用 auth-token 而非 api-key 时：

```bash
export ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic
export ANTHROPIC_AUTH_TOKEN=your-auth-token-here
export ANTHROPIC_MODEL=MiniMax-M2.7

```

### 方案 B：OpenAI 兼容 API（vLLM / Ollama / LiteLLM）

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=local-token
export OPENAI_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct

```

也支持通过`.claude/settings.json`或`.claw-config.json`配置：

```json
{
  "model": {
  "provider": "anthropic",
  "baseUrl": "https://api.anthropic.com",
  "apiKey": "sk-ant-your-key-here",
  "model": "claude-sonnet-4-6"
  }
}

```
---

## 架构概览

```text
User (CLI / REPL / GUI)
  └── main.py
  └── LocalCodingAgent (agent_runtime.py)
  ├── System Prompt (agent_context.py + agent_prompting.py)
  ├── Model Client (openai_compat.py / anthropic_compat.py)
  ├── Tool Registry + Executor (agent_tools.py)
  ├── Session + Store (agent_session.py + session_store.py)
  ├── ContextManager (context_manager.py)
  └── Runtime Modules (24 modules)
  ├── LifecycleRuntime — 完整软件工程生命周期
  ├── DevFlowRuntime — 结构化开发工作流
  ├── QuestionnaireRuntime — 顺序单题交互
  ├── DeepDiveRuntime — 独立上下文技术深钻
  ├── MCPRuntime — MCP 协议集成
  ├── SearchRuntime — 搜索提供商
  ├── BridgeRuntime — 外部平台桥接
  └── ...

```

---

## 功能详解

### Lifecycle — 完整软件工程生命周期

默认 10 阶段流程：`REQUIREMENTS → SYSTEM_DESIGN → ARCHITECTURE → STEP_DEFINITION → MODULE_ANALYSIS → IMPLEMENTATION → CODE_REVIEW → UNIT_TEST → INTEGRATION_TEST → ACCEPTANCE`

```text
╭─ Lifecycle: 学生社团管理系统 ─────────────────╮
│  REQUIREMENTS  [completed]  │
│  SYSTEM_DESIGN  [completed]  │
│  进行中 ARCHITECTURE  [in_progress]  │
│  待处理 STEP_DEFINITION  [pending]  │
│  待处理 MODULE_ANALYSIS  [pending]  │
│  待处理 IMPLEMENTATION  [pending]  │
│  待处理 CODE_REVIEW  [pending]  │
│  待处理 UNIT_TEST  [pending]  │
│  待处理 INTEGRATION_TEST  [pending]  │
│  待处理 ACCEPTANCE  [pending]  │
│  Progress: ████░░░░░░░░░░░░ 20% (2/10)  │
╰────────────────────────────────────────────────╯

```

**特点**：
- **交互式问卷（新增）**：REQUIREMENTS、SYSTEM_DESIGN、ARCHITECTURE 阶段自动生成 5-8 个澄清问题，逐题引导用户回答后，将答案作为权威输入注入 skill prompt，确保产出符合用户意图而非 agent 猜测
- 每个阶段自动执行对应的 skill prompt（需求分析、系统设计、代码审查等）
- 开发阶段（ARCHITECTURE → IMPLEMENTATION）自动委托给 DevFlow
- **Action Masking**：只读阶段物理屏蔽 write_file/bash，实现阶段自动开启
- 每阶段产出物自动保存到`projects/<项目名>/docs/`目录
- 阶段间自动进行上下文压缩，保持 token 消耗可控
- 支持`accept`/`reject`/`skip` 自然语言控制（REPL + TUI）

**REPL 命令**：`/lifecycle start`、`/lifecycle accept`、`/lifecycle reject`、`/lifecycle rollback`

### DevFlow — 结构化开发工作流

6 阶段开发流程：`ARCHITECTURE → STEP_DEFINITION → STEP_ANALYSIS → IMPLEMENTATION → VERIFY → DONE`**逐文件模块拆解 (Module-by-Module)**：
- 每个实现步骤被拆解为 2-4 个文件级模块
- 逐模块实现 → 验证 → 通过后进入下一模块
- 每个模块有独立的目标、约束、验收标准

```text
╭─ DevFlow: 学生社团管理系统 ────────────────────╮
│  step-1: 项目初始化与数据模型  [verified] │
│  ├──  step-2: 用户认证系统  [verified] │
│  │  └── 进行中 step-3: 社团 CRUD API  [in_progress]│
│  └── 待处理 step-4: 前端页面与交互  [pending]  │
│  Progress: ████████░░░░ 50% (2/4 verified)  │
╰────────────────────────────────────────────────╯

```

**特点**：
- Agent 自动生成架构提议，用户审查后 approve/reject
- 步骤之间支持依赖关系（depends_on），自动拓扑排序
- 每个步骤可附带验收标准，验证阶段逐条检查
- 支持跳过失败步骤，依赖步骤自动等待

### Questionnaire — 顺序单题交互

运行时管控的问卷系统，解决 agent "一次性抛一大堆问题"的痛点：

```text
[Q 2/5] 目标用户是谁？
> 学生社团管理员和普通社员

[Q 3/5] 需要哪些平台支持？
> /back  ← 回到上一题

[Q 2/5] 目标用户是谁？  ← 上一题重新展示
> 学生社团管理员、普通社员、系统管理员  ← 修改答案

[Q 3/5] 需要哪些平台支持？  ← 自动前进
> Web 端、移动端适配
> /goto 5  ← 跳到第 5 题

[Q 5/5] 是否需要离线支持？
> 不需要，纯在线系统
> /done  ← 完成问卷

```

**与 Skill 方案的对比**：

| | Skill 方案 | QuestionnaireRuntime |
|---|---|---|
| 问题展示 | 一次性全抛 | 逐题展示 |
| 修改答案 | 需重新手打全部 |`/back`回退单题修改 |
| 跳题 | 不支持 |`/goto N` 任意跳转 |
| 状态追踪 | 无 | 每题 answered/skipped/pending |
| 产出物 | 无结构化输出 | 自动编译为 Markdown 需求文档 |

### TUI — 三模式终端界面

基于 Textual 的全功能终端 UI（`claw tui`），支持三独立 Agent 上下文：

| 模式 | 快捷键 | 功能 |
|------|--------|------|
| Chat | `Ctrl+1`| 自由对话，Shell/Write 默认关闭，实时权限切换 |
| DevFlow |`Ctrl+2`| 结构化开发流程，阶段审批与回滚 |
| Lifecycle |`Ctrl+3`| 完整工程生命周期，自动生成问卷、逐题交互，支持`accept`/`reject`/`skip` 自然语言控制 | **其他快捷键**：`Ctrl+D`Deep-Dive 隔离调研 ·`Ctrl+S`切换 Shell ·`Ctrl+W`切换 Write ·`Ctrl+Q`退出

**关键设计**：三种模式各持独立 Agent 实例与消息历史，切换不串扰；History 面板按模式分组保存会话，恢复时自动归位。

### Deep-Dive — 技术栈深钻

当 agent 在架构阶段提出技术选型时，用户可以对不熟悉的技术发起深钻：

```text
/devflow status
  ## 架构提议
  **后端框架**: FastAPI（推荐）
  **数据库**: PostgreSQL + Redis 缓存

  发现 3 个可深入了解的技术: - FastAPI
  - PostgreSQL
  - Redis
  使用 /deep-dive <技术名> 深入了解

/deep-dive PostgreSQL
  [Deep-Dive Agent] 正在分析 PostgreSQL...
  (创建独立 Agent Session，隔离上下文)

  ## PostgreSQL 深度分析
  ### 1. What is PostgreSQL?
  ### 2. Key Features
  ### 3. Pros & Cons
  ### 4. Alternatives (MySQL, SQLite, MongoDB)
  ### 5. Best Practices
  ### 6. Suitability for This Project

  深钻结果已保存 (dd-abc123)

/deep-dive inject dd-abc123  ← 将摘要注入主 Agent 上下文

```

**关键设计**：
- 深钻 Agent 使用**全新的 AgentSession**，与主开发上下文完全隔离
- 结果仅展示给用户，需手动`/deep-dive inject`才会注入摘要
- 支持`/deep-dive scan`自动扫描当前阶段输出中的技术名词

### Rollback — 任意阶段回滚

利用状态机快照实现回退到历史阶段，这是纯 skill 方案无法做到的：

```text
/lifecycle rollback-targets
  可回滚的阶段: [0] REQUIREMENTS (completed, snapshot)
  [1] SYSTEM_DESIGN (completed, snapshot)
  [2] ARCHITECTURE (completed, snapshot) ← 当前

/lifecycle rollback ARCHITECTURE
  注意 将回滚到 ARCHITECTURE 阶段，以下阶段将被丢弃： STEP_DEFINITION, MODULE_ANALYSIS, IMPLEMENTATION
  确认? (y/N) y

  已回滚到 ARCHITECTURE

```

**实现机制**：
- 每次`advance_phase()` 自动保存快照（完整运行时状态 + 压缩后的 Agent 上下文）
- 回滚时从快照恢复，后续阶段全部重置为 pending
- 回滚事件写入审计日志（`.port_sessions/lifecycle/<id>_rollbacks.jsonl`）

### Context Management — 阶段级上下文管理

运行时在每个阶段边界主动压缩上下文，而非被动等 token 超限：

```text
Phase 6 (IMPLEMENTATION) 时的上下文结构: [system prompt]
  [boundary: REQUIREMENTS]
  [system: "需求摘要: 构建学生社团管理系统..." (300 tokens)]
  [boundary: SYSTEM_DESIGN]
  [system: "设计摘要: FastAPI + PostgreSQL + React..." (400 tokens)]
  [boundary: ARCHITECTURE]
  [system: "架构摘要: 前后端分离，JWT 认证..." (350 tokens)]
  [boundary: IMPLEMENTATION]
  [当前实现的完整对话...]

总上下文: ~1500 tokens (历史摘要) + 当前阶段 (~5000-10000 tokens)
远低于 150K 触发阈值

```
---

## 案例演示

### 场景：从零构建"学生社团管理系统"

**1. 启动完整生命周期**

```bash
$ claw agent-chat --cwd /projects/club-system

╭─ [s][w] You
╰> /lifecycle start 实现学生社团管理系统 web 端，包含社团注册编辑、添加删除社员等功能

╔══════════════════════════════════════════════╗
║  Lifecycle: Full Software Engineering  ║
╠══════════════════════════════════════════════╣
║  Session: student-club-management-a1b2  ║
╚══════════════════════════════════════════════╝

Goal: 实现学生社团管理系统 web 端
Project: ./projects/student-club-management/

Running REQUIREMENTS phase...

╭─ REQUIREMENTS Output ────────────────────────╮
│ # 学生社团管理系统 — 需求规格说明书  │
│  │
│ ## 1. 项目概述  │
│ ## 2. 用户角色（管理员/社长/社员）  │
│ ## 3. 功能需求  │
│  - 社团注册与编辑  │
│  - 社员添加与删除  │
│  - 社团活动管理  │
│  - 权限控制  │
│ ## 4. 非功能需求  │
│ Review the output above.  │
│  /lifecycle accept  — approve and advance  │
│  /lifecycle reject [feedback] — retry  │
╰──────────────────────────────────────────────╯

```

**2. 审查需求，使用深钻评估技术方案**

```text
╭─ [s][w] You
╰> /lifecycle accept
Phase 'REQUIREMENTS' accepted.
Next phase: SYSTEM_DESIGN

Running SYSTEM_DESIGN phase...

╭─ SYSTEM_DESIGN Output ───────────────────────╮
│ # 系统设计文档  │
│  │
│ ## 技术栈建议  │
│ - **后端框架**: FastAPI (Python)  │
│ - **数据库**: PostgreSQL  │
│ - **ORM**: SQLAlchemy 2.0  │
│ - **前端**: React + TypeScript  │
│ - **认证**: JWT + OAuth2  │
│ - **部署**: Docker + Nginx  │
╰──────────────────────────────────────────────╯

╭─ [s][w] You
╰> /deep-dive scan
Technologies found in 'SYSTEM_DESIGN' output: - FastAPI
  - PostgreSQL
  - SQLAlchemy
  - React
  - Docker
Use /deep-dive <technology> to explore.

╭─ [s][w] You
╰> /deep-dive FastAPI
Deep-diving into: FastAPI...
(创建独立 Agent Session — 不污染主上下文)

## FastAPI — Deep Analysis
### 1. What is FastAPI?
现代 Python Web 框架，基于 Starlette + Pydantic
### 2. Key Features
- 自动 OpenAPI 文档生成
- 异步支持 (async/await)
- 类型提示驱动的数据验证
### 3. Pros & Cons
**优点**: 性能接近 Node.js/Go，开发效率高，文档自动生成
**缺点**: 生态较 Django 小，ORM 需额外集成
### 4. Alternatives
- Django + DRF: 生态完整但较重
- Flask + Flask-RESTful: 轻量但缺少内置验证
### 5. Suitability
 非常适合本项目：前后端分离架构，需要 API 文档自动生成

╭─ [s][w] You
╰> /lifecycle accept

```

**3. 架构设计与开发执行**

Agent 自动进入 DevFlow，拆解架构为步骤，逐文件模块实现：

```text
Phase 'SYSTEM_DESIGN' accepted.
Next phase: ARCHITECTURE

╭─ ARCHITECTURE Output ────────────────────────╮
│ ## 架构设计  │
│ ### 项目结构  │
│ backend/  │
│  src/models/  — 数据模型  │
│  src/routes/  — API 路由  │
│  src/services/  — 业务逻辑  │
│ frontend/  │
│  src/pages/  — 页面组件  │
│  src/components/— 通用组件  │
╰──────────────────────────────────────────────╯

... (逐步 accept → Agent 自动执行 IMPLEMENTATION) ...

```

**4. 发现问题，回滚重新设计**

```text
╭─ [s][w] You
╰> /lifecycle rollback-targets
  [0] REQUIREMENTS (completed, snapshot)
  [1] SYSTEM_DESIGN (completed, snapshot)
  [2] ARCHITECTURE (completed, snapshot)

╭─ [s][w] You
╰> /lifecycle rollback ARCHITECTURE
已回滚到 ARCHITECTURE。当前阶段可重新执行。
（Agent 的上下文已恢复到 ARCHITECTURE 时刻的状态）

╭─ [s][w] You
╰> /lifecycle reject "前后端改用单体架构，前端用模板渲染而非 SPA"
Rejecting phase 'ARCHITECTURE'...
(重新执行阶段，带上用户反馈)

```

**5. 完整流程走完后自动归档**

```text
All lifecycle phases complete!
Report saved to: .port_sessions/lifecycle/student-club-management-a1b2_archive.md

```
---

## CLI 命令参考

### Agent 命令

| 命令 | 功能 |
|------|------|
|`claw agent <prompt> --cwd . --stream`| 执行单次任务 |
|`claw agent-chat --cwd .`| 启动交互式 REPL |
|`claw sessions`| 列出所有 Agent 会话 |

### 训练相关命令

| 命令 | 功能 |
|------|------|
|`claw train --suite <suite.json> --output results.jsonl`| 跑一遍任务集，产出 Rollout JSONL |
|`claw train-stats --input results.jsonl`| 输出 Rollout 聚合统计 |
|`.venv/bin/claw-train-web --results-dir <dir> [--port 8080]`| 启动**训练控制台**：浏览 / 对话 / Rollout / 导出 SFT-RL（需`uv sync --extra web`） |

### Lifecycle 命令（REPL 内）

| 命令 | 功能 |
|------|------|
| `/lifecycle start <goal>`| 启动完整软件工程生命周期 |
|`/lifecycle status`| 查看阶段进度 |
|`/lifecycle accept`| 批准当前阶段输出，进入下一阶段 |
|`/lifecycle reject [reason]`| 拒绝并要求重新生成 |
|`/lifecycle skip-phase`| 跳过当前阶段 |
|`/lifecycle rollback <phase>`| 回滚到指定历史阶段 |
|`/lifecycle rollback-targets`| 列出可回滚的阶段 |
|`/lifecycle archive`| 导出完整报告 |

### DevFlow 命令（REPL 内）

| 命令 | 功能 |
|------|------|
|`/devflow start <goal>`| 启动结构化开发工作流 |
|`/devflow status`| 查看步骤依赖树 |
|`/devflow step`| 查看当前步骤详情 |
|`/devflow accept`| 批准当前产物 |
|`/devflow reject [reason]`| 拒绝并要求重新生成 |
|`/devflow rollback <step-id>`| 回滚到指定步骤 |
|`/devflow rollback-phase <phase>`| 回滚到指定 DevFlow 阶段 |
|`/devflow rollback-targets`| 列出可回滚的步骤 |

### 交互增强命令（REPL 内）

| 命令 | 功能 |
|------|------|
|`/questionnaire start <goal>`| 启动顺序单题问卷 |
|`/q back`/`/q skip`/`/q goto N`| 问卷导航 |
|`/deep-dive <technology>`| 独立上下文深钻分析 |
|`/deep-dive scan`| 扫描当前输出中的技术名词 |
|`/deep-dive view <id>`| 查看深钻结果 |
|`/deep-dive inject <id>`| 将深钻摘要注入主上下文 |

---

## 安装与开发

```bash
# 同步开发环境（含 pytest 等 dev 依赖）
uv sync

# 运行全部测试（339 个用例）
uv run pytest

# 运行单个测试模块
uv run pytest tests/test_context_manager.py

# 运行单个测试
uv run pytest tests/test_agent_runtime.py::TestAgentRuntimeFromSession::test_from_session_loads_existing

# 添加依赖
uv add <package>  # 运行时依赖
uv add --dev <package>  # 开发依赖

# 构建分发包
uv build

```

### 项目结构

```text
.
├── src/  # 31 个核心模块
│  ├── main.py  # CLI 入口
│  ├── agent_runtime.py  # Agent 主循环
│  ├── lifecycle_runtime.py  # 10 阶段生命周期运行时
│  ├── devflow_runtime.py  # 结构化开发工作流
│  ├── questionnaire_runtime.py # 顺序单题问卷
│  ├── deep_dive_runtime.py  # 技术深钻
│  ├── context_manager.py  # 阶段级上下文管理
│  ├── session_naming.py  # Session 命名
│  ├── sandbox.py  # 工作区沙箱（Git + macOS Seatbelt）
│  ├── skill_registry.py  # 统一技能注册表
│  ├── skill_runtime.py  # 外部技能文件发现
│  ├── gui/  # Web GUI (21 个路由模块)
│  └── training/  # Agent 训练子系统（9 个模块）
│  ├── agent_env.py  # Gym 风格环境包装器
│  ├── sandbox.py  # 隔离执行沙箱
│  ├── tasks.py  # 任务定义与套件管理
│  ├── runner.py  # 多进程批量 Rollout
│  ├── determinism.py  # 确定性配置与快照验证
│  ├── reviewer.py  # 独立评测 Agent（评测分离）
│  ├── domain_config.py  # 多领域可配置泛化
│  ├── slime_adapter.py  # SLIME 数据格式适配
│  └── web/  # 训练控制台（FastAPI + Jinja2 SSR）
├── examples/training/  # 示例任务集（飞轮 pilot 用）
├── tests/  # 355 个测试用例（30 个测试模块）
├── slime_custom_rm.py  # SLIME 自定义 reward 函数
├── TRAINING_GUIDE.md  # 智能体能力训练完整指南
├── SE-LEARNINGS.md  # 软件工程知识点汇总（17 章）
├── pyproject.toml  # 包配置与 CLI 入口点
├── uv.lock  # uv 锁文件（确保可复现安装）
├── .python-version  # Python 版本声明（uv 自动使用）
├── .env.example  # API 配置示例
├── .port_sessions/  # Session 持久化（gitignore）
└── projects/  # 生成的项目目录（gitignore）

```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
|`ANTHROPIC_BASE_URL`| Anthropic API 地址 |`https://api.anthropic.com`|
|`ANTHROPIC_API_KEY`| Anthropic API Key | - |
|`ANTHROPIC_AUTH_TOKEN`| Anthropic Auth Token（代理用） | - |
|`ANTHROPIC_MODEL`| Anthropic 模型名 |`claude-3-sonnet-20240229`|
|`OPENAI_BASE_URL`| OpenAI 兼容 API 地址 |`http://127.0.0.1:8000/v1`|
|`OPENAI_API_KEY`| OpenAI 兼容 API Key |`local-token`|
|`OPENAI_MODEL`| OpenAI 兼容模型名 |`Qwen/Qwen3-Coder-30B-A3B-Instruct`|
|`CLAW_THINKING_ENABLED` | 思考模式开关：`auto`/`true`/`false`|`auto`|
|`CLAW_THINKING_EFFORT` | 思考强度：`low`/`medium`/`high`/`max`|`medium`|
|`CLAW_THINKING_BUDGET`| 思考 token 预算（覆盖 effort 预设） | - |
|`CLAW_API_TIMEOUT`| API 请求超时（秒） |`300`|
|`CLAW_MAX_TURNS`| 最大对话轮次 |`100`|
|`CLAW_TEMPERATURE`| 采样温度 |`0.1` |

---

## License

MIT
