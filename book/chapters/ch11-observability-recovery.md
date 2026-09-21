# 第 11 章 可观测性、事故诊断与自动恢复

> **本章的核心命题**
>
> 监督系统最糟的失效模式**不是"漏报"**，而是**"沉默被读成健康"**。
>
> 一个只订阅**未来事件**的观察者，在它自己启动之前发生的事情上一无所知——
> 而它的沉默看起来像一个健康的系统。

---

## 11.1 一个名字，两份不同的记录

这一章要从一个容易搞混的地方开始：**仓库里存在两个都叫 "incident" 的 schema，由两种语言各自产出，职责相反。**

| | `incident.bundle.v1` | `incident.v1` |
| --- | --- | --- |
| 产出者 | **Go**，`incidents` 包 | **Python**，`scripts/diagnose_task.py` |
| 定义 | `incidents/bundle.go:28` | `scripts/diagnose_task.py:37` |
| 内容 | **只有事实**：状态、事件、步骤、证据引用、耗时、修订 | 事实 + **带标签的第一次诊断**（故障族、可能根因、先查什么、覆盖测试） |
| 落盘 | `artifacts/incidents/<taskId>.bundle.json` | `--output` 目录下 `<taskId>-incident.json` |
| 是否分类 | **不分类、不推测、不建议** | 分类是它唯一的职责 |
| 是否改动机器人 | 不改 | 不改（`automation.acted` 恒为 `false`） |

**这个切分是刻意的**，理由写在源码头注释里（`incidents/bundle.go:5-9`）：

> **故障族知识只能有一处**，放在 Python 的 `FAMILIES` 表里；
> **另一种语言里的第二份拷贝必然漂移。**

**教学要点**：**"事实"和"诊断"分开存储。**

一个把两者混在一起的记录有个致命问题：**你无法判断一句话是观测还是推断**。

而分离后，两边各自可以被独立验证：

| | 怎么验证 |
| --- | --- |
| bundle（事实） | 它引用的每个 observation ID 都能查到 |
| incident（诊断） | 分类规则可以被单测覆盖 |

（第 6 章 `OpsAgent` 的"四类事件的语义分工"——anomaly 说事实、hypothesis 说结论——是同一条原则。）

> **⚠️ 而这条"只能有一处"的原则后来被咬过一次**（见 9.3）：`diagnose_task.py` 的故障族表与 `closedloop` 的分类表**仍是两套独立词表**。

### 一个真实 bundle 的样子

`artifacts/incidents/task-03ec8cde7cd2a6db8766c7da.bundle.json`：

```json
{
  "schemaVersion": "incident.bundle.v1",
  "task": {"id":"task-03ec8cde7cd2a6db8766c7da","request":"把红色杯子放进右侧收纳盒",
           "adapter":"mujoco","revision":1,"state":"RECOVERABLE_FAILURE",
           "terminalCode":"subtask 1: rpc error: code = Unavailable desc = controlled demo regression failure"},
  "recovery": {"canResume":false,"requiresReconciliation":false,"reasonCode":"NO_RECOVERY_REQUIRED", ...},
  "timeline": [ ... 16 条 ... ],
  "stepRuns": [], "evidence": [],
  "timing": null,
  "collectedAt": "2026-09-20T18:55:08.974703+08:00",
  "note": "本记录只包含事实：状态、事件、步骤、证据引用、耗时与修订。…"
}
```

字段全集在 `incidents/bundle.go:39-123`，其中 `Evidence` **只存 rgb/depth 的 SHA256 引用，不复制图像**。

**为什么不复制图像？** 因为一份事故包如果包含图像，它的大小会让"保留 200 份"变得不现实——而**保留足够多的历史事故**比"每份都完整"更重要。

### 什么时候自动产出

**唯一写入点**是 `internal/localapp/app.go:486 recordIncident`，**唯一调用点**是 `app.go:618`，位于 `run()` 的**异常结束分支**（`app.go:604-618`）：

| 触发 | 状态 |
| --- | --- |
| 暂停 | `StatePaused` |
| 可恢复失败 | `StateRecoverableFailure` |
| 操作员取消 | `StateCancelled` |
| Local Agent 进程停止 | — |

**成功结束不写。**

**教学要点**：这是一个**刻意的采样决定**。

如果成功也写，那么 200 份的保留窗口会被成功任务占满，而**事故包的价值恰恰在异常上**。

（代价是"为什么这次成功了而上一次失败了"无法直接对比——需要另开一个成功样本的采集通道。）

### 四条工程约束都写在代码里

**① 写入不得改变任务结局。**

`app.go:533-534` 写失败只 `log.Printf`；包注释 `bundle.go:11-12,151` 把"best effort"写成**契约**。

**这一条非常重要**：一个"审计写入失败导致任务失败"的系统，会让运维为了保住任务而关闭审计。

**② 读者不会看到半份事故。**

`bundle.go:182-190` 用 **write-then-rename**。

**③ 有界：`DefaultKeep = 200`。**

`prune()`（`bundle.go:210-241`）按 mtime 保留最新 200 份。

（`artifacts/incidents/*.bundle.json` **不入库**——commit `5dfa9728e` 加的 `.gitignore` 规则。）

**④ 目录可部署。**

`TANGYING_INCIDENT_DIR`，装配点 `cmd/local-agent/main.go:420`，解析函数 `main.go:576-581`。

**第 ④ 条是"跨机学习"唯一实现的路径**（见 9.3 的诚实标注）。

---

## 11.2 确定性故障族分类：一次审计踩了两次坑

### 分类表的结构

分类表在 `core/closedloop/closedloop.go:83-274`，结构是**有序数组 + 每项一个 code 集合**：

```go
var classification = []struct{ class Class; codes map[string]struct{} }{ ... }
```

**顺序敏感，第一条命中的规则胜出。**

本次实测的码数（用脚本解析源码，**不是目测**）：

| 类 | 条目数 |
| --- | ---: |
| `UnknownOutcome` | 15 |
| `Permission` | 29 |
| `Resource` | 9 |
| `Perception` | 47 |
| `Planning` | 17 |
| `Validation` | 27 |
| `Transient` | 32 |
| `Fatal` | 3 |
| **合计** | **179 条目 / 177 唯一码** |

**两个码各出现两次**（`XLEROBOT_MAX_RELATIVE_TARGET`、`XLEROBOT_MAX_ACTION_CHUNK_LENGTH` 同时在 `Permission` 与 `Planning`），因为 `Permission` 在前，实际分类结果落在 `Permission`。

**重复是无害冗余，但书里必须说明"条目数 ≠ 唯一码数"。**

### 「113 个码」是历史数字

**这是本书里第三个必须精确处理的数字**（前两个是"七类/八类"和 `tools.json` 的工具数）。

| 数字 | 含义 | 时间 |
| --- | --- | --- |
| **113** | commit `c8cae5af2`（"分类表补齐 113 个运行时故障码"）那一刻 `runtimeEmittedCodes` 清单的**唯一码数** | 2026-09-17 |
| **119** | 今天 `runtimeEmittedCodes` 的**唯一码数**（123 条目，4 个 `NAV_STOW_*` 各重复一次） | 今天 |
| **177** | 今天**分类表**覆盖的唯一码数（179 条目） | 今天 |

**写书时应写**：**"113 是 2026-09-17 那次审计的规模，此后继续增长。"**

### 「消除已知失败被报成结果未知」是怎么达成的

**触发事件**写在 commit `c8cae5af2` 正文里。

一次真实导航受阻，`GOAL_NOT_CLEAR`（**路径规划预检拒绝，底盘根本没动**）被报成：

```
（UNKNOWN_OUTCOME）建议：不要自动重试：先对账确认这次动作的实际结果
```

原文的评价：

> **让操作员去对账一个从未发生的动作，比不报还糟。**

**根因不是兜底逻辑错，而是表缺项。**

### 而审计本身踩了两次坑

**这是本节最好的方法论材料。**

| 遍 | 方法 | 结果 | 漏了什么 |
| --- | --- | --- | --- |
| 第 1 遍 | 只搜 `ToolResult(False, "...")` 一处字面量 | 找到 **53 个** | **漏掉整整一类**——走 `ServiceError(...)` 的码 |
| 第 2 遍 | 补上 `ServiceError` | 得 **83 个**，宣布"全覆盖" | 在真实机器人上又发现 `PLACEMENT_NOT_OBSERVED` 漏网——它是作为**字符串参数**传入的（`self._verify_relation(..., "PLACEMENT_NOT_OBSERVED")`） |
| 第 3 遍 | **扩宽到 5 类证据来源**（`ToolResult` / `ServiceError` / 参数传递 / diagnose 故障族 / Go 侧 emit） | **113 个码** | — |

**教学要点**：**审计一个"是否穷尽"的问题，第一遍几乎一定是漏的。**

而三次漏网的原因**各不相同**：

| 遍 | 漏网原因 | 性质 |
| --- | --- | --- |
| 1 | 只搜了一种**语法形式** | 检索不完整 |
| 2 | 漏了**作为参数传递**的字符串 | 检索不完整 |
| 3 | — | 扩到 5 类来源才齐 |

**所以守卫必须是"可持续失败的测试"，不能是一次性的搜索**——因为一次性搜索的完整性**无法被验证**。

### 契约级守卫：三条互补的机制

`core/closedloop/classification_coverage_test.go`（395 行，10 个 `Test` 函数）：

| # | 机制 | 作用 |
| --- | --- | --- |
| 1 | `runtimeFailureCodes`（`:39-43`）声明"某个码必须仍出现在某个运行时源文件里" | 源文件里删掉码 → **测试红** |
| 2 | `TestACodeTheTableMissesFallsBackToUnknownOutcome`（`:77`） | **把危险本身钉住**，而不是钉住"没有危险" |
| 3 | `TestTheClassifierTableCoversCommonRuntimeCodes`（`:89`）用 `Knows()` 而非 `Classify()` 做成员判定 | 见下 |

**第 2 条值得单独说**：大多数测试断言"正确的行为"。这一个断言"**错误的输入会导致什么**"。

**为什么？** 因为兜底方向是保守的，所以"表缺项"的**直接表现**就是"落进 `UnknownOutcome`"。**把这个表现钉住，就在测试里保留了一条关于危险的记录。**

**第 3 条的技术理由**（`:104-113`）：

> `EXECUTION_OUTCOME_UNKNOWN` **既在表里又归类为 `UnknownOutcome`**，所以**比较类别值无法区分"列了"与"没列"**。

**这正是 `Knows()` 存在的理由**（第 3 章详述）。

### Python 侧是另一套结构

`scripts/diagnose_task.py:42-187`，共 **8 族**：

```
physical_outcome_unknown
verification_not_observed
goal_or_localization_unclear
mapping_session_fault
safety_stop
grounding_failure
transport_failure
fleet_consistency
```

每族五个字段：`summary` / `codes` / `causes` / `checks` / `resolution` / `tests`。

**`tests` 里是真实可运行的测试路径与函数名**——文档明确说：

> **"指向不存在的测试比不指更糟"**，并有测试断言它们真实存在。

**教学要点**：这是一个"**文档里的引用要被测试**"的例子。

一个 `tests` 字段如果写着 `test_foo.py::test_bar` 而那个测试不存在，读者会以为"这个族有覆盖"——**而实际上没有**。

### 自动扫掠：一条命令扫出全部历史故障

```bash
.venv/bin/python scripts/diagnose_task.py --sweep artifacts/incidents
```

本次实测结果：

```
共 64 份，未归类 26 份；退出码 1
```

64 份的分布：

| 故障族 | 份数 |
| --- | ---: |
| `transport_failure` | **28** |
| `goal_or_localization_unclear` | **10** |
| `unclassified` | **26** |

而未归类的 26 份里：

| 原因 | 份数 |
| --- | ---: |
| 无错误码 | 23 |
| `TOOL_PARAMETERS_INVALID` | 1 |
| `reconstruction rejected: reconstruction is stale or future dated` | 1 |
| **`GRASP_NOT_REACHED`** | **1** |

### ⚠️ 一个未修的跨语言分歧

**`GRASP_NOT_REACHED` 在 Go 侧被显式分类为 `PERCEPTION`**（`closedloop.go:176`），而 **Python 的 `FAMILIES` 八族里没有任何一族包含它**，于是 sweep 报"未归类"。

commit `5dfa9728e` 曾发现"Go 与 Python 对 **87 个码**判定不一致"并修复了 `tool_layer.py` 的**镜像表**。

**但 `diagnose_task.py` 的故障族表与 `closedloop` 分类表仍是两套独立词表。**

**二者的表现**：

> **运维以为已覆盖，工具说没覆盖。**

**教学要点**：这与第 5 章 ADR-1 拒绝的"新开一条总线"、第 6 章 §6.8 结尾是**同一个问题**：

> **同一个概念有两份定义。**

**而它的通用解法，这个项目自己已经有了**——`tools.json` 从代码生成，`--check` 断言逐字节相同。

**同一手法应该被用到分类表上。**

### 巡检的退出码是可用的

**未归类的计入非零退出码**（本次实测 `exit 1`），因此**可直接接 CI 或巡检告警**。

**教学要点**：这是一个"**让不可见的东西可见**"的设计。

如果 sweep 只是打印一份报告，那么"26 份未归类"会被忽略。**让它出现在退出码里，就让它出现在 CI 里。**

---

## 11.3 硬件故障发布成观测：从「报出来」到「摘能力」

### 契约：`robot.faults.v1`

`Fault`（`core/robotcontract/faults.go:16-30`）字段：

```
moduleId / kind / code / severity / detail / occurrences
remedy / userInstruction / detectedAtUnixMS / lastSeenUnixMS / evidence
```

`FaultReport`（`:35-47`）多两样东西：

| 字段 | 内容 |
| --- | --- |
| `UnavailableCapabilities` | 排序后的"**没了什么**" |
| `CapabilityBlockers` | **每个能力被哪些故障摘掉** |

**两者必须一致**（`:141-153`），因为控制台解释"**这台机器人为什么不能导航**"读的正是这张 map。

### 严重度是有序词表

```
info < degraded < blocked < safety
```

`Blocking()` **排除 `info`**，注释写得很漂亮（`:70-80`）：

> **`info` is news, not a capability loss.**

**"`info` 是消息，不是能力损失。"**

### `Validate()` 拒绝九类自相矛盾的报告

`:103-155` 拒绝：

| # | 拒绝 |
| --- | --- |
| 1 | 未知字段 |
| 2 | `null` |
| 3 | 未知严重度 / 模块种类 / 处置类 |
| 4 | `count` 与条数不符 |
| 5 | **头条严重度不是最严重那条** |
| 6 | 同一 `module:code` 重复 |
| 7 | **`capabilityBlockers` 与 `unavailableCapabilities` 互相矛盾** |
| 8 | **blocker 指向报告里不存在的故障** |
| 9 | 缺 `detectedAtUnixMS` |

**理由很硬**（`:183-185`）：

> 这份文档是**能力门禁的输入**——
> **"一份自相矛盾的故障表比没有故障表更危险，它会让大脑以为某个模块是好的。"**

**"比没有故障表更危险"** —— 这句话值得单独抄下来。

一个**没有**故障表的系统，上层知道"我不知道"。一个**自相矛盾**的故障表，上层会以为它知道。

### 摘能力：三段实现

**① 机器人侧判定**

Python `module_faults.py` 的 `capability_impact()`（`:171-190`）：

- `blocking = [f for f in faults if f.blocks_capability]`
- **`safety` 级摘掉全部能力**（`:186`）
- 否则按每个 capability 声明的 `requires` 模块集合匹配（`:190`）

而 `ESCALATION_THRESHOLD = 5`（`:69`）：

> 同一故障第 **5 次复发**时，`self_recover` 自动升级为 `operator_assist`（`:132-136`）。

**教学要点**：这是一个"**重试预算**"的正确形态。

第 3 章讲过"为物理写入做重试预算必须先把尝试计数持久化"。这里的计数是**由机器人侧持有的故障台账**，而它的效果是**升级处置方式**（自愈 → 请人），不是"再试一次"。

**② 随观测发布**

故障作为 `Observation.semantic_state` 的一部分进世界模型，**不需要新总线**。

**教学要点**：这是一个"**复用已有通道**"的决定，与 ADR-1 拒绝新开执行总线是同一条原则。

**③ Go 侧门禁**

`edge/runtime/runtime.go:161` 抛 `ErrCapabilityUnavailable` 并带上 **blocker 列表**，**计划期与运行期都拒绝**。

**"带上 blocker 列表"很关键**：它让"为什么不能导航"这个问题有答案，而不是一个笼统的"不可用"。

### 对正在跑的任务的影响

`docs/architecture/module-health-and-faults.md:237-246` 给了完整分支：

```
模块故障 → 能力 unavailable → 计划期失败（原因=哪个模块）
                              ├─ 有等价能力：换策略（左臂坏→右臂；底盘坏→原地作业）
                              └─ 否则：任务进入可恢复失败 + 事故记录
                            → 用户面板提示 + LLM 提议 self_recover / 请人处理
```

**三条既有语义继续生效**：

| 语义 | 内容 |
| --- | --- |
| 安全级故障 | 任务 `SAFETY_STOPPED` 且**不能由任务动作解除** |
| 结果未知 | 命令发出后模块失联 → 步骤保持 `STARTED`、**永不重放** |
| 降级运行 | 属于"任务改版/换策略"，**不是新机制** |

**第三条尤其值得注意**：它拒绝了为"降级运行"发明一个新机制。

**"换策略"已经是一个存在的能力**（`WAITING_SAFE_POINT` + Revision），所以降级不需要新概念。

### 实测证据

文档 `:272` 标为"已完成"：

| 场景 | 结果 |
| --- | --- |
| 锁存急停后 | 观测里出现 `estop:EMERGENCY_STOP_LATCHED`（`safety`）；`navigation.navigate` / `manipulation.pick` / `observe_scene` 变 `available=false` 且 blockers 指向该故障；**而 `emergency_stop` 仍可用** |
| 未启用地图的移动场景 | 报 `chassis:NAV_MAP_NOT_READY` 且**只摘导航** |

**第二行值得注意**：一个"地图没准备好"的故障**只摘掉导航**，不摘掉别的能力。**摘能力是有粒度的。**

（这直接关系到第 6 章"机器人现在会什么"这个对象的准确性。）

### ⚠️ 必须诚实标注的边界

| 项 | 状态 |
| --- | --- |
| 设计文档列的五步落地 | 第 1、2 步"已完成"；**第 3、4、5 步未完成** |
| 机器人侧自愈引擎 `FaultRemedyEngine` | **"有完整四条约束和测试，但生产路径里 0 个调用者"**（`docs/architecture/review-agent.md:142`） |
| `remedyOutcomes` 字段 | **"会被完整传到 Agent，但没有任何规则解释它"**——传输有测试钉住；**"已传输"与"有消费者"是两件事** |

**教学要点**：这是"**没通电**"的第三个样本（第 5 章 `RequestMutation`、第 6 章 `ProjectWithFactorPolicy`）。

而 `remedyOutcomes` 是一个**更细的形态**：

> **数据被正确传输，但没有消费者。**

它的危险在于：一份**有测试覆盖的传输**会让人相信这个机制在工作。而**从数据产生到被使用之间有一步没人检查**。

**一个可用的审计问题**：

> **这个字段，谁在读它？如果我把产生它的代码删掉，会有什么失败？**

---

## 11.4 自动恢复引擎：只读的自动，改动的等人

### 一条线，一行代码

`internal/autorecovery/supervisor.go`（230 行）只做一件事：**跑计划里目录判为 `read_only` 的步骤**。

**"只读步骤无人执行，改动仍等人"的强制点**（`supervisor.go:183-189`）：

```go
if action.Risk != agentruntime.RiskReadOnly {
    return s.skip(...)   // 这一行就是那条线
}
```

### 线画在哪里很重要

**线画在"目录说它 `read_only`"，而不是"工具自称不改世界"。**

包注释（`:16-23`）给了理由：

> `MutatesWorld` 是**服务对自己的声明**；风险等级是**目录对"运行这个动作意味着什么"的判断**。
> 读风险等级让"能不能无人执行"**只有一个答案**，而且答案在**它被评审的地方**。

**教学要点**：这是一个"**权威来源**"的选择。

同一个问题——"这个动作安全吗"——有两个可能的答案来源：

| 来源 | 性质 |
| --- | --- |
| 工具/服务的自述 | **被检查者自己说** |
| 恢复目录的风险等级 | **在代码评审里被看过的地方** |

**选后者**，因为"能不能无人执行"这个判断必须有一个**可审计的**答案。

（第 5 章 `needsApproval` 从 manifest 派生而不询问工具，是同一条原则。）

### 三个"停下来"的原因被分开记录

包注释（`:5-23`）把三个原因分开：

| 原因 | 含义 |
| --- | --- |
| `bounded_write` | **等人**（目录说的） |
| `never_automatic` | **任何人都不跑**（结构上够不到） |
| 工具缺失 | **跑不了** |

**为什么分开？** 因为它们的**处置方式不同**：

- `bounded_write` → 去找人批准；
- `never_automatic` → 换一个动作，或者接受现状；
- 工具缺失 → **去修那个工具**。

**把它们合并成"没执行"，运维就不知道该做什么。**

### 四条其它硬约束

**① 上限：`DefaultMaxActionsPerPlan = 3`**

理由（`:67`）：

> **提案者可能以会产出长清单的方式出错。**

**教学要点**：这是一个"**给模型的输出设上限**"的例子，而理由不是性能，是**可靠性**。

**② 遇不确定即停**

跑过但 `!Verified`，或跑过但 `!Executed`，立刻 `break`（`:153-164`）：

> **"继续下去等于在一个上一步可能已经改变的世界里决定下一步。"**

（这与第 3 章"结果未知禁止重试"是同一条原则，只是作用在恢复计划的内部。）

**③ 没有目录就什么都不跑**

`Catalog == nil → ErrNoCatalog`（`:118`）：

> **"无法检查权限"不等于"拥有权限"。**

**④ 不伪造同意**

自动通道调用执行器时传 `OperatorApproved: false`（`:199`）：

> **Saying otherwise would put a person's consent in the record where there was none.**

**"否则就是把一个人的同意写进了一份没有同意的记录。"**

**⑤ 跳过也记录、跑过的不重复记**（`:96-109`）

理由：

> 只记"跑了什么"的回放**无法区分**"系统看过并决定不碰"与"系统从没考虑过"。

**教学要点**：**这是一个关于"负面证据"的设计。**

"系统没有执行 X"有两种含义：
1. **它考虑过 X，决定不执行**（这是一个信息）；
2. **它根本没想到 X**（这是另一个信息）。

**只记录"做了什么"的日志，无法区分这两者。**

---

## 11.5 恢复目录：16 条动作，模型不能发明动作

### 目录不是愿望清单

`agentruntime/recoverycatalog.go` 的目录**从机器人已声明的服务目录推导**（`ListServices`）。

本次实测 `DefaultRecoveryCatalog()` 共 **16 条**：

| 风险类 | 条数 | 动作 id |
| --- | ---: | --- |
| **`read_only`** | **6** | `observe.re-read`、`nav.read-map`、`map.read-conflicts`、`map.read-status`、`calibration.read`、`execution.read-history` |
| **`bounded_write`** | **7** | `map.re-survey`、`map.activate`、`nav.re-localize`、`arm.home`、`device.reconnect`、`calibration.run`、`task.retry-step` |
| **`never_automatic`** | **3** | `estop.release`、`hardware.replug`、`calibration.change` |

> **⚠️ 一处注释漂移**：`internal/recoveryexec/executor.go:12` 的注释写 *"a `switch` over the thirteen catalog ids"*，与当前 16 条不符。**这是注释漂移，不是行为差异。**

### 每条动作声明两样东西

| 字段 | 含义 |
| --- | --- |
| `Tools` | 它**只**能调的具名工具 |
| `MovesTools` | 其中**会动机器**的 |

**这两个字段的双重作用**（`recoverycatalog.go:62-98`）：

1. 是**"动作 → 工具"的映射**（不是执行器里的 `switch`）；
2. **也是批准的边界**。

**教学要点**：**"动作到工具的映射"和"批准边界"是同一份数据。**

这是刻意的——如果分成两份，它们会漂移。而漂移的后果是"批准了一个动作，但它实际调了别的工具"。

### `Executable()` 是一次真实修复

```go
RequiresApproval() = risk != read_only        // :342-344
Executable()       = risk == read_only || risk == bounded_write   // :354-356
```

**后者是一次真实修复**：原实现写成 `risk != never_automatic`，于是——

> **一个没有声明风险等级的新条目会被判为可执行**——而"要不要问人"恰好由那个字段决定。

`docs/architecture/recovery-agent.md:210-213` 记录了这次修复。

**这是第 9 章讲过的白名单默认拒绝模式的第一个实例。**

### 一条曾经把自己锁死的规则

`rule.reconcile-first` **曾经把自己锁死**。

契约说"结果未知时不得改动机器人"。旧实现是 **escalate 且不带步骤**——**读起来正确，跑起来是死锁**：

> **完成对账的动作本身就是一个只读观测**，而 escalate 会**丢弃所有步骤**。
>
> 系统能发现"世界状态不明"，却**永远无法自己离开这个状态**。

现在改为**提出目录里匹配的只读动作**，而且在这一支里**模型根本不会被咨询**（`docs/architecture/recovery-agent.md:276-284`）。

**教学要点**：**这是本书里最好的一处"规则自锁"案例。**

| | 规则 |
| --- | --- |
| 契约 | 结果未知时**不得改动机器人** |
| 实现 | 一律 escalate，**不带任何步骤** |
| 后果 | **对账本身也是"动作"，于是被自己的规则禁止了** |

**修法的关键**：识别出"**只读观测不是改动**"，于是它可以在"不得改动"的约束下执行。

**这与第 5 章 `needsScope` 那条注释是同一个洞察**：

> 把"看"也纳入范围**不是安全属性，是一次停机**。

### `ForShapes()` 只返回只读动作

`:293-311`。

**因此"确定性路线"结构上不可能提出写动作。**

**教学要点**：**"结构上不可能"比"检查后拒绝"强** ——这是本书反复出现的模式。

### 已落地的实测数据

（`docs/architecture/recovery-agent.md:285-295`，**52 个任务**）

| 动作 | 结果 |
| --- | --- |
| `observe.re-read` | 自动执行成功 **21 次** |
| `execution.read-history` | 成功 **6 次**（**接线前 8 次全部因工具缺失失败**） |
| 账本 | **27 条** `ops.recovery_executed`，`operatorApproved` 为空（**表示不是人批的**） |

**`execution.read-history` 那一行值得注意**：接线前 8 次**全部失败**，原因是工具缺失。

**这说明"机制存在"和"机制可用"是两件事**——而失败是**可见的**（8 次失败记录），不是静默的。

### 同一文档立刻给出的反面结论

> **"它没有消除任何一条发现，这是能力边界而不是失败"**
>
> `telemetry.read` 读的是机器人**当前**状态，**回答不了"那一步当时完成了没有"**。
> 要让发现真正消解，需要有人依据证据判定世界状态，而**目录里没有任何动作做这件事**。

**教学要点**：这是本章最诚实的一段。

一个自动恢复引擎跑了 27 次、全部成功——**但它没有解决任何一个原始问题**。

**"成功执行"不等于"问题解决"。** 而作者明确写下了这个边界。

---

## 11.6 恢复执行的完整链路：批准 → 执行 → 复验 → 记录

入口是 `POST /v1/recovery/execute`（路由 `console/server.go:199`，实现 `console/recovery_execute.go`）。

### 四步在代码里的位置

| 步骤 | 位置 | 关键事实 |
| --- | --- | --- |
| **① 批准** | `console/recovery_execute.go:70-135`；`internal/recoveryexec/executor.go:251-277` | 点击即批准（`OperatorApproved: true`），但**带证据**：`ApprovalEvidence: s.operatorEvidence(r)`（`recovery_execute.go:131-134`） |
| **② 按目录声明的工具执行** | `executor.go:206-320` | **顺序即安全论证**：目录可执行 → 工具存在 → 风险决定问不问人 → 在 scope 内跑 |
| **③ 复验** | `executor.go:322-345`；`Verify` 端口 `:84-97` | 结果**不由动作自述**，由 `Verify` 重新读事实 |
| **④ 记录** | `executor.go:176-198` | `Record` 在 **wrapper** 上而非各 return 上 |

### 第 ④ 步的理由值得单独引用

> `Record` 在 wrapper 上而非各 return 上，因为"**execute 有六种结束方式，将来加的第七种会静默跳过记录**"。

**教学要点**：这是一个"**为将来的改动设计**"的例子。

如果 `Record` 写在每个 `return` 之前，那么**新增一种结束路径的人必须记得加一行** —— 而他会忘。

**把记录放在 wrapper 上，让"忘记记录"在结构上不可能。**

### 四个检查的顺序就是安全论证

`executor.go:200-205` 明写 **"stated rather than implied"**：

| # | 检查 | 位置 |
| --- | --- | --- |
| 1 | `request.Action.Executable()` —— **目录拒绝先用目录自己的句子** | `:213-220` |
| 2 | 声明的工具必须在这台机器人上**真的存在** | `:231-239` |
| 3 | 风险决定要不要问人 | `:251-277` |
| 4 | 执行范围 = 该动作声明的工具 | `:281-304` |

**第 2 步的错误信息**："**不需要重试，需要先把对应的能力接上**"。

**第 3 步的硬约束**：`RiskReadOnly` 不问，`bounded_write` 必须批准，**没有配置批准入口时不执行**（`:258-261`）：

> **"没法问"不等于"可以"。**

### 两种批准来源被分别记录

| 来源 | 含义 |
| --- | --- |
| `approval.operator` | **一个人点了按钮**（接口本身即批准） |
| `approval.granted` | **上游策略 / 其它通道批准** |

区分它们是因为（`executor.go:252-276`）：

> **"有人能批准"和"这次确实有人批准了"在事后追溯里是两句话。**

### 「执行过」的含义被收紧过

`Result.Executed` 取自 `actionloop.Outcome.Calls`（`executor.go:311`），而 `Calls` **只在唯一一处真正下发调用的地方自增**。

**修复前的缺陷**是 `Executed` 在循环返回后**无条件为 `true`**，于是未配决策器时循环以 `BLOCKED` 收尾、**一次调用都没有**，控制台却显示：

```
executed: true, verified: true
```

**"已执行并复验通过"**（`docs/architecture/recovery-agent.md:233-243`）

（这是第 2 章修复 3，本章从恢复执行的视角再看一次。）

### 复验：判据与它的现状

**判据在接口上写成端口**（`executor.go:84-89`）：

```go
type Verify func(ctx, action, outcome) (Verdict, error)
```

注释：

> **A recovery action that says it worked while the fault is still there is the exact thing
> this package exists to avoid recording as success.**

**三条执行结果被严格区分**：

| 情况 | 处理 |
| --- | --- |
| **没执行** | **根本不复验**，写"没有执行任何动作，因此没有可复验的结果"（`:328-332`） |
| 执行了但**没配复验** | 写"**已执行但未确认**"（`:333-337`） |
| `verdict.Verified` 为真 | 记 `verified` |

### ⚠️ 但生产组合根里 `Verify` 是空的

`cmd/local-agent/main.go:339-403` 构造 `recoveryexec.Executor` 时设置了 `Registry` / `Observer` / `Decider`，**没有 `Verify` 字段**。

**文档自己承认这是故意留的**（`docs/architecture/recovery-agent.md:301-303`）：

> **一个读不懂效果的复验器比没有复验器更糟**，因为它会把"**没看出问题**"说成"**已恢复**"。
> 要接就得先定义每个动作的"好"长什么样。

**教学要点**：这是本章第三次遇到"**空实现比错实现好**"的判断（前两次：`RecoveryFacts` 的空列表、故障表的自相矛盾）。

| | 空实现 | 错实现 |
| --- | --- | --- |
| 后果 | "已执行但**未确认**" | "**已恢复**" |
| 运维知道吗 | **知道** | **不知道** |

**"未确认"是一个诚实的答案。** 而一个占位的复验器会给出一个**看起来更好的错误答案**。

**所以"每一次恢复都报未确认"是当前的真实状态，不是 bug。**

**而这也解释了另一件事**（第 13.3 节）：**跨机经验无法积累的根因**——因为没有任何动作在判定"这一次恢复到底有没有效果"。

### 真实端口上按过的结果

（`docs/architecture/recovery-agent.md:244-254`，端口 8895/8897，**未配模型**）

| 动作 | 结果 |
| --- | --- |
| `estop.release` | `409 RECOVERY_ACTION_REFUSED` + **目录自己的拒绝理由** |
| `robot.take_over` | `404 RECOVERY_ACTION_UNKNOWN`（目录里没有的 id **在控制台就被挡**） |
| `observe.re-read` | `executed=false`，轨迹 `tools.resolved → not-executed → verification.not-applicable` |
| `map.activate` | 轨迹含 `approval.operator`，未配决策器故未执行 |

**这四条覆盖了四种不同的"没做"**：

| 码 | 含义 |
| --- | --- |
| `RECOVERY_ACTION_REFUSED` | **目录说这个动作不允许** |
| `RECOVERY_ACTION_UNKNOWN` | **目录里没有这个 id** |
| `not-executed` | **批准了，但没有决策器** |
| （未执行，有批准） | 同上 |

**四种都有明确的原因码。**

### 超时是 3 分钟，理由很具体

`recovery_execute.go:60`：

> **a timeout that fired mid-motion would report a failure for something still happening** —
> the operator would then be looking at a robot that is moving while the console says it failed.

**"一个在运动中触发的超时会为一个仍在发生的事报告失败——操作员会看着一台正在移动的机器人，而控制台说它失败了。"**

**教学要点**：这是第 1 章"物理动作的完成判定"在**超时**上的投影。

**一个物理动作的超时不是一个"失败"，而是一个"未知"。** 而把它报成失败，是给操作员一个**错误的确定感**。

### 信任边界被明确收窄

`recovery_execute.go:29-44` 说得非常清楚：

> 这个端点由**控制台会话**授权（本进程 session token，constant-time 比较），
> **"这是一个比『有人批准了』更窄的声明，而它是这段代码有资格做的声明。"**
>
> 控制台没有用户账号，**在这里发明一个身份会比 Fleet 已有的真实鉴权更弱**。

**所以记录的是会话，不是人。**

**教学要点**：这是本章最好的一处"**知道自己能证明什么**"。

一个没有用户系统的控制台，有两条路：

| 做法 | 声明 |
| --- | --- |
| 发明一个"操作员身份" | "有人批准了"——**但这是假的** |
| **记录会话** | "**这个控制台会话批准了**"——**这是真的，而且更弱** |

**选择更弱的真声明，而不是更强的假声明。**

---

## 11.7 UI 层：横幅负责有事找你，页面负责怎么办

### 分工写在代码里

`web/problems.js:1-19`：

> The banner answers "**is anything wrong right now**", and it has to answer that
> in the corner of whatever the user was doing. …
>
> A surface that must be **glanceable** cannot also be a **worklist**, and trying to make it both
> produced the state this page replaces — **276 rows in a strip above the task input,
> which nobody could review and so nobody read.**

**"一个必须能一眼扫过的界面，不能同时是一个工作清单。"**

### 两个数字都要给

页面标题是"N 个问题"，副标题分列：

```
需要判断 / 需要批准 / 系统可自行处理 + 共 M 条报告
```

理由（`web/app.js:5260-5265`）：

> **7 problems that took 276 reports to describe is a system that is noisy,**
> and hiding either number would make the console look like it had simply lost data.

**"7 个问题花了 276 条报告来描述，说明这个系统很吵。"**

**而隐藏任一个数字，都会让控制台看起来像丢了数据。**

**教学要点**：这是一个**双向诚实**的设计。

| 只显示 | 读者会以为 |
| --- | --- |
| 只显示"7 个问题" | 控制台很干净 |
| 只显示"276 条报告" | 控制台坏了 |
| **两个都显示** | **"7 个问题，但它们产生了 276 条报告"** ← 这才是真相 |

### 「279 条报告变成 7 个问题」是怎么做到的

**按 `code@component` 聚类。** 实现是 `tasks/alert_groups.go:107 GroupAgentAlerts`。

**分组键 = `Identity` = `code@component`**（`:43-45`）。

**载荷完全没有 id 时才降级用 `Code`**，并注释说明（`:112-117`）：

> **inventing one would merge unrelated problems**

**"凭空造一个会合并不相关的问题。"**

### ⚠️ 这个数字有多个版本，必须分清

**这是本书里第四个必须精确处理的数字。**

| 出处 | 数字 |
| --- | --- |
| commit `0774468f1` **标题** | **279 → 7** |
| 同 commit **正文实测** | **120 → 7** |
| `docs/development/2026-09-18-system-review-and-improvement-plan.md:417` | **279 → 7** |
| `tasks/alert_groups.go:34-41` | **276 → 8** |
| `web/app.js:5218,6215`、`web/problems.js:9` | **276** |
| `tasks/alerts.go:266-270` | **276 → 92**（**分组前**的数字） |

**（推断）差异来自不同时刻的部署快照**：

- `276/92` 是**分组前**的投影规模；
- `279/7` 是**分组后**的一次实测；
- `120/7` 是分组逻辑改好当次的实测。

**书里应写**：

> **"数百条报告降到个位数问题"**，并把 **279 → 7** 作为那一次实测记录，同时注明代码注释里另有 **276 → 8**。

**教学要点**：这是本书第四次遇到"同一个指标有多个数值"（前三次：七类/八类、工具数、故障码数）。

**规律**：**凡是一个"改善前 → 改善后"的数字对，都要问清楚它是不是同一次测量。**

### 那三个身份缺陷（本节最有教学价值的部分）

commit `0774468f1` 修掉了**三处身份缺陷**：

**缺陷 1：一个问题的三个阶段被当成三个问题**

`ops.anomaly_detected` / `ops.root_cause_hypothesis` / `ops.escalation_required` 是**同一发现的三个阶段**，而 ops agent 给后两者加了 `hyp-` / `esc-` 前缀，投影**原样保留**。

**后果**：一次异常占**三行**。

**缺陷 2：`AnomalyReportID` 的 `#count` 后缀把一个问题切成多个**

> **"那个后缀是'什么时候再喊一次'，不是'问题是什么'。"**

按**报告 id** 分组时，`ANOMALY_ABNORMAL_TASK@task` 被拆成 **6 个组**（count 25–30）。

**缺陷 3：升级事件的两行是空的**

内容在 `context` 和 `reason` 里，而**投影只读 `code`/`message`**，控制台上是 `code:null` 和字面量 `'None'`——

> **"最需要人读的那几行恰恰什么都没有。"**

**修法**：`tasks/alerts.go` 的 `rootIdentity()`（`:275-289`）在**同一个函数里**剥离阶段前缀与 `#count` 后缀，**边界复用 `agentcontract.SameAnomaly` 的同一规则**。

**教学要点**：**三个缺陷都是"身份"问题。**

| 缺陷 | 身份错在哪 |
| --- | --- |
| 1 | **同一个问题的不同阶段有不同的身份** |
| 2 | **"喊第几次"被当成了身份的一部分** |
| 3 | **身份对了，但内容没被投影** |

**而修法的关键**是"**在同一个函数里剥离**"——如果分两处剥离，它们会漂移。

**这与第 6 章 `AnomalyIdentity` 那个"11 个组件读同一份计划"的缺陷是同一条**：

> **"这个异常是谁"这个问题，全系统必须只有一个答案。**

### 排序是审核顺序

`:177-198` 的排序：

```
Active
  → RobotWide（:184-187）
  → Handling（human=2 / approval=1 / automatic=0）
  → severity
  → TaskCount
  → Identity 字典序（兜底）
```

**`RobotWide` 的位置**（`:184-187`）：

> **"A finding about the robot leads every finding about its work"**
>
> （这条**曾差点在分组时丢掉**。）

**最后那个"字典序兜底"也值得注意**：

> 否则**等价问题会在刷新间互换位置**。

**教学要点**：**排序必须是全序。** 如果两个元素在所有维度上相等，它们的位置就取决于排序算法的实现——**于是每次刷新页面，同样的两个问题会换位置**。

而这对审核界面是致命的：**操作员会以为出现了新问题。**

### `handling` 由计划推出，不由看法推出

`:202-232`：

| `handling` | 判定 |
| --- | --- |
| `automatic` | **计划每步只读** |
| `approval` | **有任一步需批准** |
| `human` | 无计划、`Verdict == "ESCALATE"`、或计划无步骤 |

> **"判定规则与自动通道执行的规则是同一条，从另一侧读。"**

**教学要点**：这是本章最好的一个设计陈述。

控制台显示"系统可自行处理"和自动通道**实际会处理它**，用的是**同一条规则**。

| 做法 | 风险 |
| --- | --- |
| 控制台自己判断"这个看起来能自动处理" | **它会和实际行为不一致** |
| **从同一份计划读出** | 显示什么就一定会做什么 |

**这是"显示"和"行为"之间应该有的关系。**

---

## 11.8 与通用 coding agent 的运维差异

| 维度 | coding agent | 机器人 agent |
| --- | --- | --- |
| **失败的样子** | 测试红了 | **机器人卡在走廊** / **抓着杯子不动了** |
| **失败的可见性** | 日志 + 退出码 | **可能完全静默**（"沉默被读成健康"） |
| **人工介入** | 看日志、改代码 | **必须有一个"等人来处理"的队列项** |
| **恢复动作** | 重跑 | **有目录、有风险级、要批准** |
| **恢复的验证** | 重跑测试 | **必须有新鲜证据**（而这一步现在没接线） |
| **审计单位** | 一次运行 | **一次任务 + 每一步的证据** |
| **"没做"的记录** | 通常不记 | **必须记**（跳过也记录） |

### 最核心的一条差异

> **coding agent 的失败通常是"可读的"；机器人 agent 的失败可能是"沉默的"。**

这决定了三个设计：

| 设计 | 对应哪个沉默 |
| --- | --- |
| **字段白名单**（第 6 章） | "观察者没跑起来" |
| **跳过也记录**（9.4） | "系统看过并决定不碰" |
| **`Health()` 返回 DEGRADED**（第 6 章） | "看不见的观察者不是健康的" |

**共同点**：**它们都在阻止"没有消息"被读成"好消息"。**

---

## 11.9 教学要点

### 一道可以从数字下手的练习

给学生这张表：

| 出处 | 数字 |
| --- | --- |
| commit 标题 | 279 → 7 |
| 同 commit 正文 | 120 → 7 |
| `alert_groups.go` 注释 | 276 → 8 |
| `alerts.go` 注释 | 276 → 92 |

问：**这四个数字能同时为真吗？如果能，请给出一个解释。**

<details>
<summary>答案要点</summary>

**能。**

**（推断）它们来自不同时刻的部署快照，而且有两个不同的"分组前/分组后"口径**：

| 数字对 | 口径 |
| --- | --- |
| **276 → 92** | **分组前**的投影规模（276 条报告，92 个不同的问题） |
| **276 → 8** | **分组后**的问题数（`alert_groups.go` 的参考部署） |
| **279 → 7** | 另一次实测（commit 标题与文档） |
| **120 → 7** | 分组逻辑改好当次的实测（commit 正文） |

**关键区分**：

- "276 条报告"和"92 个问题"是**同一时刻的两个层面**；
- "279 → 7"和"120 → 7"是**不同时刻的两次测量**，问题数都是 7 但报告数不同。

**要教的是**：

> **凡是一个"改善前 → 改善后"的数字对，都要问清楚它是不是同一次测量。**

**加分点**：指出书里应该写"**数百条报告降到个位数问题**"，而不是挑一个数字当权威——因为**没有任何一个数字是"当前的"**，它们都是历史快照。

**再加分**：指出 `alert_groups.go:34-41` 的注释**自己说明了参考部署的规模**——这是好实践：**一个数字带着它的测量条件一起被记录下来。**

</details>

### 六道练习题

**练习 1：为什么 bundle 和 incident 要分成两个 schema？**

<details>
<summary>答案要点</summary>

**理由（源码头注释）**：

> **故障族知识只能有一处**，放在 Python 的 `FAMILIES` 表里；
> **另一种语言里的第二份拷贝必然漂移。**

**但更根本的理由是"事实"与"诊断"必须可分别验证**：

| | 怎么验证 |
| --- | --- |
| bundle（**只有事实**） | 它引用的每个 observation ID 都能查到 |
| incident（**事实 + 诊断**） | 分类规则可以被单测覆盖 |

**如果把两者合并**：你无法判断记录里的一句话是**观测到的**还是**推断出的**。

**而这恰恰是第 6 章 `OpsAgent` 的四类事件分工在讲的同一件事**：

> anomaly 说**事实**，hypothesis 说**结论**。合成一个事件，读者就分不清哪句是观测、哪句是推断。

**加分点**：指出 bundle 的 `note` 字段自己声明了这一点：

> "本记录只包含事实：状态、事件、步骤、证据引用、耗时与修订。…"

**一个数据文件自己说明"我不包含什么"，是一个好实践。**

**再加分**：指出这两份记录还有**落盘位置不同**（`artifacts/incidents/` vs `--output` 目录）与**保留策略不同**（200 份 vs 手动）——因为它们的**产出频率和用途不同**。

</details>

**练习 2：`rule.reconcile-first` 曾经把自己锁死。请说明这个死锁的完整结构，并指出它与第 5 章 `needsScope` 那条注释的共同点。**

<details>
<summary>答案要点</summary>

**死锁结构**：

```
契约：结果未知时不得改动机器人
   ↓
实现：一律 escalate，且不带任何步骤
   ↓
但「对账」本身也是一个动作（一次只读观测）
   ↓
它被「不得改动」的规则禁止了
   ↓
系统能发现「世界状态不明」，但永远无法自己离开这个状态
```

**关键洞察**：

> **完成对账的动作本身就是一个只读观测。**
> escalate 会丢弃所有步骤，于是**连只读的观测也被丢掉了**。

**修法**：改为提出目录里匹配的**只读**动作；且在这一支里**模型根本不会被咨询**。

**与 `needsScope` 的共同点**（第 5 章）：

```go
// internal/actionloop/loop.go:628-635
// Only physical calls are. A read-only call cannot change the world, and a local
// side effect does not reach the robot; bounding those would stop the loop from
// even looking, which is not a safety property but an outage.
```

> **"把『看』也纳入范围不是安全属性，是一次停机。"**

**两者是同一个洞察**：

> **安全约束的作用域必须精确。过宽的安全机制会变成故障源。**

**同类例子（项目里真实的）**：

1. **`blindRadiusM` 一个常数承担两个职责**（第 10 章）——建模盲区 + 阻止追自己的脚印，于是必然在其中一个户型上做错；
2. **`_swept_model_collision` 从当前位姿开始采样**（第 4 章）——离墙 0.373 m vs 包络 0.375 m，差 2 mm，机器人再也动不了。

**"这不是安全属性，是陷阱。"**

</details>

**练习 3：为什么 sweep 的"未归类"要计入非零退出码？**

<details>
<summary>答案要点</summary>

**因为一个只打印报告的工具，它的输出会被忽略。**

| 做法 | 后果 |
| --- | --- |
| 打印一份报告 | "26 份未归类"**在某次运行的输出里**，没人看 |
| **计入退出码** | **CI 会红**，或者巡检告警会响 |

**本次实测**：`--sweep artifacts/incidents` → 64 份，未归类 26 份，**exit 1**。

**这 26 份里有一份是真问题**：`GRASP_NOT_REACHED` 在 Go 侧归 `PERCEPTION`，而 Python 的 `FAMILIES` 八族**没有任何一族包含它**——**两套词表漂移了**。

**如果退出码是 0**，这个漂移会一直存在，直到有人在某次排查里偶然看到。

**教学要点**：

> **一个"未归类"的计数，它本身是一个需要被处理的信号，而不是一个统计数字。**

**同类设计**：

- 第 6 章：`Dropped()` **应保持为 0**，非 0 意味着某个 Agent 太慢；
- 第 6 章：`LLMRejectionCount` 是一个可观测指标；
- 第 3 章：`classification_coverage_test.go` 是一个**会失败的测试**。

**共同点**：**让"应该有零个"的东西变成"非零就报警"。**

**加分点**：指出"sweep 报未归类"这件事**本身证明了分类表有守卫的价值**——如果没有 sweep，`GRASP_NOT_REACHED` 的漂移**永远不会被发现**。

</details>

**练习 4（空白区）**

> 自动恢复引擎在 52 个任务里成功了 27 次（`observe.re-read` 21 次、`execution.read-history` 6 次），**而且没有碰过机器人**。
> 但文档说："**它没有消除任何一条发现，这是能力边界而不是失败。**"
> 请解释这两句话为什么不矛盾，并说明这个"能力边界"具体在哪里。

<details>
<summary>答案要点</summary>

**为什么不矛盾**：

27 次的是**执行成功**（动作被正确执行、没有碰机器人）。

而"**消除发现**"要求的是**判定世界状态**。

**这两件事完全不同**。

**能力边界具体在哪里**：

> `telemetry.read` 读的是机器人**当前**状态，
> **回答不了"那一步当时完成了没有"**。
>
> 要让发现真正消解，需要有人**依据证据判定世界状态**，
> 而**目录里没有任何动作做这件事**。

**换句话说**：

| 问题 | 谁能回答 |
| --- | --- |
| "机器人现在在哪、手里有什么" | **`telemetry.read`（只读，可自动）** |
| "第 7 步的 `pick` 当时成功了吗" | **只有人（`StepReconciliation`）** |

**第二个问题需要的是"对账"，而它的判据是"那条证据链"，不是"当前状态"。**

**加分点**：指出这与第 3 章的 `StepReconciliation` 完全一致——**对账只能由人做，写入是一次性的**（`WHERE reconcile_outcome = ''`）。

**再加分**：指出这个"能力边界"与**跨机经验无法积累**是同一个根因——**因为没有任何动作在判定"这一次恢复到底有没有效果"**（9.6：`Verify` 端口没接线）。

**一个不自证效果的恢复系统，无法积累经验。**

</details>

**练习 5（信任边界）**

> `recovery_execute.go:29-44` 说这个端点由**控制台会话**授权，并明确写：
> "**这是一个比『有人批准了』更窄的声明，而它是这段代码有资格做的声明。**"
> 请说明为什么"更窄的声明"是更好的选择，并给出一个反例。

<details>
<summary>答案要点</summary>

**为什么更好**：

| 做法 | 声明 | 真假 |
| --- | --- | --- |
| 发明一个"操作员身份" | "**有人**批准了" | **假**——因为没有用户系统 |
| **记录会话** | "**这个控制台会话**批准了" | **真**，而且更弱 |

**关键判断**：

> **在这里发明一个身份会比 Fleet 已有的真实鉴权更弱。**

因为 Fleet **有**真实的鉴权（操作员 JWT），而控制台**没有**。在控制台发明一个身份，会产生两个不可比的"身份"概念。

**反例（几种可能的回答）**：

1. **一个"记住我"的勾选框**：它声称"这个用户已登录"，而实际上只验证了一个浏览器 cookie。**后果**：审计记录里有"用户 A"，但任何人都可以是 A。
2. **一个自签的"管理员令牌"**：它看起来像一个正式凭证，但没有信任根。**后果**：运维会把它的存在当作一个安全边界。
3. **把"内部网络"当作身份**：声称"来自内网的请求是可信的"。**后果**：任何能访问内网的进程都成了管理员。

**教学要点**：

> **一个系统应该只声明它有能力证明的东西。**
> **一个更强的假声明，比一个更弱的真声明危险得多。**

**加分点**：指出这与第 10 章 `sim2real.py` 的 `LIMITATION` 常量是同一种做法：

> "**仅检查接入资料及人工记录，不验证实机效果，不授权电机运动**"

**一个工具自己说明"我不能证明什么"，是这个工具可信的前提。**

</details>

**练习 6（显示与行为）**

> 控制台的 `handling` 字段（`automatic` / `approval` / `human`）是**从计划推出的**，而不是从"看起来能不能自动处理"判断的。
> 请说明为什么这一点重要，并给出一个反例。

<details>
<summary>答案要点</summary>

**注释原话**：

> **"判定规则与自动通道执行的规则是同一条，从另一侧读。"**

**为什么重要**：

| 做法 | 风险 |
| --- | --- |
| 控制台**自己判断**"这个看起来能自动处理" | **它会和实际行为不一致** |
| **从同一份计划读出** | **显示什么就一定会做什么** |

**不一致的后果**：控制台显示"系统可自行处理"，操作员就不管了——而**系统实际上不会处理它**（因为计划里有一步需要批准）。**问题静默地留在那里。**

**反例（几种）**：

1. **一个"自动重试"按钮**：界面上显示"已自动重试 3 次"，但后端的重试逻辑因为某个开关关闭而没跑。
2. **一个"已同步"的徽章**：前端根据"上次请求成功"显示已同步，而后端的同步任务其实失败了。
3. **一个"不需要审批"的提示**：前端根据工具名判断，而后端的审批门禁根据 manifest 的安全级别判断——**两者可能不一致**。

（第 3 个是项目里真实防住的：`needsApproval` 从 manifest 派生，**不询问工具自己**。）

**教学要点**：

> **"显示"和"行为"必须从同一个权威源派生。**
> **任何"界面自己判断一次"的实现，都会在某一天和行为分叉。**

**加分点**：指出这个设计的**代价**——控制台必须能读到计划。如果计划在别处，界面就得等一个异步请求。

**而项目选择了"等"，而不是"自己猜"。**

</details>

### 学生最容易误解的四个点

| # | 误解 | 纠正 |
| --- | --- | --- |
| 1 | "有 `incident.v1` 这个 schema" | **有两个**：Go 的 `incident.bundle.v1`（**只有事实**）与 Python 的 `incident.v1`（事实 + 诊断）。职责相反 |
| 2 | "113 个运行时故障码" | **113 是 2026-09-17 那次审计的规模**。今天：`runtimeEmittedCodes` **119 唯一码**（123 条目），分类表 **177 唯一码**（179 条目） |
| 3 | "279 条报告变成 7 个问题" | 这个数字有**四个版本**（279→7 / 120→7 / 276→8 / 276→92）。应写"**数百条降到个位数**"，并注明是历史快照 |
| 4 | "自动恢复在修问题" | **它没有消除任何一条发现**——这是**能力边界**，不是失败。`telemetry.read` 回答不了"那一步当时完成了没有" |

---

## 11.10 本章小结

1. **事实与诊断分成两个 schema。** bundle 只含事实（"不分类、不推测、不建议"），incident 含诊断。理由是**故障族知识只能有一处**——而这条原则后来被咬过一次（两套词表漂移）。

2. **`GRASP_NOT_REACHED` 的漂移是一个真实未修缺陷**：Go 侧归 `PERCEPTION`，Python `FAMILIES` 八族**无一包含它**。**同一概念两份定义的通用解法，这个项目自己已经有了**（`tools.json` 从代码生成 + `--check`）。

3. **"一份自相矛盾的故障表比没有故障表更危险"**——因为它会让大脑**以为某个模块是好的**。所以 `Validate()` 拒绝九类自相矛盾的报告。

4. **`rule.reconcile-first` 曾经把自己锁死**：完成对账的动作本身就是一个只读观测，而 escalate 会丢弃所有步骤。**这和第 5 章"把『看』也纳入范围不是安全属性，是一次停机"是同一个洞察。**

5. **自动恢复跑了 27 次、全部成功、没碰过机器人——但没有消除任何一条发现。** 这是**能力边界**，不是失败。原因是对账需要"依据证据判定世界状态"，而**目录里没有任何动作做这件事**。

6. **"没看出问题"和"已恢复"必须能被区分。** 生产组合根里 `Verify` 是空的，所以**"每一次恢复都报未确认"是当前的真实状态**——而一个读不懂效果的复验器会把它说成"已恢复"。

7. **横幅负责有事找你，页面负责怎么办。** 而控制台的 `handling` 字段**从计划推出**，与自动通道执行的规则是同一条——**显示什么就一定会做什么**。

---

## 11.11 源码索引

### 事故记录

| 内容 | 位置 |
| --- | --- |
| `incident.bundle.v1` 定义 | `incidents/bundle.go:28` |
| **"故障族知识只能有一处"** | `incidents/bundle.go:5-9` |
| 字段全集 | `incidents/bundle.go:39-123` |
| best-effort 契约 | `incidents/bundle.go:11-12,151` |
| write-then-rename | `incidents/bundle.go:182-190` |
| `DefaultKeep = 200` 与 prune | `incidents/bundle.go:35,210-241` |
| **唯一写入点与调用点** | `internal/localapp/app.go:486,618`（异常分支 `:604-618`） |
| 写失败只 log | `app.go:533-534` |
| `TANGYING_INCIDENT_DIR` | `cmd/local-agent/main.go:420,576-581` |
| Python 侧 `incident.v1` | `scripts/diagnose_task.py:37` |
| 8 个故障族与五个字段 | `scripts/diagnose_task.py:42-187` |

### 分类与守卫

| 内容 | 位置 |
| --- | --- |
| 有序分类表 | `core/closedloop/closedloop.go:83-274` |
| 八个类 | `closedloop.go:48-68` |
| `Classify` 兜底 | `closedloop.go:289-300` |
| `Knows` | `closedloop.go:302-322` |
| `Retryable` | `closedloop.go:333-340` |
| 覆盖率守卫（10 个测试） | `core/closedloop/classification_coverage_test.go` |
| `runtimeFailureCodes` | `classification_coverage_test.go:39-43,55-70` |
| **把危险本身钉住** | `classification_coverage_test.go:77` |
| 用 `Knows()` 而非 `Classify()` 判成员 | `classification_coverage_test.go:89,104-113` |
| 行为断言组 | `classification_coverage_test.go:331-395` |

### 故障发布

| 内容 | 位置 |
| --- | --- |
| `Fault` / `FaultReport` | `core/robotcontract/faults.go:16-47` |
| **"info is news, not a capability loss"** | `faults.go:70-80` |
| `Validate()` 九类拒绝 | `faults.go:103-155` |
| **"自相矛盾比没有更危险"** | `faults.go:183-185` |
| 能力影响判定 | `robot/gateway/tangying_robot_gateway/module_faults.py:171-190` |
| `ESCALATION_THRESHOLD = 5` | `module_faults.py:69,132-136` |
| Go 侧门禁带 blocker 列表 | `edge/runtime/runtime.go:161` |
| 对正在跑的任务的影响 | `docs/architecture/module-health-and-faults.md:237-246` |
| 五步落地状态与实测 | `docs/architecture/module-health-and-faults.md:272` |
| **`FaultRemedyEngine` 零调用者** | `docs/architecture/review-agent.md:142` |

### 自动恢复

| 内容 | 位置 |
| --- | --- |
| **那一行分界线** | `internal/autorecovery/supervisor.go:183-189` |
| 线画在哪里的理由 | `supervisor.go:16-23` |
| `DefaultMaxActionsPerPlan = 3` | `supervisor.go:67` |
| 遇不确定即停 | `supervisor.go:153-164` |
| 没有目录就什么都不跑 | `supervisor.go:118` |
| **不伪造同意** | `supervisor.go:199` |
| 跳过也记录 | `supervisor.go:96-109` |

### 恢复目录与执行

| 内容 | 位置 |
| --- | --- |
| 目录的宪法（模型不能发明动作） | `agentruntime/recoverycatalog.go:5-16` |
| 16 条动作 | `recoverycatalog.go:122-229` |
| 三档风险 | `recoverycatalog.go:31-42` |
| **"批准是动作的属性"** | `recoverycatalog.go:19-28` |
| `Tools` / `MovesTools` 的双重作用 | `recoverycatalog.go:62-98` |
| `RequiresApproval` / `Executable` | `recoverycatalog.go:342-356` |
| **`Executable` 的那次修复** | `docs/architecture/recovery-agent.md:210-213` |
| `rule.reconcile-first` 的自锁与修法 | `docs/architecture/recovery-agent.md:276-284` |
| `ForShapes()` 只返回只读 | `recoverycatalog.go:293-311` |
| **真实执行数据（52 任务）** | `docs/architecture/recovery-agent.md:285-295` |
| 四步链路 | `console/recovery_execute.go:70-135`；`internal/recoveryexec/executor.go:176-345` |
| **四个检查的顺序即安全论证** | `executor.go:200-205,213-304` |
| 两种批准来源 | `executor.go:252-276` |
| `Result.Executed` 的来源 | `executor.go:311` |
| `Verify` 端口与注释 | `executor.go:84-97` |
| 三条执行结果的区分 | `executor.go:328-337` |
| **`Verify` 在生产组合根里是空的** | `cmd/local-agent/main.go:339-403`；`docs/architecture/recovery-agent.md:301-303` |
| 真实端口实测 | `docs/architecture/recovery-agent.md:244-254` |
| 3 分钟超时的理由 | `console/recovery_execute.go:60` |
| **信任边界（会话而非人）** | `recovery_execute.go:29-44` |
| `POST /v1/recovery/execute` 路由 | `console/server.go:199` |

### UI 层

| 内容 | 位置 |
| --- | --- |
| **"横幅 vs 工作清单"** | `web/problems.js:1-19` |
| 两个数字都要给 | `web/app.js:5260-5265` |
| 分组键 = `code@component` | `tasks/alert_groups.go:43-45,107-117` |
| 组内聚合规则 | `alert_groups.go:125-159` |
| **排序是审核顺序** | `alert_groups.go:177-198` |
| `handling` 由计划推出 | `alert_groups.go:202-232` |
| 参考部署规模（276→8） | `alert_groups.go:34-41` |
| 分组前规模（276→92） | `tasks/alerts.go:266-270` |
| **三个身份缺陷的修法** | `tasks/alerts.go:275-289` |
| commit | `0774468f1` |

---

**上一章**：[第 10 章 仿真、真机与 sim2real](../chapters/ch10-sim2real.md) · **下一章**：[第 12 章 评测体系与后训练](../chapters/ch12-evaluation-training.md) —— "先有刻度，再谈自训"，以及三轮实验的真实数字。
