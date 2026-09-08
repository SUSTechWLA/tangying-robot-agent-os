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


def test_sampled_points_keep_exact_rgb_pixel_alignment_after_world_transform():
    # Recover each source pixel geometrically, independently of sampler ordering.
    rgb = np.arange(48, dtype=np.uint8).reshape(4, 4, 3)
    depth = np.ones((4, 4))
    depth[1, 2], depth[2, 1] = np.nan, 0
    transform = np.array(
        [[0.0, -1.0, 0.0, 2.0], [1.0, 0.0, 0.0, 3.0], [0.0, 0.0, 1.0, 4.0], [0.0, 0.0, 0.0, 1.0]]
    )
    f = frame(rgb=rgb, depth_m=depth, world_from_camera=transform)
    scene = RgbdPerception(lambda f: [], max_points=7).reconstruct(f)
    assert len(scene.point_colors) == len(scene.points) == 7
    for point, color in zip(scene.points, scene.point_colors, strict=True):
        optical = transform[:3, :3].T @ (np.asarray(point) - transform[:3, 3])
        u, v = np.rint(optical[:2] / optical[2] * 2 + 1.5).astype(int)
        assert depth[v, u] == 1
        assert color == rgb[v, u].tolist()


def test_small_detected_surface_survives_cloud_downsampling_without_invented_points():
    rgb = np.full((100, 100, 3), 100, dtype=np.uint8)
    mask = np.zeros((100, 100), dtype=bool)
    mask[40:44, 45:49] = True
    rgb[mask] = [240, 20, 10]
    f = frame(
        rgb=rgb,
        depth_m=np.ones((100, 100)),
        intrinsics=np.array([[100.0, 0.0, 49.5], [0.0, 100.0, 49.5], [0.0, 0.0, 1.0]]),
    )
    scene = RgbdPerception(
        lambda _: [PixelDetection("small-cup", "cup", mask, 0.9)], max_points=96
    ).reconstruct(f)
    actual, valid = deproject(f)
    source_points = {tuple(point) for point in actual[valid]}
    emitted = {tuple(point) for point in scene.points}
    assert len(emitted) == len(scene.points) <= 96  # no duplicated artificial density
    assert emitted <= source_points  # no complete synthetic geometry
    target_points = {tuple(point) for point in actual[mask]}
    assert len(emitted & target_points) >= 12


def test_rgbd_cloud_without_valid_depth_has_no_point_colors():
    scene = RgbdPerception(lambda _: []).reconstruct(frame(depth_m=np.zeros((4, 4))))
    assert scene.points == scene.point_colors == []
