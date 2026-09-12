"""``map.manifest.v1``: what a built map is, and how to prove it is intact.

A dense map is not one file. It is a point cloud with a level-of-detail tree, an
occupancy image, a trajectory, and whatever else a renderer needs later. The
manifest is the one document that says which of those exist, in which frame, over
which extent, and what each file's content hash is.

Two conventions are inherited deliberately:

* **Content hashes**, the same idea the evidence store uses. A client can fetch a
  chunk and prove it belongs to this map rather than to a rebuild.
* **The calibration revision** that produced the map. Task evidence records the
  calibration it ran under, so a map and the evidence drawn on it can be checked
  against each other instead of assumed to match.

``mapId`` doubles as a directory name and a URL path segment, so it is validated as
one. A manifest that can address ``../`` is a path traversal, not a map.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "map.manifest.v1"
MANIFEST_FILENAME = "manifest.json"

SOURCES = ("rtabmap", "gazebo", "import")
MODES = ("mapping", "localization")
#: Roles a renderer can ask for. ``cloud`` and ``grid`` are required: a map with
#: no geometry or no occupancy is not a map.
REQUIRED_ARTIFACTS = ("cloud", "grid")
OPTIONAL_ARTIFACTS = ("trajectory", "robot", "mesh")

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
#: Safe as a directory name and as one URL path segment.
_MAP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class MapManifestError(ValueError):
    """Raised with a machine-readable code and a sentence a person can act on."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> None:
    raise MapManifestError(code, message)


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("INVALID_TYPE", f"{where} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], where: str, *,
                optional: set[str] | None = None) -> None:
    optional = optional or set()
    required = expected - optional
    missing = sorted(required - set(value))
    if missing:
        _fail("MISSING_FIELD", f"{where} is missing {', '.join(missing)}")
    unknown = sorted(set(value) - expected)
    if unknown:
        _fail("UNKNOWN_FIELD", f"{where} has unknown field(s) {', '.join(unknown)}")


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("INVALID_TYPE", f"{where} must be a non-empty string")
    return value


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("INVALID_TYPE", f"{where} must be a number")
    number = float(value)
    if not math.isfinite(number):
        _fail("INVALID_VALUE", f"{where} must be finite")
    return number


def _integer(value: Any, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("INVALID_TYPE", f"{where} must be an integer")
    if value < minimum:
        _fail("OUT_OF_RANGE", f"{where} must be >= {minimum}")
    return value


def _vector3(value: Any, where: str) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        _fail("INVALID_TYPE", f"{where} must be a list of 3 numbers")
    return [_number(item, f"{where}[{index}]") for index, item in enumerate(value)]


def _safe_href(value: Any, where: str) -> str:
    """A relative path inside the map directory, and nothing else."""
    href = _text(value, where)
    if href.startswith(("/", "\\")) or ":" in href.split("/")[0]:
        _fail("INVALID_HREF", f"{where} must be relative to the map directory")
    parts = href.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        _fail("INVALID_HREF", f"{where} must not contain empty, '.' or '..' segments")
    return href


@dataclass(frozen=True)
class ArtifactCheck:
    role: str
    href: str
    ok: bool
    reason: str


def build_manifest(
    *,
    map_id: str,
    robot_id: str,
    source: str,
    mode: str,
    artifacts: Mapping[str, Mapping[str, Any]],
    bounds: Mapping[str, Any],
    point_count: int,
    lod_levels: int,
    floors: Sequence[Mapping[str, Any]],
    frame_id: str = "map",
    created_at_unix_ms: int = 0,
    calibration_revision: str | None = None,
) -> dict[str, Any]:
    """Assemble and validate a manifest, computing its content hash."""
    document: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "mapId": map_id,
        "robotId": robot_id,
        "frameId": frame_id,
        "createdAtUnixMs": int(created_at_unix_ms),
        "source": source,
        "mode": mode,
        "floors": [dict(floor) for floor in floors],
        "bounds": dict(bounds),
        "pointCount": int(point_count),
        "lodLevels": int(lod_levels),
        "artifacts": {role: dict(entry) for role, entry in artifacts.items()},
        "calibrationRevision": calibration_revision or "",
    }
    return validate_manifest(document, require_hash=False)


def validate_manifest(document: Any, *, require_hash: bool = True) -> dict[str, Any]:
    """Return a normalized copy, or raise :class:`MapManifestError`."""
    document = _mapping(document, "manifest")
    expected = {"schemaVersion", "mapId", "robotId", "frameId", "createdAtUnixMs", "source",
                "mode", "floors", "bounds", "pointCount", "lodLevels", "artifacts",
                "calibrationRevision", "hash"}
    _exact_keys(document, expected, "manifest", optional={"hash"} if not require_hash else set())
    if document["schemaVersion"] != SCHEMA_VERSION:
        _fail("SCHEMA_MISMATCH", f"schemaVersion must be {SCHEMA_VERSION}")

    map_id = _text(document["mapId"], "mapId")
    if not _MAP_ID.match(map_id):
        _fail("INVALID_MAP_ID",
              "mapId must be a single safe path segment (letters, digits, dot, dash, underscore)")

    source = document["source"]
    if source not in SOURCES:
        _fail("INVALID_VALUE", f"source must be one of {', '.join(SOURCES)}")
    mode = document["mode"]
    if mode not in MODES:
        _fail("INVALID_VALUE", f"mode must be one of {', '.join(MODES)}")

    floors = document["floors"]
    if not isinstance(floors, Sequence) or isinstance(floors, (str, bytes)) or not floors:
        _fail("MISSING_FIELD", "floors must list at least one floor")
    normalized_floors = []
    seen_floor_ids: set[str] = set()
    for index, floor in enumerate(floors):
        where = f"floors[{index}]"
        floor = _mapping(floor, where)
        _exact_keys(floor, {"id", "zMin", "zMax"}, where)
        floor_id = _text(floor["id"], f"{where}.id")
        if floor_id in seen_floor_ids:
            _fail("DUPLICATE_ID", f"{where}.id {floor_id} appears twice")
        seen_floor_ids.add(floor_id)
        low = _number(floor["zMin"], f"{where}.zMin")
        high = _number(floor["zMax"], f"{where}.zMax")
        if low >= high:
            _fail("OUT_OF_RANGE", f"{where}.zMin must be below zMax")
        normalized_floors.append({"id": floor_id, "zMin": low, "zMax": high})

    bounds = _mapping(document["bounds"], "bounds")
    _exact_keys(bounds, {"min", "max"}, "bounds")
    minimum = _vector3(bounds["min"], "bounds.min")
    maximum = _vector3(bounds["max"], "bounds.max")
    for axis, (low, high) in enumerate(zip(minimum, maximum, strict=True)):
        if low >= high:
            _fail("OUT_OF_RANGE", f"bounds.min[{axis}] must be below bounds.max[{axis}]")

    artifacts = _mapping(document["artifacts"], "artifacts")
    unknown_roles = sorted(set(artifacts) - set(REQUIRED_ARTIFACTS) - set(OPTIONAL_ARTIFACTS))
    if unknown_roles:
        _fail("UNKNOWN_FIELD", f"artifacts has unknown role(s) {', '.join(unknown_roles)}")
    missing_roles = sorted(set(REQUIRED_ARTIFACTS) - set(artifacts))
    if missing_roles:
        _fail("MISSING_FIELD", f"artifacts must include {', '.join(missing_roles)}")
    normalized_artifacts: dict[str, dict[str, Any]] = {}
    for role, entry in artifacts.items():
        where = f"artifacts.{role}"
        entry = _mapping(entry, where)
        _exact_keys(entry, {"href", "bytes", "sha256"}, where)
        digest = _text(entry["sha256"], f"{where}.sha256")
        if not _SHA256.match(digest):
            _fail("INVALID_HASH", f"{where}.sha256 must be 64 lowercase hex characters")
        normalized_artifacts[role] = {
            "href": _safe_href(entry["href"], f"{where}.href"),
            "bytes": _integer(entry["bytes"], f"{where}.bytes"),
            "sha256": digest,
        }

    revision = document.get("calibrationRevision") or ""
    if revision and not _SHA256.match(str(revision)):
        _fail("INVALID_HASH", "calibrationRevision must be a 64 character content hash")

    normalized = {
        "schemaVersion": SCHEMA_VERSION,
        "mapId": map_id,
        "robotId": _text(document["robotId"], "robotId"),
        "frameId": _text(document["frameId"], "frameId"),
        "createdAtUnixMs": _integer(document["createdAtUnixMs"], "createdAtUnixMs"),
        "source": source,
        "mode": mode,
        "floors": normalized_floors,
        "bounds": {"min": minimum, "max": maximum},
        "pointCount": _integer(document["pointCount"], "pointCount"),
        "lodLevels": _integer(document["lodLevels"], "lodLevels", minimum=1),
        "artifacts": normalized_artifacts,
        "calibrationRevision": str(revision),
    }
    normalized["hash"] = manifest_hash(normalized)
    if require_hash:
        supplied = document.get("hash")
        if supplied != normalized["hash"]:
            _fail("HASH_MISMATCH",
                  "manifest hash does not match its content; the file was edited or truncated")
    return normalized


def canonical_content(document: Mapping[str, Any]) -> str:
    """Stable JSON of everything except the hash itself."""
    content = {key: value for key, value in document.items() if key != "hash"}
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def manifest_hash(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_content(document).encode("utf-8")).hexdigest()


def file_sha256(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifacts(manifest: Mapping[str, Any], root: str | os.PathLike[str]) -> list[ArtifactCheck]:
    """Check every declared file exists, has the declared size and hash.

    Returns one result per artifact instead of raising, so a caller can report
    "the grid is fine, the cloud is truncated" rather than stopping at the first
    problem.
    """
    directory = Path(root)
    checks: list[ArtifactCheck] = []
    for role, entry in sorted((manifest.get("artifacts") or {}).items()):
        target = directory / entry["href"]
        if not target.is_file():
            checks.append(ArtifactCheck(role, entry["href"], False, "文件不存在"))
            continue
        size = target.stat().st_size
        if size != entry["bytes"]:
            checks.append(ArtifactCheck(role, entry["href"], False,
                                        f"大小不符：期望 {entry['bytes']}，实际 {size}"))
            continue
        digest = file_sha256(target)
        if digest != entry["sha256"]:
            checks.append(ArtifactCheck(role, entry["href"], False, "内容哈希不符"))
            continue
        checks.append(ArtifactCheck(role, entry["href"], True, "ok"))
    return checks


def save_manifest(directory: str | os.PathLike[str], document: Any) -> dict[str, Any]:
    """Validate, then write ``manifest.json`` atomically inside the map directory."""
    normalized = validate_manifest(document)
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    handle, temporary = tempfile.mkstemp(dir=str(target), prefix=".manifest.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target / MANIFEST_FILENAME)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return normalized


def load_manifest(directory: str | os.PathLike[str]) -> dict[str, Any]:
    """Read and verify ``manifest.json``; raises when absent or tampered with."""
    path = Path(directory) / MANIFEST_FILENAME
    if not path.is_file():
        _fail("NOT_FOUND", f"{path} does not exist")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("UNREADABLE", f"cannot read {path}: {exc}")
    return validate_manifest(raw)
