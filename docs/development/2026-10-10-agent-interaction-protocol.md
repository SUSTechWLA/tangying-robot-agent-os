# 多 Agent 事件与恢复交接协议（2026-10-10）

基线 `e78e00abf`。本轮沿用 `AgentRuntime → tasks.TaskEvent` 的同一持久账本，不增加第二个任务状态源。前轮 [Gazebo 恢复闭环](2026-10-05-long-horizon-recovery.md) 已验证单次瞬时读取故障可经 Ops 与 Recovery 调查后继续；[分层上下文与检查点](2026-10-09-long-horizon-context.md) 已保存完整任务版本、未决动作和云边执行身份。本轮补齐这些身份在 Agent 之间传递时的缺口。

## 实际缺口与修复

| 缺口 | 修复 | 保守边界 |
| --- | --- | --- |
| 原生事件只用进程内 `evt-N`，重启重复；持久镜像没有事件 ID 和 envelope revision | 新事件用随机 UUID；投影仍使用原账本事件 ID；事件 ID、来源、执行身份在持久 Ops 事件中保留 | 不为旧数据补造命令、机器人或版本 |
| Ops 只按 task/step/tool 分类，Recovery 只按 task/plan 回答 | 用明确 `RecoveryBinding` 贯穿原失败、异常、计划、执行与复验；去重和冷却按失败发生及执行身份隔离 | 同 step 的另一 revision、机器人或命令不能相互解除等待 |
| Publisher 直接获得原始 bus，可误填另一个 Agent 身份或声称动作已执行 | 注入绑定注册身份的发布口；只读观察者不能发布执行事实，`ops.recovery_executed` 只由受信任的 executor/verifier 接线发布 | 提案和诊断不构成物理权限；失败发布留下 `agent.permission_denied` |
| 总线按 ID 无条件去重，冲突内容被静默吞掉 | 比对执行身份和业务载荷；同内容重传去重，不同内容拒绝并记录 `EVENT_ID_CONFLICT` | 原始 runner 和镜像允许已知附加描述字段缺省，双方有值但不相同仍冲突；私有去重副本保留首次补充字段，第三条也必须一致 |
| 只读恢复仅匹配 task、工具和 trail step | 在同一持久账本要求完整、按顺序的 source failure → anomaly → plan → verified execution，并检查当前任务版本/机器人仍一致 | 缺失绑定、旧计划、手动执行、错误工具、未复验或伪造因果链不能放行 |

## 事件信封

新运行时事件使用 `protocolVersion: "agent.event.v1"`。信封字段为 `id`、`topic`、`taskId`、`taskRevision`、`robotId`、`stepId`、`commandId`、`agent`、`agentVersion`、`causationId`、`correlationId`、`occurredAt`、`priority`、`payload`。

- `id` 标识一次事实；原始 `TOOL_ACTIVITY` 与镜像共用 ID。新 Agent 事实用 `evt-<UUID>`，不依赖进程启动次数。账本投影保留原 `eventId` 或 `taskId#sequence`。
- `taskId` 的持久归属来自所在任务账本。`taskRevision`、`robotId`、`stepId`、`commandId` 只能复制来源的声明；不能查询最新任务补齐历史身份。缺失保持空或零，意味着未知。
- `correlationId` 当前等于 task ID，方便检索整项任务；执行尝试的精确关联由 binding 决定，不能仅以 correlation 判断同一命令。
- `causationId` 指向直接导致当前事件的真实事件 ID。Ops 在调用 Publish 前分配 anomaly ID，避免传值发布后本地仍为空而丢失因果链。
- `priority` 在 Go JSON 中是整数：0/1/2/3 对应 low/normal/high/critical。未知 topic、未来不支持的协议版本、信封与载荷中明确矛盾的身份被拒绝；`AgentRuntime.Rejected()` 可读计数。

`TaskEventFromEvent` 把 Ops 信封元数据写入其 payload（事件 ID 字段名为 `eventId`），任务归属保留在所属账本，StepID 同时在账本行保存。原始动作与准备失败的镜像保留已有 body schema，不额外加入 `protocolVersion`，避免把同一物理事实的两条路误判为内容冲突；其运行时信封仍规范化为 v1。原始 `CAPABILITY_*` 事件保持原契约。

## 恢复 binding 与状态生命周期

```json
{
  "taskId": "task-example",
  "taskRevision": 2,
  "robotId": "robot-home",
  "stepId": "rev-2-cap-04",
  "commandId": "task-example/rev-2-cap-04",
  "sourceEventId": "task-example#87",
  "anomalyEventId": "evt-anomaly-uuid",
  "planEventId": "evt-plan-uuid"
}
```

以上是示意身份，真实值由来源事件复制。Ops anomaly 的 binding 尚没有 planEventId。Recovery 拟定计划时分配 planEventId，然后 Supervisor 直接复制完整 binding 到 `recoveryexec.Request`，真实执行记录器再复制到 `ops.recovery_executed`；不通过“最近计划”缓存查找，也不由模型生成。

```text
TOOL_ACTIVITY FAILED（目录已证明只读，含原 eventId）
    │ cause = sourceEventId
    ▼
ops.anomaly_detected [ops]
    │ cause = anomalyEventId
    ▼
ops.recovery_plan [recovery]
    │ cause = planEventId
    ▼
ops.recovery_executed [recovery-executor]
    │ execution.read-history + automatic + executed + independently verified
    ▼
原等待执行器核对持久完整链、当前 revision/robot、原冻结目录及持久重试预算
    ▼
CAPABILITY_READ_RETRY（原 commandId，仅一次读取重试）
```

`CAPABILITY_READ_RECOVERY_REQUESTED` 必须先持久化。等待器只消费该标记之后的匹配事实，要求原失败、anomaly、plan、executed 依次出现且因果 ID 一致。只读独立复验仅证明诊断读取可用；它不能关闭物理未知结果，不能代替抓放或导航完成验证。`ops.anomaly_cleared` 只是本轮观察未再发现该情况，不等于机器人动作成功。

计划 verdict 为 ESCALATE 时结束匹配等待；执行失败或未复验也停止。超时、取消及原始持久重试预算继续沿用前轮规则。暂停恢复、新 revision 与云边 claim 的权限由现有任务与执行协议决定，Agent 消息不自行改变状态。

## 所有权与交付保证

| 发布口 | 可表达的事实 | 不授予的权限 |
| --- | --- | --- |
| 注册观察者 / 诊断 Agent | 异常、假设、提案、计划、升级请求、自身健康 | 动作执行、任务终态、恢复已执行 |
| 注册执行 Agent（允许改变 task state） | 动作/证据/任务生命周期事实，以及诊断提案 | 不以发布事件代替 Runtime/审批门禁 |
| 宿主 runtime / ledger bridge | 注册、拒绝、延期、原始账本投影 | 不修改物理回执 |
| 真实 recovery executor/verifier 的组合根 | `ops.recovery_executed`；ran 与 verified 分别记录 | Recovery 模型无此发布口，也无物理重试端口 |

这是同一受信任进程内的注册身份与权限边界，不是对恶意 Go 插件的隔离或跨主机密码学认证。云边交接另用 `execution.context.v1`、claim/fence、持久 checkpoint 与 completion receipt。

总线是有界观察通道，慢订阅者会丢事件且有计数；它不承诺 exactly-once 或无限重放。ID 去重窗口有界，持久任务账本是审计依据。恢复放行读取持久链：若任何关键事件未成功持久化，即使实时订阅者曾收到也不能放行。当前同步发布会先排队再写 sink，不能把实时收到事件当作持久化确认。

## 验证与本轮实测关联

新增离线回归覆盖：注册身份冒充、只读观察者伪造执行/复验、跨 Runtime 重启 ID、空载荷仍持久化元数据、同 ID 内容冲突、不同机器人/版本/命令的失败隔离，以及实际 Ops→Recovery→Supervisor→执行记录器→账本等待器的整条软件接线。该集成测试用假 verifier 结果只验证身份传递；真实读取复验由已有 recoveryexec 测试与本轮 Gazebo 实测验证，不能把假结果称为物理成功。

针对性命令与原始日志：

```sh
go test -race ./core/agentcontract ./agentruntime ./internal/autorecovery ./internal/recoveryexec ./cmd/local-agent ./tasks
# artifacts/acceptance/long-horizon-20261010/agent-protocol-race.log

# 最后补充三变体冲突修复
go test -race ./agentruntime ./cmd/local-agent
# artifacts/acceptance/long-horizon-20261010/agent-protocol-identity-final-race.log
```

错/缺 task、revision、robot、step、command、source/anomaly/plan ID，以及三段断裂 causation 都必须拒绝读取继续。已有物理未知结果及 action 镜像兼容回归仍执行。三变体回归覆盖 safetyLevel、receiptObservationId、evidenceSource：原始事件缺字段、第二条镜像补齐、第三条声明冲突必须拒绝，且原始 payload 不得被归一化写回。六包 race 与最后两包补测均通过；补测中曾碰到其他 Agent 尚未写完的编译错误，失败日志 agent-protocol-mirror-final-race.log 保留，最终补测没有该错误。

本轮运行制品写入 `artifacts/acceptance/long-horizon-20261010/`。最终 task ID、事件序号、source SHA 和验收结论由同目录实测清单及本轮主报告确认；本文件不把 2026-10-05 的历史成功当作新协议实测。

### 本轮 run-001 发现与对应修复

真实任务 `task-224652b76775477ce79d8ace`（`run-001/final-task.json`，SHA256 `37ef96978c8e84dc13338c39450fb7fd964572b5dee74c286d7ef1cfefaf5f15`）在 `rev-1-cap-02 / mapping.build` 进入 `RECOVERABLE_FAILURE`。seq 25 的错误为 `RGBD_NOT_READY,JOINT_FEEDBACK_STALE,SUCTION_FEEDBACK_STALE,IMU_NOT_READY`，`mutatesWorld=true`、`outcomeUnknown=true`；这不是已恢复成功的长程任务，也没有重放该未知物理动作。

这次实际事件证明了命令级协议接线：seq 26 的 `TOOL_ACTIVITY FAILED` → seq 30 Ops anomaly → seq 34 Recovery plan → seq 35 executor 回执，保留同 task/revision/robot/step/command/source/anomaly/plan 绑定和对应 causation。其他按整个任务归纳的异常没有唯一命令来源，其 binding 明确保留未知，不据此放行命令。

现场另发现单工具诊断被归档工具阻断：seq 35、37、39 的 executor 回执均为 `executed=false, verified=false`，决策候选包含一个诊断工具和 `context_read`；`SingleToolDecider` 把回查误算为第二个业务工具，返回“声明了 2 个工具”。修复仅从选择集合中排除 Loop 保留的 `context_read`；Loop 已拒绝外部注册此名称。两个业务工具、相似业务名字、需参数工具和权限门禁均保持原规则。

`long_history_diagnostic_test.go` 重现了初始长历史即触发归档，再读取大结果并结束的真实形态；独立重读分别成功和失败，以及首次读取失败不重试都有回归。`singletool_test.go` 验证只排除该保留名字。最终 `actionloop / recoveryexec / cmd/local-agent` race 通过，日志为 `long-history-diagnostic-final-race.log`；旧失败事件及 `long-history-diagnostic-race.log` 原样保留。后续实测须用新 build，不能据此把 run-001 改判成功。

### run-002 的恢复链与镜像字段缺口

真实任务 `task-79f7e1765770f7b7de3e61db` 在建图完成后，seq 32 记录 `CAPABILITY_READ_RECOVERY_REQUESTED`，seq 34 为注入的 `navigation.status` 只读失败；seq 37 Ops anomaly、seq 39 Recovery plan、seq 40 `recovery-executor` 回执保留完整绑定与 causation。seq 40 为 `executed=true, verified=true`，确认独立诊断读取；seq 41 才用原 commandId 进行一次读取重试。这不是物理动作重放。

随后实际导航子动作揭示双路径编码差异：直接 `action.executed` 使用 `ActionPayload.Encode/cloneFacts` 的标量诊断投影，导致 `goalPose` 数组或嵌套参数被丢弃，同时空参数及零 fencingToken 被省略；`TOOL_ACTIVITY` 则保存实际命令参数和零值。`response-00823.json` 已有 44 条 `EVENT_ID_CONFLICT`，详见 `run-002-action-mirror-audit.json` 的逐对差异与源 SHA。修复只让直接事件保留同源 `command.Parameters`、`command.FencingToken`；不放宽总线的身份或参数比较。真实空参数、空对象、导航位姿数组、嵌套参数及非零 fence 都有两路径回归，改变参数或 fence 仍被拒绝。当前 run-002 的这些误报不能删除，也不能宣称该运行零冲突；修复须在之后的新 build 验证。

### GOAL 规划输入留存

手工 GOAL 规划循环现在在每轮调用模型前生成 managed snapshot，并把同一指针交给 `LLMDecider`；`plan.capabilities.planningTrace[].context` 保存准确的模型输入、预算、归档及 `model_request_json` 原文和 SHA。成功草案中先前被拒绝的模型轮也保留；完整失败时 Planner API 只返回非可执行 trace bundle 和错误，现有 `Service.Create` 不创建失败任务。模型调用发生在任务创建前，所以 scope 只绑定真实目录 robotId，taskId 为空、revision 为 0，不用后来生成的任务身份倒填历史。

发生归档时，规划器提供同作用域的内部 `context_read`；先前 SHA 留在同一次规划的私有归档集合中供连续分页，不能跨规划访问。它计入原有 10 轮总上限，不消耗 6 次 provider 读取预算，不能进入待审批的执行 calls。无法容纳必要状态时直接拒绝上下文，不发模型或机器人请求。

`planning_context_test.go` 验证实际 HTTP 原文经任务及不可变版本 SQLite 关闭/重开后逐字节一致，以及错误轮、旧 SHA 分页、禁止批准回查、读取/轮数预算。五包 race 日志为 `planning-context-final-race.log`。`planning-input-retention-audit.json` 固定了 run-001 与 run-002 旧草案和批准计划的 SHA：两次旧运行分别有 6 轮、5 轮规划记录，但都没有 context，不能用新源码反推或补造当时模型输入。新格式需要后续真实只读自然语言任务单独验收。
