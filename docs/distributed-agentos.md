# Pure Distributed AgentOS Architecture

## Goal

One system can operate as:

- Local-only brain on a laptop.
- Cloud brain managing many sold robots over the public internet.
- Hybrid: cloud plans, edge executes, local console observes.

The robot does not know and must not care whether a capability command came from
a cloud service, a local brain, or an operator tool.

## Layers

```text
┌──────────────────────────────────────────────────────────┐
│ User / Developer Console                                  │
│ Web UI, Electron, mobile app                              │
│ Responsibilities: dialogue, plan review, telemetry/map    │
└───────────────────────────┬──────────────────────────────┘
                            │ HTTPS / WebSocket / MQTT
┌───────────────────────────▼──────────────────────────────┐
│ Distributed Control Plane (Brain)                         │
│ - LLM intent and orchestration                            │
│ - multi-robot TaskGraph                                   │
│ - approval, fleet, observability                          │
│ - MySQL / Redis / MQ / object storage                     │
└───────────────────────────┬──────────────────────────────┘
                            │ HTTP/gRPC/MQTT task contract
┌───────────────────────────▼──────────────────────────────┐
│ Edge Agent (optional local brain)                         │
│ - TaskSource adapter: cloud queue or local SQLite         │
│ - deterministic materialization                           │
│ - Robot Runtime Router                                    │
│ - lease, retry, recovery, telemetry uplink                │
└───────────────────────────┬──────────────────────────────┘
                            │ mTLS gRPC RobotRuntime
┌───────────────────────────▼──────────────────────────────┐
│ Robot Runtime                                             │
│ - capabilities, observations, skill execution             │
│ - Safety Supervisor                                       │
│ - direct XLeRobot / ROS 2 backend                         │
└───────────────────────────┬──────────────────────────────┘
                            │ USB / CAN / EtherCAT / ROS 2
┌───────────────────────────▼──────────────────────────────┐
│ Hardware / realtime controller                            │
└──────────────────────────────────────────────────────────┘
```

## Brain isolation invariant

`controlplane.Brain` is the only planning boundary.

`edge/runtime.Command` is the only execution boundary.

The wire protocol does not carry `brain_id` or `source`. A Robot Runtime cannot
tell and cannot depend on who created a command. It only sees:

- schema version
- task id
- command id
- capability
- parameters
- deadline
- lease
- idempotency key
- safety profile
- approval id

This is why a robot can be controlled by a local laptop today and a cloud
fleet tomorrow without changing robot firmware.

## Multi-robot node refresh

`core/taskgraph.GraphRuntime` is the event-driven runtime graph:

```text
node-a on robot-1 completed
  -> refresh dependents
  -> node-b on robot-2 becomes READY
```

Every `SkillStep` may carry:

```go
RobotID string
BrainID string
```

`edge/runtime.Router` maps `Command.RobotID` to a registered runtime client.
The default local installation registers one robot under `robot-local`.
A fleet control plane registers many robots under stable ids.

## Deployment profiles

| Profile | Brain | Edge Agent | Robot |
|---|---|---|---|
| Local developer | local brain | local worker | one MuJoCo/XLeRobot |
| Home user | cloud brain | thin edge worker | one XLeRobot |
| Fleet / paper | cloud brain + Redis/MQ | edge workers | many robots |
| Hybrid | cloud plans, local fallback brain | edge worker with failover | one or many robots |

## Current implementation

Already present:

- `controlplane.Brain` and `LocalBrain`
- `edge/runtime.Router`
- `SkillStep.RobotID`
- `runtime.Command.RobotID`
- `core/taskgraph.GraphRuntime` event-driven refresh
- `cmd/local-agent` wires the local robot through a Router

Implemented:

- cloud control-plane HTTP/gRPC server and database adapters → `cmd/fleet-control-plane` + `fleet/…`（HTTP API、MySQL 任务仓库、Redis 流队列、mTLS gRPC 网关）
- edge TaskSource client for cloud queue → `edge/cloudclient` + `edge/worker`（Redis Stream 直连或 HTTP 长轮询 `GET /v1/queue/next`）
- streaming telemetry → mTLS gRPC Link 双向流 + HTTP 遥测数据面（链路断开自动回退 `POST /v1/telemetry`）
- global map fusion and multi-robot console → `fleet/fusion` + Web 前端 fleet 模式（全局占用栅格/轨迹/实体）
- distributed fencing for shared workspaces → 意图声明租约（超时自动回收）+ 每机器人串行执行

工作区中的 `tangying-ai-operation-system/` 是此前分布式视频创作 AgentOS 的
参考实现。当前机器人系统借鉴其 cloud/local 分层、任务编排与 worker 思路，
但物理系统新增了它不需要解决的核心边界：版本化环境事实、工具与观测双目录、
资源 fencing、单写协调器和“只有世界后置条件成立才算完成”的 fail-closed 语义。

## 创新性与成熟度评价

系统的创新点不是泛化的“多 Agent”或地图界面，而是把以下边界组合成一个面向
实体机器人的 AgentOS：

1. Tool Catalog 与 Observation Registry 分离注册，Agent 既知道“能做什么”，
   也知道“能凭什么判断做完了”。
2. `world.snapshot.v1` 是 Console、协调器和后续 Harness Agent 的共同状态边界；
   物理工具返回成功后仍必须等待新鲜、连续、命令后的环境证据。
3. 单写协调器、事件/Outbox、幂等命令和共享资源 fencing 把多机器人协作从
   prompt chaining 提升为有一致性约束的分布式执行。
4. MuJoCo 与实机复用 Robot Runtime 工具/观测契约，云端与离线 Local Brain
   复用世界模型，因此替换部署位置或执行后端不需要改任务语义。

这套组合具有明显的系统创新价值，也为 Harness Agent 提供了正确的切入点；但
当前成熟度是“端到端研究/产品原型”，不是“生产级分布式一致性已经完成”。已
验证自然语言双机器人交接、真实进程断连恢复，以及八类确定性故障边界。上线前
仍必须补齐：

- WorldHub 快照、delta 游标和 edge source sequence 的跨重启持久化；
- leader fencing token 在业务状态提交时的同存储原子校验；
- MySQL 状态、Redis 资源租约和世界投影之间可恢复的资源转移 saga；
- 断网、进程崩溃、存储切主和消息重排的多进程长期 chaos，而不只是边界单测；
- 实机传感器质量、坐标标定、观测目录真实性和硬件安全验收。
