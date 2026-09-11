# ADR: 面向 LLM 的标准化机器人工具层

状态：已接受（2026-09-11）。适用范围：`robot/gateway/tangying_robot_gateway`、`robot/ros2_ws/src/xlerobot_adapter`、`robot/mcp`。

## 背景：审计到的既有事实

请求描述的是一个"可能包含"若干组件的系统。实际仓库已经有相当完整的分布式 AgentOS，工具层必须**嵌入**它而不是与它并列：

| 既有能力 | 位置 | 本 ADR 的处理 |
| --- | --- | --- |
| 单一执行 RPC（gRPC `ExecuteSkill` 与 ROS 2 `ExecuteSkill.action` 同构） | `proto/robot/v1`、`robot/ros2_ws/src/tangying_robot_msgs/action/ExecuteSkill.action` | 作为唯一传输层保留，不新增总线 |
| 工具 schema 单一真源 | `contracts.py` 的 `TOOL_PARAMETERS`（12 个 Pydantic 模型） | 扩展，不复制 |
| 统一结果类型 | `runtime.Result(success, code, message, observation_id, confidence)` + `validate_result` | 保留为线协议结果；在其上加领域层 `ToolResult` |
| 安全否决层 | `robot/gateway/.../safety.py`（急停锁存、租约、profile、参数校验、journal） | 保留为唯一否决点；工具层不得旁路 |
| 闭环完成契约 | `core/closedloop`（`mutates_world` → 需命令后新鲜证据） | 领域写工具必须声明 `mutates_world` |
| 导航 | `xlerobot_adapter` 的 `ExecuteSkill`、`tangying_navigation` 的 Nav2 `NavigateToPose` 客户端 + RTAB-Map | 复用；只补语义位置解析 |
| 工具目录/注册表 | `sim/mujoco/tangying_sim/tools.py` 的 `ToolRegistry`（仿真侧） | 作为模式先例；领域注册表在 gateway 侧 |
| 外部 Agent 接入 | `robot/mcp`（8 个统一工具） | 不重复造 MCP 层 |

**审计结论：不存在需要"另起炉灶"的缺口。** 缺的是三件具体的东西：

1. **领域工具**：底盘只有 `navigation.navigate(goal_pose)`（坐标），机械臂只有 `arm.move(action_chunk)`（关节序列），夹爪没有独立工具（`manipulation.pick` 隐含闭合）。LLM 无法表达"导航到厨房""物体上方 10 cm"。
2. **语义位置解析**：仓库里房间名硬编码在 `robotclient.homeObjectID`/`HomeRouteGoals` 与 `home_scene.py`，没有可配置的"位置名 → 世界位姿"注册表。
3. **LLM function calling schema**：`run_plugin schema` 已经导出 12 个工具的 JSON Schema，但缺 LLM 需要的元数据（何时用、前置条件、失败恢复、安全等级、超时、可重试性、错误码），也没有 OpenAI 兼容的 `tools.json`。

## ADR-1：传输层不变，在其上增加一层"领域工具"

**决定。** 新增的领域工具（`navigate_to`、`move_arm_to_pose`、`grasp` …）**不是**新的传输通道。它们编译成既有的 `ExecuteSkill` 调用：`skill` + `parameters_json` + 既有的 lease／deadline／idempotency／approval 字段。gRPC 与 ROS 2 两侧看到的能力集与安全门禁完全不变。

**理由。** 仓库已经有两套并行的执行契约（gRPC 与 ROS 2 Action）且字段一一对应，且 `SafetySupervisor`、`RuntimeJournal`、`core/closedloop` 都挂在这条链上。新开一条总线会立刻产生两份租约、两份幂等和两份安全判定，违反"不重写现有 AgentOS"。

**后果。** 领域工具的执行必须通过既有 `PluginBackend` 的 handler 机制；工具层只负责参数翻译、结果归一化与组合，不持有硬件状态。

## ADR-2：LLM 面向的能力集与硬件动作集分离，翻译发生在适配器内

**决定。**

- **能力集（LLM 可见）**：语义参数。`navigate_to(location_name)`、`move_arm_to_pose(x,y,z,roll,pitch,yaw)`、`grasp(force,width)`。不含关节角、轮速、PWM、action_chunk。
- **动作集（适配器可见）**：既有的 `action_chunk`／`goal_pose` 等底层参数。
- **翻译位置**：`robot/gateway` 的领域工具实现把语义参数解析为动作集参数，再交给既有执行路径。适配器（XLeRobot 直连或 ROS 2）不做语义解析。

**理由。** 请求禁止把关节角暴露给 LLM，请求同时要求"不要让 LLM 输出关节角"。但底层驱动当前只接受 `action_chunk`（关节目标序列）。若把翻译放进适配器，每个型号适配器都要重做一遍语义解析并各自犯错；放在 gateway 侧可以单点测试、单点限制。

**关节空间工具的处理。** `move_arm_to_joints` 按请求保留为**底层兜底工具**，但它标记 `llm_visibility: "fallback"`，在 `tools.json` 中默认不提供给 LLM，只在诊断/调试导出中出现。这样"底层兜底可选、LLM 默认看不到"两个要求同时成立，而不是假装它不存在。

## ADR-3：领域工具复用既有错误码，统一错误码是面向 LLM 的投影

**决定。** 运行时继续使用既有细粒度码（`NAV_MAP_NOT_READY`、`ROBOT_NOT_ARMED`、`FENCING_TOKEN_STALE` …），因为 `core/closedloop` 的失败分类与恢复策略依赖它们。面向 LLM 的 `ToolResult.error_code` 使用请求规定的十项标准码，通过显式映射表投影，未识别码映射为 `HARDWARE_ERROR` 并保留 `data.runtime_code`。

**理由。** 直接替换运行时代码会破坏 `core/closedloop` 已测试的分类表和 `docs/production/operations-and-failures.md` 记载的排障路径——这是"重写现有 AgentOS"的一种。两层各自服务不同消费者：运行时码用于策略，标准码用于 LLM 推理。

**`recoverable` 的判据。** 不由错误码字面推断，而是取 `core/closedloop` 的分类结论：瞬时与感知类可恢复，未知终态、权限、资源冲突、参数错误与未知码不可自动重试。这样 Python 侧与 Go 侧的恢复语义不会分叉。

## ADR-4：语义位置来自可配置注册表，不硬编码房间

**决定。** 新增 `semantic_map.py`：位置定义（名称、别名、世界位姿、房间、坐标系）从 JSON 文件加载，缺省提供仓库已验收的四房间布局。`resolve_location` 是公开工具，也是 `navigate_to` 的内部依赖。解析失败返回 `NOT_FOUND` 并列出已知位置名。

**理由。** 请求要求"不要把坐标解析交给 LLM"，同时又要求语义优先。硬编码房间会让该系统只能用于那一个房子；纯配置化又会丢掉已验收布局的可用性。两者兼顾：配置驱动 + 随仓库提供已验收缺省。

## ADR-5：MoveIt 2 与 GripperCommand 的处理方式（本版边界）

**决定。** 本版**不宣称**已对接 MoveIt 2 或 Gazebo，理由是可验证性：

- 仓库的 ROS 2 镜像只安装 `navigation2` 与 `ros-gz`，**没有 MoveIt 2**；CI 的 `ros-build` 任务不构建动作客户端代码。
- 该机器人平台（XLeRobot）的机械臂基线是 LeRobot 直连 + 位置伺服，不是 MoveIt 2 管理的规划组；仓库的机械臂动作走 `action_chunk` 与 `policy_execution`。
- `deploy/navigation/Dockerfile` 没有 MoveIt 2 依赖，`GripperCommand` 在仓库内不存在。

因此本版交付：**领域工具 + 参数契约 + 结果归一化 + 注册/执行/健康/超时/取消/复合链 + 单测（mock 适配器）+ 既有 MuJoCo 与 ROS 桥上的集成验证**，并在 `docs/development/arm-moveit-adapter.md` 给出 MoveIt 2／`GripperCommand` 的接入契约、需要新增的 apt 依赖、`MoveGroup` 动作字段映射与验收步骤。**不写"已对接"的代码**，因为没有环境可以证明它工作。

**理由。** 任务要求"不依赖真实硬件可测"，同时要求"仿真中完成 pick_object"。后者在本仓库可以用既有的 MuJoCo 参考工位真实完成并留证；前者可以用 mock 适配器穷举。而声称 MoveIt 2 可用却无法运行，会让整套工具层的可信度归零——这正是仓库一贯拒绝的做法（见 `docs/production/v1-release-status.md` 的分层验收原则）。

## 验收映射

| 请求要求 | 本版实现 | 证据 |
| --- | --- | --- |
| `ToolResult` / `RobotTool` / `ToolRegistry` / `ToolExecutor` | `tool_layer.py` + `tools/` 包，适配到既有 `ExecuteSkill` 与 `ToolRegistry` 模式 | 单测穷举错误码映射与注册/发现 |
| 导航类 5 个工具 | `tools/navigation.py`，复用既有导航执行路径 | 单测 + 既有导航验收脚本保持通过 |
| 机械臂类 5 个工具 | `tools/arm.py`，语义参数 → 动作集翻译 | 单测 + 假适配器端到端 |
| 夹爪类 4 个工具 | `tools/gripper.py`，`grasp` 返回 `object_detected` | 单测 |
| 感知/查询 6 个工具 | `tools/perception.py` + `semantic_map.py` | 单测 + 既有场景查询 |
| 复合技能 3 个工具 | `tools/skills.py`，记录子步骤与 `failed_step` | 单测 + MuJoCo 端到端 |
| 安全类 4 个工具 | `tools/safety.py`，急停绕过队列、`safety_level=4` | 安全测试 |
| `tools.json` | `scripts/export_llm_tools.py` 从契约生成 | 与 `run_plugin schema` 交叉校验 |
| 分布式：超时/取消/重试/健康 | `tool_executor.py` | 分布式故障测试 |
| ROS 2 映射 | Nav2 复用既有；MoveIt 2／GripperCommand 给出接入契约与步骤 | 文档 + 不以"已对接"声称 |
