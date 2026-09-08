import time

import numpy as np
import pytest
from tangying_robot_gateway.rgbd import (
    PixelDetection,
    RgbdFrame,
    RgbdPerception,
    deproject,
    validate_frame,
)


def frame(**changes):
    values = {
        "robot_id": "robot-1",
        "source_id": "head-rgbd",
        "frame_id": "optical",
        "transform_revision": "cal-1",
        "captured_at_unix_ms": int(time.time() * 1000),
        "sequence": 1,
        "rgb": np.zeros((4, 4, 3), dtype=np.uint8),
        "depth_m": np.ones((4, 4)),
        "intrinsics": np.array([[2.0, 0, 1.5], [0, 2.0, 1.5], [0, 0, 1.0]]),
        "world_from_camera": np.eye(4),
    }
    return RgbdFrame(**(values | changes))


def test_metric_optical_depth_is_transformed_into_world():
    f = frame()
    points, valid = deproject(f)
    np.testing.assert_allclose(points[0, 0], [-0.75, -0.75, 1.0])
    assert valid.all()
    transform = np.eye(4)
    transform[:3, 3] = [1, 2, 3]
    moved, _ = deproject(frame(world_from_camera=transform))
    np.testing.assert_allclose(moved[0, 0], [0.25, 1.25, 4.0])


@pytest.mark.parametrize(
    "changes",
    [
        {"captured_at_unix_ms": 1},
        {"sequence": 0},
        {"rgb": np.zeros((4, 4, 3), dtype=float)},
        {"depth_m": np.ones((3, 4))},
        {"intrinsics": np.eye(3) * 0},
        {"world_from_camera": np.zeros((4, 4))},
        {"world_from_camera": np.diag([1.0, 1.0, -1.0, 1.0])},
    ],
)
def test_invalid_or_stale_capture_rejected(changes):
    with pytest.raises(ValueError):
        validate_frame(frame(**changes))


def test_detection_requires_actual_valid_depth_and_preserves_capture_identity():
    detector = lambda f: [PixelDetection("cup", "cup", np.ones((4, 4), dtype=bool), 0.9)]
    p = RgbdPerception(detector, min_pixels=3)
    f = frame()
    scene = p.reconstruct(f)
    assert scene.source_type == "rgbd_camera"
    assert scene.observed_at_unix_ms == f.captured_at_unix_ms
    assert len(scene.entities) == 1
    assert len(scene.points) == 16
    empty = p.reconstruct(frame(depth_m=np.zeros((4, 4))))
    assert empty.entities == [] and empty.points == []


def test_blank_rgb_never_creates_an_object_from_depth_alone():
    p = RgbdPerception(lambda f: [], min_pixels=3)
    assert not p.reconstruct(frame()).entities


def test_frame_point_cloud_is_bounded():
    p = RgbdPerception(lambda f: [], max_points=5)
    assert len(p.reconstruct(frame()).points) <= 5
