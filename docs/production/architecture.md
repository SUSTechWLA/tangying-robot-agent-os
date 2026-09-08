# 完整系统架构

**2026-09-08 首版交付主线：一个机器人、一个受限工位、一个 Local Agent 主控。** 下文 Fleet 架构继续作为多机器人扩展路线。当前单机感知/执行/恢复的具体链路见[RGB-D 闭环](../development/single-robot-loop.md)，历史相机数据见[证据存储](../development/observation-evidence.md)。环境信息只来自机载 RGB-D；关节、夹爪、末端属于合法本体反馈。模拟器完整世界可供开发排错，不进入这条路线的目标绑定和视觉验证。

移动版本在 Runtime 后增加独立的 [RTAB-Map / Nav2 导航服务](../development/rtabmap-navigation.md)：双 RGB-D 与本体里程计进入 ROS，RTAB-Map 维护地图和定位，Nav2 规划并输出带时间戳速度。Runtime 保有唯一实际速度执行通道及停止检查；Agent 继续通过相同 `navigation.navigate` 工具获取结果和原始观测。相机画面和已观测导航地图由控制台只读展示，页面刷新不控制机器人执行。后续多机器人须为各自的 odom、传感器、导航命令和驱动保留身份隔离，并显式建立共同地图变换，不能直接拼接多个局部 odom。

## 1. 目标、创新点与非目标

Tangying Robot AgentOS 是云端优先的分布式机器人 AgentOS。机器人完成硬件、感知、策略与安全集成后，云端 Fleet 接收自然语言，生成有版本的多机器人任务图，将动作下发到机器人侧工具，并持续接收机器人和环境观测。Harness Agent 不相信“工具调用成功”这一单一信号，而是基于权威 WorldModel、观测来源、时间新鲜度、坐标变换版本与资源 fencing 判断物理后置条件。这种“任务版本 + 工具活动 + 环境事实 + Harness 裁决”的闭环，是当前系统相对普通机器人遥控台的核心创新点。

非目标：本系统不会用软件急停替代实体急停；不会把仿真模型当作未经标定的实机真值；不会在观测陈旧、资源冲突或证据缺失时猜测成功；不会承诺单机开发栈已经具备跨地域生产 HA。

## 2. 两种部署形态

```text
云端 Fleet（主形态）
Browser Console ─HTTPS/JWT/WS─> Fleet API
                                  │
                    Task Service / Coordinator / EventLog+Outbox
                                  │
                    WorldHub / WorldModel / Harness Agent
                                  │
                         mTLS FleetGateway gRPC
                         ┌────────┴────────┐
                     Edge robot-1      Edge robot-2 ...N
                         │ mTLS gRPC        │
                    Robot Runtime      Robot Runtime
                         │                │
                    实机或仿真工具      实机或仿真工具

Local Brain（无网络）
Browser Console ─loopback HTTP─> Local Agent + SQLite
                                      │
                             同一 RobotRuntime gRPC
                                      │
                             实机或 MuJoCo
```

云端与本地共享 `robot.v1` Runtime、工具目录、Observation 契约、`world.snapshot.v1` 和任务体验模型。区别在于云端使用多进程、多机器人、事件/Outbox/lease 协调；Local Brain 在一台可信笔记本内以 SQLite 和单进程队列完成同类闭环。

## 3. 模块职责

### 异构机器人边界

新适配器采用 `RobotProfile → PluginBackend → RobotRuntimeService`：Profile 声明机械结构、传感器和规范工具子集；handler 使用厂商 SDK/ROS 2 完成动作；provider 完成真实识别、三维重建和世界坐标转换。Runtime 复用原有审批、租约、日志、取消和急停，新增按设备显式动作键/单位/上下限校验。`robot.profile.v1` 与 `scene.reconstruction.v1` 通过可选 protobuf Struct 跨语言传递，Go Agent 在遥测和 grounding 前再次验证。

重建实体已经是世界坐标，Edge 不再施加一次部署偏移；设备 `base_pose` 仍按局部部署坐标处理。原始传感器 frame/type、变换版本与采集时间保持可追溯。多传感器逐源注册，空实体观测可发布源健康，点云保留在受限重建数据中，不伪造成物体或占据地图。详细字段、流程和边界见[异构接入手册](../development/robot-adapters.md)。

MCP 是 Fleet 的统一任务/查询入口，使用现有鉴权与机器人能力目录。MCP 创建任务仍未审批，不暴露自动审批或裸关节动作。完整时序是 `MCP host → MCP stdio bridge → Fleet API → 已审批任务 → Edge → Runtime → 适配器`；[工具清单与配置](../../robot/mcp/README.md)描述实际 8 个工具。导航/移动机械臂的 Runtime 接口不代表自然语言编排已支持所有机器人任务领域。

| 模块 | 目录/进程 | 具体功能 | 核心设计 |
| --- | --- | --- | --- |
| Agent / Parser / Planner | `agent/`, `orchestration/` | 将自然语言解析为目标、任务步骤和机器人绑定 | 完整已知意图优先确定性；歧义要求澄清；可选 LLM 处理其余表达，Planner 另有确定性后备 |
| Task Service | `tasks/` | 创建、审批、取消、版本提议、CAS 确认、状态投影 | Task 是聚合；Revision 不可变；`expectedRevision` / `expectedCurrentRevision` 防止覆盖并发更新 |
| Coordinator | `fleet/coordinator/` | 领取步骤、分派机器人、寻找安全点、资源转移、恢复 | 单写协调；leader lease；command/step/revision/fencing 多重身份 |
| EventLog / Outbox | `fleet/eventlog/`, `fleet/mysql/` | 持久化领域事件并可靠发布可执行工作 | 提交事实与投影分离；消费者幂等；cursor 可重放 |
| Resource custody | `fleet/coordinator/`, `worldmodel/` | 维护方块等独占资源 owner 与 fencing token | token 只能单调增加；旧持有者不能以旧命令继续写入 |
| Fleet API | `fleet/` | 操作员 HTTP/WS、设备数据面、任务工作面 | 操作员 JWT 与设备凭证分离；WS 使用一次性 ticket |
| FleetGateway | `fleet/gateway/`, `proto/fleet/v1` | Edge 注册、心跳、状态、观测和服务端命令 | 公网通道只允许 mTLS；robot ID 与证书/注册身份绑定 |
| Edge Worker | `edge/` | 连接 Fleet 与单台 Runtime，执行工具、上报观测和结果 | 网络重连、lease、幂等、catalog revision 和 safety profile |
| Robot Runtime | `edge/runtime/`, `robot/gateway/`, `proto/robot/v1` | 统一工具执行、观测流、取消、急停 | 仿真/实机只替换 Adapter；命令需 deadline、幂等键、fencing |
| Tool Registry | Runtime/Fleet 注册目录 | 描述工具名、用途、安全参数、输入输出和可用性 | UI 展示人话；Agent 只调用本 revision 已注册工具 |
| Observation Registry | `core/observation/`, Fleet 注册 | 描述传感器/场景源、schema、frame、频率、新鲜度预算 | Harness 在运行前知道“应观察什么、多久算过期” |
| WorldHub / WorldModel | `core/worldmodel/`, `fleet/worldhub/` | 合并机器人状态、环境实体、地图、资源和来源健康 | source sequence 去重；revision 单调；陈旧/矛盾 fail closed |
| Harness Agent | `core/harness/` | 将步骤后置条件与可信世界证据匹配 | 不接受自报成功；输出 reason、evidenceIds、worldRevision |
| Web Console | `web/`, `console/` | 展示输入理解、步骤、工具活动、异常恢复和数字孪生 | 专业证据折叠；World 同版本仅新鲜度降级；任务 Experience 同版本完整快照可更新进展 |
| RoboCasa Adapter | `sim/robocasa/` | 共享 MuJoCo 厨房、两个 XLeRobot、方块交接和观测 | 与实机共用工具/观测契约；模型资产按 SHA-256 绑定 |

## 4. 权威数据流

### 4.1 创建与执行

1. 用户提交自然语言；Task Service 创建 revision 1，Task 初态 `READY`、`approved=false`。

   解析先保留明确的机器人、物体、起点和终点；已识别的否定、条件与不完整理解返回澄清错误。创建成功还不代表场景可执行，实体唯一性与指定起点在 `edge/robotclient.Ground` 根据 Runtime 观测检查。当前 RoboCasa 的单向回合与重新授权限制见[评测报告](../development/natural-language-evaluation.md)。
2. Local Console 先复述理解与步骤，再单独审批；Fleet 页面“创建并开始”会依次调用创建与审批。审批写入 `approved=true` 与 `TASK_APPROVED` 事件，不是独立 `APPROVED` Task 状态。
3. Coordinator 领取可运行步骤，检查 robot lease、catalog revision、WorldModel freshness 和资源 owner。
4. Edge 将 `SkillCommand` 交给 Runtime。Runtime 以 `command_id + idempotency_key` 去重，并流式返回 ACCEPTED/RUNNING/OBSERVATION/终态。
5. 观测通过 Edge 进入 WorldHub，按 source sequence、frame 与 transform revision 投影为新的 WorldSnapshot revision。
6. Harness Agent 从 WorldSnapshot 取证。只有后置条件满足才把步骤标为 `SATISFIED`，随后资源 custody 以新 fencing token 转给下一机器人。
7. 全部步骤满足且最终环境 owner 正确后，任务进入 `SUCCEEDED`。

### 4.2 运行中更新

1. 用户输入“最后放到右侧蓝色垫子上”；服务端基于当前 revision 生成 revision 2 预览。
2. `changeSet` 标明保留步骤、变更步骤与取消步骤；用户确认时必须提交 `expectedCurrentRevision` 和 UUID `idempotencyKey`。
3. 若机器人正持有方块，revision 2 进入 `WAITING_SAFE_POINT`；Coordinator 不强行中断不可逆动作。
4. 到达工具边界、资源安全释放点或明确可取消点后，Coordinator 激活 revision 2；旧 revision 后续事件不会覆盖新事实。
5. Console 的更新轨道持续显示“已理解 → 等待安全动作 → 新版本已启用”，刷新后仍可由 API 重建。

### 4.3 恢复

持久化配置下，Task/EventLog/Outbox 用存储恢复投影，Edge 重新注册目录。WorldHub 设置 `FLEET_WORLD_SNAPSHOT_PATH` 时保存单主 checkpoint 和源序列，恢复后只接受更新的证据；未设置时使用内存世界。重启后 delta 环形缓存不恢复，客户端需要 REST resync。Coordinator 必须取得有效 leader lease 与资源 token 才能继续。任何无法证明身份、顺序、地图版本或资源所有权的工作都保持等待/失败关闭。

## 5. 数据所有权与一致性

| 数据 | 权威写者 | 一致性 |
| --- | --- | --- |
| Task/Revision/领域事件 | Task Service 单聚合提交 | revision/aggregate version CAS；历史不可改 |
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
