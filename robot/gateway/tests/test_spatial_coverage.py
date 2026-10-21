"""Discrete geometric coverage and closed-loop evidence, not physical control."""
import copy
import hashlib
import itertools
import json
import math

import pytest
from tangying_robot_gateway.spatial_coverage import (
    CoverageConfig,
    SpatialCoverageExplorer,
    _peer_clear,
    plan_coverage_request,
)
from tangying_robot_gateway.spatial_exploration import (
    SensorModel,
    SpatialExplorer,
    raycast_observation,
)
from tangying_robot_gateway.spatial_mission import (
    CoverageLedger,
    DriverResult,
    MissionError,
    SpatialMissionRunner,
    SpatialSnapshot,
    TaskCoordinator,
    geometry_id,
)


def volume(robot="uav", shape=(8, 7, 8)):
    nx, ny, nz = shape
    cells = [100 if x in (0, nx-1) or y in (0, ny-1) or z in (0, nz-1) else 0
             for z, y, x in itertools.product(range(nz), range(ny), range(nx))]
    return {"schemaVersion": "navigation.volume.v1", "robotId": robot, "mapId": "known-room",
            "mapRevision": "registered-1", "frameId": "world", "worldFrameRevision": "world-1",
            "calibrationRevision": "cal-1", "resolution": 1., "origin": [0., 0., 0.],
            "shape": list(shape), "cells": cells}


def pose(x=2.5, y=2.5, z=2.5):
    return [x, y, z, 1., 0., 0., 0.]


def flat(document, x, y, z):
    nx, ny, _ = document["shape"]
    return (z * ny + y) * nx + x


def policy():
    return {"mode": "volume3d", "bodyHalfExtents": [.1, .1, .1], "margin": .05,
            "maxExpanded": 3000, "maxWaypoints": 256}


def scope(document, targets):
    return {"schemaVersion": "coverage.scope.v1", "geometryId": geometry_id(document),
            "targetVoxelIds": targets}


def request(document=None, targets=None):
    document = volume() if document is None else document
    targets = [flat(document, 1, 2, 2)] if targets is None else targets
    return {"schemaVersion": "coverage.request.v1", "missionId": "inspect-known-room",
            "scope": scope(document, targets), "config": {"max_viewpoints": 24, "max_rays": 25},
            "robots": [{"volume": document, "policy": policy(), "pose": pose(),
                        "sensor": {"range_m": 2., "horizontal_fov_rad": math.pi/2,
                                   "vertical_fov_rad": math.pi/2}}]}


def test_known_map_coverage_is_distinct_from_unknown_frontier_and_not_evidence():
    r = request()
    robot = r["robots"][0]
    assert SpatialExplorer(robot["volume"], policy()).propose(pose())["status"] == "no_candidate"
    plan = plan_coverage_request(r)
    assert plan["status"] == "predicted_complete"
    assert plan["predictedCoverage"] == 1.
    assert plan["observedCoverage"] is None and plan["predictionOnly"]
    assert plan["physicalValidation"] is False
    assert plan["legs"][0]["visibleUnknownVoxelIds"] == []
    assert plan["legs"][0]["expectedObservedVoxelIds"] == r["scope"]["targetVoxelIds"]
    assert r == request()  # planner does not mutate request, map or robot pose.


@pytest.mark.parametrize("mutation", [
    lambda r: r["scope"].update(geometryId="wrong"),
    lambda r: r["scope"].update(targetVoxelIds=[]),
    lambda r: r["scope"].update(targetVoxelIds=[True]),
    lambda r: r["scope"].update(targetVoxelIds=[-1]),
    lambda r: r["scope"].update(targetVoxelIds=[0, 0]),
    lambda r: r["scope"].update(targetVoxelIds=[1000000]),
    lambda r: r["robots"][0]["volume"].update(worldFrameRevision="different-world"),
    lambda r: r["robots"].append(copy.deepcopy(r["robots"][0])),
])
def test_invalid_scope_and_robot_registration_are_rejected(mutation):
    r = request(); mutation(r)
    with pytest.raises(ValueError):
        plan_coverage_request(r)


@pytest.mark.parametrize("kwargs", [{"max_viewpoints": 5}, {"max_legs": 0},
                                    {"max_total_plan_calls": 0}, {"max_travel_m": float("nan")}])
def test_resource_controls_are_finite_explicit_budgets(kwargs):
    with pytest.raises(ValueError):
        CoverageConfig(**kwargs)


def test_budget_or_occlusion_retains_all_unresolved_scope_without_false_impossibility():
    r = request(targets=[0])  # Far outer corner outside the in-place sensor range.
    r["config"].update(max_viewpoints=6, max_legs=1)
    result = plan_coverage_request(r)
    assert result["status"] == "partial" and result["unresolvedVoxelIds"] == [0]
    assert result["observedCoverage"] is None
    assert result["diagnostics"][0]["viewpointSamplingTruncated"]
    assert result["continuousSceneCompletenessProven"] is False


def test_independent_total_work_caps_apply_across_robot_proposals():
    r = request(targets=[flat(volume(), 1, 2, 2), flat(volume(), 6, 5, 6)])
    r["config"].update(max_total_plan_calls=1, max_total_viewpoint_evaluations=12,
                       max_viewpoints=12, max_legs=8)
    result = plan_coverage_request(r)
    assert result["planCalls"] <= 1 and result["evaluatedViewpoints"] <= 12
    assert result["status"] == "partial"


def test_ground_and_air_use_distinct_admissibility_but_one_coverage_scope():
    document = volume(shape=(10, 7, 8))
    r = request(document, [flat(document, 3, 2, 1), flat(document, 6, 4, 5)])
    r["config"].update(max_viewpoints=24, max_rays=25, max_legs=8)
    ground = copy.deepcopy(r["robots"][0]); ground["volume"]["robotId"] = "ugv"
    count = 10 * 7
    ground["volume"]["terrain"] = {"schemaVersion": "navigation.terrain.v1", "sourceId": "floor",
         "observedAtUnixMs": 1, "heights": [1.] * count, "supported": [True] * count,
         "slopeRadians": [0.] * count}
    ground["pose"] = pose(2.5, 2.5, 1.5)
    ground["policy"].update(mode="terrain25d", maxSlopeRad=.3, maxStepM=.1, referenceHeightM=.5)
    ground["sensor"].update(range_m=1.4, vertical_fov_rad=.1)
    r["robots"][0]["pose"] = pose(6.5, 4.5, 4.5)
    r["robots"][0]["sensor"].update(range_m=1.4)
    r["robots"].append(ground)
    result = plan_coverage_request(r)
    assert result["status"] == "predicted_complete"
    assert {leg["robotId"] for leg in result["legs"]} == {"ugv", "uav"}
    for leg in result["legs"]:
        if leg["robotId"] == "ugv":
            assert all(point[2] == 1.5 for point in leg["path"]["waypoints"])


def test_plugin_cannot_inject_a_path_with_a_different_start_or_robot():
    class WrongPlanner:
        def __init__(self, *_args): pass

        def plan(self, start, goal):
            return {"schemaVersion": "navigation.path.v1", "robotId": "foreign",
                    "waypoints": [start, goal]}
    result = plan_coverage_request(request(), planner_factory=WrongPlanner)
    assert result["status"] == "partial" and result["legs"] == []
    assert result["diagnostics"][0]["rejectedPaths"]["PLUGIN_PATH_REJECTED"] > 0


def test_stationary_peer_envelopes_block_tangent_and_crossing_routes():
    p = policy()
    assert not _peer_clear({"waypoints": [pose(1.5), pose(4.5)]}, p, pose(3.), p)
    assert not _peer_clear({"waypoints": [pose(1.5, 2.8), pose(4.5, 2.8)]}, p, pose(3.), p)
    assert _peer_clear({"waypoints": [pose(1.5, 3.), pose(4.5, 3.)]}, p, pose(3.), p)


class CoveragePort:
    def __init__(self):
        self.document, self.position, self.now, self.sequence = volume(), pose(), 1000, 1
        self.measurements, self.commands, self.reports = 0, 0, []
        self.sensor = SensorModel(range_m=2., horizontal_fov_rad=math.pi/2, vertical_fov_rad=math.pi/2)
        self.lie = False

    def snapshot(self, robot):
        return SpatialSnapshot(copy.deepcopy(self.document), self.position[:], self.sequence, self.now, "localizer")

    def capture(self, robot):
        self.measurements += 1
        ids = raycast_observation(self.document, self.position, self.sensor, max_rays=25)["observedVoxelIds"]
        return {"sourceId": "camera", "sequence": self.measurements, "observedAtMs": self.now,
                "worldFrameRevision": "world-1", "geometryId": geometry_id(self.document),
                "classified": {} if self.lie else {i: self.document["cells"][i] for i in ids},
                "evidenceId": f"image-{self.measurements}"}

    def check_cancel(self): pass

    def execute(self, robot, path, task, deadline):
        self.commands += 1; self.sequence += 1; self.now += 1
        self.position = path["waypoints"][-1][:]
        digest = hashlib.sha256(json.dumps(path, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False).encode()).hexdigest()
        return DriverResult("succeeded", task, digest)

    def stop(self, *_args): return False

    def publish(self, report): self.reports.append(report)


@pytest.mark.parametrize("missing_measurement", [False, True])
def test_runner_only_completes_explicit_coverage_with_new_measurements(missing_measurement):
    port = CoveragePort(); port.lie = missing_measurement
    target = [flat(port.document, 1, 2, 2)]
    ledger = CoverageLedger("mission", "world-1", target, geometry_id=geometry_id(port.document))
    coordinator = TaskCoordinator("mission", "world-1")
    factory = lambda snapshot: SpatialCoverageExplorer(snapshot.volume, policy(), scope(port.document, target),
                     port.sensor, CoverageConfig(max_viewpoints=24, max_rays=25))
    runner = SpatialMissionRunner(factory, port, port, port, ledger, coordinator,
                                  clock_ms=lambda: port.now, max_steps=1)
    result = runner.step("uav")
    assert port.commands == 1
    if missing_measurement:
        assert result["reason"] == "succeeded" and ledger.summary()["coverage"] == 0.
    else:
        assert result["reason"] == "coverage_complete" and result["termination"] == "completed"
        assert result["coverage"]["coverage"] == 1.
        assert runner.step("uav")["termination"] == "completed"
        assert port.commands == 1


def test_runner_refuses_a_proposal_for_a_different_coverage_scope():
    port = CoveragePort()
    target = [flat(port.document, 1, 2, 2)]
    ledger = CoverageLedger("mission", "world-1", target, geometry_id=geometry_id(port.document))
    other = [flat(port.document, 1, 3, 2)]
    factory = lambda snapshot: SpatialCoverageExplorer(snapshot.volume, policy(), scope(port.document, other))
    runner = SpatialMissionRunner(factory, port, port, port, ledger, TaskCoordinator("mission", "world-1"),
                                  clock_ms=lambda: port.now)
    with pytest.raises(MissionError) as failure:
        runner.step("uav")
    assert failure.value.code == "COVERAGE_SCOPE_MISMATCH" and port.commands == 0
