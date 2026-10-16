# 完整系统架构

2026-10-16 按实现提交 `821659df` 核对，软件发布版本仍为 v0.7.0。[新增目标 API](../guides/delegated-edge-goals.md)批准粗目标及工具/参数/预算包络，由 Local/Worker 共用的 Edge Host 在边缘生成、监督和验证子计划；原 `/v1/tasks` 与现有 UI/MCP 草案工具仍在审批前冻结完整能力序列。[完整长程说明](../architecture/long-horizon-collaboration.md)整合通讯、上下文、协作和[2026-10-15 实验](../experiments/2026-10-15-single-robot-robustness.md)，历史报告保留各自代码及证据边界。

**当前软件部署形态：云端 Server Agent 管全局目标与机群协作；Local Agent 和 Fleet Worker 是单机器人边缘 Agent 的两种接入方式，共用目标规划、诊断、受控恢复与执行核心。** 单机器人首版的受限工位与 Local Agent 路线仍保留。角色工具、模型与镜像边界见[Harness 升级规范](../superpowers/specs/2026-09-25-role-specific-agent-harness-docker-adr.md)和[软件验收](agent-harness-docker-acceptance.md)。单机感知/执行/恢复的具体链路见[RGB-D 闭环](../development/single-robot-loop.md)，历史相机数据见[证据存储](../development/observation-evidence.md)。家庭 RGB-D 参考路线的环境信息来自机载 RGB-D；严格异构路线由 Profile 声明传感器及规范观测，空间通道可接外部已注册三维测量。关节、夹爪、末端属于合法本体反馈。模拟器完整世界可供开发排错，不进入这条路线的目标绑定和视觉验证。

移动版本在 Runtime 后增加独立的 [RTAB-Map / Nav2 导航服务](../development/rtabmap-navigation.md)：双 RGB-D 与本体里程计进入 ROS，RTAB-Map 维护地图和定位，Nav2 规划并输出带时间戳速度。Runtime 保有唯一实际速度执行通道及停止检查；Agent 继续通过相同 `navigation.navigate` 工具获取结果和原始观测。相机画面和已观测导航地图由控制台只读展示，页面刷新不控制机器人执行。后续多机器人须为各自的 odom、传感器、导航命令和驱动保留身份隔离，并显式建立共同地图变换，不能直接拼接多个局部 odom。

## 1. 目标、创新点与非目标

Tangying Robot AgentOS 采用云端全局协调、边缘单机器人自治、Runtime 本机安全控制的分层架构。机器人完成硬件、感知、策略与安全集成后，Fleet 可批准目标包络并分配给 Worker，由边缘选择工具、编排和核验；也兼容中心冻结计划后的逐项派发。Local 使用同一目标核心独立运行。Harness Agent 不相信“工具调用成功”这一单一信号，而是基于权威 WorldModel、观测来源、时间新鲜度、坐标变换版本与资源 fencing 判断物理后置条件。这种“任务版本 + 工具活动 + 环境事实 + Harness 裁决”的闭环，是当前系统相对普通机器人遥控台的核心创新点。

非目标：本系统不会用软件急停替代实体急停；不会把仿真模型当作未经标定的实机真值；不会在观测陈旧、资源冲突或证据缺失时猜测成功；不会承诺单机开发栈已经具备跨地域生产 HA。

## 2. 云端与边缘部署形态

```text
云端 Fleet（机群任务权威）
Browser Console ─HTTPS/JWT/WS─> Fleet API
                                  │
                    Server Harness（系统状态 / 待审批草案）
                                  │
                    Task Service / Coordinator / EventLog+Outbox
                                  │
                    WorldHub / WorldModel / Harness Agent
                                  │
                         mTLS FleetGateway gRPC
                         ┌────────┴────────┐
                     Worker robot-1    Worker robot-2 ...N
                         │                │
                     Edge Host        Edge Host
                         │ 能力执行器 / mTLS gRPC
                    Robot Runtime      Robot Runtime
                         │                │
                    实机或仿真工具      实机或仿真工具

Orin NX 单机自治（本机模型和依赖可用时可离线）
Browser Console/API ─loopback HTTP─> Local Agent + 同一 Edge Host + SQLite
                                      │
                             同一 RobotRuntime gRPC
                                      │
                             实机或 MuJoCo
```

云端与本地共享 `robot.v1` Runtime、工具目录、Observation 契约、`world.snapshot.v1` 和任务体验模型；`internal/edgeagent.Host` 还统一批准目标的规划、检查点、监督和完成验证。Worker 不再要求云端预列目标的所有工具调用，但也未继承 Local 全部 `agentruntime` 总线。`internal/agentharness` 用同一决策循环装配不同角色。Server 只看到机群只读查询与待审批草案工具；Edge 只看到单机器人读、本地和受审批的物理写工具。云端的 `AGENT_SYSTEM_*` 与意图/规划、边缘的意图/规划/恢复模型分别配置；边缘可通过受限 Assist 凭据调用云端只读推理。云端使用多进程、多机器人、事件/Outbox/lease 协调；Local Brain 在 Orin 或开发机以 SQLite 和单进程队列完成单机闭环。`local-agent` 与 `edge-worker` 不可同时控制同一 Runtime。云端与 Orin 的 Compose 均从 `Dockerfile.agent` 构建；Orin 的 `edge`/`fleet` profile 互斥。

## 3. 模块职责

### 异构机器人边界

新适配器采用 `RobotProfile → PluginBackend → RobotRuntimeService`：Profile 声明机械结构、传感器和规范工具子集；handler 使用厂商 SDK/ROS 2 完成动作；provider 完成真实识别、三维重建和世界坐标转换。Runtime 复用原有审批、租约、日志、取消和急停，新增按设备显式动作键/单位/上下限校验。`robot.profile.v1` 与 `scene.reconstruction.v1` 通过可选 protobuf Struct 跨语言传递，Go Agent 在遥测和 grounding 前再次验证。

重建实体已经是世界坐标，Edge 不再施加一次部署偏移；设备 `base_pose` 仍按局部部署坐标处理。原始传感器 frame/type、变换版本与采集时间保持可追溯。多传感器逐源注册，空实体观测可发布源健康，点云保留在受限重建数据中，不伪造成物体或占据地图。详细字段、流程和边界见[异构接入手册](../development/robot-adapters.md)。

MCP 是 Fleet 的统一任务/查询入口，使用现有鉴权与机器人能力目录。MCP 创建任务仍未审批，不暴露自动审批或裸关节动作。完整时序是 `MCP host → MCP stdio bridge → Fleet API → 已审批任务 → Edge → Runtime → 适配器`；[工具清单与配置](../../robot/mcp/README.md)描述实际 8 个工具。导航/移动机械臂的 Runtime 接口不代表自然语言编排已支持所有机器人任务领域。

| 模块 | 目录/进程 | 具体功能 | 核心设计 |
| --- | --- | --- | --- |
| Agent / Parser / Planner | `agent/`, `orchestration/` | 将自然语言解析为目标、任务步骤和机器人绑定 | 配置 GOAL 模型时按注册能力规划并校验；无 GOAL 模型时保留完整句式的保守离线语法，歧义要求澄清；旧 Intent/Planner 路线独立保留 |
| Task Service | `tasks/` | 创建、审批、取消、版本提议、CAS 确认、状态投影 | Task 是聚合；Revision 不可变；`expectedRevision` / `expectedCurrentRevision` 防止覆盖并发更新 |
| Coordinator | `fleet/coordinator/` | 领取步骤、分派机器人、寻找安全点、资源转移、恢复 | 单写协调；leader lease；command/step/revision/fencing 多重身份 |
| EventLog / Outbox | `fleet/eventlog/`, `fleet/mysql/` | 持久化领域事件并可靠发布可执行工作 | 提交事实与投影分离；消费者幂等；cursor 可重放 |
| Resource custody | `fleet/coordinator/`, `worldmodel/` | 维护方块等独占资源 owner 与 fencing token | token 只能单调增加；旧持有者不能以旧命令继续写入 |
| Fleet API | `fleet/` | 操作员 HTTP/WS、设备数据面、任务工作面 | 操作员 JWT 与设备凭证分离；WS 使用一次性 ticket |
| FleetGateway | `fleet/gateway/`, `proto/fleet/v1` | Edge 注册、心跳、状态、观测和服务端命令 | 公网通道只允许 mTLS；robot ID 与证书/注册身份绑定 |
| Edge Host | `internal/edgeagent/` | Task 生成子计划、Ops 读账本诊断、RecoveryPolicy 建议，Host 校验后调用执行器 | 原目标批准固定、完整历史检查额度、持久预算、未知效果禁止重放 |
| Edge Worker / Local | `edge/worker/`、`internal/localapp/` | 两种入口承载同一 Host；Worker 接 Fleet/claim/回执，Local 接本地 API/事件运行时 | 单一 Runtime 控制者；网络租约、SQLite 检查点、目录与安全合同 |
| Robot Runtime | `edge/runtime/`, `robot/gateway/`, `proto/robot/v1` | 统一工具执行、观测流、取消、急停 | 仿真/实机只替换 Adapter；命令需 deadline、幂等键、fencing |
| Tool Registry | Runtime/Fleet 注册目录 | 描述工具名、用途、安全参数、输入输出和可用性 | UI 展示人话；Agent 只调用本 revision 已注册工具 |
| Observation Registry | `core/observation/`, Fleet 注册 | 描述传感器/场景源、schema、frame、频率、新鲜度预算 | Harness 在运行前知道“应观察什么、多久算过期” |
| WorldHub / WorldModel | `core/worldmodel/`, `fleet/worldhub/` | 合并机器人状态、环境实体、地图、资源和来源健康 | source sequence 去重；revision 单调；陈旧/矛盾 fail closed |
| Harness Agent | `core/harness/` | 将步骤后置条件与可信世界证据匹配 | 不接受自报成功；输出 reason、evidenceIds、worldRevision |
| Web Console | `web/`, `console/` | 展示输入理解、步骤、工具活动、异常恢复和数字孪生 | 专业证据折叠；World 同版本仅新鲜度降级；任务 Experience 同版本完整快照可更新进展 |
| RoboCasa Adapter | `sim/robocasa/` | 共享 MuJoCo 厨房、两个 XLeRobot、方块交接和观测 | 与实机共用工具/观测契约；模型资产按 SHA-256 绑定 |

## 4. 权威数据流

### 4.1 目标委托路径

批准由操作员通过任务服务完成；云端/边缘模型只能提议，不能自批或扩权。

1. 操作员从目标目录获得 `catalogRevision`，提交带 `idempotencyKey` 的 `robot.objective.v1`。服务端校验机器人、工具/参数包络、效果、`resources=["robot"]`、全部只读完成条件及预算，创建未批准的单个 `edge.goal` 父节点。改合同复用键返回 409；设备凭据不能创建或批准。
2. 使用原任务审批入口批准父包络。Fleet 协调器分配父 claim，Worker 在目标全生命周期续租；Local 由本地任务权威准入。同一 Runtime 只接受一种入口。
3. Host 读取本机目录和授权语义状态，Task 产生最多 16 calls 的子计划，验证后保存子计划到独立的 `edge-child/<childId>` 聚合（计划固定、事件只追加）。每次规划、读取和步骤在工作前预留预算；子计划变化不改变父授权摘要。
4. 执行器使用原确定性命令身份、安全、短 lease、grounding 和物理闭环。Ops 从账本诊断；RecoveryPolicy 仅对可证明无效果的失败提出重规划建议。Host 拒绝越权、预算耗尽及未知物理效果。
5. 全部子动作确认且所有完成条件精确成立后，保存目标已验证结果，再补传已申报子计划、逐步验证和父完成回执。云端按原 claim、时间、配额、目录与设备证据核验；回传失败只重发证据，不重做动作。

合同 v1 的上限为 8 代计划、128 次子能力调用、4,096 次 Host 业务读取和 86,400 秒。复合 `robot.task` 内物理步骤、相机帧、控制周期另有计量与限制；完成谓词中的固定地图身份本身不能证明路线已经走完。详见[目标架构](../architecture/hierarchical-edge-objectives.md)。

### 4.2 兼容冻结计划路径

1. 用户提交自然语言；Task Service 创建 revision 1，Task 初态 `READY`、`approved=false`。

   解析先保留明确的机器人、物体、起点和终点；已识别的否定、条件与不完整理解返回澄清错误。创建成功还不代表场景可执行，实体唯一性与指定起点在 `edge/robotclient.Ground` 根据 Runtime 观测检查。当前 RoboCasa 的单向回合与重新授权限制见[评测报告](../development/natural-language-evaluation.md)。
2. Local Console 先复述理解与步骤，再单独审批；Fleet 页面“创建并开始”会依次调用创建与审批。审批写入 `approved=true` 与 `TASK_APPROVED` 事件，不是独立 `APPROVED` Task 状态。
3. Coordinator 领取可运行步骤，检查 robot lease、catalog revision、WorldModel freshness 和资源 owner。
4. Edge 将 `SkillCommand` 交给 Runtime。Runtime 以 `command_id + idempotency_key` 去重，并流式返回 ACCEPTED/RUNNING/OBSERVATION/终态。
5. 观测通过 Edge 进入 WorldHub，按 source sequence、frame 与 transform revision 投影为新的 WorldSnapshot revision。
6. Harness Agent 从 WorldSnapshot 取证。只有后置条件满足才把步骤标为 `SATISFIED`，随后资源 custody 以新 fencing token 转给下一机器人。
7. 全部步骤满足且最终环境 owner 正确后，任务进入 `SUCCEEDED`。

### 4.3 兼容任务的运行中更新

1. 用户输入“最后放到右侧蓝色垫子上”；服务端基于当前 revision 生成 revision 2 预览。
2. `changeSet` 标明保留步骤、变更步骤与取消步骤；用户确认时必须提交 `expectedCurrentRevision` 和 UUID `idempotencyKey`。
3. 若机器人正持有方块，revision 2 进入 `WAITING_SAFE_POINT`；Coordinator 不强行中断不可逆动作。
4. 到达工具边界、资源安全释放点或明确可取消点后，Coordinator 激活 revision 2；旧 revision 后续事件不会覆盖新事实。
5. Console 的更新轨道持续显示“已理解 → 等待安全动作 → 新版本已启用”，刷新后仍可由 API 重建。

目标 API 本版没有已批准包络的原地修改入口。改变工具、效果或资源权限应先停止并对账旧目标，再创建并批准新目标，不能把兼容任务的改版界面理解成可以扩大一个执行中目标的权限。

### 4.4 恢复

持久化配置下，Task/EventLog/Outbox 用存储恢复投影，Edge 重新注册目录。WorldHub 设置 `FLEET_WORLD_SNAPSHOT_PATH` 时保存单主 checkpoint 和源序列，恢复后只接受更新的证据；未设置时使用内存世界。重启后 delta 环形缓存不恢复，客户端需要 REST resync。Coordinator 必须取得有效 leader lease 与资源 token 才能继续。任何无法证明身份、顺序、地图版本或资源所有权的工作都保持等待/失败关闭。

Host 检查点和完整子账本必须保留。已完成子步骤的独立 StepCompleted 与完整 CAPABILITY_VERIFIED 同时存在时，可重验当前授权/目录后补认父确认；其他 pending/未知状态停止对账。完成条件瞬态读取全目标最多两次持久重试；读确认落盘被中断则要求输入。完成报告 pending 只补传证明，失去原 claim 不能继续动作，也没有自动离线授权。

## 5. 数据所有权与一致性

| 数据 | 权威写者 | 一致性 |
| --- | --- | --- |
| Task/Revision/领域事件 | Task Service 单聚合提交 | revision/aggregate version CAS；历史不可改 |
| 边缘子计划/检查点/完整回执 | 共享 Host + 边缘 SQLite | 原子 CAS；预算跨代/重启累积，模型进度摘要不是权威 |
| 可执行队列 | Outbox → Redis Stream | 至少一次投递；消费者幂等 |
| leader/device/resource lease | Coordinator/Fleet | 有期限；续租失败停止新动作 |
| tool/observation catalog | Edge 注册，Fleet 记录 | revision 精确匹配；漂移时拒绝命令 |
| WorldSnapshot | WorldHub 投影 | 全局 revision 单调；源内 sequence 单调；同 revision 不改事实 |
| Harness verdict | Harness Agent | 绑定 evidence IDs、World revision、task/step/revision |
| 浏览器状态 | 只读投影 | 丢 revision 时 REST resync；旧 socket/旧 fetch 被 generation fence |

系统采用“局部强一致 + 跨组件幂等/补偿”的模型，而不是假设跨 MySQL、Redis、机器人和物理世界存在一个全局事务。

## 6. 安全边界

- 操作员：HTTPS 登录换短期 JWT；WebSocket 先申请一次性 ticket；当前身份边界是 operator/device；完整细粒度 RBAC 需额外实现。
- 机器人：FleetGateway 与 Runtime 均使用 mTLS；设备 HTTP 凭证按 robot 独立；禁止共享全 fleet token。
- 命令：deadline、approval、catalog revision、world basis、task revision、aggregate version、resource ID、fencing token 和幂等键必须共同有效。
- 观测：来源必须预注册；source sequence、frame/transform revision、时间戳、质量和 provenance 都参与 Harness 取信。
- 前端：CSP、同源资产、模型 hash、旧 revision 丢弃；视觉失败不改变 `WORLD LIVE`，而是降级语义 Canvas。
- 物理：实体急停、电源隔离、速度/力矩/工作区限制高于软件状态；详见[仿真到实机](sim-to-real.md)。

## 7. 扩展与成熟度

Fleet 与 Edge 的身份契约可表达多台机器人，当前验证以限定双机器人场景为主。`FLEET_WORLD_SNAPSHOT_PATH` 是同机单主快照，进程级文件锁阻止第二写者；损坏或无法保存时失败关闭。它不提供跨主机共识、自动 HA 或完整 delta 历史。按 world/tenant 分片和读取扩展是未来部署工程，不能直接多开当前写进程。生产扩展需要外置 MySQL/Redis、对象存储帧、可观测性、备份恢复演练和 leader fencing 与业务提交的同存储原子化。当前 RoboCasa 证明的是完整软件闭环与分布式失效边界，不是大规模容量或实机安全认证。

## 8. 学习型工具执行边界

抓取/放置不在 Agent 内硬编码关节序列。Edge 把本地 Runtime 遥测投影为版本化 ObservationBundle，按冻结 PolicyManifest 调用确定性仿真、VLA、模仿学习或强化学习 Provider。候选 action chunk 先由 Edge 做身份、兼容性、长度和数值边界校验，再由 Runtime 做硬件安全与幂等执行；最后仍由 Harness Agent 依据新 WorldSnapshot 完成确认。

模型进程不拥有 Task、资源 lease、fencing token、Runtime journal 或 WorldModel 的写权限。这个三段信任边界使模型可升级而不改变分布式一致性语义，也使未知执行结果只能进入环境对账，不能盲目重放物理命令。完整契约见[学习型策略工具](policy-tools.md)。
