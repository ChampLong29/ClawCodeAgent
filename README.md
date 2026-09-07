# Claw Code Agent

Claw Code Agent 是一个用 Python 实现的本地编码智能体运行时。它提供 OpenAI 兼容和 Anthropic 原生模型接口、可审计的工具调用循环、会话持久化、结构化开发工作流，以及从任务定义、Episode、验证、数据集构建到独立 Benchmark 的实验链路。

项目当前同时服务两个目标：

- 作为可直接使用的本地 Coding Agent，通过 CLI、REPL、TUI、Web 和外部平台桥接完成代码任务。
- 作为可研究、可复现的 Agent 训练与评测底座，保存完整轨迹、验证证据、版本和成本信息。

> 证据状态：核心 Agent、Lifecycle/DevFlow、会话与上下文管理、插件/MCP、训练 Rollout、版本化任务集和可审计 Benchmark 均已有实现。SWE-bench Lite 已完成固定版本数据快照、20 题筛选（18 题通过本地准入）、多组冻结 Dev Episode、跨模型 Tool UX 对照和官方 Harness 兼容性复评。所有结果按“实现证据、机制证据、兼容性证据和正式 Benchmark”分层记录，避免把小样本或修复过的环境误报为排行榜成绩。

跨设备继续实验或训练时，从 [`TRAINING_HANDOFF.md`](TRAINING_HANDOFF.md) 开始；其中列出不会随 Git 迁移的本地资产、环境重建、已完成四臂证据和后续 LoRA 门槛。

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

本地 Qwen3-1.7B 的重复、多轮评测应使用常驻推理服务，而不是逐 Episode
调用 Transformers `generate()`。当前 RTX 5080 + WSL2 的已验证配置见
[`configs/inference/qwen3-1.7b-vllm.json`](configs/inference/qwen3-1.7b-vllm.json)：

```bash
uv venv --python 3.12 .venv-vllm
uv pip install --python .venv-vllm/bin/python "vllm==0.28.0" --torch-backend=auto
bash tools/start_vllm_qwen3.sh
```

服务启动并预热后，另一个终端运行：

```bash
python3 tools/probe_openai_tool_server.py
```

该脚本固定关闭 Qwen3 Thinking，开启工具调用解析，并针对 WSL2 的 UVA 与
RTX 50 系 FlashInfer sampler 兼容问题采用 vLLM 原生回退。首次服务启动和首次
内核编译单独计时；只有热态请求用于判断 Episode 推理吞吐。Transformers
直连仅保留为模型加载和协议解析冒烟，不作为 SWE-bench 抽检后端。

Qwen3-1.7B 的首个冻结本地 SWE-bench Lite 筛选在 pydicom-1694 上按停止规则
结束：模型首回合定位正确文件，但重复相同搜索七次且未编辑，最终编辑请求的
JSON 格式错误，因此没有继续执行后两题。该结果说明推理吞吐边界已经打通，
但不能据此宣称已有最低成功率。Runtime 现已实现可选的重复只读 Action Guard：
只有完全相同且此前成功的读/搜索/目录/大纲请求、且期间没有可能修改工作区的
Action 时才会在分发前拒绝；可允许一次不提供新任务信息的纠正请求，再次重复则
显式停止。该机制默认关闭，单元、适配器契约及一次冻结的本地 Qwen 机制复验均已
验证：两次重复搜索被拒绝，模型在首次纠正后短暂改用不同观察，第二次重复后明确
停止；实际工具分发由 8 次降至 4 次、总 Token 由 39,332 降至 20,854，但仍未编辑且
目标测试失败，因此不代表模型正确率提升。
协议与结果分别见
[`qwen3-local-swebench-screening-protocol.json`](configs/integrations/qwen3-local-swebench-screening-protocol.json)
和
[`qwen3-local-swebench-screening-result.json`](configs/integrations/qwen3-local-swebench-screening-result.json)；
机制复验见
[`qwen3-local-swebench-loop-guard-validation-result.json`](configs/integrations/qwen3-local-swebench-loop-guard-validation-result.json)。

Tool UX v2 的本地跨模型冻结抽检已完成，完整证据见
[`tool-ux-v2-cross-model-smoke-result.json`](configs/integrations/tool-ux-v2-cross-model-smoke-result.json)：
Qwen3-1.7B 为 0/10，DeepSeek-V4-Flash 为 8/10（Core 8/8，Medium 0/2）。两个臂均无流程违规；
Qwen 的主要失败是零工具调用或错误目标定位，DeepSeek 的 Medium 失败则记录为 token-limit
终止及隐藏测试失败。该结果支持“当前小模型能力/后训练是主要瓶颈”的判断，但不是 SWE-bench
成绩，也不是纯模型大小对照。

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
- Tool Schema v2 支持按符号过滤大纲，以及有界、分页、带上下文和文件过滤的代码搜索；
  SWE Episode 仅向模型暴露本地任务所需工具。
- Bash 安全策略、GUI 权限确认、插件级工具屏蔽与别名。
- 可选的重复只读 Action Guard，在无中间修改时阻止完全相同的观察请求反复执行。
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
- 官方 Harness 适配：可生成 SWE-bench 预测、执行容器化复评并归档报告、测试输出、镜像和补丁哈希；环境修复必须作为独立兼容层披露。
- 行为诊断：从不可变 Trajectory 派生目标定位、编辑时机、模型请求/实际分发/策略拒绝、重复观察和失败恢复信号。

训练和评测的完整边界与命令见 [`TRAINING_GUIDE.md`](TRAINING_GUIDE.md)。

### Harness 对照方法

项目将模型能力、Harness 基础实现和运行时控制拆成可配对的三层：DeepSeek Harness
`sdk-minimal` 作为外部参考，Claw Minimal 用于检查工具与协议的实现等价性，Claw
Controlled 在相同模型、任务、容器和预算上启用权限、Action Masking、恢复与独立
Verifier。比较预先固定原始 Resolved、合规 Resolved、Token/工具预算效率及失败恢复，
同时报告安全收益是否以任务成功率或成本为代价，而不是只选择对 Claw 有利的指标。

这一方法由现有能力直接支撑：Episode 可冻结任务和预算，Trajectory 保存模型请求与
Tool Result，Verification 将测试、Diff、权限、格式和终止信号分开，Benchmark 汇总
成功率、Token、时延和坏例。首轮优先覆盖更多独立任务；只有 A/B 结果分歧和预注册的
稳定性样本进入重复运行，避免把有限预算消耗在无差别的全量多次重跑上。完整设计与
结论边界见
[`harness-comparison-experiment-design.md`](docs/roadmap/harness-comparison-experiment-design.md)。

### 已归档的代表性实验

- Tool UX v2 冻结对照覆盖 10 个本地版本化任务：Qwen3-1.7B 为 0/10，
  DeepSeek-V4-Flash 为 8/10；两个臂均记录完整工具与终止证据。该实验定位了当前小模型
  的模型/后训练瓶颈，不将其解释为 SWE-bench 成绩或纯模型规模消融。
- 多组 SWE-bench Lite Dev 机制实验记录了 Deadline、Post-edit Contract、渐进式
  Action Constraint 和 read-to-edit Repair 的正负结果；无正确性增益或机制未触发的
  实验同样归档，不做质量重试。
- `pvlib__pvlib-python-1854` 的 DeepSeek 候选已进入 SWE-bench v5 容器复评：原始发布
  镜像因 NumPy 2 依赖漂移在 pytest 收集前失败；披露的 `numpy<2` 兼容层下，1 条
  FAIL_TO_PASS 与 281 条 PASS_TO_PASS 全部通过。该记录证明官方评测链路兼容性，
  不等同于未修改环境的排行榜分数。

### 低成本 Agent 后训练路线

仓库提供以下可审计后训练链路：

```text
Claw Episode / Trajectory / Verification
  -> DatasetBuilder（Agent 语义、泄漏与 Tool 对齐准入）
  -> DataFlow（画像、分类、打分、筛选、平衡与导出）
  -> LLaMA-Factory 静态 SFT
  -> Patch-level GRPO/RLVR 方法验证
  -> Claw 独立 Benchmark 与 ExperimentRegistry
```

该链路支持可负担、可复建的 Base、Curated SFT 与 SFT+GRPO 对照：以许可清晰的公开 Coding Agent Trajectory 补充数据规模，以 Claw 自采 Episode 验证端到端生产能力，并可对个人云资源设置显式预算上限。DataFlex 动态选样与静态 SFT、RLVR 基线彼此解耦，不构成数据契约和独立评测的前置依赖。

详细设计与实施边界见：

- [`docs/architecture/data-centric-agent-training-design.md`](docs/architecture/data-centric-agent-training-design.md)
- [`docs/roadmap/data-centric-training-roadmap.md`](docs/roadmap/data-centric-training-roadmap.md)
- [`docs/roadmap/budget-constrained-agent-posttraining-roadmap.md`](docs/roadmap/budget-constrained-agent-posttraining-roadmap.md)
- [`docs/roadmap/swe-bench-lite-experiment-log.md`](docs/roadmap/swe-bench-lite-experiment-log.md)

项目已固定 DataFlow/DataFlex/LlamaFactory 上游版本，完成 `agent_training_record.v1`、Silver/Gold Manifest、确定性质量治理 Pipeline、DataFlow 原生 Operator，以及 LlamaFactory ShareGPT Tool-use Exporter。5 条固定脱敏 Fixture 已在隔离的 DataFlow 1.0.10 CPU 环境完成六步原生 E2E；专用 Train/Dev Episode Collector 与 Archived Episode → Silver 装配入口兼容 runtime adapter v2 轨迹。`deepseek-v4-flash` 已完成 2 个简单 Train Episode → Silver → Gold 小批次及多种真实仓库 Dev Issue Rollout，WSL2 的 LlamaFactory 0.9.4 已完成原生 SFT 预处理；这些证据证明数据契约、失败轨迹治理和训练输入链路可运行，不把契约验证表述为模型效果提升。

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

对照不同协议端点时，可用 `--api-config-root <dir>` 将模型 API 配置发现与任务套件目录
隔离。例如本地 OpenAI-compatible 服务可指向一个不含项目 `.env` 的目录，避免同仓库的
Anthropic 配置改变 Provider 选择；该参数不改变 Episode 工作区或任务内容。

Benchmark 也可选择跨平台 OCI 执行边界。宿主机安装 Docker Desktop、Docker
Engine 或 Podman 后，传入已包含任务依赖的 Linux 镜像：

```bash
claw benchmark-run \
  --manifest task_suites/medium/manifest.json \
  --group base \
  --limit 1 \
  --container-image ghcr.io/example/claw-task@sha256:<digest> \
  --container-engine auto \
  --container-cpus 2 \
  --container-memory 4g \
  --container-pids-limit 256 \
  --output .port_sessions/benchmark-container
```

该路径以相同 Docker/Podman CLI 参数支持 Windows、macOS 与 Linux：Agent 的
`bash` 和任务检查在一次性容器中执行，容器默认断网、删除全部 Linux capability、
启用 `no-new-privileges`、只读根文件系统及资源限制。每次命令先复制当前 Episode
workspace，仅将一次性副本读写挂载到 `/workspace`，命令结束后丢弃副本中的全部变化。
直接文件工具仍由宿主 Agent 进程执行，但 Benchmark
将其路径强制限制在该 workspace。镜像 ID/digest 与有效策略会进入 Benchmark
协议指纹和 Episode metadata。在 Linux/WSL 中默认映射宿主 UID:GID，避免容器
生成 root-owned workspace 文件；可用 `--container-user` 显式覆盖。这里的
“跨平台”指宿主平台兼容；任务镜像目前必须
提供 `/bin/sh` 的 Linux OCI 镜像。安装容器引擎后，可先运行不调用模型的真实
边界探针：

```bash
python tools/probe_container_runtime.py \
  --image alpine@sha256:<digest> \
  --engine auto
```

Windows + WSL2 + Docker Desktop 的真实探针证据见
[`configs/integrations/oci-container-runtime-smoke.json`](configs/integrations/oci-container-runtime-smoke.json)。
它验证 OCI Shell 边界，不是任务 Benchmark，也不覆盖宿主 Agent 进程中的模型调用
和直接文件工具。

同一环境已完成一条 Core Smoke 容器化 Episode，硬测试、Diff 范围与权限检查均通过；
机器可读摘要见
[`configs/integrations/oci-container-benchmark-smoke.json`](configs/integrations/oci-container-benchmark-smoke.json)。
该结果只有一个样本，用于验证链路，不代表模型能力或正式 SWE-bench 成绩。

### 任务集与 SWE-bench Lite

重新生成并验证内置任务集：

```bash
python tools/generate_task_suite.py
python tools/generate_medium_task_suite.py
python tools/validate_task_suite.py --manifest task_suites/manifest.json
python tools/validate_task_suite.py --manifest task_suites/medium/manifest.json
```

SWE-bench Lite Pilot 的数据版本、筛选规则、许可与隔离边界见 [`benchmarks/swe_bench_lite/README.md`](benchmarks/swe_bench_lite/README.md)。安全 Adapter、Git Archive 任务物化、验证期 Test Patch 临时挂载、候选临时副本评测和 Dev Episode Collector 已接通。新生成的 SWE 候选验证从 Episode Git `HEAD` 重建干净工作区，只覆盖 allowlist 内的候选文件后再注入隐藏测试；测试污染因此不会阻止源码候选接受独立评测。启用写 allowlist 时，未配置一次性工作区 Runner 的 Shell 会安全拒绝。Verifier Policy v3 将评测准备完整性与测试通过率分开，未执行测试时通过率保持未知。该路径已在冻结的 pvlib-1154 Control/Treatment 候选上补充验证：两者均成功执行并通过 1 条目标测试和 97 条回归，证明原始零分来自隐藏补丁准备冲突；原始越界测试修改和 Diff Scope 失败仍保持不变。上述均为本地 Dev 证据，不是官方 SWE-bench 分数。

随后将 Pilot 扩展到第 20 个、此前未运行的 `pvlib__pvlib-python-1854`。固定单次 DeepSeek Episode 在第 1 轮定位目标、第 4 轮修改唯一允许文件，并正常结束；干净验证器通过 1 条 FAIL_TO_PASS 与 281 条 PASS_TO_PASS，Diff Scope、权限、格式和终止硬门槛全部通过。Episode 同时暴露 Docker Desktop 未启用 WSL 发行版挂载时，放在 WSL `/tmp` 的一次性 Shell 副本无法挂载；后续已改为在 Episode 工作区同卷创建副本。启用发行版集成后，真实 Docker 在归档任务副本中成功读取候选源码，容器内写入未持久化；没有用第二次模型采样追认该修复。机器证据见 `configs/integrations/swe-bench-lite-pvlib1854-clean-e2e-result.json`。

官方 Docker Harness 使用隔离环境 `.venv-swebench` 和固定入口
`swebench==5.0.2`。`tools/run_official_swebench.py` 会从已结束 Episode 导出候选
Diff、核验冻结 Dev 数据哈希、生成官方三字段 prediction JSONL，并拒绝复用已有
run directory。`tools/probe_official_swebench.py` 会同时检查官方 CLI 与
`docker.from_env()`；只有该探针通过后，才能把后续结果标为官方 Harness 执行证据。
该入口已用于 `pvlib__pvlib-python-1854`：未修改的发布镜像因 NumPy 2 依赖漂移在
pytest 收集前结束；基于同一官方镜像增加一层披露的 `numpy<2` 兼容修复后，候选通过
全部 282 条选定测试。原始结果、兼容层差异、镜像与报告哈希均保存在
`configs/integrations/swe-bench-lite-pvlib1854-official-harness-evidence.json`，两种结果
分开表述，不合并为官方榜单成绩。

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

## 证据边界

- `task_suites/manifest.json` 是确定性 smoke suite；用于验证机制，不用于证明模型在真实仓库上的能力。
- Medium Pilot 当前只有两条任务，适合校准 Rollout 和 Reviewer，不足以形成统计显著结论。
- SWE-bench Lite 已完成单任务预注册对照、两条未见 Issue 的冻结复现、Strict/Progressive 配对消融，以及 Astroid-1268/pydicom-1694 的 Control/Repair 四臂实验。后者 2/4 通过硬门槛，但 Repair 未触发且两组配对结果相同，因此被记录为不确定结果，不据此宣称纠正机制提高正确率。
- PEFT SFT 后端已有可审计实现，但真实 GPU 训练结果必须以运行产物和校验后的 Checkpoint 为准；Dry-run 只验证协议与证据链。
- Reviewer 可以由独立 Agent 执行，但最终指标必须与可复现测试证据分开记录，避免主观评分替代自动验证。
- DataFlow/DataFlex 已完成上游版本基线，稳定数据契约、确定性治理 Operator、Gold 证据和 LlamaFactory ShareGPT Tool-use 导出已落地；固定 Fixture、2 条简单 Train Episode 和真实仓库 Dev Bad Case 均已形成证据。训练数据链路证据与真实 GPU 训练效果保持分层，不用前者替代后者。

## License

见仓库中的许可证文件与各外部数据/仓库快照的独立许可说明。
