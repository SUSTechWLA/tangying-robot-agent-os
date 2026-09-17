# 恢复 Agent（RecoveryAgent）：调查、提议、转人工

本文是恢复 Agent 的设计文档，也是**读懂一份恢复计划的说明书**。它回答三个问题：

1. 系统发现异常后，是谁在定位问题、依据是什么；
2. 为什么它**只提议、不执行**，以及这条边界是**结构化保证**而不是口头约定；
3. 一份计划里的每个字段、轨迹里的每一步，分别从哪里来、能不能核对。

配套阅读：

- [multi-agent-runtime.md](./multi-agent-runtime.md) —— 三个 Agent 如何被托管、事件如何路由；
- [agent-events.md](./agent-events.md) —— 事件主题与载荷形状；
- [adding-an-agent.md](../development/adding-an-agent.md) —— 再加一个 Agent 要动哪些文件（答案是：只加文件、只加配置）；
- [2026-09-17-mute-observer-and-plan-identity.md](../development/2026-09-17-mute-observer-and-plan-identity.md) —— 本文机制背后的四个真实缺陷与修复过程。

---

## 1. 为什么需要它：定死的重试策略解决不了真实故障

早期的运维动作是一条固定规则：失败 → 判断是否可重试 → 重试。这条规则有两个绕不过去的问题：

- **很多故障重试根本解决不了**。地图未加载、标定漂移、存储不可达、结果未知——重试只会把同一个失败再制造一遍，还会在物理世界里多动一次。
- **重试只是手段之一**。真正要做的是先定位"到底哪里坏了"，再从可选动作里挑一个合适的；有时正确的答案是"这个我修不了，叫人"。

所以恢复能力被拆成两层：

| 层 | 职责 | 实现 |
| --- | --- | --- |
| 观察层（OpsAgent） | 发现异常、给出失败分类与建议 | 确定性规则，只读 |
| 恢复层（RecoveryAgent） | 定位问题、从目录里挑选动作、给出**待批准**的计划 | 确定性匹配 + 可选模型 |

关键设计决定：**恢复层不是"重试器"，而是"调查 + 提议器"**。它的产物是一份带证据链的计划，不是一个被执行的动作。

## 2. 只提议，不执行：一条结构化边界

`RecoveryAgent` 不持有任何能改变世界的端口。它的字段只有这几类：

| 字段 | 作用 | 为什么不是执行能力 |
| --- | --- | --- |
| `Catalog` | 可选动作目录 | 纯数据 |
| `ReadFacts` | 读任务的持久化事实 | 读 |
| `Model` | 可选的规划模型 | 只返回计划，返回值不是句柄 |
| `Publish` | 发布事件 | 由运行时注入，见 §6 |
| `RememberPlan` | 把计划存进告警存储 | 只写内存中的展示态 |

这条边界由测试守着：`TestRecoveryAgentHoldsNoExecutionPort` 用反射遍历结构体字段，任何新字段都必须在白名单里登记并写明理由。加一个 `RobotClient` 字段会让它失败——这是有意的，因为"恢复 Agent 突然能动机器人了"这件事必须是一次显式的、被评审的改动。

由此得到一条可以直接对用户承诺的性质：**在这一版里，恢复 Agent 不可能自己动手。**

## 3. 动作目录：所有"能做的事"都在这里

`DefaultRecoveryCatalog()` 定义了 16 条动作，每条包含 `ID / Summary / Risk / Service / Shapes / Refusal`。风险等级只有三档：

| 风险等级 | 含义 | 是否需要人工批准 |
| --- | --- | --- |
| `read_only` | 只读：查地图、查执行记录、读标定 | 否 |
| `bounded_write` | 有界写：加载已保存地图、重定位、重试单步 | **是** |
| `never_automatic` | 永不由系统自动执行 | 是（且当前版本没有执行路径） |

三条 `never_automatic` 动作是**按名字拒绝**的，并且拒绝理由就是目录里写的那句话：

- `estop.release` —— 复位急停要人确认现场安全；
- `hardware.replug` —— 需要人到现场插拔；
- `calibration.change` —— 改标定要人重新测量。

**"批准"是动作的属性，不是提议者的属性**：`RequiresApproval` 由 `RiskClass` 决定，不由"谁提议的""把握多大"决定。这样即便将来接入模型，也不会出现"模型很自信所以自动执行"的路径。

### 3.1 拒绝会否决整份计划

如果规划器（尤其是模型）给出了目录里没有的动作，或者给了一条 `never_automatic` 的动作，处理方式是：

```text
refusals 非空 → 整份计划作废，转人工，payload.Steps = nil
```

不做"部分执行"。理由是：提议者既然对"什么允许做"的理解是错的，它剩下那部分推理也不值得执行。被拒绝的动作仍然留在轨迹里，所以复核者能看见"它想做什么、为什么被拦下"。

## 4. 一条压倒一切规则：先对账

闭环契约里最硬的一条是 **结果未知禁止自动重试**。它在恢复层被实现为最高优先级的规则：

```text
若 存在结果未知的物理步骤
或 该发现被标记为禁止自动重试
或 无法确认是否存在结果未知的物理步骤
    → 一律转人工，Steps 为空
```

第三个条件是本文档最值得记住的一条，它修的是一个**静默漏洞**：

> "这个任务没有结果未知的步骤"和"这个部署查不出来"曾经产生同一个结果——空列表。
> 于是执行存储不可达时，调查看起来像"世界状态已确认"，恢复计划照常给出。

现在 `RecoveryFacts` 用两个字段把三种答案分开：

| 字段 | 含义 |
| --- | --- |
| `UncertainSteps` | 确实存在结果未知的物理步骤 |
| `ReconciliationUnavailable` | **问不出来**：存储不支持按任务列出步骤，或读取失败 |
| `ReconciliationError` | 问不出来的原因，写进转人工理由 |

代价是"停下来"，收益是"不会在一个可能已经动过的世界上再动一次"。这个方向上的取舍没有例外。

## 5. 调查轨迹：把定位过程摊开给人看

一份计划的价值取决于它能不能被复核。所以每次调查都会记录一串**结构化**步骤（`TrailStep`），并随计划一起发布、一起落库、一起回放。步骤类型：

| `kind` | 含义 | 例 |
| --- | --- | --- |
| `query` | 读了一个数据存储 | `tasks.read`、`step_runs.read`、`telemetry.read` |
| `rule` | 确定性规则跑了一遍 | `rule.reconcile-first` |
| `model` | 调了一次模型 | `planner` |
| `decision` | 依据前面的事实做选择 | `catalog.read`、`recovery.decide` |
| `action` | 提交动作（本版本不会出现） | — |
| `verification` | 事后复验（本版本不会出现） | — |

每一步都带 `Source`（`kind / name / table / query`），所以控制台能直接回答**"查了哪个库、哪张表、什么条件、返回多少行"**：

```text
1. [decision] recovery.start     开始调查：<观察层给出的发现原文>
2. [decision] catalog.read       读取恢复动作目录：13 条可提议，3 条永不自动
3. [query]    tasks.read         来源=sqlite/tasks        query: id = task-xxx       rows=1
4. [query]    step_runs.read     来源=sqlite/step_runs    query: task_id = task-xxx  rows=7
5. [query]    telemetry.read     来源=runtime/telemetry snapshot                     rows=1
6. [rule]     rule.reconcile-first  检出结果未知的物理动作；对账前不执行任何恢复动作
7. [decision] recovery.decide    结论：无法自动恢复，转人工
```

两条刻意的设计：

- **失败的读取也记录**，并带 `Error`。只展示成功步骤的调查会比它实际知道的更确定。
- **目录在决策之前记录**。一份计划只能对照"当时存在的选项"来评审，事后目录变了不影响历史计划的解释。

对应前端渲染：`web/app.js` 的 `renderRecovery` 会把计划、步骤、被拒绝的动作、以及这条轨迹一起画出来；控制台接口是 `GET /v1/agent/alerts`（见 [api-reference.md](../production/api-reference.md)）。

## 6. 发布通道由运行时注入，不由接线代码记住

`Publish` 不是 `Agent` 接口的一部分，而是一个**可选能力** `agentcontract.Publisher`：

```go
type Publisher interface {
    SetPublish(publish func(ctx context.Context, event Event))
}
```

`Orchestrator.Start` 会遍历本次启用的 Agent，凡是实现了这个接口的，一律把事件总线交给它。这样做是因为一次真实事故：接线代码只给执行 Agent 手工装了发布通道，观察 Agent 没有装，于是**它发现的每一个问题都进了告警存储、却从未上总线**——恢复 Agent 永远等不到触发，而控制台照样列着发现、每个 Agent 照样报健康。

单元测试全绿，因为每个测试都自己注入了 `Publish`：它们验证的是一套生产环境并不存在的接线。把注入收进运行时之后，"一个哑掉的 Agent"在结构上不可能被漏掉；`Orchestrator.Publishers()` 也把结果打进启动日志：

```text
agent runtime started: enabled=[task ops recovery] publishers=[task ops recovery] disabled=[]
```

日志里少一个名字，就是少一条链路，而不是"它没什么可说的"。

## 7. 发现的身份：`code@component`

触发器的身份规则只写在一处（`agentcontract.AnomalyIdentity`）：

```text
identity = code + "@" + component
```

同一种失败发生在不同组件上，是**两个问题、两份计划**。这一点曾经做错：告警存储按 `code@component` 建索引，恢复 Agent 却按 `code` 归档计划，控制台又按 `code` 去查——结果是 11 个不同的组件失败全部显示第一份计划，而且那份计划的诊断里写着另一个组件的名字。没有任何一处崩溃，每一处单独看都自洽，错的只是它们的组合。

配套规则：

- `AnomalyReportID` = 身份 + `#<数量>`，用于**上报去重**：稳定状态不刷屏，恶化的状态必须重新播报；
- `SameAnomaly(reportID, identity)` 负责跨数量后缀匹配，只在 `#` 边界上比较，所以 `CODE@a` 不会匹配到 `CODE@ab`；
- 计划只会匹配到**同一任务**的告警，且只会匹配到**自己那份身份**。台账是只追加的，旧版本按裸 code 写的计划仍在回放里——它们**故意不再被匹配**，因为"借来的计划"比"没有计划"更糟：它读起来像一个答案。

## 8. 一份计划包含什么

`GET /v1/agent/alerts` 返回的 `recovery` 字段：

| 字段 | 说明 |
| --- | --- |
| `planId` | 计划标识，由轨迹 ID 派生，可回溯到具体一次调查 |
| `verdict` | `PLAN`（有可提议步骤）或 `ESCALATE`（转人工，`"我修不了"` 是一等结论） |
| `diagnosis` | 一句话诊断，优先取事实推出的结论，没有就用发现原文 |
| `confidence` | 由产生结论的规则给定，不是估出来的 |
| `source` | `deterministic` 或 `deterministic+model` |
| `steps[]` | 每一步带 `action / risk / requiresApproval / why` |
| `escalateReason` | 转人工时告诉操作员**去修什么**（例如"恢复执行记录的读取能力后重新调查"） |
| `refused[]` | 系统永远不会自动做的动作及其理由 |
| `investigation` | §5 的轨迹 |

`verdict` 的判定顺序：

```text
有 refusal            → ESCALATE（整份作废）
规划器主动要求转人工   → ESCALATE（原样保留它的理由）
校验后没有任何步骤     → ESCALATE（"没有找到可执行的恢复动作"）
否则                  → PLAN
```

## 9. 执行通道（已接通控制台）

恢复计划以前只被显示，没人执行。现在有了执行器 `internal/recoveryexec`，
以及控制台入口 `POST /v1/recovery/execute`。

**批准后执行一条动作**：

```
恢复 Agent 提议 → 控制台展示 → 人批准某一步 → 按目录声明的工具执行 → 复验 → 记录
```

**四条检查，顺序就是安全论证**：

1. **目录决定这个动作能不能跑**：`Executable()` 只放行 `read_only` 与 `bounded_write`。
   （这里修过一个**默认放行**的缺陷：原实现是 `Risk != never_automatic`，于是一个**没有声明风险等级**的新条目会被判为可执行——而"要不要问人"恰好由那个字段决定。现在改为正向列举。）
2. **声明的工具必须真的存在**：缺哪个工具就报哪个，而不是发出去让远端失败。
3. **风险决定要不要问人**：只读不问；`bounded_write` 必须批准；没有配置批准入口时**不执行**（"没法问"不等于"可以"）。
4. **执行范围 = 该动作声明的工具**：批准的是"加载一张已保存的地图"，不是"模型认为有帮助的任何事"。

**两种批准来源被分别记录**，因为它们不是同一件事：

| 来源 | 轨迹名 | 含义 |
| --- | --- | --- |
| 操作者点了按钮 | `approval.operator` | 一个人做了决定，接口本身即批准动作 |
| `Approver` 端口 | `approval.granted` | 由上游策略/人以外的通道批准 |

区分它们是因为"有人能批准"和"这次确实有人批准了"在事后追溯里是两句话。

**不硬编码**：动作 → 工具的映射**声明在目录里**（`RecoveryAction.Tools`），不是执行器里的 `switch`。
多工具动作（如 `map.re-survey` 声明了 5 个）**由模型决定用哪些、按什么顺序**；本包不知道正确顺序，这正是重点。
机器人的服务不由本包枚举：工具注册表从 `RobotServices()` 建（`telemetry.read`、`mapping.activate`、
`task.resume` 等），本机动作由 `LocalTools` 提供（重连、续跑、安全位姿、读遥测/历史）。

**复验不由动作自述**：`Verify` 端口重新读事实。没有配置复验时，结果是"已执行但**未确认**"，而不是"已恢复"。
（一次调用都没发生时连复验都不做——见下。）

### `executed` 的含义是"真的下发过调用"

`Result.Executed` 取自 `actionloop.Outcome.Calls`，而 `Calls` 只在**唯一一处真正下发调用的地方**自增。
"循环返回了"和"调用出去了"是两件不同的事，本包不自己从 verdict 字符串推断——
那会把 `actionloop` 的派发规则抄第二遍，两份必然漂移。

这条区分的代价曾经是真的：改前 `Executed` 在循环返回后无条件为 `true`，而未配决策器时循环以 `BLOCKED`
收尾、一次调用都没有，于是**一个什么都没做的运行被报成 `executed: true, verified: true`**，
控制台显示"已执行并复验通过"。**没有执行就不复验**，因为一个从未被触碰过的状态被复验器判为"通过"
是本包能产生的最坏输出。

**已在真机接口上按过**（端口 8895/8897，未配模型）：

| 动作 | 结果 |
| --- | --- |
| `estop.release` | `409 RECOVERY_ACTION_REFUSED` + 目录自己的拒绝理由 |
| `robot.take_over` | `404 RECOVERY_ACTION_UNKNOWN` |
| `observe.re-read` | `executed=false`，轨迹 `tools.resolved → not-executed → verification.not-applicable`，并列出候选 `["telemetry.read"]` |
| `map.activate` | 轨迹含 `approval.operator`，未配决策器故未执行 |

最后一条正是设计要的样子：**没有模型时它不会自己编造一次调用**，而是把"缺少决策器"如实记进轨迹。

## 9.1 仍然不做的事（诚实清单）

- **`Verify` 在生产组合根里是空的**。所以现在每次执行都报"未确认"。这是**故意留的**：
  一个读不懂效果的复验器比没有复验器更糟，因为它会把"没看出问题"说成"已恢复"。要接就得先定义
  每个动作的"好"长什么样。
- **执行结果还没有写进任务账本**。回放里看不到"某次恢复被执行过"。
- **模型默认关闭**。`Model` 字段存在且已接线（`RecoveryPlanner` 接口），但生产默认不装。装了之后它**也只能从目录里选动作**，选错照样被拒绝。
- **不跨机器人**。`edge-worker` 有云端事件上报但没有本地任务服务，机队级监督需要新的云端 RPC。

这几项写在文档里而不是留在代码注释里，是因为"系统能自己恢复"和"系统能提议恢复"是两句不同的话，使用者必须能分清自己拿到的是哪一句。

## 10. 相关实现文件

| 文件 | 内容 |
| --- | --- |
| `core/agentcontract/publisher.go` | 可选发布能力接口 |
| `core/agentcontract/anomaly.go` | 发现身份规则（唯一实现） |
| `agentruntime/recoveryagent.go` | 恢复 Agent：事实读取、规则、校验、发布 |
| `agentruntime/recoverycatalog.go` | 动作目录、风险分级、动作 → 工具声明 |
| `agentruntime/trail.go` | 调查轨迹的数据结构与编码 |
| `agentruntime/runnermemory.go` | 告警存储与计划存储 |
| `tasks/alerts.go` | 告警投影：按身份 + 任务匹配计划 |
| `console/alerts.go` | `GET /v1/agent/alerts` |
| `console/recovery_execute.go` | `POST /v1/recovery/execute` |
| `internal/recoveryexec/executor.go` | 执行器：目录 → 工具 → 批准 → 执行 → 复验 |
| `internal/recoveryexec/tools.go` | 工具注册表：机器人服务 + 本机动作 |
| `internal/actionloop/loop.go` | 受门控的有界决策循环 |
| `internal/actionloop/llmdecider.go` | 模型决策器（OpenAI 兼容工具调用） |
| `cmd/local-agent/agentruntime.go` | 组合根：事实读取器、触发订阅、注册 |
| `cmd/local-agent/main.go` | 组合根：执行器接线 |
| `web/app.js` | 计划 / 轨迹 / 被拒动作的渲染 |
