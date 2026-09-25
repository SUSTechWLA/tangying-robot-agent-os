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
| `POST /v1/assist/chat/completions` | 机器人专属 Assist 凭证或完整 device 凭证；operator 不可用 | Orin NX 调用云端只读推理工具；推荐 Assist 独立凭证，它不可访问队列、遥测或任务写接口。非流式文本 `messages`，`model` 仅接受已配置别名 `cloud-assist` 或 `cloud-intent/planning/recovery`，Fleet 覆盖真实模型名、输出 token 上限；成功返回 OpenAI 兼容 JSON | 未配置 503；无效请求/别名 400；容量 429；上游失败 502；请求 64 KiB、响应 1 MiB |
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
| `GET /v1/agent/alerts` | JWT | 监督 agent 的发现：任务级来自事件账本，机器人级（急停、模块故障、观测过期）来自运行时；附 `supervision` 说明当前是否有 agent 在观察 | 只读；告警由当前状态投影，条件消除后自动 `active=false` |
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
| `GET /healthz` | **存活**探针，恒返回 `ok`；它回答"进程还活着吗"，不回答"机器人能不能用" |
| `POST /v1/recovery/execute` | 批准并执行**一步**恢复动作：body `{actionId,planId,taskId}`。动作必须是目录里的 `read_only` 或 `bounded_write` 条目；三处会拒绝而不会执行——不在目录里(404)、目录明确拒绝如急停复位(409)、没配置执行能力(503)。执行范围是该动作声明的工具，结论由复验给出而不是动作自述。`executed` 的含义是**确实下发过调用**（取自决策循环的调用计数，不是"循环正常返回"）：未配决策器时循环空转，返回 `executed:false` 且不做复验，`trail` 记为 `not-executed`／`verification.not-applicable` |
| `POST /v1/robots/pair` | 用一次性配对码接入一台已发现的机器人（**不需要 SSH**）：body `{robotId,address,code}`。成功返回 `restartRequired`——机器人凭据是启动时读取的，必须重启 Local Agent 才生效 |
| `GET /v1/robots/discovered` | 局域网里正在广播自己的机器人（只读，不做任何探测或配对）：`robotId/hostname/address/adapter/pairingState`，以及 `listening`（有没有在监听）与 `mismatched`（有机器人在广播但协议版本读不了） |
| `POST /v1/tasks/{id}/reconcile` | 记录**人**对一个结果未知的物理步骤的结论：body `{stepId,outcome,note}`，`outcome` 取 `HAPPENED`／`NEVER_ACTED`／`ABANDONED`。需要控制台会话，且一个人与一句依据缺一不可——让人继续往下走的那个判断，正是后来的人必须能反驳的那个。步骤自身的状态**不变**（结果确实未知，记录继续这么说），变的是有人去看过了；写入一次，第二个来看的人会看到已经有人决定过。这是可用性报告里那个阻塞项**唯一**的清除路径，没有任何定时器或 agent 能调用它 |
| `GET /v1/readiness` | **可用性**报告：机器人本体自检、急停、地图、连接、监督 agent、结果未知的动作、当前自然语言能力。`ready=false` 时给出 `nextId` 与"下一步做什么" |
| `GET /v1/config/status` | 返回非秘密配置状态及意图、规划、恢复三个阶段的有效模型路由；绝不返回 API key 或设备令牌 |
| `PUT /v1/config/llm` | 更新本地默认 LLM provider/base/model/key；`clearApiKey:true` 可清除旧密钥，切换 URL 不继承旧密钥；仅 loopback。已显式覆盖的阶段仍按阶段配置 |
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
| `GET /v1/telemetry` | 本地遥测。`adapter` 查询指定运行时（不传则 `hasLatest:false`，因为 hub 按 adapter 归档）。**`unfiled`** 在收到过**没有标明 adapter** 的观测时出现：`{count,lastAt,reason}`。它区分"机器人什么都没说"与"机器人在说但我们归档不了"——只有 `hasLatest` 时这两种情况长得一模一样，而后者是接线故障 |
| `GET /v1/telemetry/latency` | 步骤耗时遥测：`groupBy`（capability/safety/robot/outcome）、`windowMs`（0 表示全部保留样本）；按**排队/准入/执行/核验**四段给出 p50/p95/p99、结果分布与最慢步骤；未启用采集返回 `LATENCY_UNAVAILABLE`，非法分组/窗口返回 400 |
| `GET /v1/scene/frame` | `adapter` 查询的场景帧 |
| `GET /v1/scene/depth` | `adapter` 查询的同一采集深度 PNG 预览，非原始米制深度数组 |
| `GET /v1/scene/camera` | `adapter` 与已声明 `sourceId` 查询；原子 snapshot、RGB/深度 data URL；不混用不同采集 |
| `GET /v1/navigation/map` | 当前机器人 RTAB-Map 占据图、来源、地图版本与定位；导航凭据仅在服务器使用，未配置返回 503 |
| `GET /v1/maps` | 已构建地图列表（id、机器人、时间、点数、LOD 层数、占用字节） |
| `GET /v1/maps/{id}` | 该地图的 `manifest.json` 原文 |
| `GET /v1/maps/{id}/cloud` | 点云文件，**支持 `Range` 并返回 `206`**；LOD 按需加载依赖它。加 `?lod=N` 取第 N 层（N 与 manifest 的 `lodLevels` 做范围校验） |
| `GET /v1/maps/{id}/artifact/{role}` | 按角色取产物（`grid`/`trajectory` 等），同样支持 `Range`。角色 `slam_session` / `slam_keyframes` 走单独路径：有字节预算（2 MB / 12 MiB）、可用 `?sha256=` 固定版本，返回 `ETag` + `Cache-Control: no-store`；错误码 `ARTIFACT_BUDGET`(422)、`ARTIFACT_REVISION`(409)、`ARTIFACT_INTEGRITY`(422) |
| `GET /v1/calibration` | 标定文档本身（16 舵机 + 2 相机的完整内外参），供查看与自行校准；未标定时返回 `available:false` |
| `GET /v1/calibration/session` | 引导式整机标定的进度快照（步骤、进度、下一步、总结），由标定向导进程写出；未开始标定时返回 `available:false` 而不是错误 |
| `GET /v1/robot/services` | 当前机器人注册的服务目录、JSON 参数 schema、可用状态和是否修改状态 |
| `GET /v1/mapping` | 只读的巡检投影：`state`、`unknownFraction`、`target`、`stopReason`、`mapId`，并显式声明 `readOnly:true`、`canStartSurvey:false`。服务名在服务器侧是字面量（只调 `mapping.status`），因此这条路由无法用来调用任何其他服务；启动/停止巡检仍需操作者会话，见 `POST /v1/robot/services` |
| `POST /v1/mapping/request` | **自然语言建图入口**：JSON 为 `{request,environment?,maxTravelM?,maxLegs?}`。识别到建图意图（词表与工具层 `build_map` 同源）后转发给机器人的 `mapping.ensure`，由**机器人**决定复用已有地图还是开始探索；不是建图请求返回 422 且不触达机器人。幂等键由控制台按请求生成。需要操作者会话 |
| `POST /v1/robot/services` | 调用注册服务，JSON 为 `{name,requestId,parameters}`；修改请求按 requestId 幂等，冲突拒绝；目录中的机器人身份由控制台绑定；标定与建图流程见[操作指南](../guides/robot-service-workflow.md) |
| `GET /v1/tasks/{id}/recovery` | 可否暂停/继续、已完成步骤和未知结果阻断原因 |
| `POST /v1/tasks/{id}/pause` | 请求当前工具完成并保存结果后暂停 |
| `POST /v1/tasks/{id}/resume` | 显式恢复，重新检查持久化记录及当前感知 |
| `GET /v1/tasks/{id}/observations` | 历史采集元数据，`limit`/`before` 分页 |
| `GET /v1/tasks/{id}/observations/{evidence}` | 同一采集的元数据及标准 Snapshot JSON |
| `GET /v1/tasks/{id}/observations/{evidence}/rgb` | 同一采集的历史彩色 PNG |
| `GET /v1/tasks/{id}/observations/{evidence}/depth` | 同一采集的历史深度预览 PNG |
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
- `ListServices(GetRuntimeInfoRequest) -> ServiceCatalog`：发现机器人注册的标定、建图与地图定位服务和参数 schema。
- `CallService(ServiceRequest) -> ServiceResponse`：按机器人 ID、服务名称、request ID 调用服务；修改调用去重，预约与运动安全由提供者执行。
- `Observe(ObserveRequest) -> stream Observation`：按 task/streams/rate 输出实体、关节、语义状态和可选压缩图像。
- `ExecuteSkill(SkillCommand) -> stream SkillEvent`：执行工具。命令包含 command/task/robot/step/revision/aggregate、deadline、lease、idempotency、catalog、world basis、resource/fencing、安全档案和审批 ID。
- `Cancel(CancelRequest) -> CancelResult`：请求安全取消；返回 accepted/state，不保证能中断不可逆动作。
- `EmergencyStop(EStopRequest) -> EStopResult`：锁存软件急停并返回时间。

`SkillEvent.sequence` 必须递增，终态只能出现一次。观察事件可携 observation ID，但任务完成仍由 Harness 对 WorldSnapshot 复核。

## 7. 兼容与限流

`schemaVersion`/`protocol_version`/`runtime_version`/`adapter_version`/catalog revision 都是独立兼容轴。catalog 漂移与安全字段不匹配应失败关闭；JSON 未知字段处理以各 handler 为准，不能假定所有 HTTP decoder 都拒绝未知字段。读接口可重试；写接口按上面的实际幂等范围处理。生产应在反向代理对登录、任务创建、帧、遥测和 WS ticket 分别限流，且保留 correlation/task/command ID 以便审计。

## 8. Policy Sidecar HTTP API

学习模型 sidecar 提供 `GET /healthz`、`GET /v1/manifest` 和 `POST /v1/infer`。该接口由 Edge 本机或受限机器人网络调用，不属于浏览器公开 API；生产应在 loopback、Unix 代理或 mTLS 服务网格中部署。请求/响应身份、错误码、大小限制和完整 JSON 字段见[学习型策略工具](policy-tools.md)。
