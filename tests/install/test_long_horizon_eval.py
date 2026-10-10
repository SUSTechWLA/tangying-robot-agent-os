import copy
import hashlib
import io
import json
from datetime import UTC, datetime

import pytest

from scripts import evaluate_long_horizon as suite

POSE = [1., 2., 0., 1., 0., 0., 0.]


def event(kind, step="", **payload):
    return {"type": kind, "stepId": step, "payload": payload}


def scenario():
    calls = [{"tool": "calibration.run", "arguments": {}}, {"tool": "robot.task", "arguments": {}, "legacyIntent": {
        "action": "home_manipulation", "routeRooms": ["kitchen"], "manipulationRouteIndex": 0,
        "object": {"category": "cup"}, "destination": {"category": "storage_bin"}}}]
    plan = {"source": "llm", "capabilities": {"calls": calls}}
    draft = {"id": "goal-1", "state": "PAUSED", "approved": False, "plan": plan, "events": [], "currentRevision": 1}
    approved = {**copy.deepcopy(draft), "state": "EXECUTING", "approved": True}
    catalog = {"services": [{"name": "calibration.run", "contract": {"verification": {
        "service": "calibration.status", "required": ["revision"], "match": {"revision": "result.revision"}}}}]}
    events = [event("TASK_APPROVED"),
              event("CAPABILITY_RECEIPT", "cap1", tool="calibration.run", result={"revision": "rev1"}),
              event("CAPABILITY_VERIFIED", "cap1", tool="calibration.run", evidence={"revision": "rev1"}, operationResult={"revision": "rev1"})]
    raw = b"captured rgbd frame"
    digest = hashlib.sha256(raw).hexdigest()
    records, details = [], {}
    steps = [("navigate", "navigation.navigate", {"goalPose": POSE}),
             ("arrive", "verify_arrival", {"goalPose": POSE}),
             ("pick", "manipulation.pick", {"objectId": "mug"}),
             ("grasp", "verify_grasp", {"objectId": "mug"}),
             ("place", "manipulation.place", {"objectId": "mug", "destinationId": "tray"}),
             ("verify-place", "verify_placement", {"objectId": "mug", "destinationId": "tray"})]
    events.append(event("CAPABILITY_CALL", "cap2", tool="robot.task", commandId="goal-1/cap2"))
    active_map = {"mapId": "map", "mapRevision": "map-rev", "calibrationRevision": "rev1"}
    for number, (step, tool, arguments) in enumerate(steps):
        scoped = "cap2-"+step
        command = "goal-1/revision/1/step/"+scoped
        capture_time = f"2026-10-05T00:00:{number*2+1:02d}.500Z"
        capture_ms = int(datetime.fromisoformat(capture_time).timestamp()*1000)
        sending = event("TOOL_ACTIVITY", scoped, toolName=tool, activityStatus="SENDING", commandId=command,
                        arguments=copy.deepcopy(arguments), taskRevision=1, robotId="robot-1")
        sending["occurredAt"] = f"2026-10-05T00:00:{number*2+1:02d}Z"
        confirmation = event("TOOL_ACTIVITY", scoped, toolName=tool, activityStatus="CONFIRMED",
                             commandId=command, arguments=arguments, evidenceSource="command_observation",
                             receiptObservationId="capture-"+step, evidenceIds=["capture-"+step], taskRevision=1, robotId="robot-1")
        confirmation["occurredAt"] = f"2026-10-05T00:00:{number*2+2:02d}Z"
        events.extend([sending, confirmation])
        records.append({"id": step, "rgbSha256": digest, "depthSha256": digest})
        details[step] = {"id": step, "taskId": "goal-1", "adapter": "gazebo", "stepId": scoped,
                         "taskRevision": 1, "observedAtUnixMs": capture_ms, "robotId": "robot-1",
                         "rgbSha256": digest, "depthSha256": digest,
                         "captureId": "capture-"+step, "snapshot": {
                             "taskId": "goal-1", "taskRevision": 1, "stepId": scoped, "adapter": "gazebo", "robotId": "robot-1",
                             "observedAt": capture_time,
                             "reconstruction": {"observationId": "capture-"+step, "observedAtUnixMs": capture_ms, "robotId": "robot-1"},
                             "robotState": {
                             "base_pose": POSE,
                             "active_map": active_map,
                             "semantic_navigation": {**active_map, "frameId": "world", "goals": {"kitchen": POSE}, "aliases": {"厨房": "kitchen"}},
                             "semantic_objects": [{"id": "mug", "category": "cup"}, {"id": "tray", "category": "storage_bin"}],
                             "map_route": {"commandId": command, "goalPose": POSE,
                                           "mapId": "map", "mapRevision": "map-rev", "calibrationRevision": "rev1"},
                             "verification": {"kind": tool, "passed": True, "evidence_source": "gazebo_physics",
                                              "object_id": "mug", "destination_id": "tray",
                                              "observed_relation": "held_by:robot-1" if tool == "verify_grasp" else "inside:tray",
                                              "sample_count": 3, "stable_duration_s": .15}}}}
    events.append(event("CAPABILITY_VERIFIED", "cap2", tool="robot.task", evidence={"basis": "LEGACY_RUNNER_VERIFIED_CHILD_STEPS"}))
    final = {**copy.deepcopy(approved), "state": "SUCCEEDED", "events": events}
    return {"draft": draft, "approved": approved, "final": final, "catalog": catalog,
            "records": records, "details": details, "image": raw}


class Clock:
    def __init__(self):
        self.now = 0.

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def install_api(monkeypatch, data, states=None):
    clock, requests = Clock(), []
    states = list(states or [data["final"]])
    flags = {"cancelled": False}
    monkeypatch.setattr(suite, "resolve_token", lambda **_: "private-test-session")
    monkeypatch.setattr(suite, "install_loopback_opener", lambda: None)
    monkeypatch.setattr(suite.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(suite.time, "sleep", clock.sleep)

    def fake_urlopen(request, timeout):
        path = request.full_url.removeprefix("http://127.0.0.1:8897")
        method = request.get_method()
        requests.append((method, path))
        if path == "/v1/robot/services":
            value = data["catalog"]
        elif path == "/v1/config/status":
            value = {"hasApiKey": True, "stages": {"recovery": {"provider": "openai"}}}
        elif path == "/v1/tasks":
            assert json.loads(request.data)["adapter"] == "gazebo"
            value = data["draft"]
        elif path.endswith("/approve"):
            value = data["approved"]
        elif path.endswith("/cancel"):
            flags["cancelled"] = True
            value = {"state": "CANCELLED"}
        elif path.endswith("/pause"):
            value = {"pauseRequested": True}
        elif path.endswith("/resume"):
            flags["resumed_at"] = clock.now
            value = {"canResume": True}
        elif path.endswith("/revisions"):
            value = {"taskId": "goal-1", "revisions": [{"revision": {"taskId": "goal-1", "revision": 1}}]}
        elif path.endswith(("/rgb", "/depth")):
            return io.BytesIO(data["image"])
        elif "/observations?" in path:
            value = {"records": data["records"]}
        elif "/observations/" in path:
            value = data["details"][path.rsplit("/", 1)[-1]]
        elif path == "/v1/tasks/goal-1":
            value = states.pop(0) if len(states) > 1 else states[0]
            if flags["cancelled"]:
                value = {**value, "state": "CANCELLED"}
        else:
            raise AssertionError("unexpected endpoint: " + path)
        return io.BytesIO(json.dumps(value).encode())
    monkeypatch.setattr(suite, "urlopen", fake_urlopen)
    return requests, flags, clock


def run(tmp_path, **kwargs):
    return suite.evaluate("http://127.0.0.1:8897", "标定，巡检并搬运杯子", tmp_path / "run",
                          required_source="llm", require_manipulation=True, settle_seconds=kwargs.pop("settle_seconds", 0), **kwargs)


def test_success_saves_raw_immutable_evidence_and_checks_every_step(tmp_path, monkeypatch, capsys):
    data = scenario()
    requests, _, _ = install_api(monkeypatch, data)
    result = run(tmp_path)
    assert result["passed"] and result["verifiedChildSteps"] == 6
    assert len(result["verifiedArrivals"]) == 1
    manifest = json.loads((tmp_path / "run/manifest.json").read_text())
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((tmp_path / "run" / name).read_bytes()).hexdigest() == digest
    assert json.loads((tmp_path / "run/draft-plan.json").read_text()) == data["draft"]
    assert json.loads((tmp_path / "run/approved-plan.json").read_text()) == data["approved"]
    assert json.loads((tmp_path / "run/final-task.json").read_text()) == data["final"]
    assert "private-test-session" not in capsys.readouterr().out
    assert [(m, p) for m, p in requests if m == "POST"] == [("POST", "/v1/tasks"), ("POST", "/v1/tasks/goal-1/approve")]
    with pytest.raises(FileExistsError):
        run(tmp_path)


def test_optional_run_provenance_revisions_and_fault_log_are_retained(tmp_path, monkeypatch):
    data = scenario()
    requests, _, _ = install_api(monkeypatch, data)
    fault = tmp_path / "proxy.jsonl"
    original = b'{"event":"fault_injected","forwarded":false}\n'
    fault.write_bytes(original)
    report = run(tmp_path, fault_log=fault, collect_revisions=True, source_commit="e78e00abf")
    assert report["passed"]
    output = tmp_path / "run"
    config = json.loads((output / "run-config.json").read_text())
    assert config["declaredSourceCommit"] == "e78e00abf"
    assert config["runnerSha256"] == hashlib.sha256(suite.Path(suite.__file__).read_bytes()).hexdigest()
    assert (output / "fault-injection.jsonl").read_bytes() == original == fault.read_bytes()
    assert ("GET", "/v1/tasks/goal-1/revisions") in requests
    assert json.loads((output / "revisions.json").read_text())["taskId"] == "goal-1"


def test_missing_requested_fault_log_retains_task_and_fails(tmp_path, monkeypatch):
    install_api(monkeypatch, scenario())
    report = run(tmp_path, fault_log=tmp_path / "missing.jsonl")
    assert not report["passed"] and "FileNotFoundError" in report["faultLogError"]
    assert (tmp_path / "run/final-task.json").exists()
    assert (tmp_path / "run/manifest.json").exists()


@pytest.mark.parametrize("damage,match", [
    ("pose", "outside commanded"), ("capture", "pinned command"), ("hash", "SHA-256"),
    ("provider", "binding differs"), ("receipt", "receipt and verification"),
    ("duplicate", "duplicate capability"), ("physical_replay", "duplicate confirmations"),
    ("unstable", "stable verification"), ("destination", "placement relation"),
])
def test_false_success_is_rejected_and_originals_retained(tmp_path, monkeypatch, damage, match):
    data = scenario()
    if damage == "pose":
        data["details"]["arrive"]["snapshot"]["robotState"]["base_pose"] = [8., 2., 0., 1., 0., 0., 0.]
    elif damage == "capture":
        data["details"]["arrive"]["captureId"] = "another-capture"
    elif damage == "hash":
        data["image"] = b"corrupted"
    elif damage == "provider":
        data["final"]["events"][2]["payload"]["evidence"]["revision"] = "wrong-revision"
    elif damage == "receipt":
        data["final"]["events"].pop(1)
    elif damage == "duplicate":
        data["final"]["events"].append(copy.deepcopy(data["final"]["events"][-1]))
    elif damage == "physical_replay":
        data["final"]["events"].insert(-1, copy.deepcopy(next(e for e in data["final"]["events"]
                     if e.get("stepId") == "cap2-navigate" and e["payload"].get("activityStatus") == "CONFIRMED")))
    elif damage == "unstable":
        data["details"]["verify-place"]["snapshot"]["robotState"]["verification"]["sample_count"] = 1
    else:
        data["details"]["verify-place"]["snapshot"]["robotState"]["verification"]["destination_id"] = "wrong-tray"
    requests, _, _ = install_api(monkeypatch, data)
    result = run(tmp_path)
    assert not result["passed"] and match in result["error"]
    assert (tmp_path / "run/final-task.json").exists() and (tmp_path / "run/manifest.json").exists()
    assert not any(path.endswith("/resume") for _, path in requests)


def test_expected_arrivals_are_an_outcome_requirement(tmp_path, monkeypatch):
    install_api(monkeypatch, scenario())
    result = run(tmp_path, expected_arrivals=2)
    assert not result["passed"] and "only 1 of 2" in result["error"]


def test_wrong_source_is_rejected_before_approval(tmp_path, monkeypatch):
    data = scenario()
    data["draft"]["plan"]["source"] = "deterministic"
    requests, _, _ = install_api(monkeypatch, data)
    result = run(tmp_path)
    assert not result["passed"] and "source" in result["error"]
    assert not any(path.endswith("/approve") for _, path in requests)


def test_frozen_plan_change_is_not_accepted(tmp_path, monkeypatch):
    data = scenario()
    data["approved"]["plan"]["capabilities"]["calls"][0]["arguments"]["unexpected"] = True
    install_api(monkeypatch, data)
    result = run(tmp_path)
    assert not result["passed"] and "approval changed" in result["error"]


def test_failed_task_retained_and_never_resumed(tmp_path, monkeypatch):
    data = scenario()
    data["final"]["state"] = "RECOVERABLE_FAILURE"
    requests, _, _ = install_api(monkeypatch, data)
    result = run(tmp_path)
    assert not result["passed"] and result["state"] == "RECOVERABLE_FAILURE"
    assert json.loads((tmp_path / "run/final-task.json").read_text())["state"] == "RECOVERABLE_FAILURE"
    assert not any(path.endswith(("/resume", "/cancel")) for _, path in requests)


def test_timeout_cancels_only_its_task_without_retrying(tmp_path, monkeypatch):
    data = scenario()
    requests, flags, clock = install_api(monkeypatch, data, [data["approved"]])
    result = run(tmp_path, timeout=3)
    assert not result["passed"] and result["errorType"] == "TimeoutError"
    assert flags["cancelled"] and clock.now == 3
    assert requests.count(("POST", "/v1/tasks")) == 1
    assert requests.count(("POST", "/v1/tasks/goal-1/approve")) == 1
    assert result["state"] == "CANCELLED"


def test_collects_late_collaboration_without_claiming_read_only_repair(tmp_path, monkeypatch):
    data = scenario()
    late = copy.deepcopy(data["final"])
    late["events"].extend([
        event("ops.anomaly_detected", agent="ops", code="ANOMALY_UNVERIFIED_MUTATION"),
        event("ops.recovery_plan", agent="recovery", planId="plan-1", taskId="goal-1"),
        event("ops.recovery_executed", agent="recovery", planId="plan-1", actionId="observe.re-read", executed=True, verified=True),
    ])
    install_api(monkeypatch, data, [data["final"], data["final"], late])
    result = run(tmp_path, settle_seconds=2, require_collaboration=True, require_auto_investigation=True)
    assert result["passed"]
    assert result["collaboration"]["automaticInvestigationVerified"]
    assert not result["automaticTaskRecoveryObserved"]
    assert len(json.loads((tmp_path / "run/agent-events.json").read_text())["events"]) == 3


def test_configuration_is_not_agent_collaboration(tmp_path, monkeypatch):
    install_api(monkeypatch, scenario())
    result = run(tmp_path, require_collaboration=True)
    assert not result["passed"] and not result["collaboration"]["collaborationObserved"]


def test_non_camera_context_snapshot_does_not_invent_required_images(tmp_path, monkeypatch):
    data = scenario()
    data["records"].append({"id": "context"})
    data["details"]["context"] = {"id": "context", "stepId": "context", "captureId": "provider-state"}
    requests, _, _ = install_api(monkeypatch, data)
    assert run(tmp_path)["passed"]
    assert not any("/context/rgb" in p or "/context/depth" in p for _, p in requests)


@pytest.mark.parametrize("override", [{"executed": False}, {"verified": False}, {"operatorApproved": True},
                                      {"approvalEvidence": "session"}, {"failed": "unverified"}, {"planId": "unlinked"}])
def test_recovery_attempt_or_operator_action_is_not_automatic_investigation(override):
    payload = {"agent": "recovery", "planId": "plan-1", "executed": True, "verified": True, **override}
    task = {"id": "goal-1", "state": "SUCCEEDED", "events": [event("ops.recovery_plan", agent="recovery", planId="plan-1", taskId="goal-1"),
                                               event("ops.recovery_executed", **payload)]}
    assert not suite.collaboration_evidence(task)["automaticInvestigationVerified"]


def test_pause_resume_exceeds_sixty_seconds_and_is_operator_attributed(tmp_path, monkeypatch):
    data = scenario()
    running = copy.deepcopy(data["approved"])
    running["events"] = copy.deepcopy(data["final"]["events"][:3])
    paused = {**running, "state": "PAUSED"}
    requests, flags, _ = install_api(monkeypatch, data, [running] + [paused]*67 + [data["final"]])
    result = run(tmp_path, pause_after_tool="calibration.run", timeout=100)
    assert result["passed"] and result["pause"]["observedSeconds"] >= 65
    assert result["pause"]["initiator"] == "acceptance_operator"
    assert requests.count(("POST", "/v1/tasks/goal-1/resume")) == 1 and flags["resumed_at"] >= 65
    assert not result["automaticTaskRecoveryObserved"]


def test_cli_returns_nonzero_for_failed_evaluation(tmp_path, monkeypatch):
    monkeypatch.setattr(suite, "evaluate", lambda **_: {"passed": False})
    monkeypatch.setattr("sys.argv", ["evaluate_long_horizon.py", "--request", "巡检", "--output", str(tmp_path)])
    assert suite.main() == 1


def recovered_scenario():
    data = scenario()
    tool, step, command = "navigation.status", "cap1", "goal-1/cap1"
    for task in (data["draft"], data["approved"], data["final"]):
        task["plan"]["capabilities"]["calls"][0]["tool"] = tool
    data["catalog"]["services"][0].update(name=tool, mutatesWorld=False)
    data["catalog"]["services"][0]["contract"]["effects"] = ["READ"]
    for item in data["final"]["events"]:
        if item["payload"].get("tool") == "calibration.run":
            item["payload"]["tool"] = tool
    chain = [
        event("CAPABILITY_CALL", step, tool=tool, commandId=command),
        event("CAPABILITY_READ_RECOVERY_REQUESTED", step, tool=tool, commandId=command, maxRetries=1),
        event("CAPABILITY_FAILED", step, tool=tool, commandId=command, phase="dispatch", mutatesWorld=False,
              outcomeUnknown=False, code="PROVIDER_UNAVAILABLE"),
        event("ops.anomaly_detected", agent="ops", code="ANOMALY_ACTION_FAILED", component=tool, facts={"stepId": step}, correlationId="goal-1"),
        event("ops.recovery_plan", agent="recovery", planId="repair-1", verdict="PLAN", trigger="ANOMALY_ACTION_FAILED@"+tool,
              taskId="goal-1", trail={"steps": [{"name": "recovery.start", "findings": {"stepId": step}}]}),
        event("ops.recovery_executed", agent="recovery", planId="repair-1", actionId="execution.read-history", executed=True, verified=True),
        event("CAPABILITY_READ_RETRY", step, tool=tool, commandId=command, attempt=2,
              basis="OPS_DIAGNOSIS_AND_VERIFIED_READ_ONLY_RECOVERY"),
    ]
    data["final"]["events"][1:1] = chain
    return data


def test_strict_automatic_read_recovery_accepts_only_linked_chain_and_success(tmp_path, monkeypatch):
    data = recovered_scenario()
    install_api(monkeypatch, data)
    result = run(tmp_path, require_auto_recovery=True, require_collaboration=True, require_auto_investigation=True)
    assert result["passed"] and result["automaticTaskRecoveryObserved"]
    chains = result["collaboration"]["verifiedReadRecoveryChains"]
    assert chains[0]["commandId"] == "goal-1/cap1" and chains[0]["retryCount"] == 1
    assert chains[0]["eventIndices"] == sorted(chains[0]["eventIndices"])


@pytest.mark.parametrize("damage", ["write", "unknown", "command", "too_many_retries", "plan", "ordering", "failure", "step", "plan_step", "no_ops"])
def test_recovery_chain_rejects_unsafe_unlinked_or_incomplete_evidence(damage):
    data = recovered_scenario()
    events = data["final"]["events"]
    by_type = {e["type"]: e for e in events}
    if damage == "write":
        data["catalog"]["services"][0]["mutatesWorld"] = True
    elif damage == "unknown":
        by_type["CAPABILITY_FAILED"]["payload"]["outcomeUnknown"] = True
    elif damage == "command":
        by_type["CAPABILITY_READ_RETRY"]["payload"]["commandId"] = "different-command"
    elif damage == "too_many_retries":
        events.insert(7, copy.deepcopy(by_type["CAPABILITY_READ_RETRY"]))
    elif damage == "plan":
        by_type["ops.recovery_executed"]["payload"]["planId"] = "unrelated-plan"
    elif damage == "ordering":
        events[6], events[7] = events[7], events[6]
    elif damage == "failure":
        data["final"]["state"] = "RECOVERABLE_FAILURE"
    elif damage == "step":
        by_type["ops.anomaly_detected"]["payload"]["facts"]["stepId"] = "another-step"
    elif damage == "plan_step":
        by_type["ops.recovery_plan"]["payload"]["trail"]["steps"][0]["findings"]["stepId"] = "another-step"
    else:
        events.remove(by_type["ops.anomaly_detected"])
    assert not suite.collaboration_evidence(data["final"], data["catalog"])["automaticTaskRecoveryObserved"]


def validate_fixture(data, expected_arrivals=1):
    details = {(d["stepId"], d["captureId"]): d for d in data["details"].values()}
    return suite.validate_execution(data["final"], data["draft"]["plan"], data["catalog"], details, expected_arrivals, True)


@pytest.mark.parametrize("kind,step,payload", [
    ("CAPABILITY_FAILED", "cap1", {"tool": "navigation.status"}),
    ("TOOL_ACTIVITY", "cap2-arrive", {"toolName": "verify_arrival", "activityStatus": "FAILED"}),
    ("TOOL_ACTIVITY", "cap2-arrive", {"toolName": "verify_arrival", "activityStatus": "RUNNING"}),
])
def test_later_failure_or_unfinished_attempt_cannot_reuse_old_success(kind, step, payload):
    data = recovered_scenario()
    data["final"]["events"].append(event(kind, step, **payload))
    with pytest.raises(AssertionError, match="later failure or unfinished"):
        validate_fixture(data)
    if kind == "CAPABILITY_FAILED":
        assert not suite.collaboration_evidence(data["final"], data["catalog"])["automaticTaskRecoveryObserved"]


@pytest.mark.parametrize("damage,expected", [
    ("revision", "revision differs"), ("capture_time", "execution window"),
    ("snapshot_task", "snapshot task/step/revision"), ("command", "command does not belong"),
])
def test_observation_must_belong_to_current_command_and_revision(damage, expected):
    data = scenario()
    detail = data["details"]["arrive"]
    if damage == "revision":
        detail["taskRevision"] = 0
    elif damage == "capture_time":
        detail["observedAtUnixMs"] = 1
    elif damage == "snapshot_task":
        detail["snapshot"]["taskId"] = "another-task"
    else:
        for e in data["final"]["events"]:
            if e.get("stepId") == "cap2-arrive":
                e["payload"]["commandId"] = "other-task/revision/1/step/cap2-arrive"
    with pytest.raises(AssertionError, match=expected):
        validate_fixture(data)


def resumed_arrival(data, *, fresh):
    originals = [e for e in data["final"]["events"] if e.get("stepId") == "cap2-arrive"]
    sending, confirmed = copy.deepcopy(originals)
    command = "goal-1/revision/1/step/cap2-arrive/resume-read/attempt-2"
    sending["payload"]["commandId"] = confirmed["payload"]["commandId"] = command
    sending["occurredAt"], confirmed["occurredAt"] = "2026-10-05T00:02:00Z", "2026-10-05T00:02:02Z"
    if fresh:
        detail = copy.deepcopy(data["details"]["arrive"])
        detail.update(id="refreshed-arrive", captureId="refreshed-capture", observedAtUnixMs=1791158521000)
        detail["snapshot"]["observedAt"] = "2026-10-05T00:02:01Z"
        detail["snapshot"]["reconstruction"].update(observationId="refreshed-capture", observedAtUnixMs=1791158521000)
        data["details"]["refreshed-arrive"] = detail
        confirmed["payload"].update(receiptObservationId="refreshed-capture", evidenceIds=["refreshed-capture"])
    data["final"]["events"][-1:-1] = [sending, confirmed]


def test_resume_read_cannot_reuse_capture_from_before_new_dispatch():
    data = scenario()
    resumed_arrival(data, fresh=False)
    with pytest.raises(AssertionError, match="execution window"):
        validate_fixture(data)


def test_resume_read_accepts_fresh_capture_and_preserves_completed_physical_history():
    data = scenario()
    resumed_arrival(data, fresh=True)
    assert validate_fixture(data)["verifiedChildSteps"] == 6


def test_grasped_cup_cannot_be_replaced_by_another_placed_object():
    data = scenario()
    for e in data["final"]["events"]:
        if e.get("stepId") == "cap2-verify-place":
            e["payload"]["arguments"]["objectId"] = "blue-bottle"
    data["details"]["verify-place"]["snapshot"]["robotState"]["verification"]["object_id"] = "blue-bottle"
    with pytest.raises(AssertionError, match="changed the picked object"):
        validate_fixture(data)


@pytest.mark.parametrize("target", ["mug", "tray"])
def test_self_consistent_manipulation_still_must_match_frozen_semantic_selector(target):
    data = scenario()
    for detail in data["details"].values():
        for entity in detail["snapshot"]["robotState"]["semantic_objects"]:
            if entity["id"] == target:
                entity["category"] = "bottle"
    with pytest.raises(AssertionError, match="frozen category"):
        validate_fixture(data)


@pytest.mark.parametrize("field", ["taskId", "correlationId", "commandId"])
def test_strict_recovery_rejects_foreign_task_identity(field):
    data = recovered_scenario()
    for e in data["final"]["events"]:
        if field == "commandId" and e["payload"].get(field) == "goal-1/cap1":
            e["payload"][field] = "other-task/cap1"
        elif field != "commandId" and e["type"].startswith("ops."):
            e["payload"][field] = "other-task"
    assert not suite.collaboration_evidence(data["final"], data["catalog"])["automaticTaskRecoveryObserved"]


def test_child_steps_cannot_borrow_another_capability_outcomes():
    data = scenario()
    for e in data["final"]["events"]:
        if e.get("stepId") == "cap2-arrive":
            e["stepId"] = "cap1-arrive"
    with pytest.raises(AssertionError, match="scoped to one frozen capability"):
        validate_fixture(data)


def test_frozen_room_is_checked_against_versioned_semantic_goal():
    data = scenario()
    for task in (data["draft"], data["final"]):
        task["plan"]["capabilities"]["calls"][1]["legacyIntent"]["routeRooms"] = ["bedroom"]
    for detail in data["details"].values():
        detail["snapshot"]["robotState"]["semantic_navigation"]["goals"]["bedroom"] = [8., 2., 0., 1., 0., 0., 0.]
    with pytest.raises(AssertionError, match="frozen room order"):
        validate_fixture(data)


def test_room_alias_is_resolved_from_observed_semantic_contract():
    data = scenario()
    for task in (data["draft"], data["final"]):
        task["plan"]["capabilities"]["calls"][1]["legacyIntent"]["routeRooms"] = ["厨房"]
    assert validate_fixture(data)["verifiedCompositeOutcomes"][0]["rooms"][0]["room"] == "kitchen"


@pytest.mark.parametrize("spoof", [False, True])
def test_five_arrivals_must_follow_frozen_room_sequence_not_repeat_one_point(spoof):
    data = scenario()
    rooms = ["kitchen", "bedroom", "bathroom", "living_room", "kitchen"]
    goals = {room: [float(i), 2., 0., 1., 0., 0., 0.] for i, room in enumerate(dict.fromkeys(rooms))}
    rows = []
    for leg, room in enumerate(rooms):
        for key in ("navigate", "arrive"):
            ev = copy.deepcopy(next(e for e in data["final"]["events"] if e.get("stepId") == "cap2-"+key
                                    and e["payload"].get("activityStatus") == "CONFIRMED"))
            ev["stepId"] += "-"+str(leg)
            ev["payload"]["arguments"]["goalPose"] = goals["kitchen" if spoof else room]
            detail = copy.deepcopy(data["details"][key])
            state = detail["snapshot"]["robotState"]
            state["semantic_navigation"]["goals"] = goals
            rows.append({"index": len(rows), "event": ev, "state": state, "detail": detail})
    call = {"legacyIntent": {"action": "home_route", "routeRooms": rooms}}
    if spoof:
        with pytest.raises(AssertionError, match="frozen room order: bedroom"):
            suite.validate_composite_outcomes(call, "cap2", rows)
    else:
        result = suite.validate_composite_outcomes(call, "cap2", rows)
        assert [r["room"] for r in result["rooms"]] == rooms


@pytest.mark.parametrize("tool,age_ms,accepted", [
    ("observe_scene", 100, True), ("observe_scene", 65_000, False),
    ("verify_arrival", 100, False), ("navigation.navigate", 100, False),
])
def test_only_plain_scene_reads_can_use_bounded_fresh_predispatch_sensor_frame(tool, age_ms, accepted):
    data = scenario()
    events = [e for e in data["final"]["events"] if e.get("stepId") == "cap2-arrive"]
    for e in events:
        e["payload"]["toolName"] = tool
    detail = data["details"]["arrive"]
    captured = suite.timestamp_ms(events[0]["occurredAt"])-age_ms
    detail["observedAtUnixMs"] = captured
    detail["sourceId"] = "head-rgbd"
    snapshot = detail["snapshot"]
    snapshot["robotProfile"] = {"sensors": [{"sourceId": "head-rgbd", "maxAgeMs": 1000}]}
    snapshot["reconstruction"]["observedAtUnixMs"] = captured
    snapshot["observedAt"] = datetime.fromtimestamp(captured/1000, tz=UTC).isoformat()
    if accepted:
        suite.validate_observation_binding(data["final"], events, 1, events[1], detail)
    else:
        with pytest.raises(AssertionError, match="execution window"):
            suite.validate_observation_binding(data["final"], events, 1, events[1], detail)
