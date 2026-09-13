"""Run explicitly selected natural-language household acceptance scenarios.

Every scenario uses the local Agent task/approval/evidence API. The runner never
resets a world, teleports a robot, or retries an uncertain physical task. Select
only one mug-transfer per fresh scene: the compact tray accommodates one mug.
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
TRANSFER_STEPS = ["observe", "navigate_01", "verify_arrival_01", "observe_after_navigation",
                  "resolve", "plan_grasp", "pick", "verify_grasp", "place", "verify_place",
                  "navigate_02", "verify_arrival_02"]


def route_steps(count):
    return ["observe", *(step for index in range(count)
                         for step in (f"navigate_{index:02}", f"verify_arrival_{index:02}"))]


SCENARIOS = {
    "patrol": [("巡检卧室和卫生间，最后回到客厅", route_steps(3))],
    "inspect-kitchen": [
        ("从客厅出发，去厨房确认一下环境", route_steps(2)),
        # The initial observe command of this second task pins a head RGB-D
        # inventory while the robot is at the kitchen, then returns it home.
        ("从厨房出发，回到客厅", route_steps(2)),
    ],
    "mug-transfer": [
        ("从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅", TRANSFER_STEPS),
    ],
}


def local_base(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("an explicit local HTTP Agent origin is required")
    return value.rstrip("/")


def run(base: str, output: Path, scenarios: list[str], timeout: float = 180) -> dict:
    base = local_base(base)
    if not scenarios or len(set(scenarios)) != len(scenarios) or set(scenarios)-SCENARIOS.keys():
        raise ValueError("select distinct declared scenarios explicitly")
    if not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise ValueError("timeout must be finite and between 0 and 600 seconds")
    output.mkdir(parents=True, exist_ok=False)

    def api(path, body=None):
        request = Request(base+path, data=None if body is None else json.dumps(body).encode(),
                          method="GET" if body is None else "POST",
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=20) as response:
            return json.load(response)

    def save(directory, name, payload):
        (directory/name).write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n")

    summary = {"passed": False, "selectedScenarios": scenarios, "results": [],
               "objectContract": {"ceramic-mug": "cup", "kitchen-tray": "storage_bin"},
               "limitations": ["commissioned RGB-D shape detector, not a trained general recognizer",
                               "dinnerware and vase meshes are decorative; no grasp claim"],
               "physicalRetries": 0, "worldResets": 0}
    save(output, "summary.json", summary)
    try:
        telemetry = api("/v1/telemetry?adapter=mujoco&limit=1")
        if not telemetry.get("hasLatest"):
            raise AssertionError("Agent has no MuJoCo telemetry")
        save(output, "initial-telemetry.json", telemetry["latest"])
        state = telemetry["latest"]["robotState"]
        active_map = state.get("active_map") or {}
        map_keys = ("mapId", "mapRevision", "calibrationRevision")
        if any(not isinstance(active_map.get(key), str) or not active_map[key] for key in map_keys):
            raise AssertionError("suite requires an activated saved map from the completed SLAM workflow")
        summary["activeMap"] = {key: active_map[key] for key in map_keys}
        if (state.get("perception", {}).get("detector") != "rgbd-household-metric-shape-v1"
                or state.get("navigation", {}).get("scene") != "home_task"
                or state.get("perception", {}).get("ground_truth_fallback") is not False):
            raise AssertionError("suite requires the furnished household RGB-D geometry scene")

        # The action catalogue is used while the mug is outside the camera's
        # current view. Check it before a patrol can hide a stale legacy fixture
        # registration until the final manipulation scenario.
        catalogue = state.get("semantic_objects")
        expected = {"ceramic-mug": "cup", "kitchen-tray": "storage_bin"}
        action_objects = ([item for item in catalogue if isinstance(item, dict)
                           and item.get("category") in expected.values()]
                          if isinstance(catalogue, list) else [])
        if (len(action_objects) != len(expected)
                or {item.get("id") for item in action_objects} != set(expected)
                or any(item.get("category") != expected[item["id"]]
                       or item.get("workArea") != "kitchen" or item.get("attributes") != {}
                       for item in action_objects)):
            raise AssertionError("furnished action catalogue must uniquely register the mug and tray in the kitchen")
        summary["registeredActionObjects"] = action_objects

        for scenario in scenarios:
            scenario_result = {"scenario": scenario, "passed": False, "tasks": []}
            summary["results"].append(scenario_result)
            for task_index, (request, expected_steps) in enumerate(SCENARIOS[scenario]):
                directory = output/f"{scenario}-{task_index+1}"
                directory.mkdir()
                task = api("/v1/tasks", {"adapter": "mujoco", "request": request})
                task_path = "/v1/tasks/"+task["id"]
                save(directory, "created-task.json", task)
                task_result = {"taskId": task["id"], "request": request, "passed": False}
                scenario_result["tasks"].append(task_result)
                try:
                    api(task_path+"/approve", {})
                    deadline = time.monotonic()+timeout
                    while time.monotonic() < deadline:
                        task = api(task_path)
                        save(directory, "final-task.json", task)
                        if task["state"] in TERMINAL:
                            break
                        time.sleep(.2)
                    else:
                        raise TimeoutError("task did not reach a terminal state; no physical retry")
                    index = api(task_path+"/observations?limit=200")
                    save(directory, "observation-index.json", index)
                    details = {}
                    for record in index.get("records", []):
                        detail = api(task_path+"/observations/"+record["id"])
                        save(directory, record["id"]+".json", detail)
                        details[(detail["stepId"], detail["captureId"])] = detail
                        for kind in ("rgb", "depth"):
                            with urlopen(base+task_path+"/observations/"+record["id"]+"/"+kind,
                                         timeout=20) as response:
                                raw = response.read()
                            if hashlib.sha256(raw).hexdigest() != record[kind+"Sha256"]:
                                raise AssertionError(f"{record['id']} {kind} SHA-256 mismatch")
                            (directory/(record["id"]+"-"+kind+".png")).write_bytes(raw)
                    confirmed = [event for event in task.get("events", [])
                                 if event.get("type") == "TOOL_ACTIVITY"
                                 and event["payload"].get("activityStatus") == "CONFIRMED"]
                    step_ids = [event["stepId"] for event in confirmed]
                    task_result.update(state=task["state"], confirmedSteps=step_ids,
                                       verifiedImageCount=len(index.get("records", []))*2)
                    if task["state"] != "SUCCEEDED" or step_ids != expected_steps:
                        raise AssertionError(f"{task['state']}: unexpected confirmed steps {step_ids}")
                    map_navigation_steps = []
                    for event in confirmed:
                        payload = event["payload"]
                        detail = details.get((event["stepId"], payload.get("receiptObservationId")))
                        if payload.get("evidenceSource") != "command_observation" or detail is None:
                            raise AssertionError(f"{event['stepId']} lacks pinned command RGB-D evidence")
                        map_route = detail["snapshot"].get("robotState", {}).get("map_route")
                        if event["stepId"].startswith("navigate_"):
                            if not isinstance(map_route, dict):
                                raise AssertionError(f"{event['stepId']} lacks an activated-map navigation receipt")
                            if any(map_route.get(key) != active_map[key] for key in map_keys):
                                raise AssertionError("navigation receipt refers to a different map or calibration")
                            map_navigation_steps.append(event["stepId"])
                        if scenario == "inspect-kitchen" and task_index == 1 and event["stepId"] == "observe":
                            observed = {entity["entityId"] for entity in detail["snapshot"].get("entities", [])}
                            if not {"ceramic-mug", "kitchen-tray"}.issubset(observed):
                                raise AssertionError("kitchen head RGB-D did not observe the commissioned mug and tray")
                            task_result["observedObjects"] = sorted(observed)
                        if event["stepId"] == "verify_place":
                            verification = detail["snapshot"]["robotState"]["verification"]
                            if (not verification.get("passed")
                                    or verification.get("observed_relation") != "inside:kitchen-tray"
                                    or verification.get("object_id") != "ceramic-mug"
                                    or verification.get("sample_count") != 3
                                    or verification.get("stable_duration_s", 0) < .1):
                                raise AssertionError("placement lacks three stable geometric RGB-D samples")
                            task_result["placementVerification"] = verification
                    if not map_navigation_steps:
                        raise AssertionError("task lacks a confirmed navigation receipt using the activated map")
                    task_result["mapNavigationSteps"] = map_navigation_steps
                    task_result["passed"] = True
                except BaseException as error:
                    task_result["error"] = f"{type(error).__name__}: {error}"
                    try:
                        current = api(task_path)
                        if current["state"] not in TERMINAL:
                            api(task_path+"/cancel", {})
                            current = api(task_path)
                        save(directory, "final-task.json", current)
                    except Exception as cleanup_error:  # noqa: BLE001
                        task_result["cleanupError"] = str(cleanup_error)
                    raise
            scenario_result["passed"] = True
        summary["passed"] = True
    except BaseException as error:
        summary["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        save(output, "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scenario", required=True, action="append", choices=SCENARIOS)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    print(json.dumps(run(args.base_url, args.output, args.scenario, args.timeout),
                     ensure_ascii=False, indent=2))
