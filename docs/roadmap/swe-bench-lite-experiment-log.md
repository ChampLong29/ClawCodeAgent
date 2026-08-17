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
- 两级提醒已经过新的付费 Rollout，但候选虽通过选定测试，仍未满足正常终止硬门槛。
- 不再对 Astroid 重复调参；完整回归通过后，应换第三种 Issue 做一次泛化检查。
- 如果未来增加“耗尽工具预算后的只读最终响应”，必须继续把终止记作预算耗尽，
  不能把强制总结伪装成成功。

## 2026-08-12：Marshmallow 修复后重跑

### 尝试 v4：环境契约假阳性

- Agent 修改了允许范围内的 `src/marshmallow/schema.py`，并记录两级收尾提醒，
  但最终 FAIL_TO_PASS/PASS_TO_PASS 都在约 0.04 秒内失败。
- 原因不是候选逻辑：传入的是 UV Python 3.8.20 基础解释器，主包导入预检通过，
  但解释器没有安装 pytest。旧 `swe_bench_environment_contract.v1` 只证明包来自
  Episode 工作区，不能证明评测命令可启动。
- 该尝试属于基础设施失败，不用于评价模型质量。

处理结果：

- 环境契约升级为 v2，在创建 Agent 前同时导入工作区主包和必需的评测模块。
- 在忽略目录创建固定 Python 3.8.20 环境，固定 pytest 8.3.5 及仓库测试依赖。
- 新契约会拒绝缺 pytest 的基础解释器，并接受固定虚拟环境。
- 使用固定环境重新执行基线/参考补丁校准，状态恢复为 Passed。

### 尝试 v5：候选测试通过，API 传输中断

- 环境契约 v2 通过；候选只修改允许文件，1 条 FAIL_TO_PASS 和 24 条
  PASS_TO_PASS 全部通过。
- 第 15 个模型调用发生 `Remote end closed connection without response`；Runtime
  将其作为未知异常直接终止，导致 termination compliance 失败。
- 该尝试证明候选满足选定测试，但不能记为合规成功，也不能进入 Gold。

处理结果：

- `_retry_call()` 增加对连接重置、超时和无状态码连接错误的窄范围指数退避重试。
- 重试只包围模型请求，发生在模型返回新的 Tool Call 之前，不会重放已经执行的
  文件或 Shell 工具副作用。

### 尝试 v6：有效质量失败

- 固定环境和传输重试均生效，没有再次出现基础设施中断。
- 候选只修改 `src/marshmallow/schema.py`，1 条 FAIL_TO_PASS 和 24 条
  PASS_TO_PASS 全部通过，Diff Scope 与权限检查通过。
- 模型收到剩余 8 turns 和 3 turns 的两级提醒后仍继续调用工具，最终耗尽 20
  turns、26 次工具调用；termination compliance 失败。

结论：运行时和环境修复已经得到真实重跑验证，但两级提醒尚不足以保证模型正常
收尾。停止继续重复 Marshmallow，三个尝试都只保留作 Dev Bad Case；下一步按
路线图选择第三种真实 Issue 做泛化检查。机器可读摘要见
`configs/integrations/swe-bench-lite-marshmallow-remediation-rollouts.json`。

## 2026-08-12：第三种 Issue——SQLFluff 1763 环境校准

### 选择理由

- `sqlfluff__sqlfluff-1763` 是 Unicode 文件写回与回滚安全问题，不同于 Marshmallow
  的反序列化校验和 Astroid 的静态推断。
- 参考变更只涉及 `src/sqlfluff/core/linter/linted_file.py`，3 条 FAIL_TO_PASS
  覆盖安全替换、清理与回滚，63 条 PASS_TO_PASS 提供较强回归约束。

### 失败、判断与处理

1. 安装全量历史开发依赖时，`types-pkg-resources` 的全部可用版本已被上游撤回。
   改为安装版本化运行依赖和最小测试依赖，不引入 lint、文档和 MyPy 工具。
2. 首次环境预检因 `pkg_resources` 缺失失败。固定 setuptools 75.3.2 后，主包和
   pytest 导入通过。
3. 首次校准中，官方快照的一条参数化 PASS_TO_PASS Node ID 以未闭合的
   `[` 结尾，导致 pytest 对整组返回 usage error。Evaluator 现在只对这种明显
   截断的 ID 做保守规范化：运行对应测试函数的全部参数组合，并记录规范化计数。
4. 规范化后唯一回归失败为 SQLFluff 插件 Hook 未注册。根因是源码 PYTHONPATH
   没有 Distribution Entry Point 元数据；将相同 Base Commit 非 editable 安装到
   隔离环境后，环境契约仍证明实际模块来自 Episode 工作区。

### 最终结果

- Python 3.8.20、pytest 8.3.5 环境契约 v2 通过。
- 基线：3 条 FAIL_TO_PASS 失败，PASS_TO_PASS 通过。
- 参考补丁：FAIL_TO_PASS 与 PASS_TO_PASS 均通过。
- 该结果只完成本地兼容校准，不是 Agent 成绩，也不是官方 SWE-bench 分数。

机器可读证据：
`configs/integrations/swe-bench-lite-sqlfluff-local-calibration.json`。

## 2026-08-12：SQLFluff 1763 泛化 Rollout

### 第一次尝试：Episode 复制失败

- SQLFluff 的忽略规则 Fixture 包含指向当前目录的相对符号链接。
- Episode `shutil.copytree()` 默认跟随链接，形成递归路径并以
  `Too many levels of symbolic links` 失败；模型调用数为 0。
- 修复后 Episode 与候选临时评测复制都会保留安全相对链接，并在复制前拒绝绝对
  链接或词法上逃逸工作区的链接。Windows 因系统权限跳过链接测试，WSL 两项安全
  测试均通过。

### 第二次尝试：有效模型失败

- 环境契约 v2 通过，Agent 收到剩余 10 turns 和 4 turns 的两级提醒。
- 模型使用 30 turns、41 次工具调用和 28,612 tokens，主要调查 CLI 编码、
  Formatter、dbt 和输出路径，没有修改任何源码，最终耗尽 turns。
- 原始 Verifier 的候选临时复制仍跟随同一链接，因此测试信号标记为
  `evaluation_error`；修复评测复制后，对不可变候选做离线复评：63 条
  PASS_TO_PASS 通过，3 条 FAIL_TO_PASS 失败，与未修改基线一致。

结论：该 Episode 是有效的路径定位/实现时机 Bad Case，但原始测试信号包含一个
已修复的评测基础设施错误。补充复评单独记录，未覆盖原始 Verification。模型在
第三类文件安全 Issue 上仍表现出调查过宽、实现过晚和提醒后不收尾的问题，因此
下一步应先提炼路径定位与实施时机信号，再扩充真实 Episode，而不是继续重复同一
Issue。机器可读摘要见
`configs/integrations/swe-bench-lite-sqlfluff-deepseek-rollout.json`。

## 2026-08-12：路径定位与实施时机诊断

为避免把 Reviewer 的主观“探索过宽”直接当作训练标签，本轮新增
`rollout_behavior_diagnostics.v2` 确定性投影。它从 Trajectory v2 计算目标路径首次
出现轮次、首次直接编辑轮次、编辑前工具调用比例、目标/非目标路径检查、失败工具、
收尾提醒时机和 Critical 后调用数。`direct_mutation` 只表示显式 `write_file` /
`edit_file` 等工具，不能据此断言 Shell 没有副作用。

离线重算结果：

- Marshmallow v6：第 4 轮到达目标路径，第 13 轮首次编辑，二者相隔 9 轮；首次
  编辑前使用 18/26 次工具调用，占 69.2%。Completion Reminder 在第 12 轮出现，
  Critical 后仍执行 3 次工具调用。
- SQLFluff v2：第 5 轮已到达 Oracle 文件，但 30 轮内没有直接编辑；41/41 次工具
  调用都发生在“尚未编辑”阶段，21 次结构化路径检查中 15 次落在目标文件之外，
  Critical 后仍执行 6 次工具调用。

因此当前两样本支持的结论是“定位到实现的转换较慢或缺失”，而不是“模型普遍无法
定位目标文件”。样本数不足以评价整体模型质量。机器证据见
`configs/integrations/swe-bench-lite-rollout-behavior-diagnostics.json`，旧轨迹可用
`tools/analyze_rollout_behavior.py` 重算。

基于该证据，SWE-bench Collector 增加默认第 12 个无成功直接编辑工具轮次触发的
Implementation Deadline Guidance；若已进入原有 Completion Reminder 区间则不重复
注入。该策略要求模型在已知路径时立即做最小实现，未知时只允许用下一轮验证一个
具体假设。它目前仅通过确定性回归测试，必须在新鲜 Dev Episode 上比较首次编辑
时机和硬门槛结果后，才能声称有效。

## 2026-08-12：pydicom 新鲜 Issue 与 Deadline 验证

### Pilot 扩展与仓库获取失败

- 将原备用项 `pydicom__pydicom-1139` 提升为第四条 Dev Pilot。它是纯 Python
  PersonName 迭代/包含协议问题，Oracle 只改 `pydicom/valuerep.py`，包含 3 条
  FAIL_TO_PASS 和 38 条 PASS_TO_PASS。
- 首次 `--filter=blob:none` Clone 在展开历史 Blob 时网络中断，留下错误 HEAD 和
  Promisor 缺失对象。该目录被移动到忽略的 `.port_sessions/failed-downloads`，没有
  进入 Snapshot 证据。
- 改为初始化空仓库并只 Fetch 精确 Base Commit，最终 HEAD、Clean 状态、394 个
  Tracked File、License Hash 均写入 `repo-snapshots.json`。

### 校准 v1：pytest 版本不兼容

- Python 3.8.20 + pytest 8.3.5 下，基线和 Oracle 的 38 条 PASS_TO_PASS 均有 2 条
  失败；诊断运行显示其余 36 条通过。
- 失败测试类使用历史 `setup()/teardown()` xUnit Hook，pytest 8 未调用它们，导致
  Fixture 属性不存在。目标逻辑和 Oracle 不是失败原因。
- 将隔离环境固定到 pytest 5.4.3 后，基线 3 条 FAIL_TO_PASS 失败、38 条
  PASS_TO_PASS 通过；Oracle 后两组全部通过，达到 Rollout 准入条件。

### Deadline Rollout：正常终止但实现不完整

- 配置为 24 turns，未编辑第 8 轮触发 Implementation Deadline，剩余 6/2 turns
  触发 Completion Reminder/Critical。
- 模型第 3 轮已定位 `pydicom/valuerep.py`，第 8 轮收到 Deadline，但到第 19 轮才
  首次编辑；编辑前使用 20/23 次工具调用，占 87.0%。第 18 轮收到 Reminder，最终
  第 22 轮正常终止，未进入 Critical 区间。
- 候选只增加 `PersonName.__iter__ -> iter(str(self))`，Diff Scope 合规。
- 原始 Verifier 从 Episode cwd 启动工具脚本时，调用方的相对 `PYTHONPATH=src`
  指向了 Episode 源码而非 Claw，Evaluator 因 `ModuleNotFoundError: claw` 失败。
  原始 Verification 保留不变。
- Evaluator 现在根据自身路径加入 Claw `src`，并用“临时 cwd + 删除 PYTHONPATH +
  `--help`”回归测试覆盖。对不可变候选补充复评：38 条 PASS_TO_PASS 全部通过，
  FAIL_TO_PASS 组仍失败。候选支持普通迭代和 `in`，但没有完整实现隐藏测试要求的
  `next()`/迭代状态协议。

结论：Deadline 被可靠注入，但模型仍在定位后延迟 16 轮才编辑；没有同任务对照，
不能把正常终止归因于该策略，也不能声称策略有效。该 Episode 只作为 Dev Bad Case，
不进入 Gold。下一步先强化“定位后收敛”策略，再在不同新 Issue 上验证，暂不重复
pydicom。机器证据见：
`configs/integrations/swe-bench-lite-pydicom-local-calibration.json` 和
`configs/integrations/swe-bench-lite-pydicom-deadline-rollout.json`。

## 2026-08-12：Deadline 后二级收敛提示

pydicom Episode 表明单次 Deadline 可以可靠记录，但模型仍继续探索 11 个模型轮次后才编辑。
因此 Collector 新增 `implementation_escalation_turns`，默认在 Deadline 后再经过 4 个工具轮次且
仍无成功显式文件编辑时注入一次 Implementation Escalation。提示要求下一工具响应直接做最小修改，
或只运行一个能区分两种具体实现选择的聚焦复现后立即编辑；它不强制屏蔽工具。

确定性测试覆盖：忽略 Deadline 后只触发一次 Escalation、尚未达到延迟阈值时不提前触发、成功
`write_file` 后抑制 Escalation，以及采集 API 拒绝“启用 Escalation 但关闭 Deadline”的无效配置。
行为诊断升级为 `rollout_behavior_diagnostics.v2`，新增升级提示轮次及 Benchmark 聚合触发率。

当前证据级别仅为 **Implemented / Contract verified**。本轮没有再次调用付费模型，也没有把
pydicom 的历史结果改写成新策略结果；下一步应换一个新鲜 Dev Issue 做一次受控验证，再决定是否
开始 5 Family 批量采集。

## 2026-08-12：pvlib 第五 Family 与 Escalation 新鲜验证

选择 `pvlib__pvlib-python-1707` 作为第五个 Family：Oracle 仅涉及 `pvlib/iam.py`，目标是
`n=1, L=0, aoi>90` 的数值边界，包含 1 条 FAIL_TO_PASS 和 30 条 PASS_TO_PASS。
Windows Git 首次 Fetch 因 Schannel `SEC_E_INVALID_TOKEN` 失败；WSL 默认 GnuTLS 重试又因
异常 TLS Packet 失败；固定 HTTP/1.1 后成功浅 Fetch 精确 Base Commit。两次失败均未形成工作树。

首次校准中，目标测试状态转换正确，但基线和 Oracle 的回归组都失败。诊断整份 `test_iam.py`
发现 31 项中 30 项通过，唯一错误是缺少 `pytest-mock` 提供的 `mocker` fixture。固定安装
`pytest-mock==3.14.0` 后，校准 v2 达到“基线目标失败/30 回归通过、Oracle 两组通过”。

受控 Rollout 使用 24 turns、Deadline=8、Escalation delay=4、Reminder/Critical=6/2。
模型第 1 轮定位目标文件，第 8 轮收到 Deadline，第 12 轮收到 Escalation，第 13 轮首次编辑，
第 19 轮正常终止；共 19 次工具调用、18,489 tokens。候选使用 `numpy.where` 将
`|aoi| >= 90` 置零，但把 pandas Series 输入的返回值变成 ndarray。原始和补充评测一致：
FAIL_TO_PASS 失败，PASS_TO_PASS 中 `test_physical` 因 Series 返回类型丢失而失败。

原 Episode 的环境预检还暴露一个独立问题：仓库 Slug `pvlib-python` 被推断成不存在的
`pvlib_python`，导致契约标记为 skipped。推断器现会剥离常见 `_python`/`_py` 后缀；对同一
不可变候选的只读探针确认 Python 3.8.20、pytest 7.4.4，并从工作区 `pvlib/__init__.py` 导入。
原始 Episode 证据保持不变。

完整项目回归首次运行有 1 项失败：Adapter 测试仍把 Pilot 数量硬编码为 4，而版本化
选择已扩展到 5。该失败不是运行时功能缺陷；断言已改为读取 `pilot-selection.json`
中的实际选择数，随后完整回归 527 项通过、2 项因 Windows 符号链接权限跳过。

结论必须分开：Escalation 后下一模型轮次发生编辑且最终正常终止，是值得继续观察的单样本时序
信号；候选正确性硬门槛失败，且没有控制组，不能声称策略或模型质量改善。该 Episode 仅作为
Dev Bad Case，不进入 Gold。机器证据见
`configs/integrations/swe-bench-lite-pvlib-local-calibration.json` 与
`configs/integrations/swe-bench-lite-pvlib-escalation-rollout.json`。

## 2026-08-12：首次编辑后的契约验证提示

pvlib Bad Case 将问题从“是否及时编辑”推进到“编辑后是否验证兼容契约”：模型确实在
Escalation 后下一轮编辑，但只关注数值边界，遗漏 pandas Series 返回类型。为此 Collector
新增默认启用的一次性 Post-edit Contract Notice，在首次成功显式文件编辑后的下一次模型请求中
要求执行最小目标验证和一项相关回归，并检查值/异常之外的 scalar/collection、容器与返回类型、
shape、ordering、null 与 metadata。该提示不读取隐藏测试，也不拦截工具；可通过
`--no-post-edit-contract-guidance` 关闭。

确定性测试证明：提示只在成功编辑后触发、下一模型请求可见、后续请求不重复追加；行为诊断升级
为 `rollout_behavior_diagnostics.v3`，新增 `post_edit_contract_guidance_turn` 及 Benchmark
聚合触发率。历史 pvlib Episode 继续保留真实的 diagnostics v2 和“未收到该提示”的事实。

当前证据等级为 **Implemented / Contract verified**，尚未进行新的付费模型调用。下一步应在
不同新鲜 Dev Issue 上验证目标/回归硬门槛，而不是用同一个 pvlib Issue 调参形成任务过拟合。

## 2026-08-12：pydicom-1413 同仓泛化与路径限定

选择 `pydicom__pydicom-1413` 作为第六条任务，用于检验 bytes 被错误拆成 `MultiValue` 的
容器契约，同时保持 5 个仓库、单仓最多 2 题。固定提交为
`f909c76e31f759246cec3708dadd173c5d6e84b1`。复用 pydicom Python 3.8.20 / pytest 5.4.3
环境的 Oracle 校准通过：基线 3 条 FAIL_TO_PASS 失败、301 条 PASS_TO_PASS 通过；参考
补丁后两组全部通过。

第一次 Rollout 在第 13 次模型请求消耗满默认 4096 输出 Token，仅返回思考块，没有正文或
工具调用。Anthropic-compatible 客户端没有保留上游 `stop_reason=max_tokens`，Runtime 因而
错误记录 `completed`。原 Episode 保持不变；修复同时覆盖 OpenAI-compatible 与
Anthropic-compatible 非流式响应的 Finish Reason，并将无工具的 `length/max_tokens` 响应
显式标记为 `stopped`。

基础设施修复后，以相同任务和策略、独立输出目录及 8192 单轮上限复验。模型第 3 轮定位
`pydicom/dataelem.py`，第 8/12 轮收到 Deadline/Escalation，但从第 14 轮起只反复修改
`repro_ol.py`，没有修改目标源码；第 18/22 轮收到 Reminder/Critical 后仍跑满 24 turns。
评测结果为 3 条 FAIL_TO_PASS 失败、301 条 PASS_TO_PASS 通过、Diff Scope 失败。这是有效
模型 Bad Case，不进入 Gold，也不再进行第三次同题调参。

复验还揭示 Post-edit Contract Notice 在第 14 轮被临时脚本写入误触发，因此不能视为对
“实现后契约检查”的有效验证。Collector 现把 Oracle-derived `allowed_path_patterns` 作为
`implementation_path_patterns` 传给 Runtime：只有匹配实现路径的成功 `write_file/edit_file`
才会抑制 Deadline/Escalation 并触发 Post-edit Notice；普通 Agent 未配置路径时保持原行为。
确定性测试覆盖“临时脚本不触发、实现路径触发一次”以及截断不误记完成。机器证据见
`configs/integrations/swe-bench-lite-pydicom-1413-local-calibration.json` 与
`configs/integrations/swe-bench-lite-pydicom-1413-post-edit-rollouts.json`。

## 2026-08-12：Astroid-1333 路径解析与 runtime_stop 契约缺口

第七条任务选择 `pylint-dev__astroid-1333`，覆盖缺失 `__init__.py` 的命名空间包路径解析。
它不是简单字段替换：Oracle 修改 `astroid/modutils.py` 的搜索路径顺序，包含 1 条
FAIL_TO_PASS 与 46 条 PASS_TO_PASS。此前 Astroid 临时环境未保留，因此在忽略提交的
`.port_sessions/environments/astroid-1333-py38` 重建 Python 3.8.20、pytest 7.4.4 和最小
运行依赖，不做 editable 安装。校准达到基线目标失败/46 回归通过、Oracle 后两组通过。

受控 Rollout 使用 8192 单轮输出、24 turns、Deadline/Escalation=8/4。模型第 6 轮定位
目标文件，第 8/12 轮收到提示，并在 `/tmp` 复现路径行为，但 14 个模型轮次和 17 次工具
调用内没有修改允许路径；最终响应只包含长思考并以 `max_tokens` 结束。路径限定生效：
`/tmp` Shell 复现既没有被算作直接实现编辑，也没有触发 Post-edit Contract Notice。
候选保持基线状态：目标失败、46 回归通过。

真实轨迹同时暴露新修复的第二层契约缺口：Runtime 已保留 `finish_reason=max_tokens` 并尝试
写入 `runtime_stop`，但 `agent_trajectory.v2` 的 Event Type 集合尚未注册该事件，Recorder
抛出 `unsupported event_type` 并把终止改为 failed。原 Episode 与 Verification 保持不变，
标记为基础设施污染且不进入 Dev Bad Case/Gold。修复将 `runtime_stop` 注册到 Schema，并在
Replay 中归为 termination；RuntimeAdapter 集成测试验证截断响应留下事件、终止为 cancelled、
正常进入 VERIFYING。由于模型“不编辑且长思考耗尽”的行为已经可见，不为同题再次付费重跑。
机器证据见 `configs/integrations/swe-bench-lite-astroid-1333-local-calibration.json` 与
`configs/integrations/swe-bench-lite-astroid-1333-path-scoped-rollout.json`。
