# 2026-09-17 哑掉的观察者与漂移的身份：四个只有在真机上跑才会暴露的缺陷

这份记录的价值不在于修了什么，而在于**它们为什么没被测试拦住**。

四个缺陷全部满足同一个模式：

> 每个部件单独看都自洽，错的只是它们的组合；测试自己注入了生产环境并不存在的那一半接线。

恢复 Agent 的机制请配合 [recovery-agent.md](../architecture/recovery-agent.md) 阅读。

---

## 缺陷一：观察者的发现从未上过总线

### 现象

三个 Agent 都注册成功、都报健康，控制台能列出观察 Agent 发现的每一条问题——但**恢复 Agent 一条计划都没产出**。它订阅 `ops.anomaly_detected`，等一个永远不会到的事件。

### 根因

组合根（`cmd/local-agent/agentruntime.go`）里，事件总线的发布通道是**逐个 Agent 手工赋值**的：

```go
runner.Events = func(ctx context.Context, event agentcontract.Event) {
    runtime.Publish(ctx, event)
}
```

执行 Agent 装了，观察 Agent 的 `Publish` 字段**从来没被赋过值**。`OpsAgent.Observe` 里的顺序恰好掩盖了它：

```go
if a.RunnerAlerts != nil {
    a.RunnerAlerts.Record(findings)   // 先记录：控制台因此看得到
}
for _, finding := range findings {
    a.publishFinding(ctx, finding, taskID)  // 再发布：a.Publish == nil 时直接 return
}
```

`publishFinding` 的第一行就是 `if a.Publish == nil { return }`——静默返回，不报错、不记日志。

### 为什么测试没拦住

`agentruntime` 的每个测试都自己注入发布通道：

```go
observer.Publish = recorder.sink
agent.Publish = func(_ context.Context, event agentcontract.Event) { ... }
```

全仓库 `.Publish =` 的赋值点**只出现在测试里**。测试验证的是一套生产环境并不存在的接线，于是"观察者能上报"这件事被验证了 100+ 次，而线上一次也没发生。

### 修复

把注入收进运行时，让"忘记接线"在结构上不可能：

1. 新增可选能力 `agentcontract.Publisher`（`SetPublish`），不污染 `Agent` 接口——只观察不发布的 Agent 不该被迫实现一个用不到的端口；
2. `Orchestrator.Start` 遍历已启用 Agent，凡是实现该接口的一律注入 `runtime.Publish`；
3. `edge/agent.Runner` 也实现它（设置自己的 `Events`），于是组合根里那句手工赋值被删掉——**发布通道只有一个来源**；
4. `Orchestrator.Publishers()` 把结果打进启动日志，因为"没什么可说的"和"根本说不了话"从外面看是一样的。

### 留下的回归测试

- `agentruntime/publisher_test.go`：全部测试**不手工注入任何 sink**。关掉注入后其中三个立刻失败（已实测）。
- `cmd/local-agent/recovery_wiring_test.go`：直接启动真实组合根，断言一条发现能走完"观察 → 总线 → 触发 → 恢复 Agent → 计划入库"。

---

## 缺陷二：一份计划被展示给十一个不同的故障

### 现象

真实机器人上，11 条告警（`ANOMALY_ACTION_FAILED` 分布在 `navigation.navigate`、`observe_scene`、`resolve_targets`、`verify_placement` 等组件上）显示的是**同一份计划、同一条 7 步轨迹**，而且诊断栏写着 `navigation.navigate 失败` ——包括那些根本不是 navigation 的告警。

### 根因

"一个发现的身份是什么"有三套答案：

| 位置 | 用的身份 |
| --- | --- |
| 告警存储 `AlertStore.Record` | `code + "@" + component` |
| 恢复 Agent `Recover` 的触发器 | `finding.Code`（裸 code） |
| 控制台查计划 `RunnerAlertPlan` | `alert.Code`（裸 code） |

再加上两个放大器：计划的 2 分钟冷静期按裸 code 计，于是同一任务里第二个组件失败**根本不会再规划**；控制台查不到就退回到"按 code 匹配"，于是所有同 code 的告警都拿到那一份。

### 修复

把规则写在一处，所有调用方都调它：

```go
// core/agentcontract/anomaly.go
func AnomalyIdentity(code, component string) string
func AnomalyReportID(code, component string, count *int) string
func SameAnomaly(reportID, identity string) bool
```

- `Finding.Identity()` 是唯一派生入口；
- 上报去重键 = 身份 + `#count`（稳定状态不刷屏，恶化必须重报）；
- 计划按身份归档、按身份查询；
- `SameAnomaly` 只在 `#` 边界比较，`CODE@a` 不会匹配 `CODE@ab`。

### 顺带修掉的第三个错误：计划借给了别的任务

真实数据暴露出：任务 A 的告警显示着任务 B 的计划（`planId` 里写着 B 的任务号）。原因是 `attachRecovery` 把**所有任务**回放里的计划汇成一张表，再按发现身份匹配——而同一个组件在几乎所有任务里都会以同样的方式失败，身份必然重复。

修复：计划按**任务**分组，只在告警自己的任务内匹配。

### 留下的回归测试

| 测试 | 钉住的性质 |
| --- | --- |
| `core/agentcontract/anomaly_test.go` | 身份规则与 `#` 边界 |
| `agentruntime` `TestTwoComponentsFailingTheSameWayGetTheirOwnPlans` | 同 code 不同组件 → 两份计划 |
| `agentruntime` `TestTheSameFindingIsNotPlannedTwiceInsideTheCooldown` | 同一发现不重复规划 |
| `console/alerts_test.go` `TestEachComponentFailureIsShownItsOwnPlan` | 端点上每条告警拿到自己那份（按旧逻辑改回 `alert.Code` 立即失败，已实测） |
| `console/alerts_test.go` `TestAPlanAboutSomethingElseIsNotBorrowed` | 旧版按裸 code 写的计划故意不再匹配 |
| `tasks/alerts_test.go` `TestAPlanFromAnotherTaskIsNotAttached` | 跨任务不借计划（去掉任务限定立即失败，已实测） |

> 只追加的台账意味着旧计划事件永远留在回放里。选择是**故意不再匹配它们**：借来的计划比没有计划更糟，因为它读起来像一个答案。

---

## 缺陷三："查不出来"被当成"没问题"

### 现象

`RecoveryFacts.UncertainSteps` 为空时，规则认为"世界上没有结果未知的动作"，于是照常给出恢复计划——包括可能重试某一步的计划。

### 根因

"这个任务没有结果未知的步骤"和"这个部署查不出来"**产生同一个空列表**。事实读取器里：

```go
if reader, ok := store.(middleware.ExecutionReader); ok {   // 不支持就整体跳过
    ...
}
```

存储不实现 `ExecutionReader` 时，这个 `if` 直接被跳过，**轨迹里连一步都不留**。读失败时记了轨迹，但同样没有把"问不出来"这一点传下去。

这直接冲击闭环契约里最硬的一条：**结果未知禁止自动重试**。一次存储抖动就足以静默关掉这条规则。

### 修复

三种答案分开：

```go
type RecoveryFacts struct {
    UncertainSteps            []agentcontract.StepRecord // 有
    ReconciliationUnavailable bool                       // 问不出来
    ReconciliationError       string                     // 为什么问不出来
}
```

规则里新增一条**优先级仅次于已知结果未知**的分支：问不出来 → `ESCALATE`，转人工理由写明"恢复执行记录的读取能力后重新调查：<具体错误>"。同时事实读取器把不支持/读失败两种情况都写进轨迹并带 `Error`。

### 留下的回归测试

- `agentruntime` `TestRecoveryStopsWhenTheReconciliationQuestionCannotBeAnswered`：模型想直接重试那一步 → 仍必须转人工；
- `cmd/local-agent` `TestAnExecutionStoreThatCannotListStepsIsReportedAsAnOpenQuestion`：存储不支持列步骤时，事实里必须出现 `ReconciliationUnavailable`，轨迹里必须出现带错误的 `step_runs.read`；
- `cmd/local-agent` `TestWhatTheObserverFindsReachesTheRecoveryAgentInProductionWiring`：真实组合根下，存储不可达 → 结论必须是 `ESCALATE` 且理由里含具体错误。

---

## 缺陷四：同一个问题在告警列表里出现三次

### 现象

发布通道接通之后，控制台任务级告警从 0 涨到 68 条，其中 6 组是同一个"（任务, 告警）"对；`activeCount` 数的是**上报次数**，不是**问题个数**。

### 根因

告警投影 `ProjectAgentAlerts` 对任务的每一条事件都 `append` 一条告警。发布通道没通的时候，agent 事件根本没进台账，这条路径是空的，所以从没暴露。接通后，一个"持续存在"的发现每次评估都会再上报一次（这正是"持续"的含义），列表就按上报次数线性增长。

### 修复

按 `(taskID, 身份)` 去重，保留最新一条：

```go
key := task.ID + "\x00" + alert.ID
```

**key 的两半都不能省**：

- 只按 `code` 去重 → 同一任务里第二个组件失败被隐藏（已实测会失败）；
- 不带 task 去重 → 另一个任务里同样的失败被隐藏（已实测会失败）。

### 留下的回归测试

`tasks/alerts_test.go`：`TestAStandingConditionIsOneAlertPerTask`、`TestTheSameFailureInTwoTasksIsTwoAlerts`、`TestTwoComponentsFailingInOneTaskAreTwoAlerts`。

---

## 结论：这次修复真正改掉的东西

不是四个 bug，而是一个**结构**：把"必须记得做的事"从调用点搬进运行时。

| 以前 | 现在 |
| --- | --- |
| 每个 Agent 的发布通道靠接线代码记得装 | 运行时在 `Start` 里统一注入，并打印 `publishers=[...]` |
| 发现身份三处各有一套 | 一处定义，四处调用 |
| 计划只按 code 归档 | 按身份归档、按任务限定匹配 |
| 空列表既是"没有"也是"查不出来" | 三种答案三个字段，问不出来即转人工 |

一个可以复用的判据：**当一段逻辑在测试里和在组合根里长得不一样时，被测试覆盖一百遍也不代表它被验证过。** 缺陷一和缺陷四都属于这一类。
