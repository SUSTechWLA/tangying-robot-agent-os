"""Measured planar mapping sessions, independent of a particular estimator.

This is an in-process acquisition port, not a ROS SLAM driver or a generic 3-D
navigation API. Geometry and poses are metric and expressed in the driver's
stable world frame. The workflow alone anchors that evidence into a saved map.
A source index identifies the observation that measured each cloud point.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from .geometry import PointCloud


class InsufficientDepthError(ValueError):
    """This capture lacks depth; a bounded survey may try a different viewpoint."""


@dataclass(frozen=True)
class SlamProviderInfo:
    provider_id: str
    version: str
    map_source: str = "rgbd_slam"
    observation_kind: str = "registered_rgbd"
    pose_model: str = "se2"
    coordinate_frame: str = "driver_world"
    units: str = "metres"

    def validate(self) -> None:
        for name in ("provider_id", "version", "map_source", "observation_kind", "pose_model", "coordinate_frame", "units"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"SLAM provider {name} must be a string")
        if not self.provider_id or not self.version or len(self.provider_id) > 128 or len(self.version) > 128:
            raise ValueError("SLAM provider identity and version must be nonempty and bounded")
        if self.map_source not in {"rgbd_slam", "rtabmap", "import"}:
            raise ValueError("SLAM provider map source is unsupported")
        if (self.observation_kind, self.pose_model, self.coordinate_frame, self.units) != (
                "registered_rgbd", "se2", "driver_world", "metres"):
            raise ValueError("SLAM provider requires registered RGB-D, SE2 driver-world poses and metres")

    def document(self) -> dict:
        self.validate()
        return {"schemaVersion": "slam.provider.v1", "id": self.provider_id, "version": self.version,
                "mapSource": self.map_source, "observationKind": self.observation_kind,
                "poseModel": self.pose_model, "coordinateFrame": self.coordinate_frame, "units": self.units}


@dataclass(frozen=True)
class SlamProgress:
    frame_count: int
    point_count: int
    registration_count: int
    loop_closures: int
    travelled_m: float
    latest_odometry: tuple[float, float, float] | None
    trajectory: tuple[tuple[float, float, float], ...]
    preview_points: tuple[tuple[float, float, float], ...] = ()
    preview_colors: tuple[tuple[int, int, int], ...] = ()

    def __post_init__(self) -> None:
        for name in ("frame_count", "point_count", "registration_count", "loop_closures"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"SLAM progress {name} must be a nonnegative integer")
        if not math.isfinite(self.travelled_m) or self.travelled_m < 0:
            raise ValueError("SLAM measured travel must be finite and nonnegative")
        if len(self.trajectory) != self.frame_count:
            raise ValueError("SLAM progress trajectory must match its observation count")
        if (self.latest_odometry is None) != (self.frame_count == 0):
            raise ValueError("SLAM progress requires the latest measured odometry")
        for name in ("trajectory", "preview_points"):
            value = getattr(self, name)
            if value:
                matrix = _matrix(value, name, 3)
                object.__setattr__(self, name, tuple(tuple(point) for point in matrix.tolist()))
        if self.latest_odometry is not None:
            matrix = _matrix([self.latest_odometry], "latest odometry", 3)
            object.__setattr__(self, "latest_odometry", tuple(matrix[0].tolist()))
        if len(self.preview_points) > 3000 or len(self.preview_colors) != len(self.preview_points):
            raise ValueError("SLAM preview must have matching colours and at most 3000 points")
        for colour in self.preview_colors:
            if len(colour) != 3 or any(type(value) is not int or not 0 <= value <= 255 for value in colour):
                raise ValueError("SLAM preview colours must be uint8-compatible RGB")
        object.__setattr__(self, "preview_colors", tuple(tuple(colour) for colour in self.preview_colors))

    def summary(self) -> dict:
        return {"frameCount": self.frame_count, "pointCount": self.point_count,
                "registrationCount": self.registration_count, "loopClosures": self.loop_closures,
                "travelledM": self.travelled_m, "trajectory": [list(point) for point in self.trajectory]}


def _matrix(value, name: str, columns: int) -> np.ndarray:
    array = np.asarray(value)
    if array.ndim != 2 or array.shape[1] != columns or array.dtype.kind not in "fiu" or not np.isfinite(array).all():
        raise ValueError(f"SLAM {name} must be a finite numeric (N, {columns}) array")
    result = np.array(array, dtype=np.float64, copy=True)
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class SlamSnapshot:
    """Detached, validated geometry at one serialized provider boundary.

    ``poses`` and ``odometry`` contain x/y/yaw; ``trajectory`` contains x/y/z.
    Point ``sources`` are local observation indices, never inherited-map IDs.
    Arrays are copied and made read-only so later provider optimization cannot
    mutate an already selected publication basis. Full snapshots are on demand.
    """
    info: SlamProviderInfo
    cloud: PointCloud
    sources: np.ndarray
    poses: np.ndarray
    odometry: np.ndarray
    trajectory: np.ndarray
    timestamps: np.ndarray
    provenance: dict

    def __post_init__(self) -> None:
        self.info.validate()
        poses = _matrix(self.poses, "poses", 3)
        odometry = _matrix(self.odometry, "odometry", 3)
        trajectory = _matrix(self.trajectory, "trajectory", 3)
        count = len(poses)
        if not count or len(odometry) != count or len(trajectory) != count:
            raise ValueError("SLAM poses, odometry and trajectory must share a nonempty observation count")
        if not np.allclose(trajectory[:, :2], poses[:, :2], rtol=0, atol=1e-8):
            raise ValueError("SLAM trajectory must use the same optimized world-frame x/y poses")
        xyz = _matrix(self.cloud.xyz, "cloud", 3)
        if np.any(np.abs(xyz) > np.finfo(np.float32).max):
            raise ValueError("SLAM cloud coordinates exceed float32 range")
        xyz = xyz.astype(np.float32)
        xyz.setflags(write=False)
        if not len(xyz):
            raise ValueError("SLAM snapshot cloud cannot be empty")
        rgb = self.cloud.rgb
        if rgb is not None:
            rgb = np.asarray(rgb)
            if rgb.shape != xyz.shape or rgb.dtype.kind not in "iu" or np.any(rgb < 0) or np.any(rgb > 255):
                raise ValueError("SLAM cloud colours must be uint8-compatible RGB")
            rgb = np.array(rgb, dtype=np.uint8, copy=True)
            rgb.setflags(write=False)
        sources = np.asarray(self.sources)
        if (sources.shape != (len(xyz),) or sources.dtype.kind not in "iu"
                or np.any(sources < 0) or np.any(sources >= count)):
            raise ValueError("SLAM cloud source indices must match cloud points and observations")
        sources = np.array(sources, dtype=np.int64, copy=True)
        sources.setflags(write=False)
        stamps = np.asarray(self.timestamps)
        if (stamps.shape != (count,) or stamps.dtype.kind not in "iu" or np.any(stamps <= 0)
                or np.any(stamps > np.iinfo(np.int64).max) or np.any(stamps[1:] <= stamps[:-1])):
            raise ValueError("SLAM timestamps must be positive strictly increasing integer Unix milliseconds")
        stamps = np.array(stamps, dtype=np.int64, copy=True)
        stamps.setflags(write=False)
        provenance = copy.deepcopy(self.provenance)
        if not isinstance(provenance, dict) or provenance.get("schemaVersion") != "slam.session.v1":
            raise ValueError("SLAM provenance must declare slam.session.v1")
        json.dumps(provenance, allow_nan=False)
        for name, value in (("cloud", PointCloud(xyz, rgb)), ("sources", sources), ("poses", poses),
                            ("odometry", odometry), ("trajectory", trajectory), ("timestamps", stamps),
                            ("provenance", provenance)):
            object.__setattr__(self, name, value)


class SlamProvider(Protocol):
    """Serialized by RobotWorkflow; a fresh instance owns one bounded session."""

    @property
    def info(self) -> SlamProviderInfo: ...

    @property
    def frame_count(self) -> int: ...

    @property
    def max_frames(self) -> int: ...

    def add(self, observation: Any) -> bool: ...

    def optimize(self) -> None: ...

    def progress(self) -> SlamProgress: ...

    def snapshot(self) -> SlamSnapshot: ...

    def keyframe_document(self, *, map_id: str, robot_id: str, calibration_revision: str) -> dict | None: ...
