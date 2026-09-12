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

HOME_MODEL_PATH = Path(__file__).resolve().parents[1] / "assets" / "xlerobot_home.xml"
HOME_SCENE_REVISION = "home-4room-rgbd-v1"
HOME_TASK_SCENE_REVISION = "home-task-rgbd-mobile-manipulation-v1"
HOME_ROOMS = ("living_room", "home_corridor", "kitchen", "bedroom", "bathroom")
# Stable semantic IDs are adapter-facing catalog entries. Their poses are
# still discovered from the task RGB-D frame at execution time.
HOME_TASK_CUP_POSITION = (1.82, 3.68, 0.85)
HOME_TASK_BIN_POSITION = (2.15, 3.68, 0.77)

HOME_TASK_OBJECTS = (
    ("red-cup", "red_cup", "red_cup_free", "cup", "red"),
    ("blue-cup", "blue_cup", "blue_cup_free", "cup", "blue"),
)
#: Where each task object starts. One table, read by the model builder and the
#: runtime placement, so a scene can never contain an object the catalogue does not
#: advertise, or advertise one the scene does not contain.
HOME_TASK_OBJECT_PLACEMENTS = {
    "red_cup_free": HOME_TASK_CUP_POSITION,
    "blue_cup_free": (1.70, 3.78, 0.85),
}
#: Every object must start inside this box or perception can never see it. It is a
#: sensor-space commissioning limit, not a semantic object lookup.
HOME_TASK_WORK_VOLUME = {"x": (1.05, 3.10), "y": (3.15, 4.65), "z": (0.62, 1.30)}
HOME_TASK_TABLE_CENTER = (2.05, 3.85, 0.40)
_Q = 2 ** -0.5
HOME_WAYPOINTS = {
    "living_room": [0.0, -1.25, 0.035, _Q, 0.0, 0.0, _Q],
    "home_corridor": [0.0, 1.85, 0.035, _Q, 0.0, 0.0, _Q],
    "kitchen": [2.20, 3.35, 0.035, _Q, 0.0, 0.0, _Q],
    "bedroom": [-2.05, 3.35, 0.035, _Q, 0.0, 0.0, _Q],
    "bathroom": [-2.05, 6.55, 0.035, _Q, 0.0, 0.0, _Q],
}
HOME_ROUTE_EDGES = {
    "living_room": ("home_corridor",),
    "home_corridor": ("living_room", "kitchen", "bedroom"),
    "kitchen": ("home_corridor",),
    "bedroom": ("home_corridor", "bathroom"),
    "bathroom": ("bedroom",),
}


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
    required_bodies = ("home_task_table", "red_cup", "kitchen_bin")
    for name in required_bodies:
        if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name) < 0:
            raise ValueError(f"home task scene is missing {name}")
    if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "red_cup_free") < 0:
        raise ValueError("home task scene is missing red_cup_free")


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
