"""Derive a local-map relation through an accepted, shared surveyed CAD anchor.

This is a known-environment fallback, not a successful direct registration of
unknown maps. It consumes accepted estimates and never simulator pose truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

import numpy as np
from tangying_robot_gateway.registered_alignment import transform_points


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _canonical_hash(value):
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def compose_shared_cad_anchor(source_calibration, target_calibration, *, source_basis,
                              target_basis, expected_scene_revision):
    """Return target_sensor_T_source_sensor with exact evidence-basis admission."""
    if not isinstance(expected_scene_revision, str) or not re.fullmatch("[0-9a-f]{64}", expected_scene_revision):
        raise ValueError("shared anchor requires the expected surveyed scene digest")
    if source_basis.get("robotId") == target_basis.get("robotId"):
        raise ValueError("shared inter-robot anchoring requires distinct robots")
    transforms, anchors = [], []
    for result, expected in ((source_calibration, source_basis), (target_calibration, target_basis)):
        if not isinstance(result, Mapping) or not isinstance(expected, Mapping):
            raise TypeError("calibration result and expected basis must be mappings")
        if (result.get("schemaVersion") != "simulation.known-scene-calibration.v1"
                or result.get("accepted") is not True or result.get("code") != "ACCEPTED"
                or result.get("sceneRevision") != expected_scene_revision):
            raise ValueError("both calibrations must be accepted in the exact surveyed scene")
        registration = result.get("registration", {})
        source, anchor = registration.get("sourceEvidence", {}), registration.get("targetEvidence", {})
        for field in ("robotId", "frameId", "mapId", "mapRevision", "sourceId", "observationId"):
            if not isinstance(expected.get(field), str) or not expected[field] or source.get(field) != expected[field]:
                raise ValueError(f"calibration source {field} basis mismatch")
        if (anchor.get("frameId") != "world" or anchor.get("mapRevision") != expected_scene_revision
                or anchor.get("mapId") != expected["mapId"]
                or anchor.get("robotId") != "survey-station"
                or anchor.get("sourceId") != "surveyed-static-cad"):
            raise ValueError("calibration target is not the expected shared surveyed CAD anchor")
        transform_points([[0., 0., 0.]], result.get("estimatedSensorToWorld"))
        transforms.append(np.asarray(result["estimatedSensorToWorld"], dtype=float))
        anchors.append({field: anchor[field] for field in ("robotId", "frameId", "mapId", "mapRevision", "sourceId")})
    if anchors[0] != anchors[1] or source_basis["mapId"] != target_basis["mapId"]:
        raise ValueError("calibrations have different anchor map or frame identities")
    if (source_calibration.get("referencePointsSha256") != target_calibration.get("referencePointsSha256")
            or not re.fullmatch("[0-9a-f]{64}", source_calibration.get("referencePointsSha256", ""))):
        raise ValueError("calibrations do not bind the same surveyed reference cloud")
    matrix = np.linalg.inv(transforms[1]) @ transforms[0]
    transform_points([[0., 0., 0.]], matrix)
    result = {"schemaVersion": "simulation.shared-cad-anchor.v1", "accepted": True,
              "code": "ACCEPTED_SHARED_KNOWN_CAD_ANCHOR", "sourceToTarget": matrix.tolist(),
              "method": "inverse-target-sensor-to-world-times-source-sensor-to-world",
              "sourceBasis": dict(source_basis), "targetBasis": dict(target_basis),
              "sharedAnchor": anchors[0], "sceneRevision": expected_scene_revision,
              "referencePointsSha256": source_calibration["referencePointsSha256"],
              "sourceHashes": {"sourceCalibrationCanonicalSha256": _canonical_hash(source_calibration),
                               "targetCalibrationCanonicalSha256": _canonical_hash(target_calibration)},
              "knownSurveyedEnvironmentRequired": True, "directCloudRegistrationSucceeded": False,
              "simulatorTruthUsedForComposition": False, "grantsExecution": False,
              "estimatesUsedToControlRobots": False}
    result["revision"] = _canonical_hash(result)
    return result


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def derive(alignment_directory, output):
    """Verify frozen input hashes and create a separate, non-overwriting record."""
    from sim.gazebo.validate_field_alignment import _identity, _truth_audit_only

    if output.exists():
        raise FileExistsError("derived shared-anchor output must be a new directory")
    manifest = json.loads((alignment_directory / "summary.json").read_text())
    checked = {}
    for item in manifest["files"]:
        relative = PurePosixPath(item["path"])
        if relative.is_absolute() or ".." in relative.parts or str(relative) in checked:
            raise ValueError("invalid or duplicate upstream manifest path")
        file = alignment_directory / str(relative)
        if file.is_symlink() or not file.is_file() or _sha(file.read_bytes()) != item["sha256"]:
            raise ValueError("upstream frozen alignment evidence digest mismatch")
        checked[str(relative)] = item["sha256"]
    required = {"calibration-ground-a.json", "calibration-ground-b.json", "sensor-snapshot.json",
                "surveyed-static-scene.json", "world.sdf", "peer-registration.json"}
    if not required <= checked.keys():
        raise ValueError("upstream manifest omits required shared-anchor inputs")
    snapshot = json.loads((alignment_directory / "sensor-snapshot.json").read_text())
    calibrations = {robot: json.loads((alignment_directory / f"calibration-{robot}.json").read_text())["result"]
                    for robot in ("ground-a", "ground-b")}
    scene = json.loads((alignment_directory / "surveyed-static-scene.json").read_text())
    result = compose_shared_cad_anchor(calibrations["ground-b"], calibrations["ground-a"],
                                       source_basis=_identity("ground-b", snapshot),
                                       target_basis=_identity("ground-a", snapshot),
                                       expected_scene_revision=scene["revision"])
    direct = json.loads((alignment_directory / "peer-registration.json").read_text())["result"]
    output.mkdir(parents=True)
    _write(output / "fallback-registration.json", {
        "result": result, "directCloudRegistration": {"accepted": direct["accepted"], "code": direct["code"]},
        "sourceFileHashes": {name: checked[name] for name in sorted(required)}})
    # Composition is already written before any simulator truth is evaluated.
    audit = _truth_audit_only(snapshot, alignment_directory / "world.sdf", calibrations, result)
    _write(output / "truth-evaluation-only.json", {"simulatorTruthUsedForComposition": False,
        "compositionWrittenBeforeTruthEvaluation": True,
        "sensorAndStateTimestampsMayDiffer": True,
        "poseError": audit["cases"]["peer-ground-b-to-ground-a"]})
    for path in (Path(__file__), Path(__file__).with_name("validate_field_alignment.py")):
        shutil.copy2(path, output / path.name)
    _write(output / "summary.json", {"schemaVersion": "experiment.shared-cad-anchor.v1",
        "parentAlignmentSummarySha256": _sha((alignment_directory / "summary.json").read_bytes()),
        "newMeasurementsCollected": False, "estimatesUsedToControlRobots": False,
        "files": [{"path": path.name, "sha256": _sha(path.read_bytes())}
                  for path in sorted(output.iterdir()) if path.is_file()]})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alignment-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    derive(arguments.alignment_directory, arguments.output)


if __name__ == "__main__":
    main()
