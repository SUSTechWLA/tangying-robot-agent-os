"""Reference RGB-D estimator adapter for the planar mapping session port."""
from __future__ import annotations

import itertools

import numpy as np

from .dense_slam import DenseSLAM
from .geometry import transform
from .slam_provider import InsufficientDepthError, SlamProgress, SlamProviderInfo, SlamSnapshot


class DenseSlamProvider:
    info = SlamProviderInfo("planar-rgbd-icp-posegraph", "1")

    def __init__(self, *, odometry_sigma=(.025, .025, .015), absolute_odometry_sigma=None):
        self.estimator = DenseSLAM(odometry_sigma=odometry_sigma, absolute_odometry_sigma=absolute_odometry_sigma)

    @property
    def frame_count(self) -> int:
        return len(self.estimator.frames)

    @property
    def max_frames(self) -> int:
        return self.estimator.MAX_FRAMES

    def add(self, observation) -> bool:
        try:
            return self.estimator.add(observation)
        except ValueError as error:
            if "深度点不足" in str(error):
                raise InsufficientDepthError(str(error)) from error
            raise

    def optimize(self) -> None:
        self.estimator.optimize()

    def progress(self) -> SlamProgress:
        frames = self.estimator.frames
        points, colors = [], []
        per_frame = max(1, 2000 // max(1, len(frames)))
        for frame in frames:
            stride = max(1, len(frame.points) // per_frame)
            points.extend(transform(frame.points[::stride], frame.pose).tolist())
            colors.extend(frame.colors[::stride].tolist())
        return SlamProgress(
            frame_count=len(frames), point_count=sum(len(frame.points) for frame in frames),
            registration_count=len(self.estimator.registrations), loop_closures=len(self.estimator.loops),
            travelled_m=float(sum(np.linalg.norm(b.odometry[:2] - a.odometry[:2])
                                  for a, b in itertools.pairwise(frames))),
            latest_odometry=tuple(float(value) for value in frames[-1].odometry) if frames else None,
            trajectory=tuple(tuple(point) for point in self.estimator.trajectory()),
            preview_points=tuple(tuple(point) for point in points[:3000]),
            preview_colors=tuple(tuple(point) for point in colors[:3000]))

    def snapshot(self) -> SlamSnapshot:
        frames = self.estimator.frames
        return SlamSnapshot(info=self.info, cloud=self.estimator.cloud(), sources=self.estimator.cloud_sources(),
                            poses=np.array([frame.pose for frame in frames]),
                            odometry=np.array([frame.odometry for frame in frames]),
                            trajectory=np.array(self.estimator.trajectory()),
                            timestamps=np.array([frame.timestamp for frame in frames]),
                            provenance=self.estimator.provenance())

    def keyframe_document(self, *, map_id, robot_id, calibration_revision) -> dict:
        return self.estimator.previews.document(map_id=map_id, robot_id=robot_id,
                                                calibration_revision=calibration_revision)
