"""Sensor-only RGB-D reconstruction. Optical coordinates: right, down, forward.

No simulator, robot driver or object-position registry is available here. A
detector identifies pixels; metric geometry comes exclusively from valid depth.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from .contracts import Entity, Reconstruction


@dataclass(frozen=True)
class RgbdFrame:
    robot_id: str
    source_id: str
    frame_id: str
    transform_revision: str
    captured_at_unix_ms: int
    sequence: int
    rgb: np.ndarray
    depth_m: np.ndarray
    intrinsics: np.ndarray
    world_from_camera: np.ndarray


@dataclass(frozen=True)
class PixelDetection:
    entity_id: str
    category: str
    mask: np.ndarray
    confidence: float
    attributes: dict[str, str] = field(default_factory=dict)


def validate_frame(frame: RgbdFrame, now_ms: int | None = None, max_age_ms: int = 2000) -> None:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if not all(
        isinstance(x, str) and x and not any(c.isspace() for c in x)
        for x in (
            frame.robot_id,
            frame.source_id,
            frame.frame_id,
            frame.transform_revision,
        )
    ):
        raise ValueError("RGB-D identity and calibration revision are required")
    if type(frame.sequence) is not int or not 0 < frame.sequence <= 9_007_199_254_740_991:
        raise ValueError("RGB-D sequence must be a positive safe integer")
    if (
        type(frame.captured_at_unix_ms) is not int
        or not 0 <= now_ms - frame.captured_at_unix_ms <= max_age_ms
    ):
        raise ValueError("RGB-D capture is stale or future dated")
    rgb, depth = frame.rgb, frame.depth_m
    if (
        not isinstance(rgb, np.ndarray)
        or rgb.dtype != np.uint8
        or rgb.ndim != 3
        or rgb.shape[2] != 3
    ):
        raise ValueError("RGB must be uint8 HxWx3")
    if not 0 < rgb.shape[0] <= 2160 or not 0 < rgb.shape[1] <= 3840:
        raise ValueError("RGB dimensions exceed bounded capture size")
    if not isinstance(depth, np.ndarray) or depth.shape != rgb.shape[:2] or depth.dtype.kind != "f":
        raise ValueError("aligned depth must be floating-point metres with RGB dimensions")
    k, transform = np.asarray(frame.intrinsics), np.asarray(frame.world_from_camera)
    if k.shape != (3, 3) or not np.isfinite(k).all() or k[0, 0] <= 0 or k[1, 1] <= 0:
        raise ValueError("valid camera intrinsics are required")
    if not np.allclose(k[2], [0, 0, 1]) or abs(k[0, 1]) + abs(k[1, 0]) > 1e-6:
        raise ValueError("intrinsics must describe a rectified pinhole camera")
    if not (0 <= k[0, 2] < rgb.shape[1] and 0 <= k[1, 2] < rgb.shape[0]):
        raise ValueError("principal point lies outside image")
    if transform.shape != (4, 4) or not np.isfinite(transform).all():
        raise ValueError("world_from_camera must be a finite 4x4 rigid transform")
    rotation = transform[:3, :3]
    if (
        not np.allclose(transform[3], [0, 0, 0, 1])
        or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5)
        or not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-5)
    ):
        raise ValueError("world_from_camera must be a proper rigid transform")


def deproject(frame: RgbdFrame, *, max_depth_m: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Return HxWx3 world points and a validity mask; missing depth stays missing."""
    validate_frame(frame)
    depth = frame.depth_m
    valid = np.isfinite(depth) & (depth > 0.02) & (depth <= max_depth_m)
    v, u = np.indices(depth.shape)
    k = frame.intrinsics
    safe_depth = np.where(valid, depth, 0.0)
    optical = np.stack(
        ((u - k[0, 2]) * safe_depth / k[0, 0], (v - k[1, 2]) * safe_depth / k[1, 1], safe_depth),
        axis=-1,
    )
    points = optical @ frame.world_from_camera[:3, :3].T + frame.world_from_camera[:3, 3]
    return points, valid


def _cloud_pixel_indices(valid: np.ndarray, masks: list[np.ndarray], limit: int) -> np.ndarray:
    """Keep small detected surfaces and scene context using measured pixels only.

    Half the budget is shared by accepted detection masks. The remainder covers
    all other valid pixels. Indices are unique and select both XYZ and RGB, so
    changing sampling density never separates color from its depth measurement.
    """
    available = valid.ravel().copy()
    all_indices = np.flatnonzero(available)
    if len(all_indices) <= limit:
        return all_indices
    selected = []
    quota = limit // (2 * len(masks)) if masks else 0
    if quota:
        for mask in masks:
            candidates = np.flatnonzero(available & mask.ravel())
            chosen = candidates[
                np.linspace(0, len(candidates) - 1, min(quota, len(candidates)), dtype=int)
            ]
            selected.extend(chosen)
            available[chosen] = False
    remaining = np.flatnonzero(available)
    count = min(limit - len(selected), len(remaining))
    selected.extend(remaining[np.linspace(0, len(remaining) - 1, count, dtype=int)])
    return np.sort(np.asarray(selected, dtype=int))


class RgbdPerception:
    def __init__(
        self,
        detector: Callable[[RgbdFrame], list[PixelDetection]],
        *,
        min_pixels: int = 12,
        max_points: int = 2048,
    ):
        if not callable(detector) or min_pixels < 1 or not 1 <= max_points <= 4096:
            raise ValueError("detector and bounded perception limits are required")
        self.detector = detector
        self.min_pixels = min_pixels
        self.max_points = max_points

    def reconstruct(self, frame: RgbdFrame) -> Reconstruction:
        points, valid = deproject(frame)
        entities = []
        masks = []
        for detection in self.detector(frame):
            if detection.mask.shape != valid.shape or detection.mask.dtype != np.bool_:
                raise ValueError("detector must return a boolean mask aligned to RGB-D")
            selected = points[detection.mask & valid]
            if len(selected) < self.min_pixels:
                continue
            masks.append(detection.mask)
            center = np.median(selected, axis=0)
            entities.append(
                Entity(
                    entity_id=detection.entity_id,
                    category=detection.category,
                    attributes=detection.attributes,
                    pose=[*center.tolist(), 1.0, 0.0, 0.0, 0.0],
                    confidence=detection.confidence,
                )
            )
        indices = _cloud_pixel_indices(valid, masks, self.max_points)
        cloud = points.reshape(-1, 3)[indices]
        colors = frame.rgb.reshape(-1, 3)[indices]
        return Reconstruction(
            schema_version="scene.reconstruction.v1",
            robot_id=frame.robot_id,
            observation_id=f"{frame.source_id}-{frame.sequence}-{frame.captured_at_unix_ms}",
            source_id=frame.source_id,
            source_type="rgbd_camera",
            source_frame_id=frame.frame_id,
            frame_id="world",
            transform_revision=frame.transform_revision,
            observed_at_unix_ms=frame.captured_at_unix_ms,
            sequence=frame.sequence,
            units="m",
            entities=entities,
            points=cloud.tolist(),
            point_colors=colors.tolist(),
        )
