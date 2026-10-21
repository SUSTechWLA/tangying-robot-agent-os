"""Measured Gazebo -> ROS2 -> Runtime -> LocalAgent natural-language campaign.

Run inside the isolated simulation container with the field hub already running.
Every process, profile, command, task event and terminal result is retained.
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
import urllib.error
import urllib.request
from pathlib import Path

import grpc
from console_session import headers as session_headers
from console_session import install_loopback_opener
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

ROOT = Path(__file__).resolve().parents[1]
ROBOTS = {"ground-a": "ugv", "ground-b": "legged", "uav-a": "uav"}
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERABLE_FAILURE", "SAFETY_STOPPED"}
REQUEST = "从客厅出发，去厨房确认一下环境，然后去卧室，再回到厨房，再去卧室，再回到厨房，再回到客厅，再去厨房，再去卧室，最后回到客厅。"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http(base, path, body=None, token=""):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
        headers=session_headers(token, {"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{path}: HTTP {exc.code}: {exc.read().decode()}") from exc


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def profile_and_goals(robot):
    profile = json.loads((ROOT / f"examples/robots/{ROBOTS[robot]}.commissioning.profile.json").read_text())
    profile.update(robotId=robot, adapterId="gazebo_" + robot.replace("-", "_"),
        modelId="gazebo_generic_thrust_body" if robot == "uav-a" else "gazebo_generic_force_body")
    profile["sensors"] = [{"sourceId": robot + "/sim-state", "sourceType": "sensor_fusion",
        "frameId": "base_link", "transformRevision": "gazebo-ground-truth-v1", "maxAgeMs": 1000}]
    profile["mobility"].update(maxLinearSpeedMps=.5, maxAngularSpeedRadps=.4)
    profile["mobility"]["safeStop"].update(procedureId=robot + "-gazebo-stop-v1", commandLossTimeoutMs=500)
    if robot == "ground-a":
        profile["mobility"]["motionModel"] = "omnidirectional"
    if robot == "uav-a":
        profile["mobility"]["safeStop"]["strategy"] = "vendor_procedure"
        profile["mobility"]["safeStop"]["procedureId"] = "gazebo-hover-only-v1"
    for key in ("navigation.x", "navigation.y"):
        profile["actionLimits"][key].update(min=-4.5, max=4.5)
    profile["actionLimits"]["navigation.z"].update(min=.05, max=2.7 if robot == "uav-a" else .25)
    xyz = {"ground-a": [(-2., -1., .15), (2., -1., .15), (-2., 1., .15)],
           "ground-b": [(2., 1., .15), (-2., 1., .15), (2., -1., .15)],
           "uav-a": [(0., -3., 1.2), (1.5, 1.5, 1.8), (-1.5, 1.5, 1.2)]}[robot]
    names = ("living_room", "kitchen", "bedroom")
    goals = {"mapId": "gazebo-surveyed-room", "mapRevision": hashlib.sha256(b"field-v1").hexdigest(),
        "arrivalToleranceM": .12, "goals": {n: [*p, 1., 0., 0., 0.] for n, p in zip(names, xyz)},
        "aliases": dict(zip(("客厅", "厨房", "卧室"), names)),
        "routeEdges": {n: [other for other in names if other != n] for n in names}}
    return profile, goals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--hub", default="http://127.0.0.1:18118")
    parser.add_argument("--robots", nargs="+", choices=list(ROBOTS), default=list(ROBOTS))
    parser.add_argument("--seeds", nargs="+", type=int, default=[3, 7, 19])
    parser.add_argument("--short", action="store_true", help="development smoke only")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    install_loopback_opener()
    processes, logs, agents = [], [], {}
    summary = {"schemaVersion": "gazebo.field-campaign.v1", "success": False,
        "evidenceClass": "actual_gazebo_classical_physics_ros2_grpc_localagent",
        "localization": "gazebo_ground_truth_link_proxy", "reinforcementLearning": False,
        "vendorDynamicsValidated": False, "seeds": args.seeds, "trials": [],
        "binarySha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
        "startedAtUnixMs": int(time.time()*1000)}
    save(output / "summary.json", summary)

    def launch(command, path, env=None):
        log = path.open("w")
        logs.append(log)
        proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        processes.append(proc)
        return proc

    def wait(predicate, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            for proc in processes:
                if proc.poll() is not None:
                    raise RuntimeError(f"owned process exited: {proc.args}: {proc.returncode}")
            time.sleep(.1)
        raise TimeoutError("campaign deadline exceeded")

    try:
        for robot in args.robots:
            folder = output / robot
            folder.mkdir()
            profile, goals = profile_and_goals(robot)
            save(folder / "profile.json", profile)
            save(folder / "goals.json", goals)
            namespace = "/field_" + robot.replace("-", "_") + "_" + str(os.getpid())
            commissioning = "gazebo-commission-v1"
            launch([sys.executable, str(ROOT / "sim/gazebo/field_ros_controller.py"),
                "--profile", str(folder / "profile.json"), "--goals", str(folder / "goals.json"),
                "--robot-id", robot, "--hub", args.hub, "--namespace", namespace,
                "--commissioning-id", commissioning, "--journal", str(folder / "controller.jsonl")], folder / "controller.log")
            config = {"profilePath": str(folder / "profile.json"), "commissioningId": commissioning,
                "namespace": namespace, "stateMaxAgeMs": 1000}
            save(folder / "ros-config.json", config)
            grpc_port, api_port = free_port(), free_port()
            env = dict(os.environ, TANGYING_ROS_PROFILE_CONFIG=str(folder / "ros-config.json"))
            launch([sys.executable, "-m", "tangying_robot_gateway.run_plugin", "serve", "--factory",
                "tangying_ros_gateway.profiled_node:create_backend", "--journal", str(folder / "runtime.json"),
                "--listen", f"127.0.0.1:{grpc_port}", "--allow-insecure"], folder / "runtime.log", env)
            with grpc.insecure_channel(f"127.0.0.1:{grpc_port}") as channel:
                client = robot_pb2_grpc.RobotRuntimeStub(channel)
                def ready(client=client):
                    try:
                        return any(t.name == "navigation.navigate" and t.available for t in
                            client.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), timeout=1).capabilities)
                    except grpc.RpcError:
                        return False
                wait(ready, 30)
            (folder / "model.env").write_text("AGENT_PROVIDER=deterministic\n" + "".join(
                f"AGENT_{s}_PROVIDER=deterministic\n" for s in ("GOAL", "INTENT", "PLANNING", "RECOVERY")))
            command = [str(args.binary.resolve()), "--config", str(folder / "model.env"),
                "--listen", f"127.0.0.1:{api_port}", "--robot", f"127.0.0.1:{grpc_port}",
                "--robot-id", robot, "--dev-insecure", "--robot-safety-profile", "desktop_standard",
                "--data-dir", str(folder / "local-agent")]
            admitted = subprocess.run([*command, "--admit-runtime-contract"], capture_output=True, text=True, timeout=30, check=False)
            (folder / "admission.log").write_text(admitted.stdout + admitted.stderr)
            if admitted.returncode:
                raise RuntimeError(f"{robot} Runtime admission failed")
            launch(command, folder / "local-agent.log")
            base = f"http://127.0.0.1:{api_port}"
            def agent_ready(base=base, profile=profile):
                try:
                    return http(base, "/v1/telemetry?adapter=" + profile["adapterId"] + "&limit=1").get("hasLatest")
                except OSError:
                    return False
            wait(agent_ready, 30)
            token = (folder / "local-agent/console-session").read_text().strip()
            agents[robot] = {"base": base, "token": token, "profile": profile, "folder": folder}
        for seed in args.seeds:
            trial = {"seed": seed, "seedMeaning": "stale-peer fault injection timing; physics is deterministic",
                "request": "从客厅出发，去厨房，最后回到客厅。" if args.short else REQUEST,
                "startedAtUnixMs": int(time.time()*1000), "tasks": {}, "faults": []}
            summary["trials"].append(trial)
            task_ids = {}
            for robot, agent in agents.items():
                task = http(agent["base"], "/v1/tasks", {"adapter": agent["profile"]["adapterId"],
                    "request": trial["request"]}, agent["token"])
                save(agent["folder"] / f"created-{seed}.json", task)
                task_ids[robot] = task["id"]
                http(agent["base"], f"/v1/tasks/{task['id']}/approve", {}, agent["token"])
            started, injected, cleared = time.monotonic(), False, False
            def completed(started=started, seed=seed, trial=trial, task_ids=task_ids):
                nonlocal injected, cleared
                elapsed = time.monotonic()-started
                if not args.short and "ground-a" in agents:
                    if not injected and elapsed > 3 + seed % 3:
                        http(args.hub, "/inject", {"robotId": "ground-a", "kind": "stale_peer"})
                        trial["faults"].append({"type": "stale_peer", "atUnixMs": int(time.time()*1000)})
                        injected = True
                    if injected and not cleared and elapsed > 5 + seed % 3:
                        http(args.hub, "/inject", {"robotId": "ground-a", "kind": "clear"})
                        trial["faults"][-1]["clearedAtUnixMs"] = int(time.time()*1000)
                        cleared = True
                for robot, agent in agents.items():
                    task = http(agent["base"], f"/v1/tasks/{task_ids[robot]}", token=agent["token"])
                    if task["state"] in TERMINAL and robot not in trial["tasks"]:
                        save(agent["folder"] / f"final-{seed}.json", task)
                        save(agent["folder"] / f"observations-{seed}.json", http(agent["base"],
                            f"/v1/tasks/{task_ids[robot]}/observations", token=agent["token"]))
                        trial["tasks"][robot] = {"taskId": task["id"], "state": task["state"]}
                        save(output / "summary.json", summary)
                return len(trial["tasks"]) == len(agents)
            wait(completed, 600)
            trial["elapsedWallSeconds"] = time.monotonic()-started
            trial["success"] = all(t["state"] == "SUCCEEDED" for t in trial["tasks"].values())
            save(output / "summary.json", summary)
            if not trial["success"]:
                raise RuntimeError("one or more natural language tasks failed; retained full events")
        summary["success"] = True
    except Exception as exc:
        summary["error"] = str(exc)
        raise
    finally:
        summary["endedAtUnixMs"] = int(time.time()*1000)
        for proc in reversed(processes):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        for log in logs:
            log.close()
        save(output / "summary.json", summary)
        # LocalAgent session credentials remain local and are never public evidence.
        save(output / "manifest.json", {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob("*")) if p.is_file() and p.name != "manifest.json"})


if __name__ == "__main__":
    main()
