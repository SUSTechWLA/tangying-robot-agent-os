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

    def drive(goal, command_id, cancel):
        assert node._motion_lock.acquire(blocking=False)
        node._motion_lock.release()
        return {"ok": True, "code": "STEP_COMPLETE"}

    backend = captured_backend()
    backend.node.navigate = drive
    backend.node.runtime.record_base_pose(np.eye(4))
    assert backend.capabilities().robot_profile["embodiment"] == "mobile_manipulator"
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


def captured_backend():
    import time

    from tangying_robot_gateway.gazebo_runtime import GazeboRuntime
    node = node_fixture()
    node.runtime = GazeboRuntime(robot_id="gz", adapter="gazebo", cameras={"base-rgbd": "base", "head-rgbd": "head"}, calibration_revision="revision", software_version="1")
    base = np.eye(4)
    base[0, 3] = 2.0
    node.runtime.record_base_pose(base)
    template = node_fixture().runtime._samples["base-rgbd"]
    for index, camera in enumerate(node.runtime.cameras):
        mount = base.copy()
        mount[2, 3] = 0.2 + index
        node.runtime.record(camera, replace(template, world_from_camera_link=mount,
                            rgb=np.full((2, 2, 3), 30 + index * 150, np.uint8),
                            captured_at_unix_ms=int(time.time()*1000),
                            received_monotonic_ns=time.monotonic_ns()))
    return GazeboSkillBackend(node)


def test_camera_selection_preserves_images_capture_and_world_transform():
    import pytest
    from tangying_robot_gateway.runtime import ObservationRequest
    from tangying_robot_gateway.service import RobotRuntimeService, observation_from_proto
    from tangying_robot_proto.robot.v1 import robot_pb2
    backend = captured_backend()
    request = observation_from_proto(robot_pb2.ObserveRequest(source_id="gz/head-rgbd"))
    assert request.source_id == "gz/head-rgbd"
    service = RobotRuntimeService(backend)
    base = service._validated_observation(ObservationRequest(source_id="gz/base-rgbd"))
    head = service._validated_observation(request)
    assert base.compressed_image != head.compressed_image
    assert head.reconstruction["sourceId"] == "gz/head-rgbd"
    assert head.reconstruction["observedAtUnixMs"] == head.wall_time_unix_ms
    assert head.reconstruction["sequence"] == 1_000_000_000
    assert head.reconstruction["frameId"] == "world"
    np.testing.assert_allclose(np.asarray(head.reconstruction["points"])[:, 0], 3.0)
    with pytest.raises(ValueError, match="UNKNOWN_CAMERA_SOURCE"):
        backend.observe(ObservationRequest(source_id="gz/unknown"))
    # A moving base must not relabel a previously captured frame.
    backend.node.runtime.record_base_pose(np.eye(4))
    same = backend.observe(request)
    assert same.reconstruction == head.reconstruction


def test_arrival_is_measured_and_stale_odometry_is_not_success(monkeypatch):
    backend = captured_backend()
    now = backend.node.runtime._samples["base-rgbd"].received_monotonic_ns
    monkeypatch.setattr("tangying_robot_gateway.gazebo_backend.time.monotonic_ns", lambda: now)
    command = Command(schema_version="robot.v1", task_id="t", command_id="verify",
                      capability="verify_arrival", parameters={"goalPose": [2., 0., 0., 1., 0., 0., 0.]})
    assert backend.execute(command).success
    wrong = replace(command, parameters={"goalPose": [3., 0., 0., 1., 0., 0., 0.]})
    assert backend.execute(wrong).code == "NOT_AT_DESTINATION"
    sample = backend.node.runtime._samples["base-rgbd"]
    backend.node.runtime._samples["base-rgbd"] = replace(sample, odometry_stamp_ns=1)
    assert backend.execute(command).code == "ODOMETRY_STALE"
    backend.node.runtime._samples["base-rgbd"] = replace(sample, received_monotonic_ns=1)
    assert backend.execute(command).code == "SENSOR_STALE"


def test_gazebo_link_transforms_agree_with_canonical_forward_kinematics():
    import xml.etree.ElementTree as ET
    from pathlib import Path

    from scipy.spatial.transform import Rotation
    from tangying_robot_gateway.arm_kinematics import arm_link_poses, arm_links
    world = ET.parse(Path(__file__).resolve().parents[2] / "ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf")
    robot = world.getroot().find("world/model[@name='tangying_robot']")
    for side in ("left", "right"):
        expected = arm_link_poses(side, {link.motor: 0. for link in arm_links(side)}, base=np.eye(4))
        transforms = {"base_link": np.eye(4)}
        for link in arm_links(side):
            pose = robot.find(f"link[@name='{link.link}']/pose")
            values = np.fromstring(pose.text, sep=" ")
            relative = np.eye(4)
            relative[:3, :3] = Rotation.from_euler("xyz", values[3:]).as_matrix()
            relative[:3, 3] = values[:3]
            transforms[link.link] = transforms[pose.attrib["relative_to"]] @ relative
            np.testing.assert_allclose(transforms[link.link], expected[link.link], atol=2e-5)


def test_concurrent_observers_do_not_reorder_capture_validation(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    import pytest
    from tangying_robot_gateway.runtime import ObservationRequest
    from tangying_robot_gateway.service import RobotRuntimeService
    backend = captured_backend()
    service = RobotRuntimeService(backend)
    original = backend.observe
    older_acquired, release_older = threading.Event(), threading.Event()
    calls = []

    def delayed(request):
        frame = original(request)
        calls.append(frame.observation_id)
        if len(calls) == 1:
            older_acquired.set()
            assert release_older.wait(2)
        return frame

    monkeypatch.setattr(backend, 'observe', delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service._validated_observation, ObservationRequest())
        assert older_acquired.wait(1)
        sample = backend.node.runtime._samples['head-rgbd']
        backend.node.runtime._samples['head-rgbd'] = replace(sample, sensor_stamp_ns=sample.sensor_stamp_ns+1)
        second = pool.submit(service._validated_observation, ObservationRequest())
        # Allow the second request to contend while the first capture is delayed.
        try:
            second.result(timeout=.05)
        except TimeoutError:
            pass
        finally:
            release_older.set()
        assert first.result().reconstruction['sequence'] < second.result().reconstruction['sequence']
    # Real source regression must still fail, including after concurrent readers.
    backend.node.runtime._samples['head-rgbd'] = sample
    with pytest.raises(ValueError, match='regressed'):
        service._validated_observation(ObservationRequest())
