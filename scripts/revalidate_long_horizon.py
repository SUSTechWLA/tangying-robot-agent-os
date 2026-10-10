"""Read-only independent revalidation of a retained long-horizon evidence pack.

No network, task creation, service calls, simulator resets, or evidence repairs.
The original manifest is verified first. Results are written exclusively to a
new file, including failures. Simulation evidence never certifies hardware.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

try:
    from . import evaluate_long_horizon as execution
except ImportError:
    import evaluate_long_horizon as execution

require = execution.require


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class EvidencePack:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.manifest_raw = (self.directory / "manifest.json").read_bytes()
        self.manifest = json.loads(self.manifest_raw)
        require(self.manifest.get("algorithm") == "sha256", "unsupported manifest algorithm")
        self.files = self.manifest.get("files")
        require(isinstance(self.files, dict) and self.files, "manifest has no files")
        self.total_bytes = 0
        for name, expected in self.files.items():
            require(isinstance(name, str) and name and Path(name).name == name,
                    "manifest file must be a flat local name")
            path = self.directory / name
            require(not path.is_symlink() and path.resolve().parent == self.directory,
                    "manifest file escapes evidence directory")
            raw = path.read_bytes()
            require(isinstance(expected, str) and digest(raw) == expected,
                    "manifest SHA-256 mismatch: " + name)
            self.total_bytes += len(raw)
        self.requests = self.manifest.get("requests", [])
        require(isinstance(self.requests, list) and self.requests, "manifest has no request index")
        for row in self.requests:
            require(row.get("file") in self.files and row.get("method") in {"GET", "POST"}
                    and isinstance(row.get("path"), str) and row["path"].startswith("/v1/"),
                    "invalid retained API request index")

    def raw(self, name):
        require(name in self.files, "required evidence is not in manifest: " + name)
        return (self.directory / name).read_bytes()

    def json(self, name):
        return json.loads(self.raw(name))

    def responses(self, path, method="GET"):
        return [row for row in self.requests if row["path"] == path and row["method"] == method
                and row.get("httpStatus", 200) < 400]

    def response(self, path, method="GET"):
        rows = self.responses(path, method)
        require(rows, "missing retained API response: " + path)
        return self.json(rows[-1]["file"])


def observation_evidence(pack, task):
    prefix = "/v1/tasks/" + quote(task["id"], safe="") + "/observations"
    pages = [row for row in pack.requests if row["method"] == "GET"
             and urlsplit(row["path"]).path == prefix and row.get("httpStatus", 200) < 400]
    require(pages, "observation index is missing")
    details, seen, images = {}, set(), 0
    for row in pages:
        index = pack.json(row["file"])
        require(isinstance(index.get("records"), list), "invalid observation index")
        for record in index["records"]:
            identifier = record.get("id")
            require(isinstance(identifier, str) and identifier and identifier not in seen,
                    "duplicate/missing observation index identity")
            seen.add(identifier)
            path = prefix + "/" + quote(identifier, safe="")
            detail = pack.response(path)
            require(detail.get("id") == identifier and detail.get("taskId") == task["id"],
                    "observation index/detail task or identity mismatch")
            for field in ("taskId", "stepId", "captureId", "taskRevision", "robotId", "snapshotSha256"):
                if field in record:
                    require(record[field] == detail.get(field), "observation index/detail differs: " + field)
            key = detail.get("stepId"), detail.get("captureId")
            require(key not in details, "duplicate pinned observation identity")
            details[key] = detail
            response = pack.responses(path)[-1]
            snapshot = json_spans(pack.raw(response["file"]).decode("utf-8")).get(("snapshot",))
            require(snapshot is not None and digest(snapshot.encode()) == detail.get("snapshotSha256"),
                    "original observation snapshot SHA-256 mismatch or missing")
            if "snapshotBytes" in detail:
                require(len(snapshot.encode()) == detail["snapshotBytes"], "snapshot byte count differs")
            for kind in ("rgb", "depth"):
                expected = record.get(kind + "Sha256")
                if not expected:
                    continue
                require(detail.get(kind + "Sha256") == expected, "index/detail image digest mismatch")
                responses = pack.responses(path + "/" + kind)
                require(len(responses) == 1 and digest(pack.raw(responses[0]["file"])) == expected,
                        "retained image SHA-256 mismatch or image missing")
                images += 1
    return details, {"observationCount": len(seen), "imageCount": images}


def pause_evidence(pack, task, minimum):
    if minimum <= 0:
        return {"requiredSeconds": 0, "observed": False}
    events = task.get("events", [])
    intervals = []
    for i, event in enumerate(events):
        p = event.get("payload", {})
        if event.get("type") != "state.transition" or p.get("state") != "PAUSED":
            continue
        end = next((e for e in events[i+1:] if e.get("type") == "state.transition"
                    and e.get("payload", {}).get("previousState") == "PAUSED"
                    and e.get("payload", {}).get("state") != "PAUSED"), None)
        if end:
            seconds = (datetime.fromisoformat(end["occurredAt"])
                       - datetime.fromisoformat(event["occurredAt"])).total_seconds()
            intervals.append({"startSequence": event.get("sequence"), "endSequence": end.get("sequence"),
                              "seconds": seconds})
    eligible = [row for row in intervals if row["seconds"] >= minimum]
    require(eligible, "no task event pause/resume interval meets required duration")
    path = "/v1/tasks/" + quote(task["id"], safe="")
    require(len(pack.responses(path + "/pause", "POST")) == 1
            and len(pack.responses(path + "/resume", "POST")) == 1, "pause/resume request evidence missing or duplicated")
    paused = pack.json("paused-task.json")
    require(paused.get("id") == task["id"] and paused.get("state") == "PAUSED"
            and paused.get("plan") == task.get("plan") and paused.get("currentRevision") == task.get("currentRevision"),
            "paused task differs from frozen task")
    return {"requiredSeconds": minimum, "observed": True, "intervals": intervals,
            "initiator": "acceptance_operator", "automaticRecovery": False}


def ledger_evidence(pack, task, draft, approved):
    path = "/v1/tasks/" + quote(task["id"], safe="")
    approvals = pack.responses(path + "/approve", "POST")
    require(len(approvals) == 1 and pack.json(approvals[0]["file"]) == approved,
            "approved plan is not the sole original approval response")
    events = task.get("events", [])
    require([e.get("sequence") for e in events] == list(range(1, len(events) + 1)),
            "task ledger sequence is missing, duplicated, reordered or incomplete")
    snapshots = pack.responses(path)
    require(snapshots and pack.json(snapshots[-1]["file"]) == task, "final task is not the last retained task response")
    for row in snapshots:
        prior = pack.json(row["file"])
        require(prior.get("id") == task["id"] and prior.get("plan") == draft.get("plan")
                and prior.get("currentRevision") == draft.get("currentRevision"),
                "an intermediate snapshot changed the frozen task plan/revision")
        history = prior.get("events", [])
        require(history == events[:len(history)], "retained task ledger is not an immutable prefix of final events")
    result = {"eventCount": len(events), "taskSnapshotsVerified": len(snapshots), "revisionHistoryAvailable": False}
    if "revisions.json" in pack.files:
        history = pack.json("revisions.json")
        require(history.get("schemaVersion") == "task.revisions.v1" and history.get("taskId") == task["id"],
                "revision history has unsupported schema/task identity")
        require(history == pack.response(path + "/revisions"), "revision history differs from retained endpoint response")
        revisions = [r.get("revision", {}) for r in history.get("revisions", [])]
        current = [r for r in revisions if r.get("revision") == task.get("currentRevision")]
        require(len(current) == 1 and current[0].get("taskId") == task["id"]
                and current[0].get("request") == task.get("request") and current[0].get("plan") == task.get("plan"),
                "immutable revision source differs from executed task")
        result.update(revisionHistoryAvailable=True, revisionCount=len(revisions))
    return result


def fault_evidence(pack, task, collaboration, required):
    if "fault-injection.jsonl" not in pack.files:
        require(not required, "proxy fault snapshot is missing")
        return {"observed": False}
    rows = [json.loads(line) for line in pack.raw("fault-injection.jsonl").splitlines() if line.strip()]
    ready = [row for row in rows if row.get("event") == "proxy_ready"]
    injected = [row for row in rows if row.get("event") == "fault_injected"]
    require(len(ready) == 1 and ready[0].get("adapter") == "gazebo"
            and ready[0].get("mutates_world") is False, "proxy did not prove a single real-Gazebo read-only deployment")
    if not injected:
        require(not required, "proxy was armed but no fault was observed")
        return {"observed": False, "proxyReady": True}
    require(len(injected) == 1, "expected exactly one bounded fault injection")
    fault = injected[0]
    require(fault.get("forwarded") is False and fault.get("grpc_status") == "UNAVAILABLE"
            and fault.get("injection_number") == 1 and fault.get("fail_count") == 1
            and fault.get("service") == ready[0].get("service"), "unexpected injection semantics")
    chains = collaboration.get("verifiedReadRecoveryChains", [])
    matches = [chain for chain in chains if chain["commandId"] == fault.get("request_id")
               and chain["tool"] == fault.get("service") and chain["taskId"] == task["id"]]
    require(len(matches) == 1, "proxy injection is not bound to this task's verified recovery chain")
    chain = matches[0]
    events = task["events"]
    injected_at = execution.timestamp_ms(fault.get("time_utc"))
    start = events[chain["eventIndices"][0]]
    failure = events[chain["eventIndices"][2]]
    require(execution.timestamp_ms(start.get("occurredAt")) <= injected_at
            <= execution.timestamp_ms(failure.get("occurredAt")), "injection timestamp lies outside original dispatch/failure")
    return {"observed": True, "count": 1, "fault": fault, "recoveryPlanId": chain["planId"]}


def json_spans(text):
    """Retain original JSON token bytes; reserializing decoded objects is unsafe."""
    decoder, spans = json.JSONDecoder(), {}

    def white(position):
        while position < len(text) and text[position].isspace():
            position += 1
        return position

    def value(position, path):
        start = position = white(position)
        if text[position] == "{":
            position = white(position + 1)
            while text[position] != "}":
                key, position = decoder.raw_decode(text, position)
                position = white(position)
                require(text[position] == ":", "invalid JSON object delimiter")
                position = white(value(position + 1, (*path, key)))
                if text[position] == ",":
                    position = white(position + 1)
                else:
                    break
            position += 1
        elif text[position] == "[":
            position, index = white(position + 1), 0
            while text[position] != "]":
                position = white(value(position, (*path, index)))
                index += 1
                if text[position] == ",":
                    position = white(position + 1)
                else:
                    break
            position += 1
        else:
            _, position = decoder.raw_decode(text, position)
        if path and path[-1] in {"data", "model_request", "snapshot"}:
            spans[path] = text[start:position]
        return position

    value(0, ())
    return spans


def display_view_lossy(source, display):
    """Permit only float64 coercion in a compatibility view; source stays authoritative."""
    if isinstance(source, dict):
        require(isinstance(display, dict) and source.keys() == display.keys(), "display object fields differ from exact source")
        losses = [display_view_lossy(value, display[key]) for key, value in source.items()]
        return any(losses)
    if isinstance(source, list):
        require(isinstance(display, list) and len(source) == len(display), "display array differs from exact source")
        losses = [display_view_lossy(value, shown) for value, shown in zip(source, display, strict=True)]
        return any(losses)
    numeric = type(source) in (int, float) and type(display) in (int, float)
    if numeric:
        if source == display:
            return False
        require(display == float(source), "display number differs beyond float64 coercion")
        return True
    require(type(source) is type(display) and source == display, "display business value differs from exact source")
    return False


def context_evidence(raw, task, *, required=False, require_model=False):
    text = raw.decode("utf-8")
    spans = json_spans(text)
    contexts, retrievals = [], []

    def visit(value, path=()):
        if isinstance(value, dict):
            if value.get("tool") == "context_read" and value.get("verdict") == "SATISFIED":
                retrievals.append((path, value))
            if str(value.get("renderer_version", "")).startswith("managed-context."):
                require(value["renderer_version"] in {"managed-context.v1", "managed-context.v2"},
                        "unsupported managed context version")
                contexts.append((path, value))
                return
            for key, child in value.items():
                visit(child, (*path, key))
        elif isinstance(value, list):
            for i, child in enumerate(value):
                visit(child, (*path, i))

    visit(task)
    require(not required or contexts, "no actual managed decision context was retained")
    summaries, archive_bytes = [], {}
    for path, p in contexts:
        body = p.get("text")
        require(isinstance(body, str) and digest(body.encode()) == p.get("sha256"), "managed context Text SHA mismatch")
        d = json.loads(body)
        scope = p.get("scope")
        require(scope == d.get("scope") and scope.get("task_id") in ("", task["id"]), "managed context scope mismatch")
        compaction = p.get("compaction", {})
        budget = compaction.get("budget", {})
        require(compaction.get("input_bytes") == len(body.encode())
                and 0 < len(body.encode()) <= budget.get("max_context_bytes", 0), "managed context byte budget mismatch")
        require(re.fullmatch(r"[0-9a-f]{64}", compaction.get("source_sha256", "")), "missing original source identity")
        archive_hashes, lossy_archives = set(), []
        for i, artifact in enumerate(p.get("artifacts", [])):
            source = artifact.get("data_json")
            data = source if isinstance(source, str) else spans.get((*path, "artifacts", i, "data"))
            require(isinstance(data, str) and digest(data.encode()) == artifact.get("sha256"),
                    "artifact original byte SHA mismatch; parsed JSON cannot substitute for original bytes")
            rows = json.loads(data)
            require(artifact.get("scope") == scope and isinstance(rows, list)
                    and len(rows) == artifact.get("count"), "archive scope/count differs")
            if display_view_lossy(rows, artifact.get("data")):
                lossy_archives.append(artifact["sha256"])
            archive_hashes.add(artifact["sha256"])
            archive_bytes[path, artifact["sha256"]] = (scope, data)
        request = p.get("model_request")
        model_display_lossy = False
        if request is not None:
            request_raw = p.get("model_request_json")
            if not isinstance(request_raw, str):
                request_raw = spans.get((*path, "model_request"))
            require(isinstance(request_raw, str) and digest(request_raw.encode()) == p.get("model_request_sha256"),
                    "model request original byte SHA mismatch")
            request = json.loads(request_raw)
            model_display_lossy = display_view_lossy(request, p.get("model_request"))
            require(len(request_raw.encode()) <= budget.get("max_request_bytes", 0)
                    and request.get("max_tokens") == budget.get("output_tokens"), "model request budget/output reserve differs")
            messages = request.get("messages", [])
            require(messages and messages[-1].get("role") == "user" and messages[-1].get("content") == body,
                    "actual model request did not contain the recorded context")
        summaries.append({"path": list(path), "sha256": p["sha256"], "stage": p.get("stage"), "scope": scope,
                          "inputBytes": len(body.encode()), "archiveCount": len(archive_hashes),
                          "archivedRecords": compaction.get("archived_records", 0),
                          "archivedAttempts": compaction.get("archived_attempts", 0),
                          "modelRequestVerified": request is not None,
                          "displayLossy": bool(lossy_archives) or model_display_lossy,
                          "lossyArchiveDisplays": lossy_archives, "modelRequestDisplayLossy": model_display_lossy,
                          "sourcePreimageVerified": False})
    model_count = sum(row["modelRequestVerified"] for row in summaries)
    require(not require_model or model_count > 0, "no actual LLM input envelope was retained and verified")
    for path, row in retrievals:
        page = row.get("resultDetail", {})
        arguments = row.get("arguments", {})
        require(arguments.get("sha256") == page.get("sha256")
                and arguments.get("item_id", "") == page.get("item_id", "")
                and arguments.get("offset", 0) == page.get("offset"), "context_read result differs from requested archive/page")
        # The production loop keeps prior bundles readable while its ledger grows.
        # Only earlier rounds in this same loop and exact scope can supply them.
        scope = row.get("context", {}).get("scope")
        candidates = [data for (archive_path, sha), (archive_scope, data) in archive_bytes.items()
                      if sha == page.get("sha256") and archive_scope == scope
                      and archive_path[:-2] == path[:-1]
                      and type(archive_path[-2]) is int and type(path[-1]) is int
                      and archive_path[-2] <= path[-1]]
        require(candidates and len(set(candidates)) == 1,
                "context_read page does not belong to this loop's current or previous decision snapshots")
        source = candidates[0]
        if page.get("item_id"):
            decoder, position, items = json.JSONDecoder(), 1, []
            while position < len(source):
                while position < len(source) and (source[position].isspace() or source[position] == ","):
                    position += 1
                if source[position] == "]":
                    break
                item, end = decoder.raw_decode(source, position)
                if item.get("id") == page["item_id"]:
                    items.append(source[position:end])
                position = end
            require(len(items) == 1, "context_read item is missing or ambiguous")
            source = items[0]
        original = source.encode()
        start, end = page.get("offset"), page.get("next_offset")
        require(type(start) is int and type(end) is int and 0 <= start <= end <= len(original)
                and page.get("total_bytes") == len(original) and page.get("complete") == (end == len(original))
                and original[start:end].decode("utf-8") == page.get("content"), "context_read page differs from exact archive bytes")
    return {"managedContextCount": len(summaries), "modelRequestCount": model_count,
            "compactedContextCount": sum(row["archiveCount"] > 0 for row in summaries),
            "displayLossy": any(row["displayLossy"] for row in summaries),
            "verifiedContextReadPages": len(retrievals),
            "contexts": summaries,
            "interpretation": "A source hash without its preimage is an identity, not independent proof of complete source retention."}


def protocol_evidence(task, collaboration, required=False):
    """Validate v1 when present; legacy events stay explicitly unversioned."""
    events = task.get("events", [])
    versioned = [(i, e, e.get("payload", {})) for i, e in enumerate(events)
                 if e.get("payload", {}).get("protocolVersion")]
    require(not required or versioned, "no versioned agent protocol events were retained")
    identities = set()
    for _, event, payload in versioned:
        require(payload["protocolVersion"] == "agent.event.v1", "unsupported agent protocol version")
        identifier = payload.get("eventId")
        require(identifier and identifier not in identities, "missing or duplicated agent protocol event identity")
        identities.add(identifier)
        require(payload.get("correlationId") == task["id"] and payload.get("agent"),
                "agent protocol task correlation/publisher missing")
        require(payload.get("taskId", task["id"]) == task["id"], "agent protocol belongs to another task")
        if payload.get("stepId") and event.get("stepId"):
            require(payload["stepId"] == event["stepId"], "agent protocol envelope/body step differs")
    linked = []
    for chain in collaboration.get("verifiedReadRecoveryChains", []):
        ops_index, plan_index, executed_index = chain["eventIndices"][3:6]
        selected = [events[i].get("payload", {}) for i in (ops_index, plan_index, executed_index)]
        if not any(p.get("protocolVersion") for p in selected):
            require(not required, "verified recovery chain has only legacy unversioned events")
            continue
        require(all(p.get("protocolVersion") == "agent.event.v1" for p in selected),
                "recovery chain mixes versioned and unversioned identities")
        ops, plan, executed = selected
        robot = task.get("plan", {}).get("capabilities", {}).get("robotId")
        expected = {"taskId": task["id"], "taskRevision": task.get("currentRevision"),
                    "robotId": robot, "stepId": chain["stepId"], "commandId": chain["commandId"]}
        require(robot and type(expected["taskRevision"]) is int and expected["taskRevision"] > 0,
                "frozen task lacks protocol robot/revision identity")
        source_id = ops.get("binding", {}).get("sourceEventId")
        request_index = chain["eventIndices"][1]
        source = [(i, e) for i, e in enumerate(events[:ops_index])
                  if request_index < i and e.get("type") == "TOOL_ACTIVITY"
                  and e.get("payload", {}).get("eventId") == source_id and source_id]
        require(source, "recovery binding source TOOL_ACTIVITY is absent between this recovery request and diagnosis")
        require(any(e.get("payload", {}).get("activityStatus") == "FAILED"
                    and e.get("payload", {}).get("toolName") == chain["tool"]
                    and e.get("payload", {}).get("mutatesWorld") is False
                    and e.get("payload", {}).get("outcomeUnknown") is not True
                    and type(e.get("payload", {}).get("taskRevision")) is int
                    and all(e.get("payload", {}).get(k) == v for k, v in expected.items() if k != "taskId")
                    for _, e in source), "recovery source does not match exact failed command identity")
        for i, payload in enumerate(selected):
            binding = payload.get("binding", {})
            require(type(binding.get("taskRevision")) is int
                    and all(binding.get(k) == v for k, v in expected.items())
                    and binding.get("sourceEventId") == source_id, "recovery binding changed task/revision/robot/step/command/source")
            require(binding.get("anomalyEventId") == ops.get("eventId"), "recovery binding names another anomaly event")
            if i > 0:
                require(binding.get("planEventId") == plan.get("eventId"), "recovery binding names another plan event")
            for key in ("taskRevision", "robotId", "stepId", "commandId"):
                require(type(payload.get(key)) is type(expected[key]) and payload.get(key) == expected[key],
                        "recovery envelope disagrees with binding: " + key)
        require(ops.get("causationId") == source_id and plan.get("causationId") == ops.get("eventId")
                and executed.get("causationId") == plan.get("eventId"), "recovery causation chain differs from event identities")
        require(executed.get("agent") == "recovery-executor", "v1 execution receipt lacks its distinct executor publisher")
        linked.append({**expected, "sourceEventId": source_id, "anomalyEventId": ops["eventId"],
                       "planEventId": plan["eventId"], "executionEventId": executed["eventId"]})
    require(not required or linked, "no complete versioned recovery protocol chain was verified")
    return {"versionedEventCount": len(versioned), "versions": sorted({p["protocolVersion"] for _, _, p in versioned}),
            "verifiedRecoveryBindings": linked, "legacyEventsAcceptedForHistoricalReplay": not required}


def revalidate(evidence, output, *, expected_arrivals=1, require_manipulation=False,
               required_source="llm", require_auto_recovery=False, pause_seconds=0,
               require_fault=False, require_managed_context=False, require_model_context=False,
               require_protocol=False):
    output = Path(output)
    require(not output.exists(), "revalidation output already exists")
    report = {"schemaVersion": "long-horizon.revalidation.v2", "passed": False,
              "revalidatedAt": datetime.now(UTC).isoformat(), "evidenceDirectory": str(Path(evidence).resolve()),
              "validatorSha256": digest(Path(__file__).read_bytes()),
              "executionValidatorSha256": digest(Path(execution.__file__).read_bytes()),
              "physicalHardwareTested": False, "checks": {}, "errors": [],
              "requirements": {"expectedArrivals": expected_arrivals, "requireManipulation": require_manipulation,
                               "requiredSource": required_source, "requireAutoRecovery": require_auto_recovery,
                               "pauseSeconds": pause_seconds, "requireFault": require_fault,
                               "requireManagedContext": require_managed_context, "requireModelContext": require_model_context,
                               "requireProtocol": require_protocol}}
    try:
        pack = EvidencePack(evidence)
        report.update(originalManifestSha256=digest(pack.manifest_raw), verifiedFiles=len(pack.files),
                      verifiedBytes=pack.total_bytes)
        report["checks"]["manifest"] = True
        task = pack.json("final-task.json")
        draft, approved = pack.json("draft-plan.json"), pack.json("approved-plan.json")
        report.update(taskId=task["id"], state=task.get("state"))
        require(draft.get("id") == approved.get("id") == task["id"] and draft.get("approved") is False
                and approved.get("approved") is True and draft.get("plan") == approved.get("plan") == task.get("plan")
                and draft.get("currentRevision") == approved.get("currentRevision") == task.get("currentRevision"),
                "draft/approval/final immutable plan or revision differs")
        require(required_source is None or draft["plan"].get("source") == required_source, "wrong frozen plan source")
        creates = pack.responses("/v1/tasks", "POST")
        require(len(creates) == 1 and pack.json(creates[0]["file"]) == draft, "draft is not the sole original task creation response")
        report["ledger"] = ledger_evidence(pack, task, draft, approved)
        report["checks"]["frozenPlanAndLedger"] = True
        catalog = pack.response("/v1/robot/services")
        details, observations = observation_evidence(pack, task)
        report["observations"] = observations
        report["checks"]["observationBytes"] = True
        report["execution"] = execution.validate_execution(task, draft["plan"], catalog, details,
                                                          expected_arrivals, require_manipulation)
        collaboration = execution.collaboration_evidence(task, catalog)
        report["collaboration"] = {k: v for k, v in collaboration.items() if k != "events"}
        require(not require_auto_recovery or (collaboration["collaborationObserved"]
                and collaboration["automaticInvestigationVerified"] and collaboration["automaticTaskRecoveryObserved"]),
                "same-task automatic recovery/collaboration chain is incomplete")
        report["pause"] = pause_evidence(pack, task, pause_seconds)
        report["fault"] = fault_evidence(pack, task, collaboration, require_fault)
        report["protocol"] = protocol_evidence(task, collaboration, require_protocol)
        report["context"] = context_evidence(pack.raw("final-task.json"), task,
                                             required=require_managed_context, require_model=require_model_context)
        report["checks"].update(execution=True, collaboration=True, pause=True, fault=True, protocol=True, context=True)
        report["passed"] = True
    except Exception as error:  # noqa: BLE001 - retain rejection without touching original evidence
        report["errors"].append({"type": type(error).__name__, "message": str(error)})
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-arrivals", type=int, default=1)
    parser.add_argument("--require-manipulation", action="store_true")
    parser.add_argument("--required-source", choices=("llm", "deterministic"), default="llm")
    parser.add_argument("--require-auto-recovery", action="store_true")
    parser.add_argument("--pause-seconds", type=float, default=0)
    parser.add_argument("--require-fault", action="store_true")
    parser.add_argument("--require-managed-context", action="store_true")
    parser.add_argument("--require-model-context", action="store_true")
    parser.add_argument("--require-protocol", action="store_true", help="require agent.event.v1 identities and bound recovery causation")
    report = revalidate(**vars(parser.parse_args()))
    print(json.dumps({k: report[k] for k in ("taskId", "state", "passed", "errors") if k in report}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
