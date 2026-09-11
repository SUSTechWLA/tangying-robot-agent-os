"""Perception and query tools.

These are read-only. They never command motion, so they carry safety level 0
and are idempotent. Everything they return is structured data taken from a
validated observation — never a natural-language description, and never
simulator ground truth.
"""

from __future__ import annotations

import base64
from typing import Any

from ..semantic_map import SemanticMap
from ..tool_layer import RobotTool, SafetyLevel, ToolError, ToolResult
from .registry import OperationContext, RobotAdapter, require_finite, require_text

NAMESPACE = "robot.perception"
OBSERVE_SKILL = "observe_scene"
CAPTURE_BYTE_CEILING = 4 * 1024 * 1024


def _entity_id(entity: Any) -> str:
    if not isinstance(entity, dict):
        return ""
    return str(entity.get("entityId") or entity.get("entity_id") or "")


def _entity_pose(entity: Any) -> list[float]:
    if not isinstance(entity, dict):
        return []
    pose = entity.get("pose") or entity.get("pose_xyz_quat") or []
    if isinstance(pose, (list, tuple)):
        return [float(value) for value in pose]
    return []


def _matches(entity: Any, wanted: str) -> bool:
    """Conservative match on id, category, colour or an exact label."""

    if not isinstance(entity, dict):
        return False
    attributes = entity.get("attributes") or {}
    colour = str(attributes.get("color") or attributes.get("colour") or "")
    candidates = {
        _entity_id(entity).casefold(),
        str(entity.get("category") or "").casefold(),
        colour.casefold(),
    }
    candidates.discard("")
    return any(candidate in wanted for candidate in candidates)


def build_perception_tools(
    adapter: RobotAdapter,
    semantic_map: SemanticMap,
    *,
    context: OperationContext | None = None,
) -> list[RobotTool]:
    """Construct the perception/query capability group."""

    def _context(suffix: str = "", timeout_s: float = 5.0) -> OperationContext:
        return (context or OperationContext()).for_operation(
            OBSERVE_SKILL, timeout_s=timeout_s, suffix=suffix,
        )

    def _observe(*, suffix: str = "", entities: bool = True):
        streams = ("entities", "reconstruction") if entities else ("robot_state",)
        return adapter.observe(streams=streams, context=_context(suffix))

    def detect_object(object_name: str, timeout_s: float = 5.0) -> ToolResult:
        name = require_text(object_name, "object_name")
        require_finite(timeout_s, "timeout_s", default=5.0)
        view = _observe(suffix="/detect")
        if not view.observation_id:
            return ToolResult.failure(ToolError.UNREACHABLE, "no observation available to search")
        wanted = name.casefold()
        for entity in view.entities:
            if _matches(entity, wanted):
                return ToolResult.ok(
                    found=True, entity_id=_entity_id(entity), pose=_entity_pose(entity),
                    confidence=float(entity.get("confidence") or 0.0),
                    observation_id=view.observation_id, source_id=view.source_id,
                )
        # Not found is not an error: it is the answer to "is it in view".
        return ToolResult.ok(
            found=False, object_name=name, observation_id=view.observation_id,
            visible_entities=[_entity_id(entity) for entity in view.entities],
        )

    def get_object_pose(object_name: str, frame_id: str = "base_link") -> ToolResult:
        name = require_text(object_name, "object_name")
        view = _observe(suffix="/pose")
        if not view.observation_id:
            return ToolResult.failure(ToolError.UNREACHABLE, "no observation available")
        wanted = name.casefold()
        for entity in view.entities:
            if _matches(entity, wanted):
                pose = _entity_pose(entity)
                return ToolResult.ok(
                    x=pose[0] if pose else None, y=pose[1] if len(pose) > 1 else None,
                    z=pose[2] if len(pose) > 2 else None, pose=pose, frame_id=frame_id,
                    entity_id=_entity_id(entity), confidence=float(entity.get("confidence") or 0.0),
                    observation_id=view.observation_id,
                )
        return ToolResult.failure(
            ToolError.NOT_FOUND, f"{name!r} is not in the current observation",
            object_name=name, frame_id=frame_id,
            visible_entities=[_entity_id(entity) for entity in view.entities],
        )

    def resolve_location(location_name: str) -> ToolResult:
        name = require_text(location_name, "location_name")
        location = semantic_map.resolve(name)
        if location is None:
            return ToolResult.failure(
                ToolError.NOT_FOUND, f"unknown location {name!r}",
                known_locations=list(semantic_map.names()),
            )
        payload = location.to_dict()
        return ToolResult.ok(
            pose=payload["pose"], room=location.room, name=location.name,
            frame_id=location.frame_id, reachable=location.reachable,
        )

    def scan_environment() -> ToolResult:
        view = _observe(suffix="/scan")
        if not view.observation_id:
            return ToolResult.failure(ToolError.UNREACHABLE, "no observation available")
        scene_graph = {
            "frame_id": "map",
            "observed_at_unix_ms": view.observed_at_unix_ms,
            "source_id": view.source_id,
            "entities": [
                {
                    "entity_id": _entity_id(entity),
                    "category": entity.get("category"),
                    "pose": _entity_pose(entity),
                    "relation": entity.get("relation"),
                    "confidence": entity.get("confidence"),
                }
                for entity in view.entities
            ],
            "known_locations": semantic_map.names(),
        }
        return ToolResult.ok(
            scene_graph=scene_graph, entity_count=len(view.entities),
            observation_id=view.observation_id, point_count=len(view.points),
        )

    def capture_image() -> ToolResult:
        view = _observe(suffix="/capture")
        frame = getattr(view, "frame", b"") or b""
        if not frame:
            return ToolResult.failure(
                ToolError.NOT_FOUND, "no camera frame is available in the latest observation",
                observation_id=view.observation_id,
            )
        if len(frame) > CAPTURE_BYTE_CEILING:
            return ToolResult.failure(
                ToolError.HARDWARE_ERROR,
                f"camera frame of {len(frame)} bytes exceeds the {CAPTURE_BYTE_CEILING} byte limit",
            )
        return ToolResult.ok(
            image_base64=base64.b64encode(frame).decode("ascii"),
            media_type=getattr(view, "frame_media_type", "") or "image/png",
            observation_id=view.observation_id,
        )

    def get_robot_status() -> ToolResult:
        health = adapter.health()
        if not health.reachable:
            return ToolResult.failure(
                ToolError.UNREACHABLE, "robot runtime is not reachable",
                errors=list(health.errors),
            )
        return ToolResult.ok(
            battery=health.battery_percent, mode=health.mode, activity=health.activity,
            errors=list(health.errors), available_tools=list(health.available_tools),
            detail=dict(health.detail),
        )

    return [
        RobotTool(
            name="detect_object",
            description=(
                "在当前视野中检测指定物体。返回 found=false 表示视野内没有，而不是工具失败；"
                "此时可用 explore_for 前往其他位置搜索，或先用 resolve_location 确认位置。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "object_name": {"type": "string", "description": "物体名称或颜色，例如 红色杯子"},
                    "timeout_s": {"type": "number", "default": 5.0},
                },
                "required": ["object_name"],
            },
            returns_schema={
                "type": "object",
                "properties": {
                    "found": {"type": "boolean"}, "pose": {"type": "array"},
                    "confidence": {"type": "number"},
                },
            },
            safety_level=SafetyLevel.QUERY,
            timeout_s=5.0,
            distributed_node=NAMESPACE,
            idempotent=True,
            handler=detect_object,
        ),
        RobotTool(
            name="get_object_pose",
            description=(
                "查询已检测物体的位姿。返回 NOT_FOUND 表示当前观测里没有该物体，"
                "应先 detect_object 或 explore_for，不要凭空假设坐标。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "object_name": {"type": "string"},
                    "frame_id": {"type": "string", "default": "base_link"},
                },
                "required": ["object_name"],
            },
            returns_schema={
                "type": "object",
                "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}},
            },
            safety_level=SafetyLevel.QUERY,
            timeout_s=5.0,
            distributed_node=NAMESPACE,
            idempotent=True,
            handler=get_object_pose,
        ),
        RobotTool(
            name="resolve_location",
            description=(
                "把语义位置名解析为 map 坐标与房间名，供规划或确认目标时参考。"
                "返回 NOT_FOUND 时会列出所有已登记位置。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {"location_name": {"type": "string"}},
                "required": ["location_name"],
            },
            returns_schema={
                "type": "object",
                "properties": {"pose": {"type": "array"}, "room": {"type": "string"}},
            },
            safety_level=SafetyLevel.QUERY,
            timeout_s=5.0,
            distributed_node=NAMESPACE,
            idempotent=True,
            handler=resolve_location,
        ),
        RobotTool(
            name="scan_environment",
            description="刷新并返回当前场景图（实体、关系、已登记位置），用于重新规划前的环境确认。",
            parameters_schema={"type": "object", "properties": {}},
            returns_schema={"type": "object", "properties": {"scene_graph": {}}},
            safety_level=SafetyLevel.QUERY,
            timeout_s=5.0,
            distributed_node=NAMESPACE,
            idempotent=True,
            handler=scan_environment,
        ),
        RobotTool(
            name="capture_image",
            description="拍摄当前相机画面，返回 base64 PNG，供多模态推理使用。没有可用画面时返回 NOT_FOUND。",
            parameters_schema={"type": "object", "properties": {}},
            returns_schema={
                "type": "object",
                "properties": {"image_base64": {"type": "string"}, "media_type": {"type": "string"}},
            },
            safety_level=SafetyLevel.QUERY,
            timeout_s=10.0,
            distributed_node=NAMESPACE,
            idempotent=True,
            handler=capture_image,
        ),
        RobotTool(
            name="get_robot_status",
            description="查询机器人整体状态：模式、活动、可用工具与错误。返回 UNREACHABLE 表示运行时不可达。",
            parameters_schema={"type": "object", "properties": {}},
            returns_schema={
                "type": "object",
                "properties": {"battery": {"type": "number"}, "mode": {"type": "string"},
                               "errors": {"type": "array"}},
            },
            safety_level=SafetyLevel.QUERY,
            timeout_s=5.0,
            distributed_node=NAMESPACE,
            idempotent=True,
            handler=get_robot_status,
        ),
    ]
