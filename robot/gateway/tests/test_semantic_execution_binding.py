"""Synthetic motion ports exercise the production semantic execution boundary, not physics."""
import copy
import hashlib
import json
import threading
import time
from dataclasses import replace

import numpy as np
import pytest
from google.protobuf.json_format import MessageToDict
from tangying_robot_gateway.contracts import NavigationParameters, VerifyArrivalParameters
from tangying_robot_gateway.journal import RuntimeJournal
from tangying_robot_gateway.runtime import Command
from tangying_robot_gateway.semantic_cloud_services import SemanticCloudServices
from tangying_robot_gateway.service import RobotRuntimeService
from tangying_robot_gateway.service_registry import ServiceError

from robot.gateway.tests.test_gazebo_backend import captured_backend
from robot.gateway.tests.test_robot_services import request
from robot.gateway.tests.test_semantic_cloud_observation import measured_view
from robot.gateway.tests.test_semantic_cloud_services import annotate, annotation, call, setup_map


def bound_plan(tmp_path, *, old=False, mode="arrival", allow_stale=False):
    workflow, registry, manifest = setup_map(tmp_path)
    workflow.semantic_execution_binding_supported = True  # Explicit synthetic driver opt-in.
    if mode == "object":
        workflow.entity_source = measured_view
    item = annotation()
    if old:
        item["observedAtUnixMs"] -= 1_000_000
    document = annotate(registry, manifest, [item])
    args = {"mapRevision": manifest["hash"], "semanticRevision": document["semanticRevision"],
            "annotationId": "table-1", "allowStaleSearch": allow_stale, "verificationMode": mode}
    planned = call(registry, "semantic.plan", **args)
    return workflow, registry, manifest, planned


def bound_backend(tmp_path, *, mode="arrival"):
    workflow, registry, manifest, plan = bound_plan(tmp_path, mode=mode)
    backend = captured_backend()
    backend.home = True  # No home fixture loaded; the driver below is a measured-pose test port.
    backend.node.workflow = workflow
    backend.node.runtime.robot_id = workflow.robot_id
    backend.node.runtime.calibration_revision = "a"*64
    backend.node.stop_navigation = lambda: None
    motions = []

    def capture_after(started_ns, started_ms):
        for camera, sample in backend.node.runtime._samples.items():
            backend.node.runtime._samples[camera] = replace(sample,
                received_monotonic_ns=max(time.monotonic_ns(), started_ns+1),
                captured_at_unix_ms=max(int(time.time()*1000), started_ms+1))
        return not backend.cancel_event.is_set()

    def navigate(goal, command_id, cancel, deadline_s):
        motions.append((command_id, list(goal)))
        pose = np.eye(4)
        yaw = 2*np.arctan2(goal[6], goal[3])
        pose[:2, :2] = [[np.cos(yaw), -np.sin(yaw)], [np.sin(yaw), np.cos(yaw)]]
        pose[:3, 3] = goal[:3]
        for camera, sample in backend.node.runtime._samples.items():
            backend.node.runtime._samples[camera] = replace(sample, base_pose_at_capture=pose.copy(),
                received_monotonic_ns=time.monotonic_ns(), captured_at_unix_ms=int(time.time()*1000))
        return {"ok": not cancel.is_set(), "code": "CANCELLED" if cancel.is_set() else "OK"}

    backend.node.navigate = navigate
    backend._wait_post_navigation_capture = capture_after
    candidate = plan["candidates"][0]
    command = Command(schema_version="robot.v1", task_id="semantic-test", command_id="navigate-1",
        capability="navigation.navigate", parameters={"goalPose": candidate["goalPose"],
        "semanticBinding": candidate["semanticBinding"]}, deadline_unix_ms=int(time.time()*1000)+30_000,
        lease_ms=5000, idempotency_key="semantic-test/navigate-1", safety_profile="desktop_standard",
        approval_id="approved-test", robot_id=workflow.robot_id)
    return workflow, registry, manifest, plan, backend, command, motions


def test_signed_plan_is_read_only_bounded_and_provider_opt_in_is_explicit(tmp_path):
    workflow, registry, manifest = setup_map(tmp_path)
    document = annotate(registry, manifest)
    args = {"mapRevision": manifest["hash"], "semanticRevision": document["semanticRevision"], "annotationId": "table-1"}
    no_driver = call(registry, "semantic.plan", **args)
    assert not no_driver["executionBindingSupported"]
    assert all("semanticBinding" not in c for c in no_driver["candidates"])
    workflow.semantic_execution_binding_supported = True
    planned = call(registry, "semantic.plan", **args)
    assert planned["executionBindingSupported"] and planned["targetFresh"]
    assert not planned["executionAuthorized"] and planned["verificationMode"] == "arrival"
    assert len(json.dumps(planned, ensure_ascii=True).encode()) <= 7000
    assert planned["identity"]["cloudSha256"] == manifest["artifacts"]["cloud"]["sha256"]
    for candidate in planned["candidates"]:
        token = candidate["semanticBinding"]
        assert 0 < len(token) <= 2048
        payload = workflow.semantic_cloud_services.validate_binding(token, candidate["goalPose"])
        assert payload["identity"] == planned["identity"]
        assert payload["semanticRevision"] == planned["semanticRevision"]
    catalog = {item.name: item for item in registry.catalogue().services}
    for name in ("semantic.map", "semantic.query", "semantic.plan"):
        assert "identity" in MessageToDict(catalog[name].contract)["planningFields"]


@pytest.mark.parametrize("mutation,code", [
    ("signature", "SEMANTIC_BINDING_INVALID"), ("goal", "SEMANTIC_GOAL_MISMATCH"),
    ("restart", "SEMANTIC_BINDING_INVALID"), ("map", "MAP_REVISION_CONFLICT"),
    ("world", "WORLD_FRAME_REVISION_CONFLICT"), ("footprint", "ROBOT_FOOTPRINT_CHANGED"),
    ("anchor", "WORLD_FRAME_REVISION_CONFLICT"),
    ("semantic", "SEMANTIC_REVISION_CONFLICT"), ("calibration", "MAP_REQUIRED"),
])
def test_bound_navigation_rejects_tampering_and_identity_changes_before_motion(tmp_path, mutation, code):
    workflow, registry, manifest, _, backend, command, motions = bound_backend(tmp_path)
    parameters = copy.deepcopy(command.parameters)
    if mutation == "signature":
        token = parameters["semanticBinding"]
        parameters["semanticBinding"] = token[:-1] + ("0" if token[-1] != "0" else "1")
    elif mutation == "goal":
        parameters["goalPose"][0] += .01
    elif mutation == "restart":
        workflow.semantic_cloud_services = SemanticCloudServices(workflow)
    elif mutation == "map":
        workflow.active["mapRevision"] = "b"*64
    elif mutation == "world":
        workflow.world_frame_revision = "changed-world"
    elif mutation == "footprint":
        workflow.footprint_radius += .01
    elif mutation == "anchor":
        workflow.map_from_world[0] += .5
    elif mutation == "semantic":
        annotate(registry, manifest, [annotation(name="changed-name", aliases=[])])
    elif mutation == "calibration":
        workflow.calibration_get = lambda: {"revision": "c"*64}
    result = backend.execute(replace(command, parameters=parameters))
    assert not result.success and result.code == code
    assert not motions


def test_old_position_search_is_explicit_and_does_not_rewrite_semantic_observation(tmp_path, monkeypatch):
    workflow, registry, _, planned = bound_plan(tmp_path, old=True, allow_stale=True)
    services = workflow.semantic_cloud_services
    before = call(registry, "semantic.map")
    assert not planned["targetFresh"]
    first = planned["candidates"][0]
    services.validate_binding(first["semanticBinding"], first["goalPose"])
    args = {"mapRevision": planned["identity"]["mapRevision"],
        "semanticRevision": planned["semanticRevision"], "annotationId": "table-1"}
    assert registry.call(request("semantic.plan", **args)).code == "SEMANTIC_STALE"
    after = call(registry, "semantic.map")
    assert after["semanticRevision"] == before["semanticRevision"]
    assert after["annotations"][0]["observedAtUnixMs"] == before["annotations"][0]["observedAtUnixMs"]
    observed = before["annotations"][0]["observedAtUnixMs"]
    monkeypatch.setattr("tangying_robot_gateway.semantic_cloud_services._now", lambda: observed-1)
    assert registry.call(request("semantic.plan", **args, allowStaleSearch=True)).code == "SEMANTIC_STALE"


@pytest.mark.parametrize("mutation,code", [
    ("semantic", "SEMANTIC_REVISION_CONFLICT"), ("world", "WORLD_FRAME_REVISION_CONFLICT"),
    ("anchor", "WORLD_FRAME_REVISION_CONFLICT"),
    ("map", "MAP_REVISION_CONFLICT"), ("ttl", "SEMANTIC_STALE"),
])
def test_motion_guard_stops_on_revision_or_freshness_change(tmp_path, monkeypatch, mutation, code):
    workflow, registry, manifest, _, backend, command, _ = bound_backend(tmp_path)
    entered = threading.Event()
    stopped = []

    def blocking(goal, command_id, cancel, deadline_s):
        entered.set()
        assert cancel.wait(2), "motion did not stop after binding changed"
        stopped.append(command_id)
        return {"ok": False, "code": "CANCELLED"}

    backend.node.navigate = blocking
    results = []
    worker = threading.Thread(target=lambda: results.append(backend.execute(command)))
    worker.start()
    try:
        assert entered.wait(2)
        if mutation == "semantic":
            annotate(registry, manifest, [annotation(name="changed", aliases=[])])
        elif mutation == "world":
            workflow.world_frame_revision = "new-world"
        elif mutation == "anchor":
            workflow.map_from_world[0] += .5
        elif mutation == "map":
            workflow.active["mapRevision"] = "b"*64
        else:
            monkeypatch.setattr("tangying_robot_gateway.semantic_cloud_services._now",
                                lambda: int(time.time()*1000)+1_000_000)
        worker.join(3)
    finally:
        backend.cancel_event.set()
        worker.join(3)
    assert stopped and len(results) == 1
    assert not results[0].success and results[0].code == code
    assert backend.semantic_verification is None


def test_bound_navigation_and_arrival_keep_verifiable_evidence_and_same_token(tmp_path):
    _, _, _, plan, backend, command, motions = bound_backend(tmp_path)
    assert backend.execute(command).success
    token_sha = hashlib.sha256(command.parameters["semanticBinding"].encode()).hexdigest()
    receipt = backend.navigation_receipt
    assert receipt["semanticBindingSha256"] == token_sha
    assert receipt["semanticExecution"]["identity"] == plan["identity"]
    verify = replace(command, capability="verify_arrival", command_id="verify-1")
    assert backend.execute(verify).success
    assert len(motions) == 1
    evidence = backend.semantic_verification
    assert evidence["navigationCommandId"] == command.command_id
    assert evidence["commandId"] == verify.command_id
    assert evidence["arrivalVerified"] and not evidence["semanticConfirmed"]
    assert evidence["semanticBindingSha256"] == token_sha
    backend.navigation_receipt = None
    assert backend.execute(verify).code == "SEMANTIC_NAVIGATION_RECEIPT_REQUIRED"


def test_object_verification_requires_live_unique_detection_after_arrival(tmp_path):
    workflow, _, _, _, backend, command, _ = bound_backend(tmp_path, mode="object")
    workflow.calibration_get = lambda: {"revision": "a"*64,
        "cameras": {"head-rgbd": {"sourceId": "unit-1/head-rgbd"}}}
    workflow.entity_source = measured_view
    assert backend.execute(command).success
    verify = replace(command, capability="verify_arrival", command_id="verify-object")
    assert backend.execute(verify).success
    assert backend.semantic_verification["semanticConfirmed"]
    assert backend.semantic_verification["observationEvidence"]["evidenceHash"]
    receipt = backend.semantic_verification
    evidence = {key: value for key, value in receipt["observationEvidence"].items() if key != "evidenceHash"}
    # Independent implementation of the documented cross-Struct number rule.
    def canonical_numbers(value):
        if isinstance(value, dict):
            return {key: canonical_numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [canonical_numbers(item) for item in value]
        return int(value) if isinstance(value, float) and value.is_integer() else value
    raw = json.dumps(canonical_numbers(evidence), ensure_ascii=True, sort_keys=True,
                     allow_nan=False, separators=(",", ":")).encode()
    assert hashlib.sha256(raw).hexdigest() == receipt["observationEvidence"]["evidenceHash"]
    assert "evidenceHash" not in receipt
    assert receipt["commandId"] == verify.command_id

    def empty_view():
        view = measured_view()
        view.reconstruction["entities"] = []
        view.entities = []
        return view

    workflow.entity_source = empty_view
    outcome = backend.execute(replace(verify, command_id="verify-missing"))
    assert not outcome.success and outcome.code == "SEMANTIC_TARGET_NOT_OBSERVED"
    assert backend.semantic_verification is None


def test_runtime_admission_journal_and_exact_replay_wrap_bound_navigation(tmp_path):
    _, _, _, _, backend, command, motions = bound_backend(tmp_path)
    runtime = RobotRuntimeService(backend, journal=RuntimeJournal(tmp_path/"runtime.json"))
    command = replace(command, catalog_revision=runtime._catalog_revision(backend.capabilities()))
    # This test isolates admission/journaling; perception contracts have separate measured tests.
    def observation(_):
        time.sleep(.002)
        return measured_view()
    runtime._validated_observation = observation
    backend.wait_command_capture = lambda *args: True
    denied = replace(command, command_id="no-approval", idempotency_key="denied", approval_id="")
    assert runtime.invoke(denied).code == "APPROVAL_REQUIRED"
    assert not motions
    successful = runtime.invoke(command)
    assert successful.success, successful
    assert runtime.invoke(command) == successful
    assert len(motions) == 1
    assert runtime.journal.lookup(command.idempotency_key, runtime._fingerprint(command)).status == "replay"


def test_strict_tool_schema_bounds_semantic_binding():
    goal = [0., 0., .035, 1., 0., 0., 0.]
    for model in (NavigationParameters, VerifyArrivalParameters):
        assert model.model_validate({"goalPose": goal, "semanticBinding": "token"}).semantic_binding == "token"
        for invalid in ("", "x"*2049, True, {}):
            with pytest.raises(ValueError):
                model.model_validate({"goalPose": goal, "semanticBinding": invalid})


def test_changed_cloud_bytes_never_reuse_an_authentic_binding(tmp_path):
    workflow, _, manifest, plan = bound_plan(tmp_path)
    path = tmp_path/manifest["mapId"]/manifest["artifacts"]["cloud"]["href"]
    path.write_bytes(path.read_bytes()+b"tampered")
    candidate = plan["candidates"][0]
    with pytest.raises((ValueError, ServiceError)):
        workflow.semantic_cloud_services.validate_binding(candidate["semanticBinding"], candidate["goalPose"])


def test_pre_position_uses_same_binding_before_any_physical_alignment(tmp_path):
    _, registry, manifest, _, backend, command, motions = bound_backend(tmp_path)
    goal = command.parameters["goalPose"]
    yaw = 2*np.arctan2(goal[6], goal[3])
    preposition = replace(command, capability="navigation.pre_position", command_id="align",
        parameters={"alignYaw": float(yaw), "semanticBinding": command.parameters["semanticBinding"]})
    wrong = replace(preposition, parameters={**preposition.parameters, "alignYaw": float(yaw+.1)})
    assert backend.execute(wrong).code == "SEMANTIC_GOAL_MISMATCH"
    assert not motions
    assert backend.execute(preposition).success
    assert len(motions) == 1 and backend.navigation_receipt is None
    annotate(registry, manifest, [annotation(name="new-label", aliases=[])])
    assert backend.execute(preposition).code == "SEMANTIC_REVISION_CONFLICT"
    assert len(motions) == 1


def test_object_mode_is_not_promised_without_perception_or_for_regions(tmp_path):
    workflow, registry, manifest, plan = bound_plan(tmp_path)
    args = {"mapRevision": manifest["hash"], "semanticRevision": plan["semanticRevision"],
            "annotationId": "table-1", "verificationMode": "object"}
    assert registry.call(request("semantic.plan", **args)).code == "SEMANTIC_PERCEPTION_UNAVAILABLE"
    workflow.entity_source = measured_view
    document = annotate(registry, manifest, [annotation(kind="region")])
    args["semanticRevision"] = document["semanticRevision"]
    assert registry.call(request("semantic.plan", **args)).code == "SEMANTIC_VERIFICATION_UNSUPPORTED"


def test_cancel_and_post_dispatch_unknown_outcome_are_not_semantic_success(tmp_path):
    _, _, _, _, backend, command, motions = bound_backend(tmp_path)
    backend.cancel_event.set()
    assert backend.execute(command).code == "CANCELLED"
    assert not motions
    backend.cancel_event.clear()  # Simulate RuntimeServicer resetting only before new admission.
    backend._wait_post_navigation_capture = lambda *args: False
    result = backend.execute(replace(command, command_id="capture-lost"))
    assert not result.success and result.code == "POSTCONDITION_OBSERVATION_TIMEOUT"
    assert len(motions) == 1
    assert backend.navigation_receipt is None and backend.semantic_verification is None


def test_new_unbound_motion_cannot_publish_previous_semantic_verification(tmp_path):
    _, _, _, _, backend, command, _ = bound_backend(tmp_path)
    assert backend.execute(command).success
    assert backend.execute(replace(command, capability="verify_arrival", command_id="verified")).success
    assert backend.semantic_verification is not None
    legacy = replace(command, command_id="next-motion", parameters={"goalPose": command.parameters["goalPose"]})
    assert backend.execute(legacy).success
    assert backend.semantic_verification is None
