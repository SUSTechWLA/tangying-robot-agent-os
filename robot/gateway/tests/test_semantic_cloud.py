"""Measured selections, cloud identity, concurrency, and stale evidence boundaries."""
import copy
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import numpy as np
import pytest
from tangying_robot_gateway.semantic_cloud import (
    SemanticCloudStore,
    compile_annotations,
    query_document,
)
from tangying_robot_gateway.service_registry import ServiceError

NOW = 1_900_000_000_000
POINTS = np.array([[0., 0., 0.], [.2, .2, .5], [1., 1., 1.], [3., 3., 2.]], dtype=np.float32)


def identity(**changes):
    return {"robotId": "robot-1", "mapId": "scan-a", "mapRevision": "a" * 64,
            "calibrationRevision": "b" * 64, "cloudSha256": "c" * 64, "frameId": "map", **changes}


def annotation(identifier="cup-1", *, kind="object", low=None, high=None, **changes):
    return {"id": identifier, "name": identifier, "category": "cup" if kind == "object" else "room",
            "kind": kind, "aliases": [], "bounds": {"min": low or [-.1, -.1, -.1],
                                                       "max": high or [.5, .5, .6]},
            "source": {"kind": "operator", "reference": "annotation-session-1"},
            "confidence": .8, "observedAtUnixMs": NOW, **changes}


def compile_layer(annotations=None):
    return compile_annotations(POINTS, annotations if annotations is not None else [annotation()], now_unix_ms=NOW)


def document(tmp_path, annotations=None):
    store = SemanticCloudStore(tmp_path / "semantics.sqlite")
    return store.replace(identity(), store.read(identity())["semanticRevision"],
                         compile_layer(annotations), now_unix_ms=NOW)


def test_measured_selection_is_reconstructable_without_becoming_an_action_target():
    original = annotation()
    layer = compile_layer([original])
    item = layer["annotations"][0]
    assert item["pointCount"] == 2
    assert item["centroid"] == pytest.approx([.1, .1, .25])
    assert item["measuredBounds"]["min"] == [0., 0., 0.]
    assert item["pointIndicesSha256"] == hashlib.sha256(np.array([0, 1], dtype="<u8").tobytes()).hexdigest()
    assert item["source"] == original["source"]
    assert item["requiresReobservation"] is True and item["ambiguous"] is False
    assert not {"goalPose", "graspPose", "recognizedClass", "pointIndices"} & item.keys()
    assert "pointCount" not in original
    assert layer["coverage"]["fraction"] == .5


def test_region_containment_and_same_kind_overlap_are_distinct_and_coverage_is_union():
    region = annotation("kitchen", kind="region", high=[2., 2., 2.])
    first, second = annotation(), annotation("cup-2", low=[.1, .1, .1], high=[1.1, 1.1, 1.1])
    layer = compile_layer([region, first, second])
    assert layer["coverage"]["annotatedPointCount"] == 3
    assert len(layer["relations"]) == 2
    assert {entry["subjectId"] for entry in layer["relations"]} == {"cup-1", "cup-2"}
    assert {entry["objectId"] for entry in layer["relations"]} == {"kitchen"}
    assert layer["conflicts"] == [{"kind": "same_kind_point_overlap", "annotationIds": ["cup-1", "cup-2"],
                                     "sharedPointCount": 1, "resolution": "requires_review"}]
    assert [entry["ambiguous"] for entry in layer["annotations"]] == [False, True, True]


def test_partial_region_overlap_is_not_full_containment():
    layer = compile_layer([annotation(), annotation("room", kind="region", low=[.1, .1, .1], high=[2., 2., 2.])])
    assert layer["relations"] == []
    assert layer["conflicts"] == []


@pytest.mark.parametrize("change", [
    {"observedAtUnixMs": True}, {"observedAtUnixMs": NOW + 1}, {"observedAtUnixMs": -1},
    {"observedAtUnixMs": 10**400},
    {"confidence": True}, {"confidence": float("nan")}, {"confidence": 1.1},
    {"confidence": 10**400},
    {"kind": "grasp"}, {"source": {"kind": "geometry_guessed", "reference": "x"}},
    {"source": {"kind": "operator", "reference": ""}}, {"name": ""}, {"name": "\nhello"},
    {"bounds": {"min": [0., 0., 0.], "max": [0., 1., 1.]}},
    {"bounds": {"min": [False, 0., 0.], "max": [1., 1., 1.]}},
    {"bounds": {"min": [float("inf"), 0., 0.], "max": [1., 1., 1.]}},
    {"navigationPose": [0., 0., 0.]}, {"aliases": ["x"] * 17},
])
def test_rejects_unsupported_annotations_without_silent_coercion(change):
    with pytest.raises(ServiceError) as raised:
        compile_layer([annotation(**change)])
    assert raised.value.rejected


@pytest.mark.parametrize("points", [np.empty((0, 3)), np.ones((2, 4)), np.ones((2, 3), dtype=bool),
                                    np.array([[np.nan, 0., 0.]]), np.zeros((2_000_001, 3), dtype=np.float32)])
def test_rejects_empty_invalid_or_unbounded_clouds(points):
    with pytest.raises(ServiceError):
        compile_annotations(points, [], now_unix_ms=NOW)


def test_rejects_empty_selection_and_duplicate_identifiers_or_normalized_labels():
    bad = [
        [annotation(low=[9., 9., 9.], high=[10., 10., 10.])],
        [annotation(), annotation()],
        [annotation(name="厨房"), annotation("other", aliases=["厨房"])],
        [annotation(name=" CUP "), annotation("other", name="cup")],
        [annotation(name="cup", aliases=["CUP"])],
        [annotation(str(index)) for index in range(129)],
    ]
    for annotations in bad:
        with pytest.raises(ServiceError):
            compile_layer(annotations)


def test_inclusive_bounds_and_order_stable_index_digest():
    item = compile_layer([annotation(low=[0., 0., 0.], high=[1., 1., 1.])])["annotations"][0]
    assert item["pointCount"] == 3
    assert item["pointIndicesSha256"] == hashlib.sha256(np.arange(3, dtype="<u8").tobytes()).hexdigest()
    assert compile_layer([])["coverage"]["unannotatedPointCount"] == len(POINTS)


def test_persists_restart_and_separates_geometric_map_revisions(tmp_path):
    path = tmp_path / "semantics.sqlite"
    store = SemanticCloudStore(path)
    empty = store.read(identity())
    assert empty == SemanticCloudStore(path).read(identity())
    layer = compile_layer()
    current = store.replace(identity(), empty["semanticRevision"], layer, now_unix_ms=NOW)
    assert current == SemanticCloudStore(path).read(identity())
    assert current["parentRevision"] == empty["semanticRevision"]
    assert current["identity"]["mapRevision"] == "a" * 64
    assert current["semanticRevision"] != empty["semanticRevision"]
    for changed in (identity(mapRevision="d" * 64), identity(cloudSha256="e" * 64),
                    identity(robotId="other"), identity(calibrationRevision="f" * 64)):
        other = store.read(changed)
        assert other["annotations"] == []
        assert other["semanticRevision"] != empty["semanticRevision"]
    layer["annotations"][0]["name"] = "mutated caller"
    current["annotations"].clear()
    assert store.read(identity())["annotations"][0]["name"] == "cup-1"


def test_compare_and_swap_serializes_independent_concurrent_writers(tmp_path):
    path = tmp_path / "semantics.sqlite"
    first, second = SemanticCloudStore(path), SemanticCloudStore(path)
    expected = first.read(identity())["semanticRevision"]
    barrier = Barrier(2)

    def write(store, name):
        barrier.wait()
        try:
            return store.replace(identity(), expected, compile_layer([annotation(name)]), now_unix_ms=NOW)
        except ServiceError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda args: write(*args), [(first, "first"), (second, "second")]))
    assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
    error = next(outcome for outcome in outcomes if isinstance(outcome, ServiceError))
    assert error.code == "SEMANTIC_REVISION_CONFLICT" and error.rejected
    assert len(first.history(identity())) == 1


def test_history_chain_and_retirement_are_preserved(tmp_path):
    store = SemanticCloudStore(tmp_path / "semantics.sqlite")
    first = store.replace(identity(), store.read(identity())["semanticRevision"], compile_layer(), now_unix_ms=NOW)
    second = store.replace(identity(), first["semanticRevision"], compile_layer([]), now_unix_ms=NOW + 1)
    history = store.history(identity(), limit=1)
    assert history == [{"semanticRevision": second["semanticRevision"], "parentRevision": first["semanticRevision"],
                        "updatedAtUnixMs": NOW + 1, "annotationCount": 0, "conflictCount": 0}]
    assert len(store.history(identity())) == 2
    assert store.read(identity())["annotations"] == []
    with pytest.raises(ServiceError):
        store.replace(identity(), second["semanticRevision"], compile_layer(), now_unix_ms=NOW)
    assert len(store.history(identity())) == 2


@pytest.mark.parametrize("column,value", [("document", '{"tampered":true}'), ("revision", "f" * 64), ("sequence", 9)])
def test_rejects_persisted_tampering_on_read_history_and_replace(tmp_path, column, value):
    path = tmp_path / "semantics.sqlite"
    store = SemanticCloudStore(path)
    current = document(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(f"UPDATE semantic_cloud_revisions SET {column}=?", (value,))
    for operation in (lambda: store.read(identity()), lambda: store.history(identity()),
                      lambda: store.replace(identity(), current["semanticRevision"], compile_layer(), now_unix_ms=NOW)):
        with pytest.raises(ServiceError) as raised:
            operation()
        assert raised.value.code == "SEMANTIC_INTEGRITY_ERROR"


def test_ancestor_tampering_is_detected_even_if_current_revision_is_intact(tmp_path):
    path = tmp_path / "semantics.sqlite"
    store = SemanticCloudStore(path)
    first = document(tmp_path)
    store.replace(identity(), first["semanticRevision"], compile_layer([]), now_unix_ms=NOW + 1)
    with sqlite3.connect(path) as connection:
        raw = connection.execute("SELECT document FROM semantic_cloud_revisions WHERE sequence=1").fetchone()[0]
        edited = json.loads(raw)
        edited["annotations"][0]["name"] = "forged"
        connection.execute("UPDATE semantic_cloud_revisions SET document=? WHERE sequence=1", (json.dumps(edited),))
    with pytest.raises(ServiceError, match="hash mismatch"):
        store.read(identity())


def test_query_freshness_region_membership_provenance_and_backwards_clock(tmp_path):
    doc = document(tmp_path, [annotation(aliases=["水杯"]),
                             annotation("kitchen", kind="region", high=[2., 2., 2.])])
    result = query_document(doc, {"name": "水杯", "regionId": "kitchen"}, now_unix_ms=NOW + 1)
    assert result["total"] == 1
    item = result["results"][0]
    assert item["ageMs"] == 1 and item["stale"] is False and item["requiresReobservation"] is True
    assert item["source"]["kind"] == "operator"
    assert query_document(doc, {}, now_unix_ms=NOW + 900_001)["total"] == 1  # persistent region metadata
    assert query_document(doc, {"kind": "region", "maxAgeMs": 100}, now_unix_ms=NOW + 101)["total"] == 0
    expired = query_document(doc, {"name": "cup-1", "includeStale": True}, now_unix_ms=NOW + 900_001)
    assert expired["results"][0]["freshness"] == "expired"
    assert query_document(doc, {}, now_unix_ms=NOW - 1)["total"] == 0
    backwards = query_document(doc, {"includeStale": True}, now_unix_ms=NOW - 1)
    assert all(item["freshness"] == "clock_disagreement" and item["stale"] for item in backwards["results"])
    assert not any("ageMs" in item for item in doc["annotations"])


def test_pagination_requires_exact_semantic_revision(tmp_path):
    doc = document(tmp_path, [annotation(), annotation("other")])
    first = query_document(doc, {"limit": 1}, now_unix_ms=NOW)
    assert first["nextOffset"] == 1
    second = query_document(doc, {"limit": 1, "offset": 1, "semanticRevision": first["semanticRevision"]}, now_unix_ms=NOW)
    assert second["results"][0]["id"] == "other" and second["nextOffset"] is None
    for filters in ({"offset": 1}, {"semanticRevision": "a" * 64}, {"limit": 51}, {"offset": True},
                    {"includeStale": 1}, {"maxAgeMs": -1}, {"unknown": "field"}):
        with pytest.raises(ServiceError):
            query_document(doc, filters, now_unix_ms=NOW)


def test_store_and_query_reject_unknown_identity_fields_or_unhashed_changes(tmp_path):
    store = SemanticCloudStore(tmp_path / "semantics.sqlite")
    for value in (identity(frameId="world"), identity(mapId="../escape"), identity(cloudSha256="unknown"),
                  identity(extra="x"), identity(mapRevision=True)):
        with pytest.raises(ServiceError):
            store.read(value)
    doc = document(tmp_path)
    modified = copy.deepcopy(doc)
    modified["annotations"][0]["bounds"]["min"][0] = -100.
    with pytest.raises(ServiceError, match="hash mismatch"):
        query_document(modified, {}, now_unix_ms=NOW)
    assert query_document(store.read(identity(mapId="empty")), {}, now_unix_ms=NOW)["results"] == []


def test_revision_capacity_rejects_without_deleting_history(tmp_path, monkeypatch):
    from tangying_robot_gateway import semantic_cloud

    monkeypatch.setattr(semantic_cloud, "MAX_REVISIONS", 2)
    store = SemanticCloudStore(tmp_path / "semantics.sqlite")
    first = store.replace(identity(), store.read(identity())["semanticRevision"], compile_layer(), now_unix_ms=NOW)
    second = store.replace(identity(), first["semanticRevision"], compile_layer([]), now_unix_ms=NOW + 1)
    with pytest.raises(ServiceError) as raised:
        store.replace(identity(), second["semanticRevision"], compile_layer(), now_unix_ms=NOW + 2)
    assert raised.value.code == "SEMANTIC_HISTORY_CAPACITY" and raised.value.rejected
    assert "archival" in str(raised.value)
    assert store.read(identity()) == second and len(store.history(identity())) == 2


def test_cumulative_byte_budget_rejects_write_and_oversized_read_before_decode(tmp_path, monkeypatch):
    from tangying_robot_gateway import semantic_cloud

    path = tmp_path / "semantics.sqlite"
    store = SemanticCloudStore(path)
    first = store.replace(identity(), store.read(identity())["semanticRevision"], compile_layer(), now_unix_ms=NOW)
    with sqlite3.connect(path) as connection:
        size = connection.execute("SELECT length(CAST(document AS BLOB)) FROM semantic_cloud_revisions").fetchone()[0]
    monkeypatch.setattr(semantic_cloud, "MAX_HISTORY_BYTES", size + 1)
    with pytest.raises(ServiceError) as raised:
        store.replace(identity(), first["semanticRevision"], compile_layer([]), now_unix_ms=NOW + 1)
    assert raised.value.code == "SEMANTIC_HISTORY_CAPACITY"
    assert store.read(identity()) == first
    monkeypatch.setattr(semantic_cloud, "MAX_HISTORY_BYTES", size - 1)
    monkeypatch.setattr(semantic_cloud.json, "loads", lambda _: pytest.fail("must reject before decoding bytes"))
    with pytest.raises(ServiceError) as raised:
        store.read(identity())
    assert raised.value.code == "SEMANTIC_HISTORY_CAPACITY"


def test_excess_revision_rows_are_rejected_before_document_transfer(tmp_path, monkeypatch):
    from tangying_robot_gateway import semantic_cloud

    path = tmp_path / "semantics.sqlite"
    store = SemanticCloudStore(path)
    first = store.replace(identity(), store.read(identity())["semanticRevision"], compile_layer(), now_unix_ms=NOW)
    store.replace(identity(), first["semanticRevision"], compile_layer([]), now_unix_ms=NOW + 1)
    monkeypatch.setattr(semantic_cloud, "MAX_REVISIONS", 1)
    monkeypatch.setattr(semantic_cloud.json, "loads", lambda _: pytest.fail("must reject before decoding bytes"))
    with pytest.raises(ServiceError) as raised:
        store.history(identity())
    assert raised.value.code == "SEMANTIC_HISTORY_CAPACITY"


def test_all_connections_close_after_success_and_rejection(tmp_path, monkeypatch):
    from tangying_robot_gateway import semantic_cloud

    original_connect = sqlite3.connect
    opened = []

    class TrackedConnection(sqlite3.Connection):
        was_closed = False

        def close(self):
            self.was_closed = True
            return super().close()

    def tracked_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs, factory=TrackedConnection)
        opened.append(connection)
        return connection

    monkeypatch.setattr(semantic_cloud.sqlite3, "connect", tracked_connect)
    store = SemanticCloudStore(tmp_path / "semantics.sqlite")
    empty = store.read(identity())
    store.replace(identity(), empty["semanticRevision"], compile_layer(), now_unix_ms=NOW)
    store.history(identity())
    with pytest.raises(ServiceError):
        store.replace(identity(), empty["semanticRevision"], compile_layer(), now_unix_ms=NOW)
    assert len(opened) == 5
    assert all(connection.was_closed for connection in opened)
