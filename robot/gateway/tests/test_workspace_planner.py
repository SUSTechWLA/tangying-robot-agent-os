import math

import numpy as np
import pytest
from tangying_robot_gateway.workspace_planner import (
    WorkspaceEnvelope,
    plan_workspace,
    shoulder_reach,
)

E = WorkspaceEnvelope(.12,.05,.2,.8,.7)

def grid(cells=None, origin=None):
    return {'width': 40,'height': 40,'resolution': .1,'origin': origin or [0,0,0],
                'cells': np.zeros((40,40), dtype=int) if cells is None else cells}

def test_candidates_have_free_connected_paths_but_no_implicit_ik_or_execution():
    result = plan_workspace(grid(), [1,1], [3,3,.9], E)
    assert result['candidates'] and result['requiresKinematicsValidation']
    assert not result['executionAuthorized']
    for candidate in result['candidates']:
        assert .2 <= candidate['reachMeters'] <= .8
        assert len(candidate['path']) > 2
        assert not candidate['kinematicsVerified']

def test_wall_and_unknown_space_cannot_be_crossed():
    cells = np.zeros((40,40),dtype=int)
    cells[:,20] = 100
    assert not plan_workspace(grid(cells),[1,1],[3,3,.9],E)['candidates']
    cells[:,20] = -1
    assert not plan_workspace(grid(cells),[1,1],[3,3,.9],E)['candidates']

def test_ik_rejection_and_rotated_map_origin():
    assert not plan_workspace(grid(),[1,1],[3,3,.9],E, validate_candidate=lambda *_: False)['candidates']
    result = plan_workspace(grid(origin=[10,20,math.pi/2]),[9,21],[7,23,.9],E,
                            validate_candidate=lambda *_: True)
    assert result['candidates'][0]['kinematicsVerified']
    assert not result['executionAuthorized']

def test_invalid_envelope_and_unknown_start_are_refused():
    with pytest.raises(ValueError): WorkspaceEnvelope(.1,0,0,float('nan'),1)
    with pytest.raises(ValueError): plan_workspace(grid(),[0,0],[2,2,1],E)


# --- the reach is measured from the shoulder, not from the base origin -------

def test_a_shoulder_offset_is_rotated_by_the_base_heading():
    """The offset is in the base frame, so it swings with the base."""

    envelope = WorkspaceEnvelope(.12, .05, .05, .9, .7, (0.2, 0.0))
    # Facing +x, the forward offset is along the target direction...
    assert envelope.shoulder_in_base(0.0) == pytest.approx((0.2, 0.0, 0.7))
    # ...and a quarter turn puts it to the left, where it no longer shortens the
    # distance to a target ahead.
    assert envelope.shoulder_in_base(math.pi / 2) == pytest.approx((0.0, 0.2, 0.7), abs=1e-12)


def test_the_shoulder_offset_changes_whether_a_target_is_reachable():
    """The whole reason the field exists: the same geometry, two verdicts.

    A shoulder 0.2 m forward reaches a target 0.15 m nearer than a shoulder at the
    base origin does. With a 0.5 m outer limit that is the difference between a
    candidate and no candidate - so a base-origin measurement is not a rounding
    error, it is a different answer.
    """

    forward = WorkspaceEnvelope(.12, .05, .05, .5, .7, (0.2, 0.0))
    centred = WorkspaceEnvelope(.12, .05, .05, .5, .7)
    target = (2.0, 0.0, 0.7)  # 2.0 m ahead of a base at the origin, at shoulder height

    assert shoulder_reach((0.0, 0.0), 0.0, target, centred) == pytest.approx(2.0)
    assert shoulder_reach((0.0, 0.0), 0.0, target, forward) == pytest.approx(1.8)


def test_a_lateral_shoulder_makes_a_target_further_not_nearer():
    """A shoulder off to one side has to stand further back to reach the same point.

    Worth pinning because the intuition runs the other way - an offset feels like it
    should help - and a sign error here would silently make marginal poses look
    comfortable.
    """

    lateral = WorkspaceEnvelope(.12, .05, .05, .9, .7, (0.0, 0.2))
    centred = WorkspaceEnvelope(.12, .05, .05, .9, .7)
    target = (0.5, 0.0, 0.7)
    assert shoulder_reach((0.0, 0.0), 0.0, target, lateral) > shoulder_reach(
        (0.0, 0.0), 0.0, target, centred)
    assert shoulder_reach((0.0, 0.0), 0.0, target, lateral) == pytest.approx(
        math.hypot(0.5, 0.2))


def test_the_planner_and_the_verifier_measure_the_same_reach():
    """One definition, so a pose cannot be planned and then rejected.

    ``plan_workspace`` reports ``reachMeters`` per candidate; the arrival check
    re-measures from the observed pose. Same envelope, same pose, same target must
    give the same number - if these two ever drift apart, the robot gets planned
    into a pose its own verifier refuses.
    """

    envelope = WorkspaceEnvelope(.12, .05, .4, .6, .7, (0.13, 0.15))
    target = (2.0, 2.0, 0.9)
    result = plan_workspace(grid(), [1, 1], target, envelope)
    assert result['candidates']
    for candidate in result['candidates'][:4]:
        pose = candidate['basePose']
        # The pose is a half-angle quaternion: recovering the heading needs the
        # factor of two, and getting that wrong is how the composite ended up
        # commanding half the planned yaw.
        yaw = 2.0 * math.atan2(pose[6], pose[3])
        assert candidate['reachMeters'] == pytest.approx(
            shoulder_reach((pose[0], pose[1]), yaw, target, envelope))


def test_the_plan_reports_the_target_and_envelope_it_planned_against():
    """A plan that cannot be re-checked is a plan whose reach nobody can confirm."""

    result = plan_workspace(grid(), [1, 1], [3, 3, .9], E)
    assert result['workTarget'] == [3.0, 3.0, 0.9]
    assert result['armReach']['min'] == .2 and result['armReach']['max'] == .8
    assert result['armReach']['shoulderOffsetM'] == [0.0, 0.0]


def test_a_bad_shoulder_offset_is_refused():
    with pytest.raises(ValueError, match="shoulder offset"):
        WorkspaceEnvelope(.12, .05, .2, .8, .7, (0.1,))
    with pytest.raises(ValueError, match="shoulder offset"):
        WorkspaceEnvelope(.12, .05, .2, .8, .7, (0.1, float("nan")))


def test_base_manipulation_travel_widens_the_operable_range_by_the_diagonal():
    """The base can creep on both axes, so the reach grows by sqrt(2) x travel.

    Commissioned from the shipped robot: a 0.418 m arm on a base allowed 0.35 m per
    axis works out to 0.914 m. That gap is not a safety margin - a live run put the
    robot at its commissioned kitchen dock, 0.442 m from the cup, and a static-arm
    check called it out of reach while the pick succeeds.
    """

    static = WorkspaceEnvelope(.12, .05, .0, .418, .7865)
    mobile = WorkspaceEnvelope(.12, .05, .0, .418, .7865,
                               manipulation_travel_m=.35)
    assert static.operable_reach == (0.0, .418)
    assert mobile.base_travel_radius == pytest.approx(math.sqrt(2) * .35)
    assert mobile.operable_reach[1] == pytest.approx(.418 + math.sqrt(2) * .35)
    # 0.442 m: outside the arm alone, inside the robot's real range.
    assert not (static.operable_reach[0] <= .442 <= static.operable_reach[1])
    assert mobile.operable_reach[0] <= .442 <= mobile.operable_reach[1]


def test_travel_also_removes_the_inner_dead_zone():
    """A target too close to the arm can be reached by backing the base away."""

    envelope = WorkspaceEnvelope(.12, .05, .30, .418, .7865, manipulation_travel_m=.35)
    assert envelope.operable_reach[0] == 0.0
    tight = WorkspaceEnvelope(.12, .05, .30, .418, .7865, manipulation_travel_m=.1)
    assert tight.operable_reach[0] == pytest.approx(.30 - math.sqrt(2) * .1)


def test_the_planner_admits_a_candidate_the_bare_arm_would_refuse():
    """The widening has to reach ``plan_workspace`` too, or the two disagree.

    The base is boxed into a small free pocket, so it cannot park next to the target
    and the only way to work on it is to reach - plus whatever the base can creep.
    The target sits 0.70 m out: past a 0.45 m arm, inside the 0.945 m mobile range.
    """

    cells = np.full((40, 40), 100, dtype=int)
    cells[15:24, 15:24] = 0                     # a 0.9 m pocket around (2.0, 2.0)
    target = (2.7, 2.0, .7)

    static = plan_workspace(grid(cells), [2.0, 2.0], target,
                            WorkspaceEnvelope(.12, .05, .0, .45, .7))
    mobile = plan_workspace(grid(cells), [2.0, 2.0], target,
                            WorkspaceEnvelope(.12, .05, .0, .45, .7, (0.0, 0.0), .35))
    assert static['candidates'] == [], "a 0.45 m arm cannot work 0.70 m away"
    assert mobile['candidates'], "0.70 m is inside the mobile working range"
    for candidate in mobile['candidates']:
        assert candidate['reachMeters'] > .45, "the accepted candidate is inside the bare shell"
    assert mobile['armReach']['operableMaxM'] == pytest.approx(.45 + math.sqrt(2) * .35)
