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
