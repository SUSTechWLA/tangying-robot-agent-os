"""Compare the two destination policies on the live stack, one arm per run.

The semantic layer can now answer "where was this object last seen, and from
which base pose" (`semantic.recall.v1`) and grounding prefers that vantage over
the commissioned work-area waypoint. This script measures whether the preference
actually changes what the robot does.

Both arms run the *same* household task against the *same* map with the same
binary; the only difference is `TANGYING_RECALL_GOAL`, which the local agent
reads at startup. So each arm needs a stack restart, and this script refuses to
pretend otherwise: it runs one arm per invocation and writes a report, and a
separate `--compare` mode joins two reports.

    # arm A (baseline: commissioned waypoint)
    TANGYING_RECALL_GOAL=off bash scripts/furnished-home-demo.sh restart ...
    .venv/bin/python scripts/compare_destination_policy.py --arm commissioned --output artifacts/destination-policy/commissioned

    # arm B (upgraded: remembered vantage)
    bash scripts/furnished-home-demo.sh restart ...
    .venv/bin/python scripts/compare_destination_policy.py --arm recalled --output artifacts/destination-policy/recalled

    .venv/bin/python scripts/compare_destination_policy.py --compare \
        --output artifacts/destination-policy/commissioned.json --baseline artifacts/destination-policy/commissioned/report.json \
        --candidate artifacts/destination-policy/recalled/report.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

REQUEST = "从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅"
TASK_TIMEOUT_S = 300
POLL_S = 2.0
#: Both arms must dispatch from the same, certified-clear base pose. The pose a
#: survey leaves behind is not usable as a start (you never observe the floor you
#: are standing on), so the operator's bounded nudge moves the base to a pose the
#: router has confirmed: the corridor, which both surveys left fully known.
PRE_POSITION = "从客厅出发，去厨房确认一下环境"


def api(base: str, path: str, body=None, timeout: float = 30) -> dict:
    request = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode() or "{}")


def wait_for_task(base: str, task_id: str, deadline: float) -> dict:
    task = {}
    while time.monotonic() < deadline:
        task = api(base, "/v1/tasks/" + task_id)
        state = str(task.get("state") or "")
        if state in {"SUCCEEDED", "FAILED", "CANCELLED", "PAUSED"}:
            return task
        time.sleep(POLL_S)
    return task


def measured_goal(task: dict) -> dict:
    """Read the grounding evidence the runner recorded for this task.

    It travels as a task event, so the same durable record an operator reviews is
    also what the experiment measures - no side channel.
    """
    events = task.get("events") or []
    for event in reversed(events):
        if event.get("type") == "GROUNDING_EVIDENCE":
            return dict(event.get("payload") or {})
    # Older recordings or a plan without a manipulation checkpoint leave no
    # evidence; that is reported as "unknown", never assumed to be the baseline.
    return {}


def commanded_goals(task: dict) -> list:
    """The poses actually dispatched, so the two arms' motion is comparable."""
    goals = []
    for event in task.get("events") or []:
        if event.get("type") != "TOOL_ACTIVITY":
            continue
        payload = event.get("payload") or {}
        if payload.get("toolName") != "navigation.navigate" or payload.get("activityStatus") != "SENDING":
            continue
        pose = (payload.get("arguments") or {}).get("goalPose")
        if pose:
            goals.append([round(float(value), 3) for value in pose])
    return goals


def latency(base: str) -> dict:
    try:
        return api(base, "/v1/telemetry/latency?groupBy=capability&windowMs=0")
    except Exception as error:  # noqa: BLE001 - a missing endpoint is data, not a crash
        return {"unavailable": f"{type(error).__name__}: {error}"}


def pre_position(base: str) -> dict:
    """Drive to a certified-clear pose through the declared service, not a shortcut."""
    request = Request(base + "/v1/robot/services",
                      data=json.dumps({"name": "mapping.status", "parameters": {}}).encode(),
                      headers={"Content-Type": "application/json", "Origin": base})
    with urlopen(request, timeout=20) as response:
        status = json.loads(response.read())["result"]
    trail = status.get("trajectory") or []
    return {"trajectoryPoints": len(trail), "lastPose": trail[-1] if trail else None}


def run_arm(base: str, arm: str, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    position = pre_position(base)
    started = time.monotonic()
    task = api(base, "/v1/tasks", {"adapter": "mujoco", "request": REQUEST})
    api(base, f"/v1/tasks/{task['id']}/approve", {})
    finished = wait_for_task(base, task["id"], time.monotonic() + TASK_TIMEOUT_S)
    duration = time.monotonic() - started
    evidence = measured_goal(finished)
    report = {
        "arm": arm,
        "taskId": task["id"],
        "request": REQUEST,
        "state": str(finished.get("state") or ""),
        "durationS": round(duration, 2),
        "goalSource": evidence.get("goalSource", "unknown"),
        "recallAgeMs": evidence.get("recallAgeMs"),
        "commandedGoals": commanded_goals(finished),
        "prePosition": position,
        "latency": latency(base),
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in
                      ("arm", "taskId", "state", "durationS", "goalSource", "recallAgeMs",
                       "commandedGoals")}, ensure_ascii=False))
    return report


def summarise(report: dict) -> dict:
    groups = {group["key"]: group for group in (report.get("latency") or {}).get("groups", [])}
    navigation = groups.get("navigation.navigate", {}).get("phases", {}).get("execute", {})
    return {
        "state": report.get("state"),
        "durationS": report.get("durationS"),
        "goalSource": report.get("goalSource"),
        "recallAgeMs": report.get("recallAgeMs"),
        "stepsRecorded": (report.get("latency") or {}).get("count", 0),
        "navigationExecuteP50Ms": navigation.get("p50Ms"),
        "navigationExecuteMaxMs": navigation.get("maxMs"),
    }


def compare(baseline: Path, candidate: Path, output: Path) -> dict:
    before = json.loads(baseline.read_text())
    after = json.loads(candidate.read_text())
    result = {"baseline": summarise(before), "candidate": summarise(after)}
    result["delta"] = {
        "succeeded": (before.get("state") == "SUCCEEDED", after.get("state") == "SUCCEEDED"),
        "goalChanged": before.get("goalSource") != after.get("goalSource"),
        "durationDeltaS": (None if before.get("durationS") is None or after.get("durationS") is None
                           else round(after["durationS"] - before["durationS"], 2)),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8897")
    parser.add_argument("--arm", choices=["commissioned", "recalled"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    args = parser.parse_args()
    if args.compare:
        if not args.baseline or not args.candidate:
            parser.error("--compare needs --baseline and --candidate reports")
        compare(args.baseline, args.candidate, args.output)
        return 0
    if not args.arm:
        parser.error("choose --arm or --compare")
    run_arm(args.base_url, args.arm, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
