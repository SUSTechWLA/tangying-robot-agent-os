"""Objects the robot has actually seen, in the map frame, with the age of each sighting.

The semantic layer shipped so far answers "which named places exist" (``map.semantics.v1``)
and "which objects may be operated" (the runtime's action catalogue). Neither
says *where* an object was last seen. That is the gap this module closes: a
mapping session accumulates every entity the perception stack reported, in the
map frame, and republishes them as ``map.objects.v1`` inside the map. A later
task can therefore ask "where was the cup when I last looked" instead of only
"is it in this frame".

Three rules make it evidence rather than folklore:

* **Only observations enter.** An instance exists because a detection was
  reported with a pose, not because the scene commission declares the object.
  The commissioned catalogue still has no measured poses - that stays true.
* **Every sighting carries its time.** Retrieval is by age, and a caller that
  needs something visible now must say so; a stale instance is a hint about
  where to look, never a claim about where the object is.
* **Association is bounded.** Two sightings join the same instance only when the
  category matches, the attributes do not contradict, and the map-frame distance
  is inside a gate derived from the observation itself. A moved object becomes a
  new instance rather than a teleporting one, and the newest sighting wins for
  position while the count keeps the history honest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "map.objects.v1"

#: How far apart two sightings of the same category may be and still be the same
#: object. Rounded to the scale a handheld RGB-D detection reproduces a tabletop
#: object at, measured on the reference workcell (a mug is re-detected within
#: about 2 cm of its previous map position while it is not being moved).
ASSOCIATION_GATE_M = .12
#: Instances that have not been seen for this long are dropped from the published
#: map. A map is a claim about a building, and an unknown-length absence of a
#: movable object is not evidence that it is still there.
MAX_AGE_MS = 24 * 60 * 60 * 1000
#: Bounded output: the map artifact travels to the console and must stay small.
MAX_INSTANCES = 512


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _attributes(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("object attributes must be a mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(item, str) or not item.strip():
            raise ValueError("object attributes must map non-empty strings")
        result[key.strip()] = item.strip()
    return result


@dataclass
class ObjectInstance:
    """One object the robot has seen, at the position of its newest sighting."""

    instance_id: str
    category: str
    attributes: dict[str, str]
    pose: list[float]
    confidence: float
    first_seen_unix_ms: int
    last_seen_unix_ms: int
    sightings: int = 1
    evidence_frame_id: str = ""
    source_id: str = ""
    observation_id: str = ""
    history: list[list[float]] = field(default_factory=list)

    def to_dict(self, *, now_unix_ms: int) -> dict[str, Any]:
        return {
            "id": self.instance_id,
            "category": self.category,
            "attributes": dict(self.attributes),
            "pose": [float(value) for value in self.pose],
            "confidence": float(self.confidence),
            "sightings": int(self.sightings),
            "firstSeenUnixMs": int(self.first_seen_unix_ms),
            "lastSeenUnixMs": int(self.last_seen_unix_ms),
            "ageMs": max(0, int(now_unix_ms) - int(self.last_seen_unix_ms)),
            "evidenceFrameId": self.evidence_frame_id,
            "sourceId": self.source_id,
            "observationId": self.observation_id,
            "history": [[float(value) for value in point] for point in self.history],
        }


class ObjectMemory:
    """Accumulated sightings of movable objects, in the map frame."""

    def __init__(self, *, association_gate_m: float = ASSOCIATION_GATE_M,
                 max_age_ms: int = MAX_AGE_MS, max_instances: int = MAX_INSTANCES) -> None:
        if not _finite(association_gate_m) or not 0 < association_gate_m <= 1.0:
            raise ValueError("association gate must be a positive distance within one metre")
        if not isinstance(max_age_ms, int) or max_age_ms <= 0:
            raise ValueError("max age must be a positive integer of milliseconds")
        if not isinstance(max_instances, int) or max_instances <= 0:
            raise ValueError("instance budget must be a positive integer")
        self.association_gate_m = float(association_gate_m)
        self.max_age_ms = max_age_ms
        self.max_instances = max_instances
        self.instances: list[ObjectInstance] = []
        #: How many times perception was consulted, and how many entity sightings
        #: came back. The two together are what makes the layer readable: "looked
        #: 197 times and saw nothing" is a different statement from "never looked",
        #: and a map that cannot tell them apart cannot be trusted about absence.
        self.polls = 0
        self.sightings = 0

    # -- ingest ---------------------------------------------------------

    def observe(self, entities, *, map_from_world, stamp_unix_ms: int,
                evidence_frame_id: str = "", source_id: str = "") -> int:
        """Add one observation's entities. Returns how many were accepted.

        ``map_from_world`` is the session's map anchor: perception reports poses
        in the driver's world frame, and an object position that is not in the
        map frame would be a coordinate from a different map wearing the map's
        name.
        """
        if not isinstance(stamp_unix_ms, int) or stamp_unix_ms <= 0:
            raise ValueError("observations need a valid capture timestamp")
        anchor = [float(value) for value in map_from_world]
        if len(anchor) != 3 or not all(math.isfinite(value) for value in anchor):
            raise ValueError("map anchor must be three finite numbers")
        accepted = 0
        self.polls += 1
        for entity in entities or ():
            pose = list(getattr(entity, "pose_xyz_quat", ()) or ())
            if len(pose) < 3 or not all(_finite(value) for value in pose[:3]):
                # An entity without a measured position is a perception claim we
                # cannot place; the catalogue still describes it, the map cannot.
                continue
            category = str(getattr(entity, "category", "") or "").strip()
            if not category:
                continue
            attributes = _attributes(dict(getattr(entity, "attributes", {}) or {}))
            confidence = getattr(entity, "confidence", 0.0)
            confidence = float(confidence) if _finite(confidence) else 0.0
            mapped = _to_map_frame(pose[:3], anchor)
            instance = self._associate(category, attributes, mapped)
            if instance is None:
                if len(self.instances) >= self.max_instances:
                    self._forget_oldest()
                instance = ObjectInstance(
                    instance_id=_instance_id(category, attributes, len(self.instances)),
                    category=category, attributes=attributes, pose=mapped,
                    confidence=confidence, first_seen_unix_ms=stamp_unix_ms,
                    last_seen_unix_ms=stamp_unix_ms, evidence_frame_id=evidence_frame_id,
                    source_id=source_id,
                    observation_id=str(getattr(entity, "entity_id", "") or ""),
                )
                self.instances.append(instance)
            else:
                instance.pose = mapped
                instance.confidence = confidence
                instance.last_seen_unix_ms = stamp_unix_ms
                instance.sightings += 1
                instance.evidence_frame_id = evidence_frame_id or instance.evidence_frame_id
                instance.source_id = source_id or instance.source_id
                instance.observation_id = str(getattr(entity, "entity_id", "") or instance.observation_id)
            instance.history.append(mapped)
            del instance.history[:-16]
            accepted += 1
            self.sightings += 1
        return accepted

    def _associate(self, category: str, attributes: dict[str, str], pose: list[float]):
        best = None
        best_distance = self.association_gate_m
        for instance in self.instances:
            if instance.category != category:
                continue
            if any(instance.attributes.get(key) not in (None, value)
                   for key, value in attributes.items()):
                continue
            distance = math.dist(instance.pose[:2], pose[:2])
            if distance <= best_distance:
                best, best_distance = instance, distance
        return best

    def _forget_oldest(self) -> None:
        if not self.instances:
            return
        oldest = min(range(len(self.instances)),
                      key=lambda index: self.instances[index].last_seen_unix_ms)
        del self.instances[oldest]

    def merge(self, document: dict[str, Any] | None) -> int:
        """Adopt instances published by an earlier session of the same map.

        A continuation inherits the base map's objects the way it inherits its
        geometry: as claims with their own timestamps, so an old sighting cannot
        masquerade as a fresh one.
        """
        if not document:
            return 0
        if document.get("schemaVersion") != SCHEMA_VERSION:
            raise ValueError("object memory document has an unknown schema version")
        adopted = 0
        for raw in document.get("objects") or []:
            if not isinstance(raw, dict):
                raise TypeError("object memory entries must be objects")
            pose = raw.get("pose")
            if (not isinstance(pose, list) or len(pose) != 3
                    or not all(_finite(value) for value in pose)):
                raise ValueError("object memory entry has an invalid pose")
            instance = ObjectInstance(
                instance_id=str(raw.get("id") or _instance_id("", {}, len(self.instances))),
                category=str(raw.get("category") or "").strip(),
                attributes=_attributes(raw.get("attributes")),
                pose=[float(value) for value in pose],
                confidence=float(raw.get("confidence") or 0.0),
                first_seen_unix_ms=int(raw.get("firstSeenUnixMs") or 0),
                last_seen_unix_ms=int(raw.get("lastSeenUnixMs") or 0),
                sightings=int(raw.get("sightings") or 1),
                evidence_frame_id=str(raw.get("evidenceFrameId") or ""),
                source_id=str(raw.get("sourceId") or ""),
                observation_id=str(raw.get("observationId") or ""),
            )
            if not instance.category or instance.last_seen_unix_ms <= 0:
                raise ValueError("object memory entry is missing its category or timestamp")
            self.instances.append(instance)
            adopted += 1
        if len(self.instances) > self.max_instances:
            del self.instances[:len(self.instances) - self.max_instances]
        return adopted

    # -- query ----------------------------------------------------------

    def recall(self, category: str, *, attributes: dict[str, str] | None = None,
               max_age_ms: int | None = None, now_unix_ms: int | None = None
               ) -> list[ObjectInstance]:
        """Instances of a category, newest sighting first, filtered by age."""
        wanted = str(category or "").strip().casefold()
        if not wanted:
            raise ValueError("recall needs an object category")
        wanted_attributes = {key.casefold(): value.casefold()
                             for key, value in _attributes(attributes).items()}
        now = now_unix_ms if isinstance(now_unix_ms, int) and now_unix_ms > 0 else None
        found = []
        for instance in self.instances:
            if instance.category.casefold() != wanted:
                continue
            if any(str(instance.attributes.get(key, "")).casefold() != value
                   for key, value in wanted_attributes.items()):
                continue
            if max_age_ms is not None:
                if now is None:
                    raise ValueError("age filtering needs the current time")
                if now - instance.last_seen_unix_ms > max_age_ms:
                    continue
            found.append(instance)
        found.sort(key=lambda item: item.last_seen_unix_ms, reverse=True)
        return found

    def reanchor(self, source_anchor, target_anchor) -> int:
        """Express every instance in another planar map anchor.

        A survey accumulates sightings while the live frame is still the driver's
        world frame; the map is written once the final anchor is known. Without
        this step those positions would be expressed in the wrong frame - the
        same class of defect as a point cloud published before its anchor.
        """
        source = [float(value) for value in source_anchor]
        target = [float(value) for value in target_anchor]
        for anchor in (source, target):
            if len(anchor) != 3 or not all(math.isfinite(value) for value in anchor):
                raise ValueError("anchors must be three finite numbers")
        if source == target:
            return 0
        inverse = _inverse_pose(source)
        for instance in self.instances:
            instance.pose = _apply_planar(_apply_planar(instance.pose, inverse), target)
            instance.history = [_apply_planar(_apply_planar(point, inverse), target)
                                for point in instance.history]
        return len(self.instances)

    def document(self, *, now_unix_ms: int, map_id: str, calibration_revision: str,
                 frame_id: str = "map") -> dict[str, Any]:
        """The published ``map.objects.v1`` record, expired instances removed."""
        if not isinstance(now_unix_ms, int) or now_unix_ms <= 0:
            raise ValueError("publishing object memory needs the current time")
        live = [instance for instance in self.instances
                if now_unix_ms - instance.last_seen_unix_ms <= self.max_age_ms]
        live.sort(key=lambda item: (item.category, item.last_seen_unix_ms))
        return {
            "schemaVersion": SCHEMA_VERSION,
            "mapId": map_id,
            "frameId": frame_id,
            "calibrationRevision": calibration_revision,
            "associationGateM": self.association_gate_m,
            "maxAgeMs": self.max_age_ms,
            "entityPolls": self.polls,
            "sightings": self.sightings,
            "objects": [instance.to_dict(now_unix_ms=now_unix_ms) for instance in live],
        }


def _apply_planar(point, pose) -> list[float]:
    """Rotate and translate a point by a planar pose, keeping its height."""
    cosine, sine = math.cos(pose[2]), math.sin(pose[2])
    return [pose[0] + cosine * point[0] - sine * point[1],
            pose[1] + sine * point[0] + cosine * point[1],
            float(point[2])]


def _inverse_pose(pose) -> list[float]:
    cosine, sine = math.cos(pose[2]), math.sin(pose[2])
    return [-(cosine * pose[0] + sine * pose[1]),
            sine * pose[0] - cosine * pose[1],
            -pose[2]]


def _to_map_frame(xyz, anchor) -> list[float]:
    """Apply the session anchor: world x/y and a planar rotation into map frame."""
    x, y, yaw = anchor
    cosine, sine = math.cos(yaw), math.sin(yaw)
    return [x + cosine * xyz[0] - sine * xyz[1],
            y + sine * xyz[0] + cosine * xyz[1],
            float(xyz[2])]


def _instance_id(category: str, attributes: dict[str, str], index: int) -> str:
    suffix = "-".join(f"{key}={value}" for key, value in sorted(attributes.items()))
    stem = "-".join(part for part in (category, suffix) if part) or "object"
    return f"{stem}-{index:03d}"
