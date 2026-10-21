"""Known-scene reference construction and holdout admission; no Gazebo claims."""
import copy
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from tangying_robot_gateway.registered_alignment import transform_points

from sim.gazebo.build_field_world import build_world
from sim.gazebo.field_calibration import (
    calibrate_in_known_scene,
    lidar_points_from_scan,
    load_surveyed_scene,
    nearest_surface_distances,
    sample_surveyed_scene,
    voxel_sample,
)
from sim.gazebo.validate_field_alignment import _scan_time, _survey_matrices

ROOT = Path(__file__).resolve().parents[2]


def identities(scene):
    return ({"robotId": "ground-a", "frameId": "lidar", "mapId": "field", "mapRevision": "local-1",
             "sourceId": "gpu-lidar", "observationId": "scan-1", "observedAtUnixMs": 1000},
            {"robotId": "survey-station", "frameId": "world", "mapId": "field",
             "mapRevision": scene["revision"], "sourceId": "surveyed-cad",
             "observationId": "cad-1", "observedAtUnixMs": 1000})


def test_reference_ignores_every_robot_and_sensor_pose(tmp_path):
    original = build_world()
    changed = original.replace("0.12 0.04 0.2 0.02 -0.08 0.12", "8 7 6 0.5 0.7 2")
    changed = changed.replace("-2.0 -1.0 0.16 0 0 0", "4 3 2 0.1 0.2 0.3")
    assert changed != original
    path = tmp_path / "scene.sdf"
    path.write_text(original)
    before = load_surveyed_scene(path)
    path.write_text(changed)
    after = load_surveyed_scene(path)
    assert before == after
    assert len(before["surfaces"]) == 9
    assert not before["robotOrSensorPosesRead"]
    assert all("ground" not in surface["name"] and "uav" not in surface["name"]
               for surface in before["surfaces"])
    path.write_text(original.replace("0.7 1.3 1.4", "0.8 1.3 1.4"))
    assert load_surveyed_scene(path)["revision"] != before["revision"]


def test_sampled_reference_is_bounded_deterministic_and_on_static_surfaces():
    scene = load_surveyed_scene(ROOT / "sim/gazebo/field_validation.sdf")
    sampled = sample_surveyed_scene(scene, maximum_points=1024)
    assert sampled == sample_surveyed_scene(scene, maximum_points=1024)
    assert len(sampled["points"]) == 1024
    assert np.max(nearest_surface_distances(sampled["points"], scene)) < 1e-8
    points = np.asarray(sampled["points"])
    assert np.all(points >= [-5., -5., 0.]) and np.all(points <= [5., 5., 3.])


def test_analytic_surface_distances_inside_box_outside_and_finite_plane():
    scene = {"surfaces": [{"kind": "box", "size": [2., 2., 2.], "localToWorld": np.eye(4).tolist()}]}
    np.testing.assert_allclose(nearest_surface_distances([[0, 0, 0], [1, 0, 0], [2, 2, 1]], scene),
                               [1., 0., np.sqrt(2)])
    scene["surfaces"] = [{"kind": "plane", "size": [2., 2.], "localToWorld": np.eye(4).tolist()}]
    np.testing.assert_allclose(nearest_surface_distances([[0, 0, .3], [2, 1, 1]], scene),
                               [.3, np.sqrt(2)])


def test_known_scene_calibration_uses_disjoint_holdout_and_estimates_extrinsics():
    scene = load_surveyed_scene(ROOT / "sim/gazebo/field_validation.sdf")
    reference = sample_surveyed_scene(scene, maximum_points=2048)
    # Synthetic unit fixture only: truth constructs input and evaluates output.
    # Production calls this function with actual Gazebo range measurements.
    truth = np.eye(4)
    truth[:3, :3] = Rotation.from_euler("xyz", [.02, -.08, .12]).as_matrix()
    truth[:3, 3] = [-1.88, -.96, .35]
    measured = transform_points(reference["points"], np.linalg.inv(truth))
    measured += np.random.default_rng(7).normal(0., .002, measured.shape)
    source, target = identities(scene)
    station = np.eye(4)
    station[:3, 3] = [-2., -1., .15]
    result = calibrate_in_known_scene(measured, scene, reference, source_evidence=source,
                                      target_evidence=target, surveyed_base_to_world=station)
    assert result["accepted"], result
    np.testing.assert_allclose(result["estimatedSensorToWorld"], truth, atol=.01)
    np.testing.assert_allclose(result["estimatedSensorToBase"], np.linalg.inv(station) @ truth, atol=.01)
    assert not set(result["fitIndices"]) & set(result["holdoutIndices"])
    assert len(result["fitIndices"]) + len(result["holdoutIndices"]) == len(measured)
    assert result["holdout"]["inlierRatio"] == 1.
    assert not result["truthTransformUsedForEstimation"]


def test_wrong_reference_revision_and_tampered_points_rejected():
    scene = load_surveyed_scene(ROOT / "sim/gazebo/field_validation.sdf")
    reference = sample_surveyed_scene(scene, maximum_points=1024)
    source, target = identities(scene)
    wrong = copy.deepcopy(reference)
    wrong["points"][0][0] += .01
    with pytest.raises(ValueError, match="digest"):
        calibrate_in_known_scene(reference["points"], scene, wrong,
                                 source_evidence=source, target_evidence=target)
    wrong = dict(reference, sceneRevision="wrong")
    with pytest.raises(ValueError, match="different surveyed"):
        calibrate_in_known_scene(reference["points"], scene, wrong,
                                 source_evidence=source, target_evidence=target)
    target["mapRevision"] = "unknown"
    with pytest.raises(ValueError, match="target evidence"):
        calibrate_in_known_scene(reference["points"], scene, reference,
                                 source_evidence=source, target_evidence=target)


def test_unrelated_measurements_preserve_failure_instead_of_using_sensor_truth():
    scene = load_surveyed_scene(ROOT / "sim/gazebo/field_validation.sdf")
    reference = sample_surveyed_scene(scene, maximum_points=1024)
    source, target = identities(scene)
    unrelated = np.random.default_rng(7).normal(size=(100, 3)) * .05
    result = calibrate_in_known_scene(unrelated, scene, reference,
                                      source_evidence=source, target_evidence=target)
    assert not result["accepted"]
    assert result["estimatedSensorToWorld"] is None
    assert result["estimatedSensorToBase"] is None


@pytest.mark.parametrize("kwargs", [{"maximum_points": 1}, {"spacing_m": .001},
                                    {"bounds": [[1, 1, 1], [0, 0, 0]]}])
def test_sampling_rejects_invalid_settings(kwargs):
    scene = load_surveyed_scene(ROOT / "sim/gazebo/field_validation.sdf")
    with pytest.raises(ValueError):
        sample_surveyed_scene(scene, **kwargs)


def test_cad_allocation_is_bounded_before_generating_grid():
    scene = {"surfaces": [{"kind": "plane", "size": [1000., 1000.],
                           "localToWorld": np.eye(4).tolist()}], "revision": "fake"}
    with pytest.raises(ValueError, match="budget"):
        sample_surveyed_scene(scene, spacing_m=.05)


def test_scan_decoder_does_not_read_pose_and_keeps_exclusion_accounting():
    scan = {"count": 3, "verticalCount": 2, "angleMin": -.5, "angleMax": .5,
            "verticalAngleMin": -.1, "verticalAngleMax": .1, "rangeMin": .05,
            "rangeMax": 10., "ranges": [.2, 1., 2., float("inf"), 10., 3.],
            "worldPose": object()}
    decoded = lidar_points_from_scan(scan, exclude_near_m=.6)
    assert decoded["validRayIndices"] == [1, 2, 5]
    assert decoded["invalidOrNoHitCount"] == 2
    assert decoded["nearFieldExcludedHitCount"] == 1
    assert len(decoded["points"]) + decoded["invalidOrNoHitCount"] + decoded["nearFieldExcludedHitCount"] == 6
    assert not decoded["simulatorPoseRead"]
    scan["worldPose"] = "intentionally invalid, ignored"
    assert lidar_points_from_scan(scan, exclude_near_m=.6) == decoded


def test_voxel_sampling_keeps_measured_returns_and_corresponding_indices():
    points = np.random.default_rng(7).uniform(-1, 1, (100, 3))
    result = voxel_sample(points, cell_m=.5)
    np.testing.assert_array_equal(result["points"], points[result["inputIndices"]])
    assert result["outputCount"] < result["inputCount"]


def test_truth_audit_accepts_protobuf_string_seconds_and_rejects_bad_nanoseconds():
    assert _scan_time({"header": {"stamp": {"sec": "12", "nsec": 500_000_000}}}) == 12.5
    assert _scan_time({"header": {"stamp": {}}}) == 0
    with pytest.raises(ValueError):
        _scan_time({"header": {"stamp": {"sec": "1", "nsec": 1_000_000_000}}})


def test_survey_input_never_reads_the_configured_sensor_mount():
    plan = {"schemaVersion": "gazebo.independent_calibration_capture_plan.v1",
            "declaredBeforeAcquisition": True,
            "surveyedBasePoses": {robot: {"position": [1., 2., .15], "quaternionWXYZ": [1., 0., 0., 0.]}
                                  for robot in ("ground-a", "ground-b", "uav-a")},
            "sensorToBaseConfiguredXYZRPY": object()}
    matrices = _survey_matrices(plan)
    for matrix in matrices.values():
        np.testing.assert_array_equal(matrix[:3, 3], [1., 2., .15])
        np.testing.assert_array_equal(matrix[:3, :3], np.eye(3))
