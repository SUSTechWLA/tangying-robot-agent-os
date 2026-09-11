# MoveIt 2 机械臂适配契约

本文规定把笛卡尔机械臂请求接到 IK 求解器（MoveIt 2 或厂商 SDK）的**接入契约与验收清单**。

**本仓库没有对接 MoveIt 2。** `deploy/robot/navigation/Dockerfile` 没有安装任何 MoveIt 2 包，CI 的 `ros-build` 任务也不构建 MoveIt 2 代码；`GripperCommand` 在仓库内**不存在**。因此下面关于 MoveIt 2 的全部内容都是**契约和清单**：给出接口形状、依赖位置、错误映射和验证步骤，**尚未在本仓库验证**，也没有实机验证。本文不声称任何 MoveIt 2 路径已经可用；在完成第 6 节验收之前，请继续按第 1 节理解现状。

## 1. 当前状态

| 能力 | 今天的行为 | 位置 |
| --- | --- | --- |
| `move_arm_to_joints` | **可用**：关节目标直通，映射为 `<prefix><joint>.pos` | [`gateway_adapter.py`](../../robot/gateway/tangying_robot_gateway/gateway_adapter.py) `plan_arm_motion` |
| `grasp` / `release` / `set_gripper_width` | **可用**：下发 `<prefix>gripper.pos`，仍走 `arm.move` | [`manipulation.py`](../../robot/gateway/tangying_robot_gateway/tools/manipulation.py) `build_gripper_tools` |
| `home_arm` / `get_arm_state` | **可用**：`recover_to_safe_pose` 与新鲜观测 | 同上 |
| `move_arm_to_pose` | **失败关闭**，`ArmPlan(ok=False, code="IK_UNAVAILABLE")` | `GatewayRobotAdapter.plan_arm_motion` |
| `move_arm_relative` | **失败关闭**，同样 `IK_UNAVAILABLE` | 同上 |

已交付的机械臂路径是**关节目标 action chunk**：`arm.move` 携带 `action_chunk`（`<joint>.pos` 条目），由 [`safety.py`](../../robot/gateway/tangying_robot_gateway/safety.py) 校验，经 `ExecuteSkill` 的 gRPC / ROS 2 契约派发到 [`node.py`](../../robot/ros2_ws/src/xlerobot_adapter/xlerobot_adapter/node.py) 与 [`driver.py`](../../robot/ros2_ws/src/xlerobot_adapter/xlerobot_adapter/driver.py)。该平台上没有接入 IK 求解器，所以笛卡尔请求**不可能**被诚实地转换成关节目标。

因此失败关闭是**正确行为**，不是缺陷：猜一组关节角去够一个笛卡尔点，等价于让机械臂朝未经验证的方向运动，撞到什么由现场决定。`IK_UNAVAILABLE` 已登记在 `tool_layer.TOOL_LAYER_CODE_TABLE` 中，投影为 `UNREACHABLE` / `PLANNING` 且 `recoverable=false`：这是规划层结论（这个型号没有求解器），不是硬件故障，因此模型应当改请求，而不是重试或报修。

## 2. `plan_arm_motion` 契约

端口定义在 [`registry.py`](../../robot/gateway/tangying_robot_gateway/tools/registry.py) 的 `RobotAdapter`。工具层只通过这个入口拿到下发键，它自己永远不拼关节名。

```python
def plan_arm_motion(
    self,
    *,
    component: str,
    joints: tuple[float, ...] | None = None,
    target_pose: tuple[float, ...] | None = None,
    relative: tuple[float, float, float] | None = None,
    frame_id: str = "base_link",
    velocity_scaling: float = 0.5,
) -> ArmPlan: ...
```

| 参数 | 含义 |
| --- | --- |
| `component` | `left_arm` / `right_arm` / `both_arms`（`manipulation.ARM_COMPONENTS`）；决定键前缀，不接受调用方自选前缀。 |
| `joints` | 按 profile 关节顺序的目标角（rad）。与 `target_pose`、`relative` 三选一。 |
| `target_pose` | `(x, y, z, roll, pitch, yaw)`，米与弧度，`frame_id` 坐标系下的绝对位姿。 |
| `relative` | `(dx, dy, dz)`，米；`move_arm_relative` 已把模长限制在 0.30 m。 |
| `frame_id` | 目标位姿所在坐标系；默认 `base_link`，相对移动默认 `end_effector`。 |
| `velocity_scaling` | 0–1，规划与执行的缩放比例；不是硬件速度寄存器。 |

`ArmPlan` 返回字段：

| 字段 | 语义 |
| --- | --- |
| `ok` | 是否得到可下发的计划。`False` 时 `waypoints` 必须为空。 |
| `waypoints` | 已可直接作为 `action_chunk` 的字典元组，键为 `<prefix><joint>.pos`。 |
| `joints` | 目标关节角，供报告使用；不用于构造命令。 |
| `final_pose` | 已知时的末端位姿，供报告使用。 |
| `code` / `message` | 失败原因码与人类可读说明。 |
| `warnings` | 非致命的提示（例如接近限位、降速）。 |

必须遵守的语义：

1. **返回而不是抛出。** 规划失败一律 `ok=False` + `code`。`GatewayRobotAdapter` 目前只在关节数量超过 profile 声明时抛 `ValueError`；新实现应把"未接线/无求解器"表达为 `IK_UNAVAILABLE`，把参数自相矛盾表达为 `INVALID_PARAM`（两者都已在 `tool_layer` 的码表里），只有真的无法返回计划对象时才允许抛出。
2. **不越关节限位。** 任何 waypoint 的关节值都必须在 `RobotProfile.joints[i].lower/upper` 内。注意 profile 用 `<jointName>.position` 声明 `actionLimits`，而 supervisor 校验的是 `<prefix><jointName>.pos`，两套键名不互相校验，**必须由适配器同时保证**。
3. **每个键都满足 supervisor 规则。** `safety._validate_action_value` 要求：`key.startswith(ALLOWED_ACTION_PREFIXES)`（`left_arm_`、`right_arm_`、`head_`）且 `key.endswith(".pos")`；数值有限、`abs(value) <= MAX_ABSOLUTE_ACTION_VALUE`(100.0)；`x.vel` / `theta.vel` 一律拒绝；`gripper.pos` 还要求 0–100。逐点前向检查，不要等到运行时被拒。
4. **waypoint 数量不超过 `MAX_ACTION_CHUNK_LENGTH`(64)。** 一次 `arm.move` 只下发一个末点轨迹；若确要多点，必须整体截断到 64 以内。
5. **`both_arms` 需要显式展开**，且两侧键前缀不同，不能复用同一组关节名。
6. **规划降速不等于执行降速。** `velocity_scaling` 只影响 MoveIt 的 `max_velocity_scaling_factor` / `max_acceleration_scaling_factor`；`gateway_adapter._scaled_parameters` 的软件限速只在派发 `arm.move` 时生效，不会进入 `plan_arm_motion`。接入方若要两处一致，需在自己的 factory 里显式取用同一份限值。
7. **结果必须与观测一致。** `arm.move` 是写工具，成功仍不算完成：证据见 `docs/development/robot-adapters.md` 的"会改变世界的工具必须能被新鲜观测确认"。

## 3. `MoveItArmAdapter` 实现草图

用 MoveGroup action 取笛卡尔规划。接口名固定为 `/move_action`（`moveit_msgs.action.MoveGroup`，goal 内是 `moveit_msgs.msg.MotionPlanRequest`），结果里是 `motion_plan_response.trajectory.joint_trajectory`。

```python
# 草图（未验证）。放在部署方本地驱动包里，不要放进 gateway 包。
from __future__ import annotations

import math
import time

from control_msgs.action import GripperCommand
from geometry_msgs.msg import Point, Pose, Quaternion
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import (Constraints, MotionPlanRequest, MoveItErrorCodes,
                             OrientationConstraint, PositionConstraint)
from rclpy.action import ActionClient

from tangying_robot_gateway.safety import ALLOWED_ACTION_PREFIXES, MAX_ACTION_CHUNK_LENGTH
from tangying_robot_gateway.tools.registry import ArmPlan

MOVE_ACTION = "/move_action"  # moveit_msgs.action.MoveGroup，goal 里是 MotionPlanRequest
PLAN_TIMEOUT_S = 5.0  # 工具层给 arm.move 的总预算是 15.0 s，规划要留出执行余量

#: MoveItErrorCodes -> 工具层已经认识的 ArmPlan.code
GROUPS = (
    ("IK_UNAVAILABLE", (MoveItErrorCodes.NO_IK_SOLUTION,)),
    ("TARGET_UNREACHABLE", (MoveItErrorCodes.PLANNING_FAILED, MoveItErrorCodes.INVALID_MOTION_PLAN,
                            MoveItErrorCodes.MOTION_PLAN_INVALIDATED_BY_ENVIRONMENT_CHANGE,
                            MoveItErrorCodes.UNABLE_TO_AQUIRE_SENSOR_DATA)),
    ("COLLISION_RISK", (MoveItErrorCodes.START_STATE_IN_COLLISION,
                        MoveItErrorCodes.GOAL_IN_COLLISION,
                        MoveItErrorCodes.START_STATE_VIOLATES_PATH_CONSTRAINTS,
                        MoveItErrorCodes.GOAL_VIOLATES_PATH_CONSTRAINTS,
                        MoveItErrorCodes.GOAL_CONSTRAINTS_VIOLATED)),
    ("TIMEOUT", (MoveItErrorCodes.TIMED_OUT, MoveItErrorCodes.CONTROL_FAILED,
                 MoveItErrorCodes.PREEMPTED)),
    # 配置或场景不可用：这不是"目标不好"，改坐标没有意义。
    ("HARDWARE_ERROR", (MoveItErrorCodes.INVALID_GROUP_NAME,
                        MoveItErrorCodes.INVALID_GOAL_CONSTRAINTS,
                        MoveItErrorCodes.INVALID_ROBOT_STATE, MoveItErrorCodes.INVALID_LINK_NAME,
                        MoveItErrorCodes.FRAME_TRANSFORM_FAILURE,
                        MoveItErrorCodes.COLLISION_CHECKING_UNAVAILABLE,
                        MoveItErrorCodes.ROBOT_STATE_STALE)),
)
ERROR_CODES = {value: name for name, values in GROUPS for value in values}


class MoveItArmAdapter:
    """把 plan_arm_motion 接到 MoveIt 2；失败一律返回 ArmPlan，不抛异常。"""

    def __init__(self, backend, node, *, planning_group: str = "right_arm") -> None:
        self.backend, self.node, self.planning_group = backend, node, planning_group
        self._move = ActionClient(node, MoveGroup, MOVE_ACTION)
        self._gripper = ActionClient(node, GripperCommand, "/right_gripper_controller/gripper_cmd")

    def plan_arm_motion(self, *, component, joints=None, target_pose=None, relative=None,
                        frame_id="base_link", velocity_scaling=0.5) -> ArmPlan:
        if sum(item is not None for item in (joints, target_pose, relative)) != 1:
            return ArmPlan(ok=False, code="INVALID_PARAM",
                           message="exactly one of joints, target_pose, relative is required")
        if joints is not None:
            return self._passthrough(component, joints)
        return self._cartesian(component, target_pose, relative, frame_id, velocity_scaling)

    def _passthrough(self, component: str, joints) -> ArmPlan:
        """关节目标直通：仍然是 <prefix><joint>.pos 的 action chunk。"""
        names, values = self._joint_names(), []
        if len(joints) != len(names):
            return ArmPlan(ok=False, code="INVALID_PARAM",
                           message=f"profile declares {len(names)} joints, got {len(joints)}")
        for index, value in enumerate(joints):
            limited = self._clamp(index, float(value))
            if limited is None:
                return ArmPlan(ok=False, code="WORKSPACE_LIMIT",
                               message=f"joint target {index} outside the profile limits")
            values.append(limited)
        waypoint = {f"{self._prefix(component)}{name}.pos": value
                    for name, value in zip(names, values, strict=True)}
        return ArmPlan(ok=True, waypoints=(waypoint,), joints=tuple(values))

    def _cartesian(self, component, target_pose, relative, frame_id, velocity_scaling) -> ArmPlan:
        """笛卡尔/相对请求：交给 MoveGroup 做 IK，只取关节轨迹末点。"""
        if not self._move.wait_for_server(timeout_sec=0.5):
            return ArmPlan(ok=False, code="IK_UNAVAILABLE",
                           message=f"no {MOVE_ACTION} server: no IK solver is wired")
        if target_pose is not None:
            if frame_id != "base_link":
                return ArmPlan(ok=False, code="IK_UNAVAILABLE",
                               message=f"frame {frame_id} needs a TF lookup that is not wired")
            goal = tuple(float(value) for value in target_pose)
        else:
            goal = self._compose_relative(relative, frame_id)  # 未接线时返回 None
            if goal is None:
                return ArmPlan(ok=False, code="IK_UNAVAILABLE",
                               message="relative moves need the current end-effector pose")
        request = self._motion_plan_request(component, goal, velocity_scaling)
        failure, trajectory = self._call(request, timeout_s=PLAN_TIMEOUT_S)  # 有界超时
        if failure is not None:
            return failure
        return self._waypoints(component, list(trajectory.joint_names),
                               list(trajectory.points[-1].positions))

    def _motion_plan_request(self, component, goal, velocity_scaling) -> MotionPlanRequest:
        """笛卡尔目标 = 位置约束 + 姿态约束；关节名映射见 _waypoints。"""
        link = self._tip_link(component)  # 末端链路名，来自 profile.endEffectors
        position = PositionConstraint()
        position.header.frame_id = "base_link"
        position.link_name = link
        position.constraint_region.primitive_poses.append(
            Pose(position=Point(x=goal[0], y=goal[1], z=goal[2]),
                 orientation=Quaternion(w=1.0, x=0.0, y=0.0, z=0.0)))
        orientation = OrientationConstraint()
        orientation.header.frame_id = "base_link"
        orientation.link_name = link
        orientation.orientation = self._quaternion(goal[3], goal[4], goal[5])
        for axis in ("absolute_x_axis_tolerance", "absolute_y_axis_tolerance",
                     "absolute_z_axis_tolerance"):
            setattr(orientation, axis, 0.1)  # 允许的末姿态误差，按现场标定收紧
        request = MotionPlanRequest()
        request.group_name = self.planning_group
        request.num_planning_attempts = 3
        request.allowed_planning_time = PLAN_TIMEOUT_S
        request.max_velocity_scaling_factor = float(velocity_scaling)
        request.max_acceleration_scaling_factor = float(velocity_scaling)
        constraints = Constraints()
        constraints.position_constraints.append(position)
        constraints.orientation_constraints.append(orientation)
        request.goal_constraints.append(constraints)
        return request

    def _call(self, request, *, timeout_s: float):
        """返回 (失败 ArmPlan, None) 或 (None, JointTrajectory)。"""
        goal = MoveGroup.Goal()
        goal.request = request
        goal.planning_options.plan_only = True  # 只规划；执行仍然走 arm.move
        try:
            handle = self._wait_future(self._move.send_goal_async(goal), timeout_s)
            if not handle.accepted:
                return ArmPlan(ok=False, code="HARDWARE_ERROR",
                               message="MoveGroup rejected the goal"), None
            response = self._wait_future(handle.get_result_async(), timeout_s).result
        except TimeoutError as exc:
            return ArmPlan(ok=False, code="TIMEOUT", message=str(exc)), None
        except Exception as exc:  # noqa: BLE001 - 通信故障不能伪装成规划失败
            return ArmPlan(ok=False, code="HARDWARE_ERROR", message=str(exc)), None
        if response.error_code.val != MoveItErrorCodes.SUCCESS:
            code = ERROR_CODES.get(response.error_code.val, "HARDWARE_ERROR")
            return ArmPlan(ok=False, code=code,
                           message=response.error_code.message or code), None
        trajectory = response.planned_trajectory.joint_trajectory
        if not trajectory.points:
            return ArmPlan(ok=False, code="TARGET_UNREACHABLE", message="empty trajectory"), None
        return None, trajectory

    def _wait_future(self, future, timeout_s: float):
        """与 tangying_ros_gateway.node.GatewayNode._wait_future 相同的阻塞等待。"""
        deadline = time.monotonic() + timeout_s
        while not future.done():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"MoveGroup action timed out after {timeout_s:.1f}s")
            time.sleep(0.01)
        return future.result()

    def _waypoints(self, component, names, final) -> ArmPlan:
        """轨迹末点 -> action_chunk：关节名映射、限位与键形状都在这里。"""
        profile_names = self._joint_names()
        if not names or len(names) != len(final) or any(n not in profile_names for n in names):
            return ArmPlan(ok=False, code="HARDWARE_ERROR", message="trajectory is malformed")
        values = []
        for name, value in zip(names, final, strict=True):
            limited = self._clamp(profile_names.index(name), float(value))
            if limited is None:
                # 拒绝而不是截断：截断后的末点是另一个位姿，不是规划器求出的解。
                return ArmPlan(ok=False, code="WORKSPACE_LIMIT",
                               message=f"{name} violates the profile joint limits")
            values.append(limited)
        prefix = self._prefix(component)
        waypoint = {f"{prefix}{name}.pos": value for name, value in zip(names, values, strict=True)}
        if len(waypoint) > MAX_ACTION_CHUNK_LENGTH:
            return ArmPlan(ok=False, code="WORKSPACE_LIMIT", message="plan exceeds 64 waypoints")
        # 逐点前向检查 supervisor 规则，不要等到运行时被拒。
        if not all(key.startswith(ALLOWED_ACTION_PREFIXES) and key.endswith(".pos")
                   for key in waypoint):
            return ArmPlan(ok=False, code="HARDWARE_ERROR", message="actuator key shape is invalid")
        return ArmPlan(ok=True, waypoints=(waypoint,), joints=tuple(values))

    # 端口上的其余成员：profile 读取、限位与键前缀

    def _profile(self):
        return getattr(self.backend, "_profile", None)

    def _joint_names(self) -> list[str]:
        return [joint.name for joint in getattr(self._profile(), "joints", ()) or ()]

    def _clamp(self, index: int, value: float) -> float | None:
        """有限且落在 profile 关节限位内时返回原值，否则返回 None（不截断）。"""
        joints = list(getattr(self._profile(), "joints", ()) or ())
        if index >= len(joints) or not math.isfinite(value):
            return None
        joint = joints[index]
        return value if joint.lower <= value <= joint.upper else None

    @staticmethod
    def _prefix(component: str) -> str:
        return "left_arm_" if component == "left_arm" else "right_arm_"

    def gripper_waypoints(self, component: str, width: float):
        """保留端口形状：仍返回 <prefix>gripper.pos，再翻成 GripperCommand（第 5 节）。"""
        return ({f"{self._prefix(component)}gripper.pos": float(width)},)

    # 以下三项依赖具体型号的链路名、TF 与当前位姿，刻意留空

    def _tip_link(self, component: str) -> str:
        raise NotImplementedError  # 由 profile.endEffectors 推导，未接线

    @staticmethod
    def _quaternion(roll: float, pitch: float, yaw: float):
        raise NotImplementedError  # roll/pitch/yaw -> geometry_msgs.Quaternion，未接线

    def _compose_relative(self, offset, frame_id):
        return None  # 需要最新末端位姿与 TF，未接线
```

草图中的 `_tip_link`、`_quaternion`、`_compose_relative` **刻意留空**：它们依赖具体型号的末端链路名、TF 树和当前位姿来源，写死一个"看起来能跑"的版本正是本文要避免的过度声明。按代码位置，`MoveItArmAdapter` 放在部署方的本地驱动包里（与 `robot-adapters.md` 的 `deployment.local_driver` 同层），只在 factory 里替换掉 `GatewayRobotAdapter`，不要改 gateway 包本身。`self._gripper`（`GripperCommand` action client）在草图里只是提前建好，消费方式见第 5 节；夹爪入口仍然是端口上的 `gripper_waypoints`。

错误映射表（工具层已经认识这些码，见 `tool_layer._RUNTIME_CODE_TABLE`）：

| MoveIt 情况 | `ArmPlan.code` | 工具层投影 | 现场含义 |
| --- | --- | --- | --- |
| 无 action server / `frame_id` 未接线 / `NO_IK_SOLUTION`(-31) | `IK_UNAVAILABLE` | `HARDWARE_ERROR`（未知终态，不可重试） | 求解能力缺失，不要改坐标重试 |
| `PLANNING_FAILED`(-1)、`INVALID_MOTION_PLAN`(-2)、空轨迹 | `TARGET_UNREACHABLE` | `UNREACHABLE`（PLANNING） | 换目标位姿，不要原样重试 |
| `START_STATE_IN_COLLISION`(-10)、`GOAL_IN_COLLISION`(-12)、`GOAL_CONSTRAINTS_VIOLATED`(-14) | `COLLISION_RISK` | `COLLISION_RISK`（PLANNING） | 先改环境或路径 |
| `TIMED_OUT`(-6)、`CONTROL_FAILED`(-4)、`PREEMPTED`(-7) | `TIMEOUT` | `TIMEOUT`（TRANSIENT） | 可有限重试，但要记录 |
| `INVALID_GROUP_NAME`(-15)、`FRAME_TRANSFORM_FAILURE`(-21)、`COLLISION_CHECKING_UNAVAILABLE`(-22)、`ROBOT_STATE_STALE`(-23)、响应畸形 | `HARDWARE_ERROR` | `HARDWARE_ERROR`（未知终态） | 配置或场景不可用，先查环境 |

`IK_UNAVAILABLE` 目前**不在** `tool_layer._RUNTIME_CODE_TABLE` 里，所以它落到"未知码"分支，投影为 `HARDWARE_ERROR` / `UNKNOWN_OUTCOME` 且 `recoverable=false`。要不要把它显式登记进 Python 与 Go 两侧的码表（`core/closedloop` 的分类同步由 `tests/tool_layer/test_tool_contract.py` 保证）由接入方决定；在登记之前，上面的投影就是实际行为。

## 4. 需要新增的依赖

在 ROS 2 Jazzy 上需要的 apt 包（`ros:jazzy-ros-base` 基础镜像不含 MoveIt 2）：

```bash
apt-get update && apt-get install -y --no-install-recommends \
  ros-jazzy-moveit ros-jazzy-moveit-configs-utils \
  ros-jazzy-moveit-ros-move-group ros-jazzy-moveit-kinematics \
  ros-jazzy-moveit-planners-ompl ros-jazzy-control-msgs
```

| 包 | 用途 |
| --- | --- |
| `ros-jazzy-moveit` | MoveIt 2 元包：`move_group`、`moveit_msgs`。 |
| `ros-jazzy-moveit-configs-utils` | 启动配置与 `moveit_configs_utils`，本仓尚未使用。 |
| `ros-jazzy-moveit-ros-move-group` | `MoveGroup` action server（`/move_action`）。 |
| `ros-jazzy-moveit-kinematics` | IK 插件（KDL 等），`IK_UNAVAILABLE` 能否消除取决于它。 |
| `ros-jazzy-moveit-planners-ompl` | 采样规划器；没有规划器就只有 IK，没有轨迹。 |
| `ros-jazzy-control-msgs` | `GripperCommand` action 类型（见第 5 节）。 |

加在哪里：

| 文件 | 改动 |
| --- | --- |
| [`deploy/robot/navigation/Dockerfile`](../../deploy/robot/navigation/Dockerfile) | 在现有 `apt-get install` 列表补上面的包。该镜像今天只装 `navigation2`、`nav2-bringup`、`ros-gz-*`、`rtabmap-ros`，**没有 MoveIt 2**。 |
| [`robot/ros2_ws/src/xlerobot_adapter/package.xml`](../../robot/ros2_ws/src/xlerobot_adapter/package.xml) | 若适配器节点 import `moveit_msgs` / `control_msgs`，用 `<exec_depend>` 声明，并由 `rosdep install --from-paths robot/ros2_ws/src --ignore-src` 解析。 |
| [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) | `ros-build` 任务今天只跑 `colcon build` 与 `colcon test --packages-select tangying_navigation tangying_dwb_critics`，**不安装也不构建 MoveIt 2**。新增规划节点必须显式加包名并有自己的 job，否则 CI 不会覆盖它。 |

`Makefile` 的 `test`（`test-go` / `test-python` / `test-web`）不构建 ROS 2，因此这几个 apt 包对 `make test` 没有影响。依赖装好不等于接通：还必须有 URDF/SRDF、`move_group` 配置和 IK 插件，`plan_arm_motion` 才会从 `IK_UNAVAILABLE` 变成可规划——这些资产**尚未在本仓库验证**。

## 5. 夹爪：`GripperCommand` 契约

仓库今天**没有使用** `GripperCommand`；夹爪走的是 `arm.move` + `<prefix>gripper.pos`（见 `GatewayRobotAdapter.gripper_waypoints`）。若改由 `GripperCommand` 驱动，契约如下：

| 项目 | 值 |
| --- | --- |
| action 类型 | `control_msgs.action.GripperCommand` |
| 接口名 | 按控制器命名，例如 `/<component>_gripper_controller/gripper_cmd` |
| goal 字段 | `command.position`（米，目标开口）、`command.max_effort`（牛顿，力上限） |
| result 字段 | `position`、`reached_goal`、`stalled` |
| 现有工具对应 | `grasp`（闭合）、`release`（张开到 `max_width_m`）、`set_gripper_width`（精确宽度） |

映射要求：

1. **保留 `gripper_waypoints` 端口。** 工具的 `_send()` 只消费 `adapter.gripper_waypoints("right_arm", width)`，所以新实现依旧返回 `({"right_arm_gripper.pos": width},)`，再由适配器把它翻译成 `GripperCommand.Goal(command=GripperCommand(position=width, max_effort=effort))`。不要改工具层去直接拼 actuator 键。
2. **`position` 就是宽度，不是规划关节值。** MoveIt 的 `joint_trajectory` 里可能包含夹爪关节；把它当作 `gripper.pos` 下发会让"宽度（米）"变成"关节位置（rad）"，语义错位。第 3 节的 `_waypoints` 因此应把夹爪关节从臂规划结果中排除。
3. **`force` 是尽力而为。** `max_effort` 来自 `grasp(force=...)` 的 0–1 比例，驱动器不回报实测夹持力；不要用 `reached_goal` 反推"夹住了"。
4. **`object_detected` 必须来自新鲜观测。** `grasp` 今天的实现已经如此：先下发闭合，再用 `adapter.observe(streams=("robot_state", "entities"))` 读一次观测，从 `robot_state.held` / `grippers` 判断是否夹到物体。接入 `GripperCommand` 后这一点**不得退化**为读 action result：`GripperCommand` 的 `stalled`/`reached_goal` 只说明夹爪动到了哪里，不说明指间有东西。观测不新鲜时返回 `UNREACHABLE`，而不是猜一个 `object_detected=True`。
5. **宽度范围。** `safety._validate_action_value` 要求 `gripper.pos` 在 0–100；工具层已把宽度限制在 `max_width_m`（默认 0.10 m）内。走 `GripperCommand` 时仍要在适配器侧再校验一次 `0.0 <= position <= max_width_m`。

## 6. 验收清单

前置：**先不改代码**，用现有测试固定"接线前"的基线行为。

```bash
# 1) 接线前：笛卡尔请求必须失败关闭
.venv/bin/pytest -q tests/tool_layer/test_gateway_adapter.py -k "cartesian"
.venv/bin/pytest -q tests/tool_layer/test_tools.py -k "move_arm_to_pose or move_arm_relative"

# 2) 接线前：关节直通与夹具键形状仍然成立
.venv/bin/pytest -q tests/tool_layer/test_gateway_adapter.py \
  -k "joint_arm_requests or left_component or gripper_waypoints"

# 3) 工具层全量（含抓取必须从观测判断 object_detected）
.venv/bin/pytest -q tests/tool_layer
```

| # | 检查 | 期望结果 |
| --- | --- | --- |
| 1 | 接线前 `move_arm_to_pose(x, y, z)` | `plan.ok is False` 且 `plan.code == "IK_UNAVAILABLE"`；工具返回 `success=false` 且 `recoverable=false`。 |
| 2 | 接线前 `move_arm_relative(dx, dy, dz)` | 同上；且不给后端派发任何命令（`arm.move` 未出现在 `failure_data`/后端记录里）。 |
| 3 | 接线后 `move_arm_to_pose` 可达目标 | `plan.ok is True`、`waypoints` 非空、每个键匹配 `^(left_arm_\|right_arm_\|head_).*\.pos$`。 |
| 4 | 每个 waypoint 的关节值 | 全部落在 `RobotProfile.joints[i].lower/upper` 内；越界必须 `ok=False`，**不截断**。 |
| 5 | 目标碰撞 | `code == "COLLISION_RISK"`，工具 `error_code == COLLISION_RISK`，不派发 `arm.move`。 |
| 6 | 目标不可达（桌面外、超出工作空间） | `code == "TARGET_UNREACHABLE"`，工具 `error_code == UNREACHABLE`，`recoverable=false`。 |
| 7 | 规划超时 | `code == "TIMEOUT"`，且监控里没有 `arm.move` 命令。 |
| 8 | 被拒绝的计划 | 后端命令列表为空（`arm.move` 零次），日志里没有成功文案。 |
| 9 | 抓取 | `grasp` 的 `object_detected` 带 `observation_id`，且该观测采集时间晚于闭合命令。 |
| 10 | 现有测试不回归 | `make test`、`.venv/bin/pytest -q tests/tool_layer`、`.venv/bin/pytest -q tests/contract` 全绿。 |

ROS 2 侧（本仓库的 xlerobot/导航测试由 pytest 收集，不依赖 MoveIt 2）：

```bash
.venv/bin/pytest -q robot/ros2_ws/src/xlerobot_adapter/test
.venv/bin/pytest -q robot/ros2_ws/src/tangying_navigation/test

# 有 ROS 2 环境时：构建与既有 ROS 测试必须仍然通过
cd robot/ros2_ws && . /opt/ros/jazzy/setup.sh && colcon build --event-handlers console_direct+
cd robot/ros2_ws && . /opt/ros/jazzy/setup.sh && . install/setup.sh \
  && colcon test --packages-select tangying_navigation tangying_dwb_critics \
     --return-code-on-test-failure --event-handlers console_direct+ \
  && colcon test-result --verbose
```

合入前按仓库惯例跑 `make test` 与 `make lint`，并记录当前提交、环境、失败与跳过原因。第 1–9 项中每一项都必须在**至少一个真实 `plan_arm_motion` 实现**上留下输出记录；未跑过的项写"未验证"，不要写"通过"。

## 7. 仍未验证的部分

即使第 6 节全绿，下面这些依然**未验证**：

- **无硬件验证。** 没有任何实机执行记录：关节方向、零位、`max_relative_target`、真实可达空间、急停与租约失效下的实际停机都不在本文覆盖范围内。
- **无碰撞场景验证。** 规划场景（`PlanningScene`）里没有真实障碍物、桌面、夹具或点云；`COLLISION_RISK` 只反映被送入 MoveIt 的那个场景，不反映现场。
- **无周期时间测量。** 没有规划耗时、动作 chunk 执行时间或端到端延迟数据；`PLAN_TIMEOUT_S` 与工具层 15 s 预算的余量是设计值，不是实测值。
- **无 TF 与坐标验证。** `frame_id` 到 `base_link` 的变换、末端链路名、手眼标定都未验证；`frame_id != "base_link"` 在当前草图中直接返回 `IK_UNAVAILABLE`。
- **无 `GripperCommand` 实测。** 控制器接口名、`max_effort` 单位、`position` 与物理开口的对应关系未在本仓库验证。
- **无 `both_arms` 双臂验证。** 两侧前缀与并发/互斥语义未测试。
- **无 CI 覆盖。** 在把 MoveIt 2 包加进 `ros-build` 之前，CI 不会构建或测试任何 MoveIt 2 路径。

在这些项目有现场证据之前，`move_arm_to_pose` / `move_arm_relative` 保持 `IK_UNAVAILABLE` 失败关闭是正确状态。
