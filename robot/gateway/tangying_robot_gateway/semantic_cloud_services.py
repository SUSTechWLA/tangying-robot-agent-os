"""Revision-bound point-cloud annotation services; geometry never authorizes motion."""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import itertools
import json
import math
import secrets
import time
from collections import Counter

import numpy as np

from .geometry import compose, relative
from .grid_navigation import GridPathPlanner
from .map_catalog import MapCatalog
from .map_pipeline import decode_lod
from .semantic_cloud import (
    DEFAULT_OBJECT_MAX_AGE_MS,
    SemanticCloudStore,
    compile_annotations,
    query_document,
)
from .service_registry import RegisteredService, ServiceError, object_schema


def _now():
    return int(time.time() * 1000)


def _reject(code, message):
    raise ServiceError(code, message, rejected=True)


def _view_bytes(value):
    # Go's encoding/json escapes these ASCII characters in addition to Unicode.
    encoded = json.dumps(value, ensure_ascii=True)
    for character in "<>&":
        encoded = encoded.replace(character, f"\\u{ord(character):04x}")
    return len(encoded.encode())


def _target_fresh(annotation, *, now_unix_ms, allow_stale_search):
    age = now_unix_ms - annotation["observedAtUnixMs"]
    fresh = age >= 0 and (annotation["kind"] != "object" or age <= DEFAULT_OBJECT_MAX_AGE_MS)
    if age < 0 or (not fresh and not allow_stale_search):
        _reject("SEMANTIC_STALE", "标注观测已过期，必须重新观察或显式搜索旧位置。")
    return fresh


def annotation_schema():
    text = {"type": "string", "minLength": 1, "maxLength": 128}
    vector = {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}}
    fields = {
        "id": text, "name": text, "category": text,
        "kind": {"type": "string", "enum": ["object", "region"]},
        "aliases": {"type": "array", "maxItems": 16, "items": text},
        "bounds": object_schema({"min": vector, "max": vector}, ["min", "max"]),
        "source": object_schema({"kind": {"type": "string", "enum": ["operator", "perception"]},
                                 "reference": {"type": "string", "minLength": 1, "maxLength": 512}},
                                ["kind", "reference"]),
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "observedAtUnixMs": {"type": "integer", "minimum": 1},
    }
    return object_schema(fields, [key for key in fields if key != "aliases"])


class SemanticCloudServices:
    def __init__(self, workflow):
        self.workflow = workflow
        self._store = None
        self._binding_key = secrets.token_bytes(32)

    @property
    def execution_binding_supported(self):
        # Only a driver that implements runtime binding admission may opt in.
        return getattr(self.workflow, "semantic_execution_binding_supported", False) is True

    def _sign_binding(self, identity, document, annotation, candidate, *, verification_mode,
                      allow_stale_search, world_frame_revision, footprint_radius, map_from_world):
        payload = {"schemaVersion": "semantic.execution.v1", "identity": identity,
            "semanticRevision": document["semanticRevision"], "annotationId": annotation["id"],
            "worldFrameRevision": world_frame_revision,
            "mapFromWorld": [float(value) for value in map_from_world],
            "goalPose": candidate["goalPose"], "footprintRadiusM": float(footprint_radius),
            "verificationMode": verification_mode, "allowStaleSearch": allow_stale_search,
            "targetObservedAtUnixMs": annotation["observedAtUnixMs"], "targetKind": annotation["kind"]}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode() + "." + hmac.new(
            self._binding_key, raw, hashlib.sha256).hexdigest()
        if len(token) > 2048:
            _reject("SEMANTIC_BINDING_BUDGET", "语义执行绑定超过大小限制。")
        return token

    def validate_binding(self, token, goal_pose, *, align_yaw=None):
        """Authenticate and revalidate before motion; no caller-supplied pose is trusted."""
        if not self.execution_binding_supported:
            _reject("SEMANTIC_EXECUTION_UNSUPPORTED", "当前驱动未实现语义导航执行边界。")
        if not isinstance(token, str) or not 1 <= len(token) <= 2048:
            _reject("SEMANTIC_BINDING_INVALID", "缺少有效的语义执行绑定。")
        try:
            encoded, signature = token.split(".")
            raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
            expected = hmac.new(self._binding_key, raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature mismatch")
            binding = json.loads(raw)
        except (ValueError, TypeError):
            _reject("SEMANTIC_BINDING_INVALID", "执行绑定无效或服务已重启，请重新规划。")
        if align_yaw is None:
            pose_matches = binding["goalPose"] == goal_pose
        else:
            target_yaw = 2*math.atan2(binding["goalPose"][6], binding["goalPose"][3])
            pose_matches = (goal_pose is None and isinstance(align_yaw, (int, float))
                and not isinstance(align_yaw, bool) and math.isfinite(align_yaw)
                and abs(math.atan2(math.sin(align_yaw-target_yaw), math.cos(align_yaw-target_yaw))) <= 1e-9)
        if binding.get("schemaVersion") != "semantic.execution.v1" or not pose_matches:
            _reject("SEMANTIC_GOAL_MISMATCH", "导航坐标与运行时签发的语义候选不同。")
        _, _, identity = self._map({"mapRevision": binding["identity"]["mapRevision"]})
        if identity != binding["identity"]:
            _reject("MAP_REVISION_CONFLICT", "语义执行绑定不属于当前地图、点云或机器人。")
        self.check_binding_current(binding)
        document = self.store.read(identity)
        if document["semanticRevision"] != binding["semanticRevision"]:
            _reject("SEMANTIC_REVISION_CONFLICT", "语义层已变化，请重新规划。")
        annotation = next((item for item in document["annotations"]
                           if item["id"] == binding["annotationId"]), None)
        if annotation is None or annotation.get("ambiguous") or annotation["confidence"] < .5:
            _reject("SEMANTIC_AMBIGUOUS", "语义目标缺失、存在冲突或置信度不足。")
        if binding["verificationMode"] == "object" and annotation["kind"] != "object":
            _reject("SEMANTIC_VERIFICATION_UNSUPPORTED", "物体确认模式不能用于区域标注。")
        return binding

    def check_binding_current(self, binding):
        """Bounded active-motion guard; full package/chain checks run at admission and completion."""
        if not self.execution_binding_supported:
            _reject("SEMANTIC_EXECUTION_UNSUPPORTED", "驱动的语义执行支持已撤销。")
        self._check_current(binding["identity"])
        with self.workflow._lock:
            if (self.workflow.world_frame_revision != binding["worldFrameRevision"]
                    or [float(value) for value in self.workflow.map_from_world] != binding["mapFromWorld"]):
                _reject("WORLD_FRAME_REVISION_CONFLICT", "定位坐标系或地图锚定变换已变化，请重新规划。")
            if float(self.workflow.footprint_radius) != binding["footprintRadiusM"]:
                _reject("ROBOT_FOOTPRINT_CHANGED", "机器人足迹已变化，请重新规划。")
        if self.store.head_revision(binding["identity"]) != binding["semanticRevision"]:
            _reject("SEMANTIC_REVISION_CONFLICT", "语义层已变化，请重新规划。")
        age = _now() - binding["targetObservedAtUnixMs"]
        stale = age < 0 or (binding["targetKind"] == "object" and age > DEFAULT_OBJECT_MAX_AGE_MS)
        if age < 0 or (stale and not binding["allowStaleSearch"]):
            _reject("SEMANTIC_STALE", "标注观测已过期；仅显式搜索旧位置才可继续。")

    @property
    def store(self):
        if self._store is None:
            self._store = SemanticCloudStore(self.workflow.root / "semantic-cloud.sqlite")
        return self._store

    def _map(self, parameters):
        """Snapshot identity briefly; hashing and point processing never hold motion locks."""
        workflow = self.workflow
        workflow._refresh_semantics()
        with workflow._lock:
            if not workflow.active or workflow.grid is None:
                _reject("MAP_REQUIRED", "请先启用并定位到一张有效 SLAM 地图。")
            active = copy.deepcopy(workflow.active)
        if parameters.get("mapRevision", active["mapRevision"]) != active["mapRevision"]:
            _reject("MAP_REVISION_CONFLICT", "地图已变化，请重新读取点云和标注。")
        directory, manifest = MapCatalog(workflow.root).open(active["mapId"],
            robot_id=workflow.robot_id, calibration_revision=active["calibrationRevision"],
            map_revision=active["mapRevision"])
        identity = {**active, "robotId": workflow.robot_id, "frameId": "map",
                    "cloudSha256": manifest["artifacts"]["cloud"]["sha256"]}
        self._check_current(identity)
        return directory, manifest, identity

    def _check_current(self, identity):
        self.workflow._refresh_semantics()
        with self.workflow._lock:
            if (not self.workflow.active or any(self.workflow.active.get(key) != identity[key]
                    for key in ("mapId", "mapRevision", "calibrationRevision"))):
                _reject("MAP_REVISION_CONFLICT", "请求期间地图或标定已变化，请重新读取。")

    @staticmethod
    def _summary(document, manifest):
        identity = document["identity"]
        annotations = []
        now = _now()
        for offset in range(0, max(1, len(document["annotations"])), 50):
            annotations.extend(query_document(document, {"limit": 50, "offset": offset,
                "includeStale": True, "semanticRevision": document["semanticRevision"]},
                now_unix_ms=now)["results"])
        coverage = document["coverage"]
        if not document["parentRevision"]:
            coverage = {"cloudPointCount": manifest["pointCount"], "annotatedPointCount": 0,
                        "unannotatedPointCount": manifest["pointCount"], "fraction": 0.,
                        "basis": "canonical_cloud_point_union"}
        return {**document, "schemaVersion": "semantic.cloud.overview.v1", "annotations": annotations,
                "coverage": coverage,
                "mapCreatedAtUnixMs": manifest["createdAtUnixMs"],
                "measuredPointCount": manifest["pointCount"], "bounds": manifest["bounds"],
                "activeMap": {key: identity[key] for key in
                    ("mapId", "mapRevision", "calibrationRevision")},
                "frameId": "map", "cloudSha256": identity["cloudSha256"],
                "annotationCount": len(annotations),
                "conflictCount": len(document["conflicts"]),
                "categories": [{"category": key, "count": count} for key, count in
                               sorted(Counter(a["category"] for a in annotations).items())[:8]],
                "categoryCount": len({a["category"] for a in annotations}),
                "categoriesTruncated": len({a["category"] for a in annotations}) > 8,
                "requiresReobservation": True, "executionAuthorized": False}

    def overview(self, parameters):
        _, manifest, identity = self._map(parameters)
        result = self._summary(self.store.read(identity), manifest)
        self._check_current(identity)
        return result

    def query(self, parameters):
        _, _, identity = self._map(parameters)
        filters = {key: value for key, value in parameters.items() if key != "mapRevision"}
        # Keep the default below the GOAL model's 8 KiB semantic result budget.
        filters.setdefault("limit", 4)
        result = query_document(self.store.read(identity), filters, now_unix_ms=_now())
        self._check_current(identity)
        output = {**result, "activeMap": {key: identity[key] for key in
                    ("mapId", "mapRevision", "calibrationRevision")},
                "frameId": "map", "requiresReobservation": True}
        # Aliases are lookup inputs; the full editable record remains in overview.
        for item in output["results"]:
            item.pop("aliases", None)
        # Bound the actual serialized response, not just its row count. A caller
        # uses nextOffset with semanticRevision for remaining rows.
        while _view_bytes(output) > 7000 and len(output["results"]) > 1:
            output["results"].pop()
        if _view_bytes(output) > 7000:
            _reject("SEMANTIC_QUERY_BUDGET", "单个标注超过模型查询预算，请缩短标注描述。")
        offset = int(output["offset"])
        output["nextOffset"] = offset + len(output["results"]) if offset + len(output["results"]) < output["total"] else None
        return output

    def annotate(self, parameters):
        directory, manifest, identity = self._map(parameters)
        entry = manifest["artifacts"]["cloud"]
        if entry["bytes"] > 32_000_000 or manifest["pointCount"] > 2_000_000:
            _reject("SEMANTIC_CLOUD_BUDGET", "点云超过标注预算，请创建有界子地图。")
        with (directory / entry["href"]).open("rb") as source:
            payload = source.read(entry["bytes"] + 1)
        if len(payload) != entry["bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            _reject("SEMANTIC_CLOUD_INTEGRITY", "所读取的点云字节与当前地图清单不一致。")
        cloud, _ = decode_lod(payload)
        now = _now()
        compiled = compile_annotations(cloud.xyz, parameters["annotations"], now_unix_ms=now)
        self._check_current(identity)
        document = self.store.replace(identity, parameters["expectedSemanticRevision"],
                                      compiled, now_unix_ms=now)
        # If activation races this commit, the revision remains historical under
        # its original identity; it is never attached to the new active map.
        result = self._summary(document, manifest)
        try:
            self._check_current(identity)
        except ServiceError as error:
            if error.code != "MAP_REVISION_CONFLICT":
                raise
            result["activeMapChanged"] = True
        else:
            result["activeMapChanged"] = False
        # A committed historical update must never be reported as no-side-effect
        # REJECTED simply because activation raced the commit.
        return {**result, "committed": True}

    def history(self, parameters):
        _, _, identity = self._map(parameters)
        result = {"identity": identity, "revisions": self.store.history(
            identity, limit=int(parameters.get("limit", 20)))}
        result["truncated"] = False
        while _view_bytes(result) > 7000 and result["revisions"]:
            result["revisions"].pop()
            result["truncated"] = True
        self._check_current(identity)
        return result

    def plan(self, parameters):
        _, _, identity = self._map(parameters)
        document = self.store.read(identity)
        if parameters["semanticRevision"] != document["semanticRevision"]:
            _reject("SEMANTIC_REVISION_CONFLICT", "语义标注已变化，请重新查询。")
        annotation = next((item for item in document["annotations"]
                           if item["id"] == parameters["annotationId"]), None)
        if annotation is None:
            _reject("SEMANTIC_NOT_FOUND", "没有此点云标注。")
        allow_stale_search = parameters.get("allowStaleSearch", False)
        verification_mode = parameters.get("verificationMode", "arrival")
        target_fresh = _target_fresh(annotation, now_unix_ms=_now(), allow_stale_search=allow_stale_search)
        if verification_mode == "object" and annotation["kind"] != "object":
            _reject("SEMANTIC_VERIFICATION_UNSUPPORTED", "物体确认模式不能用于区域标注。")
        if verification_mode == "object" and not callable(self.workflow.entity_source):
            _reject("SEMANTIC_PERCEPTION_UNAVAILABLE", "当前机器人未接入物体感知源。")
        if annotation.get("ambiguous") or annotation["confidence"] < .5:
            _reject("SEMANTIC_AMBIGUOUS", "标注有重叠冲突或置信度不足，请先复核。")
        status = self.workflow.navigation_map()
        if (status.get("ready") is not True or status.get("mapRevision") != identity["mapRevision"]
                or status.get("localizationState") != "localized"):
            _reject("LOCALIZATION_REQUIRED", "需要当前地图的新鲜定位。")
        self._check_current(identity)
        with self.workflow._lock:
            if self.workflow.active is None or self.workflow.active["mapRevision"] != identity["mapRevision"]:
                _reject("MAP_REVISION_CONFLICT", "规划期间地图已变化。")
            grid = self.workflow.grid
            anchor = self.workflow.map_from_world.copy()
            world_frame_revision = self.workflow.world_frame_revision
            footprint_radius = self.workflow.footprint_radius
        planner = GridPathPlanner(grid, radius=footprint_radius)
        result = observation_candidates(grid, status["mapPose"], annotation,
            radius=footprint_radius,
            max_distance=float(parameters.get("maxDistanceM", 2.5)), planner=planner, include_paths=True)
        self._check_current(identity)
        if self.store.read(identity)["semanticRevision"] != document["semanticRevision"]:
            _reject("SEMANTIC_REVISION_CONFLICT", "规划期间语义标注已变化。")
        if (self.workflow.world_frame_revision != world_frame_revision
                or not np.array_equal(self.workflow.map_from_world, anchor)):
            _reject("WORLD_FRAME_REVISION_CONFLICT", "规划期间定位坐标系或地图锚定变换已变化。")
        if self.workflow.footprint_radius != footprint_radius:
            _reject("ROBOT_FOOTPRINT_CHANGED", "规划期间机器人足迹已变化。")
        # Geometry computation may outlive the first capture. Obtain actual new
        # localization, never relabel the old stamp. Certify a short connector
        # from that measured start to the exact start used by every search.
        refreshed = self.workflow.navigation_map()
        if (refreshed.get("ready") is not True or refreshed.get("mapRevision") != identity["mapRevision"]
                or refreshed.get("localizationState") != "localized"):
            _reject("LOCALIZATION_REQUIRED", "规划完成后需要当前地图的新鲜定位。")
        if (math.dist(refreshed["mapPose"][:2], status["mapPose"][:2]) > .05
                or not planner.segment_clear(refreshed["mapPose"][:2], status["mapPose"][:2])):
            _reject("LOCALIZATION_CHANGED_DURING_PLAN", "规划期间位置改变或连接不再有测量支持，请重新规划。")
        initial_start = status["mapPose"][:2]
        status = refreshed
        self._check_current(identity)
        if (self.store.head_revision(identity) != document["semanticRevision"]
                or self.workflow.world_frame_revision != world_frame_revision
                or not np.array_equal(self.workflow.map_from_world, anchor)
                or self.workflow.footprint_radius != footprint_radius):
            _reject("SEMANTIC_PLAN_CHANGED", "规划完成后的地图锚定或语义身份已变化。")
        inverse = relative(anchor, np.zeros(3))
        for candidate in result:
            path = candidate.pop("_path")
            if math.dist(status["mapPose"][:2], initial_start) > 1e-12:
                path.insert(0, list(status["mapPose"][:2]))
            candidate.update(path_measurements(path))
            pose = candidate.pop("mapPose")
            world = compose(inverse, [pose[0], pose[1], pose[2]])
            candidate["goalPose"] = [float(world[0]), float(world[1]), status["mapPose"][2],
                                     math.cos(world[2]/2), 0., 0., math.sin(world[2]/2)]
            if self.execution_binding_supported:
                candidate["semanticBinding"] = self._sign_binding(identity, document, annotation, candidate,
                    verification_mode=verification_mode, allow_stale_search=allow_stale_search,
                    world_frame_revision=world_frame_revision, footprint_radius=footprint_radius, map_from_world=anchor)
        # A result is a proposal bound to both revisions, not an execution capability.
        output = {"activeMap": {key: identity[key] for key in
                    ("mapId", "mapRevision", "calibrationRevision")},
                "identity": identity, "executionBindingSupported": self.execution_binding_supported,
                "targetFresh": target_fresh, "verificationMode": verification_mode,
                "frameId": "world", "semanticRevision": document["semanticRevision"],
                "annotationId": annotation["id"], "candidates": result,
                "worldFrameRevision": world_frame_revision,
                "poseObservedAtUnixMs": status["poseObservedAtUnixMs"],
                "executionAuthorized": False, "requiresReobservation": True,
                "requiresFreshLocalization": True, "requiresDynamicObstacleChecking": True,
                "visibilityVerified": False, "manipulationReachVerified": False}
        while _view_bytes(output) > 7000 and len(result) > 1:
            result.pop()
        if _view_bytes(output) > 7000:
            _reject("SEMANTIC_PLAN_BUDGET", "语义规划超过有界结果大小。")
        response_now = _now()
        output["targetFresh"] = _target_fresh(annotation, now_unix_ms=response_now,
                                               allow_stale_search=allow_stale_search)
        if not 0 <= response_now - status["poseObservedAtUnixMs"] <= 1000:
            _reject("LOCALIZATION_REQUIRED", "规划响应前定位已过期，请重新获取观测。")
        return output

    def register(self, registry):
        revision = {"type": "string", "minLength": 64, "maxLength": 64}
        text = {"type": "string", "minLength": 1, "maxLength": 128}
        query_fields = {"mapRevision": revision, "semanticRevision": revision,
            "name": text, "category": text, "regionId": text,
            "kind": {"type": "string", "enum": ["object", "region"]},
            "includeStale": {"type": "boolean"},
            "maxAgeMs": {"type": "integer", "minimum": 0, "maximum": 86400000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "offset": {"type": "integer", "minimum": 0, "maximum": 128}}
        base = ["identity", "activeMap", "frameId", "semanticRevision", "requiresReobservation"]
        definitions = [
            ("semantic.map", "读取绑定当前 SLAM 点云的语义层摘要；标签是带来源的声明，不是实时识别结果。", self.overview,
             object_schema({"mapRevision": revision}), base + ["cloudSha256", "annotationCount", "measuredPointCount", "coverage", "conflictCount", "categories", "categoryCount", "categoriesTruncated"]),
            ("semantic.query", "按名称、类别、区域检索三维点云标注；默认隐藏过期物体。分页必须固定 semanticRevision。", self.query,
             object_schema(query_fields), base + ["results", "total", "nextOffset"]),
            ("semantic.history", "读取当前地图语义层的追加版本历史。", self.history,
             object_schema({"mapRevision": revision, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}), ["identity", "revisions", "truncated"]),
            ("semantic.plan", "为指定点云标注提出已测栅格支持的观察位置候选；不移动，不证明视线或机械臂可达性。", self.plan,
             object_schema({"mapRevision": revision, "semanticRevision": revision, "annotationId": text,
                            "maxDistanceM": {"type": "number", "minimum": .5, "maximum": 5},
                            "allowStaleSearch": {"type": "boolean"},
                            "verificationMode": {"type": "string", "enum": ["arrival", "object"]}},
                           ["mapRevision", "semanticRevision", "annotationId"]),
             base + ["annotationId", "candidates", "executionAuthorized", "requiresFreshLocalization",
                     "requiresDynamicObstacleChecking", "visibilityVerified", "manipulationReachVerified",
                     "worldFrameRevision", "poseObservedAtUnixMs", "executionBindingSupported",
                     "targetFresh", "verificationMode"]),
        ]
        for name, description, handler, schema, fields in definitions:
            registry.register(RegisteredService(name, description, schema, handler, False,
                {"version": "1", "effects": ["READ"], "resources": [],
                 "outputSchema": {"type": "object", "additionalProperties": True}, "planningFields": fields}))
        registry.register(RegisteredService("semantic.annotate",
            "提交整份三维点云标注集合；必须显式提供当前地图和语义版本，冲突时重新读取，不能自动覆盖。不会移动机器人。",
            object_schema({"mapRevision": revision, "expectedSemanticRevision": revision,
                           "annotations": {"type": "array", "maxItems": 128, "items": annotation_schema()}},
                          ["mapRevision", "expectedSemanticRevision", "annotations"]),
            self.annotate, True, {"version": "1", "effects": ["CONFIG_CHANGE", "ARTIFACT_WRITE"],
                "resources": ["robot"], "outputSchema": {"type": "object", "additionalProperties": True},
                "verification": {"service": "semantic.map", "required": ["semanticRevision"],
                                 "match": {"semanticRevision": "result.semanticRevision"}}}))
        from .semantic_cloud_observation import register_observation_service
        register_observation_service(self, registry)


def path_measurements(path):
    payload = json.dumps(path, separators=(",", ":"), allow_nan=False).encode()
    return {"pathLengthM": sum(math.dist(a, b) for a, b in itertools.pairwise(path)),
            "pathSha256": hashlib.sha256(payload).hexdigest()}


def observation_candidates(grid, start_pose, annotation, *, radius, max_distance=2.5,
                           planner=None, include_paths=False):
    """Bounded sampled candidates with complete swept-disk path admission.

    This is intentionally not an exhaustive search. A missing candidate means no
    sampled certified approach, not a proof that the object is unreachable.
    """
    target = annotation["centroid"]
    if not .5 <= max_distance <= 5 or not math.isfinite(max_distance):
        _reject("INVALID_DISTANCE", "观察距离必须在 0.5 到 5 米之间。")
    planner = GridPathPlanner(grid, radius=radius) if planner is None else planner
    if planner.radius != radius:
        _reject("ROBOT_FOOTPRINT_CHANGED", "规划上下文与机器人足迹不一致。")
    start = list(start_pose[:2])
    if not planner.point_clear(start):
        _reject("LOCALIZATION_NOT_CLEAR", "当前位置不在已测足迹净空内。")
    distance_min = radius + .2
    proposals = []
    if annotation["kind"] == "region":
        proposals.append((target[0], target[1]))
    for distance in (np.linspace(distance_min, max_distance, 4)
                     if distance_min <= max_distance else []):
        for angle in np.linspace(0., 2*math.pi, 16, endpoint=False):
            proposals.append((target[0]+float(distance*math.cos(angle)),
                              target[1]+float(distance*math.sin(angle))))
    proposals.sort(key=lambda p: math.dist(start, p))
    candidates, attempts = [], 0
    for x, y in proposals:
        if not planner.point_clear([x, y]):
            continue
        attempts += 1
        try:
            path = planner.path(start, [x, y])
        except ServiceError as error:
            if error.code not in {"NO_KNOWN_PATH", "GOAL_NOT_CLEAR", "PATH_CLEARANCE_INSUFFICIENT"}:
                raise
        else:
            candidates.append({"mapPose": [x, y, math.atan2(target[1]-y, target[0]-x)],
                **path_measurements(path), **({"_path": path} if include_paths else {}),
                "targetDistanceM": math.dist([x, y], target[:2]),
                "footprintRadiusM": float(radius), "geometryVerified": True})
        if len(candidates) >= 4 or attempts >= 12:
            break
    return candidates
