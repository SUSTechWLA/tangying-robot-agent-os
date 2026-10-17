"""Independent multilevel coverage integration, without simulator or robot motion."""
import copy
import itertools
import math

import pytest
from tangying_robot_gateway.spatial_coverage import (
    CoverageConfig,
    SpatialCoverageExplorer,
    plan_coverage_request,
)
from tangying_robot_gateway.spatial_exploration import SensorModel
from tangying_robot_gateway.spatial_mission import (
    CoverageLedger,
    MissionError,
    SpatialMissionRunner,
    SpatialSnapshot,
    TaskCoordinator,
    geometry_id,
)
from tangying_robot_gateway.spatial_navigation import SpatialPathPlanner


def fixture(*, connections=True, corridor=False):
    shape = [7, 5, 6]
    count = shape[0] * shape[1]
    layers = []
    for name in ("lower", "ramp", "upper"):
        heights = []
        for y, x in itertools.product(range(shape[1]), range(shape[0])):
            height = (1. if name == "lower" else (1.+(x-1)*.5 if name == "ramp"
                       and 2 <= x <= 5 else 3. if name == "upper" and x == 6 else None))
            heights.append(None if corridor and y != 2 else height)
        layers.append({"layerId": name, "heights": heights,
                       "supported": [height is not None for height in heights],
                       "slopeRadians": [math.atan(.5) if name == "ramp" and height is not None
                                         else 0. if height is not None else None for height in heights]})
    rows = [2] if corridor else range(shape[1])
    edges = ([{"from": ["lower", 1, y], "to": ["ramp", 2, y]} for y in rows]
             + [{"from": ["ramp", 5, y], "to": ["upper", 6, y]} for y in rows])
    volume = {"schemaVersion": "navigation.volume.v1", "robotId": "ugv", "mapId": "layers",
        "mapRevision": "measured-1", "frameId": "world", "worldFrameRevision": "world-1",
        "calibrationRevision": "ground-calibration", "resolution": 1., "origin": [0., 0., 0.],
        "shape": shape, "cells": [0]*math.prod(shape), "terrainLayers": {
            "schemaVersion": "navigation.terrain.layers.v1", "sourceId": "survey", "observedAtUnixMs": 1,
            "layers": layers, "connections": edges if connections else []}}
    assert len(layers[0]["heights"]) == count
    policy = {"mode": "terrain_layered", "bodyHalfExtents": [.1, .1, .1], "margin": .02,
              "maxExpanded": 1000, "maxWaypoints": 256, "maxSlopeRad": .6, "maxStepM": .6,
              "referenceHeightM": .5}
    target = (3 * shape[1] + 2) * shape[0] + 6
    return {"schemaVersion": "coverage.request.v1", "missionId": "layered-inspection",
        "scope": {"schemaVersion": "coverage.scope.v1", "geometryId": geometry_id(volume),
                  "targetVoxelIds": [target]},
        "config": {"max_viewpoints": 512, "max_plan_calls": 16, "max_legs": 3,
                   "max_rays": 1, "max_total_viewpoint_evaluations": 1600},
        "robots": [{"volume": volume, "policy": policy, "pose": [1.5, 2.5, 1.5, 1., 0., 0., 0.],
                    "sensor": {"range_m": .6, "horizontal_fov_rad": .01, "vertical_fov_rad": .01}}]}


def test_explicit_coverage_can_reach_upper_observation_pose_only_via_measured_ramp():
    request = fixture()
    result = plan_coverage_request(request)
    assert result["status"] == "predicted_complete"
    assert result["observedCoverage"] is None and result["predictionOnly"] is True
    assert result["physicalValidation"] is False
    assert len(result["legs"]) == 1
    leg = result["legs"][0]
    assert leg["path"]["mode"] == "terrain_layered"
    assert {2., 2.5, 3., 3.5} <= {point[2] for point in leg["path"]["waypoints"]}
    certifier = SpatialPathPlanner(request["robots"][0]["volume"], request["robots"][0]["policy"])
    assert all(certifier.segment_clear(a[:3], b[:3]) for a, b in itertools.pairwise(leg["path"]["waypoints"]))


def test_upper_targets_remain_unresolved_when_no_cross_layer_connection_was_measured():
    request = fixture(connections=False)
    result = plan_coverage_request(request)
    assert result["status"] == "partial"
    assert result["predictedCoveredVoxels"] == 0 and result["legs"] == []
    assert result["unresolvedVoxelIds"] == request["scope"]["targetVoxelIds"]


def test_plugin_straight_line_cannot_replace_supporting_ramp_with_flying_shortcut():
    class ShortcutPlanner:
        def __init__(self, volume, policy):
            self.volume, self.policy = volume, policy

        def plan(self, start, goal):
            return {"schemaVersion": "navigation.path.v1", "mode": self.policy["mode"],
                    **{key: self.volume[key] for key in ("robotId", "mapId", "mapRevision", "frameId",
                       "worldFrameRevision", "calibrationRevision")}, "waypoints": [start, goal]}

    result = plan_coverage_request(fixture(), planner_factory=ShortcutPlanner)
    assert result["status"] == "partial" and result["legs"] == []
    assert result["diagnostics"][0]["rejectedPaths"]["PLUGIN_PATH_REJECTED"] > 0


def test_successive_plugin_calls_cannot_mutate_previously_certified_paths():
    class ReusingPlanner:
        def __init__(self, volume, policy):
            self.planner = SpatialPathPlanner(volume, policy)
            self.shared = {}

        def plan(self, start, goal):
            self.shared.clear()
            self.shared.update(self.planner.plan(start, goal))
            return self.shared

    request = fixture()
    robot = request["robots"][0]
    own_cell = (1 * 5 + 2) * 7 + 1
    scope = {**request["scope"], "targetVoxelIds": [own_cell]}
    explorer = SpatialCoverageExplorer(robot["volume"], robot["policy"], scope,
        SensorModel(**robot["sensor"]), CoverageConfig(max_viewpoints=12, max_plan_calls=8, max_rays=1),
        planner_factory=ReusingPlanner)
    proposal = explorer.propose(robot["pose"])
    assert len(proposal["candidates"]) >= 2
    for candidate in proposal["candidates"]:
        assert candidate["path"]["waypoints"][-1] == candidate["pose"]
        length = sum(math.dist(a[:3], b[:3]) for a, b in itertools.pairwise(candidate["path"]["waypoints"]))
        assert candidate["pathLengthM"] == length


def test_parked_peer_blocks_only_available_layered_corridor_without_fake_completion():
    request = fixture(corridor=True)
    ground = request["robots"][0]
    aerial = copy.deepcopy(ground)
    aerial["volume"].update(robotId="parked-uav", calibrationRevision="air-calibration")
    aerial["volume"]["cells"][request["scope"]["targetVoxelIds"][0]] = -1
    aerial["policy"] = {key: value for key, value in aerial["policy"].items()
                        if key not in {"maxSlopeRad", "maxStepM", "referenceHeightM"}}
    aerial["policy"]["mode"] = "volume3d"
    aerial["pose"] = [3.5, 2.5, 2.5, 1., 0., 0., 0.]
    aerial["sensor"]["range_m"] = 1e-6
    request["robots"].append(aerial)
    result = plan_coverage_request(request)
    assert result["status"] == "partial" and result["legs"] == []
    assert sum(detail["peerRefusals"] for detail in result["diagnostics"]) > 0
    assert result["observedCoverage"] is None


def test_total_travel_budget_cannot_be_bypassed_by_multilevel_route_length():
    request = fixture()
    request["config"]["max_travel_m"] = .5
    result = plan_coverage_request(request)
    assert result["status"] == "partial" and result["legs"] == []
    assert result["pathLengthM"] == 0
    assert sum(detail["travelRefusals"] for detail in result["diagnostics"]) > 0


@pytest.mark.parametrize("mutation", ["missing_scope", "expected_outside_scope"])
def test_layered_runner_rejects_unbound_or_expanded_prediction_scope_before_dispatch(mutation):
    request = fixture()
    robot = request["robots"][0]

    class Port:
        captures = 0
        commands = 0

        def snapshot(self, _robot):
            return SpatialSnapshot(copy.deepcopy(robot["volume"]), robot["pose"][:], 1, 1000, "localizer")

        def capture(self, _robot):
            self.captures += 1
            return {"sourceId": "camera", "sequence": self.captures, "observedAtMs": 1000,
                    "worldFrameRevision": "world-1", "geometryId": request["scope"]["geometryId"],
                    "classified": {}, "evidenceId": f"capture-{self.captures}"}

        def check_cancel(self):
            return None

        def execute(self, *_args):
            self.commands += 1
            raise AssertionError("unbound scope must never reach the driver")

        def publish(self, _event):
            return None

    class BadScopePolicy:
        def propose(self, start, **kwargs):
            explorer = SpatialCoverageExplorer(robot["volume"], robot["policy"], request["scope"],
                SensorModel(**robot["sensor"]), CoverageConfig(**request["config"]))
            proposal = explorer.propose(start, **kwargs)
            assert proposal["candidates"]
            if mutation == "missing_scope":
                proposal.pop("scope")
            else:
                proposal["candidates"][0]["expectedObservedVoxelIds"].append(0)
            return proposal

    port = Port()
    ledger = CoverageLedger(request["missionId"], "world-1", request["scope"]["targetVoxelIds"],
                            geometry_id=request["scope"]["geometryId"])
    runner = SpatialMissionRunner(lambda _snapshot: BadScopePolicy(), port, port, port, ledger,
        TaskCoordinator(request["missionId"], "world-1"), clock_ms=lambda: 1000)
    with pytest.raises(MissionError) as failure:
        runner.step("ugv")
    assert failure.value.code == "COVERAGE_SCOPE_MISMATCH"
    assert port.commands == 0 and ledger.summary()["coverage"] == 0.
