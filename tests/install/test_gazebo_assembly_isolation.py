"""ROS-stub concurrency regressions; no robot or simulator is started."""

import gc
import json
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace as NS

import numpy as np
import pytest
from tangying_robot_gateway import gazebo_runtime as runtime_api
from tangying_robot_gateway.gazebo_bridge import intrinsics_from_field_of_view
from tangying_robot_gateway.gazebo_runtime import GazeboRuntime, GazeboRuntimeError

from tests.install import test_gazebo_runtime_freshness as freshness

runtime_module = freshness.runtime_module
CAMERA = "base-rgbd"
STAMP = 1_050_000_000


def stage_pair(node, stamp=STAMP, value=1):
    node._pending[CAMERA].update({
        "rgb": np.full((3, 4, 3), value, dtype=np.uint8),
        "depth": np.full((3, 4), 2., dtype=np.float64),
        "rgb_stamp": stamp, "depth_stamp": stamp,
        "rgb_received_ns": 10_000_000_000, "depth_received_ns": 10_000_000_001,
        "fov": 1.25, "intrinsics": intrinsics_from_field_of_view(4, 3, 1.25),
        "info_size": (4, 3),
    })


def assembler(module):
    node = module.GazeboRuntimeNode.__new__(module.GazeboRuntimeNode)
    node._lock = threading.Lock()
    node.runtime = GazeboRuntime(robot_id="offline-test", adapter="gazebo",
        cameras={CAMERA: "base_optical"}, calibration_revision="c"*64,
        software_version="offline-test")
    node.runtime.record_base_pose(np.eye(4))
    node._pending = {CAMERA: {}, "head-rgbd": {}}
    node._rgb_encoding = {}
    node._joint_history = deque([(1_000_000_000, {"joint": 0.}), (1_100_000_000, {"joint": 1.})])
    node._odom_history = deque([(1_000_000_000, np.eye(4)), (1_100_000_000, np.eye(4))])
    node._sensor_clock_history = deque([(1_000_000_000, (1_700_000_000_000, 9_950_000_000)),
                                      (1_100_000_000, (1_700_000_000_100, 10_050_000_000))])
    node._suction_state = {"side": "left", "attached": False}
    node._suction_sequence = 0
    node._suction_received_ns = 0
    node._imu = None
    node._assembly_timing = {}
    node.get_logger = lambda: NS(warn=lambda _message: None)
    stage_pair(node)
    return node


def block_validation(node):
    entered, release = threading.Event(), threading.Event()
    original = node.runtime.validate_sample

    def validate(camera, sample):
        entered.set()
        assert release.wait(2), "test did not release validation"
        return original(camera, sample)

    node.runtime.validate_sample = validate
    return entered, release, original


def test_blocked_image_validation_does_not_block_real_feedback_callbacks(runtime_module):
    node = assembler(runtime_module)
    entered, release, _ = block_validation(node)
    stamp = NS(sec=1, nanosec=80_000_000)
    imu = NS(orientation=NS(x=0., y=0., z=0., w=1.), header=NS(stamp=stamp))
    joints = NS(name=["joint"], position=[.8], header=NS(stamp=stamp))
    suction = NS(data=json.dumps({"schemaVersion": "gazebo.suction.v1", "sequence": 1, "attached": False}))
    odom = NS(header=NS(stamp=stamp), pose=NS(pose=NS(
        position=NS(x=.1, y=0., z=0.), orientation=NS(w=1., x=0., y=0., z=0.))))
    with ThreadPoolExecutor(max_workers=2) as pool:
        assembly = pool.submit(node._try_assemble, CAMERA)
        assert entered.wait(1)
        try:
            # Mimic the existing mutually exclusive feedback callback group:
            # callbacks remain serial, but no image processing is in that group.
            def feedback():
                node._on_imu(imu)
                node._on_suction(suction)
                node._on_joints(joints)
                node._on_odometry(odom)
            pool.submit(feedback).result(timeout=1)
            assert node._suction_sequence == 1
            assert node._imu[2] == 1_080_000_000
            assert node._odom_stamp_ns == 1_080_000_000
            assert node.joint_positions == {"joint": .8}
            assert not assembly.done()
            assert CAMERA not in node.runtime._samples
        finally:
            release.set()
        assembly.result(timeout=1)
    sample = node.runtime._samples[CAMERA]
    assert sample.sensor_stamp_ns == STAMP
    # The validated sample keeps its original capture interpolation basis even
    # though current feedback and base pose changed while validation was blocked.
    assert sample.joint_positions_at_capture == {"joint": .5}
    assert sample.base_pose_at_capture[0, 3] == 0.
    assert sample.capture_monotonic_ns == 10_000_000_000
    assert sample.captured_at_unix_ms == 1_700_000_000_050
    assert sample.camera_intrinsics[0, 0] == intrinsics_from_field_of_view(4, 3, 1.25)[0, 0]
    timing = node._assembly_timing[CAMERA]
    assert timing["executionScope"] == "timer_snapshot_then_offlock_validation_then_commit"
    assert "lastDurationUnderSensorLockMs" not in timing


def test_new_pending_pair_is_retained_while_older_valid_job_commits(runtime_module):
    node = assembler(runtime_module)
    entered, release, original = block_validation(node)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(node._try_assemble, CAMERA)
        assert entered.wait(1)
        try:
            with node._lock:
                stage_pair(node, STAMP+10_000_000, value=2)
        finally:
            release.set()
        old.result(timeout=1)
    assert node.runtime._samples[CAMERA].sensor_stamp_ns == STAMP
    assert np.all(node.runtime._samples[CAMERA].rgb == 1)
    assert node._pending[CAMERA]["rgb_stamp"] == STAMP+10_000_000
    assert np.all(node._pending[CAMERA]["rgb"] == 2)
    node.runtime.validate_sample = original
    node._try_assemble(CAMERA)
    assert node.runtime._samples[CAMERA].sensor_stamp_ns == STAMP+10_000_000
    assert np.all(node.runtime._samples[CAMERA].rgb == 2)
    assert "rgb" not in node._pending[CAMERA]


def test_out_of_order_validation_cannot_overwrite_newer_committed_sample(runtime_module):
    node = assembler(runtime_module)
    entered, release, original = block_validation(node)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(node._try_assemble, CAMERA)
        assert entered.wait(1)
        try:
            with node._lock:
                stage_pair(node, STAMP+10_000_000, value=2)
            node.runtime.validate_sample = original
            node._try_assemble(CAMERA)
            newer = node.runtime._samples[CAMERA]
        finally:
            release.set()
        old.result(timeout=1)
    assert node.runtime._samples[CAMERA] is newer
    assert node._pending[CAMERA]["last_stamp"] == STAMP+10_000_000


@pytest.mark.parametrize("invalid", ["mismatched_stamp", "missing_pose", "missing_joint", "missing_clock", "wrong_calibrated_size"])
def test_incomplete_capture_basis_is_never_committed(runtime_module, invalid):
    node = assembler(runtime_module)
    if invalid == "mismatched_stamp":
        node._pending[CAMERA]["depth_stamp"] += 1
    elif invalid == "missing_pose":
        node._odom_history.clear()
    elif invalid == "missing_joint":
        node._joint_history.clear()
    elif invalid == "missing_clock":
        node._sensor_clock_history.clear()
    else:
        node._pending[CAMERA]["info_size"] = (640, 480)
    node._try_assemble(CAMERA)
    assert CAMERA not in node.runtime._samples
    assert "last_stamp" not in node._pending[CAMERA]


@pytest.mark.parametrize("error", [GazeboRuntimeError("INVALID_CAPTURE", "offline rejection"), ValueError("bad frame")])
def test_validation_failure_preserves_previous_sample_and_pending_data(runtime_module, error):
    node = assembler(runtime_module)
    node._try_assemble(CAMERA)
    previous = node.runtime._samples[CAMERA]
    stage_pair(node, STAMP+10_000_000)
    def refuse(*_args):
        raise error
    node.runtime.validate_sample = refuse
    node._try_assemble(CAMERA)
    assert node.runtime._samples[CAMERA] is previous
    assert node._pending[CAMERA]["last_stamp"] == STAMP
    assert "rgb" in node._pending[CAMERA]


def test_extracted_validation_has_no_cache_side_effect(runtime_module):
    node = assembler(runtime_module)
    node._try_assemble(CAMERA)
    sample = node.runtime._samples.pop(CAMERA)
    validated = node.runtime.validate_sample(CAMERA, sample)
    assert not node.runtime._samples
    assert validated.base_pose_at_capture is not sample.base_pose_at_capture
    assert validated.sensor_stamp_ns == sample.sensor_stamp_ns
    with pytest.raises(GazeboRuntimeError, match="not one of"):
        node.runtime.validate_sample("foreign-camera", sample)
    assert not node.runtime._samples


def test_actual_image_callbacks_keep_buffer_alive_without_mutating_old_capture(runtime_module):
    node = assembler(runtime_module)

    def image_messages(stamp, value):
        header = NS(stamp=NS(sec=1, nanosec=stamp-1_000_000_000))
        rgb = NS(header=header, width=4, height=3, step=12, encoding="rgb8",
                 data=bytearray([value]*36))
        depth = NS(header=header, width=4, height=3, step=16, encoding="32FC1",
                   data=bytearray(np.full((3, 4), 2., dtype="<f4").tobytes()))
        return rgb, depth

    rgb, depth = image_messages(STAMP, 7)
    node._on_image(CAMERA, rgb)
    node._on_depth(CAMERA, depth)
    del rgb, depth
    gc.collect()
    node._try_assemble(CAMERA)
    sample = node.runtime._samples[CAMERA]
    rgb, depth = image_messages(STAMP+10_000_000, 9)
    node._on_image(CAMERA, rgb)
    node._on_depth(CAMERA, depth)
    del rgb, depth
    gc.collect()
    assert np.all(sample.rgb == 7)
    assert np.all(node._pending[CAMERA]["rgb"] == 9)
    node._try_assemble(CAMERA)
    assert np.all(node.runtime._samples[CAMERA].rgb == 9)
    assert np.all(sample.rgb == 7)


def test_observation_metadata_and_raw_pixels_share_one_capture_during_cache_replacement(runtime_module, monkeypatch):
    node = assembler(runtime_module)
    node._try_assemble(CAMERA)
    original = node.runtime._samples[CAMERA]
    expected_raw = node.runtime.rgbd_payload(CAMERA)
    later_pose = np.eye(4)
    later_pose[0, 3] = 1.
    newer = replace(original, rgb=np.full((3, 4, 3), 9, dtype=np.uint8),
                    sensor_stamp_ns=STAMP+10_000_000, captured_at_unix_ms=original.captured_at_unix_ms+10,
                    base_pose_at_capture=later_pose, joint_positions_at_capture={"joint": .9})
    leveled = runtime_api.leveled_base_pose

    def replace_after_snapshot(pose):
        node.runtime.record(CAMERA, newer)
        return leveled(pose)

    monkeypatch.setattr(runtime_api, "leveled_base_pose", replace_after_snapshot)
    observation = node.runtime.observation(CAMERA, observation_id="offline-race", include_raw=True)
    assert observation["robot_state"]["perception"]["sensor_stamp_ns"] == str(STAMP)
    assert observation["robot_state"]["base_pose"][0] == 0.
    assert observation["robot_state"]["joint_positions"] == {"joint": .5}
    assert observation["wall_time_unix_ms"] == original.captured_at_unix_ms
    assert observation["rgbd_frame"] == expected_raw
    assert node.runtime.rgbd_payload(CAMERA)["rgb"] == bytes([9]*36)


def test_backend_encoding_allows_feedback_and_retains_selected_capture(runtime_module):
    from tangying_robot_gateway.gazebo_backend import GazeboSkillBackend

    node = assembler(runtime_module)
    node._try_assemble(CAMERA)
    selected = node.runtime._samples[CAMERA]
    newer = node.runtime.validate_sample(CAMERA, replace(selected,
        rgb=np.full((3, 4, 3), 9, dtype=np.uint8), sensor_stamp_ns=STAMP+10_000_000))
    backend = GazeboSkillBackend.__new__(GazeboSkillBackend)
    backend.node, backend.home = node, False
    backend.perception_lock = threading.Lock()
    encoded = []

    class EncodingObserved(Exception):
        pass

    def reconstruct(frame, **_kwargs):
        encoded.append(frame)
        raise EncodingObserved

    backend.perception = NS(reconstruct=reconstruct)
    entered, release = threading.Event(), threading.Event()
    frame_for = node.runtime._frame_for

    def blocked_frame(camera, sample):
        assert sample is selected
        entered.set()
        assert release.wait(2)
        return frame_for(camera, sample)

    node.runtime._frame_for = blocked_frame
    request = NS(source_id="offline-test/"+CAMERA, streams=("robot_state",))
    suction = NS(data=json.dumps({"schemaVersion": "gazebo.suction.v1", "sequence": 1}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        observed = pool.submit(backend.observe, request)
        assert entered.wait(1)
        try:
            pool.submit(node._on_suction, suction).result(timeout=1)
            with node._lock:
                node.runtime._samples[CAMERA] = newer
            assert node._suction_sequence == 1
        finally:
            release.set()
        with pytest.raises(EncodingObserved):
            observed.result(timeout=1)
    assert encoded[0].sequence == STAMP
    assert np.all(encoded[0].rgb == 1)
    assert encoded[0].captured_at_unix_ms == selected.captured_at_unix_ms
    assert node.runtime._samples[CAMERA] is newer
