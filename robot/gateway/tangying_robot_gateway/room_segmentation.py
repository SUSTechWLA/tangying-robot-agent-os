"""Rooms derived from a surveyed occupancy grid, with no hand-authored layout.

Every semantic layer in this repository so far is a *table someone typed*: a room
name, a pose, and a list of aliases, written down for one house and loaded from
JSON. That is exact, cheap to query and impossible to get wrong for the house it
was written for - and it is wrong for every other house, silently, because a table
has no way to notice that the kitchen moved.

This module is the other end of that trade: rooms are *measured*. The surveyed
grid is eroded until the doorways close, what is left are the room cores, and
every free cell is then assigned to the nearest core. Nothing is authored, so a
re-survey after the furniture moves produces rooms that match the new map, and a
different house needs no work at all.

What it cannot do is *name* anything. A measured region is a shape, not a word;
attaching "厨房" to it is a separate act, and this module deliberately stops
short of guessing. See `semantic_workspaces.py` for how a name is attached and
what it costs.

The one free parameter is the erosion radius, and it is not a detail: it is the
widest doorway the segmentation will treat as a wall. It is *not* simply "twice
the radius", because erosion is applied twice - once to fit the robot's envelope,
once to close doorways - and a doorway has to survive both. A doorway of
geometric width `g` is closed when the eroded channel through it is narrower than
the diameter the second erosion needs:

    g  <  2 * (footprint_radius_m + margin_m + door_radius_m)

which with the commissioning defaults (0.32 + 0.10 + 0.60) is a nominal **2.04 m**,
not the 1.20 m the radius alone suggests. Measured on a 0.05 m grid the transition
lands at **1.90 m**, because the channel width is quantised to whole cells and the
core test is a strict `>`; the formula is a design rule, not a number to compare
against a doorway with a tape measure. Getting this wrong is not academic: pick a
radius from the naive rule and every doorway in the house closes, which reads as
"this house has one room". Below the threshold two rooms merge; above roughly half
the narrowest room's width, that room erodes away and disappears. A house whose
rooms and doorways differ in size by less than a factor of two cannot be segmented
this way at all, and the honest answer there is a different representation rather
than a better radius.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = ["Room", "RoomSegmentation", "clearance_radius_for", "segment_rooms"]

#: Obstacle threshold in the occupancy grid. `OCCUPIED` in exploration.py is 65;
#: everything at or above it blocks, everything below is drivable.
OCCUPIED = 65
#: A room core smaller than this is a doorway pocket or a niche, not a room. In
#: square metres, because that is what "a room" means - see the note in
#: `exploration.MIN_FRONTIER_AREA_M2` for what happens when a size threshold is
#: written in cells.
MIN_ROOM_AREA_M2 = 1.0


@dataclass(frozen=True)
class Room:
    """One measured region: a label, its cells, and where the robot should stand."""

    label: int
    area_m2: float
    #: Cell of the region's deepest interior point - the most obstacle-free place
    #: in the room, and therefore the best base pose in it.
    centre_rc: tuple[int, int]
    #: The same point in the grid's own frame, as (x, y, yaw).
    centre_xy: tuple[float, float]
    #: Distance from `centre_rc` to the nearest obstacle, in metres. This is the
    #: room's usable half-width, and it is what decides whether a base with a
    #: safety envelope can stand there at all.
    clearance_m: float

    def to_dict(self) -> dict:
        return {"label": self.label, "areaM2": round(self.area_m2, 4),
                "centre": [round(self.centre_xy[0], 4), round(self.centre_xy[1], 4), 0.0],
                "clearanceM": round(self.clearance_m, 4)}


@dataclass(frozen=True)
class RoomSegmentation:
    """The room a cell belongs to, plus the rooms themselves."""

    #: Per-cell label; 0 means "not part of any room" (obstacle or unreachable).
    labels: np.ndarray
    rooms: tuple[Room, ...]
    resolution: float
    origin: tuple[float, float]
    door_radius_m: float

    def room_of(self, x: float, y: float) -> Room | None:
        """Which room a map-frame point falls in, or ``None`` if it falls in none."""
        column = math.floor((x - self.origin[0]) / self.resolution)
        row = math.floor((y - self.origin[1]) / self.resolution)
        if not (0 <= row < self.labels.shape[0] and 0 <= column < self.labels.shape[1]):
            return None
        label = int(self.labels[row, column])
        return next((room for room in self.rooms if room.label == label), None)


def clearance_radius_for(footprint_radius_m: float, margin_m: float = 0.10) -> float:
    """The erosion radius a commissioned base needs before it counts as inside a room.

    A cell is only somewhere the robot can *be* if a disc the size of its envelope
    fits around it, so the map is eroded by that much before anything is measured.
    The default margin is small on purpose: erosion is applied twice in this module
    (once for drivable space, once to close doorways), and a generous margin
    applied twice closes rooms that are genuinely reachable.
    """
    return float(footprint_radius_m) + float(margin_m)


def _drivable(cells: np.ndarray, resolution: float, radius_m: float) -> np.ndarray:
    """Cells a disc of `radius_m` fits in, by the rule the router navigates by.

    This **must** be the production traversability rule and not a stricter local one.
    ``exploration.traversable_mask`` measures clearance against *occupied* cells only,
    because unmapped space is not an obstacle - it is space nobody has measured, and
    the survey is expected to drive into it. Measuring clearance against "anything
    that is not free" instead folds every unknown cell into the obstacle set, and on a
    real survey grid that is not a small correction:

        surveyed map scan-af0ba496987e, 36% unknown, 0.42 m envelope
            this module's old rule   -> 0 cells reachable from the survey start
            the router's rule        -> 25.7 m², 4 of the 5 rooms

    So the old rule did not merely shrink the rooms, it emptied the map - and the
    semantic layer built on it put every named place outside every published region.
    The lesson is the one this module keeps relearning: a layer that plans on a
    different map than the robot drives on is measuring a house nobody lives in.
    """

    from .exploration import traversable_mask

    values = np.asarray(cells)
    if radius_m <= 0:
        return values == 0
    return traversable_mask(values, radius_m / float(resolution))


def _reachable(drivable: np.ndarray, seed_rc: tuple[int, int] | None) -> np.ndarray:
    """The part of `drivable` connected to the robot.

    Rooms the robot cannot walk to are not rooms it can be sent to, and including
    them would put destinations in the semantic layer that no navigation can
    reach - the failure mode this whole layer exists to avoid.

    ``None`` means the caller did not ask for the filter, and is the only input
    that returns everything. A seed that is *outside* the grid is not the same
    thing: the caller asked and named a point, so it is snapped like any other
    unusable seed. Returning everything there would silently drop the filter - and
    a dropped filter publishes exactly the unreachable destinations this section
    is here to keep out. An out-of-grid seed happens in practice, because the
    robot's first pose can be off the edge of the grid it went on to build.
    """
    from scipy import ndimage

    if seed_rc is None:
        return drivable
    labels, _ = ndimage.label(drivable, structure=np.ones((3, 3), dtype=int))
    row, column = seed_rc
    if not (0 <= row < labels.shape[0] and 0 <= column < labels.shape[1]):
        # Nearest drivable cell to the out-of-grid point, measured over the whole
        # grid rather than clamped at the edge: a corner seed on a map whose
        # drivable space sits in the middle is nearer the middle than the corner.
        # The lookup itself has to be clamped, because the index arrays are the
        # grid's shape and an unclamped index would raise instead of snapping.
        lookup = (min(max(row, 0), labels.shape[0] - 1),
                  min(max(column, 0), labels.shape[1] - 1))
        _, (rows, columns) = ndimage.distance_transform_edt(
            ~drivable, return_indices=True)
        row, column = int(rows[lookup]), int(columns[lookup])
        return labels == labels[row, column]
    home = labels[row, column]
    if home == 0:
        # Standing in a cell the envelope does not fit: take the nearest cell it
        # does, exactly as the route planner opens the robot's own start cell.
        _distance, (rows, columns) = ndimage.distance_transform_edt(
            labels == 0, return_indices=True)
        row, column = int(rows[row, column]), int(columns[row, column])
        home = labels[row, column]
        if home == 0:
            return drivable
    return labels == home


def segment_rooms(cells, *, resolution: float, origin, door_radius_m: float,
                  footprint_radius_m: float = 0.32, margin_m: float = 0.10,
                  seed_xy: tuple[float, float] | None = None,
                  min_room_area_m2: float = MIN_ROOM_AREA_M2) -> RoomSegmentation:
    """Split the drivable space of an occupancy grid into rooms.

    ``door_radius_m`` is the knob that decides how fine the split is: two areas
    joined by a doorway are separated when a disc of this radius does not fit
    *through the drivable channel*, so the geometric doorway width that closes is
    ``2 * (footprint_radius_m + margin_m + door_radius_m)`` - see the module
    docstring, and `tests/test_room_segmentation.py` for the measurement. It is
    stated in metres rather than cells for the same reason `MIN_FRONTIER_AREA_M2`
    is: a cell count means a different physical size on every map, and this one
    has to be compared against real doorways.

    Cells are assigned to the room whose core is nearest *through the drivable
    space* - a plain Euclidean nearest-core assignment would hand a corridor cell
    to a room on the other side of a wall whenever the wall is thinner than the
    detour.
    """
    from scipy import ndimage

    values = np.asarray(cells)
    if values.ndim != 2 or min(values.shape) <= 0:
        raise ValueError("room segmentation needs a non-empty 2-D occupancy grid")
    if not math.isfinite(resolution) or resolution <= 0:
        raise ValueError("room segmentation needs a positive resolution")
    if not math.isfinite(door_radius_m) or door_radius_m <= 0:
        raise ValueError("door radius must be positive; a zero radius splits nothing")

    origin = (float(origin[0]), float(origin[1]))
    seed_rc = None
    if seed_xy is not None:
        seed_rc = (math.floor((seed_xy[1] - origin[1]) / resolution),
                   math.floor((seed_xy[0] - origin[0]) / resolution))

    drivable = _reachable(
        _drivable(values, resolution, clearance_radius_for(footprint_radius_m, margin_m)),
        seed_rc)

    # Erode by the door radius: what survives is room cores, and the gaps between
    # them are the doorways that were narrower than the erosion.
    distance = ndimage.distance_transform_edt(drivable) * resolution
    cores = drivable & (distance > door_radius_m)
    labels, count = ndimage.label(cores, structure=np.ones((3, 3), dtype=int))

    # Assign every drivable cell to its nearest core, measured through free space:
    # a Euclidean assignment crosses walls, this one cannot.
    if count:
        _, indices = ndimage.distance_transform_edt(labels == 0, return_indices=True)
        assigned = np.where(drivable, labels[indices[0], indices[1]], 0)
    else:
        # No core survived. Rather than return an empty layer, fall back to the
        # connected components of the drivable space: one room is a worse answer
        # than several, but an empty semantic layer is not an answer at all.
        assigned, count = ndimage.label(drivable, structure=np.ones((3, 3), dtype=int))

    rooms: list[Room] = []
    floor = min_room_area_m2 / (resolution ** 2)
    for label in range(1, count + 1):
        mask = assigned == label
        area_cells = int(mask.sum())
        if area_cells < floor:
            continue
        # The region's pole of inaccessibility: the point furthest from anything
        # that is *not this room*. Three rules were tried and two were measured
        # wrong on the Gazebo house:
        #
        #   * the centroid - an L-shaped room's centroid lies outside it, and a
        #     base pose there is a base pose in a wall;
        #   * the point furthest from the nearest *obstacle* - a region that reaches
        #     out through its doorway is widest at the junction, so this put
        #     "living room" at (-1.27, +0.28), the corridor mouth, 2.4 m from the
        #     room it names. The doorway is not an obstacle, so this rule cannot see
        #     that it is standing in one;
        #   * restricting to the room core - better, but on an open-plan band the
        #     core still runs through the junction.
        #
        # Distance to the *region boundary* is the rule that works: the boundary
        # includes the doorway, so the farthest point is the middle of the room.
        inside_region = ndimage.distance_transform_edt(mask) * resolution
        row, column = np.unravel_index(int(np.argmax(inside_region)), inside_region.shape)
        rooms.append(Room(
            label=label,
            area_m2=area_cells * resolution ** 2,
            centre_rc=(int(row), int(column)),
            centre_xy=(origin[0] + (column + 0.5) * resolution,
                       origin[1] + (row + 0.5) * resolution),
            clearance_m=float(distance[row, column])))

    rooms.sort(key=lambda room: room.area_m2, reverse=True)
    # Re-label biggest-first so a room's number is stable between two runs over
    # the same map - a comparison that renumbers its subjects cannot be read.
    relabelled = np.zeros_like(assigned)
    kept: list[Room] = []
    for index, room in enumerate(rooms, start=1):
        relabelled[assigned == room.label] = index
        kept.append(Room(label=index, area_m2=room.area_m2, centre_rc=room.centre_rc,
                         centre_xy=room.centre_xy, clearance_m=room.clearance_m))
    return RoomSegmentation(labels=relabelled, rooms=tuple(kept), resolution=resolution,
                            origin=origin, door_radius_m=float(door_radius_m))


def door_radius_sweep(cells, *, resolution: float, origin, radii,
                      **kwargs) -> list[RoomSegmentation]:
    """Segment at several door radii, for the sensitivity measurement.

    The radius is a free parameter with no ground truth behind it, so the honest
    way to report a segmentation is with the curve rather than with one number
    that happened to look right.
    """
    return [segment_rooms(cells, resolution=resolution, origin=origin,
                          door_radius_m=radius, **kwargs) for radius in radii]
