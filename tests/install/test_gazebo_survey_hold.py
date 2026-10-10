"""Actual controller methods with deterministic clocks and ROS publisher stubs.

No simulator or physical actuator is used by these tests.
"""

import json
import threading
from types import SimpleNamespace as NS

import numpy as np
import pytest
from google.protobuf.json_format import MessageToDict
from tangying_robot_gateway import gazebo_commissioning
from tangying_robot_gateway.service_registry import ServiceRegistry
from tangying_robot_proto.robot.v1 import robot_pb2

from tests.install import test_gazebo_runtime_freshness as freshness

runtime_module = freshness.runtime_module


def controlled_step(module, monkeypatch, *, recovered_at=103.0, unsafe=None, cloud_only=False):
    clock = NS(now=100.0, sleep_hook=None)

    def sleep(duration):
        clock.now += duration
        if clock.sleep_hook:
            clock.sleep_hook()

    monkeypatch.setattr(module, "time", NS(monotonic=lambda: clock.now,
        monotonic_ns=lambda: int(clock.now*1e9), sleep=sleep))
    monkeypatch.setattr(module, "Twist", lambda: NS(linear=NS(x=0., y=0.), angular=NS(z=0.)))
    monkeypatch.setattr(gazebo_commissioning, "commissioning", dict)
    node = module.GazeboRuntimeNode.__new__(module.GazeboRuntimeNode)
    node.survey_observation_hold_seconds = 8.
    node.runtime = NS(base_pose=np.eye(4))
    node.workflow = NS(operation_id="same-operation", _operation_owner="original-owner")
    node._motion_lock = threading.Lock()
    node._obstacle_received_ns = {}
    node.motion_allowed = lambda: True
    node._obstacle_points = lambda: np.zeros((0, 3))
    node._log_step = lambda _text: None
    publications, logs = [], []
    node._cmd_vel = NS(publish=lambda message: publications.append(
        (clock.now, message.linear.x, message.linear.y, message.angular.z)))
    node.get_logger = lambda: NS(info=logs.append, warning=logs.append)

    def snapshot(**_kwargs):
        fresh = clock.now >= recovered_at
        node._obstacle_received_ns = {name: int(clock.now*1e9)
            if fresh or not cloud_only else 1 for name in module.GAZEBO_CAMERA_MOUNTS}
        blockers = list(unsafe or []) if unsafe else [] if fresh or cloud_only else ["RGBD_NOT_READY"]
        return {"ready": not blockers, "blockers": blockers,
                "checkedAtMonotonicNs": str(int(clock.now*1e9))}

    node.readiness_snapshot = snapshot
    node.readiness_blockers = lambda: snapshot()["blockers"]
    monkeypatch.setattr(module, "bounded_step_command", lambda *_args, **_kwargs:
        None if any(any(p[1:]) for p in publications) else (.05, 0.))
    monkeypatch.setattr(module, "swept_step_is_clear", lambda *_args, **_kwargs: True)
    return node, clock, publications, logs


@pytest.mark.parametrize("cloud_only", [False, True])
def test_three_second_loss_stops_then_continues_the_same_step(runtime_module, monkeypatch, cloud_only):
    node, _clock, pulses, logs = controlled_step(runtime_module, monkeypatch, cloud_only=cloud_only)
    outcome = node._bounded_step([.1, 0., 0., 1., 0., 0., 0.], threading.Event())
    assert outcome["ok"] and outcome["code"] == "STEP_COMPLETE"
    assert all(not any(p[1:]) for p in pulses if p[0] < 103.)
    assert sum(any(p[1:]) for p in pulses) == 1
    evidence = outcome["motionEvidence"]
    assert evidence["operationId"] == "same-operation"
    assert evidence["operationOwnerRequestId"] == "original-owner"
    assert evidence["nonzeroPublishedPulseCount"] == 1
    assert evidence["finalZeroPublished"] and evidence["observationHoldCount"] == 1
    assert evidence["observationHolds"][0]["resumedAtMonotonicNs"]
    decoded = [json.loads(line.removeprefix("bounded step evidence ")) for line in logs]
    assert [row["event"] for row in decoded] == ["observation_hold", "observation_resumed", "ended"]
    assert len({row["stepStartedMonotonicNs"] for row in decoded}) == 1


@pytest.mark.parametrize("fresh_after_wait", [False, True])
def test_hold_budget_expiry_never_emits_a_recovery_pulse(runtime_module, monkeypatch, fresh_after_wait):
    node, clock, pulses, _logs = controlled_step(runtime_module, monkeypatch,
        recovered_at=108. if fresh_after_wait else float("inf"))
    clock.sleep_hook = lambda: setattr(clock, "now", 108.)
    outcome = node._bounded_step([.1, 0., 0., 1., 0., 0., 0.], threading.Event())
    assert outcome["code"] == "SENSOR_STALE"
    assert all(not any(p[1:]) for p in pulses)
    assert outcome["motionEvidence"]["finalZeroPublished"]


@pytest.mark.parametrize("blocker", ["JOINT_FEEDBACK_STALE", "SUCTION_FEEDBACK_STALE", "IMU_NOT_READY", "ODOMETRY_STALE"])
def test_nonvisual_feedback_never_waits(runtime_module, monkeypatch, blocker):
    node, clock, pulses, _logs = controlled_step(runtime_module, monkeypatch, unsafe=[blocker])
    outcome = node._bounded_step([.1, 0., 0., 1., 0., 0., 0.], threading.Event())
    assert outcome["code"] == "SENSOR_STALE" and outcome["message"] == blocker
    assert clock.now == 100. and all(not any(p[1:]) for p in pulses)
    assert not outcome["motionEvidence"]["observationHolds"]


@pytest.mark.parametrize("ending", ["cancel", "estop", "deadline"])
def test_authority_ends_hold_before_any_recovery_pulse(runtime_module, monkeypatch, ending):
    node, clock, pulses, _logs = controlled_step(runtime_module, monkeypatch)
    cancel = threading.Event()

    def expire():
        if ending == "cancel":
            cancel.set()
        elif ending == "estop":
            node.motion_allowed = lambda: False
        else:
            clock.now = 146.

    clock.sleep_hook = expire
    outcome = node._bounded_step([.1, 0., 0., 1., 0., 0., 0.], cancel)
    assert outcome["code"] == {"cancel": "CANCELLED", "estop": "EMERGENCY_STOP_LATCHED", "deadline": "STEP_TIMEOUT"}[ending]
    assert all(not any(p[1:]) for p in pulses)
    assert outcome["motionEvidence"]["finalZeroPublished"]


def test_geometry_work_crossing_deadline_cannot_emit_one_last_pulse(runtime_module, monkeypatch):
    node, clock, pulses, _logs = controlled_step(runtime_module, monkeypatch, recovered_at=100.)

    def guard(*_args, **_kwargs):
        clock.now = 146.
        return True

    monkeypatch.setattr(runtime_module, "swept_step_is_clear", guard)
    outcome = node._bounded_step([.1, 0., 0., 1., 0., 0., 0.], threading.Event())
    assert outcome["code"] == "STEP_TIMEOUT" and all(not any(p[1:]) for p in pulses)


@pytest.mark.parametrize("expired", ["deadline", "cancel", "estop"])
def test_publication_rechecks_authority_after_sensor_lock_wait(runtime_module, monkeypatch, expired):
    node, clock, pulses, _logs = controlled_step(runtime_module, monkeypatch, recovered_at=100.)
    cancel = threading.Event()

    def readiness():
        if expired == "deadline":
            clock.now = 146.
        elif expired == "cancel":
            cancel.set()
        else:
            node.motion_allowed = lambda: False
        return []

    node.readiness_blockers = readiness
    receipt = node._publish_velocity(.1, .1, deadline_monotonic=145., cancel=cancel)
    assert receipt == {"published": True, "nonzero": False}
    assert all(not any(p[1:]) for p in pulses)


def test_hold_requires_a_real_zero_publisher(runtime_module, monkeypatch):
    node, clock, _pulses, _logs = controlled_step(runtime_module, monkeypatch)
    node._cmd_vel = None
    outcome = node._bounded_step([.1, 0., 0., 1., 0., 0., 0.], threading.Event())
    assert outcome["code"] == "MOTION_PUBLISHER_UNAVAILABLE" and clock.now == 100.
    assert not outcome["motionEvidence"]["finalZeroPublished"]


def test_zero_publication_does_not_wait_on_sensor_readiness(runtime_module, monkeypatch):
    node, _clock, pulses, _logs = controlled_step(runtime_module, monkeypatch)
    node.readiness_blockers = lambda: pytest.fail("zero must bypass a busy sensor lock")
    assert node._publish_velocity(0., 0.) == {"published": True, "nonzero": False}
    assert pulses == [(100., 0., 0., 0.)]


def test_readiness_service_retains_exact_stamps_and_never_grants_authority(runtime_module, monkeypatch):
    stamp = 10**18 + 123
    monkeypatch.setattr(runtime_module, "time", NS(monotonic_ns=lambda: stamp+100))
    node = freshness.node_with_samples(runtime_module, stamp)
    node.runtime.robot_id = "gazebo-test"
    node._odom_stamp_ns = stamp-7
    registry = ServiceRegistry(node.runtime.robot_id)
    runtime_module.register_runtime_diagnostics(registry, node)
    service = registry.services["runtime.readiness"]
    assert not service.mutates_world and service.authority is None
    assert service.contract["effects"] == ["READ"] and service.contract["resources"] == []
    assert "cameras" not in service.contract["planningFields"]
    response = registry.call(robot_pb2.ServiceRequest(robot_id=node.runtime.robot_id, name=service.name))
    result = MessageToDict(response.result)
    assert response.ok and result["ready"]
    assert result["scope"] == "current_sensor_state" and result["phase"] == "read_only_diagnostics"
    assert result["historicalBoundedStep"] is None
    assert result["evidenceIndex"]["emptyCacheDoesNotMeanEmptyHistory"]
    assert result["checkedAtMonotonicNs"] == str(stamp+100)
    assert result["cameras"]["base-rgbd"]["captureMonotonicNs"] == str(stamp)
    assert result["odometrySensorStampNs"] == str(stamp-7)
    node._odom_received_ns = stamp-2_000_000_000
    assert node.readiness_snapshot()["blockers"] == ["ODOMETRY_STALE"]


def test_historical_hold_receipt_is_fsynced_and_separate_from_current_readiness(
    runtime_module, monkeypatch, tmp_path,
):
    node, clock, _pulses, _logs = controlled_step(runtime_module, monkeypatch)
    node._bounded_evidence_path = str(tmp_path / "bounded-steps.jsonl")
    original_fsync = runtime_module.os.fsync
    synced = []

    def sync(descriptor):
        original_fsync(descriptor)
        synced.append(True)

    monkeypatch.setattr(runtime_module.os, "fsync", sync)
    outcome = node._bounded_step([.1, 0., 0., 1., 0., 0., 0.], threading.Event())
    rows = [json.loads(line) for line in (tmp_path / "bounded-steps.jsonl").read_text().splitlines()]
    assert len(rows) == 3 and len(synced) == 2*len(rows)  # File and containing directory.
    assert rows[-1]["outcomeCode"] == outcome["code"]
    assert all(row["scope"] == "historical_bounded_step_transition" for row in rows)
    assert len({row["operationId"] for row in rows}) == 1
    original = json.loads(json.dumps(rows[-1]))
    clock.now += 50
    diagnostics = node.runtime_diagnostics()
    assert diagnostics["scope"] == "current_sensor_state"
    assert diagnostics["historicalBoundedStep"] == original
    assert diagnostics["evidenceIndex"]["boundedStepJournal"] == node._bounded_evidence_path


def test_failed_evidence_fsync_stops_without_claiming_persistence(runtime_module, monkeypatch, tmp_path):
    node, _clock, pulses, _logs = controlled_step(runtime_module, monkeypatch)
    node._bounded_evidence_path = str(tmp_path / "bounded-steps.jsonl")

    def failed_sync(_descriptor):
        raise OSError("test disk failure")

    monkeypatch.setattr(runtime_module.os, "fsync", failed_sync)
    with pytest.raises(OSError, match="test disk failure"):
        node._bounded_step([.1, 0., 0., 1., 0., 0., 0.], threading.Event())
    assert all(not any(p[1:]) for p in pulses)
    assert not getattr(node, "_last_bounded_evidence", None)


def test_get_info_never_duplicates_readiness_blocker(runtime_module):
    node = NS(readiness_blockers=lambda: ["RGBD_NOT_READY"],
              runtime=NS(runtime_info=lambda **_kwargs: {"cameras": ["base-rgbd"]}))
    servicer = runtime_module.RuntimeServicer.__new__(runtime_module.RuntimeServicer)
    servicer._node = node
    servicer._skills = NS(GetRuntimeInfo=lambda *_args: robot_pb2.RuntimeInfo(blockers=["RGBD_NOT_READY"]))
    result = servicer.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), None)
    assert list(result.blockers) == ["RGBD_NOT_READY"] and not result.manipulation_ready


@pytest.mark.parametrize("value", ["0", "-1", "15.01", "nan", "inf", "oops", True])
def test_invalid_hold_duration_is_rejected(runtime_module, value):
    with pytest.raises((ValueError, TypeError)):
        runtime_module.survey_hold_seconds(value)


@pytest.mark.parametrize("configured,expected", [
    (None, "/data/maps/gazebo-runtime"),
    ("/data/maps/home_furnished/runtime", "/data/maps/home_furnished/runtime"),
])
def test_runtime_and_diagnostic_journals_share_the_configured_persistent_root(
    runtime_module, monkeypatch, configured, expected,
):
    monkeypatch.delenv("TANGYING_GAZEBO_RUNTIME_ROOT", raising=False)
    # The previous bug used this unrelated name and escaped the mounted volume.
    monkeypatch.setenv("TANGYING_RUNTIME_ROOT", "/unmounted/wrong-root")
    if configured is not None:
        monkeypatch.setenv("TANGYING_GAZEBO_RUNTIME_ROOT", configured)
    assert runtime_module.gazebo_runtime_root() == expected
