import time

import numpy as np
import pytest
from tangying_navigation.contracts import (
    ContractError,
    decode_capture,
    localized_goal_error,
    matrix_quaternion,
    quaternion_matrix,
    validate_goal,
    visual_quality,
)
from tangying_robot_proto.robot.v1.robot_pb2 import Observation, RGBDFrame


def test_pose_confirmation_uses_fresh_true_map_goal_and_wrapped_yaw():
    world = {"ready": True, "poseSource": "rtabmap_tf", "localizationState": "LOCALIZED",
             "poseObservedAtUnixMs": 1000, "mapPose": [0, 0, 0, 1, 0, 0, 0]}
    assert localized_goal_error(world, [.01, 0, 0, -1, 0, 0, 0], 1001) == (.01, 0)
    for change in ({"ready": False}, {"poseObservedAtUnixMs": 0},
                   {"poseSource": "ground_truth"}, {"localizationState": "UNAVAILABLE"},
                   {"mapPose": [float("nan"), 0, 0, 1, 0, 0, 0]}):
        with pytest.raises(ContractError):
            localized_goal_error({**world, **change}, [0, 0, 0, 1, 0, 0, 0], 1001)


def test_transient_depth_map_without_actual_visual_dictionary_is_not_slam_ready():
    assert not visual_quality({})["ready"]
    assert not visual_quality(
        {"Keypoint/Current_frame/words": 155, "Keypoint/Dictionary_size/words": 0}
    )["ready"]
    assert not visual_quality(
        {"Keypoint/Current_frame/words": float("nan"), "Keypoint/Dictionary_size/words": 155}
    )["ready"]
    assert visual_quality(
        {"Keypoint/Current_frame/words": 155, "Keypoint/Dictionary_size/words": 147}
    )["ready"]


def capture():
    return Observation(
        observation_id="source/capture-1",
        wall_time_unix_ms=int(time.time() * 1000),
        robot_state={"base_pose": [0, 0, 0, 1, 0, 0, 0]},
        rgbd_frame=RGBDFrame(
            width=2,
            height=1,
            rgb=bytes([255, 0, 1, 0, 127, 250]),
            depth_metres_f32=np.array([1.0, np.nan], dtype="<f4").tobytes(),
            intrinsics=[100, 0, 1, 0, 100, 0.5, 0, 0, 1],
            base_from_camera=np.eye(4).reshape(-1).tolist(),
        ),
    )


def test_raw_depth_remains_metric_and_missing_returns_unknown():
    observed = capture()
    result = decode_capture(observed, observed.wall_time_unix_ms)
    assert result.rgb == observed.rgbd_frame.rgb
    values = np.frombuffer(result.depth, dtype="<f4")
    assert values[0] == 1.0 and np.isnan(values[1])
    assert result.base_pose == (0, 0, 0, 1, 0, 0, 0)
    assert result.self_filter_model_revision == ""
    assert result.robot_self_mask == b""


def test_robot_first_return_mask_only_removes_marked_measurements_as_unknown():
    observed = capture()
    observed.rgbd_frame.depth_metres_f32 = np.array([1.0, 0.4], dtype="<f4").tobytes()
    observed.rgbd_frame.robot_self_mask = bytes([1, 0])
    observed.rgbd_frame.self_filter_model_revision = "robot-cad-sha256-v1"
    result = decode_capture(observed, observed.wall_time_unix_ms)
    metric = np.frombuffer(result.depth, dtype="<f4")
    assert np.isnan(metric[0]) and metric[1] == np.float32(0.4)
    assert result.rgb == observed.rgbd_frame.rgb  # Original preview is immutable.
    assert result.robot_self_mask == bytes([1, 0])
    assert result.self_filter_model_revision == "robot-cad-sha256-v1"


@pytest.mark.parametrize(
    ("mask", "revision"),
    [(b"\x01", "v1"), (bytes([2, 0]), "v1"), (bytes([1, 0]), ""), (bytes([0, 0]), " ")],
)
def test_robot_mask_malformed_or_unattributed_fails_closed(mask, revision):
    observed = capture()
    observed.rgbd_frame.robot_self_mask = mask
    observed.rgbd_frame.self_filter_model_revision = revision
    with pytest.raises(ContractError, match="self mask"):
        decode_capture(observed, observed.wall_time_unix_ms)


@pytest.mark.parametrize("kind", ["rgb", "depth", "stale", "transform", "intrinsics", "pose"])
def test_raw_invalid_capture_rejected(kind):
    observed = capture()
    if kind == "rgb":
        observed.rgbd_frame.rgb = b"x"
    if kind == "depth":
        observed.rgbd_frame.depth_metres_f32 = b"x"
    if kind == "stale":
        observed.wall_time_unix_ms -= 10000
    if kind == "transform":
        observed.rgbd_frame.base_from_camera[0] = 2
    if kind == "intrinsics":
        observed.rgbd_frame.intrinsics[0] = 0
    if kind == "pose":
        observed.robot_state.Clear()
    with pytest.raises(ContractError):
        decode_capture(observed, int(time.time() * 1000))


def test_ros_flu_orientation_preserved_without_extra_quarter_turn():
    rotation = quaternion_matrix([2**-0.5, 0, 0, 2**-0.5])
    np.testing.assert_allclose(rotation @ [1, 0, 0], [0, 1, 0], atol=1e-8)
    np.testing.assert_allclose(matrix_quaternion(rotation), [2**-0.5, 0, 0, 2**-0.5], atol=1e-8)
    for quaternion in ([1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]):
        np.testing.assert_allclose(
            quaternion_matrix(matrix_quaternion(quaternion_matrix(quaternion))),
            quaternion_matrix(quaternion),
            atol=1e-8,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"goalPose": [0, 0, 0, 1, 0, 0, False]},
        {"goalPose": [0, 0, 0, 2, 0, 0, 0]},
        {"goalPose": [0, 0, 0, float("nan"), 0, 0, 0]},
        {"frameId": "world"},
        {"commandId": "x\nHeader: bad"},
        {"unexpected": True},
    ],
)
def test_goal_rejects_malformed_or_ambiguous_frames(change):
    with pytest.raises(ContractError):
        validate_goal(
            {
                "commandId": "cmd-1",
                "goalPose": [0, 0.05, 0, 1, 0, 0, 0],
                "frameId": "odom",
                **change,
            }
        )


def test_stamped_velocity_gate_rejects_delayed_future_and_previous_goal_messages():
    from tangying_navigation.contracts import VelocityGate

    gate = VelocityGate()
    gate.accept("first", 1000)
    assert gate.observe("first", [0.02, 0.0, 0.0], 1100, 1100)
    assert gate.read("first")["stampUnixMs"] == 1100
    assert not gate.observe("first", [0.02, 0.0, 0.0], 1100, 1600)
    assert gate.read("first")["stampUnixMs"] == 1100
    gate.clear("first")
    gate.accept("second", 1700)
    assert not gate.observe("first", [0.02, 0.0, 0.0], 1750, 1750)
    assert not gate.observe("second", [0.02, 0.0, 0.0], 1600, 1750)
    assert not gate.observe("second", [0.02, 0.0, 0.0], 2100, 1750)
    assert gate.read("second")["stampUnixMs"] == 0
    assert gate.observe("second", [0.01, 0.0, 0.0], 1760, 1770)
    assert gate.read("second")["stampUnixMs"] == 1760
