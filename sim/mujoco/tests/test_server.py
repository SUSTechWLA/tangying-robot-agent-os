import threading
import time

from tangying_robot_proto.robot.v1 import robot_pb2
from tangying_sim.server import RobotRuntimeService
from tangying_sim.tools import ToolResult
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


def test_execute_streams_accepted_and_running_before_tool_finishes():
    started = threading.Event()
    release = threading.Event()

    class BlockingTool:
        def execute(self, _context, *, target_ref="", parameters=None):
            del target_ref, parameters
            started.set()
            assert release.wait(1)
            return ToolResult(True)

    service = RobotRuntimeService(TabletopWorld.seeded(7))
    service.world.tools.register("observe_scene", BlockingTool())
    events = []
    worker = threading.Thread(
        target=lambda: events.extend(service.execute_for_test(command("observe_scene")))
    )

    worker.start()
    assert started.wait(1)
    assert [event.type for event in events] == [
        robot_pb2.SKILL_EVENT_ACCEPTED,
        robot_pb2.SKILL_EVENT_RUNNING,
    ]
    release.set()
    worker.join(1)
    assert events[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED


def test_cancel_interrupts_motion_recovers_and_emits_cancelled():
    started = threading.Event()

    class SlowMotionTool:
        def execute(self, context, *, target_ref="", parameters=None):
            del target_ref, parameters

            def slow_step(_progress):
                started.set()
                time.sleep(0.005)

            reached = context.world.motion.approach_body(
                "left",
                "Fixed_Jaw_2",
                (1.5, 1.5, 1.2),
                max_steps=150,
                on_step=slow_step,
                cancel_event=context.cancel_event,
            )
            return ToolResult(reached, "OK" if reached else "TARGET_UNREACHABLE")

    service = RobotRuntimeService(TabletopWorld.seeded(7))
    service.world.tools.register("manipulation.pick", SlowMotionTool())
    events = []
    worker = threading.Thread(
        target=lambda: events.extend(
            service.execute_for_test(command("manipulation.pick", "cmd-cancel"))
        )
    )

    worker.start()
    assert started.wait(1)
    started_at = time.monotonic()
    result = service.Cancel(
        robot_pb2.CancelRequest(command_id="cmd-cancel", reason="operator cancel"), None
    )
    cancel_latency = time.monotonic() - started_at
    worker.join(2)

    assert result.accepted
    assert result.state == "CANCELLED"
    assert cancel_latency < 0.1
    assert not worker.is_alive()
    assert events[-1].type == robot_pb2.SKILL_EVENT_CANCELLED
    assert events[-1].code == "CANCELLED"
    assert service.world.robot_state()["grippers"] == {"left": "open", "right": "open"}
    for joint_name in ("slide_joint_x", "slide_joint_y", "hinge_joint_z"):
        joint_id = service.world.model.joint(joint_name).id
        address = service.world.model.jnt_qposadr[joint_id]
        assert service.world.data.qpos[address] == 0.0


def test_emergency_stop_interrupts_motion_and_recovers_to_safe_pose():
    started = threading.Event()

    class SlowMotionTool:
        def execute(self, context, *, target_ref="", parameters=None):
            del target_ref, parameters

            def slow_step(_progress):
                started.set()
                time.sleep(0.005)

            context.world.motion.interpolate(
                "left",
                {"Rotation": 1.0},
                steps=150,
                on_step=slow_step,
                cancel_event=context.cancel_event,
            )
            return ToolResult(True)

    service = RobotRuntimeService(TabletopWorld.seeded(7))
    service.world.tools.register("manipulation.pick", SlowMotionTool())
    events = []
    worker = threading.Thread(
        target=lambda: events.extend(
            service.execute_for_test(command("manipulation.pick", "cmd-estop"))
        )
    )
    worker.start()
    assert started.wait(1)

    service.EmergencyStop(robot_pb2.EStopRequest(reason="operator stop"), None)
    worker.join(2)

    assert not worker.is_alive()
    assert events[-1].type == robot_pb2.SKILL_EVENT_CANCELLED
    assert events[-1].code == "CANCELLED"
    assert service.world.robot_state()["grippers"] == {"left": "open", "right": "open"}
    for arm in ("left", "right"):
        for name, expected in service.world.motion.target_for(arm, "HOME").items():
            assert service.world.joint_positions()[name] == expected


def test_duplicate_idempotency_requests_are_single_flight():
    started = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    class CountingTool:
        def execute(self, _context, *, target_ref="", parameters=None):
            nonlocal calls
            del target_ref, parameters
            with calls_lock:
                calls += 1
            started.set()
            assert release.wait(1)
            return ToolResult(True)

    service = RobotRuntimeService(TabletopWorld.seeded(7))
    service.world.tools.register("observe_scene", CountingTool())
    first_events = []
    second_events = []
    first = threading.Thread(
        target=lambda: first_events.extend(service.execute_for_test(command("observe_scene")))
    )
    second = threading.Thread(
        target=lambda: second_events.extend(service.execute_for_test(command("observe_scene")))
    )

    first.start()
    assert started.wait(1)
    second.start()
    time.sleep(0.05)
    release.set()
    first.join(1)
    second.join(1)

    assert calls == 1
    assert [event.type for event in first_events] == [event.type for event in second_events]
    assert first_events[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED


def test_observation_waits_for_world_mutation_before_rendering(monkeypatch):
    mutation_started = threading.Event()
    mutation_release = threading.Event()
    render_entered = threading.Event()

    class BlockingMutation:
        def execute(self, _context, *, target_ref="", parameters=None):
            del target_ref, parameters
            mutation_started.set()
            assert mutation_release.wait(1)
            return ToolResult(True)

    service = RobotRuntimeService(TabletopWorld.seeded(7))
    service.world.tools.register("manipulation.pick", BlockingMutation())
    original_render = service.renderer.render

    def recording_render(*args):
        render_entered.set()
        return original_render(*args)

    monkeypatch.setattr(service.renderer, "render", recording_render)
    execution = threading.Thread(
        target=lambda: list(
            service.execute_for_test(command("manipulation.pick", "cmd-world-lock"))
        )
    )
    execution.start()
    assert mutation_started.wait(1)
    observation = threading.Thread(target=service._observation)
    observation.start()

    assert not render_entered.wait(0.05)
    mutation_release.set()
    execution.join(1)
    observation.join(1)
    assert render_entered.is_set()


def test_service_close_releases_renderer_idempotently(monkeypatch):
    service = RobotRuntimeService(TabletopWorld.seeded(7))
    close_calls = []
    monkeypatch.setattr(service.renderer, "close", lambda: close_calls.append(True))

    service.close()
    service.close()

    assert close_calls == [True]
