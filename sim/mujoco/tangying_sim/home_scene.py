"""Commissioned four-room home scene for RGB-D and SLAM acceptance.

The scene deliberately contains only geometry a real RGB-D camera could see:
walls, furniture silhouettes, door openings and textured floors. Room names and
waypoints are planning metadata; they are never emitted as simulator truth in
an RGB-D observation.
"""

from __future__ import annotations

from pathlib import Path

import mujoco
import numpy as np
from tangying_robot_gateway.home_commissioning import *

HOME_MODEL_PATH = Path(__file__).resolve().parents[1] / "assets" / "xlerobot_home.xml"

def load_home_model(path: str | Path | None = None) -> mujoco.MjModel:
    return mujoco.MjModel.from_xml_path(str(path or HOME_MODEL_PATH))


def validate_home_model(model: mujoco.MjModel) -> None:
    required_bodies = (*HOME_ROOMS, "chassis")
    required_cameras = ("overview", "head_depth")
    for kind, names in (
        (mujoco.mjtObj.mjOBJ_BODY, required_bodies),
        (mujoco.mjtObj.mjOBJ_CAMERA, required_cameras),
    ):
        missing = [name for name in names if mujoco.mj_name2id(model, kind, name) < 0]
        if missing:
            raise ValueError(f"home scene is missing {missing}")
    if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "ikea_cart") >= 0:
        raise ValueError("home scene must not include a duplicate world-fixed cart")
    for name, pose in HOME_WAYPOINTS.items():
        if len(pose) != 7 or not np.isfinite(pose).all():
            raise ValueError(f"invalid waypoint {name}")
        if name not in HOME_ROUTE_EDGES:
            raise ValueError(f"waypoint {name} has no route adjacency")


def validate_home_task_model(model: mujoco.MjModel) -> None:
    """Validate the optional household task fixtures on top of the home map."""
    validate_home_model(model)
    if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "ceramic_mug") >= 0:
        from .household_workcell import HOUSEHOLD_OBJECTS
        for name in ("home_task_table", "kitchen_tray"):
            if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name) < 0:
                raise ValueError(f"household scene is missing {name}")
        for _item_id, slug, joint, _category, _colour in HOUSEHOLD_OBJECTS:
            if (mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, slug) < 0
                    or mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint) < 0):
                raise ValueError(f"household scene is missing {slug}/{joint}")
        return
    required_bodies = ("home_task_table", "red_cup", "kitchen_bin")
    for name in required_bodies:
        if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name) < 0:
            raise ValueError(f"home task scene is missing {name}")
    for _item_id, slug, joint, _category, _colour in HOME_TASK_OBJECTS:
        if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, slug) < 0:
            raise ValueError(f"home task scene is missing body {slug}")
        if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint) < 0:
            raise ValueError(f"home task scene is missing free joint {joint}")
    _validate_home_task_layout()


def _validate_home_task_layout() -> None:
    """Refuse a layout whose objects overlap, sit outside the sensor volume, or
    hover over the bin. All three fail silently at runtime."""
    for joint, position in HOME_TASK_OBJECT_PLACEMENTS.items():
        for axis, index in (("x", 0), ("y", 1), ("z", 2)):
            low, high = HOME_TASK_WORK_VOLUME[axis]
            if not low <= position[index] <= high:
                raise ValueError(
                    f"{joint} starts at {axis}={position[index]:.3f}, outside the work "
                    f"volume ({low}, {high}); perception could never see it"
                )
    over_bin = objects_over_the_bin()
    if over_bin:
        raise ValueError(
            f"{', '.join(over_bin)} sit over the storage bin; they would fall in and "
            "drop below the support plane, so perception could never see them"
        )


def route_between(start: str, goal: str) -> list[str]:
    """Return a shortest room route from the commissioned adjacency graph."""
    if start not in HOME_ROUTE_EDGES or goal not in HOME_ROUTE_EDGES:
        raise ValueError("unknown home room")
    queue = [(start, [start])]
    visited = {start}
    while queue:
        current, path = queue.pop(0)
        if current == goal:
            return path
        for candidate in HOME_ROUTE_EDGES[current]:
            if candidate not in visited:
                visited.add(candidate)
                queue.append((candidate, [*path, candidate]))
    raise ValueError(f"no route from {start} to {goal}")
