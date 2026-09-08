import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from tangying_robot_gateway.rgbd import RgbdFrame
from tangying_robot_proto.robot.v1 import robot_pb2
from tangying_sim.rgbd_perception import TabletopRgbdPerception
from tangying_sim.rgbd_runtime import RgbdRuntimeService, RgbdTabletopWorld
from tangying_sim.world import TabletopWorld


@pytest.fixture
def runtime():
    world = RgbdTabletopWorld.seeded(7)
    service = RgbdRuntimeService(world)
    yield service
    service.close()


def test_closed_gripper_near_unlifted_object_is_not_grasp_evidence():
    rgb = np.zeros((100, 100, 3), dtype=np.uint8)
    rgb[:] = [120, 80, 50]
    depth = np.full((100, 100), 0.8)
    rgb[45:55, 45:55] = [255, 0, 0]
    depth[45:55, 45:55] = 0.68
    transform = np.diag([1.0, -1.0, -1.0, 1.0])
    transform[:3, 3] = [0, 0.6, 1.5]
    frame = RgbdFrame(
        "robot-1",
        "head",
        "optical",
        "cal",
        int(time.time() * 1000),
        1,
        rgb,
        depth,
        np.array([[100.0, 0, 49.5], [0, 100.0, 49.5], [0, 0, 1.0]]),
        transform,
    )
    scene = TabletopRgbdPerception().reconstruct(
        frame, end_effectors={"left": [0, 0.6, 0.76]}, grippers={"left": "closed"}
    )
    assert scene.entities and scene.entities[0].relation != "held_by:robot-1"


def test_rgbd_observation_has_no_legacy_entity_oracle(runtime, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("simulator entity truth must not enter RGB-D perception")

    monkeypatch.setattr(TabletopWorld, "entities", forbidden)
    monkeypatch.setattr(runtime.world, "cached_entities", forbidden)
    observation = runtime._observation()
    assert {e.entity_id for e in observation.entities} == {
        "red-cup",
        "blue-bottle",
        "left-bin",
        "right-bin",
        "front-tray",
    }
    assert observation.reconstruction["sourceType"] == "rgbd_camera"
    assert observation.compressed_image.startswith(b"\x89PNG")
    assert observation.compressed_depth_image.startswith(b"\x89PNG")
    assert not runtime.world.has_object("green-cup")
    assert not runtime.world.has_destination("invisible-bin")


def test_two_goals_change_environment_and_camera_verifies_result(runtime):
    for name, destination in [("red-cup", "right-bin"), ("blue-bottle", "front-tray")]:
        assert runtime.world.pick(name).success
        assert runtime.world.verify_grasp(name).success
        assert runtime.world.place(destination).success
        assert runtime.world.verify_inside(name, destination).success
    scene = runtime._observation()
    relations = {e.entity_id: e.relation for e in scene.entities}
    assert relations["red-cup"] == "inside:right-bin"
    assert relations["blue-bottle"] == "inside:front-tray"
    assert runtime.world.pick_count == 2


def test_command_receipts_do_not_create_visual_placement_evidence(runtime):
    runtime.world._placements["red-cup"] = "right-bin"
    assert not runtime.world.verify_inside("red-cup", "right-bin").success


def test_depth_loss_blocks_grounding_and_physical_action(runtime, monkeypatch):
    original = runtime.renderer.render_rgbd

    def missing(*args):
        from dataclasses import replace

        captured = original(*args)
        return replace(captured, depth_m=np.zeros_like(captured.depth_m))

    monkeypatch.setattr(runtime.renderer, "render_rgbd", missing)
    assert not runtime._observation().entities
    assert not runtime.world.pick("red-cup").success
    assert runtime.world.pick_count == 0


def test_slow_motion_does_not_block_camera_or_runtime_info(runtime):
    runtime.world.motion.step_delay = 0.03
    with ThreadPoolExecutor() as pool:
        moving = pool.submit(runtime.world.pick, "red-cup")
        time.sleep(0.06)
        started = time.monotonic()
        runtime.GetRuntimeInfo(None, None)
        first = runtime._observation()
        assert time.monotonic() - started < 0.4
        assert not moving.done()
        second = runtime._observation()
        assert second.wall_time_unix_ms >= first.wall_time_unix_ms
        assert moving.result(timeout=10).success


def test_stalled_snapshot_does_not_get_a_fresh_timestamp(runtime):
    ready, release = threading.Event(), threading.Event()

    def stall():
        with runtime.world.lock:
            data, state, _ = runtime.world.sensor_snapshot
            runtime.world.sensor_snapshot = (data, state, int(time.time() * 1000) - 3000)
            ready.set()
            release.wait(5)

    with ThreadPoolExecutor() as pool:
        future = pool.submit(stall)
        ready.wait(2)
        try:
            with pytest.raises(ValueError, match="stale"):
                runtime._observation()
        finally:
            release.set()
        future.result()


@pytest.mark.parametrize("offset_ms", [-3000, 5000], ids=["stale", "future"])
def test_renderer_capture_time_cannot_be_hidden_by_new_world_snapshot(runtime, monkeypatch, offset_ms):
    from dataclasses import replace

    pixels = runtime.renderer.render_rgbd(runtime.world.model, runtime.world.data)
    invalid = replace(pixels, captured_at_unix_ms=int(time.time() * 1000) + offset_ms)
    monkeypatch.setattr(runtime.renderer, "render_rgbd", lambda *_: invalid)

    with pytest.raises(ValueError, match="stale|future"):
        runtime._observation()


def test_cached_renderer_frame_preserves_its_original_capture_time(runtime, monkeypatch):
    from dataclasses import replace

    pixels = runtime.renderer.render_rgbd(runtime.world.model, runtime.world.data)
    captured_at = int(time.time() * 1000) - 500
    cached = replace(pixels, captured_at_unix_ms=captured_at)
    monkeypatch.setattr(runtime.renderer, "render_rgbd", lambda *_: cached)

    observation = runtime._observation()
    assert observation.wall_time_unix_ms == captured_at
    assert observation.reconstruction["observedAtUnixMs"] == captured_at


def test_capture_that_expires_during_perception_is_rejected(runtime, monkeypatch):
    from types import SimpleNamespace

    from tangying_robot_gateway import rgbd

    original = runtime.perception.reconstruct

    def slow_perception(*args, **kwargs):
        result = original(*args, **kwargs)
        # Advance only the freshness-check clock, without a slow wall-clock test.
        expired_time = time.time() + 3
        monkeypatch.setattr(rgbd, "time", SimpleNamespace(time=lambda: expired_time))
        return result

    monkeypatch.setattr(runtime.perception, "reconstruct", slow_perception)
    with pytest.raises(ValueError, match="stale"):
        runtime._observation()


def test_success_events_reference_actual_camera_evidence(runtime):
    command = robot_pb2.SkillCommand(
        schema_version="robot.v1",
        command_id="observe-1",
        task_id="task-1",
        skill="observe_scene",
        deadline_unix_ms=int(time.time() * 1000) + 5000,
        lease_ms=2000,
        idempotency_key="observe-1",
        safety_profile="simulation",
    )
    events = list(runtime.execute_for_test(command))
    assert events[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert events[-1].observation_id.startswith(runtime._robot_id + "/head-rgbd-")
