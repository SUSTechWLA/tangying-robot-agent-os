# 《深入理解分布式机器人 Agent 系统》第 13/14/15 章核心素材

> **版本与取证说明（务必先读）**
>
> 本次调研对象是工作区 `/Users/wanglian/Projects/tangying-robot-agent-os`。**任务描述称 v0.6.0，但仓库实际自报 v0.7.0。** 证据：`VERSION:1` = `0.7.0`（已提交），`CHANGELOG.md:7` 有 `## v0.7.0 - 2026-09-21`，`pyproject.toml:6` = `0.7.0`，最新 tag 是 `v0.6.0`。
>
> **调研期间仓库发生了一次提交变化**：开始时 `HEAD = 774bd2a2f`（`git describe` = `v0.6.0-74-g774bd2a2f-dirty`，工作区有 226 个改动路径）；结束时 `HEAD = 8b9683be8`（`feat: prepare v0.7.0 grounded runtime and system evaluation`），工作区变干净。**行号对应 8b9683be8 上的文件内容**，与 v0.6.0 tag 可能有差异。
>
> 所有数字均为本次实测或原文转录；凡未实测而引自文档的，均标明出处。推断性内容标「(推断)」。文档与代码冲突、文档内部前后不一致处一律显式标注。

> **关于篇幅的说明（作者必读）**：这是一份**事实底稿（素材库）**，不是章节正文。任务给出的章节目标是 5000–7000 字，但任务同时要求"所有数字必须来自真实文件""真实数字全部记录""逐条列出"三份清单、以及末尾的完整源码索引——这三项要求叠加后，底稿本身必然超出章节字数。当前的取舍是：**保留全部可核查的数字与 `文件:行号`，并在文末给出压缩到章节篇幅的具体删减建议**。正文成稿时按文末「篇幅说明」压缩即可，无需回到源码重新取证。

---

## 第一部分 可观测性、事故诊断与自动恢复

### 1. `incident.v1`：一个名字，两份不同的记录

书里首先要澄清一件事：**仓库里存在两个都叫 "incident" 的 schema，由两种语言各自产出，职责相反。**

| | `incident.bundle.v1` | `incident.v1` |
| --- | --- | --- |
| 产出者 | Go，`incidents` 包 | Python，`scripts/diagnose_task.py` |
| 定义 | `incidents/bundle.go:28` | `scripts/diagnose_task.py:37` |
| 内容 | **只有事实**：状态、事件、步骤、证据引用、耗时、修订 | 事实 + **带标签的第一次诊断**（故障族、可能根因、先查什么、覆盖测试） |
| 落盘 | `artifacts/incidents/<taskId>.bundle.json`（`bundle.go:31`） | `--output` 目录下 `<taskId>-incident.json` |
| 是否分类 | **不分类、不推测、不建议**（`bundle.go:5-9` 明写） | 分类是它唯一的职责 |
| 是否改动机器人 | 不改 | 不改（`automation.acted` 恒为 `false`） |

这个切分是刻意的，理由写在源码头注释里：**故障族知识只能有一处，放在 Python 的 `FAMILIES` 表里；另一种语言里的第二份拷贝必然漂移**（`incidents/bundle.go:5-9`）。第 3 节会看到这条原则后来被跨语言一致性测试咬过一次。

**真实 bundle 样例**（`artifacts/incidents/task-03ec8cde7cd2a6db8766c7da.bundle.json`，本次实读）：

```json
{
  "schemaVersion": "incident.bundle.v1",
  "task": {"id":"task-03ec8cde7cd2a6db8766c7da","request":"把红色杯子放进右侧收纳盒",
           "adapter":"mujoco","revision":1,"state":"RECOVERABLE_FAILURE",
           "terminalCode":"subtask 1: rpc error: code = Unavailable desc = controlled demo regression failure"},
  "recovery": {"canResume":false,"requiresReconciliation":false,"reasonCode":"NO_RECOVERY_REQUIRED", ...},
  "timeline": [ ... 16 条 ... ], "stepRuns": [], "evidence": [],
  "timing": null, "collectedAt": "2026-09-20T18:55:08.974703+08:00",
  "note": "本记录只包含事实：状态、事件、步骤、证据引用、耗时与修订。…"
}
```

字段全集在 `incidents/bundle.go:39-123`：`Environment`（robotId/adapter/softwareVersion/protocolVersion/catalogRevision/mapId/mapRevision/calibrationRevision/worldRevision）、`StepRun`、`Evidence`（只存 rgb/depth 的 SHA256 引用，不复制图像）、`TimelineEvent`、`Recovery`、`Timing`。

**何时自动产出。** 唯一写入点是 `internal/localapp/app.go:486 recordIncident`，唯一调用点是 `app.go:618`，位于 `run()` 的异常结束分支（`app.go:604-618`）：暂停（`StatePaused`）、可恢复失败（`StateRecoverableFailure`）、操作员取消（`StateCancelled`）、以及 Local Agent 进程停止。成功结束**不写**。

四条工程约束都写在代码里：

- **写入不得改变任务结局**：`app.go:533-534` 写失败只 `log.Printf`；包注释 `bundle.go:11-12`、`bundle.go:151` 把"best effort"写成契约。
- **读者不会看到半份事故**：`bundle.go:182-190` write-then-rename。
- **有界**：`bundle.go:35 DefaultKeep = 200`，`prune()`（`bundle.go:210-241`）按 mtime 保留最新 200 份（commit 5dfa9728e 决定 `artifacts/incidents/*.bundle.json` 不入库，`.gitignore` 加规则；`artifacts/incidents/` 现存 64 份 bundle + 2 份 incident.json + 1 份 system-health.json）。
- **目录可部署**：`TANGYING_INCIDENT_DIR`，装配点 `cmd/local-agent/main.go:420`，解析函数 `main.go:576-581`。

**自动产出 + 自动扫掠的真实成效（本次实测）。** 我实跑了分类器：

```bash
.venv/bin/python scripts/diagnose_task.py --bundle artifacts/incidents/task-03ec8cde...bundle.json --output /tmp/inc-test
# → 故障族：transport_failure（命中 RPC_UNAVAILABLE）
.venv/bin/python scripts/diagnose_task.py --sweep artifacts/incidents
# → 共 64 份，未归类 26 份；退出码 1
```

64 份的分布：`transport_failure` 28、`goal_or_localization_unclear` 10、`unclassified` 26（其中 23 份"无错误码"、1 份 `TOOL_PARAMETERS_INVALID`、1 份 `reconstruction rejected: reconstruction is stale or future dated`、1 份 **`GRASP_NOT_REACHED`**）。

**这里有一个未修的跨语言分歧，必须写进书里**：`GRASP_NOT_REACHED` 在 Go 侧被显式分类为 `PERCEPTION`（`core/closedloop/closedloop.go:176`），而 Python 的 `FAMILIES` 八族里**没有任何一族包含它**，于是 sweep 报"未归类"。commit 5dfa9728e 曾发现"Go 与 Python 对 87 个码判定不一致"并修复了 `tool_layer.py` 的镜像表，但 **`diagnose_task.py` 的故障族表与 `closedloop` 分类表仍是两套独立词表**，二者不同步时就会在巡检报告里表现为"运维以为已覆盖、工具说没覆盖"。

### 2. 确定性故障族分类：113 个码是怎么组织起来的

**分类表在 `core/closedloop/closedloop.go:83-274`**，结构是**有序数组 + 每项一个 code 集合**：

```go
var classification = []struct{ class Class; codes map[string]struct{} }{ ... }
```

顺序敏感，**第一条命中的规则胜出**（`closedloop.go:78-79`）。八个类，按"唯一安全的下一步"划分（`:48-68`）：`TRANSIENT` / `PERCEPTION` / `PLANNING` / `PERMISSION` / `RESOURCE` / `VALIDATION` / `UNKNOWN_OUTCOME` / `FATAL`。只有 `Transient` 与 `Perception` 可重试（`:333-340`），**`UNKNOWN_OUTCOME` 永久禁止自动重试**（`:63-65`）。

本次实测的码数（用脚本解析源码，非目测）：

| 类 | 条目数 |
| --- | ---: |
| UnknownOutcome | 15 |
| Permission | 29 |
| Resource | 9 |
| Perception | 47 |
| Planning | 17 |
| Validation | 27 |
| Transient | 32 |
| Fatal | 3 |
| **合计** | **179 条目 / 177 唯一码** |

两个码各出现两次（`XLEROBOT_MAX_RELATIVE_TARGET`、`XLEROBOT_MAX_ACTION_CHUNK_LENGTH` 同时在 Permission 与 Planning），因为 Permission 在前，实际分类结果落在 Permission —— 重复是无害冗余，但书里应说明"条目数 ≠ 唯一码数"。

**"113"是历史数字，不是当前数字。** 在 commit `c8cae5af2`（"分类表补齐 113 个运行时故障码"）上实测：分类表 162 个唯一码，守卫清单 `runtimeEmittedCodes` **恰好 113 个唯一码**。当前工作区实测：`runtimeEmittedCodes` 是 123 条目 / **119 唯一**（4 个 `NAV_STOW_*` 各重复一次），分类表 177 唯一。**写书时应写"113 是 2026-09-17 那次审计的规模，此后继续增长"。**

**兜底与它的危险。** `Classify()` 对空码或不认识的码一律返回 `UnknownOutcome`（`closedloop.go:289-300`）。这个默认是安全方向（不认识的失败可能真的到了硬件），但它有一个副作用：**"表里没有"和"表里定为不可恢复"看起来完全一样**。为此单独导出了 `Knows()`（`:302-322`），注释把案例写死了：`GRASP_NOT_REACHED` 曾经就是被兜底吞掉的那个。

**契约级守卫 `core/closedloop/classification_coverage_test.go`**（395 行，10 个 `Test` 函数）用三条互补的机制钉住这张表：

1. `runtimeFailureCodes`（`:39-43`）声明"某个码必须仍出现在某个运行时源文件里"，源文件里删掉码 → 测试红（`:55-70`）。
2. `TestACodeTheTableMissesFallsBackToUnknownOutcome`（`:77`）**把危险本身钉住**，而不是钉住"没有危险"。
3. `TestTheClassifierTableCoversCommonRuntimeCodes`（`:89`）用 `Knows()` 而非 `Classify()` 做成员判定，因为 `EXECUTION_OUTCOME_UNKNOWN` 既在表里又归类为 `UnknownOutcome`，比较类别值无法区分"列了"与"没列"（`:104-113`）。
4. 一组行为断言（`:331-395`）：`GRASP_FAILED`/`PLACE_NOT_REACHED` 属 Perception 而 `*_NOT_OBSERVED` 属 UnknownOutcome；真实 stow 碰撞属 UnknownOutcome 而预测碰撞属 Planning；飞行前拒绝属 Validation。

**"消除已知失败被报成结果未知"是怎么达成的。** 触发事件写在 commit `c8cae5af2` 正文里：一次真实导航受阻，`GOAL_NOT_CLEAR`（路径规划预检拒绝，**底盘根本没动**）被报成

```
（UNKNOWN_OUTCOME）建议：不要自动重试：先对账确认这次动作的实际结果
```

原文的评价是："**让操作员去对账一个从未发生的动作，比不报还糟。**"根因不是兜底逻辑错，而是**表缺项**。审计方法本身踩了两次坑，都记在提交正文与 CHANGELOG 里，这是书里极好的方法论材料：

- 第一遍只搜 `ToolResult(False, "...")` 一处字面量 → 找到 53 个，**漏掉整整一类**（走 `ServiceError(...)` 的码）；
- 补上后得 83 个，宣布"全覆盖" → 在真实机器人上又发现 `PLACEMENT_NOT_OBSERVED` 漏网，因为它是作为**字符串参数**传入的（`self._verify_relation(..., "PLACEMENT_NOT_OBSERVED")`）；
- 扩宽到 **5 类证据来源**（ToolResult / ServiceError / 参数传递 / diagnose 故障族 / Go 侧 emit）后得到 **113 个码**。

**Python 侧的故障族表**是另一套结构，在 `scripts/diagnose_task.py:42-187`，共 8 族：`physical_outcome_unknown`、`verification_not_observed`、`goal_or_localization_unclear`、`mapping_session_fault`、`safety_stop`、`grounding_failure`、`transport_failure`、`fleet_consistency`。每族五个字段：`summary` / `codes` / `causes` / `checks` / `resolution` / `tests`。`tests` 里是**真实可运行的测试路径与函数名**——文档明确说"指向不存在的测试比不指更糟"，并有测试断言它们真实存在。

**自动扫掠分类**由 `--sweep <dir>` 实现：把一个目录里所有 `*.bundle.json` 批量分类、打印每条的族与码，**未归类的计入非零退出码**（本次实测 exit 1），因此可直接接 CI 或巡检告警。这就是"异常终态自动产事故包 → 一条命令扫出全部历史故障的分类"的完整闭环。

### 3. 硬件故障发布成观测：从"报出来"到"摘能力"

**契约 = `robot.faults.v1`**，定义在 `core/robotcontract/faults.go`。`Fault`（`:16-30`）字段：`moduleId` / `kind` / `code` / `severity` / `detail` / `occurrences` / `remedy` / `userInstruction` / `detectedAtUnixMS` / `lastSeenUnixMS` / `evidence`。

`FaultReport`（`:35-47`）多两样东西：`UnavailableCapabilities`（排序后的"没了什么"）与 `CapabilityBlockers`（每个能力被哪些故障摘掉）。两者**必须一致**（`:141-153`），因为控制台解释"这台机器人为什么不能导航"读的正是这张 map。

严重度是有序词表 `info < degraded < blocked < safety`（`:53`）；`Blocking()` 排除 `info`，注释写"`info` is news, not a capability loss"（`:70-80`）。`Validate()` 拒绝九类自相矛盾的报告（`:103-155`）：未知字段、`null`、未知严重度/模块种类/处置类、`count` 与条数不符、**头条严重度不是最严重那条**、同一 `module:code` 重复、`capabilityBlockers` 与 `unavailableCapabilities` 互相矛盾、blocker 指向报告里不存在的故障、缺 `detectedAtUnixMS`。理由很硬：**这份文档是能力门禁的输入**（`:183-185`）——"一份自相矛盾的故障表比没有故障表更危险，它会让大脑以为某个模块是好的"。

**摘能力怎么实现。** 三段：

1. **机器人侧判定**：Python `robot/gateway/tangying_robot_gateway/module_faults.py` 的 `capability_impact()`（`:171-190`）——`blocking = [f for f in faults if f.blocks_capability]`；`safety` 级摘掉**全部**能力（`:186`），否则按每个 capability 声明的 `requires` 模块集合匹配（`:190`）。`ESCALATION_THRESHOLD = 5`（`:69`）：同一故障第 5 次**复发**时 `self_recover` 自动升级为 `operator_assist`（`:132-136`）。
2. **随观测发布**：故障作为 `Observation.semantic_state` 的一部分进世界模型，**不需要新总线**（`docs/architecture/module-health-and-faults.md:24-30` 的因果链）。
3. **Go 侧门禁**：`edge/runtime/runtime.go:161` 抛 `ErrCapabilityUnavailable` 并带上 blocker 列表，计划期与运行期都拒绝。

**对正在跑的任务的影响**，文档给了完整分支（`docs/architecture/module-health-and-faults.md:237-246`）：

```
模块故障 → 能力 unavailable → 计划期失败（原因=哪个模块）
                              ├─ 有等价能力：换策略（左臂坏→右臂；底盘坏→原地作业）
                              └─ 否则：任务进入可恢复失败 + 事故记录
                            → 用户面板提示 + LLM 提议 self_recover / 请人处理
```

三条既有语义继续生效：安全级故障 → 任务 `SAFETY_STOPPED` 且**不能由任务动作解除**；结果未知（命令发出后模块失联）→ 步骤保持 `STARTED`、**永不重放**；降级运行属于"任务改版/换策略"，不是新机制。

**实测证据**（文档 `:272`，标为"已完成"）：锁存急停后观测里出现 `estop:EMERGENCY_STOP_LATCHED`（`safety`），`navigation.navigate` / `manipulation.pick` / `observe_scene` 变 `available=false` 且 blockers 指向该故障，而 `emergency_stop` 仍可用；未启用地图的移动场景报 `chassis:NAV_MAP_NOT_READY` 且**只摘导航**。

**必须诚实标注的边界**：设计文档列了五步落地，第 1、2 步"已完成"，第 3、4、5 步**未完成**；机器人侧自愈引擎 `FaultRemedyEngine` "有完整四条约束和测试，但**生产路径里 0 个调用者**"（`docs/architecture/review-agent.md:142`）。另外 `remedyOutcomes` 字段"会被完整传到 Agent，但**没有任何规则解释它**"——传输有测试钉住，"已传输"与"有消费者"是两件事（CHANGELOG 归档自陈）。

### 4. 自动恢复引擎：`internal/autorecovery` 的真实机制

`internal/autorecovery/supervisor.go`（230 行）只做一件事：**跑计划里目录判为 `read_only` 的步骤**。

**"只读步骤无人执行，改动仍等人"在代码里的强制点**是 `supervisor.go:183-189`：

```go
if action.Risk != agentruntime.RiskReadOnly {
    return s.skip(...)   // 这一行就是那条线
}
```

包注释（`:5-23`）把三个"停下来"的原因分开记录，因为它们不同：`bounded_write` 等人（目录说的）、`never_automatic` 任何人都不跑（结构上够不到）、工具缺失跑不了。**线画在"目录说它 read_only"而不是"工具自称不改世界"**：`MutatesWorld` 是服务对自己的声明，风险等级是目录对"运行这个动作意味着什么"的判断；读风险等级让"能不能无人执行"只有一个答案，而且答案在它被评审的地方（`:16-23`）。

三条其它硬约束：

- **上限**：`DefaultMaxActionsPerPlan = 3`（`:67`），理由是"提案者可能以会产出长清单的方式出错"。
- **遇不确定即停**：跑过但 `!Verified`，或跑过但 `!Executed`，立刻 `break`（`:153-164`）——"继续下去等于在一个上一步可能已经改变的世界里决定下一步"。
- **没有目录就什么都不跑**：`Catalog == nil → ErrNoCatalog`（`:118`），"无法检查权限"不等于"拥有权限"。
- **不伪造同意**：自动通道调用执行器时传 `OperatorApproved: false`（`:199`），注释说"Saying otherwise would put a person's consent in the record where there was none"。
- **跳过也记录、跑过的不重复记**（`:96-109`）：只记"跑了什么"的回放无法区分"系统看过并决定不碰"与"系统从没考虑过"。

**恢复动作从哪来：`agentruntime/recoverycatalog.go`。** 目录不是愿望清单，而是**从机器人已声明的服务目录推导**（`ListServices`）。本次实测枚举 `DefaultRecoveryCatalog()` 共 **16 条**：

| 风险类 | 条数 | 动作 id |
| --- | ---: | --- |
| `read_only` | 6 | `observe.re-read`、`nav.read-map`、`map.read-conflicts`、`map.read-status`、`calibration.read`、`execution.read-history` |
| `bounded_write` | 7 | `map.re-survey`、`map.activate`、`nav.re-localize`、`arm.home`、`device.reconnect`、`calibration.run`、`task.retry-step` |
| `never_automatic` | 3 | `estop.release`、`hardware.replug`、`calibration.change` |

（注：`internal/recoveryexec/executor.go:12` 的注释写"a `switch` over the thirteen catalog ids"，与当前 16 条不符——这是注释漂移，不是行为差异。）

每条动作声明 `Tools`（它**只**能调的具名工具）与 `MovesTools`（其中会动机器的）。这两个字段的双重作用写在 `recoverycatalog.go:62-98`：既是"动作→工具"的映射（不是执行器里的 `switch`），**也是批准的边界**。

`RequiresApproval()` = `risk != read_only`（`:342-344`），`Executable()` = `risk == read_only || risk == bounded_write`（`:354-356`）。后者是一次真实修复：原实现写成 `risk != never_automatic`，于是**一个没有声明风险等级的新条目会被判为可执行**——而"要不要问人"恰好由那个字段决定（文档 `docs/architecture/recovery-agent.md:210-213` 记录了这次修复）。

**两条语义细节值得单列**：

- `rule.reconcile-first` 曾经把自己锁死。契约说"结果未知时不得改动机器人"，旧实现是 **escalate 且不带步骤**——读起来正确，跑起来是死锁：**完成对账的动作本身就是一个只读观测**，而 escalate 会丢弃所有步骤。系统能发现"世界状态不明"，却永远无法自己离开这个状态。现在改为提出目录里匹配的**只读**动作，在这一支里模型根本不会被咨询（`docs/architecture/recovery-agent.md:276-284`）。
- `ForShapes()` 只返回只读动作（`:293-311`），因此"确定性路线"结构上不可能提出写动作。

**已落地的实测数据**（`docs/architecture/recovery-agent.md:285-295`，52 个任务）：`observe.re-read` 自动执行成功 **21 次**；`execution.read-history` 成功 **6 次**（接线前 8 次全部因工具缺失失败）；账本 **27 条** `ops.recovery_executed`，`operatorApproved` 为空表示不是人批的。

同一文档立刻给出反面结论，书里必须并列引用：**"它没有消除任何一条发现，这是能力边界而不是失败"**——`telemetry.read` 读的是机器人**当前**状态，回答不了"那一步当时完成了没有"。要让发现真正消解，需要有人依据证据判定世界状态，而目录里没有任何动作做这件事。

### 5. 批准范围：模型不得执行批准时没出现过的物理动作

实现分两处，源码把两个问题**刻意分开**（`internal/actionloop/loop.go:198-207`）：

> "这一次调用可以发生吗" → `Approver`（批准端口）
> "这类事情当初被批准过吗" → `Scope`（批准范围）
>
> 合成一个，等于"有人能批准"就是"授权无上限"：模型只要好好开口要一个操作者从没见过的动作，就能自己扩大授权范围。

```go
type Scope func(tool Tool) bool                      // loop.go:208
func ScopeOf(names ...string) Scope                  // loop.go:215-226
const VerdictOutsideScope = "OUTSIDE_APPROVED_SCOPE" // loop.go:192
```

**只约束物理调用**：`needsScope(tool)` = `tool.SafetyLevel == skills.SafetyPhysical`（`loop.go:628-630`）。只读与 local side effect 不受约束，理由写得很直接——"把'看'也纳入范围不是安全属性，是一次停机"（commit `a634003ba` 正文；`loop.go:281-283`）。**nil scope 拒绝一切物理调用**（`loop.go:276-283`）："一个没有说明被批准了什么范围的部署，什么也没批准。"

**越界不是静默跳过，而是带证据停机**（`loop.go:452-467`）：记录 `OUTSIDE_APPROVED_SCOPE` 裁决，带上**工具名、参数、模型给的理由**，然后 `Escalated = true` 并返回。注释的理由是："对'机器人想做你没批准的事'，有用的回答是那份提案，不是沉默。"

装配点在 `internal/recoveryexec/executor.go:297`：

```go
Scope: actionloop.ScopeOf(request.Action.Tools...)
```

`executor.go:285-296` 说明了为什么要**两层**：第一层是"传给决策器的工具列表里只有该动作声明的名字"（模型看不见就选不到），第二层是 `Scope`。二者防的是不同的改动——如果哪天有人把 `resolve` 放宽成"让模型看到机器人提供的一切"，列表就不再约束任何东西，而 `Scope` 仍然成立。**"去掉它会让批准的含义取决于一个列表恰好是怎么被构造的。"**

### 6. 恢复执行的完整链路：批准一步 → 按目录声明的工具执行 → 复验 → 记录

入口是 `POST /v1/recovery/execute`（路由 `console/server.go:199`，实现 `console/recovery_execute.go`）。四步在代码里的位置：

| 步骤 | 位置 | 关键事实 |
| --- | --- | --- |
| **① 批准** | `console/recovery_execute.go:70-135`；`internal/recoveryexec/executor.go:251-277` | 点击即批准（`OperatorApproved: true`），但**带证据**：`ApprovalEvidence: s.operatorEvidence(r)`（`recovery_execute.go:131-134`） |
| **② 按目录声明的工具执行** | `executor.go:206-320` | 顺序即安全论证：目录可执行 → 工具存在 → 风险决定问不问人 → 在 scope 内跑 |
| **③ 复验** | `executor.go:322-345`；`Verify` 端口 `:84-97` | 结果**不由动作自述**，由 `Verify` 重新读事实 |
| **④ 记录** | `executor.go:176-198` | `Record` 在 wrapper 上而非各 return 上，因为"execute 有六种结束方式，将来加的第七种会静默跳过记录" |

**四个检查的顺序就是安全论证**，`executor.go:200-205` 明写"stated rather than implied"：

1. `request.Action.Executable()`（`:213-220`）——目录拒绝先用目录自己的句子。
2. 声明的工具必须在这台机器人上真的存在（`:231-239`），缺哪个报哪个："不需要重试，需要先把对应的能力接上"。
3. 风险决定要不要问人（`:251-277`）；`RiskReadOnly` 不问，`bounded_write` 必须批准，**没有配置批准入口时不执行**（"没法问"不等于"可以"，`:258-261`）。
4. 执行范围 = 该动作声明的工具（`:281-304`）。

**两种批准来源被分别记录**（`executor.go:252-276`）：`approval.operator`（一个人点了按钮，接口本身即批准）vs `approval.granted`（上游策略/其它通道批准）。区分它们是因为"有人能批准"和"这次确实有人批准了"在事后追溯里是两句话。

**"执行过"的含义被收紧过**：`Result.Executed` 取自 `actionloop.Outcome.Calls`（`executor.go:311`），而 `Calls` 只在唯一一处真正下发调用的地方自增。修复前的缺陷是 `Executed` 在循环返回后无条件为 `true`，于是未配决策器时循环以 `BLOCKED` 收尾、一次调用都没有，控制台却显示 **`executed: true, verified: true`**——"已执行并复验通过"（`docs/architecture/recovery-agent.md:233-243`）。

**复验的判据与它的现状（这是书里必须如实写的一处"已实现 vs 未验证"）**：

- 判据在接口上写成端口：`type Verify func(ctx, action, outcome) (Verdict, error)`，注释是"**A recovery action that says it worked while the fault is still there is the exact thing this package exists to avoid recording as success**"（`executor.go:84-89`）。
- 三条执行结果被严格区分：没执行 → **根本不复验**，写"没有执行任何动作，因此没有可复验的结果"（`:328-332`）；执行了但没配复验 → "已执行但**未确认**"（`:333-337`）；只有 `verdict.Verified` 为真才记 `verified`。
- **但生产组合根里 `Verify` 是空的**。`cmd/local-agent/main.go:339-403` 构造 `recoveryexec.Executor` 时设置了 `Registry` / `Observer` / `Decider`，**没有 `Verify` 字段**。文档自己承认这是故意留的："一个读不懂效果的复验器比没有复验器更糟，因为它会把'没看出问题'说成'已恢复'。要接就得先定义每个动作的'好'长什么样。"（`docs/architecture/recovery-agent.md:301-303`）

**真实端口上按过的结果**（`docs/architecture/recovery-agent.md:244-254`，端口 8895/8897，未配模型）：

| 动作 | 结果 |
| --- | --- |
| `estop.release` | `409 RECOVERY_ACTION_REFUSED` + 目录自己的拒绝理由 |
| `robot.take_over` | `404 RECOVERY_ACTION_UNKNOWN`（目录里没有的 id 在控制台就被挡，`recovery_execute.go:100-105`） |
| `observe.re-read` | `executed=false`，轨迹 `tools.resolved → not-executed → verification.not-applicable`，候选列出 `["telemetry.read"]` |
| `map.activate` | 轨迹含 `approval.operator`，未配决策器故未执行 |

超时是 3 分钟（`recovery_execute.go:60`），理由写得很具体："a timeout that fired mid-motion would report a failure for something still happening — the operator would then be looking at a robot that is moving while the console says it failed"。

**信任边界被明确收窄**：`recovery_execute.go:29-44` 说，这个端点由**控制台会话**授权（本进程 session token，constant-time 比较，`guard.go`），"**这是一个比'有人批准了'更窄的声明，而它是这段代码有资格做的声明**"。控制台没有用户账号，在这里发明一个身份会比 Fleet 已有的真实鉴权更弱。所以记录的是会话，不是人。

### 7. UI 层的三个页面：横幅负责有事找你，页面负责怎么办

**横幅与页面的分工**写在 `web/problems.js:1-19`：

> "The banner answers 'is anything wrong right now', and it has to answer that in the corner of whatever the user was doing. … A surface that must be glanceable cannot also be a worklist, and trying to make it both produced the state this page replaces — **276 rows in a strip above the task input, which nobody could review and so nobody read.**"

页面标题是"N 个问题"，副标题分列"需要判断 / 需要批准 / 系统可自行处理 + 共 M 条报告"。**两个数字都要给**，理由：`web/app.js:5260-5265`——"**7 problems that took 276 reports to describe is a system that is noisy, and hiding either number would make the console look like it had simply lost data.**"

**"279 条报告变成 7 个问题"是怎么做到的：按 `code@component` 聚类。** 实现是 `tasks/alert_groups.go:107 GroupAgentAlerts`。

- **分组键 = `Identity` = `code@component`**（`:43-45`）。载荷完全没有 id 时才降级用 `Code`，并注释说明"**inventing one would merge unrelated problems**"（`:112-117`）。
- 组内聚合：`Count++`；severity 取**最高**（`:125-127`）；stage 取**最远**（`:128-130`，`escalated=2 / hypothesis=1 / detected=0`）；建议/禁止重试/缺证据/恢复计划/调查轨迹**全部取最新一条**（`:138-159`），因为"the newest report is the current wording, and its advice is the advice"。
- **排序是审核顺序**（`:177-198`）：Active → **RobotWide**（`:184-187`，"A finding about the robot leads every finding about its work"，这条曾差点在分组时丢掉）→ Handling（human=2 / approval=1 / automatic=0）→ severity → TaskCount → Identity 字典序（兜底，否则等价问题会在刷新间互换位置）。
- **管理能力 `handling` 由计划而非看法推出**（`:202-232`）：`automatic`（计划每步只读）/ `approval`（有任一步需批准）/ `human`（无计划、`Verdict == "ESCALATE"`、或计划无步骤）。"判定规则与自动通道执行的规则是同一条从另一侧读。"

**"279 → 7"这个数字有两个层面，书里必须分清**（三处原文数字不一致，我逐条列出，不做调和）：

| 出处 | 原话 | 数字 |
| --- | --- | --- |
| commit `0774468f1` 标题 | "问题列表从 279 条报告变成 7 个问题" | 279 → 7 |
| 同 commit 正文实测 | "实测 **120 条报告 → 7 个问题**" | 120 → 7 |
| `docs/development/2026-09-18-system-review-and-improvement-plan.md:417` | "控制台**已经把 279 条报告压成 7 个问题**" | 279 → 7 |
| `tasks/alert_groups.go:34-41` | "The reference deployment reached **276** active findings over 29 tasks, and the distinct problems behind them numbered **eight**" | 276 → 8 |
| `web/app.js:5218`、`web/app.js:6215`、`web/problems.js:9` | 均为 276 | 276 |
| `tasks/alerts.go:266-270` | "276 active findings that were **92** problems" | 276 → 92（**分组前**的数字） |

(推断) 差异来自不同时刻的部署快照：276/92 是**分组前**的投影规模，279/7 是**分组后**的一次实测，120/7 是分组逻辑改好当次的实测。**书里应写"数百条报告降到个位数问题"，并把 279→7 作为那一次实测记录，同时注明代码注释里另有 276→8。**

**那三个身份缺陷**（commit `0774468f1` 正文，这是本节最有教学价值的部分）：

1. **一个问题的三个阶段被当成三个问题**：`ops.anomaly_detected` / `ops.root_cause_hypothesis` / `ops.escalation_required` 是同一发现的三个阶段，而 ops agent 给后两者加了 `hyp-`/`esc-` 前缀，投影原样保留 → 一次异常占三行。
2. **`AnomalyReportID` 的 `#count` 后缀把一个问题切成多个**："那个后缀是'什么时候再喊一次'，不是'问题是什么'。"按报告 id 分组时 `ANOMALY_ABNORMAL_TASK@task` 被拆成 6 个组（count 25–30）。
3. **升级事件的两行是空的**：内容在 `context` 和 `reason` 里，而投影只读 `code`/`message`，控制台上是 `code:null` 和字面量 `'None'`——"**最需要人读的那几行恰恰什么都没有**"。

修法是 `tasks/alerts.go` 的 `rootIdentity()`（`:275-289`）在**同一个函数里**剥离阶段前缀与 `#count` 后缀，边界复用 `agentcontract.SameAnomaly` 的同一规则。另有一处真实缺陷顺手修掉：**分组行里的批准按钮会 POST 空 `taskId`**，接口会接受并归到"没有任务"下，改用组内第一个任务。

**"读不到 != 没有问题"** 这条原则在多处落地：`console/alerts.go:31-35` 的 `supervisionReporter` 是可选的，"a deployment that never started the agent runtime simply has no supervision to report, and the console must say so rather than imply everything is fine"；服务端不发 `groups` 时前端回退按报告列出并**明确标注**（`web/app.js:5223-5246`），理由是"**空横幅长得像'系统健康'，那是最糟的失败方式**"。

### 8. readiness 的锋利边缘："结果未知无清除路径"

**先看设计。** `console/readiness.go:15-34` 解释了为什么要有独立端点而不是收紧 `/healthz`：

> "Liveness asks 'should I restart this process' — a robot in an emergency stop is a healthy process doing its job, and restarting it would be an outage caused by a working safety feature. Readiness asks 'should I offer this to a person' — and being stopped is precisely a reason not to. **Folding them together is how a container ends up in a restart loop because its robot is safely stopped.**"

所以在急停锁存 + 无地图 + 有未知结果步骤的机器人上，`/healthz` 仍返回 ok，`/v1/readiness` 说不。六个检查全部 `Blocking`（`:148-156`）：`supervision`、`robot`、`safety`、`faults`、`map`、`reconciliation`。排序规则（`:177-198`）是"阻塞且要行动 → 要行动 → 阻塞且未知 → 未知 → 通过"，稳定排序，两次读同一状态结果一致。`NextID` 是**第一个**要处理的项，`Ready = NextID == ""`（`:163-170`）。

两条措辞纪律：急停的行动项写**物理动作**而不是按钮（"确认现场安全后，按机器人本体上的复位步骤解除急停。**软件不会替你解除。**"，`:269-271`）；故障的行动项用**机器人自己写的** `userInstruction`（`:305-311`），取不到时才退回"记下故障码，把日志发给支持人员"——"**那句不假装知道故障含义**"。

**"锋利边缘"的原始描述**在 `docs/architecture/readiness.md:93-107`，逐字：

> ## 7. 已知的锋利边缘：结果未知会让 `ready` 长期为 false
>
> 在被测的这台长期开发机上，`reconciliation` 检查列出了 **18 个历史任务**，于是 `ready` 会一直为 false，直到有人逐条确认那些动作到底发生了什么。
>
> 这是**刻意的保守**，但有一个真实的缺口：
>
> - 保守是对的——闭环契约不允许在一个可能已经变过的世界里继续动作，而"结果未知"从任何角度看都还没解决；
> - 但**它没有清除路径**。操作者看过现场、判断那批旧任务已经作废之后，没有任何地方能记录这个判断。**一个清不掉的阻塞项，正是本轮刚修掉的那类死路（安全确认），而且它的下游后果更糟：运维会学会忽略这份报告，而这份报告的全部价值就在于被当真。**
>
> **这不是"再改一行"的问题**，它需要一个策略决定：结果未知应该阻塞"继续那个任务/重试那一步"，还是阻塞"整台机器人执行任何新任务"。两者都说得通，但后者需要一个操作者可用的对账记录入口。在做出这个决定之前，本文如实记录它，而不是把这一项悄悄降级为不阻塞。

**它的下游后果被量化过**（`docs/development/2026-09-18-system-review-and-improvement-plan.md:195-205`），三个数字构成一条因果链：

| # | 数字 | 怎么得到的 |
| --- | --- | --- |
| 1 | 账本 **159,314 条事件里 158,193 条（99.3%）是观察者产生的**，执行事实只有 0.7% | SQL 聚合 |
| 2 | 单个任务产生 **7,737 条事件**，同一异常 `ANOMALY_UNVERIFIED_MUTATION` 重复 **1,410 次**（约每 60 s 一次，跨 33 小时） | `group by task_id, code` |
| 3 | 训练数据流水线的真实输出是 **"可用正样本 0 条"**（`sft 0 / refusals 6 / negative 0`） | 实跑 `bin/training-export` |

原文的小结是："**结果未知没有清除路径 → 观察者每 60 s 重报一次 → 只追加账本被灌满 → 而账本同时是回放源和训练语料 → 回溯、量化、数据集三件事一起失效。**"（放大倍数约 170 倍，发生在那次 Agent 层升级之后；按当前速率约 128 MB/天、47 GB/年。）

**当前状态：代码已经有了清除路径，但 `readiness.md` 没有更新。这是一处必须显式标注的文档/代码冲突。**

- `console/reconcile.go` 实现了 `POST /v1/tasks/{id}/reconcile`，路由注册在 `console/server.go:200-202`（注释："It is a person's conclusion and nothing else can produce one"）。
- 请求体 `{stepId, outcome, note}`，`outcome ∈ HAPPENED | NEVER_ACTED | ABANDONED`；**一个人与一句依据缺一不可**（`:68-72`）："让人继续往下走的那个判断，是后来的人必须能反驳的那个判断。"
- 存储层拒绝覆盖（`middleware/sqlite/store.go:220-244`）：`UPDATE ... WHERE ... AND reconcile_outcome = ''`，`affected == 0` 时报"not awaiting reconciliation, or has already been reconciled"——"**第二个来看的人要看到已经有人决定过，而不是悄悄用自己的判断替换掉**"。
- 判定层生效（`internal/localapp/recovery.go:47`）：已对账的步骤 `continue`，不计入 `UncertainStepIDs`，于是 `RequiresReconciliation` 变假、`CanResume` 可以变真、`readiness` 的 `reconciliation` 检查变绿。注释同时保留了那条纪律：**"步骤自身的状态不变（结果确实未知，记录继续这么说），变的是有人去看过了。"**
- `docs/development/2026-09-18-system-review-and-improvement-plan.md:29,165` 把"未知结果的清除路径（人是唯一的清除者）"记为 ✅ 完成。

而 `docs/architecture/readiness.md` 最后一次修改是 commit `844fc6a2c`（2026-09-17），§7 仍写着"它没有清除路径"，§8 的相关文件表里也没有 `console/reconcile.go`。**书里应写：锋利边缘的诊断成立且至今有效（18 个历史任务的观察、清不掉的阻塞项会让人学会忽略报告），但清除路径已经补上；`readiness.md` §7 是过期文本。**

另有一处小出入可一并标注：`docs/architecture/readiness.md:114` 称 `console/readiness_test.go` 是"七条承诺的测试"，实测该文件有 **10 个 `Test` 函数**（`TestAReadyRobotSaysSo`、`TestARobotWithABlockingFaultIsNotReady`、`TestAnEmergencyStopBlocksUseAndSaysWhoCanClearIt`、`TestARobotThatCannotBeReachedIsAProblemWithANextStep`、`TestARobotThatWasNeverAskedIsUnknownRatherThanBroken`、`TestARobotNobodyIsWatchingIsNotReady`、`TestTheLanguageCapabilityIsStatedPlainly`、`TestTheBlockingProblemIsFirstAndNamed`、`TestTwoReadsOfTheSameStateAgree`、`TestLivenessStaysOkWhileReadinessSaysNo`，`console/readiness_test.go:118-311`）。

---

## 第二部分 评测体系与后训练流水线

### 9. 评测体系：先有刻度，再谈自训

方法论来自 commit `505af6577` 的标题本身——「**编排层评测体系：先有刻度，再谈自训**」。同一条原则在 `docs/architecture/post-training-pipeline.md` 里被写成一句话：

> **判据不能由被判者提供。** 账本量化、拒绝采集、评测、门禁——这四件事都必须是确定的代码；LLM 只做它真正擅长的：诊断、提议、写代码。

**评测脚本清单（`scripts/`，本次实测 9 个 `evaluate_*.py` + 1 个 `gate_*.py`）**：

| 脚本 | 行数 | 评什么（依据脚本自述与 Makefile 调用） |
| --- | ---: | --- |
| `evaluate_agent_context.py` | 58 | 上下文表达主实验（三轮）：诊断/反思/动作三能力 + 多轮 episodes + 消融 |
| `evaluate_agent_stages.py` | 39 | 第二轮：八个决策环节的逐环节表达选型与留出比较 |
| `evaluate_agent_factorial.py` | 48 | 第三轮：四因素（语法/排列/标注/完整性）严格因子试验 |
| `evaluate_agent_structure.py` | 17 | 追加实验 A：层级结构（flat / nested / entity CNL） |
| `evaluate_agent_contract.py` | 17 | 追加实验 B：显式决策契约 2×2 消融 |
| `evaluate_agent_episodes.py` | 26 | 多轮工具执行沙箱（安全完成率 / 轮数 / token） |
| `evaluate_agent_system.py` | 174 | **离线系统证据汇总**，脚本自述："Does not contact models, simulators or robots." |
| `evaluate_natural_language.py` | 151 | 自然语言→计划（编排层），即 `make nl-eval` 的判卷器 |
| `evaluate_slam.py` | 225 | SLAM / 探索覆盖 |
| `gate_agent_context.py` | 32 | **门禁**：候选 checkpoint 对冻结基线，非零退出码 |

九个 `evaluate_*.py` 合计 787 行；另有 11 个配套脚本（gate / ablate / report / confirm / release / audit / verify）。`scripts/report_agent_context.py:13-15` 的默认输出正是 §10 引用那份 860 行实验报告。

**两个"没接线"的事实必须一并写清**：① `grep -n "gate_agent_context" Makefile` **无任何匹配**（exit 1）——门禁脚本**只能手工跑**，全仓非 artifacts 引用只有三处（`docs/development/natural-language-evaluation.md:140`、`docs/experiments/2026-09-21-agent-context-evaluation.md:269`、`orchestration/eval/context_eval/report.py:391`）。② `train/` 与 `training/` **不在 `Makefile:4` 的 `GO_TEST_PACKAGES` 里**，所以 `make test-go` 不会跑这两个包的门禁测试——它们是"建好了但没进 CI"的模块（与第一部分 `scripts/precheck.sh` 的处境同型）。

`orchestration/eval/` 是这些脚本的实现仓：`context_eval/`（22 个模块：`runner`、`scoring`、`factorial_*`（10 个）、`stage_*`（5 个）、`gate.py`、`archive.py`、`episodes.py`、`report.py`、`client.py`、`dataset.py`）与 `system_eval/`（`engine.py`、`adapters.py`、`report.py`）。**脚本只是 CLI 外壳，评分逻辑在库里**——这一点很重要：门禁与训练读的是同一个刻度，不是两套。

`make nl-eval` 是编排层的判卷入口；`Makefile:48 make test` = `test-go test-python test-web`。评测相关的 Makefile 目标按轮次分组（`Makefile:266-327`）：`test-agent-context` / `eval-agent-context{,-episodes,-replay,-report}`、`test-agent-factorial` / `eval-agent-factorial-{replay,formal,audit,report}`、`test-agent-stages` / `eval-agent-stages-{replay,report}`、`test-agent-system` / `eval-agent-system-demo`。注意命名分工：`test-*` 是**回归测试**（Go + pytest），`eval-*` 是**跑评测/报告**，`*-replay` 是**离线重放已有响应、不调用模型**。

**"先有刻度"的具体含义**是把评测做成**先冻结、后运行**：协议、数据、问题、评分器、生产渲染器二进制都在开发选择之前冻结；`selection.json` 写入后不可覆盖；测试集结果不得用于改模板或改标注（`docs/experiments/2026-09-21-agent-context-evaluation.md` §4.3、§12.4）。这套纪律的代价是必须公开负结果——报告里就写着"规划在全部候选中完整字段正确率为 0%"，并且**没有把它藏起来**。

**评测系统自身的设计文档里还有三条极有价值的引用**（`docs/architecture/agent-evaluation-system.md`）：

- **零事故不等于充分安全证据**（`:283-291`）：对独立同分布的簇级事件，零事件时一侧 Clopper–Pearson 上界简化为 `p_upper = 1 − (α')^(1/n)`。"若无多重比较，α=0.05，要将上界压到 1% 至少需要 **299** 个独立同分布单位；要到 0.1% 至少 **2995** 个。**五个种子即使全无事故，上界仍约 45.1%。**"并明确边界："这里的事件是按给定暴露协议定义的簇级事件，**不能转译成实机每动作事故率**。"
- **`demo` 命令不能直接当发布门禁**（`:353`）："`demo` 成功完成报告生成则返回 0，**即使其中候选正确地被判 FAIL**。"可以用的门禁是 `compare`：`0=PASS / 2=FAIL / 3=INCONCLUSIVE`，契约或输入错误非零退出。这条对书里"评测的退出码语义"一节很重要：**同一个 CLI 里，"命令跑完了"和"被测对象通过了"是两件事，必须用不同退出码区分。**
- **维护者要看的三种页面**（`:353`）：能力矩阵（阶段×层级×版本）、实现比较（效应/成本/风险/证据）、失败回放（事件→证据→独立判据→修复）。本次只提供 Markdown/JSON 文件视图，Web 工作台集成为后续工作。

**两处文档内部不一致，引用前需定夺**（如实列出，不做调和）：① `README.md:24` 与 `:141` 两处都写"**28 个工具**"，而 `tools.json` 实测 `tools` 数组 **29 项**（另有 `excluded_from_llm` 2 项）；② 同一个用例 `multiple-objects`（"把红色和蓝色方块放到交接区"）的规划长度，`orchestration/eval/cases.go:98` 记的是 hosted model"correctly and at length (**22 steps**)"，而 `docs/architecture/orchestration-post-training.md:40` 记的是同一个 planner"把'红色和蓝色方块'一次性规划了 **14 步**"。

另有一处测试计数随轮次变化：`tests/eval` 的测试数，文档分别写 39（第二轮）与 49（第三轮），本机实测 `pytest tests/eval --collect-only` = **68**。三个数字都真实，但属于不同时点，书中引用必须标注轮次。

### 10. Agent 上下文三轮实验（本节数字全部来自 `docs/experiments/2026-09-21-agent-context-evaluation.md`，逐字转录）

实验开关是 `TANGYING_AGENT_CONTEXT`。**它的唯一生产读取点是 `core/agentcontext/context.go:76-83`**，且代码接受的取值比实验文档列的多：

```go
func Mode() string {
    switch v := os.Getenv("TANGYING_AGENT_CONTEXT"); v {
    case "json", "nl_sections", "nl_decision", "hybrid", "annotated", "stage", "factorial":
        return v
    default:
        return "legacy"
    }
}
```

`json` 走常量分支（`context.go:137` 的 `style == "json"`），`stage` 则先查表（`context.go:128-130` 的 `style = Resolve(d, "stage").Format`，路由表 `core/agentcontext/routing.go:60-73`，**未知环节回退完整 JSON**），策略表是 `core/agentcontext/stage-policy.json`（42 行，其中 `gate_passed.tool_result = false` 就是 §10 那个门禁回退的落盘结果）。**默认 `legacy` 意味着这整套东西是显式开启的实验配置，不是生产默认。**

#### 第一轮：信息完整性 vs 表示形式

数据：**32 个任务族 × 5 种子 × 3 角色 = 480 个标注案例**；train 8 族/120、dev 8 族/120、test 16 族/240；种子 1729、2718、31415、16180、57721。四组对照：`legacy`（旧摘要投影）/ `json`（完整 Document，语句字段仍是中文自然语言）/ `nl_sections`（同字段、固定中文分节）/ `nl_decision`（约束与依赖前置、按来源类型与新鲜度排序）。**五种子报告均值 ± 样本标准差；主比较先在族内平均配对差，再对 16 个族 bootstrap 10,000 次。**

留出结果：

| 表达 | 角色能力 % | 下一工具及参数正确 % | 反思字段正确 % | 最小参考证据 F1 | 有效输出 % | 平均总 token |
| --- | --- | --- | --- | --- | --- | --- |
| 旧摘要投影 | 8.33 ± 1.47 | 15.00 ± 2.72 | 0.00 ± 0.00 | 0.000 | 84.58% | 1171.6 |
| JSON 事实包 | 74.17 ± 3.16 | 90.83 ± 3.78 | 61.25 ± 1.86 | 0.635 | 100.00% | 2248.0 |
| 分节自然语言 | 67.50 ± 3.16 | 81.25 ± 4.66 | 61.67 ± 4.56 | 0.623 | 98.33% | 2191.4 |
| 决策排序自然语言 | 61.67 ± 5.23 | 75.42 ± 2.28 | 60.42 ± 4.42 | 0.615 | 96.67% | 2194.4 |

分角色：

| 表达 | Ops 诊断 | Reflection 反思 | Recovery 下一步 |
| --- | --- | --- | --- |
| 旧摘要投影 | 13.75% | 0.00% | 11.25% |
| JSON 事实包 | 77.50% | 55.00% | 90.00% |
| 分节自然语言 | 66.25% | 55.00% | 81.25% |
| 决策排序自然语言 | 58.75% | 53.75% | 72.50% |

配对比较：JSON − 旧摘要 **+65.83 pp**，95% CI **[+51.67, +79.17]**，holm p = 0.000092；JSON − 分节 NL **+6.67 pp**，CI [+0.83, +14.17]，**holm p = 0.132812（不显著）**；JSON − 决策排序 NL **+12.50 pp**，CI [+1.25, +24.58]，**holm p = 0.132812（不显著）**。原文特意写："本报告采用预定校正检验，**不据正区间宣称 JSON 显著优于 NL**。"

多轮行为实验（`context-episodes.v1`，8 类问题 × 5 种子 × 4 表达 = **160 条独立轨迹**，最多 10 轮）：

| 表达 | 安全完成 | 每任务轮数 | 无进展轮数 | 平均总 token |
| --- | --- | --- | --- | --- |
| 旧摘要投影 | 100.00% | 5.375 | 0.875 | 3849.1 |
| JSON 事实包 | 100.00% | 4.500 | 0.000 | 7789.7 |
| 分节自然语言 | 100.00% | 4.500 | 0.000 | 7538.9 |
| 决策排序自然语言 | 100.00% | 4.500 | 0.000 | 7540.7 |

四组**最终完成率没有差异（配对 p=1）**；轮数差 −0.875，族级 95% CI [−1.000, −0.625]，探索性 p=0.015625。原文的结论是："**本轮证明了该沙箱中的效率收益，没有证明最终成功率提升。**"

消融（探索性）：从 `nl_decision` 删除 `attempts` 后能力仍 61.67%，差 0，p=1（因为验证语句仍明说"两次相同参数失败"，历史信息存在冗余）；同时删除记录的 scope/时间/supersedes 后能力从 61.67% 降到 52.08%，差 9.58 pp，p=0.105469，**未达 0.05**。

模型与成本：请求模型 `deepseek-v4-flash`；响应统计 `{'deepseek-flash': 2330}`；端点 `https://api.deepseek.com`；temperature=0、max_tokens=512、固定 seed、JSON 对象输出、thinking disabled。**v2 共保存 2330 份不同请求的真实模型响应，累计 4,332,701 token。**

#### 第二轮：按决策环节选择表达

新数据 `stage-cases.v1`：**640 案例**（train 160 / dev 160 / test 320），8 环节，每环节 train 4 族、dev 4 族、test 8 族，各 5 种子（104729、130363、155921、196613、262147）。五组对照：JSON / 分节 NL / 决策排序 NL / **无损混合** / **元数据标注混合**（第五组是"确定性预处理增强，增加了推导结果，不是等信息的纯格式组"）。

留出矩阵（每格 40 案例的完整字段通过率）：

| 环节 | JSON | 分节 NL | 决策排序 NL | 无损混合 | 元数据标注混合 |
| --- | --- | --- | --- | --- | --- |
| 目标与约束 | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| 步骤编排 | 100.00% | 100.00% | 100.00% | 100.00% | 97.50% |
| 工具结果 | 100.00% | 100.00% | 82.50% | 100.00% | 100.00% |
| 证据判读 | 22.50% | 22.50% | 5.00% | 17.50% | 80.00% |
| Ops 诊断 | 35.00% | 45.00% | 60.00% | 35.00% | 100.00% |
| 任务反思 | 70.00% | 70.00% | 30.00% | 50.00% | 37.50% |
| 恢复决策 | 70.00% | 72.50% | 67.50% | 77.50% | 72.50% |
| 断点交接 | 22.50% | 20.00% | 87.50% | 5.00% | 92.50% |

策略总体：

| 策略 | 完整字段通过率 | 危险提议率 | 有效输出率 | 每次总 token |
| --- | --- | --- | --- | --- |
| 全部 JSON | 65.00% | 0.94% | 100.00% | 1379.4 |
| 仅按环节选格式（不含元数据增强） | 74.38% | 2.19% | 99.69% | 1352.3 |
| 开发集最佳统一格式：元数据标注混合 | 85.00% | 0.31% | 100.00% | 1520.1 |
| 开发集分环节策略（含增强） | **87.19%** | 2.19% | 100.00% | 1427.0 |

两个预定总体主比较：`stage_policy − json` **+22.19 pp**，CI [+12.50, +32.19]，holm p = **0.000040**；`stage_policy − best_uniform` **+2.19 pp**，CI [−2.82, +7.19]，holm p = 0.485240。逐环节比较在 8 项 Holm 校正下**没有一项达到 0.05**（最小的两项是证据判读 +57.50 与断点交接 +70.00，holm p 均为 0.062500）。共保存 **2400 份**不同请求的真实响应，累计 **3,326,148 token**。

一个门禁回退被如实记录：**工具结果**环节的开发锁定候选（决策排序 NL）留出成绩 82.50% vs JSON 100.00%，**未通过门禁，保留 JSON**。

**固定发布组合的新种子确认**（3 个从未用过的新种子 99991、999983、1000033，192 个实例、576 条策略结果、456 份模型响应）：

| 固定策略 | 新种子通过率 | 危险提议率 | 平均总 token |
| --- | --- | --- | --- |
| 全部 JSON | 66.67% | 2.60% | 1379.3 |
| 全部标注混合 | 81.77% | 0.52% | 1519.6 |
| 最终内置组合 | **85.42%** | 1.04% | 1429.7 |

`released − json` = **+18.75 pp**，CI [+10.42, +27.60]，holm p = **0.000080**；`released − best_uniform` = +3.65 pp，CI [−0.52, +8.33]，p = 0.191360。原文的诚实收尾：**"危险提议并非零，且略高于全部标注混合组，因此不能宣称它在全部维度支配统一策略。恢复环节在新种子上略低于 JSON，证据判读也明显波动；这些是需要扩大样本和真实任务验证的薄弱项，未通过继续调参把它们隐藏。"**

#### 第三轮：严格信息拆分与两模型因子试验

这是书里最硬的一节。**两模型、反事实双生案例、2⁴ 因子设计、独立确认、形式化检查、真实归档字段覆盖审计。**

因果模型写成 `X →(m) X_m →(e) Z →(π_θ) Ŷ_s`，四因素：

| 因素 | 水平 | 操作定义 |
| --- | --- | --- |
| 语法 | JSON / CNL | 同一 JSON Pointer 原子表；JSON 对象数组或确定性中文句子 |
| 排列 | source / decision | source 为对象键排序及源数组顺序；decision 先保留非记录原子，再按记录类别优先级、较新时间重排 |
| 标注 | raw / derived | 在可见字段上计算作用域、时钟、有效期、显式取代关系，追加原因代码 |
| 完整性 | full / masked | 将一个**经构造证明必要**的字段置 null，并在 `missing` 中声明其路径 |

数据：**480 个完整状态**（train 80 / dev 80 / test 320），每实例 **2 个反事实状态**；两模型产生 dev 1,280 条、test 10,240 条评分行。最终归档引用 **12,484 个不同真实请求**，API 记录总 token **31,716,142**；协议/传输失败 0 个，触发传输重试 6 个。因子+结构+契约阶段合计 15,392 条评分行。

**四因素主效应**（正值代表 CNL、decision 排列、derived 标注、full 信息更好；单位 pp）：

| 模型 | 因素 | 效应 [95% CI] | 原始 p | Holm p |
| --- | --- | --- | --- | --- |
| deepseek-flash | syntax | +0.55 [−0.94, +2.11] | 0.5709 | 1 |
| deepseek-flash | order | +4.30 [+0.47, +8.83] | 0.06114 | 0.2446 |
| deepseek-flash | annotation | +3.20 [+0.15, +6.95] | 0.08346 | 0.2504 |
| deepseek-flash | complete | **+73.55 [+61.41, +84.30]** | 2e-05 | **0.00016** |
| deepseek-v4-pro | syntax | +1.17 [−3.44, +6.48] | 0.6789 | 1 |
| deepseek-v4-pro | order | **−4.30 [−7.42, −1.33]** | 0.01216 | 0.0608 |
| deepseek-v4-pro | annotation | **+5.08 [+1.80, +8.83]** | 0.00652 | **0.03912** |
| deepseek-v4-pro | complete | **+65.51 [+53.40, +76.91]** | 2e-05 | **0.00016** |

**这张表是整个书稿里最有说服力的一处负结果**：语法主效应在两个模型上都不显著（+0.55 / +1.17）；排列效应**在两个模型上符号相反**（flash +4.30，pro −4.30）；只有"把必要字段删掉"是压倒性的（+73.55 / +65.51）。原文对此的措辞是："**不能把上述增益归因于 JSON 语法：旧摘要缺失了关键事实**"（第一轮）与"确定性注释 A=f(X) 不增加信息：I(Y;X,A)=I(Y;X)，但可降低有限模型的计算难度"（命题 4）。

**反事实双生（masked）结果**：

| 模型 / masked | 状态答案正确 | 证据支持正确 | 拒答 | 无支持断言 | 危险建议 |
| --- | --- | --- | --- | --- | --- |
| deepseek-flash | 0.00% | 100.00% | 100.00% | 0.00% | 0.00% |
| deepseek-v4-pro | 0.00% | 100.00% | 100.00% | 0.00% | 0.00% |

两个模型都 100% 拒答并精确指出被擦除路径。原文立刻加了边界："**masked 的高拒答率也不证明模型能在真实世界主动发现未声明的数据缺口。本轮没有隐去 missing 清单的对照。**"

**逐环节最终配置（含门禁回退与"禁止套用"）**：

| 模型 | 环节 | 最终候选 | 确认准确率 | 危险建议率 | 资格 |
| --- | --- | --- | --- | --- | --- |
| flash | 目标 | json/source/raw | 100.00% | 0.00% | 仅显式实验启用 |
| flash | 规划 | nested_json/source/raw | **0.00%** | 0.00% | **禁止套用** |
| flash | 工具结果 | entity_cnl/source/raw | 100.00% | 0.00% | 仅显式实验启用 |
| flash | 验证 | json/decision/raw | 87.50% | **12.50%** | **禁止套用** |
| flash | Ops | entity_cnl/source/raw | 95.83% | 0.00% | 仅显式实验启用 |
| flash | 反思 | entity_cnl/source/raw | 75.00% | 0.00% | 仅显式实验启用 |
| flash | 恢复 | json/source/raw | 70.83% | 4.17% | **禁止套用** |
| flash | 交接 | nested_json/source/raw | 66.67% | 0.00% | 仅显式实验启用 |
| pro | 目标 | entity_cnl/source/raw | 100.00% | 0.00% | 仅显式实验启用 |
| pro | 规划 | nested_json/source/raw | **0.00%** | 0.00% | **禁止套用** |
| pro | 工具结果 | json/source/raw | 100.00% | 0.00% | 仅显式实验启用 |
| pro | 验证 | json/decision/derived | 87.50% | 12.50% | **禁止套用** |
| pro | Ops | entity_cnl/source/raw | 95.83% | 0.00% | 仅显式实验启用 |
| pro | 反思 | json/source/raw | 75.00% | 0.00% | 仅显式实验启用 |
| pro | 恢复 | contract_json/source/raw | 62.50% | **25.00%** | **禁止套用** |
| pro | 交接 | entity_cnl/source/raw | 70.83% | 0.00% | 仅显式实验启用 |

**结论（原文，直接可引用）**：

> "本轮没有找到能够让每个环节都可靠自主运行的语言表达，**不能用最高候选分数掩盖这个负结果**。"

追加实验 A（层级结构）与 B（显式决策契约）也保留了负结果：**规划在 flat / nested / entity CNL 四臂上全部 0.00%**，所以"不能由此断言扁平化导致规划失败"；Pro 的 recovery 加上契约后完整字段从 4.17% 升到 62.50%，**但危险建议率仍有 25%，且族级证据不足以支持显著提升**；Flash 的新增契约候选未过准确率门禁，**回退基线**——"不能将其中一个模型的改善推广到所有模型"。

**六个数学命题**（§13.3）把"能证明什么"划了界，书里值得逐条引用（这是把工程直觉形式化的难得样本）：命题 1 无损表达对理想决策器等价（下确界相等，**但实际实验固定 π_θ**）；命题 2 删掉必要信息会产生不可消除的错误（上限 ≤ ½）；命题 3 相对确定问题集可定义最小充分语义（**不能由此推出最优措辞**）；命题 4 通用最优表达不能仅从信息论推出（构造两个决策器可使排序相反）；命题 5 有限候选集的条件性最优误差界（Hoeffding + union bound：本轮每环节仅 4 个留出族、K=8、δ=0.05 时 2ε≈1.70，**截断到损失范围后仍是平凡界**；要使分布无关界 ≤5 pp，保守需要至少 **4,615 个独立族**）；命题 6 单轮上下文充分不自动保证长期闭环（Bellman 递推界 |V_H − V̄_H| ≤ Hε_r + H(H−1)/2 · R_max·ε_p）。

最后一条边界必须写进书：**"这是合成决策集和确定性工具沙箱上的真实模型测量，不是现网 OpsAgent 准确率，也不是新增 Gazebo 或真机物理成功率。"**

#### 配套：物理接地验证（Gazebo）

`docs/experiments/2026-09-21-grounded-verification.md` 是同一批方法的物理侧实验：**150 个 Gazebo 任务实例、210 条物理动作轨迹、九组 1,890 条评估、1,470 次真实模型调用**。结论同样带边界：

- 相较 B0（信任回执），GVF 把**错误接受率从 58.57% 降到 13.81%**（双侧配对 t，p=0.00003336），准确率从 41.43% 升到 64.76%。
- 相较 B1（无合约自验证），准确率提高 **2.38 pp**（p=0.0341），但错误接受率差异**未达 0.05**（p=0.0705）。
- **A5 带合约的 LLM 总准确率 74.29%，高于 GVF；不能宣称 GVF 全面优于 LLM。**
- 共同物理轨迹的实际任务成功率 **38.00 ± 1.83%**（所有组读同一批轨迹，此值相同）。
- 研究范围自陈："这是同一批物理轨迹的**配对离线验证与恢复策略回放**。恢复候选确实执行并保存，但各组没有独立在线运行；任务成功率、恢复步骤和端到端成本是该回放策略的估计，**不能当作九组自主机器人实测**。"

### 11. 后训练流水线：`train/` 与 `training/` 的区别，以及为什么不用 TrainAgent

**两个包的职责完全不同，名字相近容易混**：

| | `train/` | `training/` |
| --- | --- | --- |
| 文件 | `gate.go`（268 行）、`gate_test.go` | `export.go`（283）、`quantify.go`（312）、`quantify_test.go`（179）、`refusals.go`（80） |
| 职责 | **晋升门禁**：比较两份评测报告，决定 checkpoint 能不能替换在役模型 | **量化与导出**：读账本 → 统计 → 切分 → 写 jsonl |
| 关键 API | `Compare(Report) Decision`、`Gate{MinExecutable, MinRefusals}` | `Ledger`（只读打开 SQLite）、`HarvestRefusals` |
| 判据 | Pareto + 用例集一致 + run-incomplete | 计数与桶划分 |

**为什么不用 TrainAgent。** 依据 `docs/architecture/train-agent-assessment.md` 与 `train/gate.go:1-40` 的包注释。核心论证只有一条，但它足够硬：

> "A robot's closed loop works because the world is the judge and the world does not care what the policy wanted: either the cup moved or it did not. A training run is judged by a metric, and the run is optimised against that metric. **The thing being measured and the thing doing the measuring are the same system. That is not a risk to mitigate; it is the default outcome.**"

评估把结论收窄成一句可引用的判词：

> **"LLM 可以设计训练的过程，但绝不能定义'什么算更好'。后者是它唯一也能移动的东西。"**
> "一切**有独立判据、短周期、可复验**的工作可以是 Agent。训练没有独立判据，所以它不能是——**除非把判据切出去。**"

另外两条不成立的理由也值得引用：**时间尺度对不上**（恢复动作秒级，租约与 fencing 因此有意义；训练 run 是小时到天级，"本仓库为短时物理动作建的整套安全机制在这里没有对应物"）；**失败形态更隐蔽**（坏机器人动作有界、有审批有门禁；坏上线模型是静默的，"只是开始给出稍差的计划，没有人报警，而评测分数可能还涨了"）。

**"把判卷从训练里切出去"在代码里的三条规则**（`train/gate.go:89-160`）：

1. **Pareto，不是加权分**：可执行用例与拒绝用例**都不许变差**，且至少一个变好。"没有汇率。"原文："A weighted score lets a gain in 'plans more requests' pay for a loss in 'refuses requests it must refuse' … **A model that plans everything and refuses nothing is not 90% of a good model; it is a model that will execute a negation as an instruction.**"
2. **考试不能改**：`Compare` 拒绝比较用例集不同的两份报告（数量不同、成员不同都拒，`ReasonDifferentCases`）。"**一个模型不能靠改了考卷来通过考试**——这是最容易想到、也最致命的作弊路径。"
3. **没测过 ≠ 考砸了**：未完成的 run 记为 `run-incomplete`（`ReasonRunFailed`），既不上线也不丢弃。"当成 0 分会因为一次掉线丢掉一个好 checkpoint；当成及格会上线一个从没测过的模型。"

**首次上线走另一条路**：没有在役基线时 `Compare` 先于用例集比较就返回"没有在役基线可供比较；首次上线的准入由绝对门槛决定"（`gate.go:126-135`，理由：把"没有基线"报成"用例数不同"会把读者引去找谁篡改了用例集），准入交给 `Gate{MinExecutable, MinRefusals}`——两个绝对门槛各自设线。**"'没有基线'因此不会变成'什么都能上'。"**

去掉任一守卫测试立刻变红（已实测，`docs/architecture/train-agent-assessment.md` 表格）：去掉拒绝不回退 → `TestPlanningMoreCannotPayForRefusingLess`；去掉用例集一致 → `TestASmallerCaseSetIsRefused`、`TestASwappedCaseSetIsRefused`。

**门禁脚本 `scripts/gate_agent_context.py`（32 行，全文可读）**：调 `orchestration/eval/context_eval/gate.py:compare_runs`，参数 `--baseline/--candidate/--baseline-format/--candidate-format/--output/--min-ability-delta`（默认 `0.0`）/`--max-token-ratio`（默认 `2.5`），**`sys.exit(0 if r["passed"] else 1)`**。也就是说：判卷逻辑不在脚本里，脚本只是把库函数接到 CI 的退出码上——这与"判据必须是被测者碰不到的确定代码"是同一条原则的两种表述。

**流水线的真实产出（`docs/architecture/post-training-pipeline.md` 第三节，实测数据）**：

```
记录 64 条，其中不同请求 15 个
按可信度：  verified 23 / unknown-outcome 29 / unfinished 12
按计划来源：deterministic 59 / llm 5
可用正样本（已确认 + 真编排）：0 条
切分：SFT 0 / 拒绝 6 / 负样本 0
被排除：verified-but-no-plan 4、unknown-outcome 3、unfinished 8
```

**"0 条正样本"不是流水线坏了，是它正确地说了实话**，两条具体原因：① 23 条 verified **全是导航任务且计划是确定性生成的**（空 bundle）——有结果、没有编排信号 → 进 `verified-but-no-plan`；② 5 条 LLM 计划的**全部没成功**。原文的处置是："**所以现在的正确动作不是训练，是修执行链。**流水线把这句话直接打在报告里，而不是打印一张好看的表格让人去开训练任务。"

三个桶的划分理由值得单列（这是全书最能体现"闭环契约贯穿到数据层"的一处）：

| verdict | 进哪个集合 | 为什么 |
| --- | --- | --- |
| `verified` | SFT 正样本 | 唯一可信的正向信号 |
| `failed` | 负样本 | 可用于偏好对与挖评测用例 |
| `refused` | 拒绝样本 | **账本里不可能有**（被拒请求不落库），只能采 |
| `unknown-outcome` | **两半都不进** | 当成成功 → 教模型做没人核对过的物理动作；当成失败 → 教它偏好回报快的动作，**这是 reward hacking 的方向** |
| `unfinished` | 排除 | 还不是一个事实 |

**唯一规则**："改动世界的步骤 + 动作后有新鲜观测 → `verified`；任务成功但物理步骤没有观测 → `unknown-outcome`。**'步骤状态是 COMPLETED' 不算证据。**"原文："训练数据如果按'步骤报成功'来建，**教给模型的就是执行层拒绝相信的那个说法**。"

**两条已知缺口（代码注释里写明）**：① 今天没有记录被拒绝的请求，所以"拒绝集有多大"取决于**有人写了多少条**，而不是系统见过多少条——补法很小（把被拒请求追加进账本），而它是"当前性价比最高的数据采集"；② 去重必须按**请求**不按行（参考部署里 **64 条任务只是 15 个不同请求**），且同一请求同时成功和失败时**成功的胜出**——"模型没法同时学两个"。

`training/export.go:13-38` 还有一条与门禁同源的纪律：账本**以只读方式打开**（`dsn := "file:"+path+"?mode=ro"`），注释是"An export that could write would eventually be run against production, and a training pipeline must never be able to change the record it is learning from"。

### 12. 版本化任务经验：跨机学习走到哪一步了（诚实说明）

**必须先纠正一个容易产生的联想。** `tasks/experience.go`（639 行）**不是**跨机经验学习模块，它是**面向非技术用户的任务体验投影**：`TaskExperience` / `ProjectExperience` / `ToolActivity` / `PolicyEvidence` / `RecoveryGuidance` / `ProfessionalDetails`（`tasks/experience.go:9-133`），做的是把技术任务 ID、意图状态和审计事件翻译成"机器人理解为…""现在在做什么""还能做什么"。入口是 `GET /v1/tasks/{id}/experience`（`console/server.go:205`）。

它对应的设计是 `docs/superpowers/specs/2026-08-22-versioned-task-experience-design.md`（Status: approved for implementation），四个目标被原话列为用户问题：

> 1. What did the robots understand? 2. What are they doing now? 3. Which robot capability is being used, in ordinary language? 4. How can I change the task without losing completed work or creating an unsafe distributed race?

后两条的关键设计是**版本化任务 + 保留已完成工作**：`tasks/reconcile.go:3 BuildChangeSet` 把新计划与旧修订逐步骤比对，分成 `Added / Retained / Changed / Paused`，其中"兼容"的判据是 `prior.SemanticFingerprint == step.SemanticFingerprint`，且对已 `StepSatisfied` 的步骤还要求 `basis.EvidenceValidity[prior.StepID]`；正在跑且发生变化/被移除的步骤进 `Paused`（`:38-44`）。`SelectExperienceRevision`（`tasks/experience.go:199`）保证"已批准但等待物理安全点的修订比仍在执行的基础修订更值得给人看"，而**执行归属仍在 `CurrentRevision`**。

**"A 机解决的故障 B 机受益"这条路径的现状：有一句架构宣称，没有实现。** 这是全书里"已宣称 vs 已实现"最典型的一处，必须原样对照。

**宣称在** `docs/architecture/why-distributed.md:124`——该文把"多机协同、**跨机学习（A 机解决的故障 B 机受益）**"直接列在分布式运行时架构的**优势**清单里，与"断网可用""安全本地闭环""数据留在本地"并列。

**实现不在**，五处证据（这条链的证据密度值得书里保留）：

- `core/agentcontract/contract.go:60-66`：三项能力被**明确标为 reserved**——`completion.verification`（留给可能否决完成声明的 EvalAgent）、**`experience.recording`（留给"consolidates skills and memory"的 ExperienceAgent）**、`escalation`（留给"owns the human approval queue"的 EscalationAgent）。
- `core/agentcontract/memory.go:8-13`：三层记忆（Ledger / Beads / …）"**Declaring them now is the whole of the forward-looking work for ExperienceAgent**: the layers and their read/write shapes exist, and nothing in the runtime has to change when an implementation of them arrives."——**"前瞻性工作的全部"就是声明它们**。
- `core/agentcontract/memory.go:88`："an ExperienceAgent fills; this version ships only an **in-memory** implementation"；`agentruntime/memory.go:95,112,283` 是那个进程内实现（`MemoryBeads`，注释自陈"the part worth pinning with tests **before a durable implementation arrives**"）。
- `agentruntime/opsagent.go:48` 的字段注释："Ledger and Beads exist **for a future ExperienceAgent**."（未来时态）
- `cmd/local-agent/agentruntime.go:20-27`：组合根的设计目标是"**adding EvalAgent, ExperienceAgent or EscalationAgent later means adding a file here and a name in configuration** rather than editing the runtime"——即三个 Agent 都还**没有被添加**。
- `grep -rn "ExperienceAgent" --include=*.go` 的全部命中只有上述注释与接口声明，**没有任何实现类型**（其余命中在 `artifacts/` 的实验快照里）。
- 恢复 Agent 的"仍然不做的事"清单最后一条是："**不跨机器人**。`edge-worker` 有云端事件上报但没有本地任务服务，机队级监督需要新的云端 RPC"（`docs/architecture/recovery-agent.md:306`）。

**真正落地的是"单任务回放视图"**，调用点只有四处：`fleet/revisions.go:49,126`、`console/local_experience.go:121`、`console/revisions.go:56`。也就是说，`tasks/experience.go` 这一整套"任务体验"能力服务的是**用户看懂当前这一条任务**，不是**机器之间共享经验**。

**唯一存在的跨机路径是运维级的、不是代码级的**：`TANGYING_INCIDENT_DIR` 可以指向共享卷，"让一个诊断流程扫全部机器人"（`docs/development/2026-09-15-ai-incident-diagnosis-and-ops-loop.md:127`），配合 `diagnose_task.py --sweep` 得到全机队的故障族分布。这共享的是**诊断知识（故障族表 + 覆盖测试指针）**，不是**经验（某次故障的处置结果）**。

**诚实的分级结论**：

| 能力 | 状态 | 证据 |
| --- | --- | --- |
| 单机故障 → 事故包 → 分类 → 覆盖测试指针 | ✅ 已实现并实测 | `incidents/bundle.go`、`diagnose_task.py --sweep`（本次实测 64 份） |
| 单机只读步骤自动执行 | ✅ 已实现并实测 | `internal/autorecovery/supervisor.go`；52 个任务 27 条执行记录 |
| 故障族知识跨机共享（同一份 Python 表） | ✅ 已实现（靠代码分发，不靠运行时同步） | `diagnose_task.py:42-187` 单点定义 |
| 事故包跨机汇总（共享目录 sweep） | ✅ 运维级可用，**无代码强制** | `TANGYING_INCIDENT_DIR` + `--sweep` |
| 处置结果（哪个动作真的修好了）跨机复用 | ❌ **未实现** | 目录里没有记录动作效果的字段；`Verify` 在生产为空 |
| 跨机监督 / 机队级恢复 | ❌ **未实现，且文档自认需要新 RPC** | `docs/architecture/recovery-agent.md:308` |

书里应把这一段写成"**同一条学习路径上，知识共享已通、经验共享未通**"，并指出未通的根因恰好是第 6 节那个空着的 `Verify`：**没有独立的复验，就没有"这个动作在某些条件下真的有效"的可信记录，也就没有可跨机复用的经验。**

### 13. 训练数据导出与 `datasets/`

`cmd/training-export`（`main.go` 209 行）。契约写在包注释（`:1-20`）里，是三个问题按顺序回答、遇到坏答案就停：

1. What is in the archive, in **distinct requests rather than rows**?
2. How much of it is actually usable — **confirmed by the closed loop**, and carrying a real orchestration plan?
3. What does the split look like, and **what is missing from it**?

**退出码语义（这是最值得书里引用的一处"把状态编码进退出码"的设计）**：`0` 写出数据集；**`1` 账本还不足以支撑训练**；`2` run 本身失败。原文："The middle case is the common one and it is not an error: it means 'collect more data', and **a pipeline that reported it as a crash would be ignored.**"（`cmd/training-export/main.go:16-20`）

参数：`-db`（必填，Local Agent 数据库，**只读**）、`-out`（数据集目录，省略则只量化）、`-corpus`（每行一个请求，用于采拒绝样本）、`-provider`（默认 `deterministic`）、`-base-url` / `-api-key` / `-model`（回退到 `AGENT_BASE_URL` / `AGENT_API_KEY` / `AGENT_MODEL`）。

用法（`docs/architecture/post-training-pipeline.md` 开头）：

```bash
bin/training-export -db agent.db -out ./dataset
```

导出格式：`dataset/{sft,refusals,negative}.jsonl`，每份带输入、参考输出、来源、评分版本与奖励维度；另有 `training/manifest.json` 记录各桶计数与被排除项。

**`datasets/` 里有什么（本次实测）**：`datasets/` 只有 **5 个 0 字节标记文件**——`.tangying-robocasa-assets-fixtures-complete`、`.tangying-robocasa-assets-minimal-complete`、`.tangying-robocasa-assets-objs-lw-complete`、`.tangying-robocasa-assets-tex-complete`、`.tangying-robocasa-env-complete`。真实数据不在仓库里：`docs/operations/fresh-deployment.md:306` 写明"RoboCasa 仿真线（双机厨房）`make robocasa-install` → 会下载约 **4.2G** 到 `datasets/`。**本项目已清理掉这份数据**，需要时重新下载"。书里引用 `datasets/` 时必须说清这一点，否则会让读者以为仓库自带数据集。

另一处训练数据来源在评测输出侧：第一轮导出 `training/sft.jsonl` **120 条仅 train 的规则 oracle 样本**；第二轮按开发集锁定候选导出 **160 条 train SFT 样本**。两者都在 `artifacts/`（本地证据，不随 Git 分发）。

---

## 第三部分 安装、部署与运维

### 14. `install.sh` 的角色安装与 `robot-agent doctor`

**先说一个与任务描述不符的事实：`install.sh` 只有 3 个角色，不是 4 个。** `cloud` 已被移除并硬报错：

```
install.sh:41-44
  echo "error: cloud role was removed; install the local role on the user's laptop" >&2
  exit 2
```

实测 `./install.sh cloud` → 上述错误，`[exit=2]`。四个角色同时出现在**预检脚本**里（`scripts/precheck.sh:338` 的白名单是 `sim|local|robot-pi|cloud`），这正说明预检与安装是两个不同的工具，详见 §18。

`install.sh` 共 **94 行**。角色是**位置参数**，**没有 `--role` flag**（传 `--role` 会命中 `install.sh:63-67` 的 `unknown argument`）。flag 只有：`--yes`（`:45-47`）、`--dry-run`（`:48-50`）、`--version VERSION`（`:51-58`）、`-h/--help`（`:59-62`）。执行骨架：`:82` source `scripts/install/common.sh` → `:84` `detect_platform` → `:85` `validate_role_platform` → `:86` `print_plan_header` → `:88-92` source 角色脚本 → `:94` `install_role`（由各角色脚本各自定义）。

**`--dry-run` 的真实机制**：`common.sh:24-35` 的 `run()` 在 `DRY_RUN=1` 时打印 `DRY-RUN` + 每个参数 shell-quote（printf `%q`）后**直接 return 0**；`run_in_root()` 打印 `DRY-RUN cd <root> && …`；`confirm_mutation()` 在 dry-run 下直接放行、**不问也不检查 tty**；`write_receipt()` 打印 `DRY-RUN write receipt <dest>`。仍会真实执行的是平台探测与版本比对（`common.sh:83-131`、`:290-299`）——因为"这台机器是什么"本身是只读的。

实测输出（macOS 26 / arm64）：

```
$ ./install.sh sim --dry-run
PLAN role=sim os=darwin distro=macos version=26 arch=arm64 release=v0.7.0
==> preparing simulation development stack
DRY-RUN brew install go python@3.11 protobuf
DRY-RUN cd <root> && python3.12 -m venv .venv
DRY-RUN cd <root> && .venv/bin/pip install -e .\[dev\]
DRY-RUN cd <root> && go build -ldflags -X\ main.version=v0.7.0 -o bin/robot-agent ./cmd/robot-agent
DRY-RUN cd <root> && go build -o bin/local-agent ./cmd/local-agent
==> simulation installed; run: ./bin/robot-agent demo
```

**四个角色（三个可装 + 一个预检）分别装什么**：

| 角色 | 脚本 | 装什么（真实命令，摘要） |
| --- | --- | --- |
| `sim` | `scripts/install/sim.sh`（32 行） | macOS：`brew install go python@3.11 protobuf`；Ubuntu 22.04：`add-apt-repository ppa:deadsnakes/ppa` + `apt-get install python3.11{,-venv,-dev} protobuf-compiler curl ca-certificates build-essential` 并设 `ROBOT_AGENT_PYTHON=python3.11`；其它 Linux：`apt-get install python3{,-venv,-dev} protobuf-compiler …`；`ensure_go`；`pip install -e '.[dev]'`；`build_go_binaries`；写回执。**不装任何服务单元。** |
| `local` | `scripts/install/local.sh`（62 行） | macOS：`brew install go`；Linux：`apt-get install ca-certificates curl git openssl` + `ensure_go`；`build_go_binaries`；**仓库快照安装**（`git archive` + `tar`，不是 clone）；`install -m 0755 bin/robot-agent /usr/local/bin/robot-agent`；`local.env` 权限 **0600 且已存在则保留**；`state_dir/certs` 0700；macOS 渲染 launchd plist 并 `launchctl bootstrap gui/<uid>`，Linux 装 `~/.config/systemd/user/tangying-robot-local-agent.service` 并 `enable`（**不带 `--now`，故意不启动**）。收尾提示："Local Agent installed but not started; run robot-agent configure, then robot-agent start local"。 |
| `robot-pi` | `scripts/install/robot-pi.sh`（133 行） | `useradd --system --create-home --groups dialout --shell /bin/bash tangying-robot`；`git clone https://github.com/Vector-Wangel/XLeRobot.git /opt/XLeRobot` + `checkout --detach 3d14695e40c9c68229c0aacffca6053c75cd3eb6`；仓库快照到 `/opt/tangying-robot-agent-os`；**隔离 venv**（direct 模式不加 `--system-site-packages`）+ `pip install -e ".[robot-pi]"` + `pip check` + 复制 pinned XLeRobot 双轮集成到 `lerobot.robots`；`robot-pi.env`（0600，chown tangying-robot）；`certs` 0700 / `calibration` 0750；**跳过 ROS2 workspace 构建**（direct 模式）；装 `tangying-robot-edge-direct.service` → `/etc/systemd/system/tangying-robot-edge.service` + `99-tangying-xlerobot.rules` → `/etc/udev/rules.d/`；`systemctl daemon-reload` + `udevadm control --reload-rules`。收尾："Robot Edge installed but stopped pending certificates, serial devices, calibration, and safety checklist"。 |
| `cloud`（预检有，安装无） | — | `install.sh` 硬拒绝；实际入口是 `./scripts/fleet-up.sh up` → `deploy/cloud/docker-compose.yml`。 |

固定版本（`scripts/install/common.sh:8-9`）：Go `1.26.2`、XLeRobot 提交 `3d14695e…`。平台白名单（`common.sh:119-131`）：`sim`/`local` 支持 macOS（任意版本）+ Ubuntu 22.04/24.04；**`robot-pi` 仅 `linux:ubuntu:24.04:arm64`**；其余 `die "unsupported platform for $role: …"`。

**`robot-agent doctor` 的真实检查项**（`internal/robotagent/app.go:385-419`；`cmd/robot-agent/main.go` 只有 19 行，所有子命令实现都在 `internal/robotagent/app.go`，512 行）：

| # | 检查 | 判据 | 失败提示 | 行号 |
| --- | --- | --- | --- | --- |
| 0 | 读安装回执 `<state>/install.json` | 存在、JSON 合法、`role ∈ {sim,local,robot-pi}` | `read installation receipt: …` 等；**直接 return，后续一项都不跑** | `app.go:390-394`、`:122-135` |
| 1 | 打印 `PASS receipt role=%s version=%s platform=%s/%s` | 已过检查 0 | — | `app.go:398` |
| 2 | 配置文件存在 `<config>/<role>.env`（`role != sim`） | `os.Stat` | `FAIL config <path>: <err>` | `app.go:399-404` |
| 3 | 配置文件权限不含 group/other 位 | `info.Mode().Perm() & 0o077 == 0` | `FAIL config permissions <path>: %o` | `app.go:405-407` |
| 4 | 打印 `PASS config=%s permissions=%o` | — | — | `app.go:408` |
| 5 | `role == robot-pi` → 转派 `bash scripts/robot-pi-preflight.sh <config>/robot-pi.env` | 见下 | 见下 | `app.go:410-417` |
| 6 | `sim` / `local` → **到此为止** | — | — | `app.go:418` |

`scripts/robot-pi-preflight.sh`（55 行）的 20 项检查（`:21-55`）：配置可读 → 六个必填键（`XLEROBOT_PORT1`/`XLEROBOT_PORT2`/`XLEROBOT_CALIBRATION`/`ROBOT_SERVER_KEY`/`ROBOT_SERVER_CERT`/`ROBOT_CLIENT_CA`）→ 两个串口是字符设备且可读写 → 标定文件 `tangying-xlerobot.json` 非空 → 三份 mTLS 文件可读 → **服务端证书 ≥ 7 天有效期**（`openssl x509 -checkend 604800`）→ `.venv/bin/python` 可执行 → 能 import `lerobot.robots.xlerobot_2wheels` 与 `tangying_robot_gateway` → 跑 `xlerobot_preflight.py`。失败输出到 stderr 并 `exit 1`；成功打印 `PASS no-motion Robot Edge preflight complete`。文档佐证 `docs/install/robot-pi.md:103`。

**`--dry-run` 在 install 与 doctor 里的差别**：install **支持**；**doctor 不支持**（`app.go:386-389` 只接受 0 或 1 个参数且必须是 `sim|local|robot-pi`；实测 `./bin/robot-agent doctor --dry-run` → `error: unknown doctor option or role "--dry-run"`，`[exit=1]`）。doctor 本身是只读的。

### 15. 一键配对的真实机制：不是 SSH，也不是二维码

**发现：UDP 广播，端口 45871**（不是 mDNS）。`internal/discovery/announcement.go:41-46` `AnnouncementPort = 45871`，兜底广播地址 `255.255.255.255`（`:48-51`），协议版本 `1`，载荷标识 `topic = "tangying.robot.announce"`，单包上限 2048 字节。公告间隔 **5 秒**（`listener.go:22-24`，Python 侧 `beacon.py:46 ANNOUNCE_INTERVAL_SECONDS = 5.0`），列表保留 3 个周期 = **15 秒**，排序 `open` 优先 → LastSeen 倒序 → RobotID（`listener.go:258-278`）。监听失败**非致命**，只 log（`Listener.StartInBackground` `:302-315`）——不能因为发现服务起不来就让控制台起不来。

**广播明文且不认证是刻意的**（`announcement.go:9-20`）：两端还没有共享密钥，建立密钥正是配对要做的事；因此载荷只携带"同网段任何设备本来就看得见"的信息，**配对码永不进广播**。

一处文档与代码的偏差值得书里点出（不冲突，但文档只写了单向）：`docs/architecture/robot-discovery.md:89-93` 说 `broadcast_targets()` 会额外加 `127.0.0.1`——这**只在 Python 侧成立**（`beacon.py:247` 起手就是 `[("127.0.0.1", ANNOUNCEMENT_PORT)]`）；**Go 侧 `broadcastTargets` 显式 `continue` 跳过 loopback**（`announcement.go:263-269`），理由是"announcing on it would put the robot in the agent's own discovery list when both run on one machine — which is how the simulator is deployed"。合起来的行为是：机器人会向本机广播，但 Agent 不会因为本机广播而自我发现。

**配对：裸 TCP 45872 + 长度前缀 JSON + 预共享码加密**（`internal/pairing/protocol.go`）。关键参数：

| 项 | 值 | 行号 |
| --- | --- | --- |
| 传输 / 端口 | TCP / **45872**（紧邻广播 45871） | `client.go:79`、`protocol.go:73-77` |
| topic | 请求 `tangying.robot.enroll`，应答 `tangying.robot.enroll.result` | `protocol.go:68-69` |
| 帧格式 | 4 字节大端长度 + JSON，单帧上限 64 KiB | `protocol.go:83`、`:378-412` |
| 密钥派生 | HKDF-SHA256(配对码规范化为 ikm, salt=32B 随机, info=`tangying.robot.enroll.v1`) → 32 字节 | `protocol.go:87`、`:172-182`、`:443-468` |
| 加密 | AES-256-GCM，**AAD = robotId**（防跨机器人重放） | `protocol.go:190-208` |
| 请求载荷 | 三份 PEM：`ca` / `serverCert` / `serverKey` | `protocol.go:110-118` |
| 应答载荷 | `{"status":"paired"\|"refused","detail":"…"}`，用同一密钥封装——**这就是认证** | `protocol.go:142-148`、`:341-376` |
| 码比较 | 规范化后**常量时间**；空码不匹配任何东西 | `protocol.go:157-166`、`:414-433` |
| 机器人侧窗口 | `DEFAULT_WINDOW_SECONDS = 900`、`MAX_ATTEMPTS = 5` | `robot/gateway/tangying_robot_gateway/pairing.py:79,85` |
| 超时 | 客户端 30 s，控制台侧 45 s | `client.go:61-64`、`console/pairing.go:133` |

**"不需要 SSH"是怎么做到的**：两台机器**没有共享秘密**，因此用机器人**打印出来的一次性配对码**作为唯一预共享值（`protocol.go:11-24`）。Agent 用它派生的密钥加密请求 → 证明自己知道码；机器人用同一密钥封装应答 → 反过来证明自己知道码。**双向认证在同一次交换里完成**，而证书材料的投递不再经 SSH/scp，而是**密封在 enroll 请求里**。控制台侧注释直接写着 "with no SSH anywhere"（`console/pairing.go:20-26`）。

**HTTP 端点**：`GET /v1/robots/discovered`（`console/server.go:186`）、`POST /v1/robots/pair`（`:187`）。请求体 `{robotId, address, code, enrollmentPort?}`；错误码映射 `PAIRING_CODE_REQUIRED`(400) / `PAIRING_TARGET_REQUIRED`(400) / `PAIRING_UNAVAILABLE`(503) / `PAIRING_CODE_REJECTED`(401) / `ROBOT_NOT_PAIRING`(409) / `ROBOT_REFUSED_PAIRING`(409) / `PAIRING_FAILED`(502)（`console/pairing.go:103-155`）。

`FilePairingService.Pair` 的真实步骤（`console/pairing.go:179-278`）：① 机器人必须在本机**听到过**的广播列表里（`:195-208`，否则提示"没有发现叫 %s 的机器人正在广播"）；② 载入或创建本机 CA（`:237-241`）；③ 调配对客户端（`:256-258`）；④ **机器人接受之后才写本机那一半**（`:263-273`）：`local-agent.crt` 0644 / `local-agent.key` 0600，并更新 `local.env` 的五个键（`ROBOT_ADDRESS`/`ROBOT_SERVER_NAME`/`ROBOT_CA`/`ROBOT_CERT`/`ROBOT_KEY`，`:319-325`），其余键原样保留；⑤ 返回 `restartRequired: true`。

证书形状（`internal/pairing/authority.go`）：**CA 10 年（`:38`）、叶子 90 天（`:42`）、续期告警 7 天（`:45`）**、ECDSA P-256（`:102`）、CA 私钥**永不出本机**（`:48-53`、`:57`）、已存在 CA 不静默替换（`:73-75`）、只剩一半则**停下报缺哪个文件**（`:92-98`）、签发后立刻用本端 CA 验一遍（`:268`）。

**SSH 路径仍然存在，是回退**：`robot-agent pair HOST --ssh-user USER [--new-ca]` → `scripts/pair-robot.sh`（305 行，硬检查 `openssl`/`ssh`/`scp`，`scp` 到 `/tmp/tangying-robot-pair-$RANDOM` 再远端 `install -o tangying-robot …`）。两条路径产出等价。

**没有二维码**：全仓未找到 QR 生成/扫描实现；配对码是纯文本（启动时打印进日志 / 印在标签上，由人读出并手输，`docs/architecture/robot-pairing.md:23-31`）。

**"自然语言配置热生效"的真实链路**（这个词组本身不在仓库里，是任务描述的措辞；实现是真的）：

```
PUT /v1/config/llm  (console/server.go:189 → updateLLM :303-321)
  → 落盘 local.env（0600，临时文件 + rename；internal/localconfig/settings.go:66-110、:136-171）
  → 写盘成功后调用 onChange(status)（:96-108）
  → 回调里重新读文件（不信请求体）→ 构造新 parser → service.SetParser(...)
     （cmd/local-agent/main.go:489-516）
  → tasks/service.go:120-131  SetParser 的注释：
     "SetParser replaces how requests are understood, without a restart."
```

一个细节很值得写：**只有回调真的应用成功，才把 `RestartRequired` 置 false**（`internal/localconfig/settings.go:96-108`）——回调没装或应用失败时，界面如实显示"需要重启"。对照之下，`TANGYING_AGENTS`（启用的 Agent 集合）**只在启动时读，不热生效**（`docs/operations/agent-runtime-config.md:5-9`）。

### 16. 部署形态与端口：8787 vs 8897

**这两个端口不是两个服务，是同一个 Local Agent 控制台在两种场景下的端口。** 文档 `docs/architecture/why-distributed.md:183-185` 写得最清楚：

> 本地单机文档写的是 `8787`，而 `home-furnished` 显式传 `8897`。**两个都对**——前者是 `sim-stack.sh` 的默认，后者是家庭场景目标的显式选择。照文档敲 `127.0.0.1:8787` 打不开不是 bug，是文档里已标注的差异。

证据链：`scripts/sim-stack.sh:10 AGENT_PORT="${SIM_STACK_AGENT_PORT:-8787}"`；`Makefile:113-114` `bash scripts/furnished-home-demo.sh start --sim-port 50161 --agent-port 8897`；`Makefile:110-112` 注释说明动机——"**One canonical port, stated once**"，否则"README sends a first-time reader to a port nothing listens on"；`docs/operations/fresh-deployment.md:84、:123` 把"打开 8787 是空白 → 家庭场景在 8897"写进故障表。**根本原因是端口隔离**：家庭演示用独立 namespace（sim 50161 / agent 8897），以便与默认工位（sim 50051 / agent 8787）**同时存在**，且不删原工作台数据（`docs/development/2026-09-13-furnished-home-acceptance.md:8`）。

**本地单机进程拓扑**（脚本模式）：

```
make home-furnished (Makefile:113-114)
 └─ scripts/furnished-home-demo.sh start --sim-port 50161 --agent-port 8897
      └─ exec bash scripts/sim-stack.sh start … --scene home_task --perception rgbd
           ├─ 进程 1  .venv/bin/python -m tangying_sim.server --listen 127.0.0.1:<SIM_PORT>   (sim-stack.sh:993,997)
           └─ 进程 2  bin/local-agent --dev-insecure --robot-safety-profile desktop_standard
                      --listen 127.0.0.1:<AGENT_PORT> --robot 127.0.0.1:<SIM_PORT>          (sim-stack.sh:1007,1016-1032)
```

端口全表（`docs/operations/deployment.md:127-136`）：`8787` 本地单机控制台（家庭场景 `8897`）、`50051` 仿真 Runtime（家庭场景 `50161`）、`18790`/`18791` 机器人端导航容器、`443`+`8444` 云端、`18080` 云端仅回环、`3306`+`6379` 云端仅容器网。

**8787 的绑定安全策略**：默认 `127.0.0.1:8787`（`cmd/local-agent/main.go:79`）；**非环回绑定被拒绝**，除非显式 `--allow-remote-console` 或 `LOCAL_ALLOW_REMOTE=1`（`cmd/local-agent/listen.go:19-65`）；`loopbackListen` 对 `:8787`、`0.0.0.0:8787`、`[::]:8787`、`10.0.0.5:8787` 一律返回 false（`cmd/local-agent/listen_test.go:18-28`）。

**systemd / launchd 封装**：

| 单元 | 关键字段 |
| --- | --- |
| `deploy/local/tangying-robot-local-agent.service`（18 行，Linux 用户单元） | `ExecStart=/usr/local/bin/tangying-local-agent --config %h/.config/tangying-robot-agent-os/local.env --data-dir %h/.local/share/tangying-robot-agent-os`（`:8`）；`Restart=on-failure`、`RestartSec=2`（`:9-10`）；加固 `NoNewPrivileges` / `PrivateTmp` / `ProtectSystem=strict` / `ReadWritePaths=…`（`:11-14`）。**端口不在 unit 里**，来自 `local.env` 的 `LOCAL_LISTEN`。 |
| `deploy/local/com.tangying.robot-agent.plist`（17 行，macOS） | `ProgramArguments` 指向 `/Users/Shared/TangyingRobotAgent/bin/local-agent` + `--config`/`--data-dir`（`:6-11`）；`RunAtLoad=true`、**`KeepAlive=true`**（`:12-13`）；stdout/stderr 到 `…/logs/local-agent.log`、`…/logs/local-agent.error.log`（`:14-15`）。`__HOME__` 由安装时 `sed` 渲染（`scripts/install/local.sh:15-17`）。 |
| `deploy/robot/raspberry-pi/tangying-robot-edge-direct.service`（30 行，current） | `User/Group=tangying-robot`、`SupplementaryGroups=dialout`（`:8-10`）；`EnvironmentFile=/etc/tangying-robot-agent-os/robot-pi.env`（`:11`）；`ExecStart=/opt/tangying-robot-agent-os/.venv/bin/python -m tangying_robot_gateway.run_direct_edge --connect`（`:17`）；`Restart=on-failure`、`RestartSec=1`、`TimeoutStopSec=10`、`KillSignal=SIGINT`（`:18-21`）。注释：**"Service startup never enables torque."**（`:15-16`） |
| `deploy/robot/raspberry-pi/tangying-robot-edge.service`（27 行，ROS 2 路径，**当前不可达**） | `Requires=tangying-xlerobot.service`；`ROS_DOMAIN_ID=73`、`ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`；`ExecStart=/bin/bash -lc 'source /opt/ros/jazzy/setup.bash && … ros2 launch tangying_ros_gateway robot_edge.launch.py'`。**前提**：`scripts/install/robot-pi.sh:84-86` 的 `direct_edge()` 硬编码 `return 0`，所以 ROS 2 Jazzy 安装分支与 `colcon build` 是死代码。写书时若要提"树莓派会装 ROS 2"，必须注明这一前提。 |

**一处语义差异没有解释（如实报告，不推测）**：launchd 用 `KeepAlive=true`（正常退出后也会拉起），systemd 用 `Restart=on-failure`（只在非零退出时拉起），两者**不等价**，仓库里没有解释这个差异的注释。

**Fleet 的 Compose 部署**——注意：**没有 `deploy/fleet/` 目录**，实际位置是 `deploy/cloud/docker-compose.yml`（109 行，4 个服务）：

| 服务 | 镜像 | 端口 | restart |
| --- | --- | --- | --- |
| `mysql` | `mysql:8.4` | 仅容器网 `3306` | `unless-stopped` |
| `redis` | `redis:7-alpine --appendonly yes` | 仅容器网 `6379` | `unless-stopped` |
| `fleet-control-plane` | build `deploy/cloud/Dockerfile` | **`expose` 8080/8443，不 publish 到宿主** | `unless-stopped` |
| `nginx` | `nginx:1.27-alpine` | `${FLEET_HTTPS_PORT:-443}:443`、`${FLEET_GRPC_PORT:-8444}:8444`、`127.0.0.1:${FLEET_LOOPBACK_HTTP_PORT:-18080}:80` | `unless-stopped` |

依赖顺序由健康检查串起来：`fleet-control-plane depends_on mysql/redis (service_healthy)`，`nginx depends_on fleet-control-plane (service_healthy)`。`FLEET_DEVICE_CREDENTIALS` 用 `${…:?set robot-specific FLEET_DEVICE_CREDENTIALS in deploy/cloud/.env}`，**未设置则 compose 直接报错退出**。安全模型（`deploy/cloud/nginx.conf:1-16`）：gRPC mTLS 由 `stream{}` **透传到应用层终止**（nginx 本身没有 `ssl_verify_client`），HTTP 侧 `listen 443 ssl` + TLSv1.2/1.3 + IP 白名单，回环入口 `listen 80` + `allowed.conf`。

`scripts/fleet-up.sh`（225 行）`up` 的真实顺序：`ensure_docker` → `generate_env`（仅在不存在时写，`chmod 600`）→ **`bash scripts/fleet-certs.sh`** → `generate_allowed_conf` → `docker compose up -d [--build]`；健康检查是**最多 60 次 × 2 s = 120 s** 轮询 `curl -fsS -k https://127.0.0.1:${FLEET_HTTPS_PORT:-443}/healthz`，超时报 `cloud did not become healthy within 120s`。

`scripts/start-all.sh`（321 行）是编排器而非实现者：**启动顺序 `cloud → sim → navigation → fleet-sim`**（注释理由："Cloud first: simulated and real edges both dial it, so it must be listening before they start their presence loop."），**停止顺序逆序**（"edges stop before the cloud they dial"），`down` 只停本脚本启动过的组件（状态记在 `artifacts/start-all/state`）；默认只起 `sim`，理由写得很实在："it is the only one that needs no Docker and no real hardware. Cloud and navigation are opt-in so a developer machine never gets a surprise listener on 443."

### 17. 三份清单逐条转录

**先说状态标记的实况**：三份文件里的 checkbox **全部是未勾选 `- [ ]`，仓库中没有任何 `- [x]`**（`grep -c "^- \[ \]"`：`safety-checklist.md` = 14，`release-checklist.md` = 19，`production-readiness.md` = **0，它本身没有 checkbox**）。这一点必须写进书里：**这套系统把"愿望"和"已验收"分得很开，清单是待办，不是成绩单。**

#### 17.1 `docs/operations/production-readiness.md`（53 行）

前提（`:3`）：当前 V1 是仿真与集成候选版，**没有已完成的实机生产验收**。

**"哪些结果可以证明什么"（`:7-14`）**——这张表本身就是全书最好的"证据分级"教材：

| 证据 | 能证明什么 |
| --- | --- |
| MuJoCo / RoboCasa 测试通过 | 对应代码版本和限定仿真场景的行为 |
| `robot-agent doctor robot-pi` | 无动作配置、文件、证书与驱动兼容预检 |
| Runtime READY | 当前软件能力检查状态，**不是现场动作许可** |
| `production-check` 的 offline READY | 已配置 provider 可导入，传统记录满足字段检查 |
| Sim2Real check / report | 版本化配置、制品与操作员记录符合阶段要求 |
| 实际生产放行 | 现场负责人验证风险、策略/感知质量、真实停止与恢复，并完成必要评审 |

段落（`:16`）：以上结果**不能相互替代**；仓库没有随附适配所购 XLeRobot 的已训练生产模型；**默认 systemd 服务连接并保持扭矩关闭，不自动 arm；连接前必须支撑机械臂，底盘禁用，急停锁存不随重启解除。**

**「旧版离线前置检查」（`:18-36`）**：命令 `sudo robot-agent production-check robot-pi`（`:23`）或 `make production-check`（`:25`）。它做什么（`:28`）：执行无动作 preflight，检查 `ROBOT_ENTITY_PROVIDER` 与 `ROBOT_VERIFIER_PROVIDER` 可导入/调用，读取传统 `evidence/hardware-trials.json` 与 `evidence/safety-checklist.json`。**它不做什么**：不会调用 provider 验证物体、不会评估模型输出、不会重新进行硬件试验。通过输出（`:33`）：`READY xlerobot offline prerequisites passed; physical readiness is not verified`。

**「使用逐次证据而非预填通过」（`:38-42`）**：新现场用 Sim2Real kit 记录 inventory/integration/pilot，逐次保存 simulation、safety、estop、network、duplicate、trial、soak 结果；记录绑定配置和制品 hash；实际模型/标定/地图/安全限制变化后**重新检查**相应证据。旧脚本的数值门槛：硬件记录要求 `completed_trials >= 30` 及三个故障演练布尔值（代码侧 `scripts/xlerobot_production_check.py:75-77` 在 `completed_trials < 30` 时报 `must be an integer >= 30`）；安全记录要求实体急停和现场操作员布尔值。原文的纪律："**这些人工声明不校验事实，不能把它们当成开关预填为 true。新用户不应从文档复制一个全 true JSON 来取得 READY。**"

**「现场放行顺序」（`:44-51`，6 步）**：① 匹配固定软件与所购硬件，完成接线、实体急停和稳定串口 → ② 现场标定，校验关节、相机、坐标变换与安全限制 → ③ 无动作预检和 mTLS 配对；接入真实感知、经过评估的动作策略与 verifier → ④ 显式连接、现场 arm，先单关节/夹爪、空载 bench，再单机轻物任务 → ⑤ 记录急停、断网、重复/未知命令与持物恢复；**失败同样保留** → ⑥ 完成独立真实 trial 和 soak 门槛，再由现场责任人对受限试点作出决定；扩大到生产任务需追加容量、长稳、备份恢复及必要安全评审。

结尾（`:53`）：至少 30 次 trial 或 pilot evidence 通过**不会自动生成 PHYSICAL_GO 或生产认证**。

#### 17.2 `docs/operations/safety-checklist.md`（22 行，14 条 checkbox，全未勾选）

使用说明（`:3`）：配合购机指南与首次试验流程使用；**仓库没有已完成的物理验收结果。软件 READY 不是移动硬件的许可。**

"Before any supervised trial, the operator must verify:"（`:5`）——逐条：

1. `:7` 独立的物理急停在**不用软件**的情况下切断执行器电源，且整个试验期间可触及。
2. `:8` 工作区没有人员、宠物、线缆和易碎物；操作员站在运动包络之外。
3. `:9` 所购硬件、控制器映射、固件、pinned 驱动与标定身份与记录在案的套件一致。
4. `:10` 串口有稳定名称与正确权限；**没有其它服务占用同一个控制器**。
5. `:11` 标定是最新的；使能扭矩或执行标定需要**显式的现场监督**。
6. `:12` 桌面 profile 拒绝所有 `x.vel` 与 `theta.vel` 动作键；移动底盘操作在本 profile 之外。
7. `:13` 关节范围、相对目标、动作长度、速度与工作空间限制已为本设备选定并验证。**默认值 `8.0` 与 `64` 是软件默认，不是硬件认证的限制。**
8. `:14` 启动/停止服务前机械臂已被支撑：默认 systemd 启动是**扭矩关闭**连接，这会禁用现有扭矩并配置寄存器——**连接本身不是物理上无动作的**。
9. `:15` 本次进程的显式现场 arm 授权已完成；**重启的服务不会自动 arm**。
10. `:16` 空载、低速 bench 运动与受控取消在带载荷作业之前成功。
11. `:17` 软件停止与物理急停都已测试，并记录了**实际响应时间**。
12. `:18` 断网已按配置的命令租约/看门狗与硬件响应预算测试过；**不假设固定的 1 秒保证**。
13. `:19` 过期、重复或结果不确定的命令**不会重复物理运动**；journal 与观测对账已验证。
14. `:20` 真实感知与验证与被观察到的结果一致；第一次带载的物体是软的、轻的、非液体且非尖锐的。

收尾（`:22`）：急停之后必须检查机器人与工作区，再经**本地显式**放行；**云端不能清除锁存；重启或删除 journal 不是恢复程序**；用 Sim2Real kit 记录通过与失败的试验、配置身份与证据。

#### 17.3 `docs/operations/release-checklist.md`（34 行，19 条 checkbox，全未勾选）

使用说明（`:3`）：用于把家庭场景从开发分支交给现场集成；所有命令都应记录 commit、环境、输出目录和失败原因；**通过一项不替代其他项**。

**软件与合同（`:5-11`）**：`make build` / `make generate-check` / `make lint` / `git diff --check` 通过（`:7`）；`PYTHONPATH=sim/mujoco .venv/bin/pytest -q sim/mujoco/tests/test_home_scene.py` 通过，确认五个房间、双 RGB-D 和 `verify_arrival` 证据（`:8`）；`go test ./agent/intent ./skills/manipulation ./edge/robotclient ./edge/agent ./tasks` 通过（`:9`）；`pytest -q tests/install/test_navigation_stack.py robot/ros2_ws/src/tangying_navigation/test/test_launch_config.py` 通过，确认 `--scene home` 同时传到仿真/Compose/launch（`:10`）；`make test-web` 通过，用户模式不显示底层动作输入，开发诊断仍可按 task/revision/step/command/observation 定位（`:11`）。

**家庭仿真（`:13-19`）**：启动 `bash scripts/home-slam-stack.sh restart --sim-port 51051 --agent-port 8878`，确认健康状态、头部/底盘相机与 `scene_revision=home-4room-rgbd-v1`（`:15`）；用"从客厅出发，去厨房确认一下环境"创建任务，检查 `observe_scene → navigation.navigate → verify_arrival` 顺序与**同次底盘证据**（`:16`）；用"巡检卧室和卫生间，最后回到客厅"检查三段路线、回到起点标记与每段独立 checkpoint（`:17`）；在工具边界暂停、重启 Local Agent、显式继续；**确认已完成导航不重复发送，未确认的物理结果保持阻断**（`:18`）；家庭模型中没有 `ikea_cart`、桌面物体或全局环境实体；相机观测为空处**保持未知**（`:19`）。

**ROS 2 / RTAB-Map（`:21-26`）**：`make navigation-restart NAVIGATION_ARGS='--build --mode mapping --scene home'` 成功，`navigation-status` 明确显示 map/定位/相机/odom 都 ready（`:23`）；实际覆盖五个房间并保存 `/data/maps/home/rtabmap.db`（容器内路径，宿主对应命名卷 `tangying-navigation-maps`），停止后重开数据库确认视觉词典与占据图仍可用（`:24`）；用 `--mode localization --scene home` 从至少三个不同起点重复路线；丢失 RGB-D/TF/odom 或视觉质量时必须停止并**保留冻结失败证据**（`:25`）；逐项验证取消、断网、底部相机断流、急停、速度看门狗、地图版本漂移和进程重启；**不能自动重放未知物理命令**（`:26`）。

**实机放行（`:28-34`）**：profile、驱动、相机内参/外参、odom、工具 catalog 和策略制品已绑定版本与哈希（`:30`）；头部与底盘 RGB-D 在真实房屋照明、反光地面、窄门、低矮障碍和家具遮挡下通过观测合同检查（`:31`）；完成低速软围栏、刹车距离、实体急停、断网归零、持物恢复和人工接管演练（`:32`）；至少 **30 次**单机器人路线和一次长稳运行通过；记录成功率、定位丢失、停止延迟、失败证据和回滚版本（`:33`）；**现场负责人签字后才能进入受监护试点**；无人值守生产、通用家务和多机器人协同**另行验收**（`:34`）。

另有一份同性质的第四份清单 `docs/install/xlerobot-experiment.md:7-13`（7 条 checkbox，同样全部未勾选）。

### 18. 故障注入与冷启动预检

两个工具的出处是 commit `1d7ff7702`（2026-09-17），**只新增 3 个文件、818 行插入、0 删除**：`scripts/inject_faults.py`（275 行）、`scripts/precheck.sh`（366 行）、`tests/install/test_precheck.py`（177→178 行）。此后没有提交再碰过这三个文件。**脚本侧是唯一实现，Go 侧没有对应物。**

#### 18.1 故障注入器：`scripts/inject_faults.py`

**它证明什么**（文件自述 `:6-15`）：

> "the Go tests cover the rules with synthetic findings, and the live check earlier covered one fault by hand. **Neither answers the question an operator actually has, which is 'when this robot is broken in this particular way, does the agent say the right thing'.**"
> "The three faults below are the ones the simulator is able to *prove*, and it publishes only those — **a fault list that cries wolf is worse than a short one.** Everything this driver injects therefore arrives through the same `robot.faults.v1` contract the runtime uses in production, **not through a back door**."

**注入哪些故障——只有 3 个**（`SCENARIOS`，`:67-93`）：

| 场景 | 人类观察 | 期望 agent 故障码 | 期望消息子串 | 期望建议子串 |
| --- | --- | --- | --- | --- |
| `estop` | 急停锁存：机器人被停止，软件不会自动复位 | `ANOMALY_SAFETY_STOP` | — | `人工`、`急停` |
| `no_map` | 移动场景没有启用地图：导航能力被摘掉 | `ANOMALY_COMPONENT_FAULT` | `NAV_MAP_NOT_READY` | `地图` |
| `calibration` | 工位高度与标定不一致 | `ANOMALY_COMPONENT_FAULT` | `WORKCELL_CALIBRATION_MISMATCH` | `标定` |

**怎么注入的（逐场景，这是本节最有教学价值的部分）**：

- **`estop` 是真注入**，走正式 gRPC（`proto/robot/v1/robot.proto:14` 的 `EmergencyStop`），`operator_id="fault-injector"`（`:96-100`、调用点 `:160-162`）。
- **`no_map` 与 `calibration` 不注入，只观测**（`:163-172`）："The map and calibration faults are produced by the runtime's own commissioning checks. **They cannot be forced from outside**, so the driver reports what the runtime is already publishing rather than pretending to inject them — **a fabricated injection would prove nothing**."标为 `result["injected"] = False` 并给出 N/A 结论。
- **没有使用的手段**：不改配置文件、不杀进程、不断网、不改写模拟器状态、不返回伪造错误码。（另一支脚本 `scripts/run_navigation_source_fault.py` 用 SIGSTOP/SIGCONT 断采集桥，**与注入器无任何代码关系**，只在本该区分的地方被文档同称为"故障注入"——书里不要混为一谈。）
- **清理不可能**：`clear_estop`（`:103-115`）按 `ClearEmergencyStop` / `ResetEmergencyStop` / `ReleaseEmergencyStop` 顺序探测，全都不存在。全仓 grep 这三个名字**只命中脚本自己**；`proto` 的 RobotRuntime 只有 7 个 RPC，被 `tests/contract/test_proto_schema.py:28` 钉死。**"急停没有解除 RPC"不是遗漏，是设计的直接后果**（`docs/architecture/review-agent.md:122-126`）。

**怎么判定"有没有发现问题"**：轮询 `GET {console}/v1/agent/alerts`，合并 `alerts + runnerAlerts`（`:148-149`），窗口 **25 秒**（`:42`，注释说明 agent 默认 5 s 一 tick，等更短会得到假阴性），间隔 1.5 s。判定三条：`active` 为真、`code` 相等、**`message` 含期望子串**。第三条的动机是一次真实暴露的缺陷（`:54-59`）：

> "**Two different module faults both report `ANOMALY_COMPONENT_FAULT`, so matching on the code alone would accept the wrong fault as evidence that the right one was found.**"

结论分档（`:187-203`）：`定位并给出可执行建议` / `定位了，但建议里缺少 {missing}` / `定位了，但没有给出建议`。**退出码**（`:266-271`）：`0` 全部通过 / `1` 有遗漏 / `2` 用法错误或连不上 / **`3` 所有场景的故障当前都不存在**——最后这条专门用来区分"注入没生效"和"都通过了"（提交正文原话）。注意 docstring `:22-23` **漏写了 3**。

真实命令行（docstring `:17-20`）：

```bash
.venv/bin/python scripts/inject_faults.py --runtime 127.0.0.1:50199 \
    --console http://127.0.0.1:8890 --scenario estop
.venv/bin/python scripts/inject_faults.py --runtime … --console … --all
```

文档记录的实测输出（`docs/architecture/supervision-verification.md:184-193`，本次未复跑）：

```
[N/A ] calibration: 工位高度与标定不一致  →  当前不存在此故障（无法注入，运行时未上报）
[OK  ] estop: 急停锁存               →  定位: 机器人处于急停状态，所有依赖运动的能力都已不可用
                                        建议: 确认现场安全后，由人工复位急停按钮
[OK  ] no_map: 移动场景没有启用地图     →  定位: chassis 报告 NAV_MAP_NOT_READY：no active map is loaded
                                        建议: 按机器人给出的处置方式处理：先完成巡检建图并在控制台启用地图。
```

**必须标注的三条现状**：① **注入器没有任何执行级测试**——`tests/install/test_precheck.py:150,175` 只是 `read_text()` 做字符串断言，从不运行脚本；"运行时那 3 个故障码是否真能被 agent 检出"没有自动化覆盖。② `expect_manual` 是**死字段**（定义 `:64` + 三处赋值，零读取点），而作者写它的意图恰恰是防未来自动化（`:72-73`："Clearing an e-stop is a person's job. If the advice ever stops saying so, someone will eventually try to automate it."）。③ 设想的登记制度尚未实现：`docs/architecture/agent-evaluation-system.md:327` 要求"故障注入必须登记注入位置、起止时间、强度、可观察性、独立注入回执、是否实际生效与解除条件……**注入未生效的案例按预注册规则判实验无效并留存，不伪装成系统检出失败**"。

#### 18.2 冷启动预检：`scripts/precheck.sh`

**它证明什么**（`:8-19`）：

> "Why this exists: install.sh validates the platform and dies with 'unsupported platform for <role>', **which tells you that you failed but not what this machine has or how far off it is.** On a fresh machine that is the difference between **one command and an afternoon**."
> "* **It only reads.** It installs nothing, changes no configuration, and starts no service. **A precheck that fixes things cannot be run to find out.**
> * **A missing optional tool is a WARN, not a FAIL.** Docker is only needed for the cloud stack, and a robot build is not a cloud build. **Reporting optional gaps as failures is how a check stops being read.**"

`set -uo pipefail` 且**刻意不加 `-e`**（`:24`）；三态 `PASS/FAIL/WARN`；颜色仅在 `[ -t 1 ]` 且 `NO_COLOR` 未设时启用。

**检查项（逐条）**：

*全局 `report_platform`（`:87-98`）*：打印 `os/distro/version/arch`；平台不在支持矩阵内 → **WARN** + note "install.sh will refuse it. That is a supported-platform decision, not a toolchain problem."

*工具探针*：

| 探针 | PASS 判据 | 缺/旧时 |
| --- | --- | --- |
| `probe_go`（`:145-157`） | `go version` ≥ **1.26** | FAIL `go is not installed (need 1.26+)` + 安装指引 |
| `probe_python`（`:159-170`） | ≥ **3.11** | FAIL `python3 is not installed (need 3.11+; the project pins 3.11.9)` |
| `probe_node`（`:172-178`） | 仅 `command -v node` | **WARN** `only needed for the frontend tests, not to run the product`（**不查版本**） |
| `probe_docker`（`:180-191`） | `docker info` 成功 | WARN（未装 / 守护进程不通两种情况分开） |
| `probe_openssl`（`:193-199`） | `command -v openssl` | FAIL（robot 与 cloud 角色要生成和检查证书） |

*角色 `sim`（`:230-257`，7 项）*：平台 → Python → Go → Node → `artifacts/sim-assets` 目录（WARN，首次启动会自己准备）→ `artifacts/maps/furnished-home`（WARN，"run the mapping pass before a navigation task"）→ `.venv/bin/python` 可执行（WARN）。

*角色 `local`（`:259-273`，4 项）*：平台 → Go → Python → **checkout 可写**（`[ -w "$ROOT_DIR" ]`，理由："the build writes bin/ and .venv/"）。

*角色 `robot-pi`（`:275-302`，6 项）*：平台（**仅 `linux:ubuntu:24.04:arm64`**）→ Python → openssl → `robot-pi.env` 可读（WARN）→ **`scripts/robot-pi-preflight.sh` 是否可执行（只报存在性，不执行）** → `/dev/tangying-left|right` 是否存在（WARN，"run only after installing the udev rule"）。

*角色 `cloud`（`:304-330`，5 项）*：平台检查**恒返回 0**（注释说明 cloud 不是 install.sh 角色，是容器栈，要求是 Docker 而不是平台行）→ Docker → openssl → `deploy/cloud/docker-compose.yml` 存在 → `deploy/cloud/.env` 存在（WARN，首次 `fleet-up.sh` 会生成）→ **端口占用**（`lsof`，查 `${FLEET_HTTPS_PORT:-443}` 与 `${FLEET_GRPC_PORT:-8444}`，只警告不阻挡；macOS 上占用者常常就是 Docker Desktop 本身）。

**版本比较器是本工具最关键的可测单元**（`:116-143`），而且它的历史缺陷被写在源码注释里（`:104-115`）：

> "The expansion version of this function was written first and was wrong: inside `${x%%[0-9]*}` the alternation is a trap, and it **silently classified 1.26.2 as older than 1.26**. **A precheck that misjudges is worse than no precheck, because it sends someone to fix a machine that was already fine**, so the parsing is now explicit and has its own unit test."

改为 awk 精确提取后支持 `go1.26.2` / `Python 3.11.9` / `v24.14.1` / `1.26.2-rc1`；缺 minor/patch 按 0 计（所以 `1.26` 不早于 `1.26.0`）；**没有任何前导数字的一律 `unknown`，永不放行**（`:119`、`:133`）。

**运行方式与退出码**：`./scripts/precheck.sh`（四角色全查）、`./scripts/precheck.sh local`、`./scripts/precheck.sh sim robot-pi`、`--help`。退出码 `0` 每个必需检查通过（WARN 不影响）/ `1` 至少一个必需检查失败 / `2` 用法错误。本次实测 `./scripts/precheck.sh sim local` → `passed: 11 check(s) passed, 0 warning(s)`，exit 0；输出含 `python3 3.11.9`、`go go1.26.2`、`node v24.14.1`。

**它和 `robot-agent doctor` 是两个不同的东西**——这是本节的核心结论，证据是硬的：

| | `scripts/precheck.sh` | `robot-agent doctor` | `scripts/robot-pi-preflight.sh` |
| --- | --- | --- | --- |
| 何时能跑 | **安装前**，clone 完即可 | **安装后**：第一步读安装回执 | 上电前，需已部署 |
| 前置依赖 | 无 | `<StateDir>/install.json` 必须存在 | `robot-pi.env`、`.venv` |
| 角色覆盖 | 4 个（含 cloud） | 受回执 role 限制 | 仅 robot-pi |
| 实现 | 纯 bash，只读 | Go，`internal/robotagent/app.go:385-419` | bash + `scripts/xlerobot_preflight.py` |

`doctor` 在**未安装的机器上必然报错退出**（`app.go:390` 调 `a.receipt()`，`receipt()` 读不到 `install.json` 就 `fmt.Errorf("read installation receipt: %w", err)`，`app.go:122-126`），所以它**无法回答 precheck 要回答的问题**。反过来 `precheck.sh` 对 robot-pi **只检查 preflight 脚本是否存在**，不运行它（`:291-295`，注释："The serial devices and calibration are what scripts/robot-pi-preflight.sh checks; this only reports whether that check can run at all."）。`precheck.sh` 还明确说明为什么**不 source** `scripts/install/common.sh`（`:203-205`）："that file exits the shell on a mismatch, **which is exactly what a precheck must not do**."

两侧的平台行由测试钉住对齐（`tests/install/test_precheck.py:123` 断言 `common.sh` 仍含三行平台，且 precheck 含最窄那条 `"linux:ubuntu:24.04:arm64) return 0 ;;"`），但**没有测试断言二者的检查项集合一致**。

**这两个工具证明了什么（提交正文原话）**：

> 冷启动预检 + 故障注入器：让"**能不能装**"和"**有没有发现问题**"都可验证

也就是说，它们各自关掉一类"无法验证"的缺口：**precheck 把"这台机器能不能装"从"跑了才知道"变成"一条只读命令先告诉你差多远"**；**注入器把"故障发生时 agent 会不会说对话"从"靠人手测一次"变成"可反复跑的脚本 + 退出码"**。

**四下现状必须并列写出**（否则书会夸大）：① `scripts/precheck.sh` **不在任何 `bash -n` 列表、不在 CI、不被 `install.sh` 调用**；唯一入口是手敲与 `tests/install/test_precheck.py`（`Makefile:60-61` 的 `install-check` 目标跑 `pytest tests/install -q`，因此测试在 CI 内，但 `.github/workflows/ci.yml:36-39` 只跑 `make test`/`make lint`/`make generate-check`/`go test ./tests/docs`，**不跑 `make install-check`**，也不跑 `precheck.sh` 或 `inject_faults.py`）。② 预检里"the project pins 3.11.9"与仓库不符：全仓无 `.python-version`/`.tool-versions`，`pyproject.toml:8` 是 `requires-python = ">=3.11"`，`3.11.9` 只出现在验证记录与测试样例里——**3.11.9 是参考环境版本，不是机器可读的 pin**（Go 侧则确实是 pin：`go.mod:3` `go 1.26`、`common.sh:8` `1.26.2`）。③ `probe_node` 不校验版本，而测试用例里却有 `v24.14.1 ≥ 18.0`——**"node ≥ 18"这条要求没有任何地方真正执行**。④ 提交正文与归档 CHANGELOG 说"12 个用例"，实测版本比较器用例是 **7 + 6 = 13**；归档 CHANGELOG 另写"（6 条）"，实际是 8 个测试函数。

---

## 第四部分 横向对比与教学

### 19. 与通用 coding agent 的运维差异：用代码事实说话

用户提出的对比是：coding agent 的失败通常是"测试红了"；机器人 agent 的失败可能是"机器人卡在走廊"或"抓着杯子不动了"。这个直觉在代码里有非常明确的对应物。以下六条全部有源码依据。

**① 判据是否独立——两者的闭环强度根本不同。**
coding agent 的判据（测试通过/不通过）是**可重放的、成本近零的、可重复的**。机器人 agent 的判据是**世界本身**：`core/closedloop/closedloop.go:1-15` 的包注释把这条写成公理——"**A tool result is a trigger to look at the world, never proof that the world changed as intended.**" 更关键的是 `train/gate.go:14-19` 那句对照，它同时解释了为什么训练不能是 Agent、也解释了为什么机器人运维比 CI 难：

> "A robot's closed loop works because **the world is the judge and the world does not care what the policy wanted: either the cup moved or it did not.**"

**② 失败分类必须区分"动作没发生"与"动作发生了但没确认"。** 这是 coding agent 里不存在的维度。`closedloop.go:168-212` 把 `GRASP_FAILED`/`PLACE_NOT_REACHED` 归 Perception（手是空的 / 什么都没释放，**效果确证未发生**），而把 `*_NOT_OBSERVED` 归 `UNKNOWN_OUTCOME`（动作已经执行、只是没观测到预期关系）。原文的理由是可以直接讲给运维听的：

> "Treating it as perception would permit a retry that repeats a physical action whose first attempt may have worked — **for a place, the object could already be sitting in the destination.**"

**③ 告警设计：从"报错"到"问题"，中间隔着一整层聚合。** coding agent 的告警粒度通常是"哪个测试红了"。机器人侧的原始报告同样是"报告"，而参考部署的实际规模是**数百条报告**——`tasks/alerts.go:266-270` 记着 "276 active findings that were 92 problems"，`web/problems.js:9` 记着 "**276 rows in a strip above the task input, which nobody could review and so nobody read**"。解决的层次有三：投影（`tasks/alerts.go`，从**当前状态**投影而非累积事件流，条件消除即 `active=false`，**没有 dismiss 协议**）、聚类（`tasks/alert_groups.go`，按 `code@component` 折成问题）、分级处置（`Handling` 三分由计划推出，不由看法推出）。这条链的设计目标用一句话概括在 `web/problems.js:12-13`："**The banner says how many problems there are and links here; this page is where they are actually dealt with.**"

**④ 人工介入的形状不同：不是"点一下继续"，而是"带证据的批准"。** coding agent 的 approve 通常是"允许写文件"。机器人侧的批准有三个硬特征：**（a）批准一个具名动作，而不是一类权限**（`console/recovery_execute.go:22-27`："One action from the catalog, by id. **Not 'whatever the model decides would help'**"）；**（b）批准的范围就是该动作声明的工具集合**（`internal/actionloop/loop.go:208-226`）；**（c）批准要留可核对的证据**（`recoveryexec/executor.go:353-371` 的 `OperatorApproved` + `ApprovalEvidence`，`console/recovery_execute.go:29-44` 明确写"这是一个比'有人批准了'更窄的声明，而它是这段代码有资格做的声明"）。此外还有一条 coding agent 里没有的：**批准一次的物理动作仍然可能"未确认"**——`executed` 与 `verified` 是两个字段（`executor.go:139-161`），并且"没有执行就不复验"（`:322-332`）。

**⑤ 恢复动作的安全性：不是"能不能做"，而是"谁能做"被写成了数据结构，而不是检查项。** 三处可以拿来对比：

- **OpsAgent 结构上没有执行端口**（`agentruntime/opsagent.go:19-30`）："This type holds no execution port, no task service and no bus handle. There is no field through which it could dispatch an action … so 'the observer cannot touch the robot' is **a property of the type** rather than a rule someone has to remember to enforce."并有反射测试钉住（`cmd/local-agent/recovery_wiring_test.go`）。
- **自动通道结构上够不到写动作**（`internal/autorecovery/supervisor.go:183-189`）："**The guard and the capability are the same fact.**"
- **越权调用必须留证**（`internal/actionloop/loop.go:452-467`）：带工具名、参数与模型理由停机，而不是静默拒绝。

**⑥ 危险建议是独立指标，而不是"不安全的反面"。** 评测体系里专门统计"危险建议率"（把未决执行当成可重试、把 blocked 步骤放进 ready、恢复时越过守卫派发 `execute_step`、交接跳过未决动作——`docs/experiments/2026-09-21-agent-context-evaluation.md` §12.5）。这在 coding agent 评测里通常不需要单列。而实测值本身也说明问题：第二轮留出里 `全部 JSON` 危险提议率 0.94%，分环节策略 2.19%，元数据标注混合 0.31%；第三轮确认里 Pro 的恢复环节危险建议率高达 **25.00%**，因此该环节被写进 `deployment-policy.json` 的 `blocked_stages`，**拒绝自动套用**。书里应强调这个态度：**"准确率并列但危险率高"足以否掉一个候选，不需要等它变差。**

**⑦ 最后一条最本质的差异：有些动作必须由人来按，而且"做不到"要写进接口。**
故障注入器最生动的证据是它**跑不干净自己注入的急停**——`proto/robot/v1/robot.proto` 的 RobotRuntime 只有 7 个 RPC，**没有解除急停的接口**，`tests/contract/test_proto_schema.py:28` 把这个集合钉死。作者的自述（`docs/architecture/review-agent.md:126`）：

> "我写故障注入器时想让脚本自己清理，发现根本没有这个接口——**这是设计的直接后果，不是遗漏。**"

对照 coding agent：如果某个操作不该被自动化，通常靠"不给权限"或"文档要求"；这里靠的是**接口层不存在这个能力**，并把这份缺失作为测试断言固化下来。

### 20. 教学价值：三个练习题与第一天的阅读路径

#### 20.1 练习题

**练习一：把一条"结果未知"变成一条结论。** 场景：任务 `task-97…` 的 `manipulation.pick` 步骤状态是 `STARTED`，机器人已经重启，控制台 `readiness` 报 `reconciliation` 阻塞、`ready=false`。请写出你作为运维要走的**全部**步骤，并说明每一步在代码里由什么支撑、哪一步软件**不能**替你做。

*答案要点*：
1. 读 `GET /v1/tasks/{id}/recovery`，看 `requiresReconciliation` 与 `uncertainStepIds`（`tasks/local_recovery.go:6-17`）。
2. **去现场看**：物体是否在夹爪中、是否已在目标位置——这一步**不能靠软件推断**（`scripts/diagnose_task.py:53`）。
3. 读该步骤的 evidence（`rgbsha256`/`depths ha256` 指向的采集）确认"当时看到了什么"（`incidents/bundle.go:63-71`；`GET /v1/tasks/{id}/observations`）。
4. 用 `POST /v1/tasks/{id}/reconcile` 记录结论 `{stepId, outcome ∈ HAPPENED|NEVER_ACTED|ABANDONED, note}`。**人和依据缺一不可**，而且**只能写一次**（第二次会被 `reconcile_outcome = ''` 的条件拒绝，`middleware/sqlite/store.go:220-244`）。
5. 结论写入后**步骤自身状态不变**（结果确实未知，记录继续这么说），变的是 `UncertainStepIDs` 不再包含它 → `RequiresReconciliation=false` → `CanResume` 可以变真 → `readiness` 的 `reconciliation` 检查变绿（`internal/localapp/recovery.go:39-50`）。
6. **软件不能替你做的是第 2 步与第 4 步的"依据"**：`console/reconcile.go:20-24` 明写"no timer clears a step, no agent may"。
7. **不能做的事**：不要重放那个物理动作，也不要通过新建一个同样任务来绕过核对（`console/readiness.go:57` 的 `Reason` 原文："系统不会重放，也不允许通过修改任务版本绕过"）。

**练习二：给一个"已知失败被报成结果未知"的故障码补分类，并证明你补对了。** 场景：运维发现 `GRASP_NOT_REACHED` 在巡检报告里落进 `unclassified`，而系统对它的建议只有"不要重试"。

*答案要点*：
1. 先定位它**归谁管**：Go 分类表（`core/closedloop/closedloop.go:83-274`）与 Python 故障族表（`scripts/diagnose_task.py:42-187`）是**两套独立词表**，`GRASP_NOT_REACHED` 只在 Go 表里（`:176`，归 Perception），Python 表里没有 → 所以巡检报"未归类"。
2. 判断它**应该**归哪一类：`GRASP_NOT_REACHED` 是"末端在容差内到不了物体"，属于**已知、可解释**的失败，正确恢复是"重新观测再试"，因此归 `PERCEPTION`（可重试类）。
3. 在 Go 侧：如果它不在表里，`Classify` 会兜底成 `UnknownOutcome` 并禁止重试——这正是 `classification_coverage_test.go:15-30` 开头记录的那个真实事故。补表后 `Knows()` 必须为真，`TestTheClassifierTableCoversCommonRuntimeCodes`（`:89`）会检查。
4. 在 Python 侧：把它加进 `FAMILIES` 的对应族（或新建族）的 `codes`，并在该族 `tests` 里放**真实存在**的测试路径。
5. **验收判据**：`go test ./core/closedloop`；`.venv/bin/python scripts/diagnose_task.py --sweep artifacts/incidents` 的**未归类计数下降且退出码变化**（本次实测：64 份里 26 份未归类、exit 1）。
6. **陷阱**：不要只改一边。书里要强调，第 3 节记录的跨语言分歧（commit `5dfa9728e` 发现 Go/Python 对 87 个码判定不一致）说明"两套词表必然漂移"，正确做法是让一致性测试（目前只覆盖 `tool_layer.py`）也覆盖 `diagnose_task.py`。

**练习三：判断一次恢复执行到底成功了没有。** 场景：控制台显示某次 `observe.re-read` 执行返回，字段是 `executed: false, verified: false`，轨迹里有 `tools.resolved`、`not-executed`、`verification.not-applicable`。请说明这三个字段各自意味着什么、这次执行算不算失败、以及为什么系统**没有**报"已恢复"。

*答案要点*：
1. `executed=false` 的语义被收紧过：它取自 `actionloop.Outcome.Calls > 0`（`internal/recoveryexec/executor.go:311`），而 `Calls` 只在唯一一处真正下发调用的地方自增。修复前 `Executed` 在循环返回后无条件为 `true`，于是**一次调用都没发生的运行被报成 `executed: true, verified: true`**（`docs/architecture/recovery-agent.md:233-243`）。
2. `verified=false` 在这里**不是失败**：没有执行就不复验，写"没有执行任何动作，因此没有可复验的结果"（`executor.go:328-332`）。理由："一个从未被触碰过的状态被复验器判为'通过'是本包能产生的最坏输出。"
3. `not-executed` 的原因需要看 `Result.Reason`：本场景是"没有配置决策器"，单工具动作本可走 `SingleToolDecider`，但该路径只在**只读且不需要批准**时才启用（`executor.go:454-462`）；且生产组合根里 `Verify` 为空，所以即便执行了也只会得到"已执行但未确认"。
4. 因此正确结论是"**这次没有动作被下发**，不是一个失败，也不是一次恢复"。
5. **加分点**：指出"系统能做到什么"的边界——`docs/architecture/recovery-agent.md:297-300` 明写"只读对账不能消解'结果未知'的发现。系统能把证据摆到人面前，不能替人下结论"。

#### 20.2 第一天应该看什么（阅读路径）

**给一个刚接手这套系统的初学者的顺序**，每一步都配"看完你应该能回答什么"：

1. **先建立"失败要分类"的直觉** → `core/closedloop/closedloop.go`（373 行，先读 `:1-68` 的包注释与八类定义，再读表）。*能回答：为什么"不认识的码"要归 `UNKNOWN_OUTCOME`？为什么只有两类可重试？*
2. **再看这个直觉怎么变成契约** → `core/closedloop/classification_coverage_test.go`（395 行）。*能回答：新增一个故障码时，是什么在逼你做决定？*
3. **然后看故障怎么从机器人变成观测** → `core/robotcontract/faults.go`（202 行）+ `docs/architecture/module-health-and-faults.md`。*能回答：什么故障会摘能力？摘哪些？对正在跑的任务有什么影响？*
4. **接着看"发现"这件事本身** → `agentruntime/opsrules.go`（655 行，八条规则 + 三个严重度）+ `docs/architecture/supervision-verification.md`。*能回答：OpsAgent 为什么结构上不能动机器人？*
5. **再看"发现问题之后怎么办"** → `agentruntime/recoverycatalog.go`（356 行，16 条动作）→ `internal/autorecovery/supervisor.go`（230 行）→ `internal/recoveryexec/executor.go`（512 行）。*能回答：为什么自动通道只跑只读？"批准范围"和"批准端口"为什么要分开？*
6. **然后看人看到的界面** → `web/problems.js`（320 行，读文件头注释即可）+ `console/readiness.go`（555 行）+ `docs/architecture/readiness.md`。*能回答：为什么横幅和问题页要分成两个东西？`ready=false` 时第一个该做什么？*
7. **最后看怎么验证它** → `scripts/precheck.sh`（366 行）+ `scripts/inject_faults.py`（275 行）+ `scripts/diagnose_task.py`（660 行）。*能回答：这三个脚本分别回答什么问题？它们的退出码在说什么？*

**如果只有半天**，读这四份文件即可覆盖 80% 的运维直觉：`core/closedloop/closedloop.go`（失败分类）、`internal/autorecovery/supervisor.go` 的包注释（`:1-35`，自动边界）、`console/readiness.go` 的包注释（`:15-34`，liveness 与 readiness 的区别）、`docs/architecture/readiness.md` §7（锋利边缘）。这四段文字加起来不到 300 行，但它们定义了这套系统**在什么情况下会停下来等人，以及为什么**。

**给初学者的第一条纪律**，从这套代码里可以直接抄：**"看不到的东西不要说成没问题。"** 它在至少五处独立出现——`readiness` 的 `ReadinessUnknown` 状态、`console/alerts.go:31-35` 的"没有监督 agent 时控制台必须说不确定而不是不说"、`web/problems.js` 的"读不到 != 没有问题"、`faults.go:183-185` 的"一份看不懂的故障文档不能被当成'大概没事'"、以及 `edge/robotclient/faults_test.go` 里的 `TestAnOlderRuntimeWithoutFaultsIsUnknownNotHealthy`。这比任何一条具体规则都更接近这套系统的性格。

---

## 附：引用陷阱与待定夺清单

写书时最容易踩的 12 处，全部在正文里有对应证据，此处集中列出便于校对：

| # | 陷阱 | 正确写法 |
| --- | --- | --- |
| 1 | 版本号 | 仓库自报 **0.7.0**（`VERSION:1`），不是任务描述里的 v0.6.0；最新 tag 是 v0.6.0 |
| 2 | "113 个故障码" | 那是 **2026-09-17 那次审计**的规模（commit `c8cae5af2` 的守卫清单恰好 113 个唯一码）；当前 `runtimeEmittedCodes` 是 **119 唯一**，分类表 **177 唯一** |
| 3 | `incident.v1` | 有两个同名不同物的 schema：Go 的 `incident.bundle.v1`（只有事实）与 Python 的 `incident.v1`（事实+诊断）。不要混为一谈 |
| 4 | "279 → 7" | 正文实测是"**120 条报告 → 7 个问题**"，commit 标题与改进计划写 279→7，`tasks/alert_groups.go:34-41` 与 `web/app.js` 写 **276→8**，`tasks/alerts.go:266-270` 写 276→**92（分组前）**。建议写"数百条报告降到个位数问题"并注明出处 |
| 5 | `readiness.md` §7 | 文档说"结果未知没有清除路径"，**代码已经有**（`console/reconcile.go`）。这是文档过期，不是代码没做 |
| 6 | `Verify` | 复验端口存在、语义严格，但**生产组合根里没有装配**（`cmd/local-agent/main.go:339-403`）。所以"每次恢复都报未确认"是当前真实状态，不是 bug |
| 7 | 三份清单 | **没有任何一条被勾选**（仓库内 `- [x]` 数量为 0）。清单是待办，不是成绩单 |
| 8 | `install.sh` 的角色 | **只有 3 个**（sim/local/robot-pi），`cloud` 已移除并硬报错。四个角色只出现在预检脚本里 |
| 9 | `datasets/` | **只有 5 个 0 字节标记文件**；真实数据（RoboCasa，约 4.2G）已被清理，训练样本在 `artifacts/*/training/*.jsonl` |
| 10 | "故障注入" | `scripts/inject_faults.py` 与 `scripts/run_navigation_source_fault.py` 是**两个无关工具**，只在文档里同名。前者真实注入 1 个场景（estop），另 2 个是"N/A 不伪造" |
| 11 | 工具数与步数 | README 写 28 个工具、`tools.json` 是 29；同一用例的规划长度两处文档分别记 14 步与 22 步 |
| 12 | 跨机学习 | `why-distributed.md:124` 把它列为架构**优势**，但代码层**未实现**（`recovery-agent.md:306` 明确"不跨机器人"，`memory.go:8-13` 说是"前瞻性工作的全部"） |

另有几条"建好了但没进 CI"的同类事实，书里可用于讨论"已实现"与"在生产生效"的区别：`scripts/gate_agent_context.py` 不被任何 Makefile 目标调用；`train/` 与 `training/` 不在 `GO_TEST_PACKAGES`；`scripts/precheck.sh` 不在任何 `bash -n` 列表、不被 `install.sh` 调用；故障注入器没有任何执行级测试（只有对源码文本的字符串断言）。

**篇幅说明（如何压缩到 5000–7000 字的章节正文）**：本文是第 13/14/15 章的核心素材与事实底稿，按"可直接引用、可逐条核查"的标准撰写，因此保留了完整数据表、逐条清单与全部 `文件:行号`。压缩建议：

- **建议整段保留**（价值在于数字与代码位置的精确性）：第一部分全部 8 节；§10 第三轮的四因素主效应表、反事实表与最终配置表；§11 的 `train/` 三条规则与"0 条正样本"；§12 的跨机学习判定；§18；文末「引用陷阱与待定夺清单」。
- **可压缩为一半**：§9 的脚本清单（可改为"9 个 `evaluate_*.py` + 11 个配套脚本"一段，只留 `evaluate_agent_system.py` 与 `gate_agent_context.py` 两句）；§14 的 `doctor` 表（保留结构，检查项改为"配置存在 + 权限不含 group/other + robot-pi 转派给 20 项无动作 preflight"）；§16 的 systemd/launchd 表（保留 `ExecStart` 与 `Restart` 两列即可）。
- **可压成清单式一段**：§10 第一轮与第二轮的留出表（保留"8.33% → 74.17%，holm p=0.000092"与"65.00% → 87.19%，holm p=0.000040"两组核心数字 + 多轮实验的 5.375/4.500）；§13（保留导出格式、退出码语义与 `datasets/` 是 0 字节标记这一条）。
- **可删至 3 条代表条目**：§17 的三份清单（建议保留生产就绪的"哪些结果可以证明什么"表，因为它本身就是证据分级教材）。
- **入门阅读路径**（§20.2）建议保留全部，它是全章唯一可直接交给初学者的产物。

---

## 源码索引

> 格式：`路径:行号`。行号对应 `HEAD = 8b9683be8`（v0.7.0 准备提交）上的文件内容，见文首版本说明。

**第一部分**

- `incidents/bundle.go:1-13`（包注释：事实/分类分离）、`:28`（`SchemaVersion = "incident.bundle.v1"`）、`:31`（`DefaultDirectory`）、`:35`（`DefaultKeep = 200`）、`:39-50`（`Environment`）、`:53-59`（`StepRun`）、`:63-71`（`Evidence`）、`:74-84`（`TimelineEvent`）、`:88-95`（`Recovery`）、`:98-123`（`Task`/`Bundle`）、`:147-193`（`Write`，含 `:151` 尽力而为、`:162` 默认 note、`:182-190` write-then-rename）、`:197-207`（`Digest`）、`:210-241`（`prune`）
- `internal/localapp/app.go:18`、`:34-37`、`:225-228`（`WithIncidents`）、`:483-535`（`recordIncident`，含 `:519-523` 终态与 `terminalCode`、`:533-534` 写失败只 log）、`:590-625`（异常结束分支，`:604-613` 三种终态、`:618` 唯一调用点）
- `cmd/local-agent/main.go:420`（`WithIncidents(incidents.New(incidentDirectory(os.Getenv("TANGYING_INCIDENT_DIR"))))`）、`:576-581`（`incidentDirectory`）
- `scripts/diagnose_task.py:1-24`（docstring：事实/推测/不自动化三分）、`:37`（`SCHEMA_VERSION = "incident.v1"`）、`:42-187`（`FAMILIES` 八族）、`:190`（`_TERMINAL_HINTS`）、`:200-241`（`classify`，含 `:221-241` unclassified + `missingEvidence`）、`:248-272`（传输类正规化）、`:275-287`（`observed_codes`）、`:290-305`（`step_timings`）、`:308-319+`（`build_incident`）、`:400-460`（system 体检与 `_verdict`）、`:470-520`（`collect_bundle`）
- `artifacts/incidents/task-03ec8cde7cd2a6db8766c7da.bundle.json`（实读样例）、`artifacts/incidents/system-health.json`（`system.health.v1` 聚合样例）
- `core/closedloop/closedloop.go:1-37`（包注释，含"删除 Track 而非接线"）、`:45-68`（八类定义，`:63-65` UnknownOutcome 禁止重试）、`:78-86`（表结构）、`:87-274`（分类表；`:101` `PLACEMENT_NOT_OBSERVED`、`:121` `NAV_STOW_CONTACT`、`:176` `GRASP_NOT_REACHED`）、`:284-300`（`Classify`）、`:302-322`（`Knows`）、`:324-340`（`Retryable`）、`:342-359`（`DispatchPrecision`/`NormalizeDispatchTime`）、`:361-373`（`Evidence`）
- `core/closedloop/classification_coverage_test.go:15-30`（危险说明）、`:39-43`（`runtimeFailureCodes`）、`:55`、`:77`、`:89`、`:116-128`（`runtimeEmittedCodes` 头）、`:256`、`:277`、`:292`、`:331`、`:346`、`:363`、`:382`
- `docs/development/2026-09-15-ai-incident-diagnosis-and-ops-loop.md:5`、`:55-90`（`incident.v1` 结构）、`:91-133`（闭环七步）、`:120-128`（自动产出 + `--sweep`，`:127` 共享卷）、`:133`
- `docs/architecture/module-health-and-faults.md:13-20`（接口面）、`:29`（五层图）、`:61-73`（faults.v1 样例）、`:81`（只发布能证明的）、`:89`（能力联动）、`:96`（词汇表只在一处）、`:116`、`:147-160`（`FaultRemedyEngine`）、`:190`、`:199`、`:218`（12 条契约测试）、`:225-246`（工具与任务闭环，`:237-246` 影响分支）、`:271-275`（落地顺序与"第 2 步后唯一变化"）
- `core/robotcontract/faults.go:9-30`（`Fault`）、`:32-47`（`FaultReport`）、`:49-56`（词表）、`:58-101`（`WorstSeverity`/`Blocking`/`Keys`/`SafetyStopped`）、`:103-155`（`Validate`，`:141-153` 两视图一致）、`:183-186`（`DecodeFaults`）
- `edge/runtime/runtime.go:21`（`ErrCapabilityUnavailable`）、`:161`（能力不可用拒绝点，带 blockers）
- `internal/autorecovery/supervisor.go:1-35`（包注释：只跑 read_only 与三个停下原因）、`:50-58`（端口）、`:60-67`（`DefaultMaxActionsPerPlan = 3`）、`:74-90`（`StepOutcome`）、`:92-118`（`Supervisor`；`:96-109` 只记跳过、`:118` `ErrNoCatalog`）、`:120-167`（`Run`；`:135-137` 只要 PLAN、`:146-150` 上限、`:153-164` 两个 break）、`:169-211`（`runOne`；`:183-189` 那条线、`:199` `OperatorApproved: false`）、`:213-230`（`skip`/`record`）
- `agentruntime/recoverycatalog.go:8-25`（包注释：isApproval 属于动作不属于 agent）、`:27-41`（三档风险）、`:44-106`（`RecoveryAction`，`:62-80` Tools 为何是数据、`:81-98` `MovesTools`）、`:108-138`（目录读取类动作）、`:150-208`（写动作类）、`:210-222`（三条 never_automatic 与拒绝理由）、`:230-251`（`NewRecoveryCatalog` 校验）、`:270-285`（`Proposable`）、`:287-311`（`ForShapes` 只回只读）、`:313-335`（`Encode`）、`:337-344`（`RequiresApproval`）、`:346-356`（`Executable` 正向列举）
- `internal/actionloop/loop.go:180-193`（Verdict 常量，`:192` `OUTSIDE_APPROVED_SCOPE`）、`:194-208`（Scope 的定义与理由）、`:210-226`（`ScopeOf`）、`:270-300`（`Approve`/`Scope` 字段与 nil 语义）、`:452-467`（越界处理：记录并停机）、`:628-630`（`needsScope` 只约束物理）、`:632-638`（`withinScope`，nil → 拒绝）
- `internal/recoveryexec/executor.go:1-35`（包注释：不硬编码编排 + 批准范围双职责）、`:51-63`（`Tool`）、`:65-76`（`Registry` 延迟解析）、`:78-97`（`Observer`/`Verify`/`Verdict`）、`:99-127`（`Executor` 字段；`:104-107` 无决策器即拒绝）、`:129-136`（`ApprovalRequest`）、`:138-168`（`Result`/`Step`；`:141-148` `Executed` 的含义）、`:176-198`（`Execute`/`record` wrapper）、`:200-346`（`execute`：`:212-228` 目录、`:230-240` 工具存在、`:242-277` 批准、`:279-320` 执行与 scope、`:322-345` 复验）、`:348-371`（`Request`/`OperatorApproved`/`ApprovalEvidence`）、`:373-421`（`resolve`：`MutatesWorld` 的 OR 语义）、`:423-462`（`refusingDecider`/`decider`）、`:464-477`（`toolListMutates`）、`:502-512`（`MapRegistry`）
- `internal/recoveryexec/tools.go:48-66`（`ServiceCaller`/`EvidenceSource`）、`:68-133`（`RobotServices`）、`:134-164`（`levelOf`/`parametersOf`）、`:189-216`（`transportCode`/`requestID`）、`:217-267`（`LocalTools`）、`:268-291`（`Combined`）、`:292-322`（`EvidenceFromSnapshot`）
- `cmd/local-agent/main.go:339-403`（`recoveryexec.Executor` 装配：`:342-352` `RobotServices`、`:353-397` `LocalTools`（`ReadHistory` 用执行上下文而非参数）、`:398-402` `Observer` + 无 `Approve` 端口的理由、`:403` `Decider`；**全段无 `Verify`**）、`:404-419`（`autorecovery.Supervisor` 装配）
- `console/recovery_execute.go:1-44`（信任边界与"点击即批准"）、`:45-53`（接口）、`:55-60`（`RecoveryExecutionTimeout = 3 分钟`）、`:62-68`（请求体）、`:70-141`（handler：`:100-115` 目录边界、`:123-135` 执行与 `ApprovalEvidence`）
- `console/recovery.go:1-74`（`GET /v1/tasks/{id}/recovery`、pause/resume、错误映射 `:65-73`）
- `docs/architecture/recovery-agent.md:18-50`（为什么需要它 / 只提议不执行）、`:68-77`（拒绝否决整份计划）、`:104-135`（调查轨迹）、`:136-155`（发布通道由运行时注入）、`:156-171`（身份 `code@component`）、`:197-232`（执行通道与四条检查）、`:233-243`（`executed` 的含义与被修的假声称）、`:244-254`（真实端口实测表）、`:255-296`（自动通道；`:276-284` `rule.reconcile-first` 死锁修复；`:285-295` 52 任务实测）、`:297-308`（诚实清单，`:306` "**不跨机器人**"）、`:310-332`（实现文件表）
- `console/readiness.go:15-34`（liveness vs readiness）、`:36-50`（状态常量）、`:52-91`（`ReadinessCheck`/`ReadinessReport`）、`:93-111`（语言能力）、`:114-141`（`readinessSignals`）、`:148-174`（`buildReadiness`，六检查与 `NextID`）、`:177-203`（`orderReadiness`）、`:221-241`（`supervisionCheck`）、`:243-267`（`robotLinkCheck`）、`:269-287`（`safetyCheck`，`:270-271` 不提供按钮）、`:289-311`（`faultCheck`，用机器人自己的句子）、`:313-331`（`mapCheck`）、`:333-350`（`reconciliationCheck`）、`:352-353`（`defaultVocabulary`）、`:355-379`（`languageReadiness`）、`:382-400+`（handler 与信号采集）、`:425`、`:449`、`:514-555`（`unresolvedOutcomes`/`taskNeedsReconciliation`）
- `console/readiness_test.go:15-25`（七条承诺的说明）、`:118`、`:139`、`:162`、`:186`、`:212`、`:234`、`:250`、`:264`、`:293`、`:311`
- `console/reconcile.go:1-24`（清除路径的理由）、`:26-33`（请求体）、`:35-85`（handler，`:68-72` 依据必填）
- `middleware/sqlite/store.go:220-244`（`ReconcileStep`，`WHERE ... reconcile_outcome = ''`）
- `internal/localapp/recovery.go:13-22`（唯一写入口）、`:24-83`（`Recovery()`，`:39-50` 已对账步骤跳过、`:51` `RequiresReconciliation`、`:57-59` `PHYSICAL_OUTCOME_UNKNOWN`）、`:85-101`（`checkRevisionRecovery`）
- `console/server.go:186-217`（全部路由，重点 `:199` 恢复执行、`:200-202` 对账、`:207` 告警）
- `docs/architecture/readiness.md:75-91`（三条纪律）、`:93-107`（§7 锋利边缘）、`:109-117`（§8 相关文件，**未含 `console/reconcile.go`**）
- `docs/development/2026-09-18-system-review-and-improvement-plan.md:29`、`:165`（0.2 已完成）、`:195-205`（三个数字与因果链）、`:202`、`:405-425`（"已实现但从未通电"清单与 §P0-B）、`:415`、`:417`、`:655`、`:673`、`:822`
- `tasks/alerts.go:10-26`（从当前状态投影，无 dismiss 协议）、`:28-35`（严重度常量与理由）、`:38-111`（`AgentAlert`）、`:172-175`（`alertActive` 的解除条件）、`:207-214`（去重键与就地替换）、`:220-231`（排序）、`:236-250`（`alertActive`）、`:254-261`（stage 常量）、`:266-289`（`rootIdentity` 剥前缀后缀）、`:400-470`（按任务去重）
- `tasks/alert_groups.go:9-27`（`Handling` 三分）、`:29-97`（`AlertGroup`，`:34-41` 规模动机）、`:99-100`（`maxGroupTasks = 5`）、`:107-200`（`GroupAgentAlerts`，`:112-117` 无 identity 的降级、`:138-159` 取最新、`:163-168` 计数）、`:177-198`（排序）、`:202-232`（`handlingFor`）、`:234-254`（rank 函数）
- `console/alerts.go:10-37`（只读投影与可选监督上报）、`:40-137`（`agentAlerts`，`:104-108` 服务端聚类理由、`:121-136` 返回字段、`:129-133` 两个计数都发）、`:139-166`（runner 告警转换，`:158-159` `RobotWide`）
- `web/problems.js:1-19`（为什么是页面而不是横幅）、`:23-35`（筛选与状态）、`:112-116`（标题两数）、`:123-149`、`:286`
- `web/app.js:5212-5230`（分组渲染与 276 vs 7 的说明）、`:5223-5246`（老服务端回退并标注）、`:5256-5270`（两个计数都要给）、`:6185-6225`（标签表共用与 `renderAlertGroup`）
- `agentruntime/opsrules.go:14-45`（八个异常码）、`:47-55`（三档严重度）、`:57-71`（遥测过期 10 s、步骤预算 30 s、证据引用上限 8）、`:73-122`（`Finding` 与 `Identity`，`:110-118` 身份规则的教训）、`:123-172`（输入类型）、`:174-194`（`Evaluate` 七条规则）、`:197-241`（`safetyFindings`）、`:243-308`（`faultFindings`）、`:310+`（`uncertainMutationFindings`）
- `agentruntime/opsagent.go:14-60`（为什么结构上不能修 / 规则为什么是确定性的）、`:132-135`（能力声明）、`:424-427`（`anomalyCooldown = 60s`）、`:442`、`:444-459`（冷却键）、`:513`（`AutomationAdvisory` + `RequiresApproval: true`）、`:559-567`
- `agentruntime/recoveryagent.go:191-197`（能力声明）、`:688-696`（`recoveryPlanCooldown = 2 分钟`）
- `core/agentcontract/anomaly.go:27-31`（`AnomalyIdentity` = `code@component`）、`:46-47`（`AnomalyReportID` 与 `#count`）
- `edge/recovery/classifier.go:1-86`（六类 `Class` 与 `Classify`）
- `tasks/local_recovery.go:1-18`（`LocalRecovery` 结构）
- `tasks/reconcile.go:3-44`（`BuildChangeSet`）
- `agentruntime/permission.go:139`、`:154`
- `agentruntime/ledgerfilter.go:60-70`
- `docs/architecture/lifecycle-objects.md:475`（"唯一没有状态机的对象"）
- `docs/README.md:110`（索引条目）
- `CHANGELOG.md:3205-3234`（113 码审计与四类真实告警表）
- Git 提交正文：`c8cae5af2`（113 码审计的两次踩坑）、`5dfa9728e`（三项决策 + Go/Python 87 个码不一致）、`a634003ba`（批准范围）、`0774468f1`（279→7 与三个身份缺陷）、`336115e7f`（横幅与页面分工、276 行）、`844fc6a2c`（readiness 锋利边缘）、`d4ebc79ad`、`1d7ff7702`

**第二部分**

- `docs/experiments/2026-09-21-agent-context-evaluation.md`（860 行）：§1 结论与适用范围、§2 问题定义与设计推导、§3 表达范式、§4 冻结/数据/公平性（`:4.1` 480 案例、`4.2` 四组对照、`4.3` 开发选择与先导批修正、`4.4` 模型与重放 2330 份/4,332,701 token）、§5 指标与统计、§6 留出结果（四张表 + 逐族表 + 失败样例）、§7 多轮行为实验、§8 字段消融、§9 系统集成与边界、§10 可持续 eval 与门禁、§11 复现与回滚、§12 按环节选择表达（12.1–12.8）、§13 严格拆分（13.1 命题范围、13.2 因果路径与四因素、13.3 六个命题与边界、13.4 数据与真实调用 12,484 请求/31,716,142 token、13.5 标签与统计（含 4,615 族与 2/16=0.125）、13.6 主效应与交互、13.7 八环节矩阵、13.8 逐环节选择 + 追加实验 A/B + 最终配置、13.9 字段契约）
- `docs/experiments/2026-09-21-grounded-verification.md`：§1–§5（九组结果表、配对统计、混淆矩阵）、`:6`（复现命令）、`:8`（共同轨迹成功率 38.00 ± 1.83%）
- `scripts/evaluate_agent_context.py:1-58`、`evaluate_agent_stages.py:1-39`、`evaluate_agent_factorial.py:1-48`、`evaluate_agent_structure.py:1-17`、`evaluate_agent_contract.py:1-17`、`evaluate_agent_episodes.py:1-26`、`evaluate_agent_system.py:1-30`（"Does not contact models, simulators or robots."）、`evaluate_natural_language.py:1-151`、`evaluate_slam.py:1-225`
- `scripts/gate_agent_context.py:1-32`（全文：`compare_runs` + 两个默认阈值 + `sys.exit(0 if passed else 1)`；**未被任何 Makefile 目标调用**）
- `core/agentcontext/context.go:76-83`（`TANGYING_AGENT_CONTEXT` 唯一生产读取点与全部取值）、`:128-130`（`stage` 查表）、`:137`（`json` 常量分支）、`core/agentcontext/routing.go:60-73`（环节路由，未知环节回退 JSON）、`core/agentcontext/stage-policy.json`（42 行，含 `gate_passed.tool_result = false`）
- `orchestration/eval/context_eval/`：`gate.py`（`:23-45` 六项前置校验抛 ValueError、`:49-61` 六项检查）、`scoring.py:8-81`、`stage_runner.py:23`、`factorial_analysis.py:45`、`runner.py`、`dataset.py`、`archive.py`、`episodes.py`、`report.py:391`、`client.py`、`stage_cases.py`、`stage_report.py`、`stage_confirm.py`、`factorial_cases.py`、`factorial_runner.py`、`factorial_contract.py`、`factorial_formal.py`、`factorial_structure.py`、`factorial_confirm.py`、`factorial_release.py`、`factorial_report.py`；`orchestration/eval/system_eval/{engine.py:294,364, adapters.py, report.py}`；`orchestration/eval/cases.go:98`（`multiple-objects` 的 22 步记录）
- `scripts/report_agent_context.py:13-15`（默认输出即 860 行实验报告）；配套脚本 `ablate_*` / `report_*` / `confirm_*` / `release_*` / `audit_*` / `verify_*`
- `docs/architecture/agent-evaluation-system.md:283-291`（零事故上界 299/2995/45.1%）、`:327`（注入登记制度）、`:353`（`demo` 不能当发布门禁、三种维护者页面）
- `docs/architecture/why-distributed.md:124`（**"跨机学习（A 机解决的故障 B 机受益）"的架构宣称**）、`:183-185`（8787 vs 8897）
- `core/agentcontract/memory.go:8-13`（"Declaring them now is the whole of the forward-looking work for ExperienceAgent"）、`:88`（"this version ships only an in-memory implementation"）、`core/agentcontract/contract.go:58-66`（三项 reserved 能力，含 `experience.recording`）、`agentruntime/memory.go:95,112,283`（仅进程内 `MemoryBeads`）、`cmd/local-agent/agentruntime.go:20-27`（三个 Agent"later"才加）、`agentruntime/opsagent.go:48`
- `fleet/revisions.go:49,126`、`console/local_experience.go:121`、`console/revisions.go:56`（任务体验的四处调用点）
- `README.md:24,141`（"28 个工具" vs `tools.json` 实测 29）、`tools.json`（`tools` 29 项 + `excluded_from_llm` 2 项）
- `Makefile:4`（`GO_TEST_PACKAGES`，**不含 `train/` 与 `training/`**）、`:27`、`:264`（`CONTEXT_RUN=run-v2`）、`:284`（`STAGE_RUN`）、`:287`（`FACTORIAL_RUN=factorial-v1/run-v3`）、`:266-327`（评测目标全家）
- `train/gate.go:1-40`（包注释：为什么不是 Agent、结构性规则、LLM 能设计什么）、`:51-70`（`Report`/`Decision`）、`:72-88`（五个拒绝原因常量）、`:89-160`（`Compare`：`:108-120` run-incomplete、`:122-135` 无在役基线）
- `training/export.go:13-38`（只读打开账本）、`training/quantify.go`（312 行）、`training/refusals.go`（80 行，`HarvestRefusals`）
- `cmd/training-export/main.go:1-30`（三个问题与退出码语义）、`:31-50`（参数）
- `docs/architecture/post-training-pipeline.md:1-30`（不用 Agent 用什么 + 分界线）、`:32-70`（量化：唯一规则、三个桶、`0` 条正样本的两条原因）、`:60-80`（为什么拒绝样本不可能来自账本）、`:82-90`（按请求去重）、`:92-115`（真实账本结果）、`:117-170`（完整体系 + 两层覆盖 + 能与不能）、`:172-175`（一句话）
- `docs/architecture/train-agent-assessment.md:13-32`（三个 Agent 的约束方式）、`:34-56`（成立的部分）、`:58-100`（不成立的四条，含"被测量者和测量者是同一个系统"）、`:102-140`（`train/` 三条规则与已验证守卫）、`:142-176`（建议形状与可行性分级）、`:178-190`（结论）
- `training/quantify_test.go:1-179`
- `tasks/experience.go:9-133`（用户面向的体验结构）、`:184-226`（`ExperienceInput`）、`:195-226`（`SelectExperienceRevision`）、`:227-324`（`ProjectExperience`）、`:435-470`（`DefaultToolDisplay`）、`:471-502`（`safeArgumentProjection`/`secretLike`）、`:606-639`（`BasicRecoveryGuidance`）
- `tasks/experience_events.go:8-33`（`ToolActivitiesFromEvents`）、`:52-88`（`RecoveryGuidanceFromEvents`）、`:89-114`（`OverlayActivityStatuses`）
- `console/local_experience.go:1-40`（本地 runner 的工具映射与投影）
- `tasks/service.go:120-131`（`SetParser` 不重启替换解析器）
- `docs/superpowers/specs/2026-08-22-versioned-task-experience-design.md:1-50`（Context/Goals/Non-goals/方向）
- `docs/operations/fresh-deployment.md:306`（RoboCasa 4.2G 已清理）
- `datasets/`（5 个 0 字节标记文件，实测）
- Git 提交正文：`505af6577`（先有刻度）、`1e62b8d0b`（TrainAgent 评估 + train/ 门禁）、`21e780f48`（后训练流水线）

**第三部分**

- `install.sh:8-24`（usage）、`:34`（角色白名单）、`:35-38`（只允许一个角色）、`:41-44`（cloud 已移除）、`:45-62`（flags）、`:63-67`（未知参数）、`:72-76`（缺角色）、`:78-94`（导出环境变量与执行骨架）
- `scripts/install/common.sh:8-9`（Go 1.26.2 / XLeRobot 提交）、`:20-50`（`shell_quote`/`run`/`run_in_root`）、`:60-73`（`confirm_mutation`）、`:83-117`（`detect_platform`）、`:119-131`（`validate_role_platform`）、`:133-147`（版本解析与 `print_plan_header`）、`:149-177`（state/config/install 目录）、`:209-222`（配置文件保留分支）、`:224-245`（`write_receipt`）、`:247-279`（homebrew 与 Linux Go 安装）、`:290-306`（Python 选择与项目安装）、`:308-313`（构建两个二进制）、`:315-332`（仓库快照安装）、`:334-340`（CLI 安装）
- `scripts/install/sim.sh:1-32`、`scripts/install/local.sh:1-62`（含 `:6-22` launchd 渲染、`:23-34` systemd 只 enable）、`scripts/install/robot-pi.sh:3-25`（ROS 安装，**死代码**）、`:27-43`（用户与 XLeRobot clone）、`:45-70`（隔离 venv 与 pip 集成）、`:72-86`（服务安装与 `direct_edge()` 硬编码 `return 0`）、`:88-133`（主流程，`:104-115` 配置与目录、`:112-115` 跳过 ROS2 构建）
- `internal/robotagent/app.go:66-107`（子命令分发与 help）、`:122-135`（`receipt()`）、`:137-234`（生命周期转发，`:199-234` Linux、`:236-258` launchd）、`:260-286`（`pair` → `scripts/pair-robot.sh`）、`:294-339`（`configure` 白名单）、`:385-419`（**`doctor` 全部检查项**）、`:421-445`（`production-check`）
- `cmd/robot-agent/main.go:1-19`（全文）
- `cmd/local-agent/listen.go:19-76`（非环回绑定必须显式放行）、`cmd/local-agent/listen_test.go:18-28`
- `internal/localconfig/settings.go:18-58`（`Settings` 与恢复）、`:66-110`（`UpdateLLM`，`:86-88` openai 三件套、`:96-108` 只有回调成功才清 `restartRequired`）、`:136-171`（临时文件 + 0600 + rename）
- `cmd/local-agent/main.go:79`（默认监听）、`:489-516`（自然语言配置热生效回调）
- `internal/discovery/announcement.go:9-29`（明文的理由与跨语言 fixture）、`:41-71`（端口/组播/版本/topic/上限）、`:79-134`（包格式与三态）、`:191-203`（缺 `pairingState` 不猜）、`:245-247`、`:253-295`（广播目标，`:263-269` 跳过 loopback）
- `internal/discovery/listener.go:13-24`（保留 15 s / 间隔 5 s）、`:76-90`、`:109`、`:126-141`、`:179-216`、`:244-278`（老化与排序）、`:302-315`（启动失败非致命）
- `internal/pairing/protocol.go:11-24`（为什么用配对码）、`:68-83`（topic/端口/版本/帧上限）、`:87`、`:105-118`（请求载荷与 `Material`）、`:142-148`（应答载荷）、`:157-166`（规范化）、`:172-208`（HKDF 与 AES-GCM，AAD=robotId）、`:243-246`（随机量）、`:341-376`（应答封装即认证）、`:378-412`（长度帧）、`:414-433`（常量时间比较）、`:443-468`（密钥派生）
- `internal/pairing/authority.go:38-53`（CA 10 年、叶子 90 天、告警 7 天、私钥不出本机）、`:57`、`:73-98`（不静默替换、半份即报错）、`:102`、`:114`、`:176-195`（SAN）、`:202-212`、`:268`（签发即验）
- `internal/pairing/client.go:13-23`、`:61-64`（超时）、`:79`（TCP dial）
- `console/pairing.go:20-26`（"with no SSH anywhere"）、`:87-92`（请求体）、`:94-158`（handler 与错误码映射）、`:133`（45 s）、`:179-278`（`Pair` 五步）、`:214-222`（端口以公告为准）、`:319-325`、`:339-386`（只改五个键）
- `console/discovery.go:66-97`
- `console/server.go:180-186`（`/healthz` 与 `/v1/readiness`）、`:187`、`:189`、`:205`、`:207`、`:216-217`
- `docs/architecture/robot-discovery.md:56-64`（明文与不认证的理由）、`:70-81`（跨语言 fixture）、`:89-93`（广播目标，**只写了 Python 一侧**）
- `docs/architecture/robot-pairing.md:23-31`（配对码来源）、`:63-70`（配对契约 fixture）、`:84`（机器人接受后自己重启）
- `docs/operations/deployment.md:3-7`（定位与"两项独立放行结论"）、`:9-23`（三个运行位置）、`:25-61`（源码目录归属）、`:63-79`（云端进程）、`:81-99`（机器人端进程）、`:101-113`（本地单机进程）、`:115-125`（start-all）、`:127-136`（**端口一览**）、`:138-143`
- `docs/operations/fresh-deployment.md:9-34`（§0 预检）、`:38-53`（§1 路线与平台矩阵）、`:57-127`（§2 路线 A，`:84` 8897 的原因、`:123` 故障表）、`:131-246`（§3 路线 B，含发现与两条配对路径）、`:250-296`（§4 路线 C）、`:300-312`（§5 不覆盖什么，`:306` RoboCasa 4.2G）
- `docs/architecture/why-distributed.md:183-185`（8787 vs 8897 的解释）
- `docs/guides/quickstart.md:3-5`（定位与版本）、`:10-37`（固定工位）、`:46-71`（离桌导航）、`:90-111`（分终端调试）、`:129-147`（仿真/实机切换表与 mTLS 要求）
- `docs/guides/user-console.md:1-7`（定位与地址）、`:13-19`（任务流程与取消语义）、`:19`（暂停/未知结果阻断）、`:23-29`（FPS 语义）、`:35-44`（表达对照与不支持项）、`:75`（开发诊断与密钥）、`:77`（"开发模式是显示开关，不是服务端权限控制"）
- `docs/install/local.md`、`robot-pi.md`（含 `:103` 证书 7 天）、`robot-pi-quick.md`、`troubleshooting.md`（含 `:47-55` 错误码表）、`cloud.md`（历史归档）、`alicloud-cloud.md`（含 `:44` 无细粒度角色/无 HA）、`xlerobot-setup.md`、`xlerobot-experiment.md:7-13`
- `deploy/local/local.env.example:1-11`、`deploy/local/tangying-robot-local-agent.service:2-17`、`deploy/local/com.tangying.robot-agent.plist:5-15`
- `deploy/robot/raspberry-pi/robot-pi.env.example:1-15`、`tangying-robot-edge-direct.service:2-27`、`tangying-robot-edge.service:3-18`、`tangying-xlerobot.service:2-17`、`99-tangying-xlerobot.rules`
- `deploy/cloud/docker-compose.yml:2-17`（mysql）、`:19-29`（redis）、`:31-85`（control-plane，`:31-32` 不 publish）、`:55`（`FLEET_DEVICE_CREDENTIALS` 必填）、`:89-104`（nginx 端口）、`:106-109`（卷）、`deploy/cloud/nginx.conf:1-16`、`:21-25`、`:32-35`、`:37-46`、`:69-78`、`deploy/cloud/.env.example:21,23`
- `deploy/robot/navigation/compose.yaml:11,13,31-33`、`gazebo-house.compose.yaml:11,18`
- `scripts/fleet-up.sh:28-36`（环境变量）、`:44-94`（生成 .env 与 legacy 迁移）、`:132-148`（allowed.conf）、`:155-164`（up 的真实顺序）、`:165-176`（120 s 健康检查）、`:179-192`（summary）、`:194-205`、`:207-212`
- `scripts/start-all.sh:1-18`（定位）、`:24-59`（状态文件与组件）、`:76-78`（默认只起 sim 的理由）、`:90-129`（`component_running` 与三态）、`:161-186`、`:202-205`、`:207-224`（**启动顺序**）、`:226`、`:246-252`（停止逆序）、`:257-268`、`:286-289`、`:320`
- `scripts/pair-robot.sh:18`、`:23-40`、`:42-46`、`:69`、`:116-117`、`:146-149`、`:163-169`、`:186-197`、`:242-243`、`:277`、`:305`
- `scripts/robot-pi-preflight.sh:4-14`（输出函数）、`:21-55`（20 项检查）
- `scripts/robot-pi-quick-deploy.sh:14-44`
- `scripts/xlerobot_production_check.py:23-29`（LIMITATIONS）、`:75-77`（`completed_trials >= 30`）、`:103-105`（READY/NOT_READY 输出）
- `docs/operations/production-readiness.md:1-14`、`:16`、`:18-36`、`:38-42`、`:44-51`、`:53`
- `docs/operations/safety-checklist.md:1-22`（14 条 checkbox 全未勾选）
- `docs/operations/release-checklist.md:1-34`（19 条 checkbox 全未勾选）
- `docs/operations/agent-runtime-config.md:5-9`（`TANGYING_AGENTS` 不热生效）
- `docs/production/api-reference.md:86`（`POST /v1/tasks/{id}/reconcile`）
- `docs/development/2026-09-13-furnished-home-acceptance.md:8`、`docs/development/2026-09-13-robot-workflow-acceptance.md:21,41`
- `VERSION:1`、`pyproject.toml:6,8`、`go.mod:3`、`.github/workflows/ci.yml:36-39`
- `tests/install/test_precheck.py:42`、`:48`、`:64`、`:78`（只读守卫）、`:88-101`（12 条变更命令正则）、`:110-114`、`:117`、`:123`、`:142`、`:169`
- `tests/contract/test_proto_schema.py:28`（七 RPC 集合）、`proto/robot/v1/robot.proto:10-18`
- `tests/contract/robot_announcement.json`、`tests/contract/robot_pairing.json`

**第三部分（故障注入与冷启动预检）**

- `scripts/precheck.sh:2-24`（为什么存在与两条规则）、`:28-44`（计数器与三态输出）、`:46-55`（usage）、`:59-82`（`detect_platform`）、`:87-98`（`report_platform`）、`:104-143`（版本比较器及历史缺陷）、`:145-199`（五个探针）、`:203-227`（角色平台表与"为何不 source common.sh"）、`:230-257`（`check_sim`）、`:259-273`（`check_local`）、`:275-302`（`check_robot_pi`）、`:304-330`（`check_cloud`）、`:335-366`（退出码与 Summary）
- `scripts/inject_faults.py:2-24`（为什么存在、三条限制的骨架、退出码 docstring）、`:37-42`（依赖与 25 s 检测窗口）、`:45-64`（`Scenario`，`:64` `expect_manual`）、`:67-93`（三个场景）、`:96-115`（`estop` 与 `clear_estop`）、`:118-149`（轮询与匹配）、`:160-185`（注入判据与 N/A 分支）、`:187-203`（判定与危险/缺建议分档）、`:208-219`（argparse）、`:225`、`:237-253`（三档输出）、`:257-258`（清理调用）、`:266-271`（退出码聚合）
- `sim/mujoco/tangying_sim/rgbd_runtime.py:663-706`（`_refresh_faults` 三个码）、`:685`、`:1212`（急停进 anomalies）、`:1500-1508`（派发路径硬拒绝）
- `docs/architecture/supervision-verification.md:171-180`（注入器定位、命令、退出码）、`:184-193`（实测输出）、`:195-199`（三条限制）
- `docs/architecture/review-agent.md:122-126`（不代按急停的实测证据）、`:142`
- `docs/architecture/agent-evaluation-system.md:327`（注入登记制度，未实现）
- `docs/development/2026-09-15-robot-fault-handling-audit.md:92`（注入器出现前的缺口）
- `docs/development/2026-09-17-supervision-blind-spots.md:189`（83 → 113 与补进的 16 个码）、`:275`（只读守卫被动验证）
- `docs/operations/fresh-deployment.md:11-22`（预检运行方式）、`:32`（退出码）、`:34`（动机）、`:192-194`（配对窗口日志）
- `docs/install/robot-pi.md:103`（证书 7 天）
- `tests/install/test_precheck.py:42-178`（8 个测试函数，见 §18.2）
- `tests/e2e/test_policy_faults.py:96`、`tests/e2e/test_versioned_task_faults.py:26,41`、`tests/e2e/test_fleet_faults.py:155,175`、`tests/e2e/test_robocasa_faults.py:73,102,126`、`tests/contract/test_fault_contract.py:51,76,100,121,145,183,189`
- `robot/gateway/tangying_robot_gateway/module_faults.py:29`、`:44`、`:47`、`:56`、`:59`、`:64`、`:69`、`:132-136`、`:171-190`
- `robot/gateway/tangying_robot_gateway/beacon.py:46`、`:247`、`:261`、`pairing.py:79,85`
- `agentruntime/faultmatrix_test.go:99,204,285`、`agentruntime/opsagent_test.go:198,236`、`core/robotcontract/faults_test.go:34,56,66,81,145`、`edge/robotclient/faults_test.go:35,48,60`、`edge/worker/faults_test.go:37,71,82`、`tasks/alert_groups_test.go:27,170`、`tasks/alerts_test.go:32,52,72,115,130,154,164,208,338,400,424,443`、`internal/robotagent/app_test.go:198`
- Git 提交正文：`1d7ff7702`（冷启动预检 + 故障注入器，含"会误判的预检比没有预检更糟"与"不伪造故障"）

