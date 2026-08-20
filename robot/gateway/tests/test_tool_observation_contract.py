from __future__ import annotations

from tangying_robot_gateway.service import RobotRuntimeService, command_from_proto
from tangying_robot_gateway.xlerobot_backend import XLeRobotDirectBackend
from tangying_robot_proto.robot.v1 import robot_pb2

from .test_service import RecordingBackend, valid_command
from .test_xlerobot_backend import FakeDriver


def test_real_runtime_advertises_content_addressed_tool_catalog():
    service = RobotRuntimeService(RecordingBackend())

    info = service.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), None)

    assert len(info.catalog_revision) == 64
    assert info.adapter_version


def test_real_runtime_rejects_stale_fencing_before_backend_motion():
    backend = RecordingBackend()
    service = RobotRuntimeService(backend)
    info = service.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), None)
    service.register_resource("block:red-block", owner=info.robot_id, token=8)
    command = valid_command()
    command.robot_id = info.robot_id
    command.catalog_revision = info.catalog_revision
    command.world_revision_basis = 42
    command.resource_id = "block:red-block"
    command.fencing_token = 7

    event = list(service.execute_for_test(command))[-1]

    assert event.code == "FENCING_TOKEN_STALE"
    assert backend.executed == []


def test_transport_identity_reaches_the_real_adapter_command():
    wire = valid_command()
    wire.robot_id = "robot-7"
    wire.catalog_revision = "a" * 64
    wire.world_revision_basis = 11
    wire.resource_id = "block:red-block"
    wire.fencing_token = 9

    command = command_from_proto(wire)

    assert command.robot_id == "robot-7"
    assert command.catalog_revision == "a" * 64
    assert command.world_revision_basis == 11
    assert command.resource_id == "block:red-block"
    assert command.fencing_token == 9


def test_physical_command_cannot_install_or_advance_resource_epoch():
    backend = RecordingBackend()
    service = RobotRuntimeService(backend)
    info = service.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), None)
    command = valid_command()
    command.robot_id = info.robot_id
    command.catalog_revision = info.catalog_revision
    command.world_revision_basis = 1
    command.resource_id = "block:red-block"
    command.fencing_token = 12

    rejected = list(service.execute_for_test(command))[-1]

    assert rejected.type == robot_pb2.SKILL_EVENT_FAILED
    assert rejected.code == "RESOURCE_GRANT_REQUIRED"
    assert backend.executed == []
    assert "block:red-block" not in service._resource_grants


def test_persisted_resource_epoch_rejects_stale_or_forged_higher_token_after_restart(tmp_path):
    from tangying_robot_gateway.journal import RuntimeJournal

    path = tmp_path / "runtime-journal.json"
    first = RobotRuntimeService(RecordingBackend(), journal=RuntimeJournal(path))
    info = first.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), None)
    first.register_resource("block:red-block", owner=info.robot_id, token=8)

    backend = RecordingBackend()
    restarted = RobotRuntimeService(backend, journal=RuntimeJournal(path))
    for token in (7, 9):
        command = valid_command()
        command.command_id = f"cmd-{token}"
        command.idempotency_key = f"key-{token}"
        command.robot_id = info.robot_id
        command.catalog_revision = info.catalog_revision
        command.world_revision_basis = 42
        command.resource_id = "block:red-block"
        command.fencing_token = token
        event = list(restarted.execute_for_test(command))[-1]
        assert event.code == "FENCING_TOKEN_STALE"

    assert backend.executed == []


def test_xlerobot_is_not_manipulation_ready_without_environment_observation_and_verification():
    backend = XLeRobotDirectBackend(FakeDriver())

    info = backend.capabilities()
    tools = {tool.name: tool for tool in info.capabilities}

    assert not info.manipulation_ready
    assert "ENTITY_PROVIDER_REQUIRED" in info.blockers
    assert "VERIFIER_REQUIRED" in info.blockers
    assert not tools["manipulation.pick"].available
    assert not tools["manipulation.place"].available
