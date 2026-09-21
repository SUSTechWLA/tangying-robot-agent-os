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
    _swept_model_collision,
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


def test_frame_age_is_settled_at_acquisition_and_the_base_capture_must_match():
    """Two different questions, and only one of them belongs to this predicate.

    ``check_navigation`` answers "does the measured geometry in this frame permit the
    sweep". Frame *age* is a fact about the camera, and it is settled in
    ``capture_with_state`` - the acquisition point - where a stale frame is refused.
    Re-asking here measured something else entirely: how long the caller took to get
    here. A bounded pulse is a wait (0.50 m at 0.05 m/s is 10 s), so an age check in
    this predicate refused every step longer than 0.10 m, and a long survey ended in
    the middle of a room with nothing wrong with it.

    So the assertions below are: an old frame is *not* this predicate's business, and
    a base pose stamped differently from its frame still is.
    """

    stale = replace(frame(), captured_at_unix_ms=int(time.time() * 1000) - 3000)
    assert check(stale).code != "NAV_OBSERVATION_INVALID", (
        "frame age belongs to acquisition, not to this geometric predicate")

    # Acquisition is where it is enforced, and there it is enforced hard.
    from tangying_robot_gateway.rgbd import DEFAULT_MAX_AGE_MS, validate_frame

    with pytest.raises(ValueError):
        validate_frame(stale, now_ms=int(time.time() * 1000), max_age_ms=DEFAULT_MAX_AGE_MS)
    validate_frame(stale, now_ms=int(time.time() * 1000), max_age_ms=None)

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


def _yaw(pose):
    return 2*np.arctan2(pose[6], pose[3])


def _pose_with_yaw(pose, yaw):
    result = list(pose)
    result[3:] = [np.cos(yaw/2), 0, 0, np.sin(yaw/2)]
    return result


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


@pytest.mark.parametrize("yaw_delta", [-0.05, 0.05])
def test_same_position_yaw_uses_fresh_bounded_pulses_instead_of_teleporting(controller, monkeypatch, yaw_delta):
    captures = 0

    def capture():
        nonlocal captures
        captures += 1
        return _synthetic_clear_capture(controller)

    turns = []
    publish = controller.world._publish_sensor_snapshot

    def record_publish():
        publish()
        turns.append(_yaw(controller.world.robot_state()["base_pose"]))

    monkeypatch.setattr(controller, "capture", capture)
    monkeypatch.setattr(controller.world, "_publish_sensor_snapshot", record_publish)
    monkeypatch.setattr("tangying_sim.rgbd_navigation.time.sleep", lambda _: None)
    before = controller.world.robot_state()["base_pose"]
    goal = _pose_with_yaw(before, _yaw(before)+yaw_delta)
    outcome = controller.navigate(goal)
    after = controller.world.robot_state()["base_pose"]

    assert outcome.success and outcome.code == "NAV_REACHED"
    np.testing.assert_allclose(after[:3], before[:3], atol=1e-7)
    assert abs(_yaw(after)-_yaw(goal)) <= controller.limits.yaw_tolerance_rad+1e-8
    motion = np.diff([_yaw(before), *turns])
    assert len(motion) >= 4
    assert np.max(np.abs(motion)) <= controller.MAX_ANGULAR_SPEED_RAD_S*controller.MAX_PULSE_S+1e-8
    assert np.all(np.sign(motion) == np.sign(yaw_delta))
    assert captures >= len(motion)+1


def test_cancel_and_stop_during_turn_hold_the_current_pose(controller, monkeypatch):
    monkeypatch.setattr(controller, "capture", lambda: _synthetic_clear_capture(controller))
    before = controller.world.data.qpos.copy()
    goal = _pose_with_yaw(controller.world.robot_state()["base_pose"], _yaw(controller.world.robot_state()["base_pose"])+0.05)

    cancelled = threading.Event()
    monkeypatch.setattr("tangying_sim.rgbd_navigation.time.sleep", lambda _: cancelled.set())
    result = controller.navigate(goal, cancelled)
    assert not result.success and result.code == "CANCELLED"
    np.testing.assert_array_equal(controller.world.data.qpos, before)

    cancelled.clear()
    monkeypatch.setattr("tangying_sim.rgbd_navigation.time.sleep", lambda _: controller.stop())
    result = controller.navigate(goal)
    assert not result.success and result.code == "CANCELLED"
    np.testing.assert_array_equal(controller.world.data.qpos, before)


def test_manual_turn_accepts_half_radian_but_rejects_a_larger_request(controller, monkeypatch):
    monkeypatch.setattr(controller, "capture", lambda: _synthetic_clear_capture(controller))
    monkeypatch.setattr(controller, "_model_motion_collides", lambda *args: False)
    monkeypatch.setattr("tangying_sim.rgbd_navigation.time.sleep", lambda _: None)
    before = controller.world.robot_state()["base_pose"]
    rejected = controller.navigate(_pose_with_yaw(before, _yaw(before)+0.501))
    assert not rejected.success and rejected.code == "NAV_ROTATION_LIMIT"
    np.testing.assert_allclose(controller.world.robot_state()["base_pose"], before, atol=1e-8)

    accepted = controller.navigate(_pose_with_yaw(before, _yaw(before)+0.5))
    assert accepted.success and accepted.code == "NAV_REACHED"
    assert abs(_yaw(controller.world.robot_state()["base_pose"])-_yaw(before)-0.5) <= controller.limits.yaw_tolerance_rad+1e-8


def test_driver_model_sweep_checks_translation_and_the_entire_turn_without_mutating_live_data():
    model = mujoco.MjModel.from_xml_string("""
        <mujoco>
          <option gravity="0 0 0"/>
          <worldbody>
            <geom name="floor" type="plane" size="2 2 .1"/>
            <body name="obstacle" pos="-.01 .08 .1">
              <geom name="wall" type="box" size=".02 .02 .09"/>
            </body>
            <body name="chassis" pos="0 0 .1">
              <joint name="x" type="slide" axis="1 0 0"/>
              <joint name="y" type="slide" axis="0 1 0"/>
              <joint name="yaw" type="hinge" axis="0 0 1"/>
              <geom name="robot" type="box" pos=".15 0 0" size=".2 .05 .09"/>
            </body>
          </worldbody>
        </mujoco>
    """)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    before = data.qpos.copy()
    addresses = [model.joint(name).qposadr[0] for name in ("x", "y", "yaw")]
    robot_bodies = {model.body("chassis").id}

    assert _swept_model_collision(model, data, robot_bodies, addresses, [0, 0.04, 0], 40)
    assert _swept_model_collision(model, data, robot_bodies, addresses, [0, 0, 0.5], 200)
    # The envelope participates in the verdict, not only the hard contacts: the base
    # starts 0.06 m from the obstacle, so a 0.40 m envelope is violated and a move
    # that closes the gap further is refused...
    assert _swept_model_collision(
        model, data, robot_bodies, addresses, [0, 0.005, 0], 20,
        chassis_body_id=model.body("chassis").id,
        clearance_radius_m=0.40, clearance_height_m=0.40,
    )
    # ...while the same pose under an envelope it satisfies is not a violation at all,
    # which is how the check is shown to depend on the radius rather than on the
    # contact solver.
    assert not _swept_model_collision(
        model, data, robot_bodies, addresses, [0, 0.005, 0], 20,
        chassis_body_id=model.body("chassis").id,
        clearance_radius_m=0.02, clearance_height_m=0.40,
    )
    np.testing.assert_array_equal(data.qpos, before)


def test_driver_clearance_hook_only_certifies_the_fresh_current_pose(controller):
    current = controller.world.robot_state()["base_pose"]
    # The proven band is exactly the radius the commissioned guard cleared at, so
    # the query radius follows the controller rather than a hard-coded number:
    # what matters here is the tolerance and the freshness rules, not the value.
    radius = controller.clearance_radius_m
    assert not controller.verified_travel_clearance(current[:2], radius=radius)
    assert controller.clear_at_pose(current, radius=radius)
    assert controller.verified_travel_clearance(current[:2], radius=radius)
    assert not controller.verified_travel_clearance([current[0]+0.019, current[1]], radius=radius)
    assert controller.verified_travel_clearance([current[0]+0.019, current[1]], radius=radius-0.02)
    assert not controller.verified_travel_clearance([current[0]+0.021, current[1]], radius=radius)
    assert not controller.verified_travel_clearance(["unknown", current[1]], radius=radius)
    unvisited = list(current)
    unvisited[1] += 0.05
    assert not controller.clear_at_pose(unvisited, radius=radius)
    assert not controller.clear_at_pose(current, radius=radius+0.01)
    controller.world.reset()
    assert not controller.verified_travel_clearance(current[:2], radius=radius)


def test_clear_at_pose_records_only_the_radius_it_actually_checked(controller):
    current = controller.world.robot_state()["base_pose"]
    assert controller.clear_at_pose(current, radius=0.25)
    assert controller.verified_travel_clearance(current[:2], radius=0.25)
    assert not controller.verified_travel_clearance(current[:2], radius=0.251)


def test_the_clearance_guard_is_measured_from_the_cad_not_a_round_number(controller):
    """The driver's outer guard is its own CAD envelope plus a stated margin.

    It was a flat 0.40 m, which is 95 mm more than the CAD reaches in the band the
    guard checks - and that uncommissioned extra refused the commissioned kitchen
    waypoint as NAV_MODEL_COLLISION while the map-based router (0.32 m) called the
    same place free. A refusal an operator cannot argue from numbers is not a
    safety property, it is folklore.
    """
    from tangying_sim.rgbd_navigation import _commissioned_clearance_radius

    model, data = controller.world.model, controller.world.data
    mujoco.mj_forward(model, data)
    envelope = _commissioned_clearance_radius(model, data, controller._robot_body_ids,
                                              controller._chassis_body_id, -0.06,
                                              controller.CLEARANCE_HEIGHT_M, 0.0)
    assert 0.2 < envelope < 0.40, envelope
    assert controller.clearance_radius_m == pytest.approx(
        envelope + controller.CLEARANCE_MARGIN_M, rel=0, abs=1e-6)
    # The guard is the outer authority: it may not be looser than the planner's
    # 0.32 m footprint, or the router would propose motion the driver refuses.
    assert controller.clearance_radius_m >= 0.32
    # And it may not be tighter than the body it guards.
    assert controller.clearance_radius_m > envelope


def test_a_pose_inside_the_guard_envelope_can_still_drive_out_of_it(controller):
    """The guard must not turn a tight spot into a permanent trap.

    The sweep starts at the robot's current pose, so testing sample zero makes the
    guard answer "you are too close to the wall" to every candidate pulse - including
    the one that drives away from it. That happened for real in the furnished home: a
    corridor-to-bathroom drive (a straight line that crosses a wall, so the base crept
    into it) left the chassis 0.373 m from the wall against a 0.375 m envelope, and
    from then on *every* command - including the return leg and everything after it -
    came back NAV_MODEL_COLLISION. A robot that cannot be recovered by driving it is
    not a safer robot.

    The rule under test is asymmetric on purpose: from a legal pose any collision in
    the sweep refuses the move; from an illegal one, a move to a legal end pose is
    allowed. Both halves are asserted, because a fix that simply stopped refusing
    would pass the first half alone.
    """

    model, data = controller.world.model, controller.world.data
    address = int(model.joint("slide_joint_x").qposadr[0])
    radius = controller.clearance_radius_m

    # Find where the guard's envelope meets the nearest wall along +x, then place the
    # base 2 mm *inside* it: the exact state the live robot was left in.
    mujoco.mj_forward(model, data)
    wall = None
    from tangying_sim.rgbd_navigation import _clearance_envelope_collision

    start = float(data.qpos[address])
    probe = copy.copy(data)
    offset = 0.0
    while offset < 3.0:
        probe.qpos[address] = start + offset
        mujoco.mj_forward(model, probe)
        if _clearance_envelope_collision(model, probe, controller._robot_body_ids,
                                         controller._chassis_body_id, radius, -0.06,
                                         controller.CLEARANCE_HEIGHT_M):
            wall = offset
            break
        offset += 0.0005
    assert wall is not None, "the fixture has no wall within 3 m; pick another axis"
    # Place the base at the first *illegal* offset, so the precondition the test
    # claims to set up is asserted rather than assumed.
    data.qpos[address] = start + wall
    mujoco.mj_forward(model, data)
    assert _clearance_envelope_collision(model, data, controller._robot_body_ids,
                                         controller._chassis_body_id, radius, -0.06,
                                         controller.CLEARANCE_HEIGHT_M), (
        "the base is not actually inside the envelope; the trap was not reproduced")

    robot = controller._robot_body_ids
    # Deeper into the wall: refused.
    assert _swept_model_collision(
        model, data, robot, [address], [0.0025], 1,
        chassis_body_id=controller._chassis_body_id, clearance_radius_m=radius,
        clearance_bottom_m=-0.06, clearance_height_m=controller.CLEARANCE_HEIGHT_M) is True
    # Straight back out: allowed, and this is the half that was broken.
    assert _swept_model_collision(
        model, data, robot, [address], [-0.0025], 1,
        chassis_body_id=controller._chassis_body_id, clearance_radius_m=radius,
        clearance_bottom_m=-0.06, clearance_height_m=controller.CLEARANCE_HEIGHT_M) is False
    # The property that actually matters: the robot is not frozen. At least one
    # bounded pulse gets it out, and refusing all of them is what "trapped" means.
    def allowed(axis, step):
        try:
            return not _swept_model_collision(
                model, data, robot, [axis], [step], 1,
                chassis_body_id=controller._chassis_body_id, clearance_radius_m=radius,
                clearance_bottom_m=-0.06, clearance_height_m=controller.CLEARANCE_HEIGHT_M)
        except (ValueError, IndexError):
            return False

    escapes = [name for name, step in
               ((n, s) for n in ("slide_joint_x", "slide_joint_y")
                for s in (-0.0025, 0.0025))
               if allowed(int(model.joint(name).qposadr[0]), step)]
    assert escapes, (
        "no bounded pulse is permitted from inside the envelope: the base is trapped, "
        "which is the state the live robot was left in and could not be driven out of")
    # Holding still is not a safety violation either; refusing it would refuse to let
    # the robot wait where it is.
    assert _swept_model_collision(
        model, data, robot, [address], [0.0], 1,
        chassis_body_id=controller._chassis_body_id, clearance_radius_m=radius,
        clearance_bottom_m=-0.06, clearance_height_m=controller.CLEARANCE_HEIGHT_M) is False


def test_a_legal_pose_still_refuses_any_sweep_that_would_hit(controller):
    """The other half of the rule: away from walls, the guard is unchanged."""

    model, data = controller.world.model, controller.world.data
    address = int(model.joint("slide_joint_x").qposadr[0])
    mujoco.mj_forward(model, data)
    assert _swept_model_collision(
        model, data, controller._robot_body_ids, [address], [-0.0025], 1,
        chassis_body_id=controller._chassis_body_id,
        clearance_radius_m=controller.clearance_radius_m,
        clearance_bottom_m=-0.06, clearance_height_m=controller.CLEARANCE_HEIGHT_M) is False


def test_a_collision_refusal_names_the_obstacle_and_the_margin(controller):
    """A refused pulse must say what is close and by how much.

    The code alone is not actionable: an operator cannot tell whether the survey
    is wrong, the route is bad, or the commissioned pose is simply too close to
    furniture. That distinction is exactly what a commissioning decision needs,
    and it was missing when a household task failed here.
    """
    from tangying_sim.rgbd_navigation import _nearest_envelope_obstacle

    model, data = controller.world.model, controller.world.data
    mujoco.mj_forward(model, data)
    chassis = model.body("chassis").id
    obstacle = _nearest_envelope_obstacle(model, data, controller._robot_body_ids, chassis,
                                          controller.clearance_radius_m,
                                          controller.limits.body_bottom_offset_m,
                                          controller.CLEARANCE_HEIGHT_M)
    if obstacle is not None:
        name, distance = obstacle
        assert isinstance(name, str) and name
        assert 0.0 <= distance < 1.0, obstacle
    # The refusal path itself carries the same detail, not only a bare code.
    pose = controller.world.robot_state()["base_pose"]
    refusal = controller.navigate(_pose_with_yaw(pose, _yaw(pose)+0.05))
    if refusal.code == "NAV_MODEL_COLLISION":
        assert "envelope" in refusal.message and "m from the chassis centre" in refusal.message, refusal.message


def test_unobserved_or_model_blocked_velocity_never_moves(controller, monkeypatch):
    before = controller.world.data.qpos.copy()
    before_pose = controller.world.robot_state()["base_pose"]
    monkeypatch.setattr(controller, "capture", lambda: (_ for _ in ()).throw(ValueError("camera unavailable")))
    result = controller.apply_velocity(0, 0, 0.2, 0.05)
    assert not result.success and result.code == "NAV_OBSERVATION_INVALID"
    np.testing.assert_array_equal(controller.world.data.qpos, before)

    monkeypatch.setattr(controller, "capture", lambda: _synthetic_clear_capture(controller))
    monkeypatch.setattr(controller, "_model_motion_collides", lambda *args: True)
    pose = controller.world.robot_state()["base_pose"]
    turn = controller.navigate(_pose_with_yaw(pose, _yaw(pose)+0.05))
    assert not turn.success and turn.code == "NAV_MODEL_COLLISION"
    np.testing.assert_array_equal(controller.world.data.qpos, before)
    result = controller.apply_velocity(0.04, 0, 0, 0.05)
    assert not result.success and result.code == "NAV_MODEL_COLLISION"
    np.testing.assert_array_equal(controller.world.data.qpos, before)
    assert not controller.verified_travel_clearance(before_pose[:2], radius=controller.clearance_radius_m)


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
    midpoint = ((np.asarray(before[:2])+np.asarray(after[:2]))/2).tolist()
    assert controller.verified_travel_clearance(midpoint, radius=controller.clearance_radius_m)
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

    now[0] = 200.0
    monkeypatch.setattr("tangying_sim.rgbd_navigation.time.sleep", lambda _: None)

    def slow_collision_check(*_args):
        now[0] += 0.3
        return False

    monkeypatch.setattr(controller, "_model_motion_collides", slow_collision_check)
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


@pytest.mark.parametrize("motion", ["translate", "turn", "velocity"])
def test_command_budget_expiring_during_pulse_prevents_actual_pose_update(controller,monkeypatch,motion):
    from tangying_robot_proto.robot.v1 import robot_pb2
    from tangying_sim.server import _CommandCancellation
    now = [100.]
    monkeypatch.setattr(time,"monotonic",lambda:now[0])
    cancel = _CommandCancellation(robot_pb2.SkillCommand(lease_ms=10,
        deadline_unix_ms=int(time.time()*1000)+1000))
    monkeypatch.setattr(time,"sleep",lambda _:now.__setitem__(0,now[0]+.02))
    monkeypatch.setattr(controller,"capture",lambda:_synthetic_clear_capture(controller))
    before = controller.world.data.qpos.copy()
    pose = controller.world.robot_state()["base_pose"]
    if motion == "velocity":
        result = controller.apply_velocity(.04,0,0,.05,cancel_event=cancel)
    else:
        goal = [0.,.025,.035,*pose[3:]] if motion == "translate" else _pose_with_yaw(pose,_yaw(pose)+.05)
        result = controller.navigate(goal,cancel)
    assert not result.success and result.code == "CANCELLED"
    np.testing.assert_array_equal(controller.world.data.qpos,before)
