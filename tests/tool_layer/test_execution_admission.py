"""Exercise admission against running handlers, not only schema helper methods."""

import threading

import pytest
from tangying_robot_gateway.tool_executor import ToolCall, ToolExecutor
from tangying_robot_gateway.tool_layer import RobotTool, SafetyLevel, ToolRegistry, ToolResult


def tool(name, handler, *, node="robot.arm", motion=True, schema=None):
    return RobotTool(
        name=name, description="test admission", handler=handler,
        parameters_schema=schema or {"type": "object", "properties": {}},
        returns_schema={"type": "object"}, timeout_s=0.05, distributed_node=node,
        safety_level=SafetyLevel.NORMAL_MOTION if motion else SafetyLevel.QUERY,
        mutates_world=motion, idempotent=not motion,
    )


def test_execute_validates_arguments_before_entering_handler():
    calls = []
    executor = ToolExecutor(ToolRegistry([tool("move", lambda **kw: calls.append(kw) or ToolResult.ok())]))
    result = executor.execute(ToolCall("move", {"approval_id": "invented"}))
    assert result.error_code == "INVALID_PARAM"
    assert not calls


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "bad"])
def test_invalid_timeout_never_reaches_handler(value):
    calls = []
    executor = ToolExecutor(ToolRegistry([tool("move", lambda: calls.append(1) or ToolResult.ok())]))
    result = executor.execute(ToolCall("move", timeout_s=value))
    assert result.error_code == "INVALID_PARAM"
    assert not calls


def test_timeout_keeps_robot_motion_reserved_until_worker_exits():
    release = threading.Event()
    exited = threading.Event()
    calls = []

    def hung():
        try:
            release.wait(2)
            return ToolResult.ok()
        finally:
            exited.set()

    executor = ToolExecutor(ToolRegistry([
        tool("move", hung),
        tool("navigate", lambda: calls.append("navigate") or ToolResult.ok(), node="robot.nav"),
        tool("observe", lambda: ToolResult.ok(), node="robot.arm", motion=False),
        tool("emergency_stop", lambda: calls.append("stop") or ToolResult.ok(), node="robot.arm", motion=False),
    ]))
    try:
        result = executor.execute(ToolCall("move"))
        assert result.error_code == "TIMEOUT"
        assert result.recoverable is False
        assert executor.execute(ToolCall("navigate")).error_code == "BUSY"
        assert executor.execute(ToolCall("observe")).success
        executor.mark_unhealthy("robot.arm")
        cancelled = threading.Event()
        cancelled.set()
        assert executor.execute(ToolCall("emergency_stop", cancel_event=cancelled)).success
        assert calls == ["stop"]
    finally:
        release.set()
        assert exited.wait(2)


def test_recursive_schema_types_are_enforced_at_execution():
    calls = []
    executor = ToolExecutor(ToolRegistry([tool(
        "move", lambda **kw: calls.append(kw) or ToolResult.ok(),
        schema={"type": "object", "properties": {
            "pose": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
            "frame": {"type": "string", "enum": ["map"]},
        }, "required": ["pose", "frame"]},
    )]))
    for args in ({"pose": [1, True, 2], "frame": "map"},
                 {"pose": [1, 2, 3], "frame": "world"},
                 {"pose": [1, 2], "frame": "map"}):
        assert executor.execute(ToolCall("move", args)).error_code == "INVALID_PARAM"
    assert not calls


def test_composite_steps_and_invocations_have_distinct_repeatable_ids():
    from tangying_robot_gateway.tools.registry import OperationContext
    operations = []
    context = OperationContext(command_id='root')
    def composite():
        operations.append([context.for_operation('arm.move', timeout_s=1).command_id for _ in range(2)])
        return ToolResult.ok()
    executor = ToolExecutor(ToolRegistry([tool('composite', composite)]))
    executor.execute(ToolCall('composite', tool_call_id='first'))
    executor.execute(ToolCall('composite', tool_call_id='second'))
    executor.execute(ToolCall('composite', tool_call_id='first'))
    assert len(set(operations[0] + operations[1])) == 4
    assert operations[0] == operations[2]
