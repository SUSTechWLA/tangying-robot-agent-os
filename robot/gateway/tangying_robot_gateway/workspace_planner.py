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

    def __post_init__(self):
        values = vars(self).values()
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError("workspace envelope must contain finite numbers")
        if (self.base_radius <= 0 or self.safety_margin < 0 or self.arm_min_reach < 0
                or self.arm_max_reach <= self.arm_min_reach or self.shoulder_height < 0):
            raise ValueError("invalid commissioned workspace envelope")


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
        reach = math.sqrt((target_xyz[0]-x)**2 + (target_xyz[1]-y)**2
                          + (target_xyz[2]-envelope.shoulder_height)**2)
        if envelope.arm_min_reach <= reach <= envelope.arm_max_reach:
            theta = math.atan2(target_xyz[1]-y, target_xyz[0]-x)
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
            'requiresKinematicsValidation': validate_candidate is None,
            'executionAuthorized': False, 'requiresFreshLocalization': True,
            'requiresDynamicObstacleChecking': True}
