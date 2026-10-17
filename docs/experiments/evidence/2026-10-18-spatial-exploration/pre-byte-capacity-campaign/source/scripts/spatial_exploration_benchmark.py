"""Paired offline voxel exploration experiments; no simulator or physical robot.

Production exploration and mission coordination are exercised against synthetic
sensor/driver ports. Ground truth is confined to those ports and the independent
voxel evaluator. This is geometric software evidence, never flight, gait, SLAM,
dynamic obstacle avoidance or real-world coverage validation.

Add a scene through SCENE_BUILDERS and a policy backend through BACKENDS. New
backends receive only measured navigation.volume.v1 snapshots; evaluator truth
is deliberately absent from their interface.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import importlib.metadata
import itertools
import json
import math
import pathlib
import platform
import random
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot" / "gateway"))
sys.path.insert(0, str(ROOT / "python"))

SCHEMA = "spatial.experiment.v1"
VARIANTS = {
    "baseline": {},
    "nearest_frontier": {"strategy": "nearest_frontier"},
    "no_global_reposition": {"use_global_reposition": False},
    "no_visibility_gain": {"visibility_gain": False},
    "no_coordination": {"coordination": False},
}
SOURCES = (
    "scripts/spatial_exploration_benchmark.py",
    "robot/gateway/tangying_robot_gateway/spatial_exploration.py",
    "robot/gateway/tangying_robot_gateway/spatial_mission.py",
    "robot/gateway/tangying_robot_gateway/spatial_navigation.py",
)


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def occupancy_geometry_hash(volume):
    """Layout identity excludes scene names, map IDs and seed labels."""
    return digest({key: volume[key] for key in ("cells", "shape", "origin", "resolution")})


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def flat(shape, x, y, z):
    return (z * shape[1] + y) * shape[0] + x


def pose(x, y, z, yaw=0.0):
    return [x, y, z, math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]


@dataclass
class Scene:
    scene_id: str
    seed: int
    truth: dict
    starts: dict
    policies: dict
    target_ids: set
    faults: list

    @property
    def identity(self):
        from tangying_robot_gateway.spatial_mission import geometry_id
        # Grid geometry is public; occupancy is not part of the planner identity.
        return geometry_id(self.truth)


def build_scene(scene_id, seed):
    """Seed changes physical obstacle placement; no artificial metric noise."""
    if scene_id not in {"vertical_complementarity", "branching_occlusion", "recovery_disconnect"}:
        raise ValueError(f"unknown scene plugin: {scene_id}")
    generator = random.Random(seed)
    shape = [11, 9, 7]
    nx, ny, nz = shape
    cells = [0] * math.prod(shape)
    for z, y, x in itertools.product(range(nz), range(ny), range(nx)):
        if x in (0, nx - 1) or y in (0, ny - 1) or z in (0, nz - 1):
            cells[flat(shape, x, y, z)] = 100
    obstacle_x = generator.choice([5, 6])
    # A low barrier is passable in the upper free volume and has a ground detour.
    for z, y in itertools.product(range(1, 3), range(2, 7)):
        cells[flat(shape, obstacle_x, y, z)] = 100
    if scene_id != "vertical_complementarity":
        # Offset full-height occluders create distinct branches and blind areas.
        for x, z in itertools.product(range(3, 9), range(1, nz - 1)):
            cells[flat(shape, x, 5, z)] = 100
        cells[flat(shape, 7, 5, 1)] = 0
        cells[flat(shape, 7, 5, 2)] = 0
    truth = {
        "schemaVersion": "navigation.volume.v1", "robotId": "fixture-world",
        "mapId": f"{scene_id}-{seed}", "mapRevision": "registered-basis-v1",
        "frameId": "world", "worldFrameRevision": "fixture-world-v1",
        "calibrationRevision": "fixture-calibration-v1", "resolution": 1.0,
        "origin": [0.0, 0.0, 0.0], "shape": shape, "cells": cells,
        "terrain": {"schemaVersion": "navigation.terrain.v1", "heights": [1.0] * (nx * ny),
                    "supported": [True] * (nx * ny), "slopeRadians": [0.0] * (nx * ny),
                    "sourceId": "fixture-contact-sensor", "observedAtUnixMs": 1},
    }
    shared = {"bodyHalfExtents": [0.15, 0.15, 0.15], "margin": 0.05,
              "maxExpanded": 5000, "maxWaypoints": 256}
    policies = {
        "ugv": {**shared, "mode": "terrain25d", "maxSlopeRad": 0.45,
                "maxStepM": 0.25, "referenceHeightM": 0.5},
        "uav": {**shared, "mode": "volume3d"},
    }
    starts = {"ugv": pose(2.5, 2.5, 1.5), "uav": pose(2.5, 3.5, 2.5, math.pi / 2)}
    # Coverage denominator is explicit, all interior voxels (free AND occupied).
    targets = {flat(shape, x, y, z) for z, y, x in
               itertools.product(range(1, nz - 1), range(1, ny - 1), range(1, nx - 1))}
    faults = ([{"round": 2, "robotId": "uav", "kind": "disconnect", "durationRounds": 3},
               {"round": 3, "robotId": "ugv", "kind": "driver_rejection"},
               {"round": 5, "robotId": "uav", "kind": "unknown_execution"}]
              if scene_id == "recovery_disconnect" else [])
    return Scene(scene_id, seed, truth, starts, policies, targets, faults)


SCENE_BUILDERS: dict[str, Callable] = {
    name: build_scene for name in
    ("vertical_complementarity", "branching_occlusion", "recovery_disconnect")
}


def production_backend(volume, policy, sensor, config):
    from tangying_robot_gateway.spatial_exploration import SpatialExplorer
    return SpatialExplorer(volume, policy, sensor, config)


BACKENDS = {"production_voxel": production_backend}


class Events:
    def __init__(self, path):
        self.path, self.previous, self.count = path, "0" * 64, 0
        self.stream = path.open("x")

    def add(self, kind, **payload):
        event = {"schemaVersion": "spatial.event.v1", "index": self.count,
                 "kind": kind, "previousHash": self.previous, **payload}
        # JSON object keys are strings on replay; normalize before hashing so
        # integer voxel keys cannot change canonical ordering after serialization.
        event = json.loads(json.dumps(event, allow_nan=False))
        event["hash"] = digest(event)
        self.previous = event["hash"]
        self.count += 1
        self.stream.write(canonical(event).decode() + "\n")
        self.stream.flush()
        return event

    def close(self):
        self.stream.close()


class TruthPorts:
    """Sensor and independent safety admission own truth; policy never does."""
    def __init__(self, scene, sensor):
        self.scene, self.sensor = scene, sensor

    def observe(self, position):
        from tangying_robot_gateway.spatial_exploration import raycast_observation
        observation = raycast_observation(self.scene.truth, position, self.sensor,
                                           max_rays=81, max_ray_steps=8192)
        return {index: self.scene.truth["cells"][index] for index in observation["observedVoxelIds"]}

    def path_clear(self, path, policy, other_bodies=()):
        """Separate slab implementation certifies every swept box against truth.

        This port refuses an execution; it never supplies an alternative path or
        unseen occupancy to a policy. Terrain here is a flat fixture, not a model
        for gait, nonholonomic driving or flight dynamics.
        """
        volume = self.scene.truth
        shape, resolution, origin = volume["shape"], volume["resolution"], volume["origin"]
        half = [value + policy["margin"] for value in policy["bodyHalfExtents"]]
        points = path["waypoints"]
        for first, second in itertools.pairwise(points):
            for position, other_policy in other_bodies:
                combined = [a + b + other_policy["margin"] for a, b in
                            zip(half, other_policy["bodyHalfExtents"], strict=True)]
                enter, leave = 0.0, 1.0
                for axis in range(3):
                    lower, upper = position[axis] - combined[axis], position[axis] + combined[axis]
                    delta = second[axis] - first[axis]
                    if abs(delta) < 1e-14:
                        if not lower <= first[axis] <= upper:
                            enter = 2.0
                            break
                    else:
                        a, b = (lower - first[axis]) / delta, (upper - first[axis]) / delta
                        enter, leave = max(enter, min(a, b)), min(leave, max(a, b))
                if enter <= leave:
                    return False
            for axis in range(3):
                if min(first[axis], second[axis]) - half[axis] <= origin[axis]:
                    return False
                if max(first[axis], second[axis]) + half[axis] >= origin[axis] + shape[axis] * resolution:
                    return False
            for index, value in enumerate(volume["cells"]):
                if value == 0:
                    continue
                xyz = [index % shape[0], index // shape[0] % shape[1], index // (shape[0] * shape[1])]
                enter, leave = 0.0, 1.0
                for axis in range(3):
                    lower = origin[axis] + xyz[axis] * resolution - half[axis]
                    upper = lower + resolution + 2 * half[axis]
                    delta = second[axis] - first[axis]
                    if abs(delta) < 1e-14:
                        if not lower <= first[axis] <= upper:
                            enter = 2.0
                            break
                    else:
                        a, b = (lower - first[axis]) / delta, (upper - first[axis]) / delta
                        enter, leave = max(enter, min(a, b)), min(leave, max(a, b))
                if enter <= leave:
                    return False
        return True


def blank_volume(scene, robot):
    result = copy.deepcopy(scene.truth)
    result["robotId"] = robot
    result["cells"] = [-1] * len(result["cells"])
    count = result["shape"][0] * result["shape"][1]
    result["terrain"].update(heights=[None] * count, supported=[False] * count,
                            slopeRadians=[None] * count)
    return result


def merge_measurements(volume, classified, observed_at_ms=None):
    nx, ny, _ = volume["shape"]
    for raw_index, state in classified.items():
        index = int(raw_index)
        # A new occupied measurement conservatively wins conflicting evidence.
        volume["cells"][index] = 100 if 100 in (state, volume["cells"][index]) else state
        z = index // (nx * ny)
        xy = index % (nx * ny)
        if z == 0 and state == 100:
            volume["terrain"]["heights"][xy] = 1.0
            volume["terrain"]["supported"][xy] = True
            volume["terrain"]["slopeRadians"][xy] = 0.0
    if observed_at_ms is not None:
        volume["terrain"]["observedAtUnixMs"] = max(
            volume["terrain"]["observedAtUnixMs"], observed_at_ms)


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def independent_metrics(events, target_ids, coverage_threshold, truth_volume=None):
    """Recompute outcomes from observable events and evaluator-only ROI IDs."""
    seen, observations, repeated, per_robot = set(), 0, 0, {}
    path_length, planning, admission, messages, message_bytes = 0.0, [], [], 0, 0
    attempted, completed, rejected, uncertain, acknowledgements = 0, 0, 0, 0, 0
    recoveries, pending_recovery, failure_reasons = 0, set(), {}
    collision_refusals, reconnected = 0, 0
    planned, applied, skipped = [], [], []
    no_gain_legs, gain_legs = 0, 0
    for event in events:
        kind = event["kind"]
        if kind == "observation":
            if truth_volume is not None:
                for raw_index, state in event["classified"].items():
                    index = int(raw_index)
                    if not 0 <= index < len(truth_volume["cells"]) or state != truth_volume["cells"][index]:
                        raise ValueError("observation contradicts evaluator truth")
            ids = set(map(int, event["classified"])) & target_ids
            if event.get("executionOutcome") == "succeeded":
                if ids - seen:
                    gain_legs += 1
                else:
                    no_gain_legs += 1
            observations += len(ids)
            repeated += len(ids & seen)
            seen |= ids
            per_robot.setdefault(event["robotId"], set()).update(ids)
        elif kind == "decision":
            planning.append(event["planningMs"])
        elif kind == "execution":
            attempted += 1
            admission.append(event["admissionMs"])
            if event["outcome"] != "succeeded":
                reason = event.get("stopReason", event["outcome"])
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
            if event["outcome"] == "succeeded":
                completed += 1
                if event["robotId"] in pending_recovery:
                    recoveries += 1
                    pending_recovery.remove(event["robotId"])
                path_length += sum(math.dist(a[:3], b[:3]) for a, b in
                                   itertools.pairwise(event["path"]["waypoints"]))
            elif event["outcome"] == "unknown":
                uncertain += 1
                pending_recovery.add(event["robotId"])
            else:
                rejected += 1
                if not event["truthSafetyAdmitted"]:
                    collision_refusals += 1
        elif kind == "communication":
            messages += event.get("messageCount", 1)
            message_bytes += event["bytes"]
            if event.get("channel") == "reconnect_snapshot":
                reconnected += 1
        elif kind == "fault_planned":
            planned.append(event["fault"])
        elif kind == "fault_applied":
            applied.append(event["fault"])
        elif kind == "fault_skipped":
            skipped.append(event["fault"])
        elif kind == "stop_acknowledged":
            acknowledgements += 1
    coverage = len(seen) / len(target_ids) if target_ids else None
    if events and events[-1]["kind"] == "episode_end":
        plan_ids = [item["faultId"] for item in planned]
        actual_ids = [item["faultId"] for item in [*applied, *skipped]]
        if len(set(plan_ids)) != len(plan_ids) or len(set(actual_ids)) != len(actual_ids) or set(plan_ids) != set(actual_ids):
            raise ValueError("fault schedule is not partitioned into applied and skipped outcomes")
    return {
        "coverageMetric": "observed_roi_voxels", "coverageDenominator": len(target_ids),
        "coverageObserved": len(seen), "coverageFraction": coverage,
        "coverageThreshold": coverage_threshold,
        "status": "completed" if coverage is not None and coverage >= coverage_threshold else "partial",
        "roiVoxelObservations": observations, "repeatedVoxelObservations": repeated,
        "repeatObservationFraction": repeated / observations if observations else None,
        "perRobotObservedVoxels": {robot: len(ids) for robot, ids in per_robot.items()},
        "pathLengthM": path_length, "planningCalls": len(planning),
        "planningMsP50": percentile(planning, 0.5), "planningMsP95": percentile(planning, 0.95),
        "planningMsMax": max(planning) if planning else None,
        "planningMsTotal": sum(planning), "messageCount": messages, "messageBytes": message_bytes,
        "admissionMsP50": percentile(admission, 0.5), "admissionMsP95": percentile(admission, 0.95),
        "attemptedLegs": attempted, "completedLegs": completed, "rejectedLegs": rejected,
        "uncertainLegs": uncertain, "faultEvents": len(applied),
        "plannedFaults": len(planned), "appliedFaults": len(applied), "skippedFaults": len(skipped),
        "faultPlan": planned, "faultApplications": applied, "faultSkips": skipped,
        "successfulGainLegs": gain_legs, "successfulNoGainLegs": no_gain_legs,
        "stopAcknowledgements": acknowledgements, "recoveredUncertainLegs": recoveries,
        "independentCollisionRefusals": collision_refusals,
        "reconnectSnapshots": reconnected,
        "failureReasons": failure_reasons,
        "energyJ": None, "localizationErrorM": None, "physicalCollisionCount": None,
        "flightDynamicsValidated": None, "hardwareSuccess": None,
    }


def read_events(path):
    previous, result = "0" * 64, []
    for index, line in enumerate(path.read_text().splitlines()):
        event = json.loads(line)
        claimed = event.pop("hash")
        if event["index"] != index or event["previousHash"] != previous or digest(event) != claimed:
            raise ValueError(f"event chain mismatch at {path}:{index + 1}")
        event["hash"] = claimed
        previous = claimed
        result.append(event)
    if not result or result[-1]["kind"] != "episode_end":
        raise ValueError("episode does not have a finalized end record")
    return result


def run_episode(scene, variant, settings, output):
    from tangying_robot_gateway.spatial_exploration import ExplorationConfig, SensorModel
    from tangying_robot_gateway.spatial_mission import CoverageLedger, TaskCoordinator
    from tangying_robot_gateway.spatial_navigation import SpatialPathPlanner

    output.mkdir()
    selected = {**VARIANTS[variant]}
    coordination = selected.pop("coordination", True)
    policy_config = ExplorationConfig(**{**settings["exploration"], **selected})
    sensor = SensorModel(**settings["sensor"])
    ports = TruthPorts(scene, sensor)
    volumes = {robot: blank_volume(scene, robot) for robot in scene.starts}
    positions = copy.deepcopy(scene.starts)
    local_seen = {robot: set() for robot in scene.starts}
    excluded = {robot: set() for robot in scene.starts}
    sequence = {robot: 0 for robot in scene.starts}
    buffer = []
    ledger = CoverageLedger(f"{scene.scene_id}-{scene.seed}-{variant}", "fixture-world-v1",
                            geometry_id=scene.identity, max_age_ms=60_000,
                            journal_path=output / "coverage-ledger.jsonl")
    coordinator = TaskCoordinator(f"{scene.scene_id}-{scene.seed}-{variant}", "fixture-world-v1",
                                  use_coordination=coordination,
                                  journal_path=output / "coordination.jsonl")
    received = {}
    journal = Events(output / "events.jsonl")
    journal.add("episode_start", sceneId=scene.scene_id, seed=scene.seed, variant=variant,
                backend=settings["backend"], publicGeometry={k: scene.truth[k] for k in
                  ("shape", "origin", "resolution")}, truthHash=digest(scene.truth),
                geometryId=scene.identity, initialPoses=positions, policies=scene.policies,
                faults=scene.faults, safetyChecksEnabled=True, physicalExecution=False,
                disconnectScope="observation_exchange_only_local_scheduler_remains_available",
                executionModel="batched_assignments_serial_geometry_admission",
                peerBodyChecksEnabled=True,
                occupancyGeometryHash=occupancy_geometry_hash(scene.truth),
                coordinationComponents={"assignmentOverlap": coordination,
                    "sharedCoverageGuidance": coordination, "mapExchange": True,
                    "executionFence": True})
    fault_states = {}
    for index, fault in enumerate(scene.faults):
        record = {"faultId": f"fault-{index}", "type": fault["kind"], "robotId": fault["robotId"],
                  "scheduledRound": fault["round"], "taskId": None, "reason": "scheduled"}
        fault_states[record["faultId"]] = {"record": record, "fault": fault, "state": "planned"}
        journal.add("fault_planned", fault=record)
    write_json(output / "evaluator-truth.json", {
        "schemaVersion": "spatial.evaluator-truth.v1", "volume": scene.truth,
        "roiVoxelIds": sorted(scene.target_ids), "policyAccess": False,
    })

    def disconnected(robot, round_index):
        return any(fault["kind"] == "disconnect" and fault["robotId"] == robot and
                   fault["round"] <= round_index < fault["round"] + fault["durationRounds"]
                   for fault in scene.faults)

    def apply_fault(kind, robot, round_index, now, reason, task_id=None):
        for item in fault_states.values():
            fault = item["fault"]
            within = (fault["round"] <= round_index < fault["round"] + fault.get("durationRounds", 1))
            if item["state"] == "planned" and fault["kind"] == kind and fault["robotId"] == robot and within:
                item["state"] = "applied"
                journal.add("fault_applied", nowMs=now, fault={**item["record"],
                    "taskId": task_id, "appliedRound": round_index, "reason": reason})

    def finish_faults(last_round, reason):
        for item in fault_states.values():
            if item["state"] == "planned":
                item["state"] = "skipped"
                why = ("episode_ended_before_scheduled_round" if item["fault"]["round"] > last_round
                       else "no_eligible_execution_or_message_at_scheduled_round")
                journal.add("fault_skipped", fault={**item["record"], "reason": why,
                            "episodeStopReason": reason})

    def deliver(packet, now, round_index):
        report = ledger.observe(packet["robotId"], packet["sourceId"], packet["sequence"],
                                packet["observedAtMs"], now, "fixture-world-v1",
                                packet["classified"], packet["evidenceId"],
                                geometry_id=scene.identity)
        if report["accepted"]:
            received.update(packet["classified"])
        recipients = [robot for robot in volumes if robot != packet["robotId"]
                      and not disconnected(robot, round_index)]
        for robot in volumes:
            if robot != packet["robotId"] and disconnected(robot, round_index):
                apply_fault("disconnect", robot, round_index, now, "withheld_observation_relay")
        if report["accepted"]:
            for robot in recipients:
                merge_measurements(volumes[robot], packet["classified"], packet["observedAtMs"])
        journal.add("communication", nowMs=now, robotId=packet["robotId"],
                    sequence=packet["sequence"], evidenceId=packet["evidenceId"],
                    bytes=len(canonical(packet)) * (1 + len(recipients)),
                    messageCount=1 + len(recipients), recipients=recipients,
                    channel="observation_upload_and_relay", acceptance=report)

    def observe(robot, now, round_index, task_id=None, outcome=None):
        sequence[robot] += 1
        classified = ports.observe(positions[robot])
        new_robot = len(set(classified) - local_seen[robot])
        new_team = len(set(classified) - set().union(*local_seen.values()))
        merge_measurements(volumes[robot], classified, now)
        local_seen[robot].update(classified)
        packet = {"robotId": robot, "sourceId": f"sensor-{robot}", "sequence": sequence[robot],
                  "observedAtMs": now, "classified": classified,
                  "evidenceId": f"{robot}-{sequence[robot]}"}
        journal.add("observation", round=round_index, nowMs=now, pose=positions[robot],
                    taskId=task_id, executionOutcome=outcome, newlyClassifiedRobotVoxels=new_robot,
                    newlyClassifiedTeamVoxels=new_team, **packet)
        if disconnected(robot, round_index):
            buffer.append(packet)
            apply_fault("disconnect", robot, round_index, now, "buffered_observation_upload", task_id)
        else:
            deliver(packet, now, round_index)

    stop_reason = "round_budget"
    last_round = -1
    try:
        for robot_index, robot in enumerate(positions):
            observe(robot, 1000 + robot_index, -1)
        for round_index in range(settings["maxRounds"]):
            last_round = round_index
            now = 10_000 + round_index * 10_000
            # Buffered packets preserve source order; no fabricated fresh timestamp.
            for packet in list(buffer):
                if not disconnected(packet["robotId"], round_index):
                    deliver(packet, now, round_index)
                    buffer.remove(packet)
            for robot, volume in volumes.items():
                if round_index > 0 and disconnected(robot, round_index - 1) and not disconnected(robot, round_index):
                    # Catch up only actual registered evidence received while this
                    # peer was partitioned. This is a measured message, not a free
                    # omniscient map synchronization hidden from the byte metric.
                    payload = {"classified": received, "robotId": robot, "worldRevision": "fixture-world-v1"}
                    merge_measurements(volume, received)
                    journal.add("communication", nowMs=now, robotId=robot,
                                channel="reconnect_snapshot", classified=received,
                                recipients=[robot], messageCount=1, bytes=len(canonical(payload)))
            assignments = []
            # Both robots propose against their current measured snapshots before
            # either moves. Target overlap therefore has an opportunity to affect
            # assignment, while dispatch still keeps exactly one execution active.
            for robot_index, robot in enumerate(positions):
                tick = now + robot_index
                start = time.perf_counter_ns()
                explorer = BACKENDS[settings["backend"]](volumes[robot], scene.policies[robot], sensor, policy_config)
                proposal = explorer.propose(positions[robot], excluded_targets=excluded[robot],
                    observed_targets=(ledger.observed_ids if coordination and not disconnected(robot, round_index)
                                      else local_seen[robot]))
                planning_ms = (time.perf_counter_ns() - start) / 1e6
                journal.add("decision", round=round_index, nowMs=tick, robotId=robot,
                            knownMapHash=digest(volumes[robot]), planningMs=planning_ms, proposal=proposal)
                candidates = proposal.get("candidates", [])
                if not candidates and proposal.get("candidate"):
                    candidates = [proposal["candidate"]]
                unknown_scheduled = any(fault["kind"] == "unknown_execution" and fault["robotId"] == robot
                                        and fault["round"] == round_index for fault in scene.faults)
                # The unknown-operation lease can expire before the next robot's
                # dispatch slot; other batched assignments retain their own lease.
                lease_ms = 500 + robot_index * 2000 if unknown_scheduled else 9000
                task = coordinator.assign(robot, candidates, tick, lease_ms=lease_ms)
                if task is None:
                    continue
                assignments.append((robot_index, robot, task))
                journal.add("assignment", round=round_index, nowMs=tick, robotId=robot, task=task)
            for robot_index, robot, task in assignments:
                tick = now + 100 + robot_index * 2000
                candidate, task_id = task["candidate"], task["taskId"]
                coordinator.start(task_id, robot, tick)
                fault_kinds = {fault["kind"] for fault in scene.faults
                               if fault["round"] == round_index and fault["robotId"] == robot}
                admitted_at = time.perf_counter_ns()
                validator = SpatialPathPlanner(volumes[robot], scene.policies[robot])
                fresh_known_safe = all(validator.segment_clear(a[:3], b[:3]) for a, b in
                                       itertools.pairwise(candidate["path"]["waypoints"]))
                safe = ports.path_clear(candidate["path"], scene.policies[robot],
                    [(positions[peer], scene.policies[peer]) for peer in positions if peer != robot])
                admission_ms = (time.perf_counter_ns() - admitted_at) / 1e6
                if not safe or not fresh_known_safe or "driver_rejection" in fault_kinds:
                    outcome = "rejected"
                    if safe and fresh_known_safe and "driver_rejection" in fault_kinds:
                        apply_fault("driver_rejection", robot, round_index, tick + 1,
                                    "injected_driver_refusal", task_id)
                    excluded[robot].add(candidate["targetId"])
                    coordinator.finish(task_id, robot, outcome, tick + 1)
                elif "unknown_execution" in fault_kinds:
                    outcome = "unknown"
                    apply_fault("unknown_execution", robot, round_index, tick + 1,
                                "injected_unknown_response", task_id)
                    coordinator.finish(task_id, robot, outcome, tick + 1)
                    # Expiry alone cannot free an uncertain operation. Explicit
                    # fixture stop acknowledgement is delivered before replanning.
                else:
                    outcome = "succeeded"
                    positions[robot] = list(candidate["pose"])
                    coordinator.finish(task_id, robot, outcome, tick + 1)
                    excluded[robot].add(candidate["targetId"])
                journal.add("execution", round=round_index, nowMs=tick + 1, robotId=robot,
                            taskId=task_id, targetId=candidate["targetId"], path=candidate["path"],
                            outcome=outcome, truthSafetyAdmitted=safe, knownSnapshotAdmitted=fresh_known_safe,
                            admissionKnownMapHash=digest(volumes[robot]), admissionMs=admission_ms, stopReason=(
                                "independent_collision_refusal" if not safe else
                                "known_snapshot_refusal" if not fresh_known_safe else
                                "injected_driver_refusal" if outcome == "rejected" else
                                "injected_unknown_response" if outcome == "unknown" else "arrived"))
                if outcome == "unknown":
                    expired_at = task["deadlineMs"] + 1
                    coordinator.expire(expired_at)
                    retained = next(item for item in coordinator.tasks if item["taskId"] == task_id)
                    if retained["state"] != "uncertain":
                        raise AssertionError("lease expiry released an uncertain execution")
                    journal.add("uncertain_fence_retained", nowMs=expired_at,
                                robotId=robot, taskId=task_id, state=retained["state"])
                    coordinator.acknowledge_stop(task_id, robot, expired_at + 1)
                    journal.add("stop_acknowledged", round=round_index, nowMs=expired_at + 1,
                                robotId=robot, taskId=task_id, physicalStopMeasured=False)
                    observe(robot, expired_at + 2, round_index, task_id, outcome)
                else:
                    observe(robot, tick + 2, round_index, task_id, outcome)
            if not assignments and round_index >= 6:
                stop_reason = "no_executable_candidate"
                break
        finish_faults(last_round, stop_reason)
        journal.add("episode_end", stopReason=stop_reason, unsentObservationPackets=len(buffer))
    except Exception as exc:
        finish_faults(last_round, "software_error")
        journal.add("episode_end", stopReason="software_error", errorType=type(exc).__name__, error=str(exc))
        raise
    finally:
        journal.close()
        ledger.journal.close()
        coordinator.journal.close()
    events = read_events(output / "events.jsonl")
    metrics = independent_metrics(events, scene.target_ids, settings["coverageThreshold"], scene.truth)
    metrics.update(schemaVersion="spatial.episode-result.v1", sceneId=scene.scene_id, seed=scene.seed,
                   variant=variant, stopReason=stop_reason, physicalExecution=False,
                   truthHash=digest(scene.truth), eventChainHead=events[-1]["hash"])
    metrics["occupancyGeometryHash"] = occupancy_geometry_hash(scene.truth)
    write_json(output / "metrics.json", metrics)
    return metrics


def validate_settings(value):
    if value.get("schemaVersion") != SCHEMA:
        raise ValueError("unsupported experiment configuration schema")
    if value.get("backend") not in BACKENDS:
        raise ValueError("unknown policy backend plugin")
    if not value.get("scenes") or any(scene not in SCENE_BUILDERS for scene in value["scenes"]):
        raise ValueError("unknown or empty scene plugins")
    if not value.get("variants") or any(variant not in VARIANTS for variant in value["variants"]):
        raise ValueError("unknown or empty variants")
    if any(len(set(value[field])) != len(value[field]) for field in ("scenes", "variants", "seeds")):
        raise ValueError("paired experiment axes must have no duplicates")
    if not value["seeds"] or any(type(seed) is not int or not 0 <= seed < 2**31 for seed in value["seeds"]):
        raise ValueError("seeds must be bounded integer scene seeds")
    if type(value.get("maxRounds")) is not int or not 1 <= value["maxRounds"] <= 1000:
        raise ValueError("maxRounds must be 1..1000")
    if type(value.get("coverageThreshold")) not in (int, float) or not 0 < value["coverageThreshold"] <= 1:
        raise ValueError("coverageThreshold must be positive and at most one")
    return value


def campaign(config_path, output):
    settings = validate_settings(json.loads(config_path.read_text()))
    output.mkdir(parents=True, exist_ok=False)
    source_hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    for name in SOURCES:
        snapshot = output / "source" / name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes((ROOT / name).read_bytes())
    write_json(output / "config.json", settings)
    (output / "input-config.json").write_bytes(config_path.read_bytes())
    environment = {"python": sys.version, "platform": platform.platform(), "packages": {}}
    for package in ("numpy", "scipy", "grpcio", "protobuf", "pytest"):
        try:
            environment["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            environment["packages"][package] = None
    write_json(output / "environment.json", environment)
    manifest = {"schemaVersion": "spatial.campaign-manifest.v1", "state": "running",
                "configHash": digest(settings), "sourceHashes": source_hashes,
                "pairedAxes": ["sceneId", "seed"], "physicalExecution": False,
                "coverageDefinition": "Observed interior ROI voxels, free and occupied; not surface completeness",
                "sourceRestore": "Restore source/ files over gitHead checkout with recorded environment; replay only re-evaluates recorded events.",
                "communicationBytesDefinition": "Canonical observation payload upload plus relay and reconnect snapshot; excludes network framing, TLS and lease/control messages.",
                "ablationDefinitions": {"nearest_frontier": "ranking_strategy_only",
                    "no_global_reposition": "disable_nonlocal_known_space_repositioning",
                    "no_visibility_gain": "replace_occlusion_gain_ranking_only",
                    "no_coordination": "combined_assignment_overlap_and_shared_coverage_guidance_ablation; map exchange and safety fence remain enabled"},
                "unmeasured": ["energy", "localization error", "physical collisions", "flight/gait dynamics"],
                "assumptions": [
                    "Bounded static voxel fixtures, exact registered simulated pose and ideal finite ray sensor.",
                    "Flat load-bearing floor at z=1 is a fixture assumption; terrain support is revealed only for observed floor cells, not estimated geotechnical support.",
                    "UAV sensor orientation is an ideal observation pose; flight attitudes, camera actuation and dynamics are not validated.",
                    "Assignments are batched but execution is serial; other parked robot envelopes are checked, no concurrent 4D collision claim.",
                    "Disconnect faults partition observation exchange only; a local scheduler and stop acknowledgement port remain available.",
                    "Wall-clock planning timings depend on host load; scene seeds change geometry, not measurement noise.",
                ],
                "episodes": []}
    try:
        manifest["gitHead"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        manifest["gitDiffHash"] = hashlib.sha256(subprocess.check_output(["git", "diff", "HEAD", "--", *SOURCES], cwd=ROOT)).hexdigest()
    except (OSError, subprocess.CalledProcessError):
        manifest["gitHead"], manifest["gitDiffHash"] = None, None
    write_json(output / "manifest.json", manifest)
    results = []
    for scene_id, seed in itertools.product(settings["scenes"], settings["seeds"]):
        # One identical scene per paired block, rebuilt detached for each variant.
        for variant in settings["variants"]:
            scene = SCENE_BUILDERS[scene_id](scene_id, seed)
            episode_id = f"{scene_id}--seed-{seed}--{variant}"
            result = run_episode(scene, variant, settings, output / episode_id)
            results.append(result)
            manifest["episodes"].append(episode_id)
            write_json(output / "manifest.json", manifest)
    summary = {"schemaVersion": "spatial.campaign-summary.v1", "episodes": results,
               "pairedDeltas": paired_deltas(results), "paretoLabels": pareto_labels(results),
               "pairedBlockN": len({(item["sceneId"], item["seed"]) for item in results}),
               "seedsPerScene": {scene: len(settings["seeds"]) for scene in settings["scenes"]},
               "uniqueGeometryN": len({item["occupancyGeometryHash"] for item in results}),
               "uniqueGeometryNByScene": {scene: len({item["occupancyGeometryHash"] for item in results
                   if item["sceneId"] == scene}) for scene in settings["scenes"]},
               "uncertainty": {"confidenceInterval": None, "significanceTest": None,
                   "reason": "Small configured seed sample; descriptive paired comparisons only, no powered inference or optimality claim."},
               "claims": {
                   "optimalSystemIdentified": False, "physicalValidation": False,
                   "fullSceneCoverageGuaranteed": False,
                   "interpretation": "Paired offline geometric fixtures; report partial coverage and measure more scenes before drawing general conclusions."}}
    write_json(output / "summary.json", summary)
    scalar_fields = [key for key, value in results[0].items() if not isinstance(value, (dict, list))]
    with (output / "episodes.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(stream, scalar_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    manifest["state"] = "finalized"
    if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected
           for name, expected in source_hashes.items()):
        manifest["state"] = "invalid_source_mutation"
    manifest["files"] = {str(path.relative_to(output)): {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
        for path in sorted(output.rglob("*")) if path.is_file() and path.name != "manifest.json"}
    write_json(output / "manifest.json", manifest)
    if manifest["state"] != "finalized":
        raise RuntimeError("source changed during campaign; retain artifacts but exclude from comparisons")
    return summary


def paired_deltas(results):
    baseline = {(item["sceneId"], item["seed"]): item for item in results if item["variant"] == "baseline"}
    metrics = ("coverageFraction", "pathLengthM", "repeatedVoxelObservations", "planningMsTotal", "messageBytes")
    for item in results:
        reference = baseline.get((item["sceneId"], item["seed"]))
        if reference is not None and item["occupancyGeometryHash"] != reference["occupancyGeometryHash"]:
            raise ValueError("paired variants do not share the same occupancy geometry")
    return [{"sceneId": item["sceneId"], "seed": item["seed"], "variant": item["variant"],
             "relativeTo": "baseline", "pairedBlockN": 1,
             "delta": {metric: item[metric] - reference[metric] for metric in metrics}}
            for item in results if item["variant"] != "baseline"
            and (reference := baseline.get((item["sceneId"], item["seed"]))) is not None]


def pareto_labels(results):
    """Sample-only multi-objective labels, without a tunable winner score."""
    blocks = sorted({(item["sceneId"], item["seed"]) for item in results})
    costs = ("pathLengthM", "planningMsTotal", "messageBytes")
    labels = []
    for scene, seed in blocks:
        values = [item for item in results if (item["sceneId"], item["seed"]) == (scene, seed)]

        def dominates(other, item):
            no_worse = other["coverageFraction"] >= item["coverageFraction"] and all(other[key] <= item[key] for key in costs)
            strictly_better = other["coverageFraction"] > item["coverageFraction"] or any(other[key] < item[key] for key in costs)
            return no_worse and strictly_better

        labels.append({"sceneId": scene, "seed": seed, "sampleOnly": True,
            "maximize": ["coverageFraction"], "minimize": list(costs),
            "nondominatedVariants": sorted(item["variant"] for item in values
                if not any(dominates(other, item) for other in values)),
            "variantN": len(values)})
    return labels


def replay(output):
    manifest = json.loads((output / "manifest.json").read_text())
    if manifest.get("state") != "finalized":
        raise ValueError("campaign was not finalized")
    for name, expected in manifest["files"].items():
        path = output / name
        if path.resolve().parent != output.resolve() and output.resolve() not in path.resolve().parents:
            raise ValueError("manifest path escapes experiment directory")
        payload = path.read_bytes()
        if len(payload) != expected["bytes"] or hashlib.sha256(payload).hexdigest() != expected["sha256"]:
            raise ValueError(f"artifact integrity mismatch: {name}")
    for name, expected in manifest["sourceHashes"].items():
        if hashlib.sha256((output / "source" / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"source snapshot mismatch: {name}")
    settings = json.loads((output / "config.json").read_text())
    if digest(settings) != manifest["configHash"]:
        raise ValueError("configuration identity mismatch")
    verified, rebuilt_episodes = [], []
    for episode in manifest["episodes"]:
        directory = output / episode
        truth = json.loads((directory / "evaluator-truth.json").read_text())
        events = read_events(directory / "events.jsonl")
        metrics = json.loads((directory / "metrics.json").read_text())
        rebuilt = independent_metrics(events, set(truth["roiVoxelIds"]), settings["coverageThreshold"], truth["volume"])
        if any(metrics.get(key) != value for key, value in rebuilt.items()):
            raise ValueError(f"replayed metric mismatch: {episode}")
        if (digest(truth["volume"]) != metrics["truthHash"] or events[-1]["hash"] != metrics["eventChainHead"]
                or occupancy_geometry_hash(truth["volume"]) != metrics["occupancyGeometryHash"]):
            raise ValueError(f"episode identity mismatch: {episode}")
        verified.append(episode)
        rebuilt_episodes.append(metrics)
    summary = json.loads((output / "summary.json").read_text())
    if (summary["episodes"] != rebuilt_episodes or summary["pairedDeltas"] != paired_deltas(rebuilt_episodes)
            or summary["paretoLabels"] != pareto_labels(rebuilt_episodes)):
        raise ValueError("summary contradicts episode metrics")
    unique_by_scene = {scene: len({item["occupancyGeometryHash"] for item in rebuilt_episodes if item["sceneId"] == scene})
                       for scene in settings["scenes"]}
    if (summary["uniqueGeometryN"] != len({item["occupancyGeometryHash"] for item in rebuilt_episodes})
            or summary["uniqueGeometryNByScene"] != unique_by_scene):
        raise ValueError("summary geometry counts contradict episode layouts")
    return {"schemaVersion": "spatial.replay.v1", "verifiedEpisodes": len(verified), "passed": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=pathlib.Path,
                        default=ROOT / "examples/experiments/spatial_exploration_ablation.json")
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--replay", type=pathlib.Path)
    args = parser.parse_args(argv)
    if args.replay:
        print(json.dumps(replay(args.replay), ensure_ascii=False))
    else:
        if args.output is None:
            parser.error("--output must name a new experiment directory")
        summary = campaign(args.config, args.output)
        print(json.dumps({"episodes": len(summary["episodes"]),
                          "output": str(args.output), "physicalExecution": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
