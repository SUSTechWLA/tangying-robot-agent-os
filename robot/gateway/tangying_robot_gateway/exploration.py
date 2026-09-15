"""Frontier exploration: choose where to look next so a survey finishes in one pass.

A fixed waypoint route cannot know what a room contains, so it always leaves
something unscanned and the operator has to go back. Frontier exploration
answers the question directly instead: from the evidence collected so far,
which reachable piece of known-free space borders the most unknown space?

The pieces here are deliberately pure - a grid in, a decision out - so the
policy can be tested on synthetic maps without a robot, and so the runtime that
executes it stays a thin loop. Three properties matter and each has a test:

* An unknown cell is never planned through. Only measured floor counts as
  traversable, because driving into unmapped space on the strength of a guess is
  how a survey becomes a collision.
* Clearance is the commissioned envelope, not the chassis radius. A path that
  fits the robot but not its safety envelope is a path the driver will refuse,
  and a planner that keeps proposing refused paths wastes the whole survey.
* Travel is spent where it buys information. Gain is the unknown space a
  viewpoint can actually see, discounted by the distance needed to reach it.
"""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass, field

import numpy as np

#: Cell value written by :func:`occupancy_from_points` for observed obstacles.
OCCUPIED = 65
#: A cell whose local unknown fraction is above this is worth looking around in.
DEFAULT_LOOK_THRESHOLD = 0.25
#: How much farther than the nearest frontier a better-informed one may be, as a
#: ratio plus a small absolute allowance so the comparison works at any resolution.
NEAR_TIE_RATIO = 1.25
NEAR_TIE_MARGIN_M = 2.0
#: How much larger a far unexplored region must be before the drive is worth it.
#: Without this, the selector's near-tie band kept preferring whatever fragment
#: of unknown sat closest: a whole unmapped bathroom at the end of the corridor
#: lost to a sliver beside the kitchen, and the survey spent its budget within
#: rooms it had already mapped. Measured on a furnished home: one 22 m leg left
#: the bathroom at 74% unknown with zero trajectory points in it.
FAR_REGION_FACTOR = 2.5
#: Cells blacklisted around a refused viewpoint, in metres.
AVOID_RADIUS_M = 0.3
#: How many frontier fragments one planning step will grade. Each one costs a
#: search, so this is the knob that decides whether a survey plans in
#: milliseconds or appears to freeze on a map that has grown large.
MAX_FRONTIER_CLUSTERS = 12
#: Node expansions a single route search may spend before giving up. A route
#: that needs more than this is longer than any single step of a survey, and
#: searching for it holds up the whole loop.
MAX_SEARCH_EXPANSIONS = 15_000


@dataclass(frozen=True)
class Grid:
    """An occupancy grid plus the geometry needed to move between cells and metres."""

    cells: np.ndarray
    resolution: float
    origin: tuple[float, float]

    def __post_init__(self):
        if self.cells.ndim != 2 or min(self.cells.shape) <= 0:
            raise ValueError("occupancy grid must be a non-empty 2-D array")
        if not math.isfinite(self.resolution) or self.resolution <= 0:
            raise ValueError("occupancy resolution must be positive and finite")
        if len(self.origin) != 2 or not all(math.isfinite(v) for v in self.origin):
            raise ValueError("occupancy origin must be a finite planar point")

    @property
    def shape(self):
        return self.cells.shape

    def column(self, x):
        return math.floor((x - self.origin[0]) / self.resolution)

    def row(self, y):
        return math.floor((y - self.origin[1]) / self.resolution)

    def centre(self, row, column):
        return (self.origin[0] + (column + 0.5) * self.resolution,
                self.origin[1] + (row + 0.5) * self.resolution)

    def inside(self, row, column):
        return 0 <= row < self.cells.shape[0] and 0 <= column < self.cells.shape[1]

    def nearest_free_cell(self, x, y, radius_m=0.6):
        """The closest cell a path may start from, or ``None``.

        The chassis can legitimately stand inside the clearance envelope of a wall
        it was commissioned next to. Refusing to plan at all in that situation
        would strand the robot; starting from the nearest drivable cell and
        letting the driver vet the first metre is the honest alternative.
        """
        reach = max(1, math.ceil(radius_m / self.resolution))
        centre_r, centre_c = self.row(y), self.column(x)
        best, best_distance = None, float("inf")
        for dr in range(-reach, reach + 1):
            for dc in range(-reach, reach + 1):
                r, c = centre_r + dr, centre_c + dc
                if not self.inside(r, c) or self.cells[r, c] != 0:
                    continue
                distance = math.hypot(dr, dc)
                if distance < best_distance:
                    best, best_distance = (r, c), distance
        return best

    def unknown_fraction(self, x, y, radius_m):
        """How much of the disc around a pose is still unknown.

        Used to decide whether standing somewhere and turning is worth its
        keyframes, so it counts unknown cells only: occupied and free are both
        settled knowledge.
        """
        reach = max(1, math.ceil(radius_m / self.resolution))
        row, column = self.row(y), self.column(x)
        r0, r1 = max(0, row - reach), min(self.cells.shape[0], row + reach + 1)
        c0, c1 = max(0, column - reach), min(self.cells.shape[1], column + reach + 1)
        if r0 >= r1 or c0 >= c1:
            return 1.0
        window = self.cells[r0:r1, c0:c1]
        return float(np.count_nonzero(window < 0)) / float(window.size)


def _traversable_with_avoid(grid, radius_m, avoid_xy):
    """Where the robot may be planned, minus the approaches it was refused.

    The exclusion is deliberately smaller than the planning clearance: what the
    driver refused is one approach to one viewpoint, and blacklisting the whole
    neighbourhood of every refusal eventually walls off regions the robot can
    reach by another route.
    """
    traversable = traversable_mask(grid.cells, radius_m / grid.resolution)
    reach = max(1, math.ceil(AVOID_RADIUS_M / grid.resolution))
    for x, y in avoid_xy:
        column, row = grid.column(x), grid.row(y)
        r0, r1 = max(0, row - reach), min(grid.cells.shape[0], row + reach + 1)
        c0, c1 = max(0, column - reach), min(grid.cells.shape[1], column + reach + 1)
        traversable[r0:r1, c0:c1] = False
    return traversable


def plan_route(grid, *, robot_xy, goal_xy, radius_m, avoid_xy=(), extra_traversable=None):
    """Route to an already chosen viewpoint under the same clearance rules."""
    traversable = _traversable_with_avoid(grid, radius_m, avoid_xy)
    if extra_traversable is not None:
        traversable = traversable | extra_traversable
    return plan_path(grid, traversable, robot_xy, goal_xy)


def traversable_for(grid, radius_m, avoid_xy=(), extra_traversable=None):
    """The planning mask the explorer uses, exposed for waypoint validation."""
    traversable = _traversable_with_avoid(grid, radius_m, avoid_xy)
    if extra_traversable is not None:
        traversable = traversable | extra_traversable
    return traversable


def still_open(grid, xy, radius_m):
    """Whether a viewpoint still has unmapped space within its sensor reach.

    A target chosen a few steps ago can be satisfied before it is reached. Asking
    again lets the loop stop early instead of driving to a place that no longer
    has anything to reveal.
    """
    reach = max(1, math.ceil(radius_m / grid.resolution))
    row, column = grid.row(xy[1]), grid.column(xy[0])
    r0, r1 = max(0, row - reach), min(grid.cells.shape[0], row + reach + 1)
    c0, c1 = max(0, column - reach), min(grid.cells.shape[1], column + reach + 1)
    if r0 >= r1 or c0 >= c1:
        return False
    return bool((grid.cells[r0:r1, c0:c1] < 0).any())


def frontier_mask(cells):
    """Known-free cells that touch unknown space.

    This is the classic frontier: the boundary between what has been measured and
    what has not. Occupied cells are not frontiers - a wall is not an invitation.
    """
    free = cells == 0
    unknown = cells < 0
    adjacent = np.zeros_like(unknown)
    adjacent[1:, :] |= unknown[:-1, :]
    adjacent[:-1, :] |= unknown[1:, :]
    adjacent[:, 1:] |= unknown[:, :-1]
    adjacent[:, :-1] |= unknown[:, 1:]
    return free & adjacent


def clearance_mask(cells, radius_cells):
    """Cells at least ``radius_cells`` away from any observed obstacle.

    A square kernel would call a diagonal gap wider than it is, so the distance
    is the true Euclidean one.
    """
    blocked = cells >= OCCUPIED
    if not blocked.any():
        return np.ones_like(blocked)
    reach = max(0, math.ceil(radius_cells))
    if reach == 0:
        return ~blocked
    height, width = blocked.shape
    # The obstacle itself is never clear either, even though the shift loop
    # below only ever considers its neighbours.
    clear = ~blocked
    offsets = [(dr, dc) for dr in range(-reach, reach + 1) for dc in range(-reach, reach + 1)
               if 0 < dr * dr + dc * dc <= reach * reach]
    for dr, dc in offsets:
        shifted = np.zeros_like(blocked)
        r0, r1 = max(0, dr), min(height, height + dr)
        c0, c1 = max(0, dc), min(width, width + dc)
        shifted[r0:r1, c0:c1] = blocked[r0 - dr:r1 - dr, c0 - dc:c1 - dc]
        clear &= ~shifted
    return clear


def traversable_mask(cells, radius_cells):
    """Where the robot may be planned: measured floor with full clearance."""
    return (cells == 0) & clearance_mask(cells, radius_cells)


#: Eight-connected structuring element, the neighbourhood a frontier grows in.
_CONNECTIVITY = np.ones((3, 3), dtype=bool)


def _components(mask):
    """Eight-connected components of a boolean mask, as a label array.

    Labelling runs on every planning step over a grid with thousands of frontier
    cells, so it uses the compiled implementation. A Python flood fill over the
    same mask is correct but slow enough to turn a live survey into a robot that
    appears to have stopped.
    """
    if not mask.any():
        return np.full(mask.shape, -1, dtype=np.int64), 0
    from scipy import ndimage

    labels, count = ndimage.label(mask, structure=_CONNECTIVITY)
    return labels.astype(np.int64) - 1, int(count)


@dataclass(frozen=True)
class Frontier:
    """One connected region of unmapped boundary, and what it is worth."""

    label: int
    cells: int
    centroid: tuple[float, float]
    viewpoint: tuple[float, float] | None = None
    reach_cost_m: float = float("inf")
    gain: int = 0
    path: tuple[tuple[float, float], ...] = field(default=())

    @property
    def region_gain(self):
        """Unknown area this region stands for: its own cells plus the window.

        ``gain`` alone is a local count at the nearest edge, so a room whose
        near edge is a doorway looks tiny from there while an open corner of an
        already-mapped room looks large. The cluster's own cell count is what
        says "there is a whole unmapped room behind this".
        """
        return max(0, int(self.gain)) + max(0, int(self.cells))

    @property
    def utility(self):
        """Information per metre that is not already known.

        A distant frontier that hides a room beats a nearby one that hides a
        cupboard, but not at any price: the half-metre offset keeps a large
        nearby frontier from being skipped for a marginally larger far one.
        """
        if not math.isfinite(self.reach_cost_m) or self.region_gain <= 0:
            return 0.0
        return self.region_gain / (self.reach_cost_m + 0.5)


def _window_sum(integral, row, column, reach):
    """Unknown count in the square window around a cell, in constant time."""
    height, width = integral.shape[0] - 1, integral.shape[1] - 1
    r0, r1 = max(0, row - reach), min(height, row + reach + 1)
    c0, c1 = max(0, column - reach), min(width, column + reach + 1)
    return int(integral[r1, c1] - integral[r0, c1] - integral[r1, c0] + integral[r0, c0])


def _astar(traversable, start, goal):
    """Shortest eight-connected path between two cells, or ``None``."""
    height, width = traversable.shape
    if not (traversable[start] and traversable[goal]):
        return None
    if start == goal:
        return [start]
    costs = np.full((height, width), np.inf)
    costs[start] = 0.0
    previous = {}
    queue = [(0.0, start)]
    expansions = 0
    while queue:
        expansions += 1
        if expansions > MAX_SEARCH_EXPANSIONS:
            return None
        estimate, cell = heapq.heappop(queue)
        if cell == goal:
            path = [cell]
            while path[-1] in previous:
                path.append(previous[path[-1]])
            return path[::-1]
        if estimate > costs[cell] + math.hypot(goal[0] - cell[0], goal[1] - cell[1]):
            continue
        row, column = cell
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if not (dr or dc):
                    continue
                r, c = row + dr, column + dc
                if not (0 <= r < height and 0 <= c < width) or not traversable[r, c]:
                    continue
                step = math.sqrt(2) if dr and dc else 1.0
                # A diagonal step that clips a blocked corner is a collision the
                # grid can see and the driver would refuse.
                if dr and dc and not (traversable[row, c] and traversable[r, column]):
                    continue
                candidate = costs[row, column] + step
                if candidate + 1e-9 < costs[r, c]:
                    costs[r, c] = candidate
                    previous[(r, c)] = cell
                    heapq.heappush(queue, (candidate + math.hypot(goal[0] - r, goal[1] - c), (r, c)))
    return None


def plan_path(grid, traversable, start_xy, goal_xy):
    """A drivable polyline in metres from ``start_xy`` to ``goal_xy``.

    Returns the path and its length in metres, or ``(None, inf)`` when no
    measured-and-cleared route exists. Planning through unknown cells is never
    offered: the route a survey drives has to be evidenced before it is driven.
    """
    start = grid.nearest_free_cell(*start_xy)
    goal = grid.nearest_free_cell(*goal_xy)
    if start is None or goal is None:
        return None, float("inf")
    if not traversable[start]:
        # The robot may legitimately be standing inside the planner's clearance
        # envelope of a wall it was driven alongside: the driver let it get
        # there. It may start from that cell - it just may not stay, so only the
        # cell itself is opened and every step out of it still has to clear.
        traversable = traversable.copy()
        traversable[start] = True
    if not traversable[goal]:
        goal = _nearest_traversable(grid, traversable, goal, radius_m=0.5)
        if goal is None:
            return None, float("inf")
    cells = _astar(traversable, start, goal)
    if not cells:
        return None, float("inf")
    step = grid.resolution
    length = sum(step * (math.sqrt(2) if (b[0] - a[0]) and (b[1] - a[1]) else 1.0)
                 for a, b in itertools.pairwise(cells))
    return [grid.centre(r, c) for r, c in cells], length


def _nearest_traversable(grid, traversable, cell, radius_m=0.6):
    reach = max(1, math.ceil(radius_m / grid.resolution))
    row, column = cell
    best, best_distance = None, float("inf")
    for dr in range(-reach, reach + 1):
        for dc in range(-reach, reach + 1):
            r, c = row + dr, column + dc
            if not grid.inside(r, c) or not traversable[r, c]:
                continue
            distance = math.hypot(dr, dc)
            if distance < best_distance:
                best, best_distance = (r, c), distance
    return best


def explore_target(grid, *, robot_xy, sensor_radius_m, radius_m,
                   min_frontier_cells=6, candidates=10, avoid_xy=(), blind=None,
                   extra_traversable=None):
    """The frontier worth driving to next, or ``None`` when the survey is done.

    ``None`` is a real answer, not a failure: it means every remaining unknown
    cell is either unreachable through measured free space or not worth the
    travel, which is exactly the condition for calling a map complete.

    ``avoid_xy`` carries viewpoints the driver already refused. A refusal is
    local evidence about a specific approach, so the survey routes around it and
    keeps going instead of either retrying or giving up on the house.

    ``blind`` marks the space the sensor has already proved it cannot see from
    where the robot has been - for a forward-facing base camera, the floor
    around and behind its own path. Those cells stay unknown in the map and
    always will, so counting them as frontier makes a survey chase its own
    footprint instead of the rooms.

    ``extra_traversable`` is the robot's own footprint. The camera's near limit
    starts about half a metre ahead of the chassis, so at the very first pose
    the measured floor and the robot's own cell are separated by an unmeasured
    strip; without bridging it the planner sees the robot on an island and
    reports a fully explored house before it has moved.
    """
    cells = grid.cells
    frontier = frontier_mask(cells)
    if blind is not None:
        frontier = frontier & ~blind
    if not frontier.any():
        return None
    labels, count = _components(frontier)
    if not count:
        return None
    traversable = _traversable_with_avoid(grid, radius_m, avoid_xy)
    if extra_traversable is not None:
        traversable = traversable | extra_traversable
    unknown = (cells < 0).astype(np.int64)
    integral = np.zeros((unknown.shape[0] + 1, unknown.shape[1] + 1), dtype=np.int64)
    np.cumsum(np.cumsum(unknown, axis=0), axis=1, out=integral[1:, 1:])
    reach = max(1, math.ceil(sensor_radius_m / grid.resolution))

    sizes = np.bincount(labels[frontier].ravel(), minlength=count)
    start = grid.nearest_free_cell(*robot_xy)
    if start is None:
        return None
    # A frontier mask is mostly a long ragged edge, so it labels as hundreds of
    # fragments. Scanning the whole grid once per fragment is what made a live
    # survey look like a stopped robot; bounding boxes keep the cost proportional
    # to the fragments actually considered.
    biggest = [int(label) for label in np.argsort(sizes)[::-1][:MAX_FRONTIER_CLUSTERS]
               if sizes[label] >= min_frontier_cells]
    if not biggest:
        return None
    from scipy import ndimage

    # find_objects numbers objects from 1 and returns one slice per object, so
    # shifting the 0-based cluster labels lines index ``label`` up with cluster
    # ``label``.
    boxes = ndimage.find_objects(labels + 1)
    # Rank by what the drive costs, and let information content decide only
    # among places that are about equally far. Weighting gain against distance
    # outright made the survey sweep back and forth across a room - each pose
    # promoted whichever end of the frontier it had just left - and a survey
    # that keeps crossing its own path never finishes the far corners.
    ranked = []
    for label in biggest:
        window = boxes[label]
        if window is None:
            continue
        rows, columns = np.nonzero(labels[window] == label)
        rows = rows + window[0].start
        columns = columns + window[1].start
        # Score the cluster by its best single viewpoint rather than by its
        # centroid: a frontier that curves around a corner has a centroid inside
        # the wall it curves around. The sample has to span the whole cluster -
        # stepping by n//24 crowds it at one end and hides the rest, which is why
        # a survey used to keep revisiting the near edge of a frontier it had
        # already driven to.
        graded = []
        for index in np.linspace(0, len(rows) - 1, num=min(24, len(rows))).astype(int):
            row, column = int(rows[index]), int(columns[index])
            viewpoint = _nearest_traversable(grid, traversable, (row, column), radius_m=0.6)
            if viewpoint is None:
                continue
            distance = math.hypot(viewpoint[0] - start[0], viewpoint[1] - start[1])
            graded.append((distance, viewpoint))
        if not graded:
            continue
        graded.sort(key=lambda item: item[0])
        # The representative viewpoint is the nearest one, not the most
        # informative one: the frontier is the edge of the unknown, so the next
        # thing worth measuring is always the closest part of it. Taking the
        # highest-gain cell instead let a five-metre room put its representative
        # twenty metres away and look unreachable.
        viewpoints = [item[1] for item in graded[:3]]
        gain = _window_sum(integral, viewpoints[0][0], viewpoints[0][1], reach)
        ranked.append((graded[0][0], label, int(sizes[label]), viewpoints, gain, (rows, columns)))
    ranked.sort(key=lambda item: item[0])
    if not ranked:
        return None
    # The band is anchored to the nearest cluster that can actually be planned,
    # not to the nearest cluster at all. Anchoring it to distance alone let one
    # near-but-unreachable fragment - a sliver of unknown behind furniture, whose
    # viewpoint has no route - cap the search radius, so every reachable frontier
    # beyond it was skipped and a survey of a 28%-unknown house ended by
    # reporting "no reachable frontier". Measured on a finished leg: the three
    # nearest fragments were unplannable at 1.7 m, 3.2 m and 3.2 m while a
    # plannable one sat at 7.6 m, just outside the band the first of them set.
    band = None
    in_band = None
    #: The largest reachable region anywhere, by unknown area - not by utility.
    #: Utility divides by distance, so it always favours the near fragment; the
    #: whole point of the override is that a big far region can still win.
    largest = None
    for _distance, label, size, viewpoints, gain, (rows, columns) in ranked[:candidates]:
        if band is not None and _distance > band:
            # The near-tie band is settled; the loop still walks the remaining
            # ranked clusters so a genuinely larger region can win on its size.
            break_out = True
        else:
            break_out = False
        for viewpoint in viewpoints:
            path, length = plan_path(grid, traversable, robot_xy, grid.centre(*viewpoint))
            if path is None:
                continue
            if band is None:
                band = _distance * NEAR_TIE_RATIO + NEAR_TIE_MARGIN_M / grid.resolution
            candidate = Frontier(label=label, cells=size,
                                 centroid=grid.centre(int(rows.mean()), int(columns.mean())),
                                 viewpoint=grid.centre(*viewpoint), reach_cost_m=length,
                                 gain=gain, path=tuple(path))
            if largest is None or candidate.region_gain > largest.region_gain:
                largest = candidate
            if not break_out and (in_band is None or candidate.utility > in_band.utility):
                in_band = candidate
            break
        if break_out:
            continue
    # A far region must be clearly larger, not merely larger: the near-tie band
    # exists so the survey does not cross the house for a marginal gain, and the
    # factor keeps that guarantee while letting an unmapped room win.
    if (in_band is not None and largest is not None and largest is not in_band
            and largest.region_gain >= FAR_REGION_FACTOR * in_band.region_gain):
        return largest
    return in_band or largest


def coverage_report(cells):
    """How much of the map is settled, and how much is still open."""
    total = int(cells.size)
    unknown = int(np.count_nonzero(cells < 0))
    occupied = int(np.count_nonzero(cells >= OCCUPIED))
    return {"cells": total, "unknown": unknown, "occupied": occupied,
            "free": total - unknown - occupied,
            "unknownFraction": round(unknown / total, 6) if total else 1.0}


def next_waypoint(grid, path, *, lookahead_m, traversable=None):
    """The point on a path to drive at now, at most ``lookahead_m`` away.

    Driving the whole path open loop would plan against a map that the drive
    itself keeps changing, so the loop takes one short step and re-plans.

    With ``traversable`` the step also has to be reachable in a straight line.
    A robot drives at a point, not along a polyline, so aiming half a metre
    ahead on a route that just turned a corner asks it to cut that corner - and
    the driver refuses, because the corner it would cut is exactly the clearance
    the route existed to preserve.
    """
    if not path:
        return None
    start = path[0]
    chosen = start
    for point in path[1:]:
        if math.hypot(point[0] - start[0], point[1] - start[1]) > lookahead_m:
            break
        if traversable is not None and not _straight_line_clear(grid, start, point, traversable):
            break
        chosen = point
    if chosen == start and len(path) > 1:
        # The very next cell is the only step available; take it, because the
        # alternative is standing still until the leg times out.
        return path[1]
    return chosen


def _straight_line_clear(grid, start, end, traversable):
    """Whether every cell a straight drive would cross is plannable."""
    steps = max(1, int(math.hypot(end[0] - start[0], end[1] - start[1])
                       / (grid.resolution * 0.5)))
    for index in range(steps + 1):
        fraction = index / steps
        x = start[0] + (end[0] - start[0]) * fraction
        y = start[1] + (end[1] - start[1]) * fraction
        row, column = grid.row(y), grid.column(x)
        if not grid.inside(row, column) or not traversable[row, column]:
            return False
    return True


def shorten_path(path, *, spacing_m):
    """Thin a route down to the turning points worth reporting."""
    if not path or spacing_m <= 0:
        return list(path or ())
    kept = [path[0]]
    for point in path[1:-1]:
        if math.hypot(point[0] - kept[-1][0], point[1] - kept[-1][1]) >= spacing_m:
            kept.append(point)
    if len(path) > 1:
        kept.append(path[-1])
    return kept
