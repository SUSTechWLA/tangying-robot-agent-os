"""Associate a live, calibrated observation with a saved point-cloud annotation.

This adapter reads the robot's perception provider. It never invents detector
labels from cloud geometry, refreshes historical labels, or authorizes motion.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time

import numpy as np
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message

from .contracts import Reconstruction, validate_pose
from .geometry import transform
from .semantic_cloud import query_document
from .service_registry import RegisteredService, ServiceError, object_schema
from .tool_schema import validate_value

MAX_OBSERVATION_AGE_MS = 2000
ASSOCIATION_TOLERANCE_M = .15
MIN_CONFIDENCE = .5
MAX_ENTITIES = 128
MAX_RECONSTRUCTION_BYTES = 2_000_000


def _now():
    return int(time.time() * 1000)


def _reject(code, message):
    raise ServiceError(code, message, rejected=True)


def _mapping(value):
    return MessageToDict(value) if isinstance(value, Message) else dict(value or {})


def _canonical(value):
    """Canonical evidence bytes, stable across integral JSON number spellings.

    Protobuf Struct makes every number a double, whereas Go may write 1.0 as
    1. Normalize finite integral floats (including negative zero) recursively;
    booleans remain booleans. Non-integral floats keep Python JSON's spelling.
    This is a documented artifact format, not an RFC 8785 implementation.
    """
    def normalize(item):
        if isinstance(item, float) and math.isfinite(item) and item.is_integer():
            return int(item)
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [normalize(child) for child in item]
        return item
    return json.dumps(normalize(value), ensure_ascii=True, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode()


def _integer(value, *, minimum=1, maximum=2**53-1):
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not np.isfinite(value) or value != int(value) or not minimum <= value <= maximum:
        raise ValueError("capture identity requires positive safe integers")
    return int(value)


def observation_schema():
    revision = {"type": "string", "minLength": 64, "maxLength": 64}
    return object_schema({"mapRevision": revision, "semanticRevision": revision,
        "annotationId": {"type": "string", "minLength": 1, "maxLength": 128},
        "requireMatch": {"type": "boolean"},
        "notBeforeUnixMs": {"type": "integer", "minimum": 1, "maximum": 2**53-1}},
        ["mapRevision", "semanticRevision", "annotationId"])


def _capture(workflow, identity, not_before):
    provider = workflow.entity_source
    if not callable(provider):
        _reject("SEMANTIC_PERCEPTION_UNAVAILABLE", "当前机器人未接入实体感知源。")
    calibration = copy.deepcopy(workflow.calibration_get())
    if calibration.get("revision") != identity["calibrationRevision"]:
        _reject("MAP_REVISION_CONFLICT", "感知前标定已变化，请重新读取地图。")
    sources = {camera.get("sourceId") for camera in calibration.get("cameras", {}).values()
               if isinstance(camera, dict)}
    try:
        view = provider()
    except Exception as error:
        raise ServiceError("SEMANTIC_PERCEPTION_FAILED", "实体感知源未能提供观测。", rejected=True) from error
    try:
        raw = _mapping(view.reconstruction)
        if len(raw.get("entities", [])) > MAX_ENTITIES or len(raw.get("points", [])) > 4096:
            raise ValueError("capture exceeds entity or point budget")
        if len(_canonical(raw)) > MAX_RECONSTRUCTION_BYTES:
            raise ValueError("capture exceeds byte budget")
        for key in ("observedAtUnixMs", "sequence"):
            raw[key] = _integer(raw.get(key))
        if "pointColors" in raw:
            raw["pointColors"] = [[_integer(value, minimum=0, maximum=255) for value in color]
                                  for color in raw["pointColors"]]
        capture = Reconstruction.model_validate(raw)
        state = _mapping(view.robot_state)
        metadata = _mapping(state.get("perception"))
        stamp = _integer(view.wall_time_unix_ms)
        if (capture.robot_id != workflow.robot_id or capture.source_type not in
                {"rgbd_camera", "stereo_camera", "lidar", "sensor_fusion"}
                or capture.source_id not in sources
                or capture.transform_revision != identity["calibrationRevision"]
                or capture.observation_id != view.observation_id
                or capture.observed_at_unix_ms != stamp
                or state.get("base_pose_frame") != "world"):
            raise ValueError("capture robot, source, calibration, frame or identity disagrees")
        base_pose = list(state.get("base_pose", []))
        if any(isinstance(value, bool) for value in base_pose):
            raise ValueError("base pose cannot contain booleans")
        validate_pose(base_pose)
        for key, expected in (("source_id", capture.source_id),
                              ("camera_frame_id", capture.source_frame_id),
                              ("calibration_revision", capture.transform_revision)):
            if key in metadata and metadata[key] != expected:
                raise ValueError("same-frame perception metadata disagrees")
        projected = [{"entityId": entity.entity_id, "category": entity.category,
                      "pose": list(entity.pose_xyz_quat), "confidence": float(entity.confidence),
                      "attributes": dict(entity.attributes), "relation": entity.relation}
                     for entity in view.entities]
        if projected != [entity.to_wire() for entity in capture.entities]:
            raise ValueError("projected entities do not belong to the reconstruction")
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as error:
        raise ServiceError("SEMANTIC_OBSERVATION_INVALID", "观测的来源、坐标、标定或同帧证据不一致。",
                           rejected=True) from error
    if not 0 <= _now()-stamp <= MAX_OBSERVATION_AGE_MS or stamp < not_before:
        _reject("SEMANTIC_OBSERVATION_STALE", "需要新鲜且晚于本次验证下界的观测，不能复用旧帧或未来帧。")
    return capture, base_pose


def observe_annotation(services, parameters):
    """Return bounded live evidence; ``requireMatch`` fails closed for objects."""
    validate_value(parameters, observation_schema())
    _, _, identity = services._map(parameters)
    document = services.store.read(identity)
    if document["semanticRevision"] != parameters["semanticRevision"]:
        _reject("SEMANTIC_REVISION_CONFLICT", "标注已变化，请重新查询。")
    annotation = next((item for item in document["annotations"]
                       if item["id"] == parameters["annotationId"]), None)
    if annotation is None:
        _reject("SEMANTIC_NOT_FOUND", "当前地图没有此点云标注。")
    if parameters.get("requireMatch", False) and annotation["kind"] != "object":
        _reject("SEMANTIC_OBJECT_REQUIRED", "实体感知复核只适用于物体；区域到达须使用定位验证。")
    target = query_document(document, {"name": annotation["name"], "includeStale": True},
                            now_unix_ms=_now())["results"][0]
    workflow = services.workflow
    with workflow._lock:
        anchor = workflow.map_from_world.copy()
        world_revision = workflow.world_frame_revision
    capture, base_pose = _capture(workflow, identity, parameters.get("notBeforeUnixMs", 0))
    low, high = np.asarray(annotation["bounds"]["min"]), np.asarray(annotation["bounds"]["max"])
    points = np.asarray(capture.points, dtype=float).reshape((-1, 3))
    mapped_points = transform(points, anchor)
    geometry_count = int(np.all((mapped_points >= low) & (mapped_points <= high), axis=1).sum())
    matches = []
    for entity in capture.entities:
        if entity.category.casefold() != annotation["category"].casefold():
            continue
        mapped = transform(np.asarray([entity.pose[:3]]), anchor)[0]
        outside = np.maximum(np.maximum(low-mapped, mapped-high), 0.)
        if float(np.linalg.norm(outside)) <= ASSOCIATION_TOLERANCE_M and entity.confidence >= MIN_CONFIDENCE:
            matches.append({"entityId": entity.entity_id, "category": entity.category,
                "mapPosition": mapped.tolist(), "confidence": entity.confidence,
                "distanceToBoundsM": float(np.linalg.norm(outside))})
    confirmed = (annotation["kind"] == "object" and not annotation.get("ambiguous", False)
                 and annotation["confidence"] >= MIN_CONFIDENCE and len(matches) == 1)
    reason = ("matched" if confirmed else "region_requires_localization" if annotation["kind"] != "object"
              else "annotation_ambiguous" if annotation.get("ambiguous") or annotation["confidence"] < MIN_CONFIDENCE
              else "multiple_matches" if len(matches) > 1 else "not_observed")
    services._check_current(identity)
    if services.store.read(identity)["semanticRevision"] != document["semanticRevision"]:
        _reject("SEMANTIC_REVISION_CONFLICT", "观察期间标注已变化，结果不能绑定到当前任务。")
    with workflow._lock:
        if workflow.world_frame_revision != world_revision or not np.array_equal(workflow.map_from_world, anchor):
            _reject("MAP_REVISION_CONFLICT", "观察期间坐标变换已变化。")
    if not 0 <= _now()-capture.observed_at_unix_ms <= MAX_OBSERVATION_AGE_MS:
        _reject("SEMANTIC_OBSERVATION_STALE", "处理后观测已过期，请重新获取。")
    result = {"schemaVersion": "semantic.cloud.observation.v1", "identity": identity,
        "activeMap": {key: identity[key] for key in ("mapId", "mapRevision", "calibrationRevision")},
        "mapRevision": identity["mapRevision"], "semanticRevision": document["semanticRevision"],
        "annotationId": annotation["id"], "frameId": "map", "worldFrameRevision": world_revision,
        "observationId": capture.observation_id, "sourceId": capture.source_id,
        "sourceType": capture.source_type, "sourceFrameId": capture.source_frame_id,
        "captureBasePose": base_pose, "captureBaseFrameId": "world",
        "observedAtUnixMs": capture.observed_at_unix_ms, "sequence": capture.sequence,
        "targetFresh": not target["stale"], "targetObservedAtUnixMs": target["observedAtUnixMs"],
        "semanticConfirmed": confirmed, "geometryObserved": geometry_count > 0,
        "geometryPointCount": geometry_count, "matchCount": len(matches), "matches": matches[:3],
        "matchesTruncated": len(matches) > 3, "reason": reason,
        "associationToleranceM": ASSOCIATION_TOLERANCE_M,
        "perceptionDigest": hashlib.sha256(_canonical(capture.to_wire())).hexdigest(),
        "executionAuthorized": False, "manipulationReachVerified": False}
    # Bind the entire bounded decision and capture-time base to a reproducible
    # content digest. This is an integrity identifier, not a digital signature.
    result["evidenceHash"] = hashlib.sha256(_canonical(result)).hexdigest()
    if len(_canonical(result)) > 7000:
        _reject("SEMANTIC_OBSERVATION_BUDGET", "感知证据摘要超过上下文预算。")
    if parameters.get("requireMatch", False) and not confirmed:
        _reject("SEMANTIC_TARGET_AMBIGUOUS" if reason in {"multiple_matches", "annotation_ambiguous"}
                else "SEMANTIC_TARGET_NOT_OBSERVED", "未获得该标注物体的唯一新鲜感知证据。")
    return result


def register_observation_service(services, registry):
    registry.register(RegisteredService("semantic.observe",
        "用当前已标定感知源复核点云标注；几何可见不等于语义确认，旧标注不会被刷新。",
        observation_schema(), lambda parameters: observe_annotation(services, parameters), False,
        {"version": "1", "effects": ["READ"], "resources": [],
         "outputSchema": {"type": "object", "additionalProperties": True},
         "planningFields": ["activeMap", "mapRevision", "semanticRevision", "annotationId", "frameId",
            "worldFrameRevision", "observationId", "sourceId", "observedAtUnixMs", "targetFresh",
            "semanticConfirmed", "geometryObserved", "geometryPointCount", "matchCount", "matches",
            "matchesTruncated", "reason", "evidenceHash", "executionAuthorized", "manipulationReachVerified"]}))
