# Agent 运行时配置

## 配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TANGYING_AGENTS` | `task,ops,recovery` | 启用的 Agent 名字，逗号分隔 |

没有其它开关。队列容量、健康检查周期这类参数有代码里的默认值（`agentruntime.DefaultQueueCapacity` = 256，`agentruntime.DefaultHealthInterval` = 5s），需要时通过 `agentruntime.Config` 设置，但当前部署不暴露它们——**没有第二个旋钮就少一个能被设错的地方**。

## 当前可用的名字

| 名字 | 实现 | 说明 |
| --- | --- | --- |
| `task` | `edge/agent.Runner` | 执行 Agent，执行任务并驱动物理动作 |
| `ops` | `agentruntime.OpsAgent` | 观察 Agent，只读：异常检测、健康检查、根因假设 |
| `recovery` | `agentruntime.RecoveryAgent` | 恢复 Agent，**只提议不执行**：定位问题、从动作目录挑选待批准步骤、记录调查轨迹 |

`eval`、`experience`、`escalation` 还没有实现。**在配置里写一个没注册的名字会让启动失败并明确报错**，不会静默降级：以"比要求的少启用了东西"却什么都不说的方式启动，会让运维以为观察者在跑而它其实没跑。

```
agent runtime not started: unknown agent: enabled agents are not registered: eval
```

Agent 运行时的启动失败**不会阻止 local-agent 启动**。原因是它观察执行而不是参与执行：因为观察者起不来就拒绝跑任务，等于把可观测性变成了安全路径的一部分，正是这套设计要避免的反转。失败会记一条日志，任务照常执行。

## 两种受支持配置

### 默认：执行 + 观察 + 恢复提议

```bash
make home-furnished          # 或 ./bin/local-agent
```

日志会写明实际启用了什么：

```
agent runtime started: enabled=[task ops recovery] publishers=[task ops recovery] disabled=[]
```

`publishers` 列出拿到事件总线的 Agent，由 `Orchestrator.Publishers()` 报告。它不是装饰：少一个名字就意味着少一条链路（例如观察者上报不了，恢复 Agent 就永远等不到触发），而"没什么可说的"和"根本说不了话"从外面看是一样的。发布通道由运行时在启动时注入，接线代码不需要、也不应该逐个 Agent 手工赋值。[相关事故记录](../development/2026-09-17-mute-observer-and-plan-identity.md)

### 关掉恢复提议

```bash
TANGYING_AGENTS=task,ops ./bin/local-agent
```

观察照常、只是不再产出计划。适用于"我只想让系统告诉我哪里坏了，先别给我建议"的阶段。

### 只启用执行

```bash
TANGYING_AGENTS=task ./bin/local-agent
```

这是**受支持的部署形态，不是测试夹具**：运维要判断"观测本身有没有改变行为"时，关掉观察者、其它都不动即可。执行路径逐字节相同，有测试断言这一点。

日志里被关掉的 Agent 会被列出来，所以"打包里有但没跑"是可见的：

```
agent runtime started: enabled=[task] disabled=[ops]
```

## 空值处理

`TANGYING_AGENTS` 显式设成空或只有分隔符时，**保留默认**并记一条日志：

```
TANGYING_AGENTS is set but names no agent; using the default [task ops]
```

空列表更可能是写错了，而不是"一个都别跑"，而"一个都别跑"和"一切正常"在外部看起来完全一样。真要把观察者关掉，就明确写 `task`。

## 回滚

运行时的接入是追加式的，回滚不需要改代码：

| 想回到 | 做法 |
| --- | --- |
| 只跑执行 Agent（等价于接入前的行为） | `TANGYING_AGENTS=task` |
| 完全不启动运行时 | 移除 `cmd/local-agent/main.go` 里对 `startAgentRuntime` 的调用（一处），或保持 `TANGYING_AGENTS=task` 并忽略观察者不存在 |

任务执行、闭环契约、失败分类、租约/fencing、回放、恢复引擎都不经过 Agent 运行时，所以关掉它不改变任何任务结果。

## 排障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 日志出现 `unknown agent` | 配置里写了没注册的名字 | 改成 `task`/`ops`/`recovery`，或先实现并注册该 Agent |
| 日志出现 `agent runtime not started` | 注册或订阅失败 | 读具体错误；任务仍在正常执行 |
| 回放里没有"系统观察与诊断" | `ops` 未启用，或该任务还没有异常 | 确认日志里的 `enabled` 含 `ops` |
| 观察者一直 `DEGRADED` / `TELEMETRY_UNAVAILABLE` | 读不到遥测 | 检查机器人连接与遥测上报；这是观察者承认自己看不见，不是机器人故障 |
| 健康事件刷屏 | 不应发生：只在变化时发布 | 如果发生是 bug，检查 `agent.health_changed` 的发布条件 |
| 恢复建议长时间不出现 | 有物理动作在飞，建议被挂起 | 看 `ops.recovery_deferred` 的 `deferredReason`；动作到达终态后会在下一个评估周期释放 |
| 告警列出了发现，但一条 `ops.recovery_plan` 都没有 | 观察者的报告没上总线（历史缺陷），或 `recovery` 未启用 | 先看启动日志的 `publishers` 是否含 `ops` 与 `recovery`；再确认 `enabled` 含 `recovery` |
| 同一条告警显示的诊断像是别的组件的 | 计划与告警的身份不匹配 | 两者都应使用 `code@component` 身份；见[恢复 Agent §7](../architecture/recovery-agent.md) |
| 恢复计划恒为"无法自动恢复" | 存在结果未知的物理步骤，或执行记录读不出来 | 看轨迹里的 `rule.reconcile-first` / `rule.reconcile-unknown`，后者会写明读不到什么 |

## 相关

- [多 Agent 运行时](../architecture/multi-agent-runtime.md)
- [Agent 事件规范](../architecture/agent-events.md)
- [如何新增一个 Agent](../development/adding-an-agent.md)
- [恢复 Agent：调查、提议、转人工](../architecture/recovery-agent.md)
- [事故记录：哑掉的观察者与漂移的身份](../development/2026-09-17-mute-observer-and-plan-identity.md)
