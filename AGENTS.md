# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Claw Code Agent is a Python implementation of a Codex-style agent runtime. It emphasizes:
- OpenAI compatible model interfaces
- Tool calling with multi-turn autonomous execution
- Local filesystem state management (sessions + runtimes)
- Extensible runtime module system

## Development Commands

### Environment Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .

```

### Required Environment Variables

**Anthropic native API:**

```bash
export ANTHROPIC_BASE_URL=https://api.anthropic.com
export ANTHROPIC_API_KEY=sk-ant-your-api-key-here
export ANTHROPIC_MODEL=Codex-sonnet-4-6

```

**OpenAI-compatible API (vLLM / Ollama / LiteLLM):**

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=local-token
export OPENAI_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct

```

**Provider selection**: Any`ANTHROPIC_*`env var forces Anthropic mode. Setting only`OPENAI_*`vars uses OpenAI-compatible mode. See`.env.example`for all supported variables and proxy-specific examples.

### Running the Agent

```bash
# After pip install -e ., use the `claw`CLI from any directory:
claw agent "task" --cwd . --stream
claw agent "task" --cwd . --max-turns 50 --stream
claw agent-chat --cwd . --max-turns 30

# Or via module (no install needed):
python3 -m claw.main agent "task" --cwd . --stream

```

### Testing

```bash
python3 -m unittest discover -s tests -v
# Run specific test module
python3 -m unittest tests.test_agent_runtime -v

```

### GUI

```bash
python3 -m claw.gui --cwd . --host 127.0.0.1 --port 8765 --stream

```

## Architecture

### Logical Layering

```text
User (CLI/GUI)
  -> src/main.py (args + routing)
  -> LocalCodingAgent (agent_runtime.py)
  -> PromptContext (agent_context.py)
  -> SystemPromptBuilder (agent_prompting.py)
  -> ModelClient (openai_compat.py)
  -> ToolRegistry/Executor (agent_tools.py)
  -> SessionState + Store (agent_session.py + session_store.py)
  -> Runtime Modules (mcp/search/remote/account/...)

```

### Core Modules

**Agent Core (31 files in src/):**
-`main.py`- CLI entry point with commands for agent, session, runtime management,`sessions`, `train`, `train-stats`, and lifecycle/bridge status commands
- `agent_runtime.py`- Main agent loop, handles tool call loop, budget, compact, retry, permission callback, MCP tool registration, turn reset per query; populates`session.cwd`and`session.model`-`agent_tools.py`- Tool protocol and execution; 11 built-in tools: list_dir, read_file, write_file, edit_file, glob_search, grep_search, bash, non_tool_call, web_search, web_fetch, use_skill; plus dynamic MCP tools registered as mcp__{server}__{tool}
-`openai_compat.py`- OpenAI-compatible model client with streaming support
-`agent_context.py`- Context injection (git state, shell, platform, AGENTS.md, runtime summaries)
-`agent_prompting.py`- System prompt assembly by runtime capability
-`agent_session.py`- Session message/transcript mutation; includes`cwd`field for working directory tracking
-`session_store.py`- Session persistence to`.port_sessions/agent/<id>.json`; `list_sessions()`returns enriched fields: model, stop_reason, cwd, updated_at
-`token_budget.py`- Budget tracking and preflight checks
-`bash_security.py`- Bash command security validation (~1260 lines), SecurityResult: ALLOW/ASK/DENY/PASSTHROUGH
-`compact.py`- Context compression (~650 lines), controlled by AUTOCOMPACT_BUFFER_TOKENS
-`microcompact.py`- Lightweight compaction: tool result truncation (2000 chars), message count capping
-`query_engine.py`- Facade that drives LocalCodingAgent; supports`permission_callback`in config for GUI integration
-`devflow_runtime.py`- DevFlow structured development workflow runtime (~1300 lines), drives state machine with module-by-module implementation
-`devflow_skills.py`- 5 DevFlow skill prompt templates (architect, step-planner, step-analyzer, implementer, verifier)
-`lifecycle_runtime.py`- Full software engineering lifecycle runtime (10 phases), wraps DevFlow for development phases
-`lifecycle_skills.py`- 6 lifecycle skill templates (requirements, design, code-review, unit-test, integration-test, acceptance)
-`context_manager.py`- Phase-level context compaction (~200 lines), keeps structured outputs from completed phases while discarding intermediate tool-call chatter; triggered at phase transitions via``advance_phase()``/``next_step()``-`questionnaire_runtime.py`- Sequential single-question interaction runtime (~350 lines), runtime-driven Q&A with back/forward navigation and answer revision; agent generates question list once, runtime controls pacing
-`deep_dive_runtime.py`- Technology deep-dive runtime (~330 lines), creates isolated AgentSession per query so research context never pollutes the main development agent; includes tech-name extraction from architecture output
-`session_naming.py`- Human-readable session ID generation from project goals (e.g., "Build a club app" →``club-app-a1b2``)
- `bridge_runtime.py`- External platform bridge integration (Feishu, WeCom), session routing by user/chat
-`training/`- Agent training subsystem: RolloutRunner, TaskSuite, sandbox, determinism utilities

**Runtime Modules (24 files in src/, each provides get_state()):**
-`mcp_runtime.py`- MCP protocol integration with`MCPRuntime`(config discovery:`.claw-mcp.json`, `.mcp.json`, `.codex-mcp.json`, `mcp.json`) and `MCPClient`(stdio subprocess management, JSON-RPC 2.0: initialize → tools/list → tools/call). Tools auto-registered in agent on startup
-`search_runtime.py`- Search providers, env var injection (SEARXNG_BASE_URL, BRAVE_SEARCH_API_KEY, TAVILY_API_KEY)
-`remote_runtime.py`- SSH/Teleport connections, discovers`.claw-remote.json`, `.remote.json`, etc.
- `account_runtime.py`- Account profiles, discovers`.claw-account.json`, `.Codex/account.json`-`hook_policy.py`- Hook/policy discovery with **walk-up** behavior (searches parent directories to root + additional_working_directories)
-`remote_trigger_runtime.py`- Remote triggers, **no walk-up** (only cwd + additional_working_directories)
-`worktree_runtime.py`- Git worktree management, state path:`<git_common_dir>/claw_worktree_runtime.json`-`team_runtime.py`- Team config, discovers`.claw-teams.json`, `.claw-team.json`, `.Codex/teams.json`-`questionnaire_runtime.py`- Interactive sequential Q&A, state path:`.port_sessions/questionnaire/<id>.json`-`deep_dive_runtime.py`- Isolated technology research sessions, state path:`.port_sessions/deepdive/<id>.json`-`devflow_runtime.py`- DevFlow structured development workflow: state machine (INIT → ARCHITECTURE → STEP_DEFINITION → STEP_ANALYSIS → IMPLEMENTATION → VERIFY → DONE) with module-by-module execution, session persistence to`.port_sessions/devflow/<id>.json`-`lifecycle_runtime.py`- Full software engineering lifecycle: 10-phase state machine (REQUIREMENTS → SYSTEM_DESIGN → ... → ACCEPTANCE → DONE), configurable via`.claw-lifecycle.json`, wraps DevFlow for development phases, persistence to `.port_sessions/lifecycle/<id>.json`-`bridge_runtime.py`- External platform bridge integration (Feishu, WeCom), session routing by (user_id, chat_id), config via`.claw-bridge.json`, webhook ingress at `/api/bridge/{name}/webhook`### GUI Subsystem
- 21 route modules under`src/gui/`, each maps to an HTTP API path prefix
- Entry point: `python3 -m claw.gui`
- Multi-threaded HTTP server (`ThreadingHTTPServer`) for concurrent request handling
- **Chat UI** — Three-panel layout (session list, chat area, input area) with streaming SSE support
- **SSE streaming** — `/api/stream`endpoint pushes real-time events: text, tool_call, permission_required, done, error
- **Permission system** —`PermissionManager`provides thread-safe blocking permission requests; GUI shows shell permission confirmation cards
- **Session management** —`/api/sessions`CRUD endpoints (list, detail, resume, delete) with enriched metadata
-`AgentState`fields: model_name, allowed_tools, permission_mode, session_id, streaming, cwd, api_config
-`GUIDatabase`manages in-memory state: event_queue for SSE, PermissionManager, sessions cache

## Configuration Discovery Patterns

**Walk-up Runtimes** (search parent directories to root):
-`hook_policy.py`, `remote_runtime.py`**Non-Walk-up Runtimes** (only cwd + additional_working_directories):
-`remote_trigger_runtime.py`, `config_runtime.py`**Special Discovery Locations:**
-`team_runtime.py`: Also discovers `.Codex/teams.json`-`search_runtime.py`: Field names support both camelCase and snake_case (baseUrl/base_url)

## Directory Layout

```text
.
├── src/  # Agent source code (39+ modules)
│  ├── gui/
│  │  ├── server.py  # GUI server (ThreadingHTTPServer, chat UI, SSE, permission)
│  │  ├── permission_manager.py  # Thread-safe permission request manager
│  │  ├── sse_routes.py  # SSE real-time streaming endpoint
│  │  ├── session_routes.py  # Session CRUD API
│  │  ├── bridge_routes.py  # Bridge webhook ingress routes
│  ├── training/  # Agent training subsystem
│  ├── ...
├── tests/  # Unit tests
├── benchmarks/  # Reserved for future benchmark tests (currently empty)
├── .port_sessions/  # Agent session persistence
│  ├── agent/
│  │  └── <session_id>.json  # One JSON file per agent session
│  ├── devflow/
│  │  └── <session_id>.json  # One JSON file per DevFlow session
│  ├── lifecycle/
│  │  └── <session_id>.json  # One JSON file per lifecycle session
│  ├── questionnaire/
│  │  └── <session_id>.json  # One JSON file per questionnaire session
│  ├── deepdive/
│  │  └── <session_id>.json  # One JSON file per deep-dive session
│  └── bridge_routing.json  # Bridge session routing table
├── projects/  # Generated project directories
│  └── <project-name>/  # One per lifecycle / devflow session
├── .venv/  # Python virtual environment
├── pyproject.toml  # Project metadata and dependencies
└── AGENTS.md  # This file

```

###`.port_sessions/agent/`— Session Persistence

| 文件 | 用途 |
|------|------|
|`<session_id>.json`| 完整会话记录：messages 列表、元数据、时间戳、模型名、stop_reason | -`session_store.py`的`save_agent_session()`在每次`run()`结束后写入
-`load_agent_session()`读取历史会话用于`resume`-`list_sessions()`列出所有历史会话（session_id、created_at、message_count）
- 目录在`agent_runtime.py.__post_init__`中自动创建

###`benchmarks/`— 基准测试目录

- 目录存在但当前为空
- 预留给未来性能基准测试使用（token 消耗、响应延迟、工具调用成功率等）

---

## Memory & Context Management 机制

Agent 的记忆和上下文管理分为 **4 层**，从上到下依次生效：

### 第 1 层：会话记忆（Session Memory）
**文件**:`agent_session.py`+`session_store.py`

```

每个 run() 调用创建新 AgentSession
  ├── messages: List[Dict]  # user / assistant / tool 消息历史
  ├── created_at / updated_at  # 时间戳
  ├── stop_reason  # completed / budget_exceeded / error / stopped
  └── 持久化到 .port_sessions/agent/<id>.json

```text
- **REPL 模式**: 第一次`_execute()`创建新会话，后续持续同一会话，可累积多轮上下文
- **CLI`agent`模式**: 每次命令一个独立会话
- **Session 不跨进程共享** — REPL 会话在进程退出后仅保留 JSON 文件，重新启动时`session = None`### 第 2 层：系统提示注入（System Prompt Context）
**文件**:`agent_context.py`+`agent_prompting.py`每次`_run_loop()`启动时注入以下上下文：

| 注入内容 | 来源 | 函数 |
|----------|------|------|
| Git 状态 |`git status --porcelain`+`git rev-parse`|`get_git_status()`|
| Git diff |`git diff --stat`|`get_git_diff()`|
| Shell 信息 |`$SHELL`环境变量 |`get_shell_info()`|
| 平台信息 |`platform.system()`/`platform.release()`|`get_platform_info()`|
| AGENTS.md | 项目目录下的 AGENTS.md 文件 |`get_claude_md_content()`|
| Runtime 摘要 | 各 Runtime 的`render_summary()`|`get_runtime_summaries()`| 渲染流程：

```text
get_user_context() → format_context_for_prompt() / render_system_prompt()
  → [Environment Context] + [AGENTS.md] + [Runtime Status]
  → 注入到 messages[0]（system role）

```

### 第 3 层：Token 预算与压缩（Budget & Compaction）
**文件**:`token_budget.py`+`compact.py`+`microcompact.py`三个机制协作控制上下文大小：

```text
TokenBudget.check() 每个 turn 检查: ├── max_total_tokens  (默认 250k)
  ├── max_output_tokens (默认 120k)
  ├── max_tool_calls  (默认 500)
  ├── max_model_calls  (默认 120)
  └── 超出 → stop_reason="budget_exceeded"

should_compact() 检测 token 超阈值: ├── 阈值: AUTOCOMPACT_BUFFER_TOKENS = 150k
  └── 触发 → compact_messages()
  ├── 策略: HYBRID（总结 + 截断）
  ├── 保留: system (优先级 0)、带 tool_calls 的 assistant (5)
  ├── user (10)、tool (15)、纯文本 assistant (20)
  └── 最低保留: MIN_MESSAGES_TO_KEEP = 4

truncate_tool_result() 每次工具调用后: └── 截断到 2000 字符（超过则加 "... [truncated N chars]"）

```

### 第 4 层：Turn 控制（Turn Limit）
**文件**:`agent_runtime.py`

```text
run() 开头: self.turns = 0  # 每次查询重置（Fix 1）

_run_loop() 循环: while self.turns < max_turns:

# 默认 100，可通过 --max-turns 配置
  ...
  self.turns += 1  # 每次 tool_calls 后 +1
  → 达到上限: stop_reason="stopped"

```

### 记忆机制状态评估

| 机制 | 状态 | 说明 |
|------|------|------|
| 会话持久化 | 已建立 | JSON 文件读写，支持跨启动保存/恢复 |
| 系统提示注入 | 已建立 | Git/环境/AGENTS.md 自动注入 |
| Token 预算控制 | 已建立 | 4 维度限制 + 超限自动停止 |
| 上下文压缩 | 已建立 | 150k token 阈值触发 compact |
| 工具结果截断 | 已建立 | 每次工具调用后 2000 字符截断 |
| Turn 计数器重置 | 已修复 | 原 bug 已修复，每 query 从 0 开始 |
| 跨会话记忆 | 注意 有限 | 仅通过 resume 恢复同一 session；无长期向量化记忆 |
| 语义记忆 | 未实现 | 无语义检索、无 RAG、无 embedding 存储 |

---

## Capability Component System

Agent 的能力扩展通过三层体系实现：**Plugin（插件）** → **Skill（技能）** → **Tool（工具）**。

---

### 工具注册体系（Tool Registry）

**文件**:`src/agent_tools.py`全局单例`ToolRegistry`管理所有可用工具，通过`default_tool_registry()`获取：

```text
ToolRegistry
  ├── 10 个内置工具 (list_dir, read_file, write_file, edit_file,
  │  glob_search, grep_search, bash, non_tool_call, web_search, web_fetch, use_skill)
  ├── 动态 MCP 工具 (mcp__{server}__{tool_name})
  └── 插件虚拟工具 (command 模式 / prompt 模式)

```

**注册入口**：

| 注册方式 | 函数 | 时机 |
|----------|------|------|
| 内置工具 |`_build_default_registry()`| 首次调用`default_tool_registry()`|
| MCP 工具 |`register_mcp_tools_in_registry()`| Agent`__post_init__`时 |
| 虚拟工具 |`__post_init__`→`ToolRegistry.register()`| Agent初始化时自动注册 |

#### 虚拟工具的两种执行模式

虚拟工具从 plugin.json 的`command`字段判断执行模式：

| 模式 | 条件 | 行为 |
|------|------|------|
| **Command 模式** | 配置了`command`字段 | 通过 subprocess 执行命令，支持`{arg}`占位符替换 |
| **Prompt 模式** | 无`command`字段（默认） | 返回工具描述+参数作为模型上下文，用于指导下一步推理 | **PluginConfig.virtual_tools 支持扩展字段**:

```json
{
  "name": "deploy",
  "description": "Deploy the project",
  "command": "kubectl apply -f {file}",  // 可选：command 模式
  "cwd": "/path/to/project",  // 可选：工作目录
  "parameters": {"type": "object", "properties": {"file": {"type": "string"}}}
}

```text
-`command`: shell 命令模板，`{arg_name}`会被替换为对应参数值
-`cwd`: 可选工作目录，默认为 agent 的 cwd
- 无 `command`时自动使用 prompt 模式，返回上下文给模型

---

### 插件系统（Plugin System）

**文件**:`src/plugin_runtime.py`, `src/hook_policy.py`#### PluginRuntime — 插件发现

**发现路径**（仅 cwd，不向上遍历）：

```text
<cwd>/.codex-plugin/plugin.json  # 直接文件
<cwd>/.codex-plugin/<subdir>/plugin.json  # 子目录
<cwd>/.claw-plugin/plugin.json
<cwd>/.claw-plugin/<subdir>/plugin.json
<cwd>/plugins/<subdir>/plugin.json

```

**PluginConfig 数据结构**：

```python
@dataclass
class PluginConfig: name: str
  version: str
  description: str
  tool_aliases: List[Dict]  # [{"name": "search", "target": "grep_search"}]
  virtual_tools: List[Dict]  # [{"name": "...", "description": "...", "parameters": {...}}]
  blocked_tools: List[str]  # ["bash", "write_file"]
  hooks: Dict[str, Any]  # 钩子配置（未完全实现）
  tool_hooks: Dict[str, Any]  # 按工具钩子配置（未完全实现）

```

**plugin.json 示例**：

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "blocked_tools": ["bash"],
  "tool_aliases": [{"name": "search", "target": "grep_search"}],
  "virtual_tools": [
  {
  "name": "deploy",
  "description": "Deploy the project",
  "command": "kubectl apply -f {file}",
  "parameters": {"type": "object", "properties": {"file": {"type": "string"}}, "required": ["file"]}
  }
  ]
}

```

**插件生效流程**：

```text
__post_init__
  ├── PluginRuntime(cwd=self.cwd).plugins → List[PluginConfig]
  ├── _blocked_tools = deny_tool_prefixes + plugin.blocked_tools
  ├── _tool_aliases = {alias.name: alias.target}
  └── _virtual_tools = [virtual_tool_dicts...]

_get_toolspec()
  ├── 跳过 _blocked_tools 中的内置工具 → 模型不可见
  └── 追加 _virtual_tools → 模型可见

_run_loop() 工具执行
  ├── 工具名在 _blocked_tools → 返回 "blocked by policy" 错误
  └── 应用别名: actual_name = _tool_aliases.get(tool_name, tool_name)

```

** 已修复**：虚拟工具在 Agent 初始化时自动注册到`ToolRegistry`，支持 command 执行和 prompt 上下文两种模式。详见上方「虚拟工具的两种执行模式」。

#### HookPolicyRuntime — 策略配置

**发现路径**（向上遍历到根目录）：

```text
<cwd>/.claw-policy.json → <parent>/.claw-policy.json → ... → /
<cwd>/.codex-policy.json
<cwd>/.claw-hooks.json

```

首次匹配即停止，可配置：
-`deny_tool_prefixes`— 禁止的工具名前缀
-`budget`— 收紧 token 预算限制
-`hooks`— 钩子配置

**当前项目状态**：无任何 plugin.json 或 policy 配置文件存在。

---

### 技能系统（Skill System）

**文件**:`src/bundled_skills.py`**15 个内置技能**，通过`use_skill`工具调用： **通用技能 (4)：**
| 技能名 | 功能 | 参数 |
|--------|------|------|
|`explain-code`| 解释代码 |`code`(string) |
|`review-code`| 代码审查 |`code`(string) |
|`generate-tests`| 生成单元测试 |`code`(string),`language`(string) |
|`document-code`| 生成文档 |`code`(string) | **DevFlow 技能 (5)：**
| 技能名 | 功能 | 参数 |
|--------|------|------|
|`devflow-architect`| 分析需求并提议架构 |`goal`, `constraints`|
|`devflow-step-planner`| 将架构拆解为步骤 |`goal`, `architecture`|
|`devflow-step-analyzer`| 将步骤拆解为模块（逐文件） |`goal`, `architecture`, `step_title`, `step_goal`, `step_constraints`|
|`devflow-implementer`| 实现单个步骤/模块 |`goal`, `architecture`, `step_title`, `step_goal`, `step_constraints`, `acceptance_criteria`, `previous_steps_summary`|
|`devflow-verifier`| 验证实现是否满足验收标准 |`step_title`, `acceptance_criteria`, `implementation_result`| **Lifecycle 技能 (6)：**
| 技能名 | 功能 | 参数 |
|--------|------|------|
|`lifecycle-requirements`| 需求分析（EARS格式、用户故事） |`goal`, `constraints`|
|`lifecycle-design`| 系统设计（模块、数据模型、API） |`goal`, `requirements_summary`, `constraints`|
|`lifecycle-code-review`| 代码审查（安全、性能、可维护性） |`goal`, `implementation_summary`|
|`lifecycle-unit-test`| 单元测试（覆盖率>80%） |`goal`, `implementation_summary`|
|`lifecycle-integration-test`| 集成测试（API、E2E） |`goal`, `requirements_summary`, `implementation_summary`|
|`lifecycle-acceptance`| 验收测试（需求追溯矩阵） |`goal`, `requirements_summary`, `implementation_summary`| **调用流程**：

```

模型调用 use_skill(skill="explain-code", code="...")
  → _use_skill() handler
  → get_skill("explain-code") 查 BUNDLED_SKILLS
  → skill_def.prompt.format(code=code)
  → 返回格式化的提示词给模型
  → 模型根据返回的提示词生成回复

```

**已知局限**：
1. **技能纯内置**：`BUNDLED_SKILLS`是模块常量，无法运行时注册新技能
2. **无配置文件发现**：与 Plugin 系统不同，技能没有 JSON 配置、没有目录扫描
3. **无用户自定义技能入口**：没有`user_skills.py`、没有 `.claw-skills.json`、没有 skills 目录
4. **`use_skill`参数硬编码**：工具定义中 skill 参数的 description 需手动同步

---

### 能力系统对比

| 能力类型 | 注册方式 | 执行路径 | 用户可扩展 | 状态 |
|----------|----------|----------|------------|------|
| 内置工具 | 代码硬编码`_build_default_registry()`|`ToolRegistry`→ handler 函数 | | 完成 |
| MCP 工具 | 运行时动态`register_mcp_tools_in_registry()`|`_mcp_tool()`→`MCPClient.call_tool()`| 配置文件 | 完成 |
| 虚拟工具 | 插件`plugin.json`→`ToolRegistry.register()`|`_virtual_tool_handler()`: command / prompt 模式 | 配置文件 | 完成 |
| 工具别名 | 插件 `plugin.json`→`_tool_aliases`| 重映射到已有 handler | 配置文件 | 完成 |
| 内置技能 | 代码硬编码`BUNDLED_SKILLS`|`_use_skill()`→ 提示词模板格式化 | | 注意 无扩展点 |
| 策略/钩子 |`*.json`配置文件 →`HookPolicyRuntime`| budget 收紧 + 工具屏蔽 + 提示注入 | 配置文件 | 完成 |

---

## DevFlow: 结构化开发工作流系统

**文件**:`src/devflow_runtime.py`, `src/devflow_skills.py`, `src/bundled_skills.py`, `src/repl.py`### 概述

DevFlow 是一个结构化的开发流程管理系统，通过四层联动（Workflow + Skills + Runtime + REPL）引导完整开发流程：

| 层级 | 组件 | 职责 |
|------|------|------|
| Workflow |`DevFlowSession`| 定义开发生命周期状态机 |
| Skills | 4 个 bundled skills | 每个阶段注入专门的提示词模板 |
| Runtime |`DevFlowRuntime`| 持久化 session 状态，驱动阶段切换 |
| REPL |`/devflow`命令族 | 用户交互界面 |

### 生命周期状态机

```text
INIT → ARCHITECTURE → STEP_DEFINITION → STEP_ANALYSIS → IMPLEMENTATION → VERIFY → DONE
  │  │  │  │  │  │  │
  │  Agent分析需求  Agent生成步骤  Agent拆解模块  Agent逐个  Agent验证  所有步骤
  │  生成架构提议  含依赖关系  逐文件分析  实现模块  验收标准  完成

```text
- **STEP_ANALYSIS** 是新增阶段：将每个步骤拆解为逐文件的实现模块（每个模块 = 1 个文件）
- Module-by-module 模式：实现阶段逐个模块执行（implement → verify → next module），而非一次性实现整个步骤

### DevFlowSession 数据结构

```text
DevFlowSession: session_id: str  # 唯一会话 ID
  overall_goal: str  # 开发总目标
  user_constraints: str  # 用户额外约束
  architecture: Optional[str]  # Agent 提议的架构（Markdown）
  steps: List[DevFlowStep]  # 步骤列表
  current_step_index: int  # 当前步骤索引
  phase: str  # 状态机阶段

DevFlowStep: id: str  # 步骤 ID（如 step-1）
  title: str  # 步骤标题
  goal: str  # 本步骤目标
  constraints: str  # 本步骤约束条件
  acceptance_criteria: str  # 验收标准
  status: str  # pending | in_progress | implemented | verified | failed
  depends_on: List[str]  # 依赖的步骤 ID
  modules: List[DevFlowModule] # 实现模块列表（逐文件拆解）
  implementation_result: Optional[str]
  verification_result: Optional[str]

DevFlowModule: id: str  # 模块 ID（如 module-1）
  file_path: str  # 目标文件路径（如 src/models/user.py）
  goal: str  # 本模块目标
  constraints: str  # 本模块约束
  acceptance_criteria: str  # 本模块验收标准
  status: str  # pending | implemented | verified | failed
  implementation_result: Optional[str]
  verification_result: Optional[str]

```

### 5 个 DevFlow Skills

| Skill | 用途 | 注入时机 |
|-------|------|----------|
|`devflow-architect`| 分析需求并提议架构 | ARCHITECTURE 阶段 |
|`devflow-step-planner`| 将架构拆解为步骤（含依赖） | STEP_DEFINITION 阶段 |
|`devflow-step-analyzer`| 将步骤拆解为逐文件模块 | STEP_ANALYSIS 阶段 |
|`devflow-implementer`| 实现单个步骤/模块，严格按约束 | IMPLEMENTATION 阶段 |
|`devflow-verifier`| 逐条验证验收标准 | VERIFY 阶段 | 每个 Skill 通过`use_skill`工具调用，或由`DevFlowRuntime.get_prompt_guidance()`自动注入到 system prompt。

### DevFlowRuntime 核心方法

```python
class DevFlowRuntime(RuntimeBase): start_session(goal, constraints="") → DevFlowSession
  propose_architecture(agent) → str
  approve_architecture(architecture=None) → None
  generate_steps(agent) → List[DevFlowStep]
  approve_steps(steps=None) → None
  analyze_step(agent) → List[DevFlowModule]  # NEW: module breakdown
  approve_modules(step=None) → None  # NEW
  execute_step(agent) → str
  execute_module(agent) → str  # NEW: per-module execution
  verify_step(agent) → str
  verify_module(agent) → str  # NEW: per-module verification
  next_step() → bool
  next_module() → bool  # NEW
  skip_step() → bool
  skip_module() → bool  # NEW
  retry_step() → None
  retry_module() → None  # NEW
  save() / load(session_id)
  archive(output_path=None) → str  # Markdown report

```

### System Prompt 注入机制

当 DevFlow 活跃时，`DevFlowRuntime.get_prompt_guidance()`根据当前阶段返回专用提示：
- **ARCHITECTURE**: 分析需求、提议架构的指令
- **STEP_DEFINITION**: 拆解步骤、定义依赖的指令
- **IMPLEMENTATION**: 当前步骤的目标、约束、验收标准 + 之前步骤摘要
- **VERIFY**: 验收标准 + 实现结果，要求逐条检查
- **DONE**: 完成总结

注入通过`agent_prompting.py`的`render_system_prompt()`自动完成（遍历所有 runtime 调用`get_prompt_guidance()`）。

### REPL 命令

**DevFlow 命令族：**
| 命令 | 功能 |
|------|------|
| `/devflow start <目标>`| 启动新开发流程 |
|`/devflow status`| 查看整体进度和依赖树 |
|`/devflow step`| 查看当前步骤和模块详情 |
|`/devflow accept`| 批准当前阶段输出（架构 / 步骤 / 模块 / 验证结果） |
|`/devflow reject [原因]`| 拒绝并要求重新生成 |
|`/devflow skip`| 跳过当前步骤或模块 |
|`/devflow archive`| 保存完整 session 到 Markdown 文件 |
|`/devflow list`| 列出所有已保存的 session |
|`/devflow load <id>`| 加载已保存的 session |
|`/devflow rollback <step-id>`| 回滚到指定步骤 |
|`/devflow rollback-phase <phase>`| 回滚到指定 DevFlow 阶段 |
|`/devflow rollback-targets`| 列出可回滚的步骤/阶段 | **通用 REPL 命令：**
| 命令 | 功能 |
|------|------|
|`/name <name>`| 设置或显示会话名称 |
|`/sessions`| 列出所有已保存的 agent 会话 |
|`/resume <id>`| 恢复已保存的会话 |
|`/questionnaire start <goal>`| 启动顺序单题问卷（需求收集） |
|`/q back / /q skip / /q goto N`| 问卷导航（回退/跳过/跳转） |
|`/deep-dive <technology>`| 独立上下文技术深钻分析 |
|`/deep-dive scan`| 扫描当前输出中的技术名词 |
|`/deep-dive inject <id>` | 将深钻摘要注入主 agent 上下文 |

### 终端可视化

**依赖树**（`/devflow status`）:

```text
╭─ DevFlow: 用户认证系统 ──────────────────────╮
│  step-1: 定义数据模型  [verified] │
│  ├──  step-2: 实现注册接口  [verified] │
│  │  └── 进行中 step-3: 实现登录接口 [in_progress] │
│  └── 待处理 step-4: 密码加密工具  [pending]  │
│  Progress: ████████░░░░ 50% (2/4 verified)  │
╰───────────────────────────────────────────────╯

```

图例:``verified,`进行中`in_progress,`已实现`implemented,`待处理`pending,`失败` failed

**步骤详情**（`/devflow step`）:
显示当前步骤的目标、约束、验收标准、依赖关系、模块列表和状态。

### 持久化

- Session 文件: `.port_sessions/devflow/<session_id>.json`- 归档文件:`devflow-<session_id>.md`（通过 `/devflow archive`生成）

---

## Lifecycle: 完整软件工程生命周期

**文件**:`src/lifecycle_runtime.py`, `src/lifecycle_skills.py`, `src/bundled_skills.py`, `src/repl.py`

### 概述

Lifecycle 是一个完整的软件工程生命周期管理系统，**包装 DevFlow** 用于开发阶段，同时增加需求分析、系统设计、代码审查、测试和验收阶段。两种模式并存：
- **快速模式** (`/devflow`) — 直接进入开发
- **完整模式** (`/lifecycle`) — 完整 10 阶段流程

### 生命周期阶段（默认 10 阶段）

```text
REQUIREMENTS → SYSTEM_DESIGN → ARCHITECTURE → STEP_DEFINITION
  → MODULE_ANALYSIS → IMPLEMENTATION → CODE_REVIEW → UNIT_TEST
  → INTEGRATION_TEST → ACCEPTANCE → DONE

```text
- 前 5 个开发阶段（ARCHITECTURE → VERIFY）委托给 DevFlow
- 其余由 LifecycleRuntime 直接管理

### 各阶段职责

| 阶段 | 技能 | 产出 | 工具权限 |
|------|------|------|----------|
| REQUIREMENTS |`lifecycle-requirements`|`docs/requirements_{id}.md`| read-only |
| SYSTEM_DESIGN |`lifecycle-design`|`docs/design_{id}.md`| read-only |
| ARCHITECTURE |`devflow-architect`| 架构文档 | read-only |
| STEP_DEFINITION |`devflow-step-planner`| Step 列表 | read-only |
| MODULE_ANALYSIS |`devflow-step-analyzer`| Module 拆分 | read-only |
| IMPLEMENTATION |`devflow-implementer`| 代码 | write |
| CODE_REVIEW |`lifecycle-code-review`|`docs/code-review_{id}.md`| read-only |
| UNIT_TEST |`lifecycle-unit-test`| 单元测试 | write |
| INTEGRATION_TEST |`lifecycle-integration-test`| 集成测试 | write |
| ACCEPTANCE |`lifecycle-acceptance`|`docs/acceptance_{id}.md`| read-only |

### 阶段配置

通过`.claw-lifecycle.json`自定义启用的阶段：

```json
{
  "phases": ["REQUIREMENTS", "ARCHITECTURE", "IMPLEMENTATION", "UNIT_TEST", "ACCEPTANCE"],
  "skip_phases": ["SYSTEM_DESIGN", "CODE_REVIEW", "INTEGRATION_TEST"]
}

```

### LifecycleRuntime 核心方法

```python
class LifecycleRuntime(RuntimeBase): start_session(goal, constraints="", phase_list=None) → LifecycleSession
  execute_phase(agent) → str  # Run current phase with agent
  advance_phase() → bool  # Accept & move to next
  skip_phase() → bool  # Skip current phase
  retry_phase() → None  # Reset for retry
  save() / load(session_id)
  list_sessions() → List[Dict]
  archive(output_path=None) → str  # Full lifecycle Markdown report

```

### Lifecycle REPL 命令

| 命令 | 功能 |
|------|------|
|`/lifecycle start <目标>`| 启动完整生命周期（可加`--phases`指定阶段） |
|`/lifecycle status`| 显示生命周期进度、各阶段状态 |
|`/lifecycle accept`| 批准当前阶段产物并进入下一阶段 |
|`/lifecycle reject [原因]`| 拒绝并要求重做当前阶段 |
|`/lifecycle skip-phase`| 跳过当前阶段 |
|`/lifecycle archive`| 导出完整生命周期报告 Markdown |
|`/lifecycle list`| 列出所有保存的会话 |
|`/lifecycle load <id>`| 加载已保存的会话 |
|`/lifecycle rollback <phase>`| 回滚到指定阶段 |
|`/lifecycle rollback-targets`| 列出可回滚的阶段 |

### 持久化

```text
.port_sessions/lifecycle/{session_id}.json  — 生命周期会话
.port_sessions/devflow/{devflow_id}.json  — DevFlow 子会话（不变）
docs/requirements_{session_id}.md  — 需求文档
docs/design_{session_id}.md  — 设计文档
docs/code-review_{session_id}.md  — 代码审查报告
docs/acceptance_{session_id}.md  — 验收报告

```text
---

## Bridge: 外部平台桥接集成

**文件**:`src/bridge_runtime.py`, `src/gui/bridge_routes.py`### 概述

Bridge 支持通过 webhook 将外部平台（飞书、企业微信）连接到 agent 会话。每个 bridge 配置一个 webhook 入口端点，将 (user_id, chat_id) 映射到持久化的 session_id。

### BridgeConfig

```python
@dataclass
class BridgeConfig: name: str  # 唯一名称（如 "feishu_main"）
  type: str  # 平台类型（"feishu", "wecom"）
  enabled: bool = True
  webhook_url: str  # ingress 端点路径（如 "/api/bridge/feishu_main/webhook"）

```

### 配置发现`.claw-bridge.json`（仅 cwd，非向上遍历）：

```json
{
  "bridges": [
  {"name": "feishu_main", "type": "feishu", "enabled": true,
  "webhook_url": "/api/bridge/feishu_main/webhook"}
  ]
}

```

### 会话路由

- 路由键:`{user_id}:{chat_id}`- 会话命名:`{bridge_type}/{user_id}/{chat_id}`- 路由表持久化到`.port_sessions/bridge_routing.json`- GUI 路由:`/api/bridge/{name}/webhook`（webhook ingress）、`/api/bridge/{name}/sessions`、`/api/bridge/status`、`/api/bridge/routing`---

## Training: Agent 训练子系统

**文件**:`src/training/`### 概述

Training 子系统支持对 agent 进行 rollout 训练，包括任务定义、并行执行、determinism 控制和结果评估。

### 组件

| 模块 | 功能 |
|------|------|
|`tasks.py`| 编码任务定义（CodingTask dataclass） |
|`runner.py`| RolloutRunner — 并行/串行执行训练 episode |
|`sandbox.py`| 沙箱环境管理 |
|`agent_env.py`| Agent 环境和配置 |
|`determinism.py`| Determinism 工具（种子控制） |

### CLI 命令

```bash
# 单个任务训练
python3 -m claw.main train --task '{"goal":"...",...}' --max-turns 50

# 任务套件训练
python3 -m claw.main train --suite tasks.json --workers 4 --output trajectories.jsonl

# 查看训练统计
python3 -m claw.main train-stats --input trajectories.jsonl

```text
---

## Key Behavioral Rules

1. Session files persist to`.port_sessions/agent/<id>.json`2. Budget exceeded returns`stop_reason = "budget_exceeded"`3. Compact triggers when token count exceeds AUTOCOMPACT_BUFFER_TOKENS
4. Plugin hooks execute in order: budget override → plugin registration → before-prompt → tool preflight → tool result post-hooks
5. PlanRuntime.update_plan with sync_tasks=True converts plan steps to tasks and maps depends_on to blocked_by
6. Turn counter resets to 0 on each`run()`call — turns do NOT accumulate across separate queries
7. Bash ASK commands trigger interactive permission prompt when`permission_callback`is registered (REPL: y=execute once, n=deny, a=allow all shell)
8. MCP servers start on agent init via`start_mcp_servers()`; startup failure is non-fatal; tool naming: `mcp__{server_name}__{tool_name}`9.`max_turns` is configurable via CLI (`--max-turns`), REPL (`ClawRepl(max_turns=N)`), and `QueryEngineConfig.max_turns`10. DevFlow supports module-by-module implementation: each step can be broken into file-level modules via STEP_ANALYSIS phase
11. Lifecycle phases in DevFlow range (ARCHITECTURE → MODULE_ANALYSIS) delegate to DevFlow runtime automatically
12. Bridge session routing persists to`.port_sessions/bridge_routing.json`; sessions named `{type}/{user_id}/{chat_id}`13. Lifecycle and Bridge runtimes are auto-discovered by agent via`_runtime_classes`registration
14.`use_skill` tool now supports arbitrary kwargs forwarding for lifecycle/DevFlow skill templates
