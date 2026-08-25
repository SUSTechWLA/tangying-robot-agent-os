import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from tangying_robot_proto.robot.v1 import robot_pb2
from tangying_sim import rendering
from tangying_sim.server import RobotRuntimeService
from tangying_sim.tools import ToolContext, ToolResult
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


def test_physical_command_with_stale_catalog_fails_before_dispatch(monkeypatch):
    required = {
        "robot_id",
        "catalog_revision",
        "world_revision_basis",
        "resource_id",
        "fencing_token",
    }
    assert required <= set(robot_pb2.SkillCommand.DESCRIPTOR.fields_by_name)

    service = RobotRuntimeService(TabletopWorld.seeded(7))
    stale = command("manipulation.pick", "cmd-stale-catalog")
    stale.robot_id = service.GetRuntimeInfo(None, None).robot_id
    stale.catalog_revision = "0" * 64
    stale.world_revision_basis = 1
    stale.resource_id = "red-cup"
    stale.fencing_token = 1
    dispatched = []
    monkeypatch.setattr(service, "_dispatch", lambda *_args: dispatched.append(True))

    event = list(service.execute_for_test(stale))[-1]

    assert event.type == robot_pb2.SKILL_EVENT_FAILED
    assert event.code == "TOOL_CATALOG_STALE"
    assert dispatched == []


def test_physical_command_with_stale_fencing_fails_before_dispatch(monkeypatch):
    required = {"catalog_revision", "resource_id", "fencing_token"}
    assert required <= set(robot_pb2.SkillCommand.DESCRIPTOR.fields_by_name)

    service = RobotRuntimeService(TabletopWorld.seeded(7))
    if not hasattr(service, "register_resource"):
        pytest.fail("runtime does not expose resource fencing registration")
    service.register_resource("red-cup", owner=service.GetRuntimeInfo(None, None).robot_id, token=8)
    stale = command("manipulation.pick", "cmd-stale-fencing")
    stale.robot_id = service.GetRuntimeInfo(None, None).robot_id
    stale.catalog_revision = service.GetRuntimeInfo(None, None).catalog_revision
    stale.world_revision_basis = 1
    stale.resource_id = "red-cup"
    stale.fencing_token = 7
    dispatched = []
    monkeypatch.setattr(service, "_dispatch", lambda *_args: dispatched.append(True))

    event = list(service.execute_for_test(stale))[-1]

    assert event.type == robot_pb2.SKILL_EVENT_FAILED
    assert event.code == "FENCING_TOKEN_STALE"
    assert dispatched == []


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


def test_observation_contains_atomic_rgbd_capture():
    service = RobotRuntimeService(TabletopWorld.seeded(7), robot_id="robot-1")

    observation = service._observation()

    assert observation.capture.schema_version == "sensor.capture.v1"
    assert observation.capture.robot_id == "robot-1"
    assert observation.capture.simulation_step == observation.robot_state["step_count"]
    assert observation.capture.episode_id.endswith(
        f":{int(observation.robot_state['episode'])}"
    )
    assert {frame.modality for frame in observation.capture.frames} == {"rgb", "depth"}
    assert all(len(frame.sha256) == 64 for frame in observation.capture.frames)
    assert all(frame.sensor_id for frame in observation.capture.frames)
    service.close()


def test_idle_observation_refreshes_rgbd_capture_without_world_motion():
    service = RobotRuntimeService(TabletopWorld.seeded(7), robot_id="robot-1")
    service._frame_render_interval = 0

    first = service._observation().capture
    service._observation()
    deadline = time.monotonic() + 2
    with service._frame_condition:
        while service._capture_sequence < 2:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "idle RGB-D refresh did not complete"
            service._frame_condition.wait(remaining)
    refreshed = service._observation().capture

    assert refreshed.source_sequence > first.source_sequence
    assert refreshed.capture_id != first.capture_id
    assert refreshed.captured_unix_ms >= first.captured_unix_ms
    assert refreshed.simulation_step == first.simulation_step
    service.close()


def test_world_change_waits_for_atomic_frame_when_idle_refresh_is_in_flight(monkeypatch):
    service = RobotRuntimeService(TabletopWorld.seeded(7), robot_id="robot-1")
    service._observation()
    service._frame_render_interval = 0
    idle_render_started = threading.Event()
    release_idle_render = threading.Event()
    original_render = service.renderer.render
    calls = 0

    def block_one_idle_render(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            idle_render_started.set()
            assert release_idle_render.wait(2)
        return original_render(*args, **kwargs)

    monkeypatch.setattr(service.renderer, "render", block_one_idle_render)
    service._observation()
    assert idle_render_started.wait(1)

    context = ToolContext(service.world)
    assert service.world.tools.execute(
        "plan_grasp",
        context,
        target_ref="red-cup",
        parameters={"destinationId": "right-bin"},
    ).success
    assert service.world.tools.execute(
        "manipulation.pick", context, target_ref="red-cup"
    ).success

    observations = []
    observer = threading.Thread(target=lambda: observations.append(service._observation()))
    observer.start()
    time.sleep(0.05)
    assert observer.is_alive(), "changed state returned with the stale idle RGB-D frame"
    release_idle_render.set()
    observer.join(2)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.robot_state["held"] == "red-cup"
    assert observation.capture.simulation_step == observation.robot_state["step_count"]
    service.close()


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
    assert events[-1].type == robot_pb2.SKILL_EVENT_SAFETY_STOPPED
    assert events[-1].code == "EMERGENCY_STOP_LATCHED"
    assert "operator stop" in events[-1].message
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


def test_concurrent_observe_uses_one_gl_thread_without_holding_world_lock(monkeypatch):
    service = RobotRuntimeService(TabletopWorld.seeded(7))
    gl_threads = []
    lock_available = []

    class AffineRenderer:
        def __init__(self, *_args, **_kwargs):
            self.owner = threading.get_ident()
            self.depth = False
            gl_threads.append(self.owner)

        def _check_owner(self):
            current = threading.get_ident()
            gl_threads.append(current)
            if current != self.owner:
                raise RuntimeError("OpenGL renderer used from non-owner thread")

        def update_scene(self, *_args, **_kwargs):
            self._check_owner()
            acquired = []

            def probe_world_lock():
                locked = service.world.lock.acquire(timeout=0.1)
                acquired.append(locked)
                if locked:
                    service.world.lock.release()

            probe = threading.Thread(target=probe_world_lock)
            probe.start()
            probe.join(0.2)
            lock_available.extend(acquired)

        def render(self):
            self._check_owner()
            if self.depth:
                return np.ones((240, 320), dtype=np.float32)
            return np.zeros((240, 320, 3), dtype=np.uint8)

        def enable_depth_rendering(self):
            self._check_owner()
            self.depth = True

        def disable_depth_rendering(self):
            self._check_owner()
            self.depth = False

        def close(self):
            self._check_owner()

    monkeypatch.setattr(rendering.mujoco, "Renderer", AffineRenderer)
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(service._observation) for _ in range(12)]
        observations = [future.result(timeout=5) for future in futures]
    service.close()

    assert all(observation.compressed_image for observation in observations)
    assert all(observation.capture.frames for observation in observations)
    assert len(set(gl_threads)) == 1
    # The first caller renders once and the concurrent startup callers share
    # that completed frame. Subsequent refreshes are background-cached.
    assert lock_available == [True, True]


def test_cancel_before_place_commit_keeps_object_held(monkeypatch):
    world = TabletopWorld.seeded(7)
    context = ToolContext(world)
    assert world.tools.execute(
        "plan_grasp",
        context,
        target_ref="red-cup",
        parameters={"destinationId": "right-bin"},
    ).success
    assert world.pick("red-cup").success
    opening = threading.Event()
    resume = threading.Event()
    original_move_named = world.motion.move_named

    def pausing_move_named(arm, name, **kwargs):
        original_on_step = kwargs.get("on_step")

        def pause_before_commit(progress):
            if name == "OPEN" and not opening.is_set():
                opening.set()
                assert resume.wait(1)
            if original_on_step is not None:
                original_on_step(progress)

        kwargs["on_step"] = pause_before_commit
        return original_move_named(arm, name, **kwargs)

    monkeypatch.setattr(world.motion, "move_named", pausing_move_named)
    service = RobotRuntimeService(world)
    events = []
    placement = command("manipulation.place", "cmd-place-precommit")
    placement.target_ref = "right-bin"
    worker = threading.Thread(
        target=lambda: events.extend(service.execute_for_test(placement))
    )
    worker.start()
    assert opening.wait(2)

    cancelled = service.Cancel(
        robot_pb2.CancelRequest(command_id=placement.command_id, reason="operator cancel"),
        None,
    )
    resume.set()
    worker.join(2)

    assert cancelled.accepted
    assert events[-1].type == robot_pb2.SKILL_EVENT_CANCELLED
    assert world.robot_state()["held"] == "red-cup"
    assert world.robot_state()["placements"] == {}
    assert world.verify_grasp("red-cup").success


def test_cancel_after_place_release_is_rejected_and_terminal_succeeds():
    released = threading.Event()
    resume = threading.Event()

    class PausedAfterReleaseWorld(TabletopWorld):
        pause_after_release = False

        def __setattr__(self, name, value):
            previous = getattr(self, "_held", None)
            super().__setattr__(name, value)
            if (
                name == "_held"
                and previous is not None
                and value is None
                and self.pause_after_release
            ):
                released.set()
                assert resume.wait(1)

    world = PausedAfterReleaseWorld.seeded(7)
    context = ToolContext(world)
    assert world.tools.execute(
        "plan_grasp",
        context,
        target_ref="red-cup",
        parameters={"destinationId": "right-bin"},
    ).success
    assert world.pick("red-cup").success
    world.pause_after_release = True
    service = RobotRuntimeService(world)
    events = []
    placement = command("manipulation.place", "cmd-place-committed")
    placement.target_ref = "right-bin"
    worker = threading.Thread(
        target=lambda: events.extend(service.execute_for_test(placement))
    )
    worker.start()
    assert released.wait(2)

    cancelled = service.Cancel(
        robot_pb2.CancelRequest(command_id=placement.command_id, reason="late cancel"),
        None,
    )
    resume.set()
    worker.join(2)

    assert not cancelled.accepted
    assert events[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert world.robot_state()["held"] == ""
    assert world.robot_state()["placements"] == {"red-cup": "right-bin"}
