import time

from tangying_robot_proto.robot.v1 import robot_pb2
from tangying_sim.server import RobotRuntimeService
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
    service = RobotRuntimeService(TabletopWorld.seeded(7))
    first = list(service.execute_for_test(command("manipulation.pick")))
    second = list(service.execute_for_test(command("manipulation.pick")))
    assert first[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert second[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert service.world.pick_count == 1


def test_service_rejects_expired_command():
    service = RobotRuntimeService(TabletopWorld.seeded(7))
    expired = command("manipulation.pick")
    expired.deadline_unix_ms = 1
    event = list(service.execute_for_test(expired))[-1]
    assert event.type == robot_pb2.SKILL_EVENT_FAILED
    assert event.code == "COMMAND_EXPIRED"


def test_dispatch_routes_every_skill_through_world_registry(monkeypatch):
    service = RobotRuntimeService(TabletopWorld.seeded(7))
    seen = []
    original_execute = service.world.tools.execute

    def recording_execute(*args, **kwargs):
        seen.append(args[0])
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(service.world.tools, "execute", recording_execute)

    event = list(service.execute_for_test(command("observe_scene")))[-1]

    assert event.type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert seen == ["observe_scene"]


def test_observation_contains_image_scene_map_and_rich_robot_state():
    service = RobotRuntimeService(TabletopWorld.seeded(7))

    observation = service._observation()

    assert observation.compressed_image.startswith(b"\x89PNG\r\n\x1a\n")
    assert observation.image_media_type == "image/png"
    assert {entity.entity_id for entity in observation.entities} >= {
        "xlerobot",
        "table",
        "floor",
        "red-cup",
        "left-bin",
    }
    assert set(observation.robot_state.fields) >= {
        "model_revision",
        "base_pose",
        "joint_positions",
        "grippers",
        "held",
        "active_tool",
        "target",
        "end_effectors",
        "reward",
        "episode",
        "verification_confidence",
        "placements",
    }


def test_renderer_failure_is_nonfatal_and_reported_as_anomaly(monkeypatch):
    service = RobotRuntimeService(TabletopWorld.seeded(7))

    def fail_render(*_args):
        raise RuntimeError("no graphics context")

    monkeypatch.setattr(service.renderer, "render", fail_render)

    observation = service._observation()

    assert observation.compressed_image == b""
    assert observation.robot_state.fields["render_anomaly"].string_value == "no graphics context"
