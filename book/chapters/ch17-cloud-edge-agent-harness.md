# 第 17 章 云端大脑与边缘 Agent：目标委托、通讯与协作

**学习目标**：解释云端粗目标如何成为边缘子计划；画出控制、事实与数据通道；定位恢复与多机器人合作边界。

**先修知识**：第2、5–9章；长上下文在第18章，完整任务复盘在第19章。

**关键术语**：目标包络、共享Host、claim、执行Basis、completion receipt、资源监护权

本章按数字版2.2.0固定源码 `821659dfd6782927d02924916ced5474a8142d74` 解释当前实现。推荐职责是云端制定全局目标和分工，边缘按现场事实细化并执行，Runtime/控制器负责实际运动、采集和停止。模型推理的位置不决定谁拥有任务权威。

## 17.1 两种部署入口与共享边缘宿主

| 入口 | 拥有的状态与责任 | 本机目标执行 |
| --- | --- | --- |
| Local自治 | 本地Task/Revision、批准、事件和SQLite；本地API与工作台 | 共享 `edgeagent.Host` 调用一台Runtime |
| Fleet Worker | 云端任务源、设备身份、父claim续租、控制消息与证明交付 | 同一个Host，保存本机父/子执行账本 |
| 云端模型Assist | 受限推理服务；只返回候选，不批准或派发 | 无独立任务生命周期权威 |

```text
云端大脑：全局目标 / 分工 / 共享资源
       ↓ POST /v1/objectives：持久未批准 edge.goal
云端Task/Revision → 操作者批准 → Coordinator父claim
                                      ↓
                                 Edge Worker ─┐
本地目标API → Local Task/Revision/批准 ─────────┤
                                             ↓
                           共享 Edge Host：Task / Ops / Recovery
                              ↓ 子能力计划       ↑ 持久回执
                           Executor → Runtime → ROS 2 / 控制器
                                      ↓
                         验证 / 父检查点 / 子账本 / 完成证明
```

Local与Worker是同一目标宿主的替代入口，不能同时控制同一Runtime。切换前停止旧派发、核对在途/未知/持物，再建立设备和资源身份。进程控制租约、云端fence与设备watchdog分别约束不同边界，不提供未知动作的自动双主接管。

## 17.2 两条任务通道与批准对象

| 通道 | 创建时固定什么 | 执行时谁细化 |
| --- | --- | --- |
| `/v1/tasks` 兼容通道 | 有序能力计划和目录，随后批准 | 执行器按已冻结调用推进 |
| `/v1/objectives` 目标通道 | 粗目标、授权、完成条件与预算，随后批准 | 边缘Host分代生成包络内子计划 |

现有自然语言页面和旧系统Agent/MCP草案工具仍走冻结计划通道。新通道需要显式提交 `robot.objective.v1` 合同；创建只保存单个 `edge.goal` 父节点，不调用云端规划器、不自动批准。批准后父目标和权限不因子计划代数变化。

目标创建以认证调用者与 `idempotencyKey` 确定身份。相同请求重试返回原Task并保留批准/进度，改合同复用键返回冲突；当前在线目录准入仍适用。Fleet操作者可以创建/批准，设备凭据不能给自己授予目标。后续批准、查询和取消复用任务路由，取消请求仍需设备停止反馈。

## 17.3 目标包络怎样限制自主性

| 合同字段 | 执行含义 |
| --- | --- |
| robotId / catalogRevision | 绑定本机与规范化能力目录 |
| goal | 自然语言业务目标，最多4,000字节 |
| grants | 工具、参数Schema和相同写调用次数；默认1次 |
| effects / resources | 允许的效果；v1资源只能是已绑定机器人的 `robot` |
| completion | 1–16个已注册只读服务的键路径/精确值谓词，全部成立 |
| budget / expiresAt | 持久计划/步骤/读预算与期限，不因重启重置 |

Local目录返回 `goalCatalogRevision` 与按工具名索引的 `goalCapabilities`；Fleet通过设备goal-catalog读取。它与低层Runtime服务目录摘要不同。参数包络可限定房间ID、路线或高度范围；执行前还校验Provider原始Schema、效果与资源。自然语言约束没有对应合同或验证器时不能自动成为安全规则。

单目标上限为8代计划、128次子能力调用、4,096次Host业务读取和86,400秒；每代能力计划最多16 calls。一次复合 `robot.task` 内部可能有多个物理/验证子步骤，控制循环和遥测另有预算。要记录各层单位，不能把这些上限解释为所有动作和读取的共同计数。

共享物品、电梯、区域或空域仍由全局协调层处理，本版目标合同不能取得这些资源。更长任务应提出新的明确目标与完成条件，不自动延长原期限或批准范围。

## 17.4 Harness与Task/Ops/Recovery的实际装配

Task使用 `capabilityagent.Planner` 读取受限目录和本机语义观测，输出子计划；Executor复用能力执行与复合机器人Runner，逐步验证效果。Ops从持久子回执判断明确未准入、只读失败或结果未知。RecoveryPolicy只给出建议，默认 `BoundedRecovery` 选择剩余重规划、对账或停止，最终由Host检查权威、预算与重复效果。

这些是逻辑角色，不要求分别调用LLM。Ops/Recovery没有命令端口，即便换成模型策略也不能解除unknown或扩张grant。Local原 `agentruntime` 事件总线继续承担原本调查协作；Worker没有原样继承整套总线，也不是新增的跨机器远程Agent宿主。

Harness共享模型/工具循环、上下文、参数约束和trace，按角色装配目录与路由。GOAL、INTENT、PLANNING、RECOVERY、SYSTEM可以分别配置，但配置名不等于该角色已经在对应部署启用。源码应同时读[Host](../../internal/edgeagent/host.go)、[监督策略](../../internal/edgeagent/supervision.go)和[Local装配](../../cmd/local-agent/agentruntime.go)。

## 17.5 通讯分别承担什么权威

| 通道/合同 | 内容 | 消费后的边界 |
| --- | --- | --- |
| Local `agent.event.v1` | 执行事实、诊断、建议与因果身份 | 实时收到不等于落盘；恢复核对持久链 |
| 云边 `execution.context.v1` | task/revision、claim、Basis与执行摘要 | 通知仅唤醒；按实时批准与租约准入 |
| `robot.objective.v1` / Host事件 | 目标包络、子计划、进度、finding/decision和证明 | 建议与进度不扩大父授权 |
| `context.completion.v2` | 精确结果回执与可重入收尾 | 重传旧结果，不产生新动作 |
| `agent.data.v1` | 地图/状态等advisory业务数据 | ACK只代表收取，不批准、不证明到达 |

Fleet任务源使用Redis Streams或HTTP长轮询。Worker先把通知持久写入本机inbox再ACK，重复通知去重后领取当前claim。设备HTTP凭据、FleetGateway mTLS、Runtime安全字段分别约束不同边界。任务事件/outbox与状态在同库提交，实体动作仍不属于数据库事务。

目标父事件包括 `EDGE_PLAN_CREATED`、`EDGE_CHILD_EVENT`、`EDGE_OPS_FINDING`、`EDGE_RECOVERY_DECISION`。父目标、代数、子任务、步骤和命令标识连接执行历史。模型看有限进度；云端最终核验已申报子计划、逐步验证、次数和完成谓词，进度消息遗漏可以从本地账本补传，不能用重新执行补消息。

## 17.6 Basis、持久预算与恢复表

Basis绑定task/revision、aggregate/claim version、intent/step/command、robot/adapter/catalog、executionDigest、资源owner/fence及世界来源基线。摘要检验内容一致性，不是签名、审批或新鲜物理观测。fence/sequence使用精确typed uint64，不能经float64舍入后再比较。

边缘在同库CAS保存ACCEPTED后才允许工作；每次规划/工具动作前重新检查批准内容、原父claim、目录与设备。云断连失去父claim续租后停止，没有离线继续授权。

| 持久事实 | 允许恢复 | 禁止行为 |
| --- | --- | --- |
| 无checkpoint | 当前权威有效后先落盘接受 | 落盘失败仍派发 |
| 安全目标检查点，无在途动作 | 重入原claim，使用原预算与子计划 | 新建任务或重置额度绕过历史 |
| 子动作已完成、父确认丢失 | 同时核对StepCompleted与完整CAPABILITY_VERIFIED，追加EDGE_STEP_RECOVERED | 仅凭开始/回执或错配证据重放 |
| 写可能生效，验证缺失 | 保留UNKNOWN_OUTCOME并对账 | Recovery建议或新command使其变可重试 |
| 已验证结果，报告待交付 | 只补传原计划/证明/completion | 重做运动补回执 |
| COMPLETED | 不动作，补ACK | 将旧成功归给新revision |

父检查点保存代数、步骤/读预留、完成效果、Pending与完成读状态。完整子账本核对已确认效果次数；只给模型最近8项不会降低检查范围。`CanResumeGoal`只是本地只读预检，实际Run仍检查实时权威。

全部子动作确认后，完成条件瞬态不可用可做全目标最多两次持久只读重试，不重规划。读取确认落盘中断则要求复核；权限拒绝、取消和总期限超时不重试。初次运行与续跑共用原claim的失败上报，取消后的有界诊断上报也不产生动作权。

## 17.7 完成证明、中心收尾与物理信任

中心v2 receipt、成功状态、pendingCompletion与finalization outbox在同一存储事务提交。边缘丢失完成响应后只重发原结果；即使revision已经切换，中心也只确认旧精确回执，不推进新计划。finalization按原grants/effects释放租约、投影归属并尊重更高fence与取消；pending未闭合不得领取新工作，收尾不调用Runtime。

目标的父证明还需关联每代子计划、逐步设备认证的验证和全部完成谓词。过了执行期限可交付既有证明，但不能再动作；原父权威失效仍需对账。云端检验合同和设备证据，不能独立测量机器人是否真的到达，`eventDigest`也不是硬件远程证明。

同机器人并发Worker必须共享执行DB，CAS不是跨不同库的锁。成功事务前外部Transfer、失效且未投影的共享grant、旧leader混跑等窗口仍需对账；不能把v2收尾概括为覆盖全部物理exactly-once。

## 17.8 多机器人合作与异构移动

已有双机器人流程由A把红方块放入交接区，B取出送到目标区；Coordinator按前序成功领取下一Intent。物料稳定在区内、A不再持有、命令后来源新鲜与owner/fence转换分别验证。共同world、物料ID和标定必须对应，“A已交给B”的消息不能替代交接证据。

目标Host只绑定一台robot/catalog，v1只有robot资源；既有按序交接与新单机目标通道不能直接相加成通用异构并行编队。跨机器人目标分配、共享区域预约、联合取消和联合后置条件仍需上层协议与实际验收。见[双机操作](../../docs/operations/robocasa-handoff.md)。

异构差异通过RobotProfile、能力Manifest、验证器和ROS 2适配器表达。UGV、足式和多旋翼可以共用navigation/verify_arrival语义，实际准备、限位、制动/平衡/降落由设备实现。二维Nav2保留；地形和三维空间通道检查地图/部署摘要、完整定位与路径身份。外部SLAM负责估计，Provider保存测量与地图；目标宿主不接管高频运动控制。

2026-10-14三类记录式控制器实验支持DDS/gRPC空间协议闭环，2026-10-15 Gazebo只验证单台地面机器人巡检；它们不构成无人机飞行、足式步态或真实型号认证。`auto`驱动归一规则也不是任意Adapter通配符。

## 17.9 独立数据面与跨域冲突

edge-exchange使用独立SQLite inbox/outbox、TLS 1.3与peer凭据，信封含sender/receiver、domain、单调sequence、TTL、空间/上下文身份和有界payload。双方重启用原信封继续投递，去重后ACK；它没有机器人命令端口。

```text
A本地事实 → outbox → 认证传输 → B/云端inbox → 数据ACK
                                      ↓
                     显式消费者核对身份/时效/地图与任务范围
                                      ↓
                     查询、建议或待审批方案 → 正式任务通道
```

相同mapId却摘要不同要保留冲突、拒绝关联；新修订先记录建议，不能覆盖当前地图。跨机器人pose需共同world变换，标签与空间包也各有版本。云端同事可实现合同与业务消费者，不需要共享边缘私钥或模型key。接口与双进程验证见[设备数据交换](../../docs/development/edge-data-exchange.md)。

高频感知和控制留在设备，上传任务需要的语义字段或选定证据。模型Assist只推理；原始trace即使没有Authorization也可能含业务资料，公开发布另做权限与脱敏。部署指南见[边缘实测](../../docs/operations/edge-field-testing.md)和[目标接口](../../docs/guides/delegated-edge-goals.md)。

## 17.10 小结与源码路线

按 [目标合同](../../core/objective/contract.go)→[任务创建](../../tasks/objective.go)→[共享Host](../../internal/edgeagent/host.go)→[Worker](../../edge/worker/worker.go)→[云端证明](../../fleet/coordinator/completion.go)阅读，再检查[恢复](../../internal/edgeagent/recovery.go)、[完成只读恢复](../../internal/edgeagent/completion.go)与[收尾](../../fleet/coordinator/finalization.go)。

云端与边缘共享可核对的目标、权限和事实身份，不必共享同一份聊天。边缘自主性来自有界目标委托，稳定性来自持久预算、逐步验证及未知时停止。对应软件与实际Gazebo证据在第19章分别讨论。

[上一章：工程对照](ch16-coding-agent-contrast.md) · [下一章：长程上下文](ch18-long-horizon-context.md)

## 17.11 分层习题

基础题 B：解释Local、Fleet和Assist为何不是三套等价任务权威。

工程题 E：画出A放置到B认领的完整消息/资源/验证路径，标出丢响应点。

研究题 R：为跨Adapter合作定义节点合同和联合后置条件，比较按序与并行方案的验证成本。

评分与参考要点见[附录 I](../appendix/I-exercise-guide.md)。
