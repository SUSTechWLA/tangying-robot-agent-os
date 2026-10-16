"""Run software-only single-robot fault campaigns and retain research evidence.

This harness never connects to a robot. Gazebo and hardware trials use their
own harness and evidence units; do not pool them with these scripted fixtures.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TESTS = {"TestSingleRobotLongHorizonCampaign", "TestSingleRobotCampaignUnknownAfterLongConfirmedPrefix",
                  "TestSingleRobotCampaignBudgetBoundaries", "TestObjectiveSequentialCloudReceiptCampaign",
                  "TestSingleRobotCampaignSQLiteCloseReopen", "TestObjectiveResumedClaimReportsUnknownOutcome",
                  "TestDelegatedVerifiedCrashWindowResumesWithoutRepeatingMovement"}
REQUIRED_SCENARIOS = {"persistent_single_robot_multi_task": 5, "sequential_cloud_receipt_loss": 12,
                      "unknown_effect_after_long_confirmed_prefix": 1, "sqlite_close_reopen_verified_goal": 1,
                      "restart_then_unknown_write": 1, "plan_budget_after_confirmed_prefix": 1,
                      "step_budget_rejects_oversized_next_chunk": 1, "read_budget_preserves_final_check": 1}
PATTERN = "TestSingleRobot|TestObjectiveSequentialCloudReceiptCampaign|TestObjectiveResumedClaimReportsUnknownOutcome|TestDelegated"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict[str, str]:
    source_paths = {ROOT / "go.mod", ROOT / "go.sum", Path(__file__).resolve()}
    for directory in ("agent", "agentruntime", "console", "core", "internal", "edge", "fleet", "middleware", "tasks", "orchestration", "skills", "gen/go"):
        source_paths.update((ROOT / directory).rglob("*.go"))
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(source_paths) if path.is_file()}


def extract_records(events: list[dict]) -> list[dict]:
    """Go test splits long Output lines; join by package/test before parsing."""
    pending: dict[tuple[str, str], str] = {}
    records = []
    for event in events:
        if event.get("Action") != "output":
            continue
        key = (event.get("Package", ""), event.get("Test", ""))
        lines = (pending.get(key, "") + event.get("Output", "")).split("\n")
        pending[key] = lines.pop()
        for line in lines:
            marker = "ROBOT_EXPERIMENT "
            if marker in line:
                row = json.loads(line.split(marker, 1)[1])
                if not isinstance(row, dict):
                    raise ValueError("experiment record must be an object")
                records.append({**row, "sourcePackage": key[0], "sourceTest": key[1]})
    if any("ROBOT_EXPERIMENT " in text for text in pending.values()):
        raise ValueError("truncated experiment record")
    return records


def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=False)
    command = ["go", "test", "-count=1", "-json", "-timeout=10m"]
    command += ["./internal/edgeagent", "./edge/worker", "-run", PATTERN]
    sources = source_hashes()
    source_digest = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    metadata = {
        "schemaVersion": "robot.robustness.run.v1", "evidenceType": "software_contract",
        "startedAt": datetime.now(UTC).isoformat(), "command": command,
        "baseCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "platform": platform.platform(), "pythonVersion": platform.python_version(),
        "goVersion": subprocess.check_output(["go", "version"], cwd=ROOT, text=True).strip(),
        "sourceSetDigest": source_digest, "sourceSha256": sources,
        "sampling": "deterministic engineering scenarios, not independent robot samples",
    }
    (output / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    with (output / "test-events.jsonl").open("w") as stream:
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=False)
    source_changed = sources != source_hashes()
    events = []
    non_json = []
    for index, line in enumerate((output / "test-events.jsonl").read_text().splitlines(), 1):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            non_json.append(index)
    failures = [{"package": e.get("Package"), "test": e.get("Test"), "elapsed": e.get("Elapsed")}
                for e in events if e.get("Action") == "fail"]
    extraction_error = None
    try:
        records = extract_records(events)
    except (ValueError, TypeError) as error:
        records, extraction_error = [], str(error)
    outcomes = {(event.get("Package"), event.get("Test")): event["Action"]
                for event in events if event.get("Test") and event.get("Action") in ("pass", "fail", "skip")}
    for row in records:
        row["sourceTestOutcome"] = outcomes.get((row["sourcePackage"], row["sourceTest"]), "incomplete")
    (output / "records.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records))
    tasks = [row for row in records if row.get("recordType", "task") == "task"]
    passed_tests = {event.get("Test") for event in events if event.get("Action") == "pass"}
    missing_tests = sorted(REQUIRED_TESTS - passed_tests)
    scenario_counts = {scenario: sum(row.get("scenario") == scenario and row["sourceTestOutcome"] == "pass" for row in tasks)
                       for scenario in REQUIRED_SCENARIOS}
    missing_scenarios = {scenario: count for scenario, count in REQUIRED_SCENARIOS.items() if scenario_counts[scenario] < count}
    columns = ["sourceTest", "sourceTestOutcome", "scenario", "taskId", "taskIndex", "evidenceType", "outcome", "plannedStations", "steps", "plans", "reads", "writes", "physicalStarts", "duplicateStarts", "durationScope", "elapsedSeconds"]
    with (output / "tasks.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(tasks)
    summary = {
        "schemaVersion": "robot.robustness.summary.v1", "finishedAt": datetime.now(UTC).isoformat(),
        "passed": result.returncode == 0 and bool(records) and not non_json and extraction_error is None and not missing_tests and not missing_scenarios and not source_changed,
        "exitCode": result.returncode, "extractionError": extraction_error, "nonJSONLines": non_json,
        "sourceChangedDuringRun": source_changed,
        "requiredTestsNotPassed": missing_tests,
        "scenarioCounts": scenario_counts, "requiredScenariosMissing": missing_scenarios,
        "taskRecords": len(tasks), "allRecords": len(records), "failures": failures,
        "testResults": [{"package": e.get("Package"), "test": e.get("Test"), "outcome": e["Action"], "elapsed": e.get("Elapsed")}
                        for e in events if e.get("Test") and e.get("Action") in ("pass", "fail", "skip")],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    files = {path.name: digest(path) for path in sorted(output.iterdir()) if path.is_file()}
    (output / "manifest.json").write_text(json.dumps({"algorithm": "sha256", "files": files}, indent=2) + "\n")
    print(json.dumps({"output": str(output), "passed": summary["passed"], "taskRecords": len(tasks), "exitCode": result.returncode}))
    return 0 if summary["passed"] else (result.returncode or 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New evidence directory; existing paths are rejected")
    args = parser.parse_args()
    return run(args.output.resolve())


if __name__ == "__main__":
    sys.exit(main())
