"""Commissioned tool centre distinct from arm and jaw joint origins."""
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

import numpy as np


@lru_cache(maxsize=1)
def tool_commissioning():
    path = Path(os.environ.get('TANGYING_GAZEBO_TOOL_COMMISSIONING',
                Path(__file__).with_name('assets')/'gazebo_xlerobot_tool.json'))
    raw = path.read_bytes()
    value = json.loads(raw)
    if (value.get('schemaVersion') != 'robot.tool_commissioning.v1'
            or value.get('mode') != 'sim_suction' or value.get('parentLinkIndex') != 5
            or not isinstance(value.get('revision'), str) or not value['revision']):
        raise ValueError('TOOL_COMMISSIONING_INVALID')
    offset = value.get('offsetM')
    if (not isinstance(offset, list) or len(offset) != 3
            or any(type(v) not in (int, float) or not np.isfinite(v) for v in offset)
            or np.linalg.norm(offset) > .2):
        raise ValueError('TOOL_COMMISSIONING_OFFSET_INVALID')
    for key, low, high in [('gripperOpenRad', 0., 2.), ('contactHeightM', .06, .085),
                          ('maxAttachDistanceM', .001, .09)]:
        v = value.get(key)
        if type(v) not in (int, float) or not np.isfinite(v) or not low <= v <= high:
            raise ValueError('TOOL_COMMISSIONING_LIMIT_INVALID')
    views = value.get('observationWaypointsBaseM')
    if not isinstance(views, dict) or set(views) != {'left', 'right'}:
        raise ValueError('TOOL_COMMISSIONING_VIEW_INVALID')
    for points in views.values():
        if not isinstance(points, list) or not 1 <= len(points) <= 4:
            raise ValueError('TOOL_COMMISSIONING_VIEW_INVALID')
        for point in points:
            if (not isinstance(point, list) or len(point) != 3
                    or any(type(v) not in (int, float) or not np.isfinite(v) for v in point)
                    or not -.4 <= point[0] <= .6 or abs(point[1]) > .5
                    or not .7 <= point[2] <= 1.2):
                raise ValueError('TOOL_COMMISSIONING_VIEW_INVALID')
    return value, hashlib.sha256(raw).hexdigest()


def tool_pose(poses, tool=None):
    if tool is None:
        return poses[-1]
    result = poses[tool['parentLinkIndex']-1].copy()
    result[:3, 3] = (result @ np.r_[tool['offsetM'], 1.])[:3]
    return result
