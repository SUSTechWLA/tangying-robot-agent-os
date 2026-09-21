# 第 9 章 本地单机形态：砍掉分布式之后，剩下什么

> **本章的核心命题**
>
> 这一章是一次**对照实验**。
>
> 如果"分布式"真的不是这套系统的价值所在，那么把它全部砍掉之后，
> 剩下的东西应该**依然是一个完整的机器人 Agent 运行时**。
>
> Local Agent 就是那个实验。它砍掉了协调器、租约、outbox、fencing——
> **但闭环契约、失败分类、证据与对账，一个都没砍。**

---

## 9.1 一个设计者自己写下的"我砍了什么"

`internal/localapp/app.go` 的包注释，是整个第 8 章论点的**代码级证明**：

```go
// Package localapp owns the single-user laptop execution lifecycle.
// It deliberately has no distributed claim or task-lease protocol:
// SQLite is the business-state authority and one worker serializes physical work.
```

**"It deliberately has no distributed claim or task-lease protocol."**

注意三个词：**deliberately**（刻意）、**orchestration**（协调）、以及理由——**"SQLite 是业务状态权威，一个 worker 串行化物理工作"**。

这句话本身就说明了设计者的判断：

> **单写者 → 不需要 claim 协议。**

claim 协议存在的唯一理由是"多个写者可能同时认领同一份工作"。如果只有一个 worker，这个故障不存在，**协议就是纯负债**。

这正是第 8 章开头的判据在起作用：**机制的成本必须用故障集合来偿还。**

---

## 9.2 依赖事实：一次必须说清楚的三段式表述

先纠正一个流行但错误的说法。

项目文档 `docs/architecture/why-distributed.md:160` 有一张表：

| 检查 | 结论 |
| --- | --- |
| `cmd/local-agent` 引用云端 / fleet | **0 处** |

**这条在代码上不成立**（详见 6.11.5）。准确的表述必须是**三段式**：

| 层面 | 事实 |
| --- | --- |
| **运行时依赖** | **零。** Local Agent 从不构造任何云端客户端，不做任何云 I/O |
| **编译期依赖** | **非零。** 直接 import 含 `edge/worker`、`fleet/worldhub`；`middleware/sqlite` 反向依赖 `fleet/eventlog`；`strings` 在二进制里命中 fleet 符号 227 次 |
| **代码复用** | **有。** `fleet/worldhub` 与 `fleet/eventlog` 是被两条画像**共用的库**，不是云端专属 |

### 为什么"运行时零依赖"是真的

两个关键装配点：

```go
// cmd/local-agent/main.go:280
world = worldhub.New(...)              // ← 不是 NewPersistent

// cmd/local-agent/main.go:281-284
worker.Config{
	Cloud:   nil,                      // ← 三个字段全部留空
	Source:  nil,
	Link:    nil,
}
```

**`worldhub.New` vs `NewPersistent` 的区别是本质的**：

| | `New` | `NewPersistent` |
| --- | --- | --- |
| 存储 | 内存 | 文件 + `flock` |
| 用途 | **进程内世界投影** | 单主 checkpoint |
| 崩溃后 | 世界丢了 | 世界保留 |

Local Agent 用 `New`——**它的世界不需要跨进程重启存活**，因为重启后它会重新从运行时的遥测建立世界。

（这是刻意的简化，不是遗漏。第 8 章讲的 `recoveredObservationIDs` 机制在 Local 形态下**不适用**——因为没有从磁盘恢复的旧观测。）

### 一个必须指出的"方向性污染"

`middleware/sqlite` 依赖 `fleet/eventlog`，并在**打开本地数据库时无条件建 4 张 fleet 表**（`store.go:80`）。

这带来两个后果：

**后果 1：Local Agent 的二进制里带着一整套分布式协调原语的代码。**

**后果 2：那 4 张表建了但不用。** Local Agent 没有任何 outbox 消费者，没有协调器。

**"建了但不用"这一条很有教学价值**：它说明作者把"持久化的分布式协调原语"当成**可复用的库**（SQLite 版实现了 `eventlog.Store` 接口），而"**是否启用多写者协议**"是**装配层**的决定，不是存储层的决定。

**但如果只从工程角度评价**，这是一个**依赖方向的问题**：`middleware`（基础设施层）依赖 `fleet`（业务层）是反向的。正确的做法是把 `eventlog.Store` 接口提到一个两者都能依赖的位置。

**教学要点**：目录结构会**慢慢撒谎**。`fleet/` 这个名字承诺"这是云端的东西"，但代码实际把它当库用。**用目录表达部署边界，随着代码复用会逐渐失真。**

---

## 9.3 刻意简化清单

Local Agent 砍掉了什么、保留了什么，逐项对照：

| 机制 | Cloud Fleet | Local Agent | 证据 |
| --- | --- | --- | --- |
| **claim / 租约协议** | 有 | **刻意没有** | `localapp/app.go:1-3` 包注释 |
| **世界持久化** | `NewPersistent` + `flock` | **内存** | `main.go:280` `worldhub.New` |
| **outbox / 事件日志消费者** | 有（500 ms ticker） | **没有**（表建了不用） | `middleware/sqlite/fleet.go:14-59` |
| **资源 fencing** | 有 | **没有**（不构造 `lease.Manager`） | 传递依赖里无 `fleet/coordinator` 的**调用** |
| **安全档位** | 从 `RobotProfile` 推导 | **必须显式配置** | `main.go:90` `-robot-safety-profile`；`safety_profile_test.go:9-18` 断言"隐式安全档位……运行时策略必须自己选默认值" |
| **机器人身份** | 每台一个 ID | **`robot-local` 单一身份** | `main.go:282,307,308` |
| **步骤幂等键** | ✅ 有 | ✅ **保留** | `step_runs` 表的 `step_runs_idempotency_idx` 部分唯一索引 |
| **动作后证据门禁** | ✅ 有 | ✅ **保留** | 同一套 `core/closedloop.Gate` |
| **`UNKNOWN_OUTCOME` 的人工对账** | ✅ 有 | ✅ **保留** | 同一套 `ReconcileStep` + `WHERE reconcile_outcome = ''` |

**最后三行是本章的收束点，也是整本书最重要的一个实证。**

> **Local Agent 砍掉了协调，但没有砍掉闭环。**

代码结构本身就证明了 `docs/architecture/why-distributed.md:105-112` 那句论断：

> **去掉分布式，这三样依然在；去掉这三样，就只剩一个很慢的遥控器。**

### 重启行为：一个具体的验证

`internal/localapp/app.go:678-691`：

```
重启把执行中任务一律降为 RECOVERABLE_FAILURE，不自动重放
```

**注意：不自动重放。**

一个"简单"的实现会在重启后从头开始跑任务——毕竟这是个单机系统，没有别人看着，重跑一遍最省事。

**但那正是这个系统禁止的事。** 一个执行到一半的任务，重启后**可能已经动了**（杯子拿起来了），自动重放就是重复物理动作。

所以 Local Agent 的重启行为与 Cloud Fleet 是**同一个语义**：

| 形态 | 重启后的行为 |
| --- | --- |
| Cloud Fleet | `reclaimStaleLocked` → `UNKNOWN_OUTCOME`，不退回 READY |
| **Local Agent** | **降为 `RECOVERABLE_FAILURE`，不自动重放** |

**两种形态，一条规则。**

### 谁可以执行恢复动作

`internal/autorecovery/supervisor.go:183-190`：

```
只自动跑 read_only 的恢复动作
```

这就是文档里那句"**只读步骤无人执行，改动仍等人**"的实现——见 7.6。

---

## 9.4 SQLite schema

`middleware/sqlite.Open`（`store.go:21-82`）依次建五组表：

| 组 | 表 | 建表位置 | **Local 是否真用** |
| --- | --- | --- | --- |
| 任务 | `tasks` | `store.go:31-46` | ✅ 用 |
| 任务事件 | `task_events`（PK `(task_id, sequence)`，FK 级联删除） | `store.go:47-57` | ✅ 用 |
| **步骤账本** | `step_runs`（PK `(task_id, step_id)`，含 `reconcile_outcome/actor/note/at`；**部分唯一索引** `step_runs_idempotency_idx ON (idempotency_key) WHERE idempotency_key <> ''`） | `store.go:58-68` | ✅ 用（幂等与对账） |
| 修订 | `task_revisions` / `task_revision_events` | `store.go:175-190` | ✅ 用 |
| 证据 | `observation_evidence`（+ `observation_evidence_task_idx`） | `evidence.go:18,34` | ✅ 用 |
| **协调** | `fleet_graph_states` / `fleet_domain_events` / `fleet_outbox` / `fleet_checkpoints` | `fleet.go:16-56` | ❌ **建了但不用** |

两个 PRAGMA（`store.go:27-28`）：

```sql
PRAGMA journal_mode=WAL
PRAGMA foreign_keys=ON
```

### 那个"部分唯一索引"值得单独讲

```sql
CREATE UNIQUE INDEX step_runs_idempotency_idx
  ON step_runs (idempotency_key)
  WHERE idempotency_key <> '';
```

**"部分"（partial）是关键词。** 一个普通的唯一索引会拒绝多个 `idempotency_key = ''` 的行——但空键意味着"这一步还没有幂等键"（比如旧数据、或还没走到那一步），这种行**可以有多条**。

**语法：`WHERE idempotency_key <> ''` 让唯一性只作用于"有键的行"。**

更漂亮的是：它把"步骤幂等"这个**业务不变量**变成了一条**数据库约束**——不靠应用层判断，靠索引。

这和第 3 章讲的对账一次性约束是同一种思路：

```sql
WHERE reconcile_outcome = ''     -- 只能从空到非空
```

**"能用数据库约束表达的不变量，不要用应用层 if。"**

### 数据库位置

```
<dataDir>/agent.db
```

`dataDir` 由配置决定，`os.MkdirAll(dataDir, 0o700)`（`main.go:243`）——**只有属主可访问**。

（`0o700` 这个细节值得注意：机器人 agent 的数据库包含任务历史、证据索引、可能还有操作员备注。它不是公开数据。）

---

## 9.5 一次任务在 Local 形态下的完整链路

把前面几章的所有机制在**单机**语境下串一遍，看看哪些还在：

```
用户输入一句中文
   ↓
tasks.Service.Create
   ↓
agent.Parser（确定性优先 → 模型兜底）
   ↓
orchestration（LLMPlanner 或 DeterministicPlanner）
   ↓
Task{READY} + Revision{ApprovalRequired: true, RiskClass: "physical"}
   ↓
【人工审批】← 仍然需要
   ↓
edge/agent.Runner.RunControlled
   ├─ MarkStepStarted（落盘 STARTED）          ← 闭环
   ├─ grounder.Ground                          ← grounding
   ├─ planForIntent                            ← 计划
   └─ 逐步执行
        ├─ Invoke（带幂等键）                   ← 闭环
        ├─ 观测                                   ← 闭环
        └─ closedloop.Gate(declaration, dispatchedAt, evidence)   ← 闭环
             ├─ 通过  → MarkStepCompleted
             └─ 拒绝  → 保持 STARTED + ErrUnverifiedWorldMutation
   ↓
【结果未知时】StepReconciliation（人 + 理由，一次性）   ← 闭环
   ↓
任务账本（每一步的工具调用、证据 ID、失败分类、恢复动作）  ← 闭环
```

**把这张图与第 8 章的 Cloud Fleet 链路对比**：

| 环节 | Cloud Fleet | Local Agent |
| --- | --- | --- |
| 谁认领这一步 | **Coordinator + lease + fencing** | **本地队列（无协议）** |
| 命令从哪里来 | 云端 → Edge → Runtime | **同一进程 → Runtime** |
| 世界状态 | WorldHub + 持久化 + 订阅者 | **内存投影** |
| 跨机通知 | **Outbox → Redis → Edge** | **无** |
| 完成判据 | `harness.Evaluate`（basis + 谓词） | **`closedloop.Gate`（时间戳新鲜度）** |
| 未知结果 | `UNKNOWN_OUTCOME` + `ReconcileIntent` | **`UNKNOWN_OUTCOME` + `ReconcileStep`** |
| 证据 | 有 | **有** |

**读法**：左边第一栏到第四栏（协调）全部换了实现；**最后三栏（完成判据、未知结果、证据）一模一样。**

**这就是本章的结论，而且是可执行的验证——不是论证。**

---

## 9.6 恢复执行：只读的自动，改动的等人

Local 形态有一个 Cloud 形态没有的机制：**自动恢复**（`internal/autorecovery`）。它的设计原则值得单独讲。

### 一条分界线

```go
// internal/autorecovery/supervisor.go:183-190（语义还原）
只自动跑 Risk == read_only 的恢复动作
```

这句代码对应文档里的一句话：

> **只读步骤无人执行，改动仍等人。**

展开来说：

| 恢复动作的风险级 | 谁能执行 | 例子 |
| --- | --- | --- |
| `read_only` | **可以自动** | 再观测一次、读历史、查状态 |
| `bounded_write` | **必须有人批准** | 重做当前步骤（`task.retry-step`） |
| `never_automatic` | **永远不能自动** | 清除急停、改安全档位 |

三个风险级的定义（`agentruntime/recoverycatalog.go:31-40`）：

```go
RiskReadOnly      = "read_only"
RiskBoundedWrite  = "bounded_write"
RiskNeverAutomatic = "never_automatic"
```

配套的两个方法（`:335-358`）：

```go
func (a RecoveryAction) RequiresApproval() bool { return a.Risk != RiskReadOnly }
func (a RecoveryAction) Executable() bool {
	return a.Risk == RiskReadOnly || a.Risk == RiskBoundedWrite
}
```

**注意 `Executable()` 用的是白名单，不是黑名单。** 注释解释了这个选择：

> **一个没声明风险级的条目会默认可执行。**

所以它反过来写：**只有明确声明是可执行的风险级，才可执行。** 新增条目忘了填风险级 → **默认不可执行**。

**这是一个可以直接推广的安全设计原则：默认拒绝，白名单放行。**

### 为什么"只读可以自动"

因为只读动作**不改变物理世界**。一次自动的"再观测一次"最坏情况是浪费一次采集。

而"重做当前步骤"是 `bounded_write`——它会动机器人。

**判别标准不是"这个动作危不危险"，而是"它会不会改变物理世界"。** 前者是主观的，后者是可在类型上标注的。

### 一步一步的恢复执行

一个恢复动作的执行链路（这是第 11 章的内容，这里先给出轮廓）：

```
批准一步
   ↓
按目录声明的工具执行        ← 只能执行目录里声明过的工具
   ↓
复验                        ← 用新鲜证据验证
   ↓
记录
```

四步中每一步都值得注意：

| 步 | 关键约束 |
| --- | --- |
| 批准 | 恢复目录声明这个动作，且风险级非 `read_only` |
| 执行 | **只能执行目录里声明过的工具**——不能借"恢复"的名义执行任意工具 |
| 复验 | 用什么判据？**新鲜证据**，与正常步骤同一套 `Gate` |
| 记录 | 进同一份任务账本 |

**第三点是精髓**：恢复动作的完成判据，与正常动作**完全相同**。系统没有为恢复开一条"更宽松"的通道。

---

## 9.7 批准范围：模型不得执行批准时没出现过的物理动作

这是 Local 形态里一个非常重要的安全机制。

**问题**：操作员批准了一个恢复计划。计划里写了"重新观测 + 重做 `pick` 步骤"。然后模型（或某个执行器）能不能顺便执行一个**计划里没写**的 `place`？

**答案：不能。** 而且这个"不能"必须在代码里强制。

机制的要点：

| 约束 | 作用 |
| --- | --- |
| **批准绑定到具体动作集合** | 批准的那一刻，被批准的动作集合就固定了 |
| **执行时校验动作在批准范围内** | 不在范围内 → 拒绝 |
| **范围不能事后扩展** | 不能"批准一次，之后随便加" |

**教学要点**：这是一个"**批准的粒度**"问题。批准有两种语义：

| 语义 | 含义 | 风险 |
| --- | --- | --- |
| 批准一个**意图** | "我同意你处理这个情况" | 模型可以在意图范围内做任何事 |
| 批准一个**动作集合** | "我同意执行这 N 个具体动作" | 模型只能做这 N 个 |

这个系统选的是**后者**。代价是"批准时要看到具体动作"（所以恢复计划必须在批准前完整生成），收益是**批准之后的任何偏差都会被拒绝**。

**这与"人必须能看懂自己在批准什么"是同一条原则。**

---

## 9.8 与 Cloud Fleet 的对比：一张总结表

| 维度 | Local Agent | Cloud Fleet | 相同吗 |
| --- | --- | --- | --- |
| **完成判据** | `closedloop.Gate` | `closedloop.Gate` + `harness.Evaluate`（basis） | **核心相同**；Cloud 更严（多版本/序列校验） |
| **失败分类** | `closedloop.Classify`（177 码 / 8 类） | 同一个函数 | ✅ 完全相同 |
| **未知结果** | 保持 `STARTED` + 人工对账 | 保持 `STARTED` + `ReconcileIntent`（有租约语义） | ✅ 语义相同 |
| **证据** | 任务账本 + `observation_evidence` 表 | 任务账本 + `observation_evidence` | ✅ 相同 |
| **幂等** | `step_runs` 部分唯一索引 | `step_runs` 部分唯一索引 + Outbox 幂等键 | Local 少了跨进程幂等 |
| **审批** | 必须有（`RiskClass: "physical"`） | 必须有 | ✅ 相同 |
| **恢复** | 只读自动 / 改动等人 | 同（Cloud 还有 `ReconcileIntent`） | ✅ 语义相同 |
| **协调** | **无** | Coordinator + lease + fencing | ❌ 完全不同 |
| **世界** | 内存投影（`New`） | 持久化（`NewPersistent`）+ 订阅者 + delta | ❌ 不同 |
| **跨机通知** | **无** | Outbox → Redis Stream → Edge | ❌ 无 |
| **安全档位** | 必须显式配置 | 从 `RobotProfile` 推导 | ❌ 不同 |

**读法：上七行相同，下四行完全不同。**

**一条可执行的经验**：如果你在设计一个机器人 Agent 系统，**先做 Local 形态**。因为这七行是"物理世界强加的"，下四行是"多机强加的"。先做对上面七行，你才有一个**可以被验证**的系统；上面七行不对，加上分布式只会让错误更难发现。

---

## 9.9 一处必须标注的缺口：仿真的审批门禁

这一节是关于"仿真通过 ≠ 门禁通过"的一个具体实例，必须讲清楚。

`docs/production/sim-to-real.md:16` 说 Python Runtime 校验 approval。**但对仿真不成立**：

| 实现 | `approval_id` 处理 |
| --- | --- |
| `sim/mujoco/tangying_sim/server.py` | **零命中**（不检查） |
| `robot/gateway/tangying_robot_gateway/safety.py:117-118` | `if physical and not command.approval_id → APPROVAL_REQUIRED`（**真机**） |

同一个文件对 `task_revision` / `aggregate_version` / `step_id` 也**零命中**（这一点文档已经承认）。

**后果**：

> **仿真闭环通过 ≠ 审批门禁通过。**

也就是说：在仿真里跑完一个任务，**不能证明**审批链路工作正常——因为仿真的 Runtime 根本不检查审批字段。

**这是本书反复强调的"四种证据强度"的一个具体案例**：

| 证据 | 证明什么 | **不能证明什么** |
| --- | --- | --- |
| 仿真闭环走完 | 契约、身份、停止、恢复、证据逻辑闭合 | **审批门禁、真机安全校验** |
| 真机受监护验收 | 这一次、这台机器 | 下一台 |

而项目文档本身也承认这类缺口（`sim-to-real.md:16` 在讲"protobuf 已带 `task_revision`/`aggregate_version`/`step_id`，但 Python `Command` 当前未映射并独立检查这三个字段"）。

**教学要点**：**"协议里有这个字段"和"Runtime 强制这个字段"是两件事。** 审计一个安全机制时，必须问："**如果我把这个字段的值改成非法的，谁会拒绝我？**"——如果答案是"没有人"，那这个字段目前只是数据。

---

## 9.10 教学要点

### 一个课堂实验：先做 Local，再加 Cloud

这个对照实验可以设计成一个两周的课程项目：

| 阶段 | 任务 | 验收 |
| --- | --- | --- |
| **第 1 周** | 实现一个单进程机器人 agent：工具 + 完成判据 + 失败分类 + 账本 | 能回答"工具报成功但实际没做到时，系统会怎么反应" |
| **第 2 周** | 加第二台机器人，实现资源独占 | 两机同时想拿同一个物体 → 只有一个成功，另一个收到明确的拒绝 |

**关键设计**：第 2 周的评分标准**不是"能不能跑"，而是"两台机器人抢同一个物体时，系统会不会双写"**。

这迫使学生先想清楚自己第 1 周的完成判据对不对——因为双写的第一症状就是**两台都以为成功了**。

### 六道练习题

**题 1（砍什么、不砍什么）**

> Local Agent 的包注释说它"deliberately has no distributed claim or task-lease protocol"。请列出它因此**可以**砍掉的三样东西，和它**不能**砍掉的三样东西，并说明区分标准。

<details>
<summary>答案要点</summary>

**可以砍（因为它们只为"多写者"故障服务）**

1. claim / 租约协议——一个 worker 不存在并发认领
2. Outbox / 跨机通知——没有第二台机器要通知
3. 资源 fencing token——没有第二个持有者

**不能砍（因为它们是物理世界强加的）**

1. **动作后证据门禁**——单机同样会出现"动作发出去了但不知道成没成"
2. **步骤幂等键**——进程可能重启，重启后不能重复物理动作
3. **`UNKNOWN_OUTCOME` 的人工对账**——单机同样会出现"结果未知"

**区分标准**：问"这个机制防的故障，在只有一个写者时还存在吗？"

- 并发认领 → 只有一个 worker 时不存在 → 可砍
- "命令发出去了但结果未知" → **任何进程数下都存在** → 不可砍

</details>

**题 2（重启行为）**

> 一个"简单"的 Local Agent 实现会在进程重启后，把执行到一半的任务**从头重跑**。
> 请说明这个实现错在哪里，并指出项目里正确的行为是什么、在哪一行。

<details>
<summary>答案要点</summary>

**错在哪里**：重启时任务可能已经产生了物理效果（杯子已经拿起来了）。从头重跑 = **重复物理动作** → 可能撞坏东西。

**正确行为**：`internal/localapp/app.go:678-691`——**重启把执行中任务一律降为 `RECOVERABLE_FAILURE`，不自动重放**。

**深层原因**：重启后系统**不知道**世界变成了什么样。它的持久记录里有一条"`pick` 步骤 STARTED"，这正好是 `Uncertain` 的判据 → 阻塞 readiness，直到人对账。

**加分点**：指出这与 Cloud Fleet 的 `reclaimStaleLocked` 是**同一个语义**（`coordinator.go:338`：过期 → `UNKNOWN_OUTCOME`，不退回 READY）。两种形态一条规则。

**再加分**：指出"删掉 `agent.db` 然后重跑"也不是解决方案——那是**删除证据**而不是获取证据。

</details>

**题 3（依赖方向）**

> `middleware/sqlite` 依赖 `fleet/eventlog`，并在打开本地数据库时无条件建 4 张 fleet 表。
> 请说明这带来什么后果，为什么测试没有拦住它，以及你会怎么改。

<details>
<summary>答案要点</summary>

**后果**：

1. Local Agent 的二进制里带着一整套分布式协调原语的符号（`strings` 命中 227 次）
2. 4 张表**建了但不用**——它们没有消费者
3. 文档说"0 处引用"，构建产物说"227 处"——**两者可以同时为真**

**为什么测试没拦住**：`tests/architecture/dependencies_test.go:32-40` 的被测包集合**不含 `./cmd/...` 与 `./internal/localapp/...`**；禁列表（`:113-138`）**不含 `fleet` / `cloudclient`**。

**怎么改（两种思路）**

1. **提取接口**：把 `eventlog.Store` 接口提到一个 `middleware` 与 `fleet` 都依赖的位置（比如 `core/`），让 `middleware/sqlite` 依赖接口而非 `fleet` 实现。
2. **按需建表**：让 `middleware/sqlite.Open` 接受一个"我要哪些表"的参数，Local 形态不建 fleet 表。

**加分点**：指出根本问题是**用目录名表达部署边界**。目录名是编译期概念，部署边界是运行时概念。要真正保证"Local 不连云端"，应该用**架构测试 + 运行时断言**双重保障：既检查依赖方向，也在构造时断言"没有云客户端被注入"。

</details>

**题 4（安全默认值）**

> `RecoveryAction.Executable()` 用白名单（`Risk == read_only || Risk == bounded_write`）而不是黑名单（`Risk != never_automatic`）。
> 请说明这两种写法在"新增一个忘了填风险级的条目"时的行为差异，以及为什么白名单是对的。

<details>
<summary>答案要点</summary>

| 写法 | 忘了填风险级时 | 结果 |
| --- | --- | --- |
| 黑名单 `Risk != never_automatic` | 零值 `""` ≠ `never_automatic` | **默认可执行** ❌ |
| **白名单** `Risk == read_only \|\| Risk == bounded_write` | 零值 `""` 不等于任何一个 | **默认不可执行** ✅ |

**为什么白名单是对的**：安全默认值必须是**收紧**，不是放松。一个"忘了声明"的新条目，在不确定的情况下**应该什么都不做**。

**同类例子**（项目里还有两处）：

1. `RecallGoalMaxAge()`（`recall_goal.go:33-46`）：取值非法（解析失败、`<=0`、超 7 天）一律回退 15 分钟默认值——**"未设置"和"拼错了"不能都意味着"信任任何东西"**。
2. `Classify` 兜底为 `UnknownOutcome`（禁止自动重试）而不是 `Transient`（可重试）。

**加分点**：指出这是"**失败关闭（fail closed）**"的具体实现。它的对立面是"失败开放（fail open）"——一个权限检查抛异常时放行。

</details>

**题 5（仿真 ≠ 门禁）**

> `docs/production/sim-to-real.md:16` 说 Python Runtime 校验 approval，但 `sim/mujoco/tangying_sim/server.py` 对 `approval_id` **零命中**。
> 请说明这个差距的实际后果，并给出一条能在 CI 里自动化检查它的方法。

<details>
<summary>答案要点</summary>

**后果**：**仿真闭环通过 ≠ 审批门禁通过。** 在仿真里跑完任务不能证明审批链路工作正常——因为仿真 Runtime 根本不检查这个字段。

**更一般的教训**：同一份 proto 被三个实现（MuJoCo / Gazebo / 真机网关）使用，但**每个实现强制的字段集合不同**。协议字段的存在不意味着它被强制。

**自动化检查方法（几种）**

1. **契约测试矩阵**：对每个 Runtime 实现，逐个字段注入非法值，断言被拒绝。生成一张"字段 × 实现"的矩阵表。
2. **字段清单 diff**：从每个实现的 `_validate()` / `safety.py` 里提取检查的字段名集合，与 proto 字段集合做差，**差集必须在文档里显式列出**（"本实现不检查：A, B, C"）。
3. **Golden 拒绝测试**：为每个"应该被拒绝"的组合写一个测试，涵盖所有实现。

**加分点**：指出关键在于**"协议里有这个字段"和"Runtime 强制这个字段"是两件事**。审计安全机制时必须问："如果我把这个字段改成非法值，谁会拒绝我？"——答案是"没有人"的字段，目前只是数据。

</details>

**题 6（批准粒度）**

> 系统要求"模型不得执行批准时没出现过的物理动作"。
> 请说明为什么"批准一个意图"（"我同意你处理这个情况"）在这个系统里**不够**，并给出一个具体的失败场景。

<details>
<summary>答案要点</summary>

**为什么不够**：意图的范围是**模糊的**。操作员批准"重新观测并修复抓取"时，心里的范围可能是"再抓一次"，但模型可能理解为"抓取失败 → 换一个物体 → 放到别处"。

**具体失败场景**：

1. 操作员批准恢复计划："重新观测 + 重做 `pick`"
2. 模型在执行中发现杯子脏了，决定"顺便把杯子放到水槽冲洗"
3. 如果批准的是**意图**，这个"顺手"在范围内
4. 但它是一个**操作员从未见过的物理动作**

**物理世界的特殊性**：在软件系统里，"多做一步"通常可以撤销。在物理世界里，**一个未被批准的动作可能已经移动了一个物体、撞到了人。**

**所以批准必须是动作集合，而且这个集合在批准的那一刻就冻结。**

**代价**：恢复计划必须在批准前**完整生成**（不能"边执行边规划"）。这是这个系统里模型不参与恢复计划生成的原因之一（`recoveryagent.go:527-536`：**"模型在这个分支里完全不被咨询"**）。

</details>

### 学生最容易误解的点

| # | 误解 | 纠正 |
| --- | --- | --- |
| 1 | "本地单机 = 简化版，功能少" | 本地单机砍掉的是**协调**，不是**闭环**。它的完成判据、失败分类、证据、对账与云端**完全相同** |
| 2 | "单机不需要证据门禁" | **最需要**。没有证据门禁的单机 agent 就是一个遥控器 |
| 3 | "重启后从头重跑最省事" | 那是**重复物理动作**。正确行为是降为 `RECOVERABLE_FAILURE` 并等人 |
| 4 | "`fleet/` 目录里的东西都是云端专属" | `fleet/worldhub` 与 `fleet/eventlog` 是被两条画像**共用的库**。目录名不等于部署归属 |
| 5 | "仿真里跑通了，审批也验证过了" | 仿真 Runtime 对 `approval_id` **零命中**。仿真通过不证明审批门禁 |
| 6 | "`0o700` 只是随手写的" | 机器人 agent 的数据库包含任务历史、证据索引、操作员备注——**它不是公开数据** |

---

## 9.11 本章小结

1. **Local Agent 是一次可执行的对照实验。** 它砍掉了 claim 协议、租约、outbox、fencing、持久化世界——**但闭环契约、失败分类、证据、对账一个都没砍**。代码结构本身就证明了"去掉分布式，这三样依然在"。

2. **判据是"这个机制防的故障，在只有一个写者时还存在吗？"** 并发认领不存在 → 可砍；"命令发出去了但结果未知" → 任何进程数下都存在 → 不可砍。

3. **"0 处引用"是一个三段式事实。** 运行时零依赖、编译期有依赖、有代码复用。三者可以同时为真，因为**没有任何人或工具在检查这件事**——一份文件说 0 处，构建产物说 227 处，测试说没问题。

4. **两种形态，一条规则**：重启后不自动重放。Cloud 用 `reclaimStaleLocked`，Local 用 `RECOVERABLE_FAILURE`，语义相同。

5. **先做 Local，再加 Cloud。** 因为"完成判据 / 失败分类 / 证据 / 对账"这七行是物理世界强加的，而"协调 / 世界持久化 / 跨机通知 / 安全档位推导"这四行是多机强加的。**先做对上面七行，你才有一个可以被验证的系统。**

---

## 9.12 源码索引

| 内容 | 位置 |
| --- | --- |
| **包注释：刻意无 claim 协议** | `internal/localapp/app.go:1-3` |
| 重启不自动重放 | `internal/localapp/app.go:678-691` |
| 恢复视图与 `CanResume` | `internal/localapp/recovery.go:24-83` |
| "唯一写入者"注释 | `internal/localapp/recovery.go:13-22` |
| **禁止切版绕过** | `internal/localapp/recovery.go:85-101` |
| 只自动跑只读恢复 | `internal/autorecovery/supervisor.go:183-190` |
| 三个风险级与白名单 | `agentruntime/recoverycatalog.go:31-40,335-358` |
| 世界投影（非持久化） | `cmd/local-agent/main.go:280` |
| worker 配置（三个字段留空） | `cmd/local-agent/main.go:281-284` |
| 安全档位显式配置 | `cmd/local-agent/main.go:90`；`safety_profile_test.go:9-18` |
| SQLite 建表五组 | `middleware/sqlite/store.go:21-82` |
| 部分唯一索引 | `middleware/sqlite/store.go:58-68` |
| fleet 表建了不用 | `middleware/sqlite/fleet.go:11,14-59` |
| 对账 SQL 一次性约束 | `middleware/sqlite/store.go:220-244` |
| 架构测试的被测包集合 | `tests/architecture/dependencies_test.go:32-40,113-138` |
| 仿真不检查 approval | `sim/mujoco/tangying_sim/server.py`（`approval_id` 零命中） |
| 真机检查 approval | `robot/gateway/tangying_robot_gateway/safety.py:117-118` |
| "0 处"的文档表述 | `docs/architecture/why-distributed.md:160` |
| 真正有价值的三样 | `docs/architecture/why-distributed.md:99-112` |

---

**上一章**：[第 8 章 分布式不是部署姿势，是故障假设](../chapters/ch08-distributed-fault-assumptions.md) · **下一章**：[第 10 章 仿真、真机与 sim2real](../chapters/ch10-sim2real.md) —— 为什么一个项目要同时有三个仿真后端？覆盖率 38.9% → 81% 是怎么做到的？
