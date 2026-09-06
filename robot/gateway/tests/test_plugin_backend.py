from __future__ import annotations

import copy
import time

import grpc
import pytest
from google.protobuf.json_format import ParseDict
from tangying_robot_gateway.backend import BackendResult, RobotBackend
from tangying_robot_gateway.journal import RuntimeJournal
from tangying_robot_gateway.runtime import ObservationRequest
from tangying_robot_gateway.service import RobotRuntimeService, command_from_proto
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

from .test_plugin_contracts import arm_profile, scene
from .test_service import valid_command


def plugin(*, provider=None, handlers=None, physical_ready=None):
    from tangying_robot_gateway.plugin_backend import PluginBackend

    frame = scene()
    return PluginBackend(
        arm_profile(), observation_provider=provider or (lambda: copy.deepcopy(frame)),
        handlers=handlers or {"arm.move": lambda command: BackendResult(True)},
        stop=lambda reason: None,
        physical_ready=physical_ready,
    )


def command_for(service, values=None):
    info = service.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), None)
    command = valid_command()
    command.skill = "arm.move"
    command.robot_id = info.robot_id
    command.catalog_revision = info.catalog_revision
    ParseDict(values or {"action_chunk": [{"axis1.position": 0.5}]}, command.parameters)
    return command


def test_plugin_does_not_advertise_unsupported_tools_or_arm_hardware_implicitly():
    backend = plugin()
    snapshot = backend.capabilities()
    capabilities = {tool.name: tool for tool in snapshot.capabilities}
    assert capabilities["observe_scene"].available
    assert not capabilities["arm.move"].available
    assert "ROBOT_NOT_ARMED" in capabilities["arm.move"].blockers
    assert not capabilities["manipulation.pick"].available
    assert "TOOL_HANDLER_REQUIRED" in capabilities["manipulation.pick"].blockers


def test_new_actuator_keys_pass_the_same_runtime_safety_and_durable_replay(tmp_path):
    executed = []

    def move(command):
        executed.append(command.parameters["action_chunk"][0]["axis1.position"])
        return BackendResult(True)

    path = tmp_path / "journal.json"
    backend = plugin(handlers={"arm.move": move}, physical_ready=lambda: True)
    first = RobotRuntimeService(backend, RuntimeJournal(path))
    command = command_for(first)
    terminal = list(first.execute_for_test(command))[-1]
    second = RobotRuntimeService(backend, RuntimeJournal(path))
    replay = list(second.execute_for_test(command))[-1]
    assert terminal.type == replay.type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert executed == [0.5]


@pytest.mark.parametrize("values", [
    {"action_chunk": [{"left_arm_shoulder_pan.pos": 0.5}]},
    {"action_chunk": [{"axis1.position": 2.01}]},
    {"action_chunk": [{"axis1.position": True}]},
    {"action_chunk": [{}]},
    {"action_chunk": [{"axis1.position": 0.5}], "ignored_dangerous_setting": 5},
])
def test_profile_invalid_action_never_reaches_actuator(values):
    executed = []
    service = RobotRuntimeService(plugin(
        handlers={"arm.move": lambda command: executed.append(command) or BackendResult(True)},
        physical_ready=lambda: True,
    ))
    result = list(service.execute_for_test(command_for(service, values)))[-1]
    assert result.type == robot_pb2.SKILL_EVENT_FAILED
    assert executed == []


def test_profile_motion_still_requires_approval_and_current_catalog():
    service = RobotRuntimeService(plugin(physical_ready=lambda: True))
    command = command_for(service)
    command.approval_id = ""
    assert list(service.execute_for_test(command))[-1].code == "APPROVAL_REQUIRED"
    command = command_for(service)
    command.idempotency_key = "new-key"
    command.catalog_revision = "old-catalog"
    assert list(service.execute_for_test(command))[-1].code == "TOOL_CATALOG_STALE"


def test_reconstruction_remains_authoritative_over_legacy_entity_projection():
    observed_ms = int(time.time() * 1000) - 100
    backend = plugin(provider=lambda: scene(observed_ms))
    observation = backend.observe(ObservationRequest())
    assert observation.wall_time_unix_ms == observed_ms
    assert observation.entities[0].entity_id == "cup-1"
    assert observation.entities[0].pose_xyz_quat == [0.1, 0.2, 0.3, 1, 0, 0, 0]
    assert observation.reconstruction["sourceFrameId"] == "depth_optical"


def test_invalid_observation_cannot_be_used_to_report_motion_success():
    bad = scene()
    bad["units"] = "mm"
    executed = []
    service = RobotRuntimeService(plugin(
        provider=lambda: bad,
        handlers={"arm.move": lambda command: executed.append(command) or BackendResult(True)},
        physical_ready=lambda: True,
    ))
    result = list(service.execute_for_test(command_for(service)))[-1]
    assert result.type == robot_pb2.SKILL_EVENT_FAILED
    assert result.code == "RECONSTRUCTION_INVALID"
    assert executed == []


def test_provider_sequence_rewind_and_rewritten_frame_are_rejected():
    payload = scene()
    backend = plugin(provider=lambda: copy.deepcopy(payload))
    backend.observe(ObservationRequest())
    backend.observe(ObservationRequest())  # unchanged frame may be polled until it becomes stale
    payload["entities"][0]["pose"][0] = 0.5
    with pytest.raises(ValueError, match="sequence"):
        backend.observe(ObservationRequest())
    payload["sequence"] = 2
    payload["observationId"] = "depth-2"
    backend.observe(ObservationRequest())
    payload["sequence"] = 1
    with pytest.raises(ValueError, match="sequence"):
        backend.observe(ObservationRequest())


def test_real_grpc_boundary_carries_profile_and_validated_reconstruction():
    from concurrent.futures import ThreadPoolExecutor

    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    robot_pb2_grpc.add_RobotRuntimeServicer_to_server(RobotRuntimeService(plugin()), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            client = robot_pb2_grpc.RobotRuntimeStub(channel)
            info = client.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), timeout=2)
            observation = next(client.Observe(robot_pb2.ObserveRequest(), timeout=2))
            assert info.robot_profile["modelId"] == "six-axis-arm"
            assert observation.reconstruction["units"] == "m"
            assert observation.entities[0].entity_id == "cup-1"
    finally:
        server.stop(0).wait()


def test_profiled_grpc_rejects_invalid_source_without_returning_partial_entities():
    from concurrent.futures import ThreadPoolExecutor

    payload = scene()
    payload["sourceId"] = "unknown"
    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    robot_pb2_grpc.add_RobotRuntimeServicer_to_server(
        RobotRuntimeService(plugin(provider=lambda: payload)), server,
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            client = robot_pb2_grpc.RobotRuntimeStub(channel)
            with pytest.raises(grpc.RpcError) as error:
                next(client.Observe(robot_pb2.ObserveRequest(), timeout=2))
            assert error.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    finally:
        server.stop(0).wait()


def test_integer_observe_rate_survives_protobuf_struct_number_representation():
    service = RobotRuntimeService(plugin())
    command = command_for(service, {"max_rate_hz": 1})
    command.skill = "observe_scene"
    result = list(service.execute_for_test(command))[-1]
    assert result.type == robot_pb2.SKILL_EVENT_SUCCEEDED


def test_missing_optional_handler_does_not_block_an_available_tool():
    info = plugin(physical_ready=lambda: True).capabilities()
    assert info.blockers == []
    assert next(item for item in info.capabilities if item.name == "arm.move").available
    assert not next(item for item in info.capabilities if item.name == "manipulation.pick").available


def test_service_enforces_reconstruction_for_a_custom_profile_backend():
    executed = []
    delegate = plugin(physical_ready=lambda: True)

    class CustomProfileBackend(RobotBackend):
        def capabilities(self):
            return delegate.capabilities()

        def observe(self, request):
            result = delegate.observe(request)
            result.reconstruction["units"] = "mm"
            return result

        def execute(self, command):
            executed.append(command)
            return BackendResult(True)

        def stop(self, reason):
            pass

    service = RobotRuntimeService(CustomProfileBackend())
    result = list(service.execute_for_test(command_for(service)))[-1]
    assert result.code == "RECONSTRUCTION_INVALID"
    assert executed == []


def test_matching_planner_target_ref_reaches_handler_but_conflict_does_not():
    executed = []
    backend = plugin(handlers={"arm.move": lambda value: executed.append(value) or BackendResult(True)},
                     physical_ready=lambda: True)
    service = RobotRuntimeService(backend)
    values = {"targetRef": "red-cup", "action_chunk": [{"axis1.position": 0.5}]}
    command = command_for(service, values)
    assert command.target_ref == "red-cup"
    assert list(service.execute_for_test(command))[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert len(executed) == 1
    bad = command_for(service, dict(values, targetRef="different-cup"))
    bad.command_id, bad.idempotency_key = "conflict", "conflict"
    assert list(service.execute_for_test(bad))[-1].code == "TOOL_PARAMETERS_INVALID"
    assert backend.execute(command_from_proto(bad)).code == "TOOL_PARAMETERS_INVALID"
    assert len(executed) == 1


def test_explicit_empty_profile_cannot_downgrade_to_the_legacy_boundary():
    from .test_service import RecordingBackend

    class EmptyProfileBackend(RecordingBackend):
        def capabilities(self):
            info = super().capabilities()
            info.robot_profile = {}
            return info

    with pytest.raises(ValueError):
        RobotRuntimeService(EmptyProfileBackend())
