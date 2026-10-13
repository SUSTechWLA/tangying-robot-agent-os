"""Versioned annotations grounded in one immutable, verified SLAM point cloud.

Labels are operator or perception assertions, not geometry-derived recognition.
Neither a centroid nor a relation is an executable navigation or grasp target.
The caller verifies the canonical cloud artifact and supplies its full identity;
this module never changes geometric maps or silently transfers labels to a rebuild.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from .semantic_map import normalize_location_name
from .service_registry import ServiceError

MAX_ANNOTATIONS = 128
MAX_POINTS = 2_000_000
MAX_DOCUMENT_BYTES = 2_000_000
MAX_REVISIONS = 512
MAX_HISTORY_BYTES = 64 * 1024 * 1024
DEFAULT_OBJECT_MAX_AGE_MS = 900_000
SCHEMA_VERSION = "semantic.cloud.v1"
_IDENTITY_FIELDS = {"robotId", "mapId", "mapRevision", "calibrationRevision", "cloudSha256", "frameId"}
_COMPILED_FIELDS = {"annotations", "relations", "conflicts", "coverage"}
_HASH = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BITS = np.array([int(value).bit_count() for value in range(256)], dtype=np.uint8)


def _fail(message, code="INVALID_SEMANTIC_ANNOTATION"):
    raise ServiceError(code, message, rejected=True)


def _keys(value, required, optional=()):
    if not isinstance(value, Mapping) or set(value) - required - set(optional) or required - set(value):
        _fail("semantic document has missing or unknown fields")


def _text(value, *, maximum=256):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > maximum:
        _fail("semantic strings must be nonempty and within their byte budget")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail("semantic strings cannot contain control characters")
    return value.strip()


def _integer(value, *, minimum=0, maximum=2**53 - 1):
    # Protobuf Struct decodes JSON integer values as floats.
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not minimum <= value <= maximum or not math.isfinite(value) or int(value) != value:
        _fail("semantic integer is outside its accepted range")
    return int(value)


def _number(value, *, minimum=-math.inf, maximum=math.inf):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("semantic numeric values must be finite and within range")
    try:
        number = float(value)
    except OverflowError:
        _fail("semantic numeric values exceed the floating-point range")
    if not math.isfinite(number) or not minimum <= number <= maximum:
        _fail("semantic numeric values must be finite and within range")
    return number


def _vector(value):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        _fail("semantic bounds must contain three coordinates")
    return [_number(component) for component in value]


def _bounds(value, *, measured=False):
    _keys(value, {"min", "max"})
    low, high = _vector(value["min"]), _vector(value["max"])
    if any(a > b if measured else a >= b for a, b in zip(low, high, strict=True)):
        _fail("semantic bounds must have ordered extents")
    return {"min": low, "max": high}


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        _fail(f"semantic document is not canonical JSON: {error}")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _identity(value):
    _keys(value, _IDENTITY_FIELDS)
    result = {key: _text(value[key]) for key in _IDENTITY_FIELDS}
    if result["frameId"] != "map" or not _ID.fullmatch(result["mapId"]):
        _fail("semantic identity requires a safe mapId and the map frame")
    for key in ("mapRevision", "calibrationRevision", "cloudSha256"):
        if not _HASH.fullmatch(result[key]):
            _fail(f"semantic identity {key} must be a SHA-256 digest")
    return result


def _annotation(value, now):
    _keys(value, {"id", "name", "category", "kind", "bounds", "source", "confidence", "observedAtUnixMs"},
          {"aliases"})
    identifier = _text(value["id"], maximum=128)
    if not _ID.fullmatch(identifier):
        _fail("semantic annotation id must be a safe identifier")
    kind = value["kind"]
    if kind not in ("object", "region"):
        _fail("semantic annotation kind must be object or region")
    aliases = value.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > 16:
        _fail("semantic annotation aliases exceed their budget")
    _keys(value["source"], {"kind", "reference"})
    if value["source"]["kind"] not in ("operator", "perception"):
        _fail("semantic source must explicitly name operator or perception")
    observed = _integer(value["observedAtUnixMs"], minimum=1)
    if observed > now:
        _fail("semantic observation cannot be dated in the future")
    return {"id": identifier, "name": _text(value["name"]),
            "category": _text(value["category"]), "kind": kind,
            "aliases": [_text(alias) for alias in aliases], "bounds": _bounds(value["bounds"]),
            "source": {"kind": value["source"]["kind"],
                       "reference": _text(value["source"]["reference"], maximum=1024)},
            "confidence": _number(value["confidence"], minimum=0, maximum=1),
            "observedAtUnixMs": observed}


def compile_annotations(points, annotations, *, now_unix_ms):
    """Measure AABB selections in canonical-cloud order, retaining assertion provenance.

    Bounds are inclusive. Point-index digests hash sorted little-endian uint64
    indices. Coverage is measured point coverage, never whole-world understanding.
    Same-kind overlap remains explicit even when both assertions have high confidence.
    """
    now = _integer(now_unix_ms, minimum=1)
    if not isinstance(points, np.ndarray) or points.ndim != 2 or points.shape[1] != 3 \
            or not 0 < len(points) <= MAX_POINTS or points.dtype.kind not in "fiu" \
            or not np.isfinite(points).all():
        _fail("canonical point cloud must contain 1..2000000 finite XYZ points")
    if not isinstance(annotations, list) or len(annotations) > MAX_ANNOTATIONS:
        _fail("semantic annotation count exceeds 128")
    if len(_canonical(annotations)) > MAX_DOCUMENT_BYTES:
        _fail("semantic annotations exceed the document byte budget")
    result, masks, identifiers, labels = [], [], set(), {}
    union = np.zeros(len(points), dtype=np.bool_)
    for raw in annotations:
        item = _annotation(raw, now)
        if item["id"] in identifiers:
            _fail("semantic annotation ids must be unique")
        identifiers.add(item["id"])
        for label in [item["name"], *item["aliases"]]:
            normalized = normalize_location_name(label)
            if not normalized or normalized in labels:
                _fail("semantic names and aliases must be globally unique after normalization")
            labels[normalized] = item["id"]
        mask = np.all((points >= item["bounds"]["min"]) & (points <= item["bounds"]["max"]), axis=1)
        indices = np.flatnonzero(mask).astype("<u8", copy=False)
        if not len(indices):
            _fail(f"semantic annotation {item['id']} selects no measured cloud points")
        selected = points[mask].astype(np.float64, copy=False)
        item.update(pointCount=len(indices), centroid=selected.mean(axis=0).tolist(),
                    measuredBounds={"min": selected.min(axis=0).tolist(),
                                    "max": selected.max(axis=0).tolist()},
                    pointIndicesSha256=hashlib.sha256(indices.tobytes()).hexdigest(),
                    ambiguous=False, requiresReobservation=True,
                    geometrySource="verified_canonical_slam_cloud")
        if not np.isfinite(item["centroid"]).all():
            _fail("semantic centroid overflowed the supported coordinate range")
        union |= mask
        masks.append(np.packbits(mask))
        result.append(item)
    relations, conflicts = [], []
    for left_index, left in enumerate(result):
        for right_index in range(left_index + 1, len(result)):
            right = result[right_index]
            if any(a > b or c > d for a, b, c, d in zip(
                    left["measuredBounds"]["min"], right["measuredBounds"]["max"],
                    right["measuredBounds"]["min"], left["measuredBounds"]["max"], strict=True)):
                continue
            shared = int(_BITS[np.bitwise_and(masks[left_index], masks[right_index])].sum())
            if not shared:
                continue
            if left["kind"] == right["kind"]:
                left["ambiguous"] = right["ambiguous"] = True
                conflicts.append({"kind": "same_kind_point_overlap", "annotationIds": [left["id"], right["id"]],
                                  "sharedPointCount": shared, "resolution": "requires_review"})
            else:
                obj, region = (left, right) if left["kind"] == "object" else (right, left)
                if shared == obj["pointCount"]:
                    relations.append({"kind": "within", "subjectId": obj["id"], "objectId": region["id"],
                                      "basis": "selected_measured_points", "requiresReobservation": True})
    covered = int(union.sum())
    return {"annotations": result, "relations": relations, "conflicts": conflicts,
            "coverage": {"cloudPointCount": len(points), "annotatedPointCount": covered,
                         "unannotatedPointCount": len(points) - covered,
                         "fraction": covered / len(points), "basis": "canonical_cloud_point_union"}}


def _compiled(value):
    _keys(value, _COMPILED_FIELDS)
    if len(_canonical(value)) > MAX_DOCUMENT_BYTES:
        _fail("compiled semantic layer exceeds the document budget")
    if not isinstance(value["annotations"], list) or len(value["annotations"]) > MAX_ANNOTATIONS:
        _fail("compiled annotation count exceeds its budget")
    if not isinstance(value["relations"], list) or not isinstance(value["conflicts"], list) \
            or len(value["relations"]) > MAX_ANNOTATIONS**2 or len(value["conflicts"]) > MAX_ANNOTATIONS**2:
        _fail("compiled relation/conflict lists exceed their budget")
    _keys(value["coverage"], {"cloudPointCount", "annotatedPointCount", "unannotatedPointCount", "fraction", "basis"})
    cloud = _integer(value["coverage"]["cloudPointCount"], maximum=MAX_POINTS)
    covered = _integer(value["coverage"]["annotatedPointCount"], maximum=cloud)
    uncovered = _integer(value["coverage"]["unannotatedPointCount"], maximum=cloud)
    if covered + uncovered != cloud or value["coverage"]["basis"] != "canonical_cloud_point_union" \
            or _number(value["coverage"]["fraction"], minimum=0, maximum=1) != (covered / cloud if cloud else 0):
        _fail("compiled coverage is inconsistent")
    fields = {"id", "name", "category", "kind", "aliases", "bounds", "source", "confidence", "observedAtUnixMs"}
    enriched = {"pointCount", "centroid", "measuredBounds", "pointIndicesSha256", "ambiguous",
                "requiresReobservation", "geometrySource"}
    ids, labels = set(), set()
    for item in value["annotations"]:
        _keys(item, fields | enriched)
        base = _annotation({key: item[key] for key in fields}, 2**53 - 1)
        names = [normalize_location_name(label) for label in [base["name"], *base["aliases"]]]
        if base["id"] in ids or len(set(names)) != len(names) or any(name in labels for name in names):
            _fail("compiled annotation identities are ambiguous")
        ids.add(base["id"])
        labels.update(names)
        _integer(item["pointCount"], minimum=1, maximum=cloud)
        centroid = _vector(item["centroid"])
        bounds = _bounds(item["measuredBounds"], measured=True)
        if any(not low <= center <= high for low, center, high in zip(bounds["min"], centroid, bounds["max"], strict=True)):
            _fail("compiled centroid is outside its measured bounds")
        if not isinstance(item["pointIndicesSha256"], str) or not _HASH.fullmatch(item["pointIndicesSha256"]) \
                or type(item["ambiguous"]) is not bool or item["requiresReobservation"] is not True \
                or item["geometrySource"] != "verified_canonical_slam_cloud":
            _fail("compiled annotation evidence is invalid")
    for relation in value["relations"]:
        _keys(relation, {"kind", "subjectId", "objectId", "basis", "requiresReobservation"})
        if relation["kind"] != "within" or relation["subjectId"] not in ids or relation["objectId"] not in ids \
                or relation["subjectId"] == relation["objectId"] or relation["basis"] != "selected_measured_points" \
                or relation["requiresReobservation"] is not True:
            _fail("compiled relation is invalid")
    for conflict in value["conflicts"]:
        _keys(conflict, {"kind", "annotationIds", "sharedPointCount", "resolution"})
        if conflict["kind"] != "same_kind_point_overlap" or conflict["resolution"] != "requires_review" \
                or not isinstance(conflict["annotationIds"], list) or len(conflict["annotationIds"]) != 2 \
                or any(identifier not in ids for identifier in conflict["annotationIds"]) \
                or conflict["annotationIds"][0] == conflict["annotationIds"][1]:
            _fail("compiled conflict is invalid")
        _integer(conflict["sharedPointCount"], minimum=1, maximum=cloud)
    return copy.deepcopy(value)


def _empty(identity):
    document = {"schemaVersion": SCHEMA_VERSION, "identity": identity, "parentRevision": "",
                "updatedAtUnixMs": 0, "annotations": [], "relations": [], "conflicts": [],
                "coverage": {"cloudPointCount": 0, "annotatedPointCount": 0, "unannotatedPointCount": 0,
                             "fraction": 0, "basis": "canonical_cloud_point_union"}}
    return {**document, "semanticRevision": _digest(document)}


def _verify_document(document, identity):
    _keys(document, {"schemaVersion", "identity", "parentRevision", "updatedAtUnixMs", "semanticRevision"} | _COMPILED_FIELDS)
    if document["schemaVersion"] != SCHEMA_VERSION or _identity(document["identity"]) != identity:
        _fail("stored semantic identity mismatch", "SEMANTIC_INTEGRITY_ERROR")
    content = {key: value for key, value in document.items() if key != "semanticRevision"}
    if document["semanticRevision"] != _digest(content):
        _fail("stored semantic revision hash mismatch", "SEMANTIC_INTEGRITY_ERROR")
    _integer(document["updatedAtUnixMs"])
    _compiled({key: document[key] for key in _COMPILED_FIELDS})
    if document["parentRevision"] != "" and (not isinstance(document["parentRevision"], str)
                                                or not _HASH.fullmatch(document["parentRevision"])):
        _fail("stored semantic parent revision is invalid", "SEMANTIC_INTEGRITY_ERROR")
    if any(item["observedAtUnixMs"] > document["updatedAtUnixMs"] for item in document["annotations"]):
        _fail("stored observation is newer than its revision", "SEMANTIC_INTEGRITY_ERROR")
    return document


class SemanticCloudStore:
    """Append-only revisions; SQLite serializes writers across threads and processes."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS semantic_cloud_revisions (
                identity_hash TEXT NOT NULL, sequence INTEGER NOT NULL, revision TEXT NOT NULL UNIQUE,
                document TEXT NOT NULL, PRIMARY KEY(identity_hash, sequence))""")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _budget(connection, identity):
        # Inspect bounded integer lengths before transferring any document bytes.
        lengths = [row[0] for row in connection.execute(
            "SELECT length(CAST(document AS BLOB)) FROM semantic_cloud_revisions "
            "WHERE identity_hash=? ORDER BY sequence LIMIT ?", (_digest(identity), MAX_REVISIONS + 1))]
        if any(length > MAX_DOCUMENT_BYTES for length in lengths):
            _fail("stored semantic document exceeds budget", "SEMANTIC_INTEGRITY_ERROR")
        if len(lengths) > MAX_REVISIONS or sum(lengths) > MAX_HISTORY_BYTES:
            _fail("semantic history capacity exceeded; export the existing history for archival "
                  "and commission a new map identity before further updates", "SEMANTIC_HISTORY_CAPACITY")
        return len(lengths), sum(lengths)

    @staticmethod
    def _read(connection, identity):
        SemanticCloudStore._budget(connection, identity)
        previous = _empty(identity)
        rows = connection.execute("SELECT sequence,revision,document FROM semantic_cloud_revisions "
                                  "WHERE identity_hash=? ORDER BY sequence", (_digest(identity),))
        for index, (sequence, revision, raw) in enumerate(rows, start=1):
            try:
                if len(raw.encode()) > MAX_DOCUMENT_BYTES:
                    _fail("stored semantic document exceeds budget")
                document = _verify_document(json.loads(raw), identity)
                if sequence != index or revision != document["semanticRevision"] \
                        or document["parentRevision"] != previous["semanticRevision"] \
                        or document["updatedAtUnixMs"] < previous["updatedAtUnixMs"]:
                    _fail("stored semantic revision chain is invalid")
            except (ServiceError, TypeError, ValueError, KeyError) as error:
                _fail(str(error), "SEMANTIC_INTEGRITY_ERROR")
            previous = document
        return previous

    def read(self, identity):
        identity = _identity(identity)
        with self._connect() as connection:
            connection.execute("BEGIN")
            return self._read(connection, identity)

    def replace(self, identity, expected_revision, compiled, *, now_unix_ms):
        identity = _identity(identity)
        compiled = _compiled(compiled)
        now = _integer(now_unix_ms, minimum=1)
        if not isinstance(expected_revision, str) or not _HASH.fullmatch(expected_revision):
            _fail("replace requires an explicit semantic revision", "SEMANTIC_REVISION_CONFLICT")
        if any(item["observedAtUnixMs"] > now for item in compiled["annotations"]):
            _fail("semantic observation cannot be dated after its revision")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = self._read(connection, identity)
            if previous["semanticRevision"] != expected_revision:
                _fail("semantic layer changed; read it again before replacing", "SEMANTIC_REVISION_CONFLICT")
            if now < previous["updatedAtUnixMs"]:
                _fail("semantic update clock moved backwards")
            document = {"schemaVersion": SCHEMA_VERSION, "identity": identity,
                        "parentRevision": expected_revision, "updatedAtUnixMs": now, **compiled}
            document["semanticRevision"] = _digest(document)
            raw = _canonical(document)
            if len(raw) > MAX_DOCUMENT_BYTES:
                _fail("semantic revision exceeds the document byte budget")
            count, total_bytes = self._budget(connection, identity)
            if count >= MAX_REVISIONS or total_bytes + len(raw) > MAX_HISTORY_BYTES:
                _fail("semantic history is full; export it for archival and commission a new map "
                      "identity before further updates", "SEMANTIC_HISTORY_CAPACITY")
            sequence = count + 1
            connection.execute("INSERT INTO semantic_cloud_revisions VALUES (?,?,?,?)",
                               (_digest(identity), sequence, document["semanticRevision"], raw.decode()))
        return document

    def history(self, identity, limit=20):
        identity = _identity(identity)
        limit = _integer(limit, minimum=1, maximum=100)
        with self._connect() as connection:
            connection.execute("BEGIN")
            self._read(connection, identity)
            rows = connection.execute("SELECT document FROM semantic_cloud_revisions WHERE identity_hash=? "
                                      "ORDER BY sequence DESC LIMIT ?", (_digest(identity), limit)).fetchall()
        return [{"semanticRevision": document["semanticRevision"], "parentRevision": document["parentRevision"],
                 "updatedAtUnixMs": document["updatedAtUnixMs"], "annotationCount": len(document["annotations"]),
                 "conflictCount": len(document["conflicts"])} for (raw,) in rows for document in [json.loads(raw)]]


def query_document(document, filters=None, *, now_unix_ms):
    """Bounded metadata retrieval, with freshness and revision-bound pagination.

    Objects expire after 15 minutes by default. Regions have no default TTL but
    always require reobservation before physical action, as do fresh objects.
    A backwards clock makes a sighting stale instead of silently fresh.
    """
    identity = _identity(document.get("identity"))
    _verify_document(document, identity)
    now = _integer(now_unix_ms, minimum=1)
    filters = {} if filters is None else filters
    _keys(filters, set(), {"name", "category", "kind", "regionId", "includeStale", "maxAgeMs",
                           "limit", "offset", "semanticRevision"})
    limit = _integer(filters.get("limit", 20), minimum=1, maximum=50)
    offset = _integer(filters.get("offset", 0), maximum=MAX_ANNOTATIONS)
    revision = filters.get("semanticRevision")
    if (offset and revision is None) or (revision is not None and revision != document["semanticRevision"]):
        _fail("semantic query page belongs to another revision", "SEMANTIC_REVISION_CONFLICT")
    include_stale = filters.get("includeStale", False)
    if type(include_stale) is not bool:
        _fail("includeStale must be a boolean")
    max_age = _integer(filters["maxAgeMs"]) if "maxAgeMs" in filters else None
    values = {key: _text(filters[key]) for key in ("name", "category", "kind", "regionId") if key in filters}
    if "kind" in values and values["kind"] not in ("object", "region"):
        _fail("query kind must be object or region")
    region = values.get("regionId")
    members = {item["subjectId"] for item in document["relations"] if item["objectId"] == region}
    matched = []
    for original in document["annotations"]:
        if "name" in values and normalize_location_name(values["name"]) not in {
                normalize_location_name(label) for label in [original["name"], *original["aliases"]]}:
            continue
        if any(original[key].casefold() != values[key].casefold() for key in ("category", "kind") if key in values):
            continue
        if region and original["id"] not in members:
            continue
        age = now - original["observedAtUnixMs"]
        ttl = max_age if max_age is not None else (DEFAULT_OBJECT_MAX_AGE_MS if original["kind"] == "object" else None)
        stale = age < 0 or (ttl is not None and age > ttl)
        if stale and not include_stale:
            continue
        item = copy.deepcopy(original)
        item.update(ageMs=max(0, age), stale=stale,
                    freshness="clock_disagreement" if age < 0 else "expired" if stale else "within_policy",
                    requiresReobservation=True)
        matched.append(item)
    page = matched[offset:offset + limit]
    return {"schemaVersion": "semantic.cloud.query.v1", "identity": copy.deepcopy(identity),
            "semanticRevision": document["semanticRevision"], "results": page, "total": len(matched),
            "offset": offset, "limit": limit,
            "nextOffset": offset + limit if offset + limit < len(matched) else None,
            "requiresReobservation": True}
