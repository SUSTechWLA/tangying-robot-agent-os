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


@pytest.mark.parametrize("cloud,odom,expected", [
    (9_000_000_000, 10_000_000_000, ""),
    (8_999_999_999, 10_000_000_000, "CLOUD_SOURCE_TOO_OLD"),
    (10_250_000_000, 10_000_000_000, ""),
    (10_250_000_001, 10_000_000_000, "CLOUD_SOURCE_AHEAD_OF_ODOM"),
    (0, 10_000_000_000, "CLOUD_SOURCE_STAMP_MISSING"),
    (10_000_000_000, 0, "ODOM_SOURCE_STAMP_MISSING"),
])
def test_gazebo_cloud_source_time_boundaries(runtime_module, cloud, odom, expected):
    assert runtime_module._cloud_source_fault(cloud, odom) == expected


def test_duplicate_delayed_and_future_clouds_do_not_refresh_guard_snapshot(runtime_module, monkeypatch):
    import time

    import numpy as np

    node = runtime_module.GazeboRuntimeNode.__new__(runtime_module.GazeboRuntimeNode)
    node.runtime = NS(base_pose=np.eye(4))
    node._lock, node._motion_lock = threading.Lock(), threading.Lock()
    node._odom_stamp_ns, node._odom_received_ns = 10_000_000_000, time.monotonic_ns()
    node._obstacles, node._obstacle_received_ns, node._obstacle_source, node._obstacle_faults = {}, {}, {}, {}
    node._obstacle_fault_details = {}
    node.get_logger = lambda: NS(warn=lambda _: None)
    monkeypatch.setattr(runtime_module, "read_points_numpy", lambda *_a, **_kw: np.array([[.5, 0., .2]]))
    monkeypatch.setattr(runtime_module, "base_from_camera", lambda *_a: np.eye(4))
    monkeypatch.setattr(runtime_module, "camera_mount", lambda *_a: np.eye(4))
    def cloud(stamp):
        sec, nanosec = divmod(stamp, 1_000_000_000)
        return NS(header=NS(frame_id="base_camera_optical_frame", stamp=NS(sec=sec, nanosec=nanosec)))
    camera = "base-rgbd"
    node._on_obstacles(camera, cloud(10_000_000_000))
    original = node._obstacle_received_ns[camera]
    node._on_obstacles(camera, cloud(10_000_000_000))
    assert node._obstacle_received_ns[camera] == original
    assert node._obstacle_faults[camera] == "CLOUD_SOURCE_NOT_ADVANCING"
    node._on_obstacles(camera, cloud(10_050_000_000))
    assert camera not in node._obstacle_faults
    accepted = node._obstacle_received_ns[camera]
    for stamp, fault in [(8_000_000_000, "CLOUD_SOURCE_TOO_OLD"),
                         (10_300_000_000, "CLOUD_SOURCE_AHEAD_OF_ODOM"),
                         (0, "CLOUD_SOURCE_STAMP_MISSING")]:
        node._on_obstacles(camera, cloud(stamp))
        assert node._obstacle_received_ns[camera] == accepted
        assert node._obstacle_faults[camera] == fault


@pytest.mark.parametrize("camera", ["base-rgbd", "head-rgbd"])
def test_wrong_optical_frame_refuses_motion_until_a_fresh_correct_frame(runtime_module, monkeypatch, camera):
    import time

    import numpy as np

    node = runtime_module.GazeboRuntimeNode.__new__(runtime_module.GazeboRuntimeNode)
    node.runtime = NS(base_pose=np.eye(4))
    node._lock, node._motion_lock = threading.Lock(), threading.Lock()
    node._odom_stamp_ns, node._odom_received_ns = 10_000_000_000, time.monotonic_ns()
    node._obstacles, node._obstacle_received_ns, node._obstacle_source, node._obstacle_faults = {}, {}, {}, {}
    node._obstacle_fault_details = {}
    node.get_logger = lambda: NS(warn=lambda _: None)
    node.motion_allowed = lambda: True
    node.readiness_blockers = list
    published = []
    node._cmd_vel = NS(publish=lambda msg: published.append((msg.linear.x, msg.angular.z)))
    monkeypatch.setattr(runtime_module, "Twist", lambda: NS(linear=NS(x=0., y=0.), angular=NS(z=0.)))
    monkeypatch.setattr(runtime_module, "read_points_numpy", lambda *_a, **_kw: np.array([[.5, 0., .2]]))
    monkeypatch.setattr(runtime_module, "base_from_camera", lambda *_a: np.eye(4))
    monkeypatch.setattr(runtime_module, "camera_mount", lambda *_a: np.eye(4))

    def cloud(frame, stamp):
        sec, ns = divmod(stamp, 1_000_000_000)
        return NS(header=NS(frame_id=frame, stamp=NS(sec=sec, nanosec=ns)))

    frame = runtime_module.GAZEBO_OBSTACLE_FRAMES[camera]
    node._on_obstacles(camera, cloud(frame, 10_000_000_000))
    accepted_time, accepted_source = node._obstacle_received_ns[camera], dict(node._obstacle_source[camera])
    _, before = node._obstacle_snapshot()
    node._on_obstacles(camera, cloud("base_link", 10_010_000_000))
    assert node._obstacle_received_ns[camera] == accepted_time
    assert node._obstacle_source[camera] == accepted_source
    _, after = node._obstacle_snapshot()
    assert after[camera]["fault"] == "CLOUD_FRAME_MISMATCH"
    assert after[camera]["faultDetails"] == {"expectedFrameId": frame, "receivedFrameId": "base_link",
                                             "receivedFrameIdTruncated": False}
    # A mismatched callback arriving after the guard snapshot must still block
    # the publisher, including when the accepted snapshot is fresh.
    receipt = node._publish_velocity(.1, 0., guard_sources={camera: before[camera]})
    assert receipt["guardSourceFaults"][camera] == "CLOUD_FRAME_MISMATCH"
    assert published == [(0., 0.)]
    node._on_obstacles(camera, cloud(frame, 10_050_000_000))
    assert camera not in node._obstacle_faults
    assert camera not in node._obstacle_fault_details


def test_replayed_odometry_does_not_refresh_feedback_receipt(runtime_module):
    import time
    from collections import deque

    import numpy as np

    node = runtime_module.GazeboRuntimeNode.__new__(runtime_module.GazeboRuntimeNode)
    poses = []
    node.runtime = NS(record_base_pose=poses.append)
    node._lock = threading.Lock()
    node._odom_stamp_ns, node._odom_received_ns = 10_000_000_000, 123
    node._odom_history = deque(maxlen=120)
    node._imu = (0., 0., 10_000_000_000, time.monotonic_ns())
    def odom(stamp):
        sec, ns = divmod(stamp, 1_000_000_000)
        return NS(header=NS(stamp=NS(sec=sec, nanosec=ns)), pose=NS(pose=NS(
            position=NS(x=0., y=0., z=0.), orientation=NS(w=1., x=0., y=0., z=0.))))
    for stamp in [10_000_000_000, 9_900_000_000, 0]:
        node._on_odometry(odom(stamp))
        assert node._odom_received_ns == 123
        assert not poses
    node._on_odometry(odom(10_100_000_000))
    assert node._odom_received_ns != 123
    np.testing.assert_array_equal(poses[0], np.eye(4))


def test_home_operational_margin_refuses_before_any_nonzero_pulse(runtime_module, monkeypatch):
    import time

    import numpy as np
    from tangying_robot_gateway import gazebo_commissioning
    from tangying_robot_gateway.home_commissioning import HOME_OPERATIONAL_RADIUS_M

    monkeypatch.setattr(gazebo_commissioning, "commissioning", lambda: {"home": True})
    node = runtime_module.GazeboRuntimeNode.__new__(runtime_module.GazeboRuntimeNode)
    node.runtime = NS(base_pose=np.eye(4))
    node._motion_lock = threading.Lock()
    node._lock = threading.Lock()
    node._odom_stamp_ns, node._odom_received_ns = 100, time.monotonic_ns()
    node._obstacle_faults = {}
    node._obstacle_fault_details = {}
    node._obstacle_received_ns = {camera: time.monotonic_ns()
                                 for camera in runtime_module.GAZEBO_CAMERA_MOUNTS}
    node.motion_allowed = lambda: True
    node.readiness_snapshot = lambda: {"blockers": []}
    node._obstacles = {camera: np.array([[.35, 0., .2]]) if i == 0 else np.zeros((0, 3))
                       for i, camera in enumerate(runtime_module.GAZEBO_CAMERA_MOUNTS)}
    node._obstacle_source = {camera: {"sourceFrameId": camera + "_optical", "sensorStampNs": "100",
                                     "sensorClock": "ros_header_clock_unspecified"}
                             for camera in runtime_module.GAZEBO_CAMERA_MOUNTS}
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
    refusal = result["motionEvidence"]["guardRefusal"]
    assert refusal["intrudingPointCount"] == 1
    assert refusal["minimumSweptDistanceM"] == .35
    assert refusal["closestPointBaseM"] == [.35, 0., .2]
    assert refusal["radiusM"] == HOME_OPERATIONAL_RADIUS_M
    assert refusal["pointCoordinatesFrame"] == "base_link"
    assert all(source["sensorStampNs"] == "100" for source in refusal["sources"].values())
    assert result["motionEvidence"]["guardBasePoseAtCheck"] == [0., 0., 0.]
    assert events[-1]["guardRefusal"] == refusal


def test_replaced_stale_cloud_at_guard_check_cannot_issue_a_motion_pulse(runtime_module, monkeypatch):
    import time

    import numpy as np
    from tangying_robot_gateway import gazebo_commissioning

    monkeypatch.setattr(gazebo_commissioning, "commissioning", lambda: {"home": True})
    node = runtime_module.GazeboRuntimeNode.__new__(runtime_module.GazeboRuntimeNode)
    node.runtime = NS(base_pose=np.eye(4))
    node._motion_lock = threading.Lock()
    node._lock = threading.Lock()
    node._odom_stamp_ns, node._odom_received_ns = 100, time.monotonic_ns()
    node._obstacle_faults = {}
    node._obstacle_fault_details = {}
    node._obstacle_received_ns = {camera: time.monotonic_ns()
                                 for camera in runtime_module.GAZEBO_CAMERA_MOUNTS}
    node.motion_allowed = lambda: True
    node.readiness_snapshot = lambda: {"blockers": []}
    cancel = threading.Event()
    def stale_after_precheck():
        cancel.set()
        return np.zeros((0, 3)), {camera: {"receivedMonotonicNs": "0"}
                                  for camera in runtime_module.GAZEBO_CAMERA_MOUNTS}
    node._obstacle_snapshot = stale_after_precheck
    node._log_step = lambda _message: None
    node._record_bounded_evidence = lambda _event: None
    pulses = []
    node._publish_velocity = lambda x, z, **_kw: pulses.append((x, z)) or {
        "published": True, "nonzero": bool(x or z)}
    result = node._bounded_step([0., 0., 0., .995004165, 0., 0., .099833417], cancel)
    assert result["code"] == "CANCELLED"
    assert pulses and all(pulse == (0., 0.) for pulse in pulses)
    assert result["motionEvidence"]["nonzeroPublishedPulseCount"] == 0


@pytest.mark.parametrize("failure", ["receipt_expired", "source_fell_behind"])
def test_guard_cloud_expiring_while_publication_waits_emits_only_zero(runtime_module, monkeypatch, failure):
    import numpy as np
    from tangying_robot_gateway import gazebo_commissioning

    clock = [10_000_000_000]
    monkeypatch.setattr(runtime_module, "time", NS(monotonic_ns=lambda: clock[0],
                                                    monotonic=lambda: clock[0] / 1e9))
    monkeypatch.setattr(gazebo_commissioning, "commissioning", lambda: {"home": True})
    monkeypatch.setattr(runtime_module, "Twist", lambda: NS(linear=NS(x=0., y=0.), angular=NS(z=0.)))
    node = runtime_module.GazeboRuntimeNode.__new__(runtime_module.GazeboRuntimeNode)
    node.runtime = NS(base_pose=np.eye(4))
    node._motion_lock = threading.Lock()
    node._lock = threading.Lock()
    node._odom_stamp_ns, node._odom_received_ns = 100, clock[0]
    node._obstacle_faults = {}
    node._obstacle_fault_details = {}
    node._obstacle_received_ns = {camera: clock[0] - 500_000_000
                                 for camera in runtime_module.GAZEBO_CAMERA_MOUNTS}
    node._obstacles = {camera: np.array([[.7, 0., .2]]) for camera in runtime_module.GAZEBO_CAMERA_MOUNTS}
    node._obstacle_source = {camera: {"sensorStampNs": "100"}
                             for camera in runtime_module.GAZEBO_CAMERA_MOUNTS}
    node.motion_allowed = lambda: True
    node.readiness_snapshot = lambda: {"blockers": []}
    def blocked_readiness():
        if failure == "receipt_expired":
            clock[0] += 600_000_000  # lock contention crosses the receipt deadline
        else:
            node._odom_stamp_ns += 1_000_000_001  # simulation clock advances during that wait
        return []  # other sensor feedback remains fresh
    node.readiness_blockers = blocked_readiness
    node._log_step = lambda _message: None
    node._record_bounded_evidence = lambda _event: None
    published = []
    node._cmd_vel = NS(publish=lambda message: published.append((message.linear.x, message.angular.z)))
    result = node._bounded_step([0., 0., 0., .995004165, 0., 0., .099833417], threading.Event())
    assert result["code"] == "SENSOR_STALE"
    assert result["motionEvidence"]["guardSourceFaults"]
    assert published and all(pulse == (0., 0.) for pulse in published)
    assert result["motionEvidence"]["nonzeroPublishedPulseCount"] == 0


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
