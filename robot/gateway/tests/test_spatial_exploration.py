"""Ideal sensor and measured-space exploration tests, not vehicle simulation."""
import copy
import itertools
import json
import math

import pytest
from tangying_robot_gateway.spatial_exploration import (
    ExplorationConfig,
    SensorModel,
    SpatialExplorer,
    raycast_observation,
)
from tangying_robot_gateway.spatial_navigation import SpatialPathPlanner


def volume(shape=(7, 7, 6), *, fill=0):
    return {"schemaVersion": "navigation.volume.v1", "robotId": "aerial-a", "mapId": "scene",
            "mapRevision": "a"*64, "frameId": "world", "worldFrameRevision": "world-1",
            "calibrationRevision": "cal-1", "resolution": 1., "origin": [0., 0., 0.],
            "shape": list(shape), "cells": [fill]*math.prod(shape)}


def index(document, x, y, z):
    nx, ny, _ = document["shape"]
    return (z*ny+y)*nx+x


def set_cell(document, x, y, z, value):
    document["cells"][index(document, x, y, z)] = value


def policy(**overrides):
    return {"mode": "volume3d", "bodyHalfExtents": [.12, .12, .12], "margin": .02,
            "maxExpanded": 10000, "maxWaypoints": 1000, **overrides}


def pose(x=1.5, y=3.5, z=2.5, yaw=0.):
    return [x, y, z, math.cos(yaw/2), 0., 0., math.sin(yaw/2)]


def terrain(document, *, support=True):
    count = math.prod(document["shape"][:2])
    document["terrain"] = {"schemaVersion": "navigation.terrain.v1", "sourceId": "floor",
                           "observedAtUnixMs": 1234, "heights": [1. if support else None]*count,
                           "supported": [support]*count,
                           "slopeRadians": [0. if support else None]*count}
    return document


def ground_policy(**overrides):
    return policy(**{"mode": "terrain25d", "maxSlopeRad": .3, "maxStepM": .15,
                     "referenceHeightM": .5, **overrides})


def test_raycast_unknown_is_first_hit_and_not_transparent():
    document = volume()
    set_cell(document, 3, 3, 2, -1)
    result = raycast_observation(document, pose(), SensorModel(10., .01, .01), max_rays=1)
    assert result["observedVoxelIds"] == [index(document, x, 3, 2) for x in (1, 2, 3)]
    assert result["unknownVoxelIds"] == [index(document, 3, 3, 2)]
    assert index(document, 4, 3, 2) not in result["observedVoxelIds"]


def test_raycast_wall_hides_unknown_and_does_not_use_future_truth():
    document = volume()
    set_cell(document, 3, 3, 2, 100)
    set_cell(document, 4, 3, 2, -1)
    result = raycast_observation(document, pose(), SensorModel(10., .01, .01), max_rays=1)
    assert result["unknownVoxelIds"] == []
    assert result["occupiedVoxelIds"] == [index(document, 3, 3, 2)]
    assert result["observedVoxelIds"][-1] == index(document, 3, 3, 2)


def test_full_quaternion_rotates_sensor_optical_axis():
    document = volume()
    start = pose(2.5, 2.5, 2.5, math.pi/2)
    result = raycast_observation(document, start, SensorModel(1.1, .01, .01), max_rays=1)
    assert index(document, 2, 3, 2) in result["observedVoxelIds"]
    assert index(document, 3, 2, 2) not in result["observedVoxelIds"]
    pitched = [2.5, 2.5, 2.5, math.sqrt(.5), 0., -math.sqrt(.5), 0.]
    upward = raycast_observation(document, pitched, SensorModel(1.1, .01, .01), max_rays=1)
    assert index(document, 2, 2, 3) in upward["observedVoxelIds"]
    assert index(document, 3, 2, 2) not in upward["observedVoxelIds"]


def test_sensor_range_is_physical_and_does_not_reach_far_cells():
    document = volume()
    result = raycast_observation(document, pose(), SensorModel(.49, .01, .01), max_rays=1)
    assert result["observedVoxelIds"] == [index(document, 1, 3, 2)]


def test_diagonal_ray_does_not_pass_through_wall_corner():
    document = volume()
    set_cell(document, 2, 1, 2, 100)
    set_cell(document, 3, 3, 2, -1)
    result = raycast_observation(document, pose(1.5, 1.5, 2.5, math.pi/4),
                                 SensorModel(10., .01, .01), max_rays=1)
    assert index(document, 2, 1, 2) in result["occupiedVoxelIds"]
    assert index(document, 3, 3, 2) not in result["unknownVoxelIds"]


def test_ray_tangent_to_wall_face_cannot_see_through_closed_contact():
    document = volume()
    set_cell(document, 2, 2, 2, 100)
    set_cell(document, 4, 3, 2, -1)
    result = raycast_observation(document, pose(y=3.), SensorModel(10., .01, .01), max_rays=1)
    assert index(document, 2, 2, 2) in result["occupiedVoxelIds"]
    assert index(document, 4, 3, 2) not in result["unknownVoxelIds"]


def test_raycast_is_deterministic_json_serializable_and_does_not_mutate_map():
    document = volume()
    before = copy.deepcopy(document)
    first = raycast_observation(document, pose(), SensorModel(), max_rays=17)
    assert first == raycast_observation(document, pose(), SensorModel(), max_rays=17)
    assert first == json.loads(json.dumps(first))
    assert document == before
    assert first["raysCast"] == 17


def test_raycast_steps_and_ray_budgets_are_hard_bounds():
    result = raycast_observation(volume(), pose(), SensorModel(100.),
                                 max_rays=81, max_ray_steps=2)
    assert result["raySteps"] <= 2
    assert result["raysCast"] <= 81
    assert result["budgetExhausted"] is True


def test_vertical_frontiers_are_observable_in_volume_mode_and_paths_are_certified():
    document = volume()
    nx, ny, _ = document["shape"]
    for x, y in itertools.product(range(nx), range(ny)):
        set_cell(document, x, y, 4, -1)
    start = pose(z=2.5)
    result = SpatialExplorer(document, policy(), SensorModel(4., .4, .4)).propose(start)
    assert result["status"] == "candidate"
    assert any(abs(candidate["pose"][5]) > .1 for candidate in result["candidates"])
    for candidate in result["candidates"]:
        assert candidate["informationGain"] > 0
        assert all(document["cells"][item] == -1 for item in candidate["visibleUnknownVoxelIds"])
        planner = SpatialPathPlanner(document, policy())
        assert all(planner.segment_clear(first[:3], second[:3]) for first, second in
                   itertools.pairwise(candidate["path"]["waypoints"]))


def test_ground_robot_requires_explicit_measured_support():
    document = terrain(volume(), support=False)
    set_cell(document, 4, 3, 1, -1)
    result = SpatialExplorer(document, ground_policy()).propose(pose(z=1.5))
    assert result["status"] == "no_candidate"
    assert result["diagnostics"]["rejectedPaths"]["LOCALIZATION_NOT_CLEAR"] > 0
    with pytest.raises(ValueError, match="terrain25d"):
        SpatialExplorer(volume(), ground_policy())


def test_ground_viewpoint_search_uses_surface_height_and_yaw_only():
    document = terrain(volume())
    for y in range(1, 6):
        set_cell(document, 5, y, 1, -1)
    result = SpatialExplorer(document, ground_policy()).propose(pose(z=1.5))
    assert result["status"] == "candidate"
    assert all(candidate["pose"][2] == 1.5 and candidate["pose"][4:6] == [0., 0.]
               for candidate in result["candidates"])


def test_excluded_target_is_not_returned_and_does_not_consume_candidate_budget():
    document = volume()
    for y in range(7):
        set_cell(document, 5, y, 2, -1)
    explorer = SpatialExplorer(document, policy(), config=ExplorationConfig(max_candidates=8))
    first = explorer.propose(pose())
    excluded = {candidate["targetId"] for candidate in first["candidates"]}
    assert excluded
    second = explorer.propose(pose(), excluded_targets=excluded)
    assert not excluded.intersection(candidate["targetId"] for candidate in second["candidates"])
    assert second["diagnostics"]["evaluatedCandidates"] <= 8


def test_already_observed_unknown_targets_have_no_gain():
    document = volume()
    set_cell(document, 4, 3, 2, -1)
    result = SpatialExplorer(document, policy()).propose(pose(),
                        observed_targets=[index(document, 4, 3, 2)])
    assert result["status"] == "no_candidate"


def test_target_identity_survives_map_snapshot_change_but_not_world_change():
    document = volume()
    set_cell(document, 4, 3, 2, -1)
    first = SpatialExplorer(document, policy()).propose(pose())
    document["mapRevision"] = "b"*64
    second = SpatialExplorer(document, policy()).propose(pose())
    assert first["candidate"]["targetId"] == second["candidate"]["targetId"]
    assert first["candidate"]["path"]["mapRevision"] != second["candidate"]["path"]["mapRevision"]
    document["worldFrameRevision"] = "world-2"
    third = SpatialExplorer(document, policy()).propose(pose())
    assert third["candidate"]["targetId"] != second["candidate"]["targetId"]


def test_target_identity_is_shared_across_robots_local_maps_and_calibrations():
    from tangying_robot_gateway.spatial_mission import geometry_id

    document = volume()
    set_cell(document, 4, 3, 2, -1)
    first = SpatialExplorer(document, policy()).propose(pose())
    document.update(robotId="aerial-b", mapId="local-map-b", calibrationRevision="b-calibration")
    second = SpatialExplorer(document, policy()).propose(pose())
    assert first["candidate"]["targetId"] == second["candidate"]["targetId"]
    assert first["diagnostics"]["geometryId"] == geometry_id(document)
    assert second["candidate"]["path"]["robotId"] == "aerial-b"
    assert second["candidate"]["path"]["calibrationRevision"] == "b-calibration"


def test_equivalent_integer_and_float_grid_coordinates_keep_target_identity():
    document = volume()
    set_cell(document, 4, 3, 2, -1)
    first = SpatialExplorer(document, policy()).propose(pose())
    document.update(resolution=1, origin=[0, 0, 0])
    second = SpatialExplorer(document, policy()).propose(pose())
    assert first["candidate"]["targetId"] == second["candidate"]["targetId"]
    assert first["diagnostics"]["geometryId"] == second["diagnostics"]["geometryId"]


def test_global_reposition_can_use_known_detour_while_direct_ablation_cannot():
    document = volume((9, 9, 5))
    for y, z in itertools.product(range(1, 8), range(5)):
        set_cell(document, 4, y, z, 100)
    for y, z in itertools.product(range(3, 6), range(1, 4)):
        set_cell(document, 8, y, z, -1)
    start = pose(1.5, 4.5, 2.5)
    common = {"max_candidates": 512, "max_plan_calls": 32, "max_rays": 1}
    enabled = SpatialExplorer(document, policy(), SensorModel(2., .2, .2),
                              ExplorationConfig(**common)).propose(start)
    direct = SpatialExplorer(document, policy(), SensorModel(2., .2, .2),
                              ExplorationConfig(use_global_reposition=False, **common)).propose(start)
    assert enabled["status"] == "candidate"
    assert direct["status"] == "no_candidate"
    candidate = enabled["candidate"]
    assert candidate["pathLengthM"] > math.dist(start[:3], candidate["pose"][:3])
    assert direct["diagnostics"]["rejectedPaths"]["DIRECT_REPOSITION_BLOCKED"] > 0


def test_proposals_are_deterministic_and_bounded_and_no_candidate_does_not_claim_coverage():
    document = volume()
    for y, z in itertools.product(range(7), range(6)):
        set_cell(document, 5, y, z, -1)
    config = ExplorationConfig(max_candidates=9, max_rays=9, max_ray_steps=90, max_plan_calls=3)
    explorer = SpatialExplorer(document, policy(), config=config)
    first = explorer.propose(pose())
    assert first == explorer.propose(pose())
    assert first == json.loads(json.dumps(first))
    diagnostic = first["diagnostics"]
    assert diagnostic["evaluatedCandidates"] <= 9
    assert diagnostic["planCalls"] <= 3
    assert diagnostic["raysCast"] <= 9*9
    assert diagnostic["raySteps"] <= 9*90
    empty = SpatialExplorer(volume(), policy()).propose(pose())
    assert empty["candidate"] is None
    assert empty["diagnostics"]["completeness"] == "bounded_candidates_not_scene_completion"


@pytest.mark.parametrize("visibility_gain", [True, False])
@pytest.mark.parametrize("strategy", ["information_gain", "nearest_frontier"])
def test_all_ablation_modes_keep_identical_safety_and_first_hit_observation(visibility_gain, strategy):
    document = volume()
    for y, z in itertools.product(range(7), range(6)):
        set_cell(document, 4, y, z, 100)
        set_cell(document, 5, y, z, -1)
    result = SpatialExplorer(document, policy(), config=ExplorationConfig(
                              visibility_gain=visibility_gain, strategy=strategy)).propose(pose())
    assert result["status"] == "no_candidate"


@pytest.mark.parametrize("kwargs", [{"range_m": 0}, {"range_m": True}, {"range_m": math.nan},
                                     {"range_m": 10**1000}, {"horizontal_fov_rad": 0},
                                     {"vertical_fov_rad": 4.}])
def test_sensor_model_rejects_nonfinite_uncommissioned_values(kwargs):
    with pytest.raises(ValueError):
        SensorModel(**kwargs)


@pytest.mark.parametrize("kwargs", [{"max_candidates": 0}, {"max_rays": True},
                                     {"max_ray_steps": 1_000_001}, {"strategy": "oracle"},
                                     {"use_global_reposition": 1}, {"visibility_gain": "false"}])
def test_configuration_rejects_unbounded_or_ambiguous_modes(kwargs):
    with pytest.raises(ValueError):
        ExplorationConfig(**kwargs)


def test_quaternion_and_target_contract_validation():
    with pytest.raises(ValueError, match="quaternion"):
        raycast_observation(volume(), [1., 1., 1., 0., 0., 0., 0.], SensorModel())
    explorer = SpatialExplorer(volume(), policy())
    with pytest.raises(ValueError, match="voxel IDs"):
        explorer.propose(pose(), observed_targets=[-1])
    with pytest.raises(ValueError, match="voxel IDs"):
        explorer.propose(pose(), observed_targets=[1, True])
    with pytest.raises(ValueError, match="targetId"):
        explorer.propose(pose(), excluded_targets=[123])
    with pytest.raises(ValueError, match="bounded"):
        explorer.propose(pose(), observed_targets=iter(range(10)))
