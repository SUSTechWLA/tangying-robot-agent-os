"""Offline measured Gazebo alignment acceptance; never controls the simulator.

Estimation reads lidar ranges and static CAD only. Simulator poses are accessed
after every estimate has been written, solely for an explicitly separate audit.
Repeated processing of one snapshot is one dataset, not independent trials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation
from tangying_robot_gateway.registered_alignment import register_point_clouds, transform_points

from sim.gazebo.field_calibration import (
    calibrate_in_known_scene,
    lidar_points_from_scan,
    load_surveyed_scene,
    sample_surveyed_scene,
    voxel_sample,
)


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(robot, snapshot):
    return {"robotId": robot, "frameId": snapshot["scans"][robot]["frame"],
            "mapId": "field-validation", "mapRevision": "captured-local-map-1",
            "sourceId": "gazebo-gpu-lidar", "observationId": f"snapshot-{snapshot['sequence']}-{robot}",
            "observedAtUnixMs": snapshot["capturedAtUnixMs"]}


def _scan_time(scan):
    stamp = scan["header"]["stamp"]
    sec, nsec = stamp.get("sec", 0), stamp.get("nsec", 0)
    if isinstance(sec, str) and sec.isdecimal():
        sec = int(sec)
    if type(sec) is not int or type(nsec) is not int or sec < 0 or not 0 <= nsec < 1_000_000_000:
        raise ValueError("invalid Gazebo sensor timestamp")
    return sec + nsec / 1e9


def _survey_matrices(plan):
    if (plan.get("schemaVersion") != "gazebo.independent_calibration_capture_plan.v1"
            or plan.get("declaredBeforeAcquisition") is not True
            or set(plan.get("surveyedBasePoses", {})) != {"ground-a", "ground-b", "uav-a"}):
        raise ValueError("survey plan must predeclare the three stationary base poses")
    matrices = {}
    for robot, pose in plan["surveyedBasePoses"].items():
        quaternion = np.asarray(pose["quaternionWXYZ"], dtype=float)
        position = np.asarray(pose["position"], dtype=float)
        if (quaternion.shape != (4,) or position.shape != (3,)
                or not np.isfinite(quaternion).all() or not np.isfinite(position).all()
                or abs(np.linalg.norm(quaternion) - 1.) > 1e-6):
            raise ValueError("survey pose must contain finite xyz and a normalized quaternion")
        w, x, y, z = quaternion
        matrix = np.eye(4)
        matrix[:3, :3] = Rotation.from_quat([x, y, z, w]).as_matrix()
        matrix[:3, 3] = position
        transform_points([[0., 0., 0.]], matrix)
        matrices[robot] = matrix
    return matrices


def _truth_audit_only(snapshot, sdf_path, results, peer):
    world = ET.parse(sdf_path).getroot().find("world")
    truth, audit = {}, {}
    for robot, result in results.items():
        model = world.find(f"model[@name='{robot}']")
        pose_text = model.find("link[@name='base_link']/sensor[@name='lidar']/pose").text
        mount = np.array([float(value) for value in pose_text.split()])
        mount_matrix = np.eye(4)
        mount_matrix[:3, :3] = Rotation.from_euler("xyz", mount[3:]).as_matrix()
        mount_matrix[:3, 3] = mount[:3]
        state = snapshot["states"][robot]
        base = np.eye(4)
        w, x, y, z = state["quaternionWXYZ"]
        base[:3, :3] = Rotation.from_quat([x, y, z, w]).as_matrix()
        base[:3, 3] = state["position"]
        truth[robot] = base @ mount_matrix
        offset = state["simulationTime"] - _scan_time(snapshot["scans"][robot])
        audit[robot] = {"accepted": result["accepted"], "code": result["code"],
                        "truthStateMinusSensorTimeS": offset,
                        "truthUsedForEstimation": False}
        if result["accepted"]:
            estimate = np.asarray(result["estimatedSensorToWorld"])
            audit[robot].update(_pose_error(estimate, truth[robot]))
            if result.get("estimatedSensorToBase") is not None:
                audit[robot]["sensorToBaseErrorAgainstConfiguredMount"] = _pose_error(
                    np.asarray(result["estimatedSensorToBase"]), mount_matrix)
    if peer["accepted"]:
        audit["peer-ground-b-to-ground-a"] = _pose_error(np.asarray(peer["sourceToTarget"]),
                                                        np.linalg.inv(truth["ground-a"]) @ truth["ground-b"])
    else:
        audit["peer-ground-b-to-ground-a"] = {"accepted": False, "code": peer["code"]}
    return {"schemaVersion": "experiment.truth-evaluation.v1", "estimationAlreadyCompleted": True,
            "sensorAndStateTimestampsMayDiffer": True, "cases": audit}


def _pose_error(estimate, expected):
    rotation = Rotation.from_matrix(expected[:3, :3].T @ estimate[:3, :3])
    return {"translationErrorM": float(np.linalg.norm(estimate[:3, 3] - expected[:3, 3])),
            "rotationErrorRad": float(rotation.magnitude())}


def run(snapshot_path, sdf_path, output, *, survey_plan_path=None):
    if output.exists():
        raise FileExistsError("alignment evidence output must be a new directory")
    output.mkdir(parents=True)
    shutil.copy2(snapshot_path, output / "sensor-snapshot.json")
    shutil.copy2(sdf_path, output / "world.sdf")
    survey, survey_plan = {}, None
    if survey_plan_path is not None:
        survey_plan = json.loads(survey_plan_path.read_text())
        if survey_plan.get("worldSha256") != _sha(sdf_path):
            raise ValueError("survey plan and acquisition world digest do not match")
        survey = _survey_matrices(survey_plan)
        shutil.copy2(survey_plan_path, output / "survey-plan.json")
    repository = Path(__file__).resolve().parents[2]
    source_paths = [Path(__file__), repository / "sim/gazebo/field_calibration.py",
                    repository / "robot/gateway/tangying_robot_gateway/registered_alignment.py"]
    source_manifest = []
    for path in source_paths:
        destination = output / "frozen-source" / path.relative_to(repository)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        source_manifest.append({"path": str(path.relative_to(repository)), "sha256": _sha(path)})
    snapshot = json.loads(snapshot_path.read_text())
    if survey_plan is not None and not survey_plan["declaredAtUnixMs"] < snapshot["capturedAtUnixMs"]:
        raise ValueError("survey plan must have been declared before the input capture")
    scene = load_surveyed_scene(sdf_path)
    reference = sample_surveyed_scene(scene)
    _write(output / "surveyed-static-scene.json", scene)
    _write(output / "surveyed-reference-cloud.json", reference)
    results = {}
    started = time.monotonic()
    for robot in ("ground-a", "ground-b", "uav-a"):
        processing = lidar_points_from_scan(snapshot["scans"][robot], exclude_near_m=.6)
        source = _identity(robot, snapshot)
        target = dict(source, robotId="survey-station", frameId="world", mapRevision=scene["revision"],
                      sourceId="surveyed-static-cad", observationId="surveyed-cad-1")
        result = calibrate_in_known_scene(processing["points"], scene, reference,
                                          source_evidence=source, target_evidence=target,
                                          surveyed_base_to_world=survey.get(robot))
        _write(output / f"calibration-{robot}.json", {"processing": processing, "result": result})
        results[robot] = result
        print(robot, result["code"], flush=True)
    # Peer map experiment uses the complete valid-return cloud, retaining body
    # returns; its separately declared structure gate must reject false matches.
    clouds = {robot: voxel_sample(lidar_points_from_scan(snapshot["scans"][robot])["points"], cell_m=.2)
              for robot in ("ground-a", "ground-b")}
    peer = register_point_clouds(clouds["ground-b"]["points"], clouds["ground-a"]["points"],
                                 source_evidence=_identity("ground-b", snapshot),
                                 target_evidence=_identity("ground-a", snapshot),
                                 max_correspondence_m=.4, max_rmse_m=.16, min_overlap=.65)
    _write(output / "peer-registration.json", {"clouds": clouds, "result": peer})
    print("peer", peer["code"], flush=True)
    _write(output / "truth-evaluation-only.json", _truth_audit_only(snapshot, sdf_path, results, peer))
    summary = {"schemaVersion": "experiment.measured-field-alignment.v1",
               "sourceSnapshotSha256": _sha(snapshot_path), "sourceManifest": source_manifest,
               "independentSnapshotCount": 1, "offlineProcessingOnly": True,
               "calibrationAccepted": sum(result["accepted"] for result in results.values()),
               "calibrationCases": len(results), "peerAccepted": peer["accepted"],
               "elapsedWallSeconds": time.monotonic() - started,
               "hardwareValidation": False, "estimatesUsedToControlRobots": False,
               "predeclaredSurveyUsedAfterEstimationOnly": bool(survey),
               "sensorToBaseEstimates": sum(result["estimatedSensorToBase"] is not None
                                            for result in results.values()),
               "files": [{"path": str(path.relative_to(output)), "sha256": _sha(path)}
                         for path in sorted(output.rglob("*")) if path.is_file()]}
    _write(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--survey-plan", type=Path)
    arguments = parser.parse_args()
    run(arguments.snapshot, arguments.world, arguments.output, survey_plan_path=arguments.survey_plan)


if __name__ == "__main__":
    main()
