"""Recall where an object was last seen, from the active map's own object layer.

Perception answers "is it in front of me now". This answers the other half of the
question a task actually has: "where should I go and look". The layer comes from
the map, so it survives a restart, and every entry carries the age of its newest
sighting - a position from an hour ago is a hint about where to drive, never a
claim about where the object is.

The provider owns identity and freshness. The model gets a category and, at most,
attribute filters; it never gets to name a map revision or a coordinate.
"""
from __future__ import annotations

from ..tool_layer import RobotTool, SafetyLevel, ToolError, ToolResult

#: Age beyond which a recalled position is no longer worth driving to by default.
DEFAULT_MAX_AGE_S = 900.0
MAX_MAX_AGE_S = 7 * 24 * 3600.0
#: How many ranked candidates a single recall may return.
MAX_RESULTS = 8


def _filters(attributes):
    if attributes is None:
        return {}
    if not isinstance(attributes, dict):
        raise TypeError("attributes must be an object of name/value strings")
    if len(attributes) > 4:
        raise ValueError("at most four attribute filters are supported")
    result = {}
    for key, value in attributes.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("attribute names must be non-empty strings")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("attribute values must be non-empty strings")
        result[key.strip()] = value.strip()
    return result


def build_object_memory_tools(recall=None):
    """`recall_object`: last known positions of a category, newest first."""

    def recall_object(object_name, max_age_s=DEFAULT_MAX_AGE_S, attributes=None):
        name = str(object_name or "").strip()
        if not name:
            return ToolResult.failure(ToolError.INVALID_PARAM, "object_name is required")
        try:
            wanted = _filters(attributes)
        except (TypeError, ValueError) as error:
            return ToolResult.failure(ToolError.INVALID_PARAM, str(error))
        if (isinstance(max_age_s, bool) or not isinstance(max_age_s, (int, float))
                or not 0 < float(max_age_s) <= MAX_MAX_AGE_S):
            return ToolResult.failure(
                ToolError.INVALID_PARAM,
                f"max_age_s must be within 0..{int(MAX_MAX_AGE_S)} seconds")
        if recall is None:
            return ToolResult.failure(
                ToolError.UNREACHABLE,
                "no map object provider is configured, so no earlier sighting can be recalled",
                recoverable=True)
        try:
            found = recall(name, attributes=wanted, max_age_ms=int(float(max_age_s) * 1000))
        except ValueError as error:
            return ToolResult.failure(ToolError.NOT_FOUND, str(error), recoverable=False)
        matches = list(found or ())[:MAX_RESULTS]
        if not matches:
            return ToolResult.ok(
                found=False, object_name=name, attributes=wanted,
                max_age_s=float(max_age_s), matches=[],
                note="地图里没有该物体在有效期内的记录；需要先到可能的位置观察一次。")
        return ToolResult.ok(
            found=True, object_name=name, attributes=wanted, max_age_s=float(max_age_s),
            matches=matches, best=matches[0],
            # Stated in the payload so no caller can mistake a memory for a view.
            isCurrentObservation=False,
            note="位置来自地图里的历史观测记录，不是当前画面；到位后必须重新观察确认。")

    return [RobotTool(
        name='recall_object',
        description=('查询地图中记录的物体**上次被看到的位置**（含距今年龄与观测次数）。'
                     '这不是当前观测：用于决定去哪个房间找，找到后仍需 observe_scene 重新确认。'),
        parameters_schema={'type':'object','properties':{
            'object_name':{'type':'string'},
            'max_age_s':{'type':'number','minimum':0.001,'maximum':MAX_MAX_AGE_S},
            'attributes':{'type':'object','additionalProperties':{'type':'string'}}},
            'required':['object_name'],'additionalProperties':False},
        returns_schema={'type':'object'}, safety_level=SafetyLevel.QUERY,
        timeout_s=10, distributed_node='robot.map', idempotent=True,
        handler=recall_object)]
