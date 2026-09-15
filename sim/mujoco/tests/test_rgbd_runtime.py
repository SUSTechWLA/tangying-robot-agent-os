import itertools
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar

import numpy as np
import pytest
from tangying_robot_gateway.rgbd import RgbdFrame
from tangying_robot_proto.robot.v1 import robot_pb2
from tangying_sim.rgbd_perception import TabletopRgbdPerception
from tangying_sim.rgbd_runtime import RgbdRuntimeService, RgbdTabletopWorld
from tangying_sim.world import TabletopWorld


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.delenv("TANGYING_NAVIGATION_URL", raising=False)
    world = RgbdTabletopWorld.seeded(7)
    service = RgbdRuntimeService(world)
    yield service
    service.close()


@pytest.fixture
def rtab_runtime(monkeypatch):
    monkeypatch.setenv("TANGYING_NAVIGATION_URL", "http://127.0.0.1:8899")
    monkeypatch.setenv("TANGYING_NAVIGATION_TOKEN", "local-test-token")
    service = RgbdRuntimeService(RgbdTabletopWorld.seeded(7))
    yield service
    service.close()


def test_default_fixed_workcell_and_rtab_mobile_mode_have_explicit_capabilities(runtime):
    assert runtime.world.robot_state()["base_pose"][1] == pytest.approx(.05)
    assert "navigation.navigate" not in runtime.GetRuntimeInfo(None, None).skills


def test_active_map_polyline_is_subdivided_to_driver_bound_without_corner_shortcuts(rtab_runtime,monkeypatch):
    from tangying_robot_gateway import grid_navigation
    from tangying_sim.tools import ToolResult

    runtime=rtab_runtime
    runtime._navigation_client=None
    position=[0.,-.5,.035,1.,0.,0.,0.]
    heading=[float(np.cos(.1)),0.,0.,float(np.sin(.1))]
    route=[[0.,-.1,.035,*heading],[.05,-.1,.035,*heading],[.05,.05,.035,*heading]]
    calls=[]
    runtime.workflow.active={"mapId":"map"};runtime.workflow.grid={}
    monkeypatch.setattr(runtime.world,"robot_state",lambda:{"base_pose":position.copy()})
    monkeypatch.setattr(runtime.world,"prepare_navigation",lambda *args:ToolResult(True,"STOWED"))
    monkeypatch.setattr(grid_navigation,"world_route",lambda *args:route)
    def bounded(goal,cancel):
        assert np.linalg.norm(np.array(goal[:2])-position[:2])<=runtime.navigation.limits.max_translation_m
        assert goal[0]==pytest.approx(0.) or goal[1]==pytest.approx(-.1) or goal[0]==pytest.approx(.05)
        assert goal[3:]==[1.,0.,0.,0.]
        position[:]=goal;calls.append(goal)
        return ToolResult(True,"NAV_REACHED")
    def turn_at_endpoint(goal,cancel):
        assert np.allclose(position[:2],route[-1][:2])
        assert goal[3:]==heading
        position[:]=goal;calls.append(goal)
        return ToolResult(True,"NAV_REACHED")
    monkeypatch.setattr(runtime.navigation,"_navigate_single",bounded)
    monkeypatch.setattr(runtime.navigation,"navigate",turn_at_endpoint)
    command=robot_pb2.SkillCommand(skill="navigation.navigate",deadline_unix_ms=int(time.time()*1000)+60000,lease_ms=60000)
    command.parameters.update({"goalPose":route[-1]})
    result=runtime._dispatch(command)
    assert result.success and result.code=="NAV_REACHED"
    assert result.payload["map_route"]=={"mapId":"map","waypoint_count":len(route)}
    assert len(calls)>len(route) and np.allclose(calls[-1],route[-1])


def test_bottom_camera_keeps_robot_navigation_metadata_for_independent_map_ui(rtab_runtime):
    base = rtab_runtime._base_observation()
    assert base.robot_state["navigation"]["backend"] == "rtabmap_nav2"
    assert list(base.robot_state["navigation"]["approach_goal_pose"]) == rtab_runtime.navigation.approach_goal_pose


def test_map_validated_noop_keeps_noop_outcome_and_records_map_identity(rtab_runtime, monkeypatch):
    from tangying_robot_gateway import grid_navigation
    from tangying_sim.tools import ToolResult

    runtime = rtab_runtime
    runtime._navigation_client = None
    pose = runtime.world.robot_state()["base_pose"]
    runtime.workflow.active = {"mapId": "measured-map", "mapRevision": "revision"}
    runtime.workflow.grid = {}
    monkeypatch.setattr(grid_navigation, "world_route", lambda *args: [pose])
    final = ToolResult(True, "NAV_ALREADY_AT_GOAL", "measured at goal", .97, {"marker": "final"})
    monkeypatch.setattr(runtime.navigation, "_navigate_single", lambda *args: final)
    monkeypatch.setattr(runtime.navigation, "navigate", lambda *args: final)
    command = robot_pb2.SkillCommand(skill="navigation.navigate",
        deadline_unix_ms=int(time.time()*1000)+60000, lease_ms=60000)
    command.parameters.update({"goalPose": pose})
    result = runtime._dispatch(command)
    assert result.success and result.code == final.code and result.message == final.message
    assert result.confidence == final.confidence and result.payload["marker"] == "final"
    assert result.payload["map_route"] == {**runtime.workflow.active, "waypoint_count": 1}


def test_active_map_rejects_excessive_final_rotation_before_any_physical_preparation(rtab_runtime,monkeypatch):
    runtime=rtab_runtime
    runtime._navigation_client=None
    runtime.workflow.active={"mapId":"map"}
    monkeypatch.setattr(runtime.world,"robot_state",lambda:{"base_pose":[0.,-.5,.035,1.,0.,0.,0.]})
    def unexpected(*args):
        pytest.fail("invalid final heading must fail before moving the arms or base")
    monkeypatch.setattr(runtime.world,"prepare_navigation",unexpected)
    monkeypatch.setattr(runtime.navigation,"_navigate_single",unexpected)
    command=robot_pb2.SkillCommand(skill="navigation.navigate",deadline_unix_ms=int(time.time()*1000)+60000,lease_ms=60000)
    command.parameters.update({"goalPose":[.05,.05,.035,0.,0.,0.,1.]})
    result=runtime._dispatch(command)
    assert not result.success and result.code=="NAV_ROTATION_LIMIT"


def test_mobile_start_requires_a_measured_approach_and_preserves_visible_targets(rtab_runtime, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("navigation commissioning must not supply perception truth")
    monkeypatch.setattr(TabletopWorld, "entities", forbidden)
    monkeypatch.setattr(rtab_runtime.world, "cached_entities", forbidden)
    start = rtab_runtime.world.robot_state()["base_pose"]
    goal = rtab_runtime.navigation.approach_goal_pose
    assert goal[1] - start[1] == pytest.approx(.65)
    assert rtab_runtime.navigation.limits.world_lower[1] <= start[1]
    observation = rtab_runtime._observation()
    assert {e.entity_id for e in observation.entities} == {"red-cup", "blue-bottle", "left-bin", "right-bin", "front-tray"}
    rtab_runtime.world.reset()
    assert rtab_runtime.world.robot_state()["base_pose"] == start


def test_episode_reset_keeps_rgbd_commissioning_and_disables_implicit_base_motion(runtime):
    original_base = runtime.world.robot_state()["base_pose"]
    runtime.world.reset()
    assert runtime.world.motion.allow_base_motion is False
    assert runtime.world.robot_state()["base_pose"] == original_base
    positions = {e.entity_id: list(e.pose_xyz_quat[:3]) for e in runtime._observation().entities}
    assert positions["red-cup"][0] == pytest.approx(.29, abs=.015)
    assert positions["blue-bottle"][1] == pytest.approx(.49, abs=.015)
    assert "green-cup" not in positions


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


def test_two_cameras_return_their_own_same_frame_rgb_depth_cloud(runtime):
    from google.protobuf.json_format import MessageToDict

    info = runtime.GetRuntimeInfo(None, None)
    sources = {s["sourceId"] for s in MessageToDict(info.robot_profile)["sensors"]}
    assert sources == {f"{runtime._robot_id}/head-rgbd", f"{runtime._robot_id}/base-rgbd"}
    head = next(runtime.Observe(robot_pb2.ObserveRequest(), None))
    base = next(runtime.Observe(robot_pb2.ObserveRequest(source_id=f"{runtime._robot_id}/base-rgbd"), None))
    assert head.reconstruction["sourceId"] == f"{runtime._robot_id}/head-rgbd"
    assert base.reconstruction["sourceId"] == f"{runtime._robot_id}/base-rgbd"
    assert base.reconstruction["sourceFrameId"] == "base_depth_optical"
    assert base.observation_id != head.observation_id
    assert base.compressed_image != head.compressed_image
    assert base.compressed_image.startswith(b"\x89PNG")
    assert base.compressed_depth_image.startswith(b"\x89PNG")
    assert len(base.reconstruction["points"]) > 0
    assert len(base.reconstruction["pointColors"]) == len(base.reconstruction["points"])
    assert len(base.entities) == 0  # Floor evidence does not borrow head semantic entities.


def test_unknown_camera_is_rejected_instead_of_falling_back_to_head(runtime):
    import grpc

    class Aborted(Exception):
        pass

    class Context:
        def abort(self, code, message):
            assert code == grpc.StatusCode.INVALID_ARGUMENT
            raise Aborted(message)

    with pytest.raises(Aborted):
        next(runtime.Observe(robot_pb2.ObserveRequest(source_id="other-robot/camera"), Context()))


@pytest.mark.parametrize("camera", ["head", "base"])
def test_raw_rgbd_retains_same_capture_and_optical_to_flu_transform(runtime, monkeypatch, camera):
    import mujoco

    if camera == "head":
        capture = runtime.capture_scene()
        _, pixels, state = capture
        base_pose = state["base_pose"]
        monkeypatch.setattr(runtime, "capture_scene", lambda: capture)
    else:
        captured = runtime.navigation.capture_with_state()
        pixels, base_pose = captured.frame, captured.base_pose
        monkeypatch.setattr(runtime.navigation, "capture_with_state", lambda: captured)
    observation = next(runtime.Observe(robot_pb2.ObserveRequest(
        source_id=f"{runtime._robot_id}/{camera}-rgbd", streams=["rgbd_raw"],
    ), None))
    assert observation.HasField("rgbd_frame")
    raw = observation.rgbd_frame
    assert (raw.width, raw.height) == (pixels.rgb.shape[1], pixels.rgb.shape[0])
    np.testing.assert_array_equal(np.frombuffer(raw.rgb, dtype=np.uint8).reshape(pixels.rgb.shape), pixels.rgb)
    assert len(raw.depth_metres_f32) == raw.width * raw.height * 4
    np.testing.assert_allclose(np.frombuffer(raw.depth_metres_f32, dtype="<f4").reshape(pixels.depth_m.shape),
                               pixels.depth_m, rtol=1e-7, equal_nan=True)
    np.testing.assert_array_equal(np.asarray(raw.intrinsics).reshape(3, 3), pixels.intrinsics)
    base_from_camera = np.asarray(raw.base_from_camera).reshape(4, 4)
    world_from_base = np.eye(4)
    rotation = np.empty(9)
    mujoco.mju_quat2Mat(rotation, np.asarray(base_pose[3:]))
    world_from_base[:3, :3] = rotation.reshape(3, 3)
    world_from_base[:3, 3] = base_pose[:3]
    np.testing.assert_allclose(world_from_base @ base_from_camera, pixels.world_from_camera, atol=1e-8)
    # The commissioned base already is forward/left/up. At its +90 degree
    # world heading, +base X is world +Y; no additional axis conversion applies.
    np.testing.assert_allclose(world_from_base[:3, :3] @ [1, 0, 0], [0, 1, 0], atol=1e-8)
    assert observation.wall_time_unix_ms == observation.reconstruction["observedAtUnixMs"]
    if camera == "base":
        assert observation.wall_time_unix_ms == pixels.captured_at_unix_ms


def test_default_observation_does_not_include_unrequested_raw_rgbd(runtime):
    for camera in ("head", "base"):
        observation = next(runtime.Observe(robot_pb2.ObserveRequest(
            source_id=f"{runtime._robot_id}/{camera}-rgbd",
        ), None))
        assert not observation.HasField("rgbd_frame")


@pytest.mark.parametrize("camera", ["head", "base"])
def test_sensor_only_retains_original_pixels_and_calibration_without_display_or_inference(runtime, monkeypatch, camera):
    from google.protobuf.json_format import MessageToDict
    from tangying_robot_gateway.contracts import validate_reconstruction
    from tangying_sim import rgbd_runtime

    capture = runtime._head_sensor_capture() if camera == "head" else runtime.navigation.capture_with_state()
    owner, name = (runtime, "_head_sensor_capture") if camera == "head" else (runtime.navigation, "capture_with_state")
    monkeypatch.setattr(owner, name, lambda: capture)
    forbidden = lambda *_args, **_kwargs: pytest.fail("raw SLAM input performed semantic/display work")
    monkeypatch.setattr(runtime.perception, "reconstruct", forbidden)
    monkeypatch.setattr(runtime._base_perception, "reconstruct", forbidden)
    monkeypatch.setattr(rgbd_runtime, "_encode_png", forbidden)
    monkeypatch.setattr(rgbd_runtime, "encode_depth_preview", forbidden)
    observation = next(runtime.Observe(robot_pb2.ObserveRequest(
        source_id=f"{runtime._robot_id}/{camera}-rgbd", streams=["rgbd_raw", "sensor_only"],
    ), None))
    assert not observation.entities and not observation.compressed_image and not observation.compressed_depth_image
    assert observation.rgbd_frame.rgb == capture.frame.rgb.tobytes()
    assert observation.rgbd_frame.depth_metres_f32 == capture.frame.depth_m.astype("<f4").tobytes()
    assert observation.wall_time_unix_ms == capture.frame.captured_at_unix_ms
    assert len(observation.rgbd_frame.robot_self_mask) == capture.frame.depth_m.size
    wire = MessageToDict(observation.reconstruction)
    # Protobuf Struct numbers are doubles; validate exact integer values before
    # passing back through the gateway's strict Python-object contract.
    for key in ("observedAtUnixMs", "sequence"):
        assert wire[key].is_integer() and 0 < wire[key] <= 9_007_199_254_740_991
        wire[key] = int(wire[key])
    scene = validate_reconstruction(wire, runtime._profile_wire)
    assert scene.points == [] and scene.entities == []
    assert MessageToDict(observation.robot_state)["base_pose"] == capture.base_pose


@pytest.mark.parametrize("camera", ["head", "base"])
def test_raw_rgbd_has_same_capture_robot_self_mask_without_private_state(runtime, camera):
    observation = next(runtime.Observe(robot_pb2.ObserveRequest(
        source_id=f"{runtime._robot_id}/{camera}-rgbd", streams=["rgbd_raw"],
    ), None))
    raw = observation.rgbd_frame
    mask = np.frombuffer(raw.robot_self_mask, dtype=np.uint8)
    assert mask.size == raw.width * raw.height
    assert set(mask).issubset({0, 1}) and mask.sum() > 0
    assert raw.self_filter_model_revision
    assert not any(key.startswith("_self_filter") for key in observation.robot_state)
    # Filtering is a separate input to SLAM, never an invented depth image.
    depth = np.frombuffer(raw.depth_metres_f32, dtype="<f4")
    assert np.isfinite(depth[mask == 1]).all()
    assert (depth[mask == 1] > 0).all()


def test_raw_rgbd_rejects_joint_state_from_another_capture(runtime, monkeypatch):
    captured = runtime.capture_scene()
    captured[2]["_self_filter_observed_at_unix_ms"] -= 1
    monkeypatch.setattr(runtime, "capture_scene", lambda: captured)
    with pytest.raises(ValueError, match="same capture"):
        runtime._observation(include_raw=True)


def test_recovery_cannot_move_the_base_without_navigation_evidence(runtime):
    before = runtime.world.robot_state()["base_pose"]
    runtime.world.recover_to_safe_pose()
    assert runtime.world.robot_state()["base_pose"] == before


def test_navigation_cancel_recovery_keeps_intermediate_pose(runtime):
    from types import SimpleNamespace

    before = runtime.world.robot_state()["base_pose"]
    command = robot_pb2.SkillCommand(skill="navigation.navigate")
    runtime._recover_cancelled_command(command, SimpleNamespace(committed=False))
    assert runtime.world.robot_state()["base_pose"] == before


def _navigation_command(runtime, goal):
    command = robot_pb2.SkillCommand(
        schema_version="robot.v1", command_id="navigation-1", task_id="task-1",
        skill="navigation.navigate", deadline_unix_ms=int(time.time()*1000)+5000,
        lease_ms=2000, idempotency_key="navigation-1", safety_profile="simulation",
    )
    command.parameters.update({"goalPose": goal})
    return command


@pytest.mark.parametrize("at_goal", [True, False])
def test_rtab_navigation_checks_actual_odom_and_archives_its_base_capture(rtab_runtime, monkeypatch, at_goal):
    from types import SimpleNamespace

    from tangying_sim.tools import ToolResult

    runtime = rtab_runtime
    assert runtime.world.robot_state()["base_pose"][1] == pytest.approx(-.6)
    assert "navigation.navigate" in runtime.GetRuntimeInfo(None, None).skills
    calls = []

    def navigate(command_id, goal_pose, deadline_unix_ms, cancel_event, apply_velocity, stop):
        calls.append((command_id, deadline_unix_ms))
        assert not cancel_event.is_set()
        assert callable(apply_velocity)
        if not at_goal:
            assert runtime.world.joint_positions()["Pitch_L"] == pytest.approx(3.1)
        stop()
        return ToolResult(True, "NAV_GOAL_REACHED", confidence=1.0,
                          payload={"pose_source": "rtabmap_tf", "map_revision": "test-map"})

    runtime._navigation_client = SimpleNamespace(navigate=navigate)
    monkeypatch.setattr(runtime.navigation, "navigate", lambda *_: pytest.fail("RTAB must not use single-frame fallback"))
    goal = runtime.world.robot_state()["base_pose"]
    if not at_goal:
        goal[1] += 0.03
    started = int(time.time()*1000)
    event = list(runtime.execute_for_test(_navigation_command(runtime, goal)))[-1]
    assert len(calls) == 1
    assert started <= calls[0][1] <= int(time.time()*1000) + 2000
    assert (event.type == robot_pb2.SKILL_EVENT_SUCCEEDED) is at_goal
    if not at_goal:
        assert event.code == "NAV_ODOM_GOAL_NOT_REACHED"
    evidence = event.evidence_observation
    assert evidence.reconstruction["sourceId"] == f"{runtime._robot_id}/base-rgbd"
    assert evidence.robot_state["navigation"]["pose_source"] == "sim_proprioceptive_odom"
    assert evidence.robot_state["navigation"]["map_receipt"]["map_revision"] == "test-map"
    assert evidence.robot_state["navigation"]["passed"] is at_goal


def test_rtab_navigation_rejects_out_of_workspace_before_bridge(rtab_runtime):
    from types import SimpleNamespace

    runtime = rtab_runtime
    runtime._navigation_client = SimpleNamespace(navigate=lambda *_: pytest.fail("invalid goal reached bridge"))
    goal = runtime.world.robot_state()["base_pose"]
    goal[1] = 0.3
    event = list(runtime.execute_for_test(_navigation_command(runtime, goal)))[-1]
    assert event.type == robot_pb2.SKILL_EVENT_FAILED
    assert event.code == "TOOL_PARAMETERS_INVALID"


def test_rtab_camera_status_never_polls_http_or_single_frame_checker(rtab_runtime, monkeypatch):
    from types import SimpleNamespace

    runtime = rtab_runtime
    runtime._navigation_client = SimpleNamespace(status=lambda: pytest.fail("head image must not wait for HTTP"))
    monkeypatch.setattr(runtime.navigation, "status", lambda: pytest.fail("RTAB status cannot use unrelated checker"))
    assert runtime._observation().robot_state["navigation"]["backend"] == "rtabmap_nav2"


def test_rtab_navigation_failure_does_not_fall_back_or_claim_success(rtab_runtime, monkeypatch):
    from types import SimpleNamespace

    from tangying_sim.tools import ToolResult

    runtime = rtab_runtime
    runtime._navigation_client = SimpleNamespace(navigate=lambda *_: ToolResult(False, "NAV_MAP_NOT_READY", confidence=0.0))
    monkeypatch.setattr(runtime.navigation, "navigate", lambda *_: pytest.fail("missing map cannot fall back"))
    goal = runtime.world.robot_state()["base_pose"]
    event = list(runtime.execute_for_test(_navigation_command(runtime, goal)))[-1]
    assert event.type == robot_pb2.SKILL_EVENT_FAILED
    assert event.code == "NAV_MAP_NOT_READY"
    assert event.evidence_observation.robot_state["navigation"]["passed"] is False
    assert runtime.world.robot_state()["base_pose"] == goal


def test_rtab_navigation_configuration_is_explicit_and_requires_token(monkeypatch):
    from types import SimpleNamespace

    from tangying_sim import rgbd_runtime

    monkeypatch.setenv("TANGYING_NAVIGATION_URL", "http://127.0.0.1:8899")
    monkeypatch.delenv("TANGYING_NAVIGATION_TOKEN", raising=False)
    world = RgbdTabletopWorld.seeded(7)
    with pytest.raises(ValueError, match="token"):
        RgbdRuntimeService(world)
    calls = []
    client = SimpleNamespace()
    monkeypatch.setenv("TANGYING_NAVIGATION_TOKEN", "local-test-token")

    def factory(url, token, *, robot_id):
        calls.append((url, token))
        assert robot_id == "xlerobot-mujoco-tabletop"
        return client

    monkeypatch.setattr(rgbd_runtime, "RTABMapClient", factory)
    service = RgbdRuntimeService(world)
    try:
        assert service._navigation_client is client
        assert calls == [("http://127.0.0.1:8899", "local-test-token")]
    finally:
        service.close()


def test_navigation_stow_is_bounded_simultaneous_and_leaves_base_and_jaws(rtab_runtime):
    world = rtab_runtime.world
    before_base = world.robot_state()["base_pose"]
    before_jaws = {key: value for key, value in world.joint_positions().items() if key.startswith("Jaw_")}
    count = world.step_count
    result = world.prepare_navigation(threading.Event())
    assert result.success, result
    assert world.step_count - count == 200
    assert world.robot_state()["base_pose"] == before_base
    assert {key: value for key, value in world.joint_positions().items() if key.startswith("Jaw_")} == before_jaws
    for suffix in ("L", "R"):
        assert world.joint_positions()[f"Pitch_{suffix}"] == pytest.approx(3.1)
        assert world.joint_positions()[f"Elbow_{suffix}"] == pytest.approx(1.0)
    assert world.prepare_navigation(threading.Event()).success  # Already stowed does not move again.
    assert world.step_count - count == 200


@pytest.mark.parametrize("reason", ["cancelled", "holding", "unknown_start"])
def test_navigation_stow_refuses_uncommissioned_or_cancelled_motion(rtab_runtime, reason):
    world = rtab_runtime.world
    cancel = threading.Event()
    if reason == "cancelled":
        cancel.set()
    elif reason == "holding":
        world._held = "red-cup"
    else:
        joint = world.model.joint("Pitch_L").id
        world.data.qpos[world.model.jnt_qposadr[joint]] = .1
    before = world.data.qpos.copy()
    assert not world.prepare_navigation(cancel).success
    np.testing.assert_array_equal(world.data.qpos, before)


def test_stationary_navigation_goal_does_not_allow_unstowed_velocity(rtab_runtime):
    from types import SimpleNamespace

    runtime = rtab_runtime
    before = runtime.world.robot_state()["base_pose"]

    def navigate(_command, _goal, _deadline, cancel, apply_velocity, stop):
        return apply_velocity(.05, 0, 0, .05, cancel_event=cancel)

    runtime._navigation_client = SimpleNamespace(navigate=navigate)
    event = list(runtime.execute_for_test(_navigation_command(runtime, before)))[-1]
    assert event.code == "NAV_STOW_REQUIRED"
    assert runtime.world.robot_state()["base_pose"] == before


def test_actual_mobile_stow_and_bounded_pulses_preserve_camera_manipulation_loop(rtab_runtime):
    from types import SimpleNamespace

    from tangying_sim.tools import ToolResult

    runtime = rtab_runtime

    def controller_contract(_command, goal, _deadline, cancel, apply_velocity, stop):
        # This validates the native controller integration only. RTAB-Map's
        # mapping/planning/TF are independently exercised by its ROS bridge.
        assert runtime.world.joint_positions()["Pitch_L"] == pytest.approx(3.1)
        for _ in range(260):
            # Command age includes lock waits, so a pulse can expire against the
            # 250 ms watchdog while the camera renderer holds the world lock on a
            # loaded host. A refused pulse guarantees no position update, so the
            # production controller reacquires a fresh command while stopped
            # (rtabmap_client.NAV_VELOCITY_STALE). Model that bounded recovery
            # instead of asserting wall-clock scheduling.
            deadline = time.monotonic() + 0.5
            while True:
                pulse = apply_velocity(.05, 0, 0, .05, cancel_event=cancel)
                if pulse.success:
                    break
                assert pulse.code == "NAV_VELOCITY_STALE", pulse
                stop()
                assert time.monotonic() < deadline, pulse
        stop()
        return ToolResult(True, "NAV_GOAL_REACHED", confidence=1.0)

    runtime._navigation_client = SimpleNamespace(navigate=controller_contract)
    goal = runtime.navigation.approach_goal_pose
    command = _navigation_command(runtime, goal)
    command.lease_ms = 60_000
    command.deadline_unix_ms = int(time.time()*1000) + 60_000
    event = list(runtime.execute_for_test(command))[-1]
    assert event.type == robot_pb2.SKILL_EVENT_SUCCEEDED, event
    np.testing.assert_allclose(event.evidence_observation.robot_state["base_pose"], goal, atol=1e-8)
    for name, destination in (("red-cup", "right-bin"), ("blue-bottle", "front-tray")):
        assert runtime.world.pick(name).success
        assert runtime.world.verify_grasp(name).success
        assert runtime.world.place(destination).success
        assert runtime.world.verify_inside(name, destination).success
        np.testing.assert_allclose(runtime.world.robot_state()["base_pose"], goal, atol=1e-8)


def test_colored_cloud_reprojects_to_same_camera_pixels_and_keeps_object_surfaces(runtime):
    scene, pixels, _ = runtime.capture_scene()
    xyz, colors = np.asarray(scene.points), np.asarray(scene.point_colors)
    assert len(xyz) == len(colors) == 4096
    # Use the inverse capture transform to check the emitted data independently
    # of the sampling implementation (including approximate camera rotations).
    optical = np.linalg.solve(
        pixels.world_from_camera[:3, :3],
        (xyz - pixels.world_from_camera[:3, 3]).T,
    ).T
    uv = np.rint(
        optical[:, :2] / optical[:, 2:] * np.diag(pixels.intrinsics)[:2]
        + pixels.intrinsics[:2, 2]
    ).astype(int)
    np.testing.assert_array_equal(colors, pixels.rgb[uv[:, 1], uv[:, 0]])
    np.testing.assert_allclose(optical[:, 2], pixels.depth_m[uv[:, 1], uv[:, 0]], atol=1e-6)
    for entity in scene.entities:
        if entity.category not in {"cup", "bottle"}:
            continue
        delta = np.abs(xyz - np.asarray(entity.pose[:3]))
        near = (delta[:, :2] < 0.06).all(axis=1) & (delta[:, 2] < 0.13)
        channel = 0 if entity.category == "cup" else 2
        colored = (colors[:, channel] > 1.35 * colors[:, 1]) & (
            colors[:, channel] > 1.35 * colors[:, 2 - channel]
        )
        assert np.count_nonzero(near & colored) >= 128, entity.entity_id


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


def test_release_remains_supported_after_real_physics_settling(runtime):
    initial_base = runtime.world.robot_state()["base_pose"]
    assert runtime.world.pick("red-cup").success
    assert runtime.world.place("right-bin").success
    assert runtime.world.verify_inside("red-cup", "right-bin").success
    for _ in range(10):
        runtime.world._step(25)
    assert runtime.world.verify_inside("red-cup", "right-bin").success
    assert runtime.world._held is None
    scene, _, state = runtime.capture_scene()
    cup = next(e for e in scene.entities if e.entity_id == "red-cup")
    assert cup.relation == "inside:right-bin"
    assert all(np.linalg.norm(np.asarray(cup.pose[:3]) - p) > 0.10 for p in state["end_effectors"].values())
    assert state["base_pose"] == initial_base


def test_hovering_above_visual_bin_is_not_placement_evidence(runtime):
    import mujoco

    runtime.world._set_free_body_position("red_cup_free", (0.32, 0.34, 0.851))
    mujoco.mj_forward(runtime.world.model, runtime.world.data)
    scene, _, _ = runtime.capture_scene()
    cup = next(e for e in scene.entities if e.entity_id == "red-cup")
    assert cup.relation != "inside:right-bin"


def test_verification_cannot_count_a_repeated_capture_as_stability(runtime, monkeypatch):
    assert runtime.world.pick("red-cup").success
    assert runtime.world.place("right-bin").success
    capture = runtime.capture_scene()
    monkeypatch.setattr(runtime.world, "capture_scene", lambda: capture)
    result = runtime.world.verify_inside("red-cup", "right-bin")
    assert not result.success


def test_object_moving_within_bin_cannot_pass_stability_verification(runtime, monkeypatch):
    import mujoco

    assert runtime.world.pick("red-cup").success
    assert runtime.world.place("right-bin").success
    original = runtime.capture_scene
    captures = 0

    def displaced():
        nonlocal captures
        captures += 1
        if captures == 2:
            position = runtime.world._joint_position("red_cup_free")
            position[0] += 0.025
            runtime.world._set_free_body_position("red_cup_free", tuple(position))
            mujoco.mj_forward(runtime.world.model, runtime.world.data)
        return original()

    monkeypatch.setattr(runtime.world, "capture_scene", displaced)
    result = runtime.world.verify_inside("red-cup", "right-bin")
    assert not result.success
    assert captures >= 2


def test_unknown_gripper_near_object_cannot_be_placement_evidence(runtime):
    import mujoco
    from tangying_robot_gateway.rgbd import RgbdFrame

    runtime.world._set_free_body_position("red_cup_free", (0.32, 0.34, 0.835))
    mujoco.mj_forward(runtime.world.model, runtime.world.data)
    _, pixels, _ = runtime.capture_scene()
    frame = RgbdFrame(runtime._robot_id, "head", "optical", "cal", int(time.time()*1000), 1,
                      pixels.rgb, pixels.depth_m, pixels.intrinsics, pixels.world_from_camera)
    for gripper in ("closed", "transition"):
        scene = TabletopRgbdPerception().reconstruct(frame, end_effectors={"right": [0.32, 0.34, 0.87]}, grippers={"right": gripper})
        cup = next(e for e in scene.entities if e.entity_id == "red-cup")
        assert cup.relation != "inside:right-bin"


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
        runtime.GetRuntimeInfo(None, None)
        first = runtime._observation()
        # Prove observation completes while the physical command still holds
        # its execution lock. An arbitrary sub-renderer wall-clock threshold
        # instead measured unrelated GPU/ROS load on the host. The observation
        # itself still validates the unchanged capture freshness contract.
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


def test_verification_event_pins_decision_frame_even_if_an_observer_captures_afterward(runtime, monkeypatch):
    from google.protobuf.json_format import ParseDict

    assert runtime.world.pick("red-cup").success
    assert runtime.world.place("right-bin").success
    command = robot_pb2.SkillCommand(
        schema_version="robot.v1", command_id="verify-evidence", task_id="task-1",
        skill="verify_placement", deadline_unix_ms=int(time.time()*1000)+5000,
        lease_ms=2000, idempotency_key="verify-evidence", safety_profile="simulation",
    )
    ParseDict({"objectId": "red-cup", "destinationId": "right-bin"}, command.parameters)
    original = runtime._event

    def later_observer(*args, **kwargs):
        if args[2] == robot_pb2.SKILL_EVENT_SUCCEEDED:
            runtime.capture_scene()  # A background observer must not relabel tool evidence.
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "_event", later_observer)
    result = list(runtime.execute_for_test(command))[-1]
    assert result.type == robot_pb2.SKILL_EVENT_SUCCEEDED
    evidence = result.evidence_observation
    assert evidence.observation_id == runtime.world.verification_capture[0].observation_id
    assert result.observation_id == evidence.observation_id
    assert result.observation_id != runtime._last_scene.observation_id
    assert evidence.compressed_image.startswith(b"\x89PNG")
    assert evidence.compressed_depth_image.startswith(b"\x89PNG")
    assert evidence.robot_state["verification"]["passed"] is True
    assert evidence.robot_state["verification"]["sample_count"] == 3
    assert evidence.robot_state["verification"]["stable_duration_s"] >= 0.1


@pytest.mark.parametrize("confirmation", ["reached", "stale_map", "nonzero_velocity", "outside_tolerance"])
def test_repeated_navigation_after_real_placement_rechecks_a_legal_residual_without_moving_arms(rtab_runtime, confirmation):
    from types import SimpleNamespace

    import mujoco
    from tangying_sim.tools import ToolResult

    runtime = rtab_runtime
    goal = runtime.navigation.approach_goal_pose
    calls = []
    # This regression isolates the final 5 cm and repeated-goal arm posture.
    # Far initialization/visibility and the full 65 cm task have separate checks.
    joint = runtime.world.model.joint("slide_joint_x").id
    runtime.world.data.qpos[runtime.world.model.jnt_qposadr[joint]] = 0
    mujoco.mj_forward(runtime.world.model, runtime.world.data)
    runtime.world._publish_sensor_snapshot()

    def first_approach(_command, _goal, _deadline, cancel, apply_velocity, stop):
        for _ in range(17):
            pulse = apply_velocity(.05, 0, 0, .05, cancel_event=cancel)
            assert pulse.success, pulse
        stop()
        return ToolResult(True, "NAV_GOAL_REACHED", confidence=1.0)

    runtime._navigation_client = SimpleNamespace(navigate=first_approach)
    first = list(runtime.execute_for_test(_navigation_command(runtime, goal)))[-1]
    assert first.type == robot_pb2.SKILL_EVENT_SUCCEEDED, first
    assert first.evidence_observation.robot_state["navigation"]["position_error_m"] == pytest.approx(.0075)
    assert runtime.world.pick("red-cup").success
    assert runtime.world.verify_grasp("red-cup").success
    assert runtime.world.place("right-bin").success
    assert runtime.world.verify_inside("red-cup", "right-bin").success
    assert not runtime.world._verify_navigation_stow("CHECK_ONLY").success
    before = runtime.world.data.qpos.copy()

    def fresh_confirmation(command_id, _goal, deadline, cancel, apply_velocity, stop):
        calls.append(command_id)
        assert deadline > int(time.time() * 1000)
        assert not cancel.is_set()
        if confirmation == "nonzero_velocity":
            return apply_velocity(.01, 0, 0, .05, cancel_event=cancel)
        stop()
        if confirmation == "stale_map":
            return ToolResult(False, "NAV_MAP_NOT_READY", confidence=0.0)
        return ToolResult(True, "NAV_GOAL_REACHED", confidence=1.0,
                          payload={"pose_source": "rtabmap_tf", "map_revision": "fresh-confirmation"})

    runtime._navigation_client = SimpleNamespace(navigate=fresh_confirmation)
    if confirmation == "outside_tolerance":
        goal = list(goal)
        goal[1] += .01
    command = _navigation_command(runtime, goal)
    command.command_id = command.idempotency_key = "navigation-second-object"
    second = list(runtime.execute_for_test(command))[-1]
    assert (second.type == robot_pb2.SKILL_EVENT_SUCCEEDED) is (confirmation == "reached"), second
    assert calls == [command.command_id]
    if confirmation == "outside_tolerance":
        # The post-place HOME pose now has a checked return-to-stow path.
        # Stowing does not make an unexecuted base translation successful.
        assert runtime.world._verify_navigation_stow("CHECK_ONLY").success
        assert runtime.world.robot_state()["base_pose"][1] == pytest.approx(.0425)
    else:
        np.testing.assert_array_equal(runtime.world.data.qpos, before)
    navigation = second.evidence_observation.robot_state["navigation"]
    assert navigation["passed"] is (confirmation == "reached")
    if confirmation == "reached":
        assert navigation["position_error_m"] == pytest.approx(.0075)
        assert navigation["map_receipt"]["map_revision"] == "fresh-confirmation"
        assert runtime.world.pick("blue-bottle").success
        assert runtime.world.verify_grasp("blue-bottle").success
        assert runtime.world.place("front-tray").success
        assert runtime.world.verify_inside("blue-bottle", "front-tray").success
        assert runtime.world.robot_state()["base_pose"][1] == pytest.approx(.0425)
    else:
        assert second.code == {"stale_map": "NAV_MAP_NOT_READY", "nonzero_velocity": "NAV_STOW_REQUIRED",
                               "outside_tolerance": "NAV_ODOM_GOAL_NOT_REACHED"}[confirmation]


@pytest.mark.parametrize("sign", [-1, 1])
def test_navigation_preparation_uses_the_same_wrapped_yaw_tolerance_as_its_receipt(rtab_runtime, sign):
    import math
    from types import SimpleNamespace

    from tangying_sim.tools import ToolResult

    runtime = rtab_runtime
    before = runtime.world.data.qpos.copy()
    goal = runtime.world.robot_state()["base_pose"]
    angle = math.pi / 2 + sign * .025
    goal[3:] = [math.cos(angle / 2), 0, 0, math.sin(angle / 2)]
    calls = []

    def fresh_confirmation(command_id, *_args):
        calls.append(command_id)
        return ToolResult(True, "NAV_GOAL_REACHED", confidence=1.0)

    runtime._navigation_client = SimpleNamespace(navigate=fresh_confirmation)
    event = list(runtime.execute_for_test(_navigation_command(runtime, goal)))[-1]
    assert event.type == robot_pb2.SKILL_EVENT_SUCCEEDED, event
    assert calls == ["navigation-1"]
    assert event.evidence_observation.robot_state["navigation"]["yaw_error_rad"] == pytest.approx(.025)
    np.testing.assert_array_equal(runtime.world.data.qpos, before)


def test_navigation_budget_tracks_commissioned_route_envelope(rtab_runtime):
    from dataclasses import replace
    runtime = rtab_runtime
    runtime.navigation.limits = replace(runtime.navigation.limits,
        world_lower=(-4.,-2.,.035), world_upper=(4.,8.,.035))
    capability = next(item for item in runtime.GetRuntimeInfo(None,None).capabilities if item.name == "navigation.navigate")
    # The contract covers one 18m route at the original .05m/s controller
    # speed, a maximum turn, and the existing 15s preparation allowance.
    required = (18/.05 + runtime.navigation.limits.max_rotation_rad/.2 + 15)*1000
    assert required <= capability.default_timeout_ms <= 600_000
    assert runtime._navigation_route_limit_m() == 18


def test_oversized_map_route_fails_before_preparation(rtab_runtime, monkeypatch):
    from tangying_robot_gateway import grid_navigation
    runtime = rtab_runtime
    runtime._navigation_client = None
    runtime.workflow.active = {"mapId":"measured-map"};runtime.workflow.grid = {}
    pose = runtime.world.robot_state()["base_pose"]
    route = [[pose[0], pose[1]+10, *pose[2:]], pose]
    monkeypatch.setattr(grid_navigation,"world_route",lambda *_:route)
    monkeypatch.setattr(runtime.world,"prepare_navigation",lambda *_:pytest.fail("over-budget route moved arms"))
    monkeypatch.setattr(runtime.navigation,"_navigate_single",lambda *_:pytest.fail("over-budget route moved base"))
    command = _navigation_command(runtime,pose)
    result = runtime._dispatch(command)
    assert not result.success and result.code == "NAV_ROUTE_LIMIT"


def test_mapped_route_expiry_stops_before_next_leg_and_never_confirms_arrival(rtab_runtime,monkeypatch):
    from tangying_robot_gateway import grid_navigation
    from tangying_sim.tools import ToolResult
    runtime = rtab_runtime
    runtime._navigation_client = None
    runtime.workflow.active = {"mapId":"measured-map"};runtime.workflow.grid = {}
    pose = runtime.world.robot_state()["base_pose"]
    route = [[pose[0],pose[1]+.12,*pose[2:]], [pose[0],pose[1]+.24,*pose[2:]]]
    monkeypatch.setattr(grid_navigation,"world_route",lambda *_:route)
    monkeypatch.setattr(runtime.world,"prepare_navigation",lambda *_:ToolResult(True,"STOWED"))
    calls = []
    def delayed_segment(goal,cancel):
        calls.append(goal)
        assert cancel.wait(.5)
        return ToolResult(True,"LATE_SEGMENT")
    monkeypatch.setattr(runtime.navigation,"_navigate_single",delayed_segment)
    monkeypatch.setattr(runtime.navigation,"navigate",lambda *_:pytest.fail("expired route attempted final arrival"))
    command = _navigation_command(runtime,route[-1]);command.lease_ms=80
    terminal = list(runtime.execute_for_test(command))[-1]
    assert len(calls) == 1, terminal
    assert terminal.type == robot_pb2.SKILL_EVENT_CANCELLED and terminal.code == "COMMAND_EXPIRED"


# ── pre-position: stand on certified ground before the task drives ──────────
# A survey can leave the robot on ground its own map never certified (the floor
# under a standing robot is the one patch a forward-facing camera cannot
# measure), and the finished map then refuses the first navigation with
# LOCALIZATION_NOT_CLEAR. The step fixes that without relaxing the check: it only
# moves as far as it must, and it never changes the heading, so the driver's
# 0.5 rad turn guard is never approached either.

class _PrePositionNavigation:
    """Records what the step asked the base to do, and applies it.

    Applying the command matters for the heading loop: it turns until the base
    faces the goal, which it can only observe if the fake world moves with it.
    """

    def __init__(self, limits, state):
        self.limits = limits
        self.state = state
        self.calls: list = []

    def close(self):
        return None

    def navigate(self, goal, cancel, **kwargs):
        from tangying_sim.tools import ToolResult
        self.calls.append(list(goal))
        self.state["base_pose"] = [goal[0], goal[1], goal[2], *goal[3:]]
        return ToolResult(True, "NAV_REACHED", "moved", 1.0, {"base_pose": list(goal)})


def _pre_position_service(monkeypatch, *, cells, pose, radius=0.2, revision="rev-1"):
    import numpy as np
    from tangying_robot_gateway.navigation_map import validate_grid

    service = RgbdRuntimeService(RgbdTabletopWorld.seeded(7))

    class Workflow:
        active: ClassVar[dict] = {"mapId": "scan-test", "mapRevision": revision,
                                  "calibrationRevision": "c" * 64}
        map_from_world = np.zeros(3)
        footprint_radius = radius
        _worker = None
        grid = validate_grid({"width": cells.shape[1], "height": cells.shape[0],
                              "resolution": .05, "origin": [0., 0., 0.], "cells": cells})

        @staticmethod
        def cancel(_parameters=None):
            return {"status": "idle"}

    service.workflow = Workflow()
    # A mobile robot's profile declares the tool; the fixed workcell this fixture
    # is built on does not, so declare it here exactly as GetRuntimeInfo would.
    service.GetRuntimeInfo(None, None)
    service._profile_wire["tools"] = [*service._profile_wire["tools"], "navigation.pre_position"]
    state = {"base_pose": [*pose, .035, 1., 0., 0., 0.]}
    service.world.robot_state = lambda: state
    navigation = _PrePositionNavigation(service.navigation.limits, state)
    service.navigation = navigation
    service._navigation_client = None
    return service, navigation


def test_pre_position_leaves_a_certified_base_alone(monkeypatch):
    import numpy as np
    cells = np.zeros((80, 80), dtype=np.int16)
    service, navigation = _pre_position_service(monkeypatch, cells=cells, pose=[2.0, 2.0])
    try:
        result = service._pre_position(_command("navigation.pre_position"), _Active())
        assert result.success and result.code == "PRE_POSITION_ALREADY_CLEAR"
        assert result.payload["moved"] is False and navigation.calls == []
    finally:
        service.close()


def test_pre_position_steps_onto_clear_floor_and_keeps_the_heading(monkeypatch):
    import numpy as np
    cells = np.zeros((80, 80), dtype=np.int16)
    # Unknown ground exactly where the robot stands, clear floor around it.
    col, row = int(2.0 / .05), int(2.0 / .05)
    cells[row - 2:row + 3, col - 2:col + 3] = -1
    service, navigation = _pre_position_service(monkeypatch, cells=cells, pose=[2.0, 2.0],
                                                radius=0.15)
    try:
        result = service._pre_position(_command("navigation.pre_position"), _Active())
        assert result.success and result.code == "PRE_POSITION_MOVED", result.message
        assert result.payload["moved"] is True
        assert 0 < result.payload["offsetM"] <= service.PRE_POSITION_MAX_OFFSET_M
        assert result.payload["clearanceRadiusM"] == 0.15
        assert len(navigation.calls) == 1
        # Heading unchanged is what keeps the move inside the turn guard.
        assert navigation.calls[0][3] == pytest.approx(1.0) and navigation.calls[0][6] == pytest.approx(0.0)
    finally:
        service.close()


def test_pre_position_refuses_when_no_clear_floor_is_within_reach(monkeypatch):
    import numpy as np
    cells = np.full((80, 80), -1, dtype=np.int16)
    service, navigation = _pre_position_service(monkeypatch, cells=cells, pose=[2.0, 2.0])
    try:
        result = service._pre_position(_command("navigation.pre_position"), _Active())
        assert not result.success and result.code == "PRE_POSITION_NO_CLEAR_POSE"
        assert navigation.calls == []
    finally:
        service.close()


def test_pre_position_turns_to_face_the_first_goal_in_bounded_steps(monkeypatch):
    """Measured: a task failed its first navigation with NAV_ROTATION_LIMIT while
    standing on clear floor, because the goal heading was more than the driver's
    0.5 rad budget away. The step turns in place - each command inside that budget
    - and stops with the heading reachable rather than exactly on target.
    """
    import numpy as np
    cells = np.zeros((80, 80), dtype=np.int16)
    service, navigation = _pre_position_service(monkeypatch, cells=cells, pose=[2.0, 2.0])
    try:
        limit = service.navigation.limits.max_rotation_rad
        result = service._pre_position(
            _command("navigation.pre_position", parameters={"alignYaw": 2.6}), _Active())
        assert result.success and result.code == "PRE_POSITION_ALREADY_CLEAR"
        assert result.payload["turnsTaken"] >= 3, result.payload
        # The command's *change* of heading is what the driver bounds, so each
        # step must stay inside the budget - the absolute heading may be anything.
        assert len(navigation.calls) == result.payload["turnsTaken"]
        headings = [0.0] + [2 * np.arctan2(call[6], call[3]) for call in navigation.calls]
        deltas = [abs(np.arctan2(np.sin(b - a), np.cos(b - a)))
                  for a, b in itertools.pairwise(headings)]
        assert deltas and all(value <= limit + 1e-9 for value in deltas), deltas
        assert abs(result.payload["headingDeltaRad"]) <= limit * 0.6 + 1e-9, result.payload
        # Turning happens in place: the position never changes.
        assert all((call[0], call[1]) == (2.0, 2.0) for call in navigation.calls)
        # And the point of it: the next navigation's goal is now reachable.
        final_yaw = 2 * np.arctan2(navigation.calls[-1][6], navigation.calls[-1][3])
        assert abs(result.payload["headingDeltaRad"] - (2.6 - final_yaw)) < 1e-6
    finally:
        service.close()


def test_pre_position_needs_an_active_map(monkeypatch):
    service = RgbdRuntimeService(RgbdTabletopWorld.seeded(7))
    try:
        result = service._pre_position(_command("navigation.pre_position"), _Active())
        assert not result.success and result.code == "PRE_POSITION_UNAVAILABLE"
    finally:
        service.close()


class _Cancellation:
    """Minimal stand-in for the runtime's per-command cancellation record."""

    expired = False

    @staticmethod
    def is_set():
        return False


class _Active:
    """Minimal stand-in for the runtime's active-command record."""

    deadline_unix_ms = 0
    cancel_event = _Cancellation()


def _command(skill, parameters=None):
    from tangying_robot_proto.robot.v1 import robot_pb2
    command = robot_pb2.SkillCommand(schema_version="robot.v1", command_id="cmd-1",
                                     idempotency_key="idem-1", skill=skill, robot_id="robot-1")
    command.parameters.update(parameters or {})
    return command
