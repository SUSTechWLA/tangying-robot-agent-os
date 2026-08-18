# 当前系统架构

**状态：本地优先、分层 Runtime 与可插拔 Middleware 架构，2026-08-18 起生效。**

本页描述当前实现。完整决策与故障语义见[本次分层设计规范](superpowers/specs/2026-08-18-layered-runtime-middleware-design.md)，实施证据见[分层改造计划](superpowers/plans/2026-08-18-layered-runtime-middleware.md)。它们与此前的[本地优先规范](superpowers/specs/2026-08-18-local-first-runtime-design.md)和[实施计划](superpowers/plans/2026-08-18-local-first-runtime.md)均为长期开发设计资产，不因后续重构而删除。

## 运行拓扑

```text
用户 / 浏览器
  -> 笔记本 Local Agent（单个 Go 进程，127.0.0.1:8787）
       - Console / API / LLM Agent / 任务编排
       - Robot Capability Client
       - Middleware ports
           -> SQLite：任务、事件、执行恢复状态
           -> Memory：有界任务队列、进程内事件
       -> 用户选择的 OpenAI-compatible LLM API（可选）
       -> mTLS gRPC（由笔记本主动连接）
            树莓派 Robot Runtime（单个 Python 服务）
              - 语义能力、低频状态、命令生命周期
              - Safety Supervisor、短租约看门狗、急停锁存
              -> direct XLeRobot SDK（默认）或 ROS 2 backend（可选）
                   -> 驱动 / ros2_control / 实时控制器
                        -> USB、控制板、舵机和传感器
```

云端只承担用户选择的 LLM/VLM API 推理，不保存任务运行态，也不参与机器人控制。业务状态的唯一权威默认是笔记本 SQLite；树莓派只保留有界的幂等与急停安全日志。

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

Middleware 是应用与 Runtime 的横向基础设施能力，不位于每条机器人调用的串行路径中。当前默认只选择真正需要的 SQLite 和内存实现；PostgreSQL、Redis、Kafka 未加入运行依赖。

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
- `edge/robotclient` 是 Go 侧 protobuf/gRPC 唯一适配器。
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
3. 确定性代码生成 command ID、幂等键、deadline、短 lease、approval ID 和 safety profile；模型不能覆盖这些字段。
4. 树莓派 Safety Supervisor 再次检查版本、白名单、期限、lease、动作键和值域。
5. 驱动/实时控制器继续执行标定、速度/位置/电流限制、轨迹插值与硬件故障保护。
6. 断线或笔记本休眠时，树莓派在 lease 到期后停止；不确定的物理步骤不会自动重放。
7. 远程只能触发急停；解除锁存要求现场操作员。

线协议见 [`proto/robot/v1/robot.proto`](../proto/robot/v1/robot.proto)，行为不变量见[协议说明](protocols.md)，树莓派与主机交互及部署见[快速部署指南](install/robot-pi-quick.md)。
