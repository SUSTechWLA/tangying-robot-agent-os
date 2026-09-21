"""The map-derived semantic layer, and the ways it degrades silently.

``seeded`` is the representation this project recommends *when the map is good
enough*, so the interesting tests are the ones about what "good enough" means. The
failure it actually has is not a crash: each pose it publishes is individually valid -
inside free space, clear of obstacles, reachable - so a layer that has stopped being
able to tell the rooms apart looks exactly like a layer that works. It just sends
every name to the same place.

Two ways that happens, and both are now reported rather than published quietly:

* a seed falls where the grid has no drivable region at all (``unavailable``);
* two seeds fall in the *same* region, so two names cannot be distinguished
  (``degraded``).

Measured on a real survey of the reference house - 36% of the grid still unknown, so
the rooms do not connect through drivable space at a 0.42 m envelope - living_room,
kitchen and bedroom all resolved to the corridor, and 0 of 12 clicks per room landed
in the room they named. Nothing about the published output said so.
"""

from __future__ import annotations

import numpy as np
import pytest
from tangying_robot_gateway.semantic_workspaces import (
    REPRESENTATIONS,
    build_seeded,
    build_workspaces,
)

FREE = 0
OCCUPIED = 100
RESOLUTION = 0.05
FOOTPRINT = 0.32


def two_rooms(doorway_m: float, *, width_m: float = 8.0, height_m: float = 4.0) -> np.ndarray:
    """A wall with one centred doorway through it."""

    cells = np.full((round(height_m / RESOLUTION) + 2, round(width_m / RESOLUTION) + 2),
                    OCCUPIED, dtype=np.int8)
    cells[1:-1, 1:-1] = FREE
    column = cells.shape[1] // 2
    cells[:, column] = OCCUPIED
    gap = round(doorway_m / RESOLUTION)
    middle = cells.shape[0] // 2
    cells[middle - gap // 2: middle + gap // 2 + 1, column] = FREE
    return cells


def seed(point) -> dict:
    return {"point": list(point), "aliases": []}


def build(cells, seeds, **overrides):
    arguments = {"cells": cells, "resolution": RESOLUTION, "origin": (0.0, 0.0),
                 "seeds": seeds, "footprint_radius_m": FOOTPRINT, "door_radius_m": 0.60}
    arguments.update(overrides)
    return build_seeded(**arguments)


# --- the happy path ---------------------------------------------------------

def test_two_rooms_that_the_segmenter_can_tell_apart_publish_two_workspaces():
    # A 1.70 m doorway is below the 2.00 m threshold the commissioned radius closes at,
    # so the rooms separate; see `test_room_segmentation` for the threshold itself.
    result = build(two_rooms(1.70), {"left": seed((1.0, 2.0)), "right": seed((7.0, 2.0))})
    assert len(result.workspaces) == 2
    assert result.unavailable == "" and result.degraded == ""
    assert {item["name"] for item in result.workspaces} == {"left", "right"}
    # The published pose is not the click: it is the region's deepest interior point.
    left = next(item for item in result.workspaces if item["name"] == "left")
    assert left["target"][0] != pytest.approx(1.0)


# --- the two ways it degrades ----------------------------------------------

def test_two_names_on_one_region_are_reported_rather_than_published_quietly():
    """The failure the report measured, in its smallest form.

    A 2.60 m doorway stays open at the commissioned radius, so both seeds land in one
    region. Every published pose is still individually valid - which is exactly why
    this needs saying out loud: a caller who is not told will send "left" and "right"
    to the same corridor and only find out when the robot arrives at the wrong room.
    """

    result = build(two_rooms(2.60), {"left": seed((1.0, 2.0)), "right": seed((7.0, 2.0))})
    assert len(result.workspaces) == 2, "both poses are real, so both are still published"
    assert result.degraded, "two names on one region must be reported"
    assert "left" in result.degraded and "right" in result.degraded
    assert result.unavailable == ""
    # And the two names really do resolve to the same place.
    targets = {tuple(round(v, 6) for v in item["target"][:2]) for item in result.workspaces}
    assert len(targets) == 1


def test_a_seed_outside_every_region_is_unavailable_not_snapped():
    """Snapping to the nearest room is worse than refusing.

    A name attached to the wrong room is a robot sent to the wrong place with no
    error anywhere, so the seed is reported instead.
    """

    cells = two_rooms(1.70)
    cells[2, 2] = OCCUPIED          # the seed below sits in an obstacle
    result = build(cells, {"left": seed((0.12, 0.12)), "right": seed((7.0, 2.0))})
    assert [item["name"] for item in result.workspaces] == ["right"]
    assert "left" in result.unavailable


def test_unavailable_and_degraded_are_different_reports():
    """They want different repairs, so they are not one field.

    ``unavailable`` means the click landed somewhere the map has no region; the fix is
    a better click or a better map. ``degraded`` means the map cannot separate two
    rooms that the caller believes are separate; the fix is a different
    representation. Collapsing them into one "there was a problem" field would send
    both cases to the same wrong repair.
    """

    separate = build(two_rooms(2.60), {"left": seed((1.0, 2.0)), "right": seed((7.0, 2.0))})
    assert separate.degraded and not separate.unavailable
    unclickable = build(two_rooms(1.70), {"left": seed((1000.0, 1000.0))})
    assert unclickable.unavailable and not unclickable.degraded


# --- the representation contract -------------------------------------------

def test_the_builder_dispatches_to_every_advertised_representation():
    """REPRESENTATIONS is what callers iterate, so every entry must build."""

    cells = two_rooms(1.70)
    anchors = {"left": {"pose": (1.0, 2.0, 0.0), "aliases": []},
               "right": {"pose": (7.0, 2.0, 0.0), "aliases": []}}
    seeds = {"left": seed((1.0, 2.0)), "right": seed((7.0, 2.0))}
    # Each builder takes only what it needs, so the kwargs are per-kind: handing a
    # builder a grid it does not use would be measuring the harness.
    kwargs = {
        "static": {"anchors": anchors, "anchor_pose": (0.0, 0.0, 0.0)},
        "seeded": {"cells": cells, "resolution": RESOLUTION, "origin": (0.0, 0.0),
                   "seeds": seeds, "footprint_radius_m": FOOTPRINT, "door_radius_m": 0.60},
        "segmented": {"cells": cells, "resolution": RESOLUTION, "origin": (0.0, 0.0),
                      "footprint_radius_m": FOOTPRINT, "door_radius_m": 0.60},
        "landmark": {"objects": [{"category": "bed", "pose": [1.0, 2.0, 0.0],
                                  "confidence": 0.9}], "anchor_pose": (0.0, 0.0, 0.0)},
    }
    for kind in REPRESENTATIONS:
        representation = build_workspaces(kind, **kwargs[kind])
        assert representation.kind == kind
        # A representation that cannot answer says why, and does not pretend to be
        # an empty answer.
        if not representation.workspaces:
            assert representation.unavailable or representation.degraded
