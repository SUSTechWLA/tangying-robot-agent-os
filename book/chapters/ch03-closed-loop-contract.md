# 第 3 章 闭环契约：为什么「工具返回成功」不算完成

> **版本口径**：本章包含 v0.6.0/v0.7.0 演进案例。代码片段、计数与实验按原时点解释；出版复核修正论证，不表示历史缺口均为当前状态。当前云边能力见第17章，来源与证据边界见出版说明。

> **本章的核心命题**
>
> 物理动作的完成，不能由发起动作的那一方宣布。
> 它必须由一次**独立的、晚于命令下发的观测**来确认。
>
> 这一条假设，往下推出了整个系统最不直观的那些设计。

---

## 3.0 一个真实的、危险的失败

在讲机制之前，先看一个真实发生过的事故。它比任何抽象论证都有说服力。

用户给的指令是：

```
从客厅出发，去厨房拿红色杯子放进蓝色收纳盒，
再拿蓝色杯子放进蓝色收纳盒，然后回到客厅
```

系统的执行报告是：**成功**。

实际发生的事是：**只搬了红杯子。**

证据链是这样的（`docs/development/home-scene-expansion-plan.md`）：

> 确认步骤与单物体流程**完全一致**（`observe → navigate → resolve → plan_grasp → pick → verify_grasp → place → verify_place → navigate_02 → verify_arrival_02`），产出中只有 `object_id: red-cup`，没有任何第二次 pick/place。

项目文档对这件事的定评值得原文引用：

> **这是最危险的一种**：用户要求搬两个杯子，系统搬了一个还报成功。
> 它比直接失败更糟，因为**没有人会去检查**。

根因不在机器人，在自然语言解析器：物体的正则表达式要求每个子句都带动词（以「拿/取/抓/拾」开头），而第二个子句以「**把**」开头——整整一个子句没有被解析，也没有报错。

这个案例说明了两件事：

1. **"报成功"是一个可以被伪造的信号**，甚至不需要有人撒谎——一个漏掉的子句就够了。
2. **防止它需要的是"独立的第二次检查"，不是"更仔细的第一遍"**。

整章讲的，就是系统怎么把这个"第二次检查"变成不可绕过的机制。

---

## 3.1 一条注释，一个包

整个设计的源头是一句注释，写在 `core/closedloop/closedloop.go` 的包文档里：

```go
// A tool result is a trigger to look at the world, never proof that the world
// changed as intended. This package therefore owns two decisions that must not
// be re-implemented per adapter:
//
//   - whether a declared physical write has produced enough fresh evidence to be
//     called complete (Gate), and
//   - what a failure means and therefore what is safe to do about it
//     (Classify + Class).
```

翻译过来是三句话：

- **工具结果是"去看世界"的触发器，从来不是"世界按预期改变了"的证明。**
- 因此有两件事必须集中定义，**不许每个适配器自己实现**：
  - 一次声明的物理写入，有没有足够新鲜的证据可以叫「完成」（`Gate`）；
  - 一次失败是什么意思，因此**唯一安全的下一步**是什么（`Classify` + `Class`）。

包文档还给了一句设计约束，解释了为什么这个包必须**没有 I/O**：

```go
// Everything here is deterministic and free of I/O so the rules can be tested
// exhaustively, including the rule that an unknown physical outcome is never
// retried automatically.
```

**这是一个可直接迁移的工程原则**：把安全规则做成纯函数，你才能穷尽地测试它。一旦它需要读数据库、调网络，你就只能"抽样测试"安全规则——而安全规则的失效恰恰发生在你没想到的那个分支上。

---

## 3.2 Gate：四道判决，顺序不可交换

`Gate` 是整个系统的**唯一入口**。它的签名极简：

```go
// core/closedloop/gate.go
func Gate(declaration Declaration, dispatchedAt time.Time, evidence *Evidence) Decision
```

三个输入，一个结论。先看输入：

```go
// core/closedloop/gate.go
type Declaration struct {
	Manifest               bool  // Agent 本地可信目录说这个工具改世界
	RuntimeMutatesWorld    bool  // 已连接的 runtime 自述这个工具改世界
	RuntimeCapabilityKnown bool
}

func (d Declaration) Mutates() bool {
	return d.Manifest || d.RuntimeMutatesWorld
}
```

**两个「改世界」的声明源，取并集。** 注释解释了为什么必须是两个：

> Manifest 是 Agent 的本地可信目录。它覆盖参考工具，**且不能被远端适配器修改**。
> RuntimeMutatesWorld 是已连接 runtime 自述的。它覆盖本地目录从未听说过的适配器专有工具。
> **任一来源说是，这一步就改世界。** 只在远端声明一次写入，仍然会拿到门禁；只在本地声明，仍然会拿到门禁。

这是对两种失效的双向防御：**本地目录不认识新适配器的工具**，**远端适配器可以谎报自己不改世界**。取并集让两种说谎都无效。

### 3.2.1 判定顺序

```go
// core/closedloop/gate.go（逐行还原）
func Gate(declaration Declaration, dispatchedAt time.Time, evidence *Evidence) Decision {
	// ① 只读工具：不需要证据
	if !declaration.Mutates() {
		return Decision{Satisfied: true, Required: false,
			Reason:  ReasonNotRequired,   // "CLOSURE_NOT_REQUIRED"
			Message: "read-only tool needs no post-condition evidence"}
	}

	// ② 不知道命令何时下发：不能证明新鲜度
	if dispatchedAt.IsZero() {
		return Decision{Satisfied: false, Required: true,
			Reason:  ReasonNoCommandTime, // "CLOSURE_DISPATCH_TIME_REQUIRED"
			Message: "cannot prove freshness without the dispatch time of this command"}
	}

	// ③ 没有证据
	if evidence == nil {
		return Decision{Satisfied: false, Required: true,
			Reason:  ReasonMissing,       // "CLOSURE_EVIDENCE_REQUIRED"
			Message: "tool reported success but no post-command observation was attached; " +
				"the physical outcome is unverified"}
	}

	// ④ 有证据，但它是这次命令的证据吗？
	if err := validateFreshness(dispatchedAt, *evidence); err != nil {
		return Decision{Satisfied: false, Required: true,
			Reason: ReasonStale, Message: err.Error()}  // "CLOSURE_EVIDENCE_STALE"
	}

	return Decision{Satisfied: true, Required: true,
		Reason:  ReasonRequired,          // "CLOSURE_EVIDENCE_SATISFIED"
		Message: fmt.Sprintf("confirmed by observation %s taken after dispatch", evidence.ObservationID)}
}
```

**这个顺序是不可交换的，而且这一点极其重要。**

注意第 ② 步在第 ③ 步之前。这意味着：**一个连下发时刻都不知道的命令，不会因为恰好带了一份看起来很新的观测而被判定为完成。**

把顺序换一下——先看证据有没有，再看下发时刻——系统在"时钟没同步"或"命令时间戳丢失"的情况下就会放行。这是一个非常容易被写错、而且写错了不会立刻暴露的顺序依赖。

### 3.2.2 四条证据校验

```go
// core/closedloop/gate.go（逐行还原）
func validateFreshness(dispatchedAt time.Time, evidence Evidence) error {
	// (1) 缺观测 ID
	if evidence.ObservationID == "" {
		return fmt.Errorf("%w: no observation id", ErrEvidenceRequired)
	}
	// (2) 缺采集时间
	if evidence.ObservedAt.IsZero() {
		return fmt.Errorf("%w: no observation time", ErrEvidenceRequired)
	}

	// (3) 观测早于命令下发
	dispatchedMS := NormalizeDispatchTime(dispatchedAt)              // UTC + Truncate(1ms)
	observedMS   := evidence.ObservedAt.UTC().Truncate(DispatchPrecision)
	if observedMS.Before(dispatchedMS) {
		return fmt.Errorf("%w: observed %s, dispatched %s", ErrEvidenceStale,
			observedMS.Format(time.RFC3339Nano), dispatchedMS.Format(time.RFC3339Nano))
	}

	// (4) 来源裁决说这份观测已经过期或不可用
	switch {
	case equalFold(evidence.Freshness, "STALE"):
		return fmt.Errorf("%w: source verdict STALE", ErrEvidenceStale)
	case equalFold(evidence.Freshness, "UNKNOWN"):
		return fmt.Errorf("%w: source verdict UNKNOWN", ErrEvidenceStale)
	}
	return nil
}
```

四个细节，每个都是教学点。

#### 细节 1：缺 ID 和缺时间都报 `ErrEvidenceRequired`，不是 `ErrEvidenceStale`

这两个错误值的语义区别是整个契约的骨架：

```go
// core/closedloop/closedloop.go
// Errors a refused completion returns. They are the two answers Gate can give:
// the evidence is not there, or the evidence is there and does not describe this
// command.
var (
	ErrEvidenceRequired = errors.New("physical write requires fresh post-command evidence")
	ErrEvidenceStale    = errors.New("physical write evidence predates the dispatched command")
)
```

- **`ErrEvidenceRequired`** = 「你没有证据」；
- **`ErrEvidenceStale`** = 「你的证据不是这次命令的」。

这个区分在诊断时决定完全不同的下一步：前者是"再去看一眼"，后者是"你手里的东西根本用不上"。

#### 细节 2：毫秒截断是兼容策略，不能证明因果顺序

当前 `validateFreshness` 将下发时间和观测时间截断到整毫秒，只拒绝早一桶的观测。同桶即使实际先采集，也可能通过这项检查。

| 下发时间 | 上报观测时间 | 当前时间检查 | 能否仅据此证明动作后采集 |
| --- | --- | --- | --- |
| `…000400` | `…000000` | 同桶，接受 | 不能，可能早于下发 |
| `…000400` | `…000500` | 同桶，接受 | 若为真实精确采集时间可排序，但上报精度与时钟仍须验证 |
| `…000400` | 前一毫秒 | 拒绝 | 只能说明该时间检查未通过 |

`DispatchPrecision` 注释把它解释为上报分辨率限制。出版复核必须区分**实现事实**与**安全论证**：缺少精确信息意味着顺序未知，不能据此把同桶提升为已证实的因果关系。这不是物理定律，也不是所有传感器的固有限制。

此外，Gate 不自行校正跨设备时钟偏差。不同主机的 UTC 时间仅在校时误差有界、采集时刻语义可信时才可比较；消息到达时间不能替代采集时间。更强的协议应绑定命令 ID、设备重启纪元、采集序列和动作前证据基线，要求动作后的样本并检查超龄。尚未满足这些条件的设备应阻塞相关放行。具体设计见附录 D；此处如实记录现有策略，不宣称代码已经完成这些加强。

#### 细节 3：`Freshness` 是被计算出来的，不是被写死的

`Evidence.Freshness` 的字段注释点明了它的归属：

```go
// core/closedloop/closedloop.go
// Freshness is the projection's verdict for the declaring source.
Freshness string
```

**它不是证据自己带的属性，是投影（projection）对声明来源给出的裁决。**

这个区别不是文字游戏。历史实现里，两个调用点都写字符串字面量 `"FRESH"`。后果写在 `core/telemetry/freshness.go` 的注释里：

> gate 的 staleness 规则**根本不可能因过期而触发**。

也就是说：门禁有"来源过期就拒绝"的规则，但因为所有证据都自称 FRESH，这条规则是**死代码**。这比没有这条规则更糟——它给人一种"我们检查了新鲜度"的错觉。

修复后，两个生产者都调用 `snapshot.EvidenceFreshness(now)` 计算，并且在急停或锁存时**强制降级为 `UNKNOWN`**：

```go
// edge/agent/runner.go（语义还原）
if snapshot.EmergencyStopped {
	// An emergency stop during the action means the tool physically
	// confirmed nothing; treat the observation as unusable for closure.
	evidence.Freshness = "UNKNOWN"
}
```

**急停期间拍的画面不能用来判定完成**——因为急停意味着"动作没有物理地确认任何东西"。这条规则优雅得让人想鼓掌。

新鲜度的三档计算逻辑在 `core/telemetry/freshness.go`：

```
EvidenceFreshness(now):
    if observedAt.IsZero():                          return Unknown
    if now + ClockSkewAllowance < observedAt:        return Unknown   # 时间戳过度超前（5s 容忍）
    if now - observedAt > sourceBudget(sourceID):    return Stale
    return Fresh
```

注意这个设计：**时间戳超前不是"新鲜"，是"未知"。** 一个来自时钟错乱的机器人的未来时间戳，不会因为"看起来很新"而被采信。

#### 细节 4：`UNKNOWN` 也让门禁拒绝

看第 (4) 步的 switch：`STALE` 和 `UNKNOWN` **都被拒绝**，而且都返回 `ErrEvidenceStale`。

这是刻意的：`UNKNOWN` 意味着"我不知道这份观测有没有用"。在闭环契约里，**"不知道"必须按"不能用"处理**，不能按"大概能用"处理。

---

## 3.3 失败分类：一张 177 个码的表，和它唯一的安全下一步

`Gate` 回答"这次成功算不算数"。`Classify` 回答另一个问题：**"这次失败，唯一安全的下一步是什么？"**

### 3.3.1 八个类

```go
// core/closedloop/closedloop.go
const (
	// Transient may be retried in place once the infrastructure recovers.
	Transient Class = "TRANSIENT"
	// Perception requires a new observation or search before acting again.
	Perception Class = "PERCEPTION"
	// Planning requires a new goal or plan; repeating the same command is pointless.
	Planning Class = "PLANNING"
	// Permission means approval, profile, catalog or fencing state is wrong.
	Permission Class = "PERMISSION"
	// Resource means another owner holds the resource or a grant is missing.
	Resource Class = "RESOURCE"
	// Validation means the command itself was rejected as malformed.
	// A retry with identical arguments cannot succeed.
	Validation Class = "VALIDATION"
	// UnknownOutcome means the physical result of a dispatched action cannot be
	// determined. Automatic retry is forbidden.
	UnknownOutcome Class = "UNKNOWN_OUTCOME"
	// Fatal means the failure cannot be resolved by retrying the same work.
	Fatal Class = "FATAL"
)
```

每一句注释都是**以"唯一安全的下一步"来定义的**，不是以"错误的原因"来定义的。这是这张表最重要的设计特征。

### 3.3.2 完整分类表

表在 `closedloop.go:83-274`，共 **177 个唯一码**。**表的顺序就是优先级**——线性扫描，第一个命中获胜：

| # | 类 | 定义 | 唯一安全的下一步 | 码数 |
| --- | --- | --- | --- | --- |
| 1 | `UNKNOWN_OUTCOME` | 已下发动作的物理结果**无法判定** | **先对账**（只读取证），对账前不得改动机器人 | 15 |
| 2 | `PERMISSION` | 审批、profile、目录或 fencing 状态不对 | **人或更高层改变某个东西**（批准 / 标定 / 安装） | 29 |
| 3 | `RESOURCE` | 另一个持有者占着资源，或缺少 grant | **等待 / 获取资源所有权** | 9 |
| 4 | `PERCEPTION` | 再次行动前需要新的观测或搜索 | **重新观测 / 搜索，然后重试** | 47 |
| 5 | `PLANNING` | 需要新目标或新计划 | **换目标或换路径**（重放必然复现失败） | 17 |
| 6 | `VALIDATION` | 命令本身被判为畸形 | **改请求**（不是重试） | 27 |
| 7 | `TRANSIENT` | 基础设施恢复后可就地重试 | **依赖恢复后重发同一条命令** | 32 |
| 8 | `FATAL` | 无法通过重试同一份工作解决 | **人工判断** | 3 |

> **⚠️ 一处文档与代码冲突，必须向读者点明**
>
> 项目文档里有三处把这个分类写成「**七类**」：`docs/architecture/supervision-verification.md` 的标题、`docs/development/2026-09-17-supervision-blind-spots.md`、以及 `docs/architecture/lifecycle-objects.md`（这一处紧接着的表格里却列了 8 行）。
>
> **代码是 8 类**（`closedloop.go:48-68` 有 8 个 `Class` 常量）。ADR-10 也用「原八类映射」的措辞。
>
> 本书以代码为准。「七类」是早期文档未更新的残留。

### 3.3.3 `Classify` 的兜底方向：未识别 → 不可重试

```go
// core/closedloop/closedloop.go
// Classify maps a runtime failure code to its only safe recovery action.
//
// An empty or unrecognised code is UnknownOutcome: the system knows something
// went wrong but not what reached the hardware, so no automatic retry is
// allowed.
func Classify(code string) Class {
	normalized := strings.ToUpper(strings.TrimSpace(code))
	if normalized == "" {
		return UnknownOutcome
	}
	for _, rule := range classification {
		if _, ok := rule.codes[normalized]; ok {
			return rule.class
		}
	}
	return UnknownOutcome
}
```

**这是全章最重要的一个取舍：兜底方向是"最保守"，不是"最乐观"。**

一个没人见过的错误码，被当作 `UNKNOWN_OUTCOME`——**禁止自动重试**。而不是被当作"大概是临时故障吧，重试一次"。

为什么？因为兜底猜错的代价不对称：

| 兜底方向 | 猜对 | 猜错 |
| --- | --- | --- |
| 猜"可重试"（乐观） | 省一次人工干预 | **重复一次可能已经生效的物理动作**——杯子已经拿起来了，再拿一次就是撞 |
| 猜"不可重试"（保守） | 安全 | 多一次人工确认（成本：一次点击） |

**隔离环境中重跑纯测试通常风险较低，但仍有资源成本；部署、迁移和网络写操作同样可能结果未知**。这就是本书第 16 章那张对照表里最关键的一行。

### 3.3.4 `Knows`：为什么需要第二个函数

这是本章最漂亮的一个设计。

```go
// core/closedloop/closedloop.go
// Knows reports whether a code appears in the classification table.
//
// It exists because Classify cannot answer this. A code that IS listed as
// UnknownOutcome and a code that is not listed at all classify to the same value,
// and the difference matters: the first is a failure the system understands and
// has decided is unrecoverable, the second is one nobody has classified yet. A
// supervisor reporting the second as the first hides a missing table entry behind
// a safety-looking decision — which is exactly what happened with
// GRASP_NOT_REACHED before it was added.
func Knows(code string) bool { /* ... */ }
```

问题是这样：`Classify("EXECUTION_OUTCOME_UNKNOWN")` 和 `Classify("某个没人见过的码")` **返回同一个值**。但它们是两个完全不同的事实：

- 前者：**系统理解这个失败，并且决定了它不可恢复。**
- 后者：**没有人分类过这个失败。**

把后者报成前者，「就是把一个缺失的表项藏在看起来像安全决策的结果后面」。

这不是假想的风险。`GRASP_NOT_REACHED` 就是真实案例：它是 runtime 自己的码，意思是"末端执行器在容差内够不到物体"——一个**已知、可解释**的失败。但它不在表里，于是兜底成 `UnknownOutcome`。

源码注释记录了后果：

> 让分类器把一个已知失败报成不可恢复，并**禁止了那个实际会成功的重试**。

### 3.3.5 两道守卫：让分类表不会悄悄烂掉

因为兜底方向是保守的，**一个漏掉的码会静默地把一个已知失败报成"结果未知"**。这个失效是无声的——系统不会崩，只会让操作员去做无意义的事。

所以有两道守卫：

**守卫 1：`Knows()`** —— 让"表里没有"可以被单独检测。

**守卫 2：覆盖率测试 `TestEveryCodeTheRuntimeCanEmitIsClassified`**

清单 `runtimeEmittedCodes` 是**提交进仓库的常量**，不是运行时爬取的（`core/closedloop/classification_coverage_test.go`）。新增码而没加进清单 → 静默落入未分类 → 守卫失败。

审计数字（`classification_coverage_test.go:122-125`）值得完整引用：

> **83 个码里有 58 个缺失**，其中包括 `GOAL_NOT_CLEAR`。

真实症状是这样的：

```
navigation.navigate 失败：GOAL_NOT_CLEAR（UNKNOWN_OUTCOME）
建议：不要自动重试：先对账确认这次动作的实际结果
```

**而底盘根本没动过。**

`GOAL_NOT_CLEAR` 是路径规划器的**预检拒绝**——机器人在收到命令的那一刻就知道"这个目标不可达"，它没有移动一毫米。把它报成"结果未知，先对账"，是让操作员去核对一个**从未发生的动作**。

项目文档的定评：「让操作员去对账一个从未发生的动作，**比不报还糟**。」

修复后同一个码输出：

```
navigation.navigate 失败：GOAL_NOT_CLEAR（PERCEPTION）
建议：重新观测或搜索目标后再尝试
```

**而且这个审计本身也有盲区**（`docs/development/2026-09-17-supervision-blind-spots.md`）：扩宽证据来源到 5 类后，得到 **113 个码**，又补进 16 个——包括 `PLACEMENT_NOT_OBSERVED`。原因是它作为**字符串参数**传给 `self._verify_relation(...)`，而不是 `ToolResult(False, "...")` 或 `ServiceError("...")` 的字面量，正则匹配不到。

**这一段的教训比结论更重要**：审计一个"是否穷尽"的问题，第一遍几乎一定是漏的。所以守卫必须是**可持续失败的测试**，不能是一次性的搜索。

### 3.3.6 `UnknownOutcome` 的 15 个码，以及四条设计论证

源码注释本身就是设计文档。逐条看：

```go
{UnknownOutcome, set(
	"EVIDENCE_INSUFFICIENT",
	"EXECUTION_OUTCOME_UNKNOWN", "PHYSICAL_OUTCOME_UNKNOWN", "RUNTIME_JOURNAL_UNAVAILABLE",
	"BACKEND_STOP_FAILED", "SERVICE_SHUTDOWN",
	"PLACEMENT_NOT_OBSERVED", "GRASP_NOT_OBSERVED", "GRASP_NOT_DETECTED",
	"PLACEMENT_NOT_VERIFIED", "UNVERIFIED_WORLD_MUTATION",
	"TOOL_EXECUTION_ERROR",
	"NAV_STOW_CONTACT",
	"UNCLASSIFIED_EXECUTION_FAILURE",
	"WORKFLOW_FAULT",
)},
```

**论证一：`*_NOT_OBSERVED` 为什么不是 `Perception`**（`closedloop.go:91-100`）

> 动作跑了，但预期关系从未被观测到。这是**真正未知**的情形，不是感知失败：夹爪闭上了、放置动作完成了，而它实际达成了什么并未被确立。
> 把它当作感知失败会允许重试一个**第一次可能已经成功**的物理动作——对放置来说，**物体可能已经在目的地了**。

**论证二：`TOOL_EXECUTION_ERROR` 为什么不是 `Validation`**（`closedloop.go:103-112`）

> 工具抛了意外异常。参数一无所知，异常前是否已经触达硬件也一无所知……它曾经被归到 `Validation`，后者的建议是「参数被拒绝了，重放也没用」——这是对一个**没有任何人检查过**的命令做出的猜测。

**论证三：`NAV_STOW_CONTACT` 为什么从同一个码拆出来**（`closedloop.go:113-120`）

> 一个码覆盖「我们仿真了，它会撞」和「它撞了」，让分类器有两个物理含义要选。**拆分就是修复。**

于是现在有两个码：

| 码 | 含义 | 类 | 为什么 |
| --- | --- | --- | --- |
| `NAV_STOW_CONTACT_PREDICTED` | 动之前仿真整个扫掠，预测出碰撞 | `PLANNING` | 什么都没坏，命令也没畸形，只是路径被挡 → **换个计划** |
| `NAV_STOW_CONTACT` | 真实碰撞：手臂正在动，接触把它停在中途 | `UNKNOWN_OUTCOME` | 之后它的姿态和碰到的东西都没被确立；重试一个刚撞过的扫掠会**把手臂往障碍物里推得更深** |

**论证四：兜底为什么故意落在这里**（`closedloop.go:122-128`）

`UNCLASSIFIED_EXECUTION_FAILURE`（恢复分类器自己的兜底）与 `WORKFLOW_FAULT`（诊断层"工作流失败但原因未确定"）是刻意放进 `UnknownOutcome` 的，理由一句话：

> **分类不了的失败，就是未知结果的定义。**

### 3.3.7 三个真实码的完整推理链

**例 1：`GRASP_FAILED` vs `GRASP_NOT_OBSERVED`**

这是最容易搞混的一对。它们的区别**不是"结果好不好"，是"有没有检查过"**：

| 码 | 何时返回 | 已知什么 | 类 | 理由 |
| --- | --- | --- | --- | --- |
| `GRASP_FAILED` | 夹爪闭合、手臂抬起、**抓取检查执行**，检查发现没夹住 | **手是空的**（确定） | `PERCEPTION` | 物体可能被碰歪了 → 重新观测再试 |
| `GRASP_NOT_OBSERVED` | 动作跑了，**没有人检查** | 什么都不知道 | `UNKNOWN_OUTCOME` | 可能夹住了 |

同类对照：`PLACE_NOT_REACHED`（夹爪张开**之前**返回，什么都没释放，物体还握着 → 物理效果**可证伪**）→ `PERCEPTION`；`PLACEMENT_NOT_OBSERVED`（动作跑完了没检查）→ `UNKNOWN_OUTCOME`。

源码注释原话：

> 两者都与它们的 `*_NOT_OBSERVED` 兄弟形成对照，后者是未知结果，**因为动作跑了而且从未被检查**。

**例 2：`FENCING_TOKEN_STALE` → `RESOURCE`**

这个码意味着"另一个持有者占着资源"（第 8 章会详细讲）。它的唯一安全下一步是"等待 / 获取资源所有权"，**不是重试**——因为重试用的是同一个过期 token，结果必然一样。

**例 3：`APPROVAL_REQUIRED` → `PERMISSION`**

唯一安全下一步是"人去批准"。系统里有一条被反复强调的原则：**软件不替人做批准决定**。所以这个类里没有任何自动路径。

---

## 3.4 没有自动重试器：一个被删掉的状态机

这是学生最容易误解的一点，必须单独讲。

看到 `Transient.Retryable() == true`，大多数人会推断："所以系统会自动重试瞬时故障。"**不是。**

```go
// core/closedloop/closedloop.go
// Retryable reports whether a retry would be permissible for this class of
// failure, in principle.
//
// It is a statement about the failure, not about the system: nothing retries
// automatically, and a physical re-attempt is an operator decision offered as
// `task.retry-step`. What this answers is the narrower question a reader of the
// table needs — "is this the kind of failure re-observing and trying again fixes,
// or is it one where the same attempt cannot succeed?"
func (c Class) Retryable() bool {
	switch c {
	case Transient, Perception:
		return true
	default:
		return false
	}
}
```

**"这是一个关于失败的陈述，不是关于系统的陈述。"**

### 3.4.1 那个被删掉的状态机，和删除它的理由

`core/closedloop` 曾经有过一个重试状态机——尝试预算、退避、`NextAttemptAt`、升级阶梯。它被**删除**而不是接线。包注释记下了理由，这段值得完整引用，因为它是全书最有价值的一段工程论证：

```go
// core/closedloop/closedloop.go
// # What is deliberately not here
//
// There is no retry state machine. This package used to carry one — attempt
// budget, backoff, NextAttemptAt, an escalation ladder — and nothing ever drove
// it: `NewTrack` appeared only in this package's own tests. Meanwhile the failure
// classes it defined read as though bounded automatic retry were in effect, and
// the observer's advice for a transient failure said "retry per the existing
// policy", pointing at a policy that did not exist.
//
// It was removed rather than wired, for a reason worth recording: a retry budget
// for physical writes has to be durable to mean anything. An attempt counter held
// in memory resets when the process restarts, so an agent that crashed mid-retry
// would retry forever — the exact unbounded behaviour the ceiling was supposed to
// prevent. Making it real means persisting attempt counts, deciding who approves
// attempt two, and threading both through the evidence gate. That is a design,
// not a patch, and it must not be reintroduced from a test-only type.
//
// What actually happens is stated plainly instead: a physical failure ends the
// step, and re-attempting it is an operator decision, offered as
// `task.retry-step` in the recovery catalog where it requires approval.
```

拆出四层论证：

1. **它从来没被接线过。** `NewTrack` 只出现在这个包自己的测试里——**测试覆盖了一个生产中不存在的类型**。这是"有测试≠有功能"的最好例子。
2. **它的存在产生了误导。** 分类表读起来像"有界自动重试生效中"，观察者的建议写着"按既有策略重试"——**指向一个不存在的策略**。
3. **它无法被简单地"接上"。** 物理写入的重试预算必须有持久性：内存里的尝试计数器在进程重启时归零，于是重试中途崩溃的 agent 会**永远重试下去**——正是这个上限本该防止的无界行为。
4. **把它做真是一项设计，不是一个补丁。** 需要：持久化尝试次数、决定谁批准第二次尝试、把两者穿过证据门禁。

**结论被平实地陈述出来**：一次物理失败结束这个步骤，重新尝试是**操作员的决定**，在恢复目录里以 `task.retry-step` 提供，并且**需要批准**。

对应目录条目（`agentruntime/recoverycatalog.go`）：

```go
{
	ID: "task.retry-step", Summary: "在重新观测之后重做当前步骤（不是原样重放）",
	Risk:        RiskBoundedWrite,
	Service:     "task resume", Tools: []string{"task.resume"},
	Shapes:      []string{"TRANSIENT", "PERCEPTION"},     // ★ 不匹配 UNKNOWN_OUTCOME
},
```

注意 `Shapes` 只覆盖 `TRANSIENT` 与 `PERCEPTION`：**恢复目录本身也匹配不到未知结果**，所以 `task.retry-step` 永远不会出现在对账分支的计划里。

### 3.4.2 一处必须指出的文档残留

`docs/architecture/lifecycle-objects.md` 仍然画着一个 Tool 状态机：

```
PENDING → EXECUTING → AWAITING_EVIDENCE → VERIFIED
                    ↘ RETRYING
                    ↘ ESCALATED
```

并引用 `ErrUnknownRetry = errors.New("unknown physical outcome must not be retried automatically")`，说它在 `core/closedloop/closedloop.go`。

**实测结果**：全仓库搜索（排除 `artifacts/`）：

| 符号 | 命中 |
| --- | --- |
| `type Track` / `func NewTrack` | **0** |
| `ErrUnknownRetry` | **0** |
| `RETRYING` / `ESCALATED`（Go 标识符） | **0** |
| `AWAITING_EVIDENCE` | 4 处，但**全部是事件里的 `activityStatus` 字符串**，不是状态机状态 |

代码侧的权威说明在包注释里，而 ADR-10 有一段明确的"现状校正"：

> 上述 ADR-4 的 `Track` 重试状态机**后来已移除**；当前 `core/closedloop` 只负责完成门禁、失败分类与恢复建议。生产中物理失败仍须持久化对账及上层决策，**不能把本次实验里的重试策略描述成现有 Agent 的自动恢复能力**。

文档里那个状态机图是**旧版状态机的残留**。（`gate.go:81-84` 的注释里还留着 `Track.Fresh` 的悬空引用，`Track.Fresh` 已不存在。）

**本章以代码为准：没有自动重试器。**

---

## 3.5 「结果未知禁止重试」的完整链路

这是本章的压轴。我们跟着一次断网，走完整个链路。

### 3.5.1 场景

夹爪命令通过 gRPC 发给了机器人。机器人开始合拢。然后：

```
rpc error: code = Unavailable desc = connection closed
```

现在请问：**杯子被夹起来了吗？**

- 网络在命令到达前断 → 没夹
- 网络在命令到达后、回包前断 → 夹了
- 命令到达、机器人夹到一半断电 → 夹了一半

**这三种情况，从外面看一模一样。** 系统此刻唯一正确的回答是：**"我不知道。"**

### 3.5.2 执行器的顺序（`edge/agent/runner.go`）

```
RunControlled:
  407-412  command = CommandForTaskStep(task, step)
  411      dispatchedAt := r.now()                # ★ freshness floor，本次尝试的唯一基准
           command = runtime.CommandAtDispatch(ctx, command, runtimeSnapshot, dispatchedAt)
  413-421  record := middleware.StepRecord{TaskID, StepID, IdempotencyKey, Capability, SafetyLevel}
           r.store.MarkStepStarted(ctx, record)   # ★ 先落盘 STARTED，再调用
  424      skillResult, err := r.invoker.Invoke(ctx, command)
  426-432  if err != nil:  measure(OutcomeUnknown); return err    # ★ 连错误码都没有 → 保持 STARTED
  433-436  GVF 开关：Mutates() 且非 emergency_stop 且无 StateReportJSON → ErrPhysicalOutcomeUnknown
  437-459  有 StateReportJSON → closedloop.ParseGroundedReport(...) → 落 STATE_REPORT 事件
           groundedVerified = (report.Verdict == "VERIFIED")
           非 VERIFIED → ErrPhysicalOutcomeUnknown
  458-474  if !skillResult.Success:
               仅 preflightFailure(...) 为真时 MarkStepFailed      # ★ 唯一的 FAILED 出口
               否则保持 STARTED
  475-484  verify_* 且 VerificationConfidence < 0.7 → ErrVerificationFailed
  486-491  closureEvidence, savedCaptureID = r.closureEvidence(...)   # 先归档证据再判定
  498      decision := closedloop.Gate(declaration, dispatchedAt, closureEvidence)
  499-508  if decision.Require() != nil && !groundedVerified:
               measure(OutcomeUnverified)
               publishToolActivity(..., "FAILED", ...)
               return ErrUnverifiedWorldMutation           # ★★ 故意不推进状态
  509-512  r.store.MarkStepCompleted(persistContext, record)
  515-522  publishToolActivity(..., "CONFIRMED", evidence, ...)
```

三个动作的顺序是刻意的：

1. **先记录 `STARTED`，再调用。** 反过来的话，进程在调用中途崩溃就什么都没记下来——而那时机器人**可能已经动了**。
2. **`dispatchedAt` 在调用之前取。** 这是"新鲜度地板"（freshness floor），本次尝试唯一的时间基准。
3. **失败时只在白名单情况下写 `FAILED`。**

那个白名单小得惊人（`runner.go:525-531`）：

```go
func preflightFailure(skill runtime.CapabilityName, code string) bool {
	return code == "ROBOT_COMMISSIONING_ACTIVE" ||
		(skill == runtime.CapabilityNavigate && code == "NAV_MAP_NOT_READY")
}
```

**只有这两种失败能被记为 `FAILED`**——因为只有这两种能确定"命令在任何物理效果被授权之前就被拒绝了"。其他所有失败，状态都停在 `STARTED`。

### 3.5.3 `STARTED` 是故意不推进的

代码里的原话（`edge/agent/runner.go`）：

```go
// ErrUnverifiedWorldMutation means a tool changed, or may have changed, the
// physical world and no fresh post-command observation confirms the result.
// The step is deliberately left STARTED so recovery reconciles the world
// instead of repeating a physical action whose outcome is unknown.
```

**"The step is deliberately left STARTED"**——故意留着的。

为什么？因为如果这里写 `FAILED`，系统就会"合理地"重试一次——而重试意味着**第二次合拢夹爪**，可能是在已经有杯子的情况下。

**这就是"结果未知禁止自动重试"的全部重量，压在一个状态常量上。**

`StepStatus` 的四个值（`middleware/contracts.go`）里，关键是 `STARTED` 与 `FAILED` 的分工：

```go
// StepFailed means the runtime rejected a command before any physical
// effect was authorized. It remains retryable and is distinct from STARTED,
// whose physical outcome is unknown after an interrupted invocation.
```

- `FAILED` = 命令在任何物理效果被授权**之前**就被拒绝了 → **仍然可重试**
- `STARTED` = 命令可能已经产生物理效果了 → **不知道**

### 3.5.4 三个对象各自的"不知道"

同一个瞬间，三个对象进入三个**不同**的状态，而且这三个词不是同义词：

| 对象 | 进入的状态 | 含义 |
| --- | --- | --- |
| **Step** | `STARTED`（**保持不变**） | "我记了开始，没记结束"——故意不写 `FAILED` |
| **Round** | `VerdictUnsatisfied` + `Class = "UNKNOWN_OUTCOME"` | "物理结果不可判定" |
| **Task** | `RECOVERABLE_FAILURE` | "停了，但可以恢复" |
| **Runnable/API** | `PHYSICAL_OUTCOME_UNKNOWN`（409） | "先核对机器人和物体状态" |

> **⚠️ 一处必须修正的文档说法**
>
> `docs/architecture/lifecycle-objects.md` 的表格里，Tool 行写的是 `ESCALATED + class = UNKNOWN_OUTCOME`。
> **`ESCALATED` 在 Go 代码里不存在**（这是已删除的 `Track` 状态机的残留）。
> 真实存在的三件事是：Step 保持 `STARTED`（`runner.go:499-508`）、Round 记为 `VerdictUnsatisfied` 且 `Class="UNKNOWN_OUTCOME"`（`internal/actionloop/loop.go`）、Task 由事件投影为 `RECOVERABLE_FAILURE`。

### 3.5.5 持久化：`Uncertain` 从磁盘派生，不从内存派生

重启后系统怎么知道"有些步骤悬着"？答案：**从持久记录派生。**

```go
// agentruntime/memory.go（语义还原）
func (m *ExecutionMemory) Uncertain(taskID string) []StepRecord {
	var uncertain []StepRecord
	for _, step := range m.Steps(taskID) {
		if step.Reconciled {          // 人已经看过了
			continue                   // 继续报就是安全信号退化成噪音
		}
		if step.Status == StepStarted {
			uncertain = append(uncertain, step)
		}
	}
	return uncertain
}
```

两个设计点：

1. **`Reconciled` 的步骤不再报。** 注释说明理由："人已经看过了；继续报就是安全信号退化成噪音。"——**一个从不消失的告警会被忽略**，这是安全设计里最容易犯的错。
2. **上游读取者 `OpsAgent.uncertainSteps`（`agentruntime/opsagent.go`）在执行存储不可读时返回 `nil`**：

> 执行存储缺失意味着"说不出来"，规则已经会报告这件事，**编造一个空列表会被读成"没有任何不确定"**。

**"读不到" ≠ "没有"。** 这个区分在安全系统里是致命的：一个把"查询失败"当成"查询结果为空"的实现，会在最需要告警的时候保持沉默。

### 3.5.6 五个独立的强制落点

"结果未知禁止重试"不是一条规则，是**五个独立代码位置的共同效果**。任何一处被改坏，防护就漏了：

| # | 落点 | 文件:行号 | 强制方式 |
| --- | --- | --- | --- |
| 1 | **执行器不推进状态** | `edge/agent/runner.go` | 保持 `STARTED`，返回 `ErrUnverifiedWorldMutation` |
| 2 | **模型驱动的循环终止一切** | `internal/actionloop/loop.go` | `callUnknownOutcome` 直接 return |
| 3 | **续跑被拒** | `internal/localapp/recovery.go` | `CanResume` 里含 `!RequiresReconciliation` |
| 4 | **恢复计划只有只读动作** | `agentruntime/recoveryagent.go` | 分支内**不咨询模型**；`readOnlyMatches` 硬过滤 |
| 5 | **面向人的建议带禁令** | `agentruntime/opsrules.go` | `severity=critical` + `AutomaticRetryForbidden=true` |

落点 2 的代码值得单独看，因为它是"模型驱动"最容易软化规则的地方：

```go
// internal/actionloop/loop.go
case callUnknownOutcome:
	// The one outcome that ends everything. The world may already have
	// changed, so no further call is safe — not a different tool, not the
	// same tool again. This is the closed-loop contract, and a loop driven
	// by a model is exactly where it would be tempting to soften.
	outcome.Escalated = true
	outcome.Reason = roundRecord.Detail
	return outcome, nil
```

**"not a different tool, not the same tool again"**——不是"别重试这个工具"，是"什么都别做"。

为什么连换一个工具也不行？因为世界可能已经变了。你不知道杯子在不在夹爪里，所以任何一个动作都建立在错误的假设上。

落点 4 的实现细节更狠（`agentruntime/recoveryagent.go`）：

> **模型在这个分支里完全不被咨询**，这正是把变更提议挡在计划外的原因——**排除是结构性的，而不是对已经包含了变更动作的计划做过滤**。

**"结构性排除" vs "事后过滤"** 是安全设计里的一条分水岭。事后过滤意味着变更动作曾经出现在模型的输出里；结构性排除意味着它从一开始就没有机会出现。

### 3.5.7 从"未知"到"已知"：只有人能解除

系统给了三条出路，语义完全不同：

**A. 先去看一眼（对账）**

```
GET  /v1/tasks/{id}/recovery   ← 看 requiresReconciliation / uncertainStepIds
POST /v1/tasks/{id}/reconcile  ← 只有在"没有未对账步骤"时才允许
```

对账 = **去拿一个新的观测，看看杯子到底在不在夹爪里**。

这里有一个精妙的细节。`RunControl.ObservationAttempt`（`edge/agent/runner.go`）：

```go
type RunControl struct {
	BeforeStep func(context.Context) error
	// A new read identity prevents runtime idempotency caches from returning
	// pre-interruption verification evidence during an explicit resume.
	ObservationAttempt string
}
```

**一个新的读取身份，防止运行时的幂等缓存把中断前的旧观测当成新证据回放。**

如果没有这个字段，一次"对账"会从缓存里拿回中断前那份观测——一份**早于命令**的观测——然后系统会"确认"一个它根本没有检查过的世界。这个 bug 的隐蔽程度极高，因为表面上一切正常。

对账的持久化有两道硬约束（`middleware/sqlite/store.go`）：

```go
// 221-223
if !reconciliation.Outcome.Valid() { return error }
// 224-226
if Actor == "" || Note == "" {
	return errors.New("reconciliation requires the person making it and a reason")
}
// 227-232
// UPDATE step_runs SET reconcile_outcome=?, ... 
// WHERE task_id = ? AND step_id = ? AND reconcile_outcome = ''      -- ★ 只能从空到非空
// 240-242
if affected == 0 {
	return fmt.Errorf("step %s/%s is not awaiting reconciliation, or has already been reconciled", ...)
}
```

- 必须有人、必须有理由；
- `WHERE reconcile_outcome = ''` 使对账**只能发生一次且不可覆盖**——**不是靠应用层判断，是靠 SQL 条件**。

三个合法的结论（`middleware/contracts.go`）：

| 结论 | 含义 |
| --- | --- |
| `HAPPENED` | 确实发生了 |
| `NEVER_ACTED` | 确定没发生 |
| `ABANDONED` | 放弃判断，接受现状 |

**为什么这个类型必须存在？** `middleware/contracts.go` 的注释是这套设计最完整的自述：

> 它之所以存在，是因为另一条路是死胡同。一个未确认的物理步骤会阻塞 readiness 并禁止重试该步骤——这是对的——但**去看了机器人的操作员无处记录他看到了什么**，于是阻塞永远不会解除。
> **系统学会了忽略自己的安全报告**，而这正是那份报告要防止的失败。

**"系统学会了忽略自己的安全报告"**——这句话应该被刻在每一个安全系统的墙上。

**B. 改需求（Revision）**

```
POST /v1/tasks/{id}/revisions           → PROPOSED
POST /v1/tasks/{id}/revisions/2/confirm → WAITING_SAFE_POINT → ACTIVE
```

`WAITING_SAFE_POINT` 的意思是：**改版不能打断正在执行的物理动作**，等它走到一个安全点再生效。

而且有一条堵死的绕路（`internal/localapp/recovery.go`）：只要已存在非只读的 `COMPLETED` 步骤，切版直接报错——"任务已执行部分物理动作，请先恢复原版本完成任务；**不能切换版本重复执行已完成动作**"。

**C. 算了（Cancel）** → `CANCELLED`

### 3.5.8 一件必须诚实说明的事

`docs/architecture/lifecycle-objects.md` 里，作者自己加了一段诚实说明：

> 仓库里现存的事故记录（`artifacts/incidents/`）**没有一条**真的走到 `uncertainStepIds` 非空——那 13 条都是 `NO_RECOVERY_REQUIRED` 或 `PAUSING`。
> 上面这个断网场景是**根据代码语义构造的**，不是从真实事故里摘的。它在测试里有覆盖（`edge/agent/recovery_test.go`、`internal/localapp` 的恢复用例）。

**所以"结果未知"在生产事故归档里尚无一手实例。** 有的一手证据是验收任务与单元/集成测试：

| 证据 | 内容 | 出处 |
| --- | --- | --- |
| 验收任务 1 | 任务 `task-e2c52a114e397872b1670852` 在拿取中被终止；重启后 resume 返回 **409 `PHYSICAL_OUTCOME_UNKNOWN`**，**拿取仅派发 1 次，放置 0 次** | `docs/development/single-robot-loop.md` |
| 验收任务 2 | 任务 `task-ee64bd90bc06c0815c2ff637` 在 revision 1 完成两目标，拿取/放置各调用 2 次，**完成动作未重放** | 同上 |
| 测试 | `TestClaimLeaseLapseLeavesTheOutcomeUnknownInsteadOfReclaiming` 等四个测试把"不能退回 READY"钉死为回归测试 | `fleet/coordinator/coordinator_test.go` |

**在书里保留这个限定很重要**：一个机制"设计正确且有测试覆盖"和"在生产中触发过并正确处理"是两种不同强度的证据。这正是前言里那张"四种证据强度"表要教的东西。

---

## 3.6 物理接地验证（GVF / GCL）：更严的那一层

前面讲的 Gate 有一个内在限制：**它只判断证据"新不新"，不判断证据"内容对不对"。**

一份命令后的新鲜观测说"这里有个杯子"，Gate 就放行——至于杯子是不是**真的在夹爪里**，Gate 不管。

GVF 就是来管这件事的。

### 3.6.1 三层结构

```
GCL（Grounded Contract Language）  机器人侧的三值可执行合约检查器
        ↓
GVF（Grounded Verification Framework）   把 GCL 接进运行时与控制面的那一层
        ↓
Go 侧投影   core/closedloop/grounded_report.go
```

- **GCL**：`robot/gateway/tangying_robot_gateway/grounded/verifier.py`，文件头自述 *"Finite-trace, three-valued GCL interpreter with local evidence validation."*
- **GVF Runtime**：`robot/gateway/tangying_robot_gateway/grounded/runtime.py`，位于"**已准入的执行边界之内、终态成功之前**"
- **证据存储**：`robot/gateway/tangying_robot_gateway/grounded/store.py` 的 `EvidenceStore`，原始字节落 `blobs/<sha256>`
- **Go 侧消费**：`edge/agent/runner.go`

### 3.6.2 三值逻辑与九个谓词

**verdict ∈ {`VERIFIED`, `FALSIFIED`, `UNKNOWN`}**

`combine()` 是强 Kleene 语义（`verifier.py:34-49`）：合取中确凿反例为 `FALSIFIED`，全部通过才 `VERIFIED`，否则 `UNKNOWN`。

**九个谓词**（ADR-10 表格）：

| 谓词 | 判据 |
| --- | --- |
| `At` | 位置误差 ≤ 0.05 m，航向 ≤ 0.12 rad |
| `On` | 底部距支撑面 ≤ 0.02 m |
| `Holding` | 闭合 + 负载 ≥ 0.1 N + 身份匹配 |
| `In` | 物体边界完整落在容器内 |
| `Stable` | 相邻帧位移 ≤ 0.01 m |
| `Clear` / `Released` / `Safe` / `Capacity` | 力 ≤ 20 N 等 |

**三个核心合约**（`grounded-contracts.json`）：

| 动作 | 合约 |
| --- | --- |
| 抓取 | 连续三帧 `Holding ∧ ¬On ∧ Stable` |
| 放置 | 连续三帧 `In ∧ Stable` 且夹爪释放 |
| 导航 | 连续三帧 `At` |

### 3.6.3 和主闭环契约什么关系：两层，不是两套

| 层 | 回答的问题 | 判据 | 输入 |
| --- | --- | --- | --- |
| **主闭环契约** | 这一次工具返回**算不算完成** | **时间戳新鲜度** | `Evidence{ObservationID, ObservedAt, Freshness}` |
| **GVF** | **物理关系是否真的成立** | **谓词 + 时序算子** | 内容寻址的多模态测量流 |

Go 侧的交接点：

```go
// edge/agent/runner.go
433  groundedVerified := false
434  if os.Getenv("TANGYING_GVF_ENABLED") == "1" && closure.declaration(step.Skill, step).Mutates() &&
435      step.Skill != "emergency_stop" && skillResult.StateReportJSON == "" {
436      return fmt.Errorf("%w: GVF requires a StateReport for %s", ErrPhysicalOutcomeUnknown, step.Skill)
437  }
...
453  groundedVerified = report.Verdict == "VERIFIED"
454  if !groundedVerified {
455      return fmt.Errorf("%w: %s %s", ErrPhysicalOutcomeUnknown, report.Verdict, report.FailureType)
456  }
...
498  decision := closedloop.Gate(declaration, dispatchedAt, closureEvidence)
499  if err := decision.Require(); err != nil && !groundedVerified {
```

**读法：GVF 通过时直接跳过 Gate**（`&& !groundedVerified`）；**GVF 未通过时返回 `ErrPhysicalOutcomeUnknown`**（未知结果屏障），而不是退回 Gate。

所以 **GVF 是更严的旁路，不是替代品**。

Python 侧的屏障语义（`robot/gateway/tangying_robot_gateway/grounded/runtime.py`）：一旦有物理动作未确认，之后**所有**物理命令直接返回 `NOT_DISPATCHED`，根本不进执行。

解除屏障的三个必要条件（`runtime.py:234-259`）：

1. 同一个**意图效果**（`verify_grasp → manipulation.pick` 等映射）
2. **全部键值完全匹配**（pick → `("object","gripper")`；place → `("object","container","gripper")`；navigate → `("goalPose","location")`）
3. 新证据裁定 `VERIFIED`

注释原文：

> 一个只读验证器只能为**完全相同的意图效果**、从新鲜证据清除屏障；**调用一个无关的观察不是对账**。

这比 Go 侧的对账更强——**Go 侧的人可以对账成 `ABANDONED`，GVF 屏障只接受 `VERIFIED`**。

### 3.6.4 为什么默认关闭

开关：`TANGYING_GVF_ENABLED=1`。证据根目录 `TANGYING_GVF_ROOT`。

**Runtime 与 Local Agent 都要设**（`docs/development/single-robot-loop.md`）。

默认关闭的三个理由，都能从代码读出：

**① 它要求适配器实现采集器，而多数适配器没有。**

> **没有采集器的适配器返回 UNKNOWN，不能仅打开开关就当作具备物理验证能力。**

`robot/gateway/tangying_robot_gateway/grounded/runtime.py` 的 `_collect` 在 backend 没有 `collect_grounded_evidence` 时返回空列表，注释写明：**"传感器失败不能把工具返回提升为世界事实。"**

**② 它会把大量动作直接拒绝。**

> 尚无后置合约的 `arm.move`、`navigation.pre_position`、`recover_to_safe_pose` 在 GVF 模式下**拒绝派发**；急停始终可以执行。

**③ 它是实验阶段的机制，阈值是夹具工程的工程值。**

ADR-10 原文：

> 阈值是本次夹具/导航实验的**工程值，不是实机通用标定**。
> 置信度**未做频率校准**，0.95 不代表已证明的 95% 正确率。

还有一条安全性的**正向**理由：**原始证据留在边缘**，控制面只收到 `evidence://source/<sha256>` 与标量摘要。纳秒身份以 JSON 文本传递，不经 protobuf `Struct` 的浮点表示——避免精度损失造成身份误判。

### 3.6.5 实验结论：必须区分实测与离线估计

数据源：`docs/experiments/2026-09-21-grounded-verification.md`。规模：**150 个 Gazebo 任务实例、210 条物理动作轨迹、九组 1,890 条评估、1,470 次真实模型调用**。

⚠️ 报告第 4 行明确：**原始记录不随 Git 分发。**

**属于实测的部分：**

| 指标 | 数值 |
| --- | --- |
| 共同物理轨迹的实际任务成功率 | **38.00 ± 1.83%** |
| **错误接受率**（B0 → GVF） | **58.57% → 13.81%**，双侧配对 t，**p=0.00003336** |
| 准确率（B0 → GVF） | **41.43% → 64.76%** |
| 错误接受的绝对条数 | B0 `123/210`、B2 `47/210`、GVF `29/210` |
| 验证器延迟 | GVF **4.97 ± 1.18 ms**（含本地证据哈希检查、合约解释、报告渲染，不含采集） |
| 实测模型调用 | 总 tokens **4,596,735**；协议异常 **0** |
| 完整性复核 | **6,259** 个唯一证据哈希、**1,470** 个模型响应哈希通过 |
| 正常运行验收 | 导航 0.25 m 后独立世界位姿误差 **0.01308 m**（阈值 0.05 m） |

**错误接受率从 58.57% 降到 13.81%** —— 这是本书里关于"信任工具回执"的最有力的一个数字：**超过一半的"成功"是假的。**

**属于离线/回放估计的部分（报告自己反复标注）：**

> 各组**没有独立在线运行**；任务成功率、恢复步骤和端到端成本是该回放策略的**估计**，**不能当作九组自主机器人实测**。

**负面结论（必须一并给出）：**

| # | 结论 | 数字 |
| --- | --- | --- |
| 1 | **不能宣称 GVF 全面优于 LLM** | A5（带合约的 LLM）总准确率 **74.29%** > GVF 的 64.76%，差 9.52 个百分点，**p=0.0002249** |
| 2 | **时序约束提升准确率：未得到支持** | 移除时序算子（A1）后三值判定与 Ours **完全相同**，总正确判定数变化为 **0** |
| 3 | **弃权是可见的安全代价，不能从分母删除** | B2（只用末帧几何）准确率 70.48% > GVF，因为 GVF 有 45/210 次弃权 |
| 4 | **GVF 的 29 次错误接受全部来自未注入故障的导航动作** | 轮式里程计到达目标，独立世界位姿**没到达** |
| 5 | **分类帮助恢复，但不是执行器能力提高** | A3（隐藏失败分类）回放任务成功率 38% vs Ours 60% |

结论 4 被称为"本报告的主要负面发现"，教训原文：

> **多帧重复同一种有偏测量依然会得到错误结论。**

**这一条应该被所有做"多次测量取共识"的人抄在笔记本上**：重复一个有偏的测量，得到的是更自信的偏见，不是真相。

### 3.6.6 真实失败案例：工具 SUCCESS，物理 FALSIFIED

报告 §4 列了九条带内容寻址证据的案例，挑最能说明问题的：

| 位置 | 工具返回 | 物理真值 | 验证器裁定 |
| --- | --- | --- | --- |
| `1729/task-02` | SUCCESS | FALSIFIED | `FALSIFIED/GRASP_MISS` |
| `1729/task-03` | SUCCESS | FALSIFIED | `FALSIFIED/GRASP_SLIP` |
| `1729/task-04` | SUCCESS | FALSIFIED | `FALSIFIED/WRONG_OBJECT` |
| `1729/task-05` | SUCCESS | VERIFIED | `UNKNOWN/PERCEPTION_OCCLUDED`（**误报保守**） |
| `1729/task-06` | SUCCESS | VERIFIED | `UNKNOWN/EVIDENCE_INSUFFICIENT` |
| `1729/task-27` | SUCCESS | FALSIFIED | **`VERIFIED/NONE`（漏报，最危险）** |

每一条都带 `evidence://edge/<sha256>` 的内容寻址证据与 `gvf-<sha256>` 报告 ID。

注意最后一行：**验证器也有漏报**。这不是一个"加了验证就安全了"的故事，是一个"把错误接受率从 58.57% 降到 13.81%"的故事。

---

## 3.7 七项闭环责任：与 Coding Agent 的共同问题和具体差异

Coding Agent 也可能调用部署、数据库、网络和通知工具，因此不能把“可验证、可撤销、随便重试”当成它的默认性质。第16章给出完整对照；这里说明本项目如何落实七项责任。

| 责任 | 本项目的机制 | 适用边界 |
| --- | --- | --- |
| 完成判定 | `toolResult.Success` 触发观测，Gate 检查后置条件与证据 | 工具回执不等于目标已达成；软件部署也需要独立结果核验 |
| 重试判断 | 未识别错误归 `UnknownOutcome`；`Transient` / `Perception` 可进入受约束的恢复路径 | 分类允许恢复不代表任何参数、现场条件下都能立即重放 |
| 崩溃恢复 | 从 SQLite `step_runs` 找回未确认步骤 | 丢失的可能是执行状态；软件系统也可能丢失已发出的外部副作用 |
| 人工对账 | `StepReconciliation` 记录操作者和结论 | 是风险与权限选择；没有可靠观测时不能自动解除未知状态 |
| 最终准入 | 对账分支不请求模型；审批需求由可信目录推导 | 模型不能靠工具文本自授执行权限 |
| 副作用声明 | 本地 manifest 与 Runtime 声明取并集 | 是当前可信接线中的保守合并，不证明任一来源不可被篡改 |
| 分类完整性 | `Knows()`、错误码清单与覆盖测试 | 避免已知错误落入保守兜底；具有高风险工具的软件 Agent 同样需要 |

`docs/architecture/supervision-verification.md` 记录过监督者只订阅未来事件、重启后遗漏磁盘未确认步骤的盲区。其教训是：沉默不能表示健康，持久记录必须接入恢复判断。门禁的毫秒同桶兼容仍有因果局限，见3.2与附录D。

---

## 3.8 本章小结：三句话

1. **工具返回成功，只是"去看世界"的触发器。** 完成必须由一次独立的、晚于命令下发的观测确认。判定顺序不可交换——不知道下发时刻的命令，不会因为带了一份新鲜观测就通过。

2. **失败分类回答的是"唯一安全的下一步"，不是"错误的原因"。** 表以"安全动作"来组织，兜底方向是最保守的一侧，未识别的码一律禁止自动重试。**因为兜底猜错的代价不对称。**

3. **没有自动重试器。** 物理失败结束步骤，重新尝试是操作员的决定，需要批准。为物理写入做重试预算，必须先把尝试计数持久化、先决定谁批准第二次尝试——**那是一项设计，不是一个补丁**。

---

## 3.9 教学要点

### 三个可用的比喻

**比喻 A：快递签收。**
"工具返回成功" = 快递员说"送到了"。这个陈述是真的（他确实来过、确实按了门铃），但它不是"包裹在屋里"。**验收需要另一次独立的观测**，而且必须是**下单之后**的。一张拍于下单之前的门口照片，不是这次快递的证据。

**比喻 B（更贴合）：手术器械清点。**
手术结束前必须清点器械——不是为了记录，而是因为**下一次操作的前提是知道上一手的真实结果**。如果纱布数量对不上，正确的反应不是"再数一遍"或"继续缝合"，而是**停下来、用只读手段查清、由人签字**。

这正好对应：`Transient`/`Perception` 允许"再数一遍"，`UnknownOutcome` 只允许"查清"（`readOnlyMatches`），而清点结果**只能由人写一次**（`WHERE reconcile_outcome = ''`）。

**比喻 C（讲"未知结果"最佳）：停电时的刀。**
你切三明治切到一半，手滑了，然后停电。你不知道刀现在在哪。这时候任何"重来一次"的动作都是**伸手去摸——可能摸到刀刃**。

### 一个可以三行跑通的最小示例

```go
// 只读工具：不需要证据
closedloop.Gate(closedloop.Declaration{}, time.Time{}, nil)
// → {Satisfied: true, Required: false, Reason: "CLOSURE_NOT_REQUIRED"}

// 改世界的工具：一次成功的返回不足以完成
closedloop.Gate(closedloop.Declaration{Manifest: true}, dispatch, nil)
// → {Satisfied: false, Reason: "CLOSURE_EVIDENCE_REQUIRED"}

// 有观测也救不了：它早于命令
closedloop.Gate(closedloop.Declaration{Manifest: true}, dispatch,
    &closedloop.Evidence{ObservationID: "obs-before",
        ObservedAt: dispatch.Add(-time.Second), Freshness: "FRESH"})
// → {Satisfied: false, Reason: "CLOSURE_EVIDENCE_STALE"}
```

三个断言可直接对照 `core/closedloop/gate_test.go`。

### 学生最容易误解的十个点

| # | 误解 | 纠正 |
| --- | --- | --- |
| 1 | `UNKNOWN_OUTCOME` 意味着**失败** | 它意味着**不知道**。可能成功了，可能失败了一半。所以它既不是 `FAILED`，也不是可重试的失败 |
| 2 | 系统会**自动重试**瞬时故障 | **没有自动重试器。** `Retryable()` 是"关于失败的陈述，不是关于系统的陈述"。物理重试是操作员决定，走 `task.retry-step`，要批准 |
| 3 | 未识别的码就是瞬时故障，重试一次没关系 | 兜底方向**相反**：未识别 → `UnknownOutcome` → **禁止**重试。这是本章的核心取舍 |
| 4 | 观测**够新**就行 | 不够。**必须晚于命令下发**。10ms 前拍的、但拍于命令之前的照片，完全无用 |
| 5 | 经验丰富的观测者可以凭一次观测断定成功 | `*_NOT_OBSERVED` 家族恰好是反例：**动作跑了、没检查**。而 `GRASP_FAILED`（检查了，手是空的）反而是已知的感知失败。**区分它们的是"有没有检查过"，不是"结果好不好"** |
| 6 | 多帧重复测量更可靠 | 实验明确否证：GVF 的 29 次错误接受**全部**来自同一有偏测量重复多帧（轮式里程计） |
| 7 | 未知结果被人对账掉了 = 成功了 | 不是。`StepRecord` 的注释："它**不意味着结果已知**：对于一个从未记录过结果的步骤，没有什么能让它变已知。它意味着**有人看过了**" |
| 8 | 把任务版本改一下就能绕过 | 不能。`checkRevisionRecovery` 只要发现存在非只读 `COMPLETED` 步骤就拒绝切版 |
| 9 | `Freshness` 是证据自己的属性 | 它是**投影对来源给出的裁决**，由消费方计算，并在急停时被强制降级为 `UNKNOWN` |
| 10 | 仿真里能跑通 = 实机行为已验证 | 报告结论："**环境感知通过不意味着这套控制实现能直接迁移至实机**" |

### 练习题

**题 1（时序与粒度）**

某 runtime 以 Unix 毫秒上报采集时间。命令在 `T = 12:00:00.000400`（400 微秒）下发，证据观测时间为 `12:00:00.000000`。Gate 判通过还是拒绝？把观测时间改成 `12:00:00.000500` 呢？说明当前兼容策略为何不构成严格的因果证明。

<details>
<summary>答案要点</summary>

- `NormalizeDispatchTime` 把下发时刻截断到 `12:00:00.000`；`12:00:00.000000` 截断后**相等**，`observedMS.Before(dispatchedMS)` 为 false → **通过**。
- `12:00:00.000500` 截断到 `12:00:00.000`，同样相等 → **通过**。证据：`core/closedloop/gate_test.go`。
- **边界**：同桶不区分真实先后；缺少精度意味着顺序未知。当前实现接受同桶，但更强放行判据须结合设备序列、命令关联和时钟误差预算。
- 反例边界：`12:00:00.000000 - 1ms = 11:59:59.999` 截断后小于 `12:00:00.000` → **拒绝**。

</details>

**题 2（分类归属）**

`PLACEMENT_NOT_OBSERVED` 与 `PLACE_NOT_REACHED` 的物理语义差别是什么？为什么一个必须是 `UNKNOWN_OUTCOME`、另一个可以是 `PERCEPTION`？如果错误地把 `PLACEMENT_NOT_OBSERVED` 归到 `PERCEPTION`，最坏会发生什么？

<details>
<summary>答案要点</summary>

- 差别在**动作是否已执行**：`PLACE_NOT_REACHED` 在夹爪张开**之前**返回（接近或最终位姿检查失败），物体仍然被握着 → 物理效果**可证伪，确定没发生**。`PLACEMENT_NOT_OBSERVED` 在放置动作**完成之后**返回，只是没观测到预期关系 → **物体可能已经在目的地了**。
- 归类理由：`Perception.Retryable() == true` 允许重试；`UnknownOutcome.Retryable() == false`。
- **最坏后果**：重试一次已经成功了的放置——第二次释放会把物体推离目的地或造成碰撞。源码注释："对放置来说，**物体可能已经在目的地了**"。
- 对照：`GRASP_FAILED` 是 `Perception`（手**已知**是空的），`GRASP_NOT_OBSERVED` 是 `UnknownOutcome`。

</details>

**题 3（链路推理）**

一个物理步骤执行到一半时 Local Agent 进程被 `SIGKILL`。请列出重启后系统按顺序读取哪些持久数据、得出什么结论、以及**哪一步必须由人完成**；并说明为什么"删掉 `agent.db`"不是对账。

<details>
<summary>答案要点</summary>

1. `edge/agent/runner.go` 在调用前已 `MarkStepStarted`，SQLite `step_runs` 里该步骤状态为 `STARTED`。
2. `agentruntime/memory.go` 的 `Uncertain` 返回该步骤（`Status == STARTED` 且 `Reconciled == false`）。
3. `agentruntime/opsrules.go` 产出 `ANOMALY_UNVERIFIED_MUTATION`，`severity=critical`，`AutomaticRetryForbidden=true`。
4. `agentruntime/recoveryagent.go` 走对账分支，计划**只由 `readOnlyMatches`（只读）构成**，`TrailRule` 名为 `rule.reconcile-first`，`mutationsHeldBy = "闭环契约：结果未知时不得改动机器人"`。
5. `internal/localapp/recovery.go` → `RequiresReconciliation = true`、`CanResume = false`、`ReasonCode = "PHYSICAL_OUTCOME_UNKNOWN"`；HTTP 层返回 409。
6. **必须由人完成的一步**：对账接口 → `Runner.ReconcileStep` → `middleware/sqlite/store.go`，要求 `Outcome ∈ {HAPPENED, NEVER_ACTED, ABANDONED}` **且** `Actor`、`Note` 非空，`WHERE reconcile_outcome = ''` 保证一次性。
7. **删掉 `agent.db` 不是对账**，因为它是"**删除证据**"而不是"**获取证据**"：`Uncertain` 派生自持久记录，记录没了阻塞会消失，但**世界状态一点都没被确定**。文档明说：**"重启会保留未知结果屏障，删除数据不能视作对账。"**

</details>

---

## 3.10 源码索引

### 契约与门禁

| 内容 | 位置 |
| --- | --- |
| 包文档：工具结果是看世界的触发 | `core/closedloop/closedloop.go` |
| 重试状态机为何被删除 | `core/closedloop/closedloop.go` |
| `Class` 与 8 个类常量 | `core/closedloop/closedloop.go` |
| `ErrEvidenceRequired` / `ErrEvidenceStale` | `core/closedloop/closedloop.go` |
| 分类表（177 码） | `core/closedloop/closedloop.go` |
| `UnknownOutcome` 15 码与设计注释 | `core/closedloop/closedloop.go` |
| `Classify` 兜底方向 | `core/closedloop/closedloop.go` |
| `Knows` | `core/closedloop/closedloop.go` |
| `Retryable` | `core/closedloop/closedloop.go` |
| `DispatchPrecision` | `core/closedloop/closedloop.go` |
| `NormalizeDispatchTime` | `core/closedloop/closedloop.go` |
| `Evidence` 结构 | `core/closedloop/closedloop.go` |
| `Declaration` / `Mutates` | `core/closedloop/gate.go` |
| Reason 常量 | `core/closedloop/gate.go` |
| `Gate` 主流程 | `core/closedloop/gate.go` |
| `validateFreshness` 四条校验 | `core/closedloop/gate.go` |
| Gate 全部边界用例 | `core/closedloop/gate_test.go` |
| 分类断言 | `core/closedloop/closedloop_test.go` |
| 覆盖率守卫与 `runtimeEmittedCodes` | `core/closedloop/classification_coverage_test.go` |

### 执行与持久化

| 内容 | 位置 |
| --- | --- |
| 单步执行的完整顺序 | `edge/agent/runner.go` |
| `preflightFailure` 白名单 | `edge/agent/runner.go` |
| `ObservationAttempt` | `edge/agent/runner.go` |
| `closureEvidence` / `evidenceFromSnapshot` | `edge/agent/runner.go` |
| `StepStatus` 四态 | `middleware/contracts.go` |
| `StepOutcome` 三值 | `middleware/contracts.go` |
| `StepReconciliation` | `middleware/contracts.go` |
| `ReconcileStep` 与 SQL 条件 | `middleware/sqlite/store.go` |
| `ExecutionMemory.Uncertain` | `agentruntime/memory.go` |
| `uncertainSteps`（读不到就是说不出来） | `agentruntime/opsagent.go` |
| `choosePlan` 对账优先分支 | `agentruntime/recoveryagent.go` |
| `readOnlyMatches` | `agentruntime/recoveryagent.go` |
| `task.retry-step` 目录条目 | `agentruntime/recoverycatalog.go` |
| 三个风险级 | `agentruntime/recoverycatalog.go` |
| `callUnknownOutcome` 终止一切 | `internal/actionloop/loop.go` |
| `callVerdict` 判定 | `internal/actionloop/loop.go` |
| `needsApproval` | `internal/actionloop/loop.go` |
| `Recovery` 视图与 `CanResume` | `internal/localapp/recovery.go` |
| `checkRevisionRecovery` 禁止切版绕过 | `internal/localapp/recovery.go` |
| 建议表与 `AutomaticRetryForbidden` | `agentruntime/opsrules.go` |

### GVF

| 内容 | 位置 |
| --- | --- |
| 三值 GCL 解释器 | `robot/gateway/tangying_robot_gateway/grounded/verifier.py` |
| `combine` 强 Kleene | `robot/gateway/tangying_robot_gateway/grounded/verifier.py` |
| GVF Runtime 与屏障 | `robot/gateway/tangying_robot_gateway/grounded/runtime.py` |
| 屏障释放条件 | `robot/gateway/tangying_robot_gateway/grounded/runtime.py` |
| 三个核心合约 | `robot/gateway/tangying_robot_gateway/assets/grounded-contracts.json` |
| Go 侧报告结构 | `core/closedloop/grounded_report.go` |
| GVF 实验报告 | `docs/experiments/2026-09-21-grounded-verification.md` |

### 文档与真实案例

| 内容 | 位置 |
| --- | --- |
| 生命周期对象（十个对象） | `docs/architecture/lifecycle-objects.md` |
| 断网场景与诚实说明 | `docs/architecture/lifecycle-objects.md` |
| 监督盲点与 83 码审计 | `docs/development/2026-09-17-supervision-blind-spots.md` |
| 「搬一个报成功」实测 | `docs/development/home-scene-expansion-plan.md` |
| 监督者沉默被读成健康 | `docs/architecture/supervision-verification.md` |
| `409 PHYSICAL_OUTCOME_UNKNOWN` 验收 | `docs/development/single-robot-loop.md` |
| 为什么是分层的（与 Codex 对比） | `docs/architecture/why-distributed.md` |

---

**上一章**：[第 2 章 架构总览与演进史](../chapters/ch02-architecture-evolution.md) · **下一章**：[第 4 章 世界模型：当「真相」只能被观测](../chapters/ch04-world-model.md) —— 如果完成需要证据，那么证据描述的那个「世界」是什么？它为什么必须有版本号？
