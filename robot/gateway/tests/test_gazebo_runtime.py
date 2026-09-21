"""The runtime contract assembled from Gazebo, without Gazebo or gRPC.

The split this file tests is the point of the module: everything that can be wrong
in a way no downstream check would notice lives here as a pure function, and the
node that subscribes and serves is thin enough to hold almost no judgement.
"""

from __future__ import annotations

import numpy as np
import pytest
from tangying_robot_gateway.gazebo_bridge import intrinsics_from_field_of_view
from tangying_robot_gateway.gazebo_runtime import (
    CameraSample,
    GazeboRuntime,
    GazeboRuntimeError,
)

#: The world's own sensor: 320x240 at a 1.25 rad horizontal field of view.
FOV = 1.25


def a_sample(**changes) -> CameraSample:
    values = {
        "width": 4, "height": 3,
        "rgb": np.zeros((3, 4, 3), dtype=np.uint8),
        "depth_metres": np.full((3, 4), 2.0),
        "horizontal_fov_rad": FOV,
        "world_from_camera_link": np.eye(4),
        "captured_at_unix_ms": 1_700_000_000_000,
    }
    return CameraSample(**(values | changes))


def a_runtime(**changes) -> GazeboRuntime:
    values = {
        "robot_id": "gazebo-house-rgbd", "adapter": "gazebo",
        "cameras": {"base-rgbd": "/camera/base", "head-rgbd": "/camera/head"},
        "calibration_revision": "c" * 64, "software_version": "0.6.0",
    }
    return GazeboRuntime(**(values | changes))


def a_runtime_with_pose(**changes) -> GazeboRuntime:
    runtime = a_runtime(**changes)
    runtime.record_base_pose(np.eye(4))
    return runtime


def test_a_capture_arrives_as_the_runtimes_own_rgbd_frame():
    runtime = a_runtime_with_pose()
    runtime.record("base-rgbd", a_sample())
    payload = runtime.rgbd_payload("base-rgbd")
    assert payload["width"] == 4 and payload["height"] == 3
    assert len(payload["rgb"]) == 4 * 3 * 3
    assert len(payload["depth_metres_f32"]) == 4 * 3 * 4
    assert len(payload["base_from_camera"]) == 16
    # The intrinsics come from the declared field of view, not from a constant.
    expected = intrinsics_from_field_of_view(4, 3, FOV)
    np.testing.assert_allclose(np.asarray(payload["intrinsics"]).reshape(3, 3), expected)


def test_the_capture_is_attached_to_the_robot_not_to_the_world():
    runtime = a_runtime_with_pose()
    pose = np.eye(4)
    pose[:3, 3] = [5.0, -2.0, 0.0]
    runtime.record_base_pose(pose)
    camera = np.eye(4)
    camera[:3, 3] = [5.0, -2.0, 0.4]
    runtime.record("base-rgbd", a_sample(world_from_camera_link=camera))
    transform = np.asarray(runtime.rgbd_payload("base-rgbd")["base_from_camera"]).reshape(4, 4)
    np.testing.assert_allclose(transform[:3, 3], [0.0, 0.0, 0.4], atol=1e-12)


def test_a_capture_before_the_robot_has_a_pose_is_refused():
    """Attaching geometry to a robot whose position is unknown would put a map in
    a frame nothing else shares."""
    runtime = a_runtime()
    with pytest.raises(GazeboRuntimeError) as failure:
        runtime.record("base-rgbd", a_sample())
    assert failure.value.code == "NO_BASE_POSE"


def test_a_malformed_capture_is_refused_at_the_door_not_on_the_way_out():
    runtime = a_runtime_with_pose()
    with pytest.raises(Exception) as failure:
        runtime.record("base-rgbd", a_sample(depth_metres=np.zeros((3, 4), dtype=np.uint16)))
    assert getattr(failure.value, "code", "") == "INVALID_DEPTH"
    # And nothing was stored: a refused capture must not become the newest one.
    with pytest.raises(GazeboRuntimeError) as failure:
        runtime.rgbd_payload("base-rgbd")
    assert failure.value.code == "NO_CAPTURE"


def test_a_camera_the_runtime_does_not_declare_is_named():
    runtime = a_runtime_with_pose()
    with pytest.raises(GazeboRuntimeError) as failure:
        runtime.record("elbow-cam", a_sample())
    assert failure.value.code == "UNKNOWN_CAMERA"
    assert "base-rgbd" in failure.value.message


def test_cameras_and_calibration_are_required_at_construction():
    with pytest.raises(GazeboRuntimeError) as failure:
        a_runtime(cameras={})
    assert failure.value.code == "CAMERAS_REQUIRED"
    with pytest.raises(GazeboRuntimeError) as failure:
        a_runtime(calibration_revision="")
    assert failure.value.code == "CALIBRATION_REVISION_REQUIRED"


def test_runtime_info_derives_readiness_rather_than_asserting_it():
    """A runtime with no skills, or one that is estopped, is not ready to
    manipulate. Saying otherwise invites a client to plan against a robot that
    cannot move."""
    runtime = a_runtime()
    idle = runtime.runtime_info(skills=[])
    assert idle["manipulation_ready"] is False
    assert "NO_SKILLS_DECLARED" in idle["blockers"]

    ready = runtime.runtime_info(skills=["manipulation.pick", "observe_scene"])
    assert ready["manipulation_ready"] is True
    assert ready["skills"] == ["manipulation.pick", "observe_scene"]
    assert ready["cameras"] == ["base-rgbd", "head-rgbd"]

    stopped = runtime.runtime_info(skills=["manipulation.pick"], estopped=True)
    assert stopped["manipulation_ready"] is False
    assert stopped["blockers"][0] == "EMERGENCY_STOP_LATCHED"


def test_two_runtimes_with_the_same_skills_but_different_calibration_differ():
    """A catalog revision that hashed only skill names would call these the same
    runtime, and a client pinning it would not notice the robot had been
    recalibrated underneath it."""
    first = a_runtime().catalog_revision(["manipulation.pick"])
    second = a_runtime(calibration_revision="d" * 64).catalog_revision(["manipulation.pick"])
    assert first != second
    assert first == a_runtime().catalog_revision(["manipulation.pick"])


def test_an_observation_carries_the_metric_payload_only_when_asked():
    """A client that asked for entities and received a full metric RGB-D payload
    has been sent data it did not ask for, and on a slow link that is the
    difference between a live robot and a stalled one."""
    runtime = a_runtime_with_pose()
    runtime.record("head-rgbd", a_sample())
    lean = runtime.observation("head-rgbd", observation_id="obs-1", streams=["robot_state"])
    assert "rgbd_frame" not in lean
    assert lean["robot_state"]["calibration_revision"] == "c" * 64
    assert lean["monotonic_time_ns"] > 0

    rich = runtime.observation("head-rgbd", observation_id="obs-2", streams=["rgbd_raw"])
    assert rich["rgbd_frame"]["width"] == 4


def test_an_unknown_stream_is_named_rather_than_ignored():
    runtime = a_runtime_with_pose()
    runtime.record("head-rgbd", a_sample())
    with pytest.raises(GazeboRuntimeError) as failure:
        runtime.observation("head-rgbd", observation_id="obs", streams=["entities", "thermal"])
    assert failure.value.code == "UNKNOWN_STREAM"
    assert "thermal" in failure.value.message


def test_the_newest_capture_is_the_one_served():
    runtime = a_runtime_with_pose()
    runtime.record("base-rgbd", a_sample(captured_at_unix_ms=1_700_000_000_000))
    runtime.record("base-rgbd", a_sample(captured_at_unix_ms=1_700_000_000_100))
    observation = runtime.observation("base-rgbd", observation_id="obs")
    assert observation["wall_time_unix_ms"] == 1_700_000_000_100


def test_the_optical_convention_survives_the_whole_assembly():
    """End to end through the assembly: a level camera half a metre up, looking
    forward, must attach its capture with optical z along the robot's forward
    axis. This is the check that would have caught the 120.5-degree head camera
    before it shipped."""
    runtime = a_runtime_with_pose()
    # A level, forward-looking camera *is* the identity in the link convention:
    # REP-103 already says the link's x points forward. Setting the rotation to
    # anything else here would be describing a camera aimed somewhere else and
    # then asking the conversion to undo it.
    camera = np.eye(4)
    camera[:3, 3] = [0.0, 0.0, 0.5]
    runtime.record("base-rgbd", a_sample(world_from_camera_link=camera))
    transform = np.asarray(runtime.rgbd_payload("base-rgbd")["base_from_camera"]).reshape(4, 4)
    # Optical forward maps onto the robot's forward axis, and the camera sits half
    # a metre up: anything else means the capture is aimed somewhere it is not.
    np.testing.assert_allclose(transform[:3, :3] @ [0.0, 0.0, 1.0], [1.0, 0.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(transform[:3, 3], [0.0, 0.0, 0.5], atol=1e-12)


# -- the pose contract -------------------------------------------------------
#
# The mapping workflow derives every heading it plans with from
# `robot_state.base_pose`, through `pose_se2`, which accepts nothing but a
# 7-element normalized level-base quaternion. These tests exist because the
# runtime published `[x, y, z]` for two rounds and nothing caught it: the payload
# was well-formed, and only a *second* system (the survey loop) reads element 2 as
# nothing at all.


def test_the_base_pose_is_the_seven_element_pose_its_consumers_require():
    from tangying_robot_gateway.geometry import pose_se2

    runtime = a_runtime_with_pose()
    runtime.record("base-rgbd", a_sample())
    pose = runtime.observation("base-rgbd", observation_id="obs")["robot_state"]["base_pose"]
    assert len(pose) == 7
    # The point of the shape: it is exactly what the planner's own conversion takes.
    np.testing.assert_allclose(pose_se2(pose), [0.0, 0.0, 0.0], atol=1e-12)


def test_a_turned_robot_reports_its_heading_not_zero():
    """A 3-element pose is not a shorter pose. If it were padded, a robot that had
    turned 90 degrees would report heading zero - and a survey that cannot see its
    own heading plans turns it has already made."""
    runtime = a_runtime_with_pose()
    yaw = np.pi / 2
    pose = np.eye(4)
    pose[:3, :3] = [[np.cos(yaw), -np.sin(yaw), 0.0],
                    [np.sin(yaw), np.cos(yaw), 0.0],
                    [0.0, 0.0, 1.0]]
    pose[:3, 3] = [1.5, -2.5, 0.0]
    runtime.record_base_pose(pose)
    runtime.record("base-rgbd", a_sample())
    reported = runtime.observation("base-rgbd", observation_id="obs")["robot_state"]["base_pose"]
    np.testing.assert_allclose(reported[:3], [1.5, -2.5, 0.0], atol=1e-12)
    from tangying_robot_gateway.geometry import pose_se2

    np.testing.assert_allclose(pose_se2(reported)[2], yaw, atol=1e-12)


def test_a_tilted_base_is_refused_rather_than_flattened():
    """Flattening a tilted pose produces a plausible planar pose that is wrong in a
    way no downstream check can detect, so the runtime refuses instead."""
    runtime = a_runtime_with_pose()
    pitch = 0.4
    pose = np.eye(4)
    pose[:3, :3] = [[np.cos(pitch), 0.0, np.sin(pitch)],
                    [0.0, 1.0, 0.0],
                    [-np.sin(pitch), 0.0, np.cos(pitch)]]
    runtime.record_base_pose(pose)
    runtime.record("base-rgbd", a_sample())
    with pytest.raises(GazeboRuntimeError) as failure:
        runtime.observation("base-rgbd", observation_id="obs")
    assert failure.value.code == "BASE_NOT_LEVEL"


def test_a_pose_before_odometry_is_empty_rather_than_a_default():
    """Empty says "not known"; a default would say "at the origin", and a survey
    that starts from a pose the robot never measured plans its first step from the
    wrong place."""
    runtime = a_runtime()
    runtime._samples["base-rgbd"] = a_sample()
    payload = runtime.observation("base-rgbd", observation_id="obs", streams=["robot_state"])
    assert payload["robot_state"]["base_pose"] == []
