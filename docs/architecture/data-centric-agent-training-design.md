# 数据中心化 Agent 训练集成架构设计

文档状态：Proposed
版本：1.0
更新时间：2026-08-03

## 1. 背景

Claw Code Agent 已经具备从版本化任务、隔离 Episode、Agent Trajectory、独立 Verification、DatasetBuilder、训练后端到 Benchmark 和 ExperimentRegistry 的可审计链路。当前链路擅长保证 Agent 数据的结构正确性、任务 Split 隔离、测试证据和实验血缘，但尚未形成一套独立、可组合、可观测的数据治理工作流，也没有接入成熟训练生态完成静态 SFT 与动态数据训练的对照。

本设计引入三个外部能力：

- **DataFlow**：负责离线数据画像、清洗、打分、去重、平衡、增强和流水线编排。
- **LLaMA-Factory**：作为标准 LoRA/QLoRA SFT 基线训练器。
- **DataFlex**：作为构建在 LLaMA-Factory 之上的动态数据选择、混合和重加权训练器。

目标不是替换 Claw 已有的 Agent 数据校验，而是形成从真实交互数据到训练、再回到独立评测的闭环。

官方能力边界：

- DataFlow 使用 Operator 和 Pipeline 组织数据生成、清洗、评估与筛选：[OpenDCAI/DataFlow](https://github.com/OpenDCAI/DataFlow)。
- DataFlex 构建在 LLaMA-Factory 之上，提供 Data Selection、Data Mixture 和 Data Reweighting，并可通过标准 JSON 接收 DataFlow 输出：[OpenDCAI/DataFlex](https://github.com/OpenDCAI/DataFlex)。
- LLaMA-Factory 支持 Alpaca 与 ShareGPT 数据格式；ShareGPT 支持 `human`、`gpt`、`function_call`、`observation` 等角色：[LLaMA-Factory Data Preparation](https://llamafactory.readthedocs.io/en/latest/getting_started/data_preparation.html)。

## 2. 设计目标

### 2.1 功能目标

1. 将 Claw 的 Episode、Trajectory、Verification 转换为稳定的 Agent 训练中间格式。
2. 使用 DataFlow 构建可复现的数据质量 Pipeline，而不是一次性清洗脚本。
3. 同时导出 LLaMA-Factory 静态训练数据和 DataFlex 动态训练数据。
4. 使用同一独立 Test Manifest 对 Base、原始 SFT、治理后 SFT、动态训练模型进行消融评测。
5. 将数据版本、Operator 版本、筛选策略、训练配置、Checkpoint 和 Benchmark 报告写入 ExperimentRegistry。
6. 提供数据层指标，使项目能够解释“哪些数据产生了收益”，而不只展示 Loss 曲线。

### 2.2 非目标

- 不在第一阶段替换 `DatasetBuilder` 的 Tool 对齐、泄漏检查和 Family Split 规则。
- 不把 Reviewer 分数作为唯一质量标签。
- 不在 Claw 主运行环境内直接安装全部 GPU 训练依赖。
- 不承诺尚未通过真实训练和独立 Benchmark 验证的模型提升。
- 不把 Smoke Suite 成功率作为训练效果结论。

## 3. 当前能力与缺口

| 能力 | 当前状态 | 本设计中的位置 |
|---|---|---|
| 版本化任务与模板哈希 | 已实现 | Bronze 数据来源 |
| Episode 隔离、Checkpoint、恢复 | 已实现 | 数据采集执行层 |
| 追加式 Trajectory v2 | 已实现 | 原始事实记录 |
| 自动测试、Diff、Reviewer Verification | 已实现 | Silver 硬/软标签 |
| Tool 对齐、去重、泄漏检查 | 已实现 | Silver 准入门槛 |
| Dataset Manifest 与内容哈希 | 已实现 | 数据血缘基础 |
| Dry-run 与 PEFT SFT Backend | 已实现 | 现有训练基线/契约 |
| 独立 Benchmark 与四组消融 | 已实现 | 模型效果验证 |
| AgentTrainingRecord 与 Silver Manifest | 已实现 | DataFlow 稳定输入契约 |
| LLaMA-Factory ShareGPT 导出 | 已实现 | 静态训练数据出口 |
| 上游版本与环境边界 | 已固定 | 文件协议与三环境隔离 |
| 通用数据画像和报告 | 已实现核心报告 | Gold Pipeline |
| 可组合确定性治理 Operator | 已实现 | DataFlow Adapter |
| DataFlow 隔离环境原生实跑 | Fixture 已验证 | M2 集成证据 |
| Archived Episode → Silver 装配 | 已实现 | 真实数据入口 |
| Train/Dev Episode Collector | 已实现，2 条真实 Episode 批次通过 | 数据采集入口 |
| LlamaFactory Tokenization Dry-run | 2 条真实 Episode 批次通过 | 格式兼容验证 |
| SWE-bench Lite Agent/Evaluator 边界 | 安全加载、候选隔离评测与首组 Dev Episode 已实现 | 真实仓库数据入口 |
| DataFlex 动态选择/加权 | 待实现 | 动态训练组 |
| 数据治理前后效果归因 | 待实现 | 实验报告扩展 |

## 4. 目标架构

```text
TaskSuite / SWE-bench Adapter / Curated Tasks
                    |
                    v
          EpisodeOrchestrator
                    |
          +---------+----------+
          |                    |
          v                    v
   Trajectory v2        Verification v2
          |                    |
          +---------+----------+
                    |
                    v
            DatasetBuilder
      Tool alignment / leakage / dedup
                    |
                    v
        AgentTrainingRecord v1
                    |
                    v
             DataFlow Pipeline
  profile -> annotate -> score -> filter -> balance
                    |
          +---------+----------+
          |                    |
          v                    v
 LLaMA-Factory Export    DataFlex Export
   static ShareGPT       labels/weights/domains
          |                    |
          v                    v
 LLaMA-Factory SFT       DataFlex Train
          |          (built on LLaMA-Factory)
          +---------+----------+
                    |
                    v
            BenchmarkRunner
                    |
                    v
           ExperimentRegistry
```

### 4.1 为什么保留 DatasetBuilder

DataFlow 的通用 Operator 不理解 Claw 的以下约束：

- Tool Call 必须与唯一 Tool Result 对齐。
- Test Split 和同一 `family_id` 不得进入训练数据。
- Oracle、隐藏测试、Gold Patch、Test Patch 不得进入模型上下文。
- 自动测试失败与环境失败必须分开。
- Reviewer 不能覆盖自动测试硬失败。

因此 `DatasetBuilder` 是 Agent 语义准入层，DataFlow 是其后的数据治理与优化层。两者职责不重叠。

## 5. Bronze、Silver、Gold 数据分层

### 5.1 Bronze：不可变原始事实

Bronze 保存原始、可回放证据：

- `TaskSpec` 和 Task Manifest 引用。
- `EpisodeManifest` 和 Workspace Checkpoint。
- Trajectory Header、事件和外部 Artifact。
- Verification Report。
- Git Commit、Runtime/Prompt/Tool/Verifier 版本。

Bronze 只追加，不原地修改。清洗错误时必须能够从 Bronze 重新生成后续层。

### 5.2 Silver：规范化 Agent 样本

Silver 由 `DatasetBuilder` 产生，完成：

- Schema 校验。
- 消息和 Tool 事件重建。
- Tool Call/Result 对齐。
- Split/Family 泄漏检查。
- 内容级去重。
- 自动测试和 Reviewer 信号 Join。
- 排除原因记录。

### 5.3 Gold：训练就绪数据产品

Gold 由 DataFlow Pipeline 产生：

- 质量分、效率分和安全分。
- Domain、任务类型、难度和失败类型标签。
- 筛选结果与保留原因。
- 静态采样概率或训练权重。
- LLaMA-Factory ShareGPT 数据。
- DataFlex 所需的 Domain、Weight、Selector 特征。
- 数据卡、指标报告和内容哈希。

## 6. AgentTrainingRecord v1

DataFlow 与 Claw 之间采用稳定的中间格式，避免 DataFlow Operator 直接依赖内部 Python 对象。

```json
{
  "schema_version": "agent_training_record.v1",
  "record_id": "record-...",
  "task": {
    "task_id": "...",
    "task_version": "1.0.0",
    "family_id": "...",
    "split": "train",
    "domain": "python-service",
    "task_type": "fix_bug",
    "difficulty": "medium"
  },
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {
      "role": "assistant",
      "content": "",
      "tool_calls": [
        {"id": "call-1", "name": "read_file", "arguments": {"path": "x.py"}}
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call-1",
      "name": "read_file",
      "content": "..."
    }
  ],
  "verification": {
    "hard_gate_passed": true,
    "tests_passed": true,
    "diff_scope_passed": true,
    "reviewer_score": 0.91
  },
  "execution": {
    "turns": 19,
    "model_calls": 19,
    "tool_calls": 27,
    "input_tokens": 15000,
    "output_tokens": 3000,
    "latency_seconds": 116.6
  },
  "features": {
    "failure_type": null,
    "quality_score": null,
    "efficiency_score": null,
    "sample_weight": null
  },
  "lineage": {
    "trajectory_ref": "...",
    "verification_ref": "...",
    "generation_commit": "...",
    "prompt_version": "...",
    "tool_version": "..."
  }
}
```

约束：

- `record_id` 由规范化内容和血缘字段生成，必须稳定。
- `split=test` 的记录禁止进入训练导出。
- Tool 结果不得丢失 `tool_call_id`。
- 原始事实字段和 DataFlow 派生字段分开。
- Operator 不得覆盖 `lineage` 和 `verification` 原始证据。

## 7. DataFlow Operator 设计

### 7.1 `ClawRecordReader`

输入：Silver JSONL 与 Dataset Manifest。
输出：DataFlow Storage 中的规范化记录。

职责：

- 验证 Schema Version 和 Manifest 哈希。
- 展开任务、执行和 Verification 字段供后续 Operator 使用。
- 拒绝 Test Split。

### 7.2 `ToolAlignmentValidator`

检查：

- Tool Call ID 唯一。
- 每个 Tool Call 恰好有一个 Tool Result。
- Result 出现在对应 Call 之后。
- 终止事件中的 Tool Call 计数与轨迹一致。

该 Operator 是二次防线；第一道防线仍位于 DatasetBuilder。

### 7.3 `LeakageGuardOperator`

检查记录中是否出现：

- Oracle 文件内容。
- Gold/Test Patch。
- 隐藏测试源码与测试标识。
- Test Family 或 Test Task 引用。
- 明确的答案字段或生成器内部路径。

输出 `leakage_flags` 和证据摘要。任何高风险命中均为硬过滤。

### 7.4 `FailureTaxonomyAnnotator`

失败类别：

| 类别 | 说明 |
|---|---|
| `test_failure` | 代码行为不满足测试 |
| `diff_violation` | 修改超出允许范围 |
| `tool_error` | 工具执行或参数错误 |
| `permission_denied` | 权限策略拒绝 |
| `timeout` | Episode 或命令超时 |
| `environment_failure` | 依赖、网络、镜像等环境原因 |
| `invalid_termination` | 未正常结束或重复循环 |
| `format_failure` | Tool/消息格式损坏 |

环境失败默认不作为负面模型样本。

### 7.5 `TrajectoryQualityScorer`

建议分数：

```text
quality_score =
    hard_gate * (
        0.45 * correctness
      + 0.20 * diff_safety
      + 0.15 * reviewer_quality
      + 0.10 * tool_validity
      + 0.10 * efficiency
    )
```

其中 `hard_gate` 为 0/1。权重必须版本化，并在 Dev Split 上确定，不能根据 Test 结果反向调整。

### 7.6 `DomainDifficultyBalancer`

按下列字段生成数据切片：

- Domain。
- `task_type`。
- Difficulty。
- 成功/失败类型。
- 轨迹长度区间。
- 工具组合。

输出采样概率、数据分布报告和目标分布偏差。默认不复制样本，而是使用权重或采样清单避免重复放大。

### 7.7 `TrajectoryRefiner`

可选 LLM Operator，用于：

- 删除无意义重复读取。
- 精简冗长自然语言。
- 保留 Tool Call/Result 顺序和参数。
- 将可修复失败轨迹重写为带错误反思的训练样本。

所有 Refined 样本必须：

- 指向原始 `record_id`。
- 使用新的 `record_id` 和 `derived_from`。
- 再次通过 Tool 对齐和泄漏检查。
- 与原始轨迹分别评测，不能覆盖 Bronze/Silver 数据。

### 7.8 `LlamaFactoryExporter`

输出：

- `train-sharegpt.json` 或 JSONL。
- `dataset_info.json` 条目。
- `gold-manifest.json`。
- `data-card.md`。

角色映射：

| Claw | LLaMA-Factory ShareGPT |
|---|---|
| `system` | `system` |
| `user` | `human` |
| Assistant 文本 | `gpt` |
| Assistant Tool Call | `function_call` |
| Tool Result | `observation` |

导出后必须使用目标模型的 Chat Template 做一次 Tokenization Dry-run。

## 8. LLaMA-Factory 基线训练

LLaMA-Factory 用于建立可解释的静态 SFT 基线：

```text
Raw SFT       = 未做 DataFlow 质量筛选的合规 Silver 样本
Filtered SFT  = 通过 DataFlow 筛选和平衡的 Gold 样本
```

两组必须固定：

- Base Model 和不可变 Revision。
- Chat Template。
- LoRA Rank、Target Modules、学习率、Epoch、Batch 和 Cutoff Length。
- Seed、硬件和依赖版本。
- 相同 Test Manifest 和 Benchmark 配置。

配置示例属于规划产物，不能在真实运行前标记为 Training Verified。

## 9. DataFlex 动态训练

DataFlex 是 LLaMA-Factory 的扩展训练路径，不是额外串联在 LLaMA-Factory 之后的第二次训练。

第一阶段只选择一种低风险算法类别：

- 基于 `quality_score` 的样本重加权；或
- 在固定候选池上的动态样本选择。

第二阶段再考虑：

- 多 Domain 数据比例动态调整。
- Model-in-the-loop 的 Gradient/Embedding Selection。
- 多种 Selector/Mixer/Reweighter 对照。

DataFlex 输入除 ShareGPT 消息外还需要稳定的数据特征：

- `record_id`。
- `domain`。
- `difficulty`。
- `quality_score`。
- `sample_weight`。
- `verification` 摘要。

任何 DataFlex 结果都必须与同配置的 LLaMA-Factory 静态 SFT 比较，避免把基础训练配置变化误判为动态数据策略收益。

## 10. 环境隔离与调用方式

三个系统使用独立环境：

```text
env-claw       Agent 运行、Episode、DatasetBuilder、Benchmark
env-dataflow   DataFlow Operator 与离线数据处理
env-training   DataFlex + LLaMA-Factory + PyTorch/CUDA
```

推荐通过文件协议和受控子进程连接，不把外部框架作为 Claw 核心依赖：

```text
Claw writes immutable input manifest
  -> adapter starts external command
  -> external system writes output + evidence
  -> Claw validates hash/schema
  -> ExperimentRegistry attaches artifact refs
```

每个 Adapter 记录：

- 命令和脱敏后的参数。
- Python、CUDA、Torch 和框架版本。
- Git Commit 或 Package Version。
- 输入/输出路径和 SHA-256。
- 返回码、stdout/stderr 摘要和耗时。
- 可恢复 Checkpoint。

## 11. Claw 代码集成建议

```text
src/claw/data_pipeline/
  schemas.py                 AgentTrainingRecord v1
  bronze.py                  原始证据索引
  silver.py                  DatasetBuilder 输出适配
  episode_source.py          Archived Episode 真实产物装配
  collection.py              Train/Dev Episode Collector 与 Manifest
  manifest.py                Pipeline Manifest

src/claw/integrations/dataflow/
  adapter.py                 外部进程/文件协议
  config.py                  版本化配置
  evidence.py                DataFlow 运行证据

src/claw/integrations/llamafactory/
  exporter.py                ShareGPT + dataset_info
  backend.py                 LLaMA-Factory CLI Adapter

src/claw/integrations/dataflex/
  exporter.py                动态特征与权重
  backend.py                 DataFlex CLI Adapter

data_pipelines/
  agent_sft_v1.py            DataFlow Pipeline 定义
  operators/                 Claw 自定义 Operator

configs/training/
  llamafactory_raw_lora.yaml
  llamafactory_filtered_lora.yaml
  dataflex_weighted_lora.yaml
```

拟新增 CLI：

```bash
claw data-build --experiment <id> --strategy verifier_filtered
claw dataflow-run --input <silver-manifest> --pipeline agent_sft_v1
claw training-export --target llamafactory --manifest <gold-manifest>
claw training-run --backend llamafactory --config <yaml>
claw training-run --backend dataflex --config <yaml>
claw experiment-report --id <id>
```

上述统一 `claw` CLI 仍是设计目标；当前已先提供
`tools/build_silver_from_episodes.py` 作为真实 Episode 批量物化入口。

该入口只接受 `ARCHIVED` Episode，校验 Episode、TaskSpec、Trajectory 和
Verification 的引用、版本、模板哈希与内容哈希后，才装配为 `DatasetRecord`。
运行时新式 `model_request.messages` / `model_response.tool_calls` 事件与旧式
`payload.message` 轨迹均可重建，避免 Fixture 格式与真实 rollout 格式脱节。

```bash
python tools/build_silver_from_episodes.py \
  --task-suite task_suites/manifest.json \
  --episode-dir <archived-episode-1> \
  --episode-dir <archived-episode-2> \
  --generation-commit <git-commit> \
  --output-dir artifacts/silver/real-pilot
```

该入口仍执行 Train/Test Split 和 Family 泄漏检查；已有 Test Episode 可以用于
Benchmark 和装配器验证，但不能因为 rollout 成功而进入训练导出。

训练采集必须通过独立 `tools/collect_training_episodes.py`。Collector 复用现有
Episode、RuntimeAdapter 和 Verifier，生成 `training_episode_collection.v1`，记录
Task、模型、解码配置、Episode/Trajectory/Verification 引用、结果哈希与分 Slice
指标；它只接受 Train/Dev，现有 Benchmark adapter 继续只接受 Test。这样不会为
了采集训练数据而放宽评测边界。

真实数据完成 Silver 后，可使用 `tools/run_agent_sft_governance.py` 生成 Gold、
排除报告、Data Card 和 LLaMAFactory 导出。

## 12. 实验与消融矩阵

| 组 | 数据 | 训练器 | 要回答的问题 |
|---|---|---|---|
| `base` | 无 | 无 | 原始模型基线 |
| `raw_sft` | 合规 Silver，不做质量筛选 | LLaMA-Factory | 仅增加 Agent 数据是否有效 |
| `dataflow_sft` | DataFlow 去重、筛选和平衡 | LLaMA-Factory | 离线数据治理的增量价值 |
| `dataflex_sft` | 同一 Gold 候选池 + 动态策略 | DataFlex | 动态选择/重加权的增量价值 |

兼容现有四组名时，可将 `dataflow_sft` 映射为 `verifier_sft`，但报告中应显式记录 DataFlow Pipeline ID。后续可以升级 Benchmark Schema，直接使用新的组名。

### 12.1 数据层指标

- 输入、保留、排除和派生样本数量。
- 去重率、泄漏命中率、Tool 对齐失败率。
- Domain、难度、任务类型和轨迹长度分布。
- 自动测试、Reviewer 和综合质量分分布。
- 数据 Token 数、处理时延和 LLM Operator 成本。
- 每个 Operator 的输入/输出变化和排除原因。

### 12.2 模型层指标

- Task Success Rate 和测试通过率。
- Diff 越界率、权限违规率、工具格式错误率。
- Turns、Model Calls、Tool Calls。
- 输入/输出 Token、延迟和成本。
- 各 Domain/Difficulty Slice 指标。
- Base 到各训练组的绝对和相对变化。

### 12.3 统计边界

- 小于统计所需规模的 Pilot 只报告个案，不报告显著提升。
- 至少使用多个 Seed。
- Test 任务不能用于调 Operator 阈值或训练参数。
- 训练数据、模型和 Benchmark 的版本引用必须可重建。

## 13. 安全与数据治理

### 13.1 数据泄漏

- Test Split、Gold Patch、Test Patch 和隐藏测试永不进入 DataFlow 训练输入。
- DataFlow LLM Operator 只接收已脱敏 Silver 字段。
- 任何外部 API 处理都必须记录所发送字段，并允许切换本地模型。

### 13.2 隐私与凭据

- Trajectory 进入 Bronze 前执行凭据模式扫描和路径脱敏。
- API Key、用户目录、Git Remote Token 和私有仓库内容不得进入训练集。
- 原始 Artifact 的访问权限与派生数据分离。

### 13.3 可追溯性

Gold 样本必须能够追溯到：

```text
Gold record
  -> DataFlow output record
  -> Silver record
  -> Trajectory + Verification
  -> Episode + TaskSpec
  -> Git Commit + Runtime versions
```

## 14. 验收标准

### 14.1 Data Contract

- AgentTrainingRecord Schema 有正反例测试。
- Test Split、泄漏字段和破损 Tool 序列被拒绝。
- 同一输入生成相同 Record ID 和 Manifest Hash。

### 14.2 DataFlow

- Pipeline 可在最小样本上离线运行。
- 每个 Operator 可单测并输出结构化证据。
- 重跑结果确定，LLM Operator 除外；LLM Operator 必须记录模型与 Seed。
- Data Card 能解释每条排除规则和分布变化。

### 14.3 LLaMA-Factory

- ShareGPT 导出通过 Schema 和 Tool 顺序检查。
- `dataset_info.json` 可被 LLaMA-Factory 加载。
- Tokenization Dry-run 不丢失 `function_call/observation`。
- 最小 LoRA 训练生成真实 Adapter 和训练证据。

### 14.4 DataFlex

- 使用同一 Gold 候选池完成一种动态策略。
- 输出实际选择/权重日志。
- 与静态 LLaMA-Factory 配置保持可比。
- 失败可恢复，且不会覆盖既有 Experiment 证据。

### 14.5 Benchmark

- 四组使用相同 Test Manifest、预算和解码配置。
- 报告同时包含数据层和模型层指标。
- 训练效果结论与证据级别一致。

## 15. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 外部框架版本变化快 | Adapter 失效 | 固定版本/Commit，文件协议隔离 |
| Agent Tool 格式被模板破坏 | 训练无效 | ShareGPT 映射测试和 Tokenization Dry-run |
| Reviewer 偏差 | 错误筛选 | 自动测试硬门槛、Reviewer 只作软信号 |
| 数据量太少 | 无法证明提升 | 先证明闭环，再扩充任务 Family 与 Seed |
| 动态训练变量过多 | 无法归因 | 先静态基线，再只引入一种 DataFlex 策略 |
| GPU/依赖冲突 | 主项目不可用 | 独立环境或容器，Claw 仅调用 Adapter |
| LLM 清洗改变事实 | 数据污染 | 原始数据不可变、派生样本重新验证 |

## 16. 推荐决策

按以下顺序推进：

1. 已完成 AgentTrainingRecord 和 LLaMA-Factory ShareGPT Exporter。
2. 已完成不依赖 LLM 的确定性治理核心与 DataFlow 原生包装。
3. 已完成 2 条真实 Train Episode 的 Silver/Gold 与目标 tokenizer 原生 Dry-run；
   已完成 2 条不同 SWE-bench Lite Dev 历史仓库校准和受控 Rollout；Marshmallow
   最佳候选通过选定测试但未合规终止，Astroid 有一次 Schema 中断且有效重试仍
   未修复 FAIL_TO_PASS、再次耗尽 turns。当前已修复轨迹事件契约并保留两类失败
   轨迹；下一步提炼路径定位与验证策略、扩充真实 Train/Dev 小批次，再运行
   Raw SFT 与 DataFlow SFT 的最小 LoRA 对比。
4. 数据和 Benchmark 稳定后，引入一种 DataFlex 重加权或选择策略。
5. 最后才引入 LLM Refiner、复杂动态混合和大规模任务。

这一顺序能够在每个阶段产生独立、可演示、可验证的成果，并把“Agent 能力”“数据治理能力”和“训练能力”分开归因。

## 17. 当前实现基线（2026-08-03）

- DataFlow 固定为 Commit `047ae01698b0cdc63556c4ee9560c37ed34884b2`，包版本 `1.0.10`。
- DataFlex 固定为 Commit `3c13e372637ffb6f931d9ee058f7969cee15e468`，包版本 `1.0.0`。
- LLaMAFactory 兼容下限按 DataFlex 当前依赖固定为 `0.9.4`。
- 兼容信息以 `configs/integrations/data-centric-upstreams.json` 为机器可读真源。
- Claw、DataFlow、Training 使用隔离环境，通过版本化 JSON 与后续 subprocess Adapter 交换数据。
- 已实现的 M1 核心不需要 DataFlow、DataFlex、Torch 或 GPU；外部源码仅用于接口核对，不进入项目提交。
- 已实现 `agent_sft_v1`：输出 Gold JSONL、排除原因、质量/分布报告、Data Card、Gold Manifest 与 LLaMAFactory 数据；DataFlow 包装采用 `OperatorABC.run(DataFlowStorage)`。
- 5 条固定脱敏 Fixture 已在 DataFlow 1.0.10、Python 3.11 CPU 环境完成六步原生 E2E；证据见 `configs/integrations/dataflow-agent-sft-v1-smoke.json`。
- 已实现 Archived Episode → DatasetRecord/Silver 的真实产物入口，并兼容
  runtime adapter v2 的完整 request snapshot、结构化 response/tool 事件。
- 已实现专用 Train/Dev Episode Collector。`deepseek-v4-flash` 两样本 Pilot 已
  完成 Train Episode → Silver → Gold：Collection Success 2/2，Gold 保留 2 条、
  排除 0 条、泄漏率 0，并完成 DataFlow 六步原生执行；脱敏证据见
  `configs/integrations/dataflow-real-episode-pilot.json`。原始产物位于本地
  `.port_sessions`，不作为版本化训练数据提交。

## 18. AgentFlow 可选融合决策

AgentFlow 与 DataFlow/DataFlex 属于同一 OpenDCAI 生态，但职责不同：
DataFlow/DataFlex 位于“数据治理与训练”主线，AgentFlow 只作为后续的“分支轨迹
探索”研究扩展，不成为当前 M1–M4 的前置依赖，也不替换 Claw 的 Episode、
Trajectory、Verifier 或 Benchmark。上游能力与后续接口核对以
[OpenDCAI/AgentFlow](https://github.com/OpenDCAI/AgentFlow) 固定 Commit 为准。

```text
AgentFlow synthesis policy（分支策略与候选路径）
  -> Claw AgentFlow Backend（有状态代码环境协议）
  -> 每分支独立 worktree / sandbox snapshot
  -> Claw Trajectory v2
  -> Claw Verifier 硬门槛
  -> chosen/rejected 或树形训练记录
  -> DataFlow / LLaMA-Factory / DataFlex
```

设计约束：

1. AgentFlow 的线性 rollout 与 synthesis tree 分开对待，只复用适合代码任务的
   分支策略和调度能力。
2. 并发兄弟分支不得共享可变 Workspace；每个分支必须拥有独立 worktree、
   snapshot 或等价隔离单元。
3. Action Dedup 使用 `(state_hash, normalized_action)`，不能只按工具名和参数
   去重；同一测试或读取在不同代码状态下仍是有效动作。
4. 深度、观察长度和工具多样性只能做软排序。测试、Diff Scope、权限、格式与
   可复现性继续由 Claw Verifier 作为硬门槛。
5. AgentFlow QA synthesis 不作为代码轨迹主产物，只可用于失败归因、补丁解释
   等辅助派生数据，且必须保留 `derived_from` 并重新验证。
6. 采用可选 `integrations/agentflow/` Adapter 和固定上游 Commit，不把 AgentFlow
   安装为 Claw 核心运行依赖。

启动时机必须同时满足：

- 至少一批真实 Train Episode 已完成 Silver → Gold。
- LLaMA-Factory Tool-use Tokenization Dry-run 已通过。
- 静态 Raw/DataFlow 数据路径已有可重复基线，AgentFlow 实验不会阻塞主线。

首个 Spike 只运行 1 个中等任务、`branching_factor=2`、`depth=2`，禁用 QA
synthesis；验收指标包括分支污染为零、状态哈希可复现、Verifier 结果正确、
额外成本可量化，以及至少产生一组合法的 chosen/rejected 轨迹。未达到这些条件
前，不在简历中表述为已完成 AgentFlow 集成。
