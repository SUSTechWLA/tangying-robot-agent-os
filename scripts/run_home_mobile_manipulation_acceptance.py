"""Run the commissioned household mobile-manipulation task against a local Agent.

Start the RGB-D household task scene first::

    SIM_STACK_PERCEPTION=rgbd SIM_STACK_SCENE=home_task \
      bash scripts/sim-stack.sh start

This runner creates exactly one task, approves it, waits for the terminal state,
and preserves every step's observation plus the original RGB/depth bytes. It is
an acceptance probe for the reference simulation; it does not reset a running
world or retry an uncertain physical command.
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

REQUEST = "从客厅出发，去厨房拿红色杯子，放进蓝色收纳盒，然后回到客厅"
EXPECTED_STEPS = [
    "observe",
    "navigate_01",
    "verify_arrival_01",
    "observe_after_navigation",
    "resolve",
    "plan_grasp",
    "pick",
    "verify_grasp",
    "place",
    "verify_place",
    "navigate_02",
    "verify_arrival_02",
]
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERABLE_FAILURE", "SAFETY_STOPPED"}


def _local_base(value: str) -> str:
    origin = urlsplit(value)
    if (
        origin.scheme != "http"
        or origin.hostname not in {"127.0.0.1", "localhost"}
        or origin.username
        or origin.password
        or origin.path not in {"", "/"}
        or origin.query
        or origin.fragment
    ):
        raise ValueError("acceptance requires a local HTTP Agent origin")
    return value.rstrip("/")


def run(base: str, output: Path, timeout: float = 120.0) -> dict:
    base = _local_base(base)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 600:
        raise ValueError("timeout must be between 0 and 600 seconds")
    output.mkdir(parents=True, exist_ok=False)

    def api(path: str, body: dict | None = None):
        payload = None if body is None else json.dumps(body).encode()
        request = Request(
            base + path,
            data=payload,
            method="POST" if body is not None else "GET",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=20) as response:
            return json.load(response)

    def save(name: str, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    initial = None
    startup_error = None
    startup_deadline = time.monotonic() + 30
    while time.monotonic() < startup_deadline:
        try:
            candidate = api("/v1/telemetry?adapter=mujoco&limit=1")
            if candidate.get("hasLatest"):
                initial = candidate
                break
        except (OSError, ValueError) as error:
            startup_error = error
        time.sleep(0.2)
    if initial is None:
        raise RuntimeError(f"Agent has no latest MuJoCo telemetry: {startup_error}")
    latest = initial["latest"]
    save("initial-telemetry.json", latest)
    state = latest["robotState"]
    navigation = state.get("navigation", {})
    profile = latest["robotProfile"]
    if state.get("perception", {}).get("mode") != "rgbd":
        raise AssertionError("home_task acceptance requires RGB-D perception")
    if navigation.get("scene") != "home_task":
        raise AssertionError(f"expected home_task scene, got {navigation.get('scene')!r}")
    required_tools = {"navigation.navigate", "manipulation.pick", "manipulation.place"}
    if not required_tools.issubset(set(profile.get("tools", []))):
        raise AssertionError("profile does not expose navigation and manipulation tools")

    task = api("/v1/tasks", {"adapter": "mujoco", "request": REQUEST})
    task_path = "/v1/tasks/" + task["id"]
    save("created-task.json", task)
    api(task_path + "/approve", {})

    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = api(task_path)
            save("final-task.json", task)
            if task["state"] in TERMINAL:
                break
            time.sleep(0.2)
        else:
            raise TimeoutError("household task did not reach a terminal state")

        index = api(task_path + "/observations?limit=200")
        save("observation-index.json", index)
        records = index.get("records", [])
        details = {}
        for record in records:
            detail = api(task_path + "/observations/" + record["id"])
            # Tool events pin the immutable captureId, while the observation
            # listing is addressed by its record id. Keep both keys so the
            # runner verifies the exact command frame rather than a nearby
            # historical record.
            details[(record["stepId"], record["id"])] = detail
            details[(detail["stepId"], detail["captureId"])] = detail
            save(record["id"] + ".json", detail)
            for kind in ("rgb", "depth"):
                with urlopen(base + task_path + "/observations/" + record["id"] + "/" + kind, timeout=20) as response:
                    raw = response.read()
                expected = record[kind + "Sha256"]
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise AssertionError(f"{record['stepId']}/{kind} hash mismatch")
                (output / (record["id"] + "-" + kind + ".png")).write_bytes(raw)

        activities = [event for event in task.get("events", []) if event.get("type") == "TOOL_ACTIVITY"]
        confirmed = [event for event in activities if event["payload"].get("activityStatus") == "CONFIRMED"]
        confirmed_by_step = {event["stepId"]: event for event in confirmed}
        if task["state"] != "SUCCEEDED":
            raise AssertionError(json.dumps(task, ensure_ascii=False))
        if list(confirmed_by_step) != EXPECTED_STEPS:
            raise AssertionError(f"unexpected confirmed tool order: {list(confirmed_by_step)}")
        if len(confirmed) != len(EXPECTED_STEPS):
            raise AssertionError("a physical or verification tool was confirmed more than once")
        for step, event in confirmed_by_step.items():
            payload = event["payload"]
            evidence_id = payload.get("receiptObservationId")
            if payload.get("evidenceSource") != "command_observation" or not evidence_id:
                raise AssertionError(f"{step} has no command observation evidence")
            detail = details.get((step, evidence_id))
            if detail is None or detail.get("stepId") != step:
                raise AssertionError(f"{step} evidence is not pinned to its command capture")

        placement_event = confirmed_by_step["verify_place"]
        placement = details[("verify_place", placement_event["payload"]["receiptObservationId"])]
        verification = placement["snapshot"]["robotState"]["verification"]
        if not verification.get("passed") or verification.get("observed_relation") != "inside:kitchen-bin":
            raise AssertionError("RGB-D placement verification did not confirm inside:kitchen-bin")
        if verification.get("sample_count") != 3 or verification.get("stable_duration_s", 0) < 0.1:
            raise AssertionError("placement verification did not use three stable RGB-D samples")

        summary = {
            "passed": True,
            "taskId": task["id"],
            "request": REQUEST,
            "scene": navigation["scene"],
            "perception": state["perception"]["mode"],
            "confirmedSteps": EXPECTED_STEPS,
            "physicalToolSteps": ["navigate_01", "pick", "place", "navigate_02"],
            "historicalObservationCount": len(records),
            "verifiedImageCount": 2 * len(records),
            "finalPlacement": verification,
        }
        save("summary.json", summary)
        return summary
    except BaseException as error:
        failure = {"passed": False, "taskId": task["id"], "errorType": type(error).__name__, "message": str(error)}
        try:
            latest = api(task_path)
            if latest["state"] not in TERMINAL:
                api(task_path + "/cancel", {})
                latest = api(task_path)
            save("final-task.json", latest)
        except Exception as cleanup_error:  # noqa: BLE001 - preserve the original failure
            failure["cleanupError"] = str(cleanup_error)
        save("failure.json", failure)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    result = run(args.base_url, args.output, args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
