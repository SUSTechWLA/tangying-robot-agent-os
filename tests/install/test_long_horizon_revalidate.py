import copy
import json

import pytest

from scripts import revalidate_long_horizon as audit
from tests.install.test_long_horizon_eval import install_api, recovered_scenario, run, scenario


def make_pack(tmp_path, monkeypatch):
    data = scenario()
    for i, event in enumerate(data["final"]["events"]):
        event["sequence"] = i + 1
    for record in data["records"]:
        detail = data["details"][record["id"]]
        raw = json.dumps(detail["snapshot"]).encode()
        detail.update(snapshotSha256=audit.digest(raw), snapshotBytes=len(raw))
        record.update(snapshotSha256=detail["snapshotSha256"])
    install_api(monkeypatch, data)
    assert run(tmp_path)["passed"]
    return tmp_path / "run"


def replace_evidence(directory, name, value):
    """Test adversary can recompute the manifest; semantic checks must still fail."""
    raw = json.dumps(value).encode()
    (directory / name).write_bytes(raw)
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["files"][name] = audit.digest(raw)
    (directory / "manifest.json").write_text(json.dumps(manifest))


def test_independent_revalidation_checks_raw_pack_and_preserves_originals(tmp_path, monkeypatch):
    directory = make_pack(tmp_path, monkeypatch)
    originals = {p.name: p.read_bytes() for p in directory.iterdir()}
    report = audit.revalidate(directory, tmp_path / "revalidated.json", require_manipulation=True)
    assert report["passed"], report["errors"]
    assert report["verifiedFiles"] == len(originals) - 1
    assert report["execution"]["verifiedChildSteps"] == 6
    assert report["observations"] == {"observationCount": 6, "imageCount": 12}
    assert report["context"]["managedContextCount"] == 0
    assert originals == {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(AssertionError, match="already exists"):
        audit.revalidate(directory, tmp_path / "revalidated.json")


@pytest.mark.parametrize("damage,expected", [
    ("raw", "manifest SHA"), ("image", "image SHA"), ("snapshot", "snapshot SHA"),
    ("failure", "task ended"), ("revision", "revision differs"), ("source", "wrong frozen plan source"),
    ("duplicate-create", "sole original"), ("missing-image", "image missing"),
])
def test_revalidation_rejects_corruption_even_when_report_or_manifest_claim_success(tmp_path, monkeypatch, damage, expected):
    directory = make_pack(tmp_path, monkeypatch)
    manifest = json.loads((directory / "manifest.json").read_text())
    if damage == "raw":
        (directory / "final-task.json").write_text("{}")
    elif damage in {"failure", "revision", "source"}:
        final = json.loads((directory / "final-task.json").read_text())
        if damage == "failure":
            final["state"] = "RECOVERABLE_FAILURE"
            replace_evidence(directory, "final-task.json", final)
            row = [r for r in manifest["requests"] if r["path"] == "/v1/tasks/goal-1"][-1]
            replace_evidence(directory, row["file"], final)
        elif damage == "revision":
            final["currentRevision"] = 2
            replace_evidence(directory, "final-task.json", final)
        else:
            for name in ("final-task.json", "draft-plan.json", "approved-plan.json"):
                task = json.loads((directory / name).read_text())
                task["plan"]["source"] = "deterministic"
                replace_evidence(directory, name, task)
    elif damage == "duplicate-create":
        manifest["requests"].append(next(row for row in manifest["requests"] if row["path"] == "/v1/tasks"))
        (directory / "manifest.json").write_text(json.dumps(manifest))
    elif damage == "missing-image":
        row = next(row for row in manifest["requests"] if row["path"].endswith("/rgb"))
        manifest["requests"].remove(row)
        (directory / "manifest.json").write_text(json.dumps(manifest))
    elif damage == "image":
        row = next(row for row in manifest["requests"] if row["path"].endswith("/rgb"))
        replace_evidence(directory, row["file"], "wrong pixels")
    else:
        row = next(row for row in manifest["requests"] if "/observations/" in row["path"]
                   and not row["path"].endswith(("/rgb", "/depth")))
        detail = json.loads((directory / row["file"]).read_text())
        detail["snapshot"]["robotState"]["base_pose"][0] += 1
        replace_evidence(directory, row["file"], detail)
    report = audit.revalidate(directory, tmp_path / "rejected.json", require_manipulation=True)
    assert not report["passed"]
    assert expected in report["errors"][0]["message"]
    assert json.loads((directory / "report.json").read_text())["passed"] is True
    assert (tmp_path / "rejected.json").exists()


@pytest.mark.parametrize("name", ["../outside", "/tmp/outside"])
def test_manifest_cannot_read_outside_pack(tmp_path, name):
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / "manifest.json").write_text(json.dumps({"algorithm": "sha256", "files": {name: "0" * 64}}))
    with pytest.raises(AssertionError, match="flat local"):
        audit.EvidencePack(directory)


def context_fixture(version=2):
    scope = {"task_id": "task-1", "robot_id": "robot-1", "plan_revision": 1}
    source = [{"id": "old", "kind": "tool_return", "statement": "完整中文历史"}]
    data_json = json.dumps(source, ensure_ascii=False, separators=(",", ":"))
    document = {"goal": "阅读原始记录", "scope": scope, "records": [], "constraints": ["不重复未知物理动作"]}
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    request = {"model": "fixture", "messages": [{"role": "user", "content": text}], "max_tokens": 256}
    request_json = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
    projection = {"renderer_version": f"managed-context.v{version}", "scope": scope,
                  "text": text, "sha256": audit.digest(text.encode()),
                  "compaction": {"source_sha256": "a" * 64, "input_bytes": len(text.encode()),
                                 "archived_records": 1, "archived_attempts": 0,
                                 "budget": {"max_context_bytes": 8192, "max_request_bytes": 16384, "output_tokens": 256}},
                  "artifacts": [{"scope": scope, "kind": "records", "count": 1, "data": source,
                                 "sha256": audit.digest(data_json.encode())}],
                  "model_request": request, "model_request_sha256": audit.digest(request_json.encode())}
    if version == 2:
        projection["artifacts"][0]["data_json"] = data_json
        projection["model_request_json"] = request_json
    return {"id": "task-1", "events": [{"payload": {"decision_rounds": [{"context": projection}]}}]}


def check_context(task, *, sorted_keys=False):
    raw = json.dumps(task, ensure_ascii=False, separators=(",", ":"), sort_keys=sorted_keys).encode()
    return audit.context_evidence(raw, json.loads(raw), required=True, require_model=True)


@pytest.mark.parametrize("version", [1, 2])
def test_exact_context_model_and_archive_bytes_are_verified(version):
    result = check_context(context_fixture(version))
    assert result["managedContextCount"] == result["modelRequestCount"] == result["compactedContextCount"] == 1
    assert result["contexts"][0]["sourcePreimageVerified"] is False


def test_v2_retains_original_bytes_after_payload_map_key_reencoding():
    assert check_context(context_fixture(), sorted_keys=True)["modelRequestCount"] == 1


def test_v1_reencoded_raw_objects_are_insufficient_evidence_not_rehashed():
    with pytest.raises(AssertionError, match="original byte SHA mismatch"):
        check_context(context_fixture(1), sorted_keys=True)


def test_v2_exact_large_integer_survives_float64_compatibility_display_roundtrip():
    task = context_fixture()
    row = task["events"][0]["payload"]["decision_rounds"][0]
    p = row["context"]
    artifact = p["artifacts"][0]
    artifact["data"][0]["arguments"] = {"sequence": 9007199254740993}
    artifact["data_json"] = json.dumps(artifact["data"], ensure_ascii=False, separators=(",", ":"))
    artifact["sha256"] = audit.digest(artifact["data_json"].encode())
    p["model_request"]["seed"] = 9007199254740993
    p["model_request_json"] = json.dumps(p["model_request"], ensure_ascii=False, separators=(",", ":"))
    p["model_request_sha256"] = audit.digest(p["model_request_json"].encode())
    # Go's json.Unmarshal into map[string]any decodes every JSON number as
    # float64. Strings survive SQLite/HTTP even when the object view is rounded.
    returned = json.loads(json.dumps(task), parse_int=float)
    returned_p = returned["events"][0]["payload"]["decision_rounds"][0]["context"]
    assert returned_p["artifacts"][0]["data"][0]["arguments"]["sequence"] == 9007199254740992
    assert "9007199254740993" in returned_p["artifacts"][0]["data_json"]
    result = check_context(returned, sorted_keys=True)
    assert result["displayLossy"]
    assert result["contexts"][0]["lossyArchiveDisplays"] == [artifact["sha256"]]
    assert result["contexts"][0]["modelRequestDisplayLossy"]
    assert result["modelRequestCount"] == 1
    # A successful read must use the exact string, never the rounded view.
    source = artifact["data_json"][1:-1]
    returned_row = returned["events"][0]["payload"]["decision_rounds"][0]
    returned_row.update(tool="context_read", verdict="SATISFIED",
                        arguments={"sha256": artifact["sha256"], "item_id": "old"},
                        resultDetail={"sha256": artifact["sha256"], "item_id": "old", "content": source,
                                      "offset": 0, "next_offset": len(source.encode()), "total_bytes": len(source.encode()), "complete": True})
    assert check_context(returned)["verifiedContextReadPages"] == 1
    returned_row["resultDetail"]["content"] = source.replace("9007199254740993", "9007199254740992")
    with pytest.raises(AssertionError, match="exact archive bytes"):
        check_context(returned)


@pytest.mark.parametrize("value", ["different business content", True, 9007199254740994])
def test_display_loss_tolerance_never_masks_business_value_or_unrelated_number_changes(value):
    task = context_fixture()
    artifact = task["events"][0]["payload"]["decision_rounds"][0]["context"]["artifacts"][0]
    source = json.loads(artifact["data_json"])
    source[0]["arguments"] = {"sequence": 9007199254740993}
    artifact["data_json"] = json.dumps(source)
    artifact["sha256"] = audit.digest(artifact["data_json"].encode())
    artifact["data"] = copy.deepcopy(source)
    artifact["data"][0]["arguments"]["sequence"] = value
    with pytest.raises(AssertionError, match="display"):
        check_context(task)


@pytest.mark.parametrize("damage,expected", [
    ("text", "Text SHA"), ("scope", "scope mismatch"), ("artifact", "original byte SHA"),
    ("model", "model request original"), ("request-budget", "budget/output"), ("message", "recorded context"),
])
def test_context_claims_require_exact_bound_bytes(damage, expected):
    task = context_fixture()
    p = task["events"][0]["payload"]["decision_rounds"][0]["context"]
    if damage == "text":
        p["text"] += " "
    elif damage == "scope":
        p["scope"]["task_id"] = "other-task"
    elif damage == "artifact":
        p["artifacts"][0]["data_json"] += " "
    elif damage == "model":
        p["model_request_json"] += " "
    elif damage == "request-budget":
        p["compaction"]["budget"]["max_request_bytes"] = 1
    elif damage == "message":
        request = copy.deepcopy(p["model_request"])
        request["messages"][0]["content"] = "different input"
        p["model_request"] = request
        p["model_request_json"] = json.dumps(request)
        p["model_request_sha256"] = audit.digest(p["model_request_json"].encode())
    with pytest.raises(AssertionError, match=expected):
        check_context(task)


def test_no_context_or_model_is_reported_honestly(tmp_path, monkeypatch):
    directory = make_pack(tmp_path, monkeypatch)
    report = audit.revalidate(directory, tmp_path / "no-context.json", require_managed_context=True)
    assert not report["passed"] and "no actual managed" in report["errors"][0]["message"]


def test_fault_log_must_match_actual_same_task_recovery_chain():
    ready = {"event": "proxy_ready", "adapter": "gazebo", "mutates_world": False, "service": "navigation.status"}
    fault = {"event": "fault_injected", "request_id": "task-1/cap-3", "service": "navigation.status", "forwarded": False,
             "grpc_status": "UNAVAILABLE", "injection_number": 1, "fail_count": 1, "time_utc": "2026-10-10T00:00:01Z"}
    task = {"id": "task-1", "events": [{"occurredAt": "2026-10-10T00:00:00Z"}, {}, {"occurredAt": "2026-10-10T00:00:02Z"}]}
    chain = {"commandId": "task-1/cap-3", "tool": "navigation.status", "taskId": "task-1", "planId": "p-1", "eventIndices": [0, 1, 2]}

    class Pack:
        def __init__(self):
            self.files = {"fault-injection.jsonl": "fixture"}

        def raw(self, _):
            return (json.dumps(ready) + "\n" + json.dumps(fault) + "\n").encode()

    assert audit.fault_evidence(Pack(), task, {"verifiedReadRecoveryChains": [chain]}, True)["observed"]
    for change in ({"request_id": "other-task/cap-3"}, {"forwarded": True}, {"injection_number": 2}, {"time_utc": "2026-10-10T00:00:03Z"}):
        original = fault.copy()
        fault.update(change)
        with pytest.raises(AssertionError):
            audit.fault_evidence(Pack(), task, {"verifiedReadRecoveryChains": [chain]}, True)
        fault.clear()
        fault.update(original)


def protocol_fixture():
    data = recovered_scenario()
    task = data["final"]
    task["plan"]["capabilities"]["robotId"] = "robot-1"
    binding = {"taskId": task["id"], "taskRevision": 1, "robotId": "robot-1", "stepId": "cap1",
               "commandId": "goal-1/cap1", "sourceEventId": "goal-1#source", "anomalyEventId": "evt-ops-1"}
    source = {"type": "TOOL_ACTIVITY", "stepId": "cap1", "payload": {
        **{k: v for k, v in binding.items() if k not in {"sourceEventId", "anomalyEventId"}},
        "eventId": binding["sourceEventId"], "activityStatus": "FAILED", "toolName": "navigation.status", "mutatesWorld": False}}
    index = next(i for i, e in enumerate(task["events"]) if e["type"] == "ops.anomaly_detected")
    task["events"].insert(index, source)
    selected = {}
    for event in task["events"]:
        if event["type"] not in {"ops.anomaly_detected", "ops.recovery_plan", "ops.recovery_executed"}:
            continue
        payload = event["payload"]
        event["stepId"] = "cap1"
        selected[event["type"]] = payload
        payload.update(protocolVersion="agent.event.v1", correlationId="goal-1", taskRevision=1,
                       robotId="robot-1", stepId="cap1", commandId="goal-1/cap1", binding=copy.deepcopy(binding))
    ops = selected["ops.anomaly_detected"]
    plan = selected["ops.recovery_plan"]
    executed = selected["ops.recovery_executed"]
    ops.update(eventId="evt-ops-1", causationId="goal-1#source")
    plan.update(eventId="evt-plan-1", causationId="evt-ops-1")
    executed.update(eventId="evt-executed-1", causationId="evt-plan-1", agent="recovery-executor")
    plan["binding"]["planEventId"] = executed["binding"]["planEventId"] = "evt-plan-1"
    collaboration = audit.execution.collaboration_evidence(task, data["catalog"])
    assert collaboration["automaticTaskRecoveryObserved"]
    return task, collaboration, selected


def test_v1_protocol_proves_the_exact_source_anomaly_plan_execution_chain():
    task, collaboration, _ = protocol_fixture()
    result = audit.protocol_evidence(task, collaboration, required=True)
    assert result["versionedEventCount"] == 3
    assert result["verifiedRecoveryBindings"][0]["sourceEventId"] == "goal-1#source"
    assert result["verifiedRecoveryBindings"][0]["executionEventId"] == "evt-executed-1"


@pytest.mark.parametrize("damage", ["task", "revision", "robot", "command", "source", "anomaly", "plan", "cause", "publisher", "version", "identity"])
def test_v1_protocol_rejects_cross_scope_or_forged_causation(damage):
    task, collaboration, selected = protocol_fixture()
    receipt = selected["ops.recovery_executed"]
    fields = {"task": "taskId", "revision": "taskRevision", "robot": "robotId", "command": "commandId",
              "source": "sourceEventId", "anomaly": "anomalyEventId", "plan": "planEventId"}
    if damage in fields:
        receipt["binding"][fields[damage]] = 2 if damage == "revision" else "foreign"
    elif damage == "cause":
        receipt["causationId"] = "unrelated-plan"
    elif damage == "publisher":
        receipt["agent"] = "task"
    elif damage == "version":
        receipt["protocolVersion"] = "agent.event.v999"
    elif damage == "identity":
        receipt["eventId"] = "evt-plan-1"
    with pytest.raises(AssertionError):
        audit.protocol_evidence(task, collaboration, required=True)


def test_legacy_events_are_compatible_but_do_not_prove_the_new_protocol():
    data = recovered_scenario()
    collaboration = audit.execution.collaboration_evidence(data["final"], data["catalog"])
    assert audit.protocol_evidence(data["final"], collaboration)["versionedEventCount"] == 0
    with pytest.raises(AssertionError, match="no versioned"):
        audit.protocol_evidence(data["final"], collaboration, required=True)


def test_duplicate_context_text_does_not_hide_a_later_corrupt_archive():
    task = context_fixture()
    task["events"].append(copy.deepcopy(task["events"][0]))
    task["events"][1]["payload"]["decision_rounds"][0]["context"]["artifacts"][0]["data_json"] += " "
    with pytest.raises(AssertionError, match="original byte SHA"):
        check_context(task)


def context_read_fixture():
    task = context_fixture()
    row = task["events"][0]["payload"]["decision_rounds"][0]
    artifact = row["context"]["artifacts"][0]
    source = artifact["data_json"][1:-1]
    # A complete CJK page tests byte offsets, which differ from character counts.
    size = len(source.encode())
    row.update(tool="context_read", verdict="SATISFIED", arguments={"sha256": artifact["sha256"], "item_id": "old"},
               resultDetail={"sha256": artifact["sha256"], "item_id": "old", "content": source,
                             "offset": 0, "next_offset": size, "total_bytes": size, "complete": True})
    return task, row


def test_context_read_proves_original_cjk_item_bytes_and_pagination():
    task, row = context_read_fixture()
    assert check_context(task)["verifiedContextReadPages"] == 1
    page = row["resultDetail"]
    page.update(content=page["content"][:-2], next_offset=page["next_offset"] - 2, complete=False)
    assert check_context(task)["verifiedContextReadPages"] == 1


@pytest.mark.parametrize("damage", ["content", "request", "item", "offset", "sha", "complete", "total"])
def test_context_read_success_claim_does_not_substitute_for_exact_page(damage):
    task, row = context_read_fixture()
    page = row["resultDetail"]
    if damage == "content":
        page["content"] = "fabricated history"
    elif damage == "request":
        row["arguments"]["sha256"] = "a" * 64
    elif damage == "item":
        row["arguments"]["item_id"] = page["item_id"] = "absent"
    elif damage == "offset":
        row["arguments"]["offset"] = page["offset"] = 1
    elif damage == "sha":
        row["arguments"]["sha256"] = page["sha256"] = "a" * 64
    elif damage == "complete":
        page["complete"] = False
    else:
        page["total_bytes"] += 1
    with pytest.raises(AssertionError, match="context_read"):
        check_context(task)


def test_context_read_can_finish_previous_bundle_after_ledger_grows():
    task, first = context_read_fixture()
    later = copy.deepcopy(first)
    first.pop("tool")
    later["context"]["artifacts"] = []
    task["events"][0]["payload"]["decision_rounds"].append(later)
    assert check_context(task)["verifiedContextReadPages"] == 1


def test_context_read_cannot_borrow_archive_from_a_future_round_or_other_loop():
    task, first = context_read_fixture()
    later = copy.deepcopy(first)
    later.pop("tool")
    first["context"]["artifacts"] = []
    task["events"][0]["payload"]["decision_rounds"].append(later)
    with pytest.raises(AssertionError, match="previous decision snapshots"):
        check_context(task)
    task["events"][0]["payload"]["decision_rounds"].pop()
    task["events"].insert(0, {"payload": {"decision_rounds": [later]}})
    with pytest.raises(AssertionError, match="previous decision snapshots"):
        check_context(task)


@pytest.mark.parametrize("location", ["source", "binding", "envelope"])
def test_protocol_revision_boolean_is_not_integer_one(location):
    task, collaboration, selected = protocol_fixture()
    if location == "source":
        next(e["payload"] for e in task["events"] if e["type"] == "TOOL_ACTIVITY")["taskRevision"] = True
    elif location == "binding":
        selected["ops.recovery_executed"]["binding"]["taskRevision"] = True
    else:
        selected["ops.recovery_executed"]["taskRevision"] = True
    with pytest.raises(AssertionError):
        audit.protocol_evidence(task, collaboration, required=True)


@pytest.mark.parametrize("change", [{"toolName": "manipulation.grasp"}, {"mutatesWorld": True}, {"outcomeUnknown": True}])
def test_protocol_read_recovery_cannot_claim_another_tool_or_unknown_physical_outcome(change):
    task, collaboration, _ = protocol_fixture()
    next(e["payload"] for e in task["events"] if e["type"] == "TOOL_ACTIVITY").update(change)
    with pytest.raises(AssertionError, match="exact failed command identity"):
        audit.protocol_evidence(task, collaboration, required=True)


@pytest.mark.parametrize("damage", ["before-request", "wrong-type"])
def test_protocol_source_must_be_new_tool_activity_after_this_recovery_request(damage):
    task, collaboration, _ = protocol_fixture()
    index = next(i for i, e in enumerate(task["events"]) if e["type"] == "TOOL_ACTIVITY")
    if damage == "wrong-type":
        task["events"][index]["type"] = "action.failed"
    else:
        source = task["events"].pop(index)
        request = next(i for i, e in enumerate(task["events"]) if e["type"] == "CAPABILITY_READ_RECOVERY_REQUESTED")
        task["events"].insert(request, source)
        data = recovered_scenario()
        collaboration = audit.execution.collaboration_evidence(task, data["catalog"])
        assert collaboration["automaticTaskRecoveryObserved"]
    with pytest.raises(AssertionError, match="source TOOL_ACTIVITY"):
        audit.protocol_evidence(task, collaboration, required=True)
