# 机器人工具层

这一层把机器人能力变成 LLM 可以调用的函数。它**不是**新的执行通道：每个工具最终都编译成既有的 `ExecuteSkill` 命令（gRPC 与 ROS 2 同构），因此安全监督、租约、幂等、journal 与闭环完成判定全部照旧生效。

```text
LLM function calling
        │  navigate_to(location_name="厨房") / move_arm_to_pose(x,y,z) / grasp()
        ▼
tools.json ──► ToolRegistry ──► ToolExecutor ──► RobotAdapter ──► PluginBackend
                （schema）      （路由/超时/重试/健康）   （端口）      （既有 Runtime）
                                                                    │
                                                        SafetySupervisor（唯一否决权）
                                                        RuntimeJournal（幂等与未知终态）
```

## 为什么这样分层

| 关注点 | 归属 | 理由 |
| --- | --- | --- |
| 能力语义、参数校验、结果归一化 | 本层（`tool_layer.py`、`tools/`） | 只有这里知道"厨房"是什么、什么算可恢复失败 |
| 硬件动作、限幅、急停、租约 | 既有 `safety.py` / `service.py` | 安全判定必须单点，工具层不得旁路 |
| 关节名、执行器键、IK | 适配器（`RobotAdapter`） | 这些名字随型号变化，工具层不得臆造 |
| 完成判定 | `core/closedloop`（Go Agent） | 写工具的"做完了"由命令后新鲜证据决定 |

## 快速开始

```bash
# 1. 生成给 LLM 的 function calling schema
.venv/bin/python -m tangying_robot_gateway.llm_tools --write   # 写入 tools.json

# 2. 工具层自测（无需硬件、无需仿真）
.venv/bin/pytest -q tests/tool_layer

# 3. 校验 tools.json 与代码一致（CI 也跑这个）
.venv/bin/python -m tangying_robot_gateway.llm_tools --check
```

在 Python 里直接调用：

```python
from tangying_robot_gateway.gateway_adapter import GatewayRobotAdapter
from tangying_robot_gateway.semantic_map import SemanticMap
from tangying_robot_gateway.tool_executor import ToolCall, ToolExecutor
from tangying_robot_gateway.tools import build_registry

adapter = GatewayRobotAdapter(backend)              # backend 是既有的 PluginBackend
registry = build_registry(adapter, SemanticMap.from_file())
executor = ToolExecutor(registry)

result = executor.execute(ToolCall("navigate_to", {"location_name": "厨房"}, tool_call_id="call-1"))
print(result.success, result.data, result.error_code, result.recoverable)
```

把 `executor.openai_tools()` 交给任何支持 function calling 的模型即可。

## 工具清单

27 个工具，25 个默认提供给 LLM。命名空间只用于路由与归类，工具名本身是蛇形、不带点，便于 function calling。

| 命名空间 | 工具 |
| --- | --- |
| `robot.nav` | `navigate_to`、`navigate_to_pose`※、`explore_for`、`stop_navigation`、`get_current_pose` |
| `robot.arm` | `move_arm_to_pose`、`move_arm_to_joints`※、`move_arm_relative`、`home_arm`、`get_arm_state` |
| `robot.gripper` | `grasp`、`release`、`set_gripper_width`、`get_gripper_state` |
| `robot.perception` | `detect_object`、`get_object_pose`、`resolve_location`、`scan_environment`、`capture_image`、`get_robot_status` |
| `robot.skill` | `pick_object`、`place_object`、`fetch_object` |
| `robot.safety` | `emergency_stop`、`reset_safety_stop`、`set_speed_limit`、`check_collision` |

※ 标记为 `fallback`：仍然注册、可被上层编排调用，但**默认不提供给 LLM**，因为它需要坐标或关节角这类底层参数。

### 语义优先

`navigate_to("厨房")` 由服务端解析坐标，模型只给名字。位置表是可配置的：

```json
{
  "schema_version": "semantic.locations.v1",
  "layout_id": "home-four-room-v1",
  "locations": [
    {"name": "kitchen", "room": "kitchen", "pose": [2.2, 3.35, 0.0],
     "aliases": ["厨房", "the kitchen"]}
  ]
}
```

缺省布局在 `robot/gateway/tangying_robot_gateway/assets/home_locations.json`，与已验收的四房间场景一致（有测试防止两者漂移）。换一套房子就是换这个文件：

```python
registry = build_registry(adapter, SemanticMap.from_file("/path/to/other_house.json"))
```

解析失败返回 `NOT_FOUND` 并列出所有已知位置；不做模糊匹配，避免"最近的房间"这种静默错误。

## 统一返回结构

```python
ToolResult(
    success=True,
    error_code=None,          # 失败时为下面十个标准码之一
    error_message=None,
    recoverable=False,
    data={...},               # 结构化数据，绝不返回自然语言
    timestamp=1789106773.55,
    tool_call_id="call-1",
)
```

标准错误码与恢复语义：

| 错误码 | 含义 | `recoverable` | 模型应该怎么做 |
| --- | --- | --- | --- |
| `TIMEOUT` | 超时，动作可能仍在进行 | 是 | 先确认状态，再决定是否重试 |
| `NOT_FOUND` | 物体或位置不在视野/未登记 | 是 | 换位置搜索（`explore_for`）或确认名称 |
| `UNREACHABLE` | 目标不可达或定位未就绪 | 是 | 换目标，不要重复同一坐标 |
| `COLLISION_RISK` | 目标位姿会碰撞 | 否 | 改目标位姿 |
| `HARDWARE_ERROR` | 硬件或结果未知 | 否 | 停止并请求人工确认 |
| `PERMISSION_DENIED` | 缺少审批、profile 或操作员 | 否 | 请求授权，不要绕过 |
| `INVALID_PARAM` | 参数非法（含超范围） | 否 | 修正参数 |
| `BUSY` | 资源被占用或 fencing 过期 | 否 | 稍后重试或等待当前任务结束 |
| `CANCELLED` | 被取消 | 否 | 重新规划 |
| `SAFETY_STOP` | 急停锁存 | 否 | 现场确认后 `reset_safety_stop` |

`recoverable` 不是从字符串猜的：它来自与 Go Agent `core/closedloop` 相同的分类表，并有跨语言一致性测试防止两边分叉。运行时仍保留自己的细粒度码（`NAV_MAP_NOT_READY` 等），在失败结果的 `data.runtime_code` 里可见。

## 安全模型

- **唯一否决权**：所有运动仍经过 `SafetySupervisor`。工具层不实现限幅、急停或租约，也不绕过它们。
- **急停最高优先级**：`emergency_stop` 走适配器的带外停止路径，不排队，因此可在其他命令持有执行锁时生效。回执必须由运行时确认为已锁存，否则返回 `HARDWARE_ERROR`。
- **不可重试的动作**：`ToolExecutor` 只对查询类与幂等工具自动重试；任何 `mutates_world=True` 的工具都只执行一次。重复一个物理动作可能把它做两遍。
- **复位需要授权**：`reset_safety_stop` 要求 `operator` 与 `reason`，缺少即 `PERMISSION_DENIED`。
- **写工具仍需证据**：工具返回成功只是触发观察。Agent 侧的闭环门禁要求命令后新鲜证据，缺证据时步骤保持未完成。

## 分布式行为

`ToolExecutor` 负责本层自己的可靠性：

| 能力 | 行为 |
| --- | --- |
| 服务发现 | 按工具名解析到 `distributed_node`，不硬编码地址 |
| 健康检查 | 节点超过 `node_ttl_s` 没有心跳即标记不可用，其工具从 `openai_tools()` 移除并返回 `UNREACHABLE` |
| 超时 | 每个工具都有预算；处理函数在工作线程运行，超时返回 `TIMEOUT` 且 `data.outcome_uncertain=true`（不谎称已停止） |
| 取消 | 预取消的调用不执行；重试退避期间收到取消立即返回 `CANCELLED` |
| 并发 | 同一节点的写工具互斥；底盘与机械臂不会同时被两个调用者驱动。不同节点互不阻塞 |
| 重试 | 仅查询与幂等工具，且受尝试次数上限约束 |

## 复合技能

`pick_object`、`place_object`、`fetch_object` 只是把原子工具按固定顺序串起来（`pick_object` = detect → 靠近 → 预张开 → `grasp` → 抬起）。它们不直接命令机器人，只通过注册表调用原子工具，所以调用者本来就能调用其中的每一步。

失败时返回 `data.failed_step` 与完整子步骤轨迹：

```json
{
  "success": false,
  "error_code": "NOT_FOUND",
  "error_message": "gripper closed without detecting 'red cup'",
  "recoverable": true,
  "data": {
    "failed_step": "grasp",
    "steps": [
      {"tool": "detect_object", "success": true},
      {"tool": "move_arm_to_pose", "success": true},
      {"tool": "set_gripper_width", "success": true},
      {"tool": "grasp", "success": true}
    ]
  }
}
```

模型据此决定是重新检测、换个高度重试，还是放弃。

## 注册新工具

三种情况，按推荐顺序：

**1. 只是给既有能力换一个语义入口**（最常见）。在 `tools/` 对应模块里加一个 `RobotTool`，handler 调用 `adapter.execute(既有skill, ...)`。不要新增 skill 名，否则要同时改 `safety.ALLOWED_SKILLS`、ROS 适配器白名单与 Go 侧守卫——那才是"重写 AgentOS"。

**2. 新硬件能力**（例如一个新传感器查询）。同样先看既有 skill 能否表达；能用 `observe_scene` 就用它。

**3. 真的要新增 skill**。需要同时更新：
- `safety.ALLOWED_SKILLS`（若它改变物理状态还要进 `PHYSICAL_SKILLS`）
- `xlerobot_adapter/node.py` 的 `_validate` 白名单
- `contracts.TOOL_PARAMETERS` 的参数模型
- 如需跨进程返回结构化细节，走 `Result.payload`（进程内）或 proto 的 `SkillEvent.details`

新增工具的最低要求：

```python
RobotTool(
    name="my_tool",                     # 蛇形、无点号
    description="何时使用、前置条件、失败如何恢复；并说明返回的 error_code 含义",
    parameters_schema={"type": "object", "properties": {...}, "required": [...]},
    returns_schema={"type": "object", "properties": {...}},
    safety_level=SafetyLevel.QUERY,     # 0 查询 1 低速 2 常规 3 接触 4 安全
    timeout_s=5.0,
    distributed_node="robot.perception",
    idempotent=True,
    mutates_world=False,                # 写工具必须为 True
    handler=my_handler,
)
```

然后 `.venv/bin/python -m tangying_robot_gateway.llm_tools --write` 更新 `tools.json`。

## 调试

```bash
# 工具层单测：契约、语义解析、每个工具、执行器、schema、工具选择
.venv/bin/pytest -q tests/tool_layer

# schema 是否与代码一致
.venv/bin/python -m tangying_robot_gateway.llm_tools --check

# 运行中的 Runtime 对外声明了哪些能力（含 mutates_world）
.venv/bin/python -m tangying_robot_gateway.run_plugin check --factory <your.factory>

# 节点健康与不可用工具
python -c "from tangying_robot_gateway.tool_executor import ToolExecutor; ..."
```

排障顺序：

1. `get_robot_status` 或 `executor.node_health()` — 运行时是否可达、哪些工具不可用；
2. `data.runtime_code` — 运行时给出的原始原因；
3. `recovery_class`（`describe_failures`）— 这个失败属于瞬时、感知、规划还是未知终态；
4. 只有 `recoverable=true` 才原样重试；`HARDWARE_ERROR` 表示结果未知，必须先确认现场。

## 已知边界

- **笛卡尔机械臂控制未接通。** 该平台没有 IK 求解器，`move_arm_to_pose` / `move_arm_relative` 在实机适配器上返回 `IK_UNAVAILABLE`，不猜关节角。接入契约与清单见 [MoveIt 2 适配指南](arm-moveit-adapter.md)。
- **`set_speed_limit` 是软件限速。** 它缩放本适配器下发的速度参数并记录日志，不是硬件速度寄存器。
- **`force` 是尽力而为。** 夹持力比例会下发，但该驱动器不回报实测夹持力；是否夹到物体以 `object_detected` 为准。
- **命名匹配沿用观测词表。** `detect_object("红色杯子")` 需要观测实体带 `color=red` 或 id 含 `red`；跨语言命名不在本层做翻译。
- **`explore_for` 是路线搜索，不是规划器。** 它按位置表逐点前往并在到达后重新观测，受时间与可达性约束。
- **`Result.payload` 目前只在进程内传递。** 进程外适配器若需返回结构化细节，走 proto 的 `SkillEvent.details`（字段已存在，尚未接线）。
