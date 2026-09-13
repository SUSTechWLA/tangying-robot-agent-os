"""Optional read-only planning tool bound to a trusted deployment map provider."""
from ..tool_layer import RobotTool, SafetyLevel, ToolError, ToolResult


def build_mapping_tools(catalog, planning_context):
    def plan_work_area(location_name):
        # This provider, never model arguments, owns identity, current pose,
        # active revisions, calibrated dimensions and the IK callback.
        context = dict(planning_context())
        if context.pop('localization_fresh', False) is not True:
            return ToolResult.failure(ToolError.UNREACHABLE, 'fresh localization is required')
        try:
            return ToolResult.ok(**catalog.plan(location_name=location_name, **context))
        except (ValueError, KeyError) as exc:
            return ToolResult.failure(ToolError.UNREACHABLE, str(exc), recoverable=False)

    return [RobotTool(name='plan_work_area',
        description='解析已登记的语义工作区，按当前地图、底盘足迹和机械臂范围提出可达底盘候选。此工具只规划；需 IK、碰撞检测和执行前新观测。',
        parameters_schema={'type':'object','properties':{'location_name':{'type':'string'}},
                           'required':['location_name'],'additionalProperties':False},
        returns_schema={'type':'object'}, safety_level=SafetyLevel.QUERY,
        timeout_s=15, distributed_node='robot.map', idempotent=True,
        handler=plan_work_area)]
