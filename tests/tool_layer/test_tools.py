"""Every tool is exercised through its real code path against a fake adapter.

The fake implements the same port the live gateway does, so these tests cover
argument validation, error projection, semantic resolution, composite ordering
and recovery hints without a simulator. Hardware behaviour is separately
covered by the simulator and ROS bridge suites.
"""

from __future__ import annotations

import base64
import json

import pytest
from tangying_robot_gateway.runtime import Result
from tangying_robot_gateway.semantic_map import SemanticMap
from tangying_robot_gateway.tool_layer import SafetyLevel, ToolError, ToolRegistry
from tangying_robot_gateway.tools import build_registry
from tangying_robot_gateway.tools.registry import (
    ArmPlan,
    HardwareHealth,
    ObservationView,
    OperationContext,
)

from .fake_adapter import FakeRobotAdapter


@pytest.fixture
def adapter() -> FakeRobotAdapter:
    return FakeRobotAdapter()


@pytest.fixture
def registry(adapter) -> ToolRegistry:
    return build_registry(adapter, SemanticMap.from_file())


def run(registry: ToolRegistry, name: str, **arguments):
    tool = registry.get(name)
    assert tool is not None, f"{name} is not registered"
    return tool.execute(**arguments)


# --------------------------------------------------------------------------
# Surface
# --------------------------------------------------------------------------


def test_the_standard_surface_is_registered(registry):
    assert set(registry.names()) == {
        # navigation
        "navigate_to", "navigate_to_pose", "explore_for", "stop_navigation", "get_current_pose",
        # arm
        "move_arm_to_pose", "move_arm_to_joints", "move_arm_relative", "home_arm", "get_arm_state",
        # gripper
        "grasp", "release", "set_gripper_width", "get_gripper_state",
        # perception
        "detect_object", "get_object_pose", "resolve_location", "scan_environment",
        "capture_image", "get_robot_status",
        # safety
        "emergency_stop", "reset_safety_stop", "set_speed_limit", "check_collision",
        # composite
        "pick_object", "place_object", "fetch_object",
    }


def test_tool_names_are_function_calling_safe(registry):
    for name in registry.names():
        assert name == name.lower()
        assert "." not in name
        assert name.replace("_", "").isalnum()


def test_safety_tools_are_the_highest_level(registry):
    for name in ("emergency_stop", "reset_safety_stop", "set_speed_limit", "check_collision"):
        assert registry.require(name).safety_level == SafetyLevel.SAFETY


def test_only_fallbacks_are_hidden_from_the_llm(registry):
    primary = {tool.name for tool in registry.select(llm_only=True)}
    assert {"move_arm_to_joints", "navigate_to_pose"}.isdisjoint(primary)
    assert "navigate_to" in primary and "pick_object" in primary


# --------------------------------------------------------------------------
# Navigation
# --------------------------------------------------------------------------


def test_navigate_to_resolves_the_name_and_never_asks_the_model_for_coordinates(registry, adapter):
    result = run(registry, "navigate_to", location_name="厨房")
    assert result.success
    assert result.data["location"] == "kitchen"
    assert result.data["room"] == "kitchen"
    call = adapter.skill_calls("navigation.navigate")[0]
    # The resolved pose is the commissioned waypoint, computed server-side.
    assert call.parameters["goalPose"][:2] == [2.2, 3.35]
    assert len(call.parameters["goalPose"]) == 7


def test_navigate_to_unknown_place_lists_what_is_known(registry):
    result = run(registry, "navigate_to", location_name="garage")
    assert result.success is False
    assert result.error_code == ToolError.NOT_FOUND
    assert "kitchen" in result.data["known_locations"]
    assert result.recoverable is True


def test_navigate_to_propagates_runtime_failure_with_the_place_named(adapter):
    adapter.results["navigation.navigate"] = Result(False, "NAV_MAP_NOT_READY", "map not ready")
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "navigate_to", location_name="kitchen")
    assert result.success is False
    assert result.error_code == ToolError.UNREACHABLE
    assert result.recoverable is True
    assert result.data["runtime_code"] == "NAV_MAP_NOT_READY"
    assert result.data["location"] == "kitchen"


def test_navigate_to_pose_rejects_an_unsupported_frame(registry):
    result = run(registry, "navigate_to_pose", x=1.0, y=2.0, frame_id="odom")
    assert result.error_code == ToolError.INVALID_PARAM
    assert result.data["supported_frames"] == ["map"]


def test_navigate_to_pose_builds_a_normalised_quaternion(registry, adapter):
    result = run(registry, "navigate_to_pose", x=1.0, y=2.0, theta=1.5707963267948966)
    assert result.success
    pose = adapter.skill_calls("navigation.navigate")[0].parameters["goalPose"]
    assert pose[:3] == [1.0, 2.0, 0.0]
    assert sum(value * value for value in pose[3:]) == pytest.approx(1.0)


def test_explore_for_stops_as_soon_as_the_target_is_seen(registry, adapter):
    result = run(registry, "explore_for", target_desc="red cup")
    assert result.success and result.data["found"] is True
    assert result.data["entity_id"] == "red-cup"
    # One leg navigated, then observed: it stopped instead of sweeping the flat.
    assert len(adapter.skill_calls("navigation.navigate")) == 1
    assert result.data["searched"] == [result.data["location"]]


def test_explore_for_reports_not_found_without_failing_the_tool(adapter):
    adapter.observation = ObservationView(
        observation_id="obs-2", fresh=True, entities=({"entityId": "blue-bottle", "category": "bottle"},),
    )
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "explore_for", target_desc="red cup", max_duration_s=30.0)
    assert result.success is True
    assert result.data["found"] is False
    assert len(result.data["searched"]) >= 1


def test_stop_navigation_without_a_command_id_is_a_parameter_error(registry):
    result = run(registry, "stop_navigation")
    assert result.error_code == ToolError.INVALID_PARAM


def test_stop_navigation_reports_idle_when_nothing_is_in_flight(adapter):
    adapter.cancel_accepted = False
    registry = build_registry(
        adapter, SemanticMap.from_file(),
        context=OperationContext(robot_id="r1", command_id="cmd-nav"),
    )
    result = run(registry, "stop_navigation")
    assert result.success and result.data["stopped"] is True
    assert result.data["cancelled"] is False
    assert "stop_verified" not in result.data  # nothing moved, so nothing to verify


def test_get_current_pose_reads_the_observation_not_a_command(registry, adapter):
    result = run(registry, "get_current_pose")
    assert result.success
    assert (result.data["x"], result.data["y"]) == (0.0, -1.25)
    assert result.data["observation_id"] == "obs-1"
    assert adapter.skill_calls("navigation.navigate") == []


def test_get_current_pose_refuses_a_stale_observation(adapter):
    adapter.observation = ObservationView(observation_id="", fresh=False)
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "get_current_pose")
    assert result.error_code == ToolError.UNREACHABLE


# --------------------------------------------------------------------------
# Arm and gripper
# --------------------------------------------------------------------------


def test_move_arm_to_pose_sends_waypoints_never_the_raw_pose(registry, adapter):
    result = run(registry, "move_arm_to_pose", x=0.3, y=0.0, z=0.2)
    assert result.success
    assert result.data["final_pose"]
    parameters = adapter.skill_calls("arm.move")[0].parameters
    # The LLM's pose was translated into actuator keys by the adapter port;
    # no joint or actuator name appears in the tool's own parameters.
    assert list(parameters) == ["action_chunk"]
    assert parameters["action_chunk"] == [{"left_arm_Pitch_L.pos": 1.0}]


def test_move_arm_to_pose_rejects_unknown_component_and_bad_scaling(registry):
    assert run(registry, "move_arm_to_pose", x=0.1, y=0.0, z=0.1,
               component="third_arm").error_code == ToolError.INVALID_PARAM
    assert run(registry, "move_arm_to_pose", x=0.1, y=0.0, z=0.1,
               velocity_scaling=2.5).error_code == ToolError.INVALID_PARAM


def test_move_arm_to_pose_surfaces_an_unreachable_plan(adapter):
    adapter.arm_plan_result = ArmPlan(ok=False, code="TARGET_UNREACHABLE", message="outside workspace")
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "move_arm_to_pose", x=9.0, y=0.0, z=0.1)
    assert result.error_code == ToolError.UNREACHABLE
    assert result.recoverable is False
    assert adapter.skill_calls("arm.move") == []


def test_move_arm_relative_enforces_the_fine_adjustment_limit(registry, adapter):
    result = run(registry, "move_arm_relative", dx=0.4, dy=0.0, dz=0.0)
    assert result.error_code == ToolError.INVALID_PARAM
    assert result.data["limit_m"] == 0.30
    assert adapter.skill_calls("arm.move") == []


def test_move_arm_relative_accepts_a_small_nudge(registry, adapter):
    result = run(registry, "move_arm_relative", dx=0.0, dy=0.0, dz=0.02)
    assert result.success
    assert adapter.skill_calls("arm.move")


def test_home_arm_uses_the_existing_safe_pose_skill(registry, adapter):
    result = run(registry, "home_arm")
    assert result.success and result.data["homed"] is True
    assert adapter.skill_calls("recover_to_safe_pose")


def test_get_arm_state_reports_joints_and_grippers(registry):
    result = run(registry, "get_arm_state")
    assert result.success
    assert result.data["joint_angles"] == {"Pitch_L": 3.1}
    assert result.data["grippers"] == {"right_arm": "open"}


def test_grasp_reports_whether_an_object_was_detected(registry, adapter):
    result = run(registry, "grasp", force=0.4, width=0.03)
    assert result.success
    assert result.data["object_detected"] is False  # nothing in state["held"], gripper open
    assert result.data["width"] == 0.03
    assert adapter.skill_calls("arm.move")


def test_grasp_reports_a_held_object_from_the_observation(adapter):
    adapter.observation = ObservationView(
        observation_id="obs-3", fresh=True,
        robot_state={"held": "red-cup", "grippers": {"right_arm": "closed"}},
    )
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "grasp")
    assert result.data["object_detected"] is True
    assert result.data["held_object"] == "red-cup"


def test_grasp_rejects_a_width_beyond_the_gripper(registry):
    assert run(registry, "grasp", width=0.5).error_code == ToolError.INVALID_PARAM


def test_release_opens_the_gripper(registry, adapter):
    result = run(registry, "release")
    assert result.success and result.data["released"] is True
    assert adapter.called("gripper_waypoints")


def test_gripper_commands_fail_closed_when_the_adapter_cannot_build_keys(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "gripper_waypoints", lambda component, width: ())
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "release")
    assert result.error_code == ToolError.HARDWARE_ERROR


# --------------------------------------------------------------------------
# Perception
# --------------------------------------------------------------------------


def test_detect_object_returns_found_false_rather_than_failing(registry):
    result = run(registry, "detect_object", object_name="绿色杯子")
    assert result.success is True
    assert result.data["found"] is False
    assert "red-cup" in result.data["visible_entities"]


def test_detect_object_matches_on_colour(registry):
    result = run(registry, "detect_object", object_name="red cup")
    assert result.data["found"] is True
    assert result.data["entity_id"] == "red-cup"


def test_get_object_pose_fails_with_not_found_for_an_absent_object(registry):
    result = run(registry, "get_object_pose", object_name="绿色杯子")
    assert result.error_code == ToolError.NOT_FOUND
    assert result.recoverable is True


def test_resolve_location_is_pure_and_needs_no_robot(adapter, registry):
    result = run(registry, "resolve_location", location_name="卫生间")
    assert result.success
    assert result.data["room"] == "bathroom"
    assert adapter.calls == []


def test_scan_environment_returns_a_structured_scene_graph(registry):
    result = run(registry, "scan_environment")
    assert result.success
    graph = result.data["scene_graph"]
    assert graph["entities"][0]["entity_id"] == "red-cup"
    assert "kitchen" in graph["known_locations"]
    json.dumps(graph)


def test_capture_image_returns_base64_or_a_clear_failure(adapter):
    adapter.observation = ObservationView(
        observation_id="obs-4", fresh=True, frame=b"\x89PNG\r\n\x1a\nfake", frame_media_type="image/png",
    )
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "capture_image")
    assert result.success
    assert base64.b64decode(result.data["image_base64"]).startswith(b"\x89PNG")


def test_capture_image_reports_not_found_without_a_frame(registry):
    result = run(registry, "capture_image")
    assert result.error_code == ToolError.NOT_FOUND


def test_get_robot_status_maps_unreachable_hardware(adapter):
    adapter.health_state = HardwareHealth(reachable=False, errors=("BRIDGE_DOWN",))
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "get_robot_status")
    assert result.error_code == ToolError.UNREACHABLE
    assert result.data["errors"] == ["BRIDGE_DOWN"]


def test_get_robot_status_reports_mode_and_tools(registry):
    result = run(registry, "get_robot_status")
    assert result.success and result.data["mode"] == "SIMULATION"


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


def test_emergency_stop_reports_confirmation_not_just_dispatch(adapter):
    adapter.results["emergency_stop"] = Result(True, "OK", "latched", "", 1.0)
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "emergency_stop", reason="operator")
    assert result.success and result.data["stop_confirmed"] is True


def test_emergency_stop_never_claims_success_when_the_runtime_refused(adapter):
    adapter.results["emergency_stop"] = Result(False, "BACKEND_STOP_FAILED", "stop failed")
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "emergency_stop")
    assert result.success is False
    assert result.data["stop_confirmed"] is False


def test_reset_safety_stop_requires_an_identified_operator(registry, adapter):
    result = run(registry, "reset_safety_stop")
    assert result.error_code == ToolError.PERMISSION_DENIED
    assert result.data["required_arguments"] == ["operator", "reason"]
    assert adapter.skill_calls("reset_safety_stop") == []


def test_reset_safety_stop_passes_through_with_operator_and_reason(registry, adapter):
    result = run(registry, "reset_safety_stop", operator="alice", reason="area cleared")
    assert result.success and result.data["reset"] is True
    assert adapter.skill_calls("reset_safety_stop")[0].parameters["operator"] == "alice"


def test_set_speed_limit_validates_component_and_range(registry, adapter):
    assert run(registry, "set_speed_limit", component="wheel", limit=0.1).error_code == ToolError.INVALID_PARAM
    result = run(registry, "set_speed_limit", component="base", limit=0.02)
    assert result.success and adapter.speed_limits == [("base", 0.02)]
    over = run(registry, "set_speed_limit", component="base", limit=1.0)
    assert over.error_code == ToolError.INVALID_PARAM
    assert over.data["allowed_range"] == [0.0, 0.05]


def test_check_collision_reports_risk_without_moving(registry, adapter):
    adapter.arm_plan_result = ArmPlan(ok=False, code="COLLISION_RISK", message="hit the table")
    result = run(registry, "check_collision", target_pose={"x": 0.2, "y": 0.0, "z": 0.01})
    assert result.error_code == ToolError.COLLISION_RISK
    assert result.data["safe_to_move"] is False
    assert adapter.skill_calls("arm.move") == []


def test_check_collision_reports_safe_for_a_reachable_pose(registry):
    result = run(registry, "check_collision", target_pose={"x": 0.3, "y": 0.0, "z": 0.2})
    assert result.success and result.data["safe_to_move"] is True


def test_check_collision_rejects_a_malformed_pose(registry):
    result = run(registry, "check_collision", target_pose={"x": 0.3, "y": 0.0})
    assert result.error_code == ToolError.INVALID_PARAM


# --------------------------------------------------------------------------
# Composite skills
# --------------------------------------------------------------------------


def test_pick_object_runs_the_documented_chain_in_order(registry, adapter):
    adapter.observation = ObservationView(
        observation_id="obs-5", fresh=True,
        robot_state={"held": "red-cup", "grippers": {"right_arm": "closed"}},
        entities=({"entityId": "red-cup", "category": "cup", "pose": [0.29, 0.49, 0.80]},),
    )
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "pick_object", object_name="red cup")
    assert result.success, result
    assert [step["tool"] for step in result.data["steps"]] == [
        "detect_object", "move_arm_to_pose", "set_gripper_width", "grasp", "move_arm_relative",
    ]
    assert result.data["held_object"] == "red-cup"


def test_pick_object_reports_the_failing_step_when_the_gripper_grabs_nothing(registry, adapter):
    result = run(registry, "pick_object", object_name="red cup")
    assert result.success is False
    assert result.error_code == ToolError.NOT_FOUND
    assert result.data["failed_step"] == "grasp"
    assert result.recoverable is True


def test_pick_object_aborts_before_motion_when_the_object_is_not_visible(registry, adapter):
    result = run(registry, "pick_object", object_name="绿色杯子")
    assert result.error_code == ToolError.NOT_FOUND
    assert result.data["failed_step"] == "detect_object"
    assert adapter.skill_calls("arm.move") == []


def test_pick_object_names_the_navigation_failure(adapter):
    adapter.results["navigation.navigate"] = Result(False, "NAV_MAP_NOT_READY", "no map")
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "pick_object", object_name="red cup", location="厨房")
    assert result.success is False
    assert result.data["failed_step"] == "navigate_to"
    assert result.error_code == ToolError.UNREACHABLE


def test_place_object_confirms_the_gripper_is_empty(registry, adapter):
    result = run(registry, "place_object", location="厨房")
    assert result.success, result
    assert result.data["released"] is True
    assert [step["tool"] for step in result.data["steps"]] == [
        "navigate_to", "move_arm_relative", "release", "get_gripper_state",
    ]


def test_place_object_fails_if_something_is_still_held(adapter):
    adapter.observation = ObservationView(
        observation_id="obs-6", fresh=True, robot_state={"held": "red-cup", "grippers": {"right_arm": "closed"}},
    )
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "place_object", location="厨房")
    assert result.success is False
    assert result.data["failed_step"] == "release"


def test_fetch_object_chains_pick_and_place(adapter):
    adapter.observation = ObservationView(
        observation_id="obs-7", fresh=True,
        robot_state={"held": "red-cup", "grippers": {"right_arm": "closed"}},
        entities=({"entityId": "red-cup", "category": "cup", "pose": [0.29, 0.49, 0.80]},),
    )
    registry = build_registry(adapter, SemanticMap.from_file())
    result = run(registry, "fetch_object", object_name="red cup", target_location="厨房")
    # place_object's final get_gripper_state still reports held, so the chain
    # must report that rather than claiming delivery.
    assert result.success is False
    assert result.data["failed_step"] in {"release", "place_object"}
    assert "厨房" in result.error_message or result.data.get("failed_step")


def test_fetch_object_reports_success_once_the_object_is_released(adapter):
    """A full pick-then-place chain, with the gripper modelled as state.

    The double tracks whether the fingers are closed and what is between them,
    so the chain is exercised end to end instead of being told the answer: the
    grasp must be seen to hold the cup, and the place must be seen to free it.
    """

    class StatefulGripper(FakeRobotAdapter):
        def __init__(self):
            super().__init__()
            self.closed = False
            self.holding = ""

        def execute(self, skill, *, parameters=None, target_ref="", context=None, timeout_s=None):
            if skill == "arm.move":
                width = next((step[key] for step in (parameters or {}).get("action_chunk", [])
                              for key in step if key.endswith("gripper.pos")), None)
                if width is not None:
                    self.closed = width < 0.05
                    self.holding = "red-cup" if self.closed else ""
            return super().execute(skill, parameters=parameters, target_ref=target_ref,
                                   context=context, timeout_s=timeout_s)

        def observe(self, *, streams=("entities", "reconstruction"), context=None):
            self.observation = ObservationView(
                observation_id=f"obs-{len(self.calls)}", fresh=True,
                robot_state={"held": self.holding,
                             "grippers": {"right_arm": "closed" if self.closed else "open"}},
                entities=({"entityId": "red-cup", "category": "cup",
                           "attributes": {"color": "red"}, "pose": [0.29, 0.49, 0.80]},),
            )
            return super().observe(streams=streams, context=context)

    scripted = StatefulGripper()
    registry = build_registry(scripted, SemanticMap.from_file())
    result = run(registry, "fetch_object", object_name="red cup", target_location="厨房")
    assert result.success, result
    assert result.data["delivered"] is True
    assert [step["tool"] for step in result.data["steps"]] == ["pick_object", "place_object"]
    # Both legs actually moved the robot, and the gripper ended open.
    assert scripted.skill_calls("arm.move")
    assert scripted.holding == ""


# --------------------------------------------------------------------------
# Capability projection
# --------------------------------------------------------------------------


def test_registry_projects_capabilities_for_the_runtime(registry):
    infos = {info.name: info for info in registry.capability_infos()}
    assert infos["navigate_to"].mutates_world is True
    assert infos["detect_object"].mutates_world is False
    assert infos["get_current_pose"].safety_level == "read_only"
    assert infos["grasp"].safety_level == "physical_contact"
    assert infos["emergency_stop"].safety_level == "safety_critical"
    assert infos["navigate_to"].default_timeout_ms == 60_000


def test_every_write_tool_declares_a_timeout_and_a_target_node(registry):
    for tool in registry:
        assert tool.timeout_s > 0
        assert tool.distributed_node


def test_operation_context_derives_deterministic_identities():
    context = OperationContext(robot_id="r1", task_id="t1", command_id="cmd")
    first = context.for_operation("navigation.navigate", timeout_s=60.0)
    second = context.for_operation("navigation.navigate", timeout_s=60.0)
    assert first.command_id == second.command_id == "cmd/navigation.navigate"
    assert first.idempotency_key == first.command_id
    assert first.task_id == "t1" and first.robot_id == "r1"
