# 第 17 章 云端大脑与边缘 Agent：任务权威、通讯与协作

**学习目标**：画出实际部署图，解释一次claim和完成上报，定位多机器人交接的权限与证据。先修第2、5–9章；长上下文在第18章，完整任务复盘在第19章。

本章固定到数字版1.2.0的源码快照。系统共享能力内核、Runtime与证据契约，按角色装配工具和模型。中心大脑不是一份拥有全系统真相的聊天上下文，边缘也不是无条件执行中心文本的电机代理。

## 17.1 两种形态与任务权威

| 形态 | 任务权威与持久状态 | 执行者 |
| --- | --- | --- |
| Local自治 | Local Task/Revision/事件与SQLite | 本地能力执行器调用一台Runtime |
| Fleet机群 | 云端Task/Coordinator/EventLog/Outbox、共享世界与资源 | 每机器人Edge Worker调用本机Runtime |
| 云端模型Assist | 受限推理服务，不是任务权威 | 只返回推理候选，不批准或派发动作 |

```text
Local: 浏览器 → Local Agent(task/ops/recovery) + SQLite → Runtime

Fleet: 浏览器 → Fleet API → Task/Revision → Coordinator
                                  │             │
                         EventLog/Outbox    WorldHub + 资源租约
                                  │             │
                         HTTP/队列唤醒 → Edge Worker A/B/...
                                              │
                                  本机执行库 + Runtime + Backend
```

同一Runtime不能同时接受Local与Fleet两套任务派发。切换前停止旧派发并对账，重新建立设备和资源身份。当前没有自动接管未知动作的双主故障切换。

## 17.2 角色Harness共享什么

共享内核完成模型/工具循环、上下文预算、业务参数、证据和trace记录；Harness按角色提供目录、模型、任务权限和执行入口。

Server系统Agent只看到机群只读查询和待审批草案工具，使用独立SYSTEM路由。Local/Edge的规划和恢复只见适用的本机工具。GOAL、INTENT、PLANNING、RECOVERY、SYSTEM可以独立配置；角色不自动对应独立模型进程。

Local默认task、ops、recovery三个角色。Task推进批准任务，Ops定位异常，Recovery提出目录内计划；实际恢复由受控executor/verifier完成。当前Ops/Recovery主要是确定性角色。Fleet Worker并不天然拥有同样的三个Agent拓扑。

阅读[角色装配](../../internal/agentharness/profile.go)与[Local组合根](../../cmd/local-agent/agentruntime.go)，检查实际接线和enabled/publishers日志，比从类型名称猜权限更可靠。

## 17.3 三种通讯不能混淆

| 通道 | 传什么 | 不能推导的保证 |
| --- | --- | --- |
| Local EventBus | 注册topic的执行事实、异常、计划与健康 | 收到等于落盘、无限重放、跨主机认证 |
| TaskEvent/EventLog/Outbox | 持久版本、事件、检查点和待投递工作 | 物理世界与数据库同一事务、物理效果exactly-once |
| 云边HTTP、FleetGateway与Runtime gRPC | task、claim、事件、completion、心跳、控制和本机命令 | 队列通知或模型输出即动作授权 |

Fleet任务源可使用Redis Streams或HTTP长轮询。它们唤醒worker；worker仍领取当前claim，再检查批准内容、设备和资源。设备HTTP凭据、FleetGateway mTLS和Runtime安全字段分别约束不同边界。

Agent信封使用`agent.event.v1`，恢复绑定到task/revision/robot/step/command/source/anomaly/plan。Cloud-edge使用`execution.context.v1`，成功收尾使用`context.completion.v2`。这三种版本不能合成一个任意聊天信封。

## 17.4 从目标到冻结执行内容

配置GOAL模型时，Planner根据注册能力读取planningFields，提出调用序列。确定性层校验schema、目录、效果、资源与目标覆盖；批准前只读查询不派发物理动作。

TaskRevision固定批准的执行定义，execution digest覆盖活动目标/Intent/Plan，而非所有不断新增的显示事件。新提案可以提高aggregate版本但不自动改变active revision，待审批版本不能由completion收尾偷偷激活。

模型只选择目录工具和业务参数。task/command/robot、安全profile、期限、claim、资源和fence由受信执行层产生及复核，模型不生成有效执行权。

## 17.5 一次云边交接的执行Basis

| Basis内容 | 防止的问题 |
| --- | --- |
| task/revision、aggregate/claim version | 混用旧任务快照与新认领 |
| intentIndex、stepId、commandId | 把一个步骤的结果算到另一个步骤 |
| robot、adapter、catalog revision | 错设备、错驱动或目录热变更 |
| executionDigest | 批准内容在传输/执行期间被改变 |
| resourceId、fencingToken | 旧持有者沿用旧资源授权 |
| worldId、revision、source/sequence基线 | 用无关世界或旧证据确认新命令 |

Basis在中心INTENT_CLAIMED事务、状态与checkpoint一起持久化。摘要只是内容一致性校验，不是签名、审批或实时观测。typed uint64和精确JSON保持fence/sequence精度，不能用被float64舍入的通用投影复验身份。

边缘接受前核对Task/Basis，以同库CAS持久化ACCEPTED后才允许执行。每步调用Runtime之前再次核对任务/approval/claim、Runtime身份/目录和物理ready；慢策略推理之后也要检查。目录READY不能代替审批或现场使能。

## 17.6 重连、完成与收尾

| 持久阶段 | 重启行为 |
| --- | --- |
| 没有checkpoint | 校验并先提交ACCEPTED，失败不派发 |
| ACCEPTED | 结果可能未知，停止并对账 |
| EXECUTED | 成功结果与completion outbox已提交，只重发完成 |
| COMPLETED | 不动作，补ACK可幂等重试 |

中心v2回执、成功checkpoint、pendingCompletion和finalization outbox在同一事件存储事务中提交。若完成响应丢失且中心已切换revision，旧EXECUTED只重发精确原结果；中心核对不可变receipt确认旧成功，不推进新图。

finalization用receipt记载的原grants和effects重入：释放原租约、发布共享归属、尊重更高fence、激活已批准待切换版本、更新终态。pending未闭合时不领取新工作；整个收尾不再次调用Runtime。

同机器人并发worker须共享执行DB，CAS限制唯一接收者。不同DB不是分布式锁，还依赖Runtime幂等/fencing。ACCEPTED后的未知中断不被v2回执修复；成功事务前外部Transfer窗口、失效且未投影的共享grant、旧leader混跑仍需对账。

## 17.7 多机器人合作的物理交接

已有双机器人流程是A把红方块放入交接区，B取出并送到目标区。Coordinator要求前序意图成功后下一意图才可领取，按robot绑定/未绑定意图协调；它不是任意并行DAG或通用最优匹配器。

交接完成须同时满足物料在交接区、稳定、A已不再持有、来源新鲜，并核对命令后世界/来源序号基线。资源owner/fence在确认后转换，ready outbox通知B；B用自己的目录、grounding和Runtime执行。

物料ID、共同world坐标和标定必须可核对。旧帧重报不能作为多个独立稳定样本，“我交给B了”的Agent消息不能替代物理交接。见[Harness](../../core/harness/evaluator.go)和[双机操作流程](../../docs/operations/robocasa-handoff.md)。

生产中心要求共享Redis租约，leader owner包含唯一进程incarnation，避免重启继承旧身份。这仍不覆盖MySQL、Redis、WorldHub和物理效果的全局事务或跨主机共识。

## 17.8 异构接入与异构调度

RobotProfile声明本体、原生关节/限位、末端、传感器/标定、动作键和工具子集。Backend在本地转换SDK/ROS操作，Runtime提供统一命令/观察/停止/journal边界。

不同机构可以使用相同工具语义，但必须实际实现能力与验证器。单一capability plan绑定一台robot和一份catalog，Task还有单Adapter；worker拒绝驱动不匹配。任务入口`auto`归一为mujoco，不是任意驱动通配符。

所以“底盘运输、固定单臂加工、传感器平台复验”是可沿接口扩展的合作方向，还需每节点驱动/目录契约、能力匹配、交接区验证器和真实全流程验收。当前异构Python/Go边界测试不是三类实机组成长程任务的完成证据。

## 17.9 模型、部署与数据边界

语言模型路由与动作策略sidecar分开。模型Assist使用独立受限凭据，只提供推理；本地保持审批、设备、状态与资源权威。模型key不下发到Robot Runtime，不写入公开trace。

统一镜像可以按Server、Local/Orin或Fleet Worker入口部署；配置、数据卷和任务权威不同。高频传感器与控制留在设备，模型看到结构化语义字段。Orin量化、GPU并发、实体停止、HA和机群规模仍需对应验证，交叉编译或Docker启动不能替代它们。

详见[模型与目标配置](../../docs/guides/unified-capability-goals.md)、[Fleet部署](../../docs/architecture/fleet-cloud.md)、[Orin入口](../../docs/install/edge-orin.md)。

## 17.10 自测与源码路线

1. 完成RPC响应丢失是否重做放置？不重做；若EXECUTED已持久化，只重传completion。ACCEPTED仍不确定时先对账。
2. 旧receipt能否把新revision任务标成成功？不能，它仅确认既有结果，精确绑定及pending收尾尊重新状态。
3. 断网后能否无限执行已缓存计划？不能，每步需有效claim/授权/设备条件，失效时停止；物理停止性能另验收。
4. 增加Profile是否获得通用多机规划？没有；接入、匹配、执行和交接判定是不同层。

源码依次读[Coordinator](../../fleet/coordinator/coordinator.go)→[Basis](../../core/contextcontract/basis.go)→[worker checkpoint](../../edge/worker/context.go)→[receipt](../../fleet/coordinator/completion.go)→[finalization](../../fleet/coordinator/finalization.go)。故障测试与剩余窗口见[完成协议](../../docs/development/2026-10-10-cloud-edge-protocol.md)。

[上一章：工程对照](ch16-coding-agent-contrast.md) · [下一章：长程上下文](ch18-long-horizon-context.md)
