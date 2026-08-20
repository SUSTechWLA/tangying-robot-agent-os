"""Run RoboCasa process acceptance and write a machine-readable evidence pack."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

start_robocasa_handoff_stack = importlib.import_module(
    "tests.e2e.robocasa_harness"
).start_robocasa_handoff_stack


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tangying-robocasa-e2e-") as directory:
        stack = start_robocasa_handoff_stack(Path(directory))
        try:
            initial = stack.api("/v1/world")
            task_id = stack.create_and_approve()
            task = stack.wait_task(task_id)
            world = stack.api("/v1/world")
            intents = stack.api(f"/v1/tasks/{task_id}/intents")
            events = stack.api(f"/v1/tasks/{task_id}/domain-events")
            devices = stack.api("/v1/devices")
            write_json(output / "world-initial.json", initial)
            write_json(output / "world-final.json", world)
            write_json(output / "task.json", task)
            write_json(output / "intents.json", intents)
            write_json(output / "events.json", events)
            write_json(output / "devices.json", devices)
            verdicts = [
                {
                    "index": item["index"],
                    "status": item.get("harnessStatus"),
                    "reason": item.get("harnessReason"),
                    "evidenceIds": item.get("harnessEvidenceIds", []),
                }
                for item in intents["intents"]
            ]
            write_json(output / "harness-verdicts.json", verdicts)
            model_identity = world["entities"]["robot-1"].get("attributes", {})
            summary = {
                "taskId": task_id,
                "state": task["state"],
                "passed": task["state"] == "SUCCEEDED"
                and [item["status"] for item in verdicts] == ["SATISFIED", "SATISFIED"],
                "sceneId": model_identity.get("scene_id"),
                "adapter": model_identity.get("adapter"),
                "modelRevision": model_identity.get("model_hash"),
            }
            write_json(output / "summary.json", summary)
            if not summary["passed"]:
                raise SystemExit(1)
        finally:
            stack.stop()


if __name__ == "__main__":
    main()
