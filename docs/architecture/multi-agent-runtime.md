# 多 Agent 运行时

Agent 层是可扩展多 Agent 运行时。**当前启用三个 Agent**：

| 配置名 | 实现 | 职责 | 权限 |
| --- | --- | --- | --- |
| `task` | `edge/agent.Runner` | 执行任务（含物理动作） | 可改世界、可改任务状态 |
| `ops` | `agentruntime.OpsAgent` | 只读观测：异常检测、健康检查、结构化根因假设 | 只读，不动机器人、不改任务状态 |
| `recovery` | `agentruntime.RecoveryAgent` | 定位问题、从动作目录挑选步骤、产出恢复计划 | 只读事实 + 只写展示态，**没有任何执行端口** |

**恢复计划由谁执行**：`recovery` Agent 本身不持有任何执行端口（这一点由反射测试钉住），
执行走的是另一条通道——`internal/recoveryexec`（执行器）与 `internal/autorecovery`（自动通道）。
自动通道**只跑目录标为 `read_only` 的步骤**；`bounded_write` 要人批准，`never_automatic` 谁也跑不了。
详见 [恢复 Agent](recovery-agent.md) §9。

`eval`、`experience`、`escalation` 目前**只有接口与扩展点，没有实现**，见[如何新增一个 Agent](../development/adding-an-agent.md)。

恢复 Agent 的完整设计、计划字段说明与轨迹读法：[恢复 Agent](recovery-agent.md)。

## 一句话设计取舍

运行时是执行过程的**观察者，不是执行过程的参与者**。

任务的完成判定、证据要求、重试规则仍然完全由既有的闭环契约（`core/closedloop`）、失败分类、权限/审批/租约/fencing 门禁和任务服务决定。本包里的任何东西都改不了这些结果：EventBus 慢、OpsAgent 错、整个运行时被关掉，任务的执行与结论都不变。

这不是口号，它决定了三处代码形态：

1. **发布事件不返回错误**。调用方是正在执行任务的 Agent，一个必须处理的发布失败会把可观测性变成安全路径的一部分。
2. **慢订阅者丢自己的事件，不阻塞发布者**。每个订阅者独立有界队列，溢出记在自己头上。
3. **OpsAgent 不持有任何执行端口**。它没有 invoker、没有 task service、没有 bus 句柄，"观察者碰不到机器人"是这个类型本身的性质，不是一条要靠人记住的规矩。

## 分层

```
                    ┌─────────────────── Orchestrator ───────────────────┐
                    │ 事件路由 · 优先级仲裁 · 全局状态一致性              │
                    │ 权限门控 · Agent 生命周期                          │
                    └───────┬───────────────────────────────┬────────────┘
                            │ 订阅（带 topic 匹配）           │ 路由/仲裁
                    ┌───────▼────────┐              ┌───────▼─────────┐
                    │   EventBus     │              │  AgentRegistry  │
                    │ pub/sub·持久化 │              │ 注册·发现·启用  │
                    └───────┬────────┘              └───────┬─────────┘
                            │ TaskEvent 投影                │ 接口
                    ┌───────▼────────┐              ┌───────▼──────────┐
                    │ tasks.Service  │              │ TaskAgent  OpsAgent│
                    │  （现有回放）  │              │ Eval/Exp/Escalation│
                    └────────────────┘              │  (仅接口，不实现)  │
                                                    └────────────────────┘
```

包边界是刻意这样切的：

| 包 | 放什么 | 依赖 |
| --- | --- | --- |
| `core/agentcontract` | `Agent` 接口、事件与 topic 词表、payload 结构、三层记忆接口 | 仅标准库 |
| `edge/agent` | TaskAgent：既有 `Runner` 原生实现 `Agent` | `core/agentcontract`、既有执行链 |
| `agentruntime` | EventBus、Registry、Orchestrator、权限门控、OpsAgent、RecoveryAgent、记忆实现 | `core/agentcontract` 等，**不导入任何 Agent 实现** |
| `cmd/local-agent` | composition root：唯一同时知道"有哪些 Agent"和"怎么托管"的地方 | 全部 |

接口放在 `core/` 而不是运行时包，是因为 TaskAgent 必须能作为 Agent 使用，而核心执行路径不应该因此导入那个还知道注册表、事件总线和编排的层。`agentruntime` 不导入具体 Agent，所以**新增 Agent 不需要改运行时**。

## 为什么复用既有事件流，而不是新建事件存储

仓库已经有一条按任务持久化、有序、带回放投影的事件账本（`tasks.TaskEvent` → `console` 的任务全过程回放）。再建一个 Agent 专属事件存储，就会出现同一件事的两份记录，它们会不一致，而回放只显示其中一份。

所以 Agent 事件走同一条账本，两个方向：

- **账本 → 总线**：执行记录的 `STATE_CHANGED`、`TOOL_ACTIVITY` 等被投影成 Agent 词表的 `state.transition`、`action.executed`。观察者因此看得见执行，而执行不需要知道有观察者。
- **Agent → 账本**：Agent 发布的事件以 topic 作为事件类型追加进同一份账本。**这就是 OpsAgent 的诊断出现在普通人已经在看的任务回放里的原因**——回放本身不需要知道 Agent 的存在。

同一件事可能两条路都到，所以 `AgentRuntime` 会抑制已经投递过的事件身份。没有这层抑制，每个动作都会在回放里出现两次。

## 关键物理动作不被抢占

TaskAgent 正在执行关键物理动作时，OpsAgent 的恢复建议不能直接抢占——这条规则的落点是 `Orchestrator`，不是 OpsAgent 自己：

- `action.executed` 的 `SENDING`/`RUNNING` 到 `CONFIRMED`/`FAILED` 之间，该任务被视为"有动作在飞"（按**计数**，不是布尔：两个动作重叠时，布尔会在第一个完成时错误解除挂起）。
- 此期间到达的 `ops.recovery_proposed` **不投递给任何 Agent**，改记 `ops.recovery_deferred{deferredReason: "PHYSICAL_ACTION_IN_FLIGHT"}`。
- 延迟不是拒绝：到达安全点后建议会被重新投递（带新的事件身份，因为原身份已被消费）。

让 Agent 自己约束自己会把这条规则变成建议；而且如果把仲裁建立在"Agent 订阅的并集"上，关掉一个观察者就会悄悄关掉保护移动机器人的那条规则。所以 Orchestrator 自己订阅全部命名空间。

## 发布通道由运行时注入

`Agent` 接口刻意没有 `Publish` 方法：只消费事件的 Agent 不该被迫实现一个用不到的端口。要发布事件的 Agent 实现可选能力 `agentcontract.Publisher`（一个 `SetPublish` 方法），`Orchestrator.Start` 会把总线交给每一个实现了它的已启用 Agent。

注入放在运行时而不是组合根，是因为一次真实事故：接线代码只给执行 Agent 手工装了发布通道，观察 Agent 没有装，于是它发现的每一个问题都进了告警存储却从未上总线——恢复 Agent 永远等不到触发，而控制台照样列着发现、每个 Agent 照样报健康。**单元测试全绿，因为每个测试都自己注入了 `Publish`：它们验证的是一套生产环境并不存在的接线。** 详见[那份事故记录](../development/2026-09-17-mute-observer-and-plan-identity.md)。

`Orchestrator.Publishers()` 把结果打进启动日志，因为"没什么可说的"和"根本说不了话"从外面看是一样的：

```text
agent runtime started: enabled=[task ops recovery] publishers=[task ops recovery] disabled=[]
```

## 发现的身份只有一个定义

一个发现的身份是 `code@component`（`agentcontract.AnomalyIdentity`）：同一种失败发生在不同组件上，是两个问题、两份计划。上报去重键在它后面追加 `#<数量>`，所以稳定状态不刷屏、恶化的状态必须重报。

这条规则必须只有一处实现。曾经有三处：告警存储按 `code@component` 建索引、恢复 Agent 按裸 `code` 归档计划、控制台按裸 `code` 查询——结果是 11 个不同的组件失败全部显示第一份计划，且诊断栏里写着另一个组件的名字。

## 权限门控

声明"我是只读的"是文档，不是控制。真正的控制是 `Orchestrator.RequestMutation`：任何要改世界或改任务状态的请求都要对照该 Agent 声明的权限，不匹配就拒绝，并记 `agent.permission_denied`。

门控不重新推导安全：它不判断动作是否安全、是否可重试、操作员意义上的审批是否存在——那些已经有主。它只回答一个窄问题：**提出请求的 Agent，是不是那种可以做这件事的 Agent**。

## 三层记忆

`core/agentcontract` 里定义了 `Ledger`（发生过什么，只追加）、`Beads`（相信什么，可修订、带版本）、`Execution`（机器人实际做了什么，含结果未知的步骤）。

当前**只有 `Execution` 是真实实现**（包住既有的 `middleware.ExecutionStore`，`Uncertain()` 就是"必须对账"的输入）。`Ledger` 与 `Beads` 是进程内实现，只为让接口有东西可测。有 schema、迁移和冲突规则的持久化 bead 存储是一个独立项目，现在建它等于为一个还不存在的消费者做设计。

## 配置

见[Agent 运行时配置](../operations/agent-runtime-config.md)。默认 `enabled: [task, ops, recovery]`；`TANGYING_AGENTS=task` 只启用执行 Agent，用于判断"观测本身有没有改变行为"。

## 与 Python 侧恢复引擎的边界

`robot/gateway/.../fault_remedy.py` 是**执行引擎**：它决定该不该跑一个有界恢复动作、按什么顺序、结果如何，成功判据是复查台账后故障消失。

OpsAgent 是**只读观测者**，RecoveryAgent 是**只提议者**：它们读同一个故障台账（`robot.faults.v1`），把它翻译成结构化发现与计划，然后停下。两者都不调用恢复引擎，不执行恢复动作。OpsAgent 对任何恢复建议恒为 `automationLevel="advisory"`、`requiresApproval=true`；RecoveryAgent 的每一步是否要批准由动作的风险等级决定（只读动作不需要批准，任何非只读动作都需要），由反射白名单测试钉住"它没有执行端口"这一点。

连接点是事件：OpsAgent 输出 `ops.anomaly_detected`，RecoveryAgent 据此输出 `ops.recovery_plan`，要不要做、由谁做，是既有权限与审批路径的事。

## 相关

- 监督者怎么工作：[Review Agent 运行原理](review-agent.md)
- 事件规范：[Agent 事件](agent-events.md)
- 新增 Agent：[如何新增一个 Agent](../development/adding-an-agent.md)
- 配置与回滚：[Agent 运行时配置](../operations/agent-runtime-config.md)
- 恢复能力：[恢复 Agent](recovery-agent.md)
- 事故记录：[哑掉的观察者与漂移的身份](../development/2026-09-17-mute-observer-and-plan-identity.md)
- 迁移说明：[Agent V1 契约](agent-v1.md)
