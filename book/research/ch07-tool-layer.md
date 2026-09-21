# 第 7 章核心素材：机器人工具层、工具目录、安全标注、权限与审批、MCP 桥接、模型能力边界

> 研究基线：仓库根 `VERSION` = `0.6.0`，HEAD = `774bd2a2f`。所有事实来自当前源码；`tools.json` 已用
> `.venv/bin/python -m tangying_robot_gateway.llm_tools --check` 实测为**与代码同步**（输出：`tools.json is up to date (29 tools)`）。
> 文中所有「文档与代码冲突」均已显式标注；推断性内容标注「(推断)」。

---

## 0. 先对账：四个被反复引用的数字全都过期了

本章写作中最重要的发现是：**关于工具层的公开叙述与代码事实已经系统性漂移**。下表全部实测。

| 口径 | 代码事实（实测） | 文档/README 的说法 | 判定 |
| --- | --- | --- | --- |
| 注册工具总数 | **31** | `docs/development/robot-tool-layer.md:58` 说「27 个工具」 | 文档过期（差 4） |
| 默认提供给 LLM | **29** | 同上「25 个默认提供给 LLM」；`README.md:137` 说「28 个工具」 | 文档过期 |
| 不提供给 LLM | **2** | 一致 | ✅ |
| MCP 桥接工具 | **9** | `robot/mcp/README.md:33-42` 表格 8 个；`2026-09-11-...-adr.md:18` 说「8 个统一工具」 | 文档过期（新增 `get_survey`） |
| `mutates_world=true` | **13**（29 个公开工具） | `2026-09-18-system-review-and-improvement-plan.md:272` 说「14 个」 | 数字随工具增删变化 |
| 安全等级分布 | `{0:11, 1:2, 2:6, 3:6, 4:4}` | 同上说 `{0:11, 1:2, 2:7, 3:6, 4:4}` | 差在 level 2 少 1 |
| Go `SkillManifest` 目录（模型规划用的另一份目录） | **12** | — | 见 §4.3 |
| `contracts.TOOL_PARAMETERS` 线协议参数模型 | **13** | `adr.md:12` 说「12 个 Pydantic 模型」 | 文档过期 |

`tools.json` 的 `tools` 数组 29 项、`metadata` 31 项、`excluded_from_llm` 2 项——
**31 = 29 + 2**，这个恒等式是理解全部机制的关键（`tools.json:1145`；生成逻辑 `llm_tools.py:83-85`）。

---

## 1. 工具目录的完整事实

### 1.1 权威声明在哪

`tools.json` **不是**手写契约，而是**从代码生成的产物**：`llm_tools.py:55-86` 的 `build_catalog()` 遍历
`ToolRegistry`，调用每个 `RobotTool.openai_schema()`（`tool_layer.py:607`）导出，并附带 `metadata` 边车。
`llm_tools.py:129-151` 提供 `--write` / `--check`；CI 与 `make lint` 跑 `--check`，
`tests/tool_layer/test_llm_tools.py:82-88` 断言磁盘文件与生成结果逐字节相同。
**因此「模型看到的工具」与「运行时能执行的工具」不可能不一致** —— 这是本章最值得讲的一条工程决策。

### 1.2 按能力域分类（我的口径，与仓库自带的 12 类口径并列）

用户给出的九个域里，**「验证」「标定」「恢复」三个域在 `tools.json` 里是空的**——这不是遗漏，是本设计最核心的取舍（详见 §7.5）。

| 能力域 | 数量 | 工具名 |
| --- | --- | --- |
| 感知 | 6 | `capture_image`、`detect_object`、`get_object_pose`、`get_robot_status`、`resolve_location`、`scan_environment` |
| 本体/世界状态查询 | 3 | `get_arm_state`、`get_current_pose`、`get_gripper_state` |
| 导航 | 5 | `navigate_to`、`navigate_to_pose`※、`explore_for`、`stop_navigation`、`navigate_to_work_area` |
| 机械臂运动 | 4 | `move_arm_to_pose`、`move_arm_to_joints`※、`move_arm_relative`、`home_arm` |
| 夹爪与接触 | 4 | `grasp`、`release`、`set_gripper_width`、`get_gripper_state` |
| 复合抓放技能 | 3 | `pick_object`、`place_object`、`fetch_object` |
| 安全 | 4 | `emergency_stop`、`reset_safety_stop`、`set_speed_limit`、`check_collision` |
| 建图 / 工作区 | 3 | `build_map`、`plan_work_area`、`recall_object` |
| **验证** | **0** | `verify_grasp` / `verify_placement` / `verify_arrival` 只存在于 Go `SkillManifest` 目录，不是 LLM 工具 |
| **标定** | **0** | `calibration.py` / `calibration_wizard.py` / `calibration_solver.py` 是网关模块，未注册为工具 |
| **恢复** | **0** | `recover_to_safe_pose` 是 runtime 技能（`safety.py:23`），不是工具；`reset_safety_stop` 归安全域 |

※ = `llm_visibility="fallback"`，不给模型（§3）。

**计数说明**（避免读者对不上 29）：`get_gripper_state` 同时属于「本体查询」与「夹爪」，上表只在本体域计一次；
带 ※ 的 2 个不在 29 个公开工具内。于是 6+3+4+3+3+3+4+3 = **29**，
加上 2 个 fallback 得 **31** 个已注册工具，与 §0 的对账一致。

仓库自己还有一套 12 类口径（`docs/architecture/agent-evaluation-system.md:89-104`：感知、状态查询、建图、
语义解析/记忆、探索、导航、作业区规划、手臂运动、夹爪、抓放、组合任务、安全），两套口径可以互相校验：
分类总数一致，都能覆盖 29 个工具。

### 1.3 路由维度：`distributed_node`（工具层自己的分布式分类）

`RobotTool.distributed_node`（`tool_layer.py:549`）既是分类也是路由键——`ToolExecutor` 用它做健康检查与
`UNREACHABLE` 判定（`tool_executor.py:139`、`:192-197`）。29 个公开工具的分布：

`robot.perception` 6、`robot.nav` 4、`robot.arm` 4、`robot.gripper` 4、`robot.safety` 4、`robot.skill` 4、`robot.map` 3。
含 fallback 的 31 个工具时：`robot.nav` 5、`robot.arm` 5。

### 1.4 每个工具的字段结构

`RobotTool` 是 `@dataclass(frozen=True)`（`tool_layer.py:534`），字段与在 `tools.json` 中的落地位置：

| 字段 | 定义处 | `tools.json` 中的位置 | 语义 |
| --- | --- | --- | --- |
| `name` | `tool_layer.py:535` | `tools[].function.name` | 蛇形、**禁止含点号**（`:560`） |
| `description` | `:536` | `tools[].function.description` | 何时用 + 前置条件 + 恢复路径；导出时统一追加 `_GLOBAL_NOTES`（`llm_tools.py:32-37`） |
| `parameters_schema` | `:537` | `tools[].function.parameters` | JSON Schema，全部 `additionalProperties: false` |
| `returns_schema` | `:538` | `metadata.*.returns` | 只取 `properties` |
| `safety_level` | `:539` | `metadata.*.safety_level` | 0–4，构造期强制 `0 ≤ lvl ≤ 4`（`:561-562`） |
| `timeout_s` | `:540` | `metadata.*.timeout_s` | 必须为正（`:563-564`） |
| `distributed_node` | `:541` | `metadata.*.distributed_node` | 路由与健康分组，不得为空（`:566-567`） |
| `idempotent` | `:542` | `metadata.*.idempotent` | 决定能否自动重试（`tool_executor.py:305-314`） |
| `mutates_world` | `:544` | `metadata.*.mutates_world` | 默认 `False`；为真时禁止重试且必须过闭环证据门 |
| `llm_visibility` | `:554` | `excluded_from_llm` | `"primary"` / `"fallback"`，非法值构造期报错（`:569-570`） |
| `handler` | `:543` | 不导出 | 唯一碰硬件的入口函数 |

**没有** `reversible`（可逆性）字段——这是一个刻意的空缺，见 §7.2。

### 1.5 注册表是动态的、有条件的

`build_registry()`（`tools/__init__.py:42-76`）按组装配；三处值得注意：

1. `build_object_memory_tools(object_recall)`（`:65`）与 `build_mapping_tools(map_catalog, planning_context, ensure_mapping)`（`:71`）
   带着外部 provider 进来。`mapping.py:75-80` 对 `ensure_mapping is None` 的部署返回
   `UNREACHABLE` + 「这是交付缺口，不是可重试条件」，**而不是**不注册工具——注释写明了理由：
   「调用方必须能发现作业区规划存在，缺 provider 的部署应得到明确拒绝，而不是一个与拼写错误无法区分的缺失工具」。
2. 复合技能**最后**构造（`:73-75`），它们只能调用已注册的原子工具。
3. **文档冲突**：`robot-tool-layer.md:58` 说 `plan_work_area` 「仅当同时收到 `map_catalog` 与 `planning_context` 时才注册」，
   但 `tools/__init__.py:67-71` 的注释明确写「**Always present**」。代码已改，文档未跟上。

---

## 2. 5 级安全标注

### 2.1 定义（代码事实）

`tool_layer.py:522-531`：

```python
class SafetyLevel(int):
    """Safety classification, ordered so a supervisor can compare numerically."""
    QUERY = 0            # 查询：不改变任何状态
    LOW_SPEED_MOTION = 1 # 低速运动
    NORMAL_MOTION = 2    # 常规运动
    CONTACT = 3          # 接触物体
    SAFETY = 4           # 安全相关
```

它是 `int` 的子类，唯一目的是**可数值比较**——`ToolRegistry.select(max_safety_level=...)`（`tool_layer.py:691-704`）
因此可以一句话完成「只给我不超过某危险等级的工具」的过滤。

### 2.2 每一级有哪些工具（29 个公开工具）

| 级别 | 名称 | 数量 | 工具 |
| --- | --- | --- | --- |
| 0 | `QUERY` | 11 | `capture_image`、`detect_object`、`get_arm_state`、`get_current_pose`、`get_gripper_state`、`get_object_pose`、`get_robot_status`、`plan_work_area`、`recall_object`、`resolve_location`、`scan_environment` |
| 1 | `LOW_SPEED_MOTION` | 2 | `move_arm_relative`、`stop_navigation` |
| 2 | `NORMAL_MOTION` | 6 | `build_map`、`explore_for`、`home_arm`、`move_arm_to_pose`、`navigate_to`、`navigate_to_work_area` |
| 3 | `CONTACT` | 6 | `fetch_object`、`grasp`、`pick_object`、`place_object`、`release`、`set_gripper_width` |
| 4 | `SAFETY` | 4 | `check_collision`、`emergency_stop`、`reset_safety_stop`、`set_speed_limit` |

（含 fallback 的 31 个工具时，level 2 变为 8：多出 `move_arm_to_joints`、`navigate_to_pose`。全部取值的完整分布见 `tools.json:699-1133`。）

### 2.3 **重大准确性问题：5 级标注对外只投影出 4 个名字**

`tool_layer.py:620-627`：

```python
def _safety_level_name(level: int) -> str:
    if level >= SafetyLevel.SAFETY:          return "safety_critical"   # ≥4
    if level >= SafetyLevel.CONTACT:         return "physical_contact"  # ≥3
    if level >= SafetyLevel.LOW_SPEED_MOTION:return "physical_motion"   # ≥1
    return "read_only"                                                   # 0
```

**level 1 与 level 2 导出为同一个字符串 `"physical_motion"`。** 该函数是 `RobotTool.capability_info()`
（`tool_layer.py:585-606`）的输出，也就是运行时能力契约 `Capability.safety_level`（`runtime.py:24`）的实际值。
后果链条：

- `robot/gateway` 经 gRPC 声明的 `CapabilityInfo.safety_level`（`proto/robot/v1/robot.proto:58`）只有 4 个取值；
- `safety.py:104` 用它判定「是否物理动作」：`item.safety_level == "physical_motion"`；
- `cmd/edge-worker/main.go:249` 也按 `"physical_motion"` 分支；
- Go 侧 `core/skills/manifest.go:10-14` 的 `SafetyLevel` 更是**只有 3 档**：`read_only` / `local_side_effect` / `physical_motion`。

所以：**「5 级安全标注」是工具层内部的排序刻度；系统其余部分（能力契约、安全监督、Go 清单）看到的是 4 档，
Go Agent 看到的是 3 档。** 把它讲成「全系统 5 级」是不准确的。`artifacts/marketing/03-工具调用/素材来源与数字出处.md:102`
的表述（「5 级」）只对 `tools.json` 的 `metadata.safety_level` 成立。

### 2.4 代码里如何强制这些标注（不是文档承诺）

七处强制点，从定义期到执行期：

1. **构造期**（`tool_layer.py:559-570`）：`0 ≤ safety_level ≤ 4`，否则 `ValueError`；
   `timeout_s > 0`；`distributed_node` 非空；`llm_visibility ∈ {primary, fallback}`。
   `tests/tool_layer/test_tool_contract.py:180-196` 逐条参数化断言这些拒绝，包括 `{"safety_level": 9}`。
2. **注册期**（`tool_layer.py:658-663`）：同名重复注册直接 `ValueError`，不允许静默覆盖
   （重载走显式的 `replace()`，`:665-669`）。
3. **选择期**（`tool_layer.py:704`）：`max_safety_level` 过滤，`test_tool_contract.py:285` 断言
   `select(max_safety_level=QUERY)` 只返回 `get_current_pose`。
4. **Go 清单校验**（`core/skills/manifest.go:47-71`）：`physical_motion` 必须同时满足
   `SideEffect=true`、`DefaultLeaseMS != 0`、`AllowedSafetyProfiles` 非空；
   且 `MutatesWorld && !SideEffect` 是硬错误（`:68-70`，理由写在注释：「告诉调用方不需要确认，而闭环门禁却在等一份永远不会到的证据」）。
5. **规划校验**（`core/guard/guard.go:53-66`）：物理步骤没有 lease、deadline 已过、没有 `ApprovalID`、
   没有 `IdempotencyKey`，四条各自独立报错。
6. **执行期审批派生**（`internal/actionloop/loop.go:620-626`）：
   ```go
   // needsApproval reports whether the manifest requires a person before this call.
   // It is derived from the safety level rather than asked of the tool, so a tool
   // cannot opt itself out of approval by claiming it does not need one.
   func needsApproval(tool Tool) bool { return tool.SafetyLevel == skills.SafetyPhysical }
   ```
   注意其判定是**等于** `physical_motion`，不是「≥某级」——因为 Go 侧只有 3 档。
7. **安全监督**（`safety.py:78-130`）：`evaluate()` 是唯一否决点，物理动作进入
   `if physical and not command.approval_id: return SafetyDecision(False, "APPROVAL_REQUIRED")`（`:117-118`）。

---

## 3. 不暴露给模型的工具

### 3.1 完整清单：只有 2 个

`tools.json:1145`：`excluded_from_llm: ["move_arm_to_joints", "navigate_to_pose"]`。
即「关节角」与「世界坐标位姿」两个坐标级工具。**没有第三个。**
（`README.md:138`、`docs/development/robot-tool-layer.md:69`、`adr.md:44` 的说法与代码一致。）

### 3.2 机制：不是白名单，不是另一条注册路径，而是「同一个注册表 + 一个可见性字段 + 两道过滤」

`adr.md:44` 记录了设计意图：工具**保留**为「底层兜底」，但标记为不给 LLM——
「这样『底层兜底可选、LLM 默认看不到』两个要求同时成立，而不是假装它不存在。」落到代码：

1. 字段声明：`tool_layer.py:551-554`，默认值 `"primary"`：
   ```python
   # "primary" tools are advertised to an LLM; "fallback" tools exist for
   # diagnostics and coordinate-level recovery but are not offered by default.
   llm_visibility: str = "primary"
   ```
2. 值校验：`tool_layer.py:569-570`，非法值（如 `"hidden"`）构造期报错。
3. 标记位置（**只有这两行**）：
   - `tools/navigation.py:267` → `navigate_to_pose` 的 `llm_visibility="fallback"`
   - `tools/manipulation.py:225` → `move_arm_to_joints`
4. **过滤点 1（导出期）**：`llm_tools.py:64` `for tool in registry.select(llm_only=True)`——
   过滤实现体在 `tool_layer.py:706`：`if llm_only and tool.llm_visibility != "primary": continue`。
   `metadata` 用 `llm_only=False` 遍历（`llm_tools.py:81`），所以两个工具**仍在 `metadata` 里**，
   只是不在 `tools` 数组里；`excluded_from_llm` 由 `llm_tools.py:83-85` 显式列出。
5. **过滤点 2（广告期）**：`tool_executor.py:256-263` 的 `Executor.openai_tools(llm_only=True)`
   同样走 `registry.select(llm_only=llm_only)`，再叠加「节点不健康则移除」——但 `emergency_stop` 例外，永远保留。
6. 测试锁定：`test_llm_tools.py:73-79` 同时断言「两个名字不在 `tools` 里」「`excluded_from_llm` 恰好是这两个」
   「语义入口 `navigate_to`/`move_arm_to_pose`/`pick_object` 在」；
   `test_tool_selection.py:148-154` 断言 fallback「可被编排调用，但不出现在 `openai_tools()` 里」。

**因此答案是：过滤 + 单一可见性字段，不是名字白名单，也不是第二条注册路径。**
两个工具和其余 29 个走完全相同的 `RobotTool` → `ToolRegistry` → `ToolExecutor` → `ExecuteSkill` 路径，
一个字符的差别只在 `llm_visibility`。

### 3.3 补一层：模型即使知道名字也传不进安全字段

工具的 `parameters_schema` 全部 `additionalProperties: false`，`ToolExecutor.validate_arguments()`
（`tool_executor.py:268`）在进入 handler **之前**校验。`tests/tool_layer/test_execution_admission.py:22-26`
断言 `ToolCall("move", {"approval_id": "invented"})` 返回 `INVALID_PARAM` 且 handler 未被调用。
`orchestration/llm.go:153` 的规划提示词也显式禁止模型输出安全字段：
「Do not include safety fields (approvalId, deadlineUnixMs, leaseMs, idempotencyKey, safetyLevel); the Robot Runtime fills them.」
**「模型不能自带批准」在工具层是 schema 事实，不是提示词请求。**

---

## 4. 统一执行通道

### 4.1 完整调用链（模型 → 物理执行）

```
① 模型 function calling
      │  工具定义来自 tools.json（由 llm_tools.py:55-86 生成）
      ▼
② Executor.execute(call)                      tool_executor.py:176
      ├─ 未注册 → NOT_FOUND（含 known_tools 列表）           :181-185
      ├─ schema 校验（additionalProperties=false）           :187-189 → :268
      ├─ 节点不健康 → UNREACHABLE（emergency_stop 除外）      :192-197
      ├─ timeout = min(call.timeout_s, tool.timeout_s)      :198-205
      ├─ 预取消 → CANCELLED（emergency_stop 除外）           :205-206
      └─ mutates_world → 取全局运动锁，取不到即 BUSY          :207-215
      ▼
③ _execute_with_retry                         tool_executor.py:224-253
      attempts = 2 if _retry_allowed(tool) else 1           :225 → :305-314
      ▼
④ tool.execute(**args) → handler              tool_layer.py:572-583
      handler 抛异常 → HARDWARE_ERROR（:575-577）；返回非 ToolResult → HARDWARE_ERROR（:578-581）
      ▼
⑤ adapter.execute(skill, parameters, context)     gateway_adapter.py:76-125
      ├─ 适配器必须是 RobotRuntimeService，裸 backend 直接 TypeError  :60-64
      ├─ lease_ms = min(budget,60s)*1000；deadline = now+budget       :98,:113
      └─ 构造 Command(schema_version, command_id, task_id,
            capability=skill, target_ref, parameters,
            deadline_unix_ms, lease_ms, idempotency_key,
            safety_profile, approval_id, robot_id,
            catalog_revision, world_revision_basis,
            resource_id, fencing_token)                       :106-124
      ▼
⑥ RobotRuntimeService.invoke(command)         service.py:267-295
      （复用 RPC 的同一条准入与 journal 路径；注释：「围绕同一硬件另建 supervisor 会分裂所有权」）
      ▼
⑦ execute_for_test → 唯一安全入口               service.py:307-343
      ├─ emergency_stop 分支：直接 safety.emergency_stop()，抢在任何在途命令之前   :315-321
      ├─ 每机器人执行锁非阻塞获取，失败 → ROBOT_BUSY / IDEMPOTENCY_CONFLICT /
      │  EXECUTION_OUTCOME_UNKNOWN                                              :324-330
      ├─ journal 幂等查表（conflict / pending / reconciled / replay 四态）        :345-360
      └─ decision = self.safety.start(command)   ← 唯一否决点                    :385
      ▼
⑧ SafetySupervisor.evaluate                    safety.py:78-130
      estop 锁存 → EMERGENCY_STOP_LATCHED；忙 → ROBOT_BUSY；schema/task_id/command_id
      → deadline → lease（0/过长）→ idempotency_key → safety_profile →
      **physical && !approval_id → APPROVAL_REQUIRED** → 参数校验 → ALLOWED
      ▼
⑨ backend.execute(command) → 硬件 / 仿真
```

关键设计事实：**第 ③ 步的重试在第 ⑤ 步之前决定**，第 ⑦ 步的幂等 journal 在第 ⑧ 步的安全准入之前查表，
第 ⑧ 步是唯一能否决的层。工具层**不实现**限幅、急停或租约（`safety.py:1-13` 与
`tool_layer.py:1-24` 的模块 docstring 都写明了）。

### 4.2 「不新增执行通道」的验证点

`tests/contract/test_proto_schema.py:28` 断言 gRPC 服务方法集合恰好是
`{GetRuntimeInfo, Observe, ExecuteSkill, Cancel, EmergencyStop, ListServices, CallService}`——
工具层没有新增任何 RPC。工具层新增的 31 个名字全部编译进既有的 `ExecuteSkill`
（`proto/robot/v1/robot.proto:12`，ROS 2 侧同构 `ExecuteSkill.action`，`adr.md:11`）。

### 4.3 必须澄清的边界：仓库里有**两份**工具目录

| | Python 工具层 | Go 技能清单 |
| --- | --- | --- |
| 文件 | `tools.json` / `tools/*.py` | `skills/manipulation/plugin.go:21-60` |
| 名字形态 | `pick_object`（蛇形无点） | `manipulation.pick`（带点） |
| 条目数 | 31（29 公开） | 12 |
| 消费者 | 外部 function-calling 客户端 / `executor.openai_tools()`（**仓库内无生产调用点**，只有测试与文档示例） | 进程内 Go 规划器 `orchestration/llm.go:139-175` |
| 字段 | safety_level 0–4、mutates_world、idempotent、timeout_s | SafetyLevel 3 档、SideEffect、MutatesWorld、ApprovalPolicy、DefaultLeaseMS、AllowedSafetyProfiles |

两者在 `ExecuteSkill` 处汇合。**但 `orchestration/llm.go:178-198` 送给模型的 `skillView` 只有
`name/description/safetyLevel/sideEffect/requiredParameters`——不含 `MutatesWorld`。**
也就是说：Go 规划器看到的技能清单里，「这一步做完必须拿新鲜证据确认」这一条**没有随清单一起给模型**
（它仍被执行路径强制）。这是一个具体、可验证的教学案例：**声明存在 ≠ 声明被送到决策者面前**。

---

## 5. MCP 桥接的边界

### 5.1 实际是 9 个工具，不是 8 个

代码事实 `robot/mcp/tangying_mcp/server.py:157-199` 的 `SPECS` 字典有 9 项，
`tests/mcp/test_bridge.py:22-27` 的 `TOOLS` 集合也断言 9 个：

| # | 工具 | 参数模型 | Fleet 端点（`server.py:300-317`） | 读/写 | 幂等 |
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

**文档冲突**：`robot/mcp/README.md:33-42` 的表格只有 8 行，缺 `get_survey`；
`docs/superpowers/specs/2026-09-11-...-adr.md:18` 也写「8 个统一工具」。
新增原因见 `artifacts/release-preparation/v0.7.0/CHANGELOG.before.md:617`：
「MCP 侧只加一个只读工具 `get_survey`，没有 `start_survey` / `stop_survey` / `finish_survey`」——
**一次建图自主驱动机器人二十分钟，启动权留在控制台操作员手里。**
`tests/mcp/test_bridge.py:410-437` 把这条写成测试：不仅断言 `get_survey` 在目录里，
还断言 `start_survey`/`stop_survey`/`finish_survey`/`move_robot`/`call_robot_service` **都不在**目录里，
并核对「读一次调查 = 恰好一次 `GET /v1/mapping`」——「一个只读工具如果偷偷发了第二个请求，就是一边声称观察一边行动」。

### 5.2 「为什么只给这么少」的代码依据

桥接能触达的全部能力是 `server.py:300-317` 那 9 个 HTTP 调用，**没有一条通往机器人 gRPC/ROS 2**。
因此「不暴露关节控制」不是过滤，而是**拓扑事实**：这个进程里根本不存在指向 `arm.move` / `action_chunk` 的代码。
`server.py:1` 的模块 docstring 就是一句话：「A bounded stdio MCP facade; approval and physical execution stay in Fleet.」

### 5.3 「不提供批准、不提供自动批准」的三重实现

1. **没有批准工具**：`SPECS`（`:157-199`）里不存在 `approve_task`；
   `test_bridge.py:193-195` 断言调用 `approve_task` 会报错且 `fleet.calls == []`（一次网络请求都没发）。
2. **参数无法夹带**：`Arguments` 基类 `model_config = ConfigDict(extra="forbid", strict=True)`（`server.py:100`），
   `create_task` 只接受 `request` + `adapter`（`:111-113`），且只 `POST args.model_dump()`（`:317`）。
   `test_bridge.py:187-188` 分别注入 `{"approved": True}` 与 `{"auto_approve": True}`，两者都得到 `INVALID_ARGUMENTS`。
   同一测试还注入 `{"task_id": "../devices/arm-1/estop"}`（路径穿越）与 `{"url": "https://other.invalid"}`，
   结果是 `fleet.calls == []`——**校验失败不产生任何上游请求**。
3. **上游响应也要核对**：即便 Fleet 返回成功，`server.py:318-325` 仍要求
   `data["approved"] is False` **且** `state ∈ {READY, WAITING_APPROVAL}` **且** `id` 是非空字符串，
   否则抛 `UNSAFE_TASK_RESPONSE`（「Fleet 没有确认任务处于未审批状态，立即到控制台核查」）。
   `test_bridge.py:216-226` 用「201 + `{"approved": true, "state": "EXECUTING"}`」的假 Fleet 验证这道门会关。
   成功结果的 `approval_required` 字段由 `server.py:326` 置为 `operation == "create_task"`。

### 5.4 外部建的任务为什么一样要人工审批（端到端证据）

MCP 用的是 Fleet **操作员 Bearer token**（`server.py:253`），走的是与人类控制台**完全相同**的
`POST /v1/tasks`。链条：

1. `fleet/server.go:442-461 createTask` 只解析 `request` + `adapter` 两个字段；
2. `tasks/service.go:168-207 Create` 建出的 `Task` 的 `State` 是 `taskgraph.StateReady`
   （`core/taskgraph/state.go:6` = `"READY"`），**`Approved` 字段从未被赋值，取零值 `false`**（`:185-197`）；
   第 1 版 revision 硬编码 `RiskClass: "physical", ApprovalRequired: true`（`tasks/service.go:202`）；
3. `READY` 状态**没有**到 `EXECUTING` 的转移（`core/taskgraph/state.go:27`），
   只有 `PLANNING` / `WAITING_APPROVAL` 有（`:30-31`）；
4. 全仓库唯一写 `Approved = true` 的地方是 `tasks/service.go:622`，在 `Approve()` 内部，
   而 `Approve()` 第一行就要求非空 actor（`:613-615`）；
5. `fleet/server.go:488-508 approveTask` 的 actor 是 `"fleet:"+operatorSubject(r)`——
   必须是 operator 凭据；`/v1/tasks/{id}/approve` **不在** `deviceRoutes` 里（`fleet/auth/auth.go:233-241`），
   设备凭据得到 403；
6. 审批通过后才 `s.queues.Enqueue`（`fleet/server.go:498-504`）——**这是 `fleet/` 里唯一的入队调用点**；
7. 最终逐步骤的门：`edge/agent/runner.go:401-402`
   ```go
   if physical && !task.Approved {
       return fmt.Errorf("%w: %s", ErrApprovalRequired, step.ID)
   }
   ```
   正好位于 `CommandForTaskStep`（`:407`）之前，即真实派发之前。

**所以「外部 Agent 建的任务一样要人工审批」不是策略，是四道彼此独立的结构事实的叠加：
零值 `Approved=false` + 状态机不允许直接执行 + 唯一写批准点要求 actor + 派发前逐步骤检查。**

还有两个必须一起讲的旁证与缺口：

- **存在第二条创建/审批实现**：控制台侧是 `console/server.go:323`（create）与 `:372`（approve），
  与 Fleet 共用同一个 `tasks.Service`，但审批凭据是**进程内会话**而非账号：
  `console/guard.go` 的 `operatorEvidence(r)` 生成 `console-session:<sha256[:12]> from <addr> at <ts>`。
  Fleet 侧只有 `Role == "operator"` 一种角色，**没有更细的 approver / admin 角色**（`fleet/auth/auth.go:246-300`）。
- **（推断）一处真实缺口**：`fleet/coordinator/coordinator.go:427 NextIntent` 取回任务后
  **不读 `task.Approved`**，认领时直接 `advanceTaskState(..., StateExecuting)`（`:528`、`:542-558`），
  而 `READY → OBSERVING → PLANNING → EXECUTING` 在状态机里都是合法边。
  因此云端路径实际只被两道门夹住：**审批时才入队**（`fleet/server.go:498-504`）
  与**设备侧 runner 的逐步骤检查**（`edge/agent/runner.go:401`）——协调器自己没有独立的批准检查。
  书中若宣称「批准在执行路径上有三层独立检查」，需要限定为「入队前 + 派发前两层，协调器层没有」。
- **（推断）工具表面 ≠ 权限边界**：桥接持有的 token 本身就是完整 operator 凭据，
  因此「不给批准工具」是**工具表面**的限制，不是 token 权限的收窄——
  拿到该 token 的客户端理论上可以直接 `POST /v1/tasks/{id}/approve` 或 `/v1/devices/{id}/estop`。

---

## 6. 权限与审批模型

### 6.1 `agentruntime/permission.go`：一个刻意**不做**安全判定的门

模块注释（`permission.go:12-24`）把定位写得极清楚：

> The gate is deliberately not a second safety system. It does not decide whether an action is safe,
> whether a step may be retried, or whether approval exists in the operator's sense — those already have owners.
> It answers one narrow question: **is the agent that is asking the kind of agent that may ask this at all?**

5 个稳定拒绝码（`:27-38`），进持久事件流：`AGENT_READ_ONLY`、`CAPABILITY_NOT_DECLARED`、
`AGENT_APPROVAL_REQUIRED`、`AGENT_NOT_REGISTERED`、`NO_MUTATION_REQUESTED`。

`decide()`（`:99-146`）的判定顺序是有意的：**先只读、再能力、再审批**。
注释解释了为什么只读判定必须在最前（`:113-115`）：
「a read-only agent asking to change the world is the case this gate exists for, and it must be refused
on the declaration alone rather than on any finer check that a future change might relax.」

拒绝不是返回值，而是**发布事件**（`:163-174`，`TopicAgentPermissionDenied`）。理由（`:79-82`）：
「a refusal that leaves no trace is indistinguishable from an agent that never asked.
Observing what an agent tried to do is often more informative than observing what it did.」

**诚实边界（必须写进书里）**：`RequestMutation` 在仓库中**没有任何生产调用点**——
`grep RequestMutation` 只命中 `agentruntime/permission.go` 自身、`agentruntime/orchestrator_test.go` 与
`docs/architecture/multi-agent-runtime.md:104`。它是一道**已实现、已测试、可用的门**，不是当前每条路径都穿过的关卡。
（推断）这符合该仓库「先把端口建好并留测试，再谈接线」的一贯节奏，但读者不应以为它是强制中间件。

### 6.2 审批的三档粒度

| 粒度 | 载体 | 位置 | 语义 |
| --- | --- | --- | --- |
| 任务级 | `Task.Approved bool` | `tasks/service.go:29`；门在 `edge/agent/runner.go:401` | 一次批准覆盖该任务的全部物理步骤 |
| 步骤级 | `step.ApprovalID` | 由 `edge/agent/runner.go:864` 铸造为 `"approval:"+taskID+":physical"`；非物理步骤置空 `:870`；校验在 `core/guard/guard.go:60-61` | 注意：**同一任务的所有物理步骤共享同一个 ID**，所以它是「任务批准在步骤上的投影」，不是逐步批准 |
| 动作级（恢复通道） | `RecoveryAction.RequiresApproval()` | `agentruntime/recoverycatalog.go:337-343` | `Risk != RiskReadOnly` 即需批准；恢复是从异常中出来的，风险更高 |

`docs/architecture/llm-driven-execution.md:118` 把这条差异写成了明确决策：
「恢复计划是**逐步批准**……执行期决策在**任务级批准**的覆盖下（因为整件任务已经被人批过）」。

### 6.3 「模型不得执行批准时没出现过的物理动作」——对应实现

这句话出自 `docs/architecture/llm-driven-execution.md:56`。它在代码里的名字是 **Scope**，
与 Approver（批准端口）是**两个独立问题**（`internal/actionloop/loop.go:195-208` 的类型注释）：

| 问题 | 谁回答 |
| --- | --- |
| 「这一次调用可以发生吗」 | `Approver`（`loop.go:258-261`） |
| 「这类事情**当初**被批准过吗」 | `Scope`（`loop.go:208`） |

实现要点：

- `ScopeOf(names...)`（`loop.go:215-223`）构造一个只放行指定工具名的 scope；空列表是**显式的空**，
  而不是可能被读作「无上限」的 nil。
- `loop.go:269-291` 的字段注释：**`Scope` 为 nil 时拒绝一切物理调用**。
  理由：「a deployment that has not said what was approved has not approved anything,
  and reading a missing scope as an unbounded one would make the operator's approval meaningless exactly when it matters.」
- 只读与 `local_side_effect` **不受 Scope 约束**（`needsScope`，`loop.go:628-635`）：
  「bounding those would stop the loop from even looking, which is not a safety property but an outage.」
- 越界处理不是静默跳过：`loop.go:452-467` 记 `VerdictOutsideScope = "OUTSIDE_APPROVED_SCOPE"`，
  带上**工具名、参数、模型给的理由**，然后 `Escalated = true` 停机。
  注释：「the useful answer to 'the robot wanted to do something you did not approve' is the proposal, not a silence.」
- 范围内的物理调用**仍然要过批准**（`loop.go:469-490`）：「范围说明『这件事被考虑过』，不等于『这一次被批准了』」。
- 未获批准时的处理同样是停机而非换工具：「a physical call nobody approved is a decision for a person,
  and trying a different tool instead would be working around the refusal.」（`loop.go:478-480`）

`docs/architecture/llm-driven-execution.md:136` 记录该机制有 34 个测试（含 `-race`），
并「验证过『去掉该检查测试即失败』」——即做过变异验证。

---

## 7. 与通用 coding agent 工具层的差异

### 7.0 先纠正一个常见误解：本仓库里**没有** shell/file 类工具

实测结果必须写进书里，否则整章会被一个不存在的对照物带偏：

- **全仓库 `read_file` / `write_file` / `apply_patch` / `str_replace` / `list_directory` / `run_shell` 零命中。**
  唯一的 `bash` 出现在部署二进制的子命令里（`internal/robotagent/app.go:74,84` 的 `Runner.Run(ctx, "bash", ...)`），
  不是给模型的工具。
- 仓库里真正存在的「非物理工具层」是本地/运维工具层：`internal/recoveryexec/tools.go:254-258` 注册 5 个
  ——`telemetry.read`、`execution.read-history`、`runtime.reconnect`、`task.resume`、`recover_to_safe_pose`。
  **其中 `task.resume`（`:257`）与 `recover_to_safe_pose`（`:258`）也是 `SafetyLevel.physical_motion`**——
  在这个系统里，没有「软件工具天然安全」这回事。
- 仓库有一条明写的禁令：`docs/development/robot-adapters.md:83`
  「`tools` 只列当前型号支持的通用工具……**不能注册任意 shell 或厂商方法名**」。

因此 §7 的对照分两层：**(A) 仓库内可比的三层工具**（有代码事实），**(B) 与通用 coding agent 范式的结构性差异**（标注推断）。

### 7.1 仓库内三层工具的声明对照（每格都是代码事实）

| 声明的字段 | 机器人工具（Python） | 本地运维工具（Go） | MCP Fleet 工具 |
| --- | --- | --- | --- |
| 定义处 | `tool_layer.py:534-554` | `internal/recoveryexec/executor.go:56-63` | `server.py:149-199` |
| 安全等级 | `safety_level` 0–4 | `SafetyLevel` 3 档 | 无；只有 `read_only` 布尔 |
| 副作用 | `mutates_world` | `MutatesWorld` | `readOnlyHint` / `destructiveHint` 注解（`:350-353`） |
| 幂等性 | `idempotent` 字段 | **无此字段** | `idempotentHint` 注解 |
| 超时 | `timeout_s`，且只能被调用方缩短 | **无此字段** | 进程级单一 `TANGYING_MCP_TIMEOUT_SECONDS` |
| 可逆性 | **无此字段** | **无此字段** | **无此字段** |
| 是否需要批准 | 由 `safety_level` 派生（`actionloop/context.go:32`） | 由 `SafetyLevel` 派生，同上 | 无（根本不给批准工具） |
| 路由 | `distributed_node` | 迟绑定 registry（`executor.go:66-70`） | 9 个固定 HTTP 端点 |

**结论：只有机器人工具层同时具备幂等性、超时与副作用三个声明。** 这反过来解释了为什么机器人工具层
必须自己实现重试策略（`tool_executor.py:305-314`）——通用工具层连「能不能重试」这个问题都没被问过。

### 7.2 与 coding agent 范式的结构性差异（(推断) 部分已标注）

| 维度 | coding agent 的 shell/file 工具（范式） | 本仓库的机器人工具 | 依据 |
| --- | --- | --- | --- |
| 幂等性 | 无声明；重跑一次 `rm` 是调用者的事 | `idempotent` 字段 + 重试派生规则 | `tool_layer.py:542`；`tool_executor.py:305-314` |
| 可逆性 | 无声明，靠 git/备份兜底 | **同样没有字段**，改用 `mutates_world` + 证据门：「成功」只是「去看一眼」的触发，不构成完成 | `tool_layer.py:534-554`（无 reversible）；`core/closedloop/gate.go:60` |
| 超时 | 通常无内建上限，靠外部 kill | 每工具一个预算，调用方只能缩短：`min(call.timeout_s, tool.timeout_s)` | `tool_executor.py:198-205` |
| 超时语义 | 超时 ≈ 失败，可重试 | 超时**不声称机器人停了**：`TIMEOUT` + `recoverable=False` + `outcome_uncertain=True` | `tool_executor.py:355-360` |
| 重试 | 随手重跑 | `mutates_world=True` **一次都不重试**；仅只读/幂等可重试（`max_attempts` 默认 2） | `tool_executor.py:305-314`、`:225` |
| 并发 | 多进程写同一文件靠 OS 锁/约定 | 工具层全局运动锁 + 运行时执行锁 + 编排层在途跟踪，**三层**（详见 §7.3） | `tool_executor.py:95,207-215`；`service.py:324-330`；`agentruntime/orchestrator.go:395-399` |
| 失败终态 | 「报错」即可 | `STARTED` 本身就是语义：「physical outcome is unknown after an interrupted invocation」，不能当 `FAILED` 重试 | `middleware/contracts.go:61-70` |
| 分阶段容忍度 | 顺序写文件，写坏了再改 | 只读/`local_side_effect` 不受 `Scope` 约束，物理动作受；理由：「把『看』也纳入范围不是安全属性，是一次停机」 | `internal/actionloop/loop.go:628-635`；`llm-driven-execution.md:69` |

(附) 一个值得在书里点名的空缺：`middleware.Locker` 与 `middleware.Lease`（`middleware/contracts.go:46-53`）
**已声明但无适配器、无调用点**——全仓库除这个接口定义外零命中。也就是说「分布式锁」目前是端口而非设施，
真正生效的互斥全部发生在单进程内（§7.3）。

(推断) 上表右列全部是代码事实，左列是 coding agent 的通行做法；仓库里**没有**一篇文档显式对比二者
（`docs/development/robot-tool-layer.md:3`、`:129`、`:143-144` 与 `robot-adapters.md:83` 最接近，
但都是单向陈述机器人层，不是对照）。书中若要写成对照，应以本节为准，不要引用不存在的仓库文档。

### 7.3 「同一个物理世界不能被多个写者同时改」的三层实现

1. **工具层**：`_motion_lock`（`tool_executor.py:95`）——**一把全局锁**，不分节点。
   `test_execution_admission.py:45-60` 断言：第一个运动工具超时后第二个运动工具得到 `BUSY`，
   而只读工具与 `emergency_stop` 照常成功。
   **文档冲突**：`robot-tool-layer.md:143` 说「同一节点的写工具互斥……不同节点互不阻塞」，
   但代码用的是单一全局锁，**跨节点也是互斥的**——实际行为比文档更保守（更强），
   但「不同节点互不阻塞」这句对写工具不成立。
2. **运行时**：`service.py:324-330` 的每机器人非阻塞执行锁，并区分三种失败
   （`ROBOT_BUSY` / `EXECUTION_OUTCOME_UNKNOWN` / `IDEMPOTENCY_CONFLICT`）。
3. **编排层**：`agentruntime/orchestrator.go:395-399 PhysicalActionInFlight` + `:409-423 deferIfPhysicalActionInFlight`。
   `:57-67` 的注释解释了一个真实故障：早期用计数器跟踪在途命令，
   `SENDING(+1) RUNNING(+1) CONFIRMED(-1) = 1 forever`，导致该任务此后永远被判定为「有物理动作在途」，
   恢复提案被永久推迟——**最需要操作员的场景恰好是系统沉默的场景**。修法是改用 command id 身份集合。
   这是本章最好的「物理并发」教学案例：**计数能表达数量，不能表达身份。**

### 7.4 急停是唯一的例外通道，而例外是被写进代码的

- 工具层：`emergency = tool.name == "emergency_stop"`（`tool_executor.py:191`）——豁免健康检查（`:192`）、
  豁免取消（`:205`）、豁免运动锁（`:207`）、豁免重试退避中的取消（`:231`）、豁免超时时的取消传播（`:345`、`:358`）。
- 运行时：`service.py:315-321` 在**获取执行锁之前**直接调 `self.safety.emergency_stop(...)`，
  注释：「必须抢在运动拥有准入时也能生效」。
- MCP 桥接：`server.py:236-238` 在限流预算之外，注释：「Safety stops must remain available when ordinary
  discovery/planning exhausts the budget.」`test_bridge.py:236-243` 用 `RATE_LIMIT=1` 验证急停仍可调用。
- 安全监督：`safety.py:182-190` 先锁存、持久化，再碰驱动（「Latch before storage or driver I/O」）；
  `:196-202` 停止失败时**反向**把 estop 锁存（fail-safe 方向）；
  `:207-215` 复位需要 `operator_present=True` 且无在途命令。
- 回执诚实：`tools/safety.py:55-60` 若运行时没确认锁存，返回 `HARDWARE_ERROR` 而**不是**成功——
  「A stop that is merely *sent* is reported as unconfirmed, never as stopped.」

### 7.5 三个空能力域的含义

验证、标定、恢复在 `tools.json` 里是空的（§1.2），这不是没做，而是**归属另处**：

- **验证**属于 `core/closedloop`：写动作的完成由命令后的新鲜证据判定（`core/closedloop/gate.go:60`、
  `:85-102` 校验 observation id / 时间 / 是否早于派发 / 源判定是否 STALE）。让模型自己调一个
  `verify_x` 工具，等于让它给自己的作业打分。
- **标定**属于现场：`calibration_wizard.py` 是交付/调试流程，不是运行期能力。
- **恢复**属于 `recover_to_safe_pose` 这条 runtime 技能与 `agentruntime` 的恢复目录，
  而恢复目录自带风险分级（`agentruntime/recoverycatalog.go:353-360`：只有 `read_only` 与 `bounded_write`
  可执行；`never_automatic` 即使被批准也不执行）。

`docs/architecture/llm-driven-execution.md:166-168` 把这条边界写成三条禁令：
**不让模型直接写关节序列**（低层动作来自 Policy Provider，模型选的是工具）、
**不给模型绕过审批的能力**、**不放松门禁**。
Policy Provider 边界的具体实现：`edge/worker/policy.go:14` 的
`ErrPolicyRequired = errors.New("runtime capability requires a policy action chunk")`，
以及 `:125` 的 `parameters["action_chunk"] = wireActions(decision.Actions)`——
**关节序列由策略在 Edge 侧填充，模型的自述里根本没有这个字段。**

---

## 8. 教学价值

### 8.1 推荐教学顺序（6 步，每步都能用测试自证）

1. **先看契约，不看硬件**：读 `tool_layer.py:522-570` 的 `SafetyLevel` + `RobotTool`，
   跑 `pytest tests/tool_layer/test_tool_contract.py -q`。要点：**非法定义在构造期就死**。
2. **看目录是从代码长出来的**：跑 `python -m tangying_robot_gateway.llm_tools --check`，
   再手工改一行 `tools.json` 让 `--check` 退出码变 1。要点：**schema 不可能与执行体漂移**。
3. **看可见性过滤**：读 `tool_layer.py:706` 与 `llm_tools.py:83-85`，
   跑 `test_llm_tools.py::test_coordinate_level_tools_are_withheld_from_the_model`。
   要点：**「不给模型」是一个字段，不是一个删除动作**。
4. **看执行器的分布式职责**：读 `tool_executor.py:176-253`，
   跑 `test_execution_admission.py`（超时后仍占锁、只读与急停不受阻）。要点：**超时 ≠ 停止**。
5. **看门禁在自己外面**：读 `safety.py:78-130` 与 `service.py:324-341`，跑
   `test_gateway_safety_boundary.py::test_unapproved_call_does_not_reach_the_backend`。
   要点：**工具层不实现安全，它只是安全之前的一层翻译**。
6. **看外部接入的减法**：读 `robot/mcp/tangying_mcp/server.py:99-199`，
   跑 `pytest tests/mcp -q`。要点：**桥接的安全性来自「没有那个工具」，而不是「那个工具会拒绝」**。

### 8.2 最小可运行工具示例（骨架，全部字段来自真实 `RobotTool` 签名）

```python
# tools/my_tools.py —— 新增一个语义入口，不新增 skill
from ..tool_layer import RobotTool, SafetyLevel, ToolResult
from .registry import OperationContext, RobotAdapter, require_text

NAMESPACE = "robot.perception"

def build_my_tools(adapter: RobotAdapter, *, context: OperationContext | None = None):
    def count_shelves(zone: str = "") -> ToolResult:
        name = require_text(zone, "zone") if zone else ""
        # 只调用既有 skill；不新增 skill 名，否则要同时改 safety.ALLOWED_SKILLS、
        # xlerobot_adapter 白名单与 Go 侧守卫（robot-tool-layer.md:176）
        result = adapter.execute("observe_scene", parameters={"zone": name},
                                 context=context, timeout_s=5.0)
        if not result.success:
            return ToolResult.from_runtime(result, zone=name)
        shelves = [e for e in result.payload.get("entities", ()) if e.get("category") == "shelf"]
        return ToolResult.ok(count=len(shelves), zone=name)

    return [RobotTool(
        name="count_shelves",                    # 蛇形、无点号（tool_layer.py:560）
        description=("统计当前场景中已登记的货架数量，用于确认巡检区域是否可见。"
                     "返回 count=0 表示视野内没有货架，应先换位置再统计，不要当作设备故障。"),
        parameters_schema={"type": "object",
                           "properties": {"zone": {"type": "string"}},
                           "additionalProperties": False},   # 必写：挡住模型夹带安全字段
        returns_schema={"type": "object", "properties": {"count": {"type": "integer"}}},
        safety_level=SafetyLevel.QUERY,          # 只读就只能是 0
        timeout_s=5.0,                           # 必须 > 0
        distributed_node=NAMESPACE,              # 必须非空，决定路由与健康
        idempotent=True,
        mutates_world=False,                     # 写工具必须为 True（robot-tool-layer.md:198）
        handler=count_shelves,
    )]
```

然后在 `tools/__init__.py:60-71` 的装配列表里加一行，跑
`python -m tangying_robot_gateway.llm_tools --write` 与 `pytest tests/tool_layer -q`。
**注意 `tests/tool_layer/test_tool_selection.py:80` 会断言「每个公开工具至少能回答一条指令」——
新工具若谁也用不上，测试会替评审指出这一点。**

### 8.3 学生最容易犯的 6 个错误

1. **把 `mutates_world` 当装饰**。它同时决定三件事：能否自动重试（`tool_executor.py:311-312`）、
   是否要拿运动锁（`:207`）、是否必须过闭环证据门（`core/skills/manifest.go:30-34`）。
   标 `False` 的写工具会让系统「报告成功但永不完成」。
2. **以为加了 `llm_visibility="fallback"` 就安全了**。它只影响两个过滤点
   （`llm_tools.py:64`、`tool_executor.py:261`）。模型仍然可能在提示词里被塞进这个名字；
   真正的兜底是参数 schema 的 `additionalProperties: false` 与执行期审批。
3. **把 `SafetyLevel` 当成全系统的安全等级**。5 级只存在于 Python 工具层；
   能力契约与安全监督看到的是 4 个字符串，Go 清单看到 3 档（§2.3）。
   在文档里说「系统是 5 级安全」会被 `core/skills/manifest.go:10-14` 直接反驳。
4. **以为超时可以重试**。`TIMEOUT` 的 `recoverable=False` 且 `outcome_uncertain=True`
   （`tool_executor.py:355-360`）——「结果未知」与「失败」是两件事，
   对物理系统重试一次就是多做一次。
5. **以为工具层自己会拦住危险动作**。`safety.py` 的模块注释与 `tool_layer.py` 的模块注释都写明：
   工具层不实现限幅、急停、租约。写了工具就等于给了入口，
   是否放行由 `safety.py:78-130` 决定。
6. **把「通用工具」默认当成软件操作**。本仓库的运维工具层里，
   `task.resume` 与 `recover_to_safe_pose` 同样是 `physical_motion`（`internal/recoveryexec/tools.go:257-258`）。
   在物理 Agent 系统里，「这是软件调用所以不危险」是一个必须先证明、不能假设的前提。

### 8.4 练习题

**题 1（目录一致性）**
`tools.json` 有 29 个工具、`metadata` 有 31 项、`excluded_from_llm` 有 2 项。
请写一段脚本证明这三个数不是巧合，并说明如果有人手工把 `move_arm_to_joints` 加回 `tools` 数组，
哪些测试会失败、CI 会怎么发现。

> 答案要点：`build_catalog()`（`llm_tools.py:63-86`）用两个不同的 `select()` 口径遍历同一个注册表——
> `llm_only=True` 得 29，`llm_only=False` 得 31；`excluded_from_llm` 是后者的补集，所以 31 = 29 + 2 是恒等式。
> 手工改动会被 `test_llm_tools.py:82-88`（逐字节比对）与 `--check`（`llm_tools.py:138-143`，非零退出）同时发现；
> 若改的是生成逻辑而非产物，`test_llm_tools.py:73-79` 会先失败。

**题 2（可见性 vs 权限）**
某同学想「干脆不让模型看见 `emergency_stop`，避免它乱停」。
请指出这会破坏什么，并给出保持急停可用同时不被滥用的正确做法。

> 答案要点：破坏三处——(a) `tool_executor.py:261` 显式把 `emergency_stop` 排除在健康过滤之外，
> 它被设计成永远可见；(b) MCP 侧急停在限流预算之外（`server.py:236-238`），
> 「普通请求耗尽预算时急停仍必须可用」；(c) 急停是 `mutates_world=False` 的物理动作，
> 闭环门禁对它不成立（`skills/manipulation/plugin.go:42-44`），因此它没有「需要证据」的包袱。
> 正确做法不是隐藏，而是**让误触不产生虚假安全感**：`tools/safety.py:55-60` 在运行时未确认锁存时
> 返回 `HARDWARE_ERROR` 而非成功；复位是单独的工具且强制 `operator` + `reason`，缺一即 `PERMISSION_DENIED`（`:63-71`）。

**题 3（批准范围）**
一个 LLM 驱动的循环里，操作员批准了 `navigate_to`。模型随后提议 `move_arm_to_pose`，
`Approver` 回调返回 `true`（因为它是本地测试桩）。请描述系统的实际行为，并解释为什么这样设计。

> 答案要点：`needsScope(tool)` 对 `physical_motion` 为真，`withinScope` 查 `ScopeOf("navigate_to")`
> 不含 `move_arm_to_pose`，因此 `loop.go:452-467` 记 `OUTSIDE_APPROVED_SCOPE`、带上工具名/参数/模型理由、
> `Escalated=true` 停机——**`Approver` 说什么都不影响这个结果**（顺序上 Scope 检查在 `:452`，
> 批准检查在 `:469`）。设计理由：把「这次能不能做」与「这类事当初批没批过」分开，
> 否则「有人能批准」就等于「授权无上限」，模型只要好好开口要一个操作员没见过的动作就能自己扩大授权范围
> （`loop.go:199-207`、`llm-driven-execution.md:58-65`）。
> 加分点：`Scope` 为 nil 时拒绝一切物理调用，空 `ScopeOf()` 同样什么都不放行（`:269-291`、`:215-223`）。

---

## 9. 源码索引

**工具目录与生成**
- `tools.json:1`（`schema_version`）、`tools.json:699-1133`（`metadata.*.safety_level`）、`tools.json:1145`（`excluded_from_llm`）
- `robot/gateway/tangying_robot_gateway/llm_tools.py:27,32-37,55-86,64,81,83-85,129-151`
- `tests/tool_layer/test_llm_tools.py:57-63,73-79,82-88,121-126`

**工具契约与注册表**
- `robot/gateway/tangying_robot_gateway/tool_layer.py:1-24,38-63,318,337,384-500,522-531,534-570,572-583,585-606,607,620-627,658-669,691-712`
- `robot/gateway/tangying_robot_gateway/tools/__init__.py:42-76,65,67-71,73-75`
- `robot/gateway/tangying_robot_gateway/tools/registry.py:36-53`
- `robot/gateway/tangying_robot_gateway/tools/navigation.py:267`
- `robot/gateway/tangying_robot_gateway/tools/manipulation.py:225`
- `robot/gateway/tangying_robot_gateway/tools/mapping.py:49-141,75-80,113-140`
- `robot/gateway/tangying_robot_gateway/tools/safety.py:1-13,27-32,43-77,79-120,133-235`
- `robot/gateway/tangying_robot_gateway/runtime.py:24`
- `robot/gateway/tangying_robot_gateway/contracts.py:30-43,46,333-342`
- `tests/tool_layer/test_tool_contract.py:180-196,218-237,285,290-292`
- `tests/tool_layer/test_tool_selection.py:80,148-154`
- `tests/tool_layer/fake_adapter.py:34-113`

**执行器 / 统一通道**
- `robot/gateway/tangying_robot_gateway/tool_executor.py:1-21,95,176-253,256-263,268,305-314,321-368`
- `robot/gateway/tangying_robot_gateway/gateway_adapter.py:60-64,76-125,98,106-126`
- `robot/gateway/tangying_robot_gateway/service.py:265,267-295,307-343,315-321,324-330,345-360,385`
- `robot/gateway/tangying_robot_gateway/safety.py:14-32,78-130,104,107-118,177-190,192-205,207-243`
- `proto/robot/v1/robot.proto:12,58`
- `tests/contract/test_proto_schema.py:28`
- `tests/tool_layer/test_execution_admission.py:22-26,45-60`
- `tests/tool_layer/test_gateway_safety_boundary.py:30-43`

**安全标注（Go 侧）**
- `core/skills/manifest.go:8-14,20-36,47-71`
- `skills/manipulation/plugin.go:21-60,42-44`
- `core/guard/guard.go:13-20,53-66`
- `orchestration/llm.go:139-175,153,178-198`
- `cmd/edge-worker/main.go:249`
- `internal/actionloop/context.go:32`

**不暴露给模型**
- `robot/gateway/tangying_robot_gateway/tool_layer.py:551-554,569-570,706`
- `robot/gateway/tangying_robot_gateway/llm_tools.py:64,81,83-85`
- `robot/gateway/tangying_robot_gateway/tool_executor.py:256-263`

**权限与审批**
- `agentruntime/permission.go:12-38,61-73,78-96,99-146,153-160,163-174,202-203`
- `agentruntime/recoverycatalog.go:337-343,353-360`
- `agentruntime/orchestrator.go:55-72,395-399,403-423`
- `internal/actionloop/loop.go:49-68,164-192,195-223,258-291,425-490,523-641`
- `core/closedloop/gate.go:15-28,60,85-102`
- `core/closedloop/closedloop.go:74-75`
- `edge/agent/runner.go:29-30,277,368,401-407,436-455,485-515,864,870`
- `edge/worker/worker.go:294,443-449`
- `tasks/service.go:29,38-39,168-207,612-627`
- `fleet/server.go:126,129,442-461,488-508`
- `fleet/auth/auth.go:233-241,261-303`
- `console/server.go:191,194,323-344,372-384`
- `core/taskgraph/state.go:6,10,27-31`
- `middleware/contracts.go:44-50,61-70,79-95`

**MCP 桥接**
- `robot/mcp/tangying_mcp/server.py:1,27-29,99-131,134-146,149-199,236-244,246-333,336-366`
- `robot/mcp/README.md:3,21-29,33-42,44,46-75`
- `tests/mcp/test_bridge.py:22-27,155-171,180-195,216-226,236-243,410-437`

**执行边界 / 策略**
- `edge/worker/policy.go:14,76,99-125`

**通用/本地工具层（对照物）**
- `internal/recoveryexec/executor.go:56-63,66-70,251`
- `internal/recoveryexec/tools.go:189-204,213-260`（`:254-258` 为 5 个工具的注册）
- `internal/actionloop/context.go:32`
- `core/agentcontext/context.go:49-54,201`
- `middleware/contracts.go:21-25,44-53,145-160`
- `fleet/coordinator/coordinator.go:427-435,528,542-558`
- `console/guard.go`（`allowOperatorWrite`、`operatorEvidence`）
- `internal/robotagent/app.go:74,84`

**文档**
- `README.md:137-143`
- `docs/development/robot-tool-layer.md:3,9-14,56-69,94-123,125-131,137-144,146-170,172-203,228-235`
- `docs/development/robot-adapters.md:80-84`（禁止注册任意 shell 或厂商方法名）
- `docs/production/policy-tools.md:3,7-17,19-34,183-196`
- `docs/architecture/llm-driven-execution.md:9-21,23-39,41-52,54-73,75-95,96-106,108-120,130-161,163-168`
- `docs/architecture/middleware.md:9-17,32-35,46`
- `docs/architecture/agent-evaluation-system.md:89-104`
- `docs/superpowers/specs/2026-09-11-standard-robot-tool-layer-adr.md:11-24,26-32,34-44,46-52,54-58,60-70`
- `docs/development/2026-09-18-system-review-and-improvement-plan.md:268-274`
- `docs/architecture/multi-agent-runtime.md:104`
- `artifacts/release-preparation/v0.7.0/CHANGELOG.before.md:617,628`
- `artifacts/marketing/03-工具调用/素材来源与数字出处.md:16,29,102`
