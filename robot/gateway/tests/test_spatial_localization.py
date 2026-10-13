"""CPU contract tests: no hardware motion or ROS middleware is claimed here."""
from __future__ import annotations

import hashlib
import json
import time

import pytest
from tangying_robot_gateway.contracts import RobotProfile, validate_reconstruction
from tangying_robot_gateway.ros2_profile import Ros2ProfileBridge, navigation_state
from tangying_robot_gateway.spatial_localization import (
    SpatialNavigationDeployment,
    canonical_json,
    pose_reached,
    strict_json,
)

from .test_mobility_contract import profile
from .test_ros2_profile import Transport, command, driver_state


def volume_for(value):
    return {"schemaVersion": "navigation.volume.v1", "robotId": value["robotId"],
            "mapId": "survey-1", "mapRevision": "map-1", "frameId": "world", "worldFrameRevision": "world-1",
            "calibrationRevision": value["sensors"][0]["transformRevision"],
            "resolution": .25, "origin": [-2., -2., 0.], "shape": [16, 16, 8], "cells": [0] * 2048}


def policy():
    return {"mode": "volume3d", "bodyHalfExtents": [.05, .05, .05], "margin": .01,
            "maxExpanded": 10000, "maxWaypoints": 256}


def local_config():
    return {"sourceId": "fused-pose", "sourceType": "sensor_fusion", "sourceFrameId": "map",
            "maxAgeMs": 500, "maxPositionVarianceM2": .1, "maxOrientationVarianceRad2": .1}


def deployment(value=None):
    value = value or profile("uav")
    return SpatialNavigationDeployment(volume_for(value), policy(), local_config(), value)


def capture(bridge, sequence=1, pose=None, stamp=None):
    sensor = bridge.profile.sensors[0]
    scene = {"schemaVersion": "scene.reconstruction.v1", "robotId": bridge.profile.robot_id,
             "sourceId": sensor.source_id, "sourceType": sensor.source_type, "sourceFrameId": sensor.frame_id,
             "frameId": "world", "transformRevision": sensor.transform_revision,
             "observationId": f"ros-{sequence}", "sequence": sequence, "units": "m",
             "observedAtUnixMs": stamp or int(time.time() * 1000), "entities": [], "points": []}
    spatial = bridge.spatial_navigation
    vol = spatial.volume
    state = {"active_map": {"mapId": vol["mapId"], "mapRevision": vol["mapRevision"],
                            "calibrationRevision": vol["calibrationRevision"]},
             "spatial_localization": {"schemaVersion": "spatial.localization.v1", "robotId": bridge.profile.robot_id,
                 "sourceId": "fused-pose", "sourceType": "sensor_fusion", "sourceFrameId": "map",
                 "frameId": "world", "mapId": vol["mapId"], "mapRevision": vol["mapRevision"],
                 "worldFrameRevision": vol["worldFrameRevision"], "transformRevision": vol["calibrationRevision"],
                 "observationId": scene["observationId"], "observedAtUnixMs": scene["observedAtUnixMs"],
                 "sequence": sequence, "poseModel": bridge.profile.mobility.pose_model,
                 "pose": pose or [-.5, -.5, .5, 1., 0., 0., 0.], "linearUnit": "m", "angularUnit": "rad",
                 "quality": {"state": "localized", "positionVarianceM2": [.01, .01, .01],
                             "orientationVarianceRad2": [.01, .01, .01]}}}
    return scene, state


def publish(bridge, sequence=1, **kwargs):
    scene, state = capture(bridge, sequence, **kwargs)
    bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(scene), json.dumps(state))
    return scene, state


def make_spatial_bridge():
    transport = Transport()
    dep = deployment()
    bridge = Ros2ProfileBridge(profile("uav"), transport, commissioning_id="commission-1", spatial_navigation=dep)
    transport.bridge = bridge
    bridge.receive_state(driver_state(bridge, spatialNavigationRevision=dep.revision))
    publish(bridge)
    return bridge, transport


def good_result(bridge, goal, **updates):
    result = {"robot_id": goal["robot_id"], "profile_revision": goal["profile_revision"],
              "command_id": goal["command_id"], "success": True, "code": "OK", "message": "",
              "observation_id": "ros-2", "verification_confidence": 1.,
              "navigation_plan_id": json.loads(goal["navigation_plan_json"])["planId"]}
    return {**result, **updates}


def spatial_command(bridge):
    result = command(bridge)
    result.parameters["goalPose"] = [.5, .5, 1., 1., 0., 0., 0.]
    return result


def test_spatial_deployment_loads_relative_volume_and_pins_content(tmp_path):
    val = profile("uav")
    (tmp_path / "volume.json").write_text(json.dumps(volume_for(val)))
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps({"schemaVersion": "spatial.deployment.v1", "volumePath": "volume.json",
                              "policy": policy(), "localization": local_config()}))
    dep = SpatialNavigationDeployment.from_file(path, val)
    assert dep.revision == deployment().revision
    assert dep.volume == volume_for(val)
    changed = dep.volume
    changed["cells"][0] = 100
    assert dep.volume != changed
    assert SpatialNavigationDeployment(changed, policy(), local_config(), val).revision != dep.revision
    path.write_text(path.read_text().replace('"schemaVersion":', '"schemaVersion": "bad", "schemaVersion":'))
    with pytest.raises(ValueError, match="duplicate"):
        SpatialNavigationDeployment.from_file(path, val)


@pytest.mark.parametrize("kind", ["ugv", "legged"])
def test_ground_profile_cannot_select_flying_planner(kind):
    with pytest.raises(ValueError, match="air/se3"):
        deployment(profile(kind))


def test_localization_produces_same_capture_world_pose_and_complete_spatial_plan():
    bridge, transport = make_spatial_bridge()
    bridge.observe()
    state = bridge.state()
    assert state["base_pose_frame"] == "world"
    assert state["base_pose"] == state["spatial_localization"]["pose"]
    def execute(goal, tick):
        plan = json.loads(goal["navigation_plan_json"])
        bridge.spatial_navigation.verify_plan(plan, state["base_pose"], spatial_command(bridge).parameters["goalPose"], goal["command_id"])
        transport.goal = goal
        publish(bridge, 2, pose=plan["waypoints"][-1])
        tick()
        return good_result(bridge, goal)
    transport.execute = execute
    assert bridge.execute(spatial_command(bridge)).success
    plan = json.loads(transport.goal["navigation_plan_json"])
    assert plan["waypoints"][-1][2] == 1.
    assert plan["spatialNavigationRevision"] == bridge.spatial_navigation_revision
    assert plan["localizationObservationId"] == "ros-1"


@pytest.mark.parametrize("field,value", [
    ("robotId", "other"), ("sourceId", "other"), ("sourceType", "slam_pose"), ("sourceFrameId", "odom"),
    ("mapId", "other"), ("mapRevision", "other"), ("worldFrameRevision", "other"),
    ("transformRevision", "other"), ("frameId", "map"), ("observationId", "other"),
    ("observedAtUnixMs", 1), ("sequence", 9), ("poseModel", "se2"), ("linearUnit", "cm"),
    ("pose", [0., 0., 3., 1., 0., 0., 0.]), ("pose", [0., 0., 1., 2., 0., 0., 0.]),
])
def test_localization_wrong_capture_map_frame_source_or_pose_revokes_readiness(field, value):
    bridge, _ = make_spatial_bridge()
    scene, state = capture(bridge, 2)
    state["spatial_localization"][field] = value
    with pytest.raises(ValueError):
        bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(scene), json.dumps(state))
    assert not bridge.ready()


@pytest.mark.parametrize("quality", [
    {"state": "lost"}, {"state": "degraded"}, {"positionVarianceM2": [.01, .01, .2]},
    {"orientationVarianceRad2": [.2, .01, .01]}, {"positionVarianceM2": [.01, -.1, .01]},
    {"positionVarianceM2": [.01, float('nan'), .01]},
])
def test_localization_all_six_axes_quality_required(quality):
    bridge, _ = make_spatial_bridge()
    scene, state = capture(bridge, 2)
    state["spatial_localization"]["quality"].update(quality)
    with pytest.raises(ValueError):
        bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(scene), json.dumps(state))
    assert not bridge.ready()


def test_old_or_conflicting_base_pose_cannot_override_localization():
    bridge, _ = make_spatial_bridge()
    scene, state = capture(bridge, 2)
    state.update(base_pose=[0., 0., 0., 1., 0., 0., 0.], base_pose_frame="world")
    with pytest.raises(ValueError, match="disagree"):
        bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(scene), json.dumps(state))


def test_same_capture_rebroadcast_does_not_refresh_localization_monotonic_age():
    bridge, _ = make_spatial_bridge()
    scene, state = capture(bridge, 2)
    bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(scene), json.dumps(state))
    captured = bridge._captures['ros-2']
    bridge._captures['ros-2'] = (captured[0], captured[1] - 1, captured[2])
    bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(scene), json.dumps(state))
    assert not bridge.ready()


def test_spatial_controller_must_explicitly_admit_identical_deployment():
    bridge, _ = make_spatial_bridge()
    with pytest.raises(ValueError, match="deployment"):
        bridge.receive_state(driver_state(bridge, sequence=2))
    assert not bridge.ready()


def test_result_cannot_claim_success_without_acknowledging_exact_navigation_plan():
    bridge, transport = make_spatial_bridge()
    def execute(goal, tick):
        publish(bridge, 2, pose=spatial_command(bridge).parameters["goalPose"])
        return good_result(bridge, goal, navigation_plan_id="")
    transport.execute = execute
    with pytest.raises(ValueError, match="plan identity"):
        bridge.execute(spatial_command(bridge))


@pytest.mark.parametrize("wrong_pose", [[.5, .5, .5, 1., 0., 0., 0.], [.5, .5, 1., 0., 1., 0., 0.]])
def test_success_requires_measured_height_and_full_orientation(wrong_pose):
    bridge, transport = make_spatial_bridge()
    def execute(goal, tick):
        publish(bridge, 2, pose=wrong_pose)
        return good_result(bridge, goal)
    transport.execute = execute
    with pytest.raises(ValueError, match="measured xyz"):
        bridge.execute(spatial_command(bridge))


def test_spatial_localization_loss_during_motion_raises_into_runtime_stop_path():
    bridge, transport = make_spatial_bridge()
    def execute(goal, tick):
        scene, state = capture(bridge, 2)
        state["spatial_localization"]["quality"]["state"] = "lost"
        with pytest.raises(ValueError):
            bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(scene), json.dumps(state))
        tick()
    transport.execute = execute
    with pytest.raises(RuntimeError, match="readiness"):
        bridge.execute(spatial_command(bridge))


def test_catalog_pins_spatial_policy_and_not_transient_readiness():
    from tangying_robot_gateway.service import RobotRuntimeService
    bridge, _ = make_spatial_bridge()
    service = RobotRuntimeService(bridge.backend())
    revision = service.GetRuntimeInfo(None, None).catalog_revision
    bridge._state_received -= 1
    assert service.GetRuntimeInfo(None, None).catalog_revision == revision
    altered = policy()
    altered["margin"] = .02
    dep = SpatialNavigationDeployment(volume_for(profile("uav")), altered, local_config(), profile("uav"))
    second = Ros2ProfileBridge(profile("uav"), Transport(), commissioning_id="commission-1", spatial_navigation=dep)
    assert RobotRuntimeService(second.backend()).GetRuntimeInfo(None, None).catalog_revision != revision


def test_plan_tampering_and_waypoint_workspace_escape_are_rejected(monkeypatch):
    bridge, _ = make_spatial_bridge()
    scene, state = capture(bridge)
    local = bridge.spatial_navigation.localization(state["spatial_localization"], validate_reconstruction(scene, bridge.profile))
    goal = [.5, .5, 1., 1., 0., 0., 0.]
    plan, _ = bridge.spatial_navigation.plan(local, goal, "cmd-1")
    plan["waypoints"][-1][2] = .5
    with pytest.raises(ValueError, match="hash"):
        bridge.spatial_navigation.verify_plan(plan, local["pose"], goal, "cmd-1")
    plan["planId"] = hashlib.sha256(canonical_json({k: v for k, v in plan.items() if k != "planId"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="route"):
        bridge.spatial_navigation.verify_plan(plan, local["pose"], goal, "cmd-1")
    monkeypatch.setattr(bridge.spatial_navigation._planner, 'plan', lambda start, end: {"waypoints": [start, [5., 0., 1., 1., 0., 0., 0.], end]})
    with pytest.raises(ValueError):
        bridge.spatial_navigation.plan(local, goal, "cmd-2")


def test_return_to_start_legacy_world_pose_and_quaternion_sign_equivalence():
    value = navigation_state(json.dumps({"base_pose": [0, 0, 1, 1, 0, 0, 0]}), RobotProfile.model_validate(profile("uav")))
    assert value["base_pose_frame"] == "world"
    assert pose_reached([0, 0, 1, -1, 0, 0, 0], [0, 0, 1, 1, 0, 0, 0])
    assert not pose_reached([0, 0, 2, 1, 0, 0, 0], [0, 0, 1, 1, 0, 0, 0])
    with pytest.raises(ValueError, match="world frame"):
        navigation_state(json.dumps({**value, "base_pose_frame": "odom"}), RobotProfile.model_validate(profile("uav")))


def test_spatial_json_limits_duplicates_nonfinite_and_config_extra_fields():
    for value in ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}']:
        with pytest.raises(ValueError):
            strict_json(value, maximum=100)
    with pytest.raises(ValueError, match="budget"):
        strict_json('"123"', maximum=2)
    conf = local_config()
    conf['unexpected'] = True
    with pytest.raises(ValueError):
        SpatialNavigationDeployment(volume_for(profile("uav")), policy(), conf, profile("uav"))


def test_localization_fault_reaches_real_runtime_unknown_and_registered_stop():
    from google.protobuf.json_format import ParseDict
    from tangying_robot_gateway.service import RobotRuntimeService
    from tangying_robot_proto.robot.v1 import robot_pb2

    from .test_mobility_contract import nav_command

    bridge, transport = make_spatial_bridge()
    def execute(goal, tick):
        scene, state = capture(bridge, 2)
        state["spatial_localization"]["quality"]["state"] = "lost"
        with pytest.raises(ValueError):
            bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(scene), json.dumps(state))
        tick()
    transport.execute = execute
    service = RobotRuntimeService(bridge.backend())
    request = nav_command(service.GetRuntimeInfo(None, None))
    request.parameters.Clear()
    ParseDict({"goalPose": [.5, .5, 1., 1., 0., 0., 0.]}, request.parameters)
    terminal = list(service.execute_for_test(request))[-1]
    assert terminal.type == robot_pb2.SKILL_EVENT_SAFETY_STOPPED
    assert terminal.code == "EXECUTION_OUTCOME_UNKNOWN"
    assert transport.stop_request["procedure_id"] == bridge.profile.mobility.safe_stop.procedure_id
    assert bridge._stopped


def test_manifest_next_to_volume_cannot_be_ignored_to_load_tampered_package(tmp_path):
    value = profile("uav")
    (tmp_path / "volume.json").write_text(json.dumps(volume_for(value)))
    (tmp_path / "manifest.json").write_text('{}')
    config = tmp_path / "deployment.json"
    config.write_text(json.dumps({"schemaVersion": "spatial.deployment.v1", "volumePath": "volume.json",
                                 "policy": policy(), "localization": local_config()}))
    with pytest.raises(ValueError, match="manifest"):
        SpatialNavigationDeployment.from_file(config, value)


def test_localization_cannot_change_pose_under_repeated_capture_sequence():
    bridge, _ = make_spatial_bridge()
    scene, state = publish(bridge, 2)
    state["spatial_localization"]["pose"][2] = .75
    with pytest.raises(ValueError, match="sequence"):
        bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(scene), json.dumps(state))
    assert not bridge.ready()


def test_ground_se2_localization_rejects_roll_pitch_instead_of_projecting():
    import math

    value = profile("ugv")
    value["actionLimits"]["navigation.z"] = {"min": -1., "max": 1., "unit": "m"}
    vol = volume_for(value)
    vol['origin'][2] = -1.
    vol['terrain'] = {"schemaVersion": "navigation.terrain.v1", "sourceId": "survey",
                      "observedAtUnixMs": int(time.time() * 1000), "heights": [0.] * 256,
                      "supported": [True] * 256, "slopeRadians": [0.] * 256}
    config = {**policy(), "mode": "terrain25d", "maxSlopeRad": .3, "maxStepM": .1, "referenceHeightM": .2}
    dep = SpatialNavigationDeployment(vol, config, local_config(), value)
    transport = Transport()
    bridge = Ros2ProfileBridge(value, transport, commissioning_id="commission-1", spatial_navigation=dep)
    scene, state = capture(bridge, pose=[-.5, -.5, .2, math.cos(.1), math.sin(.1), 0., 0.])
    with pytest.raises(ValueError, match="roll/pitch"):
        bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(scene), json.dumps(state))


def test_planar_controller_cannot_receive_aerial_terrain_mode():
    value = profile('uav')
    config = {**policy(), "mode": "terrain25d", "maxSlopeRad": .3, "maxStepM": .1, "referenceHeightM": .2}
    with pytest.raises(ValueError, match="ground"):
        SpatialNavigationDeployment(volume_for(value), config, local_config(), value)


def test_wall_clock_stale_or_future_localization_cannot_enable_motion():
    bridge, _ = make_spatial_bridge()
    for stamp in (int(time.time() * 1000) - 600, int(time.time() * 1000) + 50):
        scene, state = capture(bridge, 2, stamp=stamp)
        with pytest.raises(ValueError, match="stale|future"):
            bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(scene), json.dumps(state))
        assert not bridge.ready()


def test_deployment_reader_refuses_fifo_symlink_and_oversized_regular_file(tmp_path):
    import os

    from tangying_robot_gateway.spatial_localization import _read_json

    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular"):
        _read_json(fifo, 64)
    regular = tmp_path / "regular.json"
    regular.write_text("{}")
    linked = tmp_path / "linked.json"
    linked.symlink_to(regular)
    with pytest.raises(OSError):
        _read_json(linked, 64)
    regular.write_bytes(b" " * 65)
    with pytest.raises(ValueError, match="bounded"):
        _read_json(regular, 64)


@pytest.mark.parametrize("actual_scale", [.99955, 1., 1.00045])
@pytest.mark.parametrize("goal_scale", [.99955, 1., 1.00045])
@pytest.mark.parametrize("sign", [-1, 1])
@pytest.mark.parametrize("angle,expected", [(0., True), (.099, True), (.100001, False), (.11, False)])
def test_arrival_angle_normalizes_both_quaternions_before_threshold(actual_scale, goal_scale, sign, angle, expected):
    import math

    actual = [0., 0., .5, sign * actual_scale * math.cos(angle / 2),
              sign * actual_scale * math.sin(angle / 2), 0., 0.]
    goal = [0., 0., .5, goal_scale, 0., 0., 0.]
    assert pose_reached(actual, goal) is expected
