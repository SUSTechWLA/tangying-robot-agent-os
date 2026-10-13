"""Actual ROS2 DDS + gRPC fixture experiment, deliberately without hardware."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import grpc
from google.protobuf.json_format import ParseDict
from tangying_robot_gateway.journal import RuntimeJournal
from tangying_robot_gateway.service import RobotRuntimeService
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc
from tangying_ros_gateway.profiled_node import ProfiledRosConfig, Ros2Transport

ROOT = Path(__file__).resolve().parents[2]


def run(kind, output, *, stop_check=False):
    output.mkdir(parents=True, exist_ok=True)
    profile_path = ROOT / "examples/robots" / f"{kind}.commissioning.profile.json"
    profile = json.loads(profile_path.read_text())
    namespace, commissioning_id = f"/recorded_{kind}_{'stop' if stop_check else 'loss'}", "fixture-commission-v1"
    log = (output / f"{kind}-controller.log").open("w")
    controller = subprocess.Popen([sys.executable, str(ROOT / "tests/contract/ros2_profiled_controller_fixture.py"),
        "--profile", str(profile_path), "--namespace", namespace, "--commissioning-id", commissioning_id,
        "--evidence-burst", "8",
        "--journal", str(output / f"{kind}-controller.jsonl")], stdout=log, stderr=subprocess.STDOUT)
    transport = None
    server = None
    workers = ThreadPoolExecutor(max_workers=4)
    records = []
    try:
        transport = Ros2Transport(profile, ProfiledRosConfig(profile_path=str(profile_path),
                                  commissioning_id=commissioning_id, namespace=namespace))
        deadline = time.monotonic() + 15
        while not transport.bridge.ready():
            if controller.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"{kind} controller did not become ready: {transport.bridge.state()}")
            time.sleep(.05)
        backend = transport.bridge.backend()
        service = RobotRuntimeService(backend, RuntimeJournal(output / f"{kind}-runtime.json"))
        server = grpc.server(workers)
        robot_pb2_grpc.add_RobotRuntimeServicer_to_server(service, server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            client = robot_pb2_grpc.RobotRuntimeStub(channel)
            info = client.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), timeout=2)
            last_command = None
            height = 1. if kind == "uav" else 0.
            for index, goal in enumerate([[1., 0., height, 1., 0., 0., 0.], [1., 1., height, 1., 0., 0., 0.], [0., 0., height, 1., 0., 0., 0.]]):
                for capability in ["navigation.navigate", "verify_arrival"]:
                    command = robot_pb2.SkillCommand(schema_version="robot.v1", command_id=f"{kind}-{index}-{capability}",
                        task_id=f"dds-long-task-{kind}", skill=capability, robot_id=info.robot_id,
                        catalog_revision=info.catalog_revision, deadline_unix_ms=int(time.time() * 1000) + 5000,
                        lease_ms=500, idempotency_key=f"{kind}-{index}-{capability}",
                        safety_profile="desktop_standard", approval_id="recorded-fixture-approval")
                    ParseDict({"goalPose": goal}, command.parameters)
                    events = list(client.ExecuteSkill(command, timeout=6))
                    terminal = events[-1]
                    records.append({"commandId": command.command_id, "type": terminal.type, "code": terminal.code,
                                    "observationId": terminal.observation_id})
                    if terminal.type != robot_pb2.SKILL_EVENT_SUCCEEDED:
                        (output / "failure-state.json").write_text(json.dumps(transport.bridge.state(), indent=2) + "\n")
                        raise RuntimeError(f"{kind}: {capability} failed: {terminal}")
                    last_command = command
            # Same known completed command replays in Runtime, never at controller.
            replay = list(client.ExecuteSkill(last_command, timeout=2))[-1]
            assert replay.type == robot_pb2.SKILL_EVENT_SUCCEEDED
            # Disappearance of the driver invalidates capability advertisement;
            # no last-known healthy value can keep admitting motion.
            if stop_check:
                stopped = client.EmergencyStop(robot_pb2.EStopRequest(reason="RECORDED_TEST_STOP"), timeout=2)
                assert stopped.latched
            else:
                controller.terminate()
                controller.wait(timeout=5)
                time.sleep(.6)
            stale_info = client.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), timeout=2)
            nav = next(tool for tool in stale_info.capabilities if tool.name == "navigation.navigate")
            assert not nav.available
            log.flush()
            ledger = [json.loads(line) for line in (output / f"{kind}-controller.jsonl").read_text().splitlines()]
            assert len([e for e in ledger if e["type"] == "RECORDED_MOVE"]) == 3
            assert len([e for e in ledger if e["type"] == "GOAL"]) == 6
            assert any(e["type"] == "LEASE" for e in ledger)
            if stop_check:
                stops = [e for e in ledger if e["type"] == "SAFE_STOP"]
                assert stops and stops[-1]["procedureId"] == profile["mobility"]["safeStop"]["procedureId"]
        return {"kind": kind, "success": True, "physicalFixtureMoves": 3, "independentArrivalChecks": 3,
                "evidenceBurstFramesPerCommand": 8,
                "driverLossDisablesMotion": not stop_check, "safeStopProcedureAcknowledged": stop_check,
                "replayNoDuplicate": True, "commands": records,
                "evidenceClass": "recorded_controller_actual_ros2_dds_grpc_no_hardware"}
    finally:
        if server is not None:
            server.stop(0).wait()
        workers.shutdown()
        if transport is not None:
            transport.close()
        if controller.poll() is None:
            controller.terminate()
            controller.wait(timeout=5)
        log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for kind in ["ugv", "uav", "legged"]:
        for stop_check in [False, True]:
            try:
                results.append(run(kind, args.output / f"{kind}-{'stop' if stop_check else 'loss'}", stop_check=stop_check))
            except Exception as exc:
                results.append({"kind": kind, "stopCheck": stop_check, "success": False, "error": str(exc)})
                (args.output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
                raise
            print(json.dumps(results[-1]), flush=True)
            (args.output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
