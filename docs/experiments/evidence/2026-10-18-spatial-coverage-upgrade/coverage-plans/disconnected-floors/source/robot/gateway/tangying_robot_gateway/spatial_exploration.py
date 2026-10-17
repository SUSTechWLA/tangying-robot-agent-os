"""Bounded, replaceable geometric exploration over a measured three-state map.

The explorer sees only the current ``navigation.volume.v1`` snapshot. Unknown
space may contribute a *first-hit* observation but never a traversable path.
The same ray caster can be used by a simulator with its separate truth map;
truth must never be passed to the production exploration policy.

Poses use xyz, quaternion wxyz; a sensor looks along local +X with local +Z up.
Viewpoint orientations are geometric observation goals, not dynamically feasible
flight attitudes or a promise that a ground robot has an actuated camera. An
execution provider must commission its sensor/body attitude constraints. Ground
viewpoint search therefore varies yaw only. Ground support may be single-layer
or explicitly connected measured layers; it is never inferred from free voxels.
"""
from __future__ import annotations

import hashlib
import heapq
import itertools
import json
import math
from dataclasses import dataclass
from typing import Protocol

from .spatial_navigation import SpatialNavigationError, SpatialPathPlanner, validate_volume


def _finite(value, name, minimum, maximum, *, lower_open=False):
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be finite")
    try:
        value = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be finite") from error
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if (value <= minimum if lower_open else value < minimum) or value > maximum:
        raise ValueError(f"{name} is outside its supported range")
    return float(value)


def _budget(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _pose(value):
    if not isinstance(value, (list, tuple)) or len(value) != 7:
        raise ValueError("pose must contain xyz and a normalized wxyz quaternion")
    result = [_finite(item, "pose", -1e15, 1e15) for item in value]
    if abs(sum(item * item for item in result[3:]) - 1.) > 1e-3:
        raise ValueError("pose quaternion must be normalized in wxyz order")
    return result


@dataclass(frozen=True)
class SensorModel:
    """Commissioned ideal pinhole angular field; no stochastic noise model."""

    range_m: float = 4.
    horizontal_fov_rad: float = math.pi / 2
    vertical_fov_rad: float = math.pi / 2

    def __post_init__(self):
        _finite(self.range_m, "range_m", 0., 10_000., lower_open=True)
        _finite(self.horizontal_fov_rad, "horizontal_fov_rad", 0., 2 * math.pi,
                lower_open=True)
        _finite(self.vertical_fov_rad, "vertical_fov_rad", 0., math.pi,
                lower_open=True)


@dataclass(frozen=True)
class ExplorationConfig:
    """Explicit experimental switches with bounded deterministic work.

    Ray work is bounded *per candidate* by max_rays/max_ray_steps. A proposal
    evaluates at most max_candidates and certifies at most max_plan_calls paths.
    Disabling visibility_gain changes ranking only; actual observations and
    geometric path admission always retain the same occlusion rules.
    """

    strategy: str = "information_gain"
    use_global_reposition: bool = True
    visibility_gain: bool = True
    max_candidates: int = 64
    max_rays: int = 81
    max_ray_steps: int = 8192
    max_plan_calls: int = 12

    def __post_init__(self):
        if self.strategy not in {"information_gain", "nearest_frontier"}:
            raise ValueError("unsupported exploration strategy")
        if type(self.use_global_reposition) is not bool or type(self.visibility_gain) is not bool:
            raise ValueError("exploration switches must be explicit booleans")
        _budget(self.max_candidates, "max_candidates", 1, 512)
        _budget(self.max_rays, "max_rays", 1, 4096)
        _budget(self.max_ray_steps, "max_ray_steps", 1, 1_000_000)
        _budget(self.max_plan_calls, "max_plan_calls", 1, 512)


class ExplorationPolicy(Protocol):
    """Replaceable proposal port; it does not own motion or map mutation."""

    def propose(self, start_pose, *, excluded_targets=(), observed_targets=()) -> dict:
        """Return bounded candidates, their current-map paths and diagnostics."""
        ...


class ObservationModel(Protocol):
    """Replaceable sensor port for deterministic experiments or real evidence."""

    def __call__(self, volume, pose, sensor, *, max_rays=81, max_ray_steps=8192) -> dict:
        ...


def _rotation(quaternion):
    w, x, y, z = quaternion
    return ((1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)),
            (2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)),
            (2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y)))


def _look_quaternion(direction):
    x, y, z = direction
    yaw, pitch = math.atan2(y, x), -math.atan2(z, math.hypot(x, y))
    cy, sy, cp, sp = math.cos(yaw/2), math.sin(yaw/2), math.cos(pitch/2), math.sin(pitch/2)
    return [cy*cp, -sy*sp, cy*sp, sy*cp]


def _directions(sensor, maximum):
    """A deterministic angular grid including optical axis even at budget one."""
    yield (1., 0., 0.)
    if maximum == 1:
        return
    side = max(2, math.ceil(math.sqrt(maximum)))
    samples = []
    for row in range(side):
        elevation = (row / (side-1) - .5) * sensor.vertical_fov_rad
        for column in range(side):
            azimuth = (column / (side-1) - .5) * sensor.horizontal_fov_rad
            if abs(elevation) < 1e-12 and abs(azimuth) < 1e-12:
                continue
            samples.append((elevation*elevation + azimuth*azimuth, row, column,
                            (math.cos(elevation)*math.cos(azimuth),
                             math.cos(elevation)*math.sin(azimuth), math.sin(elevation))))
    for _, _, _, direction in sorted(samples)[:maximum-1]:
        yield direction


def _inside(node, shape):
    return all(0 <= index < size for index, size in zip(node, shape, strict=True))


def _flat(node, shape):
    return (node[2] * shape[1] + node[1]) * shape[0] + node[0]


def _trace(start, direction, origin, resolution, shape, range_m):
    """Supercover voxel DDA; corner ties cannot leak through diagonal walls."""
    node = [math.floor((value-base)/resolution) for value, base in zip(start, origin, strict=True)]
    if not _inside(node, shape):
        return
    step = [1 if value > 1e-12 else -1 if value < -1e-12 else 0 for value in direction]
    boundary_axes = [axis for axis in range(3) if step[axis] == 0 and
                     abs((start[axis]-origin[axis])/resolution - node[axis]) <= 1e-10]

    def supercover(nodes):
        result = set()
        for cell in nodes:
            for offsets in itertools.product((0, -1), repeat=len(boundary_axes)):
                neighbor = list(cell)
                for axis, offset in zip(boundary_axes, offsets, strict=True):
                    neighbor[axis] += offset
                if _inside(neighbor, shape):
                    result.add(_flat(neighbor, shape))
        return tuple(sorted(result))

    yield supercover([node])
    next_t, delta_t = [], []
    for axis in range(3):
        if step[axis] == 0:
            next_t.append(math.inf)
            delta_t.append(math.inf)
        else:
            boundary = origin[axis] + (node[axis] + (1 if step[axis] > 0 else 0))*resolution
            next_t.append(max(0., (boundary-start[axis])/direction[axis]))
            delta_t.append(resolution / abs(direction[axis]))
    while True:
        distance = min(next_t)
        if distance > range_m + 1e-10:
            return
        axes = [axis for axis in range(3) if abs(next_t[axis]-distance) <= 1e-10]
        touched = []
        for count in range(1, len(axes)+1):
            for subset in itertools.combinations(axes, count):
                neighbor = list(node)
                for axis in subset:
                    neighbor[axis] += step[axis]
                if _inside(neighbor, shape):
                    touched.append(neighbor)
        if touched:
            yield supercover(touched)
        for axis in axes:
            node[axis] += step[axis]
            next_t[axis] += delta_t[axis]
        if not _inside(node, shape):
            return


def _raycast(volume, pose, sensor, max_rays, max_ray_steps):
    rotation = _rotation(pose[3:])
    observed, unknown, occupied = set(), set(), set()
    rays, steps, exhausted = 0, 0, False
    for local in _directions(sensor, max_rays):
        rays += 1
        direction = [sum(row[axis]*local[axis] for axis in range(3)) for row in rotation]
        interrupted = False
        for touched in _trace(pose[:3], direction, volume["origin"], volume["resolution"],
                              volume["shape"], sensor.range_m):
            # Treat a simultaneous corner crossing atomically: a partially
            # processed tie must never reveal the cells behind an unseen wall.
            if steps + len(touched) > max_ray_steps:
                exhausted, interrupted = True, True
                break
            steps += len(touched)
            blocked = False
            for index in touched:
                observed.add(index)
                value = volume["cells"][index]
                if value == -1:
                    unknown.add(index)
                    blocked = True
                elif value == 100:
                    occupied.add(index)
                    blocked = True
            if blocked:
                break
        if interrupted:
            break
    return {"observedVoxelIds": sorted(observed), "unknownVoxelIds": sorted(unknown),
            "occupiedVoxelIds": sorted(occupied), "raysCast": rays, "raySteps": steps,
            "budgetExhausted": exhausted}


def raycast_observation(volume, pose, sensor, *, max_rays=81, max_ray_steps=8192):
    """Observe first hits; unknown and occupied both occlude subsequent voxels.

    IDs are x-fastest flat indices in this volume's geometry/world revision.
    No map is modified. The caller must preserve provenance and transform poses
    into the map's world frame before invoking this pure observation function.
    """
    if not isinstance(sensor, SensorModel):
        raise TypeError("sensor must be a SensorModel")
    document, pose = validate_volume(volume), _pose(pose)
    _budget(max_rays, "max_rays", 1, 4096)
    _budget(max_ray_steps, "max_ray_steps", 1, 1_000_000)
    return _raycast(document, pose, sensor, max_rays, max_ray_steps)


def navigation_points(volume, policy):
    """Enumerate measured candidate centres without claiming their reachability.

    Inputs are normalized navigation contracts. The path planner must still
    certify the complete body envelope, support, connectivity and the route.
    """
    shape, origin, resolution = volume["shape"], volume["origin"], volume["resolution"]
    if policy["mode"] == "volume3d":
        for index, value in enumerate(volume["cells"]):
            if value == 0:
                node = (index % shape[0], index // shape[0] % shape[1],
                        index // (shape[0] * shape[1]))
                yield node, [base + (cell + .5) * resolution
                             for base, cell in zip(origin, node, strict=True)]
        return
    layers = ([volume["terrain"]] if policy["mode"] == "terrain25d"
              else volume["terrainLayers"]["layers"])
    for layer in layers:
        for index, supported in enumerate(layer["supported"]):
            if not supported:
                continue
            x, y = index % shape[0], index // shape[0]
            point = [origin[0] + (x + .5) * resolution,
                     origin[1] + (y + .5) * resolution,
                     layer["heights"][index] + policy["referenceHeightM"]]
            z = math.floor((point[2] - origin[2]) / resolution)
            node = (x, y, z)
            if _inside(node, shape) and volume["cells"][_flat(node, shape)] == 0:
                yield node, point


class SpatialExplorer:
    """Frontier/NBV proposal service with measured-space route certification.

    The bounded candidate grid and finite rays are an approximation. Exhausting
    them never proves that a continuous scene has been completely covered.
    ``use_global_reposition=False`` admits only a direct certified segment;
    enabling it permits the existing A* admission planner to route around known
    obstacles. Neither setting authorizes entering unknown space.
    """

    def __init__(self, volume, navigation_policy, sensor=None, config=None):
        self._planner = SpatialPathPlanner(volume, navigation_policy)
        self._volume, self._policy = self._planner.volume, self._planner.policy
        self.sensor = sensor if sensor is not None else SensorModel()
        self.config = config if config is not None else ExplorationConfig()
        if not isinstance(self.sensor, SensorModel) or not isinstance(self.config, ExplorationConfig):
            raise TypeError("explorer requires SensorModel and ExplorationConfig")
        # This is the shared grid address space, identical to the mission ledger
        # geometry_id contract. Local map/robot/calibration identities belong to
        # the certified path, not to a shared exploration objective.
        identity = {key: self._volume[key] for key in ("frameId", "worldFrameRevision",
                    "origin", "shape", "resolution")}
        self._namespace = hashlib.sha256(json.dumps(identity, sort_keys=True,
                                        separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    def _point(self, node):
        point = [base+(index+.5)*self._volume["resolution"]
                 for base, index in zip(self._volume["origin"], node, strict=True)]
        if self._policy["mode"] == "terrain25d":
            terrain = self._volume["terrain"]
            index = node[1]*self._volume["shape"][0]+node[0]
            if not terrain["supported"][index]:
                return None
            point[2] = terrain["heights"][index] + self._policy["referenceHeightM"]
        return point

    def _views(self, start):
        volume, shape = self._volume, self._volume["shape"]
        ground = self._policy["mode"] != "volume3d"
        cardinal = ((1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0))
        axes = cardinal + ((0, 0, 1), (0, 0, -1))
        # In-place looks are useful even if the camera can see a distant
        # frontier while its body cannot safely stand in the frontier voxel.
        for direction in (cardinal if ground else axes):
            yield (0., -1, direction, start[:3], 1)
        for node, point in navigation_points(volume, self._policy):
            index = _flat(node, shape)
            adjacent = [direction for direction in axes
                        if _inside(tuple(item+offset for item, offset in zip(node, direction, strict=True)), shape)
                        and volume["cells"][_flat(tuple(item+offset for item, offset in
                            zip(node, direction, strict=True)), shape)] == -1]
            if not adjacent:
                continue
            directions = sorted({(direction if direction[2] == 0 or not ground else heading)
                                for direction in adjacent
                                for heading in (cardinal if ground and direction[2] else (direction,))})
            for direction in directions:
                yield (math.dist(point, start[:3]), index, direction, point, len(adjacent))

    def propose(self, start_pose, *, excluded_targets=(), observed_targets=()):
        start = _pose(start_pose)
        if (not isinstance(excluded_targets, (list, tuple, set, frozenset))
                or len(excluded_targets) > 250_000
                or not isinstance(observed_targets, (list, tuple, set, frozenset))
                or len(observed_targets) > 250_000):
            raise ValueError("target collections must be bounded lists, tuples or sets")
        if any(type(item) is not str for item in excluded_targets):
            raise ValueError("excluded_targets must contain targetId strings")
        if any(type(item) is not int or not 0 <= item < len(self._volume["cells"])
               for item in observed_targets):
            raise ValueError("observed_targets must contain voxel IDs in this geometry")
        excluded, observed = set(excluded_targets), set(observed_targets)
        config = self.config
        diagnostics = {"candidateBudget": config.max_candidates,
                       "planCallBudget": config.max_plan_calls, "evaluatedCandidates": 0,
                       "planCalls": 0, "raysCast": 0, "raySteps": 0,
                       "rayBudgetExhaustions": 0, "rejectedPaths": {},
                       "strategy": config.strategy,
                       "useGlobalReposition": config.use_global_reposition,
                       "visibilityGain": config.visibility_gain,
                       "worldFrameRevision": self._volume["worldFrameRevision"],
                       "mapRevision": self._volume["mapRevision"],
                       "geometryId": self._namespace,
                       "completeness": "bounded_candidates_not_scene_completion"}
        candidates = []
        # nsmallest bounds retained candidate storage even for the maximum-size
        # volume. It is deterministic; input storage order is contractually fixed.
        views = heapq.nsmallest(config.max_candidates,
                (view for view in self._views(start)
                 if self._target_id(view[3], view[2]) not in excluded), key=lambda row: row[:3])
        seen = set()
        for _, index, direction, point, raw_gain in views:
            orientation = _look_quaternion(direction)
            pose = [*point, *orientation]
            target_id = self._target_id(point, direction)
            if target_id in seen:
                continue
            seen.add(target_id)
            diagnostics["evaluatedCandidates"] += 1
            observation = _raycast(self._volume, pose, self.sensor, config.max_rays, config.max_ray_steps)
            diagnostics["raysCast"] += observation["raysCast"]
            diagnostics["raySteps"] += observation["raySteps"]
            diagnostics["rayBudgetExhaustions"] += int(observation["budgetExhausted"])
            gain_ids = sorted(set(observation["unknownVoxelIds"]) - observed)
            if not gain_ids:
                continue
            gain = len(gain_ids)
            utility_gain = gain if config.visibility_gain else raw_gain
            distance = math.dist(start[:3], point)
            score = (utility_gain / (self._volume["resolution"] + distance)
                     if config.strategy == "information_gain" else -distance)
            candidates.append({"targetId": target_id, "pose": pose,
                               "visibleUnknownVoxelIds": gain_ids, "informationGain": gain,
                               "score": score, "_distance": distance, "_utilityGain": utility_gain})
        candidates.sort(key=lambda item: (-item["score"], item["_distance"], item["targetId"]))
        certified = []
        for candidate in candidates:
            if diagnostics["planCalls"] >= config.max_plan_calls:
                break
            diagnostics["planCalls"] += 1
            try:
                if not config.use_global_reposition and not self._planner.segment_clear(start[:3], candidate["pose"][:3]):
                    raise SpatialNavigationError("DIRECT_REPOSITION_BLOCKED", "direct reposition is blocked")
                path = self._planner.plan(start, candidate["pose"])
            except SpatialNavigationError as error:
                rejected = diagnostics["rejectedPaths"]
                rejected[error.code] = rejected.get(error.code, 0)+1
                continue
            length = sum(math.dist(first[:3], second[:3]) for first, second in
                         itertools.pairwise(path["waypoints"]))
            candidate.pop("_distance")
            utility = candidate.pop("_utilityGain")
            candidate.update(path=path, pathLengthM=length)
            # Final information-gain ranking pays the certified route cost,
            # rather than the optimistic endpoint distance around obstacles.
            if config.strategy == "information_gain":
                candidate["score"] = utility / (self._volume["resolution"] + length)
            else:
                candidate["score"] = -length
            certified.append(candidate)
        certified.sort(key=lambda item: (-item["score"], item["pathLengthM"], item["targetId"]))
        diagnostics["certifiedCandidates"] = len(certified)
        diagnostics["boundedSearchExhausted"] = len(views) == config.max_candidates or len(candidates) > diagnostics["planCalls"]
        return {"schemaVersion": "exploration.proposal.v1",
                "status": "candidate" if certified else "no_candidate",
                "candidate": certified[0] if certified else None,
                "candidates": certified, "diagnostics": diagnostics}

    def _target_id(self, point, direction):
        cell = [math.floor((value-base)/self._volume["resolution"])
                for value, base in zip(point, self._volume["origin"], strict=True)]
        return f"{self._namespace}:{','.join(map(str, cell))}:{','.join(map(str, direction))}"
