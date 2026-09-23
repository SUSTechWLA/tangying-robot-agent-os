#!/usr/bin/env python3
"""Drive one Gazebo RobotRuntime over gRPC, the way the agent does.

This is a development probe, not part of the product. It exists because the
migration's acceptance criterion is "the same agent drives either simulator",
and checking that by hand needs one tool that speaks the exact wire contract the
agent speaks - same schema version, same catalog revision, same safety profile -
rather than a shortcut that only works from inside the container.

    python scripts/gazebo_probe.py info
    python scripts/gazebo_probe.py observe
    python scripts/gazebo_probe.py skill observe_scene
    python scripts/gazebo_probe.py skill navigation.navigate --param goalPose=-4.7,-0.8,0,0,0,0,1
    python scripts/gazebo_probe.py skill manipulation.pick --param targetRef=ceramic-mug
    python scripts/gazebo_probe.py services
    python scripts/gazebo_probe.py call navigation.map
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

import grpc
from google.protobuf.struct_pb2 import Struct
from tangying_robot_proto.robot.v1 import robot_pb2 as pb
from tangying_robot_proto.robot.v1 import robot_pb2_grpc as pbg

#: The wire schema every runtime in this repository accepts. The MuJoCo runtime
#: and the gateway's safety supervisor both refuse anything else, and a probe
#: that guessed a different version would look like a runtime defect.
SCHEMA_VERSION = "robot.v1"


def _value(field):
    """Unwrap one google.protobuf.Value into a plain Python value."""
    kind = field.WhichOneof("kind")
    if kind == "number_value":
        return field.number_value
    if kind == "string_value":
        return field.string_value
    if kind == "bool_value":
        return field.bool_value
    if kind == "null_value":
        return None
    if kind == "struct_value":
        return {k: _value(v) for k, v in field.struct_value.fields.items()}
    if kind == "list_value":
        return [_value(v) for v in field.list_value.values]
    return None


def unwrap(struct_like) -> dict:
    return {k: _value(v) for k, v in struct_like.fields.items()}


def connect(address: str):
    channel = grpc.insecure_channel(address)
    stub = pbg.RobotRuntimeStub(channel)
    info = stub.GetRuntimeInfo(pb.GetRuntimeInfoRequest(), timeout=20)
    return stub, info


def parse_params(pairs):
    params = Struct()
    for pair in pairs or ():
        if "=" not in pair:
            raise SystemExit(f"--param needs key=value, got {pair!r}")
        key, raw = pair.split("=", 1)
        if "," in raw:
            numbers = []
            for part in raw.split(","):
                try:
                    numbers.append(float(part))
                except ValueError:
                    numbers = None
                    break
            params[key] = numbers if numbers is not None else raw
        else:
            try:
                params[key] = float(raw)
            except ValueError:
                params[key] = raw
    return params


def cmd_info(args):
    _, info = connect(args.runtime)
    print(json.dumps({
        "robot_id": info.robot_id,
        "adapter": info.adapter,
        "adapter_version": info.adapter_version,
        "software_version": info.software_version,
        "protocol_version": info.protocol_version,
        "manipulation_ready": info.manipulation_ready,
        "blockers": list(info.blockers),
        "catalog_revision": info.catalog_revision,
        "capabilities": [
            {"name": c.name, "available": c.available,
             "mutates_world": c.mutates_world, "safety_level": c.safety_level}
            for c in info.capabilities
        ],
    }, ensure_ascii=False, indent=2))


def cmd_observe(args):
    stub, info = connect(args.runtime)
    source = args.source or info.robot_id + "/base-rgbd"
    request = pb.ObserveRequest(source_id=source, streams=["robot_state", "reconstruction"])
    for observation in stub.Observe(request, timeout=40):
        payload = {
            "observation_id": observation.observation_id,
            "wall_time_unix_ms": observation.wall_time_unix_ms,
            "robot_state": unwrap(observation.robot_state),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    print("no observation arrived")


def cmd_skill(args):
    stub, info = connect(args.runtime)
    stamp = int(time.time() * 1000)
    command = pb.SkillCommand(
        schema_version=SCHEMA_VERSION,
        command_id=f"probe-{stamp}",
        task_id="probe",
        robot_id=info.robot_id,
        skill=args.name,
        parameters=parse_params(args.param),
        deadline_unix_ms=stamp + args.timeout_ms,
        lease_ms=args.timeout_ms,
        idempotency_key=f"probe/{args.name}/{stamp}",
        safety_profile=args.safety_profile,
        approval_id="probe",
        catalog_revision=info.catalog_revision,
    )
    events = []
    try:
        for event in stub.ExecuteSkill(command, timeout=args.timeout_ms / 1000 + 5):
            events.append(event)
            print(f"  {pb.SkillEventType.Name(event.type):24s} "
                  f"code={event.code!r} msg={event.message!r}")
    except grpc.RpcError as error:
        print(f"RPC {error.code().name}: {error.details()}")
        return 2
    if not events:
        print("no events")
        return 2
    final = events[-1]
    ok = final.type == pb.SKILL_EVENT_SUCCEEDED
    print(f"=> {'OK' if ok else 'FAILED'} ({pb.SkillEventType.Name(final.type)})")
    return 0 if ok else 1


def cmd_services(args):
    stub, _ = connect(args.runtime)
    try:
        # The proto declares `rpc ListServices(GetRuntimeInfoRequest)`. There is
        # no ListServicesRequest message; the declared request type has no fields
        # this call needs, and every implementation ignores them.
        catalog = stub.ListServices(pb.GetRuntimeInfoRequest(), timeout=25)
    except grpc.RpcError as error:
        print(f"RPC {error.code().name}: {error.details()}")
        return 2
    for service in catalog.services:
        print(f"  {service.name:28s} mutates={service.mutates_world} available={service.available}")
    return 0


def cmd_call(args):
    stub, info = connect(args.runtime)
    # `CallService` takes a ServiceRequest, which identifies the robot and the
    # call as well as naming the service: a runtime hosting catalogues for more
    # than one robot has to be told which one is being asked.
    request = pb.ServiceRequest(robot_id=info.robot_id, name=args.name,
                                request_id=uuid.uuid4().hex,
                                parameters=parse_params(args.param))
    try:
        response = stub.CallService(request, timeout=60)
    except grpc.RpcError as error:
        print(f"RPC {error.code().name}: {error.details()}")
        return 2
    print(f"ok={response.ok} code={response.code!r} message={response.message!r}")
    if response.result:
        print(json.dumps(unwrap(response.result), ensure_ascii=False, indent=2, default=str)[:2000])
    return 0 if response.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runtime", default="127.0.0.1:50161",
                        help="RobotRuntime gRPC address (Gazebo default 50161)")
    parser.add_argument("--safety-profile", default="desktop_standard")
    parser.add_argument("--timeout-ms", type=int, default=45000)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="GetRuntimeInfo as the agent reads it").set_defaults(func=cmd_info)

    observe = sub.add_parser("observe", help="one Observe frame")
    observe.add_argument("--source")
    observe.set_defaults(func=cmd_observe)

    skill = sub.add_parser("skill", help="ExecuteSkill and print every event")
    skill.add_argument("name")
    skill.add_argument("--param", action="append")
    skill.set_defaults(func=cmd_skill)

    sub.add_parser("services", help="ListServices").set_defaults(func=cmd_services)

    call = sub.add_parser("call", help="CallService")
    call.add_argument("name")
    call.add_argument("--param", action="append")
    call.set_defaults(func=cmd_call)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
