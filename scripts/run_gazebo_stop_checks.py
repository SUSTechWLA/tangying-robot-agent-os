#!/usr/bin/env python3
"""Measure stopping motion in the running loopback Gazebo field hub.

Use a fresh field world, or run before the natural-language controllers exit:
controller shutdown and explicit stop both latch each tested robot. This checker
never clears a stop latch; an already latched robot is a setup failure.
For each robot, controller-command loss is tested first with the hub command
lease still renewed, then an explicit stop is tested. No pose reset is used.
UAV completion means measured hover; landing is not claimed by this campaign.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class MeasurementError(ValueError):
    """Malformed or foreign measurement, not a measured braking failure."""


def validate_measured_state(state, robot=None):
    if not isinstance(state, dict):
        raise MeasurementError("measured state must be an object")
    for field in ("position", "linearVelocity", "angularVelocity"):
        values = state.get(field)
        if (not isinstance(values, list) or len(values) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)):
            raise MeasurementError("measured state requires finite three-dimensional vectors")
    at = state.get("simulationTime")
    if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
        raise MeasurementError("invalid measured simulation clock")
    expected = {"robotId": robot, "clockDomain": "simulation", "worldId": "field-validation", "frameId": "world"}
    complete = robot is not None
    for field, value in expected.items():
        if field not in state:
            complete = False
        elif value is None or state[field] != value:
            raise MeasurementError("measured robot, clock, world or frame identity mismatch")
    return complete


def measured_stop(samples, trigger, *, speed_limit=.03, angular_limit=.05, dwell=.4):
    """Only increasing simulation-time measurements establish a settled stop."""
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
           for v in (speed_limit, angular_limit, dwell)):
        raise ValueError("stop thresholds must be finite and positive")
    identity_verified = validate_measured_state(trigger, trigger.get("robotId"))
    last_time, since, settled = trigger["simulationTime"], None, False
    accepted, max_displacement, distance = [], 0., 0.
    maximum_gap, discarded = 0., 0
    previous_position = trigger["position"]
    for sample in samples:
        identity_verified = validate_measured_state(sample, trigger.get("robotId")) and identity_verified
        at = sample["simulationTime"]
        if at <= last_time:
            discarded += 1
            continue
        maximum_gap = max(maximum_gap, at-last_time)
        last_time = at
        speed = math.sqrt(sum(v*v for v in sample["linearVelocity"]))
        angular = math.sqrt(sum(v*v for v in sample["angularVelocity"]))
        distance += math.dist(previous_position, sample["position"])
        previous_position = sample["position"]
        max_displacement = max(max_displacement, math.dist(trigger["position"], sample["position"]))
        if not all(math.isfinite(v) for v in (speed, angular, distance, max_displacement)):
            raise MeasurementError("derived stop measurement is not finite")
        accepted.append(sample)
        if speed <= speed_limit and angular <= angular_limit:
            since = at if since is None else since
            settled = at - since >= dwell - 1e-9
        else:
            since, settled = None, False
    return {"measuredStopped": settled and identity_verified, "sampleCount": len(accepted),
            "measurementIdentityVerified": identity_verified, "discardedNonadvancingSamples": discarded,
            "maximumSampleGapSimulationSeconds": maximum_gap,
            "dwellEvidence": "sampled states only; no continuous-motion certificate",
            "settledSinceSimulationTime": since, "elapsedSimulationSeconds": last_time-trigger["simulationTime"],
            "maximumDisplacementMeters": max_displacement, "travelDistanceMeters": distance,
            "lastSpeedMetersPerSecond": math.sqrt(sum(v*v for v in accepted[-1]["linearVelocity"])) if accepted else None,
            "positionSource": "Gazebo rigid-body measured state", "landingVerified": False}


class HubClient:
    def __init__(self, url, journal):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("only a loopback Gazebo hub is supported")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("plain loopback hub URL required")
        self.url = url.rstrip("/")
        self.journal = journal
        self.lock = threading.Lock()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, payload=None, *, timeout=3):
        encoded = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.url+path, data=encoded,
                                         headers={"Content-Type": "application/json"})
        started = time.time()
        try:
            with self.opener.open(request, timeout=timeout) as response:
                value = json.load(response)
        except urllib.error.HTTPError as exc:
            # Keep the rejection reason, otherwise all admission failures become
            # indistinguishable "400 Bad Request" physical stopping failures.
            body = exc.read(4096).decode("utf-8", errors="replace")
            self.record({"path": path, "payload": payload, "atUnixMs": int(started*1000),
                         "error": str(exc), "httpStatus": exc.code, "responseBody": body})
            raise ValueError(f"hub HTTP {exc.code}: {body}") from exc
        except (OSError, ValueError) as exc:
            self.record({"path": path, "payload": payload, "atUnixMs": int(started*1000), "error": str(exc)})
            raise
        self.record({"path": path, "payload": payload, "atUnixMs": int(started*1000),
                     "response": value if path != "/snapshot" else {"sequence": value.get("sequence")}})
        return value

    def record(self, value):
        with self.lock:
            self.journal.write(json.dumps(value)+"\n")
            self.journal.flush()


class Lease:
    def __init__(self, client, goal):
        self.client, self.goal = client, goal
        self.done = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.done.wait(.10):
            try:
                self.client.call("/lease", {key: self.goal[key] for key in
                                           ("robotId", "commandId", "leaseMs", "deadlineUnixMs")})
            except (OSError, ValueError) as exc:
                self.error = str(exc)
                return

    def close(self):
        self.done.set()
        self.thread.join(timeout=4)


def wait_for_readiness(client, robots, *, timeout=15., clock=time.monotonic, sleep=time.sleep):
    """Wait only on observations; require two advancing samples for every robot.

    Setup does not publish goals, lease renewals, fault injections or stop commands.
    A setup timeout is distinct from a robot failing a physical stopping trial.
    """
    if not math.isfinite(timeout) or not 0 < timeout <= 15:
        raise ValueError("readiness timeout must be in (0, 15] seconds")
    started, attempts, first_times, last_error = clock(), 0, None, "no measurements"
    while clock()-started < timeout:
        remaining = timeout-(clock()-started)
        if remaining <= 0:
            break
        attempts += 1
        try:
            snapshot = client.call("/snapshot", timeout=min(1., remaining))
            stopped = snapshot["stoppedRobots"]
            if not isinstance(stopped, list) or any(not isinstance(r, str) for r in stopped):
                raise ValueError("invalid stop-latch observation")
            latched = sorted(set(robots).intersection(stopped))
            if latched:
                return {"ready": False, "failureType": "setup_failure", "attempts": attempts,
                        "elapsedWallSeconds": clock()-started, "latchedRobots": latched,
                        "error": "robot is latched stopped; use an independent fresh trial world",
                        "motionCommandsSent": False}
            states = snapshot["states"]
            times = {}
            for robot in robots:
                state = states[robot]
                if state["robotId"] != robot or state["clockDomain"] != "simulation":
                    raise ValueError("robot identity or clock mismatch")
                for field, size in (("position", 3), ("linearVelocity", 3),
                                    ("angularVelocity", 3), ("quaternionWXYZ", 4)):
                    values = state[field]
                    if len(values) != size or not all(type(v) in (int, float) and math.isfinite(v) for v in values):
                        raise ValueError("malformed measured state")
                at = state["simulationTime"]
                if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
                    raise ValueError("invalid simulation clock")
                times[robot] = at
            if first_times is not None and all(times[r] > first_times[r] for r in robots):
                return {"ready": True, "attempts": attempts, "elapsedWallSeconds": clock()-started,
                        "initialSimulationTimes": first_times, "readySimulationTimes": times,
                        "motionCommandsSent": False}
            first_times = times if first_times is None else first_times
            last_error = "waiting for advancing measured states"
        except (OSError, ValueError, KeyError, TypeError) as exc:
            last_error = str(exc)
        remaining = timeout-(clock()-started)
        if remaining > 0:
            sleep(min(.1, remaining))
    return {"ready": False, "failureType": "setup_failure", "attempts": attempts,
            "elapsedWallSeconds": clock()-started, "timeoutSeconds": timeout,
            "error": last_error, "motionCommandsSent": False}


def run_trial(client, robot, mode, samples_file, *, timeout=30.):
    trial = {"robotId": robot, "mode": mode, "passed": False, "landingVerified": False,
             "goalAdmissionAttempted": False, "goalAdmitted": False, "stopTriggered": False}
    lease, goal, injected = None, None, False
    try:
        before = client.call("/snapshot")["states"][robot]
        if not validate_measured_state(before, robot):
            raise MeasurementError("stopping trial requires complete measurement identity")
        xyz = list(before["position"])
        xyz[0] += -1.5 if xyz[0] > 0 else 1.5
        if not all(abs(x) <= 4.5 for x in xyz[:2]):
            raise RuntimeError("no bounded 1.5m stopping-test goal from current state")
        goal = {"robotId": robot, "commandId": f"stop-check-{robot}-{mode}-{uuid.uuid4().hex}",
                "leaseMs": 450, "deadlineUnixMs": int((time.time()+timeout+20)*1000),
                "goalPose": [*xyz, 1., 0., 0., 0.]}
        trial["commandId"] = goal["commandId"]
        trial["goal"] = goal
        trial["goalAdmissionAttempted"] = True
        client.call("/goal", goal)
        trial["goalAdmitted"] = True
        lease = Lease(client, goal)
        lease.thread.start()
        deadline, prior_time, moving = time.monotonic()+timeout, -1., 0
        trigger = None
        while time.monotonic() < deadline:
            if lease.error:
                raise RuntimeError(f"hub lease failure before trigger: {lease.error}")
            snapshot = client.call("/snapshot")
            state = snapshot["states"][robot]
            if not validate_measured_state(state, robot):
                raise MeasurementError("stopping trial requires complete measurement identity")
            if state["simulationTime"] > prior_time:
                prior_time = state["simulationTime"]
                samples_file.write(json.dumps({"robotId": robot, "mode": mode, "phase": "acceleration",
                                                "state": state})+"\n")
                speed = math.sqrt(sum(v*v for v in state["linearVelocity"]))
                moving = moving+1 if speed >= .18 else 0
                if moving >= 3:
                    trigger = state
                    break
            time.sleep(.04)
        if trigger is None:
            raise RuntimeError("robot never established measured nonzero motion before stop")
        trial["triggerState"] = trigger
        if mode == "watchdog":
            client.call("/inject", {"robotId": robot, "kind": "withhold_controller_command"})
            injected = True
        else:
            lease.close()
            client.call("/stop", {"robotId": robot, "commandId": goal["commandId"]})
        trial["stopTriggered"] = True
        after, deadline = [], time.monotonic()+timeout
        while time.monotonic() < deadline:
            if mode == "watchdog" and lease.error:
                raise RuntimeError(f"hub lease failed during isolated controller-watchdog trial: {lease.error}")
            snapshot = client.call("/snapshot")
            state = snapshot["states"][robot]
            if not validate_measured_state(state, robot):
                raise MeasurementError("stopping trial requires complete measurement identity")
            if state["simulationTime"] > prior_time:
                prior_time = state["simulationTime"]
                after.append(state)
                samples_file.write(json.dumps({"robotId": robot, "mode": mode, "phase": "deceleration",
                                                "state": state})+"\n")
                samples_file.flush()
                metrics = measured_stop(after, trigger)
                if metrics["measuredStopped"]:
                    break
            time.sleep(.04)
        metrics = measured_stop(after, trigger)
        expected = "command_watchdog_expired" if mode == "watchdog" else "requested_stop"
        observed_reason = any(s.get("stopReason") == expected for s in after)
        trial.update(metrics, expectedControllerReason=expected, controllerReasonObserved=observed_reason)
        distance_bound = .9 if robot == "uav-a" else .65
        trial["criterion"] = {"speedAtTriggerAtLeast": .18, "settledSpeedAtMost": .03,
                              "settledAngularSpeedAtMost": .05, "dwellSimulationSeconds": .4,
                              "maxDisplacementMeters": distance_bound, "maxElapsedSimulationSeconds": 4.}
        trial["passed"] = bool(metrics["measuredStopped"] and observed_reason
                               and metrics["maximumDisplacementMeters"] <= distance_bound
                               and metrics["elapsedSimulationSeconds"] <= 4.)
        if not trial["passed"]:
            trial["failureType"] = "physical_stop_failure"
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        trial["error"] = str(exc)
        trial["failureType"] = ("measurement_failure" if isinstance(exc, (MeasurementError, KeyError)) else
                                "admission_failure" if not trial["goalAdmitted"] else
                                "motion_readiness_failure" if not trial["stopTriggered"] else "trial_execution_failure")
    finally:
        if lease:
            lease.close()
        if goal:
            try:
                # Cancel before restoring command delivery, so an old goal cannot resume.
                client.call("/cancel", {"robotId": robot, "commandId": goal["commandId"]})
                if injected:
                    client.call("/inject", {"robotId": robot, "kind": "clear"})
                if not trial["passed"]:
                    client.call("/stop", {"robotId": robot, "commandId": goal["commandId"]})
            except (OSError, ValueError) as exc:
                trial["cleanupError"] = str(exc)
                trial.setdefault("failureType", "cleanup_failure")
                trial["passed"] = False
    return trial


def run_campaign(client, robots, samples, *, timeout=30.):
    setup = wait_for_readiness(client, robots)
    trials = []
    if setup["ready"]:
        for robot in robots:
            watchdog = run_trial(client, robot, "watchdog", samples, timeout=timeout)
            trials.append(watchdog)
            if watchdog["passed"]:
                trials.append(run_trial(client, robot, "explicit_stop", samples, timeout=timeout))
            else:
                trials.append({"robotId": robot, "mode": "explicit_stop", "passed": False,
                               "failureType": "skipped",
                               "skippedReason": "watchdog trial failed; no further motion attempted"})
    passed = setup["ready"] and len(trials) == len(robots)*2 and all(t["passed"] for t in trials)
    return {"setup": setup, "trials": trials, "passed": passed,
            "requestedTrialCount": len(robots)*2,
            "goalAdmissionAttemptCount": sum(t.get("goalAdmissionAttempted", False) for t in trials),
            "admittedTrialCount": sum(t.get("goalAdmitted", False) for t in trials),
            "physicallyTriggeredTrialCount": sum(t.get("stopTriggered", False) for t in trials),
            "skippedTrialCount": sum("skippedReason" in t for t in trials),
            "outcome": "setup_failure" if not setup["ready"] else "passed" if passed else "trial_failure"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub", default="http://127.0.0.1:18118")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--robots", nargs="+", choices=("ground-a", "ground-b", "uav-a"),
                        default=["ground-a", "ground-b", "uav-a"])
    parser.add_argument("--timeout", type=float, default=30.)
    args = parser.parse_args()
    if not 5 <= args.timeout <= 120 or len(set(args.robots)) != len(args.robots):
        parser.error("timeout must be 5..120 and robot ids must be unique")
    args.output.mkdir(parents=True, exist_ok=False)
    with (args.output/"requests.jsonl").open("w") as requests, (args.output/"samples.jsonl").open("w") as samples:
        client = HubClient(args.hub, requests)
        result = run_campaign(client, args.robots, samples, timeout=args.timeout)
    summary = {"schemaVersion": "gazebo.physical_stop_checks.v1", **result,
               "uavStopMode": "hover", "landingVerified": False,
               "hardwareValidation": False, "poseResetUsed": False,
               "physicalStateSource": "Gazebo force-driven rigid-body physics"}
    (args.output/"summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False)+"\n")
    files = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in args.output.iterdir()}
    (args.output/"manifest.json").write_text(json.dumps({"sha256": files}, indent=2)+"\n")
    print(json.dumps({"output": str(args.output), "passed": summary["passed"],
                      "outcome": summary["outcome"], "trials": len(summary["trials"]),
                      "passedTrials": sum(t["passed"] for t in summary["trials"])}))
    raise SystemExit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
