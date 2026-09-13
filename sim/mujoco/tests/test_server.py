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
    observation_started = threading.Event()
    render_entered = threading.Event()

    class BlockingMutation:
        def execute(self, _context, *, target_ref="", parameters=None):
            del target_ref, parameters
            mutation_started.set()
            mutation_release.wait()
            return ToolResult(True)

    service = RobotRuntimeService(TabletopWorld.seeded(7))
    service.world.tools.register("manipulation.pick", BlockingMutation())
    original_entities = service.world.entities

    def recording_entities():
        observation_started.set()
        return original_entities()

    def recording_render(_model, data):
        # This test checks snapshot/lock ordering. Actual GL ownership and PNG
        # rendering have their own tests; starting GL here adds unrelated
        # scheduling and cleanup work to a synchronization assertion.
        assert mutation_release.is_set()
        assert data is not service.world.data
        render_entered.set()

    monkeypatch.setattr(service.world, "entities", recording_entities)
    monkeypatch.setattr(service.renderer, "render", recording_render)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            execution = pool.submit(lambda: list(
                service.execute_for_test(command("manipulation.pick", "cmd-world-lock"))
            ))
            try:
                assert mutation_started.wait(5)
                observation = pool.submit(service._observation)
                assert observation_started.wait(5)
                assert not render_entered.wait(0.05)
            finally:
                # Release even when an assertion fails so executor cleanup
                # cannot leave the mutation or observer thread behind.
                mutation_release.set()
            events = execution.result(timeout=10)
            observation.result(timeout=10)
    finally:
        service.close()

    assert events[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
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
            return np.zeros((240, 320, 3), dtype=np.uint8)

        def close(self):
            self._check_owner()

    monkeypatch.setattr(rendering.mujoco, "Renderer", AffineRenderer)
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(service._observation) for _ in range(12)]
        observations = [future.result(timeout=5) for future in futures]
    service.close()

    assert all(observation.compressed_image for observation in observations)
    assert len(set(gl_threads)) == 1
    assert lock_available == [True] * 12


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


@pytest.mark.parametrize("expiry", ["deadline", "lease"])
def test_command_expiry_cancels_remaining_work_and_rejects_late_success(monkeypatch, expiry):
    service = RobotRuntimeService(TabletopWorld.seeded(7))
    request = command("observe_scene")
    if expiry == "deadline":
        request.deadline_unix_ms = int(time.time()*1000)+80
    else:
        request.lease_ms = 80
    cancelled = []
    def blocked_driver(_request, active):
        cancelled.append(active.cancel_event.wait(.5))
        # Even a defective driver returning success after its budget cannot
        # create a successful receipt that will be replayed on a retry.
        return ToolResult(True,"LATE_SUCCESS")
    monkeypatch.setattr(service,"_dispatch",blocked_driver)
    monkeypatch.setattr(service,"_recover_cancelled_command",lambda *_:None)
    try:
        terminal = list(service.execute_for_test(request))[-1]
        assert cancelled == [True]
        assert terminal.type == robot_pb2.SKILL_EVENT_CANCELLED
        assert terminal.code == "COMMAND_EXPIRED"
        replay = list(service.execute_for_test(request))[-1]
        assert replay == terminal
    finally:
        service.close()


class _CancelledRPC:
    def __init__(self):
        self.callbacks = []
    def add_callback(self, callback):
        self.callbacks.append(callback)
        return True
    def disconnect(self):
        for callback in self.callbacks:
            callback()


def test_only_owner_rpc_disconnect_cancels_inflight_execution(monkeypatch):
    service = RobotRuntimeService(TabletopWorld.seeded(7))
    ready, finish = threading.Event(), threading.Event()
    owner, duplicate = _CancelledRPC(), _CancelledRPC()
    request = command("observe_scene")
    cancels = []
    def blocked_driver(_request, active):
        ready.set()
        finish.wait(1)
        cancels.append(active.cancel_event.is_set())
        return ToolResult(True,"DONE")
    monkeypatch.setattr(service,"_dispatch",blocked_driver)
    monkeypatch.setattr(service,"_recover_cancelled_command",lambda *_:None)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            original=pool.submit(lambda:list(service.ExecuteSkill(request,owner)))
            assert ready.wait(1)
            reader=pool.submit(lambda:list(service.ExecuteSkill(request,duplicate)))
            duplicate.disconnect()
            assert not duplicate.callbacks
            owner.disconnect()
            finish.set()
            events=original.result(timeout=2)
            assert events[-1].type == robot_pb2.SKILL_EVENT_CANCELLED
            assert reader.result(timeout=2)[-1] == events[-1]
        assert cancels == [True]
    finally:
        finish.set()
        service.close()


def test_expiry_while_waiting_for_world_lock_never_dispatches(monkeypatch):
    service = RobotRuntimeService(TabletopWorld.seeded(7))
    request = command("observe_scene");request.lease_ms=60
    monkeypatch.setattr(service,"_dispatch",lambda *_:pytest.fail("expired queued command dispatched"))
    monkeypatch.setattr(service,"_recover_cancelled_command",lambda *_:None)
    stream = service.execute_for_test(request)
    assert next(stream).type == robot_pb2.SKILL_EVENT_ACCEPTED
    assert next(stream).type == robot_pb2.SKILL_EVENT_RUNNING
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with service.world.lock:
                result = pool.submit(lambda:list(stream))
                # Admission's timer must wake cancellation without acquiring
                # the same world lock held by the outstanding operation.
                assert service._active_commands[request.command_id].cancel_event.wait(.5)
            terminal = result.result(timeout=1)[-1]
        assert terminal.type == robot_pb2.SKILL_EVENT_CANCELLED
        assert terminal.code == "COMMAND_EXPIRED"
    finally:
        service.close()


def test_expiry_during_receipt_construction_cannot_cache_success(monkeypatch):
    service = RobotRuntimeService(TabletopWorld.seeded(7))
    request = command("observe_scene");request.lease_ms=60
    monkeypatch.setattr(service,"_dispatch",lambda *_:ToolResult(True,"DONE"))
    original = service._event
    def delayed_receipt(*args,**kwargs):
        if args[2] == robot_pb2.SKILL_EVENT_SUCCEEDED:
            assert service._active_commands[request.command_id].cancel_event.wait(.5)
        return original(*args,**kwargs)
    monkeypatch.setattr(service,"_event",delayed_receipt)
    try:
        terminal = list(service.execute_for_test(request))[-1]
        assert terminal.type == robot_pb2.SKILL_EVENT_CANCELLED
        assert terminal.code == "COMMAND_EXPIRED"
        assert list(service.execute_for_test(request))[-1] == terminal
    finally:
        service.close()


@pytest.mark.parametrize("yield_count", [1, 2])
def test_owner_stream_closed_after_acceptance_finishes_without_dispatch(monkeypatch, yield_count):
    service = RobotRuntimeService(TabletopWorld.seeded(7))
    request, context = command("observe_scene"), _CancelledRPC()
    monkeypatch.setattr(service,"_dispatch",lambda *_:pytest.fail("abandoned owner dispatched"))
    stream = service.ExecuteSkill(request,context)
    assert next(stream).type == robot_pb2.SKILL_EVENT_ACCEPTED
    if yield_count == 2:
        assert next(stream).type == robot_pb2.SKILL_EVENT_RUNNING
    context.disconnect()
    stream.close()
    try:
        assert request.command_id not in service._active_commands
        assert list(service.execute_for_test(request))[-1].type == robot_pb2.SKILL_EVENT_CANCELLED
    finally:
        service.close()
