# 数据契约与一致性语义

## 1. 标识与版本

| 字段 | 含义 | 规则 |
| --- | --- | --- |
| `taskId` | 一次用户任务 | 全链路 correlation 根 |
| `revision` / `taskRevision` | 用户意图版本 | 从 1 单调递增，不可覆盖 |
| `aggregateVersion` | Task 事件提交版本 | CAS；防双写 |
| `stepId` / `intentIndex` | 稳定步骤身份 | 保留步骤在 revision 间保持 ID |
| `commandId` | 一次物理命令 | 全局唯一；Event 引用 |
| `idempotencyKey` | 调用者重试身份 | 相同 key+相同 payload 返回同结果；不同 payload 冲突 |
| `worldRevisionBasis` | 规划/命令所依据世界 | 过旧时拒绝执行 |
| `sourceSequence` | 单观测源序列 | 每 source 严格单调；重复/倒序丢弃 |
| `fencingToken` | 独占资源代次 | custody 转移时递增；旧 token 永久失效 |
| `cursor` | 事件读取位置 | gap 时完整 resync，不猜测缺失内容 |

## 2. Task 与 TaskRevision

```json
{
  "id": "task-123",
  "request": "最后放到右侧蓝色垫子上",
  "adapter": "robocasa",
  "state": "SUCCEEDED",
  "currentRevision": 2,
  "aggregateVersion": 17
}
```

Task 是可变投影；TaskRevision 是不可变事实：

```json
{
  "schemaVersion": "task.revision.v1",
  "taskId": "task-123",
  "revision": 2,
  "baseRevision": 1,
  "request": "最后放到右侧蓝色垫子上",
  "status": "ACTIVE",
  "changeSet": {
    "retained": ["step-handoff"],
    "changed": ["step-place"],
    "cancelled": []
  }
}
```

`baseRevision` 不等于服务器当前 revision 时返回 `REVISION_CONFLICT`。确认后若当前工具不可安全取消，status 为 `WAITING_SAFE_POINT`；到达检查点后变为 `ACTIVE`。

## 3. RevisionStep 与 ToolActivity

RevisionStep 面向用户稳定表达“做什么、谁做、完成标准”：

```json
{
  "stepId": "step-place",
  "ordinal": 2,
  "robotId": "robot-2",
  "title": "2号机器人把方块放到右侧蓝色垫子",
  "status": "SATISFIED",
  "requiredPostconditions": ["red-block inside right-target-zone", "held clear"]
}
```

ToolActivity 是工具事件的安全投影，不泄露原始秘密参数：

```json
{
  "activityId": "activity-command-42-running",
  "stepId": "step-place",
  "toolName": "place_object",
  "displayName": "放下方块",
  "purpose": "把红色方块放到右侧蓝色垫子",
  "state": "RUNNING",
  "progressText": "2号机器人正在对准蓝色垫子",
  "safeArguments": {"target": "右侧蓝色垫子"}
}
```

原始参数只进入受限专业日志；UI 默认每步骤只显示最新活动，完整事件可折叠查看。

## 4. DomainEvent 与 Command

DomainEvent 必含 event ID/type、aggregate ID/version、occurredAt、correlation/causation 和 payload。事件只追加，不修改。Outbox 发布至少一次，因此投影器按 event ID 和 aggregate version 幂等。

SkillCommand 字段见 `proto/robot/v1/robot.proto`，关键约束：

```json
{
  "command_id": "cmd-42",
  "task_id": "task-123",
  "task_revision": 2,
  "aggregate_version": 17,
  "step_id": "step-place",
  "skill": "place_object",
  "idempotency_key": "task-123/r2/step-place/attempt-1",
  "deadline_unix_ms": 1787416000000,
  "catalog_revision": "tools-sha256",
  "world_revision_basis": 6405,
  "resource_id": "block:red-block",
  "fencing_token": 2,
  "safety_profile": "limited-workspace"
}
```

Runtime 在执行前逐项验证；任一身份过旧均不产生动作。

## 5. ObservationEnvelope

```json
{
  "schema_version": "observation.envelope.v1",
  "observation_id": "robot-2/scene/99/1787415000000000000",
  "world_id": "robocasa-handoff-v1",
  "source_id": "robot-2/scene",
  "robot_id": "robot-2",
  "source_type": "scene",
  "source_sequence": 99,
  "observed_unix_ms": 1787415000000,
  "received_unix_ms": 1787415000012,
  "frame_id": "world",
  "transform_revision": "robocasa-world-v1",
  "kind": "entities",
  "payload": {},
  "quality": {"latency_ms": 12, "anomalies": []},
  "causation": {"task_id": "task-123", "command_id": "cmd-42"},
  "provenance": {"adapter": "robocasa", "version": "1", "sensor": "mujoco"}
}
```

观测目录预先声明 source/schema/frame/transform/update rate/freshness budget。未注册源、倒序、时间漂移、未知 transform 或异常质量不能成为 Harness 的成功证据。

## 6. WorldSnapshot

```json
{
  "schemaVersion": "world.snapshot.v1",
  "worldId": "robocasa-handoff-v1",
  "revision": 6489,
  "projectedAt": "2026-08-22T16:30:00Z",
  "robots": {
    "robot-2": {"pose": [1.2, 0.1, 0.0, 1.57], "freshness": "FRESH", "held": ""}
  },
  "entities": {
    "red-block": {"relations": {"inside": "right-target-zone"}, "freshness": "FRESH"}
  },
  "resources": {
    "block:red-block": {"owner": "environment", "fencingToken": 3, "freshness": "FRESH"}
  },
  "sources": {"robot-2/scene": {"freshness": "FRESH"}}
}
```

Pose 支持 `[x,y,z]`、Fleet `[x,y,z,yaw]` 和 `[x,y,z,qw,qx,qy,qz]`；模型和语义 overlay 都必须使用相同 yaw。全局 revision 新增时可改变事实；相同 revision 只能把 freshness 从 FRESH 降为 STALE/UNKNOWN，不能恢复或修改 pose/held/emergency/custody。

## 7. 资源 custody 与 Harness verdict

资源同时只有一个 owner。典型交接：`environment/token0 → robot-1/token1 → robot-2/token2 → environment/token3`。entity `held_by`、robot `held`、resource owner 三个来源冲突时标记 `CONFLICT`，停止下一动作，不能任取多数。

Harness verdict：

```json
{
  "status": "SATISFIED",
  "reason": "PHYSICAL_POSTCONDITIONS_SATISFIED",
  "worldRevision": 6489,
  "evidenceIds": ["robot-2/scene/99/...", "robot-2/proprioception/120/..."],
  "observations": [{"sourceId": "robot-2/scene", "sourceSequence": 99}]
}
```

工具的 SUCCEEDED 只表示调用终态；Harness 的 SATISFIED 才表示环境完成。证据必须能在保留事件/轨迹中反向解析，source、sequence、时间、frame 和 transform 必须匹配。

## 8. 演进与迁移

增加可选字段保持同一 schema；删除/重命名/改变语义必须提升 schema 或 protocol version。消费者忽略未知可选字段，但未知必填安全字段失败关闭。数据库迁移先写兼容读、再双写、再回填、最后切换；历史 Revision、fencing token、event ID 和 evidence ID 永不重写。仿真到实机只换 Adapter/provenance，不换上层 Task/Command/Observation/World/Harness 契约。

## 9. 策略契约

PolicyManifest、ObservationBundle、InferenceRequest 和 InferenceResult 是 Edge 与模型 sidecar 之间的附加契约，不替代 ObservationEnvelope 或 WorldSnapshot。InferenceResult 必须回显 request、command、manifest 和 observation 身份；action chunk 的每个命名维度必须在清单范围内。任务事件只保留 policy/version、manifest revision、inference/observation ID 和制品 hash 前缀，绝不保留原始 action chunk。字段表和样例见[学习型策略工具](policy-tools.md)。
