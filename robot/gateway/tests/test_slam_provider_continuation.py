"""Continuation evidence stays in one map frame and retains capture identities."""

import json
import math

import numpy as np
import pytest
from tangying_robot_gateway.geometry import PointCloud, transform
from tangying_robot_gateway.map_catalog import MapCatalog
from tangying_robot_gateway.map_pipeline import build_map, decode_lod, occupancy_from_points

from .test_slam_provider import sampled_workflow


def continued_map(tmp_path, monkeypatch, *, inherited_times=(1000, 1100)):
    import tangying_robot_gateway.robot_workflow as workflow_module

    anchor = np.array([1.2, -.4, math.pi / 2])
    inherited_trail = [[2.4, 2.1, 0.], [2.6, 2.1, 0.]]
    base_id = "scan-provider-base"
    base_directory = tmp_path / base_id
    base_manifest = build_map(base_directory, map_id=base_id, robot_id="provider-unit",
        cloud=PointCloud(np.array([[2.4, 2.1, 0.], [2.6, 2.1, 0.],
                                  [2.4, 2.3, 0.], [2.6, 2.3, .2]], dtype=np.float32)),
        poses=inherited_trail,
        times_unix_ms=list(inherited_times) if inherited_times is not None else None,
        source="import", calibration_revision="a" * 64,
        slam_metadata={"schemaVersion": "slam.session.v1", "navigationEvidenceVersion": 2,
                       "robotId": "provider-unit", "mapId": base_id,
                       "calibrationRevision": "a" * 64, "worldFrameRevision": "test-world",
                       "mapFromWorld": anchor.tolist()})
    inherited_cloud, _ = decode_lod(
        (base_directory / base_manifest["artifacts"]["cloud"]["href"]).read_bytes())
    original_base_bytes = {path.name: path.read_bytes() for path in base_directory.iterdir() if path.is_file()}
    workflow, _ = sampled_workflow(tmp_path)
    workflow._base_map_id = base_id
    workflow._base_anchor = anchor.tolist()
    current = workflow.slam.snapshot()
    inputs = []
    original_occupancy = workflow_module.occupancy_from_points

    def inspect_projection(cloud, **parameters):
        inputs.append((cloud.xyz.copy(), parameters["sources"].copy(),
                       dict(parameters["sensor_origins"])))
        return original_occupancy(cloud, **parameters)

    monkeypatch.setattr(workflow_module, "occupancy_from_points", inspect_projection)
    workflow._build()
    assert workflow.state == "completed"
    directory, manifest = MapCatalog(tmp_path).open(workflow.map_id, robot_id="provider-unit",
                                                   calibration_revision="a" * 64)
    saved_trail = json.loads((directory / manifest["artifacts"]["trajectory"]["href"]).read_text())["features"][0]
    assert {path.name: path.read_bytes() for path in base_directory.iterdir() if path.is_file()} == original_base_bytes
    assert len(inputs) == 1
    return inputs[0], inherited_cloud, inherited_trail, current, anchor, saved_trail


def test_continuation_does_not_apply_anchor_to_inherited_map_cloud_again(tmp_path, monkeypatch):
    (xyz, sources, _), inherited, _, current, anchor, _ = continued_map(tmp_path, monkeypatch)
    np.testing.assert_allclose(xyz[:inherited.count], inherited.xyz, atol=1e-6)
    np.testing.assert_allclose(xyz[inherited.count:], transform(current.cloud.xyz, anchor), atol=1e-6)
    np.testing.assert_array_equal(sources[:inherited.count], np.full(inherited.count, -1))
    np.testing.assert_array_equal(sources[inherited.count:], current.sources)


def test_continuation_point_sources_use_current_capture_origins_not_inherited_trail(tmp_path, monkeypatch):
    (_, sources, origins), inherited, _, current, anchor, _ = continued_map(tmp_path, monkeypatch)
    expected = transform(current.trajectory, anchor)
    assert set(origins) == set(range(len(current.trajectory)))
    for index in np.unique(sources[inherited.count:]):
        np.testing.assert_allclose(origins[index], expected[index], atol=1e-6)


def test_continuation_trajectory_keeps_inherited_map_frame_and_transforms_new_poses(tmp_path, monkeypatch):
    _, _, inherited_trail, current, anchor, saved = continued_map(tmp_path, monkeypatch)
    expected = np.concatenate([np.array(inherited_trail), transform(current.trajectory, anchor)])
    np.testing.assert_allclose(saved["geometry"]["coordinates"], expected[:, :2], atol=1e-6)


@pytest.mark.parametrize("inherited_times", [(1000, 1100), None])
def test_continuation_preserves_capture_times_without_inventing_missing_history(tmp_path, monkeypatch, inherited_times):
    _, _, inherited_trail, current, _, saved = continued_map(
        tmp_path, monkeypatch, inherited_times=inherited_times)
    times = saved["properties"]["timesUnixMs"]
    assert len(times) == len(saved["geometry"]["coordinates"])
    expected_prefix = list(inherited_times) if inherited_times is not None else [None] * len(inherited_trail)
    assert times == expected_prefix + current.timestamps.tolist()


@pytest.mark.parametrize("sources", [[0, -1], [-1, -1]], ids=["mixed-session", "all-inherited"])
def test_inherited_points_cannot_clear_rays_from_the_last_current_sensor(sources):
    cloud = PointCloud(np.array([[0., .1, 0.], [1., .1, .5]], dtype=np.float32))
    grid = occupancy_from_points(cloud, resolution=.1,
        bounds={"min": [0., 0., 0.], "max": [1.1, .3, .6]},
        sources=np.array(sources), sensor_origins={0: (0., .1, 0.)})
    assert grid["cells"][1, 0] == 0
    assert grid["cells"][1, 10] == 100
    assert grid["cells"][1, 5] == -1, "inherited source -1 has no current sensor ray evidence"
