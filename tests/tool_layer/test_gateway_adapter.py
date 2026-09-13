"""The live adapter must not add a second way to reach the robot.

These tests drive ``GatewayRobotAdapter`` against a stub backend that mimics
``PluginBackend``. What matters: every tool call becomes exactly one normal
runtime command with a real lease, deadline and idempotency key, so the safety
supervisor validates it like any other command; and an embodiment without an
inverse-kinematics solver refuses Cartesian arm requests instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from tangying_robot_gateway.gateway_adapter import GatewayRobotAdapter
from tangying_robot_gateway.runtime import Observation, ObservationRequest, Result, RuntimeInfo
from tangying_robot_gateway.service import RobotRuntimeService
from tangying_robot_gateway.tools.registry import OperationContext


@dataclass
class StubCapability:
    name: str
    available: bool = True
    description: str = ""
    input_parameters: tuple = ()
    output_parameters: tuple = ()
    safety_level: str = "physical_motion"


@dataclass
class StubJoint:
    name: str


@dataclass
class StubProfile:
    joints: list = field(default_factory=lambda: [StubJoint("Rotation_R"), StubJoint("Pitch_R")])


@dataclass
class StubInfo(RuntimeInfo):
    robot_id: str = "xlerobot-stub"
    adapter: str = "test"
    manipulation_ready: bool = True
    capabilities: list = field(default_factory=lambda: [StubCapability("arm.move"),
                                                       StubCapability("navigation.navigate")])
    blockers: list = field(default_factory=list)
    semantic_state: object = None


class StubBackend:
    """Records the commands it is given, like the real backend would journal."""

    robot_id = "xlerobot-stub"
    _profile = StubProfile()

    def __init__(self, *, result: Result | None = None, observation=None, raise_observe=False):
        self.commands = []
        self.result = result or Result(True, "OK", "done")
        self.observation = observation or Observation(
            observation_id="obs-1", wall_time_unix_ms=_now_ms(), monotonic_time_ns=0,
            robot_state={"held": ""},
        )
        self.raise_observe = raise_observe
        self.cancelled: list[str] = []
        self.speed_request = None

    def execute(self, command):
        self.commands.append(command)
        return self.result

    def observe(self, request: ObservationRequest):
        if self.raise_observe:
            raise RuntimeError("camera offline")
        return self.observation

    def capabilities(self) -> StubInfo:
        return StubInfo()

    def cancel(self, command_id: str, reason: str) -> bool:
        self.cancelled.append(command_id)
        return True

    def set_speed_limit(self, component: str, limit: float) -> Result:
        self.speed_request = (component, limit)
        return Result(True, "OK")


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


@pytest.fixture
def backend() -> StubBackend:
    return StubBackend()


@pytest.fixture
def adapter(backend) -> GatewayRobotAdapter:
    return GatewayRobotAdapter(RobotRuntimeService(backend), robot_id="xlerobot-stub",
        joint_keys={"right_arm": ("right_arm_Rotation_R.pos", "right_arm_Pitch_R.pos"),
                    "left_arm": ("left_arm_Rotation_L.pos",)},
        gripper_keys={"right_arm": "right_arm_gripper.pos"})


def test_a_tool_call_becomes_one_validated_runtime_command(adapter, backend):
    adapter.execute("manipulation.pick", target_ref="cup",
                    context=OperationContext(robot_id="xlerobot-stub", task_id="t1", command_id="cmd-1", approval_id="approval", safety_profile="desktop_standard"),
                    timeout_s=30.0)
    assert len(backend.commands) == 1
    command = backend.commands[0]
    assert command.capability == "manipulation.pick"
    assert command.command_id == "cmd-1"
    assert command.task_id == "t1"
    # A real lease and deadline: the safety supervisor's checks have something
    # to validate rather than a privileged shortcut.
    assert command.lease_ms == 30_000
    assert command.deadline_unix_ms > _now_ms()
    assert command.idempotency_key == "cmd-1"
    assert command.safety_profile  # never empty


def test_the_lease_is_capped_even_when_the_tool_asks_for_longer(adapter, backend):
    adapter.execute("observe_scene", parameters={}, timeout_s=600.0, context=OperationContext(safety_profile="desktop_standard"))
    assert backend.commands[0].lease_ms == 60_000


def test_a_lease_is_never_zero_or_negative(adapter, backend):
    adapter.execute("observe_scene", parameters={}, timeout_s=0.1, context=OperationContext(safety_profile="desktop_standard"))
    assert backend.commands[0].lease_ms >= 1


def test_a_refused_command_is_returned_unchanged(adapter, backend):
    backend.result = Result(False, "APPROVAL_REQUIRED", "needs approval")
    result = adapter.execute("manipulation.pick", parameters={}, target_ref="red-cup", context=OperationContext(safety_profile="desktop_standard"))
    assert result.success is False and result.code == "APPROVAL_REQUIRED"
    assert not backend.commands  # the supervisor rejects before dispatch


def test_cartesian_arm_requests_are_refused_without_a_solver(adapter):
    plan = adapter.plan_arm_motion(component="right_arm", target_pose=(0.3, 0.0, 0.2, 0.0, 0.0, 0.0))
    assert plan.ok is False
    assert plan.code == "IK_UNAVAILABLE"
    assert "inverse-kinematics" in plan.message


def test_joint_arm_requests_pass_through_with_valid_actuator_keys(adapter, backend):
    declare_planning_units(adapter)
    plan = adapter.plan_arm_motion(component="right_arm", joints=(0.1, 0.2))
    assert plan.ok is True
    keys = set(plan.waypoints[0])
    # The supervisor only accepts an allowed prefix plus a .pos suffix.
    assert keys == {"right_arm_Rotation_R.pos", "right_arm_Pitch_R.pos"}
    assert plan.joints == (0.1, 0.2)


def test_left_component_gets_the_left_prefix(adapter):
    declare_planning_units(adapter)
    plan = adapter.plan_arm_motion(component="left_arm", joints=(0.5,))
    assert list(plan.waypoints[0]) == ["left_arm_Rotation_L.pos"]


def test_too_many_joint_targets_is_a_clear_error(adapter):
    with pytest.raises(ValueError, match="joints"):
        adapter.plan_arm_motion(component="right_arm", joints=tuple(range(9)))


def test_gripper_waypoints_use_the_permitted_key_shape(adapter):
    declare_planning_units(adapter)
    waypoints = adapter.gripper_waypoints("right_arm", 0.03)
    assert waypoints == ({"right_arm_gripper.pos": 0.03},)


def test_observe_reports_a_fresh_timestamped_capture(adapter):
    view = adapter.observe()
    assert view.fresh is True
    assert view.observation_id == "obs-1"
    assert view.observed_at_unix_ms > 0


def test_observe_treats_a_sensor_fault_as_no_data_not_as_fresh(backend):
    backend.raise_observe = True
    view = GatewayRobotAdapter(RobotRuntimeService(backend)).observe()
    assert view.fresh is False
    assert view.observation_id == ""


def test_a_capture_without_a_timestamp_is_not_fresh(backend):
    backend.observation = Observation(observation_id="obs-x", wall_time_unix_ms=0, monotonic_time_ns=0)
    assert GatewayRobotAdapter(RobotRuntimeService(backend)).observe().fresh is False


def test_health_reports_capabilities_and_survives_an_unreachable_runtime(adapter, backend):
    def broken():
        raise RuntimeError("runtime down")
    backend.capabilities = broken
    health = adapter.health()
    assert health.reachable is False
    assert health.errors and "runtime down" in health.errors[0]


def test_health_lists_available_tools(adapter):
    health = adapter.health()
    assert health.reachable is True
    assert "arm.move" in health.available_tools


def test_speed_limit_requires_controller_support(adapter, backend):
    result = adapter.set_speed_limit("right_arm", 0.25)
    assert not result.success
    assert not backend.commands


def test_cancel_does_not_claim_an_idle_robot_was_stopped(adapter, backend):
    assert adapter.cancel("cmd-1", "stop") is False
    assert not backend.cancelled


def test_undeclared_navigation_never_reaches_the_backend(adapter, backend):
    from tangying_robot_gateway.semantic_map import SemanticMap
    from tangying_robot_gateway.tools import build_registry

    registry = build_registry(adapter, SemanticMap.from_file(), context=OperationContext(
        safety_profile="desktop_standard", approval_id="approval",
    ))
    result = registry.require("navigate_to").execute(location_name="kitchen")
    assert not result.success
    assert not backend.commands


def declare_planning_units(adapter, unit=None):
    limits = {name: {"min": -2., "max": 2., "unit": unit or "rad"}
              for names in adapter.joint_keys.values() for name in names}
    limits.update({name: {"min": 0., "max": .09, "unit": unit or "m"}
                   for name in adapter.gripper_keys.values()})
    adapter.backend.capabilities = lambda: StubInfo(robot_profile={"actionLimits": limits})


def test_normalized_actuators_cannot_silently_accept_radians_or_metres(adapter):
    declare_planning_units(adapter, "normalized")
    with pytest.raises(ValueError, match="calibrated conversion"):
        adapter.plan_arm_motion(component="right_arm", joints=(1., 1.))
    with pytest.raises(ValueError, match="calibrated conversion"):
        adapter.gripper_waypoints("right_arm", .08)
