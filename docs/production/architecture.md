# 完整系统架构

## 1. 目标、创新点与非目标

Tangying Robot AgentOS 是云端优先的分布式机器人 AgentOS。用户购买机器人并联网后，云端 Fleet 接收自然语言，生成有版本的多机器人任务图，将动作下发到机器人侧工具，并持续接收机器人和环境观测。Harness Agent 不相信“工具调用成功”这一单一信号，而是基于权威 WorldModel、观测来源、时间新鲜度、坐标变换版本与资源 fencing 判断物理后置条件。这种“任务版本 + 工具活动 + 环境事实 + Harness 裁决”的闭环，是当前系统相对普通机器人遥控台的核心创新点。

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

| 模块 | 目录/进程 | 具体功能 | 核心设计 |
| --- | --- | --- | --- |
| Agent / Parser / Planner | `agent/`, `orchestration/` | 将自然语言解析为目标、任务步骤和机器人绑定 | LLM 可选；确定性解析器兜底；输出进入不可变 Revision |
| Task Service | `tasks/` | 创建、审批、取消、版本提议、CAS 确认、状态投影 | Task 是聚合；Revision 不可变；`baseRevision` 防止覆盖并发更新 |
| Coordinator | `coordinator/` | 领取步骤、分派机器人、寻找安全点、资源转移、恢复 | 单写协调；leader lease；command/step/revision/fencing 多重身份 |
| EventLog / Outbox | `eventlog/`, `store/` | 持久化领域事件并可靠发布可执行工作 | 提交事实与投影分离；消费者幂等；cursor 可重放 |
| Resource custody | `coordinator/`, `worldmodel/` | 维护方块等独占资源 owner 与 fencing token | token 只能单调增加；旧持有者不能以旧命令继续写入 |
| Fleet API | `fleet/` | 操作员 HTTP/WS、设备数据面、任务工作面 | 操作员 JWT 与设备凭证分离；WS 使用一次性 ticket |
| FleetGateway | `fleetgateway/`, `proto/fleet/v1` | Edge 注册、心跳、状态、观测和服务端命令 | 公网通道只允许 mTLS；robot ID 与证书/注册身份绑定 |
| Edge Worker | `edge/` | 连接 Fleet 与单台 Runtime，执行工具、上报观测和结果 | 网络重连、lease、幂等、catalog revision 和 safety profile |
| Robot Runtime | `runtime/`, `proto/robot/v1` | 统一工具执行、观测流、取消、急停 | 仿真/实机只替换 Adapter；命令需 deadline、幂等键、fencing |
| Tool Registry | Runtime/Fleet 注册目录 | 描述工具名、用途、安全参数、输入输出和可用性 | UI 展示人话；Agent 只调用本 revision 已注册工具 |
| Observation Registry | `observation/`, Fleet 注册 | 描述传感器/场景源、schema、frame、频率、新鲜度预算 | Harness 在运行前知道“应观察什么、多久算过期” |
| WorldHub / WorldModel | `worldmodel/`, `worldhub/` | 合并机器人状态、环境实体、地图、资源和来源健康 | source sequence 去重；revision 单调；陈旧/矛盾 fail closed |
| Harness Agent | `harness/` | 将步骤后置条件与可信世界证据匹配 | 不接受自报成功；输出 reason、evidenceIds、worldRevision |
| Web Console | `web/`, `console/` | 傻瓜式展示输入理解、步骤、工具活动、异常恢复和数字孪生 | 默认只讲人话；专业证据折叠；相同 revision 只允许新鲜度降级 |
| RoboCasa Adapter | `sim/robocasa/` | 共享 MuJoCo 厨房、两个 XLeRobot、方块交接和观测 | 与实机共用工具/观测契约；模型资产按 SHA-256 绑定 |

## 4. 权威数据流

### 4.1 创建与执行

1. 用户提交自然语言；Task Service 创建 revision 1，初态 `PENDING_APPROVAL`。
2. Console 用简单中文复述理解、两个步骤和即将使用的能力；用户审批后进入 `APPROVED`。
3. Coordinator 领取可运行步骤，检查 robot lease、catalog revision、WorldModel freshness 和资源 owner。
4. Edge 将 `SkillCommand` 交给 Runtime。Runtime 以 `command_id + idempotency_key` 去重，并流式返回 ACCEPTED/RUNNING/OBSERVATION/终态。
5. 观测通过 Edge 进入 WorldHub，按 source sequence、frame 与 transform revision 投影为新的 WorldSnapshot revision。
6. Harness Agent 从 WorldSnapshot 取证。只有后置条件满足才把步骤标为 `SATISFIED`，随后资源 custody 以新 fencing token 转给下一机器人。
7. 全部步骤满足且最终环境 owner 正确后，任务进入 `SUCCEEDED`。

### 4.2 运行中更新

1. 用户输入“最后放到右侧蓝色垫子上”；服务端基于当前 revision 生成 revision 2 预览。
2. `changeSet` 标明保留步骤、变更步骤与取消步骤；用户确认时必须提交 `baseRevision` 和幂等键。
3. 若机器人正持有方块，revision 2 进入 `WAITING_SAFE_POINT`；Coordinator 不强行中断不可逆动作。
4. 到达工具边界、资源安全释放点或明确可取消点后，Coordinator 激活 revision 2；旧 revision 后续事件不会覆盖新事实。
5. Console 的更新轨道持续显示“已理解 → 等待安全动作 → 新版本已启用”，刷新后仍可由 API 重建。

### 4.3 恢复

进程重启后，Task/EventLog/Outbox 重放投影；Edge 重新注册工具和观测目录；WorldHub 只接受比已知 source sequence 更新的证据；Coordinator 必须取得新 leader lease 与资源 token 才能继续。任何无法证明身份、顺序、地图版本或资源所有权的工作都保持等待/失败关闭。

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

- 操作员：HTTPS 登录换短期 JWT；WebSocket 先申请一次性 ticket；RBAC 区分查看、审批、取消、急停。
- 机器人：FleetGateway 与 Runtime 均使用 mTLS；设备 HTTP 凭证按 robot 独立；禁止共享全 fleet token。
- 命令：deadline、approval、catalog revision、world basis、task revision、aggregate version、resource ID、fencing token 和幂等键必须共同有效。
- 观测：来源必须预注册；source sequence、frame/transform revision、时间戳、质量和 provenance 都参与 Harness 取信。
- 前端：CSP、同源资产、模型 hash、旧 revision 丢弃；视觉失败不改变 `WORLD LIVE`，而是降级语义 Canvas。
- 物理：实体急停、电源隔离、速度/力矩/工作区限制高于软件状态；详见[仿真到实机](sim-to-real.md)。

## 7. 扩展与成熟度

Fleet API、WorldHub、Coordinator 和 Edge 的契约支持 N 台机器人；按 world/tenant 分片可横向扩展读取和观测，但每个任务聚合与资源仍需单写者。生产扩展需要外置 MySQL/Redis、对象存储帧、可观测性、备份恢复演练和 leader fencing 与业务提交的同存储原子化。当前 RoboCasa 证明的是完整软件闭环与分布式失效边界，不是大规模容量或实机安全认证。
