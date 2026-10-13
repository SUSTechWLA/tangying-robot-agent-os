"""Bounded SE3 measurement ingestion, without pretending to estimate SLAM poses.

An external ROS2 SLAM/localization adapter registers every hit and the actual
sensor optical origin into the commissioned world frame. This module preserves
that evidence and builds a conservative volume; it never estimates a transform,
fills unknown space, or silently projects a tilted pose onto the floor.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

MAX_CELLS = 250_000
MAX_POINTS = 1_000_000
MAX_FRAMES = 10_000
MAX_RAY_CELLS = 10_000_000
MAX_DOCUMENT_BYTES = 128 * 1024 * 1024
_FRAME_KEYS = {"schemaVersion", "robotId", "mapId", "mapRevision", "frameId", "worldFrameRevision",
               "calibrationRevision", "sourceId", "sourceType", "registrationRevision", "observationId",
               "sequence", "observedAtUnixMs", "pose", "sensorOrigin", "points"}
_IDENTITY_KEYS = ("robotId", "mapId", "mapRevision", "frameId", "worldFrameRevision", "calibrationRevision")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value, name):
    if not isinstance(value, str) or not value or len(value) > 256 or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError(f"{name} must be a nonempty bounded identity")
    return value


def _integer(value, name, minimum=1, maximum=2**53 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _vector(value, length, name):
    if not isinstance(value, (list, tuple)) or len(value) != length or any(type(v) not in (int, float) for v in value):
        raise ValueError(f"{name} must have {length} finite numbers")
    try:
        result = [float(v) for v in value]
    except OverflowError as error:
        raise ValueError(f"{name} must have {length} finite numbers") from error
    if not all(math.isfinite(v) for v in result):
        raise ValueError(f"{name} must have {length} finite numbers")
    return result


def _json(payload):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result
    return json.loads(payload, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON {value}")))


def _read_bounded(path, maximum):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise ValueError("spatial input must be a bounded regular file")
        # Limit the actual read too: a concurrent writer may grow a regular file
        # after fstat, but cannot make this allocation exceed the input budget.
        payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError("spatial input exceeds its byte budget")
    return payload


def read_spatial_json(path, *, maximum=MAX_DOCUMENT_BYTES):
    """Read a bounded regular UTF-8 file with duplicate-key and NaN rejection."""
    return _json(_read_bounded(path, maximum))


class RegisteredSpatialSlamProvider:
    """A single immutable registration basis and bounded ordered sensor history.

    ``map_revision`` identifies the external estimator's frozen map solution.
    Package ``revision`` separately hashes the complete exported artifacts. A
    loop closure changes the external revision and requires a new provider.
    """

    def __init__(self, *, robot_id, map_id, map_revision, world_frame_revision, calibration_revision,
                 resolution, origin, shape, max_measurement_age_ms=60_000,
                 max_frames=MAX_FRAMES, max_points=MAX_POINTS, max_ray_cells=MAX_RAY_CELLS):
        self._identity = {"robotId": _text(robot_id, "robotId"), "mapId": _text(map_id, "mapId"),
                         "mapRevision": _text(map_revision, "mapRevision"), "frameId": "world",
                         "worldFrameRevision": _text(world_frame_revision, "worldFrameRevision"),
                         "calibrationRevision": _text(calibration_revision, "calibrationRevision")}
        resolution = _vector([resolution], 1, "resolution")[0]
        if not .01 <= resolution <= 10:
            raise ValueError("resolution must be finite and 0.01..10 metres")
        self._resolution = float(resolution)
        self._origin = _vector(origin, 3, "origin")
        if not isinstance(shape, (list, tuple)) or len(shape) != 3:
            raise ValueError("shape must be [nx, ny, nz]")
        self._shape = [_integer(n, "shape", maximum=MAX_CELLS) for n in shape]
        if math.prod(self._shape) > MAX_CELLS:
            raise ValueError("volume exceeds 250000 cell budget")
        if any(not math.isfinite(o + n * resolution) or o + n * resolution <= o
               for o, n in zip(self._origin, self._shape, strict=True)):
            raise ValueError("volume dimensions have no finite representable extent")
        self.max_measurement_age_ms = _integer(max_measurement_age_ms, "measurement age", maximum=86_400_000)
        self.max_frames = _integer(max_frames, "frame budget", maximum=MAX_FRAMES)
        self.max_points = _integer(max_points, "point budget", maximum=MAX_POINTS)
        self.max_ray_cells = _integer(max_ray_cells, "ray budget", maximum=MAX_RAY_CELLS)
        self._frames, self._streams, self._observations = [], {}, set()
        self._point_count = 0

    @property
    def identity(self):
        return dict(self._identity)

    @property
    def resolution(self):
        return self._resolution

    @property
    def origin(self):
        return list(self._origin)

    @property
    def shape(self):
        return list(self._shape)

    @property
    def frame_count(self):
        return len(self._frames)

    def _cell(self, point):
        result = tuple(math.floor((v - o) / self._resolution) for v, o in zip(point, self._origin, strict=True))
        if any(not 0 <= v < extent for v, extent in zip(result, self._shape, strict=True)):
            raise ValueError("measurement or sensor origin outside commissioned volume bounds")
        return result

    def _flat(self, cell):
        x, y, z = cell
        nx, ny, _ = self._shape
        return (z * ny + y) * nx + x

    def add(self, frame, *, now_unix_ms=None):
        if not isinstance(frame, dict) or set(frame) != _FRAME_KEYS:
            raise ValueError("registered frame fields do not match slam.registered-frame.v1")
        if frame["schemaVersion"] != "slam.registered-frame.v1":
            raise ValueError("registered frame schema mismatch")
        if any(frame[k] != value for k, value in self.identity.items()):
            raise ValueError("registered frame robot, map, world or calibration identity mismatch")
        source = _text(frame["sourceId"], "sourceId")
        observation = _text(frame["observationId"], "observationId")
        registration = _text(frame["registrationRevision"], "registrationRevision")
        source_type = frame["sourceType"]
        if source_type not in {"rtabmap", "lio", "vio", "external", "fixture"}:
            raise ValueError("unsupported registered measurement sourceType")
        sequence = _integer(frame["sequence"], "sequence")
        stamp = _integer(frame["observedAtUnixMs"], "observedAtUnixMs")
        now = int(time.time() * 1000) if now_unix_ms is None else _integer(now_unix_ms, "reference time")
        if not 0 <= now - stamp <= self.max_measurement_age_ms:
            raise ValueError("registered measurement is stale or future dated")
        previous = self._streams.get(source)
        if previous is not None and (sequence <= previous[0] or stamp <= previous[1]
                                     or (source_type, registration) != previous[2:]):
            raise ValueError("source sequence/time regressed or registration basis changed")
        if observation in self._observations:
            raise ValueError("observation identity already accepted")
        if previous is None and len(self._streams) >= 32:
            raise ValueError("registered source count exceeds budget")
        pose = _vector(frame["pose"], 7, "SE3 pose xyz,wxyz")
        if abs(sum(v * v for v in pose[3:]) - 1.) > 1e-5:
            raise ValueError("SE3 pose requires a normalized quaternion")
        sensor = _vector(frame["sensorOrigin"], 3, "actual registered sensor origin")
        self._cell(sensor)
        points = frame["points"]
        if (not isinstance(points, list) or not points or len(self._frames) >= self.max_frames
                or len(points) + self._point_count > self.max_points):
            raise ValueError("registered frame/point count exceeds budget or is empty")
        normalized_points = []
        for point in points:
            point = _vector(point, 3, "registered endpoint")
            self._cell(point)
            normalized_points.append(point)
        accepted = {**copy.deepcopy(frame), "pose": pose, "sensorOrigin": sensor, "points": normalized_points}
        self._frames.append(accepted)
        self._streams[source] = (sequence, stamp, source_type, registration)
        self._observations.add(observation)
        self._point_count += len(points)
        return True

    def _ray(self, origin, endpoint):
        """Cells with positive interior intersection; simultaneous ties skip corners."""
        cell, end = list(self._cell(origin)), self._cell(endpoint)
        direction = [b - a for a, b in zip(origin, endpoint, strict=True)]
        if any(delta == 0 and (coordinate - base) / self._resolution == math.floor((coordinate - base) / self._resolution)
               for coordinate, base, delta in zip(origin, self._origin, direction, strict=True)):
            # A ray running along a voxel face supplies no interior evidence for
            # either adjacent voxel. The hit cell is still occupied separately.
            yield end
            return
        steps = [1 if v > 0 else -1 if v < 0 else 0 for v in direction]
        increments = [self.resolution / abs(v) if v else math.inf for v in direction]
        next_crossing = [((self.origin[a] + (cell[a] + (steps[a] > 0)) * self.resolution - origin[a]) / direction[a])
                         if steps[a] else math.inf for a in range(3)]
        # Every iteration crosses at least one plane; both ends are in bounds.
        for _ in range(sum(self._shape) + 1):
            # A negative-going ray starting exactly on a cell boundary has no
            # interior evidence in the cell on the positive side of that plane.
            crossing = min(next_crossing)
            if crossing <= 0 and tuple(cell) != end:
                for a in range(3):
                    if next_crossing[a] <= 0:
                        cell[a] += steps[a]
                        next_crossing[a] += increments[a]
                continue
            yield tuple(cell)
            if tuple(cell) == end:
                return
            crossing = min(next_crossing)
            for a in range(3):
                if abs(next_crossing[a] - crossing) < 1e-12:
                    cell[a] += steps[a]
                    next_crossing[a] += increments[a]
        raise ValueError("ray traversal failed to terminate")

    def volume(self, *, terrain=None):
        if not self._frames:
            raise ValueError("cannot publish a volume without registered observations")
        cells = [-1] * math.prod(self.shape)
        # Occupied endpoints always win, including endpoints from later frames.
        for frame in self._frames:
            for point in frame["points"]:
                cells[self._flat(self._cell(point))] = 100
        visits = 0
        for frame in self._frames:
            for point in frame["points"]:
                for cell in self._ray(frame["sensorOrigin"], point):
                    visits += 1
                    if visits > self.max_ray_cells:
                        raise ValueError("registered ray traversal exceeds computation budget")
                    index = self._flat(cell)
                    if cells[index] == 100:
                        break
                    cells[index] = 0
        result = {"schemaVersion": "navigation.volume.v1", **self.identity, "resolution": self.resolution,
                  "origin": list(self.origin), "shape": list(self.shape), "cells": cells}
        if terrain is not None:
            result["terrain"] = copy.deepcopy(terrain)
        from .spatial_navigation import validate_volume
        return validate_volume(result)

    def measurements(self):
        return {"schemaVersion": "slam.registered-measurements.v1", "identity": dict(self.identity),
                "geometry": {"resolution": self.resolution, "origin": list(self.origin), "shape": list(self.shape)},
                "budgets": {"maxMeasurementAgeMs": self.max_measurement_age_ms, "maxFrames": self.max_frames,
                            "maxPoints": self.max_points, "maxRayCells": self.max_ray_cells},
                "frames": copy.deepcopy(self._frames)}

    def provenance(self):
        return {"schemaVersion": "slam.spatial-session.v1", **self.identity,
                "provider": {"id": "registered-spatial-measurements", "version": "1", "poseModel": "se3",
                             "poseOrder": "xyz,wxyz", "units": "metres", "estimatesPoses": False},
                "occupancy": {"unknown": -1, "free": 0, "occupied": 100, "freeBasis": "measured_sensor_rays",
                              "unknownIsTraversable": False, "occupiedWins": True,
                              "sensorOriginBasis": "external_registered_optical_origin"},
                "frameCount": len(self._frames), "pointCount": self._point_count,
                "observations": [{**{k: copy.deepcopy(v) for k, v in frame.items() if k != "points"},
                                  "pointCount": len(frame["points"]), "pointsSha256": _digest(frame["points"])}
                                 for frame in self._frames]}

    @classmethod
    def restore(cls, document):
        if not isinstance(document, dict) or set(document) != {"schemaVersion", "identity", "geometry", "budgets", "frames"} \
                or document["schemaVersion"] != "slam.registered-measurements.v1":
            raise ValueError("invalid registered measurement history")
        identity, geometry, budgets = document["identity"], document["geometry"], document["budgets"]
        if (not isinstance(identity, dict) or set(identity) != set(_IDENTITY_KEYS) or identity["frameId"] != "world"
                or not isinstance(geometry, dict) or set(geometry) != {"resolution", "origin", "shape"}
                or not isinstance(budgets, dict) or set(budgets) != {"maxMeasurementAgeMs", "maxFrames", "maxPoints", "maxRayCells"}
                or not isinstance(document["frames"], list)):
            raise ValueError("registered measurement history fields invalid")
        instance = cls(robot_id=identity["robotId"], map_id=identity["mapId"], map_revision=identity["mapRevision"],
                       world_frame_revision=identity["worldFrameRevision"], calibration_revision=identity["calibrationRevision"],
                       **geometry, max_measurement_age_ms=budgets["maxMeasurementAgeMs"], max_frames=budgets["maxFrames"],
                       max_points=budgets["maxPoints"], max_ray_cells=budgets["maxRayCells"])
        # Historical restore revalidates sequence and basis; it does not claim
        # these stored observations are currently fresh localization samples.
        for frame in document["frames"]:
            if not isinstance(frame, dict) or "observedAtUnixMs" not in frame:
                raise ValueError("invalid registered frame in history")
            instance.add(frame, now_unix_ms=frame["observedAtUnixMs"])
        return instance

    def publish(self, directory, *, terrain=None):
        return publish_spatial_map(directory, self, terrain=terrain)


def _write_synced(path, payload):
    with open(path, "xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def publish_spatial_map(directory, provider, *, terrain=None):
    """Atomically publish a complete package; repeat identical publication is safe."""
    documents = {"volume.json": provider.volume(terrain=terrain), "provenance.json": provider.provenance(),
                 "measurements.json": provider.measurements()}
    payloads = {name: _canonical(document) for name, document in documents.items()}
    if any(len(data) > MAX_DOCUMENT_BYTES for data in payloads.values()):
        raise ValueError("spatial map artifact exceeds storage budget")
    manifest = {"schemaVersion": "map.spatial-package.v1", **provider.identity,
                "artifacts": {name.removesuffix(".json"): {"href": name, "bytes": len(payload),
                                                          "sha256": hashlib.sha256(payload).hexdigest()}
                              for name, payload in payloads.items()}}
    manifest["revision"] = _digest(manifest)
    destination = Path(directory).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.parent / ("." + destination.name + ".publication.lock")
    lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    staging = None
    try:
        if destination.exists() or destination.is_symlink():
            existing = load_spatial_map(destination)
            if existing["manifest"] != manifest:
                raise FileExistsError("spatial map revisions are immutable; publish in a new directory")
            return manifest
        staging = Path(tempfile.mkdtemp(prefix=".spatial-map-", dir=destination.parent))
        for name, payload in payloads.items():
            _write_synced(staging / name, payload)
        _write_synced(staging / "manifest.json", _canonical(manifest))
        fd = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(staging, destination)
        staging = None
        fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return manifest
    finally:
        os.close(lock_fd)
        lock.unlink()
        if staging is not None:
            shutil.rmtree(staging)


def load_spatial_map(directory):
    """Verify artifacts and their replayed derivation before exposing a volume."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("spatial package must be a regular local directory")
    def read(name, maximum):
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("spatial package artifact is missing, linked or exceeds budget")
        return _read_bounded(path, maximum)
    manifest = _json(read("manifest.json", 16_384))
    expected = {"schemaVersion", "revision", "artifacts", *_IDENTITY_KEYS}
    if not isinstance(manifest, dict) or set(manifest) != expected or manifest["schemaVersion"] != "map.spatial-package.v1":
        raise ValueError("spatial package manifest fields invalid")
    if manifest["revision"] != _digest({k: v for k, v in manifest.items() if k != "revision"}):
        raise ValueError("spatial package manifest hash mismatch")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != {"volume", "measurements", "provenance"}:
        raise ValueError("spatial package artifact set invalid")
    documents = {}
    for role, entry in artifacts.items():
        if not isinstance(entry, dict) or set(entry) != {"href", "bytes", "sha256"} or entry["href"] != role + ".json":
            raise ValueError("spatial package artifact reference invalid")
        _integer(entry["bytes"], "artifact bytes", maximum=MAX_DOCUMENT_BYTES)
        data = read(entry["href"], entry["bytes"])
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("spatial package artifact hash mismatch")
        documents[role] = _json(data)
    provider = RegisteredSpatialSlamProvider.restore(documents["measurements"])
    if any(manifest[k] != provider.identity[k] for k in _IDENTITY_KEYS):
        raise ValueError("spatial package identity mismatch")
    if not isinstance(documents["volume"], dict):
        raise TypeError("spatial volume artifact must be an object")
    rebuilt = provider.volume(terrain=documents["volume"].get("terrain"))
    if rebuilt != documents["volume"] or provider.provenance() != documents["provenance"]:
        raise ValueError("spatial map artifacts disagree with measured provenance")
    return {"manifest": manifest, **documents}
