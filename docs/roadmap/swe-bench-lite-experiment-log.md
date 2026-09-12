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

## 2026-08-20：有界请求与首个合规成功 Episode

DeepSeek Anthropic 兼容接口支持 `thinking=disabled` 和 `tool_choice`，但忽略
`thinking.budget_tokens`。因此 Runtime 新增可追踪的 Thinking Mode 与两道动作约束：
Escalation 后的请求只暴露 `write_file`/`edit_file` 并要求工具调用；Critical 请求隐藏全部
工具并要求最终响应。若供应商返回不符合约束的动作，运行显式记录 `runtime_stop`，不误记完成。

针对性测试通过后，在既有 Marshmallow-1343 Dev 校准题上运行一次真实 Episode：关闭显式
思考、单次 4096 输出 Token、18 turns、Deadline/Escalation=5/2、Reminder/Critical=4/1。
模型第 2 轮定位目标文件，第 5 轮收到 Deadline，第 7 轮对
`src/marshmallow/schema.py` 做最小编辑；Post-edit Contract Notice 后完成复现、相关测试、
完整 schema/field 测试与 Diff 检查，并在第 15 个工具轮次后主动正常终止。

本地 Verifier 的 1 条 FAIL_TO_PASS、24 条 PASS_TO_PASS、Diff Scope、流程权限、格式和终止
合规信号全部通过，`hard_gate_passed=true`、`verdict=success`。这是项目保留的第一条本地真实
仓库合规成功 Episode。模型在第 7 轮编辑，因此 Escalation 强制动作未触发；它也在 Critical
阈值前结束。因此不能将成功单独归因于两个强制约束，更不能宣称普遍模型增点。该题此前已有
多次调试，结果只证明有界策略配置下链路能产生一个成功样本；泛化仍需全新 Issue 对照，官方
成绩仍需 Docker Harness。机器证据见
`configs/integrations/swe-bench-lite-marshmallow-bounded-action-success.json`。

## 2026-08-20：Marshmallow-1359 新 Issue 复现

为避免只在重复校准题上观察成功，将 `marshmallow-code__marshmallow-1359` 加入 Pilot。
任务 Base Commit 为 `b40a0f4e33823e6d0f341f7e8684e359a99060d1`，Oracle 只修改
`src/marshmallow/fields.py`。Python 3.8.20 隔离校准达到：基线 1 条目标测试失败、76 条
回归通过，参考补丁后两组通过。因此该题具备本地 Rollout 准入条件。

### v1：流程合规但语义失败

固定 `thinking=disabled`、4096 Token、18 turns、Deadline/Escalation=5/2 和允许路径。
模型第 2 轮定位文件、第 5 轮收到 Deadline、第 7 轮编辑，第 14 个模型轮次正常结束。
候选通过 76 条回归以及 Diff、流程、格式和终止门槛，但目标测试失败。模型把立即父对象
缺失的 `opts` 安全回退为 `None`，只验证了默认序列化，没有验证非默认配置沿父链传播。

### v2：强制动作生效，正确性仍失败

在 Post-edit Contract Notice 中增加“回退不能破坏非默认配置的父链传播”通用检查，并把
Deadline 提前到第 4 轮。第 6 轮 Escalation 实际触发：该请求只暴露直接编辑工具并要求
工具调用，第 7 轮发生目标文件编辑。模型随后验证显式字段 `format`、76 条 field 测试和
225 条 schema 测试，均通过；独立目标测试仍失败。原因是探针覆盖了字段自身配置，却没有
覆盖 `Schema.Meta.datetimeformat`。最终候选仍使用与 v1 等价的 `None` 回退，而不是已有的
`Field.root` 抽象。

### 结论与停止条件

两次候选都在正确文件做了小范围修改、保留 76 条回归并正常收尾，说明有界动作机制可以
改变请求和动作时机；两次目标测试都失败，说明它没有弥补根配置所有权与父链传播的语义
理解。该结果归类为 `semantic_contract_miss`，不进入 Gold SFT。停止继续采样同题；下一步
把“嵌套对象的配置所有者与 root/parent-chain 不变量”作为通用验证契约，冻结后到另一条
新 Issue 做控制实验。机器证据见
`configs/integrations/swe-bench-lite-marshmallow-1359-local-calibration.json` 与
`configs/integrations/swe-bench-lite-marshmallow-1359-bounded-action-rollouts.json`。

## 2026-08-20：PyVista-4315 预注册确认实验

在获取仓库、运行校准或调用模型前，先固定并哈希
`bounded-action-confirmatory.v1`：任务为唯一新增仓库 Family 的
`pyvista__pyvista-4315`；Control 关闭 Post-edit Contract Notice，Treatment 开启；其余
模型、Thinking、Token、Turn、Deadline/Escalation、动作约束、Critical 和 evaluator 参数
完全相同；每臂只允许一个有效 Episode，两个归档完成前不分析 Control。

### 环境准备与准入失败

精确 Base Commit 为 `db6ee8dd4a747b8864caae36c5d05883976a3ae5`，仓库干净，Oracle
只允许 `pyvista/core/grid.py`。首次在 Windows 挂载盘和 WSL 原生目录用 `venv` 创建环境，
均因底层 `ensurepip` 返回 127 失败；改用现有 uv 管理的 CPython 3.8.20 在 WSL 原生目录
创建环境。第一次四段校准又因 `tests/conftest.py` 缺少 `ipykernel` 全部返回 pytest 4；
补齐后，基线目标失败、Oracle 目标通过，但 114 条回归中 3 条因缺少 `tqdm`/`meshio`
失败。以上阶段模型调用数均为 0。

按仓库声明的版本上限固定 `ipykernel==6.29.5`、`tqdm==4.65.0`、`meshio==5.3.4` 后，
最终准入达到：基线 1 条 FAIL_TO_PASS 失败、114 条 PASS_TO_PASS 通过；Oracle 后两组
通过。完整 Python 3.8.20、VTK 9.2.6、NumPy 1.24.4 锁文件与三次校准哈希均本地化。

### Control 与 Treatment

Control 第 2 轮定位目标、第 4 轮收到 Deadline、第 6 轮首次编辑；Treatment 第 3/4/6
轮达到对应阶段，并在第 6 轮实际收到 Post-edit Notice。两臂都没有触发 Escalation，第 14
轮收到 Reminder、第 17 轮进入强制最终响应，均在 18 个模型轮次后归档。

两臂候选的语义修改等价，仅 `np.asarray` 转换前的注释措辞不同。两者都通过 1 条目标测试、
114 条回归、Diff Scope、流程权限、格式和终止门槛，`aggregate_score=1.0`。Treatment 相比
Control 多 181 Token、约 2.94 秒，少一次显式编辑和一次失败工具调用。Control 在 Critical
请求把工具调用标记作为普通文本返回，Treatment 则生成正常的实现/验证摘要；该差异只作为
后续 Reviewer/终止质量假设，不作因果结论。

后续回放确认这是验证盲区而非终止状态错误：旧 `termination_compliance` 只判断 Episode 是否
以 `completed` 收尾。Verifier Policy v2 新增零权重 Soft 信号 `final_response_quality.v1`，
保守识别空回答、纯思考块和裸工具调用标记，不影响硬门槛与 Gold 准入。对不可变轨迹派生的
结果为 Control `raw_tool_call_markup/fail`、Treatment `user_facing_text/pass`；原始轨迹和
Verification 均未重写。待多任务/Seed 回放确认误报率后，再决定是否提升为数据筛选门槛。

按照预注册解释矩阵，本次落入 `control_pass_treatment_pass`：支持冻结策略可以在此前未见的
PyVista 仓库 Family 产出两条本地合规成功，不支持 Post-edit Notice 带来正确性提升。下一步
必须增加未见任务或 Seed 才能估计成功率、成本和最终回答质量分布。协议与机器证据见
`docs/roadmap/bounded-action-confirmatory-experiment-protocol.md`、
`configs/integrations/swe-bench-lite-pyvista-4315-local-calibration.json` 和
`configs/integrations/swe-bench-lite-pyvista-4315-confirmatory-comparison.json`。

## 2026-08-20：两任务冻结复现与动作约束负效应

在获取两个任务的目标提交、校准或调用模型前，哈希冻结
`bounded-action-multitask.v1`。固定 `pvlib__pvlib-python-1606` 与
`sqlfluff__sqlfluff-1733`，相同 DeepSeek、Token、Turn、Deadline/Escalation、Post-edit、
Critical 和 Verifier v2 配置，每题最多一个有效 Episode，不做质量重试。两个 Python 3.8.20
既有隔离环境均一次通过准入：pvlib 为基线目标失败/10 回归通过、Oracle 两组通过；SQLFluff
为基线目标失败/3 回归通过、Oracle 两组通过。准入前模型调用数为 0。

pvlib 第 2 轮定位 `pvlib/tools.py`，第 4 轮收到 Deadline，第 5 轮做一次最小编辑并收到
Post-edit Notice，第 10 轮正常完成。候选通过 1 条目标、10 条回归、Diff、流程、格式、终止
和最终答复质量信号；共 7420 Token、约 137.93 秒。Escalation 与 Critical 均未触发。

SQLFluff 前 6 轮集中检查 L003，第 4/6 轮收到 Deadline/Escalation。第 7 轮模型第一次在请求中
定位正确的 `src/sqlfluff/rules/L039.py`，但选择 `read_file`；该请求已经只暴露直接编辑工具，
Runtime 因而记录 `action_constraint_unsatisfied` 并取消 Episode，没有实际编辑。候选保持基线：
目标失败、3 条回归通过，最终答复质量为 `not_completed`；共 9470 Token、约 288.48 秒。

这不是环境失败，也不重跑。它表明立即强制编辑虽然能阻止无界调查，却可能截断“刚定位目标、
先读后改”的可恢复路径。原归档报告因通用 `runtime_error` 规则将其标为
`environment_or_infra`；原文件保持不可变，分类器现确定性重放为
`action_constraint_violation`，次类为 `test_failure`。行为诊断 v4 同时区分 9 次模型请求的
工具调用、8 次实际分发和 1 次被拒绝调用，将目标定位时机修正为第 7 轮。两题合计 1/2 成功，
只能作为描述性策略权衡证据。协议和机器结果分别见
`docs/roadmap/bounded-action-multitask-validation-protocol.md` 与
`configs/integrations/swe-bench-lite-bounded-action-multitask-result.json`。

## 2026-08-20：渐进式目标读取约束实现

针对 SQLFluff-1733 暴露的截断问题，Runtime 新增默认关闭的
`implementation_target_read_allowance`。其值只能为 0 或 1；设为 1 时，Escalation 后的
首次受约束请求可选择直接编辑，或读取一次命中实现路径 allowlist 的目标文件。若选择读取，
下一次工具请求只暴露 `write_file`/`edit_file` 并必须编辑 allowlist 内路径。越界读取、第二次
读取、混合调用和越界编辑均在工具分发前停止，并保留 `model_request.action_constraint` 与
`runtime_guidance` 证据。旧配置默认值为 0，因此历史严格约束可原样复现。

本次只完成机制与聚焦测试，不把测试通过解释为模型质量增点，也不重跑 SQLFluff-1733。
下一步是在获取新任务提交、校准和调用模型前冻结一份新任务对照协议。

随后哈希冻结 `progressive-action-constraint-ablation.v1`，选择此前未运行的
`pylint-dev__astroid-1978` 与 `pydicom__pydicom-1256`，按 Strict→Progressive 与
Progressive→Strict 的反向顺序做最多四条配对 Episode。任务纳入后首次尝试从既有本地副本
复制仓库失败：旧副本是部分对象仓库，切换到目标提交时缺少 Blob，并触发机器级 Git
`core.fsmonitor` helper 的 `daemon terminated`。该失败发生在新建且被忽略的任务副本中，主
工作树未受影响。删除且仅删除这两个损坏副本后，从官方上游完整重取；只在新副本内关闭失效
的 fsmonitor，最终 HEAD、对象完整性、clean-state、tracked size 与许可证哈希均通过。

模型调用前复用 WSL Python 3.8 环境完成准入。Astroid-1978 基线 1 条目标失败、12 条回归
通过，参考补丁两组通过；pydicom-1256 基线 1 条目标失败、22 条回归通过，参考补丁两组
通过。模型调用数仍为 0。证据见
`configs/integrations/swe-bench-lite-astroid-1978-local-calibration.json` 与
`configs/integrations/swe-bench-lite-pydicom-1256-local-calibration.json`。

## 2026-08-20：渐进式约束消融首条 Strict Episode

按冻结顺序运行 `pylint-dev__astroid-1978` Strict 臂，生成提交固定为 `68b7ae0`，且
`implementation_target_read_allowance=0`。Episode 完整归档：第 2 轮首次定位
`astroid/raw_building.py`，第 4 轮收到 Deadline，第 5 轮完成唯一一次直接修改并收到
Post-edit Notice，第 12 轮以正常用户答复结束。共请求并分发 11 次工具调用，无策略拒绝；
5409 Token，约 81.51 秒。由于模型在 Escalation 前已编辑，本次没有触发 Strict 与
Progressive 的实际处理差异。

候选补丁在目标 `getattr` 外增加 `warnings.catch_warnings()` 并忽略 Warning。它保持 12 条
PASS_TO_PASS 回归通过，Diff Scope、流程权限、格式、终止和最终答复质量均通过，但 1 条
FAIL_TO_PASS 仍失败，因此是有效的模型质量失败，而不是环境、基础设施或动作约束失败。
Oracle 与隐藏测试要求更广：捕获模块 `__getattr__` 写出的 stdout/stderr，并通过 logger
记录；仅忽略 Python Warning 没有覆盖该行为。该差异说明模型修复了问题描述中的表面症状，
却没有恢复基准期望的完整语义。

这是预注册四条 Episode 中的第 1 条，不做单臂因果解释，也不据此调整协议或重试。机器证据
见 `configs/integrations/swe-bench-lite-progressive-action-constraint-result.json`；
下一条仍按冻结顺序运行同任务 Progressive 臂，最终只在全部准入配对归档后比较策略。

证据防回归测试首次从未安装包的 Windows 解释器直接运行时，因缺少 `PYTHONPATH=src` 在
收集阶段报 `ModuleNotFoundError: claw`，没有执行任何测试。按仓库文档补上该环境变量后，
相关 27 项测试全部通过；这属于本地验证命令配置问题，不影响已在隔离 WSL 环境完成的
Episode 或其不可变证据。

随后按冻结顺序运行同任务 Progressive 臂。它第 2 轮定位目标、第 4 轮收到 Deadline、第 6
轮触发 Escalation，并被允许“读取一次目标文件或直接编辑”；模型没有消费读取额度，而在第
7 轮直接编辑并收到 Post-edit Notice。它第 14 轮收到 Completion Reminder，第 16 轮正常
完成；15 次模型请求的工具调用均被动作约束接受，共 11812 Token、约 119.27 秒。

Progressive 产生了与 Strict 完全相同的 `warnings.catch_warnings()` 三行修改，也同样保持
12 条回归通过但未通过目标测试。过程中的两次普通工具失败分别是复现环境缺少 NumPy，以及
模型尝试用 `git stash` 包裹复现命令而被 Shell 安全策略阻止；后者没有实际分发或改变工作区，
两者均不是 Episode 的主失败原因。

Astroid 配对因此落入“相同硬结果”的不确定单元：Strict 没有触发 Escalation；Progressive
虽触发了新增选择，却直接编辑而未使用读取许可。两臂成本差异只作描述，不解释为策略因果
效应。实验现完成 2/4 条有效 Episode，下一步按冻结的反向顺序先运行 pydicom-1256
Progressive，再运行 Strict。

## 2026-08-20：渐进式目标读取约束消融完成

pydicom-1256 按冻结的 Progressive→Strict 反向顺序各运行一次。两臂第 2 轮定位
`pydicom/jsonrep.py`，第 4 轮收到 Deadline，第 6 轮触发 Escalation。Strict 第 7 轮仍请求
`read_file`，因该轮只允许直接编辑而在分发前停止；Progressive 接受了第 7 轮唯一一次目标
读取，并把下一轮收紧为直接编辑，但模型第 8 轮再次请求读取，同样在分发前停止。两条工作区
均无修改，目标测试失败、22 条回归通过，主 Bad Case 均为 `action_constraint_violation`。

Progressive 因而把可恢复窗口延长一轮，却没有把 Strict 的约束停止转化为编辑或成功：Strict
为 5755 Token、约 116.08 秒，Progressive 为 11119 Token、约 122.78 秒。Astroid 配对则在
两臂产生相同错误补丁和相同测试失败，其中 Progressive 虽收到可选读取约束但直接编辑，未消费
许可。四条 Episode 合计 0/4 通过硬门槛，4/4 保持所选回归，Strict/Progressive 各有一次
动作约束停止。

按预注册决策规则，两组配对均为相同硬结果，整体结论为不确定：不偏好 Progressive，也不把
该小样本解释为 Strict 的因果优势，保留现有默认值。下一步不在这些任务上调参或质量重试；应
基于不可变 Bad Case 另行预注册“读取后转编辑遵循性”干预，并保持 LoRA 数据冻结为独立门槛。
最终机器证据见
`configs/integrations/swe-bench-lite-progressive-action-constraint-result.json`。

## 2026-08-20：读取后转编辑纠正——机制、首轮关闭与新载体准入

### 为什么做这项干预

pydicom-1256 Progressive 的不可变轨迹已经证明 Runtime 接受了第 7 轮唯一一次目标读取，
并在第 8 轮只暴露编辑工具；模型却再次请求读取，随后被显式停止。这支持的最窄推断是
“模型可能没有在一次协议切换后遵循 edit-only 请求”，而不是“模型缺少更多文件信息”。因此
新机制没有增加读取额度，也没有提供文件内容、测试结果或解题提示；它只把消费目标读取后的
第一次无效动作保留为未分发证据，并允许一次 no-new-information 纠正请求。若再次违规，仍按
原策略停止。参数默认值为 0，实验值只能为 1，且必须与一次目标读取额度同时启用。

该设计分别验证三层命题：H1，一次纠正能否把“重复读取”转成目标路径编辑；H2，错误请求是否
始终未分发、纠正次数是否有界、是否没有泄露新任务信息；H3，转成编辑后能否进一步通过目标和
回归测试。H1 成立但 H3 失败只说明动作协议恢复，不等于修复能力提升。聚焦测试用模拟模型复现
了“读取→重复读取→纠正→编辑”和“读取→重复读取→纠正→再次重复读取→显式停止”两条路径；
证据断言只发生一次真实读取，纠正事件标记 `rejected_before_dispatch=true` 和
`new_task_information_provided=false`，恢复请求只暴露 `write_file`/`edit_file`。

### 首轮预注册为何在零模型调用阶段关闭

在仓库获取和模型调用前冻结 `read-to-edit-constraint-repair.v1`，按确定性筛选得到
SQLFluff-1517 与 Astroid-1866。SQLFluff 首次校准发现历史参数化 Node ID 被截断；Adapter
随后做了通用且保守的完整函数归一化，解决 `::` 出现在参数值和嵌套方括号时的解析错误。
但归一化后的完整测试函数同时包含目标参数与 PASS_TO_PASS 参数，导致 baseline 回归组失败。
Astroid 的目标与回归截断 ID 也归一化为同一测试函数。继续过滤失败参数会改变冻结 evaluator
覆盖范围，因此两题均不准入，协议按“不得替换”规则关闭，模型调用数为 0。

这不是 Repair 干预失败，因为干预从未被模型请求触发；证据只否定“这两条历史快照能作为独立
目标/回归载体”。失败、归一化修正、两次 SQLFluff 校准与一次 Astroid 校准哈希均保存在
`configs/integrations/swe-bench-lite-read-to-edit-repair-calibration-result.json`，未覆盖或删除。

### 独立后续协议与准入证据

首轮关闭后另行冻结 `read-to-edit-constraint-repair-admissible.v1`，显式排除已选 15 题和肉眼可见
的截断参数 ID，确定性选出 Astroid-1268（Control→Repair）与 pydicom-1694
（Repair→Control）。两条精确仓库快照分别固定到
`ce5cbce5ba11cdc2f8139ade66feea1e181a7944` 和
`f8cf45b6c121e5a4bf4a43f71aba3bc64af3db9c`，clean-state、Tracked 文件/字节数及许可证哈希
均与版本化快照一致。

零模型调用校准得到 Astroid baseline 目标 1 条失败（rc=1）、91 条回归通过，Oracle 后两组
通过；pydicom baseline 目标 1 条失败（rc=1）、26 条回归通过，Oracle 后两组通过。由此支持
“两条任务是有效的本地实验载体”，不支持“Repair 已改善动作或正确性”。原始校准文件分别以
SHA-256 `5826631041f50f52f7895a979fad4a4195f2e6677f0beed62933a9d208cc78a2` 和
`7e593f97fbfe59f34b641be05fc17a310e5ff270d1d373d709f9d62c2f61255c` 固定；观察、推断和
结论边界见
`configs/integrations/swe-bench-lite-read-to-edit-repair-admissible-calibration-result.json`。

正式 Control/Repair 模型调用尚未开始。下一门槛是先把机制、协议、快照、校准和测试固定到
可引用的实现版本；在工作树仍有未提交实现时不把旧 HEAD 冒充生成版本。

## 2026-08-21：跨设备接续封装

为避免后续设备把 Git 中的版本化证据与本机被忽略资产混淆，新增根目录
`TRAINING_HANDOFF.md`。它明确列出不会随 Git 迁移的 `.env`、`.port_sessions`、任务仓库
副本、环境和模型产物，并给出两条下一任务的精确仓库提交、Python 3.8 环境重建、零模型调用
准入复跑、四臂顺序、Collector 公共参数、证据解释层级与 LoRA 前置门槛。

原机 Astroid 环境没有 pip 模块，因此不能用 `pip freeze`；改从已安装发行包的 `.dist-info`
元数据读取版本。pydicom 环境可直接 `pip freeze`。两组结果分别固化为
`configs/integrations/astroid-1268-py38-lock.txt` 与
`configs/integrations/pydicom-1694-py38-lock.txt`。这些锁只复现已通过的本地环境，不宣称
等价于官方 Docker Harness。机器协议同时在任何模型调用前显式固定 Collector 当前默认的
`prompt_version=swe-bench-lite-dev.deepseek-v4-flash.v1`，两臂仍只有 repair attempts 一项差异。

## 2026-08-22：接续设备重建与单任务校准修正

在提交 `6d8ab80` 的接续设备上，从官方上游重建 Astroid-1268 与 pydicom-1694 固定提交，
并用版本化锁文件重建两个 Python 3.8.20 环境。首次按交接命令运行校准时，校准器尚未进入
测试：`load_agent_tasks()` 在应用 `--instance-id` 前验证整个 17 题 Pilot，因未重建历史任务
`marshmallow-code__marshmallow-1343` 而抛出 `FileNotFoundError`。该次失败属于接续工具的
本地资产范围错误，模型调用数为 0，不是任务准入失败。

适配器现允许单实例加载，校准 CLI 和 Episode Collector 均传入请求实例：仍验证完整公共
Manifest 的修订、成员、顺序、快照和字段边界，但只验证指定实例的本地工作区。批量调用保持
原行为。两个新增回归测试与六项任务选择测试
通过。为保留首次失败目录，重跑写入新的 `retry1` 目录。Astroid 恢复预期四态：基线目标
1 条失败、91 条回归通过，Oracle 目标和回归通过；本地结果 SHA-256 为
`aa81d254b431b4367d1f08829bd06c5b4689b7b6802d54770189c965524da4cf`。pydicom 同样恢复预期
四态：基线目标 1 条失败、26 条回归通过，Oracle 两组通过；本地结果 SHA-256 为
`8583e2f9e51ec9bd0c899a5675ba9716913ed7d263e298135eb23c43a9a5c09e`。两份结果仍只支持本地
兼容性准入，不是官方 SWE-bench 分数，也不支持 Repair 已改善模型行为。

全量 `unittest` 在该 WSL 接续环境运行了 551 项，结果为 10 失败、13 错误；主要已识别的
环境差异是缺少 `python` 命令别名、Windows 工作树 CRLF 导致版本化模板字节哈希不同，以及
按交接范围只重建了 2/17 个忽略的 Pilot 仓库。上述结果不记为全量通过；本次改动的聚焦测试
和两条实际校准通过。正式 Control/Repair 调用仍未开始，因为 WSL 中六个 Anthropic/OpenAI
配置变量均未设置，模型调用数继续为 0。下一门槛是固定本次校准修正为可引用提交，并在本地
安全配置 DeepSeek 端点后按冻结顺序运行四臂 Episode。

## 2026-08-22：读取后转编辑纠正消融完成

以生成提交 `3c7f951`、`deepseek-v4-flash`、温度 0、Thinking 关闭和 4096 最大响应 Token，
严格按 Astroid Control→Repair、pydicom Repair→Control 顺序各运行一个 Episode，没有质量
重试。四条均在 Escalation 前完成目标文件编辑，因此两条 Repair 臂都没有触发读取后纠正；
所有请求工具调用均被分发，没有策略拒绝或纠正请求。这意味着 H1 动作恢复没有实际触发样本，
H2 只观察到无拒绝/无纠正的普通安全路径，不能估计纠正机制的因果效果。

Astroid 两臂均在第 5 轮向 `astroid/nodes/as_string.py` 增加返回空字符串的 `visit_unknown()`，
目标文件字节哈希相同。Control 为 8 轮、7 次工具调用、3160 Token；Repair 为 8 轮、7 次
工具调用、2907 Token。两条均保持 91 条回归通过，但目标测试失败；Diff、权限、格式、终止和
最终答复质量信号通过，主 Bad Case 均为 `test_failure`。这是相同错误补丁和相同硬结果。

pydicom 两臂均把 `data_element = self[key]` 移入既有 `try` 块，目标文件字节哈希相同。
Repair 第 2 轮编辑，16 轮、15 次工具调用、7768 Token；Control 第 5 轮编辑，12 轮、11 次
工具调用、4646 Token。两条均通过 1 条目标、26 条回归、Diff、权限、格式和终止硬门槛，
是相同正确补丁和相同硬结果。成本与编辑时机差异只作描述，不归因于未触发的 Repair。

最终 H3 为 Control 1/2、Repair 1/2、合计 2/4；两个任务配对均为相同硬结果。按预注册边界，
结论是不确定并保持 Repair 默认关闭。该结果不支持 Repair 改善动作恢复或正确性，也不是官方
SWE-bench Harness 分数或模型能力估计。逐臂事件 ID、不可变归档哈希和结论边界见
`configs/integrations/swe-bench-lite-read-to-edit-repair-admissible-result.json`。

## 2026-08-22：任务集模板哈希跨平台校验修复

### 失败现象

接续设备（WSL 访问 Windows 挂载盘）上 `tools/validate_task_suite.py` 对核心与 Medium
两个 Manifest 的全部任务报 `template hash mismatch`；`test_task_suite_m1` 与
`test_medium_task_suite` 在 `setUpClass` 阶段整体错误。此前被归为"环境差异"，实为代码
缺陷。`workspace_hash` 把可执行位（`st_mode & S_IXUSR`）混入模板哈希：Windows 原生
Python 的文件没有可执行位（记为 `644`），WSL 挂载盘把所有文件报告为 777（记为 `755`），
因此原机生成的 Manifest（`644` 基准）在任何 WSL/drvfs 检出上都无法通过校验。

### 处理

`workspace_hash` 增加可选参数 `normalize_exec`：开启时普通文件一律按 `644` 参与哈希，
与文件系统如何报告 mode 无关。凡是对 `TaskSpec.template_hash` / `test_assets_hash`
做计算或比对的位置统一开启（两个生成器、`registry.verify_asset_hashes`、
`orchestrator.prepare` 的模板校验与隐藏测试暂存校验、SWE-bench Adapter、相关测试
Fixture）；Episode 检查点完整性保留 mode 敏感默认值不变，历史检查点语义不受影响。
重新生成两个 Manifest 后逐字节与已提交版本一致，证明 Manifest 本身始终正确，缺陷只在
校验/生成代码的跨平台性。

### 验证结果

- 核心 32 题、Medium 2 题 `validate_task_suite.py` 均 `passed: true`。
- 全量 unittest：563 项运行，仅剩 4 项错误，全部来自按交接范围只重建了 2/17 个忽略
  Pilot 仓库（Marshmallow-1343 等工作区缺失），与本次修复无关；失败数由 9 降为 0。
- 顺带确认"缺 `python` 命令别名"的失败根因是相对路径 `.venv/bin` 的 PATH 条目在
  `cwd=临时目录` 的子进程里解析失效；使用绝对 venv 路径后相关测试通过，属调用方式
  问题而非代码缺陷。

支持与不支持的主张：支持"模板哈希现在与检出文件系统无关、Manifest 可在任意平台
校验"；不支持把该修复描述为模型质量或实验结论变化。后续生成或新增任务集时沿用
`normalize_exec=True`，并在提交前运行两个 Manifest 的校验。

## 2026-08-22：重建 15 个缺失 Pilot 仓库

接续设备此前只按交接范围重建了 Astroid-1268 与 pydicom-1694 两个工作区，其余 15 个
实例（Marshmallow-1343/1359、Astroid-1196/1333/1978/1866、SQLFluff-1763/1733/1517、
pydicom-1139/1413/1256、pvlib-1707/1606、PyVista-4315）缺失，导致 10 项依赖完整
17 题工作区的测试报 `FileNotFoundError`。

按仓库家族从官方上游全量克隆首个实例，兄弟实例用 `git clone --local` 派生，再
`checkout --detach` 固定到 `repo-snapshots.json` 记录的精确 Base Commit。17/17 个
工作区的 HEAD、clean 状态与 tracked 文件数均与快照一致。

快照字节数与许可证哈希是在原机 Windows CRLF 工作树上记录的元数据，本机 LF 检出
无法逐字节复现：两个 astroid/pydicom 等实例的许可证哈希与 CRLF 内容一致，而
SQLFluff、pvlib-1707、Marshmallow-1359 的字节数与 LF Blob 完全一致，说明同一
`repo-snapshots.json` 混合了两种测量约定。该字段无任何代码或测试校验（`tracked_bytes`
/`tracked_files` 在 src/tools/tests 中无引用），仅作记录，不需要重写快照使其"看起来
一致"。

验证结果：此前失败的 49 项测试（adapter、Episode 采集、benchmark CLI）全部通过；
全量 unittest 563/563 通过，为接续设备首次全绿。重建脚本保留在忽略的
`.port_sessions/rebuild_repos.py` 供后续设备参考，不进入 Git。

## 2026-08-23：Post-edit 容器/返回类型契约新鲜验证完成

### 目的与冻结

在获取任务提交、校准与模型调用前冻结 `post-edit-container-return-type-fresh-validation.v1`
单臂协议：验证路径限定的 Post-edit Contract Notice 在一条全新 Dev Issue 上能否促成
容器/返回类型契约验证——即 pvlib-1606 遗漏的失败类（Series 返回类型）。确定性选题规则
（排除 17 题 Pilot 与 2 题截断关闭行、单 Oracle 文件、1 目标/1-100 回归、
light/moderate/scientific 环境、Oracle 引入 pandas/numpy 类型保持计算且隐藏测试断言
容器类型）唯一命中 `pvlib__pvlib-python-1072`：Oracle 把 `np.diff` 的 numpy timedelta
数组改写为 pandas Series（`index.to_series().diff().dt.total_seconds()`），隐藏测试
`test_fuentes_timezone[Etc/GMT+5]` 用 `assert_series_equal` 断言返回 Series 与索引。

### 准入（零模型调用）

Python 3.8.20 + pytest 7.4.4 + pytest-mock 3.14.0 + numpy 1.24.4 + pandas 1.5.3 +
scipy 1.10.1 环境四态校准通过：基线目标失败（rc=1）、基线 18 回归通过、Oracle 两组
通过。首次校准因 `pvlib.iotools` 导入链缺 `requests` 在收集阶段失败（rc=4），补齐
依赖后通过，属环境装配问题。

### Episode 与 H1/H2/H3

生成提交 `e111cab`、`deepseek-v4-flash`、温度 0、思考关闭、4096 token、18 turns、
Deadline 4/Escalation 2、post-edit 提示开启、Critical 1。模型第 1 轮定位
`pvlib/temperature.py`，第 3 轮以 `np.diff(poa_global.index.asi8)`（int64 纳秒差分）
完成唯一一次编辑并收到 Post-edit Notice（`post_edit_contract_guidance_turn=3`），
第 4 轮运行聚焦 bash 复现同时验证 naive 与 tz-aware 索引，第 5 轮正常终止；共 4 次
工具调用、3076 token、约 86.45 秒，无策略拒绝。

H1 机制激活成立（提示在首次允许路径编辑后立即送达）；H2 契约验证成立（提示后模型
验证了 tz-aware 失败路径，但未显式断言容器类型）；H3 硬成功成立：1 条目标与 18 条
回归全部通过，Diff Scope 仅含 `pvlib/temperature.py`，权限、格式、终止与最终答复
质量信号全部通过，`verdict=success`、`aggregate_score=1.0`。

### 观察、推断与边界

模型修复与 Oracle 实现不同（`.asi8` 保持内部 numpy 容器而非改写为 Series），但
返回的 `tamb` Series 及索引与隐藏测试断言一致——说明该失败类在此题上被以保持容器
语义的等价方式解决。单臂、单样本，不能把成功归因于 Post-edit Notice（PyVista-4315
对照已报 `control_pass_treatment_pass`，无正确性增益证据）；也不支持"提示普遍改善
容器/返回类型遵循"的推广。该 Episode 不进入 Gold SFT（Dev 题不进入训练集）。

机器证据见 `configs/integrations/swe-bench-lite-post-edit-container-return-type-validation-protocol.json`、
`swe-bench-lite-pvlib-1072-local-calibration.json` 与
`swe-bench-lite-post-edit-container-return-type-validation-result.json`。下一步按路线图
扩充 family-safe Train 数据前，机制与准入门槛均已具备。

## 2026-08-23：Qwen3-1.7B 最小 SFT 数据与契约阶段

### 目的

按路线图推进最小静态 LoRA 对照的前置阶段：采集 family-safe Train Episode、固化
Silver/Gold、LlamaFactory 导出、Tokenization 验证、train-split 数据集与 DryRun 契约
验证。基座模型为用户通过 modelscope 本地下载的 `Qwen/Qwen3-1.7B`
（`/home/longwanzhou/.cache/modelscope/models/Qwen--Qwen3-1.7B/snapshots/master`，
13 个文件，内容寻址 pin `19eb1973…`）；该路径不随 Git 迁移。

### 采集与治理

从核心任务集 train split 确定性选择 6 题（两域 × 两类型 × 易/中难度），
`deepseek-v4-flash`、温度 0、prompt `training-contract-batch.deepseek-v4-flash.v1`
采集 6/6 成功 Episode（`episode_collection_df9eb5b6e96ca4be63a8`）。Silver 6 条 →
纯 Python `AgentSFTGovernancePipeline`（与 DataFlow 包装层同一组 M2 算子）→ Gold
6 条、0 排除、0 泄漏 → ShareGPT tool-use 导出 6 条。本机未重跑 DataFlow 执行层
（其固定包版本属未迁移环境），证据中已注明；Tokenization 用 transformers +
本地 Qwen3 tokenizer（vocab 151643）验证：最长样本 1728 token < 8192 cutoff。
train-split 数据集 `dataset_3ee2a9f3079c38d441e8`（verifier_filtered、6 样本、
0 排除）。DryRunBackend 契约验证通过：`contract_verified=true`、
`training_verified=false`（符合不变量，真实训练未发生）。

### 环境约束与下一步

本 agent 沙箱为只读根文件系统容器、无 GPU 设备节点；真实 LoRA 需在 GPU 机器执行
（Windows 宿主 RTX 5080 16GB 或启用 WSL CUDA 直通）。已备好 `run_peft_lora.py`
（冻结配置：lora/qlora、seed 42、lr 2e-4、lora_r 16/alpha 32、bf16、target
q/k/v/o_proj、max_seq 4096、epochs 3）与数据路径。下一步门槛：用户在 GPU 环境
安装 CUDA torch/peft/accelerate/bitsandbytes 后运行真实 PEFT，产出校验过的
Checkpoint 与产物（training_verified=true），随后立即对固定独立 Test Manifest
跑 Base/Adapter 对照。机器证据见
`configs/integrations/qwen3-minimal-sft-data-evidence.json`。

## 2026-09-07: Early-Deadline single-factor pair on pvlib-1154

The `early-deadline-pvlib1154.v1` protocol was frozen before repository
acquisition and model calls. A dedicated Python 3.8 environment was admitted
after pinning historical NumPy/Pandas/SciPy versions: baseline failed the one
target and passed all 97 regressions, while the Oracle passed both groups.

Both DeepSeek arms failed the target and Diff Scope. Moving only the
implementation Deadline from turn 12 to turn 4 advanced the first direct edit
from turn 11 to turn 5 and reduced total tokens from 22,670 to 6,586, but did
not improve correctness. Both arms dispatched an edit to
`pvlib/tests/test_irradiance.py` even though only `pvlib/irradiance.py` was in
the benchmark allowlist. This demonstrates that the current allowlist is a
post-execution verifier boundary rather than a dispatch-time physical mutation
boundary. The task will not be quality-retried; dispatch-time path enforcement
is the next Harness change to validate.

Evidence is recorded in
`configs/integrations/swe-bench-lite-early-deadline-pvlib1154-result.json`.
This is local Dev mechanism evidence, not an official SWE-bench score.

## 2026-09-08：资源受限 Harness 对照路线冻结

对三条候选路线完成取舍：Claw 自身消融因果最清楚但缺少外部基线；研究传统 Resolved
较少表达的合规、恢复和审计属性具有差异化，但自定义任务/指标容易自证；只挑 Claw
可能领先的指标与 DeepSeek Harness 比较则存在事后选指标和多因素混杂。最终采用组合
设计：固定 DeepSeek Harness `sdk-minimal`、Claw Minimal 和 Claw Controlled 三组，
以外部参考、实现等价性检查和内部机制消融分别承担不同职责。

主指标在模型调用前冻结为 Policy-compliant Resolved 与固定 Token/Turn/Tool/时间预算下
的 Resolved；原始 Resolved、Token、工具调用、时延和成本为必须同时报告的反向约束，
不得因 Claw 不占优而省略。首轮每任务/臂运行一次，优先扩大独立任务覆盖；仅对配对结果
分歧项和预注册的相同结果随机样本重复 2-3 次，并保留首次运行表，不做 best-of-N。

该路线不宣称 DeepSeek Harness 缺少策略扩展能力，也不以完整 Verified-500 或排行榜
复现为前提。比较对象限定为固定 Commit 的 `sdk-minimal` Profile，结论限定在冻结子集与
预算。完整协议、准入/停止规则和可接受表述见
`docs/roadmap/harness-comparison-experiment-design.md`。

## 2026-09-08：DeepSeek Harness parity-gate 构建阶段

在任何新模型调用前固定 DeepSeek Harness commit
`c389f96bf3a9b6807cb71ed6bdad5849be0df6d8`、`sdk-minimal` profile、Node 24
Linux 运行时、构建镜像与两项 SWE-bench Lite 任务的 base commit/allowlist。由于
Windows 挂载盘会使 pkg 递归扫描依赖极慢，改用 Docker 内部卷完成锁文件安装和构建，
最终运行时大小 262.4 MB，SHA-256 为
`3d76c815ecb5d0d62dd727b659888d69ab7417041ef61fc029f1f016258b401`；ripgrep sidecar
SHA-256 为 `193906679498de4d939345b937fa24e0e69a03c244bd70c859f5e41232713f21`。

Claw 已增加可逐字冻结外部 profile system prompt 的 `system_prompt_override`，并以单元
测试验证。官方 `sdk-minimal` 无密钥 smoke test 尚未执行：执行审批额度在启动该本机模拟
端点测试前达到上限，因此没有产生模型调用、任务结果或成功率数据。机器可复现协议见
`configs/benchmarks/deepseek-harness-parity-gate-protocol.json`；在 smoke、工具表面、
共同总预算和同一容器 verifier 四项门槛完成前，不启动六个正式 Episode。

## 2026-09-10：恢复环境后的本地成功 Episode

恢复七个已校准仓库快照和 Marshmallow Python 3.8.20 环境后，使用
`deepseek-v4-pro[1m]` 在 `marshmallow-code__marshmallow-1343` 上完成一次新 Dev
Episode。候选只修改 `src/marshmallow/schema.py`，1 条 FAIL_TO_PASS 和 24 条
PASS_TO_PASS 全部通过，Diff Scope、权限、格式和终止门均通过，正常结束于第 24
轮。该结果是单题本地 Dev 成功，不是官方 SWE-bench 分数，也尚未通过 Gold SFT
人工质量与泄漏审查。摘要见
`configs/integrations/swe-bench-lite-marshmallow-20260910-success-rollout.json`。

## 2026-09-11：首个真实 Claw–Pi 受控本地对照

在同一 Marshmallow-1343 快照、同一 Python/pytest 环境、Allowlist、Prompt、Verifier、
Turn 与累计 Token 预算下，以 `deepseek-flash`（后端 `v4-flash-9_10`）和 OpenAI
兼容协议运行 Claw 与 Pi RPC。Claw 在第 15 轮首次直接编辑并通过目标与回归测试；
Pi 完成问题复现和定位，但没有产生源码编辑，目标测试仍失败。Pi 的 cache-read Token
已计入 provider-processed Token，避免低估成本。

该结果仅证明真实 Pi RPC → Trajectory v2 → Verification v2 链路可运行，并提供一个
描述性差异样本；它不是官方 Harness 分数，也不能推出运行时统计优劣。严格结果保留
Pi 的隔离 attestation 及控制项，摘要见
`configs/integrations/swe-bench-lite-claw-pi-deepseek-flash-20260911.json`。七任务三臂
计划见 `configs/integrations/swe-bench-lite-pi-claw-ablation-plan-v1.json`；它只覆盖
20 题筛选中已经校准的有序子集，付费运行前必须先在目标 Docker 主机完成 Claw
Backend、Pi 容器包装和 RPC 冒烟。
