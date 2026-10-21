"""Bounded geometric routing in explicitly measured free space.

This is an admission planner, not a flight controller or a gait generator. Its
bodyHalfExtents are a *commissioned, world-axis-aligned collision envelope*,
covering the aerial body/rotors at every permitted attitude, or the protected
ground chassis. Ground contact geometry and articulated leg sweeps are additional
controller responsibilities. The controller must stay in the returned corridor
and still check fresh obstacles, curvature, balance and its dynamics.

Unknown voxels, the outside of the survey and tangential contact are blocked.
Terrain is separately measured evidence; a point cloud never implies support.
"""
from __future__ import annotations

import copy
import heapq
import itertools
import math
import threading
from functools import lru_cache

import numpy as np

MAX_VOLUME_CELLS = 250_000
MAX_PATH_WAYPOINTS = 4096
_IDENTITY_FIELDS = ("robotId", "mapId", "mapRevision", "frameId",
                    "worldFrameRevision", "calibrationRevision")
_VOLUME_FIELDS = {"schemaVersion", *_IDENTITY_FIELDS, "resolution", "origin", "shape", "cells"}
_POLICY_FIELDS = {"mode", "bodyHalfExtents", "margin", "maxExpanded", "maxWaypoints"}
_TERRAIN_POLICY_FIELDS = {"maxSlopeRad", "maxStepM", "referenceHeightM"}


class SpatialNavigationError(ValueError):
    """A bounded planner failure, carrying a stable machine-readable reason."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _exact(value, required, *, optional=frozenset(), label):
    if type(value) is not dict or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError(f"{label} fields do not match the versioned contract")


def _number(value, label, *, minimum=None, strict=False, maximum=None):
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be a finite number")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if minimum is not None and (value <= minimum if strict else value < minimum):
        raise ValueError(f"{label} is below its minimum")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} exceeds its maximum")
    return value


def _integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer in [{minimum},{maximum}]")
    return value


def _identifier(value, label):
    if (type(value) is not str or not 1 <= len(value) <= 256
            or any(char.isspace() or ord(char) < 32 for char in value)):
        raise ValueError(f"{label} must be a bounded nonempty identifier")
    return value


def _vector(value, size, label):
    if type(value) is not list or len(value) != size:
        raise ValueError(f"{label} must contain {size} numbers")
    return [_number(item, label) for item in value]


def _pose(value):
    pose = _vector(value, 7, "pose")
    if abs(sum(item * item for item in pose[3:]) - 1.0) > 1e-3:
        raise ValueError("pose quaternion must be normalized, in w,x,y,z order")
    return pose


def validate_volume(volume):
    """Return a fully detached JSON-shaped volume, including measured terrain.

    Storage is x-fastest, then y, then z. A cell is free only for the exact
    integer 0; the contract permits only -1 (unknown), 0 and 100 (occupied).
    A package loader must additionally authenticate mapRevision against the
    immutable package contents, including any terrain evidence.
    """
    _exact(volume, _VOLUME_FIELDS, optional={"terrain"}, label="navigation volume")
    if volume["schemaVersion"] != "navigation.volume.v1" or volume["frameId"] != "world":
        raise ValueError("volume requires navigation.volume.v1 in the world frame")
    result = {"schemaVersion": "navigation.volume.v1"}
    result.update({key: _identifier(volume[key], key) for key in _IDENTITY_FIELDS})
    resolution = _number(volume["resolution"], "resolution", minimum=0, strict=True)
    origin = _vector(volume["origin"], 3, "origin")
    shape = volume["shape"]
    if type(shape) is not list or len(shape) != 3:
        raise ValueError("volume shape must contain nx,ny,nz")
    shape = [_integer(item, "shape", 1, MAX_VOLUME_CELLS) for item in shape]
    count = math.prod(shape)
    if count > MAX_VOLUME_CELLS:
        raise ValueError("volume exceeds the 250000 cell budget")
    if any(not math.isfinite(base + size * resolution) or base + size * resolution <= base
           for base, size in zip(origin, shape, strict=True)):
        raise ValueError("volume dimensions have no finite representable extent")
    cells = volume["cells"]
    if (type(cells) is not list or len(cells) != count
            or any(type(cell) is not int or cell not in (-1, 0, 100) for cell in cells)):
        raise ValueError("volume cells must be flat integer -1,0,100 values matching shape")
    result.update(resolution=resolution, origin=origin, shape=shape, cells=list(cells))
    if "terrain" in volume:
        terrain = volume["terrain"]
        _exact(terrain, {"schemaVersion", "heights", "supported", "slopeRadians", "sourceId",
                         "observedAtUnixMs"}, label="terrain")
        if terrain["schemaVersion"] != "navigation.terrain.v1":
            raise ValueError("terrain schema is unsupported")
        count = shape[0] * shape[1]
        for field in ("heights", "supported", "slopeRadians"):
            if type(terrain[field]) is not list or len(terrain[field]) != count:
                raise ValueError("terrain arrays must match nx*ny")
        detached = {"schemaVersion": "navigation.terrain.v1",
                    "sourceId": _identifier(terrain["sourceId"], "terrain.sourceId"),
                    "observedAtUnixMs": _integer(terrain["observedAtUnixMs"],
                        "terrain.observedAtUnixMs", 1, 9_007_199_254_740_991),
                    "heights": [], "supported": [], "slopeRadians": []}
        for height, supported, slope in zip(terrain["heights"], terrain["supported"],
                                             terrain["slopeRadians"], strict=True):
            if type(supported) is not bool:
                raise ValueError("terrain support must be an explicit boolean measurement")
            if supported:
                height = _number(height, "terrain height", minimum=origin[2],
                                 maximum=origin[2] + shape[2] * resolution)
                slope = _number(slope, "terrain slope", minimum=0, maximum=math.pi / 2)
            elif height is not None or slope is not None:
                raise ValueError("unsupported terrain must have null height and slope")
            detached["heights"].append(height)
            detached["supported"].append(supported)
            detached["slopeRadians"].append(slope)
        result["terrain"] = detached
    return result


def validate_policy(policy):
    """Validate explicit robot envelope and resource limits; never infer a mode."""
    if type(policy) is not dict or policy.get("mode") not in {"volume3d", "terrain25d"}:
        raise ValueError("spatial policy mode must be volume3d or terrain25d")
    terrain_mode = policy["mode"] == "terrain25d"
    _exact(policy, _POLICY_FIELDS | (_TERRAIN_POLICY_FIELDS if terrain_mode else set()),
           label="spatial policy")
    half = _vector(policy["bodyHalfExtents"], 3, "bodyHalfExtents")
    if any(value <= 0 for value in half):
        raise ValueError("every commissioned body half extent must be positive")
    margin = _number(policy["margin"], "margin", minimum=0)
    if any(not math.isfinite(value + margin) for value in half):
        raise ValueError("body envelope plus margin must be finite")
    result = {"mode": policy["mode"], "bodyHalfExtents": half, "margin": margin,
              "maxExpanded": _integer(policy["maxExpanded"], "maxExpanded", 1, MAX_VOLUME_CELLS),
              "maxWaypoints": _integer(policy["maxWaypoints"], "maxWaypoints", 2, MAX_PATH_WAYPOINTS)}
    if terrain_mode:
        result.update(maxSlopeRad=_number(policy["maxSlopeRad"], "maxSlopeRad", minimum=0,
                                          maximum=math.pi / 2 - 1e-6),
                      maxStepM=_number(policy["maxStepM"], "maxStepM", minimum=0),
                      referenceHeightM=_number(policy["referenceHeightM"], "referenceHeightM",
                                                minimum=half[2] + margin, strict=True))
    return result


def _intersects_boxes(start, end, lower, upper):
    """Closed segment versus closed axis-aligned boxes, vectorized slab test."""
    delta = end - start
    enter, leave = np.zeros(len(lower)), np.ones(len(lower))
    for axis in range(len(start)):
        if delta[axis] == 0:
            outside = (start[axis] < lower[:, axis]) | (start[axis] > upper[:, axis])
            enter[outside] = np.inf
        else:
            first, second = (lower[:, axis] - start[axis]) / delta[axis], (upper[:, axis] - start[axis]) / delta[axis]
            enter = np.maximum(enter, np.minimum(first, second))
            leave = np.minimum(leave, np.maximum(first, second))
    return enter <= leave + 1e-12


class SpatialPathPlanner:
    """Deterministic bounded A* with continuous swept-envelope certification.

    Ground endpoint z must agree within 2 cm with its measured surface plus the
    configured referenceHeightM. Both local measured slope and adjacent
    sample height differences are bounded, including beneath the full envelope.
    This intentionally rejects some dynamically traversable steps and ramps.
    A single height per XY cell cannot represent overlapping floors or bridges.
    """

    def __init__(self, volume, policy):
        self._volume = validate_volume(volume)
        self._policy = validate_policy(policy)
        self._terrain_mode = self._policy["mode"] == "terrain25d"
        if self._terrain_mode and "terrain" not in self._volume:
            raise ValueError("terrain25d requires explicit measured terrain support")
        self._origin = np.asarray(self._volume["origin"])
        self._shape = np.asarray(self._volume["shape"], dtype=int)
        self._resolution = self._volume["resolution"]
        self._upper = self._origin + self._shape * self._resolution
        self._envelope = np.asarray(self._policy["bodyHalfExtents"]) + self._policy["margin"]
        self._cells = np.asarray(self._volume["cells"], dtype=np.int16).reshape(tuple(self._shape[::-1]))
        self._terrain = self._volume.get("terrain")
        if self._terrain is not None:
            self._heights = np.asarray(self._terrain["heights"], dtype=float).reshape(tuple(self._shape[1::-1]))
            self._support = np.asarray(self._terrain["supported"], dtype=bool).reshape(self._heights.shape)
            self._slopes = np.asarray(self._terrain["slopeRadians"], dtype=float).reshape(self._heights.shape)
        self._checks = 0
        self._planning_lock = threading.RLock()

        @lru_cache(maxsize=32768)
        def cached_segment(first, second):
            self._checks += 1
            if self._checks > 1_000_000:
                raise SpatialNavigationError("PLANNING_BUDGET_EXCEEDED", "collision check budget exhausted")
            a, b = np.asarray(first), np.asarray(second)
            if np.any(np.minimum(a, b) - self._envelope <= self._origin) or np.any(
                    np.maximum(a, b) + self._envelope >= self._upper):
                return False
            low = np.maximum(np.floor((np.minimum(a, b) - self._envelope - self._origin) / self._resolution).astype(int) - 1, 0)
            high = np.minimum(np.floor((np.maximum(a, b) + self._envelope - self._origin) / self._resolution).astype(int) + 1, self._shape - 1)
            window = self._cells[low[2]:high[2] + 1, low[1]:high[1] + 1, low[0]:high[0] + 1]
            indices = np.argwhere(window != 0)[:, ::-1] + low
            if len(indices):
                lower = self._origin + indices * self._resolution - self._envelope
                upper = self._origin + (indices + 1) * self._resolution + self._envelope
                if np.any(_intersects_boxes(a, b, lower, upper)):
                    return False
            return not self._terrain_mode or self._terrain_clear(a, b, low[:2], high[:2])

        self._cached_segment = cached_segment

    @property
    def volume(self):
        return copy.deepcopy(self._volume)

    @property
    def policy(self):
        return copy.deepcopy(self._policy)

    def _supported_endpoint_height(self, point):
        if not self._terrain_mode:
            return True
        column, row = self._cell(point)
        if not (0 <= column < self._shape[0] and 0 <= row < self._shape[1]
                and self._support[row, column]):
            return False
        expected = self._heights[row, column] + self._policy["referenceHeightM"]
        return abs(point[2] - expected) <= .02

    def _terrain_clear(self, a, b, low, high):
        yy, xx = np.mgrid[low[1]:high[1] + 1, low[0]:high[0] + 1]
        indices = np.stack((xx.ravel(), yy.ravel()), axis=1)
        lower = self._origin[:2] + indices * self._resolution - self._envelope[:2]
        upper = self._origin[:2] + (indices + 1) * self._resolution + self._envelope[:2]
        touched = indices[_intersects_boxes(a[:2], b[:2], lower, upper)]
        x, y = touched[:, 0], touched[:, 1]
        if not np.all(self._support[y, x]) or np.any(self._slopes[y, x] > self._policy["maxSlopeRad"]):
            return False
        max_step = self._policy["maxStepM"]
        covered = set(map(tuple, touched))
        for column, row in covered:
            for dx, dy in ((1, 0), (0, 1)):
                if ((column + dx, row + dy) in covered and
                        abs(self._heights[row, column] - self._heights[row + dy, column + dx]) > max_step + 1e-9):
                    return False
        # Every intersected centreline cell constrains height; checking only
        # endpoints could admit a bridge over an unsupported dip or cliff.
        plain_lower = self._origin[:2] + touched * self._resolution
        plain_upper = plain_lower + self._resolution
        on_line = _intersects_boxes(a[:2], b[:2], plain_lower, plain_upper)
        delta = b[:2] - a[:2]
        height_matched = True
        line_cells = touched[on_line]
        for column, row in line_cells:
            lower = self._origin[:2] + np.asarray([column, row]) * self._resolution
            upper = lower + self._resolution
            enter, leave = 0., 1.
            for axis in range(2):
                if delta[axis] != 0:
                    first, second = (lower[axis] - a[axis]) / delta[axis], (upper[axis] - a[axis]) / delta[axis]
                    enter, leave = max(enter, min(first, second)), min(leave, max(first, second))
            expected = self._heights[row, column] + self._policy["referenceHeightM"]
            errors = [abs(a[2] + fraction * (b[2] - a[2]) - expected)
                      for fraction in (enter, leave)]
            if max(errors) > max_step + 1e-9:
                return False
            height_matched = height_matched and max(errors) <= .02
        if height_matched:
            return True
        # A step is a transition between two neighboring measured support
        # patches, not permission to shortcut above a valley. Retain all such
        # nodes; only an adjacent patch transition may use the step allowance.
        return (len(line_cells) == 2 and sum(abs(line_cells[0] - line_cells[1])) == 1
                and self._supported_endpoint_height(a) and self._supported_endpoint_height(b))

    def segment_clear(self, start, goal):
        """Certify a complete translation, including its measured ground support."""
        start = _vector(list(start), 3, "segment start")
        goal = _vector(list(goal), 3, "segment goal")
        first, second = sorted((tuple(start), tuple(goal)))
        with self._planning_lock:
            return self._cached_segment(first, second)

    def _cell(self, point):
        node = tuple(np.floor((np.asarray(point) - self._origin) / self._resolution).astype(int))
        return node[:2] if self._terrain_mode else node

    def _world(self, node):
        if self._terrain_mode:
            x, y = node
            return [*map(float, self._origin[:2] + (np.asarray(node) + .5) * self._resolution),
                    float(self._heights[y, x] + self._policy["referenceHeightM"])]
        return list(map(float, self._origin + (np.asarray(node) + .5) * self._resolution))

    def _valid(self, node):
        dimensions = 2 if self._terrain_mode else 3
        if any(index < 0 or index >= self._shape[axis] for axis, index in enumerate(node)):
            return False
        if self._terrain_mode and not self._support[node[1], node[0]]:
            return False
        return len(node) == dimensions and self.segment_clear(self._world(node), self._world(node))

    def _connect(self, point):
        node = self._cell(point)
        candidates = (tuple(node[axis] + delta[axis] for axis in range(len(node)))
                      for delta in itertools.product((-1, 0, 1), repeat=len(node)))
        return sorted((candidate for candidate in candidates if self._valid(candidate)
                       and self.segment_clear(point, self._world(candidate))),
                      key=lambda candidate: (math.dist(point, self._world(candidate)), candidate))

    def plan(self, start_pose, goal_pose):
        with self._planning_lock:
            return self._plan(start_pose, goal_pose)

    def _plan(self, start_pose, goal_pose):
        start_pose, goal_pose = _pose(start_pose), _pose(goal_pose)
        start, goal = start_pose[:3], goal_pose[:3]
        # Each call has the same resource budget even when a snapshot is reused.
        self._checks = 0
        self._cached_segment.cache_clear()
        if not self.segment_clear(start, start) or not self._supported_endpoint_height(start):
            raise SpatialNavigationError("LOCALIZATION_NOT_CLEAR", "start lacks measured body clearance or ground support")
        if not self.segment_clear(goal, goal) or not self._supported_endpoint_height(goal):
            raise SpatialNavigationError("GOAL_NOT_CLEAR", "goal lacks measured body clearance or ground support")
        if self.segment_clear(start, goal):
            return self._result([start_pose, goal_pose], 0)
        sources, targets = self._connect(start), set(self._connect(goal))
        if not sources or not targets:
            raise SpatialNavigationError("NO_KNOWN_PATH", "no certified endpoint connector")
        cost = {node: math.dist(start, self._world(node)) for node in sources}
        parent = dict.fromkeys(sources)
        # Euclidean distance to the goal is admissible for the multi-connector
        # search. Goal connectors are included when selecting the final result.
        frontier = [(distance + math.dist(self._world(node), goal), distance, node)
                    for node, distance in cost.items()]
        heapq.heapify(frontier)
        neighbors = ((1, 0), (-1, 0), (0, 1), (0, -1)) if self._terrain_mode else tuple(
            delta for delta in itertools.product((-1, 0, 1), repeat=3) if any(delta))
        expanded, target, best = 0, None, math.inf
        while frontier:
            estimate, distance, node = heapq.heappop(frontier)
            if distance != cost[node]:
                continue
            if estimate >= best:
                break
            if node in targets:
                total = distance + math.dist(self._world(node), goal)
                if total < best:
                    target, best = node, total
                continue
            if expanded >= self._policy["maxExpanded"]:
                raise SpatialNavigationError("PLANNING_BUDGET_EXCEEDED", "expanded node budget exhausted")
            expanded += 1
            for delta in neighbors:
                neighbor = tuple(index + offset for index, offset in zip(node, delta, strict=True))
                if not self._valid(neighbor) or not self.segment_clear(self._world(node), self._world(neighbor)):
                    continue
                candidate = distance + math.dist(self._world(node), self._world(neighbor))
                if candidate < cost.get(neighbor, math.inf):
                    cost[neighbor], parent[neighbor] = candidate, node
                    heapq.heappush(frontier, (candidate + math.dist(self._world(neighbor), goal), candidate, neighbor))
            if len(frontier) > min(MAX_VOLUME_CELLS * 2, max(4096, self._policy["maxExpanded"] * 4)):
                raise SpatialNavigationError("PLANNING_BUDGET_EXCEEDED", "frontier budget exhausted")
        if target is None:
            raise SpatialNavigationError("NO_KNOWN_PATH", "no route through measured free supported space")
        nodes = []
        while target is not None:
            nodes.append(target)
            target = parent[target]
            if len(nodes) + 2 > self._policy["maxWaypoints"]:
                raise SpatialNavigationError("PLANNING_BUDGET_EXCEEDED", "waypoint budget exhausted")
        nodes.reverse()
        waypoints = [start_pose, *[[*self._world(node), *goal_pose[3:]] for node in nodes], goal_pose]
        if any(not self.segment_clear(first[:3], second[:3]) for first, second in itertools.pairwise(waypoints)):
            raise SpatialNavigationError("PATH_CLEARANCE_INSUFFICIENT", "complete path validation failed")
        return self._result(waypoints, expanded)

    def _result(self, waypoints, expanded):
        return {"schemaVersion": "navigation.path.v1", "mode": self._policy["mode"],
                **{key: self._volume[key] for key in _IDENTITY_FIELDS},
                "waypoints": copy.deepcopy(waypoints), "expandedNodes": expanded,
                "clearanceModel": "commissioned_axis_aligned_envelope"}
