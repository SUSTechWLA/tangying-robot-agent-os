"""Verify fail-closed navigation on loss of the simulation ROS acquisition bridge.

Requires an explicitly supplied, already running task. Never creates, approves,
retries or resumes a robot task. SIGSTOP/SIGCONT only target one verified bridge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import time
from itertools import pairwise
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

BRIDGE = "/opt/tangying-nav/install/tangying_navigation/lib/tangying_navigation/runtime_rgbd_bridge"
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERABLE_FAILURE", "SAFETY_STOPPED"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def bridge_identity(proc=Path("/proc")):
    matches = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            if os.fsencode(BRIDGE) not in argv:
                continue
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            matches.append({"pid": int(entry.name), "startTicks": fields[19], "state": fields[0]})
        except (FileNotFoundError, ProcessLookupError):
            continue
    require(len(matches) == 1, "expected exactly one installed acquisition bridge process")
    return matches[0]


def signal_bridge(operation, identity, *, proc=Path("/proc"), kill=os.kill):
    require(operation in {"stop", "continue"}, "unsupported bridge operation")
    current = bridge_identity(proc)
    require(all(current[k] == identity[k] for k in ("pid", "startTicks")),
            "bridge identity changed; no signal sent")
    kill(current["pid"], signal.SIGSTOP if operation == "stop" else signal.SIGCONT)
    return current


def container_identity(name):
    raw = subprocess.run(["docker", "inspect", name], check=True, capture_output=True, text=True)
    values = json.loads(raw.stdout)
    require(len(values) == 1, "navigation container identity is ambiguous")
    value = values[0]
    labels = value["Config"].get("Labels", {})
    require(value["State"]["Running"]
            and labels.get("com.docker.compose.project") == "tangying-navigation"
            and labels.get("com.docker.compose.service") == "navigation",
            "expected the running tangying-navigation/navigation compose container")
    return value["Id"]  # Pin the immutable ID, never operate a replacement by name.


def control(container, operation, identity=None):
    args = ["docker", "exec", "-i", container, "python3", "-", "--inside-container", operation]
    if identity is not None:
        args.append(json.dumps(identity, separators=(",", ":")))
    result = subprocess.run(args, input=Path(__file__).read_text(), text=True,
                            capture_output=True, timeout=5, check=True)
    return json.loads(result.stdout)


def map_diagnostics():
    # Keep the private token inside this process/container. The public Console
    # intentionally omits diagnostic internals; this read-only acceptance path
    # returns an explicit whitelist without images, map cells or credentials.
    port = int(os.environ.get("TANGYING_NAVIGATION_PORT", "18790"))
    request = Request(f"http://127.0.0.1:{port}/v1/navigation/map", headers={
        "Authorization": "Bearer " + os.environ["TANGYING_NAVIGATION_TOKEN"]})
    with urlopen(request, timeout=3) as response:
        value = json.load(response)
    return {key: value.get(key) for key in (
        "ready", "readinessBlockers", "inputAgeMs", "checkedAtUnixMs", "poseObservedAtUnixMs",
        "odomObservedAtUnixMs", "sensorObservedAtUnixMs", "poseSource", "mapRevision", "visualQuality",
        "localizationState", "robotId", "mode")}


def validate_profile(observation):
    profile, state = observation["robotProfile"], observation["robotState"]
    require(profile["adapterId"] == "mujoco" and profile["modelId"] == "xlerobot-rgbd-reference"
            and state["perception"]["mode"] == "rgbd"
            and state["navigation"]["backend"] == "rtabmap_nav2",
            "fault injection is restricted to the commissioned MuJoCo RGB-D simulation")
    return state["base_pose"]


def navigation_activity(task):
    latest = {}
    for event in task.get("events", []):
        if event["type"] == "TOOL_ACTIVITY" and event["payload"].get("toolName") == "navigation.navigate":
            latest[event["stepId"]] = event
    return next((event for event in reversed(list(latest.values()))
                 if event["payload"].get("activityStatus") == "RUNNING"), None)


def no_manipulation_after(task, sequence):
    return not any(event.get("sequence", 0) > sequence and event["type"] == "TOOL_ACTIVITY"
                   and event["payload"].get("toolName") in {"manipulation.pick", "manipulation.place"}
                   for event in task.get("events", []))


def pose_drift(poses):
    def yaw(pose):
        w, x, y, z = pose[3:]
        return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    def angle(pose):
        delta = yaw(pose) - yaw(poses[0])
        return abs(math.atan2(math.sin(delta), math.cos(delta)))
    return max(math.dist(poses[0][:2], pose[:2]) for pose in poses), max(map(angle, poses))


def native_sample(observation, checked_at_ms=None):
    reconstruction = observation["reconstruction"]
    stamp = reconstruction.get("observedAtUnixMs")
    checked = round(time.time() * 1000) if checked_at_ms is None else checked_at_ms
    require(type(stamp) is int and 0 <= checked - stamp <= 1000
            and reconstruction.get("sourceType") == "rgbd_camera"
            and reconstruction.get("observationId"),
            "native stop evidence requires a fresh original RGB-D observation")
    return {"captureId": reconstruction["observationId"], "sourceId": reconstruction["sourceId"],
            "observedAtUnixMs": stamp, "checkedAtUnixMs": checked,
            "basePose": observation["robotState"]["base_pose"]}


def camera_sample(api, source_id):
    # Telemetry is an asynchronous display cache. Request one atomic original
    # camera capture for stop proof instead of aging/reusing its cached pose.
    value = api("/v1/scene/camera?adapter=mujoco&sourceId=" + quote(source_id, safe=""))["snapshot"]
    validate_profile(value)
    require(value["reconstruction"]["sourceId"] == source_id,
            "stop evidence camera source changed")
    return native_sample(value)


def validate_stopped_samples(samples):
    stamps = [sample["observedAtUnixMs"] for sample in samples]
    require(len(set(stamps)) >= 2 and stamps[-1] - stamps[0] >= 600
            and all(after >= before for before, after in pairwise(stamps)),
            "stop evidence must contain increasing original captures spanning at least 600 ms")
    drift, yaw_drift = pose_drift([sample["basePose"] for sample in samples])
    require(drift <= .002 and yaw_drift <= .005, "base continued moving during stop verification")
    return drift, yaw_drift


def run(base, task_id, output, container_name):
    origin = urlsplit(base)
    require(origin.scheme == "http" and origin.hostname in {"127.0.0.1", "localhost"}
            and not origin.username and not origin.password and origin.path in {"", "/"}
            and not origin.query and not origin.fragment, "requires a loopback simulation origin")
    require(task_id and "/" not in task_id, "requires an explicit task ID")
    output.mkdir(parents=True, exist_ok=False)
    task_path = "/v1/tasks/" + quote(task_id, safe="")

    def api(path, body=None):
        request = Request(base.rstrip("/") + path,
                          data=None if body is None else json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    def save(name, data):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def telemetry():
        value = api("/v1/telemetry?adapter=mujoco&limit=1")["latest"]
        validate_profile(value)
        return value

    initial = telemetry()
    initial_pose = validate_profile(initial)
    source_id = initial["reconstruction"].get("sourceId")
    require(isinstance(source_id, str) and source_id
            and initial["reconstruction"].get("sourceType") == "rgbd_camera",
            "requires an existing RGB-D camera source for native stop evidence")
    save("initial-observation.json", initial)
    task = api(task_path)
    require(task["adapter"] == "mujoco" and task["state"] not in TERMINAL,
            "specified task must already be active in the simulation")
    save("initial-task.json", task)
    target = initial["robotState"]["navigation"]["approach_goal_pose"]
    require(math.dist(initial_pose[:2], target[:2]) >= .1,
            "need at least 10 cm remaining approach to inject during real movement")
    container = container_identity(container_name)
    bridge = control(container, "inspect")
    require(bridge["state"] not in {"T", "t", "Z"}, "bridge was already stopped; refusing to take ownership")
    save("bridge-identity.json", {"containerId": container, **bridge})
    restore_needed = False
    samples, failed_record, failed_event = [], None, None
    try:
        end = time.monotonic() + 30
        while time.monotonic() < end:
            task, observed = api(task_path), telemetry()
            require(task["state"] not in TERMINAL, "task terminated before fault injection")
            active = navigation_activity(task)
            moved = math.dist(initial_pose[:2], observed["robotState"]["base_pose"][:2])
            if active and moved >= .03:
                step_id = active["stepId"]
                save("before-fault-observation.json", observed)
                save("before-fault-task.json", task)
                break
            time.sleep(.1)
        else:
            raise TimeoutError("no confirmed navigation movement within 30 seconds; bridge untouched")

        restore_needed = True  # Restore even if the stop reply is lost after delivery.
        control(container, "stop", bridge)
        stopped_at = time.monotonic()
        save("injection.json", {"signal": "SIGSTOP", "injectedAtUnixMs": round(time.time() * 1000),
                                "stepId": step_id, "movedBeforeInjectionM": moved})
        while time.monotonic() - stopped_at < 8:
            task, world = api(task_path), control(container, "map")
            samples.append({"checkedAtUnixMs": round(time.time() * 1000),
                            "state": task["state"], "map": world,
                            "nativeObservation": camera_sample(api, source_id)})
            save("fault-samples.json", samples)
            save("failed-task.json", task)
            if task["state"] in TERMINAL:
                break
            time.sleep(.1)
        require(task["state"] in {"FAILED", "RECOVERABLE_FAILURE", "SAFETY_STOPPED"},
                "source loss did not fail the task within 8 seconds")
        failed_event = next((event for event in reversed(task["events"])
                             if event.get("stepId") == step_id and event["type"] == "TOOL_ACTIVITY"
                             and event["payload"].get("activityStatus") == "FAILED"), None)
        require(failed_event is not None, "expected a failed receipt for the interrupted navigation")
        require(no_manipulation_after(task, active["sequence"]),
                "manipulation continued after the interrupted navigation began")
        index = api(task_path + "/observations?limit=200")
        save("observation-index.json", index)
        capture = failed_event["payload"]["receiptObservationId"]
        record = next(value for value in index["records"] if value["captureId"] == capture and value["stepId"] == step_id)
        detail_path = task_path + "/observations/" + record["id"]
        failed_record = api(detail_path)
        save("failed-navigation-observation.json", failed_record)
        for kind in ("rgb", "depth"):
            with urlopen(base.rstrip("/") + detail_path + "/" + kind, timeout=3) as response:
                raw = response.read()
            (output / ("failed-navigation-" + kind + ".png")).write_bytes(raw)
            require(hashlib.sha256(raw).hexdigest() == record[kind + "Sha256"], "original evidence hash mismatch")
        navigation = failed_record["snapshot"]["robotState"]["navigation"]
        require(navigation["passed"] is False, "failure evidence incorrectly claims arrival")
        frozen = navigation["map_receipt"]["failure_observation"]
        require(frozen.get("checkedAtUnixMs", 0) > 0, "failed navigation lacks original frozen diagnostics")
        # Actual controller stopping can precede the camera-age watchdog. Retain
        # that precise failure observation; never replace it with a later map.
        stable = [camera_sample(api, source_id)]
        for _ in range(10):
            time.sleep(.1)
            stable.append(camera_sample(api, source_id))
        save("stopped-base-poses.json", stable)
        drift, yaw_drift = validate_stopped_samples(stable)
        blocked = control(container, "map")
        save("source-still-stopped-map.json", blocked)
        require(blocked["ready"] is False and any(
            name in blocked.get("readinessBlockers", []) for name in ("BASE_RGBD_STALE", "HEAD_RGBD_STALE")),
            "stopped acquisition bridge did not produce genuine RGB-D staleness")
        save("failure-summary.json", {"taskId": task_id, "stepId": step_id,
                                       "failureObservation": frozen, "postFailureDriftM": drift,
                                       "postFailureYawDriftRad": yaw_drift})
    except BaseException:
        if restore_needed:
            # A monitoring failure must not turn SIGCONT into permission to
            # continue the interrupted task. Cancel only the explicitly supplied
            # task when a terminal failure could not be established.
            try:
                current = api(task_path)
                if current["state"] not in TERMINAL:
                    save("monitor-error-cancel.json", api(task_path + "/cancel", {}))
            except (OSError, ValueError, KeyError, TypeError) as error:
                save("monitor-error-cancel-failed.json", {"error": str(error)})
        raise
    finally:
        if restore_needed:
            try:
                restored = control(container, "continue", bridge)
                save("source-restored.json", {"containerId": container, **restored})
            except Exception as error:
                save("restore-error.json", {"error": str(error), "containerId": container, **bridge})
                raise

    end = time.monotonic() + 15
    while time.monotonic() < end:
        recovered = control(container, "map")
        if recovered.get("ready"):
            save("recovered-map.json", recovered)
            break
        time.sleep(.2)
    else:
        raise TimeoutError("bridge resumed but real localization did not recover within 15 seconds")
    after = api(task_path + "/observations/" + failed_record["id"])
    require(all(after[key] == failed_record[key] for key in (
        "captureId", "stepId", "snapshot", "snapshotSha256", "rgbSha256", "depthSha256")),
        "historical failure evidence changed after source recovery")
    unchanged = api(task_path)
    require(unchanged["state"] == task["state"], "source recovery implicitly resumed the failed task")
    require(not navigation_activity(unchanged), "a new navigation command appeared after recovery")
    require(no_manipulation_after(unchanged, active["sequence"]),
            "pick or place was dispatched after the interrupted navigation")
    stable_after = [camera_sample(api, source_id)]
    for _ in range(10):
        time.sleep(.1)
        stable_after.append(camera_sample(api, source_id))
    save("recovered-base-poses.json", stable_after)
    resumed_drift, resumed_yaw = validate_stopped_samples(stable_after)
    save("recovered-task.json", unchanged)
    save("summary.json", {"passed": True, "taskId": task_id, "sourceRestored": True,
                          "failureEvidenceUnchanged": True, "postFailureDriftM": drift,
                          "postFailureYawDriftRad": yaw_drift,
                          "postRecoveryDriftM": resumed_drift, "postRecoveryYawDriftRad": resumed_yaw,
                          "faultScope": "ROS acquisition bridge: dual RGB-D and its odometry stream"})


def main():
    if sys.argv[1:2] == ["--inside-container"]:
        operation = sys.argv[2]
        result = (bridge_identity() if operation == "inspect" else map_diagnostics()
                  if operation == "map" else signal_bridge(operation, json.loads(sys.argv[3])))
        print(json.dumps(result))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--container", default="tangying-navigation-navigation-1")
    args = parser.parse_args()
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    run(args.base_url, args.task_id, args.output, args.container)


if __name__ == "__main__":
    main()
