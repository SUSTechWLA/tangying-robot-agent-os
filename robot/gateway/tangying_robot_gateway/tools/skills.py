"""Composite skills: named shortcuts over the atomic tools.

A composite tool is not a new capability. It calls the same atomic tools an
LLM would call, in order, and reports which sub-step failed so the caller can
retry one step instead of re-running the whole chain. Keeping the chain here
means the ordering is versioned and testable, while the atomic tools stay
individually callable for recovery.

Nothing in this module commands the robot directly: it only invokes other
``RobotTool`` handlers through the registry, so every physical effect still
passes the same validation, safety and closure machinery.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..tool_layer import RobotTool, SafetyLevel, ToolError, ToolResult

SKILL_NAMESPACE = "robot.skill"


@dataclass
class _Trace:
    """Ordered record of the sub-steps one composite call performed."""

    steps: list[dict[str, Any]] = field(default_factory=list)

    def record(self, name: str, arguments: dict[str, Any], result: ToolResult) -> None:
        self.steps.append({
            "tool": name,
            "arguments": arguments,
            "success": result.success,
            "error_code": result.error_code,
            "recoverable": result.recoverable,
            "tool_call_id": result.tool_call_id,
        })

    def failed_step(self) -> str | None:
        for step in self.steps:
            if not step["success"]:
                return str(step["tool"])
        return None

    def payload(self) -> dict[str, Any]:
        return {"steps": list(self.steps), "step_count": len(self.steps)}


def build_skill_tools(registry) -> list[RobotTool]:
    """Construct composite skills from an already-built registry.

    ``registry`` is the :class:`~tangying_robot_gateway.tool_layer.ToolRegistry`
    holding the atomic tools, so a composite tool cannot reach a capability the
    caller could not reach itself.
    """

    def _call(trace: _Trace, name: str, **arguments: Any) -> ToolResult:
        from ..tool_invocation import current_invocation

        invocation = current_invocation.get()
        if invocation and invocation.cancel_event and invocation.cancel_event.is_set():
            return ToolResult.failure(ToolError.CANCELLED, "composite was cancelled; verify the current physical state", recoverable=False)
        tool = registry.get(name)
        if tool is None:
            missing = ToolResult.failure(ToolError.NOT_FOUND, f"required tool {name!r} is not registered")
            trace.record(name, arguments, missing)
            return missing
        result = tool.execute(**arguments)
        trace.record(name, arguments, result)
        return result

    def _abort(trace: _Trace, message: str, **extra: Any) -> ToolResult:
        payload = trace.payload()
        payload.update(extra)
        return ToolResult(
            success=False, error_code=ToolError.HARDWARE_ERROR, error_message=message,
            recoverable=True, data={**payload, "failed_step": trace.failed_step()},
        )

    def pick_object(
        object_name: str, location: str | None = None, arm_component: str = "right_arm",
    ) -> ToolResult:
        """navigate (optional) -> detect -> hover -> grasp -> confirm."""

        trace = _Trace()
        target = str(object_name or "").strip()
        if not target:
            return ToolResult.failure(ToolError.INVALID_PARAM, "object_name is required")

        if location:
            moved = _call(trace, "navigate_to", location_name=location)
            if not moved.success:
                return _failure(trace, moved, "navigation to the object's place failed")

        detected = _call(trace, "detect_object", object_name=target)
        if not detected.success:
            return _failure(trace, detected, "object detection failed")
        if not detected.data.get("found"):
            return ToolResult(
                success=False, error_code=ToolError.NOT_FOUND,
                error_message=f"{target!r} is not visible from the current position",
                recoverable=True,
                data={**trace.payload(), "failed_step": "detect_object", "object_name": target},
            )

        approach = _call(trace, "move_arm_to_pose", **_hover_pose(detected.data, target))
        if not approach.success:
            return _failure(trace, approach, "arm could not reach the object")

        preopen = _call(trace, "set_gripper_width", width=0.09)
        if not preopen.success:
            return _failure(trace, preopen, "could not open the gripper")

        closed = _call(trace, "grasp", force=0.5)
        if not closed.success:
            return _failure(trace, closed, "grasp command failed")
        if not closed.data.get("object_detected"):
            # The gripper closed on nothing: report it as a missing object so
            # the caller re-detects instead of carrying air to the destination.
            return ToolResult(
                success=False, error_code=ToolError.NOT_FOUND,
                error_message=f"gripper closed without detecting {target!r}",
                recoverable=True,
                data={**trace.payload(), "failed_step": "grasp", "object_name": target},
            )

        lifted = _call(trace, "move_arm_relative", dx=0.0, dy=0.0, dz=0.05)
        if not lifted.success:
            # The object is held; failing here is recoverable by retrying the
            # lift, and the trace says exactly which step stopped.
            return _failure(trace, lifted, "object grasped but the lift motion failed")

        payload = trace.payload()
        return ToolResult.ok(
            object_name=target, location=location, held_object=closed.data.get("held_object", ""),
            component=arm_component, **payload,
        )

    def place_object(location: str, release_height_m: float = 0.02) -> ToolResult:
        """navigate -> lower -> release -> confirm the gripper is empty."""

        trace = _Trace()
        place = str(location or "").strip()
        if not place:
            return ToolResult.failure(ToolError.INVALID_PARAM, "location is required")
        if not 0.0 < release_height_m <= 0.20:
            return ToolResult.failure(
                ToolError.INVALID_PARAM, "release_height_m must be between 0 and 0.20 m",
            )

        moved = _call(trace, "navigate_to", location_name=place)
        if not moved.success:
            return _failure(trace, moved, "navigation to the destination failed")

        lowered = _call(trace, "move_arm_relative", dx=0.0, dy=0.0, dz=-release_height_m)
        if not lowered.success:
            return _failure(trace, lowered, "could not lower the arm to the release height")

        released = _call(trace, "release")
        if not released.success:
            return _failure(trace, released, "release failed")

        state = _call(trace, "get_gripper_state")
        if not state.success:
            return _failure(trace, state, "could not confirm the gripper released the object")
        if state.data.get("is_holding"):
            return ToolResult(
                success=False, error_code=ToolError.HARDWARE_ERROR,
                error_message="the gripper still reports a held object after release",
                recoverable=True,
                data={**trace.payload(), "failed_step": "release"},
            )

        return ToolResult.ok(location=place, released=True, **trace.payload())

    def fetch_object(object_name: str, target_location: str) -> ToolResult:
        """pick at the current place, then carry it to ``target_location``."""

        trace = _Trace()
        picked = pick_object(object_name=object_name)
        trace.record("pick_object", {"object_name": object_name}, picked)
        if not picked.success:
            return _failure(trace, picked, "pick failed, so nothing was carried")

        placed = place_object(location=target_location)
        trace.record("place_object", {"location": target_location}, placed)
        if not placed.success:
            return _failure(
                trace, placed,
                f"{object_name!r} is still held; carry it to a safe place before retrying",
            )

        return ToolResult.ok(
            object_name=object_name, target_location=target_location,
            delivered=True, **trace.payload(),
        )

    def navigate_to_work_area(location_name: str, candidate_index: int = 0) -> ToolResult:
        """Plan a work area, then drive to one of its reachable poses.

        The commissioned room waypoint is a single point, and a single point can
        stop being reachable: a new survey can leave it unplannable, or the
        driver's own clearance envelope can refuse it while the map-based router
        still calls it free. Planning first turns "go to the kitchen" into "go to
        one of these reachable poses", and the pose that was commanded is reported
        so the caller verifies arrival against the motion it actually asked for
        rather than against a point the robot never went to.
        """

        trace = _Trace()
        planned = _call(trace, "plan_work_area", location_name=location_name)
        if not planned.success:
            return _failure(trace, planned,
                            "no reachable base pose could be planned for this work area")
        candidates = list((planned.data or {}).get("candidates") or [])
        if not candidates:
            return _failure(trace, planned, "the work area has no reachable candidate pose")
        if (isinstance(candidate_index, bool) or not isinstance(candidate_index, int)
                or not 0 <= candidate_index < len(candidates)):
            return ToolResult.failure(
                ToolError.INVALID_PARAM,
                f"candidate_index must be an integer within 0..{len(candidates) - 1}",
                candidateCount=len(candidates), **trace.payload())
        chosen = candidates[candidate_index]
        pose = list(chosen.get("basePose") or [])
        if len(pose) != 7:
            return _failure(trace, planned, "the planned candidate has no usable pose")

        driven = _call(trace, "navigate_to_pose", x=pose[0], y=pose[1],
                       theta=math.atan2(pose[6], pose[3]))
        if not driven.success:
            return _failure(trace, driven, "the planned pose could not be reached")
        return ToolResult.ok(
            final_pose=pose, target_pose=pose, frame_id="map",
            workspace=(planned.data or {}).get("workspace", location_name),
            candidate_index=candidate_index, candidateCount=len(candidates),
            reach_meters=chosen.get("reachMeters"),
            kinematics_verified=bool(chosen.get("kinematicsVerified")),
            requires_kinematics_validation=bool((planned.data or {}).get("requiresKinematicsValidation")),
            plan_revision=(planned.data or {}).get("mapRevision"),
            arrival_verified=False,
            note="候选位姿来自地图工作区规划；到达仍需一次新的到达核验。",
            **trace.payload(),
        )

    work_area_tool = RobotTool(
        name="navigate_to_work_area",
        description=(
            "前往语义工作区的**可达候选位姿**（而不是唯一的登记点），并返回实际下达的目标位姿；"
            "到达必须用返回的 final_pose 再核验一次。适合登记点被判不可达、或停车点余量太小时使用。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "location_name": {"type": "string"},
                "candidate_index": {"type": "integer", "minimum": 0, "maximum": 7},
            },
            "required": ["location_name"],
        },
        returns_schema={
            "type": "object",
            "properties": {"final_pose": {"type": "array"}, "candidateCount": {"type": "integer"}},
        },
        safety_level=SafetyLevel.NORMAL_MOTION,
        timeout_s=120.0,
        distributed_node=SKILL_NAMESPACE,
        idempotent=False,
        mutates_world=True,
        handler=navigate_to_work_area,
    )

    return [
        work_area_tool,
        RobotTool(
            name="pick_object",
            description=(
                "抓取指定物体的快捷方式：可选先导航到 location，然后检测、靠近、闭合夹爪并抬起。"
                "失败时 data.failed_step 指出是哪一步失败；返回 NOT_FOUND 表示没看到或没夹到，"
                "应先重新检测或换位置，不要盲目重复抓取。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "object_name": {"type": "string", "description": "物体名称，例如 红色杯子"},
                    "location": {"type": "string", "description": "可选的语义位置，先导航到那里"},
                    "arm_component": {"type": "string", "default": "right_arm"},
                },
                "required": ["object_name"],
            },
            returns_schema={
                "type": "object",
                "properties": {
                    "object_name": {"type": "string"}, "steps": {"type": "array"},
                    "failed_step": {"type": "string"},
                },
            },
            safety_level=SafetyLevel.CONTACT,
            timeout_s=120.0,
            distributed_node=SKILL_NAMESPACE,
            idempotent=False,
            mutates_world=True,
            handler=pick_object,
        ),
        RobotTool(
            name="place_object",
            description=(
                "把手中物体放到指定语义位置：导航、下放、释放并确认夹爪已空。"
                "失败时 data.failed_step 指出失败步骤；物体可能仍被夹持，请据此决定重试或人工处理。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "目标语义位置"},
                    "release_height_m": {"type": "number", "default": 0.02},
                },
                "required": ["location"],
            },
            returns_schema={
                "type": "object",
                "properties": {"location": {"type": "string"}, "released": {"type": "boolean"}},
            },
            safety_level=SafetyLevel.CONTACT,
            timeout_s=120.0,
            distributed_node=SKILL_NAMESPACE,
            idempotent=False,
            mutates_world=True,
            handler=place_object,
        ),
        RobotTool(
            name="fetch_object",
            description=(
                "取物并送达：pick_object 然后 place_object。适合“把桌上的杯子拿到厨房”这类指令。"
                "中途失败时物体可能仍在夹爪中，先确认状态再决定下一步。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "object_name": {"type": "string"},
                    "target_location": {"type": "string", "description": "送达的语义位置"},
                },
                "required": ["object_name", "target_location"],
            },
            returns_schema={
                "type": "object",
                "properties": {"delivered": {"type": "boolean"}, "steps": {"type": "array"}},
            },
            safety_level=SafetyLevel.CONTACT,
            timeout_s=240.0,
            distributed_node=SKILL_NAMESPACE,
            idempotent=False,
            mutates_world=True,
            handler=fetch_object,
        ),
    ]


def _hover_pose(detection: dict[str, Any], object_name: str) -> dict[str, Any]:
    """A grasp pose above the detected object.

    The offset is deliberately expressed in the tool result so a failed grasp
    can be retried from a different height without re-detecting.
    """

    pose = detection.get("pose") or []
    x = float(pose[0]) if len(pose) > 0 else 0.30
    y = float(pose[1]) if len(pose) > 1 else 0.0
    z = float(pose[2]) if len(pose) > 2 else 0.10
    return {"x": x, "y": y, "z": z + 0.10, "roll": 0.0, "pitch": 1.2, "yaw": 0.0,
            "frame_id": "base_link"}


def _failure(trace: _Trace, result: ToolResult, message: str) -> ToolResult:
    """Carry the sub-step failure forward, keeping its code and recovery hint."""

    payload = trace.payload()
    payload.update(result.data)
    payload["failed_step"] = trace.failed_step()
    return ToolResult(
        success=False, error_code=result.error_code, error_message=f"{message}: {result.error_message}",
        recoverable=result.recoverable, data=payload, timestamp=result.timestamp,
        tool_call_id=result.tool_call_id,
    )
