# 第 8 章核心素材：多 Agent 运行时

> 对象：`tangying-robot-agent-os` v0.6.0（`VERSION` = `0.6.0`，HEAD = `774bd2a2f`）
> 阅读范围：`agentruntime/`、`core/agentcontract/`、`core/agentcontext/`、`core/closedloop/`、`edge/agent/`、`cmd/local-agent/agentruntime.go`、`internal/autorecovery/`、`internal/recoveryexec/`、`tasks/context.go`、`internal/actionloop/context.go`、相关 docs 与实验报告。
> 证据规则：正文每个事实后标注 `文件:行号`；推断处标注「(推断)」；文档与代码冲突处标注「⚠️ 冲突」并同时给出两边。

**篇幅与用法说明**：本文是**素材**而非成稿，实际约 1.8 万字，超出"3000–5000 字"的目标篇幅。原因是任务列出的 8 项要求里有三项本身是多问句（第 1 项含 9 个子问题且要求精确签名、第 3 项要求故障码分类与举例、第 6 项要求三轮实验的全部真实数字），逐项如实回答必然超出。**若直接按 3000–5000 字成章，建议的取材优先级是**：§1.2–1.4（注册表/总线/编排器，约 1200 字）、§2 的三类边界与"只提议不执行"的结构保证（约 900 字）、§3.1–3.3（8 类 + 覆盖守卫，约 700 字）、§4.2–4.4 与 §4.7 的否证（约 900 字，本章最有教学价值的一段）、§5.2–5.3（投影与"为什么不能传 live 数据"，约 700 字）、§6.3–6.4（反事实证明与拒绝套用机制，约 600 字）、§7 的对比表（约 400 字）、§8（约 800 字）。§1.5–1.9 与全部源码索引是**查阅用的参考层**，成章时可只留结论。**(推断) 若目标篇幅是硬约束，唯一可压缩的是"每个事实都给出处"这一条——但那样会失去本文唯一的价值。**

---

## 1. Agent 运行时的架构事实

### 1.1 契约：`agentcontract.Agent`

Agent 的接口在 `core/` 而不在运行时包，理由是"TaskAgent 必须能作为 Agent 使用，而核心执行路径不应因此导入那个还知道注册表、事件总线和编排的层"（`core/agentcontract/contract.go:1-11`）。真实签名（`contract.go:199-229`）：

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

方法分两半：静态半（身份、订阅、权限）在启动任何东西之前被读，动态半（健康、事件、执行、关闭）运行期用（`contract.go:193-198`）。`Capability` 是粗粒度声明标签（`task.execution`/`observation`/`diagnosis`/`completion.verification`/`experience.recording`/`escalation`，`contract.go:48-68`），后三项明确标注 reserved——是给尚未实现的 EvalAgent、ExperienceAgent、EscalationAgent 留的位。

`Permission` 的零值是最安全的读法：只读、不改世界、需要批准（`contract.go:70-91`）。发布能力**不在** `Agent` 里，而是可选接口 `agentcontract.Publisher`（一个 `SetPublish` 方法，`core/agentcontract/publisher.go:27-29`）。

### 1.2 注册表：注册与启用分离

`Registry` 的三条字段就是它的全部：`agents map[string]agentcontract.Agent`、`order []string`、`config Config`（`agentruntime/registry.go:19-23`）。

- `NewRegistry(config Config) (*Registry, error)`（`:33-39`）在构造时校验配置，让"启用名打错"由建注册表的那段代码报出来，而不是在启动深处。
- `Register(agent agentcontract.Agent) error`（`:44-71`）拒绝五种情况：nil、空名、名字带首尾空白（它是配置键，必须精确）、**`len(agent.Capabilities()) == 0`**（什么都不声明的 Agent 更可能是接线错误）、订阅了词表外模式。重名返回 `ErrAgentAlreadyRegistered`（`:25-27`），理由是"名字给持久事件归属，冲突会让回放有歧义"。
- 注册顺序被保留，因为它是两个 Agent 优先级相同时的仲裁规则（`:67-69`）。
- `Discover`、`Registered`、`Enabled`、`EnabledAgents`、`Disabled`、`Validate`（`:74-149`）。`Validate` 的核心取舍：**配置启用了但未注册的名字被拒绝而不是跳过**——"否则运维会以为一个观察者正在运行"（`:111-130`）。

注册与启用分离是刻意设计：可以把 Agent 随构建发布，再用配置决定它跑不跑（`:12-18`）。默认启用三个（`agentruntime/config.go:63-65`）：

```go
Config{Enabled: []string{TaskAgentName, OpsAgentName, RecoveryAgentName}} // "task","ops","recovery"
```

另有 `ExecutableConfig()` 只启用 `task`（`config.go:73-75`），它是受支持的配置而不是测试夹具：用来判断"观测本身有没有改变行为"（`docs/architecture/multi-agent-runtime.md:116`）。`TaskAgentName`/`OpsAgentName` 常量在 `config.go:32,35` 手动重复而不从实现包导入，因为"本包不得导入它托管的 Agent 实现"（`config.go:28-31`）。

### 1.3 消息总线：投递语义

总线类型是 `AgentRuntime`（`agentruntime/bus.go:50-89`）。包注释先立了一条压倒一切的规则："运行时是执行过程的观察者，从不是参与者……一个慢的、错的或被关掉的运行时，让执行保持原样"（`bus.go:1-15`）。三条代码形态由此而来：

**(a) 订阅与模式。** `Subscribe(name string, patterns []string, capacity int) (Subscription, error)`（`bus.go:139-163`）。`Subscription` 只有 `Receive(ctx) (agentcontract.Event, error)` 和 `Close() error`（`:38-41`）。模式必须 `agentcontract.KnownPattern`，否则返回 `UnknownTopicError`（`:423-431`）——"订阅一个没人能发布的主题是拼写错误，而启动时失败的拼写错误是一份 bug 报告，静默失败的是一个永远不上报的 Agent"（`:135-138`）。

**(b) 不是广播，是每订阅者独立队列。** `Publish` 遍历订阅者，对 `MatchesAny` 命中的投递（`bus.go:195-200`）。设计理由写在结构体注释里：共享队列会让一个停止读取的观察者拖慢所有其他 Agent，并在队列满后开始让别人丢事件；这里每个订阅者有自己的 pending 集，**溢出记在造成它的订阅者头上**（`:44-49`）。

**(c) `Publish` 永不阻塞、永不返回错误。** `func (r *AgentRuntime) Publish(ctx context.Context, event agentcontract.Event)`（`:172`）：ctx 已取消则直接返回；ID 为空则生成 `evt-<n>`；`OccurredAt` 为零则取当前 UTC；然后 `firstDelivery(event.ID)` 去重，最后遍历投递。注释明说这"不是偷懒"：调用方是正在执行任务的 Agent，一个必须处理的发布失败会把事件流变成安全路径的一部分（`:165-171`）。

**(d) 去重是必需的，因为一个事实有两条入口。** `seen map[string]struct{}` 记住已投递的事件身份（`:58-69`）：Agent 可以直接发布一个事实，同一事实也会被记进按任务的持久账本、再由账本观察者喂回总线；两条路携带同一身份（由账本序号派生），所以第二次到达被识别而不是重复。"没有这一层，任务 Agent 做的每个动作都会在回放里出现两次。"（`:64-66`）`seen` 有界：每 4096 次投递清空一次，只需覆盖两条路可能竞争的窗口（`seenPruneInterval`，`:91-94,220-224`）。

**(e) 溢出按优先级驱逐。** 每个订阅者的 pending 按 `agentcontract.Priority` 分桶存放（`subscriber.queues map[Priority][]Event`，`:312-324`）。满时 `evictFor(priority)` 只在**严格更低优先级**的桶里丢最老的一个；如果所有 pending 都比新事件重要，被丢的是新事件本身（`:351-369`）。理由："否则一个低价值遥测的洪水会挤掉这个 Agent 存在的唯一理由——那一条关键事件。"（`:306-311`）默认每订阅者容量 256（`DefaultQueueCapacity`，`:29`）。

**(f) 优先级与顺序。** `Priority` 四档：Low/Normal/High/Critical，Critical 是"可能要求停止物理工作"的事件（`core/agentcontract/event.go:45-58`）。`pop()` 从最高优先级取，桶内保持到达顺序，这样"一次回放里 Agent 看到的东西与事实到达顺序一致"（`bus.go:371-387`）。

**(g) 关闭语义。** `Receive` 在订阅关闭后**先把已 pending 的排空**再报 `ErrSubscriptionClosed`（`:389-408`），"这样关停不会静默丢弃 Agent 还没读到的事件"。

**(h) 持久化与唤醒是两个旁路端口。** `EventSink func(ctx, Event)` + `SetEventSink`（`:445-459`）把事件写进已有账本，best-effort，错误绝不回传给发布者；`onPublish func()` + `SetPublishObserver`（`:111-120,439-443`）只做一次非阻塞"推一下"，因为运行时把事件投进每订阅者队列却**刻意不为每个订阅者起 goroutine**，必须有东西告诉排空者队列值得排空（`:80-88`）。

**投递语义小结**：同步（在发布者 goroutine 上入队）+ 每订阅者异步消费 + 按模式过滤的"定向广播"；无 ack、无重投、有去重、有优先级驱逐、有寿命计数（`Published()`/`Dropped()`，`:268-273`，注释说"Dropped 应保持为 0，非 0 意味着某个 Agent 相对于它订阅的速率太慢"）。

### 1.4 编排器：唯一拥有"排序"这个决定的地方

`Orchestrator`（`agentruntime/orchestrator.go:31-96`）明确不做什么："它不决定任务是否完成、某步是否可重试、某个物理动作是否可派发——那些已经有主；第二个决策者持不同意见，是系统开始自相矛盾的起点。它唯一拥有的决定是排序：观察者在机器人运动中提议时，提议要等。"（`:16-30`）

- `Start`（`:123-216`）：先 `registry.Validate()`（配置不能被满足时系统保持原样而不是半启动），再把总线**注入**每个实现了 `Publisher` 的已启用 Agent（`:156-167`）。注入放在运行时而不是组合根，源于一次真实事故：接线代码手工给执行 Agent 装了发布通道、观察 Agent 没装，于是它发现的每个问题都进了告警存储却从未上总线——"单元测试全绿，因为每个测试都自己注入了 sink：它们验证的是一套生产环境并不存在的接线"（`:141-155`；另见 `core/agentcontract/publisher.go:18-23`）。`Publishers()`（`:614-618`）把结果打进启动日志，因为"没什么可说的"和"根本说不了话"从外面看一样。
- 编排器**自己**用 `supervisorPatterns()` 订阅全部命名空间（`:171-177,336-342`），因为"把仲裁建立在 Agent 订阅的并集上，会让关掉一个观察者静默关掉保护移动机器人的那条规则"（`:46-52`）。
- 投递循环 `deliver`（`:219-239`）先 drain 再等 `wake`，`drain` 从订阅里"拉"而不是被推（`collect` 带 20ms 超时，`:324-332`），这样慢 Agent 不能拖住编排器。顺序上 supervisor 先于 Agent：**仲裁状态必须在任何 Agent 看到事件之前更新**（`:255-266`）。
- `handle`（`:285-295`）里，Agent 的事件处理失败**不向上传播**：记为 `agent.health_changed{EVENT_HANDLING_FAILED}`，"观察者的反应失败是观察者自己的问题，让任务背上就是把可观测性变成安全路径的一部分"（`permission.go:184-200`）。
- `trackPhysicalAction`（`:356-391`）：按 `commandId` 记录在飞的物理命令（没有 commandId 才退化到计数）。注释记录了一个真实缺陷：早期用计数器，`SENDING(+1) RUNNING(+1) CONFIRMED(-1)` 留下永久 +1，于是 `PhysicalActionInFlight` 对那个任务永远为真、每条恢复建议被永久推迟——"最需要一个操作员的那种情况，恰恰是运行时沉默的那种"（`:56-68`）。`AWAITING_EVIDENCE` 结束在飞窗口，因为机器人已经停了（`:344-355`）。
- `deferIfPhysicalActionInFlight`（`:409-421`）不只扣住事件、还发布 `ops.recovery_deferred{deferredReason:"PHYSICAL_ACTION_IN_FLIGHT"}`（`:425-435`），"没有记录的推迟与丢弃无法区分"。`releaseDeferred`（`:439-473`）释放时**赋予新的事件身份**（`released.ID = ""`），因为原身份已被去重消费——"原样重发会被当作重复抑制，提议就丢了而不是被推迟"。
- `Observer` 接口（`:519-527`）是"运行时如何调度一个 Agent"，因此不在 contract 里；`DefaultHealthInterval = 5s`（`:532`）；`evaluateOnce` 的顺序是"先释放被扣住的、再让每个观察者看、最后报健康"（`:507-517`），且在第一个 tick 之前**立刻评估一次**，否则"每次启动后有整整一个间隔是盲的"（`:482-491`）。

### 1.5 `context.go` 与 `projection.go`：两个被同名混淆的东西

这两个文件名字里的"上下文/投影"指的是**两件不同的事**，教学中必须分开：

**(A) `agentruntime/projection.go`：任务账本 ↔ Agent 词表的桥。** 仓库已经有一条按任务持久化、有序、带回放投影的事件账本；再建一个 Agent 专属事件存储会出现同一件事的两份记录，它们会不一致，而回放只显示其中一份（`projection.go:12-25`）。于是：

- `ProjectTaskEvent(taskID string, event tasks.TaskEvent) (agentcontract.Event, bool)`（`:31-33`）：把账本事件投影进 Agent 词表。映射表在 `taskEventToEvent`（`:48-114`）：`TOOL_ACTIVITY → action.executed`（`FAILED` 提升为 High，并带出 `taskRevision`）、`STATE_CHANGED → state.transition`（按目标状态定优先级，`:120-132`）、`GROUNDING_EVIDENCE → evidence.collected`、`LOCAL_RUN_SUCCEEDED → task.completed`、`RECOVERY_ACTIVITY → ops.recovery_proposed`、`SAFETY_STOPPED → ops.escalation_required`（Critical）。无法识别的类型返回 false 而**不是**发明一个没人订阅的主题（`:43-47`）。
- `TaskEventFromEvent(event agentcontract.Event) (tasks.TaskEvent, bool)`（`:37-39`）：反方向。topic 成为事件类型**逐字**，这正是"Agent 的发现出现在普通人已经在看的任务回放里，而回放本身不需要知道 Agent 的存在"的原因（`:153-157`）。`TaskID` 为空的事件（如 `agent.registered`）返回 false——账本是按任务的，"把它们归到某个它们并不描述的任务下"是错的（`:159-164`）。
- 身份是派生的：`projectionID` = `taskID + "#" + sequence`（`:140-151`），"回放同一份账本产生同样的身份；身份在实时流和回放之间变化会让两者无法对账，而保持一份账本就是全部意义"。

**(B) `agentruntime/context.go`：给模型看的决策上下文。** 只有 54 行：

- `FindingContext(f Finding, taskID string, now time.Time) agentcontext.Document`（`:11-16`）：把一个发现变成 `Stage:"ops"` 的文档，`Records` 里写"规则发现=…；组件=…；分类=…；严重度=…；消息=…；规则输入=…"，`Constraints` 明写"自动重试禁止=%t；该发现不授予执行权限""规则推断不能升级为新的物理观测"。
- `RecoveryRequest.ContextDocument(now time.Time) agentcontext.Document`（`:20-39`）：`Stage:"recovery"`，三条约束（目录是唯一可建议的动作范围；建议不是批准；执行终态未知或账本不可读时不得派发新的物理动作；模型输出属于假设）。目录被写成 `Tools[]`，每条的 `MutatesWorld` 由 `a.Risk != RiskReadOnly` 决定；trail 的每一步变成一条 `Record`，`TrailModel` 的 kind 标为 `hypothesis`（`:28-37`）——**模型自己说过的话被降级为假设，而不是事实**。
- `contextEnvelope(d agentcontext.Document) map[string]any`（`:41-54`）：`Mode()=="legacy"` 时返回 nil（不改变旧线格式），否则 `agentcontext.Project(d, d.Stage)` 后序列化。使用时挂在事件载荷的 `agent_context` 字段上（`agentruntime/opsagent.go:477-479`）以及模型规划请求上（`agentruntime/recoveryagent.go:609-610`）。

### 1.6 三层记忆与运行级存储

`core/agentcontract/memory.go:8-27` 定义三层，并给出为何不能混为一谈：

- **Ledger**：发生过什么。只追加、有序、永不编辑；这是回放与审计读的记录。`Append(ctx, LedgerRecord) (LedgerRecord, error)`、`List(ctx, taskID, limit) ([]LedgerRecord, error)`（`:30-52`）。
- **Beads**：相信什么。命名、带版本、可修订的摘要；`Upsert/Get/Query`（`:55-98`）。"一条信念可以被修订；一个事实不可以。"
- **Execution**：机器人实际做了什么，**包括结果未知的步骤**。`Steps()` 与 `Uncertain(ctx, taskID) ([]StepRecord, error)`（`:105-131`）——`Uncertain` 就是对账输入，也是这一层必须与账本分开的原因。

`Memory` 结构体把三者打包，字段是接口：nil 表示该层不可用，Agent 必须把不可用读作"无法回答"而**不是**"空"（`:133-142`）。`TaskHistory` 是第四个端口：`TaskIDs`/`Abnormal`/`Events`（`:155-170`），它存在的理由是"实时事件只描述现在：一次失败后重启的进程看到的是一个空世界，而对监督者来说，值得复核的失败恰恰是活得比产生它的进程更久的那些"（`:144-154`）。

实现状态（`agentruntime/memory.go`）：`MemoryLedger`（进程内，`:34-89`，`Append` 要求 `Kind` 非空）、`MemoryBeads`（进程内，`:101-176`，`Upsert` 递增 `Version`、校验 `Confidence∈[0,1]`、`Query` 按更新时间倒序）、`ExecutionMemory`（**唯一真实实现**，`NewMemory(store ExecutionStore)` 只在 store 非 nil 时装配，`:280-289`）。`Uncertain` 的判据是"已启动且从未到达终态且未对账"（`:245-263`），并显式跳过 `Reconciled` 的步骤，否则"报告会比它描述的条件活得更久——安全信号就是这样变成噪音的"（`:252-257`）。`stepRecordFromRun` 故意不填时间戳："在执证证据上伪造一个时间戳比缺一个更糟"（`:265-276`）。

`runnermemory.go` 是**运行级（机器人级）**存储，不是记忆层：`RunnerAlert`（`:29-48`）与 `AlertStore`（`:51-83`）。存在的理由是"发现有两种作用域，只有一种有地方去"：任务级发现写进该任务的账本，机器人级发现（急停、模块故障、观测过期）不属于任何任务，早期版本给它空 taskID，于是**发给了没人、也没记在任何地方**（`:10-26`）。`Record` 整批替换而不是合并（`:90-113`），"一次评估报告它当前能看到的一切，所以不再被报告的条件就不再为真"；TTL = 3 个评估周期（`DefaultAlertTTL`，`:72`），"比评估间隔长才不会每个 tick 闪一下"。`RunnerAlertPlan`（`:170-180`）把计划连同调查轨迹挂在触发键上，供控制台把告警与它的计划一起显示。

### 1.7 `trail.go`：调查轨迹记录什么

一段结论只有在通往它的路被记下来时才能被复核："散文式推理不可复核——读者分不清哪句来自数据库、哪句来自模型记忆、哪句是假设。所以一次调查的每一步都是一条形状固定的记录，并与结论一起发布。"（`trail.go:9-23`）

- `TrailKind` 六种（`:25-41`）：`query`（读数据存储）、`rule`（确定性规则跑过）、`model`（一次模型调用）、`decision`（依据前面事实做选择）、`action`（提交动作，本版本不出现）、`verification`（事后复验，本版本不出现）。
- `DataSource`（`:49-61`）带 `Kind/Name/Table/Query`，**写真实表名**而不是"任务存储"这种口号（`:43-48`）；`dataSources` 是常量，名字对齐 `middleware/sqlite` 的 schema：`tasks`/`task_events`/`step_runs`/`observation_evidence`，另加 `file/incident bundle`、`runtime/telemetry snapshot`、`memory/recovery catalog`（`:196-212`）。
- `TrailStep`（`:83-106`）：`Sequence`、`Kind`、`Name`、`Source`、`Summary`、`Findings map[string]any`、`Rows`、`Error`、`At`。两处刻意设计：`Rows` 与 `Findings` 分开，因为"我读了 200 行"和"我读了 0 行"是不同情况（`:97-100`）；`Findings` 要求**有界、可打印的标量**——"结果展示不出来的步骤无法复核"（`:94-96`）。失败的读取也记 `Error`——"隐藏失败步骤的调查会显得比它实际知道的更确定"（`:101-103`）。
- `Trail`（`:109-121`）：`ID`（可把提议追回到产生它的步骤）、`TaskID`、`Trigger`、`Steps`、`StartedAt/EndedAt`。"一次调查写一次、永不重写，因此可以安全发布"（`:123-124`）。`Append` 自动分配序号（`:125-131`）；`Encode()`（`:147-189`）输出 `trailId/steps/stepCount/startedAt/endedAt`，每步显式写出 `sequence/kind/name/summary/at` 与可选 `source(+sourceDetail)/findings/rows/error`。`trailID(taskID, trigger)`（`:217-222`）让"同一任务里同一条件的两次调查"可识别为同一条调查线。

### 1.8 `ledgerfilter.go`：过滤什么

过滤的是**持久写入**，不是总线（`ledgerfilter.go:40-45`）。问题有实测数字：一次真实部署的 **159,314 行账本里 158,193 行（99.3%）是观察者上报**，单单一个任务上的一个持续条件占了 **1,410 行**；账本同时是回放源与训练语料，所以重复不只是浪费空间——它埋掉了账本存在的意义（执行记录），并让数据集导出在一份"基本上是同一句话重复"的语料上报出"没有可用样本"（`:9-23`）。

规则是"账本记录的是**转变**，不是观测"（`:25-38`）：未打开的条件是事实（它打开了）；已打开条件的重复不是（什么都没发生）；关闭是事实并重新打开该身份。键是稳定身份（code+component）而**不是**上报身份——上报身份带观测计数，用在这里会让每个 tick 都恶化的条件每个 tick 都被记录（`:34-38`）。

`Admit(event agentcontract.Event) bool`（`:80-113`）：非发现类事件直接通过（执行事实、状态转变、恢复动作天然一行一次）；`TaskID` 为空直接通过（账本是按任务的，让它消耗一次 episode 会静默掉之后可归属任务的同名发现）；`ops.anomaly_cleared` 先 `forget(key)` 再返回 true（"收尾边永远是事实，它是让读者能给一次 episode 定界的那一行"）；已打开则拒绝。容量 `DefaultLedgerFilterLimit = 4096`（`:60-65`），淘汰计数 `Dropped()`（`:125-133`）。组合根把它挂在 `runtime.SetEventSink` 上，并明确"过滤只在持久写入这一侧，总线——因而是控制台、恢复触发和所有其他订阅者——仍看到每一份上报"（`cmd/local-agent/agentruntime.go:155-176`）。

### 1.9 `permission.go`：门控

"声明'我是只读的'是文档。这才是控制。"（`permission.go:11-26`）`Orchestrator.RequestMutation(ctx, MutationRequest) (MutationDecision, error)`（`:85-92`）拒绝时**发布** `agent.permission_denied` 再返回错误——"一次不留痕的拒绝，与一个从未请求过的 Agent 无法区分；观察一个 Agent 试图做什么，往往比观察它做了什么更有信息量"（`:78-84`）。`decide` 是纯函数（`:96-146`），判定顺序即安全论证：什么都没请求 → 未注册 → **只读优先**（"一个只读 Agent 要改世界正是这道门存在的理由，必须仅凭声明拒绝，而不是凭某个未来改动可能放松的更细的检查"）→ 未声明 `MutatesWorld`/`MutatesTaskState` → 未声明能力 → 需要批准而请求未携带。拒绝码是稳定常量：`AGENT_READ_ONLY`/`CAPABILITY_NOT_DECLARED`/`AGENT_APPROVAL_REQUIRED`/`AGENT_NOT_REGISTERED`/`NO_MUTATION_REQUESTED`（`:30-43`）。**门控不重新推导安全**：它不判断动作是否安全、是否可重试、操作员意义上的批准是否存在——那些已经有主；它只回答"提出请求的 Agent，是不是那种可以做这件事的 Agent"（`:17-26`）。

---

## 2. 三类 Agent 的职责边界

当前启用的是 **task + ops + recovery** 三类（`config.go:63-65`，`docs/architecture/multi-agent-runtime.md:3-16`）。

| | `task` | `ops`（产品名 Review Agent） | `recovery` |
|---|---|---|---|
| 实现 | `edge/agent.Runner` | `agentruntime.OpsAgent` | `agentruntime.RecoveryAgent` |
| 输入 | 宿主给的 `ExecuteRequest`（不订阅事件） | 事件 + 遥测 + 故障台账 + 持久执行记录 + 持久任务索引 | 一个 `Finding`（由 `ops.anomaly_detected` 触发） |
| 输出 | 任务终态、动作、证据 | `ops.anomaly_detected` / `root_cause_hypothesis` / `recovery_proposed` / `escalation_required` / `anomaly_cleared` | `ops.recovery_plan`（含轨迹与目录） |
| 权限 | `ReadOnly=false, MutatesWorld=true, MutatesTaskState=true, RequiresApproval=false`（`edge/agent/agent.go:69-76`） | `ReadOnlyPermission()`（`opsagent.go:156-158`） | `ReadOnlyPermission()`（`recoveryagent.go:218-220`） |
| `Execute` | 委托给 `RunControlled`，闭环规则一条不少（`edge/agent/agent.go:117-130`） | 恒返回 `ErrNotExecutable`（`opsagent.go:209-211`） | 恒返回 `ErrNotExecutable`（`recoveryagent.go:257-259`） |

TaskAgent 的 `Subscriptions()` 返回 **nil**："它在被要求时执行任务，不对事件作出反应；假装它订阅会让它进入一条投递路径，而慢执行者会在那里丢掉它从不需要的事件"（`edge/agent/agent.go:56-59`）。它的 `Capabilities()` 从 `manipulation.Catalog()` 读，不手写，以免与 guard/运行时能力检查漂移（`:39-54`）。`RequiresApproval=false` 是刻意的：物理工作的批准已经在执行路径里逐步强制（`Task.Approved` 然后派发门），在 Agent 层再复制一遍"要么冗余，要么成为以后有人放松的那一条"（`:61-68`）。

### OpsAgent（Review Agent）：只读观测者

**"Review Agent" 在 Go 里没有对应标识符**（全仓库 `grep ReviewAgent` 只命中文档），它是 `OpsAgent` 的产品角色名（`docs/architecture/review-agent.md:3`）。**因此 Review Agent 已实现**；真正"只有接口与扩展点、没有实现"的是 `eval`、`experience`、`escalation`（`docs/architecture/multi-agent-runtime.md:16`，`docs/development/adding-an-agent.md:68-156`）。

不能说不能做，是结构决定的，不是纪律：`OpsAgent` **没有任何执行端口、任务服务或总线句柄**（`opsagent.go:20-27`），并由一张字段白名单反射测试钉住——任何新字段都会失败，"这强制'观察者需不需要这个'这个问题被刻意回答，而不是被静默吸收"（`agentruntime/opsagent_test.go:44-105`）。

- 规则是确定性的，不猜："当证据不足时，Agent 说出缺哪条证据，而不是产出一个看似合理的成因。操作机器人的人需要知道一句话是被证明的还是被怀疑的。"（`opsagent.go:35-42`）规则本体在 `agentruntime/opsrules.go`：`Evaluate(input ObservationInput) []Finding`（`:180-195`）依次跑 safety → fault → uncertain mutation → failed action → latency → abnormal task → stale telemetry，然后排序。阈值是文档化常量而不是学出来的基线：`DefaultTelemetryMaxAge = 10s`（`:61`）、`DefaultStepLatencyBudget = 30s`（`:67`）、`maxEvidenceRefs = 8`（`:71`）。
- `Finding`（`opsrules.go:74-108`）的关键字段：`Category`（所属闭环失败类，非失败类为空）、`RecommendedActions`（人的动作，按顺序）、`MissingEvidence`（说不清时说明缺什么）、`AutomaticRetryForbidden`（**只为物理结果未知设置**，"唯一一条不得在下游被软化的建议"）、`Confidence`（由产生它的规则给定而非从数据估计）。`Identity()` = `agentcontract.AnomalyIdentity(code, component)`（`:110-121`），"它是系统每一部分都必须同意的键"，并且注释记录了曾经的失败：告警存储按 `code@component` 建索引、恢复 Agent 按裸 `code` 归档计划、控制台按裸 `code` 查——"结果是一份计划被显示给 11 个不同的组件失败，每个都在读一份诊断里写着另一个组件名字的计划"（`:112-118`）。
- 上报去重键在身份后追加 `#<数量>`（`AnomalyReportID`，`core/agentcontract/anomaly.go:46-52`），"稳定条件不刷屏，**恶化的状态必须重播**"；`SameAnomaly` 只在 `#` 边界比较，所以 `CODE@a` 不会匹配 `CODE@ab`（`:54-68`）。
- 四类事件的语义分工是刻意的："anomaly 说事实，hypothesis 说结论。合成一个事件，读者就分不清哪句是观测、哪句是推断。"（`docs/architecture/supervision-verification.md:88`）`publishFinding`（`opsagent.go:435-535`）先发 anomaly，有 `Category` 才发 hypothesis（带 `evidenceChain`、`confidence`、`automaticRetryForbidden`），有建议才发 proposal，`Severity==critical` 才发 escalation。
- **"不执行"的第一层保证是恒为 advisory**：`AutomationAdvisory = "advisory"`（`opsagent.go:539`），proposal 恒 `AutomationLevel=advisory, RequiresApproval=true`（`:500-516`），注释说明"这个 Agent 的每一条建议都是人的工作；把它提议成自动化会误表述已知的东西"。
- 它同时输出**两类作用域**的发现：任务级进任务账本（`RunnerAlerts.Record` 显式跳过 `TaskID != ""`，`runnermemory.go:96-100`），机器人级进 `AlertStore`（因为"急停不是任务问题，无论有没有任务在跑，它都是操作员必须听到的那件事"，`runnermemory.go:18-21`）。
- `Health()` 在"运行着但没有观测可推理"时返回 **DEGRADED 而不是 HEALTHY**："一个看不见的观察者不是健康的；说清这一点就是'没有坏事'与'我判断不了'之间的差别。"（`opsagent.go:160-204`）

### RecoveryAgent：只提议不执行的第三类

`RecoveryAgent` 的定位在 `recoveryagent.go:17-41`：**它提议，它不动手**。"这个类型不持有执行端口、任务服务或机器人客户端。即使它决定要跑一个恢复动作也跑不了——与观察 Agent 遵循同一条结构规则：'一个只做建议的 Agent'应当是**这个类型**的性质，而不是它当前配置的性质。执行属于运行时，在权限门控之后，这样只有一处需要审计。"

`TestRecoveryAgentHoldsNoExecutionPort` 用反射遍历字段并白名单（`Catalog`/`ReadFacts`/`Model`/`Publish`/`RememberPlan`/`Now`/`mu`/`proposals`/`stopped`），"加一个 `RobotClient` 字段会让它失败——这是有意的，因为'恢复 Agent 突然能动机器人了'必须是一次显式的、被评审的改动"（`agentruntime/recoveryagent_test.go:46-73`）。由此得到一条可以直接对用户承诺的性质：**在这一版里，恢复 Agent 不可能自己动手**（`docs/architecture/recovery-agent.md:48`）。

- 输入：一个 `Finding`。`Recover(ctx, finding Finding) *agentcontract.RecoveryPlanPayload`（`recoveryagent.go:278`）。触发键是**发现的身份而不是 code**："code 标识一类问题；身份标识这个问题。两个组件以同样方式失败是两个问题、两份计划。"（`:289-293`）
- 事实是**值而不是活句柄**：`RecoveryFacts`（`:77-114`）"刻意是普通结构体而不是存储的活句柄：Agent 必须无法触及任何它没有声明的东西，而一个值不能被用来跑一条没人记录的查询"。其中最要紧的是三态区分：`UncertainSteps`（确实存在未知步骤）、`ReconciliationUnavailable`（**问不出来**）、`ReconciliationError`（原因）。文档说明了它修的是什么静默漏洞："'这个任务没有结果未知的步骤'和'这个部署查不出来'曾经产生同一个结果——空列表；于是执行存储不可达时，调查看起来像世界状态已确认，恢复计划照常给出。"（`docs/architecture/recovery-agent.md:78-102`）
- 一条压倒一切的规则：**先对账**。`choosePlan`（`recoveryagent.go:505-630`）在 `AutomaticRetryForbidden`、存在 `UncertainSteps`、或 `ReconciliationUnavailable` 三种情况下都转人工、不给步骤（`:556-586`），并分别记 `rule.reconcile-first` / `rule.reconcile-unknown` 轨迹步。第三种情况的代价是停下来、收益是"不会在一个可能已经动过的世界上再动一次"（`docs/architecture/recovery-agent.md:102`）。
- 确定性路线永远生效："先看再动永远不会错"——只读匹配永远排在前面（`:588-596`）。有模型时，模型选出的步骤**前缀在确定性只读步骤之后**（`:615-623`），且只有在模型输出真的进入返回计划的那一处才把 `Source` 标成 `"deterministic+model"`（`:627-628`）——"对一份模型从未看过的计划报'来自规则 + 模型'是关于来源的、纯粹的假声称"（`:152-161`）。
- 输出：`agentcontract.RecoveryPlanPayload`（`core/agentcontract/payload.go:459-491`）：`PlanID`（由轨迹 ID 派生）、`TaskID`、`Verdict`（`PLAN`/`ESCALATE`，`payload.go:422-432`）、`Trigger`、`Diagnosis`、`Confidence`、`Steps[]`、`EscalateReason`、`Trail`、**`Catalog`**、`Source`、`ProposedAt`。`RecoveryStep` 带 `Order/Action/Summary/Risk/RequiresApproval/Why`（`payload.go:436-457`）——`RequiresApproval` 写在步骤上而不是让读者推断（"一份需要同意的计划应当在它被读到的地方说明这一点"）。
- **目录是模型驱动恢复的承重安全边界**（`recoverycatalog.go:5-16`）："模型可以推理一个不熟悉的故障并提出新颖的序列，这正是用一个模型的意义——但它提出的每一步都必须命名这里的一个条目。它不能发明动作，也不能触及一个未被列出的接口。"`DefaultRecoveryCatalog()`（`:122-229`）**16 条**动作：6 条 `read_only`、7 条 `bounded_write`、3 条 `never_automatic`（`estop.release`/`hardware.replug`/`calibration.change`，每条带目录自己写的拒绝理由）。三档风险（`:31-42`）：`read_only` 不需批准；`bounded_write` **必须**批准；`never_automatic` 谁的提议都不执行。**"批准"是动作的属性，不是提议者的属性**（`:19-28`）："随着更多提议者出现（模型、模式库、未来的策略引擎），否则每个都需要自己的一套'什么安全'的概念，而它们会漂移。"
- 校验把提议变成可信计划（`recoveryagent.go:632-668`）：目录里没有的动作、`!Executable()`（即 `never_automatic`）都被**按名字拒绝并附目录自己的理由**；重复动作只保留第一次（"对一次写来说，重复运行就是第二次没人要求的物理动作"）。**拒绝会否决整份计划**：`refusals` 非空 → `Verdict=ESCALATE`、`payload.Steps=nil`，但被拒动作留在轨迹里供复核（`:365-403`，"提议者既然对'什么允许做'的理解是错的，它剩下那部分推理也不值得执行"）。
- 冷却 `recoveryPlanCooldown = 2 * time.Minute`（`:695-696`），防止持续条件每个 tick 重新调查、重新问一次模型。

### 执行通道：提议与执行在代码里被分开

- 手动通道 `internal/recoveryexec`（`docs/architecture/recovery-agent.md:197-253`）：`Executable()` 只放行 `read_only` 与 `bounded_write`；声明的工具必须真的存在；风险决定要不要问人，**没配批准入口时不执行**（"没法问"不等于"可以"）；执行范围 = 该动作声明的工具。两种批准来源分别记 `approval.operator`（操作者点按钮，点击即批准，`executor.go:257`）与 `approval.granted`（上游批准端口，`:275`），因为"有人能批准"和"这次确实有人批准了"在事后追溯里是两句话。
- 自动通道 `internal/autorecovery`（`docs/architecture/recovery-agent.md:255-296`）：**线画在"目录说它 `read_only`"上，不是画在"工具自称不改世界"上**（`internal/autorecovery/supervisor.go:16-23`）。`runOne` 在 `action.Risk != agentruntime.RiskReadOnly` 时直接 skip 并记理由（`:183-190`）。"目录里 7 个会动机器人的动作全是 `bounded_write`，所以自动通道结构上够不到它们——守卫和能力是同一个事实，不是一道可以被删掉的检查。"（`docs/architecture/recovery-agent.md:266-267`）它用 `OperatorApproved: false` 标记录（`supervisor.go:196-204`），"说成别的就是把一个人的同意写进了一份没有同意的记录"。真实数据（52 个任务）：`observe.re-read` 自动执行成功 **21 次**、`execution.read-history` **6 次**（接线前 8 次全部因工具缺失失败）、账本 **27 条** `ops.recovery_executed` 且 `operatorApproved` 为空（`docs/architecture/recovery-agent.md:285-291`）。
- 触发链：组合根用一个**独立订阅者**订阅 `ops.anomaly_detected`，把事件还原成 `Finding` 再调 `recovery.Recover`（`cmd/local-agent/agentruntime.go:178-214`，`findingFromEvent` 在 `:515-538`）。注释给出了为什么是订阅者而不是观察 Agent 内部的一次调用："这样观察 Agent 保持只读、两个角色保持可分离——和这里其他地方 Agent 从不直接互相调用是同一个理由。"（`:181-183`）

### 未实现的三类

`eval`/`experience`/`escalation` 只有接口占位（`contract.go:59-67`）、扩展点文档与一段 EvalAgent 骨架（`docs/development/adding-an-agent.md:68-147`）。关于否决权，文档明确要先解决的问题："一个否决是阻止完成声明，还是把任务送进一个需要人工的状态？仓库既有原则倾向后者……否决权应当复用同一套状态语义，而不是新增一条'Agent 可以让任务失败'的路径。"（`adding-an-agent.md:145-147`）

---

## 3. 故障矩阵

### 3.1 分类体系：8 类，179 条表项 / 177 个唯一码

**8 个失败类**（`core/closedloop/closedloop.go:45-68`）：`TRANSIENT`（基础设施恢复后可原地重试）、`PERCEPTION`（需要先新观测或搜索）、`PLANNING`（需要新目标或计划）、`PERMISSION`（批准/画像/目录/fencing 状态不对）、`RESOURCE`（资源被别的所有者持有或缺少授权）、`VALIDATION`（命令本身被判为畸形，同参数重试不可能成功）、`UNKNOWN_OUTCOME`（**禁止自动重试**）、`FATAL`（重试同一工作无法解决）。

查找表 `classification`（`:78-287`）是**有序**的，"第一条匹配的规则获胜，所以更具体的码集列在前面"。我的清点（对 `closedloop.go` 中 `classification` 块逐条解析）：**8 条规则、179 条表项、177 个唯一码**，其中 `XLEROBOT_MAX_RELATIVE_TARGET` 与 `XLEROBOT_MAX_ACTION_CHUNK_LENGTH` 同时出现在 `Permission` 与 `Planning` 两处，按顺序由 `Permission` 生效。各类条数：`UnknownOutcome` 15、`Permission` 29、`Resource` 9、`Perception` 47、`Planning` 17、`Validation` 27、`Transient` 32、`Fatal` 3（每条规则内部经 `set()` 去重）。

**⚠️ 冲突（且已定位"113"的出处）**：`docs/development/2026-09-18-system-review-and-improvement-plan.md:230` 与 `:628` 写"**113 个运行时故障码**……分成 8 类"。这个 113 不是分类表的条数，而是**提交 `c8cae5af2`（2026-09-17，提交信息「分类表补齐 113 个运行时故障码」）那一刻 `classification_coverage_test.go` 里 `runtimeEmittedCodes` 清单的条数**；该清单今天有 **123 条 / 119 个唯一码**，而 `closedloop` 分类表本身有 **179 条表项 / 177 个唯一码**。也就是说：三个数字（113 / 119 / 177）分别是"当时提交的运行时码清单""今天的运行时码清单""今天分类表覆盖的码"，互不等价。文档的类区间引用（`closedloop.go:50-67`、`:329-336`）也已相对当前代码漂移（现在是 `:48-68` 与 `:333-341`）。教学时应以代码为准，并把该文档视为历史快照。

**⚠️ 冲突**：`docs/architecture/review-agent.md:48` 写"**七类**"，紧接着列出的却是 8 个类名；`docs/architecture/supervision-verification.md:24,298` 同样写"七类 + `UNKNOWN_OUTCOME` 兜底"，而 `UNKNOWN_OUTCOME` 就是 8 类之一。代码是 8 类（`closedloop.go:45-68`），且 `docs/architecture/review-agent.md:177` 自己也写"八类"。

**兜底方向选在安全一侧**：`Classify(code string) Class`（`:289-299`）对空或未识别的码返回 `UnknownOutcome`——"系统知道出了事，但不知道什么到了硬件，所以不允许自动重试"（`:286-288`）。`Retryable()` 只对 `Transient` 与 `Perception` 为真（`:333-341`），并且注释把它的含义收窄写明了：它回答的是"这一类失败，重试在原则上是否安全"，**不是**"系统会重试"——没有任何东西会自动重试，物理重试是操作员的决定，入口是 `requiresApproval` 的 `task.retry-step`（`:325-332`，`docs/architecture/review-agent.md:188-194`）。

### 3.2 `faultmatrix_test.go` 钉住了什么

文件头说明它回答的不是"观察者有没有规则"（那由 `opsagent_test.go` 覆盖），而是"当一个真实任务出问题时，监督者是否真的说了有用的东西"。每个场景驱动**真实 OpsAgent**，断言三件事：**检出了**、**按系统其余部分的方式分类了**、**带一条人能执行的动作**（`agentruntime/faultmatrix_test.go:22-36`）。第三条是重点，也是以前从未测过的："一条没有可用建议的 finding 就是一行日志：它告诉操作员一件他本来就能看到的事，而没告诉他该做什么。"（`:30-33`）

`assertScenario`（`:306-348`）逐项断言 `Category`/`Severity`/`wantRetryForbidden`/建议子串；`wantRetryForbidden` 是**每个场景都显式断言**的，而不是只在为真时断言——"危险的回归是一条失败的禁止重试悄悄消失"（`:48-52`）。四个测试函数：

- `TestFaultMatrixCoversEveryFailureClass`（`:99-200`）：**10 个**场景覆盖 8 个类（`PLANNING` 有三例：`TARGET_UNREACHABLE`、`NAV_WORKSPACE_LIMIT`、`NAV_STEP_LIMIT`）。关键行为：`TRANSIENT` 建议含"可重试"；`PERCEPTION` 含"重新观测/搜索"；`PLANNING` 含"新的目标/路径"；`PERMISSION` 与 `RESOURCE` 含"审批/租约/资源"；`VALIDATION` 含"参数/版本/重放"；**`UNKNOWN_OUTCOME` 严重度 critical + `wantRetryForbidden` + 建议含"不要自动重试/对账"**；`FATAL` 含"人工"。
- `TestFaultMatrixCoversNonCodeFaults`（`:204-281`）：**6 个**非错误码场景。机器人报 `blocked` 模块 → `ANOMALY_COMPONENT_FAULT`，`Category=""`（"blocked 故障不是闭环重试类，监督者不得假装它是"），critical，建议含机器人自己的处置指令；急停 → `ANOMALY_SAFETY_STOP` + `Permission` + critical + "人工复位/急停"；执行记录里 `STARTED` 的物理步骤 → `ANOMALY_UNVERIFIED_MUTATION` + `UnknownOutcome` + critical + **禁止重试** + "对账/观测"；90s vs 30s 预算 → `ANOMALY_STEP_LATENCY`，`Category=""`（"慢步骤不是失败类，不得给它安一个"）；120 秒前的观测与完全没有观测都 → `ANOMALY_TELEMETRY_STALE`。
- `TestFaultMatrixEscalatesOnRepetition`（`:285-303`）：同一动作失败 3 次 → severity 从 warning 升到 **critical**，"一次发生是平常的；第二次意味着自动处理已经没起作用了"（`:283-284`）。
- `TestRepeatedFailureEscalatesThroughTheRealAgent`（`:379`）与 `TestUnknownOutcomeForbidsRetryAndClearsOnReconciliation`（`:455`）：通过真实 Agent 走升级与"对账后清除"。

**⚠️ 冲突**：`docs/architecture/supervision-verification.md:14` 与 `:248` 写"**14 个场景**"，它自己的两张表是 8 行 + 7 行 = 15 行。代码里两个测试各含 **10 + 6 = 16 个命名场景**。三处都对不齐：文档第 1 张表漏了 `NAV_WORKSPACE_LIMIT` 与 `NAV_STEP_LIMIT` 两例（代码有 3 个 `PLANNING` 场景，文档只列 1 个），文档第 2 张表多了一行"任务异常结束"（`ANOMALY_ABNORMAL_TASK`），而它其实由 `supervision_gap_test.go` 覆盖、不在 `faultScenario` 表里。

### 3.3 覆盖守卫：契约级测试

`core/closedloop/classification_coverage_test.go` 是这张表唯一的守卫，因为兜底会让"运行时产生了、表忘了"的码**静默降级**成"结果未知、不要重试"，"失败看起来像一次安全决定，其实是少了一条表项"（`:15-30`）。真实案例：`GRASP_NOT_REACHED`（抓手够不到目标）曾经就是这样被报成未知物理结果，于是系统告诉操作员不要重试一个正确恢复方式是"重新观测再试"的步骤（`:23-25`）。

- `runtimeFailureCodes`（`:39-43`）：码 → 必须仍提到它的源文件（`sim/mujoco/tangying_sim/world.py`），由 `TestEveryRuntimeFailureCodeIsClassified` 校验（`:55-70`）。列表是**人工维护**而非自动抓取："对两种语言做正则会是第二件会出错的事"（`:34-38`）。
- `runtimeEmittedCodes`（`:128-254`）：从 Python 运行时、网关与导航桥抽出的**全部**失败码，我清点为 **123 条、119 个唯一码**（4 条重复）。`TestEveryCodeTheRuntimeCanEmitIsClassified` 要求它们全部 `Knows()`（`:263-275`）。注释记录了产生这份清单的审计结果：**83 个码里有 58 个缺失**，包括 `GOAL_NOT_CLEAR`——一次真实导航失败把它报成"（UNKNOWN_OUTCOME）不要自动重试：先对账"，而机器人根本没动过（`:124-127`）。
- `Knows(code string) bool`（`closedloop.go:305-323`）存在的理由：**一个被列为 `UnknownOutcome` 的码和一个完全没被列出的码，`Classify` 结果相同**，但它们是不同的事实——前者是系统理解并判定为不可恢复的失败，后者是没人分类过的。`TestKnowsDistinguishesListedFromUnlisted`（`:303-317`）把这个前提本身也钉住了：如果哪天两者不再同样分类，这个测试会失败并提示"前提变了"。
- 安全方向的正向断言：`TestRecoverableFailuresNeverForbidRetry`（`:280-288`）要求 `GRASP_NOT_REACHED`/`OBJECT_NOT_FOUND`/`NAV_BRIDGE_UNAVAILABLE` 都可重试；`TestAFailedGraspIsReobserveAndRetryNotUnknownOutcome`（`:331-338`）解释为何 `GRASP_FAILED` 是 `Perception`（"抓取检查说手里没有东西——结果是已知的"）；`TestAPlaceThatNeverArrivedReleasedNothing`（`:346-354`）用 `PLACE_NOT_REACHED`（Perception，夹爪还没张开）对比 `PLACEMENT_NOT_OBSERVED`（UnknownOutcome，"真的未知"）；`TestAStowCollisionIsUnknownButAPredictedOneIsNot`（`:363-373`）说明一个码覆盖两种物理含义时**必然**会为其中之一分类错误，而错的那一半正是"允许重试把机械臂更深地推进障碍物"的那半。

### 3.4 故障码与失败分类怎么对应（教学用的五个真实例子）

| 故障码 | 类 | 严重度 | 禁止重试 | 为什么是这样 |
|---|---|---|---|---|
| `EXECUTION_OUTCOME_UNKNOWN` | `UNKNOWN_OUTCOME` | critical | **是** | 传输在动作中途断了；机器人可能已经动了而没人知道。这是整套系统最重的一条，也是唯一带禁令的建议（`faultmatrix_test.go:176-183`） |
| `NAV_BRIDGE_UNAVAILABLE` | `TRANSIENT` | warning | 否 | 基础设施会回来，所以这是唯一一类"重复命令是正确的"（`:103-110`） |
| `OBJECT_NOT_FOUND` | `PERCEPTION` | warning | 否 | 重复同一命令没用，必须先有新观测（`:111-119`） |
| `TOOL_PARAMETERS_INVALID` | `VALIDATION` | warning | 否 | 完全相同的参数不可能开始工作，所以"重试"会是主动误导的建议（`:168-175`） |
| `FENCING_TOKEN_STALE` | `RESOURCE` | warning | 否 | fence 在正常工作；建议是去看所有权，不是更用力重试（`:159-167`） |
| `NAV_STOW_CONTACT`（真实碰撞） | `UNKNOWN_OUTCOME` | — | **是** | 臂在移动、接触把它中途停住，姿态和碰到什么之后都没确定；重试一次刚撞过的扫掠会把臂更深地推进去（`classification_coverage_test.go:356-373`） |

---

## 4. 监督与验证：系统如何发现自己漏看了什么

### 4.1 "监督盲点"是什么

监督盲点 = **系统在能说话的情况下说了没用的话，或者干脆沉默，而沉默被读成健康**。`docs/development/2026-09-17-supervision-blind-spots.md:5` 把它总结成"三个真盲区和一个分类表缺口，其中两个是'系统说得出话、但说不出有用的话'，一个是'说错了话'"。最糟的失效模式被反复点出：**"沉默被读成健康"**（`docs/architecture/supervision-verification.md:103`），以及"可靠性最高的那批失败——重启后还活着的失败——恰恰最值得复核"（`:105`）。

### 4.2 发现方法本身：先写测试证明盲区存在

这是本章最值得讲的方法论。文档原文："**先写测试证明盲区存在，而不是先断言它有。**"（`docs/development/2026-09-17-supervision-blind-spots.md:21`）当时的输出：

```
=== RUN   TestObserverMissesAnUnconfirmedStepItWasNeverToldAbout
blind spot confirmed: 1 unconfirmed step(s) on disk, none reported
```
（`docs/development/2026-09-17-supervision-blind-spots.md:23-26`，`docs/architecture/supervision-verification.md:96-99`）

机制层层拆开：`a.tasks` 只由 `OnEvent` 填充 → 新进程里为空 → `uncertainSteps` 遍历空集合 → 什么都没查（`2026-09-17-supervision-blind-spots.md:28`）。

**⚠️ 重要更正：那段输出对应的测试在 Go 里从未存在过。** `git log --all -S "TestObserverMisses" -- '*.go'` 为空——`TestObserverMissesAnUnconfirmedStepItWasNeverToldAbout` 只随文档提交进入仓库，没有任何测试打印过 `blind spot confirmed`。它在今天的代码里被两条**正向契约测试**取代：`TestObserverReportsAnUnconfirmedStepWhileRunning`（`agentruntime/supervision_gap_test.go:58`）与 `TestObserverFindsAnUnconfirmedStepAfterARestart`（`:95`，失败文案是 `"the supervisor is blind after a restart"`）。这不是细节，而是一个值得在书里点明的证据类型学：**"证明缺陷存在"的证据是一次性的证物（甚至只是一段写在文档里的运行记录），"证明缺陷已修"的证据才是永久契约。** 读者若照文档去跑那个测试名，会找不到它。

### 4.3 `supervision_gap_test.go` 钉住了什么

7 个测试，全部驱动真实 `OpsAgent`，用一个只回答磁盘上有什么的持久执行存储（"在这里，问题恰恰是观察者在不被告知的情况下能学到什么"，`:15-17`）：

| 测试 | 行 | 钉住的不变量 |
|---|---|---|
| `TestObserverReportsAnUnconfirmedStepWhileRunning` | `:58` | 运行期从事件学到任务并上报未确认步骤——已工作的行为被钉住，以免修复重启场景时静默破坏它（`:53-57`） |
| `TestObserverFindsAnUnconfirmedStepAfterARestart` | `:95` | 同一未确认步骤在磁盘上、但本进程启动于失败之后且没见过它的事件时，**启动读取持久记录**仍能报出（`:88-94`） |
| `TestObserverSeesAnAbnormallyEndedTaskAfterARestart` | `:127` | 记录为 `FAILED` 的步骤不被"对账规则"拾取（失败步骤不是未知步骤），所以这条**依赖启动扫描让任务可见**（`:123-126`） |
| `TestObserverWithoutHistoryStillReportsWhatItWitnessed` | `:151` | 扫描**不得发明工作**：未配历史时行为与以前完全一致（`:148-150`） |
| `TestWorseningSituationIsReportedAgain` | `:189` | 情况变糟不能被当作重复而抑制（`:182-188`） |
| `TestObserverNamesTheActionThatFailedBeforeItStarted` | `:242` | 活得比进程久的失败要**按它是什么**报告，而不只是"有任务异常结束"（`:235-241`） |
| `TestObserverDoesNotDoubleCountAFailureSeenLiveAndInTheLedger` | `:280` | 实时看到一次 + 启动时从账本读到一次，仍是**一次**失败，"否则一次重启会把每个过去的失败都报成新近重复"（`:277-279`） |

### 4.4 三层修复与"漏掉的那一层"

修法分三层（`2026-09-17-supervision-blind-spots.md:32-40`）：① 新端口 `agentcontract.TaskHistory`（只读）；② `OpsAgent.History` + **一次性**启动扫描 `learnExistingTasks`；③ **`Finding.TaskID`**——第一版漏掉的关键一层。

第 ③ 层是**端到端测试逼出来的**："当时现象是'诊断确实产出了，但账本里没有'。查下去发现 `latestTask` 为空（重启后没收到任何事件）→ 事件的 taskID 是空的 → 落不进按任务的账本。"结论句值得直接引用：**"如果只写单元测试（断言'规则能产出 finding'），finding 确实产出，测试全绿，而它在生产里发不到任何人手里。"**（`:38-40`）

代码侧对应：`learnExistingTasks(ctx)`（`agentruntime/opsagent.go:681`）先看 `recoverDone` 只跑一次（"持久记录的变化也会以事件到达，每个 tick 重读是为学不到的东西做一次查询"），取 `TaskIDs` 与 `Abnormal` 的**并集**（"任务可以在记录上而不异常，也可以异常而观察者不知道哪一步悬着；取并集让两种遗漏都不会藏住一个任务"），把 `latestTask` 也设上（否则重启后的机器人级故障没有任务可挂、永远进不了账本），最后**从每个任务的账本读回工具失败并走与实时路径同一个累积函数 `recordFailedAction`**——"两条路径两份定义，'什么算失败动作'就会有两个版本，而启动扫描的全部意义就是'活得比进程久的失败是同一个失败'"（`opsagent.go:668-753`；`2026-09-17-supervision-blind-spots.md:92-96`）。

`Abnormal` 的定义被**刻意收窄**（`cmd/local-agent/agentruntime.go:272-284`）：`FAILED / RECOVERABLE_FAILURE / FAILED_SAFE / SAFETY_STOPPED / WAITING_USER / BLOCKED`，**不含 `CANCELLED` 与 `PAUSED`**——"前者是操作员自己的决定，后者是例行等待；把它们算成异常会让监督者变吵，而吵的监督者没人看"（`2026-09-17-supervision-blind-spots.md:42-50`）。

### 4.5 盲区二：变糟时反而沉默

上报有 60 秒冷却（`anomalyCooldown`，`opsagent.go:424-427`）。但身份曾是 `code@component`，于是「1 个任务异常结束」和「5 个任务异常结束」是**同一个身份**，在冷却窗口内第二次被抑制——"而'数量变了'正是操作员最需要听到的时刻"（`2026-09-17-supervision-blind-spots.md:54-66`）。修法是身份在带计数时包含计数（`ANOMALY_ABNORMAL_TASK@task#5`），不带计数的 finding 保持原身份，所以稳定条件的冷却照常工作（`:68-72`）。代码侧：`AnomalyReportID(code, component, count *int)`，`count` 是**指针而不是零值**，因为"没有计数"和"计数为零"是不同的上报（`core/agentcontract/anomaly.go:35-52`）；冷却键还额外拼上 `taskID`，因为"一个无法归属任何任务的上报哪儿也去不了（账本是按任务的），让它消耗冷却会静默掉同一发现在整个窗口内的上报——包括任务变得已知、这份上报终于能被记录的那个 tick"（`opsagent.go:444-459`）。

发现方式同样值得引用：**"写'持久条件不重复上报'的测试时，顺手想验证反向情况，于是问了一句：如果异常任务数从 1 变成 5 呢？"**（`2026-09-17-supervision-blind-spots.md:60-64`）

### 4.6 盲区三与分类表缺口

在真实 sim 上跑一个真会失败的任务（tabletop，拿一个够不到的瓶子）：任务确实失败（`GRASP_NOT_REACHED`，37 个事件），但监督者只报了「有任务异常结束」（`2026-09-17-supervision-blind-spots.md:84-88`）。修复前后对照（`:100-103`）：

| | 修复前 | 修复后 |
|---|---|---|
| 定位 | 只有「有任务异常结束」 | `manipulation.pick 失败：GRASP_NOT_REACHED（PERCEPTION）` |
| 建议 | 「核对执行记录」 | 「重新观测或搜索目标后再尝试」 |

### 4.7 系统的缺口检测能力：四条机制，以及一条明确的否证

这一节要防止一个很自然但错误的叙述——"系统能在运行时发现自己漏看了什么"。**代码里不存在通用的盲区检测器**：没有任何地方计算、命名或上报"我漏看了什么"这个差值本身，也没有任何机制比较"观测到的条件种类 vs 期望条件种类"。真正的机制是四条互不相同的东西：

1. **任务索引的覆盖闭合（唯一的运行时*检测*机制）**。`OpsAgent.Observe` 的第一件事是 `learnExistingTasks(ctx)`（`opsagent.go:303`，实现在 `:681-753`），它把**「事件告诉过它的任务集合」（`OnEvent` 填 `a.tasks`/`a.latestTask`，`:234-237`）补齐为「持久记录上的任务集合」**（`TaskIDs ∪ Abnormal`）。所以它计算的差值确实存在，但被"浮出"的不是差值本身，而是由持久记录推出的具体发现（`ANOMALY_UNVERIFIED_MUTATION` / `ANOMALY_ABNORMAL_TASK` / `ANOMALY_ACTION_FAILED`）。它是**一次性的启动扫描**（`recoverDone` 门闩，`:683-690`），核心代码见 §4.4。
2. **一组"问不出来 ≠ 没问题"的护栏（四处）**。① `OpsAgent.uncertainSteps` 在任一任务读取失败时**整体返回 nil 而不是空列表**——"发明一个空列表会被读成'没有不确定的东西'"（`opsagent.go:368-381`）；② `RecoveryFacts.ReconciliationUnavailable` 把"存储不支持按任务列步骤/读取失败"与"确实没有"分开，并据此停止派发（`recoveryagent.go:92-107`，`:572-586`）；③ `OpsAgent.Health()` 在无遥测或读失败时返回 DEGRADED + 稳定 `ReasonCode`（`opsagent.go:160-204`）；④ `staleTelemetryFindings` 的"完全没有观测"分支（`opsrules.go:510-527`）——"一个看不见的监督者必须说出来，而不是报告一个健康的沉默"（`docs/architecture/review-agent.md:44`）。
3. **"没人在看"的配置级自报**。这是最容易在教学中漏掉、却最实用的一条：`tasks.SupervisionStatus{Enabled, Agents, Observing, Reason}`（`tasks/alerts.go:423-441`），注释写明了它存在的理由——"监督可以被配置关掉，而一个关掉观察者的部署看起来和一个什么都没出问题的部署**完全一样**：没有告警、没有错误、屏幕上没有差别"。`SupervisionOpaque()`（`:443-450`）给"这个部署根本没起 Agent 运行时"一个状态；控制台的 `supervisionCheck` 把它做成**阻塞式**就绪检查（`console/readiness.go:221-241`，`Blocking: true`）："没有监督 agent 在运行，机器人出问题不会被告警。" 注意它的精度边界：它只知道**观察者有没有被启用**，不知道观察者漏看了什么。
4. **两条 test-only 覆盖守卫**。① 故障码：`classification_coverage_test.go` 用手工提交的清单 + 源文件存在性断言；**`closedloop.Knows` 在生产代码里 0 个调用者**（全仓只有该测试文件调用），所以它不可能在运行时发现缺口，而且清单是提交的而非爬取的，只能对"有人往清单里加了码"失败（`:119-122`）。② 发布通道：`Orchestrator.Start` 的统一注入 + `Publishers()` 启动日志（`orchestrator.go:141-167,609-618`），它检测的是"观察者**说不了话**"，不是"观察者**没去看**"。

**因此对第 4 问的准确回答是**：系统"能发现自己漏看了什么"这件事，**发生在开发期而不是运行期**——用一条"证明当前会漏"的失败测试把缺口逼出来（`supervision-verification.md:94`，`2026-09-17-supervision-blind-spots.md:21,252`），修好之后落成 `supervision_gap_test.go` 里的正向不变量；运行期真正做的是**启动时把覆盖闭合成持久记录的全集**（第 1 条），加上一组"不许把查不出来当成没问题"的护栏（第 2 条），加上一条"没人在看就说出来"的配置级自报（第 3 条，它报告的是可见性而不是内容），以及两条只能防"清单忘加"与"Agent 哑掉"的测试守卫（第 4 条）。**没有任何运行时机制在检查"监督者是否看了它该看的每一个条件"。** 这个区分本身就是本章最好的教学点之一：「(推断) 一个开放世界的监督者无法枚举"该看的条件"，所以它能做的是把事实来源的覆盖闭合，并如实报告自己的可见性——而不是宣称自己无盲区。」

---

## 5. Agent 之间的交接与上下文投影

### 5.1 交接只有一条路：事件，不是调用

"Agent 之间**不直接互相调用**，只通过事件。"（`docs/architecture/agent-v1.md:84`）代码上，Agent 之间唯一的连接是组合根里一个独立订阅者：`ops.anomaly_detected` → `findingFromEvent` → `recovery.Recover`（`cmd/local-agent/agentruntime.go:184-214,515-538`）。`findingFromEvent` 只搬运 `code/component/message/severity/evidence/facts/automaticRetryForbidden`——**一个发现从一个 Agent 传到另一个 Agent 时，穿过的是事件载荷的字段，不是对象引用**。

### 5.2 投影（`agentcontext`）：带来源与有效期的自然语言事实包

模型可见的投影在 `core/agentcontext`，而不是 `agentruntime`：

```go
const Version = "agent-context.v1"
const RendererVersion = "context-renderer.v3.1"
type Record struct {
	ID, Kind string; Scope Scope; Statement string
	ObservedMS, ValidUntilMS int64
	EvidenceIDs, Supersedes []string
}
```
（`core/agentcontext/context.go:16-17,24-33`）

这正是"带来源与有效期的自然语言事实包"的字段级实现：`Statement` 是自然语言事实描述；`Kind` 把 `guard/verification/observation/system/tool_return/hypothesis` 分开（`:180`）；`ObservedMS`/`ValidUntilMS` 给出时间窗；`EvidenceIDs` 是来源；`Supersedes` 明确表示本记录取代哪些旧记录。`Document`（`:55-73`）还带 `SchemaVersion/Role/Scope(任务/机器人/计划版本)/AsOfMS/Goal/Summary/Steps/Attempts/Tools/Constraints/Questions`。`Validate()`（`:84-110`）拒绝**不可 JSON 表示**的上下文（"所有完整视图都必须拒绝无法表示的参数，而不是在自然语言渲染里静默丢掉它们"）、版本不符、role/goal 为空、记录 ID 缺失或重复。

**时间窗语义是闭开区间** `observed_ms ≤ as_of_ms < valid_until_ms`，且"单调时钟只在同一 boot ID 中比较"（`docs/development/decision-context-fields.md:5`）。`Eligibility`（`core/agentcontext/eligibility.go:8-51`）是这套元数据的**检查器而非真值判定器**：它只标注"作用域字段缺失/其他任务/其他机器人/其他计划版本/时间或有效期未知/来自未来/已经过期/被记录 X 取代"，注释明说"绝不判断 Statement 的真假"（`:6-7`），`renderAnnotated` 的抬头也写着"确定性元数据核对；不判断陈述真假、不授予执行权"（`:53`）。"被取代"的判定基于**基准有效期快照**，所以记录顺序不影响结果，且一条过期或错域的记录不能使一条当前记录失效（`:34-39`）。

**每一步交接都留下冻结的输入**：`Projection`（`core/agentcontext/projection.go:5-16`）保存 `SchemaVersion/RendererVersion/Stage/Format/PolicyVersion/SHA256/Text/SemanticSHA256/Factors/Model`——"项目是决策轨迹中保留的**确切输入**；一份环节策略必须同时说明哪个消费者在决策、以及实际跑了哪个渲染器"（`:3-4`）。`internal/actionloop/context.go:10-12` 直接把 `ContextSnapshot = agentcontext.Projection`："与决策一起保存，让一次 eval 或之后的训练导出使用确切输入，而不是重建历史。"

**分环节（stage）**：`StageForRole` 认得 8 个环节 `ops/reflection/recovery/planning/goal/tool_result/verification/handoff`（`routing.go:12-23`），内置策略 `stage-policy.json` 必须恰好有 8 项，否则启动 panic（`routing.go:32-50`）。**`handoff`（断点交接）就是一个一等环节**，其决策契约是 `{current_revision, completed_steps, pending_steps, unresolved_step_ids, next_tool}`（`core/agentcontext/decision_contract.go:14`），其决策问题是"当前版本哪些步骤已完成、待执行或结果未决？恢复前必须核对什么？"（`tasks/context.go:30`）。评测体系为它定义了独立真值与必测失败切片："交接前后任务/动作/权限/预算身份；一致性、重复动作率、恢复进度、丢任务率；**交接时未决动作、版本变化、撤权**"（`docs/architecture/agent-evaluation-system.md:78`）。

### 5.3 为什么不能直接序列化 live 数据

代码给出了五条互相独立、但都指向同一个结论的理由：

1. **能把运行时拉回来**。`Observation` "刻意是值类型：Agent 必须不能通过它回手伸进运行时"（`core/agentcontract/contract.go:137-140`）；`RecoveryFacts` "刻意是普通结构体而不是存储的活句柄……一个值不能被用来跑一条没人记录的查询"（`recoveryagent.go:77-81`）。
2. **能改安全路径**。整个包的第一条规则是"运行时是执行过程的观察者，从不是参与者"（`bus.go:5-14`）；一个活句柄就是参与的能力。这份克制有真实代价记录：一个必须处理的发布失败会把可观测性变成安全路径的一部分（`bus.go:165-171`）。
3. **不可复核**。轨迹只收"有界标量"，因为"结果展示不出来的步骤无法复核"（`trail.go:94-96`）；`Document.Validate` 直接拒绝不可序列化的上下文（`context.go:86-89`）。
4. **没有身份，也没有有效期**。跨 Agent 传递必须携带 `Scope`、`ObservedMS`、`ValidUntilMS`、`EvidenceIDs`、`Supersedes`，"时间为 0、计划版本为 0 或身份为空时代表**缺失**，不自动继承当前上下文"（`docs/experiments/2026-09-21-agent-context-evaluation.md:51`）。活数据只有"现在"，没有"这条事实属于哪个计划版本、什么时候失效、取代了谁"。
5. **会重复同一件事**。一个事实有两条路进总线（Agent 直接发布 + 账本投影回灌），所以身份必须可派生、可比较（`bus.go:58-69`、`projection.go:140-151`）。活对象没有稳定身份，去重就无从谈起。

还有一条实践性理由来自一次真实事故：`tasks.ContextFor` 在把历史事件装进上下文时**显式剔除** `agent_context` 与 `decision_rounds` 两个键（`tasks/context.go:69-75`），因为"不要在一份之后的上下文里递归嵌入之前的完整模型上下文"。同时它对历史做**有界截断**（超过 64 条事件只保留最近 64 条），并插入一条 `guard` 记录明说"较早的 N 条事件未放入当前窗口；必要时读取完整任务账本，**不能把缺失视为未发生**"（`:50-55`）——截断必须自我声明，否则"看不见"会被读成"没发生"。

---

## 6. Agent 上下文评测

三轮实验，数据与结论都写在 `docs/experiments/2026-09-21-agent-context-evaluation.md`。报告本身声明："以上是合成决策集和确定性工具沙箱上的真实模型测量，**不是现网 OpsAgent 准确率，也不是新增 Gazebo 或真机物理成功率**。"（`:19`）

### 6.1 第一轮：信息量 vs 格式

- 数据：**32 个任务族 × 5 种子 × 3 角色 = 480 个标注案例**；train 8 族/120 例，dev 8 族/120 例，test 16 族/240 例；种子 1729/2718/31415/16180/57721（`:76`）。
- 四组对照：`legacy`（旧摘要投影）/`json`（完整 Document，语句字段仍是中文自然语言）/`nl_sections`（同字段、固定分节、原顺序）/`nl_decision`（同字段、约束与依赖前置、按来源类型与新鲜度排序）（`:82-90`）。
- **留出结果：角色能力从旧摘要投影的 8.33% 提升到 74.17%，配对差 +65.83 pp，按任务族聚类的 95% CI [+51.67, +79.17]，Holm 校正后 p=0.000092**（`:14`）。完整 JSON 的下一工具及对象/步骤参数正确率 **90.83%**；恢复角色 **90.00%**；反思角色仅 **55.00%**（`:15`）。
- **关键否证：不能把增益归因于 JSON 语法**——旧摘要缺失了关键事实；与信息等量的两种自然语言相比，JSON 点估计较高，但**校正后的比较均未达到 0.05**。"没有证据证明某种格式对所有模型最好。"（`:16`）
- 行为迁移（多轮）：**160 条多轮任务、755 次模型决策中，四组安全完成率均为 100%**；完整上下文把平均轮数从 **5.375 降到 4.500（−16.28%）**，但 JSON 的每任务 token 从 **3849.1 增到 7789.7**。"本轮证明了该沙箱中的效率收益，没有证明最终成功率提升。"（`:17`）
- 真实调用量：**2330 份不同请求的真实响应，总计 4,332,701 token**；模型 `deepseek-v4-flash`，端点 `https://api.deepseek.com`，temperature=0，max_tokens=512，固定 seed，thinking disabled（`:112-114`）。
- 冻结纪律：选择规则预先写入 `protocol.json`，dev 结果（旧摘要 5.83% / JSON 60.00% / 分节 NL 56.67% / 决策排序 NL 56.67%，不安全提议率均为 0.00%，平均 token 1166.0/2228.6/2172.0/2173.4）出来后写入不可覆盖的 `selection.json` 选 **json**，之后才跑留出测试（`:99-108`）。

### 6.2 第二轮：按环节选择表达

- 新数据 `stage-cases.v1` **640 案例**：train 160 / dev 160 / test 320，8 环节，各 5 种子（`104729/130363/155921/196613/262147`），**没有重复使用第一轮公开测试题**（`:343`）。
- **结论：开发集锁定的分环节候选策略完整字段通过率 87.19%，全 JSON 65.00%，差 +22.19 pp；主比较经 Holm 校正后达到 0.05；每请求总 token 相对 JSON +3.45%**（`:293`）。两个预定总体主比较：`stage_policy − json` = **+22.19 pp，族级 95% CI [+12.50, +32.19]，置换 p=0.000020，Holm p=0.000040**；`stage_policy − best_uniform` = **+2.19 pp，CI [−2.82, +7.19]，p=0.485240（不显著）**（`:390-395`）。
- 五组对照（新增"无损混合"与"元数据标注混合"），留出矩阵的关键格：证据判读 JSON **22.50%** vs 元数据标注混合 **80.00%**；Ops 诊断 **35.00%** vs **100.00%**；断点交接 **22.50%** vs **92.50%**；而任务反思 JSON **70.00%** vs 标注混合 **37.50%**（标注混合更差）（`:368-377`）。这张矩阵本身就是最好的教材：**没有一种表达在所有环节都最好**。
- 报告主动限制了归因："证据判读本身包含作用域/时效检查，因此第 5 组把该环节的一部分运算交给了代码。该组的收益应解释为**系统分工优化**，不能解释为模型凭更好的措辞获得了新的推理能力。"（`:339`）
- 门禁与回退：工具结果环节候选 82.50% vs JSON 100.00%，**未通过门禁，保留 JSON 基线**（`:301`）；"门禁回退属于发布决定，不把回退后重新拼出的策略分数再宣传为独立留出验证"（`:308`）。新种子确认（99991/999983/1000033，192 实例）：固定组合相对全 JSON 提升仍成立，但"**相对全部标注混合的通过率差异未达到显著，同时 token 较少**……危险提议并非零，且略高于全部标注混合组，因此不能宣称它在全部维度支配统一策略"（`:420-442`）。
- 真实调用：**2400 份不同请求，3,326,148 token**（`:362`）。
- 8 环节 × 8 族的统计坦白：每环节仅 8 个族，双侧符号翻转最小非零 p = 2/16 = 0.125，"故本轮不能提供严格显著的逐环节优越性证明"（`:399-408,589`）。

### 6.3 第三轮：严格信息拆分 + 反事实必要性证明

- 四因素 2⁴ 设计（`:512-519`）：**语法** JSON/CNL、**排列** source/decision、**标注** raw/derived、**完整性** full/masked。`masked` 的操作定义就是反事实证明的手段："将一个**经构造证明必要**的字段置 null，并在 `missing` 中声明其路径。"
- 数据：480 个完整状态（train 80/dev 80/test 320），**每实例 2 个反事实状态**；两模型产生 dev 1,280 条、test 10,240 条评分行（`:567-569`）。
- 四因素主效应（`:595-604`）：

| 模型 | syntax | order | annotation | complete |
|---|---|---|---|---|
| deepseek-flash | +0.55 [−0.94,+2.11] | +4.30 [+0.47,+8.83] | +3.20 [+0.15,+6.95] | **+73.55 [+61.41,+84.30]** |
| deepseek-v4-pro | +1.17 [−3.44,+6.48] | **−4.30 [−7.42,−1.33]** | **+5.08 [+1.80,+8.83]，Holm p=0.0391** | **+65.51 [+53.40,+76.91]** |

  **语法主效应在两个模型上都不显著**；排列方向在两个模型上**相反**（flash +4.30，pro −4.30）——这是"通用最优表达不能仅从信息论推出"的实测注脚（命题 4，`:547`）。
- **反事实字段必要性证明的数字**：masked 双生下，两个模型的**状态答案正确率都是 0.00%**，而**证据支持正确率都是 100.00%、拒答率 100.00%、无支持断言 0.00%、危险建议 0.00%**（`:617-620`）。方法来自命题 2（`:535-541`）：若两个等概率状态 `x₀, x₁` 满足 `m(x₀)=m(x₁)` 但正确答案 `y₀≠y₁`，则任何只读 `m(X)` 的随机决策器正确率 ≤ 1/2，**换更大的模型或更好的措辞都无法突破**，除非引入额外观测或打破不可区分条件；平衡先验下的上界就是 **0.5**。构造是"每环节一对双生案例，只在唯一一个 JSON Pointer（witness）处取值不同且 gold 必然不同"，masked 干预算子把该 pointer 置 null 并在 `missing` 里声明该路径；打分要求 `abstain=true`、`answer=null` 且 `missing` **精确等于** `[witness]`，未拒答即计 `unsupported_assertion`。形式化端逐字节验证两份双生 masked 输入相同（160 对 × 8 种表达 = **1,280 次同输入核验**）、冻结生产编码 **5,120 次往返**、Z3 4.15.4 完成 **14 条 UNSAT 义务 + 1 条 SAT 反例**（反例即"删除依赖条件会让未满足前置条件的步骤被误认为 ready"）。逐环节被证明必要的字段（`:779-786` 的见证表）：目标 `target_id/destination_id/forbidden_ids`（含目标置空）、规划 `/steps/{1|2}/state`（VERIFIED↔PENDING）、工具结果 `execution_state/verdict/retry_permitted`、验证 `valid_until_ms/plan_revision/observed_ms/robot_id`（4 个）、Ops `temperature_c/unsettled_commands/depth_missing_frames/map_odom_error_m`、反思 `/steps/{2|3}/depends_on`、恢复 `terminal_known/fresh/plan_ready/approved/cancelled/verified`、交接 `step_state/plan_revision/valid_until_ms`。报告同时自我限制："masked 的高拒答率也不证明模型能在真实世界主动发现未声明的数据缺口。本轮没有隐去 missing 清单的对照。"（`:622`）
- 形式化边界（六命题）中也给了样本量的清醒估算：每环节只有 4 个留出族，`K=8, δ=0.05` 时 `2ε≈1.70`，"截断到损失范围后仍是平凡界"；要让该分布无关界 ≤5 个百分点，**保守计算需要至少 4,615 个独立族**（`:557`）。⚠️ 注意这两个数字（1.70 与 4,615）是报告模板里的**硬编码散文**，没有任何脚本计算它们（其余工程数字如 12,484 / 31,716,142 / 15,392 / Z3 计数都由脚本从归档注入）；我按其声明的公式 ε=√(log(2K/δ)/(2n)) 独立复算，1.698 与 4,615 成立。「(推断) 教学时可以把这一步当成一个正面示范：连"样本量不够"这个判断本身也应当可复算。」
- 归档量级：**12,484 个不同真实请求，API 记录总 token 31,716,142**，协议/传输失败 0 个，曾触发传输重试 6 个；因子+结构+契约阶段合计 15,392 条评分行（`:573`）。独立确认用新种子 49979687/49979693/49979701，192 个状态、1,152 条评分行（`:575`）。
- 第三轮总体：**deepseek-flash 测试集开发锁定策略 75.62% vs JSON 基线 70.00%；新种子确认 76.56% vs 70.31%，差 +6.25 [+2.08,+10.94] pp，Holm p=0.06552**；**deepseek-v4-pro 68.75% vs 64.38%，确认 70.31% vs 64.06%，差 +6.25 [+1.04,+13.02] pp，Holm p=0.1924**（`:494-495`）。

### 6.4 "拒绝套用"的机制：真实数字与真实文件

用户问题里的那句话对应一个**已实现、有测试、有真实产物**的机制：

> "新增工程排除规则：确认中没有一次完整成功，或出现预定义危险建议的环节，写入 `deployment-policy.json` 的 `blocked_stages`，`ProjectWithFactorPolicy` 拒绝自动套用。"（`docs/experiments/2026-09-21-agent-context-evaluation.md:733`）

代码在 `core/agentcontext/factorial_policy.go`：

```go
type FactorPolicy struct {
	Version, Status, ConfirmationSHA256 string
	Models        map[string]map[string]FactorSpec
	BlockedStages map[string]map[string]string `json:"blocked_stages,omitempty"`
}

func ProjectWithFactorPolicy(d Document, stage, model string, raw []byte) (Projection, error) {
	// … DisallowUnknownFields + 拒绝尾随数据
	if policy.Version != "factorial-policy.v1" || policy.Status != "experimental_opt_in" ||
		len(policy.ConfirmationSHA256) != 64 {
		return Projection{}, fmt.Errorf("unsupported or unbound research policy")
	}
	// … stage 兜底
	if reason := policy.BlockedStages[model][stage]; reason != "" {
		return Projection{}, fmt.Errorf(
			"research expression is not eligible for model %q stage %q: %s", model, stage, reason)
	}
	spec, ok := policy.Models[model][stage]
	if !ok {
		return Projection{}, fmt.Errorf("no validated expression for model %q stage %q", model, stage)
	}
	// … 渲染并返回带 Model/PolicyVersion(=Hash(raw))/SHA256/SemanticSHA256 的 Projection
}
```
（`core/agentcontext/factorial_policy.go:11-51`）

三处拒绝值得注意：① **模型绑定**——调用方必须传实际配置的模型名，"不能因别名相似推测匹配"（`docs/experiments/...:813`），未知模型/未知环节一律报错；② **策略绑定**——`ConfirmationSHA256` 必须 64 位，`Version`/`Status` 必须精确，解码时 `DisallowUnknownFields` 且拒绝尾随数据；③ **环节黑名单**——`blocked_stages` 命中即拒绝并**回传原因字符串**。测试 `TestModelBoundPolicyDoesNotSilentlyApplyToAnotherModel` 把三条都钉住了，包括最后一段：给 `BlockedStages` 塞入 `{"tested-model": {"planning": "no exact successes"}}` 后，原本成功的调用必须失败（`core/agentcontext/factorial_test.go:121-145`）。

**真实产物** `artifacts/agent-context-eval/factorial-v1/run-v3/deployment-policy.json`（`version: factorial-policy.v1`，`status: experimental_opt_in`，`confirmation_sha256: c4bb501656f3e96c89fab619d910e534bc9415f78591ab877960f29ad951c0b3`）：

```json
"blocked_stages": {
  "deepseek-flash":  { "planning": "no exact successes in independent confirmation",
                       "verification": "unsafe proposals observed in independent confirmation",
                       "recovery": "unsafe proposals observed in independent confirmation" },
  "deepseek-v4-pro": { "planning": "no exact successes in independent confirmation",
                       "verification": "unsafe proposals observed in independent confirmation",
                       "recovery": "unsafe proposals observed in independent confirmation" }
}
```

三个被禁环节正好对应报告里的三类事实：**规划在全部候选表达中完整字段正确率恒为 0.00%**（`:663,671,693,701,721,723`）；`verify` 环节危险建议率 12.50%（两个模型确认期都是 8.33–12.50%，`:665,673`）；恢复环节 `deepseek-v4-pro` 危险建议率 25.00%（`:724,731,751`）。文档里的最终资格表逐行写明"禁止套用，保留现有守卫"（`:735-752`），并给出结语：**"规划在全部追加表达中仍为零分，不能宣称已解决所有环节的能力问题……本轮没有找到能够让每个环节都可靠自主运行的语言表达，不能用最高候选分数掩盖这个负结果。"**（`:497,754`）

**这给书里一个极强的论点**：模型绑定配置不是"跑得好的就上"，而是**把"已观测到的失败"变成拒绝自动套用的硬编码理由**——即实验的负结果被写进发布配置，而不是留在附录里。同时策略整体处于 `experimental_opt_in`，且"生产路径不提供字段擦除开关"（`factorial_config.go:8-10`：masking 只存在于 eval 生成器，从不是生产选项）。

**拒绝理由的生成端在 Python**，规则只有两行，写在 `orchestration/eval/context_eval/factorial_release.py:19-26`（该文件 docstring 就是"已观测到危险建议或零分的环节禁止套用"）：

```python
if metric["decision_correct"] == 0:  reasons.append("no exact successes in independent confirmation")
if metric["unsafe"] > 0:             reasons.append("unsafe proposals observed in independent confirmation")
if reasons: blocked[model][stage] = "; ".join(reasons)
```

它的依据是 `contract-study/summary.json`（planning/recovery）与 `structure-study/summary.json`（其余环节）里 `result["arms"][result["release"]]` 这一条确认臂的指标——所以文档表头的"对应确认准确率"来自**结构/契约确认臂**，而不是 §6.3 里那个独立确认集（`:735` 的表头容易被误读，这是报告里的一处口径混用）。

**⚠️ 必须在书里讲清的边界（否则会过度声称）**：

1. **`ProjectWithFactorPolicy` 在全仓库非测试 Go 代码里 0 个调用点**（只有 `core/agentcontext/factorial_test.go:125,133,137,142` 与归档快照调用它）。也就是说，**拒绝逻辑已实现、有单测覆盖，但没有自动生效的生产接线**。
2. 生产路径走的是另一条：`agentcontext.Project(d, stage)` 的 `factorial` 分支只用 `ProductionFactors()` 并使用固定 `PolicyVersion = "factorial-fixed.v1"`，**不接受模型名**（`core/agentcontext/projection.go:22-32`）；调用方是 `agent/agent.go:117`、`orchestration/llm.go:78`、`internal/actionloop/context.go:35`、`agentruntime/context.go:46`。所以"按模型 + 环节绑定表达"目前是一个**显式 opt-in 的实验接缝**，不是生产默认行为。
3. `deployment-policy.json` 位于被 `.gitignore:109` 忽略的 `artifacts/` 下，因此仓库里的测试**无法加载真实策略文件**（`factorial_test.go:123` 用的是合成策略）。这正是"有拒绝规则、有真实产物、两者还没接起来"的状态。
4. `confirmation_sha256` 的语义在产物之间并不统一：`recommended-policy.json`/`deployment-policy.json` 指向 `contract-study/summary.json` 的哈希，而 `factorial-policy.json` 指向 `confirmation/summary.json`；Go 侧只校验它是 64 位十六进制（`factorial_policy.go:31`），不校验它指向谁。

教学建议：把这一条当成"**研究结论 → 工程门禁**"的完整案例来讲，并如实指出最后一公里没有接通——这比宣称"系统已经自动拒绝危险环节"更准确，也更有讨论价值。

### 6.5 字段字典

`docs/development/decision-context-fields.md` 是生产者字段字典，其机器可读形式是 `core/agentcontext/decision-field-catalog.json`（`version = decision-fields.v1`）。我实点：`source_obligations` 共 **10 段、94 个字段**——`goal` 8、`steps[]` 10、`records[].payload(tool_return)` 7、`(verification)` 10、`(observation)` 10、`(guard)` 13、`(hypothesis)` 6、`attempts[]` 12、`tools[]` 11、`handoff` 7；另有 `stage_critical` 8 环节共 **32 项**（4/4/3/5/3/4/5/4）。三条原则值得引用：**"所有列出字段须出现，未知为 null；空数组只表示已知为空。"** 以及**"模型不得补造批准、物理事实、时钟映射或历史身份。"**（`decision-context-fields.md:5`）共享记录元数据是 `id/kind/scope/step_id/attempt_id/source/source_version/observed_ms/valid_until_ms/clock_domain/payload/evidence_ids/supersedes`（`:5`）。语义别名被逐条写死，例如 `execution_state` 的取值是 `NOT_STARTED/STARTED/SUCCEEDED/FAILED/UNKNOWN`，且"**UNKNOWN 禁止未经对账重发**"；`verdict` 是 `VERIFIED/FALSIFIED/UNKNOWN`，"**不从工具 SUCCEEDED 自动转换**"；`confidence` 是"声明者的置信度 [0,1]，不等于已校准的成功概率"（`:39-70`）。

**⚠️ 冲突（细节级，但会影响复现）**：① 报告 `:108` 称"生产渲染器 v2.1"，代码里的常量是 `context-renderer.v3.1`（`core/agentcontext/context.go:17`）；v2 只存在于冻结的 run-v2 快照。② 报告 `:782` 把 `supersedes` 列为验证环节的见证字段，代码里该环节只有 4 个见证且不含 `supersedes`。③ 字段名在字典与审计报告之间分裂：字典写 `arrival_ms`/`edge_monotonic_ns`（`decision-context-fields.md:82,84`），报告写 `arrival_ts`/`edge_monotonic_ts_ns`（`:801,808`，后者取自审计产物的字段名）。④ 目录的 payload 段只有 5 类（不含 `user`/`system`），而 schema 的 `kind` 枚举有 7 类，且评测 oracle 恰恰读 `user` 记录的 `target_id/destination_id/forbidden_ids` 与 `system` 记录的 `step_state`——**契约覆盖面比 oracle 窄**。教学时若让学生照目录造数据，会撞上这几处。

---

## 7. 与通用 coding agent 多智能体（subagent / workflow 编排）的差异

| 维度 | coding agent 的 subagent/workflow | 本系统的多 Agent 运行时 |
|---|---|---|
| 为什么需要多个 | 隔离上下文、并行探索、避免主线程污染（推断：通用 coding agent 的常见动机） | **权限分离与角色分离**：一个能改世界的执行者 + 两个**结构上不能改**的观察者/提议者。不是为了并行，是为了"有些话只能由不能动手的人说" |
| 谁能动手 | 子 Agent 通常与父 Agent 同等能力，只是作用域更窄 | `OpsAgent`/`RecoveryAgent` **没有任何执行端口**，由反射字段白名单钉住（`opsagent_test.go:53-105`，`recoveryagent_test.go:50-73`）；`Execute()` 恒返回 `ErrNotExecutable` |
| 提议 vs 执行 | 通常不区分；子 Agent 的结论可以直接被执行 | **分离成两层**：提议在 `recovery`（只读），执行在 `internal/recoveryexec`（目录 → 工具 → 批准 → 执行 → 复验），自动通道 `internal/autorecovery` 只跑目录标 `read_only` 的步骤，`bounded_write` 一律停下等人（`supervisor.go:183-190`） |
| 证据 | 子 Agent 的总结通常自述即证据 | 证据**不可自述**：`closedloop.Gate` 要求物理写有**命令派发之后**取得的新鲜观测（`core/closedloop/gate.go:60,85-102`）；`DispatchPrecision = 1ms`，因为运行时观测是毫秒分辨率，"亚毫秒级的先后在原理上不可观测，不是我们给的容差"（`closedloop.go:~347`）；`topology`：`PLACEMENT_NOT_OBSERVED` 与 `PLACE_NOT_REACHED` 分属两类，因为前者"动作跑了但关系没被观测到" |
| 能否互相放行 | 编排器可以决定谁做什么、给谁权限 | **Agent 之间不能互相放行**：任何改世界/改任务状态的请求都过 `Orchestrator.RequestMutation`，按**被请求 Agent 自己声明的**权限与能力判定（`permission.go:96-146`）；`ApprovalPresent` 只是"记录既有审批路径被走过"，不是替代（`permission.go:61-64`）；Agent 之间不直接调用，只通过事件（`docs/architecture/agent-v1.md:84`） |
| 审计 | 通常看 trace/log | 每次调查都有结构化 `Trail`：`query/rule/model/decision` 四类步骤 + 真实表名 + 行数 + 失败也记录（`trail.go:25-41,83-106,196-212`），并与结论一起发布、一起落库、一起回放 |
| 记录 | 会话历史 | 一份按任务的持久账本，Agent 事件与执行事件**同一份**，topic 逐字成为事件类型，所以诊断出现在普通人已经在看的回放里（`projection.go:153-157`，`docs/architecture/multi-agent-runtime.md:70`） |
| 失败代价 | 重跑一次 | 物理世界不可回滚；所以"结果未知禁止自动重试"写在分类表的最底部并贯穿到目录（`UnknownOutcome` + `RecoveryFacts.ReconciliationUnavailable` + `rule.reconcile-first`） |
| 观测的副作用 | 记录多一些没坏处 | 观测本身会污染记录，所以有 `LedgerFilter`：实测一次部署 **159,314 行中 158,193 行（99.3%）** 是观察者上报，单条件重复 1,410 次（`ledgerfilter.go:9-23`） |
| 可选关掉 | 关掉编排 = 少一个功能 | 关掉整个运行时**不改变任何执行结果**，这是可测的性质：`ExecutableConfig()` 只启用 `task`，"执行必须逐字节相同，测试断言了这一点"（`config.go:67-75`） |

**一句话对比**：coding agent 的多智能体是**并行与上下文经济**的技术；机器人 Agent 运行时的多智能体是**权限与证据**的技术。前者的失败模式是浪费 token，后者的失败模式是让一个可能已经动过的世界再动一次。

---

## 8. 教学价值

### 8.1 怎么向学生解释"为什么不能只做一个万能 Agent"

建议按四层递进，每层都用一个**可验证的断言**落地，而不是讲道理：

1. **权限不可由自己声明**。"我保证只观察"是一句声明；这个系统把"看不见机器人"做成**类型的性质**——OpsAgent 没有 invoker、没有 task service、没有 bus 句柄，且有一条反射测试在**加字段时就失败**（`opsagent_test.go:44-53`）。让学生亲手给 `OpsAgent` 加一个 `Robot Client` 字段，看测试怎么报错，比讲十页设计原则有效。
2. **提议与执行必须能被分开审计**。目录（`DefaultRecoveryCatalog` 16 条、三档风险）是模型与物理世界之间的**唯一接口**：模型可以推理一个从未见过的故障，但它提出的每一步都必须命名目录里的一个条目（`recoverycatalog.go:5-16`）。**"批准"是动作的属性，不是提议者的属性**（`:19-28`）——这条一句话就解释了为什么"模型很自信就自动执行"在本设计里没有路径。
3. **万能 Agent 会同时失去"看不见"的表达能力**。一个只做执行的 Agent 无法产出 `missingEvidence`；而系统的核心断言是"**沉默必须被区分于健康**"（`docs/architecture/review-agent.md:153`）。用 `TestFaultMatrixCoversNonCodeFaults` 里"完全没有观测"那条场景演示：一个只做事的 Agent 只会安静，一个观察者会报 `ANOMALY_TELEMETRY_STALE` 并说"缺少机器人观测：连接运行时并确认遥测在流动"。
4. **万能 Agent 会把"结果未知"变成"重试一次"**。用 `NAV_STOW_CONTACT` 与 `NAV_STOW_CONTACT_PREDICTED` 这对码讲：一个码覆盖两种物理含义，必然为其中之一分类错误，而错的那半正是"允许重试把机械臂更深推进障碍物"（`classification_coverage_test.go:356-373`）。**角色分离的收益，是把"谁有资格下这个判断"变成可检查的类型问题。**

### 8.2 练习题

**练习一：给 `OpsAgent` 加一个新规则，并证明它不会变成第二套安全判断。**

要求：为"同一机器人上连续两次恢复动作都未确认结果"新增一个发现码；写出 `Finding` 的关键字段、它在 `Evaluate` 中的位置、它的 `Category` 应取什么（或为什么应为空）、`AutomaticRetryForbidden` 该不该为真，以及你要在哪三个测试文件里加断言。

答案要点：① 规则必须加在 `Evaluate`（`opsrules.go:180-195`）的确定性链条里，输入只能取自 `ObservationInput` 已有的系统事实，不得新增采集（"观察者不成为第二个、竞争的事实来源"，`:123-128`）。② 若该条件涉及物理结果未知，`Category` 应取 `closedloop.UnknownOutcome` 且 `AutomaticRetryForbidden=true`，并且必须**调用 `closedloop.Classify` 而不是自己判断**——两套分类迟早会对"能不能重试"给出不同答案，而其中一套会做出物理动作（`docs/architecture/review-agent.md:50-52`）。若它是"慢步骤"这类非失败，`Category` 必须留空（对照 `ANOMALY_STEP_LATENCY`，`faultmatrix_test.go:252-254`）。③ 必须在 `faultmatrix_test.go` 加一个场景（断言检出 + 分类 + **可执行建议的具体内容** + `wantRetryForbidden`），在 `opsagent_test.go` 确认字段白名单未被破坏，并在 `core/closedloop/classification_coverage_test.go` 确认任何新引入的失败码已被分类（否则它会静默降级成"结果未知、不要重试"）。加分项：说明为何**不要**给 `ANOMALY_...` 码加进 `closedloop` 分类表——`closedloop` 分类的是**执行失败码**，而 `ANOMALY_*` 是 Agent 词表的发现码，两者层次不同（`docs/architecture/multi-agent-runtime.md:118-124`）。

**练习二：实现一个 `handoff`（断点交接）消费者，并解释为什么它不能直接读任务对象。**

要求：用 `tasks.ContextFor(task, "handoff", now)` + `agentcontext.Project(d, "handoff")` 产出一份交接快照，说明 snapshot 里必须出现哪些字段、如何表达"未决动作"，以及为什么不能把 `*tasks.Task` 直接交给消费者；再说明如果历史超过 64 条事件会发生什么。

答案要点：① `handoff` 的决策契约是 `{current_revision, completed_steps, pending_steps, unresolved_step_ids, next_tool}`（`decision_contract.go:14`），而 `ContextFor` 已经把该环节的决策问题替换为"当前版本哪些步骤已完成、待执行或结果未决？恢复前必须核对什么？"（`tasks/context.go:30`）。② `steps[].state` 在 `ContextFor` 里**一律写 `UNKNOWN`**，因为"事件并不可靠地携带 revision，所以不推断每步的 VERIFIED"（`tasks/context.go:45-46`）——这是"宁可承认未知也不补造"的实例，学生最容易在这里写成"看到 `SUCCEEDED` 就填 VERIFIED"。③ 不能直接传 `*tasks.Task` 的理由与 §5.3 同构：值类型、可序列化、有作用域与有效期、可被冻结成 `Projection`（含 `SHA256`/`RendererVersion`/`PolicyVersion`）供事后复核；而一个活对象既不能被冻结，也不能保证消费者不越过它去改状态。④ 超过 64 条事件会插入一条 `history:omitted` 的 `guard` 记录并写明"较早的 N 条事件未放入当前窗口；**不能把缺失视为未发生**"（`tasks/context.go:50-55`）——截断必须自我声明。⑤ 交接的评测判据是"一致性、重复动作率、恢复进度、丢任务率"，失败切片必须覆盖"**交接时未决动作、版本变化、撤权**"（`docs/architecture/agent-evaluation-system.md:78`）。

**练习三：证明"系统能发现自己漏看了什么"，并说明这类证据的强度边界。**

要求：写出至少三条不同层次的缺口检测机制，给出对应的测试或代码位置，并说明各自**证明不了**什么。

答案要点：① 能力层——`OpsAgent.Health()` 在无遥测/无观测时返回 DEGRADED + `ReasonCode`（`opsagent.go:165-204`），它证明"这个 Agent 报告了自己看不见"，**证明不了**它看不见的东西有多重要。② 记录层——`rule.reconcile-first` / `rule.reconcile-unknown` 把"世界状态未知"变成显式结论，`ReconciliationUnavailable` 把"问不出来"与"确实没有"分开（`recoveryagent.go:92-107,556-586`）；它证明"系统在不能回答时停了下来"，**证明不了**停下来之后一定有人接手。③ 分类表层——`classification_coverage_test.go` 用提交清单 + 源文件存在性把"运行时会产生的码都在表里"变成断言（`:55-70,263-275`）；它证明"清单上的码不会静默降级"，**证明不了**清单本身完整（它不会自动扫源码发现新码）。④ 方法论层——"先写测试证明盲区存在，再修"（`2026-09-17-supervision-blind-spots.md:21-26`），以及一个容易被忽略的事实：**那个证物级测试今天已不在工作树里**，取而代之的是正向契约测试（`supervision_gap_test.go:58,95`）。这引出最强的教学点：**"证明缺陷存在"的证据是一次性的，"证明缺陷已修"的证据是永久的**；而两者都不能替代端到端测试——第 ③ 层修复（`Finding.TaskID`）正是只在端到端测试里暴露的（`2026-09-17-supervision-blind-spots.md:38-40`）。

---

## 源码索引

### Go 源码

| 位置 | 内容 |
|---|---|
| `core/agentcontract/contract.go:1-11,28-41,48-68,70-91,106-135,137-160,165-191,193-229,241-253` | Agent 接口、Capability、Permission、Health、Observation、ExecuteRequest/Outcome、Describe |
| `core/agentcontract/event.go:15-43,45-58,60-71,75-139,141-165,174-197,199-232,234-249` | Event、Priority、Topic 词表、通配、Matches/MatchesAny、KnownTopic/KnownPattern、NamespaceOf |
| `core/agentcontract/publisher.go:5-29` | 可选发布能力 `Publisher` 与"运行时注入"的理由 |
| `core/agentcontract/memory.go:8-27,30-52,55-98,105-131,133-142,144-170,172-181` | 三层记忆、Ledger/Beads/Execution、Memory、TaskHistory、TaskEvent |
| `core/agentcontract/anomaly.go:8-33,35-52,54-68,70-100,102-129` | 发现身份唯一实现、上报去重键、`SameAnomaly`、`AnomalyIdentityOf`、`ConditionIdentity` |
| `core/agentcontract/payload.go:23-53,55-89,91-111,113-126,127-147,148-160,211-282,422-432,436-457,459-491,534-...` | 四类发现事件载荷、注册/健康/权限拒绝载荷、RecoveryVerdict/RecoveryStep/RecoveryPlanPayload |
| `agentruntime/registry.go:12-23,25-27,33-39,44-71,74-106,111-133,135-149` | 注册表：注册与启用分离、Register 五条拒绝、Enabled/Disabled/Validate |
| `agentruntime/config.go:28-42,43-53,55-75,80-106,108-123` | 配置、默认启用 task+ops+recovery、ExecutableConfig、normalize |
| `agentruntime/bus.go:1-15,29-34,38-41,44-96,99-133,135-163,165-208,210-226,228-253,255-304,306-324,326-369,371-387,389-421,423-431,433-459,461-471` | 包规则、Subscription、AgentRuntime、Publish、去重、投递与优先级驱逐、subscriber、Close、EventSink/唤醒 |
| `agentruntime/orchestrator.go:16-30,31-96,98-112,118-216,218-281,283-307,309-342,344-399,401-435,437-473,475-527,529-555,557-600,602-618,620-625,627-658` | Orchestrator 全部：订阅/注入/announce、drain/handle/supervise、仲裁与推迟、评估 tick、健康、Shutdown、Publishers、Wake |
| `agentruntime/permission.go:11-26,28-47,49-67,78-92,94-146,148-160,162-182,184-200,202-203` | 权限门控：拒绝码、MutationRequest/Decision、decide 判定顺序、拒绝留痕、Agent 错误留痕 |
| `agentruntime/projection.go:12-25,27-39,41-114,116-138,140-151,153-184,186-219` | 账本↔Agent 词表桥、映射表、优先级、派生身份、反方向渲染 |
| `agentruntime/context.go:11-16,18-39,41-54` | `FindingContext`、`RecoveryRequest.ContextDocument`、`contextEnvelope` |
| `agentruntime/memory.go:16-23,29-89,95-176,182-193,195-276,278-289` | MemoryLedger、MemoryBeads、ExecutionMemory、`Uncertain` 的判据、`NewMemory` |
| `agentruntime/runnermemory.go:10-26,29-48,50-83,85-123,125-163,165-219` | 运行级告警存储、RunnerAlert、TTL、Record 整批替换、RunnerAlertPlan |
| `agentruntime/trail.go:9-23,25-41,43-80,82-106,108-131,133-139,141-189,191-212,214-222,224-231` | TrailKind、DataSource、TrailStep、Trail、Append、Encode、dataSources 真实表名、trailID |
| `agentruntime/ledgerfilter.go:9-45,46-73,75-113,115-133,135-157` | 账本只记转变、159,314/158,193/1,410 实测、Admit 规则、有界淘汰 |
| `agentruntime/opsagent.go:17-42,43-114,116-158,160-220,222-286,288-...427-427,429-535,537-572,574-579,659-666,668-753,755-801` | OpsAgent：无执行端口、默认订阅、只读权限、Health 三态、OnEvent 只累积、recordFailedAction、Observe、publishFinding 四事件、advisory、冷却、learnExistingTasks、条件收尾 |
| `agentruntime/opsrules.go:14-45,47-55,57-71,73-121,123-172,174-195,197-556,557-655` | 8 个异常码、severity、阈值、Finding、Identity、ObservationInput、Evaluate 与七族规则 |
| `agentruntime/recoveryagent.go:17-41,42-75,77-114,116-140,142-162,164-172,174-220,222-272,274-410,412-456,458-472,474-503,505-630,632-696,698-735,737-768,770-833` | RecoveryAgent：只提议、事实三态、RecoveryPlanner、计划字段、Recover 全流程、轨迹、choosePlan 先对账与模型路线、validate 拒绝即作废、冷却、发布、shapesOf |
| `agentruntime/recoverycatalog.go:5-28,30-42,44-110,112-229,231-...254,263-299,316-334,336-355` | 目录是安全边界、三档风险、RecoveryAction、16 条默认动作、Lookup/Actions/Proposible/ForShapes/Encode、RequiresApproval、Executable |
| `agentruntime/faultmatrix_test.go:18-62,64-97,99-200,202-281,283-303,305-348,350-377,379-453,455-510` | 故障矩阵：三件断言、`wantRetryForbidden` 恒断言、10 个失败类场景、6 个非码场景、重复升级、unknown-outcome 与对账 |
| `agentruntime/supervision_gap_test.go:15-17,35-51,53-58,88-95,123-127,148-151,182-189,235-242,277-280` | 7 条监督缺口契约测试及各自的"为什么" |
| `agentruntime/opsagent_test.go:40-42,44-105,107-118` | 只读权限断言、**字段白名单反射测试**、Execute 拒绝 |
| `agentruntime/recoveryagent_test.go:30-42,44-73,75-87,89-...` | 恢复 finding 形状、字段白名单反射测试、Execute 拒绝、只读断言 |
| `core/closedloop/closedloop.go:1-36,45-68,70-76,78-287,289-299,301-323,325-341,343-...` | 删除 Track 的自我否决、8 个失败类、分类表 177 个唯一码、Classify 兜底、Knows、Retryable、DispatchPrecision |
| `core/closedloop/classification_coverage_test.go:15-30,32-43,54-70,72-85,87-114,116-127,128-254,256-275,277-288,290-317,319-395` | 覆盖守卫、83 缺 58 的审计、119 个运行时代码、Knows/Classify 不可区分、五个真实码的复审 |
| `core/closedloop/gate.go:8-34,36-57,60-79,81-102` | Declaration、Reason 常量、Gate、validateFreshness |
| `edge/agent/agent.go:17-31,33-59,61-76,78-109,111-115,117-130,218-...` | TaskAgent 原生实现：身份、从目录读能力、空订阅、可改世界权限与"不复制批准"、Health 三态、Execute 委托 RunControlled |
| `cmd/local-agent/agentruntime.go:62-60…70-246,148-176,178-214,216-245,248-284,286-298,468-513,515-538` | 组合根：注册三个 Agent、双向桥、账本过滤、恢复触发订阅者、Publishers 日志、Abnormal 定义、自动恢复回执、findingFromEvent |
| `internal/autorecovery/supervisor.go:1-36,126-167,169-211,213-223,225-...` | 自动通道：线画在 read_only、skip 与理由、OperatorApproved=false |
| `internal/recoveryexec/executor.go:183-205,206-...257,275,398-...` | 执行器：批准来源分别记录、工具解析、拒绝码 |
| `core/agentcontext/context.go:1-17,19-73,75-83,84-110,111-112,114-219,220` | agent-context.v1、Record/Scope/Document、Mode、Validate、Render（含 legacy/nl_sections/nl_decision/annotated/hybrid）、Hash |
| `core/agentcontext/projection.go:3-16,18-40` | Projection（冻结的确切输入）、Project 与 PolicyVersion 选择 |
| `core/agentcontext/eligibility.go:6-51,53-...` | Eligibility 只判元数据与取代关系、renderAnnotated |
| `core/agentcontext/routing.go:12-23,25-50,58-...` | 8 个环节、内置策略完整性 panic、Resolve |
| `core/agentcontext/stage-policy.json` | `stage-policy.v1-131ef9d8…`、8 环节表达、gate_passed、automatic_deployment=false |
| `core/agentcontext/decision_contract.go:3-21,23-31` | 各环节决策契约（含 handoff 五字段）与操作语义 |
| `core/agentcontext/factorial_policy.go:11-51` | `FactorPolicy`、`ProjectWithFactorPolicy` 的三处拒绝 |
| `core/agentcontext/factorial_config.go:8-30` | ProductionFactors（masking 从不是生产选项） |
| `core/agentcontext/factorial_test.go:121-145` | 模型绑定/环节黑名单的契约测试 |
| `core/agentcontext/decision-field-catalog.json:2-172` | `decision-fields.v1`：10 段 94 字段、stage_critical 8 环节 32 项、required_envelope 与 payload 契约指针 |
| `tasks/context.go:12-85` | `ContextFor`：分环节问题、批准不等于当前许可、state 恒 UNKNOWN、64 条截断自我声明、剔除嵌套上下文 |
| `tasks/alerts.go:418-450` | `SupervisionStatus{Enabled,Agents,Observing,Reason}` 与 `SupervisionOpaque()`："关掉观察者的部署看起来和什么都没出问题的部署完全一样" |
| `console/readiness.go:221-241` | `supervisionCheck`：阻塞式就绪检查，把"没有监督 agent 在运行"变成不能忽略的一项 |
| `internal/actionloop/context.go:10-35` | `ContextSnapshot = Projection`、恢复环节模型输入组装 |
| `agent/agent.go:1-6,116-117` | 自然语言意图解析器（**不是** agentruntime 的 Agent）、goal 环节投影 |
| `orchestration/eval/context_eval/factorial_release.py:1-45` | `blocked_stages` 的生成规则（零完整成功 / 观测到危险建议）与"发布阶段工程排除、非预注册统计主张"的自述 |

### 文档与实验

| 位置 | 内容 |
|---|---|
| `docs/architecture/multi-agent-runtime.md:3-16,20-30,32-61,63-72,74-82,84-94,96-100,102-106,108-112,114-116,118-124` | 三个 Agent 表、观察者非参与者、分层与包边界、复用账本、物理动作不被抢占、运行时注入发布通道、身份唯一、权限门控、三层记忆、配置、与 Python 恢复引擎的边界 |
| `docs/architecture/review-agent.md:3-27,29-64,66-94,96-113,115-126,128-147,149-157,168-204` | Review Agent = OpsAgent、三条不可动摇的规则、输入四路与输出两作用域、告警生命周期、明确不做的事、恢复闭环只走到第③段、Track 已删除 |
| `docs/architecture/recovery-agent.md:18-32,34-48,50-76,78-102,104-133,136-154,156-170,172-195,197-253,255-296,297-308` | 为什么需要它、结构化边界、16 条目录与三档风险、拒绝否决整份计划、先对账、轨迹读法、发布通道注入、身份规则、计划字段、执行通道四条检查、自动通道与 52 任务实测、诚实清单 |
| `docs/architecture/supervision-verification.md:14-57,61-88,92-133,137-180,182-199,201-224,226-237,245-274,278-289,293-323` | 故障矩阵（表列 14 行/代码 16 场景）、可回溯链条、三个盲区与三层修复、Abnormal 定义、实测五阶段、交叉验证、故障注入器、分类表缺口、分工闭环实测、边界、自动化运维接口 |
| `docs/architecture/module-health-and-faults.md:3-35,37-96,98-120,122-140,141-176,178-201,202-240,242-277` | 五层设计、`robot.faults.v1`、能力联动、处置阶梯、检测四层、自动定位/恢复/解决、发布路径与跨语言契约、LLM 工具面、与任务闭环的关系、扩展清单、明确不做 |
| `docs/architecture/agent-v1.md:5-25,27-53,55-88` | V1 契约、迁移说明"什么变了什么没变"、为什么 TaskAgent 是原生 Agent、回滚、新边界（Agent 之间不直接调用） |
| `docs/architecture/agent-evaluation-system.md:42-56,58-85,87-110,241-315,315-329,331-341,343-...` | 七层评测、二十项能力（含 handoff 判据与失败切片）、工具层、统计与三态门禁、故障矩阵与五套 suite、评分器可信度、发布工作流 |
| `docs/development/adding-an-agent.md:5-11,13-44,46-56,58-66,68-147,149-156,158-168` | 三步新增、接口与逐项要求、Publisher、Observer、EvalAgent 骨架、否决权需先解决的问题、experience/escalation 扩展点、检查清单 |
| `docs/development/decision-context-fields.md:1-5,7-21,22-38,39-52,53-69,70-86,87-106,107-119,120-138,139-156,157-...` | 生产者字段字典（goal/steps/tool_return/verification/observation/guard/hypothesis/attempts/tools/handoff）、"未知为 null""不得补造"、时间窗与时钟域 |
| `docs/experiments/2026-09-21-agent-context-evaluation.md:6-19,25-36,38-70,72-116,118-133,134-190,191-216,217-236,287-308,312-325,327-339,341-362,364-412,414-442,444-462,488-499,501-523,525-565,567-577,579-589,591-622,624-654,656-686,688-713,715-731,733-754,756-785,811-815,842-853` | 三轮实验全部：结论与数字、表达范式、冻结与公平性、模型与重放、指标、留出结果、多轮行为、分环节选型、统计、四因素主效应、反事实 masked、逐环节最终配置、字段契约、形式化检查、落地与复现、评测体系、可发表主张 |
| `docs/development/2026-09-17-supervision-blind-spots.md:3-7,11-50,54-72,76-103,107-...` | 三个盲区的发现过程（含"先写测试证明盲区存在"）、三层修复与漏掉的第三层、Abnormal 收窄、变糟时沉默、说得出"任务失败"说不出"哪一步" |
| `docs/development/2026-09-18-system-review-and-improvement-plan.md:230,628` | ⚠️ 与代码冲突的"113 个运行时故障码"表述及其类区间引用（113 = 提交 `c8cae5af2` 时 `runtimeEmittedCodes` 的条数，非分类表条数） |
| `artifacts/agent-context-eval/factorial-v1/run-v3/deployment-policy.json` | 真实的 `blocked_stages`：两模型 × {planning, verification, recovery} 及理由原文（该目录被 `.gitignore:109` 忽略，不随 Git 分发） |
