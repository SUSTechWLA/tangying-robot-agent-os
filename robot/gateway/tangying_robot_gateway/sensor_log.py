"""A replayable sensor log: raw frames and the state they were taken in.

Why this exists
---------------
Every part of the mapping and exploration stack was being compared by running a
robot. At 0.05 m/s a survey takes twenty minutes, so in practice nothing was
compared at all: a change to the fusion rule, the voxel size, the registration
threshold or the frontier policy could only be argued from a single frozen grid,
and a single grid cannot show what a *change* would have done.

The map artifacts the console already writes are not a substitute. Their own
header says so::

    kind = capture_previews, rgb = jpeg-quality-78,
    depth = nearest-sample-fixed-scale-preview, rawDepthSaved = False

Those are pictures of a map for a person to look at, at 240x180, with the raw
depth discarded. Replaying a pipeline from them is impossible in principle, not
merely awkward.

What is recorded here is the input, unchanged: the RGB and depth arrays a camera
produced, the pose it produced them from, and the intrinsics, revision and joint
state needed to interpret them. Given a log, a mapping or localization change can
be evaluated offline, on the same frames, as many times as anyone likes.

Determinism
-----------
A log is only useful if a replay is reproducible, so every frame carries a digest
of exactly the bytes that were written, and the manifest carries a digest over the
whole log. Recording the same run twice, or reading one back twice, is checkable
rather than assumed.

Format
------
A directory, chosen so that a log can be written while a robot is moving and read
without loading it all:

* ``manifest.json`` - schema, identity, calibration revision, encoding, totals
* ``index.jsonl``   - one line per frame: scalars plus offsets into the blobs
* ``rgb.bin``       - concatenated ``uint8`` HxWx3 frames, row-major
* ``depth.bin``     - concatenated ``float32`` little-endian metres, row-major
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import struct
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from .rgbd import RgbdFrame, validate_frame

#: Bumped when the on-disk layout changes in a way a reader must know about.
SCHEMA = "robot.sensor_log.v1"

MANIFEST_NAME = "manifest.json"
INDEX_NAME = "index.jsonl"
RGB_NAME = "rgb.bin"
DEPTH_NAME = "depth.bin"


class SensorLogError(Exception):
    """A refusal a reader or writer can act on: a code and a sentence."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LoggedFrame:
    """One recorded instant: what the camera produced and where it was."""

    frame: RgbdFrame
    joints: dict[str, float]
    base_pose: tuple[float, ...] | None
    digest: str
    #: Which capture in the log this is, counted from zero.
    ordinal: int


def _digest(parts: Sequence[bytes]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(struct.pack("<Q", len(part)))
        digest.update(part)
    return digest.hexdigest()


def _scalars(frame: RgbdFrame) -> dict[str, Any]:
    return {
        "robot_id": frame.robot_id,
        "source_id": frame.source_id,
        "frame_id": frame.frame_id,
        "transform_revision": frame.transform_revision,
        "captured_at_unix_ms": frame.captured_at_unix_ms,
        "sequence": frame.sequence,
    }


class SensorLogWriter:
    """Appends frames to a directory, and finishes with a verifiable manifest.

    Deliberately append-only and unbuffered in spirit: a survey that is
    interrupted should still leave every frame it managed to record readable,
    which is why the index is one line per frame rather than one document.
    """

    def __init__(self, directory: str | os.PathLike[str], *, robot_id: str,
                 calibration_revision: str, source: str,
                 metadata: Mapping[str, Any] | None = None):
        if not robot_id or not calibration_revision:
            raise SensorLogError(
                "LOG_IDENTITY_REQUIRED",
                "a log is only replayable against the robot and calibration it was "
                "taken with, so both are required")
        self.directory = pathlib.Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.robot_id = robot_id
        self.calibration_revision = calibration_revision
        self.source = source
        self.metadata = dict(metadata or {})
        self._index = (self.directory / INDEX_NAME).open("wb")
        self._rgb = (self.directory / RGB_NAME).open("wb")
        self._depth = (self.directory / DEPTH_NAME).open("wb")
        self._count = 0
        self._rgb_bytes = 0
        self._depth_bytes = 0
        self._closed = False
        self._digests: list[str] = []

    def append(self, frame: RgbdFrame, *, joints: Mapping[str, float] | None = None,
               base_pose: Sequence[float] | None = None) -> str:
        """Record one frame. Returns its digest."""
        if self._closed:
            raise SensorLogError("LOG_CLOSED", "this log has already been finished")
        if frame.robot_id != self.robot_id:
            raise SensorLogError(
                "LOG_ROBOT_MISMATCH",
                f"the log is for {self.robot_id} and this frame is from {frame.robot_id}")
        validate_frame(frame, max_age_ms=None)
        rgb = np.ascontiguousarray(frame.rgb, dtype=np.uint8)
        depth = np.ascontiguousarray(frame.depth_m, dtype="<f4")
        rgb_bytes = rgb.tobytes(order="C")
        depth_bytes = depth.tobytes(order="C")
        entry = {
            **_scalars(frame),
            "width": int(rgb.shape[1]),
            "height": int(rgb.shape[0]),
            "rgb_offset": self._rgb_bytes,
            "rgb_bytes": len(rgb_bytes),
            "depth_offset": self._depth_bytes,
            "depth_bytes": len(depth_bytes),
            "intrinsics": [float(value) for value in np.asarray(frame.intrinsics).reshape(-1)],
            "world_from_camera": [float(value)
                                  for value in np.asarray(frame.world_from_camera).reshape(-1)],
            "joints": {str(name): float(value) for name, value in (joints or {}).items()},
            "base_pose": None if base_pose is None else [float(v) for v in base_pose],
        }
        digest = _digest([rgb_bytes, depth_bytes,
                          json.dumps(entry, sort_keys=True, ensure_ascii=False).encode("utf-8")])
        entry["digest"] = digest
        self._rgb.write(rgb_bytes)
        self._depth.write(depth_bytes)
        self._index.write(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        self._index.write(b"\n")
        self._rgb_bytes += len(rgb_bytes)
        self._depth_bytes += len(depth_bytes)
        self._count += 1
        self._digests.append(digest)
        return digest

    def close(self) -> dict[str, Any]:
        """Finish the log and write its manifest. Idempotent."""
        if self._closed:
            return json.loads((self.directory / MANIFEST_NAME).read_text(encoding="utf-8"))
        self._index.flush()
        os.fsync(self._index.fileno())
        self._rgb.flush()
        os.fsync(self._rgb.fileno())
        self._depth.flush()
        os.fsync(self._depth.fileno())
        for handle in (self._index, self._rgb, self._depth):
            handle.close()
        self._closed = True
        manifest = {
            "schemaVersion": SCHEMA,
            "robotId": self.robot_id,
            "calibrationRevision": self.calibration_revision,
            "source": self.source,
            "metadata": self.metadata,
            "frames": self._count,
            "encoding": {
                "rgb": "uint8-hxwx3-row-major",
                "depth": "float32-le-metres-row-major",
                "index": "jsonl-one-line-per-frame",
            },
            "bytes": {"rgb": self._rgb_bytes, "depth": self._depth_bytes},
            # Over the per-frame digests in order, so a log with a frame inserted,
            # removed or altered cannot present the same identity.
            "logDigest": _digest([d.encode("ascii") for d in self._digests]),
        }
        path = self.directory / MANIFEST_NAME
        handle, temporary = tempfile.mkstemp(dir=str(self.directory),
                                             prefix=f".{MANIFEST_NAME}.", suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return manifest


class SensorLogReader:
    """Reads a log back into frames, verifying each one as it goes."""

    def __init__(self, directory: str | os.PathLike[str]):
        self.directory = pathlib.Path(directory)
        manifest_path = self.directory / MANIFEST_NAME
        if not manifest_path.is_file():
            raise SensorLogError("LOG_MISSING", f"{manifest_path} is not a sensor log")
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("schemaVersion") != SCHEMA:
            raise SensorLogError(
                "LOG_SCHEMA",
                f"this log is {self.manifest.get('schemaVersion')!r}, "
                f"this build reads {SCHEMA!r}")
        self._index_path = self.directory / INDEX_NAME
        for name in (INDEX_NAME, RGB_NAME, DEPTH_NAME):
            if not (self.directory / name).is_file():
                raise SensorLogError("LOG_INCOMPLETE", f"{name} is missing from the log")

    def __len__(self) -> int:
        return int(self.manifest.get("frames", 0))

    @property
    def robot_id(self) -> str:
        return str(self.manifest.get("robotId", ""))

    @property
    def calibration_revision(self) -> str:
        return str(self.manifest.get("calibrationRevision", ""))

    def entries(self) -> Iterator[dict[str, Any]]:
        with self._index_path.open("rb") as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)

    def __iter__(self) -> Iterator[LoggedFrame]:
        """Every frame, in the order it was recorded.

        The blobs are opened once and seeked per frame rather than read into
        memory: a log is allowed to be larger than the machine, and a replayer
        that loads it all would put a ceiling on how long a survey can be
        recorded.
        """
        with (self.directory / RGB_NAME).open("rb") as rgb_stream, \
                (self.directory / DEPTH_NAME).open("rb") as depth_stream:
            for ordinal, entry in enumerate(self.entries()):
                width, height = int(entry["width"]), int(entry["height"])
                rgb_stream.seek(int(entry["rgb_offset"]))
                raw_rgb = rgb_stream.read(int(entry["rgb_bytes"]))
                depth_stream.seek(int(entry["depth_offset"]))
                raw_depth = depth_stream.read(int(entry["depth_bytes"]))
                expected_rgb = width * height * 3
                expected_depth = width * height * 4
                if len(raw_rgb) != expected_rgb or len(raw_depth) != expected_depth:
                    raise SensorLogError(
                        "LOG_TRUNCATED",
                        f"frame {ordinal} is {len(raw_rgb)}/{len(raw_depth)} bytes, "
                        f"expected {expected_rgb}/{expected_depth}")
                digest = _digest([raw_rgb, raw_depth, json.dumps(
                    {k: v for k, v in entry.items() if k != "digest"},
                    sort_keys=True, ensure_ascii=False).encode("utf-8")])
                if digest != entry.get("digest"):
                    raise SensorLogError(
                        "LOG_CORRUPT",
                        f"frame {ordinal} does not match its recorded digest")
                frame = RgbdFrame(
                    robot_id=entry["robot_id"], source_id=entry["source_id"],
                    frame_id=entry["frame_id"],
                    transform_revision=entry["transform_revision"],
                    captured_at_unix_ms=int(entry["captured_at_unix_ms"]),
                    sequence=int(entry["sequence"]),
                    rgb=np.frombuffer(raw_rgb, dtype=np.uint8).reshape(height, width, 3),
                    depth_m=np.frombuffer(raw_depth, dtype="<f4").reshape(height, width)
                    .astype(np.float64),
                    intrinsics=np.asarray(entry["intrinsics"], dtype=np.float64).reshape(3, 3),
                    world_from_camera=np.asarray(entry["world_from_camera"],
                                                 dtype=np.float64).reshape(4, 4),
                )
                yield LoggedFrame(
                    frame=frame,
                    joints={str(k): float(v) for k, v in (entry.get("joints") or {}).items()},
                    base_pose=(None if entry.get("base_pose") is None
                               else tuple(float(v) for v in entry["base_pose"])),
                    digest=digest, ordinal=ordinal)

    def verify(self) -> dict[str, Any]:
        """Read every frame and check the whole log against its manifest.

        The check a person runs before trusting a comparison: if this disagrees,
        two runs fed the same log were not fed the same frames.
        """
        digests: list[str] = []
        frames = 0
        for logged in self:
            digests.append(logged.digest)
            frames += 1
        computed = _digest([d.encode("ascii") for d in digests])
        recorded = str(self.manifest.get("logDigest", ""))
        if frames != len(self):
            raise SensorLogError(
                "LOG_FRAME_COUNT",
                f"the manifest says {len(self)} frames and the index holds {frames}")
        if computed != recorded:
            raise SensorLogError(
                "LOG_DIGEST_MISMATCH",
                f"the log digests to {computed[:16]} and its manifest claims "
                f"{recorded[:16]}")
        return {"frames": frames, "logDigest": computed,
                "bytes": dict(self.manifest.get("bytes") or {})}
