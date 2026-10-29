"""Recompute task and measured-motion metrics without treating stop ACKs as proof."""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERABLE_FAILURE", "SAFETY_STOPPED"}


def finite_number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"invalid finite nonnegative {name}")
    return value


def validate_physics(physics, roster):
    """Validate the field transport identity; absent legacy metadata is unknown."""
    previous, state_times, missing = None, {}, set()
    gaps, wall_gaps = [], []
    maximum_measurement_lag = 0.
    for row in physics:
        sequence = row["sequence"]
        if type(sequence) is not int or sequence < 0:
            raise ValueError("invalid physics source sequence")
        at = finite_number(row["simulationTime"], "simulation clock")
        wall = finite_number(row["capturedAtUnixMs"], "capture clock")
        if previous is not None:
            if sequence <= previous[0] or at <= previous[1] or wall < previous[2]:
                raise ValueError("physics source sequence or clock regressed")
            gaps.append(at-previous[1])
            wall_gaps.append(wall-previous[2])
        previous = sequence, at, wall
        if not roster <= set(row["states"]):
            raise ValueError("physics frame omits a configured robot")
        for robot, state in row["states"].items():
            for field, expected in (("robotId", robot), ("clockDomain", "simulation"),
                                    ("frameId", "world"), ("worldId", "field-validation")):
                if field not in state:
                    missing.add(robot + ":" + field)
                elif state[field] != expected:
                    raise ValueError("physics robot, clock, frame or world identity mismatch")
            finite_vector(state["position"] + state["quaternionWXYZ"], 7)
            finite_vector(state["linearVelocity"], 3)
            if "angularVelocity" in state:
                finite_vector(state["angularVelocity"], 3)
            measured_at = finite_number(state["simulationTime"], "robot simulation clock")
            if measured_at > at + 1e-9 or measured_at < state_times.get(robot, measured_at):
                raise ValueError("robot measurement clock regressed or exceeds source frame")
            state_times[robot] = measured_at
            maximum_measurement_lag = max(maximum_measurement_lag, at-measured_at)
    return {"verified": bool(physics) and not missing, "missingFields": sorted(missing),
            "maximumSimulationSampleGapS": max(gaps, default=None),
            "maximumWallSampleGapMs": max(wall_gaps, default=None),
            "maximumRobotMeasurementLagS": maximum_measurement_lag}


def verify_task_journal(campaign, robot, result):
    """Offline, checkpointed SQLite can prove closure beyond an early API poll."""
    path = campaign / robot / "local-agent/agent.db"
    if not path.is_file():
        return {"verified": False, "status": "journal_not_retained"}
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("task journal must be checkpointed before offline analysis")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as db:
            row = db.execute("SELECT state FROM tasks WHERE id=?", (result["taskId"],)).fetchone()
            if row is None or row[0] != result["state"]:
                raise ValueError("task journal terminal state contradicts final task")
            events = db.execute("SELECT sequence,type,payload_json FROM task_events WHERE task_id=? ORDER BY sequence",
                                (result["taskId"],)).fetchall()
    except sqlite3.Error as exc:
        raise ValueError("task journal cannot be independently read") from exc
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("task journal changed during offline analysis")
    if any(sequence != index+1 for index, (sequence, _, _) in enumerate(events)):
        raise ValueError("task journal event sequence is incomplete")
    terminal = any(kind == "state.transition" and json.loads(payload).get("state") == result["state"]
                   for _, kind, payload in events)
    local_finished = result["state"] != "SUCCEEDED" or any(kind == "LOCAL_RUN_SUCCEEDED" for _, kind, _ in events)
    verified = terminal and local_finished
    return {"verified": verified, "status": "verified" if verified else "terminal_events_incomplete",
            "eventCount": len(events), "sha256": digest}


def read_lines(path):
    with path.open() as stream:
        for number, line in enumerate(stream, 1):
            try:
                yield json.loads(line)
            except ValueError as exc:
                raise ValueError(f"invalid evidence {path}:{number}") from exc


def finite_vector(value, size):
    if (not isinstance(value, list) or len(value) != size
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in value)):
        raise ValueError("evidence requires finite measured coordinates")
    if size == 7 and abs(sum(x*x for x in value[3:]) - 1) > 1e-5:
        raise ValueError("evidence quaternion must have unit norm")
    return value


def roster_and_goals(campaign):
    configured = {}
    for folder in sorted(p for p in campaign.iterdir() if p.is_dir()):
        profile_path, goals_path = folder / "profile.json", folder / "goals.json"
        if not profile_path.exists() and not goals_path.exists():
            continue
        if not profile_path.is_file() or not goals_path.is_file():
            raise ValueError("configured robot has incomplete profile/goals evidence")
        profile, goals = json.loads(profile_path.read_text()), json.loads(goals_path.read_text())
        if profile["robotId"] != folder.name or not goals.get("goals"):
            raise ValueError("configured robot identity or named goals mismatch")
        tolerance = goals.get("arrivalToleranceM", .12)
        if type(tolerance) not in (float, int) or not .01 <= tolerance <= .25:
            raise ValueError("invalid configured arrival tolerance")
        for pose in goals["goals"].values():
            finite_vector(pose, 7)
        configured[folder.name] = (profile, goals)
    if not configured:
        raise ValueError("campaign lacks configured robot roster")
    return configured


def route_for_task(campaign, robot, trial, result, configuration):
    profile, goals = configuration
    created = json.loads((campaign / robot / f"created-{trial['seed']}.json").read_text())
    final = json.loads((campaign / robot / f"final-{trial['seed']}.json").read_text())
    for task in (created, final):
        if (task.get("id") != result["taskId"] or task.get("request") != trial.get("request")
                or task.get("adapter") != profile["adapterId"]):
            raise ValueError("task identity/request differs from retained campaign envelope")
    if final.get("state") != result["state"]:
        raise ValueError("summary terminal state contradicts retained final task")
    if result["state"] not in TERMINAL:
        raise ValueError("retained final task is not terminal")
    calls = created["plan"]["capabilities"]["calls"]
    if (len(calls) != 1 or calls[0].get("tool") != "robot.task"
            or calls[0].get("arguments", {}).get("request") != trial["request"]
            or calls[0].get("legacyIntent", {}).get("robotId") != robot
            or final.get("plan") != created.get("plan")):
        raise ValueError("unsupported or changed retained route envelope")
    names = calls[0]["legacyIntent"].get("routeRooms")
    if not isinstance(names, list) or not names or any(n not in goals["goals"] for n in names):
        raise ValueError("task route lacks configured named goal bindings")
    if result["state"] == "SUCCEEDED" and (final.get("approved") is not True
            or not any(e.get("type") == "TASK_APPROVED" for e in final.get("events", []))):
        raise ValueError("successful task lacks retained approval")
    return names, [goals["goals"][n] for n in names]


def verify_arrival(arrival, goal, source_frames, robot, tolerance, window=None):
    sequence = arrival["sourceSequence"]
    if type(sequence) is not int or type(goal.get("admissionSequence")) is not int:
        raise ValueError("arrival/admission sequence must be an integer")
    admitted_at = finite_number(goal["admittedAtUnixMs"], "command admission clock")
    if sequence not in source_frames or goal.get("admissionSequence") not in source_frames:
        raise ValueError("arrival/admission receipt has no retained Gazebo measurement")
    frame, admitted = source_frames[sequence], source_frames[goal["admissionSequence"]]
    if window and (not window[0] <= admitted_at <= window[1]
                   or not window[0] <= frame["capturedAtUnixMs"] <= window[1]):
        raise ValueError("arrival or command admission lies outside campaign interval")
    measured = frame["states"][robot]
    pose = finite_vector(measured["position"] + measured["quaternionWXYZ"], 7)
    velocity = finite_vector(measured["linearVelocity"], 3)
    if (math.dist(pose, finite_vector(arrival["pose"], 7)) > 1e-7
            or math.dist(velocity, finite_vector(arrival["velocity"], 3)) > 1e-7
            or not math.isfinite(arrival["simulationTime"])
            or abs(measured["simulationTime"] - arrival["simulationTime"]) > 1e-9
            or arrival["observationId"] != f"gazebo-{robot}-{sequence}"):
        raise ValueError("arrival receipt does not match retained Gazebo measurement")
    if (sequence <= goal["admissionSequence"]
            or measured["simulationTime"] <= admitted["states"][robot]["simulationTime"]
            or admitted["capturedAtUnixMs"] > admitted_at
            or frame["capturedAtUnixMs"] < admitted_at):
        raise ValueError("arrival measurement is not after command admission")
    desired = finite_vector(goal["goalPose"], 7)
    distance = math.dist(pose[:3], desired[:3])
    speed = math.sqrt(sum(v*v for v in velocity))
    angle = 2 * math.acos(min(1., abs(sum(a*b for a, b in zip(pose[3:], desired[3:])))))
    for key, actual in (("distanceM", distance), ("orientationErrorRad", angle)):
        reported = arrival.get(key)
        if type(reported) not in (int, float) or not math.isfinite(reported) or abs(reported-actual) > 1e-7:
            raise ValueError("arrival residual does not match measured pose and issued goal")
    # The driver uses 0.08 m/s for its own capture. Hub status separately uses
    # 0.07 m/s, possibly on another source frame; this receipt proves only 0.08.
    if distance > tolerance or speed > .08 or angle > .15:
        raise ValueError("arrival violates the driver's measured completion thresholds")
    return distance, speed


def collision_radii(world, robots):
    """Offline SDF geometry audit around measured base_link origins, never pose truth."""
    if world is None:
        return {}
    models = {model.get("name"): model for model in ET.parse(world).getroot().findall("./world/model")}
    radii = {}
    for robot in robots:
        model = models.get(robot)
        if model is None:
            raise ValueError("world geometry omits a measured robot")
        bounds = []
        for link in model.findall("link"):
            collisions = link.findall("collision")
            if collisions and (link.get("name") != "base_link" or link.find("pose") is not None):
                raise ValueError("world audit only supports collisions expressed in base_link")
            for collision in collisions:
                pose = collision.find("pose")
                if pose is not None and (pose.attrib or len(pose.text.split()) != 6):
                    raise ValueError("world audit cannot resolve collision pose attributes")
                offset = [float(x) for x in pose.text.split()] if pose is not None else [0.] * 6
                if not all(math.isfinite(x) for x in offset):
                    raise ValueError("non-finite collision pose")
                geometry = collision.find("geometry")
                if geometry is None or len(geometry) != 1 or geometry[0].tag != "box":
                    raise ValueError("world audit requires explicit box collision geometry")
                size = [float(x) for x in geometry.findtext("box/size", "").split()]
                finite_vector(size, 3)
                if any(x <= 0 for x in size):
                    raise ValueError("collision dimensions must be positive")
                bounds.append(math.sqrt(sum(x*x for x in offset[:3])) + .5*math.sqrt(sum(x*x for x in size)))
        if not bounds:
            raise ValueError("world robot has no supported collision geometry")
        radii[robot] = max(bounds)
    return radii


def analyze(campaign, hub, output, world=None):
    summary = json.loads((campaign / "summary.json").read_text())
    if "endedAtUnixMs" not in summary:
        raise ValueError("campaign is not terminal")
    if finite_number(summary["endedAtUnixMs"], "campaign end") < finite_number(
            summary["startedAtUnixMs"], "campaign start"):
        raise ValueError("campaign time interval regressed")
    trial_seeds = [trial["seed"] for trial in summary["trials"]]
    seeds = summary.get("seeds", trial_seeds)
    if (any(type(s) is not int for s in seeds + trial_seeds) or len(set(seeds)) != len(seeds)
            or len(set(trial_seeds)) != len(trial_seeds) or not set(trial_seeds) <= set(seeds)):
        raise ValueError("declared or observed trial identities are invalid")
    if summary["success"] and (not summary["trials"] or any(
            not trial["tasks"] or any(task["state"] != "SUCCEEDED" for task in trial["tasks"].values())
            for trial in summary["trials"])):
        raise ValueError("campaign success contradicts actual terminal tasks")
    if summary["success"] and "seeds" in summary and [t["seed"] for t in summary["trials"]] != summary["seeds"]:
        raise ValueError("campaign success omits declared trials")
    configuration = roster_and_goals(campaign)
    roster = set(configuration)
    for trial in summary["trials"]:
        if not set(trial["tasks"]) <= roster or (summary["success"] and set(trial["tasks"]) != roster):
            raise ValueError("campaign task roster does not match configured robots")
    output.mkdir(parents=True, exist_ok=False)
    physics = list(read_lines(hub / "physics.jsonl"))
    source_identity = validate_physics(physics, roster)
    geometry_radii = collision_radii(world, {r for row in physics for r in row["states"]})
    source_frames = {row["sequence"]: row for row in physics}
    if len(source_frames) != len(physics):
        raise ValueError("duplicate physics source sequence")
    tasks = []
    for trial in summary["trials"]:
        for robot, result in trial["tasks"].items():
            route_names, route = route_for_task(campaign, robot, trial, result, configuration[robot])
            journal = verify_task_journal(campaign, robot, result)
            events = list(read_lines(campaign / robot / "controller.jsonl"))
            command_ids = {e["commandId"] for e in events if e["type"] == "GOAL"
                           and e["commandId"].startswith(result["taskId"] + "/")}
            goals = [e for e in events if e["type"] == "GOAL" and e["commandId"] in command_ids]
            arrivals = [e for e in events if e["type"] == "MEASURED_ARRIVAL" and e["commandId"] in command_ids]
            nav = [e for e in goals if e["capability"] == "navigation.navigate"]
            checks = [e for e in goals if e["capability"] == "verify_arrival"]
            if len(command_ids) != len(goals):
                raise ValueError("duplicate issued command identity")
            expected = [(kind, pose) for pose in route for kind in ("navigation.navigate", "verify_arrival")]
            observed = [(e["capability"], e["goalPose"]) for e in goals]
            route_matched = observed == expected
            if result["state"] == "SUCCEEDED" and not route_matched:
                raise ValueError("successful task does not execute its complete ordered approved route")
            arrived_ids = {e["commandId"] for e in arrivals}
            if len(arrived_ids) != len(arrivals):
                raise ValueError("duplicate command arrival receipt")
            by_command = {e["commandId"]: e for e in goals}
            measured_arrivals = []
            for arrival in arrivals:
                measured_arrivals.append(verify_arrival(arrival, by_command[arrival["commandId"]],
                    source_frames, robot, configuration[robot][1].get("arrivalToleranceM", .12),
                    (summary["startedAtUnixMs"], summary["endedAtUnixMs"])))
            if result["state"] == "SUCCEEDED" and (not nav or not checks or any(
                    e["commandId"] not in arrived_ids for e in nav + checks)):
                raise ValueError("task success lacks measured command receipts")
            tasks.append({"seed": trial["seed"], "robotId": robot, **result,
                "navigationCommands": len(nav), "independentArrivalChecks": len(checks),
                "expectedRouteRooms": route_names, "orderedApprovedRouteMatched": route_matched,
                "measuredReceipts": len(arrivals), "maxArrivalPositionErrorM": max(
                    (a[0] for a in measured_arrivals), default=None),
                "maxArrivalSpeedMps": max((a[1] for a in measured_arrivals), default=None),
                "distinctCommandIds": len(command_ids),
                "journalTerminalVerification": journal,
                "observationIds": [e["observationId"] for e in arrivals]})
    counts, contacts = Counter(), {}
    minimum, minimum_declared, minimum_geometry = math.inf, math.inf, math.inf
    advertised_radii = {}
    maximum_speed, distance, last = {}, {}, {}
    start_sim, end_sim, captures = None, None, 0
    contact_counts = {robot: [] for robot in roster}
    sensor_counts = {channel: {robot: [] for robot in roster} for channel in ("scan", "imu")}
    with (output / "trajectory.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["source_sequence", "captured_unix_ms", "simulation_s", "robot_id", "x_m", "y_m", "z_m",
                         "vx_mps", "vy_mps", "vz_mps", "command_id", "avoidance_reason", "scan_points"])
        for row in physics:
            if not summary["startedAtUnixMs"] <= row["capturedAtUnixMs"] <= summary["endedAtUnixMs"]:
                continue
            captures += 1
            for robot in roster:
                value = row.get("contactCounts", {}).get(robot)
                if value is not None:
                    if type(value) is not int or value < 0 or (contact_counts[robot] and value < contact_counts[robot][-1]):
                        raise ValueError("contact message counter invalid or regressed")
                    contact_counts[robot].append(value)
                for channel, counters in sensor_counts.items():
                    value = row.get(channel + "Counts", {}).get(robot)
                    if value is not None:
                        if type(value) is not int or value < 0 or (counters[robot] and value < counters[robot][-1]):
                            raise ValueError("sensor message counter invalid or regressed")
                        counters[robot].append(value)
            start_sim = row["simulationTime"] if start_sim is None else start_sim
            end_sim = row["simulationTime"]
            for robot, state in row["states"].items():
                command = row["commands"][robot]
                p, v = finite_vector(state["position"], 3), finite_vector(state["linearVelocity"], 3)
                radius = state["radius"]
                if type(radius) not in (float, int) or not math.isfinite(radius) or radius <= 0:
                    raise ValueError("measured state radius must be finite and positive")
                advertised_radii[robot] = max(advertised_radii.get(robot, 0), radius)
                maximum_speed[robot] = max(maximum_speed.get(robot, 0), math.sqrt(sum(x*x for x in v)))
                distance[robot] = distance.get(robot, 0) + (math.dist(last[robot], p) if robot in last else 0)
                last[robot] = p
                counts[robot + ":" + command["reason"]] += 1
                writer.writerow([row["sequence"], row["capturedAtUnixMs"], state["simulationTime"], robot,
                    *p, *v, command["commandId"], command["reason"],
                    command["diagnostics"].get("scan", {}).get("selectedCount", 0)])
            for (left_id, left), (right_id, right) in itertools.combinations(row["states"].items(), 2):
                separation = math.dist(left["position"], right["position"])
                minimum_declared = min(minimum_declared, separation-left["radius"]-right["radius"])
                minimum = min(minimum, separation-max(left["radius"], geometry_radii.get(left_id, 0))
                              - max(right["radius"], geometry_radii.get(right_id, 0)))
                if geometry_radii:
                    minimum_geometry = min(minimum_geometry, separation-geometry_radii[left_id]-geometry_radii[right_id])
            for record in row["contactHistory"]:
                # Raw contacts are retained including support-floor contacts.
                contacts[record["key"]] = record
    unexpected = [r for key, r in contacts.items() if "floor" not in key]
    coverage = {robot: {"firstMessageCount": values[0] if values else None,
        "lastMessageCount": values[-1] if values else None,
        "snapshotCountWithCounter": len(values),
        "messagesAdvancedDuringCampaign": bool(values and values[-1] > values[0]),
        "status": "observed" if len(values) == captures and values and values[-1] > values[0] else "unverified"}
        for robot, values in sorted(contact_counts.items())}
    sensor_coverage = {channel: {robot: {
        "firstMessageCount": values[0] if values else None,
        "lastMessageCount": values[-1] if values else None,
        "snapshotCountWithCounter": len(values),
        "messagesAdvancedDuringCampaign": bool(values and values[-1] > values[0]),
        "status": "observed" if len(values) == captures and values and values[-1] > values[0] else "unverified"}
        for robot, values in sorted(counters.items())} for channel, counters in sensor_counts.items()}
    missing_contact = [robot for robot, item in coverage.items() if item["status"] != "observed"]
    gap_passed = bool(captures and math.isfinite(minimum) and minimum >= 0)
    no_contact = not unexpected
    safety_passed = bool(gap_passed and no_contact and not missing_contact and geometry_radii
                         and source_identity["verified"])
    planned_count = len(seeds)*len(roster)
    success_count = sum(t["state"] == "SUCCEEDED" for t in tasks)
    journals_verified = bool(tasks) and all(t["journalTerminalVerification"]["verified"] for t in tasks)
    result = {"schemaVersion": "gazebo.field-metrics.v1", "taskCampaignSuccess": summary["success"],
        "tasks": tasks, "successCount": sum(t["state"] == "SUCCEEDED" for t in tasks), "taskCount": len(tasks),
        "plannedTaskCount": planned_count, "observedTerminalTaskCount": len(tasks),
        "unobservedTaskCount": planned_count-len(tasks),
        "successRateOverPlannedTasks": success_count/planned_count if planned_count else None,
        "missingDeclaredTrials": sorted(set(seeds)-set(trial_seeds)),
        "taskJournalsVerified": journals_verified,
        "endToEndClosureVerified": bool(summary["success"] and journals_verified
                                         and source_identity["verified"] and len(tasks) == planned_count),
        "closureVerificationScope": "retained robot.task route, measured arrivals and local task journal; not GOAL/Host or hardware",
        "navigationCommands": sum(t["navigationCommands"] for t in tasks),
        "independentArrivalChecks": sum(t["independentArrivalChecks"] for t in tasks),
        "arrivalReceiptsMatchedToPhysics": True,
        "arrivalThresholds": {"receiptSpeedMps": .08, "receiptOrientationRad": .15,
            "positionToleranceSource": "per-robot goals.json",
            "hubStatusSpeedMps": .07, "hubStatusFrameIndependentlyVerified": False},
        "configuredRobotRoster": sorted(roster),
        "trialRosterCoverage": [{"seed": trial["seed"], "missingRobots": sorted(roster-set(trial["tasks"]))}
                                for trial in summary["trials"]],
        "physicsCaptures": captures, "simulationDurationS": end_sim-start_sim if captures else None,
        "physicsSourceIdentity": source_identity,
        "minimumMeasuredBoundingSphereGapM": minimum if math.isfinite(minimum) else None,
        "minimumDeclaredRadiusGapM": minimum_declared if math.isfinite(minimum_declared) else None,
        "minimumWorldGeometryGapM": minimum_geometry if math.isfinite(minimum_geometry) else None,
        "boundingSphereGapBasis": "max_advertised_and_world_box_outer_sphere" if geometry_radii
                                 else "declared_radius_only_geometry_unverified",
        "worldGeometrySha256": hashlib.sha256(world.read_bytes()).hexdigest() if world else None,
        "worldConservativeRadiiM": geometry_radii, "maximumAdvertisedRadiiM": advertised_radii,
        "maximumMeasuredSpeedMps": maximum_speed, "measuredPathLengthM": distance,
        "avoidanceReasons": dict(counts), "contactPairs": contacts, "unexpectedContactPairs": unexpected,
        "contactChannelCoverage": coverage, "contactCoverageMissingRobots": missing_contact,
        "sensorChannelCoverage": sensor_coverage,
        "sensorCoverageScope": "counter presence and advancement only; not calibration or continuous freshness proof",
        "noObservedUnexpectedContacts": no_contact, "nonnegativeSampledGap": gap_passed,
        "safetyMetricsPassed": safety_passed,
        "safetyVerificationStatus": "passed" if safety_passed else (
            "failed" if unexpected or (captures and minimum < 0) else "not_fully_verified"),
        "safetyVerificationLimitations": ["No contact messages or advancing message counter observed for " + r
                                         for r in missing_contact] + ([] if geometry_radii else [
                                             "World collision geometry not provided; declared radii are unverified"])
                                         + ([] if source_identity["verified"] else [
                                             "Physics source identity metadata is incomplete"]),
        "samplingLimit": "state snapshots ~0.1 sim s; full contact topic history retained; no continuous distance certificate",
        "localization": summary["localization"], "independentMapsOrHardwareTrials": False}
    (output / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    (output / "input-manifest.json").write_text(json.dumps({str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [campaign / "summary.json", hub / "physics.jsonl", *campaign.glob("*/controller.jsonl"),
                  *campaign.glob("*/profile.json"), *campaign.glob("*/goals.json"),
                  *campaign.glob("*/created-*.json"), *campaign.glob("*/final-*.json"),
                  *campaign.glob("*/local-agent/agent.db"),
                  *([world] if world else [])]}, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--hub", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--world", type=Path, help="exact frozen SDF for offline robot collision-radius audit")
    args = parser.parse_args()
    result = analyze(args.campaign, args.hub, args.output, args.world)
    print(json.dumps({k: result[k] for k in ("taskCount", "successCount", "navigationCommands",
        "independentArrivalChecks", "minimumMeasuredBoundingSphereGapM", "safetyMetricsPassed")}, indent=2))


if __name__ == "__main__":
    main()
