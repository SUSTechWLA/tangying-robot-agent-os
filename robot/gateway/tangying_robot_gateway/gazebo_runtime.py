"""The robot runtime contract, assembled from Gazebo.

Gazebo is not a special case of the system; it is another backend. The agent talks
to ``robot.profile.v1`` and does not know whether the other end is MuJoCo, a
Gazebo container or a physical unit — that is the property this module exists to
preserve. What differs between backends is only where the samples come from, so
that is the only thing this module takes as input.

The split is deliberate:

* **here** — turning a synchronised capture into the runtime's own payloads, and
  refusing one that is malformed. Pure: samples in, dictionaries out. No ROS, no
  gRPC, no container. This is where the numbers can be wrong in ways no downstream
  check can see, so this is where the tests are.
* **the node** — subscribing, serving, publishing. Thin, and hard to test without
  a simulator, so it holds as little judgement as possible.

Validation is not re-implemented. A capture becomes a real
:class:`~tangying_robot_gateway.rgbd.RgbdFrame` and goes through the same
``validate_frame`` every other backend uses; a bridge with its own idea of what a
valid capture is would be a second definition, and the two would drift.

What is *not* here yet, and must not be implied: skills and services. A runtime
that can be observed but not commanded is not a backend. The honest boundary is
that this module answers ``GetRuntimeInfo`` and ``Observe``; ``ExecuteSkill``,
``CallService`` and the emergency stop are the next piece.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from .gazebo_bridge import (
    base_from_camera,
    build_rgbd_frame,
    intrinsics_from_field_of_view,
)
from .rgbd import RgbdFrame, validate_frame

__all__ = ["CameraSample", "GazeboRuntime", "GazeboRuntimeError",
           "leveled_base_pose", "observation_message"]

#: Streams a client can ask for, matching what the runtime already declares.
KNOWN_STREAMS = ("entities", "rgb", "depth", "reconstruction", "robot_state", "rgbd_raw")

#: How far the base may be off level before its own pose stops describing a planar
#: robot. A differential-drive base is planar by construction; a capture attached to
#: a tilted pose is a capture whose heading and height both mean something else.
MAX_LEVEL_TILT_RAD = 0.15


def leveled_base_pose(world_from_base: np.ndarray | None) -> list[float]:
    """The base pose as ``[x, y, z, qw, qx, qy, qz]``, refusing a tilted one.

    This is the shape every consumer of ``robot_state.base_pose`` already expects -
    ``pose_se2`` validates a 7-element *normalized level-base* quaternion and raises
    on anything else - and until this function existed the Gazebo runtime published
    only the translation ``[x, y, z]``. That is not a shorter pose, it is a
    different one: the mapping workflow reads element 2 as nothing and derives the
    heading from elements 3..6, so a 3-element pose either raised a validation error
    or, if it had been padded, silently reported heading zero for a robot that had
    turned. A survey that cannot see its own heading cannot plan a turn.

    The tilt check is not pedantry. Flattening a pose that is genuinely tilted
    produces a plausible planar pose that is wrong in a way no downstream check can
    detect, so the runtime refuses instead of approximating.
    """
    if world_from_base is None:
        return []
    value = np.asarray(world_from_base, dtype=float)
    if value.shape != (4, 4) or not np.isfinite(value).all():
        raise GazeboRuntimeError("INVALID_POSE", "the base pose must be a finite 4x4 matrix")
    rotation = value[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-3):
        raise GazeboRuntimeError("INVALID_POSE", "the base orientation is not a rotation")
    # Column 2 is the base's own z axis. A level base keeps it vertical; the tilt
    # is therefore the angle between it and world z.
    up = rotation[:, 2]
    tilt = float(np.arccos(np.clip(up[2], -1.0, 1.0)))
    if tilt > MAX_LEVEL_TILT_RAD:
        raise GazeboRuntimeError(
            "BASE_NOT_LEVEL",
            f"the base is tilted {np.degrees(tilt):.1f}deg; planar SLAM cannot use this pose")
    # Only the yaw survives: roll and pitch are within tolerance and are dropped
    # deliberately, so the published quaternion is level rather than almost level.
    yaw = float(np.arctan2(rotation[1, 0], rotation[0, 0]))
    half = yaw / 2.0
    return [float(value[0, 3]), float(value[1, 3]), float(value[2, 3]),
            float(np.cos(half)), 0.0, 0.0, float(np.sin(half))]


class GazeboRuntimeError(Exception):
    """A refusal with a stable code, because these end up in client diagnostics."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CameraSample:
    """One synchronised capture, as the ROS driver hands it over.

    ``world_from_camera_link`` is in the *link* convention (REP-103, x forward),
    not the optical one: that is what a Gazebo camera's pose is expressed in, and
    converting here rather than at the caller keeps the conversion in one file.
    """

    width: int
    height: int
    rgb: np.ndarray
    depth_metres: np.ndarray
    horizontal_fov_rad: float
    world_from_camera_link: np.ndarray
    captured_at_unix_ms: int
    sensor_stamp_ns: int = 0
    odometry_stamp_ns: int = 0
    received_monotonic_ns: int = 0
    base_pose_at_capture: np.ndarray | None = None


class GazeboRuntime:
    """Assembles the runtime's payloads from whatever the newest samples are."""

    def __init__(self, *, robot_id: str, adapter: str, cameras: Mapping[str, str],
                 calibration_revision: str, software_version: str,
                 protocol_version: str = "1.0", runtime_version: str = "0.1.0"):
        if not robot_id or not adapter:
            raise GazeboRuntimeError(
                "IDENTITY_REQUIRED",
                "a runtime must name the robot and the adapter it presents as")
        if not cameras:
            raise GazeboRuntimeError("CAMERAS_REQUIRED", "a runtime with no cameras cannot be observed")
        if not calibration_revision:
            raise GazeboRuntimeError(
                "CALIBRATION_REVISION_REQUIRED",
                "a capture is only comparable against the calibration it was taken with")
        self.robot_id = robot_id
        self.adapter = adapter
        #: Runtime camera name -> the Gazebo/ROS frame it comes from. The mapping
        #: is data rather than a convention, so `base-rgbd` meaning `/camera/base`
        #: is stated once instead of assumed at every call site.
        self.cameras = dict(cameras)
        self.calibration_revision = calibration_revision
        self.software_version = software_version
        self.protocol_version = protocol_version
        self.runtime_version = runtime_version
        self._samples: dict[str, CameraSample] = {}
        self._base_pose: np.ndarray | None = None
        self._sequence = 0

    @property
    def base_pose(self) -> np.ndarray | None:
        """Where the robot is, or ``None`` before the first odometry message."""
        return self._base_pose

    # -- input --------------------------------------------------------------

    def record_base_pose(self, robot_from_base: np.ndarray) -> None:
        """Where the robot is, in whatever world frame the cameras share."""
        value = np.asarray(robot_from_base, dtype=float)
        if value.shape != (4, 4) or not np.isfinite(value).all():
            raise GazeboRuntimeError("INVALID_POSE", "the base pose must be a finite 4x4 matrix")
        self._base_pose = value

    def record(self, camera: str, sample: CameraSample) -> None:
        """Keep the newest capture for one camera, refusing a malformed one.

        Refused rather than stored: a capture that cannot be encoded is worse than
        a missing one, because a client would receive plausible geometry built
        from numbers nobody validated.
        """
        if camera not in self.cameras:
            raise GazeboRuntimeError(
                "UNKNOWN_CAMERA",
                f"{camera!r} is not one of this runtime's cameras ({sorted(self.cameras)})")
        if self._base_pose is None:
            raise GazeboRuntimeError(
                "NO_BASE_POSE",
                "a capture cannot be attached to a robot whose pose is not known yet")
        sample = replace(sample, base_pose_at_capture=np.array(self._base_pose, copy=True))
        self._frame_for(camera, sample)   # validate now, not on the way out
        self._samples[camera] = sample

    # -- assembly -----------------------------------------------------------

    def _frame_for(self, camera: str, sample: CameraSample) -> RgbdFrame:
        intrinsics = intrinsics_from_field_of_view(
            sample.width, sample.height, sample.horizontal_fov_rad)
        transform = base_from_camera(sample.base_pose_at_capture if sample.base_pose_at_capture is not None
                                     else self._base_pose, sample.world_from_camera_link)
        build_rgbd_frame(
            width=sample.width, height=sample.height, rgb=sample.rgb,
            depth_metres=sample.depth_metres, intrinsics=intrinsics,
            capture_in_base=transform, calibration_revision=self.calibration_revision)
        frame = RgbdFrame(
            robot_id=self.robot_id,
            source_id=f"{self.robot_id}/{camera}",
            frame_id=f"{camera}_optical",
            transform_revision=self.calibration_revision,
            captured_at_unix_ms=int(sample.captured_at_unix_ms),
            sequence=1,
            rgb=np.asarray(sample.rgb, dtype=np.uint8),
            depth_m=np.asarray(sample.depth_metres, dtype=np.float64),
            intrinsics=intrinsics,
            world_from_camera=np.asarray(transform, dtype=np.float64),
        )
        # The same rules every other backend obeys. Age is not re-checked: this
        # frame was just taken, and freshness belongs where it enters the system.
        validate_frame(frame, max_age_ms=None)
        return frame

    def rgbd_payload(self, camera: str) -> dict[str, Any]:
        """One capture, in the wire fields ``RGBDFrame`` declares."""
        sample = self._samples.get(camera)
        if sample is None:
            raise GazeboRuntimeError(
                "NO_CAPTURE", f"no capture has arrived for {camera!r} yet")
        intrinsics = intrinsics_from_field_of_view(
            sample.width, sample.height, sample.horizontal_fov_rad)
        transform = base_from_camera(sample.base_pose_at_capture if sample.base_pose_at_capture is not None
                                     else self._base_pose, sample.world_from_camera_link)
        return build_rgbd_frame(
            width=sample.width, height=sample.height, rgb=sample.rgb,
            depth_metres=sample.depth_metres, intrinsics=intrinsics,
            capture_in_base=transform, calibration_revision=self.calibration_revision)

    def runtime_info(self, *, estopped: bool = False,
                     skills: Sequence[str] = (),
                     blockers: Sequence[str] = ()) -> dict[str, Any]:
        """``RuntimeInfo`` as a dictionary, ready for the message.

        ``manipulation_ready`` is derived, not asserted: a runtime that is
        estopped, or whose skills are empty, is not ready to manipulate, and
        saying otherwise would invite a client to plan against a robot that cannot
        move.
        """
        self._sequence += 1
        problems = list(blockers)
        if estopped:
            problems = ["EMERGENCY_STOP_LATCHED", *problems]
        if not skills:
            problems = [*problems, "NO_SKILLS_DECLARED"]
        return {
            "robot_id": self.robot_id,
            "adapter": self.adapter,
            "skills": sorted(skills),
            "cameras": sorted(self.cameras),
            "manipulation_ready": bool(skills) and not estopped,
            "blockers": problems,
            "software_version": self.software_version,
            "protocol_version": self.protocol_version,
            "runtime_version": self.runtime_version,
            "catalog_revision": self.catalog_revision(skills),
            "calibration_revision": self.calibration_revision,
        }

    def catalog_revision(self, skills: Sequence[str]) -> str:
        """A digest over what this runtime presents, so a client can pin it.

        Over the skills *and* the calibration revision: two runtimes offering the
        same skills against different calibrations are not the same runtime, and a
        catalog that hashed only the names would call them identical.
        """
        wire = json.dumps(
            {"skills": sorted(skills), "cameras": sorted(self.cameras),
             "calibrationRevision": self.calibration_revision},
            ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(wire).hexdigest()

    def observation(self, camera: str, *, observation_id: str,
                    streams: Sequence[str] = (), include_raw: bool = False) -> dict[str, Any]:
        """One ``Observation`` for one camera.

        The stream list is honoured rather than ignored: a client that asked for
        entities and got a full metric RGB-D payload has been sent data it did not
        ask for, and on a slow link that is the difference between a live robot
        and a stalled one.
        """
        requested = tuple(streams) if streams else KNOWN_STREAMS
        unknown = sorted(set(requested) - set(KNOWN_STREAMS))
        if unknown:
            raise GazeboRuntimeError(
                "UNKNOWN_STREAM", f"this runtime does not serve {unknown}")
        sample = self._samples.get(camera)
        if sample is None:
            raise GazeboRuntimeError(
                "NO_CAPTURE", f"no capture has arrived for {camera!r} yet")
        payload: dict[str, Any] = {
            "observation_id": observation_id,
            "wall_time_unix_ms": int(sample.captured_at_unix_ms),
            # A monotonic clock, read now: a wall-clock stamp multiplied up
            # would look like a monotonic one and drift with NTP corrections.
            "monotonic_time_ns": time.monotonic_ns(),
            "robot_state": {
                "base_pose": leveled_base_pose(self._base_pose),
                "calibration_revision": self.calibration_revision,
                "adapter": self.adapter,
            },
        }
        if include_raw or "rgbd_raw" in requested:
            payload["rgbd_frame"] = self.rgbd_payload(camera)
        return payload


def observation_message(payload: Mapping[str, Any]):
    """One :func:`GazeboRuntime.observation` payload as the wire message.

    One definition, two callers: the gRPC node that serves ``Observe`` and the
    in-process capture the mapping workflow reads through. They were written
    separately once, and the second one dropped ``robot_state`` - so every
    observation reached the client with the base pose missing while looking
    perfectly well formed. A conversion that exists twice is a conversion that
    disagrees with itself eventually; this is the one copy.
    """
    from tangying_robot_proto.robot.v1 import robot_pb2

    message = robot_pb2.Observation(
        observation_id=payload["observation_id"],
        wall_time_unix_ms=payload["wall_time_unix_ms"],
        monotonic_time_ns=payload["monotonic_time_ns"])
    message.robot_state.update(payload["robot_state"])
    raw = payload.get("rgbd_frame")
    if raw is not None:
        message.rgbd_frame.CopyFrom(robot_pb2.RGBDFrame(
            width=raw["width"], height=raw["height"], rgb=raw["rgb"],
            depth_metres_f32=raw["depth_metres_f32"],
            intrinsics=raw["intrinsics"], base_from_camera=raw["base_from_camera"]))
    return message
