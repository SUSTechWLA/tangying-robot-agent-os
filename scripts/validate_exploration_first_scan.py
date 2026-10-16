#!/usr/bin/env python3
"""Record one supervised Gazebo map-cold exploration, including failed attempts.

This is a Runtime acceptance test, not a natural-language/Host test. It starts
motion using mapping.build(mode=explore), renews the provider operation lease,
and never equates a saved map with complete environmental coverage. Use a new,
isolated Gazebo namespace and preserve its database/launch provenance separately.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "robot/gateway"))

import grpc
from google.protobuf.json_format import MessageToDict
from tangying_robot_gateway.map_manifest import load_manifest, verify_artifacts
from tangying_robot_proto.robot.v1 import robot_pb2 as pb
from tangying_robot_proto.robot.v1 import robot_pb2_grpc as rpc

TERMINAL = {"completed", "failed", "cancelled"}
ACTIVE = {"moving", "recording", "exploring", "surveying", "finalizing"}
SOURCE_AREAS = ("robot/gateway/tangying_robot_gateway", "python/tangying_robot_proto",
                "robot/ros2_ws/src/tangying_navigation", "deploy/robot/navigation")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def now():
    return time.time_ns() // 1_000_000


def compact(value):
    """Remove large preview only; retain all trajectory and exploration data."""
    if isinstance(value, dict):
        return {key: compact(item) for key, item in value.items() if key != "preview"}
    if isinstance(value, list):
        return [compact(item) for item in value]
    return value


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def git(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True)
    return result.stdout


def source_snapshot(source_root):
    found = {}
    for area in SOURCE_AREAS:
        for path in sorted((source_root / area).rglob("*")):
            if (path.is_file() and not path.is_symlink()
                    and not any(part.startswith(".") or part == "__pycache__"
                                for part in path.relative_to(source_root).parts)):
                found[str(path.relative_to(source_root))] = sha(path)
    found["acceptanceHarness"] = sha(Path(__file__))
    if len(found) < 10:
        raise ValueError("source root must contain the complete maintained gateway/proto sources")
    return found


def archive_map(map_root, output, map_id):
    if not isinstance(map_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", map_id):
        raise ValueError("invalid published map ID")
    directory = (map_root / map_id).resolve()
    if not directory.is_relative_to(map_root.resolve()):
        raise ValueError("published map directory escapes map root")
    manifest = load_manifest(directory)
    checks = verify_artifacts(manifest, directory)
    write_json(output / "map-artifact-checks.json", [dataclasses.asdict(item) for item in checks])
    if not checks or not all(item.ok for item in checks):
        raise ValueError("published map artifacts failed independent hash/size verification")
    target = output / "map-package"
    target.mkdir()
    shutil.copyfile(directory / "manifest.json", target / "manifest.json")
    for entry in manifest["artifacts"].values():
        original = (directory / entry["href"]).resolve()
        if not original.is_relative_to(directory):
            raise ValueError("map artifact escapes package")
        destination = target / entry["href"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
    copied_checks = verify_artifacts(load_manifest(target), target)
    if not all(item.ok for item in copied_checks):
        raise ValueError("archived map changed during copy")
    return manifest


def evaluate(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # This first file exists before source inspection or the first RPC.
    write_json(output / "manifest.json", {
        "schemaVersion": "research.exploration-evidence.v1", "state": "collecting",
        "startedAtUnixMs": now(), "files": {}, "manifestExcludesItself": True,
    })
    started = time.monotonic()
    report = {"schemaVersion": "exploration.first-scan.acceptance.v1", "passed": False,
              "scope": "gazebo_runtime_mapping_build_explore", "physicalCertification": False,
              "naturalLanguageHostTest": False, "wholeEnvironmentCoverage": False,
              "coverageGroundTruth": "not_measured", "startedAtUnixMs": now(),
              "runtime": args.runtime, "parameters": {"mode": "explore", "name": args.name,
                                                       "maxTravelM": args.max_travel_m,
                                                       "maxLegs": args.max_legs}}
    sources = None
    status = None
    operation_id = None
    owner = "firstscan-" + uuid.uuid4().hex
    issued = False
    stub = None
    robot_id = None
    calls = (output / "calls.jsonl").open("x")
    progress = (output / "progress.jsonl").open("x")

    def record(stream, value):
        stream.write(json.dumps(compact({"atUnixMs": now(),
                    "elapsedS": round(time.monotonic() - started, 6), **value}),
                    ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())

    def invoke(name, parameters=None, *, renew=False, request_id=None):
        request = pb.ServiceRequest(robot_id=robot_id, name=name,
                                    request_id=request_id or uuid.uuid4().hex)
        request.parameters.update(parameters or {})
        if renew:
            request.operation_lease_ms = 30_000
            if operation_id:
                request.operation_id = operation_id
                request.operation_owner_id = owner
        record(calls, {"event": "request", "request": MessageToDict(request)})
        try:
            response = stub.CallService(request, timeout=10)
        except grpc.RpcError as error:
            record(calls, {"event": "rpc_error", "service": name,
                           "code": error.code().name, "details": error.details()})
            raise
        record(calls, {"event": "response", "service": name,
                       "response": MessageToDict(response)})
        if not response.ok:
            raise RuntimeError(f"{name}: {response.code}: {response.message}")
        return MessageToDict(response.result)

    try:
        sources = source_snapshot(args.source_root.resolve())
        write_json(output / "source-before.json", sources)
        patch = git("diff", "--binary", "HEAD", "--", *SOURCE_AREAS,
                    "scripts/validate_exploration_first_scan.py")
        (output / "source-dirty.patch").write_bytes(patch)
        preregistration = {**report, "gitHead": git("rev-parse", "HEAD").decode().strip(),
                          "gitStatus": git("status", "--porcelain=v1").decode(),
                          "dirtyPatchSha256": sha(output / "source-dirty.patch"),
                          "sourceRoot": str(args.source_root.resolve()),
                          "sourceSetSha256": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
                          "mapRoot": str(args.map_root.resolve()),
                          "operationLeaseMs": 30_000, "timeoutS": args.timeout,
                          "pythonVersion": sys.version, "externalEnvironmentProvenance": None}
        if args.environment_json:
            environment = json.loads(args.environment_json.read_text())
            # Only an explicitly prepared, sanitized metadata file belongs here;
            # never pass docker-inspect output or a private .env file.
            if not isinstance(environment, dict):
                raise ValueError("environment JSON must be a sanitized metadata object")
            for key in environment:
                if any(word in key.lower() for word in ("token", "secret", "password", "api_key")):
                    raise ValueError("environment JSON must not contain credential fields")
            write_json(output / "environment.json", environment)
            preregistration["externalEnvironmentProvenance"] = sha(output / "environment.json")
        write_json(output / "preregistration.json", preregistration)
        channel = grpc.insecure_channel(args.runtime)
        stub = rpc.RobotRuntimeStub(channel)
        info = stub.GetRuntimeInfo(pb.GetRuntimeInfoRequest(), timeout=10)
        robot_id = info.robot_id
        write_json(output / "runtime-info.json", MessageToDict(info))
        if info.adapter != "gazebo":
            raise ValueError("this acceptance harness requires a Gazebo runtime")
        catalog = stub.ListServices(pb.GetRuntimeInfoRequest(), timeout=10)
        write_json(output / "service-catalog.json", MessageToDict(catalog))
        available = {item.name for item in catalog.services if item.available}
        required = {"mapping.build", "mapping.status", "mapping.cancel", "mapping.inventory", "navigation.status"}
        if not required <= available:
            raise ValueError(f"missing registered services: {sorted(required - available)}")
        initial = {"mapping": invoke("mapping.status"), "inventory": invoke("mapping.inventory"),
                   "navigation": invoke("navigation.status")}
        write_json(output / "initial-state.json", compact(initial))
        if initial["mapping"].get("state") in ACTIVE:
            raise ValueError("runtime has an active mapping operation")
        if (initial["inventory"].get("count", 0) != 0 or initial["inventory"].get("activeMapId")
                or initial["mapping"].get("activeMap")):
            raise ValueError("first scan requires an empty registered-map inventory and no active map")
        if initial["navigation"].get("mode") != "mapping":
            raise ValueError("first scan requires a mapping-mode navigation sidecar")
        report["registeredMapCold"] = True
        report["robotId"] = robot_id
        issued = True
        status = invoke("mapping.build", report["parameters"], renew=True, request_id=owner)
        operation_id = status.get("operationId")
        if not operation_id:
            raise ValueError("mapping.build omitted operationId; cannot supervise safely")
        report["operationId"] = operation_id
        deadline = time.monotonic() + args.timeout
        while True:
            record(progress, {"status": status})
            if status.get("operationId") != operation_id:
                raise ValueError("mapping operation identity changed during scan")
            if status.get("state") in TERMINAL:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("exploration operation deadline exceeded")
            time.sleep(args.poll_s)
            status = invoke("mapping.status", renew=True)
        write_json(output / "final-state.json", compact(status))
        if status.get("state") != "completed":
            raise RuntimeError(f"mapping ended in {status.get('state')}: {status.get('message')}")
        travel = status.get("operationTravelledM")
        if not isinstance(travel, (int, float)) or not math.isfinite(travel):
            raise ValueError("operation-level measured travel is missing")
        if not 0 <= travel <= args.max_travel_m + 1e-6:
            raise ValueError("exploration exceeded the requested operation travel budget")
        exploration = status.get("exploration") or {}
        if not exploration.get("stopReason"):
            raise ValueError("exploration omitted its termination reason")
        if args.max_legs == 1 and (exploration.get("initialScanAttempted") is not True
                                   or exploration.get("initialScanStatus") != "returned"):
            raise ValueError("first-leg initial scan port did not return with explicit evidence")
        if not 1 <= exploration.get("leg", 0) <= args.max_legs:
            raise ValueError("exploration leg count is missing or exceeds budget")
        active = status.get("activeMap") or {}
        if not active.get("mapRevision") or active.get("mapId") != status.get("mapId"):
            raise ValueError("completed map was not activated at a named matching revision")
        manifest = archive_map(args.map_root, output, active["mapId"])
        if manifest["robotId"] != robot_id or manifest.get("calibrationRevision") != active.get("calibrationRevision"):
            raise ValueError("published map robot/calibration identity differs from active result")
        navigation = invoke("navigation.status")
        write_json(output / "navigation-final.json", compact(navigation))
        if (navigation.get("mapId") != active["mapId"]
                or navigation.get("mapRevision") != active["mapRevision"]):
            raise ValueError("navigation did not load the independently archived map revision")
        if navigation.get("ready") is not True or navigation.get("localizationState") != "localized":
            raise ValueError("published navigation map is not ready with confirmed localization")
        report.update(passed=True, state=status["state"], operationTravelledM=travel,
                      stopReason=exploration["stopReason"], exploration=exploration,
                      mapId=active["mapId"], mapRevision=active["mapRevision"],
                      mapArtifactVerification=True)
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 - every failed attempt must retain evidence and stop.
        report["error"] = f"{type(error).__name__}: {error}"
        if issued and stub is not None:
            try:
                latest = invoke("mapping.status")
                status = latest
                if operation_id and latest.get("operationId") == operation_id:
                    if latest.get("state") not in TERMINAL:
                        invoke("mapping.cancel")
                    stop_deadline = time.monotonic() + 45
                    while True:
                        latest = invoke("mapping.status")
                        record(progress, {"event": "shutdown_status", "status": latest})
                        if latest.get("operationId") != operation_id:
                            raise ValueError("operation changed while confirming stop")
                        if latest.get("state") in TERMINAL:
                            report["stopConfirmed"] = True
                            break
                        if time.monotonic() >= stop_deadline:
                            raise TimeoutError("mapping cancellation did not reach a terminal state")
                        time.sleep(args.poll_s)
                    status = latest
                elif not operation_id:
                    report["unknownStartOutcome"] = True
                    report["cleanupNote"] = "start outcome unknown; no blind cancel or renewal; wait for provider's 30 s lease"
                    observed_operation = latest.get("operationId")
                    stop_deadline = time.monotonic() + 45
                    while latest.get("state") in ACTIVE:
                        if time.monotonic() >= stop_deadline:
                            raise TimeoutError("unknown start did not reach a stopped state after lease expiry")
                        time.sleep(args.poll_s)
                        latest = invoke("mapping.status")
                        record(progress, {"event": "lease_expiry_wait", "status": latest})
                        if latest.get("operationId") != observed_operation:
                            raise ValueError("mapping identity changed during unknown-start lease wait")
                    status = latest
                    report["stopConfirmed"] = latest.get("state") in TERMINAL | {"idle"}
                    if not report["stopConfirmed"]:
                        report["stopUnconfirmed"] = True
                else:
                    raise ValueError("refusing to cancel another mapping operation")
            except Exception as cleanup:  # noqa: BLE001 - preserve the original fault and failed stop confirmation.
                report["stopUnconfirmed"] = True
                report["cleanupError"] = f"{type(cleanup).__name__}: {cleanup}"
        if status:
            write_json(output / "final-state.json", compact(status))
            report["state"] = status.get("state")
    finally:
        calls.close()
        progress.close()
        if status and not (output / "map-package").exists():
            partial = status.get("activeMap") or {}
            if partial.get("mapId"):
                try:
                    archive_map(args.map_root, output, partial["mapId"])
                    report["partialMapArtifactVerification"] = True
                except Exception as archive_error:  # noqa: BLE001 - artifact errors must not erase the attempt.
                    report["partialMapArchiveError"] = str(archive_error)
        try:
            after = source_snapshot(args.source_root.resolve())
            write_json(output / "source-after.json", after)
            report["sourceChangedDuringRun"] = sources is None or sources != after
            if report["sourceChangedDuringRun"]:
                report["passed"] = False
        except Exception as source_error:  # noqa: BLE001 - source audit failure invalidates, but still archives the run.
            report["passed"] = False
            report["sourceVerificationError"] = str(source_error)
        report.update(finishedAtUnixMs=now(), elapsedS=round(time.monotonic() - started, 6))
        write_json(output / "report.json", compact(report))
        files = {str(path.relative_to(output)): {"sha256": sha(path), "bytes": path.stat().st_size}
                 for path in sorted(output.rglob("*")) if path.is_file() and path != output / "manifest.json"}
        write_json(output / "manifest.json", {
            "schemaVersion": "research.exploration-evidence.v1", "state": "finalized",
            "files": files, "manifestExcludesItself": True, "passed": report["passed"],
        })
    print(json.dumps(compact(report), ensure_ascii=False, allow_nan=False), flush=True)
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, help="isolated Gazebo gRPC address")
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory; must not exist")
    parser.add_argument("--map-root", type=Path, required=True, help="host mount of this runtime's workflow maps")
    parser.add_argument("--source-root", type=Path, default=ROOT,
                        help="full frozen repository source snapshot actually mounted into Gazebo")
    parser.add_argument("--environment-json", type=Path,
                        help="explicit sanitized launch/image/database provenance; never a .env file")
    parser.add_argument("--name", default="first-scene-exploration")
    parser.add_argument("--max-travel-m", type=float, default=8.0)
    parser.add_argument("--max-legs", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--poll-s", type=float, default=2.0)
    args = parser.parse_args()
    if not all(math.isfinite(value) for value in (args.max_travel_m, args.timeout, args.poll_s)):
        parser.error("budgets and polling interval must be finite")
    if not (2 <= args.max_travel_m <= 120 and 1 <= args.max_legs <= 6
            and 1 <= args.timeout <= 7200 and 0.1 <= args.poll_s <= 5):
        parser.error("travel 2..120 m, legs 1..6, timeout 1..7200 s, poll 0.1..5 s required")
    if not args.map_root.is_dir():
        parser.error("map-root must be an existing host workflow directory")
    return evaluate(args)


if __name__ == "__main__":
    raise SystemExit(main())
