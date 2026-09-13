"""Runtime-owned semantic navigation and object commissioning contracts.

The agent consumes only this generic public contract. Scene names and simulator
fixtures remain private inputs to the driver service that publishes it.
"""

from __future__ import annotations

import math
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
    return {
        "active_map": dict(selected_map),
        "semantic_navigation": navigation,
        "semantic_objects": objects,
    }


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
