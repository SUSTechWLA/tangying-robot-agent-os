import time

import pytest
from tangying_robot_gateway.backend import RobotBackend
from tangying_robot_gateway.gateway_adapter import GatewayRobotAdapter
from tangying_robot_gateway.runtime import Observation, Result, RuntimeInfo
from tangying_robot_gateway.service import RobotRuntimeService
from tangying_robot_gateway.tools.registry import OperationContext


class Backend(RobotBackend):
    def __init__(self):
        self.commands = []
        self.stops = []

    def capabilities(self):
        return RuntimeInfo(robot_id="test-robot", adapter="test", manipulation_ready=True)

    def execute(self, command):
        self.commands.append(command)
        return Result(True, "OK", observation_id="after-motion", confidence=1.0)

    def observe(self, request):
        return Observation("obs", int(time.time() * 1000), 0)

    def stop(self, reason):
        self.stops.append(reason)


def test_raw_backend_is_not_a_valid_tool_execution_boundary():
    with pytest.raises(TypeError, match="RobotRuntimeService"):
        GatewayRobotAdapter(Backend())


def test_unapproved_call_does_not_reach_the_backend():
    backend = Backend()
    service = RobotRuntimeService(backend)
    adapter = GatewayRobotAdapter(service)
    result = adapter.execute("manipulation.pick", target_ref="cup", context=OperationContext(
        robot_id="test-robot", command_id="no-approval", safety_profile="desktop_standard",
    ))
    assert result.code == "APPROVAL_REQUIRED"
    assert not backend.commands


def test_shared_service_replays_a_command_and_owns_emergency_latch():
    backend = Backend()
    service = RobotRuntimeService(backend)
    adapter = GatewayRobotAdapter(service)
    context = OperationContext(robot_id="test-robot", task_id="task", command_id="approved",
                               approval_id="operator-approval", safety_profile="desktop_standard")
    for _ in range(2):
        result = adapter.execute("manipulation.pick", target_ref="cup", context=context)
        assert result.success
        assert result.observation_id == "after-motion"
    assert len(backend.commands) == 1
    stopped = adapter.execute("emergency_stop", parameters={"reason": "operator stop"})
    assert stopped.success and stopped.payload["latched"]
    assert service.safety.estop_latched and backend.stops
    assert adapter.observe().emergency_stopped


def test_speed_limit_without_controller_acknowledgement_is_not_success():
    adapter = GatewayRobotAdapter(RobotRuntimeService(Backend()))
    assert not adapter.set_speed_limit("base", 0.01).success
