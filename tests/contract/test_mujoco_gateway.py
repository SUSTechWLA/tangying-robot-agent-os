import time
from concurrent import futures

import grpc
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc
from tangying_sim.server import RobotGatewayService
from tangying_sim.world import TabletopWorld


def make_command(target: str) -> robot_pb2.SkillCommand:
    return robot_pb2.SkillCommand(
        schema_version="robot.v1",
        command_id="cmd-live",
        task_id="task-live",
        skill="manipulation.pick",
        target_ref=target,
        deadline_unix_ms=int(time.time() * 1000) + 10_000,
        lease_ms=5_000,
        idempotency_key="task-live-pick-1",
        safety_profile="simulation",
    )


def test_live_gateway_capabilities_observation_and_idempotency_conflict():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    robot_pb2_grpc.add_RobotGatewayServicer_to_server(
        RobotGatewayService(TabletopWorld.seeded(7)), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            client = robot_pb2_grpc.RobotGatewayStub(channel)
            capabilities = client.GetCapabilities(robot_pb2.GetCapabilitiesRequest())
            observation = next(client.Observe(robot_pb2.ObserveRequest(task_id="task-live")))
            first = list(client.ExecuteSkill(make_command("red-cup")))
            conflict = list(client.ExecuteSkill(make_command("red-cup-2")))

        assert capabilities.adapter == "mujoco"
        assert {item.name for item in capabilities.capabilities} >= {"observe_scene", "manipulation.pick"}
        assert observation.semantic_state.activity == "IDLE"
        assert {entity.entity_id for entity in observation.entities} >= {"red-cup", "right-bin"}
        assert first[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
        assert conflict[-1].type == robot_pb2.SKILL_EVENT_FAILED
        assert conflict[-1].code == "IDEMPOTENCY_CONFLICT"
    finally:
        server.stop(grace=0).wait()
