from __future__ import annotations

import time
from concurrent import futures
from threading import Thread

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
    assert set(sender_info.cameras) >= {"overview", "robot-1__rgbd_head"}


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


def test_runtime_monotonically_adopts_cloud_fencing_after_restart(runtime_pair) -> None:
    world, services = runtime_pair
    world.reset()
    sender = services["robot-1"]
    sender.register_resource("block:red-block", owner="robot-1", token=1)
    command = _command(sender, "manipulation.pick", "red-block", "adopt-token")
    command.fencing_token = 7

    result = list(sender.execute_for_test(command))[-1]

    assert result.type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert world.fencing_token == 7


def test_reset_episode_is_idempotent_per_runtime_command(runtime_pair) -> None:
    world, services = runtime_pair
    world.adopt_fencing_token(19)
    command = _command(services["robot-1"], "simulation.reset_episode", "", "reset-episode-1")
    command.resource_id = ""
    command.fencing_token = 0
    before = world.episode

    assert list(services["robot-1"].execute_for_test(command))[-1].code == "OK"
    assert list(services["robot-1"].execute_for_test(command))[-1].code == "OK"

    assert world.episode == before + 1
    assert world.fencing_token >= 19
    assert world.placement == "left-start-zone"


def test_runtime_rejects_stale_fencing_after_monotonic_adoption(runtime_pair) -> None:
    world, services = runtime_pair
    world.reset()
    sender = services["robot-1"]
    sender.register_resource("block:red-block", owner="robot-1", token=7)
    command = _command(sender, "manipulation.pick", "red-block", "stale-token")
    command.fencing_token = 6

    result = list(sender.execute_for_test(command))[-1]

    assert result.type == robot_pb2.SKILL_EVENT_FAILED
    assert result.code == "FENCING_TOKEN_STALE"
    assert world.pick_count("robot-1") == 0


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


def test_cached_robot_state_stays_live_while_a_skill_holds_the_physics_lock(
    runtime_pair,
) -> None:
    world, services = runtime_pair
    world.reset()
    sender_observation = services["robot-1"]._observation()
    assert sender_observation.compressed_image.startswith(b"\x89PNG")
    world.human_speed = 0.03
    sender = services["robot-1"]
    sender.register_resource("block:red-block", owner="robot-1", token=1)
    command = _command(sender, "manipulation.pick", "red-block", "live-pick")
    events: list[robot_pb2.SkillEvent] = []

    worker = Thread(target=lambda: events.extend(sender.execute_for_test(command)))
    worker.start()
    try:
        deadline = time.monotonic() + 2
        while (
            (world.cached_world_state() or {}).get("step_count", 0) < 4
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        assert (world.cached_world_state() or {}).get("step_count", 0) >= 4

        started = time.monotonic()
        state = sender.world.cached_robot_state()
        elapsed = time.monotonic() - started

        assert state is not None
        assert elapsed < 0.12
        assert worker.is_alive(), "cached telemetry waited for the whole pick skill"
        assert 4 <= state["step_count"] < 19
        assert any(abs(value) > 0.01 for value in state["joint_positions"].values())
        observed_started = time.monotonic()
        observation = next(sender.Observe(robot_pb2.ObserveRequest(), None))
        assert time.monotonic() - observed_started < 0.2
        assert observation.semantic_state.activity == "EXECUTING"
    finally:
        worker.join(timeout=3)
        world.human_speed = 0.0

    assert events[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED


def test_grpc_observe_reports_execution_without_waiting_for_the_skill(runtime_pair) -> None:
    world, services = runtime_pair
    world.reset()
    world.human_speed = 0.03
    sender = services["robot-1"]
    sender.register_resource("block:red-block", owner="robot-1", token=1)
    assert sender._observation().compressed_image.startswith(b"\x89PNG")

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    robot_pb2_grpc.add_RobotRuntimeServicer_to_server(sender, server)
    port = server.add_insecure_port("127.0.0.1:0")
    assert port > 0
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    stub = robot_pb2_grpc.RobotRuntimeStub(channel)
    command = _command(sender, "manipulation.pick", "red-block", "grpc-live-pick")
    events: list[robot_pb2.SkillEvent] = []
    worker = Thread(target=lambda: events.extend(stub.ExecuteSkill(command, timeout=5)))
    worker.start()
    try:
        deadline = time.monotonic() + 2
        while (
            (world.cached_world_state() or {}).get("step_count", 0) < 4
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        assert worker.is_alive(), "gRPC execution finished before live observation"

        started = time.monotonic()
        observation = next(
            stub.Observe(robot_pb2.ObserveRequest(), timeout=1)
        )

        assert time.monotonic() - started < 0.2
        assert observation.semantic_state.activity == "EXECUTING"
    finally:
        worker.join(timeout=5)
        channel.close()
        server.stop(grace=0).wait()
        world.human_speed = 0.0

    assert events[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
