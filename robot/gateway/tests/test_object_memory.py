"""Object memory: what the robot has actually seen, where, and how long ago.

The semantic layer shipped before this answered "which named places exist" and
"which objects may be operated". Neither says where an object was. These tests
hold the three properties that make the added layer evidence: an instance exists
only because a detection was reported, every sighting carries its own time, and
association is bounded so a moved object does not teleport.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

import pytest
from tangying_robot_gateway.object_memory import (
    SCHEMA_VERSION,
    ObjectMemory,
)


@dataclass
class Entity:
    entity_id: str
    category: str
    pose_xyz_quat: list[float]
    attributes: dict[str, str]
    confidence: float = .9


def mug(x: float, y: float, z: float = .85, *, colour: str = "white") -> Entity:
    return Entity("ceramic-mug", "cup", [x, y, z, 1., 0., 0., 0.], {"color": colour})


def test_a_sighting_is_recorded_with_its_own_time_and_place():
    memory = ObjectMemory()
    assert memory.observe([mug(1.0, 2.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000,
                          evidence_frame_id="map") == 1
    document = memory.document(now_unix_ms=1_500, map_id="map-a", calibration_revision="c" * 64)
    assert document["schemaVersion"] == SCHEMA_VERSION
    assert document["frameId"] == "map"
    (instance,) = document["objects"]
    assert instance["category"] == "cup"
    assert instance["pose"] == [1.0, 2.0, .85]
    assert instance["lastSeenUnixMs"] == 1_000 and instance["ageMs"] == 500
    assert instance["sightings"] == 1
    assert instance["evidenceFrameId"] == "map"


def test_repeated_sightings_join_one_instance_and_keep_the_newest_place():
    memory = ObjectMemory()
    for index, (x, y) in enumerate([(1.0, 2.0), (1.02, 2.01), (0.99, 1.98)]):
        memory.observe([mug(x, y)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000 + index)
    (instance,) = memory.instances
    assert instance.sightings == 3
    assert instance.pose[:2] == pytest.approx([0.99, 1.98])
    assert instance.first_seen_unix_ms == 1_000 and instance.last_seen_unix_ms == 1_002
    assert len(instance.history) == 3


def test_a_moved_object_becomes_a_new_instance_instead_of_teleporting():
    memory = ObjectMemory(association_gate_m=.12)
    memory.observe([mug(1.0, 2.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000)
    memory.observe([mug(1.6, 2.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=2_000)
    assert [instance.pose[:2] for instance in memory.instances] == [[1.0, 2.0], [1.6, 2.0]]
    # The newest sighting is the one a caller offering to go and look gets first.
    recalled = memory.recall("cup", now_unix_ms=2_500)
    assert [instance.pose[0] for instance in recalled] == [1.6, 1.0]


def test_attributes_that_contradict_do_not_join_the_same_instance():
    memory = ObjectMemory()
    memory.observe([mug(1.0, 2.0, colour="white")], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000)
    memory.observe([mug(1.0, 2.0, colour="blue")], map_from_world=[0., 0., 0.], stamp_unix_ms=1_100)
    assert len(memory.instances) == 2
    assert [instance.attributes["color"] for instance in memory.instances] == ["white", "blue"]


def test_an_entity_without_a_measured_place_is_not_remembered():
    """The commissioned catalogue declares objects; only perception places them."""
    memory = ObjectMemory()
    placed = Entity("ceramic-mug", "cup", [], {})
    unplaced = Entity("kitchen-tray", "storage_bin", [float("nan"), 0., 0.], {})
    assert memory.observe([placed, unplaced], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000) == 0
    assert memory.instances == []


def test_recall_filters_by_age_and_never_returns_a_stale_place_as_current():
    memory = ObjectMemory()
    memory.observe([mug(1.0, 2.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000)
    memory.observe([mug(3.0, 1.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=9_000)
    assert len(memory.recall("cup", now_unix_ms=9_500, max_age_ms=1_000)) == 1
    assert len(memory.recall("cup", now_unix_ms=9_500, max_age_ms=60_000)) == 2
    assert memory.recall("cup", now_unix_ms=9_500, max_age_ms=1_000)[0].pose[0] == 3.0
    with pytest.raises(ValueError, match="current time"):
        memory.recall("cup", max_age_ms=1_000)


def test_expired_instances_leave_the_published_layer_but_stay_in_the_session():
    memory = ObjectMemory(max_age_ms=5_000)
    memory.observe([mug(1.0, 2.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000)
    fresh = memory.document(now_unix_ms=3_000, map_id="m", calibration_revision="c" * 64)
    stale = memory.document(now_unix_ms=9_000, map_id="m", calibration_revision="c" * 64)
    assert len(fresh["objects"]) == 1 and stale["objects"] == []
    assert len(memory.instances) == 1, "the session still knows what it saw"


def test_the_anchor_is_applied_so_positions_are_in_the_map_frame():
    memory = ObjectMemory()
    memory.observe([mug(1.0, 0.0)], map_from_world=[5.0, 5.0, math.pi / 2], stamp_unix_ms=1_000)
    (instance,) = memory.instances
    assert instance.pose[:2] == pytest.approx([5.0, 6.0], abs=1e-9)


def test_reanchoring_moves_every_sighting_to_the_published_frame():
    memory = ObjectMemory()
    memory.observe([mug(1.0, 2.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000)
    assert memory.reanchor([0., 0., 0.], [1.0, 0.5, math.pi / 2]) == 1
    (instance,) = memory.instances
    assert instance.pose[:2] == pytest.approx([-1.0, 1.5], abs=1e-9)
    assert instance.history[0][:2] == pytest.approx([-1.0, 1.5], abs=1e-9)
    assert memory.reanchor([1.0, 0.5, math.pi / 2], [0., 0., 0.]) == 1
    assert memory.instances[0].pose[:2] == pytest.approx([1.0, 2.0], abs=1e-9)


def test_a_continuation_adopts_the_base_map_layer_with_its_own_timestamps():
    memory = ObjectMemory()
    base = ObjectMemory()
    base.observe([mug(1.0, 2.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000)
    document = base.document(now_unix_ms=2_000, map_id="base", calibration_revision="c" * 64)
    assert memory.merge(document) == 1
    adopted, = memory.instances
    assert adopted.last_seen_unix_ms == 1_000, "an old sighting must not become fresh"
    assert memory.recall("cup", now_unix_ms=2_100)[0].pose[0] == 1.0


def test_a_foreign_or_malformed_layer_is_refused_rather_than_guessed():
    memory = ObjectMemory()
    with pytest.raises(ValueError, match="unknown schema"):
        memory.merge({"schemaVersion": "map.objects.v2", "objects": []})
    with pytest.raises(ValueError, match="invalid pose"):
        memory.merge({"schemaVersion": SCHEMA_VERSION, "objects": [{"id": "x", "category": "cup",
                                                                   "pose": [0.0, 0.0], "lastSeenUnixMs": 1}]})
    with pytest.raises(ValueError, match="missing its category"):
        memory.merge({"schemaVersion": SCHEMA_VERSION, "objects": [{"id": "x", "category": "",
                                                                   "pose": [0.0, 0.0, 0.0], "lastSeenUnixMs": 0}]})


def test_the_published_layer_is_bounded_and_serialisable():
    memory = ObjectMemory(max_instances=4)
    for index in range(9):
        memory.observe([mug(float(index), 0.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000 + index)
    assert len(memory.instances) == 4, "an object budget keeps the artifact small"
    document = memory.document(now_unix_ms=2_000, map_id="m", calibration_revision="c" * 64)
    json.dumps(document)
    assert document["sightings"] == 9 and document["entityPolls"] == 9


def test_a_bad_cadence_or_anchor_is_refused():
    memory = ObjectMemory()
    with pytest.raises(ValueError, match="capture timestamp"):
        memory.observe([mug(1.0, 2.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=0)
    with pytest.raises(ValueError, match="map anchor"):
        memory.observe([mug(1.0, 2.0)], map_from_world=[0., 0.], stamp_unix_ms=1_000)
    with pytest.raises(ValueError, match="association gate"):
        ObjectMemory(association_gate_m=2.0)
    with pytest.raises(ValueError, match="max age"):
        ObjectMemory(max_age_ms=0)


# ── the vantage a sighting was taken from ───────────────────────────────────
# An object's position is not a destination: a base cannot drive to a mug that
# sits on a table. What a later task needs is where the robot stood when it could
# see the object, so every sighting records that pose too, in the map frame.

def test_a_sighting_records_the_base_pose_it_was_taken_from():
    memory = ObjectMemory()
    # Base at (1, 0) facing +x, expressed as the runtime's [x, y, z, qw, qx, qy, qz].
    memory.observe([mug(3.0, 0.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000,
                   base_pose=[1.0, 0.0, .035, 1.0, 0., 0., 0.])
    (instance,) = memory.instances
    assert instance.pose[:2] == pytest.approx([3.0, 0.0])
    assert instance.observed_from[:2] == pytest.approx([1.0, 0.0])
    assert instance.observed_from[2] == pytest.approx(0.0)


def test_the_vantage_follows_the_map_anchor_and_rotates_with_it():
    memory = ObjectMemory()
    memory.observe([mug(1.0, 2.0)], map_from_world=[5.0, 5.0, math.pi / 2], stamp_unix_ms=1_000,
                   base_pose=[1.0, 0.0, .0, 1.0, 0., 0., 0.])
    (instance,) = memory.instances
    assert instance.observed_from[:2] == pytest.approx([5.0, 6.0], abs=1e-9)
    assert instance.observed_from[2] == pytest.approx(math.pi / 2, abs=1e-9)


def test_the_newest_sighting_wins_the_vantage_as_well_as_the_position():
    memory = ObjectMemory()
    memory.observe([mug(1.0, 1.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000,
                   base_pose=[0.0, 0.0, 0., 1.0, 0., 0., 0.])
    memory.observe([mug(1.01, 1.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=2_000,
                   base_pose=[0.5, 0.5, 0., 1.0, 0., 0., 0.])
    (instance,) = memory.instances
    assert instance.sightings == 2
    assert instance.observed_from[:2] == pytest.approx([0.5, 0.5])


def test_planar_odometry_is_accepted_as_the_vantage():
    # Not every driver reports a quaternion: a planar [x, y, yaw] odometry is the
    # same measurement in fewer numbers and must not be thrown away.
    memory = ObjectMemory()
    memory.observe([mug(3.0, 0.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000,
                   base_pose=[1.0, 0.5, 0.25])
    (instance,) = memory.instances
    assert instance.observed_from == pytest.approx([1.0, 0.5, 0.25])


def test_a_driver_that_cannot_report_a_base_pose_leaves_the_vantage_empty():
    memory = ObjectMemory()
    memory.observe([mug(1.0, 2.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000)
    (instance,) = memory.instances
    assert instance.observed_from == []
    # A malformed pose is treated the same way: no vantage, never a guess.
    memory.observe([mug(1.0, 2.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=2_000,
                   base_pose=[1.0, 2.0])
    assert memory.instances[0].observed_from == []


def test_the_vantage_is_published_and_reanchored_with_its_heading():
    memory = ObjectMemory()
    memory.observe([mug(1.0, 0.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000,
                   base_pose=[0.5, 0.0, .0, 1.0, 0., 0., 0.])
    document = memory.document(now_unix_ms=1_500, map_id="m", calibration_revision="c" * 64)
    assert document["objects"][0]["observedFrom"][:2] == [0.5, 0.0]
    memory.reanchor([0., 0., 0.], [1.0, 0.5, math.pi / 2])
    published = memory.document(now_unix_ms=1_500, map_id="m", calibration_revision="c" * 64)
    assert published["objects"][0]["observedFrom"][:3] == pytest.approx([1.0, 1.0, math.pi / 2], abs=1e-9)


def test_a_continuation_adopts_the_vantage_too():
    base = ObjectMemory()
    base.observe([mug(1.0, 2.0)], map_from_world=[0., 0., 0.], stamp_unix_ms=1_000,
                 base_pose=[0.2, 0.3, 0., 1.0, 0., 0., 0.])
    document = base.document(now_unix_ms=2_000, map_id="base", calibration_revision="c" * 64)
    memory = ObjectMemory()
    assert memory.merge(document) == 1
    assert memory.instances[0].observed_from[:2] == pytest.approx([0.2, 0.3])
