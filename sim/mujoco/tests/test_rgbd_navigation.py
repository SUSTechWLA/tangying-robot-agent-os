import copy
import threading
import time
from dataclasses import replace

import mujoco
import numpy as np
import pytest
from tangying_robot_gateway.rgbd import RgbdFrame
from tangying_sim.model import load_task_model, validate_task_model
from tangying_sim.rgbd_navigation import (
    NavigationController,
    NavigationLimits,
    check_navigation,
    load_navigation_model,
    robot_local_bounds,
    validate_navigation_model,
)
from tangying_sim.self_filter import robot_joint_positions
from tangying_sim.world import TabletopWorld


def frame():
    transform = np.diag([1.0, -1.0, -1.0, 1.0])
    transform[:3, 3] = [0, 0, 2]
    return RgbdFrame(
        "robot-1", "head", "optical", "cal-1", int(time.time() * 1000), 1,
        np.zeros((100, 100, 3), dtype=np.uint8), np.full((100, 100), 2.0),
        np.array([[100.0, 0, 49.5], [0, 100.0, 49.5], [0, 0, 1.0]]), transform,
    )


def limits():
    return NavigationLimits(
        world_lower=(-0.5, -0.5, 0.035), world_upper=(0.5, 0.5, 0.035),
        footprint_half_extents=(0.2, 0.2), body_height_m=0.6,
    )


def check(capture, goal=None, **kwargs):
    return check_navigation(
        capture, [0, 0, 0.035, 1, 0, 0, 0],
        goal if goal is not None else [0.1, 0, 0.035, 1, 0, 0, 0],
        base_observed_at_unix_ms=capture.captured_at_unix_ms,
        limits=limits(), **kwargs,
    )


def test_full_same_frame_depth_can_certify_short_swept_body():
    result = check(frame())
    assert result.allowed and not result.already_at_goal
    assert result.code == "NAV_PATH_OBSERVED_CLEAR"
    assert result.checked_pixels > 0


@pytest.mark.parametrize("depth, expected", [
    (0.0, "NAV_DEPTH_UNKNOWN"),
    (float("nan"), "NAV_DEPTH_UNKNOWN"),
    (6.0, "NAV_DEPTH_UNKNOWN"),
    (0.3, "NAV_PATH_OCCLUDED"),
    (1.6, "NAV_OBSTACLE_OBSERVED"),
])
def test_missing_occluded_or_occupied_path_is_not_free(depth, expected):
    capture = frame()
    # This ray intersects the new body volume near world (0.264, 0.008, 0.4).
    capture.depth_m[49, 66] = depth
    result = check(capture)
    assert not result.allowed
    assert result.code == expected


def test_out_of_view_swept_volume_is_unknown():
    capture = frame()
    capture.intrinsics[0, 0] = 500
    result = check(capture)
    assert not result.allowed and result.code == "NAV_PATH_OUT_OF_VIEW"


def test_existing_goal_does_not_claim_a_motion_or_free_path():
    capture = frame()
    capture.depth_m[:] = 0
    result = check(capture, [0, 0, 0.035, 1, 0, 0, 0])
    assert result.allowed and result.already_at_goal
    assert result.code == "NAV_ALREADY_AT_GOAL" and result.checked_pixels == 0


def test_old_camera_or_mismatched_base_capture_is_rejected():
    capture = replace(frame(), captured_at_unix_ms=int(time.time() * 1000) - 3000)
    assert check(capture).code == "NAV_OBSERVATION_INVALID"
    fresh = frame()
    result = check_navigation(
        fresh, [0, 0, 0.035, 1, 0, 0, 0], [0.1, 0, 0.035, 1, 0, 0, 0],
        base_observed_at_unix_ms=fresh.captured_at_unix_ms - 1, limits=limits(),
    )
    assert not result.allowed and result.code == "NAV_BASE_CAPTURE_MISMATCH"


@pytest.mark.parametrize("goal, expected", [
    ([0.16, 0, 0.035, 1, 0, 0, 0], "NAV_DISTANCE_LIMIT"),
    ([0.6, 0, 0.035, 1, 0, 0, 0], "NAV_WORKSPACE_LIMIT"),
    ([0.1, 0, 0.1, 1, 0, 0, 0], "NAV_WORKSPACE_LIMIT"),
    ([0.1, 0, 0.035, 0.70710678, 0, 0, 0.70710678], "NAV_ROTATION_UNSUPPORTED"),
    ([True, 0, 0.035, 1, 0, 0, 0], "NAV_POSE_INVALID"),
])
def test_uncommissioned_motion_is_rejected(goal, expected):
    result = check(frame(), goal)
    assert not result.allowed and result.code == expected


def test_checker_is_pure_and_uses_world_pose_not_slide_joint_name():
    capture = frame()
    before = capture.depth_m.copy()
    q = [2**-0.5, 0, 0, 2**-0.5]
    result = check_navigation(
        capture, [0, 0, 0.035, *q], [0, 0.1, 0.035, *q],
        base_observed_at_unix_ms=capture.captured_at_unix_ms, limits=limits(),
    )
    assert result.allowed
    np.testing.assert_array_equal(capture.depth_m, before)


class CameraWorld(TabletopWorld):
    def _load_model(self, path):
        return load_navigation_model(path)

    def _validate_model(self, model):
        validate_navigation_model(model)

    def _publish_sensor_snapshot(self):
        captured_at = int(time.time()*1000)
        data, state = copy.copy(self.data), self.robot_state()
        state["_self_filter_joint_positions"] = robot_joint_positions(self.model, data)
        state["_self_filter_observed_at_unix_ms"] = captured_at
        self.sensor_snapshot = data, state, captured_at

    def _refresh_observation_cache(self):
        self._publish_sensor_snapshot()


@pytest.fixture
def controller():
    world = CameraWorld.seeded(7)
    world._publish_sensor_snapshot()
    controller = NavigationController(world, world.robot_id)
    yield controller
    controller.close()


def test_base_camera_is_fixed_to_chassis_and_capture_tf_tracks_world_motion(controller):
    camera = controller.world.model.camera("base_depth").id
    assert controller.world.model.cam_bodyid[camera] == controller.world.model.body("chassis").id
    assert controller.world.model.cam_mode[camera] == mujoco.mjtCamLight.mjCAMLIGHT_FIXED
    first, first_base = controller.capture()
    assert first.source_id.endswith("/base-rgbd")
    assert first.frame_id == "base_depth_optical"
    assert np.isfinite(first.depth_m).all() and first.rgb.shape == (240, 320, 3)
    with controller.world.lock:
        address = int(controller.world.model.joint("slide_joint_x").qposadr[0])
        controller.world.data.qpos[address] += 0.025
        mujoco.mj_forward(controller.world.model, controller.world.data)
        controller.world._publish_sensor_snapshot()
    second, second_base = controller.capture()
    delta = np.asarray(second_base[:3]) - np.asarray(first_base[:3])
    np.testing.assert_allclose(delta, [0, 0.025, 0], atol=1e-7)
    np.testing.assert_allclose(second.world_from_camera[:3, 3]-first.world_from_camera[:3, 3], delta, atol=1e-7)
    assert second.captured_at_unix_ms >= first.captured_at_unix_ms


def test_rgbd_model_removes_only_duplicate_world_cart_without_changing_pinned_asset():
    model = load_navigation_model()
    validate_navigation_model(model)
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "ikea_cart") == -1
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "cart_top") == -1
    assert model.body("chassis").id > 0
    assert model.geom("base_plate_layer1-v5-1_geom").id >= 0
    original = load_task_model()
    validate_task_model(original)
    assert original.body("ikea_cart").id > 0 and original.camera("cart_depth").id >= 0


def test_reference_camera_blind_volume_blocks_real_base_motion(controller):
    before = controller.world.data.qpos.copy()
    outcome = controller.navigate([0, 0.05, 0.035, 2**-0.5, 0, 0, 2**-0.5])
    assert not outcome.success
    assert outcome.code in {"NAV_PATH_OUT_OF_VIEW", "NAV_PATH_OCCLUDED", "NAV_DEPTH_UNKNOWN", "NAV_OBSTACLE_OBSERVED"}
    np.testing.assert_array_equal(controller.world.data.qpos, before)


def _synthetic_clear_capture(controller):
    # A controlled overhead depth fixture proves the control loop separately
    # from the real low-camera blind-spot rejection test above.
    capture = frame()
    # Synthetic far surface is below the conservative wheel mesh AABB.
    capture.depth_m[:] = 4.5
    capture.world_from_camera[2, 3] = 4.0
    base = controller.world.robot_state()["base_pose"]
    return capture, base


def test_observed_clear_fixture_moves_real_base_in_world_y_with_speed_bound(controller, monkeypatch):
    monkeypatch.setattr(controller, "capture", lambda: _synthetic_clear_capture(controller))
    waits = []
    monkeypatch.setattr("tangying_sim.rgbd_navigation.time.sleep", waits.append)
    before = controller.world.robot_state()["base_pose"]
    outcome = controller.navigate([0, 0.025, 0.035, *before[3:]])
    after = controller.world.robot_state()["base_pose"]
    assert outcome.success and outcome.code == "NAV_REACHED"
    assert abs(after[0]-before[0]) < 1e-7
    assert 0.020 <= after[1] <= 0.025
    assert waits and sum(waits) >= (after[1]-before[1])/controller.MAX_LINEAR_SPEED_M_S - 1e-6
    np.testing.assert_allclose(outcome.payload["base_pose"], after)
    assert outcome.payload["capture"].captured_at_unix_ms > 0


def test_navigation_receipt_keeps_exact_check_capture_when_shared_last_frame_changes(controller, monkeypatch):
    capture, base = controller.capture()
    unrelated = replace(capture, sequence=capture.sequence+1)

    def selected_capture():
        controller.last_frame = unrelated
        return capture, base

    monkeypatch.setattr(controller, "capture", selected_capture)
    outcome = controller.navigate(base)
    assert outcome.success and outcome.code == "NAV_ALREADY_AT_GOAL"
    assert outcome.payload["capture"] is capture
    assert outcome.payload["capture"] is not controller.last_frame
    assert outcome.payload["base_pose"] == base


def test_cancel_during_bounded_wait_never_moves_or_returns_home(controller, monkeypatch):
    stop = threading.Event()
    monkeypatch.setattr(controller, "capture", lambda: _synthetic_clear_capture(controller))
    monkeypatch.setattr("tangying_sim.rgbd_navigation.time.sleep", lambda _: stop.set())
    before = controller.world.data.qpos.copy()
    outcome = controller.navigate([0, 0.025, 0.035, 2**-0.5, 0, 0, 2**-0.5], stop)
    assert not outcome.success and outcome.code == "CANCELLED"
    np.testing.assert_array_equal(controller.world.data.qpos, before)


def test_depth_loss_after_one_step_stops_without_rollback_or_additional_motion(controller, monkeypatch):
    calls = 0

    def capture():
        nonlocal calls
        calls += 1
        value, base = _synthetic_clear_capture(controller)
        if calls > 1:
            value.depth_m[:] = 0
        return value, base

    monkeypatch.setattr(controller, "capture", capture)
    monkeypatch.setattr("tangying_sim.rgbd_navigation.time.sleep", lambda _: None)
    outcome = controller.navigate([0, 0.025, 0.035, 2**-0.5, 0, 0, 2**-0.5])
    assert not outcome.success and outcome.code == "NAV_DEPTH_UNKNOWN"
    assert 0 < controller.world.robot_state()["base_pose"][1] <= controller.MAX_STEP_M + 1e-7


def test_base_camera_never_restamps_old_renderer_pixels(controller, monkeypatch):
    original = controller.renderer.render_rgbd

    def old(*args):
        return replace(original(*args), captured_at_unix_ms=int(time.time()*1000)-3000)

    monkeypatch.setattr(controller.renderer, "render_rgbd", old)
    with pytest.raises(ValueError, match="stale"):
        controller.capture()


def test_commissioned_downward_camera_observes_near_front_ground(controller):
    capture, _ = controller.capture()
    points = np.array(np.meshgrid(np.linspace(-0.2, 0.2, 41), np.linspace(0.205, 0.30, 20), [0.01], indexing="ij")).reshape(3, -1).T
    optical = (points-capture.world_from_camera[:3, 3]) @ capture.world_from_camera[:3, :3]
    k = capture.intrinsics
    uv = np.rint(optical[:, :2]/optical[:, 2, None]*[k[0, 0], k[1, 1]]+[k[0, 2], k[1, 2]]).astype(int)
    assert np.all(optical[:, 2] > 0.02)
    assert np.all((uv >= [0, 0]) & (uv < [320, 240]))
    measured = capture.depth_m[uv[:, 1], uv[:, 0]]
    assert np.all(np.isfinite(measured) & (measured <= 5.0))
    # This only proves these floor rays, not full-body navigation clearance.
    assert np.all(measured > optical[:, 2]+0.002)
    assert capture.transform_revision == "base-front-down45-v2"


def test_conservative_diagnostic_envelope_includes_initial_and_stowed_robot_cad(controller):
    model, data = controller.world.model, controller.world.data
    chassis = model.body("chassis").id
    bodies = {chassis}
    for body in range(chassis+1, model.nbody):
        if model.body_parentid[body] in bodies:
            bodies.add(body)
    original = data.qpos.copy()
    target = original.copy()
    for suffix in ("L", "R"):
        for name, value in (("Rotation", 0), ("Pitch", 3.1), ("Elbow", 1.0), ("Wrist_Pitch", 0), ("Wrist_Roll", 0)):
            target[model.joint(f"{name}_{suffix}").qposadr[0]] = value
    try:
        for fraction in np.linspace(0, 1, 9):
            data.qpos[:] = original+(target-original)*fraction
            mujoco.mj_forward(model, data)
            rotation = data.xmat[chassis].reshape(3, 3)
            for geom in range(model.ngeom):
                if model.geom_bodyid[geom] not in bodies:
                    continue
                local_rotation = rotation.T @ data.geom_xmat[geom].reshape(3, 3)
                center = (data.geom_xpos[geom]-data.xpos[chassis]) @ rotation + local_rotation @ model.geom_aabb[geom, :3]
                half = np.abs(local_rotation) @ model.geom_aabb[geom, 3:]
                assert np.all(np.abs(center[:2])+half[:2] <= controller.limits.footprint_half_extents)
                assert center[2]-half[2] >= controller.limits.body_bottom_offset_m
                assert center[2]+half[2] <= controller.limits.body_height_m
    finally:
        data.qpos[:] = original
        mujoco.mj_forward(model, data)


def test_robot_cad_bounds_are_invariant_under_base_pose_and_exclude_environment(controller):
    model, data = controller.world.model, controller.world.data
    # Use the commissioned zero-joint pose, before legacy fixture physics drift.
    for joint in range(model.njnt):
        if model.jnt_type[joint] != mujoco.mjtJoint.mjJNT_FREE:
            data.qpos[model.jnt_qposadr[joint]] = 0
    mujoco.mj_forward(model, data)
    lower, upper = robot_local_bounds(model, data)
    np.testing.assert_allclose(lower, [-0.23, -0.21841884, -0.05426936], atol=1e-5)
    np.testing.assert_allclose(upper, [0.58243401, 0.2, 0.90332489], atol=1e-5)
    data.qpos[model.joint("slide_joint_x").qposadr[0]] += 0.025
    data.qpos[model.joint("hinge_joint_z").qposadr[0]] += 0.15
    model.body_pos[model.body("table").id] += [3, 2, 1]
    mujoco.mj_forward(model, data)
    moved_lower, moved_upper = robot_local_bounds(model, data)
    np.testing.assert_allclose(moved_lower, lower, atol=1e-8)
    np.testing.assert_allclose(moved_upper, upper, atol=1e-8)


@pytest.mark.parametrize("vx, vy, wz, expected", [
    (0.04, 0.0, 0.0, [0.0, 0.002, 0.0]),
    (0.0, 0.04, 0.0, [-0.002, 0.0, 0.0]),
    (0.0, 0.0, 0.2, [0.0, 0.0, 0.01]),
])
def test_velocity_uses_actual_flu_base_axes_and_stops_at_end_of_pulse(controller, monkeypatch, vx, vy, wz, expected):
    monkeypatch.setattr("tangying_sim.rgbd_navigation.time.sleep", lambda _: None)
    before = controller.world.robot_state()["base_pose"]
    result = controller.apply_velocity(vx, vy, wz, 0.05)
    after = controller.world.robot_state()["base_pose"]
    delta_yaw = 2*np.arctan2(after[6], after[3]) - 2*np.arctan2(before[6], before[3])
    assert result.success and result.code == "NAV_VELOCITY_APPLIED"
    np.testing.assert_allclose([after[0]-before[0], after[1]-before[1], delta_yaw], expected, atol=1e-7)
    for joint in ("slide_joint_x", "slide_joint_y", "hinge_joint_z"):
        assert controller.world.data.qvel[controller.world.model.joint(joint).dofadr[0]] == 0


@pytest.mark.parametrize("values", [
    (float("nan"), 0, 0, 0.05), (True, 0, 0, 0.05), (0.05, 0.05, 0, 0.05),
    (0, 0, 0.21, 0.05), (0, 0, 0, 0), (0.04, 0, 0, 0.051),
])
def test_invalid_velocity_is_rejected_without_clipping_or_moving(controller, values):
    before = controller.world.data.qpos.copy()
    result = controller.apply_velocity(*values)
    assert not result.success and result.code == "NAV_VELOCITY_INVALID"
    np.testing.assert_array_equal(controller.world.data.qpos, before)


def test_velocity_stale_command_or_expiry_during_wait_never_moves(controller, monkeypatch):
    before = controller.world.data.qpos.copy()
    stale = controller.apply_velocity(0.04, 0, 0, 0.05, command_age_s=0.251)
    assert not stale.success and stale.code == "NAV_VELOCITY_STALE"
    now = [100.0]
    monkeypatch.setattr("tangying_sim.rgbd_navigation.time.monotonic", lambda: now[0])
    monkeypatch.setattr("tangying_sim.rgbd_navigation.time.sleep", lambda _: now.__setitem__(0, now[0]+0.3))
    expired = controller.apply_velocity(0.04, 0, 0, 0.05)
    assert not expired.success and expired.code == "NAV_VELOCITY_STALE"
    np.testing.assert_array_equal(controller.world.data.qpos, before)


def test_stop_invalidates_pending_velocity_pulse_without_returning_home(controller, monkeypatch):
    before = controller.world.data.qpos.copy()
    monkeypatch.setattr("tangying_sim.rgbd_navigation.time.sleep", lambda _: controller.stop())
    result = controller.apply_velocity(0.04, 0, 0, 0.05)
    assert not result.success and result.code == "CANCELLED"
    np.testing.assert_array_equal(controller.world.data.qpos, before)


def test_cancel_and_workspace_limit_prevent_velocity_pulse(controller, monkeypatch):
    before = controller.world.data.qpos.copy()
    stop = threading.Event()
    stop.set()
    result = controller.apply_velocity(0.04, 0, 0, 0.05, cancel_event=stop)
    assert not result.success and result.code == "CANCELLED"
    controller.limits = replace(controller.limits, world_upper=(0.15, 0.001, 0.035))
    monkeypatch.setattr("tangying_sim.rgbd_navigation.time.sleep", lambda _: None)
    result = controller.apply_velocity(0.04, 0, 0, 0.05)
    assert not result.success and result.code == "NAV_WORKSPACE_LIMIT"
    np.testing.assert_array_equal(controller.world.data.qpos, before)
