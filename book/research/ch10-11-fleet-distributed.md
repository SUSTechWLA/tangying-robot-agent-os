# 第 10–11 章核心素材：分布式控制面

> **阅读指引（关于篇幅）**：这是一份**素材 + 证据**文件，不是章节正文。
> 其中**可直接改写成正文的论述约 5000 字**，集中在 §1.1/§1.6、§2.2/§2.3、§3.3、
> §4.2/§4.5、§5.2、§6.1、§8.3/§8.4、§9.3/§9.4、§10、§11；
> 其余篇幅是**回答任务书 11 个问题的必备证据**：MySQL/SQLite DDL、19 字段清单、
> 逐行调度伪代码、拒绝点对照表、时序图、故障矩阵，以及任务书要求的「源码索引」。
> 任务书要求「给出真实的 Go 类型与函数签名」「给出源码位置」「给出行号」，
> 按此精度回答 11 个问题必然超出 4000–6000 字的散文体量；**此处按『准确性 >> 篇幅』处理**，
> 并在正文中标注了哪些段落是成文候选。

> **调研对象**：`tangying-robot-agent-os` v0.6.0（`VERSION`），工作树 HEAD `774bd2a2f`。
> **方法**：直接阅读 Go / Python / proto 源码与文档；所有「已实现」判断以代码和测试文件为准，
> 文档仅作为作者意图的证据。凡推断，标注「(推断)」；凡文档与代码冲突，单独开小节标注。
> **写作视角**：作者把「分布式」定义为**故障假设**，不是部署姿势。

**这个定义的确切位置是 `README.md:111`**（全文唯一一处；`grep -rn "故障假设" docs/` 零命中）：

> 「「分布式」在这套系统里不是部署姿势，是**故障假设**。它意味着四件具体的事：」

紧接着的四行（`README.md:115-118`，逐条照抄）——**这张表就是本章第 1–4 节的提纲**：

| 设计 | 解决的问题 |
| --- | --- |
| **单写协调 + 资源归属** | 多个写者不会同时改同一个物理世界（地图、位姿、谁抓着什么、充电位） |
| **租约 + 单调递增的 fencing token** | 旧持有者拿着过期授权写入，会被直接拒绝——脑裂挡在数据层，不靠人盯 |
| **事件与 Outbox + 幂等键** | 先落盘事实再投递；进程重启/队列宕机后能接着做，而不是从头再来 |
| **云端慢环 + 边缘快环** | 实时控制环与安全监督留在机器人上，规划与对账在控制面；**断网安全停机，恢复后对账** |

`README.md:115` 括号里的四个资源名（地图、位姿、谁抓着什么、充电位）正是第 2 节的检验清单——
§2.2 会发现其中**有一个从未被实现**。

与之呼应的是 `docs/architecture/why-distributed.md:49-51`：「这个仓库之所以需要 fencing token、租约、
版本不匹配检测（`internal/discovery` 里真有这个），是因为它选了多机路线。**这些复杂度是那条路线的
真实代价，不是可以省的**——它们是『部分失败成为常态』的直接后果。」
同一份文档 `:46` 还写了反面：「**一台机器人 + 一个人看着 + 非安全关键 → 三层是过度设计。**」

合起来就是本章的判据：**分层与一致性机制的成本，必须用故障集合来偿还；故障集合没覆盖到的机制，就是纯负债。**

---

## 0. 一个必须先说的事实校正

任务书里有一条待验证命题：「`cmd/local-agent` + `internal/localapp` + `middleware/sqlite` **零引用** cloud/fleet」。
文档 `docs/architecture/why-distributed.md:160` 也写着「`cmd/local-agent` 引用云端 / fleet | **0 处**」。

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

具体引用点：`cmd/local-agent/main.go:30`（`edge/worker`）、`:31`（`fleet/worldhub`）、
`middleware/sqlite/fleet.go:11`（`fleet/eventlog`）。

**但「零引用」想表达的意思，在运行时层面是真的**：`cmd/local-agent` 从不构造任何云端客户端。
它把 `edge/worker` 只当作一个**遥测→观测的映射器**使用（`main.go:281-284` 构造 `worker.Config` 时
`Cloud` / `Source` / `Link` / `Runtime` 四个字段**全部留空**，且只调用纯函数
`ObservationsFromTelemetry`，`edge/worker/observation.go:31-56`，不触 `config.Cloud`），
`fleet/worldhub` 也只当**进程内世界投影器**用（`main.go:280`，`worldhub.New(...)` 而非 `NewPersistent`）。
`internal/localapp` 的传递依赖中**完全没有 fleet 包**，只有 `edge/agent` 与 `edge/runtime`
（所以也不能表述成「无 edge 依赖」）。

**依赖真的进了二进制**，不是只在源码里：

```bash
GOCACHE=/tmp/gocache-verify go build -o /tmp/la-verify ./cmd/local-agent   # exit 0, 80,966,386 B
strings -a /tmp/la-verify | grep -oE "tangying-robot-agent-os/(edge/cloudclient|fleet/[a-z]+|gen/go/fleet/v1)" | sort | uniq -c
#    1 edge/cloudclient   1 fleet/coordinator   1 fleet/eventlog   2 fleet/redis
#    1 fleet/telemetry    9 fleet/worldhub    227 gen/go/fleet/v1
```

**并且没有任何机械保障阻止它继续增长。** `tests/architecture/dependencies_test.go:31`
`TestCorePackagesDoNotImportConcreteInfrastructure`（a）被测包集合 `:32-40` **不含 `./cmd/...`
与 `./internal/localapp/...`**；（b）禁列表 `forbiddenImport`（`:113-138`）只含 `database/sql`、
`gen/go/robot/v1`、`middleware/{sqlite,postgres,redis,kafka}`、`google.golang.org/grpc` 与几个 vendor 前缀，
**完全没有 `fleet/` 或 `edge/cloudclient` 规则**（`grep -n "fleet\|cloudclient"` 该文件零命中）。
该测试当前全部 PASS。

**本章采用的准确表述**：Local Agent 与云端控制面之间**没有运行时依赖，但有编译期依赖与代码复用**。
`cmd/local-agent` 直接 import `fleet/worldhub`（`main.go:31`，实际调用在 `:280`）并因此传递引入
`edge/cloudclient` 与 `gen/go/fleet/v1`；`internal/localapp` 侧**确实是零 fleet**
（但仍依赖 `edge/agent`、`edge/runtime`）；`middleware/sqlite` 反向依赖 `fleet/eventlog`
（`fleet.go:11`）并在打开本地库时**无条件**建 4 张 fleet 表（`store.go:80`）。
`fleet/` 这个目录名不等于「云端专属」——`fleet/worldhub` 与 `fleet/eventlog` 是被两条画像共用的库
（`docs/operations/deployment.md:29-61` 的归属表也把 `fleet/worldhub/` 标为「云端+本地」）。

**这个冲突的成因值得在书里点出**：`middleware/sqlite` 作为「本地优先持久化适配器」
（`store.go:1-3` 包注释）却反向依赖了 `fleet/eventlog` 的领域类型，把一个云端领域概念拉进了本地适配器包。
兑现文档承诺的唯一结构性改法是拆出 `middleware/sqlitefleet`，或把 `ensureFleetSchema` 变成可选的
`OpenFleet`。这是一个**在依赖图上真实可见、但文档未承认**的方向性污染。

---

## 1. 租约与 fencing token

### 1.1 系统里有四种租约，它们不是一套机制

这是本章最容易讲错的地方。`fleet/lease` 只定义了一个通用接口，但业务上至少有四个**语义完全不同**的租约：

| 租约 | 资源 ID 形态 | 默认 TTL | 过期后果 | 源码 |
| --- | --- | --- | --- | --- |
| 设备租约 | 每 robot 一条记录 | 15s | 设备判为 `online=false`，协调器拒绝为其分派 | `fleet/registry/registry.go`；`fleet/gateway/gateway.go:99-100` |
| 声明租约（intent claim） | 每 intent 一条 | 2m | **不是释放，是进入 `UNKNOWN_OUTCOME`** | `fleet/coordinator/coordinator.go:153,324-347` |
| 资源租约 | `block:red-block` | 2m | 世界投影里资源降级；harness 拒绝用过期资源确认完成 | `coordinator.go:913`；`core/harness/evaluator.go:157` |
| 领导者租约 | `leader/<worldID>` | 15s | 本协调器所有变更被拒绝（`ErrLeadershipLost`） | `coordinator.go:274,1333-1345` |

**声明租约不是互斥锁**，这是全系统最反直觉的设计，必须单独讲（§1.6）。

### 1.2 真实类型与函数签名

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

- `lease.MemoryManager`（`fleet/lease/memory.go`）——单进程，`sync.Mutex` + `grants map[string]Grant` + `tokens map[string]uint64`，支持注入时钟（`NewMemoryManagerWithClock`）以便测试过期。
- `redis.LeaseManager`（`fleet/redis/lease.go`）——多进程真正的实现，**五个操作各是一段 Lua 脚本**，靠 Redis 单线程保证原子性。

### 1.3 fencing token 只在一处递增

**内存实现**（`memory.go:38`，`Acquire` 抢到空闲租约时；`memory.go:71`，`Transfer` 时）：

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

两个键由 `resourceID` 的 sha256 前 8 字节派生并放进同一个 Redis Cluster hash tag
（`lease.go:148-153`：`tangying:fleet:lease:{<tag>}:state` 与 `:counter`），因此 `INCR` 与 `HSET` 必然同槽、必然同脚本原子执行。

**单调性的关键**：`INCR` 只在**授予新租约**或**转移**时发生，`Renew` / `Validate` / `Release` 都不递增
（`renewLeaseScript` 只 `PEXPIRE` 并返回原 token，`lease.go:30-37`）。
所以 token 是**纪元号（epoch）**，不是版本号——同一次持有期间 token 恒定，正是这一点让
「旧持有者的重放」可以被一个整数比较识别。

### 1.4 旧持有者拿着过期 token 写入：在哪一行被拒绝

系统在不同尺度上有**六个**独立的拒绝点，任何一个生效都足以阻止双写。这是本书最值得画表的一页：

| # | 尺度 | 拒绝点 | 判定 | 错误 |
| --- | --- | --- | --- | --- |
| 1 | Redis（多进程权威） | `fleet/redis/lease.go:50-56` `validateLeaseScript` | `HGET owner ~= ARGV[1] or HGET token ~= ARGV[2]` | `LEASE_HELD` / `STALE_FENCING_TOKEN`，映射于 `:169-181` |
| 2 | 内存（单进程） | `fleet/lease/memory.go:94-106` `validateLocked` | `:99` 先判 `!now.Before(grant.ExpiresAt)` → `ErrLeaseExpired`；`:102` 再判 `grant.Owner != owner \|\| grant.Token != token` → `ErrStaleFencingToken` | — |
| 3 | 任务图聚合（DB） | `fleet/mysql/coordination.go:106-107` | `currentVersion != request.ExpectedVersion \|\| request.State.Version != request.ExpectedVersion+1` → `ErrVersionConflict`；`:121-131` 再用 `UPDATE ... WHERE aggregate_id=? AND version=?` 加一道 `RowsAffected()==1` 检查（`:128`） | `eventlog.ErrVersionConflict` |
| 4 | 协调器（API 入口） | `fleet/coordinator/coordinator.go:639-641` | `if fencingToken != node.FencingToken` → `ErrIntentIdentityConflict`；`:636-638` 同时校验 `taskRevision / aggregateVersion / stepID / commandID` 四个不可变坐标 | `ErrIntentIdentityConflict` |
| 5 | 世界谓词（验证层） | `core/harness/evaluator.go:150,157` | `resource.FencingToken != expected.FencingToken` → `FENCING_TOKEN_MISMATCH`（`FailedSafe`）；租约过期 → `RESOURCE_LEASE_EXPIRED`（`Waiting`） | — |
| 6 | **Robot Runtime（最后一道）** | `sim/mujoco/tangying_sim/server.py:471-472` | `if grant != expected_grant: return "FENCING_TOKEN_STALE"`，发生在 `_validate()` 内、`_dispatch()` 之前 | 命令根本不进执行 |

第 6 点是全书唯一「物理上真的没动」的保证。它的完整前置检查链在同一函数里
（`server.py:429-478`，按顺序）：

```
CANCELLED → ROBOT_COMMISSIONING_ACTIVE → SCHEMA_VERSION_UNSUPPORTED
→ COMMAND_EXPIRED(deadline) → LEASE_REQUIRED(lease_ms==0)
→ IDEMPOTENCY_KEY_REQUIRED → ROBOT_ID_MISMATCH → TOOL_CATALOG_STALE
→ TOOL_CATALOG_REVISION_REQUIRED / FENCING_TOKEN_REQUIRED
→ FENCING_TOKEN_STALE → SAFETY_PROFILE_REJECTED → EMERGENCY_STOP_LATCHED
```

**注意 `:462-469` 的 `allow_monotonic_grant_adoption`**：Runtime 允许「同一 owner、token 严格更大」时
**单向前进式采纳**新 token。这是为了让云端的 transfer 结果能推进本地纪元，但它同时意味着
**Runtime 侧不能独自证明 token 没被跳号**——跳号检测依赖云端 `INCR` 的权威性。这一条属于 §2.3 的分歧风险。

### 1.5 时序图（文字版）：双机交接 + 旧持有者写入

```
时刻  robot-1 worker        Fleet Coordinator(leader A)      Redis lease          robot-2 worker
 t0   │                     │                               │                    │
 t1   │ POST intents/next ──►│                               │                    │
 t2   │                     │ validateLeadership() :1333     │                    │
 t3   │                     │  Validate(leader/world-1,A,1)─►│ ok                 │
 t4   │                     │ reclaimStaleLocked() :324      │                    │
 t5   │                     │ prefixSucceeded(i=0) :562      │                    │
 t6   │                     │ Acquire(block:red-block,      │                    │
 t7   │                     │        robot-1, 2m) ──────────►│ INCR counter → 1   │
 t8   │                     │ store.Commit(INTENT_CLAIMED)   │                    │
 t9   │                     │  CAS version, event, checkpoint│                    │
t10   │◄─ IntentNode{token:1,│  publishResource() → WorldHub  │                    │
 t11  │   worldRevision,    │   (ResourceUpsert, sourceSeq=1)│                    │
 t12  │   commandId, stepId}│                               │                    │
t13  │ Invoke(Command{fencingToken:1}) ──── Robot Runtime: _validate() 通过 ──────►│
t14  │ 物理动作：robot-1 把红方块放进 handoff-zone                                  │
t15  │ POST intents/0/complete {token:1,...} ──►│                                  │
t16  │                     │ CompleteIntentRevision :602   │                    │
t17  │                     │  ① token == node.token :639   │                    │
t18  │                     │  ② harness.Evaluate :703      │                    │
t19  │                     │     EntityInside + EntityStable(2)                    │
t20  │                     │     + RobotHeld(robot-1)=="" + SourceFresh            │
t21  │                     │  ③ Transfer(block, robot-1 →  │                    │
t22  │                     │     robot-2, oldToken=1) ────►│ INCR counter → 2   │
t23  │                     │  next.Status = READY :759     │                    │
t24  │                     │  outbox: "<task>/intent/1/ready"  topic "robot/robot-2"
t25  │                     │  store.Commit(BLOCK_AVAILABLE, outbox) ── 同一事务   │
t26  │                     │ DispatchOutbox(500ms ticker, main.go:194-207)        │
t27  │                     │  ClaimOutbox(FOR UPDATE SKIP LOCKED) → enqueue ─────►│
t28  │                     │  AckOutbox                                     t29  │ 认领，token=2
      ⋮                                                                          ⋮
 ══ 故障：robot-1 的进程在 t15 与 t28 之间被 kill，随后云端换主 ══
t30  │ (旧的 leader A 仍在跑) │                               │                    │
t31  │                     │ 换主：B.Acquire("leader/world-1") → counter=2       │
t32  │ (A 收到 robot-1 的重放)│                               │                    │
t33  │  POST complete{token:1}─►│ A.validateLeadership() :1341                 │
t34  │                     │  Validate(leader/world-1,A,1) ────────────────► 失败 │
t35  │◄─ 409 ErrLeadershipLost                              │                    │
t36  │ (即使 A 侥幸通过)      │ CompleteIntentRevision 里 :639 token 比对       │
t37  │                     │  若拿到的是新纪元，token 必然不匹配 → 拒绝      │
```

**结论**：拒绝是**两道独立机制**——领导者租约（阻止旧协调器工作）+ 资源 token（阻止旧持有者写世界）。
第 3 点（DB 版本 CAS）是第三道，它拦的是同一聚合上的并发提交。三道都失败才会双写；
这正是 §8 里「leader fencing 与所有业务提交的同存储原子校验」仍列在**未验证**清单的原因：
它们目前是**三道并联的检查**，不是**一次原子提交**。

### 1.6 租约过期 ≠ 可以重试（本章的核心教学点）

`reclaimStaleLocked`（`coordinator.go:324-347`）是全书最值得逐行讲的一段代码。它对过期 RUNNING intent 做的事是：

```go
node.Status = StatusUnknownOutcome   // :338
node.Finished = now
node.Error = "claim lease expired with the outcome unknown; reconcile before acting again"
// Claimed 故意保留 —— "哪个 worker 在租约失效时持有它" 是对账要问的第一个问题
```

源码注释 `:303-323` 把设计变更写成了论证：

> 「它**曾经**把它们退回 READY，理由是『崩溃或断连的 worker 绝不能永远阻塞任务』。
> 这个理由对 **worker** 是对的，对**机器人**是错的：租约失效告诉我们 worker 停止报告了，
> **不是**机器人站着没动。」

退出该状态只有一条路：`ReconcileIntent(ctx, taskID, index, decision, actor, note)`
（`coordinator.go:1065`），且**必须同时提供人和理由**（`:1074-1079`），
决策只有两种：`NEVER_ACTED`（唯一能把 intent 放回可认领池的决策，`:1127-1132`）与
`ABANDON`（判失败，`:1133-1137`）。`FencingToken` 在对账时被清零（`:1146`），
确保下一次认领拿到**严格更大**的新 token。

晚到的完成上报不会被静默丢弃，而是被**具名拒绝**（`coordinator.go:648-658`）：

```go
return nil, fmt.Errorf("%w: intent %d held by %s since %s; reconcile it before reporting a result",
    ErrClaimExpired, index, node.Claimed, node.Started.UTC().Format(time.RFC3339))
```

注释 `:649-654` 解释了为什么错误必须具名：「『你来晚了，这个已经不能认领』和
『机器人可能动过，而且没人知道』需要完全不同的下一步。」

---

## 2. 单写协调与资源归属

### 2.1 系统在三个尺度上防「多个写者同时改同一物理世界」

| 尺度 | 机制 | 源码 |
| --- | --- | --- |
| **世界** | 领导者租约 `leader/<worldID>`；每次变更前 `validateLeadership` | `coordinator.go:270-283`（Acquire）、`:285-301`（Renew）、`:1333-1345`（Validate）；续租 ticker 在 `cmd/fleet-control-plane/main.go:176-193`，周期 `leaderTTL/3` |
| **任务图** | 聚合版本 CAS：`SELECT version ... FOR UPDATE` + `WHERE version = ?` + `RowsAffected()==1` | `fleet/mysql/coordination.go:96-98,121-131` |
| **单个物理对象** | 资源租约 + fencing token，且**必须被世界谓词再次复核** | `coordinator.go:477-491`（认领时）、`core/harness/evaluator.go:143-158`（验证时） |

第三层是机器人系统区别于普通分布式系统的关键：
**租约持有 ≠ 世界谓词成立**。协调器不会因为「我持有 token」就接受完成，
它要求 `EntityInside + EntityStable(2) + RobotHeld(empty) + SourceFresh` 同时为真
（`coordinator.go:694-726`，谓词构造见 `:938-959`）。

### 2.2 资源归属的建模：诚实清单

任务是「资源（地图、位姿、谁抓着什么、充电位）的归属是如何建模的」。**答案是：只有一项真正被建模为归属。**

| 资源 | 建模方式 | 是否有归属/互斥 | 源码 |
| --- | --- | --- | --- |
| 共享红方块 | `worldmodel.ResourceState{ResourceID, Owner, FencingToken, ExpiresAt, Evidence}` | **是**，唯一被 lease+fencing 管的资源 | `core/worldmodel/types.go:59-66`；资源 ID 常量 `coordinator.go:913` |
| 「谁抓着什么」 | `worldmodel.RobotState.Held string`，来自遥测 `held` 字段 | **否**，是**观测**而非授权；`RobotHeld` 只作谓词 | `core/worldmodel/types.go:33`；`proto/fleet/v1/fleet.proto:164`；谓词 `core/worldmodel/predicate.go:74-88` |
| 位姿 | `RobotState.Pose` / `EntityState.Pose` + `Freshness` | **否**，只有新鲜度，没有排他 | `core/worldmodel/types.go:31,50` |
| 地图 | 每机器人本地占用栅格经 `fleet/fusion` **确定性纯函数**融合（max 合并、按 id 去重取高置信度） | **否**，无归属概念 | `fleet/fusion/fusion.go`；文档 `docs/architecture/fleet-cloud.md:189-192` |
| 静态几何 | `EntityState.Attributes["static"]=="true"` → 视为**不可变的版本化 map/model 事实**，永不过期，只在显式 update/delete 或 transform revision 变化时改变 | **否** | `core/worldmodel/projector.go:228-232` |
| 充电位 | **不存在**。全仓库 `grep -rn "charg\|dock"`（排除 docker）在 `fleet/ edge/ core/ skills/` 下**零命中** | — | 未建模 |

**这张表要对着 `README.md:115` 读**。README 把「地图、位姿、谁抓着什么、充电位」并列为
「资源归属」应当覆盖的四个对象；代码事实是：**四个里只有一个（谁抓着什么，且只对共享红方块）
真的被 owner + lease + fencing 管住**。地图与位姿只有新鲜度，没有排他；充电位完全不存在。
这不是文档说谎，而是**README 那句话描述的是设计意图的完整清单，实现只落地了其中一格**——
(推断) 多机充电调度属于未来工作，因为当前可复现场景是固定的两台机器人 + 一个共享方块
（`docs/architecture/multi-robot.md:28`：「当前可复现任务是明确有序的双机器人限定场景，
**不是任意任务的并行调度器或通用碰撞规避系统**」）。

**leas 的资源粒度**：资源 ID 是一个**字符串键**，调用方自定。
生产代码里只有两种实际取值：`leader/<worldID>`（每个世界一把）与 `block:red-block`（每个共享物体一把）。
`intent claim` 走的是 `IntentNode.Started` 时间戳而非 `lease.Manager`——它是**协调器内的软租约**
（`coordinator.go:332-337` 用 `now.Sub(node.Started) > c.claimLease` 判定），
只有共享交接的**物体**才走真正的 `lease.Manager`。这个区分很重要：
**「声明租约」不是通过 `fleet/lease` 实现的**，它没有 fencing token。

### 2.3 一个必须标注的分歧风险：存在两个 fencing 纪元

- **云端纪元**：`fleet/redis/lease.go` 的 `:counter`，由 `Acquire` / `Transfer` 递增。
- **仿真世界纪元**：`sim/mujoco/tangying_sim/shared_handoff.py:45` 的 `self._fencing_token = 1`，
  由 `transfer()` 递增（`shared_handoff.py:100-108`，不匹配抛 `StaleHandoffToken`）。

两者通过 `server.py:474-476` 的 `world.adopt_fencing_token(command.fencing_token)` 对齐
（RoboCasa 侧实现见 `sim/robocasa/tangying_robocasa/world.py:151,499-500`），
且只在 `allow_monotonic_grant_adoption` 为真时生效（`server.py:462-469`）。

**这是「旧观测/旧状态不会变成新证据」的一个边界情况**：世界侧会接受一个**更大的** token 并
单向前进，它无法独立验证这个跳号是合法的转移还是被伪造的。本项属于 §8 未验证范围。
文档 `docs/production/operations-and-failures.md:106` 也明确写了：「不得……**降低 token**……
来伪造支持。」

---

## 3. 事件日志与 Outbox

### 3.1 真实表结构（MySQL，`fleet/mysql/coordination.go:15-53`）

```sql
CREATE TABLE IF NOT EXISTS fleet_graph_states (          -- :15-20  聚合当前状态（快照）
  aggregate_id VARCHAR(128) PRIMARY KEY,
  version BIGINT UNSIGNED NOT NULL,
  data JSON NOT NULL,
  updated_unix_ms BIGINT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS fleet_domain_events (         -- :21-35  不可变事实流
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

CREATE TABLE IF NOT EXISTS fleet_outbox (                -- :36-46  待投递
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

CREATE TABLE IF NOT EXISTS fleet_checkpoints (           -- :47-53  恢复游标
  aggregate_id VARCHAR(128) PRIMARY KEY,
  version BIGINT UNSIGNED NOT NULL,
  event_cursor VARCHAR(256) NOT NULL,
  data JSON NOT NULL,
  created_unix_ms BIGINT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**关键设计**：状态表与事件表**不是**事件溯源（event sourcing）的两个视图。
`fleet_graph_states.data` 是**权威状态**（`persistedState`，`coordinator.go:202-207`），
事件表是**审计与追溯**（`DomainEvents` 只被读，从不被重放来重建状态）。
`coordinator.go:170-173` 的注释说明了原因：协调器「首次看到一个任务时从持久化任务重建视图，
因此协调器自己不需要持久化存储」——**真相在 `fleet_graph_states`，不在事件流**。
（这一点与常见 CQRS 教材不同，讲课时要明确指出。）

### 3.2 一次 `Commit` 就是一个事务

`Store.Commit`（`coordination.go:85-188`）在一个 `LevelReadCommitted` 事务里做五件事：

1. `SELECT version ... FOR UPDATE` 拿行锁，做 CAS 前置检查（`:96-108`）；
2. 写/更新 `fleet_graph_states`（`:114-132`）；
3. 插入 `fleet_domain_events`（`:136-154`）；
4. 插入 `fleet_outbox`（`:155-166`）；
5. `INSERT ... ON DUPLICATE KEY UPDATE` 更新 `fleet_checkpoints`，且**用 `IF(incoming.version >= ...)` 保证版本只增不减**（`:172-181`）。

### 3.3 为什么「先落盘事实再投递」

不是风格选择，是三个可验证的后果：

1. **投递失败不丢事实。** `DispatchOutbox`（`coordinator.go:1204-1251`）投递失败时调
   `ReleaseOutbox` 清空 claim（`:1236`），条目留在 outbox，下一轮重试。
   测试 `TestQueueOutageLeavesCommittedHandoffOutboxForRecovery`（`fleet/coordinator/coordinator_test.go:307`）
   与 `TestOutboxDispatcherRecoversAfterPublisherReturns`（`:341`）固定了这一行为。
2. **崩溃点任意，状态一致。** `fleet/eventlog/store.go:13-14` 的 `OutboxClaimTTL = 30 * time.Second`
   让「claim 之后崩溃」的条目自动重新可见（`coordination.go:254,258`），
   注释写得很直白：`// OutboxClaimTTL makes a crashed dispatcher claim recoverable.`
3. **不变量可以在提交点检查。** 因为事件、outbox、checkpoint 与状态同事务，
   不可能出现「事件说成功、状态说没成功」的裂口。这是 §1.5 三道防线里唯一**原子**的那一道。

### 3.4 幂等键：谁生成、长什么样、在哪里去重

全部由协调器在 `persistLocked`（`coordinator.go:1279-1321`）里用**确定性字符串**生成，
不是 UUID——这是刻意的：同一次逻辑变更重放时键必须相同。

| 事件 | 幂等键 | 源码 |
| --- | --- | --- |
| `FLEET_GRAPH_CREATED` | `taskID + "/graph-created"` | `coordinator.go:405` |
| `INTENT_CLAIMED` | `fmt.Sprintf("%s/intent/%d/claim/%d", taskID, index, state.version+1)` | `:513` |
| `INTENT_SUCCEEDED` / `BLOCK_AVAILABLE` | `fmt.Sprintf("%s/intent/%d/succeeded", taskID, index)` | `:809` |
| `INTENT_FAILED` | `fmt.Sprintf("%s/intent/%d/failed", taskID, index)` | `:1035` |
| `INTENT_RECONCILED` | `fmt.Sprintf("%s/intent/%d/reconciled", taskID, index)` | `:1148` |
| `INTENT_CLAIM_EXPIRED` | `fmt.Sprintf("%s/reclaim/%d", taskID, state.version+1)` | `:1106,1183` |

`event_id` 另有一套：`fmt.Sprintf("%s/%020d/%s", state.taskID, nextVersion, eventType)`（`:1294`）。

**去重发生在三处，语义不同**：

- **MySQL 唯一键**（`coordination.go:33`，`UNIQUE KEY fleet_events_idempotency`）——**最后兜底**。
  但它几乎不会被触发，因为版本 CAS（`:106-107`）会先失败：重放的 `ExpectedVersion` 落后于
  已提交版本，直接返回 `ErrVersionConflict`。
- **内存 store 显式查重**（`fleet/eventlog/memory.go:62-68`）——返回
  `duplicate event id "..."` / `duplicate event idempotency key "..."`，**是错误，不是成功**。
- **协调器状态检查（真正处理重放的地方）**——`coordinator.go:642-644`：

```go
if node.Status == StatusSucceeded && node.Claimed == robotID {
    return c.snapshotLocked(ctx, state)   // 精确重复 → 返回当前快照，不写任何事件
}
```

`FailIntentRevision` 有对称的一段（`:1022-1024`）。
测试 `TestCompletionRejectsLowerFenceAndExactDuplicateIsIdempotent`
（`fleet/coordinator/revisions_test.go:274-324`）同时固定了两件事：
**低 token 被拒且不写事件**（`:287-298`），**精确重复不增加事件数且 `Finished` 时间不变**（`:315-323`）。

> **诚实标注**：这个「重放→成功」分支**只存在于 CompleteIntentRevision 与 FailIntentRevision**。
> `NextIntent` 的认领重放、`ReconcileIntent` 的重放都没有等价分支——它们会走到 `persistLocked`，
> 拿回 `ErrVersionConflict`。这是「已实现但覆盖面不完整」，不是「未实现」。

### 3.5 进程重启后如何「接着做」

```go
// coordinator.go:351-422  ensure()
if ok := c.store.LoadState(ctx, taskID); ok {
    json.Unmarshal(stored.Data, &persisted)          // 恢复 intents 与状态
    state := &taskState{... version: stored.Version ...}
    ...
}
```

三个要点：

1. **重建是幂等的**（`:349-350` 注释）：「已有的节点保留它们的状态，所以重启不会丢在途状态。」
2. **先做版本对齐再恢复**（`:371-395`）：当 `taskRevision` 为 0（旧格式）时，先拉任务重新推导
   revision 身份，但**只覆盖身份字段**（`StepID/TaskRevision/AggregateVersion/SemanticFingerprint`），
   运行态从旧记录逐字段抄回来（`:384-394`）。
3. **重启不会重复推进**：`TestCoordinatorRestoresRunningIntentWithoutDoubleAdvance`
   （`fleet/coordinator/coordinator_test.go:519-545`）断言重启后 `Status` 仍为 `RUNNING`、
   `Claimed` 不变，且 `NextIntent` 返回 `nil`（不重复认领）。

### 3.6 Outbox 的消费者是谁

**两个消费者，一条快路径 + 一条慢路径**：

- **慢路径（主）**：`Coordinator.DispatchOutbox(ctx, limit)`
  （`coordinator.go:1204-1251`），由 `cmd/fleet-control-plane/main.go:194-207` 的
  **500ms ticker** 调用，每次取 64 条。它先 `ClaimOutbox`（`FOR UPDATE SKIP LOCKED`，
  `coordination.go:259`），投递成功后 `AckOutbox`，失败则 `ReleaseOutbox`。
  它持有 `c.mu`（`:1211-1212`），注释解释是为了防止「ready 通知超越资源投影」（`:1208-1210`）。
- **快路径（内联）**：`CompleteIntentRevision` 在提交成功后立即试投一次，
  成功就直接 ack（`coordinator.go:824-828`）：

```go
if outboxID != "" && c.enqueue != nil {
    if err := c.enqueue(context.Background(), taskID, enqueueRobots); err == nil {
        _ = c.store.AckOutbox(context.Background(), outboxID)
    }
}
```

`enqueue` 的真实实现是 `queue.Router.Enqueue`（`fleet/queue/queue.go:56-`），
它把 task id 扇出到每个相关机器人的队列 + `any` 队列（`AnyRobot = ""`，`queue.go:21`）。
topic 命名见 `coordinator.go:770-773`：`robot/<robotID>` 或 `fleet/unbound`。

**注意一个不一致（值得在书里点出）**：`Commit` 里写入的 outbox `topic` 是
`fleet/unbound`，而 `DispatchOutbox` **完全不读 `topic`**——它只解 payload 里的
`taskId`/`robotIds`（`:1220-1224`）然后调 `enqueue`。topic 字段目前是**只写不读**的存储。
(推断) 这是为将来换成真正消息中间件预留的字段。

---

## 4. WorldHub 与世界快照

### 4.1 三层结构

```
observation.Envelope ──► worldmodel.Projector ──► worldhub.Hub ──► REST /v1/world + WS /v1/world/events/ws
   (输入契约)              (确定性 reducer)          (串行化+持久化+扇出)
```

- **`Projector`**（`core/worldmodel/projector.go`）是纯函数式 reducer：
  `Apply(event) (Snapshot, accepted bool, err error)`（`:66-95`）。
  四道入口过滤，顺序固定：
  `worldID` 不匹配 → `ErrWorldMismatch`（`:72-74`）；
  `observationID` 已见 → 静默丢弃（`:75-77`）；
  `SourceSequence <= 高水位` → 静默丢弃（`:78-80`）；
  `TransformRevision` 与已接受源冲突 → `ErrTransformRevisionConflict`（`:81-83`）。
- **`Hub`**（`fleet/worldhub/hub.go`）持有 `sync.Mutex`，串行化所有 `Ingest`
  （`:61-116`），维护一个有界 delta 环（`:103-106`）与订阅者集合（`:107-114`）。
- **持久化**：`NewPersistent`（`:39-59`）在启动时 `Load` 或写入空 checkpoint；
  `Ingest` 里**先 `Clone` 投影器再 `Apply`**（`:70-73`），只有 `store.Save` 成功后才
  `h.projector = candidate`（`:90`）。保存失败会把 `persistenceErr` 置位并**关闭所有订阅者**，
  之后所有 `Ingest`/`Snapshot`/`Subscribe` 全部返回该错误（`:64-66,83-88,121-123,129-131`）
  ——这是**失败关闭**，不是降级。

### 4.2 basis：两种 basis，不要混为一谈

这是本章最容易混淆的术语。系统里有两个都叫「basis」的东西，回答不同的问题：

**(a) `harness.EvidenceBasis`** —— 「完成判定所依据的**证据基线**」，回答
*「我是从世界的哪个版本、哪些源序列开始，才允许把后续观测当作**新**证据？」*
（`core/harness/evaluator.go:31-39`）：

```go
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

它由**协调器在认领时**从世界快照抓取并写进 `IntentNode`
（`coordinator.go:493-508`：`WorldRevision`、`EntitySourceID/Sequence/Count`（取 `red-block`）、
`RobotSourceID/Sequence`），失败时由验证器逐条比对。

**(b) `runtime.Command.WorldRevisionBasis uint64`** —— 「这条命令是**基于哪个世界版本**规划的」，
是一个**建议性**字段，传给 Robot Runtime 但 Runtime 不据此授权。
（`edge/runtime/runtime.go:75`；proto 字段 `world_revision_basis = 14`，
`proto/robot/v1/robot.proto:170`。）

**为什么需要 basis**：因为「方块在目标区」是一个**可以一直为真的旧事实**。
没有 basis，一个 30 秒前的快照就能证明一次刚刚下发的抓取成功了。
basis 把「真」变成「**在正确的时间窗口内、由正确的源、以更高的序列号新产生的**真」。

### 4.3 `FLEET_WORLD_SNAPSHOT_PATH` 的 checkpoint 语义

`cmd/fleet-control-plane/main.go:311-329` 的 `buildWorld`：未设置 → `worldhub.New`（内存世界）；
设置 → `worldhub.OpenFileStore(path)` + `worldhub.NewPersistent`。

`FileStore`（`fleet/worldhub/store.go`）的语义比名字严格得多：

- **单主强制**：`OpenFileStore` 用 `unix.Flock(LOCK_EX|LOCK_NB)` 拿 `<path>.lock`（`:64-71`），
  第二个进程直接启动失败。注释 `:30-33` 明确划界：
  > 「它的建议锁只排斥在同一文件系统上使用该 checkpoint 的进程；它**不提供**分布式领导者或 fencing。」
- **完整性**：文件是信封格式 `{schemaVersion, sha256, checkpoint}`（`:40-44`），
  `Load` 校验 schema 与 sha256，且 `DisallowUnknownFields` + 拒绝尾随 JSON（`:165-178`）。
- **持久性**：`Save` 是「写临时文件 → `Sync` → `Rename` → **目录 `Sync`**」（`:123-144`），
  注释 `:143`：`// A successful rename alone does not guarantee recovery after power loss.`
- **恢复语义**：`worldmodel.RestoreProjector` 把恢复时的 `observationIDs` 与
  `sourceSequence` 存成 `recoveredObservationIDs` / `recoveredSourceSequences`
  （`checkpoint.go:66-67`），此后**任何 `ObservedAt` 早于恢复时刻的观测都被标记为 recovered**，
  在 `snapshotLocked` 里一律降级为 `Stale`（`projector.go:197-198,217-219,226-227,239-240`）。

这就是那句文档断言的代码依据：**「恢复保留世界与源序列身份，旧观测不会因此变成新证据」**
（`docs/architecture/distributed-agentos.md:29`）。

### 4.4 delta 历史与客户端重同步

- 环容量 = `FLEET_WORLD_DELTA_RETENTION`（默认 512，`main.go:314`；`hub.go:104-106` 保留最后 N 条）。
- 订阅时游标校验（`hub.go:133-137`）三选一失败就返回 `worldmodel.ErrResyncRequired`：
  `afterRevision > current`（未来游标）、
  `len(deltas)==0 && afterRevision < current`（环空且落后）、
  `afterRevision+1 < deltas[0].Revision`（游标落在环外）。
- 传输层把它翻译成一个显式消息（`fleet/server.go:210-212`）：
  `{"type": "RESYNC_REQUIRED", "afterRevision": ...}`，客户端必须重新 `GET /v1/world`。
- **环不持久化**（`docs/production/architecture.md:93`、`deployment-and-capacity.md`）：
  重启后 delta 历史为空，任何 `after_revision > 0` 的订阅都会重同步。

### 4.5 「旧观测不会变成新证据」在代码里的六处强制

| # | 位置 | 拒绝理由 |
| --- | --- | --- |
| 1 | `projector.go:78-80` | 源序列不前进 → 丢弃（乱序/重放） |
| 2 | `projector.go:75-77` | 同一 `observationID` → 丢弃（重复投递） |
| 3 | `projector.go:114-117,197-198` | 恢复前观测 → 事件面标 recovered、快照面标 `Stale` |
| 4 | `harness/evaluator.go:76` | `Snapshot.Revision <= Basis.WorldRevision` → `WORLD_REVISION_NOT_ADVANCED`（`Waiting`） |
| 5 | `harness/evaluator.go:84,87` | `ObservationCount < Basis+2` → `ENTITY_EVIDENCE_NOT_POST_COMMAND_STABLE`；`ObservedAt` 不晚于认领时刻 → `ENTITY_EVIDENCE_PREDATES_CLAIM` |
| 6 | `harness/evaluator.go:90-91` | 同源但序列号不前进 → `ENTITY_EVIDENCE_PREDATES_COMMAND` |

**全部是拒绝，没有一处是「勉强接受后标注」。** 且 4/5/6 的返回值是 `Waiting` 或
`RetryableFailure`（不是 `Satisfied`），而协调器在 `:717-719` 对非 `Satisfied` 一律
`return nil, fmt.Errorf("%w: %s (%s)", ErrWorldNotReady, ...)`。
edge worker 收到 409 后**只重试 completion，不重放物理动作**
（`edge/worker/worker.go:395-423` `retryWorldCompletion`，只对 `ErrWorldNotReady` 重试，40 次 × 250ms）。

---

## 5. 边缘运行时：`edge/runtime.Command`

### 5.1 完整字段清单

`edge/runtime/runtime.go:61-81`，共 19 个字段：

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
	Lease              time.Duration     // 执行预算（见 dispatch.go）
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

能力枚举 `runtime.go:31-39`（7 个）：`state.get` / `observe_scene` / `navigation.navigate` /
`arm.move` / `manipulation.pick` / `manipulation.place` / `safety.emergency_stop`。
`Capability` 结构（`:85-104`）额外携带 `MutatesWorld bool`，
注释 `:99-103` 写明用途：**「Agent 的闭环门禁使用 Runtime 自己声明的这个字段，
这样新增一个 adapter 工具就无法悄悄跳过动作后验证。」**

期限与预算的分离在 `edge/runtime/dispatch.go:16-34` `CommandAtDispatch`：
`Lease` 被**能力声明的 `DefaultTimeout` 覆盖**（`:18-20`），上限 `MaxDispatchBudget = 10 * time.Minute`（`:10`），
并且**只能被 caller context 收紧**（`:28-31`），注释 `:13-15` 是一句关键的安全规则：
> 「计划中的 deadline 是**新鲜度占位符**，不是任务期限……**绝不能**把它应用到可能已经执行过的命令的重试上。」

### 5.2 为什么 Runtime 不依据命令来源绕过安全校验

**答案的一半是结构性的：`Command` 里根本没有「来源」这个字段，proto 里也没有。**

我逐字段核对了 `proto/robot/v1/robot.proto:156-176` 的 `message SkillCommand`：
19 个字段，与上面的 Go 结构一一对应，`grep -n "origin|brain|source_plane|created_by"` 在
`proto/robot/v1/robot.proto` 与 `proto/fleet/v1/fleet.proto` 上**零命中**
（proto 里唯一的 `origin` 是 `fleet.proto:181-183` 的占用栅格坐标原点）。

设计文档 `docs/superpowers/specs/2026-08-20-distributed-agentos-world-harness-design.md:201` 把这个决定写成了明文规范：

> 「**Robot Runtime 不接收 `brain_id` 或『来自云端/本地』的来源字段。
> 它只验证工具、参数、安全、期限、幂等和 fencing。**」

文档 `docs/architecture/distributed-agentos.md:7` 给了同样的表述。

### 5.3 强制这一点的五处代码

| # | 位置 | 强制方式 |
| --- | --- | --- |
| 1 | `edge/runtime/runtime.go:61-81` + `proto/robot/v1/robot.proto:156-176` | **类型层面无法表达来源**——不是「不检查」，是「没有可检查的东西」 |
| 2 | `sim/mujoco/tangying_sim/server.py:429-478` `_validate()` | 仿真 Runtime 的**唯一门禁**。检查表见 §1.4，没有一项是按来源分支的；失败即返回稳定错误码，命令不进 `_dispatch` |
| 3 | `edge/robotclient/client.go:399-412` | 默认安全档位**由连接到的 Runtime 的 `RobotProfile` 推导**，不由命令来源推导；注释 `:407-408`：「明文是传输选择，**不是**新适配器支持旧仿真安全档位的证据」 |
| 4 | `edge/worker/worker.go:293-296` | worker 在**本地重新物化并重新校验**计划：`manipulation.Plan(...)` → `guard.New(manipulation.Catalog()).Validate(plan)` → `compiler.New().Compile(plan)`。云端只给了 intent，计划与安全字段是边缘重造的 |
| 5 | `cmd/local-agent/main.go:90` + `safety_profile_test.go:9-18` | 本地形态**必须显式**配置 `robot-safety-profile`，测试断言「隐式安全档位……运行时策略必须自己选默认值」 |

第 4 点的证据在 `docs/architecture/fleet-cloud.md:202-203` 被总结成一句可引用的设计规则：
> 「LLM 永远不能设置安全字段（deadline / lease / approval / idempotency 由 edge-worker 本地重造）。」

### 5.4 三个「线上有字段、Runtime 不检查」的缺口（必须显式标注）

`SkillCommand` 有 19 个字段，但**接收 ≠ 校验**。逐个字段核对 Python 侧后：

| 字段 | proto | Go 侧是否发送 | 仿真 Runtime（`sim/mujoco/tangying_sim/server.py`） | 真机安全监督（`robot/gateway/tangying_robot_gateway/safety.py`） |
| --- | --- | --- | --- | --- |
| `approval_id` | `robot.proto:167` | 是（`client.go:551`） | **完全不读**（`grep -c "approval" server.py` → **0**） | **强制**：`safety.py:117-118` `if physical and not command.approval_id: return SafetyDecision(False, "APPROVAL_REQUIRED")` |
| `task_revision` / `aggregate_version` / `step_id` | `robot.proto:173-175` | 是（`client.go:557-559`） | **完全不读**（`server.py` 中三字段零命中） | 由 Gateway 转发（`robot/ros2_ws/install/tangying_ros_gateway/lib/python3.12/site-packages/tangying_ros_gateway/node.py:132` 只转 `approval_id`） |
| `world_revision_basis` | `robot.proto:170` | 是（`client.go:554`） | 只在日志/事件处被**读取**（`server.py:614`），`_validate` 不据此授权 | 同 |

这与 `docs/production/sim-to-real.md:16` 的原文**部分冲突**，原文是：

> 「当前 Python Runtime 校验 robot/task/command、deadline、**approval**、catalog、world basis、
> resource/fencing、idempotency 和 safety profile。protobuf 已携带 task_revision、
> aggregate_version、step_id，但 **Python `Command` 当前未映射并独立检查这三个字段**，
> 不能把上层版本约束等同于 Runtime 已实现独立版本授权。」

**准确的表述是**：这句话对 `robot/gateway`（真机安全监督）成立，对 `sim/mujoco`（仿真 Runtime）**不成立**——
仿真的 `_validate` 不检查 `approval_id`。文档把两条 Python 实现当成了同一条。
**这是本书必须分开写的一点**：仿真通过 ≠ 真机门禁通过，反之亦然。
（设计规范 `spec:201` 列出的 Runtime 校验项是「工具、参数、安全、期限、幂等和 fencing」，
**其中本来就没有审批**——所以更准确的说法是「审批是边缘/网关层的事实，不是 Runtime 层的普遍事实」。）

---

## 6. 协调器（`fleet/coordinator`）

### 6.1 调度判定逻辑（伪代码，逐行对应真实代码）

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

**C9 是双机协同的引擎**：intent k 只有在 0..k-1 全部 `SUCCEEDED` 时才可认领。
注释 `coordinator.go:5-8` 说明了它的设计意图：
> 「当机器人 A 完成它的子任务，协调器把下一个子任务刷成 READY，机器人 B 的 worker 于是可以认领它。」

**C18 的状态机合法性**（`:544-559`）值得单独讲：协调器不能直接跳状态，
它必须走合法路径（`Observing → Planning → Executing`；`Verifying → Succeeded`；
`RecoverableFailure → Failed`），且错误被忽略（注释 `:540-543`：并发认领可能已经推进过）。
这是**乐观的状态机推进**，不是事务性的——(推断) 属于 §8 未验证范围的一部分。

**C16 的顺序是刻意设计**：先提交持久化，成功后才把资源归属投影进世界
（`:517-527`）。测试 `TestResourceOwnershipIsNotPublishedWhenClaimCommitFails`
（`coordinator_test.go:88`）固定了这一点。这防止「世界说 robot-1 拥有方块，但协调器没记住」。

### 6.2 双机交接（handoff）如何分解与协调

`isSharedBlockHandoff`（`coordinator.go:931-936`）的识别条件：物体 `category=="block"` 且
`attributes["color"]=="red"`，且源或目标是 `handoff-zone` / `target-zone`。

**生命周期**（资源 `block:red-block`，owner 转移三次）：

| 阶段 | 触发 | 资源动作 | fencing | 事件 | 源码 |
| --- | --- | --- | --- | --- | --- |
| robot-1 认领 | `NextIntent` | `Acquire(block, robot-1)` | `INCR → 1` | `INTENT_CLAIMED` | `:481-485` |
| robot-1 完成 | `CompleteIntentRevision` | `Transfer(block, robot-1 → robot-2, token=1)` | `INCR → 2` | `BLOCK_AVAILABLE` | `:745-757` |
| 同一提交内 | — | `next.Status = READY` + outbox `<task>/intent/1/ready`，topic `robot/robot-2` | — | — | `:759-774` |
| robot-2 认领 | `NextIntent` | token 已非 0，只 `Validate` | 不变 | `INTENT_CLAIMED` | `:488-490` |
| robot-2 完成 | `CompleteIntentRevision` | `Transfer(block, robot-2 → "environment", token=2)` 并投递后 `Release` | `INCR → 3` | `BLOCK_DELIVERED` | `:776-786,820-822` |

**两个细节值得写进书里**：

1. **转移与 ready 通知在同一事务**（`:809` 的一次 `persistLocked` 同时提交状态、事件、outbox）。
   这消除了「token 转了但 robot-2 没被唤醒」的窗口。
2. **物理完成 ≠ 记录完成。** 验证必须通过 `harness.Evaluate`（`:703-719`），
   而 `RequiredStableObservations: 2`（`:715`）要求连续两次稳定观测。
   若验证不通过，`return nil, ErrWorldNotReady`，**资源不转移、outbox 不产生**。
   测试 `TestExternalBlockMovePreventsVerifiedCompletion`（`coordinator_test.go:227`）
   覆盖「有人从外部移动了方块」这一情形。

### 6.3 任务修订（revision）与安全点

- `commandIdentity(taskID, revision, stepID)`（`fleet/coordinator/revisions.go:16`）把命令身份
  绑定到 `(taskID, taskRevision, stepID)` 三元组。
- `CompleteIntentRevision` 校验四个不可变坐标 + token（`coordinator.go:633-641`），
  **任何一项不匹配即拒绝**，因此「被取代的修订的延迟包无法推进当前图」（注释 `:598-601`）。
- 修订切换必须在安全点：`hasRunningIntent`（`revisions.go:20-27`）为真时 `reloadIfNeeded`
  **直接返回旧状态不切换**（`coordinator.go:1359-1361`），
  测试 `TestConfirmRevisionWaitsForRunningIntentThenActivatesAtHarnessSafePoint`。

---

## 7. gRPC / mTLS 契约

### 7.1 proto 定义了什么

`proto/` 下只有两个文件、两个 service：

**`tangying.robot.v1.RobotRuntime`**（`proto/robot/v1/robot.proto:9-19`）——Laptop/Edge 与机器人之间：

| RPC | 形态 | 说明 |
| --- | --- | --- |
| `GetRuntimeInfo` | unary | 能力/阻塞项/协议版本 |
| `Observe` | **server stream** | 观测流 |
| `ExecuteSkill` | **server stream** | 命令下发 + 事件流（`SkillCommand` → `SkillEvent`） |
| `Cancel` | unary | 取消单个任务（**不等于**急停） |
| `EmergencyStop` | unary | 锁存式安全停止；注释 `edge/robotclient/client.go:496-497` 说明「LLM/Agent 不能通过这个 API 清除它，清除需要本地人工操作」 |
| `ListServices` / `CallService` | unary | Runtime 自有的服务目录（不预选硬件或仿真实现） |

**`tangying.fleet.v1.FleetGateway`**（`proto/fleet/v1/fleet.proto:20-27`）——Edge 与云端之间，**仅两个 RPC**：

| RPC | 形态 | 说明 |
| --- | --- | --- |
| `Register` | unary | 声明机器人身份，返回协商后的心跳/租约策略 |
| `Link` | **bidi stream** | 心跳（续设备租约）、低频遥测、状态/事件上报、接收服务器命令 |

`LinkMessage` 是一个 `oneof`（`:81-92`）：`Heartbeat | TelemetrySample | StatusReport | EventReport | ServerCommand | Ack | ObservationEnvelope`。
`ServerCommand` 只定义了三种 type（`:205`）：`"cancel_step"` / `"emergency_stop"` / `"ping"`。

**proto 注释 `:17-19` 划了一条重要边界**：
> 「Task pulling and step state transitions intentionally stay on HTTP (or a Redis Stream)
> so the data plane can be served independently of the control channel.」

即：**控制通道（Link）挂了，数据面（拉任务、报结果）照常**。这直接决定了 §8 里
「网络分区」的处理方式不是「全停」，而是「能力变窄」。

### 7.2 mTLS 如何配置

`scripts/fleet-certs.sh`（82 行）生成三件套：

| 产物 | 生成方式 | 关键点 |
| --- | --- | --- |
| CA | `openssl req -x509 -newkey rsa:3072`，`CN=Tangying Fleet CA`，默认 3650 天 | 私钥留在控制面主机，目录 `deploy/cloud/certs/` **已 gitignore** |
| 服务器证书 | `CN=fleet-control-plane`，SAN = `DNS:localhost, DNS:fleet-control-plane, IP:127.0.0.1, IP:0.0.0.0`，`extendedKeyUsage=serverAuth`，825 天 | 幂等：文件已存在则跳过 |
| 每机器人客户端证书 | `CN=$robot`，`subjectAltName=DNS:$robot`，`extendedKeyUsage=clientAuth`，825 天 | `ROBOTS="${FLEET_ROBOTS:-robot-1,robot-2}"` 逗号分隔循环 |

服务端配置在 `cmd/fleet-control-plane/main.go:258-274` `buildGateway`：
未设证书时**网关自动禁用**（`:262-265`），设了才启动。环境变量：
`FLEET_GRPC_CA` / `FLEET_GRPC_CERT` / `FLEET_GRPC_KEY` / `FLEET_GRPC_REQUIRE_CN`（默认 true）/
`FLEET_DEVICE_LEASE`（15s）/ `FLEET_HEARTBEAT_INTERVAL`（5s）。

认证边界（`docs/architecture/fleet-cloud.md:130-132`）：
服务端 `RequireAndVerifyClientCert` + TLS 1.3，并强制客户端证书 CN == robot id；
**配置项不能放宽这个身份边界**。

**另有一条与 gRPC 平行的 mTLS 体系**：`internal/pairing/authority.go` 在**进程内**用
Go 的 crypto 生成 ECDSA P-256 体系（`AuthorityLifetime = 10 年`，`LeafLifetime = 90 天`，
`RenewalWarning = 7 天`，`:33-43`），用于 laptop↔robot 的本地配对。
注释 `:16-23` 解释了为什么不用脚本：「按钮没法合理地 shell out 到一个需要 openssl 的脚本……
中途失败会在盘上留下半签发的证书。」两条路径的产物**形状刻意相同**，否则「两种配对就变成两种配对」。

### 7.3 Fleet API 端点清单

来自 `fleet/server.go:117-154`（真实路由注册，`net/http` 的 method-pattern 语法）：

```
GET    /healthz
POST   /v1/auth/login                              POST   /v1/auth/ws-ticket
GET    /v1/devices                                 GET    /v1/devices/{id}
POST   /v1/devices/{id}/estop                      POST   /v1/devices/{id}/cancel
POST   /v1/tasks                                   GET    /v1/tasks
GET    /v1/tasks/{id}                              POST   /v1/tasks/{id}/approve
POST   /v1/tasks/{id}/cancel                       POST   /v1/tasks/{id}/state
POST   /v1/tasks/{id}/events
POST   /v1/tasks/{id}/revisions                    POST   /v1/tasks/{id}/revisions/{revision}/confirm
GET    /v1/tasks/{id}/revisions                    GET    /v1/tasks/{id}/experience
GET    /v1/tasks/{id}/intents                      GET    /v1/tasks/{id}/domain-events
GET    /v1/telemetry                               POST   /v1/telemetry
GET    /v1/maps/global                             GET    /v1/world
GET    /v1/scene/frames                            GET    /v1/scene/frames/{robot}
GET    /v1/world/events/ws                         GET    /v1/orchestration/metrics
--- 设备数据面（device token 认证）---
GET    /v1/queue/next
POST   /v1/tasks/{id}/intents/next
POST   /v1/tasks/{id}/intents/{index}/complete
POST   /v1/tasks/{id}/intents/{index}/fail
--- 静态 ---
GET    /                                           (operatorweb.Handler())
```

认证分两条：操作员 JWT（`fleet/auth`）与设备令牌（`X-Robot-ID` + `X-Device-Token`，
`docs/architecture/fleet-cloud.md:71`）。设备主体越权会被拒：
`fleet/server.go:676-689` `deviceRobotIdentity` 在 `claimed != "" && claimed != robotID` 时返回
`403 ROBOT_IDENTITY_MISMATCH`。

---

## 8. 故障假设清单：已实现 vs 未验证

**这一节是本章必须最诚实的部分。** 我把三者分开：

- **(A) 已实现且被自动化测试固定**——有可点名的测试；
- **(B) 已实现但测试面窄**——代码在，测试只覆盖了不变量的一部分；
- **(C) 文档明列为未验证**——代码可能在，但没有证据。

### 8.1 故障矩阵与它的真实覆盖（`tests/e2e/test_fleet_faults.py`）

`FAULT_CHECKS`（`:23-151`）声明了 **10 类故障**。但文件头注释 `:1-7` 说得很清楚：

> 「**只有一个场景跨越活的 OS 进程/网络边界。其余场景只针对拥有该不变量的那个窄一致性边界**；
> 这让故障注入保持确定性，并避免在生产二进制里塞进一个无认证的混沌端点。」

| 故障 | 归类 | 固定它的测试 | 覆盖边界 |
| --- | --- | --- | --- |
| `observation_duplicate_reorder` | **A** | `./core/worldmodel` `TestProjectorRejectsDuplicateAndOutOfOrderSourceSequence` | reducer 单进程 |
| `worker_crash_after_place` | **B** | `./edge/worker` `TestWorldNotReadyRetriesCompletionOnly` + Python `test_service_replays_terminal_event_for_duplicate_command` | 分别测 Go 侧与 Runtime 侧，**没有真跨进程杀 worker 再验** |
| `coordinator_restart` | **B** | `./fleet/coordinator` `TestCoordinatorRestoresRunningIntentWithoutDoubleAdvance` | **同进程内换一个 Coordinator 实例**，不是真重启进程 |
| `redis_outage` | **B** | `./fleet/coordinator` `TestQueueOutageLeavesCommittedHandoffOutboxForRecovery` | 用注入的 enqueue 失败模拟，**不真停 Redis** |
| `stale_fencing_token` | **A** | Python `test_physical_command_with_stale_fencing_fails_before_dispatch`、`test_stale_handoff_token_cannot_change_shared_block_owner` | 真正的门禁代码 |
| `receiver_offline_after_handoff` | **A** | `./fleet/coordinator` `TestReceiverOfflineAfterHandoffCannotClaimOrDeliver` + `./fleet/registry` `TestDeviceGoesOfflineAfterLeaseExpiry` | 租约过期逻辑 |
| `camera_loss_and_ui_reconnect` | **A** | `./fleet/coordinator` `TestCameraLossDoesNotBlockSemanticGroundTruthHandoff` + Python renderer + `web/world_view_test.mjs` | — |
| `external_block_move` | **A** | `./fleet/coordinator` `TestExternalBlockMovePreventsVerifiedCompletion` | — |
| `versioned_task_update_fencing` | **A** | coordinator 三个测试 + `./tasks` 两个并发写测试 | — |
| `versioned_task_experience_gap` | **A** | `web/app_test.mjs` 前端 | 只在前端 |

**真正跨越进程/网络边界的只有一个测试**：`test_edge_disconnect_reconnect_completes_without_false_advance`
（`:175-215`）。它做的事值得完整引用，因为它就是本章「分布式=故障假设」的最佳论证：

```python
stack.pause_process("edge-robot-1")            # 真的 SIGSTOP 一个 edge-worker 进程
# 等待设备租约过期：断言 not _device_online(stack, "robot-1")   # :182-187
task_id = stack.create_and_approve()
time.sleep(0.75)
assert task["state"] != "SUCCEEDED"            # :192  ← 不会假成功
assert 没有 BLOCK_AVAILABLE 事件                 # :193-196 ← 后续意图不被解锁
stack.resume_process("edge-robot-1")           # SIGCONT
# 等设备重新在线，等任务终态
assert final["state"] == "SUCCEEDED"           # :204
assert events.count("BLOCK_AVAILABLE") == 1    # :211  ← 恰好一次，不重复
assert events.count("BLOCK_DELIVERED") == 1    # :212
```

**「恰好一次」在这里被真正跨进程验证了**——这是全仓库最强的分布式证据。

### 8.2 文档原文的「仍须独立验证」清单

**主清单在 `docs/architecture/distributed-agentos.md:33-39`**，逐条照抄：

> ## 仍须独立验证的范围
> - leader fencing 与所有业务提交的同存储原子校验；
> - MySQL、Redis、世界投影与物理结果之间可恢复的资源转移 saga；
> - 数据库/队列切主、网络重排和长期多进程故障验证；
> - 实机传感器质量、坐标标定、匹配策略、实体急停、停止响应和受限任务验收；
> - 目标终端可见帧率、现场容量、备份恢复和长期运维。

同一文档 `:31` 划了硬边界：

> 「这解决单主进程重启的**一部分**状态恢复，**不提供跨主机共识或跨存储事务**。
> 文件锁/损坏/保存失败应失败关闭；不能多开 writer、删除快照或降低 token 来『恢复服务』。」

**第二份清单在 `docs/production/deployment-and-capacity.md`**：

- `:11`：「当前 Fleet **不是可直接复制多个副本的自动 HA 服务**。Coordinator、WorldHub 以及物理资源
  所有权必须维持**同一单写关系**。**即使数据库或 Redis 部署为 HA，也不自动补齐业务提交 fencing
  和资源转移 saga。**」
- `:15`：「**不要将该环境变量（`FLEET_WORLD_SNAPSHOT_PATH`）误当作任意共享文件系统的多写协调机制。**」
- `:18`：「独占文件锁防止同机第二个 writer；损坏、世界身份不符或写入失败时**关闭推进能力**，
  **不自动退回空世界**。」
- `:19`：「重启不恢复 delta 环形缓存；浏览器应 REST resync。**恢复快照不是当前现场真值**，
  需要新观测重新证明 freshness 和后置条件。」
- `:21`：「**跨主机 HA、共享盘多写、全局原子事务、长期故障容忍和自动切主仍需独立工程与验收。**」
- `:31`：「**尚无适用于任意硬件数量的认证容量。**」
- `:39`：「未来按 world/tenant 分片、读取副本、对象存储、双 ingress 和数据库 HA 的方案
  **均需另立设计及容量/恢复证据，不能作为当前已经实现的功能列入交付。**」

**第三份清单在 `docs/architecture/why-distributed.md:221-227`**（原文见 §11 引用），
以及 `README.md:135` 的总边界：

> 「当前**主线是一台机器人**在受限环境把活干完；……云端 Fleet 是仓库里**已经实现**的扩展路线，
> **不等于『单机开发栈已经具备跨地域生产级高可用』**。**恢复能力限于单主进程重启范围内的一部分
> 状态恢复，跨主机共识与跨存储事务仍在待验证清单上。**」

**第四份是 runbook 的故障矩阵**，`docs/production/operations-and-failures.md`。
它比上面三份更接近运维现实：**每一项都明确写了「安全不变量」（不许做什么）**。
总纲 `:5`：

> 「任何时候都遵守安全不变量：**旧 revision 不覆盖新 revision；旧 fencing token 不驱动物理资源；
> 观测不新鲜不判成功；软件急停不替代实体急停；不要手工改 Task 历史、World revision、
> source sequence、custody token、Harness evidence 或签名摘要。**」

任务书列举的六项故障里，它有专门条目的有四项（`:24` 时钟漂移、`:25` MySQL 不可用、
`:26` Redis 不可用、`:41` 进程崩溃），另有 `:34` leader lease 丢失与 `:98`/`:100` 世界快照/锁存两条运维条目，
**但术语层面从未出现「网络分区」与「状态分歧」**——最近邻是 `:23` DNS/路由、`:47` 机器人离线、
`:53` 矛盾观测（「**不投票决定资源所有权**」）、`:56` custody/fencing 冲突（「**旧 token 永不复活**」）。

### 8.3 逐项回答任务书的故障清单

分类：**(A)** 已实现且有可点名测试；**(B)** 已实现但测试面窄；**(C)** 文档明列未验证。

| 故障 | 代码 | 测试证据 | 文档原文（runbook） | 归类 |
| --- | --- | --- | --- | --- |
| **进程重启** | `coordinator.go:351-422`/`:1347-1366`；`worldhub.NewPersistent`；`OutboxClaimTTL=30s` | `coordinator_test.go:519` | `:41`「**重启不自动重放副作用**」 | **(B)** 测试是**同进程新建实例**，不是真杀进程 |
| **队列宕机（Redis）** | outbox（§3.3）；`main.go:145-151` 无 Redis 时退化为内存领导者 | `coordinator_test.go:307`/`:341` | `:26`「**至少一次必须由幂等吸收**」「从 Outbox 重放」「不要清空未审计 PEL」 | **(B)** ⚠️ `main.go:147` 的分支意味着**未配 Redis 时多实例会静默失去互斥**（(推断)，文档未提） |
| **网络分区** | 设备租约 15s 过期；Link 退避 1s→30s；控制/数据面分离；`README.md:118`「断网安全停机，恢复后对账」 | `test_fleet_faults.py:175-215`（唯一跨进程用例） | `:23` DNS/路由（「不扩大到 `all` 作为永久修复」）；`:47` 机器人离线（「**不向离线机器人分派**」） | **(B)** 机制齐备，**但没有任何测试真的断开网络**；`distributed-agentos.md:37` 列为未验证 |
| **时钟漂移** | ⚠️ **无自动处理**：租约过期用本地 `time.Now()`（`memory.go:99`、`coordinator.go:328,335`）；`Grant.ExpiresAt` 由持有者本地推算（`redis/lease.go:155-157`），真正过期由 Redis `PEXPIRE` 决定 | **无测试** | **`:24` 有一整行**：现象「**token 早过期、观测 stale、证据时间倒退**」；「**不手改证据时间**」；恢复「暂停派发，同步时钟，Runtime 重注册并使用新 sequence」；防止复发「chrony/NTP 告警」 | **(C) 有 runbook、无实现**。准确说法不是「忽略时钟漂移」，而是「**识别了现象、给了人工处置、装了 NTP 告警，但没有自动检测、没有测试，两个时钟源不一致这一根因未被代码处理**」 |
| **状态分歧** | `ErrTransformRevisionConflict`（`projector.go:81-83`）；`multi-robot.md:11`「held/entity/resource **三源冲突时停止后续动作**」 | 冲突检测有测试 | `:53` 矛盾观测（「**不投票决定资源所有权**」）；`:36` 倒序事件（「**旧事实丢弃**」）；`:56`「**旧 token 永不复活**」 | **(A/B)** 检测明确；**自动消解不存在**（按设计交给人）。多源对同一实体的**语义**分歧无合并策略（`reduce` 是最后接受者胜，(推断)） |
| **数据库切主** | 无。只用 `LevelReadCommitted` + 行锁 + 版本 CAS | 无 | `:25` 现象「创建/审批 5xx，**已有机器人可能仍运行当前命令**」（=DB 挂了不停止已下发的物理动作）；「**不从 Redis 反写 Task 真值**」；防止复发栏「**HA、备份恢复演练**」←**待建** | **(C)** 四重声明：`distributed-agentos.md:37`、`deployment-and-capacity.md:11`/`:21`/`:39` |
| **跨存储事务** | **明确不存在**：状态/事件/outbox/checkpoint 同事务（好），但**租约在 Redis、世界在文件、任务在图**，三者无法原子 | — | `:34` 防止复发栏「**lease 与提交同存储 fencing、chaos test**」；`:56`「**原子 custody 迁移、故障注入**」 | **(C)** 正是 `distributed-agentos.md:35-36` 列为未验证的两项 |

### 8.4 最容易混淆的六处，必须显式标注

1. **`coordinator_test.go:519` 的「协调器重启」是同进程新建实例。**
   它验证的是「状态重建逻辑正确」，**不是**「进程崩溃后恢复」。两者不能互相替代。
2. **`TestQueueOutageLeavesCommittedHandoffOutboxForRecovery` 里 Redis 没有真的宕机。**
   它验证的是「enqueue 返回错误时的处理」。
3. **`docs/architecture/why-distributed.md:160` 的「cmd/local-agent 引用云端/fleet | 0 处」与代码不符**（§0）。
4. **`docs/architecture/fleet-paper-loop.md:93`「云端协调器状态在内存中（单实例画像）；任务数据本身
   持久化在 MySQL」与 HEAD 的代码路径不总是一致。** 该文档在 `:3` 与 `:7` **自我声明是历史档案**
   （「本页保留早期杯子/瓶子与占用栅格实验的版本语义，命令、测试数量与事件名不作为当前验收承诺」、
   「复现时需选择对应历史版本，不能把下文计数套到当前代码」）。在 HEAD 上，
   `cmd/fleet-control-plane/main.go:153` 用 `coordinator.NewWithStore(service, claimLease, coordinationStore)`：
   **`FLEET_STORE=mysql` 时协调器状态是持久化的**（走 `store.LoadState`/`Commit`）；
   **`FLEET_STORE=memory`（默认）时才是纯内存**。同时 `main.go:145-151` 决定领导者租约是 Redis 还是进程内。
   **结论：引用 `:93` 必须标注「历史档案 + 仅对 memory profile 成立」。**
5. **runbook 的「防止复发」栏里写的是待建能力，不是现有保障。**
   `operations-and-failures.md:34` 写「lease 与提交同存储 fencing、chaos test」、
   `:56` 写「原子 custody 迁移、故障注入」、`:25` 写「HA、备份恢复演练」——
   这三项恰好是 `distributed-agentos.md:35-36` 与 `deployment-and-capacity.md:11` 列为
   **未实现/未验证**的东西。**读该表时必须把「恢复」列（今天怎么做）与「防止复发」列（将来该做什么）
   分开**，否则会把待办当成已交付。
6. **`docs/production/sim-to-real.md:16` 把仿真 Runtime 与真机安全监督当成同一条 Python 实现。**
   实测：`sim/mujoco/tangying_sim/server.py` 对 `approval_id` **零引用**；
   `robot/gateway/tangying_robot_gateway/safety.py:117-118` 才真正强制 `APPROVAL_REQUIRED`。
   **仿真闭环通过不能推断出审批门禁通过。**（详见 §5.4）

---

## 9. Local Agent 形态

### 9.1 依赖事实（见 §0 的验证命令与结果）

一句话总结：
**零运行时云端依赖（不构造任何云客户端、不做任何云 I/O），非零库依赖
（`fleet/worldhub`、`fleet/eventlog`、`edge/worker` 的观测映射、`edge/agent`、`edge/runtime`）。**

`internal/localapp/app.go:1-3` 的包注释是理解它设计意图的关键：

> 「Package localapp owns the single-user laptop execution lifecycle.
> It **deliberately has no distributed claim or task-lease protocol**:
> SQLite is the business-state authority and one worker serializes physical work.」

**这句话本身就是本章的论点**：作者明确知道自己在砍什么，并说明了砍掉的**理由**（只有一个写者 → 不需要 claim 协议）。

### 9.2 SQLite schema

`middleware/sqlite.Open`（`store.go:21-82`）依次建五组表：

| 组 | 表 | 建表位置 | 本地是否真用 |
| --- | --- | --- | --- |
| 任务 | `tasks` | `store.go:31-46` | 用 |
| 任务事件 | `task_events`（PK `(task_id, sequence)`，FK 级联删除） | `store.go:47-57` | 用 |
| 步骤账本 | `step_runs`（PK `(task_id, step_id)`，含 `reconcile_outcome/actor/note/at`；**部分唯一索引** `step_runs_idempotency_idx ON (idempotency_key) WHERE idempotency_key <> ''`） | `store.go:58-68` | 用（幂等与对账） |
| 修订 | `task_revisions` / `task_revision_events` | `store.go:175-190` | 用 |
| 证据 | `observation_evidence`（+ `observation_evidence_task_idx`） | `evidence.go:18,34` | 用 |
| **协调** | `fleet_graph_states` / `fleet_domain_events` / `fleet_outbox` / `fleet_checkpoints` | `fleet.go:16-56` | **建了但不用**（无协调器，无 outbox 消费者） |

`PRAGMA journal_mode=WAL` + `PRAGMA foreign_keys=ON`（`store.go:27-28`）。
数据库默认落在 `<dataDir>/agent.db`（`cmd/local-agent/main.go:246`，`dataDir` 由配置决定），
`os.MkdirAll(dataDir, 0o700)`（`:243`）。

**「建了但不用」这一条很有教学价值**：它说明作者把「持久化的分布式协调原语」当成
**可复用的库**（SQLite 版实现了 `eventlog.Store` 接口，`fleet.go:14-59`），
而「是否启用多写者协议」是**装配层**的决定，不是存储层的决定。

### 9.3 刻意简化清单

| 简化 | 证据 |
| --- | --- |
| 无 claim / 租约协议 | `localapp/app.go:1-4` 包注释明写「**deliberately has no distributed claim or task-lease protocol**」；`middleware/contracts.go:44-53` 定义了 `Lease`（带 fencing token）与 `Locker` 端口，**本地无实现** |
| 世界不做持久化 | `main.go:280` `worldhub.New(...)`，非 `NewPersistent` |
| 无 outbox、无资源 fencing | `cmd/local-agent` 不构造 `lease.Manager`；`fleet_*` 表建了不用 |
| 无批准端口（自动发起者） | `main.go:401-404`：「No Approve port: the only way to reach this executor today is the operator's endpoint, which sets `OperatorApproved` itself. An automatic initiator added later must supply one, and **until it does, nothing it starts can run a bounded write**.」 |
| 观察者不在执行路径上 | `cmd/local-agent/agentruntime.go:65-69`：「The agent runtime **observes execution; it is not part of it**, and a system that refused to run a task because an observer could not be started would have made observability part of the safety path」 |
| 自动恢复只跑只读动作 | `internal/autorecovery/supervisor.go:1-35` 包注释「Exactly one thing is automatic: an action the catalog classifies as `read_only`」；硬线在 `:183-190` `if action.Risk != RiskReadOnly { skip }` |
| 安全档位必须显式配置 | `main.go:90` `-robot-safety-profile`；`safety_profile_test.go:9-18` |
| 机器人用 `robot-local` 单一身份 | `main.go:282,307,308` |
| **保留**了 step 幂等 + 证据 + 对账 | `step_runs` 表与 `middleware.ExecutionStore`（`contracts.go:150-160`，含 `ReconcileStep`） |

**最后一行是本章的收束点**：Local Agent 砍掉了协调，**没有砍掉闭环**。
它保留了「动作后的证据」「步骤幂等键」「UNKNOWN_OUTCOME 的人工对账」——
即 `docs/architecture/why-distributed.md:105-112` 所谓「物理世界强加的三样东西」。代码结构自证了那句论断。

**并且它用两处注释把「为什么必须保留」写成了论证**（这两段值得整段引用进书里）：

1. `internal/localapp/recovery.go:13-19` —— 对账状态**只能由人清除**：
   > 「The block exists to wait for a person, so **a software path that could clear it would make the
   > wait theatre**.」
   数据库层再兜一道：`ReconcileStep` 的 `WHERE ... AND reconcile_outcome = ''`，
   二次写返回 0 rows 即报错。
2. `internal/autorecovery/supervisor.go:183-190` —— 自动恢复的**唯一**允许动作的风险等级是 `read_only`。

### 9.4 重启语义：能活下来的与活不下来的

| 状态 | 载体 | 重启后 |
| --- | --- | --- |
| 任务、状态、事件账本、版本 | SQLite `tasks` / `task_events` / `task_revisions` / `task_revision_events` | ✅ 存活（`UpdateWithEvent` 保证状态与审计事件原子） |
| 物理步骤进度 | SQLite `step_runs`（`INSERT … ON CONFLICT(task_id,step_id) DO UPDATE`） | ✅ 存活 |
| 观测证据原始字节 | SQLite `observation_evidence`（含 SHA256 校验） | ✅ 存活，受双预算过期约束 |
| **队列与暂停/恢复标记** | `App` 的**内存 map**（`app.go:63-66`：`queued`/`active`/`pauses`/`resumes`） | ❌ **全部丢失** |

**重启的恢复动作**（`app.go:678-691`）：把所有处于 `STATE_OBSERVING/PLANNING/EXECUTING/VERIFYING/
SAFE_RECOVERY` 的任务**一律降级为 `RECOVERABLE_FAILURE`**，理由串写死为
`"Local Agent restarted during execution"` —— **不自动重放**。
这与云端 `reclaimStaleLocked` → `UNKNOWN_OUTCOME`（§1.6）是**同一个设计原则在单机形态的复现**：
不确定的物理结果不会被自动重试，只会被显式地报告为「需要人」。

**本地没有 `fleet_checkpoints.event_cursor` 那样的游标**（该表本地从不写入）。它真正的两个「游标」是：
账本序号 `task_events.sequence`（下一个值由 `SELECT COALESCE(MAX(sequence),0)+1` 求取）
与证据序号 `observation_evidence.record_index`（`AUTOINCREMENT`）。
恢复的判定是**逻辑水位**而非指针：由 `step_runs.status` 算出 `CompletedStepIDs` 与 `UncertainStepIDs`，
`RequiresReconciliation = len(UncertainStepIDs) > 0`（`internal/localapp/recovery.go:24-83`）。

### 9.5 数据位置与迁移策略

- 主库 `<dataDir>/agent.db`（`main.go:246`；目录 `MkdirAll(0o700)`）。
  `dataDir` 默认：darwin `~/Library/Application Support/TangyingRobotAgent`，
  其他平台 `~/.local/share/tangying-robot-agent-os`，失败回退 `.tangying-robot-agent`（`main.go:583-596`）。
- 控制台会话令牌 `<dataDir>/console-session` 与监听地址 `<dataDir>/console-address`，均 `0o600`。
- `PRAGMA journal_mode=WAL` + `PRAGMA foreign_keys=ON`（`store.go:27-28`）。
- **没有版本化迁移**：无 `schema_migrations` 表、无 `.sql` 文件。策略是**幂等 forward-only 内联 DDL**：
  `CREATE TABLE IF NOT EXISTS` 全量重放 + 用 `PRAGMA table_info` 探测后
  `ALTER TABLE … ADD COLUMN <c> TEXT NOT NULL DEFAULT ''`（`store.go:95-173`）。
  **没有删除列、改类型、重命名或回滚路径。**
  迁移正确性靠「重开库」测试表达（如 `store_test.go:13 TestTaskPersistsAcrossReopen`、
  `fleet_test.go:13 TestFleetCoordinationSurvivesSQLiteReopen`）。

---

## 10. 与通用 coding agent 的分布式差异

`docs/architecture/why-distributed.md:66-77` 给了一张对照表，我用代码事实逐行验证并补强：

| 维度 | coding agent（Codex / Claude Code 一类） | 本项目 | 代码事实 |
| --- | --- | --- | --- |
| 真相在哪 | 文件系统即真相，可 `stat`/`read` | **世界是真相，只能被观测** | `worldmodel.Snapshot` 的一切都带 `Evidence{ObservationID, SourceID, SourceSequence, ObservedAt}`（`types.go:20-27`）；没有「直接读世界」的 API |
| 部分失败 | 几乎不存在（原子 rename、进程有自己的内存） | **常态** | §8.3 的故障矩阵；`fleet/registry` 的在线判定就是「租约没过期」 |
| 单写 | 单进程单写者；`flock` 就够 | **三层**（世界/任务图/物体） | §2.1 |
| 幂等重试 | 重跑测试是免费的 | **结果未知时禁止自动重试** | `edge/recovery/classifier.go:17-23` 六类分类里，只有 `ObservationWait` / `PolicyRetry` 的 `Retryable=true`；`ExecutionReconcile`（`:68-73`）注释明写「系统**不会**自动重复可能已经执行的动作」 |
| 重试预算 | 无 | 有上限，超限即 `BLOCKED`/`FAILED_SAFE` | `edge/worker/worker.go:360,379` `PolicyMaxAttempts`；`spec:400`「超出重试/恢复预算只能进入 BLOCKED 或 FAILED_SAFE，**不能假成功解锁后续节点**」 |
| 断网行为 | N/A（本地进程） | **能力变窄，不是系统挂掉** | `why-distributed.md:173-176`：「确定性语法负责解析；**只读动作照跑，改动机器人的动作停住等人**」；Link 指数退避 `edge/cloudclient/link.go:100-124` |
| 会话长度 | 分钟到小时 | **年** | 需持久化 checkpoint + 版本化迁移；`middleware/sqlite` 的 `PRAGMA table_info(step_runs)` 增量加列迁移（`store.go:85-120`）就是为这个存在的 |
| 缺证据时的默认 | 没证据就再跑一次 | **没有新鲜证据就不能推进** | `coordinator.go:676-690`：无世界时记 `WORKER_REPORT_NO_WORLD`（具名承认）；有世界但对场景改变类 intent 无谓词时**直接拒绝** `ErrIntentUnverifiable` |

**最值得写进书里的一条对比是「重试」**。
`coordinator.go:45-57` 的注释把这件事讲透了：
`StatusUnknownOutcome` 被刻意**不**设为 `READY`，因为「把 intent 放回可认领池，
会让另一个 worker 重复一次**第一次可能已经发生过**的物理动作——
这正是 `core/closedloop` 禁止的事，它的 `UnknownOutcome` 类带有永久禁止自动重试的语义。
租约失效告诉我们 worker 停止报告了，**它没有告诉我们机器人站着不动**。」

**另一条对比是「谁有权威」**：coding agent 的门禁是「测试通过」；
本项目的门禁是**外部世界的新鲜观测**，而且**这个权威不能由被检查者自己提供**——
`Capability.MutatesWorld` 由 Runtime 声明（`runtime.go:99-103`），
证据由独立观测源产生（`completionRequiresPostCommandEvidence`），
校验由协调器执行（`coordinator.go:694-726`）。

---

## 11. 教学价值

### 11.1 最好的一个例子：**「租约过期了，但你不能重试」**

几乎所有分布式系统教材都教「租约过期 → 可以安全地重新获取」。
这个代码库**明确地、带论证地拒绝**了这条规则，而且理由是**领域性的**：

> 「租约失效告诉我们 worker 停止报告了，**不是**机器人站着没动。」
> —— `fleet/coordinator/coordinator.go:308-309`

这个例子的教学价值在于它一次性串起六个概念：

1. **租约的本质是「暂停信任」，不是「证明终止」**——租约只给出 liveness 保证，
   不给出 safety 保证。教材里常把两者混在一起。
2. **fencing token 与租约的分工**：租约管**何时**可以换手，token 管**换手后旧人还能不能写**。
   这个系统两者都有，而且**都只是必要条件，不是充分条件**（还必须过世界谓词）。
3. **不可逆操作改变一致性协议的形态**：一个可重试的动作只需要幂等键；
   一个不可重试的动作需要**一个人类决策点**（`ReconcileIntent` 强制 `actor` + `note`，`coordinator.go:1074-1079`）。
4. **「错误必须具名」**：`ErrClaimExpired` vs 「intent not running」的区别
   （`coordinator.go:649-654`）说明**错误码是运维接口**，不是日志美化。
5. **状态机的形状由物理决定**：`UNKNOWN_OUTCOME` 是一个**没有自动出边**的状态。
   在 coding agent 里这个状态根本不需要存在。
6. **可测试性**：`TestClaimLeaseLapseLeavesTheOutcomeUnknownInsteadOfReclaiming`
   （`coordinator_test.go:604`）等四个测试把「不能退回 READY」钉死成回归测试。

**次要例子候选**（若需要第二个）：
(i) `Commit` 的五合一事务（§3.2）——演示「为什么 outbox 是必需的」；
(ii) `worldhub.Subscribe` 的重同步判定（§4.4）——演示「delta 流的游标安全边界」；
(iii) `test_edge_disconnect_reconnect_completes_without_false_advance`——演示「假成功」是最危险的失败模式。

### 11.2 练习题（含答案要点）

**题 1（租约与 fencing）**
> 某个协调器实例 A 因 GC 停顿 40 秒未续租（`FLEET_LEADER_LEASE=15s`），
> 实例 B 已接管并推进了任务图。A 恢复后立刻处理一条来自 robot-1 的
> `POST /v1/tasks/T/intents/0/complete`，请求里带 `fencingToken=1`。
> 请列出所有会拒绝它的检查点，并回答：**如果 A 的请求恰好赶在 B 接管之前到达，会发生什么？**

*答案要点*：
- 检查点：① `validateLeadership` → Redis `validateLeaseScript` 里 `owner != A` → `ErrLeadershipLost`
  （`coordinator.go:1333-1345`）；② 即使跳过 ①，`CompleteIntentRevision` 的
  `fencingToken != node.FencingToken`（`:639-641`）会因为新纪元而拒绝；
  ③ 若 B 尚未改 token，则 DB 版本 CAS（`coordination.go:106-107`）会因为 `ExpectedVersion` 落后而拒绝。
- **关键洞察**：三个检查点**不是原子的**（`distributed-agentos.md:35` 把它列为未验证项）。
- 「赶在 B 接管之前」= 领导者租约尚未过期、token 尚未递增、版本尚未推进。
  此时 A 的请求**会成功**——而这是**正确的**，因为「B 接管」这个事实当时还不存在。
  **这正是租约模型的语义：它保证的是「不会有两个有效领导者同时存在」，
  而不是「一个被暂停的进程立刻失去权力」。** 若要求后者，需要 TrueTime/租约令牌一致性等更强的机制。
- 加分点：指出 `Grant.ExpiresAt` 是**各持有者本地时钟**推算的（`redis/lease.go:155-157`），
  真正的过期由 Redis 服务端 `PEXPIRE` 决定 —— 时钟漂移下两者不一致，
  而全仓库没有时钟漂移测试。

**题 2（事件日志与 Outbox）**
> 请解释：为什么 `INTENT_SUCCEEDED` 与它的 outbox 条目必须写在**同一个事务**里？
> 如果改成「先提交事件，再异步写 outbox」，会出现哪些具体故障？
> 请指出代码里为此做了哪两处保护，并说明为什么 `DispatchOutbox` 里还需要 `OutboxClaimTTL`。

*答案要点*：
- 分开写的故障：提交成功但 outbox 写入前进程崩溃 → **robot-2 永远不会被唤醒**，
  任务卡在 `BLOCK_AVAILABLE` 之后无人认领，而事件日志显示一切正常。
  这是「静默的不一致」，比崩溃更难发现。
- 两处保护：① `persistLocked` 构造单个 `eventlog.CommitRequest{State, Events, Outbox, Checkpoint}`
  （`coordinator.go:1304-1315`），`Store.Commit` 在一个事务里写全部（`coordination.go:85-188`）；
  ② `CompleteIntentRevision` 的**内联快路径**（`:824-828`）在提交后立刻试投并 ack。
- `OutboxClaimTTL = 30s`（`eventlog/store.go:14`）解决的是**投递者的崩溃**：
  没有它，一个 claim 之后就死掉的 dispatcher 会让条目永远处于 `claimed` 状态。
  `ClaimOutbox` 的 `WHERE ... claimed_unix_ms <= claimCutoff`（`coordination.go:258`）是它的实现。
- 加分点：指出 `topic` 字段**只写不读**（§3.6），说明「预留字段」是设计债。

**题 3（世界状态与基准）**
> 协调器在认领 intent 时保存了 `WorldRevision`、`EntitySourceSequence`、`RobotSourceSequence`
> 与 `CommandStartedAt`。请回答：为什么**只**比较「方块在目标区」不够？
> 请给出至少三种「方块确实在目标区，但这次完成上报仍然必须被拒绝」的场景，
> 并指出代码里分别由哪一行拒绝。

*答案要点*：
三种场景与对应拒绝点：
1. **方块从来没有动过**——它在目标区已经 10 分钟了，robot 的抓取其实失败了。
   拒绝点：`evaluator.go:84` `ENTITY_EVIDENCE_NOT_POST_COMMAND_STABLE`
   （`ObservationCount < Basis+2`）与 `:87` `ENTITY_EVIDENCE_PREDATES_CLAIM`。
   测试：`TestPreexistingStableWorldCannotProveAClaimedPhysicalIntent`（`coordinator_test.go:183`）。
2. **世界版本根本没前进**——没有新的观测进来，快照是旧的。
   拒绝点：`evaluator.go:76` `WORLD_REVISION_NOT_ADVANCED` → `Waiting`。
3. **有人从外部把方块挪过去了**——不是这次命令的功劳。
   拒绝点：`:90-91` `ENTITY_EVIDENCE_PREDATES_COMMAND`（同源序列不前进）；
   测试 `TestExternalBlockMovePreventsVerifiedCompletion`（`coordinator_test.go:227`）。
4. （加分）**机器人还夹着东西**——`:139-141` `ROBOT_STILL_HOLDING_ENTITY`（`RetryableFailure`）；
   **资源 token 不匹配**——`:150` `FENCING_TOKEN_MISMATCH`（`FailedSafe`）。
- **核心概念**：basis 把「命题为真」升级为「命题**因这次动作**而为真」。
  通用 coding agent 没有这个概念，因为它不需要——但**任何操作物理世界的系统都需要**。

---

## 源码索引

以下为本文引用过的全部 `文件:行号`。

### fleet/lease
- `fleet/lease/manager.go:9-14`（四个错误值）、`:16-21`（`Grant`）、`:23-29`（`Manager` 接口）
- `fleet/lease/memory.go:10-21`（`MemoryManager`）、`:23-42`（`Acquire`，`:38` 递增）、
  `:44-57`（`Renew`）、`:59-75`（`Transfer`，`:71` 递增）、`:77-82`（`Validate`）、
  `:84-92`（`Release`）、`:94-106`（`validateLocked`，`:99` 过期、`:102` token 比对）、`:108-116`

### fleet/redis
- `fleet/redis/lease.go:17-28`（acquire 脚本，`:24` `INCR`）、`:30-37`（renew 脚本）、
  `:39-48`（transfer 脚本，`:44` `INCR`）、`:50-56`（validate 脚本）、`:58-65`（release 脚本）、
  `:133-146`（`runToken`）、`:148-153`（hash-tag 键推导）、`:155-157`（`grant`，本地 `ExpiresAt`）、
  `:169-181`（`mapLeaseError`）

### fleet/coordinator
- `fleet/coordinator/coordinator.go:1-13`（包注释：顺序栅栏）、`:34-58`（`IntentStatus`，`:45-57` `UNKNOWN_OUTCOME` 论证）、
  `:61-102`（`IntentNode`）、`:119-143`（四个错误）、`:145-153`（`IntentClaimLease`）、
  `:155-168`（`ReconcileDecision`）、`:174-192`（`Coordinator` 结构）、`:194-207`（`taskState`/`persistedState`）、
  `:210-266`（构造函数与 `WithWorld`/`WithResourceLeases`/`WithCatalogLookup`）、
  `:268-301`（`WithLeadership`/`RenewLeadership`，`:274` `leader/<worldID>`）、
  `:303-347`（`reclaimStaleLocked`，`:308-309` 论证，`:338` `UNKNOWN_OUTCOME`）、
  `:349-422`（`ensure`，`:405` 幂等键，`:405-419` 版本冲突恢复）、
  `:424-532`（`NextIntent`，`:428`/`:431`/`:434`/`:440`/`:444`/`:445`/`:449-451`/`:452-454`/`:455-457`/`:458-472`/`:473-491`/`:492-508`/`:509-511`/`:513`/`:517-527`）、
  `:534-559`（`advanceTaskState`）、`:561-569`（`prefixSucceeded`）、
  `:571-596`（`CompleteIntent`）、`:598-837`（`CompleteIntentRevision`，`:633-641`/`:642-644`/`:648-658`/`:659-662`/`:670-693`/`:694-726`/`:727-730`/`:737-775`/`:776-786`/`:809`/`:824-828`/`:829-835`）、
  `:839-850`（`auditableEvidence`）、`:852-883`（`postClaimEvidenceReason`）、`:885-911`（`publishResource`）、
  `:913`（`sharedBlockResourceID`）、`:915-936`（`changesTheScene`/`isSharedBlockHandoff`）、
  `:938-959`（`evaluateHandoffPredicate`）、`:961-1051`（`FailIntentRevision`，`:1019`/`:1022-1024`/`:1042-1048`）、
  `:1053-1167`（`ReconcileIntent`，`:1074-1084`/`:1104-1115`/`:1127-1137`/`:1146`/`:1156-1162`）、
  `:1169-1189`（`Snapshot`）、`:1191-1199`（`DomainEvents`）、
  `:1201-1251`（`DispatchOutbox`，`:1211-1212`/`:1220-1224`/`:1235-1247`）、
  `:1253-1277`（`snapshotLocked`）、`:1279-1321`（`persistLocked`，`:1294` eventID）、
  `:1333-1345`（`validateLeadership`）、`:1347-1366`（`reloadIfNeeded`）
- `fleet/coordinator/revisions.go:12-16`（错误与 `commandIdentity`）、`:20-27`（`hasRunningIntent`）、
  `:44-63`（`applyRevisionIdentity`）、`:65-82`（`nodesForRevision`）、`:84-120`（`RevisionBasis`）、
  `:122-156`（`ProposeRevision`）、`:205-260`（`ConfirmRevision`）、`:295-390`（`reconcileRevisionLocked`）
- `fleet/coordinator/coordinator_test.go:88`/`:118`/`:140`/`:183`/`:227`/`:266`/`:307`/`:341`/`:385`/`:469`/`:496`/`:519`/`:546`/`:604`/`:641`/`:673`/`:706`/`:734`/`:773`/`:790`/`:806`/`:839`/`:871`
- `fleet/coordinator/revisions_test.go:274-324`（`:287-298` 低 token 拒绝不写事件；`:315-323` 精确重复幂等）
- `fleet/coordinator/harness_test.go:11`

### fleet/eventlog 与 fleet/mysql
- `fleet/eventlog/store.go:11`（`ErrVersionConflict`）、`:13-14`（`OutboxClaimTTL`）、
  `:16-21`（`AggregateState`）、`:23-35`（`DomainEvent`）、`:37-46`（`OutboxEntry`）、
  `:48-54`（`Checkpoint`）、`:56-62`（`CommitRequest`）、`:64-72`（`Store` 接口）
- `fleet/eventlog/memory.go:43-109`（`Commit`，`:62-68` 查重，`:65` 幂等键拼接）、
  `:111-129`（`ListEvents`）、`:139-167`（`ClaimOutbox`，`:154` TTL 判定）
- `fleet/mysql/coordination.go:15-53`（DDL，`:33` 唯一键，`:45` pending 索引）、
  `:55-66`（`ensureCoordinationSchema`）、`:68-83`（`LoadState`）、
  `:85-188`（`Commit`，`:86` 隔离级别，`:96-108` CAS，`:114-132` 状态写，`:136-154` 事件写，`:155-166` outbox 写，`:172-181` checkpoint 单调写）、
  `:190-222`（`ListEvents`）、`:224-239`（`LatestCheckpoint`）、
  `:241-290`（`ClaimOutbox`，`:254` cutoff，`:259` `FOR UPDATE SKIP LOCKED`）、`:292-305`（`ReleaseOutbox`）、`:307-320`（`AckOutbox`）

### fleet/worldhub 与 core/worldmodel
- `fleet/worldhub/hub.go:16-25`（`Hub`）、`:27-35`（`New`）、`:37-59`（`NewPersistent`）、
  `:61-116`（`Ingest`，`:67`/`:70-73`/`:81-89`/`:95-97`/`:103-106`/`:107-114`）、
  `:118-125`（`Snapshot`）、`:127-167`（`Subscribe`，`:134-137` 重同步判定）
- `fleet/worldhub/store.go:21`（`ErrSnapshotNotFound`）、`:23-28`（`SnapshotStore` 注释）、
  `:30-38`（`FileStore` 注释：不提供分布式 leadership 或 fencing）、`:40-44`（文件信封）、
  `:46-73`（`OpenFileStore`，`:68` flock）、`:75-101`（`Load`，`:92-95` 校验和）、
  `:103-145`（`Save`，`:131-144` 临时文件+Sync+Rename+目录 Sync）、`:165-178`（严格 JSON 解码）
- `core/worldmodel/projector.go:20-37`（`Projector`）、`:66-95`（`Apply` 四道过滤）、
  `:97-118`（`recordSource`/`recordHighWater`，`:114-117` recovered 标记）、
  `:120-132`（`unchangedStaticEntity`）、`:140-184`（`reduce`）、
  `:186-240`（`snapshotLocked`，`:197-198`/`:217-219`/`:226-232`/`:238-240`）
- `core/worldmodel/types.go:20-27`（`EvidenceRef`）、`:29-44`（`RobotState`）、`:46-57`（`EntityState`）、
  `:59-66`（`ResourceState`）、`:68-77`（`SourceState`）、`:84-96`（`Snapshot`）、`:98-`（`Delta`）
- `core/worldmodel/checkpoint.go:20-21`（`ObservationIDs`/`SourceSequences`）、`:27-39`（`Checkpoint`）、
  `:44-55`（`Clone`）、`:57-70`（`RestoreProjector`）、`:83-125`（`validateCheckpoint`）
- `core/worldmodel/predicate.go:19`（`ReasonEvidenceStale`）、`:42-118`（`EntityInside`/`EntityStable`/`RobotHeld`/`ResourceOwner`/`SourceFresh`/`All`）、`:138-`（`evidenceStale`）
- `core/harness/evaluator.go:1-4`（包注释）、`:11-18`（`Status`）、`:31-39`（`EvidenceBasis`）、
  `:41-44`（`ExpectedResource`）、`:46-54`（`Input`）、`:69-164`（`Evaluate`，`:76`/`:84`/`:87`/`:90-91`/`:143-158`）、`:166-183`

### fleet 其他
- `fleet/server.go:117-154`（路由表）、`:189-212`（世界 WS 与 `RESYNC_REQUIRED`）、
  `:276-350`（intent 端点）、`:676-689`（`deviceRobotIdentity`）
- `fleet/queue/queue.go:1-10`（包注释）、`:21`（`AnyRobot`）、`:56-`（`Enqueue`）
- `fleet/registry/registry.go:1-7`（包注释：设备租约）、`:16-47`（能力/工具/观测源声明）、`:49-67`（`Device`）
- `fleet/gateway/gateway.go:6-7`（心跳续租注释）、`:99-100`（默认 15s 租约）、`:138-160`（TLS 凭据与 CN）、
  `:175-242`（`Register`）、`:244-337`（`Link`）、`:352-370`（`handleHeartbeat`）
- `fleet/fusion/fusion.go`（确定性融合）
- `fleet/revisions.go:16`/`:53`/`:98`/`:132`/`:162`/`:177`/`:195`

### edge
- `edge/runtime/runtime.go:18-27`（错误）、`:29-39`（能力枚举）、`:43-53`（`Result`）、
  `:58-81`（`Command` 19 字段）、`:83-104`（`Capability`，`:99-103` `MutatesWorld`）、
  `:106-120`（`Snapshot`）、`:122-129`（`ValidateProtocol`）、`:148-164`（`CanExecute`）、`:176-185`（接口）
- `edge/runtime/dispatch.go:8-10`（`MaxDispatchBudget`）、`:13-15`（注释）、`:16-34`（`CommandAtDispatch`）
- `edge/worker/worker.go:1-9`（包注释）、`:38-52`（`TaskCloud`/`RobotRuntime`）、
  `:53-98`（`Config`）、`:143-201`（`Run`/`taskLoop`）、`:203-235`（`retryTask`）、
  `:237-266`（`processTask`）、`:268-352`（`runIntent`，`:293-300` 本地重造计划，`:340-344` completion 重试）、
  `:354-393`（`preparePolicyCommandWithRecovery`）、`:395-423`（`retryWorldCompletion`）、
  `:425-448`（`commandForIntentNode`，`:443-446` 只有物理步骤才带 fencing）、`:475-491`（`preflight`）
- `edge/worker/tasksource.go:1-9`（两种任务源）、`:23-80`
- `edge/worker/observation.go:31`（`ObservationsFromTelemetry`）
- `edge/robotclient/client.go:399-412`（默认安全档位推导，`:407-408` 注释）、`:488-501`（`Cancel`/`EmergencyStop`）、
  `:515-561`（`commandToProto`，`:536-539` profile 选择）
- `edge/recovery/classifier.go:1-2`（包注释）、`:11-23`（六类 `Class`）、`:24-32`（`Activity`）、
  `:34-73`（`Classify`，`:68-73` `ExecutionReconcile`）

### cmd
- `cmd/fleet-control-plane/main.go:37-38`（flags）、`:45-64`（store 选择）、`:105-128`（队列扇出）、
  `:136-141`（世界构建）、`:143-151`（租约 TTL 与 Redis/内存领导者管理器）、`:152-169`（协调器装配）、
  `:170-193`（领导者续租 ticker）、`:194-207`（outbox ticker 500ms/64 条）、`:209-222`（网关）、
  `:224-232`（server 装配）、`:258-274`（`buildGateway`，`:262-265` 无证书则禁用）、
  `:311-329`（`buildWorld`，`:315-318` 未设则内存世界）
- `cmd/local-agent/main.go:30`（import `edge/worker`）、`:31`（import `fleet/worldhub` —— **§0 冲突点**）、
  `:78-99`（flags）、`:90`（`-robot-safety-profile`）、`:106-111`（非环回绑定默认拒绝）、
  `:148-167`（配置路径搜索顺序）、`:242-261`（sqlite 与 robotclient 装配，`:246` `agent.db`）、
  `:271-279`（parser / planner / `tasks.NewService`）、`:280-284`（内存世界 + worker 观测映射器，
  `Cloud`/`Source`/`Link`/`Runtime` 全空）、`:285-306`（`publishTelemetry`，`:300` 只用纯函数
  `ObservationsFromTelemetry`）、`:307-314`（runner）、`:316-321`（观察者复用同一遥测源）、
  `:323-330`（信号与遥测 ticker 1s）、`:336`（UDP 发现）、`:339-409`（恢复执行器装配，
  `:401-404` **No Approve port** 注释）、`:415-427`（`localapp.New` 装配链）、`:438`（`Start`）、
  `:440-445`（agent runtime，`TANGYING_AGENTS`）、`:501-519`（配置热重载 + console server）、
  `:528-543`（`console-session`/`console-address`，均 0600）、`:544-570`（HTTP server 与优雅关停）、
  `:573-596`（事故目录与 `defaultDataDir`）
- `cmd/local-agent/agentruntime.go:32`（`TANGYING_AGENTS=task` 关闭观察）、
  `:64-69`（**「observes execution; it is not part of it」**）
- `cmd/local-agent/safety_profile_test.go:9-20`（安全档位必须显式）
- `internal/localapp/app.go:1-4`（**包注释：deliberately has no distributed claim or task-lease protocol**）、
  `:63-66`（`queued`/`active`/`pauses`/`resumes` 四张内存 map）、`:254-259`（`Start` 先 `reconcile`）、
  `:268-270`（`ErrApprovalRequired`）、`:321-347`（`Pause`/`Resume` 同样检查批准）、
  `:467-481`（`work` 队列循环）、`:538-662`（`run`，`:558-573` 续跑语义）、
  `:678-691`（**重启把执行中任务一律降为 `RECOVERABLE_FAILURE`，不自动重放**）
- `internal/localapp/recovery.go:13-19`（**「a software path that could clear it would make the wait theatre」**）、
  `:24-83`（`Recovery()`：`CompletedStepIDs` / `UncertainStepIDs` / `RequiresReconciliation`）、
  `:85-101`（`checkRevisionRecovery` 禁止绕过不确定性）
- `internal/autorecovery/supervisor.go:1-35`（包注释：只有 `read_only` 是自动的）、
  `:183-190`（**硬线：非只读即停**）
- `middleware/contracts.go:1-3`（端口中立的包注释）、`:21-55`（`Queue`/`Cache`/`Lease`/`Locker` 等端口，
  `:44-53` `Lease` 显式带 fencing token）、`:62-70`（`StepStatus` 枚举）、
  `:84-104`（`StepOutcome` = `HAPPENED`/`NEVER_ACTED`/`ABANDONED`）、`:150-160`（`ExecutionStore`）
- `middleware/sqlite/store.go:15`（`modernc.org/sqlite`，纯 Go）、`:23`（`Open`）、
  `:27-28`（PRAGMA WAL + foreign_keys）、`:31-46`（`tasks`）、`:47-57`（`task_events`）、
  `:58-68`（`step_runs`）、`:70-71`（**`step_runs_idempotency_idx` 唯一部分索引**）、
  `:76-91`（四个 ensure 的调用顺序，`:80` **`ensureFleetSchema` 无条件执行**）、
  `:95-173`（`PRAGMA table_info` + `ALTER TABLE ADD COLUMN` 增量迁移）、
  `:175-196`（`task_revisions` / `task_revision_events`）、`:200-295`（增删改查与 `ReconcileStep`）
- `middleware/sqlite/fleet.go:11`（**import `fleet/eventlog` —— §0 冲突点**）、
  `:15-57`（四张 fleet 表 DDL）、`:61-332`（`eventlog.Store` 的 SQLite 实现）
- `middleware/sqlite/evidence.go:15`（接口断言）、`:18-34`（`observation_evidence` 与索引）、
  `:80-86`（同 id 重放的 SHA256 比对）、`:106-127`（双预算过期，`expiration never substitutes
  a different or newly stamped frame`）、`:167-180`（`Evidence` / `ListEvidence` 分页）
- `middleware/sqlite/retention.go:44-55`（保留窗口）、`:63-76`（可丢事件类型白名单）、
  `:95`（`LedgerSize`）、`:126-129`（`PruneLedger` 拒绝零窗口）
- `tests/architecture/dependencies_test.go:31`（`TestCorePackagesDoNotImportConcreteInfrastructure`）、
  `:32-40`（被测包集合，**不含 `./cmd/...` 与 `./internal/localapp/...`**）、
  `:113-138`（`forbiddenImport` 禁列表，**不含 `fleet`/`edge/cloudclient`**）

### proto 与 sim
- `proto/robot/v1/robot.proto:9-19`（`RobotRuntime` 七个 RPC）、`:21-45`（服务目录）、
  `:47-70`（`CapabilityInfo`，`:63-66` `mutates_world` 注释）、`:156-176`（`SkillCommand` 19 字段）
- `proto/fleet/v1/fleet.proto:9-19`（服务注释与数据/控制面分离）、`:20-27`（`FleetGateway`）、
  `:29-41`（`RegisterRequest`）、`:61-71`（`ObservationSource`）、`:73-79`（`RegisterResponse`）、
  `:81-92`（`LinkMessage` oneof）、`:94-113`（`ObservationEnvelope`）、`:140-166`（`Heartbeat`/`TelemetrySample`，`:164` `held`）、
  `:177-186`（`OccupancyGrid`）、`:205-210`（`ServerCommand`）、`:212-218`（`Ack`）
- `sim/mujoco/tangying_sim/server.py:130-149`（目录 revision 哈希）、`:150-155`（`register_resource`）、
  `:429-478`（`_validate`，`:439` `COMMAND_EXPIRED`，`:443` `IDEMPOTENCY_KEY_REQUIRED`，`:448` `TOOL_CATALOG_STALE`，
  `:462-469` 单调采纳，`:471-472` `FENCING_TOKEN_STALE`，`:474-476` `adopt_fencing_token`）、`:481-`（`_dispatch`）、
  `:614`（`world_revision_basis` 唯一被读取处）—— **该文件 `approval` 零命中、`task_revision`/
  `aggregate_version`/`step_id` 零命中**（§5.4）
- `sim/mujoco/tangying_sim/shared_handoff.py:45`（`_fencing_token = 1`）、`:61-63`（getter）、
  `:90-108`（`transfer`，`:100-105` `StaleHandoffToken`）、`:110-117`（`view_for`）、`:129-146`（`on_picked`）、`:148-172`（`on_placed`）
- `sim/robocasa/tangying_robocasa/world.py:151,499-500`（`adopt_fencing_token`）
- **`robot/gateway/tangying_robot_gateway/safety.py:100-128`（真机安全监督 `evaluate`）**：
  `:106-108` `COMMAND_EXPIRED`/`LEASE_REQUIRED`/`LEASE_TOO_LONG`、`:110` `IDEMPOTENCY_KEY_REQUIRED`、
  `:112` `SAFETY_PROFILE_REJECTED`、**`:117-118` `if physical and not command.approval_id: → APPROVAL_REQUIRED`**
  （§5.4：这条门禁在仿真里不存在）
- `robot/gateway/tangying_robot_gateway/service.py:45,279`（`approval_id` 透传）、
  `tools/registry.py:48,70`（作为 `Command` 字段下传）、`runtime.py:120`（字段定义）、
  `tests/test_safety.py:55`、`tests/test_plugin_backend.py:88-91`（审批门禁测试）
- `robot/mcp/tangying_mcp/server.py:1`（「approval and physical execution stay in Fleet」）、
  `:341-342`（「No tool grants approval or bypasses safety」）

### 脚本、测试与文档
- `scripts/fleet-certs.sh:1-82`（CA / 服务器 / 每机器人客户端证书；`:12-17` TTL 参数）
- `tests/e2e/test_fleet_faults.py:1-7`（**核心诚实声明**）、`:23-151`（`FAULT_CHECKS` 十类）、
  `:154-167`（`test_fault_boundary_preserves_consistency_invariant`）、
  `:175-215`（`test_edge_disconnect_reconnect_completes_without_false_advance`，`:185`/`:192-196`/`:204`/`:211-212`）
- `README.md:109`（章节标题「分布式：不是把程序拆到多台机器上」）、
  **`:111`（「分布式」= 故障假设，全文唯一定义）**、`:115-118`（四件事：单写协调+资源归属 /
  租约+fencing / 事件+Outbox+幂等键 / 云端慢环+边缘快环）、`:120-131`（两种形态共享同一
  Runtime/工具目录/观测契约/世界快照）、**`:135`（边界声明：主线一台机器人；恢复能力限于单主进程
  重启范围内的一部分状态恢复，跨主机共识与跨存储事务仍在待验证清单上）**
- `docs/architecture/distributed-agentos.md:7`（Runtime 边界）、`:19`（部署替换边界不是自动故障切换）、
  `:21-25`（已实现的协同闭环）、`:27-31`（持久化与单写范围）、`:33-39`（**仍须独立验证的范围**）
- `docs/architecture/fleet-cloud.md:12-23`（拓扑）、`:38-41`（交接闭环三步）、`:59-64`（组件与端口）、
  `:66-83`（云端环境变量）、`:85-97`（edge-worker 环境变量）、`:99`（`FLEET_WORLD_SNAPSHOT_PATH`）、
  `:101-123`（完整流程六步，`:110` 声明租约 2m，`:115-120` 四条件推进 + fail-closed）、
  `:125-139`（mTLS 接入，`:130-132` 身份边界，`:135-136` 指数退避）、
  `:155-178`（God View）、`:180-192`（地图融合）、`:194-203`（安全边界，`:202-203` LLM 不能设安全字段）、
  `:209-226`（API 摘要）、`:228-232`（无 Docker 开发画像）
- `docs/architecture/why-distributed.md:13-16`（三句话）、`:26-30`（三条硬约束）、
  `:46-51`（反面成立与复杂度代价）、`:66-77`（与 coding agent 对照表）、`:78-93`（重试的代价，
  `:89` 含未落实标记「待取证」）、`:99-112`（真正有价值的三样）、`:127-133`（分布式代价清单，
  `:129` 是「网络分区、时钟漂移、状态分歧」的唯一成句出处）、`:160`（**「0 处」——与代码不符**）、
  `:166-186`（三个必须说清的前提与端口差异）、`:221-227`（**这份文档没证明的事**）
- `docs/architecture/fleet-paper-loop.md:1`（标题即「**早期**…档案」）、**`:3`/`:7`（自我声明历史档案，
  计数不可套到当前代码）**、`:93`（**「云端协调器状态在内存中（单实例画像）」——历史声明，
  仅对 `FLEET_STORE=memory` 成立**，见 §8.4-4）
- `docs/architecture/multi-robot.md:5-11`（已实现的边界，`:11` 三源冲突停止动作）、
  `:13-22`（token 1→2→3 交接）、`:24`（工具成功不能跳过环境确认）、`:28`/`:30`（范围与实机前置条件）
- `docs/production/deployment-and-capacity.md:3`（效力边界）、**`:11`（不是自动 HA 服务；同一单写关系；
  即使 DB/Redis 是 HA 也不自动补齐 fencing 与 saga）**、`:15`（勿当作多写协调机制）、
  `:18`（独占文件锁 + 失败关闭）、`:19`（delta 不恢复；**恢复快照不是当前现场真值**）、
  `:21`（跨主机 HA / 共享盘多写 / 全局原子事务 / 长期故障容忍 / 自动切主仍需独立工程）、
  `:25`（不要把开发凭据带入生产）、`:27`（RBAC/租户隔离/审计留存待补齐）、
  `:31`（**尚无认证容量**）、`:33`（指标清单，含 Outbox lag，「成功率不能掩盖安全失败」）、
  `:35-37`（升级回滚：回滚不降低 token、不重写 TaskRevision）、`:39`（未来分片/HA 方案不能算已实现）
- **`docs/production/operations-and-failures.md`（故障矩阵主文件）**：`:5`（安全不变量总纲）、
  `:7`（统一检查顺序）、`:9`（`/healthz` 成功不等于 Runtime 就绪）、`:11`（错误分层）、
  `:21`（登录/JWT）、`:22`（证书）、`:23`（DNS/路由/CIDR）、**`:24`（时钟漂移）**、
  **`:25`（MySQL 不可用；「不从 Redis 反写 Task 真值」）**、**`:26`（Redis 不可用/重复；
  「至少一次必须由幂等吸收」；「从 Outbox 重放」）**、`:27`（Outbox 卡住）、`:28`（磁盘/内存/CPU）、
  **`:34`（leader lease 丢失；防止复发栏 = 待建）**、`:35`（重复事件/命令）、`:36`（倒序事件）、
  `:37`（revision gap）、`:38`（CAS 冲突）、`:39`（WAITING_SAFE_POINT）、`:40`（任务/intent lease 超时）、
  **`:41`（进程崩溃；「重启不自动重放副作用」）**、`:47`（机器人离线）、`:51`（工具成功但 Harness 失败）、
  `:52`（stale observation）、**`:53`（矛盾观测；「不投票决定资源所有权」）**、`:55`（source sequence 回退）、
  **`:56`（custody/fencing 冲突；「旧 token 永不复活」；防止复发栏 = 待建）**、`:57`（Harness timeout）、
  `:58`（急停不自动解除）、`:60`（异构适配器）、`:62`（`EXECUTION_OUTCOME_UNKNOWN`）、
  `:64`（旧帧三因；「禁止把 observedAt 改为发送时刻」）、`:79-90`（灾难恢复 8 步，
  `:84` 不以更高 token 重建 custody）、`:92-94`（学习策略 6 状态）、`:98`（单主世界快照故障）、
  `:100`（服务重启不解除锁存）、`:104`（RoboCasa 单回合单向）、
  **`:106`（反向动作尚未实现；不得降低 token 伪造支持）**
- `docs/production/architecture.md:93`（恢复后只接受更新证据）、`:120`（**不提供跨主机共识**）
- `docs/production/configuration-and-security.md:31-37`（世界/租约相关变量默认值与约束）
- `docs/production/data-contracts.md:143`（checkpoint 与旧证据降级）
- `docs/production/sim-to-real.md:16`（**protobuf 已带 `task_revision`/`aggregate_version`/`step_id`，
  但 Python `Command` 当前未映射并独立检查这三个字段**——一处「线协议有、Runtime 未强制」的缺口）
- `docs/operations/deployment.md:1`/`:5`（唯一的归属判据）、`:7`（云端与机器人端是两项独立放行结论）、
  `:22`（路线判据）、`:67-70`（云端进程与端口，`:67` 与 `fleet-cloud.md:62` 表述不一）、
  `:79`（云端只下发命令与租约，不直接驱动硬件）、`:105-113`（本地单机与后台单元）、
  `:129-136`（端口一览表）
- `docs/superpowers/specs/2026-08-20-distributed-agentos-world-harness-design.md:13-20`（目标）、
  `:27-39`（基线证据）、`:46-51`（**当时的不足**）、`:82-86`（本轮不包含）、`:88-127`（选定路线与架构图）、
  `:167-201`（`CommandEnvelope` 与 **`:201` Runtime 不收来源字段**）、`:400`（重试预算）、
  `:406-418`（**九类故障注入矩阵**）、`:487-535`（测试策略）、`:539-549`（完成标准）
- `deploy/cloud/docker-compose.yml:61-69`（生产环境变量固定值）
- `VERSION`（`0.6.0`）
