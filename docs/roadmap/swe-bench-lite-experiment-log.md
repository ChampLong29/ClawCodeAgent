# SWE-bench Lite 本地实验与修复日志

本文档记录真实仓库 Pilot 中观察到的失败、当时的判断、采取的修复、验证结果和
尚未解决的边界。它是工程实验日志，不是官方 SWE-bench 成绩，也不证明训练后
能力提升。Gold Patch、Test Patch、隐藏测试正文和原始模型轨迹不写入本文档。

## 2026-08-04：Marshmallow Rollout

### 失败现象

1. 首次 `deepseek-v4-flash` Episode 未获得历史 Python 环境，30 turns 内没有形成
   源码修改，FAIL_TO_PASS 失败。
2. 环境感知重试只修改 `src/marshmallow/schema.py`，1 条 FAIL_TO_PASS 和 24 条
   PASS_TO_PASS 全部通过，但在 20 turns 上限前没有返回最终响应。

### 判断与处理

- 测试通过只能证明候选满足当前选定回归，不能覆盖正常终止硬门槛。
- 候选比参考修复更窄，因此不因测试通过自动提升为 Gold。
- Collector 开始把历史解释器加入 PATH，并在 Episode 工作区上设置 PYTHONPATH。
- Agent Runtime 增加可选的临近预算收尾提醒。

### 结果

- 环境感知候选作为 Dev Bad Case 保存，不进入 Gold SFT。
- 版本化摘要：
  `configs/integrations/swe-bench-lite-marshmallow-deepseek-rollouts.json`。

## 2026-08-04：Astroid 校准与 Rollout

### 校准结果

- Python 3.8.20 隔离环境中，基线 2 条 FAIL_TO_PASS 失败、24 条 PASS_TO_PASS
  通过；参考补丁后两组均通过。
- 这只校准 Evaluator 状态转换，不是 Agent 成绩。

### 第一次 Rollout：基础设施失败

- Agent 修改了 `astroid/nodes/node_classes.py`，但 FAIL_TO_PASS 仍失败。
- 第 27 个工具轮次触发收尾提醒时，Trajectory Schema 拒绝新增的
  `runtime_guidance` 事件，运行以 `unsupported event_type` 中断。
- 该 Episode 不能用于评价模型质量，只保留为基础设施回归样本。

处理结果：

- 将 `runtime_guidance` 注册到 `agent_trajectory.v2` 事件集合。
- Replay 将该事件归入 `process`，而不是 termination/error。
- Runtime Adapter 端到端测试覆盖“提醒事件 → 正常终止 → Verifier 成功”。

### 第二次 Rollout：有效模型失败

- Schema 修复后按同一模型、温度和 30-turn 配置重试。
- 候选仍只修改 `astroid/nodes/node_classes.py`；24 条 PASS_TO_PASS 通过，2 条
  FAIL_TO_PASS 失败。
- 轨迹合法记录收尾提醒，但模型仍使用完 30 turns、38 次工具调用和 35,165
  tokens；Verifier 将其归类为 `budget_or_timeout`，并附带 test failure。

Reviewer 观察到的低效行为：

- 开始时假设仓库位于 `/workspace`，随后重新定位真实 Episode 路径。
- 多次检查 editable install、site-packages 和原始仓库映射。
- 实现发生过晚，收到收尾提醒后仍继续新的调查。

结论：有效重试只作为 Dev Bad Case；基础设施尝试和有效重试都不能进入 Gold。
版本化摘要：
`configs/integrations/swe-bench-lite-astroid-deepseek-rollouts.json`。

## 2026-08-04：路径、环境与收尾协议修复

### 根因

1. `get_user_context()` 已采集 cwd，但 `format_context_for_prompt()` 没有渲染它。
2. SWE-bench Collector 只把 `cwd/src` 放入 PYTHONPATH，不完整覆盖 Astroid 这类
   根目录包布局。
3. 历史 venv 的 editable metadata 指向原始 Benchmark checkout；即使 cwd 优先级
   可以加载 Episode 副本，也会诱发模型进行无效环境考古。
4. 单级提醒只在剩余 3 turns 时出现，对晚实现轨迹介入过迟。

### 已实施修复

- Environment Context 显示精确 `Working directory`。
- SWE-bench Prompt 明确所有工具已位于工作区，要求相对路径，禁止猜测
  `/workspace` 或切换 checkout。
- Benchmark Agent 文件工具启用 `restrict_workspace`，拒绝 cwd 外路径。
- Python 路径同时覆盖 `cwd/src` 与 `cwd`；Evaluator 使用相同布局策略。
- Collector 在创建 Agent 前导入主包，并证明模块文件位于 Episode 工作区；结果
  写入每个 collection 的 `environment-contract.json`。预检失败时同样先写入
  `failed`、异常类型和截断原因，再终止 Episode。
- 新增 `tools/probe_swe_bench_environment.py`，允许在不调用模型时复查环境契约。
- 收尾策略改为两级：默认剩余 8 turns 停止宽泛调查，剩余 3 turns 禁止开启新
  调查并要求完成最后一次修改/验证后返回。

### 确定性验证结果

- 针对性单元与集成测试：59/59 通过。
- 环境契约专项测试（含工作区外导入拒绝）：3/3 通过。
- Astroid 根目录布局预检：Python 3.8.20，从 `astroid/__init__.py` 导入，Passed。
- Marshmallow src 布局预检：Python 3.8.20，从
  `src/marshmallow/__init__.py` 导入，Passed。
- 最终完整回归：506/506 通过，耗时 108.131 秒。
- 回归过程中观察到既有 `test_training_pipeline_e2e.py` 使用裸 `open()` 产生的
  `ResourceWarning`；它没有导致测试失败，也不是本次修复引入，后续可单独清理。

## 剩余风险与后续验证

- `restrict_workspace` 当前覆盖结构化文件工具；Shell 命令的跨目录语义仍由现有
  Bash Security 与 Prompt 共同约束，尚未增加通用 Shell cwd 解析器。
- 旧历史 venv 的 editable metadata 仍存在，但新的导入预检会在模块不来自 Episode
  工作区时 fail closed。后续环境应改为只安装依赖，不 editable-install 原始仓库。
- 两级提醒改善介入时机，但尚无新的付费 Rollout 证明模型行为因此改善。
- 不再对 Astroid 重复调参；完整回归通过后，应换第三种 Issue 做一次泛化检查。
- 如果未来增加“耗尽工具预算后的只读最终响应”，必须继续把终止记作预算耗尽，
  不能把强制总结伪装成成功。
