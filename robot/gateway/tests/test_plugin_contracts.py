from __future__ import annotations

import copy
import time

import pytest


def arm_profile():
    return {
        "schemaVersion": "robot.profile.v1", "robotId": "arm-1",
        "adapterId": "example_arm", "adapterVersion": "1.0.0", "modelId": "six-axis-arm",
        "embodiment": "arm",
        "joints": [{"name": "axis1", "kind": "revolute", "unit": "rad", "lower": -2.0, "upper": 2.0}],
        "endEffectors": [{"id": "gripper", "kind": "gripper", "jointNames": ["axis1"]}],
        "sensors": [{"sourceId": "arm-1/depth", "sourceType": "rgbd_camera", "frameId": "depth_optical",
                     "transformRevision": "depth-world-cal-1", "maxAgeMs": 1000}],
        "actionLimits": {"axis1.position": {"min": -2.0, "max": 2.0, "unit": "rad"}},
        "tools": ["observe_scene", "arm.move", "manipulation.pick", "emergency_stop"],
    }


def scene(now_ms=None):
    return {
        "schemaVersion": "scene.reconstruction.v1", "robotId": "arm-1",
        "observationId": "depth-1", "sourceId": "arm-1/depth", "sourceType": "rgbd_camera",
        "sourceFrameId": "depth_optical", "frameId": "world", "transformRevision": "depth-world-cal-1",
        "observedAtUnixMs": now_ms or int(time.time() * 1000), "sequence": 1, "units": "m",
        "entities": [{"entityId": "cup-1", "category": "cup", "attributes": {"color": "red"},
                      "pose": [0.1, 0.2, 0.3, 1, 0, 0, 0], "confidence": 0.9, "relation": "on:table-1"}],
        "points": [[0.1, 0.2, 0.3], [0.2, 0.2, 0.3]],
    }


def test_contract_validates_world_scene_against_declared_sensor():
    from tangying_robot_gateway.contracts import validate_reconstruction

    value = validate_reconstruction(scene(10_000), arm_profile(), now_ms=10_500)
    assert value.entities[0].pose == [0.1, 0.2, 0.3, 1.0, 0.0, 0.0, 0.0]
    assert value.observed_at_unix_ms == 10_000


@pytest.mark.parametrize("mutate", [
    lambda x: x.update(robotId="other-robot"),
    lambda x: x.update(sourceId="unknown/sensor"),
    lambda x: x.update(sourceType="lidar"),
    lambda x: x.update(sourceFrameId="unregistered-camera"),
    lambda x: x.update(frameId="camera"),
    lambda x: x.update(units="mm"),
    lambda x: x.update(transformRevision="old-calibration"),
    lambda x: x.update(observedAtUnixMs=8999),
    lambda x: x.update(observedAtUnixMs=10_251),
    lambda x: x.update(sequence=0),
    lambda x: x["entities"][0].update(pose=[0, 0, 0, 2, 0, 0, 0]),
    lambda x: x["entities"][0].update(pose=[0, 0, float("nan"), 1, 0, 0, 0]),
    lambda x: x["entities"][0].update(confidence=1.1),
    lambda x: x["entities"].append(copy.deepcopy(x["entities"][0])),
    lambda x: x.update(points=[[0, 0, 0]] * 4097),
    lambda x: x.update(points=[[0, float("inf"), 0]]),
    lambda x: x.update(sequence=True),
])
def test_contract_rejects_untrustworthy_scene(mutate):
    from tangying_robot_gateway.contracts import validate_reconstruction

    value = scene(10_000)
    mutate(value)
    with pytest.raises(ValueError):
        validate_reconstruction(value, arm_profile(), now_ms=10_000)


@pytest.mark.parametrize("mutate", [
    lambda x: x["joints"][0].update(lower=3),
    lambda x: x["joints"][0].update(unit="m"),
    lambda x: x["endEffectors"][0].update(jointNames=["missing-joint"]),
    lambda x: x["sensors"][0].update(maxAgeMs=60_001),
    lambda x: x["sensors"].append(copy.deepcopy(x["sensors"][0])),
    lambda x: x["actionLimits"]["axis1.position"].update(min=float("nan")),
    lambda x: x["tools"].append("shell.execute"),
    lambda x: x["tools"].remove("emergency_stop"),
])
def test_profile_rejects_unsafe_or_inconsistent_hardware(mutate):
    from tangying_robot_gateway.contracts import RobotProfile

    value = arm_profile()
    mutate(value)
    with pytest.raises(ValueError):
        RobotProfile.model_validate(value)


def test_empty_scene_is_valid_but_does_not_fabricate_entities():
    from tangying_robot_gateway.contracts import validate_reconstruction

    value = scene(10_000)
    value["entities"], value["points"] = [], []
    assert validate_reconstruction(value, arm_profile(), now_ms=10_000).entities == []


def test_navigation_requires_profile_workspace_and_rejects_outside_goal():
    from tangying_robot_gateway.contracts import RobotProfile, validate_tool_parameters

    value = arm_profile()
    value["tools"].append("navigation.navigate")
    with pytest.raises(ValueError):
        RobotProfile.model_validate(value)
    for axis in "xyz":
        value["actionLimits"][f"navigation.{axis}"] = {"min": -1.0, "max": 1.0, "unit": "m"}
    profile = RobotProfile.model_validate(value)
    validate_tool_parameters("navigation.navigate", {"goalPose": [0, 0, 0, 1, 0, 0, 0]}, profile)
    with pytest.raises(ValueError, match="workspace"):
        validate_tool_parameters("navigation.navigate", {"goalPose": [2, 0, 0, 1, 0, 0, 0]}, profile)


@pytest.mark.parametrize("value", [1.5, True, "1"])
def test_observe_rate_cannot_coerce_fraction_boolean_or_text(value):
    from tangying_robot_gateway.contracts import RobotProfile, validate_tool_parameters

    with pytest.raises(ValueError):
        validate_tool_parameters("observe_scene", {"max_rate_hz": value}, RobotProfile.model_validate(arm_profile()))


@pytest.mark.parametrize("attributes", [
    {"": "red"}, {"color name": "red"}, {"x" * 257: "red"}, {"color": "x" * 1025},
])
def test_entity_attribute_bounds_match_the_go_world_boundary(attributes):
    from tangying_robot_gateway.contracts import validate_reconstruction

    value = scene(10_000)
    value["entities"][0]["attributes"] = attributes
    with pytest.raises(ValueError):
        validate_reconstruction(value, arm_profile(), now_ms=10_000)


def test_mutated_pydantic_instances_are_revalidated_at_the_boundary():
    from tangying_robot_gateway.contracts import (
        Reconstruction,
        RobotProfile,
        validate_reconstruction,
    )

    value = Reconstruction.model_validate(scene(10_000))
    profile = RobotProfile.model_validate(arm_profile())
    value.entities[0].pose[3] = 2.0
    with pytest.raises(ValueError):
        validate_reconstruction(value, profile, now_ms=10_000)
    value.entities[0].pose[3] = 1.0
    profile.sensors.clear()
    with pytest.raises(ValueError):
        validate_reconstruction(value, profile, now_ms=10_000)


@pytest.mark.parametrize("framework,artifact,source,allowed", [
    ("vla", "a" * 64, "rgbd_camera", True),
    ("imitation", "a" * 64, "rgbd_camera", True),
    ("reinforcement", "a" * 64, "rgbd_camera", True),
    ("vla", "deterministic:example", "rgbd_camera", False),
    ("deterministic", "deterministic:example", "rgbd_camera", False),
    ("deterministic", "a" * 64, "sim_ground_truth", False),
    ("deterministic", "deterministic:example", "sim_ground_truth", True),
])
def test_policy_artifact_identity_and_simulation_boundary(framework, artifact, source, allowed):
    from tangying_robot_gateway.contracts import RobotProfile, validate_tool_parameters

    profile = arm_profile()
    profile["sensors"][0]["sourceType"] = source
    profile = RobotProfile.model_validate(profile)
    parameters = {"action_chunk": [{"axis1.position": 0.5}], "policy_execution": {
        "policyId": "policy-1", "policyVersion": "1.0.0", "framework": framework,
        "artifactSha256": artifact, "manifestRevision": "b" * 64, "inferenceId": "inference-1",
        "observationId": "obs-1", "elapsedMs": 1.0,
    }}
    if allowed:
        validate_tool_parameters("arm.move", parameters, profile)
    else:
        with pytest.raises(ValueError):
            validate_tool_parameters("arm.move", parameters, profile)
