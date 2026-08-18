from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .world import TabletopWorld


@dataclass(frozen=True)
class ToolResult:
    success: bool
    code: str = "OK"
    message: str = ""
    confidence: float = 1.0
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolContext:
    world: TabletopWorld


class Tool(Protocol):
    def execute(
        self,
        context: ToolContext,
        *,
        target_ref: str = "",
        parameters: dict[str, object] | None = None,
    ) -> ToolResult: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def register(self, capability: str, tool: Tool) -> None:
        self._tools[capability] = tool

    def execute(
        self,
        capability: str,
        context: ToolContext,
        *,
        target_ref: str = "",
        parameters: dict[str, object] | None = None,
    ) -> ToolResult:
        tool = self._tools.get(capability)
        if tool is None:
            return ToolResult(False, "SKILL_NOT_ALLOWED", capability)
        return tool.execute(context, target_ref=target_ref, parameters=parameters)


class ObserveSceneTool:
    def execute(self, context, *, target_ref="", parameters=None):
        del target_ref, parameters
        return ToolResult(True, payload={"entities": context.world.entities()})


class ResolveTargetsTool:
    def execute(self, context, *, target_ref="", parameters=None):
        parameters = parameters or {}
        if target_ref:
            matches = [item for item in context.world.entities() if item.entity_id == target_ref]
        else:
            matches = context.world.resolve_all(
                category=str(parameters.get("category", "")),
                color=str(parameters.get("color", "")),
                relation=str(parameters.get("relation", "")),
            )
        if not matches:
            return ToolResult(False, "OBJECT_NOT_FOUND", target_ref)
        if len(matches) > 1:
            return ToolResult(False, "TARGET_AMBIGUOUS", f"found {len(matches)} targets")
        return ToolResult(True, payload={"entity_id": matches[0].entity_id})


class PlanGraspTool:
    def execute(self, context, *, target_ref="", parameters=None):
        parameters = parameters or {}
        arm = context.world.select_arm(target_ref, str(parameters.get("destinationId", "")))
        if arm is None:
            return ToolResult(False, "OBJECT_NOT_FOUND", target_ref)
        context.world.set_active_arm(arm, target_ref)
        return ToolResult(True, payload={"arm": arm})


class PickTool:
    def execute(self, context, *, target_ref="", parameters=None):
        del parameters
        return context.world.pick(target_ref)


class VerifyGraspTool:
    def execute(self, context, *, target_ref="", parameters=None):
        del parameters
        return context.world.verify_grasp(target_ref)


class PlaceTool:
    def execute(self, context, *, target_ref="", parameters=None):
        del parameters
        return context.world.place(target_ref)


class VerifyPlacementTool:
    def execute(self, context, *, target_ref="", parameters=None):
        parameters = parameters or {}
        object_id = str(parameters.get("objectId", ""))
        if not object_id:
            return ToolResult(False, "OBJECT_ID_REQUIRED")
        return context.world.verify_inside(object_id, target_ref)


class RecoverToSafePoseTool:
    def execute(self, context, *, target_ref="", parameters=None):
        del target_ref
        parameters = parameters or {}
        arm = str(parameters.get("arm", "")) or context.world.active_arm
        return context.world.recover_to_safe_pose(arm or None)


def default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("observe_scene", ObserveSceneTool())
    registry.register("resolve_targets", ResolveTargetsTool())
    registry.register("plan_grasp", PlanGraspTool())
    registry.register("manipulation.pick", PickTool())
    registry.register("verify_grasp", VerifyGraspTool())
    registry.register("manipulation.place", PlaceTool())
    registry.register("verify_placement", VerifyPlacementTool())
    registry.register("recover_to_safe_pose", RecoverToSafePoseTool())
    return registry
