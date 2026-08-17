# 数据中心化 Agent 训练实施路线图

状态：**进行中**。个人预算下的当前执行顺序以
[`budget-constrained-agent-posttraining-roadmap.md`](budget-constrained-agent-posttraining-roadmap.md)
为准；本文件保留完整 DataFlow/DataFlex 扩展路线。M1 已完成，M2 已形成 Fixture、真实小批次和历史仓库
Dev Rollout 证据；路径定位/首次编辑诊断、无编辑 Deadline 和延迟 Escalation Guidance 已实现，
当前主线已完成 pydicom Deadline 与 pvlib Escalation 新鲜 Episode；pvlib 在二级提示后
下一轮编辑并正常终止，但候选破坏 Series 返回类型并失败目标/回归测试。Post-edit Contract Notice
已实现并通过确定性测试，因此先做新鲜验证，再扩充多 Family 数据并建立
Raw/DataFlow 静态 LoRA 对照。
依赖设计：[数据中心化 Agent 训练集成架构设计](../architecture/data-centric-agent-training-design.md)

## 总目标

形成以下闭环，并能够用独立证据解释数据治理对模型能力的贡献：

```text
Claw Rollout
  -> Agent 数据契约
  -> DataFlow 治理
  -> LLaMA-Factory / DataFlex 训练
  -> Claw Benchmark
  -> 数据与模型联合报告
```

## M0：接口与环境预研

状态：**基本完成**。已固定 DataFlow/DataFlex Commit、LLaMAFactory 最低兼容版本与三环境边界；5 条固定 Fixture 和 DataFlow 最小 CPU 环境已验证，Adapter 统一错误协议仍待补充。

目标：固定外部依赖边界，不改动现有训练主链路。

交付物：

- 固定 DataFlow、DataFlex、LLaMA-Factory 版本或 Commit。
- 三套隔离环境说明：Claw、DataFlow、Training。
- 5–10 条 Silver 样本的脱敏 Fixture。
- ShareGPT Tool-use 格式验证样例。
- Adapter 失败、超时、版本不匹配的错误协议。

完成标准：

- 外部框架可以独立安装并输出版本。
- Fixture 不包含 Test、Oracle、隐藏测试或凭据。
- 现有完整测试不受外部可选依赖影响。

## M1：AgentTrainingRecord 与数据分层

状态：**完成**。Schema、Silver Converter、稳定 Record/Manifest Hash、Test Split 与破损 Tool 序列拒绝测试、固定脱敏 Fixture 和数据画像/治理报告均已落地；Archived Episode 真实产物装配入口也已实现。

目标：建立 Bronze/Silver/Gold 中间契约。

交付物：

- `agent_training_record.v1` Schema。
- Trajectory + Verification → Silver Converter。
- 稳定 Record ID 和 Manifest Hash。
- Test Split、泄漏和破损 Tool 序列拒绝测试。
- Silver 数据画像命令。
- Archived Episode → DatasetRecord/Silver 装配与血缘校验。

完成标准：

- 同一输入可确定性重建同一 Manifest。
- 每条 Silver 记录可追溯到 Task、Episode、Trajectory 和 Verification。
- 不引入外部训练依赖。

## M2：DataFlow 确定性治理 Pipeline

状态：**Fixture E2E、真实两样本 Contract Batch、五仓库七任务 Dev 验证已完成**。七类确定性职责、`agent_sft_v1`、Gold Manifest、排除报告、分布/成本报告、Data Card 与 LlamaFactory 输出已经实现。SWE-bench Lite 已接通安全输入、验证期私有资产挂载、候选临时副本评测和 Dev Episode；Bad Case 已投影路径定位、首次编辑、编辑前探索和 Guidance 后调用指标。pvlib 候选测试失败；pydicom-1413 暴露临时脚本误触发实现提示，Astroid-1333 又显示长思考耗尽单轮输出且暴露 `runtime_stop` Schema 缺口。缺口已修复，但都不能作为有效性或增点结论；下一步先设计有界思考/动作预算，再换新 Issue 验证路径限定的 Post-edit Contract Notice。

目标：先实现无需 LLM 的可复现数据治理。

第一批 Operator：

1. `ClawRecordReader`
2. `ToolAlignmentValidator`
3. `LeakageGuardOperator`
4. `FailureTaxonomyAnnotator`
5. `TrajectoryQualityScorer`
6. `DomainDifficultyBalancer`
7. `LlamaFactoryExporter`

交付物：

- `agent_sft_v1` DataFlow Pipeline。
- Operator 单测和 Pipeline E2E 测试。
- 数据保留率、去重率、泄漏率、分布和成本报告。
- Gold Manifest 与 Data Card。

完成标准：

- Pipeline 可对最小 Fixture 和真实少量 Episode 重跑。
- 所有排除样本都有机器可读原因。
- Gold 数据可追溯到 Silver/Bronze。

## M3：LLaMA-Factory 静态 SFT 基线

状态：**原生 Tokenization 已完成，真实 LoRA 待实现**。固定 Qwen2.5-Coder
tokenizer revision 已在 LlamaFactory 0.9.4 中成功处理 2 条 Gold Tool-use
样本，得到 1713/1542 tokens、505/393 个监督 tokens、0 截断；尚未加载权重或训练 Adapter。

目标：验证 Agent Tool-use 数据能够被标准训练生态正确消费。

实验：

- Base：不训练。
- Raw SFT：合规 Silver 样本。
- DataFlow SFT：治理后的 Gold 样本。

交付物：

- ShareGPT 与 `dataset_info.json` Exporter。
- 目标模型 Chat Template Tokenization Dry-run。
- 固定 LoRA YAML。
- 真实 Adapter、训练日志、依赖和硬件证据。
- 三组相同 Test Manifest 的 Benchmark 报告。

完成标准：

- Tool Call 与 Observation 在 Tokenization 后保持完整顺序。
- ExperimentRegistry 标记真实 `training_verified=true`。
- 报告能区分“增加数据”和“治理数据”的效果。

## M4：DataFlex 动态训练（Deferred）

目标：在不改变候选数据池的前提下验证动态数据策略。

第一阶段二选一：

- 基于质量分的 Sample Reweighting。
- 固定候选池的 Dynamic Selection。

交付物：

- DataFlex Exporter 和 YAML。
- 选择/权重日志与统计。
- Resume 和失败恢复证据。
- 与 LLaMA-Factory 静态 Gold SFT 的等预算对比。

完成标准：

- 使用相同 Base Model、Gold Pool、Seed 集合和 Test Manifest。
- 动态策略的实际选样/权重可审计。
- 不根据 Test 结果调整数据策略。

## M5：规模化数据飞轮

目标：扩充真实任务和 Episode，使结论从接口验证升级为可重复实验。

交付物：

- 更多不同 Family、Domain、Difficulty 的任务。
- 多模型或多 Seed Rollout。
- 可选 LLM Refiner 与派生样本再验证。
- DataFlow WebUI 或数据质量 Dashboard。
- Base/Raw/DataFlow/DataFlex 四组联合报告。

完成标准：

- 数据规模足以报告分 Slice 指标和置信区间。
- 训练、数据和 Benchmark 成本均可量化。
- 坏例可以反馈到任务生成、Operator 和采样策略。

## M6：AgentFlow 分支轨迹探索（可选研究项）

状态：**Deferred**。M2 两样本数据契约验证和 M3 Tokenization Dry-run 已完成，
但仍优先建立多 Family 静态 LoRA 基线；本项不作为 DataFlow/DataFlex/LlamaFactory
主线的前置依赖。

目标：评估 AgentFlow 的分支采样策略是否能在不削弱 Claw 可验证性和环境隔离的
前提下，产生更有价值的代码轨迹及 chosen/rejected 数据。

最小交付物：

- 可选 `ClawAgentFlowBackend`，不引入核心依赖。
- `reset/execute/snapshot/fork/restore/verify/export` 状态协议。
- 每分支独立 worktree 或 sandbox snapshot。
- 基于 `(state_hash, normalized_action)` 的状态感知去重。
- 1 个中等任务、2×2 分支树的可复现实验报告。

完成标准：

- 并发分支交叉污染为零。
- Claw Verifier 仍是正确性硬门槛。
- 相比线性 rollout 的成功、成本、路径多样性可以独立比较。
- 产生至少一组通过 Schema、泄漏和血缘检查的 chosen/rejected 记录。

## 优先级

```text
完成 AgentTrainingRecord + ShareGPT Exporter
完成 Tool 对齐、工作区脱敏和两样本 Tokenization 验证
完成 DataFlow 确定性治理核心与 Operator 包装
P1  Raw SFT / DataFlow SFT 小模型对比
P2  DataFlex 单一动态策略
P2  数据质量 Dashboard
P3  LLM Refiner 与多策略动态训练
Deferred  AgentFlow 分支轨迹探索（M2/M3 门槛后）
```

## 当前推荐执行序列

以下顺序是当前主线的执行真源。除非前一步暴露必须先修复的阻塞问题，否则不并行
引入新的训练框架或复杂策略。

1. **已完成——Marshmallow 修复后 Rollout**：固定历史环境后候选通过选定测试，
   但仍因连接中断或 Max turns 未通过正常终止硬门槛，失败轨迹已保存。
2. **已完成——第三至第五种真实 Issue 与行为诊断**：SQLFluff、pydicom、pvlib 已完成校准
   和有效 Rollout；确定性投影显示样本都较早到达目标路径，但从定位转入实现过晚
   或完全没有发生。Implementation Deadline 在 pydicom 第 8 轮触发，但模型仍到
   第 19 轮才编辑；pvlib 在 Escalation 后下一轮编辑但测试失败，策略尚未验证有效。
3. **首批多 Family 训练数据**：先生成 20–50 条 Train/Dev Episode，覆盖至少
   5 个 Family；pydicom-1413 已证明临时复现脚本不能作为实现编辑信号，路径限定已完成，
   放量前需换一个新 Issue 验证编辑后的容器/返回类型契约提示，
   同时保留成功、失败和不同轨迹长度的样本。Test Episode 禁止进入训练数据。
4. **冻结 Raw/DataFlow 数据版本**：从同一 Silver 候选池生成 Raw 和 Gold 数据，
   固定 Manifest、去重/泄漏报告、Operator 统计和 Tokenization 证据。
5. **最小静态 LoRA 对照**：固定 Base Model Revision、Chat Template、训练预算、
   Seed 和 Test Manifest，运行 Base、Raw SFT、DataFlow SFT 三组。
6. **独立 Benchmark 与联合报告**：扩充 Medium Test，并在条件允许时接入官方
   SWE-bench Docker Harness；报告数据指标、模型指标、成本和失败 Slice。
7. **DataFlex 单策略实验**：静态基线稳定后，只引入一种基于质量分的重加权或
   动态选择策略，保持候选池和训练预算不变。
8. **数据质量 Dashboard**：展示 Silver → Gold 血缘、排除原因、分布、成本以及
   Base/Raw/DataFlow/DataFlex 对比。
9. **AgentFlow Spike**：完成静态训练与 Benchmark 基线后，再执行 1 个中等任务、
   `branching_factor=2`、`depth=2` 的隔离分支实验。

阶段性完成定义是获得一份可重建的 Base/Raw/DataFlow 对照报告，而不是仅生成
Adapter 或通过数据 Dry-run。在真实 Adapter、Checkpoint 和独立 Benchmark 报告
同时存在前，项目仍只可表述为“数据与训练闭环正在搭建”。

## 每个里程碑的证据等级

| 里程碑 | 可对外描述 |
|---|---|
| M0 | 完成生态预研与接口设计 |
| M1 | 实现可追溯 Agent 训练数据契约 |
| M2 | 实现 DataFlow 数据治理 Pipeline |
| M3 | 完成真实 LLaMA-Factory LoRA 闭环与静态消融 |
| M4 | 完成 DataFlex 动态数据策略实验 |
| M5 | 建立可迭代的数据飞轮和规模化评测 |
| M6 | 完成可复现的 AgentFlow 隔离分支轨迹实验 |

未达到对应里程碑时，不使用更高等级表述。

## 建议的首个迭代

首个实现迭代控制在小范围：

1. 已从 Train Split 生成 1 个真实成功 Episode 并完成 Silver/Gold；下一步扩展为少量成功/失败、多 Family Episode。现有 Benchmark Test Episode 只用于验证装配器和评测，禁止进入训练集。
2. 已实现 AgentTrainingRecord Converter。
3. 已实现 ShareGPT Tool-use Exporter，并固化 5 条 Fixture。
4. 已编写 Test Split、Tool 顺序和确定性测试；待补充跨 Family 泄漏 Fixture。
5. 已用真实 Episode 构建 Silver/Gold，并完成 LlamaFactory 数据加载与 Tokenization Dry-run；下一步扩充 Train/Dev 小批次，不立即宣称训练效果。
6. 已完成 SWE-bench Lite 五仓库七任务校准、Dev Episode 和行为诊断 v3；当前候选均不满足 Gold 硬门槛。路径限定提示与 `runtime_stop` 已完成确定性/集成验证，但连续两题没有实现路径编辑；先加入有界思考或动作预算实验，再扩充多 Family Episode。

这一步不需要 GPU，但可以验证最关键的数据契约，为后续 DataFlow 和真实训练消除最大的不确定性。
