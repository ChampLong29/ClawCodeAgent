# Claw Code Agent 训练与评测指南

本文档描述仓库当前已经实现的训练数据生产、可审计实验和独立评测链路。它刻意区分“已实现并有测试的能力”“需要真实模型或 GPU 才能验证的能力”和“尚未接入的外部系统”，避免把接口存在误写成训练效果已经得到证明。

## 1. 先理解两条数据链路

项目保留两条用途不同的链路。

### 1.1 轻量 Rollout 链路

```text
CodingTask JSON
    -> TaskSuite
    -> RolloutRunner
    -> LocalCodingAgent / MockAgent
    -> RolloutResult JSONL
    -> train-stats / trace-show / train-web
```

它适合：

- 快速确认模型能否调用工具并完成任务。
- 生成少量可浏览轨迹。
- 用 `mock` 模式验证 CLI、并发和导出通路。
- 在训练控制台中人工 Review。

这条链路的 JSONL 不是完整的实验注册记录，也不自动提供任务版本、隐藏测试隔离、数据泄漏检查和训练—评测血缘。

### 1.2 可审计 V2 链路

```text
TaskSpec / TaskSuiteManifest
    -> EpisodeOrchestrator
    -> RuntimeAdapter + LocalCodingAgent
    -> append-only Trajectory v2
    -> independent Verification v2
    -> DatasetBuilder
    -> DryRunBackend / PeFTSFTBackend
    -> BenchmarkRunner
    -> ExperimentRegistry
```

它适合：

- 需要复现和审计的模型实验。
- 比较 Base、Raw SFT、Success SFT、Verifier SFT 四组消融。
- 保存 Git Commit、模型版本、Prompt/Tool/Verifier 版本、Seed、Token、成本和产物哈希。
- 将自动测试证据与 Reviewer 评价分离。

后续正式训练应以 V2 链路为主；轻量 Rollout 作为探索和人工检查工具继续保留。

## 2. 环境准备

要求 Python 3.9 或更高版本。

```bash
python -m venv .venv
source .venv/bin/activate       # Linux / macOS
# .venv\Scripts\Activate.ps1   # Windows PowerShell

python -m pip install -U pip
python -m pip install -e ".[dev]"
```

复制 `.env.example` 为 `.env`，选择 DeepSeek Anthropic 兼容或 OpenAI 兼容
协议。不要同时保留无意使用的 `ANTHROPIC_*` 和 `OPENAI_*` 配置，因为任一
`ANTHROPIC_*` 变量都会选择 Anthropic 模式。共享回退模型是 `deepseek-flash`
（当前后端 `v4-flash-9_10`）；Qwen 等本地训练/推理实验仍须显式写入配置。

真实 LoRA/QLoRA 训练还需要相应的 PyTorch、Transformers、PEFT、Accelerate；QLoRA 另外需要 BitsAndBytes。是否可用以 `PeFTSFTBackend.prepare()` 的依赖检查为准，不应仅凭包已安装就宣称训练可用。

训练框架与推理框架是两条独立边界。PEFT/Transformers/BitsAndBytes 用于
LoRA/QLoRA 训练；多轮 Agent Episode 的本地推理使用常驻 vLLM 服务，通过现有
OpenAI-compatible client 接入。不要因训练端需要 Transformers，就让 Benchmark
逐 Episode 使用 `generate()` 加载和解码。Qwen3-1.7B 的固定服务配置与 WSL2
回退见 `configs/inference/qwen3-1.7b-vllm.json` 和
`tools/start_vllm_qwen3.sh`。

## 3. 快速 Rollout

仓库提供最小示例 [`examples/training/sample_suite.json`](examples/training/sample_suite.json)。先用 Mock 模式确认通路：

```bash
claw train \
  --suite examples/training/sample_suite.json \
  --mode mock \
  --workers 1 \
  --seed 42 \
  --output .port_sessions/training/sample-mock.jsonl
```

使用真实模型：

```bash
claw train \
  --suite examples/training/sample_suite.json \
  --mode real \
  --model "$OPENAI_MODEL" \
  --workers 1 \
  --temperature 0.1 \
  --max-turns 20 \
  --timeout 300 \
  --seed 42 \
  --output .port_sessions/training/sample-real.jsonl
```

查看统计和单条轨迹：

```bash
claw train-stats --input .port_sessions/training/sample-real.jsonl
claw trace-show --input .port_sessions/training/sample-real.jsonl --index 0
```

`--workers` 大于 1 会并发执行任务。正式采样前先单并发运行一两条，确认 API 限流、工作目录隔离和成本记录符合预期。

当前 `claw agent`、`agent-chat` 与 `resume` 已可显式选择 Docker Sandbox
Backend；版本化 `benchmark-run` 也已把 Agent 命令与 Episode 初始/最终检查接入
独立 Backend，并要求 Docker Benchmark 镜像固定到 digest。轻量 Rollout、训练
`SandboxManager` 与正式 SWE-bench Harness 尚未整体迁移，当前机器也尚未完成真实
Docker Pilot，因此只能标记为 **Implemented / Contract verified**，不能描述为
Benchmark verified 或官方 SWE-bench 隔离结果。
迁移到具备 Docker 的主机后，应按
[`docs/architecture/DOCKER_SANDBOX_VALIDATION_RUNBOOK.md`](docs/architecture/DOCKER_SANDBOX_VALIDATION_RUNBOOK.md)
依次执行 Live Test、单任务 Pilot 和证据检查，再更新验证状态。

## 4. 训练控制台

安装 Web 依赖并启动：

```bash
python -m pip install -e ".[web]"
claw train-web \
  --results-dir .port_sessions/training \
  --host 127.0.0.1 \
  --port 8080
```

训练控制台是 `claw train-web` 子命令。项目没有安装名为 `claw-train-web` 的独立可执行文件。

控制台适合浏览 JSONL、查看轨迹、执行少量 Rollout 和导出数据。人工 Reviewer 评分应作为单独信号保存；它不能替代隐藏测试、Diff 安全检查或独立 Benchmark。

## 5. 版本化任务集

### 5.1 Core Smoke Suite

[`task_suites/manifest.json`](task_suites/manifest.json) 包含 32 条确定性任务，覆盖：

- `python-cli` 和 `python-library` 两个 Domain。
- `add_feature` 和 `fix_bug` 两种任务类型。
- `train`、`dev`、`test` 三个 Family 隔离 Split。

这些任务用于验证 TaskSpec、模板哈希、验证命令、Episode 和 Benchmark 链路。任务同构且规模小，不能用它们的成功率证明复杂仓库能力。

### 5.2 Medium Pilot

[`task_suites/medium/manifest.json`](task_suites/medium/manifest.json) 当前包含两条跨模块任务：

- 原子库存预留和不可变订单模型。
- 分层配置递归合并、类型校验和输入隔离。

任务测试存放在版本化 `test_assets_ref` 中。Episode 在初始验证和最终验证时临时复制为 `.claw_hidden_tests`，验证内容哈希后运行，并在 Agent 工作阶段移除。这可以降低直接读取测试答案的风险，但不等同于强对抗沙箱。

### 5.3 重新生成和验证

```bash
python tools/generate_task_suite.py
python tools/generate_medium_task_suite.py

python tools/validate_task_suite.py \
  --manifest task_suites/manifest.json
python tools/validate_task_suite.py \
  --manifest task_suites/medium/manifest.json
```

生成器应具有确定性。运行后如果 `git diff` 出现无预期变化，应先调查哈希、排序或环境差异。

## 6. 可审计 Benchmark

### 6.1 先跑一条 Smoke

```bash
claw benchmark-run \
  --manifest task_suites/manifest.json \
  --group base \
  --limit 1 \
  --model "$OPENAI_MODEL" \
  --temperature 0.1 \
  --max-turns 20 \
  --seed 42 \
  --output .port_sessions/benchmark-smoke
```

### 6.2 再跑 Medium Pilot

```bash
claw benchmark-run \
  --manifest task_suites/medium/manifest.json \
  --group base \
  --limit 1 \
  --model "$OPENAI_MODEL" \
  --temperature 0.1 \
  --max-turns 50 \
  --seed 42 \
  --output .port_sessions/benchmark-medium
```

也可以通过 `--task-id <id>` 固定任务。不要一开始并发或完整跑完所有外部任务；先用一到两条校准 Prompt、Turn、超时、失败日志和成本。

### 6.3 Diff 安全边界

默认允许修改的路径从每个任务的版本化 Oracle 文件推导。确有需要时可显式覆盖：

```bash
claw benchmark-run \
  --manifest task_suites/medium/manifest.json \
  --group base \
  --task-id python-service-atomic-order-01 \
  --allow-path "order_app/**" \
  --output .port_sessions/benchmark-medium-order
```

显式 `--allow-path` 会替代默认推导结果，应保持最小范围。临时文件、缓存或隐藏测试泄漏都应作为 Diff 违规处理。

### 6.4 跨平台 OCI 执行边界

安装 Docker 或 Podman 后，可用固定镜像运行 Agent Shell 与任务检查：

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

`auto` 依次发现 Docker 与 Podman。Runner 在开始前检查 daemon 和本地镜像，
不会隐式拉取；建议始终使用 digest 引用。容器默认 `network=none`、只读根文件
系统、临时 `/tmp`、`cap-drop=ALL`、`no-new-privileges` 和 PID/CPU/内存限制，
只挂载当前 Episode workspace。超时后会按随机生成的精确容器名强制清理。
Linux/WSL 调用时会默认使用宿主 UID:GID，避免容器写入的文件阻断后续 Git
checkpoint/reset；`--container-user` 可覆盖这个默认值。

这不是完整的宿主系统调用审计：直接文件工具仍运行在宿主 Agent 进程内，并由
`restrict_workspace` 做路径限制；模型 API 调用也在容器之外。对外应描述为
“Agent Shell 与任务检查的 OCI 隔离边界”，除非后续把整个 Agent 进程及所有外部
工具也迁入容器并完成逃逸测试。

固定 Alpine digest 的 Windows + WSL2 + Docker Desktop 实机探针已完成，机器可读
证据位于 `configs/integrations/oci-container-runtime-smoke.json`。该证据只将 OCI
Shell 边界标记为真实引擎验证，不将其升级为 Benchmark verified。

固定 Python digest 的一条 Core Smoke 容器化 Episode 也已通过硬测试、Diff 范围与
权限检查，摘要位于 `configs/integrations/oci-container-benchmark-smoke.json`。这是
单样本链路验证，不是能力评测；该 Episode 首次绝对路径命令失败后由 Agent 自行恢复，
其后新增的宿主路径到 `/workspace` 映射只经过聚焦测试与无模型实机探针验证，未用同一
模型 Episode 做结果重试。

新代码优先使用统一 `SandboxBackend` 参数；它会为 Agent 与 Verifier 创建不同的
Sandbox Owner/Handle，并在最终验证前销毁 Agent Sandbox：

```bash
claw benchmark-run \
  --manifest task_suites/manifest.json \
  --group base \
  --limit 1 \
  --sandbox-backend docker \
  --sandbox-image 'python@sha256:<64-hex-digest>' \
  --output .port_sessions/benchmark-docker
```

Docker Backend 必须使用本地已有且 digest 固定的镜像；缺失、启动失败或清理失败均
Fail Closed，不回退 Host。实机验证步骤见
`docs/architecture/DOCKER_SANDBOX_VALIDATION_RUNBOOK.md`。

### 6.5 固定实验变量

Benchmark 至少固定并记录：

- `model_ref` 与模型/Checkpoint Revision。
- `temperature`、`max_tokens`、`max_turns`、`seed`。
- `runtime_version`、`prompt_version`、`tool_version`、`verifier_version`。
- 测试 Manifest、Dataset Manifest、Training Run 和 Experiment 引用。
- 输入/输出 Token 单价（需要成本比较时）。

可用命令行参数显式传入这些值：

```bash
claw benchmark-run --help
```

### 6.6 结果解释

每个结果应至少检查：

- 自动测试是否通过。
- 初始测试是否正确失败，避免任务模板本身已经满足答案。
- 是否只修改允许路径。
- Stop Reason、模型调用数、工具调用数和 Turn 数是否一致。
- 输入/输出 Token、时延和估算成本。
- Reviewer 分数是否有明确 Rubric 和证据引用。

一次成功 Episode 只是个案。模型质量结论至少需要多任务、相同预算和独立 Test Split；
多次运行用于估计轨迹方差，不作为每个小规模实验的机械要求。

### 6.7 资源受限的 Harness 对照

Harness 评测采用三层分离：固定 Commit 的 DeepSeek Harness `sdk-minimal` 是外部参考，
Claw Minimal 检查 Prompt、协议和 Tool UX 是否等价，Claw Controlled 只加入预注册的
运行时控制。不得直接用完整 Claw 对比外部 Minimal 后把全部差异归因于某一项策略。

主指标为 Policy-compliant Resolved 与固定 Token/Turn/Tool/时间预算下的 Resolved；
原始 Resolved、Token、工具调用、时延和成本必须同时报告，即使结果不利于 Claw。
首轮优先增加冻结任务覆盖，每任务/臂运行一个新 Episode；配对结果分歧项和预注册的
相同结果样本再重复 2-3 次，并单独保留首次运行表。完整的取舍、准入、停止规则和
表述边界见 `docs/roadmap/harness-comparison-experiment-design.md`。

### 6.8 Pi-inspired RPC 基线

Pi 基线通过长驻 `pi --mode rpc --no-session` JSONL 进程接入，复用 Claw 的 Episode、
Trajectory v2、Diff Allowlist 和 Verification v2。运行时版本、工具版本、模型、每次
响应 Token、累计 Token、Turn 和超时都必须显式冻结：

```bash
claw benchmark-pi \
  --manifest task_suites/medium/manifest.json \
  --model deepseek-flash \
  --runtime-version 'pi@0.85.1' \
  --tool-version 'pi-builtins@0.85.1' \
  --sandbox-attestation '<实际隔离边界>' \
  --max-tokens 4096 \
  --max-total-tokens 250000 \
  --max-turns 24 \
  --limit 1 \
  --output .port_sessions/benchmark-pi
```

`--use-claw-api-config` 只把密钥的环境变量引用写入 Pi 配置，真实密钥仅进入子进程
环境。macOS 可增加 `--enforce-macos-seatbelt`。Docker 主机目前需要通过一个容器包装
入口让完整 Pi 进程留在容器内，并如实记录 attestation；`PiRpcClient` 尚未由 Claw
的 `SandboxBackend` 创建或独立验证该容器，所以不能把 attestation 本身当成隔离证明。

本地 SWE-bench Lite Dev 的同任务双臂和三臂消融入口分别为：

```bash
python tools/compare_swe_bench_lite_runtimes.py --help
python tools/run_swe_bench_lite_runtime_ablation.py --help
```

在 Docker 主机运行三臂契约时，Claw 两臂与三个臂的 Verifier 可以固定到同一任务镜像：

```bash
python tools/run_swe_bench_lite_runtime_ablation.py \
  --instance-id '<INSTANCE_ID>' \
  --python '<HOST_CALIBRATED_PYTHON>' \
  --output-root '<NEW_OUTPUT_ROOT>' \
  --allow-path '<IMPLEMENTATION_PATH>' \
  --model deepseek-flash \
  --claw-sandbox-backend docker \
  --claw-sandbox-image '<TASK_IMAGE@sha256:DIGEST>' \
  --claw-sandbox-python /usr/local/bin/python \
  --pi-executable '<OPERATOR_MANAGED_PI_DOCKER_WRAPPER>' \
  --no-macos-seatbelt \
  --pi-sandbox-attestation '<EXACT_PI_CONTAINER_BOUNDARY>'
```

镜像内 Python 必须可导入 Claw，并包含目标历史仓库的冻结测试依赖；宿主
`--python` 仍用于模型调用前的工作区导入准入。Claw Agent 与 Verifier 由
`SandboxBackend` 管理，Pi Agent 仍由外部包装器管理；不要把 Pi 的 attestation
描述为程序独立验证。若缺少 digest、镜像内 Python 或非 macOS Pi 隔离证明，入口会在
模型调用前失败。

已归档一个 Marshmallow-1343 的真实 Claw–Pi 对照，Claw 通过测试而 Pi 未编辑；它只
是单样本本地机制证据。七任务三臂计划仅覆盖 20 题筛选中已经校准的有序子集，必须先
完成 Docker/RPC 冒烟再启动付费 Episode。协议、证据边界和后续步骤见
`docs/architecture/PI_INSPIRED_HARNESS.md`。

## 7. Episode、Trajectory 与 Verification

### Episode

`EpisodeOrchestrator` 负责：

- 从模板创建隔离工作区。
- 运行初始验证，确保任务并非预先通过。
- 创建 Checkpoint 和恢复信息。
- 调用 `RuntimeAdapter` 执行 Agent。
- 在最终验证阶段挂载隐藏测试。
- 收集 Workspace Diff、安全检查和状态转换。

### Trajectory

Trajectory v2 是追加式记录，包含：

- 用户与模型消息。
- 工具调用和结果。
- Token、时延、错误和终止原因。
- Runtime、Prompt、Tool、模型和任务引用。

消费轨迹时应以记录中的唯一调用标识和计数为准，避免把响应中的 `tool_calls` 与实际执行事件重复计数。

### Verification

Verification v2 与生成轨迹分离，保存自动测试、Diff、质量或 Reviewer 信号。Reviewer 可以由同一模型系列执行，但应使用独立 Session 和只读证据，且不能看到 Oracle、Gold Patch、隐藏测试源码或官方测试补丁。

推荐 Reviewer Rubric：

1. 正确性：行为是否满足任务描述。
2. 安全性：是否有越权修改、数据破坏或绕过测试。
3. 可维护性：修改是否局部、清晰、遵循现有接口。
4. 效率：是否存在明显无效 Turn、重复读取和临时文件污染。

自动测试和 Reviewer 得分必须分别报告。

## 8. DatasetBuilder

`DatasetBuilder` 接收 `TaskSpec + Trajectory + 可选 Verification`，并输出：

- Tool-use SFT JSONL。
- 分析样本。
- 排除记录及原因。
- 数据泄漏报告。
- 带内容哈希和血缘信息的 Dataset Manifest。

当前策略包括原始轨迹、成功样本和 Verifier 筛选等路径。构建前会验证：

- 任务、轨迹与 Verification 引用一致。
- Split 与任务 Family 不泄漏。
- 重复样本已去重。
- Tool Call 与 Tool Result 对齐。
- 质量阈值、Reviewer 阈值和消息上限有效。

关键 API：

```python
from claw.dataset import DatasetBuilder, DatasetRecord

result = DatasetBuilder(generation_commit="<immutable-git-commit>").build(
    records,
    strategy="verifier_filtered",
    split="train",
    output_dir="artifacts/datasets/verifier-filtered",
    quality_threshold=0.80,
    reviewer_threshold=0.70,
    dataset_version="1.0.0",
)
```

不要把 Test Split 轨迹混入训练集；即使文本不同，同一 `family_id` 也应保持 Split 隔离。

## 9. SFT 训练后端

### 9.1 DryRunBackend

`DryRunBackend` 验证数据集 Manifest、样本哈希、Experiment 配置和产物协议，并生成契约证据。它不会进行梯度更新，结果会明确记录：

```text
contract_verified = true
training_verified = false
```

Dry-run 适合在下载大模型或占用 GPU 前发现数据和血缘错误，不能被描述为“模型已训练”。

### 9.2 PeFTSFTBackend

`PeFTSFTBackend` 提供单机 LoRA/QLoRA：

- Transformers Tokenizer 与 Model 加载。
- PEFT Adapter 配置。
- 仅对 Assistant 输出计算 Loss 的 Tool-use Chat 编码。
- 固定 Seed、最大序列长度、Batch、梯度累积和保存间隔。
- Adapter、Tokenizer、训练指标和峰值显存证据。
- Checkpoint 恢复。

真实训练前至少固定：

```python
from claw.training_backends import SFTTrainingConfig

config = SFTTrainingConfig(
    output_dir="artifacts/training/exp-001",
    model_revision="<immutable-model-revision>",
    mode="lora",               # 或 qlora
    seed=42,
    max_seq_length=4096,
    num_train_epochs=1.0,
    learning_rate=2e-4,
    per_device_train_batch_size=1,
    gradient_accumulation_steps=8,
)
```

项目当前没有把完整 V2 SFT 实验封装为一个“一键训练” CLI；应通过 Python API 或后续实验驱动器调用，并用 `ExperimentRegistry` 保存状态和产物。

## 10. ExperimentRegistry

`ExperimentRegistry` 为每个实验保存唯一、可重建的权威记录：

```text
created
  -> training / contract verification
  -> trained 或 contract_verified
  -> benchmarking
  -> completed
```

注册实验必须包含：

- 不可变 Git Commit。
- 环境快照。
- Base Model、Dataset Manifest、训练后端和完整训练配置。
- Seed 和推理配置。

训练记录、Benchmark、消融报告和内容寻址产物通过显式引用附加。Registry 会拒绝模型、数据集、Seed 或实验引用不一致的证据，也会阻止覆盖已经完成的训练/评测证据。

## 11. 四组消融设计

Benchmark 支持以下组名：

| 组 | 含义 | 目的 |
|---|---|---|
| `base` | 原始模型 | 基线 |
| `raw_sft` | 未筛选轨迹训练 | 判断“只增加数据”是否有效 |
| `success_sft` | 自动验证成功样本训练 | 判断成功过滤的收益 |
| `verifier_sft` | 自动验证 + Reviewer 过滤 | 判断 Reviewer 信号的增量 |

四组必须使用同一 Test Manifest、预算、解码设置和评测代码。非 Base 组还必须携带匹配的 Dataset Manifest、Training Run 和 Experiment 引用。

不要在 Test 结果出来后继续调筛选阈值；阈值应在 Train/Dev 上确定。

## 12. SWE-bench Lite Pilot

本仓库已完成：

- 固定官方数据 Revision。
- 保存原始 Parquet、标准化行、元数据和 SHA-256。
- 对开发 Split 建立 Catalog。
- 按规模、依赖、许可证、任务类型和失败模式选择少量 Pilot。
- 保存去除 Gold Patch、Test Patch、Hints 和测试标识的 Agent 输入。
- 在精确 Base Commit 上准备浅层仓库工作副本并记录快照。
- 实现 `SweBenchLiteDevAdapter`，分别加载 Agent-safe 输入与评测专用数据；评测侧
  对外只导出 Patch/Test IDs 的数量和哈希指纹。
- 实现非官方的 `LocalSweBenchLiteCalibrationRunner`，通过 Git Archive 构造无 Git
  元数据快照并强制补丁落在隔离工作区。
- 在 Python 3.8.20 环境完成 `marshmallow-code__marshmallow-1343` 校准：基线
  FAIL_TO_PASS 失败、24 条 PASS_TO_PASS 通过，参考补丁后两组均通过。版本化
  证据位于 `configs/integrations/swe-bench-lite-marshmallow-local-calibration.json`。
- 在独立 Python 3.8.20 环境完成 `pylint-dev__astroid-1196` 校准：基线 2 条
  FAIL_TO_PASS 失败、24 条 PASS_TO_PASS 通过，参考补丁后两组均通过。版本化
  证据位于 `configs/integrations/swe-bench-lite-astroid-local-calibration.json`。
- 在同一固定 Python 3.8.20 环境完成新 Issue
  `marshmallow-code__marshmallow-1359` 校准：基线 1 条目标测试失败、76 条回归通过，
  参考补丁后两组通过。机器证据位于
  `configs/integrations/swe-bench-lite-marshmallow-1359-local-calibration.json`。
- 完成 `sqlfluff__sqlfluff-1763` 的第三类本地校准：固定 Python 3.8 环境、非
  editable 插件元数据和保守 Node ID 规范化后，3 条 FAIL_TO_PASS 在基线失败、
  参考补丁后通过，PASS_TO_PASS 保持通过。该证据只表示环境具备 Rollout
  准入条件，位于
  `configs/integrations/swe-bench-lite-sqlfluff-local-calibration.json`。
- 接通验证期 Test Patch 临时挂载、候选临时副本评测和真实 Dev Episode Collector。
- 完成两次 `deepseek-v4-flash` 对照：原始环境版本无修改且测试失败；环境感知
  Prompt 版本通过 1 条 FAIL_TO_PASS 与 24 条 PASS_TO_PASS，但因 Max turns 未正常
  终止，仍是失败 Episode。对照证据位于
  `configs/integrations/swe-bench-lite-marshmallow-deepseek-rollouts.json`。
- 完成修复后的 Marshmallow 三次受控尝试：首次暴露环境预检未检查 pytest，第二次
  候选通过全部选定测试但 API 连接中断，补充依赖预检和传输重试后，最终候选仍
  通过全部选定测试但耗尽 turns。三次均未通过终止硬门槛，只保留作 Dev Bad Case；
  证据位于
  `configs/integrations/swe-bench-lite-marshmallow-remediation-rollouts.json`。
- 在 Astroid 上执行一次受控 Rollout 与一次基础设施修复后的重试：首次暴露
  `runtime_guidance` 未注册到 Trajectory Schema 的问题；修复后重试仍未通过 2 条
  FAIL_TO_PASS，并在收到收尾提醒后耗尽 30 turns。对照证据位于
  `configs/integrations/swe-bench-lite-astroid-deepseek-rollouts.json`。
- SQLFluff 泛化 Rollout 的首次准备因自引用 Fixture 链接失败，未调用模型；安全
  保留链接并拒绝越界链接后，第二次尝试使用完 30 turns 但未形成源码修改。修复
  候选评测复制后离线复评确认 PASS_TO_PASS 通过、3 条 FAIL_TO_PASS 仍失败。
  该结果只作为路径定位与实现时机 Bad Case，见
  `configs/integrations/swe-bench-lite-sqlfluff-deepseek-rollout.json`。
- 新增 `rollout_behavior_diagnostics.v3`：旧 Trajectory 可确定性重算目标路径首次
  出现、首次直接编辑、编辑前探索比例和收尾提醒后的工具调用。Marshmallow 在第 4
  轮定位、第 13 轮编辑；SQLFluff 第 5 轮已定位但 30 轮内未编辑。证据位于
  `configs/integrations/swe-bench-lite-rollout-behavior-diagnostics.json`。

离线分析已有轨迹：

```powershell
$env:PYTHONPATH = "src"
python tools/analyze_rollout_behavior.py `
  .port_sessions/<run>/episodes/<episode>/trajectory.json `
  --target-path src/package/implementation.py
```

采集新 Episode 时可显式固定策略阈值：

```powershell
python tools/collect_swe_bench_lite_episode.py <其余参数> `
  --container-image <包含任务依赖的固定镜像> `
  --container-engine auto `
  --thinking-mode disabled `
  --max-tokens 4096 `
  --implementation-deadline-turns 12 `
  --implementation-escalation-turns 4 `
  --force-direct-mutation-after-escalation `
  --implementation-target-read-allowance 1 `
  --reject-repeated-readonly-actions `
  --repeated-action-repair-attempts 1 `
  --completion-reminder-turns 8 `
  --completion-critical-turns 3 `
  --force-final-response-at-critical
```

新生成的 Benchmark/Training Episode 会把同一投影写入 Episode Result 的
`behavior_diagnostics`，聚合报告包含直接编辑率、目标路径定位率、平均首次定位/
编辑轮次、编辑前探索比例和 Critical 后调用数。SWE-bench Collector 默认在连续
12 个工具轮次仍无成功显式文件编辑时注入 Implementation Deadline；若再经过 4 个
工具轮次仍未成功编辑，则注入一次 Implementation Escalation。成功编辑会抑制升级
提示，进入 Completion Reminder 区间后也不会叠加注入。首次成功修改允许提交的实现路径后还会注入
一次 Post-edit Contract Notice，要求用目标测试与相关回归检查值、异常、scalar/collection、
容器与返回类型、shape、ordering、null 和 metadata 等兼容契约；可用
`--no-post-edit-contract-guidance` 关闭。可选的动作约束默认会在 Escalation 请求只暴露
`write_file`/`edit_file` 并设置 required tool choice；显式设置
`--implementation-target-read-allowance 1` 后，升级后的首次请求额外允许一次命中实现路径
allowlist 的 `read_file`，随后下一次工具请求只允许直接编辑。越界读取、连续第二次读取或编辑
非目标路径都会在分发前显式停止。在 Critical 请求可隐藏工具并要求最终响应。DeepSeek Anthropic 兼容接口忽略
`budget_tokens`，因此本项目用 `thinking=disabled` 加单次 `max_tokens` 实现可验证的有界
请求，而不宣称不存在的精确思考 Token 预算。

SWE Collector 还会把同一实现路径 allowlist 写入 Agent 权限上下文。此后所有显式
`write_file`/`edit_file` 请求都会在文件系统写入前解析真实工作区相对路径并匹配 allowlist；
不匹配的请求返回权限错误且不创建或修改文件。启用 allowlist 后，`bash` 只有在 Runner
声明使用一次性工作区时才会分发；否则以 `unsafe_shell_workspace` 安全拒绝。OCI Runner
为每条命令复制当前 Episode，只挂载副本并在返回结果后丢弃其文件变化。采集命令可通过
`--container-image` 和 `--container-engine` 启用该路径。

SWE Verifier 不再直接复制 Agent 使用过的候选树。它从 Episode Git `HEAD` 解包干净基线，
仅覆盖 allowlist 匹配的候选源码文件，再注入 Test Patch 并执行目标与回归测试。因此越界
测试修改仍会触发 Diff Scope，但不会再造成隐藏补丁冲突并遮蔽源码候选的功能结果。
Verifier Policy v3 新增必需的 `evaluation_integrity` 硬门槛：评测准备失败时记录
`evaluation_prepared=false`、`tests_executed=false`，`test_pass_rate` 保持 `null`，聚合时
从测试通过率分母中排除，同时单独计入 `evaluation_error_count`。

该干净重建路径已直接用于冻结的 pvlib-1154 两个候选：Control 与 Treatment 均完成
隐藏测试注入，并通过 1 条 FAIL_TO_PASS 和 97 条 PASS_TO_PASS。补充结果只纠正“源码
候选是否通过测试”的未知状态，不覆盖原始 Episode 的测试文件越界修改、Diff Scope 失败
或不可变 Verification。机器证据见
`configs/integrations/swe-bench-lite-clean-verification-pvlib1154-supplemental-result.json`。

在此基础上，Pilot 扩展到此前未运行的 `pvlib__pvlib-python-1854`。该任务在模型调用前
完成本地准入：基线 1 条目标测试失败、281 条回归通过，参考补丁后两组均通过。冻结的
单次 `deepseek-v4-flash` Episode 第 1 轮定位 `pvlib/pvsystem.py`、第 4 轮完成唯一源码
修改，并在 12 轮内正常结束；干净验证器通过全部 282 条选定测试，Diff Scope、权限、
格式和终止门槛也全部通过。两次 Agent Shell 调用因 Docker Desktop 未启用 WSL 发行版
挂载而失败，未影响显式白名单编辑与独立评测；随后一次性副本改为创建在 Episode 工作区
同一宿主卷。启用发行版集成后，真实 Docker 已在归档任务副本中读取候选源码并证明容器
写入不会持久化；遵守零质量重试约束，未再次调用模型。协议与结果见
`configs/integrations/swe-bench-lite-pvlib1854-clean-e2e-*.json`。这是一条本地 Dev 成功
Episode，不是官方 SWE-bench 分数，也不能单样本估计 Harness 或模型成功率。

`--reject-repeated-readonly-actions` 是独立且默认关闭的运行时策略。它只缓存成功的
`list_dir`、`read_file`、`code_outline`、`glob_search` 和 `grep_search` 观察，并对路径参数
做规范化；任一已分发的 `write_file`、`edit_file` 或 `bash` 会清空缓存。完全相同的观察
再次出现时不会分发；配置 `--repeated-action-repair-attempts 1` 可给模型一次不包含新任务
信息的纠正机会，再次重复会以 `repeated_readonly_action` 明确停止。被拒请求、纠正提示和
停止原因都写入不可变 Trajectory。此机制只消除确定性的无信息循环，不证明模型会编辑、
测试通过或成功率提高。

新生成的 Episode 默认记录 `tool-schema.v3`；该版本继承 v2 的搜索 UX，并加入写白名单下
Shell 必须使用一次性工作区的分发契约。其中 `code_outline` 可用 `query` 按任务中
出现的类名或函数名过滤，即使符号位于大文件末尾也不会被通用截断丢失；`grep_search`
默认每页最多返回 10 条，支持 `file_pattern`、`context_lines`、`offset` 和
`next_offset`，并使用稳定的工作区相对路径。分页元数据位于匹配内容之前，即使模型可见
结果被截断也能判断是否需要缩小搜索或读取下一页。SWE Collector 不再暴露 Web、Skill 等
与隔离代码任务无关的工具。

DeepSeek Anthropic 端点可用 `tools/probe_anthropic_tool_server.py` 做合成协议预检。探针只
发送虚构路径和代码片段，验证 required tool call、Tool Result 回传、第二轮结构化调用、
响应 ID 与服务端模型名，不构成任务能力评测。已验证证据见
`configs/integrations/deepseek-v4-flash-anthropic-tool-probe.json`。

冻结的本地跨模型抽检结果见 `configs/integrations/tool-ux-v2-cross-model-smoke-result.json`：
Qwen3-1.7B 为 0/10，DeepSeek-V4-Flash 为 8/10（Core 8/8、Medium 0/2）。这只是本地
版本化任务对照，不是 SWE-bench 成绩；Medium 失败保留隐藏测试与 token-limit 终止证据。

同一任务集对照多个 Provider 时，为每个实验臂固定独立的 API 配置目录，并通过
`claw benchmark-run --api-config-root <dir>` 选择它。该目录只参与 API 配置发现，不改变
任务 Manifest、Episode 工作区或验证器；尤其可防止项目根目录的 Anthropic `.env` 污染
本地 OpenAI-compatible 模型臂。

Qwen3-1.7B 的冻结机制复验在 pydicom-1694 上实际触发两次 Guard：第一次拒绝后模型改用
`list_dir` 和不同搜索范围，第二次重复新搜索时明确停止。相较此前失败载体，实际工具分发
从 8 次降至 4 次、总 Token 从 39,332 降至 20,854；仍无源码编辑且目标测试失败。因此该
结果只把“Runtime 可阻断重复循环”提升为已验证，不把“基本成功率”或“任务质量”提升为
已验证。协议与机器结果位于 `configs/integrations/qwen3-local-swebench-loop-guard-validation-*`。

2026-08-20 在 Marshmallow-1343 上完成首次本地真实仓库成功 Episode。配置关闭显式思考、
单次上限 4096 Token、18 turns、Deadline/Escalation=5/2，并启用直接编辑和最终响应约束。
模型第 2 轮定位目标、第 5 轮收到 Deadline、第 7 轮编辑允许路径、第 15 个工具轮次后主动
正常结束；1 条 FAIL_TO_PASS、24 条 PASS_TO_PASS、Diff Scope、流程、格式和终止门槛全部
通过。Escalation 与 Critical 约束未实际触发，因此该结果只能证明有界动作策略配置下首次
产生合规成功，不能估计两个强制约束的独立因果效果，也不是官方 SWE-bench 分数。机器证据见
`configs/integrations/swe-bench-lite-marshmallow-bounded-action-success.json`。

随后在新加入的 Marshmallow-1359 上进行跨 Issue 复现。固定关闭显式思考、4096 Token、
18 turns 和同一允许路径边界。v1 第 2 轮定位、第 5 轮收到 Deadline、第 7 轮编辑；v2 将
Deadline 提前至第 4 轮，并在第 6 轮实际触发只允许直接编辑的 Escalation 请求，第 7 轮
完成编辑。两次均通过 76 条 PASS_TO_PASS、Diff Scope、流程、格式和正常终止，但 1 条
FAIL_TO_PASS 均失败。模型用 `None` 回退避免立即异常，却没有沿已有 `Field.root` 父链读取
根 `Schema.opts`；v2 虽增加了显式字段格式探针，仍未覆盖 `Schema.Meta.datetimeformat` 的
非默认配置继承。这证明动作约束可以被实际执行，但没有复现成功或产生质量增点证据。两条
失败轨迹保留为 `semantic_contract_miss` Bad Case，不再对同题继续采样。机器证据见
`configs/integrations/swe-bench-lite-marshmallow-1359-bounded-action-rollouts.json`。

为验证该假设而不继续拟合 1359，新增并在仓库获取、校准和模型调用前哈希固定
`bounded-action-confirmatory.v1` 协议。选择此前未运行且新增仓库 Family 的
`pyvista__pyvista-4315`，固定 Control 关闭 Post-edit Notice、Treatment 开启 Notice，
其余 DeepSeek 模型、Thinking、Token、Turn、Deadline/Escalation、Critical 和评测参数完全
一致，每臂最多一个有效 Episode。Python 3.8.20/VTK 9.2.6 环境首次因缺少 `ipykernel`
无法收集，补齐后又有 3/114 回归缺少 `tqdm`/`meshio`；所有失败均发生在模型调用前并原样
保留。冻结依赖后校准满足基线目标失败/114 回归通过、Oracle 两组通过。

双臂均在第 6 轮首次编辑，最终同时通过 1 条 FAIL_TO_PASS、114 条 PASS_TO_PASS、Diff、
流程、格式和终止硬门槛。Treatment 的 Notice 在第 6 轮实际注入；相较 Control 多 181
Token、约 2.94 秒，少一次编辑和一次失败工具调用，并生成更正常的最终说明，但两者候选语义
等价。按照预注册解释矩阵，该结果支持“冻结流程可在全新仓库 Family 复现合规成功”，不支持
“Notice 提升正确性”。协议见
`docs/roadmap/bounded-action-confirmatory-experiment-protocol.md`，机器证据见
`configs/integrations/swe-bench-lite-pyvista-4315-confirmatory-comparison.json`。

Verifier Policy v2 将最终答复盲区拆成独立、零权重的 `final_response_quality` Soft 信号；
Policy v3 在保留它的同时加入独立的评测完整性硬门槛。
它只从 Trajectory v2 的不可变终止详情确定性识别空回答、纯思考块和裸工具调用标记，不判断
实现正确性，也不覆盖测试、Diff、权限、格式或终止硬门槛。对上述两条历史轨迹回放后，
Control 为 `raw_tool_call_markup/fail`，Treatment 为 `user_facing_text/pass`；原始 Episode
与 Verification 文件不被改写。可用 `tools/analyze_final_response_quality.py` 重放该诊断。

pydicom 新鲜 Dev Episode 已实际触发首级策略：第 3 轮定位目标、第 8 轮收到 Deadline、
第 19 轮才编辑，第 22 轮正常终止。补充评测中 38 条 PASS_TO_PASS 通过但
FAIL_TO_PASS 仍失败。由于没有同任务控制组，且编辑仍明显延迟，该结果只能证明
策略被执行，不能证明策略改善模型。原始 Verifier 同时暴露了 Evaluator 对相对
`PYTHONPATH` 的依赖；工具现已基于自身路径加载 Claw，原始失败证据仍保留。

随后在第五个 Family `pvlib__pvlib-python-1707` 上验证二级策略。固定 Python 3.8、
NumPy/Pandas/SciPy 与 `pytest-mock` 后，基线目标测试失败且 30 条回归通过，Oracle 后
两组均通过。Episode 第 1 轮定位目标，第 8/12 轮收到 Deadline/Escalation，第 13 轮
编辑并在第 19 轮正常终止；但 `numpy.where` 把 `pandas.Series` 返回值转换为 ndarray，
导致目标和回归硬门槛均失败。因此只能记录“Escalation 后下一轮编辑”的时序信号，
不能声称策略有效。机器证据位于
`configs/integrations/swe-bench-lite-pvlib-escalation-rollout.json`。

第六条 `pydicom__pydicom-1413` 用作同仓跨任务契约泛化。Oracle 校准满足“基线 3 条
FAIL_TO_PASS 失败、301 条 PASS_TO_PASS 通过，参考补丁后两组通过”。首次 Rollout 在
第 13 次模型请求达到默认 4096 输出 Token 上限，只有思考块而没有正文或工具调用；客户端
丢失上游 `max_tokens` 原因并误记为 completed。客户端现保留 Finish Reason，运行时将这种
响应标记为 stopped。基础设施修复后的 8192 上限复验定位了 `pydicom/dataelem.py`，但只
反复编辑 `repro_ol.py`，最终耗尽 24 turns，目标测试失败、301 条回归通过且 Diff Scope
失败。该过程还证明原 Post-edit Notice 会被临时脚本误触发；Collector 现把 Oracle 路径
传给 Runtime，临时脚本写入既不算实现进展，也不会触发契约提示。两次失败均保留，且不再
第三次运行同题。机器证据位于
`configs/integrations/swe-bench-lite-pydicom-1413-post-edit-rollouts.json`。

第七条 `pylint-dev__astroid-1333` 覆盖命名空间包路径解析。独立 Python 3.8.20
环境校准达到基线 1 条目标失败/46 条回归通过、Oracle 后两组通过。受控 Rollout 第 6 轮
定位 `astroid/modutils.py`，第 8/12 轮收到 Deadline/Escalation，却未发生源码编辑；最终
模型用满 8192 输出 Token 生成思考块。路径限定正确地没有被 `/tmp` 复现命令误触发。
Runtime 虽识别 `max_tokens`，但首次真实记录时发现 `runtime_stop` 尚未列入 Trajectory v2
事件集合，原 Episode 因此被基础设施错误污染。该事件现已注册，且 RuntimeAdapter 集成测试
验证其终止为 cancelled 并正常进入 VERIFYING；原 Episode 保持不变，不再付费重跑同题。

详见 [`benchmarks/swe_bench_lite/README.md`](benchmarks/swe_bench_lite/README.md)；
逐次失败、判断、修复和验证结果见
[`docs/roadmap/swe-bench-lite-experiment-log.md`](docs/roadmap/swe-bench-lite-experiment-log.md)。

当前未完成：

- 官方 Docker Harness 适配器、固定 `swebench==5.0.2` 环境、冻结输入导出与
  fail-closed 预检已实现；Ubuntu-24.04 尚未启用 Docker Desktop WSL integration，
  因此 Python Docker SDK 无法连接 daemon，真实评测未启动。
- 对其余历史仓库构建固定依赖镜像。
- FAIL_TO_PASS / PASS_TO_PASS 的正式容器化验证。
- 已完成两条未见 Issue 的冻结多任务复现：pvlib-1606 成功，SQLFluff-1733 因直接编辑约束
  拒绝 read-before-edit 请求而停止，合计 1/2。随后已在两条新 Issue 上完成四 Episode 的
  Strict/Progressive 配对消融：两组硬结果相同；Progressive 在 pydicom-1256 上接受一次目标
  读取后仍因重复读取停止，未消除 Strict 失败。结论不确定，暂不改变默认策略或进入 LoRA。
- Astroid-1268/pydicom-1694 的 read-to-edit Control/Repair 四臂实验已完成，硬成功 2/4；
  两个任务的臂内补丁和硬结果分别相同，且 Repair 两臂都未触发纠正。因此保持默认关闭，
  不把结果解释为动作恢复或正确性增益。

因此现阶段可以用 Pilot 做阅读、适配设计和少量受控 Rollout，但不能发布正式 SWE-bench Lite 成绩。所有本地结果只证明固定环境能观察到对应状态转换；均未使用官方 Docker Harness。不要直接在当前 Python 3.14 主机环境运行这些历史项目的完整测试。

官方 Harness 继续步骤：

```bash
python3 -m venv .venv-swebench
.venv-swebench/bin/python -m pip install -r configs/benchmarks/swebench-harness.txt
.venv-swebench/bin/python tools/probe_official_swebench.py
```

Windows 用户需在 Docker Desktop 的 `Resources > WSL integration` 中启用实际运行
项目的发行版（当前为 Ubuntu-24.04）并 Apply/Restart。仅有 `docker.exe` CLI 可用
还不够：官方 Harness 通过 Python Docker SDK 调用 daemon，预检会明确验证
`docker.from_env()`。探针通过后，才运行准备好的单任务候选；首次实例镜像可能需要
较大磁盘与较长时间。官方文档建议为本地评测预留至少 120GB 空间。

## 13. 推荐推进顺序

如果要在另一台设备继续，请先执行 [`TRAINING_HANDOFF.md`](TRAINING_HANDOFF.md) 的仓库、Python 3.8 环境、准入复跑和证据核验步骤；不要直接复制旧 `generation_commit` 或跳过准入开始模型调用。

1. 用 Core Smoke 的一条任务验证端到端链路。
2. 用 Medium Pilot 的一到两条任务校准模型、Prompt、Turn 和失败日志。
3. 对 Episode 做人工 Reviewer，确认自动测试与主观质量信号能够分离。
4. 从成功与失败轨迹各抽样，检查数据泄漏、Tool 对齐和无效行为。
5. 构建小规模 Train Dataset，先运行 Dry-run 契约验证。
6. Pilot 已扩为六仓库二十题：十八题通过本地准入，两题因历史参数化测试 ID 不足以隔离目标/回归而在零模型调用阶段关闭。Astroid-1978/pydicom-1256 与 Astroid-1268/pydicom-1694 的两组四臂配对实验均已完成并收束为不确定结果；不要重跑 Dev 题做质量重试。
7. 扩充少量真实 Train 数据后运行最小 LoRA，并立即对固定 Test Suite 跑 Base/Adapter 对比。
8. 接入官方 SWE-bench Docker Harness，只运行筛选出的少量 Pilot。
9. 证据链稳定后再扩大任务数、Seed 和消融组。

## 14. 实验完成标准

一次实验只有同时满足下列条件才可标记完成：

- 代码和任务集 Git Commit 固定。
- 任务、模板、隐藏测试、Dataset 和训练配置都有内容哈希。
- Trajectory 与 Verification 可单独读取并相互引用。
- 没有 Train/Test Family 泄漏。
- 训练后端明确区分 Contract Verified 与 Training Verified。
- Base 与训练组在相同 Benchmark 配置下运行。
- 成功率、Token、时延、成本和坏例均有报告。
- Reviewer 结论附证据且不覆盖自动测试事实。
- Experiment Report 的内容哈希可以重新验证。

## 15. 常用检查

```bash
# 任务集
python tools/validate_task_suite.py --manifest task_suites/manifest.json
python tools/validate_task_suite.py --manifest task_suites/medium/manifest.json

# 全部自动测试
python -m unittest discover -s tests -v

# 变更格式
git diff --check

# CLI 参数以当前实现为准
claw train --help
claw benchmark-run --help
claw train-web --help
```

当文档示例和 `--help` 不一致时，以当前代码和测试为准，并在同一提交中修正文档。

## 16. DataFlow、DataFlex 与 LLaMA-Factory 集成路线

这一节描述数据中心化训练链路。当前已完成 Silver 数据契约、确定性 Gold 治理、DataFlow 原生 Operator 与 LlamaFactory 文件导出；5 条固定 Fixture 已在隔离环境完成 DataFlow 原生实跑，2 条真实 Train Episode 已完成原生 LlamaFactory Tokenization，但任务仍偏简单，尚未产生真实 Adapter 或训练效果证据。

当前实现入口：

- `src/claw/data_pipeline/schemas.py`：`agent_training_record.v1`。
- `src/claw/data_pipeline/silver.py`：Trajectory/Verification → Silver 与确定性 Manifest。
- `src/claw/data_pipeline/operators.py`：Tool 对齐、泄漏、失败分类、质量评分和平衡策略。
- `src/claw/data_pipeline/gold.py`：`agent_sft_v1` Gold Pipeline、报告与 Data Card。
- `data_pipelines/agent_sft_v1.py`：DataFlow `OperatorABC/DataFlowStorage` 原生执行定义。
- `src/claw/integrations/llamafactory/exporter.py`：ShareGPT Tool-use、`dataset_info.json` 和导出血缘。
- `configs/integrations/data-centric-upstreams.json`：DataFlow、DataFlex、LLaMAFactory 兼容基线。
- `examples/data_pipeline/silver/`：5 条可重建的脱敏 Silver Fixture。
- `configs/integrations/dataflow-agent-sft-v1-smoke.json`：DataFlow 原生 E2E 环境、哈希与结果证据。

### 16.1 职责划分

```text
Claw
  生成和验证 Agent 数据，维护任务、轨迹、自动测试和实验血缘

DataFlow
  对合规 Agent 数据做画像、失败分类、打分、筛选、平衡和格式导出

LLaMA-Factory
  使用固定 Gold/Raw 数据进行标准 LoRA/QLoRA，提供静态 SFT 基线

DataFlex
  在 LLaMA-Factory 训练基础上执行动态样本选择、数据混合或重加权

Claw Benchmark
  在固定 Test Manifest 上进行独立对比
```

DataFlex 本身构建在 LLaMA-Factory 之上，因此应将二者设计成可比较的训练后端，而不是 `DataFlex -> LLaMA-Factory` 两次串联训练。

### 16.2 数据分层

采用 Bronze/Silver/Gold：

- **Bronze**：不可变 Task、Episode、Trajectory 和 Verification 原始证据。
- **Silver**：DatasetBuilder 完成 Tool 对齐、泄漏检查、去重和标签 Join 后的规范化记录。
- **Gold**：DataFlow 完成质量筛选、分布平衡、权重计算和训练格式导出后的数据产品。

DataFlow 不替代 DatasetBuilder。Oracle、隐藏测试、Test Patch、Test Family 和破损 Tool 序列必须在进入 DataFlow 前被拒绝。

### 16.3 训练格式

优先输出 LLaMA-Factory ShareGPT 格式：

| Claw Role | ShareGPT Role |
|---|---|
| `system` | `system` |
| `user` | `human` |
| Assistant 文本 | `gpt` |
| Assistant Tool Call | `function_call` |
| Tool Result | `observation` |

Exporter 必须同时生成：

- ShareGPT JSON/JSONL。
- `dataset_info.json` 条目。
- Dataset Manifest 与内容哈希。
- Data Card。
- DataFlex 使用的 Domain、Difficulty、Quality Score 和 Sample Weight。

导出后必须做 Chat Template Tokenization Dry-run，确认 Tool Call 与 Observation 没有被截断、重排或训练成错误角色。仓库提供 `tools/run_llamafactory_tokenization_smoke.py` 与固定配置 `configs/training/llamafactory-qwen25-coder-tokenization-smoke.json`；它只加载 tokenizer 和原生 SFT 数据处理器，不加载模型权重。

### 16.4 消融实验

| 组 | 数据 | 训练方式 |
|---|---|---|
| Base | 无训练 | 原始模型 |
| Raw SFT | 合规但未经 DataFlow 筛选的 Silver 数据 | LLaMA-Factory |
| DataFlow SFT | 去重、筛选和平衡后的 Gold 数据 | LLaMA-Factory |
| DataFlex SFT | 同一 Gold 候选池和动态策略 | DataFlex |

四组固定 Base Model、Revision、Seed 集合、训练预算、Chat Template 和 Test Manifest。先完成静态 Raw/DataFlow 对比，再只引入一种 DataFlex 策略，避免同时改变多个变量。

### 16.5 数据指标

实验报告除模型指标外还需包含：

- 输入、保留、排除、派生样本数量。
- 去重率、泄漏率、Tool 对齐失败率。
- Domain、难度、任务类型和长度分布。
- 自动测试、Reviewer、质量分和训练权重分布。
- DataFlow 各 Operator 的输入/输出变化。
- 数据处理 Token、时间和 API 成本。

模型层继续使用 Claw Benchmark 的成功率、测试、Diff、安全、Turns、Tool Calls、Token、时延和成本指标。

### 16.6 实施顺序

1. 已完成：定义 `agent_training_record.v1` 中间格式。
2. 已完成：实现 Trajectory/Verification → Silver Converter。
3. 已完成 ShareGPT Exporter 与真实 LlamaFactory Tokenization Dry-run。
4. 已完成确定性治理核心、DataFlow Operator、固定 Fixture 隔离环境实跑和真实 Episode 装配入口；待扩充多 Family Train/Dev Episode 小批次。
5. 运行 Raw SFT 与 DataFlow SFT 的最小 LoRA 对比。
6. 接入一种 DataFlex 重加权或动态选择策略。
7. 扩展任务 Family、Seed 和真实仓库 Episode。

当前执行顺序固定为：Marshmallow 修复重跑、SQLFluff/pydicom 泛化检查和行为诊断
均已完成；接下来先强化并验证定位后收敛 Guidance，随后生成
20–50 条、至少 5 个 Family 的首批
Train/Dev Episode，冻结 Raw/DataFlow 两套数据版本，并运行 Base、Raw SFT、
DataFlow SFT 的最小 LoRA 对照。只有静态对照和独立 Benchmark 形成可重建报告后，
才接入 DataFlex 单一动态策略；数据质量 Dashboard 随联合报告建设，AgentFlow
继续延后到静态主线稳定之后。

每一步都必须记录失败现象、原因判断、修复措施、重跑结果和证据引用。自动测试、
正常终止、Reviewer 判断和训练效果使用独立字段，不能互相替代。

详细架构和验收标准见：

- [`docs/architecture/data-centric-agent-training-design.md`](docs/architecture/data-centric-agent-training-design.md)
- [`docs/roadmap/data-centric-training-roadmap.md`](docs/roadmap/data-centric-training-roadmap.md)

在真实 Adapter、Checkpoint 和独立 Benchmark 报告产生前，对外表述可使用“完成可追溯 Agent 训练数据契约与 LLaMAFactory Tool-use 导出”或“正在搭建数据中心化训练闭环”，不能使用“已通过 DataFlex 提升模型效果”。

### 16.7 从真实 Episode 生成 Silver

使用独立 Collector 采集 Train/Dev Episode。它复用 Episode、RuntimeAdapter 和
Verifier，但与 BenchmarkRunner 分离并明确拒绝 Test Split：

```powershell
python tools/collect_training_episodes.py `
  --task-suite task_suites/manifest.json `
  --output-root .port_sessions/training-pilot `
  --generation-commit <git-commit> `
  --split train `
  --task-id <train-task-id> `
  --model deepseek-v4-flash `
  --max-turns 20
```

随后只选择已经完成 Verification 并进入 `ARCHIVED` 状态的 Episode：

```powershell
python tools/build_silver_from_episodes.py `
  --task-suite task_suites/manifest.json `
  --episode-dir .port_sessions/<run>/episodes/<episode-1> `
  --episode-dir .port_sessions/<run>/episodes/<episode-2> `
  --generation-commit <git-commit> `
  --output-dir artifacts/silver/real-pilot
```

装配器会核对 Task、Episode、Trajectory、Verification、模板哈希和任务内容哈希，
并兼容真实 runtime adapter v2 与旧 Fixture 轨迹格式。`split=test`、跨 Family
泄漏、未归档 Episode 和不一致的血缘会被拒绝。现有 Benchmark Episode 即使
成功，也不能直接作为训练样本。

确定性 Gold 治理入口：

```powershell
python tools/run_agent_sft_governance.py `
  --silver-records artifacts/silver/real-pilot/silver-records.jsonl `
  --silver-manifest artifacts/silver/real-pilot/silver-manifest.json `
  --output-dir artifacts/gold/real-pilot
```

2026-08-03 至 2026-08-04 已用 `deepseek-v4-flash` 分两次完成 2 个 Train 任务的
真实小批次：Collection Success 2/2，Silver 2 条，Gold 2 条，排除 0 条、泄漏率 0。
随后在 WSL2、LlamaFactory 0.9.4 和固定 Qwen2.5-Coder tokenizer revision 下完成
原生 SFT 预处理，得到 1713/1542 tokens、505/393 个监督 tokens、0 截断。两条任务
覆盖 CLI/Library 与 Add Feature/Fix Bug，但仍是小型合成任务。该结果仅验证执行、
血缘、治理和 Tokenization 链路，不用于声称数据规模充分或模型能力提升。脱敏后的
机器可读证据见 `configs/integrations/dataflow-real-episode-pilot.json` 和
`configs/integrations/dataflow-real-episode-batch2.json`；原始 Episode
和训练消息继续保留在忽略提交的 `.port_sessions`。

### 16.8 AgentFlow 延后接入原则

AgentFlow 被记录为可选的分支轨迹探索层，不替代 Claw Verifier，也不阻塞
DataFlow → LlamaFactory/DataFlex 主线。真实 Silver/Gold 和 Tool-use Tokenization
门槛已经通过单样本验证，但仍优先扩充多 Family 小批次并建立静态 LoRA 基线；
之后才启动 1 个任务、2×2 分支树的隔离 Spike。
详细边界见架构文档第 18 节和路线图 M6。
