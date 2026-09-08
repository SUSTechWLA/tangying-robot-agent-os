"""Run in ROS CI/container: real node, messages and SQLite, no external actuation."""

import math
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

rclpy = pytest.importorskip("rclpy")
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import TransformStamped
from tangying_navigation import navigation_node as module


class IsolatedAction:
    def __init__(self):
        self.sent = []

    def server_is_ready(self):
        return True

    def send_goal_async(self, goal):
        self.sent.append(goal)
        return Future()


class MeasuredTransforms:
    def __init__(self):
        self.actual_x = 1.04
        self.actual_yaw = 0
        self.map_odom_stamp_offset = 0

    def lookup_transform(self, target, source, _time):
        assert target == "map" and source in {"odom", "base_link"}
        value = TransformStamped()
        value.header.frame_id, value.child_frame_id = target, source
        stamp = module.now_ms() + (self.map_odom_stamp_offset if source == "odom" else 0)
        value.header.stamp.sec, value.header.stamp.nanosec = stamp // 1000, stamp % 1000 * 1000000
        value.transform.translation.x = 1.0 if source == "odom" else self.actual_x
        yaw = 0 if source == "odom" else self.actual_yaw
        value.transform.rotation.w = math.cos(yaw / 2)
        value.transform.rotation.z = math.sin(yaw / 2)
        return value


@pytest.fixture
def node(monkeypatch, tmp_path):
    monkeypatch.setenv("TANGYING_NAVIGATION_TOKEN", "isolated-navigation-test-token")
    monkeypatch.setenv("TANGYING_NAVIGATION_PORT", "0")
    monkeypatch.setenv("TANGYING_NAVIGATION_GOAL_DATABASE", str(tmp_path / "goals.sqlite"))
    action = IsolatedAction()
    monkeypatch.setattr(module, "ActionClient", lambda *_args, **_kwargs: action)
    rclpy.init()
    value = module.NavigationNode()
    value.buffer = MeasuredTransforms()
    value.map_data = {"knownCells": 20, "mapRevision": "measured-test-map"}
    stamp = module.now_ms()
    value.sensor_ms = {"base": stamp, "head": stamp}
    value.info_ms = value.odom_ms = stamp
    value.info_ref = 1
    value.visual_quality = {"ready": True, "currentFrameWords": 30, "dictionaryWords": 50}
    yield value
    value.destroy_node()
    rclpy.shutdown()


def goal():
    return {"commandId": "repeat-approach", "goalPose": [.05, 0, 0, 1, 0, 0, 0], "frameId": "odom"}


def test_actual_node_confirms_existing_pose_after_tf_binding_without_action_or_velocity(node):
    result = node.registry.submit(goal())
    assert result["state"] == "SUCCEEDED" and result["message"] == "POSE_ALREADY_CONFIRMED"
    assert result["completionSource"] == "pose_confirmation"
    assert result["goalPoseMap"][0] == pytest.approx(1.05)
    assert abs(result["mapPose"][0] - result["goalPoseMap"][0]) == pytest.approx(.01)
    assert not node.action.sent and not node.goal_handles and node.velocity_gate.goal_id is None
    assert not result["velocityValid"] and result["latestCmdVel"]["linearX"] == 0
    repeated = node.registry.submit(goal())
    assert repeated["completionPoseObservedAtUnixMs"] == result["completionPoseObservedAtUnixMs"]
    assert not node.action.sent


@pytest.mark.parametrize("actual_x,actual_yaw", [(1.0, 0), (1.04, .041)])
def test_position_or_yaw_outside_confirmation_budget_still_submits_real_map_goal(node, actual_x, actual_yaw):
    node.buffer.actual_x, node.buffer.actual_yaw = actual_x, actual_yaw
    result = node.registry.submit(goal())
    assert result["state"] == "PENDING" and result["completionSource"] == ""
    assert len(node.action.sent) == 1
    sent = node.action.sent[0].pose
    assert sent.header.frame_id == "map" and sent.pose.position.x == pytest.approx(1.05)
    node.buffer.actual_x, node.buffer.actual_yaw = 1.05, 0
    completed = Future()
    completed.set_result(SimpleNamespace(status=GoalStatus.STATUS_SUCCEEDED))
    node.goal_result(result["goalId"], completed)
    finished = node.registry.status(result["goalId"])
    assert finished["state"] == "SUCCEEDED" and finished["completionSource"] == "nav2_action"
    assert finished["completionPoseObservedAtUnixMs"] > 0


@pytest.mark.parametrize("offset", [-1001, 251])
def test_stale_or_future_input_transform_never_confirms_or_dispatches(node, offset):
    node.buffer.map_odom_stamp_offset = offset
    result = node.registry.submit(goal())
    assert result["state"] == "FAILED" and result["completionSource"] == ""
    assert not node.action.sent and node.velocity_gate.goal_id is None


def test_sensor_loss_between_submission_and_confirmation_fails_without_action(node, monkeypatch):
    original = node.map_status
    calls = 0

    def status(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 1:
            node.sensor_ms["base"] -= 2000
        return result

    monkeypatch.setattr(node, "map_status", status)
    result = node.registry.submit(goal())
    assert result["state"] == "FAILED" and result["completionSource"] == ""
    assert not node.action.sent and node.velocity_gate.goal_id is None


def test_aborted_nav2_callback_freezes_actual_diagnostics_before_recovery(node):
    node.buffer.actual_x = 1.0
    result = node.registry.submit(goal())
    stamp = module.now_ms()
    node.sensor_ms = {"base": stamp - 700, "head": stamp - 750}
    original = node.map_status()
    assert original["ready"]
    aborted = Future()
    aborted.set_result(SimpleNamespace(status=GoalStatus.STATUS_ABORTED))
    node.goal_result(result["goalId"], aborted)
    failed = node.registry.status(result["goalId"])
    assert failed["state"] == "FAILED" and failed["message"] == "NAV2_ACTION_ENDED"
    frozen = failed["failureObservation"]
    assert frozen["ready"] and frozen["inputAgeMs"]["base"] >= 700
    node.sensor_ms = {"base": module.now_ms(), "head": module.now_ms()}
    assert node.registry.status(result["goalId"])["failureObservation"] == frozen
