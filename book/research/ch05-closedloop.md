# 第 5 章素材：闭环契约、证据、失败分类、生命周期对象、结果未知对账

> 仓库：`tangying-robot-agent-os`，Go 1.26 + Python 3.11，v0.6.0
> 采集日期：源码与文档均取自本工作区当前 HEAD（工作树有未提交改动，见 §9 冲突清单）
> 本文所有标识符、行号均来自源码实读。凡推断处标注「(推断)」；文档与代码冲突处在 §9 集中列出。

---

## 0. 一句话结论

这套系统把「工具返回成功」和「物理动作完成」拆成两个不同层级的命题，并为后者设计了一个**唯一的、不可绕过的判定入口** `closedloop.Gate`，以及一个**唯一的、以保守为默认的失败语义映射** `closedloop.Classify`。两者的共同产物是一条硬规则：**物理结果未知时，任何后续动作——包括换一个工具——都不得执行，直到有人或某条只读证据把世界状态重新确定下来。**

这条规则在代码里有 5 个独立落点（§4），并且是唯一在系统里带「禁令」语义的产出（`Finding.AutomaticRetryForbidden`）。

---

## 1. 精确技术事实（标识符清单）

### 1.1 契约层：`core/closedloop`

| 标识符 | 类型 | 位置 | 语义 |
|---|---|---|---|
| `Class` | `type Class string` | `core/closedloop/closedloop.go:46` | 失败分类枚举 |
| `Transient` `Perception` `Planning` `Permission` `Resource` `Validation` `UnknownOutcome` `Fatal` | `Class` 常量 | `closedloop.go:50,52,55,57,59,62,65,67` | 共 8 类；字符串值分别是 `"TRANSIENT"` `"PERCEPTION"` `"PLANNING"` `"PERMISSION"` `"RESOURCE"` `"VALIDATION"` `"UNKNOWN_OUTCOME"` `"FATAL"` |
| `classification` | 有序查表 `[]struct{class Class; codes map[string]struct{}}` | `closedloop.go:83-274` | **顺序敏感，先匹配先赢**；共 **177 个唯一码**（含 2 个重复项，见 §9-4） |
| `Classify(code string) Class` | 函数 | `closedloop.go:289-300` | 空码或未识别码 → `UnknownOutcome` |
| `Knows(code string) bool` | 函数 | `closedloop.go:311-322` | 区分「表内列为 UnknownOutcome」与「表里根本没有」 |
| `(Class).Retryable() bool` | 方法 | `closedloop.go:333-340` | **仅** `Transient`、`Perception` 返回 true |
| `DispatchPrecision = time.Millisecond` | 常量 | `closedloop.go:349` | 时序比较粒度 |
| `NormalizeDispatchTime(at time.Time) time.Time` | 函数 | `closedloop.go:354-359` | UTC + 截断到毫秒 |
| `Evidence` | struct | `closedloop.go:362-373` | 字段：`ObservationID` `ObservedAt` `Freshness` `SourceID` `Confidence` |
| `ErrEvidenceRequired` | error var | `closedloop.go:74` | `"physical write requires fresh post-command evidence"` |
| `ErrEvidenceStale` | error var | `closedloop.go:75` | `"physical write evidence predates the dispatched command"` |

`Evidence.ObservedAt` 的注释明确写的是**传感器采集时间而非接收时间**（`closedloop.go:365`）；`Freshness` 是**投影（projection）对声明来源给出的裁决**（`closedloop.go:367`），不是证据自带的属性——这一点是 GVF 之外整条链的关键设计（§3 第 4 条）。

### 1.2 门禁层：`core/closedloop/gate.go`

| 标识符 | 位置 | 语义 |
|---|---|---|
| `Declaration{Manifest, RuntimeMutatesWorld, RuntimeCapabilityKnown bool}` | `gate.go:20-24` | 两个**独立**的「是否改世界」声明来源 |
| `(Declaration).Mutates() bool` | `gate.go:27-29` | `Manifest \|\| RuntimeMutatesWorld`；`RuntimeCapabilityKnown` **不参与**判定（`gate_test.go:119-131` 钉死） |
| `ReasonNotRequired = "CLOSURE_NOT_REQUIRED"` | `gate.go:34` | 只读工具 |
| `ReasonRequired = "CLOSURE_EVIDENCE_SATISFIED"` | `gate.go:35` | 通过 |
| `ReasonMissing = "CLOSURE_EVIDENCE_REQUIRED"` | `gate.go:36` | 无证据 |
| `ReasonStale = "CLOSURE_EVIDENCE_STALE"` | `gate.go:37` | 证据不可用 |
| `ReasonNoCommandTime = "CLOSURE_DISPATCH_TIME_REQUIRED"` | `gate.go:38` | 无下发时刻 |
| `Decision{Satisfied, Required bool; Reason, Message string}` | `gate.go:42-51` | 门禁结论 |
| `Gate(declaration, dispatchedAt, evidence) Decision` | `gate.go:60-79` | **唯一入口** |
| `validateFreshness(dispatchedAt, evidence) error` | `gate.go:85-105` | 四条校验 |
| `equalFold(value, want)` | `gate.go:108-126` | 大小写无关比较（避免仅为两次比较引入 `strings`） |
| `ErrNotSatisfied` | `gate.go:129` | `"closed-loop post-condition not satisfied"` |
| `(Decision).Require() error` | `gate.go:132-137` | 不满足则包装 `ErrNotSatisfied` |

### 1.3 GCL/GVF 报告：`core/closedloop/grounded_report.go`

| 标识符 | 位置 | 说明 |
|---|---|---|
| `GroundedReport` | `grounded_report.go:13-34` | 字段：`ReportID` `SchemaVersion` `ActionID` `ActionName` `TaskID` `Verdict` `FailureType` `Confidence` `EvidenceCompleteness` `EdgeBootID` `MonotonicNS` `VerifierVersion` `ToolReturnStatus` `LogicalClock` `VerifiedFacts[]` `EvidenceRefs[]{URI,SHA256,Kind}` |
| `evidenceURI` | `grounded_report.go:36` | 正则 `^evidence://[a-zA-Z0-9_.-]+/([a-f0-9]{64})$` |
| `ParseGroundedReport(raw, actionID, actionName, taskID)` | `grounded_report.go:38-64` | 大小上限 1 MiB；`SchemaVersion` 必须为 `"state-report.v1"`；三值 verdict `VERIFIED`/`FALSIFIED`/`UNKNOWN`；`EvidenceRefs` 必须是**内容寻址**（URI 尾段必须等于 `SHA256`）；`VERIFIED` 要求 `Confidence != 0 && EvidenceCompleteness == 1 && len(EvidenceRefs) > 0 && len(VerifiedFacts) > 0 && FailureType == "NONE" && ToolReturnStatus != "NOT_DISPATCHED"`（`grounded_report.go:60-63`） |

### 1.4 观测层：`core/observation`、`core/telemetry`

| 标识符 | 位置 | 说明 |
|---|---|---|
| `Envelope` | `core/observation/envelope.go:43-62` | 含 `ObservationID` `SourceSequence` `ObservedAt` `ReceivedAt` `Confidence` `Causation{TaskID,CommandID}` `Provenance{Adapter,Version,Sensor}` |
| `(Envelope).Validate()` | `envelope.go:122-177` | `SchemaVersion` 必须 `"world.observation.v1"`；`ObservedAt`/`ReceivedAt` 非零；`ReceivedAt.Before(ObservedAt)` → `ErrInvalidEnvelope`（`envelope.go:130-132`）；`Confidence` 必须在 [0,1] 且非 NaN/Inf |
| `ClockSkewAllowance = 5 * time.Second` | `core/telemetry/freshness.go:18` | 允许机器人时钟超前代理的额度 |
| `DefaultMaxAge = 60 * time.Second` | `freshness.go:24` | `MaxAgeMS` 的契约上限 |
| `(Snapshot).sourceBudget(sourceID) time.Duration` | `freshness.go:39-62` | 三档：具名且 profile 认识 → 该传感器自身预算；来源未知/不认识 → profile 中**最严格**的预算；无 profile → `DefaultMaxAge` |
| `(Snapshot).EvidenceFreshness(now) worldmodel.Freshness` | `freshness.go:81-106` | 返回 `Fresh`/`Stale`/`Unknown`。时间戳为零 → `Unknown`；`now+ClockSkewAllowance < observedAt`（时间戳过度超前）→ `Unknown`；`now-observedAt > budget` → `Stale` |

### 1.5 Harness 层：`core/harness/evaluator.go`

这一层是**同一思想的第二个实现**，用 `basis` 而不是 `dispatch time` 表达「命令开始时刻」。

| 标识符 | 位置 | 说明 |
|---|---|---|
| `Status` | `evaluator.go:13-20` | `WAITING` / `SATISFIED` / `RETRYABLE_FAILURE` / `FAILED_SAFE` |
| `EvidenceBasis` | `evaluator.go:27-35` | `WorldRevision` `EntityObservationCount` `EntitySourceID` `EntitySourceSequence` `RobotSourceID` `RobotSourceSequence` `CommandStartedAt` |
| `(Agent).Evaluate(input) Verdict` | `evaluator.go:71-164` | 依次返回 `HARNESS_INPUT_INVALID`(74) `WORLD_REVISION_NOT_ADVANCED`(76) `ENTITY_UNKNOWN`(81) `ENTITY_EVIDENCE_NOT_POST_COMMAND_STABLE`(84) `ENTITY_EVIDENCE_PREDATES_CLAIM`(87) `ENTITY_EVIDENCE_PREDATES_COMMAND`(91) `ROBOT_UNKNOWN`(96) `ROBOT_SOURCE_CHANGED`(99) `ROBOT_EVIDENCE_PREDATES_COMMAND`(102) `ROBOT_EVIDENCE_PREDATES_CLAIM`(105) `ROBOT_EMERGENCY_STOPPED`(108) `ENTITY_EVIDENCE_STALE`(116) `ROBOT_EVIDENCE_STALE`(119) `ENTITY_SOURCE_STALE`(122) `ROBOT_SOURCE_STALE`(125) `RELATION_MISMATCH`(128) `ENTITY_NOT_STABLE`(135) `ROBOT_STILL_HOLDING_ENTITY`(138) 以及资源三态 `RESOURCE_UNKNOWN`(144) `RESOURCE_OWNER_MISMATCH`(147) `FENCING_TOKEN_MISMATCH`(150) `RESOURCE_LEASE_EXPIRED`(157)，最终 `PHYSICAL_POSTCONDITIONS_SATISFIED`(162) |
| `stale(now, observedAt, maxAge)` | `evaluator.go:166-168` | 任一为零即 stale |
| `New(maxAge)` 默认 | `evaluator.go:64-69` | `maxAge <= 0` → 5 秒 |

注意 `Evaluate` 的分工：`Waiting` 是「还不能下结论」（可以再等一次观测），`RetryableFailure` 是「关系不成立、可以重试」，`FailedSafe` 是「输入或资源状态根本不对」。

### 1.6 生命周期对象状态名

**Task（18 态）** `core/taskgraph/state.go:5-24`：
`READY` `PAUSED` `OBSERVING` `PLANNING` `WAITING_APPROVAL` `EXECUTING` `WAITING_FOR_OBSERVATION` `RECOVERING` `BLOCKED` `FAILED_SAFE` `VERIFYING` `SUCCEEDED` `RECOVERABLE_FAILURE` `SAFE_RECOVERY` `WAITING_USER` `SAFETY_STOPPED` `CANCELLED` `FAILED`
合法转换表在 `state.go:26-40`，唯一入口 `CanTransition(from, to)`（`state.go:42-44`）。

> 注意 `StateSucceeded` 的入边只有 `StateVerifying`（`state.go:35`），而 `StateVerifying` 的入边只有 `StateExecuting`（`state.go:32`）。

**Step（4 态）** `middleware/contracts.go:60-69`：
`StepPending="PENDING"` `StepStarted="STARTED"` `StepCompleted="COMPLETED"` `StepFailed="FAILED"`。
`StepFailed` 的定义是「运行时在**任何物理效果被授权之前**拒绝了一个命令」，因此仍然可重试，与 `STARTED` 明确区分。

**人对未知结果的结论（3 值）** `middleware/contracts.go:82-104`：
`StepOutcomeHappened="HAPPENED"` `StepOutcomeNeverActed="NEVER_ACTED"` `StepOutcomeAbandoned="ABANDONED"`，`(StepOutcome).Valid()` 是唯一合法集合。
承载结构 `StepReconciliation{Outcome, Actor, Note, RecordedAt}`（`contracts.go:118-123`），持久化后备是 `StepRun.Reconciled *StepReconciliation`（`contracts.go:127-134`）。

**Recovery 动作风险级（3 级）** `agentruntime/recoverycatalog.go:31-40`：
`RiskReadOnly="read_only"` `RiskBoundedWrite="bounded_write"` `RiskNeverAutomatic="never_automatic"`。
`(RecoveryAction).RequiresApproval()` = `Risk != RiskReadOnly`（`recoverycatalog.go:341-343`）；`Executable()` = `Risk == RiskReadOnly || Risk == RiskBoundedWrite`（`recoverycatalog.go:356-358`）——用白名单而非黑名单，理由写在注释里：「一个没声明风险级的条目会默认可执行」。

**Supervision 异常码** `agentruntime/opsrules.go:12-47`：
`ANOMALY_COMPONENT_FAULT` `ANOMALY_SAFETY_STOP` `ANOMALY_ACTION_FAILED` `ANOMALY_UNVERIFIED_MUTATION` `ANOMALY_STEP_LATENCY` `ANOMALY_TELEMETRY_STALE` `ANOMALY_REPEATED_FAILURE` `ANOMALY_ABNORMAL_TASK`；严重度 `info`/`warning`/`critical`（`opsrules.go:52-56`）。

---

## 2. Gate 证据校验的判定逻辑（伪代码，带行号）

### 2.1 `Gate` 主流程 — `core/closedloop/gate.go:60-79`

```
function Gate(declaration, dispatchedAt, evidence):
    # gate.go:61-64
    if not (declaration.Manifest or declaration.RuntimeMutatesWorld):
        return Decision{Satisfied: true, Required: false,
                        Reason: "CLOSURE_NOT_REQUIRED",
                        Message: "read-only tool needs no post-condition evidence"}

    # gate.go:65-68
    if dispatchedAt.IsZero():
        return Decision{Satisfied: false, Required: true,
                        Reason: "CLOSURE_DISPATCH_TIME_REQUIRED",
                        Message: "cannot prove freshness without the dispatch time of this command"}

    # gate.go:69-73
    if evidence == nil:
        return Decision{Satisfied: false, Required: true,
                        Reason: "CLOSURE_EVIDENCE_REQUIRED",
                        Message: "tool reported success but no post-command observation was attached; "
                                 + "the physical outcome is unverified"}

    # gate.go:74-76
    err := validateFreshness(dispatchedAt, *evidence)
    if err != nil:
        return Decision{Satisfied: false, Required: true,
                        Reason: "CLOSURE_EVIDENCE_STALE", Message: err.Error()}

    # gate.go:77-78
    return Decision{Satisfied: true, Required: true,
                    Reason: "CLOSURE_EVIDENCE_SATISFIED",
                    Message: "confirmed by observation {evidence.ObservationID} taken after dispatch"}
```

判定顺序是**不可交换**的：先问「要不要证据」，再问「知不知道命令何时下发」，然后才问「证据有没有」。这个顺序保证了一条关键的诚实性——一个连下发时刻都不知道的命令，**不会**因为恰好带了一份看起来很新的观测而被判定为完成。

### 2.2 四条证据校验 — `core/closedloop/gate.go:85-105`

```
function validateFreshness(dispatchedAt, evidence) -> error | nil:

    # (1) 缺观测 ID —— gate.go:86-88
    if evidence.ObservationID == "":
        return ErrEvidenceRequired wrapped with "no observation id"

    # (2) 缺采集时间 —— gate.go:89-91
    if evidence.ObservedAt.IsZero():
        return ErrEvidenceRequired wrapped with "no observation time"

    # (3) 早于命令下发 —— gate.go:92-97
    dispatchedMS := NormalizeDispatchTime(dispatchedAt)          # UTC + Truncate(1ms)
    observedMS   := evidence.ObservedAt.UTC().Truncate(1ms)
    if observedMS.Before(dispatchedMS):
        return ErrEvidenceStale wrapped with
               "observed {observedMS.RFC3339Nano}, dispatched {dispatchedMS.RFC3339Nano}"

    # (4) 来源裁决过期 —— gate.go:98-103
    switch:
        case equalFold(evidence.Freshness, "STALE"):   return ErrEvidenceStale "source verdict STALE"
        case equalFold(evidence.Freshness, "UNKNOWN"): return ErrEvidenceStale "source verdict UNKNOWN"

    # gate.go:104
    return nil
```

四个判定要点，教学上必须讲清：

1. **缺 ID 与缺时间都报 `ErrEvidenceRequired`，不是 `ErrEvidenceStale`。** 前者是「你没有证据」，后者是「你的证据不是这个命令的」。这个区分在 Reason 码层面被保留（`gate.go:75` vs `gate.go:70`），因为 Message 里两者会拼接不同的原因串。

2. **毫秒截断不是宽容，而是承认分辨率极限。** `DispatchPrecision` 的注释（`closedloop.go:342-348`）写得非常直白：runtime 以 Unix 毫秒上报采集时间，所以「下发后紧接着的观测」在亚毫秒层级**原理上不可排序**，这不是给出的容差。等价实现是「同一毫秒内或更晚的观测可以确认，任何更早的毫秒被拒绝」。`gate_test.go:92-106` 把这个边界钉死：同毫秒 +100µs 通过，前一毫秒 -1ms 拒绝。

3. **`Freshness` 只有 `STALE` 和 `UNKNOWN` 被拒，`FRESH` 与其它任意串被放行。** 看代码：switch 只列了两个 case，default 落入 `return nil`。所以一个空字符串 `Freshness` 会被 Gate 接受。
   **这是一个真实的宽松点（(推断) 是否刻意）：** 生产者侧 `evidenceFromSnapshot` 总是写一个值（`edge/agent/runner.go:765`），但 Gate 本身不强制。`gate_test.go:69-86` 的用例集合里没有「空 Freshness」这一条，说明它是未被考虑的分支，而不是被刻意允许的分支。

4. **`Freshness` 是被「计算」出来的，不是被写死的。** 历史实现里两个调用点都写字符串字面量 `"FRESH"`，注释（`core/telemetry/freshness.go:66-73`）明确记录了后果：「gate 的 staleness 规则根本不可能因过期而触发」。现在两个生产者都调用 `snapshot.EvidenceFreshness(now)`（`edge/agent/runner.go:765`、`internal/recoveryexec/tools.go:308`），并在急停/锁存时强制降级为 `"UNKNOWN"`（`runner.go:767-775`、`tools.go:311-315`）。

### 2.3 与 harness 层 `basis` 校验的对照

`core/harness/evaluator.go:71-164` 是同一问题的另一份精确实现，用 `basis`（基准）表达「命令前世界的状态」。它把「过期」拆成**六个**独立原因，比 Gate 更细：

| 校验 | 行号 | Reason |
|---|---|---|
| 世界版本未推进 | 75-77 | `WORLD_REVISION_NOT_ADVANCED` |
| 实体观测计数未增加 2 次 | 83-85 | `ENTITY_EVIDENCE_NOT_POST_COMMAND_STABLE` |
| 实体证据早于 `CommandStartedAt`（墙钟） | 86-88 | `ENTITY_EVIDENCE_PREDATES_CLAIM` |
| 同一来源、序号未前进 | 89-92 | `ENTITY_EVIDENCE_PREDATES_COMMAND` |
| 机器人来源 ID 变了 | 98-100 | `ROBOT_SOURCE_CHANGED`（**FailedSafe**，不是 Waiting） |
| 投影时刻 vs 证据时刻超龄 | 115-126 | `ENTITY_EVIDENCE_STALE` / `ROBOT_EVIDENCE_STALE` / `ENTITY_SOURCE_STALE` / `ROBOT_SOURCE_STALE` |

教学价值：Gate 用**时间戳**判断新鲜度（适合单命令单回执），harness 用**版本 + 计数 + 序号 + 时间戳四重**判断（适合持续投影的世界模型）。两者都拒绝「早于命令」，但只有后者能识别「同一来源没有前进」这种攻击/故障形态。

---

## 3. 八类失败分类：定义、触发条件、唯一安全的下一步

分类表在 `core/closedloop/closedloop.go:83-274`。**表的顺序即优先级**：`UnknownOutcome` → `Permission` → `Resource` → `Perception` → `Planning` → `Validation` → `Transient` → `Fatal`。`Classify` 线性扫描，第一个命中的规则获胜（`closedloop.go:294-298`）。

「唯一安全的下一步」在**两处**被强制：
- **只读裁决**：`(Class).Retryable()`（`closedloop.go:333-340`）——只有 `Transient`、`Perception` 返回 true。
- **面向人的建议**：`agentruntime/opsrules.go:391-418` 的 `switch class`，每一类输出一句**固定**的建议文案；`UnknownOutcome` 分支同时把 `severity` 提升到 `critical` 并把 `AutomaticRetryForbidden` 置 true（`opsrules.go:395-397`）。

| # | 类 | 精确定义（源码注释） | 触发条件 | 唯一安全的下一步 | 码数 |
|---|---|---|---|---|---|
| 1 | `TRANSIENT` | 「基础设施恢复后可就地重试」（`closedloop.go:49-50`） | 依赖服务/连接/租约暂时不可用 | **依赖恢复后重发同一条命令**（这是唯一允许「重发原命令」的类） | 32 |
| 2 | `PERCEPTION` | 「再次行动前需要新的观测或搜索」（`52`） | 世界与计划假设不符，或机器人无法确定物体位置 | **重新观测/搜索，然后重试** | 47 |
| 3 | `PLANNING` | 「需要新目标或新计划；重复同一条命令没有意义」（`54-55`） | 目标在已勘测世界之外、路径被限定、几何上无法服从 | **换目标或换路径**，重放原目标必然复现失败 | 17 |
| 4 | `PERMISSION` | 「审批、profile、目录或 fencing 状态不对」（`57`） | 缺少审批、安全 profile 不符、标定/校准缺失、急停锁存 | **人或更高层改变某个东西**（批准、校准、安装），无自动路径 | 29 |
| 5 | `RESOURCE` | 「另一个持有者占着资源，或缺少 grant」（`59`） | 租约/fencing token 缺失或过期、机器人被占用 | **等待/获取资源所有权** | 9 |
| 6 | `VALIDATION` | 「命令本身被判为畸形。用相同参数重试不可能成功」（`61-62`） | 参数非法、schema 版本不支持、前置检查在动作前就拒绝 | **改请求**，不是重试 | 27 |
| 7 | `UNKNOWN_OUTCOME` | 「已下发动作的物理结果无法判定。**禁止自动重试**」（`64-65`） | 见下 §3.1 | **先对账**（只读取证），对账前不得改动机器人 | 15 |
| 8 | `FATAL` | 「无法通过重试同一份工作解决」（`67`） | `CANCELLED` `VERIFICATION_FAILED` `VERIFICATION_CONFIDENCE_LOW` | **人工判断** | 3 |

> 计数为源码实读（177 唯一码）。文档多处写「**七类**失败分类」（`docs/architecture/supervision-verification.md:24` 的标题下却列了 **8** 行；同文件 `:298` 写「七类 + `UNKNOWN_OUTCOME` 兜底」；`docs/development/2026-09-17-supervision-blind-spots.md:167`、`docs/architecture/lifecycle-objects.md:305,611` 同样用「七类」）。**代码是 8 个平级常量，`UnknownOutcome` 与其余 7 个是同一层级的 `Class` 值。**（§9-1）

### 3.1 `UNKNOWN_OUTCOME` 的完整触发面（15 个码）

这是全书最值得逐条讲解的一段。源码注释（`closedloop.go:87-129`）本身就是设计文档：

```
EVIDENCE_INSUFFICIENT              证据不足
EXECUTION_OUTCOME_UNKNOWN          执行结果未知（传输中断族）
PHYSICAL_OUTCOME_UNKNOWN           物理结果未知
RUNTIME_JOURNAL_UNAVAILABLE        运行时 journal 不可读
BACKEND_STOP_FAILED                后端停止失败
SERVICE_SHUTDOWN                   服务关闭
PLACEMENT_NOT_OBSERVED             放置动作执行了，但没观测到预期关系
GRASP_NOT_OBSERVED                 抓取动作执行了，但没观测到
GRASP_NOT_DETECTED                 未检测到抓取
PLACEMENT_NOT_VERIFIED             放置未验证
UNVERIFIED_WORLD_MUTATION          未验证的世界变更
TOOL_EXECUTION_ERROR               工具抛异常，且没给出错误码
NAV_STOW_CONTACT                   收臂扫掠过程中真实碰撞
UNCLASSIFIED_EXECUTION_FAILURE     恢复分类器自己的兜底
WORKFLOW_FAULT                     诊断层「工作流失败但原因未确定」
```

四条注释里的取舍值得原文引用：

- **`*_NOT_OBSERVED` 为什么不是 `Perception`**（`closedloop.go:91-100`）：「夹爪闭上了、放置动作完成了，而它实际达成了什么并未被确立。当作感知失败会允许重试一个第一次可能已经成功的物理动作——对放置来说，物体可能已经在目的地了。」
- **`TOOL_EXECUTION_ERROR` 为什么不是 `Validation`**（`closedloop.go:103-112`）：「工具抛了意外异常。参数一无所知，异常前是否已经触达硬件也一无所知……它曾经被归到 `Validation`，后者的建议是『参数被拒绝了，重放也没用』——这是对一个**没有任何人检查过**的命令做出的猜测。」
- **`NAV_STOW_CONTACT` 为什么从同一个码拆出来**（`closedloop.go:113-120`）：「一个码覆盖『我们仿真了，它会撞』和『它撞了』，让分类器有两个物理含义要选。拆分就是修复。」
- **兜底为什么故意落在这里**（`closedloop.go:122-128`）：`UNCLASSIFIED_EXECUTION_FAILURE` 与 `WORKFLOW_FAULT` 是「分类不了」本身，而「分类不了的失败就是未知结果的**定义**」。

### 3.2 三个真实 fault code 的例子（含义 + 分类归属的推理）

**例 1：`GRASP_NOT_REACHED` → `PERCEPTION`（`closedloop.go:169-176`）**
运行时自己的码，含义是「末端执行器在容差内够不到物体」。它曾经**不在表里**，于是兜底成 `UnknownOutcome`——把一个已知、可解释的失败报成「不可恢复、禁止重试」。注释原文：「让分类器把一个已知失败报成不可恢复，并禁止了那个**实际会成功**的重试。」
守卫：`core/closedloop/classification_coverage_test.go` 的 `TestEveryRuntimeFailureCodeIsClassified`（该文件第 288-303 行），配套清单 `runtimeEmittedCodes`（第 127-280 行）。

**例 2：`GRASP_FAILED` 与 `PLACE_NOT_REACHED` → `PERCEPTION`（`closedloop.go:198-206`）**
两者的共同点是**物理效果可证伪**：`GRASP_FAILED` 在夹爪闭合、手臂抬起、抓取检查失败之后返回——手是空的（已知），物体可能被碰歪了（所以要重观测）；`PLACE_NOT_REACHED` 在夹爪张开**之前**返回——什么都没释放，物体还握着。对比它们的兄弟码 `PLACEMENT_NOT_OBSERVED`：动作跑了、没检查 → `UnknownOutcome`。

**例 3：`NAV_STOW_CONTACT_PREDICTED` → `PLANNING`（`closedloop.go:225-228`）vs `NAV_STOW_CONTACT` → `UNKNOWN_OUTCOME`（`closedloop.go:121`）**
前者是动之前仿真整个扫掠预测出碰撞：什么都没坏，命令也没畸形，只是路径被挡，所以答案是**换个计划**。后者是真实碰撞：手臂正在动、接触把它停在中途，之后它的姿态和碰到的东西都没被确立——重试一个刚撞过的扫掠会把手臂往障碍物里推得更深。

### 3.3 分类表的两道守卫

1. **`Knows(code)` 存在的原因**（`closedloop.go:302-310`）：`Classify` 无法区分「表里列为 `UnknownOutcome`」和「表里没有」。前者是系统理解并决定不可恢复的失败，后者是没人分类过的失败。把后者报成前者，「就是把一个缺失的表项藏在看起来像安全决策的结果后面——`GRASP_NOT_REACHED` 正是如此」。
2. **`TestEveryCodeTheRuntimeCanEmitIsClassified`**（`classification_coverage_test.go:288-303`），清单 `runtimeEmittedCodes` 是**提交进仓库的常量**而不是运行时爬取的（`classification_coverage_test.go:118-126`）：新增码而没加进清单 → 静默落入未分类 → 守卫失败。
   审计数字（`classification_coverage_test.go:122-125`）：**83 个码里有 58 个缺失**，其中包括 `GOAL_NOT_CLEAR`——一次真实导航失败把它报成「（UNKNOWN_OUTCOME）不要自动重试：先对账」，而底盘根本没动过。

---

## 4. 「结果未知禁止重试」的完整链路

### 4.1 动作下发 → 状态推进到「待取证」

`edge/agent/runner.go` 是执行侧。关键顺序（行号实读）：

```
Runner.RunControlled:
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

`preflightFailure` 是刻意收窄的白名单（`runner.go:525-531`）：

```go
func preflightFailure(skill runtime.CapabilityName, code string) bool {
	return code == "ROBOT_COMMISSIONING_ACTIVE" ||
		(skill == runtime.CapabilityNavigate && code == "NAV_MAP_NOT_READY")
}
```

**这就是整个机制的支点。** 我在同一份文档里看到过一句话（`docs/architecture/lifecycle-objects.md:125`）把三个对象在断网场景下的状态总结成一张表，值得直接引为教学素材：

| 对象 | 进入的状态 | 含义 |
|---|---|---|
| Step | `STARTED`（**保持不变**） | 「我记了开始，没记结束」——故意不写 `FAILED` |
| Tool 下发 | `ESCALATED` + `class = UNKNOWN_OUTCOME` | 「物理结果不可判定，禁止自动重试」 |
| Task | `RECOVERABLE_FAILURE` | 「停了，但可以恢复」 |

> **重要修正：** 该表第二行的「`ESCALATED`」在 Go 代码里**不存在**（§9-2）。真实存在的三件事是：Step 保持 `STARTED`（`runner.go:499-508`）、Round 记为 `VerdictUnsatisfied` 且 `Class = "UNKNOWN_OUTCOME"`（`internal/actionloop/loop.go:602-604`）、Task 由事件投影为 `RECOVERABLE_FAILURE`（`docs/development/single-robot-loop.md:265` 记录了 409 `PHYSICAL_OUTCOME_UNKNOWN`）。文档里的状态机图是**旧版 `Track` 状态机的残留**。

### 4.2 持久化：为什么必须是 `STARTED` 而不是 `FAILED`

`Uncertain` 的判据是**从持久记录派生**的，不是从任何内存态派生的（`agentruntime/memory.go:245-262`）：

```
function ExecutionMemory.Uncertain(taskID):
    steps := m.Steps(taskID)
    for step in steps:
        if step.Reconciled:            # memory.go:252-254
            continue                   # 人已经看过；继续报就是安全信号退化成噪音
        if step.Status == StepStarted: # memory.go:255-257
            uncertain.append(step)
    return uncertain
```

上游读取者 `OpsAgent.uncertainSteps`（`agentruntime/opsagent.go:364-379`）在**执行存储不可读时返回 `nil`**，注释（`opsagent.go:365-368`）写明理由：「执行存储缺失意味着『说不出来』，规则已经会报告这件事，编造一个空列表会被读成『没有任何不确定』。」

`Recovery` 视图把这一步对外化（`internal/localapp/recovery.go:50-59`）：

```
50-52  if !active && run.Status == StepStarted && !agent.IsReadOnlyCapability(run.Capability):
           view.UncertainStepIDs = append(...)
54     view.RequiresReconciliation = len(view.UncertainStepIDs) > 0
56     view.CanResume = task.Approved && !active && !queued && !view.RequiresReconciliation
                       && (task.State == PAUSED || task.State == RECOVERABLE_FAILURE)
58-59  case view.RequiresReconciliation:
           ReasonCode = "PHYSICAL_OUTCOME_UNKNOWN"
           Reason = "有物理动作缺少完成记录，请先核对机器人和物体状态。系统不会重放，也不允许通过修改任务版本绕过。"
```

`CanResume` 里 `!view.RequiresReconciliation` 这一项就是 HTTP 层 409 的来源。配套的 `CheckRecovery`（`edge/agent/runner.go:198-210`）返回 `ErrPhysicalOutcomeUnknown`，再经 `reasonCodeFor`（`edge/agent/agent.go:185-205`）映射成稳定码 `PHYSICAL_OUTCOME_UNKNOWN`（`agent.go:198`）。

`internal/localapp/recovery.go:85-101`（`checkRevisionRecovery`）堵死了另一条绕路：**不许改任务版本来跳过**——只要已存在非只读的 `COMPLETED` 步骤，切版直接报错「任务已执行部分物理动作，请先恢复原版本完成任务；不能切换版本重复执行已完成动作」。

### 4.3 对账：只有人能解除

`middleware/contracts.go:106-123` 的注释是这套设计最完整的自述：

> 「它之所以存在，是因为另一条路是死胡同。一个未确认的物理步骤会阻塞 readiness 并禁止重试该步骤——这是对的——但去看了机器人的操作员无处记录他看到了什么，于是阻塞永远不会解除。系统学会了忽略自己的安全报告，而这正是那份报告要防止的失败。」

写路径 `middleware/sqlite/store.go:220-244` 有两道硬约束：

```go
221-223  if !reconciliation.Outcome.Valid() { return error }
224-226  if Actor == "" || Note == "" { return "reconciliation requires the person making it and a reason" }
227-232  UPDATE step_runs SET reconcile_outcome=?, reconcile_actor=?, reconcile_note=?, reconcile_at=?
         WHERE task_id = ? AND step_id = ? AND reconcile_outcome = ''      -- ★ 只能从空到非空
240-242  if affected == 0 { return "step %s/%s is not awaiting reconciliation, or has already been reconciled" }
```

`WHERE reconcile_outcome = ''` 使对账**只能发生一次且不可覆盖**——不是靠应用层判断，是靠 SQL 条件。

### 4.4 恢复计划在改动机器人之前被结构性地挡住

`agentruntime/recoveryagent.go:505-582` 的 `choosePlan` 把对账置于最高优先级：

```
518  if len(facts.UncertainSteps) > 0 || finding.AutomaticRetryForbidden:
         # 读取失败也走这条分支：读失败 ≠ 没有东西要对账（recoveryagent.go:512-517）
         lookFirst := a.readOnlyMatches(finding)         # 只返回 Risk == RiskReadOnly（489-500）
         if len(lookFirst) > 0:
             return RecoveryPlan{
                 Diagnosis: "有物理动作结果未知；先重新观测完成对账，对账之前不改动机器人",
                 Confidence: 1,
                 Steps: stepsFromActions(lookFirst, "结果未知，必须先取证；该动作只读，不改动机器人"),
             }, TrailStep{Kind: TrailRule, Name: "rule.reconcile-first",
                  Findings: {"mutationsHeldBy": "闭环契约：结果未知时不得改动机器人"}}
         # 没有只读动作可匹配 → 只能升级给人
         return RecoveryPlan{Diagnosis: "有物理动作结果未知；在对账之前不能执行任何恢复动作",
                             Escalate: true}, TrailStep{Name: "rule.reconcile-first"}
583-590  if facts.ReconciliationUnavailable:
         return RecoveryPlan{Diagnosis: "无法核实该任务是否存在结果未知的物理动作；……",
                             Escalate: true}, TrailStep{Name: "rule.reconcile-unknown"}
```

**关键实现细节（`recoveryagent.go:527-536`）**：这个分支里**根本不咨询模型**，计划完全由目录中的只读匹配构造。注释说明这是刻意的——「排除是**结构性**的，而不是对一份已经包含了变更动作的计划做过滤」。

### 4.5 由模型驱动的循环里，未知结果是唯一「终止一切」的结论

`internal/actionloop/loop.go:523-611` 定义了四种调用结论：

```
callOK             — 通过，可以进下一轮
callUnknownOutcome — 调用可能改变了世界且没能确立；之后什么都不许跑
callFatal          — 该类失败不允许再尝试
callRecoverable    — 允许在新观测之后再来一轮
```

判定（`loop.go:560-610`）：

```
560-565  if err != nil && result.Code == "":  result.Code = "TOOL_EXECUTION_ERROR"   # 抛异常且没命名任何东西 = 未知
566-584  if !result.Success:
              class := closedloop.Classify(result.Code)
              if class == UnknownOutcome:      return callUnknownOutcome
              if !class.Retryable():           return callFatal
              return callRecoverable
586-608  # 工具自述成功。它说了不算。
         verdict := closedloop.Gate(Declaration{Manifest: tool.MutatesWorld}, dispatchedAt, result.Evidence)
         if verdict.Require() != nil:
              if tool.MutatesWorld:
                   record.Code  = "UNVERIFIED_WORLD_MUTATION"
                   record.Class = string(closedloop.UnknownOutcome)
                   return callUnknownOutcome
              return callRecoverable
```

`loop.go:494-501` 是这段的全部重量所在：

```go
case callUnknownOutcome:
    // The one outcome that ends everything. The world may already have
    // changed, so no further call is safe — not a different tool, not the
    // same tool again. This is the closed-loop contract, and a loop driven
    // by a model is exactly where it would be tempting to soften.
    outcome.Escalated = true
    outcome.Reason = roundRecord.Detail
    return outcome, nil
```

### 4.6 五个独立落点汇总

| # | 落点 | 文件:行号 | 强制方式 |
|---|---|---|---|
| 1 | 执行器不推进状态 | `edge/agent/runner.go:499-508` | 保持 `STARTED`，返回 `ErrUnverifiedWorldMutation` |
| 2 | 循环终止一切 | `internal/actionloop/loop.go:494-501` | `callUnknownOutcome` 直接 return |
| 3 | 续跑被拒 | `internal/localapp/recovery.go:56` | `CanResume` 含 `!RequiresReconciliation` |
| 4 | 恢复计划只有只读动作 | `agentruntime/recoveryagent.go:518-582` | 分支内不咨询模型；`readOnlyMatches` 硬过滤 |
| 5 | 面向人的建议带禁令 | `agentruntime/opsrules.go:391-397` | `severity=critical` + `AutomaticRetryForbidden=true` |

**没有自动重试器。** 这是整章最容易被学生误解的一点，必须讲透。旧版 `core/closedloop` 里存在过一个重试状态机（attempt budget、backoff、`NextAttemptAt`、升级阶梯），现在被**删除**而不是接线，理由写在包注释里（`closedloop.go:17-36`）：

> 「一个物理写入的重试预算要有意义就必须是持久的。内存里的尝试计数器在进程重启时会归零，所以一个在重试中途崩溃的 agent 会永远重试下去——正是这个上限本该防止的无界行为。」
>
> 「实际发生的事被平实地陈述出来：一次物理失败结束这个步骤，重新尝试它是**操作员的决定**，在恢复目录里以 `task.retry-step` 提供，并且需要批准。」

`task.retry-step` 的目录条目（`agentruntime/recoverycatalog.go:204-207`）：

```go
{
    ID: "task.retry-step", Summary: "在重新观测之后重做当前步骤（不是原样重放）",
    Risk: RiskBoundedWrite,
    Service: "task resume", Tools: []string{"task.resume"},
    Shapes: []string{"TRANSIENT", "PERCEPTION"},     // ★ 不匹配 UNKNOWN_OUTCOME
},
```

注意 `Shapes` 只覆盖 `TRANSIENT` 与 `PERCEPTION`：**目录本身也匹配不到未知结果**，所以它不会出现在对账分支的计划里。`RequiresApproval()` 为 true（非只读），`Executable()` 为 true。

---

## 5. 物理接地验证 GVF / GCL

### 5.1 它是什么

**GCL（Grounded Contract Language）**：边缘侧（Python）的**有限轨迹、三值、可执行合约检查器**。主文件 `robot/gateway/tangying_robot_gateway/assets/grounded-contracts.json`，schema 为 `grounded-contract.schema.json` / `state-report.schema.json`，解释器 `robot/gateway/tangying_robot_gateway/grounded/verifier.py`（文件头自述 `"Finite-trace, three-valued GCL interpreter with local evidence validation."`）。

**GVF（Grounded Verification Framework）**：把 GCL 接进运行时与 Go 控制面的那一层。组成：
- `robot/gateway/tangying_robot_gateway/grounded/runtime.py` — `GroundedRuntime`，位于「已准入的执行边界之内、终态成功之前」
- `robot/gateway/tangying_robot_gateway/grounded/store.py` — `EvidenceStore`，原始字节落 `blobs/<sha256>`
- `core/closedloop/grounded_report.go` — Go 侧的控制面投影
- `edge/agent/runner.go:433-459` — Go 侧消费点

**三值逻辑**：verdict ∈ {`VERIFIED`, `FALSIFIED`, `UNKNOWN`}（`grounded_report.go:51-53`）。`combine()` 是强 Kleene 语义（`verifier.py:34-49`）：合取中确凿反例为 `FALSIFIED`，全部通过才 `VERIFIED`，否则 `UNKNOWN`。

**九个谓词**（ADR-10 表格，`docs/superpowers/specs/2026-09-10-closed-loop-semantic-upgrade-adr.md:158-167`）：`At`（位置误差 ≤ 0.05 m，航向 ≤ 0.12 rad）、`On`（底部距支撑面 ≤ 0.02 m）、`Holding`（闭合 + 负载 ≥ 0.1 N + 身份匹配）、`In`（物体边界完整落在容器内）、`Clear`、`Stable`（相邻帧位移 ≤ 0.01 m）、`Released`、`Safe`（力 ≤ 20 N）、`Capacity`。

**三个核心合约**（`grounded-contracts.json`）：抓取要求连续三帧 `Holding ∧ ¬On ∧ Stable`；放置要求连续三帧 `In ∧ Stable` 且夹爪释放；导航要求连续三帧 `At`。

### 5.2 与主闭环契约什么关系

**它们是两层，不是两套。** 主闭环契约（`core/closedloop`）回答「**这一次工具返回算不算完成**」，用**时间戳新鲜度**判据（`Gate`），判据的输入是一个 `Evidence{ObservationID, ObservedAt, Freshness}`。GVF 回答「**物理关系是否真的成立**」，用**谓词 + 时序算子**判据，判据的输入是内容寻址的多模态测量流。

在 Go 侧两者的交接点是 `edge/agent/runner.go`：

```go
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

读法：**GVF 通过时直接跳过 Gate**（`&& !groundedVerified`）；GVF 未通过时返回 `ErrPhysicalOutcomeUnknown`（未知结果屏障），而不是退回 Gate。所以 GVF 是**更严的旁路**，不是替代品。反过来，GVF 关闭时 Gate 是唯一判据。

Python 侧的屏障语义（`grounded/runtime.py:104-117, 234-259`）：

```python
104  blocked = self.store.blocked(command.robot_id)
105  if physical and blocked:
106-115      # 构造 NOT_DISPATCHED 报告并以原屏障的 action 身份追加（保身份用于对账）
             return self.result(report)
...
234-259  release = bool(
             blocked
             and report.verdict == "VERIFIED"
             and effect == equivalent.get(blocked.action_name, blocked.action_name)
             and keys
             and all(report.action_params.get(key) == blocked.action_params.get(key) for key in keys)
         )
```

`equivalent` 映射（`runtime.py:236-240`）：`verify_grasp → manipulation.pick`、`verify_placement → manipulation.place`、`verify_arrival → navigation.navigate`。
`keys` 映射（`runtime.py:241-245`）：pick → `("object","gripper")`，place → `("object","container","gripper")`，navigate → `("goalPose","location")`。

**解除屏障的三个必要条件**：同一个意图效果、全部键值完全匹配、新证据裁定 `VERIFIED`。注释原文（`runtime.py:234-235`）：「一个只读验证器只能为**完全相同的意图效果**、从新鲜证据清除屏障；调用一个无关的观察不是对账。」这比 Go 侧的对账更强——Go 侧的人可以对账成 `ABANDONED`，GVF 屏障只接受 VERIFIED。

### 5.3 为什么默认关闭、开关是什么

**开关**：环境变量 `TANGYING_GVF_ENABLED`，值为字符串 `"1"` 时开启。
- Go 侧：`edge/agent/runner.go:434`、`tasks/service.go:544`
- Python 侧：`grounded/runtime.py:30-32`（`from_environment()` 不满足就返回 `None`）、`robot/ros2_ws/src/tangying_navigation/tangying_navigation/gazebo_runtime_node.py:471-475`
- 证据根目录：`TANGYING_GVF_ROOT`，Go 默认 `artifacts/grounded-runtime`，ROS 节点默认 `/data/maps/grounded-runtime`
- Runtime 与 Local Agent **都要设**（`docs/development/single-robot-loop.md:284`）

**默认关闭的三个理由**（都能从代码/文档读出，非推测）：

1. **它要求适配器实现采集器，而多数适配器没有。** 文档原文（`single-robot-loop.md:293`）：「**没有采集器的适配器返回 UNKNOWN，不能仅打开开关就当作具备物理验证能力。**」`grounded/runtime.py:44-57` 的 `_collect` 在 `backend` 没有 `collect_grounded_evidence` 时返回空列表，并在注释里写明「传感器失败不能把工具返回提升为世界事实」。
2. **它会把大量动作直接拒绝。** 文档原文（`single-robot-loop.md:293`）：「尚无后置合约的 `arm.move`、`navigation.pre_position`、`recover_to_safe_pose` 在 GVF 模式下**拒绝派发**；急停始终可以执行。」代码对应 `grounded/runtime.py:123-128`（`physical and not contract.postconditions` → `NOT_DISPATCHED`）。Gazebo 的服务 RPC 同样不能绕过：未接入 GCL 的变更服务返回 `GVF_CONTRACT_REQUIRED`（`gazebo_runtime_node.py:554-567`）。
3. **它是实验阶段的机制，合约阈值是夹具工程的工程值。** ADR-10 原文：「阈值是本次夹具/导航实验的工程值，**不是实机通用标定**」，以及「置信度未做频率校准，0.95 不代表已证明的 95% 正确率」。

另外有一条安全性的正向理由（`grounded_report.go:10-12`）：**原始证据留在边缘**，控制面只收到 `evidence://source/<sha256>` 与标量摘要；纳秒身份以 JSON 文本传递，不经 protobuf `Struct` 的浮点表示。

### 5.4 实验结论：必须区分实测与离线/回放估计

数据源：`docs/experiments/2026-09-21-grounded-verification.md`（150 个 Gazebo 任务实例、210 条物理动作轨迹、九组 1,890 条评估、1,470 次真实模型调用）。报告开头的发布说明（第 4 行）明确**原始记录不随 Git 分发**。

**属于实测的部分：**

| 指标 | 数值 | 行号 |
|---|---|---|
| 共同物理轨迹的实际任务成功率 | **38.00 ± 1.83%**（各组读同一批轨迹，对所有组相同） | 第 60 行 |
| 错误接受率（B0 → GVF） | **58.57% → 13.81%**，双侧配对 t，p=0.00003336 | 第 8 行 |
| 准确率（B0 → GVF） | **41.43% → 64.76%** | 第 8 行 |
| 错误接受的绝对条数 | B0 `123/210`、B2 `47/210`、GVF `29/210` | 第 195 行 |
| 验证器延迟 | GVF `4.97 ± 1.18 ms`（含本地证据哈希检查、合约解释、报告渲染，不含采集） | 第 53 行 |
| 实测模型调用 | 总 tokens **4,596,735**；协议异常 **0**；返回模型 `deepseek-flash` | 第 64、227 行 |
| 完整性复核 | **6,259** 个唯一证据哈希、**1,470** 个模型响应哈希通过 | 第 226 行 |
| Go/Python 测试 | Python：2,007 通过 / 33 跳过 | 第 224 行 |
| 正常运行验收 | 导航 0.25 m 后独立世界位姿误差 **0.01308 m**（阈值 0.05 m） | 第 218 行 |

**属于离线/回放估计的部分（报告本身反复标注）：**

- **任务成功率、恢复步骤、重复失败率、端到端耗时、每动作人工升级次数**——全部是「恢复策略回放估计」。第 22 行原文：「各组没有独立在线运行；任务成功率、恢复步骤和端到端成本是该回放策略的估计，**不能当作九组自主机器人实测**。」
- **B1 与 A5 两组是离线计分器**，「只用于离线计分，不能向生产硬件下指令」（第 20 行）。
- **验证器延迟**中 B1/A5 的 851.77 ms / 878.93 ms 是**实测模型请求时间**，不是验证器计算时间。

**负面结论（必须一并给出）：**

1. **A5（带合约的 LLM）总准确率 74.29%，高于 GVF 的 64.76%**，差 9.52 个百分点，p=0.0002249。报告第 8 行原文：「**不能宣称 GVF 全面优于 LLM。**」错误接受率差异不显著（p=0.3739）。
2. **移除时序算子（A1）后三值判定与 Ours 完全相同**，总正确判定数变化为 0。第 196 行：「**时序约束提升本批准确率：未得到支持。**」
3. **B2（只用末帧几何）准确率 70.48% 高于 GVF**，因为 GVF 有 45/210 次弃权。第 195 行：「UNKNOWN 是可见的安全代价，**不能从分母删除**。」
4. **GVF 的 29 次错误接受全部来自未注入故障的导航动作**：轮式里程计到达目标，独立世界位姿没到达。第 203 行称这是「本报告的主要负面发现」。教训原文：「多帧重复同一种有偏测量依然会得到错误结论。」
5. **A3（隐藏失败分类）回放任务成功率 38%，Ours 60%**——分类帮助恢复「仅在给定回放策略下支持」，不是执行器能力提高。

**消融汇总**（第 36-46 行表格）：B0 38.00 / B1 58.00 / B2 53.33 / Ours 60.00 / A1 60.00 / A2 60.00 / A3 38.00 / A4 60.00 / A5 59.33（任务成功率估计 %）。

---

## 6. 与通用 coding agent（Codex / Claude Code 类）的根本差异

不讲「机器人更难」这种空话，只列能从代码读出的结构性差异。

**差异 1：成功信号的性质不同。** 通用 coding agent 的 `Write`/`Edit`/`Bash` 返回码是**可验证的终态**——文件在磁盘上，读回来就知道。本系统的 `toolResult.Success` 被显式定义为**「去看世界的触发」而不是证明**（`core/closedloop/closedloop.go:1-15`、`core/harness/evaluator.go:3-4`、`core/skills/manifest.go:30-33`）。因此存在一个 coding agent 不需要的东西：一个**独立于返回码的完成判定器**，以及一条「工具自述成功但没有动作后的新鲜观测 → 不记为完成」的规则（`internal/actionloop/loop.go:586-608`）。

**差异 2：不可逆性与幂等性的默认假设相反。** coding agent 的中间失败可以随便重试——`git checkout`、重跑测试、再写一次文件都不产生新事实。物理动作**每次重试都是一个新事实**。所以本系统把「不可重试」设为默认：任何未识别的错误码一律归 `UnknownOutcome`（`closedloop.go:286-299`），只有明确列入白名单的 `Transient`/`Perception` 才 `Retryable()`（`closedloop.go:333-340`）。coding agent 的默认是「失败就重试」，这里的默认是「失败就停下问人」。

**差异 3：状态必须持久到能跨越进程崩溃。** `Uncertain` 从 SQLite 的 `step_runs` 派生（`agentruntime/memory.go:245-262`），文档记录过一次真实盲区：`OpsAgent` 只订阅未来事件，进程重启后磁盘上躺着「可能已经动了但没人知道」的步骤，而监督 agent 报告一个干净、安静的机器人（`docs/architecture/supervision-verification.md:92-101`）。原话：「这是监督者最糟的失效模式：**沉默被读成健康**。」coding agent 的会话上下文丢失顶多是重新解释需求；这里丢失的是「机器人现在在哪、手里有没有东西」。

**差异 4：存在一个只有人能解的状态。** `StepOutcomeAbandoned`（`middleware/contracts.go:91-93`）与 `StepReconciliation`（`contracts.go:106-123`）是「把世界状态的结论交给一个人」的**显式数据类型**。SQL 层的 `WHERE reconcile_outcome = ''`（`middleware/sqlite/store.go:230`）保证它一次性且不可覆盖。`internal/localapp/recovery.go:16-19` 的注释点名了唯一写入者：「它在 App 上……是那条记录的唯一写入者：没有定时器、没有 agent 可以调用它。这个阻塞存在的目的就是等人，所以一条能清除它的软件路径会让这个等待变成表演。」coding agent 里没有对应物——没有任何状态是「必须由人签字才能离开」的。

**差异 5：模型不参与安全关键的那一步。** 通用 agent 的安全边界通常是提示词 + 工具白名单 + 人工确认。这里的关键分支**在结构上排除了模型**：`choosePlan` 的对账分支不构造模型请求（`recoveryagent.go:527-536` 注释：「模型在这个分支里完全不被咨询，这正是把变更提议挡在计划外的原因——排除是结构性的，而不是对已经包含了变更动作的计划做过滤」）。工具的 `SafetyLevel` 也不能自证：`needsApproval` 从 manifest 的安全级别派生，不询问工具本身（`internal/actionloop/loop.go:620-626`：「所以一个工具不能通过声称自己不需要批准来把自己排除在批准之外」）。

**差异 6：有两个独立的「改世界」声明源，且取并集。** `Declaration.Manifest || Declaration.RuntimeMutatesWorld`（`core/closedloop/gate.go:20-29`）——本地可信目录（`manipulation.Catalog()`，常量，适配器改不动）与已连接 runtime 的自述能力。注释原文：「只有远端声明一次写入，仍然会拿到门禁；只有本地声明，仍然会拿到门禁。」这是对「远端适配器可能不知道本地目录、本地目录可能不认识适配器特有工具」的双向防御。coding agent 的工具清单只有一个权威来源。

**差异 7：错误码分类表是安全决策，需要守卫。** 因为兜底方向是「最保守 = 最阻碍恢复」，一个漏掉的码会静默地把一个已知失败报成未知结果。所以有 `Knows()`（`closedloop.go:302-322`）、有提交进仓库的 `runtimeEmittedCodes` 清单、有 `TestEveryCodeTheRuntimeCanEmitIsClassified`。coding agent 的错误处理通常不需要这种「表项完整性」守卫，因为最坏后果是重试一次。

---

## 7. 教学价值

### 7.1 适合的比喻

**比喻 A：快递签收。** 「工具返回成功」= 快递员说「送到了」。这个陈述是真的（他确实来过、确实按了门铃），但它不是「包裹在屋里」。**验收需要另一次独立的观测**，而且必须是**下单之后**的（`Gate` 的时序规则）。一个「放在门口」的照片如果拍于下单之前，那不是这次快递的证据。

**比喻 B（更贴合）：手术器械清点。** 手术结束前必须清点器械——不是为了记录，而是因为**下一次操作的前提是知道上一手的真实结果**。如果纱布数量对不上，正确的反应不是「再数一遍」或「继续缝合」，而是**停下来、用只读手段查清、由人签字**。这正好对应：`Transient`/`Perception` 允许「再数一遍」，`UnknownOutcome` 只允许「查清」（`readOnlyMatches`），而清点结果只能由人写一次（`StepReconciliation` + `WHERE reconcile_outcome = ''`）。

**比喻 C（讲「未知结果」的最佳）：三明治里的刀。** 你切三明治切到一半，手滑了，然后停电。你不知道刀现在在哪。这时候任何「重来一次」的动作都是伸手去摸——**可能摸到刀刃**。

### 7.2 最小示例（可直接放进章节）

**最小 Gate 示例**（可以只用三行 Go 表达核心思想）：

```go
// 只读工具：不需要证据
closedloop.Gate(closedloop.Declaration{}, time.Time{}, nil)
// → {Satisfied: true, Required: false, Reason: "CLOSURE_NOT_REQUIRED"}

// 改世界的工具：一次成功的返回不足以完成
closedloop.Gate(closedloop.Declaration{Manifest: true}, dispatch, nil)
// → {Satisfied: false, Reason: "CLOSURE_EVIDENCE_REQUIRED"}

// 有病历的观测也救不了：它早于命令
closedloop.Gate(closedloop.Declaration{Manifest: true}, dispatch,
    &closedloop.Evidence{ObservationID: "obs-before",
        ObservedAt: dispatch.Add(-time.Second), Freshness: "FRESH"})
// → {Satisfied: false, Reason: "CLOSURE_EVIDENCE_STALE"}
```

三个断言可以直接对照 `core/closedloop/gate_test.go:18-67`。

**最小分类示例**：

```go
closedloop.Classify("GRASP_NOT_REACHED")         // → Perception,  Retryable() == true
closedloop.Classify("PLACEMENT_NOT_OBSERVED")    // → UnknownOutcome, Retryable() == false
closedloop.Classify("A_CODE_NOBODY_HAS_SEEN")    // → UnknownOutcome, Retryable() == false
closedloop.Knows("EXECUTION_OUTCOME_UNKNOWN")    // → true   （表里列为 UnknownOutcome）
closedloop.Knows("A_CODE_NOBODY_HAS_SEEN")       // → false  （表里根本没有）
```

最后两行的对比是本章最漂亮的一个教学点：**两个码 `Classify` 到同一个值，但它们是不同的事实。**

### 7.3 学生最容易误解的点

| # | 误解 | 纠正 |
|---|---|---|
| 1 | 「`UNKNOWN_OUTCOME` 意味着失败了」 | 它意味着**不知道**。可能成功了，可能失败了一半。所以它既不是 `FAILED`，也不是可重试的失败。 |
| 2 | 「系统会自动重试瞬时故障」 | **没有自动重试器。** `Transient.Retryable()` 回答的是「这类失败是否原则上可重试」，注释写明「这是一个关于失败的陈述，不是关于系统的陈述」。物理重试是操作员决定，走 `task.retry-step`，要批准。 |
| 3 | 「未识别的码就是瞬时故障，重试一次没关系」 | 兜底方向**相反**：未识别 → `UnknownOutcome` → 禁止重试。这是本章的核心取舍。 |
| 4 | 「观测够新就行」 | 不够。**必须晚于命令下发**（`gate.go:92-97`）。一个 10ms 前拍的、但拍于命令之前的照片，是完全无用的证据。 |
| 5 | 「经验丰富的观测者可以凭一次观测断定成功」 | `*_NOT_OBSERVED` 家族恰好是反例：**动作跑了、没检查**。而 `GRASP_FAILED`（检查了，手是空的）反而是已知的感知失败。区分它们的是「有没有检查过」，不是「结果好不好」。 |
| 6 | 「多帧重复测量更可靠」 | 实验明确否证：GVF 的 29 次错误接受全部来自**同一有偏测量重复多帧**（轮式里程计）。`docs/experiments/2026-09-21-grounded-verification.md:203`：「多帧重复同一种有偏测量依然会得到错误结论。」 |
| 7 | 「未知结果状态被人对账掉了，就等于成功了」 | 不是。`StepReconciliation` 记录的是「有人看过」，`StepRecord` 的注释原文：「它不意味着结果已知：对于一个从未记录过结果的步骤，没有什么能让它变已知。它意味着有人看过了。」（`core/agentcontract/memory.go:113-118`） |
| 8 | 「把任务版本改一下就能绕过」 | 不能。`checkRevisionRecovery`（`internal/localapp/recovery.go:85-101`）只要发现存在非只读 `COMPLETED` 步骤就拒绝切版。 |
| 9 | 「`Freshness` 是证据自己的属性」 | 它是**投影对来源给出的裁决**（`closedloop.go:367`），由消费方计算（`Snapshot.EvidenceFreshness`），并且在急停时被强制降级为 `UNKNOWN`。 |
| 10 | 「沙盒/仿真里能跑通就等于实机行为已验证」 | 报告结论第 280 行：「环境感知通过不意味着这套控制实现能直接迁移至实机。」 |

### 7.4 练习题（3 题，含答案要点）

**题 1（时序与粒度）**
某 runtime 以 Unix 毫秒上报采集时间。命令在 `T = 12:00:00.000400`（400 微秒）下发，证据观测时间为 `12:00:00.000000`。Gate 判通过还是拒绝？把观测时间改成 `12:00:00.000500` 呢？说明为什么这不是「给出的容差」。

**答案要点：**
- `NormalizeDispatchTime` 把下发时刻截断到 `12:00:00.000`；`12:00:00.000000` 截断后相等，`observedMS.Before(dispatchedMS)` 为 false → **通过**。
- `12:00:00.000500` 截断到 `12:00:00.000`，相等 → 同样**通过**。证据：`core/closedloop/gate_test.go:92-106`。
- 这不是容差：runtime 的采集时间分辨率就是整毫秒，亚毫秒排序**原理上不可观测**。把 `T=…000400` 的观测判为「早于命令」需要亚毫秒信息，而这份信息根本不存在于上报格式中。
- 反例边界：`12:00:00.000000 - 1ms = 11:59:59.999` 截断后小于 `12:00:00.000` → **拒绝**。

**题 2（分类归属）**
`PLACEMENT_NOT_OBSERVED` 与 `PLACE_NOT_REACHED` 的物理语义差别是什么？为什么一个必须是 `UNKNOWN_OUTCOME`、另一个可以是 `PERCEPTION`？如果错误地把 `PLACE_NOT_OBSERVED` 归到 `PERCEPTION`，最坏会发生什么？

**答案要点：**
- 语义差别在于**动作是否已执行**：`PLACE_NOT_REACHED` 在夹爪张开**之前**返回（接近或最终位姿检查失败），物体仍然被握着 → 物理效果可证伪，**确定没发生**。`PLACEMENT_NOT_OBSERVED` 在放置动作**完成之后**返回，只是没观测到预期关系 → 物体可能已经在目的地了。
- 归类理由：`Perception.Retryable() == true`，允许重试；`UnknownOutcome.Retryable() == false`。
- 最坏后果：重试一次已经成功了的放置——第二次释放会把物体推离目的地或造成碰撞。源码注释原文（`closedloop.go:93-100`）：「对放置来说，物体可能已经在目的地了。」
- 对照：`GRASP_FAILED` 是 `Perception`（手已知是空的），`GRASP_NOT_OBSERVED` 是 `UnknownOutcome`。

**题 3（链路推理）**
一个物理步骤执行到一半时 Local Agent 进程被 `SIGKILL`。请列出重启后系统按顺序读取哪些持久数据、得出什么结论、以及**哪一步必须由人完成**；并说明为什么「删掉 `agent.db`」不是对账。

**答案要点：**
1. `edge/agent/runner.go:419` 在调用前已 `MarkStepStarted`，SQLite `step_runs` 里该步骤状态为 `STARTED`。
2. `agentruntime/memory.go:245-262` 的 `Uncertain` 返回该步骤（`Status == STARTED` 且 `Reconciled == false`）。
3. `agentruntime/opsrules.go:316-373` 产出 `ANOMALY_UNVERIFIED_MUTATION`，`severity=critical`，`AutomaticRetryForbidden=true`，建议含「对账」「观测」。
4. `agentruntime/recoveryagent.go:518` 走对账分支，计划只由 `readOnlyMatches`（只读）构成，`TrailRule` 名为 `rule.reconcile-first`，`mutationsHeldBy = "闭环契约：结果未知时不得改动机器人"`。
5. `internal/localapp/recovery.go:54-59` → `RequiresReconciliation = true`、`CanResume = false`、`ReasonCode = "PHYSICAL_OUTCOME_UNKNOWN"`；HTTP 层返回 409。
6. **必须由人完成的一步**：`POST` 对账接口 → `Runner.ReconcileStep` → `middleware/sqlite/store.go:220-244`，要求 `Outcome ∈ {HAPPENED, NEVER_ACTED, ABANDONED}` **且** `Actor`、`Note` 非空，`WHERE reconcile_outcome = ''` 保证一次性。
7. **删除 `agent.db` 不是对账**，因为它是「删除证据」而不是「获取证据」：`Uncertain` 派生自持久记录，记录没了阻塞会消失，但世界状态**一点都没被确定**。文档明说（`single-robot-loop.md:284`）：「重启会保留未知结果屏障，**删除数据不能视作对账**。」

---

## 8. 可引用的真实案例

**案例 1（最直接）：用户要求搬两个杯子，系统搬了一个并报成功。**
文件：`docs/development/home-scene-expansion-plan.md:141-147`（§3.6「多物体自然语言任务：实测结果」）。
请求：「从客厅出发，去厨房拿红色杯子放进蓝色收纳盒，**再拿蓝色杯子放进蓝色收纳盒**，然后回到客厅」。
结果：**任务报告成功，只搬了红杯子**。证据链是「确认步骤与单物体流程**完全一致**（`observe → navigate → resolve → plan_grasp → pick → verify_grasp → place → verify_place → navigate_02 → verify_arrival_02`），产出中只有 `object_id: red-cup`，没有任何第二次 pick/place」。
文档自己的定评：「**这是最危险的一种**：用户要求搬两个杯子，系统搬了一个还报成功。它比直接失败更糟，因为**没有人会去检查**。」
根因：`agent/intent/parser.go` 的物体正则要求每个子句都有动词（`homeObjectAction` 正则以 拿/取/抓/拾 开头），以「把」开头的子句整体未被解析。

**案例 2（数字最硬）：工具返回 SUCCESS，物理真值 FALSIFIED。**
文件：`docs/experiments/2026-09-21-grounded-verification.md:131-165`（§6 失败案例与原始证据）。九条里挑最能说明问题的：

| 位置 | 工具返回 | 物理真值 | 验证器裁定 |
|---|---|---|---|
| `1729/task-02` | SUCCESS | FALSIFIED | `FALSIFIED/GRASP_MISS` |
| `1729/task-03` | SUCCESS | FALSIFIED | `FALSIFIED/GRASP_SLIP` |
| `1729/task-04` | SUCCESS | FALSIFIED | `FALSIFIED/WRONG_OBJECT` |
| `1729/task-05` | SUCCESS | VERIFIED | `UNKNOWN/PERCEPTION_OCCLUDED`（**误报保守**） |
| `1729/task-06` | SUCCESS | VERIFIED | `UNKNOWN/EVIDENCE_INSUFFICIENT` |
| `1729/task-27` | SUCCESS | FALSIFIED | `VERIFIED/NONE`（**漏报，最危险**） |

每条都带内容寻址证据（`evidence://edge/<sha256>`）与报告 ID（`gvf-<sha256>`）。
聚合数字：B0（信任回执）错误接受 **123/210 = 58.57%**；GVF **29/210 = 13.81%**，p=0.00003336。

**案例 3：一个真实的诊断错误——把一个从未发生的动作报成「结果未知」。**
文件：`docs/development/2026-09-17-supervision-blind-spots.md:145-172`；守卫在 `core/closedloop/classification_coverage_test.go:122-126`。
过程：审计机器人侧所有能返回的失败码。第一遍只搜一种写法找到 53 个；发现 `GOAL_NOT_CLEAR` 是通过 `ServiceError(...)` 抛出的，「**整整一类被漏掉了**」，补上后合计 **83 个唯一码**。
结果：**58 个不在分类表里**，全部静默退化成「结果未知，禁止重试」。
真实症状：`navigation.navigate 失败：GOAL_NOT_CLEAR（UNKNOWN_OUTCOME）`，建议「不要自动重试：先对账确认这次动作的实际结果」——而**底盘根本没动过**。文档定评：「让操作员去对账一个从未发生的动作，比不报还糟。」
修复后同一码输出 `（PERCEPTION）` + 「重新观测或搜索目标后再尝试」。
**审计自身也有盲区**（同一文件第 172-190 行）：扩宽证据来源到 5 类后得到 **113 个码**，又补进 16 个，包括 `PLACEMENT_NOT_OBSERVED`——它作为**字符串参数**传给 `self._verify_relation(...)`，而不是 `ToolResult(False, "...")` 或 `ServiceError("...")` 的字面量，正则匹配不到。

**案例 4：监督者的沉默被读成健康。**
文件：`docs/architecture/supervision-verification.md:92-113`。
测试输出原文：`blind spot confirmed: 1 unconfirmed step(s) on disk, none reported`。
问题：`OpsAgent` 只订阅未来事件，进程重启后崩溃前的失败完全看不见——磁盘上躺着「可能已经动了但没人知道」的物理步骤，监督 agent 报告一个干净、安静的机器人。
修复三层：`agentcontract.TaskHistory`（新 port）、`OpsAgent.History` + `learnExistingTasks` 启动扫描、`Finding.TaskID`（第三层是第一版**漏掉**的，现象是「诊断出来了但账本里没有」，`taskID=""` 是根因）。

**案例 5：真实机器人上「哪一步失败」不可达。**
文件：`docs/architecture/supervision-verification.md:201-215`。
实测蓝色瓶子任务（`tabletop` 场景，瓶子够不到）：任务真的失败了（`GRASP_NOT_REACHED`，37 事件），但 OpsAgent 只报「有任务异常结束」。
修复后对照：定位从「只有『有任务异常结束』」变为「`manipulation.pick 失败：GRASP_NOT_REACHED（PERCEPTION）`」；建议从「核对执行记录」变为「重新观测或搜索目标后再尝试」。

**案例 6（注意：这是 SLAM 覆盖实验，不是伪成功实验）**
`38.9% → 81%` 这组数字出自 `docs/experiments/2026-09-15-slam-exploration-coverage-upgrade.md:60`，讲的是 **SLAM 探索覆盖率**（改前 38.9% / 改后 81%），配套结论在第 82 行「多段是特性：leg 1 在 39% 处报 `no_reachable_frontier` 后，leg 2/3 继续把地图推到 81%」。**它与「工具报成功但实际没做到」无关**，若在本书中并列使用需要显式区分。伪成功主题的正确数字是 §5.4 与案例 2 的那一组。

**案例 7：真实运行时上「未知结果」的落地验收。**
文件：`docs/development/single-robot-loop.md:265`。
任务 `task-e2c52a114e397872b1670852` 在拿取中被终止；重启后 resume 返回 **409 `PHYSICAL_OUTCOME_UNKNOWN`**，**拿取仅派发 1 次，放置 0 次**。证据存于 `artifacts/acceptance/rgbd-v1-2026-09-08/crash-run-1/`。
对照任务 `task-ee64bd90bc06c0815c2ff637` 在 revision 1 完成两目标，拿取/放置各调用 2 次，**完成动作未重放**。

**案例 8：实验里被修掉的、可能产生错误的真实缺陷（可作为「为什么需要内容寻址证据」的引子）**（`docs/experiments/2026-09-21-grounded-verification.md:207-214`）：
- 「未知动作被后续观察覆盖、逻辑时钟重启复用、阻断报告沿用旧动作身份」→ 改为持久化原始效果屏障、事务分配时钟、当前命令独立报告。
- 「动作超时或适配器异常可能已产生运动」→ 停止后保存 UNKNOWN 与回执，**重启仍禁止直接重试**。
- 「无合约服务 RPC 绕过写入屏障」→ GVF 模式拒绝此类变更服务，急停和只读查询保留。

---

## 9. 冲突与不确定清单（写作时必须显式处理）

**冲突 1：失败分类是「七类」还是「八类」——计数口径差异，但标题会误导读者。**
代码是 **8 个平级常量**（`core/closedloop/closedloop.go:48-68`）：`Transient` `Perception` `Planning` `Permission` `Resource` `Validation` `UnknownOutcome` `Fatal`。
文档侧的「七类」出现在：
- `docs/architecture/supervision-verification.md:24`（标题「七类失败分类」，其下表格列 **8** 行）；
- `docs/architecture/supervision-verification.md:298`（「失败分类 | 七类 + `UNKNOWN_OUTCOME` 兜底」）；
- `docs/development/2026-09-17-supervision-blind-spots.md:167`（「按既有七类语义把 83 个码全部归类（权限 / 验证 / 资源 / 瞬时 / 感知 / 规划）」——注意这里只列了 **6** 个，还漏了 `PLANNING`，是写作者的即席枚举）；
- `docs/architecture/lifecycle-objects.md:305,611`。

**判定：这不是事实冲突，而是口径差异。** `supervision-verification.md:298` 给出了文档作者的意图——「七类 + `UNKNOWN_OUTCOME` 兜底」，即把 `UnknownOutcome` 当成兜底而非平级类。但代码里它是 `Class` 枚举的平级成员，`Retryable()` 也把它和其余 7 个一视同仁地比较（`closedloop.go:333-340`）。
**写作建议：正文统一说「八类」，并在脚注说明文档旧称「七类 + 兜底」。** 标题写「七类」而表格列 8 行是最容易让学生困惑的写法。
ADR-10 用的是「原八类映射」（`docs/superpowers/specs/2026-09-10-closed-loop-semantic-upgrade-adr.md:189`）与「细分及八类失败」（同文件 `:205`），与代码一致。

**冲突 2：`Track` 重试状态机在文档里仍存在，代码里已删除。**
文档 `docs/architecture/lifecycle-objects.md:295-329` 给出一个 Tool 状态机图：

```
PENDING → EXECUTING → AWAITING_EVIDENCE → VERIFIED   （成功且证据新鲜）
                    ↘ RETRYING                        （可重试的失败，有预算）
                    ↘ ESCALATED                       （不可自动处理）
```

并引用 `ErrUnknownRetry = errors.New("unknown physical outcome must not be retried automatically")`（第 321 行）说它「在 `core/closedloop/closedloop.go`」。

**代码实读结果（全仓库非 artifacts 范围搜索）：**
- `type Track` / `func NewTrack` — **零命中**
- `ErrUnknownRetry` — **零命中**
- `RETRYING` / `ESCALATED`（Go 标识符）— **零命中**
- `AWAITING_EVIDENCE` — 命中 4 处，但**全部是事件 `activityStatus` 字符串**，不是状态机状态：`edge/worker/worker.go:337`（发布）、`edge/agent/runner.go:332`（续跑绑定时接受）、`edge/worker/worker_identity_test.go:66-68`、`edge/worker/worker_activity_test.go:39`

代码侧的权威说明在 `core/closedloop/closedloop.go:17-36`，并且 ADR-10 开头有一段明确的「现状校正」（`docs/superpowers/specs/2026-09-10-closed-loop-semantic-upgrade-adr.md:143`）：

> 「上述 ADR-4 的 `Track` 重试状态机**后来已移除**；当前 `core/closedloop` 只负责完成门禁、失败分类与恢复建议。生产中物理失败仍须持久化对账及上层决策，**不能把本次实验里的重试策略描述成现有 Agent 的自动恢复能力**。」

另外 `core/closedloop/gate.go:81-84` 的 `validateFreshness` 注释仍写着「shared by Gate and **Track.Fresh** so the two entry points cannot drift」——这是删改后遗留的**悬空注释**，`Track.Fresh` 已不存在。

**冲突 3：`docs/architecture/lifecycle-objects.md:140` 自己标注的诚实说明。**
原文：「仓库里现存的事故记录（`artifacts/incidents/`）**没有一条**真的走到 `uncertainStepIds` 非空——那 13 条都是 `NO_RECOVERY_REQUIRED` 或 `PAUSING`。上面这个断网场景是**根据代码语义构造的**，不是从真实事故里摘的。它在测试里有覆盖（`edge/agent/recovery_test.go`、`internal/localapp` 的恢复用例）。」
**所以「结果未知」在生产事故归档里尚无一手实例**；有的一手证据是 §8 案例 7 的验收任务与单元/集成测试。本书引用时必须保留这个限定。

**冲突 4：分类表有 2 个重复项，其中 2 个 Planning 条目不可达。**（本次实读发现，未见任何文档提及）
实读结果：`XLEROBOT_MAX_RELATIVE_TARGET` 与 `XLEROBOT_MAX_ACTION_CHUNK_LENGTH` **同时**出现在 `Permission`（`closedloop.go:147`）与 `Planning`（`closedloop.go:216-217`）。由于 `Permission` 在有序表中先于 `Planning`（`closedloop.go:130` vs `213`），`Classify` 对这两个码返回 `Permission`，**`Planning` 里的两个条目是死代码**。
这是本次研究独立发现的问题，测试 `TestEveryCodeTheRuntimeCanEmitIsClassified` 只断言 `Knows() == true`，无法发现跨类重复。
(推断) 更合理的一侧应是 `Planning`——`XLEROBOT_MAX_RELATIVE_TARGET` 在命名上是「单次相对目标上限」，属于「计划不可执行」，与同表的 `NAV_WORKSPACE_LIMIT` `NAV_STEP_LIMIT` 同族；但 `Permission` 侧带有「XLeRobot 驱动的 blocker 码，都是委托/配置事实」的注释（`closedloop.go:135-152`），说明是有意放的。**两侧都有注释，无法判定哪个是笔误。**

**不确定 1：`Freshness` 为空字符串时 Gate 放行。** `validateFreshness`（`gate.go:98-103`）的 switch 只处理 `STALE`/`UNKNOWN`，其余落 default 返回 nil。测试用例集（`gate_test.go:69-86`）没有覆盖空串。是刻意宽松还是遗漏，**无法从代码判定**，但生产者总是写值，所以现实中不会触发。

**不确定 2：`Declaration.RuntimeCapabilityKnown` 在 `Mutates()` 中不参与判定**，只在 `closureContext.declaration` 里被填（`edge/agent/runner.go:673`），且在 `gate_test.go:125-126` 被断言单独为 true 时 `Mutates() == false`。它的用途（诊断？未来扩展？）**在当前代码里没有消费者**。(推断) 它是为「runtime 能力未知时应该更保守还是更宽松」留的字段，但目前语义未启用。

**不确定 3：`agentruntime/permission.go` 有第三个 `MutatesWorld`。** 字段在 `permission.go:58-59`，被 `permission.go:97, 121, 170` 使用——这是 **agent 级权限声明**，与 `closedloop.Declaration` 和 `skills.SkillManifest.MutatesWorld` 是三个不同层级的概念。写作时必须区分，否则读者会以为是同一个开关。第三章的 `edge/agent/agent.go:70-76` 把执行 agent 声明为 `MutatesWorld: true, MutatesTaskState: true, RequiresApproval: false`，注释解释为什么 agent 级不设审批。

**不确定 4：文档里「七类」之外的分类名对齐表**只在 ADR-10（`2026-09-10-closed-loop-semantic-upgrade-adr.md:180-190`）出现，把 9 个 GCL 细分码映射到 8 类（如 `EVIDENCE_INSUFFICIENT → UNKNOWN_OUTCOME`、`CONTRACT_VIOLATION → VALIDATION`）。这 9 个细分码与 `FailureType` 字段（`grounded_report.go:20`）的关系在 Go 侧只是字符串，**Go 不做二次分类**——`ParseGroundedReport` 只检查 `Verdict` 三值，不检查 `FailureType` 是否属于已知集合（`VERIFIED` 时要求 `FailureType == "NONE"`，其余不校验）。

---

## 10. 源码索引

### Go 源码

- `core/closedloop/closedloop.go:1-16`（包文档：工具结果是看世界的触发）
- `core/closedloop/closedloop.go:17-36`（重试状态机为何被删除）
- `core/closedloop/closedloop.go:45-68`（`Class` 与 8 个类常量）
- `core/closedloop/closedloop.go:70-76`（`ErrEvidenceRequired` / `ErrEvidenceStale`）
- `core/closedloop/closedloop.go:83-274`（分类表）
- `core/closedloop/closedloop.go:87-129`（`UnknownOutcome` 15 码与设计注释）
- `core/closedloop/closedloop.go:130-162`（`Permission` 29 码）
- `core/closedloop/closedloop.go:163-167`（`Resource` 9 码）
- `core/closedloop/closedloop.go:168-212`（`Perception` 47 码）
- `core/closedloop/closedloop.go:213-229`（`Planning` 17 码）
- `core/closedloop/closedloop.go:230-253`（`Validation` 27 码）
- `core/closedloop/closedloop.go:254-270`（`Transient` 32 码）
- `core/closedloop/closedloop.go:271-273`（`Fatal` 3 码）
- `core/closedloop/closedloop.go:284-300`（`Classify`）
- `core/closedloop/closedloop.go:302-322`（`Knows`）
- `core/closedloop/closedloop.go:324-340`（`Retryable`）
- `core/closedloop/closedloop.go:342-349`（`DispatchPrecision`）
- `core/closedloop/closedloop.go:351-359`（`NormalizeDispatchTime`）
- `core/closedloop/closedloop.go:361-373`（`Evidence`）
- `core/closedloop/gate.go:20-29`（`Declaration` / `Mutates`）
- `core/closedloop/gate.go:33-39`（Reason 常量）
- `core/closedloop/gate.go:42-51`（`Decision`）
- `core/closedloop/gate.go:60-79`（`Gate`）
- `core/closedloop/gate.go:81-105`（`validateFreshness`）
- `core/closedloop/gate.go:108-126`（`equalFold`）
- `core/closedloop/gate.go:128-137`（`ErrNotSatisfied` / `Require`）
- `core/closedloop/grounded_report.go:10-34`（`GroundedReport`）
- `core/closedloop/grounded_report.go:36-64`（`ParseGroundedReport`）
- `core/closedloop/gate_test.go:16-132`（Gate 全部边界用例）
- `core/closedloop/closedloop_test.go:16-49`（分类断言）
- `core/closedloop/classification_coverage_test.go:12-31`（表无守卫的风险）
- `core/closedloop/classification_coverage_test.go:118-280`（`runtimeEmittedCodes` 清单）
- `core/closedloop/classification_coverage_test.go:288-303`（覆盖守卫测试）
- `core/closedloop/classification_coverage_test.go:305-350`（`Knows` 的区分测试）
- `core/observation/envelope.go:43-62`（`Envelope`）
- `core/observation/envelope.go:122-177`（`Validate`）
- `core/observation/catalog.go:14-32`（`SourceDescriptor` / `Catalog`）
- `core/harness/evaluator.go:1-20`（包文档 + `Status`）
- `core/harness/evaluator.go:27-52`（`EvidenceBasis` / `Input`）
- `core/harness/evaluator.go:71-164`（`Evaluate` 全判据）
- `core/harness/evaluator.go:166-176`（`stale` / `sourceStale`）
- `core/telemetry/freshness.go:9-24`（`ClockSkewAllowance` / `DefaultMaxAge`）
- `core/telemetry/freshness.go:26-62`（`sourceBudget`）
- `core/telemetry/freshness.go:64-106`（`EvidenceFreshness`）
- `core/taskgraph/state.go:5-24`（18 个 Task 状态）
- `core/taskgraph/state.go:26-44`（转换表 / `CanTransition`）
- `core/skills/manifest.go:30-34`（`MutatesWorld` 语义）
- `core/skills/manifest.go:44-72`（`Validate`，含「世界变更必须声明副作用」）
- `core/guard/guard.go:12-20`（Guard 层的 7 个错误变量）
- `core/guard/guard.go:36-75`（`Validate`：租约/期限/审批/幂等键/接地置信度）
- `core/agentcontract/memory.go:105-119`（`StepRecord` 与 `Reconciled` 的语义）
- `core/agentcontract/memory.go:121-131`（`Execution` 端口，含 `Uncertain`）
- `core/agentcontract/payload.go:153-154`（`MutatesWorld` 的 agent 级字段）
- `core/agentcontract/contract.go:76-97`（agent 级 `MutatesWorld` 权限）
- `middleware/contracts.go:60-69`（`StepStatus` 四态）
- `middleware/contracts.go:80-104`（`StepOutcome` 三值 + `Valid`）
- `middleware/contracts.go:106-123`（`StepReconciliation`）
- `middleware/contracts.go:125-134`（`StepRun.Reconciled`）
- `middleware/contracts.go:136-144`（`ReconciliationStore`）
- `middleware/contracts.go:152-154`（`ExecutionStore` 三方法）
- `middleware/sqlite/store.go:202-219`（`MarkStepStarted`）
- `middleware/sqlite/store.go:220-244`（`ReconcileStep` 与 `WHERE reconcile_outcome = ''`）
- `edge/agent/runner.go:28-40`（执行侧错误变量）
- `edge/agent/runner.go:170-175`（`RunControl.ObservationAttempt`）
- `edge/agent/runner.go:177-221`（`ExecutionHistory` / `ReconcileStep` / `CheckRecovery` / `IsReadOnlyCapability`）
- `edge/agent/runner.go:318-345`（续跑绑定校验，接受 `CONFIRMED` / `AWAITING_EVIDENCE`）
- `edge/agent/runner.go:407-522`（单步执行的完整顺序，§4.1）
- `edge/agent/runner.go:525-531`（`preflightFailure`）
- `edge/agent/runner.go:653-704`（`closureContext` / `declaration` / `mutatesWorldSkill` / `loadClosureContext`）
- `edge/agent/runner.go:706-777`（`closureEvidence` / `evidenceFromSnapshot`）
- `edge/agent/agent.go:160-205`（`outcomeOf` / `reasonCodeFor`）
- `edge/agent/agent.go:70-76`（执行 agent 的 `Permissions()`）
- `edge/worker/worker.go:325-340`（发布 `AWAITING_EVIDENCE` 活动状态）
- `internal/actionloop/loop.go:1-31`（包文档：为什么需要这个循环）
- `internal/actionloop/loop.go:45-83`（`Tool` / `Result`）
- `internal/actionloop/loop.go:440-520`（轮次主循环与范围/审批门禁）
- `internal/actionloop/loop.go:494-501`（`callUnknownOutcome` 终止一切）
- `internal/actionloop/loop.go:523-611`（`callVerdict` 与 `call`）
- `internal/actionloop/loop.go:620-635`（`needsApproval` / `needsScope`）
- `internal/localapp/recovery.go:13-22`（`ReconcileStep`：唯一写入者）
- `internal/localapp/recovery.go:24-83`（`Recovery` 视图与 `CanResume`）
- `internal/localapp/recovery.go:85-101`（`checkRevisionRecovery`：禁止切版绕过）
- `internal/recoveryexec/tools.go:59`（`EvidenceSource` 类型）
- `internal/recoveryexec/tools.go:285-316`（`EvidenceFromSnapshot`）
- `cmd/local-agent/main.go:341-350`（服务型动作的证据来源）
- `cmd/local-agent/main.go:351-395`（`ReadHistory` = 对账的具体实现）
- `agentruntime/opsrules.go:12-47`（8 个异常码）
- `agentruntime/opsrules.go:52-70`（严重度与两个默认预算）
- `agentruntime/opsrules.go:137-139`（`UncertainSteps` 输入）
- `agentruntime/opsrules.go:310-374`（`uncertainMutationFindings`）
- `agentruntime/opsrules.go:376-441`（`failedActionFindings` 与 `switch class` 建议表）
- `agentruntime/opsagent.go:319-330`（组装 `ObservationInput`）
- `agentruntime/opsagent.go:362-379`（`uncertainSteps`：读不到就是「说不出来」）
- `agentruntime/memory.go:236-262`（`ExecutionMemory.Uncertain`）
- `agentruntime/memory.go:265-278`（`stepRecordFromRun`）
- `agentruntime/recoveryagent.go:85-92`（`UncertainSteps` 为何是决定性的）
- `agentruntime/recoveryagent.go:489-502`（`readOnlyMatches`）
- `agentruntime/recoveryagent.go:505-590`（`choosePlan` 的对账优先分支）
- `agentruntime/recoverycatalog.go:31-40`（三个风险级）
- `agentruntime/recoverycatalog.go:120-225`（完整目录，含 `task.retry-step` 与三条 never-automatic）
- `agentruntime/recoverycatalog.go:335-358`（`RequiresApproval` / `Executable`）
- `agentruntime/permission.go:56-59, 97, 121, 170`（agent 级 `MutatesWorld`，与 §1.2 不同层）
- `agentruntime/faultmatrix_test.go:99-284`（14 场景故障矩阵）
- `agentruntime/faultmatrix_test.go:455-`（`TestUnknownOutcomeForbidsRetryAndClearsOnReconciliation`）

### Python 源码

- `robot/gateway/tangying_robot_gateway/grounded/verifier.py:1`（三值 GCL 解释器自述）
- `robot/gateway/tangying_robot_gateway/grounded/verifier.py:34-49`（`combine` 强 Kleene）
- `robot/gateway/tangying_robot_gateway/grounded/runtime.py:1`（「已准入执行边界之内、终态成功之前」）
- `robot/gateway/tangying_robot_gateway/grounded/runtime.py:28-32`（`from_environment` 开关）
- `robot/gateway/tangying_robot_gateway/grounded/runtime.py:44-57`（`_collect`：传感器失败不提升为事实）
- `robot/gateway/tangying_robot_gateway/grounded/runtime.py:100-128`（屏障 / `NOT_DISPATCHED` / 空后置合约拒绝）
- `robot/gateway/tangying_robot_gateway/grounded/runtime.py:212-259`（超时/异常 → UNKNOWN_OUTCOME 与屏障释放条件）
- `robot/gateway/tangying_robot_gateway/tool_layer.py:49-63`（`RecoveryClass` 镜像 Go 的 `Class`）
- `robot/gateway/tangying_robot_gateway/tool_layer.py:64-115`（`_RUNTIME_CODE_TABLE`）
- `robot/gateway/tangying_robot_gateway/assets/grounded-contracts.json`（三个核心合约）
- `robot/ros2_ws/src/tangying_navigation/tangying_navigation/gazebo_runtime_node.py:471-475, 554-567`（ROS 侧开关与 `GVF_CONTRACT_REQUIRED`）

### 文档

- `docs/architecture/lifecycle-objects.md:103-140`（断网场景与三对象状态表）
- `docs/architecture/lifecycle-objects.md:140`（事故归档无一手 `uncertainStepIds` 实例的诚实说明）
- `docs/architecture/lifecycle-objects.md:142-173`（三个收拾选项：对账 / 改版 / 取消）
- `docs/architecture/lifecycle-objects.md:175-198`（「工具说成功只是去看世界的触发」）
- `docs/architecture/lifecycle-objects.md:220-244`（十个对象的状态图）
- `docs/architecture/lifecycle-objects.md:272-329`（Step 与 Tool 对象；含已删除的 `Track` 状态机，见 §9-2）
- `docs/architecture/lifecycle-objects.md:611`（「七类分类」口径的另一处出现）
- `docs/architecture/supervision-verification.md:14-57`（14 场景故障矩阵与「七类」标题冲突）
- `docs/architecture/supervision-verification.md:298`（「七类 + `UNKNOWN_OUTCOME` 兜底」，口径差异的根据）
- `docs/architecture/supervision-verification.md:61-88`（故障→诊断→账本→回放链）
- `docs/architecture/supervision-verification.md:92-121`（重启盲区与三层修复；`Abnormal` 定义）
- `docs/architecture/supervision-verification.md:125-133`（计数进入异常身份）
- `docs/architecture/supervision-verification.md:137-199`（真实 sim 实测与三条限制）
- `docs/architecture/supervision-verification.md:201-239`(另两个检出盲区与分类表缺项)
- `docs/architecture/agent-events.md:1-80`（topic 词表与 `ops.anomaly_detected` payload）
- `docs/development/single-robot-loop.md:257-280`（本轮证据登记表，含 409 `PHYSICAL_OUTCOME_UNKNOWN`）
- `docs/development/single-robot-loop.md:282-293`（GVF 开关、限制与拒绝派发清单）
- `docs/development/single-robot-loop.md:295-325`（实验/回放/LLM 对照操作）
- `docs/development/single-robot-loop.md:327-351`（合约定义、屏障语义）
- `docs/development/observation-evidence.md:1-68`（证据 schema `evidence.capture.v1`、保留策略、HTTP 接口）
- `docs/development/2026-09-17-supervision-blind-spots.md:145-190`（83 码审计、58 缺失、`GOAL_NOT_CLEAR` 危害）
- `docs/development/2026-09-17-supervision-blind-spots.md:195-215`（修好后 5 条真实告警）
- `docs/development/home-scene-expansion-plan.md:141-165`（「搬一个报成功」实测案例）
- `docs/development/2026-09-16-faults-as-observations.md:1-105`（故障作为观测、`episodes` 计数单位）
- `docs/experiments/2026-09-21-grounded-verification.md:4-24`（范围限定与九组设置）
- `docs/experiments/2026-09-21-grounded-verification.md:36-64`（结果两表与共同物理轨迹 38.00%）
- `docs/experiments/2026-09-21-grounded-verification.md:79-128`（配对统计与三条边界结论）
- `docs/experiments/2026-09-21-grounded-verification.md:131-165`（九条失败案例与证据哈希）
- `docs/experiments/2026-09-21-grounded-verification.md:175-189`（局限与结论）
- `docs/experiments/2026-09-21-grounded-verification.md:191-233`（支持/不支持假设、错误来源、已修复问题、验收与复现记录）
- `docs/experiments/2026-09-15-slam-exploration-coverage-upgrade.md:29-82`（38.9% → 81%，SLAM 覆盖，非伪成功）
- `docs/experiments/README.md:47`（38.9% → 81% 的索引条目，确认归属）
- `docs/superpowers/specs/2026-09-10-closed-loop-semantic-upgrade-adr.md:141-190`（ADR-10：现状校正、格式取舍、九个谓词、三值语义、失败分类映射、报告边界）
