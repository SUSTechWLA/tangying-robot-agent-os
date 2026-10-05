"""Accept one long-horizon Gazebo goal through the production console API.

This creates and approves a simulation task. It never resets the world, calls a
robot service directly, or retries a failed mutation. Each HTTP response and
the draft, approved and final task are retained once, with SHA-256 hashes.
An automatic investigation is reported separately from a recovered task.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

try:
    from .console_session import headers as session_headers
    from .console_session import install_loopback_opener, resolve_token
except ImportError:  # Direct script invocation.
    from console_session import headers as session_headers
    from console_session import install_loopback_opener, resolve_token


TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERABLE_FAILURE", "SAFETY_STOPPED", "WAITING_USER"}
PHYSICAL = {"navigation.navigate", "manipulation.pick", "manipulation.place"}


def require(condition, message):
    # Acceptance checks must also run under python -O.
    if not condition:
        raise AssertionError(message)


class EvidenceAPI:
    def __init__(self, base, output):
        self.base, self.output = base, output
        self.token = resolve_token(base_url=base)
        self.files, self.requests = {}, []
        self.last_raw = b""

    def save(self, name, value, *, raw=False):
        wire = value if raw else (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        with (self.output / name).open("xb") as stream:
            stream.write(wire)
        self.files[name] = hashlib.sha256(wire).hexdigest()

    def __call__(self, path, body=None, *, binary=False):
        method = "GET" if body is None else "POST"
        req = Request(self.base + path, method=method,
                      data=None if body is None else json.dumps(body).encode(),
                      headers=session_headers(self.token, {"Content-Type": "application/json", "Origin": self.base}))
        entry = {"method": method, "path": path, "file": f"response-{len(self.requests)+1:05d}.{'bin' if binary else 'json'}"}
        self.requests.append(entry)
        try:
            with urlopen(req, timeout=120 if path == "/v1/tasks" or path.endswith("/approve") else 45) as response:
                raw = response.read()
        except HTTPError as error:
            raw = error.read()
            self.save(entry["file"], raw, raw=True)
            entry["httpStatus"] = error.code
            raise
        self.save(entry["file"], raw, raw=True)
        self.last_raw = raw
        return raw if binary else json.loads(raw)


def at(value, path):
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def pose_error(actual, expected):
    for pose in (actual, expected):
        require(isinstance(pose, list) and len(pose) == 7 and all(
            type(v) in (float, int) and math.isfinite(v) for v in pose), "arrival requires finite XYZ+wxyz poses")
        require(abs(sum(v*v for v in pose[3:])-1) < .001, "arrival quaternion is not normalized")
    def yaw(pose):
        w, x, y, z = pose[3:]
        return math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))
    angle = yaw(actual)-yaw(expected)
    return math.dist(actual[:2], expected[:2]), abs(math.atan2(math.sin(angle), math.cos(angle)))


def read_recovery_chains(task, catalog):
    """Prove the bounded failed-read -> agent diagnosis -> retry -> verify chain."""
    events = task.get("events", [])
    manifests = {item["name"]: item for item in (catalog or {}).get("services", [])}
    chains = []
    for start, request in enumerate(events):
        if request.get("type") != "CAPABILITY_READ_RECOVERY_REQUESTED":
            continue
        step, payload = request.get("stepId"), request.get("payload", {})
        tool, command = payload.get("tool"), payload.get("commandId")
        manifest = manifests.get(tool, {})
        if (not step or not event_belongs_to_task(request, task["id"])
                or command != f"{task['id']}/{step}" or payload.get("maxRetries") != 1
                or manifest.get("mutatesWorld") is not False
                or manifest.get("contract", {}).get("effects") != ["READ"]):
            continue
        if sum(e.get("type") == request["type"] and e.get("stepId") == step for e in events) != 1:
            continue
        if sum(e.get("type") == "CAPABILITY_READ_RETRY" and e.get("stepId") == step for e in events) != 1:
            continue
        original = [i for i, e in enumerate(events[:start]) if e.get("type") == "CAPABILITY_CALL"
                    and e.get("stepId") == step and e.get("payload", {}).get("tool") == tool
                    and event_belongs_to_task(e, task["id"])
                    and e["payload"].get("commandId") == command]
        if len(original) != 1:
            continue
        cursor, indices, plan_id = start, [original[0], start], None
        def find(kind, predicate, after, current_plan):
            return next(((i, e) for i, e in enumerate(events) if i > after and e.get("type") == kind
                         and event_belongs_to_task(e, task["id"])
                         and predicate(e, e.get("payload", {}), current_plan)), None)
        checks = [
            ("CAPABILITY_FAILED", lambda e, p, current_plan, step=step, tool=tool, command=command: e.get("stepId") == step and p.get("tool") == tool
             and p.get("commandId") == command and p.get("phase") == "dispatch"
             and p.get("mutatesWorld") is False and p.get("outcomeUnknown") is False
             and p.get("code") in {"PROVIDER_UNAVAILABLE", "PROVIDER_DEADLINE_EXCEEDED"}),
            ("ops.anomaly_detected", lambda e, p, current_plan, step=step, tool=tool: p.get("agent") == "ops" and p.get("code") == "ANOMALY_ACTION_FAILED"
             and p.get("facts", {}).get("stepId") == step and p.get("component") == tool
             and p.get("correlationId") == task["id"]),
            ("ops.recovery_plan", lambda e, p, current_plan, step=step, tool=tool: p.get("agent") == "recovery" and p.get("verdict") == "PLAN"
             and p.get("trigger") == "ANOMALY_ACTION_FAILED@"+tool and bool(p.get("planId"))
             and p.get("taskId") == task["id"]
             and any(s.get("name") == "recovery.start" and s.get("findings", {}).get("stepId") == step
                     for s in p.get("trail", {}).get("steps", []))),
            ("ops.recovery_executed", lambda e, p, current_plan: p.get("agent") == "recovery" and p.get("planId") == current_plan
             and p.get("actionId") == "execution.read-history" and p.get("executed") is True
             and p.get("verified") is True and not p.get("operatorApproved") and not p.get("approvalEvidence") and not p.get("failed")),
            ("CAPABILITY_READ_RETRY", lambda e, p, current_plan, step=step, tool=tool, command=command: e.get("stepId") == step and p.get("tool") == tool
             and p.get("commandId") == command and p.get("attempt") == 2
             and p.get("basis") == "OPS_DIAGNOSIS_AND_VERIFIED_READ_ONLY_RECOVERY"),
            ("CAPABILITY_VERIFIED", lambda e, p, current_plan, step=step, tool=tool: e.get("stepId") == step and p.get("tool") == tool and bool(p.get("evidence"))),
        ]
        for kind, predicate in checks:
            found = find(kind, predicate, cursor, plan_id)
            if found is None:
                break
            cursor, matched = found
            indices.append(cursor)
            if kind == "ops.recovery_plan":
                plan_id = matched["payload"]["planId"]
        else:
            if any(e.get("stepId") == step and e.get("type") in {"CAPABILITY_FAILED", "CAPABILITY_CALL", "CAPABILITY_READ_RETRY"}
                   for e in events[cursor+1:]):
                continue
            chains.append({"taskId": task["id"], "stepId": step, "tool": tool, "commandId": command,
                           "planId": plan_id, "eventIndices": indices, "retryCount": 1})
    return chains


def event_belongs_to_task(event, task_id):
    payload = event.get("payload", {})
    return all(value in (None, "", task_id) for value in
               (event.get("taskId"), payload.get("taskId"), payload.get("correlationId")))


def collaboration_evidence(task, catalog=None):
    events = task.get("events", [])
    ops = [e for e in events if e.get("type", "").startswith("ops.") and event_belongs_to_task(e, task.get("id"))]
    agents = sorted({e.get("payload", {}).get("agent") for e in ops if e.get("payload", {}).get("agent")})
    automatic = [e for e in ops if e["type"] == "ops.recovery_executed"
                 and e.get("payload", {}).get("executed") is True
                 and e["payload"].get("verified") is True
                 and not e["payload"].get("operatorApproved")
                 and not e["payload"].get("approvalEvidence")
                 and not e["payload"].get("failed")]
    plans = {e["payload"].get("planId"): e for e in ops if e["type"] == "ops.recovery_plan"
             and e["payload"].get("planId") and e["payload"].get("taskId") == task.get("id")}
    linked = [e for e in automatic if e["payload"].get("planId") in plans
              and events.index(plans[e["payload"]["planId"]]) < events.index(e)]
    # A successful read is useful investigation, not evidence of task repair.
    # Existing execution policy records automatic attempts as RECOVERY_ACTIVITY;
    # credit task recovery only if the same step subsequently has confirmation.
    recovered = []
    for index, event in enumerate(events):
        if event.get("type") != "RECOVERY_ACTIVITY" or not event.get("payload", {}).get("automaticAction"):
            continue
        step = event.get("stepId")
        if step and any(e.get("stepId") == step and e.get("type") == "TOOL_ACTIVITY"
                        and e.get("payload", {}).get("activityStatus") == "CONFIRMED" for e in events[index+1:]):
            recovered.append(step)
    chains = read_recovery_chains(task, catalog)
    return {"eventAgents": agents, "opsEventCount": len(ops),
            "collaborationObserved": "ops" in agents and "recovery" in agents and bool(plans),
            "automaticInvestigationVerified": bool(linked),
            "automaticVerifiedActionIds": [e["payload"].get("actionId") for e in linked],
            "automaticTaskRecoveryObserved": task.get("state") == "SUCCEEDED" and bool(chains),
            "automaticallyRecoveredStepIds": [chain["stepId"] for chain in chains],
            "verifiedReadRecoveryChains": chains,
            "policyRecoveryConfirmedStepIds": sorted(set(recovered)),
            "interpretation": "Configuration is not participation; verified diagnostic reads do not prove task repair.",
            "events": [e for e in events if e.get("type", "").startswith("ops.")
                       or e.get("type") in {"RECOVERY_ACTIVITY", "CAPABILITY_READ_RECOVERY_REQUESTED",
                                            "CAPABILITY_FAILED", "CAPABILITY_READ_RETRY"}]}


def collect_observations(api, path):
    details, before, cursors = {}, None, set()
    while True:
        index = api(path + "/observations?limit=200" + (f"&before={before}" if before else ""))
        for record in index.get("records", []):
            detail = api(path + "/observations/" + quote(record["id"], safe=""))
            require(detail.get("id") == record["id"], "observation index/detail identity mismatch")
            key = (detail.get("stepId"), detail.get("captureId"))
            require(key not in details, "duplicate observation identity")
            details[key] = detail
            for kind in ("rgb", "depth"):
                expected = record.get(kind+"Sha256")
                # Provider/context snapshots can legitimately have no camera
                # frames. Child command observations are checked below.
                if not expected:
                    continue
                require(detail.get(kind+"Sha256") == expected, "observation index/detail image hash mismatch")
                raw = api(path + "/observations/" + quote(record["id"], safe="") + "/" + kind, binary=True)
                require(hashlib.sha256(raw).hexdigest() == expected, "observation " + kind + " SHA-256 mismatch")
        before = index.get("nextBefore")
        if not before:
            return details
        require(before not in cursors, "observation pagination did not advance")
        cursors.add(before)


def timestamp_ms(value):
    require(isinstance(value, str) and value, "event/capture timestamp is missing")
    stamp = datetime.fromisoformat(value)
    require(stamp.tzinfo is not None, "event/capture timestamp has no timezone")
    return int(stamp.timestamp()*1000)


def validate_observation_binding(task, events, index, event, detail):
    payload, step = event["payload"], event["stepId"]
    revision, command = task.get("currentRevision"), payload.get("commandId")
    require(type(revision) is int and revision > 0 and payload.get("taskRevision") == revision
            and detail.get("taskRevision") == revision, "observation task revision differs from execution")
    prefix = f"{task['id']}/revision/{revision}/step/{step}"
    require(isinstance(command, str) and (command == prefix or command.startswith(prefix+"/resume-read/")),
            "child command does not belong to this task/revision/step")
    starts = [e for e in events[:index] if e.get("type") == "TOOL_ACTIVITY" and e.get("stepId") == step
              and e.get("payload", {}).get("commandId") == command
              and e["payload"].get("activityStatus") in {"RUNNING", "SENDING"}]
    require(starts, "child observation has no matching command start")
    start = starts[-1]
    require(start["payload"].get("toolName") == payload.get("toolName")
            and start["payload"].get("arguments") == payload.get("arguments")
            and start["payload"].get("taskRevision") == revision, "child command changed after dispatch")
    snapshot = detail.get("snapshot", {})
    started = timestamp_ms(start.get("occurredAt"))
    # Scene reads can use the latest sensor frame. Motion and new verification
    # outcomes must instead have a capture taken after their own dispatch.
    if payload.get("toolName") in {"observe_scene", "resolve_targets", "plan_grasp"}:
        sensors = snapshot.get("robotProfile", {}).get("sensors", [])
        budgets = [s.get("maxAgeMs") for s in sensors if s.get("sourceId") == detail.get("sourceId")
                   and type(s.get("maxAgeMs")) is int and s["maxAgeMs"] > 0]
        if budgets:
            started -= min(min(budgets), 1000)
    captured = detail.get("observedAtUnixMs")
    require(type(captured) is int and captured > 0
            and started <= captured <= timestamp_ms(event.get("occurredAt")),
            "child observation is outside its command execution window")
    require(snapshot.get("taskId") == task["id"] and snapshot.get("stepId") == step
            and snapshot.get("taskRevision") == revision and snapshot.get("adapter") == "gazebo",
            "snapshot task/step/revision binding differs from metadata")
    require(timestamp_ms(snapshot.get("observedAt")) == captured, "snapshot capture time differs from metadata")
    reconstruction = snapshot.get("reconstruction", {})
    require(reconstruction.get("observationId") == detail.get("captureId")
            and reconstruction.get("observedAtUnixMs") == captured,
            "snapshot capture identity differs from command observation")
    require(bool(detail.get("robotId")) and detail["robotId"] == snapshot.get("robotId")
            == payload.get("robotId") == reconstruction.get("robotId"), "observation robot binding differs")


def same_pose(actual, expected):
    distance, angle = pose_error(actual, expected)
    return distance < 1e-6 and angle < 1e-6


def require_selector(state, snapshot, entity_id, selector):
    candidates = [e for e in state.get("semantic_objects", []) if e.get("id") == entity_id]
    candidates += [e for e in snapshot.get("entities", []) if e.get("entityId") == entity_id]
    require(candidates, "manipulation target has no observed or registered semantic identity")
    category = selector.get("category")
    require(category and any(e.get("category") == category for e in candidates),
            "manipulation target differs from frozen category")
    for key, value in selector.get("attributes", {}).items():
        require(any(e.get("attributes", {}).get(key) == value for e in candidates),
                "manipulation target differs from frozen attribute: " + key)


def validate_composite_outcomes(call, cap_step, rows):
    """Bind child outcomes to this frozen capability's room and object intent."""
    intent = call.get("legacyIntent") or {}
    # Repeated read-only checks after a resume do not add route visits.
    latest = {row["event"]["stepId"]: row for row in rows}
    ordered = sorted(latest.values(), key=lambda row: row["index"])
    route, pending_navigation, transfer, transfers = [], None, None, []
    for row in ordered:
        event, state, snapshot = row["event"], row["state"], row["detail"]["snapshot"]
        payload = event["payload"]
        tool, args = payload["toolName"], payload.get("arguments") or {}
        if tool == "navigation.navigate":
            require(pending_navigation is None, "navigation leg lacks its own arrival verification")
            pending_navigation = row
        elif tool == "verify_arrival":
            require(pending_navigation is not None, "arrival has no preceding navigation in its capability")
            nav_args = pending_navigation["event"]["payload"].get("arguments") or {}
            require(same_pose(args.get("goalPose"), nav_args.get("goalPose")), "arrival goal differs from its navigation leg")
            route.append(row)
            pending_navigation = None
        elif tool == "manipulation.pick":
            require(transfer is None, "new pick started before previous transfer was verified")
            obj = args.get("targetRef", args.get("objectId"))
            require(obj, "pick has no bound object")
            transfer = {"objectId": obj, "pickStepId": event["stepId"], "stage": "picked"}
        elif tool == "verify_grasp":
            require(transfer and transfer["stage"] == "picked" and args.get("objectId") == transfer["objectId"],
                    "grasp verification is not bound to the preceding pick")
            require_selector(state, snapshot, transfer["objectId"], intent.get("object", {}))
            check = state["verification"]
            require(check.get("observed_relation") == "held_by:"+row["detail"]["robotId"], "grasp relation does not show the picked object held by this robot")
            transfer.update(stage="grasped", graspStepId=event["stepId"])
        elif tool == "manipulation.place":
            require(transfer and transfer["stage"] == "grasped", "placement has no verified grasp of the same object")
            require(args.get("objectId", transfer["objectId"]) == transfer["objectId"], "place changed the grasped object")
            destination = args.get("targetRef", args.get("destinationId"))
            require(destination, "place has no bound destination")
            transfer.update(stage="placed", destinationId=destination, placeStepId=event["stepId"])
        elif tool == "verify_placement":
            require(transfer and transfer["stage"] == "placed" and args.get("objectId") == transfer["objectId"]
                    and args.get("destinationId") == transfer["destinationId"], "placement verification changed the picked object or place destination")
            require_selector(state, snapshot, transfer["destinationId"], intent.get("destination", {}))
            transfers.append({k: v for k, v in transfer.items() if k != "stage"})
            transfer = None
    require(pending_navigation is None, "navigation leg lacks its own arrival verification")
    require(transfer is None, "manipulation transfer did not complete all verification stages")
    action, rooms = intent.get("action"), intent.get("routeRooms", [])
    if action not in {"home_route", "home_manipulation"}:
        return {"capabilityStepId": cap_step, "rooms": [], "transfers": transfers}
    start = 1 if action == "home_manipulation" and intent.get("manipulationRouteIndex") != 0 else 0
    expected_rooms = rooms[start:]
    require(expected_rooms and len(route) == len(expected_rooms), "arrival count differs from this frozen capability route")
    checked_rooms = []
    for room, row in zip(expected_rooms, route):
        state, payload = row["state"], row["event"]["payload"]
        semantic, active = state.get("semantic_navigation") or {}, state.get("active_map") or {}
        require(semantic.get("frameId") == "world" and all(active.get(k) and semantic.get(k) == active[k]
                for k in ("mapId", "mapRevision", "calibrationRevision")), "arrival semantic goals are not bound to the active map")
        canonical = semantic.get("aliases", {}).get(room, room)
        goal = semantic.get("goals", {}).get(canonical)
        require(goal is not None and same_pose(payload.get("arguments", {}).get("goalPose"), goal),
                "arrival goal does not satisfy frozen room order: " + str(room))
        checked_rooms.append({"room": canonical, "stepId": row["event"]["stepId"]})
    if action == "home_manipulation":
        require(transfers, "frozen home manipulation completed without a verified transfer")
    return {"capabilityStepId": cap_step, "rooms": checked_rooms, "transfers": transfers}


def validate_execution(task, draft, catalog, details, expected_arrivals, require_manipulation):
    require(task.get("state") == "SUCCEEDED", "task ended in " + str(task.get("state")))
    require(task.get("approved") is True and task.get("plan") == draft, "executed plan differs from approved draft")
    events = task.get("events", [])
    require(any(e.get("type") == "TASK_APPROVED" for e in events), "approval event is missing")
    calls = draft["capabilities"]["calls"]
    verified = [e for e in events if e.get("type") == "CAPABILITY_VERIFIED"]
    require(len(verified) == len(calls), "missing or duplicate capability completion evidence")
    require(len({e.get("stepId") for e in verified}) == len(calls), "duplicate capability step verification")
    manifests = {item["name"]: item for item in catalog.get("services", [])}
    receipts = [e for e in events if e.get("type") == "CAPABILITY_RECEIPT"]
    latest_capability = {e.get("stepId"): e["type"] for e in events
                         if e.get("type") in {"CAPABILITY_CALL", "CAPABILITY_FAILED", "CAPABILITY_VERIFIED"}}
    require(all(kind == "CAPABILITY_VERIFIED" for kind in latest_capability.values()),
            "capability has a later failure or unfinished invocation")
    provider_steps = [e.get("stepId") for e in verified if e["payload"].get("tool") != "robot.task"]
    require([e.get("stepId") for e in receipts] == provider_steps, "receipt and verification steps differ")
    for call, event in zip(calls, verified):
        payload = event.get("payload", {})
        require(payload.get("tool") == call["tool"], "capability order/tool differs from frozen plan")
        evidence = payload.get("evidence")
        require(isinstance(evidence, dict) and evidence, "empty capability evidence")
        if call["tool"] == "robot.task":
            require(evidence.get("basis") == "LEGACY_RUNNER_VERIFIED_CHILD_STEPS", "composite lacks child verification")
            continue
        manifest = manifests.get(call["tool"])
        require(manifest is not None, "capability missing from retained catalog")
        contract = manifest.get("contract", {})
        verification = contract.get("verification") or {}
        for field in verification.get("required", []):
            require(at(evidence, field) not in (None, ""), "missing provider verification field: " + field)
        for field, source in verification.get("match", {}).items():
            expected = at({"arguments": call.get("arguments", {}), "result": payload.get("operationResult", {})}, source)
            require(expected is not None and at(evidence, field) == expected, "provider verification binding differs: " + field)
        operation = contract.get("operation")
        result = payload.get("operationResult", {})
        if operation and result.get("decision") != "reuse":
            require(at(result, operation["statePath"]) in operation["success"], "provider operation did not complete")

    capability_ids = {e.get("stepId") for e in verified}
    composite_calls = {event["stepId"]: call for call, event in zip(calls, verified) if call["tool"] == "robot.task"}
    composite_rows = {step: [] for step in composite_calls}
    latest_child = {e.get("stepId"): e.get("payload", {}).get("activityStatus") for e in events
                    if e.get("type") == "TOOL_ACTIVITY" and e.get("stepId") not in capability_ids}
    require(all(status == "CONFIRMED" for status in latest_child.values()),
            "child step has a later failure or unfinished invocation")
    confirmed = [e for e in events if e.get("type") == "TOOL_ACTIVITY"
                 and e.get("payload", {}).get("activityStatus") == "CONFIRMED" and e.get("stepId") not in capability_ids]
    child_started = {e.get("stepId") for e in events if e.get("type") == "TOOL_ACTIVITY"
                     and e.get("payload", {}).get("activityStatus") in {"RUNNING", "SENDING"}
                     and e.get("stepId") not in capability_ids}
    require(child_started <= {e.get("stepId") for e in confirmed}, "child step has no completion evidence")
    arrivals, manipulation, physical_steps, evidence_rows = {}, {}, set(), []
    for event in confirmed:
        step, payload = event.get("stepId"), event["payload"]
        index = next(i for i, item in enumerate(events) if item is event)
        parents = [cap for cap in composite_calls if step.startswith(cap+"-")]
        require(len(parents) == 1, "child step is not scoped to one frozen capability")
        parent = parents[0]
        require(any(e.get("type") == "CAPABILITY_CALL" and e.get("stepId") == parent for e in events[:index])
                and any(e.get("type") == "CAPABILITY_VERIFIED" and e.get("stepId") == parent for e in events[index+1:]),
                "child step is outside its capability execution interval")
        tool, capture = payload.get("toolName"), payload.get("receiptObservationId")
        detail = details.get((step, capture))
        require(payload.get("evidenceSource") == "command_observation" and detail is not None
                and capture in payload.get("evidenceIds", []), "child step lacks pinned command observation: " + str(step))
        require(detail.get("taskId") == task["id"] and detail.get("adapter") == "gazebo" and not detail.get("expired"),
                "observation task/adapter binding is invalid")
        require(detail.get("rgbSha256") and detail.get("depthSha256"), "child observation lacks RGB-D image hashes")
        validate_observation_binding(task, events, index, event, detail)
        if tool in PHYSICAL:
            require(step not in physical_steps, "physical step has duplicate confirmations: " + str(step))
            physical_steps.add(step)
        state = detail.get("snapshot", {}).get("robotState", {})
        composite_rows[parent].append({"index": index, "event": event, "state": state, "detail": detail})
        if tool == "verify_arrival":
            distance, angle = pose_error(state.get("base_pose"), payload.get("arguments", {}).get("goalPose"))
            require(distance <= .08 and angle <= .15, "arrival observation is outside commanded pose tolerance")
            arrivals[step] = {"stepId": step, "positionErrorM": distance, "yawErrorRad": angle}
        if tool == "navigation.navigate":
            route = state.get("map_route") or {}
            require(route.get("commandId") == payload.get("commandId") and bool(route.get("commandId")), "navigation receipt belongs to another command")
            require(all(route.get(k) for k in ("mapId", "mapRevision", "calibrationRevision")), "navigation lacks a versioned map receipt")
            require(same_pose(route.get("goalPose"), (payload.get("arguments") or {}).get("goalPose")),
                    "navigation receipt differs from dispatched goal")
            active = state.get("active_map") or {}
            require(all(active.get(k) == route[k] for k in ("mapId", "mapRevision", "calibrationRevision")),
                    "navigation receipt differs from active map binding")
            distance, angle = pose_error(state.get("base_pose"), route.get("goalPose"))
            require(distance <= .08 and angle <= .15, "navigation observation is outside arrival tolerance")
        if tool in {"verify_grasp", "verify_placement"}:
            check = state.get("verification") or {}
            args = payload.get("arguments", {})
            require(check.get("passed") is True and check.get("kind") == tool and check.get("evidence_source") == "gazebo_physics",
                    "manipulation lacks independent Gazebo physics verification")
            require(check.get("object_id") == args.get("objectId") and bool(args.get("objectId")), "manipulation observation belongs to another object")
            require(check.get("sample_count", 0) >= 3 and check.get("stable_duration_s", 0) >= .1,
                    "manipulation lacks stable verification samples")
            if tool == "verify_placement":
                require(check.get("destination_id") == args.get("destinationId") and args.get("destinationId")
                        and check.get("observed_relation") == "inside:" + args["destinationId"], "placement relation differs from requested destination")
            manipulation.setdefault(tool, []).append({"stepId": step, "verification": check})
        evidence_rows.append({"stepId": step, "tool": tool, "captureId": capture, "observationId": detail["id"]})
    composites = [validate_composite_outcomes(call, step, composite_rows[step]) for step, call in composite_calls.items()]
    require(len(arrivals) >= expected_arrivals, f"only {len(arrivals)} of {expected_arrivals} expected arrivals verified")
    if require_manipulation:
        require(all(manipulation.get(tool) for tool in ("verify_grasp", "verify_placement")), "grasp and placement verification are required")
        require({"manipulation.pick", "manipulation.place"} <= {e["payload"].get("toolName") for e in confirmed}, "pick/place execution is missing")
    return {"verifiedCapabilities": len(verified), "verifiedChildSteps": len({e["stepId"] for e in confirmed}),
            "verifiedArrivals": list(arrivals.values()), "manipulation": manipulation, "childEvidence": evidence_rows,
            "verifiedCompositeOutcomes": composites}


def evaluate(base_url, request, output, timeout=1800, *, required_source=None, expected_arrivals=1,
             require_manipulation=False, settle_seconds=10, poll_interval=1, pause_after_tool=None,
             pause_seconds=65, require_collaboration=False, require_auto_investigation=False,
             require_auto_recovery=False):
    parsed = urlsplit(base_url)
    require(parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
            and not parsed.username and not parsed.password and parsed.path in {"", "/"}
            and not parsed.query and not parsed.fragment, "a local HTTP console origin is required")
    require(all(math.isfinite(v) for v in (timeout, settle_seconds, poll_interval, pause_seconds))
            and timeout > 0 and settle_seconds >= 0 and poll_interval > 0 and pause_seconds > 60,
            "invalid timing; controlled long-horizon pauses must exceed 60 seconds")
    require(type(expected_arrivals) is int and expected_arrivals >= 0 and request.strip(), "invalid request/arrival requirement")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    install_loopback_opener()
    api = EvidenceAPI(base_url.rstrip("/"), output)
    report = {"schemaVersion": "long-horizon.acceptance.v1", "request": request, "passed": False,
              "physicalHardwareTested": False, "automaticTaskRecoveryObserved": False,
              "expectedArrivals": expected_arrivals, "requireManipulation": require_manipulation,
              "pause": {"requestedAfterTool": pause_after_tool, "requestedSeconds": pause_seconds if pause_after_tool else 0,
                        "initiator": "acceptance_operator", "resumed": False}}
    task = task_raw = path = catalog = None
    started = time.monotonic()
    try:
        catalog = api("/v1/robot/services")
        task = api("/v1/tasks", {"request": request, "adapter": "gazebo"})
        task_raw = api.last_raw
        report["taskId"] = task["id"]
        path = "/v1/tasks/" + quote(task["id"], safe="")
        api.save("draft-plan.json", task_raw, raw=True)
        draft = task.get("plan") or {}
        draft_revision = task.get("currentRevision")
        calls = draft.get("capabilities", {}).get("calls", [])
        require(calls and not task.get("approved"), "new task requires an unapproved capability plan")
        require(required_source is None or draft.get("source") == required_source, "unexpected plan source")
        require(not pause_after_tool or any(c["tool"] == pause_after_tool for c in calls), "pause target absent from plan")
        report.update(planSource=draft.get("source"), tools=[c["tool"] for c in calls])
        task = api(path + "/approve", {"operator": "long-horizon-acceptance"})
        task_raw = api.last_raw
        api.save("approved-plan.json", task_raw, raw=True)
        require(task.get("approved") is True and task.get("plan") == draft
                and task.get("currentRevision") == draft_revision, "approval changed the frozen plan")
        api.save("model-routes.json", api("/v1/config/status"))
        deadline = time.monotonic() + timeout
        paused_at = None
        pause_requested = False
        previous = None
        while time.monotonic() < deadline:
            task = api(path)
            task_raw = api.last_raw
            require(task.get("plan") == draft and task.get("currentRevision") == draft_revision, "plan changed during execution")
            if task.get("state") != previous:
                previous = task.get("state")
                print(json.dumps({"taskId": task["id"], "state": previous}), flush=True)
            if previous in TERMINAL:
                break
            if pause_after_tool and not pause_requested and any(e.get("type") == "CAPABILITY_VERIFIED"
                    and e.get("payload", {}).get("tool") == pause_after_tool for e in task.get("events", [])):
                api.save("pause-response.json", api(path + "/pause", {}))
                pause_requested = True
            if previous == "PAUSED" and pause_requested and paused_at is None:
                paused_at = time.monotonic()
                api.save("paused-task.json", task_raw, raw=True)
            if paused_at is not None and not report["pause"]["resumed"] and time.monotonic()-paused_at >= pause_seconds:
                api.save("resume-response.json", api(path + "/resume", {}))
                report["pause"].update(resumed=True, observedSeconds=time.monotonic()-paused_at)
            time.sleep(min(poll_interval, max(0, deadline-time.monotonic())))
        else:
            raise TimeoutError("task timeout; evidence retained and cancellation requested")
        api.save("terminal-task.json", task_raw, raw=True)
        # Investigations are asynchronous and may be appended after terminal state.
        settle_deadline = time.monotonic() + settle_seconds
        while time.monotonic() < settle_deadline:
            time.sleep(min(poll_interval, settle_deadline-time.monotonic()))
            task = api(path)
            task_raw = api.last_raw
        collaboration = collaboration_evidence(task, catalog)
        report["collaboration"] = {k: v for k, v in collaboration.items() if k != "events"}
        report["automaticTaskRecoveryObserved"] = collaboration["automaticTaskRecoveryObserved"]
        api.save("agent-events.json", collaboration)
        details = collect_observations(api, path)
        require(task.get("currentRevision") == draft_revision, "task revision changed after approval")
        report.update(validate_execution(task, draft, catalog, details, expected_arrivals, require_manipulation))
        require(not pause_after_tool or report["pause"]["resumed"], "task did not exercise the requested pause/resume")
        require(not require_collaboration or collaboration["collaborationObserved"], "ops/recovery collaboration was not observed")
        require(not require_auto_investigation or collaboration["automaticInvestigationVerified"], "automatic investigation was not executed and verified")
        require(not require_auto_recovery or collaboration["automaticTaskRecoveryObserved"],
                "ordered automatic read recovery chain and successful task completion were not verified")
        report["passed"] = True
    except Exception as error:  # noqa: BLE001 - every acceptance failure must retain original evidence
        report.update(errorType=type(error).__name__, error=str(error))
        if path:
            try:
                task = api(path)
                task_raw = api.last_raw
                if task.get("state") not in TERMINAL:
                    api(path + "/cancel", {})
                    report["cancellationRequested"] = True
                    task = api(path)
                    task_raw = api.last_raw
            except Exception as cleanup_error:  # noqa: BLE001 - cleanup must not replace the original failure
                report["cleanupError"] = f"{type(cleanup_error).__name__}: {cleanup_error}"
    finally:
        if task is not None:
            report["state"] = task.get("state")
            api.save("final-task.json", task_raw, raw=True)
            report.setdefault("collaboration", {k: v for k, v in collaboration_evidence(task, catalog).items() if k != "events"})
        report["elapsedSeconds"] = time.monotonic()-started
        api.save("report.json", report)
        api.save("manifest.json", {"algorithm": "sha256", "files": dict(api.files), "requests": api.requests})
    print(json.dumps({k: report[k] for k in ("taskId", "state", "passed", "errorType", "error") if k in report}, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8897")
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--required-source", choices=("llm", "deterministic"))
    parser.add_argument("--expected-arrivals", type=int, default=1)
    parser.add_argument("--require-manipulation", action="store_true")
    parser.add_argument("--settle-seconds", type=float, default=10)
    parser.add_argument("--poll-interval", type=float, default=1)
    parser.add_argument("--pause-after-tool", help="pause after this capability is verified; a later tool may already be running")
    parser.add_argument("--pause-seconds", type=float, default=65)
    parser.add_argument("--require-collaboration", action="store_true")
    parser.add_argument("--require-auto-investigation", action="store_true")
    parser.add_argument("--require-auto-recovery", action="store_true", help="require a bounded failed-read/ops/recovery/retry/verification chain and task success")
    args = parser.parse_args()
    return 0 if evaluate(**vars(args))["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
