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


def test_home_operational_margin_refuses_before_any_nonzero_pulse(runtime_module, monkeypatch):
    import time

    import numpy as np
    from tangying_robot_gateway import gazebo_commissioning
    from tangying_robot_gateway.home_commissioning import HOME_OPERATIONAL_RADIUS_M

    monkeypatch.setattr(gazebo_commissioning, "commissioning", lambda: {"home": True})
    node = runtime_module.GazeboRuntimeNode.__new__(runtime_module.GazeboRuntimeNode)
    node.runtime = NS(base_pose=np.eye(4))
    node._motion_lock = threading.Lock()
    node._obstacle_received_ns = {camera: time.monotonic_ns()
                                 for camera in runtime_module.GAZEBO_CAMERA_MOUNTS}
    node.motion_allowed = lambda: True
    node.readiness_snapshot = lambda: {"blockers": []}
    node._obstacle_points = lambda: np.array([[.35, 0., .2]])
    node._log_step = lambda _message: None
    events, pulses, radii = [], [], []
    node._record_bounded_evidence = events.append
    node._publish_velocity = lambda x, z, **_kw: pulses.append((x, z)) or {
        "published": True, "nonzero": bool(x or z)}
    guard = runtime_module.swept_disc_step_is_clear
    def record_guard(points, **kwargs):
        radii.append(kwargs["radius"])
        return guard(points, **kwargs)
    monkeypatch.setattr(runtime_module, "swept_disc_step_is_clear", record_guard)
    result = node._bounded_step([0., 0., 0., .995004165, 0., 0., .099833417], threading.Event())
    assert result["code"] == "NAV_ENVELOPE"
    assert radii == [HOME_OPERATIONAL_RADIUS_M]
    assert pulses and all(pulse == (0., 0.) for pulse in pulses)
    assert result["motionEvidence"]["nonzeroPublishedPulseCount"] == 0
    assert result["motionEvidence"]["finalZeroPublished"] is True


@pytest.mark.parametrize("ready,cancelled,expected", [
    (False, False, "NAVIGATION_NOT_READY"),
    (True, True, "CANCELLED"),
    (True, False, "SUCCEEDED"),
])
def test_navigation_checks_driver_before_any_arm_preparation(runtime_module, ready, cancelled, expected):
    node = runtime_module.GazeboRuntimeNode.__new__(runtime_module.GazeboRuntimeNode)
    calls = []
    cancel = threading.Event()
    if cancelled:
        cancel.set()
    node._command_lock = threading.Lock()
    node._actuator_lock = threading.Lock()
    node.navigation = NS(readiness=lambda: calls.append("readiness") or {"ready": ready},
                         navigate=lambda *_a, **_k: calls.append("navigate") or {"ok": True, "code": "SUCCEEDED"})
    node.prepare_navigation = lambda _c: calls.append("prepare") or NS(success=True)
    node.stop_navigation = lambda: calls.append("stop")
    result = node.navigate([0., 0., 0., 1., 0., 0., 0.], "command", cancel)
    assert result["code"] == expected
    assert calls == (["readiness", "prepare", "navigate", "stop"] if expected == "SUCCEEDED"
                     else ["readiness", "stop"])
    assert not node._command_lock.locked()


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
