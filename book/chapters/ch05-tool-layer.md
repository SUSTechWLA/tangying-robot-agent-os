# 第 5 章 工具层：模型能碰什么，绝对不能碰什么

> **版本口径**：本章包含 v0.6.0/v0.7.0 演进案例。代码片段、计数与实验按原时点解释；出版复核修正论证，不表示历史缺口均为当前状态。当前云边能力见第17章，来源与证据边界见出版说明。

> **本章的核心命题**
>
> 把大模型接上机器人，第一个要回答的问题**不是**"怎么让它调工具"，
> 而是"**它调的工具应该长什么样，以及哪些工具根本不该给它**"。
>
> 这一章讲的是一条边界：**物理动作的粒度是任务，不是电机。**

---

## 5.1 先对账：关于这一层，公开叙述已经系统性漂移

写这一章时最重要的一件事，是我发现**关于工具层的说法与代码事实已经整体过期了**。

全部实测结果：

| 口径 | **代码事实** | 文档/README 的说法 | 判定 |
| --- | --- | --- | --- |
| 注册工具总数 | **31** | `docs/development/robot-tool-layer.md` 说「27 个」 | 文档过期（差 4） |
| 默认提供给 LLM | **29** | 同上「25 个默认提供给 LLM」；`README.md:137` 说「28 个」 | 文档过期 |
| 不提供给 LLM | **2** | 一致 | ✅ |
| MCP 桥接工具 | **9** | `robot/mcp/README.md` 表格 8 个 | 文档过期（新增 `get_survey`） |
| `mutates_world=true` | **13**（在 29 个公开工具里） | `docs/development/2026-09-18-system-review-and-improvement-plan.md` 说「14 个」 | 数字随工具增删变化 |
| 安全等级分布 | `{0:11, 1:2, 2:6, 3:6, 4:4}` | 同上说 `{0:11, 1:2, 2:7, 3:6, 4:4}` | 差在 level 2 少 1 |

**为什么这件事值得放在章首？**

因为它是本书方法论的第一个实证：**数字会腐烂，代码不会**。

我不是在挑错——这些差异每一个都只有 1–4 个单位，看起来无关紧要。但它们的**累积效应**是：一个读者如果照 README 去数工具，**他会数不出来**，然后怀疑是自己理解错了。

**教学要点**：在任何技术写作里，凡是引用一个"数量"的地方，都应该同时给出**测量它的命令**。否则那个数字在下一个提交后就开始腐烂。

我实测这个数字的方法：

```bash
python3 -c "
import json
d = json.load(open('tools.json'))
print('schema:', d['schema_version'])
print('tools:', len(d['tools']))
print('metadata:', len(d['metadata']))
print('excluded_from_llm:', d['excluded_from_llm'])
"
# → schema: llm.tools.v1
# → tools: 29
# → metadata: 31
# → excluded_from_llm: ['move_arm_to_joints', 'navigate_to_pose']
```

**`31 = 29 + 2`。这个恒等式是理解全部机制的关键。**

---

## 5.2 工具目录是生成的，不是手写的

### 这是一条被低估的工程决策

`tools.json` **不是**手写契约，而是**从代码生成的产物**：

```
build_catalog()                    llm_tools.py:55-86
   遍历 ToolRegistry
   调用每个 RobotTool.openai_schema()   tool_layer.py:607
   导出为 OpenAI function calling schema
   附带 metadata 边车
```

`llm_tools.py:129-151` 提供两个开关：

| 开关 | 作用 |
| --- | --- |
| `--write` | 重新生成 `tools.json` |
| `--check` | 断言磁盘文件与生成结果**逐字节相同** |

CI 与 `make lint` 跑 `--check`，`tests/tool_layer/test_llm_tools.py` 断言这一点。

### 后果：消除同一生成链中的手工漂移

> **同一 Python 注册表生成工具目录，减少目录与实现的手工漂移。** 跨版本部署、Go 目录、Runtime 广告、健康与权限过滤仍须校验（见 5.5）。

**为什么这一条重要？**

在大多数 agent 系统里，"工具目录"是一份**文档**——它描述能力，但不保证实现。于是会出现两类经典 bug：

| Bug | 症状 |
| --- | --- |
| 目录里有、实现里没有 | 模型调一个不存在的工具，得到一个 500 |
| 实现里有、目录里没有 | 一个能力存在但模型永远发现不了 |

**在同一生成链和相同版本内，生成式目录防止这两类手工失配**——因为目录不是"描述"实现，目录**就是**实现的一个投影。

**教学要点**：这是一个可以直接复用的模式。任何"外部可发现的接口清单"，都应该是**从权威源生成的**，而不是手工维护的。手工维护的清单只有一个归宿：腐烂。

### `RobotTool` 的字段

`RobotTool` 是 `@dataclass(frozen=True)`（`tool_layer.py:534`）——**不可变**：

| 字段 | 语义 | 在 `tools.json` 的位置 |
| --- | --- | --- |
| `name` | 蛇形，**禁止含点号**（`:560`） | `tools[].function.name` |
| `description` | 何时用 + 前置条件 + 恢复路径 | `tools[].function.description` |
| `parameters_schema` | JSON Schema，全部 `additionalProperties: false` | `tools[].function.parameters` |
| `returns_schema` | 返回值契约 | `metadata.*.returns` |
| `safety_level` | **0–4**，构造期强制 | `metadata.*.safety_level` |
| `timeout_s` | 必须为正 | `metadata.*.timeout_s` |
| `distributed_node` | 路由与健康分组 | `metadata.*.distributed_node` |
| `idempotent` | 决定能否自动重试 | `metadata.*.idempotent` |
| `mutates_world` | 默认 `False`；为真时禁止重试且必须过闭环证据门 | `metadata.*.mutates_world` |
| `llm_visibility` | `"primary"` / `"fallback"` | `excluded_from_llm` |
| `handler` | **唯一碰硬件的入口函数** | 不导出 |

**注意这里没有 `reversible`（可逆性）字段。** 这是一个刻意的空缺——见 3.7。

---

## 5.3 5 级安全标注：一个必须讲清楚的准确性问题

### 定义

```python
# tool_layer.py:522-531
class SafetyLevel(int):
    """Safety classification, ordered so a supervisor can compare numerically."""
    QUERY            = 0   # 查询：不改变任何状态
    LOW_SPEED_MOTION = 1   # 低速运动
    NORMAL_MOTION    = 2   # 常规运动
    CONTACT          = 3   # 接触物体
    SAFETY           = 4   # 安全相关
```

**它是 `int` 的子类，唯一目的是可数值比较。** 这让 `ToolRegistry.select(max_safety_level=...)` 能一句话完成"只给我不超过某危险等级的工具"的过滤。

**这个设计选择值得注意**：把安全等级做成**可比较的数值**而不是枚举字符串。为什么？因为安全过滤的本质是"**不超过**某一级"，而这是一个不等式。用字符串你就得写一个映射表。

### 每一级的真实工具清单（29 个公开工具）

| 级别 | 名称 | 数量 | 工具 |
| --- | --- | --- | --- |
| **0** | `QUERY` | **11** | `capture_image`、`detect_object`、`get_arm_state`、`get_current_pose`、`get_gripper_state`、`get_object_pose`、`get_robot_status`、`plan_work_area`、`recall_object`、`resolve_location`、`scan_environment` |
| **1** | `LOW_SPEED_MOTION` | **2** | `move_arm_relative`、`stop_navigation` |
| **2** | `NORMAL_MOTION` | **6** | `build_map`、`explore_for`、`home_arm`、`move_arm_to_pose`、`navigate_to`、`navigate_to_work_area` |
| **3** | `CONTACT` | **6** | `fetch_object`、`grasp`、`pick_object`、`place_object`、`release`、`set_gripper_width` |
| **4** | `SAFETY` | **4** | `check_collision`、`emergency_stop`、`reset_safety_stop`、`set_speed_limit` |

**两个反直觉的归类值得单独说：**

**① `stop_navigation` 是 level 1（低速运动），不是 level 0。**

停止是一个**动**作——它要下发零速度指令，要改变底盘状态。把它归为"查询"是危险的：一个被认为"只读所以随时可以调"的停止指令，在系统里是有物理副作用的。

**② `emergency_stop` 是 level 4，而 `mutates_world = false`。**

这一点极其重要，也是全章最漂亮的一处设计：

| 属性 | 值 | 为什么 |
| --- | --- | --- |
| `safety_level` | **4**（最高） | 它改变物理世界的运动状态 |
| `mutates_world` | **false** | 它**不需要动作后的证据**才能算完成 |

**"改世界"和"需要证据"在这个系统里是两个独立的属性。**

`emergency_stop` 改世界，但它不需要"动作后的新鲜观测确认"——因为**急停的正确行为由硬件回执确认（`stop_confirmed` / `latched`），而不是由一次相机采集确认**。要求"急停后拍张照证明它停了"是荒谬的。

**教学要点**：这是一个很好的反例，用来纠正"改世界 = 需要证据"这个过于简化的理解。正确的表述是：**每个物理动作需要什么证据来确认，是由动作的语义决定的，不是由"它是否改世界"决定的。**

### ⚠️ 一个必须标注的准确性问题：5 级对外只投影出 4 个名字

这是本章最重要的准确性说明。

```python
# tool_layer.py:620-627
def _safety_level_name(level: int) -> str:
    if level >= SafetyLevel.SAFETY:           return "safety_critical"   # ≥4
    if level >= SafetyLevel.CONTACT:          return "physical_contact"  # ≥3
    if level >= SafetyLevel.LOW_SPEED_MOTION: return "physical_motion"   # ≥1
    return "read_only"                                                    # 0
```

**`level 1` 与 `level 2` 导出为同一个字符串 `"physical_motion"`。**

这个函数是 `RobotTool.capability_info()` 的输出，也就是**运行时能力契约** `Capability.safety_level` 的实际值。后果链条：

| 层 | 看到的档数 | 证据 |
| --- | --- | --- |
| `tools.json` 的 `metadata.safety_level` | **5 档**（0–4） | 本节的表 |
| gRPC `CapabilityInfo.safety_level`（proto `robot.proto:58`） | **4 档**（字符串） | `_safety_level_name` |
| `safety.py:104` 判定"是否物理动作" | 只看 == `"physical_motion"` | |
| `cmd/edge-worker/main.go` | 同上 | |
| **Go 侧 `core/skills/manifest.go`** | **只有 3 档**：`read_only` / `local_side_effect` / `physical_motion` | |

**所以：**

> **「5 级安全标注」是工具层内部的排序刻度。**
> **系统其余部分（能力契约、安全监督）看到的是 4 档，Go Agent 看到的是 3 档。**

把它讲成"全系统 5 级"是**不准确**的。宣传材料里的"5 级"只对 `tools.json` 的 `metadata.safety_level` 成立。

**这本身就是一个教学点**：一个精度在跨层传递时**被降级**了，而降级是**有意的**——因为更高层不需要那么细的区分。但**如果文档不说明这一点**，读者会以为自己的工具级过滤（`select(max_safety_level=1)`）在系统层面也被尊重。

### 七处强制点：从定义期到执行期

安全标注不是文档承诺，代码里有**七处**强制：

| # | 阶段 | 强制内容 | 位置 |
| --- | --- | --- | --- |
| 1 | **构造期** | `0 ≤ safety_level ≤ 4`，否则 `ValueError`；`timeout_s > 0`；`distributed_node` 非空；`llm_visibility` 合法 | `tool_layer.py:559-570`；测试 `test_tool_contract.py:180-196` 逐条参数化断言（含 `{"safety_level": 9}`） |
| 2 | **注册期** | 同名重复注册直接 `ValueError`，**不允许静默覆盖**（重载走显式的 `replace()`） | `tool_layer.py:658-669` |
| 3 | **选择期** | `max_safety_level` 过滤 | `tool_layer.py:704`；测试 `test_tool_contract.py:285` |
| 4 | **Go 清单校验** | `physical_motion` 必须同时满足 `SideEffect=true`、`DefaultLeaseMS != 0`、`AllowedSafetyProfiles` 非空；且 **`MutatesWorld && !SideEffect` 是硬错误** | `core/skills/manifest.go` |
| 5 | **规划校验** | 物理步骤缺 lease / deadline 已过 / 缺 `ApprovalID` / 缺 `IdempotencyKey`，四条各自独立报错 | `core/guard/guard.go` |
| 6 | **执行期审批派生** | `needsApproval` 从 manifest 的安全级别派生，**不询问工具自己** | `internal/actionloop/loop.go` |
| 7 | **安全监督** | 物理动作无 `approval_id` → `APPROVAL_REQUIRED` | `safety.py:117-118` |

### 第 4 处和第 6 处的注释值得原文引用

**第 4 处**（`core/skills/manifest.go`）解释了为什么 `MutatesWorld && !SideEffect` 是硬错误：

> 那会**告诉调用方不需要确认，而闭环门禁却在等一份永远不会到的证据**。

**这句话精辟地指出了一类设计错误**：两个属性分别看都对，合起来自相矛盾。声明"我不改世界"的后果是"跳过证据门禁"，而声明"我改世界"的后果是"等待证据"——如果两个声明同时存在，系统会**永远等待一份永远不会来的证据**。

**第 6 处**（`internal/actionloop/loop.go`）是一句可以刻在墙上的注释：

```go
// needsApproval reports whether the manifest requires a person before this call.
// It is derived from the safety level rather than asked of the tool, so a tool
// cannot opt itself out of approval by claiming it does not need one.
func needsApproval(tool Tool) bool { return tool.SafetyLevel == skills.SafetyPhysical }
```

**"所以一个工具不能通过声称自己不需要批准来把自己排除在批准之外。"**

**注意判定用的是 `==`，不是 `>=`**——因为 Go 侧只有 3 档，`physical_motion` 就是最高档。

---

## 5.4 不暴露给模型的工具：只有两个，机制是一行

### 完整清单

```json
"excluded_from_llm": ["move_arm_to_joints", "navigate_to_pose"]
```

**只有两个：关节角（`move_arm_to_joints`）与世界坐标位姿（`navigate_to_pose`）。没有第三个。**

### 机制：不是白名单，不是第二条注册路径

设计意图写在 ADR 里：

> 工具**保留**为"底层兜底"，但标记为不给 LLM——
> 「这样『底层兜底可选、LLM 默认看不到』两个要求同时成立，**而不是假装它不存在**。」

落到代码是**一个字段 + 两道过滤**：

**字段声明**（`tool_layer.py:551-554`，默认 `"primary"`）：

```python
# "primary" tools are advertised to an LLM; "fallback" tools exist for
# diagnostics and coordinate-level recovery but are not offered by default.
llm_visibility: str = "primary"
```

**标记位置——只有这两行**：

| 文件 | 工具 |
| --- | --- |
| `robot/gateway/tangying_robot_gateway/tools/navigation.py` | `navigate_to_pose` |
| `robot/gateway/tangying_robot_gateway/tools/manipulation.py` | `move_arm_to_joints` |

**过滤点 1（导出期）**：`llm_tools.py:64`

```python
for tool in registry.select(llm_only=True):
```

实现体在 `tool_layer.py:706`：

```python
if llm_only and tool.llm_visibility != "primary": continue
```

**注意一个细节**：`metadata` 用 `llm_only=False` 遍历（`llm_tools.py:81`），所以这两个工具**仍在 `metadata` 里**，只是不在 `tools` 数组里。`excluded_from_llm` 由 `llm_tools.py:83-85` 显式列出。

**为什么要保留在 metadata 里？** 因为运维需要知道它们存在、需要能诊断它们。**"不给模型"不等于"不存在"**——这个区别在治理上很重要。

**过滤点 2（广告期）**：`tool_executor.py:256-263` 的 `Executor.openai_tools(llm_only=True)` 同样走 `registry.select`，再叠加"节点不健康则移除"——**但 `emergency_stop` 例外，永远保留**。

**最后这个例外是全章最重要的一个细节。**

### `emergency_stop` 永远保留

**为什么？** 因为一个"因为某个子系统不健康而从模型工具列表里消失"的急停工具，会让模型在最需要停的时候**没有停的能力**。

而这个系统有一条贯穿性的原则：

> 已经停住的机器人，仍然必须能被停住。

（本章工具能力摘除机制中：最严重的 `safety` 级故障会摘掉**所有**声明了依赖的能力，**唯一幸免的是声明"零依赖"的 `emergency_stop`**。）

**教学要点**：这是一条可以推广到所有安全系统的规则——**安全机制本身不能被它要保护的机制影响。** 一个"当系统不健康时自动禁用"的急停按钮是**反安全**的。

### 测试锁定

`tests/tool_layer/test_llm_tools.py` 同时断言三件事：

1. 两个名字**不在** `tools` 里；
2. `excluded_from_llm` **恰好**是这两个；
3. 语义入口 `navigate_to` / `move_arm_to_pose` / `pick_object` **在**列表里。

`test_tool_selection.py:148-154` 断言 fallback 工具"**可被编排调用，但不出现在 `openai_tools()` 里**"。

**第 3 条断言很讲究**：它防止一个开发者"为了让模型看不到坐标级接口"而顺手把语义入口也藏起来。**藏对了东西**和**藏多了东西**都要被测试拦住。

### 补一层：模型即使知道名字也传不进安全字段

这一层很重要，因为它把"模型不能自带批准"从**提示词请求**变成了**schema 事实**。

**机制**：所有工具的 `parameters_schema` 都是 `additionalProperties: false`，`ToolExecutor.validate_arguments()`（`tool_executor.py:268`）在进入 handler **之前**校验。

测试 `tests/tool_layer/test_execution_admission.py` 断言：

```python
ToolCall("move", {"approval_id": "invented"})
# → INVALID_PARAM，且 handler 未被调用
```

**规划提示词里也显式禁止**（`orchestration/llm.go`）：

> Do not include safety fields (approvalId, deadlineUnixMs, leaseMs, idempotencyKey, safetyLevel);
> the Robot Runtime fills them.

**但提示词是第二道防线，schema 是第一道。**

| 防线 | 类型 | 能被绕过吗 |
| --- | --- | --- |
| 提示词 | 请求 | **能**——模型可以不听 |
| **schema `additionalProperties: false`** | **强制** | **不能**——多余的字段被拒绝 |

**教学要点**：这是本书反复出现的一个模式——**把请求变成约束**。第 8 章讲过同样的手法（`Command` 里根本没有"来源"字段），第 3 章也讲过（对账用 SQL 的 `WHERE` 条件而不是应用层 if）。

---

## 5.5 统一执行通道：从模型到电机的九步

所有工具走同一条 `ExecuteSkill` 通道。完整调用链：

```
① 模型 function calling
      │  工具定义来自 tools.json（由 llm_tools.py 生成）
      ▼
② Executor.execute(call)                          tool_executor.py:176
      ├─ 未注册 → NOT_FOUND（含 known_tools 列表）           :181-185
      ├─ schema 校验（additionalProperties=false）           :187-189 → :268
      ├─ 节点不健康 → UNREACHABLE（emergency_stop 除外）      :192-197
      ├─ timeout = min(call.timeout_s, tool.timeout_s)      :198-205
      ├─ 预取消 → CANCELLED（emergency_stop 除外）           :205-206
      └─ mutates_world → 取全局运动锁，取不到即 BUSY          :207-215
      ▼
③ _execute_with_retry                             tool_executor.py:224-253
      attempts = 2 if _retry_allowed(tool) else 1           :225 → :305-314
      ▼
④ tool.execute(**args) → handler                  tool_layer.py:572-583
      handler 抛异常 → HARDWARE_ERROR（:575-577）
      返回非 ToolResult → HARDWARE_ERROR（:578-581）
      ▼
⑤ adapter.execute(skill, parameters, context)     gateway_adapter.py:76-125
      ├─ 适配器必须是 RobotRuntimeService，裸 backend 直接 TypeError  :60-64
      ├─ lease_ms = min(budget,60s)*1000；deadline = now+budget       :98,:113
      └─ 构造 Command(...19 字段...)                                 :106-124
      ▼
⑥ RobotRuntimeService.invoke(command)             service.py:267-295
      （复用 RPC 的同一条准入与 journal 路径）
      ▼
⑦ execute_for_test → 唯一安全入口                  service.py:307-343
      ├─ emergency_stop 分支：直接 safety.emergency_stop()，
      │  抢在任何在途命令之前                                        :315-321
      ├─ 每机器人执行锁非阻塞获取，失败 → ROBOT_BUSY /
      │  IDEMPOTENCY_CONFLICT / EXECUTION_OUTCOME_UNKNOWN           :324-330
      ├─ journal 幂等查表（conflict / pending / reconciled / replay 四态）  :345-360
      └─ decision = self.safety.start(command)   ← 最终软件安全准入点        :385
      ▼
⑧ SafetySupervisor.evaluate                       safety.py:78-130
      estop 锁存 → EMERGENCY_STOP_LATCHED
      忙 → ROBOT_BUSY
      schema/task_id/command_id → deadline → lease（0/过长）
      → idempotency_key → safety_profile
      → **physical && !approval_id → APPROVAL_REQUIRED**
      → 参数校验 → ALLOWED
      ▼
⑨ backend.execute(command) → 硬件 / 仿真
```

### 三个顺序细节值得单独讲

**细节 1：第 ③ 步的重试在第 ⑤ 步之前决定。**

也就是说，**重试决策发生在一个 `Command` 被构造出来之前**。这不是巧合——它的含义是"重试的是**工具调用**，不是**命令**"。

**细节 2：第 ⑦ 步的幂等 journal 在第 ⑧ 步的安全准入之前查表。**

顺序是"**先查我是不是重复调用，再问安全能不能做**"。

这个顺序用于返回已记录命令的缓存结果，避免重新执行。它不重新授权硬件动作，也不把历史成功当成当前世界状态。结果未知或在途记录须按 journal 协议处置；新命令必须重新经过当前安全准入。

**细节 3：第 ⑧ 步汇总 Runtime 安全准入。**

`SafetySupervisor.evaluate` 返回 `SafetyDecision`，它在引用的执行路径中提供最终安全准入；schema、健康、锁、Guard 等前置检查也能拒绝。

**教学要点**：统一安全策略与多层拒绝并存。记录每一层的拒绝理由和授权来源，最终准入仍不能取代实体安全联锁。单个检查通过不代表整个链路可执行。

### 工具层不做安全监督

`tool_layer.py:1-24` 与 `safety.py:1-13` 的模块 docstring 都写明了：

> 工具层**不实现**限幅、急停或租约。

**为什么？** 因为如果工具层也能否决，就变成了两个否决点——而"哪个否决了这次调用"会变得难以回答。

**分工**：

| 层 | 职责 |
| --- | --- |
| **工具层** | 参数校验、路由、超时、重试预算、并发锁 |
| **Runtime 安全监督** | 审批、急停、schema、deadline、lease、safety profile |

### 「不新增执行通道」是可以验证的

`tests/contract/test_proto_schema.py` 断言 gRPC 服务方法集合**恰好**是：

```python
{GetRuntimeInfo, Observe, ExecuteSkill, Cancel, EmergencyStop, ListServices, CallService}
```

**工具层新增的 31 个名字全部编译进既有的 `ExecuteSkill`，没有新增任何 RPC。**

**教学要点**：这是一个非常好的可测试不变量。**"我们没有新增执行通道"** 这句话在大多数系统里只是一个声明；在这个系统里它是一条**会失败的测试**。

---

## 5.6 一个必须澄清的边界：仓库里有**两份**工具目录

这是本章最容易讲错的地方之一。

| | **Python 工具层** | **Go 技能清单** |
| --- | --- | --- |
| 文件 | `tools.json` / `tools/*.py` | `skills/manipulation/plugin.go` |
| 名字形态 | `pick_object`（蛇形**无点**） | `manipulation.pick`（**带点**） |
| 条目数 | **31**（29 公开） | **12** |
| 消费者 | 外部 function-calling 客户端 / `executor.openai_tools()`（**仓库内无生产调用点**，只有测试与文档示例） | 进程内 Go 规划器 `orchestration/llm.go` |
| 字段 | `safety_level` 0–4、`mutates_world`、`idempotent`、`timeout_s` | SafetyLevel **3 档**、`SideEffect`、`MutatesWorld`、`ApprovalPolicy`、`DefaultLeaseMS`、`AllowedSafetyProfiles` |

**两者在 `ExecuteSkill` 处汇合。**

### 一个具体、可验证的教学案例

`orchestration/llm.go` 送给模型的 `skillView` **只有五个字段**：

```
name / description / safetyLevel / sideEffect / requiredParameters
```

**不含 `MutatesWorld`。**

也就是说：**Go 规划器看到的技能清单里，「这一步做完必须拿新鲜证据确认」这一条没有随清单一起给模型。**

它仍然被执行路径强制（闭环门禁在 `runner.go` 里），但**模型在做计划时看不到它**。

**这是一个可以直接推广的审计问题：**

> **声明存在 ≠ 声明被送到决策者面前。**

一个字段在数据模型里有、在文档里写着、在执行路径上被强制——但**在决策发生的那一刻不在场**——它的作用就只剩"事后拒绝"，而不是"事前避免"。

**教学要点**：审计一个系统的信息流时，要问的不是"这个字段存在吗"，而是"**做这个决定的那段代码，能看到它吗**"。

---

## 5.7 一处刻意的空缺：没有 `reversible` 字段

`RobotTool` 有 `mutates_world`，但**没有** `reversible`（可逆性）。

**这不是遗漏。** 想清楚为什么，就理解了这一层的设计哲学。

### 可逆性无法在工具粒度上标注

考虑几个工具：

| 工具 | 可逆吗？ |
| --- | --- |
| `navigate_to` | 可逆（走回去），**但代价是时间，而且可能路上有障碍** |
| `grasp` | 可逆（松开），**但如果物体已经摔了就不行** |
| `place_object` | "可逆"（再拿起来），**但如果放进去的是杯子、拿起来时碰倒了旁边的——不可逆** |
| `set_speed_limit` | 可逆 |
| `emergency_stop` | 可逆（复位），**但急停期间发生的事不可逆** |

**"可逆性"不是工具的属性，是"这一次执行 + 当前世界状态"的属性。**

把它标在工具上会给出**虚假的保证**：一个标注着"可逆"的 `place_object`，在一次把物体推进窄缝的执行里，是不可逆的。

### 系统选择了什么

它选择了两个**可在工具粒度上判定**的属性：

| 属性 | 可在工具粒度判定吗 | 因为它回答的是 |
| --- | --- | --- |
| `mutates_world` | ✅ | "**这个动作会改变世界吗**"——这是一类动作的固有性质 |
| `idempotent` | ✅ | "**重复执行会不会产生新效果**"——这也是固有性质 |

而"可逆性"被**下推到了完成判据**上：系统不问"这个动作可逆吗"，它问"**这个动作做完之后，我怎么知道它做成了**"——也就是 `Gate`。

**如果做成了，就不需要撤销。如果不知道做成了没有，就不能重做。** 这两条覆盖了可逆性真正要防的东西，而且**都是可判定的**。

**教学要点**：这是一个"**不要标注不可判定的属性**"的范例。一个标注了但不可判定的属性，比不标注更糟——因为它会让人以为这里有一道保护。

---

## 5.8 MCP 桥接：给外部 Agent 的一条窄门

### 实际是 9 个工具，不是 8 个

代码事实：`robot/mcp/tangying_mcp/server.py` 的 `SPECS` 字典有 **9** 项，`tests/mcp/test_bridge.py` 的 `TOOLS` 集合也断言 9 个。

| # | 工具 | 参数 | Fleet 端点 | 读/写 | 幂等 |
| --- | --- | --- | --- | --- | --- |
| 1 | `list_robots` | 无 | `GET /v1/devices` | 读 | 是 |
| 2 | `get_robot_capabilities` | `robot_id` | `GET /v1/devices/{id}` | 读 | 是 |
| 3 | `observe_world` | 无 | `GET /v1/world` | 读 | 是 |
| 4 | **`get_survey`** | 无 | `GET /v1/mapping` | 读 | 是 |
| 5 | `list_tasks` | 无 | `GET /v1/tasks` | 读 | 是 |
| 6 | `create_task` | `request`, `adapter` | `POST /v1/tasks` | **写** | **否** |
| 7 | `get_task` | `task_id` | `GET /v1/tasks/{id}` | 读 | 是 |
| 8 | `cancel_task` | `task_id` | `POST /v1/tasks/{id}/cancel` | **写** | **否** |
| 9 | `emergency_stop` | `robot_id`, `reason` | `POST /v1/devices/{id}/estop` | **写** | 是 |

> **文档冲突**（第 5.1 节那张表里的最后一处）：`robot/mcp/README.md` 的表格只有 8 行，缺 `get_survey`；`docs/superpowers/specs/2026-09-11-standard-robot-tool-layer-adr.md` 也写"8 个统一工具"。**以代码为准：9 个。**

### `get_survey` 的加入方式本身就是一条设计原则

新增原因（CHANGELOG）：

> MCP 侧**只加一个只读工具** `get_survey`，**没有** `start_survey` / `stop_survey` / `finish_survey`。

理由：**一次建图自主驱动机器人二十分钟，启动权留在控制台操作员手里。**

而测试把这条写成了断言（`tests/mcp/test_bridge.py`）：

1. 断言 `get_survey` **在**目录里；
2. 断言 `start_survey` / `stop_survey` / `finish_survey` / `move_robot` / `call_robot_service` **都不在**目录里；
3. 核对"**读一次调查 = 恰好一次 `GET /v1/mapping`**"。

第 3 条的注释值得原文引用：

> **一个只读工具如果偷偷发了第二个请求，就是一边声称观察一边行动。**

**这是一个非常精确的安全定义**：只读性的判据不是"工具的名字里有 get"，而是"**它发出的请求数等于它声称的数量**"。

### 「为什么只给这么少」的答案：拓扑事实

桥接能触达的**全部能力**就是上面那 9 个 HTTP 调用，**没有一条通往机器人 gRPC / ROS 2**。

所以"不暴露关节控制"**不是过滤，是拓扑事实**：

> **这个进程里根本不存在指向 `arm.move` / `action_chunk` 的代码。**

模块 docstring 一句话：

> A bounded stdio MCP facade; approval and physical execution stay in Fleet.

**教学要点**：这是一个比"白名单过滤"强得多的保证。白名单可以被改；**"代码根本不存在"不能被执行**。

这个模式与第 8 章的 `Command` 没有"来源"字段是同一种手法：**把约束实现为"结构上不可能"，而不是"检查后拒绝"**。

### 「不提供批准、不提供自动批准」的三重实现

**第一重：没有批准工具。**

`SPECS` 里不存在 `approve_task`。测试 `test_bridge.py:193-195` 断言调用 `approve_task` 会报错**且 `fleet.calls == []`**——**一次网络请求都没发**。

**第二重：参数无法夹带。**

```python
# server.py:100
model_config = ConfigDict(extra="forbid", strict=True)
```

`create_task` 只接受 `request` + `adapter`（`:111-113`），只 `POST args.model_dump()`（`:317`）。

测试 `test_bridge.py:187-188` 分别注入：

| 注入 | 结果 |
| --- | --- |
| `{"approved": True}` | `INVALID_ARGUMENTS` |
| `{"auto_approve": True}` | `INVALID_ARGUMENTS` |
| `{"task_id": "../devices/arm-1/estop"}`（**路径穿越**） | `INVALID_ARGUMENTS`，**`fleet.calls == []`** |
| `{"url": "https://other.invalid"}` | `INVALID_ARGUMENTS`，**`fleet.calls == []`** |

**最后两行值得注意**：校验失败**不产生任何上游请求**。一个"先转发再校验"的实现会把这些恶意输入送到 Fleet——即使最终被拒绝，也产生了一次不必要的信任边界穿越。

**第三重：上游响应也要核对。**

即使 Fleet 返回成功，`server.py:318-325` 仍要求：

```python
data["approved"] is False
and data["state"] in {READY, WAITING_APPROVAL}
and isinstance(data["id"], str) and data["id"] != ""
```

否则抛 **`UNSAFE_TASK_RESPONSE`**：

> Fleet 没有确认任务处于未审批状态，立即到控制台核查。

测试 `test_bridge.py:216-226` 用"HTTP 201 + `{"approved": true, "state": "EXECUTING"}`"的假 Fleet 验证这道门会关。

**为什么需要第三重？** 因为前两重防的是"**客户端作弊**"，第三重防的是"**服务端行为变化**"。如果 Fleet 某天改成了自动批准，桥接会说"这不对"而不是默默地把一个已执行的任务报成待审批。

**教学要点**：这是一个"**不信任上游**"的例子。桥接不相信 Fleet 会遵守约定——它**核对**。

### 外部建的任务为什么一样要人工审批

MCP 用的是 Fleet **操作员 Bearer token**，走的是与人类控制台**完全相同**的 `POST /v1/tasks`。链条：

| 步 | 事实 | 位置 |
| --- | --- | --- |
| 1 | `createTask` 只解析 `request` + `adapter` 两个字段 | `fleet/server.go` |
| 2 | 建出的 `Task.State` 是 `StateReady`，**`Approved` 从未被赋值，取零值 `false`** | `tasks/service.go` |
| 3 | 第 1 版 revision 硬编码 `RiskClass: "physical", ApprovalRequired: true` | `tasks/service.go` |
| 4 | **`READY` 状态没有到 `EXECUTING` 的转移**，只有 `PLANNING` / `WAITING_APPROVAL` 有 | `core/taskgraph/state.go` |
| 5 | 全仓库**唯一**写 `Approved = true` 的地方在 `Approve()` 内部 | `tasks/service.go` |

**第 4 条是最强的保证**：它不是"审批检查失败就拒绝"，而是"**状态机里根本没有那条边**"。

**一个未审批的任务在结构上无法到达执行状态。**

### ⚠️ 一处必须标注的诚实边界

`fleet/coordinator/coordinator.go` 的 `NextIntent` **不读 `task.Approved`**。

（推断）也就是说：**云端派发路径实际上只有"入队前 + 派发前"两层门，协调器层没有独立的批准检查。**

这个缺口被第 4 条（状态机没有那条边）兜住了——因为状态到不了 `EXECUTING`，协调器也就不会去认领。**但它是一个"靠状态机兜住"而不是"靠显式检查"的保证**，性质不同。

**教学要点**：审计一个安全机制时，要问"**如果我把这个检查删掉，还有什么会拦住它**"。答案如果是"另一条路径上的检查"，那么这个保证是**间接的**——它依赖于那条路径的行为不变。

---

## 5.9 批准范围：一个比"批准"更细的问题

### 问题

操作员批准了一个恢复计划："重新观测 + 重做 `pick` 步骤"。

现在问：模型能不能在执行过程中**顺手**做一个计划里没写的 `place`？

**答案：不能。** 而且这个"不能"必须在代码里强制，不能靠提示词。

### 机制：`Scope`，而且它在批准**之前**检查

```go
// internal/actionloop/loop.go
// Scope reports whether a physical call is within what an operator approved.
type Scope func(tool Tool) bool

// ScopeOf builds a scope that admits exactly the named tools.
func ScopeOf(names ...string) Scope { ... }
```

判定入口（`:452-467`）：

```go
if needsScope(tool) && !l.withinScope(tool) {
	// Refused, recorded with the exact call, and escalated: the operator
	// needs to see what the model wanted, not that something was blocked.
	outcome.Rounds = append(outcome.Rounds, l.record(Round{
		Round: round, Tool: tool.Name, Candidates: names(l.Tools),
		Reason: decision.Reason, Arguments: decision.Arguments,
		Verdict: VerdictOutsideScope,
		Detail: fmt.Sprintf("%s 会动机器人，但不在已批准的范围内；需要重新批准后才能执行", tool.Name),
		ObservedAt: l.now(),
	}))
	outcome.Escalated = true
	outcome.Reason = fmt.Sprintf(
		"模型想执行 %s，它不在已批准的范围内。需要人工批准这一动作后才能继续。", tool.Name)
	return outcome, nil
}
```

### 三个设计细节

**细节 1：`Scope` 为 nil 时拒绝一切物理调用。**

```go
// internal/actionloop/loop.go
func (l Loop) withinScope(tool Tool) bool {
	if l.Scope == nil {
		return false
	}
	return l.Scope(tool)
}
```

**"没配置范围" ≠ "没有范围限制"，而是"什么都做不了"。** 这又是白名单默认拒绝的模式（与第 9 章的 `RecoveryAction.Executable()` 同源）。

**细节 2：只约束物理调用。**

```go
// internal/actionloop/loop.go
// needsScope reports whether a call is bounded by the approved scope.
//
// Only physical calls are. A read-only call cannot change the world, and a local
// side effect does not reach the robot; bounding those would stop the loop from
// even looking, which is not a safety property but an outage.
func needsScope(tool Tool) bool {
	return tool.SafetyLevel == skills.SafetyPhysical
}
```

那句注释值得逐字读：

> **约束那些调用会阻止循环连"看"都做不到，这不是安全属性，是停机。**

这是一个极其重要的区分。**安全机制的作用域必须精确——过宽的安全机制会变成故障源。** 第 4 章那个"安全检查变成陷阱"的例子（离墙 0.373 m vs 包络 0.375 m，差 2 mm，机器人再也动不了）是同一个道理。

**细节 3：范围检查在批准检查之前。**

代码顺序是：

```go
if needsScope(tool) && !l.withinScope(tool) { ... }   // :452  先查范围
if needsApproval(tool) { ... }                        // :469  再查批准
```

**这个顺序有语义**：**越界是一个比"没批准"更根本的拒绝**。

如果顺序反过来，一个越界的调用会先弹出批准窗口——操作员可能"顺手批了"一个**从未出现在原始批准范围内**的动作。而现在的顺序是：**越界直接升级，连问都不问。**

**"所以 Approver 说什么都不影响越界的结果"** —— 这个性质很重要，它意味着批准者不能通过"同意"来扩大自己的权限。

### 它是怎么接上生产的：一段值得全文引用的注释

`ScopeOf` 在**生产代码**里只有**一个**调用点——`internal/recoveryexec/executor.go`：

```go
Scope: actionloop.ScopeOf(request.Action.Tools...),
```

而它上面那段注释，是我在这个仓库里找到的**最好的防御性设计论证**：

```go
// A second line of defence, not the first.
//
// The first is that `tools` above contains only the action's declared
// names, so the decider is never offered anything else — it cannot choose
// what it cannot see, which is better than refusing after the fact.
//
// This bound is kept anyway because the two protect different changes. The
// tool list is what this function happens to pass; the scope is what was
// approved. If `resolve` is ever widened — "let the model see everything the
// robot offers" is a plausible edit — the list stops bounding anything and
// this is what still holds. Removing it would make the approval's meaning
// depend on how a list happens to be built.
```

拆开看这四段：

| 段 | 论点 |
| --- | --- |
| 1 | **这是第二道防线，不是第一道。** |
| 2 | 第一道是"**决策者看不到它就不能选它**"——**比事后拒绝更好** |
| 3 | 但第二道仍然保留，因为**两者防的是不同的改动**：工具列表防的是"这一次传了什么"，scope 防的是"**当时批准了什么**" |
| 4 | 如果哪天有人把 `resolve` 放宽成"让模型看到机器人提供的一切"，**工具列表就不再约束任何东西**，而 scope 仍然成立。删掉它，"批准的含义"就依赖于"一个列表恰好是怎么被构建的" |

**最后一句是这个论证的核心**：

> **"删掉它，会让批准的含义取决于一个列表恰好是怎么被构建的。"**

这是"**语义必须绑定到它自己的来源，而不是绑定到某个实现细节**"的一个精确表述。

**教学要点**：这是一段可以直接用于架构评审的模板。当有人问"这两道检查不是重复了吗"，正确的回答不是"多一道更安全"，而是**"它们防的是不同的改动"**。

### 拒绝时必须记录"模型想做什么"

注意 `VerdictOutsideScope` 那条记录里同时存了 **`Tool`** 和 **`Arguments`**。

注释写明了理由：

> 操作员需要看到**模型想要什么**，而不是"有东西被拦住了"。

**"被拦住了"是一个信息量为零的消息。** 它不告诉操作员该做什么。而"模型想执行 `place_object`，参数是 `{object: blue-cup, destination: sink}`"告诉了他一切。

**这和第 8 章那句"错误码是运维接口"是同一条原则。**

### ⚠️ 一处"已实现但未接线"的门

`agentruntime/permission.go` 里有一个 `RequestMutation`（`:85`）：

```go
func (o *Orchestrator) RequestMutation(ctx context.Context, request MutationRequest) (MutationDecision, error)
```

它问的是：**"这个 Agent 可以改东西吗？"**——基于 Agent 自己声明的权限（`MutatesWorld` / `MutatesTaskState`）。

**但它没有任何生产调用点。** 全仓库搜索 `RequestMutation`：

| 位置 | 类型 |
| --- | --- |
| `agentruntime/permission.go` | 定义与文档注释 |
| `agentruntime/orchestrator_test.go` | **8 处测试** |

**零处生产代码。**

这是一个"**可用但未接线**"的门——它有实现、有测试、有文档，但**没有任何一条真实路径经过它**。

**教学要点**：这是一个"**没通电**"的典型样本（第 15 章会专门讲这一类）。它比"没实现"更危险，因为它看起来像已实现的保护：

- 有类型 ✓
- 有测试 ✓
- 有文档 ✓
- **没有生产调用点 ✗**

**审计一个系统时，一个很好的问题是："如果我把这个机制的实现改成 `return nil`，会有任何测试失败吗？"** 如果答案是"只有单测失败，集成测试全绿"，那么这个机制就是没通电的。

---

## 5.10 与通用 coding agent 的差异

| 维度 | coding agent 的工具 | 机器人工具 | 代码事实 |
| --- | --- | --- | --- |
| **工具数量** | 少而通用（Read/Write/Edit/Bash/Grep…） | 31 个专用工具，5 级安全标注 | `tools.json` |
| **副作用声明** | 隐式（Write 会改文件，谁都知道） | **显式**：`mutates_world` 触发闭环证据门 | `tool_layer.py:544` |
| **幂等性** | 多数天然幂等（写同一个内容结果一样） | **显式标注**，且**多数物理工具不幂等**（13 个中只有 1 个标了 `idempotent`） | `tools.json` |
| **并发** | 按工作区、文件与外部资源约束 | **`mutates_world` 工具抢当前 Executor 的运动锁**，取不到即 `BUSY` | `tool_executor.py:207-215` |
| **超时** | 可以很长或没有 | **每个工具有 `timeout_s`**，且实际 = `min(调用方, 工具)` | `tool_executor.py:198-205` |
| **审批** | 通常无（或提示词请求） | **schema 强制**：`additionalProperties: false` 让模型传不进 `approval_id` | `test_execution_admission.py:22-26` |
| **可逆性** | 大多可逆（git checkout） | **刻意不标注**——因为不可判定 | 3.7 |
| **急停** | N/A | 有，且**永不从工具列表移除** | `tool_executor.py:192-197` |

### 差异 1 的深层含义：工具数量

coding agent 用**少数通用**工具覆盖一切：`Bash` 一个工具就能做几乎所有事。

机器人 agent 用**多数专用**工具，因为：

| 原因 | 说明 |
| --- | --- |
| **安全等级必须可标注** | `Bash` 的安全等级是什么？它取决于你敲了什么。**一个无法标注安全等级的工具，在这个系统里不能用** |
| **超时必须可预算** | 一次 `pick` 的合理超时是 30 秒，一次 `build_map` 是 30 分钟。通用工具无法给出 |
| **幂等性必须可声明** | 见差异 2 |
| **副作用必须可声明** | 见差异 3 |

**最根本的一条**：**通用工具的安全属性是不可判定的。** 你不能说"`Bash` 是 level 2"——因为 `ls` 是 level 0，`rm -rf /` 是灾难。

**所以机器人工具层被迫做成"一个动作一个工具"**——不是为了方便，是为了**让每个动作的安全属性可以被静态标注**。

### 差异 2 的深层含义：不幂等是常态

看 `tools.json` 里 13 个 `mutates_world=true` 的工具：

| 工具 | `idempotent` |
| --- | --- |
| `set_gripper_width` | **true** |
| 其余 12 个 | **false** |

**只有"设置夹爪宽度"是幂等的**——因为它是**设置一个值**，重复设置同一个值结果一样。

其余的：`navigate_to`（走两次到同一个地方？第一次就到了）、`grasp`（抓两次）、`place_object`（放两次）、`build_map`（建两次图）——**没有一个是幂等的**。

**这就是为什么第 3 章那套"结果未知禁止重试"的机制是必需的**：幂等性须按效果、参数和有效期定义；即使幂等重试也有成本。某些物理命令可设计为幂等，但不能假定任意抓取或相对运动可安全重放。

### 差异 3 的深层含义：并发

`tool_executor.py:207-215`：

```python
mutates_world → 取全局运动锁，取不到即 BUSY
```

**当前 Executor 一把运动锁**，用于串行化它负责的身体写动作。它不是跨进程、跨机器人锁，也不自动覆盖控制器后台行为。

**为什么这么粗？** 因为一台机器人只有一个身体。两个"改世界"的动作同时进行，意味着**两条运动链可能相撞**。

**这与 coding agent 的对比很鲜明**：coding agent 可以并行处理 10 个文件（文件之间独立），但**一台机器人不能同时走和抓**——至少不能在缺乏精细运动规划的情况下。

**教学要点**：锁的粒度由**物理约束**决定，不由"并发性能"决定。当你的资源是一个物理身体时，**粗粒度锁是正确选择**。

---

## 5.11 教学要点

### 一道可以现场做的实验

**实验：给工具加一个不该有的字段。**

1. 在 `robot/gateway/tangying_robot_gateway/tools/manipulation.py` 里给 `pick_object` 的 `parameters_schema` 加一个字段 `approval_id`；
2. 跑 `make lint`（会跑 `llm_tools.py --check`）；
3. 观察会发生什么；
4. 然后尝试从模型侧传 `approval_id`，观察 `test_execution_admission.py` 的行为。

**这个实验的价值在于**：它让学生看到"**模型不能自带批准**"不是一句提示词，是一个会失败的测试。

### 六道练习题

**题 1（两份目录）**

> 仓库里有两份工具目录：Python 侧的 `tools.json`（31 个，蛇形名）和 Go 侧的技能清单（12 个，带点名）。请回答：它们会在哪里汇合？为什么不能合并成一份？

<details>
<summary>答案要点</summary>

**汇合点**：`ExecuteSkill`（`proto/robot/v1/robot.proto`）。

**为什么不能合并**

1. **消费者不同**：Python 目录给**外部 function-calling 客户端**（OpenAI 兼容 schema）；Go 清单给**进程内 Go 规划器**。
2. **抽象层级不同**：`pick_object` 是一个**工具**（可能内部调用多个关节动作）；`manipulation.pick` 是一个**技能**（一个语义单元）。
3. **安全字段模型不同**：Python 侧 `safety_level` 有 5 档；Go 侧只有 3 档（`read_only` / `local_side_effect` / `physical_motion`）。强行合并会让 Go 侧承担它不需要的精度。

**但有一处真实的不一致值得指出**：`orchestration/llm.go` 送给模型的 `skillView` **只有 `name/description/safetyLevel/sideEffect/requiredParameters`——不含 `MutatesWorld`**。

也就是说：**「这一步做完必须拿新鲜证据确认」这条信息没有随清单一起给模型**（它仍被执行路径强制）。

**教学点**：**声明存在 ≠ 声明被送到决策者面前。**

</details>

**题 2（急停永远保留）**

> `tool_executor.py:192-197` 在"节点不健康"时会把工具从列表里移除，**但 `emergency_stop` 例外**。请解释这个例外的理由，并给出另外两个同类的设计。

<details>
<summary>答案要点</summary>

**理由**：一个"因为子系统不健康而从模型工具列表里消失"的急停工具，会让模型**在最需要停的时候没有停的能力**。

**原则**：**安全机制本身不能被它要保护的机制影响。**

**同类设计（项目里真实存在的）**

1. **能力摘除时急停幸免**：最严重的 `safety` 级故障会摘掉**所有**声明了依赖的能力，**唯一幸免的是声明"零依赖"的 `emergency_stop`**——"已经停住的机器人仍然必须能被停住"。
2. **`execute_for_test` 的急停分支抢在任何在途命令之前**（`service.py:315-321`）——急停不走正常的队列，它插队。
3. **限流不影响急停**：MCP 桥接的 `TANGYING_MCP_RATE_LIMIT` 对普通工具请求限流，**急停不受此预算阻挡**。

**加分点**：指出这和一个反模式的关系——"**当系统检测到异常时自动禁用某些功能**"这个做法，如果被禁用的是**恢复手段**，就会把系统锁死。

</details>

**题 3（`additionalProperties: false`）**

> 请说明为什么"模型不能自带批准"必须由 schema 的 `additionalProperties: false` 保证，而不能只靠提示词。并给出一条测试来验证它。

<details>
<summary>答案要点</summary>

**提示词为什么不够**：提示词是**请求**，模型可以不遵守。一个被注入的、或者仅仅是理解偏差的输出，可能包含 `approval_id`。

**schema 为什么够**：`additionalProperties: false` + `validate_arguments()` 在**进入 handler 之前**拒绝多余字段。这是**强制**，不是请求。

**测试**（项目里真实的，`test_execution_admission.py:22-26`）：

```python
result = executor.execute(ToolCall("move", {"approval_id": "invented"}))
assert result.code == "INVALID_PARAM"
assert handler_was_not_called
```

**关键是第二个断言**：不仅要求被拒绝，还要求 **handler 没有被调用**。一个"先调用再校验"的实现会通过第一个断言，但物理世界已经被动了。

**同类设计（项目里真实的）**

1. `Command` 结构体里**根本没有"来源"字段**（第 8 章）——不是"检查来源"，是"无法表达来源"。
2. 对账用 SQL 的 `WHERE reconcile_outcome = ''`，**不是应用层 if**（第 3 章）。
3. `step_runs` 的**部分唯一索引**保证幂等，不是应用层查重（第 9 章）。

**模式**：**能用约束表达的不变量，不要用检查。**

</details>

**题 4（没有 `reversible` 字段）**

> `RobotTool` 有 `mutates_world` 但没有 `reversible`。请说明为什么"可逆性"不应该标在工具上，并指出系统用什么替代了它。

<details>
<summary>答案要点</summary>

**为什么不该标**：可逆性**不是工具的属性，是"这一次执行 + 当前世界状态"的属性**。

举例：`place_object` 通常可逆（再拿起来），但如果把物体推进了窄缝、或者拿起来时会碰倒旁边的——**不可逆**。

**在工具上标 `reversible: true` 会给出虚假的保证。**

**系统的替代方案**：把问题**下推**到完成判据：

- 系统不问"这个动作可逆吗"；
- 它问"**这个动作做完之后，我怎么知道它做成了**"（`Gate`）。

**为什么这样更好**：

- 如果**做成了** → 不需要撤销；
- 如果**不知道做成了没有** → 不能重做（`UNKNOWN_OUTCOME`）。

**这两条覆盖了可逆性真正要防的东西，而且都是可判定的。**

**附加原则**：**不要标注不可判定的属性。** 一个标注了但不可判定的属性，比不标注更糟——因为它会让人以为这里有一道保护。

</details>

**题 5（只读性的定义）**

> MCP 桥接的测试断言"读一次调查 = **恰好一次** `GET /v1/mapping`"。请解释为什么一个只读工具需要"恰好一次"这么强的断言。

<details>
<summary>答案要点</summary>

测试注释给了答案：

> **一个只读工具如果偷偷发了第二个请求，就是一边声称观察一边行动。**

**为什么这是安全问题**：MCP 的整个设计前提是"外部 Agent 只能**观察**，不能**行动**"。如果一个名字叫 `get_*` 的工具实际上会发第二个请求（比如触发一次扫描），那么：

1. "只读工具不需要审批"这个前提破产；
2. 外部 Agent 就获得了**未审批的物理动作能力**；
3. 而且它是**隐藏的**——调用者从工具名上看不出来。

**所以只读性的判据不是"名字里有 get"，而是"发出的请求数等于它声称的数量"。**

**同类案例（真实存在的）**：`test_bridge.py:410-437` 不仅断言 `get_survey` 在目录里，还断言 `start_survey` / `stop_survey` / `finish_survey` / `move_robot` / `call_robot_service` **都不在**目录里——**藏对了东西和藏多了东西都要被测试拦住**。

**加分点**：指出"一个建图能自主驱动机器人二十分钟，所以启动权必须留在控制台"这个判断——**只读与只写的边界，应该按"副作用持续多久"来划，不只是"有没有副作用"**。

</details>

**题 6（安全等级的跨层降级）**

> 工具层的 `safety_level` 是 0–4 五档，但 `_safety_level_name` 把 level 1 和 level 2 都映射为 `"physical_motion"`。请说明这个降级带来的一个具体风险，以及如果你要修它，应该怎么改。

<details>
<summary>答案要点</summary>

**具体风险**：一个调用方如果用 `select(max_safety_level=1)` 拿到了"低速运动工具"的清单，它可能假定这些工具"比常规运动更安全"。但**传输到能力契约时，`move_arm_relative`（level 1）和 `navigate_to`（level 2）都变成了 `"physical_motion"`**——接收方**无法区分**。

也就是说：**"低速"这个语义在跨层时丢失了。**

**第二个风险**：Go 侧只有 3 档（`read_only` / `local_side_effect` / `physical_motion`），`needsApproval` 用的是 `==`。这意味着**level 1 和 level 4 在 Go 侧都是 `physical_motion`，都要求审批**——对于 `stop_navigation`（level 1）来说，要求审批是**过度保守**的（一个停止指令如果要等人批准，那它会等很久）。

**怎么改（几种思路）**

1. **扩协议**：把 `CapabilityInfo.safety_level` 改成数值字段（proto 已经是 string，可以加一个 `int32 safety_level_numeric`），保留字符串作为向后兼容的粗分类。
2. **显式声明丢失**：如果不能扩协议，就在文档里**逐项列出降级映射**，让调用方知道自己拿到了什么。
3. **按用途给不同的字段**：安全监督需要的是"是否物理动作"（4 档够了），工具过滤需要的是精确保序（要 5 档）。**给两个字段，各取所需。**

**加分点**：指出这类问题的通用形式——**精度在跨层传递时被降级，而降级如果不在文档里说明，接收方就会以为自己拿到的是全部信息。**

</details>

### 学生最容易误解的点

| # | 误解 | 纠正 |
| --- | --- | --- |
| 1 | "5 级安全标注是全系统的" | 工具层内部是 5 档；能力契约 4 档；**Go Agent 只有 3 档** |
| 2 | "`emergency_stop` 是只读工具，所以可以做" | 它 `mutates_world=false`，但 `safety_level=4`。**"改世界"与"需要证据"是两个独立属性** |
| 3 | "`stop_navigation` 是查询" | 它是 **level 1**（低速运动）——它是一个**动作** |
| 4 | "工具目录是手写的，改工具要改两处" | `tools.json` **从代码生成**，`--check` 断言逐字节相同。**同源生成减少手工漂移，跨版本与跨层仍需验证** |
| 5 | "模型不能自带批准，因为提示词里写了" | 提示词是**第二道**防线。第一道是 `additionalProperties: false` + `validate_arguments()` |
| 6 | "MCP 有 8 个工具" | **9 个**（文档过期，缺 `get_survey`） |
| 7 | "MCP 不暴露关节控制是因为做了过滤" | 是**拓扑事实**：这个进程里根本不存在指向 `arm.move` 的代码 |
| 8 | "有 `mutates_world` 标注就说明系统知道哪些动作危险" | `mutates_world` 只说"会改世界"，**不说"危险"**。危险程度是 `safety_level`。两者独立 |

---

## 5.12 本章小结

1. **工具目录是从代码生成的**，`--check` 断言逐字节相同。**"模型看到的工具"与"运行时能执行的工具"不可能不一致。**

2. **只有两个工具不给模型**：`move_arm_to_joints`（关节角）与 `navigate_to_pose`（位姿）。机制是**一个可见性字段 + 两道过滤**，不是白名单。而"模型不能自带批准"由 **schema 的 `additionalProperties: false`** 保证，不是提示词。

3. **`emergency_stop` 永远保留在工具列表里**，即使节点不健康。**安全机制本身不能被它要保护的机制影响。**

4. **5 级安全标注是工具层的内部刻度**，跨层后被降级为 4 档（能力契约）和 3 档（Go Agent）。这个降级是有意的，但必须在文档里说明。

5. **没有 `reversible` 字段是刻意的**——可逆性不是工具的属性。系统把它下推为"完成判据"：**如果做成了就不需要撤销，如果不知道做成了没有就不能重做。**

6. **MCP 只给 9 个工具，且不暴露关节控制**——这是**拓扑事实**（代码不存在），比白名单更强。

---

## 5.13 源码索引

### 目录与生成

| 内容 | 位置 |
| --- | --- |
| 目录生成 | `robot/gateway/tangying_robot_gateway/llm_tools.py` |
| 逐字节一致断言 | `tests/tool_layer/test_llm_tools.py` |
| `tools.json` 结构 | `tools.json`（`schema_version: llm.tools.v1`） |
| `RobotTool` 字段 | `robot/gateway/tangying_robot_gateway/tool_layer.py` |
| 注册表按条件装配 | `robot/gateway/tangying_robot_gateway/tools/__init__.py` |
| "交付缺口不是重试条件" | `robot/gateway/tangying_robot_gateway/tools/mapping.py` |

### 安全等级

| 内容 | 位置 |
| --- | --- |
| 五个等级定义 | `tool_layer.py:522-531` |
| 构造期强制 | `tool_layer.py:559-570`；`tests/tool_layer/test_tool_contract.py` |
| **跨层降级为 4 档** | `tool_layer.py:620-627` |
| 选择期过滤 | `tool_layer.py:691-704` |
| Go 侧只有 3 档 | `core/skills/manifest.go` |
| Go 清单校验 | `core/skills/manifest.go` |
| 规划校验四条 | `core/guard/guard.go` |
| 审批从安全级派生 | `internal/actionloop/loop.go` |
| 安全监督最终软件安全准入点 | `robot/gateway/tangying_robot_gateway/safety.py` |

### 不暴露给模型的工具

| 内容 | 位置 |
| --- | --- |
| 声明字段 | `tool_layer.py:551-554,569-570` |
| 标记的两行 | `robot/gateway/tangying_robot_gateway/tools/navigation.py`；`robot/gateway/tangying_robot_gateway/tools/manipulation.py` |
| 导出期过滤 | `llm_tools.py:64,81-85` |
| 广告期过滤（急停例外） | `tool_executor.py:256-263,192-197` |
| 测试锁定 | `tests/tool_layer/test_llm_tools.py`；`test_tool_selection.py:148-154` |
| schema 挡住夹带 | `tool_executor.py:268`；`tests/tool_layer/test_execution_admission.py` |
| 提示词禁令 | `orchestration/llm.go` |

### 执行通道

| 内容 | 位置 |
| --- | --- |
| `Executor.execute` | `tool_executor.py:176-215` |
| 重试预算 | `tool_executor.py:224-253,305-314` |
| 全局运动锁 | `tool_executor.py:207-215` |
| handler 边界 | `tool_layer.py:572-583` |
| `Command` 构造（19 字段） | `robot/gateway/tangying_robot_gateway/gateway_adapter.py` |
| 幂等 journal | `robot/gateway/tangying_robot_gateway/service.py` |
| 急停插队 | `service.py:315-321` |
| RPC 集合不变量 | `tests/contract/test_proto_schema.py` |

### 两份目录

| 内容 | 位置 |
| --- | --- |
| Go 技能清单 12 项 | `skills/manipulation/plugin.go` |
| `skillView` 只有五字段 | `orchestration/llm.go` |

### MCP

| 内容 | 位置 |
| --- | --- |
| 9 个工具定义 | `robot/mcp/tangying_mcp/server.py` |
| 严格参数模型 | `server.py:100,111-113` |
| 上游响应核对 | `server.py:318-326` |
| 测试锁定 | `tests/mcp/test_bridge.py` |
| `get_survey` 的加入理由 | `artifacts/release-preparation/v0.7.0/CHANGELOG.before.md` |

### 审批链

| 内容 | 位置 |
| --- | --- |
| `createTask` 只解析两个字段 | `fleet/server.go` |
| `Task.Approved` 零值 | `tasks/service.go` |
| **`READY` 没有到 `EXECUTING` 的边** | `core/taskgraph/state.go` |
| 唯一写 `Approved = true` 的地方 | `tasks/service.go` |
| 真机审批门禁 | `safety.py:117-118` |

---

**上一章**：[第 4 章 世界模型](../chapters/ch04-world-model.md) · **下一章**：[第 6 章 多 Agent 运行时](../chapters/ch06-multi-agent-runtime.md) —— 为什么不能只做一个万能 Agent？"只提议不执行"如何在代码里保证？
