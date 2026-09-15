# Docker Sandbox 主机验证与接续手册

状态：实现与合同已验证；真实 Docker 边界、任务镜像与实际工作区无模型探针已执行

状态日期：2026-09-15

本文是把当前工作迁移到具备 Docker 的主机后直接继续执行的操作手册。架构决策、
威胁模型和后端比较见
[`AGENT_SANDBOX_BACKEND_DESIGN.md`](AGENT_SANDBOX_BACKEND_DESIGN.md)。本文只描述
仓库当前代码确实支持的路径、验证方法、证据位置和剩余工作。

## 1. 当前实现边界

当前 Docker 路径采用 **Sandbox-as-tool**：模型调用、Session、权限判断、Trajectory、
Git 初始化/Checkpoint/Diff 和 Verification 汇总留在可信宿主；以下不可信命令进入
Docker：

- Agent 的 Bash 工具和命令型 Plugin；
- Episode 的初始任务检查；
- Episode 的最终 Verifier 测试命令。

Agent 的结构化文件工具仍由可信宿主进程操作当前 Episode 工作区，并在 Docker
模式下强制工作区路径限制。模型 API 密钥只供宿主模型客户端使用，不会作为容器
环境变量继承。

一个 Benchmark Episode 的顺序是：

```text
复制模板并初始化 Git（可信宿主）
  -> 创建初始 Verifier Sandbox
  -> 暂存隐藏测试、执行初始检查、移除隐藏测试、销毁 Verifier
  -> 创建 Agent Sandbox
  -> Agent 文件工具操作受限工作区，Shell/命令型 Plugin 在容器执行
  -> 销毁 Agent Sandbox
  -> 创建全新的最终 Verifier Sandbox
  -> 暂存隐藏测试、执行最终测试、移除隐藏测试、销毁 Verifier
  -> 宿主采集 Git Diff、生成 Verification 和归档证据
```

Agent Sandbox 清理失败时不会继续最终验证；该结果记录为基础设施失败，而不是普通
测试失败。Episode 重新打开时默认继承 Manifest 中的 Backend、镜像和安全 Profile，
避免无提示降级到 Host。

Agent 的一次性 Shell 与长生命周期 Verifier Sandbox 是两个不同边界。一次性 Shell
显式覆盖任务镜像的 `ENTRYPOINT`，将 Episode 源目录只读挂载为 `/claw-source`，并在
容器内有界 Tmpfs `/workspace` 中准备可写副本。每条命令后删除容器和临时副本，Shell
侧修改不会写回 Episode。执行证据分别标记容器创建、工作区材料化、Shell 启动和任务
命令阶段，避免把挂载失败误报成普通测试失败。

## 2. Docker Backend 当前强制项

隔离 Profile 使用结构化 Docker CLI 参数，不接受模型提供任意 Docker 参数：

- 本地镜像检查和 `--pull never`，运行时不隐式下载镜像；
- `--network none`；
- 非 root 数字 UID/GID；
- 只读 RootFS，工作区为唯一默认读写 Bind Mount；
- `--cap-drop ALL`；
- `no-new-privileges=true`；
- 独立 IPC Namespace；
- `/tmp` 使用带大小限制的 `nosuid,nodev` Tmpfs；
- CPU、内存和 PID 上限；
- 单命令墙钟超时、容器强制终止和总输出大小限制；
- Mount Owner、来源根、只读类型、目标冲突、特殊文件和符号链接逃逸准入；
- Docker、Owner、Sandbox 和 Spec 的哈希 Label；
- Docker Server 版本、镜像身份、Profile、网络模式和生命周期证据。

Benchmark 使用 `benchmark_offline` Profile，并强制镜像引用采用
`name@sha256:<64 hex>`。交互式 `claw agent` 允许开发阶段使用本地 Tag，但同样不会
自动拉取。

## 3. 主机和镜像前提

1. 安装 Docker Engine 或 Docker Desktop，并确认当前用户可以执行 Docker 命令。
2. Docker Desktop 需要允许共享本仓库所在目录。
3. 推荐使用 Rootless Docker；当前代码不会自动证明 Docker daemon 本身是 Rootless。
4. 镜像至少需要 `/bin/sh`、`cp`、`rm` 和 `sleep`。运行 Live Test 还需要 `id` 与 `grep`。
5. 实际 Coding/Benchmark 镜像需要包含任务用到的 Python、Git、Ripgrep、编译工具和
   依赖；`python:3.12-slim` 只适合作为最小合同验证起点。
6. 容器默认使用宿主当前数字 UID/GID。工作区必须允许该身份读写。

先准备本地镜像并取得不可变引用：

```bash
docker pull python:3.12-slim
docker image inspect python:3.12-slim
docker image inspect --format '{{index .RepoDigests 0}}' python:3.12-slim
```

最后一条命令应返回类似
`python@sha256:0123...` 或 `python:3.12-slim@sha256:0123...` 的引用。后续将它记为
`<PINNED_IMAGE>`。如果镜像没有 `RepoDigests`，先推送到受信 Registry，或使用组织
内部的内容寻址镜像发布流程；不要用可变 Tag 运行正式 Benchmark。

## 4. 第一阶段：确认代码和 Docker 合同

在仓库根目录执行：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[dev]'

PYTHONPATH=src python -m unittest \
  tests.test_sandbox_backend \
  tests.test_docker_backend \
  tests.test_sandbox_cli \
  -v
```

不设置 `CLAW_TEST_DOCKER_IMAGE` 时，伪 Docker 控制面的合同测试应通过，Live Test 会
明确跳过。随后使用固定镜像运行真实测试：

```bash
CLAW_TEST_DOCKER_IMAGE='<PINNED_IMAGE>' \
PYTHONPATH=src python -m unittest \
  tests.test_docker_backend.DockerBackendLiveTest \
  -v
```

Live Test 同时验证：

- 容器内 UID 不是 0；
- 宿主临时设置的 `OPENAI_API_KEY` 没有进入容器；
- 容器没有默认网络路由；
- 容器能够被确定性销毁。

只有这一步在目标 Docker 主机真实通过后，才可以把该主机/镜像组合标记为“真实
Backend Smoke 已执行”；它仍不足以单独宣称完成全面安全验证。

随后必须对即将使用的实际 Episode 工作区运行无模型压力探针：

```bash
PYTHONPATH=src python tools/probe_container_runtime.py \
  --image '<PINNED_IMAGE>' \
  --engine auto \
  --workspace /absolute/path/to/materialized/episode/workspace \
  --task-python /path/inside/image/to/python \
  --import-name repository_package \
  --repeat 5
```

`--repeat` 在同一真实工作区执行多次，不调用模型。每个 attempt 必须同时满足
`workspace_ready=true`、`shell_started=true`、退出码 0，并且宿主目录不存在探针标记。
指定任务解释器时还必须满足 `task_import_verified=true`，且导入模块来自该 Episode 副本。
`failure_stage=container_create` 优先检查 daemon、Docker context 和 bind source；
`workspace_materialization` 优先检查挂载可读性、Tmpfs 容量与文件权限；`shell_start`
检查镜像 `/bin/sh`；`task_command` 才表示命令自身的非零退出。

## 5. 第二阶段：交互式 Agent Smoke

先使用没有真实密钥的测试仓库，确认镜像工具链与文件权限：

```bash
claw agent '检查工作区，运行最小 Python 命令，不修改文件' \
  --cwd /absolute/path/to/disposable-workspace \
  --sandbox-backend docker \
  --sandbox-image '<PINNED_IMAGE>' \
  --stream
```

预期行为：

- Docker 不可用或镜像不存在时直接失败，不回退 Host；
- Bash 在容器执行；
- 容器内工作目录为 `/workspace`；
- Session 的 `metadata.sandbox` 包含 Backend、Spec Hash、镜像身份和 Profile；
- 使用 `resume` 且不传 Backend 参数时继续继承 Docker 边界。

交互式会话设计为进程内复用 Sandbox。退出进程时会做尽力清理，但跨进程残留回收
尚未实现，所以每次异常退出后都要检查残留：

```bash
docker ps -a --filter label=com.claw.sandbox=true
```

不要在尚未确认归属时批量删除容器。先核对
`com.claw.sandbox-id-hash`、`com.claw.owner-id-hash` 和
`com.claw.spec-hash` Label。

## 6. 第三阶段：单任务 Docker Benchmark Pilot

从 Core Test Split 的一个任务开始：

```bash
claw benchmark-run \
  --manifest task_suites/manifest.json \
  --group base \
  --task-id python-cli-add_feature-07 \
  --model '<当前固定模型引用>' \
  --temperature 0 \
  --max-turns 12 \
  --sandbox-backend docker \
  --sandbox-image '<PINNED_IMAGE>' \
  --output .port_sessions/benchmark-docker-pilot
```

如果镜像只有 Python 而缺少 Agent 实际调用的工具，先把失败分类为镜像兼容性问题，
不要放宽 Sandbox Profile 或改用 Host 来制造通过结果。

检查以下证据：

```bash
python -m json.tool \
  .port_sessions/benchmark-docker-pilot/episodes/<EPISODE_ID>/episode.json

python -m json.tool \
  .port_sessions/benchmark-docker-pilot/episodes/<EPISODE_ID>/trajectory.json

python -m json.tool \
  .port_sessions/benchmark-docker-pilot/episodes/<EPISODE_ID>/verification.json
```

`episode.json` 应包含：

- `metadata.sandbox_config.backend == "docker"`；
- 固定镜像和 `benchmark_offline`；
- 分别标记 `initial_check` 与 `final_verification` 的 Sandbox；
- 两个 Verifier 使用不同 Sandbox ID；
- `destroyed == true` 且 `final_state == "destroyed"`。

`trajectory.json` 应包含：

- Agent 工具结果中的 Backend、Owner、Sandbox ID、Spec Hash 和镜像证据；
- `sandbox_lifecycle` 的 `destroyed_before_verification` 事件；
- 最终 `test_result` 中每条命令的 Verifier Sandbox 证据；
- 基础设施错误与测试非零返回码没有混为一类。

运行后再次确认没有 Claw 容器残留。

## 7. 目标主机验收矩阵

| 检查项 | 通过条件 | 当前仓库状态 |
| --- | --- | --- |
| Docker CLI/daemon | Version 检查成功 | 待目标主机验证 |
| 本地固定镜像 | `--pull never` 下可创建 | 待目标主机验证 |
| 非 root | Live Test UID 非 0 | 待目标主机验证 |
| 密钥隔离 | 临时 API Key 在容器中为空 | 待目标主机验证 |
| 离线网络 | 无默认路由，外部访问失败 | 待目标主机验证 |
| 资源限制 | CPU/内存/PID 参数生效 | 合同已验证；真实 cgroup 待验证 |
| 超时清理 | 超时后容器停止且可销毁 | 合同已验证；真实 daemon 待验证 |
| Hidden Test 边界 | 仅 Verifier 阶段可见 | Host/伪 Docker 合同已验证 |
| Agent/Verifier 分离 | 不同 Owner/Handle，Agent 先销毁 | Host/伪 Docker 合同已验证 |
| 单任务 Benchmark | 证据完整且可重复运行 | 待目标主机验证 |
| 并发污染 | 不同 Episode 无文件/进程交叉 | Planned |
| 官方 SWE-bench | 官方固定 Harness 完成运行 | Planned |

真实验证完成后，记录 Docker Engine/Desktop 版本、宿主 OS/Kernel、镜像完整 digest、
命令、UTC 时间、退出码和产物路径。不要只写“Docker 测试通过”。

## 8. 已知限制与下一步

- Docker 共享宿主内核，不是 microVM，也不是公网多租户强隔离证明。
- 当前没有 Proxy-only/域名 Allowlist 网络控制器。
- 当前没有可移植的工作区磁盘配额。
- Docker Exec 当前为缓冲输出，不是真流式传输。
- 进程退出清理是尽力而为；尚无跨进程 Lease 和残留扫描器。
- SELinux Bind Mount Label、设备策略和 Rootless daemon 证明尚未实现。
- 任务清单中的命令必须能在容器镜像内执行。SWE-bench Dev 三臂入口会把 evaluator
  随隐藏资产注入，并分别支持 `--claw-sandbox-python` 历史任务解释器与可选的
  `--claw-sandbox-evaluator-python`；镜像仍必须预装 Claw 和该历史任务的冻结依赖，
  宿主校准环境不会自动复制进镜像。
- 轻量 Rollout、训练 `SandboxManager`、MCP 外部进程和 Pi RPC 尚未统一迁移到 Session
  Docker Backend。三臂入口可让 Pi 的 Verifier 使用同一 Backend，并可由
  `PiDockerRpcClient` 启动完整 Pi 容器，但仍要求显式 attestation。Pi Provider 与工具
  共用容器网络，而 Claw Shell 离线，不能宣称网络策略等价。
- 校正后的三臂入口在任何模型调用前执行一次一次性 Shell 合约：候选快照必须可见，
  容器写入必须被丢弃，并把通过证据写入 `runtime-ablation.json.admission`。该 Runner
  必须同时注入两个 Claw 臂；只验证 Verifier 容器不足以通过准入。
- 正式 SWE-bench 仍必须接入官方 Docker Harness；本地 Pilot 不能当作官方分数。

推荐在 Docker 主机按以下顺序继续：

1. 完成 Live Test 并保存主机/镜像证据。
2. 完成一个 Core 单任务 Benchmark，两次重复运行并比较生命周期证据。
3. 增加真实 CPU、内存、PID、无限输出、后台进程和异常退出清理测试。
4. 增加两至四个并发 Episode 的文件、进程、网络和容器 Label 交叉污染测试。
5. 制作包含项目工具链的固定 Benchmark 镜像，并记录 Dockerfile 与 digest。
6. 再接入官方 SWE-bench Harness，保持 Agent 输入与 Hidden Test/Verifier 数据分离。

完成上述步骤前，状态标签应保持为：

- 代码与单元/合同测试：**Implemented / Contract verified**；
- 真实 Docker Live Test：目标主机执行后可标记为 **Backend smoke verified**；
- 固定任务集真实运行并保存报告后：相应运行可标记为 **Benchmark verified**；
- 未运行官方 Harness：不得称为官方 SWE-bench 结果。
