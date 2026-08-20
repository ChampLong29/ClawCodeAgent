# ClawCodeAgent 面试证据摘要

> 用途：将项目实现、实验记录与简历叙事对齐。本文只陈述仓库中可追溯的事实；
> “已实现”“契约验证”“训练验证”“Benchmark 验证”的含义遵循根目录
> `AGENTS.md`。本文不是官方 SWE-bench 成绩单，也不替代实验报告。

## 1. 一分钟项目介绍

ClawCodeAgent 是一个面向软件工程任务的本地 Coding Agent Runtime。我负责将原有的
工具调用 Agent 扩展为可审计的数据与评测闭环：任务在独立 Episode 工作区中执行，模型、
工具、文件变更、测试和终止原因被写入不可变的 Trajectory v2；随后由测试、Diff、流程
规则和独立 Reviewer 生成 Verification，再以 Manifest 管理数据筛选、训练后端与独立
Benchmark 的血缘。

近期我用固定版本的 SWE-bench Lite 小型 Dev Pilot 做真实仓库验证，而不是只在玩具任务上
验证。实验中暴露出环境契约、模型输出截断、路径外临时编辑被误判为实现进度、以及编辑后
缺少兼容性检查等问题。我保留原始失败 Episode，新增确定性诊断和回归测试修复 Runtime
边界。现阶段的结论是“采集、验证和失败归因链路可用”，不是“模型已经增点”或“获得了
官方 SWE-bench 分数”。

## 2. 可用于简历的表述

- 设计并实现受控 Coding Agent Runtime：将多轮工具调用、权限、会话、预算与终止状态接入
  可追加的 Trajectory v2，实现 Episode 级工作区隔离、Checkpoint、回放与验证证据关联。
- 构建可审计的数据与评测闭环：以测试、Diff Scope、流程规则和独立 Reviewer 组成多信号
  Verifier；以 Dataset Manifest 完成筛选、去重、泄漏检查和 Tool-use SFT 数据导出，并预留
  PEFT 与 LlamaFactory 训练接口。
- 在固定版本 SWE-bench Lite Dev Pilot 上开展真实 Agent Rollout：完成历史 Python 环境与
  Oracle 校准、失败轨迹本地化、行为诊断和 Runtime 修复，定位“路径已定位但未及时实现”及
  “编辑后兼容契约遗漏”等可复现 Bad Case。

除非真实训练产物和独立 Benchmark 报告已经生成，不应在简历中写“训练后提升了 X%”、
“SWE-bench 得分为 X”或“完成 Agentic RL”。

## 3. 当前状态与证据边界

| 能力 | 当前状态 | 可出示证据 | 面试中应如何表述 |
|---|---|---|---|
| Agent Runtime、工具调用、会话与权限 | 已实现 | `src/claw/agent_runtime.py`、工具与 Runtime 测试 | 已实现受控的本地 Coding Agent 执行循环。 |
| Episode、Checkpoint、Trajectory v2、Replay | 已实现 | `src/claw/episode/`、`src/claw/trajectory/`、集成测试 | 执行事实与后处理评测分离，原始轨迹不被修复逻辑回写。 |
| Verifier、Bad Case、Dataset Manifest | 已实现 / 契约验证 | `src/claw/verification/`、`src/claw/dataset/`、Task Suite | 硬信号优先，Reviewer 不可覆盖测试或 Diff 等硬失败。 |
| DataFlow、DataFlex、LlamaFactory 对接 | 已实现接口与数据契约 / 部分契约验证 | 数据中心路线图、导出与训练后端实现 | 已建立可接入的数据治理和 SFT 接口；不等同于已完成真实后训练。 |
| 本地 SWE-bench Lite Dev Pilot | 本地校准完成；已有首个合规成功 Episode | `benchmarks/swe_bench_lite/`、`configs/integrations/`、实验日志 | 已证明本地链路可产出一条真实仓库成功；不是官方榜单或策略增点。 |
| LoRA/QLoRA 真实训练 | 计划中 | 低成本后训练路线图 | 训练契约和后端已具备，真实 Checkpoint 与 Base/SFT 对照仍待完成。 |
| 官方 SWE-bench Harness | 计划中 | Pilot 文档与路线图 | 尚未使用官方隔离 Harness，绝不把本地校准称作官方成绩。 |

## 4. 真实实验：讲什么，怎么讲

### 实验设计

- 固定公开数据版本、基准 Base Commit、任务选择规则、许可证元数据和仓库快照；本地工作副本
  不进入 Git。
- 先做准入校准：未修复基线必须让目标测试失败且回归测试通过，Oracle Patch 必须让两组通过；
  校准失败时先修环境或评测适配，而不评价模型。
- 将真实模型 Rollout 限定在 Dev Pilot。每次保留模型、提示词策略、工作区、Trajectory、
  Verification 和机器可读摘要；历史 Episode 不因后来修复而被改写。
- 使用目标路径、首次直接编辑轮次、编辑前工具调用比例、提示注入时机等确定性投影做 Bad Case
  分析，而不是仅依赖 Reviewer 主观判断。

### 已观察到的现象及修复

| 现象 | 根因判断 | 已完成的处理 | 当前结论 |
|---|---|---|---|
| 历史仓库测试无法启动或导入原始 checkout | 解释器、pytest 版本、包布局和 entry point 与任务版本不匹配 | 增加环境契约预检，固定隔离环境，校准基线/Oracle 状态转换 | 环境问题与模型失败被分离；本地校准不等同官方评测。 |
| 模型已定位目标路径但长时间调查 | 工具轨迹显示“定位→首次编辑”滞后或没有编辑 | 新增 Deadline、Escalation 和行为诊断；策略均可开关并记录为 `runtime_guidance` | 这是小样本 Dev 观察，不能推断策略有效或模型整体能力。 |
| 编辑后通过目标用例但破坏容器/返回类型语义 | 模型仅检查局部数值条件，未验证相邻兼容契约 | 新增一次性 Post-edit Contract Notice，提示最小目标验证与相关回归 | 规则实现已由确定性测试覆盖，仍需全新任务上的受控验证。 |
| 临时复现脚本被误当成“已实现” | 旧逻辑把任意显式文件编辑都当作直接实现变更 | Collector 传入 Oracle 派生的允许路径；仅实现路径编辑可解除 Deadline 或触发 Post-edit 提示 | 路径范围语义已修复，临时编辑不再污染实现进度信号。 |
| 供应商返回 `max_tokens` 却被记录为 completed | 客户端没有保留 finish reason，且事件 Schema 漏注册 `runtime_stop` | 保留两类客户端的 finish reason；无工具截断显式停止；注册并测试 `runtime_stop` | 原始 Episode 保持基础设施污染标记；修复由集成测试验证。 |
| 候选通过测试但持续调用工具、无法合规终止 | 提示级提醒不能约束 DeepSeek 长思考和工具选择 | 增加显式 Thinking Mode、Escalation 直接编辑约束和 Critical 最终响应约束 | Marshmallow 重复 Dev 题首次完整成功；强制约束未触发，不能宣称独立因果效果。 |
| 新 Issue 候选避免异常且回归通过，但目标语义失败 | 模型把立即父对象当配置所有者，未沿 `root` 父链读取根 Schema 的非默认配置 | 增强嵌套回退契约；提前 Escalation 并记录真实强制编辑；保留两次失败 | 约束能改变动作时机，但未修复语义理解；该题停止重采样并作为 Bad Case。 |
| 探索性失败后容易继续拟合同一 Dev 题 | 反复看结果再改 Prompt 会造成 Benchmark Overfitting | 在仓库获取和模型调用前哈希固定 PyVista 新 Family 的 Control/Treatment、预算、停止规则和解释矩阵 | 两臂均通过目标、114 条回归和硬门槛；证明可复现成功，不证明 Notice 增点。 |
| 强制动作能阻止继续调查，但可能过早 | SQLFluff 在 Escalation 后首次定位正确文件，仍按 read-before-edit 请求读取 | Runtime 合规停止；行为诊断 v4 记录被拒绝请求，Bad Case 将动作约束违规与基础设施失败分离 | 冻结多任务复现为 1/2；证明约束存在可观测的效率/成功率权衡。 |

详细时间线在 [`swe-bench-lite-experiment-log.md`](swe-bench-lite-experiment-log.md)，机器可读
摘要在 `configs/integrations/`。面试时应主动说明：候选测试通过和合规终止是两道独立硬门槛；
两者均通过才可能进入 Gold SFT 数据。

## 5. 高频问答

### Q1：这和调用一个大模型加几个工具有什么本质区别？

核心差异是把“能跑”变成“可验证、可复盘”。每个 Episode 有版本化任务、独立工作区、
轨迹、测试、Diff 和终止证据；训练数据和实验报告通过 Manifest 反向追溯到这些事实。
因此失败不是一段聊天记录，而是可分类、可重放、可用于下一轮 Runtime 改进的样本。

### Q2：为什么要把 Trajectory 设计成不可变？

原始轨迹记录的是当时真正发生的事件。后续发现评测或运行时缺陷时，只能新增诊断、补充复评或
版本化修复，不能修改旧事件把失败伪装成成功。这样才能区分模型问题、环境问题和评测问题，也让
训练数据准入可审计。

### Q3：真实仓库 Benchmark 最难的部分是什么？

不是调用模型，而是环境和证据边界。历史项目常对 Python、pytest、entry point 与包布局敏感；
若基线/Oracle 状态没有校准，任何模型分数都没有解释力。我的流程先验证“基线失败、Oracle
通过”，再运行 Agent，并把评测基础设施错误独立标记。

### Q4：Reviewer 会不会掩盖测试失败？

不会。Verifier 把测试、Diff Scope、权限和终止合规作为硬门槛；Reviewer 只提供正确性、覆盖度、
可维护性等软信号。硬失败时 Reviewer 高分只能用于分析，不能把样本标成成功或 Gold 数据。

### Q5：你如何判断一条轨迹是否能用于训练？

先验证 Schema 与工具调用配对，再检查测试、Diff、终止和数据泄漏；然后按 raw、success-only、
verifier-filtered 三种策略生成带内容哈希和来源的 Manifest。真实 Rollout 的失败轨迹会保留用于
Bad Case 和 Reward 设计，但不会被误标为 Gold SFT。

### Q6：DataFlow、DataFlex 和 LlamaFactory 在项目中的位置？

DataFlow 用于离线的对齐、去重、泄漏检查、打分和数据配比；LlamaFactory 用于承接工具调用格式的
SFT 导出和实际 LoRA/QLoRA 训练。DataFlex 更适合训练稳定后再做动态数据选择；现在将它列为后续
扩展，避免在尚无稳定基线时过早增加系统复杂度。

### Q7：为什么不直接宣称 Deadline/Escalation 有效果？

Marshmallow-1359 的 v2 确实触发了只允许直接编辑的请求，但候选仍因父链配置语义遗漏失败。
随后预注册 PyVista 对照，两臂都成功，因此同样无法得到 Notice 的正确性增益。现阶段只陈述
“约束可执行、冻结流程可在新仓库成功”，而不是性能提升；多任务/Seed 与官方 Harness 仍缺失。

### Q8：实验失败时你做了什么？

先确定失败发生在模型、环境、工具、评测还是轨迹 Schema；保留原始 Episode；再做最小修复并添加
回归测试。例如模型输出截断事件缺失时，我修复 finish reason 传递和 Schema 注册，但没有重写
历史轨迹或为同一行为重复付费调用模型。

### Q9：现在已经有成功结果了吗？

有一条。关闭 DeepSeek 显式思考并启用有界动作策略后，Marshmallow-1343 同时通过目标测试、
24 条回归、Diff Scope、流程、格式和正常终止门槛。但它是重复使用的 Dev 校准题，且模型在
Escalation 强制编辑与 Critical 强制收尾前主动完成，所以它最初只证明“链路可以产出合规
成功”。随后 Marshmallow-1359 两次失败，PyVista-4315 则在预注册的 Control/Treatment 中
双双通过目标、114 条回归和全部硬门槛。这进一步说明项目已经能在新仓库 Family 复现成功，
但由于两臂都成功，仍不能把成功归因于 Post-edit Notice。

### Q10：下一步怎样证明项目真的有效？

当前已完成一次单任务预注册对照，以及两条未见 Issue 的冻结复现（1/2 成功）。下一步在新
任务/Seed 上比较“立即强制编辑”与“允许一次目标读取后必须编辑”，估计成功率、成本和终止
回答质量分布；随后用许可清晰的公开轨迹
和自采成功样本构建 Manifest，完成一次真实小模型 LoRA/QLoRA，并以同一 Test Manifest 比较 Base
与 Curated SFT。只有产出真实 Checkpoint 和独立报告后，才可以报告增益；官方 SWE-bench 结果还
需要单独通过官方 Harness。

## 6. 面试演示建议

优先演示一条已完成的、无密钥依赖的链路：版本化任务 -> Episode -> Trajectory -> Verification ->
Dataset Manifest。再打开实验日志展示一个“发现问题—保留事实—最小修复—回归验证”的真实案例。
不要现场调用付费模型或试图复现历史外部仓库环境；网络、模型随机性和依赖可用性会削弱演示重点。

可提前准备三个问题的证据入口：

- Runtime 控制：`src/claw/agent_runtime.py` 与 `tests/test_agent_runtime.py`。
- 数据/验证闭环：`src/claw/episode/`、`src/claw/trajectory/`、`src/claw/verification/`、
  `src/claw/dataset/`。
- 真实实验复盘：`benchmarks/swe_bench_lite/README.md`、实验日志与对应的
  `configs/integrations/` 摘要。
