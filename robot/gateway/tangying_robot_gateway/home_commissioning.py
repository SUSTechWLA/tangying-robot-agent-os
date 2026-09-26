"""Commissioned four-room home scene for RGB-D and SLAM acceptance.

The scene deliberately contains only geometry a real RGB-D camera could see:
walls, furniture silhouettes, door openings and textured floors. Room names and
waypoints are planning metadata; they are never emitted as simulator truth in
an RGB-D observation.
"""

from __future__ import annotations

#: Default base-camera vertical FOV for the reference simulation.
#: The furnished home head camera has its own 70-degree vertical FOV; deployed
#: drivers publish measured CameraInfo and never infer both cameras from this constant.
RGBD_VERTICAL_FOV_DEG = 58

HOME_SCENE_REVISION = "home-4room-rgbd-v2"
HOME_TASK_SCENE_REVISION = "home-task-rgbd-mobile-manipulation-v2"
HOME_ROOMS = ("living_room", "home_corridor", "kitchen", "bedroom", "bathroom")
# Stable semantic IDs are adapter-facing catalog entries. Their poses are
# still discovered from the task RGB-D frame at execution time.
HOME_TASK_CUP_POSITION = (2.275, 3.415, 0.85)
HOME_TASK_BIN_POSITION = (2.46, 3.335, 0.71)

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
    # The red cup and bin share the front edge of the station so the right arm can
    # reach both without moving the base. The remaining objects sit farther back,
    # clear of that transfer lane and inside the D435i's commissioned field of view.
    "red_cup_free": HOME_TASK_CUP_POSITION,
    "blue_cup_free": (2.80, 3.65, 0.85),
    "green_cup_free": (2.50, 3.85, 0.85),
    "yellow_plate_free": (2.86, 4.02, 0.82),
}

#: The compact parts tray admits the 9 cm cup with a real 5 mm wall and enough
#: fore-aft settling room for a vertical gripper retreat. Its complete collision
#: footprint remains outside the commissioned 40 cm chassis circle.
HOME_TASK_BIN_HALF_EXTENT = (0.07, 0.075)
HOME_TASK_BIN_SURFACE_HALF_EXTENT = (0.06, 0.065, 0.02)
HOME_TASK_BIN_WALL_THICKNESS = 0.005
HOME_TASK_BIN_WALL_HEIGHT = 0.06


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
HOME_TASK_WORK_VOLUME = {"x": (1.05, 3.10), "y": (3.30, 4.65), "z": (0.62, 1.30)}
HOME_TASK_TABLE_CENTER = (2.6625, 3.76, 0.40)
HOME_TASK_TABLE_HALF_EXTENT = (0.4375, 0.395, 0.33)
HOME_TASK_BIN_SHELF_CENTER = (2.75, 3.31, 0.36)
HOME_TASK_BIN_SHELF_HALF_EXTENT = (0.35, 0.06, 0.33)
_Q = 2 ** -0.5
HOME_WAYPOINTS = {
    "living_room": [0.0, -1.25, 0.035, _Q, 0.0, 0.0, _Q],
    "home_corridor": [0.0, 1.85, 0.035, _Q, 0.0, 0.0, _Q],
    # This dock sits 0.405 m from the task table's real corner, and the arm's
    # reach is what fixes that: 0.472 m to the cup and 0.529 m to the bin are the
    # workcell's verified limits, so moving the dock 0.04 m further out already
    # fails the place step and 0.06 m fails the pick. The driver's clearance
    # guard is therefore the commissioned one (measured CAD envelope 0.305 m plus
    # a 0.05 m margin), which leaves this pose 0.05 m of margin instead of the
    # 4.8 mm a flat 0.40 m guard allowed.
    "kitchen": [2.05, 3.00, 0.035, 0.7581022795354195, 0.0, 0.0, 0.6521356712856614],
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



HOUSEHOLD_DIMENSIONS = {"ceramic-mug": (.09,.09,.12), "dinnerware": (.2769131927,.1595688553,.1876213837), "ceramic-vase": (.0716437912,.0971251488,.2413838895)}
HOUSEHOLD_ACTION_CATALOG = ({"id":"ceramic-mug","category":"cup","attributes":{},"confidence":1.,"workArea":"kitchen"}, {"id":"kitchen-tray","category":"storage_bin","attributes":{},"confidence":1.,"workArea":"kitchen"})

# Simulation driver limits; physical profiles must declare their commissioned limits.
HOME_DRIVE_LIMITS = {"maxLinearMps": .20, "maxAngularRps": .50, "surveyLinearMps": .15}
