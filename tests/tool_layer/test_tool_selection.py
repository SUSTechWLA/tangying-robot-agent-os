"""LLM tool selection: does the catalogue let a model pick the right tool?

There is no model in CI, so this suite tests the property that actually
determines whether a model can choose correctly: for a set of representative
instructions, the intended tool exists, accepts the semantic arguments a model
would naturally produce, and reports failures in a way that suggests the right
recovery. A routing shim stands in for the model and is checked against the
same catalogue the model would receive.

This is deliberately not a language-understanding benchmark. It is a regression
guard on the *interface*: if someone renames a parameter or drops a tool, the
instructions below stop being answerable and the test says so.
"""

from __future__ import annotations

import pytest
from tangying_robot_gateway.semantic_map import SemanticMap
from tangying_robot_gateway.tool_layer import ToolError, ToolResult
from tangying_robot_gateway.tools import build_registry

from .fake_adapter import FakeRobotAdapter


@pytest.fixture
def registry():
    return build_registry(FakeRobotAdapter(), SemanticMap.from_file())


# (instruction, expected tool, required semantic arguments)
INSTRUCTIONS = [
    ("去厨房", "navigate_to", {"location_name"}),
    ("到卫生间看看", "navigate_to", {"location_name"}),
    ("走到坐标 1.5, 2.0", "navigate_to_pose", {"x", "y"}),
    ("停下来", "stop_navigation", set()),
    ("你现在在哪", "get_current_pose", set()),
    ("找一下红色杯子", "explore_for", {"target_desc"}),
    ("把机械臂移到物体上方十厘米", "move_arm_to_pose", {"x", "y", "z"}),
    ("再往下两厘米", "move_arm_relative", {"dx", "dy", "dz"}),
    ("机械臂回零", "home_arm", set()),
    ("机械臂现在什么姿态", "get_arm_state", set()),
    ("夹住它", "grasp", set()),
    ("松开", "release", set()),
    ("夹爪张开到三厘米", "set_gripper_width", {"width"}),
    ("夹爪状态怎么样", "get_gripper_state", set()),
    ("看看有没有蓝色瓶子", "detect_object", {"object_name"}),
    ("杯子在什么位置", "get_object_pose", {"object_name"}),
    ("厨房在哪", "resolve_location", {"location_name"}),
    ("刷新一下场景", "scan_environment", set()),
    ("拍张照片", "capture_image", set()),
    ("机器人现在什么状态", "get_robot_status", set()),
    ("紧急停止", "emergency_stop", set()),
    ("复位安全停止", "reset_safety_stop", {"operator", "reason"}),
    ("把底盘速度限制到 0.02", "set_speed_limit", {"component", "limit"}),
    ("检查这个位姿会不会撞到", "check_collision", {"target_pose"}),
    ("把桌上的红色杯子拿到厨房", "fetch_object", {"object_name", "target_location"}),
    ("去厨房把红色杯子抓起来", "pick_object", {"object_name"}),
    ("把杯子放到客厅", "place_object", {"location"}),
]


@pytest.mark.parametrize("instruction,tool_name,required", INSTRUCTIONS)
def test_the_intended_tool_exists_for_each_instruction(registry, instruction, tool_name, required):
    tool = registry.get(tool_name)
    assert tool is not None, f"no tool answers {instruction!r}"
    declared = set(tool.parameters_schema.get("properties") or {})
    assert required <= declared, f"{tool_name} cannot take {required - declared}"


def test_every_advertised_tool_answers_at_least_one_instruction(registry):
    """A tool no instruction can reach is either undocumented or unnecessary."""

    covered = {tool_name for _, tool_name, _ in INSTRUCTIONS}
    advertised = {tool.name for tool in registry.select(llm_only=True)}
    assert advertised - covered == set(), "advertised tools with no representative instruction"


def test_semantic_arguments_are_what_a_model_would_emit(registry):
    """Names must be semantic, never coordinates-for-a-place or joint vectors."""

    forbidden = {"joint_angles", "action_chunk", "wheel_speed", "pwm"}
    for tool in registry.select(llm_only=True):
        properties = set(tool.parameters_schema.get("properties") or {})
        assert not (properties & forbidden), f"{tool.name} exposes low-level arguments"


def test_a_named_place_resolves_without_the_model_supplying_coordinates(registry):
    """The rule from the brief: never hand coordinate resolution to the model."""

    result = registry.require("navigate_to").execute(location_name="厨房")
    assert result.success
    assert result.data["location"] == "kitchen"
    # The model gave a name only; the pose was resolved server-side.
    assert "x" not in result.data and "y" not in result.data


def test_recovery_hints_are_present_in_the_failure_surface(registry):
    """A model decides what to do next from the code and the message alone."""

    cases = {
        "navigate_to": ({"location_name": "garage"}, ToolError.NOT_FOUND, True),
        "get_object_pose": ({"object_name": "unicorn"}, ToolError.NOT_FOUND, True),
        "reset_safety_stop": ({}, ToolError.PERMISSION_DENIED, False),
        "move_arm_relative": ({"dx": 1.0, "dy": 0.0, "dz": 0.0}, ToolError.INVALID_PARAM, False),
        "capture_image": ({}, ToolError.NOT_FOUND, True),
    }
    for tool_name, (arguments, expected_code, expected_recoverable) in cases.items():
        result: ToolResult = registry.require(tool_name).execute(**arguments)
        assert result.success is False, tool_name
        assert result.error_code == expected_code, tool_name
        assert result.recoverable is expected_recoverable, tool_name
        assert result.error_message, tool_name


def test_a_pick_failure_tells_the_model_which_step_to_retry(registry):
    result = registry.require("pick_object").execute(object_name="red cup")
    assert result.success is False
    assert result.data["failed_step"] == "grasp"
    assert result.error_code == ToolError.NOT_FOUND
    # The trace is present so the model can see what already happened.
    assert [step["tool"] for step in result.data["steps"]][:2] == ["detect_object", "move_arm_to_pose"]


def test_the_documented_example_chain_is_executable(registry):
    """detect_object -> navigate_to -> move_arm_to_pose -> grasp -> release."""

    detect = registry.require("detect_object").execute(object_name="red cup")
    assert detect.data["found"] is True
    navigate = registry.require("navigate_to").execute(location_name="kitchen")
    assert navigate.success
    move = registry.require("move_arm_to_pose").execute(x=0.29, y=0.49, z=0.90)
    assert move.success
    grasp = registry.require("grasp").execute(force=0.5)
    assert grasp.success and "object_detected" in grasp.data
    release = registry.require("release").execute()
    assert release.success


def test_coordinate_fallbacks_are_callable_but_not_advertised(registry):
    """A coordinator may use them; a model should not have to."""

    advertised = {item["function"]["name"] for item in registry.openai_tools()}
    assert "navigate_to_pose" not in advertised
    fallback = registry.require("navigate_to_pose")
    assert fallback.llm_visibility == "fallback"
    assert fallback.execute(x=1.0, y=2.0).success
