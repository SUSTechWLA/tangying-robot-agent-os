#!/usr/bin/env python3
"""Three-embodiment unknown-map exploration through the production mission runner.

Single-process ideal sensor/localization/controller fixture only: no ROS, DDS,
Gazebo, hardware, LLM interpretation, gait or flight dynamics are exercised.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "robot/gateway"))
from tangying_robot_gateway.spatial_exploration import (
    ExplorationConfig,
    SensorModel,
    SpatialExplorer,
)
from tangying_robot_gateway.spatial_mission import (
    CoverageLedger,
    DriverResult,
    EventJournal,
    SpatialMissionRunner,
    SpatialSnapshot,
    TaskCoordinator,
)
from tangying_robot_gateway.spatial_navigation import SpatialPathPlanner

from scripts import spatial_exploration_benchmark as benchmark

SOURCES = ("scripts/run_heterogeneous_exploration_fixture.py", *benchmark.SOURCES)
SCHEMA = "spatial.trio.fixture.v1"
KINDS = ("ugv", "uav", "legged")
LOGS = ("events.jsonl", "coordination.jsonl", "coverage-ledger.jsonl")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate_config(value):
    fields = {"schemaVersion", "seeds", "maxRounds", "maxSteps", "maxTravelM", "sensor", "exploration", "taskText"}
    if type(value) is not dict or set(value) != fields or value["schemaVersion"] != SCHEMA:
        raise ValueError("invalid trio fixture configuration")
    if (type(value["seeds"]) is not list or not 1 <= len(value["seeds"]) <= 20
            or len(set(value["seeds"])) != len(value["seeds"])
            or any(type(seed) is not int or not 0 <= seed < 2**31 for seed in value["seeds"])):
        raise ValueError("seeds must be unique bounded integers")
    for field, maximum in (("maxRounds", 100), ("maxSteps", 300)):
        if type(value[field]) is not int or not 1 <= value[field] <= maximum:
            raise ValueError("invalid bounded episode budget")
    if type(value["maxTravelM"]) not in (int, float) or not 0 < value["maxTravelM"] <= 1000:
        raise ValueError("invalid travel budget")
    if type(value["taskText"]) is not str or not 1 <= len(value["taskText"]) <= 4096:
        raise ValueError("task text required")
    SensorModel(**value["sensor"])
    ExplorationConfig(**value["exploration"])
    return copy.deepcopy(value)


def build_scene(seed):
    scene = benchmark.build_extended_scene("warehouse_aisles", seed)
    scene.scene_id = "trio_unknown_warehouse"
    scene.starts["legged"] = benchmark.pose(2.5, 4.5, 1.5, math.pi / 2)
    scene.policies["legged"] = {**scene.policies["ugv"], "bodyHalfExtents": [.22, .16, .20],
                                "maxSlopeRad": .5, "maxStepM": .35}
    scene.faults = []
    scene.metadata.update(embodiments=list(KINDS), leggedDynamicsValidated=False,
                          supportAssumption="ideal measured flat load-bearing floor")
    for robot in KINDS:
        volume = copy.deepcopy(scene.truth)
        volume["robotId"] = robot
        SpatialPathPlanner(volume, scene.policies[robot]).plan(scene.starts[robot], scene.starts[robot])
    return scene


class Ports:
    def __init__(self, scene, config, journal, faulted):
        self.scene, self.journal, self.faulted = scene, journal, faulted
        self.truth_ports = benchmark.TruthPorts(scene, SensorModel(**config["sensor"]))
        self.volumes = {robot: benchmark.blank_volume(scene, robot) for robot in KINDS}
        self.positions = copy.deepcopy(scene.starts)
        self.pose_seq = dict.fromkeys(KINDS, 1)
        self.pose_stamp = dict.fromkeys(KINDS, 1000)
        self.capture_seq = dict.fromkeys(KINDS, 0)
        self.now, self.round, self.pending = 1000, -1, []
        self.applied = set()
        self.fault_plan = ([{"kind": "disconnect", "robotId": "legged", "fromRound": 2, "durationRounds": 3},
                            {"kind": "driver_rejection", "robotId": "legged", "fromRound": 3},
                            {"kind": "unknown_execution", "robotId": "uav", "fromRound": 5}] if faulted else [])

    def add(self, kind, **payload):
        self.journal.append(kind, {"nowMs": self.now, "round": self.round, **payload})

    def disconnected(self, robot):
        return self.faulted and robot == "legged" and 2 <= self.round < 5

    def apply_fault(self, kind, **payload):
        if kind not in self.applied:
            self.applied.add(kind)
            self.add("fault_applied", faultKind=kind, **payload)

    def begin_round(self, index):
        self.round = index
        for item in self.pending[:]:
            if not self.disconnected(item["sender"]) and not self.disconnected(item["recipient"]):
                benchmark.merge_measurements(self.volumes[item["recipient"]], item["classified"], item["observedAtMs"])
                self.add("map_relay", sender=item["sender"], recipient=item["recipient"],
                         evidenceId=item["evidenceId"], replayed=True, bytes=item["bytes"])
                self.pending.remove(item)

    def snapshot(self, robot):
        return SpatialSnapshot(copy.deepcopy(self.volumes[robot]), self.positions[robot][:],
                               self.pose_seq[robot], self.pose_stamp[robot], f"fixture-localizer-{robot}")

    def capture(self, robot):
        self.now += 1
        self.capture_seq[robot] += 1
        classified = self.truth_ports.observe(self.positions[robot])
        benchmark.merge_measurements(self.volumes[robot], classified, self.now)
        packet = {"sourceId": f"fixture-sensor-{robot}", "sequence": self.capture_seq[robot],
                  "observedAtMs": self.now, "worldFrameRevision": self.scene.truth["worldFrameRevision"],
                  "geometryId": self.scene.identity, "classified": classified,
                  "evidenceId": f"fixture-{robot}-{self.capture_seq[robot]}"}
        self.add("capture", robotId=robot, pose=self.positions[robot][:],
                 **{key: value for key, value in packet.items() if key != "classified"},
                 classified=sorted(map(list, classified.items())), physicalMeasurement=False)
        for peer in KINDS:
            if peer == robot:
                continue
            item = {"sender": robot, "recipient": peer, "classified": classified,
                    "evidenceId": packet["evidenceId"], "observedAtMs": self.now,
                    "bytes": len(benchmark.canonical(packet))}
            if self.disconnected(robot) or self.disconnected(peer):
                self.pending.append(item)
                self.apply_fault("disconnect", robotId="legged", scope="occupancy_map_relay_only")
                self.add("map_buffered", sender=robot, recipient=peer, evidenceId=packet["evidenceId"], bytes=item["bytes"])
            else:
                benchmark.merge_measurements(self.volumes[peer], classified, self.now)
                self.add("map_relay", sender=robot, recipient=peer, evidenceId=packet["evidenceId"], replayed=False, bytes=item["bytes"])
        return packet

    def check_cancel(self):
        return None

    def execute(self, robot, path, task, deadline):
        before = self.positions[robot][:]
        planner = SpatialPathPlanner(self.volumes[robot], self.scene.policies[robot])
        known_clear = all(planner.segment_clear(a[:3], b[:3]) for a, b in itertools.pairwise(path["waypoints"]))
        truth_clear = self.truth_ports.path_clear(path, self.scene.policies[robot],
            [(self.positions[peer], self.scene.policies[peer]) for peer in KINDS if peer != robot])
        valid = known_clear and truth_clear and self.now < deadline and path["waypoints"][0] == before
        injected = None
        if valid and self.faulted:
            if robot == "legged" and self.round >= 3 and "driver_rejection" not in self.applied:
                injected = "driver_rejection"
            elif robot == "uav" and self.round >= 5 and "unknown_execution" not in self.applied:
                injected = "unknown_execution"
        if injected:
            self.apply_fault(injected, robotId=robot, taskId=task)
        outcome = "unknown" if injected == "unknown_execution" else "rejected" if not valid or injected else "succeeded"
        dispatch_ms = self.now
        self.now += 10
        if outcome == "succeeded":
            self.positions[robot] = path["waypoints"][-1][:]
            self.pose_seq[robot] += 1
            self.pose_stamp[robot] = self.now
        self.add("command", robotId=robot, taskId=task, dispatchAtMs=dispatch_ms, deadlineMs=deadline,
                 path=path, pathDigest=benchmark.digest(path), startPose=before, finalPose=self.positions[robot][:],
                 knownClear=known_clear, truthClear=truth_clear, outcome=outcome, injectedFault=injected,
                 physicalExecution=False)
        return DriverResult(outcome, task, benchmark.digest(path))

    def stop(self, robot, task):
        self.now += 1
        self.add("stop_acknowledged", robotId=robot, taskId=task, physicalStopMeasured=False,
                 acknowledged=True, basis="positive_ideal_fixture_controller_receipt")
        return True

    def publish(self, value):
        self.add("progress", report=value)


class LoggedExplorer:
    def __init__(self, snapshot, scene, config, ports):
        self.snapshot, self.ports = snapshot, ports
        self.delegate = SpatialExplorer(snapshot.volume, scene.policies[snapshot.volume["robotId"]],
                                        SensorModel(**config["sensor"]), ExplorationConfig(**config["exploration"]))

    def propose(self, pose, **kwargs):
        started = time.perf_counter()
        proposal = self.delegate.propose(pose, **kwargs)
        self.ports.add("proposal", robotId=self.snapshot.volume["robotId"],
                       mapHash=benchmark.digest(self.snapshot.volume), planningMs=(time.perf_counter()-started)*1000,
                       proposal=proposal)
        return proposal


def read_journal(path):
    journal = EventJournal(path)
    try:
        return journal.events
    finally:
        journal.close()


def evaluate(directory):
    frozen = json.loads((directory / "input.json").read_text())
    truth, targets = frozen["scene"]["truth"], set(frozen["scene"]["roiVoxelIds"])
    events = read_journal(directory / "events.jsonl")
    if not events or events[-1]["kind"] != "episode_end":
        raise ValueError("episode has no terminal record")
    covered, by_robot, marginal = set(), {robot: set() for robot in KINDS}, dict.fromkeys(KINDS, 0)
    commands, acks, applied, pending_unknown = [], [], [], {}
    captures = {}
    for event in events:
        kind, value = event["kind"], event["payload"]
        if kind == "capture":
            if value["evidenceId"] in captures:
                raise ValueError("duplicate capture identity")
            captures[value["evidenceId"]] = value
            for voxel, occupancy in value["classified"]:
                if type(voxel) is not int or not 0 <= voxel < len(truth["cells"]) or truth["cells"][voxel] != occupancy:
                    raise ValueError("capture contradicts evaluator truth")
            observed = {voxel for voxel, _ in value["classified"]} & targets
            marginal[value["robotId"]] += len(observed - covered)
            covered.update(observed)
            by_robot[value["robotId"]].update(observed)
        elif kind == "command":
            if pending_unknown:
                raise ValueError("dispatch occurred before positive unknown stop acknowledgement")
            if value["pathDigest"] != benchmark.digest(value["path"]):
                raise ValueError("command path digest mismatch")
            commands.append(value)
            if value["outcome"] == "unknown":
                pending_unknown[value["taskId"]] = {"robotId": value["robotId"], "nowMs": value["nowMs"]}
        elif kind == "stop_acknowledged":
            unknown = pending_unknown.get(value["taskId"], {})
            if (value["acknowledged"] is not True or unknown.get("robotId") != value["robotId"]
                    or value["nowMs"] <= unknown.get("nowMs", 0)):
                raise ValueError("stop receipt does not bind preceding unknown command")
            del pending_unknown[value["taskId"]]
            acks.append({"taskId": value["taskId"], "robotId": value["robotId"], "atMs": value["nowMs"], "eventSequence": event["sequence"]})
        elif kind == "fault_applied":
            applied.append(value["faultKind"])
    ledger = read_journal(directory / "coverage-ledger.jsonl")
    ledger_ids = {event["payload"]["evidenceId"] for event in ledger if event["kind"] == "observation"}
    if ledger_ids != set(captures):
        raise ValueError("runner did not durably bind every capture to coverage ledger")
    for event in ledger:
        if event["kind"] != "observation":
            continue
        payload = event["payload"]
        capture = captures[payload["evidenceId"]]
        if any(payload[key] != capture[key] for key in (
                "robotId", "sourceId", "sequence", "observedAtMs", "evidenceId", "classified")):
            raise ValueError("ledger observation differs from recorded sensor capture")
    coordination = read_journal(directory / "coordination.jsonl")
    stop_tasks = {event["payload"].get("taskId") for event in coordination
                  if event["payload"].get("state") == "stopped"}
    if any(ack["taskId"] not in stop_tasks for ack in acks):
        raise ValueError("positive stop receipt absent from coordinator journal")
    last = events[-1]["payload"]
    return {"schemaVersion": "spatial.trio.result.v1", "seed": frozen["scene"]["seed"],
        "faulted": frozen["faulted"], "status": "software_error" if last.get("error") else "completed" if covered >= targets else "partial",
        "stopReason": last["reason"], "softwareError": last.get("error"),
        "coverageObserved": len(covered), "coverageDenominator": len(targets), "coverageFraction": len(covered)/len(targets),
        "unresolvedVoxelIds": sorted(targets-covered), "perRobotObservedVoxels": {robot: len(ids) for robot, ids in by_robot.items()},
        "perRobotFirstObservedVoxels": marginal, "commands": len(commands),
        "perRobotCommands": {robot: {outcome: sum(c["robotId"] == robot and c["outcome"] == outcome for c in commands)
                                    for outcome in ("succeeded", "rejected", "unknown")} for robot in KINDS},
        "executedPathM": sum(math.dist(a[:3], b[:3]) for c in commands if c["outcome"] == "succeeded" for a, b in itertools.pairwise(c["path"]["waypoints"])),
        "faultApplications": applied, "faultSkips": [f["kind"] for f in frozen["faultPlan"] if f["kind"] not in applied],
        "stopAcknowledgements": acks, "pendingUnknownTasks": pending_unknown,
        "independentSafetyRefusals": sum(not c["truthClear"] or not c["knownClear"] for c in commands),
        "relayedMapMessages": sum(e["kind"] == "map_relay" for e in events),
        "replayedMapMessages": sum(e["kind"] == "map_relay" and e["payload"]["replayed"] for e in events),
        "pendingMapMessages": last["pendingMapMessages"], "physicalExecution": False,
        "naturalLanguageInterpreted": False, "rosDDSTested": False,
        "occupancyGeometryHash": benchmark.occupancy_geometry_hash(truth), "eventChainHead": events[-1]["hash"]}


def run_episode(scene, config, faulted, directory):
    directory.mkdir()
    events = EventJournal(directory / "events.jsonl")
    ports = Ports(scene, config, events, faulted)
    frozen = {"scene": benchmark.scene_input(scene), "config": config, "faulted": faulted,
              "faultPlan": ports.fault_plan, "taskTextInterpretation": "fixed fixture scenario, not LLM execution"}
    benchmark.write_json(directory / "input.json", frozen)
    mission = f"trio-{scene.seed}-{'fault' if faulted else 'normal'}"
    ledger = CoverageLedger(mission, scene.truth["worldFrameRevision"], scene.target_ids,
                            geometry_id=scene.identity, max_age_ms=60_000, journal_path=directory / "coverage-ledger.jsonl")
    coordinator = TaskCoordinator(mission, scene.truth["worldFrameRevision"], journal_path=directory / "coordination.jsonl")
    runner = SpatialMissionRunner(lambda snapshot: LoggedExplorer(snapshot, scene, config, ports), ports, ports, ports,
                ledger, coordinator, clock_ms=lambda: ports.now, max_steps=config["maxSteps"], max_travel_m=config["maxTravelM"],
                max_duration_ms=600_000, max_snapshot_age_ms=1000, lease_ms=5000)
    reason, error = "round_budget", None
    ports.add("episode_start", missionId=mission, taskText=config["taskText"], faultPlan=ports.fault_plan,
              initialKnownVoxelCount={robot: sum(v != -1 for v in ports.volumes[robot]["cells"]) for robot in KINDS},
              executionModel="single_process_serial_runner_shared_ledger", physicalExecution=False,
              disconnectScope="occupancy_map_relay_only_shared_ledger_and_coordinator_remain_available")
    try:
        for index in range(config["maxRounds"]):
            ports.begin_round(index)
            reports = [runner.step(robot) for robot in KINDS]
            if ledger.targets <= ledger.observed_ids:
                reason = "coverage_complete"
                break
            if all(report["reason"] in {"budget_exhausted", "travel_budget"} for report in reports):
                reason = "mission_budget"
                break
    except Exception as exc:  # noqa: BLE001 - preserve software failures as experimental outcomes
        reason, error = "software_error", f"{type(exc).__name__}: {exc}"
    finally:
        ports.add("episode_end", reason=reason, error=error, pendingMapMessages=len(ports.pending))
        events.close()
        ledger.journal.close()
        coordinator.journal.close()
    summary = evaluate(directory)
    benchmark.write_json(directory / "summary.json", summary)
    return summary


def summarize_campaign(results):
    return {"episodes": results, "uniqueGeometryN": len({r["occupancyGeometryHash"] for r in results}),
            "completed": sum(r["status"] == "completed" for r in results),
            "partial": sum(r["status"] == "partial" for r in results),
            "softwareErrors": sum(r["status"] == "software_error" for r in results), "physicalValidation": False}


def campaign(config_path, output):
    raw = config_path.read_bytes()
    config = validate_config(json.loads(raw))
    output.mkdir(parents=True, exist_ok=False)
    source_hashes = {}
    for name in SOURCES:
        data = (ROOT/name).read_bytes()
        source_hashes[name] = sha(data)
        destination = output/"source"/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (output/"config.json").write_bytes(raw)
    benchmark.write_json(output/"environment.json", {"python": sys.version, "platform": platform.platform()})
    scenes = {seed: build_scene(seed) for seed in config["seeds"]}
    benchmark.write_json(output/"preregistration.json", {"seeds": config["seeds"], "conditions": ["normal", "fault"],
        "inputHashes": {str(seed): benchmark.digest(benchmark.scene_input(scene)) for seed, scene in scenes.items()},
        "coverageThreshold": 1.0, "coverageROI": "all interior free and occupied voxels; no posthoc pruning",
        "execution": "production SpatialMissionRunner + TaskCoordinator + CoverageLedger; ideal ports, serial single process",
        "faultSchedule": "legged map-relay partition rounds 2..4; first eligible legged command from round 3 rejected; first eligible UAV command from round 5 unknown",
        "limits": ["No hardware, DDS, SLAM accuracy, flight/gait/dynamics or concurrent avoidance validation.",
                   "Map exchange partition leaves shared in-process coverage ledger/coordinator and stop port available.",
                   "Task text documents fixed intent; no LLM natural-language interpretation in this fixture."]})
    manifest = {"schemaVersion": SCHEMA, "state": "running", "sources": source_hashes, "episodes": []}
    benchmark.write_json(output/"manifest.json", manifest)
    results = []
    for seed, faulted in itertools.product(config["seeds"], (False, True)):
        name = f"seed-{seed}-{'fault' if faulted else 'normal'}"
        result = run_episode(copy.deepcopy(scenes[seed]), config, faulted, output/name)
        results.append(result)
        manifest["episodes"].append(name)
        benchmark.write_json(output/"manifest.json", manifest)
        print(json.dumps({"episode": name, "status": result["status"], "coverage": result["coverageFraction"], "commands": result["commands"], "faults": result["faultApplications"]}), flush=True)
    benchmark.write_json(output/"summary.json", summarize_campaign(results))
    manifest["state"] = "finalized" if all(sha((ROOT/name).read_bytes()) == value for name, value in source_hashes.items()) else "source_changed"
    manifest["files"] = {str(path.relative_to(output)): sha(path.read_bytes()) for path in sorted(output.rglob("*")) if path.is_file() and path != output/"manifest.json"}
    benchmark.write_json(output/"manifest.json", manifest)
    if manifest["state"] != "finalized":
        raise ValueError("source changed during campaign")
    return results


def verify(output):
    manifest = json.loads((output/"manifest.json").read_text())
    if manifest["schemaVersion"] != SCHEMA or manifest["state"] != "finalized":
        raise ValueError("campaign not finalized")
    config = validate_config(json.loads((output/"config.json").read_text()))
    expected_episodes = [f"seed-{seed}-{'fault' if faulted else 'normal'}"
                         for seed, faulted in itertools.product(config["seeds"], (False, True))]
    if manifest["episodes"] != expected_episodes or set(manifest["sources"]) != set(SOURCES):
        raise ValueError("manifest omitted or added an episode or required source")
    required = {"config.json", "environment.json", "preregistration.json", "summary.json"}
    required.update("source/" + name for name in SOURCES)
    required.update(f"{episode}/{name}" for episode in expected_episodes
                    for name in ("input.json", "summary.json", *LOGS))
    actual = {str(path.relative_to(output)) for path in output.rglob("*")
              if path.is_file() and path != output/"manifest.json"}
    if set(manifest["files"]) != required or actual != required:
        raise ValueError("campaign file set differs from required artifact inventory")
    for name, expected in manifest["files"].items():
        path = (output/name).resolve()
        if output.resolve() not in path.parents or sha(path.read_bytes()) != expected:
            raise ValueError("artifact hash mismatch or path escape: " + name)
    for name, expected in manifest["sources"].items():
        if sha((output/"source"/name).read_bytes()) != expected or sha((ROOT/name).read_bytes()) != expected:
            raise ValueError("replay source mismatch: " + name)
    summaries = []
    registration = json.loads((output/"preregistration.json").read_text())
    if (registration["seeds"] != config["seeds"] or registration["conditions"] != ["normal", "fault"]
            or set(registration["inputHashes"]) != set(map(str, config["seeds"]))):
        raise ValueError("preregistered episode axes differ from configuration")
    for name in manifest["episodes"]:
        frozen = json.loads((output/name/"input.json").read_text())
        if (frozen["config"] != config or frozen["faulted"] != name.endswith("-fault")
                or benchmark.digest(frozen["scene"]) != registration["inputHashes"].get(str(frozen["scene"]["seed"]))):
            raise ValueError("episode differs from preregistered input")
        summary = evaluate(output/name)
        if summary != json.loads((output/name/"summary.json").read_text()):
            raise ValueError("summary differs from recorded observations")
        summaries.append(summary)
    rebuilt = summarize_campaign(summaries)
    if json.loads((output/"summary.json").read_text()) != rebuilt:
        raise ValueError("campaign summary differs from episode summaries")
    return {"verifiedEpisodes": len(summaries), "passed": True, "physicalRerun": False,
            "softwareErrors": rebuilt["softwareErrors"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    if args.verify:
        result = verify(args.verify)
    elif args.config and args.output:
        rows = campaign(args.config, args.output)
        result = {"episodes": len(rows), "softwareErrors": sum(row["status"] == "software_error" for row in rows)}
    else:
        parser.error("provide --config/--output or --verify")
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["softwareErrors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
