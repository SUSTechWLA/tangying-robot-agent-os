"""Runtime-owned semantic navigation and object commissioning contracts.

The agent consumes only this generic public contract. Scene names and simulator
fixtures remain private inputs to the driver service that publishes it.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from typing import Any

from .home_scene import (
    HOME_SCENE_REVISION,
    HOME_TASK_OBJECTS,
    HOME_TASK_SCENE_REVISION,
    HOME_WAYPOINTS,
)

_ALIASES = {
    "客厅": "living_room",
    "走廊": "home_corridor",
    "厨房": "kitchen",
    "卧室": "bedroom",
    "浴室": "bathroom",
    "卫生间": "bathroom",
    "厕所": "bathroom",
}


def build_semantic_services(
    scene: str,
    *,
    robot_id: str,
    calibration_revision: str,
    active_map: Mapping[str, Any] | None = None,
    map_to_world: Mapping[str, Any] | None = None,
    object_catalog: Sequence[Mapping[str, Any]] | None = None,
    recalled_objects: Mapping[str, Any] | None = None,
    now_unix_ms: int | None = None,
) -> dict[str, Any]:
    """Build semantic state for a commissioned home service.

    With no live map, the service declares the scene's commissioning map. A
    live map can replace that identity only with a validated transform tied to
    the exact map revision, preventing old coordinates from being relabeled as
    belonging to a newly scanned map. Drivers may replace the legacy catalogue
    with commissioned action references; these carry no measured object poses.
    None retains the default, whereas an explicit empty catalogue stays empty.
    """

    if scene not in {"home", "home_task"} or not _identity(robot_id) or not _identity(calibration_revision):
        return {}

    if active_map is None:
        map_revision = HOME_TASK_SCENE_REVISION if scene == "home_task" else HOME_SCENE_REVISION
        selected_map = {
            "mapId": "commissioned-" + scene.replace("_", "-"),
            "mapRevision": map_revision,
            "calibrationRevision": calibration_revision,
        }
        transform_pose = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    else:
        selected_map = _active_map(active_map, calibration_revision)
        if not selected_map:
            return {}
        transform_pose = _validated_transform(map_to_world, selected_map)
        if transform_pose is None:
            return {}

    goals = {
        name: _transform_pose(list(pose), transform_pose)
        for name, pose in HOME_WAYPOINTS.items()
    }
    navigation = {
        "schemaVersion": "semantic.navigation.v1",
        "frameId": "world",
        "robotId": robot_id,
        "mapId": selected_map["mapId"],
        "mapRevision": selected_map["mapRevision"],
        "calibrationRevision": selected_map["calibrationRevision"],
        "goals": goals,
        "aliases": dict(_ALIASES),
    }
    objects: list[dict[str, Any]] = []
    if scene == "home_task":
        objects = [
            {
                "id": item_id,
                "category": category,
                "attributes": {"color": color},
                "confidence": 1.0,
                "workArea": "kitchen",
            }
            for item_id, _body, _joint, category, color in HOME_TASK_OBJECTS
        ]
        objects.append(
            {
                "id": "kitchen-bin",
                "category": "storage_bin",
                "attributes": {"color": "blue"},
                "confidence": 1.0,
                "workArea": "kitchen",
            }
        )
    if object_catalog is not None:
        objects = _action_catalog(object_catalog)
    state = {
        "active_map": dict(selected_map),
        "semantic_navigation": navigation,
        "semantic_objects": objects,
    }
    # Where each object was last seen, in the active map's frame, with the age of
    # that sighting. The action catalogue above deliberately carries no measured
    # poses; this is the other half - remembered positions that a caller may drive
    # to and must re-confirm on arrival. Absent when no survey has recorded the
    # object, so "we never saw it" and "we saw it here" stay distinguishable.
    try:
        recall = _recall(recalled_objects, selected_map, now_unix_ms, _navigation_plane(navigation))
    except ValueError as error:
        # A layer that does not belong to this map is not a reason to lose the
        # observation it was decorating: the caller simply gets no memory.
        state["semantic_recall_error"] = str(error)
        recall = {}
    if recall:
        state["semantic_recall"] = recall
    return state


def _navigation_plane(navigation: Mapping[str, Any] | None) -> float:
    """The height the commissioned goals sit at, which is the plane a base drives in.

    A ``vantagePose`` is lifted from the object layer's two-dimensional
    ``observedFrom`` (x, y, yaw), which carries no height at all. It used to be
    lifted with z = 0 — the map frame's floor — while the commissioned goals carry
    the base height (0.035 on this robot). The two are then different kinds of
    pose wearing the same name, and the consumer's workspace check refuses the
    recalled one: ``goal exceeds robot workspace on navigation.z``, for every
    household task that used the recall path.

    Reading the plane off the goals rather than declaring a second constant is what
    keeps the two producers in agreement: there is one plane, and both use it.
    """
    goals = (navigation or {}).get("goals")
    if not isinstance(goals, Mapping):
        return 0.0
    for pose in goals.values():
        if (isinstance(pose, Sequence) and not isinstance(pose, (str, bytes))
                and len(pose) == 7 and isinstance(pose[2], (int, float))):
            return float(pose[2])
    return 0.0


def _recall(document: Mapping[str, Any] | None, selected_map: Mapping[str, Any],
            now_unix_ms: int | None, plane: float = 0.0) -> dict[str, Any]:
    """Validate and group remembered object positions by category.

    The argument is the published object layer *document*. Its own schema and map
    identity are what make the entries inside it evidence about this map: a
    position measured against another map is a coordinate wearing this map's
    name. Ages are recomputed here rather than trusted from the file, because the
    file may have been written minutes - or days - ago.

    # Why mapRevision is not one of the identity fields

    It used to be, and that check could never pass. The object layer is one of the
    map's own artifacts: it is written into the map directory and its bytes are
    part of what the manifest hashes to produce ``mapRevision``. A document
    therefore cannot contain the hash of itself, so requiring the field meant
    every published layer was refused, ``semantic_recall`` was always empty, and
    grounding saw zero objects on a robot that had surveyed the room.

    The mistake survived because the unit tests fabricate the document with a
    matching ``mapRevision`` in their own helper, so the field was always present
    where it was checked and never present where it was produced. Identity is
    established here by the fields that genuinely can be inside the document —
    which map it names, and which calibration it was measured under — plus the
    fact that the reader obtained it from *this* map's directory.
    """
    if not document:
        return {}
    if document.get("schemaVersion") != "map.objects.v1":
        raise ValueError("recalled object layer has an unknown schema version")
    for field in ("mapId", "calibrationRevision"):
        if str(document.get(field) or "") != str(selected_map.get(field) or ""):
            raise ValueError(f"recalled object layer {field} does not match the active map")
    entries = document.get("objects") or []
    now = int(now_unix_ms) if isinstance(now_unix_ms, int) and now_unix_ms > 0 else int(time.time() * 1000)
    by_category: dict[str, list[dict[str, Any]]] = {}
    for raw in entries:
        if not isinstance(raw, Mapping):
            continue
        category = str(raw.get("category") or "").strip()
        pose = raw.get("pose")
        if not category:
            continue
        if (not isinstance(pose, Sequence) or isinstance(pose, (str, bytes)) or len(pose) != 3
                or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in pose)):
            continue
        seen_at = raw.get("lastSeenUnixMs")
        if not isinstance(seen_at, int) or seen_at <= 0 or seen_at > now:
            # A sighting stamped in the future is a clock disagreement, not
            # evidence about where anything is.
            continue
        observed_from = raw.get("observedFrom")
        vantage = None
        if (isinstance(observed_from, Sequence) and not isinstance(observed_from, (str, bytes))
                and len(observed_from) == 3
                and all(isinstance(value, (int, float)) and math.isfinite(value) for value in observed_from)):
            half = observed_from[2] / 2.0 if hasattr(observed_from[2], "__float__") else 0.0
            vantage = [float(observed_from[0]), float(observed_from[1]), float(plane),
                       math.cos(half), 0.0, 0.0, math.sin(half)]
        by_category.setdefault(category, []).append({
            "id": str(raw.get("id") or ""), "pose": [float(value) for value in pose],
            "vantagePose": vantage,
            "frameId": "map", "ageMs": now - seen_at, "lastSeenUnixMs": seen_at,
            "sightings": int(raw.get("sightings") or 1),
            "confidence": float(raw.get("confidence") or 0.0),
            "attributes": {str(key): str(value)
                           for key, value in dict(raw.get("attributes") or {}).items()},
        })
    for category, found in by_category.items():
        found.sort(key=lambda item: item["ageMs"])
        by_category[category] = found[:8]
    return {"schemaVersion": "semantic.recall.v1", "frameId": "map",
            "mapId": str(selected_map.get("mapId") or ""),
            "mapRevision": str(selected_map.get("mapRevision") or ""),
            "categories": by_category}


def _action_catalog(catalog: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(catalog, Sequence) or isinstance(catalog, (str, bytes)):
        raise TypeError("semantic object catalog must be a sequence")
    objects, seen = [], set()
    for item in catalog:
        if not isinstance(item, Mapping) or any(not _identity(item.get(key)) for key in ("id", "category", "workArea")):
            raise ValueError("semantic object catalog entry requires id, category and workArea")
        if item["id"] in seen:
            raise ValueError("semantic object catalog contains duplicate ids")
        seen.add(item["id"])
        attributes = item.get("attributes", {})
        confidence = item.get("confidence", 1.)
        if (not isinstance(attributes, Mapping)
                or any(not _identity(key) or not isinstance(value, str) for key, value in attributes.items())
                or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or not 0. <= confidence <= 1.):
            raise ValueError("semantic object catalog has invalid attributes or confidence")
        # Only commissioned reference metadata crosses this boundary. Poses
        # and relations must be supplied later by fresh measured perception.
        objects.append({"id": item["id"], "category": item["category"],
                        "attributes": {key: value for key, value in attributes.items() if value.strip()},
                        "confidence": float(confidence), "workArea": item["workArea"]})
    return objects


def _identity(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


def _active_map(value: Mapping[str, Any], calibration_revision: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result = {key: value.get(key) for key in ("mapId", "mapRevision", "calibrationRevision")}
    if not all(_identity(item) for item in result.values()):
        return {}
    if result["calibrationRevision"] != calibration_revision:
        return {}
    return result  # type: ignore[return-value]


def _validated_transform(
    value: Mapping[str, Any] | None,
    active_map: Mapping[str, str],
) -> list[float] | None:
    if not isinstance(value, Mapping) or value.get("validated") is not True:
        return None
    if value.get("fromFrame") != "commissioning_world" or value.get("toFrame") != "world":
        return None
    if any(value.get(key) != active_map[key]
           for key in ("mapId", "mapRevision", "calibrationRevision")):
        return None
    pose = value.get("pose")
    if not _valid_pose(pose):
        return None
    return [float(item) for item in pose]


def _valid_pose(value: Any) -> bool:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 7
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value)
        or any(not math.isfinite(float(item)) for item in value)
    ):
        return False
    return abs(sum(float(item) ** 2 for item in value[3:]) - 1.0) <= 0.001


def _transform_pose(pose: list[float], transform: list[float]) -> list[float]:
    translation = transform[:3]
    rotation = transform[3:]
    rotated = _rotate(rotation, pose[:3])
    orientation = _multiply_quaternion(rotation, pose[3:])
    return [
        rotated[0] + translation[0],
        rotated[1] + translation[1],
        rotated[2] + translation[2],
        *orientation,
    ]


def _rotate(quaternion: Sequence[float], vector: Sequence[float]) -> list[float]:
    conjugate = [quaternion[0], -quaternion[1], -quaternion[2], -quaternion[3]]
    rotated = _multiply_quaternion(
        _multiply_quaternion(quaternion, [0.0, *vector]),
        conjugate,
    )
    return rotated[1:]


def _multiply_quaternion(first: Sequence[float], second: Sequence[float]) -> list[float]:
    aw, ax, ay, az = (float(item) for item in first)
    bw, bx, by, bz = (float(item) for item in second)
    return [
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ]
