"""Arm and gripper tools.

These expose a *capability*: "move the end effector to this pose", "close the
gripper". They never expose a joint vector to the LLM as a primary tool, and
they never build actuator keys themselves — that is the adapter's job, because
the accepted key names are embodiment specific.

Every motion here is dispatched as the existing ``arm.move`` skill, so the
safety supervisor's lease, approval and range checks apply unchanged, and the
Agent's closure gate still demands fresh post-command evidence.
"""

from __future__ import annotations

import math
from typing import Any

from ..tool_layer import RobotTool, SafetyLevel, ToolError, ToolResult
from .registry import (
    OperationContext,
    RobotAdapter,
    require_finite,
    require_fraction,
    require_text,
)

ARM_NAMESPACE = "robot.arm"
GRIPPER_NAMESPACE = "robot.gripper"
ARM_SKILL = "arm.move"

#: Component names the adapter understands. Kept explicit so an unknown value
#: is a clear INVALID_PARAM rather than a silent default.
ARM_COMPONENTS = ("left_arm", "right_arm", "both_arms")
_GRIPPER_KEY_SUFFIX = "gripper.pos"


def _component(value: Any, *, default: str) -> str:
    name = require_text(value, "component") if value is not None else default
    if name not in ARM_COMPONENTS:
        raise ValueError(f"component must be one of {', '.join(ARM_COMPONENTS)}")
    return name


def build_arm_tools(
    adapter: RobotAdapter,
    *,
    context: OperationContext | None = None,
) -> list[RobotTool]:
    """Construct the arm capability group."""

    def dispatch(plan, *, timeout_s: float, failure_data: dict[str, Any]) -> ToolResult:
        if not plan.ok:
            return ToolResult.failure(
                _standard_code(plan.code), plan.message or plan.code, **failure_data,
            )
        if not plan.waypoints:
            return ToolResult.failure(
                ToolError.INVALID_PARAM, "arm plan produced no waypoints", **failure_data,
            )
        operation = (context or OperationContext()).for_operation(ARM_SKILL, timeout_s=timeout_s)
        result = adapter.execute(
            ARM_SKILL,
            parameters={"action_chunk": [dict(step) for step in plan.waypoints]},
            context=operation, timeout_s=timeout_s,
        )
        if not result.success:
            return ToolResult.from_runtime(result, **failure_data)
        payload = dict(failure_data)
        payload["final_pose"] = list(plan.final_pose)
        payload["joint_angles"] = list(plan.joints)
        if plan.warnings:
            payload["warnings"] = list(plan.warnings)
        return ToolResult.ok(**payload)

    def move_arm_to_pose(
        x: float, y: float, z: float, roll: float = 0.0, pitch: float = 0.0, yaw: float = 0.0,
        frame_id: str = "base_link", velocity_scaling: float = 0.5, component: str = "right_arm",
    ) -> ToolResult:
        target = (
            require_finite(x, "x"), require_finite(y, "y"), require_finite(z, "z"),
            require_finite(roll, "roll", default=0.0), require_finite(pitch, "pitch", default=0.0),
            require_finite(yaw, "yaw", default=0.0),
        )
        arm = _component(component, default="right_arm")
        scaling = require_fraction(velocity_scaling, "velocity_scaling", default=0.5)
        plan = adapter.plan_arm_motion(
            component=arm, target_pose=target, frame_id=frame_id, velocity_scaling=scaling,
        )
        return dispatch(plan, timeout_s=15.0, failure_data={
            "component": arm, "target_pose": list(target), "frame_id": frame_id,
        })

    def move_arm_to_joints(
        joint_angles: list[float], velocity_scaling: float = 0.5, component: str = "right_arm",
    ) -> ToolResult:
        if not isinstance(joint_angles, (list, tuple)) or not joint_angles:
            return ToolResult.failure(ToolError.INVALID_PARAM, "joint_angles must be a non-empty list")
        joints = tuple(require_finite(value, f"joint_angles[{index}]")
                       for index, value in enumerate(joint_angles))
        arm = _component(component, default="right_arm")
        scaling = require_fraction(velocity_scaling, "velocity_scaling", default=0.5)
        plan = adapter.plan_arm_motion(component=arm, joints=joints, velocity_scaling=scaling)
        return dispatch(plan, timeout_s=15.0, failure_data={"component": arm})

    def move_arm_relative(
        dx: float, dy: float, dz: float, frame_id: str = "end_effector",
        velocity_scaling: float = 0.5, component: str = "right_arm",
    ) -> ToolResult:
        offset = (
            require_finite(dx, "dx"), require_finite(dy, "dy"), require_finite(dz, "dz"),
        )
        magnitude = math.sqrt(sum(value * value for value in offset))
        if magnitude > 0.30:
            # A relative nudge is a fine adjustment, not a transport move. A
            # large offset almost always means the caller meant an absolute
            # pose, and silently clipping it would move somewhere unintended.
            return ToolResult.failure(
                ToolError.INVALID_PARAM,
                f"relative offset {magnitude:.3f} m exceeds the 0.30 m fine-adjustment limit",
                requested_offset_m=magnitude, limit_m=0.30,
            )
        arm = _component(component, default="right_arm")
        scaling = require_fraction(velocity_scaling, "velocity_scaling", default=0.5)
        plan = adapter.plan_arm_motion(
            component=arm, relative=offset, frame_id=frame_id, velocity_scaling=scaling,
        )
        return dispatch(plan, timeout_s=15.0, failure_data={
            "component": arm, "offset": list(offset), "frame_id": frame_id,
        })

    def home_arm(component: str = "both_arms") -> ToolResult:
        arm = _component(component, default="both_arms")
        operation = (context or OperationContext()).for_operation(
            "recover_to_safe_pose", timeout_s=15.0,
        )
        result = adapter.execute("recover_to_safe_pose", parameters={}, context=operation, timeout_s=15.0)
        if not result.success:
            return ToolResult.from_runtime(result, component=arm)
        view = adapter.observe(
            streams=("robot_state",),
            context=(context or OperationContext()).for_operation(
                "observe_scene", timeout_s=5.0, suffix="/home_arm",
            ),
        )
        joints = dict(view.robot_state).get("joint_positions")
        return ToolResult.ok(component=arm, joint_angles=joints or [], homed=True)

    def get_arm_state(component: str = "both_arms") -> ToolResult:
        arm = _component(component, default="both_arms")
        view = adapter.observe(
            streams=("robot_state",),
            context=(context or OperationContext()).for_operation("observe_scene", timeout_s=5.0),
        )
        if not view.observation_id or not view.fresh:
            return ToolResult.failure(ToolError.UNREACHABLE, "no fresh robot-state observation available")
        state = dict(view.robot_state)
        return ToolResult.ok(
            component=arm,
            joint_angles=state.get("joint_positions") or {},
            ee_pose=state.get("end_effectors") or {},
            grippers=state.get("grippers") or {},
            active_tool=state.get("active_tool") or "",
            observation_id=view.observation_id,
        )

    return [
        RobotTool(
            name="move_arm_to_pose",
            description=(
                "把机械臂末端移动到指定笛卡尔位姿，例如“物体上方 10 厘米”。"
                "frame_id 默认 base_link；优先用绝对位姿，微调用 move_arm_relative。"
                "返回 UNREACHABLE 表示目标超出工作空间或不可达，请改目标位姿而不是重试同一坐标。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "x": {"type": "number", "description": "目标 X（米）"},
                    "y": {"type": "number", "description": "目标 Y（米）"},
                    "z": {"type": "number", "description": "目标 Z（米）"},
                    "roll": {"type": "number", "default": 0.0},
                    "pitch": {"type": "number", "default": 0.0},
                    "yaw": {"type": "number", "default": 0.0},
                    "frame_id": {"type": "string", "default": "base_link"},
                    "velocity_scaling": {"type": "number", "default": 0.5, "description": "0-1 速度比例"},
                    "component": {"type": "string", "default": "right_arm",
                                  "enum": list(ARM_COMPONENTS)},
                },
                "required": ["x", "y", "z"],
            },
            returns_schema={
                "type": "object",
                "properties": {
                    "final_pose": {"type": "array"}, "joint_angles": {"type": "array"},
                },
            },
            safety_level=SafetyLevel.NORMAL_MOTION,
            timeout_s=15.0,
            distributed_node=ARM_NAMESPACE,
            idempotent=False,
            mutates_world=True,
            handler=move_arm_to_pose,
        ),
        RobotTool(
            name="move_arm_to_joints",
            description=(
                "按关节角移动机械臂，仅在已知该型号关节顺序时使用；LLM 一般应使用 move_arm_to_pose。"
                "参数错误会返回 INVALID_PARAM，不会部分执行。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "joint_angles": {"type": "array", "items": {"type": "number"},
                                     "description": "按 profile 关节顺序排列的目标角度（弧度）"},
                    "velocity_scaling": {"type": "number", "default": 0.5},
                    "component": {"type": "string", "default": "right_arm", "enum": list(ARM_COMPONENTS)},
                },
                "required": ["joint_angles"],
            },
            returns_schema={"type": "object", "properties": {"final_joints": {"type": "array"}}},
            safety_level=SafetyLevel.NORMAL_MOTION,
            timeout_s=15.0,
            distributed_node=ARM_NAMESPACE,
            idempotent=False,
            mutates_world=True,
            llm_visibility="fallback",
            handler=move_arm_to_joints,
        ),
        RobotTool(
            name="move_arm_relative",
            description=(
                "相对当前位置做小幅微调，例如“再向下 2 厘米”。偏移量上限 0.30 米；"
                "需要更大位移时改用 move_arm_to_pose。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "dx": {"type": "number"}, "dy": {"type": "number"}, "dz": {"type": "number"},
                    "frame_id": {"type": "string", "default": "end_effector"},
                    "velocity_scaling": {"type": "number", "default": 0.5},
                    "component": {"type": "string", "default": "right_arm", "enum": list(ARM_COMPONENTS)},
                },
                "required": ["dx", "dy", "dz"],
            },
            returns_schema={"type": "object", "properties": {"final_pose": {"type": "array"}}},
            safety_level=SafetyLevel.LOW_SPEED_MOTION,
            timeout_s=15.0,
            distributed_node=ARM_NAMESPACE,
            idempotent=False,
            mutates_world=True,
            handler=move_arm_relative,
        ),
        RobotTool(
            name="home_arm",
            description="把机械臂收回安全位（收臂）。导航前应先收臂；失败时不会假装已收臂。",
            parameters_schema={
                "type": "object",
                "properties": {"component": {"type": "string", "default": "both_arms",
                                             "enum": list(ARM_COMPONENTS)}},
            },
            returns_schema={"type": "object", "properties": {"joint_angles": {}, "homed": {"type": "boolean"}}},
            safety_level=SafetyLevel.NORMAL_MOTION,
            timeout_s=15.0,
            distributed_node=ARM_NAMESPACE,
            idempotent=False,
            mutates_world=True,
            handler=home_arm,
        ),
        RobotTool(
            name="get_arm_state",
            description="查询机械臂关节角、末端位姿与夹爪状态，用于诊断或规划前确认。",
            parameters_schema={
                "type": "object",
                "properties": {"component": {"type": "string", "default": "both_arms",
                                             "enum": list(ARM_COMPONENTS)}},
            },
            returns_schema={
                "type": "object",
                "properties": {"joint_angles": {}, "ee_pose": {}, "grippers": {}},
            },
            safety_level=SafetyLevel.QUERY,
            timeout_s=5.0,
            distributed_node=ARM_NAMESPACE,
            idempotent=True,
            handler=get_arm_state,
        ),
    ]


def build_gripper_tools(
    adapter: RobotAdapter,
    *,
    context: OperationContext | None = None,
    max_width_m: float = 0.10,
) -> list[RobotTool]:
    """Construct the gripper capability group.

    Gripper motion is dispatched as ``arm.move`` because that is the skill the
    runtime validates for actuator writes. ``grasp`` additionally reports
    whether an object was actually detected, so the caller learns the truth
    instead of assuming a closed gripper means a held object.
    """

    def _width(value: Any, name: str, default: float | None = None) -> float:
        number = require_finite(value, name, default=default)
        if not 0.0 <= number <= max_width_m:
            raise ValueError(f"{name} must be between 0 and {max_width_m} m")
        return number

    def _send(width: float, *, suffix: str, timeout_s: float, extra: dict[str, Any]) -> ToolResult:
        waypoints = adapter.gripper_waypoints("right_arm", width)
        if not waypoints:
            return ToolResult.failure(
                ToolError.HARDWARE_ERROR, "adapter could not build a gripper command", width=width,
            )
        operation = (context or OperationContext()).for_operation(ARM_SKILL, timeout_s=timeout_s, suffix=suffix)
        result = adapter.execute(
            ARM_SKILL, parameters={"action_chunk": [dict(step) for step in waypoints]},
            context=operation, timeout_s=timeout_s,
        )
        if not result.success:
            return ToolResult.from_runtime(result, width=width)
        return ToolResult.ok(width=width, **extra)

    def grasp(force: float = 0.5, width: float | None = None, timeout_s: float = 5.0) -> ToolResult:
        effort = require_fraction(force, "force", default=0.5)
        target = _width(width, "width", default=max_width_m * 0.2)
        budget = min(max(require_finite(timeout_s, "timeout_s", default=5.0), 0.5), 30.0)
        closed = _send(target, suffix="/grasp", timeout_s=budget, extra={})
        if not closed.success:
            return closed
        # Whether something is between the fingers is a perception question, so
        # answer it from a fresh observation rather than from the close command.
        view = adapter.observe(
            streams=("robot_state", "entities"),
            context=(context or OperationContext()).for_operation(
                "verify_grasp", timeout_s=5.0, suffix="/grasp",
            ),
        )
        state = dict(view.robot_state)
        held = str(state.get("held") or "")
        grippers = state.get("grippers") or {}
        measured = grippers.get("right_arm") or grippers.get("right") or "unknown"
        object_detected = bool(held) or measured == "closed"
        return ToolResult.ok(
            object_detected=object_detected, width=target, held_object=held,
            gripper_state=measured, force=effort, observation_id=view.observation_id,
        )

    def release(timeout_s: float = 5.0) -> ToolResult:
        budget = min(max(require_finite(timeout_s, "timeout_s", default=5.0), 0.5), 30.0)
        return _send(max_width_m, suffix="/release", timeout_s=budget, extra={"released": True})

    def set_gripper_width(width: float, timeout_s: float = 5.0) -> ToolResult:
        target = _width(width, "width")
        budget = min(max(require_finite(timeout_s, "timeout_s", default=5.0), 0.5), 30.0)
        return _send(target, suffix="/set_width", timeout_s=budget, extra={})

    def get_gripper_state() -> ToolResult:
        view = adapter.observe(
            streams=("robot_state",),
            context=(context or OperationContext()).for_operation("observe_scene", timeout_s=5.0),
        )
        if not view.observation_id or not view.fresh:
            return ToolResult.failure(ToolError.UNREACHABLE, "no fresh gripper observation available")
        state = dict(view.robot_state)
        grippers = state.get("grippers") or {}
        measured = grippers.get("right_arm") or grippers.get("right") or "unknown"
        held = str(state.get("held") or "")
        return ToolResult.ok(
            width=state.get("gripper_width"), state=measured, is_holding=bool(held),
            held_object=held, observation_id=view.observation_id,
        )

    return [
        RobotTool(
            name="grasp",
            description=(
                "闭合夹爪抓取。必须检查返回的 object_detected：为 false 表示没有夹到物体，"
                "此时应重新检测或调整位姿，不要直接搬运。前置条件：末端已位于目标附近。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "force": {"type": "number", "default": 0.5, "description": "0-1 夹持力比例"},
                    "width": {"type": "number", "description": "目标开口宽度（米），省略则用默认抓取宽度"},
                    "timeout_s": {"type": "number", "default": 5.0},
                },
            },
            returns_schema={
                "type": "object",
                "properties": {
                    "object_detected": {"type": "boolean"}, "width": {"type": "number"},
                    "held_object": {"type": "string"},
                },
            },
            safety_level=SafetyLevel.CONTACT,
            timeout_s=5.0,
            distributed_node=GRIPPER_NAMESPACE,
            idempotent=False,
            mutates_world=True,
            handler=grasp,
        ),
        RobotTool(
            name="release",
            description="张开夹爪释放物体。放下物体后应确认物体已离开夹爪再移动机械臂。",
            parameters_schema={
                "type": "object",
                "properties": {"timeout_s": {"type": "number", "default": 5.0}},
            },
            returns_schema={"type": "object", "properties": {"width": {"type": "number"},
                                                             "released": {"type": "boolean"}}},
            safety_level=SafetyLevel.CONTACT,
            timeout_s=5.0,
            distributed_node=GRIPPER_NAMESPACE,
            idempotent=False,
            mutates_world=True,
            handler=release,
        ),
        RobotTool(
            name="set_gripper_width",
            description=f"精确设置夹爪开口宽度（米，0-{max_width_m}）。用于预张开以容纳目标物体。",
            parameters_schema={
                "type": "object",
                "properties": {
                    "width": {"type": "number", "description": "目标宽度（米）"},
                    "timeout_s": {"type": "number", "default": 5.0},
                },
                "required": ["width"],
            },
            returns_schema={"type": "object", "properties": {"width": {"type": "number"}}},
            safety_level=SafetyLevel.CONTACT,
            timeout_s=5.0,
            distributed_node=GRIPPER_NAMESPACE,
            idempotent=True,
            mutates_world=True,
            handler=set_gripper_width,
        ),
        RobotTool(
            name="get_gripper_state",
            description="查询夹爪开口与是否持物，用于确认抓取结果或诊断。",
            parameters_schema={"type": "object", "properties": {}},
            returns_schema={
                "type": "object",
                "properties": {"width": {"type": "number"}, "is_holding": {"type": "boolean"}},
            },
            safety_level=SafetyLevel.QUERY,
            timeout_s=5.0,
            distributed_node=GRIPPER_NAMESPACE,
            idempotent=True,
            handler=get_gripper_state,
        ),
    ]


def _standard_code(runtime_code: str) -> str:
    from ..tool_layer import standard_error

    return str(standard_error(runtime_code)[0])
