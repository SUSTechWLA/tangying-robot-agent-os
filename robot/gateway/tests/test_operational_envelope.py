"""Operational clearance must tighten admission without inventing map evidence."""
import hashlib
from types import SimpleNamespace

import numpy as np
import pytest
from tangying_robot_gateway.gazebo_workflow import (
    GazeboWorkflowBindings,
    swept_disc_refusal_evidence,
    swept_disc_step_is_clear,
)
from tangying_robot_gateway.grid_navigation import plan_grid_path
from tangying_robot_gateway.home_commissioning import HOME_OPERATIONAL_RADIUS_M
from tangying_robot_gateway.map_pipeline import PointCloud, build_map
from tangying_robot_gateway.service_registry import ServiceError

from robot.gateway.tests.test_robot_services import workflow_fixture


def test_disc_refusal_records_exact_guard_geometry_without_inventing_collision():
    points = np.array([[.40, .02, .2], [.35, 0., .2], [.20, 0., .8], [np.nan, 0., .2]])
    evidence = swept_disc_refusal_evidence(points, forward_m=.05, turn_rad=0.)
    assert evidence["intrudingPointCount"] == 2
    assert evidence["relevantPointCount"] == 2
    assert evidence["closestPointBaseM"] == [.35, 0., .2]
    assert evidence["minimumSweptDistanceM"] == pytest.approx(.30)
    assert evidence["scope"] == "guard_snapshot_not_collision_or_observed_free_space"
    assert swept_disc_refusal_evidence(points, forward_m=0., turn_rad=0.) is None
    assert swept_disc_refusal_evidence(np.array([[.5, 0., .2]]), forward_m=0., turn_rad=.1) is None


def test_home_planning_and_live_disc_guard_share_the_operational_envelope(tmp_path):
    binding = GazeboWorkflowBindings(runtime=SimpleNamespace(_samples={}), navigation=None,
                                    robot_id="gazebo-house-rgbd", root=tmp_path)
    binding.home = {"commissioned": True}
    workflow = binding.build_workflow()
    assert workflow.footprint_radius == .32  # no expansion of occupied-pose evidence
    assert workflow._planning_clearance() == HOME_OPERATIONAL_RADIUS_M == .355
    for distance, expected in [(.32, False), (.35, False), (.355, False), (.356, True)]:
        assert bool(swept_disc_step_is_clear(np.array([[distance, 0., .2]]),
                                            forward_m=0., turn_rad=.1)) is expected
    binding.clearance.record((0., 0.), (1., 0.))
    assert not binding.clearance([.5, 0.], HOME_OPERATIONAL_RADIUS_M), (
        "the narrower historical Nav2 proof cannot acquire a larger radius by configuration")


@pytest.mark.parametrize("unknown", [-1, 100])
def test_reloading_old_narrow_map_rechecks_goals_and_paths_without_rewriting(tmp_path, unknown):
    cells = np.zeros((100, 100), dtype=np.int16)
    cells[:, 67:] = unknown  # obstacle/unknown begins at x=.34; old .32 fits
    grid = {"width": 100, "height": 100, "resolution": .02,
            "origin": [-1., -1., 0.], "cells": cells}
    pose = [0., 0., .035, 1., 0., 0., 0.]
    build_map(tmp_path/"narrow-map", map_id="narrow-map", robot_id="unit-1",
              cloud=PointCloud(np.array([[0., 0., 0.], [.1, .1, .2]])),
              calibration_revision="a"*64, occupancy_grid=grid,
              semantic_workspaces=[{"name": "dock", "kind": "work_area", "aliases": [],
                  "navigationPose": pose, "target": [0., 0., .5], "navigationReady": True,
                  "footprintRadiusM": .32}],
              slam_metadata={"schemaVersion": "slam.session.v1", "robotId": "unit-1",
                  "mapId": "narrow-map", "calibrationRevision": "a"*64,
                  "worldFrameRevision": "stable-driver-frame", "mapFromWorld": [0., 0., 0.],
                  "navigationEvidenceVersion": 2, "footprintRadiusM": .32})
    def hashes():
        return {str(f.relative_to(tmp_path)): hashlib.sha256(f.read_bytes()).hexdigest()
                for f in (tmp_path/"narrow-map").rglob("*") if f.is_file()}
    before = hashes()
    workflow, _ = workflow_fixture(tmp_path)
    workflow.operational_radius = .32
    workflow.activate({"mapId": "narrow-map"})
    original_grid = workflow.grid["cells"].copy()
    assert workflow.resolve_location({"name": "dock"})["navigationReady"]
    revision = workflow.active.copy()
    restarted, _ = workflow_fixture(tmp_path)
    restarted.operational_radius = HOME_OPERATIONAL_RADIUS_M
    restarted.activate({"mapId": "narrow-map"})
    assert restarted.active == revision
    assert restarted.locations()["locations"][0]["footprintRadiusM"] == .355
    with pytest.raises(ServiceError, match="自由空间"):
        restarted.resolve_location({"name": "dock"})
    assert "dock" not in restarted.semantic_navigation()["goals"]
    assert plan_grid_path(grid, [-.1, 0.], [0., 0.], radius=.32)
    with pytest.raises(ServiceError):
        plan_grid_path(grid, [-.1, 0.], [0., 0.], radius=restarted._planning_clearance())
    np.testing.assert_array_equal(restarted.grid["cells"], original_grid)
    assert hashes() == before


def test_operational_margin_does_not_clear_unknown_cells_around_occupied_pose(tmp_path):
    workflow, _ = workflow_fixture(tmp_path)
    workflow.footprint_radius = .32
    workflow.operational_radius = HOME_OPERATIONAL_RADIUS_M
    cells = np.full((200, 200), -1, dtype=np.int16)
    grid = {"width": 200, "height": 200, "resolution": .01,
            "origin": [-1., -1., 0.], "cells": cells}
    workflow._observed_travel(grid, [[0., 0., 0.]], np.zeros(3))
    assert cells[100, 100] == 0
    assert cells[100, 133] == -1  # x=.335 is margin, not body evidence


def test_wider_operation_preserves_only_the_measured_narrow_travel_band(tmp_path):
    from tangying_robot_gateway.gazebo_workflow import GazeboTravelClearance
    workflow, _ = workflow_fixture(tmp_path)
    workflow.footprint_radius = .32
    workflow.operational_radius = HOME_OPERATIONAL_RADIUS_M
    proof = GazeboTravelClearance()
    proof.record((0., 0.), (1., 0.))
    workflow.clearance_validator = proof
    grid = {"width": 300, "height": 200, "resolution": .01,
            "origin": [-1., -1., 0.], "cells": np.full((200, 300), -1, dtype=np.int16)}
    workflow._observed_travel(grid, [[0., 0., 0.], [1., 0., 0.]], np.zeros(3))
    assert grid["cells"][100, 150] == 0  # between keyframes: valid narrow proof survives
    assert grid["cells"][133, 150] == -1  # operational margin remains unproved


def test_gazebo_navigation_rejects_narrow_middle_before_driver_dispatch(tmp_path):
    from dataclasses import replace

    from tangying_robot_gateway.runtime import Command

    from robot.gateway.tests.test_gazebo_backend import captured_backend
    backend = captured_backend()
    backend.home = {"home": True}
    workflow, _ = workflow_fixture(tmp_path)
    workflow.operational_radius = HOME_OPERATIONAL_RADIUS_M
    workflow.active = {"mapId": "old-map", "mapRevision": "old-revision"}
    cells = np.zeros((200, 200), dtype=np.int16)
    cells[90:110, :83] = cells[90:110, 117:] = 100  # 0.68 m middle gap
    workflow.grid = {"width": 200, "height": 200, "resolution": .02,
                     "origin": [0., -2., 0.], "cells": cells}
    backend.node.workflow = workflow
    template = backend.node.runtime._samples["base-rgbd"]
    pose = template.base_pose_at_capture.copy()
    pose[1, 3] = -.7
    backend.node.runtime._samples["base-rgbd"] = replace(template, base_pose_at_capture=pose)
    backend.node.navigate = lambda *_a, **_k: pytest.fail("blocked route reached driver")
    start, goal = [2., -.7], [2., .7, 0., 1., 0., 0., 0.]
    assert plan_grid_path(workflow.grid, start, goal, radius=.32)
    result = backend.execute(Command(schema_version="robot.v1", task_id="t", command_id="narrow",
        capability="navigation.navigate", parameters={"goalPose": goal}))
    assert not result.success


def test_semantic_plan_and_binding_recheck_operational_margin(tmp_path, monkeypatch):
    import tangying_robot_gateway.semantic_cloud_services as services

    from robot.gateway.tests.test_semantic_cloud_services import annotate, call, setup_map
    workflow, registry, manifest = setup_map(tmp_path)
    workflow.operational_radius = HOME_OPERATIONAL_RADIUS_M
    after = annotate(registry, manifest)
    observed = []
    candidates = services.observation_candidates
    def inspect(*args, **kwargs):
        observed.append(kwargs["radius"])
        return candidates(*args, **kwargs)
    monkeypatch.setattr(services, "observation_candidates", inspect)
    planned = call(registry, "semantic.plan", mapRevision=manifest["hash"],
                   semanticRevision=after["semanticRevision"], annotationId="table-1")
    assert planned["candidates"] and observed == [HOME_OPERATIONAL_RADIUS_M]
    binding = {"identity": planned["identity"],
               "worldFrameRevision": workflow.world_frame_revision,
               "mapFromWorld": list(workflow.map_from_world), "footprintRadiusM": .32}
    workflow.semantic_execution_binding_supported = True
    with pytest.raises(ServiceError, match="足迹"):
        workflow.semantic_cloud_services.check_binding_current(binding)
