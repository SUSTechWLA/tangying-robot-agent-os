"""Rooms measured from a surveyed grid, with no hand-authored layout.

These tests use small synthetic floor plans rather than a surveyed house, because
the questions that matter are geometric and each one has a plan that isolates it:
does a doorway split, does a room the robot cannot walk to get published as a
destination anyway, does a seed off the edge silently disable the reachability
filter. A real house makes those slower to ask without making them truer.

The one thing a synthetic plan cannot check is whether the answer matches *this*
house - that is the benchmark's job (`scripts/semantic_map_benchmark.py --truth
gazebo`), which validates the world file's own partitions before it scores
anything.

Three conventions worth stating once, because each is easy to get wrong when
reading an assertion:

  * Rows are bottom-up, matching the occupancy convention: row 0 is the lowest y.
  * A published room's area is the **drivable** floor, which is smaller than the
    architectural floor by the robot's envelope margin on every side. A 4 m x 4 m
    plan at the default 0.42 m clearance publishes 10.24 m2, not 16 m2. That is
    deliberate - the difference is floor the robot's base cannot occupy.
  * A doorway "closes" at 2 x (footprint + margin + door_radius), not at 2 x
    door_radius. The whole point of the first test group is that this is easy to
    get wrong and expensive when you do.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import ndimage
from tangying_robot_gateway.room_segmentation import (
    RoomSegmentation,
    clearance_radius_for,
    door_radius_sweep,
    segment_rooms,
)

FREE = 0
OCCUPIED = 100
RESOLUTION = 0.05
FOOTPRINT = 0.32
MARGIN = 0.10
CLEARANCE = clearance_radius_for(FOOTPRINT, MARGIN)


def plan(width_m: float, height_m: float) -> np.ndarray:
    """A fully free plan of the given size, with a solid wall all around it.

    The wall matters: `distance_transform_edt` needs a False cell to measure
    against, and an all-free grid would report infinite clearance everywhere.
    """
    columns = round(width_m / RESOLUTION)
    rows = round(height_m / RESOLUTION)
    cells = np.full((rows + 2, columns + 2), OCCUPIED, dtype=np.int8)
    cells[1:-1, 1:-1] = FREE
    return cells


def divide(cells: np.ndarray, *, at: int, doorway_m: float = 0.0) -> np.ndarray:
    """Put a vertical wall down a column, optionally leaving a centred doorway.

    `doorway_m` is the **geometric** width of the opening, before the robot's
    envelope is taken off it.
    """
    cells[:, at] = OCCUPIED
    if doorway_m > 0:
        gap = round(doorway_m / RESOLUTION)
        middle = cells.shape[0] // 2
        cells[middle - gap // 2: middle + gap // 2 + 1, at] = FREE
    return cells


def segment(cells: np.ndarray, **kwargs) -> RoomSegmentation:
    """Segment with the suite's defaults, letting a test override the envelope."""
    kwargs.setdefault("footprint_radius_m", FOOTPRINT)
    kwargs.setdefault("margin_m", MARGIN)
    return segment_rooms(cells, resolution=RESOLUTION, origin=(0.0, 0.0), **kwargs)


def drivable_floor(width_m: float, height_m: float) -> float:
    """The drivable area of a `plan`, derived from the plan and not from the module.

    A free cell is drivable when nothing occupied lies within the robot's clearance,
    which is `exploration.clearance_mask`'s rule: the radius is rounded **up** to whole
    cells and the test is Euclidean in cell units. Free cells sit at indices 1..n
    inside a one-cell wall, so the distance from a free cell to that wall is
    ``min(i, n + 1 - i)`` cells, and the cell is clear when that exceeds the reach.

    Written out here rather than imported, on purpose: if the module's erosion
    convention changes, this says so instead of quietly agreeing with it.
    """
    reach = math.ceil(CLEARANCE / RESOLUTION)

    def span(n: int) -> int:
        return sum(1 for i in range(1, n + 1) if min(i, n + 1 - i) > reach)

    return (span(round(width_m / RESOLUTION)) * RESOLUTION
            * span(round(height_m / RESOLUTION)) * RESOLUTION)


# --- the compound doorway threshold -----------------------------------------

def test_a_doorway_closes_at_twice_the_sum_of_the_two_erosions():
    """The compound threshold, bracketed on both sides.

    The module docstring claims a doorway of geometric width ``g`` is closed when
    ``g < 2 * (footprint + margin + door_radius)``: 2.04 m nominal at the defaults,
    against the 1.20 m the radius alone suggests. These two openings are 0.2 m either
    side of the measured transition, so the test fails if the erosion ever stops
    compounding - and it will not fail on grid quantisation.
    """
    narrow = segment(divide(plan(8.0, 4.0), at=80, doorway_m=1.80), door_radius_m=0.60)
    wide = segment(divide(plan(8.0, 4.0), at=80, doorway_m=2.20), door_radius_m=0.60)
    assert len(narrow.rooms) == 2, "1.80 m is below the threshold"
    assert len(wide.rooms) == 1, "2.20 m is above the threshold"


def test_the_radius_alone_does_not_decide_a_doorway():
    """A 1.70 m doorway is 2.8x the radius and still closes.

    This is the reading the module docstring exists to prevent: take the radius as
    the threshold and this looks like a bug, because 1.70 > 2 x 0.60.
    """
    cells = divide(plan(8.0, 4.0), at=80, doorway_m=1.70)
    assert len(segment(cells, door_radius_m=0.60).rooms) == 2
    # Widen the opening past the compound threshold and the same radius merges.
    assert len(segment(divide(plan(8.0, 4.0), at=80, doorway_m=2.60),
                       door_radius_m=0.60).rooms) == 1


def test_a_wide_opening_merges_at_a_small_radius_and_splits_at_a_large_one():
    """The knob still means something once the doorway is past the envelope.

    Both radii are on the same 3.40 m opening, so the only thing that changed is the
    parameter under test. The large radius also has to be small enough that each
    room keeps a core of its own, which is why the plan is 8 m wide.
    """
    def rooms_at(radius: float) -> int:
        return len(segment(divide(plan(8.0, 4.0), at=80, doorway_m=3.40),
                           door_radius_m=radius).rooms)

    assert rooms_at(0.30) == 1, "3.40 m opening, erodes to a 2.65 m channel: one room"
    assert rooms_at(1.40) == 2, "the same channel cannot hold a 1.40 m core"


def test_the_transition_is_where_the_measurement_put_it():
    """Pin the measured boundary, quantisation and all.

    The nominal rule says 2.04 m; the grid says 2.00 m, because the channel width is
    quantised to whole cells and the core test is a strict `>`. Recording the real
    number keeps the docstring's "design rule, not a tape measure" claim honest - if
    someone later changes the comparison to `>=`, or the erosion rule, this is where
    that shows up.
    """
    def rooms(doorway_m: float) -> int:
        return len(segment(divide(plan(8.0, 4.0), at=80, doorway_m=doorway_m),
                           door_radius_m=0.60).rooms)

    assert rooms(1.95) == 2
    assert rooms(2.00) == 1


# --- the geometry the split is supposed to get right ------------------------

def test_one_open_room_is_one_room():
    cells = plan(4.0, 4.0)
    rooms = segment(cells, door_radius_m=0.60)
    assert len(rooms.rooms) == 1
    assert rooms.rooms[0].area_m2 == pytest.approx(drivable_floor(4.0, 4.0), abs=0.05)


def test_the_published_area_is_the_drivable_floor_not_the_architectural_one():
    """The room is where the robot can be, not where the walls are.

    Pinning this because the gap is large enough (a 4 m plan loses 36% of its area)
    that a caller assuming architectural area would draw the wrong conclusion about
    how much of the house is usable.
    """
    cells = plan(4.0, 4.0)
    room = segment(cells, door_radius_m=0.60).rooms[0]
    assert room.area_m2 < 16.0
    assert room.area_m2 == pytest.approx(drivable_floor(4.0, 4.0), abs=0.05)
    # Monotone in the envelope: a bigger robot has less floor, never more.
    wider = segment(cells, door_radius_m=0.60, footprint_radius_m=0.60).rooms[0]
    assert wider.area_m2 < room.area_m2


def test_rooms_are_numbered_biggest_first():
    """A comparison that renumbers its subjects between runs cannot be read."""
    cells = divide(divide(plan(14.0, 4.0), at=100, doorway_m=1.70), at=200, doorway_m=1.70)
    rooms = segment(cells, door_radius_m=0.60)
    assert len(rooms.rooms) == 3
    areas = [room.area_m2 for room in rooms.rooms]
    assert areas == sorted(areas, reverse=True)
    assert [room.label for room in rooms.rooms] == [1, 2, 3]


def test_the_pose_is_the_point_furthest_from_the_regions_own_boundary():
    """The third rule, and the two that were measured wrong before it.

    The rule is: of the cells in the region, take the one furthest from *anything
    that is not this region*. Two earlier rules were rejected on the Gazebo house --
    the centroid (an L-shaped room's centroid lies outside it, so the pose is in a
    wall) and the point furthest from the nearest *obstacle* (a region reaching out
    through its doorway is widest at the junction, which put "living room" at the
    corridor mouth 2.4 m from the room it named).

    Only the rule itself is pinned here. The case that separates it from the second
    rejected rule is a doorway junction, which needs a two-room plan with a specific
    topology; the benchmark scores that end to end against the world file instead.
    """
    cells = plan(8.0, 8.0)
    # A step out of the corner, so the region is not a plain rectangle and the
    # centroid is not the deepest point.
    cells[118:, 118:] = OCCUPIED
    segmentation = segment(cells, door_radius_m=0.60)
    assert len(segmentation.rooms) == 1
    room = segmentation.rooms[0]
    x, y = room.centre_xy
    assert segmentation.room_of(x, y) is room, "the pose is not inside its own room"
    assert room.clearance_m > 1.0, "the deepest point of an open room is not near a wall"
    assert segmentation.room_of(6.5, 6.5) is None, "the step is not part of the room"

    inside = ndimage.distance_transform_edt(segmentation.labels == room.label) * RESOLUTION
    assert inside[room.centre_rc] == pytest.approx(inside.max()), (
        "the pose is not the point furthest from the region's own boundary")
    # ... and that is a different point from the centroid and from the corner.
    assert (x, y) != pytest.approx((4.0, 4.0), abs=0.3), "the pose is the bounding-box centre"


# --- what must not be published ---------------------------------------------

def test_a_room_the_robot_cannot_walk_to_is_not_published():
    """The failure this whole layer exists to avoid: destinations nothing can reach."""
    cells = divide(plan(10.0, 4.0), at=100)  # a solid wall, no doorway
    segmentation = segment(cells, door_radius_m=0.60, seed_xy=(0.5, 2.0))
    assert len(segmentation.rooms) == 1
    assert segmentation.rooms[0].centre_xy[0] < 5.0, "published the far side of a solid wall"


def test_a_seed_off_the_edge_of_the_grid_still_filters():
    """An out-of-grid seed must not silently drop the reachability filter.

    The robot's first pose can be off the grid it went on to build, so this input
    happens. Treating it as "no filter requested" publishes the unreachable half of
    the house - the same failure as above, reached by a different door. Only an
    explicit `seed_xy=None` may mean "give me every room".
    """
    cells = divide(plan(10.0, 4.0), at=100)
    filtered = segment(cells, door_radius_m=0.60, seed_xy=(-5.0, -5.0))
    assert len(filtered.rooms) == 1
    assert filtered.rooms[0].centre_xy[0] < 5.0
    unfiltered = segment(cells, door_radius_m=0.60)
    assert len(unfiltered.rooms) == 2, "None is still the documented opt-out"


def test_every_published_pose_is_somewhere_the_envelope_fits():
    cells = divide(plan(8.0, 6.0), at=80, doorway_m=1.70)
    segmentation = segment(cells, door_radius_m=0.60)
    assert len(segmentation.rooms) == 2
    for room in segmentation.rooms:
        x, y = room.centre_xy
        assert segmentation.room_of(x, y) is room
        assert room.clearance_m >= CLEARANCE, (
            "a pose the robot's envelope does not fit in is not a pose it can occupy")


def test_the_area_floor_drops_small_regions_rather_than_padding_them():
    """The floor is a guard against a doorway pocket being published as a room.

    Driven through the parameter rather than through a hand-built pocket, because
    with the default 0.60 m radius a region too small to hold a core cannot exist at
    all - the guard's real job is at smaller radii, and the parameter is the same
    code path.
    """
    cells = divide(plan(8.0, 4.0), at=80, doorway_m=1.70)
    two = segment(cells, door_radius_m=0.60).rooms
    assert len(two) == 2
    smaller = min(room.area_m2 for room in two)
    largest = max(room.area_m2 for room in two)
    # A floor under both keeps both; a floor over both keeps neither. The floor
    # drops regions - it does not merge them into a neighbour or grow them to fit.
    assert len(segment(cells, door_radius_m=0.60,
                       min_room_area_m2=smaller - 0.1).rooms) == 2
    assert segment(cells, door_radius_m=0.60,
                   min_room_area_m2=largest + 0.1).rooms == ()


# --- the documented failure mode --------------------------------------------

def test_no_core_survives_falls_back_to_connected_components_not_to_nothing():
    """An empty semantic layer is not an answer; one room is a worse answer.

    A door radius wider than the plan erodes every core away. The module promises a
    fallback rather than silence, so the caller still gets a region it can route to.
    """
    cells = plan(4.0, 4.0)
    segmentation = segment(cells, door_radius_m=5.0)
    assert len(segmentation.rooms) == 1
    assert segmentation.rooms[0].area_m2 == pytest.approx(drivable_floor(4.0, 4.0), abs=0.05)


def test_a_zero_door_radius_is_refused():
    with pytest.raises(ValueError, match="door radius must be positive"):
        segment(plan(4.0, 4.0), door_radius_m=0.0)


def test_a_bad_grid_or_resolution_is_refused():
    with pytest.raises(ValueError, match="non-empty 2-D"):
        segment(np.zeros((0, 0), dtype=np.int8), door_radius_m=0.60)
    with pytest.raises(ValueError, match="positive resolution"):
        segment_rooms(plan(4.0, 4.0), resolution=0.0, origin=(0.0, 0.0),
                      door_radius_m=0.60)


# --- the frame conversions ---------------------------------------------------

def test_room_of_round_trips_a_published_pose_and_rejects_the_outside():
    cells = plan(6.0, 4.0)
    segmentation = segment_rooms(cells, resolution=RESOLUTION, origin=(-3.0, -2.0),
                                 door_radius_m=0.60, footprint_radius_m=FOOTPRINT,
                                 margin_m=MARGIN)
    assert len(segmentation.rooms) == 1
    room = segmentation.rooms[0]
    assert segmentation.room_of(*room.centre_xy) is room
    assert segmentation.room_of(-100.0, -100.0) is None
    assert segmentation.room_of(100.0, 100.0) is None


def test_a_seed_inside_an_obstacle_snaps_to_the_nearest_cell_that_fits():
    """The robot's own start cell may be one its envelope does not fit in.

    The rule is the route planner's: take the nearest cell the envelope does fit
    in, and segment from there. The wall is thick and the seed sits on its
    left-hand edge, so "nearest" has one answer rather than a tie.
    """
    cells = plan(6.0, 4.0)
    cells[:, 60:70] = OCCUPIED  # a 0.5 m wall: too thick for the envelope, no doorway
    segmentation = segment(cells, door_radius_m=0.60, seed_xy=(3.05, 2.0))
    assert len(segmentation.rooms) == 1, "the wall is not a doorway"
    assert segmentation.rooms[0].centre_xy[0] < 3.0, "snapped to the wrong side of the wall"


# --- the sensitivity helper --------------------------------------------------

def test_the_sweep_returns_one_segmentation_per_radius_and_changes_its_answer():
    """The helper exists because the radius has no ground truth behind it.

    If a sweep returned the same split at every radius, reporting a curve would be
    theatre - so the test asserts the answer actually moves, and returns them in the
    order asked for.
    """
    cells = divide(plan(8.0, 4.0), at=80, doorway_m=3.40)
    radii = (0.30, 0.60, 1.40)
    results = door_radius_sweep(cells, resolution=RESOLUTION, origin=(0.0, 0.0),
                                radii=radii, footprint_radius_m=FOOTPRINT, margin_m=MARGIN)
    assert len(results) == len(radii)
    assert [result.door_radius_m for result in results] == list(radii)
    counts = [len(result.rooms) for result in results]
    assert counts[0] < counts[-1], f"expected the split to coarsen, got {counts}"


def test_clearance_radius_is_the_envelope_plus_a_small_margin():
    assert clearance_radius_for(0.32) == pytest.approx(0.42)
    assert clearance_radius_for(0.32, 0.0) == pytest.approx(0.32)
