#!/usr/bin/env python3
"""Run real coverage mission ports against an ideal known-map software fixture.

This entry point never connects to Docker, ROS, a simulator or a physical robot.
Pose transitions and positive stop acknowledgements are explicit fixture events.
Coverage is recomputed from recorded ray-cast captures, never from predictions.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import itertools
import json
import math
import pathlib
import platform
import subprocess
import sys
from dataclasses import replace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot/gateway"))
SOURCES = (
    "scripts/run_spatial_coverage_fixture.py",
    "robot/gateway/tangying_robot_gateway/spatial_coverage.py",
    "robot/gateway/tangying_robot_gateway/spatial_navigation.py",
    "robot/gateway/tangying_robot_gateway/spatial_exploration.py",
    "robot/gateway/tangying_robot_gateway/spatial_mission.py",
)
LOGS = ("events.jsonl", "commands.jsonl", "captures.jsonl", "reports.jsonl",
        "coverage-ledger.jsonl", "coordination.jsonl")
VIRTUAL_DURATION_MS = 600_000


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def validate_request(request):
    from tangying_robot_gateway.spatial_coverage import CoverageConfig, _peer_clear, validate_scope
    from tangying_robot_gateway.spatial_exploration import SensorModel
    from tangying_robot_gateway.spatial_navigation import SpatialPathPlanner, _identifier, _pose

    if (type(request) is not dict or request.get("schemaVersion") != "coverage.request.v1"
            or not {"schemaVersion", "missionId", "scope", "robots"} <= request.keys()
            or request.keys() - {"schemaVersion", "missionId", "scope", "robots", "config"}):
        raise ValueError("request fields do not match coverage.request.v1")
    _identifier(request["missionId"], "missionId")
    robots = request["robots"]
    if type(robots) is not list or not 1 <= len(robots) <= 16:
        raise ValueError("fixture requires 1..16 registered robots")
    config, records, occupancy = CoverageConfig(**request.get("config", {})), {}, None
    for robot in robots:
        if type(robot) is not dict or set(robot) != {"volume", "policy", "pose", "sensor"}:
            raise ValueError("each robot requires volume, policy, pose and sensor")
        certifier = SpatialPathPlanner(robot["volume"], robot["policy"])
        volume, policy = certifier.volume, certifier.policy
        validate_scope(request["scope"], volume)
        if -1 in volume["cells"]:
            raise ValueError("known-map fixture rejects unknown occupancy")
        if occupancy is not None and volume["cells"] != occupancy:
            raise ValueError("all fixture robots must use identical registered occupancy")
        occupancy = volume["cells"]
        robot_id = volume["robotId"]
        if robot_id in records:
            raise ValueError("robot IDs must be unique")
        pose, sensor = _pose(robot["pose"]), SensorModel(**robot["sensor"])
        certifier.plan(pose, pose)
        records[robot_id] = {"volume": volume, "policy": policy, "pose": pose,
                             "sensor": sensor, "certifier": certifier}
    for first, second in itertools.combinations(records.values(), 2):
        if not _peer_clear({"waypoints": [first["pose"], first["pose"]]}, first["policy"],
                           second["pose"], second["policy"]):
            raise ValueError("initial fixture robot envelopes overlap")
    return config, records


class FixturePorts:
    """Deterministic ideal observation/localization/controller ports, with logs."""

    def __init__(self, records, config, journals, unknown_step):
        self.records, self.config, self.journals = records, config, journals
        self.now, self.unknown_step, self.command_count = 1000, unknown_step, 0
        self.pose_sequence = {robot: 1 for robot in records}
        self.pose_stamp = {robot: self.now for robot in records}
        self.capture_sequence = {robot: 0 for robot in records}
        self.unknown_applied = False

    def snapshot(self, robot):
        from tangying_robot_gateway.spatial_mission import SpatialSnapshot
        record = self.records[robot]
        return SpatialSnapshot(copy.deepcopy(record["volume"]), record["pose"][:],
            self.pose_sequence[robot], self.pose_stamp[robot], f"fixture-localizer-{robot}")

    def capture(self, robot):
        from tangying_robot_gateway.spatial_exploration import raycast_observation
        from tangying_robot_gateway.spatial_mission import geometry_id

        self.now += 1
        self.capture_sequence[robot] += 1
        record = self.records[robot]
        observation = raycast_observation(record["volume"], record["pose"], record["sensor"],
                    max_rays=self.config.max_rays, max_ray_steps=self.config.max_ray_steps)
        classified = {voxel: record["volume"]["cells"][voxel]
                      for voxel in observation["observedVoxelIds"]}
        packet = {"sourceId": f"fixture-sensor-{robot}", "sequence": self.capture_sequence[robot],
                  "observedAtMs": self.now, "worldFrameRevision": record["volume"]["worldFrameRevision"],
                  "geometryId": geometry_id(record["volume"]), "classified": classified,
                  "evidenceId": f"fixture-{robot}-{self.capture_sequence[robot]}"}
        # Pairs preserve numeric voxel ordering across JSON string-key conversion.
        self.journals["captures.jsonl"].append("capture", {"robotId": robot,
            "pose": record["pose"][:], **{key: value for key, value in packet.items() if key != "classified"},
            "classified": sorted(map(list, classified.items())), "observation": observation,
            "observationModel": "ideal_fixture_raycast", "physicalMeasurement": False})
        return packet

    def check_cancel(self):
        return None

    def execute(self, robot, path, task, deadline):
        from tangying_robot_gateway.spatial_coverage import _peer_clear
        from tangying_robot_gateway.spatial_mission import DriverResult

        record, start = self.records[robot], self.records[robot]["pose"][:]
        dispatched_at = self.now
        certifier = record["certifier"]
        identities = ("robotId", "mapId", "mapRevision", "frameId", "worldFrameRevision", "calibrationRevision")
        admitted = (path.get("schemaVersion") == "navigation.path.v1"
                    and path.get("mode") == record["policy"]["mode"]
                    and all(path.get(key) == record["volume"][key] for key in identities)
                    and path["waypoints"][0] == start and self.now < deadline
                    and all(certifier.segment_clear(a[:3], b[:3])
                            for a, b in itertools.pairwise(path["waypoints"]))
                    and all(_peer_clear(path, record["policy"], peer["pose"], peer["policy"])
                            for name, peer in self.records.items() if name != robot))
        if admitted:
            certifier.plan(path["waypoints"][-1], path["waypoints"][-1])
        self.command_count += 1
        injected = admitted and self.command_count == self.unknown_step
        outcome = "unknown" if injected else "succeeded" if admitted else "rejected"
        if injected:
            self.unknown_applied = True
        self.now += 10
        if outcome == "succeeded":
            record["pose"] = path["waypoints"][-1][:]
            self.pose_sequence[robot] += 1
            self.pose_stamp[robot] = self.now
        receipt = DriverResult(outcome, task, digest(path))
        self.journals["commands.jsonl"].append("command", {"robotId": robot, "taskId": task,
            "commandIndex": self.command_count, "pathDigest": receipt.path_digest, "path": path,
            "deadlineMs": deadline, "dispatchAtMs": dispatched_at, "nowMs": self.now, "startPose": start,
            "finalPose": record["pose"][:], "outcome": outcome, "independentAdmission": admitted,
            "unknownInjected": injected, "physicalMotion": False})
        return receipt

    def stop(self, robot, task):
        self.now += 1
        self.journals["events.jsonl"].append("fixture_stop_acknowledged", {
            "robotId": robot, "taskId": task, "nowMs": self.now,
            "acknowledged": True, "physicalStopMeasured": False,
            "basis": "positive_ideal_fixture_controller_ack"})
        return True

    def publish(self, report):
        self.journals["reports.jsonl"].append("report", report)


class BudgetedPolicy:
    def __init__(self, snapshot, record, request, config, ports, work, events):
        self.snapshot, self.record, self.request = snapshot, record, request
        self.config, self.ports, self.work, self.events = config, ports, work, events

    def propose(self, start, **kwargs):
        from tangying_robot_gateway.spatial_coverage import SpatialCoverageExplorer, _peer_clear

        calls = self.config.max_total_plan_calls-self.work["planCalls"]
        views = self.config.max_total_viewpoint_evaluations-self.work["viewpointEvaluations"]
        if calls <= 0 or views < 6:
            self.work["exhausted"] = True
            return {"candidates": [], "diagnostics": {"reason": "global_planning_budget"}}
        config = replace(self.config, max_plan_calls=min(self.config.max_plan_calls, calls),
                         max_viewpoints=min(self.config.max_viewpoints, views))
        explorer = SpatialCoverageExplorer(self.snapshot.volume, self.record["policy"],
                        self.request["scope"], self.record["sensor"], config)
        proposal = explorer.propose(start, **kwargs)
        self.work["planCalls"] += proposal["diagnostics"]["planCalls"]
        self.work["viewpointEvaluations"] += proposal["diagnostics"]["evaluatedViewpoints"]
        candidates = [candidate for candidate in proposal["candidates"] if all(
            _peer_clear(candidate["path"], self.record["policy"], peer["pose"], peer["policy"])
            for robot, peer in self.ports.records.items() if robot != self.snapshot.volume["robotId"])]
        proposal["diagnostics"]["peerRefusals"] = len(proposal["candidates"])-len(candidates)
        proposal["candidates"] = candidates
        proposal["candidate"] = candidates[0] if candidates else None
        proposal["status"] = "candidate" if candidates else "no_candidate"
        self.events.append("proposal", {"robotId": self.snapshot.volume["robotId"], "proposal": proposal})
        return proposal


def recompute_summary(captures, commands, reports, events, request, work, unknown_step):
    targets = set(request["scope"]["targetVoxelIds"])
    seen, repeated, observations = set(), 0, 0
    by_robot = {record["volume"]["robotId"]: record for record in request["robots"]}
    for event in captures:
        packet = event["payload"]
        volume = by_robot[packet["robotId"]]["volume"]
        for voxel, value in packet["classified"]:
            if type(voxel) is not int or not 0 <= voxel < len(volume["cells"]) or volume["cells"][voxel] != value:
                raise ValueError("fixture capture contradicts the registered known map")
        ids = {voxel for voxel, _value in packet["classified"]} & targets
        observations += len(ids)
        repeated += len(ids & seen)
        seen.update(ids)
    paths = [event["payload"] for event in commands]
    executed = sum(sum(math.dist(a[:3], b[:3]) for a, b in itertools.pairwise(command["path"]["waypoints"]))
                   for command in paths if command["outcome"] == "succeeded")
    commanded = sum(sum(math.dist(a[:3], b[:3]) for a, b in itertools.pairwise(command["path"]["waypoints"]))
                    for command in paths)
    stop_acks = [event["payload"] for event in events if event["kind"] == "fixture_stop_acknowledged"]
    proposals = [event["payload"]["proposal"]["diagnostics"] for event in events if event["kind"] == "proposal"]
    calls = sum(proposal["planCalls"] for proposal in proposals)
    evaluations = sum(proposal["evaluatedViewpoints"] for proposal in proposals)
    if calls != work["planCalls"] or evaluations != work["viewpointEvaluations"]:
        raise ValueError("fixture global work counters disagree with recorded proposals")
    return {"schemaVersion": "coverage.fixture-result.v1", "missionId": request["missionId"],
        "status": "completed" if targets <= seen else "partial", "targetVoxels": len(targets),
        "observedVoxels": len(seen), "observedCoverage": len(seen)/len(targets),
        "unresolvedVoxelIds": sorted(targets-seen), "roiObservations": observations,
        "repeatedRoiObservations": repeated, "commands": len(paths),
        "succeededCommands": sum(command["outcome"] == "succeeded" for command in paths),
        "unknownCommands": sum(command["outcome"] == "unknown" for command in paths),
        "rejectedCommands": sum(command["outcome"] == "rejected" for command in paths),
        "stopAcknowledgements": len(stop_acks), "unknownStepRequested": unknown_step,
        "unknownFaultApplied": any(command["unknownInjected"] for command in paths),
        "executedFixturePathM": executed, "commandedPathM": commanded,
        "planningCalls": calls, "viewpointEvaluations": evaluations,
        "lastReportReason": reports[-1]["payload"]["reason"] if reports else None,
        "observationModel": "ideal_known_map_fixture_raycast", "physicalExecution": False,
        "physicalMeasurements": False, "physicalValidation": False,
        "clockModel": "deterministic_virtual_milliseconds_not_wall_time",
        "maxVirtualDurationMs": VIRTUAL_DURATION_MS}


def run_fixture(request_path, output, *, unknown_step=None):
    from tangying_robot_gateway.spatial_mission import (
        CoverageLedger,
        EventJournal,
        SpatialMissionRunner,
        TaskCoordinator,
    )
    if output.exists():
        raise FileExistsError("refusing to overwrite fixture evidence: " + str(output))
    if unknown_step is not None and (type(unknown_step) is not int or not 1 <= unknown_step <= 256):
        raise ValueError("unknown_step must be an actual command index in 1..256")
    if request_path.stat().st_size > 32_000_000:
        raise ValueError("coverage request exceeds 32 MB")
    request_bytes = request_path.read_bytes()
    request = json.loads(request_bytes)
    config, records = validate_request(request)
    source_bytes = {name: (ROOT/name).read_bytes() for name in SOURCES}
    output.mkdir(parents=True, exist_ok=False)
    (output/"request.json").write_bytes(request_bytes)
    for name, data in source_bytes.items():
        target = output/"source"/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    manifest = {"schemaVersion": "coverage.fixture-artifacts.v1", "state": "running",
        "sourceHashes": {name: sha(data) for name, data in source_bytes.items()},
        "gitHead": head.stdout.strip() if head.returncode == 0 else None,
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "numpy": importlib.metadata.version("numpy")},
        "unknownStep": unknown_step, "physicalExecution": False}
    write_json(output/"manifest.json", manifest)
    journals = {name: EventJournal(output/name) for name in LOGS[:4]}
    first = next(iter(records.values()))["volume"]
    ledger = CoverageLedger(request["missionId"], first["worldFrameRevision"],
        request["scope"]["targetVoxelIds"], geometry_id=request["scope"]["geometryId"],
        journal_path=output/"coverage-ledger.jsonl")
    coordinator = TaskCoordinator(request["missionId"], first["worldFrameRevision"],
                                  journal_path=output/"coordination.jsonl")
    ports = FixturePorts(records, config, journals, unknown_step)
    work = {"planCalls": 0, "viewpointEvaluations": 0, "exhausted": False}
    factory = lambda snapshot: BudgetedPolicy(snapshot, records[snapshot.volume["robotId"]],
                            request, config, ports, work, journals["events.jsonl"])
    runner = SpatialMissionRunner(factory, ports, ports, ports, ledger, coordinator,
        clock_ms=lambda: ports.now, max_steps=config.max_legs, max_travel_m=config.max_travel_m,
        max_duration_ms=VIRTUAL_DURATION_MS, max_snapshot_age_ms=60_000, lease_ms=60_000)
    failure = None
    try:
        journals["events.jsonl"].append("fixture_started", {"unknownStep": unknown_step,
            "physicalExecution": False, "observationModel": "ideal_known_map_fixture_raycast"})
        for _ in range(config.max_legs+1):
            before = ports.command_count
            for robot in records:
                result = runner.step(robot)
                if result.get("termination") == "completed":
                    break
                if result["reason"] == "execution_uncertain" and not result["stopAcknowledged"]:
                    raise RuntimeError("fixture has an unacknowledged stop and cannot continue")
            if ledger.targets <= ledger.observed_ids or work["exhausted"] or ports.command_count == before:
                break
        journals["events.jsonl"].append("fixture_finished", {"work": work,
            "virtualNowMs": ports.now, "ledger": ledger.summary()})
    except Exception as error:  # noqa: BLE001 - preserve failed fixture evidence before re-raising.
        failure = error
        journals["events.jsonl"].append("fixture_failed", {"errorType": type(error).__name__, "error": str(error), "work": work})
    finally:
        snapshots = {name: journal.events for name, journal in journals.items()}
        for journal in journals.values():
            journal.close()
        ledger.journal.close()
        coordinator.journal.close()
    summary = recompute_summary(snapshots["captures.jsonl"], snapshots["commands.jsonl"],
              snapshots["reports.jsonl"], snapshots["events.jsonl"], request, work, unknown_step)
    summary["ledgerCoverage"] = ledger.summary()["coverage"]
    if failure is not None:
        summary.update(status="software_error", errorType=type(failure).__name__, error=str(failure))
    write_json(output/"summary.json", summary)
    manifest["state"] = "failed" if failure is not None else "finalized"
    if any((ROOT/name).read_bytes() != data for name, data in source_bytes.items()):
        manifest["state"] = "invalid_source_mutation"
    manifest["files"] = {str(path.relative_to(output)): {"bytes": path.stat().st_size,
        "sha256": sha(path.read_bytes())} for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "manifest.json"}
    write_json(output/"manifest.json", manifest)
    if failure is not None:
        raise failure
    if manifest["state"] != "finalized":
        raise RuntimeError("source changed during fixture; keep artifacts but exclude from results")
    return summary


def verify_fixture(directory):
    from tangying_robot_gateway.spatial_mission import EventJournal

    manifest = json.loads((directory/"manifest.json").read_text())
    required = {"request.json", "summary.json", *LOGS, *("source/"+name for name in SOURCES)}
    if (manifest.get("schemaVersion") != "coverage.fixture-artifacts.v1"
            or manifest.get("state") != "finalized" or set(manifest.get("files", {})) != required
            or set(manifest.get("sourceHashes", {})) != set(SOURCES)):
        raise ValueError("fixture manifest contract mismatch or unfinished run")
    for name, expected in manifest["files"].items():
        data = (directory/name).read_bytes()
        if len(data) != expected["bytes"] or sha(data) != expected["sha256"]:
            raise ValueError("fixture artifact bytes changed: "+name)
    for name, expected in manifest["sourceHashes"].items():
        if sha((directory/"source"/name).read_bytes()) != expected or sha((ROOT/name).read_bytes()) != expected:
            raise ValueError("fixture source snapshot mismatch")
    logs = {}
    for name in LOGS:
        journal = EventJournal(directory/name)
        logs[name] = journal.events
        journal.close()
    request = json.loads((directory/"request.json").read_text())
    validate_request(request)
    final = logs["events.jsonl"][-1]
    if final["kind"] != "fixture_finished":
        raise ValueError("fixture has no completed software run record")
    rebuilt = recompute_summary(logs["captures.jsonl"], logs["commands.jsonl"],
        logs["reports.jsonl"], logs["events.jsonl"], request, final["payload"]["work"], manifest["unknownStep"])
    captures = [event["payload"] for event in logs["captures.jsonl"]]
    receipts = logs["coverage-ledger.jsonl"][1:]
    if len(receipts) != len(captures):
        raise ValueError("core ledger observation count disagrees with raw captures")
    for captured, event in zip(captures, receipts, strict=True):
        expected = {key: captured[key] for key in ("robotId", "sourceId", "sequence", "observedAtMs",
                                                   "evidenceId", "classified")}
        expected["fingerprint"] = digest(expected)
        if event["kind"] != "observation" or event["payload"] != expected:
            raise ValueError("core ledger receipt is not bound to its raw capture")
    tasks = {event["payload"]["taskId"]: event["payload"]
             for event in logs["coordination.jsonl"] if event["kind"] == "task"}
    commands = [event["payload"] for event in logs["commands.jsonl"]]
    acks = [event["payload"] for event in logs["events.jsonl"]
            if event["kind"] == "fixture_stop_acknowledged"]
    for index, command in enumerate(commands):
        task = tasks.get(command["taskId"])
        if (task is None or task["candidate"]["path"] != command["path"]
                or task["pathDigest"] != digest(command["path"])
                or task["robotId"] != command["robotId"]):
            raise ValueError("fixture command is not bound to a durable core task")
        if command["outcome"] == "unknown":
            acknowledgements = [ack for ack in acks if ack["taskId"] == command["taskId"]]
            if (task["state"] != "stopped" or len(acknowledgements) != 1
                    or acknowledgements[0]["acknowledged"] is not True
                    or acknowledgements[0]["nowMs"] < command["nowMs"]
                    or any(later["dispatchAtMs"] <= acknowledgements[0]["nowMs"] for later in commands[index+1:])):
                raise ValueError("unknown fixture command resumed without a durable positive stop acknowledgement")
    config, _records = validate_request(request)
    if (len(commands) > config.max_legs or rebuilt["commandedPathM"] > config.max_travel_m+1e-9
            or rebuilt["planningCalls"] > config.max_total_plan_calls
            or rebuilt["viewpointEvaluations"] > config.max_total_viewpoint_evaluations):
        raise ValueError("fixture exceeded its declared global budgets")
    ledger_seen = {voxel for event in receipts for voxel, _value in event["payload"]["classified"]}
    ledger_coverage = len(ledger_seen & set(request["scope"]["targetVoxelIds"]))/len(request["scope"]["targetVoxelIds"])
    rebuilt["ledgerCoverage"] = ledger_coverage
    stored = json.loads((directory/"summary.json").read_text())
    if rebuilt != stored or stored["observedCoverage"] != ledger_coverage:
        raise ValueError("fixture summary or core ledger contradicts capture evidence")
    return {"verified": True, "status": stored["status"], "observedCoverage": stored["observedCoverage"],
            "verification": "hash_chains_and_recorded_capture_reaggregation_not_physical_reexecution",
            "physicalValidation": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--request", type=pathlib.Path)
    mode.add_argument("--verify", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--unknown-step", type=int)
    args = parser.parse_args(argv)
    if args.verify:
        if args.output is not None or args.unknown_step is not None:
            parser.error("--verify does not accept --output or --unknown-step")
        result = verify_fixture(args.verify)
    else:
        if args.output is None:
            parser.error("--request requires a new --output directory")
        result = run_fixture(args.request, args.output, unknown_step=args.unknown_step)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
