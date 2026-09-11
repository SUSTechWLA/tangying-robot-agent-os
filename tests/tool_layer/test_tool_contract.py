"""The tool contract is the LLM's only view of the robot, so it is tested hard.

Two properties matter most and are asserted directly:

1. a failure's ``recoverable`` flag comes from the same classification the Go
   Agent uses, so Python and Go cannot disagree about retry safety, and
2. an unrecognised runtime code is treated as an unknown outcome, never as a
   retryable one.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest
from tangying_robot_gateway.runtime import Result
from tangying_robot_gateway.tool_layer import (
    RECOVERABLE_CLASSES,
    RecoveryClass,
    RobotTool,
    SafetyLevel,
    ToolError,
    ToolRegistry,
    ToolResult,
    known_runtime_codes,
    recovery_class_map,
    standard_error,
)

REPO = Path(__file__).resolve().parents[2]
CLOSED_LOOP_GO = REPO / "core/closedloop/closedloop.go"


def make_tool(**overrides) -> RobotTool:
    fields = {
        "name": "navigate_to",
        "description": "Navigate to a named place.",
        "parameters_schema": {
            "type": "object",
            "properties": {"location_name": {"type": "string"}, "timeout_s": {"type": "number"}},
            "required": ["location_name"],
        },
        "returns_schema": {"type": "object", "properties": {"final_pose": {"type": "array"}}},
        "safety_level": SafetyLevel.NORMAL_MOTION,
        "timeout_s": 60.0,
        "distributed_node": "robot.nav",
        "idempotent": False,
        "handler": lambda **kwargs: ToolResult.ok(**kwargs),
        "mutates_world": True,
    }
    fields.update(overrides)
    return RobotTool(**fields)


# --------------------------------------------------------------------------
# ToolResult
# --------------------------------------------------------------------------


def test_success_carries_no_error_code():
    result = ToolResult.ok(final_pose=[1.0, 2.0, 0.0])
    assert result.success and result.error_code is None and not result.recoverable
    assert result.data["final_pose"] == [1.0, 2.0, 0.0]
    assert result.timestamp > 0


def test_result_fields_cannot_be_rebound_after_validation():
    """Frozen fields stop a later stage from rewriting a stored verdict."""

    result = ToolResult.ok(x=1)
    # A frozen dataclass raises FrozenInstanceError, which is a subclass of
    # AttributeError; asserting that type keeps the test meaningful if the
    # result ever stops being frozen.
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.success = False  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.error_code = ToolError.TIMEOUT  # type: ignore[misc]


def test_contradictory_results_are_rejected_at_construction():
    with pytest.raises(ValueError):
        ToolResult(success=True, error_code=ToolError.TIMEOUT)
    with pytest.raises(ValueError):
        ToolResult(success=False)
    with pytest.raises(ValueError):
        ToolResult(success=False, error_code="SOMETHING_ELSE")


@pytest.mark.parametrize(
    "code,expected_error,expected_recoverable",
    [
        ("NAV_VELOCITY_STALE", ToolError.TIMEOUT, True),
        ("OBJECT_NOT_FOUND", ToolError.NOT_FOUND, True),
        ("NAV_MAP_NOT_READY", ToolError.UNREACHABLE, True),
        ("TARGET_UNREACHABLE", ToolError.UNREACHABLE, False),
        ("NAV_WORKSPACE_LIMIT", ToolError.UNREACHABLE, False),
        ("FENCING_TOKEN_STALE", ToolError.BUSY, False),
        ("APPROVAL_REQUIRED", ToolError.PERMISSION_DENIED, False),
        ("TOOL_PARAMETERS_INVALID", ToolError.INVALID_PARAM, False),
        ("EXECUTION_OUTCOME_UNKNOWN", ToolError.HARDWARE_ERROR, False),
        ("EMERGENCY_STOP_LATCHED", ToolError.SAFETY_STOP, False),
        ("CANCELLED", ToolError.CANCELLED, False),
        ("", ToolError.HARDWARE_ERROR, False),
        ("SOME_FUTURE_CODE", ToolError.HARDWARE_ERROR, False),
    ],
)
def test_error_projection_is_stable(code, expected_error, expected_recoverable):
    error, recovery, recoverable = standard_error(code)
    assert error is expected_error
    assert recoverable is expected_recoverable
    assert (recovery in RECOVERABLE_CLASSES) is recoverable


def test_unknown_runtime_code_is_an_unknown_outcome_not_a_retry():
    code, recovery, recoverable = standard_error("WHO_KNOWS")
    assert code is ToolError.HARDWARE_ERROR
    assert recovery is RecoveryClass.UNKNOWN_OUTCOME
    assert recoverable is False


def test_failure_derives_recoverable_from_the_code_but_allows_override():
    assert ToolResult.failure("NAV_VELOCITY_STALE").recoverable is True
    assert ToolResult.failure("TARGET_UNREACHABLE").recoverable is False
    # An explicit override is a deliberate statement by the caller.
    assert ToolResult.failure("TARGET_UNREACHABLE", recoverable=True).recoverable is True


def test_runtime_result_normalisation_keeps_the_runtime_code():
    ok = ToolResult.from_runtime(Result(True, "OK", "done", "obs-1", 0.9), final_pose=[0, 0, 0])
    assert ok.success and ok.error_code is None

    failed = ToolResult.from_runtime(Result(False, "NAV_MAP_NOT_READY", "map not ready", "obs-2", 0.0))
    assert failed.success is False
    assert failed.error_code == ToolError.UNREACHABLE
    assert failed.recoverable is True
    assert failed.data["runtime_code"] == "NAV_MAP_NOT_READY"
    assert failed.data["observation_id"] == "obs-2"
    assert "map not ready" in failed.error_message


@pytest.mark.parametrize(
    "exc,expected",
    [
        (TimeoutError("too slow"), ToolError.TIMEOUT),
        (PermissionError("no"), ToolError.PERMISSION_DENIED),
        (ValueError("bad"), ToolError.INVALID_PARAM),
        (KeyError("missing"), ToolError.INVALID_PARAM),
        (RuntimeError("boom"), ToolError.HARDWARE_ERROR),
    ],
)
def test_exceptions_become_structured_failures(exc, expected):
    result = ToolResult.from_exception(exc, context="tool step")
    assert result.success is False and result.error_code == expected


def test_call_id_is_attached_without_mutating_the_original():
    result = ToolResult.ok(x=1)
    tagged = result.with_call_id("call-7")
    assert tagged.tool_call_id == "call-7" and result.tool_call_id is None
    assert tagged.with_call_id(None) is tagged


# --------------------------------------------------------------------------
# RobotTool
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"name": "NavigateTo"}, "lower snake"),
        ({"name": "navigate.to"}, "dot"),
        ({"name": ""}, "lower snake"),
        ({"safety_level": 9}, "safety level"),
        ({"timeout_s": 0}, "positive timeout"),
        ({"description": "   "}, "description"),
        ({"distributed_node": ""}, "target node"),
        ({"llm_visibility": "hidden"}, "llm_visibility"),
    ],
)
def test_invalid_tool_definitions_are_rejected(overrides, message):
    with pytest.raises(ValueError, match=message):
        make_tool(**overrides)


def test_execute_never_raises_and_always_returns_a_tool_result():
    def explode(**_kwargs):
        raise RuntimeError("hardware exploded")

    tool = make_tool(handler=explode)
    result = tool.execute()
    assert result.success is False
    assert result.error_code == ToolError.HARDWARE_ERROR
    assert "hardware exploded" in result.error_message


def test_execute_rejects_a_handler_that_returns_the_wrong_type():
    tool = make_tool(handler=lambda **_kwargs: {"success": True})
    result = tool.execute()
    assert result.success is False
    assert result.error_code == ToolError.HARDWARE_ERROR
    assert "not ToolResult" in result.error_message


def test_capability_view_matches_the_runtime_contract():
    capability = make_tool().capability_info()
    assert capability.name == "navigate_to"
    assert capability.safety_level == "physical_motion"
    assert capability.mutates_world is True
    assert capability.default_timeout_ms == 60_000
    assert capability.input_parameters == ["location_name", "timeout_s"]
    assert capability.cancellable is True

    read_only = make_tool(name="get_current_pose", idempotent=True, safety_level=SafetyLevel.QUERY,
                          mutates_world=False).capability_info()
    assert read_only.safety_level == "read_only"
    assert read_only.mutates_world is False
    assert read_only.cancellable is False


def test_safety_tools_report_the_highest_safety_level():
    capability = make_tool(name="emergency_stop", safety_level=SafetyLevel.SAFETY,
                           mutates_world=False).capability_info()
    assert capability.safety_level == "safety_critical"


def test_openai_schema_is_plain_json():
    schema = make_tool().openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "navigate_to"
    json.dumps(schema)  # must be serialisable for the API
    assert schema["function"]["parameters"]["required"] == ["location_name"]


# --------------------------------------------------------------------------
# ToolRegistry
# --------------------------------------------------------------------------


def test_registry_register_discover_and_list():
    registry = ToolRegistry([make_tool()])
    registry.register(make_tool(name="get_current_pose", idempotent=True,
                                safety_level=SafetyLevel.QUERY, mutates_world=False))
    assert registry.names() == ("get_current_pose", "navigate_to")
    assert "navigate_to" in registry and len(registry) == 2
    assert registry.get("missing") is None
    with pytest.raises(KeyError):
        registry.require("missing")


def test_registry_rejects_duplicate_names_and_non_tools():
    registry = ToolRegistry([make_tool()])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(make_tool())
    with pytest.raises(TypeError):
        registry.register("navigate_to")  # type: ignore[arg-type]
    # An adapter reload may legitimately replace its own tool definition.
    registry.replace(make_tool(description="updated"))
    assert registry.require("navigate_to").description == "updated"


def test_registry_discovery_filters_namespace_safety_and_visibility():
    registry = ToolRegistry([
        make_tool(name="navigate_to"),
        make_tool(name="get_current_pose", safety_level=SafetyLevel.QUERY, idempotent=True),
        make_tool(name="move_arm_to_joints", safety_level=SafetyLevel.NORMAL_MOTION,
                  llm_visibility="fallback"),
        make_tool(name="emergency_stop", safety_level=SafetyLevel.SAFETY),
    ])
    assert [t.name for t in registry.select(namespace="navigate")] == ["navigate_to"]
    assert [t.name for t in registry.select(namespace="nonexistent")] == []
    assert [t.name for t in registry.select(max_safety_level=SafetyLevel.QUERY)] == ["get_current_pose"]
    assert "move_arm_to_joints" not in [t.name for t in registry.select(llm_only=True)]
    assert len(registry.select(llm_only=False)) == 4


def test_registry_exports_openai_tools_without_fallbacks():
    registry = ToolRegistry([make_tool(), make_tool(name="move_arm_to_joints", llm_visibility="fallback")])
    exported = registry.openai_tools()
    assert [item["function"]["name"] for item in exported] == ["navigate_to"]


def test_registry_capability_view_marks_unavailable_tools_with_blockers():
    registry = ToolRegistry([make_tool(), make_tool(name="home_arm", distributed_node="robot.arm")])
    infos = {item.name: item for item in registry.capability_infos(
        unavailable={"home_arm": ["HARDWARE_ERROR"]},
    )}
    assert infos["navigate_to"].available is True
    assert infos["home_arm"].available is False
    assert infos["home_arm"].blockers == ["HARDWARE_ERROR"]


# --------------------------------------------------------------------------
# Cross-language agreement
# --------------------------------------------------------------------------


def go_recovery_classes() -> dict[str, str]:
    """Parse the recovery class of every code classified in core/closedloop."""

    text = CLOSED_LOOP_GO.read_text()
    table = text[text.index("var classification = []struct {"):text.index("func set(")]
    starts = [m.start() for m in re.finditer(r"\{(Transient|Perception|Planning|Permission|Resource|Validation|UnknownOutcome|Fatal), set\(", table)]
    classes: dict[str, str] = {}
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(table)
        block = table[start:end]
        recovery = re.match(r"\{(\w+),", block).group(1)
        for code in re.findall(r'"([A-Z][A-Z0-9_]+)"', block):
            classes[code] = recovery
    return classes


def test_go_and_python_agree_on_retry_safety_for_every_code():
    """The two sides must not disagree about whether a failure may be retried.

    A code classified as retryable by one side and unknown by the other would
    let the Agent replay a physical action Python already refused, or refuse
    one Python considers safe.
    """

    go = {code: _screaming(recovery) for code, recovery in go_recovery_classes().items()}
    python = recovery_class_map()
    assert go, "failed to parse the Go classification table"
    assert set(go) <= set(python), f"codes only Go classifies: {sorted(set(go) - set(python))}"
    for code, recovery in go.items():
        assert python[code] == recovery, f"{code}: Go={recovery} Python={python[code]}"


def _screaming(name: str) -> str:
    """UnknownOutcome -> UNKNOWN_OUTCOME so Go and Python spellings compare."""

    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()


def test_every_recognised_code_maps_onto_a_standard_tool_error():
    for code in known_runtime_codes():
        error, recovery, recoverable = standard_error(code)
        assert error in set(ToolError)
        assert recovery in set(RecoveryClass)
        assert recoverable is (recovery in RECOVERABLE_CLASSES)
