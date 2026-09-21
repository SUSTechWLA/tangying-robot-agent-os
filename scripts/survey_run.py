#!/usr/bin/env python3
"""Drive a mapping survey against any ``robot.profile.v1`` runtime and record it.

This is the backend-agnostic runner. It speaks gRPC to whatever the agent would
speak to - a MuJoCo runtime, the Gazebo runtime, or a physical unit - and it never
asks which one it reached beyond recording the answer in the report. That is the
whole claim under test: ``adapter`` is evidence, not a branch.

Why not ``scripts/build_sim_map.py``: that one goes through the console HTTP API and
therefore needs a console, a session token and a robot address configured for one
backend. This one needs an address. During the Gazebo work the console was the part
most likely to be the thing that was broken, and a harness that shares a failure
mode with its subject cannot separate "the robot did not explore" from "the console
did not forward the request".

What the report contains is chosen so a comparison is possible at all: the runtime's
own identity, the survey's own coverage accounting, the leg-by-leg progress the
workflow published, and wall-clock. A number without the identity of the robot that
produced it is not a measurement.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "robot" / "gateway"))

import grpc
from google.protobuf.json_format import MessageToDict
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

TERMINAL = {"completed", "failed", "cancelled"}

#: The workflow reports its measurement counters flat on the status document rather
#: than under a nested key. Naming them here once means the harness cannot quietly
#: read a key that does not exist and report zero travel for a survey that drove -
#: which is exactly what the first version of this script did.
MEASUREMENT_FIELDS = ("frameCount", "pointCount", "travelledM",
                      "registrationCount", "loopClosures")


def measurement(status: dict) -> dict:
    return {field: status[field] for field in MEASUREMENT_FIELDS if field in status}


def call(stub, name, parameters=None, *, robot_id, timeout=120.0):
    request = robot_pb2.ServiceRequest(
        robot_id=robot_id, name=name, request_id=uuid.uuid4().hex)
    if parameters:
        request.parameters.update(parameters)
    response = stub.CallService(request, timeout=timeout)
    if not response.ok:
        raise RuntimeError(f"{name}: {response.code}: {response.message}")
    return MessageToDict(response.result)


def survey(target: str, output: pathlib.Path, *, mode="explore", name="survey",
           max_travel_m=0.0, max_legs=0, base_map_id="", timeout=1800.0,
           entry="mapping.start", poll_s=2.0) -> dict:
    channel = grpc.insecure_channel(target)
    stub = robot_pb2_grpc.RobotRuntimeStub(channel)
    info = stub.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), timeout=20.0)
    robot_id = info.robot_id
    catalogue = stub.ListServices(robot_pb2.GetRuntimeInfoRequest(), timeout=20.0)
    available = {item.name for item in catalogue.services if item.available}
    if entry not in available:
        raise RuntimeError(
            f"{robot_id} does not serve {entry}; it offers {sorted(available)}")

    output.mkdir(parents=True, exist_ok=True)
    started = time.time()
    parameters: dict[str, object] = {"name": name}
    if entry == "mapping.start":
        parameters["mode"] = mode
        if base_map_id:
            parameters["baseMapId"] = base_map_id
    elif base_map_id:
        parameters["mapId"] = base_map_id
    if max_travel_m:
        parameters["maxTravelM"] = float(max_travel_m)
    if max_legs:
        parameters["maxLegs"] = int(max_legs)

    status = call(stub, entry, parameters, robot_id=robot_id)
    report: dict = {
        "schemaVersion": "survey.run.v1",
        "target": target,
        "robotId": robot_id,
        "adapter": info.adapter,
        "runtimeVersion": info.runtime_version,
        "catalogRevision": info.catalog_revision,
        "entry": entry,
        "parameters": parameters,
        "startedAtUnixMs": int(started * 1000),
    }
    if "session" in status:
        status = status["session"]
    session = status.get("sessionId", "")
    report["decision"] = report.get("decision")
    report["sessionId"] = session

    progress = output / "progress.jsonl"
    legs: list[dict] = []
    with progress.open("w") as log:
        while status.get("state") not in TERMINAL:
            if time.time() - started > timeout:
                report["timedOut"] = True
                call(stub, "mapping.cancel", {}, robot_id=robot_id, timeout=60.0)
                break
            row = {
                "atUnixMs": int(time.time() * 1000),
                "elapsedS": round(time.time() - started, 2),
                "state": status.get("state"),
                "message": status.get("message"),
                "measurement": measurement(status),
                "exploration": status.get("exploration"),
            }
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            exploration = status.get("exploration") or {}
            if exploration and (not legs or legs[-1].get("leg") != exploration.get("leg")
                                or legs[-1].get("stopReason") != exploration.get("stopReason")):
                legs.append(dict(exploration))
            time.sleep(poll_s)
            status = call(stub, "mapping.status", {}, robot_id=robot_id)

    report.update({
        "finishedAtUnixMs": int(time.time() * 1000),
        "elapsedS": round(time.time() - started, 2),
        "state": status.get("state"),
        "message": status.get("message"),
        "summary": measurement(status),
        "status": {key: value for key, value in status.items()
                   if key not in {"preview", "exploration"}},
        "exploration": status.get("exploration"),
        "legs": legs,
        "mapId": status.get("mapId"),
        "mapRevision": status.get("mapRevision"),
    })
    (output / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", default="127.0.0.1:50051",
                        help="robot runtime gRPC address")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--entry", choices=("mapping.start", "mapping.ensure"),
                        default="mapping.start",
                        help="mapping.ensure is the natural-language intent entry")
    parser.add_argument("--mode", choices=("survey", "explore"), default="explore")
    parser.add_argument("--name", default="house survey")
    parser.add_argument("--max-travel-m", type=float, default=0.0)
    parser.add_argument("--max-legs", type=int, default=0)
    parser.add_argument("--base-map-id", default="")
    parser.add_argument("--timeout", type=float, default=1800.0)
    args = parser.parse_args()
    report = survey(args.target, args.output, mode=args.mode, name=args.name,
                    max_travel_m=args.max_travel_m, max_legs=args.max_legs,
                    base_map_id=args.base_map_id, timeout=args.timeout,
                    entry=args.entry)
    print(json.dumps({key: report.get(key) for key in
                      ("adapter", "state", "elapsedS", "mapId", "legs")},
                     ensure_ascii=False, indent=2)[:4000])
    return 0 if report.get("state") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
