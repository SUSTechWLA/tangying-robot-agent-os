"""Independent read-only audit of retained task004/task005 and the usage ledger.
No API call, environment/credential read, live database write, or robot command.
"""

import argparse
import collections
import datetime
import hashlib
import json
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
base = args.repo / "artifacts/acceptance/user-e2e-20261028-001"
out = args.output
out.mkdir(parents=True, exist_ok=False)
sources = {}


def load(rel):
    p = base / rel
    data = p.read_bytes()
    sources[rel] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return json.loads(data)


def save(name, value):
    (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def stamp(s):
    return datetime.datetime.fromisoformat(s).timestamp() * 1000


t4 = load("task-004-draft/cancelled.json")
d4 = load("task-004-draft/draft.json")
t5 = load("task-005-closure/api-final-after-backup.json")
before = load("task-005-closure/api-final-before-backup.json")
collector = load("task-005-closure/report.json")
manifest = load("task-005-closure/manifest.json")
for name, record in manifest.items():
    p = base / "task-005-closure" / name
    assert p.stat().st_size == record["bytes"], name
    assert hashlib.sha256(p.read_bytes()).hexdigest() == record["sha256"], name

# Read the already retained online-backup image, not the live agent database.
db_path = base / "task-005-closure/journal-backup.sqlite"
assert not any(Path(str(db_path) + suffix).exists() for suffix in ("-wal", "-shm"))
sources["task-005-closure/journal-backup.sqlite"] = manifest["journal-backup.sqlite"]
with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as db:
    db.row_factory = sqlite3.Row
    tasks = [
        dict(r)
        for r in db.execute("SELECT id,state,approved,current_revision FROM tasks ORDER BY id")
    ]
    event_counts = {
        r["task_id"]: r["n"]
        for r in db.execute("SELECT task_id,COUNT(*) AS n FROM task_events GROUP BY task_id")
    }
    observations5 = db.execute(
        "SELECT COUNT(*) FROM observation_evidence WHERE task_id=?", (t5["id"],)
    ).fetchone()[0]
    assert observations5 == 0
    raw5 = dict(
        db.execute(
            "SELECT id,state,current_revision,approved,plan_json,request FROM tasks WHERE id=?",
            (t5["id"],),
        ).fetchone()
    )
    assert (
        raw5["id"] == t5["id"]
        and raw5["state"] == t5["state"]
        and raw5["current_revision"] == t5["currentRevision"]
    )
    assert (
        bool(raw5["approved"]) == t5["approved"]
        and json.loads(raw5["plan_json"]) == t5["plan"]
        and raw5["request"] == t5["request"]
    )
    db_events = [
        dict(r)
        for r in db.execute(
            "SELECT sequence,type,step_id,message,payload_json,occurred_at FROM task_events WHERE task_id=? ORDER BY sequence",
            (t5["id"],),
        )
    ]
    assert len(db_events) == len(t5["events"])
    for row, event in zip(db_events, t5["events"], strict=True):
        assert [
            row["sequence"],
            row["type"],
            row["step_id"],
            row["message"],
            json.loads(row["payload_json"]),
            row["occurred_at"],
        ] == [
            event["sequence"],
            event["type"],
            event.get("stepId", ""),
            event.get("message", ""),
            event.get("payload"),
            event["occurredAt"],
        ]
    steps = [
        dict(r)
        for r in db.execute(
            "SELECT step_id,status,reconcile_outcome FROM step_runs WHERE task_id=? ORDER BY step_id",
            (t5["id"],),
        )
    ]
    assert len(steps) == 3 and all(
        r["status"] == "COMPLETED" and not r["reconcile_outcome"] for r in steps
    )
    db4 = dict(db.execute("SELECT state,approved FROM tasks WHERE id=?", (t4["id"],)).fetchone())
    assert db4["state"] == t4["state"] and bool(db4["approved"]) == t4["approved"]
    events4 = [
        dict(r)
        for r in db.execute(
            "SELECT sequence,type,payload_json FROM task_events WHERE task_id=? ORDER BY sequence",
            (t4["id"],),
        )
    ]
    assert len(events4) == len(t4["events"]) == 3
    assert [r["type"] for r in events4] == [e["type"] for e in t4["events"]]
    step4count = db.execute(
        "SELECT COUNT(*) FROM step_runs WHERE task_id=?", (t4["id"],)
    ).fetchone()[0]
    assert step4count == 0
    historical_failure_rows = [
        dict(r)
        for r in db.execute(
            "SELECT task_id,sequence,type,payload_json FROM task_events WHERE type='state.transition' ORDER BY task_id,sequence"
        )
        if json.loads(r["payload_json"]).get("state") in ("FAILED", "RECOVERABLE_FAILURE")
    ]

assert t5 == before
assert [e["sequence"] for e in t5["events"]] == list(range(1, len(t5["events"]) + 1))
assert t5["state"] == "SUCCEEDED" and t5["currentRevision"] == 1
terminal = [
    e
    for e in t5["events"]
    if e["type"] == "state.transition" and e.get("payload", {}).get("state") == "SUCCEEDED"
][-1]
success = [e for e in t5["events"] if e["type"] == "LOCAL_RUN_SUCCEEDED"][-1]
finish = [e for e in t5["events"] if e["type"] == "LOCAL_RUN_FINISHED"][-1]
assert terminal["sequence"] < success["sequence"] < finish["sequence"]
assert (
    success["payload"]["taskRevision"] == finish["payload"]["taskRevision"] == t5["currentRevision"]
)
assert finish["payload"]["state"] == t5["state"]
calls = [e for e in t5["events"] if e["type"] == "CAPABILITY_CALL"]
verified = [e for e in t5["events"] if e["type"] == "CAPABILITY_VERIFIED"]
receipts = [e for e in t5["events"] if e["type"] == "CAPABILITY_RECEIPT"]
expected_tools = ["navigation.status", "mapping.inventory", "mapping.status"]
assert [e["payload"]["tool"] for e in calls] == expected_tools
assert len(calls) == len(verified) == len(receipts) == 3
for index, (call, receipt, verification) in enumerate(
    zip(calls, receipts, verified, strict=True), 1
):
    step = f"rev-1-cap-{index:02}"
    for event in (call, receipt, verification):
        payload = event["payload"]
        assert event["stepId"] == step and payload["robotId"] == "gazebo-home_furnished"
        assert payload["taskRevision"] == 1 and payload["commandId"] == t5["id"] + "/" + step
        assert payload["tool"] == expected_tools[index - 1] and payload["mutatesWorld"] is False
    assert call["sequence"] < receipt["sequence"] < verification["sequence"] < terminal["sequence"]
    assert (
        receipt["payload"]["result"]
        == verification["payload"]["operationResult"]
        == verification["payload"]["evidence"]["readResult"]
    )
assert (
    t4["id"] == d4["id"]
    and t4["plan"] == d4["plan"]
    and not t4["approved"]
    and t4["state"] == "CANCELLED"
)
assert t4["events"][-1]["payload"]["previousState"] == "READY"
assert t4["events"][-1]["payload"]["state"] == "CANCELLED"

ledger_path = base / "model-usage.jsonl"
ledger_bytes = ledger_path.read_bytes()
sources["model-usage.jsonl"] = {
    "bytes": len(ledger_bytes),
    "sha256": hashlib.sha256(ledger_bytes).hexdigest(),
}
(out / "model-usage-snapshot.jsonl").write_bytes(ledger_bytes)
ledger = [json.loads(line) for line in ledger_bytes.splitlines() if line.strip()]
starts = {}
results = {}
for item in ledger:
    assert item["schema_version"] == "agent.model-usage.v1"
    target = starts if item["event"] == "start" else results
    assert item["event"] in ("start", "result") and item["call"]["call_id"] not in target
    target[item["call"]["call_id"]] = item
assert starts.keys() == results.keys()


def usage(task):
    trace = task["plan"]["capabilities"]["planningTrace"]
    calls = [
        step["context"]["model_call"] for step in trace if step.get("context", {}).get("model_call")
    ]
    assert len({c["call_id"] for c in calls}) == len(calls)
    fields = ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens")
    totals = {f: 0 for f in fields}
    for c in calls:
        actual = results[c["call_id"]]["call"]
        start = starts[c["call_id"]]["call"]
        for key in ("call_id", "model", "stage", "started_at", "request_bytes", "output_tokens"):
            assert actual[key] == c[key] == start[key], key
        for key in (
            "usage",
            "usage_status",
            "http_status",
            "outcome",
            "duration_ms",
            "response_bytes",
        ):
            assert actual[key] == c[key], key
        assert results[c["call_id"]]["scope"]["robot_id"] == task["plan"]["capabilities"]["robotId"]
        assert c["usage_status"] == "valid" and c["http_status"] == 200
        assert all(type(c["usage"][f]) is int and c["usage"][f] >= 0 for f in fields)
        assert (
            c["usage"]["total_tokens"]
            == c["usage"]["prompt_tokens"] + c["usage"]["completion_tokens"]
        )
        assert c["usage"]["cached_tokens"] <= c["usage"]["prompt_tokens"]
        for key in totals:
            totals[key] += c["usage"][key]
    return {
        "planningTraceRows": len(trace),
        "uniqueModelCalls": len(calls),
        "matchingLedgerStartRecords": len(calls),
        "matchingLedgerResultRecords": len(calls),
        "ledgerScopeTaskIdEmpty": all(
            results[c["call_id"]]["scope"]["task_id"] == "" for c in calls
        ),
        "taskBinding": "exact call_id and metadata joined to task planningTrace; planning precedes task allocation",
        "http200": len(calls),
        "invalidDecision": sum(c["outcome"] == "invalid_decision" for c in calls),
        "validProviderUsageCalls": len(calls),
        "providerReportedTokens": totals,
        "recordedModelDurationSumMs": sum(c["duration_ms"] for c in calls),
        "callIds": [c["call_id"] for c in calls],
        "estimatedTokenTotal": None,
    }


u4, u5 = usage(t4), usage(t5)
assert u5["providerReportedTokens"] == collector["modelUsage"]["providerReportedTotals"]
assert len(results) == u4["uniqueModelCalls"] + u5["uniqueModelCalls"]
state_counts = dict(collections.Counter(t["state"] for t in tasks))
metrics4 = load("task-004-draft/metrics.json")
metric_source = args.repo / "orchestration/metrics.go"
metrics_source_hash = hashlib.sha256(metric_source.read_bytes()).hexdigest()
summary = {
    "schemaVersion": "independent.user-tasks-audit.v1",
    "capturedAtUTC": datetime.datetime.now(datetime.UTC).isoformat(),
    "scope": "offline independent recomputation of retained API/online-backup and ledger; no physical actions or new model calls",
    "task005": {
        "taskId": t5["id"],
        "state": t5["state"],
        "revision": 1,
        "apiAndDatabaseEvents": len(db_events),
        "apiSnapshotBeforeAfterEqual": True,
        "allEventsContiguousAndEqualToDatabase": True,
        "terminalSequence": terminal["sequence"],
        "successSequence": success["sequence"],
        "finishSequence": finish["sequence"],
        "closureVerified": True,
        "completedStepRuns": steps,
        "readOnlyTools": expected_tools,
        "readOnlyVerifiedCalls": 3,
        "mutatingCapabilityCalls": 0,
        "newObservationEvidence": observations5,
        "approvedToTerminalMs": round(stamp(terminal["occurredAt"]) - stamp(t5["approvedAt"]), 3),
        "approvedToFinishMs": round(stamp(finish["occurredAt"]) - stamp(t5["approvedAt"]), 3),
        "usage": u5,
        "interpretation": "status-query task succeeded; does not demonstrate route/motion/coverage success",
        "reportedMappingState": receipts[2]["payload"]["result"]["state"],
        "reportedMappingStopReason": receipts[2]["payload"]["result"]["exploration"]["stopReason"],
    },
    "task004": {
        "taskId": t4["id"],
        "state": t4["state"],
        "approved": False,
        "events": len(events4),
        "stepRuns": step4count,
        "capabilityDispatchEvents": 0,
        "draftPlanUnchanged": True,
        "previousState": "READY",
        "closureClassification": "cancelled_before_run; LOCAL_RUN_FINISHED not applicable because no local run began",
        "usage": u4,
        "interpretation": "cancel/admission test passed; not an executed robot task and not execution failure",
    },
    "metrics": {
        "retainedTask004Metrics": metrics4,
        "retainedMetricsClassification": "older metric conflated cancelled states with failedTasks; retained without rewrite",
        "backupCurrentStatePopulation": len(tasks),
        "backupCurrentStateCounts": state_counts,
        "backupEventCountsAtSnapshot": event_counts,
        "recomputedNewCurrentStateMetrics": {
            "totalTasks": len(tasks),
            "succeededTasks": state_counts.get("SUCCEEDED", 0),
            "failedTasks": state_counts.get("FAILED", 0)
            + state_counts.get("RECOVERABLE_FAILURE", 0),
            "cancelledTasks": state_counts.get("CANCELLED", 0),
            "safetyStoppedTasks": state_counts.get("SAFETY_STOPPED", 0),
            "endToEndSuccessRate": state_counts.get("SUCCEEDED", 0) / len(tasks),
        },
        "historicalFailureTransitions": [
            {
                "taskId": r["task_id"],
                "sequence": r["sequence"],
                "state": json.loads(r["payload_json"])["state"],
            }
            for r in historical_failure_rows
        ],
        "sourceSHA256": metrics_source_hash,
        "boundary": "Current cancelledTasks is a snapshot category. Task001 historical execution failure persists despite later cancellation. All-created success share mixes drafts/read-only/physical tasks and is not physical-route success rate.",
    },
    "ledger": {
        "lines": len(ledger),
        "startRecords": len(starts),
        "resultRecords": len(results),
        "uniqueRequestAttempts": len(results),
        "unfinishedAttempts": 0,
        "providerReportedTotals": {
            key: u4["providerReportedTokens"][key] + u5["providerReportedTokens"][key]
            for key in u4["providerReportedTokens"]
        },
        "allResultRecordsMatchedToTaskTraces": True,
        "scope": "This ledger snapshot covers task004 and task005 only, not earlier task001; start placeholder request_error is not a completed failure. Tokens counted on result once. Cached tokens are included in prompt tokens; output_tokens is a cap. Provider telemetry is not invoice/packet attestation.",
    },
    "inputHashes": sources,
}
save("report.json", summary)
save("source-manifest.json", sources)
save(
    "task005-event-index.json",
    [
        {key: e.get(key) for key in ("sequence", "type", "stepId", "occurredAt")}
        for e in t5["events"]
    ],
)
save(
    "task004-event-index.json",
    [{key: e.get(key) for key in ("sequence", "type", "occurredAt")} for e in t4["events"]],
)
assert (
    hashlib.sha256(ledger_path.read_bytes()).hexdigest() == sources["model-usage.jsonl"]["sha256"]
), "ledger changed during audit; preserve output and retry into another dir"
print(
    json.dumps(
        {
            "task005ClosureVerified": True,
            "task004CancelledWithoutRun": True,
            "providerTokensTask004": u4["providerReportedTokens"]["total_tokens"],
            "providerTokensTask005": u5["providerReportedTokens"]["total_tokens"],
            "currentStates": state_counts,
            "historicalFailures": len(historical_failure_rows),
            "output": str(out),
        }
    )
)
