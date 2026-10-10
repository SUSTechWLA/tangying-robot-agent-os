"""The original grasp input must survive postcondition capture and restart."""

import copy
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from tangying_robot_gateway.arm_kinematics import arm_links
from tangying_robot_gateway.backend import RobotBackend
from tangying_robot_gateway.gazebo_manipulation import GazeboManipulation, pose_matrix
from tangying_robot_gateway.journal import RuntimeJournal
from tangying_robot_gateway.runtime import Observation, RuntimeInfo, SceneEntity
from tangying_robot_gateway.service import RobotRuntimeService
from tangying_robot_proto.robot.v1 import robot_pb2

BASE = [2.051344551251087, 3.0002529952830903, .035,
        .7667298993953489, 0., 0., .6419698290209582]
TARGET = [2.4515001112462356, 3.333774199761013, .7909868361224217]
DESTINATION = [2.458995248850189, 3.3350008908945, .7309792842265203]


class PlanningBackend(RobotBackend):
    """Real pure FK/IK planner; every actuation entry point is forbidden."""

    def __init__(self):
        self.executed = []
        self.home = True
        self.cancel_event = threading.Event()
        joints = {link.motor: value for side in ("left", "right")
                  for link, value in zip(arm_links(side), (0., 3.1, 1., 0., 0., 0.), strict=True)}
        self.node = SimpleNamespace(
            runtime=SimpleNamespace(base_pose=pose_matrix(BASE), robot_id="recorded-robot",
                                    calibration_revision="recorded-calibration"),
            joint_snapshot=lambda: (dict(joints), 0., 1),
        )
        self.controller = GazeboManipulation(self)
        self.node.suction_snapshot = lambda: {"attached": False,
                                             "toolCommissioning": self.controller.tool}
        self.input_capture = None
        self.completion_capture = None

    def capabilities(self):
        return RuntimeInfo("recorded-robot", "offline-planner", True, [],
                           catalog_revision="recorded-catalog")

    def observe(self, request):
        if self.completion_capture is not None:
            return self.completion_capture
        frame = Observation(
            observation_id="recorded-robot/head-rgbd-plan-source",
            wall_time_unix_ms=int(time.time()*1000), monotonic_time_ns=time.monotonic_ns(),
            robot_state={
                "base_pose": list(BASE),
                "perception": {"sensor_stamp_ns": "9007199254740991",
                               "calibration_revision": self.node.runtime.calibration_revision},
            },
            entities=[SceneEntity("ceramic-mug", "cup", {"recognition": "rgbd_metric_shape"},
                                  [*TARGET, 1., 0., 0., 0.], .90),
                      SceneEntity("kitchen-tray", "storage_bin", {},
                                  [*DESTINATION, 1., 0., 0., 0.], .90)],
            reconstruction={"robotId": "recorded-robot", "entities": []},
        )
        self.input_capture = frame
        return frame

    def execute(self, command):
        self.executed.append(command.capability)
        return self.controller.execute(command)

    def wait_command_capture(self, command, completed_ns, completed_ms):
        # This is intentionally a different source, as the real service pins a
        # capture after execution instead of persisting the backend input ID.
        self.completion_capture = Observation("post-command-new-capture", completed_ms+1, completed_ns+1)
        return True

    def stop(self, reason):
        raise AssertionError(f"offline planning must not actuate: {reason}")


def command(skill="plan_grasp"):
    request = robot_pb2.SkillCommand(
        schema_version="robot.v1", command_id=f"command-{skill}", task_id="task-source-audit",
        skill=skill, robot_id="recorded-robot", catalog_revision="recorded-catalog",
        deadline_unix_ms=int(time.time()*1000)+30_000, lease_ms=30_000,
        idempotency_key=f"source-audit-{skill}", safety_profile="desktop_standard",
        approval_id="offline-only", target_ref="ceramic-mug" if skill == "manipulation.pick" else "",
    )
    if skill == "plan_grasp":
        request.parameters.update({"objectId": "ceramic-mug", "destinationId": "kitchen-tray"})
    return request


@pytest.mark.parametrize("fail_pick", [False, True])
def test_original_grasp_source_survives_gateway_capture_and_durable_replay(tmp_path, monkeypatch, fail_pick):
    import tangying_robot_gateway.gazebo_manipulation as module

    def no_motion(*args, **kwargs):
        pytest.fail("source audit must never dispatch a joint target")

    monkeypatch.setattr(module, "execute_chunk", no_motion)
    backend = PlanningBackend()
    path = tmp_path / "journal.json"
    service = RobotRuntimeService(backend, RuntimeJournal(path))
    request = command()
    request.catalog_revision = service.GetRuntimeInfo(None, None).catalog_revision
    terminal = list(service.execute_for_test(request))[-1]
    assert terminal.type == robot_pb2.SKILL_EVENT_SUCCEEDED, terminal.message
    assert terminal.observation_id == terminal.evidence_observation.observation_id == "post-command-new-capture"
    original_message = json.loads(terminal.message)
    source = original_message["planSource"]
    assert source["captureId"] == backend.input_capture.observation_id != terminal.observation_id
    assert source["sensorStampNs"] == "9007199254740991"
    assert source["robotId"] == request.robot_id
    assert source["taskId"] == request.task_id
    assert source["commandId"] == request.command_id
    assert source["catalogRevision"] == request.catalog_revision
    assert source["calibrationRevision"] == "recorded-calibration"
    assert source["position"] == TARGET
    assert np.allclose(source["base"], pose_matrix(BASE))
    assert original_message["physicalStarted"] is False
    canonical_source = json.dumps(source, sort_keys=True, separators=(",", ":"))
    assert original_message["sourceToken"] == hashlib.sha256(canonical_source.encode()).hexdigest()

    # Later mutation of the provider object and live base cannot rewrite the
    # serialized input retained by the plan or the already journaled result.
    backend.input_capture.entities[0].pose_xyz_quat[0] += 5.
    backend.input_capture.robot_state["base_pose"][0] += 5.
    assert backend.controller.plan["sourceJSON"] == canonical_source
    assert json.loads(terminal.message) == original_message
    if fail_pick:
        backend.completion_capture = None
        backend.node.runtime.calibration_revision = "other-calibration"
        request = command("manipulation.pick")
        request.catalog_revision = service.GetRuntimeInfo(None, None).catalog_revision
        terminal = list(service.execute_for_test(request))[-1]
        assert terminal.type == robot_pb2.SKILL_EVENT_FAILED
        diagnostic = json.loads(terminal.message)
        assert diagnostic["planSource"] == source
        assert diagnostic["sourceToken"] == original_message["sourceToken"]
        assert diagnostic["physicalStarted"] is False
        assert any(phase.get("rejectReason") == "GRASP_CALIBRATION_CHANGED"
                   for phase in diagnostic["phases"])
        assert not terminal.HasField("evidence_observation")

    expected = copy.deepcopy(terminal)
    restarted = PlanningBackend()
    replay = list(RobotRuntimeService(restarted, RuntimeJournal(path)).execute_for_test(request))[-1]
    assert replay.SerializeToString(deterministic=True) == expected.SerializeToString(deterministic=True)
    assert restarted.executed == []
    assert restarted.input_capture is None
    assert restarted.controller.plan is None
