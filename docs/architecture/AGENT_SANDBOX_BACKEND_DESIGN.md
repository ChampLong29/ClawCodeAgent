# Agent Sandbox Backend 架构设计

状态：M1 已实现；M2 已实现并完成合同验证；M3 部分实现

状态日期：2026-09-11

在具备 Docker 的主机继续真实验证时，直接使用
[`DOCKER_SANDBOX_VALIDATION_RUNBOOK.md`](DOCKER_SANDBOX_VALIDATION_RUNBOOK.md)。
该手册给出镜像固定、Live Test、单任务 Benchmark、证据检查、验收矩阵和剩余工作，
并以当前实现而非目标架构为准。

本文设计 Claw Code Agent 的统一执行隔离层。目标不是把 Claw 改造成某个
商业沙箱 SDK 的包装器，也不是把现有临时工作区重新命名为“安全沙箱”，而是
建立可验证、可替换、默认失败关闭的执行边界，并让本地交互、Lifecycle、
Episode、Benchmark、SWE-bench 和训练 Rollout 共用同一套契约。

本文同时记录目标架构和实施状态。只有“现状”表与里程碑中明确标记为
Implemented 的能力才代表已落地；其余均不代表已经实现或通过安全验证。

## 1. 结论与关键决策

1. Claw 采用 **Sandbox-as-tool**：模型客户端、Agent 循环、Session、权限审批、
   Trajectory 和 Verification 留在可信宿主；文件与命令等有副作用的执行进入
   Sandbox Backend。
2. 沙箱按 **Session 或 Episode** 持有，而不是每条命令重新创建。交互式编码和
   Lifecycle 需要持续工作区；Benchmark/RL 的每个 Episode 必须使用全新环境。
3. 引入声明式 `SandboxSpec`、能力协商 `SandboxCapabilities` 和可替换
   `SandboxBackend`。业务层不得拼接 Docker、gVisor 或商业服务的原始启动参数。
4. `HostBackend` 只提供兼容与开发能力，必须明确标注为非强隔离；第一种真正的
   本地隔离后端为 `DockerBackend`，后续可增加 `GVisorBackend` 和远程托管后端。
5. 命令正则、工具 allowlist 和人工审批保留，但定位为策略与纵深防御，不能作为
   OS 隔离边界。
6. 安全配置采用默认拒绝：最小挂载、环境变量白名单、网络关闭或强制代理、非
   root、资源上限、超时和确定性销毁。后端无法满足必需能力时拒绝创建，不静默
   降级。
7. 模型 API 密钥默认永不进入沙箱。未来确需让沙箱访问外部服务时，使用短期、
   限域的委派凭据或宿主代理，不把长期真实密钥写入环境变量、文件或快照。

## 2. 范围与非目标

### 2.1 本阶段范围

- 统一 Shell、测试、受控文件操作和可执行插件的执行边界。
- 支持本地 Host、Docker，并为 gVisor、microVM 和商业后端保留稳定接口。
- 定义挂载、环境、网络、资源、生命周期、证据和错误契约。
- 保持当前工具结果、Trajectory v2、Verification v2、Session 和任务套件不变量。
- 为正式 SWE-bench Docker Harness、并行 Episode 和后续 RL Rollout 提供基础。

### 2.2 非目标

- 本阶段不自研 Firecracker 控制面、镜像分发或集群调度系统。
- 不把 NanoClaw、E2B、Daytona 或 AgentCore 作为 Claw 的必选运行时。
- 不承诺 Docker Backend 能提供公网多租户所需的 microVM 级隔离。
- 不在第一阶段把模型推理、完整 Agent 循环或 Session Store 搬入沙箱。
- 不用沙箱替代权限审批、Diff allowlist、隐藏测试隔离或供应链治理。
- 不把“成功创建容器”描述为“安全验证通过”。

## 3. 当前实现与缺口

| 能力 | 当前状态 | 事实边界 |
| --- | --- | --- |
| `WorkspaceSandbox` 临时目录、Git 快照与回滚 | Implemented | 提供工作区与可恢复性，不等于强隔离 |
| macOS Seatbelt 包装 | Implemented，有限路径使用 | `allow default` 加少量拒绝规则；非跨平台统一边界 |
| Bash 命令安全校验 | Implemented | 正则/规则策略，可降低误操作，不能防御任意恶意 Shell |
| `AgentPermissions` | Implemented | Shell/写入开关；Docker 强制工作区路径限制，Host 保持兼容默认值 |
| Python 文件工具的工作区路径检查 | Implemented，可选 | 不约束 Shell 内的文件访问 |
| Backend 中立的 Spec/Capabilities/Handle/Result/错误契约 | Implemented | `sandbox_backend.py`；Host 与 Docker 均实现核心生命周期 |
| Agent Shell 与命令型 Plugin 经过统一 Backend | Implemented | Session 复用同一 Handle；并未因此获得 OS 隔离 |
| `HostBackend` 生命周期与能力报告 | Implemented | 兼容/开发后端，明确报告 Host tier 和不支持的安全能力 |
| `DockerBackend` 代码与合同测试 | Implemented / Contract verified | 安全 argv、生命周期、准入与失败关闭已测；当前主机无 Docker，未完成真实后端安全验证 |
| 训练 `SandboxManager` 的 OS 隔离 | 未实现 | 默认关闭 Seatbelt 与 Git，本质为临时目录包装 |
| Agent Shell/Plugin command 子进程环境最小化 | Implemented | 显式继承白名单；密钥形态和动态加载变量拒绝进入 |
| Episode 任务检查命令 | Implemented | 初始检查和最终验证使用短生命周期 Verifier Backend；Git 控制面仍在可信宿主 |
| 其他可执行进程入口的环境最小化 | 部分 Implemented | Episode 已迁移；轻量训练、MCP 和外部 Harness 等尚未迁移 |
| 默认拒绝的出站网络 | Docker Implemented / Contract verified | Docker 隔离 Profile 使用 `--network none`；Host 与尚未迁移入口不具备此边界 |
| CPU、内存、PID、墙钟、输出限制 | Docker Implemented / Contract verified | Docker 不支持可移植磁盘配额；Host 不提供后端级资源隔离 |
| 可替换 Sandbox Backend 接口 | Implemented | Agent Shell 已可显式选择 Host/Docker；远程后端仍为 Planned |
| 执行级证据 | Implemented | Tool Result/Trajectory 保留 Backend、Sandbox ID、Spec Hash、代际、Profile、网络、镜像身份、超时与截断事实 |
| 经过验证的强隔离证据 | 未实现 | `HostBackend` 不是强隔离；`sandbox_attestation` 仍不是自动证明 |

因此，在新后端落地并完成相应验证前，文档和报告应继续使用“隔离工作区”、
“临时工作区”或“操作方隔离声明”等准确措辞，不能笼统声称任意不可信代码已被
安全隔离。

## 4. 威胁模型

### 4.1 受保护资产

- 宿主文件、其他仓库、SSH/云凭据、浏览器配置和个人数据。
- 模型 API Key、GitHub Token、数据库凭据和云服务身份。
- 其他 Session/Episode 的工作区、Memory、隐藏测试和结果。
- 宿主进程、容器运行时、内网服务和云元数据服务。
- CPU、内存、进程、磁盘、网络带宽和账单预算。
- Trajectory、Verification 与 Benchmark 证据的真实性。

### 4.2 不可信输入与代码

- 模型生成的 Shell、Python、构建脚本和测试命令。
- 仓库内的 `Makefile`、安装脚本、测试钩子和依赖 post-install 脚本。
- 外部 Issue、PR、网页、文档和消息中的 Prompt Injection。
- Agent 下载或生成的二进制文件与依赖包。
- 工作区内可能诱导 Agent 调用危险工具的配置文件。

### 4.3 需要缓解的威胁

- 越过工作区读取或修改宿主文件。
- 读取并外传环境变量、配置文件或挂载中的秘密。
- 访问云元数据服务、Docker Socket、宿主网络或其他 Session。
- 通过符号链接、路径别名、重复挂载或嵌套挂载绕过策略。
- 使用 Fork Bomb、无限输出、磁盘填充或后台进程耗尽资源。
- 容器退出、宿主重启或重试竞态造成双重执行和重复副作用。
- 把未真正执行的隔离策略记录为已验证证据。
- Hidden Test 在 Agent 阶段泄漏，或不同 Split/Episode 间状态污染。

### 4.4 暂不承诺解决

- CPU 微架构侧信道及底层虚拟化零日漏洞。
- Agent 在已明确授权范围内做出的恶意但“合法”操作。
- 所有第三方依赖的供应链安全。
- 商业 Backend 控制面内部的实现正确性；只能记录版本、能力和供应商证据。
- 对宿主可信控制面的攻陷。如果 Claw 宿主进程本身被攻陷，本文边界不成立。

## 5. 信任边界与目标架构

```text
Trusted host
┌──────────────────────────────────────────────────────────────┐
│ CLI / GUI / Benchmark / Training Orchestrator                │
│ LocalCodingAgent                                             │
│ ModelClient ── real model credentials                         │
│ AgentSession / SessionStore / RuntimeEventBus                 │
│ Permission / Bash policy / Budget                            │
│ Trajectory v2 / Verification v2                              │
│                                                              │
│ SandboxService                                               │
│   ├── policy resolution                                      │
│   ├── admission validation                                   │
│   ├── lifecycle ownership / lease                            │
│   └── Backend registry                                       │
└───────────────────────┬──────────────────────────────────────┘
                        │ typed SandboxSpec / ExecRequest
                        │ typed results / events / artifacts
Untrusted execution     ▼
┌──────────────────────────────────────────────────────────────┐
│ Sandbox instance                                             │
│   ├── isolated workspace                                     │
│   ├── shell / tests / build tools                            │
│   ├── ephemeral home and writable overlay                    │
│   ├── resource and time limits                               │
│   └── denied, allowlisted, or proxy-only network             │
└──────────────────────────────────────────────────────────────┘
```

模型请求仍先经过工具可见性、别名、策略、权限和 Runtime Event 处理；获准执行并
不意味着可以绕过 Sandbox Admission。权限回答“是否允许做”，Sandbox 回答“允许
后在哪里、以什么能力做”。

## 6. 核心领域模型

### 6.1 `SandboxSpec`

`SandboxSpec` 是后端无关的声明式合同。建议字段如下：

```python
@dataclass(frozen=True)
class SandboxSpec:
    sandbox_id: str
    owner_kind: str              # interactive_session | lifecycle | episode | benchmark
    owner_id: str
    backend: str                 # host | docker | gvisor | remote name
    runtime_tier: str            # host | container | user_kernel | microvm
    image: Optional[ImageRef]
    workspace: WorkspaceSpec
    mounts: Tuple[MountSpec, ...]
    environment: EnvironmentSpec
    network: NetworkPolicy
    resources: ResourceLimits
    lifecycle: LifecyclePolicy
    security_profile: str
    provenance: ProvenanceSpec
```

约束：

- 不提供 `extra_args`、`docker_args` 等任意字符串逃生口。
- 镜像正式运行时使用 digest 或等价不可变引用；Tag 只可用于明确的开发模式。
- `sandbox_id` 与 `owner_id` 不直接拼入 Shell 命令，必须经过后端安全编码。
- Spec 在创建前完成规范化并计算哈希，原始 Spec 与有效 Spec 都写入证据。
- Backend 只能收紧 Spec；任何放宽都必须回到策略解析层重新授权。

### 6.2 `SandboxCapabilities`

```python
@dataclass(frozen=True)
class SandboxCapabilities:
    isolation_tiers: Tuple[str, ...]
    network_modes: Tuple[str, ...]
    supports_snapshots: bool
    supports_pause_resume: bool
    supports_streaming_exec: bool
    supports_exec_cancel: bool
    supports_fs_api: bool
    supports_resource_limits: Tuple[str, ...]
    supports_verified_image_identity: bool
    admission_enforced_out_of_process: bool
    unsupported_fields: Tuple[str, ...]
```

创建前执行能力协商。安全 Profile 标记为 required 的能力缺失时返回
`capability_unavailable`，不能仅记录警告后继续运行。

### 6.3 `SandboxBackend`

第一版接口应保持小而完整：

```python
class SandboxBackend(Protocol):
    def capabilities(self) -> SandboxCapabilities: ...
    def prepare(self, spec: SandboxSpec) -> SandboxHandle: ...
    def start(self, handle: SandboxHandle) -> None: ...
    def exec(self, handle: SandboxHandle, request: ExecRequest) -> ExecResult: ...
    def stream_exec(self, handle: SandboxHandle, request: ExecRequest) -> Iterator[ExecStreamEvent]: ...
    def read_file(self, handle: SandboxHandle, path: str, ...) -> FileResult: ...
    def write_file(self, handle: SandboxHandle, path: str, data: bytes, ...) -> FileResult: ...
    def list_files(self, handle: SandboxHandle, path: str, ...) -> FileResult: ...
    def snapshot(self, handle: SandboxHandle, label: str) -> SnapshotRef: ...
    def restore(self, handle: SandboxHandle, snapshot: SnapshotRef) -> None: ...
    def stop(self, handle: SandboxHandle, reason: str) -> None: ...
    def destroy(self, handle: SandboxHandle) -> None: ...
    def inspect(self, handle: SandboxHandle) -> SandboxStatus: ...
    def list_owned(self, owner_id: str) -> Sequence[SandboxStatus]: ...
```

`prepare()` 与 `start()` 分离，以便先建立终止监听、Lease 和证据记录，再运行不可信
代码。`destroy()` 语义是最终清理，不等同于可能保留状态的 `stop()`。

M1 已实现 capabilities、prepare/start、exec/stream_exec、stop/destroy 和
inspect/list_owned 这一纵向合同。文件 API 与 Snapshot/Restore 仍是目标接口，
待后续 Backend 提供对应 Capability 时实现；上层在此前不得假定它们存在。

### 6.4 执行与结果合同

`ExecRequest` 至少包含：

- 结构化 argv 或明确标注的 Shell 字符串；
- cwd（必须为沙箱内路径）；
- 环境增量，不接受宿主完整环境；
- 命令超时与输出上限；
- 是否允许后台存活；
- 调用 ID、父 Trajectory Event ID 和取消令牌。

`ExecResult` 必须保留现有 `ToolResult` 不变量：stdout、stderr、return code、timeout、
错误细节，并增加 start/end 时间、截断信息、资源统计、Backend/Instance ID、有效
策略哈希。一次模型请求的工具调用仍只能在 Trajectory 和指标中计数一次。

## 7. Policy 与 Admission

### 7.1 安全 Profile

建议提供稳定命名的 Profile，而不是散落布尔开关：

| Profile | 用途 | 网络 | 资源限制 | 强隔离要求 |
| --- | --- | --- | --- | --- |
| `host_development` | 可信本地调试 | 继承或提示 | 建议 | 无；明确非安全 |
| `isolated_development` | 普通本地 Coding Agent | allowlist | 必需 | Container 或更高 |
| `benchmark_offline` | Task Suite、SWE-bench | 默认关闭 | 必需 | Container 或更高 |
| `untrusted_code` | 外部仓库或用户代码 | proxy-only/allowlist | 必需 | gVisor/microVM 优先 |
| `multi_tenant` | 公网不同用户 | proxy-only | 必需 | microVM 或经评审的等价边界 |

Profile 是最低要求。CLI 可以请求更严格配置，但不能用普通任务参数降低最低要求。

### 7.2 挂载合同

`MountSpec` 至少包含：

```text
host_path, sandbox_path, mode(ro/rw), kind, owner_scope
```

`kind` 建议限制为：

- `workspace`：当前 Session/Episode 的工作区；
- `runtime`：Agent Runner 或工具支持文件，必须只读；
- `test_fixture`：Verifier 阶段临时挂载，Agent 阶段禁止；
- `identity_material`：原则上不能挂载进 Agent Sandbox；
- `allowlisted_extra`：经宿主外部 allowlist 批准的额外路径。

Admission 必须：

- 对宿主路径执行 absolute path、`realpath` 和存在性检查；
- 验证路径位于对应 owner 的允许根目录；
- 拒绝符号链接逃逸、重复目标、冲突嵌套和读写权限升级；
- 拒绝 Docker Socket、容器运行时 Socket、设备节点、宿主根目录和敏感目录；
- 额外挂载默认拒绝、默认只读；allowlist 文件位于 Agent 不可读写的宿主路径；
- 把完整有效挂载表写入安全证据，但对用户私密路径进行适当脱敏。

### 7.3 环境变量与秘密

当前 `runtime_subprocess_environment()` 的完整环境复制不能进入隔离后端。新合同使用
显式白名单，基础集合可包含：

```text
PATH, LANG, LC_ALL, TZ, TERM, TMPDIR, HOME
```

其中 `PATH`、`HOME`、`TMPDIR` 由 Backend 构造，不直接继承宿主值。所有变量还要
经过以下检查：

- 默认拒绝 `*_KEY`、`*_TOKEN`、`*_SECRET`、`*_PASSWORD`、云凭据和动态加载变量；
- 禁止 `LD_PRELOAD`、`DYLD_*`、`PYTHONPATH` 等改变执行边界的宿主继承；
- Provider API Key 只留在宿主 ModelClient；
- 必需的外部身份采用短期最小权限 Token，或经强制代理在请求出站时注入；
- Snapshot 和日志不得包含真实秘密；代理不可用时失败关闭。

### 7.4 网络策略

`NetworkPolicy` 采用显式模式：

```text
none          无出站和入站网络
proxy_only    只能到达受控代理
allowlist     只能访问声明的域名/CIDR/端口
unrestricted  仅供明确授权的可信开发任务
```

仅设置 `HTTP_PROXY`/`HTTPS_PROXY` 不构成强制策略，因为程序可以使用原始 Socket
绕过。`proxy_only` 必须由 Docker internal network、网络命名空间、防火墙或云端
等价机制强制，并阻断云元数据地址、宿主网关和内网默认路由。

### 7.5 运行时加固

`DockerBackend` 的 `isolated_development` 及以上 Profile 至少要求：

- 非 root 用户与固定/映射 UID；
- `cap-drop=ALL`，仅按结构化能力单独恢复；
- `no-new-privileges`；
- 禁止 privileged、host PID/IPC/network 和设备透传；
- 禁止 Docker/Podman/Kubernetes 管理 Socket；
- 固定基础镜像，运行时使用临时可写层；
- 可行时只读 RootFS，确需安装依赖时使用独立可写 overlay；
- 合理的 seccomp/AppArmor/SELinux 配置；
- CPU、内存、PID、临时盘、输出和墙钟时间上限；
- 正确的 PID 1 与信号转发，取消时先终止再强杀；
- Sandbox 自身不能修改 Admission Policy。

## 8. Backend 规划

### 8.1 `HostBackend`

用途：保持 CLI、本地测试和现有调用兼容。

- 可封装现有 `WorkspaceSandbox`、Seatbelt 和 Git 快照。
- Capability 明确报告 `runtime_tier=host`、无强多租户隔离。
- 仍执行环境白名单、路径限制、超时和证据记录。
- 安全 Profile 要求 Container 以上时必须拒绝，不得自动退回 Host。

### 8.2 `DockerBackend`

用途：本地交互、受控 Benchmark、CI 和正式隔离能力的第一阶段。

- 一个 Session/Episode 对应一个容器实例和一个独立工作区。
- 模型调用仍在宿主，容器不需要模型密钥。
- 本地交互允许 Session 级复用；Benchmark/RL 每 Episode 新建并最终销毁。
- 镜像构建与不可信执行分开；不允许 Agent 访问宿主 Docker Daemon。
- 正式证据记录镜像 digest、Docker 版本、有效 Profile 和能力报告。

Docker 共享 Linux 宿主内核，因此 `DockerBackend` 不自动满足 `multi_tenant`。
macOS/Windows 上 Docker Desktop 自身位于 VM 内，也不能由此推断不同 Agent 容器间
已获得独立 microVM 边界。

### 8.3 `GVisorBackend`

用途：自部署 Linux 上运行更不可信的代码。

- 复用 OCI/Docker Spec，使用 `runsc` 或集群 RuntimeClass 实现。
- Capability 报告 `runtime_tier=user_kernel`。
- 运行兼容性、系统调用、文件与网络性能必须通过任务矩阵验证。
- 不因使用 gVisor 而取消挂载、网络、秘密和资源策略。

### 8.4 远程商业/开源后端

候选包括 E2B、Daytona、AgentCore 或其他提供 SDK/API 的沙箱。统一适配器不得把
供应商营销名称映射为未经验证的安全等级，而应探测并记录具体能力：

- 实际隔离类型及所选 Region/Tier；
- 镜像与模板身份；
- 网络默认值和能否强制 allowlist；
- Snapshot/Pause/Resume 语义；
- 最大会话时间、并发和资源上限；
- 工作区、日志、快照和秘密的数据保留；
- 销毁完成证据与故障恢复语义。

远程后端首先作为实验适配器，不影响本地核心路径。是否成为默认后端由固定任务的
兼容性、延迟、失败率、成本和安全评审决定。

## 9. 生命周期、所有权与恢复

### 9.1 状态机

```text
NEW -> PREPARING -> READY -> RUNNING -> STOPPED -> RUNNING
          |            |         |          |
          +----------> FAILED <--+          +-> DESTROYED
                                  RUNNING ----------> DESTROYED
```

约束：

- `prepare()` 幂等；相同 owner 和 generation 不得创建重复实例。
- 每次重建增加 generation/incarnation，过期实例的退出事件不能覆盖新实例状态。
- 启动前先持有 Lease 并注册终止观察者，避免快速失败遗漏。
- `stop()` 可保留工作区；`destroy()` 必须清理容器、网络、临时卷和凭据租约。
- 超过 TTL、宿主重启或 Agent 取消后执行残留扫描与回收。
- 后端不可达、代理不可达或 Admission 失败时不执行不可信代码。

### 9.2 Owner 映射

| 使用者 | Sandbox 所有权 | 持久性 |
| --- | --- | --- |
| 普通 `claw agent`/chat | Agent Session | 可跨用户轮次暂停恢复 |
| Lifecycle/DevFlow | Lifecycle Session | 阶段快照，完成后按策略保留或销毁 |
| Episode/Benchmark | Episode ID | 每任务全新，Verification 后销毁 |
| SWE-bench | Instance + Run ID | Agent 与 Verifier 边界分离 |
| RL 分支采样 | Episode + Branch ID | 每分支独立快照/克隆，不共享可写层 |

### 9.3 Snapshot

- Snapshot 是性能和恢复能力，不等于安全边界。
- Snapshot 必须绑定 Spec Hash、基础镜像、Backend 和 owner。
- 恢复后重新应用当前最低安全 Profile；旧 Snapshot 不能降低新策略。
- 不快照真实秘密、代理临时凭据或 Verification 隐藏资产。
- Git Checkpoint 仍用于源代码历史与 Diff；Backend Snapshot 用于文件系统/依赖状态，
  两者不要混为同一证据。

## 10. 与现有 Claw 子系统的集成

### 10.1 Tool Registry 与 Executor

- `ToolExecutionContext` 增加 `sandbox_handle` 或等价执行上下文。
- `_bash()` 不再直接调用宿主 `subprocess.run()`，而是构造 `ExecRequest`。
- 文件工具通过 Backend FS API，或使用同一 Workspace Resolver 后访问明确映射的
  本地目录；不得接受未校验绝对宿主路径。
- 工具 Schema、Handler 参数和模型可见描述保持同步。
- blocked tools、aliases、permission callback 和 Runtime Event 顺序保持不变。

### 10.2 插件、Hook 与 MCP

所有扩展必须声明执行位置：

```text
host_trusted       Claw 随发行版的可信控制面代码
sandbox_untrusted  工作区命令、插件 command、构建或测试
external_service   独立 MCP/远程服务，由其自身身份和策略约束
```

- Workspace 提供的可执行内容默认归类为 `sandbox_untrusted`。
- Plugin virtual tool 的 `command` 通过 Sandbox Backend 执行。
- 声明式 Hook 可留在宿主；未来若允许工作区代码 Hook，必须进入沙箱。
- 外部 MCP 不因 Claw 有沙箱就自动变安全；其权限、数据和网络边界单独审计。

### 10.3 Episode、Verification 与隐藏测试

- `EpisodeOrchestrator` 请求 Backend 创建独立 workspace，而不是只创建目录。
- Agent 阶段没有 Hidden Test 挂载或引用。
- Verification 使用同一不可变候选快照的隔离克隆，并临时挂载 Hidden Test。
- Verifier 结束后移除测试资产并销毁克隆。
- Train/Dev/Test Family 隔离、Oracle Diff allowlist 和不可变 Trajectory 规则保持不变。

### 10.4 Pi 与其他 Runtime 对比

- Claw 与 Pi 必须运行在相同等级、同镜像和同网络策略的 Backend 上。
- `sandbox_attestation` 拆分为 `operator_claim` 与 Backend 自动生成的
  `enforcement_evidence`；报告明确二者可信度不同。
- Backend 无法提供等价能力时，Comparison 标为不可严格比较，不补写虚假声明。

## 11. 证据、可观测性与审计

每个 Sandbox 生命周期至少记录：

- Backend 名称、版本与 Capability；
- Sandbox/Owner/Generation ID；
- 原始与有效 Spec Hash；
- 镜像 digest 或模板版本；
- 有效挂载、网络、资源和安全 Profile；
- prepare/start/exec/stop/destroy 时间与结果；
- 超时、取消、OOM、资源拒绝和 Backend 错误类型；
- Snapshot 来源和恢复链；
- 清理是否确认完成；
- 操作方声明与自动执行证据的区分。

这些事件投影到 Trajectory v2 时保持追加式，不重写历史。详细 stdout/stderr 仍按
现有 Micro-compaction 处理；安全相关摘要、返回码和截断事实必须保留。

## 12. 失败语义

后端错误使用稳定分类，避免上层解析厂商字符串：

```text
spec_invalid
denied_by_policy
capability_unavailable
image_unavailable
backend_unavailable
resource_exhausted
start_failed
exec_timeout
exec_cancelled
instance_lost
snapshot_failed
destroy_incomplete
unknown
```

- Spec/Policy 错误不可重试，基础设施瞬态错误可按有界退避重试。
- 重试同一工具调用不得在指标中重复计数，但每次 Backend 尝试需要子事件证据。
- 不确定命令是否产生副作用时，不自动重放；先检查实例和调用 ID 状态。
- `destroy_incomplete` 必须进入残留回收队列并可被操作员观察。
- 不把“Backend 失联”解释成命令成功或安全销毁完成。

## 13. 从 NanoClaw 借鉴与不采用的部分

| NanoClaw 机制 | Claw 决策 | 原因 |
| --- | --- | --- |
| 声明式 Session Spec 与 Driver seam | 借鉴 | 解耦策略与 Docker/远程实现，支持能力协商 |
| 每 Session 容器与 idle kill | 借鉴 | 兼顾多轮状态和资源回收 |
| Claim/fencing、心跳、重启收养 | 分阶段借鉴 | 防止双重执行，适合长会话与远程后端 |
| Mount 分类、外部 allowlist、`realpath` | 借鉴 | 建立可审计、失败关闭的文件边界 |
| 运行时代码只读、Agent 非 root、移除 setuid | 借鉴 | 减少自修改与提权面 |
| 真实秘密不进容器、出站时代理注入 | 借鉴原则 | Claw 模型 Key 更应直接留在宿主；外部身份后续委派 |
| Internal network 强制代理 | 借鉴 | 防止原始 Socket 绕过 `HTTPS_PROXY` |
| 双 SQLite 单写者消息箱 | 暂不采用 | Claw 第一阶段为 Sandbox-as-tool，不需要完整 Agent-in-sandbox IPC |
| 完整 Agent Runner/渠道系统 | 不采用 | 与 Claw Runtime、Session 和扩展系统职责重复 |
| 多 Session 共享 Agent Group 可写目录 | Benchmark 不采用 | 会造成任务间状态和训练/评测污染 |
| 资源限制可选、Egress Lockdown 默认关闭 | 不照搬 | Benchmark 与不可信执行必须使用安全默认值 |

## 14. 分阶段实施计划

### M0：事实边界与兼容准备

- 新增本文档和术语约束。
- 盘点所有 `subprocess`、Shell、测试、Plugin command 和外部进程入口。
- 定义哪些是可信宿主操作，哪些必须进入 Sandbox。
- 对现有 `WorkspaceSandbox` 保持兼容，不扩大安全声明。

完成门槛：执行入口清单可审查，没有把临时目录误标为 OS 沙箱。

### M1：合同与 `HostBackend`

- **Implemented**：`SandboxSpec`、Capabilities、Backend、Handle、Exec Result 和稳定错误类型。
- **Implemented**：`HostBackend` 及 prepare/start/exec/stream/stop/destroy/inspect/list-owned 生命周期。
- **Implemented**：Agent Shell 和命令型 Plugin 经过 Session 所持有的 Backend Handle。
- **Implemented**：上述执行入口的子进程环境改为白名单；`PATH/HOME/TMPDIR`
  由后端构造。
- **Implemented**：Tool Result/Trajectory 记录 Spec Hash、Backend、Sandbox ID、超时和输出截断事实。
- **Implemented**：Docker Profile 强制 `restrict_workspace`；Host 仍保留兼容默认值。
- **Planned**：将 Episode、Task Suite、受控文件 API 和其他进程入口迁移到 Backend。

完成门槛：现有功能与测试兼容；Host 模式明确报告非强隔离；秘密环境回归测试通过。

### M2：`DockerBackend`

- **Implemented**：本地镜像检查、容器 prepare/start/exec/stop/destroy/inspect、超时强制停止和进程退出信息。
- **Implemented**：Agent/REPL/Resume 可用 `--sandbox-backend docker --sandbox-image ...`
  显式选择；镜像缺失或 Docker 不可用时失败关闭，不降级 Host。
- **Implemented**：Resume 未显式覆盖时继承 Session 中的 Backend 与镜像；只有显式
  选择才允许切换执行边界，防止 Docker 会话恢复为 Host。
- **Implemented**：Mount Admission、Owner 匹配、额外挂载根 allowlist、符号链接逃逸/特殊文件/目标冲突拒绝。
- **Implemented**：非 root、只读 RootFS、`cap-drop=ALL`、`no-new-privileges`、
  offline network 和 CPU/内存/PID/墙钟/输出限制的结构化 Docker argv。
- **Contract verified**：不依赖 Docker daemon 的合同和安全回归测试已通过。
- **Not security verified**：当前主机未安装 Docker；真实容器的挂载、网络、
  cgroup 和清理证据尚未运行。
- **Planned**：proxy-only/allowlist 网络控制器、可移植磁盘配额、真流式 exec 与跨进程残留回收。

完成门槛：安全回归矩阵和 Backend Contract Tests 通过；失败时不降级 Host。

### M3：Episode、Benchmark 与 SWE-bench

- **Implemented**：Episode 的初始检查和最终测试命令统一通过 Sandbox Backend；
  每个阶段使用新 Verifier Handle，并在阶段结束后确定性销毁。
- **Implemented**：Agent 与 Verifier 使用不同 Owner/Handle；Hidden Test 只在 Verifier
  命令期间暂存；Agent Handle 必须先销毁才会启动最终 Verifier，清理失败按基础设施
  失败处理，不继续验证。
- **Implemented**：`benchmark-run` 可显式选择 Docker；Benchmark Profile 强制
  `name@sha256:<digest>` 镜像、离线网络和 Agent/Verifier 独立 Sandbox。
- **Implemented**：Episode Manifest、Trajectory 和测试命令结果记录 Backend、Owner、
  Sandbox ID、Spec Hash、Profile、网络模式、镜像身份、生命周期清理和执行失败事实。
- **Contract verified**：Host 端到端回归、Backend 路由、隐藏测试阶段隔离、镜像固定
  准入与 CLI 传递测试通过。
- **Not security/benchmark verified**：当前主机无 Docker，尚未用真实容器执行固定 Pilot。
- **Planned**：官方 SWE-bench Harness、容器兼容任务镜像、并发 Episode 交叉污染矩阵。

完成门槛：固定 Pilot 在容器环境完成可复现运行；只有通过官方 Harness 的结果才可
称为官方 SWE-bench 结果。

### M4：gVisor 与远程适配 Spike

- 实现或验证 `GVisorBackend`。
- 选择一个远程候选实现实验 Backend。
- 用固定 20–50 个任务比较兼容性、启动/恢复延迟、失败率、成本和清理可靠性。

完成门槛：形成版本化对比报告；不因单一营销指标决定默认后端。

### M5：持久沙箱与委派身份

- 增加 Pause/Resume、Snapshot、Lease、Generation fencing 和残留回收。
- 对需要外部 API 的工具实现短期身份或强制代理凭据注入。
- 为高并发 Rollout 增加配额、背压和成本预算。

完成门槛：宿主重启、网络中断和重试竞态测试通过；真实秘密不出现在 Sandbox、
Snapshot、日志和 Trajectory 中。

## 15. 验证策略与验收标准

### 15.1 Backend Contract Tests

每个 Backend 共用同一组合同测试：

- prepare/start/exec/stop/destroy 状态转换；
- stdout/stderr/return code/timeout/取消保持；
- 文件 API 和 cwd 一致性；
- Capability 缺失失败关闭；
- 重复 prepare、stop、destroy 的幂等性；
- 残留发现与回收。

### 15.2 安全回归测试

至少覆盖：

- 读取宿主 `.env`、SSH、AWS/GCP/Kubernetes 配置失败；
- `../`、绝对路径、符号链接和嵌套挂载逃逸失败；
- Docker Socket、host network、privileged 和设备挂载被拒绝；
- 沙箱环境不存在模型 API Key；
- 原始 Socket 无法绕过 proxy-only；
- 云元数据和宿主网关不可达；
- Fork Bomb、磁盘填充、无限输出和后台进程受到限制；
- 一个 Episode 无法读取另一个 Episode 的文件或进程；
- Hidden Test 在 Agent 阶段不可见；
- Backend 中断不触发不确定副作用的自动重放。

### 15.3 完成定义

只有同时满足以下条件，某个 Profile 才能标记为 Implemented：

1. 代码存在且 Backend Contract Tests 通过；
2. 相应安全回归测试在真实 Backend 执行通过；
3. 文档说明适用范围和已知限制；
4. Trajectory/Verification 能记录实际执行证据；
5. 未满足的隔离性质不会由名称、注释或操作方字符串代替。

外部渗透测试、供应商审计或 microVM 本身的安全说明可以增强证据，但不能替代
Claw 的挂载、网络、凭据与集成测试。

## 16. 被否决或延后的方案

### 16.1 直接采用 NanoClaw

否决。NanoClaw 是带渠道、Session、Provider 和 Agent Runner 的完整 Harness，
与 Claw 架构层重叠；其 TypeScript/Bun、SQLite 消息箱和 OneCLI 集成不是独立
Sandbox SDK。适合作为参考实现，不适合作为核心依赖。

### 16.2 继续仅使用命令正则和临时目录

否决。它们不能阻止合法 Shell 语法读取宿主秘密、建立原始网络连接或执行资源
耗尽，无法支持“不可信执行”的证据声明。

### 16.3 第一阶段把完整 Agent 放进容器

延后。这样会把模型凭据、Session IPC、权限交互和 Runtime 恢复一并搬进新边界，
增加复杂度。只有远程长驻 Agent、离线自治或宿主最小化需求成立时再设计。

### 16.4 直接自研 Firecracker 平台

延后。Firecracker 只提供 microVM 原语，生产系统仍需宿主加固、镜像、网络、
Snapshot、调度、回收、日志和安全补丁。当前优先通过 Backend 接口接入成熟实现。

### 16.5 直接绑定单一商业服务

否决。Claw 同时存在本地交互、正式 Benchmark 和高并发 Rollout，负载特征不同；
先保持后端中立，再用固定任务和安全门槛选择部署组合。

## 17. 开放问题

- 第一版 Docker 镜像按语言维护，还是从任务 Manifest 声明镜像？
- 依赖安装应允许临时联网，还是通过预构建镜像/包缓存完成？
- 本地文件工具直接操作受控 bind mount，还是全部走容器内 FS API？
- macOS Seatbelt 是保留为 `HostBackend` 加固，还是逐步仅用于 Pi 等外部进程？
- Snapshot 的内容寻址、保留期限与磁盘预算如何进入 Experiment Registry？
- 商业后端的销毁完成如何形成可验证而非供应商字符串式证据？
- Plugin command 和外部 MCP 的默认执行位置如何向用户清晰展示？
- `untrusted_code` Profile 的最低边界选择 gVisor 还是 microVM，需要怎样的风险评审？

这些问题不阻塞 M1 合同设计，但必须在相应 Backend 成为正式默认值前解决。

## 18. 参考资料

- [NanoClaw repository](https://github.com/nanocoai/nanoclaw)
- [NanoClaw architecture](https://github.com/nanocoai/nanoclaw/blob/main/docs/architecture.md)
- [NanoClaw security model](https://docs.nanoclaw.dev/concepts/security)
- [NanoClaw session driver contract](https://github.com/nanocoai/nanoclaw/blob/main/src/drivers/types.ts)
- [Docker Sandboxes security model](https://docs.docker.com/ai/sandboxes/security/)
- [gVisor security architecture](https://gvisor.dev/docs/architecture_guide/intro/)
- [Firecracker design](https://github.com/firecracker-microvm/firecracker/blob/main/docs/design.md)
- [E2B open-source architecture and deployment options](https://e2b.dev/open-source)
- [Daytona architecture](https://www.daytona.io/docs/en/architecture/)
- [Amazon Bedrock AgentCore runtime lifecycle](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html)
