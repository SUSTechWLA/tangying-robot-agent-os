# 监督能力的三个盲区：怎么发现的、怎么修的、以及我犯的错

**这一轮**（[Review Agent 运行原理](../architecture/review-agent.md)之后）目标是把监督能力**接到真东西上验证**：不再只用合成的 finding 测规则，而是在真实运行的 sim 上模拟机器人异常，看 Review Agent 说的对不对。

结果：**发现了三个真盲区和一个分类表缺口**，其中两个是"系统说得出话、但说不出有用的话"，一个是"说错了话"。另外我自己的操作犯了三次错，也记在这里。

这份文档记的是**过程**——为什么当时以为是对的、哪一步实测推翻了它、以及改完怎么确认。结论在[运行原理](../architecture/review-agent.md)，验证方法在[监督 Agent 验证](../architecture/supervision-verification.md)。

---

## 盲区一：重启后，崩溃前的失败全部不可见

### 当时以为是对的

Review Agent 订阅事件、在 `OnEvent` 里累积失败动作、在每次评估时遍历 `a.tasks` 里的任务去读执行记录。

看起来完整：事件驱动 + 状态驱动都有。

### 怎么发现的

**先写测试证明盲区存在，而不是先断言它有。** 一个物理步骤留在 `STARTED`、磁盘上躺着"可能已经动了但没人知道"，但让 Review Agent 在一个**没有收到任何事件**的新进程里评估：

```
=== RUN   TestObserverMissesAnUnconfirmedStepItWasNeverToldAbout
blind spot confirmed: 1 unconfirmed step(s) on disk, none reported
```

`a.tasks` 只由 `OnEvent` 填充 → 新进程为空 → `uncertainSteps` 遍历空集合 → 什么都没查。

**而"重启后还活着的失败"恰恰是最值得复核的那一批。**

### 修法分了三层，第三层是第一版漏掉的

1. **`agentcontract.TaskHistory`**(新 port，只读)。放在 contract 层是因为"读持久记录"是 agent 的一部分，不是某个宿主提供的。
2. **`OpsAgent.History` + 一次性启动扫描**。只跑一次：持久记录的变化也会以事件到达，每 tick 重读是为学不到的东西做一次查询。
3. **`Finding.TaskID`** —— **关键的一层，第一版漏了。**

第 3 层是**端到端测试逼出来的**。当时现象是"诊断确实产出了，但账本里没有"。查下去发现：`latestTask` 为空（重启后没收到任何事件）→ 事件的 `taskID` 是空的 → **落不进按任务的账本**。

**这个洞只在端到端测试里暴露。** 如果只写单元测试（断言"规则能产出 finding"），finding 确实产出，测试全绿，而它在生产里发不到任何人手里。

### 一个副产物：`Abnormal` 的定义必须收窄

启动扫描要知道"哪些任务异常结束"。最初想用"非成功终态"：

```
FAILED / RECOVERABLE_FAILURE / FAILED_SAFE / SAFETY_STOPPED / WAITING_USER / BLOCKED
```

**不包括 `CANCELLED` 和 `PAUSED`**：前者是操作员自己的决定，后者是例行等待。算成异常会让监督者变吵，而**吵的监督者没人看**。两个方向都写了测试。

---

## 盲区二：情况变糟时反而沉默

### 当时以为是对的

异常上报有 60 秒冷却，防止每 tick 重报同一件事。这个机制本身是对的——仓库的故障台账**踩过这个坑**（每次观测都重发当前故障，导致锁存急停在 4 秒内 `occurrences` 涨到 10，把每个自愈故障判成"人手故障"）。

### 怎么发现的

写"持久条件不重复上报"的测试时，顺手想验证反向情况，于是问了一句：**如果异常任务数从 1 变成 5 呢？**

发现：异常身份是 `code@component`，所以「1 个任务异常结束」和「5 个任务异常结束」是**同一个身份**，冷却窗口内第二次被抑制。

**而"数量变了"正是操作员最需要听到的时刻。**

### 修法

身份在带计数时包含计数：`ANOMALY_ABNORMAL_TASK@task#5`。不带计数的 finding 保持原身份，**稳定条件的冷却照常工作**。

测试：`TestWorseningSituationIsReportedAgain`。

---

## 盲区三：说得出"任务失败了"，说不出"哪一步"

### 当时以为是对的

盲区一修完之后，启动扫描会读持久记录、把历史任务纳入评估、报出"有任务异常结束"。看起来已经覆盖了"重启后仍可见"。

### 怎么发现的

在真实 sim 上跑了一个**真的会失败**的任务：tabletop 场景里让机器人拿一个够不到的瓶子。

结果：任务确实失败（`GRASP_NOT_REACHED`，37 个事件），但 Review Agent **只报了「有任务异常结束，需要核对执行记录确认实际到哪一步」**。

「有任务异常结束」说"出事了"。**操作员需要的是"`manipulation.pick` 抓取失败，因为够不到"**——而后者当时根本不可达：失败动作只从**实时事件**累积，而那个任务的事件在我查询之前就已落盘。

**这是"能说出话"和"说出有用的话"之间的差别**，单元测试看不出来，因为单元测试里我喂的就是 finding 本身。

### 修法

启动扫描不只知道"有哪些任务"，还从每个任务的账本里**读回工具失败**，并且**走与实时路径同一个累积函数**（抽出 `recordFailedAction`）。

为什么要共用：两份实现就是两份"什么算失败动作"的定义。而启动扫描的全部意义就是**"活得比进程久的失败，是同一个失败"**——如果两条路径判定不同，重启就会改变结论。

### 修复前后对照（同一个真实任务）

| | 修复前 | 修复后 |
| --- | --- | --- |
| 定位 | 只有「有任务异常结束」 | `manipulation.pick 失败：GRASP_NOT_REACHED（PERCEPTION）` |
| 建议 | 「核对执行记录」 | 「**重新观测或搜索目标后再尝试**」 |

---

## 分类表缺口：一个已知失败被报成"结果未知"

### 怎么发现的

修盲区三时顺手核对了一下分类结果，发现不对：

```
input="skill manipulation.pick failed: GRASP_NOT_REACHED blue-bottle"
   -> code=GRASP_NOT_REACHED category=UNKNOWN_OUTCOME sev=critical
input="skill manipulation.pick failed: NAV_BRIDGE_UNAVAILABLE down"
   -> code=NAV_BRIDGE_UNAVAILABLE category=TRANSIENT sev=warning
```

`GRASP_NOT_REACHED` 是**运行时自己产生**的码（`sim/mujoco/tangying_sim/world.py`：末端执行器够不到目标超出容差），但它**不在 `core/closedloop` 的分类表里**。

### 为什么这比"报错"严重

分类表的作用是给每个失败码**唯一一个安全恢复动作**。兜底是 `UNKNOWN_OUTCOME`——它本身是**正确且保守**的默认（无法识别的失败可能已经到达硬件，不该自动重试）。

但代价是：**把一个已知、可解释的失败报成"结果未知，禁止重试"，等于给出了错误的处置。**

- 正确：够不到目标 → 重新观测后重试
- 实际：不要重试，先对账

**兜底方向是对的，但"表里没有"和"表里定为不可恢复"这两件事看起来一模一样，而它们完全不同。**

### 修法：一件是补代码，一件是补守卫

**补代码**：把 `GRASP_NOT_REACHED` 按 `PERCEPTION` 归入表中。

**补守卫**（更重要）：此前只有 `TestClassifyAssignsTheExpectedClassForRealRuntimeCodes`（表内代码分类正确），**没有测试断言"运行时会产生的码都在表里"**。

新增 `core/closedloop/classification_coverage_test.go`（4 条），并为此**导出 `closedloop.Knows(code)`**。

为什么必须新加函数：**`Classify` 无法回答"这个码在不在表里"**。表内列为 `UnknownOutcome` 的 `EXECUTION_OUTCOME_UNKNOWN` 与完全未列出的码**分类结果相同**。我第一版测试就是拿 `Classify(code) == UnknownOutcome` 当判据，结果把 `EXECUTION_OUTCOME_UNKNOWN` 误报成"不在表里"——这个错在测试自己身上重演了一遍同一个混淆。

### 然后做了系统性审计：83 个码里 58 个不在表里

`GRASP_NOT_REACHED` 之后没有停在个例上，而是把机器人侧**所有**能返回的失败码扫了一遍。

扫描本身也踩了一次坑：第一遍只搜一种写法，找到 53 个；后来发现 `GOAL_NOT_CLEAR` 是通过 `ServiceError(...)` 抛出的——**整整一类被漏掉了**。补上后合计 **83 个唯一码**。

结果：**58 个不在分类表里**，全部静默退化成「结果未知，禁止重试」。

**导航受阻的实测把危害演示得很清楚**：真实失败 `GOAL_NOT_CLEAR`（路径规划预检：目标工作区未扫描或安全间距不足）被报成

```
navigation.navigate 失败：GOAL_NOT_CLEAR（UNKNOWN_OUTCOME）
建议：不要自动重试：先对账确认这次动作的实际结果
```

**而底盘根本没动过。** 让操作员去对账一个从未发生的动作，比不报还糟。

修复后：

```
navigation.navigate 失败：GOAL_NOT_CLEAR（PERCEPTION）
建议：重新观测或搜索目标后再尝试
```

**修法与结果**：按既有七类语义把 83 个码全部归类（权限 / 验证 / 资源 / 瞬时 / 感知 / 规划），并把这个清单**提交进仓库**作为永久守卫 `TestEveryCodeTheRuntimeCanEmitIsClassified`。

清单**提交而不是自动爬取**是刻意的：新增一个码而没有加进清单，它就会落在未分类状态，而这个守卫会失败。**「未分类」作为默认是正确的，作为意外是危险的。**


### 审计本身也有盲区：漏掉了一整类抛出方式

第一版审计宣布"83 个码全部覆盖"之后，**在真实机器人上又发现了漏网**：

```
verify_placement 失败：PLACEMENT_NOT_OBSERVED（UNKNOWN_OUTCOME）
建议：不要自动重试：先对账确认这次动作的实际结果
```

`PLACEMENT_NOT_OBSERVED` 不在那 83 个里。原因是**扫描的证据来源太窄**：它通过

```python
self._verify_relation(entity_id, f"inside:{destination_id}", "PLACEMENT_NOT_OBSERVED")
```

作为**字符串参数**传入，而不是 `ToolResult(False, "...")` 或 `ServiceError("...")` 的字面量。一个正则匹配不到另一种写法。

**扩宽证据来源后（5 类来源合并）得到 113 个码**，又补进 16 个，其中包括被一起漏掉的 `GRASP_NOT_OBSERVED`、`NAV_OBSTACLE_OBSERVED`、`NAV_PATH_OCCLUDED`、`NAV_DEPTH_UNKNOWN`、`DEPTH_STARVED`、`GROUNDING_ABSENT`、`SEMANTIC_AMBIGUOUS`、`SAFETY_STOPPED`，以及传输类（`RPC_UNAVAILABLE`、`TRANSPORT_ERROR`、`CONNECTION_REFUSED` 等）。

**关于 `*_NOT_OBSERVED` 归到哪一类，这次是刻意选保守的。** 它们的语义是「**动作已经执行了**（夹爪闭合了/放置动作做了），但没能观测到预期关系」——这与「动作根本没发生」的感知失败**不同**：放置动作即便验证失败，物体也可能已经在目的地，重试会再放一次。所以它们归 `UNKNOWN_OUTCOME`（结果未知、必须对账），而不是 `PERCEPTION`（重观测后可重试）。

这与 `diagnose_task.py` 已有的 `verification_not_observed` 故障族一致，也与闭环契约把 `VERIFICATION_FAILED` 归为 `Fatal` 的方向一致。

### 修好之后的实测（你自己的机器人上的 5 条真实告警）

| 真实失败码 | 分类 | 建议 |
| --- | --- | --- |
| `PLACEMENT_NOT_OBSERVED` | `UNKNOWN_OUTCOME` | 不要自动重试：先对账 |
| `NO_KNOWN_PATH` | `PERCEPTION` | 重新观测或搜索目标后再尝试 |
| `ROBOT_COMMISSIONING_ACTIVE` | `RESOURCE` | 检查审批、租约或资源占用状态 |
| `DESTINATION_NOT_FOUND` | `PERCEPTION` | 重新观测或搜索目标后再尝试 |

修复前这 4 条里只有 1 条是已知码，另外 3 条全被报成「结果未知，禁止重试」且建议完全相同。现在建议按失败类型分化了。

### 教训：宣布"全覆盖"之前先问证据来源够不够宽

这一节的初稿写了「83 个码全部覆盖」，那是**基于一次扫描的结论**，而扫描的覆盖面本身就是假设。真实数据推翻它只用了一条告警。

**如果只在测试里验证，这个漏检不会被发现** —— 测试用的是我列出的码，而漏掉的码不在我的清单里。是**接上真东西**才暴露的。

---

## 我犯的三次错（记下来，因为都是真实教训）

### 错误 1：注入故障时没有确认恢复路径

为了测断联，我 `kill` 掉了正在被使用的 sim。**但没有确认它的启动参数和恢复方式。**

后果：
- 正在运行的控制台失去遥测
- 重启后 sim 的地图未启用，机器人报 `NAV_MAP_NOT_READY`，**导航任务跑不了**
- 那个状态需要走一遍「巡检建图 + 控制台启用地图」才能恢复

**教训：破坏性实验必须在独立端口上做。** 这一轮后半段改成 `--listen 127.0.0.1:50199` 之后，就不再影响任何正在运行的东西。已写进[验证文档](../architecture/supervision-verification.md)。

### 错误 2：把自己的操作失误当成产品缺陷

上一条之后，我看到"遥测一直不恢复"，于是判断"**运行时重启后客户端不会重连**"，还把它当成一个发现报告了出去。

后来做了干净的复现实验：杀 sim → 重启 sim → **客户端自动重连，遥测恢复，告警自动撤回**。`grpc.NewClient` 是惰性连接，本来就会恢复。

**真相是：我杀掉了 sim 但没有重启它。** "不恢复"是正确行为。

**教训：一次观察不构成结论。** 我当时的证据是"遥测看起来不恢复"，而那个证据同样符合"我根本没把服务起回来"这个更简单的解释。

### 错误 3：两个自己写的测试断言错了

- 一个断言"重复上报要抑制"，但测试造的输入里**有两个不同的 finding**（急停 + 观测过期），所以 2 条告警是**正确**的，是断言太严。
- 一个断言"故障码必须同时出现在模拟器和注入脚本里"，但**两层用的词表本来就不同**（机器人证明 `EMERGENCY_STOP_LATCHED`，agent 报告 `ANOMALY_SAFETY_STOP`）。是测试写错，不是代码错。

**教训：测试失败时，先问"是代码错还是我的预期错"。** 这两次都是预期错，而如果我当时直接改代码去迎合测试，会把正确的行为改坏。

---

## 这一轮沉淀下来的三条方法

不是抽象原则，是这一轮反复用到的具体做法：

### 1. 先写测试证明盲区存在，再修

盲区一的发现方式就是这一条。如果先断言"应该有 XXX 功能"，就会去实现一个**自己想象出来的**缺口；先写测试证明"当前会漏"，缺口的形状是数据给出的。

### 2. 端到端测试抓单元测试抓不到的洞

盲区一的第 3 层（`Finding.TaskID`）**只在端到端测试里暴露**。单元测试里 finding 正常产出、断言全绿，而它在生产里发不到任何人手里。

**"组件正确"和"链路正确"是两件事。**

### 3. 交叉验证：agent 说的要和别处的事实对得上

实测时最有价值的不是"agent 报警了"，而是：

- agent 说 `NAV_MAP_NOT_READY`，查导航接口得到 `"ready": false, "gridUnavailable": true` —— **同一事实，两个来源**
- 遥测恢复不靠"告警消失了"推断，而是**历史条数在持续增长**
- 日志的断联错误与告警**同时出现、同时停止**

单看 agent 的输出，无法区分"它对了"和"它碰巧说了句话"。

### 4. 守护要有，而且要能验证自己会失败

新增的前端 DOM 测试、分类覆盖测试、注入器诚实性测试，都**注入过退化去验证它们真的会报错**：

- 把"禁止自动重试"横幅从 `<p class="mission-agent-forbidden">` 降级成普通 `<span>` → 测试立刻报错
- 往预检脚本里加一条 `pip install` → 只读性守卫报出文件名和行号

**不能失败的守护等于没有守护。**

---

## 相关

- [Review Agent 运行原理](../architecture/review-agent.md) —— 结论与设计
- [监督 Agent 验证](../architecture/supervision-verification.md) —— 故障矩阵、注入器、实测数据
- [模块故障与自愈](../architecture/module-health-and-faults.md) —— 机器人侧处置阶梯
- [生命周期对象](../architecture/lifecycle-objects.md) —— 为什么"结果未知"必须是一个终态
