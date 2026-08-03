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

复制 `.env.example` 为 `.env`，选择 Anthropic 原生或 OpenAI 兼容协议。不要同时保留无意使用的 `ANTHROPIC_*` 和 `OPENAI_*` 配置，因为任一 `ANTHROPIC_*` 变量都会选择 Anthropic 模式。

真实 LoRA/QLoRA 训练还需要相应的 PyTorch、Transformers、PEFT、Accelerate；QLoRA 另外需要 BitsAndBytes。是否可用以 `PeFTSFTBackend.prepare()` 的依赖检查为准，不应仅凭包已安装就宣称训练可用。

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

### 6.4 固定实验变量

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

### 6.5 结果解释

每个结果应至少检查：

- 自动测试是否通过。
- 初始测试是否正确失败，避免任务模板本身已经满足答案。
- 是否只修改允许路径。
- Stop Reason、模型调用数、工具调用数和 Turn 数是否一致。
- 输入/输出 Token、时延和估算成本。
- Reviewer 分数是否有明确 Rubric 和证据引用。

一次成功 Episode 只是个案。模型质量结论至少需要多任务、多 Seed、相同预算和独立 Test Split。

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

详见 [`benchmarks/swe_bench_lite/README.md`](benchmarks/swe_bench_lite/README.md)。

当前未完成：

- 官方 Docker Harness 接入。
- 对每个历史仓库构建固定依赖镜像。
- FAIL_TO_PASS / PASS_TO_PASS 的正式容器化验证。
- 将 SWE-bench 实例转换为本项目 `TaskSpec` 和 Episode 的稳定 Adapter。

因此现阶段可以用 Pilot 做阅读、适配设计和少量人工 Rollout，不能发布正式 SWE-bench Lite 成绩。不要直接在当前 Python 3.14 主机环境运行这些历史项目的完整测试；它们的依赖和 Python 版本需要容器隔离。

## 13. 推荐推进顺序

1. 用 Core Smoke 的一条任务验证端到端链路。
2. 用 Medium Pilot 的一到两条任务校准模型、Prompt、Turn 和失败日志。
3. 对 Episode 做人工 Reviewer，确认自动测试与主观质量信号能够分离。
4. 从成功与失败轨迹各抽样，检查数据泄漏、Tool 对齐和无效行为。
5. 构建小规模 Train Dataset，先运行 Dry-run 契约验证。
6. 有 GPU 后运行最小 LoRA 实验，并立即对固定 Test Suite 跑 Base/Adapter 对比。
7. 接入官方 SWE-bench Docker Harness，只运行筛选出的少量 Pilot。
8. 证据链稳定后再扩大任务数、Seed 和消融组。

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
