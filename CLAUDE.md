# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Claw Code Agent is a Python implementation of a Claude Code-style agent runtime. It emphasizes:
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
```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=local-token
export OPENAI_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct
```

### Running the Agent
```bash
python3 -m src.main agent "task" --cwd . --stream
# With custom max turns (default: 100)
python3 -m src.main agent "task" --cwd . --max-turns 50 --stream
# Interactive REPL mode
python3 -m src.main agent-chat --cwd . --max-turns 30
```

### Testing
```bash
python3 -m unittest discover -s tests -v
# Run specific test module
python3 -m unittest tests.test_agent_runtime -v
```

### GUI
```bash
python3 -m src.gui --cwd . --host 127.0.0.1 --port 8765 --stream
```

## Architecture

### Logical Layering
```
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

**Agent Core (22 files in src/):**
- `main.py` - CLI entry point with commands for agent, session, runtime management
- `agent_runtime.py` - Main agent loop, handles tool call loop, budget, compact, retry, permission callback, MCP tool registration, turn reset per query
- `agent_tools.py` - Tool protocol and execution; 10 built-in tools: list_dir, read_file, write_file, edit_file, glob_search, grep_search, bash, non_tool_call, web_search, web_fetch; plus dynamic MCP tools registered as mcp__{server}__{tool}
- `openai_compat.py` - OpenAI-compatible model client with streaming support
- `agent_context.py` - Context injection (git state, shell, platform, CLAUDE.md, runtime summaries)
- `agent_prompting.py` - System prompt assembly by runtime capability
- `agent_session.py` - Session message/transcript mutation
- `session_store.py` - Session persistence to `.port_sessions/agent/<id>.json`
- `token_budget.py` - Budget tracking and preflight checks
- `bash_security.py` - Bash command security validation (~1260 lines), SecurityResult: ALLOW/ASK/DENY/PASSTHROUGH
- `compact.py` - Context compression (~650 lines), controlled by AUTOCOMPACT_BUFFER_TOKENS
- `microcompact.py` - Lightweight compaction: tool result truncation (2000 chars), message count capping
- `query_engine.py` - Facade that drives LocalCodingAgent

**Runtime Modules (18 files in src/, each provides get_state()):**
- `mcp_runtime.py` - MCP protocol integration with `MCPRuntime` (config discovery: `.claw-mcp.json`, `.mcp.json`, `.codex-mcp.json`, `mcp.json`) and `MCPClient` (stdio subprocess management, JSON-RPC 2.0: initialize → tools/list → tools/call). Tools auto-registered in agent on startup
- `search_runtime.py` - Search providers, env var injection (SEARXNG_BASE_URL, BRAVE_SEARCH_API_KEY, TAVILY_API_KEY)
- `remote_runtime.py` - SSH/Teleport connections, discovers `.claw-remote.json`, `.remote.json`, etc.
- `account_runtime.py` - Account profiles, discovers `.claw-account.json`, `.claude/account.json`
- `hook_policy.py` - Hook/policy discovery with **walk-up** behavior (searches parent directories to root + additional_working_directories)
- `remote_trigger_runtime.py` - Remote triggers, **no walk-up** (only cwd + additional_working_directories)
- `worktree_runtime.py` - Git worktree management, state path: `<git_common_dir>/claw_worktree_runtime.json`
- `team_runtime.py` - Team config, discovers `.claw-teams.json`, `.claw-team.json`, `.claude/teams.json`

### GUI Subsystem
- 18 route modules under `src/gui/*_routes.py`, each maps to an HTTP API path prefix
- Entry point: `python3 -m src.gui`
- AgentState fields: model_name, allowed_tools, permission_mode, session_id, streaming

## Configuration Discovery Patterns

**Walk-up Runtimes** (search parent directories to root):
- `hook_policy.py`, `remote_runtime.py`

**Non-Walk-up Runtimes** (only cwd + additional_working_directories):
- `remote_trigger_runtime.py`, `config_runtime.py`

**Special Discovery Locations:**
- `team_runtime.py`: Also discovers `.claude/teams.json`
- `search_runtime.py`: Field names support both camelCase and snake_case (baseUrl/base_url)

## Directory Layout

```
.
├── src/                        # Agent source code (29 modules)
├── tests/                      # Unit tests
├── benchmarks/                 # Reserved for future benchmark tests (currently empty)
├── .port_sessions/             # Agent session persistence
│   └── agent/
│       └── <session_id>.json   # One JSON file per session
├── .venv/                      # Python virtual environment
├── pyproject.toml              # Project metadata and dependencies
└── CLAUDE.md                   # This file
```

### `.port_sessions/agent/` — Session Persistence

| 文件 | 用途 |
|------|------|
| `<session_id>.json` | 完整会话记录：messages 列表、元数据、时间戳、模型名、stop_reason |

- `session_store.py` 的 `save_agent_session()` 在每次 `run()` 结束后写入
- `load_agent_session()` 读取历史会话用于 `resume`
- `list_sessions()` 列出所有历史会话（session_id、created_at、message_count）
- 目录在 `agent_runtime.py.__post_init__` 中自动创建

### `benchmarks/` — 基准测试目录

- 目录存在但当前为空
- 预留给未来性能基准测试使用（token 消耗、响应延迟、工具调用成功率等）

---

## Memory & Context Management 机制

Agent 的记忆和上下文管理分为 **4 层**，从上到下依次生效：

### 第 1 层：会话记忆（Session Memory）
**文件**: `agent_session.py` + `session_store.py`

```
每个 run() 调用创建新 AgentSession
  ├── messages: List[Dict]    # user / assistant / tool 消息历史
  ├── created_at / updated_at  # 时间戳
  ├── stop_reason              # completed / budget_exceeded / error / stopped
  └── 持久化到 .port_sessions/agent/<id>.json
```

- **REPL 模式**: 第一次 `_execute()` 创建新会话，后续持续同一会话，可累积多轮上下文
- **CLI `agent` 模式**: 每次命令一个独立会话
- **Session 不跨进程共享** — REPL 会话在进程退出后仅保留 JSON 文件，重新启动时 `session = None`

### 第 2 层：系统提示注入（System Prompt Context）
**文件**: `agent_context.py` + `agent_prompting.py`

每次 `_run_loop()` 启动时注入以下上下文：

| 注入内容 | 来源 | 函数 |
|----------|------|------|
| Git 状态 | `git status --porcelain` + `git rev-parse` | `get_git_status()` |
| Git diff | `git diff --stat` | `get_git_diff()` |
| Shell 信息 | `$SHELL` 环境变量 | `get_shell_info()` |
| 平台信息 | `platform.system()` / `platform.release()` | `get_platform_info()` |
| CLAUDE.md | 项目目录下的 CLAUDE.md 文件 | `get_claude_md_content()` |
| Runtime 摘要 | 各 Runtime 的 `render_summary()` | `get_runtime_summaries()` |

渲染流程：
```
get_user_context() → format_context_for_prompt() / render_system_prompt()
  → [Environment Context] + [CLAUDE.md] + [Runtime Status]
  → 注入到 messages[0]（system role）
```

### 第 3 层：Token 预算与压缩（Budget & Compaction）
**文件**: `token_budget.py` + `compact.py` + `microcompact.py`

三个机制协作控制上下文大小：

```
TokenBudget.check() 每个 turn 检查:
  ├── max_total_tokens  (默认 250k)
  ├── max_output_tokens (默认 120k)
  ├── max_tool_calls    (默认 500)
  ├── max_model_calls   (默认 120)
  └── 超出 → stop_reason="budget_exceeded"

should_compact() 检测 token 超阈值:
  ├── 阈值: AUTOCOMPACT_BUFFER_TOKENS = 150k
  └── 触发 → compact_messages()
        ├── 策略: HYBRID（总结 + 截断）
        ├── 保留: system (优先级 0)、带 tool_calls 的 assistant (5)
        ├── user (10)、tool (15)、纯文本 assistant (20)
        └── 最低保留: MIN_MESSAGES_TO_KEEP = 4

truncate_tool_result() 每次工具调用后:
  └── 截断到 2000 字符（超过则加 "... [truncated N chars]"）
```

### 第 4 层：Turn 控制（Turn Limit）
**文件**: `agent_runtime.py`

```
run() 开头:
  self.turns = 0     # 每次查询重置（Fix 1）

_run_loop() 循环:
  while self.turns < max_turns:  # 默认 100，可通过 --max-turns 配置
    ...
    self.turns += 1  # 每次 tool_calls 后 +1
  → 达到上限: stop_reason="stopped"
```

### 记忆机制状态评估

| 机制 | 状态 | 说明 |
|------|------|------|
| 会话持久化 | ✅ 已建立 | JSON 文件读写，支持跨启动保存/恢复 |
| 系统提示注入 | ✅ 已建立 | Git/环境/CLAUDE.md 自动注入 |
| Token 预算控制 | ✅ 已建立 | 4 维度限制 + 超限自动停止 |
| 上下文压缩 | ✅ 已建立 | 150k token 阈值触发 compact |
| 工具结果截断 | ✅ 已建立 | 每次工具调用后 2000 字符截断 |
| Turn 计数器重置 | ✅ 已修复 | 原 bug 已修复，每 query 从 0 开始 |
| 跨会话记忆 | ⚠️ 有限 | 仅通过 resume 恢复同一 session；无长期向量化记忆 |
| 语义记忆 | ❌ 未实现 | 无语义检索、无 RAG、无 embedding 存储 |

---

## Capability Component System

Agent 的能力扩展通过三层体系实现：**Plugin（插件）** → **Skill（技能）** → **Tool（工具）**。

---

### 工具注册体系（Tool Registry）

**文件**: `src/agent_tools.py`

全局单例 `ToolRegistry` 管理所有可用工具，通过 `default_tool_registry()` 获取：

```
ToolRegistry
  ├── 10 个内置工具 (list_dir, read_file, write_file, edit_file,
  │     glob_search, grep_search, bash, non_tool_call, web_search, web_fetch, use_skill)
  ├── 动态 MCP 工具 (mcp__{server}__{tool_name})
  └── 插件虚拟工具 (command 模式 / prompt 模式)
```

**注册入口**：

| 注册方式 | 函数 | 时机 |
|----------|------|------|
| 内置工具 | `_build_default_registry()` | 首次调用 `default_tool_registry()` |
| MCP 工具 | `register_mcp_tools_in_registry()` | Agent `__post_init__` 时 |
| 虚拟工具 | `__post_init__` → `ToolRegistry.register()` | Agent初始化时自动注册 |

#### 虚拟工具的两种执行模式

虚拟工具从 plugin.json 的 `command` 字段判断执行模式：

| 模式 | 条件 | 行为 |
|------|------|------|
| **Command 模式** | 配置了 `command` 字段 | 通过 subprocess 执行命令，支持 `{arg}` 占位符替换 |
| **Prompt 模式** | 无 `command` 字段（默认） | 返回工具描述+参数作为模型上下文，用于指导下一步推理 |

**PluginConfig.virtual_tools 支持扩展字段**:
```json
{
  "name": "deploy",
  "description": "Deploy the project",
  "command": "kubectl apply -f {file}",   // 可选：command 模式
  "cwd": "/path/to/project",              // 可选：工作目录
  "parameters": {"type": "object", "properties": {"file": {"type": "string"}}}
}
```
- `command`: shell 命令模板，`{arg_name}` 会被替换为对应参数值
- `cwd`: 可选工作目录，默认为 agent 的 cwd
- 无 `command` 时自动使用 prompt 模式，返回上下文给模型

---

### 插件系统（Plugin System）

**文件**: `src/plugin_runtime.py`, `src/hook_policy.py`

#### PluginRuntime — 插件发现

**发现路径**（仅 cwd，不向上遍历）：
```
<cwd>/.codex-plugin/plugin.json          # 直接文件
<cwd>/.codex-plugin/<subdir>/plugin.json  # 子目录
<cwd>/.claw-plugin/plugin.json
<cwd>/.claw-plugin/<subdir>/plugin.json
<cwd>/plugins/<subdir>/plugin.json
```

**PluginConfig 数据结构**：
```python
@dataclass
class PluginConfig:
    name: str
    version: str
    description: str
    tool_aliases: List[Dict]    # [{"name": "search", "target": "grep_search"}]
    virtual_tools: List[Dict]   # [{"name": "...", "description": "...", "parameters": {...}}]
    blocked_tools: List[str]    # ["bash", "write_file"]
    hooks: Dict[str, Any]       # 钩子配置（未完全实现）
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
```
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

**✅ 已修复**：虚拟工具在 Agent 初始化时自动注册到 `ToolRegistry`，支持 command 执行和 prompt 上下文两种模式。详见上方「虚拟工具的两种执行模式」。

#### HookPolicyRuntime — 策略配置

**发现路径**（向上遍历到根目录）：
```
<cwd>/.claw-policy.json → <parent>/.claw-policy.json → ... → /
<cwd>/.codex-policy.json
<cwd>/.claw-hooks.json
```

首次匹配即停止，可配置：
- `deny_tool_prefixes` — 禁止的工具名前缀
- `budget` — 收紧 token 预算限制
- `hooks` — 钩子配置

**当前项目状态**：无任何 plugin.json 或 policy 配置文件存在。

---

### 技能系统（Skill System）

**文件**: `src/bundled_skills.py`

**4 个内置技能**，通过 `use_skill` 工具调用：

| 技能名 | 功能 | 参数 |
|--------|------|------|
| `explain-code` | 解释代码 | `code` (string) |
| `review-code` | 代码审查 | `code` (string) |
| `generate-tests` | 生成单元测试 | `code` (string), `language` (string) |
| `document-code` | 生成文档 | `code` (string) |

**调用流程**：
```
模型调用 use_skill(skill="explain-code", code="...")
  → _use_skill() handler
    → get_skill("explain-code") 查 BUNDLED_SKILLS
    → skill_def.prompt.format(code=code)
    → 返回格式化的提示词给模型
  → 模型根据返回的提示词生成回复
```

**⚠️ 已知缺陷**：
1. **技能纯内置**：`BUNDLED_SKILLS` 是模块常量，无法运行时注册新技能
2. **无配置文件发现**：与 Plugin 系统不同，技能没有 JSON 配置、没有目录扫描
3. **无用户自定义技能入口**：没有 `user_skills.py`、没有 `.claw-skills.json`、没有 skills 目录

---

### 能力系统对比

| 能力类型 | 注册方式 | 执行路径 | 用户可扩展 | 状态 |
|----------|----------|----------|------------|------|
| 内置工具 | 代码硬编码 `_build_default_registry()` | `ToolRegistry` → handler 函数 | ❌ | ✅ 完成 |
| MCP 工具 | 运行时动态 `register_mcp_tools_in_registry()` | `_mcp_tool()` → `MCPClient.call_tool()` | ✅ 配置文件 | ✅ 完成 |
| 虚拟工具 | 插件 `plugin.json` → `ToolRegistry.register()` | `_virtual_tool_handler()`: command / prompt 模式 | ✅ 配置文件 | ✅ 完成 |
| 工具别名 | 插件 `plugin.json` → `_tool_aliases` | 重映射到已有 handler | ✅ 配置文件 | ✅ 完成 |
| 内置技能 | 代码硬编码 `BUNDLED_SKILLS` | `_use_skill()` → 提示词模板格式化 | ❌ | ⚠️ 无扩展点 |
| 策略/钩子 | `*.json` 配置文件 → `HookPolicyRuntime` | budget 收紧 + 工具屏蔽 + 提示注入 | ✅ 配置文件 | ✅ 完成 |

---

## Key Behavioral Rules

1. Session files persist to `.port_sessions/agent/<id>.json`
2. Budget exceeded returns `stop_reason = "budget_exceeded"`
3. Compact triggers when token count exceeds AUTOCOMPACT_BUFFER_TOKENS
4. Plugin hooks execute in order: budget override → plugin registration → before-prompt → tool preflight → tool result post-hooks
5. PlanRuntime.update_plan with sync_tasks=True converts plan steps to tasks and maps depends_on to blocked_by
6. Turn counter resets to 0 on each `run()` call — turns do NOT accumulate across separate queries
7. Bash ASK commands trigger interactive permission prompt when `permission_callback` is registered (REPL: y=execute once, n=deny, a=allow all shell)
8. MCP servers start on agent init via `start_mcp_servers()`; startup failure is non-fatal; tool naming: `mcp__{server_name}__{tool_name}`
9. `max_turns` is configurable via CLI (`--max-turns`), REPL (`ClawRepl(max_turns=N)`), and `QueryEngineConfig.max_turns`
