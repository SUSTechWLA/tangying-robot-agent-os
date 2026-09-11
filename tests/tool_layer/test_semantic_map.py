"""Semantic locations must resolve server-side, so they are tested like an API.

The important properties: an unknown name fails loudly instead of resolving to
a nearby guess, aliases are unambiguous, and the shipped layout cannot silently
drift away from the scene the navigation stack was commissioned against.
"""

from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path

import pytest
from tangying_robot_gateway.semantic_map import (
    DEFAULT_LAYOUT_PATH,
    LocationError,
    SemanticLocation,
    SemanticMap,
    normalize_location_name,
)

REPO = Path(__file__).resolve().parents[2]
HOME_SCENE = REPO / "sim/mujoco/tangying_sim/home_scene.py"


@pytest.fixture
def home_map() -> SemanticMap:
    return SemanticMap.from_file()


def test_shipped_layout_loads_and_covers_the_commissioned_rooms(home_map):
    assert home_map.layout_id == "home-four-room-v1"
    assert set(home_map.names()) == {
        "living_room", "home_corridor", "kitchen", "bedroom", "bathroom",
    }
    assert home_map.rooms() == ("bathroom", "bedroom", "home_corridor", "kitchen", "living_room")


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("厨房", "kitchen"),
        ("kitchen", "kitchen"),
        ("The Kitchen", "kitchen"),
        ("  the   kitchen  ", "kitchen"),
        ("客厅", "living_room"),
        ("Lounge", "living_room"),
        ("起点", "living_room"),
        ("卫生间", "bathroom"),
        ("wc", "bathroom"),
        ("走廊", "home_corridor"),
        ("hallway", "home_corridor"),
    ],
)
def test_spoken_names_and_aliases_resolve(home_map, spoken, expected):
    location = home_map.resolve(spoken)
    assert location is not None and location.name == expected


def test_an_unknown_place_does_not_resolve_to_a_nearby_guess(home_map):
    for unknown in ("garage", "车库", "kitchen sink", "", "   "):
        assert home_map.resolve(unknown) is None


def test_pose7_conversion_matches_the_runtime_pose_contract(home_map):
    kitchen = home_map.resolve("kitchen")
    pose = kitchen.to_pose7()
    assert len(pose) == 7
    # x, y, z then wxyz; the yaw of the commissioned waypoint is identity here.
    assert pose[:3] == [2.2, 3.35, 0.0]
    assert pose[3:] == [1.0, 0.0, 0.0, 0.0]
    assert math.isclose(sum(value * value for value in pose[3:]), 1.0, abs_tol=1e-9)


def test_yaw_is_encoded_as_a_quaternion():
    turned = SemanticLocation(name="facing_back", pose=(1.0, 2.0, math.pi), room="test")
    pose = turned.to_pose7()
    assert pose[3] == pytest.approx(0.0, abs=1e-9)
    assert pose[6] == pytest.approx(1.0, abs=1e-9)


def test_duplicate_names_and_ambiguous_aliases_are_rejected():
    base = SemanticLocation(name="kitchen", pose=(0.0, 0.0, 0.0), room="kitchen", aliases=("厨房",))
    with pytest.raises(LocationError, match="duplicate location"):
        SemanticMap([base, SemanticLocation(name="kitchen", pose=(1.0, 0.0, 0.0), room="kitchen")])
    with pytest.raises(LocationError, match="ambiguous"):
        SemanticMap([base, SemanticLocation(name="pantry", pose=(1.0, 0.0, 0.0), room="pantry",
                                            aliases=("厨房",))])


@pytest.mark.parametrize(
    "payload,match",
    [
        ({"locations": []}, "non-empty"),
        ({}, "non-empty"),
        ({"locations": [{"name": "x", "pose": [1.0, 2.0], "room": "r"}]}, "3-element pose"),
        ({"locations": [{"name": "x", "pose": [1.0, 2.0, 3.0], "aliases": "kitchen"}]}, "aliases must be strings"),
        ({"locations": [{"name": "", "pose": [1.0, 2.0, 3.0]}]}, "requires a name"),
        ({"locations": [{"name": "x", "pose": [1.0, float("nan"), 3.0]}]}, "finite"),
    ],
)
def test_invalid_layouts_are_rejected(payload, match):
    with pytest.raises(LocationError, match=match):
        SemanticMap.from_dict(payload)


def test_missing_or_malformed_layout_files_fail_closed(tmp_path):
    with pytest.raises(LocationError, match="not found"):
        SemanticMap.from_file(tmp_path / "absent.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json")
    with pytest.raises(LocationError, match="not valid JSON"):
        SemanticMap.from_file(broken)


def test_normalisation_only_removes_presentation_noise():
    assert normalize_location_name("  The Kitchen ") == "kitchen"
    assert normalize_location_name("厨房") == "厨房"
    # Distinct names must stay distinct: nothing here does fuzzy matching.
    assert normalize_location_name("bedroom") != normalize_location_name("bathroom")


def test_describe_exposes_every_location_for_diagnostics(home_map):
    described = home_map.describe()
    assert len(described) == len(home_map)
    assert {entry["name"] for entry in described} == set(home_map.names())
    json.dumps(described)


def test_shipped_layout_matches_the_commissioned_scene_waypoints():
    """The navigation stack is verified against home_scene.py; so is this map.

    If the scene moves a room and the layout is not updated, a semantic
    navigation would aim at a pose the commissioned route never validated.
    """

    source = HOME_SCENE.read_text()
    block = source[source.index("HOME_WAYPOINTS = {"):source.index("HOME_ROUTE_EDGES")]
    # The scene expresses the quaternion through the module constant _Q, so
    # inline it before evaluating the remaining literal.
    # _Q is written as a power expression, which literal_eval refuses; parse the
    # two numbers and compute the value rather than reaching for eval().
    expression = re.search(r"^_Q = (.+)$", source, re.MULTILINE).group(1)
    base_text, exponent_text = (part.strip() for part in expression.split("**"))
    quaternion = float(base_text) ** float(exponent_text)
    literal = block[block.index("{"):block.rindex("}") + 1].replace("_Q", repr(quaternion))
    waypoints_raw = ast.literal_eval(literal)
    waypoints = {name: [float(value) for value in pose] for name, pose in waypoints_raw.items()}

    assert waypoints, "failed to parse HOME_WAYPOINTS"
    layout = json.loads(DEFAULT_LAYOUT_PATH.read_text())
    for entry in layout["locations"]:
        name = entry["name"]
        assert name in waypoints, f"{name} is not a commissioned waypoint"
        assert entry["pose"][0] == pytest.approx(waypoints[name][0])
        assert entry["pose"][1] == pytest.approx(waypoints[name][1])
        assert entry["pose"][2] == pytest.approx(0.0)
