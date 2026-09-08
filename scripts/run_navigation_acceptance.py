"""Exercise the running local MuJoCo/ROS stack and retain original task evidence.

Start `make navigation-start NAVIGATION_ARGS='--build --mode mapping'` first.
This explicitly creates and approves one simulation task; it never resets the
world, deletes journals, or retries an uncertain physical command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERABLE_FAILURE", "SAFETY_STOPPED"}
REQUEST = "把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来"


def pose_errors(actual, goal, *, planar=False):
    for pose in (actual, goal):
        assert isinstance(pose, list) and len(pose) == 7, "navigation requires XYZ+wxyz poses"
        assert all(type(value) in (int, float) and math.isfinite(value) for value in pose), "navigation pose must be finite"
        assert abs(sum(value * value for value in pose[3:]) - 1) < .001, "navigation quaternion must be normalized"

    def yaw(pose):
        w, x, y, z = pose[3:]
        return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))

    delta = yaw(actual) - yaw(goal)
    return math.dist(actual[:2 if planar else 3], goal[:2 if planar else 3]), abs(math.atan2(math.sin(delta), math.cos(delta)))


def validate_navigation(record, expected_goal, expected_source):
    navigation = record["snapshot"]["robotState"]["navigation"]
    receipt = navigation["map_receipt"]
    assert receipt.get("completion_source") == expected_source, (
        "first navigation must execute Nav2" if expected_source == "nav2_action"
        else "second navigation must confirm the existing pose without another motion")
    assert navigation["passed"] is True
    assert navigation["pose_source"] == "sim_proprioceptive_odom", "navigation requires independent odometry"
    assert navigation["observed_at_unix_ms"] == record["observedAtUnixMs"], "independent capture time mismatch"
    target_distance, target_yaw = pose_errors(navigation["goal_pose"], expected_goal)
    assert target_distance < 1e-8 and target_yaw < 1e-8, "navigation changed the commissioned goal"
    distance, angle = pose_errors(navigation["base_pose"], navigation["goal_pose"])
    assert distance <= .015 and 0 <= navigation["position_error_m"] <= .015 and math.isclose(
        distance, navigation["position_error_m"], abs_tol=1e-8), "independent position is outside tolerance or inconsistent"
    assert angle <= .04 and 0 <= navigation["yaw_error_rad"] <= .04 and math.isclose(
        angle, navigation["yaw_error_rad"], abs_tol=1e-8), "independent yaw is outside tolerance or inconsistent"
    assert receipt["pose_source"] == "rtabmap_tf", "navigation requires an RTAB-Map pose source"
    checked = receipt["checked_at_unix_ms"]
    stamp = receipt["pose_observed_at_unix_ms"]
    completed_stamp = receipt["completion_pose_observed_at_unix_ms"]
    assert type(checked) is int and checked > 0, "receipt check time is invalid"
    assert type(stamp) is int and stamp > 0 and 0 <= checked - stamp <= 1000, "localization receipt is stale or future dated"
    assert type(completed_stamp) is int and completed_stamp > 0 and 0 <= checked - completed_stamp <= 1000, "completion localization is stale or future dated"
    assert abs(checked - record["observedAtUnixMs"]) <= 1000, "independent capture and map receipt are not contemporaneous"
    map_distance, map_angle = pose_errors(receipt["map_pose"], receipt["goal_pose_map"], planar=True)
    assert map_distance <= .015 and 0 <= receipt["position_error_m"] <= .015 and math.isclose(
        map_distance, receipt["position_error_m"], abs_tol=1e-8), "map position is outside tolerance or inconsistent"
    assert map_angle <= .04 and 0 <= receipt["yaw_error_rad"] <= .04 and math.isclose(
        map_angle, receipt["yaw_error_rad"], abs_tol=1e-8), "map yaw is outside tolerance or inconsistent"
    assert isinstance(receipt["goal_id"], str) and receipt["goal_id"], "navigation goal identity is missing"
    assert isinstance(receipt["map_revision"], str) and receipt["map_revision"], "navigation map revision is missing"
    return navigation


def run(base: str, output: Path, pause_seconds: float = 0):
    origin = urlsplit(base)
    if (origin.scheme != "http" or origin.hostname not in {"127.0.0.1", "localhost"}
            or origin.username or origin.password or origin.path not in {"", "/"}
            or origin.query or origin.fragment):
        raise ValueError("acceptance requires a local HTTP simulation origin")
    if not math.isfinite(pause_seconds) or not 0 <= pause_seconds <= 90:
        raise ValueError("pause must be between 0 and 90 seconds")
    output.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def api(path, body=None):
        req = Request(base.rstrip("/") + path, headers={"Content-Type": "application/json"},
                      data=None if body is None else json.dumps(body).encode())
        with urlopen(req, timeout=15) as response:
            return json.load(response)

    initial = api("/v1/telemetry?adapter=mujoco&limit=1")["latest"]
    save("initial-observation.json", initial)
    profile = initial["robotProfile"]
    state = initial["robotState"]
    assert profile["adapterId"] == "mujoco" and profile["modelId"] == "xlerobot-rgbd-reference"
    assert state["perception"]["mode"] == "rgbd"
    assert state["navigation"]["backend"] == "rtabmap_nav2"
    start = state["base_pose"]
    goal = state["navigation"]["approach_goal_pose"]
    assert math.dist(start[:2], goal[:2]) >= .60, "restart simulation at the commissioned far start first"
    nav = api("/v1/navigation/map")
    save("initial-map.json", nav)
    assert nav["ready"] and nav["poseSource"] == "rtabmap_tf", "wait for genuine map readiness first"
    task = api("/v1/tasks", {"adapter": "mujoco", "request": REQUEST})
    task_path = "/v1/tasks/" + task["id"]
    def collect_evidence(*, best_effort=False):
        index = api(task_path + "/observations?limit=200")
        save("observation-index.json", index)
        records, details, errors = index["records"], {}, []
        aliases = set()

        def retain(operation, callback):
            try:
                return callback()
            except Exception as error:
                if not best_effort:
                    raise
                errors.append({"operation": operation, "error": str(error)})
                return None

        for record in records:
            def capture_detail(record=record):
                detail = api(task_path + "/observations/" + record["id"])
                save(record["id"] + ".json", detail)
                details[record["stepId"], record["captureId"]] = detail
            retain(record["id"], capture_detail)
            for kind in ("rgb", "depth"):
                def capture_image(record=record, kind=kind):
                    with urlopen(base.rstrip("/") + task_path + "/observations/" + record["id"] + "/" + kind,
                                 timeout=10) as response:
                        raw = response.read()
                    # Keep original bytes even when a corrupt hash fails acceptance.
                    (output / (record["id"] + "-" + kind + ".png")).write_bytes(raw)
                    assert hashlib.sha256(raw).hexdigest() == record[kind + "Sha256"], record["id"] + "/" + kind + " hash mismatch"
                    alias = (record["stepId"], kind)
                    if (record.get("stepId", "").endswith(("navigate", "verify_grasp", "verify_place"))
                            and alias not in aliases):
                        # The server lists newest first; stage shortcuts must not
                        # overwrite the latest capture with a pre-resume image.
                        (output / (record["stepId"] + "-" + kind + ".png")).write_bytes(raw)
                        aliases.add(alias)
                retain(record["id"] + "/" + kind, capture_image)
        return records, details, errors

    evidence_collected = False
    try:
        save("created-task.json", task)
        print(task["id"], flush=True)
        api(task_path + "/approve", {})
        pause_requested = resumed = False
        pause_started = None
        last_status = None
        deadline = time.monotonic() + 210 + pause_seconds
        while time.monotonic() < deadline:
            task = api(task_path)
            activities = [e for e in task.get("events", []) if e["type"] == "TOOL_ACTIVITY"]
            latest = activities[-1] if activities else {}
            status = (task["state"], latest.get("stepId"), latest.get("payload", {}).get("activityStatus"))
            if status != last_status:
                print(status, flush=True)
                last_status = status
            save("final-task.json", task)
            if task["state"] in TERMINAL:
                break
            if (pause_seconds and not pause_requested and any(
                    e.get("stepId") == "task01-navigate" and e["payload"].get("activityStatus") == "RUNNING"
                    for e in activities)):
                api(task_path + "/pause", {})
                pause_requested = True
            if task["state"] == "PAUSED" and pause_started is None:
                pause_started = time.monotonic()
                save("paused-task.json", task)
                save("paused-recovery.json", api(task_path + "/recovery"))
            if pause_started is not None and not resumed and time.monotonic() - pause_started >= pause_seconds:
                api(task_path + "/resume", {})
                resumed = True
            time.sleep(.2)
        else:
            raise TimeoutError("task timed out; stopping this acceptance task and retaining evidence")

        records, details, _ = collect_evidence()
        evidence_collected = True
        assert task["state"] == "SUCCEEDED", task.get("events", [])[-1:]
        confirmed = [e for e in activities if e["payload"].get("activityStatus") == "CONFIRMED"]
        assert len({e["stepId"] for e in confirmed}) == 18
        physical = [e for e in confirmed if e["payload"]["toolName"] in {
            "navigation.navigate", "manipulation.pick", "manipulation.place"}]
        assert len(physical) == len({e["stepId"] for e in physical}) == 6
        assert {e["stepId"]: e["payload"]["toolName"] for e in physical} == {
            prefix + "-" + stage: tool for prefix in ("task01", "task02") for stage, tool in (
                ("navigate", "navigation.navigate"), ("pick", "manipulation.pick"), ("place", "manipulation.place"))
        }, "physical tool classifications do not match the two-subtask plan"
        for event in confirmed:
            payload = event["payload"]
            assert payload.get("evidenceSource") == "command_observation"
            assert payload.get("evidenceIds") == [payload["receiptObservationId"]]
            detail = details[event["stepId"], payload["receiptObservationId"]]
            assert detail["stepId"] == event["stepId"]

        def evidence(step):
            # Safe resume deliberately refreshes completed read-only observations.
            # Keep every original event, and validate the most recent stage evidence.
            event = next(e for e in reversed(confirmed) if e["stepId"] == step)
            return details[step, event["payload"]["receiptObservationId"]]

        navigation_calls = []
        for prefix, source in (("task01", "nav2_action"), ("task02", "pose_confirmation")):
            record = evidence(prefix + "-navigate")
            result = validate_navigation(record, goal, source)
            assert evidence(prefix + "-observe_after_navigation")["observedAtUnixMs"] > record["observedAtUnixMs"]
            navigation_calls.append({"stepId": prefix + "-navigate", "completionSource": source,
                                     "motionExecuted": source == "nav2_action", "verification": result})
        navigation = navigation_calls[0]["verification"]
        second_navigation = navigation_calls[1]["verification"]
        assert second_navigation["map_receipt"]["goal_id"] != navigation["map_receipt"]["goal_id"], "navigation calls reused a goal receipt"
        assert second_navigation["map_receipt"]["checked_at_unix_ms"] > navigation["map_receipt"]["checked_at_unix_ms"], "second navigation reused an old receipt check"
        displacement = math.dist(start[:2], navigation["base_pose"][:2])
        assert displacement >= .60
        placements = []
        for prefix in ("task01", "task02"):
            record = evidence(prefix + "-verify_place")
            check = record["snapshot"]["robotState"]["verification"]
            assert check["passed"] and check["sample_count"] == 3
            assert check["stable_duration_s"] >= .1 and check["max_displacement_m"] <= .008
            assert check["observation_id"] == record["captureId"]
            placements.append(check)
        final = evidence("task02-verify_place")["snapshot"]
        relations = {e["entityId"]: e.get("relation") for e in final["reconstruction"]["entities"]}
        assert relations["red-cup"] == "inside:right-bin"
        assert relations["blue-bottle"] == "inside:front-tray"
        if pause_seconds:
            assert resumed and pause_started is not None
        summary = {"passed": True, "taskId": task["id"], "request": REQUEST, "mode": nav["mode"],
                   "startPose": start, "actualDisplacementM": displacement, "navigation": navigation,
                   "navigationCalls": navigation_calls, "physicalToolCalls": len(physical),
                   "motionToolSteps": [e["stepId"] for e in physical if e["stepId"] != "task02-navigate"],
                   "poseConfirmationSteps": ["task02-navigate"],
                   "confirmedSteps": len({e["stepId"] for e in confirmed}),
                   "confirmedEvents": len(confirmed), "historicalFrames": len(records),
                   "verifiedImageHashes": 2 * len(records),
                   "placements": placements, "finalRelations": relations, "pauseSeconds": pause_seconds,
                   "physicalHardwareTested": False}
        save("summary.json", summary)
        print(json.dumps({k: v for k, v in summary.items() if k not in {"navigation", "navigationCalls", "placements"}},
                         ensure_ascii=False, indent=2))

    except BaseException as error:
        # The approve/pause/resume response can be lost after the server acted.
        # Never repeat those mutations or create a replacement task. Cancel only
        # our known task, and keep the original exception even if cleanup fails.
        failure = {"passed": False, "taskId": task["id"], "errorType": type(error).__name__,
                   "message": str(error), "cleanupErrors": [], "cancellationRequested": False}

        def cleanup(operation, callback):
            try:
                return callback()
            except Exception as cleanup_error:  # noqa: BLE001 - cleanup must preserve the original failure
                failure["cleanupErrors"].append({"operation": operation, "error": str(cleanup_error)})
                return None

        cleanup("save failure", lambda: save("failure.json", failure))
        latest = cleanup("read task before cleanup", lambda: api(task_path))
        if latest is not None:
            task = latest
        if task.get("state") not in TERMINAL:
            failure["cancellationRequested"] = True
            cleanup("cancel task", lambda: api(task_path + "/cancel", {}))
            latest = cleanup("read task after cancellation", lambda: api(task_path))
            if latest is not None:
                task = latest
        cleanup("save final task", lambda: save("final-task.json", task))
        if not evidence_collected:
            retained = cleanup("collect observations", lambda: collect_evidence(best_effort=True))
            if retained is not None:
                failure["cleanupErrors"].extend(retained[2])
        cleanup("save failure", lambda: save("failure.json", failure))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pause-seconds", type=float, default=0)
    args = parser.parse_args()
    run(args.base_url, args.output, args.pause_seconds)
