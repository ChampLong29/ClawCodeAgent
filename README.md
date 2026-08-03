# Claw Code Agent

Claw Code Agent 是一个用 Python 实现的本地编码智能体运行时。它提供 OpenAI 兼容和 Anthropic 原生模型接口、可审计的工具调用循环、会话持久化、结构化开发工作流，以及从任务定义、Episode、验证、数据集构建到独立 Benchmark 的实验链路。

项目当前同时服务两个目标：

- 作为可直接使用的本地 Coding Agent，通过 CLI、REPL、TUI、Web 和外部平台桥接完成代码任务。
- 作为可研究、可复现的 Agent 训练与评测底座，保存完整轨迹、验证证据、版本和成本信息。

> 当前状态：核心 Agent、Lifecycle/DevFlow、会话与上下文管理、插件/MCP、训练 Rollout、版本化任务集、可审计 Benchmark 均已有实现。SWE-bench Lite 目前完成了固定版本数据快照和少量任务筛选，尚未接入官方容器化 Harness，因此不能把筛选结果等同于正式 SWE-bench 分数。

## 快速开始

要求 Python 3.9 或更高版本。

```bash
git clone <repository-url>
cd ClawCodeAgent

python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1

python -m pip install -U pip
python -m pip install -e .
```

复制 `.env.example` 为 `.env`，然后选择一种模型协议配置。

Anthropic 原生协议：

```dotenv
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_API_KEY=sk-ant-your-api-key-here
ANTHROPIC_MODEL=claude-sonnet-4-6
```

OpenAI 兼容协议（vLLM、Ollama、LiteLLM 或兼容服务）：

```dotenv
OPENAI_BASE_URL=http://127.0.0.1:8000/v1
OPENAI_API_KEY=local-token
OPENAI_MODEL=Qwen/Qwen3-Coder-30B-A3B-Instruct
```

任何 `ANTHROPIC_*` 环境变量都会选择 Anthropic 模式；只设置 `OPENAI_*` 时使用 OpenAI 兼容模式。更多示例见 [`.env.example`](.env.example)。

运行 Agent：

```bash
claw agent "修复当前项目中的失败测试" --cwd . --stream
claw agent "实现需求并运行测试" --cwd . --max-turns 50 --stream
claw agent-chat --cwd . --max-turns 30
claw tui --cwd .
```

也可以在尚未安装命令入口时从仓库运行：

```bash
PYTHONPATH=src python -m claw.main agent "分析这个仓库" --cwd . --stream
```

Windows PowerShell 对应写法：

```powershell
$env:PYTHONPATH = "src"
python -m claw.main agent "分析这个仓库" --cwd . --stream
```

## 核心能力

### Agent 运行时

- 多轮模型调用与工具执行循环，支持流式输出、重试、Turn 和 Token 预算。
- 内置文件、搜索、Shell、Web 与 Skill 工具；MCP 工具在启动时动态注册。
- Bash 安全策略、GUI 权限确认、插件级工具屏蔽与别名。
- 会话持久化、恢复、自动压缩和工具结果截断。
- 自动注入 Git 状态、平台信息、`AGENTS.md` 和各 Runtime 摘要。

### 开发工作流

- `DevFlow`：架构、步骤、逐文件模块分析、实现和验证。
- `Lifecycle`：需求、设计、开发、审查、单元测试、集成测试和验收。
- `Questionnaire`：由 Runtime 控制的逐题需求澄清。
- `Deep-Dive`：使用隔离会话研究技术问题，避免污染主开发上下文。
- 阶段回滚、归档和阶段级上下文压缩。

### 扩展能力

- MCP：发现 `.claw-mcp.json`、`.mcp.json`、`.codex-mcp.json` 或 `mcp.json`。
- Plugin：支持工具别名、虚拟工具、命令工具和工具屏蔽。
- Skill：内置代码解释、审查、测试、文档、DevFlow 和 Lifecycle 技能。
- Runtime：搜索、远程连接、账号、团队、Worktree、触发器和平台桥接等模块。

### 训练与评测

- 轻量 Rollout：`CodingTask -> RolloutRunner -> RolloutResult JSONL`，适合快速生成和浏览样本。
- 可审计链路：版本化 `TaskSpec`、隔离 Episode、追加式 Trajectory、独立 Verification、DatasetBuilder、训练后端、Benchmark 和 ExperimentRegistry。
- 训练后端：无需 GPU 的 `DryRunBackend` 与可选的 PEFT/Transformers SFT 后端。
- Benchmark：固定任务/模型/温度/Seed/版本引用，输出成功率、工具调用、Token、时延、成本和坏例分布。
- 隐藏测试：Medium Pilot 在 Agent 执行前后临时挂载测试资产，并验证内容哈希。

训练和评测的完整边界与命令见 [`TRAINING_GUIDE.md`](TRAINING_GUIDE.md)。

## 架构概览

```text
CLI / REPL / TUI / GUI / Bridge
              |
              v
       LocalCodingAgent
       |      |       |
       |      |       +-- SessionStore / Context / Budget / Compact
       |      +---------- ToolRegistry / MCP / Plugin / Policy
       +----------------- ModelClient (Anthropic / OpenAI compatible)

Versioned Task Suite
       |
       v
EpisodeOrchestrator -> RuntimeAdapter -> LocalCodingAgent
       |                    |
       |                    +-- append-only Trajectory v2
       +-- checkpoints / hidden tests / workspace reset
       |
       v
Verification v2 -> DatasetBuilder -> TrainingBackend
       |                                  |
       +------------ Benchmark <---------+
                          |
                          v
                  ExperimentRegistry
```

关键目录：

```text
src/claw/
  agent_runtime.py          Agent 主循环
  agent_tools.py            工具注册与执行
  openai_compat.py          模型协议适配
  agent_session.py          会话内消息与状态
  session_store.py          会话持久化
  devflow_runtime.py        结构化开发工作流
  lifecycle_runtime.py      完整软件工程生命周期
  episode/                  Episode、Checkpoint、恢复与运行时适配
  trajectory/               追加式轨迹与工具调用证据
  verification/             独立验证记录
  dataset/                  筛选、泄漏检查、转换和 Manifest
  training_backends/        Dry-run 与 PEFT SFT 后端
  benchmark/                独立 Benchmark 执行与报告
  experiment/               实验注册与内容寻址产物
  task_suite/               版本化任务集加载与验证
  training/                 轻量 Rollout 子系统

task_suites/
  manifest.json             32 条确定性 smoke 任务
  medium/manifest.json      2 条跨模块 Medium Pilot

benchmarks/swe_bench_lite/  固定版本数据、筛选结果和仓库快照元数据
tests/                      单元与集成测试
tools/                      任务集生成、验证和外部基准筛选脚本
```

## 常用命令

### Agent 与状态

```bash
claw agent "task" --cwd . --stream
claw agent-chat --cwd . --max-turns 30
claw sessions
claw resume <session-id>
claw agent-context --cwd .
claw agent-prompt --cwd .
claw mcp-status --cwd .
claw bridge-status --cwd .
```

查看所有可用命令和参数：

```bash
claw --help
claw <command> --help
```

### REPL 中的结构化工作流

```text
/lifecycle start <目标>
/lifecycle status
/lifecycle accept
/lifecycle reject [原因]
/lifecycle rollback <phase>

/devflow start <目标>
/devflow status
/devflow step
/devflow accept
/devflow reject [原因]
/devflow rollback <step-id>

/questionnaire start <目标>
/q back | /q skip | /q goto N
/deep-dive <technology>
/deep-dive scan
/deep-dive inject <id>
```

### 轻量 Rollout

```bash
claw train \
  --suite examples/training/sample_suite.json \
  --mode mock \
  --output .port_sessions/training/sample.jsonl

claw train-stats --input .port_sessions/training/sample.jsonl
claw trace-show --input .port_sessions/training/sample.jsonl --index 0
```

训练控制台属于 `claw` 子命令，不是独立的 `claw-train-web` 可执行文件：

```bash
python -m pip install -e ".[web]"
claw train-web --results-dir .port_sessions/training --port 8080
```

### 可审计 Benchmark

Smoke 任务用于确认端到端链路，不代表复杂工程能力：

```bash
claw benchmark-run \
  --manifest task_suites/manifest.json \
  --group base \
  --limit 1 \
  --output .port_sessions/benchmark-smoke
```

Medium Pilot 更适合观察跨模块修改、错误诊断和约束遵循：

```bash
claw benchmark-run \
  --manifest task_suites/medium/manifest.json \
  --group base \
  --limit 1 \
  --output .port_sessions/benchmark-medium
```

默认 Diff 白名单从任务的版本化 Oracle 文件推导；重复传入 `--allow-path <glob>` 可显式覆盖。输出目录包含 `benchmark-run.json`、Markdown 报告和每个 Episode 的证据。

### 任务集与 SWE-bench Lite

重新生成并验证内置任务集：

```bash
python tools/generate_task_suite.py
python tools/generate_medium_task_suite.py
python tools/validate_task_suite.py --manifest task_suites/manifest.json
python tools/validate_task_suite.py --manifest task_suites/medium/manifest.json
```

SWE-bench Lite Pilot 的数据版本、筛选规则、许可与隔离边界见 [`benchmarks/swe_bench_lite/README.md`](benchmarks/swe_bench_lite/README.md)。本仓库只提交原始数据快照、选择结果和仓库快照元数据；`benchmarks/swe_bench_lite/repos/` 下的工作副本被忽略。

## 会话与上下文

Agent 会话保存在：

```text
.port_sessions/agent/<session-id>.json
```

每次运行会记录消息、模型、工作目录、时间戳和停止原因。REPL 在进程内复用同一会话，退出后可通过 `resume` 恢复。上下文管理包含四层：

1. 会话消息与磁盘持久化。
2. Git、环境、`AGENTS.md` 和 Runtime 状态注入。
3. Token 预算、自动压缩与工具结果截断。
4. 每次查询重置的 Turn 上限。

这是可恢复的会话记忆，不是向量化长期语义记忆；当前没有 Embedding/RAG 记忆库。

## 开发与测试

安装开发依赖：

```bash
python -m pip install -e ".[dev]"
```

运行完整测试：

```bash
python -m unittest discover -s tests -v
```

运行单个模块：

```bash
python -m unittest tests.test_agent_runtime -v
```

提交前建议额外检查：

```bash
git diff --check
python tools/validate_task_suite.py --manifest task_suites/manifest.json
python tools/validate_task_suite.py --manifest task_suites/medium/manifest.json
```

## 当前边界与下一步

- `task_suites/manifest.json` 是确定性 smoke suite；用于验证机制，不用于证明模型在真实仓库上的能力。
- Medium Pilot 当前只有两条任务，适合校准 Rollout 和 Reviewer，不足以形成统计显著结论。
- SWE-bench Lite 已完成小样本筛选和固定 Commit 快照，但正式执行仍需官方 Docker Harness、依赖镜像和隔离环境。
- PEFT SFT 后端已有可审计实现，但真实 GPU 训练结果必须以运行产物和校验后的 Checkpoint 为准；Dry-run 只验证协议与证据链。
- Reviewer 可以由独立 Agent 执行，但最终指标必须与可复现测试证据分开记录，避免主观评分替代自动验证。

## License

见仓库中的许可证文件与各外部数据/仓库快照的独立许可说明。
