import time

from tangying_robot_proto.robot.v1 import robot_pb2
from tangying_sim.server import RobotGatewayService
from tangying_sim.world import TabletopWorld


def command(skill: str, command_id: str = "cmd-1") -> robot_pb2.SkillCommand:
    return robot_pb2.SkillCommand(
        schema_version="robot.v1",
        command_id=command_id,
        task_id="task-1",
        skill=skill,
        target_ref="red-cup",
        deadline_unix_ms=int(time.time() * 1000) + 10_000,
        lease_ms=5_000,
        idempotency_key=f"task-1-{skill}",
        safety_profile="simulation",
    )


def test_service_replays_terminal_event_for_duplicate_command():
    service = RobotGatewayService(TabletopWorld.seeded(7))
    first = list(service.execute_for_test(command("manipulation.pick")))
    second = list(service.execute_for_test(command("manipulation.pick")))
    assert first[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert second[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert service.world.pick_count == 1


def test_service_rejects_expired_command():
    service = RobotGatewayService(TabletopWorld.seeded(7))
    expired = command("manipulation.pick")
    expired.deadline_unix_ms = 1
    event = list(service.execute_for_test(expired))[-1]
    assert event.type == robot_pb2.SKILL_EVENT_FAILED
    assert event.code == "COMMAND_EXPIRED"
