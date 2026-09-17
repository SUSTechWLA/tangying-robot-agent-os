# Agent 事件规范

事件词表固定在 `core/agentcontract/event.go`。订阅一个词表里没有的 topic 会在启动时报错，而不是静默地永远收不到——拼错的订阅在运行期表现为"这个 Agent 看起来健康但从不报告"。

## topic 与命名空间

| topic | 发布者 | 含义 |
| --- | --- | --- |
| `task.started` | task | 任务开始执行 |
| `task.completed` | task | 任务到达成功终态（**闭环被满足**，不是工具返回成功） |
| `task.failed` | task | 任务到达不成功终态（含仍需人工对账的状态） |
| `action.executed` | task | 一次工具下发及其状态迁移 |
| `evidence.collected` | task | 某个步骤归档了动作后的证据 |
| `state.transition` | task | 任务生命周期状态变化 |
| `ops.anomaly_detected` | ops | 发现异常 |
| `ops.root_cause_hypothesis` | ops | 结构化的、有证据链的根因假设 |
| `ops.recovery_proposed` | ops | 请求考虑一个恢复动作（**是请求，不是动作**） |
| `ops.escalation_required` | ops | 情况需要人 |
| `ops.recovery_deferred` | orchestrator | 建议在当下被挂起，并说明原因 |
| `agent.registered` | orchestrator | 启动时每个启用 Agent 一条 |
| `agent.health_changed` | orchestrator / ops | **健康状态真的变化时**才发 |
| `agent.permission_denied` | orchestrator | 请求超出该 Agent 声明的权限，被拒绝 |

命名空间通配：`task.*`、`action.*`、`evidence.*`、`state.*`、`ops.*`、`agent.*`。

匹配规则只有两种形式：**精确 topic** 或 **命名空间通配**。`ops.*` 匹配 `ops.anomaly_detected`，不匹配裸 `ops`，也不匹配只是前缀相同的 `opsx.y`。没有更复杂的 glob——多一种形式就多一处会写错的地方。

每个 topic 必须属于某个命名空间，这条由测试钉住：不属于任何命名空间的 topic 只能按精确名订阅，意味着按命名空间写的观察者会静默地听不到它。

## 事件信封

```json
{
  "id": "task-abc#7",
  "topic": "ops.anomaly_detected",
  "taskId": "task-abc",
  "taskRevision": 1,
  "stepId": "pick",
  "agent": "ops",
  "agentVersion": "1",
  "priority": "critical",
  "causationId": "task-abc#7",
  "correlationId": "task-abc",
  "occurredAt": "2026-09-16T12:00:00Z",
  "payload": { }
}
```

- `id`：从账本投影来的事件用 `taskID#sequence`，**重放同一份账本得到同样的身份**；新发布的事件用 `evt-N`。
- `taskId` 为空表示运行时级事件（如 `agent.registered`）。账本是按任务的，所以这类事件只广播、不落盘。
- `priority`：`low` / `normal` / `high` / `critical`。订阅者队列满时按优先级淘汰，**只有更低优先级的待投递事件会被顶掉**；如果待投递的都比新事件重要，丢的是新事件。
- `causationId`：直接导致本事件的那条事件。

## payload 结构

payload 的字段用 `agentcontract` 里的结构体声明并通过 `Encode()` 落地，而不是各调用点手写 map：只在发布者脑子里存在的 payload 形状会漂移，而被迫防御"每个字段都可能缺失"的消费者最终会干脆忽略这个事件。

### `ops.anomaly_detected`

```json
{
  "anomalyId": "ANOMALY_UNVERIFIED_MUTATION@execution",
  "severity": "critical",
  "component": "execution",
  "code": "ANOMALY_UNVERIFIED_MUTATION",
  "message": "有物理动作已下发但没有确认结果，必须先对账再决定下一步",
  "evidence": ["task-abc/pick"],
  "facts": { "uncertainSteps": "pick", "stepCount": 1 },
  "detectedAt": "2026-09-16T12:00:00Z"
}
```

`anomalyId` 对同一个 `(code, component)` 稳定，所以重复报告能被认成同一个问题而不是新问题。这不是洁癖：每条遥测都重报一次异常，会让人被永久地拉进告警里——仓库的故障台账已经踩过这个坑（`episodes` 计数）。

`severity` 是观察者自己的尺度（`info` / `warning` / `critical`），说的是"该多打扰人"。它**不是**机器人的故障严重度；机器人那套词表（`info`/`degraded`/`blocked`/`safety`）原样保留在 `component` 与 `code` 里。

### `ops.root_cause_hypothesis`

```json
{
  "hypothesisId": "hyp-ANOMALY_ACTION_FAILED@manipulation.pick",
  "category": "UNKNOWN_OUTCOME",
  "confidence": 1,
  "summary": "manipulation.pick 失败但没有错误码，结果未知",
  "evidenceChain": ["rule:ANOMALY_ACTION_FAILED", "..."],
  "affectedComponents": ["manipulation.pick"],
  "recommendedActions": ["不要自动重试：先对账确认这次动作的实际结果"],
  "automaticRetryForbidden": true,
  "missingEvidence": ["运行时没有给出错误码，无法确定这次动作是否到达硬件"],
  "proposedAt": "2026-09-16T12:00:00Z"
}
```

- `category` 恒为**闭环契约的失败分类**（`TRANSIENT` / `PERCEPTION` / `PLANNING` / `PERMISSION` / `RESOURCE` / `VALIDATION` / `UNKNOWN_OUTCOME` / `FATAL`）之一。复用那套词表是刻意的：仓库已经决定一个失败码只有一个安全恢复动作，观察者再发明第二套分类，两边迟早会就"能不能重试"给出不同答案。
- `confidence` 由规则显式给定，不是从数据里估出来的。确定性规则知道自己知道多少。
- `evidenceChain` 为空是不允许的：没有证据的解释就是猜测。缺证据时用 `missingEvidence` 说明缺什么。
- `automaticRetryForbidden`：结果未知时为 `true`。这是唯一一条**不允许被下游软化**的建议，所以显式携带而不是隐含在 category 里。

### `ops.recovery_proposed`

```json
{
  "proposalId": "prop-ANOMALY_UNVERIFIED_MUTATION@execution",
  "action": "先观测机器人当前姿态与目标物体位置，确认这个动作到底有没有发生",
  "automationLevel": "advisory",
  "requiresApproval": true,
  "rationale": "有物理动作已下发但没有确认结果",
  "hypothesisId": "hyp-..."
}
```

第一版 `automationLevel` 恒为 `advisory`、`requiresApproval` 恒为 `true`，有测试断言。**建议是给人做的**；把它描述成自动化会误报系统知道多少。

`deferredReason` 由 orchestrator 填写（不是提出者），值为 `PHYSICAL_ACTION_IN_FLIGHT`。

### `ops.escalation_required`

```json
{
  "escalationId": "esc-ANOMALY_SAFETY_STOP@estop",
  "reason": "ANOMALY_SAFETY_STOP",
  "urgency": "soon",
  "context": {
    "taskId": "task-abc", "component": "estop",
    "category": "PERMISSION", "summary": "机器人处于急停状态",
    "automaticRetryForbidden": false
  },
  "raisedAt": "2026-09-16T12:00:00Z"
}
```

### `agent.permission_denied`

```json
{
  "capability": "manipulation.pick",
  "reasonCode": "AGENT_READ_ONLY",
  "mutatesWorld": true,
  "mutatesTaskState": false,
  "detail": "agent ops was refused: the agent declared itself read-only"
}
```

拒绝原因码：`AGENT_READ_ONLY`、`CAPABILITY_NOT_DECLARED`、`AGENT_APPROVAL_REQUIRED`、`AGENT_NOT_REGISTERED`、`NO_MUTATION_REQUESTED`。

拒绝会记事件，因为**没有痕迹的拒绝和"根本没问过"无法区分**。观察一个 Agent 试图做什么，常常比观察它做成了什么更有信息量。

### `agent.health_changed`

```json
{ "previous": "UNKNOWN", "current": "HEALTHY", "reasonCode": "" }
```

只在状态**变化**时发布。每次检查都发一条没变过的状态，是让人学会忽略它的最快方式。

## 观察者的规则与代码

OpsAgent 第一版是确定性规则集，输入全部来自既有契约：

| 代码 | 触发 | 严重度 | 关键建议 |
| --- | --- | --- | --- |
| `ANOMALY_SAFETY_STOP` | 急停锁存 | critical | 由人工复位，系统不代按 |
| `ANOMALY_COMPONENT_FAULT` | `robot.faults.v1` 的非 info 故障 | blocked/safety→critical，其余 warning | 按机器人给出的处置方式 |
| `ANOMALY_UNVERIFIED_MUTATION` | 执行记录里结果未知的物理步骤 | critical | **禁止自动重试**，先对账 |
| `ANOMALY_ACTION_FAILED` | 工具失败迁移（用 `closedloop.Classify` 分类） | warning，重复失败升 critical | 按失败分类 |
| `ANOMALY_STEP_LATENCY` | 阶段耗时超预算 | warning | 检查设备与网络时延 |
| `ANOMALY_TELEMETRY_STALE` | 观测超龄或缺失 | warning | 检查连接与遥测上报 |

规则是纯函数（`agentruntime.Evaluate`）：同样输入同样输出，无 I/O，无自己的时钟。所以它可被穷举测试，且一个结论能从回放里被复现——调查这个报告的人会得到和观察者一样的答案。

观测新鲜度是规则的一部分，因为**没有当前观测就是什么都不知道**：其他规则沉默不能被当成好消息。

## 回放

Agent 事件与执行事件在同一份账本里，`tasks.AgentEventsFromEvents` 只投影**带 `agent` 归属**的条目，输出在 `task.experience.v1` 的 `agentEvents` 字段（`omitempty`，既有字段与消费者不受影响），控制台"系统观察与诊断"一节渲染它。

只投影带归属的条目是刻意的：工具活动是执行，不是某个 Agent 对执行的陈述；混在一起会让回放把观察者的诊断说成机器人干的。
