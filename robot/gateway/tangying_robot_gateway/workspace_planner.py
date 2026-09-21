"""Map-bound base candidates for mobile manipulation; never a motion controller.

Unknown cells and the map boundary are obstacles. Reach is only a geometric
prefilter: a commissioned robot-specific whole-body IK/collision validator must
accept a candidate before it is eligible for execution. Nav2 remains responsible
for fresh localization, dynamic obstacles and bounded motion during execution.
"""
import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from .navigation_map import validate_grid


@dataclass(frozen=True)
class WorkspaceEnvelope:
    base_radius: float
    safety_margin: float
    arm_min_reach: float
    arm_max_reach: float
    shoulder_height: float
    #: Where the arm's shoulder joint sits in the base frame, as (forward, left).
    #:
    #: This is not a refinement - it is the difference between a reach figure that
    #: means something and one that does not. ``arm_min_reach``/``arm_max_reach``
    #: are properties of the arm measured *from its shoulder*, because that is the
    #: point the first joint rotates about. Measuring the same shell from the base
    #: origin instead puts the shoulder's own offset (0.195 m laterally and 0.7865 m
    #: up on the shipped tabletop arm) inside the number: the arm's true reachable
    #: set, 0.000-0.418 m from the shoulder, becomes 0.703-1.264 m from the base,
    #: so a correctly commissioned ``arm_max_reach = 0.42`` would reject every
    #: candidate on a base-origin measurement and accept everything on a
    #: shoulder-based one.
    #:
    #: Defaults to zero so a caller that has not measured its shoulder keeps the
    #: old arithmetic, and the caller that has gets the right answer.
    shoulder_offset_m: tuple[float, float] = (0.0, 0.0)
    #: How far the **base** may travel while the arm is working, in metres per axis.
    #:
    #: Not a planning margin and not a footprint: it is the bounded creep a mobile
    #: manipulator performs to finish a grasp, and leaving it out makes the operable
    #: range measurably wrong. The shipped tabletop robot allows 0.35 m per axis
    #: (``TabletopWorld.BASE_TRANSLATION_LIMIT``), so a point 0.442 m from the
    #: shoulder is 0.024 m outside a static 0.418 m arm shell and comfortably inside
    #: the working range - the false negative this field was added to remove, after a
    #: live run against the running robot produced exactly it.
    manipulation_travel_m: float = 0.0

    def __post_init__(self):
        scalars = (self.base_radius, self.safety_margin, self.arm_min_reach,
                   self.arm_max_reach, self.shoulder_height, self.manipulation_travel_m)
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in scalars):
            raise ValueError("workspace envelope must contain finite numbers")
        if (self.base_radius <= 0 or self.safety_margin < 0 or self.arm_min_reach < 0
                or self.arm_max_reach <= self.arm_min_reach or self.shoulder_height < 0):
            raise ValueError("invalid commissioned workspace envelope")
        if self.manipulation_travel_m < 0:
            raise ValueError("manipulation travel cannot be negative")
        if (not isinstance(self.shoulder_offset_m, (tuple, list))
                or len(self.shoulder_offset_m) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       for v in self.shoulder_offset_m)):
            raise ValueError("shoulder offset must be two finite numbers (forward, left)")

    @property
    def base_travel_radius(self) -> float:
        """How far base travel alone can carry the tool, in any direction.

        The base moves up to ``manipulation_travel_m`` along each axis, so the
        furthest it can carry the arm is the diagonal, not the axis distance.
        """

        return math.sqrt(2.0) * float(self.manipulation_travel_m)

    @property
    def operable_reach(self) -> tuple[float, float]:
        """The shoulder-to-target distances the robot can actually work at.

        The arm's shell, thickened by what the base can carry it: a point just
        outside the static shell is reached by creeping towards it, and one just
        inside the inner limit by creeping away.
        """

        travel = self.base_travel_radius
        return (max(0.0, float(self.arm_min_reach) - travel),
                float(self.arm_max_reach) + travel)

    def shoulder_in_base(self, yaw: float) -> tuple[float, float, float]:
        """The shoulder joint in the map frame for a base at ``(x, y, yaw)``.

        Returned relative to the base origin, so a caller adds it to the base
        position. The base faces its work target when a candidate is planned, so
        the offset's forward component points at the target and its lateral
        component is perpendicular to it - which is why a base that reaches its
        target with a lateral shoulder still has to be *further* away than the
        reach alone suggests, not nearer.
        """

        forward, left = float(self.shoulder_offset_m[0]), float(self.shoulder_offset_m[1])
        cos_yaw, sin_yaw = math.cos(yaw), math.sin(yaw)
        return (forward * cos_yaw - left * sin_yaw,
                forward * sin_yaw + left * cos_yaw,
                float(self.shoulder_height))


def shoulder_reach(base_xy, yaw: float, target_xyz, envelope: WorkspaceEnvelope) -> float:
    """3-D distance from the shoulder joint to a target, for a base at ``base_xy``.

    The one place this distance is defined, so the planner and the arrival check
    cannot drift into measuring two different things - which is how a robot gets
    planned into a pose the verifier then rejects.
    """

    shoulder = envelope.shoulder_in_base(yaw)
    return math.sqrt((float(target_xyz[0]) - (base_xy[0] + shoulder[0])) ** 2
                     + (float(target_xyz[1]) - (base_xy[1] + shoulder[1])) ** 2
                     + (float(target_xyz[2]) - shoulder[2]) ** 2)


def plan_workspace(grid, start_xy, target_xyz, envelope, *, validate_candidate=None):
    grid = validate_grid(grid)
    if grid['width'] * grid['height'] > 250000:
        raise ValueError("workspace planning exceeds 250000-cell budget; use the Nav2 planner")
    for values, length in ((start_xy, 2), (target_xyz, 3)):
        if len(values) != length or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError("start and target must be finite map coordinates")
    resolution = grid['resolution']
    ox, oy, yaw = grid['origin']
    c, s = math.cos(yaw), math.sin(yaw)

    def cell(x, y):
        return (math.floor((-s*(x-ox)+c*(y-oy))/resolution),
                math.floor((c*(x-ox)+s*(y-oy))/resolution))

    def world(row, col):
        x, y = (col+.5)*resolution, (row+.5)*resolution
        return ox+c*x-s*y, oy+s*x+c*y

    rows, cols = grid['height'], grid['width']
    # Include half the cell diagonal, because obstacle samples represent cells,
    # not infinitesimal points. Outside-map padding is occupied.
    radius = envelope.base_radius + envelope.safety_margin + resolution / math.sqrt(2)
    n = math.ceil(radius/resolution)
    if n > 100:
        raise ValueError("footprint inflation exceeds the planning budget")
    blocked = grid['cells'] != 0
    padded = np.pad(blocked, n, constant_values=True)
    free = ~blocked.copy()
    for dy in range(-n, n+1):
        for dx in range(-n, n+1):
            if math.hypot(dx, dy)*resolution <= radius:
                free &= ~padded[n+dy:n+dy+rows, n+dx:n+dx+cols]
    start = cell(*start_xy)
    if not (0 <= start[0] < rows and 0 <= start[1] < cols and free[start]):
        raise ValueError("current base pose is outside known footprint-clear free space")
    parent = {start: None}
    queue = deque([start])
    candidates = []
    while queue:
        current = queue.popleft()
        x, y = world(*current)
        # A candidate base faces its target, so its yaw is known before the reach
        # is measured - the shoulder offset has to be rotated by it, not ignored.
        theta = math.atan2(target_xyz[1]-y, target_xyz[0]-x)
        reach = shoulder_reach((x, y), theta, target_xyz, envelope)
        low, high = envelope.operable_reach
        if low <= reach <= high:
            pose = [x, y, 0., math.cos(theta/2), 0., 0., math.sin(theta/2)]
            # Only literal True is acceptance; dictionaries/strings/errors are
            # not a collision-checking verdict. Errors propagate, never approve.
            verified = validate_candidate is not None and validate_candidate(pose, target_xyz) is True
            if validate_candidate is None or verified:
                path, node = [], current
                while node is not None:
                    path.append(list(world(*node)))
                    node = parent[node]
                candidates.append({'basePose': pose, 'path': list(reversed(path)),
                                   'reachMeters': reach, 'kinematicsVerified': verified})
                if len(candidates) >= 8:
                    break
        for dy, dx in ((1,0),(-1,0),(0,1),(0,-1)):
            neighbor = current[0]+dy, current[1]+dx
            if (0 <= neighbor[0] < rows and 0 <= neighbor[1] < cols
                    and free[neighbor] and neighbor not in parent):
                parent[neighbor] = current
                queue.append(neighbor)
    return {'frameId': 'map', 'candidates': candidates,
            # What the candidates were chosen *against*. Returning the target and
            # the envelope is what lets a caller re-check reach once the robot has
            # actually moved, instead of trusting a prediction about a pose it may
            # never have occupied. See `operable_arrival`.
            'workTarget': [float(value) for value in target_xyz],
            'armReach': {'min': envelope.arm_min_reach, 'max': envelope.arm_max_reach,
                         'shoulderHeightM': envelope.shoulder_height,
                         'shoulderOffsetM': list(envelope.shoulder_offset_m),
                         'baseRadiusM': envelope.base_radius,
                         'safetyMarginM': envelope.safety_margin,
                         'manipulationTravelM': envelope.manipulation_travel_m,
                         'operableMinM': envelope.operable_reach[0],
                         'operableMaxM': envelope.operable_reach[1]},
            'requiresKinematicsValidation': validate_candidate is None,
            'executionAuthorized': False, 'requiresFreshLocalization': True,
            'requiresDynamicObstacleChecking': True}
