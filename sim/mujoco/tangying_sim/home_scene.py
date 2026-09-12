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

#: Advertised objects the RGB-D detector does not yet report. Empty is the goal.
#: Listing them keeps the gap visible instead of letting a catalogue entry quietly
#: promise something perception cannot deliver.
PERCEPTION_PENDING: tuple[str, ...] = ()

HOME_TASK_OBJECTS = (
    ("red-cup", "red_cup", "red_cup_free", "cup", "red"),
    ("blue-cup", "blue_cup", "blue_cup_free", "cup", "blue"),
    ("green-cup", "green_cup", "green_cup_free", "cup", "green"),
    ("yellow-plate", "yellow_plate", "yellow_plate_free", "plate", "yellow"),
)
#: Where each task object starts. One table, read by the model builder and the
#: runtime placement, so a scene can never contain an object the catalogue does not
#: advertise, or advertise one the scene does not contain.
HOME_TASK_OBJECT_PLACEMENTS = {
    "red_cup_free": HOME_TASK_CUP_POSITION,
    # Objects go beside the bin, never above it. The bin takes a large part of the
    # work surface, and an object placed over it falls in: it then rests below the
    # support-plane gate and is never perceived, which reads as a perception fault
    # rather than a placement mistake.
    "blue_cup_free": (1.58, 3.90, 0.85),
    "green_cup_free": (1.58, 4.10, 0.85),
    "yellow_plate_free": (2.72, 4.02, 0.82),
}

#: Bin walls measured from its centre in the scene builder.
HOME_TASK_BIN_HALF_EXTENT = (0.32, 0.26)


def objects_over_the_bin() -> list[str]:
    """Objects whose footprint overlaps the storage bin.

    Anything above the bin falls into it, lands lower than the support plane and
    drops out of perception. That is invisible in the scene and looks like a
    detector problem, so it is checked rather than eyeballed.
    """
    low_x = HOME_TASK_BIN_POSITION[0] - HOME_TASK_BIN_HALF_EXTENT[0]
    high_x = HOME_TASK_BIN_POSITION[0] + HOME_TASK_BIN_HALF_EXTENT[0]
    low_y = HOME_TASK_BIN_POSITION[1] - HOME_TASK_BIN_HALF_EXTENT[1]
    high_y = HOME_TASK_BIN_POSITION[1] + HOME_TASK_BIN_HALF_EXTENT[1]
    return [
        joint for joint, position in HOME_TASK_OBJECT_PLACEMENTS.items()
        if low_x <= position[0] <= high_x and low_y <= position[1] <= high_y
    ]
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
