# 监督 Agent：故障矩阵与可回溯验证

这份文档回答一个问题：**任务执行中出问题，监督 agent 到底有没有用。**

> 想知道它**怎么工作**、边界在哪，看[Review Agent 运行原理](review-agent.md)；
> 想知道这些结论是**怎么被发现的**（含走过的弯路），看[监督能力的三个盲区](../development/2026-09-17-supervision-blind-spots.md)。

`agentruntime` 里的 `OpsAgent` 就是监督 agent（"ReviewAgent"）：它只读地观察执行过程，检出异常、给出分类和可执行建议，并把结论写进任务回放。**它不修改任务状态，也不动机器人**——这一点的结构性与测试性保证见[多 Agent 运行时](multi-agent-runtime.md)。

本文的每一条结论都有对应测试。测试名写在每个结论后面，可以逐个跑。

---

## 1. 故障矩阵：14 个场景逐一验证

`agentruntime/faultmatrix_test.go`。每个场景驱动**真实 OpsAgent**，断言三件事：

1. **检出了**（而不是沉默）
2. **分类正确**（复用既有闭环分类器，不是第二套）
3. **给了可执行建议**（这一条以前从没测过）

第 3 条是重点：只报"出错了"的 finding 是一行日志——它告诉操作员一件他本来就能看到的事，而没告诉他该做什么。

### 七类失败分类

| 场景 | 注入的故障码 | 期望分类 | 严重度 | 禁止自动重试 | 建议含 |
| --- | --- | --- | --- | --- | --- |
| 桥接不可用 | `NAV_BRIDGE_UNAVAILABLE` | `TRANSIENT` | warning | 否 | "可重试" |
| 目标不在原处 | `OBJECT_NOT_FOUND` | `PERCEPTION` | warning | 否 | "重新观测"、"搜索" |
| 目的地不可达 | `TARGET_UNREACHABLE` | `PLANNING` | warning | 否 | "新的目标"、"路径" |
| 需要审批 | `APPROVAL_REQUIRED` | `PERMISSION` | warning | 否 | "审批"、"租约"、"资源" |
| 对象被别人持有 | `FENCING_TOKEN_STALE` | `RESOURCE` | warning | 否 | "审批"、"租约"、"资源" |
| 命令本身被拒 | `TOOL_PARAMETERS_INVALID` | `VALIDATION` | warning | 否 | "参数"、"版本"、"重放" |
| **传输中断** | `EXECUTION_OUTCOME_UNKNOWN` | **`UNKNOWN_OUTCOME`** | **critical** | **是** | **"不要自动重试"、"对账"** |
| 动作后校验失败 | `VERIFICATION_FAILED` | `FATAL` | warning | 否 | "人工" |

`UNKNOWN_OUTCOME` 那一行是整套系统最重的一条：**机器人可能已经动了，而没有人知道**。它的建议是唯一一条带禁令的。

### 非错误码类故障

| 场景 | 触发方式 | 检出码 | 严重度 | 建议含 |
| --- | --- | --- | --- | --- |
| 机器人报模块阻塞 | `robot.faults.v1` 里 `severity=blocked` | `ANOMALY_COMPONENT_FAULT` | critical | "处置"、机器人自己的操作指令 |
| 急停锁存 | `EmergencyStopped` / `EMERGENCY_STOP_LATCHED` | `ANOMALY_SAFETY_STOP` | critical | "人工复位"、"急停" |
| **物理步骤下发未确认** | 执行记录里 `STARTED` | `ANOMALY_UNVERIFIED_MUTATION` | **critical** | "对账"、"观测" |
| 步骤阶段超预算 | 90 秒 vs 30 秒预算 | `ANOMALY_STEP_LATENCY` | warning | "设备"、"时延" |
| 观测过期 | 120 秒前的观测 | `ANOMALY_TELEMETRY_STALE` | warning | "遥测"、"连接" |
| 完全没有观测 | 拿不到 telemetry | `ANOMALY_TELEMETRY_STALE` | warning | "遥测" |
| 任务异常结束 | 持久记录里状态需人工 | `ANOMALY_ABNORMAL_TASK` | warning | "执行记录"、"对账" |

另外两条：**重复失败升级**（同一动作失败 3 次 → critical，因为"自动处理已经没起作用了"）、**健康时完全沉默**（不能有噪音）。

### 矩阵发现并修掉的一个真问题

"观测过期"那条有建议，"完全没有观测"那条**没有**——同一类问题的两个分支行为不一致。一个只有 `MissingEvidence`（缺什么）而没有 `RecommendedActions`（怎么办）的 finding，等于把"You're on your own"交给了操作员。

修的是代码不是测试：两条分支现在都给同一句建议，因为成因相同。

---

## 2. 可回溯：从故障到回放的完整链条

`agentruntime/bridge_test.go` 的 `TestFaultDiagnosisReachesTheReplayWithAdvice` 走完整条链：

```
故障发生（执行记录里一个 STARTED 的物理步骤）
   ↓  监督 agent 读持久记录
诊断（ANOMALY_UNVERIFIED_MUTATION，critical，禁止重试）
   ↓  发布事件
落账本（同一份按任务的 TaskEvent 流，以 topic 为事件类型）
   ↓  回放投影
前端"系统观察与诊断"（显示代码、严重度、建议动作、禁止重试横幅、证据）
```

四件事在回放里都能看到：

| 字段 | 内容 | 为什么要有 |
| --- | --- | --- |
| `agent` | `ops` | 谁说的，与执行 agent 区分开 |
| `severity` | `critical` | 一眼排序 |
| `recommendedActions` | 2 条具体动作 | **可执行** |
| `automaticRetryForbidden` | `true` | 唯一一条不能被软化的建议 |
| `confidence` | `1` | 确定性规则知道自己知道多少 |
| `evidenceIds` / `evidenceChain` | 步骤引用 + 规则引用 | 结论可以被核对 |

同一次故障产生 4 个事件：`ops.anomaly_detected`（看到了什么）、`ops.root_cause_hypothesis`（意味着什么 + 建议）、`ops.recovery_proposed`（advisory + 需批准）、`ops.escalation_required`（critical 才发）。

**这个分工是刻意的**：anomaly 说事实，hypothesis 说结论。合成一个事件，读者就分不清哪句是观测、哪句是推断。

---

## 3. 一个真实盲区，以及它是怎么被修的

**发现方式**：先写测试证明盲区存在，而不是先断言它有。

```
=== RUN   TestObserverMissesAnUnconfirmedStepItWasNeverToldAbout
blind spot confirmed: 1 unconfirmed step(s) on disk, none reported
```

**问题**：`OpsAgent` 只订阅未来事件，任务集合只由 `OnEvent` 填充。**进程重启后，崩溃前发生的失败它完全看不见**——磁盘上躺着"可能已经动了但没人知道"的物理步骤，监督 agent 报告一个干净、安静的机器人。

这是监督者最糟的失效模式：**沉默被读成健康**。

而"重启后还活着的失败"恰恰是最值得复核的那一批。

### 修复的三层

1. **`agentcontract.TaskHistory`**（新 port，只读：`TaskIDs` + `Abnormal`）。放在 contract 层是因为"读持久记录"是 agent 的一部分，不是某个宿主提供的。
2. **`OpsAgent.History` + 启动扫描**（`learnExistingTasks`）。只跑一次：持久记录的变化也会以事件到达，每 tick 重读是为学不到的东西做一次查询。
3. **`Finding.TaskID`**（关键的一层）。这是修第一版时**漏掉的**：诊断确实产出了，但 `latestTask` 为空（重启后没收到任何事件），于是事件的 `taskID` 是空的——**落不进按任务的账本**。

第 3 层是在端到端测试里暴露的。当时看到的现象是"诊断出来了但账本里没有"，`taskID=""` 是根因。如果只写单元测试，这个洞会留到生产。

### `Abnormal` 的定义（刻意收窄）

```
FAILED / RECOVERABLE_FAILURE / FAILED_SAFE / SAFETY_STOPPED / WAITING_USER / BLOCKED
```

**不包括 `CANCELLED` 和 `PAUSED`**：前者是操作员自己的决定，后者是例行等待。把它们算成异常会让监督者变吵，而吵的监督者没人看。`cmd/local-agent/agentruntime_test.go` 把两个方向都钉住了。

---

## 4. 另一个真问题：情况变糟时反而沉默

异常上报有 60 秒冷却（防止每 tick 重报同一件事——仓库的故障台账踩过这个坑）。但异常身份原来是 `code@component`，所以：

> "1 个任务异常结束" 和 "5 个任务异常结束" 是**同一个身份**，在冷却窗口内第二次被抑制。

而"数量变了"正是操作员最需要听到的时刻。

修复：身份在带计数时包含计数（`ANOMALY_ABNORMAL_TASK@task#5`）。不带计数的 finding 保持原身份，所以稳定条件的冷却照常工作。测试：`TestWorseningSituationIsReportedAgain`。

---

## 5. 实测：真实 sim 上跑通的完整闭环

上面是测试证据。这一节是**在一个真实运行的 sim 上**（不是 mock）跑出来的实测结果。目的是回答一个测试回答不了的问题:**这套东西接上真东西以后还成立吗。**

### 实测过程与结果

| 阶段 | 操作 | 结果 |
| --- | --- | --- |
| 1 健康基线 | agent 连真实 sim | `activeCount: 0`,`observing: true` — 健康时静默 |
| 2 真实故障 | 无需注入，sim 重启后地图未启用 | agent 报 `ANOMALY_COMPONENT_FAULT`(critical):「chassis 报告 NAV_MAP_NOT_READY：no active map is loaded」,建议「先完成巡检建图并在控制台启用地图」 |
| 3 断联注入 | `kill` 掉机器人运行时 | **约 26 秒**内报 `ANOMALY_TELEMETRY_STALE` |
| 4 重连 | 用同一端口重启运行时 | **客户端自动重连,无需人工干预** |
| 5 撤回 | 遥测恢复流动 | 同一告警变为 `active: false` — **自动撤回** |

### 三条独立交叉验证

实测的价值在于它不是"agent 说了什么",而是**agent 说的和另一处事实对得上**:

1. **`NAV_MAP_NOT_READY` 被独立证实。** agent 的结论是「no active map is loaded」;查导航接口得到 `"ready": false, "gridUnavailable": true` — 同一个事实,两个来源。
2. **遥测确实恢复了。** 不是靠"告警消失了"推断,而是遥测历史条数在持续增长。
3. **日志与告警一致。** 断联时日志出现 `telemetry observer: rpc error: code = Unavailable desc = error reading from server: EOF`;重连后**不再有新错误**,而告警同时转为 resolved。

### 一个被证伪的担心

我先前怀疑「运行时重启后客户端不会重连」——**实测证明这个担心是错的**。`grpc.NewClient` 是惰性连接,会自动恢复。第 4 步就是这个担心的反证。

（我之所以会怀疑,是因为早期一次验证里遥测看起来一直不恢复。真正原因是那次我为了注入故障而杀掉了 sim,但**没有重启它**,所以"不恢复"是正确行为。教训写在下面。）

### 一个未解释的观察

sim 的观测时间戳**滞后于墙钟约两分钟**,且滞后在缓慢增长(实测 16:18:41 时 `latest=16:16:38`)。遥测仍被判定为新鲜(告警已撤回),所以**不构成误报**。但原因我没有查明:可能是仿真非实时推进,也可能是仿真的时钟语义与 Agent 的墙钟不同。

**这不是 bug 的证据,是一个待解释的观察。** 如果仿真的 `observedAt` 语义与真实机器人不同,那么"新鲜度"这条规则在仿真里和实机上验证的就不是同一件事——值得单独确认。

### 故障注入器：`scripts/inject_faults.py`

上面是手工做的一次。为了能反复做,故障注入被写成脚本:

```bash
.venv/bin/python scripts/inject_faults.py --runtime 127.0.0.1:50199 \
    --console http://127.0.0.1:8890 --all
```

退出码：`0` 全部定位并给出建议,`1` 有故障被遗漏,`2` 连不上,`3` **所有场景的故障当前都不存在**(说明注入没生效,而不是"都通过了")。

实测输出：

```
[N/A ] calibration: 工位高度与标定不一致
        结论: 当前不存在此故障（无法注入，运行时未上报）
[OK  ] estop: 急停锁存：机器人被停止，软件不会自动复位
        定位: 机器人处于急停状态，所有依赖运动的能力都已不可用
        建议: 确认现场安全后，由人工复位急停按钮
[OK  ] no_map: 移动场景没有启用地图：导航能力被摘掉
        定位: chassis 报告 NAV_MAP_NOT_READY：no active map is loaded
        建议: 按机器人给出的处置方式处理：还没有启用的地图：先完成巡检建图并在控制台启用地图。
```

**三条如实记录的限制**：

1. **运行时只会证明它能证明的故障。** sim 只产生三个(`EMERGENCY_STOP_LATCHED`、`WORKCELL_CALIBRATION_MISMATCH`、`NAV_MAP_NOT_READY`),驱动脚本不会伪造第四个——伪造的注入证明不了任何事。
2. **标定故障无法从外部注入。** 它的触发条件是工位实测高度与标定相差 > 15 mm,由运行时自己判定。脚本把它标为 `N/A`(当前不存在),而**不是**算成"agent 漏检"——没有东西可找,报告成漏检就是错的。要覆盖它需要在场景里把工位高度改错,那是另一件事。
3. **急停没有解除 RPC。** 急停只能人工复位(这正是设计),所以脚本跑完 estop 场景后**无法清理**,必须重启运行时。这是"软件不代按急停"的直接后果,值得在使用注入器时知道。

### 第二个检出盲区：只报"任务失败"，不报"哪一步失败"

实测蓝色瓶子任务（`tabletop` 场景，瓶子够不到）时发现:任务真的失败了(`GRASP_NOT_REACHED`,37 事件),但 OpsAgent **只报了「有任务异常结束」**。

「有任务异常结束」说"出事了",而操作员需要的是"`manipulation.pick` 抓取失败,因为够不到"。后者当时根本不可达——失败动作只从**实时事件**累积,而这个任务的事件在查询前就已落盘。

**修复**：启动扫描不只知道"有哪些任务",还从每个任务的账本里读回工具失败,并**走与实时路径同一个累积函数**(`recordFailedAction`)。两条路径共用一份定义,否则"什么算失败动作"会有两个版本。

修复后同一个任务：

| | 修复前 | 修复后 |
| --- | --- | --- |
| 定位 | 只有「有任务异常结束」 | `manipulation.pick 失败：GRASP_NOT_REACHED（PERCEPTION）` |
| 建议 | 「核对执行记录」 | 「重新观测或搜索目标后再尝试」 |

### 第三个问题：一个真实故障码不在分类表里

修上面那条时暴露出更深的问题。`GRASP_NOT_REACHED` 是**运行时自己产生**的码(抓手够不到目标),但它**不在 `core/closedloop` 的分类表里**,于是分类器兜底到 `UnknownOutcome`——**把一个已知、可解释的失败报成"结果未知,禁止自动重试"**。

后果不是"报错了",而是**给出了错误的处置**:抓取够不到应该"重新观测后重试",系统却说"不要重试,先对账"。

已把它按 `PERCEPTION` 归入表中(够不到目标 → 需要先重新观测)。修复后同一个任务得到 `（PERCEPTION）` 与「重新观测或搜索目标后再尝试」。

**这一类问题值得单独注意**:分类表的完整性没有守卫。任何运行时新增的故障码,如果忘了加进表里,都会静默地降级成"结果未知"——而"结果未知"是最保守、也最阻碍恢复的那一类。修复前已有 `TestClassifyAssignsTheExpectedClassForRealRuntimeCodes` 覆盖表内代码,但**没有测试断言"运行时会产生的码都在表里"**。

### 完整分工闭环实测：一个完成、一个处理

用 `tabletop` 场景(不需要地图,任务真的能跑完)验证了两个 agent 各司其职:

| 场景 | TaskAgent | OpsAgent |
| --- | --- | --- |
| **正常任务** | 「把红色杯子放进右侧收纳盒」→ `SUCCEEDED`,62 事件,21 次工具调用,最后 `verify_placement CONFIRMED` | **0 条告警** — 健康时静默 |
| **执行中机器人掉线** | 同一条自然语言请求 → 能力检查时连接中断 → `RECOVERABLE_FAILURE` | 报 `ANOMALY_ABNORMAL_TASK`「有任务异常结束,需要核对执行记录确认实际到哪一步」+ 2 条建议;同时报 `ANOMALY_TELEMETRY_STALE` |

正常任务的回放里是 **33 条 TaskAgent 事件、0 条 OpsAgent 事件**——没有异常就没有诊断,这是对的。掉线那条则同时有两类发现:**任务异常结束**(要人去核对执行记录)和**机器人观测过期**(要人去查连接)。

值得注意:第二条告警来自我上一轮加的**任务级异常规则**。它证明那条规则在真实运行中会触发——如果只靠启动扫描,这个失败要等到进程重启才会被报告。

### 一条教训（给我自己的）

注入故障时,**必须同时确认自己有能力恢复**。我第一次做断联实验时杀掉了正在被使用的 sim,却没有确认它的启动参数和恢复路径,结果是:机器人被留在地图未启用的状态,而那个正在运行的控制台失去遥测。

正确的做法是**在独立端口上做破坏性实验**(`--listen 127.0.0.1:50199`),这一轮的后半段就是这么做的。

## 6. 怎么自己验证

```bash
# 故障矩阵（14 个场景）
go test -run TestFaultMatrix -v ./agentruntime/

# 端到端：故障 → 诊断 → 账本 → 回放
go test -run TestFaultDiagnosisReachesTheReplay -v ./agentruntime/

# 重启后的监督能力
go test -run TestObserver ./agentruntime/

# 前端：真实执行渲染函数，断言它构造出的 DOM
node --test web/agent_rail_dom_test.mjs
```

### 前端为什么是真的在测

`web/agent_rail_dom_test.mjs` **执行 `app.js` 里的真实渲染函数**（按大括号配对抽出函数体，在注入三个 DOM 调用后运行），断言它构造出的节点树：建议是一个 `<ol>` 且顺序正确、禁止重试是独立横幅而不是列表项、空回放给出说明而不是空白。

这与仓库其余前端测试（断言源码里出现过某个字段名）是两种强度。差别不是理论上的：**本轮之前，前端测试全绿的同时，两个装配 bug 活了下来**——一个缺失的事件 sink、一个没有归属的事件——因为它们断言的是"源码提到了什么"，而不是"代码执行后产生了什么"。

这套测试也验证过自己会失败：把"禁止重试"横幅从 `<p class="mission-agent-forbidden">` 降级成普通 `<span>`，测试立刻报错。

在真实栈上看：

```bash
make home-furnished
# 打开 http://127.0.0.1:8897/ ，跑一个任务，看"系统观察与诊断"一节
```

---

## 7. 边界：监督 agent 不做什么

| 不做 | 为什么 |
| --- | --- |
| 不修改任务状态 | 它没有 task service。改动判定仍由 `tasks.Service` 独占 |
| 不动机器人 | 它没有 invoker。白名单测试按字段钉住，加字段就会失败 |
| 不自动恢复 | 第一版对任何建议恒为 `advisory` + `requiresApproval`，有测试断言 |
| 不重新推导安全 | 只回答"提出请求的 agent 是不是那种可以做这件事的 agent" |
| 不猜根因 | 确定性规则；证据不足时输出 `missingEvidence` 说明缺什么 |
| 不发明第二套分类 | 复用 `closedloop.Classify`，否则两边迟早对"能不能重试"给出不同答案 |

Python 侧的 `fault_remedy.py` 是**执行引擎**（决定该不该跑一个有界恢复动作）；OpsAgent 是**只读观测者**。两者通过事件连接：OpsAgent 输出 `ops.recovery_proposed`，要不要做、由谁做，是既有权限与审批路径的事。

---

## 8. 为自动化运维留下的接口

| 已有 | 说明 |
| --- | --- |
| 结构化事件 | `ops.anomaly_detected` / `root_cause_hypothesis` / `recovery_proposed` / `escalation_required`，字段固定、可被机器消费 |
| 失败分类 | 七类 + `UNKNOWN_OUTCOME` 兜底，是自动化决策的唯一安全依据 |
| 禁令信号 | `automaticRetryForbidden`，自动化必须尊重它 |
| 建议清单 | `recommendedActions`，按顺序，可被工单系统直接采纳 |
| 持久历史 | `TaskHistory`，运维扫掠"哪些任务还没被复核"的输入 |

| 还没有 | 落地前要先解决 |
| --- | --- |
| 审批队列与超时 | `ApprovalID` 现在是个字符串，没有对象、没有超时（见[生命周期对象](lifecycle-objects.md)第五节） |
| 跨进程的 finding 去重 | 冷却表在内存里，重启后重置 |
| 舰队级监督 | 监督 agent 只在本地单机接线；`edge-worker`（云端路线）尚未接 |

最后一条是最实际的缺口。下面的步骤是查证过的，不是估计：

### 舰队级监督的落地步骤

**已经具备的条件**（这条比预期好）：`edge/worker` 的 `w.config.Cloud.AppendEvent(ctx, taskID, eventType, stepID, message, payload)` 已经存在，形状与本地账本的 sink 等价。所以"诊断写进可回看的事件流"这条链路在云端路线**是有位置的**，不需要新的存储。

**三处要改**：

1. **`AgentRuntime` 的 sink 签名**（`agentruntime/bus.go`）。当前是 `func(ctx, tasks.TaskEvent) error`，绑定了本地账本类型。放宽成与 `Cloud.AppendEvent` 同形的接口，两种宿主就都能接。这是纯接口放宽，本地路径不变。
2. **`OpsAgent.History` 需要一个分布式任务索引**。它现在依赖 `tasks.Service.List`，而 `edge/worker` **没有本地任务账本**（已查证：`edge/worker/worker.go` 只有上报，没有 `tasks.Service`）。所以云端路线需要一个新的 RPC：返回"哪些任务异常结束"。**这是唯一真正的新接口**，也是这一步的主要工作量。
3. **生命周期装配**。`workerInstance.Run(ctx)` 是阻塞的，监督运行时必须是并列的 goroutine，并随 ctx 一起收尾；否则它会拖住 worker 的退出。这个模式在 `cmd/local-agent` 已经跑通，可以照搬。

**为什么没有顺手做完**：第 2 步要新增云端 RPC，而本环境没有可跑的云服务器与舰队栈（`docker compose` 那套需要真实部署），因此**无法验证**。在不能验证的地方留下一段看起来能跑的新接口，比诚实地留着不做更糟——这个仓库的既有约定是"结论要有证据"。

**可以立刻做的部分**：第 1 步（接口放宽）与第 3 步（装配）是本地可验证的，只有第 2 步需要云端。如果先做 1+3 并用一个临时索引兜底，舰队能先获得**机器人级**监督（故障台账、急停、遥测过期、策略拒绝），只是还看不到任务级的"未确认步骤"。这个折中值得单独讨论。

---

## 相关

- [多 Agent 运行时](multi-agent-runtime.md) —— 分层、权限门控、仲裁
- [Agent 事件规范](agent-events.md) —— 每个事件的字段
- [生命周期对象](lifecycle-objects.md) —— 为什么有这么多对象、该不该加新的
- 闭环契约与失败分类：`core/closedloop/`
