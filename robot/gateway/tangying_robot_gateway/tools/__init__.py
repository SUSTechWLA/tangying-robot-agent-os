"""The robot tool layer: a standard, LLM-callable capability surface.

The tools here do not own hardware, transport or safety. They translate a
semantic request into the parameter shape the existing Robot Runtime already
accepts, then normalise the result into :class:`ToolResult`. Everything that
actually touches a robot still flows through one ``ExecuteSkill`` call, so the
safety supervisor remains the single veto point and the closure gate on the
Agent side remains the authority on completion.
"""

from __future__ import annotations

from ..semantic_map import SemanticMap
from ..tool_layer import RobotTool, ToolRegistry
from .manipulation import build_arm_tools, build_gripper_tools
from .navigation import build_navigation_tools
from .perception import build_perception_tools
from .registry import (
    DEFAULT_SAFETY_PROFILE,
    ArmPlan,
    HardwareHealth,
    ObservationView,
    OperationContext,
    RobotAdapter,
)
from .safety import build_safety_tools
from .skills import build_skill_tools

__all__ = [
    "DEFAULT_SAFETY_PROFILE",
    "ArmPlan",
    "HardwareHealth",
    "ObservationView",
    "OperationContext",
    "RobotAdapter",
    "build_registry",
    "default_registry",
]


def build_registry(
    adapter: RobotAdapter,
    semantic_map: SemanticMap,
    *,
    context: OperationContext | None = None,
    include_composite: bool = True,
) -> ToolRegistry:
    """Build the standard tool surface for one robot.

    Order matters only in that composite skills are constructed last: they are
    given the registry of atomic tools and can therefore only call capabilities
    the caller already has.
    """

    tools: list[RobotTool] = []
    tools.extend(build_navigation_tools(adapter, semantic_map, context=context))
    tools.extend(build_arm_tools(adapter, context=context))
    tools.extend(build_gripper_tools(adapter, context=context))
    tools.extend(build_perception_tools(adapter, semantic_map, context=context))
    tools.extend(build_safety_tools(adapter, context=context))
    registry = ToolRegistry(tools)
    if include_composite:
        for tool in build_skill_tools(registry):
            registry.register(tool)
    return registry


def default_registry(
    adapter: RobotAdapter,
    *,
    layout_path: str | None = None,
    context: OperationContext | None = None,
) -> ToolRegistry:
    """Build the tool surface with the shipped semantic layout."""

    return build_registry(adapter, SemanticMap.from_file(layout_path), context=context)
