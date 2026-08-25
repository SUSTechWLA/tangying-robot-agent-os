# 全部接口参考

## 1. 约定

云端 Fleet 默认 HTTPS；Local Brain 默认 loopback HTTP。JSON 使用 UTF-8。操作员接口使用 `Authorization: Bearer <JWT>`；机器人 HTTP 数据面使用 `X-Robot-ID` 与独立设备凭证；FleetGateway gRPC 使用 mTLS。创建/更新/确认/完成接口应携带 `Idempotency-Key`，重试必须复用原值。资源写入还要携带最新 fencing token。

通用错误体：

```json
{"error":{"code":"REVISION_CONFLICT","message":"base revision is stale","retryable":true}}
```

常见错误码：`UNAUTHORIZED`(401)、`FORBIDDEN`(403)、`NOT_FOUND`(404)、`METHOD_NOT_ALLOWED`(405)、`REVISION_CONFLICT`/`FENCING_CONFLICT`/`CUSTODY_CONFLICT`(409)、`INVALID_ARGUMENT`(400)、`UNSUPPORTED_INTENT`(422)、`STALE_WORLD`/`CATALOG_MISMATCH`/`SAFETY_BLOCKED`(412/422)、`INTERNAL`(500)、`UNAVAILABLE`(503)。409 后先 GET 最新 Revision/World，再重新生成预览，不能覆盖服务器事实。

## 2. Fleet HTTP API

| 接口 | 鉴权 | 用途、请求和成功响应 | 幂等/主要错误 |
| --- | --- | --- | --- |
| `GET /healthz` | 无 | 健康检查，200 | 只读 |
| `POST /v1/auth/login` | 用户密码 | `{"user":"...","password":"..."}` → JWT/过期时间 | 401；不要记录密码 |
| `POST /v1/auth/demo-session` | 仅显式 Demo profile | 无请求体 → `demo-operator` JWT/过期时间 | `required` 模式固定 404；不能访问设备专用路由 |
| `POST /v1/auth/ws-ticket` | JWT | 生成一次性短期 WS ticket | ticket 仅可消费一次 |
| `GET /v1/devices` | JWT | 设备、lease、adapter、catalog、状态列表 | 只读 |
| `GET /v1/devices/{id}` | JWT | 单设备详情 | 404 |
| `POST /v1/devices/{id}/estop` | JWT+急停权限 | `{"reason":"..."}`，锁存软件急停 | 重复调用安全；不能替代实体急停 |
| `POST /v1/devices/{id}/cancel` | JWT | 取消设备当前命令 | command 终态后返回当前状态 |
| `POST /v1/tasks` | JWT | `{"request":"自然语言","adapter":"robocasa"}` → Task revision 1 | `Idempotency-Key`；400/422 |
| `GET /v1/tasks` | JWT | 任务列表 | 只读 |
| `GET /v1/tasks/{id}` | JWT | Task 当前投影 | 404 |
| `POST /v1/tasks/{id}/approve` | JWT+审批权限 | 批准 revision 1 或当前待批版本 | 幂等；409 状态冲突 |
| `POST /v1/tasks/{id}/cancel` | JWT | `{"reason":"..."}` | 幂等；不可撤销物理动作等待安全点 |
| `POST /v1/tasks/{id}/state` | 内部设备/协调器 | 上报任务状态迁移 | aggregate version CAS |
| `POST /v1/tasks/{id}/events` | 内部设备/协调器 | 追加步骤/工具/领域事件 | event ID 去重 |
| `POST /v1/tasks/{id}/revisions` | JWT | `{"baseRevision":1,"request":"最后放到右侧蓝色垫子上","idempotencyKey":"..."}` → 预览 | 幂等；409 `REVISION_CONFLICT` |
| `POST /v1/tasks/{id}/revisions/{revision}/confirm` | JWT | `{"baseRevision":1,"idempotencyKey":"..."}` → ACTIVE 或 WAITING_SAFE_POINT | 同 key 同结果；409 |
| `GET /v1/tasks/{id}/revisions` | JWT | 不可变 Revision 历史 | 只读 |
| `GET /v1/tasks/{id}/experience` | JWT | 面向用户的人话理解、步骤、工具活动、更新轨道、专业证据 | 只读；含 revision/cursor |
| `GET /v1/tasks/{id}/intents` | JWT | 机器人绑定和 Harness 状态 | 只读 |
| `GET /v1/tasks/{id}/domain-events` | JWT | 审计事件 | 只读；生产分页 |
| `GET /v1/telemetry` | JWT | `robot_id`、`limit` 查询 | 只读；限制 limit |
| `GET /v1/maps/global` | JWT | 地图、world frame、transform revision | 只读 |
| `GET /v1/scene/frames` | JWT | 机器人帧索引 | 只读 |
| `GET /v1/scene/frames/{robot}` | JWT | 机器人最新帧；可用 `t` 防缓存 | 只读 |
| `GET /v1/sensors/captures` | JWT | 每台机器人最新的原子 RGB-D capture 元数据 | 只读；不内嵌图像字节 |
| `GET /v1/sensors/latest/{robot}` | JWT | 指定机器人最新 `sensor.capture.v1` 元数据 | 404 表示尚无完整同步 capture |
| `GET /v1/sensors/media/{captureKey}/{modality}` | JWT | 读取 capture 的 `rgb` 或 `depth` 原始媒体 | 支持 ETag/304；400 非法 key/modality，404 不存在 |
| `GET /v1/world` | JWT | 完整 `world.snapshot.v1` | 只读；ETag/revision 可缓存 |
| `GET /v1/world/events/ws` | 一次性 ticket | World delta 流 | gap 后 REST resync |
| `GET /v1/orchestration/metrics` | JWT | 协调、Harness、队列、lease 指标 | 只读 |
| `GET /v1/queue/next` | 设备凭证 | 取可运行任务 | 至少一次；Edge 幂等 |
| `POST /v1/tasks/{id}/intents/next` | 设备凭证 | `{"robotId":"robot-1"}` 领取下一步骤 | lease/CAS/fencing |
| `POST /v1/tasks/{id}/intents/{index}/complete` | 设备凭证 | 上报工具终态和 Harness observation 引用 | 命令/步骤/revision 身份；409/422 |
| `POST /v1/tasks/{id}/intents/{index}/fail` | 设备凭证 | 失败码、可重试性和证据 | event ID 幂等 |
| `POST /v1/telemetry` | 设备凭证 | 低频状态和 freshness | source sequence 去重 |
| `GET /` | 无/登录页 | Console 静态入口 | CSP/同源资源 |

任务更新示例：

```bash
BASE=https://fleet.example
TOKEN='replace-with-short-lived-jwt'
TASK=task-123
curl -fsS -X POST "$BASE/v1/tasks/$TASK/revisions" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: update-task-123-v2' \
  --data '{"baseRevision":1,"request":"最后放到右侧蓝色垫子上","idempotencyKey":"update-task-123-v2"}'
curl -fsS -X POST "$BASE/v1/tasks/$TASK/revisions/2/confirm" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data '{"baseRevision":1,"idempotencyKey":"confirm-task-123-v2"}'
```

RGB-D 读取示例：

```bash
CAPTURE_JSON=$(curl -fsS "$BASE/v1/sensors/latest/robot-1" \
  -H "Authorization: Bearer $TOKEN")
RGB_URI=$(printf '%s' "$CAPTURE_JSON" | jq -r '.frames[] | select(.modality == "rgb") | .uri')
curl -fsS "$BASE$RGB_URI" -H "Authorization: Bearer $TOKEN" -o robot-1-rgb.png
```

元数据包含 `captureId`、`robotId`、`episodeId`、`simulationStep`、单调
`sourceSequence`、`capturedAt`、`frameId`、`transformRevision`、已关联的
`worldRevision`，以及 RGB/depth 两个 frame 的尺寸、SHA-256、内参和
`cameraToWorld`。depth frame 还包含 `depthScaleM`、`minRangeM` 和
`maxRangeM`。`captureKey` 只能使用元数据 `uri` 中服务器返回的 URL-safe
编码值，不应由客户端猜测。Harness 和浏览器必须拒绝错 robot/episode、倒序
sequence、超前 world revision、哈希不一致或缺少任一模态的 capture。

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

世界消息使用 `world.delta.v1`，其中 `snapshot` 是同 revision、同 worldId 的完整
`world.snapshot.v1`。服务端最多每 50ms（20 FPS）发送一次，并在窗口内只保留最新
权威快照，避免多源遥测让浏览器积压旧画面。客户端规则：小于等于当前 revision 的
消息丢弃；若跳号消息携带上述自包含完整快照，可直接原子替换到最新 revision；未知
schema、worldId 改变或不完整 delta 仍必须停止应用并 GET `/v1/world` 完整 resync。
socket 替换后旧 socket 的 open/message/error/close 和未完成 fetch 全部失效；指数退避
加抖动重连。Local 任务 WS 为 `/v1/tasks/{id}/events/ws`，仍使用严格的
cursor/gap/resync 语义。

## 5. FleetGateway gRPC

定义：`proto/fleet/v1/fleet.proto`。

- `Register(RegisterRequest) -> RegisterResponse`：mTLS 后声明 `robot_id`、软件/协议/Runtime/Adapter 版本、能力、ToolDescriptor 和 ObservationSource。服务端返回心跳间隔和 lease。catalog 不兼容时 `accepted=false`。
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

`schemaVersion`/`protocol_version`/`runtime_version`/`adapter_version`/catalog revision 都是独立兼容轴。未知必填字段或 catalog 漂移应失败关闭。读接口可重试；写接口只用相同幂等键重试。生产应在反向代理对登录、任务创建、帧、遥测和 WS ticket 分别限流，且保留 correlation/task/command ID 以便审计。

## 8. Policy Sidecar HTTP API

学习模型 sidecar 提供 `GET /healthz`、`GET /v1/manifest` 和 `POST /v1/infer`。该接口由 Edge 本机或受限机器人网络调用，不属于浏览器公开 API；生产应在 loopback、Unix 代理或 mTLS 服务网格中部署。请求/响应身份、错误码、大小限制和完整 JSON 字段见[学习型策略工具](policy-tools.md)。
