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
MAX_TERRAIN_LAYERS = 32
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
    _exact(volume, _VOLUME_FIELDS, optional={"terrain", "terrainLayers"}, label="navigation volume")
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
    if "terrainLayers" in volume:
        result["terrainLayers"] = _validate_terrain_layers(volume["terrainLayers"], shape,
                                                          origin, resolution)
    return result


def _validate_terrain_layers(terrain, shape, origin, resolution):
    """Versioned, measured surfaces and explicit adjacent-patch transitions.

    Layer labels are topology, not vertical order. Merely being close in XYZ
    never creates a cross-layer edge; a connection is separate measured evidence.
    It still requires all commissioned geometry checks at planning time.
    """
    _exact(terrain, {"schemaVersion", "sourceId", "observedAtUnixMs", "layers", "connections"},
           label="layered terrain")
    if terrain["schemaVersion"] != "navigation.terrain.layers.v1":
        raise ValueError("layered terrain schema is unsupported")
    count = shape[0] * shape[1]
    layers = terrain["layers"]
    if (type(layers) is not list or not 1 <= len(layers) <= MAX_TERRAIN_LAYERS
            or count * len(layers) > MAX_VOLUME_CELLS):
        raise ValueError("layered terrain exceeds layer or patch-slot budget")
    result = {"schemaVersion": "navigation.terrain.layers.v1",
              "sourceId": _identifier(terrain["sourceId"], "layered terrain sourceId"),
              "observedAtUnixMs": _integer(terrain["observedAtUnixMs"],
                    "layered terrain observedAtUnixMs", 1, 9_007_199_254_740_991),
              "layers": [], "connections": []}
    by_id = {}
    for layer in layers:
        _exact(layer, {"layerId", "heights", "supported", "slopeRadians"}, label="terrain layer")
        layer_id = _identifier(layer["layerId"], "terrain layerId")
        if layer_id in by_id:
            raise ValueError("terrain layerId must be unique")
        if any(type(layer[key]) is not list or len(layer[key]) != count
               for key in ("heights", "supported", "slopeRadians")):
            raise ValueError("terrain layer arrays must match nx*ny")
        detached = {"layerId": layer_id, "heights": [], "supported": [], "slopeRadians": []}
        for height, supported, slope in zip(layer["heights"], layer["supported"],
                                             layer["slopeRadians"], strict=True):
            if type(supported) is not bool:
                raise ValueError("layer support must be an explicit boolean measurement")
            if supported:
                height = _number(height, "layer height", minimum=origin[2],
                                 maximum=origin[2] + shape[2] * resolution)
                slope = _number(slope, "layer slope", minimum=0, maximum=math.pi / 2)
            elif height is not None or slope is not None:
                raise ValueError("unsupported layer must have null height and slope")
            detached["heights"].append(height)
            detached["supported"].append(supported)
            detached["slopeRadians"].append(slope)
        result["layers"].append(detached)
        by_id[layer_id] = detached
    connections = terrain["connections"]
    if type(connections) is not list or len(connections) > MAX_VOLUME_CELLS:
        raise ValueError("layer connection budget exceeded")
    seen = set()
    for connection in connections:
        _exact(connection, {"from", "to"}, label="layer connection")
        endpoints = []
        for endpoint in (connection["from"], connection["to"]):
            if type(endpoint) is not list or len(endpoint) != 3:
                raise ValueError("layer connection endpoint requires layerId,x,y")
            layer_id = _identifier(endpoint[0], "connection layerId")
            if layer_id not in by_id:
                raise ValueError("layer connection references an unknown layer")
            x = _integer(endpoint[1], "connection x", 0, shape[0]-1)
            y = _integer(endpoint[2], "connection y", 0, shape[1]-1)
            if not by_id[layer_id]["supported"][y * shape[0] + x]:
                raise ValueError("layer connection requires measured support at both endpoints")
            endpoints.append((layer_id, x, y))
        first, second = endpoints
        if first[0] == second[0] or abs(first[1]-second[1]) + abs(first[2]-second[2]) != 1:
            raise ValueError("cross-layer connections must join adjacent XY patches, never vertical portals")
        key = tuple(sorted(endpoints))
        if key in seen:
            raise ValueError("duplicate bidirectional layer connection")
        seen.add(key)
        result["connections"].append({"from": list(first), "to": list(second)})
    return result


def validate_policy(policy):
    """Validate explicit robot envelope and resource limits; never infer a mode."""
    if type(policy) is not dict or policy.get("mode") not in {"volume3d", "terrain25d", "terrain_layered"}:
        raise ValueError("spatial policy mode must be volume3d, terrain25d or terrain_layered")
    terrain_mode = policy["mode"] != "volume3d"
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
    terrain25d is a single height per XY cell. terrain_layered keeps overlapping
    measured surfaces separate and crosses them only at explicit, adjacent-patch
    connections; a connection is never a teleport or permission to fly a chassis.
    """

    def __init__(self, volume, policy):
        self._volume = validate_volume(volume)
        self._policy = validate_policy(policy)
        self._layered_mode = self._policy["mode"] == "terrain_layered"
        self._terrain_mode = self._policy["mode"] != "volume3d"
        if self._terrain_mode and not self._layered_mode and "terrain" not in self._volume:
            raise ValueError("terrain25d requires explicit measured terrain support")
        if self._layered_mode and "terrainLayers" not in self._volume:
            raise ValueError("terrain_layered requires explicit measured terrainLayers")
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
        self._layer_connections = {}
        if self._layered_mode:
            layered = self._volume["terrainLayers"]
            self._layers = layered["layers"]
            layer_shape = tuple(self._shape[1::-1])
            self._layer_heights = [np.asarray(layer["heights"], dtype=float).reshape(layer_shape)
                                   for layer in self._layers]
            self._layer_support = [np.asarray(layer["supported"], dtype=bool).reshape(layer_shape)
                                   for layer in self._layers]
            self._layer_slopes = [np.asarray(layer["slopeRadians"], dtype=float).reshape(layer_shape)
                                  for layer in self._layers]
            indices = {layer["layerId"]: index for index, layer in enumerate(self._layers)}
            for connection in layered["connections"]:
                first, second = [(endpoint[1], endpoint[2], indices[endpoint[0]])
                                 for endpoint in (connection["from"], connection["to"])]
                self._layer_connections.setdefault(first, set()).add(second)
                self._layer_connections.setdefault(second, set()).add(first)
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
            if not self._terrain_mode:
                return True
            if self._layered_mode:
                return self._layered_clear(a, b, low[:2], high[:2])
            return self._terrain_clear(a, b, low[:2], high[:2])

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
        if self._layered_mode:
            return self._point_layer(point) is not None
        column, row = self._cell(point)
        if not (0 <= column < self._shape[0] and 0 <= row < self._shape[1]
                and self._support[row, column]):
            return False
        expected = self._heights[row, column] + self._policy["referenceHeightM"]
        return abs(point[2] - expected) <= .02

    def _point_layer(self, point):
        column, row = np.floor((np.asarray(point[:2])-self._origin[:2])/self._resolution).astype(int)
        if not 0 <= column < self._shape[0] or not 0 <= row < self._shape[1]:
            return None
        matches = [index for index, (heights, support) in enumerate(zip(
                   self._layer_heights, self._layer_support, strict=True))
                   if support[row, column]
                   and abs(point[2] - heights[row, column] - self._policy["referenceHeightM"]) <= .02]
        # No nearest-height inference, especially when two surfaces overlap.
        return matches[0] if len(matches) == 1 else None

    def _layered_clear(self, a, b, low, high):
        first_layer, second_layer = self._point_layer(a), self._point_layer(b)
        if first_layer is None or second_layer is None:
            return False
        if first_layer == second_layer:
            return self._terrain_clear(a, b, low, high, self._layer_heights[first_layer],
                                       self._layer_support[first_layer], self._layer_slopes[first_layer])
        first, second = self._cell(a), self._cell(b)
        if second not in self._layer_connections.get(first, ()):
            return False
        # At a declared seam, the start side of the shared XY cell boundary
        # must use its own measured layer, and the far side the destination.
        # Choosing whichever surface is nearest Z under the robot could silently
        # jump from a lower floor to an overlapping upper floor.
        axis = 0 if first[0] != second[0] else 1
        coordinate = np.arange(self._shape[axis])
        start_side = coordinate <= first[axis] if first[axis] < second[axis] else coordinate >= first[axis]
        selector = start_side[None, :] if axis == 0 else start_side[:, None]
        heights = np.where(selector, self._layer_heights[first_layer], self._layer_heights[second_layer])
        support = np.where(selector, self._layer_support[first_layer], self._layer_support[second_layer])
        slopes = np.where(selector, self._layer_slopes[first_layer], self._layer_slopes[second_layer])
        return self._terrain_clear(a, b, low, high, heights, support, slopes)

    def _terrain_clear(self, a, b, low, high, heights=None, support=None, slopes=None):
        heights = self._heights if heights is None else heights
        support = self._support if support is None else support
        slopes = self._slopes if slopes is None else slopes
        yy, xx = np.mgrid[low[1]:high[1] + 1, low[0]:high[0] + 1]
        indices = np.stack((xx.ravel(), yy.ravel()), axis=1)
        lower = self._origin[:2] + indices * self._resolution - self._envelope[:2]
        upper = self._origin[:2] + (indices + 1) * self._resolution + self._envelope[:2]
        touched = indices[_intersects_boxes(a[:2], b[:2], lower, upper)]
        x, y = touched[:, 0], touched[:, 1]
        if not np.all(support[y, x]) or np.any(slopes[y, x] > self._policy["maxSlopeRad"]):
            return False
        max_step = self._policy["maxStepM"]
        covered = set(map(tuple, touched))
        for column, row in covered:
            for dx, dy in ((1, 0), (0, 1)):
                if ((column + dx, row + dy) in covered and
                        abs(heights[row, column] - heights[row + dy, column + dx]) > max_step + 1e-9):
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
            expected = heights[row, column] + self._policy["referenceHeightM"]
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
        if self._layered_mode:
            layer = self._point_layer(point)
            return (*node[:2], -1 if layer is None else layer)
        return node[:2] if self._terrain_mode else node

    def _world(self, node):
        if self._layered_mode:
            x, y, layer = node
            return [*map(float, self._origin[:2] + (np.asarray(node[:2]) + .5) * self._resolution),
                    float(self._layer_heights[layer][y, x] + self._policy["referenceHeightM"])]
        if self._terrain_mode:
            x, y = node
            return [*map(float, self._origin[:2] + (np.asarray(node) + .5) * self._resolution),
                    float(self._heights[y, x] + self._policy["referenceHeightM"])]
        return list(map(float, self._origin + (np.asarray(node) + .5) * self._resolution))

    def _valid(self, node):
        if self._layered_mode:
            if (len(node) != 3 or not 0 <= node[2] < len(self._layers)
                    or any(index < 0 or index >= self._shape[axis]
                           for axis, index in enumerate(node[:2]))
                    or not self._layer_support[node[2]][node[1], node[0]]):
                return False
            point = self._world(node)
            return self._point_layer(point) == node[2] and self.segment_clear(point, point)
        dimensions = 2 if self._terrain_mode else 3
        if any(index < 0 or index >= self._shape[axis] for axis, index in enumerate(node)):
            return False
        if self._terrain_mode and not self._support[node[1], node[0]]:
            return False
        return len(node) == dimensions and self.segment_clear(self._world(node), self._world(node))

    def _connect(self, point):
        node = self._cell(point)
        if self._layered_mode:
            candidates = {(node[0]+dx, node[1]+dy, node[2])
                          for dx, dy in itertools.product((-1, 0, 1), repeat=2)}
            candidates.update(self._layer_connections.get(node, ()))
        else:
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
            if self._layered_mode:
                adjacent = sorted({(node[0]+dx, node[1]+dy, node[2]) for dx, dy in neighbors}
                                  | self._layer_connections.get(node, set()))
            else:
                adjacent = (tuple(index + offset for index, offset in zip(node, delta, strict=True))
                            for delta in neighbors)
            for neighbor in adjacent:
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
