# 低成本 Agent 后训练实验路线

状态：**目标方案，待实测回填**。本文定义个人可承担预算下的最小研究闭环；文中的规模、资源与阈值是实验设计，不是已经获得的结果。真实结论只能来自 `ExperimentRegistry`、训练产物和固定 Test Manifest 报告。

## 1. 研究目标

围绕一个可回答的问题推进：

> 在固定训练与推理预算下，经可验证筛选的 Coding Agent Trajectory 能否通过 LoRA SFT 改善小模型的软件任务能力；基于隐藏测试与 Patch 约束的 GRPO/RLVR 能否在 SFT 基础上提供进一步增益？

主线收敛为：

```text
公开 Coding Agent Trajectory + 少量 Claw 自采 Episode
  -> AgentTrainingRecord（来源、许可、任务族与验证证据）
  -> DataFlow（对齐、泄漏、去重、评分与配比）
  -> LLaMA-Factory LoRA SFT
  -> Claw 独立 Benchmark
  -> Patch-level GRPO/RLVR Method Validation
```

DataFlow 保留为离线数据治理方法；DataFlex 延后到静态 SFT 与 RLVR 基线稳定之后，不作为当前闭环的前置条件。

## 2. 数据计划

### 2.1 数据组成

- 接入 1,000–3,000 条许可清晰的公开 Coding Agent Trajectory，保留来源、版本、许可证、原始任务 ID 与内容哈希。
- 使用 50–100 个 Claw Train Task，每题运行 2–4 个 Rollout，目标采集 100–400 条自有 Episode。
- 使用 Repository Family 进行 Train/Dev/Test 隔离；公开数据与 Claw Benchmark 同样执行任务 ID、仓库、Patch、文本近重复与家族级污染检查。
- 成功轨迹用于 SFT；失败轨迹用于失败分类、Reward 设计、轨迹诊断与回归，不因失败而直接丢弃。

### 2.2 必须自动统计

- 数据源、Task、Episode、Trajectory 与有效训练样本数量。
- 输入/输出 Token、监督 Token、轨迹长度和 Tool Call 数分布。
- Tool 对齐失败率、泄漏拦截率、去重率与各 Operator 保留率。
- Repository Family、任务类型、难度和成功/失败类型分布。
- Claw 自采 Episode 的有效编辑率、正常终止率、测试通过率与平均成本。

所有统计写入 Dataset Manifest、Data Card 和机器可读报告，不在文档中手工维护易失效的数字。

## 3. SFT 最小实验

### 3.1 首选配置

- Base Model：`Qwen/Qwen2.5-Coder-1.5B-Instruct`，固定实际使用的 Revision；如果 24GB 显存或吞吐不满足预算，再降级到同系列 0.5B，而不是同时改变数据与训练策略。
- 训练方式：LoRA 或 QLoRA；5M–15M 有效训练 Token；1–2 Epoch。
- 资源上限：单张 24GB GPU；完整 SFT 阶段云资源预算不超过人民币 400 元。
- 第一阶段只比较 `base` 与 `curated_sft`；资源允许时再补 `raw_sft`，用于隔离数据治理增益。

### 3.2 验收条件

- 产生可加载的 Adapter、Tokenizer、训练日志和校验后的 Checkpoint 引用。
- 记录实际训练 Token、GPU 型号、GPU Hours、峰值显存、吞吐、Seed 和完整配置。
- Base 与 Adapter 使用相同 Prompt、工具、最大 Turns、解码参数和 Test Manifest。
- 无论提升、持平或下降，都保留按任务类型拆分的结果和 Bad Case。

## 4. RLVR 最小实验

完整多轮工具型 Agent GRPO 不是个人预算下的首要门槛。第一阶段采用 Patch-level RLVR：模型读取任务与受控代码上下文并生成候选 Patch，Claw 在隔离仓库中应用 Patch、挂载隐藏测试、运行回归并计算可验证奖励。

### 4.1 首选配置

- 第一实现框架：Hugging Face TRL `GRPOTrainer`，关闭独立 vLLM Server，从 Transformers/PEFT 的单卡同步生成开始；通过自定义 `reward_funcs` 或 `rollout_func` 调用 Claw Patch Verifier。
- Policy：优先沿用已验证的 1.5B SFT Checkpoint，保持模型与 Benchmark 可直接比较；仅在单卡 Smoke 无法运行时建立独立的 0.5B Method Validation 分支。
- Train Task：50–100；Group Size 4；先运行 20 Step Smoke Test，再决定是否扩展到几十至数百 Step。
- 资源上限：单张 24GB GPU，必要时使用 LoRA、短上下文和生成/训练分时复用；RLVR 阶段云资源预算不超过人民币 500 元。
- Reward 主信号：隐藏测试、回归测试、Patch Scope 与测试篡改检测；格式、长度和 Reviewer 不作为主要奖励。

### 4.2 必须监控

- Reward Mean/Std、非零奖励率、组内奖励方差与零奖励组比例。
- KL、Entropy、生成长度、完成率、无效 Patch 率和越界修改率。
- Step 时间、Rollout/Verifier/Update 时间占比、峰值显存、GPU 利用率与 GPU Hours。
- Reward Hacking：删除或修改测试、硬编码样例、访问 Oracle、伪造工具结果和扩大修改范围。

当连续多个评估窗口的组内奖励方差接近零、独立 Dev 无改善或安全指标恶化时停止扩容。负结果同样作为实验结论，不通过增加预算掩盖。

### 4.3 框架升级边界

- **TRL（当前选择）**：用于单卡 Patch-level GRPO；接口轻、支持 PEFT 与自定义 Reward，便于先验证训练是否存在有效信号。
- **verl（后续优先）**：获得多卡资源并升级为多轮 Tool Agent 时采用；利用异步 Rollout、推理/Agent 解耦和 Tool Agent Loop 降低环境等待造成的 GPU 空转。
- **slime（Deferred）**：保留现有 Adapter 和自定义 Reward 接口研究，但其 Megatron + SGLang 主路径更适合规模化训练，不作为个人预算下的首个真实 GRPO 后端。
- **OpenRLHF（备选）**：当团队已有 Ray + vLLM + DeepSpeed 运维经验时考虑；当前不同时维护多个 RL 后端。

## 5. 独立 Benchmark

- 冻结 30–50 个完全隔离的 Test Task，至少覆盖多个 Repository Family 和任务类型。
- 第一阶段比较 Base 与 Curated SFT；RLVR 训练稳定后加入 SFT+GRPO。
- 指标包括任务成功率、目标/回归测试通过率、正常终止率、越界修改率、平均 Turns、Tool Calls、推理 Token 与成本。
- 小样本报告同时给出成功数/总数和 Bootstrap 置信区间，不仅报告百分比。
- 不将本地校准结果称为官方 SWE-bench 分数；官方成绩必须通过官方隔离 Harness 获得。

### 5.1 首轮目标值（不是实验结果）

首轮冻结 50 个 Test Task，每组使用 2 个固定 Seed，共 100 个评测 Episode。目标值用于预算、停止条件和结果表设计，不得作为已完成成绩对外发布：

| 组 | 目标训练数据 | 目标成功率 | 相对前组目标 | 安全约束 |
|---|---:|---:|---:|---|
| Base | 无 | 18% | - | 建立基线 |
| Curated SFT | 1,200–1,600 条 / 8M–12M Token | 24% | +6 pp | 越界修改率不高于 Base |
| SFT + GRPO | 50–100 Train Task / Group Size 4 | 27% | +3 pp | 回归破坏率不高于 SFT |

对应 100 个评测 Episode 的目标成功数约为 18、24、27。该规模只能作为项目级效果信号，不能宣称统计显著或普适结论。最低验收门槛设为：SFT 相对 Base 至少多成功 4 个 Episode；GRPO 若没有进一步提升，只要训练和 Reward 证据完整，也记录为负结果并分析零奖励组、策略漂移和 Reward Hacking。

## 6. 预算门槛

```text
公开数据治理                     约 0 元
Claw 自采 100–400 Episode        50–300 元
1.5B LoRA SFT                    200–400 元
1.5B Patch-level GRPO Smoke      200–500 元
30–50 题独立 Benchmark           50–200 元
```

总预算上限设为人民币 1,500 元，并按阶段解锁：数据与 Reward 契约未通过时不租 GPU；SFT 在 Dev 上没有可测信号时不扩大 RLVR；GRPO Smoke 出现全零奖励或系统性 Reward Hacking 时停止正式训练。

## 7. 结果记录模板

下表只能由实验产物自动回填，禁止填写估计数字：

| 组 | 模型/Revision | Train 样本 | Train Token | GPU Hours | Test 成功数/总数 | 成功率 | 越界修改率 | 平均 Token |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Base | 待实测 | - | - | - | 待实测 | 待实测 | 待实测 | 待实测 |
| Curated SFT | 待实测 | 待实测 | 待实测 | 待实测 | 待实测 | 待实测 | 待实测 | 待实测 |
| SFT + GRPO | 待实测 | 待实测 | 待实测 | 待实测 | 待实测 | 待实测 | 待实测 | 待实测 |

公开论文、数据集卡和其他项目的结果只能标记为 `reported baseline`，不得写入本项目 `measured result` 字段。

## 8. 里程碑

1. **B0 数据接入**：实现至少一种公开轨迹 Adapter、来源/许可证记录和跨源去重。
2. **B1 自采 Pilot**：完成 50 个 Dev Task 的小批量 Rollout，基于实测成功率与单 Episode 成本决定是否放量。
3. **B2 SFT Verified**：产出首个真实 1.5B LoRA Adapter 和 Base/SFT 联合报告。
4. **B3 Reward Verified**：Reward 在真实隔离仓库上通过隐藏测试、Patch Scope、测试篡改与安全回归测试。
5. **B4 RLVR Method Validation**：优先完成 1.5B SFT Checkpoint 的 Patch-level GRPO Smoke；显存不可行时才降级为独立 0.5B 分支，并记录稳定性、资源和失败证据。
6. **B5 Benchmark Verified**：冻结报告并回填简历可使用的真实数字。
7. **Deferred**：DataFlex 动态选样、完整交互式 Agent GRPO、多 Seed、3B/7B 与官方 SWE-bench 扩展。
