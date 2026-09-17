# 生命周期对象：一次家庭任务里到底有几种"东西"在各自走状态

这篇文档回答三个问题：

1. 一次任务里到底有哪几种对象在各自走状态？
2. 为什么不能合成一个？
3. 以后什么时候该加新的，什么时候是在给自己找麻烦？

阅读顺序：先看[第一幕](#第一幕一次开冰箱拿杯子的任务)（一次真实任务，不涉及代码），再看[一张图](#一张图十个对象各自的生死)（全局），然后按需读[逐个对象](#逐个对象)和[四个分界线](#四、为什么不能合成一个四个分界线)。如果你只想知道"该不该加新对象"，直接跳到[判据](#六、该不该加新对象三条判据)。

---

## 为什么这是个真问题

普通软件里，一个对象"活着"通常只有一种含义：进程里的内存还在。进程没了，对象就没了，数据库里那条记录还在，两边语义清晰。

机器人系统不是这样。同一个瞬间，"红色杯子"这一个东西，在系统里有**四份状态同时存在，而且它们本来就允许不一致**：

| 谁知道 | 它认为的情况 | 它凭什么这么认为 |
|---|---|---|
| Task 状态机 | "正在执行第 6 步：合上夹爪" | 我们下发了命令，还没收到结果 |
| 执行记录（Step） | "`pick` 这一步 STARTED" | 我们记了"开始"，还没记"结束" |
| 闭环跟踪（Track） | "AWAITING_EVIDENCE" | 工具回了成功，但还没有动作后的新观测 |
| 夹爪本身 | 可能夹着，可能空着，可能夹到一半卡住 | 没人问它 |

**这四份不一致不是 bug，是系统的正常工作状态。** 而"生命周期管理"的全部工作，就是保证这四份在每一刻都能被正确地对齐、或者被人看见"它们不一致"。

如果把它们合成一个对象（"杯子状态 = 正在被夹取"），你就再也无法表达"命令发出去了但结果不知道"——而这正是这套系统存在的最核心理由。

---

## 第一幕：一次开冰箱拿杯子的任务

用仓库里真实的任务串：「从客厅出发，去厨房拿杯子，放进收纳盒，然后回到客厅」。

### 时刻 1：你说了一句话

```
你说：把红色杯子放进右侧收纳盒
```

此刻系统里只有一个对象：**Task**，刚被创建。

```json
{ "sequence": 1, "type": "TASK_CREATED" }
```

它的状态是 `READY`。因为计划里有物理动作，它还被要求"必须有人批准"——所以真正开始前会有 `TASK_APPROVED`，然后才走 `STATE_CHANGED → OBSERVING → PLANNING → EXECUTING`。

> 下面是**真实**的一条事故记录（`artifacts/incidents/task-1c85d72309f225f7714cc660.bundle.json`），时间线开头就是这样：
> ```
> 1  TASK_CREATED
> 2  TASK_APPROVED
> 3  STATE_CHANGED  local execution started
> 4  STATE_CHANGED  grounding and local planning started
> 5  STATE_CHANGED  local physical execution started
> 6  TOOL_ACTIVITY  stepId=observe  toolName=observe_scene  status=SENDING
> ```

这里出现了第一个关键区别：**Task 的状态是"你要它做什么"的记录，不是"世界现在什么样"的记录。** 这两件事必须分开，后面会反复回到这一点。

### 时刻 2：机器人要伸手了

执行到 `pick` 这一步。此刻系统里同时有：

**Step 对象**（执行记录，落在 `middleware.ExecutionStore`）：

```
StepRecord{TaskID: "task-abc", StepID: "pick", Capability: "manipulation.pick",
           SafetyLevel: "physical_motion", IdempotencyKey: "task-abc/revision/1/step/pick"}
→ MarkStepStarted
```

状态：`PENDING → STARTED`

**Tool 下发对象**（闭环跟踪，`core/closedloop.Track`）：

```
NewTrack("pick", "manipulation.pick", dispatchedAt, DefaultPolicy())
→ Dispatch(now)
```

状态：`PENDING → EXECUTING`

**Resource 租约**（如果这一步需要独占"杯子"这个对象）：

```
Acquire(ctx, "object/red-cup", "robot-1", time.Minute)
→ Grant{ResourceID: "object/red-cup", Owner: "robot-1", Token: 7, ExpiresAt: ...}
```

这三件事发生在**同一瞬间**，但它们**各自的过期时间差了三个数量级**：

| 对象 | 时间尺度 | 为什么 |
|---|---|---|
| 租约 | **秒级**（例：1 分钟 TTL） | 机器人进程死了，不能让杯子被永久锁住 |
| Tool 下发 | **秒级** | 命令有 deadline 和 lease |
| Step 状态 | **持久**（重启后还在） | 崩溃恢复要靠它 |
| Task 状态 | **持久** | 这是你的业务意图 |

### 时刻 3：命令发出去了，然后网断了

这是理解整套设计的**唯一最重要的场景**。

夹爪命令通过 gRPC 发给了机器人。机器人开始合拢。然后：

```
rpc error: code = Unavailable desc = connection closed
```

现在请问：**杯子被夹起来了吗？**

- 网络在命令到达前断 → 没夹
- 网络在命令到达后、回包前断 → 夹了
- 命令到达、机器人夹到一半断电 → 夹了一半

**这三种情况，从外面看一模一样。** 系统此刻唯一正确的回答是：**"我不知道。"**

于是三个对象各自进入它们的"不知道"状态，而且这三个词不是同义词：

| 对象 | 进入的状态 | 含义 |
|---|---|---|
| Step | `STARTED`（**保持不变**） | "我记了开始，没记结束"——故意不写 FAILED |
| Tool 下发 | `ESCALATED` + `class = UNKNOWN_OUTCOME` | "物理结果不可判定，禁止自动重试" |
| Task | `RECOVERABLE_FAILURE` | "停了，但可以恢复" |

代码里的原话（`edge/agent/runner.go`）：

```go
// ErrUnverifiedWorldMutation means a tool changed, or may have changed, the
// physical world and no fresh post-command observation confirms the result.
// The step is deliberately left STARTED so recovery reconciles the world
// instead of repeating a physical action whose outcome is unknown.
```

注意 `STARTED` 是**故意不推进**的。如果这里写 `FAILED`，系统就会"合理地"重试一次——而重试意味着第二次合拢夹爪，可能是在已经有杯子的情况下。**这就是"结果未知禁止自动重试"的全部重量。**

> **诚实说明**：仓库里现存的事故记录（`artifacts/incidents/`）**没有一条**真的走到 `uncertainStepIds` 非空——那 13 条都是 `NO_RECOVERY_REQUIRED` 或 `PAUSING`。上面这个断网场景是**根据代码语义构造的**，不是从真实事故里摘的。它在测试里有覆盖（`edge/agent/recovery_test.go`、`internal/localapp` 的恢复用例）。

### 时刻 4：人来了，怎么收拾

系统给人三个选项，对应三种完全不同的语义：

**A. 先去看一眼**（对账）

```
POST /v1/tasks/{id}/resume     ← 只有在“没有未对账步骤”时才允许
GET  /v1/tasks/{id}/recovery   ← 看 requiresReconciliation / uncertainStepIds
```

对账的意思是：**去拿一个新的观测**，看看杯子到底在不在夹爪里。注意 `ObservationAttempt` 这个字段——它让读取身份变化，防止运行时的幂等缓存把**中断前**的旧观测当成新证据回放：

```go
type RunControl struct {
    BeforeStep func(context.Context) error
    // A new read identity prevents runtime idempotency caches from returning
    // pre-interruption verification evidence during an explicit resume.
    ObservationAttempt string
}
```

**B. 改需求**（Revision）

```
POST /v1/tasks/{id}/revisions            → PROPOSED
POST /v1/tasks/{id}/revisions/2/confirm  → WAITING_SAFE_POINT → ACTIVE
```

`WAITING_SAFE_POINT` 的意思是：**改版不能打断正在执行的物理动作**，等它走到一个安全点再生效。这就是为什么 Revision 是独立对象——它需要在"还没生效"的状态里等一个物理条件。

**C. 算了**（Cancel）→ `CANCELLED`

### 时刻 5：任务成功了，为什么还要证据

终于，`place` 完成。工具返回 `Success: true`。

**但这一刻任务还不能标记完成。** 因为"工具说成功"只是"去看世界"的触发：

```go
// core/closedloop/closedloop.go
// A tool result is a trigger to look at the world, never proof that the world
// changed as intended.
```

所以系统去拿一个**动作之后**的新观测（Evidence），并且比较时间：

```go
// core/closedloop/closedloop.go
// The dispatch instant is the freshness floor for this attempt: only an
// observation taken after it can confirm that this command changed the
// world, no matter how fresh an earlier capture still looks.
```

如果观测的时间戳**早于**命令下发时刻 → `CLOSURE_EVIDENCE_STALE` → 回到 `AWAITING_EVIDENCE`。

于是又多了两个对象：**Evidence**（有新鲜度生命周期）和 **Observation**（有序号 `SourceSequence` + 采集时间 `ObservedAt`）。

### 这一幕的收尾

任务结束时，系统里留下了这些状态的**快照**（就是 incident bundle）：

```json
{
  "task":     { "state": "SUCCEEDED" },
  "stepRuns": [ { "stepId": "observe", "status": "COMPLETED" },
                { "stepId": "pick",    "status": "COMPLETED" },
                { "stepId": "place",   "status": "COMPLETED" } ],
  "evidence": [ ... ],
  "timeline": [ ... 每一条事件 ... ],
  "timing":   [ ... 每一步四个阶段的耗时 ... ]
}
```

**这四样东西各自独立，正是因为它们回答四个不同的问题。**

---

## 一张图：十个对象各自的生死

这是全局视图。横轴是时间尺度，纵轴是"崩溃后系统知不知道世界"。

```
                     崩溃后世界状态：
                   ┌─────────────────────────────┬──────────────────────────┐
                   │  知道 / 无关                 │  不知道 → 必须对账        │
┌──────────────────┼─────────────────────────────┼──────────────────────────┤
│ 持久             │  Task（17 态）               │  Step STARTED            │
│ （重启后还在）    │  Revision（4 态）            │  ↑ 唯一"必须人工/对账"的  │
│                  │  Incident（不可变归档）       │                          │
├──────────────────┼─────────────────────────────┼──────────────────────────┤
│ 会话级           │  Agent（5 态）               │  Tool 下发（7 态）        │
│ （丢了重建）      │  Capability（可用/受阻）      │  ↑ UNKNOWN 是终态之一     │
│                  │  Event（身份→去重→落账）      │                          │
├──────────────────┼─────────────────────────────┼──────────────────────────┤
│ 秒级 TTL         │  Resource 租约（+fencing）    │  （不允许存在：         │
│ （过期即释放）    │  Observation（新鲜/过期）     │    租约没有"不知道"状态） │
└──────────────────┴─────────────────────────────┴──────────────────────────┘
```

读法：**右上角只有两个格子，那是最难的部分。** 只有 `Step STARTED` 和 `Tool UNKNOWN_OUTCOME` 代表"可能已经动了但没人知道"。整个仓库最重的规则（禁止自动重试、必须对账后才能恢复）全都压在这两个格子上。

左上角是"业务"，左下角是"资源与观测"，右下角**故意留空**——一个租约不存在"不知道"状态：要么有效，要么过期，二选一。这是刻意的简化，因为租约只保护访问权，不承诺世界被改变。

---

## 逐个对象

每个对象给三样东西：**代码在哪、状态长什么样、以及它不可替代的那个职责。**

### 1. Task —— 你要它做什么

**位置**：`core/taskgraph/state.go`（17 个状态）、`tasks/service.go`

```
READY → OBSERVING → PLANNING → EXECUTING → VERIFYING → SUCCEEDED
  │         │           │          │  │                    
  │         │           │          │  └→ PAUSED / SAFETY_STOPPED / RECOVERABLE_FAILURE
  │         │           │          └→ WAITING_FOR_OBSERVATION → RECOVERING → BLOCKED
  │         │           └→ WAITING_APPROVAL
  │         └→ WAITING_USER
  └→ CANCELLED（几乎可以从任何状态进入）
```

**唯一职责**：记录**意图**的生命周期。它是权威对象——同一时刻只能有一个状态，且转换必须合法（`CanTransition` 会拒绝非法转换）。

**为什么不能省**：它是你唯一能问"我那个任务怎么样了"的地方。它**故意不知道**物理世界——这就是为什么它和 Step 必须分开。

**真实例子**：`TASK_CREATED` → `TASK_APPROVED` → 三个 `STATE_CHANGED` → `TOOL_ACTIVITY`（见上面那 6 条真实时间线）。

### 2. Step —— 机器人实际做了什么（含"可能做了什么"）

**位置**：`middleware/contracts.go`

```go
StepPending   = "PENDING"
StepStarted   = "STARTED"     // ← 唯一代表“不知道”的状态
StepCompleted = "COMPLETED"
StepFailed    = "FAILED"      // 命令在产生物理效果前就被拒绝，可安全重试
```

注意注释里那句关键区分：

```go
// StepFailed means the runtime rejected a command before any physical
// effect was authorized. It remains retryable and is distinct from STARTED,
// whose physical outcome is unknown after an interrupted invocation.
```

**唯一职责**：让"重启后还能知道哪些步骤悬着"。它必须**持久**，因为崩溃恢复时内存里什么都没有了。

**为什么不能省**：这是 `RequiresReconciliation` 的数据来源。没有它，重启后系统会以为一切如常。

### 3. Tool 单次下发 —— 一次不可撤销的物理尝试

**位置**：`core/closedloop/closedloop.go`

```
PENDING → EXECUTING → AWAITING_EVIDENCE → VERIFIED      （成功且证据新鲜）
                    ↘ RETRYING                          （可重试的失败，有预算）
                    ↘ ESCALATED                         （不可自动处理）
```

**唯一职责**：**管理不可逆性**。它回答："这一次尝试能不能再来一次？"答案由 `Classify(code)` 给出，七类：

| 类 | 能否自动重试 | 例子 |
|---|---|---|
| `TRANSIENT` | ✅ | `NAV_BRIDGE_UNAVAILABLE` |
| `PERCEPTION` | ✅ | `OBJECT_NOT_FOUND` |
| `PLANNING` | ❌ | `TARGET_UNREACHABLE`（重放同一条命令没意义） |
| `PERMISSION` | ❌ | `APPROVAL_REQUIRED` |
| `RESOURCE` | ❌ | `FENCING_TOKEN_STALE` |
| `VALIDATION` | ❌ | `TOOL_PARAMETERS_INVALID` |
| **`UNKNOWN_OUTCOME`** | **🚫 明令禁止** | 任何无法识别的码也归这里 |
| `FATAL` | ❌ | `VERIFICATION_FAILED` |

**为什么不能省**：这是"结果未知禁止自动重试"的**唯一**落点。

```go
ErrUnknownRetry = errors.New("unknown physical outcome must not be retried automatically")
```

注意最后一行：**未识别的错误码归到 `UNKNOWN_OUTCOME`**，不是归到"临时故障"。猜错方向会造成重复物理动作，所以代码选择保守：

```go
// An empty or unrecognised code is UnknownOutcome: the system knows something
// went wrong but not what reached the hardware, so no automatic retry is allowed.
```

### 4. Resource 租约 + Fencing Token —— 谁有权碰这个东西

**位置**：`fleet/lease/manager.go`、`core/worldmodel/types.go`

```go
type Grant struct {
    ResourceID string
    Owner      string
    Token      uint64      // ← fencing token，单调递增
    ExpiresAt  time.Time
}
```

生命周期：`Acquire → (Renew)* → Transfer/Release → 过期`

**独有的机制是 token 只增不减**。真实的测试断言：

```go
func TestTransferInvalidatesPreviousFencingToken(t *testing.T) {
    first, _  := manager.Acquire(ctx, "object/red-block", "robot-1", time.Minute)   // Token: 1
    second, _ := manager.Transfer(ctx, "object/red-block", "robot-1", "robot-2", first.Token, time.Minute)  // Token: 2
    // robot-1 拿着旧 token 再说话：
    manager.Validate(ctx, "object/red-block", "robot-1", first.Token)
    // → ErrStaleFencingToken
}
```

**为什么需要 token 而不只是 TTL**：TTL 只能防止**永久**占用，防不住**时序错乱**——robot-1 的网络卡了 30 秒，它的命令在租约过期后才到达机器人，此时 robot-2 已经在操作同一个杯子了。token 让机器人在协议层拒绝这条迟到的命令。

```go
// ExpiredGrantCanBeAcquiredButTokenKeepsIncreasing 的真实断言：
// second.Token > first.Token  ← 即使第一个已过期，编号也从 1 继续涨
```

**一句话总结这个对象的独特职责**：它是唯一一个**为了对抗"时序"而不是对抗"失败"**的对象。

### 5. Agent —— 谁在观察（这次新增的）

**位置**：`core/agentcontract/contract.go`、`agentruntime/`

```
UNKNOWN → HEALTHY ⇄ DEGRADED → UNHEALTHY
                                    ↓
                                 STOPPED
```

**唯一职责**：**可丢弃的观察者身份**。它的关键性质是：**它死了不影响任何东西**。

我要强调的是这次改造里最刻意的一个决定：`OpsAgent` **不持有任何执行端口**——没有 invoker、没有 task service、没有 bus 句柄。所以"观察者碰不到机器人"不是一条规则，是这个类型的性质。有测试按**字段白名单**钉住它：

```go
// 加任何新字段都会失败，强制重新回答“观察者需不需要这个”
for index := 0; index < fields.NumField(); index++ {
    if _, ok := allowed[field.Name]; !ok {
        t.Fatalf("OpsAgent has a new field %q...", field.Name)
    }
}
```

**为什么 Agent 不必是权威对象**：它产出的是**关于执行的陈述**，不是执行本身。所以它的事件可以被去重、可以在队列满时丢弃、可以在关掉之后任务结果逐字节不变（有测试断言）。

配置也体现了这一点：`TANGYING_AGENTS=task` 关掉观察者，任务执行完全照旧。

### 6. Capability —— 机器人现在会什么

**位置**：`edge/runtime/runtime.go`、机器人侧 `service_registry.py` + `module_faults.py`

```go
type Capability struct {
    Name      string
    Available bool
    Blockers  []string    // ← 为什么不可用
    MutatesWorld bool
    ...
}
```

生命周期由**故障台账**驱动：故障出现 → 能力被摘掉；故障清除 → 能力自动回来。最严重的 `safety` 级故障（急停）会摘掉**所有声明了依赖的能力**，唯一幸免的是声明"零依赖"的 `emergency_stop`——已经停住的机器人仍然必须能被停住。

**为什么是独立对象**：它是**环境事实**，不是任何人的状态。"机器人现在能不能导航"这个问题，不该去问 Task、也不该去问 Agent。

### 7. Revision —— 你要改主意

**位置**：`tasks/revision.go`

```
PROPOSED → WAITING_APPROVAL → WAITING_SAFE_POINT → ACTIVE
                             ↘ REJECTED
```

**唯一职责**：**让"改主意"安全地跨越一次物理动作**。

`WAITING_SAFE_POINT` 是关键：新版任务不能打断正在执行的物理步骤，要等它走到一个安全点。这就是为什么 Revision 必须是独立对象——它的生命周期里有一个**物理条件**作为闸门，这个闸门不属于任何别的对象。

### 8. Observation / Evidence —— 你凭什么这么说

**位置**：`core/observation/envelope.go`、`core/closedloop/gate.go`

```go
type Envelope struct {
    SourceSequence uint64     // 来源内的序号，用于判断"比上次新"
    ObservedAt     time.Time  // 采集时间，不是接收时间
    ReceivedAt     time.Time
    ...
}
```

**生命周期是"新鲜度"，而且有三个值**：`FRESH` / `STALE` / `UNKNOWN`。

`UNKNOWN` 的用法很讲究——急停期间取的观测**不能被用来判定完成**：

```go
if snapshot.EmergencyStopped {
    // An emergency stop during the action means the tool physically
    // confirmed nothing; treat the observation as unusable for closure.
    evidence.Freshness = "UNKNOWN"
}
```

**为什么独立**：证据的权威性**随时间衰减**，这个衰减规律既不属于 Task 也不属于 Robot。

### 9. Event —— 发生过什么（含运行时事件）

**位置**：`tasks/service.go`（账本）、`core/agentcontract/event.go`（词表）

生命周期：`产生 → 分配身份 → 按身份去重 → 落账本 → 可回放`

**唯一职责**：**唯一有序的、不可变的"发生过什么"**。

这里有个这次改造里我特别想讲的取舍：**Agent 事件没有新建存储，而是写进同一份任务账本**，以 topic 作为事件类型。

理由是：两份记录同一件事，它们迟早会不一致，而回放只显示其中一份。所以：

```
账本 → 总线：STATE_CHANGED 投影成 state.transition（观察者看得见执行）
Agent → 账本：ops.anomaly_detected 追加进同一账本（普通人回放时看得见诊断）
```

同一件事两条路都到 → **按事件身份去重**，否则每个动作在回放里出现两次。这个去重是我在写测试时才发现的必要性，也顺手修掉了。

### 10. Incident —— 事后归档

**位置**：`incidents/bundle.go`

这是唯一**没有状态机**的对象——它一旦写出就不可变（`schemaVersion: "incident.v1"`），因为它是证据，不是状态。

**为什么独立**：它的生命周期是"一次写入，永久只读"。给它加状态转换反而会毁掉它的用途。

---

## 四、为什么不能合成一个：四个分界线

我试着把它们归并过。只有四个**真正独立**的关注点，每一个都不能用另一个表达。

### 分界线 1：权威 vs 派生

| | 权威对象 | 派生对象 |
|---|---|---|
| 谁 | Task、Step、租约、Revision | Agent、Event、Capability、Observation |
| 副本数 | **恰好 1 个 owner** | 任意多，丢了重建 |
| 冲突时 | 必须串行化 + fencing | 可以按时间戳/序号择优 |

**为什么不能混**：把 Agent 做成权威，就等于给观察者发言权——正是这套设计要避免的。反过来把 Task 做成派生，多机就会各自"以为"任务完成了。

### 分界线 2：意图 vs 事实

Task 说"要做什么"，Step/Evidence 说"实际发生了什么"。

**它们必须能不一致**——不一致正是系统要发现的东西。合成一个对象，就再也无法表达"任务说要放置，但证据是命令之前的"。

### 分界线 3：可逆 vs 不可逆

Revision 可以拒绝、Event 可以重放，代价是零。

物理动作一旦下发就**没有撤销**。所以它需要一套完全不同的东西：幂等键、fencing、`UNKNOWN_OUTCOME` 作为终态、禁止自动重试。

**这是整套设计里最硬的一条约束**，也是右上角那两个格子为什么那么重。

### 分界线 4：时间尺度

| 对象 | 尺度 | 如果统一会怎样 |
|---|---|---|
| 租约 | 秒 | 太长 → 死进程锁住机器人 |
| Tool 下发 | 秒 | — |
| Agent 健康 | 秒~分 | — |
| Task | 分~小时 | 太短 → 重启丢业务 |
| 地图/标定 | 天 | 太短 → 每次观测都"过期" |

一个统一的 TTL 机制**必然在某个尺度上是错的**。

---

## 五、这些对象现在还剩什么问题

我不想把现状说得比实际更整齐。以下是真实缺口：

| 事实上存在的生命周期 | 现状 | 影响 |
|---|---|---|
| **人工审批** | 只是一个字符串 `ApprovalID`（`runner.go` 里 `"approval:" + taskID + ":physical"`） | **没有对象、没有超时、没有队列**。EscalationAgent 要落地必须先补这个 |
| **任务级租约** | 只有资源租约 | "一台机器人一个 Brain"的假设在分布式形态下不成立 |
| **标定 / 地图 / 策略版本** | 各自在 Python 侧有版本概念 | 没有统一契约，无法回答"这次失败是不是标定漂移导致的" |
| **操作员会话** | 控制台有登录 | 不参与任何生命周期 |

**`ApprovalID` 是最值得注意的一个**：它现在处于"大部分看着像新对象的东西，最后都是既有对象缺一个字段"这个判断的边界上——它缺的那个字段比较关键（超时和队列），所以它很可能是真要升格为对象的那一个。

---

## 六、该不该加新对象：三条判据

只有三个信号同时指向"不能复用既有的"时，才新建对象：

### 判据 1：是否引入了新的"世界不确定"状态？

> owner 崩溃后，是否有人会不知道物理世界的样子？

`Step` 和 `Tool 下发` 就是因此存在的——它们编码了"可能已经动了但没人知道"。

**是 → 必须新对象，且必须带对账路径。**

### 判据 2：是否引入了新的独占资源？

> 两个 actor 同时操作会不会造成损坏？

是 → 需要租约 + fencing token，而**不能复用语义不同的锁**。

### 判据 3：是否引入了无法回放的效果？

> 重放一次会不会改变世界？

是 → 需要幂等身份 + 不可逆终态。

### 三条都不满足 → **不要新建对象**

用「事件 + 投影」。这正是我把 Agent 事件塞进既有账本、而不是新建事件存储的原因。

---

## 七、未来可能出现什么

### 很可能需要（已有明确缺口）

- **Approval / Escalation 对象** —— 见上面第五节，最确定的一个。
- **Task lease** —— 多 Brain 抢同一任务。
- **Calibration / Map / Policy 版本** —— 统一"环境事实"的版本轴。

### 条件性需要

- **Safety envelope / 速度档位** —— 若允许执行中动态调整安全边界（比如有人进房间）。**风险等级最高**：它直接改物理约束，必须走审批。
- **Remedy 作为对象** —— Python 侧 `fault_remedy.py` 已经是一次有界动作（有尝试次数、有证据、有成功判据），但只存在于 Python 运行时。若要把 `ops.recovery_proposed` 接到执行，它得升格为跨语言对象。
- **经验 / 技能（Beads）** —— 接口我留好了（`agentcontract.Beads`，可修订、带版本、带置信度），但**刻意没有持久化实现**：没有消费者就先建 schema，是在为不存在的需求做设计。

### 建议不要加

"Agent session"、"conversation"、"memory session" 这类。当前每一个都是既有对象换名字，加了只会让"谁是权威"变模糊。

特别地：**LLM 会话状态故意不入账本**。它一旦进了权威路径，模型就间接获得了对物理动作的发言权。

---

## 八、落到代码上

这次多 Agent 改造的分层，恰好就是按"谁能被重启"划的：

| 层 | 可丢弃性 | 证据 |
|---|---|---|
| `agentruntime`（Agent/Bus/Registry） | **完全可丢弃** | `TANGYING_AGENTS=task` 关掉它，任务结果逐字节不变 |
| `core/agentcontract` | 只有值类型和接口 | **契约不该有生命周期**，有生命周期的是实现 |
| Task / Step / 租约 / 证据 | **不可丢弃** | 一个都没动 |

判断一个对象属于哪层，只要问：**它崩溃后，会不会有人不知道世界的样子？**

- 会 → 它必须在持久层，且必须有对账路径。
- 不会 → 它可以被随意丢弃和重建，不应该出现在权威路径上。

---

## 相关

- [多 Agent 运行时](../architecture/multi-agent-runtime.md)
- [Agent 事件规范](../architecture/agent-events.md)
- 闭环契约与失败分类：`core/closedloop/`（含 `closedloop_test.go`，把七类分类逐个钉住）
- 租约与 fencing：`fleet/lease/`
- 异常终态归档：`incidents/bundle.go`、`artifacts/incidents/`
