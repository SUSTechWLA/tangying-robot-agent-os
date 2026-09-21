"""生产 Gazebo 适配器的捕获时位姿、陈旧里程计和安全接口回归。"""

import threading
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
from tangying_robot_gateway.gazebo_backend import GazeboSkillBackend
from tangying_robot_gateway.gazebo_runtime import CameraSample
from tangying_robot_gateway.grounded import RuntimeVerifier, load_contracts
from tangying_robot_gateway.grounded.store import EvidenceStore
from tangying_robot_gateway.runtime import Command


def node_fixture():
    pose = np.eye(4)
    sample = CameraSample(
        width=2,
        height=2,
        rgb=np.zeros((2, 2, 3), dtype=np.uint8),
        depth_metres=np.ones((2, 2)),
        horizontal_fov_rad=1.0,
        world_from_camera_link=pose,
        captured_at_unix_ms=1,
        sensor_stamp_ns=1_000_000_000,
        odometry_stamp_ns=1_000_000_000,
        received_monotonic_ns=100_000_000,
        base_pose_at_capture=pose.copy(),
    )
    runtime = SimpleNamespace(
        robot_id="gz",
        calibration_revision="revision",
        _samples={"base-rgbd": sample},
        base_pose=pose.copy(),
    )
    return SimpleNamespace(
        runtime=runtime,
        _lock=threading.Lock(),
        _motion_lock=threading.Lock(),
        enable_bounded_motion=lambda: None,
        _publish_velocity=lambda *args: None,
    )


def test_navigation_evidence_uses_capture_pose_and_rejects_old_odometry(tmp_path, monkeypatch):
    node = node_fixture()
    backend = GazeboSkillBackend(node)
    goal = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    command = Command(
        schema_version="robot.v1",
        task_id="t",
        command_id="a",
        capability="navigation.navigate",
        parameters={"goalPose": goal},
    )
    elapsed = [0.0]
    monkeypatch.setattr("tangying_robot_gateway.gazebo_backend.time.monotonic", lambda: elapsed[0])

    def tick(seconds):
        elapsed[0] += 0.1
        previous = node.runtime._samples["base-rgbd"]
        node.runtime._samples["base-rgbd"] = replace(
            previous,
            sensor_stamp_ns=previous.sensor_stamp_ns + 100_000_000,
            received_monotonic_ns=previous.received_monotonic_ns + 100_000_000,
            odometry_stamp_ns=previous.odometry_stamp_ns + 100_000_000,
        )

    monkeypatch.setattr("tangying_robot_gateway.gazebo_backend.time.sleep", tick)
    node.runtime.base_pose[0, 3] = 10.0  # newer robot pose must not relabel an old capture
    store = EvidenceStore(tmp_path)
    stream = backend.collect_grounded_evidence(
        command=command, action_id="a", start_ns=1, edge_boot_id="boot", store=store, phase="post"
    )
    assert len(stream) == 3 and all(s.values["position_error_m"] == 0.0 for s in stream)
    report = RuntimeVerifier(store.exists).verify(
        load_contracts()[command.capability],
        stream,
        action_id="a",
        edge_boot_id="boot",
        start_ns=1,
        end_ns=500_000_001,
    )
    assert report.verdict == "VERIFIED"
    node.runtime._samples["base-rgbd"] = replace(
        node.runtime._samples["base-rgbd"], odometry_stamp_ns=1
    )
    assert (
        backend.collect_grounded_evidence(
            command=command,
            action_id="a",
            start_ns=1,
            edge_boot_id="boot",
            store=store,
            phase="post",
        )
        == []
    )


def test_backend_uses_driver_serialization_without_holding_obstacle_lock():
    node = node_fixture()

    def drive(goal, cancel):
        assert node._motion_lock.acquire(blocking=False)
        node._motion_lock.release()
        return {"ok": True, "code": "STEP_COMPLETE"}

    node.bounded_step = drive
    backend = GazeboSkillBackend(node)
    assert backend.capabilities().robot_profile["embodiment"] == "mobile_base"
    command = Command(
        schema_version="robot.v1",
        task_id="t",
        command_id="a",
        capability="navigation.navigate",
        parameters={"goalPose": [0.1, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]},
    )
    assert backend.execute(command).success


def test_wheel_axes_rotate_about_model_y_not_the_rotated_wheel_frame():
    import math
    import xml.etree.ElementTree as ET
    from pathlib import Path

    world = ET.parse(
        Path(__file__).resolve().parents[2]
        / "ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf"
    )
    robot = world.getroot().find("world/model[@name='tangying_robot']")
    for side in ["left", "right"]:
        joint = robot.find(f"joint[@name='{side}_wheel_joint']/axis/xyz")
        vector = np.array([float(v) for v in joint.text.split()])
        if joint.get("expressed_in") != "__model__":
            roll = float(robot.find(f"link[@name='{side}_wheel']/pose").text.split()[3])
            rotation = np.array(
                [
                    [1, 0, 0],
                    [0, math.cos(roll), -math.sin(roll)],
                    [0, math.sin(roll), math.cos(roll)],
                ]
            )
            vector = rotation @ vector
        assert np.allclose(vector, [0, 1, 0], atol=1e-4), (
            "wheel steering cannot serve as drive odometry"
        )


def test_service_rpc_cannot_bypass_gvf_but_stopping_remains_available():
    from tangying_robot_gateway.gazebo_backend import grounded_service_requires_contract

    assert grounded_service_requires_contract("mapping.move", True)
    assert grounded_service_requires_contract("vendor.undeclared_motion", True)
    assert not grounded_service_requires_contract("mapping.stop_motion", True)
    assert not grounded_service_requires_contract("mapping.cancel", True)
    assert not grounded_service_requires_contract("mapping.status", False)
