# 数据契约与一致性语义

本页以当前 Go 语义类型和 protobuf 为准。示例注明“片段”时省略其他字段，不能直接作为可执行请求。HTTP JSON 使用字段的 JSON tag；protobuf 字段名与 Go JSON 不是同一种编码。

原始传感器流与规范三维重建是两个合同。显式请求 `rgbd_raw` 才提供 `Observation.rgbd_frame`，其 RGB、米制深度、内参、采集时刻与本体变换来自同一次观测；可选自身掩码只标记对应机器人表面，不能改写原始深度。导航地图通过只读 API 单独提供占用栅格，`-1` 表示未知、`0..100` 表示占用概率；地图可以保留历史观测，机器人位置必须有新鲜 RTAB-Map TF，不能沿用过期位置作为当前定位。详见 [RTAB-Map 合同](../development/rtabmap-navigation.md)。

工具终态的 `SkillEvent.evidence_observation` 是原始验证输入。它必须匹配命令回执的观测编号，并通过来源、时间与重建合同校验；成功或失败都可以归档。历史归档不推进或倒退当前实时观测游标，底部相机的导航证据也不会覆盖头部相机实时场景。旧 Runtime 未提供该字段时才保存执行后观测，并标注 `post_tool_observation`，不能将其描述为原始验证输入。

## 1. 标识与版本

新增严格接入合同：`RuntimeInfo.robot_profile` 携带 `robot.profile.v1`；`Observation.reconstruction` 携带 `scene.reconstruction.v1`。内部 JSON 使用 camelCase。新 Profile 存在时 reconstruction 必填，机器人/适配器身份、传感器类型、sourceFrameId、变换版本和采集时刻必须一致。Profile 在一次 Runtime/Go 客户端连接生命周期内不可变；改变型号或标定声明后应重新接入并验收，不能静默降级为 legacy。

重建坐标限定 `frameId=world`、`units=m`，pose 为 `[x,y,z,qw,qx,qy,qz]` 且四元数归一；最多 2048 个语义实体、4096 个 XYZ 点。每源 sequence 为正整数且不超过 `2^53-1`；同序号的帧不可改写，新序号不得重用上一 observationId 或倒退采集时间。源 maxAgeMs 为 1..60000，未来时钟容差 250ms；接收时钟不覆盖采集时钟。空场景合法，不能因此声称找到目标。Schema 导出和坏数据例子见[适配器手册](../development/robot-adapters.md)。

可选 `pointColors` 为与 XYZ 一一对应的 `N×3` RGB 整数数组，通道范围 0–255，沿用 `scene.reconstruction.v1` 的可选字段兼容方式。缺失或空数组表示无色；非空数组必须与点数一致，拒绝 `null`、非整数和越界颜色。RGB-D 使用同一对齐像素的实际颜色，不能用类别颜色代替。颜色参与完整帧一致性检查、深复制和历史 snapshot 哈希；旧无色记录不会被当前图像补色。

World 的逐实体 Envelope 使用原始 source/sequence/capture/entity 身份构造稳定 observationId，保留 sourceType/transformRevision/observedAt；其 sourceSequence 是 Edge 世界事件序号，与重建帧 sequence 分开管理。Provenance.sensor 在该投影中记录原始 sourceFrameId。点云不进入低频 Fleet Sample JSON/WorldSnapshot，不表示已经实现稠密地图发布。

| 字段 | 含义与约束 |
| --- | --- |
| `taskId`、Task 的 `id` | 一次用户任务，全链路关联根 |
| `revision` / `taskRevision` | 意图版本，从 1 递增，不覆盖历史内容 |
| `aggregateVersion` | Task 聚合提交版本；CAS 防并发覆盖 |
| `stepId` / `intentIndex` | 稳定步骤身份；保留步骤维持原 ID |
| `commandId` | 一次 Runtime 命令，全局唯一 |
| `idempotencyKey` | revision API 要求 UUID；Runtime 命令使用自己的幂等身份，不能混用两种格式要求 |
| `worldRevisionBasis` | 命令/验证所依据的世界版本 |
| `sourceSequence` | 每个观测源的单调序列；重复/倒序不得变成新证据 |
| `fencingToken` | 独占资源代次；转移时增加，旧 token 不复活 |
| `eventCursor` | 世界事件位置；delta 丢失时完整 resync |

`aggregateVersion` 不保证随每条执行活动递增，不能替代执行事件序号；World 的 `eventCursor` 也不能作为 Task Experience 游标。各层版本分别比较。

## 2. Task 与 TaskRevision

### 单机器人相机证据

严格 RGB-D 观测使用同一个采集返回 RGB、深度预览和 `reconstruction`。protobuf `Observation.compressed_depth_image`（10）与 `depth_image_media_type`（11）是新增可选字段；预览 PNG 不是原始米制深度，测量与算法使用 RGB-D provider 内部浮点数组。Local 实时接口校验图像格式、采集时间与 profile 新鲜度，缺失/过期不会返回伪造图像。`ObserveRequest.streams` 支持 `entities/rgb/depth/reconstruction/robot_state`；空列表保留兼容行为。

Local `evidence.capture.v1` 把 task/revision/step 与原始 `captureId`、来源、时间、标定版本关联，存储同帧标准 JSON、RGB/depth 与各自 SHA256。URL 的 `id` 是 task/step/capture 的哈希，跨任务读取返回 404。历史不是实时事实，不因旧采集时间而拒绝回看；原始内容超过 512 条或 256 MiB 保留预算后清空，保留身份/哈希/expired 元数据，图像返回 410。

Runner 的 `CONFIRMED.evidenceIds` 只引用执行后观测成功保存的原始 captureId；工具自己的回执 ID 位于 `receiptObservationId`，二者不能互当持久图像证明。证据缺失不补造。此存储是 Local V1 路线，不代表 Fleet 已持久存储全速视频、原始深度或稠密地图。

源码：`tasks/service.go`、`tasks/revision.go`。Task 是当前投影；创建时 `state=READY`、`approved=false`、`currentRevision=1`、`aggregateVersion=1`。批准写入布尔值和事件，不产生名为 APPROVED 的 Task 状态。

TaskRevision 内容不可变；生命周期放在外层 RevisionRecord 的 `status` 和 `events`。以下为返回结构片段：

```json
{
  "revision": {
    "taskId": "task-123",
    "revision": 2,
    "baseRevision": 1,
    "expectedAggregateVersion": 17,
    "request": "最后放到右侧蓝色垫子上",
    "changeSet": {"retained": ["step-handoff"], "changed": ["step-place"], "added": [], "paused": []}
  },
  "status": "WAITING_SAFE_POINT",
  "events": []
}
```

`baseRevision` 是存储记录字段。HTTP 提议输入是 `expectedRevision`，确认输入是 `expectedCurrentRevision`，两者均配 JSON 中 UUID `idempotencyKey`；见[API 示例](api-reference.md)。同一次操作重试复用 key，新操作使用新 key；同 key 不同内容冲突。确认后可能等待安全点，随后成为 ACTIVE，生命周期不修改历史意图内容。

### 意图中的实体与起点

`skills/manipulation.Intent` 的 `object`、`source`、`destination` 是独立 `EntitySelector`，各自可含 category、attributes、relation；不能从整句中取一个颜色或方位填到所有角色。同句后续“它”绑定前一步物体，未明确起点时以前一步目的地作为 source。新请求不继承上一任务的指代。

`edge/robotclient.Ground` 对物体与目的地做唯一匹配；source.category 非空时还要求唯一匹配起点，且物体在本次 Runtime 观测中的 `relation` 为 `inside:<source-id>` 或 `on:<source-id>`。关系缺失、持物、位置不符或多义都会失败。这里的 Runtime 单字符串 relation 与 World 实体的 `relations` 映射属于不同边界，provider 应按各自类型输出。

上下文终点修改只接受可完整解析的目标区/垫子表达，保留其他步骤与历史意图；含否定或额外未知动作的请求不会生成提案。解析规则与反例见[Agent V1](../agent-v1.md)。

## 3. RevisionStep 与 ToolActivity

`RevisionStep` 实际字段包括 `stepId`、`introducedRevision`、`semanticFingerprint`、`intentIndex`、`action`、`robotId`、`resourceId`、单数 `requiredPostcondition`、`status` 和 `harnessEvidenceIds`。状态为 PENDING、READY、RUNNING、AWAITING_EVIDENCE、SATISFIED、FAILED 或 CANCELLED_BY_REVISION。

面向用户的步骤是 `tasks/experience.go` 中 `ExperienceStep`，含 `explanation`、`assignedRobot`、`capabilityLabel` 与 `statusText`。不要把用户标题字段直接写入 Runtime 的步骤结构。

ToolActivity 是安全展示投影，示例：

```json
{
  "displayName": "放下方块",
  "purpose": "把红色方块放到右侧目标区",
  "status": "RUNNING",
  "statusText": "正在执行",
  "robotId": "robot-2",
  "stepId": "step-place",
  "safeArguments": {"target": "右侧目标区"}
}
```

`toolName`、command/catalog/fencing/revision 和策略身份位于 `professional.activities`。原始 action chunk、密钥和原始异常不进入任务事件或浏览器投影；只输出白名单参数和安全错误码。

### TaskExperience 完整快照

`TaskExperience` schema 是 `task.experience.v1`，当前 HTTP JSON **没有 `cursor` 字段**。`steps`、`activities`、`recovery` 可在相同 revision/aggregateVersion 下变化；`updateStatus=ACTIVE` 表示该任务版本生效，不等于 Task 正在 RUNNING。Task.state 才用于区分 SUCCEEDED、FAILED、CANCELLED 等终态。

前端接受当前选择、当前请求的完整快照，并用请求代次隔离旧响应；低 revision/aggregateVersion 仍拒绝，版本跳跃需重读历史。若收到有非零游标的版本，同聚合下仍需拒绝重复/倒序游标。此规则仅用于任务展示，不能套用到 WorldSnapshot 来允许同版本 pose/custody 改写。

## 4. DomainEvent 与 Command

`fleet/eventlog/store.go` 定义 DomainEvent：`eventId`、`aggregateType`、`aggregateId`、`aggregateVersion`、`eventType`、`idempotencyKey`、`occurredAt`，可附 payload/correlation/causation/actor。事件只追加；Outbox 至少一次发布，消费者按事件与版本幂等。

`proto/robot/v1/robot.proto` 定义 SkillCommand。其 proto 字段包括 `schema_version`、`command_id`、`task_id`、`skill`、`target_ref`、`parameters`、`deadline_unix_ms`、`lease_ms`、`idempotency_key`、`safety_profile`、`approval_id`、`robot_id`、`catalog_revision`、`world_revision_basis`、`resource_id`、`fencing_token`、`task_revision`、`aggregate_version`、`step_id`。

当前抓取/放置能力名是 `manipulation.pick` / `manipulation.place`；动作块位于 parameters。`desktop_standard` 是当前 Safety 的默认允许 profile，任意自造 `limited-workspace` 字符串不会自动获得授权。只有确定性编译层可填安全字段，Runtime 再复核；重复身份返回 journal 中的既有结果，未知物理终态进入对账。

## 5. ObservationEnvelope

`core/observation/envelope.go` 的语义 JSON schema 是 **`world.observation.v1`**。Fleet protobuf 的 ObservationEnvelope 是传输映射，不应把其 snake_case 字段当成 HTTP JSON。语义 JSON 示例：

```json
{
  "schemaVersion": "world.observation.v1",
  "observationId": "robot-2/scene/99/1787415000000000000",
  "worldId": "robocasa-handoff-v1",
  "sourceId": "robot-2/scene",
  "robotId": "robot-2",
  "sourceType": "sim_ground_truth",
  "sourceSequence": 99,
  "observedAt": "2026-08-22T16:10:00Z",
  "receivedAt": "2026-08-22T16:10:00.012Z",
  "frameId": "world",
  "transformRevision": "robocasa-world-v1",
  "kind": "entity_upsert",
  "payload": {"entityId": "red-block", "category": "block", "pose": [1.2, 0.1, 0.8], "relations": {"inside": "right-target-zone"}},
  "confidence": 1,
  "quality": {"latencyMs": 12},
  "causation": {"taskId": "task-123", "commandId": "cmd-42"},
  "provenance": {"adapter": "robocasa", "version": "1", "sensor": "mujoco"}
}
```

kind 支持 entity_upsert、entity_delete、robot_state_upsert、resource_upsert、source_health、frame_reference；每种必须对应正确 payload。frameRef 与 payload 不可并存。source/schema/frame/transform 先注册；未注册源、倒序、非法时间、坐标版本或质量不能成为 Harness 成功证据。

## 6. WorldSnapshot

`core/worldmodel/types.go` 定义 `world.snapshot.v1`，字段包括 worldId、revision、eventCursor、projectedAt、robots、entities、resources、sources、activeTasks、health。各对象带 freshness 和 evidence；实体还带 observationCount/stableObservations。EvidenceRef 关联 observationId/sourceId/sourceSequence/observedAt/frameId/transformRevision。

Pose 根据具体来源支持 `[x,y,z]`、Fleet `[x,y,z,yaw]` 或 `[x,y,z,qw,qx,qy,qz]`，生产方与消费方必须明确编码；模型和语义覆盖层使用相同方向。相同 revision 只允许 freshness 降级，不能恢复为新鲜或改变 pose/held/custody；新观测才产生新事实。

设置 `FLEET_WORLD_SNAPSHOT_PATH` 时，单主 checkpoint 保留投影与源序列。恢复不会恢复 delta 环形历史，客户端重新获取完整快照；旧观测按时间和恢复约束降级，不能直接判物理完成。详情见[架构](architecture.md)。

## 7. 资源 custody 与 Harness verdict

共享资源同时只有一个 owner。交接典型轨迹为 `environment/token0 → robot-1/token1 → robot-2/token2 → environment/token3`。实体 held_by、机器人 held 与 resource owner 冲突时停止下一动作，不能投票决定所有权。

`core/harness/evaluator.go` 的 Verdict 本身只有 `status`、`reason`、可选 `evidenceIds`；worldRevision 和 command basis 在输入与关联记录中，不应假设 Verdict 总会返回 worldRevision/observations。状态支持 WAITING、SATISFIED、RETRYABLE_FAILURE、FAILED_SAFE。

工具 SUCCEEDED 是调用终态；Harness SATISFIED 表示后置条件已被环境证据确认。证据必须能反查实际 source、sequence、时间、frame 与 transform，不能由模型或 UI 编造。

## 8. 演进与迁移

schema/字段兼容规则由具体解析与验证代码实施；不能假定所有 JSON 端点都拒绝未知字段。删除、重命名或改变安全语义需版本化并验证旧客户端。数据库升级按 `fleet/mysql` 的实际迁移执行，旧记录的 task revision、aggregate version 与事件身份不能丢失。

仿真与实机复用上层 Task/Command/Observation/World/Harness 契约，仍需独立匹配硬件、感知、策略和标定。跨 MySQL、Redis、世界快照与物理状态不具备全局原子事务。

## 9. 策略契约

PolicyManifest、ObservationBundle、InferenceRequest 和 InferenceResult 是 Edge 与模型 sidecar 的附加协议。结果必须回显 request/command/manifest/observation 身份，候选动作逐维通过清单和 Runtime 边界。任务事件只保留策略版本、inference/observation ID 与制品 hash 前缀。完整字段见[策略工具](policy-tools.md)。
