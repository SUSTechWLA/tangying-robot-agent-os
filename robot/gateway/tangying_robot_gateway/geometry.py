"""Geometry the mapping, localization and exploration layers share.

This module holds the *vocabulary* those layers agree on - a point cloud, the voxel
downsample that thins it, and the planar pose arithmetic - and nothing else. It has
no dependency on the occupancy grid, the map artifacts or the SLAM estimator.

Before this split, ``dense_slam`` imported ``PointCloud`` from ``map_pipeline``: a
type dependency, not a behavioural one, and it made the two look coupled in exactly
the direction that matters when the question is "can I replace the estimator
without touching the grid builder".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = [
    "PointCloud",
    "compose",
    "pose_se2",
    "relative",
    "transform",
    "voxel_downsample",
    "wrap",
]


@dataclass(frozen=True)
class PointCloud:
    """Positions in metres and optional 8-bit colours."""

    xyz: np.ndarray
    rgb: np.ndarray | None = None

    def __post_init__(self) -> None:
        if self.xyz.ndim != 2 or self.xyz.shape[1] != 3:
            raise ValueError("xyz must be an (N, 3) array")
        if self.xyz.dtype.kind not in "fiu" or not np.isfinite(self.xyz).all():
            raise ValueError("xyz must contain finite numeric coordinates")
        if self.rgb is not None and self.rgb.shape != self.xyz.shape:
            raise ValueError("rgb must match xyz shape")

    @property
    def count(self) -> int:
        return int(self.xyz.shape[0])

    def bounds(self) -> dict[str, list[float]]:
        if self.count == 0:
            raise ValueError("an empty cloud has no bounds")
        return {
            "min": [float(value) for value in self.xyz.min(axis=0)],
            "max": [float(value) for value in self.xyz.max(axis=0)],
        }

def voxel_downsample(cloud: PointCloud, voxel_size: float) -> PointCloud:
    """One representative point per voxel.

    Real RGB-D scans repeat the same surface many times over, so the raw count
    overstates the map. Averaging inside each voxel keeps a stable surface and
    removes the duplicates; colour travels with the position.
    """
    if not math.isfinite(voxel_size) or voxel_size <= 0:
        raise ValueError("voxel_size must be positive")
    if cloud.count == 0:
        return cloud
    keys = np.floor(cloud.xyz / voxel_size).astype(np.int64)
    _, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inverse = inverse.ravel()
    averaged = np.zeros((counts.shape[0], 3), dtype=np.float64)
    np.add.at(averaged, inverse, cloud.xyz)
    averaged /= counts[:, None]
    rgb = None
    if cloud.rgb is not None:
        summed = np.zeros((counts.shape[0], 3), dtype=np.float64)
        np.add.at(summed, inverse, cloud.rgb.astype(np.float64))
        rgb = np.clip(np.rint(summed / counts[:, None]), 0, 255).astype(np.uint8)
    return PointCloud(xyz=averaged.astype(np.float32), rgb=rgb)

def wrap(angle):
    return np.arctan2(np.sin(angle), np.cos(angle))

def pose_se2(pose):
    values = np.asarray(pose, dtype=float)
    if values.shape != (7,) or not np.isfinite(values).all():
        raise ValueError("mapping requires a finite same-capture base pose")
    w, x, y, z = values[3:]
    if abs(np.linalg.norm(values[3:]) - 1) > .001 or abs(x) + abs(y) > .02:
        raise ValueError("planar SLAM requires a normalized level-base quaternion")
    return np.array([values[0], values[1], math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))])

def transform(points, pose):
    c, s = np.cos(pose[2]), np.sin(pose[2])
    result = points.copy()
    result[:, :2] = points[:, :2] @ np.array([[c, s], [-s, c]]) + pose[:2]
    return result

def relative(a, b):
    c, s = np.cos(a[2]), np.sin(a[2])
    return np.array([c*(b[0]-a[0])+s*(b[1]-a[1]),
                     -s*(b[0]-a[0])+c*(b[1]-a[1]), wrap(b[2]-a[2])])

def compose(a, delta):
    c, s = np.cos(a[2]), np.sin(a[2])
    return np.array([a[0]+c*delta[0]-s*delta[1], a[1]+s*delta[0]+c*delta[1], wrap(a[2]+delta[2])])
