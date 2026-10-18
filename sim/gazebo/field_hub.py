"""Loopback-only Gazebo experiment coordinator; not a deployed robot driver.

Owns simulator stepping and classical local avoidance. HTTP goals have a bounded
command lease; loss of the ROS driver stops refreshing the physics controller.
Localization here is explicitly Gazebo rigid-body ground truth, not SLAM.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from field_scan import extract_obstacles
from tangying_robot_gateway.dynamic_avoidance import (
    AvoidanceConfig,
    MotionObservation,
    VelocityAvoider,
)

ROBOTS = ("ground-a", "ground-b", "uav-a")


class Hub:
    def __init__(self, executable, output):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.transport = subprocess.Popen([executable], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.lock = threading.RLock()
        self.snapshot = {}
        self.goals = {}
        self.stopped = set()
        self.injections = {}
        self.running = True
        self.error = None
        self.sequence = 0
        self.avoiders = {r: VelocityAvoider(AvoidanceConfig(dimensions=3 if r == "uav-a" else 2,
            max_speed=.5, max_observation_age=.35)) for r in ROBOTS}
        self.journal = (self.output / "physics.jsonl").open("w")
        self.events = (self.output / "hub-events.jsonl").open("w")

    def rpc(self, **data):
        self.transport.stdin.write(json.dumps(data) + "\n")
        self.transport.stdin.flush()
        value = json.loads(self.transport.stdout.readline())
        if not value.get("ok"):
            raise RuntimeError(value.get("error", "Gazebo transport unavailable"))
        return value

    def event(self, event_type, **data):
        self.events.write(json.dumps({"type": event_type, "atUnixMs": int(time.time()*1000), **data}) + "\n")
        self.events.flush()

    def status(self, robot):
        state = self.snapshot.get("states", {}).get(robot)
        goal = self.goals.get(robot)
        if state is None:
            raise ValueError("no measured state")
        pose = [*state["position"], *state["quaternionWXYZ"]]
        distance = math.dist(pose[:3], goal["goalPose"][:3]) if goal else None
        speed = math.sqrt(sum(v*v for v in state["linearVelocity"]))
        fresh_lease = goal and time.monotonic() < goal["expiresMonotonic"] and time.time()*1000 < goal["deadlineUnixMs"]
        return {"robotId": robot, "commandId": goal["commandId"] if goal else None,
            "pose": pose, "velocity": state["linearVelocity"], "simulationTime": state["simulationTime"],
            "sequence": self.sequence, "capturedAtUnixMs": self.snapshot["capturedAtUnixMs"],
            "distance": distance, "arrived": bool(fresh_lease and distance <= .12 and speed <= .07
                and not goal.get("blockedReason")), "blockedReason": goal.get("blockedReason") if goal else None}

    def request(self, path, data):
        robot = data.get("robotId")
        if robot not in ROBOTS:
            raise ValueError("unknown robot")
        with self.lock:
            if path in ("/goal", "/lease"):
                lease = data.get("leaseMs", 0)
                deadline = data.get("deadlineUnixMs", 0)
                if not 0 < lease <= 500 or deadline <= time.time()*1000:
                    raise ValueError("expired or invalid lease/deadline")
                if robot in self.stopped:
                    raise ValueError("robot is latched stopped")
                if path == "/goal":
                    pose = data["goalPose"]
                    if (len(pose) != 7 or not all(math.isfinite(x) for x in pose)
                            or any(abs(x) > 4.5 for x in pose[:2]) or not .05 <= pose[2] <= 2.7):
                        raise ValueError("goal outside simulation envelope")
                    previous = self.goals.get(robot)
                    if previous:
                        if previous["commandId"] == data.get("commandId"):
                            raise ValueError("motion goal command identity cannot be replayed")
                        arrived = self.status(robot)["arrived"]
                        # Arrival requires a live lease. Between commands the
                        # old lease can expire before the physics loop records
                        # blockedReason; classify expiry under this same lock,
                        # rather than treating that timer lag as an active goal.
                        expired = (time.monotonic() >= previous["expiresMonotonic"]
                                   or time.time()*1000 >= previous["deadlineUnixMs"])
                        if not arrived and not previous.get("blockedReason") and not expired:
                            raise ValueError("robot already has an active goal")
                        if expired and not previous.get("blockedReason"):
                            previous["blockedReason"] = "lease_expired"
                            self.event("PREVIOUS_GOAL_EXPIRED", robotId=robot,
                                commandId=previous["commandId"], nextCommandId=data.get("commandId"))
                    self.goals[robot] = dict(data)
                goal = self.goals.get(robot)
                if not goal or goal["commandId"] != data.get("commandId"):
                    raise ValueError("lease does not match active command")
                if path == "/lease" and (goal.get("blockedReason") or time.monotonic() >= goal["expiresMonotonic"]):
                    goal["blockedReason"] = goal.get("blockedReason", "lease_expired")
                    raise ValueError("expired or blocked command cannot be renewed")
                if goal["deadlineUnixMs"] != deadline:
                    raise ValueError("lease cannot alter command deadline")
                goal["expiresMonotonic"] = time.monotonic() + lease / 1000
                goal["deadlineUnixMs"] = deadline
            elif path in ("/cancel", "/stop"):
                goal = self.goals.get(robot)
                if goal:
                    if data.get("commandId") and data["commandId"] != goal["commandId"]:
                        raise ValueError("cancel command identity mismatch")
                    goal["blockedReason"] = "requested_stop" if path == "/stop" else "cancelled"
                if path == "/stop":
                    self.stopped.add(robot)
            elif path == "/inject":
                if data.get("kind") not in ("stale_peer", "withhold_controller_command", "clear"):
                    raise ValueError("unknown injection")
                self.injections[robot] = data["kind"]
            else:
                raise ValueError("unknown endpoint")
            if path != "/lease":
                self.event(path, **data)
            return {"ok": True, "commandId": data.get("commandId"), "physicalStopVerified": False}

    def observation(self, robot, state, dimensions, now, *, stale=False):
        return MotionObservation(identity=robot, position=tuple(state["position"][:dimensions]),
            velocity=tuple(state["linearVelocity"][:dimensions]), radius=state["radius"],
            observed_at=state["simulationTime"] - (1 if stale else 0), world_id="field-validation",
            frame_id="world", transform_revision="gazebo-ground-truth-v1")

    def run(self):
        try:
            self.rpc(op="step", steps=100)
            previous = -1
            while self.running:
                started = time.monotonic()
                snap = self.rpc(op="snapshot", includeScans=True, waitForSensors=True)
                now = min(s["simulationTime"] for s in snap["states"].values())
                if now <= previous:
                    raise RuntimeError("simulator measurement clock did not advance")
                previous = now
                commands = {}
                with self.lock:
                    self.sequence += 1
                    snap.update(sequence=self.sequence, capturedAtUnixMs=int(time.time()*1000))
                    self.snapshot = snap
                    goals = copy.deepcopy(self.goals)
                    stopped, injections = set(self.stopped), dict(self.injections)
                for robot in ROBOTS:
                    state, goal = snap["states"][robot], goals.get(robot)
                    velocity, reason, diagnostics = [0., 0., 0.], "idle", {}
                    if goal:
                        if time.monotonic() > goal["expiresMonotonic"] or time.time()*1000 > goal["deadlineUnixMs"]:
                            goal["blockedReason"] = "lease_expired"
                        if not goal.get("blockedReason") and robot not in stopped:
                            d = 3 if robot == "uav-a" else 2
                            own = self.observation(robot, state, d, now)
                            peers = [self.observation(other, value, d, now,
                                stale=injections.get(robot) == "stale_peer")
                                for other, value in snap["states"].items() if other != robot
                                and (d == 3 or abs(value["position"][2]-state["position"][2]) < .65)]
                            delta = [goal["goalPose"][i]-state["position"][i] for i in range(d)]
                            desired = [x*.9 for x in delta]
                            scan = extract_obstacles(robot, snap["scans"].get(robot, {}), state,
                                peer_states=snap["states"], now=now, dimensions=d)
                            if scan.usable:
                                decision = self.avoiders[robot].select(own, tuple(desired),
                                    peers=peers, obstacles=scan.obstacles, now=now)
                                velocity[:d] = decision.velocity
                                reason, diagnostics = decision.reason, decision.diagnostics
                            else:
                                reason = "scan_unavailable"
                            diagnostics["scan"] = scan.diagnostics
                        else:
                            reason = goal.get("blockedReason", "requested_stop")
                    commands[robot] = {"velocity": velocity, "reason": reason, "diagnostics": diagnostics,
                                       "commandId": goal.get("commandId") if goal else None}
                for robot, command in commands.items():
                    with self.lock:
                        active = self.goals.get(robot)
                        expired = active and (time.monotonic() > active["expiresMonotonic"]
                            or time.time()*1000 > active["deadlineUnixMs"])
                        if expired:
                            active["blockedReason"] = "lease_expired"
                        blocked = active and (active.get("blockedReason")
                            or active["commandId"] != command["commandId"])
                        stop = robot in self.stopped or expired or blocked
                        withheld = self.injections.get(robot) == "withhold_controller_command"
                    if not withheld:
                        if stop:
                            self.rpc(op="stop", robotId=robot)
                            command["velocity"] = [0., 0., 0.]
                            command["reason"] = active.get("blockedReason", "command_changed") if active else "requested_stop"
                        else:
                            self.rpc(op="velocity", robotId=robot, velocity=command["velocity"])
                record = {k: v for k, v in snap.items() if k != "scans"}
                record["commands"] = commands
                self.journal.write(json.dumps(record) + "\n")
                self.journal.flush()
                if self.sequence == 1 or self.sequence % 50 == 0:
                    (self.output / f"sensors-{self.sequence:06}.json").write_text(json.dumps(snap))
                self.rpc(op="step", steps=50)
                time.sleep(max(0., .1 - (time.monotonic()-started)))
        except (ValueError, OSError, RuntimeError, KeyError) as exc:
            self.error = str(exc)
            self.event("HUB_FAILURE", error=self.error)
        finally:
            for robot in ROBOTS:
                try:
                    self.rpc(op="stop", robotId=robot)
                except (ValueError, OSError, RuntimeError) as exc:
                    self.event("CLEANUP_STOP_FAILED", robotId=robot, error=str(exc))
            self.transport.terminate()
            self.transport.wait(timeout=5)
            self.journal.close()
            self.events.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--port", type=int, default=18118)
    args = parser.parse_args()
    hub = Hub(args.transport, args.output)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self, data=None):
            try:
                url = urlparse(self.path)
                with hub.lock:
                    if hub.error:
                        raise ValueError(hub.error)
                    if data is not None:
                        value = hub.request(url.path, data)
                    elif url.path == "/snapshot":
                        value = {k: v for k, v in hub.snapshot.items() if k != "scans"}
                    elif url.path == "/status":
                        query = parse_qs(url.query)
                        value = hub.status(query["robotId"][0])
                        if query.get("commandId") and query["commandId"][0] != value["commandId"]:
                            raise ValueError("status command identity mismatch")
                    else:
                        raise ValueError("unknown endpoint")
                status = 200
            except (ValueError, KeyError, TypeError) as exc:
                value, status = {"error": str(exc)}, 400
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            try:
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            except (ValueError, TypeError):
                self.send_error(400)
                return
            self.handle_request(data)

    worker = threading.Thread(target=hub.run, daemon=True)
    worker.start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    try:
        server.serve_forever()
    finally:
        hub.running = False
        worker.join(timeout=10)
        server.server_close()


if __name__ == "__main__":
    main()
