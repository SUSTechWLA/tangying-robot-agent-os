# 第 8 章 分布式不是部署姿势，是故障假设

> **本章的核心命题**
>
> 「分布式」在这套系统里**不是**"把程序拆到多台机器上"。
> 它是**一组必须被处理的故障的集合**。
>
> 判据是：**分层与一致性机制的成本，必须用故障集合来偿还。
> 故障集合没覆盖到的机制，就是纯负债。**

---

## 8.0 一句话的定义

README 里有一段话，是全书对"分布式"最精确的定义（`README.md:111`）：

> 「分布式」在这套系统里不是部署姿势，是**故障假设**。它意味着四件具体的事：

| 设计 | 解决的问题 |
| --- | --- |
| **单写协调 + 资源归属** | 多个写者不会同时改同一个物理世界（地图、位姿、谁抓着什么、充电位） |
| **租约 + 单调递增的 fencing token** | 旧持有者拿着过期授权写入，会被直接拒绝——**脑裂挡在数据层，不靠人盯** |
| **事件与 Outbox + 幂等键** | 先落盘事实再投递；进程重启/队列宕机后能接着做，而不是从头再来 |
| **云端慢环 + 边缘快环** | 实时控制环与安全监督留在机器人上，规划与对账在控制面；**断网安全停机，恢复后对账** |

**这四行就是本章的提纲。**

但我在写作过程中发现了一件必须放在最前面说的事：**这四条里有一条，实现只落地了一部分。** 先把它说清楚，因为不讲清楚，后面所有的讨论都会建立在错误的假设上。

---

## 8.1 一个先说清楚的事实校正：四个资源里只落地了一个

README 那句"单写协调 + 资源归属"的括号里列了四个资源：**地图、位姿、谁抓着什么、充电位**。

**代码事实是：四个里只有一个真的被 owner + lease + fencing 管住。** 我逐项核对了：

| 资源 | 建模方式 | 有归属/互斥吗 | 位置 |
| --- | --- | --- | --- |
| **共享红方块** | `worldmodel.ResourceState{ResourceID, Owner, FencingToken, ExpiresAt, Evidence}` | **有**——唯一被 lease + fencing 管的资源 | `core/worldmodel/types.go:59-66`；资源 ID 常量 `coordinator.go:913` |
| **「谁抓着什么」** | `worldmodel.RobotState.Held string`，来自遥测 `held` 字段 | **没有**——它是**观测**而非授权；`RobotHeld` 只作谓词 | `types.go:33`；`proto/fleet/v1/fleet.proto:164`；`predicate.go:74-88` |
| **位姿** | `RobotState.Pose` / `EntityState.Pose` + `Freshness` | **没有**——只有新鲜度，没有排他 | `types.go:31,50` |
| **地图** | 每机器人本地占用栅格经 `fleet/fusion` **确定性纯函数**融合（max 合并、按 id 去重取高置信度） | **没有**——无归属概念 | `fleet/fusion/fusion.go` |
| **充电位** | **不存在** | — | 全仓库 `grep -rn "charg\|dock"`（排除 docker）在 `fleet/ edge/ core/ skills/` 下**零命中** |

（另有一类：静态几何。`EntityState.Attributes["static"]=="true"` → 视为**不可变的版本化 map/model 事实**，永不过期，只在显式 update/delete 或 transform revision 变化时改变。`core/worldmodel/projector.go:228-232`。这也不是"归属"。）

### 这不是文档说谎

这不是"文档在吹牛"。README 那句话描述的是**设计意图的完整清单**，而实现只落地了其中一格。

（推断）多机充电调度属于未来工作——因为当前可复现场景是**固定的两台机器人 + 一个共享方块**。项目文档自己划了范围（`docs/architecture/multi-robot.md:28`）：

> 当前可复现任务是明确有序的双机器人限定场景，**不是任意任务的并行调度器或通用碰撞规避系统**。

### 为什么这件事必须放在本章开头

因为**它决定了你怎么读这一章剩下的内容**。

如果你以为"地图有归属"，你会问"两个机器人同时改地图怎么办"。正确答案是：**它们不共享一张地图**——每台机器人有本地占用栅格，融合是**确定性纯函数**，谁先谁后结果一样。

**这比"加一把锁"更简单，也更正确。** 因为两个传感器看到的世界本来就可以不一致——把它们的一致性用锁强行维护，是解决一个不存在的问题。

**教学要点**：这是"机制必须用故障集合来偿还"的第一个例子。地图融合**不需要**归属，因为它没有"两个写者互相覆盖"的故障——融合函数是幂等的、可交换的。**加一把锁只会增加复杂度和失败模式。**

---

## 8.2 四种租约：它们不是一套机制

这是本章最容易讲错的地方。

`fleet/lease` 只定义了一个通用接口，但业务上至少有**四个语义完全不同**的租约：

| 租约 | 资源 ID 形态 | 默认 TTL | 过期后果 | 源码 |
| --- | --- | --- | --- | --- |
| **设备租约** | 每 robot 一条记录 | **15 s** | 设备判为 `online=false`，协调器拒绝为其分派 | `fleet/registry/registry.go`；`fleet/gateway/gateway.go:99-100` |
| **声明租约**（intent claim） | 每 intent 一条 | **2 m** | **不是释放，是进入 `UNKNOWN_OUTCOME`** | `fleet/coordinator/coordinator.go:153,324-347` |
| **资源租约** | `block:red-block` | **2 m** | 世界投影里资源降级；harness 拒绝用过期资源确认完成 | `coordinator.go:913`；`core/harness/evaluator.go:157` |
| **领导者租约** | `leader/<worldID>` | **15 s** | 本协调器**所有**变更被拒绝（`ErrLeadershipLost`） | `coordinator.go:274,1333-1345` |

**声明租约不是互斥锁，这是全系统最反直觉的设计**，见 6.6。

### 通用接口

```go
// fleet/lease/manager.go:9-29
var (
	ErrLeaseHeld         = errors.New("resource lease is held by another owner")
	ErrLeaseNotFound     = errors.New("resource lease not found")
	ErrLeaseExpired      = errors.New("resource lease expired")
	ErrStaleFencingToken = errors.New("stale fencing token")
)

type Grant struct {
	ResourceID string    `json:"resourceId"`
	Owner      string    `json:"owner"`
	Token      uint64    `json:"token"`
	ExpiresAt  time.Time `json:"expiresAt"`
}

type Manager interface {
	Acquire(context.Context, string, string, time.Duration) (Grant, error)
	Renew(context.Context, string, string, uint64, time.Duration) (Grant, error)
	Transfer(context.Context, string, string, string, uint64, time.Duration) (Grant, error)
	Validate(context.Context, string, string, uint64) error
	Release(context.Context, string, string, uint64) error
}
```

两个实现：

| 实现 | 场景 | 机制 |
| --- | --- | --- |
| `lease.MemoryManager` | 单进程 | `sync.Mutex` + `grants map[string]Grant` + `tokens map[string]uint64`，**支持注入时钟**以便测试过期（`NewMemoryManagerWithClock`） |
| `redis.LeaseManager` | 多进程（真正的实现） | **五个操作各是一段 Lua 脚本**，靠 Redis 单线程保证原子性 |

**"支持注入时钟"这个细节值得注意**：租约过期是一个时间相关的行为，而时间相关的行为**默认不可测试**。注入时钟是让它可测的标准做法——否则你只能 `sleep`，而 sleep 出来的测试既慢又不稳定。

### fencing token 只在一处递增

**内存实现**（`memory.go:38` 在 `Acquire` 抢到空闲租约时；`memory.go:71` 在 `Transfer` 时）：

```go
m.tokens[resourceID]++
grant := Grant{ResourceID: resourceID, Owner: owner, Token: m.tokens[resourceID], ExpiresAt: now.Add(ttl)}
```

**Redis 实现**（`lease.go:24`，`acquireLeaseScript`；`lease.go:44`，`transferLeaseScript`）：

```lua
local token = redis.call('INCR', KEYS[2])
redis.call('HSET', KEYS[1], 'owner', ARGV[1], 'token', token)
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return token
```

两个键由 `resourceID` 的 sha256 前 8 字节派生，并放进**同一个 Redis Cluster hash tag**（`lease.go:148-153`）：

```
tangying:fleet:lease:{<tag>}:state
tangying:fleet:lease:{<tag>}:counter
```

**为什么要 hash tag？** 因为 Redis Cluster 只保证**同一个槽**内的多键操作是原子的。`INCR` 和 `HSET` 必须落同一个槽，才能被同一个 Lua 脚本原子执行。这是一个非常具体、非常容易被忽略的分布式实现细节——**如果你用随机的键名，这段 Lua 在 Redis Cluster 上会直接报错**（而不是静默出错，这算是幸运的）。

### token 是"纪元号"，不是"版本号"

**关键**：`INCR` **只在授予新租约或转移时发生**。`Renew` / `Validate` / `Release` 都**不递增**（`renewLeaseScript` 只 `PEXPIRE` 并返回原 token，`lease.go:30-37`）。

所以：

> **同一次持有期间 token 恒定。**

**这正是让"旧持有者的重放"可以被一个整数比较识别的原因。**

如果 token 每次续租都递增，那么"当前有效 token"就在不断变化，一个迟到的合法请求会因为 token 落后而被拒——你会得到一堆误报。让 token 只在持有者变更时递增，它就精确地表达了"这是第几任持有者"。

**教学要点**：给一个计数器起对名字很重要。它**不是**"版本号"（版本号每次修改都变），它是**"纪元号"（epoch）**。名字对了，用法就不会错。

---

## 8.3 旧持有者写入：六个独立的拒绝点

这是本章最值得画表的一页。系统在不同尺度上有**六个**独立的拒绝点，任何一个生效都足以阻止双写：

| # | 尺度 | 拒绝点 | 判定 | 错误 |
| --- | --- | --- | --- | --- |
| 1 | **Redis**（多进程权威） | `fleet/redis/lease.go:50-56` `validateLeaseScript` | `HGET owner ~= ARGV[1] or HGET token ~= ARGV[2]` | `LEASE_HELD` / `STALE_FENCING_TOKEN` |
| 2 | **内存**（单进程） | `fleet/lease/memory.go:94-106` `validateLocked` | 先判过期（`:99`）→ `ErrLeaseExpired`；再判 `owner/token`（`:102`）→ `ErrStaleFencingToken` | — |
| 3 | **任务图聚合**（DB） | `fleet/mysql/coordination.go:106-107` | `currentVersion != ExpectedVersion` → `ErrVersionConflict`；`:121-131` 再加一道 `UPDATE ... WHERE version=?` + `RowsAffected()==1` | `eventlog.ErrVersionConflict` |
| 4 | **协调器**（API 入口） | `fleet/coordinator/coordinator.go:639-641` | `fencingToken != node.FencingToken` → `ErrIntentIdentityConflict`；`:636-638` 同时校验 `taskRevision / aggregateVersion / stepID / commandID` | `ErrIntentIdentityConflict` |
| 5 | **世界谓词**（验证层） | `core/harness/evaluator.go:150,157` | `resource.FencingToken != expected` → `FENCING_TOKEN_MISMATCH`（`FailedSafe`）；租约过期 → `RESOURCE_LEASE_EXPIRED`（`Waiting`） | — |
| 6 | **Robot Runtime**（最后一道） | `sim/mujoco/tangying_sim/server.py:471-472` | `if grant != expected_grant: return "FENCING_TOKEN_STALE"`，在 `_validate()` 内、`_dispatch()` 之前 | 命令**根本不进执行** |

### 第 6 点是唯一"物理上真的没动"的保证

其他五道都是"软件拒绝写入"，第 6 道是"**命令根本没到执行器**"。

它的完整前置检查链在同一函数里（`server.py:429-478`，按顺序）：

```
CANCELLED → ROBOT_COMMISSIONING_ACTIVE → SCHEMA_VERSION_UNSUPPORTED
→ COMMAND_EXPIRED (deadline) → LEASE_REQUIRED (lease_ms == 0)
→ IDEMPOTENCY_KEY_REQUIRED → ROBOT_ID_MISMATCH → TOOL_CATALOG_STALE
→ TOOL_CATALOG_REVISION_REQUIRED / FENCING_TOKEN_REQUIRED
→ FENCING_TOKEN_STALE → SAFETY_PROFILE_REJECTED → EMERGENCY_STOP_LATCHED
```

**13 项检查，全部通过才进 `_dispatch`。**

### 一个必须标注的分歧风险

`:462-469` 有一段 `allow_monotonic_grant_adoption`：Runtime 允许"同一 owner、token 严格更大"时**单向前进式采纳**新 token。

这是为了让云端的 transfer 结果能推进本地纪元。但它同时意味着：

> **Runtime 侧不能独自证明 token 没被跳号**——跳号检测依赖云端 `INCR` 的权威性。

这是一个**真实的信任假设**，它属于 6.8 的"未验证"范围。项目运维文档也明确写了（`docs/production/operations-and-failures.md:106`）：

> 不得……**降低 token**……来伪造支持。

### 三道并联 vs 一次原子

**注意一个关键事实**：拒绝是**三道独立机制**——领导者租约（阻止旧协调器工作）+ 资源 token（阻止旧持有者写世界）+ DB 版本 CAS（拦同一聚合上的并发提交）。

**三道都失败才会双写。**

而这三道**不是原子的**。这正是"leader fencing 与所有业务提交的同存储原子校验"**仍然列在未验证清单上**的原因：

> 它们目前是**三道并联的检查**，不是**一次原子提交**。

**教学要点**：这是一个关于"安全"的重要区分。三道并联检查的失效概率是三者之积（假设独立），而一次原子提交的失效概率是 0 或 1。**在工程上这两者常常够用，但在证明上是两回事。**

---

## 8.4 时序图：双机交接 + 旧持有者写入

用一个完整的时序把上面所有机制串起来。

```
时刻   robot-1 worker     Coordinator(leader A)      Redis lease        robot-2 worker
 t1    │ POST intents/next ──►│                                            │
 t2    │                       │ validateLeadership() :1333                 │
 t3    │                       │  Validate(leader/world-1,A,1) ──►│ ok      │
 t4    │                       │ reclaimStaleLocked() :324                  │
 t5    │                       │ prefixSucceeded(i=0) :562                  │
 t6    │                       │ Acquire(block:red-block,                   │
 t7    │                       │        robot-1, 2m) ──────────►│ INCR → 1 │
 t8    │                       │ store.Commit(INTENT_CLAIMED)               │
 t9    │                       │  CAS version, event, checkpoint            │
t10    │◄─ IntentNode{token:1, │  publishResource() → WorldHub              │
t11    │   worldRevision,      │   (ResourceUpsert, sourceSeq=1)            │
t12    │   commandId, stepId}  │                                            │
t13    │ Invoke(Command{fencingToken:1}) ── Runtime._validate() 通过 ──────►│
t14    │ 物理动作：robot-1 把红方块放进 handoff-zone                         │
t15    │ POST intents/0/complete {token:1,...} ──►│                          │
t16    │                       │ CompleteIntentRevision :602                │
t17    │                       │  ① token == node.token :639                │
t18    │                       │  ② harness.Evaluate :703                   │
t19    │                       │     EntityInside + EntityStable(2)          │
t20    │                       │     + RobotHeld(robot-1)=="" + SourceFresh  │
t21    │                       │  ③ Transfer(block, robot-1 →               │
t22    │                       │     robot-2, oldToken=1) ─────►│ INCR → 2   │
t23    │                       │  next.Status = READY :759                   │
t24    │                       │  outbox: "<task>/intent/1/ready"            │
t25    │                       │  store.Commit(BLOCK_AVAILABLE, outbox) ← 同一事务
t26    │                       │ DispatchOutbox(500ms ticker)                │
t27    │                       │  ClaimOutbox(FOR UPDATE SKIP LOCKED) ──────►│
t28    │                       │  AckOutbox                        t29 认领，token=2
       ⋮                                                                     ⋮
 ══ 故障：robot-1 的进程在 t15 与 t28 之间被 kill，随后云端换主 ══
t30    │ (旧的 leader A 仍在跑) │                                            │
t31    │                       │ 换主：B.Acquire("leader/world-1") → counter=2
t32    │ (A 收到 robot-1 的重放)│                                            │
t33    │ POST complete{token:1}─►│ A.validateLeadership() :1341              │
t34    │                       │  Validate(leader/world-1,A,1) ──────────► 失败
t35    │◄─ 409 ErrLeadershipLost                                            │
t36    │ (即使 A 侥幸通过)      │ CompleteIntentRevision :639 token 比对      │
t37    │                       │  若拿到的是新纪元，token 必然不匹配 → 拒绝  │
```

**结论：拒绝是两道独立机制。**

- **领导者租约**阻止旧协调器工作；
- **资源 token** 阻止旧持有者写世界；
- **DB 版本 CAS** 是第三道，拦同一聚合上的并发提交。

而第 6 道（Runtime 的门禁）保证**即使前五道全漏了，命令也进不了执行器**。

### 一个精妙的语义点

注意 t36：即使旧 leader A 的请求侥幸通过了领导者校验，`CompleteIntentRevision` 里的 **token 比对** 仍然会拒绝它——**因为新纪元下 token 必然不匹配**。

**两道机制拦的是不同的事件**：

| 机制 | 拦什么 |
| --- | --- |
| 领导者租约 | **旧协调器还在工作** |
| 资源 token | **旧持有者还在写世界** |

**这两件事是独立的**：一个失去领导权的协调器可能还在处理一条合法的完成上报（那条上报本身是真的！），但那条上报引用的资源纪元已经变了。

**教学要点**：这是一个关于"分层防御"的好例子。第二道不是为了"万一第一道失效"，而是为了**拦住第一道根本不覆盖的另一类事件**。

---

## 8.5 单写协调：三个尺度

系统在**三个尺度**上防"多个写者同时改同一物理世界"：

| 尺度 | 机制 | 源码 |
| --- | --- | --- |
| **世界** | 领导者租约 `leader/<worldID>`；每次变更前 `validateLeadership` | `coordinator.go:270-283`（Acquire）、`:285-301`（Renew）、`:1333-1345`（Validate）；续租 ticker 周期 = `leaderTTL/3` |
| **任务图** | 聚合版本 CAS：`SELECT version ... FOR UPDATE` + `WHERE version = ?` + `RowsAffected()==1` | `fleet/mysql/coordination.go:96-98,121-131` |
| **单个物理对象** | 资源租约 + fencing token，且**必须被世界谓词再次复核** | `coordinator.go:477-491`（认领时）、`core/harness/evaluator.go:143-158`（验证时） |

### 第三层是机器人系统区别于普通分布式系统的关键

> **租约持有 ≠ 世界谓词成立。**

协调器**不会**因为"我持有 token"就接受完成。它要求四个条件**同时**为真（`coordinator.go:694-726`）：

```
EntityInside        — 方块在目标区域内
EntityStable(2)     — 连续 2 次稳定观测
RobotHeld(empty)    — 机器人不再持有它
SourceFresh         — 来源没过期
```

**为什么"我持有 token"不够？** 因为 token 只说明"**我有权操作它**"，不说明"**我操作成功了**"。

这是一个容易被混淆的区分：

| 陈述 | 类型 | 谁保证 |
| --- | --- | --- |
| "robot-1 有权移动这个方块" | **授权** | 租约 + fencing token |
| "方块现在在 handoff-zone" | **事实** | 观测 + 世界谓词 |

**把授权当事实，是分布式机器人系统里最经典的一类错误。** 它对应的现实场景是："我拿到了锁，所以我以为我做完了。"

---

## 8.6 声明租约：租约过期不等于可以重试

这是本章最重要的一节，也是最值得给学生讲的一节。

### 大多数分布式系统教材教你什么

> 租约过期 → 资源不再被保护 → **可以安全地重新获取**

这个规则对**绝大多数**资源是对的：一个过期的数据库锁、一个过期的缓存条目、一个过期的会话——重新获取没有问题。

**这个代码库明确地、带论证地拒绝了这条规则**，而理由是**领域性的**。源码注释（`coordinator.go:303-323`）：

> 它**曾经**把它们退回 READY，理由是「崩溃或断连的 worker 绝不能永远阻塞任务」。
>
> 这个理由对 **worker** 是对的，对**机器人**是错的：
> **租约失效告诉我们 worker 停止报告了，不是机器人站着没动。**

**"租约失效告诉我们 worker 停止报告了，不是机器人站着没动。"**

这一句话应该被刻在每一个做机器人分布式系统的团队的墙上。

### 正确的行为

`reclaimStaleLocked`（`coordinator.go:324-347`）对过期 RUNNING intent 做的事是：

```go
node.Status = StatusUnknownOutcome   // :338
node.Finished = now
node.Error = "claim lease expired with the outcome unknown; reconcile before acting again"
// Claimed 故意保留 —— "哪个 worker 在租约失效时持有它" 是对账要问的第一个问题
```

**注意 `Claimed` 是故意保留的。** 对账时要问的第一个问题就是"**哪个 worker 在租约失效时持有它**"——如果这里清空了，这个问题就永远答不出来了。

### 退出这个状态只有一条路

```go
ReconcileIntent(ctx, taskID, index, decision, actor, note)   // coordinator.go:1065
```

硬约束（`:1074-1079`）：

- **必须同时提供人和理由**；
- 决策只有两种：

| 决策 | 语义 | 后续 |
| --- | --- | --- |
| `NEVER_ACTED` | 确定没发生 | **唯一能把 intent 放回可认领池的决策**（`:1127-1132`） |
| `ABANDON` | 判失败 | （`:1133-1137`） |

而且 `FencingToken` 在对账时被**清零**（`:1146`）：

> 确保下一次认领拿到**严格更大**的新 token。

### 晚到的完成上报必须被"具名拒绝"

```go
// coordinator.go:648-658
return nil, fmt.Errorf("%w: intent %d held by %s since %s; reconcile it before reporting a result",
	ErrClaimExpired, index, node.Claimed, node.Started.UTC().Format(time.RFC3339))
```

注释（`:649-654`）解释了为什么错误必须**具名**：

> 「**你来晚了，这个已经不能认领**」和「**机器人可能动过，而且没人知道**」需要完全不同的下一步。

**这是一个可以直接推广到所有 API 设计的原则：错误码是运维接口，不是日志美化。**

一个只说 "not running" 的错误，会让运维去找"为什么没在跑"；一个说 "held by robot-1 since 10:23:41, reconcile before reporting" 的错误，直接告诉他该做什么。

### 四个测试把它钉死

`TestClaimLeaseLapseLeavesTheOutcomeUnknownInsteadOfReclaiming`（`coordinator_test.go:604`）等四个测试，把"不能退回 READY"变成为回归测试。

### 这个例子的教学价值：一次串起六个概念

**这是我认为整本书最好的一个教学案例**，因为它一次性把六个分布式概念串在一起：

| # | 概念 | 这个系统怎么体现 |
| --- | --- | --- |
| 1 | **租约的本质是"暂停信任"，不是"证明终止"** | 租约只给出 **liveness** 保证，不给出 **safety** 保证。教材里常把两者混在一起 |
| 2 | **fencing token 与租约的分工** | 租约管**何时**可以换手，token 管**换手后旧人还能不能写**。而这两者**都只是必要条件**，还必须过世界谓词 |
| 3 | **不可逆操作改变一致性协议的形态** | 一个可重试的动作只需要幂等键；一个**不可重试**的动作需要**一个人类决策点**（`ReconcileIntent` 强制 `actor` + `note`） |
| 4 | **"错误必须具名"** | `ErrClaimExpired` vs "intent not running" 的区别说明错误码是**运维接口** |
| 5 | **状态机的形状由物理决定** | `UNKNOWN_OUTCOME` 是一个**没有自动出边**的状态。在 coding agent 里这个状态根本不需要存在 |
| 6 | **可测试性** | 四个测试把"不能退回 READY"钉死 |

### 一道配套练习

> **题**：某个协调器实例 A 因 GC 停顿 40 秒未续租（`FLEET_LEADER_LEASE=15s`），实例 B 已接管并推进了任务图。A 恢复后立刻处理一条来自 robot-1 的 `POST /v1/tasks/T/intents/0/complete`，请求里带 `fencingToken=1`。
> 请列出所有会拒绝它的检查点，并回答：**如果 A 的请求恰好赶在 B 接管之前到达，会发生什么？**

<details>
<summary>答案要点</summary>

**检查点：**

1. `validateLeadership` → Redis `validateLeaseScript` 里 `owner != A` → `ErrLeadershipLost`（`coordinator.go:1333-1345`）
2. 即使跳过 ①，`CompleteIntentRevision` 的 `fencingToken != node.FencingToken`（`:639-641`）会因为新纪元而拒绝
3. 若 B 尚未改 token，则 DB 版本 CAS（`coordination.go:106-107`）会因为 `ExpectedVersion` 落后而拒绝

**关键洞察**：三个检查点**不是原子的**（`distributed-agentos.md:35` 把它列为未验证项）。

**"赶在 B 接管之前"会发生什么？** = 领导者租约尚未过期、token 尚未递增、版本尚未推进。

此时 **A 的请求会成功**——而这是**正确的**，因为"B 接管"这个事实当时还不存在。

**这正是租约模型的语义**：它保证的是"**不会有两个有效领导者同时存在**"，而不是"**一个被暂停的进程立刻失去权力**"。若要求后者，需要 TrueTime / 租约令牌一致性等更强的机制。

**加分点**：指出 `Grant.ExpiresAt` 是**各持有者本地时钟**推算的（`redis/lease.go:155-157`），真正的过期由 Redis 服务端 `PEXPIRE` 决定——时钟漂移下两者不一致，而全仓库**没有时钟漂移测试**。

</details>

---

## 8.7 事件日志与 Outbox

### 7.1 一个必须先纠正的直觉：这不是事件溯源

```sql
-- fleet/mysql/coordination.go:15-20  聚合当前状态（快照）
CREATE TABLE IF NOT EXISTS fleet_graph_states (
  aggregate_id VARCHAR(128) PRIMARY KEY,
  version BIGINT UNSIGNED NOT NULL,
  data JSON NOT NULL,
  updated_unix_ms BIGINT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- :21-35  不可变事实流
CREATE TABLE IF NOT EXISTS fleet_domain_events (
  event_id VARCHAR(256) PRIMARY KEY,
  aggregate_type VARCHAR(64) NOT NULL,
  aggregate_id VARCHAR(128) NOT NULL,
  aggregate_version BIGINT UNSIGNED NOT NULL,
  event_type VARCHAR(96) NOT NULL,
  payload_json JSON NOT NULL,
  idempotency_key VARCHAR(256) NOT NULL,
  causation_id VARCHAR(256) NOT NULL DEFAULT '',
  correlation_id VARCHAR(256) NOT NULL DEFAULT '',
  actor VARCHAR(128) NOT NULL DEFAULT '',
  occurred_unix_ms BIGINT NOT NULL,
  UNIQUE KEY fleet_events_idempotency (aggregate_type, aggregate_id, idempotency_key),  -- :33 ★
  INDEX fleet_events_aggregate (aggregate_type, aggregate_id, aggregate_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- :36-46  待投递
CREATE TABLE IF NOT EXISTS fleet_outbox (
  id VARCHAR(256) PRIMARY KEY,
  topic VARCHAR(256) NOT NULL,
  message_key VARCHAR(256) NOT NULL,
  payload LONGBLOB NOT NULL,
  created_unix_ms BIGINT NOT NULL,
  claimed_unix_ms BIGINT NULL,
  acked_unix_ms BIGINT NULL,
  attempts INT NOT NULL DEFAULT 0,
  INDEX fleet_outbox_pending (acked_unix_ms, claimed_unix_ms, created_unix_ms)  -- :45
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- :47-53  恢复游标
CREATE TABLE IF NOT EXISTS fleet_checkpoints (
  aggregate_id VARCHAR(128) PRIMARY KEY,
  version BIGINT UNSIGNED NOT NULL,
  event_cursor VARCHAR(256) NOT NULL,
  data JSON NOT NULL,
  created_unix_ms BIGINT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**状态表与事件表不是事件溯源的两个视图。**

- `fleet_graph_states.data` 是**权威状态**（`persistedState`）；
- 事件表是**审计与追溯**——`DomainEvents` **只被读，从不被重放来重建状态**。

原因写在 `coordinator.go:170-173`：

> 协调器"首次看到一个任务时从持久化任务重建视图，**因此协调器自己不需要持久化存储**"。

**真相在 `fleet_graph_states`，不在事件流。**

**⚠️ 这一点与常见 CQRS / 事件溯源教材不同，讲课时必须明确指出。** 事件溯源的定义是"事件是唯一真相，状态是投影"。这里是"状态是真相，事件是审计"。两者的恢复策略完全不同：

| | 事件溯源 | 本项目 |
| --- | --- | --- |
| 恢复方式 | **重放**所有事件 | **直接读**状态快照 |
| 事件的作用 | 唯一真相 | 审计、追溯、投影给前端 |
| 事件 schema 演进 | 极难（要支持历史所有版本） | 相对容易 |

### 7.2 一次 `Commit` 就是一个事务

`Store.Commit`（`coordination.go:85-188`）在一个 `LevelReadCommitted` 事务里做**五件事**：

| # | 动作 | 行号 |
| --- | --- | --- |
| 1 | `SELECT version ... FOR UPDATE` 拿行锁，做 CAS 前置检查 | `:96-108` |
| 2 | 写/更新 `fleet_graph_states` | `:114-132` |
| 3 | 插入 `fleet_domain_events` | `:136-154` |
| 4 | 插入 `fleet_outbox` | `:155-166` |
| 5 | `INSERT ... ON DUPLICATE KEY UPDATE` 更新 `fleet_checkpoints`，**用 `IF(incoming.version >= ...)` 保证版本只增不减** | `:172-181` |

### 7.3 为什么「先落盘事实再投递」

不是风格选择，是三个可验证的后果：

**① 投递失败不丢事实。**

`DispatchOutbox`（`coordinator.go:1204-1251`）投递失败时调 `ReleaseOutbox` 清空 claim（`:1236`），条目留在 outbox，下一轮重试。

测试固定了这一行为：

- `TestQueueOutageLeavesCommittedHandoffOutboxForRecovery`（`coordinator_test.go:307`）
- `TestOutboxDispatcherRecoversAfterPublisherReturns`（`:341`）

**② 崩溃点任意，状态一致。**

```go
// fleet/eventlog/store.go:13-14
// OutboxClaimTTL makes a crashed dispatcher claim recoverable.
const OutboxClaimTTL = 30 * time.Second
```

这 30 秒让"claim 之后崩溃"的条目自动重新可见（`coordination.go:254,258`）。

**为什么要这个 TTL？** 因为 claim 是一个"我正在处理它"的标记。如果一个 dispatcher claim 之后就死了，没有 TTL 的实现在于：那个条目**永远**处于 claimed 状态，**永远不会**被重新投递。

**③ 不变量可以在提交点检查。**

因为事件、outbox、checkpoint 与状态**同事务**，不可能出现"事件说成功、状态说没成功"的裂口。

**这是 6.3 六道防线里唯一原子的一道。**

### 7.4 幂等键：确定性字符串，不是 UUID

全部由协调器在 `persistLocked`（`coordinator.go:1279-1321`）里生成，**不是 UUID**——这是刻意的：**同一次逻辑变更重放时键必须相同**。

| 事件 | 幂等键 | 源码 |
| --- | --- | --- |
| `FLEET_GRAPH_CREATED` | `taskID + "/graph-created"` | `:405` |
| `INTENT_CLAIMED` | `"%s/intent/%d/claim/%d"`（taskID, index, version+1） | `:513` |
| `INTENT_SUCCEEDED` / `BLOCK_AVAILABLE` | `"%s/intent/%d/succeeded"` | `:809` |
| `INTENT_FAILED` | `"%s/intent/%d/failed"` | `:1035` |
| `INTENT_RECONCILED` | `"%s/intent/%d/reconciled"` | `:1148` |
| `INTENT_CLAIM_EXPIRED` | `"%s/reclaim/%d"`（taskID, version+1） | `:1106,1183` |

（`event_id` 另有一套：`"%s/%020d/%s"`（taskID, nextVersion, eventType），`:1294`。）

**教学要点**：UUID 保证"每次生成都不同"，这在需要**去重**的场景里**恰好是错的**——因为重放时会生成一个新的 UUID，去重失效。幂等键必须是**由内容决定的函数**。

### 7.5 去重发生在三处，语义不同

| # | 位置 | 语义 |
| --- | --- | --- |
| 1 | **MySQL 唯一键**（`coordination.go:33`） | **最后兜底**。但它几乎不会被触发，因为版本 CAS（`:106-107`）会先失败 |
| 2 | **内存 store 显式查重**（`fleet/eventlog/memory.go:62-68`） | 返回 `duplicate event id ...` / `duplicate event idempotency key ...`——**是错误，不是成功** |
| 3 | **协调器状态检查**（真正处理重放的地方） | 见下 |

第 3 处的代码：

```go
// coordinator.go:642-644
if node.Status == StatusSucceeded && node.Claimed == robotID {
	return c.snapshotLocked(ctx, state)   // 精确重复 → 返回当前快照，不写任何事件
}
```

`FailIntentRevision` 有对称的一段（`:1022-1024`）。

测试 `TestCompletionRejectsLowerFenceAndExactDuplicateIsIdempotent`（`revisions_test.go:274-324`）同时固定了两件事：

- **低 token 被拒且不写事件**（`:287-298`）
- **精确重复不增加事件数且 `Finished` 时间不变**（`:315-323`）

> **⚠️ 诚实标注**：这个"重放→成功"分支**只存在于 `CompleteIntentRevision` 与 `FailIntentRevision`**。
> `NextIntent` 的认领重放、`ReconcileIntent` 的重放都**没有**等价分支——它们会走到 `persistLocked`，拿回 `ErrVersionConflict`。
>
> 这是"**已实现但覆盖面不完整**"，不是"未实现"。

### 7.6 进程重启后如何「接着做」

```go
// coordinator.go:351-422  ensure()
if ok := c.store.LoadState(ctx, taskID); ok {
	json.Unmarshal(stored.Data, &persisted)          // 恢复 intents 与状态
	state := &taskState{... version: stored.Version ...}
	...
}
```

三个要点：

| # | 要点 | 说明 |
| --- | --- | --- |
| 1 | **重建是幂等的**（`:349-350` 注释） | "已有的节点保留它们的状态，所以重启不会丢在途状态" |
| 2 | **先做版本对齐再恢复**（`:371-395`） | 当 `taskRevision` 为 0（旧格式）时，先拉任务重新推导 revision 身份，但**只覆盖身份字段**，运行态从旧记录逐字段抄回来（`:384-394`） |
| 3 | **重启不会重复推进** | `TestCoordinatorRestoresRunningIntentWithoutDoubleAdvance`（`coordinator_test.go:519-545`）断言重启后 `Status` 仍为 `RUNNING`、`Claimed` 不变，且 `NextIntent` 返回 `nil` |

> **⚠️ 必须标注的混淆点**：`coordinator_test.go:519` 的"协调器重启"是**同进程内换一个 Coordinator 实例**。
> 它验证的是"**状态重建逻辑正确**"，**不是**"进程崩溃后恢复"。**两者不能互相替代。**
>
> 这是本章最容易被过度解读的一处。第 8 节会系统整理这类"覆盖边界"。

### 7.7 Outbox 的消费者：一条快路径 + 一条慢路径

| 路径 | 实现 | 说明 |
| --- | --- | --- |
| **慢路径（主）** | `Coordinator.DispatchOutbox`（`:1204-1251`），由 `cmd/fleet-control-plane/main.go:194-207` 的 **500 ms ticker** 调用，每次取 **64 条** | 先 `ClaimOutbox`（`FOR UPDATE SKIP LOCKED`，`coordination.go:259`），成功后 `AckOutbox`，失败则 `ReleaseOutbox`。**它持有 `c.mu`**（`:1211-1212`），注释解释是为了防止"ready 通知超越资源投影"（`:1208-1210`） |
| **快路径（内联）** | `CompleteIntentRevision` 在提交成功后立即试投一次 | 成功就直接 ack（`:824-828`） |

```go
// coordinator.go:824-828
if outboxID != "" && c.enqueue != nil {
	if err := c.enqueue(context.Background(), taskID, enqueueRobots); err == nil {
		_ = c.store.AckOutbox(context.Background(), outboxID)
	}
}
```

**"它持有 `c.mu`"这个细节很重要**：投递 ready 通知本身不需要锁，但**投递的顺序**需要——如果 ready 通知比资源投影先到 robot-2，robot-2 会认领一个"它还不拥有"的资源。

**topic 命名**（`:770-773`）：`robot/<robotID>` 或 `fleet/unbound`。`enqueue` 的真实实现是 `queue.Router.Enqueue`（`fleet/queue/queue.go:56-`），它把 task id 扇出到每个相关机器人的队列 + `any` 队列（`AnyRobot = ""`）。

> **一处值得在书里点出的不一致**：`Commit` 里写入的 outbox `topic` 是 `fleet/unbound`，而 `DispatchOutbox` **完全不读 `topic`**——它只解 payload 里的 `taskId` / `robotIds`（`:1220-1224`）然后调 `enqueue`。
>
> **topic 字段目前是只写不读的存储。**（推断）这是为将来换成真正消息中间件预留的字段。
>
> 这是一个很好的教学点：**"预留字段"是设计债**。它看起来像已实现的抽象，实际上没有消费者。

---

## 8.8 WorldHub 与世界快照

### 8.1 三层结构

```
observation.Envelope ──► worldmodel.Projector ──► worldhub.Hub ──► REST /v1/world + WS
   (输入契约)              (确定性 reducer)          (串行化+持久化+扇出)
```

| 层 | 做什么 | 关键性质 |
| --- | --- | --- |
| **`Projector`** | `Apply(event) (Snapshot, accepted bool, err error)` | **纯函数式 reducer**，四道入口过滤 |
| **`Hub`** | 持 `sync.Mutex`，串行化所有 `Ingest` | 维护有界 delta 环 + 订阅者集合 |
| **持久化** | `NewPersistent` 在启动时 `Load` 或写入空 checkpoint | **失败关闭，不是降级** |

**第四层的"失败关闭"值得展开**（`fleet/worldhub/hub.go`）：

`Ingest` 里**先 `Clone` 投影器再 `Apply`**（`:70-73`），只有 `store.Save` 成功后才 `h.projector = candidate`（`:90`）。

保存失败时：把 `persistenceErr` 置位并**关闭所有订阅者**，之后**所有** `Ingest` / `Snapshot` / `Subscribe` 全部返回该错误（`:64-66,83-88,121-123,129-131`）。

**为什么这样？** 因为一个"世界状态无法持久化"的系统，在重启后会**忘掉一切**——包括"哪个机器人手里有东西"。继续接受观测会让系统在崩溃时产生一个**比停止更糟**的结果：一台以为自己是干净的、其实手握杯子的机器人。

**"失败关闭"在这里的具体含义是：宁可整个系统停下来，也不要产生一个不可信的世界。**

### 8.2 两个都叫「basis」的东西

这是本章最容易混淆的术语。系统里有两个 basis，回答**不同**的问题：

**（a）`harness.EvidenceBasis`** —— "完成判定所依据的**证据基线**"

```go
// core/harness/evaluator.go:31-39
type EvidenceBasis struct {
	WorldRevision          uint64
	EntityObservationCount uint64
	EntitySourceID         string
	EntitySourceSequence   uint64
	RobotSourceID          string
	RobotSourceSequence    uint64
	CommandStartedAt       time.Time
}
```

它回答：*「我是从世界的哪个版本、哪些源序列开始，才允许把后续观测当作**新**证据？」*

由**协调器在认领时**从世界快照抓取并写进 `IntentNode`（`coordinator.go:493-508`），失败时由验证器**逐条比对**。

**（b）`runtime.Command.WorldRevisionBasis uint64`** —— "这条命令是**基于哪个世界版本**规划的"

（`edge/runtime/runtime.go:75`；proto 字段 `world_revision_basis = 14`，`proto/robot/v1/robot.proto:170`。）

这是一个**建议性**字段，传给 Robot Runtime，但 **Runtime 不据此授权**。

### 为什么需要 basis：一个可以永远为真的旧事实

这是 basis 存在的全部理由：

> **「方块在目标区」是一个可以一直为真的旧事实。**

没有 basis，一个 **30 秒前的快照**就能证明一次**刚刚下发**的抓取成功了——因为方块确实在目标区（它已经在那里 10 分钟了）。

basis 把"真"升级为：

> **"在正确的时间窗口内、由正确的源、以更高的序列号新产生的"真。**

### 三种"方块确实在目标区，但这次完成上报仍必须被拒绝"

这道题值得作为练习：

| # | 场景 | 拒绝点 |
| --- | --- | --- |
| 1 | **方块从来没有动过**——它在目标区已经 10 分钟了，robot 的抓取其实失败了 | `evaluator.go:84` `ENTITY_EVIDENCE_NOT_POST_COMMAND_STABLE`（`ObservationCount < Basis+2`）+ `:87` `ENTITY_EVIDENCE_PREDATES_CLAIM`。测试：`TestPreexistingStableWorldCannotProveAClaimedPhysicalIntent`（`coordinator_test.go:183`） |
| 2 | **世界版本根本没前进**——没有新的观测进来 | `evaluator.go:76` `WORLD_REVISION_NOT_ADVANCED` → `Waiting` |
| 3 | **有人从外部把方块挪过去了**——不是这次命令的功劳 | `:90-91` `ENTITY_EVIDENCE_PREDATES_COMMAND`（同源序列不前进）。测试：`TestExternalBlockMovePreventsVerifiedCompletion`（`coordinator_test.go:227`） |
| 4 | （加分）**机器人还夹着东西** | `:139-141` `ROBOT_STILL_HOLDING_ENTITY`（`RetryableFailure`） |
| 5 | （加分）**资源 token 不匹配** | `:150` `FENCING_TOKEN_MISMATCH`（`FailedSafe`） |

**核心概念**：basis 把"命题为真"升级为"命题**因这次动作**而为真"。

**通用 coding agent 没有这个概念，因为它不需要**——但它对任何操作物理世界的系统都是必需的。

### 8.3 `FLEET_WORLD_SNAPSHOT_PATH` 的 checkpoint 语义

`FileStore`（`fleet/worldhub/store.go`）的语义比名字严格得多：

| 性质 | 实现 |
| --- | --- |
| **单主强制** | `OpenFileStore` 用 `unix.Flock(LOCK_EX\|LOCK_NB)` 拿 `<path>.lock`（`:64-71`），**第二个进程直接启动失败** |
| **完整性** | 文件是信封格式 `{schemaVersion, sha256, checkpoint}`（`:40-44`）；`Load` 校验 schema 与 sha256，且 `DisallowUnknownFields` + **拒绝尾随 JSON**（`:165-178`） |
| **持久性** | `Save` 是"写临时文件 → `Sync` → `Rename` → **目录 `Sync`**"（`:123-144`） |

注释 `:143` 的那句话值得抄下来：

> `// A successful rename alone does not guarantee recovery after power loss.`

**"仅仅 rename 成功并不保证掉电后可恢复。"**

这是一个非常容易漏掉的细节：`rename` 是原子的（不会看到半个文件），但 **`rename` 这个目录项的变更本身需要 fsync 才会落盘**。少了目录 `Sync`，掉电后可能出现"文件内容在，但目录项指向旧文件"或反过来。

**注释 `:30-33` 还明确划了边界**：

> 它的建议锁只排斥在**同一文件系统**上使用该 checkpoint 的进程；
> 它**不提供**分布式领导者或 fencing。

**这条自我划界非常重要**——它防止读者把一个单机文件锁误当成分布式协调机制。

### 8.4 恢复语义：旧观测不会变成新证据

`worldmodel.RestoreProjector` 把恢复时的 `observationIDs` 与 `sourceSequence` 存成 `recoveredObservationIDs` / `recoveredSourceSequences`（`checkpoint.go:66-67`）。

此后**任何 `ObservedAt` 早于恢复时刻的观测都被标记为 recovered**，在 `snapshotLocked` 里**一律降级为 `Stale`**（`projector.go:197-198,217-219,226-227,239-240`）。

**这就是那句文档断言的代码依据**：

> 恢复保留世界与源序列身份，**旧观测不会因此变成新证据**。
> —— `docs/architecture/distributed-agentos.md:29`

**为什么必须有这条？** 因为重启后，系统会从磁盘加载一张世界快照。那张快照里的每条事实都是**陈旧**的——它们是在崩溃前观测到的。如果不降级，一次重启就会让所有旧观测"复活"成当前真相。

**这是"重启不等于世界没变"的代码化。**

### 8.5 delta 历史与客户端重同步

| 项 | 值 |
| --- | --- |
| 环容量 | `FLEET_WORLD_DELTA_RETENTION`，默认 **512**（`main.go:314`；`hub.go:104-106`） |
| 重同步判据 | 三选一失败就返回 `worldmodel.ErrResyncRequired`（`hub.go:133-137`） |

三个重同步条件：

```
afterRevision > current                        # 未来游标
len(deltas) == 0 && afterRevision < current    # 环空且落后
afterRevision + 1 < deltas[0].Revision         # 游标落在环外
```

传输层把它翻译成一个**显式消息**（`fleet/server.go:210-212`）：

```json
{"type": "RESYNC_REQUIRED", "afterRevision": ...}
```

客户端必须重新 `GET /v1/world`。

**环不持久化**（`docs/production/architecture.md:93`）：重启后 delta 历史为空，任何 `after_revision > 0` 的订阅都会重同步。

**教学要点**：这是一个"**显式失败**"的好例子。很多增量同步协议的实现是"发现游标对不上就悄悄发全量"——那看起来更友好，但客户端不知道发生了什么，也就无法诊断。**发一个显式的 `RESYNC_REQUIRED`，客户端知道自己错过了东西。**

### 8.6 「旧观测不会变成新证据」的六处强制

| # | 位置 | 拒绝理由 |
| --- | --- | --- |
| 1 | `projector.go:78-80` | 源序列不前进 → 丢弃（乱序/重放） |
| 2 | `projector.go:75-77` | 同一 `observationID` → 丢弃（重复投递） |
| 3 | `projector.go:114-117,197-198` | 恢复前观测 → 事件面标 recovered、快照面标 `Stale` |
| 4 | `harness/evaluator.go:76` | `Snapshot.Revision <= Basis.WorldRevision` → `WORLD_REVISION_NOT_ADVANCED` |
| 5 | `harness/evaluator.go:84,87` | `ObservationCount < Basis+2` → `ENTITY_EVIDENCE_NOT_POST_COMMAND_STABLE`；`ObservedAt` 不晚于认领时刻 → `ENTITY_EVIDENCE_PREDATES_CLAIM` |
| 6 | `harness/evaluator.go:90-91` | 同源但序列号不前进 → `ENTITY_EVIDENCE_PREDATES_COMMAND` |

**全部是拒绝，没有一处是"勉强接受后标注"。**

而且 4/5/6 的返回值是 `Waiting` 或 `RetryableFailure`（**不是 `Satisfied`**），而协调器在 `:717-719` 对非 `Satisfied` 一律返回：

```go
return nil, fmt.Errorf("%w: %s (%s)", ErrWorldNotReady, ...)
```

而 edge worker 收到 409 后**只重试 completion，不重放物理动作**（`edge/worker/worker.go:395-423` `retryWorldCompletion`，只对 `ErrWorldNotReady` 重试，40 次 × 250 ms）。

**最后这一点是全章的关键**：世界还没准备好 → 重试**上报**；**绝不重试物理动作**。

---

## 8.9 边缘运行时：为什么 Runtime 不看命令来源

### 9.1 `Command` 的完整字段清单

`edge/runtime/runtime.go:61-81`，共 **19 个字段**：

```go
type Command struct {
	SchemaVersion      string            // 线协议版本
	CommandID          string            // 服务器生成，不可变
	TaskID             string
	RobotID            string
	Capability         CapabilityName    // 语义能力名，非工具/话题名
	TargetRef          string
	Parameters         map[string]any
	Deadline           time.Time         // 绝对期限
	Lease              time.Duration     // 执行预算
	IdempotencyKey     string
	SafetyProfile      string            // 安全档位
	ApprovalID         string            // 审批凭据
	CatalogRevision    string            // 工具目录版本（stale 则拒绝）
	WorldRevisionBasis uint64            // 规划所依据的世界版本
	ResourceID         string            // 资源归属
	FencingToken       uint64            // 归属纪元
	TaskRevision       uint64
	AggregateVersion   uint64
	StepID             string
}
```

**19 个字段，没有一个叫 `origin`、`brain_id`、`source_plane` 或 `created_by`。**

### 9.2 答案的一半是结构性的

> **`Command` 里根本没有「来源」这个字段，proto 里也没有。**

我逐字段核对了 `proto/robot/v1/robot.proto:156-176` 的 `message SkillCommand`：19 个字段，与 Go 结构一一对应。

`grep -n "origin|brain|source_plane|created_by"` 在 `proto/robot/v1/robot.proto` 与 `proto/fleet/v1/fleet.proto` 上**零命中**（proto 里唯一的 `origin` 是 `fleet.proto:181-183` 的**占用栅格坐标原点**）。

设计文档把这个决定写成了明文规范（`docs/superpowers/specs/2026-08-20-...-design.md:201`）：

> **Robot Runtime 不接收 `brain_id` 或「来自云端/本地」的来源字段。**
> 它只验证工具、参数、安全、期限、幂等和 fencing。

**这是一个非常漂亮的架构决定**：它把"命令不能绕过安全检查"从一条**规则**变成了一个**类型性质**。

规则可以被违反（有人加个 if），类型性质不能——**因为要违反它，你必须先改协议**。

**教学要点**：这是"用类型而不是用纪律来强制约束"的典范。类似的例子还有：`OpsAgent` 不持有任何执行端口（所以"观察者碰不到机器人"是这个类型的性质）；`StepOutcomeAbandoned` 是一个类型（所以"人必须签字"可以被编译器检查）。

### 9.3 强制这一点的五处代码

| # | 位置 | 强制方式 |
| --- | --- | --- |
| 1 | `edge/runtime/runtime.go:61-81` + `proto/robot/v1/robot.proto:156-176` | **类型层面无法表达来源**——不是"不检查"，是"**没有可检查的东西**" |
| 2 | `sim/mujoco/tangying_sim/server.py:429-478` `_validate()` | **唯一门禁**。检查表 13 项（6.3），**没有一项是按来源分支的**。每一项失败都返回稳定错误码，命令不进 `_dispatch` |
| 3 | `edge/robotclient/client.go:399-412` | 默认安全档位**由连接到的 Runtime 的 `RobotProfile` 推导**，不由命令来源推导 |
| 4 | `edge/worker/worker.go:293-296` | worker 在**本地重新物化并重新校验**计划：`manipulation.Plan(...)` → `guard.New(manipulation.Catalog()).Validate(plan)` → `compiler.New().Compile(plan)`。**云端只给了 intent，计划与安全字段是边缘重造的** |
| 5 | `cmd/local-agent/main.go:90` + `safety_profile_test.go:9-18` | 本地形态**必须显式**配置 `robot-safety-profile` |

第 3 点的注释值得引用（`:407-408`）：

> 明文是传输选择，**不是**新适配器支持旧仿真安全档位的证据。

第 4 点被总结成一句可引用的设计规则（`docs/architecture/fleet-cloud.md:202-203`）：

> **LLM 永远不能设置安全字段**（deadline / lease / approval / idempotency 由 edge-worker 本地重造）。

### 9.4 期限与预算是两件事

```go
// edge/runtime/dispatch.go:16-34  CommandAtDispatch
```

| 字段 | 语义 | 谁能改 |
| --- | --- | --- |
| `Deadline` | **绝对期限**——命令的规划期限，**新鲜度占位符** | 计划 |
| `Lease` | **执行预算** | 被能力声明的 `DefaultTimeout` **覆盖**（`:18-20`），上限 `MaxDispatchBudget = 10 * time.Minute`（`:10`），且**只能被 caller context 收紧**（`:28-31`） |

注释 `:13-15` 是一句关键的安全规则：

> 计划中的 deadline 是**新鲜度占位符**，不是任务期限……
> **绝不能**把它应用到**可能已经执行过**的命令的重试上。

**为什么？** 因为一次重试面对的已经不是一个"新命令"了——它面对的是一个**可能已经产生物理效果**的命令。用一个"计划时的期限"去约束它，等于假设它还没跑过。

---

## 8.10 协调器：调度判定逻辑

### 10.1 `NextIntent` 的完整流程

```
NextIntent(ctx, taskID, robotID):
  [C1] robotID 为空 → 拒绝                                    # :428-430
  [C2] validateLeadership(ctx) → ErrLeadershipLost            # :431, :1333-1345
  [C3] state = ensure(ctx, taskID)        # 加载/重建 intent   # :434, :351-422
  [C4] reloadIfNeeded(ctx, state)         # 任务修订变化时重建  # :440, :1350-1366
  [C5] before = cloneTaskState(state)     # 回滚点             # :444
  [C6] reclaimStaleLocked(state)          # 过期 → UNKNOWN_OUTCOME, 不回收  # :445, :324-347
  FOR index, node IN state.intents:
    [C7]  node.Status ∉ {PENDING, READY}         → continue    # :449-451
    [C8]  node.RobotID != "" && != robotID       → continue    # :452-454
    [C9]  !prefixSucceeded(state, index)         → return nil  # :455-457  ← 顺序栅栏
    [C10] task = service.Get(taskID); intents = task.Intent.Tasks()  # :458-462
    [C11] node.CatalogRevision = catalogLookup(robotID)        # :463-472
          目录版本为空 → 拒绝（"robot tool catalog revision is required"）
    [C12] IF isSharedBlockHandoff(intents[index]):             # :473
            c.resources == nil → 拒绝                          # :474-476
            node.ResourceID ?= "block:red-block"               # :477-479
            IF node.FencingToken == 0:                         # :480
                grant = resources.Acquire(resourceID, robotID, resourceTTL)  # :481
                node.FencingToken = grant.Token                # :485
            resources.Validate(resourceID, robotID, token)     # :488
    [C13] node.Started = now                                   # :492
    [C14] IF c.world != nil:                                   # :493
            world = c.world.Snapshot(ctx)                      # :494
            node.WorldRevision = world.Revision                # :498
            node.EntitySourceID/Sequence/Count = world.Entities["red-block"]  # :499-503
            node.RobotSourceID/Sequence = world.Robots[robotID]                # :504-507
    [C15] node.Status = RUNNING; node.Claimed = robotID        # :509-510
          node.CommandID = commandIdentity(taskID, TaskRevision, StepID)      # :511
    [C16] persistLocked("INTENT_CLAIMED", key, payload)        # :513
          失败 → Release 资源租约 + *state = *before + 返回错误  # :517-522  ★
    [C17] publishResource(grant) → WorldHub                    # :523-527
    [C18] advanceTaskState(StateExecuting)                     # :528, :544-559
    return node
  return nil                                                   # :531
```

### C9 是双机协同的引擎

**intent k 只有在 0..k−1 全部 `SUCCEEDED` 时才可认领。**

包注释（`coordinator.go:5-8`）说明了设计意图：

> 当机器人 A 完成它的子任务，协调器把下一个子任务刷成 READY，机器人 B 的 worker 于是可以认领它。

**这是一个"顺序栅栏"（sequential fence），不是并行调度器。** 项目文档明确划了范围（`multi-robot.md:28`）：

> 当前可复现任务是明确有序的双机器人限定场景，**不是任意任务的并行调度器或通用碰撞规避系统**。

### C16 的顺序是刻意设计

**先提交持久化，成功后才把资源归属投影进世界**（`:517-527`）。

测试 `TestResourceOwnershipIsNotPublishedWhenClaimCommitFails`（`coordinator_test.go:88`）固定了这一点。

**它防止什么？** 防止"世界说 robot-1 拥有方块，但协调器没记住"——一个**世界的投影与权威状态不一致**的状态。这种不一致比单纯的提交失败更难处理，因为它会让别的机器人看到一个虚假的归属。

### C18 是乐观的状态机推进

协调器**不能直接跳状态**，它必须走合法路径（`:544-559`）：

```
Observing → Planning → Executing
Verifying → Succeeded
RecoverableFailure → Failed
```

且**错误被忽略**（注释 `:540-543`：并发认领可能已经推进过）。

这是**乐观的状态机推进**，不是事务性的——（推断）属于 6.11 未验证范围的一部分。

**教学要点**：乐观推进的代价是"最终一致"，收益是不需要分布式锁来保护状态转换。对于"推进错了会怎样"的问题，答案是"下一次推进会修正它"——但**前提是状态转换是幂等的**。

---

## 8.11 故障假设清单：已实现 vs 未验证

**这是本章必须最诚实的部分。**

我把三者分开：

- **(A) 已实现且被自动化测试固定**——有可点名的测试；
- **(B) 已实现但测试面窄**——代码在，测试只覆盖了不变量的一部分；
- **(C) 文档明列为未验证**——代码可能在，但没有证据。

### 11.1 故障矩阵与它的真实覆盖

`tests/e2e/test_fleet_faults.py` 的 `FAULT_CHECKS`（`:23-151`）声明了 **10 类故障**。

但文件头注释（`:1-7`）说得非常清楚，这段值得完整引用：

> **只有一个场景跨越活的 OS 进程/网络边界。** 其余场景只针对拥有该不变量的那个窄一致性边界；
> 这让故障注入保持确定性，并避免在生产二进制里塞进一个无认证的混沌端点。

| 故障 | 归类 | 固定它的测试 | **覆盖边界** |
| --- | --- | --- | --- |
| `observation_duplicate_reorder` | **A** | `./core/worldmodel` `TestProjectorRejectsDuplicateAndOutOfOrderSourceSequence` | reducer 单进程 |
| `worker_crash_after_place` | **B** | `./edge/worker` + Python 测试 | **没有真跨进程杀 worker 再验** |
| `coordinator_restart` | **B** | `TestCoordinatorRestoresRunningIntentWithoutDoubleAdvance` | **同进程内换实例，不是真重启进程** |
| `redis_outage` | **B** | `TestQueueOutageLeavesCommittedHandoffOutboxForRecovery` | 用注入的 enqueue 失败模拟，**不真停 Redis** |
| `stale_fencing_token` | **A** | Python `test_physical_command_with_stale_fencing_fails_before_dispatch` | 真正的门禁代码 |
| `receiver_offline_after_handoff` | **A** | `TestReceiverOfflineAfterHandoffCannotClaimOrDeliver` + registry 测试 | 租约过期逻辑 |
| `camera_loss_and_ui_reconnect` | **A** | Go + Python renderer + `web/world_view_test.mjs` | — |
| `external_block_move` | **A** | `TestExternalBlockMovePreventsVerifiedCompletion` | — |
| `versioned_task_update_fencing` | **A** | coordinator 三个测试 + `./tasks` 两个并发写测试 | — |
| `versioned_task_experience_gap` | **A** | `web/app_test.mjs` | 只在前端 |

**真正跨越进程/网络边界的只有一个测试。**

`test_edge_disconnect_reconnect_completes_without_false_advance`（`:175-215`）。它做的事值得完整引用，因为它就是本章「分布式 = 故障假设」的**最佳论证**：

```python
stack.pause_process("edge-robot-1")            # 真的 SIGSTOP 一个 edge-worker 进程
# 等待设备租约过期：断言 not _device_online(stack, "robot-1")   # :182-187
task_id = stack.create_and_approve()
time.sleep(0.75)
assert task["state"] != "SUCCEEDED"            # :192  ← 不会假成功
assert 没有 BLOCK_AVAILABLE 事件                # :193-196 ← 后续意图不被解锁
stack.resume_process("edge-robot-1")           # SIGCONT
# 等设备重新在线，等任务终态
assert final["state"] == "SUCCEEDED"           # :204
assert events.count("BLOCK_AVAILABLE") == 1    # :211  ← 恰好一次，不重复
assert events.count("BLOCK_DELIVERED") == 1    # :212
```

**「恰好一次」在这里被真正跨进程验证了**——这是全仓库**最强**的分布式证据。

### 11.2 文档原文的「仍须独立验证」清单

主清单在 `docs/architecture/distributed-agentos.md:33-39`，逐条照抄：

> ## 仍须独立验证的范围
> - leader fencing 与所有业务提交的同存储原子校验；
> - MySQL、Redis、世界投影与物理结果之间可恢复的资源转移 saga；
> - 数据库/队列切主、网络重排和长期多进程故障验证；
> - 实机传感器质量、坐标标定、匹配策略、实体急停、停止响应和受限任务验收；
> - 目标终端可见帧率、现场容量、备份恢复和长期运维。

### 11.3 逐项回答"哪些故障被处理了"

| 故障 | 代码处理 | 归类 |
| --- | --- | --- |
| **进程重启** | `ensure`、`reloadIfNeeded`；`NewPersistent` + `RestoreProjector`；outbox `OutboxClaimTTL=30s` | **(A/B)** 有实现 + 测试，但测试是**同进程新建实例**，不是真杀进程 |
| **队列宕机（Redis 不可用）** | outbox 保证事实不丢；`main.go:145-151`：`REDIS_ADDR` 为空时 `leaderManager = lease.NewMemoryManager()` | **(B)** 机制在、测试用注入失败模拟 |
| **网络分区** | 设备租约 15 s 过期 → 离线；Link 指数退避 1 s→30 s；控制通道与数据面分离；"断网安全停机，恢复后对账" | **(B)** 语义与机制齐备，**但没有任何测试真的断开网络** |
| **时钟漂移** | ⚠️ **无自动处理**。租约过期用本地 `time.Now()`；`Grant.ExpiresAt` 由持有者本地推算，而真正过期由 Redis `PEXPIRE` 决定 | **(C) 未验证，但有 runbook** |
| **状态分歧** | `worldmodel` 有 `Health.Conflicts` 与 `ErrTransformRevisionConflict`；"held/entity/resource 三源冲突时停止后续动作" | **(A/B)** 冲突**检测**明确；冲突**自动消解不存在**（按设计：交给人） |
| **数据库切主（MySQL failover）** | 无。只用 `LevelReadCommitted` + 行锁 + 版本 CAS，**无主从/切主处理** | **(C) 明确未验证** |
| **跨存储事务** | **明确不存在**：状态/事件/outbox/checkpoint 在同一 MySQL 事务内（好），但**租约在 Redis、世界在文件、任务在图**，三者无法原子 | **(C)** |

**关于时钟漂移，我要纠正一个常见的说法。** 不是"系统忽略时钟漂移"，而是：

> **识别了现象、给了人工处置、装了 NTP 告警，但没有自动检测、没有测试、两个时钟源不一致这一根因未被代码处理。**

runbook 里有一整行（`operations-and-failures.md:24`）：

| 列 | 内容 |
| --- | --- |
| 现象 | **token 早过期、观测 stale、证据时间倒退** |
| 检查 | `date` / NTP、observed/received 时间差 |
| 安全不变量 | **不手改证据时间** |
| 恢复 | 暂停派发，同步时钟，Runtime 重注册并使用新 sequence |
| 防止复发 | chrony/NTP 告警 |

### 11.4 最容易混淆的五处，必须显式标注

| # | 混淆 | 真相 |
| --- | --- | --- |
| 1 | "协调器重启测试验证了崩溃恢复" | `coordinator_test.go:519` 是**同进程新建实例**。它验证"状态重建逻辑正确"，**不是**"进程崩溃后恢复" |
| 2 | "Redis 宕机测试验证了队列宕机" | `TestQueueOutageLeavesCommittedHandoffOutboxForRecovery` 里 **Redis 没有真的宕机**。它验证的是"enqueue 返回错误时的处理" |
| 3 | `why-distributed.md:160` 说 "`cmd/local-agent` 引用云端/fleet \| **0 处**" | **与代码不符**，见下 |
| 4 | `fleet-paper-loop.md:93` 说"云端协调器状态在内存中" | 该文档 `:3`/`:7` **自我声明是历史档案**。HEAD 上 `FLEET_STORE=mysql` 时协调器状态**是持久化的**，`memory`（默认）时才是纯内存 |
| 5 | runbook 的"防止复发"栏 | **写的是待建能力，不是现有保障**。`:34` 写"lease 与提交同存储 fencing、chaos test"、`:56` 写"原子 custody 迁移"、`:25` 写"HA、备份恢复演练"——这三项恰好是被列为**未实现/未验证**的东西 |

**第 5 条是读那份 runbook 最大的陷阱**：必须把「恢复」列（**今天怎么做**）与「防止复发」列（**将来该做什么**）分开，否则会把待办当成已交付。

### 11.5 一处文档与代码冲突：Local Agent 的"0 处"

文档 `docs/architecture/why-distributed.md:160` 有一张表：

| 检查 | 结论 |
| --- | --- |
| `cmd/local-agent` 引用云端 / fleet | **0 处** |

**这条命题在 HEAD 上不成立。** 实测：

```bash
# 1) 直接 import（非测试文件）
go list -f '{{range .Imports}}{{println .}}{{end}}' ./cmd/local-agent | grep tangying
# → 含 edge/worker、fleet/worldhub、middleware/sqlite

# 2) 传递依赖中的 fleet/ 与 cloudclient
go list -deps ./cmd/local-agent | grep -E 'tangying.*(fleet|cloudclient)'
# → fleet/coordinator  fleet/eventlog  fleet/lease  fleet/redis
#    fleet/registry   fleet/telemetry fleet/worldhub
#    edge/cloudclient gen/go/fleet/v1
```

具体引用点：`cmd/local-agent/main.go:30`（`edge/worker`）、`:31`（`fleet/worldhub`）、`middleware/sqlite/fleet.go:11`（`fleet/eventlog`）。

更严重的是：**这些符号真的进了二进制**。`go build ./cmd/local-agent`（80,966,386 字节）后 `strings` 命中 `edge/cloudclient`、`fleet/*`、`gen/go/fleet/v1` **227 次**。

**而且没有任何机械保障阻止它**：架构测试 `tests/architecture/dependencies_test.go:32-40` 的被测包集合**不含 `./cmd/...` 与 `./internal/localapp/...`**；禁列表（`:113-138`）**不含 `fleet` / `cloudclient`**（该文件 grep 零命中）。测试**全 PASS**。

**成因是一处"反向依赖"**：`middleware/sqlite` 依赖 `fleet/eventlog`，并在**打开本地数据库时无条件建 4 张 fleet 表**（`store.go:80`）。

**这就是为什么它值得写进书里**：一份文件说"0 处"，而构建产物说"227 处"，而测试说"没问题"。三者可以同时为真，因为**没有任何一个人或工具在检查这件事**。

**但"零引用"想表达的意思，在运行时层面是真的**：

- `cmd/local-agent` **从不构造任何云端客户端**。它把 `edge/worker` 只当作一个**遥测→观测的映射器**使用（`main.go:281-284` 构造 `worker.Config` 时 `Cloud` / `Source` / `Link` 三个字段**全部留空**）；
- `fleet/worldhub` 也只当**进程内世界投影器**用（`main.go:280`，`worldhub.New(...)` 而非 `NewPersistent`）；
- `internal/localapp` 的传递依赖中**完全没有 fleet 包**，只有 `edge/agent` 与 `edge/runtime`。

**本书采用的准确表述**：

> Local Agent 与云端控制面之间**没有运行时依赖，但有代码复用**。
> `fleet/` 这个目录名不等于"云端专属"——`fleet/worldhub` 与 `fleet/eventlog` 是被两条画像共用的**库**。

文档里的"0 处"应当读作"**0 处云端连接**"，字面表述需要修正。

**教学要点**：这是一个关于"目录名 ≠ 部署归属"的实例。用**目录**来表达**部署边界**，随着代码复用会逐渐失真。更可靠的做法是像这个项目一样，用**装配层**（`cmd/` 与 `internal/`）来表达——因为"构造了什么客户端"是运行时事实，"import 了什么包"只是编译期事实。

### 11.6 一个必须点出的配置坑

```go
// cmd/fleet-control-plane/main.go:145-151
if redisAddr == "" {
	leaderManager = lease.NewMemoryManager()      // ← 进程内 mutex
} else {
	leaderManager = redis.NewLeaseManager(...)
}
```

**这个条件分支意味着：未配 Redis 时，领导者租约退化为进程内 mutex。多实例部署会静默失去互斥。**

（推断）文档未提这一点，但它属于必须标注的坑：**一个"看起来在跑"的多实例部署，如果忘了配 Redis，会失去唯一防脑裂的机制，而且不会有任何报错。**

**教学要点**：这是"降级路径必须显式"的一个反例。一个更好的设计可能是：启动时如果检测到"没有分布式租约后端"，就拒绝以多实例模式启动，或者打印一个**很响的警告**。

---

## 8.12 与通用 coding agent 的分布式差异

| 维度 | coding agent | 本项目 | 代码事实 |
| --- | --- | --- | --- |
| **真相在哪** | 文件系统即真相，可 `stat`/`read` | **世界是真相，只能被观测** | `worldmodel.Snapshot` 的一切都带 `Evidence{ObservationID, SourceID, SourceSequence, ObservedAt}`；**没有"直接读世界"的 API** |
| **部分失败** | 几乎不存在（原子 rename、进程有自己的内存） | **常态** | 7.1 的故障矩阵；`fleet/registry` 的在线判定就是"租约没过期" |
| **单写** | 单进程单写者；`flock` 就够 | **三层**（世界 / 任务图 / 物体） | 6.5 |
| **幂等重试** | 重跑测试是免费的 | **结果未知时禁止自动重试** | `edge/recovery/classifier.go:17-23` 六类分类里，只有 `ObservationWait` / `PolicyRetry` 的 `Retryable=true` |
| **重试预算** | 无 | 有上限，超限即 `BLOCKED` / `FAILED_SAFE` | `edge/worker/worker.go:360,379` `PolicyMaxAttempts` |
| **断网行为** | N/A（本地进程） | **能力变窄，不是系统挂掉** | "确定性语法负责解析；**只读动作照跑，改动机器人的动作停住等人**" |
| **会话长度** | 分钟到小时 | **年** | 需持久化 checkpoint + 版本化迁移；`middleware/sqlite` 的 `PRAGMA table_info(step_runs)` 增量加列迁移 |
| **缺证据时的默认** | 没证据就再跑一次 | **没有新鲜证据就不能推进** | `coordinator.go:676-690`：无世界时记 `WORKER_REPORT_NO_WORLD`（**具名承认**）；有世界但对场景改变类 intent 无谓词时**直接拒绝** `ErrIntentUnverifiable` |

### 最值得写进书里的一条：重试

`coordinator.go:45-57` 的注释把这件事讲透了：

`StatusUnknownOutcome` 被刻意**不**设为 `READY`，因为：

> 把 intent 放回可认领池，会让另一个 worker 重复一次**第一次可能已经发生过**的物理动作——
> 这正是 `core/closedloop` 禁止的事，它的 `UnknownOutcome` 类带有永久禁止自动重试的语义。
> **租约失效告诉我们 worker 停止报告了，它没有告诉我们机器人站着不动。**

### 另一条：谁有权威

coding agent 的门禁是"**测试通过**"。

本项目的门禁是**外部世界的新鲜观测**，而且**这个权威不能由被检查者自己提供**：

| 环节 | 谁提供 | 位置 |
| --- | --- | --- |
| 工具是否改世界 | **Runtime 声明**（不是工具自己说） | `runtime.go:99-103` |
| 证据 | **独立观测源**产生 | `completionRequiresPostCommandEvidence` |
| 校验 | **协调器**执行 | `coordinator.go:694-726` |

**"被检查者不能提供自己的证据"**——这一条在安全设计里是通用原则，在机器人系统里有最直接的物理含义。

---

## 8.13 本章小结

1. **「分布式」是故障假设，不是部署姿势。** 分层与一致性机制的成本，必须用故障集合来偿还；**故障集合没覆盖到的机制，就是纯负债**。README 列的四个资源里，只有一个真的被 owner + lease + fencing 管住——**其余三个要么用更简单的方式解决（地图融合是确定性纯函数），要么根本不存在（充电位）**。

2. **租约过期不等于可以重试。** 租约失效告诉我们 **worker 停止报告了**，不是**机器人站着没动**。这句话是整个系统的分水岭。

3. **三道并联的检查不等于一次原子提交。** 「leader fencing 与所有业务提交的同存储原子校验」仍列在未验证清单上，这是诚实的。

4. **`Command` 里没有"来源"字段。** 这不是"不检查来源"，是"**类型层面无法表达来源**"——把规则变成了性质。

5. **最强的分布式证据只有一个测试**：`test_edge_disconnect_reconnect_completes_without_false_advance` 真的 `SIGSTOP` 一个进程，然后断言"**恰好一次**"。其余九个故障场景都只针对"拥有该不变量的那个窄一致性边界"。

---

## 8.14 教学要点

### 最好的一个例子：「租约过期了，但你不能重试」

几乎所有分布式系统教材都教"租约过期 → 可以安全地重新获取"。这个代码库**明确地、带论证地拒绝**了这条规则。

**它的教学价值在于一次性串起六个概念**（见 6.6）。

**次要例子候选**：

| # | 例子 | 演示什么 |
| --- | --- | --- |
| 1 | `Commit` 的五合一事务（6.7.2） | **为什么 outbox 是必需的** |
| 2 | `worldhub.Subscribe` 的重同步判定（6.8.5） | **delta 流的游标安全边界** |
| 3 | `test_edge_disconnect_reconnect_...`（6.11.1） | **"假成功"是最危险的失败模式** |

### 练习题

**题 1（租约与 fencing）** —— 见 6.6 结尾。

**题 2（事件日志与 Outbox）**

> 请解释：为什么 `INTENT_SUCCEEDED` 与它的 outbox 条目必须写在**同一个事务**里？
> 如果改成"先提交事件，再异步写 outbox"，会出现哪些具体故障？
> 请指出代码里为此做了哪两处保护，并说明为什么 `DispatchOutbox` 里还需要 `OutboxClaimTTL`。

<details>
<summary>答案要点</summary>

**分开写的故障**：提交成功但 outbox 写入前进程崩溃 → **robot-2 永远不会被唤醒**，任务卡在 `BLOCK_AVAILABLE` 之后无人认领，而事件日志显示一切正常。

**这是"静默的不一致"，比崩溃更难发现**——因为它不产生任何错误。

**两处保护**：

1. `persistLocked` 构造单个 `eventlog.CommitRequest{State, Events, Outbox, Checkpoint}`（`coordinator.go:1304-1315`），`Store.Commit` 在一个事务里写全部（`coordination.go:85-188`）；
2. `CompleteIntentRevision` 的**内联快路径**（`:824-828`）在提交后立刻试投并 ack。

**`OutboxClaimTTL = 30s`**（`eventlog/store.go:14`）解决的是**投递者的崩溃**：没有它，一个 claim 之后就死掉的 dispatcher 会让条目永远处于 `claimed` 状态。`ClaimOutbox` 的 `WHERE ... claimed_unix_ms <= claimCutoff`（`coordination.go:258`）是它的实现。

**加分点**：指出 `topic` 字段**只写不读**（6.7.7），说明"预留字段"是设计债。

</details>

**题 3（世界状态与基准）**

> 协调器在认领 intent 时保存了 `WorldRevision`、`EntitySourceSequence`、`RobotSourceSequence` 与 `CommandStartedAt`。
> 请回答：为什么**只**比较「方块在目标区」不够？
> 请给出至少三种「方块确实在目标区，但这次完成上报仍然必须被拒绝」的场景，并指出代码里分别由哪一行拒绝。

<details>
<summary>答案要点（见 6.8.2 的完整表）</summary>

**核心概念**：basis 把"命题为真"升级为"命题**因这次动作**而为真"。

三种场景：

1. **方块从来没有动过**——它在目标区已经 10 分钟了，robot 的抓取其实失败了。拒绝点：`evaluator.go:84` + `:87`。
2. **世界版本根本没前进**。拒绝点：`evaluator.go:76`。
3. **有人从外部把方块挪过去了**。拒绝点：`:90-91`。

**通用 coding agent 没有这个概念，因为它不需要**——但**任何操作物理世界的系统都需要**。

</details>

### 学生最容易误解的点

| # | 误解 | 纠正 |
| --- | --- | --- |
| 1 | "租约过期了就可以重新认领" | **不行。** 租约失效只说明 worker 停止报告 |
| 2 | "fencing token 是一个版本号" | 它是**纪元号**——同一次持有期间恒定，只在转移时递增 |
| 3 | "有租约就可以确认完成" | **不行。** 租约是**授权**，完成需要**世界谓词**。6.5 |
| 4 | "这个系统是事件溯源的" | **不是。** 状态是权威，事件是审计。恢复靠读状态，不靠重放事件 |
| 5 | "有 10 个故障注入测试，所以故障都验证了" | **只有 1 个真正跨越进程/网络边界。** 其余九个只针对窄一致性边界 |
| 6 | "runbook 的『防止复发』栏说明了系统已经有这些保障" | 那栏写的是**待建能力**，不是现有保障 |
| 7 | "标定会过期" | 全仓 grep 不到 `CALIBRATION_STALE` / `CALIBRATION_EXPIRED`。标定**没有失效时间**，只有"缺失"或"与模型/地图不一致" |

---

## 8.15 源码索引

### 租约与 fencing

| 内容 | 位置 |
| --- | --- |
| 四个错误值 / `Grant` / `Manager` 接口 | `fleet/lease/manager.go:9-29` |
| 内存实现（含注入时钟） | `fleet/lease/memory.go:10-116` |
| Redis 五个 Lua 脚本 | `fleet/redis/lease.go:17-65` |
| hash-tag 键推导 | `fleet/redis/lease.go:148-153` |
| `mapLeaseError` | `fleet/redis/lease.go:169-181` |
| 设备租约 15 s | `fleet/gateway/gateway.go:99-100` |
| Runtime 13 项门禁 | `sim/mujoco/tangying_sim/server.py:429-478` |

### 协调器

| 内容 | 位置 |
| --- | --- |
| 包注释：顺序栅栏 | `fleet/coordinator/coordinator.go:1-13` |
| `UNKNOWN_OUTCOME` 的论证 | `coordinator.go:34-58` |
| `reclaimStaleLocked`（核心） | `coordinator.go:303-347` |
| `ensure` 与重启恢复 | `coordinator.go:349-422` |
| `NextIntent` 全流程 | `coordinator.go:424-532` |
| `advanceTaskState` 合法路径 | `coordinator.go:534-559` |
| `prefixSucceeded` 顺序栅栏 | `coordinator.go:561-569` |
| `CompleteIntentRevision` | `coordinator.go:598-837` |
| 重放的幂等分支 | `coordinator.go:642-644` |
| 晚到上报的具名拒绝 | `coordinator.go:648-658` |
| basis 抓取 | `coordinator.go:493-508` |
| 世界谓词验证 | `coordinator.go:694-726` |
| `ReconcileIntent` | `coordinator.go:1053-1167` |
| `DispatchOutbox` | `coordinator.go:1201-1251` |
| `persistLocked` 与幂等键 | `coordinator.go:1279-1321` |
| `validateLeadership` | `coordinator.go:1333-1345` |
| 关键测试 | `coordinator_test.go:88,183,227,307,341,519,604`；`revisions_test.go:274-324` |

### 事件日志与存储

| 内容 | 位置 |
| --- | --- |
| `OutboxClaimTTL` | `fleet/eventlog/store.go:13-14` |
| `Store` 接口五件套 | `fleet/eventlog/store.go:56-72` |
| MySQL DDL 四张表 | `fleet/mysql/coordination.go:15-53` |
| `Commit` 五合一事务 | `coordination.go:85-188` |
| `ClaimOutbox`（`FOR UPDATE SKIP LOCKED`） | `coordination.go:241-290` |

### 世界

| 内容 | 位置 |
| --- | --- |
| `EvidenceBasis` 七字段 | `core/harness/evaluator.go:31-39` |
| `Evaluate` 全部判据 | `core/harness/evaluator.go:69-164` |
| `Hub.Ingest`（失败关闭） | `fleet/worldhub/hub.go:61-116` |
| `FileStore` 三条注释 | `fleet/worldhub/store.go:21-44,64-71,123-144` |
| `Subscribe` 重同步判定 | `fleet/worldhub/hub.go:127-167` |
| `RestoreProjector` 恢复语义 | `core/worldmodel/checkpoint.go:55-69` |
| `Apply` 六道闸 | `core/worldmodel/projector.go:66-95` |

### 边缘与契约

| 内容 | 位置 |
| --- | --- |
| `Command` 19 字段 | `edge/runtime/runtime.go:61-81` |
| `Capability.MutatesWorld` 注释 | `edge/runtime/runtime.go:99-103` |
| `CommandAtDispatch` | `edge/runtime/dispatch.go:8-34` |
| 计划在边缘重造 | `edge/worker/worker.go:293-300` |
| completion 重试（不重放物理动作） | `edge/worker/worker.go:395-423` |
| 恢复分类六类 | `edge/recovery/classifier.go:11-73` |
| `SkillCommand` proto | `proto/robot/v1/robot.proto:156-176` |
| `FleetGateway` 只有两个 RPC | `proto/fleet/v1/fleet.proto:20-27` |
| 数据面与控制面分离的注释 | `proto/fleet/v1/fleet.proto:17-19` |

### 脚本与测试

| 内容 | 位置 |
| --- | --- |
| 证书生成 | `scripts/fleet-certs.sh:1-82` |
| **故障矩阵的诚实声明** | `tests/e2e/test_fleet_faults.py:1-7` |
| 十类故障声明 | `tests/e2e/test_fleet_faults.py:23-151` |
| **唯一跨进程测试** | `tests/e2e/test_fleet_faults.py:175-215` |
| Redis/内存租约的选择分支 | `cmd/fleet-control-plane/main.go:145-151` |

### 文档

| 内容 | 位置 |
| --- | --- |
| 「分布式 = 故障假设」的唯一定义 | `README.md:111` |
| 四条设计表 | `README.md:115-118` |
| **仍须独立验证的范围** | `docs/architecture/distributed-agentos.md:33-39` |
| 与 coding agent 的对照表 | `docs/architecture/why-distributed.md:66-77` |
| 「0 处」——与代码不符 | `docs/architecture/why-distributed.md:160` |
| 分层不是自动 HA | `docs/production/deployment-and-capacity.md:11-39` |
| **故障矩阵主文件**（含安全不变量） | `docs/production/operations-and-failures.md` |
| 多机器人范围限定 | `docs/architecture/multi-robot.md:5-30` |
| Runtime 不接收来源字段 | `docs/superpowers/specs/2026-08-20-...-design.md:201` |

---

**上一章**：[第 7 章 任务编排](../chapters/ch07-orchestration.md) · **下一章**：[第 9 章 本地单机形态](../chapters/ch09-local-single-machine.md) —— 砍掉几乎所有分布式机制之后，剩下的那三样是什么？
