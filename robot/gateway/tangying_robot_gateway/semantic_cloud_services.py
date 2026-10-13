"""Revision-bound point-cloud annotation services; geometry never authorizes motion."""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import time
from collections import Counter

import numpy as np

from .dense_slam import compose, relative
from .grid_navigation import plan_grid_path, pose_is_clear
from .map_catalog import MapCatalog
from .map_pipeline import decode_lod
from .semantic_cloud import SemanticCloudStore, compile_annotations, query_document
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
        current = query_document(document, {"name": annotation["name"], "includeStale": False},
                                 now_unix_ms=_now())
        if not current["results"]:
            _reject("SEMANTIC_STALE", "标注观测已过期，必须重新观察。")
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
        result = observation_candidates(grid, status["mapPose"], annotation,
            radius=self.workflow.footprint_radius,
            max_distance=float(parameters.get("maxDistanceM", 2.5)))
        self._check_current(identity)
        if self.store.read(identity)["semanticRevision"] != document["semanticRevision"]:
            _reject("SEMANTIC_REVISION_CONFLICT", "规划期间语义标注已变化。")
        inverse = relative(anchor, np.zeros(3))
        for candidate in result:
            pose = candidate.pop("mapPose")
            world = compose(inverse, [pose[0], pose[1], pose[2]])
            candidate["goalPose"] = [float(world[0]), float(world[1]), status["mapPose"][2],
                                     math.cos(world[2]/2), 0., 0., math.sin(world[2]/2)]
        # A result is a proposal bound to both revisions, not an execution capability.
        return {"activeMap": {key: identity[key] for key in
                    ("mapId", "mapRevision", "calibrationRevision")},
                "frameId": "world", "semanticRevision": document["semanticRevision"],
                "annotationId": annotation["id"], "candidates": result,
                "worldFrameRevision": self.workflow.world_frame_revision,
                "poseObservedAtUnixMs": status["poseObservedAtUnixMs"],
                "executionAuthorized": False, "requiresReobservation": True,
                "requiresFreshLocalization": True, "requiresDynamicObstacleChecking": True,
                "visibilityVerified": False, "manipulationReachVerified": False}

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
        base = ["activeMap", "frameId", "semanticRevision", "requiresReobservation"]
        definitions = [
            ("semantic.map", "读取绑定当前 SLAM 点云的语义层摘要；标签是带来源的声明，不是实时识别结果。", self.overview,
             object_schema({"mapRevision": revision}), base + ["cloudSha256", "annotationCount", "measuredPointCount", "coverage", "conflictCount", "categories", "categoryCount", "categoriesTruncated"]),
            ("semantic.query", "按名称、类别、区域检索三维点云标注；默认隐藏过期物体。分页必须固定 semanticRevision。", self.query,
             object_schema(query_fields), base + ["results", "total", "nextOffset"]),
            ("semantic.history", "读取当前地图语义层的追加版本历史。", self.history,
             object_schema({"mapRevision": revision, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}), ["identity", "revisions", "truncated"]),
            ("semantic.plan", "为指定点云标注提出已测栅格支持的观察位置候选；不移动，不证明视线或机械臂可达性。", self.plan,
             object_schema({"mapRevision": revision, "semanticRevision": revision, "annotationId": text,
                            "maxDistanceM": {"type": "number", "minimum": .5, "maximum": 5}},
                           ["mapRevision", "semanticRevision", "annotationId"]),
             base + ["annotationId", "candidates", "executionAuthorized", "requiresFreshLocalization",
                     "requiresDynamicObstacleChecking", "visibilityVerified", "manipulationReachVerified",
                     "worldFrameRevision", "poseObservedAtUnixMs"]),
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


def observation_candidates(grid, start_pose, annotation, *, radius, max_distance=2.5):
    """Bounded sampled candidates with complete swept-disk path admission.

    This is intentionally not an exhaustive search. A missing candidate means no
    sampled certified approach, not a proof that the object is unreachable.
    """
    target = annotation["centroid"]
    if not .5 <= max_distance <= 5 or not math.isfinite(max_distance):
        _reject("INVALID_DISTANCE", "观察距离必须在 0.5 到 5 米之间。")
    start = list(start_pose[:2])
    if not pose_is_clear(grid, start, radius):
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
        if not pose_is_clear(grid, [x, y], radius):
            continue
        attempts += 1
        try:
            path = plan_grid_path(grid, start, [x, y], radius=radius)
        except ServiceError as error:
            if error.code not in {"NO_KNOWN_PATH", "GOAL_NOT_CLEAR", "PATH_CLEARANCE_INSUFFICIENT"}:
                raise
        else:
            payload = json.dumps(path, separators=(",", ":"), allow_nan=False).encode()
            candidates.append({"mapPose": [x, y, math.atan2(target[1]-y, target[0]-x)],
                "pathLengthM": sum(math.dist(a, b) for a, b in itertools.pairwise(path)),
                "pathSha256": hashlib.sha256(payload).hexdigest(),
                "targetDistanceM": math.dist([x, y], target[:2]),
                "footprintRadiusM": float(radius), "geometryVerified": True})
        if len(candidates) >= 4 or attempts >= 12:
            break
    return candidates
