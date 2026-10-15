"""Offline checks for a frozen point-cloud route and its Runtime receipts.

These checks establish consistency of retained provider evidence. They do not
possess the Runtime's signing key or turn a receipt into an independent physical
sensor. Position, command freshness and RGB-D checks remain in the parent suite.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _stamp(value):
    return (type(value) in (int, float) and 0 < value <= 2**53-1
            and math.isfinite(value) and int(value) == value)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _scope(cap_step, revision, index, count):
    require(type(revision) is int and revision > 0, "semantic evidence lacks a task revision")
    prefix = f"revision-{revision}/" if revision > 1 else ""
    if cap_step.startswith("revision-"):
        require(prefix and cap_step.startswith(prefix), "capability revision prefix differs")
        cap_step = cap_step[len(prefix):]
    stage = f"task{index+1:02d}-" if count > 1 else ""
    return prefix + cap_step + "-" + stage, stage


def _occurred(event):
    value = event.get("occurredAt")
    require(isinstance(value, str) and value, "semantic command start has no timestamp")
    try:
        date = datetime.fromisoformat(value)
    except ValueError as error:
        raise AssertionError("semantic command start timestamp is invalid") from error
    require(date.tzinfo is not None, "semantic command start timestamp has no timezone")
    return int(date.timestamp()*1000)


def canonical_observation(value):
    """Mirror the documented sensor summary format across Struct/JSON numbers."""
    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, float) and math.isfinite(item) and item.is_integer():
            return int(item)
        return item
    return json.dumps(normalize(value), ensure_ascii=True, sort_keys=True,
                      allow_nan=False, separators=(",", ":")).encode()


def validate_grounding_checkpoints(task, call, cap_step, rows):
    targets = call["arguments"]["semanticRoute"]["targets"]
    frozen = call["legacyIntent"].get("sequence") or [call["legacyIntent"]]
    events = task.get("events", [])
    require(targets and len(targets) == len(frozen), "semantic checkpoint target count differs")
    revision = task.get("currentRevision", 1)
    previous_navigation = -1
    for index, (target, intent) in enumerate(zip(targets, frozen, strict=True)):
        scope, prefix = _scope(cap_step, revision, index, len(targets))
        checkpoint = scope + "semantic-grounding"
        saved = [(i, e) for i, e in enumerate(events)
                 if e.get("type") == "SEMANTIC_GROUNDING_FROZEN" and e.get("stepId") == checkpoint]
        require(len(saved) == 1, "semantic visit lacks one durable grounding checkpoint")
        position, event = saved[0]
        grounded = event.get("payload", {}).get("grounded") or {}
        evidence = grounded.get("goalEvidence") or {}
        require(event["payload"].get("taskRevision") == revision
                and _sha(event["payload"].get("intentBinding"))
                and grounded.get("taskId") == task["id"] and grounded.get("robotId") == intent["robotId"]
                and grounded.get("action") == "home_route"
                and grounded.get("stepIdPrefix", "") == prefix
                and grounded.get("routeRooms") == [target["name"]]
                and evidence.get("semanticTarget") == intent["semanticTarget"],
                "semantic checkpoint has another task, revision or frozen target")
        nav = [row for row in rows if row["event"]["payload"].get("toolName") == "navigation.navigate"
               and row["event"]["stepId"] == scope + "navigate_00"]
        require(len(nav) == 1, "semantic checkpoint has missing or repeated navigation")
        args = nav[0]["event"]["payload"].get("arguments", {})
        require(grounded.get("routeGoals") == [args.get("goalPose")]
                and grounded.get("semanticBindings") == [args.get("semanticBinding")]
                and evidence.get("candidate", {}).get("goalPose") == args.get("goalPose")
                and evidence.get("candidate", {}).get("semanticBinding") == args.get("semanticBinding"),
                "execution changed its durable semantic candidate")
        starts = [i for i, e in enumerate(events) if e.get("stepId", "").startswith(scope)
                  and e.get("type") == "TOOL_ACTIVITY" and e.get("payload", {}).get("activityStatus") == "SENDING"
                  and e["payload"].get("toolName") in {"navigation.navigate", "navigation.pre_position",
                      "arm.move", "manipulation.pick", "manipulation.place", "recover_to_safe_pose"}]
        require(starts and position < min(starts), "semantic candidate was not frozen before physical execution")
        nav_position = next((i for i, e in enumerate(events) if e == nav[0]["event"]), -1)
        require(nav_position > previous_navigation, "semantic checkpoint visits are out of approved order")
        previous_navigation = nav_position
        for row in rows:
            if row["event"]["stepId"] != scope + "verify_arrival_00" or target["verificationMode"] != "object":
                continue
            payload = row["event"]["payload"]
            dispatches = [e for e in events if e.get("stepId") == row["event"]["stepId"]
                and e.get("type") == "TOOL_ACTIVITY" and e.get("payload", {}).get("activityStatus") == "SENDING"
                and e["payload"].get("commandId") == payload.get("commandId")]
            nested = (row.get("state", {}).get("semantic_verification") or {}).get("observationEvidence") or {}
            require(dispatches and _stamp(nested.get("observedAtUnixMs"))
                    and nested["observedAtUnixMs"] >= _occurred(dispatches[-1]),
                    "object observation predates its own verification command")


def _object_evidence(verification, row, identity, route, target, execution):
    require(verification.get("semanticConfirmed") is True,
            "object verification mode completed without semantic confirmation")
    nested = verification.get("observationEvidence") or {}
    require(isinstance(nested, dict) and verification.get("observationId") and _sha(nested.get("evidenceHash")),
            "object confirmation lacks an observation reference and digest")
    active = {key: identity[key] for key in ("mapId", "mapRevision", "calibrationRevision")}
    require(nested.get("schemaVersion") == "semantic.cloud.observation.v1"
            and nested.get("semanticConfirmed") is True and nested.get("identity") == identity
            and nested.get("activeMap") == active and nested.get("mapRevision") == identity["mapRevision"]
            and nested.get("frameId") == "map" and nested.get("captureBaseFrameId") == "world"
            and nested.get("annotationId") == target["annotationId"]
            and nested.get("semanticRevision") == route["semanticRevision"]
            and nested.get("worldFrameRevision") == execution["worldFrameRevision"]
            and nested.get("observationId") == verification["observationId"]
            and nested.get("sourceId") == verification.get("sourceId")
            and nested.get("observedAtUnixMs") == verification.get("observedAtUnixMs"),
            "object observation differs from executed target, frame, source or capture")
    stamp, observed = nested.get("observedAtUnixMs"), row["detail"].get("observedAtUnixMs")
    nav_observed = row["navigation"].get("detail", {}).get("observedAtUnixMs")
    require(_stamp(stamp) and _stamp(observed) and _stamp(nav_observed)
            and nav_observed <= stamp <= observed and observed-stamp <= 2000,
            "object observation is stale, future or predates navigation completion")
    snapshot = row["detail"].get("snapshot", {})
    sensors = snapshot.get("robotProfile", {}).get("sensors", [])
    sensor = [sensor for sensor in sensors if sensor.get("sourceId") == nested.get("sourceId")]
    require(len(sensor) == 1 and nested.get("sourceType") in
            {"rgbd_camera", "stereo_camera", "lidar", "sensor_fusion"}
            and sensor[0].get("sourceType") == nested.get("sourceType")
            and sensor[0].get("frameId") == nested.get("sourceFrameId")
            and sensor[0].get("transformRevision") == identity["calibrationRevision"],
            "object evidence source/frame/calibration is not independently declared")
    require(_stamp(sensor[0].get("maxAgeMs")) and observed-stamp <= min(2000, sensor[0]["maxAgeMs"]),
            "object evidence exceeds its declared sensor age")
    pose = nested.get("captureBasePose")
    require(isinstance(pose, list) and len(pose) == 7
            and all(type(value) in (int, float) and math.isfinite(value) for value in pose)
            and abs(sum(value*value for value in pose[3:])-1.) <= 1e-3,
            "object evidence has no finite normalized same-frame base pose")
    matches = nested.get("matches")
    require(nested.get("reason") == "matched" and nested.get("matchCount") == 1
            and type(nested.get("matchCount")) in (int, float)
            and nested.get("matchesTruncated") is False and isinstance(matches, list) and len(matches) == 1
            and nested.get("executionAuthorized") is False and nested.get("manipulationReachVerified") is False
            and type(nested.get("targetFresh")) is bool
            and (nested["targetFresh"] or target["allowStaleSearch"] is True)
            and _sha(nested.get("perceptionDigest")) and _stamp(nested.get("sequence")),
            "object evidence does not contain exactly one bounded perception match")
    match = matches[0]
    position = match.get("mapPosition") if isinstance(match, dict) else None
    confidence = match.get("confidence") if isinstance(match, dict) else None
    distance = match.get("distanceToBoundsM") if isinstance(match, dict) else None
    require(isinstance(position, list) and len(position) == 3
            and all(type(value) in (int, float) and math.isfinite(value) for value in position)
            and type(confidence) in (int, float) and .5 <= confidence <= 1
            and type(distance) in (int, float) and 0 <= distance <= .15
            and isinstance(match.get("entityId"), str) and match["entityId"]
            and isinstance(match.get("category"), str) and match["category"],
            "object match lacks bounded measured position, confidence or identity")
    observation = {key: value for key, value in nested.items() if key != "evidenceHash"}
    digest = hashlib.sha256(canonical_observation(observation)).hexdigest()
    require(nested["evidenceHash"] == digest, "semantic observation digest differs")


def validate_semantic_route(call, cap_step, arrivals):
    route = call["arguments"]["semanticRoute"]
    targets = route.get("targets", [])
    intent = call.get("legacyIntent") or {}
    sequence = intent.get("sequence") or [intent]
    robot_id = intent.get("robotId")
    require(robot_id and targets and len(arrivals) == len(targets) == len(sequence),
            "semantic route visits differ from the frozen target count")
    identity = {"robotId": robot_id, "frameId": "map",
                **{key: route.get(key) for key in
                   ("mapId", "mapRevision", "calibrationRevision", "cloudSha256")}}
    require(all(identity.values()) and all(_sha(identity[key]) for key in
            ("mapRevision", "calibrationRevision", "cloudSha256")) and _sha(route.get("semanticRevision")),
            "semantic route lacks full identity")
    checked = []
    previous = -1
    for index, (target, frozen, row) in enumerate(zip(targets, sequence, arrivals, strict=True)):
        distance = target.get("maxDistanceM")
        require(target.get("verificationMode") in {"arrival", "object"}
                and type(target.get("allowStaleSearch")) is bool
                and type(distance) in (int, float) and .5 <= distance <= 5,
                "semantic route contains an invalid execution policy")
        expected = {"identity": identity, "semanticRevision": route["semanticRevision"], **target}
        require(frozen.get("action") == "home_route" and frozen.get("robotId") == robot_id
                and frozen.get("routeRooms") == [target["name"]]
                and frozen.get("semanticTarget") == expected,
                "frozen semantic intent differs from approved target identity or policy")
        navigation = row["navigation"]
        nav_event, verify_event = navigation["event"], row["event"]
        nav_payload, verify_payload = nav_event["payload"], verify_event["payload"]
        revision = nav_payload.get("taskRevision")
        scope, _ = _scope(cap_step, revision, index, len(targets))
        require(verify_payload.get("taskRevision") == revision
                and nav_event.get("stepId") == scope + "navigate_00"
                and verify_event.get("stepId") == scope + "verify_arrival_00"
                and nav_payload.get("toolName") == "navigation.navigate"
                and verify_payload.get("toolName") == "verify_arrival"
                and type(navigation.get("index")) is int and type(row.get("index")) is int
                and previous < navigation["index"] < row["index"],
                "semantic route visit order, revision or capability scope differs")
        previous = row["index"]
        binding = nav_payload.get("arguments", {}).get("semanticBinding")
        require(isinstance(binding, str) and 0 < len(binding) <= 2048
                and verify_payload.get("arguments", {}).get("semanticBinding") == binding
                and verify_payload.get("arguments", {}).get("goalPose") == nav_payload.get("arguments", {}).get("goalPose"),
                "arrival does not carry the exact navigation semantic binding")
        sha = hashlib.sha256(binding.encode()).hexdigest()
        receipt = navigation["state"].get("map_route") or {}
        execution = receipt.get("semanticExecution") or {}
        require(receipt.get("semanticBindingSha256") == sha, "navigation receipt has another semantic binding")
        require(execution.get("identity") == identity
                and execution.get("semanticRevision") == route["semanticRevision"]
                and execution.get("annotationId") == target["annotationId"]
                and execution.get("verificationMode") == target["verificationMode"]
                and execution.get("allowStaleSearch") == target["allowStaleSearch"]
                and execution.get("worldFrameRevision"), "navigation semantic identity or search policy differs")
        require(type(execution.get("targetFresh")) is bool
                and (execution["targetFresh"] is True or target["allowStaleSearch"] is True),
                "stale target executed without explicit search permission")
        verification = row["state"].get("semantic_verification") or {}
        verified_execution = verification.get("semanticExecution") or {}
        require(isinstance(nav_payload.get("commandId"), str) and nav_payload["commandId"]
                and isinstance(verify_payload.get("commandId"), str) and verify_payload["commandId"]
                and verification.get("commandId") == verify_payload.get("commandId")
                and verification.get("navigationCommandId") == nav_payload.get("commandId")
                and verification.get("semanticBindingSha256") == sha
                and {k: v for k, v in verified_execution.items() if k != "targetFresh"}
                    == {k: v for k, v in execution.items() if k != "targetFresh"}
                and verification.get("arrivalVerified") is True,
                "semantic arrival evidence belongs to another command, target or revision")
        require(type(verified_execution.get("targetFresh")) is bool
                and (verified_execution["targetFresh"] is True or target["allowStaleSearch"] is True),
                "verification accepted stale target without search permission")
        # The parent validator independently checks post-dispatch capture and
        # pose errors; this additional stamp cannot be a future synthetic value.
        stamp = verification.get("freshCaptureAtUnixMs")
        observed = row["detail"].get("observedAtUnixMs")
        require(_stamp(stamp) and _stamp(observed) and stamp <= observed and observed - stamp <= 2000,
                "semantic arrival evidence lacks a fresh measured capture")
        if target["verificationMode"] == "object":
            _object_evidence(verification, row, identity, route, target, execution)
        else:
            require(verification.get("semanticConfirmed") is False,
                    "arrival-only mode must not claim object confirmation")
        checked.append({"annotationId": target["annotationId"], "name": target["name"],
                        "stepId": verify_event["stepId"], "semanticBindingSha256": sha,
                        "verificationMode": target["verificationMode"],
                        "semanticConfirmed": verification.get("semanticConfirmed") is True,
                        "targetFresh": execution["targetFresh"],
                        "navigationCommandId": nav_payload["commandId"],
                        "verificationCommandId": verify_payload["commandId"]})
    return {"capabilityStepId": cap_step, "rooms": [], "transfers": [],
            "semanticRoute": checked, "identity": identity,
            "semanticRevision": route["semanticRevision"],
            "basis": "runtime_bound_receipts_and_parent_post_command_rgbd_pose_checks",
            "signatureIndependentlyVerified": False}
