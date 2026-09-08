from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest


def native(monkeypatch):
    path = Path(__file__).parents[2] / "ros2_ws/src/tangying_robot_gateway"
    monkeypatch.syspath_prepend(str(path))
    return importlib.import_module("tangying_ros_gateway.rgbd_node")


def header():
    return NS(stamp=NS(sec=1_800_000_000, nanosec=123_456_789), frame_id="camera_optical")


def test_native_snapshot_preserves_nanosecond_stamp_and_owns_message_bytes(monkeypatch):
    module = native(monkeypatch)
    message = NS(header=header(), width=1, height=1, encoding="rgb8", step=3,
                 is_bigendian=0, data=bytearray([1, 2, 3]))
    packet = module.image_packet(message)
    message.data[0] = 99
    assert packet.stamp_ns == 1_800_000_000_123_456_789
    assert packet.data == bytes([1, 2, 3])


def test_native_camera_info_keeps_rectified_projection_and_roi(monkeypatch):
    module = native(monkeypatch)
    info = NS(header=header(), width=640, height=480, k=[1.] * 9, d=[.1] * 5,
              p=[2.] * 12, binning_x=0, binning_y=0,
              roi=NS(x_offset=0, y_offset=0, width=0, height=0))
    packet = module.camera_info_packet(info, rectified=True)
    assert packet.p == (2.,) * 12
    assert packet.rectified is True
    assert packet.roi == (0, 0, 0, 0)


def test_native_transform_converts_ros_xyzw_without_using_latest_time(monkeypatch):
    module = native(monkeypatch)
    transform = NS(header=header(), child_frame_id="camera_optical",
                   transform=NS(translation=NS(x=1., y=2., z=3.),
                                rotation=NS(x=0., y=0., z=1., w=0.)))
    transform.header.frame_id = "world"
    packet = module.world_transform(transform, "calibration-1")
    np.testing.assert_allclose(packet.matrix, [[-1, 0, 0, 1], [0, -1, 0, 2],
                                               [0, 0, 1, 3], [0, 0, 0, 1]])
    assert packet.stamp_ns == 1_800_000_000_123_456_789
    assert not packet.static
    transform.transform.rotation.w = 2.
    with pytest.raises(ValueError, match="quaternion"):
        module.world_transform(transform, "calibration-1")


def test_native_factory_rejects_motion_profile_before_importing_ros(monkeypatch, tmp_path):
    from .test_ros_rgbd import profile

    module = native(monkeypatch)
    robot = profile()
    robot["tools"].append("arm.move")
    robot["actionLimits"] = {"slide.position": {"min": 0., "max": 1., "unit": "m"}}
    (tmp_path / "profile.json").write_text(json.dumps(robot))
    config = tmp_path / "rgbd.json"
    config.write_text(json.dumps({"profilePath": "profile.json", "sourceId": "front-rgbd"}))
    monkeypatch.setenv("TANGYING_ROS_RGBD_CONFIG", str(config))
    monkeypatch.setitem(sys.modules, "rclpy", None)
    with pytest.raises(ValueError, match="sensor-only"):
        module.create_sensor_backend()
