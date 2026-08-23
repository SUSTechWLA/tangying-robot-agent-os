"""Run the cloud handoff acceptance and write a machine-readable evidence pack."""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

fleet_harness = importlib.import_module("tests.e2e.fleet_harness")
start_fleet_handoff_stack = fleet_harness.start_fleet_handoff_stack


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def run_normal(output: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="tangying-fleet-harness-") as directory:
        stack = start_fleet_handoff_stack(Path(directory))
        try:
            initial = stack.api("/v1/world")
            task_id = stack.create_and_approve()
            task = stack.wait_task(task_id)
            experience = stack.wait_experience(
                task_id,
                lambda current: (
                    len(
                        [
                            activity
                            for activity in current.get("activities", [])
                            if activity.get("controlMethod") == "仿真确定性策略"
                            and activity.get("controlStage") == "已由环境确认"
                        ]
                    )
                    == 4
                ),
            )
            world = stack.api("/v1/world")
            events = stack.api(f"/v1/tasks/{task_id}/domain-events")
            intents = stack.api(f"/v1/tasks/{task_id}/intents")
            devices = stack.api("/v1/devices")
            write_json(output / "task.json", task)
            write_json(output / "world-final.json", world)
            write_json(output / "leases.json", world.get("resources", {}))
            write_json(output / "devices.json", devices)
            write_json(output / "intents.json", intents)
            write_json(output / "task-experience.json", experience)
            (output / "events.jsonl").write_text(
                "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
            )
            event_types = [event["eventType"] for event in events]
            policy_activities = [
                activity
                for activity in experience.get("professional", {}).get("activities", [])
                if activity.get("policy")
            ]
            policy_inferences = {
                activity["policy"].get("inferenceId") for activity in policy_activities
            }
            confirmed_policy_tools = [
                activity
                for activity in experience.get("activities", [])
                if activity.get("controlMethod") == "仿真确定性策略"
                and activity.get("controlStage") == "已由环境确认"
            ]
            experience_wire = json.dumps(experience, ensure_ascii=False)
            invariants = {
                "taskSucceeded": task["state"] == "SUCCEEDED",
                "worldRevisionMonotonic": world["revision"] > initial["revision"],
                "singleBlockOwner": world.get("resources", {})
                .get("block:red-block", {})
                .get("owner")
                == "environment",
                "blockAtTarget": world.get("entities", {})
                .get("red-block", {})
                .get("relations", {})
                .get("inside")
                == "right-target-zone",
                "blockAvailableExactlyOnce": event_types.count("BLOCK_AVAILABLE") == 1,
                "blockDeliveredExactlyOnce": event_types.count("BLOCK_DELIVERED") == 1,
                "policyConfirmedTools": len(confirmed_policy_tools) == 4,
                "policyEvidenceComplete": len(policy_inferences) == 4
                and all(
                    activity["policy"].get("manifestRevision")
                    and activity["policy"].get("observationId")
                    for activity in policy_activities
                ),
                "noPolicyActionLeak": "action_chunk" not in experience_wire
                and "left_arm_gripper.pos" not in experience_wire,
            }
            return {"taskId": task_id, "state": task["state"], "invariants": invariants}
        finally:
            stack.stop()


def run_faults(output: Path) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/e2e/test_fleet_faults.py",
            "tests/e2e/test_versioned_task_faults.py",
            "tests/e2e/test_policy_faults.py",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    result = {
        "passed": completed.returncode == 0,
        "exitCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "scenarios": [
            "observation_duplicate_reorder",
            "edge_disconnect_reconnect",
            "worker_crash_after_place",
            "coordinator_restart",
            "redis_outage",
            "stale_fencing_token",
            "receiver_offline_after_handoff",
            "camera_loss_and_ui_reconnect",
            "external_block_move",
            "versioned_task_update_fencing",
            "versioned_task_experience_gap",
            "policy_observation_stale_or_degraded",
            "policy_provider_timeout_or_unavailable",
            "policy_manifest_or_robot_mismatch",
            "policy_malformed_or_unsafe_action",
            "policy_unknown_physical_execution",
            "policy_public_projection_redaction",
        ],
    }
    versioned = [
        dataclasses.asdict(fleet_harness.run_revision_fault(fault))
        for fault in fleet_harness.REVISION_FAULT_COMMANDS
    ]
    result["versionedEvidence"] = versioned
    result["versionedPassed"] = all(
        all(command["exitCode"] == 0 for command in item["commands"]) for item in versioned
    )
    result["passed"] = result["passed"] and result["versionedPassed"]
    write_json(output / "faults.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("normal", "faults", "all"), default="all")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    summary: dict = {"scenario": args.scenario}
    if args.scenario in {"normal", "all"}:
        summary["normal"] = run_normal(output)
    if args.scenario in {"faults", "all"}:
        summary["faults"] = run_faults(output)
    summary["passed"] = (
        all(summary.get("normal", {}).get("invariants", {}).values())
        and summary.get("faults", {"passed": True})["passed"]
    )
    summary["consoleScreenshot"] = "validated separately by the in-app browser acceptance"
    write_json(output / "summary.json", summary)
    if not summary["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
