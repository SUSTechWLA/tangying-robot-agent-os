# 如何新增一个 Agent

新增一个 Agent **只需要实现接口并注册**，不需要改 `agentruntime` 里的任何核心代码。这份文档用一个 EvalAgent 骨架把那条路径走一遍。

## 三步

1. 实现 `agentcontract.Agent`（通常在 `agentruntime/` 或你的 Agent 自己的包里）。
2. 在 composition root（`cmd/local-agent/agentruntime.go`）注册它。
3. 在配置里启用它的名字。

第 2、3 步都是新增行。`agentruntime` 不导入任何具体 Agent，所以它不会因为你加了 Agent 而需要改。

## 接口

```go
type Agent interface {
    Name() string
    Version() string
    Capabilities() []Capability
    Subscriptions() []string
    Permissions() Permission
    Health(ctx context.Context) Health
    OnEvent(ctx context.Context, event Event) error
    Execute(ctx context.Context, request ExecuteRequest) (ExecutionOutcome, error)
    Shutdown(ctx context.Context) error
}
```

一半是静态的（身份、订阅、权限），运行时在启动任何东西之前读它；一半是动态的（健康、事件、执行、关闭）。静态部分必须能被并发调用。

### 各项的要求

| 方法 | 要求 |
| --- | --- |
| `Name()` | 稳定身份，同时是配置键和事件归属键。**不能带首尾空格**，运行时按精确字符串匹配。注册后不可变：它决定持久事件流归谁。 |
| `Version()` | 你自己的行为版本，用来把行为变化和它产生的事件对上。 |
| `Capabilities()` | 声明你是干什么的。**空列表会被注册拒绝**——什么都不声明的 Agent 更可能是接线错误。 |
| `Subscriptions()` | 想要的 topic，支持命名空间通配。**超出词表的模式在启动时报错**，这样拼写错误是一个 bug 报告而不是静默。只需要发布的 Agent 返回 `nil`。 |
| `Permissions()` | 声明你能做什么。运行时按它拒绝不匹配的请求。 |
| `Health()` | 必须便宜、不能 panic（它在定时器和变化时被调用）。**不能验证的事情返回 `UNKNOWN`，不要返回 `HEALTHY`**——报告未经核实的健康比承认不知道更糟。 |
| `OnEvent()` | 必须**快速返回**。运行时有界投递，慢的 Agent 丢自己的事件而不是阻塞发布者。不要在 `OnEvent` 里做慢活，把活放到 `Observe` 里。 |
| `Execute()` | 执行一次请求。只观察的 Agent 返回 `agentcontract.ErrNotExecutable`，不要假装干活。 |
| `Shutdown()` | 释放资源，幂等，**不能无限阻塞**。永远不要取消任务还依赖其完成的工作——那是宿主的决定。 |

### 想定时干活？实现 `Observer`

```go
type Observer interface {
    Observe(ctx context.Context) []Finding
}
```

`agentruntime.Observer` 描述的是**运行时如何调度你**，不是 Agent 是什么，所以它不在 `agentcontract` 里。实现了它的 Agent 会在每个评估周期被调用一次，用来跑规则并发布结果。TaskAgent 不实现它：它在宿主让它执行时才执行。

## 骨架：EvalAgent

EvalAgent 的独特权力是**依据任务级成功标准否决 TaskAgent 的完成声明**。这个权力目前只有接口占位（`agentcontract.CapabilityCompletionVerification`），运行时第一版不接完成判定——把完成判定接进一个观察者路径，正是 `agentruntime` 明确不做的事。骨架如下：

```go
package agentruntime

// EvalAgent independently verifies completion claims against a task's success
// criteria and may veto one.
type EvalAgent struct {
    // Criteria resolves the success criteria for a task.
    Criteria func(ctx context.Context, taskID string) ([]SuccessCriterion, error)
    // Evidence reads the archived evidence a claim rests on.
    Evidence func(ctx context.Context, taskID string) ([]agentcontract.StepRecord, error)
    Publish  func(ctx context.Context, event agentcontract.Event)

    mu      sync.Mutex
    vetoed  map[string]string // taskID -> reason
}

func (a *EvalAgent) Name() string    { return "eval" }
func (a *EvalAgent) Version() string { return "1" }

func (a *EvalAgent) Capabilities() []agentcontract.Capability {
    return []agentcontract.Capability{
        agentcontract.CapabilityCompletionVerification,
        agentcontract.CapabilityObservation,
    }
}

// It watches completion claims and the evidence behind them.
func (a *EvalAgent) Subscriptions() []string {
    return []string{
        agentcontract.TopicTaskAll,
        agentcontract.TopicEvidenceAll,
        agentcontract.TopicStateAll,
    }
}

// Read-only: verifying completion is an observation. A veto is a statement, and
// the task state authority remains the task service.
func (a *EvalAgent) Permissions() agentcontract.Permission {
    return agentcontract.ReadOnlyPermission()
}

func (a *EvalAgent) Health(context.Context) agentcontract.Health {
    // UNKNOWN when criteria or evidence cannot be read, never HEALTHY.
    return agentcontract.Health{Status: agentcontract.HealthUnknown, ReasonCode: "NOT_IMPLEMENTED"}
}

func (a *EvalAgent) OnEvent(ctx context.Context, event agentcontract.Event) error {
    // Record the claim; evaluate on the tick, not here.
    return nil
}

func (a *EvalAgent) Execute(context.Context, agentcontract.ExecuteRequest) (agentcontract.ExecutionOutcome, error) {
    return agentcontract.ExecutionOutcome{}, agentcontract.ErrNotExecutable
}

func (a *EvalAgent) Shutdown(context.Context) error { return nil }
```

注册：

```go
evaluator := agentruntime.NewEvalAgent(...)
registry.Register(evaluator)
```

配置：

```
TANGYING_AGENTS=task,ops,eval
```

就这些。没有核心代码改动，没有对 `agentruntime` 的改动。

### 关于否决权，落地时要先解决的问题

接口留好了，但接进执行路径之前需要先决定：一个否决是**阻止完成声明**，还是**把任务送进一个需要人工的状态**？仓库的既有原则倾向于后者——一次无法确认的物理写入会留在 `STARTED` 等对账，而不是被自动改判。否决权应当复用同一套状态语义，而不是新增一条"Agent 可以让任务失败"的路径。

## 经验与升级：另外两个扩展点

| Agent | 扩展点 | 落地时需要先解决的问题 |
| --- | --- | --- |
| `experience` | `agentcontract.Memory` 三层（`Ledger` / `Beads` / `Execution`）。`Execution` 已是真实实现；`Ledger`/`Beads` 是进程内实现，等着被替换。 | 持久化 bead 存储需要 schema、迁移与冲突规则；还要决定"一条信念被修订"在回放里如何呈现。 |
| `escalation` | 订阅 `ops.escalation_required`，拥有人工审批队列、超时策略与决策回传。 | 队列的持久化与去重；超时后是自动降级还是继续等；决策如何写回且不绕过既有审批路径。 |

两个都应该遵循同一条线：**新增能力走既有机制，不新建平行的权威**。

## 检查清单

- [ ] `Name()` 稳定、无首尾空格、与配置键一致
- [ ] `Capabilities()` 非空，且与 `Permissions()` 自洽（只读就不要声明能改世界）
- [ ] `Subscriptions()` 里的模式都在词表内（否则启动报错）
- [ ] `Health()` 对无法验证的情况返回 `UNKNOWN`/`DEGRADED`，并带 `ReasonCode`
- [ ] `OnEvent()` 里没有慢活
- [ ] `Execute()` 对不该执行的请求返回 `ErrNotExecutable`
- [ ] `Shutdown()` 幂等、有界、不取消任务依赖的工作
- [ ] 有测试覆盖：注册、订阅路由、权限（含**被拒绝**的路径）、健康、关闭幂等
- [ ] 如果它会产出结论，结论里有证据链或明确的 `missingEvidence`

## 相关

- [多 Agent 运行时](../architecture/multi-agent-runtime.md)
- [Agent 事件规范](../architecture/agent-events.md)
- [Agent 运行时配置](../operations/agent-runtime-config.md)
