"""Measured SE3 calibration and conservative inter-robot cloud registration.

The existing camera solver owns intrinsics/hand-eye calibration and dense_slam
owns planar odometry. This module estimates a rigid transform from measured 3D
points. It neither reads simulator poses nor installs TF/map revisions. Callers
must separately commission an accepted estimate and retain its input evidence.
Cloud registration is a bounded, overlapping-cloud method, not global SLAM or a
guarantee that arbitrary unrelated/symmetric scenes can be disambiguated.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Mapping

import numpy as np
from scipy.spatial import cKDTree

SCHEMA_VERSION = "slam.measured-alignment.v1"
ALGORITHM_VERSION = "measured-se3-v3"
MAX_POINTS = 4096
_IDENTITY_FIELDS = ("robotId", "frameId", "mapId", "mapRevision", "sourceId", "observationId")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _points(value, name, *, minimum=6):
    try:
        result = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be finite Nx3 points in metres") from error
    if (result.ndim != 2 or result.shape[1] != 3 or not minimum <= len(result) <= MAX_POINTS
            or not np.isfinite(result).all() or np.max(np.abs(result)) > 1_000_000):
        raise ValueError(f"{name} must contain {minimum}..{MAX_POINTS} finite Nx3 metric points")
    return result.copy()


def _evidence(value, points, name):
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} evidence must be a mapping")
    result = {}
    for field in _IDENTITY_FIELDS:
        text = value.get(field)
        if (not isinstance(text, str) or not text or len(text) > 256
                or any(character.isspace() or ord(character) < 32 for character in text)):
            raise ValueError(f"{name}.{field} must be a bounded identity")
        result[field] = text
    stamp = value.get("observedAtUnixMs")
    if type(stamp) is not int or not 0 < stamp <= 2**53 - 1:
        raise ValueError(f"{name}.observedAtUnixMs must be a positive exact integer")
    result.update(observedAtUnixMs=stamp, pointCount=len(points),
                  pointsSha256=_digest(points.tolist()))
    return result


def _positive(value, name, *, maximum=100.):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a positive finite number")
    value = float(value)
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f"{name} must be in (0, {maximum}]")
    return value


def _condition(points):
    singular = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
    return float(singular[-1] / singular[0]) if singular[0] > 1e-9 else 0.


def _fit(source, target):
    source_center, target_center = source.mean(axis=0), target.mean(axis=0)
    u, _, vt = np.linalg.svd((source - source_center).T @ (target - target_center))
    correction = np.eye(3)
    correction[2, 2] = np.linalg.det(vt.T @ u.T)
    rotation = vt.T @ correction @ u.T
    transform = np.eye(4)
    transform[:3, :3] = rotation
    transform[:3, 3] = target_center - rotation @ source_center
    return transform


def transform_points(points, source_to_target):
    """Apply a checked proper SE3 transform; does not change input arrays."""
    array = _points(points, "points", minimum=1)
    transform = np.asarray(source_to_target, dtype=np.float64)
    if (transform.shape != (4, 4) or not np.isfinite(transform).all()
            or not np.allclose(transform[3], [0., 0., 0., 1.], atol=1e-9, rtol=0)
            or not np.allclose(transform[:3, :3].T @ transform[:3, :3], np.eye(3),
                               atol=1e-6, rtol=0)
            or not math.isclose(float(np.linalg.det(transform[:3, :3])), 1., abs_tol=1e-6)):
        raise ValueError("sourceToTarget must be a proper SE3 matrix")
    return array @ transform[:3, :3].T + transform[:3, 3]


def _apply(points, transform):
    return points @ transform[:3, :3].T + transform[:3, 3]


def _record(method, source, target, source_evidence, target_evidence, parameters):
    return {"schemaVersion": SCHEMA_VERSION, "algorithmVersion": ALGORITHM_VERSION,
            "method": method, "sourceEvidence": _evidence(source_evidence, source, "source"),
            "targetEvidence": _evidence(target_evidence, target, "target"),
            "parameters": parameters, "accepted": False, "sourceToTarget": None,
            "automaticMapActivation": False,
            "confidenceInterpretation": "geometric quality score; not a calibrated probability"}


def _finish(record, code, *, transform=None, metrics=None):
    result = dict(record, code=code, metrics=metrics or {}, accepted=code == "ACCEPTED")
    if code == "ACCEPTED":
        result["sourceToTarget"] = transform.tolist()
    result["revision"] = _digest(result)
    return result


def validate_alignment_for_use(result, *, source_basis, target_basis, now_unix_ms,
                               max_age_ms=60_000):
    """Admit an estimate for explicit application in exactly its measured bases.

    Each expected basis supplies robotId, frameId, mapId and mapRevision. The
    caller owns those trusted local identities. The digest detects corruption,
    not authorship: authenticate remote evidence before this check. A loop
    closure, stale/future measurement, rejected estimate or mutated record fails.
    This does not publish TF or authorize physical motion.
    """
    if (type(now_unix_ms) is not int or not 0 < now_unix_ms <= 2**53 - 1
            or type(max_age_ms) is not int or not 0 < max_age_ms <= 86_400_000):
        raise ValueError("alignment use requires bounded positive integer time and maximum age")
    if not isinstance(result, Mapping):
        raise TypeError("alignment must be a result mapping")
    if (result.get("schemaVersion") != SCHEMA_VERSION
            or result.get("algorithmVersion") != ALGORITHM_VERSION
            or result.get("accepted") is not True or result.get("code") != "ACCEPTED"):
        raise ValueError("only an accepted supported alignment may be applied")
    content = {key: value for key, value in result.items() if key != "revision"}
    try:
        valid_digest = _digest(content) == result.get("revision")
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("alignment result is not canonical finite JSON") from error
    if not valid_digest:
        raise ValueError("alignment revision does not match its evidence and transform")
    for side, expected in (("source", source_basis), ("target", target_basis)):
        measured = result.get(side + "Evidence")
        if not isinstance(measured, Mapping) or not isinstance(expected, Mapping):
            raise TypeError("alignment basis is missing")
        for field in ("robotId", "frameId", "mapId", "mapRevision"):
            if not isinstance(expected.get(field), str) or not expected[field] or measured.get(field) != expected[field]:
                raise ValueError(f"alignment {side} {field} basis mismatch")
        stamp = measured.get("observedAtUnixMs")
        if type(stamp) is not int or not 0 <= now_unix_ms - stamp <= max_age_ms:
            raise ValueError(f"alignment {side} evidence is stale or future dated")
    # Reuse the public transform validator before returning a defensive copy.
    transform_points([[0., 0., 0.]], result.get("sourceToTarget"))
    return np.asarray(result["sourceToTarget"], dtype=np.float64).copy()


def solve_corresponded_alignment(source_points, target_points, *, source_evidence,
                                target_evidence, max_residual_m=.03, min_inlier_ratio=.7,
                                seed=0, ransac_trials=256):
    """Estimate target_T_source from measured correspondences and surveyed targets.

    For sensor extrinsics, source points are measured sensor-frame target hits;
    target points are independently surveyed target positions in the base/world
    frame. These are calibration correspondences, never an input transform.
    At least six non-coplanar inliers are required. RANSAC rejects bad matches.
    Invalid contracts raise ValueError/TypeError; unobservable data are rejected.
    """
    source, target = _points(source_points, "source"), _points(target_points, "target")
    if len(source) != len(target):
        raise ValueError("calibration correspondences must have equal lengths")
    threshold = _positive(max_residual_m, "max_residual_m")
    ratio = _positive(min_inlier_ratio, "min_inlier_ratio", maximum=1.)
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("seed must be an unsigned 32-bit integer")
    if type(ransac_trials) is not int or not 1 <= ransac_trials <= 4096:
        raise ValueError("ransac_trials must be 1..4096")
    parameters = {"maxResidualM": threshold, "minInlierRatio": ratio,
                  "seed": seed, "ransacTrials": ransac_trials, "minShapeCondition": .001}
    record = _record("corresponded-ransac-kabsch", source, target, source_evidence,
                     target_evidence, parameters)
    if min(_condition(source), _condition(target)) < .001:
        return _finish(record, "DEGENERATE_GEOMETRY")
    rng = np.random.default_rng(seed)
    best_indices, best_error = np.array([], dtype=int), math.inf
    for _ in range(ransac_trials):
        indices = rng.choice(len(source), 4, replace=False)
        if min(_condition(source[indices]), _condition(target[indices])) < .001:
            continue
        transform = _fit(source[indices], target[indices])
        residuals = np.linalg.norm(_apply(source, transform) - target, axis=1)
        inliers = np.flatnonzero(residuals <= threshold)
        error = float(np.mean(residuals[inliers]**2)) if len(inliers) else math.inf
        if len(inliers) > len(best_indices) or (len(inliers) == len(best_indices) and error < best_error):
            best_indices, best_error = inliers, error
    required = max(6, math.ceil(len(source) * ratio))
    if len(best_indices) < required:
        return _finish(record, "INSUFFICIENT_INLIERS",
                       metrics={"inlierCount": len(best_indices), "requiredInliers": required})
    for _ in range(5):
        if min(_condition(source[best_indices]), _condition(target[best_indices])) < .001:
            return _finish(record, "DEGENERATE_INLIERS")
        transform = _fit(source[best_indices], target[best_indices])
        residuals = np.linalg.norm(_apply(source, transform) - target, axis=1)
        inliers = np.flatnonzero(residuals <= threshold)
        if len(inliers) < required:
            return _finish(record, "INSUFFICIENT_INLIERS")
        if np.array_equal(inliers, best_indices):
            break
        best_indices = inliers
    # Refit once to the exact reported inlier set, including when the bounded
    # refinement exhausted its budget immediately after changing membership.
    transform = _fit(source[best_indices], target[best_indices])
    residuals = np.linalg.norm(_apply(source, transform) - target, axis=1)
    inlier_residuals = residuals[best_indices]
    if np.max(inlier_residuals) > threshold:
        return _finish(record, "UNSTABLE_INLIER_SET")
    condition = min(_condition(source[best_indices]), _condition(target[best_indices]))
    if (condition < .001 or len(np.unique(source[best_indices], axis=0)) < 6
            or len(np.unique(target[best_indices], axis=0)) < 6):
        return _finish(record, "DEGENERATE_INLIERS")
    rmse = float(np.sqrt(np.mean(inlier_residuals**2)))
    return _finish(record, "ACCEPTED", transform=transform, metrics={
        "inlierCount": len(best_indices), "inlierIndices": best_indices.tolist(),
        "inlierRatio": len(best_indices) / len(source), "rmseM": rmse,
        "maxInlierResidualM": float(np.max(inlier_residuals)), "shapeCondition": condition,
        "qualityScore": float((len(best_indices) / len(source)) * math.exp(-rmse / threshold)),
        "unmatchedPointCount": len(source) - len(best_indices)})


def _initial_transforms(source, target):
    """PCA axis permutations/signs give measured, deterministic global starts."""
    source_center, target_center = source.mean(axis=0), target.mean(axis=0)
    _, _, source_axes = np.linalg.svd(source - source_center, full_matrices=False)
    _, _, target_axes = np.linalg.svd(target - target_center, full_matrices=False)
    rotations = [np.eye(3)]
    for permutation in itertools.permutations(range(3)):
        for signs in itertools.product((-1., 1.), repeat=3):
            basis = np.eye(3)[:, permutation] @ np.diag(signs)
            rotation = target_axes.T @ basis @ source_axes
            if np.linalg.det(rotation) > 0:
                rotations.append(rotation)
    for rotation in rotations:
        transform = np.eye(4)
        transform[:3, :3] = rotation
        transform[:3, 3] = target_center - rotation @ source_center
        yield transform


def _central_points(points):
    """Rotation-invariant geometric median limits disconnected cloud outliers."""
    center = points.mean(axis=0)
    for _ in range(40):
        weights = 1. / np.maximum(np.linalg.norm(points - center, axis=1), 1e-8)
        updated = np.sum(points * weights[:, None], axis=0) / np.sum(weights)
        if np.linalg.norm(updated - center) < 1e-7:
            break
        center = updated
    radius = np.linalg.norm(points - center, axis=1)
    return points[radius <= np.quantile(radius, .85)]


def _measured_planes(points, threshold):
    """Bounded dominant-plane extraction supplies geometry, never pose truth."""
    remaining = np.arange(len(points))
    rng = np.random.default_rng(0)
    planes = []
    for _ in range(5):
        if len(remaining) < max(24, len(points) * .04):
            break
        subset = points[remaining]
        best = np.array([], dtype=int)
        for _ in range(96):
            triple = subset[rng.choice(len(subset), 3, replace=False)]
            normal = np.cross(triple[1] - triple[0], triple[2] - triple[0])
            norm = np.linalg.norm(normal)
            if norm < 1e-8:
                continue
            normal /= norm
            selected = np.flatnonzero(np.abs(subset @ normal - triple[0] @ normal) <= threshold)
            if len(selected) > len(best):
                best = selected
        if len(best) < max(24, len(points) * .04):
            break
        inliers = subset[best]
        _, _, vt = np.linalg.svd(inliers - inliers.mean(axis=0), full_matrices=False)
        normal = vt[-1]
        offset = -normal @ inliers.mean(axis=0)
        if offset < 0:
            normal, offset = -normal, -offset
        planes.append((normal, offset, len(best)))
        remaining = np.delete(remaining, best)
    return planes, remaining


def _plane_frames(planes):
    if len(planes) < 3:
        return []
    frames = []
    # The dominant plane anchors the frame (often a floor/wall). Multiple
    # pairings retain competing room-corner interpretations for later gating.
    anchor, anchor_offset, _ = planes[0]
    for i, j in itertools.permutations(range(1, len(planes)), 2):
        normal, offset, _ = planes[i]
        other, other_offset, _ = planes[j]
        normals = np.stack([anchor, normal, other])
        if abs(np.linalg.det(normals)) < .5:
            continue
        x_axis = normal - (normal @ anchor) * anchor
        x_axis /= np.linalg.norm(x_axis)
        rotation = np.column_stack([x_axis, np.cross(anchor, x_axis), anchor])
        origin = np.linalg.solve(normals, -np.array([anchor_offset, offset, other_offset]))
        frames.append((rotation, origin))
    return frames


def _plane_initializations(source_planes, target_planes):
    for source_axes, source_origin in _plane_frames(source_planes):
        for target_axes, target_origin in _plane_frames(target_planes):
            transform = np.eye(4)
            transform[:3, :3] = target_axes @ source_axes.T
            transform[:3, 3] = target_origin - transform[:3, :3] @ source_origin
            yield transform


def _icp(source, target, target_tree, initial, correspondence, iterations):
    transform = initial.copy()
    for iteration in range(iterations):
        moved = _apply(source, transform)
        distances, matches = target_tree.query(moved)
        # Mutual matching prevents many source points collapsing on one target.
        _, reverse = cKDTree(moved).query(target)
        inliers = np.flatnonzero((distances <= correspondence)
                                 & (reverse[matches] == np.arange(len(source))))
        if len(inliers) < 6:
            break
        # Trim only during refinement; reported overlap always uses every point.
        cutoff = max(float(np.quantile(distances[inliers], .85)), 1e-9)
        trimmed = inliers[distances[inliers] <= cutoff]
        if len(trimmed) < 6:
            trimmed = inliers
        update = _fit(source[trimmed], target[matches[trimmed]])
        converged = np.max(np.abs(update - transform)) < 1e-7
        transform = update
        if converged:
            break
    return transform, iteration + 1


def _cloud_metrics(source, target, target_tree, transform, threshold):
    moved = _apply(source, transform)
    forward, matches = target_tree.query(moved)
    backward, reverse = cKDTree(moved).query(target)
    source_inliers, target_inliers = forward <= threshold, backward <= threshold
    mutual = np.flatnonzero(source_inliers & (reverse[matches] == np.arange(len(source))))
    residuals = np.concatenate([forward[source_inliers], backward[target_inliers]])
    return {"sourceOverlap": float(np.mean(source_inliers)),
            "targetOverlap": float(np.mean(target_inliers)),
            "bidirectionalOverlap": float(min(np.mean(source_inliers), np.mean(target_inliers))),
            "rmseM": float(np.sqrt(np.mean(residuals**2))) if len(residuals) else None,
            "mutualPairCount": len(mutual),
            "shapeCondition": min(_condition(source[mutual]), _condition(target[matches[mutual]]))
            if len(mutual) >= 6 else 0.}


def _different(first, second, distance, angle):
    rotation = first[:3, :3].T @ second[:3, :3]
    radians = math.acos(float(np.clip((np.trace(rotation) - 1.) / 2., -1., 1.)))
    return np.linalg.norm(first[:3, 3] - second[:3, 3]) > distance or radians > angle


def register_point_clouds(source_points, target_points, *, source_evidence, target_evidence,
                          max_correspondence_m=.2, max_rmse_m=.04, min_overlap=.65,
                          max_iterations=60, coarse_distance_factor=4.,
                          min_minor_structure_overlap=.55, return_candidates=False):
    """Estimate an SE3 registration from two unordered, overlapping point clouds.

    No initial pose, simulator truth, point correspondence or world coordinates
    are accepted. Ambiguous equivalent solutions and planar/linear geometry are
    rejected. An accepted fit remains an estimate, never proof of shared place;
    deployment needs independent place identity and map-revision admission.
    """
    source, target = _points(source_points, "source", minimum=12), _points(target_points, "target", minimum=12)
    distance = _positive(max_correspondence_m, "max_correspondence_m")
    rmse_limit = _positive(max_rmse_m, "max_rmse_m")
    overlap = _positive(min_overlap, "min_overlap", maximum=1.)
    coarse_factor = _positive(coarse_distance_factor, "coarse_distance_factor", maximum=8.)
    if coarse_factor < 1.:
        raise ValueError("coarse_distance_factor must be at least one")
    if (type(min_minor_structure_overlap) not in (int, float)
            or not 0 <= min_minor_structure_overlap <= 1):
        raise ValueError("min_minor_structure_overlap must be in 0..1")
    if type(return_candidates) is not bool:
        raise TypeError("return_candidates must be a boolean")
    if rmse_limit > distance:
        raise ValueError("max_rmse_m cannot exceed max_correspondence_m")
    if type(max_iterations) is not int or not 1 <= max_iterations <= 200:
        raise ValueError("max_iterations must be 1..200")
    parameters = {"maxCorrespondenceM": distance, "maxRmseM": rmse_limit,
                  "minOverlap": overlap, "maxIterations": max_iterations,
                  "coarseDistanceFactor": coarse_factor,
                  "minShapeCondition": .001, "minMinorStructureOverlap": min_minor_structure_overlap,
                  "planeResidualLimitM": min(.025, rmse_limit),
                  "returnNonExecutableCandidates": return_candidates,
                  "initialization": "full-and-central-pca-plus-measured-plane-intersections"}
    record = _record("multistart-mutual-trimmed-icp", source, target, source_evidence,
                     target_evidence, parameters)
    if min(_condition(source), _condition(target)) < .001:
        return _finish(record, "DEGENERATE_GEOMETRY")
    target_tree = cKDTree(target)
    plane_threshold = min(.025, rmse_limit)
    source_planes, source_detail = _measured_planes(source, plane_threshold)
    target_planes, target_detail = _measured_planes(target, plane_threshold)
    candidates = []
    initializations = itertools.chain(_initial_transforms(source, target),
                                      _initial_transforms(_central_points(source),
                                                          _central_points(target)),
                                      _plane_initializations(source_planes, target_planes))
    for initial in initializations:
        coarse, coarse_iterations = _icp(source, target, target_tree, initial,
                                         distance * coarse_factor, max_iterations)
        transform, iterations = _icp(source, target, target_tree, coarse, distance, max_iterations)
        metrics = _cloud_metrics(source, target, target_tree, transform, distance)
        metrics["iterations"] = iterations
        metrics["coarseIterations"] = coarse_iterations
        if (len(source_planes) >= 3 and len(target_planes) >= 3
                and 24 <= len(source_detail) < len(source) * .5
                and 24 <= len(target_detail) < len(target) * .5):
            detail_forward, _ = cKDTree(target[target_detail]).query(_apply(source[source_detail], transform))
            detail_backward, _ = cKDTree(_apply(source[source_detail], transform)).query(target[target_detail])
            metrics["minorStructureOverlap"] = float(min(np.mean(detail_forward <= distance),
                                                         np.mean(detail_backward <= distance)))
        else:
            metrics["minorStructureOverlap"] = None
        candidates.append((transform, metrics))
    candidates.sort(key=lambda item: (-item[1]["bidirectionalOverlap"],
                                     item[1]["rmseM"] if item[1]["rmseM"] is not None else math.inf))
    if return_candidates:
        proposals = []
        for transform, metrics in candidates:
            if (metrics["bidirectionalOverlap"] < overlap or metrics["rmseM"] is None
                    or metrics["rmseM"] > rmse_limit or metrics["mutualPairCount"] < 12
                    or metrics["shapeCondition"] < .001):
                continue
            if all(_different(transform, old[0], .15, .1) for old in proposals):
                proposals.append((transform, metrics))
            if len(proposals) == 12:
                break
        record["nonExecutableCandidates"] = [{"sourceToTarget": matrix.tolist(), "metrics": metric,
                                               "grantsMapActivation": False}
                                              for matrix, metric in proposals]
    valid = [candidate for candidate in candidates
             if candidate[1]["bidirectionalOverlap"] >= overlap
             and candidate[1]["rmseM"] is not None and candidate[1]["rmseM"] <= rmse_limit
             and candidate[1]["mutualPairCount"] >= 12 and candidate[1]["shapeCondition"] >= .001
             and (candidate[1]["minorStructureOverlap"] is None
                  or candidate[1]["minorStructureOverlap"] >= min_minor_structure_overlap)]
    if not valid:
        return _finish(record, "NO_RELIABLE_OVERLAP", metrics=candidates[0][1])
    best_transform, best = valid[0]
    for other_transform, other in valid[1:]:
        if (other["bidirectionalOverlap"] >= best["bidirectionalOverlap"] - .02
                and other["rmseM"] <= max(best["rmseM"] * 1.3, best["rmseM"] + .003)
                and _different(best_transform, other_transform, max(.02, 2 * rmse_limit), .03)):
            return _finish(record, "AMBIGUOUS_ALIGNMENT", metrics={**best,
                           "competingRmseM": other["rmseM"],
                           "competingOverlap": other["bidirectionalOverlap"]})
    quality = best["bidirectionalOverlap"] * math.exp(-best["rmseM"] / rmse_limit)
    return _finish(record, "ACCEPTED", transform=best_transform,
                   metrics={**best, "qualityScore": float(quality), "candidateCount": len(candidates)})
