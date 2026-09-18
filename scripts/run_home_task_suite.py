"""Run explicitly selected natural-language household acceptance scenarios.

Every scenario uses the local Agent task/approval/evidence API. The runner never
resets a world, teleports a robot, or retries an uncertain physical task. Select
only one mug-transfer per fresh scene: the compact tray accommodates one mug.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import math
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

# The console requires a session on every mutating route. A browser gets it as a
# cookie; a script reads the file the agent writes at startup. See
# scripts/console_session.py for where it looks.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from console_session import headers as session_headers, resolve_token  # noqa: E402


TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERABLE_FAILURE", "SAFETY_STOPPED"}

# What a scenario must *do*, expressed as the tools it invoked and how many times
# the robot drove to a new place — never as the names of its steps.
#
# The suite used to assert `step_ids == ["observe", "pre_position", "navigate_00",
# ...]`. That holds for the deterministic planner and nothing else: on a stack with
# a model configured, the same patrol request came back as `observe-start`,
# `navigate-bedroom`, `verify-bedroom`, … and the suite failed a task that was doing
# exactly the right thing. It is the coupling docs/architecture/orchestration-post-training.md
# warns about in its own words — "用例断言的是机器人去了厨房，不是计划里有 navigate_route"
# — and the eval design was taught that by a real failure.
#
# A planner is free to name and order its steps; it is not free to skip the tools
# that make the outcome true. That is what these lists pin.
BOTH_WAYS = 2

NAVIGATION_TOOLS = ("navigation.navigate", "verify_arrival")

# Reaching the rooms is the whole of a patrol: nothing is manipulated, and the
# receipt check below is what proves the robot actually drove.
PATROL_TOOLS = NAVIGATION_TOOLS
PATROL_STOPS = 3

# A household transfer: find the mug in another room, pick it up, confirm the
# grasp, put it in the tray, confirm the placement, then go back.
TRANSFER_TOOLS = (*NAVIGATION_TOOLS, "observe_scene", "resolve_targets", "plan_grasp",
                  "manipulation.pick", "verify_grasp", "manipulation.place", "verify_placement")

# The first task of the kitchen inspection pins a head RGB-D inventory while the
# robot is in the kitchen and then returns home.
KITCHEN_TOOLS = (*NAVIGATION_TOOLS, "observe_scene")


def scenario_task(request, tools, *, stops, inventory=False, transfer=False):
    return {"request": request, "tools": tools, "stops": stops,
            "inventory": inventory, "transfer": transfer}


SCENARIOS = {
    "patrol": [scenario_task("巡检卧室和卫生间，最后回到客厅", PATROL_TOOLS, stops=PATROL_STOPS)],
    "inspect-kitchen": [
        scenario_task("从客厅出发，去厨房确认一下环境", KITCHEN_TOOLS, stops=BOTH_WAYS),
        scenario_task("从厨房出发，回到客厅", KITCHEN_TOOLS, stops=BOTH_WAYS, inventory=True),
    ],
    "mug-transfer": [
        scenario_task("从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅",
                      TRANSFER_TOOLS, stops=BOTH_WAYS, transfer=True),
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

    session_token = resolve_token(base_url=base)

    def api(path, body=None):
        request = Request(base+path, data=None if body is None else json.dumps(body).encode(),
                          method="GET" if body is None else "POST",
                          headers=session_headers(session_token, {"Content-Type": "application/json"}))
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
            for task_index, plan in enumerate(SCENARIOS[scenario]):
                request = plan["request"]
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
                    tools_run = [event["payload"].get("toolName", "") for event in confirmed]
                    task_result.update(state=task["state"], confirmedSteps=[event["stepId"] for event in confirmed],
                                       confirmedTools=tools_run,
                                       verifiedImageCount=len(index.get("records", []))*2)
                    # The outcome, not the plan: the task finished, and every
                    # step it took is backed by a capture taken after its command.
                    if task["state"] != "SUCCEEDED":
                        raise AssertionError(f"{task['state']}: the task did not finish")
                    missing = [tool for tool in plan["tools"] if tool not in tools_run]
                    if missing:
                        raise AssertionError(
                            f"the task finished without invoking {missing}; "
                            f"it ran {sorted(set(tools_run))}")
                    map_navigation_steps = []
                    inventory_seen = set()
                    for event in confirmed:
                        payload = event["payload"]
                        detail = details.get((event["stepId"], payload.get("receiptObservationId")))
                        if payload.get("evidenceSource") != "command_observation" or detail is None:
                            raise AssertionError(f"{event['stepId']} lacks pinned command RGB-D evidence")
                        snapshot = detail["snapshot"]
                        tool = payload.get("toolName", "")
                        if tool == "navigation.navigate":
                            map_route = snapshot.get("robotState", {}).get("map_route")
                            if not isinstance(map_route, dict):
                                raise AssertionError(f"{event['stepId']} lacks an activated-map navigation receipt")
                            if any(map_route.get(key) != active_map[key] for key in map_keys):
                                raise AssertionError("navigation receipt refers to a different map or calibration")
                            map_navigation_steps.append(event["stepId"])
                        if plan["inventory"] and tool == "observe_scene":
                            inventory_seen |= {entity["entityId"] for entity in snapshot.get("entities", [])}
                        if plan["transfer"] and tool == "verify_placement":
                            verification = snapshot["robotState"]["verification"]
                            if (not verification.get("passed")
                                    or verification.get("observed_relation") != "inside:kitchen-tray"
                                    or verification.get("object_id") != "ceramic-mug"
                                    or verification.get("sample_count") != 3
                                    or verification.get("stable_duration_s", 0) < .1):
                                raise AssertionError("placement lacks three stable geometric RGB-D samples")
                            task_result["placementVerification"] = verification
                    # How far the robot actually went, which no step name can tell us.
                    if len(map_navigation_steps) < plan["stops"]:
                        raise AssertionError(
                            f"the robot drove {len(map_navigation_steps)} legs on the activated map, "
                            f"expected at least {plan['stops']}")
                    if plan["inventory"]:
                        if not {"ceramic-mug", "kitchen-tray"}.issubset(inventory_seen):
                            raise AssertionError("kitchen head RGB-D did not observe the commissioned mug and tray")
                        task_result["observedObjects"] = sorted(inventory_seen)
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
    parser.add_argument("--session-token", default=None,
                        help="console session token; default: $TANGYING_CONSOLE_SESSION, then the file the agent wrote")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    if args.session_token:
        os.environ["TANGYING_CONSOLE_SESSION"] = args.session_token
    print(json.dumps(run(args.base_url, args.output, args.scenario, args.timeout),
                     ensure_ascii=False, indent=2))
