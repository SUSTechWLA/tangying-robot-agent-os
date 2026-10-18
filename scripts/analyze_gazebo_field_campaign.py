"""Recompute task and measured-motion metrics without treating stop ACKs as proof."""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


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


def verify_arrival(arrival, goal, source_frames, robot, tolerance):
    sequence = arrival["sourceSequence"]
    if sequence not in source_frames or goal.get("admissionSequence") not in source_frames:
        raise ValueError("arrival/admission receipt has no retained Gazebo measurement")
    frame, admitted = source_frames[sequence], source_frames[goal["admissionSequence"]]
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
            or frame["capturedAtUnixMs"] < goal["admittedAtUnixMs"]):
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
    geometry_radii = collision_radii(world, {r for row in physics for r in row["states"]})
    source_frames = {row["sequence"]: row for row in physics}
    if len(source_frames) != len(physics):
        raise ValueError("duplicate physics source sequence")
    tasks = []
    for trial in summary["trials"]:
        for robot, result in trial["tasks"].items():
            route_names, route = route_for_task(campaign, robot, trial, result, configuration[robot])
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
                    source_frames, robot, configuration[robot][1].get("arrivalToleranceM", .12)))
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
                "observationIds": [e["observationId"] for e in arrivals]})
    counts, contacts = Counter(), {}
    minimum, minimum_declared, minimum_geometry = math.inf, math.inf, math.inf
    advertised_radii = {}
    maximum_speed, distance, last = {}, {}, {}
    start_sim, end_sim, captures = None, None, 0
    contact_counts = {robot: [] for robot in roster}
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
        "messagesAdvancedDuringCampaign": bool(values and values[-1] > values[0]),
        "status": "observed" if values and values[-1] > values[0] else "unverified"}
        for robot, values in sorted(contact_counts.items())}
    missing_contact = [robot for robot, item in coverage.items() if item["status"] != "observed"]
    gap_passed = bool(captures and math.isfinite(minimum) and minimum >= 0)
    no_contact = not unexpected
    safety_passed = bool(gap_passed and no_contact and not missing_contact and geometry_radii)
    result = {"schemaVersion": "gazebo.field-metrics.v1", "taskCampaignSuccess": summary["success"],
        "tasks": tasks, "successCount": sum(t["state"] == "SUCCEEDED" for t in tasks), "taskCount": len(tasks),
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
        "noObservedUnexpectedContacts": no_contact, "nonnegativeSampledGap": gap_passed,
        "safetyMetricsPassed": safety_passed,
        "safetyVerificationStatus": "passed" if safety_passed else (
            "failed" if unexpected or (captures and minimum < 0) else "not_fully_verified"),
        "safetyVerificationLimitations": ["No contact messages or advancing message counter observed for " + r
                                         for r in missing_contact] + ([] if geometry_radii else [
                                             "World collision geometry not provided; declared radii are unverified"]),
        "samplingLimit": "state snapshots ~0.1 sim s; full contact topic history retained; no continuous distance certificate",
        "localization": summary["localization"], "independentMapsOrHardwareTrials": False}
    (output / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (output / "input-manifest.json").write_text(json.dumps({str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [campaign / "summary.json", hub / "physics.jsonl", *campaign.glob("*/controller.jsonl"),
                  *campaign.glob("*/profile.json"), *campaign.glob("*/goals.json"),
                  *campaign.glob("*/created-*.json"), *campaign.glob("*/final-*.json"),
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
