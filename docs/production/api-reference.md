# 全部接口参考

## 1. 约定

云端 Fleet 默认 HTTPS；Local Brain 默认 loopback HTTP。JSON 使用 UTF-8。操作员接口使用 `Authorization: Bearer <JWT>`；机器人 HTTP 数据面使用 `X-Robot-ID` 与 `X-Device-Token` 独立设备凭证；FleetGateway gRPC 使用 mTLS。幂等由各接口显式实现：revision 提议/确认使用 JSON 中的 UUID `idempotencyKey`，Runtime 使用 command 身份、fingerprint 与 journal。通用 `Idempotency-Key` HTTP 头不会自动让所有写接口幂等；当前创建/审批请求发生不确定结果时先查询任务，不盲目重发。资源写入还要满足 fencing 约束。

通用错误体：

```json
{"code":"REVISION_CONFLICT","message":"task revision conflict"}
```

错误码以对应 handler 的 `code` 为准；不存在统一 `retryable` 字段。常见类别包括鉴权 401/403、`INVALID_REQUEST`(400)、`UNSUPPORTED_INTENT`(422)、`TASK_NOT_FOUND`(404)、`REVISION_CONFLICT`/`IDEMPOTENCY_CONFLICT`(409)。版本冲突可能附带 `current` 任务；409 后读取最新 Task/Revision/World，重新预览，不能覆盖事实。

任务创建中的 `UNSUPPORTED_INTENT` 也包括需要澄清的否定、条件、多个不明确物体、未知目标或部分步骤未理解；`message` 给出具体原因。任务修改接口遇到同类解析错误时，当前映射为 **400 `REVISION_FAILED`**，并非创建接口的 422。客户端应保留用户输入并展示原因，不重复提交同一句或静默删掉约束。创建通过只证明形成了意图，物体不存在、起点不符、回合授权等仍可能在执行前或 Runtime 阶段失败。

`GET /v1/tasks/{id}/experience` 当前是无 `cursor` 的完整任务展示快照，同一 revision/aggregateVersion 下进展仍会变化。结合 Task.state 显示终态，不把 `updateStatus=ACTIVE` 当作 RUNNING；完整字段与刷新规则见[数据契约](data-contracts.md#taskexperience-完整快照)。

## 2. Fleet HTTP API

外部 Agent 可使用[官方 SDK 的 MCP stdio 桥接](../../robot/mcp/README.md)调用同一 Fleet API。8 个工具是 `list_robots`、`get_robot_capabilities`、`observe_world`、`list_tasks`、`create_task`、`get_task`、`cancel_task`、`emergency_stop`；工具输入和结果有 JSON Schema。`create_task` 仅创建 `approved=false` 的提议，用户在控制台审批。MCP 结果中的 `retryable` 属于桥接协议，不是本页 Fleet 通用错误体的新字段。

RobotRuntime gRPC 的新增可选字段为 `RuntimeInfo.robot_profile`（field 14）和 `Observation.reconstruction`（field 9）；RPC 方法保持不变。声明 Profile 的新适配器必须提交严格重建数据，旧客户端需要与当前 Edge 一起升级才能获得新增校验。新适配器的命令、schema 导出和服务器启动见[接入手册](../development/robot-adapters.md)。

| 接口 | 鉴权 | 用途、请求和成功响应 | 幂等/主要错误 |
| --- | --- | --- | --- |
| `GET /healthz` | 无 | 健康检查，200 | 只读 |
| `POST /v1/auth/login` | 用户密码 | `{"user":"...","password":"..."}` → JWT/过期时间 | 401；不要记录密码 |
| `POST /v1/auth/ws-ticket` | JWT | 生成一次性短期 WS ticket | ticket 仅可消费一次 |
| `GET /v1/devices` | JWT | 设备、lease、adapter、catalog、状态列表 | 只读 |
| `GET /v1/devices/{id}` | JWT | 单设备详情 | 404 |
| `POST /v1/devices/{id}/estop` | JWT（operator） | `{"reason":"..."}` 下发软件急停 | pushed 不证明物理停止；不能替代实体急停 |
| `POST /v1/devices/{id}/cancel` | JWT | `{"taskId":"...","stepId":"...","reason":"..."}` 下发 cancel_step | 返回 pushed 只表示下发，不是停止完成 |
| `POST /v1/tasks` | JWT | `{"request":"自然语言","adapter":"robocasa"}` → Task revision 1 | 未实现通用创建幂等；400/422 |
| `GET /v1/tasks` | JWT | 任务列表 | 只读 |
| `GET /v1/tasks/{id}` | JWT 或设备凭证 | Task 当前投影 | 404 |
| `POST /v1/tasks/{id}/approve` | JWT（operator） | 设置 `approved=true` 并排队；revision 更新使用 confirm | 请求不确定先查询；勿假定重复审批无副作用 |
| `POST /v1/tasks/{id}/cancel` | JWT | 转移 Task 为 CANCELLED；当前 Fleet handler 使用固定取消说明 | 不是物理停止证明；重复终态取消可返回错误 |
| `POST /v1/tasks/{id}/state` | JWT（operator；设备不可调用） | `{"state":"...","reason":"..."}` 请求合法状态迁移 | `TRANSITION_REJECTED`；不应用此接口伪造工具结果 |
| `POST /v1/tasks/{id}/events` | 设备凭证 | 追加步骤/工具/领域事件 | event ID 去重 |
| `POST /v1/tasks/{id}/revisions` | JWT | `{"expectedRevision":1,"request":"最后放到右侧蓝色垫子上","idempotencyKey":"UUID"}` → 预览 | 幂等；409 `REVISION_CONFLICT` |
| `POST /v1/tasks/{id}/revisions/{revision}/confirm` | JWT | `{"expectedCurrentRevision":1,"idempotencyKey":"UUID"}` → ACTIVE 或 WAITING_SAFE_POINT | 同 key 同结果；409 |
| `GET /v1/tasks/{id}/revisions` | JWT | 不可变 Revision 历史 | 只读 |
| `GET /v1/tasks/{id}/experience` | JWT | 面向用户的人话理解、步骤、工具活动、更新轨道、专业证据 | 只读；含 revision/aggregateVersion |
| `GET /v1/tasks/{id}/intents` | JWT | 机器人绑定和 Harness 状态 | 只读 |
| `GET /v1/tasks/{id}/domain-events` | JWT | 审计事件 | 只读；不要假定任意分页字段都已实现 |
| `GET /v1/telemetry` | JWT | `robot_id`、`limit` 查询 | 只读；限制 limit |
| `GET /v1/maps/global` | JWT | 地图、world frame、transform revision | 只读 |
| `GET /v1/scene/frames` | JWT | 机器人帧索引 | 只读 |
| `GET /v1/scene/frames/{robot}` | JWT | 机器人最新帧；可用 `t` 防缓存 | 只读 |
| `GET /v1/world` | JWT | 完整 `world.snapshot.v1` | 只读；按 revision 排序，不假定已有 ETag 支持 |
| `GET /v1/world/events/ws` | 一次性 ticket | World delta 流 | gap 后 REST resync |
| `GET /v1/orchestration/metrics` | JWT | 协调、Harness、队列、lease 指标 | 只读 |
| `GET /v1/queue/next` | 设备凭证 | 取可运行任务 | 至少一次；Edge 幂等 |
| `POST /v1/tasks/{id}/intents/next` | 设备凭证 | `{"robotId":"robot-1"}` 领取下一步骤 | lease/CAS/fencing |
| `POST /v1/tasks/{id}/intents/{index}/complete` | 设备凭证 | 上报工具终态和 Harness observation 引用 | 命令/步骤/revision 身份；409/422 |
| `POST /v1/tasks/{id}/intents/{index}/fail` | 设备凭证 | 失败码、可重试性和证据 | event ID 幂等 |
| `POST /v1/telemetry` | 设备凭证 | 低频状态和 freshness | source sequence 去重 |
| `GET /` | 无/登录页 | Console 静态入口 | CSP/同源资源 |

任务更新示例（UUID 仅为本例；每次新提议/确认生成新 UUID，重试同一次操作复用原值，revision 路径使用预览返回的版本）：

```bash
BASE=https://fleet.example
TOKEN='replace-with-short-lived-jwt'
TASK=task-123
curl -fsS -X POST "$BASE/v1/tasks/$TASK/revisions" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data '{"expectedRevision":1,"request":"最后放到右侧蓝色垫子上","idempotencyKey":"54c3a986-18a6-4c8c-9654-1b4815ef8c52"}'
curl -fsS -X POST "$BASE/v1/tasks/$TASK/revisions/2/confirm" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data '{"expectedCurrentRevision":1,"idempotencyKey":"21377425-1859-4457-81ba-0725d3d1c75b"}'
```

## 3. Local Brain HTTP API

Local Brain 路由由 `console/server.go` 注册：

| 接口 | 用途 |
| --- | --- |
| `GET /healthz` | 本地健康 |
| `GET /v1/config/status` | 返回非秘密配置状态；绝不返回 API key |
| `PUT /v1/config/llm` | 更新本地 LLM provider/base/model/key；仅 loopback |
| `GET /v1/runtime` | Runtime 能力、blocker、adapter/catalog |
| `POST /v1/tasks` | 创建本地 Task |
| `GET /v1/tasks` | 列表 |
| `GET /v1/tasks/{id}` | 详情 |
| `POST /v1/tasks/{id}/approve` | 审批 |
| `POST /v1/tasks/{id}/cancel` | 取消 |
| `POST /v1/tasks/{id}/revisions` | 生成本地更新预览 |
| `POST /v1/tasks/{id}/revisions/{revision}/confirm` | CAS 确认更新 |
| `GET /v1/tasks/{id}/revisions` | 版本历史 |
| `GET /v1/tasks/{id}/experience` | 用户任务体验投影 |
| `GET /v1/tasks/{id}/events/ws` | 单任务事件流 |
| `GET /v1/telemetry` | 本地遥测 |
| `GET /v1/scene/frame` | `adapter` 查询的场景帧 |
| `GET /v1/scene/depth` | `adapter` 查询的同一采集深度 PNG 预览，非原始米制深度数组 |
| `GET /v1/scene/camera` | `adapter` 与已声明 `sourceId` 查询；原子 snapshot、RGB/深度 data URL；不混用不同采集 |
| `GET /v1/navigation/map` | 当前机器人 RTAB-Map 占据图、来源、地图版本与定位；导航凭据仅在服务器使用，未配置返回 503 |
| `GET /v1/maps` | 已构建地图列表（id、机器人、时间、点数、LOD 层数、占用字节） |
| `GET /v1/maps/{id}` | 该地图的 `manifest.json` 原文 |
| `GET /v1/maps/{id}/cloud` | 点云文件，**支持 `Range` 并返回 `206`**；LOD 按需加载依赖它 |
| `GET /v1/maps/{id}/artifact/{role}` | 按角色取产物（`grid`/`trajectory` 等），同样支持 `Range` |
| `GET /v1/calibration/session` | 引导式整机标定的进度快照（步骤、进度、下一步、总结），由标定向导进程写出；未开始标定时返回 `available:false` 而不是错误 |
| `GET /v1/tasks/{id}/recovery` | 可否暂停/继续、已完成步骤和未知结果阻断原因 |
| `POST /v1/tasks/{id}/pause` | 请求当前工具完成并保存结果后暂停 |
| `POST /v1/tasks/{id}/resume` | 显式恢复，重新检查持久化记录及当前感知 |
| `GET /v1/tasks/{id}/observations` | 历史采集元数据，`limit`/`before` 分页 |
| `GET /v1/tasks/{id}/observations/{evidenceId}` | 同一采集的元数据及标准 Snapshot JSON |
| `GET /v1/tasks/{id}/observations/{evidenceId}/rgb` | 同一采集的历史彩色 PNG |
| `GET /v1/tasks/{id}/observations/{evidenceId}/depth` | 同一采集的历史深度预览 PNG |
| `GET /v1/world` | 本地世界快照 |
| `GET /v1/world/events/ws` | 本地世界增量 |
| `GET /v1/orchestration/metrics` | 编排指标 |
| `GET /` | 本地 Console |

Local Brain 默认只监听 `127.0.0.1:8787`。若反向代理到局域网，必须自行增加 TLS、身份认证和访问控制。

## 4. WebSocket

Fleet：先用 JWT 调 `POST /v1/auth/ws-ticket`，再连接：

```text
wss://fleet.example/v1/world/events/ws?after_revision=123&ticket=<one-time-ticket>
```

消息包含 schema、revision、snapshot/delta。客户端规则：小于当前 revision 的消息丢弃；相同 revision 只能让 freshness 从 FRESH 降级；`revision > current+1` 时停止应用 delta，GET `/v1/world` 完整 resync；socket 替换后旧 socket 的 open/message/error/close 和未完成 fetch 全部失效。当前浏览器关闭连接后约 1 秒重连，连接初始化失败后约 1.5 秒重试，并重新申请票据；没有实现浏览器指数退避加抖动。Local 任务 WS 为 `/v1/tasks/{id}/events/ws`，发送任务事件；不要把 World delta 的 after_revision/resync 协议套到任务 WS。

后端检查 Origin 与请求 Host；开发预览转发升级请求时保留浏览器侧 Host。若代理将 Host 改成内部端口而 Origin 仍是外部地址，同源连接会被拒绝，应修复转发而非放宽来源校验。

## 5. FleetGateway gRPC

定义：`proto/fleet/v1/fleet.proto`。

- `Register(RegisterRequest) -> RegisterResponse`：mTLS 后声明 `robot_id`、软件/协议/Runtime/Adapter 版本、能力、ToolDescriptor 和 ObservationSource。服务端返回注册结果、心跳与 lease 约束，具体字段以 proto 和 gateway 校验为准。
- `Link(stream LinkMessage) -> stream LinkMessage`：双向 sequence 流。Edge 上行 Heartbeat、TelemetrySample、StatusReport、EventReport、ObservationEnvelope；云端下行 ServerCommand，双方 Ack。断线重连后 sequence 仍不得倒退。

## 6. RobotRuntime gRPC

定义：`proto/robot/v1/robot.proto`。

- `GetRuntimeInfo(GetRuntimeInfoRequest) -> RuntimeInfo`：读取 adapter、版本、capability、blocker、catalog revision。
- `Observe(ObserveRequest) -> stream Observation`：按 task/streams/rate 输出实体、关节、语义状态和可选压缩图像。
- `ExecuteSkill(SkillCommand) -> stream SkillEvent`：执行工具。命令包含 command/task/robot/step/revision/aggregate、deadline、lease、idempotency、catalog、world basis、resource/fencing、安全档案和审批 ID。
- `Cancel(CancelRequest) -> CancelResult`：请求安全取消；返回 accepted/state，不保证能中断不可逆动作。
- `EmergencyStop(EStopRequest) -> EStopResult`：锁存软件急停并返回时间。

`SkillEvent.sequence` 必须递增，终态只能出现一次。观察事件可携 observation ID，但任务完成仍由 Harness 对 WorldSnapshot 复核。

## 7. 兼容与限流

`schemaVersion`/`protocol_version`/`runtime_version`/`adapter_version`/catalog revision 都是独立兼容轴。catalog 漂移与安全字段不匹配应失败关闭；JSON 未知字段处理以各 handler 为准，不能假定所有 HTTP decoder 都拒绝未知字段。读接口可重试；写接口按上面的实际幂等范围处理。生产应在反向代理对登录、任务创建、帧、遥测和 WS ticket 分别限流，且保留 correlation/task/command ID 以便审计。

## 8. Policy Sidecar HTTP API

学习模型 sidecar 提供 `GET /healthz`、`GET /v1/manifest` 和 `POST /v1/infer`。该接口由 Edge 本机或受限机器人网络调用，不属于浏览器公开 API；生产应在 loopback、Unix 代理或 mTLS 服务网格中部署。请求/响应身份、错误码、大小限制和完整 JSON 字段见[学习型策略工具](policy-tools.md)。
