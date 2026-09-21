"""Four ways to turn a survey into "go to the kitchen", and what each one costs.

A semantic layer has to answer one question - *where is the robot supposed to
stand when it is in the kitchen* - and there are at least four defensible ways to
produce the answer. They are not variations on a theme; they sit at different
points on a trade between **how much a human must know about this house** and
**how well the answer survives the house changing**:

``static``
    A person writes the pose. Exact, free to query, and silently wrong the moment
    the map is re-surveyed or the furniture moves - a table cannot notice.

``seeded``
    A person writes only *a point somewhere in the room* - something they can see
    on a floor plan in one glance - and the surveyed map decides the pose: the
    deepest interior cell of the region that point falls in. The human supplies
    "which room", the map supplies "where exactly", and the exact pose follows the
    map when the map changes.

``segmented``
    Nobody writes anything. Rooms are the regions the occupancy grid falls into
    (see `room_segmentation`), named by position and size. Free to build and
    honest about being unlabelled, which makes it a good baseline for what "no
    human input at all" is actually worth.

``landmark``
    Rooms are named by what is *in* them: a bed means bedroom, a sink means
    bathroom. This is the only representation that can answer "go to the room with
    the bed" - and the only one that can be wrong because a detector was wrong.

All four return the same structure, in the shape the map package already stores
(`map.semantics.v1` ``workspaces[]``), so a comparison between them is a
comparison of the answers and not of the plumbing.

The comparison itself - the scoring, the ground truth and the report - lives in
``scripts/semantic_map_benchmark.py``. It is not re-exported from here: this module
knows how to *produce* a representation, and a second definition of what "better"
means would be a second place for the two to disagree.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from .room_segmentation import segment_rooms

__all__ = [
    "REPRESENTATIONS",
    "Representation",
    "build_workspaces",
]

#: The four ways, in the order they are reported.
REPRESENTATIONS = ("static", "seeded", "segmented", "landmark")

#: How close to a room's deepest point a named pose has to be to count as "in that
#: room" when a representation is scored. One metre is roughly a doorway: a pose
#: further than this from the room it claims is a pose in a different room.
ROOM_MATCH_RADIUS_M = 1.0

#: Furniture that names a room, used by the ``landmark`` representation. The
#: mapping is deliberately small and literal: it says what the detector saw, not
#: what the room is for.
LANDMARK_ROOMS = {
    "bed": "bedroom", "床": "bedroom",
    "sink": "bathroom", "toilet": "bathroom", "洗手池": "bathroom", "马桶": "bathroom",
    "sofa": "living_room", "couch": "living_room", "沙发": "living_room",
    "counter": "kitchen", "stove": "kitchen", "fridge": "kitchen",
    "橱柜": "kitchen", "灶台": "kitchen", "冰箱": "kitchen",
    "table": "dining_room", "餐桌": "dining_room",
}


@dataclass
class Representation:
    """One semantic layer, plus what it cost to produce."""

    kind: str
    workspaces: list[dict[str, Any]]
    #: Numbers a human had to supply for *this* house. The whole point of the
    #: comparison: a static table needs a full pose per room, a seeded one needs a
    #: point, a segmented one needs nothing.
    human_inputs: int
    build_seconds: float
    #: Bytes the layer would occupy in the map package.
    payload_bytes: int
    notes: str = ""
    #: Set when the representation could not be built at all for this map - an
    #: absent answer is a result, and reporting it as an empty list would hide it.
    unavailable: str = ""
    #: Set when the representation published, but not well enough to be trusted for
    #: every name it carries. Distinct from ``unavailable``: the poses are real, and
    #: a caller may still use the names that are not named here.
    degraded: str = ""

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(str(item.get("name", "")) for item in self.workspaces)

    def resolve(self, name: str) -> dict[str, Any] | None:
        """The workspace a name refers to, using the same normalisation the tool
        layer uses, so a representation is not credited for an alias it does not
        actually publish."""
        from .semantic_map import normalize_location_name

        wanted = normalize_location_name(name)
        for item in self.workspaces:
            for label in (item.get("name", ""), *item.get("aliases", [])):
                if normalize_location_name(str(label)) == wanted:
                    return item
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "workspaceCount": len(self.workspaces),
                "humanInputs": self.human_inputs,
                "buildSeconds": round(self.build_seconds, 6),
                "payloadBytes": self.payload_bytes, "notes": self.notes,
                "unavailable": self.unavailable,
                "aliases": sum(len(item.get("aliases", [])) for item in self.workspaces)}


def _workspace(name: str, x: float, y: float, yaw: float, *, aliases: Iterable[str] = (),
               source: str, clearance_m: float | None = None,
               room_m2: float | None = None) -> dict[str, Any]:
    """One entry, in the shape ``map.semantics.v1`` already stores."""
    half = yaw / 2.0
    entry: dict[str, Any] = {
        "name": name,
        "aliases": list(aliases),
        "target": [float(x), float(y), 0.035],
        "navigationPose": [float(x), float(y), 0.035,
                           float(math.cos(half)), 0.0, 0.0, float(math.sin(half))],
        "annotationSource": source,
    }
    if clearance_m is not None:
        entry["clearanceM"] = round(float(clearance_m), 4)
    if room_m2 is not None:
        entry["roomAreaM2"] = round(float(room_m2), 4)
    return entry


def _payload_bytes(workspaces: Sequence[Mapping[str, Any]]) -> int:
    import json

    return len(json.dumps(list(workspaces), ensure_ascii=False,
                          separators=(",", ":")).encode())


def _yaw_towards(origin_xy: tuple[float, float], target_xy: tuple[float, float]) -> float:
    """Face the room's centre from the door when we know one, otherwise face +x.

    Stated rather than derived from anything: a base pose's heading only matters
    for what the camera and the arm can reach next, and neither this module nor
    the map knows that yet.
    """
    dx, dy = target_xy[0] - origin_xy[0], target_xy[1] - origin_xy[1]
    return math.atan2(dy, dx) if math.hypot(dx, dy) > 1e-6 else 0.0


# -- the four builders -------------------------------------------------------


def build_static(anchors: Mapping[str, Mapping[str, Any]], *, anchor_pose) -> Representation:
    """The shipped representation: one authored (x, y, yaw) per room.

    ``anchors`` maps a room name to ``{"pose": (x, y, yaw), "aliases": [...]}`` in
    the house's own frame; ``anchor_pose`` is the map frame's pose in that frame,
    exactly as ``RobotWorkflow.semantic_workspaces`` already converts.
    """
    from .dense_slam import transform

    started = time.perf_counter()
    workspaces = []
    for name, spec in anchors.items():
        pose = np.asarray([[*spec["pose"]]], dtype=float)
        point = transform(pose, np.asarray(anchor_pose, dtype=float))[0]
        workspaces.append(_workspace(name, point[0], point[1], float(spec["pose"][2]),
                                     aliases=spec.get("aliases", ()),
                                     source="commissioned_static"))
    return Representation(
        kind="static", workspaces=workspaces,
        # A full pose per room: three numbers and a name.
        human_inputs=sum(4 for _ in anchors),
        build_seconds=time.perf_counter() - started,
        payload_bytes=_payload_bytes(workspaces),
        notes="人手写每个房间的完整位姿；地图重建后不会跟随")


def build_seeded(cells, *, resolution: float, origin, seeds: Mapping[str, Any],
                 footprint_radius_m: float, door_radius_m: float,
                 seed_xy: tuple[float, float] | None = None,
                 min_room_area_m2: float = 1.0) -> Representation:
    """Name a point in each room; let the map decide where the robot stands.

    The human's contribution is a *point they can point at on a floor plan*, not a
    pose - and the pose that comes out is the deepest interior cell of whatever
    region that point falls in, so it is inside free space, clear of obstacles,
    and moves with the map when the map is re-surveyed.
    """
    started = time.perf_counter()
    segmentation = segment_rooms(cells, resolution=resolution, origin=origin,
                                 door_radius_m=door_radius_m,
                                 footprint_radius_m=footprint_radius_m,
                                 seed_xy=seed_xy, min_room_area_m2=min_room_area_m2)
    workspaces, missing = [], []
    # Which names resolved to which region. Two names on one region is the failure
    # this representation actually has, and it is silent: each pose is individually
    # valid, inside free space and clear of obstacles, so nothing looks wrong - the
    # map simply cannot tell those rooms apart, and every one of those names sends
    # the robot to the same place. Measured on a real survey of the reference house
    # (36% of the grid still unknown, so the rooms do not connect through drivable
    # space), living_room, kitchen and bedroom all resolved to the corridor and
    # 0 of 12 clicks per room landed in the room they named. Reporting the collision
    # is what turns "the map-derived layer quietly degraded" into a fact a caller can
    # act on - and it needs no invented threshold, because two names on one region is
    # wrong at any coverage.
    claimed: dict[int, list[str]] = {}
    for name, spec in seeds.items():
        point = spec["point"] if isinstance(spec, Mapping) else spec
        room = segmentation.room_of(float(point[0]), float(point[1]))
        if room is None:
            # The seed fell outside every drivable region: report it rather than
            # snapping to the nearest room, because a name on the wrong room is a
            # robot sent to the wrong place with no error anywhere.
            missing.append(name)
            continue
        claimed.setdefault(room.label, []).append(name)
        workspaces.append(_workspace(
            name, room.centre_xy[0], room.centre_xy[1], float(spec.get("yaw", 0.0))
            if isinstance(spec, Mapping) else 0.0,
            aliases=spec.get("aliases", ()) if isinstance(spec, Mapping) else (),
            source="seeded_segmented", clearance_m=room.clearance_m, room_m2=room.area_m2))
    collisions = {label: sorted(names) for label, names in claimed.items() if len(names) > 1}
    representation = Representation(
        kind="seeded", workspaces=workspaces,
        # Two numbers per room instead of three: a point, plus the name.
        human_inputs=sum(3 for _ in seeds),
        build_seconds=time.perf_counter() - started,
        payload_bytes=_payload_bytes(workspaces),
        notes=f"人只给房间里的一个点；位姿由地图决定（door={door_radius_m} m）")
    if missing:
        representation.unavailable = f"seeds outside every region: {sorted(missing)}"
    if collisions:
        # The representation still publishes - the poses are real - but it says out
        # loud that some of its names are not distinguishable, so a caller can fall
        # back to commissioned poses for those rooms instead of sending the robot to
        # the same corridor under three different names.
        detail = "; ".join(f"{names} → one region" for names in collisions.values())
        representation.degraded = (
            f"{len(collisions)} region(s) claimed by more than one name: {detail}")
    return representation


def build_segmented(cells, *, resolution: float, origin, footprint_radius_m: float,
                    door_radius_m: float, seed_xy: tuple[float, float] | None = None,
                    min_room_area_m2: float = 1.0) -> Representation:
    """No human input at all: rooms are numbered by size and position.

    Included as the honest baseline for "what does zero authoring buy". It finds
    real rooms - the shapes are right, the clearances are right, the poses are
    reachable - and it cannot be asked for the kitchen, because nothing told it
    which room is the kitchen.
    """
    started = time.perf_counter()
    segmentation = segment_rooms(cells, resolution=resolution, origin=origin,
                                 door_radius_m=door_radius_m,
                                 footprint_radius_m=footprint_radius_m,
                                 seed_xy=seed_xy, min_room_area_m2=min_room_area_m2)
    workspaces = []
    for index, room in enumerate(segmentation.rooms, start=1):
        workspaces.append(_workspace(
            f"room_{index}", room.centre_xy[0], room.centre_xy[1],
            _yaw_towards(segmentation.origin, room.centre_xy),
            aliases=(), source="grid_segmented",
            clearance_m=room.clearance_m, room_m2=room.area_m2))
    return Representation(
        kind="segmented", workspaces=workspaces, human_inputs=0,
        build_seconds=time.perf_counter() - started,
        payload_bytes=_payload_bytes(workspaces),
        notes=f"零人工输入；房间按面积编号（door={door_radius_m} m）")


def build_landmark(objects: Sequence[Mapping[str, Any]], *, anchor_pose,
                   aliases: Mapping[str, Sequence[str]] | None = None) -> Representation:
    """Name rooms by the furniture the survey actually saw in them.

    The only representation that can be asked for "the room with the bed", and the
    only one whose answer can be wrong because a detector was wrong. It needs the
    robot to have driven past the furniture, so it is silent about any room the
    survey did not enter - which is reported rather than papered over.
    """
    started = time.perf_counter()
    aliases = aliases or {}
    grouped: dict[str, list[tuple[float, float]]] = {}
    for item in objects:
        category = str(item.get("category") or "").strip().lower()
        room = LANDMARK_ROOMS.get(category)
        if room is None:
            continue
        pose = item.get("pose") or []
        if len(pose) < 2 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in pose[:2]):
            continue
        grouped.setdefault(room, []).append((float(pose[0]), float(pose[1])))
    workspaces = []
    for room, points in sorted(grouped.items()):
        x = sum(p[0] for p in points) / len(points)
        y = sum(p[1] for p in points) / len(points)
        workspaces.append(_workspace(room, x, y, 0.0, aliases=aliases.get(room, ()),
                                     source="observed_landmark"))
    seen = sorted({str(item.get("category") or "").strip().lower()
                   for item in objects if item.get("category")})
    representation = Representation(
        kind="landmark", workspaces=workspaces, human_inputs=0,
        build_seconds=time.perf_counter() - started,
        payload_bytes=_payload_bytes(workspaces),
        notes="房间由看到过的家具命名；没进去过的房间没有条目")
    if not workspaces:
        # Say *which* vocabulary arrived and which was needed, because "no objects"
        # and "objects, none of them furniture" are different problems with
        # different fixes - the first is a survey that did not run a detector, the
        # second is a detector whose vocabulary cannot name a room.
        usable = seen and bool(set(seen) & set(LANDMARK_ROOMS))
        if not seen:
            representation.unavailable = (
                "object layer is empty: no perception pass ran over this map")
        elif not usable:
            representation.unavailable = (
                f"object layer has categories {seen}, none of which name a room; "
                f"naming needs {sorted(set(LANDMARK_ROOMS))}")
    return representation


def build_workspaces(kind: str, **kwargs) -> Representation:
    """Dispatch by name, so the comparison runs one code path per representation."""
    builders = {"static": build_static, "seeded": build_seeded,
                "segmented": build_segmented, "landmark": build_landmark}
    if kind not in builders:
        raise ValueError(f"unknown representation {kind!r}; know {sorted(builders)}")
    return builders[kind](**kwargs)


# -- scoring -----------------------------------------------------------------
