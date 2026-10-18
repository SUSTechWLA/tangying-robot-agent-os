"""SE3 estimation from measurements; truth is used only by test assertions."""
import copy
import gzip
import itertools
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from tangying_robot_gateway.registered_alignment import (
    MAX_POINTS,
    register_point_clouds,
    solve_corresponded_alignment,
    transform_points,
    validate_alignment_for_use,
)


def evidence(robot="r1", frame="sensor", observation="scan-1"):
    return {"robotId": robot, "frameId": frame, "mapId": "warehouse", "mapRevision": "map-1",
            "sourceId": "lidar", "observationId": observation, "observedAtUnixMs": 1000}


def truth_transform():
    result = np.eye(4)
    result[:3, :3] = Rotation.from_euler("xyz", [.2, -.35, .8]).as_matrix()
    result[:3, 3] = [1.3, -.8, .4]
    return result


def measured_cloud(seed, size=160):
    rng = np.random.default_rng(seed)
    # An asymmetric ellipsoid and a separate cluster prevent shape symmetry.
    points = rng.normal(size=(size, 3)) * [2.3, 1.1, .6]
    points[:size // 5] += [2.7, -1., .8]
    return points


@pytest.mark.parametrize("seed", [1, 7, 19, 42, 88])
def test_ransac_estimates_noisy_calibration_with_bad_correspondences(seed):
    rng = np.random.default_rng(seed)
    source = measured_cloud(seed, 40)
    truth = truth_transform()
    target = transform_points(source, truth) + rng.normal(0., .002, source.shape)
    target[::7] += [1., -2., 1.]
    result = solve_corresponded_alignment(source, target, source_evidence=evidence(),
                                         target_evidence=evidence(frame="base"), seed=seed)
    assert result["accepted"], result
    np.testing.assert_allclose(result["sourceToTarget"], truth, atol=.004)
    assert result["metrics"]["inlierCount"] == 34
    assert result["metrics"]["rmseM"] < .005
    assert result["metrics"]["unmatchedPointCount"] == 6
    assert not result["automaticMapActivation"]
    assert len(result["sourceEvidence"]["pointsSha256"]) == 64
    assert len(result["revision"]) == 64
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("seed", [3, 7, 19])
def test_unordered_cloud_registration_without_pose_hint(seed):
    rng = np.random.default_rng(seed)
    source = measured_cloud(seed)
    truth = truth_transform()
    target = transform_points(source, truth) + rng.normal(0., .002, source.shape)
    rng.shuffle(target)
    result = register_point_clouds(source, target, source_evidence=evidence(),
                                   target_evidence=evidence("r2", "local-map", "scan-2"))
    assert result["accepted"], result
    np.testing.assert_allclose(result["sourceToTarget"], truth, atol=.004)
    assert result["metrics"]["bidirectionalOverlap"] == 1.
    assert result["metrics"]["mutualPairCount"] > 150
    assert result["metrics"]["rmseM"] < .005


def test_cloud_registration_retains_partial_overlap_in_denominator():
    rng = np.random.default_rng(11)
    shared = measured_cloud(11, 180)
    source = np.vstack([shared, rng.normal(size=(12, 3)) + [-8., -7., 1.]])
    target = transform_points(np.vstack([shared, rng.normal(size=(12, 3)) + [8., 7., -1.]]),
                              truth_transform())
    result = register_point_clouds(source, target, source_evidence=evidence(),
                                   target_evidence=evidence("r2", "map", "scan-2"),
                                   max_correspondence_m=.5, max_rmse_m=.02)
    assert result["accepted"], result
    np.testing.assert_allclose(result["sourceToTarget"], truth_transform(), atol=1e-6)
    assert .9 < result["metrics"]["bidirectionalOverlap"] < .95


@pytest.mark.parametrize("shape", ["line", "plane", "identical"])
def test_unobservable_shapes_are_rejected(shape):
    points = measured_cloud(19, 40)
    if shape == "line":
        points[:, 1:] = 0.
    elif shape == "plane":
        points[:, 2] = 0.
    else:
        points[:] = 0.
    target = transform_points(points, truth_transform())
    for function in (solve_corresponded_alignment, register_point_clouds):
        result = function(points, target, source_evidence=evidence(), target_evidence=evidence("r2"))
        assert not result["accepted"]
        assert result["code"] == "DEGENERATE_GEOMETRY"
        assert result["sourceToTarget"] is None


def test_inlier_geometry_must_be_observable_even_when_outliers_span_3d():
    source = measured_cloud(12, 40)
    source[:35, 2] = 0.
    target = transform_points(source, truth_transform())
    target[35:] += [8., 10., 7.]
    result = solve_corresponded_alignment(source, target, source_evidence=evidence(),
                                         target_evidence=evidence("r2"))
    assert not result["accepted"]
    assert result["code"] in {"DEGENERATE_INLIERS", "INSUFFICIENT_INLIERS"}


def test_wrong_correspondences_and_unrelated_clouds_fail_closed():
    source = measured_cloud(7)
    target = measured_cloud(19) * [1., 2., .1]
    for function in (solve_corresponded_alignment, register_point_clouds):
        result = function(source, target, source_evidence=evidence(), target_evidence=evidence("r2"))
        assert not result["accepted"]
        assert result["sourceToTarget"] is None


def test_duplicate_targets_cannot_inflate_calibration_support():
    source = np.tile(np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]), (5, 1))
    target = transform_points(source, truth_transform())
    result = solve_corresponded_alignment(source, target, source_evidence=evidence(),
                                         target_evidence=evidence("r2"))
    assert not result["accepted"]
    assert result["code"] == "DEGENERATE_INLIERS"


def test_reflected_correspondences_are_not_a_rigid_rotation():
    source = measured_cloud(11, 50)
    target = source * [-1., 1., 1.]
    result = solve_corresponded_alignment(source, target, source_evidence=evidence(),
                                         target_evidence=evidence("r2"))
    assert not result["accepted"]


def test_symmetrical_scene_rejects_equivalent_different_transforms():
    source = np.asarray(list(itertools.product([-1., 0., 1.], repeat=3))) * [2., 1., .5]
    target = transform_points(source, truth_transform())
    result = register_point_clouds(source, target, source_evidence=evidence(),
                                   target_evidence=evidence("r2"))
    assert not result["accepted"]
    assert result["code"] == "AMBIGUOUS_ALIGNMENT"


def test_measurements_and_identities_are_bound_to_result_revision():
    source = measured_cloud(7, 30)
    target = transform_points(source, truth_transform())
    kwargs = {"source_evidence": evidence(), "target_evidence": evidence("r2")}
    first = solve_corresponded_alignment(source, target, **kwargs)
    assert first == solve_corresponded_alignment(source, target, **kwargs)
    kwargs["source_evidence"]["mapRevision"] = "loop-closure-2"
    second = solve_corresponded_alignment(source, target, **kwargs)
    assert first["revision"] != second["revision"]
    assert first["sourceEvidence"]["pointsSha256"] == second["sourceEvidence"]["pointsSha256"]
    source[0, 0] += .001
    third = solve_corresponded_alignment(source, target, **kwargs)
    assert third["sourceEvidence"]["pointsSha256"] != second["sourceEvidence"]["pointsSha256"]


@pytest.mark.parametrize("field,value", [("robotId", ""), ("frameId", "bad frame"),
                                         ("mapRevision", None), ("sourceId", 1),
                                         ("observedAtUnixMs", True), ("observedAtUnixMs", 0)])
def test_malformed_evidence_rejected(field, value):
    source = measured_cloud(7, 30)
    identity = evidence()
    identity[field] = value
    with pytest.raises(ValueError):
        solve_corresponded_alignment(source, source, source_evidence=identity,
                                     target_evidence=evidence("r2"))


@pytest.mark.parametrize("points", [[], [[1., 2.]] * 12, [[1., 2., float("nan")]] * 12,
                                   [[1., 2., float("inf")]] * 12, [[1., 2., 3.]] * (MAX_POINTS + 1)])
def test_malformed_or_unbounded_cloud_rejected(points):
    with pytest.raises(ValueError):
        register_point_clouds(points, points, source_evidence=evidence(), target_evidence=evidence("r2"))


@pytest.mark.parametrize("kwargs", [{"max_residual_m": float("nan")}, {"seed": -1},
                                    {"ransac_trials": 100000}, {"min_inlier_ratio": True}])
def test_invalid_calibration_options(kwargs):
    source = measured_cloud(7, 30)
    with pytest.raises((ValueError, TypeError)):
        solve_corresponded_alignment(source, source, source_evidence=evidence(),
                                     target_evidence=evidence("r2"), **kwargs)


@pytest.mark.parametrize("kwargs", [{"max_iterations": 0}, {"max_correspondence_m": -1},
                                    {"max_rmse_m": .4}, {"min_overlap": 1.1}])
def test_invalid_registration_options(kwargs):
    source = measured_cloud(7, 30)
    with pytest.raises(ValueError):
        register_point_clouds(source, source, source_evidence=evidence(),
                              target_evidence=evidence("r2"), **kwargs)


def test_transform_rejects_reflection_and_does_not_mutate_measurements():
    source = measured_cloud(7, 30)
    before = copy.deepcopy(source)
    transform_points(source, truth_transform())
    np.testing.assert_array_equal(source, before)
    reflection = np.eye(4)
    reflection[0, 0] = -1.
    with pytest.raises(ValueError, match="proper SE3"):
        transform_points(source, reflection)


def test_admission_applies_estimate_only_in_matching_current_bases():
    source = measured_cloud(7, 30)
    source_identity, target_identity = evidence(), evidence("r2", "local-map", "scan-2")
    target = transform_points(source, truth_transform())
    result = register_point_clouds(source, target, source_evidence=source_identity,
                                   target_evidence=target_identity)
    matrix = validate_alignment_for_use(result, source_basis=source_identity,
                                        target_basis=target_identity, now_unix_ms=2000)
    np.testing.assert_allclose(transform_points(source, matrix), target, atol=1e-6)
    matrix[0, 3] += 10
    assert result["sourceToTarget"][0][3] != matrix[0, 3]
    for side in ("source", "target"):
        for field in ("robotId", "frameId", "mapId", "mapRevision"):
            source_basis, target_basis = source_identity.copy(), target_identity.copy()
            (source_basis if side == "source" else target_basis)[field] = "changed"
            with pytest.raises(ValueError, match="basis mismatch"):
                validate_alignment_for_use(result, source_basis=source_basis,
                                           target_basis=target_basis, now_unix_ms=2000)
    for now in (999, 61_001):
        with pytest.raises(ValueError, match="stale or future"):
            validate_alignment_for_use(result, source_basis=source_identity,
                                       target_basis=target_identity, now_unix_ms=now)
    damaged = copy.deepcopy(result)
    damaged["sourceToTarget"][0][3] += .001
    with pytest.raises(ValueError, match="revision"):
        validate_alignment_for_use(damaged, source_basis=source_identity,
                                   target_basis=target_identity, now_unix_ms=2000)
    rejected = dict(result, accepted=False, code="NO_RELIABLE_OVERLAP")
    with pytest.raises(ValueError, match="accepted"):
        validate_alignment_for_use(rejected, source_basis=source_identity,
                                   target_basis=target_identity, now_unix_ms=2000)


def test_measured_sparse_room_rejects_the_old_half_turn_solution():
    fixture = json.loads(gzip.decompress((Path(__file__).parent / "fixtures" /
                                          "field_registration_sparse_room.json.gz").read_bytes()))
    result = register_point_clouds(fixture["sourcePoints"], fixture["targetPoints"],
                                   source_evidence=evidence("ground-b", "lidar", "scan-b"),
                                   target_evidence=evidence("ground-a", "lidar", "scan-a"),
                                   max_correspondence_m=.4, max_rmse_m=.16, min_overlap=.65)
    assert result["accepted"], result
    assert result["metrics"]["minorStructureOverlap"] >= .55
    # The simulator-derived expected pose appears only after estimation. The old
    # centroid/PCA-only solver accepted a physically wrong 180 degree solution.
    np.testing.assert_allclose(result["sourceToTarget"], fixture["evaluationOnlySourceToTarget"], atol=.03)
    assert not np.allclose(result["sourceToTarget"], fixture["rejectedOldHalfTurn"], atol=.1)
