from __future__ import annotations

import dataclasses
import time

import numpy as np
import pytest


def profile():
    return {
        "schemaVersion": "robot.profile.v1", "robotId": "camera-robot",
        "adapterId": "ros-rgbd", "adapterVersion": "1", "modelId": "rgbd-rig",
        "embodiment": "sensor_rig", "joints": [], "endEffectors": [],
        "sensors": [{"sourceId": "front-rgbd", "sourceType": "rgbd_camera",
                     "frameId": "camera_optical", "transformRevision": "calibration-1",
                     "maxAgeMs": 2000}],
        "actionLimits": {}, "tools": ["observe_scene", "emergency_stop"],
    }


def packets(stamp_ns=None, *, depth_encoding="16UC1", bigendian=False):
    from tangying_robot_gateway.ros_rgbd import CameraInfoPacket, ImagePacket

    stamp_ns = stamp_ns or time.time_ns() - 20_000_000
    color = ImagePacket(stamp_ns, "camera_optical", 2, 2, "bgr8", 8, False,
                        bytes([0, 0, 255, 0, 255, 0, 99, 99] * 2))
    dtype = (">" if bigendian else "<") + ("u2" if depth_encoding == "16UC1" else "f4")
    values = [[1000, 2000], [0, 3000]] if depth_encoding == "16UC1" else [[1, 2], [0, 3]]
    depth = ImagePacket(stamp_ns, "camera_optical", 2, 2, depth_encoding,
                        2 * np.dtype(dtype).itemsize, bigendian,
                        np.asarray(values, dtype=dtype).tobytes())
    info = CameraInfoPacket(stamp_ns, "camera_optical", 2, 2,
                            (2., 0., 0., 0., 2., 0., 0., 0., 1.), ())
    return color, depth, info


def source(*, transform=None, **kwargs):
    from tangying_robot_gateway.ros_rgbd import RosRgbdInput, WorldTransform

    def world_transform(frame_id, stamp_ns):
        matrix = np.eye(4)
        matrix[:3, 3] = [1., 2., 3.]
        return WorldTransform(stamp_ns, frame_id, "world", "calibration-1", matrix)

    return RosRgbdInput(profile(), "front-rgbd", transform_lookup=transform or world_transform,
                        **kwargs)


def submit(input_source, values):
    color, depth, info = values
    input_source.push_color(color)
    input_source.push_depth(depth)
    input_source.push_camera_info(info)


@pytest.mark.parametrize("encoding,bigendian", [("16UC1", False), ("16UC1", True), ("32FC1", False)])
def test_ros_camera_decodes_padded_rgb_and_depth_in_metres_without_retimestamp(encoding, bigendian):
    input_source = source()
    color, depth, info = packets(depth_encoding=encoding, bigendian=bigendian)
    submit(input_source, (color, depth, info))
    frame = input_source.read()
    np.testing.assert_array_equal(frame.rgb[0], [[255, 0, 0], [0, 255, 0]])
    np.testing.assert_allclose(frame.depth_m, [[1., 2.], [0., 3.]])
    np.testing.assert_allclose(frame.world_from_camera[:3, 3], [1., 2., 3.])
    assert frame.captured_at_unix_ms == color.stamp_ns // 1_000_000
    assert frame.sequence == input_source.read().sequence
    assert frame.source_id == "front-rgbd"


def test_ros_capture_queries_tf_at_depth_capture_stamp_and_preserves_sequence():
    from tangying_robot_gateway.ros_rgbd import WorldTransform

    values = packets()
    seen = []

    def lookup(frame_id, stamp_ns):
        seen.append((frame_id, stamp_ns))
        return WorldTransform(stamp_ns, frame_id, "world", "calibration-1", np.eye(4))

    input_source = source(transform=lookup)
    submit(input_source, values)
    first = input_source.read()
    second = input_source.read()
    assert seen == [("camera_optical", values[1].stamp_ns)]
    assert first.sequence == second.sequence
    submit(input_source, packets(values[0].stamp_ns + 1_000_000))
    assert input_source.read().sequence > first.sequence


@pytest.mark.parametrize("field,value", [
    ("frame_id", "another_camera"), ("step", 1), ("data", b"bad"),
    ("encoding", "jpeg"), ("width", 8193), ("stamp_ns", 0),
])
def test_malformed_or_wrong_camera_frame_is_rejected(field, value):
    input_source = source()
    color, _, _ = packets()
    with pytest.raises(ValueError):
        input_source.push_color(dataclasses.replace(color, **{field: value}))


def test_unsynchronized_frames_never_become_a_fresh_observation():
    input_source = source()
    color, depth, info = packets()
    submit(input_source, (color, dataclasses.replace(depth, stamp_ns=depth.stamp_ns-100_000_000), info))
    with pytest.raises(ValueError, match="synchronized"):
        input_source.read()


def test_stale_frame_and_missing_camera_info_are_not_relabelled_as_current():
    input_source = source()
    color, _depth, _info = packets(time.time_ns() - 3_000_000_000)
    with pytest.raises(ValueError, match="stale"):
        input_source.push_color(color)
    color, depth, _info = packets()
    input_source.push_color(color)
    input_source.push_depth(depth)
    with pytest.raises(ValueError, match="synchronized"):
        input_source.read()


@pytest.mark.parametrize("change", [
    {"target_frame_id": "base_link"}, {"source_frame_id": "wrong"},
    {"transform_revision": "uncalibrated"}, {"stamp_ns": 1},
    {"matrix": np.diag([2., 1., 1., 1.])},
])
def test_wrong_or_nonrigid_tf_blocks_perception(change):
    from tangying_robot_gateway.ros_rgbd import WorldTransform

    def lookup(frame_id, stamp_ns):
        return dataclasses.replace(WorldTransform(stamp_ns, frame_id, "world", "calibration-1",
                                                  np.eye(4)), **change)

    input_source = source(transform=lookup)
    submit(input_source, packets())
    with pytest.raises(ValueError):
        input_source.read()


def test_camera_info_rejects_uncorrected_distortion_and_intrinsic_changes():
    input_source = source()
    color, depth, info = packets()
    with pytest.raises(ValueError, match="distortion"):
        input_source.push_camera_info(dataclasses.replace(info, d=(0.1, 0., 0., 0., 0.)))
    submit(input_source, (color, depth, info))
    input_source.read()
    changed = dataclasses.replace(info, stamp_ns=info.stamp_ns + 1_000_000,
                                  k=(3., 0., 0., 0., 2., 0., 0., 0., 1.))
    with pytest.raises(ValueError, match="calibration"):
        input_source.push_camera_info(changed)


def test_packet_snapshots_cannot_be_mutated_after_acceptance():
    input_source = source()
    color, depth, info = packets()
    mutable = bytearray(color.data)
    input_source.push_color(dataclasses.replace(color, data=mutable))
    mutable[:] = bytes(len(mutable))
    input_source.push_depth(depth)
    input_source.push_camera_info(info)
    frame = input_source.read()
    assert frame.rgb[0, 0, 0] == 255
    with pytest.raises(ValueError):
        frame.rgb[0, 0, 0] = 1


def test_missing_sensor_and_ros_sim_clock_are_rejected_at_construction():
    from tangying_robot_gateway.ros_rgbd import RosRgbdInput

    with pytest.raises(ValueError, match="source"):
        RosRgbdInput(profile(), "missing", transform_lookup=lambda *args: None)
    with pytest.raises(ValueError, match="Unix"):
        source(clock_domain="ros_sim")


def test_sensor_backend_uses_rgbd_provider_and_never_advertises_motion():
    from tangying_robot_gateway.rgbd import RgbdPerception
    from tangying_robot_gateway.ros_rgbd import create_rgbd_backend
    from tangying_robot_gateway.runtime import ObservationRequest

    input_source = source()
    submit(input_source, packets())
    stopped = []
    backend = create_rgbd_backend(profile(), input_source, RgbdPerception(lambda frame: []),
                                  stop=stopped.append)
    info = backend.capabilities()
    assert not info.manipulation_ready
    assert {item.name for item in info.capabilities if item.available} == {"observe_scene", "emergency_stop"}
    observation = backend.observe(ObservationRequest())
    assert observation.reconstruction["sourceType"] == "rgbd_camera"
    assert observation.reconstruction["entities"] == []
    assert len(observation.reconstruction["points"]) == 3
    backend.stop("OPERATOR_STOP")
    assert stopped == ["OPERATOR_STOP"]


def test_input_fault_during_tf_lookup_cannot_publish_the_inflight_frame():
    from tangying_robot_gateway.ros_rgbd import WorldTransform

    def lookup(frame_id, stamp_ns):
        input_source.reject("camera disconnected")
        submit(input_source, packets(stamp_ns + 1_000_000))
        return WorldTransform(stamp_ns, frame_id, "world", "calibration-1", np.eye(4))

    input_source = source(transform=lookup)
    submit(input_source, packets())
    with pytest.raises(ValueError, match="invalidated"):
        input_source.read()


def test_replayed_frame_cannot_clear_a_camera_fault():
    input_source = source()
    values = packets()
    submit(input_source, values)
    input_source.read()
    input_source.reject("bad image")
    submit(input_source, values)
    with pytest.raises(ValueError, match="fresh"):
        input_source.read()
    submit(input_source, packets(values[0].stamp_ns + 1_000_000))
    assert input_source.read().sequence == 2


def test_rectified_projection_can_replace_raw_camera_distortion():
    input_source = source()
    color, depth, info = packets()
    info = dataclasses.replace(info, d=(.1, 0., 0., 0., 0.), rectified=True,
                               p=(4., 0., 1., 0., 0., 4., 1., 0., 0., 0., 1., 0.))
    submit(input_source, (color, depth, info))
    np.testing.assert_allclose(input_source.read().intrinsics, [[4, 0, 1], [0, 4, 1], [0, 0, 1]])


def test_ros_rgbd_public_grpc_preserves_metric_cloud_and_rejects_faulted_input(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    import grpc
    from tangying_robot_gateway.journal import RuntimeJournal
    from tangying_robot_gateway.rgbd import RgbdPerception
    from tangying_robot_gateway.ros_rgbd import create_rgbd_backend
    from tangying_robot_gateway.service import RobotRuntimeService
    from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

    input_source = source()
    values = packets()
    submit(input_source, values)
    backend = create_rgbd_backend(profile(), input_source, RgbdPerception(lambda frame: []),
                                  stop=lambda reason: input_source.reject(reason))
    service = RobotRuntimeService(backend, RuntimeJournal(tmp_path / "runtime.json"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        server = grpc.server(executor)
        robot_pb2_grpc.add_RobotRuntimeServicer_to_server(service, server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
                client = robot_pb2_grpc.RobotRuntimeStub(channel)
                observation = next(client.Observe(robot_pb2.ObserveRequest(), timeout=2))
                assert observation.wall_time_unix_ms == values[1].stamp_ns // 1_000_000
                assert list(observation.reconstruction["points"][0]) == [1., 2., 4.]
                assert observation.reconstruction["sourceType"] == "rgbd_camera"
                assert observation.compressed_image.startswith(b"\x89PNG\r\n\x1a\n")
                assert observation.compressed_depth_image.startswith(b"\x89PNG\r\n\x1a\n")
                assert observation.depth_image_media_type == "image/png"
                input_source.reject("CAMERA_DISCONNECTED")
                with pytest.raises(grpc.RpcError) as error:
                    next(client.Observe(robot_pb2.ObserveRequest(), timeout=2))
                assert error.value.code() == grpc.StatusCode.FAILED_PRECONDITION
        finally:
            server.stop(0).wait()
