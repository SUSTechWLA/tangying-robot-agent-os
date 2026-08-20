from __future__ import annotations

import time
from concurrent import futures

import grpc
import pytest
from tangying_robocasa.fleet_server import create_fleet_services
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

pytestmark = pytest.mark.robocasa


def _command(service, skill: str, target: str, key: str) -> robot_pb2.SkillCommand:
    info = service.GetRuntimeInfo(None, None)
    return robot_pb2.SkillCommand(
        schema_version="robot.v1",
        command_id=f"cmd-{key}",
        task_id="task-robocasa-handoff",
        skill=skill,
        target_ref=target,
        deadline_unix_ms=int(time.time() * 1000) + 30_000,
        lease_ms=10_000,
        idempotency_key=key,
        safety_profile="simulation",
        robot_id=info.robot_id,
        catalog_revision=info.catalog_revision,
        world_revision_basis=1,
        resource_id="block:red-block",
        fencing_token=service.world.shared.fencing_token,
    )


@pytest.fixture(scope="module")
def runtime_pair():
    pytest.importorskip("robocasa")
    world, services = create_fleet_services(seed=7)
    yield world, services
    for service in services.values():
        service.close()


def test_runtime_info_identifies_robocasa_and_robot(runtime_pair) -> None:
    _world, services = runtime_pair

    sender_info = services["robot-1"].GetRuntimeInfo(None, None)
    receiver_info = services["robot-2"].GetRuntimeInfo(None, None)

    assert sender_info.robot_id == "robot-1"
    assert receiver_info.robot_id == "robot-2"
    assert sender_info.adapter == "robocasa"
    assert receiver_info.adapter == "robocasa"
    assert set(sender_info.cameras) >= {"overview", "robot-1-evidence"}


def test_two_runtime_endpoints_are_independently_reachable_over_grpc(runtime_pair) -> None:
    _world, services = runtime_pair
    servers = []
    channels = []
    try:
        identities = []
        for robot_id in ("robot-1", "robot-2"):
            server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
            robot_pb2_grpc.add_RobotRuntimeServicer_to_server(services[robot_id], server)
            port = server.add_insecure_port("127.0.0.1:0")
            assert port > 0
            server.start()
            servers.append(server)
            channel = grpc.insecure_channel(f"127.0.0.1:{port}")
            channels.append(channel)
            stub = robot_pb2_grpc.RobotRuntimeStub(channel)
            identities.append(
                stub.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), timeout=5).robot_id
            )
        assert identities == ["robot-1", "robot-2"]
    finally:
        for channel in channels:
            channel.close()
        for server in servers:
            server.stop(grace=0).wait()


def test_duplicate_command_is_effectively_once(runtime_pair) -> None:
    world, services = runtime_pair
    world.reset()
    command = _command(services["robot-1"], "manipulation.pick", "red-block", "pick-once")

    first = list(services["robot-1"].execute_for_test(command))
    second = list(services["robot-1"].execute_for_test(command))

    assert first[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert second[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert world.pick_count("robot-1") == 1


def test_fenced_handoff_updates_receiver_runtime_grant(runtime_pair) -> None:
    world, services = runtime_pair
    world.reset()
    sender = services["robot-1"]
    receiver = services["robot-2"]

    sender.register_resource("block:red-block", owner="robot-1", token=1)
    receiver.register_resource("block:red-block", owner="robot-1", token=1)
    assert list(sender.execute_for_test(_command(sender, "manipulation.pick", "red-block", "s-pick")))[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert list(sender.execute_for_test(_command(sender, "manipulation.place", "handoff-zone", "s-place")))[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED

    receive = _command(receiver, "manipulation.pick", "red-block", "r-pick")
    assert receive.fencing_token == 2
    assert list(receiver.execute_for_test(receive))[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED


def test_runtime_observation_renders_shared_kitchen(runtime_pair) -> None:
    _world, services = runtime_pair

    observation = services["robot-1"]._observation()

    assert observation.compressed_image.startswith(b"\x89PNG\r\n\x1a\n")
    assert observation.image_media_type == "image/png"
    assert {entity.entity_id for entity in observation.entities} >= {
        "robot-1",
        "robot-2",
        "red-block",
        "handoff-zone",
    }
    assert observation.robot_state["frame_id"] == "world"
