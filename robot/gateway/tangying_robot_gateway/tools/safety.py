"""Safety tools.

``emergency_stop`` and ``reset_safety_stop`` are the only tools that may
interrupt normal work. They are dispatched out of band — not through the skill
queue — because their entire purpose is to act while another command holds the
runtime's execution lock.

This module does not implement safety. The latch, the stop generation and the
local-only reset already live in ``safety.SafetySupervisor`` and
``RuntimeJournal``. What is added here is a tool-shaped, LLM-callable way to
reach them, with the access control the manual path already assumes (the
runtime's reset requires an operator, so the tool does too).
"""

from __future__ import annotations

from typing import Any

from ..tool_layer import RobotTool, SafetyLevel, ToolError, ToolResult
from .registry import OperationContext, RobotAdapter, require_finite, require_text

NAMESPACE = "robot.safety"

#: Components whose speed may be limited, and the physical ceiling for each.
#: ``component`` is validated against this map instead of accepting free text,
#: so a typo cannot silently leave a limit unapplied.
COMPONENT_LIMITS: dict[str, tuple[float, float]] = {
    "base": (0.0, 0.05),
    "left_arm": (0.0, 1.0),
    "right_arm": (0.0, 1.0),
    "head": (0.0, 1.0),
}


def build_safety_tools(
    adapter: RobotAdapter,
    *,
    context: OperationContext | None = None,
    reset_requires_permission: bool = True,
) -> list[RobotTool]:
    """Construct the safety capability group."""

    def emergency_stop(reason: str = "") -> ToolResult:
        """Latch the stop as fast as possible.

        This deliberately does not reuse the tool's own timeout: it calls the
        adapter's out-of-band stop and reports the latch state the runtime
        acknowledges. A stop that is merely *sent* is reported as unconfirmed,
        never as stopped.
        """

        detail = require_text(reason, "reason") if reason else "emergency_stop tool"
        result = adapter.execute("emergency_stop", parameters={"reason": detail}, timeout_s=5.0)
        latched = bool(result.payload.get("latched", result.success))
        if not latched:
            return ToolResult.failure(
                ToolError.HARDWARE_ERROR,
                f"emergency stop was not confirmed by the runtime: {result.message or result.code}",
                runtime_code=result.code, stop_confirmed=False,
            )
        return ToolResult.ok(stop_confirmed=True, latched=True, reason=detail)

    def reset_safety_stop(operator: str = "", reason: str = "") -> ToolResult:
        if reset_requires_permission and not (operator.strip() and reason.strip()):
            # Reset is an operator action with physical consequences; without an
            # identified operator and a reason the runtime would refuse it too.
            return ToolResult.failure(
                ToolError.PERMISSION_DENIED,
                "resetting a safety stop requires both operator and reason",
                required_arguments=["operator", "reason"],
            )
        result = adapter.execute(
            "reset_safety_stop", parameters={"operator": operator, "reason": reason}, timeout_s=10.0,
        )
        if not result.success:
            return ToolResult.from_runtime(result)
        return ToolResult.ok(reset=bool(result.payload.get("reset", True)))

    def set_speed_limit(component: str, limit: float) -> ToolResult:
        name = require_text(component, "component")
        if name not in COMPONENT_LIMITS:
            return ToolResult.failure(
                ToolError.INVALID_PARAM, f"unknown component {name!r}",
                known_components=sorted(COMPONENT_LIMITS),
            )
        lower, upper = COMPONENT_LIMITS[name]
        value = require_finite(limit, "limit")
        if not lower <= value <= upper:
            return ToolResult.failure(
                ToolError.INVALID_PARAM,
                f"{name} speed limit must be between {lower} and {upper}",
                component=name, allowed_range=[lower, upper],
            )
        result = adapter.set_speed_limit(name, value)
        if not result.success:
            return ToolResult.from_runtime(result, component=name, limit=value)
        return ToolResult.ok(component=name, limit=value, applied=True)

    def check_collision(target_pose: dict[str, Any], component: str = "right_arm") -> ToolResult:
        name = require_text(component, "component")
        if name not in COMPONENT_LIMITS:
            return ToolResult.failure(
                ToolError.INVALID_PARAM, f"unknown component {name!r}",
                known_components=sorted(COMPONENT_LIMITS),
            )
        if not isinstance(target_pose, dict):
            return ToolResult.failure(ToolError.INVALID_PARAM, "target_pose must be an object")
        try:
            plan = adapter.plan_arm_motion(
                component=name,
                target_pose=(
                    require_finite(target_pose.get("x"), "target_pose.x"),
                    require_finite(target_pose.get("y"), "target_pose.y"),
                    require_finite(target_pose.get("z"), "target_pose.z"),
                    require_finite(target_pose.get("roll"), "target_pose.roll", default=0.0),
                    require_finite(target_pose.get("pitch"), "target_pose.pitch", default=0.0),
                    require_finite(target_pose.get("yaw"), "target_pose.yaw", default=0.0),
                ),
                frame_id=str(target_pose.get("frame_id") or "base_link"),
            )
        except ValueError as exc:
            return ToolResult.failure(ToolError.INVALID_PARAM, str(exc))
        if plan.ok:
            return ToolResult.ok(collision=False, safe_to_move=True, component=name,
                                 target_pose=target_pose)
        # A planner that refuses the target reports why; that is a collision or
        # workspace verdict, not a hardware fault.
        code = ToolError.COLLISION_RISK if plan.code == "COLLISION_RISK" else ToolError.UNREACHABLE
        return ToolResult(
            success=False, error_code=code,
            error_message=plan.message or f"{name} cannot reach the target pose",
            recoverable=True,
            data={"collision": code is ToolError.COLLISION_RISK, "safe_to_move": False,
                  "component": name, "target_pose": target_pose, "planner_code": plan.code},
        )

    return [
        RobotTool(
            name="emergency_stop",
            description=(
                "立即停止所有运动并锁存安全停止。可在任何时刻调用，会绕过普通命令队列。"
                "回执 stop_confirmed=false 表示运行时未确认锁存，请立即使用实体急停。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {"reason": {"type": "string", "description": "触发原因，便于审计"}},
            },
            returns_schema={
                "type": "object",
                "properties": {"stop_confirmed": {"type": "boolean"}, "latched": {"type": "boolean"}},
            },
            safety_level=SafetyLevel.SAFETY,
            timeout_s=5.0,
            distributed_node=NAMESPACE,
            idempotent=True,
            handler=emergency_stop,
        ),
        RobotTool(
            name="reset_safety_stop",
            description=(
                "复位安全停止。需要 operator 与 reason，且必须由现场人员确认环境安全后再调用；"
                "缺少任一参数将返回 PERMISSION_DENIED。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "operator": {"type": "string", "description": "执行复位的操作员标识"},
                    "reason": {"type": "string", "description": "复位理由"},
                },
                "required": ["operator", "reason"],
            },
            returns_schema={"type": "object", "properties": {"reset": {"type": "boolean"}}},
            safety_level=SafetyLevel.SAFETY,
            timeout_s=10.0,
            distributed_node=NAMESPACE,
            idempotent=False,
            handler=reset_safety_stop,
        ),
        RobotTool(
            name="set_speed_limit",
            description=(
                f"限制指定组件速度，可选组件：{', '.join(sorted(COMPONENT_LIMITS))}。"
                "超出物理上限会返回 INVALID_PARAM；这是减速，不是急停。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "component": {"type": "string", "enum": sorted(COMPONENT_LIMITS)},
                    "limit": {"type": "number", "description": "速度上限（底盘 m/s，关节比例）"},
                },
                "required": ["component", "limit"],
            },
            returns_schema={
                "type": "object",
                "properties": {"component": {"type": "string"}, "limit": {"type": "number"}},
            },
            safety_level=SafetyLevel.SAFETY,
            timeout_s=5.0,
            distributed_node=NAMESPACE,
            idempotent=True,
            handler=set_speed_limit,
        ),
        RobotTool(
            name="check_collision",
            description=(
                "在运动前预检查目标位姿是否碰撞或不可达。返回 COLLISION_RISK 表示会碰撞，"
                "应改目标而不是重试；返回 UNREACHABLE 表示超出工作空间。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "target_pose": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"},
                            "roll": {"type": "number"}, "pitch": {"type": "number"},
                            "yaw": {"type": "number"}, "frame_id": {"type": "string"},
                        },
                        "required": ["x", "y", "z"],
                    },
                    "component": {"type": "string", "enum": sorted(COMPONENT_LIMITS)},
                },
                "required": ["target_pose"],
            },
            returns_schema={
                "type": "object",
                "properties": {"collision": {"type": "boolean"}, "safe_to_move": {"type": "boolean"}},
            },
            safety_level=SafetyLevel.SAFETY,
            timeout_s=10.0,
            distributed_node=NAMESPACE,
            idempotent=True,
            handler=check_collision,
        ),
    ]
