"""Joint-space execution with measured Gazebo joint feedback, never pose writes."""
from __future__ import annotations

import json
import math
import time

from .arm_kinematics import all_links
from .runtime import Result

LIMITS = {link.motor: (link.range_min, link.range_max) for link in all_links()}


def validate_chunk(chunk):
    if not isinstance(chunk, list) or not 1 <= len(chunk) <= 64:
        raise ValueError("action_chunk must contain 1..64 waypoints")
    targets = []
    for waypoint in chunk:
        if not isinstance(waypoint, dict) or not waypoint:
            raise ValueError("empty joint waypoint")
        target = {}
        for key, value in waypoint.items():
            name = key.removesuffix('.pos')
            if not key.endswith('.pos') or name not in LIMITS or isinstance(value, bool):
                raise ValueError("undeclared joint")
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not LIMITS[name][0] <= value <= LIMITS[name][1]:
                raise ValueError("joint target outside radians limits")
            target[name] = float(value)
        targets.append(target)
    return targets


def execute_chunk(node, chunk, cancel, *, clock=time.monotonic, sleep=time.sleep):
    try:
        targets = validate_chunk(chunk)
    except ValueError as error:
        return Result(False, "TOOL_PARAMETERS_INVALID", str(error))
    if not node._command_lock.acquire(blocking=False):
        return Result(False, "ROBOT_BUSY")
    completed = False
    try:
        for waypoint, target in enumerate(targets):
            deadline = clock() + 12.0
            settled = 0
            previous_stamp = 0
            commanded = None
            while clock() < deadline:
                if cancel.is_set() or not node.motion_allowed():
                    return Result(False, "CANCELLED")
                positions, age, stamp = node.joint_snapshot()
                if age > 0.5 or age < 0 or not set(target) <= positions.keys():
                    return Result(False, "JOINT_FEEDBACK_STALE")
                # Maximum commanded advance per 50 ms tick: 0.5 rad/s.
                if commanded is None:
                    commanded = {name: positions[name] for name in target}
                commanded = {name: commanded[name] + max(-.025, min(.025, goal-commanded[name])) for name, goal in target.items()}
                node.send_joint_targets(commanded)
                if stamp != previous_stamp:
                    settled = settled + 1 if all(abs(positions[n]-g) <= .04 for n, g in target.items()) else 0
                    previous_stamp = stamp
                if settled >= 4 and all(abs(commanded[n]-g) < 1e-9 for n, g in target.items()):
                    break
                sleep(.05)
            else:
                return Result(False, "JOINT_TARGET_TIMEOUT", json.dumps({
                    "waypoint": waypoint, "targetRad": target,
                    "measuredRad": {name: positions.get(name) for name in target},
                    "toleranceRad": .04,
                }, sort_keys=True))
        completed = True
        return Result(True)
    finally:
        # On success the controller must keep the requested setpoint. Replacing
        # every joint target with a sagged measurement accumulates gravity error
        # in joints this command never moved, and can push an arm into the table.
        if not completed:
            node.hold_joints()
        node._command_lock.release()
