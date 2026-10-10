"""Offline geometry and deterministic sensor fixtures, never physical success.

The recorded focus003 failure left the mug against two tray rims.  These tests
keep the payload envelope above the rim before allowing lateral retraction;
they do not alter actuator effort, IK residuals, or arrival tolerances.
"""

import copy
import json
import math
import threading
from types import SimpleNamespace as NS

import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from tangying_robot_gateway import gazebo_manipulation as module
from tangying_robot_gateway.runtime import Command, Result

DIMENSIONS = (.09, .09, .12)
RIM_Z = .83


def test_extent_accounts_for_rotated_payload_corners():
    assert module.payload_vertical_extent(np.eye(3), DIMENSIONS) == pytest.approx(.06)
    rotation = Rotation.from_euler("xyz", [.12, -.08, .7]).as_matrix()
    corners = np.array([[x, y, z] for x in (-.045, .045)
                        for y in (-.045, .045) for z in (-.06, .06)])
    expected = max(abs((rotation @ corner)[2]) for corner in corners)
    assert module.payload_vertical_extent(rotation, DIMENSIONS) == pytest.approx(expected)
    assert expected > .06


@pytest.mark.parametrize("dimensions", [(0, .09, .12), (-.09, .09, .12),
                                         (.09, .09, math.nan), (.09, .12)])
def test_invalid_payload_geometry_cannot_authorize_a_path(dimensions):
    with pytest.raises(ValueError):
        module.payload_vertical_extent(np.eye(3), dimensions)


def test_loaded_path_clears_whole_payload_before_horizontal_retraction():
    # The original mug begins inside a 0.83 m rim, not on an open tabletop.
    tip = np.array([2.424, 3.358, .858])
    payload = np.array([2.452, 3.334, .791])
    base = np.eye(4)
    base[:3, :3] = Rotation.from_euler("xyz", [.03, -.04, 1.45]).as_matrix()
    before = tip.copy(), payload.copy(), base.copy()
    vertical, horizontal = module.loaded_lift_waypoints(
        tip, payload, base, rim_z=RIM_Z, dimensions=DIMENSIONS)
    bound = .5*DIMENSIONS[2]*math.cos(.15) + .5*math.hypot(*DIMENSIONS[:2])*math.sin(.15)
    rise = max(.13, RIM_Z+.025+bound+.012+.020-payload[2])
    assert vertical == pytest.approx(tip+[0., 0., rise])
    assert payload[2]+vertical[2]-tip[2]-bound >= RIM_Z+.025+.012+.020-1e-12
    projected = base[:3, :3] @ [-.04, 0., 0.]
    assert horizontal[:2] == pytest.approx(vertical[:2]+projected[:2])
    assert horizontal[2] == vertical[2]
    for value, original in zip((tip, payload, base), before, strict=True):
        np.testing.assert_array_equal(value, original)


def test_lift_retains_minimum_rise_but_refuses_excessive_clearance():
    tip = np.array([.4, -.1, 1.05])
    vertical, _ = module.loaded_lift_waypoints(
        tip, [.4, -.1, .98], np.eye(4), rim_z=RIM_Z, dimensions=DIMENSIONS)
    assert vertical[2]-tip[2] == pytest.approx(.13)
    with pytest.raises(ValueError):
        module.loaded_lift_waypoints(
            tip, [.4, -.1, .60], np.eye(4), rim_z=RIM_Z, dimensions=DIMENSIONS)


def test_requested_tracking_reserve_cannot_expand_maximum_lift():
    bound = .5*DIMENSIONS[2]*math.cos(.15) + .5*math.hypot(*DIMENSIONS[:2])*math.sin(.15)
    # With only the existing 12 mm tracking allowance this case requested
    # 19.9 cm. Adding 20 mm transport reserve must reject, not raise the cap.
    payload_z = RIM_Z+.025+bound+.012-.199
    with pytest.raises(ValueError, match="PAYLOAD_CLEARANCE_UNREACHABLE"):
        module.loaded_lift_waypoints(
            [.4, -.1, payload_z+.067], [.4, -.1, payload_z], np.eye(4),
            rim_z=RIM_Z, dimensions=DIMENSIONS)


@pytest.mark.parametrize("tracking_deficit", [.006, .012])
def test_requested_reserve_keeps_measured_clearance_for_bounded_deficit(tracking_deficit):
    # Recorded focus005 source and rim geometry, evaluated offline. This
    # checks the requested path algebra, not an actuator performance promise.
    payload = np.array([2.4497256363852506, 3.331745260261742, .7909643546965319])
    tip = payload+[0., 0., .067]
    rim = .8309715024365885
    vertical, _ = module.loaded_lift_waypoints(
        tip, payload, np.eye(4), rim_z=rim, dimensions=DIMENSIONS)
    bound = .5*DIMENSIONS[2]*math.cos(.15) + .5*math.hypot(*DIMENSIONS[:2])*math.sin(.15)
    measured_bottom_bound = payload[2]+vertical[2]-tip[2]-tracking_deficit-bound
    assert measured_bottom_bound >= rim+.045-1e-12
    assert .13 <= vertical[2]-tip[2] <= .20


@pytest.mark.parametrize("measured_z,expected_z", [(.9934661707560539, 1.0008047894264793),
                                                (1.010, 1.010)])
def test_retract_preserves_requested_height_without_pressing_down_higher_feedback(measured_z, expected_z):
    # Focus006 lowered the next requested height by 7.338619 mm by using
    # measured Z alone. Keep measured XY, but never accumulate that Z error.
    lifts = np.array([[2.4194450120473254, 3.353711282719011, 1.0008047894264793],
                      [2.41455879819164, 3.314010842996553, 1.0008047894264793]])
    measured = np.array([2.419818, 3.354127, measured_z])
    originals = measured.copy(), lifts.copy()
    target = module.loaded_retract_target(measured, lifts)
    assert target[:2] == pytest.approx(measured[:2]+lifts[1, :2]-lifts[0, :2])
    assert target[2] == pytest.approx(expected_z)
    np.testing.assert_array_equal(measured, originals[0])
    np.testing.assert_array_equal(lifts, originals[1])


@pytest.mark.parametrize("measured,lifts", [
    ([0., 1.], [[0., 0., 1.], [-.04, 0., 1.]]),
    ([0., 0., math.nan], [[0., 0., 1.], [-.04, 0., 1.]]),
    ([0., 0., 1.], [[0., 0., 1.]]),
    ([0., 0., 1.], [[0., 0., math.inf], [-.04, 0., math.inf]]),
    ([0., 0., 1.], [[0., 0., 1.], [-.04, 0., .99]]),
])
def test_invalid_retract_basis_is_rejected(measured, lifts):
    with pytest.raises(ValueError, match="PAYLOAD_GEOMETRY_INVALID"):
        module.loaded_retract_target(measured, lifts)


def clearance_controller(monkeypatch, *, base_z=0.):
    """Use the real guard and verifier with a fake, explicitly offline clock."""
    clock = [100.]
    wall_origin = 1_791_595_800.
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: int(clock[0]*1e9))
    monkeypatch.setattr(module.time, "time", lambda: wall_origin+clock[0])
    monkeypatch.setattr(module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    base = np.eye(4)
    base[2, 3] = base_z
    runtime = NS(base_pose=base, robot_id="offline-robot", calibration_revision="offline-cal")
    node = NS(runtime=runtime)
    backend = NS(node=node, home=True, cancel_event=threading.Event())
    control = module.GazeboManipulation(backend)
    command = Command(schema_version="robot.v1", command_id="owned-pick", task_id="owned-task",
                      capability="manipulation.pick", target_ref="ceramic-mug", robot_id=runtime.robot_id,
                      catalog_revision="offline-catalog", deadline_unix_ms=int((wall_origin+clock[0]+60)*1000))
    source = {"robotId": runtime.robot_id, "taskId": command.task_id,
              "catalogRevision": command.catalog_revision, "calibrationRevision": runtime.calibration_revision,
              "toolRevision": control.tool_revision, "base": base.tolist()}
    plan = {"task": command.task_id, "object": "ceramic-mug", "side": "left", "base": base.copy(),
            "position": np.array([2.452, 3.334, .791]), "created": clock[0],
            "sourceJSON": json.dumps(source), "sourceToken": "offline-source"}
    control.plan = plan
    control.acquisition = {"object": "ceramic-mug", "task": command.task_id, "z": .791}
    control._evidence.value = {"phases": []}

    def forbidden(*_args, **_kwargs):
        pytest.fail("A clearance verifier must not publish horizontal or other motion")

    monkeypatch.setattr(module, "execute_chunk", forbidden)
    node.send_joint_targets = forbidden
    control.move_tip = forbidden
    control.suction = forbidden
    return control, command, plan, clock


def physics_sample(sequence, stamp_ms, *, z=.93, tilt=0., attached=True,
                   held="ceramic_mug", side="left", robot_z=0.):
    quaternion = Rotation.from_euler("x", tilt).as_quat()
    pose = [2.452, 3.334, z, quaternion[3], *quaternion[:3]]
    return {"schemaVersion": "gazebo.suction.v1", "mode": "sim_suction", "sequence": sequence,
            "simTimeNs": int(stamp_ms*1e6), "attached": attached, "held": held, "side": side,
            "code": "OK", "robotPose": [0., 0., robot_z, 1., 0., 0., 0.],
            "objects": {"ceramic_mug": pose},
            "tips": {"left": [2.424, 3.358, z+.067, 1., 0., 0., 0.]}}


def feed(control, states, *, after_read=None):
    reads = []

    def state():
        index = len(reads)
        sample = copy.deepcopy(states[min(index, len(states)-1)])
        reads.append(sample)
        if after_read is not None:
            after_read(len(reads))
        return sample

    control.state = state
    return reads


def verify(control, command, plan):
    return control._verify_payload_clearance(command, plan, RIM_Z, DIMENSIONS)


def test_clearance_requires_three_independent_samples_over_200ms(monkeypatch):
    c, command, plan, _ = clearance_controller(monkeypatch)
    reads = feed(c, [physics_sample(11, 1000), physics_sample(12, 1100), physics_sample(13, 1200)])
    result = verify(c, command, plan)
    assert result.success
    assert len({state["sequence"] for state in reads}) >= 3
    assert reads[-1]["simTimeNs"]-reads[0]["simTimeNs"] >= 200_000_000


def test_transport_start_requires_45mm_even_when_original_25mm_gate_passes(monkeypatch):
    c, command, plan, _ = clearance_controller(monkeypatch)
    states = [physics_sample(n, n*100, z=.93) for n in (11, 12, 13)]
    feed(c, states)
    assert verify(c, command, plan).success  # Actual bottom .87: 40 mm above rim.
    feed(c, states)
    result = c._verify_payload_clearance(command, plan, RIM_Z, DIMENSIONS, required_clearance=.045)
    assert not result.success
    phase = c._evidence.value["phases"][-1]
    assert phase["phase"] == "payload_clearance_stopped"
    assert phase["requiredBottomZ"] == pytest.approx(.875)


def test_transport_start_accepts_only_fresh_stable_45mm_clearance(monkeypatch):
    c, command, plan, _ = clearance_controller(monkeypatch)
    reads = feed(c, [physics_sample(n, n*100, z=.936) for n in (11, 12, 13)])
    result = c._verify_payload_clearance(command, plan, RIM_Z, DIMENSIONS, required_clearance=.045)
    assert result.success
    assert len(reads) == 3 and reads[-1]["simTimeNs"]-reads[0]["simTimeNs"] == 200_000_000


def test_transport_during_motion_keeps_original_25mm_guard(monkeypatch):
    c, command, plan, _ = clearance_controller(monkeypatch)
    feed(c, [physics_sample(11, 1000, z=.93), physics_sample(12, 1100, z=.914)])
    cancel = c._pick_cancel(command, plan, clearance=(RIM_Z, DIMENSIONS))
    assert not cancel.is_set()  # 40 mm is below start reserve, above hard limit.
    assert cancel.is_set()  # 24 mm still stops, despite the planned reserve.
    phase = c._evidence.value["phases"][-1]
    assert phase["rejectReason"] == "PAYLOAD_CLEARANCE_NOT_VERIFIED"
    assert phase["requiredBottomZ"] == pytest.approx(.855)


@pytest.mark.parametrize("required", [.024, 0., math.nan, math.inf])
def test_required_transport_clearance_cannot_disable_original_hard_gate(monkeypatch, required):
    c, command, plan, _ = clearance_controller(monkeypatch)
    reads = feed(c, [physics_sample(n, n*100, z=.936) for n in (11, 12, 13)])
    result = c._verify_payload_clearance(command, plan, RIM_Z, DIMENSIONS, required_clearance=required)
    assert not result.success
    assert not reads
    reason, _ = c._payload_clearance_error(plan, physics_sample(11, 1000), RIM_Z,
                                          DIMENSIONS, required_clearance=required)
    assert reason


@pytest.mark.parametrize("states", [
    [physics_sample(11, 1000)],
    [physics_sample(11, 1000), physics_sample(12, 1100)],
    [physics_sample(11, 1000), physics_sample(12, 1000), physics_sample(13, 1000)],
    [physics_sample(11, 1000), physics_sample(12, 1100), physics_sample(11, 1300)],
    [physics_sample(11, 1000), physics_sample(12, 1300), physics_sample(13, 1100), physics_sample(14, 1400)],
])
def test_repeated_short_or_reordered_physics_cannot_prove_clearance(monkeypatch, states):
    c, command, plan, clock = clearance_controller(monkeypatch)
    feed(c, states)
    assert not verify(c, command, plan).success
    assert clock[0] < 104.


@pytest.mark.parametrize("change", [{"z": .90}, {"z": math.nan}, {"z": math.inf},
                                   {"tilt": .151}, {"attached": False},
                                   {"held": "ceramic_vase"}, {"side": "right"}])
def test_unclear_or_unowned_payload_never_authorizes_horizontal_motion(monkeypatch, change):
    c, command, plan, _ = clearance_controller(monkeypatch)
    feed(c, [physics_sample(n, n*100, **change) for n in (11, 12, 13)])
    assert not verify(c, command, plan).success


def test_bad_sample_resets_the_three_sample_clearance_proof(monkeypatch):
    c, command, plan, _ = clearance_controller(monkeypatch)
    states = [physics_sample(11, 1000), physics_sample(12, 1100), physics_sample(13, 1200, z=.90),
              physics_sample(14, 1300), physics_sample(15, 1400)]
    feed(c, states)
    assert not verify(c, command, plan).success


def test_recorded_focus005_shortfall_still_fails_despite_requested_reserve(monkeypatch):
    c, command, plan, _ = clearance_controller(monkeypatch)
    # Actual retained final-window pose: bottom .85565748944037 is below
    # required .8559715024365885. Repeated fresh samples remain unsafe even
    # after the path planner begins requesting its additional 12 mm.
    pose = [2.4545084546167533, 3.3289215223898396, .9170217211439554,
            .9484853011326237, -.01425908792725876, .00510045625765615, -.316458997806678]
    states = [physics_sample(n, n*100) for n in (11, 12, 13)]
    for state in states:
        state["objects"]["ceramic_mug"] = pose.copy()
    feed(c, states)
    result = c._verify_payload_clearance(command, plan, .8309715024365885, DIMENSIONS)
    assert not result.success
    assert result.code == "PAYLOAD_CLEARANCE_NOT_VERIFIED"
    assert not any(p["phase"] == "payload_clearance_verified" for p in c._evidence.value["phases"])


def test_sample_returned_after_clearance_wait_deadline_cannot_authorize_horizontal(monkeypatch):
    c, command, plan, clock = clearance_controller(monkeypatch)

    def late_read(count):
        if count == 3:
            clock[0] += 3.001

    feed(c, [physics_sample(n, n*100) for n in (11, 12, 13)], after_read=late_read)
    assert not verify(c, command, plan).success


@pytest.mark.parametrize("change", ["expiry", "deadline", "cancel", "calibration", "base"])
def test_original_guard_failure_during_samples_stops_without_waiting_for_new_budget(monkeypatch, change):
    c, command, plan, clock = clearance_controller(monkeypatch)

    def invalidate(count):
        if count != 2:
            return
        if change == "expiry":
            plan["created"] = clock[0]-30.001
        elif change == "deadline":
            monkeypatch.setattr(module.time, "time", lambda: command.deadline_unix_ms/1000)
        elif change == "cancel":
            c.backend.cancel_event.set()
        elif change == "calibration":
            c.node.runtime.calibration_revision = "changed"
        elif change == "base":
            c.node.runtime.base_pose = c.node.runtime.base_pose.copy()
            c.node.runtime.base_pose[0, 3] += .031

    reads = feed(c, [physics_sample(n, n*100) for n in (11, 12, 13)], after_read=invalidate)
    assert not verify(c, command, plan).success
    assert len(reads) <= 2
    assert clock[0] < 100.2


@pytest.mark.parametrize("base_z,robot_z,cup_z,expected", [(.2, 0., .73, True), (0., .2, .93, False)])
def test_clearance_compares_payload_and_rim_in_same_odom_frame(monkeypatch, base_z, robot_z, cup_z, expected):
    c, command, plan, _ = clearance_controller(monkeypatch, base_z=base_z)
    feed(c, [physics_sample(n, n*100, z=cup_z, robot_z=robot_z) for n in (11, 12, 13)])
    assert verify(c, command, plan).success is expected


@pytest.mark.parametrize("vertical_reaches_target,tracking_deficit,expected_retract", [
    (False, 0., False), (True, 0., True), (True, .007338619, True), (True, .025, False),
])
def test_actual_pick_only_retracts_after_independent_payload_clearance(
        monkeypatch, vertical_reaches_target, tracking_deficit, expected_retract):
    c, command, plan, _ = clearance_controller(monkeypatch)
    source = json.loads(plan["sourceJSON"])
    source.update(category="cup", recognition="rgbd_metric_shape", sensorStampNs="1000000000",
                  position=plan["position"].tolist(), destinationPosition=[2.46, 3.335, .73])
    plan["sourceJSON"] = json.dumps(source)
    plan["upright"] = np.array([0., 0., 1.])
    entity = NS(category="cup", confidence=.9, pose_xyz_quat=[*plan["position"], 1., 0., 0., 0.],
                attributes={"recognition": "rgbd_metric_shape"})
    frame = NS(observation_id="offline-input", wall_time_unix_ms=int(module.time.time()*1000),
               robot_state={"base_pose": [0., 0., 0., 1., 0., 0., 0.],
                            "perception": {"calibration_revision": "offline-cal", "sensor_stamp_ns": "1000000000"}},
               reconstruction={"robotId": "offline-robot"})
    c.capture = lambda: (frame, {"ceramic-mug": entity})
    c._observe_grasp_views = lambda *_args: (plan["position"].copy(), None)
    held, counter = [False], [10]
    current_tip = plan["position"] + [0., 0., c.tool["contactHeightM"]]
    moves, lift_calls = [], []

    def state():
        counter[0] += 1
        result = physics_sample(counter[0], counter[0]*100, z=current_tip[2]-.067, attached=held[0])
        result["tips"]["left"][:3] = current_tip.tolist()
        return result

    def move_tip(target, **kwargs):
        assert not kwargs["cancel"].is_set()
        moves.append(np.asarray(target).copy())
        if len(moves) == 2 and not vertical_reaches_target:
            # A driver ACK cannot substitute for the payload physics proof.
            current_tip[2] += .03
        else:
            current_tip[:] = target
            if len(moves) == 2:
                current_tip[2] -= tracking_deficit
        return Result(True)

    def attach(*_args, **_kwargs):
        held[0] = True
        return Result(True)

    original_lift = module.loaded_lift_waypoints

    def lift(*args, **kwargs):
        waypoints = original_lift(*args, **kwargs)
        lift_calls.append(waypoints)
        return waypoints

    monkeypatch.setattr(module, "loaded_lift_waypoints", lift)
    c.state, c.move_tip, c.suction = state, move_tip, attach
    c.verify_grasp = lambda *_args: Result(True)
    result = c.execute(command)
    assert len(lift_calls) == 1
    assert result.success is expected_retract
    assert len(moves) == (3 if expected_retract else 2)
    assert moves[1] == pytest.approx(lift_calls[0][0])
    phases = json.loads(result.message)["phases"]
    if expected_retract:
        names = [phase["phase"] for phase in phases]
        assert names.index("payload_clearance_verified") < names.index("lift_retract")
        # A successful independent clearance window does not turn a low
        # measured pose into the next segment's ideal commanded height.
        assert moves[2][2] == moves[1][2]
    else:
        assert all(phase["phase"] != "lift_retract" for phase in phases)
        if tracking_deficit == .025:
            stopped = next(phase for phase in phases if phase["phase"] == "payload_clearance_stopped")
            assert RIM_Z+.025 < stopped["lastReading"]["bottomZ"] < RIM_Z+.045
            assert stopped["requiredBottomZ"] == pytest.approx(RIM_Z+.045)


def test_horizontal_joint_loop_stops_on_lost_payload_clearance(monkeypatch):
    from tangying_robot_gateway.gazebo_actuation import execute_chunk

    c, command, plan, clock = clearance_controller(monkeypatch)
    height = [.93]
    c.state = lambda: physics_sample(10, 1000, z=height[0])
    published, held = [], []
    c.node._command_lock = threading.Lock()
    c.node.motion_allowed = lambda: True
    c.node.joint_snapshot = lambda: ({"left_arm_shoulder_lift": 1.}, 0., int(clock[0]*1000))

    def publish(target):
        published.append(target)
        height[0] = .90

    c.node.send_joint_targets = publish
    c.node.hold_joints = lambda: held.append(True)
    result = execute_chunk(c.node, [{"left_arm_shoulder_lift.pos": 1.5}],
                           c._pick_cancel(command, plan, clearance=(RIM_Z, DIMENSIONS)),
                           clock=lambda: clock[0], sleep=module.time.sleep)
    assert result.code == "CANCELLED"
    assert len(published) == 1 and held == [True]
    assert c._evidence.value["phases"][-1]["rejectReason"] == "PAYLOAD_CLEARANCE_NOT_VERIFIED"


def test_motion_guard_diagnostic_uses_same_rejecting_tick_without_rereading(monkeypatch):
    c, command, plan, _ = clearance_controller(monkeypatch)
    reads = feed(c, [physics_sample(11, 1000, z=.90), physics_sample(12, 1100, z=.93)])
    cancel = c._pick_cancel(command, plan, clearance=(RIM_Z, DIMENSIONS))
    assert cancel.is_set()
    assert len(reads) == 1
    phase = c._evidence.value["phases"][-1]
    assert phase["phase"] == "motion_guard"
    assert phase["rejectReason"] == "PAYLOAD_CLEARANCE_NOT_VERIFIED"
    assert phase["sequence"] == "11" and phase["simTimeNs"] == "1000000000"
    assert phase["bottomZ"] == pytest.approx(.84)
    assert phase["requiredBottomZ"] == pytest.approx(.855)


def test_unavailable_guard_geometry_cannot_become_authorization_or_nonfinite_evidence(monkeypatch):
    c, command, plan, _ = clearance_controller(monkeypatch)
    feed(c, [physics_sample(11, 1000, z=math.nan)])
    cancel = c._pick_cancel(command, plan, clearance=(RIM_Z, DIMENSIONS))
    assert cancel.is_set()
    phase = c._evidence.value["phases"][-1]
    assert phase["rejectReason"] == "PAYLOAD_GEOMETRY_INVALID"
    assert phase["geometryUnavailable"] is True
    assert "bottomZ" not in phase
    json.dumps(phase, allow_nan=False)


@pytest.fixture(scope="module")
def focus006_loaded_retract():
    """Pure replay: actual source/fresh pose, nearest pinned post-plan joints.

    The contact and vertical endpoint below are computed IK, not measured
    physical poses or a claim that the failed task completed.
    """
    from tangying_robot_gateway.arm_kinematics import arm_links, chain_poses
    from tangying_robot_gateway.gazebo_tools import tool_commissioning, tool_pose

    base = np.array([[.12215534639213876, -.9925109930614454, 0., 2.050236839927874],
                     [.9925109930614454, .12215534639213876, 0., 2.997593918667861],
                     [0., 0., 1., .035], [0., 0., 0., 1.]])
    # gazebo-home_furnished/head-rgbd-279590900000000-1791599443253
    joints = {"left_arm_elbow_flex": 1.0000016703763324,
              "left_arm_gripper": -9.258374619800816e-06,
              "left_arm_shoulder_lift": 3.100003291725602,
              "left_arm_shoulder_pan": -1.9365085173715848e-06,
              "left_arm_wrist_flex": -5.499269928602298e-06,
              "left_arm_wrist_roll": .0011278309853571763}
    tool, _ = tool_commissioning()
    links = arm_links("left")
    upright = tool_pose(chain_poses(links, {link.motor: 0. for link in links},
                                   base=np.eye(4)), tool)[:3, :3].T @ [0., 0., 1.]
    view = np.array([2.5032796681749305, 3.27126661782188, .950964354696532])-[0., 0., .16]
    fresh = np.array([2.421061412069515, 3.3540028904676285, .790964358406478])
    _, approach = module.plan_empty_arm_approach(view, joints, base, upright=upright, side="left", tool=tool)
    goal = fresh+[0., 0., tool["contactHeightM"]]
    _, contact = module.plan_tip_chunk(goal, approach, base, upright=upright, side="left",
                                       tool=tool, fixed_gripper=tool["gripperOpenRad"])
    lifts = module.loaded_lift_waypoints(goal, fresh, base, rim_z=.8309715414641675, dimensions=DIMENSIONS)
    _, vertical = module.plan_tip_chunk(lifts[0], contact, base, upright=upright, side="left",
                                        tool=tool, fixed_gripper=tool["gripperOpenRad"])
    return lifts[1], vertical, base, upright, tool


def test_recorded_loaded_retract_checks_synchronized_intermediate_fk(focus006_loaded_retract):
    from tangying_robot_gateway.arm_kinematics import arm_links, chain_poses
    from tangying_robot_gateway.gazebo_actuation import joint_ramp_step, validate_chunk
    from tangying_robot_gateway.gazebo_tools import tool_pose

    target, joints, base, upright, tool = focus006_loaded_retract
    links = arm_links("left")
    start = tool_pose(chain_poses(links, joints, base=base), tool)
    chunk, end = module.plan_loaded_retract_chunk(target, joints, base, upright=upright,
                                                 side="left", tool=tool)
    commanded, poses = dict(joints), []
    for waypoint in validate_chunk(chunk):
        while max(abs(waypoint[n]-commanded[n]) for n in waypoint) > 1e-9:
            commanded = joint_ramp_step(commanded, waypoint, coordinated=True)
            poses.append(tool_pose(chain_poses(links, commanded, base=base), tool))
    assert poses
    assert min(p[2, 3] for p in poses) >= min(start[2, 3], target[2])-.008
    assert max(np.linalg.norm(p[:3, :3] @ upright-[0., 0., 1.]) for p in poses) <= .12
    assert commanded == pytest.approx(end)


def test_recorded_independent_joint_ramp_is_rejected_before_loaded_retract(monkeypatch, focus006_loaded_retract):
    from tangying_robot_gateway.gazebo_actuation import joint_ramp_step

    target, joints, base, upright, tool = focus006_loaded_retract
    # Restore only the former independent ramp. The same reachable Cartesian
    # endpoints must now be rejected because their intermediate FK drops >8mm.
    monkeypatch.setattr(module, "joint_ramp_step",
                        lambda commanded, waypoint, **_kw: joint_ramp_step(commanded, waypoint))
    with pytest.raises(ValueError, match="PAYLOAD_RETRACT_PATH_UNREACHABLE"):
        module.plan_loaded_retract_chunk(target, joints, base, upright=upright, side="left", tool=tool)


@pytest.mark.parametrize("loaded", [False, True])
def test_only_loaded_retract_opts_into_coordinated_execution(monkeypatch, focus006_loaded_retract, loaded):
    target, joints, base, upright, tool = focus006_loaded_retract
    c, _, _, _ = clearance_controller(monkeypatch)
    c.node.runtime.base_pose = base.copy()
    c.node.joint_snapshot = lambda: (dict(joints), 0., 1)
    c.tool = tool
    sent = []

    def execute(_node, chunk, cancel, **kwargs):
        assert chunk and not cancel.is_set()
        sent.append(kwargs)
        return Result(True)

    monkeypatch.setattr(module, "execute_chunk", execute)
    # clearance_controller intentionally masks motion; use the real planning
    # method while this test alone records the final executor call offline.
    result = module.GazeboManipulation.move_tip(c, target, upright=upright, loaded_retract=loaded)
    assert result.success
    assert sent == ([{"coordinated": True}] if loaded else [{}])
