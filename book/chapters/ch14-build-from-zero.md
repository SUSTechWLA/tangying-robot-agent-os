# 第 14 章 从零搭建一套分布式机器人 Agent 系统

> **版本口径**：本章包含 v0.6.0/v0.7.0 演进案例。代码片段、计数与实验按原时点解释；出版复核修正论证，不表示历史缺口均为当前状态。当前云边能力见第17章，来源与证据边界见出版说明。

> **本章是一张施工图。**
>
> 它按**阶段**组织，每个阶段有：**你要建什么、为什么必须先建它、以及"这一步做完了怎么证明"**。
>
> **顺序不是建议，是约束**——后面的阶段依赖前面阶段提供的东西。
> 而每一个"怎么证明"都是一个**会失败的检查**，不是一个声明。

---

## 14.0 三条施工原则

在动手之前，先接受三条原则。它们来自前面十五章的全部教训。

### 原则 1：先建"什么算完成"，再建"怎么完成"

这是整本书最重要的一条。

绝大多数机器人 Demo 的构建顺序是：

```
工具 → 能调通 → 加个完成判定 → 上线
```

**正确的顺序是反的**：

```
完成判定（契约）→ 工具（必须满足契约）→ 执行 → 上线
```

**为什么？** 因为"完成判定"决定了工具的**接口形状**。先写工具，你会发现：

- 工具不知道要返回什么证据；
- 工具的返回值里没有观测 ID；
- 你的"完成判定"只能依赖工具的自述——而**那正是第 1 章说要禁止的**。

**判据**：如果你在第 3 阶段才发现"我需要一个观测 ID"，那么第 2 阶段的工具都要改。

### 原则 2：先做 Local，再加 Cloud

第 9 章的那个对照实验证明了一件事：

| 行 | 是物理世界强加的吗 |
| --- | --- |
| 完成判据 / 失败分类 / 证据 / 对账 | ✅ **是**——任何进程数下都需要 |
| 协调 / 世界持久化 / 跨机通知 / 安全档位推导 | ❌ **不是**——只有多机才需要 |

**先做对上面那四行，你才有一个可以被验证的系统。**

**上面四行不对，加上分布式只会让错误更难发现**——因为你会把"完成判定错了"误诊成"分布式一致性问题"。

### 原则 3：每个阶段都要有一个"会失败的检查"

第 2 章那九个缺陷修复有一个共同点：**"验证过把修复撤掉它就会红"出现了三次**。

> **一个从没红过的测试，不是一个测试。**

**所以本章的每个阶段都有一个"怎么证明"——而它是一个命令，不是一个判断。**

---

## 14.1 施工总览：八个阶段（编号 0–7）

```
阶段 0  定义完成 ·············· 契约先行（1–2 天）
阶段 1  单一事实来源 ·········· 工具层与安全标注（3–5 天）
阶段 2  观测与世界 ············ 世界模型 + 三值逻辑（3–5 天）
阶段 3  闭环 ·················· 证据门禁 + 失败分类（3–5 天）
阶段 4  执行 ·················· 运行时 + 审批 + 幂等（5–7 天）
阶段 5  编排与多 Agent ········ 意图 → 计划 → 观察者（5–7 天）
阶段 6  分布式 ················ 多机 + 协调 + fencing（7–10 天）
阶段 7  运维与放行 ············ 可观测 + 评测 + 检查清单（持续）
```

**每个阶段的产出都是下一个阶段的输入**，而"阶段 N 完成"的定义是**它的检查会失败**。

---

## 阶段 0 · 定义完成（1–2 天）

### 你要建什么

**一个纯函数，不依赖任何 I/O。** 它回答一个问题：

> **一次声称成功的物理动作，能不能被记为完成？**

### 为什么先建它

因为它是**唯一一个不允许有例外的地方**。而这个项目把它做成了一个**没有 I/O 的包**，理由写得很清楚：

```go
// Everything here is deterministic and free of I/O so the rules can be tested
// exhaustively, including the rule that an unknown physical outcome is never
// retried automatically.
```

**"把安全规则做成纯函数，你才能穷尽地测试它。"**

一旦它需要读数据库、调网络，你就只能"抽样测试"安全规则——**而安全规则的失效恰恰发生在你没想到的那个分支上**。

### 最小实现

```go
// contract/closure.go —— 只有三个输入，一个输出，零 I/O

type Declaration struct {
    Manifest            bool   // 本地可信目录说这个工具改世界
    RuntimeMutatesWorld bool   // 已连接的 runtime 自述这个工具改世界
}

func (d Declaration) Mutates() bool {
    return d.Manifest || d.RuntimeMutatesWorld
}

type Evidence struct {
    ObservationID string
    ObservedAt    time.Time   // ★ 传感器采集时间，不是接收时间
    Freshness     string      // "FRESH" / "STALE" / "UNKNOWN"
}

type Decision struct {
    Satisfied bool
    Required  bool
    Reason    string   // ★ 稳定标识符，不是散文
    Message   string   // 给人看的
}

func Gate(decl Declaration, dispatchedAt time.Time, ev *Evidence) Decision
```

**四个必须做对的地方**：

| # | 要点 | 为什么 |
| --- | --- | --- |
| 1 | **判定顺序不可交换**：先问"要不要证据"，再问"知不知道下发时刻"，然后才问"证据有没有" | 否则一个连下发时刻都不知道的命令，会因为带了一份"看起来很新"的观测而通过 |
| 2 | **`Reason` 是稳定标识符** | 让调用方能分类和展示，**不用解析散文** |
| 3 | **缺 ID 和缺时间报 `ErrEvidenceRequired`，早于下发报 `ErrEvidenceStale`** | "你没有证据"和"你的证据不是这次命令的"是两种诊断 |
| 4 | **时间比较用整毫秒截断** | 当前实现接受整毫秒同桶；这只是兼容策略，因果证明还须命令关联、采集序列与校时预算 |

### 怎么证明这一步做完了

**写三个断言**：

```go
// ① 只读工具不需要证据
Gate(Declaration{}, time.Time{}, nil)
// → {Satisfied: true, Required: false, Reason: "CLOSURE_NOT_REQUIRED"}

// ② 改世界的工具，一次成功的返回不足以完成
Gate(Declaration{Manifest: true}, dispatch, nil)
// → {Satisfied: false, Reason: "CLOSURE_EVIDENCE_REQUIRED"}

// ③ 有观测也救不了：它早于命令
Gate(Declaration{Manifest: true}, dispatch,
     &Evidence{ObservationID: "obs-before",
               ObservedAt: dispatch.Add(-time.Second), Freshness: "FRESH"})
// → {Satisfied: false, Reason: "CLOSURE_EVIDENCE_STALE"}
```

**第四个断言（边界）**：

```
命令在 …000400 下发，观测在 …000000
→ 通过（同一毫秒）

命令在 …000400 下发，观测在 前一毫秒
→ 拒绝
```

**再加一条**：**知道下发时刻但证据为 nil ≠ 不知道下发时刻**——查 `Reason` 是否不同。

> **验收命令**：`go test ./contract/... -run TestGate`
>
> **要求**：四个断言全部存在，且**把第 1 条判定顺序换一下，测试会红**。

---

## 阶段 1 · 单一事实来源（3–5 天）

### 你要建什么

**一份工具目录，以及它是生成物而不是手写物。**

### 为什么

第 5 章那个发现值得重复一次：项目里的 `tools.json` **从代码生成**，`--check` 断言磁盘文件与生成结果**逐字节相同**。

**后果**：

> **同源生成降低目录漂移；跨版本、Go 目录及 Runtime 能力仍须校验。**

**手写的目录只有一个归宿：腐烂。**

### 最小实现

```
tools/
  layer.py        # RobotTool 定义（dataclass, frozen）
  perception.py   # capture_image / detect_object / ...
  navigation.py   # navigate_to / ...
  manipulation.py # pick_object / place_object / ...
  __init__.py     # build_registry()
generate_catalog.py   # --write / --check
tools.json            # ★ 生成物，进版本库
```

**`RobotTool` 的字段（缺一个都会在后面出问题）**：

| 字段 | 语义 | 为什么要它 |
| --- | --- | --- |
| `name` | 蛇形，**禁止含点号** | 点号在 function calling schema 里有歧义 |
| `description` | **何时用 + 前置条件 + 恢复路径** | 模型只看到这个 |
| `parameters_schema` | JSON Schema，**全部 `additionalProperties: false`** | ← 这一条挡住"模型自带批准" |
| `safety_level` | **0–4，可数值比较** | 让"不超过某一级"成为不等式 |
| `timeout_s` | 必须为正 | 见下 |
| `idempotent` | 决定能否自动重试 | 多数物理工具**不幂等** |
| **`mutates_world`** | 为真时**禁止重试**且必须过证据门 | ★ 核心字段 |
| `llm_visibility` | `primary` / `fallback` | 坐标级接口不暴露给模型 |

**五级安全标注（`int` 的子类，唯一目的是可比较）**：

```
0  QUERY              查询：不改变任何状态
1  LOW_SPEED_MOTION   低速运动
2  NORMAL_MOTION      常规运动
3  CONTACT            接触物体
4  SAFETY             安全相关
```

**七处强制点（从定义期到执行期）**：

| # | 阶段 | 强制 |
| --- | --- | --- |
| 1 | **构造期** | `0 ≤ safety_level ≤ 4`；`timeout_s > 0`；`llm_visibility` 合法 |
| 2 | **注册期** | 同名重复注册直接报错，**不允许静默覆盖** |
| 3 | **选择期** | `max_safety_level` 过滤 |
| 4 | **清单校验** | `physical_motion` 必须同时有 `SideEffect=true` + `DefaultLeaseMS != 0` + `AllowedSafetyProfiles` 非空 |
| 5 | **规划校验** | 物理步骤缺 lease / 缺审批 / 缺幂等键，各自独立报错 |
| 6 | **审批派生** | `needsApproval` **从清单的安全级别派生，不询问工具自己** |
| 7 | **执行门禁** | 物理动作无审批 → `APPROVAL_REQUIRED` |

**第 4 条里有一个必须实现的硬错误**：

```
MutatesWorld && !SideEffect  →  硬错误
```

理由：

> 那会**告诉调用方不需要确认，而闭环门禁却在等一份永远不会到的证据**。

**两个属性分别看都对，合起来自相矛盾** —— 而系统会**永远等待一份永远不会来的证据**。

### 怎么证明这一步做完了

```bash
# ① 目录与代码一致（逐字节）
python generate_catalog.py --check
# → tools.json is up to date (N tools)

# ② 五个字段在构造期被校验
pytest tests/test_tool_contract.py -k "safety_level or timeout or visibility"
# 应当含一个 {"safety_level": 9} 的拒绝用例

# ③ 同名重复注册被拒
# ④ additionalProperties=false 真的挡住夹带
pytest tests/test_execution_admission.py
# 关键断言不只是"被拒绝"，还有 "handler 未被调用"

# ⑤ 物理工具的数量与清单
python -c "import json;d=json.load(open('tools.json'));print(len(d['tools']),len(d['metadata']),d['excluded_from_llm'])"
```

> **`metadata` 数 > `tools` 数，差额等于 `excluded_from_llm` 的长度。**
>
> **这个恒等式是理解"哪些工具不给模型"的关键。**

**再加一条**：

> **给一个工具加上 `approval_id` 参数，`--check` 与 schema 测试应当红。**

---

## 阶段 2 · 观测与世界（3–5 天）

### 你要建什么

**一个世界模型，以及它里面的三值逻辑。**

### 为什么

因为**没有一个带证据引用的世界模型，"完成判定"就无处落地**。

阶段 0 的 `Gate` 需要一份 `Evidence`。而那份证据必须能回答：

| 问题 | 需要的字段 |
| --- | --- |
| 这次采集是新的吗？ | `ObservedAt` |
| 它来自哪个源？ | `SourceID` |
| 这个源的序列前进过吗？ | `SourceSequence` |
| 它来自正确的坐标系吗？ | `TransformRevision` |
| 它还有效吗？ | `Freshness` |

**所以世界模型里每一条事实都必须带一个证据指针。**

### 最小实现

```python
class EvidenceRef:
    observation_id: str
    source_id: str
    source_sequence: int
    observed_at: datetime
    frame_id: str
    transform_revision: str

class EntityState:
    entity_id: str
    category: str
    attributes: dict[str, str]
    pose: list[float]
    relations: dict[str, str]        # {"inside": "kitchen-tray"}
    confidence: float
    freshness: str                   # FRESH / STALE / UNKNOWN / DEGRADED
    observation_count: int
    stable_observations: int
    evidence: EvidenceRef            # ★

class RobotState:
    robot_id: str
    pose: list[float]
    held: str                        # ★ 实体 ID，不是布尔
    emergency_stopped: bool
    faults: FaultReport | None
    freshness: str
    evidence: EvidenceRef

class Snapshot:
    schema_version: str
    world_id: str
    revision: int                    # ★ 只对"真变更"递增
    event_cursor: str
    robots: dict[str, RobotState]
    entities: dict[str, EntityState]
    resources: dict[str, ResourceState]
    sources: dict[str, SourceState]
```

**五个必须做对的地方**：

| # | 要点 | 为什么 |
| --- | --- | --- |
| 1 | **每条事实带 `EvidenceRef`** | 后续所有"相信它"的地方都要检查新鲜度与坐标系 |
| 2 | **`Held` 放实体 ID，不是布尔** | 布尔无法回答"它抓着**哪个**" |
| 3 | **`Revision` 只对真变更递增** | 它是"世界变过几次"，不是"收到过几条消息" |
| 4 | **投影器是纯函数式 reducer，四道入口过滤** | 见下 |
| 5 | **谓词返回三值 + `Reason`** | `UNKNOWN` **不是** `FALSE` |

**投影器的四道闸（顺序固定）**：

```
worldID 不匹配                    → 报错
observationID 已见                → 静默丢弃（重复投递）
SourceSequence <= 高水位          → 静默丢弃（乱序/重放）
TransformRevision 冲突            → 报错
静态实体无变化                    → 只推进私有高水位，不推进 Revision
```

**为什么前两"静默丢弃"而后两"报错"？**

| 情况 | 性质 |
| --- | --- |
| 重复投递、乱序 | **正常现象**——网络就是这样的 |
| 世界 ID 不符、坐标系冲突 | **配置错误**——必须让人知道 |

**三值逻辑的判据**：

```python
def EntityInside(entity_id, zone_id, max_age) -> Verdict:
    """返回 TRUE / FALSE / UNKNOWN，且必须带 Reason"""
    if entity not in snapshot.entities:
        return UNKNOWN, "ENTITY_UNKNOWN"
    ev = snapshot.entities[entity_id].evidence
    if ev.observed_at is None:
        return UNKNOWN, "EVIDENCE_HAS_NO_TIME"
    if max_age <= 0:
        return UNKNOWN, "NO_VALID_WINDOW"
    if now - ev.observed_at > max_age:
        return UNKNOWN, "EVIDENCE_STALE"
    if snapshot.entities[entity_id].relations.get("inside") != zone_id:
        return FALSE, "RELATION_MISMATCH"
    return TRUE, ""
```

**四条判据的顺序**：实体不在 → 时间缺失 → 窗口无效 → **超龄** → 关系不符。

**前四条都是 `UNKNOWN`，只有最后一条是 `FALSE`。**

### 怎么证明这一步做完了

```bash
# ① 四道闸
pytest tests/test_projector.py
# 必须含：重复 observationID 被丢、序列倒退被丢、坐标系冲突报错

# ② Revision 只对真变更递增
# 连续投两次"同一个静态实体、无变化"的观测 → revision 不变

# ③ 三值逻辑
pytest tests/test_predicate.py
# 必须含：实体缺失 → UNKNOWN（不是 FALSE）；证据超龄 → UNKNOWN（不是 FALSE）
```

**最关键的一条断言**：

> **`EntityInside` 在证据超龄时返回 `UNKNOWN`，而 `Verdict` 的布尔化会抛异常或返回 None——绝不能默默变成 `False`。**

**为什么这条是关键？** 因为 `UNKNOWN` 被当成 `FALSE` 的那一天，你的系统就会开始**重放一个可能已经成功的物理动作**。

---

## 阶段 3 · 闭环（3–5 天）

### 你要建什么

**失败分类表，以及"结果未知禁止重试"的完整链路。**

### 为什么

阶段 0 回答了"这次成功算不算数"。阶段 3 回答另一个问题：

> **这次失败，唯一安全的下一步是什么？**

**而这两个问题的答案都不能由发起动作的那一方给出。**

### 最小实现：八个类

```python
class FailureClass(str, Enum):
    TRANSIENT       = "TRANSIENT"        # 依赖恢复后重发同一条命令
    PERCEPTION      = "PERCEPTION"       # 重新观测/搜索，然后重试
    PLANNING        = "PLANNING"         # 换目标或换路径
    PERMISSION      = "PERMISSION"       # 人或更高层改变某个东西
    RESOURCE        = "RESOURCE"         # 等待/获取资源所有权
    VALIDATION      = "VALIDATION"       # 改请求，不是重试
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"  # ★ 先对账，禁止自动重试
    FATAL           = "FATAL"            # 人工判断
```

**每一句注释都应该以"唯一安全的下一步"来写，不以"错误的原因"来写。**

**这是这张表最重要的设计特征。**

### 兜底方向：未识别 → `UNKNOWN_OUTCOME`

```python
def classify(code: str) -> FailureClass:
    normalized = (code or "").strip().upper()
    if not normalized:
        return FailureClass.UNKNOWN_OUTCOME
    for cls, codes in CLASSIFICATION:      # ★ 有序，第一条命中获胜
        if normalized in codes:
            return cls
    return FailureClass.UNKNOWN_OUTCOME    # ★ 保守兜底
```

**这个兜底方向是本章最重要的一个取舍。**

| 兜底方向 | 猜对 | 猜错 |
| --- | --- | --- |
| 猜"可重试"（乐观） | 省一次人工干预 | **重复一次可能已经生效的物理动作** |
| **猜"不可重试"（保守）** | 安全 | 多一次人工确认（成本：一次点击） |

**隔离环境中重跑纯测试通常风险较低，但仍有资源成本；部署、迁移和网络写操作同样可能结果未知**。

### 第二个函数：`knows`

```python
def knows(code: str) -> bool:
    """区分「表里列为 UNKNOWN_OUTCOME」与「表里根本没有」"""
```

**为什么需要它？**

```python
classify("EXECUTION_OUTCOME_UNKNOWN")  == UNKNOWN_OUTCOME   # 表里列了
classify("A_CODE_NOBODY_HAS_SEEN")     == UNKNOWN_OUTCOME   # 表里没有
```

**同返回值，但它们是两个不同的事实**：

| | 含义 |
| --- | --- |
| 表里列了 | **系统理解这个失败，并决定它不可恢复** |
| 表里没有 | **没有人分类过这个失败** |

**把后者报成前者，就是把一个缺失的表项藏在看起来像安全决策的结果后面。**

### 两道守卫

**守卫 1：表项完整性**

```
一份提交进仓库的清单 runtimeEmittedCodes
  + 一个测试 TestEveryCodeTheRuntimeCanEmitIsClassified
```

**为什么清单是人工维护的？** 这个项目的理由是：

> **"对两种语言做正则会是第二件会出错的事。"**

**人工清单的失效是可见的（守卫会红）；自动抓取的失效是静默的（正则没匹配到）。**

**守卫 2：把危险本身钉住**

```python
def test_a_code_the_table_misses_falls_back_to_unknown_outcome():
    assert classify("SOME_NEW_CODE") == UNKNOWN_OUTCOME
```

**它断言的是"错误的输入会导致什么"，而不是"正确的行为"。**

（大多数测试断言后者。**而这一个把危险的当前形态记录在测试里**。）

### 「结果未知禁止重试」的五个落点

**这是本阶段的核心。** 你必须实现**五个独立的位置**——任何一个缺失，防护就漏了：

| # | 落点 | 强制方式 |
| --- | --- | --- |
| 1 | **执行器不推进状态** | 保持 `STARTED`，返回 `ErrUnverifiedWorldMutation` |
| 2 | **模型驱动的循环终止一切** | `callUnknownOutcome` 直接 return |
| 3 | **续跑被拒** | `CanResume` 里含 `!RequiresReconciliation` |
| 4 | **恢复计划只有只读动作** | 分支内**不咨询模型**；只读硬过滤 |
| 5 | **面向人的建议带禁令** | `severity=critical` + `AutomaticRetryForbidden=true` |

**第 1 个落点的代码形状**：

```python
# 调用之前
dispatched_at = now()                      # ★ freshness floor
store.mark_step_started(record)            # ★ 先落盘 STARTED

result = invoke(command)

if not result.success:
    if preflight_failure(skill, result.code):
        store.mark_step_failed(record)     # ★ 唯一的 FAILED 出口
    # ★ 否则保持 STARTED —— 故意不推进

evidence = collect_closure_evidence()
decision = gate(declaration, dispatched_at, evidence)
if decision.require() is not None:
    # ★★ 故意不推进状态
    return ErrUnverifiedWorldMutation
store.mark_step_completed(record)
```

**那个 `preflight_failure` 白名单必须极小**：

```python
def preflight_failure(skill, code) -> bool:
    return code == "ROBOT_COMMISSIONING_ACTIVE" or \
           (skill == "navigation.navigate" and code == "NAV_MAP_NOT_READY")
```

**只有这两种失败能被记为 `FAILED`**——因为只有这两种能确定"命令在任何物理效果被授权之前就被拒绝了"。

**其他所有失败，状态都停在 `STARTED`。**

### 第 4 个落点：结构性排除

```python
if facts.uncertain_steps or finding.automatic_retry_forbidden:
    look_first = read_only_matches(finding)     # ★ 只返回 read_only
    if look_first:
        return RecoveryPlan(
            diagnosis="有物理动作结果未知；先重新观测完成对账，对账之前不改动机器人",
            steps=steps_from(look_first),
        )
    return RecoveryPlan(diagnosis="…", escalate=True)
```

**注意这一支里根本不构造模型请求。** 理由：

> **排除是结构性的，而不是对一份已经包含了变更动作的计划做过滤。**

**"结构性排除"和"事后过滤"是安全设计里的一条分水岭**：事后过滤意味着变更动作**曾经出现在模型的输出里**。

### `Uncertain` 从磁盘派生

```python
def uncertain(task_id) -> list[StepRecord]:
    result = []
    for step in steps(task_id):
        if step.reconciled:          # ★ 人已经看过了
            continue
        if step.status == "STARTED":
            result.append(step)
    return result
```

**两个设计点**：

| 点 | 理由 |
| --- | --- |
| **`reconciled` 的不再报** | "继续报就是安全信号退化成噪音"——**一个从不消失的告警会被忽略** |
| **存储不可读时返回 `None`，不是 `[]`** | **"读不到" ≠ "没有"** |

**第二个点是类型设计**：`None` 和 `[]` 必须是**两个不同的值**。

### 对账：只有人能解除

```sql
UPDATE step_runs
   SET reconcile_outcome = ?, reconcile_actor = ?, reconcile_note = ?
 WHERE task_id = ? AND step_id = ?
   AND reconcile_outcome = ''          -- ★ 只能从空到非空
```

**如果不是非零行数 → 报错 "not awaiting reconciliation, or has already been reconciled"**。

**并且要求 `actor` 与 `note` 都非空**：

> 让人继续往下走的那个判断，是**后来的人必须能反驳**的那个判断。

### 怎么证明这一步做完了

```bash
# ① 八类的完整性与兜底方向
pytest tests/test_classification.py
# 必须含：未识别的码 → UNKNOWN_OUTCOME；只有 TRANSIENT/PERCEPTION 可重试

# ② knows 的区分
assert classify("EXECUTION_OUTCOME_UNKNOWN") == classify("NOBODY_SAW_THIS")
assert knows("EXECUTION_OUTCOME_UNKNOWN") is True
assert knows("NOBODY_SAW_THIS") is False
# ★ 第 2、3 行的对比是这个测试的全部价值

# ③ 五个落点各有一个测试
pytest tests/test_unknown_outcome.py
# - 执行器在证据缺失时保持 STARTED
# - 循环在 callUnknownOutcome 时 return
# - CanResume 在 RequiresReconciliation 时为 False
# - 恢复计划只有只读动作（且不调用模型）
# - 建议带 AutomaticRetryForbidden

# ④ 对账是一次性的
# 连续两次对账同一个步骤 → 第二次报错
```

**最关键的一条**：

> **把五个落点中的任意一个删掉，必须有测试变红。**

**如果删掉一个而测试全绿，那说明那一个落点是"没通电"的。**

---

## 阶段 4 · 执行（5–7 天）

### 你要建什么

**一条唯一的执行通道，一个唯一的安全否决点，以及幂等。**

### 为什么

阶段 3 定义了"什么算完成"。阶段 4 要保证**所有动作都走同一条路**——包括安全检查和幂等去重。

### 最小实现：九步调用链

```
① 模型 function calling
      ▼
② Executor.execute(call)
      ├─ 未注册 → NOT_FOUND（含 known_tools 列表）
      ├─ schema 校验（additionalProperties=false）
      ├─ 节点不健康 → UNREACHABLE（emergency_stop 除外）
      ├─ timeout = min(调用方, 工具)
      ├─ 预取消 → CANCELLED（emergency_stop 除外）
      └─ mutates_world → 取全局运动锁，取不到即 BUSY
      ▼
③ 重试预算（★ 在构造命令之前决定）
      ▼
④ tool.execute(**args) → handler
      ▼
⑤ adapter.execute(...) → 构造 Command（19 字段）
      ▼
⑥ Runtime.invoke(command)
      ▼
⑦ 执行入口
      ├─ emergency_stop 分支：抢在任何在途命令之前
      ├─ 每机器人执行锁非阻塞获取
      ├─ journal 幂等查表（★ 在安全准入之前）
      └─ decision = safety.start(command)   ← ★ 最终软件安全准入点
      ▼
⑧ SafetySupervisor.evaluate（13 项检查）
      ▼
⑨ backend.execute → 硬件 / 仿真
```

### 三个顺序细节

**细节 1：第 ③ 步的重试在第 ⑤ 步之前决定。**

含义是"**重试的是工具调用，不是命令**"。

**细节 2：第 ⑦ 步的幂等 journal 在第 ⑧ 步的安全准入之前查表。**

理由：重复命令读取既有 journal 结果，避免重新执行。读取历史结果不授权新动作，也不证明当前物理状态；未知记录需要对账，新命令仍须当前安全准入。

**细节 3：第 ⑧ 步汇总 Runtime 安全准入。**

**多层拒绝是纵深防御**：schema、健康、锁、Guard 和 SafetySupervisor 各自负责不同约束，统一记录拒绝原因，不能用单一检查替代整个链路或硬件联锁。

### `Command` 的 19 个字段

```
SchemaVersion      CommandID          TaskID            RobotID
Capability         TargetRef          Parameters        Deadline
Lease              IdempotencyKey     SafetyProfile     ApprovalID
CatalogRevision    WorldRevisionBasis ResourceID        FencingToken
TaskRevision       AggregateVersion   StepID
```

**19 个字段，没有一个叫 `origin` / `brain_id` / `source_plane` / `created_by`。**

**这不是"不检查来源"，是"类型层面无法表达来源"。**

**要违反它，你必须先改协议。**

### 安全监督的 13 项检查

```
CANCELLED → ROBOT_COMMISSIONING_ACTIVE → SCHEMA_VERSION_UNSUPPORTED
→ COMMAND_EXPIRED (deadline) → LEASE_REQUIRED (lease_ms == 0)
→ IDEMPOTENCY_KEY_REQUIRED → ROBOT_ID_MISMATCH → TOOL_CATALOG_STALE
→ TOOL_CATALOG_REVISION_REQUIRED / FENCING_TOKEN_REQUIRED
→ FENCING_TOKEN_STALE → SAFETY_PROFILE_REJECTED → EMERGENCY_STOP_LATCHED
→ **physical && !approval_id → APPROVAL_REQUIRED**
→ 参数校验 → ALLOWED
```

**13 项全部通过才进执行。**

### 幂等的两个层次

**层次 1：步骤级的部分唯一索引**

```sql
CREATE UNIQUE INDEX step_runs_idempotency_idx
  ON step_runs (idempotency_key)
  WHERE idempotency_key <> '';      -- ★ 部分索引
```

**"部分"是关键**：空键意味着"这一步还没有幂等键"，这种行**可以有多条**。

**而这把"步骤幂等"这个业务不变量变成了一条数据库约束。**

**层次 2：运行时的 journal 四态**

```
conflict / pending / reconciled / replay
```

### 审批的四个结构事实

| # | 事实 |
| --- | --- |
| 1 | 建出的任务 `state = READY`，**`approved` 取零值 `false`** |
| 2 | 第 1 版 revision 硬编码 `ApprovalRequired: true, RiskClass: "physical"` |
| 3 | **`READY` 状态没有到 `EXECUTING` 的转移** ← ★ 最强的一条 |
| 4 | 全仓库**唯一**写 `approved = true` 的地方在 `Approve()` 内部 |

**第 3 条是最强的保证**：它不是"审批检查失败就拒绝"，而是"**状态机里根本没有那条边**"。

> **一个未审批的任务在结构上无法到达执行状态。**

### 急停的特权

**`emergency_stop` 有四个特权，每一个都必须实现**：

| # | 特权 |
| --- | --- |
| 1 | 节点不健康时**不移除**它 |
| 2 | 预取消时**不拦**它 |
| 3 | **抢在任何在途命令之前**执行 |
| 4 | `safety` 级故障摘掉**所有**声明了依赖的能力，**唯一幸免的是零依赖的它** |

**原则**：

> **安全机制本身不能被它要保护的机制影响。**

**一个"因为某个子系统不健康而从工具列表里消失"的急停工具，会让模型在最需要停的时候没有停的能力。**

### 怎么证明这一步做完了

```bash
# ① 不新增执行通道（可测量的不变量）
pytest tests/test_proto_schema.py
# 断言 gRPC 服务方法集合恰好是那七个

# ② 模型不能自带批准
pytest tests/test_execution_admission.py -k approval
# ★ 关键断言不只是"被拒绝"，还有 "handler 未被调用"

# ③ 未审批的任务到不了执行状态
pytest tests/test_task_states.py
# 断言 READY 没有到 EXECUTING 的边

# ④ 急停的四个特权
pytest tests/test_emergency_stop.py
# - 节点不健康时仍在 openai_tools() 里
# - 预取消时不拦
# - safety 级故障后仍可用
```

**最具价值的一条**：

> **把 19 个字段里加一个 `origin`，`test_proto_schema.py` 应当红。**

---

## 阶段 5 · 编排与多 Agent（5–7 天）

### 你要建什么

**从一句中文到一份计划，以及"至少两种 Agent"的运行时。**

### 阶段 5.1 三条编排规则

**规则 1：确定性优先，成功不被覆盖。**

```
确定性解析器：对它覆盖的句式是精确的、零成本、不会幻觉
```

**规则 2：解析失败返回原解析错误，不静默降级。**

```
只有"要求澄清"时才把模型错误追加在原错误之后
否则原样返回原错误
```

**理由**：

> **"语法表达不了"和"这句话本身有歧义"曾被当成同一个答案。**

| 诊断 | 正确的行动 |
| --- | --- |
| **语法太窄** | **配模型**（或扩展语法） |
| **这句话有歧义** | **问用户** |

**合并它们 = 把一个可修的问题伪装成一个不可修的问题。**

**规则 3：规划层另有确定性后备。**

**⚠️ 注意一个容易写错的地方**：一个返回空 `Bundle` 的 `DeterministicPlanner` **不是**后备——它不产出步骤。

**真正的确定性计划生产者是领域计划构造器**（`skills/manipulation.Plan`），由运行时调用。

**混淆这两者会得出"后备是空操作"的错误结论。**

### 阶段 5.2 计划必须携带它赖以成立的世界

```python
def plan(request, intent, world) -> Plan:   # ★ 三参数
    ...
```

**理由**：

> **一份计划只对它形成时的那个状态有意义；
> 一个缓存了机器人位置的规划器，会一直为机器人曾经在的地方做计划。**

**这是 coding agent 完全不需要的设计**——文件系统不会在你读它的时候移动。

### 阶段 5.3 提示词写成"带理由的规则"，不是数据字段

```
✅ "机器人不能穿墙。寻找或操作物体的技能只在机器人处于物体所在房间时有效。"
❌ "robot_room: living_room"
```

**理由**：

> **写成字段，模型会当成可选上下文；写成带理由的规则，它才改变计划。**

**而第三轮实验证明了更重要的一条**：

> **语法/格式不是关键，信息完整性才是**（+73.55/+65.51 pp vs 语法 +0.55/+1.17 不显著）。

**所以重点不是"怎么措辞"，而是"这条信息有没有被送到决策者面前"。**

### 阶段 5.4 多 Agent：至少两个，且观察者不能动手

**最小配置**：

| Agent | 权限 | 关键约束 |
| --- | --- | --- |
| **task**（执行者） | `MutatesWorld=true` | **不订阅事件**（它可能很慢，订阅会占队列） |
| **ops**（观察者） | **只读** | **没有任何执行端口** |

**观察者的三条设计**：

| # | 设计 | 为什么 |
| --- | --- | --- |
| 1 | **字段白名单反射测试** | 加任何新字段都会失败——**强制"观察者需不需要这个"被刻意回答** |
| 2 | `Execute` **恒返回 `ErrNotExecutable`** | 它不是一个"当前配置成不执行"的 Agent |
| 3 | `Health()` 在"看不见"时返回 **DEGRADED** | **"一个看不见的观察者不是健康的"** |

**第 1 条的代码形状**：

```python
def test_ops_agent_holds_no_execution_port():
    allowed = {"catalog", "read_facts", "publish", "now", "mu", "findings", "stopped"}
    for name in vars(OpsAgent).keys():
        assert name in allowed, f"OpsAgent has a new field {name!r}"
```

**这一个 for 循环，把"观察者碰不到机器人"从一句注释变成了一个会失败的测试。**

### 阶段 5.5 四类事件的语义分工

```
anomaly       说事实
hypothesis    说结论
proposal      说建议
escalation    说"要人"
```

**理由**：

> **合成一个事件，读者就分不清哪句是观测、哪句是推断。**

### 阶段 5.6 身份必须全系统唯一

```
identity = code + "@" + component
```

**这个项目为此吃过一次亏**：告警存储按 `code@component` 建索引、恢复 Agent 按裸 `code` 归档计划、控制台按裸 `code` 查——

> 结果是一份计划被显示给 **11 个不同的组件失败**，每个都在读一份**诊断里写着另一个组件名字**的计划。

**所以"这个异常是谁"必须只有一个答案。**

**而去重键要在身份后追加数量**：

```python
report_id = identity + "#" + str(count)
```

> **稳定条件不刷屏，恶化的状态必须重播。**

**注意比较时要在 `#` 边界上比**，否则 `CODE@a` 会匹配 `CODE@ab`。

### 怎么证明这一步做完了

```bash
# ① 确定性优先不被覆盖
pytest tests/test_parser.py -k deterministic_first

# ② 失败不降级
pytest tests/test_parser.py -k "clarification or not_degrade"
# 断言：语法错误 + 模型也失败 → 返回的是语法错误，且带模型失败说明

# ③ 观察者无执行端口
pytest tests/test_ops_agent.py -k holds_no_execution_port
# ★ 给 OpsAgent 加一个 robot_client 字段，这个测试必须红

# ④ 三值不会被压成布尔
pytest tests/test_world_predicates.py

# ⑤ 故障矩阵：每个失败类都有一条人能执行的建议
pytest tests/test_fault_matrix.py
# ★ 每个场景都要显式断言 want_retry_forbidden（不只是为真的那些）
```

**最后一条的细节很重要**：

> **每个场景都断言"是否禁止重试"，而不是只在"应该禁止"时断言。**
>
> 因为**危险的回归是一条失败的"禁止重试"悄悄消失**。

---

## 阶段 6 · 分布式（7–10 天）

### 你要建什么

**四种租约、单写协调、单调递增的 fencing token、事件与 Outbox。**

### 阶段 6.1 先问一个筛选题

**在做这一步之前，先回答**：

> **我防的是哪些故障？**

| 故障 | 需要什么机制 |
| --- | --- |
| 两个写者同时改同一个物理对象 | **资源租约 + fencing token** |
| 旧持有者的迟到命令 | **fencing token** |
| 协调器脑裂 | **领导者租约** |
| 进程重启后丢失在途状态 | **事件 + Outbox + 版本 CAS** |
| 队列宕机 | **Outbox（先落盘事实再投递）** |

**如果你防不住任何一条，就不要建对应的机制。**

第 8 章那个教训值得重复：

> README 列了四个资源（地图、位姿、谁抓着什么、充电位），**代码里只有一个真的被 owner + lease + fencing 管住**。
>
> 其余三个：地图融合是**确定性纯函数**（不需要归属）、位姿只有新鲜度（不需要排他）、充电位**根本不存在**。

**加一把不需要的锁，只会增加复杂度和失败模式。**

### 阶段 6.2 四种租约不是一套机制

| 租约 | 资源 ID | 默认 TTL | 过期后果 |
| --- | --- | --- | --- |
| **设备租约** | 每 robot 一条 | **15 s** | 设备判为离线，**拒绝为其分派** |
| **声明租约** | 每 intent 一条 | **2 m** | **不是释放，是进入 `UNKNOWN_OUTCOME`** |
| **资源租约** | `object:red-block` | **2 m** | 世界投影降级；验证拒绝用过期资源确认完成 |
| **领导者租约** | `leader/<worldID>` | **15 s** | 本协调器**所有**变更被拒绝 |

**声明租约不是互斥锁，这是最反直觉的设计**（4.4）。

### 阶段 6.3 fencing token 只在两处递增

```
Acquire  → INCR
Transfer → INCR
Renew / Validate / Release → 不递增
```

**所以 token 是「纪元号（epoch）」，不是「版本号」**——同一次持有期间恒定。

**这正是让"旧持有者的重放"可以被一个整数比较识别的原因。**

**Redis 实现必须用同一个 hash tag**：

```
tangying:fleet:lease:{<tag>}:state
tangying:fleet:lease:{<tag>}:counter
```

**因为 Redis Cluster 只保证同一个槽内的多键操作是原子的。**

### 阶段 6.4 本节最重要的一条：租约过期 ≠ 可以重试

**大多数分布式教材教**：租约过期 → 可以安全地重新获取。

**这套系统明确拒绝这条规则**，理由是**领域性的**：

> 它**曾经**把它们退回 READY，理由是"崩溃或断连的 worker 绝不能永远阻塞任务"。
> 这个理由对 **worker** 是对的，对**机器人**是错的：
> **租约失效告诉我们 worker 停止报告了，不是机器人站着没动。**

### 正确的行为

```python
def reclaim_stale(state):
    for node in state.intents:
        if node.status == RUNNING and now - node.started > claim_lease:
            node.status = UNKNOWN_OUTCOME       # ★ 不是 READY
            node.finished = now
            node.error = "claim lease expired with the outcome unknown; reconcile before acting again"
            # ★ node.claimed 故意保留
```

**`claimed` 故意保留的理由**：

> **"哪个 worker 在租约失效时持有它"是对账要问的第一个问题。**

### 退出这个状态只有一条路

```python
def reconcile_intent(task_id, index, decision, actor, note):
    assert actor and note, "对账必须同时提供人和理由"
    assert decision in ("NEVER_ACTED", "ABANDON")
    if decision == "NEVER_ACTED":
        node.fencing_token = 0     # ★ 清零，确保下次认领拿到严格更大的新 token
```

**而晚到的完成上报必须被"具名拒绝"**：

```python
raise ClaimExpired(
    f"intent {index} held by {node.claimed} since {node.started}; "
    f"reconcile it before reporting a result"
)
```

**为什么必须具名**：

> **「你来晚了，这个已经不能认领」和「机器人可能动过，而且没人知道」需要完全不同的下一步。**

### 阶段 6.5 一次 `Commit` 是一个事务

```
① SELECT version ... FOR UPDATE   ← 取行锁 + CAS 前置检查
② 写状态表
③ 插事件表
④ 插 Outbox
⑤ 更新 checkpoint（版本只增不减）
```

**五件事在一个事务里。**

**为什么"先落盘事实再投递"？三个可验证的后果**：

| # | 后果 |
| --- | --- |
| 1 | **投递失败不丢事实**（条目留在 outbox，下轮重试） |
| 2 | **崩溃点任意，状态一致**（`OutboxClaimTTL = 30s` 让"claim 之后崩溃"的条目自动重新可见） |
| 3 | **不变量可以在提交点检查** |

### 阶段 6.6 幂等键必须是确定性字符串，不是 UUID

```
taskID + "/intent/" + index + "/claim/" + (version+1)
taskID + "/intent/" + index + "/succeeded"
```

**为什么不是 UUID？**

> **同一次逻辑变更重放时键必须相同。**

**UUID 保证"每次生成都不同"——这在需要去重的场景里恰好是错的。**

### 阶段 6.7 去重发生在三处

| # | 位置 | 语义 |
| --- | --- | --- |
| 1 | **数据库唯一键** | **最后兜底**（但几乎不会被触发，因为版本 CAS 会先失败） |
| 2 | **显式查重** | **是错误，不是成功** |
| 3 | **协调器状态检查** | **真正处理重放的地方** |

**第 3 处的形状**：

```python
if node.status == SUCCEEDED and node.claimed == robot_id:
    return snapshot(state)      # 精确重复 → 返回当前快照，不写任何事件
```

### 怎么证明这一步做完了

```bash
# ① 旧 token 写入被拒
pytest tests/test_fencing.py
# 转让后，旧持有者用旧 token 写入 → 拒绝

# ② token 单调递增（即使前一个已过期）
# first = acquire(...)   token=1
# second = acquire(...)  token=2  ← 从 1 继续涨
assert second.token > first.token

# ③ 过期不退回 READY
pytest tests/test_claim_lease.py
# ★ 这条测试的名字应该叫 ...LeavesTheOutcomeUnknownInsteadOfReclaiming

# ④ Outbox 在投递失败后仍可恢复
pytest tests/test_outbox.py

# ⑤ 重启不重复推进
pytest tests/test_restart.py
```

**最强的一条证据（如果你想做的话）**：

```
真的 SIGSTOP 一个 edge worker 进程
→ 断言设备离线
→ 创建并批准一个任务
→ 断言任务没有 SUCCEEDED，且没有"下一步解锁"事件
→ SIGCONT
→ 断言任务最终 SUCCEEDED，且解锁事件恰好一次
```

**这个跨进程故障用例验证一种暂停时序下的事件去重。** 它不证明任意故障下的物理效果恰好一次；还需覆盖 journal 持久性、掉电边界、设备重启和动作结果未知。

### ⚠️ 6.8 必须诚实标注的边界

**如果你按这一章建了系统，你**没**实现下面的东西**（这个项目也没有）：

| 未实现 | 说明 |
| --- | --- |
| **leader fencing 与业务提交的同存储原子校验** | 三道检查是**并联的**，不是**一次原子提交** |
| **跨存储事务** | 租约在 Redis、世界在文件、任务在库——**三者无法原子** |
| **数据库/队列切主** | 无主从/切主处理 |
| **时钟漂移的自动处理** | `Grant.ExpiresAt` 由持有者**本地时钟**推算，而真正过期由服务端 `PEXPIRE` 决定 |
| **网络分区的真实测试** | 语义与机制齐备，**但没有任何测试真的断开网络** |

**本书的立场是**：把这些写清楚，比假装它们不存在好。

---

## 阶段 7 · 运维与放行（持续）

### 你要建什么

**可观测性、评测刻度、以及三份不会骗人的清单。**

### 阶段 7.1 事故记录：事实与诊断分开

**两个 schema，职责相反**：

| | `bundle` | `incident` |
| --- | --- | --- |
| 内容 | **只有事实**：状态、事件、步骤、证据引用、耗时 | 事实 + **带标签的诊断** |
| 特性 | **不分类、不推测、不建议** | 分类是它唯一的职责 |

**理由**：

> **故障族知识只能有一处；另一种语言里的第二份拷贝必然漂移。**

**四条工程约束**：

| # | 约束 |
| --- | --- |
| 1 | **写入不得改变任务结局**（写失败只 log） |
| 2 | **读者不会看到半份事故**（write-then-rename） |
| 3 | **有界**（保留最新 N 份） |
| 4 | **目录可部署**（环境变量） |

**第 1 条最重要**：

> 一个"审计写入失败导致任务失败"的系统，会让运维为了保住任务而**关闭审计**。

**只在异常终态写**（成功不写）——因为保留窗口会被成功任务占满。

### 阶段 7.2 一个让"未归类"可见的巡检

```bash
python diagnose.py --sweep artifacts/incidents
# → 共 64 份，未归类 26 份；exit 1
```

**未归类的计入非零退出码**——因为：

> **一个只打印报告的工具，它的输出会被忽略。**

**这条巡检的价值在本书里被验证过一次**：它发现了 `GRASP_NOT_REACHED` 在 Go 侧归 `PERCEPTION`，而 Python 的故障族表**没有任何一族包含它**——**两套词表漂移了**。

**如果退出码是 0，这个漂移会一直存在。**

### 阶段 7.3 评测刻度：先冻结，后运行

**四条规则**：

| # | 规则 |
| --- | --- |
| 1 | **拒绝是独立维度**：`executable: a/b` / `refusals: c/d` 永远分开打印 |
| 2 | **运行失败 ≠ 答错**：模型不可达是 `Error`，不是 `Refused` |
| 3 | **断言目标，不断言实现** |
| 4 | **换一个端点就是全部的接口** |

**第 1 条的理由**：

> **"不敢做"和"做错了"需要完全相反的修法。**

**"先冻结"的纪律**：

```
协议、数据、问题、评分器、生产渲染器二进制 —— 都在开发选择之前冻结
selection.json 写入后不可覆盖
测试集结果不得用于改模板或改标注
```

**代价是必须公开负结果**——而报告里就写着"规划在全部候选中完整字段正确率为 0%"，**并且没有把它藏起来**。

### 阶段 7.4 三个必须区分的状态

**你的评测/门禁/巡检工具都需要三态，不是两态**：

| 位置 | 三态 |
| --- | --- |
| 训练门禁 | PASS / FAIL / **INCONCLUSIVE** |
| 训练数据 | 正样本 / 负样本 / **都不进** |
| 训练 run | 上线 / 丢弃 / **`run-incomplete`** |
| 恢复复验 | `verified` / 失败 / **"已执行但未确认"** |
| 故障注入 | 通过 / 遗漏 / **"故障根本不存在"** |
| 预检 | PASS / FAIL / **WARN** |

**而"命令跑完了"和"被测对象通过了"必须用不同退出码区分。**

### 阶段 7.5 三份清单

**而它们应该一条都不勾。**

| 清单 | 内容 |
| --- | --- |
| **安全检查清单** | 实体急停、工作区、串口、标定、arm 授权、停止响应时间、第一次带载的物体 |
| **发布检查清单** | 软件与合同、家庭仿真、ROS 2、**实机放行** |
| **生产就绪** | **"哪些结果可以证明什么"的证据分级表** |

**证据分级表（最值得抄走的一张）**：

| 证据 | 能证明什么 |
| --- | --- |
| 仿真测试通过 | 对应代码版本和**限定仿真场景**的行为 |
| 无动作预检 | 配置、文件、证书与驱动兼容 |
| **Runtime READY** | 当前软件能力检查状态，**不是现场动作许可** |
| 现场记录 | 版本化配置、制品与操作员记录**符合阶段要求** |
| **实际生产放行** | **现场负责人**验证风险、质量、真实停止与恢复 |

**"以上结果不能相互替代。"**

---

## 14.2 一张总检查表

| 阶段 | 核心产出 | **怎么证明（会失败的检查）** |
| --- | --- | --- |
| **0** | `Gate` 纯函数 | 交换判定顺序 → 测试红；毫秒边界用例 |
| **1** | 生成的工具目录 | `--check` 逐字节；加 `approval_id` → 测试红 |
| **2** | 三值世界模型 | 证据超龄 → `UNKNOWN`（不是 `FALSE`） |
| **3** | 8 类 + 5 个落点 | 删任一落点 → 测试红；未识别码 → 禁止重试 |
| **4** | 单通道 + 最终软件安全准入点 | 加 `origin` 字段 → 测试红；`READY` 无 `EXECUTING` 边 |
| **5** | 编排 + 观察者 | 给观察者加字段 → 测试红；故障矩阵每场景断言 `want_retry_forbidden` |
| **6** | 租约 + fencing | 旧 token 被拒；过期不退回 READY；**真的 SIGSTOP 一次** |
| **7** | 巡检 + 刻度 + 清单 | 未归类 → 非零退出码；**三份清单一条不勾** |

---

## 14.3 如果你只有一周

**不可能做完八个阶段（编号 0–7）。** 但你可以做完**最不可省的三件事**：

| 天 | 做什么 | 交付 |
| --- | --- | --- |
| **1** | 阶段 0 | `Gate` 纯函数 + 四个断言 |
| **2–3** | 阶段 1 的**安全标注部分** | 工具目录 + `mutates_world` + 5 级 |
| **4–5** | 阶段 3 的**分类与兜底** | 8 类 + `knows` + 保守兜底 |
| **6** | 阶段 4 的**幂等 + 审批** | `Command` 无来源字段 + `READY` 无 `EXECUTING` 边 |
| **7** | 把上面串成一个 Local Agent | 一次完整的"工具报成功但没做成 → 系统拒绝完成" |

**注意第 7 天的验收**：

> **不是"任务成功了"，而是"系统拒绝了一个假的成功"。**

**这是本书想让你带走的最重要的一个测试。**

---

## 14.4 十件最容易做错的事

| # | 错误 | 正确 |
| --- | --- | --- |
| 1 | 先写工具，后想完成判定 | **先写 `Gate`** |
| 2 | 用布尔而不是三值 | `UNKNOWN` **不等于** `FALSE` |
| 3 | 未识别的错误码当成"可重试" | **兜底方向必须保守** |
| 4 | 只在"应该禁止重试"时断言 | **每个场景都断言**（否则回归会悄悄消失） |
| 5 | 用时间戳的浮点值直接比较 | **说明同桶兼容策略与因果局限**，补设备序列和跨机时钟预算 |
| 6 | 工具自述"我不改世界"就信它 | **从清单的安全级别派生**，不问工具 |
| 7 | 让观察者持有执行端口 | **字段白名单反射测试** |
| 8 | 租约过期 → 重新认领 | **→ `UNKNOWN_OUTCOME`，等人对账** |
| 9 | 用 UUID 做幂等键 | **确定性字符串**（重放时必须相同） |
| 10 | 审计写入失败导致任务失败 | **best-effort**（否则运维会关掉审计） |

---

## 14.5 本章小结

1. **先建"什么算完成"，再建"怎么完成"。** 完成判定决定了工具的接口形状——反过来不成立。

2. **先做 Local，再加 Cloud。** "完成判据 / 失败分类 / 证据 / 对账"是物理世界强加的；"协调 / 世界持久化 / 跨机通知"是多机强加的。先做对前者。

3. **每个阶段都要有一个"会失败的检查"。** **一个从没红过的测试，不是一个测试。**

4. **第 7 天的验收不是"任务成功了"，而是"系统拒绝了一个假的成功"。**

5. **诚实标注你没做的东西。** 这个项目列了五条"仍须独立验证"——而那让它比一个声称全都做完的项目**更可信**。

---

## 14.6 与全书各章的对应

| 阶段 | 对应章节 |
| --- | --- |
| 0 · 定义完成 | 第 3 章 |
| 1 · 单一事实来源 | 第 5 章 |
| 2 · 观测与世界 | 第 4 章 |
| 3 · 闭环 | 第 3 章、第 6 章 |
| 4 · 执行 | 第 5 章、第 9 章 |
| 5 · 编排与多 Agent | 第 6 章、第 7 章 |
| 6 · 分布式 | 第 8 章 |
| 7 · 运维与放行 | 第 10、11、12、13 章 |
| **贯穿全书** | 第 1 章（三条物理约束）、第 2 章（演进史的九个缺陷） |

---

**下一章**：[第 15 章 未来方向](../chapters/ch15-future-directions.md) —— 这套系统自己承认还有哪些没解决？24 条显式 TODO 与 6 个推断方向。
