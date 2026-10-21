"""Repeated LocalAgent/DDS acceptance with frozen evidence, never physical robots.

Run inside an isolated ROS2 environment with a built Linux LocalAgent binary.
Every route launches its own agent/runtime/controller processes. Driver-loss and
stop cases use the existing DDS contract harness; they are separate evidence
units, not natural-language task completions or flight/gait validation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import signal
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KINDS = ("ugv", "uav", "legged")
EVIDENCE_CLASS = "recorded_controller_actual_localagent_grpc_ros2_no_hardware"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def source_hashes() -> dict[str, str]:
    """Include untracked new sources while excluding generated experiment files."""
    directories = ("agent", "agentruntime", "cmd", "console", "core", "edge", "fleet", "gen/go", "incidents", "latency",
                   "internal", "middleware", "orchestration", "skills", "tasks", "robot", "python",
                   "scripts", "tests", "examples/robots", "web")
    paths = {ROOT / name for name in ("go.mod", "go.sum", "pyproject.toml")}
    for directory in directories:
        paths.update(path for path in (ROOT / directory).rglob("*")
                     if (path.suffix in {".py", ".go", ".proto", ".action", ".msg", ".srv", ".json", ".xml", ".html", ".js", ".css", ".cfg", ".txt", ".yaml", ".hpp", ".cpp", ".h"}
                         or "resource" in path.parts)
                     and not set(path.parts) & {"__pycache__", "build", "install", "log", "node_modules"})
    # These files are part of the Go embedded console and affect the binary.
    paths.update((ROOT / "web/assets").rglob("*"))
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(paths) if path.is_file()}


def valid_route(summary: dict) -> bool:
    return (isinstance(summary, dict) and isinstance(summary.get("taskId"), str) and bool(summary["taskId"])
            and summary.get("success") is True and summary.get("state") == "SUCCEEDED"
            and summary.get("evidenceClass") == EVIDENCE_CLASS
            and summary.get("navigationCommands") == 11
            and summary.get("independentArrivalChecks") == 11
            and summary.get("actualRecordedMoves") == 11
            and summary.get("actualArrivalCheckGoals") == 11
            and len(set(summary.get("actualMoveCommandIds", []))) == 11
            and summary.get("spatialPaths") == 11
            and summary.get("returnedToObservedStart") is True
            and summary.get("duplicateMotionCommands") == 0
            and summary.get("sourceChangedDuringExperiment") == []
            and summary.get("binaryChangedDuringExperiment") is False
            and summary.get("profileChangedDuringExperiment") is False)


def summarize(trials: list[dict], rounds: int, source_stable: bool) -> dict:
    expected = {(kind, repetition) for kind in KINDS for repetition in range(1, rounds + 1)}
    actual = {(trial["kind"], trial["round"]) for trial in trials if trial["type"] == "natural_language_route"}
    routes = [trial for trial in trials if trial["type"] == "natural_language_route"]
    faults = [trial for trial in trials if trial["type"] == "driver_fault_contracts"]
    task_ids = [trial["taskId"] for trial in routes if trial.get("passed")]
    unique_tasks = len(task_ids) == len(set(task_ids))
    faults_ok = len(faults) == rounds and all(trial["passed"] and trial.get("caseCount") == 6 for trial in faults)
    return {"schemaVersion": "edge.ros2-campaign.summary.v1", "evidenceClass": EVIDENCE_CLASS,
            "passed": source_stable and unique_tasks and actual == expected and len(routes) == len(expected)
                      and all(trial["passed"] for trial in routes) and faults_ok,
            "sourceStable": source_stable, "uniqueTaskIds": unique_tasks, "rounds": rounds, "plannedRoutes": len(expected),
            "completedRoutes": sum(trial["passed"] for trial in routes),
            "plannedFaultCases": 6 * rounds, "passedFaultCases": sum(trial.get("passedCaseCount", 0) for trial in faults),
            "recordedNavigationCommands": sum(trial.get("navigationCommands", 0) for trial in routes),
            "independentArrivalChecks": sum(trial.get("independentArrivalChecks", 0) for trial in routes),
            "physicalValidation": False, "dynamicsValidation": False,
            "sampling": "repeated deterministic engineering checks, not independent environments or robot samples",
            "trials": trials}


def fault_results_valid(rows: object) -> bool:
    if not isinstance(rows, list) or len(rows) != 6 or not all(isinstance(row, dict) for row in rows):
        return False
    keys = {(row.get("kind"), row.get("safeStopProcedureAcknowledged")) for row in rows}
    return keys == {(kind, mode) for kind in KINDS for mode in (True, False)} and all(
        row.get("success") is True and row.get("physicalFixtureMoves") == 3
        and row.get("independentArrivalChecks") == 3 and row.get("replayNoDuplicate") is True
        and (row.get("driverLossDisablesMotion") is True) != (row.get("safeStopProcedureAcknowledged") is True)
        for row in rows)


def run_command(argv: list[str], log, timeout: float = 300) -> int:
    """Own the entire process group so a timed-out agent cannot leave drivers."""
    process = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    except BaseException as error:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        process.wait(timeout=5)
        if isinstance(error, subprocess.TimeoutExpired):
            return 124
        raise


def run(output: Path, binary: Path, rounds: int) -> int:
    if not 1 <= rounds <= 20:
        raise ValueError("rounds must be within 1..20")
    if not binary.is_file():
        raise ValueError("LocalAgent binary does not exist")
    output.mkdir(parents=True, exist_ok=False)
    # Capture each source's actual bytes once; the archive and metadata always
    # refer to the same bytes even if a concurrent editor invalidates the run.
    source_bytes = {name: (ROOT / name).read_bytes() for name in source_hashes()}
    sources = {name: hashlib.sha256(data).hexdigest() for name, data in source_bytes.items()}
    frozen_binary = digest(binary)
    metadata = {"schemaVersion": "edge.ros2-campaign.v1", "startedAt": datetime.now(UTC).isoformat(),
                "rounds": rounds, "kinds": KINDS, "binarySha256": frozen_binary,
                "sourceSha256": sources, "pythonVersion": platform.python_version(),
                "platform": platform.platform(), "rosDistro": os.environ.get("ROS_DISTRO"),
                "rosDomainId": os.environ.get("ROS_DOMAIN_ID"),
                "rosLocalhostOnly": os.environ.get("ROS_LOCALHOST_ONLY"),
                "rmwImplementation": os.environ.get("RMW_IMPLEMENTATION"),
                "isolationRequirement": "dedicated localhost-only DDS domain; recorded controller; no hardware",
                "routeCriterion": "11 confirmed paths and independent arrivals including return to observed start",
                "physicalValidation": False, "dynamicsValidation": False}
    save(output / "run.json", metadata)
    with tarfile.open(output / "source-snapshot.tar.gz", "w:gz") as archive:
        for relative, data in source_bytes.items():
            member = tarfile.TarInfo(relative)
            member.size = len(data)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
    trials: list[dict] = []
    stable = source_hashes() == sources

    def launch(argv: list[str], destination: Path) -> tuple[int, float]:
        start = time.monotonic()
        with (output / (destination.name + ".log")).open("w") as log:
            return run_command(argv, log), time.monotonic() - start

    for repetition in range(1, rounds + 1):
        if not stable:
            break
        for kind in KINDS:
            destination = output / f"round-{repetition:02d}-{kind}"
            deployment = output / f"map-{repetition:02d}-{kind}"
            code, elapsed = launch([sys.executable, str(ROOT / "tests/contract/spatial_scenarios.py"),
                                    "--kind", kind, "--output", str(deployment)], deployment)
            if code == 0:
                code, elapsed = launch([sys.executable, str(ROOT / "scripts/run_ros2_edge_acceptance.py"),
                    "--binary", str(binary), "--kind", kind, "--spatial-deployment", str(deployment / "deployment.json"),
                    "--return-start", "--output", str(destination)], destination)
            summary = json.loads((destination / "summary.json").read_text()) if (destination / "summary.json").exists() else {}
            trials.append({"type": "natural_language_route", "kind": kind, "round": repetition,
                           "output": destination.name, "exitCode": code, "passed": code == 0 and valid_route(summary),
                           "elapsedSeconds": elapsed, "taskId": summary.get("taskId"),
                           "navigationCommands": summary.get("actualRecordedMoves", 0),
                           "independentArrivalChecks": summary.get("actualArrivalCheckGoals", 0)})
            stable = source_hashes() == sources and digest(binary) == frozen_binary
            save(output / "summary.json", summarize(trials, rounds, stable))
            if not stable:
                break
        if stable:
            destination = output / f"round-{repetition:02d}-driver-faults"
            code, elapsed = launch([sys.executable, str(ROOT / "tests/contract/ros2_profiled_smoke.py"),
                                    "--output", str(destination)], destination)
            rows = json.loads((destination / "summary.json").read_text()) if (destination / "summary.json").exists() else []
            passed = code == 0 and fault_results_valid(rows)
            trials.append({"type": "driver_fault_contracts", "round": repetition, "output": destination.name,
                           "exitCode": code, "passed": passed, "elapsedSeconds": elapsed,
                           "caseCount": len(rows), "passedCaseCount": 6 if passed else 0})
            stable = source_hashes() == sources and digest(binary) == frozen_binary
            save(output / "summary.json", summarize(trials, rounds, stable))
    result = summarize(trials, rounds, stable)
    result["finishedAt"] = datetime.now(UTC).isoformat()
    save(output / "summary.json", result)
    with (output / "trials.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["type", "round", "kind", "passed", "exitCode", "taskId",
            "navigationCommands", "independentArrivalChecks", "caseCount", "elapsedSeconds", "output"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trials)
    files = {str(path.relative_to(output)): digest(path) for path in sorted(output.rglob("*")) if path.is_file()}
    save(output / "manifest.json", {"algorithm": "sha256", "files": files})
    print(json.dumps({key: value for key, value in result.items() if key != "trials"}), flush=True)
    return 0 if result["passed"] else 1


def verify(output: Path) -> dict:
    manifest = json.loads((output / "manifest.json").read_text())
    if manifest.get("algorithm") != "sha256" or not isinstance(manifest.get("files"), dict):
        raise ValueError("invalid manifest")
    actual_files = {str(path.relative_to(output)) for path in output.rglob("*")
                    if path.is_file() and path != output / "manifest.json"}
    if actual_files != set(manifest["files"]):
        raise ValueError("manifest does not cover exactly the evidence files")
    for name, expected in manifest["files"].items():
        path = (output / name).resolve()
        if not path.is_relative_to(output.resolve()) or not path.is_file() or digest(path) != expected:
            raise ValueError(f"evidence hash mismatch: {name}")
    metadata = json.loads((output / "run.json").read_text())
    with tarfile.open(output / "source-snapshot.tar.gz", "r:gz") as archive:
        members = archive.getmembers()
        if len(members) != len(metadata["sourceSha256"]) or {member.name for member in members} != set(metadata["sourceSha256"]):
            raise ValueError("source archive members mismatch")
        for member in members:
            data = archive.extractfile(member)
            if not member.isfile() or data is None or hashlib.sha256(data.read()).hexdigest() != metadata["sourceSha256"][member.name]:
                raise ValueError(f"source archive mismatch: {member.name}")
    summary = json.loads((output / "summary.json").read_text())
    trials = summary["trials"]
    for trial in trials:
        path = (output / trial["output"]).resolve()
        if not path.is_relative_to(output.resolve()):
            raise ValueError("trial path escapes evidence")
        child = json.loads((path / "summary.json").read_text()) if (path / "summary.json").exists() else {}
        expected = trial["exitCode"] == 0 and (valid_route(child) if trial["type"] == "natural_language_route" else fault_results_valid(child))
        if trial["passed"] != expected:
            raise ValueError("trial outcome disagrees with retained evidence")
    recomputed = summarize(trials, metadata["rounds"], summary["sourceStable"])
    if any(summary.get(key) != value for key, value in recomputed.items()):
        raise ValueError("campaign summary disagrees with trials")
    return {"verified": True, "passed": summary["passed"], "files": len(manifest["files"]),
            "verificationScope": "byte hashes and recorded assertions; no ROS or physical re-execution"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify(args.output.resolve())))
        return 0
    if args.binary is None:
        parser.error("--binary is required unless --verify")
    return run(args.output.resolve(), args.binary.resolve(), args.rounds)


if __name__ == "__main__":
    raise SystemExit(main())
