"""Contract and admission checks only; these adapters never operate hardware."""
from __future__ import annotations

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import grpc
import pytest
from google.protobuf.json_format import ParseDict
from tangying_robot_gateway.contracts import RobotProfile, validate_tool_parameters
from tangying_robot_gateway.journal import RuntimeJournal
from tangying_robot_gateway.plugin_backend import PluginBackend
from tangying_robot_gateway.runtime import Result
from tangying_robot_gateway.service import RobotRuntimeService
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

from .test_plugin_contracts import arm_profile
from .test_service import valid_command

ROOT = Path(__file__).resolve().parents[3]


def profile(name="ugv"):
    return json.loads((ROOT / "examples/robots" / f"{name}.commissioning.profile.json").read_text())


def adapter(value, *, moves=None, stops=None, ready=False, policy_id=None):
    sequence = 0

    def observe():
        nonlocal sequence
        sequence += 1
        sensor = value["sensors"][0]
        return {
            "schemaVersion": "scene.reconstruction.v1", "robotId": value["robotId"],
            "observationId": f"recorded-{sequence}", "sourceId": sensor["sourceId"],
            "sourceType": sensor["sourceType"], "sourceFrameId": sensor["frameId"],
            "frameId": "world", "transformRevision": sensor["transformRevision"],
            "observedAtUnixMs": int(time.time() * 1000), "sequence": sequence,
            "units": "m", "entities": [], "points": [],
        }

    def navigate(command):
        if moves is not None:
            moves.append(command)
        return Result(True, "RECORDED_CONTRACT_ONLY")

    return PluginBackend(
        value, observation_provider=observe,
        handlers={"navigation.navigate": navigate},
        stop=lambda reason: stops.append(reason) if stops is not None else None,
        physical_ready=lambda: ready,
        stop_policy_id=policy_id or value["mobility"]["safeStop"]["procedureId"],
    )


def nav_command(info):
    command = valid_command()
    command.skill = "navigation.navigate"
    command.target_ref = ""
    command.robot_id = info.robot_id
    command.catalog_revision = info.catalog_revision
    ParseDict({"goalPose": [0.25, 0.25, 0, 1, 0, 0, 0]}, command.parameters)
    return command


@pytest.mark.parametrize("name", ["ugv", "uav", "legged"])
def test_mobility_templates_cross_actual_grpc_and_default_to_disarmed(name):
    moves, stops = [], []
    backend = adapter(profile(name), moves=moves, stops=stops)
    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    robot_pb2_grpc.add_RobotRuntimeServicer_to_server(RobotRuntimeService(backend), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            client = robot_pb2_grpc.RobotRuntimeStub(channel)
            info = client.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), timeout=2)
            assert info.robot_profile["mobility"]["safeStop"]["controllerOwned"] is True
            assert "ROBOT_NOT_ARMED" in info.blockers
            final = list(client.ExecuteSkill(nav_command(info), timeout=2))[-1]
            assert final.type == robot_pb2.SKILL_EVENT_FAILED
            assert moves == []
            stopped = client.EmergencyStop(robot_pb2.EStopRequest(reason="TEST_LINK_LOSS"), timeout=2)
            assert stopped.latched
            assert stops == ["TEST_LINK_LOSS"]
    finally:
        server.stop(0).wait()


@pytest.mark.parametrize("name", ["ugv", "uav", "legged"])
def test_commissioned_mobility_uses_shared_admission_journal_and_callback(name, tmp_path):
    moves = []
    value = profile(name)
    first = RobotRuntimeService(adapter(value, moves=moves, ready=True), RuntimeJournal(tmp_path / "journal.json"))
    command = nav_command(first.GetRuntimeInfo(None, None))
    assert list(first.execute_for_test(command))[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    # Recreating both backend and service with the same journal must not repeat
    # a known completed physical command, regardless of embodiment.
    second = RobotRuntimeService(adapter(value, moves=moves, ready=True), RuntimeJournal(tmp_path / "journal.json"))
    assert list(second.execute_for_test(command))[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert len(moves) == 1


@pytest.mark.parametrize("name,mutate", [
    ("ugv", lambda p: p.update(mobility=None)),
    ("ugv", lambda p: p["mobility"].update(commandFrame="map")),
    ("ugv", lambda p: p["mobility"].update(worldConvention="ned")),
    ("ugv", lambda p: p["mobility"].update(linearUnit="mm")),
    ("ugv", lambda p: p["mobility"].update(maxLinearSpeedMps=0)),
    ("ugv", lambda p: p["mobility"].update(maxAngularSpeedRadps=float("nan"))),
    ("ugv", lambda p: p["mobility"]["safeStop"].update(controllerOwned=False)),
    ("ugv", lambda p: p["mobility"]["safeStop"].update(commandLossTimeoutMs=True)),
    ("ugv", lambda p: p["mobility"].update(poseModel="se3")),
    ("ugv", lambda p: p["mobility"]["safeStop"].update(strategy="controlled_land")),
    ("uav", lambda p: p.pop("mobility")),
    ("uav", lambda p: p["mobility"]["safeStop"].update(strategy="motor_cut")),
    ("uav", lambda p: p["mobility"]["safeStop"].update(strategy="brake")),
    ("uav", lambda p: p["mobility"]["safeStop"].update(strategy="hold_position")),
    ("uav", lambda p: p["mobility"].update(poseModel="se2")),
    ("uav", lambda p: p["tools"].append("navigation.pre_position")),
    ("legged", lambda p: p.pop("mobility")),
    ("legged", lambda p: p["mobility"]["safeStop"].update(strategy="brake")),
    ("legged", lambda p: p.update(embodiment="mobile_base")),
])
def test_mobility_rejects_inconsistent_coordinates_stop_and_controller_ownership(name, mutate):
    value = profile(name)
    mutate(value)
    with pytest.raises(ValueError):
        RobotProfile.model_validate(value)


def test_driver_must_explicitly_bind_the_stop_procedure_and_readiness_is_not_inferred():
    with pytest.raises(ValueError, match="stop_policy_id"):
        PluginBackend(profile("uav"), observation_provider=dict, handlers={}, stop=lambda reason: None)
    with pytest.raises(ValueError, match="stop_policy_id"):
        adapter(profile("uav"), policy_id="unrelated-ugv-brake")
    info = adapter(profile("uav")).capabilities()
    assert not next(tool for tool in info.capabilities if tool.name == "navigation.navigate").available


def test_planar_goals_cannot_smuggle_roll_or_pitch():
    value = RobotProfile.model_validate(profile("ugv"))
    validate_tool_parameters("navigation.navigate", {"goalPose": [0, 0, 0, 1, 0, 0, 0]}, value)
    with pytest.raises(ValueError, match="planar"):
        validate_tool_parameters("navigation.navigate", {"goalPose": [0, 0, 0, 0, 1, 0, 0]}, value)


@pytest.mark.parametrize("mutate", [
    lambda p: p["actionLimits"]["navigation.x"].update(max=1.0),
    lambda p: p["sensors"][0].update(transformRevision="calibration-2"),
    lambda p: p["sensors"][0].update(maxAgeMs=250),
    lambda p: p["mobility"].update(maxLinearSpeedMps=0.1),
    lambda p: p["mobility"]["safeStop"].update(procedureId="stop-policy-2"),
    lambda p: p["mobility"]["safeStop"].update(commandLossTimeoutMs=250),
])
def test_approved_command_rejected_after_restart_with_changed_execution_profile(mutate, tmp_path):
    value, moves = profile("ugv"), []
    first = RobotRuntimeService(adapter(value, moves=moves, ready=True), RuntimeJournal(tmp_path / "journal.json"))
    command = nav_command(first.GetRuntimeInfo(None, None))
    mutate(value)
    second = RobotRuntimeService(adapter(value, moves=moves, ready=True), RuntimeJournal(tmp_path / "journal.json"))
    assert second.GetRuntimeInfo(None, None).catalog_revision != command.catalog_revision
    terminal = list(second.execute_for_test(command))[-1]
    assert terminal.code == "TOOL_CATALOG_STALE"
    assert moves == []


def test_profile_revision_is_stable_for_dictionary_order_and_live_availability():
    backend = adapter(profile("uav"), ready=True)
    info = backend.capabilities()
    revision = RobotRuntimeService._catalog_revision(info)
    info.robot_profile = dict(reversed(list(info.robot_profile.items())))
    for tool in info.capabilities:
        tool.available = False
        tool.blockers.append("TEST_TRANSIENT")
    assert RobotRuntimeService._catalog_revision(info) == revision


def test_legacy_profile_omits_mobility_and_can_be_revalidated_or_updated():
    value = RobotProfile.model_validate(arm_profile())
    assert "mobility" not in value.to_wire()
    assert RobotProfile.model_validate(value) == value
    value.adapter_version = "2.0.0"
    assert RobotProfile.model_validate(value).adapter_version == "2.0.0"
    bad = copy.deepcopy(value)
    bad.joints.clear()
    with pytest.raises(ValueError, match="unknown joint"):
        RobotProfile.model_validate(bad)
