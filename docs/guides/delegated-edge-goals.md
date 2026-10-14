# 云端与本地目标委托接入指南

先读[架构与当前边界](../architecture/hierarchical-edge-objectives.md)。目标合同是云端大脑与边缘团队的接口；云端不需要导入边缘代码或生成具体工具序列。旧任务入口仍兼容，本指南使用新增目标 API，前端现有自然语言任务表单不会自动改成该 API。

## 1. 读取目标能力目录

云端 operator 调用 `GET /v1/devices/{robotId}/goal-catalog`，取得 `robotId`、`catalogRevision` 和 `capabilities`；Local 调用 `GET /v1/robot/services`，使用 `goalCatalogRevision` 与 `goalCapabilities`。保留这些响应作为计划输入。不要拿 `Runtime.Info` 的低层目录摘要代替目标目录摘要，也不要对原始服务 JSON 自行猜哈希。

## 2. 创建并批准父目标

Local 与 Fleet 都接受 `POST /v1/objectives`，请求为 `{"adapter":"ros2","idempotencyKey":"<本次请求唯一键>","objective":{...}}`。同一次重试保持键和合同不变；复用键改变合同返回 409，重复创建不重置批准和进度。Fleet 按认证操作员隔离键，Local 按本机身份隔离。Fleet 使用 operator Bearer token；Local 使用原 Console session 及 JSON/同源检查。设备凭据不能创建或批准目标。请求限制为单个 JSON 文档、256 KiB；键限制为 128 字节。

下面是字段完整的示意；目录哈希、机器人名、能力、期望地图 revision 和期限必须从你的部署取得。`mapping.build` 需要实际 Runtime 声明对应效果与完成合同。

```json
{
  "adapter": "ros2",
  "idempotencyKey": "<本次唯一请求键；重试保持不变>",
  "objective": {
    "schemaVersion": "robot.objective.v1",
    "robotId": "robot-7",
    "goal": "检查标定，建图",
    "catalogRevision": "<GET goal-catalog 返回的 64 位摘要>",
    "grants": [
      {"tool": "calibration.get", "argumentsSchema": {"type": "object", "additionalProperties": false}},
      {"tool": "mapping.build", "argumentsSchema": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["explore"]}}, "required": ["mode"], "additionalProperties": false}},
      {"tool": "mapping.status", "argumentsSchema": {"type": "object", "additionalProperties": false}}
    ],
    "effects": ["READ", "PHYSICAL_MOTION", "ARTIFACT_WRITE"],
    "resources": ["robot"],
    "completion": [{"tool": "mapping.status", "arguments": {}, "path": ["activeMap", "mapId"], "equals": "<本次要求的地图身份>"}],
    "budget": {"maxPlans": 2, "maxSteps": 8, "maxReads": 128, "timeoutSeconds": 600},
    "expiresAt": "<本次真实 UTC 绝对期限，RFC3339>"
  }
}
```

创建后返回标准 Task，未批准且只有一个 `edge.goal` 节点。核对合同后使用原 `POST /v1/tasks/{id}/approve`。暂停/取消仍走原任务入口；本版不提供已批准目标的原地修改 API，改变包络时取消旧目标并创建、批准新目标，不能编辑旧 JSON。

## 3. 边缘规划配置

Worker 现在读取 `AGENT_GOAL_PROVIDER/BASE_URL/API_KEY/MODEL`；示例见 `deploy/edge-orin/edge-worker.env.example`。`deterministic` 只处理明确离线语法；复杂条件、剩余目标的自适应编排需要配置 GOAL 模型。Local 保留原模型路由和热更新。模型端点位于本机或云端，不改变边缘拥有子任务规划和执行生命周期这一职责。

边缘收到父目标后读取本机目录和授权的语义状态，生成并校验子计划，逐步执行、核验、写检查点。Ops/Recovery 默认是依据持久结果的确定性诊断和有界建议，不意味着每个角色都单独运行大模型。

## 4. 回溯和拒绝路径

`GET /v1/tasks/{id}` 与 experience/原事件入口保留父目标、`EDGE_PLAN_CREATED`、`EDGE_CHILD_EVENT`、`EDGE_OPS_FINDING`、`EDGE_RECOVERY_DECISION` 和最终 `CAPABILITY_VERIFIED`。云端事件保留父 claim、目录与调用绑定；原始子规划上下文、回执和检查点位于边缘 SQLite，研究取证时必须一起归档。

只读失败或明确未准入可以重规划；未知动作不能。预算耗尽、目录变化、目标越界、缺少完成条件、期限或租约失效均会拒绝继续。业务读预算包含规划查询、执行器状态/验证和目标完成检查，不计目录获取/父租约，也不限制拥有原操作的有界停止监督。

全目标完成要求所有精确值条件成立。期望值必须有业务依据；不能为了“通过”把完成条件写成始终成立的版本非空或常量服务。实验示例 `hash-1` 是测试设备的固定输出，不是部署地图的真实哈希。

需要有意重复同一动作（例如 A→B→A）时，在对应 grant 明确设定 `maxExecutions`，并提供能证明所需次数/顺序的完成条件；默认相同工具和参数的写调用只准一次，重规划与重启不能重置额度。该额度不是自由重复权限，仍受总步骤、期限和设备验证合同约束。

## 5. 开发验证

```bash
go test -v ./internal/edgeagent -run TestDelegated
go test -v ./edge/worker -run 'TestCloudDelegates|TestObjective'
go test -race ./internal/edgeagent ./edge/worker ./fleet/coordinator ./core/objective ./console
```

[本轮报告](../experiments/2026-10-15-hierarchical-edge-objectives.md)解释可支持的结论与未测条件。接入具体无人机、无人车或足式前，继续按[实机测试](../operations/edge-field-testing.md)验证控制器、停止、定位和运动约束。
