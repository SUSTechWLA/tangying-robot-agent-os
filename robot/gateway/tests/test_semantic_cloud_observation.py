"""Measured RGB-D fixture through production reconstruction and map services."""
import copy
import hashlib
import json
import math
import threading
import time

import numpy as np
import pytest
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct
from tangying_robot_gateway.plugin_backend import project_entities
from tangying_robot_gateway.rgbd import PixelDetection, RgbdFrame, RgbdPerception
from tangying_robot_gateway.runtime import Observation
from tangying_robot_gateway.semantic_cloud_observation import (
    _canonical,
    observe_annotation,
    register_observation_service,
)
from tangying_robot_gateway.semantic_cloud_services import SemanticCloudServices
from tangying_robot_gateway.service_registry import ServiceError
from tangying_robot_proto.robot.v1 import robot_pb2

from robot.gateway.tests.test_robot_services import request
from robot.gateway.tests.test_semantic_cloud_services import annotate, annotation, setup_map


def measured_view():
    stamp = int(time.time()*1000)
    world_from_camera = np.eye(4)
    world_from_camera[:3, 3] = [1., 1., -.4]
    frame = RgbdFrame("unit-1", "unit-1/head-rgbd", "head_optical", "a"*64, stamp, 1,
        np.full((4, 4, 3), 127, dtype=np.uint8), np.ones((4, 4), dtype=np.float32),
        np.asarray([[100., 0., 1.5], [0., 100., 1.5], [0., 0., 1.]]), world_from_camera)
    detector = lambda value: [PixelDetection("measured-table", "table", np.ones((4, 4), dtype=bool), .9)]
    reconstruction = RgbdPerception(detector).reconstruct(frame)
    return Observation(reconstruction.observation_id, stamp, time.monotonic_ns(),
        robot_state={"base_pose": [0., 0., .035, 1., 0., 0., 0.], "base_pose_frame": "world",
            "perception": {"source_id": frame.source_id, "camera_frame_id": frame.frame_id,
                           "calibration_revision": frame.transform_revision}},
        entities=project_entities(reconstruction), reconstruction=reconstruction.to_wire())


def configured(tmp_path, *, old=False, anchor=None, kind="object"):
    workflow, registry, manifest = setup_map(tmp_path, anchor=anchor)
    item = annotation(kind=kind)
    if old:
        item["observedAtUnixMs"] -= 1_000_000
    document = annotate(registry, manifest, [item])
    workflow.calibration_get = lambda: {"revision": "a"*64,
        "cameras": {"head-rgbd": {"sourceId": "unit-1/head-rgbd"}}}
    workflow.entity_source = measured_view
    services = SemanticCloudServices(workflow)
    if "semantic.observe" not in registry.services:
        register_observation_service(services, registry)
    args = {"mapRevision": manifest["hash"], "semanticRevision": document["semanticRevision"],
            "annotationId": "table-1"}
    return workflow, registry, services, args


def test_production_reconstruction_confirms_object_without_rewriting_label_or_motion(tmp_path):
    workflow, registry, services, args = configured(tmp_path)
    workflow.move = lambda *args, **kwargs: pytest.fail("observation cannot move robot")
    identity = services._map({})[2]
    original = services.store.read(identity)
    result = observe_annotation(services, {**args, "requireMatch": True})
    assert result["semanticConfirmed"] is True
    assert result["geometryObserved"] is True and result["geometryPointCount"] == 16
    assert result["matches"][0]["mapPosition"] == pytest.approx([1., 1., .6])
    assert result["matchCount"] == 1 and len(result["evidenceHash"]) == 64
    assert result["targetFresh"] and not result["executionAuthorized"]
    payload = {key: value for key, value in result.items() if key != "evidenceHash"}
    assert hashlib.sha256(_canonical(payload)).hexdigest() == result["evidenceHash"]
    assert services.store.read(identity) == original
    service = next(item for item in registry.catalogue().services if item.name == "semantic.observe")
    assert not service.mutates_world
    assert MessageToDict(service.contract)["effects"] == ["READ"]
    response = registry.call(request("semantic.observe", **args))
    assert response.ok and MessageToDict(response.result)["semanticConfirmed"]


def test_old_annotation_search_is_explicit_and_does_not_refresh_history(tmp_path):
    _, _, services, args = configured(tmp_path, old=True)
    before = services.overview({})
    result = observe_annotation(services, {**args, "requireMatch": True})
    assert result["semanticConfirmed"] and not result["targetFresh"]
    after = services.overview({})
    assert before["semanticRevision"] == after["semanticRevision"]
    assert before["annotations"][0]["observedAtUnixMs"] == after["annotations"][0]["observedAtUnixMs"]


def test_protobuf_struct_integer_transport_keeps_same_capture_contract(tmp_path):
    workflow, _, services, args = configured(tmp_path)
    view = measured_view()
    wire = robot_pb2.Observation(observation_id=view.observation_id,
        wall_time_unix_ms=view.wall_time_unix_ms, monotonic_time_ns=view.monotonic_time_ns)
    wire.reconstruction.update(view.reconstruction)
    wire.robot_state.update(view.robot_state)
    for item in view.entities:
        entity = wire.entities.add(entity_id=item.entity_id, category=item.category,
            pose_xyz_quat=item.pose_xyz_quat, confidence=item.confidence, relation=item.relation)
        entity.attributes.update(item.attributes)
    workflow.entity_source = lambda: wire
    result = observe_annotation(services, {**args, "requireMatch": True})
    assert result["semanticConfirmed"]
    assert result["observedAtUnixMs"] == view.wall_time_unix_ms


def test_integral_float_evidence_hash_survives_struct_and_go_style_json(tmp_path):
    workflow, _, services, args = configured(tmp_path)
    view = measured_view()
    view.entities[0].confidence = 1.0
    view.reconstruction["entities"][0]["confidence"] = 1.0
    workflow.entity_source = lambda: view
    result = observe_annotation(services, args)
    evidence_hash = result.pop("evidenceHash")
    message = Struct()
    message.update(result)
    transported = MessageToDict(message)
    # Parsing all numeric tokens as float emulates Struct, while converting
    # integral float tokens to integer models Go encoding/json's 1 spelling.
    def go_number(text):
        number = float(text)
        return int(number) if number.is_integer() else number
    go_style = json.loads(json.dumps(transported), parse_float=go_number)
    assert go_style["matches"][0]["confidence"] == 1
    assert isinstance(go_style["matches"][0]["confidence"], int)
    assert hashlib.sha256(_canonical(go_style)).hexdigest() == evidence_hash
    assert _canonical({"z": -0.0, "b": True, "n": [1.0, .5], "s": "1.0"}) == \
        b'{"b":true,"n":[1,0.5],"s":"1.0","z":0}'


def test_geometry_alone_and_duplicate_objects_do_not_confirm(tmp_path):
    workflow, _, services, args = configured(tmp_path)
    view = measured_view()
    view.entities.clear()
    view.reconstruction["entities"] = []
    workflow.entity_source = lambda: view
    result = observe_annotation(services, args)
    assert result["geometryObserved"] and not result["semanticConfirmed"]
    assert result["reason"] == "not_observed"
    with pytest.raises(ServiceError) as error:
        observe_annotation(services, {**args, "requireMatch": True})
    assert error.value.code == "SEMANTIC_TARGET_NOT_OBSERVED"
    view = measured_view()
    duplicate = copy.deepcopy(view.entities[0])
    duplicate.entity_id = "second-table"
    view.entities.append(duplicate)
    duplicate_wire = copy.deepcopy(view.reconstruction["entities"][0])
    duplicate_wire["entityId"] = "second-table"
    view.reconstruction["entities"].append(duplicate_wire)
    workflow.entity_source = lambda: view
    result = observe_annotation(services, args)
    assert result["matchCount"] == 2 and result["reason"] == "multiple_matches"
    with pytest.raises(ServiceError) as error:
        observe_annotation(services, {**args, "requireMatch": True})
    assert error.value.code == "SEMANTIC_TARGET_AMBIGUOUS"


@pytest.mark.parametrize("field,value", [
    ("robotId", "another-robot"), ("sourceId", "unit-1/undeclared"),
    ("sourceType", "sim_ground_truth"), ("transformRevision", "b"*64),
    ("observationId", "another-frame"), ("observedAtUnixMs", 1),
    ("sourceFrameId", "another-optical-frame"), ("frameId", "map"),
    ("sequence", True), ("sequence", 1.5),
])
def test_reconstruction_identity_mismatch_is_rejected(tmp_path, field, value):
    workflow, _, services, args = configured(tmp_path)
    view = measured_view()
    view.reconstruction[field] = value
    workflow.entity_source = lambda: view
    with pytest.raises(ServiceError) as error:
        observe_annotation(services, args)
    assert error.value.code == "SEMANTIC_OBSERVATION_INVALID" and error.value.rejected


@pytest.mark.parametrize("change", ["projection", "base_frame", "base_pose", "source_metadata", "source_missing"])
def test_same_capture_metadata_and_declared_source_are_required(tmp_path, change):
    workflow, _, services, args = configured(tmp_path)
    view = measured_view()
    if change == "projection":
        view.entities[0].pose_xyz_quat[0] += .2
    elif change == "base_frame":
        view.robot_state["base_pose_frame"] = "map"
    elif change == "base_pose":
        view.robot_state.pop("base_pose")
    elif change == "source_metadata":
        view.robot_state["perception"]["source_id"] = "unknown-source"
    else:
        workflow.calibration_get = lambda: {"revision": "a"*64}
    workflow.entity_source = lambda: view
    with pytest.raises(ServiceError) as error:
        observe_annotation(services, args)
    assert error.value.code == "SEMANTIC_OBSERVATION_INVALID"


@pytest.mark.parametrize("delta", [-2100, 10000])
def test_old_and_future_observations_fail(tmp_path, delta):
    workflow, _, services, args = configured(tmp_path)
    view = measured_view()
    view.wall_time_unix_ms += delta
    view.reconstruction["observedAtUnixMs"] = view.wall_time_unix_ms
    workflow.entity_source = lambda: view
    with pytest.raises(ServiceError) as error:
        observe_annotation(services, args)
    assert error.value.code == "SEMANTIC_OBSERVATION_STALE"


def test_capture_must_follow_arrival_bound_and_stay_fresh_after_processing(tmp_path, monkeypatch):
    workflow, _, services, args = configured(tmp_path)
    view = measured_view()
    workflow.entity_source = lambda: view
    with pytest.raises(ServiceError) as error:
        observe_annotation(services, {**args, "notBeforeUnixMs": view.wall_time_unix_ms+1})
    assert error.value.code == "SEMANTIC_OBSERVATION_STALE"
    import tangying_robot_gateway.semantic_cloud_observation as module
    times = iter([view.wall_time_unix_ms, view.wall_time_unix_ms, view.wall_time_unix_ms+2001])
    monkeypatch.setattr(module, "_now", lambda: next(times))
    with pytest.raises(ServiceError) as error:
        observe_annotation(services, args)
    assert error.value.code == "SEMANTIC_OBSERVATION_STALE"


def test_missing_provider_and_region_verification_are_explicit(tmp_path):
    workflow, _, services, args = configured(tmp_path)
    workflow.entity_source = None
    with pytest.raises(ServiceError) as error:
        observe_annotation(services, {**args, "requireMatch": True})
    assert error.value.code == "SEMANTIC_PERCEPTION_UNAVAILABLE"
    _, _, regions, region_args = configured(tmp_path / "regions", kind="region")
    result = observe_annotation(regions, region_args)
    assert result["reason"] == "region_requires_localization" and not result["semanticConfirmed"]
    with pytest.raises(ServiceError) as error:
        observe_annotation(regions, {**region_args, "requireMatch": True})
    assert error.value.code == "SEMANTIC_OBJECT_REQUIRED"


def test_rotated_map_anchor_applies_to_entities_and_geometry(tmp_path):
    # This transform maps world (1, 1) back onto the annotation around (1, 1).
    _, _, services, args = configured(tmp_path, anchor=[2., 0., math.pi/2])
    result = observe_annotation(services, {**args, "requireMatch": True})
    assert result["matches"][0]["mapPosition"] == pytest.approx([1., 1., .6])
    assert result["geometryPointCount"] == 16


@pytest.mark.parametrize("change", ["semantic", "active_map", "anchor"])
def test_concurrent_identity_change_refuses_result_without_holding_stop_lock(tmp_path, change):
    workflow, registry, services, args = configured(tmp_path)
    entered, release = threading.Event(), threading.Event()
    result = []
    view = measured_view()

    def blocking_source():
        entered.set()
        assert release.wait(2)
        return view

    def run():
        try:
            result.append(observe_annotation(services, args))
        except ServiceError as error:
            result.append(error.code)

    workflow.entity_source = blocking_source
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(1)
        assert workflow._lock.acquire(timeout=.1)
        workflow._lock.release()
        if change == "semantic":
            annotate(registry, {"hash": args["mapRevision"]}, [annotation(name="修改后实验台")])
        elif change == "active_map":
            with workflow._lock:
                workflow.active = None
        else:
            with workflow._lock:
                workflow.map_from_world[0] += .2
    finally:
        release.set()
        thread.join(3)
    assert result == ["SEMANTIC_REVISION_CONFLICT" if change == "semantic" else "MAP_REVISION_CONFLICT"]
