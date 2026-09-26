# 第 7 章 任务编排：从一句中文到 12 步计划

> **版本口径**：本章包含 v0.6.0/v0.7.0 演进案例。代码片段、计数与实验按原时点解释；出版复核修正论证，不表示历史缺口均为当前状态。当前云边能力见第17章，来源与证据边界见出版说明。

> **本章的核心命题**
>
> 通用 coding agent 的 planning 面对的是**文件系统**——可以任意读、真相确定、读多少次都行。
>
> 机器人 planning 面对的是一个**不完整的、会过期的、只能被观测的世界**。
>
> 这一个差别，改变了一整层代码的形状。

---

## 7.1 先看结果：12 步

用户输入一句中文：

```
从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅
```

系统产出的 12 步计划（`README.md:161-176`）：

| # | 步骤 | 工具 | 这一步在证明什么 |
| --- | --- | --- | --- |
| 1 | 看一眼现场 | `observe_scene` | 当前能看到哪些物体与房间 |
| 2 | 走到厨房 | `navigation.navigate` | 底盘真的移动了 |
| 3 | 确认到达 | `verify_arrival` | 用**新的**底盘相机与位姿误差确认，不是相信回执 |
| 4 | 重新观察 | `observe_scene` | 到达后重新感知，**不复用出发前的画面** |
| 5 | 解析目标 | `resolve_targets` | 陶瓷杯与收纳盘在场景中的身份 |
| 6 | 规划抓取 | `plan_grasp` | 抓取位姿可行性 |
| 7 | 拿起 | `manipulation.pick` | 物理动作 |
| 8 | 确认拿起 | `verify_grasp` | 物体确实离桌并在夹爪中 |
| 9 | 放下 | `manipulation.place` | 物理动作 |
| 10 | 确认放好 | `verify_placement` | 连续三帧稳定处于 `inside:kitchen-tray` |
| 11 | 回到客厅 | `navigation.navigate` | 底盘再次移动 |
| 12 | 确认回到客厅 | `verify_arrival` | 同第 3 步的判据 |

**实测结果：全部闭合、12 条观测证据。**

**返程那一步例外**：README 明确标注（`:151`）——

> ⚠️ 受地图覆盖限制。参考地图上返程点未认证可通行，`navigation.navigate` 以 `GOAL_NOT_CLEAR` 结束。这是"**不知道的地方不进去**"，不是缺陷，但**在这一版上返程确实没走通**。

> **⚠️ 一处文档版本冲突，引用时必须带日期**
>
> `README.md:151` 记返程未走通；而 `docs/experiments/2026-09-20-semantic-map-representations-and-nl-navigation.md` 在修掉两个缺陷（"守卫陷阱"与"委托拓扑未发布"）后报告**同一栋房子 4 个房间 8 条腿全部 `NAV_REACHED` + `verify_arrival` 确认**。
>
> **两处都对，但说的是不同版本的代码。** 引用必须带日期，否则就是把后来的结论塞进从前的证据里。

---

## 7.2 完整数据流：从一句话到一条可执行计划

这一节把整条链路逐列出来，**每一步标注真实的函数与位置**。

| # | 动作 | 位置 |
| --- | --- | --- |
| 1 | `tasks.Service.Create(request, adapter)` | `tasks/service.go` |
| 2 | `s.currentParser().Parse(request)` —— **解析器在锁后读，可运行时替换** | `tasks/service.go` |
| 3 | `agent.Parser.Parse`：`ValidateRequest` → **确定性优先** → 模型兜底 → 两错合一 | `agent/agent.go` |
| 4 | `NormalizeAdapter`（`auto/sim`→`mujoco` 等） | `tasks/service.go` |
| 5 | `s.worldFor(adapter)`：GVF 开则走 grounded world，否则取该 adapter **最新遥测** → `WorldFrom` | `tasks/service.go` |
| 6 | `orchestration.New(...)`：**provider=openai 且 BaseURL/APIKey/Model 三者齐备**才返回 `LLMPlanner`，否则 `DeterministicPlanner` | `orchestration/llm.go` |
| 7 | `LLMPlanner.Plan`：`agentcontext.Project(..., "planning")` 生成模型输入 | `orchestration/llm.go` |
| 8 | 采样循环：`marshal → post → parseBundle → validateBundle`，**每个失败都追加进 `Rejections` 而不是中止** | `orchestration/llm.go` |
| 9 | 无候选 → `Bundle{Source: deterministic, Attempts, Rejections}`；有候选 → 按 **`sha256(plans)` 的众数**选一份 | `orchestration/llm.go` |
| 10 | 规划整体报错 → `Bundle{Source: deterministic, Rejections: [err]}`（**失败可见，不静默**） | `tasks/service.go` |
| 11 | 落库：`Task{State: READY}` + `TaskRevision{ApprovalRequired: true, RiskClass: "physical"}` + `buildRevisionSteps` | `tasks/service.go` |
| 12 | 步骤级安全字段：`SafetyLevel`、`ApprovalID="approval:<taskID>:physical"`、`DeadlineUnixMS`、`LeaseMS`、`IdempotencyKey` | `skills/manipulation/plugin.go`；`edge/agent/runner.go` |
| 13 | 审批：`Task.Approved/ApprovedBy/ApprovedAt`（**谁批的、什么时候批的**是三个独立字段） | `tasks/service.go` |
| 14 | 执行：`Runner.RunControlled` → `grounder.Ground` → `planForIntent` → 逐步执行 | `edge/agent/runner.go` |
| 15 | 编译：`compiler.Compile` 校验形状 + 拓扑排序 + 环检测 | `core/compiler/compiler.go` |
| 16 | 运行态：`taskgraph.GraphRuntime` 维护 ready 集并在节点终态时刷新后继 | `core/taskgraph/runtime.go+` |

**三个细节值得单独指出**：

**① 第 2 步："解析器在锁后读，可运行时替换"。**

这意味着**解析器是一个可变依赖**。它的用途在第 6 章提过：`ExecutableConfig()` 那种"受支持的对照配置"。

**② 第 9 步："按 `sha256(plans)` 的众数选一份"。**

这是**自一致性采样**（self-consistency）：多次采样，选出现次数最多的那一份计划。用 `sha256` 作为等价判据，而不是字符串相等——因为 JSON 的键序可能不同。

**③ 第 8 步："每个失败都追加进 `Rejections` 而不是中止"。**

**一个候选被拒绝不是失败，是信息。** 报告里保留所有拒绝原因，让"为什么这个计划没能用"可查。

---

## 7.3 三条规则：确定性优先、失败不降级、规划层另有后备

这是本章最重要的设计。三条规则各有明确的代码位置。

### 规则一：已知意图优先走确定性解析，成功不被覆盖

```go
// agent/agent.go
// Deterministic first: it is exact for the phrasings it knows, costs nothing
// and cannot hallucinate. A success here is not overridden.
```

**四句理由**：

| 理由 | 含义 |
| --- | --- |
| **it is exact for the phrasings it knows** | 对它覆盖的句式是**精确的** |
| **costs nothing** | 零成本（无网络、无 token） |
| **cannot hallucinate** | **不会幻觉** |
| **A success here is not overridden** | 成功不被模型覆盖 |

**最后一句是设计决定**：即使配了模型，确定性解析成功时**不问模型**。

**为什么？** 因为模型可能给出一个"更好"但**不同**的解析——而两个解析都"对"时，选哪个取决于一个非确定的过程。

**这会让同一个输入在两次运行中得到不同结果**——对一个要交付给操作员审批的系统来说，这是不可接受的。

### 规则二：解析失败返回原解析错误，不静默降级

```go
// agent/agent.go（语义还原）
if errors.Is(deterministicErr, intent.ErrClarificationRequired) {
    return fmt.Errorf("%w（模型也没能理解：%v）", deterministicErr, modelErr)
}
return deterministicErr   // ← 原样返回
```

注释解释了为什么必须这样写（`:84-88`）：

> 澄清文本保留在**错误头部**，因为那是**操作员能据以行动的部分**（"说清楚是哪个房间"）。
> **模型的失败是被追加而不是被替换**：一个模型够不到的部署，**不能看起来像一个语法很窄的部署**。

而同一段注释还记着被这条规则修掉的旧行为：

> **「语法表达不了」和「这句话本身有歧义」曾被当成同一个答案**，
> 于是一个**已经为模型付了钱**的部署，仍然对"把红色杯子放到桌上"回答"请明确交接区、目标区或收纳盒"——
> 而模型对这句话**毫不犹豫地就能解对**。

**教学要点**：这是第 2 章修复 7 的完整论证。

| 诊断 | 含义 | 正确的行动 |
| --- | --- | --- |
| **语法太窄** | 解析器不认识这个说法 | **配模型**（或扩展语法） |
| **这句话有歧义** | 人也没法确定要什么 | **问用户** |

**把两者合并成一个错误，就等于把一个可修的问题（配模型）伪装成一个不可修的问题（重新表述）。**

### 规则三：规划层另有确定性后备

三个位置：

| 位置 | 行为 |
| --- | --- |
| `orchestration/llm.go` | 配置不全 → `DeterministicPlanner` |
| `orchestration/llm.go` | 所有候选被拒 → 返回 `Source: deterministic`，但**保留 `Attempts` 与 `Rejections`** |
| `tasks/service.go` | `NewService` 无 planner 时同样兜底 |

### ⚠️ 一个必须纠正的误解：`DeterministicPlanner` 不产出步骤

**这是本章最容易讲错的一处。**

`DeterministicPlanner.Plan` 返回的 `Bundle` **不含 Steps**（`orchestration/types.go`），注释写明它：

> **"忽略世界，因为它自己不产出任何需要与世界一致的步骤"**

**真正产出确定性步骤的是领域计划构造器 `skills/manipulation.Plan`**（`plugin.go:68+`），由运行时调用。

**所以"确定性后备"指的是那条经过验证的领域计划，不是 orchestration 包里那个空 Bundle。**

**混淆这两者会得出一个错误结论**："后备是空操作"——而实际上后备是**整条领域计划路径**。

### 指标层把「后备」当成可观测事件

```go
// orchestration/metrics.go
LLMFallbackTasks = 来源是 deterministic 且 Attempts > 0
```

**定义是"试过模型但没用上"，而不是"没配模型"。**

**教学要点**：这是一个**指标定义**的范例。

"后备率"这个指标有两种可能的定义：

| 定义 | 含义 |
| --- | --- |
| 来源是 deterministic | **包含了"没配模型"** —— 而那不是后备，那是配置 |
| 来源是 deterministic **且** `Attempts > 0` | **只包含"试过但失败"** |

**第二个定义才能回答"模型在这里表现如何"。**

---

## 7.4 一个真实缺陷：「隔着墙找杯子」

### 现象

机器人停在客厅 `[0, −1.25]`，杯子在厨房 `[2.05, 3]`，**四米外隔一堵墙**。

计划的第一步是在客厅找杯子：

```
observe_scene → resolve_targets → …
```

检测器是几何式的，**看不见就是看不见**，`objects = 0`。

### 提交正文的根因链（值得整段引用）

> 这个故障活得久，是因为它**长得像感知问题，实际是规划问题**。
> 模型漏掉导航不是疏忽：**没有任何输入告诉它机器人在哪、杯子在哪**。
>
> **看不见世界的规划器，会为它想象出来的世界写计划。**

**最后一句是本章的核心命题。**

### 为什么这个缺陷可怕

**不在失败本身，而在每一层的行为都是正确的**：

| 层 | 行为 | 正确吗 |
| --- | --- | --- |
| `resolve_targets` | 正确地报告找不到 | ✅ |
| 闭环契约 | 正确地失败关闭 | ✅ |
| 恢复 | 正确地进入可恢复状态 | ✅ |
| **计划** | **错的** | ❌ |

**只有计划本身是错的，而错误被伪装成感知问题。**

**所以修复不是加一个检查，而是改变规划器的输入。**

### 修法：把世界状态注入规划器

修前：`observe_scene` 开头。
修后：`navigation.navigate → verify_arrival → observe_scene → resolve_targets → … → manipulation.place → verify_placement`。

**Go 50 包全绿，新增 5 个 `WorldFrom` 测试。**

---

## 7.5 `orchestration/world.go`：注入的是什么

`World`（`orchestration/world.go`）只有**三个业务字段**：

```go
type World struct {
	RobotRoom string
	Objects   []ObjectPlace{Category, Room}
	GroundedOnly bool
	StateReports []string
}
```

它的能力边界由三个方法钉住：

| 方法 | 作用 |
| --- | --- |
| `Known()`（`:52-54`） | 是否**够格**规划 |
| `Rooms(category)`（`:57-74`） | 去重 + 排序 |
| `Describe()`（`:82-139`） | 渲染成**提示词句子** |

### 三个值得写进书里的设计决定

**决定 1：世界按调用传入，不缓存在 planner 上。**

```go
// orchestration/types.go
Plan(request, intent, world)   // 三参数
```

注释：

> *a plan is only meaningful against the state it was formed from, and a planner
> that cached the robot's location would keep planning for wherever it used to be.*

**"一份计划只对它形成时的那个状态有意义；一个缓存了机器人位置的规划器，会一直为机器人曾经在的地方做计划。"**

修订路径同理（`tasks/service.go`）：

> 修订是**对着现在的世界**规划的，不是 task 创建时的世界：**机器人已经移动过了**。

**教学要点**：**这是一个 coding agent 完全不需要的设计。** 文件系统不会在你读它的时候移动。

**决定 2：提示词写的是约束带理由，不是数据字段。**

```
// orchestration/world.go
**The robot cannot see through walls.** A skill that looks for or manipulates
an object only works when the robot is in a room the object is known to be in.
```

理由写在注释里（`:134-137`）：

> **写成字段，模型会当成可选上下文；写成带理由的规则，它才改变计划。**

**教学要点**：这是一个**提示词工程**的具体发现，而且它有解释：

| 写法 | 模型的理解 |
| --- | --- |
| `robot_room: "living_room"` | **一条可选的事实** |
| "机器人不能穿墙。寻找或操作物体的技能只在机器人处于物体所在房间时有效。" | **一条必须遵守的规则** |

**"字段"和"规则"在模型那里不是同一种东西。**

**决定 3：读不出来的一律留空，绝不猜。**

```go
// orchestration/world.go（注释）
"A robot placed in a room it is not in would produce a confidently wrong plan,
 which is worse than a plan that admits it lacks the navigation it needs."
```

**实现**：离所有登记点超过 **4 米**（`roomMatchRadiusM`，`:219`）就**不认领最近的那个房间**。

**"一个自信的错误计划，比一份承认自己缺少所需导航信息的计划更糟。"**

**这与第 4 章世界模型的三值逻辑、第 6 章 `Health()` 返回 DEGRADED 是同一条原则。**

---

## 7.6 计划的结构

### 计划与步骤

```go
// core/taskgraph/model.go
type TaskPlan struct {
	ID, Goal, Domain string
	Revision int
	Steps []SkillStep
	Budget, StopPolicy
}

// :25-38
type SkillStep struct {
	ID, Skill, RobotID, BrainID string
	Arguments map[string]any
	DependsOn []string
	ExpectedOutput []string
	SafetyLevel
	ApprovalID
	DeadlineUnixMS, LeaseMS
	IdempotencyKey
}
```

### 形状校验：依赖只能指向已经出现过的步骤

```go
// model.go:51-67
// 依赖必须指向「已经出现过」的步骤
// 错误信息："depends on unknown or later step"
```

**这个约束的含义很漂亮**：**合法计划天然是拓扑序 DAG**。

于是 `compiler.Compile` 只需要做一次**线性 Kahn 扫描**并检测环（`core/compiler/compiler.go`）——**不需要先做拓扑排序**。

**教学要点**：**用一个校验规则消灭一类算法。** "依赖只能向后看"这条规则，让"拓扑排序"这件事在计划生成时就被免费完成了。

### 「stage」有两个互不相干的含义

**讲课必须分开，否则会混淆。**

**含义 A：决策环节 `Document.Stage`**（`core/agentcontext/context.go`）

取值：`goal` / `planning` / `tool_result` / `verification` / `ops` / `reflection` / `recovery` / `handoff`

由 `StageForRole()` 映射（`routing.go:19-27`），并由内置 `stage-policy.json` **逐环节选定表达格式**：

| 环节 | 格式 |
| --- | --- |
| `planning` | `nl_decision` |
| `tool_result` | `json` |
| `recovery` | `nl_sections` |
| … | … |

而 `Project()` 返回**实际用过的渲染器**，不只是全局开关（`projection.go:3-13`）。

**含义 B：路线阶段 `ManipulationRouteIndex`**（`skills/manipulation/plugin.go`）

机械臂动作**绑定在路线的第几段**。注释写明**不许退回"第一个匹配的房间"**：

> 否则**操作会被搬到另一段路线上**。

**教学要点**：同名的两个概念出现在同一份代码里，是真实存在的。**讲课时必须先分开定义，否则学生会在读到第二个时以为它是第一个的特例。**

### 前置条件落在三处，不在计划里

**计划本身没有 precondition 字段。** 前置条件落在三个地方：

| # | 位置 | 内容 |
| --- | --- | --- |
| 1 | **目录层的必需参数** | `SkillManifest.RequiredParameters`，由 `validateBundle` 逐条检查（`orchestration/llm.go`） |
| 2 | **能力与安全前置** | `RobotProfile.tools` 广告的可用能力 + `AllowedSafetyProfiles`。**没有的能力在计划阶段就被拒，不是运行时才发现** |
| 3 | **运行时预检** | `GOAL_NOT_CLEAR` / `LOCALIZATION_NOT_CLEAR` / `NO_KNOWN_PATH` **在底盘动之前**拒绝（`grid_navigation.py:137-172`） |

`SkillManifest.Validate` 还拒绝两件事：

| 拒绝 | 理由（注释原文） |
| --- | --- |
| 物理技能没有租约 / 没有安全 profile | — |
| **改世界的技能却没有副作用标记** | 调用方会被告知**无需确认**，而闭环门禁在**等一份永远不会到的证据** |

**最后一条是第 5 章讲过的那个漂亮的反例**：两个属性分别看都对，合起来自相矛盾。

### 后置条件是一个字符串，但它不是判据

```go
// tasks/revision.go
RevisionStep.RequiredPostcondition
```

由 `buildRevisionSteps` 构造（`tasks/service.go`）：

```
"<color>-<category> in <destinationCategory>[/<relation>][/<destinationId>]"

例：red-cup in storage_bin/right_side/blue-target_zone
路线任务：arrived at living_room -> home_corridor -> kitchen
```

它是一个**人类可读的、用于对账的字符串**，参与 `SemanticFingerprint` 与稳定步骤 ID 的推导（同 revision 内语义未变则沿用原 StepID、状态与证据）。

**但它不是物理完成的判据。**

**物理完成由闭环门禁决定**：`MutatesWorld` 的技能必须有**动作后的新鲜证据**才允许被记为完成。

**教学要点**：**"给人看的描述"和"机器判定的判据"是两个东西。**

这个项目的处理方式是：**描述保持人类可读（用于审批与对账），判据放在 `core/closedloop`（机器可判定）**。

如果把它们合并——比如让 `RequiredPostcondition` 字符串成为判据——你就要写一个字符串解析器来判定物理完成，而**字符串解析器会被措辞变化击穿**。

### 编排层如何决定某步能不能执行：三个独立层次

| # | 层 | 内容 | 缺了会怎样 |
| --- | --- | --- | --- |
| 1 | **依赖是否满足** | `taskgraph.GraphRuntime` 的 ready 集 | 步骤乱序执行 |
| 2 | **能力/安全/审批是否齐备** | 目录 + profile + `ApprovalID` | 一个没有能力或没批准的步骤被派发 |
| 3 | **世界现在是不是真的那样** | `core/worldmodel/predicate.go` 对着 `Snapshot` 求值，返回**带证据引用的三值结果** | **一个对旧世界成立的计划被执行** |

**第三层是机器人与 coding agent 最本质的区别所在。**

### `compiler` 与 `taskgraph` 的分工

| | `core/compiler` | `core/taskgraph.GraphRuntime` |
| --- | --- | --- |
| 时机 | **一次性静态** | **可变运行态** |
| 做什么 | 校验形状、算出一个执行顺序、环即报错 | 节点终态时刷新后继 |

`runtime.go:25-30` 写明了为什么必须分开：

> **多机器人计划里，机器人 A 的节点 N 可能要在机器人 B 的节点完成后才就绪。**

**教学要点**：**"静态可算的"和"运行期才知道的"必须分开。** 把它们合在一起，你会得到一个每次都要重新做全部校验的运行时。

---

## 7.7 编排层评测体系：「先有刻度，再谈自训」

### 设计取舍：只问一个问题

`orchestration/eval/eval.go` 的包头 + `docs/architecture/orchestration-post-training.md`：

> **只问一个问题**——"给定这句话，产出的是不是**一份会执行的计划**、以及**该不该拒绝**"。

**四条硬规则**：

**规则 1：拒绝是独立维度。**

报告**永远分两半打印**：

```
executable: a/b
refusals:   c/d
```

理由：

> **"不敢做"和"做错了"需要完全相反的修法。**

**教学要点**：这是一个**指标设计**的根本判断。

如果只报一个"准确率"，那么"把一个能做对的计划拒绝了"和"把一个该拒绝的计划执行了"会被平均成一个数字——**而它们需要完全相反的修法**。

**规则 2：运行失败 ≠ 答错。**

模型不可达是 `Error`，不是 `Refused`。退出码 `0` / `1` / `2` 区分"全过 / 有错 / 跑不起来"。

**规则 3：断言目标，不断言实现。**

**这条是被自己的 bug 教会的**（`orchestration/eval/cases.go` 保留了记录）：

> 第一版 `navigate-kitchen` 断言工具名 `navigate_route`，而计划里装的是技能 `navigation.navigate`——
> **一个正确的导航计划被判为失败。**

**教学要点**：这是一个**评测自身正确性**的问题。**一个错误的评测会给出错误的刻度，而错误的刻度比没有刻度更糟**——因为它会让团队朝错误方向优化。

**规则 4：换一个端点就是全部的接口**，供 checkpoint 准入。

### 真实基线数字

| 规划器 | 总分 | executable | refusals | 备注 |
| --- | --- | --- | --- | --- |
| `deterministic` | **6/10** | **0/4** | **6/6** | **一条都规划不出来，但该拒的全拒了** |
| `deepseek-flash` | **9/10** | **4/4** | **5/6** | 把"红色和蓝色方块"一次性规划了 **14 步**，违反产品规则"一条指令一个物体" |

**"两边的失败方向相反"** —— 这句话是第 7 章最好的开场。

| | 确定性 | 模型 |
| --- | --- | --- |
| 能做的 | **一条都规划不出来** | 4/4 全对 |
| 拒绝的 | **6/6 全对** | 5/6（**漏拒一个**） |

**确定性太保守，模型太配合。**

**这就是为什么确定性后备不能删。** 删掉语法，你就删掉了"该拒的全拒"这一半。

### 指标

```go
// orchestration/metrics.go
LLMPlanRate
LLMCandidateRate      // = 接受候选 / 尝试次数，只在 Attempts>0 的记录上算
LLMRejectionCount
EndToEndSuccessRate
SuccessByPlanSource
```

以及一个显式的合成分（`:85-87`）：

```go
metrics.OrchestrationScore  = metrics.EndToEndSuccessRate * 60
metrics.OrchestrationScore += metrics.LLMCandidateRate    * 25
metrics.OrchestrationScore += metrics.LLMPlanRate         * 15
```

文档对它的定性很克制（`docs/architecture/orchestration.md`）：

> **"分数只用于迭代，不参与物理放行"**

并且提醒：

> 不要用 fallback 率推断用户要求是否被完整保留——因为"**已知请求走确定性 Parser**"和"**Planner 是否采用 LLM**"是两个独立决定。

**教学要点**：**一个合成分的权重是产品判断的编码。** `60/25/15` 说明"端到端成功"最重要，"选到 LLM 候选"次之，"用了 LLM"最次。

而文档的两条提醒同样重要：**分数不参与物理放行**、**不要从 fallback 率反推需求保留**。

### 编排层评测体系的三步顺序

`docs/architecture/orchestration-post-training.md` 的核心论证：

> 收益在成本 / 延迟 / 格式 / 离线；**域外与歧义处理明显更差，拒绝的准确性风险最高**。
>
> 用 API 输出蒸馏会**丢掉"该拒绝时拒绝"**——所以顺序不可选：
> **先评测（已做），再蒸馏，最后才是 RL。**

**奖励设计三条硬规则**：

| # | 规则 | 理由 |
| --- | --- | --- |
| 1 | **拒绝必须能得分** | 否则模型学会"什么都答应" |
| 2 | **`UNKNOWN_OUTCOME` 既不给正分也不给负分，而是屏蔽该样本** | 结果未知的样本**不能用来训练**——因为它的标签是"不知道" |
| 3 | **闭环门禁不参与训练** | 训练只能读它的结论，**不能改它**——"反过来就是本仓库一直在防的那种自证" |

**第 2 条是本章最深刻的一条。**

**为什么"未知"要屏蔽样本，而不是给 0 分？**

因为给 0 分等于**告诉模型"这个答案是错的"**——而系统**根本不知道它对不对**。

**用一个"不知道"的标签去训练，就是在教模型猜测。**

**第 3 条与第 2 章"判卷的人不能是考生"是同一条原则。**

---

## 7.8 与通用 coding agent 的五条结构性差异

| # | coding agent 的世界 | 机器人的世界 | 代码证据 |
| --- | --- | --- | --- |
| 1 | 读文件能获取当次字节，但仍有并发、缓存、权限和外部状态问题 | 每条事实带 `Freshness` 与 `Confidence`，**陈旧即 `UNKNOWN`** | `types.go:20-27,46-57`；`predicate.go:138-140` |
| 2 | 新鲜度是**自然的**（刚读的） | 新鲜度是**算出来的**，而且曾经是假的 | 旧实现写字面量 `"FRESH"` |
| 3 | 真相在两次操作之间**不变** | 世界在**规划与执行之间**会变 | `orchestration/types.go`；`tasks/service.go` |
| 4 | 动作**可重放** | 物理动作**不可撤销，且结果可能是 `UNKNOWN`** | `UnknownOutcome` 类 |
| 5 | **路径即身份**（`/etc/hosts` 就是它） | **"房间"是给一个已委托位姿起的名字** | `world.go:172-219` |

### 差异 1 的深层含义：`ENOENT` 与 `objects=0` 不是一回事

coding agent 读不到文件，得到 `ENOENT`——一个**确定的**答案。

机器人看不到物体，得到 `objects = 0`——一个**不确定的**答案。

**把 `objects = 0` 当成"物体不存在"来推理，是机器人 agent 最危险的推理错误。**（第 4 章 §4.8 详述）

### 这五条如何改变了 planning 的设计

| 影响 | 落地 |
| --- | --- |
| **计划必须携带它赖以成立的世界** | `Plan(request, intent, world)` 三参数 |
| **计划的正确性不能只靠 schema 校验** | `validateBundle` 只能证明"技能名合法、必需参数齐全、至少有一个副作用技能"——**它证明不了这个计划在物理上可能** |
| **"不知道"必须能被表达，并且必须能拦住动作** | `GOAL_NOT_CLEAR` 在底盘动之前拒绝；`PLACEMENT_NOT_OBSERVED` 禁止重试；`UNKNOWN` 在奖励函数里被屏蔽 |
| **规划的失败会伪装成感知的失败** | 三条 grounding 根因全部表现为 `objects = 0` |

**最后一条是本章为什么要单独成章的理由**：

> **在机器人系统里，一个错误的计划会以所有下游层都正确的方式失败。**

---

## 7.9 一个仍未修掉的循环依赖

这一节是本章最诚实的一部分。

### 问题

`edge/agent/runner.go` 的执行顺序是：

```
:241  grounder.Ground(ctx, intent)     ← 先 grounding
:266  planForIntent(...)               ← 后建计划
```

而"**先导航到厨房**"是计划里的一步。

**所以：计划依赖 grounding，而 grounding 依赖"机器人已经在厨房"——而"去厨房"是计划里的一步。**

**这是循环依赖，不是接线疏忽。**

### 为什么它构成循环

```
要生成正确的计划
   ↓ 需要
知道机器人在哪、物体在哪（grounding）
   ↓ 需要
机器人已经走到能看见物体的地方
   ↓ 需要
一个包含导航步骤的计划
   ↓ 回到起点
```

### 提交正文的定性

> 提交把它定性为**循环依赖，不是接线疏忽**，真正的修法要在**执行顺序**上。

（提交正文当时记的是 `:225` / `:741`，HEAD 上已漂移到 `:241` / `:266`——引用行号时二者都要能对上。）

### 教学要点：这是本章最好的一个辩证案例

"世界状态注入"解决了"**计划不知道世界**"，但**没有**解决"**世界自己需要计划才能被观测**"。

| 问题 | 修法 | 状态 |
| --- | --- | --- |
| 计划不知道世界 | 注入世界状态 | ✅ 已修（`world.go`） |
| **世界需要计划才能被观测** | **需要重新设计执行顺序** | ❌ **未修** |

**这是一个"修复暴露了下一个更深的问题"的典型**。

而值得学的是：**作者没有把它包装成"已解决"**。提交正文明确写它是循环依赖，而不是说"我们在计划里加了导航步骤"。

---

## 7.10 教学要点

### 一道可以从评测数字下手的练习

给学生这张表：

| 规划器 | 总分 | executable | refusals |
| --- | --- | --- | --- |
| `deterministic` | 6/10 | 0/4 | **6/6** |
| `deepseek-flash` | 9/10 | **4/4** | 5/6 |

问：**如果要把总分从 9/10 提到 10/10，应该改哪一边？为什么这个"提升"可能是错的？**

<details>
<summary>答案要点</summary>

**要提到 10/10，需要让 flash 的 refusals 从 5/6 变成 6/6**——也就是让它**多拒绝一个**。

**为什么这个"提升"可能是错的**：

**"拒绝"和"执行"是两类不同的错误，而它们的代价不对称。**

| 错误 | 后果 |
| --- | --- |
| 该拒的没拒（flash 的问题） | **一个不该执行的任务被执行了**——物理后果 |
| 该执行的没执行（deterministic 的问题） | 用户重说一遍 |

**所以"总分 = 9/10"掩盖了这个不对称**：10 个用例里 4 个 executable、6 个 refusal，**等权相加**。

**正确的做法是分开看**（这正是 eval 设计的第一条规则）：

```
executable: a/b     ← 能做对的
refusals:   c/d     ← 该拒的
```

**加分点**：指出"把 5/6 提到 6/6"的具体困难——**它要求模型学会"什么时候不该做"**，而这正是文档里说的：

> **用 API 输出蒸馏会丢掉"该拒绝时拒绝"。**

**再加分**：指出奖励设计的第一条规则就是"**拒绝必须能得分**"。因为如果拒绝不得分，模型在 RL 里会学会**什么都答应**。

</details>

### 五道练习题

**题 1（后备路径为什么不能删）**

> 有人提议：既然配了模型，就把确定性解析器删掉，让模型处理所有请求，代码更简单。
> 请用评测数字反驳，并指出"确定性后备"在代码里到底指什么。

<details>
<summary>答案要点</summary>

**用数字反驳**：

```
deterministic  6/10（executable 0/4，refusals 6/6）
deepseek-flash 9/10（executable 4/4，refusals 5/6）
```

**两边的失败方向相反，而且"拒绝"是安全属性。**

- 语法**太保守**：4 个能做的都没做 → 用户重说一遍
- 模型**太配合**：6 个该拒的漏拒 1 个 → **一个不该执行的任务被执行了**

**删掉语法就删掉了"该拒的全拒"这一半。**

**"确定性后备"在代码里指两层**：

| 层 | 位置 | 内容 |
| --- | --- | --- |
| 1 | `orchestration/llm.go` | 配置不全 → `DeterministicPlanner` |
| 2 | `orchestration/llm.go` | 所有候选被拒 → 返回 `Source: deterministic`，**保留 `Attempts` 与 `Rejections`** |

**⚠️ 但真正产出步骤的不是 `DeterministicPlanner`** —— 它返回的 `Bundle` **不含 Steps**（`types.go:49-51`）。真正的确定性计划生产者是领域计划构造器 `skills/manipulation.Plan`。

**混淆这两者会得出"后备是空操作"的错误结论。**

**还有一条独立理由**：确定性解析器**零成本、无网络、不会幻觉**，是**离线与实机验收的基线**（默认 `AGENT_PROVIDER=deterministic`，演示闭环不依赖任何模型服务）。

**加分点**：指出"模型的 14 步计划"违反了产品规则"一条指令一个物体"——**这说明模型的失败不只是"漏拒"，还有"过度规划"**。

</details>

**题 2（指标定义）**

> `LLMFallbackTasks` 的定义是"来源是 deterministic **且** `Attempts > 0`"，而不是"来源是 deterministic"。
> 请说明这两个定义的差别，以及为什么第二个定义会误导。

<details>
<summary>答案要点</summary>

| 定义 | 包含什么 | 能回答什么 |
| --- | --- | --- |
| 来源是 deterministic | "试过但失败" **+ "没配模型"** | 什么都回答不了 |
| **来源是 deterministic 且 `Attempts > 0`** | **只有"试过但失败"** | **"模型在这里表现如何"** |

**为什么第二个定义会误导**：

一个**没有配模型**的部署，它的 fallback 率是 100%。而这**不是后备**——**那是配置**。

如果把两者混在一起，那么：
- 一个"配了模型但模型表现很差"的部署
- 和一个"根本没配模型"的部署

会得到**相同的 fallback 率**。于是你无法用这个指标判断"要不要换个模型"。

**一般原则**：

> **当你要度量某个机制的表现时，分母必须只包含"这个机制被使用了"的样本。**

**同类例子**：

- 缓存命中率应该只在"缓存可用"的时段计算；
- 重试成功率应该只在"确实重试了"的请求上计算；
- **故障检出率应该只在"故障真的注入过"的场景上计算**（第 10 章的故障矩阵就是这么做的）。

**加分点**：指出这是"**指标的空值处理**"问题。一个 `nil` 或"未配置"的样本，不应该被当作一个"失败"的样本计入分母。

</details>

**题 3（"先看再动"的循环）**

> `runner.go:241` 先 grounding，`:266` 才建计划；而"先导航到厨房"是计划里的一步。
> 请说明这个循环依赖的完整结构，并给出**两种**可能的修法（各自说明代价）。

<details>
<summary>答案要点</summary>

**循环结构**：

```
正确的计划 ← 需要 → 知道物体在哪（grounding）
                    ← 需要 → 机器人已走到能看见的地方
                              ← 需要 → 一个包含导航步骤的计划
                                        ← 回到起点
```

**修法 A：把 grounding 变成"先导航再 ground"的两阶段**

先 ground 一个**粗粒度**目标（房间级），执行导航，**到达后重新 ground** 细粒度目标（物体级），再生成后续计划。

**代价**：计划要在执行中途重新生成。这打破了"计划在执行前完整生成"这个前提——而第 5 章的"批准范围"机制**依赖这个前提**（批准的是动作集合，所以集合必须在批准前完整）。

**修法 B：让 grounding 支持"空间推理"**

grounding 时不仅问"现在看得见什么"，还问"**我知道哪些房间里有这类物体**"（来自地图的语义层，而不是当前视野）。

**代价**：需要地图的语义层包含"物体在哪一类房间"这个先验——而这个信息**可能过期**（物体被移动过）。于是要引入新鲜度判断。

**修法 C（可能最简单）：把 grounding 拆成两个调用**

- `GroundCoarse(intent)` → 房间级目标（用于生成导航步骤）
- `GroundFine(intent)` → 物体级目标（导航到达后调用）

**代价**：需要明确"什么时候该用哪一个"，而这个判断本身可能需要世界状态。

**加分点**：指出**这个问题的本质是"观测需要行动"**——这在机器人领域有一个名字：**主动感知**（active perception）。

**"要让机器人看见，你得先让它走过去。"** 而"走过去"需要一个计划，而计划需要"看见"。

**这是一个真实的开放问题**，不是接线疏忽。修补它需要重新设计执行顺序——也就是重新设计"计划和观测谁是前提"。

</details>

**题 4（提示词的写法）**

> `orchestration/world.go` 把"机器人不能穿墙"写成了一句带理由的规则，而不是一个数据字段（如 `robot_room: "living_room"`）。
> 注释说："写成字段，模型会当成可选上下文；写成带理由的规则，它才改变计划。"
> 请解释这个区别，并给出一个你自己的例子。

<details>
<summary>答案要点</summary>

**区别**：

| 写法 | 模型的理解 | 效果 |
| --- | --- | --- |
| `robot_room: "living_room"` | **一条可选的事实** | 模型可能用它，也可能不用 |
| "机器人不能穿墙。寻找或操作物体的技能只在机器人处于物体所在房间时有效。" | **一条必须遵守的规则** | 模型改变计划 |

**为什么**：字段是**数据**，规则是**约束**。模型对两者的处理方式不同——数据可以忽略，约束需要满足。

（推断）这与训练数据里"规则"和"数据"的分布不同有关：指令微调让模型对祈使句/约束句更敏感。

**自己的例子**（学生可能给出的）：

| 字段写法 | 规则写法 |
| --- | --- |
| `gripper: "closed"` | "如果夹爪已经夹着东西，不要再次抓取；先放置。" |
| `battery: 15` | "电量低于 20% 时，不要开始新的导航任务；先返回充电位。" |
| `map_coverage: 0.62` | "地图未覆盖的区域不可通行；如果目标在未覆盖区域，先要求补扫。" |

**加分点**：指出这个发现有一个**代价**——规则比字段长，**占 token**。所以"全部写成规则"不是免费的。

**正确的做法是按重要性分级**：真正会改变计划形状的约束写成规则，背景信息写成字段。

**再加分**：指出这与第三轮实验结果一致——**语法/格式不是关键，信息完整性才是**。所以重点不是"怎么措辞"，而是"**这条信息有没有被送到决策者面前**"。

</details>

**题 5（奖励设计）**

> 训练奖励的三条硬规则之一是："`UNKNOWN_OUTCOME` 既不给正分也不给负分，而是**屏蔽该样本**。"
> 请说明为什么"给 0 分"是错的，并指出这与项目里哪两条原则是同一条。

<details>
<summary>答案要点</summary>

**为什么给 0 分是错的**：

0 分等于**告诉模型"这个答案是错的"**。而系统**根本不知道它对不对**——那正是 `UNKNOWN_OUTCOME` 的定义。

**用一个"不知道"的标签去训练，就是在教模型猜测。**

**而猜错的代价**：模型会学会"在信息不足时给出一个看起来合理的答案"——而这**恰好是第 3 章那套设计要防的事**。

**同源的两条原则**：

1. **第 3 章的三值逻辑**：`UNKNOWN` 不等于 `FALSE`。"证据不在"和"事实不成立"是两种答案。
2. **第 6 章 `ReconciliationUnavailable`**："这个任务没有未对账步骤"和"这个部署查不出来"**必须能被区分**。

**共同点**：

> **"我不知道"必须是一个能被表达的状态，而且不能在任何地方被降级成一个确定的答案。**

**加分点**：指出这与 masked 双生实验的结果一致——**模型在被隐藏必要信息时，100% 拒答**。这说明"信息不足时拒答"是**可以被系统性做到的**，而奖励设计应该**强化**这个行为，而不是惩罚它。

**再加分**：指出另一条硬规则"**拒绝必须能得分**"是同一个道理的另一面：

- 拒绝能得分 → 模型学会在不确定时拒绝
- 拒绝不得分 → 模型学会"什么都答应"

</details>

### 学生最容易误解的四个点

| # | 误解 | 纠正 |
| --- | --- | --- |
| 1 | 以为 `orchestration.DeterministicPlanner` 就是确定性计划 | 它返回的 `Bundle` **不含 Steps**。真正产出步骤的是领域计划构造器 `skills/manipulation.Plan` |
| 2 | 以为 `resolve_targets` 在做"自然语言名词 → 实体"的解析 | 在生产路径上，实体 ID 是 **grounder 在计划生成之前**决定的 |
| 3 | 以为 `RequiredPostcondition` 字符串是完成判据 | 它是**人类可读的、用于对账的字符串**。物理完成由 `core/closedloop.Gate` 决定 |
| 4 | 以为"计划正确 = schema 校验通过" | `validateBundle` 只能证明"技能名合法、必需参数齐全、至少有一个副作用技能"——**它证明不了这个计划在物理上可能** |

---

## 7.11 本章小结

1. **12 步计划实测全部闭合**（12 条观测证据），**返程那一步因地图覆盖不足而失败**——README 明确标注"在这一版上返程确实没走通"。这是"不知道的地方不进去"，不是缺陷。

2. **三条规则的实现位置明确**：确定性优先（`agent/agent.go`）、失败不降级（`:84-91`）、规划层另有后备（`orchestration/llm.go`）。**而真正的确定性计划生产者是 `skills/manipulation.Plan`，不是 `DeterministicPlanner`。**

3. **"看不见世界的规划器，会为它想象出来的世界写计划。"** 这是本章的核心命题，也是"隔着墙找杯子"那个缺陷的根因。

4. **计划必须携带它赖以成立的世界**（`Plan(request, intent, world)` 三参数）。**这是一个 coding agent 完全不需要的设计**——文件系统不会在你读它的时候移动。

5. **评测的第一条规则是"拒绝是独立维度"**：`executable: a/b` / `refusals: c/d` 必须分开打印，因为"不敢做"和"做错了"需要**完全相反的修法**。

6. **"在机器人系统里，一个错误的计划会以所有下游层都正确的方式失败。"** 这就是为什么要单独用一章讲 planning。

---

## 7.12 源码索引

### 意图与解析

| 内容 | 位置 |
| --- | --- |
| **确定性优先** | `agent/agent.go` |
| **失败不降级** | `agent/agent.go` |
| 解析器可运行时替换 | `tasks/service.go` |

### 编排

| 内容 | 位置 |
| --- | --- |
| `orchestration.New` 的兜底 | `orchestration/llm.go` |
| 采样循环与 `Rejections` | `orchestration/llm.go` |
| 众数选择 | `orchestration/llm.go` |
| 规划整体报错 | `tasks/service.go` |
| 规划提示词的安全字段禁令 | `orchestration/llm.go` |
| `skillView` 只有五字段 | `orchestration/llm.go` |
| `validateBundle` | `orchestration/llm.go` |
| `Bundle` 不含 Steps | `orchestration/types.go` |
| `Plan` 三参数的理由 | `orchestration/types.go` |
| 指标与合成分 | `orchestration/metrics.go` |
| 评测四条规则 | `orchestration/eval/eval.go` 包头 |
| **断言目标而非实现（被自己的 bug 教会）** | `orchestration/eval/cases.go` |

### 世界注入

| 内容 | 位置 |
| --- | --- |
| `World` 三字段 | `orchestration/world.go` |
| `Known()` / `Rooms()` / `Describe()` | `orchestration/world.go` |
| **提示词写成带理由的规则** | `orchestration/world.go` |
| **读不出来就不猜** | `orchestration/world.go` |
| 房间归属与 `roomMatchRadiusM = 4.0` | `orchestration/world.go` |
| 修订对着现在的世界 | `tasks/service.go` |

### 计划结构

| 内容 | 位置 |
| --- | --- |
| `TaskPlan` / `SkillStep` | `core/taskgraph/model.go` |
| **依赖只能向后看** | `core/taskgraph/model.go` |
| `compiler.Compile` 线性扫描 | `core/compiler/compiler.go` |
| **`compiler` 与 `GraphRuntime` 的分工** | `core/taskgraph/runtime.go+` |
| `Document.Stage` 八环节 | `core/agentcontext/context.go`；`routing.go:19-27` |
| `ManipulationRouteIndex` | `skills/manipulation/plugin.go` |
| 必需参数校验 | `core/skills/manifest.go`；`orchestration/llm.go` |
| **`MutatesWorld && !SideEffect` 是硬错误** | `core/skills/manifest.go` |
| 后置条件字符串 | `tasks/revision.go`；`tasks/service.go` |
| 步骤级安全字段 | `skills/manipulation/plugin.go`；`edge/agent/runner.go` |
| 审批三字段 | `tasks/service.go` |

### 运行时预检

| 内容 | 位置 |
| --- | --- |
| 三个预检拒绝码 | `robot/gateway/tangying_robot_gateway/grid_navigation.py` |
| 为什么它们被归 `PERCEPTION` | `core/closedloop/closedloop.go` |

### 循环依赖

| 内容 | 位置 |
| --- | --- |
| 先 grounding 后建计划 | `edge/agent/runner.go` |
| 提交正文的定性 | 提交 `6f87aca04` |

### 评测与后训练

| 内容 | 位置 |
| --- | --- |
| 编排层后训练（"先有刻度，再谈自训"） | `docs/architecture/orchestration-post-training.md` |
| 真实基线数字 | 提交 `505af6577` 正文；`docs/architecture/orchestration-post-training.md` |
| Agent 上下文评测（三轮） | `docs/experiments/2026-09-21-agent-context-evaluation.md` |
| 字段字典 | `docs/development/decision-context-fields.md` |
| 训练门禁 | `train/`；`docs/operations/deployment.md` |

---

**上一章**：[第 6 章 多 Agent 运行时](../chapters/ch06-multi-agent-runtime.md) · **下一章**：[第 8 章 分布式不是部署姿势，是故障假设](../chapters/ch08-distributed-fault-assumptions.md) —— 租约、fencing、Outbox，以及六个拒绝点。
