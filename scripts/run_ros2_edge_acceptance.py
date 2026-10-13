"""Single natural-language long route over LocalAgent -> gRPC -> ROS2 fixture.

Requires built ROS2 messages/gateway and a Linux LocalAgent binary in the test
container. Runs only isolated recorded controllers; no hardware is contacted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib import error, request

import grpc
from console_session import headers as session_headers
from console_session import install_loopback_opener
from tangying_robot_gateway.ros2_profile import profile_revision
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERABLE_FAILURE", "SAFETY_STOPPED"}
NATURAL_REQUEST = "从客厅出发，去厨房确认一下环境，然后去卧室，再回到厨房，再去卧室，再回到厨房，再回到客厅，再去厨房，再去卧室，最后回到客厅。"


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    logs, processes = [], []
    profile_path = ROOT / "examples/robots/ugv.commissioning.profile.json"
    profile = json.loads(profile_path.read_text())
    namespace = "/recorded_nl_" + uuid.uuid4().hex[:12]
    grpc_port, http_port = port(), port()
    while grpc_port == http_port:
        http_port = port()
    base, token = f"http://127.0.0.1:{http_port}", ""
    install_loopback_opener()

    def save(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    source_paths = ["core/robotcontract/contract.go", "core/capability/owned_stop.go", "edge/agent/runner.go",
        "edge/runtime/runtime.go", "edge/robotclient/contract_pin.go", "edge/robotclient/client.go",
        "edge/robotclient/semantic_routes.go", "skills/manipulation/plugin.go", "cmd/local-agent/main.go",
        "robot/gateway/tangying_robot_gateway/ros2_profile.py", "robot/gateway/tangying_robot_gateway/contracts.py",
        "robot/gateway/tangying_robot_gateway/service.py", "robot/gateway/tangying_robot_gateway/plugin_backend.py",
        "robot/ros2_ws/src/tangying_robot_gateway/tangying_ros_gateway/profiled_node.py",
        "tests/contract/ros2_profiled_controller_fixture.py", "scripts/run_ros2_edge_acceptance.py"]
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_paths}
    binary_sha = hashlib.sha256(args.binary.read_bytes()).hexdigest()
    summary = {"schemaVersion": "edge.ros2-nl-experiment.v1", "success": False, "request": NATURAL_REQUEST,
        "startedAtUnixMs": int(time.time() * 1000), "binarySha256": binary_sha,
        "profileRevision": profile_revision(profile), "profileFileSha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
        "robotId": profile["robotId"], "adapter": profile["adapterId"], "provider": "deterministic", "modelCalls": 0,
        "rosNamespace": namespace,
        "evidenceClass": "recorded_controller_actual_localagent_grpc_ros2_no_hardware",
        "sourceSnapshotNote": "Source hashes sampled at experiment start; binary hash is tracked independently."}
    save("source-manifest.json", sources)
    save("summary.json", summary)
    started = time.monotonic()

    def launch(argv, name, env=None):
        log = (output / name).open("w")
        logs.append(log)
        process = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)
        processes.append(process)
        return process

    def api(path, method="GET", body=None):
        req = request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                              headers=session_headers(token, {"Content-Type": "application/json"}))
        try:
            with request.urlopen(req, timeout=15) as response:
                return json.load(response)
        except error.HTTPError as exc:
            raise RuntimeError(f"{method} {path}: {exc.code}: {exc.read().decode()}") from exc

    def wait(predicate, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            for process in processes:
                if process.poll() is not None:
                    raise RuntimeError(f"owned subprocess exited with {process.returncode}")
            time.sleep(.05)
        raise TimeoutError("ROS2 LocalAgent acceptance timed out")

    try:
        launch([sys.executable, str(ROOT / "tests/contract/ros2_profiled_controller_fixture.py"),
                "--profile", str(profile_path), "--namespace", namespace, "--journal", str(output / "controller.jsonl")],
               "controller.log")
        config_path = output / "ros2-config.json"
        save("ros2-config.json", {"profilePath": str(profile_path), "commissioningId": "fixture-commission-v1",
                                 "namespace": namespace})
        env = dict(os.environ, TANGYING_ROS_PROFILE_CONFIG=str(config_path))
        launch([sys.executable, "-m", "tangying_robot_gateway.run_plugin", "serve", "--factory",
                "tangying_ros_gateway.profiled_node:create_backend", "--journal", str(output / "runtime.json"),
                "--listen", f"127.0.0.1:{grpc_port}", "--allow-insecure"], "runtime.log", env)
        with grpc.insecure_channel(f"127.0.0.1:{grpc_port}") as channel:
            client = robot_pb2_grpc.RobotRuntimeStub(channel)
            def runtime_ready():
                try:
                    info = client.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), timeout=1)
                    return next(t.available for t in info.capabilities if t.name == "navigation.navigate")
                except grpc.RpcError:
                    return False
            wait(runtime_ready, 20)
        model_config = output / "deterministic.env"
        model_config.write_text("AGENT_PROVIDER=deterministic\n" + "".join(
            f"AGENT_{stage}_PROVIDER=deterministic\n" for stage in ("GOAL", "INTENT", "PLANNING", "RECOVERY")))
        arguments = [str(args.binary.resolve()), "--config", str(model_config), "--listen", f"127.0.0.1:{http_port}",
                     "--robot", f"127.0.0.1:{grpc_port}", "--robot-id", profile["robotId"], "--dev-insecure",
                     "--robot-safety-profile", "desktop_standard", "--data-dir", str(output / "local-agent")]
        admission = subprocess.run([*arguments, "--admit-runtime-contract"], cwd=ROOT,
                                   capture_output=True, text=True, timeout=30, check=False)
        (output / "contract-admission.log").write_text(admission.stdout + admission.stderr)
        if admission.returncode != 0:
            raise RuntimeError("test Runtime admission failed; see contract-admission.log")
        launch(arguments, "local-agent.log", dict(os.environ, TANGYING_INCIDENT_DIR=str(output / "incidents")))
        def agent_ready():
            try:
                return api("/v1/telemetry?adapter=" + profile["adapterId"] + "&limit=1").get("hasLatest")
            except (OSError, RuntimeError):
                return False
        wait(agent_ready, 20)
        token = (output / "local-agent/console-session").read_text().strip()
        initial = api("/v1/telemetry?adapter=" + profile["adapterId"] + "&limit=1")
        save("initial-observation.json", initial)
        summary["map"] = initial["latest"]["robotState"]["active_map"]
        start = time.monotonic()
        task = api("/v1/tasks", "POST", {"adapter": profile["adapterId"], "request": NATURAL_REQUEST})
        summary["taskId"] = task["id"]
        planned_rooms = task["plan"]["capabilities"]["calls"][0]["legacyIntent"]["routeRooms"]
        summary["plannedNamedVisits"] = len(planned_rooms)
        summary["plannedRooms"] = planned_rooms
        save("created-task.json", task)
        task_path = f"/v1/tasks/{task['id']}"
        approval = api(task_path + "/approve", "POST")
        save("approved-task.json", approval)
        def terminal():
            current = api(task_path)
            return current if current["state"] in TERMINAL else None
        final = wait(terminal, 90)
        save("final-task.json", final)
        summary["state"] = final["state"]
        save("observations.json", api(task_path + "/observations"))
        save("final-telemetry.json", api("/v1/telemetry?adapter=" + profile["adapterId"] + "&limit=1"))
        if final["state"] != "SUCCEEDED":
            raise AssertionError(f"natural-language task {task['id']} ended {final['state']}; see final-task.json")
        ledger = [json.loads(line) for line in (output / "controller.jsonl").read_text().splitlines()]
        moves = [e for e in ledger if e["type"] == "RECORDED_MOVE"]
        goals = [e for e in ledger if e["type"] == "GOAL"]
        verifies = [e for e in goals if e["capability"] == "verify_arrival"]
        assert len(moves) >= 8 and len(moves) == len(verifies), (len(moves), len(verifies))
        commissioned_goals = initial["latest"]["robotState"]["semantic_navigation"]["goals"]
        assert [move["goalPose"] for move in moves] == [commissioned_goals[room] for room in planned_rooms]
        assert len({e["commandId"] for e in moves}) == len(moves)
        assert not any(e.get("capability") == "navigation.pre_position" for e in goals)
        confirmed = [e["payload"] for e in final["events"] if e["type"] == "TOOL_ACTIVITY"
                     and e["payload"].get("activityStatus") == "CONFIRMED"]
        arrivals = [e for e in confirmed if e.get("toolName") == "verify_arrival"]
        assert len(arrivals) == len(verifies), (len(arrivals), len(verifies))
        assert all(e.get("receiptObservationId") and e.get("evidenceSource") == "command_observation" for e in arrivals), arrivals
        summary.update({"success": True, "elapsedTaskSeconds": time.monotonic() - start, "navigationCommands": len(moves),
            "independentArrivalChecks": len(verifies), "prePositionCommands": 0, "duplicateMotionCommands": 0,
            "plannedNamedVisits": len(planned_rooms), "visitedRooms": planned_rooms})
        save("summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except Exception as exc:
        summary["error"] = str(exc)
        raise
    finally:
        ledger_path = output / "controller.jsonl"
        if ledger_path.exists():
            ledger = [json.loads(line) for line in ledger_path.read_text().splitlines()]
            actual_moves = [entry for entry in ledger if entry["type"] == "RECORDED_MOVE"]
            summary["actualRecordedMoves"] = len(actual_moves)
            summary["actualArrivalCheckGoals"] = len([entry for entry in ledger
                if entry["type"] == "GOAL" and entry["capability"] == "verify_arrival"])
            summary["actualMoveCommandIds"] = [entry["commandId"] for entry in actual_moves]
        summary["elapsedSeconds"] = time.monotonic() - started
        summary["endedAtUnixMs"] = int(time.time() * 1000)
        summary["sourceChangedDuringExperiment"] = [name for name, sha in sources.items()
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha]
        save("summary.json", summary)
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
