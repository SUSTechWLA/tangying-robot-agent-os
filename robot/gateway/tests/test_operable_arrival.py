"""Judging an arrival by whether the robot can *work* from where it stopped.

The two claims under test are separate on purpose. "Is the base where it was
commanded" is a following check the runtime already owns; "can the arm reach the
work target from there" is the one nothing answered, and the tests below are mostly
about keeping them separable - because a caller told only one of them goes and
fixes the wrong thing.

Poses are 7-element ``[x, y, z, qw, qx, qy, qz]`` unless a test says otherwise.
A yaw of ``theta`` is ``qw = cos(theta/2), qz = sin(theta/2)``.
"""

from __future__ import annotations

import math

import pytest
from tangying_robot_gateway.operable_arrival import (
    MAX_USABLE_POSITION_TOLERANCE_M,
    MAX_USABLE_YAW_TOLERANCE_RAD,
    verify_operable_arrival,
)
from tangying_robot_gateway.workspace_planner import WorkspaceEnvelope

#: Reach 0.20-0.80 m from a shoulder at 0.70 m - the shape of the commissioned
#: figures, not the values.
ENVELOPE = WorkspaceEnvelope(base_radius=0.12, safety_margin=0.05,
                             arm_min_reach=0.20, arm_max_reach=0.80,
                             shoulder_height=0.70)


def pose_at(x: float, y: float, yaw: float = 0.0, z: float = 0.0) -> list[float]:
    return [x, y, z, math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]


#: A target that is 0.50 m from the origin at shoulder height: comfortably inside
#: the shell, so a test that wants an out-of-reach case has to move the base.
TARGET = (0.50, 0.0, 0.70)


def verdict(**overrides):
    arguments = {
        "planned_pose": pose_at(0.0, 0.0), "observed_pose": pose_at(0.0, 0.0),
        "work_target": TARGET, "envelope": ENVELOPE,
    }
    arguments.update(overrides)
    return verify_operable_arrival(**arguments)


# --- the happy path ---------------------------------------------------------

def test_arriving_at_the_commanded_pose_in_range_passes_both_claims():
    result = verdict()
    assert result.passed and result.code == "NAV_ARRIVAL_CONFIRMED"
    assert result.pose_matched and result.in_reach
    assert result.position_error_m == pytest.approx(0.0)
    assert result.yaw_error_rad == pytest.approx(0.0)
    assert result.reach_m == pytest.approx(0.50)
    # Headroom is the distance to the nearer envelope edge: 0.80 - 0.50.
    assert result.reach_margin_m == pytest.approx(0.30)


def test_the_reported_reach_is_measured_from_the_observed_pose_not_the_planned_one():
    """The whole point: the plan's reach figure describes a pose the base may
    never have occupied, so a pass may not be inherited from it."""

    result = verdict(planned_pose=pose_at(0.0, 0.0), observed_pose=pose_at(0.10, 0.0))
    assert result.reach_m == pytest.approx(0.40), "reach was taken from the plan, not the base"
    assert result.reach_m != pytest.approx(0.50)


def test_reach_is_three_dimensional_and_uses_the_shoulder_height():
    """A base directly under the target is at the *inner* limit, not at zero."""

    overhead = (0.0, 0.0, 1.60)
    result = verdict(work_target=overhead)
    assert result.reach_m == pytest.approx(0.90)  # 1.60 - 0.70
    assert result.in_reach is False
    assert result.reach_margin_m < 0


# --- the two failures, told apart ------------------------------------------

def test_a_short_drive_is_a_following_error_and_names_the_reach_consequence():
    result = verdict(observed_pose=pose_at(0.40, 0.0))
    assert not result.passed and result.code == "NAV_ARRIVAL_MISMATCH"
    assert result.pose_matched is False
    assert result.position_error_m == pytest.approx(0.40)
    # Driving past the target puts the base 0.10 m from it, inside the 0.20 m
    # inner limit - the arm is on top of the thing it was meant to reach.
    assert result.in_reach is False
    assert result.reach_m == pytest.approx(0.10)
    assert result.reach_margin_m == pytest.approx(-0.10)
    assert "outside the operable range" in result.message, (
        "a reached-out-of-reach arrival must name the consequence, not only the miss")
    assert "0.400 m" in result.message


def test_a_short_drive_that_still_leaves_the_target_reachable_says_so():
    """The reach consequence is reported either way - including when there is none.

    A caller that only hears "you missed the pose" cannot tell whether the arm can
    still work, and the answer changes what to do next.
    """

    result = verdict(observed_pose=pose_at(0.05, 0.0))
    assert result.pose_matched is False and result.in_reach is True
    assert result.code == "NAV_ARRIVAL_MISMATCH"
    assert "can still work from here" in result.message
    assert "0.450 m" in result.message


def test_a_matched_pose_outside_the_envelope_is_a_planning_failure_not_a_miss():
    """At the commanded pose and still out of range: the candidate was marginal.

    Re-driving to the same candidate cannot help, so this must not be reported as
    a following error - that is the classification the caller acts on.
    """

    # A target 1.40 m away: outside the 0.80 m envelope, with the base exactly
    # where it was told to be.
    result = verdict(work_target=(1.40, 0.0, 0.70))
    assert not result.passed and result.code == "TARGET_UNREACHABLE"
    assert result.pose_matched is True and result.in_reach is False
    assert result.reach_m == pytest.approx(1.40)
    assert result.reach_margin_m == pytest.approx(-0.60)
    assert "choose another rather than re-driving" in result.message


def test_the_inner_limit_is_reported_as_the_inner_limit():
    result = verdict(work_target=(0.10, 0.0, 0.70))
    assert result.reach_margin_m == pytest.approx(-0.10)
    assert "inside the inner" in result.message


def test_yaw_error_is_reported_but_does_not_change_reach():
    """Reach here is yaw-independent, and the module says so rather than pretending.

    A real arm's accessible set depends on orientation. Modelling that here would
    be inventing a conversion nothing measured, so the yaw error is surfaced for a
    caller that knows better and the reach number is left alone.
    """

    turned = verdict(observed_pose=pose_at(0.0, 0.0, yaw=math.pi))
    facing = verdict(observed_pose=pose_at(0.0, 0.0, yaw=0.0))
    assert turned.reach_m == pytest.approx(facing.reach_m)
    assert turned.in_reach and facing.in_reach
    assert turned.yaw_error_rad == pytest.approx(math.pi)
    assert turned.pose_matched is False and turned.code == "NAV_ARRIVAL_MISMATCH"


# --- pose parsing -----------------------------------------------------------

def test_yaw_error_wraps_the_short_way_round():
    """+pi and -pi are the same heading; the error must not read as 2*pi."""

    result = verdict(observed_pose=pose_at(0.0, 0.0, yaw=-math.pi),
                     planned_pose=pose_at(0.0, 0.0, yaw=math.pi))
    assert result.yaw_error_rad == pytest.approx(0.0, abs=1e-9)


def test_a_three_element_pose_is_read_as_x_y_yaw():
    result = verify_operable_arrival(
        planned_pose=[0.0, 0.0, 0.0], observed_pose=[0.0, 0.0, 0.0],
        work_target=TARGET, envelope=ENVELOPE)
    assert result.passed and result.reach_m == pytest.approx(0.50)


def test_a_pose_of_the_wrong_length_is_refused():
    with pytest.raises(ValueError, match="3- or 7-element"):
        verdict(observed_pose=[0.0, 0.0])


def test_a_non_finite_pose_or_target_is_refused():
    with pytest.raises(ValueError, match="must be finite"):
        verdict(observed_pose=[float("nan"), 0.0, 0.0])
    with pytest.raises(ValueError, match="work target must be finite"):
        verdict(work_target=(0.5, 0.0, float("inf")))


def test_a_degenerate_quaternion_is_refused_rather_than_read_as_zero_yaw():
    with pytest.raises(ValueError, match="zero norm"):
        verdict(observed_pose=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])


# --- the tolerance rule -----------------------------------------------------

def test_a_verifier_may_not_be_looser_than_the_controller_it_checks():
    """A checker that accepts what the controller rejects cannot fail usefully.

    The runtime refuses to be commissioned above these bounds, so asking this
    module for a sloppier arrival would produce a pass for a pose the robot was
    never able to hold. Refused loudly, not clamped - a clamped tolerance reads
    as agreement.
    """

    with pytest.raises(ValueError, match="exceeds the"):
        verdict(position_tolerance_m=MAX_USABLE_POSITION_TOLERANCE_M + 1e-6)
    with pytest.raises(ValueError, match="exceeds the"):
        verdict(yaw_tolerance_rad=MAX_USABLE_YAW_TOLERANCE_RAD + 1e-6)
    # Exactly at the bound is allowed: that is the controller's own limit.
    assert verdict(position_tolerance_m=MAX_USABLE_POSITION_TOLERANCE_M,
                   yaw_tolerance_rad=MAX_USABLE_YAW_TOLERANCE_RAD).passed


def test_a_tighter_tolerance_than_the_default_is_allowed_and_can_fail():
    loose = verdict(observed_pose=pose_at(0.010, 0.0))
    tight = verdict(observed_pose=pose_at(0.010, 0.0), position_tolerance_m=0.005)
    assert loose.passed
    assert not tight.passed and tight.code == "NAV_ARRIVAL_MISMATCH"


def test_negative_or_non_finite_tolerances_are_refused():
    with pytest.raises(ValueError, match="cannot be negative"):
        verdict(position_tolerance_m=-0.001)
    with pytest.raises(ValueError, match="must be finite"):
        verdict(yaw_tolerance_rad=float("nan"))


def test_a_missing_envelope_is_refused_rather_than_defaulted():
    """Without an envelope there is no reach claim to make, and guessing one
    would produce a confident answer about a robot nobody commissioned."""

    with pytest.raises(TypeError, match="commissioned workspace envelope"):
        verify_operable_arrival(planned_pose=pose_at(0, 0), observed_pose=pose_at(0, 0),
                                work_target=TARGET, envelope=None)


# --- the wire shape ---------------------------------------------------------

def test_the_report_carries_both_verdicts_and_the_model_they_came_from():
    payload = verdict().to_dict()
    assert payload["passed"] is True and payload["poseMatched"] is True
    assert payload["inReach"] is True
    assert payload["reachModel"].startswith("shoulder-relative")
    assert payload["armReach"] == {"min": 0.20, "max": 0.80, "shoulderHeightM": 0.70}
    # This envelope commissions no manipulation travel, so the operable range is
    # the arm's own shell and the two numbers coincide.
    assert payload["operableRangeM"] == [0.20, 0.80]
    assert payload["manipulationTravelM"] == 0.0
    assert payload["workTarget"] == list(TARGET)
    assert payload["verdictSource"] == "gateway_operable_arrival"
    # Nothing in the payload is a numpy scalar or a tuple: it goes on the wire.
    import json

    assert json.loads(json.dumps(payload)) == payload
