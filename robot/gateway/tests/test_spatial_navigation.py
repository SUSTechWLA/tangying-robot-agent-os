"""Geometric admission tests; none of these simulate vehicle dynamics."""
import copy
import itertools
import json
import math

import numpy as np
import pytest
from tangying_robot_gateway.spatial_navigation import (
    SpatialNavigationError,
    SpatialPathPlanner,
    validate_policy,
    validate_volume,
)


def volume(cells=None):
    cells = np.zeros((8, 10, 10), dtype=int) if cells is None else cells
    return {"schemaVersion": "navigation.volume.v1", "robotId": "robot-a", "mapId": "warehouse",
            "mapRevision": "a" * 64, "frameId": "world", "worldFrameRevision": "world-1",
            "calibrationRevision": "cal-1", "resolution": .5, "origin": [0., 0., 0.],
            "shape": list(cells.shape[::-1]), "cells": cells.ravel().tolist()}


def policy(**overrides):
    return {"mode": "volume3d", "bodyHalfExtents": [.1, .1, .1], "margin": .02,
            "maxExpanded": 10000, "maxWaypoints": 1000, **overrides}


def pose(x, y, z, yaw=0.):
    return [x, y, z, math.cos(yaw / 2), 0., 0., math.sin(yaw / 2)]


def terrain_volume(heights=None, *, supported=None, slopes=None):
    result = volume()
    count = result["shape"][0] * result["shape"][1]
    result["terrain"] = {"schemaVersion": "navigation.terrain.v1", "sourceId": "measured-floor",
                         "observedAtUnixMs": 123456789,
                         "heights": heights if heights is not None else [0.] * count,
                         "supported": supported if supported is not None else [True] * count,
                         "slopeRadians": slopes if slopes is not None else [0.] * count}
    return result


def terrain_policy(**overrides):
    return policy(**{"mode": "terrain25d", "maxSlopeRad": .3, "maxStepM": .15,
                     "referenceHeightM": .5, **overrides})


def _assert_every_swept_box_avoids_obstacles(document, route, config):
    """Independent oracle: scipy linear feasibility for segment/expanded voxel."""
    from scipy.optimize import linprog

    cells = np.asarray(document["cells"]).reshape(tuple(document["shape"][::-1]))
    half = np.asarray(config["bodyHalfExtents"]) + config["margin"]
    for first, second in itertools.pairwise(route["waypoints"]):
        a, b = np.asarray(first[:3]), np.asarray(second[:3])
        for z, y, x in np.argwhere(cells != 0):
            lower = np.asarray(document["origin"]) + np.array([x, y, z]) * document["resolution"] - half
            upper = lower + document["resolution"] + 2 * half
            feasible = linprog([0.], A_ub=np.concatenate(((b - a)[:, None], (a - b)[:, None])),
                              b_ub=np.concatenate((upper - a, a - lower)), bounds=[(0., 1.)],
                              method="highs")
            assert not feasible.success, (first, second, [x, y, z])


def test_volume_air_route_climbs_over_wall_and_preserves_complete_endpoint_poses():
    cells = np.zeros((8, 8, 8), dtype=int)
    cells[:4, :, 4] = 100
    document, config = volume(cells), policy()
    start = [1.25, 1.25, .75, math.cos(.2), math.sin(.2), 0., 0.]
    goal = [3.25, 1.25, .75, math.cos(.3), 0., math.sin(.3), 0.]
    planner = SpatialPathPlanner(document, config)
    result = planner.plan(start, goal)
    assert result["mode"] == "volume3d"
    assert result["waypoints"][0] == start
    assert result["waypoints"][-1] == goal
    assert max(waypoint[2] for waypoint in result["waypoints"]) > 2.
    assert result["expandedNodes"] > 0
    assert result["mapRevision"] == document["mapRevision"]
    assert json.loads(json.dumps(result)) == result
    assert planner.plan(start, goal) == result
    _assert_every_swept_box_avoids_obstacles(document, result, config)


@pytest.mark.parametrize("blocking", [-1, 100])
def test_unknown_ceiling_or_wall_is_never_assumed_flyable(blocking):
    cells = np.zeros((8, 8, 8), dtype=int)
    cells[:, :, 4] = blocking
    with pytest.raises(SpatialNavigationError) as error:
        SpatialPathPlanner(volume(cells), policy()).plan(pose(1.25, 1.25, .75), pose(3.25, 1.25, .75))
    assert error.value.code == "NO_KNOWN_PATH"


def test_air_route_cannot_cut_diagonally_between_unknown_voxels():
    cells = np.full((5, 5, 5), -1, dtype=int)
    cells[1, 1, 1] = cells[2, 2, 2] = 0
    planner = SpatialPathPlanner(volume(cells), policy())
    assert not planner.segment_clear([.75, .75, .75], [1.25, 1.25, 1.25])
    with pytest.raises(SpatialNavigationError) as error:
        planner.plan(pose(.75, .75, .75), pose(1.25, 1.25, 1.25))
    assert error.value.code == "NO_KNOWN_PATH"


@pytest.mark.parametrize("axis", [0, 1, 2])
@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("gap", [-1e-6, 0., 1e-6])
def test_box_contact_is_blocked_in_all_six_directions(axis, direction, gap):
    cells = np.zeros((8, 8, 8), dtype=int)
    cells[3, 3, 3] = 100
    config = policy(bodyHalfExtents=[.25, .25, .25], margin=0.)
    point = [1.75, 1.75, 1.75]
    point[axis] += direction * (.25 + .25 + gap)
    planner = SpatialPathPlanner(volume(cells), config)
    assert planner.segment_clear(point, point) is (gap > 0)


def test_whole_body_must_fit_not_just_center_and_outside_survey_is_blocked():
    cells = np.zeros((8, 8, 8), dtype=int)
    cells[3, 3, 3] = 100
    planner = SpatialPathPlanner(volume(cells), policy(bodyHalfExtents=[.3, .1, .1]))
    assert not planner.segment_clear([1.2, 1.75, 1.75], [1.2, 1.75, 1.75])
    assert not planner.segment_clear([.3, 1., 1.], [.3, 1., 1.])
    assert planner.segment_clear([.321, 1., 1.], [.321, 1., 1.])


def test_planner_and_validators_detach_caller_owned_data():
    document, config = volume(), policy()
    frozen = validate_volume(document)
    planner = SpatialPathPlanner(document, config)
    document["origin"][0] = 100.
    document["cells"][:] = [-1] * len(document["cells"])
    config["bodyHalfExtents"][0] = 100.
    result = planner.plan(pose(.75, .75, .75), pose(2.75, 2.75, 2.75))
    assert frozen["origin"][0] == 0.
    assert result["waypoints"] == [pose(.75, .75, .75), pose(2.75, 2.75, 2.75)]
    result["waypoints"][0][0] = 100.
    assert planner.plan(pose(.75, .75, .75), pose(2.75, 2.75, 2.75))["waypoints"][0][0] == .75


@pytest.mark.parametrize("changes", [
    {"schemaVersion": "future"}, {"robotId": ""}, {"frameId": "map"},
    {"worldFrameRevision": "bad id"}, {"resolution": 0}, {"resolution": True},
    {"resolution": float("nan")}, {"resolution": 10**1000}, {"shape": [True, 10, 8]},
    {"shape": [100, 100, 100]}, {"shape": [0, 10, 8]}, {"origin": [0, 0, float("inf")]},
    {"origin": [1e100, 0, 0]}, {"cells": [0]}, {"extra": "ignored?"},
])
def test_invalid_volume_is_rejected_before_planning(changes):
    with pytest.raises(ValueError):
        validate_volume({**volume(), **changes})


@pytest.mark.parametrize("bad", [False, .0, 1, 65, -2, 101, None])
def test_only_exact_trinary_integers_are_valid_cells(bad):
    document = volume()
    document["cells"][0] = bad
    with pytest.raises(ValueError):
        validate_volume(document)


@pytest.mark.parametrize("changes", [
    {"mode": "se3"}, {"bodyHalfExtents": [.1, 0, .1]}, {"margin": -.1},
    {"maxExpanded": True}, {"maxExpanded": 250001}, {"maxWaypoints": 1},
    {"maxWaypoints": 4097}, {"maxStepM": .1}, {"bodyHalfExtents": [True, 1, 1]},
])
def test_invalid_or_ambiguous_policy_is_rejected(changes):
    with pytest.raises(ValueError):
        validate_policy(policy(**changes))


def test_budget_failure_does_not_return_partial_or_uncertified_path():
    cells = np.zeros((8, 8, 8), dtype=int)
    cells[:4, :, 4] = 100
    for config in (policy(maxExpanded=1), policy(maxWaypoints=2)):
        with pytest.raises(SpatialNavigationError) as error:
            SpatialPathPlanner(volume(cells), config).plan(pose(1.25, 1.25, .75), pose(3.25, 1.25, .75))
        assert error.value.code == "PLANNING_BUDGET_EXCEEDED"


def test_terrain_route_requires_support_across_full_route_even_when_air_is_free():
    document = terrain_volume()
    for row in range(10):
        offset = row * 10 + 5
        document["terrain"]["supported"][offset] = False
        document["terrain"]["heights"][offset] = None
        document["terrain"]["slopeRadians"][offset] = None
    with pytest.raises(SpatialNavigationError) as error:
        SpatialPathPlanner(document, terrain_policy()).plan(pose(.75, .75, .5), pose(3.75, .75, .5))
    assert error.value.code == "NO_KNOWN_PATH"
    assert SpatialPathPlanner(document, policy()).plan(pose(.75, .75, .5), pose(3.75, .75, .5))


def test_terrain_route_follows_measured_height_and_refuses_excessive_steps():
    heights = [0. if column < 5 else .1 for _row in range(10) for column in range(10)]
    document = terrain_volume(heights)
    result = SpatialPathPlanner(document, terrain_policy()).plan(pose(.75, .75, .5), pose(3.75, .75, .6))
    assert result["mode"] == "terrain25d"
    assert result["waypoints"][0][2] == .5
    assert result["waypoints"][-1][2] == .6
    document["terrain"]["heights"] = [0. if column < 5 else .4 for _row in range(10) for column in range(10)]
    with pytest.raises(SpatialNavigationError) as error:
        SpatialPathPlanner(document, terrain_policy()).plan(pose(.75, .75, .5), pose(3.75, .75, .9))
    assert error.value.code == "NO_KNOWN_PATH"


def test_terrain_slope_and_support_under_entire_body_are_required():
    document = terrain_volume()
    document["terrain"]["slopeRadians"][2 * 10 + 2] = .4
    with pytest.raises(SpatialNavigationError) as error:
        SpatialPathPlanner(document, terrain_policy()).plan(pose(1.25, 1.25, .5), pose(3.75, .75, .5))
    assert error.value.code == "LOCALIZATION_NOT_CLEAR"
    document = terrain_volume()
    document["terrain"]["supported"][2 * 10 + 2] = False
    document["terrain"]["heights"][2 * 10 + 2] = None
    document["terrain"]["slopeRadians"][2 * 10 + 2] = None
    planner = SpatialPathPlanner(document, terrain_policy(bodyHalfExtents=[.3, .3, .1]))
    assert not planner.segment_clear([.9, 1.25, .5], [.9, 1.25, .5])


def test_ground_cannot_use_arbitrary_flight_height_or_hop_over_low_ceiling():
    document = terrain_volume()
    planner = SpatialPathPlanner(document, terrain_policy())
    with pytest.raises(SpatialNavigationError) as error:
        planner.plan(pose(.75, .75, 2.), pose(3.75, .75, .5))
    assert error.value.code == "LOCALIZATION_NOT_CLEAR"
    cells = np.asarray(document["cells"]).reshape((8, 10, 10))
    cells[1, :, 5] = 100
    document["cells"] = cells.ravel().tolist()
    with pytest.raises(SpatialNavigationError) as error:
        SpatialPathPlanner(document, terrain_policy()).plan(pose(.75, .75, .5), pose(3.75, .75, .5))
    assert error.value.code == "NO_KNOWN_PATH"


@pytest.mark.parametrize("field,value", [
    ("supported", [1] * 100), ("heights", [None] * 100), ("slopeRadians", [None] * 100),
    ("heights", [float("nan")] * 100), ("slopeRadians", [-1] * 100),
    ("heights", [50.] * 100), ("observedAtUnixMs", 0), ("sourceId", ""),
])
def test_terrain_is_explicit_measured_evidence(field, value):
    document = terrain_volume()
    document["terrain"][field] = value
    with pytest.raises(ValueError):
        validate_volume(document)


def test_terrain_cannot_be_inferred_from_free_volume_and_null_support_is_unambiguous():
    with pytest.raises(ValueError, match="explicit measured terrain"):
        SpatialPathPlanner(volume(), terrain_policy())
    document = terrain_volume()
    document["terrain"]["supported"][0] = False
    with pytest.raises(ValueError, match="null"):
        validate_volume(document)


@pytest.mark.parametrize("bad", [pose(1, 1, 1)[:6], [1., 1., 1., 2., 0., 0., 0.],
                                  [True, 1., 1., 1., 0., 0., 0.]])
def test_invalid_pose_is_not_silently_flattened_or_normalized(bad):
    with pytest.raises(ValueError):
        SpatialPathPlanner(volume(), policy()).plan(bad, pose(2, 2, 2))


def test_same_position_rotation_does_not_invent_a_translation():
    start, goal = pose(.75, .75, .75), pose(.75, .75, .75, 1.2)
    assert SpatialPathPlanner(volume(), policy()).plan(start, goal)["waypoints"] == [start, goal]


def test_terrain_input_is_deeply_detached():
    original = terrain_volume()
    detached = validate_volume(original)
    expected = copy.deepcopy(detached)
    original["terrain"]["heights"][0] = 3.
    assert detached == expected


def test_public_snapshot_cannot_relabel_collision_map_or_expand_robot_permissions():
    planner = SpatialPathPlanner(terrain_volume(), terrain_policy())
    planner.volume["mapRevision"] = "a-different-map"
    planner.policy["maxStepM"] = 100.
    planner.policy["bodyHalfExtents"][0] = .0001
    result = planner.plan(pose(.75, .75, .5), pose(3.75, .75, .5))
    assert result["mapRevision"] == "a" * 64
    assert planner.policy["maxStepM"] == .15
    assert planner.policy["bodyHalfExtents"][0] == .1


def test_large_step_permission_does_not_authorize_hovering_ground_endpoints():
    planner = SpatialPathPlanner(terrain_volume(), terrain_policy(maxStepM=2.))
    with pytest.raises(SpatialNavigationError) as error:
        planner.plan(pose(.75, .75, 1.5), pose(3.75, .75, .5))
    assert error.value.code == "LOCALIZATION_NOT_CLEAR"


def test_ground_path_retains_valley_support_nodes_instead_of_flying_across():
    heights = [1.5 if column < 3 or column > 6 else 0.
               for _row in range(10) for column in range(10)]
    document = terrain_volume(heights)
    planner = SpatialPathPlanner(document, terrain_policy(maxStepM=2.))
    start, goal = pose(.75, .75, 2.), pose(4.25, .75, 2.)
    assert not planner.segment_clear(start[:3], goal[:3])
    result = planner.plan(start, goal)
    assert min(waypoint[2] for waypoint in result["waypoints"]) == .5
    for waypoint in result["waypoints"]:
        x, y = (math.floor(value / .5) for value in waypoint[:2])
        assert waypoint[2] == pytest.approx(heights[y * 10 + x] + .5)


def layered_policy(**overrides):
    return terrain_policy(mode="terrain_layered", **overrides)


def _layer(layer_id, height_at):
    heights = [height_at(x, y) for y in range(10) for x in range(10)]
    return {"layerId": layer_id, "heights": heights,
            "supported": [height is not None for height in heights],
            "slopeRadians": [0. if height is not None else None for height in heights]}


def layered_volume(*, ramp=False, connections=True, slab=False):
    result = volume()
    lower = _layer("lower", lambda _x, _y: 0.)
    if ramp:
        connector = _layer("ramp", lambda x, _y: (x-2)*.25 if 3 <= x <= 6 else None)
        connector["slopeRadians"] = [math.atan(.5) if supported else None
                                     for supported in connector["supported"]]
        upper = _layer("upper", lambda x, _y: 1. if x >= 7 else None)
        layers = [lower, upper, connector]  # Labels do not encode height/order.
        edges = [{"from": ["lower", 2, y], "to": ["ramp", 3, y]} for y in range(10)]
        edges += [{"from": ["ramp", 6, y], "to": ["upper", 7, y]} for y in range(10)]
    else:
        layers = [lower, _layer("upper", lambda _x, _y: 1.5)]
        edges = []
    result["terrainLayers"] = {"schemaVersion": "navigation.terrain.layers.v1",
        "sourceId": "measured-multilevel-surfaces", "observedAtUnixMs": 123456789,
        "layers": layers, "connections": edges if connections else []}
    if slab:
        # A measured upper-floor slab occupies z=[1,1.5]; chassis routes below
        # and above it remain separated by actual occupied volume.
        result["cells"][2 * 100:3 * 100] = [100] * 100
    return result


def test_layered_support_keeps_two_overlapping_floors_and_preserves_poses():
    document, config = layered_volume(slab=True), layered_policy()
    planner = SpatialPathPlanner(document, config)
    for height in (.5, 2.):
        start, goal = pose(.75, .75, height, .1), pose(3.75, .75, height, .2)
        route = planner.plan(start, goal)
        assert route["mode"] == "terrain_layered"
        assert route["waypoints"] == [start, goal]
        assert planner.plan(start, goal) == route
        assert json.loads(json.dumps(route)) == route
        _assert_every_swept_box_avoids_obstacles(document, route, config)
    assert len(planner.volume["terrainLayers"]["layers"]) == 2


def test_layered_robot_cannot_change_floors_without_measured_connections_even_in_free_air():
    document = layered_volume()
    start, goal = pose(.75, .75, .5), pose(.75, .75, 2.)
    planner = SpatialPathPlanner(document, layered_policy(maxStepM=3.))
    assert not planner.segment_clear(start[:3], goal[:3])
    with pytest.raises(SpatialNavigationError) as error:
        planner.plan(start, goal)
    assert error.value.code == "NO_KNOWN_PATH"
    # The same measured free voxels do support a geometric aerial route.
    assert SpatialPathPlanner(document, policy()).plan(start, goal)["waypoints"] == [start, goal]


def test_layered_route_uses_explicit_ramp_seams_and_cannot_shortcut_between_floors():
    document, config = layered_volume(ramp=True), layered_policy(maxStepM=.3, maxSlopeRad=.5)
    planner = SpatialPathPlanner(document, config)
    start, goal = pose(.75, .75, .5), pose(4.25, .75, 1.5)
    assert not planner.segment_clear(start[:3], goal[:3])
    result = planner.plan(start, goal)
    heights = [point[2] for point in result["waypoints"]]
    assert .75 in heights and 1. in heights and 1.25 in heights
    assert all(planner.segment_clear(first[:3], second[:3]) for first, second in
               itertools.pairwise(result["waypoints"]))
    assert all(point[2] in (.5, .75, 1., 1.25, 1.5) for point in result["waypoints"])
    assert planner.plan(start, goal) == result
    reverse = planner.plan(goal, start)
    assert reverse["waypoints"][0] == goal and reverse["waypoints"][-1] == start


@pytest.mark.parametrize("missing", ["all", "lower-seam", "upper-seam"])
def test_layered_ramp_requires_both_explicit_connections(missing):
    document = layered_volume(ramp=True)
    edges = document["terrainLayers"]["connections"]
    document["terrainLayers"]["connections"] = ([] if missing == "all" else
        [edge for edge in edges if (edge["from"][0] != "lower" if missing == "lower-seam"
                                    else edge["to"][0] != "upper")])
    planner = SpatialPathPlanner(document, layered_policy(maxStepM=.3, maxSlopeRad=.5))
    with pytest.raises(SpatialNavigationError) as error:
        planner.plan(pose(.75, .75, .5), pose(4.25, .75, 1.5))
    assert error.value.code == "NO_KNOWN_PATH"


@pytest.mark.parametrize("overrides", [{"maxStepM": .1}, {"maxSlopeRad": .2}])
def test_layer_connections_never_override_step_or_slope_limits(overrides):
    config = {"maxStepM": .3, "maxSlopeRad": .5, **overrides}
    planner = SpatialPathPlanner(layered_volume(ramp=True), layered_policy(**config))
    with pytest.raises(SpatialNavigationError) as error:
        planner.plan(pose(.75, .75, .5), pose(4.25, .75, 1.5))
    assert error.value.code == "NO_KNOWN_PATH"


@pytest.mark.parametrize("blocking", [-1, 100])
def test_layered_connections_do_not_authorize_unknown_or_occupied_chassis_sweeps(blocking):
    document = layered_volume(ramp=True)
    cells = np.asarray(document["cells"]).reshape(8, 10, 10)
    cells[:, :, 4] = blocking
    document["cells"] = cells.ravel().tolist()
    planner = SpatialPathPlanner(document, layered_policy(maxStepM=.3, maxSlopeRad=.5))
    with pytest.raises(SpatialNavigationError) as error:
        planner.plan(pose(.75, .75, .5), pose(4.25, .75, 1.5))
    assert error.value.code == "NO_KNOWN_PATH"


def test_layered_support_rejects_hovering_and_ambiguous_surface_endpoints():
    document = layered_volume()
    planner = SpatialPathPlanner(document, layered_policy(maxStepM=3.))
    with pytest.raises(SpatialNavigationError) as error:
        planner.plan(pose(.75, .75, 1.), pose(3.75, .75, 2.))
    assert error.value.code == "LOCALIZATION_NOT_CLEAR"
    document["terrainLayers"]["layers"][1]["heights"] = [.01] * 100
    ambiguous = SpatialPathPlanner(document, layered_policy())
    with pytest.raises(SpatialNavigationError) as error:
        ambiguous.plan(pose(.75, .75, .5), pose(3.75, .75, .5))
    assert error.value.code == "LOCALIZATION_NOT_CLEAR"


def test_layered_robot_checks_support_under_entire_envelope_without_using_floor_below():
    document = layered_volume()
    upper = document["terrainLayers"]["layers"][1]
    index = 1 * 10 + 2
    upper["heights"][index], upper["supported"][index], upper["slopeRadians"][index] = None, False, None
    planner = SpatialPathPlanner(document, layered_policy(bodyHalfExtents=[.3, .1, .1]))
    assert not planner.segment_clear([.75, .75, 2.], [1.75, .75, 2.])
    assert planner.segment_clear([.75, .75, .5], [1.75, .75, .5])


def test_upper_floor_slab_or_low_ceiling_still_blocks_chassis_on_valid_layer():
    document = layered_volume(slab=True)
    planner = SpatialPathPlanner(document, layered_policy(bodyHalfExtents=[.1, .1, .6],
                                                         referenceHeightM=.7))
    with pytest.raises(SpatialNavigationError) as error:
        planner.plan(pose(.75, .75, .7), pose(3.75, .75, .7))
    assert error.value.code == "LOCALIZATION_NOT_CLEAR"


def test_layered_validation_and_planner_are_detached_from_input_mutations():
    document = layered_volume(ramp=True)
    planner = SpatialPathPlanner(document, layered_policy(maxStepM=.3, maxSlopeRad=.5))
    document["terrainLayers"]["layers"][0]["heights"][0] = 2.
    document["terrainLayers"]["connections"].clear()
    assert planner.volume["terrainLayers"]["layers"][0]["heights"][0] == 0.
    assert planner.volume["terrainLayers"]["connections"]
    assert planner.plan(pose(.75, .75, .5), pose(4.25, .75, 1.5))


@pytest.mark.parametrize("connection", [
    {"from": ["lower", 2, 1], "to": ["upper", 2, 1]},
    {"from": ["lower", 2, 1], "to": ["upper", 4, 1]},
    {"from": ["lower", 2, 1], "to": ["upper", 3, 2]},
    {"from": ["lower", 2, 1], "to": ["lower", 3, 1]},
    {"from": ["lower", 2, 1], "to": ["missing", 3, 1]},
    {"from": ["lower", 2, 1], "to": ["upper", True, 1]},
])
def test_layered_validation_rejects_vertical_portals_nonadjacent_and_unknown_layer_links(connection):
    document = layered_volume()
    document["terrainLayers"]["connections"] = [connection]
    with pytest.raises(ValueError):
        validate_volume(document)


def test_layered_validation_rejects_duplicate_edges_and_unmeasured_links():
    document = layered_volume(ramp=True)
    edge = document["terrainLayers"]["connections"][0]
    document["terrainLayers"]["connections"].append({"from": edge["to"], "to": edge["from"]})
    with pytest.raises(ValueError, match="duplicate"):
        validate_volume(document)
    document = layered_volume(ramp=True)
    edge = document["terrainLayers"]["connections"][0]
    edge["to"] = ["ramp", 2, 0]
    with pytest.raises(ValueError, match="measured support"):
        validate_volume(document)


@pytest.mark.parametrize("mutation", ["duplicate-layer", "unknown-height", "nan-slope", "bool-height",
                                       "array-shape", "schema", "layer-budget"])
def test_layered_surface_contract_rejects_malformed_measurements(mutation):
    document = layered_volume()
    terrain = document["terrainLayers"]
    upper = terrain["layers"][1]
    if mutation == "duplicate-layer":
        upper["layerId"] = "lower"
    elif mutation == "unknown-height":
        upper["supported"][0] = False
    elif mutation == "nan-slope":
        upper["slopeRadians"][0] = math.nan
    elif mutation == "bool-height":
        upper["heights"][0] = True
    elif mutation == "array-shape":
        upper["supported"].pop()
    elif mutation == "schema":
        terrain["schemaVersion"] = "navigation.terrain.layers.v2"
    else:
        terrain["layers"] = [terrain["layers"][0]] * 33
    with pytest.raises(ValueError):
        validate_volume(document)


def test_layered_mode_requires_its_explicit_contract_and_legacy_modes_stay_separate():
    with pytest.raises(ValueError, match="terrainLayers"):
        SpatialPathPlanner(terrain_volume(), layered_policy())
    with pytest.raises(ValueError, match="terrain25d"):
        SpatialPathPlanner(layered_volume(), terrain_policy())
    document = layered_volume()
    assert SpatialPathPlanner(document, policy()).policy["mode"] == "volume3d"


@pytest.mark.parametrize("limit", [{"maxExpanded": 1}, {"maxWaypoints": 2}])
def test_layered_ramp_budget_exhaustion_returns_no_partial_route(limit):
    planner = SpatialPathPlanner(layered_volume(ramp=True),
                                 layered_policy(maxStepM=.3, maxSlopeRad=.5, **limit))
    with pytest.raises(SpatialNavigationError) as error:
        planner.plan(pose(.75, .75, .5), pose(4.25, .75, 1.5))
    assert error.value.code == "PLANNING_BUDGET_EXCEEDED"


def test_layered_patch_slot_budget_is_independent_of_occupancy_volume_budget():
    document = {**volume(np.zeros((1, 100, 100), dtype=int)), "resolution": 1.}
    layer = {"layerId": "base", "heights": [0.] * 10_000, "supported": [True] * 10_000,
             "slopeRadians": [0.] * 10_000}
    document["terrainLayers"] = {"schemaVersion": "navigation.terrain.layers.v1",
        "sourceId": "measured", "observedAtUnixMs": 1,
        "layers": [{**layer, "layerId": f"layer-{number}"} for number in range(26)],
        "connections": []}
    with pytest.raises(ValueError, match="patch-slot"):
        validate_volume(document)
