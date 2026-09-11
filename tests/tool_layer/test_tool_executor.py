"""Distributed behaviour: discovery, timeouts, retries, health and locking.

These tests exercise the failure modes the brief calls out — a node going
down, a slow tool, a cancelled call, a duplicate command — and they check the
rule that matters most for safety: a world-mutating tool is never retried
automatically, however transient the error looks.
"""

from __future__ import annotations

import threading
import time

import pytest
from tangying_robot_gateway.semantic_map import SemanticMap
from tangying_robot_gateway.tool_executor import ToolCall, ToolExecutor, describe_failures
from tangying_robot_gateway.tool_layer import (
    RobotTool,
    SafetyLevel,
    ToolError,
    ToolRegistry,
    ToolResult,
)
from tangying_robot_gateway.tools import build_registry

from .fake_adapter import FakeRobotAdapter


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def registry() -> ToolRegistry:
    return build_registry(FakeRobotAdapter(), SemanticMap.from_file())


@pytest.fixture
def executor(registry, clock) -> ToolExecutor:
    return ToolExecutor(registry, clock=clock, backoff_s=0.0)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def test_tools_are_discoverable_by_name_and_resolve_to_a_node(executor):
    assert executor.node_for("navigate_to") == "robot.nav"
    assert executor.node_for("grasp") == "robot.gripper"
    assert executor.node_for("emergency_stop") == "robot.safety"
    assert executor.node_for("not_a_tool") is None


def test_every_namespace_gets_a_registration(executor):
    health = executor.node_health()
    assert set(health) == {"robot.nav", "robot.arm", "robot.gripper", "robot.perception",
                           "robot.safety", "robot.skill"}
    assert "navigate_to" in health["robot.nav"]["tools"]
    assert all(node["healthy"] for node in health.values())


def test_unknown_tool_reports_what_is_available(executor):
    result = executor.execute(ToolCall("fly_to_moon"))
    assert result.error_code == ToolError.NOT_FOUND
    assert "navigate_to" in result.data["known_tools"]


def test_openai_schema_excludes_tools_on_down_nodes(registry, clock):
    executor = ToolExecutor(registry, clock=clock)
    executor.mark_unhealthy("robot.arm", detail={"reason": "solver crashed"})
    names = [item["function"]["name"] for item in executor.openai_tools()]
    assert "move_arm_to_pose" not in names
    assert "navigate_to" in names
    assert executor.unavailable_tools()["move_arm_to_pose"] == ["NODE_UNREACHABLE"]


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


def test_a_node_that_stops_heartbeating_marks_its_tools_unreachable(registry, clock):
    executor = ToolExecutor(registry, clock=clock, node_ttl_s=10.0)
    clock.advance(11.0)
    health = executor.node_health()
    assert health["robot.nav"]["expired"] is True
    assert health["robot.nav"]["healthy"] is False

    result = executor.execute(ToolCall("navigate_to", {"location_name": "kitchen"}))
    assert result.error_code == ToolError.UNREACHABLE
    assert result.data["blockers"] == ["NODE_HEARTBEAT_EXPIRED"]


def test_a_heartbeat_restores_a_node(registry, clock):
    executor = ToolExecutor(registry, clock=clock, node_ttl_s=10.0)
    clock.advance(11.0)
    executor.heartbeat("robot.nav")
    result = executor.execute(ToolCall("navigate_to", {"location_name": "kitchen"}))
    assert result.success


# --------------------------------------------------------------------------
# Retry policy
# --------------------------------------------------------------------------


def _flaky_registry(failures: int, *, mutates_world: bool) -> tuple[ToolRegistry, list[int]]:
    attempts: list[int] = []

    def handler(**_kwargs) -> ToolResult:
        attempts.append(len(attempts) + 1)
        if len(attempts) <= failures:
            return ToolResult.failure(ToolError.TIMEOUT, "transient")
        return ToolResult.ok(value=1)

    registry = ToolRegistry([
        RobotTool(
            name="flaky_query", description="test", parameters_schema={"type": "object", "properties": {}},
            returns_schema={"type": "object", "properties": {}},
            safety_level=SafetyLevel.QUERY, timeout_s=5.0, distributed_node="robot.perception",
            idempotent=True, mutates_world=mutates_world, handler=handler,
        ),
    ])
    return registry, attempts


def test_a_read_only_tool_is_retried_within_budget(clock):
    registry, attempts = _flaky_registry(failures=1, mutates_world=False)
    executor = ToolExecutor(registry, clock=clock, max_attempts=2, backoff_s=0.0)
    result = executor.execute(ToolCall("flaky_query"))
    assert result.success and len(attempts) == 2
    assert result.data["attempt"] == 2


def test_a_read_only_tool_stops_retrying_at_the_budget(clock):
    registry, attempts = _flaky_registry(failures=5, mutates_world=False)
    executor = ToolExecutor(registry, clock=clock, max_attempts=2, backoff_s=0.0)
    result = executor.execute(ToolCall("flaky_query"))
    assert result.success is False and len(attempts) == 2


def test_a_world_mutating_tool_is_never_retried_automatically(clock):
    """Repeating a physical action can duplicate its effect."""

    registry, attempts = _flaky_registry(failures=1, mutates_world=True)
    executor = ToolExecutor(registry, clock=clock, max_attempts=5, backoff_s=0.0)
    result = executor.execute(ToolCall("flaky_query"))
    assert result.success is False
    assert len(attempts) == 1
    assert result.data["attempt"] == 1


def test_an_unknown_outcome_is_not_retried_even_for_a_query(clock):
    attempts: list[int] = []

    def handler(**_kwargs) -> ToolResult:
        attempts.append(1)
        return ToolResult.failure(ToolError.HARDWARE_ERROR,
                                  "unknown", recoverable=False)

    registry = ToolRegistry([
        RobotTool(name="unclear", description="test", parameters_schema={"type": "object", "properties": {}},
                  returns_schema={"type": "object", "properties": {}}, safety_level=SafetyLevel.QUERY,
                  timeout_s=5.0, distributed_node="robot.perception", idempotent=True, handler=handler),
    ])
    executor = ToolExecutor(registry, clock=clock, max_attempts=3, backoff_s=0.0)
    result = executor.execute(ToolCall("unclear"))
    assert result.success is False and len(attempts) == 1


# --------------------------------------------------------------------------
# Timeout and cancellation
# --------------------------------------------------------------------------


def test_a_hung_tool_times_out_without_claiming_the_robot_stopped(clock):
    released = threading.Event()

    def handler(**_kwargs) -> ToolResult:
        released.wait(5.0)
        return ToolResult.ok()

    registry = ToolRegistry([
        RobotTool(name="hang", description="test", parameters_schema={"type": "object", "properties": {}},
                  returns_schema={"type": "object", "properties": {}},
                  safety_level=SafetyLevel.NORMAL_MOTION, timeout_s=0.15,
                  distributed_node="robot.arm", idempotent=False, mutates_world=True, handler=handler),
    ])
    executor = ToolExecutor(registry, clock=clock)
    started = time.monotonic()
    result = executor.execute(ToolCall("hang"))
    elapsed = time.monotonic() - started
    released.set()
    assert result.error_code == ToolError.TIMEOUT
    assert result.data["outcome_uncertain"] is True
    assert elapsed < 2.0


def test_a_pre_cancelled_call_does_not_execute(registry, clock):
    executor = ToolExecutor(registry, clock=clock)
    cancel = threading.Event()
    cancel.set()
    result = executor.execute(ToolCall("navigate_to", {"location_name": "kitchen"}, cancel_event=cancel))
    assert result.error_code == ToolError.CANCELLED


# --------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------


def test_a_write_holds_its_node_so_two_commands_cannot_overlap(registry, clock):
    """The base and the arm must not be driven by two callers at once."""

    executor = ToolExecutor(registry, clock=clock, lock_timeout_s=0.05)
    held = threading.Event()
    release = threading.Event()
    original = executor._execute_with_retry

    def blocking(tool, call, timeout_s):
        held.set()
        release.wait(2.0)
        return original(tool, call, timeout_s)

    executor._execute_with_retry = blocking  # type: ignore[method-assign]
    first = threading.Thread(target=lambda: executor.execute(
        ToolCall("navigate_to", {"location_name": "kitchen"}),
    ))
    first.start()
    assert held.wait(2.0)
    busy = executor.execute(ToolCall("navigate_to", {"location_name": "kitchen"}))
    release.set()
    first.join(2.0)
    assert busy.error_code == ToolError.BUSY
    assert busy.data["node"] == "robot.nav"


def test_different_nodes_do_not_block_each_other(executor, clock):
    results: list[ToolResult] = []

    def call(name, arguments):
        results.append(executor.execute(ToolCall(name, arguments)))

    threads = [
        threading.Thread(target=call, args=("navigate_to", {"location_name": "kitchen"})),
        threading.Thread(target=call, args=("detect_object", {"object_name": "red cup"})),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5.0)
    assert len(results) == 2
    assert all(result.success for result in results)


# --------------------------------------------------------------------------
# Argument validation
# --------------------------------------------------------------------------


def test_missing_required_arguments_are_rejected_before_execution(executor):
    tool = executor.registry.require("navigate_to")
    calls = []
    original = tool.handler
    executor.registry.replace(RobotTool(
        name=tool.name, description=tool.description, parameters_schema=tool.parameters_schema,
        returns_schema=tool.returns_schema, safety_level=tool.safety_level, timeout_s=tool.timeout_s,
        distributed_node=tool.distributed_node, idempotent=tool.idempotent, mutates_world=tool.mutates_world,
        handler=lambda **kwargs: calls.append(kwargs) or original(**kwargs),
    ))
    problem = executor.validate_arguments(ToolCall("navigate_to", {}))
    assert problem is not None and problem.error_code == ToolError.INVALID_PARAM
    assert problem.data["missing_arguments"] == ["location_name"]


def test_unknown_arguments_are_rejected_and_listed(executor):
    problem = executor.validate_arguments(ToolCall("navigate_to", {
        "location_name": "kitchen", "teleport": True,
    }))
    assert problem is not None
    assert problem.data["unknown_arguments"] == ["teleport"]
    assert "location_name" in problem.data["accepted_arguments"]


def test_validation_passes_for_a_well_formed_call(executor):
    assert executor.validate_arguments(ToolCall("navigate_to", {"location_name": "kitchen"})) is None


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def test_tool_call_id_is_echoed_on_success_and_failure(executor):
    ok = executor.execute(ToolCall("navigate_to", {"location_name": "kitchen"}, tool_call_id="call-1"))
    assert ok.tool_call_id == "call-1"
    bad = executor.execute(ToolCall("navigate_to", {"location_name": "nowhere"}, tool_call_id="call-2"))
    assert bad.tool_call_id == "call-2"


def test_failure_summary_is_model_readable(executor):
    result = executor.execute(ToolCall("navigate_to", {"location_name": "nowhere"}))
    summary = describe_failures([result])
    assert summary[0]["error_code"] == "NOT_FOUND"
    assert summary[0]["recovery_class"] == "PERCEPTION"
    assert summary[0]["retryable"] is True


def test_capability_view_reports_down_nodes_as_blockers(registry, clock):
    executor = ToolExecutor(registry, clock=clock)
    executor.mark_unhealthy("robot.gripper")
    infos = {info.name: info for info in executor.capability_infos()}
    assert infos["grasp"].available is False
    assert infos["grasp"].blockers == ["NODE_UNREACHABLE"]
    assert infos["navigate_to"].available is True


def test_executor_rejects_an_invalid_retry_budget(registry):
    with pytest.raises(ValueError):
        ToolExecutor(registry, max_attempts=0)
