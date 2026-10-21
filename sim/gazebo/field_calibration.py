"""Known-scene calibration from surveyed static CAD and actual lidar returns.

Only static collision geometry is read from SDF. Robot poses, sensor poses,
simulator world hits and dynamic model state are deliberately excluded. This is
known-scene registration, not calibration in an unknown environment. A separate
surveyed stationary base pose is needed to interpret the estimate as extrinsics.
"""
from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
from tangying_robot_gateway.registered_alignment import (
    _measured_planes,
    register_point_clouds,
    transform_points,
)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def lidar_points_from_scan(scan, *, exclude_near_m=0.):
    """Decode ranges without poses; optional explicit stationary self-return mask.

    A nonzero near-field mask is only suitable for surveyed, cleared calibration
    stations. It must never suppress near obstacles in a motion safety input.
    """
    horizontal, vertical = scan.get("count"), scan.get("verticalCount")
    if (type(horizontal) is not int or type(vertical) is not int
            or not 2 <= horizontal <= 2048 or not 2 <= vertical <= 128):
        raise ValueError("invalid bounded lidar angular counts")
    ranges = np.asarray(scan.get("ranges"), dtype=float)
    if ranges.shape != (horizontal * vertical,):
        raise ValueError("lidar range count disagrees with angular grid")
    angular = [scan.get(key) for key in ("angleMin", "angleMax", "verticalAngleMin", "verticalAngleMax")]
    if not all(isinstance(value, (float, int)) and math.isfinite(value) for value in angular):
        raise ValueError("lidar angular limits must be finite")
    if not -math.pi <= angular[0] < angular[1] <= math.pi or not -math.pi / 2 <= angular[2] < angular[3] <= math.pi / 2:
        raise ValueError("lidar angular limits out of range")
    minimum, maximum = scan.get("rangeMin"), scan.get("rangeMax")
    if (not isinstance(minimum, (float, int)) or not isinstance(maximum, (float, int))
            or not math.isfinite(minimum) or not math.isfinite(maximum) or not 0 <= minimum < maximum <= 1000):
        raise ValueError("invalid lidar metric range limits")
    if type(exclude_near_m) not in (int, float) or not 0 <= exclude_near_m < maximum:
        raise ValueError("near-field calibration mask must be finite and below sensor range")
    azimuth, elevation = np.meshgrid(np.linspace(angular[0], angular[1], horizontal),
                                     np.linspace(angular[2], angular[3], vertical))
    unit = np.stack([np.cos(elevation) * np.cos(azimuth), np.cos(elevation) * np.sin(azimuth),
                     np.sin(elevation)], axis=-1).reshape(-1, 3)
    hit = np.isfinite(ranges) & (ranges >= minimum) & (ranges < maximum)
    valid = hit & (ranges >= exclude_near_m)
    return {"points": (unit[valid] * ranges[valid, None]).tolist(),
            "validRayIndices": np.flatnonzero(valid).tolist(),
            "totalRayCount": len(ranges), "invalidOrNoHitCount": int(np.sum(~hit)),
            "stationaryNearFieldMaskM": exclude_near_m,
            "nearFieldExcludedHitCount": int(np.sum(hit & ~valid)),
            "simulatorPoseRead": False}


def voxel_sample(points, *, cell_m=.2, maximum_points=4096):
    """Density equalization retaining actual returns, not synthesized centroids."""
    array = np.asarray(points, dtype=float)
    if (array.ndim != 2 or array.shape[1] != 3 or not 1 <= len(array) <= 262_144
            or not np.isfinite(array).all() or not .02 <= cell_m <= 1.
            or type(maximum_points) is not int or not 12 <= maximum_points <= 4096):
        raise ValueError("invalid bounded voxel sampling input")
    cells = np.floor(array / cell_m).astype(np.int64)
    _, indices = np.unique(cells, axis=0, return_index=True)
    indices = np.sort(indices)
    if len(indices) > maximum_points:
        indices = indices[np.linspace(0, len(indices) - 1, maximum_points, dtype=int)]
    return {"points": array[indices].tolist(), "inputIndices": indices.tolist(), "cellM": cell_m,
            "inputCount": len(array), "outputCount": len(indices)}


def _numbers(text, count, name):
    result = [float(value) for value in (text or "").split()]
    if len(result) != count or not all(math.isfinite(value) for value in result):
        raise ValueError(f"invalid static CAD {name}")
    return np.asarray(result)


def _pose(element):
    node = element.find("pose")
    if node is not None and (node.get("relative_to") or node.get("degrees") == "true"):
        raise ValueError("surveyed CAD accepts only local radian poses")
    values = np.zeros(6) if node is None else _numbers(node.text, 6, "pose")
    matrix = np.eye(4)
    matrix[:3, :3] = Rotation.from_euler("xyz", values[3:]).as_matrix()
    matrix[:3, 3] = values[:3]
    return matrix


def load_surveyed_scene(sdf_path):
    """Read bounded static box/plane geometry; ignore every non-static model."""
    path = Path(sdf_path)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("surveyed scene exceeds 4 MiB")
    root = ET.fromstring(path.read_text())
    world = root.find("world")
    if world is None:
        raise ValueError("surveyed CAD requires one SDF world")
    surfaces = []
    for model in world.findall("model"):
        if (model.findtext("static") or "").strip().lower() != "true":
            continue
        model_pose = _pose(model)
        for link in model.findall("link"):
            link_pose = model_pose @ _pose(link)
            for collision in link.findall("collision"):
                transform = link_pose @ _pose(collision)
                geometry = collision.find("geometry")
                box = geometry.find("box") if geometry is not None else None
                plane = geometry.find("plane") if geometry is not None else None
                if box is not None:
                    size = _numbers(box.findtext("size"), 3, "box size")
                    kind = "box"
                elif plane is not None:
                    normal = _numbers(plane.findtext("normal"), 3, "plane normal")
                    if not np.allclose(normal, [0., 0., 1.], atol=1e-9, rtol=0):
                        raise ValueError("surveyed plane normal must be local +Z")
                    size = _numbers(plane.findtext("size"), 2, "plane size")
                    kind = "plane"
                else:
                    raise ValueError("unsupported surveyed static collision geometry")
                if np.any(size <= 0) or np.any(size > 1000):
                    raise ValueError("static CAD dimensions must be positive bounded metres")
                surfaces.append({"name": "/".join([model.get("name", ""), link.get("name", ""),
                                                   collision.get("name", "")]),
                                 "kind": kind, "size": size.tolist(),
                                 "localToWorld": transform.tolist()})
    if not surfaces or len(surfaces) > 128:
        raise ValueError("survey requires 1..128 static collision geometries")
    return {"schemaVersion": "simulation.surveyed-cad.v1", "frameId": "world",
            "surfaces": surfaces, "revision": _hash(surfaces),
            "robotOrSensorPosesRead": False}


def sample_surveyed_scene(scene, *, spacing_m=.22, maximum_points=4096,
                          bounds=((-5., -5., 0.), (5., 5., 3.)), seed=0):
    """Sample fixed CAD faces inside an a priori ROI, independent of any scan."""
    if (type(maximum_points) is not int or not 12 <= maximum_points <= 4096
            or type(seed) is not int or not 0 <= seed < 2**32
            or not isinstance(spacing_m, (int, float)) or isinstance(spacing_m, bool)
            or not .05 <= spacing_m <= 1.):
        raise ValueError("invalid bounded CAD sampling settings")
    limits = np.asarray(bounds, dtype=float)
    if limits.shape != (2, 3) or not np.isfinite(limits).all() or np.any(limits[1] <= limits[0]):
        raise ValueError("CAD sampling bounds must be finite lower/upper xyz")
    points = []
    generated_count = 0
    for surface in scene["surfaces"]:
        size = np.asarray(surface["size"])
        transform = np.asarray(surface["localToWorld"])
        axes = range(3) if surface["kind"] == "box" else (2,)
        for axis in axes:
            tangent = [index for index in range(3) if index != axis]
            dimensions = size[tangent] if surface["kind"] == "box" else size
            coordinates = [np.linspace(-extent / 2, extent / 2,
                                       max(2, math.ceil(extent / spacing_m) + 1))
                           for extent in dimensions]
            generated_count += len(coordinates[0]) * len(coordinates[1]) * (2 if surface["kind"] == "box" else 1)
            if generated_count > 250_000:
                raise ValueError("CAD surface sampling exceeds bounded point budget")
            grid = np.stack(np.meshgrid(*coordinates, indexing="ij"), axis=-1).reshape(-1, 2)
            sides = (-1., 1.) if surface["kind"] == "box" else (0.,)
            for side in sides:
                local = np.zeros((len(grid), 3))
                local[:, tangent] = grid
                if surface["kind"] == "box":
                    local[:, axis] = side * size[axis] / 2
                world = local @ transform[:3, :3].T + transform[:3, 3]
                mask = np.all(world >= limits[0] - 1e-8, axis=1) & np.all(world <= limits[1] + 1e-8, axis=1)
                points.extend(world[mask])
    result = np.unique(np.round(np.asarray(points), decimals=10), axis=0)
    if len(result) < 12:
        raise ValueError("CAD ROI contains fewer than 12 sampled points")
    if len(result) > maximum_points:
        selected = np.random.default_rng(seed).choice(len(result), maximum_points, replace=False)
        result = result[np.sort(selected)]
    return {"schemaVersion": "simulation.surveyed-cloud.v1", "sceneRevision": scene["revision"],
            "sampling": {"spacingM": spacing_m, "maximumPoints": maximum_points,
                         "bounds": limits.tolist(), "seed": seed},
            "points": result.tolist(), "pointsSha256": _hash(result.tolist())}


def nearest_surface_distances(points_world, scene):
    """Analytic distance to static CAD surfaces, without sampled-cloud aliasing."""
    points = np.asarray(points_world, dtype=float)
    if (points.ndim != 2 or points.shape[1] != 3 or not 1 <= len(points) <= 100_000
            or not np.isfinite(points).all()):
        raise ValueError("surface distance requires bounded finite Nx3 points")
    minimum = np.full(len(points), np.inf)
    for surface in scene["surfaces"]:
        transform, size = np.asarray(surface["localToWorld"]), np.asarray(surface["size"])
        local = (points - transform[:3, 3]) @ transform[:3, :3]
        if surface["kind"] == "box":
            offset = np.abs(local) - size / 2
            distance = np.linalg.norm(np.maximum(offset, 0.), axis=1)
            inside = np.all(offset < 0., axis=1)
            distance[inside] = np.min(-offset[inside], axis=1)
        else:
            tangent = np.maximum(np.abs(local[:, :2]) - size / 2, 0.)
            distance = np.sqrt(np.sum(tangent**2, axis=1) + local[:, 2]**2)
        minimum = np.minimum(minimum, distance)
    return minimum


def calibrate_in_known_scene(sensor_points, scene, reference_cloud, *, source_evidence,
                             target_evidence, surveyed_base_to_world=None, seed=0,
                             max_correspondence_m=.4, max_rmse_m=.25, min_overlap=.25,
                             holdout_residual_m=.08, min_holdout_inlier_ratio=.85):
    """Fit 80% of actual returns against CAD, evaluate remaining 20% analytically.

    Point-cloud gates initialize a coarse estimate; analytic CAD-surface robust
    refinement then estimates the pose without sampled-reference aliasing. The
    disjoint holdout gate determines acceptance. Dynamic returns are retained in
    that denominator. No true sensor pose is an input.
    """
    points = np.asarray(sensor_points, dtype=float)
    if (points.ndim != 2 or points.shape[1] != 3 or not 30 <= len(points) <= 100_000
            or not np.isfinite(points).all() or type(seed) is not int or not 0 <= seed < 2**32):
        raise ValueError("known-scene calibration requires bounded finite measured Nx3 points")
    if (not isinstance(holdout_residual_m, (int, float)) or not 0 < holdout_residual_m <= 1.
            or not isinstance(min_holdout_inlier_ratio, (int, float))
            or not 0 < min_holdout_inlier_ratio <= 1.):
        raise ValueError("invalid holdout gates")
    if reference_cloud.get("sceneRevision") != scene["revision"]:
        raise ValueError("reference cloud belongs to a different surveyed scene")
    if reference_cloud.get("pointsSha256") != _hash(reference_cloud["points"]):
        raise ValueError("surveyed reference points do not match their digest")
    if target_evidence.get("mapRevision") != scene["revision"] or target_evidence.get("frameId") != "world":
        raise ValueError("target evidence must name the surveyed CAD revision and world frame")
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(len(points))
    holdout_indices = shuffled[:max(6, len(points) // 5)]
    fit_indices = shuffled[len(holdout_indices):][:4096]
    result = register_point_clouds(points[fit_indices], reference_cloud["points"],
                                   source_evidence=source_evidence, target_evidence=target_evidence,
                                   max_correspondence_m=max_correspondence_m,
                                   max_rmse_m=max_rmse_m, min_overlap=min_overlap,
                                   min_minor_structure_overlap=0., return_candidates=True)
    output = {"schemaVersion": "simulation.known-scene-calibration.v1",
              "method": "measured-lidar-to-independent-static-cad",
              "accepted": False, "registration": result, "sceneRevision": scene["revision"],
              "referencePointsSha256": reference_cloud["pointsSha256"],
              "measuredPointsSha256": _hash(points.tolist()), "seed": seed,
              "fitIndices": fit_indices.tolist(), "holdoutIndices": holdout_indices.tolist(),
              "unusedIndices": shuffled[len(holdout_indices) + len(fit_indices):].tolist(),
              "holdoutResidualLimitM": holdout_residual_m,
              "minimumHoldoutInlierRatio": min_holdout_inlier_ratio,
              "estimatedSensorToWorld": None, "estimatedSensorToBase": None,
              "truthTransformUsedForEstimation": False}
    proposals = result.get("nonExecutableCandidates", [])
    if not proposals:
        output["code"] = result["code"]
        return output

    def residual(parameters):
        rotation = Rotation.from_rotvec(parameters[:3]).as_matrix()
        moved = points[fit_indices] @ rotation.T + parameters[3:]
        return nearest_surface_distances(moved, scene)

    refinements = []
    for index, proposal in enumerate(proposals):
        estimate = np.asarray(proposal["sourceToTarget"])
        initial = np.concatenate([Rotation.from_matrix(estimate[:3, :3]).as_rotvec(), estimate[:3, 3]])
        solved = least_squares(residual, initial, loss="soft_l1", f_scale=.02, max_nfev=80)
        if not solved.success or not np.isfinite(solved.x).all():
            continue
        # Candidate selection uses fitting points only. Clipping bounds dynamic
        # returns without deleting them or looking at holdout/GT pose errors.
        errors = residual(solved.x)
        refinements.append({"candidateIndex": index, "parameters": solved.x.tolist(),
                            "evaluations": solved.nfev,
                            "fitScore": float(np.mean(np.minimum(errors, .5)**2)),
                            "fitMedianM": float(np.median(errors)),
                            "fitP95M": float(np.quantile(errors, .95))})
    if not refinements:
        output["code"] = "CAD_REFINEMENT_DID_NOT_CONVERGE"
        return output
    refinements.sort(key=lambda item: item["fitScore"])
    best = refinements[0]
    output["candidateRefinements"] = refinements
    for other in refinements[1:]:
        if other["fitScore"] > max(best["fitScore"] * 1.25, best["fitScore"] + .0004):
            continue
        left, right = np.asarray(best["parameters"]), np.asarray(other["parameters"])
        angle = (Rotation.from_rotvec(left[:3]).inv() * Rotation.from_rotvec(right[:3])).magnitude()
        if np.linalg.norm(left[3:] - right[3:]) > .1 or angle > .03:
            output["code"] = "CAD_CANDIDATES_AMBIGUOUS"
            return output
    parameters = np.asarray(best["parameters"])
    estimate = np.eye(4)
    estimate[:3, :3] = Rotation.from_rotvec(parameters[:3]).as_matrix()
    estimate[:3, 3] = parameters[3:]
    output["refinement"] = {"method": "analytic-static-cad-soft-l1", **best,
                            "holdoutUsedForCandidateSelection": False, "poseTruthUsed": False}
    held_points = np.vstack([transform_points(points[holdout_indices[start:start + 4096]], estimate)
                             for start in range(0, len(holdout_indices), 4096)])
    residuals = nearest_surface_distances(held_points, scene)
    output["holdout"] = {"count": len(residuals), "rmseM": float(np.sqrt(np.mean(residuals**2))),
                         "medianM": float(np.median(residuals)),
                         "p95M": float(np.quantile(residuals, .95)),
                         "inlierRatio": float(np.mean(residuals <= holdout_residual_m))}
    planes, _ = _measured_planes(points[fit_indices], .025)
    if len(planes) >= 3:
        distances = np.stack([np.abs(points[holdout_indices] @ normal + offset)
                              for normal, offset, _ in planes])
        minor = np.min(distances, axis=0) > .025
        output["holdout"]["minorStructureCount"] = int(np.sum(minor))
        output["holdout"]["minorStructureInlierRatio"] = float(np.mean(residuals[minor] <= holdout_residual_m)) if np.any(minor) else None
        if np.sum(minor) >= 12 and output["holdout"]["minorStructureInlierRatio"] < .65:
            output["code"] = "HELD_OUT_MINOR_STRUCTURE_REJECTED"
            return output
    if output["holdout"]["inlierRatio"] < min_holdout_inlier_ratio:
        output["code"] = "HELD_OUT_CAD_RESIDUAL_REJECTED"
        return output
    output.update(accepted=True, code="ACCEPTED", estimatedSensorToWorld=estimate.tolist())
    if surveyed_base_to_world is not None:
        # Validate the independently surveyed station transform through the same
        # SE3 check, then derive sensor extrinsics; never read dynamic SDF poses.
        transform_points([[0., 0., 0.]], surveyed_base_to_world)
        output["estimatedSensorToBase"] = (np.linalg.inv(surveyed_base_to_world) @ estimate).tolist()
        output["surveyedBaseToWorld"] = np.asarray(surveyed_base_to_world).tolist()
    return output
