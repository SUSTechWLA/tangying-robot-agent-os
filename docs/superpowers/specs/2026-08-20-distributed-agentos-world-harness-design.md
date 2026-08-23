# 分布式 Robot AgentOS、世界模型与 Harness 设计

**日期：** 2026-08-20

**状态：** 已完成交互式设计确认，等待书面审阅

**重点部署：** 云端 Fleet；本地端作为无网 Local Brain 画像

**首个闭环：** 两台 MuJoCo 机器人通过共享交接区传递方块

## 1. 目标

本轮把当前已能运行的 Fleet 演示闭环提升为可恢复、可观测、可验证、可迁移到实机的分布式 Robot AgentOS 基线：

1. 用户购买机器人并联网后，可以直接通过云端对话创建任务；云端负责自然语言理解、多机器人任务图、协调、状态融合和用户端实时展示。
2. 无网络环境使用 Local Brain；Local Brain 与云端复用同一 Agent Core、工具契约、观测契约和世界状态语义，只替换持久化与传输适配器。
3. 仿真与实机共享同一 Tool Registry。当前工具下发给 MuJoCo adapter，后续注册 XLeRobot、ROS 2 或其他实机 adapter，不改变云端任务协议。
4. 机器人状态、环境状态和任务事件分别采集并统一投影为版本化 `WorldSnapshot`。用户端和未来 Harness Agent 消费同一份世界事实。
5. 在 MuJoCo 中跑通“自然语言 → 任务图 → robot-1 放入交接区 → 环境验证 → 所有权转移 → robot-2 取走并放到目标区”的完整闭环。
6. 确定性注入分布式故障，证明任务不会假成功、世界版本不会倒退、资源不会出现双重所有权、协调器可恢复。
7. 用户端实时显示语义 3D 数字孪生和相机证据，支持左键平移、右键旋转、滚轮缩放、双击聚焦和刷新恢复。

## 2. 当前项目评价

### 2.1 已有基础

当前工作树已存在一批尚未提交的 Fleet 实现，模块测试和真实双 MuJoCo 端到端用例已经证明以下能力能够运行：

- 云端 Fleet HTTP API、MySQL 任务仓库、Redis 队列与缓存、mTLS gRPC 设备链路；
- 两个 edge-worker 分别连接两台 MuJoCo Robot Runtime；
- 一句话按 `robot_id` 切分成两个串行 intent；
- robot-1 与 robot-2 各执行七步 manipulation 计划；
- 遥测、PNG 场景帧、融合占用栅格与 `/v1/world` 快照；
- 双机器人任务最终进入 `SUCCEEDED`，并能验证物体落点。

基线验证证据：

- `make test`：Go 全包测试通过；Python 246 项通过；Web 3 项通过；
- `.venv/bin/pytest tests/e2e/test_fleet_cloud.py -q -vv`：2 项真实双 MuJoCo Fleet 端到端测试通过。

### 2.2 当前不足

现状仍属于演示级 Fleet，而不是 Harness-ready 的分布式 AgentOS：

- 协调器的关键 intent 运行态主要在进程内存中，无法证明进程切换后的唯一推进；
- 环境实体、机器人状态、画面与占用栅格被合并到低频遥测样本，没有独立的观测源、单调序列和世界版本；
- `/v1/world` 是读取时临时拼装的快照，没有可重放的投影器、event cursor 或 UNKNOWN 语义；
- 当前任务只验证两个独立 pick/place intent，没有实现共享方块、共享交接区、对象租约和 fencing；
- 前端 Fleet 模式主要依赖 1.5–5 秒轮询，God View 不支持左键平移、右键旋转和以指针为中心的缩放；
- 当前异常测试主要覆盖模块错误，没有覆盖动作后崩溃、协调器切换、Redis 故障、观测乱序和 stale fencing token；
- 文档同时保留“本地优先、云端退出默认产品”和“Fleet 云端”两套产品叙述，需要明确新方向：云端是重点产品画像，本地端服务无网场景。

### 2.3 创新判断

“云端管理多机器人”本身不是足够独特的创新声明。系统真正有价值的组合创新是：

- 云端 Brain、本地 Brain 与 Robot Runtime 在同一命令边界上隔离；
- Tool Registry 和 Observation Registry 分离，仿真与实机只替换 adapter；
- 环境状态是一等公民，并形成用户端与 Harness Agent 共享的版本化世界模型；
- 任务推进由环境谓词验证，而不是把工具返回成功等同于物理完成；
- 多机器人对象交接通过世界状态、对象租约、fencing 和可恢复事件日志保持一致；
- 同一套系统同时支持联网即用的云端产品与无网 Local Brain。

这构成很强的产品与系统架构创新点，也适合作为论文系统贡献。是否属于学术或专利意义上的“巨大原创创新”，必须在实现完成后结合最新 cloud robotics、multi-robot task allocation、robot world model 和 agent harness 工作做正式检索与对比，不能仅凭工程完成度宣称。

## 3. 范围

### 3.1 本轮包含

- 当前 Robot AgentOS 内的云端/本地公共契约；
- 持久化任务事件、checkpoint、outbox 和单写者协调器；
- Tool Registry、Observation Registry 和请求级不可变目录快照；
- 世界观测事件、世界投影、快照和增量流；
- 共享方块与交接区资源租约；
- 两机器人交接任务及确定性异常注入；
- 语义 3D Console 和浏览器验收；
- MuJoCo 完整实现与实机 adapter 示例/契约测试。

### 3.2 本轮不包含

- 具体相机型号、真实 SLAM、目标检测模型或多传感器标定实现；
- 机械臂到机械臂的空中直接递交；
- 多主协调器；
- Kubernetes、跨地域容灾和大规模性能压测；
- 把旧视频 AgentOS 的业务包、Kafka 拓扑或 Electron 客户端直接复制到机器人项目；
- 在仿真没有物理证据时伪造实机可用性。

## 4. 选定路线

采用“借鉴成熟契约、在当前机器人项目内重新实现”的路线：

- 保留当前 Go 控制平面、Python MuJoCo Runtime、静态 Web Console、MySQL、Redis 和现有 RobotRuntime gRPC 边界；
- 从 `tangying-ai-operation-system/` 借鉴不可变工具目录、`targetRunnerId + catalogRevision`、lease/idempotency、checkpoint、outbox、版本化 observability event 和 effectively-once terminal delivery；
- 不共享视频业务模型，不引入视频项目的 artifact、shot、workflow stage 或本地文件语义；
- 对机器人领域新增 Observation Registry、World Projector、对象租约、WorldPredicate 和物理恢复协议。

## 5. 总体架构

```text
Cloud Console / Harness Agent
  │ HTTPS snapshot + WebSocket world deltas
  ▼
Fleet API / Realtime Gateway
  ├─ Agent Core
  │    Context → Tools → Constrain → Verify → Correct
  ├─ immutable ToolCatalogSnapshot
  ├─ single-writer TaskGraph Coordinator
  ├─ Object Lease Manager
  ├─ World Projector
  └─ durable backbone
       MySQL: tasks, domain events, outbox, checkpoints, snapshots
       Redis: streams, leader/device/resource leases, cache
       frame store: camera evidence bytes
  │ mTLS task stream / observation stream / commands
  ▼
Edge Agent
  ├─ Task Inbox
  ├─ Tool Registry
  ├─ Observation Registry
  ├─ Safety + Atomic Executor
  └─ adapter
       MuJoCo now / XLeRobot or ROS later
```

云端画像和本地画像共享 `agentcore`、`toolcontract`、`observation`、`worldmodel`、`taskgraph` 和 `runtime` 接口：

| 责任 | 云端画像 | Local Brain 画像 |
| --- | --- | --- |
| 事件持久化 | MySQL | SQLite |
| 队列/租约 | Redis + MySQL outbox | 进程内队列 + SQLite fencing |
| 机器人连接 | 公网 mTLS gateway | 本机/局域网 mTLS |
| 世界增量 | WebSocket | 本地 WebSocket |
| Agent Core | 相同 | 相同 |
| 工具/观测 adapter | 相同接口 | 相同接口 |

## 6. Tool Registry

### 6.1 ToolManifest

每个可执行工具只有一个规范定义：

```text
name
description
input_schema
output_schema
capability
execution_plane = edge
approval_mode
timeout
retry_policy
side_effect_class
adapter_binding
```

`side_effect_class` 至少区分：

- `read_only`：观测和解析；
- `idempotent`：相同 key 可安全重放；
- `physical_atomic`：有物理副作用，只能通过命令幂等与环境 reconciliation 恢复；
- `emergency`：急停或取消，优先于普通任务。

### 6.2 ToolCatalogSnapshot

Edge heartbeat 广告安全工具摘要和目录 SHA-256 revision。每个任务在规划时绑定不可变快照：

```text
robot_id
runner_id
catalog_revision
adapter_id
adapter_version
tools[]
created_at
```

目录变化后，已编译任务不静默调用不同工具。执行前 revision 不匹配时返回稳定错误 `TOOL_CATALOG_STALE`，刷新能力并重新规划。

### 6.3 CommandEnvelope

每个下发命令包含：

```text
schema_version
task_id
node_id
command_id
robot_id
tool_name
arguments
catalog_revision
world_revision_basis
resource_id
fencing_token
idempotency_key
deadline
approval_id
safety_profile
```

Robot Runtime 不接收 `brain_id` 或“来自云端/本地”的来源字段。它只验证工具、参数、安全、期限、幂等和 fencing。

## 7. Observation Registry

工具与观测是两类不同扩展：

- Tool adapter 改变世界；
- Observation adapter 描述世界。

Observation adapter 示例：

- `mujoco_ground_truth`；
- `robot_proprioception`；
- `rgbd_camera`；
- `lidar`；
- `slam_pose`；
- `tool_result_evidence`；
- `operator_annotation`。

每个 adapter 广告：

```text
source_id
source_type
schema_revision
frame_ids
transform_revision
update_rate_hz
freshness_budget_ms
payload_kinds
adapter_version
```

实机接入至少需要注册 Tool adapter、Observation adapter 和坐标变换，不允许只注册动作工具而没有可验证环境结果的观测源。

## 8. ObservationEnvelope

统一观测事件：

```json
{
  "schemaVersion": "world.observation.v1",
  "observationId": "obs-...",
  "worldId": "world-demo",
  "sourceId": "robot-1/mujoco-ground-truth",
  "robotId": "robot-1",
  "sourceType": "sim_ground_truth",
  "sourceSequence": 1234,
  "observedAt": "2026-08-20T00:00:00.000Z",
  "receivedAt": "2026-08-20T00:00:00.032Z",
  "frameId": "world",
  "transformRevision": "scene-r2-v1",
  "kind": "entity_upsert",
  "payload": {},
  "confidence": 1.0,
  "quality": {"latencyMs": 32, "anomalies": []},
  "causation": {"taskId": "task-...", "commandId": "cmd-..."},
  "provenance": {"adapter": "mujoco", "version": "...", "sensor": "ground-truth"}
}
```

规则：

- `sourceSequence` 在单个 `sourceId` 内严格单调；
- 相同 `observationId` 幂等；
- 旧 sequence 不覆盖新状态；
- `observedAt` 用于判断事实时间，`receivedAt` 用于测量网络延迟；
- 坐标变换 revision 不匹配时不参与世界融合；
- 图片/深度帧字节不进入领域事件，事件只保存不可变 `frameRef`、MIME、尺寸、哈希和时间；
- 未观测、过期或冲突结果表示 UNKNOWN/DEGRADED，不能默认为 free/false/不存在。

## 9. World Projector 与 WorldSnapshot

World Projector 是单写者、确定性的事件 reducer。它消费 observation、tool result、device presence、resource lease 和 task events，输出：

```json
{
  "schemaVersion": "world.snapshot.v1",
  "worldId": "world-demo",
  "revision": 1842,
  "eventCursor": "evt-...",
  "projectedAt": "2026-08-20T00:00:00.050Z",
  "robots": {},
  "entities": {},
  "resources": {},
  "activeTasks": [],
  "health": {
    "degradedSources": [],
    "conflicts": []
  }
}
```

每个机器人和实体状态包含来源引用、最后观测时间、freshness、confidence 和坐标系。`WorldSnapshot.revision` 全局单调，只在接受有效事件后增加。

HTTP 提供完整 snapshot；WebSocket 提供按 revision 排序的 `WorldDelta`。客户端发现 revision gap 时停止应用增量，重新取 snapshot，再从 cursor 继续。

Harness Agent API 读取 snapshot 和 delta，并能声明 WorldPredicate：

```text
entity(block).inside(handoff-zone)
entity(block).stable_for(2 observations)
robot(robot-1).held == none
resource(block).owner == environment
source(robot-1/vision).fresh_within(500ms)
```

任务推进前必须在最新世界 revision 上重新计算 predicate。

## 10. 持久化协调与事件模型

### 10.1 单写者

- Redis leader lease 保证任一时刻只有一个 TaskGraph Coordinator 推进状态；
- fencing epoch 随 leader 变更递增；
- API、查询、帧和 WebSocket 服务可以多实例；
- 失去 leader lease 的旧实例不能提交新 task event。

### 10.2 领域事件

领域事件至少包含：

```text
event_id
aggregate_type
aggregate_id
aggregate_version
event_type
payload
idempotency_key
causation_id
correlation_id
occurred_at
actor
```

任务、intent、资源租约和世界投影均使用单调 aggregate version 做乐观并发控制。

### 10.3 Outbox 与恢复

- task state、domain event 和 outbox row 在同一 MySQL 事务提交；
- Redis stream 发布失败时 outbox 保留，恢复后补发；
- checkpoint 保存当前任务图、目录 snapshot、世界 predicate、资源 lease 和 event cursor；
- 新协调器从最近 checkpoint 加之后续事件恢复；
- 物理命令只提供 effectively-once 语义：允许网络重放，但命令幂等、fencing 和观测 reconciliation 保证物理副作用不重复。

## 11. 方块交接任务

自然语言示例：

```text
让1号机器人把红色方块放到交接区，再让2号机器人接过方块并放到右侧目标区。
```

任务图：

```text
observe-world
  -> acquire(block, handoff-zone) for robot-1
  -> robot-1 pick(block)
  -> verify(robot-1 held == block)
  -> robot-1 place(block, handoff-zone)
  -> verify(block inside zone AND stable AND robot-1 held == none)
  -> commit BLOCK_AVAILABLE
  -> transfer block lease robot-1 token 41 -> robot-2 token 42
  -> robot-2 pick(block)
  -> verify(robot-2 held == block AND handoff-zone empty)
  -> robot-2 place(block, target-zone)
  -> verify(block inside target-zone AND stable)
  -> SUCCEEDED
```

核心不变量：

1. 方块只有一个当前 owner；
2. 新 fencing token 产生后旧 token 永久无效；
3. 工具 success 不能直接推进依赖物理结果的节点；
4. 观测不新鲜时进入 `WAITING_FOR_OBSERVATION`；
5. 重复命令、事件和回调不产生额外副作用；
6. 交接区中的方块由环境托管，接收机器人离线时不得让发送机器人擅自取回；
7. 外力改变世界时，以新观测为准，重新 grounding/replan。

## 12. 任务恢复状态

在现有任务终态之外新增或明确以下运行态：

- `WAITING_FOR_OBSERVATION`：缺少满足 freshness/confidence 的世界证据；
- `RECOVERING`：正在从 lease expiry、重连或 physical reconciliation 恢复；
- `BLOCKED`：需要用户、硬件或新机器人介入；
- `FAILED_SAFE`：无法继续但物理系统已安全停驻。

允许的典型路径：

```text
RUNNING
  -> WAITING_FOR_OBSERVATION
  -> RECOVERING
  -> RUNNING
  -> SUCCEEDED
```

超出重试/恢复预算只能进入 `BLOCKED` 或 `FAILED_SAFE`，不能假成功解锁后续节点。

## 13. 异常注入矩阵

| 故障 | 检测 | 恢复 | 关键断言 |
| --- | --- | --- | --- |
| 观测重复/乱序/延迟 | source sequence、时间 | 去重/拒绝；过期变 UNKNOWN | world revision 不倒退 |
| Edge 断网/重连 | heartbeat lease | 当前原子动作后 safe stop；cursor 补事件 | 无离线新命令 |
| 动作后、回报前 worker 崩溃 | command lease + 世界变化 | reconcile；相同 key 不重复动作 | 方块只移动一次 |
| 协调器崩溃/切换 | leader lease | checkpoint + event replay | 单一 task advance |
| Redis 不可用 | stream/lease 写失败 | MySQL outbox；停止新调度；恢复补发 | durable commit 优先 |
| stale fencing/双重争抢 | token 不匹配 | Edge 和云端拒绝；刷新世界 | 方块唯一 owner |
| robot-2 交接后离线 | device lease | checkpoint；兼容机器人可重新认领 | 方块留在交接区 |
| 相机丢帧/UI 重连 | frame age/revision gap | camera stale；snapshot + cursor | 语义世界不回退 |
| 物体被移走/抓取失败 | WorldPredicate 失效 | 重观测、grounding、replan、预算耗尽 BLOCKED | 工具结果不覆盖世界 |

故障注入点：`TaskSource`、`ObservationSink`、Robot Runtime client、Clock、Event Store、Redis publisher、Realtime Gateway。每个场景固定 seed、触发步骤、期望事件序列和最终不变量。

## 14. 用户端

### 14.1 主视图

用户端以语义 3D 数字孪生为主，相机画面为证据辅助：

- 中心：机器人、方块、交接区、目标区、轨迹、朝向、租约 owner；
- 右侧：任务节点、predicate、恢复状态、观测 freshness；
- 辅助：各机器人相机帧和 stale 标记；
- 顶部：world revision、delta latency、连接状态；
- 事件面板：按 correlation/task/robot 筛选。

### 14.2 交互

- 左键拖动：沿视平面平移 target；
- 右键拖动：绕 target 调整 yaw/pitch，并禁用浏览器右键菜单；
- 滚轮：以指针所在世界位置为锚缩放；
- 双击实体：聚焦机器人或物体；
- `F`：复位并自动适配当前世界 bounds；
- 浏览器刷新：相机 pose 写入本地存储，snapshot 重载后恢复；
- pointer capture 保证拖出 canvas 后仍正确结束操作。

### 14.3 实时性

- MuJoCo 结构化观测目标 10–20 Hz；
- WorldDelta WebSocket 最高 15 Hz；
- 客户端对连续快照插值到 60 FPS；
- 相机证据自适应 2–10 FPS；
- 世界状态与相机分别显示 latency/freshness；
- revision gap、WebSocket 超时或 source stale 时停止增量并重新同步，不能继续展示旧状态为 LIVE。

纯 Canvas/WebGL 语义场景使用本地代码和项目内资源，不依赖 CDN，保证云端自托管和无网 Local Brain 都能展示。

## 15. 实机接入

新增实机不修改 Agent Core、Coordinator、World Projector 或 Console，只注册：

1. Tool adapter：动作工具、输入/输出 schema、安全和幂等语义；
2. Observation adapter：机器人状态、实体、栅格、相机 frameRef；
3. Transform provider：`map/world/base/camera/gripper` 坐标变换及 revision；
4. Capability/observation catalog heartbeat；
5. 驱动级 fencing 与 command journal；
6. 物理 readiness 和急停状态。

实机 adapter 必须通过共同契约测试：

- tool catalog revision 稳定；
- command schema 与 output schema；
- duplicate command 幂等；
- stale fencing 拒绝；
- observation sequence 单调；
- stale/unknown 状态；
- transform revision；
- emergency stop；
- physical success 由世界 predicate 验证。

## 16. 安全与隐私

- 公网机器人只通过 mTLS gateway 连接；证书身份绑定 robot id；
- 设备 token 不替代 mTLS 设备身份；
- LLM 不能生成 fencing token、deadline、approval、idempotency key 或安全 profile；
- 命令参数和观测 schema 有大小、深度和字段限制；
- 相机帧独立存储、短时保留、按用户/机群授权；
- 可观测事件只保存必要元数据、哈希、稳定错误码和引用，不记录密钥；
- 急停与本地安全监督优先于云端任务；
- 网络分区时不接收新动作，当前物理原子动作完成或被本地安全逻辑中止后停驻。

## 17. 测试策略

### 17.1 单元与契约

- ToolCatalog canonical hash 与 stale revision；
- ObservationEnvelope validation、dedupe、reorder、freshness；
- World reducer 的 revision、UNKNOWN、conflict、transform revision；
- resource lease 与 fencing；
- event store、outbox、checkpoint 和 replay；
- camera transform：pan/orbit/zoom/focus/reset；
- Local Brain 和 Cloud 使用相同测试向量。

### 17.2 集成

- MySQL transaction + outbox；
- Redis stream、leader lease、resource lease；
- mTLS device identity；
- Edge reconnect/cursor replay；
- coordinator failover；
- frameRef 与 world delta 独立 freshness。

### 17.3 端到端

正常场景和九类故障均运行真实双 MuJoCo、双 worker 和 Fleet control plane。每次产生证据包：

```text
task.json
events.jsonl
world-final.json
leases.json
robot-1-command-journal.json
robot-2-command-journal.json
faults.json
console-screenshot.png
```

### 17.4 浏览器验收

自动测试验证：

- WebSocket snapshot/delta 顺序与重连；
- 左键只平移、右键只旋转、滚轮以指针锚点缩放；
- 刷新后恢复 view 和最新 world revision；
- camera stale 不会让 semantic world 消失；
- world stale 不显示 LIVE；
- 方块、机器人 held、租约 owner、任务阶段与 `/v1/world` 一致。

最后在真实运行的用户端页面人工查看并保存截图，不能只依赖 API 测试。

## 18. 完成标准

只有同时满足以下条件才可宣称本轮完成：

1. 用户的一句自然语言生成包含共享方块和交接区的双机器人 TaskGraph；
2. robot-1 将方块放入交接区，环境验证后 robot-2 获得新 fencing token 并完成目标放置；
3. 最终任务为 `SUCCEEDED`，最终世界快照中方块在目标区、两机器人 held 为空、资源 lease 已释放；
4. 正常场景与九类故障场景均满足各自不变量；
5. 协调器重启后从持久化事件恢复，不重复物理动作；
6. 用户端可以平移、旋转、缩放、聚焦和刷新恢复；
7. 用户端实时画面与世界 API、任务事件一致，并有浏览器截图证据；
8. MuJoCo adapter 与实机示例 adapter 通过同一契约测试；
9. `make test`、Fleet e2e、异常套件、构建、lint 和安装契约全部通过；
10. 文档明确云端重点、本地无网画像、真实限制和实机接入步骤。
