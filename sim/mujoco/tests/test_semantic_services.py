import math

import pytest
from tangying_sim.home_scene import HOME_TASK_OBJECTS, HOME_WAYPOINTS
from tangying_sim.semantic_services import build_semantic_services


def test_commissioning_contract_uses_actual_robot_and_calibration_identity():
    state = build_semantic_services(
        "home", robot_id="robot-a", calibration_revision="calibration-a"
    )
    active = state["active_map"]
    navigation = state["semantic_navigation"]
    assert navigation["schemaVersion"] == "semantic.navigation.v1"
    assert navigation["frameId"] == "world"
    assert navigation["robotId"] == "robot-a"
    assert navigation["calibrationRevision"] == "calibration-a"
    assert {key: navigation[key] for key in ("mapId", "mapRevision", "calibrationRevision")} == {
        key: active[key] for key in ("mapId", "mapRevision", "calibrationRevision")
    }
    assert navigation["goals"] == HOME_WAYPOINTS
    assert navigation["aliases"]["厨房"] == "kitchen"
    assert state["semantic_objects"] == []


def test_home_task_contract_publishes_every_object_and_destination_reference():
    state = build_semantic_services(
        "home_task", robot_id="robot-a", calibration_revision="calibration-a"
    )
    objects = {item["id"]: item for item in state["semantic_objects"]}
    assert set(objects) == {item_id for item_id, *_rest in HOME_TASK_OBJECTS} | {"kitchen-bin"}
    for item_id, _body, _joint, category, color in HOME_TASK_OBJECTS:
        assert objects[item_id] == {
            "id": item_id,
            "category": category,
            "attributes": {"color": color},
            "confidence": 1.0,
            "workArea": "kitchen",
        }
    assert objects["kitchen-bin"]["category"] == "storage_bin"
    assert objects["kitchen-bin"]["attributes"] == {"color": "blue"}
    assert objects["kitchen-bin"]["workArea"] == "kitchen"


def test_live_map_identity_requires_a_revision_bound_validated_transform():
    active = {
        "mapId": "scan-a",
        "mapRevision": "revision-a",
        "calibrationRevision": "calibration-a",
    }
    transform = {
        "validated": True,
        "fromFrame": "commissioning_world",
        "toFrame": "world",
        "mapId": "scan-a",
        "mapRevision": "revision-a",
        "calibrationRevision": "calibration-a",
        "pose": [1.0, 2.0, 0.0, 1.0, 0.0, 0.0, 0.0],
    }
    state = build_semantic_services(
        "home",
        robot_id="robot-a",
        calibration_revision="calibration-a",
        active_map=active,
        map_to_world=transform,
    )
    assert state["active_map"] == active
    assert state["semantic_navigation"]["mapId"] == "scan-a"
    goal = state["semantic_navigation"]["goals"]["kitchen"]
    assert goal[:3] == pytest.approx([HOME_WAYPOINTS["kitchen"][0] + 1.0,
                                     HOME_WAYPOINTS["kitchen"][1] + 2.0,
                                     HOME_WAYPOINTS["kitchen"][2]])


@pytest.mark.parametrize(
    "active_map,map_to_world",
    [
        ({"mapId": "scan-a", "mapRevision": "revision-a", "calibrationRevision": "calibration-a"}, None),
        ({"mapId": "scan-a", "mapRevision": "revision-a", "calibrationRevision": "old-calibration"},
         {"validated": True, "fromFrame": "commissioning_world", "toFrame": "world",
          "mapId": "scan-a", "mapRevision": "revision-a", "calibrationRevision": "calibration-a",
          "pose": [0, 0, 0, 1, 0, 0, 0]}),
        ({"mapId": "scan-a", "mapRevision": "revision-a", "calibrationRevision": "calibration-a"},
         {"validated": False, "fromFrame": "commissioning_world", "toFrame": "world",
          "mapId": "scan-a", "mapRevision": "revision-a", "calibrationRevision": "calibration-a",
          "pose": [0, 0, 0, 1, 0, 0, 0]}),
        ({"mapId": "scan-a", "mapRevision": "revision-a", "calibrationRevision": "calibration-a"},
         {"validated": True, "fromFrame": "commissioning_world", "toFrame": "world",
          "mapId": "scan-a", "mapRevision": "revision-a", "calibrationRevision": "old-calibration",
          "pose": [0, 0, 0, 1, 0, 0, 0]}),
    ],
)
@pytest.mark.parametrize("object_catalog", [None, [{"id":"driver-cup","category":"cup","workArea":"kitchen"}]])
def test_live_map_never_relabels_commissioning_coordinates_without_proof(active_map, map_to_world, object_catalog):
    assert build_semantic_services(
        "home",
        robot_id="robot-a",
        calibration_revision="calibration-a",
        active_map=active_map,
        map_to_world=map_to_world,
        object_catalog=object_catalog,
    ) == {}


def test_noncommissioned_scene_and_missing_identity_publish_no_semantic_contract():
    assert build_semantic_services("tabletop", robot_id="robot-a", calibration_revision="cal-a") == {}
    assert build_semantic_services("home", robot_id="", calibration_revision="cal-a") == {}
    assert build_semantic_services("home", robot_id="robot-a", calibration_revision="") == {}


def test_explicit_driver_catalog_replaces_defaults_and_never_publishes_positions():
    catalog = [{"id":"driver-cup","category":"cup","attributes":{"color":"","material":"ceramic"},
                "confidence":.98,"workArea":"kitchen","pose":[1,2,3,1,0,0,0]}]
    result = build_semantic_services("home_task",robot_id="robot-a",calibration_revision="cal-a",
                                     object_catalog=catalog)
    assert result["semantic_objects"] == [{"id":"driver-cup","category":"cup",
        "attributes":{"material":"ceramic"},"confidence":.98,"workArea":"kitchen"}]
    result["semantic_objects"][0]["attributes"]["material"]="modified-copy"
    assert catalog[0]["attributes"]["material"] == "ceramic"
    assert build_semantic_services("home_task",robot_id="robot-a",calibration_revision="cal-a",
                                   object_catalog=[])["semantic_objects"] == []


@pytest.mark.parametrize("invalid", [
    [{"id":"","category":"cup","workArea":"kitchen"}],
    [{"id":"cup","category":"cup","workArea":"kitchen","confidence":float("nan")}],
    [{"id":"cup","category":"cup","workArea":"kitchen"}]*2,
])
def test_invalid_injected_catalog_never_falls_back_to_legacy_objects(invalid):
    with pytest.raises(ValueError,match="catalog"):
        build_semantic_services("home_task",robot_id="robot-a",calibration_revision="cal-a",
                                object_catalog=invalid)


# ── remembered object positions (`semantic.recall.v1`) ──────────────────────
# The action catalogue deliberately carries no measured poses. The recall layer
# is the other half: positions a survey actually measured, each with the base
# pose it was measured from and the age of that sighting. These cases hold the
# rule that keeps it evidence: only the selected map's entries, only timestamps
# that are not in the future, and only entries a base could actually drive to.

def _recall_entry(**overrides):
    # Exactly the shape `map.objects.v1` publishes: the entries carry no map id,
    # the document does. A per-entry id would let a foreign position pass.
    entry = {"id": "cup-000", "category": "cup", "pose": [2.275, 3.415, .85],
             "observedFrom": [2.6, 3.0, 1.4], "sightings": 3, "confidence": .9,
             "attributes": {"color": "white"}, "lastSeenUnixMs": 1_700_000_000_000}
    entry.update(overrides)
    return entry


def _layer(entries, **overrides):
    """An object layer document shaped like the one the robot actually publishes.

    It carries no ``mapRevision`` because no real one can: the layer is written
    into the map directory and its bytes are hashed to produce that very revision.
    The helper used to include a matching ``mapRevision``, which is what let a
    check that could never pass in production stay green here — see
    ``test_a_published_object_layer_is_recalled``.
    """
    document = {"schemaVersion": "map.objects.v1", "mapId": "scan-1",
                "frameId": "map", "calibrationRevision": "c" * 64,
                "associationGateM": .12, "maxAgeMs": 86_400_000,
                "entityPolls": 10, "sightings": len(entries), "objects": entries}
    document.update(overrides)
    return document


def _active_map():
    return {"mapId": "scan-1", "mapRevision": "rev-1", "calibrationRevision": "c" * 64}


def _binding():
    """The validated map-to-world transform a driver publishes with a live map."""
    return {**_active_map(), "validated": True, "fromFrame": "commissioning_world",
            "toFrame": "world", "pose": [0., 0., 0., 1., 0., 0., 0.]}


def test_recall_publishes_the_vantage_next_to_the_object_position():
    state = build_semantic_services(
        "home_task", robot_id="robot-1", calibration_revision="c" * 64,
        active_map=_active_map(), map_to_world=_binding(),
        recalled_objects=_layer([_recall_entry()]), now_unix_ms=1_700_000_030_000)
    recall = state["semantic_recall"]
    assert recall["schemaVersion"] == "semantic.recall.v1" and recall["frameId"] == "map"
    assert recall["mapId"] == "scan-1" and recall["mapRevision"] == "rev-1"
    entry = recall["categories"]["cup"][0]
    assert entry["pose"] == [2.275, 3.415, .85], "the object's own position is kept for the record"
    assert entry["ageMs"] == 30_000
    # The drivable answer: the base pose it was seen from, as a seven-element pose.
    vantage = entry["vantagePose"]
    assert vantage[0] == pytest.approx(2.6) and vantage[1] == pytest.approx(3.0)
    assert vantage[3] == pytest.approx(math.cos(0.7)) and vantage[6] == pytest.approx(math.sin(0.7))


def test_a_published_object_layer_is_recalled():
    """The layer the robot writes must be usable, not merely well-formed.

    This is the case that was missing: every other test built its document through
    ``_layer``, which supplied a ``mapRevision`` the real writer never produces.
    The check demanding it therefore passed in every test and failed on every
    robot — grounding saw zero objects, every task failed at grounding, and the
    system reported a memory problem as "the cup is not there".
    """
    state = build_semantic_services(
        "home_task", robot_id="robot-1", calibration_revision="c" * 64,
        active_map=_active_map(), map_to_world=_binding(),
        recalled_objects=_layer([_recall_entry()]), now_unix_ms=1_700_000_030_000)
    assert "semantic_recall_error" not in state, state.get("semantic_recall_error")
    assert state["semantic_recall"]["categories"]["cup"][0]["pose"] == [2.275, 3.415, .85]


def test_a_recalled_vantage_sits_on_the_same_plane_as_the_commissioned_goals():
    """Otherwise the recall path is unusable, and the failure reads as a workspace error.

    ``observedFrom`` is a two-dimensional base pose — x, y and a heading — and
    carries no height at all. The lift into the seven-element form used to give it
    z = 0, the map frame's floor, while every commissioned goal carries the base
    height (0.035 on this robot). A recalled goal was then a different kind of pose
    wearing the same name, and the consumer's workspace check refused it with
    "goal exceeds robot workspace on navigation.z" for every household task that
    went looking for an object it could not currently see.
    """
    state = build_semantic_services(
        "home_task", robot_id="robot-1", calibration_revision="c" * 64,
        active_map=_active_map(), map_to_world=_binding(),
        recalled_objects=_layer([_recall_entry()]), now_unix_ms=1_700_000_030_000)
    goals = state["semantic_navigation"]["goals"]
    heights = {pose[2] for pose in goals.values() if len(pose) == 7}
    assert len(heights) == 1, f"the commissioned goals are not all on one plane: {heights}"
    vantage = state["semantic_recall"]["categories"]["cup"][0]["vantagePose"]
    assert vantage[2] == pytest.approx(heights.pop()), (
        "a recalled vantage and a commissioned goal must be the same kind of pose, "
        "or the workspace check accepts one and refuses the other")
    # The heading is still the observed one: only the missing coordinate was filled.
    assert vantage[0] == pytest.approx(2.6) and vantage[1] == pytest.approx(3.0)


def test_a_layer_from_another_map_is_refused_without_losing_the_observation():
    state = build_semantic_services(
        "home_task", robot_id="robot-1", calibration_revision="c" * 64,
        active_map=_active_map(), map_to_world=_binding(),
        recalled_objects=_layer([_recall_entry()], mapId="scan-other"),
        now_unix_ms=1_700_000_030_000)
    assert "semantic_recall" not in state, "a position from another map is not evidence"
    assert "does not match the active map" in state["semantic_recall_error"]
    assert state["semantic_navigation"]["mapId"] == "scan-1", "the observation itself survives"


def test_recall_drops_future_timestamps_and_keeps_the_newest_first():
    now = 1_700_000_030_000
    state = build_semantic_services(
        "home_task", robot_id="robot-1", calibration_revision="c" * 64,
        active_map=_active_map(), map_to_world=_binding(),
        recalled_objects=_layer([
            _recall_entry(id="future", lastSeenUnixMs=now + 60_000),
            _recall_entry(id="old", lastSeenUnixMs=now - 60_000),
            _recall_entry(id="new", lastSeenUnixMs=now - 1_000),
        ]), now_unix_ms=now)
    entries = state["semantic_recall"]["categories"]["cup"]
    assert [item["id"] for item in entries] == ["new", "old"]
    assert [item["ageMs"] for item in entries] == [1_000, 60_000]


def test_recall_is_absent_rather_than_empty_when_nothing_was_ever_seen():
    state = build_semantic_services(
        "home_task", robot_id="robot-1", calibration_revision="c" * 64,
        active_map=_active_map(), map_to_world={"pose": [0., 0., 0., 1., 0., 0., 0.]})
    assert "semantic_recall" not in state, "never-seen and seen-here must stay distinguishable"
    # A sighting without a recorded vantage is still published, but carries no
    # drivable pose: the caller decides whether the room is worth searching.
    state = build_semantic_services(
        "home_task", robot_id="robot-1", calibration_revision="c" * 64,
        active_map=_active_map(), map_to_world=_binding(),
        recalled_objects=_layer([_recall_entry(observedFrom=None)]), now_unix_ms=1_700_000_030_000)
    assert state["semantic_recall"]["categories"]["cup"][0]["vantagePose"] is None


def test_recall_rejects_malformed_entries_without_failing_the_observation():
    now = 1_700_000_030_000
    state = build_semantic_services(
        "home_task", robot_id="robot-1", calibration_revision="c" * 64,
        active_map=_active_map(), map_to_world=_binding(),
        recalled_objects=_layer([
            _recall_entry(id="bad-pose", pose=[1.0, 2.0]),
            _recall_entry(id="bad-time", lastSeenUnixMs=0),
            _recall_entry(id="no-category", category=""),
            "not-an-entry",
            _recall_entry(id="good"),
        ]), now_unix_ms=now)
    entries = state["semantic_recall"]["categories"]["cup"]
    assert [item["id"] for item in entries] == ["good"], "bad entries are skipped, not fatal"
