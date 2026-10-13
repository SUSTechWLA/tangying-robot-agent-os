"""The real registry/map-package boundary, with explicit synthetic sensor fixtures."""
import copy
import json
import threading
import time

import numpy as np
import pytest
from google.protobuf.json_format import MessageToDict
from tangying_robot_gateway.map_pipeline import PointCloud, build_map
from tangying_robot_gateway.semantic_cloud_services import observation_candidates
from tangying_robot_gateway.service_registry import ServiceError, ServiceRegistry

from robot.gateway.tests.test_robot_services import frame, request, workflow_fixture


def setup_map(tmp_path, *, map_id="semantic-test", anchor=None):
    grid = {"width": 60, "height": 60, "resolution": .1, "origin": [-3., -3., 0.],
            "cells": np.zeros((60, 60), dtype=np.int16)}
    points = np.array([[1., 1., .5], [1.1, 1., .6], [1., 1.1, .7],
                       [-1., -1., .5], [-1.1, -1., .6], [-1., -1.1, .7]])
    manifest = build_map(tmp_path / map_id, map_id=map_id, robot_id="unit-1",
        cloud=PointCloud(points), source="rgbd_slam", calibration_revision="a"*64,
        occupancy_grid=grid, slam_metadata={"schemaVersion": "slam.session.v1",
            "navigationEvidenceVersion": 2, "robotId": "unit-1", "mapId": map_id,
            "calibrationRevision": "a"*64, "worldFrameRevision": "stable-driver-frame",
            "mapFromWorld": anchor or [0., 0., 0.]})
    workflow, _ = workflow_fixture(tmp_path)
    workflow.activate({"mapId": map_id})
    registry = ServiceRegistry("unit-1")
    workflow.register(registry)
    return workflow, registry, manifest


def annotation(**fields):
    result = {"id": "table-1", "name": "实验台", "category": "table", "kind": "object",
        "aliases": ["测试台"], "bounds": {"min": [.9, .9, .4], "max": [1.2, 1.2, .8]},
        "source": {"kind": "operator", "reference": "synthetic unit fixture; not visual recognition"},
        "confidence": 1., "observedAtUnixMs": int(time.time()*1000)-100}
    return {**result, **fields}


def call(registry, service_name, **parameters):
    message = request(service_name, request_id=str(time.monotonic_ns()))
    message.parameters.update(parameters)
    response = registry.call(message)
    assert response.ok, f"{response.code}: {response.message}"
    return MessageToDict(response.result)


def annotate(registry, manifest, items=None):
    before = call(registry, "semantic.map")
    return call(registry, "semantic.annotate", mapRevision=manifest["hash"],
                expectedSemanticRevision=before["semanticRevision"],
                annotations=[annotation()] if items is None else items)


def test_registry_annotation_query_restarts_and_keeps_geometry_immutable(tmp_path):
    _, registry, manifest = setup_map(tmp_path)
    package = tmp_path / manifest["mapId"]
    original = {path.relative_to(package): path.read_bytes() for path in package.rglob("*") if path.is_file()}
    result = annotate(registry, manifest)
    assert result["annotationCount"] == 1
    assert result["annotations"][0]["pointCount"] == 3
    assert result["annotations"][0]["stale"] is False
    assert result["mapCreatedAtUnixMs"] == manifest["createdAtUnixMs"]
    assert result["measuredPointCount"] == manifest["pointCount"]
    assert {path.relative_to(package): path.read_bytes() for path in package.rglob("*") if path.is_file()} == original
    restored, _ = workflow_fixture(tmp_path)
    new_registry = ServiceRegistry("unit-1")
    restored.register(new_registry)
    query = call(new_registry, "semantic.query", name="测试台")
    assert query["semanticRevision"] == result["semanticRevision"]
    assert query["results"][0]["id"] == "table-1"
    assert query["results"][0]["requiresReobservation"] is True
    assert call(new_registry, "semantic.history")["revisions"]


def test_cas_mismatch_wrong_map_invalid_annotation_and_tampered_cloud_fail_closed(tmp_path):
    _, registry, manifest = setup_map(tmp_path)
    before = call(registry, "semantic.map")
    after = annotate(registry, manifest)
    for parameters, expected in [
        ({"mapRevision": manifest["hash"], "expectedSemanticRevision": before["semanticRevision"],
          "annotations": []}, "SEMANTIC_REVISION_CONFLICT"),
        ({"mapRevision": "b"*64, "expectedSemanticRevision": after["semanticRevision"],
          "annotations": []}, "MAP_REVISION_CONFLICT"),
        ({"mapRevision": manifest["hash"], "expectedSemanticRevision": after["semanticRevision"],
          "annotations": [annotation(bounds={"min": [10, 10, 10], "max": [11, 11, 11]})]},
         "INVALID_SEMANTIC_ANNOTATION"),
    ]:
        response = registry.call(request("semantic.annotate", request_id=str(time.monotonic_ns()), **parameters))
        assert not response.ok and response.code == expected
        assert MessageToDict(response.result)["outcome"] == "REJECTED"
    cloud = tmp_path / manifest["mapId"] / manifest["artifacts"]["cloud"]["href"]
    cloud.write_bytes(cloud.read_bytes() + b"tamper")
    assert not registry.call(request("semantic.query")).ok


def test_map_or_calibration_change_cannot_reuse_annotations(tmp_path):
    workflow, registry, manifest = setup_map(tmp_path)
    annotate(registry, manifest)
    _, _, other = setup_map(tmp_path, map_id="another-map")
    workflow.activate({"mapId": other["mapId"]})
    assert call(registry, "semantic.map")["annotationCount"] == 0
    workflow.calibration_get = lambda: {"revision": "b"*64}
    response = registry.call(request("semantic.query"))
    assert not response.ok and response.code == "MAP_REQUIRED"


def test_plan_checks_revision_staleness_conflicts_and_live_pose_without_motion(tmp_path):
    workflow, registry, manifest = setup_map(tmp_path)
    workflow.move = lambda *args, **kwargs: pytest.fail("semantic services must not move the robot")
    result = annotate(registry, manifest)
    args = {"mapRevision": manifest["hash"], "semanticRevision": result["semanticRevision"],
            "annotationId": "table-1"}
    planned = call(registry, "semantic.plan", **args)
    assert planned["candidates"] and planned["executionAuthorized"] is False
    assert planned["visibilityVerified"] is False
    assert planned["manipulationReachVerified"] is False
    assert planned["candidates"][0]["goalPose"][2] == pytest.approx(.035)
    workflow.capture = lambda: frame(stamp=int(time.time()*1000)-10_000)
    response = registry.call(request("semantic.plan", **args))
    assert response.code == "LOCALIZATION_REQUIRED" and not response.ok
    workflow.capture = frame
    old = annotate(registry, manifest, [annotation(observedAtUnixMs=int(time.time()*1000)-1_000_000)])
    assert call(registry, "semantic.query")["total"] == 0
    assert call(registry, "semantic.query", includeStale=True)["results"][0]["stale"]
    args["semanticRevision"] = old["semanticRevision"]
    assert registry.call(request("semantic.plan", **args)).code == "SEMANTIC_STALE"
    overlap = annotation(id="table-2", name="另一实验台", aliases=[])
    conflict = annotate(registry, manifest, [annotation(), overlap])
    args["semanticRevision"] = conflict["semanticRevision"]
    assert registry.call(request("semantic.plan", **args)).code == "SEMANTIC_AMBIGUOUS"


def test_candidate_geometry_respects_unknown_and_disconnected_space():
    grid = {"width": 70, "height": 40, "resolution": .1, "origin": [0., 0., 0.],
            "cells": np.zeros((40, 70), dtype=np.int16)}
    target = {"centroid": [5.5, 2., .7], "kind": "object"}
    grid["cells"][:, 32:35] = -1
    assert observation_candidates(grid, [1., 2., .035], target, radius=.3, max_distance=.8) == []
    grid["cells"][15:26, 32:35] = 0
    assert observation_candidates(grid, [1., 2., .035], target, radius=.3, max_distance=.8)
    grid["cells"][:] = -1
    with pytest.raises(ServiceError, match="当前位置"):
        observation_candidates(grid, [1., 2., .035], target, radius=.3)


def test_planning_contracts_only_expose_semantics_and_annotate_requires_approval(tmp_path):
    _, registry, _ = setup_map(tmp_path)
    catalogue = {item.name: item for item in registry.catalogue().services}
    for name in ("semantic.map", "semantic.query", "semantic.plan", "semantic.history"):
        item = catalogue[name]
        assert not item.mutates_world
        contract = MessageToDict(item.contract)
        assert contract["effects"] == ["READ"]
        assert contract["planningFields"]
        assert not set(contract["planningFields"]) & {"points", "cloud", "cells", "annotations"}
    writer = catalogue["semantic.annotate"]
    assert writer.mutates_world
    assert "planningFields" not in MessageToDict(writer.contract)
    assert "ARTIFACT_WRITE" in MessageToDict(writer.contract)["effects"]
    json.dumps(copy.deepcopy(MessageToDict(writer.input_schema)), allow_nan=False)


def test_annotation_computation_does_not_block_stop_lock_and_activation_race_is_rejected(tmp_path, monkeypatch):
    import tangying_robot_gateway.semantic_cloud_services as services
    workflow, registry, manifest = setup_map(tmp_path)
    before = call(registry, "semantic.map")
    entered, release = threading.Event(), threading.Event()
    compile_original = services.compile_annotations
    responses = []

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return compile_original(*args, **kwargs)

    monkeypatch.setattr(services, "compile_annotations", slow)
    worker = threading.Thread(target=lambda: responses.append(registry.call(request(
        "semantic.annotate", mapRevision=manifest["hash"],
        expectedSemanticRevision=before["semanticRevision"], annotations=[annotation()]))))
    worker.start()
    try:
        assert entered.wait(2)
        assert workflow._lock.acquire(timeout=.1), "semantic compute must not block stop/lease expiry"
        try:
            workflow.active = None
        finally:
            workflow._lock.release()
    finally:
        release.set()
        worker.join(3)
    assert responses and responses[0].code == "MAP_REVISION_CONFLICT"
    workflow.activate({"mapId": manifest["mapId"]})
    assert call(registry, "semantic.map")["semanticRevision"] == before["semanticRevision"]


def test_query_byte_budget_paginates_large_rows_and_checks_revision(tmp_path):
    _, registry, manifest = setup_map(tmp_path)
    items = [annotation(id=f"object-{i}", name=f"物体{i}", aliases=[],
                        source={"kind": "operator", "reference": "证据"*100}) for i in range(20)]
    after = annotate(registry, manifest, items)
    page = call(registry, "semantic.query", limit=50)
    assert len(json.dumps(page, ensure_ascii=True).encode()) < 7200
    assert 0 < len(page["results"]) < 20
    assert page["nextOffset"] == len(page["results"])
    following = call(registry, "semantic.query", offset=int(page["nextOffset"]),
                     semanticRevision=after["semanticRevision"])
    assert following["results"][0]["id"] != page["results"][0]["id"]
    stale = registry.call(request("semantic.query", offset=1, semanticRevision="c"*64))
    assert stale.code == "SEMANTIC_REVISION_CONFLICT"


def test_large_robot_distance_limit_and_rotated_map_frame(tmp_path):
    grid = {"width": 50, "height": 50, "resolution": .1, "origin": [0., 0., 0.],
            "cells": np.zeros((50, 50), dtype=np.int16)}
    assert observation_candidates(grid, [1., 1., .035], {"centroid": [2., 2., 1.], "kind": "object"},
                                  radius=.6, max_distance=.5) == []
    workflow, registry, manifest = setup_map(tmp_path, anchor=[.2, .3, .5])
    after = annotate(registry, manifest)
    result = call(registry, "semantic.plan", mapRevision=manifest["hash"],
                  semanticRevision=after["semanticRevision"], annotationId="table-1")
    from tangying_robot_gateway.dense_slam import compose, pose_se2
    from tangying_robot_gateway.grid_navigation import pose_is_clear
    for candidate in result["candidates"]:
        mapped = compose(workflow.map_from_world, pose_se2(candidate["goalPose"]))
        assert pose_is_clear(workflow.grid, mapped, .3)
        assert candidate["goalPose"][2] == pytest.approx(.035)


def test_activation_after_commit_returns_historical_receipt_not_rejected(tmp_path, monkeypatch):
    from tangying_robot_gateway.semantic_cloud import SemanticCloudStore
    workflow, registry, manifest = setup_map(tmp_path)
    empty = call(registry, "semantic.map")
    assert empty["coverage"]["unannotatedPointCount"] == manifest["pointCount"]
    original = SemanticCloudStore.replace

    def raced(*args, **kwargs):
        result = original(*args, **kwargs)
        with workflow._lock:
            workflow.active = None
        return result

    monkeypatch.setattr(SemanticCloudStore, "replace", raced)
    result = call(registry, "semantic.annotate", mapRevision=manifest["hash"],
                  expectedSemanticRevision=empty["semanticRevision"], annotations=[annotation()])
    assert result["committed"] is True and result["activeMapChanged"] is True
    workflow.activate({"mapId": manifest["mapId"]})
    assert call(registry, "semantic.map")["semanticRevision"] == result["semanticRevision"]
