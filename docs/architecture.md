# 当前系统架构

> 本页保留架构演进背景。当前生产交付的模块边界、数据流和一致性模型见[完整系统架构](production/architecture.md)。

当前单机器人移动主线已提供 [双 RGB-D / RTAB-Map / Nav2 接入](development/rtabmap-navigation.md)。地图、导航、动作策略与 Agent 分层；页面的观测点云是局部相机测量，独立“导航地图”展示 SLAM 结果，二者不能混称全知环境。

**状态：云端 Fleet 为联网主形态，独立 Local Brain 为离线形态。当前实现与限制以[完整架构](production/architecture.md)和[V1 状态](production/v1-release-status.md)为准。**

本页描述当前实现。完整决策与故障语义见[本次分层设计规范](superpowers/specs/2026-08-18-layered-runtime-middleware-design.md)，与此前的[本地优先规范](superpowers/specs/2026-08-18-local-first-runtime-design.md)一起保留为长期设计资产，不因后续重构而删除。当时的实施清单不随发布分发。

World/Harness 的设计依据保留在[分布式设计](superpowers/specs/2026-08-20-distributed-agentos-world-harness-design.md)；设计档案与历史测试记录不自动证明当前发布通过。

## 主要运行拓扑

```text
用户 / Cloud Console
  -> Fleet control plane
       - 自然语言 Agent、持久任务图、单写 leader、Outbox
       - Tool Catalog + Observation Registry + WorldHub
       - MySQL / Redis（生产）或内存适配器（测试）
       -> mTLS Fleet Link -> Edge Worker（每台机器人）
            -> Robot Runtime（MuJoCo / XLeRobot direct / ROS 2）
                 -> Safety Supervisor -> 驱动、执行器、传感器

无网络：Local Console -> Local Brain + SQLite -> 同一个 Robot Runtime
```

云端是联网机器人的任务与世界状态权威。任何跨机器人推进都要求新鲜、稳定、可追溯的 `WorldSnapshot` 证据，并使用资源 fencing token 阻止旧协调器或迟到命令。Local Brain 不参与云端一致性域，但复用同一快照与 Runtime 契约。

仿真部署保持同一边界，只替换 Robot Runtime 的后端：

```text
./bin/robot-agent start sim
  -> MuJoCo Robot Runtime（固定官方 XLeRobot 模型 + 语义工具）
  -> Local Agent（同一个 edge/runtime 客户端与 Runner）
       -> 启动/周期 Observe -> TelemetryHub + Scene Frame Cache -> Console
```

Agent 不根据“仿真/实机”分支编排业务逻辑。切换环境只改变 Runtime endpoint、adapter 身份和安全配置；命令仍通过 `edge/runtime.Command`，结果仍通过 `edge/runtime.Result`。MuJoCo 与实机 adapter 都不能让学习策略构造 approval、deadline、lease、幂等键或 safety profile。

## 六层边界

| 层 | 负责 | 明确不负责 |
| --- | --- | --- |
| Agent / Orchestration | 意图理解、任务规划、能力选择、审批与异常恢复 | ROS 消息、原始传感器、高频控制、硬件保护 |
| Robot Runtime / Capability | `navigate`、`move_arm`、`pick`、`place`、状态查询等语义命令与结果 | LLM 推理、具体 Topic/Action、舵机循环 |
| Middleware | 持久化、队列、事件、缓存、协调锁、Trace 的稳定端口 | 机器人命令必经的串行代理 |
| ROS 2 / Robot SDK | 感知、SLAM、导航、机械臂和生态集成 | Agent API 与业务状态权威 |
| Realtime / Safety | deadline、lease、看门狗、限位、轨迹和急停 | 自然语言决策 |
| Hardware | 控制板、执行器、实体急停和传感器 | 软件层策略 |

Middleware 是应用与 Runtime 的横向基础设施能力，不位于每条机器人调用的串行路径中。Local Brain 使用 SQLite 与内存实现；Fleet 则使用 `fleet/mysql`、`fleet/redis`、事件/Outbox 和可选单主世界快照。不能把 Local 的无数据库服务依赖理解成 Fleet 不需要 MySQL/Redis。

## 代码依赖方向

```text
agent / orchestration / tasks / edge/agent
        -> edge/runtime + middleware contracts + domain ports

cmd/local-agent（composition root）
        -> middleware/sqlite + middleware/memory + edge/robotclient

edge/robotclient
        -> gRPC / generated protobuf

RobotRuntimeService（wire mapper）
        -> semantic runtime models -> SafetySupervisor -> RobotBackend
        -> XLeRobot direct backend OR ROS 2 backend -> controller / hardware
```

- `tasks.Repository` 由消费方定义；`middleware/sqlite.Store` 同时实现任务仓库和 `middleware.ExecutionStore`。
- `edge/agent.Runner` 只依赖 `ExecutionStore`、`Grounder` 和 `runtime.Invoker`。
- `internal/localapp.App` 接收 `middleware.Queue[string]`，默认由 `middleware/memory` 提供有界队列。
- `edge/robotclient` 是 Go 侧 RobotRuntime protobuf/gRPC 适配器；FleetGateway 的传输实现在 `edge/cloudclient` / `fleet/gateway`。
- Python `RobotBackend`、Safety、direct backend 和 ROS backend 使用纯语义 dataclass；只有 `service.py` 映射 protobuf。
- 自动架构测试通过 `go list -json` 阻止核心包重新引入 SQLite、PostgreSQL、Redis、Kafka、gRPC 或生成协议类型。

新增或替换基础设施时，只新增 `middleware/<adapter>` 并在 `cmd/local-agent` 装配；核心 Agent 接口保持不变。详细规则见[Middleware 适配指南](middleware.md)。

## 状态与传感器数据流

```text
Camera / LiDAR / IMU / Joint State（机器人侧高频）
  -> 驱动、ROS 2 感知、SLAM、状态估计与融合
  -> 有界 Robot State / Semantic State / Scene Entity（低频）
  -> Robot Runtime
  -> Agent grounding、恢复判断与 Console 展示
```

Agent-facing 类型不包含 ROS Topic、Action、QoS、图像帧、点云、IMU sample 或关节控制流。需要调试原始数据时应进入机器人侧专用诊断工具，不进入任务事件总线。

## 仿真可观测闭环与训练边界

MuJoCo Runtime 以 `TabletopWorld` 为状态权威：有界运动控制器驱动关节和底座自由度，attachment controller 只在末端到达容差后建立持有关系，语义工具执行抓取、验证、放置和恢复。低频 Observe 同时返回机器人/实体状态和可选 PNG；画面渲染失败只产生 anomaly，不改变动作结果。

NumPy Q-learning 模块复用同一语义工具目录，学习有限状态下的工具顺序。checkpoint 带 state/action schema 版本和工具目录 fingerprint，不匹配时失败关闭。首个里程碑不包含关节级 PPO/SAC、视觉策略或实机 sim-to-real 标定。

固定模型 revision 为 `3d14695e40c9c68229c0aacffca6053c75cd3eb6`。这是官方 XLeRobot 模型的可重复集成版本，不宣称是最新双轮硬件的标定数字孪生。

## 安全路径

1. Local Agent 要求物理任务经过用户审批。
2. Runner 刷新机器人能力、完成实体 grounding 并验证计划。
3. 确定性代码生成 command ID、幂等键、deadline、短 lease、approval ID 和 safety profile；模型不能覆盖这些字段。任务声明的 adapter 必须与 RuntimeInfo.adapter 一致，防止把 MuJoCo 执行误报为实体成功。
4. 树莓派 Safety Supervisor 再次检查版本、白名单、期限、lease、动作键和值域。
5. 驱动实施已实现的标定、命名关节范围、相对目标限制与停止处理；速度/电流/碰撞等实际硬件保护需独立配置验证，不因接口存在视为完成。
6. 断线或笔记本休眠时，树莓派在 lease 到期后停止；不确定的物理步骤不会自动重放。
7. 远程只能触发急停；解除锁存要求现场操作员。

线协议见 [`proto/robot/v1/robot.proto`](../proto/robot/v1/robot.proto)，行为不变量见[协议说明](protocols.md)，树莓派与主机交互及部署见[快速部署指南](install/robot-pi-quick.md)。
