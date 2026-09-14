"""Work-area planning: a destination as a set of reachable poses, not one point.

A commissioned room waypoint is a single pose chosen when the layout was
surveyed. It is the right default and a bad only option: the same point can be
unreachable after a new survey, too close to a table corner for the driver's own
clearance envelope, or outside the arm's reach once the furniture moves. The map
already carries ranked base-pose candidates for every registered workspace; these
tools expose that, so a caller can ask *where can I stand for the kitchen* and
then command the pose it wants - and verify the arrival against that same pose,
rather than against a point the robot never went to.

This module plans only. Commanding one of the candidates is a composite skill
(`navigate_to_work_area`), because motion has to go through the atomic navigation
tool so it inherits the same safety admission and journalling as every other
command - a planner must never grow its own way to drive the base.

Nothing here may invent a pose: candidates come from the deployment map provider,
which owns identity, the active revision, the current pose, calibrated dimensions
and the collision callback.
"""
from __future__ import annotations

from ..tool_layer import RobotTool, SafetyLevel, ToolError, ToolResult


def _provider(requested, catalog, planning_context):
    """Shared plumbing: fresh localization, then one provider-owned plan call."""
    if catalog is None or planning_context is None:
        return None, ToolResult.failure(
            ToolError.UNREACHABLE,
            "no deployment map provider is configured, so no work area can be planned",
            recoverable=True)
    context = dict(planning_context())
    if context.pop("localization_fresh", False) is not True:
        return context, ToolResult.failure(ToolError.UNREACHABLE, "fresh localization is required")
    try:
        plan = catalog.plan(location_name=requested, **context)
    except (ValueError, KeyError) as exc:
        # The provider owns the interesting reasons - unknown workspace, no free
        # footprint-clear cell, a candidate the IK check refused - and they are
        # all news for the caller, not faults of the tool.
        return context, ToolResult.failure(ToolError.UNREACHABLE, str(exc), recoverable=False)
    return context, plan


def build_mapping_tools(catalog=None, planning_context=None):
    """The work-area planning tool. Driving to a candidate is a composite skill."""

    def plan_work_area(location_name):
        _context, plan = _provider(location_name, catalog, planning_context)
        if isinstance(plan, ToolResult):
            return plan
        candidates = [item for item in plan.get("candidates") or []]
        return ToolResult.ok(**plan, candidateCount=len(candidates),
                             requiresArrivalVerification=True)

    return [
        RobotTool(name='plan_work_area',
            description=('解析已登记的语义工作区，按当前地图、底盘足迹和机械臂范围提出可达底盘候选。'
                         '此工具只规划；需 IK、碰撞检测和执行前新观测。'),
            parameters_schema={'type':'object','properties':{'location_name':{'type':'string'}},
                               'required':['location_name'],'additionalProperties':False},
            returns_schema={'type':'object'}, safety_level=SafetyLevel.QUERY,
            timeout_s=15, distributed_node='robot.map', idempotent=True,
            handler=plan_work_area),
    ]
