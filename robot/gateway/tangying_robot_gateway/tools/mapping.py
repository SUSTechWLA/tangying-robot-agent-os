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

from collections.abc import Mapping
from typing import Any

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


def build_mapping_tools(catalog=None, planning_context=None, ensure_mapping=None):
    """The work-area planning tool, and the survey entry point an agent calls.

    Driving to a candidate is a composite skill; asking for a map is not, because
    the decision it carries - reuse what exists or survey - belongs where the
    evidence is (the map catalog and the active revision), not in a model's head.
    """

    def plan_work_area(location_name):
        _context, plan = _provider(location_name, catalog, planning_context)
        if isinstance(plan, ToolResult):
            return plan
        candidates = [item for item in plan.get("candidates") or []]
        return ToolResult.ok(**plan, candidateCount=len(candidates),
                             requiresArrivalVerification=True)

    def build_map(environment: str = "", map_id: str = "", max_travel_m: float = 0.0,
                  max_legs: int = 0):
        """Ask for a usable map of the environment, and let the robot decide how.

        This tool exists because the alternative - the model orchestrating
        ``mapping.start``, then polling ``mapping.status``, then choosing between
        ``mapping.activate`` and a survey - is a sequence the model can get wrong
        in ways that cost a twenty-minute drive. The decision is made where the
        evidence is, and this tool reports it.
        """
        if ensure_mapping is None:
            return ToolResult.failure(
                ToolError.UNREACHABLE,
                "no deployment mapping provider is configured, so no map can be built "
                "or reused; this is a commissioning gap, not a retryable condition",
                recoverable=False)
        arguments: dict[str, Any] = {}
        if environment.strip():
            arguments["environment"] = environment.strip()
        if map_id.strip():
            arguments["mapId"] = map_id.strip()
        if max_travel_m:
            arguments["maxTravelM"] = max(0.0, float(max_travel_m))
        if max_legs:
            arguments["maxLegs"] = max(0, int(max_legs))
        try:
            report = ensure_mapping(arguments)
        except (ValueError, KeyError) as exc:
            # A rejected argument is the caller's to fix; a refused one is news.
            return ToolResult.failure(ToolError.INVALID_PARAM, str(exc), recoverable=False)
        if not isinstance(report, Mapping):
            return ToolResult.failure(
                ToolError.HARDWARE_ERROR,
                f"the mapping provider answered {type(report).__name__}, not a report",
                recoverable=False)
        decision = str(report.get("decision") or "")
        if decision == "ambiguous":
            # Not a failure: the tool did its job and the answer is a question.
            return ToolResult.ok(**report, requiresChoice=True)
        return ToolResult.ok(
            **report,
            # The map the caller now has, whichever branch produced it: a reused
            # package names its id, a fresh survey names the session that will
            # publish one. A caller that has to know which happened reads
            # `decision`.
            reuseExistingMap=decision == "reuse",
            surveyStarted=decision == "explore")

    return [
        RobotTool(name='plan_work_area',
            description=('解析已登记的语义工作区，按当前地图、底盘足迹和机械臂范围提出可达底盘候选。'
                         '此工具只规划；需 IK、碰撞检测和执行前新观测。'),
            parameters_schema={'type':'object','properties':{'location_name':{'type':'string'}},
                               'required':['location_name'],'additionalProperties':False},
            returns_schema={'type':'object'}, safety_level=SafetyLevel.QUERY,
            timeout_s=15, distributed_node='robot.map', idempotent=True,
            handler=plan_work_area),
        RobotTool(name='build_map',
            description=('当用户要求“探索环境 / 建图 / 构建全局地图 / 扫描这里 / 看看这地方长什么样”时调用。'
                         '本工具自行判断该复用已有地图还是自动探索建图：已有可用地图则直接启用并返回 decision=reuse，'
                         '没有则开始探索建图并返回 decision=explore；若 environment 对应多张地图，'
                         '返回 decision=ambiguous 并列出候选，此时应先问用户选哪一张，不要自行决定。'
                         '可选 environment 指定地点（如“客厅”），mapId 指定具体地图。'
                         '建图是移动底盘的长时间任务，返回成功仅表示已开始，'
                         '进度与最终结果要读 mapping.status，不要假设已经建完。'),
            parameters_schema={'type':'object','properties':{
                'environment':{'type':'string'},
                'mapId':{'type':'string'},
                'max_travel_m':{'type':'number','minimum':0,'maximum':120},
                'max_legs':{'type':'integer','minimum':0,'maximum':6}},
                'additionalProperties':False},
            # A survey drives the base through a whole house: it is motion, and it
            # is the longest-running thing this surface can start.
            returns_schema={'type':'object'}, safety_level=SafetyLevel.NORMAL_MOTION,
            timeout_s=30, distributed_node='robot.map', idempotent=False,
            mutates_world=True, handler=build_map),
    ]
