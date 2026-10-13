"""Replay versioned semantic services against a copied, verified SLAM map.

This is an offline software experiment. Recorded poses receive a disclosed
controlled-clock timestamp; no new sensor observation or physical execution is
claimed. Labels name measured point selections, not recognized real-world objects.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from google.protobuf.json_format import MessageToDict
from tangying_robot_gateway import robot_workflow, semantic_cloud_services
from tangying_robot_gateway.dense_slam import compose
from tangying_robot_gateway.grid_navigation import pose_is_clear
from tangying_robot_gateway.map_catalog import MapCatalog
from tangying_robot_gateway.map_manifest import load_manifest, verify_artifacts
from tangying_robot_gateway.map_pipeline import decode_lod
from tangying_robot_gateway.robot_workflow import RobotWorkflow
from tangying_robot_gateway.service_registry import ServiceRegistry
from tangying_robot_proto.robot.v1 import robot_pb2

ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2).encode()


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def git_output(*arguments):
    result = subprocess.run(["git", *arguments], cwd=ROOT, capture_output=True, check=False)
    return result.stdout if result.returncode == 0 else b"unavailable"


class Evidence:
    def __init__(self, output):
        self.output = output
        self.requests = []
        self.current_case = "setup"
        self.projection_sizes = []

    def save(self, name, value, *, raw=False):
        path = self.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = value if raw else canonical(value)
        with path.open("xb") as stream:
            stream.write(payload)
        return {"file": name, "bytes": len(payload), "sha256": digest(payload)}

    def call(self, registry, name, parameters=None, *, expect_code="OK"):
        index = len(self.requests) + 1
        request = robot_pb2.ServiceRequest(robot_id=registry.robot_id, name=name,
                                           request_id=f"semantic-replay-{index:04d}")
        request.parameters.update(parameters or {})
        base = f"requests/{index:04d}-{name}"
        input_record = self.save(base + "-request.json", MessageToDict(request))
        started = time.perf_counter()
        response = registry.call(request)
        elapsed = time.perf_counter() - started
        payload = MessageToDict(response)
        output_record = self.save(base + "-response.json", payload)
        record = {"caseId": self.current_case, "service": name, "elapsedSeconds": elapsed,
                  "request": input_record, "response": output_record, "ok": response.ok,
                  "code": response.code, "expectedCode": expect_code}
        self.requests.append(record)
        result = MessageToDict(response.result)
        if response.ok:
            fields = registry.services[name].contract.get("planningFields", [])
            if fields:
                projected = {key: result[key] for key in fields if key in result}
                record["planningProjection"] = self.save(base + "-planning-projection.json", projected)
                # Conservative JSON byte measure includes Unicode escaping used by downstream Go.
                self.projection_sizes.append({"service": name, "bytes": semantic_cloud_services._view_bytes(projected)})
        if expect_code is None:
            require(not response.ok, f"{name} unexpectedly accepted the negative case")
        else:
            require(response.code == expect_code and response.ok == (expect_code == "OK"),
                    f"{name}: expected {expect_code}, got {response.code}: {response.message}")
        return result


def measured_labels(points, manifest, map_pose):
    """Deterministic manual-test selection rule; no simulator labels or detector."""
    centre = np.asarray(map_pose[:2])
    low, high = points.min(axis=0).astype(float), points.max(axis=0).astype(float)
    region_bounds = None
    for half_width in (.5, .75, 1., 1.5, 2.5):
        box_low = np.array([centre[0] - half_width, centre[1] - half_width, low[2] - .01])
        box_high = np.array([centre[0] + half_width, centre[1] + half_width, high[2] + .01])
        mask = np.all((points >= box_low) & (points <= box_high), axis=1)
        if mask.any():
            region_bounds = {"min": box_low.tolist(), "max": box_high.tolist()}
            selected = points[mask]
            break
    require(region_bounds is not None, "no measured points around any bounded replay pose region")
    point = selected[np.argmin(np.linalg.norm(selected[:, :2] - centre, axis=1))].astype(float)
    object_low = np.maximum(point - .04, region_bounds["min"])
    object_high = np.minimum(point + .04, region_bounds["max"])
    require(np.all(object_high > object_low), "fixture selection has degenerate bounds")
    common = {"source": {"kind": "operator", "reference":
              "offline replay protocol: deterministic AABB selection of measured points; not object recognition"},
              "confidence": 1., "observedAtUnixMs": manifest["createdAtUnixMs"]}
    return [
        {**copy.deepcopy(common), "id": "review-region-1", "name": "测量区域1",
         "category": "observed_region", "kind": "region", "aliases": ["review-region-1"],
         "bounds": region_bounds},
        {**copy.deepcopy(common), "id": "review-fixture-1", "name": "人工选取测量点簇1",
         "category": "unclassified_fixture", "kind": "object", "aliases": ["review-fixture-1"],
         "bounds": {"min": object_low.tolist(), "max": object_high.tolist()}},
    ]


def select_pose(directory, manifest, metadata, radius):
    grid = MapCatalog.navigation_grid(directory, manifest)
    anchor = np.asarray(metadata["mapFromWorld"], dtype=float)
    for observation in metadata.get("observations", []):
        odometry = observation.get("odometry")
        if isinstance(odometry, list) and len(odometry) == 3:
            mapped = compose(anchor, odometry)
            if pose_is_clear(grid, mapped[:2], radius):
                z = float(observation.get("baseZ", .035))
                return {"source": "slam_session_recorded_odometry", "sourceObservationId": observation.get("id"),
                        "originalStampUnixMs": observation.get("stamp"), "worldSE2": odometry,
                        "mapSE2": mapped.tolist(), "baseZ": z}
    raise ValueError("map has no recorded odometry sample with certified footprint clearance")


def evaluate(map_directory, output, *, evidence_origin="historical_gazebo_measurement"):
    map_directory, output = Path(map_directory).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    evidence = Evidence(output)
    started = time.perf_counter()
    report = {"schemaVersion": "semantic.cloud.replay.v1", "passed": False, "cases": [],
              "evidenceOriginDeclaration": evidence_origin, "replay": True,
              "liveLocalizationTested": False, "newGazeboMotionTested": False,
              "physicalHardwareTested": False, "automaticSemanticRecognitionTested": False,
              "physicalCommandAttempts": 0, "physicalCommandsExecuted": 0,
              "selectionMeaning": "operator-assigned geometric point groups, not detected object instances",
              "outputDirectory": str(output), "sourceDirectory": str(map_directory)}
    source_snapshot = {}
    physical_attempts = []
    captures = []
    try:
        manifest = load_manifest(map_directory)
        checks = verify_artifacts(manifest, map_directory)
        require(all(item.ok for item in checks), "input map artifact verification failed")
        paths = {"manifest.json", *(entry["href"] for entry in manifest["artifacts"].values())}
        source_snapshot = {name: digest((map_directory / name).read_bytes()) for name in sorted(paths)}
        target = output / "maps" / manifest["mapId"]
        for name in sorted(paths):
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(map_directory / name, destination)
        require(all(item.ok for item in verify_artifacts(manifest, target)), "copied map verification failed")
        metadata = json.loads((target / manifest["artifacts"]["slam_session"]["href"]).read_bytes())
        radius = float(metadata.get("footprintRadiusM", .30))
        pose = select_pose(target, manifest, metadata, radius)
        cloud, _ = decode_lod((target / manifest["artifacts"]["cloud"]["href"]).read_bytes())
        labels = measured_labels(cloud.xyz, manifest, pose["mapSE2"])
        clock = {"nowUnixMs": int(manifest["createdAtUnixMs"]) + 1000}
        sources = [Path(__file__), *sorted((ROOT / "robot/gateway/tangying_robot_gateway").glob("*.py"))]
        source_files = {str(path.relative_to(ROOT)): {"sha256": digest(path.read_bytes()), "bytes": path.stat().st_size}
                        for path in sources}
        for path in sources:
            evidence.save("provenance/sources/" + str(path.relative_to(ROOT)), path.read_bytes(), raw=True)
        patch_bytes = git_output("diff", "--binary", "HEAD", "--")
        evidence.save("provenance/worktree.patch", patch_bytes, raw=True)
        evidence.save("provenance/source-map-sha256.json", source_snapshot)
        evidence.save("provenance/source-code-sha256.json", source_files)
        evidence.save("provenance/run-config.json", {
            "mapManifest": manifest, "sourceMapFiles": source_snapshot,
            "gitHead": git_output("rev-parse", "HEAD").decode().strip(),
            "gitStatus": git_output("status", "--porcelain=v1").decode(),
            "worktreeDiffSha256": digest(patch_bytes), "python": sys.version, "numpy": np.__version__,
            "platform": platform.platform(), "clockMode": "controlled_offline_replay",
            "initialClockUnixMs": clock["nowUnixMs"], "recordedPose": pose,
            "captureTimestampPolicy": "inject controlled clock into recorded pose only; not a fresh observation",
            "evidenceOriginDeclaration": evidence_origin,
            "selectionRule": "first bounded square around first clear recorded odometry; nearest measured point cluster",
            "physicalDriver": "no driver connected; move port always raises", "labels": labels})

        def blocked_move(*args, **kwargs):
            physical_attempts.append({"arguments": repr(args), "keywords": repr(kwargs)})
            raise AssertionError("offline replay forbids every physical command")

        def capture():
            x, y, yaw = pose["worldSE2"]
            observation = robot_pb2.Observation(observation_id=f"offline-pose-replay-{len(captures) + 1}",
                                                wall_time_unix_ms=clock["nowUnixMs"])
            observation.robot_state.update({"base_pose": [x, y, pose["baseZ"], math.cos(yaw / 2), 0., 0., math.sin(yaw / 2)],
                                            "pose_fusion_source": "offline_recorded_pose_replay_not_live",
                                            "replay": True})
            captures.append({"observation": MessageToDict(observation), "source": pose,
                             "timestampInjected": True, "newSensorMeasurement": False})
            return observation

        def new_workflow():
            workflow = RobotWorkflow(robot_id=manifest["robotId"], root=output / "maps",
                calibration_get=lambda: {"revision": manifest["calibrationRevision"]},
                calibration_run=blocked_move, calibration_save=blocked_move, capture=capture,
                move=blocked_move, reserve=lambda: "offline-map-config", release=lambda _: None,
                survey_goals=list, semantic_workspaces=lambda _: [], footprint_radius=radius,
                world_frame_revision=metadata["worldFrameRevision"])
            registry = ServiceRegistry(manifest["robotId"])
            workflow.register(registry)
            return workflow, registry

        def run_case(identifier, purpose, action):
            evidence.current_case = identifier
            begin = len(evidence.requests)
            tick = time.perf_counter()
            result = {"id": identifier, "purpose": purpose, "controlledClockUnixMs": clock["nowUnixMs"], "passed": False}
            try:
                details = action()
                result.update(passed=True, details=details or {})
            except Exception as error:  # noqa: BLE001 - Preserve every attempted case, including harness failures.
                result.update(errorType=type(error).__name__, error=str(error))
            result.update(elapsedSeconds=time.perf_counter() - tick,
                          requestRange=[begin + 1, len(evidence.requests)],
                          requests=copy.deepcopy(evidence.requests[begin:]))
            report["cases"].append(result)
            evidence.save(f"cases/{identifier}.json", result)
            return result

        replay_time = SimpleNamespace(time=lambda: clock["nowUnixMs"] / 1000,
                                      monotonic=time.monotonic, sleep=time.sleep)
        with patch.object(robot_workflow, "time", replay_time), \
                patch.object(semantic_cloud_services, "_now", lambda: clock["nowUnixMs"]):
            workflow, registry = new_workflow()
            evidence.call(registry, "mapping.activate", {"mapId": manifest["mapId"]})
            state = {}

            def empty():
                overview = evidence.call(registry, "semantic.map")
                require(overview["annotationCount"] == 0, "new isolated replay already has annotations")
                require(overview["coverage"]["cloudPointCount"] == manifest["pointCount"], "empty overview lost cloud size")
                state["emptyRevision"] = overview["semanticRevision"]
                return {"semanticRevision": overview["semanticRevision"], "measuredPointCount": manifest["pointCount"]}
            run_case("S01-empty", "Empty layer preserves measured-map identity and point count", empty)

            def write(items, expected=None):
                return evidence.call(registry, "semantic.annotate", {"mapRevision": manifest["hash"],
                    "expectedSemanticRevision": expected or state["revision"], "annotations": items})

            def submit():
                result = write(labels, state["emptyRevision"])
                require(result["annotationCount"] == 2 and len(result["relations"]) == 1, "missing measured region/fixture relation")
                require(result["coverage"]["fraction"] > 0, "selection did not cover measured points")
                state["revision"] = result["semanticRevision"]
                return {"semanticRevision": state["revision"], "coverage": result["coverage"],
                        "pointCounts": {item["id"]: item["pointCount"] for item in result["annotations"]}}
            run_case("S02-annotate", "Submit operator-labeled measured region and contained unclassified point fixture", submit)

            def query():
                by_name = evidence.call(registry, "semantic.query", {"name": labels[1]["name"]})
                by_region = evidence.call(registry, "semantic.query", {"regionId": labels[0]["id"]})
                require(by_name["total"] == by_region["total"] == 1, "name/region queries differ")
                require(by_name["results"][0]["requiresReobservation"] is True, "annotation became fresh physical authority")
                return {"matchedIds": [item["id"] for item in by_region["results"]]}
            run_case("S03-query", "Query measured labels by exact name and containing region", query)

            def plan():
                result = evidence.call(registry, "semantic.plan", {"mapRevision": manifest["hash"],
                    "semanticRevision": state["revision"], "annotationId": labels[0]["id"]})
                require(result["candidates"], "no bounded candidate found at replayed recorded pose")
                require(result["executionAuthorized"] is False and result["visibilityVerified"] is False,
                        "offline candidates incorrectly claimed execution or visibility")
                return {"replay": True, "liveLocalization": False, "candidateCount": len(result["candidates"]),
                        "physicalCommandsExecuted": 0, "candidates": result["candidates"]}
            run_case("S04-plan-replay", "Propose grid-certified observation positions using explicitly replayed recorded localization", plan)

            def restart():
                nonlocal workflow, registry
                workflow, registry = new_workflow()
                result = evidence.call(registry, "semantic.map")
                require(result["semanticRevision"] == state["revision"], "restart changed semantic revision")
                history = evidence.call(registry, "semantic.history")
                require(len(history["revisions"]) == 1, "restart lost append-only history")
                return {"semanticRevision": state["revision"], "historyLength": len(history["revisions"])}
            run_case("S05-restart", "Restart production workflow and read identical semantic revision/history", restart)

            def stale_write():
                evidence.call(registry, "semantic.annotate", {"mapRevision": manifest["hash"],
                    "expectedSemanticRevision": state["emptyRevision"], "annotations": []}, expect_code="SEMANTIC_REVISION_CONFLICT")
                result = evidence.call(registry, "semantic.map")
                require(result["semanticRevision"] == state["revision"], "rejected stale write changed state")
            run_case("S06-cas", "Reject stale writer without overwriting the committed revision", stale_write)

            run_case("S07-map-revision", "Reject annotations addressed to a different geometric map revision", lambda:
                evidence.call(registry, "semantic.annotate", {"mapRevision": "f" * 64,
                    "expectedSemanticRevision": state["revision"], "annotations": []}, expect_code="MAP_REVISION_CONFLICT"))

            def empty_selection():
                item = copy.deepcopy(labels[1])
                low = np.array(manifest["bounds"]["max"]) + 10.
                item["bounds"] = {"min": low.tolist(), "max": (low + 1.).tolist()}
                evidence.call(registry, "semantic.annotate", {"mapRevision": manifest["hash"],
                    "expectedSemanticRevision": state["revision"], "annotations": [item]},
                    expect_code="INVALID_SEMANTIC_ANNOTATION")
            run_case("S08-empty-selection", "Reject annotation boxes with no measured cloud points", empty_selection)

            def conflict():
                duplicate = {**copy.deepcopy(labels[0]), "id": "review-region-2", "name": "测量区域2", "aliases": []}
                result = write([*labels, duplicate])
                state["revision"] = result["semanticRevision"]
                require(result["conflictCount"] == 1, "same-kind overlap did not remain explicit")
                evidence.call(registry, "semantic.plan", {"mapRevision": manifest["hash"],
                    "semanticRevision": state["revision"], "annotationId": labels[0]["id"]}, expect_code="SEMANTIC_AMBIGUOUS")
                return {"conflicts": result["conflicts"]}
            run_case("S09-overlap", "Retain overlapping region assertions and reject ambiguous planning", conflict)

            def expiration():
                clock["nowUnixMs"] += 900_001
                normal = evidence.call(registry, "semantic.query", {"kind": "object"})
                stale = evidence.call(registry, "semantic.query", {"kind": "object", "includeStale": True})
                require(normal["total"] == 0 and stale["total"] == 1 and stale["results"][0]["stale"], "old object freshness policy failed")
                evidence.call(registry, "semantic.plan", {"mapRevision": manifest["hash"],
                    "semanticRevision": state["revision"], "annotationId": labels[1]["id"]}, expect_code="SEMANTIC_STALE")
                return {"controlledClockUnixMs": clock["nowUnixMs"], "objectAgeMs": stale["results"][0]["ageMs"]}
            run_case("S10-expiration", "Advance controlled clock; hide expired objects and reject physical proposals", expiration)

            def tamper():
                file = target / manifest["artifacts"]["cloud"]["href"]
                original = file.read_bytes()
                corrupted = original + b"semantic-replay-controlled-corruption"
                evidence.save("faults/tampered-cloud.bin", corrupted, raw=True)
                try:
                    file.write_bytes(corrupted)
                    result = evidence.call(registry, "semantic.query", {}, expect_code=None)
                finally:
                    file.write_bytes(original)
                require(all(item.ok for item in verify_artifacts(manifest, target)), "failed to restore experiment copy")
                return {"originalSha256": digest(original), "tamperedSha256": digest(corrupted),
                        "restoredCopy": True, "response": result}
            run_case("S11-integrity", "Reject corrupted copied point cloud, retain fault bytes, then restore the experiment copy", tamper)

            def source_unchanged():
                after = {name: digest((map_directory / name).read_bytes()) for name in source_snapshot}
                require(after == source_snapshot, "historical input evidence changed")
                require(not physical_attempts, "offline experiment attempted physical movement")
                return {"sourceFileCount": len(after), "sourceUnchanged": True, "physicalCommandAttempts": 0}
            run_case("S12-no-motion-source-integrity", "Verify immutable historical input and zero physical command attempts", source_unchanged)
        report.update(mapId=manifest["mapId"], mapRevision=manifest["hash"], cloudSha256=manifest["artifacts"]["cloud"]["sha256"],
                      cloudBytes=manifest["artifacts"]["cloud"]["bytes"], measuredPointCount=manifest["pointCount"],
                      maxPlanningProjectionBytes=max((item["bytes"] for item in evidence.projection_sizes), default=0),
                      planningProjectionMeasurements=evidence.projection_sizes,
                      semanticRevision=state.get("revision"), recordedPose=pose)
        report["planningProjectionToCloudByteRatio"] = report["maxPlanningProjectionBytes"] / report["cloudBytes"]
        report["passed"] = all(case["passed"] for case in report["cases"])
    except Exception as error:  # noqa: BLE001 - Retain setup failures rather than deleting the attempted run.
        report.update(errorType=type(error).__name__, error=str(error))
    finally:
        report.update(elapsedSeconds=time.perf_counter() - started, physicalCommandAttempts=len(physical_attempts),
                      caseCount=len(report["cases"]), passedCaseCount=sum(case["passed"] for case in report["cases"]))
        if physical_attempts:
            report["passed"] = False
        evidence.save("replayed-captures.json", captures)
        evidence.save("physical-command-attempts.json", physical_attempts)
        evidence.save("report.json", report)
        evidence.save("requests.json", evidence.requests)
        files = {str(path.relative_to(output)): {"bytes": path.stat().st_size, "sha256": digest(path.read_bytes())}
                 for path in sorted(output.rglob("*")) if path.is_file()}
        evidence.save("manifest.json", {"schemaVersion": "experiment.files.v1", "algorithm": "sha256", "files": files})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-origin", choices=["historical_gazebo_measurement", "synthetic_unit_fixture"],
                        default="historical_gazebo_measurement")
    args = parser.parse_args()
    report = evaluate(args.map_directory, args.output, evidence_origin=args.evidence_origin)
    print(json.dumps({key: report[key] for key in ("passed", "caseCount", "passedCaseCount", "elapsedSeconds", "outputDirectory")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
