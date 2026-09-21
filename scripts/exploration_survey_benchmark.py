"""Closed-loop survey benchmark: how much of a house does one pass actually map?

Why this exists
---------------
`explore_target` picks one target at a time, and every previous attempt to improve
it was argued from a single decision on a frozen grid. That cannot answer the only
question that matters - *if the robot drives this policy to exhaustion, how much of
the house ends up on the map* - because a survey is a closed loop: where it goes
next depends on what it has already seen, and the last 20% of a house is reached by
a route that only exists once the first 80% has been measured.

So this harness runs the loop. Ground truth is the MuJoCo house, rasterised from its
own geometry, so the answer does not depend on any scan the policies produced.

What is measured
----------------
* ``mapped``  - truth cells the survey measured, over the cells it *could* have
  measured. The denominator is not "all cells": a floor cell sealed inside a
  cupboard is invisible to every reachable pose, and no policy can be blamed for
  it. ``coverable`` is the union of what is visible from some legal pose on the
  ground truth, computed once.
* ``travel``  - metres driven. Coverage bought with distance is not free.
* ``decisions`` - planning steps, a proxy for how much the policy thrashes.
* ``stopReason`` - *why* it stopped. "It stopped" and "it finished" are different
  answers to "did this policy cover the house", and a bare percentage cannot tell
  them apart.
* ``uncovered`` - the coverable space left behind, split into connected pieces. A
  few large pieces are places the policy never went (a selection failure);
  hundreds of crumbs are the gaps between the sensor's rays (a mapping limit, and
  no policy closes them). The two need different fixes, and one percentage hides
  which is which.

Adding a strategy is one line in ``_strategy_kwargs``. Both variants tried so far
lost to the baseline and were removed with their numbers recorded in
``docs/experiments/2026-09-19-exploration-importance-and-coverage.md`` - this
harness exists to make that verdict cheap, not to accumulate options.

Run::

    python scripts/exploration_survey_benchmark.py
    python scripts/exploration_survey_benchmark.py --strategy baseline
"""

from __future__ import annotations

import argparse
import math
import os
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot" / "gateway"))
sys.path.insert(0, str(ROOT / "sim" / "mujoco"))

from tangying_robot_gateway.exploration import (
    OCCUPIED,
    Grid,
    clearance_mask,
    explore_target,
    frontier_mask,
    plan_route,
)

#: The robot's commissioned envelope, matching the reference stack.
CLEARANCE_M = 0.32
#: How far the base RGB-D settles the map, matching EXPLORATION["sensorRadiusM"].
SENSOR_RADIUS_M = 3.0
#: Half-angle of what the sensor sees. The real camera is forward-facing; an
#: omnidirectional model would make target selection look far better than it is.
SENSOR_HALF_ANGLE = math.radians(60.0)
#: Space inside this radius of the traversed path is never measured: a forward
#: camera on a base cannot see the floor it is standing on.
BLIND_RADIUS_M = 1.0
RESOLUTION = 0.05


def build_truth(*, width_m: float = 13.0, height_m: float = 14.0) -> tuple[np.ndarray, float, tuple[float, float]]:
    """Rasterise the MuJoCo house at the robot's height band.

    Returns the occupied mask plus the frame it lives in. Only geometry that
    crosses the band between the floor and the robot's head counts: a ceiling and a
    floor plane are not obstacles to a base, and a wall is.
    """
    import mujoco

    os.environ.setdefault("TANGYING_HOME_ASSET_PACK",
                          str(ROOT / "artifacts" / "sim-assets" / "furnished-home"))
    from tangying_sim.rgbd_navigation import load_navigation_model

    model = load_navigation_model(scene="home_task")
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    origin = (-6.0, -4.0)
    shape = (int(height_m / RESOLUTION), int(width_m / RESOLUTION))
    occupied = np.zeros(shape, dtype=bool)

    def mark(points: np.ndarray) -> None:
        columns = ((points[:, 0] - origin[0]) / RESOLUTION).astype(int)
        rows = ((points[:, 1] - origin[1]) / RESOLUTION).astype(int)
        keep = (rows >= 0) & (rows < shape[0]) & (columns >= 0) & (columns < shape[1])
        occupied[rows[keep], columns[keep]] = True

    # The robot is in the same model as the house, so its own chassis, base plate
    # and standoffs would rasterise as obstacles and wall the survey in at its
    # starting pose. Robot bodies are the chassis and everything parented under it,
    # which is the same rule the navigation controller uses for its self filter.
    chassis = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "chassis")
    robot_bodies = {chassis}
    for body in range(model.nbody):
        if int(model.body_parentid[body]) in robot_bodies:
            robot_bodies.add(body)

    band = (0.03, 1.2)
    for geom in range(model.ngeom):
        kind = int(model.geom_type[geom])
        if kind == 0:  # a floor plane is not an obstacle
            continue
        if int(model.geom_bodyid[geom]) in robot_bodies:
            continue
        position = data.geom_xpos[geom].copy()
        rotation = data.geom_xmat[geom].reshape(3, 3)
        size = model.geom_size[geom]
        if kind == 6:  # box: sample its volume, not its corners
            local = np.stack(np.meshgrid(
                np.linspace(-size[0], size[0], max(3, int(4 * size[0] / RESOLUTION))),
                np.linspace(-size[1], size[1], max(3, int(4 * size[1] / RESOLUTION))),
                np.linspace(-size[2], size[2], max(2, int(2 * size[2] / RESOLUTION))),
                indexing="ij"), axis=-1).reshape(-1, 3)
            points = (rotation @ local.T).T + position
        elif kind == 5:  # cylinder
            angles = np.linspace(0.0, 2.0 * np.pi, 32)
            local = np.stack([
                np.repeat(size[0] * np.cos(angles), 3),
                np.repeat(size[0] * np.sin(angles), 3),
                np.tile(np.linspace(-size[1], size[1], 3), angles.size),
            ], axis=-1)
            points = (rotation @ local.T).T + position
        elif kind == 7:  # mesh
            mesh = int(model.geom_dataid[geom])
            start = int(model.mesh_vertadr[mesh])
            count = int(model.mesh_vertnum[mesh])
            points = (rotation @ model.mesh_vert[start:start + count].T).T + position
        else:
            continue
        inside = (points[:, 2] >= band[0]) & (points[:, 2] <= band[1])
        if int(inside.sum()) < 3:
            continue
        mark(points[inside])
    return occupied, RESOLUTION, origin


def build_gazebo_truth(world: pathlib.Path | None = None):
    """Rasterise the Gazebo house at the robot's height band.

    The same band, the same resolution and the same rule about the robot's own
    body as :func:`build_truth` - the two truth sources exist to be *compared*, and
    a comparison between two different rasterisers measures the rasterisers.

    Read straight out of the SDF rather than from a running simulator: ground truth
    that required a simulator would make the policy benchmark depend on the stack
    whose behaviour it is supposed to be independent of, and the numbers would then
    move whenever the stack did.
    """
    from xml.etree import ElementTree

    if world is None:
        world = (ROOT / "robot" / "ros2_ws" / "src" / "tangying_navigation"
                 / "worlds" / "tangying_home.sdf")
    if not world.is_file():
        raise SystemExit(f"Gazebo world not found: {world}")
    tree = ElementTree.parse(world)
    origin = (-7.0, -4.0)
    shape = (int(12.0 / RESOLUTION), int(14.0 / RESOLUTION))
    occupied = np.zeros(shape, dtype=bool)

    def mark(points: np.ndarray) -> None:
        columns = ((points[:, 0] - origin[0]) / RESOLUTION).astype(int)
        rows = ((points[:, 1] - origin[1]) / RESOLUTION).astype(int)
        keep = (rows >= 0) & (rows < shape[0]) & (columns >= 0) & (columns < shape[1])
        occupied[rows[keep], columns[keep]] = True

    band = (0.03, 1.2)
    for model in tree.iter("model"):
        # The robot is in the same world as the house, so its own chassis would
        # rasterise as an obstacle and wall the survey in at its starting pose.
        # Exactly the rule the MuJoCo rasteriser applies to bodies under `chassis`.
        if "robot" in (model.get("name") or ""):
            continue
        if (model.findtext("static") or "").strip().lower() != "true":
            continue
        pose = [float(value) for value in (model.findtext("pose") or "0 0 0 0 0 0").split()]
        while len(pose) < 6:
            pose.append(0.0)
        cx, cy, cz, roll, pitch, yaw = pose[:6]
        cr, sr = math.cos(roll), math.sin(roll)
        cp, sp = math.cos(pitch), math.sin(pitch)
        cy_, sy_ = math.cos(yaw), math.sin(yaw)
        rotation = np.array([
            [cy_ * cp, cy_ * sp * sr - sy_ * cr, cy_ * sp * cr + sy_ * sr],
            [sy_ * cp, sy_ * sp * sr + cy_ * cr, sy_ * sp * cr - cy_ * sr],
            [-sp, cp * sr, cp * cr],
        ])
        centre = np.array([cx, cy, cz])
        for geometry in model.iter("geometry"):
            interior = next(iter(geometry)) if len(geometry) else None
            if interior is None:
                continue
            kind, size = interior.tag, [float(v) for v in (interior.findtext("size") or "").split()]
            if kind == "box" and len(size) == 3:
                local = np.stack(np.meshgrid(
                    np.linspace(-size[0] / 2, size[0] / 2, max(3, int(2 * size[0] / RESOLUTION))),
                    np.linspace(-size[1] / 2, size[1] / 2, max(3, int(2 * size[1] / RESOLUTION))),
                    np.linspace(-size[2] / 2, size[2] / 2, max(2, int(size[2] / RESOLUTION))),
                    indexing="ij"), axis=-1).reshape(-1, 3)
            elif kind == "cylinder" and len(size) == 2:
                angles = np.linspace(0.0, 2.0 * np.pi, 32)
                local = np.stack([
                    np.repeat(size[0] * np.cos(angles), 3),
                    np.repeat(size[0] * np.sin(angles), 3),
                    np.tile(np.linspace(-size[1] / 2, size[1] / 2, 3), angles.size),
                ], axis=-1)
            elif kind == "sphere" and len(size) == 1:
                angles = np.linspace(0.0, 2.0 * np.pi, 24)
                local = np.stack([
                    np.repeat(size[0] * np.cos(angles), 3),
                    np.repeat(size[0] * np.sin(angles), 3),
                    np.tile(np.linspace(-size[0], size[0], 3), angles.size),
                ], axis=-1)
            else:
                continue
            points = (rotation @ local.T).T + centre
            inside = (points[:, 2] >= band[0]) & (points[:, 2] <= band[1])
            if int(inside.sum()) < 3:
                continue
            mark(points[inside])
    return occupied, RESOLUTION, origin


def gazebo_start_xy(world: pathlib.Path | None = None) -> tuple[float, float]:
    """Where the Gazebo robot is placed, read from the world rather than assumed."""
    from xml.etree import ElementTree

    if world is None:
        world = (ROOT / "robot" / "ros2_ws" / "src" / "tangying_navigation"
                 / "worlds" / "tangying_home.sdf")
    for model in ElementTree.parse(world).iter("model"):
        if "robot" in (model.get("name") or ""):
            pose = [float(value) for value in (model.findtext("pose") or "").split()]
            return pose[0], pose[1]
    raise SystemExit(f"no robot model in {world}")


def reachable_free(occupied: np.ndarray, start_row: int, start_column: int) -> np.ndarray:
    """Floor the robot can actually get to on the ground truth, with clearance."""
    from scipy import ndimage

    open_space = ~occupied
    open_space &= clearance_mask(np.where(occupied, OCCUPIED, 0).astype(np.int16),
                                 math.ceil(CLEARANCE_M / RESOLUTION))
    labels, _ = ndimage.label(open_space, structure=np.ones((3, 3), dtype=bool))
    if not (0 <= start_row < labels.shape[0] and 0 <= start_column < labels.shape[1]):
        return np.zeros_like(open_space)
    component = labels[start_row, start_column]
    if component == 0:
        return np.zeros_like(open_space)
    return labels == component


def visible_from(occupied: np.ndarray, row: int, column: int,
                 reach_cells: int, half_angle: float, heading: float,
                 density: float = 1.0, salt: int = 0) -> np.ndarray:
    """Truth cells a sensor at this pose settles, as a boolean mask.

    Ray-cast rather than a disc: a wall hides the room behind it, and a policy that
    is graded as if walls were transparent is being graded on the wrong problem.
    """
    height, width = occupied.shape
    directions = np.linspace(heading - half_angle, heading + half_angle, 61)
    seen = np.zeros((height, width), dtype=bool)
    steps = np.arange(1, reach_cells + 1, dtype=float)
    for angle in directions:
        dr = np.sin(angle) * steps
        dc = np.cos(angle) * steps
        rows = np.rint(row + dr).astype(int)
        columns = np.rint(column + dc).astype(int)
        for r, c in zip(rows, columns):
            if not (0 <= r < height and 0 <= c < width):
                break
            if density >= 1.0:
                seen[r, c] = True
            else:
                # A real RGB-D frame is a decimated point cloud, not a filled
                # depth image: a cell becomes known only where a point landed in
                # it. This is the difference between a simulator that grades the
                # policy and one that grades a robot nobody has.
                hashed = (r * 73856093) ^ (c * 19349663) ^ (salt * 83492791)
                if (hashed % 1000) / 1000.0 < density:
                    seen[r, c] = True
            if occupied[r, c]:
                break
    return seen


def coverable_cells(occupied: np.ndarray, reachable: np.ndarray,
                    headings: int = 8) -> np.ndarray:
    """The most any survey could map: whatever some legal pose can see.

    The ceiling matters because "covered 62% of the house" is meaningless when a
    third of the house is sealed behind furniture. Grading against this instead
    answers the question a person actually asks - did it map what was there to be
    mapped.
    """
    reach_cells = math.ceil(SENSOR_RADIUS_M / RESOLUTION)
    rows, columns = np.nonzero(reachable)
    total = np.zeros(occupied.shape, dtype=bool)
    step = max(1, len(rows) // 4000)
    for row, column in zip(rows[::step], columns[::step]):
        for index in range(headings):
            total |= visible_from(occupied, int(row), int(column), reach_cells,
                                  SENSOR_HALF_ANGLE, 2.0 * math.pi * index / headings)
    return total


class Survey:
    """One closed-loop run of a policy against the ground truth."""

    def __init__(self, occupied: np.ndarray, reachable: np.ndarray,
                 origin: tuple[float, float], start_rc: tuple[int, int],
                 density: float = 1.0, blind_radius_m: float | None = None,
                 blind_model: str = "disc"):
        self.density = float(density)
        self.blind_radius_m = BLIND_RADIUS_M if blind_radius_m is None else float(blind_radius_m)
        self.blind_model = blind_model
        self._salt = 0
        self.occupied = occupied
        self.reachable = reachable
        self.origin = origin
        self.start = start_rc
        self.known = np.full(occupied.shape, -1, dtype=np.int16)
        self.row, self.column = start_rc
        self.heading = 0.0
        self.travel = 0.0
        self.decisions = 0
        self.legs = 0
        self.trail: list[tuple[float, float]] = []
        self._observe()

    # -- geometry ---------------------------------------------------------

    def _xy(self, row: int, column: int) -> tuple[float, float]:
        return (self.origin[0] + (column + 0.5) * RESOLUTION,
                self.origin[1] + (row + 0.5) * RESOLUTION)

    def _rc(self, x: float, y: float) -> tuple[int, int]:
        return (int((y - self.origin[1]) / RESOLUTION), int((x - self.origin[0]) / RESOLUTION))

    def grid(self) -> Grid:
        return Grid(cells=self.known, resolution=RESOLUTION, origin=self.origin)

    def _blind_disc(self) -> np.ndarray:
        """Space within the blind radius of every pose the robot has stood at.

        This is the shipped rule, and on its own it is wrong in one direction: it
        treats "too close to a pose" as "invisible to the whole survey". A doorway
        the robot drives past is struck off as a frontier forever, so on a
        corridor-and-rooms layout the rooms are never entered at all.
        """
        from tangying_robot_gateway.exploration import traversable_mask as _unused  # noqa: F401
        mask = np.zeros(self.known.shape, dtype=bool)
        radius = self.blind_radius_m
        reach = math.ceil(radius / RESOLUTION)
        offsets = np.arange(-reach, reach + 1)
        disc = (((offsets[None, :] * RESOLUTION) ** 2 + (offsets[:, None] * RESOLUTION) ** 2)
                <= radius ** 2)
        for x, y in self.trail:
            row, column = self._rc(x, y)
            r0, r1 = max(0, row - reach), min(mask.shape[0], row + reach + 1)
            c0, c1 = max(0, column - reach), min(mask.shape[1], column + reach + 1)
            if r0 >= r1 or c0 >= c1:
                continue
            mask[r0:r1, c0:c1] |= disc[r0 - (row - reach):r1 - (row - reach),
                                      c0 - (column - reach):c1 - (column - reach)]
        return mask

    def blind(self) -> np.ndarray:
        """Space the forward camera has already proved it cannot measure.

        Two models, and the difference between them is the whole finding:

        * ``disc`` is the shipped rule - a disc of the blind radius around every
          pose the robot has stood at. It is a reasonable statement about *one
          pose* and a false one about a *survey*: a cell is only unmeasurable from
          poses that are too close to it, and after driving on, the same cell is
          plainly visible from three metres back.
        * ``visibility`` keeps the disc's intent - do not drive at your own
          footprint - but withdraws a cell from it as soon as some pose on the
          trail, far enough back to see it, actually has line of sight to it. That
          is the same ray-cast the sensor model already uses, so the two agree by
          construction rather than by tuning.
        """
        mask = self._blind_disc()
        if self.blind_model != "visibility" or not mask.any():
            return mask
        # Only poses at least the blind radius from a cell can see it, so sampling
        # the trail at that spacing loses nothing and bounds the cost.
        spacing = max(1, round(self.blind_radius_m / RESOLUTION))
        reach = math.ceil(SENSOR_RADIUS_M / RESOLUTION)
        for index in range(0, len(self.trail), spacing):
            x, y = self.trail[index]
            row, column = self._rc(x, y)
            seen = visible_from(self.known >= OCCUPIED, row, column, reach,
                                 heading=0.0, half_angle=math.pi,
                                 density=1.0, salt=0)
            # A cell the pose is too close to is not evidence about the cell; it is
            # exactly what the disc already covers. Subtract only what a *far*
            # pose settles.
            near = math.ceil(self.blind_radius_m / RESOLUTION)
            if near > 0:
                offsets = np.arange(-near, near + 1)
                disc = (((offsets[None, :] * RESOLUTION) ** 2
                         + (offsets[:, None] * RESOLUTION) ** 2)
                        <= self.blind_radius_m ** 2)
                r0, r1 = max(0, row - near), min(seen.shape[0], row + near + 1)
                c0, c1 = max(0, column - near), min(seen.shape[1], column + near + 1)
                if r0 < r1 and c0 < c1:
                    seen[r0:r1, c0:c1] &= ~disc[r0 - (row - near):r1 - (row - near),
                                                c0 - (column - near):c1 - (column - near)]
            mask &= ~seen
        return mask

    # -- the robot ---------------------------------------------------------

    def _observe(self) -> None:
        """Measure what the sensor settles from here, and remember the pose."""
        reach = math.ceil(SENSOR_RADIUS_M / RESOLUTION)
        self._salt += 1
        seen = visible_from(self.occupied, self.row, self.column, reach,
                            SENSOR_HALF_ANGLE, self.heading,
                            density=self.density, salt=self._salt)
        # Only truth the robot could reach is part of the map it is building; the
        # interior of a wall is not free space it failed to notice.
        self.known[seen] = np.where(self.occupied[seen], OCCUPIED, 0)
        self.trail.append(self._xy(self.row, self.column))

    def drive(self, path: list[tuple[float, float]]) -> bool:
        """Follow a planned polyline one cell at a time, measuring as it goes."""
        if len(path) < 2:
            return False
        moved = False
        for point in path[1:]:
            row, column = self._rc(*point)
            if not (0 <= row < self.known.shape[0] and 0 <= column < self.known.shape[1]):
                break
            if self.occupied[row, column]:
                break  # the ground truth says a wall is here; stop rather than pass through
            step = math.dist(self._xy(self.row, self.column), self._xy(row, column))
            if step > 0:
                self.heading = math.atan2(row - self.row, column - self.column)
            self.travel += step
            self.row, self.column = row, column
            moved = True
            self._observe()
            self._certify_trail()
        return moved

    def _certify_trail(self) -> None:
        """Mark the footprint the robot has physically occupied as free.

        This is not a convenience. A decimated point cloud leaves gaps, so without
        it the cells under and beside the robot stay unknown, the free space it has
        already driven through is not connected to the free space it is heading
        for, and the planner refuses a corridor the robot is standing in. The real
        workflow does exactly this - ``_observed_travel`` widens the driven trail by
        the planning clearance - and a benchmark that omits it does not measure the
        real robot; it measures one that cannot move.
        """
        reach = math.ceil(CLEARANCE_M / RESOLUTION)
        r0, r1 = max(0, self.row - reach), min(self.known.shape[0], self.row + reach + 1)
        c0, c1 = max(0, self.column - reach), min(self.known.shape[1], self.column + reach + 1)
        rows, columns = np.mgrid[r0:r1, c0:c1]
        disc = (((rows - self.row) * RESOLUTION) ** 2
                + ((columns - self.column) * RESOLUTION) ** 2) <= CLEARANCE_M ** 2
        window = self.known[r0:r1, c0:c1]
        window[disc & (window < 0)] = 0

    def look_around(self, *, sweeps: int) -> None:
        """Turn on the spot at a new target.

        ``sweeps`` is not decoration. The real workflow only spends turns when the
        local unknown fraction is above ``EXPLORATION["lookThreshold"]``, and it
        caps how many sweeps it will do. A benchmark that always sweeps four ways
        measures a robot nobody has: it is the difference between 98.7% and the
        ~62% a real survey actually reaches.
        """
        for index in range(max(0, sweeps)):
            self.heading = 2.0 * math.pi * index / max(1, sweeps)
            self._observe()

    def local_unknown(self, radius_m: float = SENSOR_RADIUS_M) -> float:
        reach = math.ceil(radius_m / RESOLUTION)
        r0, r1 = max(0, self.row - reach), min(self.known.shape[0], self.row + reach + 1)
        c0, c1 = max(0, self.column - reach), min(self.known.shape[1], self.column + reach + 1)
        window = self.known[r0:r1, c0:c1]
        return float((window < 0).mean()) if window.size else 0.0


#: Local unknown fraction above which the real workflow spends turns on the spot.
LOOK_THRESHOLD = 0.25
#: Ceiling on those sweeps per target, and the floor the real workflow applies.
LOOK_SWEEPS_MAX = 4
LOOK_SWEEPS_MIN = 2


def uncovered_regions(final_unknown: np.ndarray, coverable: np.ndarray,
                      *, smallest: int = 8) -> tuple[int, int, int]:
    """The leftover coverable space, and whether it is rooms or crumbs.

    A coverage percentage cannot say *what kind* of space a policy missed, and the
    fix is different for each: a handful of large components are regions the robot
    never got to (a selection failure), while hundreds of tiny ones are unmeasured
    slivers between the sensor's rays (a mapping-limit failure, and no amount of
    exploring closes them). Reporting both keeps a policy from being blamed for
    the second and credited for the first.
    """
    from scipy import ndimage

    missed = final_unknown & coverable
    if not missed.any():
        return 0, 0, 0
    labels, count = ndimage.label(missed, structure=np.ones((3, 3), dtype=bool))
    sizes = np.bincount(labels.ravel())[1:]
    return int(missed.sum()), count, int((sizes >= smallest).sum())


def run_survey(strategy: str, *, occupied, reachable, origin, start_rc,
               max_decisions: int = 400, realism: str = "real",
               density: float = 1.0, max_clusters: int | None = None,
               blind_radius_m: float | None = None,
               blind_model: str = "disc",
               min_frontier_area_m2: float | None = None) -> dict:
    """Drive a policy until it has no target left, and report what it mapped."""
    survey = Survey(occupied, reachable, origin, start_rc, density=density,
                    blind_radius_m=blind_radius_m, blind_model=blind_model)
    blind_history = np.zeros(occupied.shape, dtype=bool)
    stop_reason = "decision_cap"
    for _ in range(max_decisions):
        grid = survey.grid()
        blind = blind_history | survey.blind()
        pose = survey._xy(survey.row, survey.column)
        self_mask = np.zeros(grid.cells.shape, dtype=bool)
        r0, r1 = max(0, survey.row - 6), min(grid.cells.shape[0], survey.row + 7)
        c0, c1 = max(0, survey.column - 6), min(grid.cells.shape[1], survey.column + 7)
        self_mask[r0:r1, c0:c1] = True
        survey.decisions += 1
        target = explore_target(
            grid, robot_xy=pose, sensor_radius_m=SENSOR_RADIUS_M, radius_m=CLEARANCE_M,
            **({} if min_frontier_area_m2 is None
               else {"min_frontier_area_m2": min_frontier_area_m2}),
            blind=blind, extra_traversable=self_mask,
            **({} if max_clusters is None else {"max_clusters": max_clusters}),
            **_strategy_kwargs(strategy))
        if target is None:
            # The loop's own completion condition, reported so "it stopped" can be
            # told apart from "it finished": the policy hands back ``None`` both
            # when nothing is left and when nothing left is reachable.
            open_frontier = frontier_mask(grid.cells) & ~blind
            stop_reason = "policy_complete" if not open_frontier.any() else "policy_no_reachable"
            break
        path, _length = plan_route(grid, robot_xy=pose, goal_xy=target.viewpoint,
                                   radius_m=CLEARANCE_M, extra_traversable=self_mask)
        if path is None:
            stop_reason = "no_route"
            break
        survey.legs += 1
        if not survey.drive(path):
            stop_reason = "blocked_on_truth"
            break
        if realism == "ideal":
            survey.look_around(sweeps=4)
        else:
            # The real rule: sweep only where it is worth it, within a cap.
            unknown = survey.local_unknown()
            if unknown >= LOOK_THRESHOLD:
                sweeps = LOOK_SWEEPS_MIN + round((LOOK_SWEEPS_MAX - LOOK_SWEEPS_MIN)
                                                 * min(1.0, unknown))
                survey.look_around(sweeps=sweeps)
        blind_history |= survey.blind()
    mapped = int(((survey.known >= 0) & (reachable | occupied)).sum())
    return {"strategy": strategy, "mapped": mapped, "travel": survey.travel,
            "decisions": survey.decisions, "legs": survey.legs,
            "known": survey.known, "trail": survey.trail, "stopReason": stop_reason}


def _strategy_kwargs(strategy: str) -> dict:
    """Strategy switches handed to ``explore_target``.

    Kept as keywords rather than subclasses so every variant runs the same code
    path and differs only in the decision it makes - a benchmark is only worth
    reading if the thing under test is the only thing that changed. Adding a
    strategy is a line here plus the keyword it sets, which is the whole point of
    the exploration layer being a pure function with named switches.

    Two variants that lived here were measured and removed rather than kept:
    ranking a region on the unknown space its viewpoint can actually *see* scored
    93.1% against the window sum's 99.0%, and subtracting the already-visible
    unknown from the window sum changed no decision at all (identical coverage,
    travel and leg count) while making the run four times slower. Both are
    recorded with their numbers in
    ``docs/experiments/2026-09-19-exploration-importance-and-coverage.md``; a
    strategy that does not survive this harness should not sit in it looking like
    an option.
    """
    if strategy in {"baseline", "reach"}:
        return {}
    raise SystemExit(f"unknown strategy {strategy!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strategy", nargs="+", default=["baseline", "reach"])
    parser.add_argument("--realism", choices=("real", "ideal"), default="real",
                        help="real: sweep only where the real workflow would")
    parser.add_argument("--density", type=float, default=1.0,
                        help="fraction of ray cells a point cloud actually lands in")
    parser.add_argument("--min-frontier-area-m2", type=float, default=None,
                        help="the shipped floor, stated the way the policy states "
                             "it: a physical area, so it means the same thing at any "
                             "grid resolution; default is MIN_FRONTIER_AREA_M2")
    parser.add_argument("--min-frontier-cells", type=int, default=None,
                        help="a frontier fragment smaller than this is a corner of a "
                             "known room, not a room; expressed in cells at this "
                             "harness's resolution, default is the shipped area")
    parser.add_argument("--blind-model", choices=("disc", "visibility"), default="disc",
                        help="disc: the shipped rule, a permanent band around the "
                             "trail; visibility: withdraw a cell once a far enough "
                             "pose on the trail can actually see it")
    parser.add_argument("--blind-radius", type=float, default=None,
                        help="radius around the driven path the forward camera is "
                             "assumed never to measure; default is BLIND_RADIUS_M")
    parser.add_argument("--max-frontier-clusters", type=int, default=None,
                        help="frontier fragments one planning step will grade; "
                             "default is the shipped MAX_FRONTIER_CLUSTERS")
    parser.add_argument("--truth", choices=("mujoco", "gazebo"), default="mujoco",
                        help="which simulator's geometry is the ground truth")
    parser.add_argument("--cache", type=pathlib.Path,
                        default=pathlib.Path("artifacts/benchmark/survey-truth.npz"))
    args = parser.parse_args()
    cache = args.cache if args.truth == "mujoco" else args.cache.with_name(
        args.cache.stem + "-gazebo" + args.cache.suffix)

    if cache.is_file():
        saved = np.load(cache)
        occupied = saved["occupied"]
        origin = tuple(float(v) for v in saved["origin"])
        start_xy = tuple(float(v) for v in saved["start"]) if "start" in saved else (0.0, 0.0)
        print(f"真值来自缓存 {cache}")
    else:
        started = time.perf_counter()
        if args.truth == "gazebo":
            occupied, _resolution, origin = build_gazebo_truth()
            start_xy = gazebo_start_xy()
        else:
            occupied, _resolution, origin = build_truth()
            start_xy = (0.0, 0.0)
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache, occupied=occupied, origin=np.array(origin),
                            start=np.array(start_xy))
        print(f"真值栅格化完成 {time.perf_counter() - started:.1f}s，占用 {int(occupied.sum())} 格")

    start_rc = (int((start_xy[1] - origin[1]) / RESOLUTION), int((start_xy[0] - origin[0]) / RESOLUTION))
    reachable = reachable_free(occupied, *start_rc)
    print(f"真值来源: {args.truth}   起点 {tuple(round(v, 2) for v in start_xy)}")
    print(f"从起点可达的地面 {int(reachable.sum())} 格（起点栅格 {start_rc}）")
    if not reachable.any():
        print("起点不可达，无法评测", file=sys.stderr)
        return 2

    ceiling = coverable_cells(occupied, reachable)
    coverable = ceiling & (reachable | occupied)
    denominator = int(coverable.sum())
    print(f"任何政策的上限（可达姿态能看到的真值格）: {denominator} 格\n")

    rows = []
    for strategy in args.strategy:
        started = time.perf_counter()
        result = run_survey(strategy, occupied=occupied, reachable=reachable,
                            origin=origin, start_rc=start_rc, realism=args.realism,
                            density=args.density, max_clusters=args.max_frontier_clusters,
                            blind_radius_m=args.blind_radius,
                            blind_model=args.blind_model,
                            min_frontier_area_m2=(
                                args.min_frontier_area_m2 if args.min_frontier_area_m2 is not None
                                else None if args.min_frontier_cells is None
                                else args.min_frontier_cells * RESOLUTION ** 2))
        elapsed = time.perf_counter() - started
        coverage = 100.0 * result["mapped"] / max(1, denominator)
        missed, regions, big = uncovered_regions(result["known"] < 0, coverable)
        rows.append((strategy, coverage, result["travel"], result["legs"],
                     result["decisions"], elapsed, result["stopReason"], missed,
                     regions, big))
        print(f"{strategy:16s} d={args.density:<4} 覆盖 {coverage:5.1f}%  行程 {result['travel']:6.1f} m  "
              f"{result['legs']:3d} 段  {result['decisions']:4d} 次决策  {elapsed:5.1f}s")
        print(f"{'':16s} 停止原因 {result['stopReason']}  漏掉 {missed} 格 "
              f"分属 {regions} 个连通块（其中 {big} 块 ≥8 格）")

    print("\n策略             覆盖率   行程(m)  段数  决策  漏掉(格) 大块  停止原因")
    for (strategy, coverage, travel, legs, decisions, _elapsed, reason, missed,
         regions, big) in sorted(rows, key=lambda item: -item[1]):
        print(f"{strategy:16s} {coverage:6.1f}%  {travel:7.1f}  {legs:4d}  {decisions:5d}  "
              f"{missed:8d} {big:4d}  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
