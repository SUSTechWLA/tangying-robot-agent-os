"""Bounded ROS message snapshots feeding the transport-neutral RGB-D pipeline.

This module deliberately imports no ROS runtime. Image topics must already be
rectified and depth aligned to the colour optical frame. It never invents
capture timestamps, detects objects from simulator state, or connects a motor.
"""
from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from .contracts import RobotProfile
from .plugin_backend import PluginBackend
from .rgbd import RgbdFrame, RgbdPerception, validate_frame
from .rgbd_images import encode_depth_preview, encode_rgb_png
from .runtime import Command, ReconstructionCapture, Result


@dataclass(frozen=True)
class ImagePacket:
    stamp_ns: int
    frame_id: str
    width: int
    height: int
    encoding: str
    step: int
    is_bigendian: bool
    data: bytes


@dataclass(frozen=True)
class CameraInfoPacket:
    stamp_ns: int
    frame_id: str
    width: int
    height: int
    k: tuple[float, ...]
    d: tuple[float, ...]
    # Explicitly rectified topics may use CameraInfo.P even when the raw
    # camera's D is nonzero. P must have no stereo translation component.
    p: tuple[float, ...] = ()
    rectified: bool = False
    binning_x: int = 0
    binning_y: int = 0
    roi: tuple[int, int, int, int] = (0, 0, 0, 0)


@dataclass(frozen=True)
class WorldTransform:
    stamp_ns: int
    source_frame_id: str
    target_frame_id: str
    transform_revision: str
    matrix: np.ndarray
    static: bool = False


def _immutable_array(value, dtype=None) -> np.ndarray:
    array = np.asarray(value, dtype=dtype)
    # The bytes backing the view prevent callers from making it writeable.
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)


class RosRgbdInput:
    """Synchronize bounded Image/CameraInfo snapshots and look up capture-time TF.

    ``transform_lookup(optical_frame, capture_stamp_ns)`` must perform an exact
    timestamp lookup, never a latest-transform fallback. Static transforms have
    an explicit zero stamp and ``static=True``. Only Unix wall-clock captures
    are accepted by this first ROS adapter; ROS simulated clocks need their
    own declared clock mapping before entering the product contract.
    """

    def __init__(self, profile: dict | RobotProfile, source_id: str, *,
                 transform_lookup: Callable[[str, int], WorldTransform],
                 max_skew_ms: int = 30, queue_size: int = 4,
                 clock_domain: str = "unix"):
        self.profile = RobotProfile.model_validate(profile)
        self.sensor = next((s for s in self.profile.sensors if s.source_id == source_id), None)
        if self.sensor is None or self.sensor.source_type != "rgbd_camera":
            raise ValueError("source must identify a declared rgbd_camera sensor")
        if clock_domain != "unix":
            raise ValueError("ROS RGB-D requires Unix capture timestamps; simulated clocks are unsupported")
        if (type(max_skew_ms) is not int or not 0 <= max_skew_ms <= 100
                or type(queue_size) is not int or not 1 <= queue_size <= 8):
            raise ValueError("synchronization limits must be bounded integers")
        if not callable(transform_lookup):
            raise TypeError("capture-time transform lookup is required")
        self._lookup = transform_lookup
        self._skew_ns = max_skew_ms * 1_000_000
        self._queue_size = queue_size
        self._queues: dict[str, list] = {"color": [], "depth": [], "info": []}
        self._lock = threading.RLock()
        self._read_lock = threading.Lock()
        self._calibration = None
        self._last_depth_stamp = 0
        self._frame: RgbdFrame | None = None
        self._sequence = 0
        self._fault: str | None = None
        self._fault_generation = 0

    def reject(self, reason: str) -> None:
        """Invalidate cached input after a malformed ROS callback or clock fault."""
        with self._lock:
            self._fault = str(reason)
            self._fault_generation += 1
            for values in self._queues.values():
                values.clear()

    def _header(self, packet) -> None:
        if (type(packet.stamp_ns) is not int or packet.stamp_ns <= 0
                or packet.frame_id != self.sensor.frame_id):
            raise ValueError("capture stamp and declared optical frame are required")
        age_ns = time.time_ns() - packet.stamp_ns
        if age_ns < 0 or age_ns > self.sensor.max_age_ms * 1_000_000:
            raise ValueError("ROS capture is stale or future dated; verify Unix clock synchronization")
        if (type(packet.width) is not int or type(packet.height) is not int
                or not 0 < packet.width <= 3840 or not 0 < packet.height <= 2160):
            raise ValueError("RGB-D image dimensions exceed bounded capture size")

    def _image(self, packet: ImagePacket, color: bool) -> ImagePacket:
        if not isinstance(packet, ImagePacket):
            raise TypeError("expected ImagePacket")
        self._header(packet)
        encodings = {"rgb8": 3, "bgr8": 3} if color else {"16UC1": 2, "32FC1": 4}
        pixel_bytes = encodings.get(packet.encoding)
        if pixel_bytes is None:
            raise ValueError("unsupported RGB or metric depth image encoding")
        if (type(packet.step) is not int or not packet.width * pixel_bytes <= packet.step
                <= packet.width * pixel_bytes + 4096):
            raise ValueError("image row step does not match pixel encoding")
        if type(packet.is_bigendian) is not bool:
            raise ValueError("image endian flag must be boolean")
        if not isinstance(packet.data, (bytes, bytearray, memoryview)):
            raise TypeError("image data must be a bounded byte buffer")
        if len(packet.data) != packet.step * packet.height:
            raise ValueError("image buffer length does not match rows and step")
        return dataclasses.replace(packet, data=bytes(packet.data))

    def _push(self, kind: str, packet) -> None:
        with self._lock:
            values = self._queues[kind]
            if values and packet.stamp_ns < values[-1].stamp_ns:
                raise ValueError("ROS source timestamp regressed")
            if values and packet.stamp_ns == values[-1].stamp_ns:
                if packet != values[-1]:
                    raise ValueError("ROS source changed an already identified frame")
                return
            values.append(packet)
            del values[:-self._queue_size]

    def push_color(self, packet: ImagePacket) -> None:
        self._push("color", self._image(packet, True))

    def push_depth(self, packet: ImagePacket) -> None:
        self._push("depth", self._image(packet, False))

    @staticmethod
    def _intrinsics(packet: CameraInfoPacket) -> np.ndarray:
        if packet.binning_x not in (0, 1) or packet.binning_y not in (0, 1) or any(packet.roi):
            raise ValueError("ROI and binned CameraInfo require upstream normalization")
        d = np.asarray(packet.d, dtype=float)
        if d.ndim != 1 or len(d) > 14 or not np.isfinite(d).all():
            raise ValueError("invalid camera distortion coefficients")
        k = np.asarray(packet.k, dtype=float)
        if k.shape != (9,) or not np.isfinite(k).all():
            raise ValueError("CameraInfo must contain calibrated intrinsics")
        k = k.reshape(3, 3)
        if packet.rectified:
            projection = np.asarray(packet.p, dtype=float)
            if projection.shape != (12,) or not np.isfinite(projection).all():
                raise ValueError("rectified CameraInfo requires its projection matrix")
            projection = projection.reshape(3, 4)
            if not np.allclose(projection[:, 3], 0):
                raise ValueError("aligned depth requires a monocular optical projection")
            k = projection[:, :3]
        elif np.any(d != 0):
            raise ValueError("uncorrected camera distortion must be rectified upstream")
        if (k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1])
                or abs(k[0, 1]) + abs(k[1, 0]) > 1e-6
                or not 0 <= k[0, 2] < packet.width or not 0 <= k[1, 2] < packet.height):
            raise ValueError("CameraInfo must describe a rectified calibrated pinhole camera")
        return k

    def push_camera_info(self, packet: CameraInfoPacket) -> None:
        self._header(packet)
        k = self._intrinsics(packet)
        calibration = (packet.width, packet.height, tuple(k.ravel()))
        with self._lock:
            if self._calibration is not None and calibration != self._calibration:
                raise ValueError("camera calibration changed; update profile revision and restart adapter")
            self._push("info", dataclasses.replace(packet, k=tuple(packet.k), d=tuple(packet.d),
                                                    p=tuple(packet.p), roi=tuple(packet.roi)))
            self._calibration = calibration

    def _synchronized(self):
        if any(not values for values in self._queues.values()):
            raise ValueError("no synchronized RGB, depth and CameraInfo capture is available")
        for depth in reversed(self._queues["depth"]):
            color = min(self._queues["color"], key=lambda p: abs(p.stamp_ns - depth.stamp_ns))
            info = min(self._queues["info"], key=lambda p: abs(p.stamp_ns - depth.stamp_ns))
            stamps = [p.stamp_ns for p in (color, depth, info)]
            if max(stamps) - min(stamps) <= self._skew_ns:
                return color, depth, info
        raise ValueError("no synchronized RGB, depth and CameraInfo capture is available")

    def read(self) -> RgbdFrame:
        with self._read_lock:
            with self._lock:
                generation = self._fault_generation
                color, depth, info = self._synchronized()
                for packet in (color, depth, info):
                    self._header(packet)
                if depth.stamp_ns == self._last_depth_stamp and self._frame is not None:
                    if self._fault is not None:
                        raise ValueError("camera fault requires a fresh synchronized capture")
                    validate_frame(self._frame, max_age_ms=self.sensor.max_age_ms)
                    return self._frame
                if depth.stamp_ns <= self._last_depth_stamp:
                    raise ValueError("ROS depth capture regressed")
                if (color.width, color.height) != (depth.width, depth.height) or (
                    color.width, color.height
                ) != (info.width, info.height):
                    raise ValueError("depth must already be aligned to RGB and CameraInfo dimensions")
            transform = self._lookup(depth.frame_id, depth.stamp_ns)
            if (not isinstance(transform, WorldTransform)
                    or transform.source_frame_id != self.sensor.frame_id
                    or transform.target_frame_id != "world"
                    or transform.transform_revision != self.sensor.transform_revision
                    or not (transform.stamp_ns == depth.stamp_ns
                            or transform.static is True and transform.stamp_ns == 0)):
                raise ValueError("TF identity, capture time or calibration revision does not match")
            rgb = np.ndarray((color.height, color.width, 3), dtype=np.uint8,
                             buffer=color.data, strides=(color.step, 3, 1))
            if color.encoding == "bgr8":
                rgb = rgb[..., ::-1]
            itemsize = 2 if depth.encoding == "16UC1" else 4
            dtype = (">" if depth.is_bigendian else "<") + ("u2" if itemsize == 2 else "f4")
            metric = np.ndarray((depth.height, depth.width), dtype=dtype, buffer=depth.data,
                                strides=(depth.step, itemsize)).astype(np.float64)
            if depth.encoding == "16UC1":
                metric *= .001
            frame = RgbdFrame(
                robot_id=self.profile.robot_id, source_id=self.sensor.source_id,
                frame_id=self.sensor.frame_id, transform_revision=self.sensor.transform_revision,
                captured_at_unix_ms=depth.stamp_ns // 1_000_000, sequence=self._sequence + 1,
                rgb=_immutable_array(rgb), depth_m=_immutable_array(metric),
                intrinsics=_immutable_array(self._intrinsics(info)),
                world_from_camera=_immutable_array(transform.matrix, float),
            )
            validate_frame(frame, max_age_ms=self.sensor.max_age_ms)
            with self._lock:
                # A callback fault while TF was loading must invalidate this
                # in-flight sample too, even if its old bytes were well formed.
                if generation != self._fault_generation:
                    raise ValueError("RGB-D input invalidated during transform lookup")
                self._sequence = frame.sequence
                self._last_depth_stamp = depth.stamp_ns
                self._frame = frame
                self._fault = None
            return frame


def create_rgbd_backend(
    profile: dict | RobotProfile, source: Any, perception: RgbdPerception, *,
    stop: Callable[[str], None], handlers: Mapping[str, Callable[[Command], Result]] | None = None,
    physical_ready: Callable[[], bool] | None = None,
    state_provider: Callable[[], dict[str, Any]] | None = None,
    disconnect: Callable[[], None] | None = None,
) -> PluginBackend:
    """Attach a real RGB-D source to canonical handlers and the common safety boundary.

    A perception-only source advertises only the tools in its supplied profile;
    this function never supplies successful placeholder manipulation handlers.
    Run the returned backend with ``run_plugin serve`` for persistent journal,
    fencing, lease enforcement and mTLS. Factory configuration is trusted local
    code and must supply a commissioned local stop callback for motion tools.
    """
    if not callable(getattr(source, "read", None)) or not callable(getattr(perception, "reconstruct", None)):
        raise TypeError("RGB-D source and reconstruction processor are required")

    def observation():
        frame = source.read()
        # No second read and no separately indexed image cache: all fields
        # leave the provider as one transaction from this exact camera frame.
        reconstruction = perception.reconstruct(frame)
        return ReconstructionCapture(
            reconstruction.to_wire(), compressed_image=encode_rgb_png(frame.rgb),
            image_media_type="image/png", compressed_depth_image=encode_depth_preview(frame.depth_m),
            depth_image_media_type="image/png",
        )

    return PluginBackend(profile, observation_provider=observation, handlers=handlers or {},
                         stop=stop, physical_ready=physical_ready, state_provider=state_provider,
                         disconnect=disconnect)
