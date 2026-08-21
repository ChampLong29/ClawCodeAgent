# Claw Code Agent

Claw Code Agent 是一个用 Python 实现的本地编码智能体运行时。它提供 OpenAI 兼容和 Anthropic 原生模型接口、可审计的工具调用循环、会话持久化、结构化开发工作流，以及从任务定义、Episode、验证、数据集构建到独立 Benchmark 的实验链路。

项目当前同时服务两个目标：

- 作为可直接使用的本地 Coding Agent，通过 CLI、REPL、TUI、Web 和外部平台桥接完成代码任务。
- 作为可研究、可复现的 Agent 训练与评测底座，保存完整轨迹、验证证据、版本和成本信息。

> 当前状态：核心 Agent、Lifecycle/DevFlow、会话与上下文管理、插件/MCP、训练 Rollout、版本化任务集、可审计 Benchmark 均已有实现。SWE-bench Lite 已完成固定版本数据快照、17 题筛选（15 题通过本地准入、2 题因测试 ID 精度不足被拒绝）、安全加载边界，以及多组真实 Dev Episode 和预注册机制对照；尚未接入官方容器化 Harness，因此不能把本地结果等同于正式 SWE-bench 分数。

跨设备继续实验或训练时，从 [`TRAINING_HANDOFF.md`](TRAINING_HANDOFF.md) 开始；其中列出不会随 Git 迁移的本地资产、环境重建、准入复跑、冻结的四臂顺序和后续 LoRA 门槛。

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

### 低成本 Agent 后训练路线

当前正在将现有可审计链路扩展为：

```text
Claw Episode / Trajectory / Verification
  -> DatasetBuilder（Agent 语义、泄漏与 Tool 对齐准入）
  -> DataFlow（画像、分类、打分、筛选、平衡与导出）
  -> LLaMA-Factory 静态 SFT
  -> Patch-level GRPO/RLVR 方法验证
  -> Claw 独立 Benchmark 与 ExperimentRegistry
```

当前主线优先建立可负担、可复建的 Base、Curated SFT 与 SFT+GRPO 对照：以许可清晰的公开 Coding Agent Trajectory 补充数据规模，以少量 Claw 自采 Episode 验证端到端生产能力，并将个人云资源总预算限制在 1,500 元以内。DataFlex 动态选样后移到静态 SFT 与 RLVR 基线稳定之后，不作为当前闭环的前置条件。

详细设计与实施边界见：

- [`docs/architecture/data-centric-agent-training-design.md`](docs/architecture/data-centric-agent-training-design.md)
- [`docs/roadmap/data-centric-training-roadmap.md`](docs/roadmap/data-centric-training-roadmap.md)
- [`docs/roadmap/budget-constrained-agent-posttraining-roadmap.md`](docs/roadmap/budget-constrained-agent-posttraining-roadmap.md)
- [`docs/roadmap/swe-bench-lite-experiment-log.md`](docs/roadmap/swe-bench-lite-experiment-log.md)

当前已固定 DataFlow/DataFlex/LlamaFactory 上游版本，完成 `agent_training_record.v1`、Silver/Gold Manifest、确定性质量治理 Pipeline、DataFlow 原生 Operator，以及 LlamaFactory ShareGPT Tool-use Exporter。5 条固定脱敏 Fixture 已在隔离的 DataFlow 1.0.10 CPU 环境完成六步原生 E2E；专用 Train/Dev Episode Collector 与 Archived Episode → Silver 装配入口也已落地，并兼容 runtime adapter v2 轨迹。当前已使用 `deepseek-v4-flash` 完成 2 个简单 Train Episode → Silver → Gold 小批次，以及多种真实仓库 Dev Issue Rollout；WSL2 的 LlamaFactory 0.9.4 已完成原生 SFT 预处理。现有结果证明数据契约、真实仓库评测和失败轨迹采集链路可运行，不代表已有训练效果提升。下一步按低成本路线接入公开轨迹、扩充少量 Claw 自采数据、完成 1.5B LoRA 与小规模独立 Benchmark，再验证 0.5B Patch-level GRPO；DataFlex 和完整交互式 Agent RL 延后。

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
docs/architecture/          版本化架构设计
docs/roadmap/               版本化实施路线图
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

SWE-bench Lite Pilot 的数据版本、筛选规则、许可与隔离边界见 [`benchmarks/swe_bench_lite/README.md`](benchmarks/swe_bench_lite/README.md)。安全 Adapter、Git Archive 任务物化、验证期 Test Patch 临时挂载、候选临时副本评测和 Dev Episode Collector 已接通。当前固定 17 个任务、覆盖 6 个仓库，其中 15 题满足本地 baseline/Oracle 准入门。Runtime 支持显式 Thinking Mode、Escalation 后强制直接编辑或一次目标读取后强制编辑、一次默认关闭且不提供新任务信息的 read-to-edit 纠正请求、Critical 强制最终响应，以及路径限定的 Post-edit Contract Notice。Marshmallow-1343 产生首个本地真实仓库 Dev 成功 Episode；Marshmallow-1359 的跨 Issue 复现暴露配置父链语义遗漏。预注册 PyVista-4315 双臂均通过 1 条目标测试、114 条回归及全部硬门槛，但不支持 Notice 正确性增益。随后冻结的两任务复现中，pvlib-1606 成功，SQLFluff-1733 在首次定位正确文件时仍请求读取，被直接编辑约束拒绝并停止，最终为 1/2 成功。由此预注册的 Strict/Progressive 消融已在 Astroid-1978 与 pydicom-1256 完成四条有效 Episode：两组配对硬结果均相同，Progressive 在 pydicom 上多允许一次读取但未消除约束停止，因此结论不确定并保留原默认策略。新的 read-to-edit 纠正机制已完成契约测试；首轮两题因参数化测试 ID 截断在零模型调用准入阶段关闭，独立后续协议选择的 Astroid-1268 与 pydicom-1694 已通过准入，尚未产生模型效果证据。Verifier Policy v2 与行为诊断 v4 分别记录最终答复质量、被拒绝工具请求和真实定位时机；上述结果均为本地 Dev 证据，不是官方 SWE-bench 分数。

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
- SWE-bench Lite 已完成单任务预注册对照、两条未见 Issue 的冻结复现，以及两任务四 Episode 的 Strict/Progressive 配对消融。后者 0/4 通过硬门槛，且两组配对结果相同；Progressive 只在一题延后了一轮停止，尚无策略增益证据。样本仍太少，正式成绩需更多任务/Seed 与官方 Docker Harness。
- PEFT SFT 后端已有可审计实现，但真实 GPU 训练结果必须以运行产物和校验后的 Checkpoint 为准；Dry-run 只验证协议与证据链。
- Reviewer 可以由独立 Agent 执行，但最终指标必须与可复现测试证据分开记录，避免主观评分替代自动验证。
- DataFlow/DataFlex 已完成上游版本基线，稳定数据契约、确定性治理 Operator、Gold 证据和 LlamaFactory ShareGPT Tool-use 导出已落地；固定 Fixture、2 条简单 Train Episode 和首组真实仓库 Dev Bad Case 均已形成证据。下一步先修正 Agent 的终止效率并扩充真实 Train 数据，再运行最小 Raw/DataFlow LoRA 对照。

## License

见仓库中的许可证文件与各外部数据/仓库快照的独立许可说明。
