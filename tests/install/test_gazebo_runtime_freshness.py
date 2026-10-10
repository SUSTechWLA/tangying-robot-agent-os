"""Offline ROS stubs exercise the real readiness method; no ROS graph is started."""

from __future__ import annotations

import importlib.util
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

RUNTIME_SOURCE = (Path(__file__).resolve().parents[2] /
                  "robot/ros2_ws/src/tangying_navigation/tangying_navigation/gazebo_runtime_node.py")
BLOCKERS = ["RGBD_NOT_READY", "JOINT_FEEDBACK_STALE", "SUCTION_FEEDBACK_STALE", "IMU_NOT_READY", "ODOMETRY_STALE"]


@pytest.fixture
def runtime_module(monkeypatch):
    for name, module in {
        "rclpy": NS(), "rclpy.node": NS(Node=object),
        "rclpy.callback_groups": NS(MutuallyExclusiveCallbackGroup=object),
        "rclpy.executors": NS(MultiThreadedExecutor=object),
        "rclpy.qos": NS(qos_profile_sensor_data=object()),
        "geometry_msgs": NS(), "geometry_msgs.msg": NS(Twist=object),
        "nav_msgs": NS(), "nav_msgs.msg": NS(Odometry=object),
        "sensor_msgs": NS(),
        "sensor_msgs.msg": NS(CameraInfo=object, Image=object, Imu=object, JointState=object, PointCloud2=object),
        "sensor_msgs_py": NS(), "sensor_msgs_py.point_cloud2": NS(read_points_numpy=object),
        "std_msgs": NS(), "std_msgs.msg": NS(Float64=object, String=object),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location("gazebo_freshness_under_test", RUNTIME_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def node_with_samples(module, stamp):
    node = module.GazeboRuntimeNode.__new__(module.GazeboRuntimeNode)
    node._lock = threading.Lock()
    node.runtime = NS(cameras={"base-rgbd": "base", "head-rgbd": "head"}, _samples={})
    refresh_samples(node, stamp)
    return node


def refresh_samples(node, stamp):
    for camera in node.runtime.cameras:
        node.runtime._samples[camera] = NS(capture_monotonic_ns=stamp, received_monotonic_ns=stamp)
    node.joint_positions = {"joint": 0.0}
    node.joint_received_ns = stamp
    node._suction_state = {"attached": False}
    node._suction_received_ns = stamp
    node._imu = (0.0, 0.0, 123, stamp)
    node._odom_received_ns = stamp


def test_fresh_callbacks_during_lock_contention_are_not_future_dated(runtime_module, monkeypatch):
    clock = [10_000_000_000]
    monkeypatch.setattr(runtime_module, "time", NS(monotonic_ns=lambda: clock[0]))
    node = node_with_samples(runtime_module, clock[0])
    held = threading.Lock()
    waiting = threading.Event()

    class ContendedLock:
        def __enter__(self):
            waiting.set()
            held.acquire()

        def __exit__(self, *_):
            held.release()

    node._lock = ContendedLock()
    held.acquire()
    with ThreadPoolExecutor(max_workers=1) as executor:
        check = executor.submit(node.readiness_blockers)
        try:
            assert waiting.wait(2), "readiness did not reach the shared sample lock"
            # Model a callback owning the lock and updating all feedback while
            # readiness is waiting. These samples are fresh at lock acquisition.
            clock[0] += 200_000_000
            refresh_samples(node, clock[0])
        finally:
            held.release()
        assert check.result(timeout=2) == []


@pytest.mark.parametrize("offset,expected", [
    (0, []),
    (-500_000_000, []),
    (-500_000_001, BLOCKERS[1:4]),
    (-1_000_000_000, BLOCKERS[1:4]),
    (-1_000_000_001, BLOCKERS),
    (1, BLOCKERS),
])
def test_readiness_preserves_exact_age_limits_and_rejects_future_samples(runtime_module, monkeypatch, offset, expected):
    now = 10_000_000_000
    monkeypatch.setattr(runtime_module, "time", NS(monotonic_ns=lambda: now))
    node = node_with_samples(runtime_module, now + offset)
    assert node.readiness_blockers() == expected


def test_missing_sensor_samples_remain_unready(runtime_module, monkeypatch):
    now = 10_000_000_000
    monkeypatch.setattr(runtime_module, "time", NS(monotonic_ns=lambda: now))
    node = node_with_samples(runtime_module, now)
    node.runtime._samples.clear()
    node.joint_positions = {}
    node._suction_state = None
    node._imu = None
    node._odom_received_ns = 0
    assert node.readiness_blockers() == BLOCKERS
