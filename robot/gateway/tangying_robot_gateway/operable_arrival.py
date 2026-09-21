"""Did the robot arrive somewhere it can actually *work* from?

The chain this module closes is "say a place, drive there, operate on something
there", and the third clause is the one that was never checked. Two different
questions get confused under the single heading "did we arrive":

  1. **Is the base where we commanded it?** The runtime answers this. MuJoCo's
     ``verify_arrival`` captures a fresh pose and compares it to ``goalPose``.
     That is a *following* check: it says the controller did what it was told.

  2. **Can the arm reach the work target from where the base actually ended up?**
     Nobody answered this. ``workspace_planner.plan_workspace`` promises it about
     the pose it *planned* - it only offers cells whose 3-D distance to the target
     falls inside the commissioned arm envelope - but the robot does not end up at
     the planned pose. It ends up wherever odometry, the controller and the
     physics left it, and the plan's own ``reachMeters`` is a prediction about a
     pose the base may never have occupied.

The gap is not academic. With the shipped numbers a base can be 0.05 m and 0.12 rad
from the commanded pose and still pass ``verify_arrival`` - while the arm envelope
is a hard interval, so a pose a few centimetres outside its outer edge is a pose
from which the object cannot be picked up at all. The robot reports
``NAV_ARRIVAL_CONFIRMED`` and then fails the grasp, and the failure is attributed
to perception.

So this module measures reach **from the observed pose**, and reports it together
with the following error rather than instead of it. Both numbers, always: a caller
told only "you are not in range" will re-drive, and a caller told only "you missed
the pose" will re-tune the controller, and when both are wrong either single answer
sends them to fix the wrong thing.

Two limits of the model, stated here so nobody reads more into a pass than it says:

  * **Reach is measured from the shoulder joint, at the observed yaw.** The shell
    ``[arm_min_reach, arm_max_reach]`` is a property of the arm about its first
    joint, so the shoulder's offset in the base frame is rotated by the base's
    observed heading before the distance is taken. A base that ends up facing away
    from its target therefore gets a *different, larger* reach than one facing it,
    which is a real effect rather than a modelled one: the shoulder swings round
    with the base. What is still not modelled is the arm's orientation-dependent
    accessible set beyond that first joint - a target inside the shell can still be
    ungraspable at some wrist angles. That belongs to the kinematics validator.
  * **Only the base and the target are modelled.** No self-collision, no payload, no
    intermediate obstacle between shoulder and target. Those belong to the
    kinematics validator the plan already flags as ``requiresKinematicsValidation``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .workspace_planner import WorkspaceEnvelope, shoulder_reach

__all__ = [
    "MAX_USABLE_POSITION_TOLERANCE_M",
    "MAX_USABLE_YAW_TOLERANCE_RAD",
    "OperableArrival",
    "verify_operable_arrival",
]

#: A verifier may be *tighter* than the controller it checks, never looser.
#:
#: ``rgbd_navigation`` refuses to be commissioned with a position tolerance above
#: 0.02 m or a yaw tolerance above 0.05 rad, so a caller that asks this module to
#: accept a sloppier arrival than the controller itself would accept has built a
#: check that cannot fail for the reason it exists. That is refused loudly instead
#: of silently clamped, because a silently clamped tolerance reads as agreement.
MAX_USABLE_POSITION_TOLERANCE_M = 0.02
MAX_USABLE_YAW_TOLERANCE_RAD = 0.05

#: The verdict codes are the **runtime's own vocabulary**, not a parallel one.
#:
#: ``NAV_ARRIVAL_CONFIRMED`` and ``NAV_ARRIVAL_MISMATCH`` are what MuJoCo's
#: ``verify_arrival`` already answers, so a caller that has handled those handles
#: these. ``TARGET_UNREACHABLE`` is the Go classification table's existing word for
#: "the thing you wanted is not reachable from the plan" - class Planning, which is
#: correctly not retryable: re-driving to the same candidate cannot help.
#:
#: (``NAV_ARRIVAL_MISMATCH`` is emitted by the runtime but was classified by
#: neither side, so the tool layer filed a stopped-short drive as
#: ``HARDWARE_ERROR / UNKNOWN_OUTCOME`` - "result unknown, do not retry" for a base
#: that measurably stopped 8 cm short. It is now classified Perception/recoverable,
#: matching ``NAV_GOAL_NOT_REACHED`` and ``NAV_ODOM_GOAL_NOT_REACHED``, which Go
#: already files that way.)
CONFIRMED = "NAV_ARRIVAL_CONFIRMED"
POSE_MISMATCH = "NAV_ARRIVAL_MISMATCH"
OUTSIDE_REACH = "TARGET_UNREACHABLE"


def _pose_xy(pose: Sequence[float], label: str) -> tuple[float, float, float]:
    if not isinstance(pose, (list, tuple)) or len(pose) not in (3, 7):
        raise ValueError(f"{label} must be a 3- or 7-element pose")
    values = [float(value) for value in pose]
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{label} must be finite")
    return values[0], values[1], values[2]


def _yaw(pose: Sequence[float]) -> float:
    """Yaw of a 7-element pose, or the third element of a 3-element one.

    The 7-element layout is ``[x, y, z, qw, qx, qy, qz]`` - the same order the
    runtime's ``goalPose`` uses and the same order ``grid_navigation`` publishes.
    """

    values = [float(value) for value in pose]
    if len(values) == 3:
        return values[2]
    w, x, y, z = values[3], values[4], values[5], values[6]
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm <= 0.0:
        raise ValueError("pose quaternion has zero norm")
    w, x, y, z = w / norm, x / norm, y / norm, z / norm
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


@dataclass(frozen=True)
class OperableArrival:
    """The two answers, plus the numbers behind them."""

    passed: bool
    code: str
    message: str
    #: Distance from where the base was observed to where it was commanded.
    position_error_m: float
    #: Smallest signed rotation from the observed heading to the commanded one.
    yaw_error_rad: float
    #: 3-D distance from the **observed** base origin to the work target.
    reach_m: float
    #: How much room is left inside the operable range. Positive is inside, and its
    #: magnitude is the distance to the nearer limit; negative is how far outside.
    reach_margin_m: float
    pose_matched: bool
    in_reach: bool
    planned_pose: tuple[float, ...]
    observed_pose: tuple[float, ...]
    work_target: tuple[float, float, float]
    arm_min_reach: float
    arm_max_reach: float
    shoulder_height_m: float
    operable_min_m: float
    operable_max_m: float
    manipulation_travel_m: float

    def to_dict(self) -> dict[str, Any]:
        """Wire-shaped, with the two verdicts kept separate on purpose."""

        return {
            "passed": self.passed, "code": self.code, "message": self.message,
            "positionErrorM": round(self.position_error_m, 6),
            "yawErrorRad": round(self.yaw_error_rad, 6),
            "reachM": round(self.reach_m, 6),
            "reachMarginM": round(self.reach_margin_m, 6),
            "poseMatched": self.pose_matched, "inReach": self.in_reach,
            "plannedPose": list(self.planned_pose), "observedPose": list(self.observed_pose),
            "workTarget": list(self.work_target),
            "armReach": {"min": self.arm_min_reach, "max": self.arm_max_reach,
                         "shoulderHeightM": self.shoulder_height_m},
            "operableRangeM": [self.operable_min_m, self.operable_max_m],
            "manipulationTravelM": self.manipulation_travel_m,
            "reachModel": "shoulder-relative, at the observed yaw; thickened by base manipulation travel",
            "verdictSource": "gateway_operable_arrival",
        }


def verify_operable_arrival(
    *,
    planned_pose: Sequence[float],
    observed_pose: Sequence[float],
    work_target: Sequence[float],
    envelope: WorkspaceEnvelope,
    position_tolerance_m: float = 0.015,
    yaw_tolerance_rad: float = 0.04,
) -> OperableArrival:
    """Judge one arrival against both the commanded pose and the operable range.

    ``planned_pose`` is the pose that was commanded, ``observed_pose`` is where the
    base actually is now (a fresh capture - a stale one would answer a question
    about the past), and ``work_target`` is the 3-D point in the map frame that the
    task means to operate on.

    The tolerance defaults are the runtime's own pre-position tolerances
    (``rgbd_runtime``: 0.015 m / 0.04 rad). They are defaults rather than constants
    so a commissioning can tighten them, and they are refused above
    ``MAX_USABLE_*`` because a verifier looser than the controller is not a verifier.
    """

    if not isinstance(envelope, WorkspaceEnvelope):
        raise TypeError("an operable-arrival check needs a commissioned workspace envelope")
    position_tolerance_m = float(position_tolerance_m)
    yaw_tolerance_rad = float(yaw_tolerance_rad)
    if not math.isfinite(position_tolerance_m) or not math.isfinite(yaw_tolerance_rad):
        raise ValueError("arrival tolerances must be finite")
    if position_tolerance_m < 0 or yaw_tolerance_rad < 0:
        raise ValueError("arrival tolerances cannot be negative")
    if position_tolerance_m > MAX_USABLE_POSITION_TOLERANCE_M:
        raise ValueError(
            f"position tolerance {position_tolerance_m} exceeds the {MAX_USABLE_POSITION_TOLERANCE_M} m "
            "the navigation controller itself accepts; a looser verifier cannot fail usefully")
    if yaw_tolerance_rad > MAX_USABLE_YAW_TOLERANCE_RAD:
        raise ValueError(
            f"yaw tolerance {yaw_tolerance_rad} exceeds the {MAX_USABLE_YAW_TOLERANCE_RAD} rad "
            "the navigation controller itself accepts; a looser verifier cannot fail usefully")

    planned_x, planned_y, _ = _pose_xy(planned_pose, "planned_pose")
    observed_x, observed_y, _ = _pose_xy(observed_pose, "observed_pose")
    if not isinstance(work_target, (list, tuple)) or len(work_target) != 3:
        raise ValueError("work target must be a 3-D point")
    target = tuple(float(value) for value in work_target)
    if not all(math.isfinite(value) for value in target):
        raise ValueError("work target must be finite")

    position_error_m = math.hypot(observed_x - planned_x, observed_y - planned_y)
    yaw_error_rad = abs(math.atan2(
        math.sin(_yaw(observed_pose) - _yaw(planned_pose)),
        math.cos(_yaw(observed_pose) - _yaw(planned_pose))))

    # The measurement, not the prediction: the plan's own reach figure describes a
    # pose the base may never have occupied. Measured from the shoulder joint at the
    # *observed* yaw, through the same `shoulder_reach` the planner used - if the two
    # ever measured different things, a pose could be planned and then rejected.
    reach_m = shoulder_reach((observed_x, observed_y), _yaw(observed_pose),
                             target, envelope)
    # The interval is the arm's shell thickened by what base travel can carry it,
    # not the bare arm: this is a mobile manipulator, and judging it by the static
    # arm reports a robot at its commissioned dock as unable to reach the cup.
    operable_min, operable_max = envelope.operable_reach
    reach_margin_m = min(reach_m - operable_min, operable_max - reach_m)

    pose_matched = position_error_m <= position_tolerance_m and yaw_error_rad <= yaw_tolerance_rad
    in_reach = operable_min <= reach_m <= operable_max
    passed = pose_matched and in_reach

    if passed:
        code = CONFIRMED
        message = (
            f"base is {position_error_m:.3f} m / {yaw_error_rad:.3f} rad from the commanded pose and "
            f"{reach_m:.3f} m from the work target, inside the "
            f"{operable_min:.3f}-{operable_max:.3f} m operable range")
    elif not pose_matched:
        # The primary fact is that the base is not where it was told to be, because
        # that is what a caller can act on. Being out of reach is reported as the
        # *consequence*: the planner only ever offers candidates inside the
        # envelope, so an out-of-reach observed pose is almost always a pose error
        # rather than a bad plan. Saying "not in range" alone would send an operator
        # to re-plan work areas that were never the problem.
        code = POSE_MISMATCH
        consequence = (
            f"; as a result the work target is {abs(reach_margin_m):.3f} m outside the operable range"
            if not in_reach else
            f"; the robot can still work from here ({reach_margin_m:.3f} m inside the operable range)")
        message = (
            f"base missed the commanded pose by {position_error_m:.3f} m / "
            f"{yaw_error_rad:.3f} rad and is {reach_m:.3f} m from the work target"
            + consequence)
    else:
        # At the commanded pose and still out of reach: the candidate was marginal.
        # At most a tolerance's worth of drift separates the two, so this is the
        # envelope edge rather than a following error - pick a different candidate,
        # do not re-drive this one.
        reason = "beyond the outer" if reach_m > operable_max else "inside the inner"
        code = OUTSIDE_REACH
        message = (
            f"base reached the commanded pose but is {reach_m:.3f} m from the work target, "
            f"{abs(reach_margin_m):.3f} m {reason} limit of the "
            f"{operable_min:.3f}-{operable_max:.3f} m operable range; "
            "this candidate was marginal, so choose another rather than re-driving this one")

    return OperableArrival(
        passed=passed, code=code, message=message,
        position_error_m=position_error_m, yaw_error_rad=yaw_error_rad,
        reach_m=reach_m, reach_margin_m=reach_margin_m,
        pose_matched=pose_matched, in_reach=in_reach,
        planned_pose=tuple(float(value) for value in planned_pose),
        observed_pose=tuple(float(value) for value in observed_pose),
        work_target=target,
        arm_min_reach=envelope.arm_min_reach, arm_max_reach=envelope.arm_max_reach,
        shoulder_height_m=envelope.shoulder_height,
        operable_min_m=operable_min, operable_max_m=operable_max,
        manipulation_travel_m=envelope.manipulation_travel_m,
    )
