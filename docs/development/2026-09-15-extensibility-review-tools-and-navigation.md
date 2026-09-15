# 可扩展性评审：工具封装与导航受阻（v1 不动结构，只留接口）

问题来自实际使用：**抓取同一个工具要面对软的、硬的、易碎的物品**；**导航到目标点时地图可能过期、路径上出现障碍物导致到不了**。本文先如实描述现状（含代码位置），再判断哪些地方已经可扩展、哪些地方是写死的，最后给出"v1 只加接口、不重构"的落地建议与验收口径。

---

## 一、工具是怎么定义的（现状）

一个能力（capability）在系统里由**五处**共同定义，缺一处就进不来：

| 层 | 位置 | 作用 |
| --- | --- | --- |
| 规范工具名 | `robot/gateway/tangying_robot_gateway/contracts.py` `CANONICAL_TOOLS` / Go 侧 `core/robotcontract/contract.go` `canonicalTools` | 名字白名单，两侧必须一致（否则 profile 校验直接拒） |
| 参数契约 | `TOOL_PARAMETERS`（每个工具一个 Pydantic 契约） | 参数形状与取值边界，运行时**逐个校验后才派发** |
| 安全分类 | `PHYSICAL_TOOLS` / `MUTATES_WORLD_TOOLS` | 是否需要物理准入；是否需要"动作后的新观测"才允许判定完成 |
| 技能清单 | `skills/manipulation/plugin.go` `Catalog()` | `SafetyLevel`、`RequiredParameters`、`AllowedSafetyProfiles`、`ApprovalPolicy`、`MutatesWorld`、`DefaultLeaseMS` |
| 本体声明 | `RobotProfile.tools`（由运行时能力广告生成） | 这台机器人**有没有**这个能力；没有就在计划阶段被拒（不是运行时才发现） |

**已经留好的扩展口（比多数人以为的多）**：

1. **学习型策略已经能进契约**：`ActionParameters` 带 `policy_execution`（`framework: deterministic|vla|imitation|reinforcement` + `artifact_sha256` + `manifest_revision` + `inference_id`/`observation_id`/`elapsed_ms`）。也就是说"用某个具体权重去抓"这件事在**参数层已经可表达**，不需要新工具名。
2. **能力是每台机器人可裁剪的**：`RobotProfile.tools` + `AllowedSafetyProfiles` 让"这台机器没有吸盘"这类差异在计划阶段就失败关闭。
3. **工具层（`tools/`）与运行时是分开的两套**：Python 工具层（MCP/插件面）可以自由增删工具而不动运行时；运行时面则要求规范白名单——**这正是"扩展不破坏契约"的设计**。

**写死的地方（本轮真正的发现）**：

| 位置 | 现状 | 问题 |
| --- | --- | --- |
| `sim/mujoco/tangying_sim/rgbd_runtime.py:412` | `half_height = {"cup": 0.06, "bottle": 0.08}[entity.category]` | **物体类别是代码常量**：换一个类别直接 `KeyError`，而不是"这类物体我还不会抓"的可诊断失败 |
| `rgbd_runtime.py:346`（`has_object`）与 `:412`（放置高度） | `e.category in {"cup", "bottle"}` | 可抓集合写死在检测器分支里 |
| 全仓库 | **没有材质/易碎/可变形任何字段** | 实体只有 `category` + `attributes{color,name,recognition}`；"软/硬/易碎"无处声明 |
| 抓取执行 | 参考控制器固定（合爪到固定开度 + 固定抬升） | 没有夹持力/顺应性/速度预算这类参数 |
| 后置条件 | `verify_grasp` = 3 帧稳定 `held_by:robot`；`verify_placement` = 3 帧稳定 `inside:<dest>` | 判据是**关系谓词**，无法表达"抓稳且未压碎" |

**结论（工具封装）**：**结构是对的，缺的是"物体属性 → 抓取策略"这条轴**。现在的封装把"怎么抓"整个藏在运行时实现里，工具面只暴露 `targetRef`；这在小规模下是优点（安全、可审计），但把"新物体类别"的代价推到了**改运行时代码**这一层。

---

## 二、导航到目标点但地图过期 / 路径被挡（现状）

**现在有几层保护（都是真的，不是文档话术）**：

| 层 | 机制 | 位置 |
| --- | --- | --- |
| 派发前 | 命令带 deadline/lease/幂等键，且要求**新鲜观测**才允许物理动作 | `edge/agent/runner.go` `CommandAtDispatch` |
| 参考栈逐脉冲 | 每一步前进都必须有**同帧深度证据**证明扫掠体有净空，否则拒绝并给出原因（`NAV_PATH_OCCLUDED`/`NAV_DEPTH_UNKNOWN`/`NAV_OBSTACLE_OBSERVED`/`NAV_PATH_OUT_OF_VIEW`） | `sim/mujoco/tangying_sim/rgbd_navigation.py` `check_navigation` |
| 规划层 | 未知格**不通行**、按底盘足迹膨胀、只在认证净空位姿之间规划 | `robot/gateway/tangying_robot_gateway/grid_navigation.py` |
| 目标层 | 房间目标在地图认证不足时被替换为最近的认证净空位姿（本轮新增，调整量随目标发布） | `rgbd_runtime._certify_semantic_goals` |
| ROS 部署栈 | NavFn 全局（`allow_unknown: false`，跑在**已发布的地图**上）+ DWB 局部，4×4 m 滚动体素层带 **marking/clearing**，`track_unknown_space: true` | `robot/ros2_ws/src/tangying_navigation/config/nav2.yaml` |
| 任务层 | 拒绝变成步骤失败码；`RECOVERABLE_FAILURE` 可续跑，物理结果未知则**永不重放** | `edge/agent`、`internal/localapp` |

**真正的缺口（按影响排序）**：

1. **任务期间不会更新在用地图**：地图是"勘测产物"，`mapping.activate` 之后就只读（`GOAL_NOT_CLEAR` 的提示语就是"请补扫"）。于是沙发被挪动、纸箱被放下这类**持久性变化**，任务期只能靠局部层躲开，躲不开就失败——**系统不会把"这里现在有障碍"写回地图**。
2. **"被挡住"只是拒绝码，不是可决策信息**：DWB 有局部绕行，参考栈有"停下重规划 + `avoid_xy` 记忆"，但**计划层没有重试预算/策略**——一条导航步骤失败后，是"再试一次""换目标位姿"还是"回安全位姿"，由 runner 的通用重试规则决定，而不是由"为什么挡"决定。
3. **没有"到不了 → 给你备选"的契约**：`plan_work_area` 能给可达候选位姿，但它只在工具层（MCP）可用，**Go 任务计划还没有消费它**；因此"换一个能站的位姿"目前只能靠本轮新增的目标认证做一次静态吸附，做不到"沿路线逐段重选"。
4. **地图过期没有显式事件**：`source freshness` 管的是**传感器观测**；"地图相对当前观测已经过时"（例如反复在同一位置观测到占位格子而地图上仍是自由）没有对应的世界事件，因此无法触发"局部地图维护"。

---

## 三、建议：v1 只加接口，四件事都不重构

原则：**不引入第二套执行路径**，只在已有契约上加"可选的、缺省不变"的字段与事件。

### 扩展点 1：物体的物理属性作为**观测数据**，不是代码常量

- **加什么**：实体属性允许 `material`（`rigid|soft|fragile|deformable|granular`）、`mass_g`、`max_grip_force_n` 这类**可选**键（`attributes` 已经是 `map[string]string`，无需改协议）。
- **谁填**：检测器/上层标注；`unknown` 是合法值，表示"没识别出来"。
- **怎么用**：`manipulation.pick` 增加**可选**参数 `grasp_profile`（缺省 = 现在行为），运行时按 `grasp_profile` 选择抓取策略；识别不到就沿用当前参考控制器，**不因为缺字段而失败**。
- **为什么是接口而不是实现**：v1 只需要"能声明、能透传、能被审核"；真正的力控/顺应性实现可以后补。
- **验收**：① 未声明 `material` 的物体行为与今天**逐位一致**（回归测试）；② 声明 `fragile` 时，抓取参数里出现受限的夹持力/速度预算并被记录进证据；③ 未知材质时给出**可诊断的拒绝**（而不是 `KeyError`）。

### 扩展点 2：抓取策略注册表（把"怎么抓"从运行时里搬出来）

- **加什么**：`GraspStrategyRegistry`：`(category, material, gripper_kind) → strategy_id + 参数 + 后置条件`。第一版只放一条"参考控制器"策略，行为与今天相同。
- **落在哪**：`robot/gateway`（策略目录，随地图/标定一样版本化）；运行时按 `strategy_id` 取参数执行；`RobotProfile.tools` 不变。
- **为什么**：这是"换物体不改代码"的关键一步；也是 `policy_execution`（VLA/IL/RL）真正的落点——学习策略就是一个 `strategy_id`。
- **验收**：新增一类物体（例如瓶子）**不改运行时代码**即可抓取；策略变更不影响已完成任务的可复现性（策略 id + 版本进证据）。

### 扩展点 3：把"路径被挡"升级为可决策事件

- **加什么**：导航拒绝时除错误码外，附**结构化原因**（`blocked_by`: 动态障碍/未知区域/地图过期；`observed_at`；`evidence_id`；`attempt`）。
- **任务层**：导航步骤允许声明**重试策略**（`max_attempts`、`on_blocked: replan|alternative_goal|safe_pose|abort`）。第一版只实现 `replan`（= 今天的行为）+ `abort`。
- **为什么**：现在"再试一次"和"换地方"是同一件事；把原因带出来之后，计划才能真正按原因分支，而且**审计时能回答"为什么这条步骤重试了三次"**。
- **验收**：同一障碍注入下，① 事件流里能看到 `blocked_by` 与证据 id；② `max_attempts` 生效且不超过预算；③ 走到 `abort` 时任务进入可恢复失败而不是卡死。

### 扩展点 4：地图维护（**可选**，v1 只留事件与只读接口）

- **加什么**：一个显式的世界事件 `MAP_STALE_SUSPECTED`（同一位置重复观测到的占位与在用地图冲突）与一个**只读**接口 `map.conflicts()`：报告"哪些格子与最近观测冲突"。
- **不做**：v1 **不自动改写**在用地图。原因很实在：地图是已验收的导航依据，静默变更会让"昨天能走的路线今天为什么不能走"无法解释。要改就走已有的 `mapping.start {baseMapId}` 续建流程（有版本、有证据、有确认）。
- **验收**：注入"同一位置连续 N 次观测到障碍"后，① 出现 `MAP_STALE_SUSPECTED` 事件并给出冲突格子数；② 在用地图**逐位不变**；③ 控制台能提示"建议续建"。

### 关于"到不了就给备选"的最小闭环

把 `plan_work_area` 的输出接进 Go 计划的导航步骤（`P0-2b`，此前已记录）：目标不可达时，用同一房间的认证候选位姿重试，并把**实际下达的位姿**写进步骤参数与到达核验（保持"核验对象 = 下达对象"）。这一条独立于上面四点，可以单独做。

---

## 四、明确不做（以及为什么）

| 不做 | 原因 |
| --- | --- |
| 为每种材质新增工具名（`pick_fragile`、`pick_soft`…） | 工具名爆炸，且计划/审批/白名单全要跟着变；属性 + 策略注册表能用一处解决 |
| 让模型直接输出夹持力/关节角 | 违反现行边界（模型只说"拿杯子"）；力参数应由策略与安全边界决定 |
| 任务期自动改写在用地图 | 会让导航依据不可解释；续建流程已经具备版本与确认 |
| 引入第二套导航实现（新的规划器/新的控制器） | v1 不需要；DWB + 参考栈已覆盖局部动态障碍，缺的是"原因与决策"而不是"算法" |

---

## 五、一句话回答

- **工具封装**：结构是对的（规范名 + 参数契约 + 安全分类 + 本体声明 + 学习策略字段），**缺的是"物体属性 → 抓取策略"这条轴**；它现在是运行时代码里的字面量（`{"cup": 0.06, "bottle": 0.08}`），所以换物体等于改代码。
- **导航受阻**：**保护层是真的**（逐脉冲深度证据、未知不通行、认证目标、局部 costmap marking/clearing），**缺的是决策与记忆**——"被挡住"只是拒绝码，"地图过期"没有事件，任务期不改写地图（这条是**刻意的**，应该保留，用续建流程代替）。
- **v1 要做的**：四个**可选**扩展口（属性字段、策略注册表、结构化受阻原因 + 重试策略、地图冲突只读事件），全部缺省不变、不新增工具名、不引入第二套执行路径。
