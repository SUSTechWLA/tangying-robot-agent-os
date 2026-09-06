"""Exercise natural-language tasks through an isolated real RoboCasa process stack.

No physical adapter is used. Each executable case starts a fresh simulator episode;
incorrectly interpreted requests are cancelled before approval. Reports contain
public task/world evidence, never operator tokens or private keys.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib import error, request

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tests.e2e.fleet_harness import _wait_port
from tests.e2e.robocasa_harness import start_robocasa_handoff_stack


def step(robot, destination, *, source="", color="red", relation=""):
    return {"robot": robot, "object": "block", "color": color, "destination": destination, "relation": relation, "source": source}


HANDOFF = [step("robot-1", "handoff_zone"), step("robot-2", "target_zone", source="handoff_zone", relation="right_side")]
CASES = [
    {"id": "canonical", "request": "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区", "steps": HANDOFF, "inside": "right-target-zone"},
    {"id": "polite", "request": "请帮我让一号机器人把红色方块放到交接区。", "steps": [step("robot-1", "handoff_zone")], "inside": "handoff-zone"},
    {"id": "colloquial", "request": "麻烦 1 号机器人将红色积木移到交接点。", "steps": [step("robot-1", "handoff_zone")], "inside": "handoff-zone"},
    {"id": "pronoun", "request": "先让一号机器人把红色方块放到交接区，然后让二号机器人把它放到右侧目标区。", "steps": HANDOFF, "inside": "right-target-zone"},
    {"id": "english", "request": "Robot 1, please move the red block to the handoff zone.", "steps": [step("robot-1", "handoff_zone")], "inside": "handoff-zone"},
    {"id": "negation_zh", "request": "把红色方块放到交接区是不允许的", "reject": True},
    {"id": "negation_en", "request": "Do not put the red block into the right bin", "reject": True},
    {"id": "conditional", "request": "If the person leaves, put the red block into the right bin", "reject": True},
    {"id": "multiple_objects", "request": "把红色和蓝色方块放到交接区", "reject": True},
    {"id": "unknown_object", "request": "让1号机器人把蓝色方块放到交接区", "steps": [step("robot-1", "handoff_zone", color="blue")], "executionReject": True},
    {"id": "unknown_destination", "request": "让1号机器人把红色方块放进冰箱", "reject": True},
    {"id": "unsupported", "request": "帮我做晚饭", "reject": True},
    {"id": "source_mismatch", "request": "让1号机器人把红色方块从右侧目标区放到交接区", "steps": [step("robot-1", "handoff_zone", source="target_zone")], "executionReject": True},
]


def summarize_intent(intent):
    return [{"robot": item.get("robotId", ""), "object": item.get("object", {}).get("category", ""),
             "color": item.get("object", {}).get("attributes", {}).get("color", ""),
             "destination": item.get("destination", {}).get("category", ""),
             "relation": item.get("destination", {}).get("relation", ""),
             "source": item.get("source", {}).get("category", "")}
            for item in intent.get("sequence") or [intent]]


def reset_episode(stack):
    before = stack.api("/v1/world")
    old_observation = before.get("entities", {}).get("red-block", {}).get("evidence", {}).get("observationId")
    for name in ["edge-robot-2", "edge-robot-1", "robocasa-runtime"]:
        stack.stop_process(name)
    stack.start_process("robocasa-runtime", stack.runtime_command, stack.runtime_environment)
    _wait_port(stack.sim1_port, timeout=60)
    _wait_port(stack.sim2_port, timeout=60)
    for robot in ["robot-1", "robot-2"]:
        stack.start_process(f"edge-{robot}", [str(stack.tmp / "bin/edge-worker")], stack.edge_environments[robot])
    stack.wait_ready()
    stack.wait_world(lambda world: world.get("entities", {}).get("red-block", {}).get("relations", {}).get("inside") == "left-start-zone"
                     and world["entities"]["red-block"].get("freshness") == "FRESH"
                     and world["revision"] > before["revision"]
                     and world["entities"]["red-block"].get("evidence", {}).get("observationId") != old_observation)


def evaluate(stack, case):
    result = {"id": case["id"], "request": case["request"], "expected": case, "passed": False}
    call = request.Request(stack.fleet_url + "/v1/tasks", data=json.dumps({"request": case["request"], "adapter": "robocasa"}).encode(),
                           headers={"Content-Type": "application/json", "Authorization": "Bearer " + stack.operator_token})
    try:
        with request.urlopen(call, timeout=25) as response:
            task = json.load(response)
            result["httpStatus"] = response.status
    except error.HTTPError as exc:
        result.update(httpStatus=exc.code, response=json.loads(exc.read()), passed=bool(case.get("reject") and exc.code == 422))
        return result
    result.update(taskId=task["id"], parsed=summarize_intent(task["intent"]))
    if case.get("reject") or result["parsed"] != case["steps"]:
        stack.api(f"/v1/tasks/{task['id']}/cancel", method="POST")
        result["reason"] = "Incorrect interpretation; cancelled before approval"
        return result
    reset_episode(stack)
    initial = stack.api("/v1/world")
    start = time.monotonic()
    stack.api(f"/v1/tasks/{task['id']}/approve", method="POST")
    final = stack.wait_task(task["id"], timeout=50)
    world = stack.api("/v1/world")
    nodes = stack.api(f"/v1/tasks/{task['id']}/intents")["intents"]
    result.update(state=final["state"], durationSeconds=round(time.monotonic()-start, 2), initialRevision=initial["revision"],
                  finalWorld=world, intents=nodes, task=final, experience=stack.experience(task["id"]))
    block = world.get("entities", {}).get("red-block", {})
    if case.get("executionReject"):
        result["passed"] = final["state"] in {"FAILED", "FAILED_SAFE", "REJECTED_SAFE", "BLOCKED"} and block.get("relations", {}).get("inside") == "left-start-zone"
    else:
        result["passed"] = final["state"] == "SUCCEEDED" and block.get("relations", {}).get("inside") == case["inside"] and block.get("freshness") == "FRESH" and world["revision"] > initial["revision"] and len(nodes) == len(case["steps"]) and all(node.get("harnessStatus") == "SATISFIED" and node.get("harnessEvidenceIds") for node in nodes)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New report directory; must not already exist")
    parser.add_argument("--case", action="append", default=[], choices=[case["id"] for case in CASES])
    parser.add_argument("--keep-running", action="store_true", help="After successful checks, leave a fresh loopback simulation ready until Ctrl-C")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.chmod(root, 0o700)
    # This evaluation intentionally measures the deterministic path. It cannot
    # inherit a developer's configured remote model or robot adapter.
    os.environ["AGENT_PROVIDER"] = "deterministic"
    os.environ.pop("AGENT_API_KEY", None)
    results = []
    stack = None
    try:
        stack = start_robocasa_handoff_stack(root / "runtime")
        metadata = {"adapter": "robocasa", "planner": "deterministic", "baseURL": stack.fleet_url,
                    "binaries": {name: hashlib.sha256((root / "runtime" / "bin" / name).read_bytes()).hexdigest() for name in ["fleet-control-plane", "edge-worker"]}}
        print(f"Ready: {stack.fleet_url}; current checkout, isolated RoboCasa", flush=True)
        for case in CASES:
            if args.case and case["id"] not in args.case:
                continue
            try:
                result = evaluate(stack, case)
            except Exception as exc:  # noqa: BLE001 -- record each failure, finish reporting, then clean up owned processes
                result = {"id": case["id"], "request": case["request"], "passed": False, "error": str(exc)[:1500]}
            results.append(result)
            (root / "results.json").write_text(json.dumps({**metadata, "cases": results}, ensure_ascii=False, indent=2))
            print(json.dumps({key: result[key] for key in ["id", "passed", "httpStatus", "state", "reason", "error", "durationSeconds"] if key in result}, ensure_ascii=False), flush=True)
        passed = all(result["passed"] for result in results)
        if passed and args.keep_running:
            reset_episode(stack)
            (root / "ready.json").write_text(json.dumps({"baseURL": stack.fleet_url, "processes": {name: process.pid for name, process in stack.processes.items()}}))
            print(f"Simulation ready for interactive use: {stack.fleet_url}; Ctrl-C stops this evaluation stack", flush=True)
            while all(process.poll() is None for process in stack.processes.values()):
                time.sleep(1)
            raise RuntimeError("An evaluation process exited; inspect the runtime logs")
        return 0 if passed else 1
    finally:
        if stack:
            stack.stop()


if __name__ == "__main__":
    raise SystemExit(main())
