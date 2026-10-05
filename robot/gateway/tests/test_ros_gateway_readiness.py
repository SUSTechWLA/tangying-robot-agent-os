"""Offline legacy ROS boundary checks; no ROS graph or robot is started."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from tangying_robot_gateway.journal import RuntimeJournal
from tangying_robot_gateway.local_recovery import exclusive_runtime
from tangying_robot_gateway.runtime import Command
from tangying_robot_gateway.service import RobotRuntimeService
from tangying_robot_proto.robot.v1 import robot_pb2


@pytest.fixture
def ros_gateway(monkeypatch):
    class Node:
        def destroy_node(self):
            self.destroyed = True

    for name, module in {
        "rclpy": NS(), "rclpy.action": NS(ActionClient=object),
        "rclpy.node": NS(Node=Node), "std_msgs": NS(),
        "std_msgs.msg": NS(Bool=object, Int64=object, String=object),
        "tangying_robot_msgs": NS(), "tangying_robot_msgs.action": NS(ExecuteSkill=object),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).parents[2] / "ros2_ws/src/tangying_robot_gateway/tangying_ros_gateway/node.py"
    spec = importlib.util.spec_from_file_location("legacy_ros_gateway_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def node_for(module, path, *, action_ready=True):
    node = module.GatewayNode.__new__(module.GatewayNode)
    parameters = {"runtime_journal": str(path), "allow_insecure": True,
                  "grpc_listen": "127.0.0.1:0", "server_key": "unused-key",
                  "server_cert": "unused-cert", "client_ca": "unused-ca"}
    node.get_parameter = lambda name: NS(value=parameters[name])
    node._action = NS(wait_for_server=lambda **_: action_ready)
    node.dispatches = []
    node.execute = lambda command: node.dispatches.append(command)
    return node


def command(skill="observe_scene"):
    return Command(schema_version="robot.v1", command_id="cmd-1", task_id="task-1",
                   capability=skill, deadline_unix_ms=int(time.time() * 1000) + 10000,
                   lease_ms=5000, idempotency_key="task-1-step-1",
                   safety_profile="desktop_standard", approval_id="approval-1")


@pytest.mark.parametrize("action_ready", [False, True])
def test_ros_action_server_cannot_advertise_missing_verification_or_recovery(ros_gateway, tmp_path, action_ready):
    node = node_for(ros_gateway, tmp_path / "runtime.json", action_ready=action_ready)
    backend = ros_gateway.ROSBackend(node)
    info = backend.capabilities()
    tools = {item.name: item for item in info.capabilities}
    assert not info.manipulation_ready
    assert "VERIFIER_REQUIRED" in info.blockers
    assert ("ROS_ACTION_SERVER_UNAVAILABLE" in info.blockers) is not action_ready
    for name in ("verify_grasp", "verify_placement", "verify_arrival", "manipulation.pick", "manipulation.place"):
        assert not tools[name].available
        assert "VERIFIER_REQUIRED" in tools[name].blockers
        result = backend.execute(command(name))
        assert not result.success
        assert result.confidence == 0
    assert not tools["recover_to_safe_pose"].available
    assert not tools["recover_to_safe_pose"].recoverable
    assert backend.execute(command("recover_to_safe_pose")).code == "RECOVERY_POLICY_REQUIRED"
    assert node.dispatches == [], "missing verification cannot be bypassed by directly dispatching a skill"


class OfflineServer:
    def __init__(self, backend, journal):
        self.service = RobotRuntimeService(backend, journal=journal)
        self.stopped = False
        self.waited = False

    def stop(self, *, grace):
        self.stopped = True
        return self

    def wait(self):
        self.waited = True


def install_server(monkeypatch, module):
    def start(backend, address, *, journal, **security):
        return OfflineServer(backend, journal)
    monkeypatch.setattr(module, "start_server", start)


def test_ros_gateway_restart_retains_estop_and_replays_completed_command(ros_gateway, monkeypatch, tmp_path):
    install_server(monkeypatch, ros_gateway)
    path = tmp_path / "state" / "runtime.json"
    first = node_for(ros_gateway, path)
    first._grpc_server = first._start_gateway()
    journal = first._grpc_server.service.journal
    assert journal.path == path
    journal.set_estop(True, "ATTENDED_STOP")
    original = command()
    terminal = robot_pb2.SkillEvent(command_id=original.command_id,
                                   type=robot_pb2.SKILL_EVENT_SUCCEEDED, code="ORIGINAL_RESULT")
    journal.record(original.idempotency_key, RobotRuntimeService._fingerprint(original),
                   [terminal.SerializeToString().hex()])
    with pytest.raises(RuntimeError, match="already owns"), exclusive_runtime(path):
        pytest.fail("live gateway must exclude local recovery and a second owner")
    first.destroy_node()
    assert first._grpc_server.stopped and first._grpc_server.waited and first.destroyed

    second = node_for(ros_gateway, path)
    second._grpc_server = second._start_gateway()
    try:
        service = second._grpc_server.service
        assert service.safety.estop_latched
        assert service.safety.last_stop_reason == "ATTENDED_STOP"
        assert service.invoke(original).code == "ORIGINAL_RESULT"
        assert second.dispatches == [], "restart must replay durable evidence without dispatching again"
    finally:
        second.destroy_node()


def test_ros_gateway_restart_keeps_uncertain_command_blocked(ros_gateway, monkeypatch, tmp_path):
    install_server(monkeypatch, ros_gateway)
    path = tmp_path / "runtime.json"
    original = command("manipulation.pick")
    journal = RuntimeJournal(path)
    journal.begin(original.idempotency_key, RobotRuntimeService._fingerprint(original))
    node = node_for(ros_gateway, path)
    node._grpc_server = node._start_gateway()
    try:
        assert node._grpc_server.service.safety.estop_latched
        result = node._grpc_server.service.invoke(original)
        assert not result.success
        assert result.code == "EXECUTION_OUTCOME_UNKNOWN"
        assert node.dispatches == []
    finally:
        node.destroy_node()


def test_failed_ros_gateway_start_releases_journal_ownership(ros_gateway, monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise OSError("TLS credentials unreadable")
    monkeypatch.setattr(ros_gateway, "start_server", fail)
    path = tmp_path / "runtime.json"
    with pytest.raises(OSError, match="TLS credentials"):
        node_for(ros_gateway, path)._start_gateway()
    with exclusive_runtime(path):
        assert True


def test_failed_ros_server_shutdown_retains_journal_ownership(ros_gateway, monkeypatch, tmp_path):
    install_server(monkeypatch, ros_gateway)
    path = tmp_path / "runtime.json"
    node = node_for(ros_gateway, path)
    node._grpc_server = node._start_gateway()
    original_wait = node._grpc_server.wait

    def fail():
        raise RuntimeError("RPC shutdown failed")

    monkeypatch.setattr(node._grpc_server, "wait", fail)
    try:
        with pytest.raises(RuntimeError, match="RPC shutdown failed"):
            node.destroy_node()
        with pytest.raises(RuntimeError, match="already owns"), exclusive_runtime(path):
            pytest.fail("failed shutdown must retain ownership")
    finally:
        monkeypatch.setattr(node._grpc_server, "wait", original_wait)
        node.destroy_node()


def test_ros_gateway_rejects_empty_journal_configuration(ros_gateway, tmp_path):
    with pytest.raises(ValueError, match="durable journal"):
        node_for(ros_gateway, "  ")._start_gateway()
