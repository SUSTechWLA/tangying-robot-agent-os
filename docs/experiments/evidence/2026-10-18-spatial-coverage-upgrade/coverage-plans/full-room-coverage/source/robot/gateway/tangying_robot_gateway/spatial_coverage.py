"""Explicit-scope geometric observation coverage over registered measured maps.

The plan contains predicted observations, never evidence of execution. Unknown
space remains blocked for motion. Aircraft use free volume; ground platforms use
measured support and commissioned envelopes. Serial peer clearance is checked,
but flight dynamics, wheel curvature and foot contacts remain driver duties.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
from dataclasses import dataclass, replace
from typing import Protocol

from .spatial_exploration import (
    SensorModel,
    _budget,
    _finite,
    _look_quaternion,
    _pose,
    navigation_points,
    raycast_observation,
)
from .spatial_mission import geometry_id
from .spatial_navigation import SpatialNavigationError, SpatialPathPlanner, _identifier


class RoutePlannerPort(Protocol):
    def plan(self, start_pose, goal_pose) -> dict:
        """Return a current-map navigation.path.v1 or a bounded refusal."""


@dataclass(frozen=True)
class CoverageConfig:
    strategy: str = "gain_per_meter"
    max_viewpoints: int = 96
    max_plan_calls: int = 8
    max_rays: int = 49
    max_ray_steps: int = 8192
    max_legs: int = 32
    max_travel_m: float = 200.
    max_total_plan_calls: int = 256
    max_total_viewpoint_evaluations: int = 3072

    def __post_init__(self):
        if self.strategy not in {"gain_per_meter", "nearest"}:
            raise ValueError("unsupported coverage strategy")
        _budget(self.max_viewpoints, "max_viewpoints", 6, 4096)
        _budget(self.max_plan_calls, "max_plan_calls", 1, 512)
        _budget(self.max_rays, "max_rays", 1, 4096)
        _budget(self.max_ray_steps, "max_ray_steps", 1, 1_000_000)
        _budget(self.max_legs, "max_legs", 1, 256)
        _finite(self.max_travel_m, "max_travel_m", 0., 1_000_000., lower_open=True)
        _budget(self.max_total_plan_calls, "max_total_plan_calls", 1, 4096)
        _budget(self.max_total_viewpoint_evaluations, "max_total_viewpoint_evaluations", 6, 131072)


def validate_scope(scope, volume):
    if (type(scope) is not dict or set(scope) != {"schemaVersion", "geometryId", "targetVoxelIds"}
            or scope["schemaVersion"] != "coverage.scope.v1"
            or scope["geometryId"] != geometry_id(volume)):
        raise ValueError("coverage scope must bind the exact registered world/grid")
    targets = scope["targetVoxelIds"]
    if (type(targets) is not list or not 1 <= len(targets) <= len(volume["cells"])
            or any(type(item) is not int or not 0 <= item < len(volume["cells"]) for item in targets)
            or len(set(targets)) != len(targets)):
        raise ValueError("coverage targets must be nonempty unique voxel IDs in this grid")
    return frozenset(targets)


def _target_id(namespace, pose):
    body = json.dumps(pose, separators=(",", ":"), allow_nan=False).encode()
    return namespace + ":coverage:" + hashlib.sha256(body).hexdigest()


class SpatialCoverageExplorer:
    """A replaceable proposal port for inspecting explicit known-map targets.

    Finite global viewpoints are stratified across the address space, rather
    than only following frontiers. Sampling is deterministic and budgeted; no
    candidate is not a proof of unobservability or continuous-space infeasibility.
    Factories are trusted Python injection points, never remote module names.
    """
    def __init__(self, volume, navigation_policy, scope, sensor=None, config=None, *,
                 planner_factory=SpatialPathPlanner, observation_model=raycast_observation):
        # Independent default certifier remains mandatory for plugin paths.
        self.certifier = SpatialPathPlanner(volume, navigation_policy)
        self.volume, self.policy = self.certifier.volume, self.certifier.policy
        self.targets = validate_scope(scope, self.volume)
        self.namespace = geometry_id(self.volume)
        self.sensor = SensorModel() if sensor is None else sensor
        self.config = CoverageConfig() if config is None else config
        if not isinstance(self.sensor, SensorModel) or not isinstance(self.config, CoverageConfig):
            raise TypeError("coverage requires SensorModel and CoverageConfig")
        self.planner = planner_factory(copy.deepcopy(self.volume), copy.deepcopy(self.policy))
        self.observe = observation_model

    def _viewpoints(self, start):
        axes = ((1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0))
        if self.policy["mode"] == "volume3d":
            axes += ((0, 0, 1), (0, 0, -1))
        for direction in axes:
            yield [*start[:3], *_look_quaternion(direction)]
        # The source map bounds this list at 250000 points. Uniform index
        # stratification preserves distant floors/regions under a small budget.
        points = list(navigation_points(self.volume, self.policy))
        slots = max(0, (self.config.max_viewpoints - len(axes)) // len(axes))
        selected = (list(range(len(points))) if len(points) <= slots else
                    sorted({math.floor(i * (len(points) - 1) / max(1, slots - 1))
                            for i in range(slots)}))
        for index in selected:
            _, point = points[index]
            for direction in axes:
                yield [*point, *_look_quaternion(direction)]
        self._point_count = len(points)
        self._sample_count = len(selected)

    def propose(self, start_pose, *, excluded_targets=(), observed_targets=()):
        start, config = _pose(start_pose), self.config
        if (not isinstance(excluded_targets, (tuple, list, set, frozenset))
                or len(excluded_targets) > 250_000
                or any(type(item) is not str for item in excluded_targets)
                or not isinstance(observed_targets, (tuple, list, set, frozenset))
                or len(observed_targets) > 250_000
                or any(type(item) is not int or not 0 <= item < len(self.volume["cells"])
                       for item in observed_targets)):
            raise ValueError("coverage exclusions and observations must be bounded valid IDs")
        self.certifier.plan(start, start)
        remaining = self.targets - set(observed_targets)
        excluded, seen, candidates = set(excluded_targets), set(), []
        diagnostics = {"objectiveKind": "explicit_scope_coverage", "targetVoxels": len(self.targets),
                       "remainingTargetVoxels": len(remaining), "evaluatedViewpoints": 0,
                       "planCalls": 0, "rejectedPaths": {}, "rayBudgetExhaustions": 0,
                       "predictionOnly": True, "strategy": config.strategy}
        for pose in self._viewpoints(start):
            target_id = _target_id(self.namespace, pose)
            if target_id in excluded or target_id in seen:
                continue
            seen.add(target_id)
            diagnostics["evaluatedViewpoints"] += 1
            if not self.certifier.segment_clear(pose[:3], pose[:3]):
                continue
            observation = self.observe(copy.deepcopy(self.volume), pose[:], self.sensor,
                                       max_rays=config.max_rays, max_ray_steps=config.max_ray_steps)
            ids = observation.get("observedVoxelIds")
            if (type(ids) is not list or len(ids) > len(self.volume["cells"])
                    or len(set(ids)) != len(ids) or any(type(item) is not int
                    or not 0 <= item < len(self.volume["cells"]) for item in ids)):
                raise ValueError("observation model returned invalid voxel addresses")
            diagnostics["rayBudgetExhaustions"] += int(bool(observation.get("budgetExhausted")))
            expected = sorted(remaining & set(ids))
            if not expected:
                continue
            distance = math.dist(start[:3], pose[:3])
            score = (len(expected) / (self.volume["resolution"] + distance)
                     if config.strategy == "gain_per_meter" else -distance)
            candidates.append({"targetId": target_id, "pose": pose, "score": score,
                               "expectedObservedVoxelIds": expected,
                               "visibleUnknownVoxelIds": [], "informationGain": len(expected),
                               "objectiveKind": "explicit_scope_coverage"})
        candidates.sort(key=lambda c: (-c["score"], c["targetId"]))
        certified = []
        for candidate in candidates[:config.max_plan_calls]:
            diagnostics["planCalls"] += 1
            try:
                path = copy.deepcopy(self.planner.plan(start[:], candidate["pose"][:]))
                # A backend cannot turn an unsupported shortcut into authority.
                if type(path) is not dict:
                    raise SpatialNavigationError("PLUGIN_PATH_REJECTED", "backend returned no path document")
                points = path.get("waypoints")
                if (path.get("schemaVersion") != "navigation.path.v1"
                        or path.get("mode") != self.policy["mode"]
                        or any(path.get(key) != self.volume[key] for key in
                               ("robotId", "mapId", "mapRevision", "frameId",
                                "worldFrameRevision", "calibrationRevision"))
                        or type(points) is not list or not 2 <= len(points) <= self.policy["maxWaypoints"]):
                    raise SpatialNavigationError("PLUGIN_PATH_REJECTED", "backend path failed independent admission")
                normalized = [_pose(point) for point in points]
                if (normalized[0] != start or normalized[-1] != candidate["pose"]
                        or any(not self.certifier.segment_clear(a[:3], b[:3])
                               for a, b in itertools.pairwise(normalized))):
                    raise SpatialNavigationError("PLUGIN_PATH_REJECTED", "backend path failed independent admission")
                self.certifier.plan(candidate["pose"], candidate["pose"])
            except (SpatialNavigationError, ValueError) as error:
                reasons = diagnostics["rejectedPaths"]
                code = getattr(error, "code", "PLUGIN_PATH_REJECTED")
                reasons[code] = reasons.get(code, 0) + 1
                continue
            length = sum(math.dist(a[:3], b[:3]) for a, b in itertools.pairwise(points))
            candidate.update(path=path, pathLengthM=length)
            candidate["score"] = (len(candidate["expectedObservedVoxelIds"]) /
                                  (self.volume["resolution"] + length)
                                  if config.strategy == "gain_per_meter" else -length)
            certified.append(candidate)
        certified.sort(key=lambda c: (-c["score"], c["pathLengthM"], c["targetId"]))
        diagnostics.update(sampledPositions=self._sample_count, availablePositions=self._point_count,
                           viewpointSamplingTruncated=self._sample_count < self._point_count,
                           pathSamplingTruncated=len(candidates) > config.max_plan_calls,
                           scopeAlreadyObserved=not remaining)
        return {"schemaVersion": "exploration.proposal.v1", "scope": {
                    "schemaVersion": "coverage.scope.v1", "geometryId": self.namespace,
                    "targetVoxelIds": sorted(self.targets)},
                "status": "candidate" if certified else "no_candidate",
                "candidate": certified[0] if certified else None, "candidates": certified,
                "diagnostics": diagnostics}


def _peer_clear(path, own_policy, other_pose, other_policy):
    half = [own + own_policy["margin"] + other + other_policy["margin"]
            for own, other in zip(own_policy["bodyHalfExtents"], other_policy["bodyHalfExtents"], strict=True)]
    low = [p - h for p, h in zip(other_pose[:3], half, strict=True)]
    high = [p + h for p, h in zip(other_pose[:3], half, strict=True)]
    for a, b in itertools.pairwise(path["waypoints"]):
        enter, leave = 0., 1.
        for axis in range(3):
            delta = b[axis] - a[axis]
            if delta == 0:
                if a[axis] < low[axis] or a[axis] > high[axis]:
                    enter, leave = 1., 0.
                    break
            else:
                first, second = (low[axis] - a[axis]) / delta, (high[axis] - a[axis]) / delta
                enter, leave = max(enter, min(first, second)), min(leave, max(first, second))
        if enter <= leave + 1e-12:
            return False
    return True


def plan_coverage_request(request, *, planner_factory=SpatialPathPlanner,
                          observation_model=raycast_observation):
    """Greedy serial heterogeneous coverage; explicit unmet scope is retained."""
    if (type(request) is not dict or request.get("schemaVersion") != "coverage.request.v1"
            or not {"schemaVersion", "missionId", "scope", "robots"} <= request.keys()
            or request.keys() - {"schemaVersion", "missionId", "scope", "robots", "config"}):
        raise ValueError("request fields do not match coverage.request.v1")
    _identifier(request["missionId"], "missionId")
    robots = request["robots"]
    if type(robots) is not list or not 1 <= len(robots) <= 16:
        raise ValueError("coverage requires 1..16 registered robots")
    config = CoverageConfig(**request.get("config", {}))
    explorers, positions, policies = {}, {}, {}
    for robot in robots:
        if type(robot) is not dict or set(robot) != {"volume", "policy", "pose", "sensor"}:
            raise ValueError("each robot requires volume, policy, pose and sensor")
        explorer = SpatialCoverageExplorer(robot["volume"], robot["policy"], request["scope"],
                     SensorModel(**robot["sensor"]), config, planner_factory=planner_factory,
                     observation_model=observation_model)
        robot_id = explorer.volume["robotId"]
        if robot_id in explorers:
            raise ValueError("robot IDs must be unique")
        explorers[robot_id], policies[robot_id] = explorer, explorer.policy
        positions[robot_id] = _pose(robot["pose"])
        explorer.certifier.plan(positions[robot_id], positions[robot_id])
    for first, second in itertools.combinations(explorers, 2):
        path = {"waypoints": [positions[first], positions[first]]}
        if not _peer_clear(path, policies[first], positions[second], policies[second]):
            raise ValueError("initial robot envelopes overlap")
    targets = next(iter(explorers.values())).targets
    predicted, legs, length, calls, evaluations, diagnostics = set(), [], 0., 0, 0, []
    excluded = {robot: set() for robot in explorers}
    reason = "leg_budget"
    for _ in range(config.max_legs):
        options = []
        for robot_id, explorer in explorers.items():
            plan_budget = config.max_total_plan_calls - calls
            view_budget = config.max_total_viewpoint_evaluations - evaluations
            if plan_budget <= 0 or view_budget < 6:
                reason = "global_planning_budget"
                break
            explorer.config = replace(config, max_plan_calls=min(config.max_plan_calls, plan_budget),
                                      max_viewpoints=min(config.max_viewpoints, view_budget))
            proposal = explorer.propose(positions[robot_id], observed_targets=predicted,
                                       excluded_targets=excluded[robot_id])
            calls += proposal["diagnostics"]["planCalls"]
            evaluations += proposal["diagnostics"]["evaluatedViewpoints"]
            detail = {"robotId": robot_id, **proposal["diagnostics"], "peerRefusals": 0, "travelRefusals": 0}
            for candidate in proposal["candidates"]:
                if length + candidate["pathLengthM"] > config.max_travel_m:
                    detail["travelRefusals"] += 1
                    continue
                if any(not _peer_clear(candidate["path"], policies[robot_id], positions[other], policies[other])
                       for other in explorers if other != robot_id):
                    detail["peerRefusals"] += 1
                    continue
                options.append((robot_id, candidate))
            diagnostics.append(detail)
        if not options:
            if reason != "global_planning_budget":
                reason = "bounded_search_no_admissible_viewpoint"
            break
        robot_id, candidate = min(options, key=lambda item: (-item[1]["score"], item[0], item[1]["targetId"]))
        predicted.update(candidate["expectedObservedVoxelIds"])
        length += candidate["pathLengthM"]
        positions[robot_id] = candidate["pose"]
        excluded[robot_id].add(candidate["targetId"])
        legs.append({"robotId": robot_id, **copy.deepcopy(candidate)})
        if targets <= predicted:
            reason = "predicted_scope_covered"
            break
    return {"schemaVersion": "coverage.plan.v1", "missionId": request["missionId"],
            "geometryId": request["scope"]["geometryId"], "predictionOnly": True,
            "status": "predicted_complete" if targets <= predicted else "partial",
            "stopReason": reason, "targetVoxels": len(targets), "predictedCoveredVoxels": len(predicted),
            "predictedCoverage": len(predicted) / len(targets), "observedCoverage": None,
            "unresolvedVoxelIds": sorted(targets - predicted), "pathLengthM": length,
            "planCalls": calls, "evaluatedViewpoints": evaluations, "legs": legs, "diagnostics": diagnostics,
            "executionModel": "serial_with_stationary_peer_envelope_checks",
            "physicalValidation": False, "continuousSceneCompletenessProven": False}
