"""Navigation tools: semantic-first, coordinates as an explicit fallback.

The runtime layer already owns Nav2, map readiness, path checking and the
bounded velocity lease. These tools add the one thing it does not have: a
server-side name for "the kitchen", so the LLM never has to invent
coordinates.

Only ``navigation.navigate`` commands motion. Stopping uses the runtime's
cancel path, and pose reads use the observation path; neither is a new skill,
so the safety supervisor's allow-list is unchanged.
"""

from __future__ import annotations

import math
from typing import Any

from ..runtime import Result
from ..semantic_map import SemanticMap
from ..tool_layer import RobotTool, SafetyLevel, ToolError, ToolResult
from .registry import (
    OperationContext,
    RobotAdapter,
    require_finite,
    require_text,
)

NAMESPACE = "robot.nav"
WORKSPACE_TOOL = "navigation.navigate"


def _yaw_from_quaternion(values: Any) -> float:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return 0.0
    w, x, y, z = (float(value) for value in values)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _pose_from_state(state: dict[str, Any]) -> list[float] | None:
    """Read the base pose out of a robot-state snapshot.

    The RGB-D runtime publishes ``base_pose``; the simulator publishes an
    equivalent list. Reading it here means ``get_current_pose`` needs no new
    capability.
    """

    for key in ("base_pose", "mapPose", "map_pose"):
        value = state.get(key)
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            return [float(item) for item in value]
    navigation = state.get("navigation")
    if isinstance(navigation, dict):
        for key in ("mapPose", "current_pose"):
            value = navigation.get(key)
            if isinstance(value, (list, tuple)) and len(value) >= 3:
                return [float(item) for item in value]
    return None


def build_navigation_tools(
    adapter: RobotAdapter,
    semantic_map: SemanticMap,
    *,
    context: OperationContext | None = None,
    timeout_ceiling_s: float = 180.0,
) -> list[RobotTool]:
    """Construct the navigation capability group."""

    def bounded_timeout(value: Any, default: float) -> float:
        return min(max(require_finite(value, "timeout_s", default=default), 0.1), timeout_ceiling_s)

    def navigate(
        *,
        goal_pose: list[float],
        timeout_s: float,
        failure_context: dict[str, Any],
    ) -> Result:
        operation = (context or OperationContext()).for_operation(WORKSPACE_TOOL, timeout_s=timeout_s)
        return adapter.execute(
            WORKSPACE_TOOL, parameters={"goalPose": goal_pose}, context=operation, timeout_s=timeout_s,
        )

    def navigate_to(location_name: str, timeout_s: float = 60.0) -> ToolResult:
        name = require_text(location_name, "location_name")
        location = semantic_map.resolve(name)
        if location is None:
            return ToolResult.failure(
                ToolError.NOT_FOUND, f"unknown location {name!r}",
                known_locations=list(semantic_map.names()),
            )
        if not location.reachable:
            return ToolResult.failure(
                ToolError.UNREACHABLE, f"location {location.name!r} is not commissioned as reachable",
                location=location.name,
            )
        budget = bounded_timeout(timeout_s, 60.0)
        result = navigate(goal_pose=location.to_pose7(), timeout_s=budget,
                          failure_context={"location": location.name})
        if not result.success:
            # Name the place that failed so the model can choose another.
            return ToolResult.from_runtime(result, location=location.name, room=location.room)
        return ToolResult.ok(
            # final_pose is the goal that was commanded. Whether the base is
            # actually there is confirmed by the caller's verify_arrival step
            # from a fresh capture, so it is reported as not yet verified
            # rather than assumed equal to the goal.
            final_pose=location.to_pose7(), target_pose=location.to_pose7(),
            location=location.name, room=location.room, frame_id=location.frame_id,
            arrival_verified=False,
        )

    def navigate_to_pose(
        x: float, y: float, theta: float = 0.0, frame_id: str = "map", timeout_s: float = 60.0,
    ) -> ToolResult:
        values = [
            require_finite(x, "x"), require_finite(y, "y"),
            require_finite(theta, "theta", default=0.0),
        ]
        if frame_id != "map":
            # The stack plans in the map frame; accepting another frame here
            # would silently ignore what the caller asked for.
            return ToolResult.failure(
                ToolError.INVALID_PARAM,
                f"unsupported frame_id {frame_id!r}; navigation plans in 'map'",
                supported_frames=["map"],
            )
        half = values[2] / 2.0
        goal = [values[0], values[1], 0.0, math.cos(half), 0.0, 0.0, math.sin(half)]
        result = navigate(goal_pose=goal, timeout_s=bounded_timeout(timeout_s, 60.0), failure_context={})
        if not result.success:
            return ToolResult.from_runtime(result)
        return ToolResult.ok(final_pose=goal, target_pose=goal, frame_id=frame_id,
                             arrival_verified=False)

    def explore_for(target_desc: str, max_duration_s: float = 120.0) -> ToolResult:
        """Bounded search by driving the commissioned route waypoints.

        This is not a new planner: it navigates to each known place in turn,
        re-observing after every arrival, and stops as soon as the target is
        detected. Time and step budgets are explicit, and the last observation
        of each leg is what decides detection — never simulator ground truth.
        """

        description = require_text(target_desc, "target_desc")
        budget = bounded_timeout(max_duration_s, 120.0)
        deadline = _monotonic() + budget
        searched: list[str] = []
        for location in semantic_map.all():
            if not location.reachable:
                continue
            if _monotonic() >= deadline:
                break
            remaining = max(0.1, deadline - _monotonic())
            result = navigate(
                goal_pose=location.to_pose7(), timeout_s=min(remaining, 60.0), failure_context={},
            )
            if not result.success:
                # A place we cannot reach is information, not a fatal error:
                # keep searching the remaining places inside the budget.
                searched.append(location.name)
                continue
            searched.append(location.name)
            view = adapter.observe(context=(context or OperationContext()).for_operation(
                "observe_scene", timeout_s=remaining, suffix=f"/explore/{location.name}",
            ))
            match = _match_entity(view, description)
            if match is not None:
                return ToolResult.ok(
                    found=True, pose=list(match.get("pose") or []), entity_id=match.get("entityId") or match.get("entity_id"),
                    location=location.name, searched=searched,
                )
        return ToolResult.ok(found=False, searched=searched, budget_s=budget)

    def stop_navigation() -> ToolResult:
        command_id = (context or OperationContext()).command_id
        if not command_id:
            return ToolResult.failure(
                ToolError.INVALID_PARAM, "stop_navigation needs the command id of the navigation to cancel",
            )
        accepted = adapter.cancel(command_id, "operator requested stop_navigation")
        if not accepted:
            # Nothing was in flight: the useful answer is "already idle", not
            # an error, because the caller's intent is satisfied either way.
            return ToolResult.ok(stopped=True, cancelled=False,
                                 note="no in-flight navigation to cancel")
        # Cancellation is acknowledged, not proven: the base is shown to be
        # stopped by a fresh velocity/pose observation, not by this receipt.
        return ToolResult.ok(stopped=True, cancelled=True, stop_verified=False)

    def get_current_pose(frame_id: str = "map") -> ToolResult:
        if frame_id != "map":
            return ToolResult.failure(
                ToolError.INVALID_PARAM, f"unsupported frame_id {frame_id!r}", supported_frames=["map"],
            )
        view = adapter.observe(
            streams=("robot_state",),
            context=(context or OperationContext()).for_operation("observe_scene", timeout_s=5.0),
        )
        if not view.observation_id or not view.fresh:
            return ToolResult.failure(
                ToolError.UNREACHABLE, "no fresh pose observation available", frame_id=frame_id,
            )
        pose = _pose_from_state(dict(view.robot_state))
        if pose is None:
            return ToolResult.failure(
                ToolError.HARDWARE_ERROR, "runtime reported no base pose in its observation",
            )
        theta = pose[2] if len(pose) == 3 else _yaw_from_quaternion(pose[3:7])
        return ToolResult.ok(
            x=pose[0], y=pose[1], theta=theta, frame_id=frame_id,
            observation_id=view.observation_id, observed_at_unix_ms=view.observed_at_unix_ms,
        )

    return [
        RobotTool(
            name="navigate_to",
            description=(
                "导航到语义位置，例如“厨房”“走廊”“客厅”。内部解析语义地图坐标，不要自行计算坐标。"
                "前置条件：定位就绪、目标位置已登记。返回 UNREACHABLE 时换一个位置或改用 explore_for；"
                "返回 BUSY 表示底盘被其他任务占用，稍后再试。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "location_name": {"type": "string", "description": "语义位置名称，例如 厨房 / kitchen"},
                    "timeout_s": {"type": "number", "default": 60.0, "description": "超时秒数"},
                },
                "required": ["location_name"],
            },
            returns_schema={
                "type": "object",
                "properties": {
                    "final_pose": {"type": "array", "items": {"type": "number"}},
                    "location": {"type": "string"}, "room": {"type": "string"},
                },
            },
            safety_level=SafetyLevel.NORMAL_MOTION,
            timeout_s=60.0,
            distributed_node=NAMESPACE,
            idempotent=False,
            mutates_world=True,
            handler=navigate_to,
        ),
        RobotTool(
            name="navigate_to_pose",
            description=(
                "按 map 坐标导航，仅在语义位置不可用时作为兜底。参数为 x、y（米）与 theta（弧度）。"
                "返回 UNREACHABLE 表示该坐标超出已验收工作空间，不要盲目重试。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "x": {"type": "number", "description": "目标 X（米），map 坐标系"},
                    "y": {"type": "number", "description": "目标 Y（米），map 坐标系"},
                    "theta": {"type": "number", "default": 0.0, "description": "目标朝向（弧度）"},
                    "frame_id": {"type": "string", "default": "map"},
                    "timeout_s": {"type": "number", "default": 60.0},
                },
                "required": ["x", "y"],
            },
            returns_schema={"type": "object", "properties": {"final_pose": {"type": "array"}}},
            safety_level=SafetyLevel.NORMAL_MOTION,
            timeout_s=60.0,
            distributed_node=NAMESPACE,
            idempotent=False,
            mutates_world=True,
            llm_visibility="fallback",
            handler=navigate_to_pose,
        ),
        RobotTool(
            name="explore_for",
            description=(
                "在有界时间内依次前往已登记位置搜索目标并靠近它，用于目标不在当前视野时。"
                "返回 found=false 表示预算内未找到，可换更大预算或先确认目标名称。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "target_desc": {"type": "string", "description": "目标描述，例如 红色杯子"},
                    "max_duration_s": {"type": "number", "default": 120.0},
                },
                "required": ["target_desc"],
            },
            returns_schema={
                "type": "object",
                "properties": {
                    "found": {"type": "boolean"}, "pose": {"type": "array"},
                    "searched": {"type": "array", "items": {"type": "string"}},
                },
            },
            safety_level=SafetyLevel.NORMAL_MOTION,
            timeout_s=120.0,
            distributed_node=NAMESPACE,
            idempotent=False,
            mutates_world=True,
            handler=explore_for,
        ),
        RobotTool(
            name="stop_navigation",
            description=(
                "中止当前导航。回执只表示取消已被接受，底盘是否停止要以新的观测为准。"
                "紧急情况请使用 emergency_stop。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {"command_id": {"type": "string", "description": "要取消的命令 id"}},
            },
            returns_schema={"type": "object", "properties": {"stopped": {"type": "boolean"}}},
            safety_level=SafetyLevel.LOW_SPEED_MOTION,
            timeout_s=10.0,
            distributed_node=NAMESPACE,
            idempotent=True,
            mutates_world=False,
            handler=stop_navigation,
        ),
        RobotTool(
            name="get_current_pose",
            description="查询机器人在 map 坐标系的当前位姿，用于确认导航结果或诊断定位是否就绪。",
            parameters_schema={
                "type": "object",
                "properties": {"frame_id": {"type": "string", "default": "map"}},
            },
            returns_schema={
                "type": "object",
                "properties": {
                    "x": {"type": "number"}, "y": {"type": "number"}, "theta": {"type": "number"},
                },
            },
            safety_level=SafetyLevel.QUERY,
            timeout_s=5.0,
            distributed_node=NAMESPACE,
            idempotent=True,
            handler=get_current_pose,
        ),
    ]


def _match_entity(view, description: str):
    """Find an observed entity matching a free-text description.

    Matching stays conservative: exact id, exact category or a colour/name
    token found in the description. A miss returns ``None`` and the search
    continues, rather than guessing.
    """

    wanted = description.strip().casefold()
    for entity in view.entities:
        entity_id = str(entity.get("entityId") or entity.get("entity_id") or "")
        category = str(entity.get("category") or "")
        attributes = entity.get("attributes") or {}
        colour = str(attributes.get("color") or attributes.get("colour") or "")
        tokens = {entity_id.casefold(), category.casefold(), colour.casefold()}
        tokens.discard("")
        if any(token in wanted for token in tokens):
            return entity
    return None


def _monotonic() -> float:
    import time

    return time.monotonic()
