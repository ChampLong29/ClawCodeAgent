# Benchmark Pilot 分层方案

## 目标

将“运行管线是否正常”和“Agent 是否具备真实工程能力”分开衡量，避免简单函数补全掩盖检索、跨模块推理、验证和工作区卫生问题。

## 三层任务

| 层级 | 清单 | 用途 | 主要判据 |
|---|---|---|---|
| Smoke | `task_suites/manifest.json` | 验证 Episode、Trajectory、工具、Diff 和 Verifier 链路 | 快速、确定性、不能作为能力结论 |
| Medium pilot | `task_suites/medium/manifest.json` | 跨模块实现与修复，使用带哈希的隐藏测试 | 正确性、原子性、边界行为、工作区卫生、终止效率 |
| Real issue pilot | SWE-bench Lite dev 小子集 | 真实仓库 issue、依赖安装和回归测试 | FAIL_TO_PASS、PASS_TO_PASS、补丁范围、资源与成本 |

## Medium pilot 约束

- 测试资产通过 `test_assets_ref` 和 `test_assets_hash` 版本化。
- 隐藏测试仅在初始校验和最终验证时挂载为 `.claw_hidden_tests`，执行后立即删除。
- Oracle 仅用于任务构建、Diff 白名单和离线校准，不注入 Agent 上下文。
- 小型策展清单通过 manifest `validation` 声明规模和所需 split，不削弱默认 30-task 清单契约。

## SWE-bench 接入选择

第一阶段选 SWE-bench Lite 的 2–3 个 dev instance，而不是直接跑完整 test：官方 Lite 包含 300 个测试实例和 23 个开发实例，适合低成本集成验证。每个 instance 提供仓库、base commit、issue 描述、gold patch、test patch、FAIL_TO_PASS 和 PASS_TO_PASS 等字段；执行需要官方 Docker harness。

当前已对两个不同语义类型的任务完成非官方本地校准：
`marshmallow-code__marshmallow-1343` 的基线满足 1 条 FAIL_TO_PASS 失败、24 条
PASS_TO_PASS 通过；`pylint-dev__astroid-1196` 的基线满足 2 条 FAIL_TO_PASS
失败、24 条 PASS_TO_PASS 通过。两者应用参考补丁后所有选定测试均通过。该结果
只验证本地 Evaluator 状态转换，不代表 Agent 已解决任务，也不计作官方
SWE-bench 分数。Astroid 随后完成一次受控 Rollout 和一次基础设施修复后的重试；
有效重试保留全部 24 条 PASS_TO_PASS，但未修复 2 条 FAIL_TO_PASS，并耗尽
30 turns，因此只作为 Dev Bad Case。

接入映射：

| SWE-bench 字段 | Claw 证据 |
|---|---|
| `instance_id`、`repo`、`base_commit` | Task identity、template/source provenance |
| `problem_statement` | Agent prompt |
| `test_patch`、`FAIL_TO_PASS`、`PASS_TO_PASS` | 隐藏验证资产与 hard signals |
| Agent patch | Trajectory workspace diff |
| `patch` | 隔离保存的 oracle；禁止进入模型上下文 |

数据导入时还需保存原仓库许可信息，并固定 dataset revision、Docker image/harness version 和 instance IDs。官方 SWE-bench harness 为 MIT 许可，但实例代码仍应遵循各上游仓库许可。

参考：

- [SWE-bench 官方仓库与评测命令](https://github.com/SWE-bench/SWE-bench)
- [SWE-bench 官方数据结构说明](https://www.swebench.com/SWE-bench/guides/datasets/)
- [SWE-bench Lite 官方说明](https://www.swebench.com/lite.html)

## Pilot 判定

单条任务必须同时满足：隐藏测试通过、Diff 不越界、无权限违规、正常终止。另行记录 turns、tool calls、tokens、latency 和人工 Reviewer 的过程效率评分，防止“代码碰巧正确但 Agent 无法收尾”被计为成功。
